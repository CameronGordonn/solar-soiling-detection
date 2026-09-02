"""Upload the packaged 21cm tile set to Roboflow as per-annotator batches.

Consumes build_21cm_labelset.py output. Everything lands in ONE project; each annotator gets half of
train, half of val and half of test, as a named batch per split so nobody touches the same tile:

    w<week>-<annotator>-val      blind — no seed annotations (measurement instrument)
    w<week>-<annotator>-test     blind
    w<week>-<annotator>-train    2022 polygons attached as predictions to confirm/correct/extend

Refuses to run if qa_report.json has any failure: a tile that failed integrity must never reach an
annotator, because hours of labeling on a degraded raster is unrecoverable work.

IMAGE INTEGRITY: we upload lossless PNG at native ~0.21 m/px. Roboflow stores the original and
annotates against it, but a generated *version* re-encodes to JPEG. We therefore treat Roboflow as an
annotation store only — the export step pairs Roboflow's label txt with our local PNGs and throws
Roboflow's images away. See docs/LABELING_SPRINT_21CM.md §5.

Safe to re-run: Roboflow deduplicates identical images within a project, so re-sending tiles already
uploaded is a no-op rather than a second copy (verified 2026-07-31 — 249 uploads over a 4-tile smoke
test left 249 distinct images, not 253).

AUTH: ROBOFLOW_API_KEY in repo-root .env or the environment.

  PYTHONPATH=. conda run -n solar-soiling python scripts/data/upload_21cm_to_roboflow.py --dry-run
  PYTHONPATH=. conda run -n solar-soiling python scripts/data/upload_21cm_to_roboflow.py --limit 4
  PYTHONPATH=. conda run -n solar-soiling python scripts/data/upload_21cm_to_roboflow.py
"""

from pathlib import Path
import argparse
import csv
import json
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE = "solarsoilingml"
# Separate from solar_arrays_scc_21cm: that project holds 50m permit-seed chips (confirm-not-draw,
# panel-centred). Mixing 50m chips and 245m tiles in one export gives an unusable eval set.
DEFAULT_PROJECT = "solar_arrays_scc21_tiles"


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--labelset", default="data/interim/scc21_labelset", type=Path)
    p.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--batch-prefix", default=None, help="default: w<isoweek>")
    p.add_argument("--only", default="", help="comma-separated annotator filter")
    p.add_argument("--limit", type=int, default=0, help="cap uploads (use 4 for a first check)")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def load_plan(labelset: Path, only):
    qa = json.loads((labelset / "qa_report.json").read_text())
    if qa.get("n_failed"):
        raise SystemExit(f"qa_report.json lists {qa['n_failed']} failed tile(s) — fix before upload: "
                         f"{list(qa['failures'])[:5]}")
    rows = list(csv.DictReader((labelset / "assignment.csv").open()))
    if only:
        keep = {a.strip() for a in only.split(",")}
        rows = [r for r in rows if r["annotator"] in keep]
    return rows


def main(argv=None):
    args = parse_args(argv)
    import pandas as pd

    rows = load_plan(args.labelset, args.only)
    prefix = args.batch_prefix or f"w{pd.Timestamp.today().isocalendar().week}"
    if args.limit:
        rows = rows[:args.limit]

    plan = []
    for r in rows:
        png = args.labelset / "images" / f"{r['tile']}.png"
        seed = args.labelset / "seeds" / f"{r['tile']}.txt"
        if not png.exists():
            logger.warning(f"{r['tile']}: png missing — skipping")
            continue
        base = [f"origsplit:{r['split']}", "vintage:scc_2025", "gsd:21cm", "source:scc_tile"]
        use_seed = (r["mode"] == "seeded") and seed.exists()
        plan.append((png, seed if use_seed else None, r, args.project,
                     f"{prefix}-{r['annotator']}-{r['split']}",
                     [f"annotator:{r['annotator']}", f"mode:{r['mode']}"] + base))

    dest = {}
    for _, _, _, proj, batch, _ in plan:
        dest[(proj, batch)] = dest.get((proj, batch), 0) + 1
    logger.info(f"{'DRY-RUN: ' if args.dry_run else ''}{len(plan)} upload(s) -> {args.workspace}")
    for (proj, batch), n in sorted(dest.items()):
        logger.info(f"  {proj} / {batch}: {n} tiles")

    if args.dry_run:
        for png, seed, r, proj, batch, tags in plan[:8]:
            logger.info(f"  {png.name:20s} {proj}/{batch:22s} "
                        f"seed={'yes' if seed else 'NONE(blind)'} tags={tags[:2]}")
        if len(plan) > 8:
            logger.info(f"  ... +{len(plan) - 8} more")
        logger.info("dry-run OK — drop --dry-run to upload (start with --limit 4).")
        return

    _load_dotenv()
    key = os.environ.get("ROBOFLOW_API_KEY")
    if not key:
        raise SystemExit("ROBOFLOW_API_KEY not set — add it to repo-root .env or export it.")
    try:
        from roboflow import Roboflow
    except ImportError:
        raise SystemExit("pip install roboflow")

    workspace = Roboflow(api_key=key).workspace(args.workspace)
    projects = {}
    ok, fail = 0, []
    for png, seed, r, proj_name, batch, tags in plan:
        if proj_name not in projects:
            projects[proj_name] = workspace.project(proj_name)
        project = projects[proj_name]
        kwargs = dict(image_path=str(png), batch_name=batch, tag_names=tags,
                      split=r["split"], num_retries=2)
        if seed is not None:
            kwargs.update(annotation_path=str(seed), is_prediction=True)
        try:
            project.single_upload(**kwargs)
            ok += 1
        except Exception as e:
            logger.warning(f"{png.name}: {type(e).__name__}: {e} — retrying image-only")
            try:
                project.single_upload(image_path=str(png), batch_name=batch,
                                      tag_names=tags + ["seed_upload_failed"], split=r["split"])
                ok += 1
            except Exception as e2:
                fail.append((png.name, str(e2)))
    logger.info(f"done: {ok}/{len(plan)} uploaded")
    if fail:
        logger.error(f"{len(fail)} failed: {fail[:5]}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
