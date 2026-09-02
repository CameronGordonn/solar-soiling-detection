#!/usr/bin/env python3
"""Run inference with a trained YOLOv11 segmentation model. Supports SAHI sliced inference."""

from pathlib import Path
import argparse
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.train_utils import resolve_project_dir, resolve_weights, select_device
from solarsoiled.manifest import write_manifest

def _load_yolo():
    """Import ultralytics lazily and return the YOLO class.

    NOT a module-level import, even though this file is the ultralytics lane:
    src/solarsoiled/cli.py imports this module at module scope, so a top-level
    `from ultralytics import YOLO` here put AGPL-3.0 code into every process that
    touched the CLI -- including the deployed API. It was inside a `try:` block,
    which made it read as lazy to a grep for unindented imports; it was not.
    """
    try:
        from ultralytics import YOLO
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            'ultralytics is in the `legacy` extra (AGPL-3.0, eval-only). '
            'Run: pip install -e ".[legacy]"'
        ) from exc
    return YOLO


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run YOLO segmentation inference")
    parser.add_argument("--weights", type=str, default=None)
    parser.add_argument("--source", type=str, default=None)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.7)
    parser.add_argument("--max-det", type=int, default=300)
    parser.add_argument("--save-visuals", dest="save_visuals", action="store_true")
    parser.add_argument("--no-save-visuals", dest="save_visuals", action="store_false")
    parser.set_defaults(save_visuals=True)
    parser.add_argument("--project", type=str, default="runs/segment")
    parser.add_argument("--name", type=str, default="predict")
    parser.add_argument("--sahi", action="store_true", default=False,
                        help="Run SAHI as a supplementary second pass after whole-tile inference to recover small panels (pip install sahi)")
    parser.add_argument("--sahi-slice-size", type=int, default=640)
    parser.add_argument("--sahi-overlap", type=float, default=0.2)
    parser.add_argument("--sahi-conf", type=float, default=0.10,
                        help="Confidence threshold for SAHI second pass (lower than primary --conf to surface small panels)")
    parser.add_argument("--sahi-merge-iou", type=float, default=0.30,
                        help="Bbox IoU threshold below which a SAHI detection is considered new and added")
    return parser.parse_args(argv)


def run_standard_inference(model, input_folder: Path, args, device: str) -> None:
    print(f"Running inference on: {input_folder}")
    model.predict(
        source=str(input_folder), imgsz=args.imgsz, conf=args.conf, iou=args.iou,
        max_det=args.max_det, device=device, save=args.save_visuals,
        save_txt=True, save_conf=True, project=args.project, name=args.name, verbose=True,
    )


