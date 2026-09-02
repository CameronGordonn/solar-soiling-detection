#!/usr/bin/env python3
"""Render a NAIP chip-review sheet for the permit true-miss queue (Thread 2 §3 prep).

The recall audit (`permit_recall_audit.py`) leaves a queue of permitted parcels the
detector did not find. Before deciding whether the fix is *relabeling* (panel is
visible at 60 cm but unlabeled — free) or *higher-res imagery* (panel is genuinely
unresolvable at NAIP 60 cm → county orthophotos), a human needs to look at each chip.

This emits:
  * a contact-sheet **PDF** — one NAIP chip per genuine miss, the permit point marked,
    any detected arrays in view drawn in red, titled with apn/year/kw/address;
  * a companion **CSV** in the same order with blank `tag` / `notes` columns to fill
    (suggested tags: label_miss | resolution_miss | not_solar | offset_geocode | unsure).

The chip is a small mosaic of the source NAIP tiles around the geocoded permit point
(the geocode scatters ~10-25 m off the roof, so chips are ~90 m wide for context).

PII: reads/writes under outputs/ + data/ (gitignored). Never publish.

ENV:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/render_miss_review_sheet.py
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pyproj  # noqa: E402

_SHARE = os.path.dirname(pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_DATA", pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_LIB", pyproj.datadir.get_data_dir())
os.environ.setdefault("GDAL_DATA", os.path.join(_SHARE, "gdal"))

import geopandas as gpd  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from rasterio.merge import merge as rio_merge  # noqa: E402
from shapely.geometry import box  # noqa: E402

import sys  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from permit_parcels import load_parcel_index, parcel_geom  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MERC = "EPSG:3857"  # source NAIP tiles are stored in Web Mercator


def split_map(labels_root: Path) -> dict:
    """tile_id -> split, from which labels/<split>/ holds that tile's label file."""
    import glob
    import re
    rx = re.compile(r"(tile_\d+)")
    out = {}
    for sp in ("train", "val", "test"):
        for txt in glob.glob(str(labels_root / sp / "*.txt")):
            m = rx.search(os.path.basename(txt))
            if m:
                out[m.group(1)] = sp
    return out


def load_tiles(tile_index: Path, naip_dir: Path) -> gpd.GeoDataFrame:
    """One row per source NAIP tile: its 3857 rectangle, tile name + path to the GeoTIFF."""
    ti = json.loads(tile_index.read_text())["tiles"]
    rows = []
    for name, meta in ti.items():
        b = meta["bounds"]
        rows.append({"name": name.replace(".png", ""),
                     "path": str(naip_dir / meta["source"]),
                     "geometry": box(b["minx"], b["miny"], b["maxx"], b["maxy"])})
    return gpd.GeoDataFrame(rows, crs=MERC)


