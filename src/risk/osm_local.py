"""Local, vectorized OSM proximity — the offline replacement for per-array Overpass calls.

`location_features.distance_to_highway_m` issues one Overpass query per point. That is
tolerable for a handful of NREL stations and untenable for a detected AOI: 3,362 arrays meant
thousands of rate-limited queries re-fetching the same roads, with a real 429/504 rate whose
failure mode is a silently missing feature rather than an error.

Here the layers are read once from a Geofabrik extract (see build_osm_layers.py) and every
array's distance is computed in a single `sjoin_nearest`. Same definition of "nearest major
road", different transport: exact, offline, and O(seconds) for the whole AOI.

Semantics are matched to the Overpass functions deliberately, so swapping transports cannot
quietly change a feature value:
  - distance in METRES to the nearest feature geometry
  - capped at ``radius_m`` when nothing is closer, exactly as the API version returns
    ``radius_m`` on an empty bbox
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import numpy as np

logger = logging.getLogger(__name__)

WORK_CRS = "EPSG:3857"
DEFAULT_LAYER_DIR = Path("data/external/osm")


class LayersUnavailable(RuntimeError):
    """Raised when the local extract is missing, so callers can fall back explicitly."""


@lru_cache(maxsize=4)
def _load_layer(path_str: str) -> gpd.GeoDataFrame:
    path = Path(path_str)
    if not path.is_file():
        raise LayersUnavailable(
            f"{path} not found — build it with scripts/analyze/build_osm_layers.py")
    gdf = gpd.read_parquet(path)
    logger.info("loaded %d features from %s", len(gdf), path.name)
    return gdf


def nearest_distance_m(points: gpd.GeoSeries, layer_path: Path, radius_m: float) -> np.ndarray:
    """Metres from each point to the nearest feature in `layer_path`, capped at `radius_m`.

    Distances are computed in a local UTM projection rather than in Web Mercator: 3857
    distances are inflated by 1/cos(lat) (~25% here), which would silently stretch every
    proximity feature. This is the same trap as the GSD units bug, one layer down.
    """
    layer = _load_layer(str(layer_path))
    if layer.empty:
        return np.full(len(points), float(radius_m))

    pts = gpd.GeoDataFrame(geometry=points.to_crs(WORK_CRS), crs=WORK_CRS)
    metric_crs = pts.estimate_utm_crs()
    pts_m = pts.to_crs(metric_crs)

    # Clip the layer to the AOI + radius before the join. Statewide layers are large and only
    # the local neighbourhood can ever win a nearest-neighbour query.
    minx, miny, maxx, maxy = pts_m.total_bounds
    pad = float(radius_m)
    layer_m = layer.to_crs(metric_crs).cx[minx - pad:maxx + pad, miny - pad:maxy + pad]
    if layer_m.empty:
        return np.full(len(points), float(radius_m))

    joined = gpd.sjoin_nearest(
        pts_m.reset_index(drop=True), layer_m[["geometry"]].reset_index(drop=True),
        how="left", max_distance=float(radius_m), distance_col="_dist_m",
    )
    # sjoin_nearest can emit several rows for one point on exact ties; keep the first per point.
    d = joined.groupby(joined.index)["_dist_m"].min().reindex(range(len(pts_m)))
    return d.fillna(float(radius_m)).to_numpy(dtype=float)


def distances_for_points(points: gpd.GeoSeries, layer_dir: Path = DEFAULT_LAYER_DIR,
                         highway_radius_m: float = 5000.0,
                         agriculture_radius_m: float = 5000.0) -> dict[str, np.ndarray]:
    """Both proximity features for a whole point set. -> {feature_name: array}."""
    return {
        "distance_to_highway_m": nearest_distance_m(
            points, Path(layer_dir) / "highways.parquet", highway_radius_m),
        "distance_to_agriculture_m": nearest_distance_m(
            points, Path(layer_dir) / "agriculture.parquet", agriculture_radius_m),
    }


def layers_available(layer_dir: Path = DEFAULT_LAYER_DIR) -> bool:
    d = Path(layer_dir)
    return (d / "highways.parquet").is_file() and (d / "agriculture.parquet").is_file()
