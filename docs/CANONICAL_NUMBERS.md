# Canonical numbers

**Every headline number in this repo, what produced it, and how to reproduce it.**
Built 2026-08-31 by reading the artifacts on disk, not by reconciling the docs against each other.

Why this file exists: the same quantity was being quoted at different values in different docs, and
in several cases the *reasoning* around it had moved on while the number had not. When a doc and
this file disagree, **this file wins and the doc is stale**. When this file and an artifact on disk
disagree, **the artifact wins and this file is stale**. Fix upward, never sideways.

The house rule from [README.md](README.md) still applies on top: when a number moves, fix it where
it is canonical and mark the superseded copy in place rather than deleting it.

---

## Stage 1 — detection

Source of truth: `outputs/eval/rfdetr_w2/gate.json`. Reproduce with
`PYTHONPATH=. conda run -n solar-soiling python scripts/detect/eval_tile_f1.py --weights models/rfdetr_w2_20260807.pth --run-name <name>`.

| Quantity | Value | Artifact |
|---|---|---|
| test F1 (the gate) | **0.8260** | `rfdetr_w2/gate.json:f1` |
| test precision | 0.8499 | same |
| test recall | 0.8034 | same |
| test F1 95% CI | [0.798, 0.8528] | same, bootstrap over tiles |
| conf\*, tuned on val and frozen | 0.50 | same |
| n_gt / n_tiles (test, tile-level) | 585 / 49 | same |
| test's own best F1 (the tuning penalty) | 0.8431 | same |
| **Gate verdict** | **PASS** on all three | F1 ≥ 0.75, CI-lower ≥ 0.70, recall ≥ 0.70 |

W1 anchor, same path (`outputs/eval/rfdetr_w1_anchor/gate.json`): F1 **0.8015**, P 0.8533,
R 0.7556, CI [0.7734, 0.8302], conf\* 0.40. Paired bootstrap W2 − W1: F1 **+0.0246**
CI [+0.0051, +0.0441], recall **+0.0479** CI [+0.0202, +0.0753].

**Two GT counts, not interchangeable.** Tile-level (the gate): val 340 / test 585. Chip-level:
val 385 / test 636. A tile-level F1 quoted against 636 is wrong by construction.

---

## Stage 2 — soiling risk

> ### ⚠️ Read this before quoting anything in Stage 2
>
> **The folds that produced every Stage 2 gate figure below were leaking.** Found 2026-09-22 while
> writing `paper/paper.tex`; it supersedes every Stage 2 validation number published before that
> date, including the ones in the tables that follow.
>
> Both production folds hold out **one axis and not the other**. The 10 km spatial fold holds out
> *place* but lets a station's other years into training; the leave-one-year-out fold holds out
> *year* but lets the same station in through its other years — for **88.7%** of rows, measured.
> Neither fold ever asked the model to score a station it had not already seen.
>
> Under a fold that is **jointly out-of-station and out-of-year**, the 40-feature production model
> scores **0.622** (95% CI [0.571, 0.670], P(AUC ≥ 0.70) = 0.001) — not the 0.710 the gate recorded.
>
> | Under the joint fold | AUC | What it means |
> |---|---|---|
> | production, 40 features | **0.6222** | the honest number |
> | **latitude + longitude alone** | **0.6439** | *beats* all 40 features |
> | unfitted Kimber (2006) | 0.6096 | no training at all |
> | unfitted SOMOSclean | 0.5440 | no training at all |
> | 7-seed robustness | 0.620 ± 0.0047 | not a seed artefact |
>
> **How much of the fall is leakage, not fold size?** A size-matched control isolates it: the
> control scores **0.6986** against a leave-one-year-out reference of **0.7144**, putting **83%**
> of the drop on leakage (95% CI [52, 99]).
>
> **Nothing below is a fabrication, and the older numbers still reproduce.** Matched on reporting
> basis — pooled vs mean-of-folds, 1,002 rows vs 891 panel rows, plain fit vs calibrated pipeline —
> every previously published figure reproduces to within **0.012**. This was a fold-construction
> error, not a measurement error, which is why the tables below are corrected in place rather than
> deleted.
>
> **Artifacts:** `outputs/soiling/audit/{reconciliation,validation_audit,trainsize_control,robustness,physics_baselines}.json`.
> ⚠️ These are **gitignored and not committed**; they are produced by the standalone
> `soiling-validation-audit` repo, not by anything in `scripts/`. `paper/verify_numbers.py` reads
> them and will fail on a clone without them. Full argument: `paper/paper.tex` §4.4.
>
> **Practical rule.** Stage 2 is valid for estimating soiling **level** in the training geography.
> It is **not** validated for ranking roofs against each other, and the registry's "all three
> Phase-2 gates cleared" note is stale.

