"""Validate the W4 mask/area stage: box -> SAM mask -> minAreaRect -> m2.

Answers two questions with one run:

  1. Is the mask model good enough?  (vs a no-SAM control that uses the prompt box directly)
  2. How wrong can the DETECTOR box be before SAM escapes onto the roof plane?

Question 2 is the one that matters in production. Prompting with ground-truth boxes measures
a ceiling nobody ever operates at; a real detector box is loose, tight, or shifted, and the
whole-roof blowout is conditioned on *which*. So the box is perturbed on purpose.

Runs SAM2 (Apache-2.0) or SAM3 (Meta SAM License) behind one interface -- they expose the
identical `set_image` / `predict(box=..., multimask_output=...)` contract, so this is a true
A/B on the same arrays with the same evaluator.

    # SAM2 -- works today
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/sam_mask_probe.py --model sam2

    # SAM3 -- GPU ONLY, and needs approved weights access (see below)
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/sam_mask_probe.py --model sam3

Two things gate the SAM3 arm, both external (verified 2026-08-06):
  1. `facebook/sam3` on HuggingFace is `gated: manual` -- Meta approves access by hand.
     Request it, then `huggingface-cli login`. Without it you get a GatedRepoError 401.
  2. SAM3 cannot run on CPU. `sam3/model/position_encoding.py` hardcodes `device="cuda"`
     in PositionEmbeddingSine, so it raises "Found no NVIDIA driver" on a CPU box regardless
     of --device. Run the SAM3 arm on Colab. SAM2 runs fine on CPU.

Area is the m2 -> kW -> $ input, so a 1.8x mask blowout is a 1.8x dollar error. Treat
`roofgrab` as the headline number, not IoU.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.detect.rfdetr_infer import choose_mask, shrink_box  # noqa: E402

# (name, box scale, x-shift as fraction of box width, y-shift as fraction of box height)
# scale 1.5 == detector box 50% larger than the true array.
PERTURBATIONS = [
    ("shrink15", 0.85, 0.00, 0.00),
    ("exact", 1.00, 0.00, 0.00),
    ("shift15", 1.00, 0.15, 0.15),
    ("dilate10", 1.10, 0.00, 0.00),
    ("dilate25", 1.25, 0.00, 0.00),
    ("dilate25_shift15", 1.25, 0.15, 0.15),
    ("dilate50", 1.50, 0.00, 0.00),
]


def load_predictor(model: str, device: str):
    """Return an object exposing set_image(np.ndarray) and predict(box=..., ...)."""
    if model == "sam2":
        from sam2.sam2_image_predictor import SAM2ImagePredictor
        return SAM2ImagePredictor.from_pretrained("facebook/sam2-hiera-large", device=device)
    if model == "sam3":
        # Weights live in a GATED repo. Request access at https://huggingface.co/facebook/sam3
        # (gated: manual -- Meta approves by hand), then `huggingface-cli login`.
        # SAM3's box/point prompting lives on the SAM1-task interactive predictor, which is only
        # built when enable_inst_interactivity=True. It exposes the same set_image/predict
        # contract as SAM2, which is what makes this an apples-to-apples A/B.
        from sam3.model_builder import build_sam3_image_model
        sam3_model = build_sam3_image_model(device=device, enable_inst_interactivity=True)
        predictor = getattr(sam3_model, "inst_interactive_predictor", None)
        if predictor is None:
            raise RuntimeError("SAM3 built without an interactive predictor -- "
                               "enable_inst_interactivity did not take effect")
        return predictor
    raise ValueError(f"unknown model: {model}")


def read_polygons(path: Path) -> list[np.ndarray]:
    """YOLO-seg polygon labels -> list of (N,2) arrays in normalized coords."""
    out: list[np.ndarray] = []
    if not path.exists():
        return out
    for line in path.read_text().strip().splitlines():
        parts = line.split()
        if len(parts) < 7:  # class + >=3 xy pairs
            continue
        coords = np.array([float(v) for v in parts[1:]], dtype=np.float64)
        xs, ys = coords[0::2], coords[1::2]
        if len(xs) >= 3:
            out.append(np.column_stack([xs, ys]))
    return out


def min_area_rect_px(mask: np.ndarray) -> float:
    """Production area recipe: largest contour -> minAreaRect -> px^2."""
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0
    (w, h) = cv2.minAreaRect(max(contours, key=cv2.contourArea))[1]
    return float(w * h)


def render_overlay(out_path: Path, image: np.ndarray, gt_poly_px: np.ndarray,
                   panels: list[tuple[str, np.ndarray, np.ndarray, float, float]]) -> None:
    """One PNG per array: GT polygon vs SAM mask, for each rendered prompt box.

    panels: (label, mask, prompt_box, iou, area_vs_gt). Green = your label, red = SAM's mask,
    yellow dashed = the box SAM was prompted with.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPolygon, Rectangle

    H, W = image.shape[:2]
    x0, y0 = gt_poly_px[:, 0].min(), gt_poly_px[:, 1].min()
    x1, y1 = gt_poly_px[:, 0].max(), gt_poly_px[:, 1].max()
    pad = max(24.0, 0.6 * max(x1 - x0, y1 - y0))  # context around the array
    cx0, cy0 = int(max(0, x0 - pad)), int(max(0, y0 - pad))
    cx1, cy1 = int(min(W, x1 + pad)), int(min(H, y1 + pad))

    fig, axes = plt.subplots(1, len(panels), figsize=(3.6 * len(panels), 4.0))
    if len(panels) == 1:
        axes = [axes]
    for ax, (label, mask, pbox, iou, area_ratio) in zip(axes, panels):
        ax.imshow(image[cy0:cy1, cx0:cx1])
        red = np.zeros((cy1 - cy0, cx1 - cx0, 4))
        red[..., 0] = 1.0
        red[..., 3] = mask[cy0:cy1, cx0:cx1] * 0.45
        ax.imshow(red)
        ax.add_patch(MplPolygon(gt_poly_px - [cx0, cy0], closed=True, fill=False,
                                edgecolor="#00ff66", linewidth=1.8))
        ax.add_patch(Rectangle((pbox[0] - cx0, pbox[1] - cy0), pbox[2] - pbox[0], pbox[3] - pbox[1],
                               fill=False, edgecolor="#ffd400", linewidth=1.2, linestyle="--"))
        ax.set_title(f"{label}\nIoU {iou:.2f} | area {area_ratio:.2f}x GT", fontsize=9)
        ax.axis("off")
    fig.suptitle(out_path.stem, fontsize=8, y=0.02)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def ring_points(box: np.ndarray, dilate: float, w: int, h: int
                ) -> tuple[np.ndarray, np.ndarray]:
    """Background points on a ring just outside the box, plus one foreground point at its centre.

    A different mechanism from shrinking the prompt: shrinking constrains SAM *geometrically* and
    is only as good as the box, whereas a negative point is a semantic assertion -- "this roof
    pixel is not the object" -- evaluated against the image. That is the textbook remedy for a
    mask leaking onto the surface an object sits on, and it degrades gracefully when the box is
    wrong, which is exactly where clip-and-select failed (see --mask-containment).
    """
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = (x1 - x0) * dilate / 2, (y1 - y0) * dilate / 2
    ring = [(cx, cy - hh), (cx, cy + hh), (cx - hw, cy), (cx + hw, cy),
            (cx - hw, cy - hh), (cx + hw, cy - hh), (cx - hw, cy + hh), (cx + hw, cy + hh)]
    pts = [(cx, cy)] + [(min(max(px, 0), w - 1), min(max(py, 0), h - 1)) for px, py in ring]
    labels = np.array([1] + [0] * len(ring), dtype=np.int32)
    return np.array(pts, dtype=np.float32), labels


