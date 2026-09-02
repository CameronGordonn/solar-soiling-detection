# Plan — make this repo onboardable without Cameron

**Written 2026-08-23. Rewritten 2026-08-26 after every claim in it was checked against the repo,
and after the code and doc phases were executed.** Target: a person with a GitHub invite and an R2
token can clone this repo and reach a reproduced gate number without asking anyone a question.
Hard deadline 2026-08-31.

Findings are in [`COLD_START_AUDIT_20260823.md`](COLD_START_AUDIT_20260823.md). Part A of that
audit is now fixed; Part B is a permanent trap reference and stays.

---

## What the verification pass changed

The original plan was right about the shape of the problem and wrong about one load-bearing
detail. Both are worth recording, because the wrong one would have failed silently.

**Confirmed by re-running it, then corrected by running it properly.** `make test-fast` in a bare
clone does pass with no data of any kind, and the onboarding doc pointed at `holdout_ci.py`
instead, which needs handed-over data. A pointer bug, and fixing it was the single largest
improvement available.

But the exact number in the original plan, **248 passed / 3 skipped**, is an artifact of measuring
on Cameron's machine. `solarsoiled.paths.REPO_ROOT` follows the editable install rather than the
working directory, so two Stage-2 contract tests run from the throwaway clone reached back into the
real repo, found its `registry.yaml` and its `run_optionb/model.ubj`, and passed **on handed-over
data inside a test that was supposed to prove no data was needed**. Built from scratch in a clean
conda env, the honest number is **246 passed, 5 skipped, 1 deselected**. Added to the Part B trap
list, because it produces a green signal that means nothing.

**Wrong, and it broke the plan's own definition of done.** The "irreplaceable core" was given as
825 MB: `data/yolo/scc21` + `rfdetr_w2` + `tile_index.json` + `runs/soiling`. But
`scripts/detect/eval_tile_f1.py` reads `data/interim/scc21_labelset/images` — **603 MB of full
tiles that appeared nowhere in the plan** — while 684 MB of the `data/yolo/scc21` it did bundle is
training chips the gate never touches. A newcomer restoring from that core would have passed
`check-data` and then been unable to reproduce 0.826. The real bundle is **1.5 GB in three groups**
and is now defined in code, not prose: `scripts/data/build_handoff_bundle.py`.

**Also found, none of which were in the audit:**

- **`requirements.txt` pinned `ultralytics>=11.0`, which does not exist.** The package tops out at
  8.4.x; YOLO11 ships inside 8.3+. `pip install -r requirements.txt` was unresolvable, for
  everyone, always. The env on this machine was built by hand.
- **`rfdetr` and `sam2` were undeclared** in both `requirements.txt` and `pyproject.toml`, along
  with `laspy`, `folium`, `matplotlib`, `planetary-computer`, `pystac-client`, `pypdf`, `scipy`.
  The audit caught `laspy` alone. Without `rfdetr` the detection lane cannot run **at all**, which
  makes "reproduce 0.826" unreachable regardless of data.
- **`setup_conda.sh` installed `pytorch-cuda=11.8` unconditionally under `set -e`**, which aborts
  the script on any CPU-only or Apple machine. A harder blocker than the 3.11 pin, and it hits
  before it.
- **`runs/soiling/run_latest` does not exist on Cameron's machine either**, so `check-data` had
  never been green for anyone. Audit item A5 was worse than written.
- **`TEAM.md` said "`main` is protected"**, contradicting both the audit and `ONBOARDING.md`.
- **`make demo-aoi` was written up as broken and is not.** The registry's own constraint note and
  `CLAUDE.md` both say `cli.py tile` fetches 60cm, so a 21cm `production` needs pre-staged tiles.
  That stopped being true in `95d3368` (2026-08-07): `run` reads the resolved model's
  `gsd_ground_m` and routes to the county service below 0.35. Confirmed by running the full
  pipeline on a small Santa Cruz bbox on 2026-08-26 — tiled `scc21`, detected 36 arrays with
  `rfdetr-w2-20260807`, scored, recommended. The surviving constraint is geographic (the service
  covers Santa Cruz only), not plumbing. **Both stale notes are still in the working tree's
  uncommitted registry work and should be corrected before that lands.**

