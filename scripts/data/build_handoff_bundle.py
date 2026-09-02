"""Assemble the data hand-off bundle: what must leave this machine, and nothing else.

The repo working tree is ~28 GB. Almost none of that needs to survive Cameron's
departure -- but the subset that does is not obvious, and the first attempt at
sizing it got the composition wrong in a way that would have failed silently:
it bundled `data/yolo/scc21` (the training chips) and omitted
`data/interim/scc21_labelset/images`, which is what `eval_tile_f1.py` actually
reads. A restore from that bundle passes `check-data` and then cannot reproduce
the 0.826 gate. Hence this script: the bundle is defined in ONE place, in code,
next to the reason for each entry.

    # what would go, with sizes, no writes
    PYTHONPATH=. python scripts/data/build_handoff_bundle.py

    # manifest with sha256 (what the restore is verified against)
    PYTHONPATH=. python scripts/data/build_handoff_bundle.py --hash

    # hardlinked staging tree, ready for `rclone copy`
    PYTHONPATH=. python scripts/data/build_handoff_bundle.py --hash --stage ~/handoff_staging

The manifest and the per-lane file lists land in `setup/handoff/` and are TRACKED in
git on purpose: a newcomer needs the file lists before they have pulled anything, and
a restore verified against a manifest that shipped with the clone is worth more than
one verified against a manifest that shipped with the data.

Hardlinks, so staging 1.4 GB costs no disk and no time. Falls back to copying
across filesystems. Verify a RESTORE, never an upload -- see DATA.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

# (group, repo-relative path, why it is irreplaceable)
BUNDLE: list[tuple[str, str, str]] = [
    # ── Stage 1: reproduce the GA gate (test F1 0.826) ────────────────────────
    ("stage1-gate", "models/rfdetr_w2_20260807.pth",
     "The gate result is unreproducible without it. Retrainable only on Colab."),
    ("stage1-gate", "data/interim/scc21_labelset/images",
     "The 249 full 21cm tiles eval_tile_f1.py reads (--tiles default). NOT the chips."),
    ("stage1-gate", "data/interim/scc21_labelset/tile_index_21cm.json",
     "CRS + affine for the 21cm tiles. Distinct file from data/interim/tile_index.json (60cm)."),
    ("stage1-gate", "data/interim/scc21_labelset/assignment.csv",
     "Which labeler got which tile; the provenance of the relabel sprint."),
    ("stage1-gate", "data/yolo/scc21/tile_labels",
     "Tile-level GT (val 340 / test 585). The chip-level labels are a different count."),
    ("stage1-gate", "data/interim/tile_index.json",
     "Sacred: the 60cm tile index. Regenerable only by re-tiling the imagery."),

    # ── Stage 1: retrain, not just evaluate ───────────────────────────────────
    ("stage1-train", "data/yolo/scc21/images",
     "976 training chips. Recoverable only from Roboflow, which needs account access."),
    ("stage1-train", "data/yolo/scc21/labels",
     "Chip polygons (3,894)."),
    ("stage1-train", "data/yolo/scc21/annotations",
     "COCO form of the same polygons."),
    ("stage1-train", "data/yolo/scc21/data.yaml", "Split definition."),
    ("stage1-train", "data/yolo/scc21/tile_index_chips.json", "Chip -> tile georeferencing."),
    ("stage1-train", "data/yolo/scc21/import_qa.json", "Import QA record for the rebuild."),
    ("stage1-train", "models/rfdetr_w1_20260806.pth",
     "Superseded, but it is the anchor in the paired W2-vs-W1 bootstrap. Keep to re-run that."),

    # ── PVDAQ fleet: the labels, and the API quota that bought them ───────────
    # This group exists because of what it costs to NOT have it. The irradiance cache
    # is ~3 days of Open-Meteo free-tier DAILY quota; the limit is a hard per-day
    # ceiling, so a fresh clone cannot re-earn 225 cells by waiting an hour, it waits
    # two to three days before it can fit anything at all. Nothing else in this bundle
    # is rate-limited to re-acquire.
    ("pvdaq", ".cache/soiling/pvdaq_irradiance",
     "Open-Meteo hourly irradiance, one parquet per 0.5-deg cell. ~3 days of daily "
     "free-tier quota. Re-fetching is rate-limited, not merely slow."),
    ("pvdaq", ".cache/soiling/pvdaq_sysmeta",
     "PVDAQ per-system metadata. What module_gamma.py resolves the temperature "
     "coefficient from; 1,629 small files, cheap to refetch but tedious."),
    ("pvdaq", "outputs/soiling/fleet",
     "Sharded fit results so far. Resumable: a rerun skips every system in here."),
    ("pvdaq", "outputs/soiling/pvdaq_fleet_labels.json",
     "Merged fleet labels, once --merge has been run."),
    ("pvdaq", "outputs/soiling/pvdaq_fleet_labels.csv",
     "The same labels flat: one row per system with loss_pct, CI, climate, and how "
     "its gamma was resolved. The form anything downstream actually joins against."),
    ("pvdaq", "outputs/soiling/pvdaq_fleet_summary.json",
     "n, Koppen spread and the east/west split against NREL's. The number that says "
     "whether this label set escapes the geographic bias it was built to escape."),

    # ── Stage 2 + paper lane: reproduce holdout AUC 0.710 ─────────────────────
    ("stage2", "outputs/soiling/training_matrix.parquet",
     "Nominally regenerable, practically not: a rebuild hits the Open-Meteo quota wall."),
    ("stage2", "runs/soiling/run_optionb",
     "The reference run. holdout_ci.py reads its feature_names.json."),
    ("stage2", "runs/soiling/run_lossreg",
     "The CQR-conformalised loss regressor that replaced RISK_TO_LOSS_PCT."),
    ("stage2", "data/external/nrel_soiling_map_annual.csv",
     "891 panel rows + 109 censored. Regenerable from NREL, but it is 100 KB."),
    ("stage2", "data/external/static_features.csv",
     "Elevation + WorldCover + OSM. A rebuild is ~15 min against external services."),

    # ── The shipping AOI: what the live dashboard is actually made of ─────────
    ("aoi", "outputs/aoi/santa-cruz-w2-21cm",
     "The 1,865-site Santa Cruz run behind the live dashboard, produced by rfdetr-w2. "
     "Regenerable in principle -- a full 249-tile detect + SAM2 pass is ~2.7 h on CPU -- "
     "but check-data asks for arrays.geojson, and every economics number in the repo is "
     "computed against this exact run. IDs and geometry only: no addresses, APNs or names."),
]

# Deliberately NOT in the bundle, with the reason, so the next person does not
# re-litigate it from scratch.
EXCLUDED: list[tuple[str, str]] = [
    ("data/rfdetr/scc21 (685 MB)",
     "Derived from data/yolo/scc21 by one command: scripts/data/build_rfdetr_dataset.py"),
    ("data/external/osm (1.3 GB)",
     "Regenerable and documented (7edb710); cuts 3,362 API calls to ~24"),
    ("data/interim/scc_2025_6cm (11 GB)",
     "Source imagery at 6cm. Re-fetchable from the county MapServer; not on any gate path"),
    (".cache/soiling/merra2.sqlite (1.3 GB)",
     "MERRA-2 was tried and hurt performance. Do not rebuild, do not ship"),
    ("models/*.pt, data/yolo/naip",
     "AGPL-lineage YOLO weights + the retired 60cm set. Eval-only history; never distributed"),
    ("outputs/aoi/* (~1 GB)",
     "Regenerable from the bundle above by re-running the pipeline"),
]


def iter_files(path: Path):
    """Real files only. Symlinks are SKIPPED, and that is load-bearing.

    `outputs/aoi/santa-cruz-w2-21cm/tiles/` is 249 absolute symlinks into
    `data/interim/scc21_labelset/images/`. Following them silently added 601 MB of
    duplicate pixels to a 21 MB group and would have shipped the same tiles twice.
    They are also absolute paths under Cameron's home directory, so they are broken
    in every other clone regardless -- the pipeline recreates them, they are not data.
    """
    if path.is_symlink():
        return
    if path.is_file():
        yield path
    elif path.is_dir():
        for p in sorted(path.rglob("*")):
            if p.is_file() and not p.is_symlink():
                yield p


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def verify(manifest_path: Path, wanted: set[str]) -> int:
    """Check a RESTORE against the manifest that shipped with the clone.

    This is the step the hand-off actually turns on. An upload nobody has restored
    from is not a backup, and "the files are there" is not the same claim as "the
    bytes are the ones that produced 0.826".
    """
    if not manifest_path.exists():
        raise SystemExit(f"No manifest at {manifest_path}. Build one with --hash on the source machine.")
    manifest = json.loads(manifest_path.read_text())
    if not manifest.get("hashed"):
        raise SystemExit("Manifest carries no hashes. Rebuild it with --hash.")

    # Per group, because lanes pull different slices. A group with NOTHING present was
    # not pulled and is not a failure -- reporting it as one would train people to
    # ignore this command. A group that is partly present is a broken restore and is.
    by_group: dict[str, dict[str, list]] = {}
    for rec in manifest["files"]:
        if rec["group"] not in wanted:
            continue
        g = by_group.setdefault(rec["group"], {"ok": [], "missing": [], "corrupt": []})
        path = REPO_ROOT / rec["path"]
        if not path.exists():
            g["missing"].append(rec["path"])
        elif sha256(path) != rec["sha256"]:
            g["corrupt"].append(rec["path"])
        else:
            g["ok"].append(rec["path"])

    failed = False
    for group in sorted(by_group):
        g = by_group[group]
        total = len(g["ok"]) + len(g["missing"]) + len(g["corrupt"])
        if not g["ok"] and not g["corrupt"]:
            print(f"  {group:13} not pulled ({total} files) -- skipped")
            continue
        if g["missing"] or g["corrupt"]:
            failed = True
            print(f"  {group:13} BROKEN: {len(g['ok'])}/{total} verified, "
                  f"{len(g['missing'])} missing, {len(g['corrupt'])} checksum mismatch")
            for label, items in (("missing", g["missing"]), ("mismatch", g["corrupt"])):
                for rel in items[:10]:
                    print(f"      {label}  {rel}")
                if len(items) > 10:
                    print(f"      ... and {len(items) - 10} more {label}")
        else:
            print(f"  {group:13} OK: {total} files byte-identical to the source machine")

    if failed:
        print("\n  A pulled group is incomplete. Re-run the rclone copy for it (DATA.md) "
              "and verify again.")
        return 1
    if not any(by_group[g]["ok"] for g in by_group):
        print("\n  Nothing pulled yet. See DATA.md for the rclone command.")
        return 1
    print("\n  Every group you pulled is complete.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hash", action="store_true", help="sha256 every file (slower; needed for verify)")
    ap.add_argument("--stage", type=Path, default=None, help="build a hardlinked staging tree here")
    ap.add_argument("--groups", default="stage1-gate,stage1-train,stage2,aoi,pvdaq",
                    help="comma-separated subset of groups to include")
    ap.add_argument("--manifest", type=Path, default=REPO_ROOT / "setup/handoff/MANIFEST.json")
    ap.add_argument("--tar", type=Path, default=None,
                    help="write one tar per group here (the no-card route: GitHub Release assets, "
                         "each group under the 2 GB per-asset limit)")
    ap.add_argument("--verify", action="store_true",
                    help="check this clone against the tracked manifest instead of building one")
    args = ap.parse_args()

    if args.verify:
        return verify(args.manifest, {g.strip() for g in args.groups.split(",") if g.strip()})

    wanted = {g.strip() for g in args.groups.split(",") if g.strip()}
    entries, missing, totals = [], [], {}

    for group, rel, why in BUNDLE:
        if group not in wanted:
            continue
        src = REPO_ROOT / rel
        if not src.exists():
            missing.append(rel)
            continue
        files = list(iter_files(src))
        size = sum(f.stat().st_size for f in files)
        totals[group] = totals.get(group, 0) + size
        print(f"  {group:13} {human(size):>10}  {len(files):>5} files  {rel}")
        for f in files:
            rec = {
                "group": group,
                "path": str(f.relative_to(REPO_ROOT)),
                "bytes": f.stat().st_size,
            }
            if args.hash:
                rec["sha256"] = sha256(f)
            entries.append(rec)
            if args.stage:
                link_or_copy(f, args.stage / f.relative_to(REPO_ROOT))

    print()
    for group, size in totals.items():
        print(f"  TOTAL {group:13} {human(size):>10}")
    print(f"  TOTAL {'bundle':13} {human(sum(totals.values())):>10}  ({len(entries)} files)")

    if missing:
        print("\n  MISSING from this machine -- the bundle would be incomplete:")
        for rel in missing:
            print(f"    {rel}")

    print("\n  Deliberately excluded:")
    for what, why in EXCLUDED:
        print(f"    {what}\n        {why}")

    if args.tar:
        import tarfile
        args.tar.mkdir(parents=True, exist_ok=True)
        for group in sorted(totals):
            dest = args.tar / f"handoff-{group}.tar"
            with tarfile.open(dest, "w") as tf:
                for rec in entries:
                    if rec["group"] == group:
                        tf.add(REPO_ROOT / rec["path"], arcname=rec["path"])
            print(f"  tar      -> {dest}  ({human(dest.stat().st_size)})")
        print("  restore with: tar xf handoff-<group>.tar -C <repo root>")

    # Guard: a partial run must not clobber the tracked full manifest. Found the hard
    # way -- a `--groups stage2 --tar` run replaced a hashed 2,473-file manifest with an
    # unhashed 17-file one, and a restore verified against THAT would have declared a
    # bundle complete while two thirds of it was absent.
    all_groups = {g for g, _, _ in BUNDLE}
    is_full = wanted >= all_groups and args.hash
    if not is_full and args.manifest == REPO_ROOT / "setup/handoff/MANIFEST.json":
        print("\n  NOT writing setup/handoff/MANIFEST.json: the tracked manifest must stay the "
              "full hashed one.\n  Rebuild it with: --hash (all groups), or pass --manifest "
              "<other path> for a partial.")
        if args.stage:
            print(f"  staged   -> {args.stage}")
        return 1 if missing else 0

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps({
        "generated_from": str(REPO_ROOT),
        "groups": sorted(wanted),
        "hashed": bool(args.hash),
        "total_bytes": sum(totals.values()),
        "file_count": len(entries),
        "missing": missing,
        "reasons": [{"group": g, "path": p, "why": w} for g, p, w in BUNDLE if g in wanted],
        "files": entries,
    }, indent=2))
    # Per-group file lists, so a lane can pull only its slice:
    #     rclone copy solarsoiled:solar-soiling-handoff . --files-from outputs/handoff/files-stage1-gate.txt
    # The bucket mirrors repo-relative paths, so one list works for both directions.
    def shown(path: Path) -> str:
        # --manifest may legitimately point outside the repo: the partial-run guard above
        # tells you to do exactly that, and relative_to() raises on an outside path, so
        # this used to crash *after* writing the tar -- work done, non-zero exit, and a
        # traceback that looks like the build failed when it had already succeeded.
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return str(path)

    for group in sorted(wanted):
        listing = args.manifest.parent / f"files-{group}.txt"
        listing.write_text("\n".join(e["path"] for e in entries if e["group"] == group) + "\n")
        print(f"  files    -> {shown(listing)}")

    print(f"\n  manifest -> {shown(args.manifest)}")
    if args.stage:
        print(f"  staged   -> {args.stage}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
