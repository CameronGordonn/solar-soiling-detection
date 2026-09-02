> ⚠️ **ARCHIVED 2026-08-20. Do not quote any number in this file.**
> It was written as a living doc and stopped being updated. It states `SAHI F1 = 0.40`
> as the current state and `GA gate = 0.65` as the bar — **both retired 2026-08-07.**
> The gate is now tile-level box-F1 (≥0.75 / CI-lower ≥0.70 / recall ≥0.70) and it
> **passed at 0.8260**. The 0.40 is a descendant of the "0.396" figure CLAUDE.md names
> as a stale metric whose provenance nobody recorded.
>
> Live equivalents: [Q2_PLAN.md](../Q2_PLAN.md) for status, [CLAUDE.md](../../CLAUDE.md)
> for pipeline structure, [PRODUCT_VISION.md](../PRODUCT_VISION.md) for the product thesis.
> Kept only for the core-loop narrative and the feedback-flywheel framing.

---

# SolarSoiled — Product Pipeline

How the product works end-to-end, where we are today, and how each stage gets better over time. Written as a living doc — update metrics in place as numbers improve.

---

## The core loop

```
Aerial imagery (NAIP)
        ↓
Stage 1 — Detect every solar array (YOLOv11 polygon segmentation)
        ↓
Stage 2 — Score each array for soiling risk (XGBoost + SOMOSclean + Kimber)
        ↓
Outreach — Top-50 arrays → personalized postcard + QR code → homeowner dashboard
        ↓
Recommend — Clean this array before this date (rule-based v1 → ML v2)
        ↓
Partner acts — cleans panels, reports energy recovery
        ↓
Feedback — labeled training row enters Stage 2
        ↓
Stage 2 gets sharper at this site and similar ones
```

The loop is the moat. Every clean event a partner reports makes the model more accurate at that site and generalizes to similar sites. The model gets better the more it's used.

---

## Stage 1 — Detection

**What it does:** Takes public NAIP 60 cm aerial imagery for any AOI and runs a YOLOv11 polygon segmentation model to find every solar array. Output is a GeoJSON of array polygons with CRS and affine metadata preserved end-to-end.

**Production metric:** SAHI F1 (sliced inference with IoS NMS). Full-tile mAP50 is a fast regression signal only — not the shipping metric.

**Current state:** SAHI F1 = 0.40 at conf=0.40, iou=0.50 on relabeled NAIP Santa Cruz val set. Beta gate = 0.55. GA gate = 0.65.

**How it improves:**
- Train relabeling in progress (val/test done, train in progress). Each relabeling round closes label gaps that were teaching the model wrong signal.
- R0 retrain on relabeled NAIP (warm-started from SAHI baseline to preserve small-panel detection prior).
- Duke ramp (R1+) after R0 clears 0.45 SAHI F1 — adds small-panel diversity the 60 cm NAIP labels can't teach.
- Long-term: partner-reported array polygons (from cleaning crews who know exactly where the panels are) become labeled training data, closing the label-quality gap entirely.

**What Josh is working on this week:** Improving the training set labels and getting a clean R0 training run by end of week.

---

## Stage 2 — Soiling Risk Model

**What it does:** For each detected array, scores soiling risk on a 0–1 scale using environmental and structural features. High score = clean soon. Low score = monitor.

**Current state:** **0.728 spatial-CV AUC** ✓ / 0.679 holdout-2022 AUC (`run_optionb`, nrel_merged labels). Spatial-CV gate cleared. Holdout 2.1 pts short; next lever is MERRA-2 historical PM2.5.

**What the model is actually predicting right now:** Regional soiling risk — "this location tends to accumulate soiling based on weather, dust, land cover, and site context." It is NOT yet predicting soiling on specific panels. The NREL panel labels used for training are measured at monitoring stations, not at our detected array locations. The spatial join is an approximation.

**This is why the feedback loop is the critical next step.** The environmental proxy works well enough to rank arrays within a region — the geography passes the smell test — but panel-level accuracy requires panel-level labels.

### Current features

| Category | Features |
|---|---|
| Weather | MERRA-2 PM2.5/PM10, Open-Meteo precipitation, temperature, wind |
| Location | Elevation, lat/lon, distance to coast |
| Land cover | ESA WorldCover class, distance to agriculture/bare soil |
| Structural | Array area, aspect ratio, tilt proxy |
| Physics proxies | Kimber soiling rate, SOMOSclean soiling index |

### How it improves — the three phases

