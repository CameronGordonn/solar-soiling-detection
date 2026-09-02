"""Rebuild the 21cm YOLO dataset from a Roboflow export — annotations only, our own pixels.

Roboflow re-encodes a generated version to JPEG and applies whatever Resize the version was built
with. The 60cm set already lost to this: tile_index.json says 512x512 sources, but every image in
data/yolo/naip/images/ is 640x640 JPEG. So we take ONLY the label .txt files from the export and
pair them with the lossless PNGs written by build_21cm_labelset.py.

Then chip 1200px tiles -> 640px at NATIVE resolution (crop, never resize) so training at imgsz=640
sees true ~0.21 m/px pixels, and carry the geospatial metadata through to every chip.

  <out>/images/{train,val,test}/<tile>_r<row>c<col>.png
  <out>/labels/{train,val,test}/<tile>_r<row>c<col>.txt
  <out>/data.yaml
  <out>/annotations/instances_{train,val,test}.json   COCO segmentation, same polygons
  <out>/tile_index_chips.json     CRS + affine + bounds per chip
  <out>/import_qa.json            coverage, polygon counts, area distribution vs the 60cm set

Both label formats come out of one pass because the backbone is still undecided: YOLO-seg txt feeds
ultralytics today, COCO feeds RF-DETR for the W1 rematch in docs/PERMISSIVE_STACK_MIGRATION.md. The
polygons are identical — only the container differs, which is exactly the file-boundary contract
that migration doc's guardrail 3 asks us to preserve.

Usage:
  PYTHONPATH=. conda run -n solar-soiling python scripts/data/import_21cm_from_roboflow.py \
      --export-dir ~/Downloads/solar_arrays_scc21_tiles.v1i.yolov11
"""

from pathlib import Path
import argparse
import csv
import json
import logging

import numpy as np
import rasterio
from PIL import Image
from shapely.geometry import Polygon, box

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CHIP = 640
MIN_KEEP_FRAC = 0.30    # a boundary polygon must retain this much area to survive the crop
MIN_KEEP_M2 = 2.0       # ...and be at least this big on the ground
NAIP_MEDIAN_M2 = 13.5   # 60cm-set median; a large drop means someone drifted to per-panel labels


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--export-dir", required=True, type=Path, help="unzipped Roboflow YOLOv11-seg export")
    p.add_argument("--labelset", default="data/interim/scc21_labelset", type=Path)
    p.add_argument("--out-dir", default="data/yolo/scc21", type=Path)
    p.add_argument("--chip", type=int, default=CHIP)
    p.add_argument("--skip-untouched-seeds", action="store_true",
                   help="build from the reviewed tiles and drop seeded train tiles that came back "
                        "identical to their 2022 seed. Without this the import refuses outright: "
                        "Roboflow counts an uploaded prediction as an annotation, so those tiles "
                        "read as 'done' in the job without a human ever opening them, and importing "
                        "them would ship 2022 polygons as 2025 ground truth.")
    return p.parse_args(argv)


def seed_fingerprint(txt: Path):
    """Canonical form of a label file, for 'did a human touch this?'.

    Roboflow round-trips coordinates through its own float formatting, so compare at ~0.1 px on a
    1200 px tile (4 decimal places normalized) rather than requiring exact string equality.
    """
    if not txt.exists():
        return None
    polys = []
    for line in txt.read_text().splitlines():
        parts = line.split()
        if len(parts) < 7:
            continue
        polys.append(tuple(round(float(v), 4) for v in parts[1:]))
    return tuple(sorted(polys))


def index_export_labels(export_dir: Path):
    """Roboflow mangles names to <tile>_png.rf.<hash>.txt — recover the tile id."""
    found, dupes = {}, []
    for txt in export_dir.rglob("*.txt"):
        if txt.name in ("classes.txt", "README.txt"):
            continue
        tile = txt.name.split("_png.rf.")[0].split(".rf.")[0]
        if not tile.startswith("tile_"):
            continue
        if tile in found:
            dupes.append(tile)
        found[tile] = txt
    if dupes:
        logger.warning(f"{len(dupes)} tile(s) appeared twice in the export; kept the last: {dupes[:5]}")
    return found


def read_polygons(txt: Path, W: int, H: int):
    """Normalized YOLO-seg -> pixel-space shapely polygons."""
    polys = []
    if not txt.exists():
        return polys
    for line in txt.read_text().splitlines():
        parts = line.split()
        if len(parts) < 7:
            continue
        c = np.asarray(parts[1:], dtype=float)
        if c.size % 2:
            continue
        pts = list(zip(c[0::2] * W, c[1::2] * H))
        g = Polygon(pts)
        if not g.is_valid:
            g = g.buffer(0)          # self-intersections happen in hand-drawn polygons
        if g.is_empty or g.area <= 0:
            continue
        polys.append(g)
    return polys


def chip_origins(size: int, chip: int):
    """Evenly spaced crop origins covering `size`, overlapping rather than padding."""
    if size <= chip:
        return [0]
    n = int(np.ceil((size - chip) / chip)) + 1
    return [int(round(i * (size - chip) / (n - 1))) for i in range(n)]


