# Cold-start audit + traps

**2026-08-23.** Findings from cloning this repo to an empty directory and following
`docs/ONBOARDING.md` literally, plus the set of traps that silently produce *plausible wrong
numbers* rather than errors. Written because as of 2026-08-31 nobody left on the project has
run a cold setup, and every gap below is one a newcomer eats as hours of debugging.

Part A is a fix list. Part B is a permanent reference.

> **Status 2026-08-26: every Part A item is fixed, and the audit was incomplete.** Re-checking it
> against the repo turned up four more blockers it missed, three of them worse than what it found:
> `requirements.txt` pinned `ultralytics>=11.0`, a version that **does not exist**, so
> `pip install -r requirements.txt` never resolved for anyone; `rfdetr` and `sam2` were undeclared,
> so the detection lane could not run at all; `setup_conda.sh` installed `pytorch-cuda` under
> `set -e` and aborted on any non-NVIDIA machine before reaching the python-version problem; and
> `runs/soiling/run_latest` (A5) does not exist on Cameron's machine either, so `check-data` had
> never been green for anybody. Fixes and the measured re-runs are in
> [`ONBOARDING_OVERHAUL_PLAN.md`](ONBOARDING_OVERHAUL_PLAN.md). **Part B still stands unchanged and
> is the reason to keep this file.**

---

## Part A — what a fresh clone actually does

Verified by `git clone` into an empty dir (266 tracked files, 59 MB) and working forward.

### A1. `ONBOARDING.md` never says how to create the environment — BLOCKING

§2 "Environment" states the env is `solar-soiling` and jumps straight to
`conda run -n solar-soiling python <script>`. It does not mention **any** of:

- `setup/setup_conda.sh` — the script that actually creates the env
- `make bootstrap` — installs the package + extras
- `DATA.md` — provenance for every gitignored artifact

`README.md:264` has the setup command and `README.md:259` points at `DATA.md`. But
`CLAUDE.md` and `TEAM.md` both send new people to `ONBOARDING.md`, and §1 of ONBOARDING
tells them to read README first, so the pointer chain only works if they read in that exact
order and remember it two sections later. Someone who opens ONBOARDING and skims to
"Environment" hits a dead end.

**Fix:** add the three commands to §2, in order.

### A2. `setup_conda.sh` builds the WRONG Python — BLOCKING, and it fails silently

```
setup/setup_conda.sh:  conda create -y -n solar-soiling python=3.11
actual working env:    .../envs/solar-soiling/lib/python3.10/...
```

The repo was developed and tested on **3.10**. The setup script hands a newcomer **3.11**.
`pyproject.toml` says `requires-python = ">=3.10"` so nothing complains, and geopandas /
rasterio / xgboost will all resolve to *different* wheels. Any resulting breakage looks like
their bug, not an environment mismatch.

**Fix:** pin `python=3.10` in the script, or state the tested version explicitly and let
them choose knowingly.

### A3. The verify step fails with an unactionable stack trace — BLOCKING

`ONBOARDING.md` §5 says run `scripts/predict/holdout_ci.py` and expect AUC ~0.710. Cold, it
dies twelve frames deep in `pandas.io.parquet` with:

```
FileNotFoundError: [Errno 2] No such file or directory: '.../outputs/soiling/training_matrix.parquet'
```

No mention of `make check-data`, no mention of `DATA.md`, no hint that this file is a
deliberate hand-off artifact rather than something the newcomer broke. This is the very
first command the onboarding doc asks them to run.

**Fix:** preflight the input paths in `holdout_ci.py` and fail with "missing X — run
`make check-data`, see DATA.md". `make check-data` itself is good and reports all six
artifacts correctly; it just isn't reachable from the failure.

### A4. `laspy` is an undeclared dependency

`src/risk/roof_geometry.py` reads 3DEP EPT over HTTP with `laspy` (deliberately, to avoid a
PDAL/PROJ dependency). `laspy` appears in neither `requirements.txt` nor `pyproject.toml`.
`rdtools` and `pvlib` are correctly declared in an optional extra with a comment explaining
why; `laspy` was missed, and it sits in the production physics path.

**Fix:** declare it.

### A5. `check-data` and the verify step disagree about which run matters

`make check-data` looks for `runs/soiling/run_latest/model.ubj`. `ONBOARDING.md` §5 runs
`holdout_ci.py`, which needs `runs/soiling/run_optionb/feature_names.json`. A newcomer can
have a fully green `check-data` and still fail §5.

**Fix:** make `check-data` check what §5 actually needs.

---

## A6. A 23 MB AGPL-lineage weight is TRACKED IN GIT — needs a decision, not a fix

```
$ git ls-files | grep '\.pt$'
models/yolov8s_solar_array_v1.pt      (23 MB, added in cc4ec96)
```

This contradicts three separate statements in the repo:

| says | where |
|---|---|
| `*.pt` and `*.pth` are ignored | `.gitignore:226-227` |
| "Base weights (`.pt` files) are gitignored — download manually to `models/`" | `CLAUDE.md:125` |
| "Ship no AGPL-derivative weights" | `ONBOARDING.md` §4 guardrails |

It was committed **before** the ignore rule existed, and `.gitignore` has no effect on
already-tracked files, so the rule has never applied to it. A fresh clone gets the weight,
which is why `make check-data` cheerfully reports `present models/*.pt` on a machine that
received no hand-off at all.

