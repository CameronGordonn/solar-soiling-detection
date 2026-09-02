"""Pack the scc21 chips into the zip layout notebooks/rfdetr_pipeline.ipynb already expects.

That notebook's staging cell walks the archive for directories named train/valid/test, each holding
`images/` and `labels/`, and remaps valid->val locally. Emitting exactly that shape means the
notebook needs no change to its extraction logic — only the zip path.

Deliberately stores PNG uncompressed-as-is (ZIP_STORED): the chips are already PNG, so DEFLATE buys
~nothing and costs minutes on a 435 MB archive.

  PYTHONPATH=. conda run -n solar-soiling python scripts/data/pack_scc21_for_colab.py
"""

from pathlib import Path
import argparse
import json
import logging
import zipfile

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SPLIT_OUT = {"train": "train", "val": "valid", "test": "test"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--src", default="data/yolo/scc21", type=Path)
    p.add_argument("--out", default="outputs/scc21_colab.zip", type=Path)
    p.add_argument("--deflate", action="store_true", help="compress (slow, ~no gain on PNG)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    mode = zipfile.ZIP_DEFLATED if args.deflate else zipfile.ZIP_STORED

    counts = {}
    with zipfile.ZipFile(args.out, "w", mode) as z:
        for split, out_split in SPLIT_OUT.items():
            img_dir = args.src / "images" / split
            lbl_dir = args.src / "labels" / split
            if not img_dir.is_dir():
                logger.warning(f"{img_dir} missing — skipping")
                continue
            n_i = n_l = 0
            for img in sorted(img_dir.glob("*.png")):
                z.write(img, f"{out_split}/images/{img.name}")
                n_i += 1
                lbl = lbl_dir / f"{img.stem}.txt"
                # Chips with no arrays carry an empty .txt on purpose — a background image is a
                # real negative, and dropping it would bias precision.
                z.writestr(f"{out_split}/labels/{img.stem}.txt",
                           lbl.read_text() if lbl.exists() else "")
                n_l += 1
            counts[out_split] = (n_i, n_l)
            logger.info(f"{out_split}: {n_i} images, {n_l} labels")

        idx = args.src / "tile_index_chips.json"
        if idx.exists():
            z.write(idx, "tile_index_chips.json")
        qa = args.src / "import_qa.json"
        if qa.exists():
            z.write(qa, "import_qa.json")
        z.writestr("PROVENANCE.json", json.dumps({
            "source": str(args.src), "vintage": "scc_2025", "gsd_ground_m": 0.208,
            "chip_px": 640, "note": "cropped at native resolution, never resized",
            "splits": {k: {"images": v[0], "labels": v[1]} for k, v in counts.items()},
        }, indent=2))

    mb = args.out.stat().st_size / 1e6
    logger.info(f"wrote {args.out} ({mb:.0f} MB)")
    return counts


if __name__ == "__main__":
    main()
