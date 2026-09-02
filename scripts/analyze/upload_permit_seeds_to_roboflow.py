"""Push permit-seed chips (21cm image + kW-sized seed box) into Roboflow for confirm-not-draw review.

Step 4 of the permit-driven labeling plan. Uploads each chip from fetch_permit_chips.py to a named
Roboflow batch with the seed as a starting annotation, tagged with permit provenance. In Roboflow you
then confirm / nudge / reject a pre-drawn box on a crisp chip instead of hunting the address and
drawing from a blank tile.

PROVENANCE / SAFETY (mirrors generate_permit_seeds.py):
  - Every image is tagged `source:permit_seed` + `year:<y>` + `kw:<k>` so seeds never silently enter
    eval, and you can filter them in Roboflow.
  - Seeds are *hints*, not truth — a human confirms each before it exports back into data/yolo/naip.

AUTH: set ROBOFLOW_API_KEY in the environment (do NOT hardcode it). Get it from Roboflow > Settings.

  export ROBOFLOW_API_KEY=...   # your private key
  PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/upload_permit_seeds_to_roboflow.py --dry-run
  # then, for real:
  PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/upload_permit_seeds_to_roboflow.py --limit 5

NOTE: the annotation-ingest format is the one leg that needs a live round-trip to confirm against
your project settings (segmentation vs box). --dry-run validates everything else offline; run --limit 5
first and check the batch in Roboflow before uploading the full queue.
"""

from pathlib import Path
import argparse
import logging
import os
import sys

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE = "solarsoilingml"
DEFAULT_PROJECT = "solar_arrays_scc_21cm"  # 21cm county imagery + permit-seed chips (separate from 60cm set)


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from repo-root .env into os.environ (matches scripts/outreach/*)."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chips-dir", default="data/interim/permit_chips", type=Path)
    p.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--batch-name", default=None, help="Roboflow batch (default: permit-seed-<today>)")
    p.add_argument("--limit", type=int, default=0, help="cap uploads (0 = all); use 5 for a first check")
    p.add_argument("--no-annotation", action="store_true",
                   help="upload images + tags only, no seed box (draw fresh in Roboflow)")
    p.add_argument("--dry-run", action="store_true", help="validate + list actions, no API calls")
    return p.parse_args(argv)


def collect(chips_dir: Path, limit: int):
    man_path = chips_dir / "manifest.csv"
    man = pd.read_csv(man_path) if man_path.exists() else None
    meta = {}
    if man is not None:
        for _, r in man.iterrows():
            meta[Path(str(r["chip"])).stem] = r
    items = []
    for png in sorted(chips_dir.glob("*.png")):
        stem = png.stem
        txt = chips_dir / f"{stem}.txt"
        items.append((png, txt if txt.exists() else None, meta.get(stem)))
    return items[:limit] if limit else items


def tags_for(row):
    if row is None:
        return ["source:permit_seed"]
    t = ["source:permit_seed"]
    if pd.notna(row.get("year")):
        t.append(f"year:{int(row['year'])}")
    if pd.notna(row.get("kw")):
        t.append(f"kw:{row['kw']}")
    if pd.notna(row.get("mount")) and str(row.get("mount")).strip():
        t.append(f"mount:{str(row['mount']).strip()}")
    if row.get("has_parcel") is False:
        t.append("geocode_fallback")  # positioned on scattered point, not parcel — check carefully
    return t


def main(argv=None):
    args = parse_args(argv)
    items = collect(args.chips_dir, args.limit)
    if not items:
        raise SystemExit(f"no chips in {args.chips_dir} — run fetch_permit_chips.py first")
    batch = args.batch_name or f"permit-seed-{pd.Timestamp.today():%Y%m%d}"
    logger.info(f"{'DRY-RUN: ' if args.dry_run else ''}{len(items)} chip(s) -> "
                f"{args.workspace}/{args.project} batch '{batch}'"
                f"{' (images only)' if args.no_annotation else ' with seed annotations'}")

    if args.dry_run:
        for png, txt, row in items[:10]:
            ann = "no-seed" if (args.no_annotation or txt is None) else txt.name
            logger.info(f"  {png.name:22s} ann={ann:16s} tags={tags_for(row)}")
        if len(items) > 10:
            logger.info(f"  ... +{len(items) - 10} more")
        logger.info("dry-run OK — set ROBOFLOW_API_KEY and drop --dry-run to upload (start with --limit 5).")
        return

    _load_dotenv()
    key = os.environ.get("ROBOFLOW_API_KEY")
    if not key:
        raise SystemExit("ROBOFLOW_API_KEY not set — add it to repo-root .env or export it.")
    try:
        from roboflow import Roboflow
    except ImportError:
        raise SystemExit("pip install roboflow")

    project = Roboflow(api_key=key).workspace(args.workspace).project(args.project)
    ok = 0
    for png, txt, row in items:
        kwargs = dict(image_path=str(png), batch_name=batch, tag_names=tags_for(row),
                      split="train", num_retries=2)
        if txt is not None and not args.no_annotation:
            # seed uploaded as a model prediction the reviewer confirms/edits (is_prediction=True)
            kwargs.update(annotation_path=str(txt), is_prediction=True)
        try:
            project.single_upload(**kwargs)
            ok += 1
        except Exception as e:
            logger.warning(f"{png.name}: upload failed ({type(e).__name__}: {e}); "
                           "retrying image-only")
            try:
                project.single_upload(image_path=str(png), batch_name=batch,
                                      tag_names=tags_for(row), split="train")
                ok += 1
            except Exception as e2:
                logger.error(f"{png.name}: image-only also failed: {e2}")
    logger.info(f"done: {ok}/{len(items)} uploaded to batch '{batch}'. "
                "Open Roboflow > Annotate to confirm the seeds.")


if __name__ == "__main__":
    main()