**Why it matters.** The entire permissive-stack migration exists to get off Ultralytics
AGPL. A YOLOv8-derived weight distributed inside the repo is the specific asset that effort
is trying not to distribute, and `requirements.txt:18` still hard-requires
`ultralytics>=11.0`. It also means anyone reading `CLAUDE.md` has an incorrect mental model
of what this repo hands out.

**Why it is not a quick fix.** It is in history. `git rm` removes it from `HEAD` and leaves
it in every prior commit, so a real removal is a history rewrite — which this project has
done once before, for the leaked address file, and which is on the "ask first" list in
`.claude/rules/working-agreement.md`.

**Three options, all defensible:**

1. **Leave it, correct the docs.** Cheapest. The repo is private; the exposure is bounded.
   `CLAUDE.md:125` and the §4 guardrail get a sentence saying this one file is tracked, and
   why.
2. **`git rm` from HEAD, correct the docs, accept the history.** Stops new clones from
   receiving it. History still contains it.
3. **Full history rewrite.** Only genuinely necessary if this repo is ever going public or
   the weight is going into a grant deliverable.

**Recommendation: 2, plus the doc correction from 1.** It stops the ongoing distribution,
costs ten minutes, and does not spend Cameron's last week on a rewrite. Revisit 3 if the
repo's visibility ever changes.

---

## Part B — traps that produce plausible wrong numbers

The Part A items announce themselves. These do not. Each has bitten this project at least
once and cost real time.

| trap | what happens | where |
|---|---|---|
| **Mercator scale factor** | A plane fitted on raw EPSG:3857 lidar reports tilt **18.9% too shallow**, silently, on every roof, with an entirely plausible-looking distribution. `fit_plane()` de-inflates X/Y by `cos(lat)`. Do not remove that line. | `src/risk/roof_geometry.py`, pinned by `tests/test_roof_geometry.py` |
| **`tile_index.json` stores affines in TWO orderings** | Readers that assume one ordering silently mis-georeference. Fixed in `2b5a31d`; the dual format remains. | `data/interim/tile_index.json` |
| **`soiling_srr` default convention** | NREL's map uses `perfect_clean`; RdTools defaults to `half_norm_clean`. Using the default produces labels that are **not comparable** to the existing 891 rows, with no error. | `scripts/analyze/washable_share_probe.py` |
| **PVDAQ 2107 2025 files** | Known timezone and cadence defects. Do not use the 2025 vintage for annual numbers. | `docs/AOI_CLEANING_TARGETING_PLAN.md` |
| **`sl_sat` was fitted against the wrong side of the trajectory** | The objective scored 31 December, mid wet season, against an *annual-mean* measured IWSR. Doesn't invalidate the annual-mean validation, but a refit would not be expected to return 0.08. | `src/risk/physics_score.py`, `configs/soiling/features.yaml` |
| **Two GT counts, not interchangeable** | Chip-level val 385 / test 636; tile-level val 340 / test 585. A tile-level F1 quoted against 636 is wrong by construction. | `.claude/rules/stage1-detect.md` |
| **An editable install points at a DIFFERENT checkout** | `solarsoiled.paths.REPO_ROOT` follows `pip install -e`, not the cwd. Run a script or the suite from clone B while the env was installed from clone A and it silently reads A's `registry.yaml`, models and data. Two Stage-2 contract tests pass this way on a clone that received no hand-off at all: 248/3 instead of the honest 246/5. | `src/solarsoiled/paths.py`, any second checkout |
| **`optimizer: auto`** | Silently overrides `lr0` to AdamW(0.002). Must stay `SGD`. | `configs/yolo/*.yaml` |
| **Grouped CV on PVDAQ must group by CLUSTER** | 173 coordinates carry >1 system, one carries 21. Grouping by station or system leaks and yields a beautiful meaningless AUC. | `docs/PVDAQ_LABEL_PIPELINE_SPEC.md` §5 |
| **The 15% shrink appears twice for the same reason** | SAM2 prompt boxes and lidar plane fits both shrink 15%, both to avoid pulling in neighbouring structure. Changing one does not change the other. | `rfdetr_infer.py`, `roof_geometry.py` |
| **`main` is NOT protected** | Branch protection is unavailable on private repos under the org's free plan. "PR + 1 approval" is a convention nothing enforces. | `docs/ONBOARDING.md` §3 |
| **Open-Meteo quota wall** | ~600 homes/day on the free tier. Bulk scoring hits it; gates are built in but a careless test run burns the day's budget. | `src/risk/weather_client.py` |
| **Lob is live money** | `mail_via_lob.py --send` mails real postcards. `mailers_v13` already went 50/50 on 2026-06-30. Never re-run a sent batch. | `scripts/outreach/mail_via_lob.py` |

---

## Reproduce this audit

```bash
git clone <repo> /tmp/coldclone && cd /tmp/coldclone
make check-data                 # every hand-off artifact MISSING on a true cold clone
git ls-files | grep '\.pt$'     # EMPTY as of 2026-08-26 (A6 resolved, HEAD only)
grep -c "setup_conda" docs/ONBOARDING.md   # >= 1 as of 2026-08-26 (A1 fixed)
make test-fast                  # 246 passed, 5 skipped, 1 deselected, with NO data
```

The last line is the one that matters. It is the only check that separates a broken environment
from a missing hand-off, and it was not in the onboarding doc at all when this audit was written.
