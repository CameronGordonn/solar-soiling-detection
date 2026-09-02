# Rules: Stage 2 — Soiling Risk Model

Applied when working on `scripts/predict/`, `scripts/analyze/`, `src/risk/`, `configs/soiling/`, or anything touching the XGBoost risk model, feature engineering, or weather/AQ data.

## Current status

- **ECONOMICS GROUNDING PASS, 2026-08-09 — the dollar chain was audited and the product
  does not clear in coastal Santa Cruz.** Full writeup: `docs/ECONOMICS_GROUNDING_20260809.md`.
  Zero of 1,865 sites show a positive expected net, at any rate to $0.70/kWh, any system
  size, 2,000 MC draws each. This is kill-risk **C1 confirmed**, not a bug to fix.
  - **Scope, added 2026-08-10 (doc §12): that zero is on RECOVERABLE soiling only.** NREL
    defines IWSR as the insolation-weighted mean of daily soiling ratios *assuming perfect
    cleaning*, so a permanent wash-only layer is excluded by construction from the labels,
    from `sl_sat=0.08`, and from `recovery_frac=0.045` — they do not double-count, they are
    disjoint channels. Nothing here measures the permanent one. Quote the result as "zero
    of 1,865 on recoverable soiling". Verify with
    `scripts/analyze/persistent_soiling_probe.py`. Also note `loss_model.py` predicts
    recoverable loss, not total output deficit, since it trains on `(1-iwsr)*100`.
  - **`recovery_frac = 0.90` was the error carrying the product — measured 0.045, a 20x
    overstatement.** One clean buys ~one array-month out of twelve: at SOMOSclean k=15 the
    array re-soils to its annual mean in ~2 weeks and Santa Cruz gets 27 heavy-rain resets
    a year. New module `src/risk/recovery.py`; validated because the same k reproduces
    NREL's measured coastal-CA 4.70% annual loss (model gives 5.06%). Robust to k: even
    k=60 in Phoenix gives only 0.125. With 0.90 restored, 44.8–95.4% of the AOI cleans.
  - **`RISK_TO_LOSS_PCT = 8.0` is REMOVED** — it multiplied a calibrated *classification
    probability* by 8. Replaced by `src/risk/loss_model.py`: three XGBoost quantile heads
    on `(1-iwsr)*100`, spatial CV, **CQR-conformalised** (raw PI coverage 0.558 → 0.802).
    OOF MAE 1.75 pts, Spearman 0.310, spread ratio 0.481. Train with
    `scripts/predict/train_loss_regressor.py`. **No risk_score → loss_pct fallback exists
    any more, deliberately** — callers fall back to `BASE_SOILING_PCT` and flag it.
  - **The AOI variance collapse was NOT caused by the ×8 mapping.** The regression head
    only moves within-AOI spread 0.61 → 0.78 pts. Cause: NREL labels are *station-level*,
    and AOI feature SDs are 0.3–23% of training SDs — there is no within-AOI signal to
    learn. **58.1%** of model importance sits on features that are effectively constant across
    the AOI (measured 2026-08-12; **supersedes the earlier 21.8%**, which counted only the
    absent features and missed the all-NaN and low-variance ones). Because it is 58.1% and
    structural, supplying the missing `worldcover_*` / `tilt_deg` columns is **not** the cheap
    fix the 21.8% reading implied.
  - **`BASE_RATE` is now sourced** (`src/risk/rates.py`): retail offset $0.4573/kWh vs NBT
    export $0.0392/kWh, blended by midday self-consumption σ. Only **5.5%** of PV output
    lands in the 4-9pm peak, so soiling losses are ~94% off-peak. Default (NBT no-battery)
    **$0.165 — below the retired flat $0.25**. NEM 2.0 legacy homes are worth **2.8×** more
    per lost kWh: the sharpest targeting signal found. Rates come from the bill-reconciled
    `energy-advisor` tariff specs (±$0.22/mo over 11 real PG&E+3CE bills).
  - **Economics run per SITE, not per polygon** (`src/risk/site_cluster.py`, parcel APN +
    proximity fallback). 21cm fragments 1.80 polygons/site vs 1.30 at 60cm; per-polygon
    costing overcharged a 4-way-split roof 2.3× ($600 vs $264).
  - `array_recommendation_mc()` propagates loss-PI, area (1σ=0.26, the measured 21cm/60cm
    shift), rate band and recovery timing into `prob_net_positive`.
  - Still `UNSOURCED` and flagged in code: `PACKING_FACTOR` 0.90, `MIN_PRO_SERVICE` $150,
    the per-panel rate schedule, NBT σ=0.30. The ACC table is **SDG&E's standing in for
    PG&E's**. `MIN_PRO_SERVICE` decides most residential cases alone — get three quotes.

