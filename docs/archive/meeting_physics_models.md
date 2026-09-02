> ⚠️ **ARCHIVED — explainer written for Professor.** The physics is still accurate, but two
> parameters moved after it was written: SOMOSclean `sl_sat` 0.10 → **0.08** and `k` 30 → **15**,
> recalibrated against coastal-CA NREL data (2026-06-01). Anything downstream of those constants
> in here is off. Note also that `sl_sat` was later found to have been fitted against the wrong
> point of the trajectory — see §6 of [CRAIG_BRIEF_2026-08-19.md](../CRAIG_BRIEF_2026-08-19.md).
> Live: [SOILING_STAGE2_GUIDE.md](../SOILING_STAGE2_GUIDE.md) and `src/risk/physics_score.py`.

# Meeting Notes — Physics-Based Soiling Models in SolarSoiled

**Purpose:** Explain to Professor how the Kimber and SOMOSclean models are integrated into the system, their role relative to each other, and what is still open.

---

## Overview

The system uses two physics-based soiling models — **Kimber** and **SOMOSclean** — in two distinct roles:

1. **As features** fed into the XGBoost risk model (both models run on each training row's weather window and their outputs become input columns). *(XGBoost part is out of scope for today — covered separately.)*
2. **As a direct scoring engine** — SOMOSclean runs standalone in production to score detected arrays without any ML model involved. This is the current live path.

---

## 1. Kimber Model

**Reference:** Kimber et al., 2007 — original empirical soiling accumulation model.

**Mechanism (as implemented):**
- Soiling ratio starts at 1.0 (clean array).
- Each day, soiling loss accumulates proportionally to daily PM2.5 concentration:
  `ratio -= deposition_per_pm25 × PM2.5`
- Any day where precipitation ≥ `rain_clean_mm` (1 mm default) **fully resets** the ratio to 1.0 — binary rain cleaning event.
- Output: a daily time series of soiling ratio (1.0 = clean, lower = more soiling).

**Parameters (from `configs/soiling/features.yaml`):**
| Parameter | Value | Meaning |
|---|---|---|
| `deposition_per_pm25` | 0.0006 | Fractional soiling loss per µg/m³ per day (Kimber 2007 fit) |
| `rain_clean_mm` | 1.0 mm | Minimum precipitation to trigger a full cleaning reset |

**Key limitation:** Binary cleaning assumption — any rain resets completely. This overestimates cleaning from light rain and doesn't model partial cleaning. Also uses only PM2.5 (not PM10), so coarse dust events are underweighted.

**Role in the system:**
- Serves as the default synthetic label generator for bootstrap runs (`label_source: kimber_proxy`) — runs on a set of seed California station coordinates when no NREL data is available.
- Also contributes a feature column `kimber_iwsr_proxy` (full-window mean soiling ratio) and per-window means (`kimber_iwsr_7d_mean`, `kimber_iwsr_30d_mean`, `kimber_iwsr_90d_mean`) to the XGBoost feature matrix.
- **Not used for direct production scoring.** SOMOSclean replaced it for that role.

---

## 2. SOMOSclean Model

**Reference:** Micheli et al. (ENEL) — empirical soiling model validated on 200 MW of Spanish PV plants. MAE 0.71% vs measured soiling, below sensor noise floor.

**Mechanism (as implemented):**
The model tracks an "equivalent day" (`eqD`) counter that drives a complementary exponential soiling loss curve:

```
SL(d) = sl_sat × (1 − exp(−eqD(d) / k))
IWSR(d) = 1 − SL(d)
```

Each day, `eqD` updates based on weather:
- **Heavy rain** (`precip ≥ heavy_rain_mm`): full reset, `eqD → 0`
- **Light rain** (`rain_min_mm ≤ precip < heavy_rain_mm`): partial cleaning, linear between the two thresholds
- **Dust day** (`PM10 > pm10_dust_threshold`): accelerated accumulation, `eqD` increments by `1 + pm10_dust_scale × (PM10 − threshold)` instead of 1
- **Normal day**: `eqD` increments by 1

**Parameters (from `configs/soiling/features.yaml`):**
| Parameter | Value | Meaning |
|---|---|---|
| `sl_sat` | 0.10 (10%) | Saturation ceiling — max soiling loss the model can reach |
| `k` | 30.0 days | Time constant; controls how fast saturation is approached |
| `heavy_rain_mm` | 10.0 mm | Threshold for full cleaning reset |
| `rain_min_mm` | 1.0 mm | Threshold below which rain has no cleaning effect |
| `pm10_dust_threshold` | 50.0 µg/m³ | PM10 level above which dust accelerates accumulation |
| `pm10_dust_scale` | 0.02 | Rate of PM10-driven acceleration above the threshold |

**Why 10% saturation ceiling (`sl_sat = 0.10`):** The NREL dataset covers US installations with median soiling below 5%/yr. A 10% ceiling captures the realistic upper tail without distorting the label distribution. The Spanish validation used 0.25 — we tuned down for US conditions.

**Key advantages over Kimber:**
- Saturation dynamics — soiling can't grow unboundedly even without rain
- Partial cleaning from light rain (not binary reset)
- PM10-driven dust acceleration (not just PM2.5 accumulation)
- Validated on real measured soiling data

**Role in the system:**

**a) Direct production scorer** (`src/risk/physics_score.py`):
SOMOSclean runs on each detected array's trailing 365-day weather window and outputs:
- `risk_score` — normalized to [0, 1] by dividing terminal SL by `sl_sat`
- `soiling_loss_pct` — raw soiling loss in percent
- `eqD` — equivalent days of accumulation at scoring date
- `last_rain_date` — ISO date of last heavy rain event (≥ 10 mm)
- `scoring_method = "somosclean-physics-v1"`