### The three spatial-CV AUCs, and which one to quote

This is the single biggest source of cross-doc disagreement in the repo. **All three numbers below
are real and on disk.** They measure different things, and quoting one as another is the error.

| Value | Run | What it is | Artifact |
|---|---|---|---|
| **0.728** (0.7276) | `run_optionb` | The shipped run **as originally recorded**. The number most docs quote. | `runs/soiling/run_optionb/metrics.json:mean_auc` |
| **0.712** (0.7118) | `abl_full40` | **The honest current figure.** The same 40-feature set re-measured 2026-08-06 on the cached matrix under today's `model.yaml`. 0.721 ± 0.006 over 7 seeds. | `runs/soiling/abl_full40/metrics.json:mean_auc` |
| **0.746** (0.7459) | `run_regularized2022` | The regularized default-config reference (`max_depth=4, reg_lambda=5`). | `runs/soiling/run_regularized2022/metrics.json:mean_auc` |

**Quote 0.712 for "how good is the model today" *under the 10 km spatial fold*** — and say which
fold, because that fold is one of the two leaking ones. 0.728 is the stored artifact of a specific
past run and does not reproduce under the current config. The margin over the 0.70 gate is about
1 point, not 3.

⚠️ **All three values above share the same defect**: holding out place does not hold out the
station, whose other years remain in training. Under the joint fold the same 40 features score
**0.622**. Treat 0.712 as "spatial-CV as historically computed", never as evidence the model
generalizes. Reasoning in [SOILING_STAGE2_GUIDE.md](SOILING_STAGE2_GUIDE.md).

### Single-year 2022 holdout — two values, two runs

| Value | Run | Artifact |
|---|---|---|
| **0.679** (0.67886) | `run_optionb` | `run_optionb/metrics.json:holdout_auc`, n=97 |
| **0.680** (0.68035) | `run_regularized2022` | `run_regularized2022/metrics.json:holdout_auc`, n=97 |

Both are correct for their own run. Do not attribute 0.680 to `run_optionb`. **This gate is retired
either way** (see below), so neither number should carry a verdict.

### Pooled out-of-year AUC — the live temporal gate

Source of truth: `runs/soiling/run_optionb/holdout_ci.json`. Reproduce with
`PYTHONPATH=. conda run -n solar-soiling python scripts/predict/holdout_ci.py` (cached matrix, no
weather refetch, ~60s).

| Quantity | Value |
|---|---|
| pooled out-of-year AUC | **0.7095** — ⚠️ **leaking fold; do not quote as a gate result.** Reproduces exactly, but see the verdict row |
| n pooled / pos / neg | 891 / 440 / 451 |
| Hanley-McNeil SE | 0.01725 |
| bootstrap 95% CI | [0.676, 0.742] |
| P(AUC ≥ 0.70) | 0.70 |
| verdict | ⚠️ **RETIRED — leaking fold.** 88.7% of rows keep their station in training through its other years. Corrected joint-fold value: **0.622** [0.571, 0.670] |
| Brier / base-rate ref | 0.2177 / 0.2500 |
| ECE / MCE | 0.0457 / 0.1259 |
| calibration retained | **True** |

