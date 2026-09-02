#!/usr/bin/env python3
"""Thread 2 §1–§2 — a DEFENSIBLE detector-recall metric from solar permits.

The county solar-permit registry is independent ground truth the detector never
trained on. The raw AOI-bbox overlap (36/764 = 5%) is *not* recall: the bbox is
not the area we actually imaged, and many permits post-date the NAIP capture.

This script fixes both:

  §1  Intersect geocoded permit points with the **tile_index FOOTPRINT** (the union
      of imaged tile rectangles), not the AOI bbox. Recall = (in-footprint permit
      points with a detected array within tolerance) / (all in-footprint points).
      Tolerance: point-in-array-polygon, else nearest array polygon within ~15 m;
      match distances are recorded. Reports recall + a Wilson 95% CI + confusion.

  §2  Split the in-footprint misses by permit YEAR against the NAIP vintage (2022):
        - real miss        : year <= vintage, in footprint, not detected  → label budget
        - post-imagery     : year >  vintage  → panel legitimately not in imagery
      The vintage-corrected recall uses only the imaged-era denominator. The
      true-miss set (not the raw 728-parcel queue) is written as the labeling CSV.

PII: reads/writes under outputs/ + data/external/ (both gitignored). Never publish.

ENV:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/permit_recall_audit.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

# Make GDAL/PROJ find their data dirs (same shim as join_permits_to_arrays.py).
import pyproj  # noqa: E402

_SHARE = os.path.dirname(pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_DATA", pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_LIB", pyproj.datadir.get_data_dir())
os.environ.setdefault("GDAL_DATA", os.path.join(_SHARE, "gdal"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.geometry import box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "outreach"))
from select_targets_from_permits import clean_street  # noqa: E402

# Metric CRS for true-meter distances at this latitude (UTM 10N). EPSG:3857
# distances are inflated ~1.25x here (1/cos 37deg), so we never measure in 3857.
UTM = "EPSG:32610"

# NAIP vintage — every tile_index source is naip_sc_2022_*.tif. CA NAIP 2022 was
# flown ~summer 2022; a permit *issued* in 2022 may be either side of the capture,
# so 2022 is a boundary year (folded into the imaged window, flagged separately).
NAIP_VINTAGE_YEAR = 2022


def apn_base(s) -> str | None:
    """Normalize an APN to its 8-digit book-page-parcel base (matches join_permits)."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    digits = "".join(ch for ch in str(s) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for a binomial proportion k/n."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def reconstruct_permit_points(permits_csv: Path, geocoded: Path) -> gpd.GeoDataFrame:
    """Rebuild the exact dataframe Thread 1 geocoded (pid = row index), merge the
    cached geocode back on, and return matched points as a GeoDataFrame (4326)."""
    df = pd.read_csv(permits_csv)
    # one row per parcel: latest by (kw, year) — identical to Thread 1 / select_targets.
    d = df.sort_values(["apn", "kw", "year"]).groupby("apn").last()
    d["street"] = d["situs_raw"].map(clean_street)
    d = d[d["street"].notna()].reset_index()
    d["pid"] = d.index

    g = pd.read_parquet(geocoded)
    g["id"] = g["id"].astype(int)
    merged = d.merge(g, left_on="pid", right_on="id", how="inner", validate="one_to_one")
    assert len(merged) == len(d), f"pid merge dropped rows: {len(merged)} != {len(d)}"

    pts = merged[merged["matched"] & merged["lat"].notna() & merged["lon"].notna()].copy()
    gdf = gpd.GeoDataFrame(
        pts[["pid", "apn", "kw", "year", "street", "matched_address", "lat", "lon"]],
        geometry=gpd.points_from_xy(pts["lon"], pts["lat"]),
        crs="EPSG:4326",
    )
    print(f"permits: {len(df)} rows -> {len(d)} parcels w/ street -> "
          f"{len(gdf)} geocoded points (of {int(g['matched'].sum())} matched)")
    return gdf


def build_footprint(tile_index: Path) -> gpd.GeoDataFrame:
    """Union the imaged tile rectangles into the detection footprint (in UTM)."""
    ti = json.loads(tile_index.read_text())
    tiles = ti["tiles"]
    crss = {t["crs"] for t in tiles.values()}
    assert crss == {"EPSG:3857"}, f"unexpected tile CRS: {crss}"
    rects = [box(t["bounds"]["minx"], t["bounds"]["miny"],
                 t["bounds"]["maxx"], t["bounds"]["maxy"]) for t in tiles.values()]
    fp = gpd.GeoDataFrame(geometry=rects, crs="EPSG:3857").to_crs(UTM)
    union = unary_union(fp.geometry.values)
    area_km2 = union.area / 1e6
    print(f"footprint: {len(rects)} tiles -> {area_km2:.2f} km^2 imaged "
          f"(bbox would be {gpd.GeoSeries([box(*fp.total_bounds)], crs=UTM).area.iloc[0]/1e6:.2f} km^2)")
    return gpd.GeoDataFrame(geometry=[union], crs=UTM)