def emit_chip_polygons(polys, x0, y0, chip, px_area_m2):
    """Clip polygons to one crop; return normalized YOLO-seg lines."""
    rect = box(x0, y0, x0 + chip, y0 + chip)
    lines = []
    for g in polys:
        inter = g.intersection(rect)
        if inter.is_empty:
            continue
        parts = list(inter.geoms) if inter.geom_type == "MultiPolygon" else [inter]
        for part in parts:
            if part.geom_type != "Polygon" or part.area <= 0:
                continue
            # Drop slivers: a polygon clipped to a corner is a worse label than no label. The
            # overlap between crops means the full object survives in the neighbouring chip.
            if part.area / g.area < MIN_KEEP_FRAC or part.area * px_area_m2 < MIN_KEEP_M2:
                continue
            xs, ys = part.exterior.coords.xy
            coords = []
            for x, y in zip(xs, ys):
                coords += [np.clip((x - x0) / chip, 0.0, 1.0), np.clip((y - y0) / chip, 0.0, 1.0)]
            lines.append("0 " + " ".join(f"{v:.6f}" for v in coords))
    return lines


def main(argv=None):
    args = parse_args(argv)
    chip = args.chip

    rows = list(csv.DictReader((args.labelset / "assignment.csv").open()))
    splits = {r["tile"]: r["split"] for r in rows}
    tile_index = json.loads((args.labelset / "tile_index_21cm.json").read_text())["tiles"]
    exported = index_export_labels(args.export_dir)
    logger.info(f"export has labels for {len(exported)} tiles; assignment covers {len(rows)}")

    missing = [t for t in splits if t not in exported]
    if missing:
        logger.warning(f"{len(missing)} assigned tile(s) have NO export label — unlabeled in "
                       f"Roboflow, or the batch was never finished: {missing[:8]}")

    # A seeded train tile that comes back identical to its seed was never reviewed. Roboflow marks
    # it "annotated" on upload (the seed is a prediction), so neither the job counter nor the
    # missing-label check above can see it — the tile looks finished from every angle except this
    # one. Importing it would put 2022 polygons into a 2025 dataset as ground truth.
    untouched = []
    for tile, split in sorted(splits.items()):
        if split != "train" or tile not in exported:
            continue
        fp = seed_fingerprint(args.labelset / "seeds" / f"{tile}.txt")
        if fp is not None and fp == seed_fingerprint(exported[tile]):
            untouched.append(tile)
    if untouched:
        logger.error(f"{len(untouched)} seeded train tile(s) are byte-identical to their 2022 seed "
                     f"— never opened by an annotator: {untouched[:8]}")
        if not args.skip_untouched_seeds:
            raise SystemExit(
                f"refusing to import {len(untouched)} unreviewed seed tile(s). Finish those batches, "
                f"or pass --skip-untouched-seeds to build from the reviewed tiles without them.")
        logger.warning(f"--skip-untouched-seeds: excluding {len(untouched)} tile(s) from the build")

    chips_index, areas_m2, per_split = {}, [], {}
    coco = {s: {"images": [], "annotations": []} for s in ("train", "val", "test")}
    n_poly = 0
    untouched_set = set(untouched)
    for tile, split in sorted(splits.items()):
        png = args.labelset / "images" / f"{tile}.png"
        if not png.exists() or tile not in exported:
            continue
        if tile in untouched_set:
            continue    # unreviewed 2022 seed — see the --skip-untouched-seeds guard above
        with rasterio.open(args.labelset.parent / "scc_2025" / f"{tile}_21cm_3857.tif") as src:
            transform, crs = src.transform, src.crs
        im = Image.open(png).convert("RGB")
        W, H = im.size
        arr = np.asarray(im)
        # 3857 pixel -> ground m2 (divide out the Mercator inflation once, per axis)
        gsd_m = tile_index[f"{tile}.png"]["gsd_ground_m"]
        px_area_m2 = gsd_m * gsd_m

        polys = read_polygons(exported[tile], W, H)
        n_poly += len(polys)

        # Tile-level GT, kept alongside the chips. The chip labels cannot substitute: an array
        # sitting in the ~80px chip overlap appears in two chips, and one straddling a seam is
        # clipped in both. Scoring the production path (whole tile -> chip grid -> NMS merge)
        # needs GT that was never cut up. See scripts/detect/eval_tile_f1.py.
        tl_dir = args.out_dir / "tile_labels" / split
        tl_dir.mkdir(parents=True, exist_ok=True)
        tl_lines = []
        for g in polys:
            # buffer(0) repair of a self-intersecting polygon can split it into a MultiPolygon;
            # each piece is its own object, same as the chip path treats them.
            for part in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
                xs, ys = np.asarray(part.exterior.coords).T
                tl_lines.append("0 " + " ".join(
                    f"{v:.6f}" for v in np.ravel(np.column_stack([xs / W, ys / H]))))
        (tl_dir / f"{tile}.txt").write_text(("\n".join(tl_lines) + "\n") if tl_lines else "")
        for ri, y0 in enumerate(chip_origins(H, chip)):
            for ci, x0 in enumerate(chip_origins(W, chip)):
                name = f"{tile}_r{ri}c{ci}"
                lines = emit_chip_polygons(polys, x0, y0, chip, px_area_m2)

                img_id = len(coco[split]["images"]) + 1
                coco[split]["images"].append(
                    {"id": img_id, "file_name": f"{name}.png", "width": chip, "height": chip})
                for ln in lines:
                    c = np.asarray(ln.split()[1:], float)
                    xs, ys = c[0::2] * chip, c[1::2] * chip
                    areas_m2.append(Polygon(list(zip(xs, ys))).area * px_area_m2)
                    x0b, y0b = float(xs.min()), float(ys.min())
                    coco[split]["annotations"].append({
                        "id": len(coco[split]["annotations"]) + 1, "image_id": img_id,
                        "category_id": 1, "iscrowd": 0,
                        "segmentation": [[round(float(v), 2) for v in np.ravel(np.column_stack([xs, ys]))]],
                        "bbox": [round(x0b, 2), round(y0b, 2),
                                 round(float(xs.max()) - x0b, 2), round(float(ys.max()) - y0b, 2)],
                        "area": round(float(Polygon(list(zip(xs, ys))).area), 2)})

                img_dir = args.out_dir / "images" / split
                lab_dir = args.out_dir / "labels" / split
                img_dir.mkdir(parents=True, exist_ok=True)
                lab_dir.mkdir(parents=True, exist_ok=True)
                # Crop at native resolution — no resampling anywhere in this path.
                Image.fromarray(arr[y0:y0 + chip, x0:x0 + chip]).save(img_dir / f"{name}.png")
                (lab_dir / f"{name}.txt").write_text(("\n".join(lines) + "\n") if lines else "")

                ct = transform * rasterio.Affine.translation(x0, y0)
                chips_index[f"{name}.png"] = {
                    "transform": list(ct)[:6], "crs": str(crs), "width": chip, "height": chip,
                    "parent_tile": tile, "split": split, "vintage": "scc_2025",
                    "bounds": dict(zip(("minx", "miny", "maxx", "maxy"),
                                       map(float, rasterio.transform.array_bounds(chip, chip, ct)))),
                }
                per_split[split] = per_split.get(split, 0) + 1

    (args.out_dir / "tile_index_chips.json").write_text(json.dumps(
        {"tiles": chips_index,
         "metadata": {"crs": "EPSG:3857", "vintage": "scc_2025", "chip_px": chip,
                      "source": "SCC Imagery_2025", "n_chips": len(chips_index)}}, indent=2))
    (args.out_dir / "data.yaml").write_text(
        f"path: {args.out_dir.resolve()}\ntrain: images/train\nval: images/val\ntest: images/test\n"
        f"nc: 1\nnames: ['solar_array']\n")

    ann_dir = args.out_dir / "annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)
    for split, payload in coco.items():
        if not payload["images"]:
            continue
        (ann_dir / f"instances_{split}.json").write_text(json.dumps(
            {"info": {"description": "SCC 2025 21cm solar arrays", "vintage": "scc_2025"},
             "categories": [{"id": 1, "name": "solar_array", "supercategory": "none"}],
             **payload}))

    a = np.asarray(areas_m2) if areas_m2 else np.zeros(1)
    med = float(np.median(a))
    qa = {"n_tiles_imported": len(set(t for t in splits if t in exported) - untouched_set),
          "n_tiles_missing_labels": len(missing), "missing": missing,
          "n_untouched_seeds": len(untouched), "untouched_seeds": untouched,
          "n_source_polygons": n_poly, "n_chip_polygons": len(areas_m2),
          "chips_per_split": per_split,
          "area_m2": {"median": round(med, 2), "p10": round(float(np.percentile(a, 10)), 2),
                      "p90": round(float(np.percentile(a, 90)), 2)},
          "naip_median_m2": NAIP_MEDIAN_M2,
          "per_panel_drift_suspected": bool(med < NAIP_MEDIAN_M2 * 0.4)}
    (args.out_dir / "import_qa.json").write_text(json.dumps(qa, indent=2))

    logger.info(f"wrote {len(chips_index)} chips -> {args.out_dir}  splits={per_split}")
    logger.info(f"  polygons: {n_poly} source -> {len(areas_m2)} after chipping")
    logger.info(f"  area m2: median {med:.1f} (60cm set: {NAIP_MEDIAN_M2})")
    if qa["per_panel_drift_suspected"]:
        logger.error("  MEDIAN AREA COLLAPSED vs the 60cm set — likely per-panel labeling drift. "
                     "Check the convention (docs/LABELING_SPRINT_21CM.md §2) before training.")
    if missing:
        logger.warning(f"  {len(missing)} tile(s) missing labels — finish those batches.")


if __name__ == "__main__":
    main()
