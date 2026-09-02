#!/usr/bin/env python3
"""Permissive-stack Stage 1 inference: RF-DETR box -> SAM2 mask -> YOLO polygon .txt.

This is the W5 port (docs/PERMISSIVE_STACK_MIGRATION.md). It mirrors the *output contract* of
`scripts/detect/infer.py` exactly -- normalized polygon `.txt` under `runs/segment/<name>/labels/`
plus a `manifest.json` -- so `export_polygons_geojson.py` and all of Stage 2 keep working
unchanged. Nothing downstream imports a detector; that file boundary is the whole port surface.

THREE THINGS THAT ARE EASY TO GET WRONG HERE
--------------------------------------------
1. **Never feed a whole 1200px tile to the network.** RF-DETR has a fixed 728px input, so a
   1201px tile is downscaled 0.61x while the 640px training chips were *upscaled* 1.14x. The
   median array (26.7 chip px) would arrive at 16px instead of the 30px the model learned -- and
   84% of our objects are COCO-small, so that is exactly the population that disappears. We
   therefore re-chip each tile into the SAME 2x2 grid of 640px windows the training data was
   built from and run one pass per chip, then merge. This is what SAHI used to do; we don't need
   the library because our own chipping already does it with recorded geometry.

2. **Shrink the box before prompting SAM2.** Measured on 21cm val chips: an exact box gives
   median IoU 0.809 with 0% roof-grab, but a box dilated 50% collapses to 0.551 with 47.5% of
   masks exceeding 2x the true array area -- SAM escapes onto the roof plane. A box shrunk 15%
   is the *best* setting (0.824), because SAM expands to the true boundary anyway. See
   `scripts/analyze/sam_mask_probe.py`.

3. **Ground GSD is not the affine pixel size.** Tiles are EPSG:3857, where Web Mercator inflates
   pixel size by 1/cos(lat) -- 0.2558 m/px on paper vs 0.2043 m/px on the ground at Santa Cruz,
   a 1.57x area error if confused. Read `gsd_ground_m` from the tile index; never hardcode.
   (The Drive copy of the W1 manifest shipped `gsd_m: 0.6`, stale from the 60cm notebook, which
   would have inflated every area 8.6x.)

USAGE
-----
    PYTHONPATH=. python scripts/detect/rfdetr_infer.py \
        --weights models/rfdetr_w1_20260806.pth \
        --source data/interim/scc21_labelset/images \
        --tile-index data/interim/scc21_labelset/tile_index_21cm.json \
        --name rfdetr_scc21

    # then, unchanged:
    python scripts/detect/export_polygons_geojson.py \
        --labels-dir runs/segment/rfdetr_scc21/labels \
        --tile-index data/interim/scc21_labelset/tile_index_21cm.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from solarsoiled.manifest import write_manifest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CHIP = 640          # training chip size, in native tile pixels
NET_RES = 728       # RF-DETR input resolution (must match training)


def chip_origins(width: int, height: int, chip: int = CHIP) -> list[tuple[int, int]]:
    """Top-left corners of the 2x2 grid used to build the training set.

    Mirrors `scripts/data/import_21cm_from_roboflow.py`: corners are flush to the tile edges,
    so a 1201x1196 tile yields origins (0,0) (561,0) (0,556) (561,556) -- ~80px of overlap.
    Tiles smaller than one chip fall back to a single origin.
    """
    xs = [0] if width <= chip else sorted({0, width - chip})
    ys = [0] if height <= chip else sorted({0, height - chip})
    return [(x, y) for y in ys for x in xs]


def box_iou(a: np.ndarray, b: np.ndarray) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def nms(dets: list[dict], iou_thresh: float) -> list[dict]:
    """Greedy NMS over tile-space boxes -- deduplicates arrays seen in two overlapping chips."""
    kept: list[dict] = []
    for d in sorted(dets, key=lambda x: -x["score"]):
        if all(box_iou(d["box"], k["box"]) < iou_thresh for k in kept):
            kept.append(d)
    return kept


def shrink_box(box: np.ndarray, factor: float, w: int, h: int) -> np.ndarray:
    """Contract a box about its centre. factor 0.85 == 15% smaller (the measured optimum)."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = (x1 - x0) * factor / 2, (y1 - y0) * factor / 2
    return np.array([max(0, cx - hw), max(0, cy - hh), min(w - 1, cx + hw), min(h - 1, cy + hh)])


