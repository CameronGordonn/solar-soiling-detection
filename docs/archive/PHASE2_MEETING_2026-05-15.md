> ⚠️ **ARCHIVED — meeting notes, 2026-05-15 (Cameron / Josh / Tyler).** Direction-setting for
> Stage 2, kept for the reasoning. What has changed since: Stage 2 **cleared all three gates**
> on 2026-07-05 (spatial-CV AUC 0.728, pooled out-of-year AUC 0.710, calibration retained) and
> model work is **done** — further tuning is below measurement resolution. More importantly, the
> 2026-08 economics audit showed the risk model contributes only **2.2% of per-home dollar
> variance**, so "which array is soiled" turned out not to be the question the product turns on.
> Live: [SOILING_STAGE2_GUIDE.md](../SOILING_STAGE2_GUIDE.md),
> [SOILING_LEVEL_INVESTIGATION.md](../SOILING_LEVEL_INVESTIGATION.md).

# Phase 2 — Soiling Risk Model: Strategy & Direction
### Meeting: Cameron / Josh / Tyler — May 15, 2026

---

## What Phase 2 Is Trying to Do

Phase 1 answers "where are the solar arrays?" Phase 2 answers "which of those arrays is likely soiled right now, and by how much?"

We can't see soiling directly in 0.6 m aerial imagery — dust accumulation on panels is invisible at that resolution. So instead of detecting soiling visually, we predict it: given what we know about a location's weather history, air quality, terrain, and land use, how likely is it that an array at that location has accumulated enough soiling to warrant cleaning?

The output is a single risk score per array, between 0 and 1, that flows downstream into a cleaning recommendation. That recommendation is where the revenue lives.

---

## Where We Stand

| Metric | Current | Gate |
|---|---|---|
| Spatial cross-validation AUC | 0.63 | ≥ 0.70 |
| Year-holdout AUC (2022) | 0.66 | ≥ 0.70 |
| Status | **Beta** | GA requires both gates |

The honest story: we're below the acceptance bar on both metrics. The number that was previously reported (~0.668) was inflated by a data leakage bug — imputation medians were being computed across the full dataset before folding, so the model was implicitly seeing validation data during training. That's fixed. The 0.63 / 0.66 numbers are leakage-free and trustworthy.

---

## Data Sources

### Labels — what the model is predicting

The ground truth comes from NREL's PV Soiling Map, a dataset of 255 US monitoring stations that report measured soiling rates as an **Insolation-Weighted Soiling Ratio (IWSR)**: a value where 1.0 means a perfectly clean array and 0.95 means 5% annual energy loss to soiling.

We use the **annual panel-level breakdown** of this data: one observation per station per year, going back up to ~15 years. This gives us roughly 890 training rows across the full dataset (641 for training, 76 withheld for temporal validation). Each row is matched to the weather conditions during that specific year — not a single today-centered snapshot.

A physics-derived fallback (Kimber model) is available that synthesizes soiling estimates from weather data alone, without needing NREL's measurements. We use it for ablation and sanity checks, not as the primary training signal.

### Features — what the model learns from

**Weather history** (from Open-Meteo ERA5 archive):
- Precipitation accumulation over 7, 30, and 90-day windows
- Dry day streaks and days since meaningful rainfall
- Wind speed, humidity, and temperature rolling averages

**Air quality** (from Open-Meteo CAMS reanalysis):
- PM2.5 and PM10 rolling averages over the same windows
- Note: CAMS data only goes back to 2013 — rows before that date have sparse AQ features, which currently limits our effective training set

**Physics prior** (Kimber model, as a feature):
- A computed estimate of soiling accumulation based on the daily dust-deposition physics from Kimber 2007
- Gives the model a structured prior for how soiling builds and resets with rain

**Static location features** (pre-computed once per station):
- Elevation
- Land cover type (ESA WorldCover — cropland, urban, bare ground, vegetation)
- Distance to nearest highway and agricultural area
- Array tilt, mounting type (fixed vs. tracking), measurement type

**Array geometry** (from Stage 1, at inference time):
- Area, orientation, compactness, proximity to neighboring arrays

The geographic pattern this should reproduce: Central Valley and desert southeast (Fresno, Palm Springs, Barstow) score high — long dry seasons, heavy agricultural and road dust. Coastal areas (Santa Cruz, SF Bay) score low — frequent rain, marine air. If the map doesn't look like that, the feature pipeline has a problem.

---

## How Training Works

### The target: binary risk classification

