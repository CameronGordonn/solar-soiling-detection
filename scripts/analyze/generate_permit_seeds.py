#!/usr/bin/env python3
"""Thread 2 §3 — turn confirmed permit misses into weak YOLO label seeds.

Once the chip-review sheet is tagged (`render_miss_review_sheet.py` →
`permit_miss_review.csv`), the rows a human marked **`label_miss`** are parcels where
the permit asserts a panel that is visible at 60 cm but was never labeled. This emits a
pre-seeded label candidate per such parcel: a weak box (sized from the permitted kW)
centered on the geocoded permit point, in the containing tile's normalized coords —
so relabeling starts from a hint instead of a blank tile.

SAFETY / PROVENANCE:
  * Writes ONLY to the staging dir (gitignored) — NEVER to `data/yolo/naip/` (data-pipeline
    rule). The seeds are *candidates*; a human confirms/adjusts each in Roboflow before it
    enters the real label set.
  * Every seed is tagged `source=permit_seed` in the manifest so it can never silently
    inflate eval metrics.
  * Seeds landing in val/test tiles are flagged (editing eval tiles re-baselines the metric);
    use --skip-eval-tiles to exclude them.

The geocode scatters ~10-25 m off the roof, so the seed box is a *starting hint*, not a
ground-truth polygon — the human nudges it onto the panel.

ENV:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/generate_permit_seeds.py --all
    # real use, after tagging:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/generate_permit_seeds.py
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from pathlib import Path

import pyproj  # noqa: E402

_SHARE = os.path.dirname(pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_DATA", pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_LIB", pyproj.datadir.get_data_dir())
os.environ.setdefault("GDAL_DATA", os.path.join(_SHARE, "gdal"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.geometry import Point, box  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
try:
    from risk.economics import M2_PER_KW
except ImportError:  # pragma: no cover
    from src.risk.economics import M2_PER_KW

MERC = "EPSG:3857"
MERC_PER_GROUND_M = 1.25  # 3857 distance inflation at ~37N (1/cos lat)
TILE_RE = re.compile(r"(tile_\d+)")


def tile_split_map(labels_root: Path) -> dict[str, str]:
    """tile_id -> split, from which labels/<split>/ holds that tile's label file."""
    out = {}
    for split in ("train", "val", "test"):
        for txt in glob.glob(str(labels_root / split / "*.txt")):
            m = TILE_RE.search(os.path.basename(txt))
            if m:
                out[m.group(1)] = split
    return out


def load_tiles(tile_index: Path) -> gpd.GeoDataFrame:
    ti = json.loads(tile_index.read_text())["tiles"]
    rows = [{"tile": name.replace(".png", ""), "bounds": meta["bounds"],
             "geometry": box(meta["bounds"]["minx"], meta["bounds"]["miny"],
                             meta["bounds"]["maxx"], meta["bounds"]["maxy"])}
            for name, meta in ti.items()]
    return gpd.GeoDataFrame(rows, crs=MERC)