**The single-year-2022 gate was replaced on 2026-07-05, not failed.** At n=97 its SE was 0.061 and
the gate sat 0.32 SE from the point estimate, so it could not adjudicate 0.68 vs 0.70. Any doc still
saying the holdout gate is "2.1 points short" is quoting a retired framing of a measurement that was
noise.

⚠️ **The replacement did not clear either, and the 0.710 above is the leaking figure.** Its fold
holds out the year but not the station. The honest replacement is the joint station-and-year fold:
**0.622**, 95% CI [0.571, 0.670], P(AUC ≥ 0.70) = **0.001**. Do not quote 0.710 as a passing gate.
The calibration rows in the table remain valid — calibration was never the leaking quantity.

### Label set

Source of truth: `outputs/soiling/training_matrix.parquet`.

| Quantity | Value |
|---|---|
| total rows | **1,002** |
| annual panel rows (`is_summary=False`) | **891** across **146** stations |
| summary-only censored rows (`is_summary=True`) | **111** across 111 stations |
| distinct stations in the matrix | **257** |
| panel years | **15**, 2008–2022 |
| states | **15** |

Common errors to watch for: "109 summary rows / 1,000 total" (the 109 is a *different* quantity,
the 109 of 255 stations reporting IWSR > 0.99 in
[ECONOMICS_GROUNDING_20260809.md](ECONOMICS_GROUNDING_20260809.md) §455), and "6 states", which
never matched the 15 in the same sentence. The 255 in older docs is the station count in the source
NREL CSVs; the matrix carries 257.

### Regional generalization

Source of truth: `outputs/soiling/regional_holdout.json`. Reproduce with
`PYTHONPATH=. conda run -n solar-soiling python scripts/predict/regional_holdout.py --regions 8 --seeds 3 --out-json outputs/soiling/regional_holdout.json`.

⚠️ **The table below is the superseded 2026-08-30 measurement.** It carried two defects, both found
2026-09-22: the fold has **no year axis**, and `regional_holdout.py` read hyperparameters from a
config key that does not exist, so it had always silently fitted a **stock** model rather than the
production one. Corrected figures are in the block underneath; quote those.

| Feature set | Pooled out-of-region AUC | Per-region range |
|---|---|---|
| **production (40)** — ⚠️ superseded | **0.6774** | 0.406 – 0.912 |
| trim: −tilt −deadAQ (33) | 0.6759 | 0.469 – 0.900 |
| weather+physics (27) | 0.6374 | 0.559 – 0.930 |
| +location (30) | 0.6623 | — |

Regions are k-means clusters on (lat, lon), 8 of them, 992 rows scored.

**Corrected (2026-09-22), and these are the numbers to quote.** Source:
`outputs/soiling/audit/regional_holdout_audit.json` (⚠️ gitignored, see the Stage 2 banner).

| Fold | Params | Pooled | Largest region (n=677) |
|---|---|---|---|
| region only | stock — *reproduces the published 0.6774/0.548* | 0.6748 | 0.5504 |
| region only | production | 0.6900 | 0.5582 |
| **region + year** | **production** | **0.6550** | **0.5272** |

Fixing the params *raised* the number and adding the year axis lowered it further; the two defects
were pulling in opposite directions, which is why the error survived review. **The conclusion is
unchanged and is strengthened, not weakened: the model does not generalize to a region unlike its
training geography** — the largest region scores 0.527, nearer chance than the 0.548 previously
reported.

**Benchmark future work against 0.655 pooled / 0.527 worst region** (not 0.6774/0.548, and not
0.5313). The
"hold out Arizona = 0.5313" block in [PVDAQ_LANE_HANDOFF_20260831.md](PVDAQ_LANE_HANDOFF_20260831.md)
§1 does not reproduce from anything committed: the script has no Arizona-by-name mode and no
349-row mode. See §4b there for the full note.

Since 2026-08-31 `regional_holdout.py` takes `--matrix` and `--feats` and records both in its
output, so every future result is traceable to the label set that produced it. Pass them explicitly
for anything that is not the NREL default.

### PVDAQ fleet — the balanced label set being built to fix this