Each NREL station-year observation gets converted to a binary label: soiling risk **high** or **low**, based on whether the measured IWSR falls below a threshold (meaning the array lost more than a threshold percentage of energy to soiling that year). The model learns to classify each array-year as high or low risk.

We also support a **regression head** that fits directly to the continuous IWSR value, optimizing mean squared error rather than binary cross-entropy. Regression is theoretically better once we have enough data; the binary head is more robust at our current sample size (~640 rows). The direction we want to move is toward the regression head as data grows.

### The loss function

- **Binary mode** (current): cross-entropy loss. The model learns to output the probability that an array-year exceeds the soiling threshold.
- **Regression mode** (target): squared error loss on the continuous IWSR. Directly predicts percentage energy loss rather than a risk bucket.

In both cases the raw model output gets post-processed through an **isotonic calibration** step — a monotonic curve fit to out-of-fold predictions that maps the model's raw scores to well-calibrated probabilities. This is what makes the final 0–1 risk score meaningful as a probability rather than just a relative ranking.

### Validation strategy: spatial cross-validation

Standard k-fold validation would leak information between neighboring stations — two stations 5 km apart have nearly identical weather, so one can "see" the other through the validation split. We use **spatial cross-validation**: stations are grouped into ~10 km geographic clusters, and each fold withholds an entire cluster. This forces the model to generalize across geography rather than memorize local patterns.

We also run a **temporal holdout**: train on all years except 2022, then evaluate on 2022 data it has never seen. This catches year-to-year drift that spatial CV misses (a model that memorizes 2018 conditions doesn't necessarily work for 2022).

The current AUC numbers (0.63 spatial-CV, 0.66 holdout) are measured under both of these constraints simultaneously.

---

## Why We're Below the 0.70 Gate

Two structural constraints limit us right now:

1. **Sample size**: ~640 training rows is small for a tabular model. The NREL dataset caps out at 255 stations × ~15 years, and AQ data only reaches back to 2013, cutting the effective rows further.

2. **Sparse air quality features**: PM2.5 and PM10 are among the strongest predictors of soiling, but the CAMS historical archive starts in 2013. Any station-year before that date has missing AQ features. The current pipeline drops features below 20% coverage — which means some of the highest-value predictors aren't available for the full training window.

---

## Direction: How We Get to 0.70

### Option A — More data (highest leverage)

NREL's PVDAQ dataset contains performance time-series for thousands of US systems. Extracting IWSR labels from those systems using the same methodology would expand training rows from ~640 to potentially thousands. This is the single highest-leverage path but requires non-trivial data engineering.

### Option B — Better features (medium lift)

- **Lagged weather features**: rolling 30/90-day precipitation deficit, seasonal dry-period length. These capture the accumulation dynamics better than point-in-time windows.
- **Wind direction relative to dust sources**: CAMS has a separate dust AOD channel. Prevailing wind direction toward an agricultural or road source matters for desert and Central Valley stations specifically.
- **SHAP-driven feature audit**: identify which features are actually contributing and which are noise at this sample size.

### Option C — Expand to more NREL stations

The current pipeline is configured for California. The NREL panel data covers 15 states. Training on the national dataset would increase rows and geographic diversity without any new data acquisition.

### Option D — Regression head when data warrants

As N grows past ~900 rows, switching from binary classification to direct IWSR regression gives the model more signal per row (it learns the continuous value, not just a threshold comparison). This doesn't require new data — just more of it.

---

## How Phase 2 Connects to the Product

The risk score is not the end product — it's the input to the cleaning recommendation. The recommendation engine takes:
- Risk score from Phase 2
- Forecasted precipitation (Open-Meteo, 7-day)
- Days since last cleaning (from the operator)

And outputs a cleaning window with an expected energy-recovery range. That window is what the operator pays for.

The model is already wired into the CLI end-to-end. A partner can run a single command against an AOI and get: detected arrays → soiling scores → cleaning recommendation → manifest. Phase 2 going beta means that pipeline exists and works. Phase 2 going GA means the scores are accurate enough to trust operationally.

---

## What Beta Means Right Now

We're shipping Phase 2 as **beta** with explicit disclosure: every API response and CLI output carries the current AUC, a `beta: true` flag, and a known limitations list. Design partners see real numbers. That's intentional — it builds trust and means their feedback is calibrated to actual model quality, not marketing.

The beta flag flips when both AUC gates (0.70 spatial-CV and 0.70 year-holdout) clear.