def mask_to_polygon(mask: np.ndarray) -> np.ndarray | None:
    """Largest contour -> minAreaRect -> 4 corner points (the production area recipe)."""
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    rect = cv2.minAreaRect(max(contours, key=cv2.contourArea))
    if rect[1][0] <= 0 or rect[1][1] <= 0:
        return None
    return cv2.boxPoints(rect)


# --- Keeping SAM on the panels and off the roof ------------------------------------------
# SAM's failure here is not noise, it is *ambiguity*: prompted with a box on an array, "the
# array", "one module", and "the whole roof plane" are all defensible answers, and the roof is
# the one that costs us 1.8x on area. Three layered defenses, cheapest first:
#
#   1. ASK FOR ALL THREE ANSWERS. multimask_output=True returns SAM's part / subpart / whole
#      candidates instead of one arbitrary pick. The roof-grab is literally the "whole"
#      candidate, so the fix is to *choose*, not to accept and then reject.
#   2. CLIP TO A WINDOW. Intersect with the detector box dilated by --mask-window. This bounds
#      area geometrically rather than by rejection: a mask physically cannot spill onto the roof
#      because those pixels are zeroed. The detector box is a real localization signal and there
#      is no reason a panel mask should extend far past it.
#   3. KEEP ONE BLOB. After clipping, take the connected component under the box centre, which
#      drops the neighbouring array or shadow that clipping left behind at the window edge.
#
# The old 2x-area guard stays as the last resort, but it should now almost never fire.


def clip_to_window(mask: np.ndarray, box: np.ndarray, window: float) -> np.ndarray:
    """Zero everything outside the detector box dilated by `window` about its centre."""
    h, w = mask.shape
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = (x1 - x0) * window / 2, (y1 - y0) * window / 2
    ix0, iy0 = max(0, int(np.floor(cx - hw))), max(0, int(np.floor(cy - hh)))
    ix1, iy1 = min(w, int(np.ceil(cx + hw))), min(h, int(np.ceil(cy + hh)))
    out = np.zeros_like(mask)
    if ix1 > ix0 and iy1 > iy0:
        out[iy0:iy1, ix0:ix1] = mask[iy0:iy1, ix0:ix1]
    return out


