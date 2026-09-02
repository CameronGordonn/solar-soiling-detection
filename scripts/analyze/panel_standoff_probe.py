"""Do the 3DEP returns come off the PANELS or off the bare roof?

This decides whether ``roof_planes.csv`` reports **racking angle** (what the modules
actually do) or merely **roof pitch** (what they'd do if flush-mounted). It matters for the
paper: racking angle is the novel claim, pitch is not.

Method. For each array polygon, fit a plane to a narrow annulus of returns *just outside* it
-- that is bare roof -- then measure how high the array's own returns sit above that plane.
A flush residential mount stands the modules ~0.10-0.20 m off the shingles on rails, so a
positive standoff of that order means the laser is hitting module surfaces. Zero standoff
would mean we are fitting the roof and the panels are invisible to the sensor.

    PYTHONPATH=. python3 scripts/analyze/panel_standoff_probe.py \
        --arrays outputs/aoi/santa-cruz-w2-21cm/arrays.geojson \
        --out    outputs/aoi/santa-cruz-w2-21cm/panel_standoff.csv

Why this script exists separately from ``fit_roof_planes.py``: an earlier inline version of
this test returned n=14, because it demanded a *clean* ring and most polygons fill their roof
face so the ring spilled onto the next plane. The fixes here are (1) a fixed-width metric
annulus instead of one scaled to polygon size, which keeps the band on one face, (2) harder
trimming so a minority second plane in the ring is rejected rather than failing the fit, and
(3) no requirement that the ring fit clear the production RMS gate, only that its own
trimmed inliers are planar.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.risk.roof_geometry import KEEP_CLASSES, MIN_POINTS, EptSource, fit_plane

#: Annulus around the polygon, in TRUE metres. Inner edge clears georegistration slop
#: between 2025 imagery and the 2020 flight; outer edge stays on the same roof face.
RING_INNER_M = 0.60
RING_OUTER_M = 2.20

#: The array polygon is shrunk by this before taking interior points, for the same
#: registration reason -- see POLYGON_SHRINK in src/risk/roof_geometry.py.
INNER_SHRINK_M = 0.40

MIN_INTERIOR_PTS = 15
MIN_RING_PTS = 30
#: Planarity required of the ring's own trimmed inliers. Looser than the production gate
#: because we only need a locally valid roof reference, not a publishable roof fit.
MAX_RING_RMS_M = 0.12


def _fit_ring(x, y, z, lat, iters: int = 6, sigma: float = 2.0):
    """Heavily-trimmed plane fit. Returns (a, b, c, rms, inlier_frac) in TRUE metres."""
    k = math.cos(math.radians(lat))
    X, Y = (x - x.mean()) * k, (y - y.mean()) * k
    keep = np.ones(len(z), bool)
    a = b = c = 0.0
    for _ in range(iters):
        A = np.column_stack([X[keep], Y[keep], np.ones(keep.sum())])
        a, b, c = np.linalg.lstsq(A, z[keep], rcond=None)[0]
        r = z - (a * X + b * Y + c)
        s = r[keep].std()
        if not np.isfinite(s) or s == 0:
            break
        new = np.abs(r) <= sigma * s
        if new.sum() < MIN_POINTS or (new == keep).all():
            break
        keep = new
    r = z - (a * X + b * Y + c)
    return a, b, c, float(np.sqrt(np.mean(r[keep] ** 2))), float(keep.mean()), X.mean, Y.mean


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arrays", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import geopandas as gpd

    g = gpd.read_file(args.arrays)
    if args.limit and args.limit < len(g):
        g = g.iloc[np.random.default_rng(args.seed).choice(len(g), args.limit, False)]
    lat = float(g.geometry.union_all().centroid.y)
    kinv = 1.0 / math.cos(math.radians(lat))     # true metres -> EPSG:3857 metres
    gm = g.to_crs(3857)
    ept = EptSource()

    rows, t0 = [], time.time()
    for n, (idx, geom) in enumerate(gm.geometry.items(), start=1):
        rec = {"index": int(idx), "ok": False, "reason": ""}
        inner = geom.buffer(-INNER_SHRINK_M * kinv)
        if inner.is_empty or inner.area <= 0:
            rec["reason"] = "polygon too small to shrink"
            rows.append(rec)
            continue
        outer = geom.buffer(RING_OUTER_M * kinv)
        pts = ept.points_in_bbox(outer.bounds)
        if len(pts):
            pts = pts[np.isin(pts.cls, KEEP_CLASSES) & (pts.ret == 1)]
        if not len(pts):
            rec["reason"] = "no lidar"
            rows.append(rec)
            continue

        in_mask = shapely.contains_xy(inner, pts.x, pts.y)
        ring_mask = (shapely.contains_xy(outer, pts.x, pts.y)
                     & ~shapely.contains_xy(geom.buffer(RING_INNER_M * kinv), pts.x, pts.y))
        if in_mask.sum() < MIN_INTERIOR_PTS or ring_mask.sum() < MIN_RING_PTS:
            rec["reason"] = f"pts interior={int(in_mask.sum())} ring={int(ring_mask.sum())}"
            rows.append(rec)
            continue

        rp, ip = pts[ring_mask], pts[in_mask]
        k = math.cos(math.radians(lat))
        a, b, c, rms, frac, _, _ = _fit_ring(rp.x, rp.y, rp.z, lat)
        if rms > MAX_RING_RMS_M:
            rec["reason"] = f"ring not planar (rms {rms:.2f} m)"
            rows.append(rec)
            continue

        Xi = (ip.x - rp.x.mean()) * k
        Yi = (ip.y - rp.y.mean()) * k
        standoff = ip.z - (a * Xi + b * Yi + c)

        roof = fit_plane(rp.x, rp.y, rp.z, lat)
        arr = fit_plane(ip.x, ip.y, ip.z, lat)
        d_az = float("nan")
        if roof.ok and arr.ok:
            d = abs(roof.azimuth_deg - arr.azimuth_deg) % 360.0
            d_az = min(d, 360.0 - d)
        rec.update(ok=True, standoff_m=float(np.median(standoff)),
                   ring_rms_m=rms, ring_inlier_frac=frac,
                   n_interior=int(in_mask.sum()), n_ring=int(ring_mask.sum()),
                   roof_tilt_deg=roof.tilt_deg if roof.ok else float("nan"),
                   array_tilt_deg=arr.tilt_deg if arr.ok else float("nan"),
                   d_azimuth_deg=d_az)
        rows.append(rec)
        if n % 500 == 0:
            print(f"  {n}/{len(gm)}  {time.time()-t0:.0f}s  "
                  f"({sum(r['ok'] for r in rows)} usable)", flush=True)

    df = pd.DataFrame(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    ok = df[df.ok]
    print(f"\nwrote {args.out}")
    print(f"usable: {len(ok)} / {len(df)}  ({100*len(ok)/len(df):.1f}%)")
    if not len(ok):
        print(df.reason.value_counts().head().to_string())
        return 1
    print("\ntop rejection reasons:")
    print(df.loc[~df.ok, "reason"].str.replace(r"[\d.]+", "N", regex=True)
          .value_counts().head(4).to_string())
    q = ok.standoff_m.quantile([.1, .25, .5, .75, .9])
    print("\n=== standoff of array returns above the surrounding roof plane (m) ===")
    print(f"  p10 {q.iloc[0]:+.3f}  p25 {q.iloc[1]:+.3f}  MEDIAN {q.iloc[2]:+.3f}  "
          f"p75 {q.iloc[3]:+.3f}  p90 {q.iloc[4]:+.3f}")
    for thr in (0.05, 0.10, 0.15):
        print(f"  arrays >{thr:.2f} m proud: {100*(ok.standoff_m > thr).mean():5.1f}%")
    d = ok.array_tilt_deg - ok.roof_tilt_deg
    d = d[np.isfinite(d)]
    print(f"\n=== array plane vs underlying roof plane (n={len(d)}) ===")
    print(f"  median |tilt difference| {d.abs().median():.2f} deg   "
          f"p90 {d.abs().quantile(.9):.2f} deg")
    print(f"  differing >5 deg (non-flush / tilt-racked): {100*(d.abs() > 5).mean():.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
