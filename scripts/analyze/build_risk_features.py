"""Build per-array soiling feature matrix from detected arrays + weather history.

Joins onto outputs/array_features.geo.parquet (from script 07) on `array_id`.
Writes outputs/soiling/inference_matrix.parquet.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.risk.feature_engineering import build_feature_row
from src.risk.location_features import (
    load_static_lookup, location_feature_vector, sample_worldcover_batch,
    worldcover_one_hot_from_code,
)
from src.risk.weather_client import fetch_combined
from src.risk import osm_local
from solarsoiled.manifest import write_manifest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _window_start(as_of: date, lookback_days: int) -> date:
    """Inclusive lookback: 180 days ending today includes today as day 180."""
    return as_of - timedelta(days=max(lookback_days - 1, 0))


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/soiling/california.yaml")
    p.add_argument("--features-config", default="configs/soiling/features.yaml")
    p.add_argument("--arrays", default="outputs/array_features.geo.parquet")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD; default = today UTC")
    p.add_argument("--out", default="outputs/soiling/inference_matrix.parquet")
    p.add_argument("--weather-grid-deg", type=float, default=0.05,
                   help="Snap weather lookups to this grid and fetch once per cell (default "
                        "0.05 deg ~5.5 km). Open-Meteo's archive is ERA5 at 0.25 deg, so this "
                        "is 5x finer than the source and changes no feature value -- it just "
                        "stops us spending one API call per array to resample one grid cell. "
                        "Set 0 to disable and fetch per exact centroid.")
    p.add_argument("--cache-only", action="store_true",
                   help="Never touch the weather network: serve every cell from the on-disk "
                        "cache and skip arrays that are not cached. Lets a test or an offline "
                        "re-run reproduce a result from the SAME weather bytes the original run "
                        "used, instead of re-fetching and burning free-tier quota.")
    p.add_argument("--no-worldcover", action="store_true",
                   help="skip the ESA WorldCover batch sample (offline runs). The five "
                        "worldcover_* one-hots will then be median-filled at inference, "
                        "which discards ~22%% of the loss model's feature importance.")
    p.add_argument("--no-local-osm", action="store_true",
                   help="Force the per-array Overpass path instead of the local OSM layers. "
                        "Only for validating the two against each other -- it is thousands of "
                        "rate-limited queries whose failures become silently missing features.")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]

    region_cfg = yaml.safe_load((repo_root / args.config).read_text())
    feat_cfg = yaml.safe_load((repo_root / args.features_config).read_text())

    arrays_path = repo_root / args.arrays
    if not arrays_path.exists():
        raise FileNotFoundError(
            f"{arrays_path} missing — run scripts/analyze/extract_array_features.py first."
        )
    gdf = gpd.read_parquet(arrays_path)
    if gdf.empty:
        raise ValueError("array_features.geo.parquet has no rows")

    # Centroids in lat/lon for weather lookup. Compute them in a projected (UTM)
    # CRS first — centroids taken directly in a geographic CRS are inaccurate —
    # then reproject the centroid points back to WGS84.
    centroids_ll = gdf.to_crs(gdf.estimate_utm_crs()).geometry.centroid.to_crs("EPSG:4326")

    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.utcnow().date()
    lookback = int(region_cfg.get("weather_history_days", 180))
    start = _window_start(as_of, lookback)
    cache_dir = repo_root / region_cfg.get("cache_dir", ".cache/soiling")
    nlcd_path = region_cfg.get("nlcd_path")
    nlcd_path = Path(nlcd_path) if nlcd_path else None

    windows = tuple(feat_cfg["rolling_windows_days"])
    kimber_cfg = feat_cfg.get("kimber")
    geom_cols = [c for c in feat_cfg["geometric_features"] if c in gdf.columns]
    static_path = region_cfg.get("static_features_csv")
    static_lookup = load_static_lookup(repo_root / static_path) if static_path else None

    # Weather lookups are snapped to a coarse grid and fetched ONCE per cell.
    #
    # fetch_combined was being called with each array's exact centroid, so 3,362 arrays meant
    # 3,362 Open-Meteo archive requests -- roughly six days against the free tier's ~600/day.
    # Those requests return near-identical data: Open-Meteo's archive is ERA5 at 0.25 deg
    # (~25 km) and CAMS air quality is coarser still, while this whole AOI is ~0.12 deg across.
    # We were paying 3,362 requests to sample one grid cell thousands of times.
    #
    # A 0.05 deg snap (~5.5 km) is still 5x finer than the underlying product, so it cannot
    # move a feature value, and it collapses this AOI to a couple of fetches. Only the WEATHER
    # lookup is snapped -- location features (elevation, land cover, road/farm proximity) are
    # genuinely per-array and keep the true centroid.
    grid = float(args.weather_grid_deg)

    def _cell(lat: float, lon: float) -> tuple[float, float]:
        return (round(lat / grid) * grid, round(lon / grid) * grid) if grid > 0 else (lat, lon)

    cells = {_cell(float(centroids_ll.iloc[i].y), float(centroids_ll.iloc[i].x))
             for i in range(len(gdf))}
    logger.info("weather: %d arrays -> %d unique %.3f deg cells (%.0fx fewer API calls)",
                len(gdf), len(cells), grid, len(gdf) / max(1, len(cells)))
    weather_cache: dict[tuple[float, float], object] = {}

    # OSM proximity, computed locally for every array at once. The per-array Overpass path it
    # replaces was thousands of rate-limited queries whose 429/504s land in the feature row as
    # a silently missing value -- a quietly degraded matrix rather than a crash.
    osm_dists = None
    if not args.no_local_osm and osm_local.layers_available(repo_root / "data/external/osm"):
        osm_dists = osm_local.distances_for_points(
            centroids_ll, layer_dir=repo_root / "data/external/osm")
        logger.info("OSM proximity computed locally for %d arrays (0 API calls)", len(gdf))
    else:
        logger.warning("local OSM layers unavailable -- falling back to per-array Overpass "
                       "queries. Expect rate limiting and silently missing proximity features; "
                       "build the layers with scripts/analyze/build_osm_layers.py")

    # ESA WorldCover, sampled ONCE for the whole AOI (one STAC resolve + one vectorized
    # raster sample) rather than per array. Without this the five worldcover_* one-hots
    # are absent from the AOI matrix and get median-filled at inference, silently
    # discarding 21.8% of the loss model's feature importance -- including
    # worldcover_tree, the only tree-overhang signal the model has. See
    # docs/ECONOMICS_GROUNDING_20260809.md section 4.
    wc_codes = [None] * len(gdf)
    if not args.no_worldcover:
        wc_codes = sample_worldcover_batch(centroids_ll)
    else:
        logger.warning("--no-worldcover: worldcover_* columns will be omitted and "
                       "median-filled at inference")

    rows = []
    # Why count failures instead of only logging them: this loop swallows every
    # per-array exception and continues, which is right for a few bad rows and
    # catastrophic for a systemic outage. When Open-Meteo's quota is exhausted
    # EVERY array fails, 0 rows get written, and the run dies much later inside
    # sklearn's isotonic stage as `ValueError: Found array with 0 sample(s)` --
    # after detection has already done all its work. Measured 2026-09-01 on a
    # 109-array AOI. The cause was 109 identical quota warnings scrolled far up
    # the log. Fail here, where the reason is still in hand.
    failures: Counter[str] = Counter()
    for i, (_, arr) in enumerate(gdf.iterrows()):
        lon = float(centroids_ll.iloc[i].x)
        lat = float(centroids_ll.iloc[i].y)
        try:
            key = _cell(lat, lon)
            if key not in weather_cache:
                weather_cache[key] = fetch_combined(key[0], key[1], start, as_of,
                                                    cache_dir=cache_dir,
                                                    cache_only=args.cache_only)
            daily = weather_cache[key]
            loc = location_feature_vector(
                lat, lon, nlcd_path=nlcd_path,
                cache_dir=cache_dir, static_lookup=static_lookup,
                osm_distances=(None if osm_dists is None else
                               {k: float(v[i]) for k, v in osm_dists.items()}),
            )
        except Exception as exc:
            failures[type(exc).__name__] += 1
            logger.warning("array_id=%s weather/loc fetch failed: %s", arr["array_id"], exc)
            continue

        row = {"array_id": int(arr["array_id"]), "latitude": lat, "longitude": lon}
        row.update({c: arr[c] for c in geom_cols})
        row.update(loc)
        # Only overwrite when the batch sampler actually produced a class; the
        # static_lookup path may already have supplied these for a known station.
        if wc_codes[i] is not None:
            row.update(worldcover_one_hot_from_code(wc_codes[i]))
            row["worldcover_class"] = int(wc_codes[i])
        row.update(build_feature_row(daily, as_of, windows=windows, kimber_cfg=kimber_cfg))
        rows.append(row)

        if (i + 1) % 50 == 0:
            logger.info("Built features for %d / %d arrays", i + 1, len(gdf))

    n_in = len(gdf)
    if n_in and not rows:
        top = ", ".join(f"{name} x{n}" for name, n in failures.most_common(3)) or "unknown"
        hint = ""
        if "OpenMeteoQuotaError" in failures:
            hint = (
                "\nOpen-Meteo's free tier enforces sliding minute/hour/day windows. "
                "Either wait for the window to reset and re-run, or set OPEN_METEO_API_KEY "
                "to a paid subscription key (see src/risk/weather_client.py) -- note that "
                "buying a plan WITHOUT setting that variable changes nothing, because the "
                "free hosts keep their limits."
            )
        raise RuntimeError(
            f"Feature building produced 0 rows from {n_in} arrays; every array failed. "
            f"Causes: {top}.{hint}\n"
            "Refusing to write an empty inference matrix -- scoring it would fail far "
            "downstream with an opaque sklearn error instead of this one."
        )
    if n_in and len(rows) < n_in:
        logger.warning(
            "%d of %d arrays produced no features (%s). The inference matrix is INCOMPLETE.",
            n_in - len(rows), n_in,
            ", ".join(f"{k} x{v}" for k, v in failures.most_common(3)) or "unknown",
        )

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = repo_root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out_path, index=False)
    logger.info("Wrote %d rows → %s", len(rows), out_path)

    write_manifest(
        out_path.parent,
        stage="stage2_score",
        model_version="features-v1",
        model_weights=None,
        inputs=[str(arrays_path)],
        metrics={"n_arrays_in": int(len(gdf)), "n_rows_out": int(len(rows))},
        known_limitations=[
            "Open-Meteo + AQ daily aggregation; missing days dropped",
            "Static features (NLCD/elevation/OSM) skipped if static_features_csv absent",
            "WorldCover sampled at 10 m; a rooftop in a treed neighbourhood may read "
            "built_up, so worldcover_tree UNDER-detects per-roof canopy overhang",
        ],
        extra={
            "as_of": as_of.isoformat(),
            "lookback_days": int(lookback),
            "rolling_windows_days": list(windows),
            "out_matrix": str(out_path),
        },
    )


if __name__ == "__main__":
    main()
