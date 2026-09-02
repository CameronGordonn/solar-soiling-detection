"""Fit roof-plane tilt + azimuth per detected array from USGS 3DEP lidar.

Replaces the flat ``BASE_SUN = 5.5`` GHI assumption in ``src/risk/economics.py`` with a
per-roof clear-sky POA multiplier, and supplies the ``tilt_deg`` that
``src/solarsoiled/recommend.py:_EXCEPTION_TILT_DEG`` needs to detect flat roofs.

    PYTHONPATH=. python3 scripts/analyze/fit_roof_planes.py \
        --arrays outputs/aoi/santa-cruz-w2-21cm/arrays.geojson \
        --out outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv [--limit 500]

Output columns: tilt_deg, azimuth_deg (downslope bearing cw from north), poa_rel
(annual clear-sky POA relative to the south-20deg reference the dollar chain assumes),
plus fit diagnostics. Rows that fail the fit keep NaN geometry and ``fit_ok=False``
rather than a guessed value -- see ``docs/`` on why median-filling a missing feature is
how ``tilt_deg`` got fabricated in the first place.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.risk import rates as R
from src.risk.roof_geometry import EptSource, SCC_EPT_URL, fit_polygon

#: Reference orientation the dollar chain implicitly assumes today.
REF_TILT, REF_AZIMUTH = R.DEFAULT_TILT_DEG, R.DEFAULT_AZIMUTH_DEG


def annual_poa(lat: float, lon: float, tilt: float, azimuth: float) -> float:
    """Annual clear-sky plane-of-array irradiance, arbitrary units (ratios only)."""
    tot = 0.0
    doy = 0
    hoy = 0
    for ndays in R._DAYS_IN_MONTH:
        for _ in range(ndays):
            doy += 1
            for hour in range(24):
                p = R._clearsky_poa(hoy, doy, hour, lat, lon, tilt, azimuth)
                hoy += 1
                if p > 0:
                    tot += p
    return tot


def poa_interpolator(lat: float, lon: float, tilt_step: float = 2.5,
                     az_step: float = 10.0):
    """Bilinear lookup over (tilt, azimuth). ~800 annual sums, a few seconds.

    Built once rather than per array: ``annual_poa`` is 8,760 trig evaluations and
    the surface is smooth on this grid.
    """
    tilts = np.arange(0.0, 60.0 + tilt_step, tilt_step)
    azes = np.arange(0.0, 360.0 + az_step, az_step)
    grid = np.array([[annual_poa(lat, lon, float(t), float(a)) for a in azes]
                     for t in tilts])
    ref = annual_poa(lat, lon, REF_TILT, REF_AZIMUTH)
    grid /= ref

    def f(tilt: float, azimuth: float) -> float:
        if not (np.isfinite(tilt) and np.isfinite(azimuth)):
            return float("nan")
        t = min(max(tilt, tilts[0]), tilts[-1])
        a = azimuth % 360.0
        ti = np.clip(np.searchsorted(tilts, t) - 1, 0, len(tilts) - 2)
        ai = np.clip(np.searchsorted(azes, a) - 1, 0, len(azes) - 2)
        wt = (t - tilts[ti]) / (tilts[ti + 1] - tilts[ti])
        wa = (a - azes[ai]) / (azes[ai + 1] - azes[ai])
        return float(
            grid[ti, ai] * (1 - wt) * (1 - wa) + grid[ti + 1, ai] * wt * (1 - wa)
            + grid[ti, ai + 1] * (1 - wt) * wa + grid[ti + 1, ai + 1] * wt * wa)

    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arrays", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ept-url", default=SCC_EPT_URL)
    ap.add_argument("--cache-dir", default=".cache/lidar")
    ap.add_argument("--limit", type=int, default=0, help="random subsample for a probe run")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    import geopandas as gpd

    g = gpd.read_file(args.arrays)
    if args.limit and args.limit < len(g):
        g = g.iloc[np.random.default_rng(args.seed).choice(len(g), args.limit, False)]
    cen = g.geometry.union_all().centroid
    lat, lon = float(cen.y), float(cen.x)
    gm = g.to_crs(3857)
    area_m2 = g.to_crs(32610).area.to_numpy()

    print(f"{len(g)} polygons | AOI centroid ({lat:.4f}, {lon:.4f}) "
          f"| mercator k={1/math.cos(math.radians(lat)):.4f}", flush=True)

    ept = EptSource(args.ept_url, args.cache_dir)
    rows = []
    t0 = time.time()
    for n, (idx, geom) in enumerate(gm.geometry.items(), start=1):
        f = fit_polygon(ept, geom, lat)
        rows.append({"index": idx, "area_m2": round(float(area_m2[n - 1]), 2),
                     **f.as_dict()})
        if n % 250 == 0:
            print(f"  {n}/{len(g)}  {time.time()-t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)

    print(f"fits done in {time.time()-t0:.0f}s; building POA surface...", flush=True)
    poa = poa_interpolator(lat, lon)
    df["poa_rel"] = [poa(t, a) if ok else float("nan")
                     for t, a, ok in zip(df.tilt_deg, df.azimuth_deg, df.fit_ok)]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    ok = df[df.fit_ok]
    print(f"\nwrote {out}  ({len(df)} rows, {len(ok)} fitted = {100*len(ok)/len(df):.1f}%)")
    if len(ok):
        for col in ("tilt_deg", "poa_rel"):
            q = ok[col].quantile([0.1, 0.5, 0.9])
            print(f"  {col:10s} p10 {q.iloc[0]:.3f}  p50 {q.iloc[1]:.3f}  "
                  f"p90 {q.iloc[2]:.3f}  ratio {q.iloc[2]/q.iloc[0]:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