def _bbox_from_yolo_polygon(coords: list[float]) -> tuple[float, float, float, float]:
    """Return (x1, y1, x2, y2) bbox from flat normalized polygon coord list."""
    xs = coords[0::2]
    ys = coords[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_iou(a: tuple, b: tuple) -> float:
    """Axis-aligned bounding box IoU."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


def run_combined_inference(weights_path: Path, input_folder: Path, args, device: str) -> None:
    """Whole-tile inference first, then SAHI as a supplementary second pass.

    SAHI detections are added only when their bbox IoU with every existing
    whole-tile detection is below args.sahi_merge_iou — meaning they represent
    panels the primary pass missed (typically small arrays).
    """
    try:
        from sahi import AutoDetectionModel
        from sahi.predict import get_sliced_prediction
        from PIL import Image as PILImage
    except ImportError as exc:
        raise ImportError("Run: pip install sahi") from exc

    repo_root = Path(__file__).resolve().parents[2]
    output_dir = resolve_project_dir(args.project, repo_root) / args.name
    labels_dir = output_dir / "labels"

    # Step 1 — whole-tile inference
    print("Step 1/2: whole-tile inference")
    run_standard_inference(
        _load_yolo()(str(weights_path), task="segment"), input_folder, args, device
    )

    # Step 2 — SAHI second pass at lower conf to surface small panels
    detection_model = AutoDetectionModel.from_pretrained(
        model_type="ultralytics", model_path=str(weights_path),
        confidence_threshold=args.sahi_conf, device=device,
    )

    image_paths = sorted(list(input_folder.glob("*.png")) + list(input_folder.glob("*.jpg")))
    print(f"Step 2/2: SAHI supplementary pass on {len(image_paths)} images "
          f"(slice={args.sahi_slice_size}px, overlap={args.sahi_overlap}, "
          f"sahi_conf={args.sahi_conf}, merge_iou<{args.sahi_merge_iou})")

    added_total = 0
    for img_path in image_paths:
        label_path = labels_dir / img_path.with_suffix(".txt").name
        img_w, img_h = PILImage.open(img_path).size

        # Load existing whole-tile detections and their bboxes
        existing_lines: list[str] = []
        existing_bboxes: list[tuple] = []
        if label_path.exists():
            for line in label_path.read_text().splitlines():
                parts = line.strip().split()
                if len(parts) >= 7:  # cls + at least 3 points
                    existing_lines.append(line)
                    coords = [float(v) for v in parts[1:]]
                    existing_bboxes.append(_bbox_from_yolo_polygon(coords))

        result = get_sliced_prediction(
            str(img_path), detection_model,
            slice_height=args.sahi_slice_size, slice_width=args.sahi_slice_size,
            overlap_height_ratio=args.sahi_overlap, overlap_width_ratio=args.sahi_overlap,
            postprocess_type="GREEDYNMM", postprocess_match_metric="IOS",
            postprocess_match_threshold=args.iou, verbose=0,
        )

        new_lines: list[str] = []
        for pred in result.object_prediction_list:
            if pred.mask is None or not pred.mask.segmentation:
                continue
            coords = pred.mask.segmentation[0]
            if len(coords) < 6:
                continue
            norm_coords = []
            for i in range(0, len(coords), 2):
                norm_coords.append(coords[i] / img_w)
                norm_coords.append(coords[i + 1] / img_h)
            sahi_bbox = _bbox_from_yolo_polygon(norm_coords)
            if all(_bbox_iou(sahi_bbox, eb) < args.sahi_merge_iou for eb in existing_bboxes):
                pts = " ".join(f"{v:.6f}" for v in norm_coords)
                new_lines.append(f"{pred.category.id} {pts}")
                existing_bboxes.append(sahi_bbox)

        if new_lines:
            added_total += len(new_lines)
            all_lines = existing_lines + new_lines
            label_path.write_text("\n".join(all_lines))

    print(f"SAHI supplementary pass complete — added {added_total} detections across {len(image_paths)} tiles")


def main(argv=None):
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]

    weights_path = resolve_weights(args.weights, repo_root)

    input_folder = (
        Path(args.source).expanduser().resolve() if args.source
        else repo_root / "data" / "tiles"
    )
    if not input_folder.exists():
        raise FileNotFoundError(f"Input folder not found: {input_folder}")

    device = select_device()
    project_dir = resolve_project_dir(args.project, repo_root)

    if args.sahi:
        args.project = str(project_dir)
        run_combined_inference(weights_path, input_folder, args, device)
    else:
        args.project = str(project_dir)
        run_standard_inference(_load_yolo()(str(weights_path), task="segment"), input_folder, args, device)

    output_dir = project_dir / args.name
    print(f"Results saved to: {output_dir}")

    image_paths = sorted(
        list(input_folder.glob("*.png")) + list(input_folder.glob("*.jpg"))
    )
    run_tag = (
        weights_path.parent.parent.name
        if weights_path.parent.name == "weights"
        else weights_path.stem
    )
    write_manifest(
        output_dir,
        stage="stage1_detect",
        model_version=f"stage1-{run_tag}",
        model_weights=weights_path,
        inputs=[str(p) for p in image_paths],
        metrics={
            "imgsz": args.imgsz,
            "conf": args.conf,
            "iou": args.iou,
            "n_images": len(image_paths),
        },
        known_limitations=[
            "NAIP Santa Cruz training distribution; 0.6m GSD",
            "Below 0.70 mAP50 GA bar",
        ],
        extra={"sahi": bool(args.sahi)},
    )


if __name__ == "__main__":
    main()