Source of truth: `scripts/analyze/pvdaq_fleet_extract.py --status` (a live count, not a fixed one).

| Quantity | Value |
|---|---|
| residential systems | 1,629 |
| **readable** (two-channel, daily aggregate feed) | **1,359** |
| unreadable (multi-channel sub-daily schema) | 270, excluded by default |
| readable fleet: west of -114 / east of -100 | **46.9% / 45.6%** |
| NREL for comparison: west / east | 81.0% / 5.2% |
| Koppen classes | 16 |
| validated against NREL on shared ground | +0.16 pts, 95% CI [-0.62, +0.91], n=24 |

**The denominator is 1,359, not 1,629** — quoting 1,629 overstates the fleet by 20%. The gate
itself is **specified but not yet runnable**: nothing converts fleet labels into a feature matrix
yet. See [PVDAQ_LANE_HANDOFF_20260831.md](PVDAQ_LANE_HANDOFF_20260831.md) §4a.

---

## AOI and product

Source of truth: `outputs/aoi/santa-cruz-w2-21cm/` and
`../BBF-Website/public/tools/arrays_data.manifest.json`.

| Quantity | Value | Note |
|---|---|---|
| detected polygons in the AOI | **3,362** | `arrays.geojson`, and what the dashboard renders |
| **sites** after parcel clustering | **1,865** | economics runs per site, ~1.80 polygons/site at 21cm |
| sites with positive Regular Soiling planning net | **91 of 1,865 (4.879%)** | `econ_summary.json`, 2026-09-04; April-September dry-season scenario, July clean |
| Regular Soiling recovery, professional / basic | **0.44496 / 0.34608** | disclosed July planning scenario; a perfect July reset protects 0.4944 of annual Regular Soiling value |
| weather-trajectory recovery sensitivity | **0.045** | historical SOMOSclean best-date result; retained for comparison, not the public planning default |
| model importance on AOI-constant features | **58.1%** | measured 2026-08-12; **supersedes 21.8%**, which counted only absent features and missed all-NaN and low-variance ones |
| within-AOI loss spread (p10–p90) | 0.76 pts | valid for level, **not** for ranking homes |
| model_version on the shipped run | `rfdetr-w2-20260807` | `detect/manifest.json` |

**"334 arrays" is dead.** It was the 60cm `santa-cruz-outreach-v1` pilot, two generations back. Any
doc describing the live dashboard as 334 arrays is stale. Say 1,865 sites, or 3,362 polygons, and
say which one you mean.

### Three different multipliers that all look like "2.8x"

These get conflated. They are unrelated.

| Multiplier | What it means | Status |
|---|---|---|
| **2.6×** | Rise in the value of a lost kWh across the AOI once tariff vintage is measured | live |
| **2.8×** | A NEM 2.0 legacy home vs the current tariff, per lost kWh | live, and the sharpest targeting signal found |
| ~~2.8×~~ | Between-system spread vs per-label uncertainty | **RETRACTED.** n=12 across different metros, so it carried climate variation the fixed effects remove. Replaced by **1.7×** ([HANDOFF_20260827.md](HANDOFF_20260827.md) §4) |

---

## Repo health

| Quantity | Value | Command |
|---|---|---|
| `make test-fast` | **492 passed, 3 skipped, 2 deselected** (2026-09-24); ~3m30s cold in CI, <1m warm locally | `make test-fast` |

Measured 2026-08-31, after `tests/test_canonical_numbers.py` added 38 cases (it was 432 before).
Supersedes "248 tests" and "246 passed, 5 skipped". The suite grows; an older count is not a sign
of a broken environment, and this row is expected to move whenever tests are added.

The *reasoning* attached to the old count in [ONBOARDING.md](ONBOARDING.md) §1 is still correct and
still worth reading: `solarsoiled.paths.REPO_ROOT` follows the editable install rather than your
working directory, so a suite that finds another clone's `models/registry.yaml` will pass on
somebody else's data. If your count differs from the one above, re-run `make bootstrap` from the
directory you are actually working in before assuming anything else.
