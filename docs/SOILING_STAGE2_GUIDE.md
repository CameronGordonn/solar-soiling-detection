# Stage 2 — Soiling Risk Model (XGBoost) Partner Guide

**Goal:** per-array soiling risk score in `[0, 1]` from weather + location + structural features. Complements Stage 1 detection: Stage 1 tells us *where* arrays are, Stage 2 tells us *how likely each is soiled right now* without needing visual soiling detection (too coarse at 0.6 m GSD).

**Status:** v2 pipeline — real NREL annual-IWSR panel labels (255 stations × up to ~15 years of per-year observations), physics-prior Kimber IWSR as a feature, ESA WorldCover land-cover + OSM distance features pre-computed per station, isotonic-calibrated XGBoost with spatial CV and optional temporal holdout. The Kimber physics-proxy path remains available as a bootstrap/ablation.

**Honest current baseline (re-measured 2026-08-06 on the cached matrix, run `abl_full40`):** the production 40-feature set under the current `model.yaml` gives mean spatial-CV AUC **0.712** (seed 42; **0.721 ± 0.006** over 7 seeds) and pooled out-of-year AUC **0.7095** (bootstrap 95% CI [0.676, 0.742]), with calibration retained (Brier 0.215 vs base-rate 0.250, ECE 0.031). All three Phase-2 gates still clear, but the spatial-CV margin over the 0.70 bar is ~1 point, not the ~3 points the older 0.728 figure implied — describe it as *at* the gate, not comfortably over. See [Feature ablation](#feature-ablation--how-much-comes-from-the-kimber-physics-prior-2026-08-06) for why 0.728 no longer reproduces.

*Leakage-patch history (2026-04-24, `run_k_patched_holdout2022`):* mean spatial-CV AUC 0.630 / holdout-2022 0.655. Runs before that reported ~0.668 mean CV — inflated by per-fold imputation leakage (medians computed on the full matrix before splitting). The patched pipeline learns medians on training data only, persists them to `feature_medians.json` for inference, drops features below 20% coverage, and clamps Open-Meteo AQ fetches to the CAMS start date (2013-01-01). **Any run dated on or before 2026-04-23 is pre-patch and is not comparable to anything above.**

---

## Quick run (end-to-end)

```bash
conda activate solar-soiling
pip install -r requirements.txt          # requests-cache, pystac-client, planetary-computer, xgboost, pyarrow, joblib

# Unit tests (offline, no API calls)
PYTHONPATH=. pytest tests/test_weather_client.py tests/test_feature_engineering.py -v

# One-time data prep
PYTHONPATH=. python scripts/predict/ingest_nrel_soiling_map.py   # NREL JSON → CSV + annual panel CSV
PYTHONPATH=. python scripts/predict/build_static_features.py     # elevation + WorldCover + OSM (one-time, ~15–20 min for 255 stations)

# Train (default: nrel_panel labels, Kimber feature on, regression or binary via model.yaml)
PYTHONPATH=. python scripts/predict/train_risk_model.py --run-name run_latest

# Temporal holdout variant (train on pre-2022, test on 2022)
PYTHONPATH=. python scripts/predict/train_risk_model.py --run-name run_holdout2022 --holdout-year 2022

# Compare any saved runs
PYTHONPATH=. python scripts/predict/compare_runs.py

# Ablation: synthetic Kimber-only labels (edit configs/soiling/california.yaml → label_source: kimber_proxy)

# Build features for detected arrays (requires scripts/07)
PYTHONPATH=. python scripts/analyze/build_risk_features.py --arrays outputs/array_features.geo.parquet

# Score arrays (automatically applies the saved isotonic calibrator if present)
PYTHONPATH=. python scripts/predict/predict_risk.py --model runs/soiling/<run_name>/model.ubj
```

Output: `outputs/soiling_risk.geojson` — each detected array polygon gets a calibrated `risk_score` column in `[0, 1]`.

---

## Architecture

```
Open-Meteo (ERA5 + CAMS)          array_features.geo.parquet
         │                                     │
         ▼                                     ▼
weather_client.py               location_features.py
         │                                     │
         └──────────────┬──────────────────────┘
                        ▼
              feature_engineering.py   (rolling 7/30/90d aggregates)
                        │
         ┌──────────────┴──────────────┐
         ▼                             ▼
  labels.py (Kimber OR NREL)   inference matrix (per-array features)
         │                             │
         ▼                             ▼
  risk_model.py (XGBoost + spatial CV) → runs/soiling/<run>/model.ubj
         │                             │
         └──────────────┬──────────────┘
                        ▼
               soiling_risk.geojson
```

---

## Label source

Controlled by `configs/soiling/california.yaml:label_source`:

- **`nrel_panel`** (default): the per-(station, year) NREL panel-label CSV used by the current config. This is the preferred path for Stage 2 because it aligns weather windows to the actual observation year.

- **`nrel_soiling_map`**: ingests the NREL PV Soiling Map (Micheli/Deceglie/Muller, 255 US stations). Request from NREL at https://www.nrel.gov/pv/soiling or extract from the Micheli 2019 *Progress in PV* supplementary. This is the summary CSV path when you do not need the panel-year breakdown.

- **`kimber_proxy`**: generates IWSR labels from historical weather using the Kimber 2007 model (daily dust deposition ∝ PM2.5, reset by any day with precipitation ≥ 1 mm). Use this only as a bootstrap fallback when you want the pipeline to train without external label files. Results are *physically plausible but uncalibrated* — treat the model as a ranking tool, not an absolute loss estimator.

  **Required CSV schema** for the summary NREL CSV at `data/external/nrel_soiling_map.csv` (extra columns ignored):

  | column | type | notes |
  |--------|------|-------|
  | `station_id` | str | unique per row |
  | `latitude` | float | EPSG:4326 decimal degrees |
  | `longitude` | float | EPSG:4326 decimal degrees |
  | `iwsr` | float | insolation-weighted soiling ratio; 1.0 = clean, 0.95 = 5% annual loss |

  Optional: `soiling_rate_pct_per_day`, `start_date`, `end_date`. Once the file is in place, flip `label_source: nrel_soiling_map` in [configs/soiling/california.yaml](../configs/soiling/california.yaml) and re-run script 10.

Switching sources is a one-line config change; nothing else in the pipeline knows or cares which labels were used.

---

## Features

| Group | Columns | Source |
|-------|---------|--------|
| Weather (rolling 7/30/90d) | `precip_*d_mm`, `dry_day_streak`, `days_since_rain_5mm`, `wind_speed_10m_max_*d_mean`, `relative_humidity_2m_mean_*d_mean`, `temperature_2m_max_*d_mean` | Open-Meteo ERA5 archive |
| Air quality (rolling 7/30/90d) | `pm2_5_*d_mean`, `pm10_*d_mean` | Open-Meteo CAMS |
| Physics prior | `kimber_iwsr_proxy`, `kimber_iwsr_7d_mean`, `kimber_iwsr_30d_mean`, `kimber_iwsr_90d_mean` | Kimber 2007 IWSR computed on the same daily weather stream. **Ablated 2026-08-06: contributes no measurable skill on top of the learned features** — see [Feature ablation](#feature-ablation--how-much-comes-from-the-kimber-physics-prior-2026-08-06) |
| Static location | `elevation_m`, `nlcd_class` (WorldCover), `worldcover_cropland`, `worldcover_built_up`, `worldcover_bare`, `worldcover_tree`, `worldcover_grass`, `distance_to_highway_m`, `distance_to_agriculture_m` | Open-Meteo elevation, ESA WorldCover 2021 (Planetary Computer), OSM Overpass — pre-computed by [scripts/predict/build_static_features.py](../scripts/predict/build_static_features.py) |
| NREL covariates | `tilt_deg`, `months_in_data_set`, `mounting_Fixed`, `mounting_Tracking`, `measurement_type_PV System`, `measurement_type_Soiling Station` | NREL soiling-map CSV |
| Geometric (inference only) | `area_m2`, `orientation_deg`, `compactness`, `neighbor_count_100m`, `distance_to_nearest_array_m` | [scripts/analyze/extract_array_features.py](../scripts/analyze/extract_array_features.py) |

All Open-Meteo calls pass through `requests-cache` — first request for a given (lat, lon, window) hits the API, subsequent requests hit `.cache/soiling/openmeteo.sqlite`. Open-Meteo 429s trigger exponential-backoff retries in `src/risk/weather_client.py` so large panel runs don't silently drop rows. Overpass queries are cached separately in `.cache/soiling/overpass.sqlite`.

## Panel labels (station × year)

`scripts/predict/ingest_nrel_soiling_map.py` emits two CSVs from the NREL JSON:

- `data/external/nrel_soiling_map.csv` — one row per station, with a single summary IWSR. Historical/v1 path.
- `data/external/nrel_soiling_map_annual.csv` — one row per (station, year) from the NREL `Annual IWSR` block. **This is the default training source**; each row is matched to a year-specific weather window (`as_of = Dec 31`, `lookback = 365 d`) instead of a single today-centered snapshot. Yields ~891 rows across 15 years.

Switch between them via `label_source: nrel_panel | nrel_soiling_map | kimber_proxy` in `configs/soiling/california.yaml`.

## Sample weights, calibration, and temporal holdout

- **Sample weights** (`model.yaml:sample_weights.mode`): `iwsr_ci_width` weights each training row by `1 / (iwsr_upper − iwsr_lower)`, clipped to the 5–95th percentile. Stations with tighter NREL confidence intervals pull the model harder.
- **Isotonic calibration**: `train_risk_model` collects out-of-fold predictions during spatial CV, fits `sklearn.isotonic.IsotonicRegression`, and saves the result to `runs/soiling/<run>/calibrator.joblib`. Training also persists `feature_medians.json` so inference uses the same NaN-imputation values learned on train/CV instead of recomputing medians on the scored batch.
- **Temporal holdout**: pass `--holdout-year 2022` (panel mode only). Trains on all other years, evaluates separately on the held-out year. Use to catch year-to-year drift that spatial CV alone misses.
- **Regression head** (`model.yaml:target_mode: regression`): fits `reg:squarederror` on continuous IWSR instead of the thresholded binary label. Reports Spearman rank correlation alongside AUC. Binary is the more robust head at N ≲ 200; regression is preferred once the panel path is fully warm (N ≈ 900).

---

## Spatial cross-validation

Stations are clustered by lat/lon into ~10 km buckets and held out as whole groups (`GroupKFold`). Random KFold would leak neighboring stations between folds and inflate AUC by 5–15 points. Acceptance gate (in `configs/soiling/model.yaml`): mean CV AUC ≥ 0.70.

**Why 10 km, not 50 km:** at 50 km, the LA / Inland Empire region (~40 stations) bucketed into a single cluster that took 37% of the training data into one fold. That fold's near-random AUC dragged down the mean while the other four folds (multi-cluster, multi-region) reported 0.62–0.75 — and conversely the mean was *inflated* relative to a balanced split by the asymmetric averaging. Dropping to 10 km splits LA into ~12 contiguous bins that GroupKFold spreads across all five folds (159 rows each), producing the honest 0.63 mean. See `scripts/research/diag_soiling_folds.py` for the per-fold composition reporter used to diagnose this.

---

## Feature ablation — how much comes from the Kimber physics prior? (2026-08-06)

**Headline: the Kimber physics prior contributes nothing measurable on top of the learned
weather/AQ/static features.** Dropping all four `kimber_iwsr_*` columns changes spatial-CV AUC by
**−0.004** and pooled out-of-year AUC by **−0.006** — both an order of magnitude inside the noise
(seed SD 0.003–0.006; out-of-year Hanley-McNeil SE 0.017). Dropping the SOMOSclean features as well
(7 physics features gone, 33 remaining) costs the same nothing. The learned features alone retain
**97%** of the model's out-of-year skill above chance.

This supersedes the 2026-04-23 `nrel_only` (0.516) vs `nrel_kimber_feature` (0.684) comparison,
which is **not valid evidence for the physics prior**: both runs predate the per-fold imputation
leakage fix (2026-04-24), used 50 km CV clusters, `iwsr_ci_width` sample weights, `nrel_panel`
labels, and a training matrix that has since been overwritten. Their 17-point gap does not survive
re-measurement on the patched pipeline.

### Setup — everything except the feature list is held identical

Cached `outputs/soiling/training_matrix.parquet` (1002 rows: 891 NREL annual panel + 111
summary-only), `nrel_merged` labels, 10 km GroupKFold spatial CV at 5 folds, `max_depth=4` /
`reg_lambda=5`, seed 42, uniform sample weights with summary rows ×0.5, per-fold median imputation
learned on training folds only. **No weather refetch** — the Open-Meteo quota is untouched. Every
variant runs through the production `train_risk_model` path; only the feature list and (for the
decomposition arms) the XGBoost block vary.

### Results

| Run (2026-08-06) | Feats | Spatial-CV AUC (seed 42) | CV 7-seed mean ± SD | Pooled OOY AUC | OOY bootstrap 95% CI | P(≥0.70) |
|---|---:|---:|---:|---:|---:|---:|
| `abl_full40` — reference (= `run_optionb` feature set) | 40 | 0.7118 | 0.7209 ± 0.0060 | **0.7095** | [0.676, 0.742] | 0.70 |
| `abl_no_kimber` — **minus the 4 Kimber features** | 36 | 0.7149 | 0.7167 ± 0.0029 | 0.7038 | [0.669, 0.736] | 0.59 |
| `abl_no_physics` — minus Kimber **and** SOMOSclean | 33 | 0.7204 | 0.7199 ± 0.0035 | 0.7040 | [0.670, 0.737] | 0.59 |
| `abl_physics_only` — physics priors alone | 7 | 0.6920 | 0.6915 ± 0.0029 | 0.6178 | [0.580, 0.654] | 0.00 |
| `abl_kimber_only` — Kimber alone | 4 | 0.6789 | 0.6812 ± 0.0029 | 0.6111 | [0.573, 0.648] | 0.00 |

**Deltas vs the reference** (negative = worse without the features):

| Ablation | Δ CV (seed 42) | Δ CV (7-seed mean) | Δ pooled OOY |
|---|---:|---:|---:|
| drop 4 Kimber features | **+0.0031** | **−0.0042** | **−0.0057** |
| drop all 7 physics features | **+0.0086** | **−0.0010** | **−0.0055** |

Every delta is smaller than the seed-to-seed spread of the reference itself (±0.006) and ~3× smaller
than the out-of-year standard error (0.017). The sign is not even stable — on seed 42 the model is
*better* without the physics features. There is no recoverable effect here.

### Physics prior vs learned features — the share question

Measured as skill above chance (AUC − 0.50), as a share of the reference:

| Variant | Spatial CV | share | Pooled OOY | share |
|---|---:|---:|---:|---:|
| reference (40 feat) | 0.2209 | 100% | 0.2095 | 100% |
| learned only, no physics (33 feat) | 0.2199 | **99.6%** | 0.2040 | **97.3%** |
| physics only (7 feat) | 0.1915 | 86.7% | 0.1178 | **56.2%** |
| Kimber only (4 feat) | 0.1812 | 82.0% | 0.1111 | 53.0% |

**Stated plainly:** the learned features do the work. They reproduce essentially all of the model's
skill without any physics prior. The physics prior, on its own, is *not* worthless — Kimber alone
reaches 0.68 spatial-CV / 0.61 out-of-year, well above chance — but everything it knows is already
recoverable from the features it is computed from. Kimber IWSR is a deterministic function of the
same daily precipitation + PM2.5 stream that feeds `precip_*d_mm`, `dry_day_streak`,
`days_since_rain_5mm`, and `pm2_5_*d_mean`, so a gradient-boosted tree ensemble re-derives it. The
two are **substitutes, not complements**.

Note the split between the two columns: the physics prior holds 87% of the spatial-CV lift but only
56% of the out-of-year lift. It generalizes across *locations* far better than across *years* — it
is largely encoding climatology, which is stable per site but does not track year-to-year variation.
Physics-only is decisively below the gate out-of-year (CI [0.580, 0.654], P(≥0.70) = 0.00) and its
CI does not overlap the reference's.

**Implication:** `abl_no_physics` (33 features) is statistically indistinguishable from production on
both metrics and would remove the Kimber + SOMOSclean feature computation from the training path.
That is a simplification available at zero measured cost — but it is also zero measured *gain*, and
its OOY point estimate (0.7040) sits closer to the 0.70 gate than the reference's (0.7095). Not worth
churning production for on this evidence. Dropping these *features* would not affect the dashboard's
SOMOSclean/Kimber model tabs, which score independently via `scripts/predict/score_alternative_models.py`.

### Decomposing the historical 0.684 → 0.728

The old story attributed that +0.044 to "more features + regularized hyperparameters". Re-running the
2×2 on the current matrix with everything else held fixed shows **it was neither**:

Spatial-CV AUC, 7-seed mean:

| | old HP (`max_depth=5`, `reg_lambda=1`) | new HP (`max_depth=4`, `reg_lambda=5`) | HP effect |
|---|---:|---:|---:|
| **28-feat** (2026-04-23 set) — `abl_old28_oldhp` / `abl_old28_newhp` | 0.7141 | 0.7208 | **+0.0067** |
| **40-feat** (production set) — `abl_full40_oldhp` / `abl_full40` | 0.7144 | 0.7209 | **+0.0064** |
| **feature-set effect (28 → 40)** | +0.0004 | +0.0001 | |

Pooled out-of-year AUC:

| | old HP | new HP | HP effect |
|---|---:|---:|---:|
| **28-feat** | 0.7002 | 0.7054 | +0.0052 |
| **40-feat** | 0.7058 | 0.7095 | +0.0037 |
| **feature-set effect (28 → 40)** | +0.0057 | +0.0041 | |

Attribution of the historical +0.044:

- **Hyperparameters (depth 5→4, λ 1→5): +0.0064** — small but real and consistent across both feature
  sets and both metrics. This is the one genuine effect.
- **Feature set (28 → 40 columns): +0.0001** — nothing. The 12 extra columns (lat/lon, month,
  tilt, WorldCover, SOMOSclean) buy no spatial-CV skill; they buy ~+0.005 out-of-year, still inside SE.
- **Together: +0.0068, or ~16% of the +0.044.**
- **Residual ~0.037 (~84%) is attributable to neither.** It comes from everything else that changed
  between 2026-04-23 and now: the per-fold imputation leakage fix, `cluster_km` 50→10, sample weights
  `iwsr_ci_width`→uniform, label source `nrel_panel`→`nrel_merged` (+111 summary rows), and the
  training-matrix rebuild. The historical 0.684 cannot be exactly reproduced — its matrix was
  overwritten — so this residual is bounded, not decomposed further.

### Reproducibility finding: 0.728 is a fossil

`run_optionb/metrics.json` records mean spatial-CV AUC 0.7276 (folds 0.698 / 0.813 / 0.705 / 0.698 /
0.725). Re-running that exact feature set and config against the current cached matrix gives **0.7118**
(folds 0.676 / 0.762 / 0.761 / 0.619 / 0.742). The *fold-by-fold pattern* differs, not just the mean,
so this is not seed or hyperparameter drift — it is a different input matrix.

`run_optionb` was trained **2026-05-19**; `outputs/soiling/training_matrix.parquet` was rebuilt
**2026-06-09** by `run_regression_loss2022`, overwriting it in place. The rebuild refetched Open-Meteo
(ERA5 revises preliminary data, and the request cache expires ~30 days), added `humid_days_*` /
`dew_no_rain_days_*` columns, and moved the row count 1000 → 1002. The pre-rebuild matrix no longer
exists, so the old weather values cannot be diffed.

**Control confirming this diagnosis:** re-running `holdout_ci.py` on the reference reproduces
`run_optionb/holdout_ci.json` **bit-for-bit** — pooled AUC 0.7095217698044749, HM-SE
0.017252475343849945, CI [0.6760382548385608, 0.7420983043486418], all identical. That run was
executed 2026-07-05, *after* the June rebuild, so it was measured against the current matrix. The
XGBoost path is therefore exactly reproducible and the environment has not drifted; only the May-19
`metrics.json` is stale. (One exception: the pooled Brier moved 0.2177 → 0.2151 and ECE 0.046 → 0.031,
affecting the isotonic calibrator only, not the trees — consistent with a scikit-learn version change
in `IsotonicRegression` tie handling. Ranking metrics are untouched and calibration still passes.)

`run_regularized2022`'s recorded 0.7459 also does not reproduce (0.7246 here). It was an ad-hoc
build; its exact masking is not recoverable. Treat it as unverified.

**Process fixes this argues for** (not yet implemented): stamp each run's `metrics.json` with a hash
of the matrix it consumed, and write new matrices to a dated filename instead of overwriting, so a
stale number is detectable rather than silently wrong. `runs/` is gitignored — that is why these
numbers are recorded here in version control.

### Reproduce

```bash
# 1. Spatial CV for all 8 variants x 7 seeds; writes runs/soiling/abl_*/ (~4 min)
#    OMP_NUM_THREADS=2 avoids XGBoost thread thrash on this small matrix and is
#    bit-identical to the unpinned result; --all is the default variant set.
OMP_NUM_THREADS=2 PYTHONPATH=. conda run -n solar-soiling \
    python scripts/predict/ablate_features.py --all --seeds 42,0,1,2,3,4,5

# 2. Pooled leave-one-year-out AUC + bootstrap CI for a variant (~4 min each)
OMP_NUM_THREADS=2 PYTHONPATH=. conda run -n solar-soiling \
    python scripts/predict/holdout_ci.py \
      --feature-names runs/soiling/abl_no_kimber/feature_names.json \
      --out-json      runs/soiling/abl_no_kimber/holdout_ci.json

# 3. The pre-regularization hyperparameter arms add:
      --model-config configs/soiling/model_prepatch_hp.yaml
```

Supporting files added 2026-08-06: [scripts/predict/ablate_features.py](../scripts/predict/ablate_features.py)
(variant definitions live in its `VARIANTS` dict), [configs/soiling/model_prepatch_hp.yaml](../configs/soiling/model_prepatch_hp.yaml)
(measurement control — never train production with it), and a `--model-config` flag on
[scripts/predict/holdout_ci.py](../scripts/predict/holdout_ci.py).

---

## Sanity check on California outputs

After running end-to-end, the GeoJSON should show:
- Higher `risk_score` on arrays in the **Central Valley** (Fresno/Bakersfield — high PM2.5, long dry season, ag dust)
- Lower `risk_score` on arrays in **coastal Santa Cruz / SF Bay** (marine air + frequent rain)
- Highest scores in **the desert southeast** (Palm Springs / Barstow — bone dry + dust events)

If the map doesn't look like that, something is wrong with the feature stream (most likely the AQ fetch silently failing).

---

## Known gaps (deferred to v3)

- **PVDAQ label source**: NREL PVDAQ has ~44 GB of per-system performance time-series that could yield thousands of additional IWSR labels via the SRR extraction method. Path to the largest accuracy win; high engineering cost.
- **Dust-specific CAMS variables + wind direction**: CAMS has a `dust` AOD channel separate from PM; prevailing wind direction vs. nearby source matters for desert stations. Small to medium lift.
- **Explainability**: `predict_risk` returns a scalar. For per-array SHAP attributions, wrap with `shap.TreeExplainer(model)` in script 11 and add a `top_features` column.
- **Live scoring**: pipeline is batch-only. For periodic rescoring, schedule script 09 + 11 via cron — Open-Meteo cache expires every 30 days by default.
- **Multi-region**: `configs/soiling/california.yaml` is the only region config. The NREL panel data already covers 15 states; to train a region-specific model copy the file and set `bbox`.

---

## Troubleshooting

**`requests-cache` not installed:** `pip install requests-cache` (added to requirements.txt).

**Kimber bootstrap labels all zero (or all one):** the risk threshold (`iwsr_risk_threshold: 0.97`) may not match the seed stations' climate. Lower to 0.99 for more positives, raise to 0.95 for fewer. Check `outputs/soiling/training_matrix.parquet` to see the IWSR distribution.

**Single-class CV fold warnings:** means at least one spatial cluster has all-positive or all-negative labels. Either expand the seed station list in [scripts/predict/train_risk_model.py](../scripts/predict/train_risk_model.py) `DEFAULT_CA_STATIONS` or lower `cluster_km` in `configs/soiling/model.yaml`.

**`array_features.geo.parquet` missing:** run the full Stage 1 pipeline (`scripts/04` → `scripts/06` → `scripts/07`) first.

**Open-Meteo rate limits:** free tier is 10k calls/day. California has O(10²) stations + whatever detected arrays you have, well under the limit. If you hit rate limits during bulk backfill, the cache will serve repeats for free.

**AQ features mostly null:** if the training log reports PM feature coverage well below 50%, treat it as an upstream historical-AQ problem, not evidence that PM has no predictive value. The trainer now warns and drops extremely sparse features (<20% coverage by default) so the saved model does not silently depend on a broken feed.