- **Best run:** `run_optionb`. **Quote 0.712 spatial-CV AUC, not 0.728.** Three real values exist on disk and get conflated: **0.728** is `run_optionb`'s stored `metrics.json` figure, **0.712** (`abl_full40`) is that same 40-feature set re-measured 2026-08-06 under the current `model.yaml` and is the honest current number, **0.746** is `run_regularized2022`, the default-config reference. Single-year-2022 holdout is **0.679** for `run_optionb` and **0.680** for `run_regularized2022` — do not attribute 0.680 to `run_optionb`. Full table: [docs/CANONICAL_NUMBERS.md](../../docs/CANONICAL_NUMBERS.md).
- **Phase 2 is GA-ready — all three gate conditions clear on the honest evaluation (2026-07-05).** (1) Spatial-CV AUC ≥0.70 ✓ (0.728 as recorded for `run_optionb`; **0.712 re-measured**, so the margin is ~1 point, not 3). (2) Pooled out-of-year AUC 0.710 ≥0.70 ✓. (3) Calibration retained out-of-year ✓ (Brier 0.218 < base-rate 0.250, ECE 0.046 <0.10). Model quality is *done* **within the training geography** — further AUC/feature/hyperparameter chasing on this label set is below measurement resolution (SE 0.017). **This does not mean the model generalizes.** Measured 2026-08-30 (`outputs/soiling/regional_holdout.json`): pooled out-of-region AUC **0.677**, largest region (n=677) **0.548**, against ~0.73 for a random split. 81% of NREL rows sit west of -114, so the model has effectively seen the Southwest. Never write that it works "in any region"; adding features does not fix it. The open Stage 2 work is a geographically balanced label set (PVDAQ), not more tuning — see [docs/PVDAQ_LANE_HANDOFF_20260831.md](../../docs/PVDAQ_LANE_HANDOFF_20260831.md). Status ships `beta` until Cameron flips the registry.
- **Holdout evaluation replaced (2026-07-05) — `scripts/predict/holdout_ci.py`.** The single-year-2022 gate was statistically unresolvable at n=97 (AUC 0.680 ± 0.061 Hanley-McNeil SE, 95% CI [0.557, 0.797], P(AUC≥0.70)=0.37 — the gate sits only 0.32 SE away; a 97-row year cannot adjudicate 0.68 vs 0.70). The tool instead runs a **rolling leave-one-year-out holdout across all 15 panel years (2008–2022)** and pools every row's out-of-its-year prediction (n=891): **point AUC 0.710, HM-SE 0.017, bootstrap 95% CI [0.676, 0.742], P(AUC≥0.70)=0.70**, and pooled out-of-year **calibration** (each training-year isotonic map applied to its held-out year): **Brier 0.218 (base-rate ref 0.250), ECE 0.046, MCE 0.126.** The AUC point estimate clears 0.70; the CI straddles it (lower bound 0.676) — temporal generalization is *at* the gate, not below it. **Gate wording changed** to "pooled out-of-year AUC ≥ 0.70 (point; CI reported) **and** calibration retained"; we did **not** adopt the stricter lower-CI≥0.70 criterion (not cleared at 0.676, a materially harder bar than the original point gate). Reproduce: `PYTHONPATH=. conda run -n solar-soiling python scripts/predict/holdout_ci.py [--out-json runs/soiling/<run>/holdout_ci.json]` — reads cached `training_matrix.parquet` + `run_optionb/feature_names.json`, **no Open-Meteo refetch**. Result recorded at `runs/soiling/run_optionb/holdout_ci.json`; `compare_runs.py` surfaces `ooy_auc / ooy_ci95 / ooy_ece / cal_ok` when that file is present. The single-year-2022 per-year number (0.680, SE 0.061, P=0.37) reproduces exactly, confirming faithfulness. Weak year to note: 2019 (per-year AUC 0.626); early years 2008–2012 are near-single-class. (Cached matrix carries `is_feedback=True` on all rows → the ×3 feedback multiplier is a uniform no-op; summary-row ×0.5 relative downweight is preserved, matching production. Also fixed a latent `compare_runs.py` bug from the `scripts/` reorg: `repo_root` used `parent.parent` → resolved to `scripts/`, silently finding zero runs; now `parents[2]`.)
- **Regularization tuned + adopted (2026-07-05):** 40 features on ~900 rows overfit at `max_depth=5`. New `configs/soiling/model.yaml` default is `max_depth=4, reg_lambda=5.0`. Same-matrix ablation (cached `training_matrix.parquet`, 7-seed mean): spatial-CV 0.739→0.746 (robust, ±0.004), holdout 0.678→0.690 mean (within seed noise ±0.014). Reference run: `runs/soiling/run_regularized2022` (built from cached matrix, no refetch; seed-42 CV 0.746 / holdout 0.680). CV gain is real; holdout gain is not seed-reliable.
- **`run_recalibrated_slsat` never landed** — the earlier "active retrain, results pending" line was stale; no such run exists on disk. Do not go looking for it.
- **MERRA-2 exhausted:** `run_merged_merra2` tried and hurt performance (0.716 CV / 0.666 holdout). Cache is at 1.3 GB. Do not retry.
- **Leakage warning:** `run_somosclean_v2_calibrated` and `run_somosclean_v4` show CV AUC=1.0 — both used SOMOSclean-derived labels while including `somosclean_sl_proxy` as a feature. Feature = label = perfect leakage. These runs are invalid.
- **SOMOSclean/Kimber params updated (2026-06-01):** sl_sat 0.10→0.08, k 30→15, calibrated against coastal CA NREL data. All four locations updated: `physics_score.py`, `labels.py` (both functions), `features.yaml`, `score_alternative_models.py`. Santa Cruz dashboard re-scored with new params.
- Active label source: `nrel_merged` — **1,002 total rows**: 891 annual panel rows (146 stations, years 2008–2022) + **111** summary-only censored rows; 257 stations, 15 states. (The "109" in older copies is a different quantity: the 109 of 255 stations reporting IWSR > 0.99 in `ECONOMICS_GROUNDING_20260809.md`.)
- Full runbook: `docs/SOILING_STAGE2_GUIDE.md`

