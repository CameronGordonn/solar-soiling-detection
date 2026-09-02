"""Rank detected arrays by how likely they are to carry moss/lichen/organic soiling.

The moss channel is the only soiling mechanism left that could clear the cleaning bar
(`docs/CRAIG_BRIEF_2026-08-19.md` §4): rain removes dust but not moss, lichen, algae or
leaf litter, so it sits outside NREL's IWSR labels by construction and outside everything
`loss_model.py` predicts. Settling it is a **max statistic** -- the question is whether
band thickness reaches f = 0.2-0.3 *anywhere in this town*, not what the average roof looks
like. So the survey has to be pointed at the worst roofs, not a random sample.

Three predictors, all measured rather than assumed:

1. **Canopy overhang, straight from the lidar.** Points sitting well above the array's own
   fitted plane within a short radius are trees (or a taller neighbouring structure). This
   is a far better shade proxy than `worldcover_tree`, which is a 10 m landcover class that
   cannot see one oak over one roof -- and which already misfired once, scoring tree pixels
   as *lower* risk because the model learned it from rural NREL stations.
2. **Low tilt**, from the same plane fit. `src/risk/tilt_response.py` measures a flat roof
   accumulating ~2x a steep one, and standing water is what organics need.
3. **North-facing**, from the fitted azimuth. Less direct sun, slower drying, longer wet.

    PYTHONPATH=. python3 scripts/analyze/rank_moss_candidates.py \
        --arrays outputs/aoi/santa-cruz-w2-21cm/arrays.geojson \
        --planes outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv \
        --out    outputs/aoi/santa-cruz-w2-21cm/moss_candidates.csv --top 120

Output is ranked, so `--top` is a survey list, not a sample. Treat the score as an ordering
device only: it has no calibration and nothing has validated it, because the thing it
predicts has never been measured here. That is the point of the survey.
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

from src.risk.roof_geometry import KEEP_CLASSES, EptSource

#: Horizontal radius around the array searched for overhanging canopy, TRUE metres.
#: An oak that shades a roof at midday is within roughly this distance of it.
CANOPY_RADIUS_M = 9.0

#: A return this far above the array's own plane is not roof furniture, it is a tree or a
#: taller structure. Chosen well above chimney/vent height so flashing does not score.
CANOPY_MIN_HEIGHT_M = 2.5

#: North-ish downslope azimuths dry slowest. Scored as a smooth cosine, not a hard bin.
def _north_score(azimuth_deg: float) -> float:
    if not np.isfinite(azimuth_deg):
        return 0.0
    return 0.5 * (1.0 + math.cos(math.radians(azimuth_deg)))


def _tilt_score(tilt_deg: float) -> float:
    """1.0 at flat, decaying to 0 by ~35 deg. Standing water is the mechanism."""
    if not np.isfinite(tilt_deg):
        return 0.0
    return float(np.clip(1.0 - tilt_deg / 35.0, 0.0, 1.0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arrays", required=True)
    ap.add_argument("--planes", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=120)
    ap.add_argument("--cache-dir", default=".cache/lidar")
    ap.add_argument("--min-area-m2", type=float, default=8.0,
                    help="skip slivers; a fragment too small to photograph usefully")
    args = ap.parse_args()

    import geopandas as gpd

    g = gpd.read_file(args.arrays)
    planes = pd.read_csv(args.planes)
    planes = planes[planes["fit_ok"].astype(bool)]
    lat = float(g.geometry.union_all().centroid.y)
    k = math.cos(math.radians(lat))
    kinv = 1.0 / k
    gm = g.to_crs(3857)
    area = g.to_crs(32610).area.to_numpy()
    ept = EptSource(cache_dir=args.cache_dir)

    keep = planes.set_index("index")
    rows, t0 = [], time.time()
    for n, idx in enumerate(keep.index, start=1):
        if area[idx] < args.min_area_m2:
            continue
        row = keep.loc[idx]
        geom = gm.geometry.iloc[idx]
        halo = geom.buffer(CANOPY_RADIUS_M * kinv)
        pts = ept.points_in_bbox(halo.bounds)
        if not len(pts):
            continue
        pts = pts[np.isin(pts.cls, KEEP_CLASSES)]
        inside = shapely.contains_xy(halo, pts.x, pts.y)
        pts = pts[inside]
        if len(pts) < 30:
            continue

        # Height above the array's OWN fitted plane, extended over the halo.
        cx, cy = geom.centroid.x, geom.centroid.y
        a = math.tan(math.radians(row.tilt_deg))
        de = math.sin(math.radians(row.azimuth_deg))
        dn = math.cos(math.radians(row.azimuth_deg))
        dx = (pts.x - cx) * k
        dy = (pts.y - cy) * k
        # plane drops along the downslope direction at gradient a
        z_plane = np.median(pts.z) - a * (dx * de + dy * dn)
        above = pts.z - z_plane
        canopy = float(np.mean(above > CANOPY_MIN_HEIGHT_M))
        canopy_h = float(np.percentile(above, 98))

        rows.append({
            "index": int(idx), "area_m2": round(float(area[idx]), 1),
            "tilt_deg": round(float(row.tilt_deg), 1),
            "azimuth_deg": round(float(row.azimuth_deg), 1),
            "canopy_frac": round(canopy, 3),
            "canopy_p98_height_m": round(canopy_h, 2),
            "lon": float(g.geometry.iloc[idx].centroid.x),
            "lat": float(g.geometry.iloc[idx].centroid.y),
        })
        if n % 400 == 0:
            print(f"  {n}/{len(keep)}  {time.time()-t0:.0f}s  {len(rows)} scored", flush=True)

    df = pd.DataFrame(rows)
    if df.empty:
        print("no candidates scored")
        return 1
    df["tilt_score"] = df.tilt_deg.map(_tilt_score)
    df["north_score"] = df.azimuth_deg.map(_north_score)
    # Canopy dominates deliberately: shade+moisture is the mechanism, tilt and aspect only
    # modulate how long the array stays wet once shaded.
    df["moss_score"] = (0.60 * df.canopy_frac.clip(0, 1)
                        + 0.25 * df.tilt_score
                        + 0.15 * df.north_score).round(4)
    df = df.sort_values("moss_score", ascending=False)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)

    print(f"\nwrote {args.out}  ({len(df)} arrays scored)")
    print(f"canopy_frac  p50 {df.canopy_frac.median():.3f}  p90 {df.canopy_frac.quantile(.9):.3f}"
          f"  max {df.canopy_frac.max():.3f}")
    print(f"arrays with >20% of nearby returns 2.5 m above the panel plane: "
          f"{int((df.canopy_frac > 0.20).sum())}")
    print(f"\ntop {min(args.top, len(df))} by moss_score:")
    cols = ["index", "moss_score", "canopy_frac", "canopy_p98_height_m", "tilt_deg",
            "azimuth_deg", "area_m2"]
    print(df.head(12)[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