def chip_mosaic(tiles: gpd.GeoDataFrame, cx: float, cy: float, half: float):
    """Mosaic the NAIP tiles overlapping a box centered on (cx, cy); return (rgb, extent)."""
    bb = box(cx - half, cy - half, cx + half, cy + half)
    hits = tiles[tiles.intersects(bb)]
    if hits.empty:
        return None, None
    srcs = [rasterio.open(p) for p in hits["path"]]
    try:
        mosaic, transform = rio_merge(srcs, bounds=(cx - half, cy - half, cx + half, cy + half),
                                      res=srcs[0].res)
    finally:
        for s in srcs:
            s.close()
    rgb = mosaic[:3].transpose(1, 2, 0)  # (H, W, 3)
    extent = [cx - half, cx + half, cy - half, cy + half]
    return rgb, extent


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", default="outputs/economics/permit_true_miss_queue.csv", type=Path)
    ap.add_argument("--tile-index", default="data/interim/tile_index.json", type=Path)
    ap.add_argument("--naip-dir", default="data/raw/naip", type=Path)
    ap.add_argument("--arrays",
                    default="outputs/aoi/santa-cruz-outreach-v1/arrays.geojson", type=Path)
    ap.add_argument("--parcels",
                    default="data/external/santa_cruz_parcels/aoi_santa-cruz-outreach-v1.geojson",
                    type=Path, help="parcel polygons — chips center on the parcel, not the geocode")
    ap.add_argument("--labels-root", default="data/yolo/naip/labels", type=Path,
                    help="to tag each chip's tile with its train/val/test split")
    ap.add_argument("--chip-m", type=float, default=90.0, help="chip width in ground meters (approx)")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--include-apn-detected", action="store_true",
                    help="also render the 16 point-miss-but-APN-detected rows")
    ap.add_argument("--order", choices=("tile", "queue"), default="tile",
                    help="chip render order: 'tile' groups by tile number (no-tile rows last); "
                         "'queue' keeps the original queue order")
    ap.add_argument("--out-dir", default="outputs/economics", type=Path)
    args = ap.parse_args(argv)

    q = pd.read_csv(REPO / args.queue)
    if "apn_detected" in q.columns and not args.include_apn_detected:
        q = q[~q["apn_detected"]].reset_index(drop=True)
    q = q.reset_index(drop=True)
    print(f"rendering {len(q)} genuine-miss parcels")

    tiles = load_tiles(REPO / args.tile_index, REPO / args.naip_dir)
    arrays = gpd.read_file(REPO / args.arrays).to_crs(MERC)

    # geocode -> 3857; 3857 meters are ~1.25x ground here, so widen the box.
    pts = gpd.GeoDataFrame(q.copy(),
                           geometry=gpd.points_from_xy(q["lon"], q["lat"]), crs="EPSG:4326").to_crs(MERC)
    # Position on the PARCEL (authoritative for the APN), not the Census geocode —
    # the geocode interpolates along the street and lands 50-150 m off the roof.
    par_index = load_parcel_index(REPO / args.parcels)
    pts["parcel"] = [parcel_geom(par_index, a) for a in pts["apn"]]
    pts["has_parcel"] = pts["parcel"].notna()
    pts["center"] = [p.centroid if p is not None else g
                     for p, g in zip(pts["parcel"], pts.geometry)]
    print(f"  positioned on parcel: {int(pts['has_parcel'].sum())}/{len(pts)} "
          f"(rest fall back to geocode)")

    # tag each chip with its source tile + split (so the reviewer sees at a glance
    # whether a fix lands in a safe-to-edit train tile or a careful val/test one).
    centers_gdf = gpd.GeoDataFrame(geometry=list(pts["center"]), crs=MERC)
    tj = gpd.sjoin(centers_gdf, tiles[["name", "geometry"]], predicate="within", how="left")
    tj = tj[~tj.index.duplicated(keep="first")].reindex(centers_gdf.index)
    pts["tile"] = tj["name"].values
    sm = split_map(REPO / args.labels_root)
    pts["split"] = [sm.get(t, "unlabeled") if isinstance(t, str) else None for t in pts["tile"]]

    # Order chips by tile number so a reviewer works one source tile at a time.
    # Rows with no tile (geocode landed outside every NAIP tile) sort last.
    if args.order == "tile":
        import re as _re

        def _tilenum(t):
            m = _re.search(r"(\d+)", t) if isinstance(t, str) else None
            return int(m.group(1)) if m else 10 ** 9

        order = sorted(range(len(pts)),
                       key=lambda i: (_tilenum(pts["tile"].iloc[i]),
                                      pts["year"].iloc[i], str(pts["apn"].iloc[i])))
        pts = pts.iloc[order].reset_index(drop=True)
        q = q.iloc[order].reset_index(drop=True)
        print(f"  ordered {len(pts)} chips by tile number")

    half = args.chip_m * 1.25 / 2.0  # ground-m -> 3857-m half-width

    per_page = args.cols * args.rows
    out_dir = REPO / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "permit_miss_review_sheet.pdf"

    n_in_chip = []
    with PdfPages(pdf_path) as pdf:
        for page_start in range(0, len(pts), per_page):
            page = pts.iloc[page_start:page_start + per_page]
            fig, axes = plt.subplots(args.rows, args.cols,
                                     figsize=(args.cols * 3.2, args.rows * 3.4))
            axes = axes.ravel()
            for ax in axes:
                ax.axis("off")
            for j, (_, row) in enumerate(page.iterrows()):
                ax = axes[j]
                center = row["center"]
                cx, cy = center.x, center.y
                rgb, extent = chip_mosaic(tiles, cx, cy, half)
                idx = page_start + j
                if rgb is None:
                    ax.text(0.5, 0.5, "no imagery", ha="center", va="center")
                    n_in_chip.append(0)
                    continue
                ax.imshow(rgb, extent=extent, origin="upper")
                view = box(extent[0], extent[2], extent[1], extent[3])
                # tile seams (black dashed) — boundaries of the source NAIP tiles, so
                # you can see exactly which tile(s) the 90 m window is assembled from.
                hits = tiles[tiles.intersects(view)]
                for tg in hits.geometry:
                    txs, tys = tg.exterior.xy
                    ax.plot(txs, tys, color="black", lw=1.4, ls="--", alpha=0.9)
                # the permitted PARCEL outline (cyan) — this is the property to inspect
                if row["has_parcel"]:
                    pg = row["parcel"]
                    for poly in (pg.geoms if pg.geom_type == "MultiPolygon" else [pg]):
                        pxs, pys = poly.exterior.xy
                        ax.plot(pxs, pys, color="cyan", lw=1.6)
                # detected arrays in view (red outline) — context for the reviewer
                vis = arrays[arrays.intersects(view)]
                for geom in vis.geometry:
                    xs, ys = geom.exterior.xy
                    ax.plot(xs, ys, color="red", lw=1.2)
                n_in_chip.append(len(vis))
                # parcel center marker (yellow); geocode marker only when no parcel
                ax.plot(cx, cy, marker="+", color="yellow", ms=14, mew=2.0)
                ax.set_xlim(extent[0], extent[1]); ax.set_ylim(extent[2], extent[3])
                ax.axis("off")
                # source tile under the chip center (for the title)
                center_tile = hits[hits.contains(center)]["name"]
                tname = center_tile.iloc[0].replace("tile_0", "t") if len(center_tile) else "?"
                kw = f"{row['kw']:.1f}kW" if pd.notna(row["kw"]) else "kW?"
                addr = str(row["matched_address"]).split(",")[0][:22]
                flag = "" if row["has_parcel"] else " [GEO!]"  # no parcel -> geocode fallback
                ax.set_title(f"#{idx}  {int(row['year'])} {kw}{flag}  {tname}\n{addr}",
                             fontsize=7.5)
            fig.suptitle(f"Permit true-miss NAIP review (60cm) — chips ~{args.chip_m:.0f} m wide  "
                         f"| cyan = permitted parcel, red = detected array, + = center "
                         f"([GEO!] = no parcel, geocode fallback)  | page {page_start//per_page+1}",
                         fontsize=9)
            fig.tight_layout(rect=[0, 0, 1, 0.97])
            pdf.savefig(fig, dpi=150)
            plt.close(fig)
            print(f"  page {page_start//per_page+1} ({len(page)} chips)")

    # companion CSV in render order, with blank tag columns for the human
    out = q.copy()
    out["chip_index"] = range(len(out))
    out["detected_arrays_in_chip"] = n_in_chip
    out["tile"] = pts["tile"].values
    out["split"] = pts["split"].values
    out["has_parcel"] = pts["has_parcel"].values
    out["tag"] = ""            # label_miss | resolution_miss | not_solar | offset_geocode | unsure
    out["notes"] = ""
    # Carry over any hand-entered tags/notes from a prior review CSV, keyed on
    # (apn, year) so re-rendering (e.g. a re-sort) never discards review work.
    csv_path = out_dir / "permit_miss_review.csv"
    if csv_path.exists():
        prev = pd.read_csv(csv_path)
        if {"apn", "year", "tag"}.issubset(prev.columns):
            prev_map = prev.set_index(["apn", "year"])[["tag", "notes"]]
            keys = list(zip(out["apn"], out["year"]))
            for col in ("tag", "notes"):
                out[col] = [str(prev_map[col].get((a, y), "")) if (a, y) in prev_map.index else ""
                            for a, y in keys]
            out["tag"] = out["tag"].replace("nan", "")
            out["notes"] = out["notes"].replace("nan", "")
            print(f"  carried over {int((out['tag'].str.strip() != '').sum())} existing tags")
    cols = ["chip_index", "tile", "split", "has_parcel", "apn", "year", "kw",
            "detected_arrays_in_chip", "apn_detected", "match_dist_m", "lat", "lon",
            "matched_address", "tag", "notes"]
    cols = [c for c in cols if c in out.columns]
    out[cols].to_csv(csv_path, index=False)

    n_eval = int(((pts["split"] == "val") | (pts["split"] == "test")).sum())
    print(f"\n[ok] review sheet ({len(pts)} chips) -> {pdf_path}")
    print(f"[ok] companion CSV -> {csv_path}  ({n_eval} chips in val/test tiles — edit carefully)")
    print("    tag each chip: label_miss | resolution_miss | not_solar | offset_geocode | unsure")


if __name__ == "__main__":
    main()
