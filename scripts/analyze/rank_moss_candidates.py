"""Build the Persistent Soiling inspection screen for detected arrays.

The moss channel is the only soiling mechanism left that could clear the cleaning bar
(`docs/CRAIG_BRIEF_2026-08-19.md` §4): rain removes dust but not moss, lichen, algae or
leaf litter, so it sits outside NREL's IWSR labels by construction and outside everything
`loss_model.py` predicts. Settling it is a **max statistic** -- the question is whether
band thickness reaches f = 0.2-0.3 *anywhere in this town*, not what the average roof looks
like. So the survey has to be pointed at the worst roofs, not a random sample.

The screen uses two per-array predictors:

1. **Canopy exposure, straight from the lidar.** Points sitting well above the array's own
   fitted plane within a short radius are candidate tree canopy. Santa Cruz's 3DEP delivery
   marks most non-ground returns as "unclassified", so a valid canopy proxy is either a
   vegetation-classified return *or* an elevated, non-ground multiple-return pulse. This is
   still a screen, not a tree inventory: a taller neighbouring structure can occasionally
   qualify and should be resolved in the visual inspection. It is nevertheless far more
   local than `worldcover_tree`, a 10 m landcover class that cannot see one oak over one roof.
2. **Low tilt**, from the same plane fit. `src/risk/tilt_response.py` measures a flat roof
   accumulating ~2x a steep one, and standing water is what organics need.
The default score is 70% low-tilt and 30% canopy exposure. It flags the top 10% of
measurable arrays in an AOI for inspection. It is an uncalibrated prioritisation tool,
not a persistent-loss percentage and not an automatic cleaning recommendation.

    PYTHONPATH=. python3 scripts/analyze/rank_moss_candidates.py \
        --arrays outputs/aoi/santa-cruz-w2-21cm/arrays.geojson \
        --planes outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv \
        --out    outputs/aoi/santa-cruz-w2-21cm/persistent_soiling_screen.csv

Output is ranked, so the top decile is a survey list, not a sample. Treat the score as
an ordering device only: it has no calibration and nothing has validated it, because the
thing it predicts has never been measured here. That is the point of the survey.
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

from src.risk.persistent_soiling import (
    CANOPY_SEARCH_RADIUS_M,
    CANOPY_WEIGHT,
    TILT_WEIGHT,
    flag_top_fraction,
    persistent_soiling_components,
    persistent_soiling_score,
)
from src.risk.roof_geometry import EptSource, POLYGON_SHRINK

#: Horizontal radius around the array searched for overhanging canopy, TRUE metres.
#: An oak that shades a roof at midday is within roughly this distance of it.
CANOPY_RADIUS_M = CANOPY_SEARCH_RADIUS_M

#: A return this far above the array's own plane is not roof furniture, it is a tree or a
#: taller structure. Chosen well above chimney/vent height so flashing does not score.
CANOPY_MIN_HEIGHT_M = 2.5

VEGETATION_CLASSES = (3, 4, 5)
SURFACE_CLASSES = (1, 6)
GROUND_CLASSES = (2,)
CANOPY_GRID_M = 1.0
CANOPY_MIN_RETURNS_PER_CELL = 3
CANOPY_MIN_SUPPORTED_CELLS = 3


def candidate_canopy_mask(points: np.ndarray, above_plane_m: np.ndarray) -> np.ndarray:
    """Return likely canopy returns in a 3DEP halo.

    The Santa Cruz point cloud keeps almost all vegetation in ASPRS class 1
    (unclassified), so class 3/4/5 alone produces a silently empty tree layer.
    A return high above the panel plane with more than one return in its pulse is
    a conservative canopy signature; hard structures are predominantly single-return.
    Keep classified vegetation too for AOIs where the provider did label it.
    """
    classified_vegetation = np.isin(points.cls, VEGETATION_CLASSES)
    elevated_multi_return = (
        ~np.isin(points.cls, GROUND_CLASSES)
        & (points.nret > 1)
    )
    return (above_plane_m > CANOPY_MIN_HEIGHT_M) & (
        classified_vegetation | elevated_multi_return
    )


def supported_canopy_mask(
    points: np.ndarray,
    candidate_mask: np.ndarray,
    *,
    mercator_to_true_m: float,
) -> np.ndarray:
    """Keep only canopy candidates covering a small, multi-return footprint.

    A one-off high return can be a laser artifact or a piece of roof equipment.
    At this survey's ~18 points/m2 density, three 1 m grid cells with at least
    three candidate returns each is a modest but meaningful canopy footprint.
    """
    keep = np.zeros(len(points), dtype=bool)
    indices = np.flatnonzero(candidate_mask)
    if not len(indices):
        return keep

    grid = np.floor(np.column_stack((
        points.x[indices] * mercator_to_true_m / CANOPY_GRID_M,
        points.y[indices] * mercator_to_true_m / CANOPY_GRID_M,
    ))).astype(np.int64)
    _cells, inverse, counts = np.unique(grid, axis=0, return_inverse=True, return_counts=True)
    supported_cells = counts >= CANOPY_MIN_RETURNS_PER_CELL
    if int(supported_cells.sum()) < CANOPY_MIN_SUPPORTED_CELLS:
        return keep
    keep[indices[supported_cells[inverse]]] = True
    return keep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arrays", required=True)
    ap.add_argument("--planes", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top-fraction", type=float, default=0.10,
                    help="fraction of scored arrays to flag for inspection (default: 0.10)")
    ap.add_argument("--tilt-weight", type=float, default=TILT_WEIGHT,
                    help="provisional low-tilt weight; must sum with canopy weight to 1")
    ap.add_argument("--canopy-weight", type=float, default=CANOPY_WEIGHT,
                    help="provisional canopy-exposure weight; must sum with tilt weight to 1")
    ap.add_argument("--cache-dir", default=".cache/lidar")
    ap.add_argument("--min-area-m2", type=float, default=8.0,
                    help="skip slivers; a fragment too small to photograph usefully")
    args = ap.parse_args(argv)
    if args.tilt_weight < 0 or args.canopy_weight < 0 or not math.isclose(
        args.tilt_weight + args.canopy_weight, 1.0, abs_tol=1e-9
    ):
        ap.error("--tilt-weight and --canopy-weight must be non-negative and sum to 1")

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
        # Roof-plane ``index`` is the source GeoDataFrame row position, not
        # necessarily its current pandas index label.
        pos = int(idx)
        if pos < 0 or pos >= len(g):
            continue
        if area[pos] < args.min_area_m2:
            continue
        row = keep.loc[idx]
        geom = gm.geometry.iloc[pos]
        halo = geom.buffer(CANOPY_RADIUS_M * kinv)
        pts = ept.points_in_bbox(halo.bounds)
        if not len(pts):
            continue
        inside = shapely.contains_xy(halo, pts.x, pts.y)
        pts = pts[inside]
        if len(pts) < 30:
            continue
        # Anchor the roof plane inside the array itself, not in the surrounding
        # halo. The previous halo-wide median could be pulled upward by a tree or
        # taller neighbouring roof and erase exactly the canopy signal we seek.
        panel = geom.buffer(-POLYGON_SHRINK * kinv)
        if panel.is_empty:
            panel = geom
        panel_inside = shapely.contains_xy(panel, pts.x, pts.y)
        surface_pts = pts[panel_inside & np.isin(pts.cls, SURFACE_CLASSES)]
        if len(surface_pts) < 30:
            continue

        # Height above the array's OWN fitted plane, extended over the halo.
        cx, cy = geom.centroid.x, geom.centroid.y
        a = math.tan(math.radians(row.tilt_deg))
        de = math.sin(math.radians(row.azimuth_deg))
        dn = math.cos(math.radians(row.azimuth_deg))
        dx = (pts.x - cx) * k
        dy = (pts.y - cy) * k
        # Plane drops along the downslope direction at gradient a. Recover its
        # centroid intercept from panel returns before extending it into the halo.
        surface_dx = (surface_pts.x - cx) * k
        surface_dy = (surface_pts.y - cy) * k
        z0 = np.median(surface_pts.z + a * (surface_dx * de + surface_dy * dn))
        z_plane = z0 - a * (dx * de + dy * dn)
        above = pts.z - z_plane
        raw_canopy_mask = candidate_canopy_mask(pts, above)
        canopy_mask = supported_canopy_mask(
            pts, raw_canopy_mask, mercator_to_true_m=k
        )
        canopy = float(np.mean(canopy_mask))
        canopy_h = float(np.percentile(above[canopy_mask], 98)) if canopy_mask.any() else 0.0
        nearest_canopy_m = float("nan")
        if canopy_mask.any():
            canopy_points = shapely.points(pts.x[canopy_mask], pts.y[canopy_mask])
            # EPSG:3857 distances are inflated by 1 / cos(latitude); restore true metres.
            nearest_canopy_m = float(shapely.distance(geom, canopy_points).min()) * k

        rows.append({
            "plane_index": pos,
            "array_id": int(g.iloc[pos].get("array_id", pos)),
            "area_m2": round(float(area[pos]), 1),
            "tilt_deg": round(float(row.tilt_deg), 1),
            "azimuth_deg": round(float(row.azimuth_deg), 1),
            "canopy_frac": round(canopy, 3),
            "canopy_p98_height_m": round(canopy_h, 2),
            "canopy_method": "classified_vegetation_or_elevated_multi_return_footprint",
            "nearest_canopy_m": round(nearest_canopy_m, 2),
            "lon": float(g.geometry.iloc[pos].centroid.x),
            "lat": float(g.geometry.iloc[pos].centroid.y),
        })
        if n % 400 == 0:
            print(f"  {n}/{len(keep)}  {time.time()-t0:.0f}s  {len(rows)} scored", flush=True)

    df = pd.DataFrame(rows)
    if df.empty:
        print("no candidates scored")
        return 1
    components = [
        persistent_soiling_components(tilt, canopy, nearest)
        for tilt, canopy, nearest in zip(df.tilt_deg, df.canopy_frac, df.nearest_canopy_m)
    ]
    df[["low_tilt_score", "canopy_exposure_score"]] = components
    df["persistent_soiling_score"] = [
        persistent_soiling_score(
            tilt, canopy, nearest, tilt_weight=args.tilt_weight, canopy_weight=args.canopy_weight
        )
        for tilt, canopy, nearest in zip(df.tilt_deg, df.canopy_frac, df.nearest_canopy_m)
    ]
    df["tilt_weight"] = args.tilt_weight
    df["canopy_weight"] = args.canopy_weight
    # Persist the screening policy with every row.  The public dashboard must be
    # able to show the exact settings used for this AOI, rather than hard-coding
    # the current defaults and silently drifting if a future run changes them.
    df["canopy_radius_m"] = CANOPY_RADIUS_M
    df["inspection_fraction"] = args.top_fraction
    df = df.sort_values(["persistent_soiling_score", "array_id"], ascending=[False, True])
    df = flag_top_fraction(df, fraction=args.top_fraction)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)

    print(f"\nwrote {args.out}  ({len(df)} arrays scored)")
    print(f"canopy_frac  p50 {df.canopy_frac.median():.3f}  p90 {df.canopy_frac.quantile(.9):.3f}"
          f"  max {df.canopy_frac.max():.3f}")
    print(f"arrays with >20% candidate-canopy returns 2.5 m above the panel plane: "
          f"{int((df.canopy_frac > 0.20).sum())}")
    n_flagged = int(df.persistent_soiling_top_decile.sum())
    print(f"flagged for persistent-soiling inspection: {n_flagged}/{len(df)} "
          f"({args.top_fraction:.0%})")
    print("\ntop persistent-soiling inspection priorities:")
    cols = ["array_id", "plane_index", "persistent_soiling_score", "persistent_soiling_top_decile",
            "low_tilt_score", "canopy_exposure_score", "canopy_frac", "nearest_canopy_m",
            "canopy_p98_height_m", "tilt_deg", "area_m2"]
    print(df.head(12)[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
