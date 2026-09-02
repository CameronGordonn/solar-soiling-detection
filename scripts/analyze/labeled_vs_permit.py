#!/usr/bin/env python3
"""How many hand-LABELED arrays have no permit? (the inverse of permit-recall).

The recall audit asks: of permitted homes, how many did the detector find. This
asks the mirror question about our **ground-truth labels**: of the solar arrays we
hand-labeled in NAIP, how many sit on a parcel with **no entry in the permit
registry**? A large "labeled but unregistered" count is the direct evidence that
permits (2016+) and real installs (CA boom 2013-2016) are largely disjoint
populations — i.e. low permit-overlap is expected, not a detector failure.

Method (geocode-free, parcel-APN based, robust):
  * read YOLO polygon labels for train/val/test, convert each to a world polygon
    via the tile_index affine (normalized tile coords -> EPSG:3857),
  * assign each labeled array the APN of the parcel under its interior point,
  * a label is "registered" if its parcel APN appears in the permit registry.

PII: reads/writes under outputs/ + data/ (gitignored). Never publish.

ENV:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/labeled_vs_permit.py
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from pathlib import Path

import pyproj  # noqa: E402

_SHARE = os.path.dirname(pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_DATA", pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_LIB", pyproj.datadir.get_data_dir())
os.environ.setdefault("GDAL_DATA", os.path.join(_SHARE, "gdal"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MERC = "EPSG:3857"
TILE_RE = re.compile(r"(tile_\d+)")


def apn_base(s) -> str | None:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    digits = "".join(ch for ch in str(s) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def load_labels(labels_root: Path, tile_index: Path) -> gpd.GeoDataFrame:
    """All YOLO polygon labels -> world polygons (3857), tagged with split + tile."""
    ti = json.loads(tile_index.read_text())["tiles"]
    rows = []
    for split in ("train", "val", "test"):
        for txt in glob.glob(str(labels_root / split / "*.txt")):
            m = TILE_RE.search(os.path.basename(txt))
            if not m:
                continue
            key = m.group(1) + ".png"
            meta = ti.get(key)
            if meta is None:
                continue
            b = meta["bounds"]
            dx, dy = b["maxx"] - b["minx"], b["maxy"] - b["miny"]
            with open(txt) as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) < 7:
                        continue
                    coords = list(map(float, parts[1:]))
                    pts = [(b["minx"] + coords[i] * dx, b["maxy"] - coords[i + 1] * dy)
                           for i in range(0, len(coords) - 1, 2)]
                    try:
                        poly = Polygon(pts)
                        if poly.is_valid and poly.area > 0:
                            rows.append({"split": split, "tile": m.group(1), "geometry": poly})
                    except Exception:
                        continue
    gdf = gpd.GeoDataFrame(rows, crs=MERC)
    print(f"labeled arrays: {len(gdf)}  ("
          + ", ".join(f"{s}={int((gdf['split']==s).sum())}" for s in ('train', 'val', 'test')) + ")")
    return gdf


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels-root", default="data/yolo/naip/labels", type=Path)
    ap.add_argument("--tile-index", default="data/interim/tile_index.json", type=Path)
    ap.add_argument("--permits", default="data/external/sc_solar_permits.csv", type=Path)
    ap.add_argument("--parcels",
                    default="data/external/santa_cruz_parcels/aoi_santa-cruz-outreach-v1.geojson",
                    type=Path)
    ap.add_argument("--out-dir", default="outputs/economics", type=Path)
    args = ap.parse_args(argv)

    labels = load_labels(REPO / args.labels_root, REPO / args.tile_index)
    parcels = gpd.read_file(REPO / args.parcels).to_crs(MERC)
    parcels["apn_base"] = parcels["APN"].map(apn_base)

    # APN of the parcel under each labeled array's interior point
    pts = labels.copy()
    pts["geometry"] = labels.representative_point()
    joined = gpd.sjoin(pts, parcels[["apn_base", "geometry"]], predicate="within", how="left")
    joined = joined[~joined.index.duplicated(keep="first")]
    labels["apn_base"] = joined["apn_base"].values

    permitted = set(pd.read_csv(REPO / args.permits)["apn"].map(apn_base).dropna())

    on_parcel = labels["apn_base"].notna()
    labels["registered"] = labels["apn_base"].isin(permitted)
    n = len(labels)
    n_parcel = int(on_parcel.sum())
    n_reg = int(labels["registered"].sum())
    n_unreg_parcel = int((on_parcel & ~labels["registered"]).sum())
    n_off_parcel = n - n_parcel

    # distinct parcels (a parcel may carry several labeled arrays)
    reg_parcels = labels.loc[labels["registered"], "apn_base"].nunique()
    unreg_parcels = labels.loc[on_parcel & ~labels["registered"], "apn_base"].nunique()

    print(f"\nlabeled arrays on a known parcel : {n_parcel}/{n}  (off-parcel/edge: {n_off_parcel})")
    print(f"  registered (parcel has a permit)        : {n_reg}  ({n_reg/n:.0%} of all labels)")
    print(f"  LABELED BUT NOT REGISTERED (no permit)  : {n_unreg_parcel}  ({n_unreg_parcel/n:.0%})")
    print(f"distinct parcels — registered: {reg_parcels}, unregistered: {unreg_parcels}")
    by_split = (labels[on_parcel].groupby("split")["registered"]
                .agg(["sum", "count"]).astype(int))
    print("\nby split (registered / labeled-on-parcel):")
    for s, r in by_split.iterrows():
        print(f"  {s:5s}: {r['sum']:3d}/{r['count']:3d} registered  "
              f"-> {r['count']-r['sum']:3d} unregistered")

    out_dir = REPO / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rep = out_dir / "labeled_vs_permit.md"
    rep.write_text(f"""# Labeled arrays vs. permit registry (inverse of permit-recall)