## Research scripts — predict pipeline

```bash
# One-time ingestion + static feature pre-computation
PYTHONPATH=. python scripts/predict/ingest_nrel_soiling_map.py        # NREL JSON → CSV labels
PYTHONPATH=. python scripts/predict/build_static_features.py          # elevation + WorldCover + OSM (~15 min)

# Train
PYTHONPATH=. python scripts/predict/train_risk_model.py --run-name run_latest
PYTHONPATH=. python scripts/predict/train_risk_model.py --run-name run_holdout2022 --holdout-year 2022
# With MERRA-2 (when cache ready):
NASA_EARTHDATA_TOKEN=<token> PYTHONPATH=. python scripts/predict/train_risk_model.py \
    --run-name run_merged_merra2 --holdout-year 2022

# Compare runs
PYTHONPATH=. python scripts/predict/compare_runs.py

# Build feature matrix for detected arrays
PYTHONPATH=. python scripts/analyze/build_risk_features.py --arrays outputs/array_features.geo.parquet

# Score arrays (auto-applies calibrator.joblib)
PYTHONPATH=. python scripts/predict/predict_risk.py --model runs/soiling/<run_name>/model.ubj

# Validate + calibrate physics alternatives
PYTHONPATH=. python scripts/predict/validate_against_nrel.py
PYTHONPATH=. python scripts/predict/calibrate_somosclean.py

# Score with SOMOSclean + Kimber from SQLite cache (no HTTP)
PYTHONPATH=. python scripts/predict/score_alternative_models.py --partner-id santa-cruz-outreach-v1
```

## `src/risk/` package

| Module | Purpose |
|--------|---------|
| `feature_engineering.py` | Rolling-window aggregation (7/30/90d) over daily weather/AQ; builds feature rows |
| `labels.py` | Label sources: `load_nrel_panel`, `kimber_synthetic_labels`, `somosclean_panel_labels` |
| `location_features.py` | Static features: elevation, ESA WorldCover, OSM proximity (highways/agriculture) |
| `weather_client.py` | MERRA-2 + Open-Meteo daily aggregates (precipitation, wind, humidity, PM2.5/PM10) with SQLite cache |
| `risk_model.py` | XGBoost training + spatial-CV + isotonic calibration; `train_risk_model`, `predict_risk`, `load_model` |
| `physics_score.py` | SOMOSclean physics-based scorer (daily trajectory → normalized [0,1] risk) |

Import style: `from src.risk.feature_engineering import build_feature_row` (with `PYTHONPATH=.`)

## Key paths — Stage 2

| What | Where |
|------|-------|
| Run outputs | `runs/soiling/<run>/{model.ubj, feature_names.json, feature_medians.json, metrics.json, calibrator.joblib}` |
| Training matrix | `outputs/soiling/training_matrix.parquet` |
| MERRA-2 cache | `.cache/soiling/merra2.sqlite` — gitignored |
| NREL labels | `data/external/nrel_soiling_map_annual.csv` — gitignored |
| Static features | `data/external/static_features.csv` — gitignored |
| Risk model configs | `configs/soiling/{california.yaml, features.yaml, model.yaml}` |
| Risk GeoJSON output | `outputs/soiling_risk.geojson` |