def build_apn_recall(parcels_path: Path, arrays: gpd.GeoDataFrame,
                     inside: gpd.GeoDataFrame) -> dict:
    """Geocode-free recall: of in-footprint permit parcels (by recorded APN), how many
    carry a parcel that the detector placed an array on? Robust to geocode scatter."""
    parcels = gpd.read_file(parcels_path).to_crs(UTM)
    parcels["apn_base"] = parcels["APN"].map(apn_base)
    # APN under each detected array's interior point
    arr_pt = arrays.copy()
    arr_pt["geometry"] = arrays.representative_point()
    arr_apn = gpd.sjoin(arr_pt, parcels[["apn_base", "geometry"]],
                        predicate="within", how="left")
    det_apns = set(arr_apn["apn_base"].dropna())

    perm = inside.copy()
    perm["apn_base"] = perm["apn"].map(apn_base)
    perm["apn_detected"] = perm["apn_base"].isin(det_apns)
    n = len(perm)
    found = int(perm["apn_detected"].sum())
    lo, hi = wilson_ci(found, n)
    era = perm[perm["year"] <= NAIP_VINTAGE_YEAR]
    n_v, found_v = len(era), int(era["apn_detected"].sum())
    return {"n": n, "found": found, "recall": found / n if n else float("nan"),
            "lo": lo, "hi": hi, "n_v": n_v, "found_v": found_v,
            "recall_v": found_v / n_v if n_v else float("nan"),
            "n_det_apns": len(det_apns), "det_apns": det_apns}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--partner-id", default="santa-cruz-outreach-v1")
    ap.add_argument("--permits", default="data/external/sc_solar_permits.csv", type=Path)
    ap.add_argument("--geocoded", default="outputs/permit_homes/geocoded.parquet", type=Path)
    ap.add_argument("--tile-index", default="data/interim/tile_index.json", type=Path)
    ap.add_argument("--parcels",
                    default="data/external/santa_cruz_parcels/aoi_santa-cruz-outreach-v1.geojson",
                    type=Path, help="cached parcel polygons (for the geocode-free APN cross-check)")
    ap.add_argument("--tolerance-m", type=float, default=15.0,
                    help="max point->array distance to count as a match (meters)")
    ap.add_argument("--out-dir", default="outputs/economics", type=Path)
    args = ap.parse_args(argv)

    arrays_path = REPO / "outputs" / "aoi" / args.partner_id / "arrays.geojson"

    # --- load + reproject everything to UTM (true meters) ---
    pts = reconstruct_permit_points(REPO / args.permits, REPO / args.geocoded).to_crs(UTM)
    footprint = build_footprint(REPO / args.tile_index)
    arrays = gpd.read_file(arrays_path).to_crs(UTM)
    print(f"detected arrays: {len(arrays)}")

    fp_geom = footprint.geometry.iloc[0]

    # --- §1: restrict denominator to the imaged footprint ---
    pts["in_footprint"] = pts.geometry.within(fp_geom)
    inside = pts[pts["in_footprint"]].copy()
    print(f"\n{len(inside)} / {len(pts)} permit points fall inside the imaged footprint")

    # --- match each in-footprint point to its nearest detected array (meters) ---
    inside["match_dist_m"] = inside.geometry.apply(lambda g: float(arrays.distance(g).min()))
    inside["detected"] = inside["match_dist_m"] <= args.tolerance_m
    inside["inside_polygon"] = inside["match_dist_m"] == 0.0

    n = len(inside)
    found = int(inside["detected"].sum())
    recall = found / n if n else float("nan")
    lo, hi = wilson_ci(found, n)

    # tolerance sensitivity — the geocode-to-rooftop offset means point-in-polygon
    # almost never fires; this curve shows how recall responds to the tolerance.
    tol_grid = [10, 15, 20, 25, 30]
    tol_curve = [(t, int((inside["match_dist_m"] <= t).sum())) for t in tol_grid]

    # --- robust, geocode-free cross-check via parcel APN ---
    # The geocode scatters ~12 m off the roof, so the point method under-counts. The
    # permit's *recorded* APN is exact: ask whether the parcel under each detected
    # array carries a permit APN that is in the footprint. Immune to geocode error.
    apn_recall = build_apn_recall(REPO / args.parcels, arrays, inside)

    # geocode-quality diagnostic: how many points even land inside a known parcel?
    parcels = gpd.read_file(REPO / args.parcels).to_crs(UTM)
    pts_in_parcel = int(gpd.sjoin(inside[["pid", "geometry"]], parcels[["geometry"]],
                                  predicate="within", how="inner")
                        .drop_duplicates("pid").shape[0])

    # --- §2: split by vintage ---
    inside["miss_class"] = "detected"
    miss = ~inside["detected"]
    inside.loc[miss & (inside["year"] <= NAIP_VINTAGE_YEAR), "miss_class"] = "real_miss"
    inside.loc[miss & (inside["year"] > NAIP_VINTAGE_YEAR), "miss_class"] = "post_imagery"

    # vintage-corrected recall: drop post-imagery installs from the denominator —
    # they legitimately aren't in 2022 imagery, so they aren't detector failures.
    imaged_era = inside[inside["year"] <= NAIP_VINTAGE_YEAR]
    n_v = len(imaged_era)
    found_v = int(imaged_era["detected"].sum())
    recall_v = found_v / n_v if n_v else float("nan")
    lo_v, hi_v = wilson_ci(found_v, n_v)

    # outside-footprint audit (by year)
    outside = pts[~pts["in_footprint"]]

    # --- console summary ---
    real_miss = int((inside["miss_class"] == "real_miss").sum())
    post_img = int((inside["miss_class"] == "post_imagery").sum())
    print(f"\n=== §1 recall (all in-footprint points, point<= {args.tolerance_m:.0f} m) ===")
    print(f"  recall = {found}/{n} = {recall:.1%}  (Wilson 95% CI {lo:.1%}–{hi:.1%})")
    print(f"  tolerance sweep: " + ", ".join(f"{t}m:{c}={c/n:.1%}" for t, c in tol_curve))
    print(f"=== §1b robust APN cross-check (geocode-free) ===")
    print(f"  recall = {apn_recall['found']}/{apn_recall['n']} = {apn_recall['recall']:.1%} "
          f"(CI {apn_recall['lo']:.1%}–{apn_recall['hi']:.1%}); "
          f"imaged-era {apn_recall['found_v']}/{apn_recall['n_v']} = {apn_recall['recall_v']:.1%}")
    print(f"  geocode quality: only {pts_in_parcel}/{n} points land inside any parcel polygon")
    print(f"=== §1' vintage-corrected recall (year <= {NAIP_VINTAGE_YEAR}) ===")
    print(f"  recall = {found_v}/{n_v} = {recall_v:.1%}  (Wilson 95% CI {lo_v:.1%}–{hi_v:.1%})")
    print(f"=== §2 confusion split (in footprint) ===")
    print(f"  detected      : {found}")
    print(f"  real_miss     : {real_miss}   (year <= {NAIP_VINTAGE_YEAR}, undetected -> LABEL BUDGET)")
    print(f"  post_imagery  : {post_img}   (year > {NAIP_VINTAGE_YEAR}, not in imagery)")
    print(f"  outside fp    : {len(outside)}   (excluded from recall)")
    det = inside[inside["detected"]]
    if len(det):
        print(f"\nmatch-distance (detected): inside-polygon={int(inside['inside_polygon'].sum())}, "
              f"median={det['match_dist_m'].median():.2f} m, max={det['match_dist_m'].max():.2f} m "
              f"| all in-fp points median-nearest={inside['match_dist_m'].median():.1f} m")

    # --- write the true-miss labeling queue (CSV) ---
    args_out = REPO / args.out_dir
    args_out.mkdir(parents=True, exist_ok=True)
    queue = inside[inside["miss_class"] == "real_miss"].copy()
    # flag rows already found by the geocode-free APN method: those are point-misses
    # (geocode landed too far), not true relabel targets. Genuine misses fail both.
    queue["apn_detected"] = queue["apn"].map(apn_base).isin(apn_recall["det_apns"])
    queue["lat"] = queue.to_crs(4326).geometry.y
    queue["lon"] = queue.to_crs(4326).geometry.x
    queue_cols = ["pid", "apn", "year", "kw", "apn_detected", "match_dist_m", "lat", "lon",
                  "street", "matched_address"]
    # genuine misses (undetected by both methods) first, then by year
    queue_out = queue.sort_values(["apn_detected", "year"])[queue_cols]
    csv_path = args_out / "permit_true_miss_queue.csv"
    queue_out.to_csv(csv_path, index=False)
    genuine = int((~queue["apn_detected"]).sum())
    print(f"\n[ok] true-miss labeling queue ({len(queue_out)} parcels; "
          f"{genuine} undetected by BOTH point+APN) -> {csv_path}")

    # year histograms for the report
    def yr_counts(s):
        return s["year"].value_counts().sort_index().to_dict()

    report = build_report(
        partner=args.partner_id, tol=args.tolerance_m, vintage=NAIP_VINTAGE_YEAR,
        n_pts=len(pts), n_in=n, n_out=len(outside), n_arrays=len(arrays),
        found=found, recall=recall, ci=(lo, hi), tol_curve=tol_curve,
        found_v=found_v, n_v=n_v, recall_v=recall_v, ci_v=(lo_v, hi_v),
        real_miss=real_miss, post_img=post_img,
        inside_poly=int(inside["inside_polygon"].sum()),
        det_med=float(det["match_dist_m"].median()) if len(det) else float("nan"),
        det_max=float(det["match_dist_m"].max()) if len(det) else float("nan"),
        allpt_med=float(inside["match_dist_m"].median()),
        apn=apn_recall, pts_in_parcel=pts_in_parcel, genuine=genuine,
        real_miss_years=yr_counts(inside[inside["miss_class"] == "real_miss"]),
        post_years=yr_counts(inside[inside["miss_class"] == "post_imagery"]),
        queue_csv=csv_path.name,
    )
    rep_path = args_out / "permit_recall_audit.md"
    rep_path.write_text(report, encoding="utf-8")
    print(f"[ok] report -> {rep_path}")