def perturb_box(box: np.ndarray, scale: float, dx: float, dy: float,
                w: int, h: int) -> np.ndarray:
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2 + dx * bw, (y0 + y1) / 2 + dy * bh
    hw, hh = bw * scale / 2, bh * scale / 2
    return np.array([max(0, cx - hw), max(0, cy - hh), min(w - 1, cx + hw), min(h - 1, cy + hh)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=["sam2", "sam3"], default="sam2")
    ap.add_argument("--dataset", type=Path, default=REPO / "data/yolo/scc21")
    ap.add_argument("--split", default="val")
    ap.add_argument("--n-arrays", type=int, default=60)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--out-json", type=Path, default=None)
    ap.add_argument("--render-dir", type=Path, default=None,
                    help="write one overlay PNG per array (GT polygon vs SAM mask)")
    ap.add_argument("--render-boxes", nargs="+", default=["shrink15", "exact", "dilate50"],
                    help="which prompt-box variants to draw side by side")
    ap.add_argument("--neg-points", action="store_true",
                    help="also score box + BACKGROUND points on a ring just outside the box, "
                         "emitting a <variant>+negpts row. Semantic rather than geometric: tells "
                         "SAM the roof is not the object, independent of box accuracy.")
    ap.add_argument("--neg-ring", type=float, default=1.35,
                    help="ring radius as a multiple of the prompt box")
    ap.add_argument("--containment", action="store_true",
                    help="also score the production containment path (multimask + clip to the "
                         "detector box + centre blob + area-fit selection) on the same arrays, "
                         "emitting a <variant>+contain row beside each baseline row")
    ap.add_argument("--box-shrink", type=float, default=0.85)
    ap.add_argument("--mask-window", type=float, default=1.25)
    ap.add_argument("--max-area-ratio", type=float, default=2.0)
    args = ap.parse_args(argv)

    img_dir = args.dataset / "images" / args.split
    lbl_dir = args.dataset / "labels" / args.split
    if not img_dir.is_dir():
        logger.error("no such split: %s", img_dir)
        return 1

    logger.info("loading %s on %s", args.model, args.device)
    predictor = load_predictor(args.model, args.device)

    t_set = t_pred = 0.0
    n_set = n_pred = 0
    records: list[dict] = []

    for img_path in sorted(img_dir.iterdir()):
        if len(records) >= args.n_arrays:
            break
        if img_path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        polys = read_polygons(lbl_dir / f"{img_path.stem}.txt")
        if not polys:
            continue

        image = np.array(Image.open(img_path).convert("RGB"))
        H, W = image.shape[:2]
        t = time.time()
        predictor.set_image(image)
        t_set += time.time() - t
        n_set += 1

        for poly in polys:
            if len(records) >= args.n_arrays:
                break
            pk = poly * np.array([W, H])
            box = np.array([pk[:, 0].min(), pk[:, 1].min(), pk[:, 0].max(), pk[:, 1].max()])
            if (box[2] - box[0]) * (box[3] - box[1]) < 16:
                continue
            gt = np.zeros((H, W), np.uint8)
            cv2.fillPoly(gt, [pk.astype(np.int32)], 1)
            gt_area = float(gt.sum())
            if gt_area < 9:
                continue

            rec = {"image": img_path.name, "gt_area_px": gt_area}

            # No-SAM control: the exact box, used directly as the polygon.
            ctrl = np.zeros((H, W), np.uint8)
            ctrl[int(box[1]):int(box[3]) + 1, int(box[0]):int(box[2]) + 1] = 1
            inter, union = int((ctrl & gt).sum()), int((ctrl | gt).sum())
            rec["box_only"] = {"iou": inter / union if union else 0.0,
                               "area_vs_gt": float(ctrl.sum()) / gt_area,
                               "area_err_rect_pct": abs(min_area_rect_px(ctrl) - gt_area) / gt_area * 100}

            panels = []
            for name, scale, dx, dy in PERTURBATIONS:
                pbox = perturb_box(box, scale, dx, dy, W, H)
                t = time.time()
                with torch.inference_mode():
                    masks, _, _ = predictor.predict(box=pbox, multimask_output=False)
                t_pred += time.time() - t
                n_pred += 1
                m = masks[0].astype(np.uint8)
                inter, union = int((m & gt).sum()), int((m | gt).sum())
                iou = inter / union if union else 0.0
                area_ratio = float(m.sum()) / gt_area
                rec[name] = {"iou": iou, "area_vs_gt": area_ratio,
                             "area_err_rect_pct": abs(min_area_rect_px(m) - gt_area) / gt_area * 100}
                if args.render_dir and name in args.render_boxes:
                    panels.append((name, m, pbox, iou, area_ratio))

                if args.neg_points:
                    # Same perturbed box, plus background points on a ring just outside it.
                    pc, pl = ring_points(pbox, args.neg_ring, W, H)
                    with torch.inference_mode():
                        nm, _, _ = predictor.predict(point_coords=pc, point_labels=pl,
                                                     box=pbox, multimask_output=False)
                    n_pred += 1
                    nmask = nm[0].astype(np.uint8)
                    ni, nu = int((nmask & gt).sum()), int((nmask | gt).sum())
                    rec[f"{name}+negpts"] = {
                        "iou": ni / nu if nu else 0.0,
                        "area_vs_gt": float(nmask.sum()) / gt_area,
                        "area_err_rect_pct": abs(min_area_rect_px(nmask) - gt_area) / gt_area * 100}

                if args.containment:
                    # A/B the production containment path on the SAME array and the SAME
                    # perturbed box, so any difference is the logic and not the sample.
                    # `pbox` stands in for the detector box: production shrinks it before
                    # prompting and clips the result to it dilated by --mask-window.
                    with torch.inference_mode():
                        cm, cs, _ = predictor.predict(
                            box=shrink_box(pbox, args.box_shrink, W, H), multimask_output=True)
                    n_pred += 1
                    chosen, _ = choose_mask(cm, cs, pbox, args.mask_window, args.max_area_ratio)
                    cmask = (chosen if chosen is not None
                             else np.zeros((H, W), np.uint8)).astype(np.uint8)
                    ci, cu = int((cmask & gt).sum()), int((cmask | gt).sum())
                    rec[f"{name}+contain"] = {
                        "iou": ci / cu if cu else 0.0,
                        "area_vs_gt": float(cmask.sum()) / gt_area,
                        "area_err_rect_pct": abs(min_area_rect_px(cmask) - gt_area) / gt_area * 100}

            if panels:
                # Filename leads with exact-box IoU so `ls` sorts worst cases first.
                key = rec.get("exact", {}).get("iou", 0.0)
                render_overlay(
                    args.render_dir / f"iou{key:.2f}_{img_path.stem}_{len(records):03d}.png",
                    image, pk, panels)
            records.append(rec)

    if not records:
        logger.error("no arrays evaluated")
        return 1

    strategies = ["box_only"]
    for p in PERTURBATIONS:
        strategies.append(p[0])
        if args.neg_points:
            strategies.append(f"{p[0]}+negpts")
        if args.containment:
            strategies.append(f"{p[0]}+contain")
    print(f"\n{args.model} | {args.split} | n={len(records)} arrays | device={args.device}\n")
    print(f'{"prompt box":22s} {"medIoU":>7s} {"meanIoU":>8s} {"medArea/GT":>11s} '
          f'{"roofgrab>2xGT":>14s} {"IoU<0.3":>8s}')
    summary = {}
    for s in strategies:
        iou = np.array([r[s]["iou"] for r in records])
        avg = np.array([r[s]["area_vs_gt"] for r in records])
        summary[s] = {"median_iou": float(np.median(iou)), "mean_iou": float(iou.mean()),
                      "median_area_vs_gt": float(np.median(avg)),
                      "roofgrab_rate": float((avg > 2.0).mean()),
                      "iou_below_0.3_rate": float((iou < 0.3).mean())}
        label = f"{s}{' (no SAM)' if s == 'box_only' else ''}"
        print(f'{label:22s} {np.median(iou):7.3f} {iou.mean():8.3f} {np.median(avg):11.2f} '
              f'{(avg > 2.0).mean() * 100:13.1f}% {(iou < 0.3).mean() * 100:7.1f}%')

    per_chip, per_box = t_set / max(1, n_set), t_pred / max(1, n_pred)
    print(f"\ncost on {args.device}: set_image {per_chip:.2f} s/chip | predict {per_box:.3f} s/box")
    print("  cost is per CHIP, not per box -- reuse one set_image across a chip's boxes, "
          "and skip chips with zero detections")
    chips, boxes = 996, 3375  # full 249-tile SCC AOI at 4 chips/tile
    print(f"  full AOI ({chips} chips, ~{boxes} boxes) ~= "
          f"{(chips * per_chip + boxes * per_box) / 3600:.1f} h")

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(
            {"model": args.model, "split": args.split, "n": len(records),
             "device": args.device, "summary": summary,
             "cost": {"set_image_s_per_chip": per_chip, "predict_s_per_box": per_box},
             "records": records}, indent=1))
        logger.info("wrote %s", args.out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