def seed_polygon(b: dict, x: float, y: float, side_merc: float) -> list[float]:
    """Axis-aligned box of `side_merc` (3857 units) centered on (x,y), as normalized
    tile coords clamped to [0,1]; returned as a flat [x1,y1,...,x4,y4] polygon."""
    dx, dy = b["maxx"] - b["minx"], b["maxy"] - b["miny"]
    hx, hy = side_merc / 2.0, side_merc / 2.0
    corners = [(x - hx, y + hy), (x + hx, y + hy), (x + hx, y - hy), (x - hx, y - hy)]
    flat = []
    for cx, cy in corners:
        nx = min(1.0, max(0.0, (cx - b["minx"]) / dx))
        ny = min(1.0, max(0.0, (b["maxy"] - cy) / dy))  # image y grows downward
        flat += [round(nx, 6), round(ny, 6)]
    return flat


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review-csv", default="outputs/economics/permit_miss_review.csv", type=Path)
    ap.add_argument("--tags", default="label_miss",
                    help="comma list of tags to seed (default: label_miss)")
    ap.add_argument("--all", action="store_true",
                    help="ignore the tag column and seed every row (testing / full sweep)")
    ap.add_argument("--tile-index", default="data/interim/tile_index.json", type=Path)
    ap.add_argument("--labels-root", default="data/yolo/naip/labels", type=Path)
    ap.add_argument("--default-area-m2", type=float, default=28.0,
                    help="seed-box area when kW is missing (~5 kW * 5.67 m2/kW)")
    ap.add_argument("--min-side-m", type=float, default=5.0)
    ap.add_argument("--skip-eval-tiles", action="store_true",
                    help="exclude seeds that land in val/test tiles")
    ap.add_argument("--render", action="store_true",
                    help="also write a per-seed overlay PNG (chip + seed box) for review")
    ap.add_argument("--out-dir", default="outputs/economics/permit_seeds", type=Path)
    args = ap.parse_args(argv)

    df = pd.read_csv(REPO / args.review_csv)
    tags = {t.strip() for t in args.tags.split(",") if t.strip()}
    if args.all:
        sel = df.copy()
        print(f"--all: seeding every row ({len(sel)})")
    elif "tag" not in df.columns or df["tag"].fillna("").eq("").all():
        print(f"[!] no tags found in {args.review_csv}. Tag the review sheet first, or pass "
              f"--all to seed every genuine miss. Nothing written.")
        return
    else:
        sel = df[df["tag"].fillna("").str.strip().isin(tags)].copy()
        print(f"selected {len(sel)} rows tagged {sorted(tags)} (of {len(df)})")
    if sel.empty:
        print("nothing to seed."); return

    tiles = load_tiles(REPO / args.tile_index)
    splits = tile_split_map(REPO / args.labels_root)

    pts = gpd.GeoDataFrame(sel, geometry=gpd.points_from_xy(sel["lon"], sel["lat"]),
                           crs="EPSG:4326").to_crs(MERC)

    records = []
    for _, r in pts.iterrows():
        hit = tiles[tiles.contains(r.geometry)]
        if hit.empty:  # point outside every imaged tile
            records.append({**r, "tile": None}); continue
        t = hit.iloc[0]
        kw = r["kw"] if "kw" in r and pd.notna(r["kw"]) else None
        area = (float(kw) * M2_PER_KW) if kw else args.default_area_m2
        side_m = max(math.sqrt(area), args.min_side_m)
        poly = seed_polygon(t["bounds"], r.geometry.x, r.geometry.y, side_m * MERC_PER_GROUND_M)
        records.append({"tile": t["tile"], "split": splits.get(t["tile"], "unlabeled"),
                        "apn": r.get("apn"), "year": r.get("year"), "kw": kw,
                        "lat": r["lat"], "lon": r["lon"], "side_m": round(side_m, 1),
                        "poly": poly})

    out_dir = REPO / args.out_dir
    (out_dir / "labels").mkdir(parents=True, exist_ok=True)
    valid = [rec for rec in records if rec.get("tile")]
    dropped = len(records) - len(valid)
    eval_seeds = [rec for rec in valid if rec["split"] in ("val", "test")]
    if args.skip_eval_tiles:
        valid = [rec for rec in valid if rec["split"] not in ("val", "test")]

    # one YOLO label file per tile (accumulate seeds), class 0 polygon segmentation
    by_tile: dict[str, list] = {}
    for rec in valid:
        by_tile.setdefault(rec["tile"], []).append(rec["poly"])
    for tile, polys in by_tile.items():
        lines = ["0 " + " ".join(map(str, p)) for p in polys]
        (out_dir / "labels" / f"{tile}.txt").write_text("\n".join(lines) + "\n")

    # provenance manifest — source=permit_seed, never merge blindly
    man = pd.DataFrame([{**{k: rec.get(k) for k in
                           ("tile", "split", "apn", "year", "kw", "side_m", "lat", "lon")},
                         "source": "permit_seed",
                         "in_eval_split": rec["split"] in ("val", "test")} for rec in valid])
    man_path = out_dir / "manifest.csv"
    man.to_csv(man_path, index=False)

    if args.render:
        _render_overlays(valid, tiles, out_dir)

    print(f"\n[ok] {len(valid)} seeds across {len(by_tile)} tiles -> {out_dir}/labels/")
    print(f"[ok] provenance manifest -> {man_path}")
    if dropped:
        print(f"[!] {dropped} rows skipped (geocode outside every imaged tile)")
    if eval_seeds and not args.skip_eval_tiles:
        ev = ", ".join(sorted({rec['tile'] for rec in eval_seeds}))
        print(f"[!] {len(eval_seeds)} seeds land in VAL/TEST tiles ({ev}) — editing these "
              f"re-baselines the eval metric. Re-run with --skip-eval-tiles to exclude.")
    print("    NEXT: import these as suggestions in Roboflow, confirm/adjust each, and only "
          "then export into data/yolo/naip. Never copy seeds in blind (source=permit_seed).")


def _render_overlays(valid, tiles, out_dir):
    """Per-seed overlay PNG (chip + green seed box + permit point) for quick confirmation."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    sys.path.insert(0, str(REPO / "scripts" / "analyze"))
    from render_miss_review_sheet import load_tiles as _lt, chip_mosaic
    rtiles = _lt(REPO / "data/interim/tile_index.json", REPO / "data/raw/naip")
    ov = out_dir / "overlays"; ov.mkdir(exist_ok=True)
    tmap = {t["tile"]: t["bounds"] for _, t in tiles.iterrows()}
    half = 90 * MERC_PER_GROUND_M / 2
    for i, rec in enumerate(valid):
        b = tmap[rec["tile"]]
        # seed polygon back to 3857 for plotting
        dx, dy = b["maxx"] - b["minx"], b["maxy"] - b["miny"]
        xs = [b["minx"] + rec["poly"][k] * dx for k in range(0, 8, 2)] + [b["minx"] + rec["poly"][0] * dx]
        ys = [b["maxy"] - rec["poly"][k] * dy for k in range(1, 8, 2)] + [b["maxy"] - rec["poly"][1] * dy]
        cx = sum(xs[:-1]) / 4; cy = sum(ys[:-1]) / 4
        rgb, ext = chip_mosaic(rtiles, cx, cy, half)
        if rgb is None:
            continue
        fig, ax = plt.subplots(figsize=(4, 4))
        ax.imshow(rgb, extent=ext, origin="upper")
        ax.plot(xs, ys, color="lime", lw=1.8)
        ax.plot(cx, cy, "+", color="yellow", ms=14, mew=2)
        ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3]); ax.axis("off")
        ax.set_title(f"{rec['tile']} [{rec['split']}] {rec['kw'] or '?'}kW", fontsize=8)
        fig.tight_layout(); fig.savefig(ov / f"seed_{i:03d}_{rec['tile']}.png", dpi=120)
        plt.close(fig)
    print(f"[ok] overlays -> {ov}/")


if __name__ == "__main__":
    main()