def keep_centre_blob(mask: np.ndarray, box: np.ndarray) -> np.ndarray:
    """Keep the connected component under the box centre; fall back to the largest."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 2:                      # background + at most one blob
        return mask
    h, w = mask.shape
    cx = int(np.clip((box[0] + box[2]) / 2, 0, w - 1))
    cy = int(np.clip((box[1] + box[3]) / 2, 0, h - 1))
    lab = int(labels[cy, cx])
    if lab == 0:                    # centre landed on background
        lab = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (labels == lab).astype(np.uint8)


def choose_mask(masks: np.ndarray, scores: np.ndarray, box: np.ndarray,
                window: float, max_area_ratio: float) -> tuple[np.ndarray | None, str]:
    """Pick the SAM candidate that is a panel array rather than a roof plane.

    Every candidate is clipped and de-blobbed first, so selection compares what would actually
    ship. Selection is by **area fit to the detector box**, not by SAM's confidence: SAM's score
    answers "is this a good mask", which the whole-roof candidate often wins, whereas we already
    know *which* object we want -- the one the detector boxed. The box is a tight bound on the
    array by construction, and the measured optimum prompt returns median area/GT = 1.03, so a
    ratio near 1.0 is the correct prior. SAM's score is only the tiebreak.

    If every candidate blows the budget we take the smallest, the least-wrong answer, and let the
    caller's guard decide whether even that is usable.
    """
    box_area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    cleaned, areas, keep = [], [], []
    for m in masks:
        c = keep_centre_blob(clip_to_window(m.astype(np.uint8), box, window), box)
        cleaned.append(c)
        a = float(c.sum())
        areas.append(a)
        keep.append(0 < a <= max_area_ratio * box_area)
    if not cleaned:
        return None, "no_candidates"
    if any(keep):
        idx = min((i for i, k in enumerate(keep) if k),
                  key=lambda i: (abs(areas[i] / box_area - 1.0), -float(scores[i])))
        return cleaned[idx], "selected"
    positive = [i for i, a in enumerate(areas) if a > 0]
    if not positive:
        return None, "all_empty"
    return cleaned[min(positive, key=lambda i: areas[i])], "all_over_budget"


def load_detector(weights: Path, device: str):
    try:
        from rfdetr import RFDETRBase
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(
            'RF-DETR not installed. Run: pip install "rfdetr[train,loggers]"') from exc
    model = RFDETRBase(resolution=NET_RES, pretrain_weights=str(weights), device=device)
    return model


def load_sam(device: str):
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    return SAM2ImagePredictor.from_pretrained("facebook/sam2-hiera-large", device=device)


def detect_chip(model, chip_img: np.ndarray, conf: float) -> list[dict]:
    """Run RF-DETR on one chip. Returns boxes in CHIP pixel coords."""
    result = model.predict(Image.fromarray(chip_img), threshold=conf)
    boxes = np.asarray(getattr(result, "xyxy", []), dtype=np.float64).reshape(-1, 4)
    scores = np.asarray(getattr(result, "confidence", []), dtype=np.float64).reshape(-1)
    class_ids = np.asarray(getattr(result, "class_id", np.zeros(len(boxes))), dtype=int).reshape(-1)
    return [{"box": boxes[i], "score": float(scores[i]), "cls": 0 if class_ids[i] < 0 else 0}
            for i in range(len(boxes))]


def process_tile(tile_path: Path, model, sam, args, gsd_m: float) -> tuple[list[str], list[dict]]:
    image = np.array(Image.open(tile_path).convert("RGB"))
    H, W = image.shape[:2]

    # --- 1. detect per chip, in chip space, then lift into tile space -------------------
    detections: list[dict] = []
    for (ox, oy) in chip_origins(W, H):
        chip = image[oy:oy + CHIP, ox:ox + CHIP]
        for det in detect_chip(model, chip, args.conf):
            det["box"] = det["box"] + np.array([ox, oy, ox, oy])
            det["origin"] = (ox, oy)
            detections.append(det)
    detections = nms(detections, args.iou)

    # --- 2. mask each surviving box with SAM2, one set_image per chip -------------------
    # Cost is per chip (~30s CPU / ~0.3s GPU), not per box, so group by the chip a box came from.
    lines: list[str] = []
    records: list[dict] = []
    by_chip: dict[tuple[int, int], list[dict]] = {}
    for d in detections:
        by_chip.setdefault(d["origin"], []).append(d)

    for (ox, oy), dets in by_chip.items():
        chip = image[oy:oy + CHIP, ox:ox + CHIP]
        ch, cw = chip.shape[:2]
        if sam is not None:
            sam.set_image(chip)
        for d in dets:
            local = d["box"] - np.array([ox, oy, ox, oy])
            box_area = max(1.0, (local[2] - local[0]) * (local[3] - local[1]))
            box_poly = np.array([[local[0], local[1]], [local[2], local[1]],
                                 [local[2], local[3]], [local[0], local[3]]])

            if sam is None:
                # Detector-only mode. The box IS the polygon, so areas carry the measured
                # box-as-polygon error (0.509 median IoU, 78.5% area error -- see
                # sam_mask_probe.py). Geometry is honest for *detection* scoring, which is why
                # the Stage 1 gate matches on boxes; it is NOT good enough to price a roof.
                mask, blown, poly = None, False, box_poly
            else:
                prompt = shrink_box(local, args.box_shrink, cw, ch)
                if args.mask_containment:
                    masks, scores, _ = sam.predict(box=prompt, multimask_output=True)
                    mask, reason = choose_mask(masks, scores, local,
                                               args.mask_window, args.max_area_ratio)
                else:
                    masks, _, _ = sam.predict(box=prompt, multimask_output=False)
                    mask, reason = masks[0].astype(np.uint8), "single"

                # Guard: SAM escaping onto the roof plane shows up as a mask far larger than its
                # prompt. Fall back to the detector box rather than ship a 2x area error.
                blown = mask is None or mask.sum() > args.max_area_ratio * box_area
                poly = None if blown else mask_to_polygon(mask)
                if poly is None:
                    poly = box_poly
                    mask = None

            poly_tile = poly + np.array([ox, oy])
            poly_tile[:, 0] = poly_tile[:, 0].clip(0, W - 1)
            poly_tile[:, 1] = poly_tile[:, 1].clip(0, H - 1)
            norm = poly_tile / np.array([W, H])
            lines.append("0 " + " ".join(f"{v:.6f}" for v in norm.reshape(-1)))

            # Area comes from the MASK, not the minAreaRect polygon. Contours trace pixel
            # *centres*, so a 20px-wide blob yields a 19px rect -- a ~10% area underestimate at
            # our median 26px array size, biased consistently low and straight into the dollar
            # figure. The rect is still the right shape to ship as the polygon; it is just the
            # wrong thing to integrate. Falls back to the rect when the guard rejected the mask.
            area_px = (float(mask.sum()) if mask is not None and not blown
                       else cv2.contourArea(poly_tile.astype(np.float32)))
            records.append({"tile": tile_path.stem, "score": d["score"],
                            "box": [round(float(v), 2) for v in d["box"]],
                            "area_m2": round(area_px * gsd_m ** 2, 2),
                            "area_ratio_to_box": round(area_px / box_area, 3),
                            "area_source": "box" if sam is None else ("box" if blown else "sam_mask"),
                            "mask_selection": None if sam is None else reason,
                            "sam_fallback_to_box": bool(blown)})
    return lines, records


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--weights", type=Path, default=REPO_ROOT / "models/rfdetr_w1_20260806.pth")
    p.add_argument("--source", type=Path, required=True, help="directory of full tiles")
    p.add_argument("--tile-index", type=Path,
                   default=REPO_ROOT / "data/interim/scc21_labelset/tile_index_21cm.json")
    p.add_argument("--project", type=Path, default=REPO_ROOT / "runs/segment")
    p.add_argument("--name", default="rfdetr_predict")
    p.add_argument("--conf", type=float, default=0.25,
                   help="operating confidence (test-tuned; see the W1 manifest)")
    p.add_argument("--iou", type=float, default=0.50, help="NMS IoU for the chip-seam merge")
    p.add_argument("--no-sam", action="store_true",
                   help="skip the SAM2 mask stage; emit each detector box as the polygon. "
                        "Detection geometry is unchanged (the gate matches on boxes), so this is "
                        "the right mode for scoring a detector and for CPU runs -- set_image costs "
                        "~30s/chip. Areas are then box areas and are NOT product-grade.")
    p.add_argument("--box-shrink", type=float, default=0.85,
                   help="contract detector boxes before prompting SAM2 (0.85 = 15%% smaller)")
    p.add_argument("--mask-containment", action="store_true",
                   help="EXPERIMENTAL, off because it MEASURED WORSE. Multimask + clip + "
                        "area-fit selection: 0.824 -> 0.673 median IoU on GT boxes, and roof-grab "
                        "1.7%% -> 71.7%% at dilate25. Area-fit selects the candidate matching the "
                        "detector box, which amplifies box error in exactly the regime it was "
                        "meant to fix. Kept only for further study (outputs/eval/"
                        "sam_containment_ab.json).")
    p.add_argument("--mask-window", type=float, default=1.25,
                   help="hard-clip each SAM mask to the detector box dilated by this factor. "
                        "Bounds area geometrically instead of by rejection -- the roof pixels are "
                        "zeroed, so the mask cannot spill onto the roof plane at all.")
    p.add_argument("--max-area-ratio", type=float, default=2.0,
                   help="reject a SAM mask larger than this multiple of its prompt box")
    p.add_argument("--device", default=None)
    p.add_argument("--resume", action="store_true",
                   help="skip tiles whose label file already exists (safe: labels are written "
                        "atomically, so a file on disk is a finished tile)")
    return p.parse_args(argv)


def _tile_gsd(tile: Path, tile_index: dict) -> float | None:
    """Ground GSD for `tile` from the index, or None if absent.

    Tile indices key on the .png name even when the raster on disk is a .tif, so try
    both before giving up.
    """
    meta = tile_index.get(tile.name) or tile_index.get(f"{tile.stem}.png") or {}
    gsd = meta.get("gsd_ground_m")
    return float(gsd) if gsd is not None else None


def main(argv=None) -> int:
    args = parse_args(argv)
    import torch
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")

    if not args.source.is_dir():
        logger.error("source is not a directory: %s", args.source)
        return 1
    tiles = sorted(p for p in args.source.iterdir()
                   if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif"))
    if not tiles:
        logger.error("no tiles found in %s", args.source)
        return 1

    raw = json.loads(args.tile_index.read_text())
    tile_index = raw.get("tiles", raw)

    # Pre-flight the GSD lookup for EVERY tile before loading any weights. This used to
    # warn-and-skip per tile, which meant a tile index without gsd_ground_m (the NAIP one
    # still has none) produced an empty arrays.geojson and exit code 0 -- a silent
    # no-detections result that looks identical to "this AOI has no arrays". Failing here
    # costs two seconds; failing quietly costs a partner run. Checked before load_detector
    # so the error arrives before the ~9.5 s/chip SAM2 stage is even initialised.
    gsds = {t: _tile_gsd(t, tile_index) for t in tiles}
    missing = [t.name for t, g in gsds.items() if g is None]
    if missing:
        shown = ", ".join(missing[:5]) + (f", … (+{len(missing) - 5} more)" if len(missing) > 5 else "")
        logger.error(
            "%d/%d tiles have no gsd_ground_m in %s: %s\n"
            "Areas are computed as area_px * gsd^2, so a missing GSD is a wrong m2 and "
            "therefore a wrong dollar figure. Re-generate the tile index with "
            "gsd_ground_m (scripts/data/tile_naip_image.py emits it), or point --tile-index "
            "at one that carries it (e.g. data/interim/scc21_labelset/tile_index_21cm.json).",
            len(missing), len(tiles), args.tile_index, shown)
        return 2

    out_dir = args.project / args.name
    labels_dir = out_dir / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)

    logger.info("loading RF-DETR (res=%d)%s on %s", NET_RES,
                "" if args.no_sam else " + SAM2", device)
    model = load_detector(args.weights, device)
    sam = None if args.no_sam else load_sam(device)

    all_records: list[dict] = []
    n_skipped = 0
    for i, tile in enumerate(tiles, 1):
        label_path = labels_dir / f"{tile.stem}.txt"
        if args.resume and label_path.exists():
            # A 249-tile AOI is ~15 h of CPU with SAM2, on a laptop that gets closed. Without
            # this the run restarts from tile 1 every time. Written atomically below, so a
            # label file on disk is a COMPLETE tile -- never a half-written one that resume
            # would then trust.
            n_skipped += 1
            continue
        lines, records = process_tile(tile, model, sam, args, gsds[tile])
        tmp = label_path.with_suffix(".txt.part")
        tmp.write_text("\n".join(lines))
        tmp.replace(label_path)
        all_records.extend(records)
        logger.info("[%d/%d] %s -> %d detections", i, len(tiles), tile.name, len(lines))
    if n_skipped:
        logger.info("resumed: skipped %d tiles already done", n_skipped)

    n_fallback = sum(r["sam_fallback_to_box"] for r in all_records)
    logger.info("total %d detections across %d tiles | %d SAM masks rejected by the area guard",
                len(all_records), len(tiles), n_fallback)

    write_manifest(
        out_dir,
        stage="stage1_detect",
        model_version=f"stage1-rfdetr-{args.weights.stem}",
        model_weights=args.weights,
        inputs=[str(t) for t in tiles],
        # n_detections counts what is ON DISK, not what this process produced -- on a resumed
        # run those differ, and the manifest describes the artifact, not the session.
        metrics={"conf": args.conf, "iou": args.iou, "resolution": NET_RES,
                 "n_tiles": len(tiles),
                 "n_detections": sum(len([ln for ln in p.read_text().splitlines() if ln.strip()])
                                     for p in labels_dir.glob("*.txt")),
                 "n_detections_this_run": len(all_records),
                 "n_tiles_resumed": n_skipped,
                 "n_sam_area_guard_fallbacks": n_fallback},
        known_limitations=[
            "Trained on Santa Cruz County 2025 21cm imagery; unvalidated at 60cm NAIP "
            "(see scripts/data/build_gsd_ablation.py)",
        ] + ([
            "--no-sam: polygons are detector boxes, so area_m2 carries ~78% error "
            "(sam_mask_probe.py). Detection geometry is unaffected; areas are not product-grade."
        ] if args.no_sam else []),
        extra={"detector": "rf-detr",
               "mask_stage": None if args.no_sam else "sam2-hiera-large",
               "chip_tiling": f"{CHIP}px 2x2 grid", "box_shrink": args.box_shrink,
               "permissive_stack": True},
    )
    (out_dir / "detections.json").write_text(json.dumps(all_records, indent=1))
    logger.info("results -> %s", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
