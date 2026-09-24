# About this mirror

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
| `configs/outreach/published_qr_ids.csv` | QR ids mapped to mailed addresses |
| `docs/outreach/CLEANING_QUOTE_CALL_SHEET.md` | named companies and call notes |
| `docs/outreach/CPRA_CITY_OF_SANTA_CRUZ_SOLAR_PERMITS.md` | records request naming a jurisdiction and parcels |
| `docs/outreach/cpra_city_email_READY.txt` | ready-to-send correspondence |
| `outputs/aoi/santa-cruz-w2-21cm/array_install_era.csv` | per-array install dates, joinable to parcels |
| `tests/test_qr_compat.py` | reads published_qr_ids.csv |

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
