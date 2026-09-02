"""Check the local OSM proximity against the Overpass answers we already paid for.

Swapping thousands of per-array Overpass calls for one local `sjoin_nearest` is a change of
TRANSPORT that is only safe if it is not also a change of ANSWER. The two can silently
disagree in several ways: a different highway tag set, ways-vs-nodes geometry sampling,
distance measured in Web Mercator instead of a metric CRS (a 25% inflation here), or the
Geofabrik extract being a different vintage from the live Overpass database.

We do not need new API calls to find out. Today's runs left ~1,900 cached Overpass responses
in .cache/soiling/overpass.sqlite, which is free ground truth: replay the cached path with
`cache_only` semantics for a sample of arrays and compare against the local computation.

    PYTHONPATH=. python scripts/analyze/validate_osm_layers.py \
        --arrays outputs/aoi/santa-cruz-w2-21cm/features/array_features.geo.parquet --n 40

Reports the distribution of |local - overpass|. Small scatter is expected (extract vintage,
geometry densification); a systematic offset or a Mercator-shaped ~1.25x ratio is a bug.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.risk import location_features as lf  # noqa: E402
from src.risk import osm_local  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arrays", type=Path, required=True)
    ap.add_argument("--n", type=int, default=40, help="arrays to sample")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--layer-dir", type=Path, default=REPO_ROOT / "data/external/osm")
    args = ap.parse_args(argv)

    gdf = gpd.read_parquet(args.arrays)
    cent = gdf.to_crs(gdf.estimate_utm_crs()).geometry.centroid.to_crs("EPSG:4326")

    rng = np.random.default_rng(args.seed)
    idx = rng.choice(len(cent), size=min(args.n, len(cent)), replace=False)
    sample = cent.iloc[idx].reset_index(drop=True)

    local = osm_local.distances_for_points(sample, layer_dir=args.layer_dir)

    rows = []
    for i in range(len(sample)):
        lat, lon = sample.iloc[i].y, sample.iloc[i].x
        # Overpass path. Cached responses answer instantly; anything uncached costs a live
        # query, which is why this is a sample and not the full set.
        api_hw = lf.distance_to_highway_m(lat, lon, cache_dir=REPO_ROOT / ".cache/soiling")
        api_ag = lf.distance_to_agriculture_m(lat, lon, cache_dir=REPO_ROOT / ".cache/soiling")
        rows.append((api_hw, local["distance_to_highway_m"][i],
                     api_ag, local["distance_to_agriculture_m"][i]))

    def report(name, api_vals, loc_vals):
        # Filter ONCE into paired lists. Rebinding `api` before deriving `loc` (as an earlier
        # version did) zips a filtered sequence against an unfiltered one and silently
        # mispairs every comparison.
        pairs = [(a, b) for a, b in zip(api_vals, loc_vals) if a is not None]
        api = np.array([p[0] for p in pairs], dtype=float)
        loc = np.array([p[1] for p in pairs], dtype=float)
        if not len(api):
            print(f"{name}: no comparable pairs (all Overpass calls failed)")
            return
        d = np.abs(loc - api)
        ratio = np.median(loc / np.maximum(api, 1e-6))
        print(f"{name}: n={len(api)}  median|diff|={np.median(d):8.1f} m  "
              f"p90={np.percentile(d, 90):8.1f} m  max={d.max():8.1f} m  "
              f"median ratio={ratio:.3f}")
        if 1.20 < ratio < 1.30:
            print("   ^ ratio near 1.25 — suspect a Web Mercator distance (1/cos(lat)), not metric")

    hw_api = [r[0] for r in rows]; hw_loc = [r[1] for r in rows]
    ag_api = [r[2] for r in rows]; ag_loc = [r[3] for r in rows]
    report("highway    ", hw_api, hw_loc)
    report("agriculture", ag_api, ag_loc)
    n_failed = sum(1 for a in hw_api if a is None)
    if n_failed:
        print(f"\nNOTE: {n_failed}/{len(hw_api)} Overpass calls returned None (rate limit / "
              f"error). In the old pipeline each of those became a silently missing feature.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