**Phase 1 (now — environmental proxy):**
Model predicts soiling risk from environment alone. Useful for regional ranking. Labels come from NREL monitoring stations (imperfect spatial match). AUC target: 0.70.

**Phase 2 (first partner data):**
Add `days_since_last_clean` as a feature — captures individual maintenance schedules. Add `last_clean_outcome_pct` (energy recovery reported by partner after cleaning) as a training label for arrays where we have it. These rows dominate gradient updates because they're real panel measurements, not proxies. Expected AUC lift: significant, because the label noise drops sharply.

**Phase 3 (at scale):**
Historical clean outcomes at specific arrays become the primary signal. Environmental features become supporting features that generalize to new sites with no history. The model learns "array X at this site gets dirty fast in April specifically" from real observations, not from weather station interpolation.

---

## The Feedback Loop

**Why it matters:** Without it, we're predicting soiling from environment and calling it a proxy. With it, we're predicting soiling from actual cleaning outcomes and calling it validated.

**The data we need from a partner:**
```json
{
  "array_id": 142,
  "cleaned_at": "2026-03-15",
  "pre_clean_kwh_7d": 284.2,
  "post_clean_kwh_7d": 312.8,
  "notes": "heavy dust accumulation visible"
}
```

**What that becomes in training:**
- `actual_recovery_pct = (312.8 - 284.2) / 284.2 = 10.1%` — the label
- Joined to the array's environmental features at `cleaned_at` date — the features
- One row in the training matrix, weighted higher than NREL proxy rows

**How partners submit it:** `POST /feedback` endpoint (to be wired up). Free, always — the data is more valuable to us than charging for it.

**Retraining cadence:** Batch retrain weekly once feedback rows exist. Each retrain run is versioned; `manifest.json` on every output tracks which model version produced which prediction.

---

## Recommend

**What it does:** Takes the AOI-level risk distribution and each array's individual score, checks a 7-day Open-Meteo forecast for dry windows, and outputs a cleaning recommendation per array.

**Current state:** Rule-based v1. Logic: if AOI p90 risk > threshold AND days since clean > 30 AND < 5mm rain forecast → recommend cleaning window = next dry stretch. Per-array: arrays above threshold get the window, arrays below get "monitor."

**v2 (after feedback loop):** Optimize expected `recovery_kWh − cleaning_cost` over a rolling calendar. Requires clean outcome data to train the recovery model. The rule-based v1 ships now; v2 is gated on ~6 months of partner feedback.

---

## What "beta" means concretely

Every API response carries:
```json
{
  "model_version": "r2-cameron-20260509",
  "beta": true,
  "known_limitations": [
    "Detection below 0.65 SAHI F1 GA bar",
    "Soiling AUC below 0.70 GA bar — panel-level validation pending partner feedback",
    "Recommend engine v1 is rule-based; expected_recovery_pct is a static placeholder"
  ]
}
```

The beta flag flips to false when Stage 1 clears 0.65 SAHI F1 and Stage 2 clears 0.70 AUC on both spatial-CV and year-holdout. Until then, partners know exactly what they're acting on.

---

## Current pipeline status

| Stage | Status | Gate | Current |
|---|---|---|---|
| Stage 1 detection | beta | SAHI F1 ≥ 0.65 | 0.40 |
| Stage 2 soiling risk (XGBoost) | beta | Spatial-CV AUC ≥ 0.70 + holdout ≥ 0.70 | **0.728** ✓ / 0.679 |
| Stage 2 alt models (SOMOSclean + Kimber) | shipped | Pre-computed from cached weather data | live (334 arrays) |
| Recommend v1 | shipped | Rule-based, always available | live |
| Dashboard + outreach | shipped | Homeowner can see their array and compare models | live in the BBF site (`betterbehaviorfoundation.com/tools/`, Cloudflare Pages) |
| Physical mailers | shipped | 50 cards to top-50 Santa Cruz homeowners | `mailers_v13` sent live 50/50 (2026-06-30) |
| Feedback loop | queued | First partner clean event | not yet |
| Recommend v2 | queued | ~6 months partner data | not yet |

---

## What a design partner unlocks

One partner reporting clean outcomes does four things:
1. Gives us the first panel-level validation data (answers Tyler's label question)
2. Adds `actual_recovery_pct` as a training label — the strongest signal we can get
3. Lets us compute a real `expected_recovery_pct` range instead of the static bucket placeholder
4. Gives the partner a model that gets sharper at their specific site with each event reported

The product gets better the more it's used. That's the pitch.
