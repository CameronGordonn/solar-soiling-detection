"""Fetch a 21cm imagery chip for each permitted parcel in the miss queue -> permit-seeded labeling.

Step 2 of the permit-driven labeling plan (docs/PERMIT_DETECTION_ENRICHMENT.md §3). For every
permitted parcel the detector missed, pull a crisp 2025 21cm chip centered on the AUTHORITATIVE
parcel polygon (not the scattered geocode point -- match_dist is often >100m), georeferenced to the
EPSG:3857 grid. The human then confirms a panel on a chip they can actually see, instead of hunting
the address on Google Maps.

Emits:
  - data/interim/permit_chips/<apn>.tif   (21cm RGB, EPSG:3857)
  - data/interim/permit_chips/manifest.csv (apn, year, kw, mount, expected_m2, n_modules_est,
                                            has_parcel, chip, bounds) -- fuel for pre-labels + QA

Reuses the verified fetch/georef functions from scripts/data/fetch_scc_imagery.py.

Usage:
  PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/fetch_permit_chips.py \
      --service-url "https://sccgis.santacruzcountyca.gov/server/rest/services/Cache/Imagery_2025/MapServer/export" \
      --limit 5           # smoke-test on a few before the full queue
"""

from pathlib import Path
import argparse
import logging
import sys

