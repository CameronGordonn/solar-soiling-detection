#!/usr/bin/env python3
# Copyright 2026 Cameron Gordon
# SPDX-License-Identifier: Apache-2.0
"""Build the public mirror tree (``CameronGordonn/solar-soiling-detection``) from a branch.

WHY THIS EXISTS. The mirror was being assembled by hand: export the branch, delete the
outreach files, copy the built PDFs, notice that two doc links now dangle, remember the
banner. Every step is easy and the whole of it is easy to get wrong once, which is how a
public repo ends up either leaking a file or contradicting itself.

WHAT THE MIRROR IS. A curated snapshot, not a fork. It carries the full pipeline, docs and
paper; it does **not** carry the outreach records tied to identifiable homes, and it cannot
reach the private data bundle. It is squashed rather than history-mirrored on purpose:
the history contains address PII that was scrubbed in 2026-06 and a committed checkpoint,
so replaying it would re-expose both.

Usage::

    python scripts/publish_public_mirror.py --dest /path/to/solar-soiled-detection-clone
    python scripts/publish_public_mirror.py --dest <clone> --branch main --check-only

It stages files into ``--dest`` (a clone of the mirror) and leaves committing and pushing
to you, so the diff can be read before anything is published.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Held back from the mirror. Outreach records tied to identifiable homes, plus the test
#: that reads one of them. Every entry needs a reason, so nobody later "tidies" the list.
EXCLUDE = {
    "configs/outreach/published_qr_ids.csv": "QR ids mapped to mailed addresses",
    "docs/outreach/CLEANING_QUOTE_CALL_SHEET.md": "named companies and call notes",
    "docs/outreach/CPRA_CITY_OF_SANTA_CRUZ_SOLAR_PERMITS.md": "records request naming a jurisdiction and parcels",
    "docs/outreach/cpra_city_email_READY.txt": "ready-to-send correspondence",
    "outputs/aoi/santa-cruz-w2-21cm/array_install_era.csv": "per-array install dates, joinable to parcels",
    "tests/test_qr_compat.py": "reads published_qr_ids.csv",
}

#: Built artifacts copied in from the (gitignored) build dir so the papers are readable
#: without a LaTeX toolchain.
PDFS = {
    "paper/build/paper.pdf": "paper/paper.pdf",
    "paper/build/paper_detection.pdf": "paper/paper_detection.pdf",
}

BANNER = """> ### 📦 This is a public mirror
>
> A curated snapshot of a private working repository, published so the work can be read.
> It carries the full pipeline, the docs and the paper. Two things it does **not** carry:
>
> - **Outreach records.** Six files naming real homes, companies or correspondence are
>   held back. Doc links to them are marked *(not in the public mirror)*.
> - **The data bundle.** Weights, imagery, tiles and the label sets live in a release on
>   the private repo. Commands below that `gh release download` from
>   `Better-Behavior-Foundation/solar-soiling-ml` **will not work here**, and neither will
>   anything needing that data — including `paper/verify_numbers.py`, which reads
>   `outputs/soiling/audit/*.json`. The checked-in PDFs are the readable evidence.
>
> Everything else — the code, the tests, the numbers and their provenance — is complete
> and internally consistent. `make test-fast` passes from a clean clone with no data.

"""

MIRROR_DOC = """# About this mirror

This repository is a **curated public snapshot** of a private working repository. It is
published so the work can be read and run, not as a fork that receives changes.

## What is here

The full detection and soiling-risk pipeline, the CLI and API, the test suite, the docs,
and both papers with their sources and built PDFs. `make test-fast` passes from a clean
clone and needs no data.

## What is not here, and why

**Outreach records — six files.** They name real homes, companies, or carry
ready-to-send correspondence. Doc links to them are annotated *(not in the public
mirror)* rather than deleted, so the index still describes the real project.

| held back | reason |
|---|---|
{table}

**The data bundle.** Model weights, aerial tiles, label sets and the soiling-validation
audit artifacts live in a release on the private repository. Any instruction here to
`gh release download ... --repo Better-Behavior-Foundation/solar-soiling-ml` cannot be
followed from this mirror, and anything requiring that data will not run. Specifically:

- `paper/verify_numbers.py` reads `outputs/soiling/audit/*.json` and will fail. Those
  artifacts are produced by a separate, unpublished repository. The committed
  `paper/paper.pdf` and `paper/paper_detection.pdf` are the readable evidence.
- The Stage 1 gate (`scripts/detect/eval_tile_f1.py`) needs the 21cm tiles.
- Tests that depend on artifacts **skip** rather than fail, by design.

**History.** The mirror is squashed, not history-mirrored. The private history contains
homeowner address data that was scrubbed in 2026-06 and a model checkpoint committed
before the ignore rule existed; replaying it would re-expose both.

## Where numbers live

Every headline figure, the artifact that produced it and how to reproduce it are in
[`docs/CANONICAL_NUMBERS.md`](docs/CANONICAL_NUMBERS.md). When any other doc disagrees
with that file, that file wins.

## Rebuilding this mirror

```bash
python scripts/publish_public_mirror.py --dest /path/to/this/clone
```