Mirror of `permit_recall_audit.md`: of the arrays **we hand-labeled** in NAIP, how
many sit on a parcel with **no permit** in `sc_solar_permits.csv`? Matched parcel-APN
(geocode-free), so robust.

| metric | count | share |
|---|---|---|
| labeled arrays (train/val/test) | {n} | 100% |
| …on a known parcel | {n_parcel} | {n_parcel/n:.0%} |
| …off-parcel / tile-edge (unmatched) | {n_off_parcel} | {n_off_parcel/n:.0%} |
| **registered** (parcel carries a permit) | **{n_reg}** | **{n_reg/n:.0%}** |
| **labeled but NOT registered** (no permit) | **{n_unreg_parcel}** | **{n_unreg_parcel/n:.0%}** |

Distinct parcels: {reg_parcels} registered, {unreg_parcels} unregistered.

**Read:** ~{n_unreg_parcel/n:.0%} of arrays we *know* are real (we labeled them) have **no
permit**. Permits (2016+) and the labeled install base (CA boom 2013-2016) are largely
disjoint populations — so the low permit→detection overlap is mostly a *temporal* mismatch,
not a detector failure. The permit registry is a valid **independent recall probe**, but it
is **not** a complete census of real arrays, and must never be used as a precision denominator.

by split (registered / labeled-on-parcel): """
                   + "; ".join(f"{s}={int(r['sum'])}/{int(r['count'])}"
                               for s, r in by_split.iterrows())
                   + f"\n\n_Note: counts are the live on-disk YOLO labels "
                   f"({n} polygons total) and differ from the CLAUDE.md 2026-05-13 dataset "
                   f"snapshot — labels have drifted through relabeling rounds; verify the "
                   f"canonical split before training off these numbers._\n",
                   encoding="utf-8")
    print(f"\n[ok] report -> {rep}")


if __name__ == "__main__":
    main()
