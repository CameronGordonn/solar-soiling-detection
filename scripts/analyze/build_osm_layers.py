"""Extract the two OSM layers the risk model needs from a Geofabrik .osm.pbf, once.

WHY
---
`location_features.distance_to_highway_m` / `distance_to_agriculture_m` issued one Overpass
query per array. Scoring a 3,362-array AOI meant thousands of rate-limited API calls to fetch
the same few roads over and over, and Overpass answered a meaningful fraction with 429/504.
Those failures return None, which lands in the feature row as a silently missing value rather
than an error -- so the damage is a quietly degraded risk matrix, not a crash.

County scale makes it not merely slow but impossible: 1,153 km2 is ~46x this AOI, and the
6.3cm sweep is 17,300 tiles. There is no per-array-API version of that.

So: pull the layers once from a state extract, compute distances locally with a spatial index.
Exact, offline, no rate limit, and it turns hours of API calls into seconds of sjoin_nearest.

WHAT IT WRITES
--------------
    data/external/osm/highways.parquet     major roads (motorway|trunk|primary|secondary)
    data/external/osm/agriculture.parquet  farmland|orchard|vineyard|farmyard

Both in EPSG:3857 with a `source_tag` column, so a later change of which tags count is
auditable rather than baked in.

USAGE
-----
    PYTHONPATH=. python scripts/analyze/build_osm_layers.py \
        --pbf data/external/osm/california-latest.osm.pbf
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WORK_CRS = "EPSG:3857"

# Kept identical to the Overpass queries these replace, so the swap is a change of TRANSPORT
# and not a change of definition. If these ever diverge, the cached-Overpass validation in
# validate_osm_layers will catch it.
HIGHWAY_CLASSES = {"motorway", "trunk", "primary", "secondary"}
AGRI_LANDUSE = {"farmland", "orchard", "vineyard", "farmyard"}


def _read_layer(pbf: Path, layer: str, columns: list[str], bbox=None) -> gpd.GeoDataFrame:
    """Read one GDAL OSM layer, keeping only the columns we filter on.

    `bbox` (WGS84 minx, miny, maxx, maxy) is pushed down to GDAL so the features are never
    materialised. California's `multipolygons` layer does not comfortably fit in this box's
    7.4 GB alongside a geopandas copy; the statewide read was killed partway through. Only the
    neighbourhood around the AOI can ever win a nearest-neighbour query anyway.
    """
    logger.info("reading layer %r from %s%s", layer, pbf.name, f" bbox={bbox}" if bbox else "")
    gdf = gpd.read_file(pbf, layer=layer, columns=columns, bbox=bbox, engine="pyogrio")
    logger.info("  %d raw features", len(gdf))
    return gdf


def extract(pbf: Path, out_dir: Path, bbox=None, overwrite: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {}

    # --- highways: OSM 'lines' carries the highway tag -----------------------------------
    hw_path = out_dir / "highways.parquet"
    if hw_path.is_file() and not overwrite:
        stats["highways"] = "skipped (exists)"
        logger.info("highways: %s already built [SKIP]", hw_path.name)
    else:
        lines = _read_layer(pbf, "lines", ["highway"], bbox=bbox)
        hw = lines[lines["highway"].isin(HIGHWAY_CLASSES)].copy()
        hw = hw.rename(columns={"highway": "source_tag"})[["source_tag", "geometry"]]
        hw = hw[~hw.geometry.isna() & ~hw.geometry.is_empty].to_crs(WORK_CRS)
        hw.to_parquet(hw_path)
        stats["highways"] = len(hw)
        logger.info("highways: %d features -> %s", len(hw), hw_path)
        del lines, hw

    # --- agriculture: landuse polygons live in 'multipolygons' ---------------------------
    ag_path = out_dir / "agriculture.parquet"
    if ag_path.is_file() and not overwrite:
        stats["agriculture"] = "skipped (exists)"
        logger.info("agriculture: %s already built [SKIP]", ag_path.name)
    else:
        polys = _read_layer(pbf, "multipolygons", ["landuse", "natural"], bbox=bbox)
        agri = polys[polys["landuse"].isin(AGRI_LANDUSE) | (polys["natural"] == "farmland")].copy()
        agri["source_tag"] = agri["landuse"].fillna(agri["natural"])
        agri = agri[["source_tag", "geometry"]]
        agri = agri[~agri.geometry.isna() & ~agri.geometry.is_empty].to_crs(WORK_CRS)
        agri.to_parquet(ag_path)
        stats["agriculture"] = len(agri)
        logger.info("agriculture: %d features -> %s", len(agri), ag_path)

    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", type=Path,
                    default=REPO_ROOT / "data/external/osm/california-latest.osm.pbf")
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data/external/osm")
    ap.add_argument("--bbox", default="",
                    help="WGS84 'minx,miny,maxx,maxy' to restrict the read. Strongly recommended "
                         "for the multipolygons layer -- a statewide read needs more RAM than "
                         "this box has. Pad generously: the layer only has to contain everything "
                         "within the proximity radius of the AOI.")
    ap.add_argument("--overwrite", action="store_true",
                    help="rebuild layers that already exist (default: skip them)")
    args = ap.parse_args(argv)

    if not args.pbf.is_file():
        logger.error("extract not found: %s\nDownload from https://download.geofabrik.de/", args.pbf)
        return 1
    bbox = tuple(float(v) for v in args.bbox.split(",")) if args.bbox else None
    if bbox is not None and len(bbox) != 4:
        raise SystemExit(f"--bbox needs 4 comma-separated numbers, got {len(bbox)}")
    stats = extract(args.pbf, args.out_dir, bbox=bbox, overwrite=args.overwrite)
    print(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
