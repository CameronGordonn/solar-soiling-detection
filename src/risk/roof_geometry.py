"""Roof-plane tilt and azimuth per array polygon, from USGS 3DEP lidar.

Why this exists
---------------
``src/risk/economics.py`` hands every site the same ``BASE_SUN = 5.5`` GHI
peak-sun-hours, so array orientation enters the dollar chain nowhere. Clear-sky
POA across the plausible orientation space spans 0.79-1.02 of the south-20deg
reference (``src/risk/rates.py:_clearsky_poa``), i.e. a 1.29x bound on the kWh
denominator, against the soiling head's measured 1.15x. This module measures the
real spread instead of assuming it.

It also supplies ``tilt_deg`` to ``src/solarsoiled/recommend.py``, whose
``_EXCEPTION_TILT_DEG = 5.0`` flat-roof rule currently never fires because tilt is
always ``None`` in the AOI matrix.

NOT for the risk model. ``docs/CRAIG_BRIEF_2026-08-19.md`` sec.3 ran the controlled
experiment: NREL labels are station-level, so no feature learned from them can rank
roofs within one AOI. Tilt is that class of feature. This is a physics input, not a
learned one.

Data source
-----------
USGS 3DEP public EPT, ``s3://usgs-lidar-public/CA_SantaCruzCounty_2020``. 41.8e9
points, ~18 pts/m2, classified, EPSG:3857. The headline 3DEP raster product is
*bare-earth DEM* and is useless here by construction; roofs only exist in the point
cloud. 2020 vintage against 2025 imagery is fine: roof pitch does not change, so a
2023 install still sits on a plane the 2020 flight measured.

Read over plain HTTP + laspy rather than PDAL, deliberately: PDAL wants the C++
library and a healthy PROJ, and this env's PROJ is already degraded. The octree walk
is ~40 lines against a well-specified format and costs no C++ dependency.

THE MERCATOR TRAP
-----------------
EPT X/Y are EPSG:3857 metres; Z is true metres. At lat 36.97 the mercator scale
factor is 1/cos(lat) = 1.252, so a plane fitted on raw EPT coordinates reports tilt
~20% too shallow, silently, on every roof. :func:`fit_plane` therefore multiplies
X/Y by cos(lat) before fitting. Do not remove that line.
"""

from __future__ import annotations

import io
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests
import shapely

SCC_EPT_URL = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/CA_SantaCruzCounty_2020"
DEFAULT_CACHE = Path(".cache/lidar")

#: EPT depth at which a node is roughly one building. Root cube is 77,296 m, so
#: side = 77296 / 2**depth: depth 12 -> 18.9 m (mercator), ~15 m true.
MAX_DEPTH = 14

#: Shrink applied to the array polygon before selecting points. Same rationale as the
#: SAM2 prompt-box shrink in .claude/rules/stage1-detect.md: the polygon comes from
#: 2025 21cm imagery and the cloud from a 2020 flight, so a sub-metre georegistration
#: offset would otherwise pull in eave, gutter or the neighbouring roof plane.
POLYGON_SHRINK = 0.15

#: ASPRS classes kept. 1 = unclassified, 6 = building. PV modules are not a class
#: of their own and land in either depending on the vendor's workflow, so keeping
#: both is required; ground (2) and vegetation (3-5) are dropped.
KEEP_CLASSES = (1, 6)


