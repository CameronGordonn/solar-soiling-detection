"""Re-tile fetched 21cm imagery into 512px chips and render existing polygons onto the new grid.

W2 steps 4-5 of docs/PERMISSIVE_STACK_MIGRATION.md. Consumes the per-tile 21cm EPSG:3857 GeoTIFFs
from fetch_scc_imagery.py plus the existing 60cm labels + tile_index.json, and produces:
  - 512px chips at ~0.256 m/px (footprint ~131m vs 307m at 60cm), CLAHE tone-mapped like the 60cm set
  - a tile_index_21cm.json (CRS/affine per chip) -- the grid contract for the 21cm set
  - YOLO polygon .txt labels, re-rendered GEOSPATIALLY (label -> 60cm px -> 3857 world -> 21cm chip px)

Labels are 512px to match the size the 60cm labels were drawn at (NOT the 640 default in
tile_naip_image.py -- the on-disk tiles are 512, see tile_index.json).

VINTAGE (read docs / the W1 discussion): these labels were drawn on NAIP-2022; the imagery is 2025.
Existing labels stay valid (panels persist), but panels installed 2022-25 are unlabeled -> a false
"background" signal. This script does NOT fix that. Run the model-assisted QA pass (--emit-qa-manifest
here -> detector inference in Colab -> confident detections without a rendered label = candidate new
panels for human review) BEFORE training on these chips.

Usage:
  # self-test the geometry on synthetic data (no imagery needed):
  PYTHONPATH=. conda run -n solar-soiling python scripts/data/build_multiscale_tiles.py --self-test

  # real re-tile of fetched 21cm rasters:
  PYTHONPATH=. conda run -n solar-soiling python scripts/data/build_multiscale_tiles.py \
      --imagery-dir data/interim/scc_2025 --split val --render-overlays
"""

from pathlib import Path
import argparse
import json
import logging
import sys

import numpy as np
import rasterio
from rasterio.transform import from_bounds, array_bounds, rowcol
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from utils.naip_preprocessing import tone_map_naip_clahe  # noqa: E402
from utils.tile_metadata import affine_from_entry  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CHIP = 512          # match the size the 60cm labels were drawn at
GRID_CRS = "EPSG:3857"


def tile_stem(name):
    """tile_000039_png.rf.<hash> or tile_000039.png -> tile_000039"""
    import re
    m = re.match(r"(tile_\d+)", Path(name).name)
    return m.group(1) if m else Path(name).stem


def load_index(path):
    idx = json.loads(Path(path).read_text())
    return idx["tiles"] if isinstance(idx, dict) and "tiles" in idx else idx


def label_path_for(labels_dir, tile):
    hits = list(Path(labels_dir).glob(f"{tile}*.txt"))
    return hits[0] if hits else None


def read_polys_norm(label_file):
    """YOLO polygon .txt -> list of (N,2) arrays in [0,1] (of the source tile)."""
    polys = []
    if label_file is None or not Path(label_file).exists():
        return polys
    for line in Path(label_file).read_text().strip().splitlines():
        parts = line.split()
        if len(parts) < 7:
            continue
        c = np.array([float(p) for p in parts[1:]], dtype=np.float64)
        xs, ys = c[0::2], c[1::2]
        if len(xs) >= 3:
            polys.append(np.column_stack([xs, ys]))
    return polys


def polys_to_world(polys_norm, src_transform, src_w, src_h):
    """Source-normalized polygon coords -> world (3857) coords via the source tile's affine."""
    out = []
    for p in polys_norm:
        px = p[:, 0] * src_w
        py = p[:, 1] * src_h
        wx, wy = rasterio.transform.xy(src_transform, py, px)  # (col=px, row=py)
        out.append(np.column_stack([np.asarray(wx), np.asarray(wy)]))
    return out