This is the V1 production path — no ML model required. Any detected array can be scored using only weather data from Open-Meteo (free, no API key).

**b) Feature for XGBoost** (`src/risk/feature_engineering.py`):
Contributes `somosclean_sl_proxy` (terminal soiling loss over the full window) and per-window means (`somosclean_sl_7d_mean`, `somosclean_sl_30d_mean`, `somosclean_sl_90d_mean`) to the training feature matrix.

**c) Label generator** (`src/risk/labels.py`):
- `somosclean_panel_labels()` — generates per-(station, year) synthetic labels using NREL station coordinates only (measured IWSR not used in training — only geography and year drive the weather fetch). Produces ~891 rows across 146 stations in 15 states.
- `somosclean_synthetic_labels()` — same logic on arbitrary station lists (e.g., seed stations for bootstrap).

---

## 3. How They Fit Together

```
Weather (ERA5 + CAMS/MERRA-2)
        │
        ▼
  fetch_combined()          ← Open-Meteo API, on-disk SQLite cache
        │
   ┌────┴────────────┐
   │                 │
Kimber           SOMOSclean
trajectory       trajectory
   │                 │
   ▼                 ▼
kimber_iwsr_*    somosclean_sl_*
  features         features
   │                 │
   └────┬────────────┘
        │
        ▼
  [XGBoost features]    ← out of scope today
        │
        ▼
   XGBoost model
        │
        ▼
   risk_score (ML)

─────────── parallel path ───────────

Detected array centroids
        │
        ▼
  fetch_combined()
        │
        ▼
  SOMOSclean trajectory
  (physics_score.py)
        │
        ▼
  risk_score (physics V1)  ← current live production path
```

---

## 4. Data Pipeline

**Weather source:**
- **ERA5 reanalysis** (Open-Meteo): precipitation, wind, humidity, temperature, radiation — free, no API key, goes back to 1940
- **CAMS air quality** (Open-Meteo): PM2.5, PM10 — free, starts 2013-01-01
- **MERRA-2** (NASA GES DISC): PM2.5, PM10 — goes back to 1980, requires NASA Earthdata Bearer token. Currently pre-fetching for 2008–2022 to cover the full NREL label set. Covers ~35 unique grid cells × 15 years = ~99k day-fetches; ~50% complete.

**Cache:** All fetches stored in `requests-cache` SQLite databases on disk. MERRA-2 cache is immutable (reanalysis data never changes); Open-Meteo cache expires after 30 days.

---

## 5. Open Questions / Things to Discuss

1. **SOMOSclean parameter calibration for US conditions** — current `sl_sat=0.10` and `k=30` were set by analogy with the Spanish validation and the NREL label distribution. We haven't done a formal grid search or Bayesian calibration against measured NREL IWSR values. This is the most obvious lever for improving the physics model's accuracy before the ML layer.

2. **MERRA-2 vs CAMS for PM** — the two AQ sources use different aerosol models. Current production uses MERRA-2 when the token is set (consistent source across all years), CAMS otherwise. Pre-2013 rows fall back to NaN PM columns (median-imputed). We should verify the MERRA-2 and CAMS PM distributions are reasonably consistent for the 2013–2022 overlap period.

3. **Kimber as ablation baseline** — Kimber is currently active as a feature (`use_kimber_feature: true`). It's worth running an ablation to confirm it adds signal over SOMOSclean or if it's redundant. If redundant, dropping it simplifies the feature set.

4. **SOMOSclean V1 vs ML model** — physics V1 (direct SOMOSclean scoring) ships today with no training data required. The XGBoost model will replace it when the holdout AUC gate clears (currently 0.679, gate 0.70). The physics model stays in the codebase as an interpretable fallback and a sanity check on the ML outputs.

5. **Partial cleaning validation** — the linear partial-cleaning ramp between `rain_min_mm` and `heavy_rain_mm` is an assumption, not validated against the Spanish dataset directly. If the professor has access to high-resolution cleaning measurement data, this is the SOMOSclean parameter most worth revisiting.
