#!/usr/bin/env python3
"""Stage 1 GA gate: tile-level detection F1 through the production inference path.

This replaces "val SAHI F1 >= 0.65", which named a metric that no longer exists on this stack
(different labels, different imagery, and no SAHI). See `.claude/rules/stage1-detect.md` for the
gate wording; this script *is* the definition -- if the two ever disagree, the script wins.

WHAT IS MEASURED, EXACTLY
-------------------------
1. **The production path, not a chip benchmark.** Each full ~1200px tile is cut into the same 2x2
   grid of 640px chips the model trained on, one RF-DETR pass per chip, then boxes are lifted into
   tile space and merged across the ~80px seams with NMS. Chip-level scoring double-counts every
   array in an overlap and never exercises the seam merge, so it is not a product number.
2. **Boxes, not masks.** Matching is axis-aligned box IoU >= 0.50 via `src/utils/det_match.py`, the
   same greedy one-to-one matcher every other eval in this repo uses. The gate is therefore
   invariant to whether SAM2 is in the pipeline -- SAM changes area accuracy, not what was found.
   Mask/area quality is a *separate* measurement (`scripts/analyze/sam_mask_probe.py`); conflating
   the two is how you end up unable to say which stage regressed.
3. **Micro-averaged, not mean-of-tiles.** TP/FP/FN are pooled across the split before computing
   P/R/F1. A tile holding one array should not weigh the same as a tile holding thirty.
4. **conf is tuned on val and frozen; test is the reported number.** The W1 manifest tuned conf on
   test, which silently spent the held-out split -- test F1 0.809 was an optimistic, tuned-on
   figure. Here val (25 tiles / 385 GT) picks the operating point and test (49 tiles / 636 GT)
   never influences it, so the reported number is genuinely held out. Test is also the larger
   split, which is where you want your statistical power.
5. **A CI, not just a point.** 95% bootstrap over *tiles* (never over objects -- arrays within a
   tile are correlated, so resampling objects understates the interval). Same reporting discipline
   as the Stage 2 gate.

Inference runs ONCE at a low confidence floor and every threshold is then swept over the cached
detections. The old `sahi_threshold_sweep.py` re-ran inference per conf/iou combination, which is
why it was slow enough to avoid running.

USAGE
-----
    # full gate: tune on val, report test
    PYTHONPATH=. python scripts/detect/eval_tile_f1.py \
        --weights models/rfdetr_w2_<date>.pth --run-name rfdetr_w2

    # reuse cached detections (instant; re-sweeps only)
    PYTHONPATH=. python scripts/detect/eval_tile_f1.py --run-name rfdetr_w2 --cached

    # no-regression anchor: same path, previous weights
    PYTHONPATH=. python scripts/detect/eval_tile_f1.py \
        --weights models/rfdetr_w1_20260806.pth --run-name rfdetr_w1_anchor
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.detect.rfdetr_infer import CHIP, chip_origins, detect_chip, load_detector, nms  # noqa: E402
from src.utils.det_match import match_predictions  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IOU_MATCH = 0.50        # detection match threshold -- fixed, not a tunable
IOU_NMS = 0.55          # chip-seam merge -- fixed, part of the production path
CONF_FLOOR = 0.05       # inference floor; every reported threshold is swept above this


def polygon_txt_to_boxes(path: Path, width: int, height: int) -> list[tuple[float, ...]]:
    """YOLO-seg normalized polygon .txt -> axis-aligned boxes in tile pixels."""
    if not path.exists():
        return []
    boxes = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 7:      # class + at least 3 points
            continue
        coords = np.array([float(v) for v in parts[1:]], dtype=np.float64).reshape(-1, 2)
        coords[:, 0] *= width
        coords[:, 1] *= height
        boxes.append((coords[:, 0].min(), coords[:, 1].min(),
                      coords[:, 0].max(), coords[:, 1].max()))
    return boxes


def run_inference(model, tiles: list[Path], conf_floor: float) -> dict[str, list[dict]]:
    """One chip-grid pass per tile at the confidence floor. Boxes come back in tile space."""
    out: dict[str, list[dict]] = {}
    for i, tile in enumerate(tiles, 1):
        image = np.array(Image.open(tile).convert("RGB"))
        H, W = image.shape[:2]
        dets: list[dict] = []
        for (ox, oy) in chip_origins(W, H):
            for det in detect_chip(model, image[oy:oy + CHIP, ox:ox + CHIP], conf_floor):
                det["box"] = det["box"] + np.array([ox, oy, ox, oy])
                dets.append(det)
        dets = nms(dets, IOU_NMS)
        out[tile.stem] = [{"box": [float(v) for v in d["box"]], "score": float(d["score"])}
                          for d in dets]
        if i % 10 == 0 or i == len(tiles):
            logger.info("  inferred %d/%d tiles", i, len(tiles))
    return out


def per_tile_counts(dets: dict[str, list[dict]], gts: dict[str, list], conf: float
                    ) -> dict[str, tuple[int, int, int]]:
    """(tp, fp, fn) per tile at a confidence threshold. Preds sorted best-first for the matcher."""
    counts = {}
    for tile, gt_boxes in gts.items():
        preds = sorted((d for d in dets.get(tile, []) if d["score"] >= conf),
                       key=lambda d: -d["score"])
        m = match_predictions([tuple(p["box"]) for p in preds], gt_boxes, IOU_MATCH)
        counts[tile] = (len(m.tp), len(m.fp), len(m.fn))
    return counts


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f1


def pooled(counts: dict[str, tuple[int, int, int]]) -> tuple[float, float, float]:
    tp = sum(c[0] for c in counts.values())
    fp = sum(c[1] for c in counts.values())
    fn = sum(c[2] for c in counts.values())
    return prf(tp, fp, fn)


def bootstrap_f1(counts: dict[str, tuple[int, int, int]], n: int, seed: int) -> tuple[float, float]:
    """95% CI by resampling TILES with replacement -- objects within a tile are correlated."""
    rng = np.random.default_rng(seed)
    arr = np.array(list(counts.values()), dtype=np.int64)      # (n_tiles, 3)
    idx = rng.integers(0, len(arr), size=(n, len(arr)))
    sums = arr[idx].sum(axis=1)                                # (n, 3)
    tp, fp, fn = sums[:, 0], sums[:, 1], sums[:, 2]
    denom = 2 * tp + fp + fn
    f1 = np.divide(2 * tp, denom, out=np.zeros(n, dtype=float), where=denom > 0)
    return float(np.percentile(f1, 2.5)), float(np.percentile(f1, 97.5))


def load_split(split: str, tile_dir: Path, label_dir: Path) -> tuple[list[Path], dict[str, list]]:
    """Tile images come from our lossless PNGs; split membership + GT from the tile-level labels."""
    label_files = sorted((label_dir / split).glob("*.txt"))
    if not label_files:
        raise SystemExit(
            f"no tile-level GT under {label_dir / split}. Rebuild with "
            f"scripts/data/import_21cm_from_roboflow.py (it emits tile_labels/), or point "
            f"--tile-labels at a Roboflow export's <split>/labels directory.")
    tiles, gts = [], {}
    for lf in label_files:
        tile = tile_dir / f"{lf.stem}.png"
        if not tile.exists():
            logger.warning("no image for %s -- skipping", lf.stem)
            continue
        with Image.open(tile) as im:
            W, H = im.size
        tiles.append(tile)
        gts[lf.stem] = polygon_txt_to_boxes(lf, W, H)
    return tiles, gts


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--weights", type=Path, default=REPO_ROOT / "models/rfdetr_w1_20260806.pth")
    p.add_argument("--tiles", type=Path, default=REPO_ROOT / "data/interim/scc21_labelset/images")
    p.add_argument("--tile-labels", type=Path, default=REPO_ROOT / "data/yolo/scc21/tile_labels")
    p.add_argument("--out-dir", type=Path, default=REPO_ROOT / "outputs/eval")
    p.add_argument("--run-name", required=True)
    p.add_argument("--tune-split", default="val")
    p.add_argument("--report-split", default="test")
    p.add_argument("--conf-floor", type=float, default=CONF_FLOOR)
    p.add_argument("--conf-grid", default="0.05:0.90:0.05", help="start:stop:step")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--cached", action="store_true", help="reuse detections.json; skip inference")
    p.add_argument("--device", default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    out_dir = args.out_dir / args.run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "detections.json"

    splits = {}
    for split in (args.tune_split, args.report_split):
        tiles, gts = load_split(split, args.tiles, args.tile_labels)
        splits[split] = {"tiles": tiles, "gts": gts}
        logger.info("%s: %d tiles, %d GT objects", split, len(tiles),
                    sum(len(v) for v in gts.values()))

    if args.cached and cache_path.exists():
        cached = json.loads(cache_path.read_text())
        logger.info("reusing cached detections from %s", cache_path)
    else:
        import torch
        device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
        logger.info("loading RF-DETR from %s on %s", args.weights, device)
        model = load_detector(args.weights, device)
        cached = {}
        for split, d in splits.items():
            logger.info("inference on %s (conf floor %.2f)", split, args.conf_floor)
            cached[split] = run_inference(model, d["tiles"], args.conf_floor)
        cache_path.write_text(json.dumps(cached))

    start, stop, step = (float(v) for v in args.conf_grid.split(":"))
    grid = [round(c, 4) for c in np.arange(start, stop + 1e-9, step)]

    # --- tune on the tune split -------------------------------------------------------
    sweep_rows = []
    best = (-1.0, grid[0])
    for conf in grid:
        counts = per_tile_counts(cached[args.tune_split], splits[args.tune_split]["gts"], conf)
        p, r, f1 = pooled(counts)
        sweep_rows.append({"split": args.tune_split, "conf": conf,
                           "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)})
        if f1 > best[0]:
            best = (f1, conf)
    tuned_f1, conf_star = best
    logger.info("tuned on %s: conf*=%.2f (F1 %.3f)", args.tune_split, conf_star, tuned_f1)

    # --- report on the held-out split at the FROZEN conf -------------------------------
    report_counts = per_tile_counts(cached[args.report_split],
                                    splits[args.report_split]["gts"], conf_star)
    p, r, f1 = pooled(report_counts)
    lo, hi = bootstrap_f1(report_counts, args.bootstrap, args.seed)
    n_gt = sum(len(v) for v in splits[args.report_split]["gts"].values())

    # The report split's own best-F1, recorded ONLY to show the tuning penalty. Never gate on it:
    # it is optimised on the split it is measured on.
    report_best = max(pooled(per_tile_counts(cached[args.report_split],
                                             splits[args.report_split]["gts"], c))[2] for c in grid)

    for conf in grid:
        counts = per_tile_counts(cached[args.report_split],
                                 splits[args.report_split]["gts"], conf)
        pp, rr, ff = pooled(counts)
        sweep_rows.append({"split": args.report_split, "conf": conf, "precision": round(pp, 4),
                           "recall": round(rr, 4), "f1": round(ff, 4)})

    with (out_dir / "threshold_sweep.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["split", "conf", "precision", "recall", "f1"])
        w.writeheader()
        w.writerows(sweep_rows)

    gate = {
        "run_name": args.run_name,
        "weights": str(args.weights),
        "metric": "tile-level box F1 @ IoU 0.50, micro-averaged, production chip-grid path",
        "iou_match": IOU_MATCH, "iou_nms": IOU_NMS, "chip": CHIP,
        "tuned_on": args.tune_split, "conf_star": conf_star,
        f"{args.tune_split}_f1_at_conf_star": round(tuned_f1, 4),
        "reported_on": args.report_split,
        "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
        "f1_ci95": [round(lo, 4), round(hi, 4)],
        "n_gt": n_gt, "n_tiles": len(report_counts),
        f"{args.report_split}_best_f1_tuned_on_itself": round(report_best, 4),
        "gate": {
            "f1_point_ge_0.75": bool(f1 >= 0.75),
            "f1_ci_lower_ge_0.70": bool(lo >= 0.70),
            "recall_ge_0.70": bool(r >= 0.70),
        },
    }
    gate["gate"]["PASS"] = all(gate["gate"].values())
    (out_dir / "gate.json").write_text(json.dumps(gate, indent=2))

    logger.info("=" * 72)
    logger.info("%s @ conf %.2f (frozen from %s)", args.report_split, conf_star, args.tune_split)
    logger.info("  P %.3f  R %.3f  F1 %.3f  95%% CI [%.3f, %.3f]  n_gt=%d", p, r, f1, lo, hi, n_gt)
    logger.info("  gate: %s", "PASS" if gate["gate"]["PASS"] else "FAIL")
    for k, v in gate["gate"].items():
        if k != "PASS":
            logger.info("    %-22s %s", k, "ok" if v else "MISS")
    logger.info("results -> %s", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