def clip_poly_to_chip(world_poly, chip_transform, chip_w, chip_h):
    """World polygon -> chip pixel coords, normalized; return None if fully outside the chip."""
    rows, cols = rowcol(chip_transform, world_poly[:, 0], world_poly[:, 1])
    cols = np.asarray(cols, float); rows = np.asarray(rows, float)
    if cols.max() < 0 or rows.max() < 0 or cols.min() > chip_w or rows.min() > chip_h:
        return None  # no overlap with this chip
    nx = np.clip(cols / chip_w, 0, 1)
    ny = np.clip(rows / chip_h, 0, 1)
    poly = np.column_stack([nx, ny])
    # drop if clipped area is degenerate
    if _poly_area(poly) < 1e-6:
        return None
    return poly


def _poly_area(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def chip_grid(raster_w, raster_h, chip=CHIP):
    """Top-left offsets for a chip grid that covers the raster (last row/col snap to the edge)."""
    xs = list(range(0, max(1, raster_w - chip + 1), chip))
    ys = list(range(0, max(1, raster_h - chip + 1), chip))
    if not xs or xs[-1] != raster_w - chip:
        xs.append(max(0, raster_w - chip))
    if not ys or ys[-1] != raster_h - chip:
        ys.append(max(0, raster_h - chip))
    return sorted(set(xs)), sorted(set(ys))


def process_raster(tif_path, src_meta, labels_dir, out_dirs, chip_index, render_overlays):
    tile = tile_stem(tif_path.name)
    with rasterio.open(tif_path) as ds:
        data = ds.read()  # (3, H, W)
        rt = ds.transform
        rw, rh = ds.width, ds.height

    # The NAIP index is GDAL-order but the 21cm one is Affine-order, and picking wrong relocates
    # the tile without erroring -- resolve it rather than assume.
    src_tf = affine_from_entry(src_meta) if isinstance(src_meta["transform"], list) \
        else src_meta["transform"]
    polys_norm = read_polys_norm(label_path_for(labels_dir, tile))
    world_polys = polys_to_world(polys_norm, src_tf, src_meta["width"], src_meta["height"])

    xs, ys = chip_grid(rw, rh)
    n_chips = n_labeled = 0
    for yi in ys:
        for xi in xs:
            sub = data[:, yi:yi + CHIP, xi:xi + CHIP]
            if sub.shape[1] != CHIP or sub.shape[2] != CHIP:
                continue
            chip_tf = rt * rasterio.Affine.translation(xi, yi)
            chip_bounds = array_bounds(CHIP, CHIP, chip_tf)
            chip_name = f"{tile}_r{yi:04d}_c{xi:04d}.png"

            # tone-map + save image
            rgb = np.transpose(sub, (1, 2, 0))
            toned = tone_map_naip_clahe(rgb, clip_limit=0.03)
            Image.fromarray(toned).save(out_dirs["images"] / chip_name)

            # render labels that fall in this chip
            lines = []
            for wp in world_polys:
                cp = clip_poly_to_chip(wp, chip_tf, CHIP, CHIP)
                if cp is None:
                    continue
                pts = " ".join(f"{v:.6f}" for xy in cp for v in xy)
                lines.append(f"0 {pts}")
            (out_dirs["labels"] / chip_name.replace(".png", ".txt")).write_text("\n".join(lines))
            if lines:
                n_labeled += 1
            if render_overlays and lines:
                _overlay(toned, [clip_poly_to_chip(wp, chip_tf, CHIP, CHIP) for wp in world_polys],
                         out_dirs["overlays"] / chip_name)

            chip_index[chip_name] = {
                "transform": list(chip_tf.to_gdal()),  # GDAL order, matching tile_index convention
                "crs": GRID_CRS, "width": CHIP, "height": CHIP,
                "source_tile": tile, "source_raster": tif_path.name,
                "bounds": {"minx": chip_bounds[0], "miny": chip_bounds[1],
                           "maxx": chip_bounds[2], "maxy": chip_bounds[3]},
            }
            n_chips += 1
    logger.info(f"{tile}: {n_chips} chips ({n_labeled} labeled) from {rw}x{rh}")
    return n_chips, n_labeled


def _overlay(img, polys, out_path):
    import cv2
    canvas = img.copy()
    for p in polys:
        if p is None:
            continue
        pk = (p * [img.shape[1], img.shape[0]]).astype(np.int32)
        cv2.polylines(canvas, [pk], True, (0, 255, 0), 2)
    Image.fromarray(canvas).save(out_path)


def self_test():
    """Verify label geometry survives 60cm-tile -> world -> 21cm-chip with no drift."""
    # A synthetic 60cm tile with a known square polygon; re-render onto a co-located 21cm chip grid.
    src_tf = from_bounds(-13588070.4, 4432588.8, -13587763.2, 4432896.0, 512, 512)
    poly_norm = [np.array([[0.30, 0.30], [0.50, 0.30], [0.50, 0.50], [0.30, 0.50]])]
    world = polys_to_world(poly_norm, src_tf, 512, 512)[0]
    # 21cm chip covering the SAME ground as the top-left quadrant of the 60cm tile
    chip_tf = from_bounds(-13588070.4, 4432742.4, -13587916.8, 4432896.0, CHIP, CHIP)
    cp = clip_poly_to_chip(world, chip_tf, CHIP, CHIP)
    # The polygon (x:0.30-0.50, y:0.30-0.50 of full tile) vs the top-left quadrant chip (x:0-0.5, y:0-0.5)
    # maps to chip-normalized x:0.60-1.00, y:0.60-1.00.
    exp = np.array([[0.60, 0.60], [1.00, 0.60], [1.00, 1.00], [0.60, 1.00]])
    err = np.abs(np.sort(cp, 0) - np.sort(exp, 0)).max()
    print(f"self-test: chip poly={np.round(cp,3).tolist()}  max_err={err:.4f}")
    assert err < 0.01, f"geometry drift {err}"
    print("SELF-TEST PASS — label geometry survives the 60cm->world->21cm remap.")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--imagery-dir", default="data/interim/scc_2025")
    p.add_argument("--tile-index", default="data/interim/tile_index.json")
    p.add_argument("--labels-root", default="data/yolo/naip/labels")
    p.add_argument("--split", default="val", choices=["train", "val", "test"])
    p.add_argument("--out-root", default="data/yolo/naip_21cm")
    p.add_argument("--render-overlays", action="store_true", help="save GT overlay PNGs for eyeballing")
    p.add_argument("--self-test", action="store_true", help="run geometry self-test and exit")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.self_test:
        self_test()
        return

    src_index = load_index(args.tile_index)
    labels_dir = Path(args.labels_root) / args.split
    out_root = Path(args.out_root)
    out_dirs = {
        "images": out_root / "images" / args.split,
        "labels": out_root / "labels" / args.split,
        "overlays": out_root / "overlays" / args.split,
    }
    for d in out_dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    tifs = sorted(Path(args.imagery_dir).glob("*_21cm_3857.tif"))
    chip_index, tot_chips, tot_labeled = {}, 0, 0
    for tif in tifs:
        tile = tile_stem(tif.name)
        key = f"{tile}.png"
        if key not in src_index:
            logger.warning(f"{tile}: no entry in tile_index — skipping")
            continue
        # only process tiles whose labels live in this split
        if label_path_for(labels_dir, tile) is None:
            continue
        c, l = process_raster(tif, src_index[key], labels_dir, out_dirs, chip_index, args.render_overlays)
        tot_chips += c; tot_labeled += l

    idx_out = out_root / f"tile_index_21cm_{args.split}.json"
    idx_out.write_text(json.dumps({"tiles": chip_index,
                                   "metadata": {"gsd_m": 0.256, "chip": CHIP, "crs": GRID_CRS,
                                                "source": "scc_imagery_2025", "split": args.split}}, indent=1))
    logger.info(f"done: {tot_chips} chips ({tot_labeled} labeled) -> {out_root}  index -> {idx_out.name}")
    logger.info("NEXT: model-assisted vintage QA before training — detector inference on these chips, "
                "confident detections with no rendered label = candidate 2022->2025 new panels to review.")


if __name__ == "__main__":
    main()