---

## The hand-off bundle — 1.5 GB, three groups

Generated and checksummed by `scripts/data/build_handoff_bundle.py`; the manifest with a sha256
per file is tracked at `setup/handoff/MANIFEST.json`, so a restore is verified against something
that shipped with the clone rather than with the data.

| group | size | files | buys you |
|---|---|---|---|
| `stage1-gate` | 724 MB | 497 | reproduce test F1 **0.826** |
| `stage1-train` | 806 MB | 1,959 | retrain the detector |
| `stage2` | 2.4 MB | 17 | reproduce holdout AUC **0.710**, run the product pipeline |

Excluded on purpose, with the reason attached in the script: `data/rfdetr/scc21` (685 MB, one
command from `data/yolo/scc21`), `data/external/osm` (1.3 GB, regenerable), `scc_2025_6cm`
(11 GB, off every gate path), `merra2.sqlite` (1.3 GB, measured to hurt), the AGPL `models/*.pt`,
and `outputs/aoi/*`.

**`models/rfdetr_w2_20260807.pth` is still the single highest-value file in the project.** The
0.826 result is unreproducible without it and it exists only from a Colab run. Note the known
trap: the Drive MCP connector returns base64 into the conversation, so a 122 MB checkpoint cannot
move that way. Use `rclone`.

---

## Status

| # | phase | state |
|---|---|---|
| 1 | Data custody | **done 2026-08-26** — published as GitHub Release `handoff-v1`, 1.5 GB in four groups |
| 2 | Tier the verification | **done** |
| 3 | Rewrite ONBOARDING | **done** |
| 4 | Lane quickstarts | **done**, with measured numbers |
| 5 | Second-person validation | **not done, and it is the one that produces evidence rather than belief** |

### Done (2026-08-26)

- `setup/setup_conda.sh`: python 3.10 pinned, CUDA opt-in behind `WITH_CUDA=1`, rasterio installed
  rather than assumed, and it ends by naming the next three commands.
- `pyproject.toml` / `requirements.txt`: the ultralytics pin corrected, nine undeclared imports
  declared, and a new `[detect]` extra carrying `rfdetr` + `sam2`.
- `scripts/predict/holdout_ci.py`: preflights its inputs and exits with the missing paths and
  `make check-data`, instead of a twelve-frame pandas traceback. Verified in a cold clone.
- `Makefile`: the env name is a variable (`CONDA_ENV`) rather than hardcoded, so `make test-fast` can no longer test an env you did not build; `check-data` regrouped by lane and pointed at the artifacts each lane actually
  reads; new `verify-handoff` target that checksums a restore; `bootstrap` names the detect extra.
- `models/yolov8s_solar_array_v1.pt` removed from `HEAD` (option 2 of audit A6 — history left
  alone). `check-data` no longer reports a present weight on a machine that received nothing.
- `docs/ONBOARDING.md` rewritten as a linear path with three tiers and three lane quickstarts,
  each ending in a number that was measured on 2026-08-26, not asserted.
- `DATA.md` rewritten around the bucket and named custodians. "Ask Cameron" is gone.
- `TEAM.md` and `CLAUDE.md` corrected where they contradicted reality.

### Measured, not asserted

Every number quoted to a newcomer was re-run on 2026-08-26 on this machine:

```
setup_conda.sh (fresh env)         python 3.10.21, rasterio 1.4.3,
                                   torch 2.6.0 cpu                         clean run, ~15 min solve
make bootstrap + make test-fast    246 passed, 5 skipped, 1 deselected     ~20s, no data
holdout_ci.py                      pooled out-of-year AUC 0.7095           ~60s
eval_tile_f1.py (rfdetr_w2)        conf* 0.50 -> test P 0.850 R 0.803
                                   F1 0.826  CI [0.798, 0.853]  n_gt 585   ~5.5 min CPU, PASS
```

The whole of §1 has now been executed in order, in a throwaway clone, against a conda env built
from scratch by the rewritten `setup_conda.sh` — including the two commands the plan's own rule
("no command goes in that hasn't been run") would otherwise have let through on trust.