The script is the definition of what the mirror contains: the exclusion list, the PDF
copy, the link annotation and this page all live in it.
"""


#: Files whose instructions cannot be followed from the mirror get a note under their H1,
#: because a reader lands on these directly rather than via the README.
NOTES = {
    "DATA.md": (
        "> ⚠️ **Not reachable from the public mirror.** The release this page describes "
        "lives on the private repository, so the `gh release download` commands below "
        "will fail here. Everything that does not need the bundle — the code, the tests, "
        "the docs and the built papers — is present. See [MIRROR.md](MIRROR.md)."
    ),
    "docs/ONBOARDING.md": (
        "> ⚠️ **Public mirror.** The clone URL and data-bundle steps below point at the "
        "private repository and will not work here. `make test-fast` does pass from a "
        "clean clone with no data. See [../MIRROR.md](../MIRROR.md)."
    ),
    "docs/CODEX_QUICKSTART_MAC.md": (
        "> ⚠️ **Public mirror.** The clone URL and data-bundle steps below point at the "
        "private repository and will not work here. See [../MIRROR.md](../MIRROR.md)."
    ),
}


def add_notes(tree: pathlib.Path) -> list[str]:
    """Insert a mirror note under the first heading of each file in NOTES."""
    done = []
    for rel, note in NOTES.items():
        md = tree / rel
        if not md.exists():
            continue
        lines = md.read_text().split("\n")
        if any("public mirror" in l.lower() for l in lines[:12]):
            continue
        i = next((n for n, l in enumerate(lines) if l.startswith("# ")), -1)
        lines.insert(i + 1, "\n" + note)
        md.write_text("\n".join(lines))
        done.append(rel)
    return done


def run(cmd: list[str], **kw) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def export_branch(branch: str, dest: pathlib.Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", branch], cwd=REPO,
                             check=True, capture_output=True)
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)


def annotate_excluded_links(tree: pathlib.Path) -> list[str]:
    """Mark links to held-back files instead of leaving them dangling.

    Deleting the rows would misrepresent the project (the docs index would stop describing
    work that exists); leaving them would dangle. Annotating keeps the index honest and
    tells the reader why they cannot follow it.
    """
    touched = []
    names = [pathlib.PurePosixPath(p).name for p in EXCLUDE]
    for md in tree.rglob("*.md"):
        text = original = md.read_text(errors="ignore")
        for name in names:
            # [label](any/path/NAME) -> label *(not in the public mirror)*
            text = re.sub(
                r"\[([^\]]+)\]\((?:[^)]*/)?" + re.escape(name) + r"\)",
                r"\1 *(not in the public mirror)*",
                text,
            )
        if text != original:
            md.write_text(text)
            touched.append(str(md.relative_to(tree)))
    return touched


def add_mirror_docs(tree: pathlib.Path) -> None:
    readme = tree / "README.md"
    text = readme.read_text()
    if "This is a public mirror" not in text:
        lines = text.split("\n")
        i = next((n for n, l in enumerate(lines) if l.startswith("# ")), -1)
        lines.insert(i + 1, "\n" + BANNER.rstrip())
        readme.write_text("\n".join(lines))
    table = "\n".join(f"| `{p}` | {why} |" for p, why in sorted(EXCLUDE.items()))
    (tree / "MIRROR.md").write_text(MIRROR_DOC.format(table=table))


def check_links(tree: pathlib.Path) -> list[str]:
    bad = []
    for md in sorted(tree.rglob("*.md")):
        if ".git/" in str(md):
            continue
        for m in re.finditer(r"\[([^\]]*)\]\(([^)]+)\)", md.read_text(errors="ignore")):
            target = m.group(2).split("#")[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "tel:", "#")):
                continue
            if not (md.parent / target).resolve().exists():
                bad.append(f"{md.relative_to(tree)} -> {target}")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=pathlib.Path, required=True,
                    help="a clone of the mirror repo; its contents are replaced")
    ap.add_argument("--branch", default="HEAD", help="branch to publish (default: HEAD)")
    ap.add_argument("--check-only", action="store_true",
                    help="build into a temp dir and report, touching nothing")
    args = ap.parse_args(argv)

    dest = args.dest.resolve()
    if not args.check_only and not (dest / ".git").is_dir():
        print(f"[!] {dest} is not a git clone — refusing to write", file=sys.stderr)
        return 2

    staging = dest if not args.check_only else dest / ".mirror-check"
    if args.check_only and staging.exists():
        shutil.rmtree(staging)

    if not args.check_only:
        for child in dest.iterdir():
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() else child.unlink()

    export_branch(args.branch, staging)
    print(f"[export] {args.branch} -> {staging}")

    removed = 0
    for rel in EXCLUDE:
        p = staging / rel
        if p.exists():
            p.unlink()
            removed += 1
    print(f"[exclude] held back {removed}/{len(EXCLUDE)} outreach files")

    for src, dst in PDFS.items():
        s = REPO / src
        if s.exists():
            shutil.copy2(s, staging / dst)
            print(f"[pdf] {src} -> {dst}")
        else:
            print(f"[pdf] MISSING {src} — run `make -C paper` first", file=sys.stderr)

    touched = annotate_excluded_links(staging)
    print(f"[links] annotated held-back links in {len(touched)} file(s): {touched or '-'}")

    add_mirror_docs(staging)
    noted = add_notes(staging)
    print(f"[docs] README banner + MIRROR.md written; noted {len(noted)} file(s): {noted or '-'}")

    bad = check_links(staging)
    if bad:
        print(f"[links] {len(bad)} BROKEN:", file=sys.stderr)
        for b in bad:
            print("   ", b, file=sys.stderr)
    else:
        print("[links] no broken internal links")

    leaked = [r for r in EXCLUDE if (staging / r).exists()]
    if leaked:
        print(f"[!] EXCLUDED FILE PRESENT: {leaked}", file=sys.stderr)
        return 1

    if args.check_only:
        shutil.rmtree(staging)
        print("[check-only] staging removed")
    else:
        print(f"\nStaged in {dest}. Review `git -C {dest} status`, then commit and push.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
