"""Build a GSD-ablation copy of the scc21 chip set.

WHY
---
The 21cm detector is trained on Santa Cruz County imagery. That layer exists for Santa
Cruz. NAIP (~60cm) is the only nationwide layer, so the question that decides whether
SolarSoiled is a one-county product or a California product is:

    how much of the 21cm win survives at 60cm?

This script answers the *isolated* form of that question. It degrades each chip to a
target ground GSD and resamples back to the original pixel canvas, so the ONLY variable
that changes is information content -- framing, footprint, labels, and network input size
all stay fixed. Labels are copied verbatim: YOLO polygon coords are normalized, so they
are invariant to this operation.

Degradation uses INTER_AREA (a box filter, i.e. what a coarser sensor actually does) on
the way down and INTER_LINEAR on the way back up.

WHAT THIS IS NOT
----------------
This is an UPPER BOUND on real 60cm performance, not a prediction of it. It changes
resolution only. Real NAIP also differs in radiometry, sun angle, season, compression,
and acquisition year. Treat a pass here as "resolution is not the blocker"; treat a
failure here as decisive -- if the model breaks on clean simulated 60cm, it will not do
better on the real thing.

The honest end-to-end test (run the model on real NAIP tiles) is confounded today: our
NAIP tiles are 2022 and the 21cm labels are 2025, so any 2023-2025 install scores as a
false positive. Run this first; do that one only with per-vintage labels.

USAGE
-----
    PYTHONPATH=. conda run -n solar-soiling python scripts/data/build_gsd_ablation.py
    PYTHONPATH=. conda run -n solar-soiling python scripts/data/build_gsd_ablation.py \
        --gsd 0.30 0.60 1.00 --splits val test

Then zip each output dir, upload to Drive, and score it in
`notebooks/w1_rematch_scc21.ipynb` with the SAME evaluator cell -- pointing NAIP_DIR at
the degraded set and changing nothing else. One row per GSD gives you the decay curve.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SRC = REPO / "data/yolo/scc21"
NATIVE_GSD_M = 0.208  # ground GSD of the SCC 2025 county imagery (see LABELING_SPRINT_21CM.md)


def degrade(img: np.ndarray, native_gsd: float, target_gsd: float) -> np.ndarray:
    """Resample `img` to `target_gsd` ground sampling, then back to its original canvas."""
    if target_gsd <= native_gsd:
        raise ValueError(f"target GSD {target_gsd} must be coarser than native {native_gsd}")
    h, w = img.shape[:2]
    scale = native_gsd / target_gsd
    small_w, small_h = max(1, round(w * scale)), max(1, round(h * scale))
    small = cv2.resize(img, (small_w, small_h), interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def build(src: Path, dst: Path, target_gsd: float, splits: list[str], native_gsd: float) -> dict:
    if dst.exists():
        shutil.rmtree(dst)
    stats = {"target_gsd_m": target_gsd, "native_gsd_m": native_gsd, "splits": {}}

    for split in splits:
        src_img, src_lbl = src / "images" / split, src / "labels" / split
        if not src_img.is_dir():
            logger.warning("no such split, skipping: %s", src_img)
            continue
        dst_img, dst_lbl = dst / "images" / split, dst / "labels" / split
        dst_img.mkdir(parents=True, exist_ok=True)
        dst_lbl.mkdir(parents=True, exist_ok=True)

        n_img = n_lbl = 0
        for p in sorted(src_img.iterdir()):
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is None:
                logger.error("unreadable image: %s", p)
                continue
            # PNG out regardless of input suffix -- never re-encode a degraded chip as JPEG.
            cv2.imwrite(str(dst_img / f"{p.stem}.png"), degrade(img, native_gsd, target_gsd))
            n_img += 1
            lbl = src_lbl / f"{p.stem}.txt"
            if lbl.exists():
                shutil.copy2(lbl, dst_lbl / lbl.name)
                n_lbl += 1

        stats["splits"][split] = {"images": n_img, "label_files": n_lbl}
        logger.info("%s @ %.2fm: %d images, %d label files", split, target_gsd, n_img, n_lbl)

    # Carry the COCO annotations through untouched -- RF-DETR reads these, and normalized
    # geometry is unchanged by the degradation.
    src_ann = src / "annotations"
    if src_ann.is_dir():
        shutil.copytree(src_ann, dst / "annotations")
        stats["annotations_copied"] = True
    for extra in ("data.yaml", "tile_index_chips.json"):
        if (src / extra).exists():
            shutil.copy2(src / extra, dst / extra)

    (dst / "gsd_ablation.json").write_text(json.dumps(stats, indent=1))
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out-root", type=Path, default=REPO / "data/yolo")
    ap.add_argument("--gsd", type=float, nargs="+", default=[0.30, 0.60],
                    help="target ground GSDs in metres (default: 0.30 0.60)")
    ap.add_argument("--splits", nargs="+", default=["val", "test"],
                    help="train is pointless here -- this is an eval-only ablation")
    ap.add_argument("--native-gsd", type=float, default=NATIVE_GSD_M)
    args = ap.parse_args(argv)

    if not args.src.is_dir():
        logger.error("source dataset not found: %s", args.src)
        return 1

    summary = {}
    for gsd in args.gsd:
        dst = args.out_root / f"scc21_gsd{int(round(gsd * 100)):03d}"
        logger.info("building %s (native %.3fm -> %.2fm)", dst.name, args.native_gsd, gsd)
        summary[dst.name] = build(args.src, dst, gsd, args.splits, args.native_gsd)
        logger.info("  -> %s", dst)

    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