import pandas as pd
import geopandas as gpd
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/ for permit_parcels
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from analyze.permit_parcels import load_parcel_index, parcel_geom, apn_base  # noqa: E402
from data.fetch_scc_imagery import (  # noqa: E402
    export_bbox_and_size, fetch_export, georeference_and_reproject,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KW_PER_M2 = 0.18        # module density: ~180 W per m2 of panel -> kW / 0.18 = m2
WATTS_PER_MODULE = 400  # for a rough module-count estimate
MERC_INFLATE = 1.28     # ~1/cos(37N): ground meters -> EPSG:3857 units at Santa Cruz latitude


def seed_label(center_xy, expected_m2, chip_bounds, min_side_m=5.0):
    """kW-sized square seed centered on the parcel, in chip-normalized YOLO coords.
    A *starting hint* (doc §3) the human nudges onto the panel; not ground truth."""
    import numpy as np
    minx, miny, maxx, maxy = chip_bounds
    side_m = max(min_side_m, (expected_m2 ** 0.5) if expected_m2 else min_side_m)
    half = side_m * MERC_INFLATE / 2.0  # ground m -> 3857 units
    cx, cy = center_xy
    corners = [(cx - half, cy - half), (cx + half, cy - half),
               (cx + half, cy + half), (cx - half, cy + half)]
    pts = []
    for x, y in corners:
        nx = np.clip((x - minx) / (maxx - minx), 0, 1)
        ny = np.clip((maxy - y) / (maxy - miny), 0, 1)  # image y is top-down
        pts += [nx, ny]
    return "0 " + " ".join(f"{v:.6f}" for v in pts)


def _save_png(tif_path, png_path):
    import numpy as np
    from PIL import Image
    with rasterio.open(tif_path) as d:
        Image.fromarray(np.transpose(d.read(), (1, 2, 0))).save(png_path)


def parcel_or_point_bounds_3857(apn, lat, lon, par_index, pad_m):
    """Authoritative parcel bounds (padded) in 3857; fall back to a padded box on the geocode."""
    geom = parcel_geom(par_index, apn)
    if geom is not None:
        minx, miny, maxx, maxy = geom.bounds
        center = (geom.centroid.x, geom.centroid.y)
        has_parcel = True
    else:
        pt = gpd.GeoSeries.from_xy([lon], [lat], crs="EPSG:4326").to_crs("EPSG:3857").iloc[0]
        minx = maxx = pt.x
        miny = maxy = pt.y
        center = (pt.x, pt.y)
        has_parcel = False
    # pad, and enforce a minimum footprint so small lots still show roof + context
    minx -= pad_m; miny -= pad_m; maxx += pad_m; maxy += pad_m
    MIN = 60.0
    if maxx - minx < MIN:
        c = (minx + maxx) / 2; minx, maxx = c - MIN / 2, c + MIN / 2
    if maxy - miny < MIN:
        c = (miny + maxy) / 2; miny, maxy = c - MIN / 2, c + MIN / 2
    return (minx, miny, maxx, maxy), has_parcel, center


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--service-url", required=True)
    p.add_argument("--queue", default="outputs/economics/permit_true_miss_queue.csv", type=Path)
    p.add_argument("--permits", default="data/external/sc_solar_permits.csv", type=Path,
                   help="joined on apn for the mount (racking) field")
    p.add_argument("--parcels",
                   default="data/external/santa_cruz_parcels/aoi_santa-cruz-outreach-v1.geojson", type=Path)
    p.add_argument("--out-dir", default="data/interim/permit_chips", type=Path)
    p.add_argument("--pad-m", type=float, default=12.0, help="padding around the parcel (meters)")
    p.add_argument("--native-gsd", type=float, default=0.208)
    p.add_argument("--export-sr", type=int, default=2227)
    p.add_argument("--limit", type=int, default=0, help="cap rows (0 = all) for smoke-testing")
    p.add_argument("--force", action="store_true", help="re-fetch chips that already exist (default: skip)")
    return p.parse_args(argv)


def main(argv=None):
    import requests
    args = parse_args(argv)
    q = pd.read_csv(args.queue)
    if args.limit:
        q = q.head(args.limit)
    par_index = load_parcel_index(args.parcels)

    # mount (racking) lookup by 8-digit APN base
    mounts = {}
    if args.permits.exists():
        perm = pd.read_csv(args.permits, dtype=str)
        perm["ab"] = perm["apn"].map(apn_base)
        mounts = perm.dropna(subset=["ab"]).groupby("ab")["mount"].first().to_dict()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    rows, ok = [], 0
    for _, r in q.iterrows():
        apn = str(r["apn"])
        bounds, has_parcel, center = parcel_or_point_bounds_3857(
            apn, r["lat"], r["lon"], par_index, args.pad_m)
        safe = apn.replace("/", "-").replace(" ", "")
        out_path = args.out_dir / f"{safe}.tif"
        kw = float(r["kw"]) if pd.notna(r.get("kw")) else None
        expected_m2 = round(kw / KW_PER_M2, 1) if kw else None
        w = h = 0
        if out_path.exists() and not args.force:
            ok += 1
            rows.append(dict(apn=apn, year=r.get("year"), kw=kw, mount=mounts.get(apn_base(apn)),
                             expected_m2=expected_m2,
                             n_modules_est=round(kw * 1000 / WATTS_PER_MODULE) if kw else None,
                             has_parcel=has_parcel, chip=out_path.name, status="SKIP-exists",
                             minx=bounds[0], miny=bounds[1], maxx=bounds[2], maxy=bounds[3]))
            continue
        try:
            bbox_exp, w, h = export_bbox_and_size(bounds, args.export_sr, args.native_gsd)
            tiff = fetch_export(args.service_url, bbox_exp, args.export_sr, w, h, session)
            georeference_and_reproject(tiff, bbox_exp, args.export_sr, bounds, out_path)
            _save_png(out_path, args.out_dir / f"{safe}.png")  # Roboflow ingests PNG, not GeoTIFF
            # kW-sized seed hint in chip coords -> source=permit_seed (never auto-trusted)
            (args.out_dir / f"{safe}.txt").write_text(seed_label(center, expected_m2, bounds))
            ok += 1
            status = "OK"
        except Exception as e:
            status = f"FAIL:{type(e).__name__}"
            logger.warning(f"{apn}: {e}")
        rows.append(dict(
            apn=apn, year=r.get("year"), kw=kw,
            mount=mounts.get(apn_base(apn)),
            expected_m2=expected_m2,
            n_modules_est=round(kw * 1000 / WATTS_PER_MODULE) if kw else None,
            has_parcel=has_parcel, chip=out_path.name, status=status,
            minx=bounds[0], miny=bounds[1], maxx=bounds[2], maxy=bounds[3],
        ))
        logger.info(f"{apn}: parcel={has_parcel} {w}x{h}px kw={kw} -> {out_path.name} [{status}]")

    man = pd.DataFrame(rows)
    man_path = args.out_dir / "manifest.csv"
    man.to_csv(man_path, index=False)
    logger.info(f"done: {ok}/{len(q)} chips fetched  ->  {args.out_dir}  (manifest: {man_path.name})")
    logger.info(f"  parcel-positioned: {int(man['has_parcel'].sum())}/{len(man)} "
                f"(rest fell back to the scattered geocode point)")


if __name__ == "__main__":
    main()