def _make_session(pool_size: int = 32) -> requests.Session:
    """Session sized for the thread pool, with retries on the transient S3 5xx/429."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    retry = Retry(total=5, backoff_factor=0.5,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]))
    ad = HTTPAdapter(max_retries=retry, pool_connections=pool_size,
                     pool_maxsize=pool_size)
    s.mount("https://", ad)
    s.mount("http://", ad)
    return s


_POINT_DTYPE = np.dtype([("x", "f8"), ("y", "f8"), ("z", "f8"),
                         ("cls", "u1"), ("ret", "u1"), ("nret", "u1")])


def _empty_points() -> np.ndarray:
    return np.empty(0, dtype=_POINT_DTYPE).view(np.recarray)


# --------------------------------------------------------------------------- EPT

class EptSource:
    """Lazy reader for an Entwine Point Tile store served over HTTP."""

    def __init__(self, base_url: str = SCC_EPT_URL, cache_dir: Path | str = DEFAULT_CACHE,
                 session: requests.Session | None = None, pool_size: int = 32):
        self.base = base_url.rstrip("/")
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.session = session or _make_session(pool_size)
        self.meta = json.loads(self._get(f"{self.base}/ept.json", "ept.json"))
        b = self.meta["bounds"]
        self.origin = (float(b[0]), float(b[1]), float(b[2]))
        self.side0 = float(b[3]) - float(b[0])
        self._hier: dict[str, int] = {}
        self._pages_loaded: set[str] = set()

    # -- transport ---------------------------------------------------------

    def _get(self, url: str, cache_name: str, attempts: int = 5) -> bytes:
        path = self.cache / cache_name
        if path.exists():
            return path.read_bytes()
        last: Exception | None = None
        for i in range(attempts):
            try:
                r = self.session.get(url, timeout=120)
                r.raise_for_status()
                break
            except (requests.ConnectionError, requests.Timeout) as exc:
                # S3 resets connections under concurrency; urllib3's Retry does not
                # cover a reset mid-handshake, so back off here too.
                last = exc
                time.sleep(0.5 * 2 ** i)
        else:
            raise RuntimeError(f"giving up on {url} after {attempts} attempts") from last
        path.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name: concurrent workers must not race on the same .part file.
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.part")
        tmp.write_bytes(r.content)
        os.replace(tmp, path)
        return r.content

    # -- octree geometry ---------------------------------------------------

    def node_bounds(self, key: str) -> tuple[float, float, float, float]:
        """(minx, miny, maxx, maxy) of a ``D-X-Y-Z`` key, in EPT CRS."""
        d, x, y, _z = (int(v) for v in key.split("-"))
        side = self.side0 / (2 ** d)
        minx = self.origin[0] + x * side
        miny = self.origin[1] + y * side
        return minx, miny, minx + side, miny + side

    def _load_page(self, key: str) -> None:
        if key in self._pages_loaded:
            return
        page = json.loads(self._get(f"{self.base}/ept-hierarchy/{key}.json",
                                    f"hierarchy/{key}.json"))
        self._hier.update(page)
        self._pages_loaded.add(key)

    def nodes_for_bbox(self, bbox: tuple[float, float, float, float],
                       max_depth: int = MAX_DEPTH) -> list[str]:
        """Every node key whose extent intersects ``bbox``.

        EPT is additive: each level stores a further subsample, so full density
        requires the whole chain from the root down, not just the leaves.
        """
        self._load_page("0-0-0-0")
        out: list[str] = []
        stack = ["0-0-0-0"]
        while stack:
            key = stack.pop()
            n = self._hier.get(key)
            if n is None:
                continue
            if n < 0:                       # pointer to a sub-hierarchy page
                self._load_page(key)
                n = self._hier.get(key, 0)
                if n is None or n < 0:
                    continue
            if n == 0:
                continue
            nb = self.node_bounds(key)
            if nb[2] <= bbox[0] or nb[0] >= bbox[2] or nb[3] <= bbox[1] or nb[1] >= bbox[3]:
                continue
            out.append(key)
            d, x, y, z = (int(v) for v in key.split("-"))
            if d >= max_depth:
                continue
            for dx in (0, 1):
                for dy in (0, 1):
                    for dz in (0, 1):
                        stack.append(f"{d+1}-{2*x+dx}-{2*y+dy}-{2*z+dz}")
        return out

    # -- point access ------------------------------------------------------

    def fetch_node(self, key: str) -> np.ndarray:
        """Structured array (x, y, z, cls, ret, nret) for one node."""
        import laspy
        raw = self._get(f"{self.base}/ept-data/{key}.laz", f"data/{key}.laz")
        las = laspy.read(io.BytesIO(raw))
        out = np.empty(len(las.x), dtype=_POINT_DTYPE)
        out["x"] = las.x
        out["y"] = las.y
        out["z"] = las.z
        out["cls"] = las.classification
        out["ret"] = las.return_number
        out["nret"] = las.number_of_returns
        return out.view(np.recarray)

    def points_in_bbox(self, bbox: tuple[float, float, float, float],
                       max_depth: int = MAX_DEPTH, workers: int = 12) -> np.ndarray:
        keys = self.nodes_for_bbox(bbox, max_depth=max_depth)
        if not keys:
            return _empty_points()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            parts = list(ex.map(self.fetch_node, keys))
        parts = [p for p in parts if len(p)]
        if not parts:
            return _empty_points()
        # np.concatenate drops the recarray view; restore it so .x/.y/.z still work.
        pts = np.concatenate(parts).view(np.recarray)
        m = ((pts.x >= bbox[0]) & (pts.x < bbox[2])
             & (pts.y >= bbox[1]) & (pts.y < bbox[3]))
        return pts[m]


# ---------------------------------------------------------------------- fitting

@dataclass
class PlaneFit:
    tilt_deg: float
    azimuth_deg: float          # bearing of the DOWNSLOPE direction, cw from north
    n_points: int
    inlier_frac: float
    rms_residual_m: float
    ok: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "tilt_deg": self.tilt_deg, "azimuth_deg": self.azimuth_deg,
            "n_points": self.n_points, "inlier_frac": self.inlier_frac,
            "rms_residual_m": self.rms_residual_m,
            "fit_ok": self.ok, "fit_reason": self.reason,
        }


#: A fit must clear all three or it is reported as failed rather than guessed.
MIN_POINTS = 12
MAX_RMS_M = 0.25            # hip roofs / ridge-straddling arrays land well above this
MIN_INLIER_FRAC = 0.60


def fit_plane(x: np.ndarray, y: np.ndarray, z: np.ndarray, lat_deg: float,
              *, trim_iters: int = 3, trim_sigma: float = 2.5) -> PlaneFit:
    """Robust ``z = ax + by + c`` fit; returns tilt and downslope azimuth.

    ``x``/``y`` are EPSG:3857 metres and are de-inflated by ``cos(lat)`` before
    fitting. See THE MERCATOR TRAP in the module docstring.
    """
    n0 = len(x)
    if n0 < MIN_POINTS:
        return PlaneFit(float("nan"), float("nan"), n0, 0.0, float("nan"),
                        False, f"only {n0} points")

    k = math.cos(math.radians(lat_deg))
    X = (x - x.mean()) * k
    Y = (y - y.mean()) * k
    Z = np.asarray(z, dtype=float)

    keep = np.ones(n0, dtype=bool)
    a = b = c = 0.0
    resid = np.zeros(n0)
    for _ in range(trim_iters):
        A = np.column_stack([X[keep], Y[keep], np.ones(keep.sum())])
        try:
            coef, *_ = np.linalg.lstsq(A, Z[keep], rcond=None)
        except np.linalg.LinAlgError:
            return PlaneFit(float("nan"), float("nan"), n0, 0.0, float("nan"),
                            False, "singular")
        a, b, c = (float(v) for v in coef)
        resid = Z - (a * X + b * Y + c)
        s = resid[keep].std()
        if not np.isfinite(s) or s == 0:
            break
        new = np.abs(resid) <= trim_sigma * s
        if new.sum() < MIN_POINTS or (new == keep).all():
            break
        keep = new

    inlier_frac = float(keep.sum()) / n0
    rms = float(np.sqrt(np.mean(resid[keep] ** 2)))
    tilt = math.degrees(math.atan(math.hypot(a, b)))
    # (a, b) is the uphill gradient; downslope is its negation. Bearing is
    # clockwise from north, so east component first.
    az = math.degrees(math.atan2(-a, -b)) % 360.0

    ok = (keep.sum() >= MIN_POINTS and rms <= MAX_RMS_M
          and inlier_frac >= MIN_INLIER_FRAC)
    reason = ""
    if not ok:
        bits = []
        if keep.sum() < MIN_POINTS:
            bits.append(f"{int(keep.sum())} inliers")
        if rms > MAX_RMS_M:
            bits.append(f"rms {rms:.2f}m")
        if inlier_frac < MIN_INLIER_FRAC:
            bits.append(f"inlier {inlier_frac:.2f}")
        reason = "; ".join(bits)
    return PlaneFit(tilt, az, n0, inlier_frac, rms, ok, reason)


def fit_polygon(ept: EptSource, geom_3857, lat_deg: float, *,
                shrink: float = POLYGON_SHRINK,
                keep_classes: tuple[int, ...] = KEEP_CLASSES,
                max_depth: int = MAX_DEPTH) -> PlaneFit:
    """Fit one array polygon (shapely geometry already in EPSG:3857)."""
    minx, miny, maxx, maxy = geom_3857.bounds
    r = shrink * 0.5 * math.hypot(maxx - minx, maxy - miny)
    inner = geom_3857.buffer(-r) if r > 0 else geom_3857
    if inner.is_empty or inner.area <= 0:
        inner = geom_3857           # tiny polygon: shrinking would erase it

    pts = ept.points_in_bbox(inner.bounds, max_depth=max_depth)
    if not len(pts):
        return PlaneFit(float("nan"), float("nan"), 0, 0.0, float("nan"),
                        False, "no lidar points")

    m = np.isin(pts.cls, keep_classes) & (pts.ret == 1)
    pts = pts[m]
    if not len(pts):
        return PlaneFit(float("nan"), float("nan"), 0, 0.0, float("nan"),
                        False, "no first-return building points")

    inside = shapely.contains_xy(inner, pts.x, pts.y)
    pts = pts[inside]
    return fit_plane(pts.x, pts.y, pts.z, lat_deg)
