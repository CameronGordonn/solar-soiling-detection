"""Backfill the five ``worldcover_*`` one-hots into an existing AOI inference matrix.

Why this exists
---------------
``build_risk_features.py`` samples ESA WorldCover by default (``sample_worldcover_batch``),
but AOI matrices built before that landed have no ``worldcover_*`` columns at all. At
inference ``loss_model.align_features`` then fills them with **training medians**, and the
training set is full of Central Valley agricultural stations. So a coastal city roof is
told it sits near cropland.

That is worse than it sounds. It is 21.8% of the loss model's importance, including its
single highest-importance feature (``worldcover_cropland``) and ``worldcover_tree``, the
only tree-overhang signal the model has, and the fill is biased in the direction that
inflates predicted soiling.

This backfills the columns in place rather than re-running the whole feature build,
because the alternative refetches weather for every array and would hit the Open-Meteo
quota wall. It also isolates the change: only the worldcover columns move, so any shift in
the predictions is attributable to them alone.

The permanent fix is to rebuild the matrix with ``build_risk_features.py``; this is for
matrices that already exist.

Usage
-----
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/backfill_worldcover.py \
        --matrix outputs/aoi/santa-cruz-w2-21cm/features/inference_matrix.parquet
"""

from __future__ import annotations

import argparse
import collections
import shutil
from pathlib import Path

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", required=True, type=Path)
    ap.add_argument("--lat-col", default="latitude")
    ap.add_argument("--lon-col", default="longitude")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    import geopandas as gpd

    from src.risk.location_features import (
        sample_worldcover_batch,
        worldcover_one_hot_from_code,
    )

    df = pd.read_parquet(args.matrix)
    present = [c for c in df.columns if c.startswith("worldcover_")]
    if present:
        print(f"[skip] matrix already has {len(present)} worldcover columns: {present}")
        return

    pts = gpd.GeoSeries(
        gpd.points_from_xy(df[args.lon_col], df[args.lat_col]), crs="EPSG:4326"
    )
    print(f"[sample] {len(pts)} points -> ESA WorldCover 2021 v200")
    codes = sample_worldcover_batch(pts)
    ok = [c for c in codes if c is not None]
    print(f"[sample] {len(ok)}/{len(codes)} resolved")
    if not ok:
        raise SystemExit("no points resolved; refusing to write an all-null backfill")
    print(f"[sample] class codes: {collections.Counter(ok).most_common()}")

    one_hots = pd.DataFrame(
        [worldcover_one_hot_from_code(c) for c in codes], index=df.index
    )
    print("[sample] column means:")
    print(one_hots.mean().round(4).to_string())

    if not args.no_backup:
        bak = args.matrix.with_suffix(args.matrix.suffix + ".pre_worldcover.bak")
        shutil.copy2(args.matrix, bak)
        print(f"[backup] {bak}")

    out = pd.concat([df, one_hots], axis=1)
    out.to_parquet(args.matrix, index=False)
    print(f"[ok] {args.matrix}  {df.shape} -> {out.shape}")


if __name__ == "__main__":
    main()
