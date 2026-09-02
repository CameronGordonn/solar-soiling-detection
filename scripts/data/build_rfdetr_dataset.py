"""Re-lay the scc21 chips into the Roboflow-COCO layout RF-DETR expects.

`import_21cm_from_roboflow.py` already emits COCO segmentation from the same polygons that feed the
YOLO txt — the geometry is identical and this script does not touch it. What differs is packaging:

    ours            data/yolo/scc21/images/{train,val,test}/     annotations/instances_<split>.json
    RF-DETR         <out>/{train,valid,test}/<images>            <split>/_annotations.coco.json

Two conventions matter beyond the directory shuffle:
  * the split is called **valid**, not val;
  * a Roboflow COCO export carries a dummy supercategory at id 0 with real classes from id 1, and
    RF-DETR sizes its classification head off the category list. Emitting a bare single category at
    id 1 gives an off-by-one num_classes, so we reproduce the dummy.

Images are hardlinked by default — 435 MB of chips, and a hardlink costs nothing on disk while still
zipping as a real file for the Colab upload. Falls back to copying across filesystems.

  PYTHONPATH=. conda run -n solar-soiling python scripts/data/build_rfdetr_dataset.py
  PYTHONPATH=. conda run -n solar-soiling python scripts/data/build_rfdetr_dataset.py --copy
"""

from pathlib import Path
import argparse
import json
import logging
import os
import shutil

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SPLIT_OUT = {"train": "train", "val": "valid", "test": "test"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--src", default="data/yolo/scc21", type=Path)
    p.add_argument("--out", default="data/rfdetr/scc21", type=Path)
    p.add_argument("--copy", action="store_true", help="copy instead of hardlinking")
    return p.parse_args(argv)


def place(src: Path, dst: Path, copy: bool) -> None:
    if dst.exists():
        dst.unlink()
    if copy:
        shutil.copy2(src, dst)
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def main(argv=None):
    args = parse_args(argv)
    totals = {}
    for split, out_split in SPLIT_OUT.items():
        ann_path = args.src / "annotations" / f"instances_{split}.json"
        if not ann_path.exists():
            logger.warning(f"{ann_path} missing — skipping {split}")
            continue
        coco = json.loads(ann_path.read_text())

        dst_dir = args.out / out_split
        dst_dir.mkdir(parents=True, exist_ok=True)

        missing = []
        for im in coco["images"]:
            src_img = args.src / "images" / split / im["file_name"]
            if not src_img.exists():
                missing.append(im["file_name"])
                continue
            place(src_img, dst_dir / im["file_name"], args.copy)
        if missing:
            raise SystemExit(f"{split}: {len(missing)} image(s) referenced by COCO but not on disk: "
                             f"{missing[:5]}")

        # Roboflow-style category list: dummy supercategory at 0, real classes from 1. Annotations
        # already use category_id=1, so only the header changes.
        coco["categories"] = [
            {"id": 0, "name": "solar-arrays", "supercategory": "none"},
            {"id": 1, "name": "solar_array", "supercategory": "solar-arrays"},
        ]
        coco["info"] = {**coco.get("info", {}), "split": out_split}
        (dst_dir / "_annotations.coco.json").write_text(json.dumps(coco))
        totals[out_split] = (len(coco["images"]), len(coco["annotations"]))
        logger.info(f"{out_split}: {len(coco['images'])} images, {len(coco['annotations'])} annotations")

    logger.info(f"wrote {args.out}")
    for s, (ni, na) in totals.items():
        logger.info(f"  {s}/  {ni} images  {na} annotations  + _annotations.coco.json")
    return totals


if __name__ == "__main__":
    main()