The gate reproduces the documented 0.8260 exactly. Calibration decimals in `holdout_ci.py` move
run to run (Brier 0.215–0.218, ECE 0.031–0.046), so the lane doc tells newcomers to check the
verdict line, not the decimals.

---

## Data custody — done, and what is left

**The store is a GitHub Release on this repo:
[`handoff-v1`](https://github.com/Better-Behavior-Foundation/solar-soiling-ml/releases/tag/handoff-v1).**
Chosen 2026-08-26 over R2 and Drive for one reason that outranks the convenience arguments: release
assets ride on the org membership a teammate already has, so the hand-off adds **no credential to
hold, hand over, or lose**. A bucket would have replaced "ask Cameron for the files" with "ask
Craig for the token", which is the same dependency wearing a different hat. R2 was the first choice
and was dropped because Cloudflare requires a payment method on file even inside the free tier.

Four assets, 1.5 GB, repo-relative paths inside each tar:

| asset | size | files | buys you |
|---|---|---|---|
| `handoff-stage1-gate.tar` | 725 MB | 497 | reproduce test F1 **0.826** |
| `handoff-stage1-train.tar` | 809 MB | 1,959 | retrain the detector |
| `handoff-stage2.tar` | 2.4 MB | 17 | reproduce holdout AUC **0.7095** |
| `handoff-aoi.tar` | 19 MB | 272 | the live 1,865-site run and its economics |

Two bugs the bundle build hit, both of the kind that fail silently:

- **The AOI group tried to ship 601 MB of duplicate pixels.**
  `outputs/aoi/santa-cruz-w2-21cm/tiles/` is 249 **absolute symlinks** into
  `data/interim/scc21_labelset/images/`. Dereferencing them turned an 18 MB group into 620 MB of
  the same tiles already in `stage1-gate`. Symlinks are skipped now. They are also absolute paths
  under Cameron's home directory, so they are broken in every other clone anyway; the pipeline
  recreates them.
- **A partial run clobbered the tracked manifest.** `--groups stage2 --tar` replaced a hashed
  2,473-file manifest with an unhashed 17-file one, and a restore verified against *that* would
  have declared the bundle complete with two thirds of it absent. The default manifest path now
  refuses any write that is not a full hashed run.

### The round trip is proven

Not asserted. On 2026-08-26, into a throwaway clone that received nothing else:

```
git clone …                                     37 MB, code only
gh release download handoff-v1                  4 assets, 1.5 GB
tar xf handoff-*.tar -C .
make check-data          -> zero MISSING in both Tier-2 blocks
make verify-handoff      -> 2,745 files byte-identical across all four groups
holdout_ci.py            -> pooled out-of-year AUC 0.7095, calibration RETAINED
eval_tile_f1.py          -> conf* 0.50, test P 0.850 R 0.803 F1 0.826
                            95% CI [0.798, 0.853], n_gt 585, gate PASS
```

Both gate numbers come out identical to the ones produced on the working tree, so the bundle
carries everything they depend on and nothing in the reproduction path silently reaches back into
Cameron's checkout. That is the definition of done at the bottom of this document, met.

### Still outstanding

1. **A restore run by someone who is not Cameron.** The round trip above was executed from
   Cameron's machine and account. It proves the release is complete and the bundle sufficient; it
   does not prove a second person can reach it, because the one thing it could not test is
   somebody else's GitHub access. First teammate to try: run the block above and say whether the
   download worked without anyone granting you anything.
2. **Keep more than one GitHub org owner.** Custody is now the org itself. That is a better single
   point of failure than a person, but it is still a single point of failure.
3. **Akshitha runs `ONBOARDING.md` §1 top to bottom on a clean machine, on a call**, saying out
   loud every point where she has to guess, ask, or look elsewhere. Log each as a defect, fix the
   same day. She onboarded in July so she is not a true cold reader and this is weaker than a
   stranger would be. It is still the only evidence-producing step available before the 31st. If
   nobody is available, §7 of `ONBOARDING.md` tells the first newcomer to treat §1 as a test.

## Definition of done

A person with a GitHub invite and an R2 token reaches **0.826** or **0.7095** without asking a
question. Everything except the bucket is in place; the bucket is the whole remaining risk, and it
is the one item that becomes impossible rather than merely harder after the 31st.
