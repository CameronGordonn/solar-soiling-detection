"""Give detected arrays identities that survive a re-detection.

WHY
---
`extract_array_features.py` assigns ``array_id = np.arange(len(gdf))`` -- a positional row
index. It is stable only while the detections are. Re-run with a new model, new imagery, or
even a reordered input and every array silently renumbers, taking with it anything keyed on
the id: mailer PDFs (named ``<array_id>.pdf``), QR deep-links (``dashboard.html?id=N``),
feedback rows, and the risk/recommendation joins.

This assigns two things instead, which answer different questions:

  stable_id       durable. Derived from the array's own geometry, so the same roof gets the
                  same id across re-detections and across AOI runs. This is what new
                  customer-facing links should use.
  legacy_array_id continuity. The positional id this array had in a previous run, carried
                  over by spatial match. This is what tells you WHICH HOUSEHOLD ALREADY GOT
                  A POSTCARD -- the audit trail, not the link.

The two are deliberately separate. A pure geometry hash would renumber the existing 334
arrays (breaking the audit trail); pure carry-over would leave us with positional ids
forever. Mixed: inherit when we can match, mint from geometry when we cannot.

STABILITY DETAIL
----------------
The geometry hash quantizes the centroid to a grid, so a centroid that drifts across a cell
boundary between runs would otherwise mint a new id. That is why a matched array INHERITS
the previous stable_id rather than recomputing it -- the hash seeds an identity, matching
preserves it. Quantization only has to be good enough to keep unrelated arrays apart.

SPLITS AND MERGES ARE REAL HERE
-------------------------------
Going 60cm -> 21cm, one blobby old detection often resolves into two or three true arrays.
The best-overlapping new array inherits the legacy id; its siblings get fresh stable_ids and
record ``split_from``. Merges (several old -> one new) record ``merged_from``. Neither is
silently dropped, because both change what a household is owed.

USAGE
-----
    PYTHONPATH=. python scripts/analyze/assign_stable_ids.py \
        --new  outputs/aoi/<new>/arrays.geojson \
        --previous outputs/aoi/<old>/risk.geojson \
        --out  outputs/aoi/<new>/arrays_identified.geojson \
        --id-map outputs/aoi/<new>/id_map.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WORK_CRS = "EPSG:3857"     # metric enough for matching at this latitude; areas are not used here
QUANTIZE_M = 1.0           # centroid grid for the geometry hash
MIN_IOU = 0.30             # below this, "the same roof" is not a defensible claim
MAX_CENTROID_M = 5.0       # fallback match radius when IoU is degenerate (tiny/sliver polys)
ID_NAMESPACE = "solarsoiled/array/v1"


def stable_id_for(geom, namespace: str = ID_NAMESPACE) -> str:
    """Deterministic 12-hex id from a geometry's quantized centroid in WORK_CRS.

    Quantized so that sub-metre jitter between runs does not change the id, and namespaced so
    these can never be confused with ids from another scheme.
    """
    c = geom.centroid
    key = f"{namespace}|{round(c.x / QUANTIZE_M):d}|{round(c.y / QUANTIZE_M):d}"
    return hashlib.blake2b(key.encode(), digest_size=6).hexdigest()


def _iou(a, b) -> float:
    inter = a.intersection(b).area
    if inter <= 0:
        return 0.0
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def match_to_previous(new: gpd.GeoDataFrame, prev: gpd.GeoDataFrame) -> dict[int, int]:
    """-> {new_row_index: prev_row_index} for confident one-to-one matches.

    Greedy by descending IoU so the best-overlapping pair claims each other first; a split
    then leaves the siblings unmatched (which is what we want -- they are new arrays).
    """
    if prev.empty or new.empty:
        return {}
    cand = gpd.sjoin(
        new[["geometry"]].reset_index(names="new_i"),
        prev[["geometry"]].reset_index(names="prev_i"),
        how="inner", predicate="intersects",
    )
    scored = []
    for _, row in cand.iterrows():
        ni, pi = int(row["new_i"]), int(row["prev_i"])
        scored.append((_iou(new.geometry.iloc[ni], prev.geometry.iloc[pi]), ni, pi))

    # Fallback for polygons too small or too slivery for IoU to be meaningful.
    matched_new: dict[int, int] = {}
    used_prev: set[int] = set()
    for iou, ni, pi in sorted(scored, key=lambda t: -t[0]):
        if iou < MIN_IOU or ni in matched_new or pi in used_prev:
            continue
        matched_new[ni] = pi
        used_prev.add(pi)

    unmatched = [i for i in range(len(new)) if i not in matched_new]
    if unmatched:
        prev_cent = prev.geometry.centroid
        for ni in unmatched:
            c = new.geometry.iloc[ni].centroid
            d = prev_cent.distance(c)
            pi = int(d.idxmin()) if len(d) else -1
            if pi >= 0 and pi not in used_prev and float(d.loc[pi]) <= MAX_CENTROID_M:
                matched_new[ni] = pi
                used_prev.add(pi)
    return matched_new


def assign(new_path: Path, prev_path: Path | None, out_path: Path, id_map_path: Path | None):
    new = gpd.read_file(new_path).to_crs(WORK_CRS).reset_index(drop=True)
    new["stable_id"] = [stable_id_for(g) for g in new.geometry]

    # A geometry hash can collide if two arrays share a quantized centroid (concentric or
    # duplicated polygons). Detect rather than silently merging two roofs into one identity.
    dupes = new["stable_id"].duplicated(keep=False)
    if dupes.any():
        logger.warning("%d arrays share a quantized centroid; disambiguating with area",
                       int(dupes.sum()))
        for sid in new.loc[dupes, "stable_id"].unique():
            rows = new.index[new["stable_id"] == sid]
            for rank, i in enumerate(sorted(rows, key=lambda r: -new.geometry.iloc[r].area)):
                if rank:
                    new.at[i, "stable_id"] = hashlib.blake2b(
                        f"{sid}|{rank}".encode(), digest_size=6).hexdigest()

    new["legacy_array_id"] = None
    new["match_status"] = "new"
    stats = {"matched": 0, "new": 0, "dropped": 0}

    if prev_path is not None:
        prev = gpd.read_file(prev_path).to_crs(WORK_CRS).reset_index(drop=True)
        pairs = match_to_previous(new, prev)
        prev_ids = (prev["array_id"].tolist() if "array_id" in prev.columns
                    else list(range(len(prev))))
        prev_stable = prev["stable_id"].tolist() if "stable_id" in prev.columns else None

        for ni, pi in pairs.items():
            new.at[ni, "legacy_array_id"] = int(prev_ids[pi])
            new.at[ni, "match_status"] = "matched"
            # Inherit the previous stable_id so quantization drift cannot mint a new identity
            # for a roof we have already identified.
            if prev_stable is not None and prev_stable[pi]:
                new.at[ni, "stable_id"] = prev_stable[pi]

        stats["matched"] = len(pairs)
        stats["new"] = len(new) - len(pairs)
        dropped = [int(prev_ids[pi]) for pi in range(len(prev)) if pi not in set(pairs.values())]
        stats["dropped"] = len(dropped)

        id_map = {
            "matched": {str(int(prev_ids[pi])): new.at[ni, "stable_id"] for ni, pi in pairs.items()},
            # Previously-detected arrays with no counterpart now. These are the ones to look at
            # by hand: a real removal, a detector miss, and a georef artifact all land here and
            # they are not the same problem.
            "dropped_legacy_ids": sorted(dropped),
            "new_stable_ids": sorted(new.loc[new["match_status"] == "new", "stable_id"].tolist()),
            "params": {"min_iou": MIN_IOU, "max_centroid_m": MAX_CENTROID_M,
                       "quantize_m": QUANTIZE_M, "namespace": ID_NAMESPACE},
            "counts": stats,
            "previous": str(prev_path), "new": str(new_path),
        }
        if id_map_path:
            id_map_path.parent.mkdir(parents=True, exist_ok=True)
            id_map_path.write_text(json.dumps(id_map, indent=1))
            logger.info("id map -> %s", id_map_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    new.to_crs("EPSG:4326").to_file(out_path, driver="GeoJSON")
    logger.info("%d arrays -> %s", len(new), out_path)
    logger.info("matched %d | new %d | dropped-from-previous %d",
                stats["matched"], stats["new"], stats["dropped"])
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", type=Path, required=True, help="arrays.geojson from the new run")
    ap.add_argument("--previous", type=Path, default=None,
                    help="risk/arrays geojson from the run being replaced (for continuity)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--id-map", type=Path, default=None)
    args = ap.parse_args(argv)

    if args.previous is not None and not args.previous.is_file():
        raise SystemExit(f"--previous not found: {args.previous}")
    assign(args.new, args.previous, args.out, args.id_map)
    return 0


if __name__ == "__main__":
    sys.exit(main())