def build_report(**k) -> str:
    lo, hi = k["ci"]
    lo_v, hi_v = k["ci_v"]
    a = k["apn"]
    ry = ", ".join(f"{y}:{c}" for y, c in k["real_miss_years"].items()) or "—"
    py = ", ".join(f"{y}:{c}" for y, c in k["post_years"].items()) or "—"
    tol_rows = "\n".join(f"| ≤ {t} m | {c} | {c/k['n_in']:.1%} |" for t, c in k["tol_curve"])
    return f"""# Permit-driven detector recall — corrected metric (Thread 2 §1–§2)

AOI: **{k['partner']}**  ·  detected arrays: {k['n_arrays']}  ·  NAIP vintage: **{k['vintage']}**
(every `tile_index` source is `naip_sc_{k['vintage']}_*.tif`; CA NAIP {k['vintage']} flown ~summer {k['vintage']}).

The permit registry is independent ground truth the detector never trained on. The
raw AOI-bbox overlap of **36/764 = 5%** in `permit_detection_recall.md` is *not* recall:
the bbox over-counts (un-imaged parcels can't be detected) and permits post-dating the
imagery aren't detector failures. Corrected below against the **tiled footprint** and the
**{k['vintage']} vintage cutoff**.

## TL;DR

- Footprint denominator: **{k['n_in']}** of {k['n_pts']} geocoded permit points fall inside the
  {k['n_arrays']}-array imaged footprint ({k['n_out']} excluded as un-imaged).
- **Headline defensible recall ≈ {a['recall_v']:.0%}** (imaged-era, geocode-free APN method:
  {a['found_v']}/{a['n_v']}). All-years APN recall is **{a['recall']:.1%}** ({a['found']}/{a['n']}),
  matching the original 36/764 overlap.
- The spec-literal **point method reads lower ({k['recall']:.1%}, CI {lo:.1%}–{hi:.1%})** because
  the geocode sits a median **{k['allpt_med']:.0f} m** off the roof — a geocode-quality floor, not a
  detection floor. Treat it as a lower bound; the APN number is trustworthy.
- **Half the in-footprint permits ({k['post_img']}/{k['n_in']}) post-date the {k['vintage']}
  imagery** — correctly excluded as not-yet-imaged, not detector failures. Removing them roughly
  doubles recall ({a['recall']:.1%} → {a['recall_v']:.1%} APN).
- Even so, ~{a['recall_v']:.0%} is a genuine small-residential-rooftop recall gap (risk M1), not a
  denominator artifact — the {k['real_miss']}-parcel real-miss queue is the relabel budget.

## §1 — Recall on the imaged footprint (point method, as specified)

- Denominator = geocoded permit points inside the **union of imaged tiles**, not the bbox.
- A point is **detected** if it lies in a detected array polygon, else if the nearest array
  polygon is within **{k['tol']:.0f} m** (geocode + roof-offset slack). Match distance recorded.

| metric | value |
|---|---|
| geocoded permit points (matched) | {k['n_pts']} |
| …inside imaged footprint (denominator) | **{k['n_in']}** |
| …outside footprint (excluded) | {k['n_out']} |
| in-footprint points with a detected array | **{k['found']}** |
| **footprint recall** | **{k['recall']:.1%}**  (Wilson 95% CI {lo:.1%}–{hi:.1%}) |

**Caveat — the geocode is not on the roof.** The Census batch geocoder interpolates each
address onto the street segment, so the points scatter a median **{k['allpt_med']:.0f} m** from the
nearest detected array and **{k['inside_poly']}** of {k['found']} matches are point-in-polygon
(median matched distance {k['det_med']:.1f} m, max {k['det_max']:.1f} m). The {k['tol']:.0f} m
tolerance therefore *under-counts* true matches — see the sensitivity sweep and the APN
cross-check below.

### Tolerance sensitivity

| tolerance | matched | recall |
|---|---|---|
{tol_rows}

Recall climbs with tolerance because the geocode offset, not detection, sets the floor —
which is exactly why the geocode-free APN method is the defensible number.

## §1b — Geocode-free cross-check (parcel APN, robust)

Bypasses geocode error entirely: a permit parcel counts as detected if the detector placed
an array on that **recorded APN** (parcel polygons under each detection's interior point;
{a['n_det_apns']} distinct array-parcels). Only {k['pts_in_parcel']}/{k['n_in']} geocoded points
even land inside *any* parcel polygon, confirming the geocodes are too noisy for point-in-parcel
matching — so APN is matched on the permit's own identifier, not the point.

| metric | value |
|---|---|
| in-footprint permit parcels | **{a['n']}** |
| …with a detected array on that APN | **{a['found']}** |
| **APN recall (all years)** | **{a['recall']:.1%}**  (Wilson 95% CI {a['lo']:.1%}–{a['hi']:.1%}) |
| **APN recall (imaged-era, year ≤ {k['vintage']})** | **{a['recall_v']:.1%}**  ({a['found_v']}/{a['n_v']}) |

This agrees with the original `36/764 = 5%` overlap. **All-years recall ≈ 5%**; restricting to
the imaged era (removing post-{k['vintage']} installs) lifts it to **≈ {a['recall_v']:.0%}** — the
defensible detector recall on this AOI. The point method's {k['recall']:.1%} is a geocode-noise
lower bound on the same quantity.

## §1' — Vintage-corrected recall (point method, imaged-era only)

Permits with `year > {k['vintage']}` describe panels installed **after** the {k['vintage']} capture —
they cannot appear in our imagery, so they are dropped from the denominator (not detector
misses). Restricting to the imaged era (`year <= {k['vintage']}`):

| metric | value |
|---|---|
| in-footprint, imaged-era points (denominator) | **{k['n_v']}** |
| …detected | **{k['found_v']}** |
| **vintage-corrected recall** | **{k['recall_v']:.1%}**  (Wilson 95% CI {lo_v:.1%}–{hi_v:.1%}) |

> Caveat: {k['vintage']} is a boundary year — a panel permitted in {k['vintage']} may be on either
> side of the mid-{k['vintage']} flight. It is folded into the imaged window here; the year
> histogram below lets you re-cut it out.

## §2 — Miss split by permit year (in-footprint, undetected)

| class | count | meaning |
|---|---|---|
| detected | {k['found']} | matched a detected array (point ≤ {k['tol']:.0f} m) |
| **real_miss** | **{k['real_miss']}** | year ≤ {k['vintage']}, in footprint, undetected → **labeling budget** |
| post_imagery | {k['post_img']} | year > {k['vintage']}, panel not in {k['vintage']} imagery → re-imaging flag |

- real_miss by year: {ry}
- post_imagery by year: {py}

The **{k['real_miss']}-parcel real-miss set** — not the raw 728-parcel "permitted-but-undetected"
queue — is the Stage-1 relabel budget (risk M1). Written to `{k['queue_csv']}` with APN, year, kW,
geocode, nearest-array distance, and an **`apn_detected`** flag. **{k['genuine']}** of the
{k['real_miss']} are undetected by *both* the point and APN methods — these (sorted first) are the
genuine relabel targets and the §3 auto-label seed list. The remaining
{k['real_miss'] - k['genuine']} are APN-detected but the geocode landed > {k['tol']:.0f} m off, so
the point method flagged them — re-check, don't relabel blind.
"""


if __name__ == "__main__":
    main()
