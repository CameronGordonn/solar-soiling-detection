# Why does the AOI predict 5.53% and the nearest real station read 4.00%?

**Sprint starting point for Cameron + Akshitha, written 2026-08-12.** Production is at a
defensible baseline as of this date; this is the open question to dig into, and it is the
one that most affects what the product can honestly say.

---

## Read this first: the soiling model is the smallest lever on per-home dollars

The goal is an individualized per-home figure with money attached. **That already exists and
already individualizes** — just not from the soiling model. Measured across the 1,710
residential-scale sites (2 to 15 kW):

> **Superseded 2026-08-19 (the table immediately below).** Roof orientation from 3DEP
> lidar is a third driver worth 11.1%, taken out of *size's* share, not the soiling
> model's. The current three-way split is 86.7% size / 11.1% orientation / **2.2%**
> soiling — see `docs/HANDOFF_roof_geometry_for_paper.md` Result 1. The 97.3% / 2.5%
> below is the correct two-way answer and is kept so the decomposition can be redone.

| driver | p10 → p90 | ratio | share of dollar variance | source |
|---|---|---|---|---|
| **System size (kW)** | 2.89 → 9.26 | **3.20x** | **97.3%** | detection, live today |
| Soiling loss % | 5.07 → 5.83 | 1.15x | **2.5%** | the XGBoost head |
| Annual loss $ | $43.06 → $140.84 | 3.27x | — | the product output |

**97.3% of the per-home dollar spread is already there, and it comes from detected array
size.** The soiling model contributes 2.5%.

That has a hard implication for how to spend the sprint. Ranked by how much each lever moves
a per-home dollar figure:

1. **System size — 3.20x — already live.** Detection-derived, per-home, real variance.
2. **Tariff vintage — 2.78x — data, not modelling.** NEM 2.0 at $0.4573 against NBT at
   $0.1646. Known for 17% of the AOI today; the City of Santa Cruz records request
   (`AOI_CLEANING_TARGETING_PLAN.md` Option 8) takes that to ~88%.
3. **Roof condition — up to ~20x, unmeasured.** The moss/edge-band channel, worth 1% to 21%
   of output depending on band thickness, mounting orientation and cell era. Physics is
   already built (`scripts/analyze/substring_shade_loss.py`); it needs the ground photos.
4. **Soiling % — 1.15x — the thing everyone has been trying to improve.**

**Doubling the soiling model's spread would add less per-home differentiation than joining
one permit file.** That is the case for spending the sprint on levers 2 and 3 rather than on
the regression head.

### What is actually missing, stated precisely

It is not that the per-home number does not vary. It varies 3.27x. It is that **the per-home
*answer* does not vary**: all 1,865 sites return `no_clean`, because the breakeven shortfall
is 9x to 40x and no lever above is big enough to cross it except lever 3, which is unmeasured.

So the product today can honestly say, per home: *"your array loses about $87 a year to
soiling; a $150 clean recovers about $4 of that; don't."* That is an individualized
performance stat with a monetary result. What it cannot yet do is produce a **lead list**,
because nothing in it ever says yes. Only the moss channel can, and only after the photo
survey and the split-array wash.

---

## The gap

| source | annual soiling loss | n | distance |
|---|---|---|---|
| **Our model, AOI median** | **5.53%** | 1,865 sites | 0 km |
| NREL station, Santa Clara County | 4.00% | 8 station-years | 38 km |
| NREL station, Alameda County | 3.10% | 7 station-years | 62 km |
| Genuinely coastal CA counties, p50 | 2.80% | 332 | SoCal, 82% LA |
| All CA, p50 | 3.10% | 774 | statewide |
| *(retracted inland "coastal" anchor)* | *4.70%* | *66* | *Central Valley* |

We predict above every measurement, including the nearest one. The gap is 1.5 to 2.7
points, which on the dollar chain is a 38% to 96% overstatement of the loss.

**This does not change the product's conclusion.** Zero of 1,865 sites clear breakeven, and
they do not clear it at 2.80% either — the shortfall is 9x to 40x, so a 2-point correction
is nowhere near enough to flip it. The reason this matters is credibility, not economics:
the project's standing rests on having argued itself *out* of a revenue conclusion, and
publishing an inflated loss figure cuts directly against that.

---

## Ruled out (do not re-run these)

Both were the obvious candidates. Both were tested on 2026-08-12 and both failed.

**1. The `worldcover_*` median-fill.** The theory was that missing worldcover columns were
being filled with training medians from an agriculture-heavy training set, telling coastal
city roofs they sit near cropland.

Wrong on the mechanism. `worldcover_cropland` has a training **median of 0.0**, and the AOI
is 0.0 — the fill was already landing on the correct value. `built_up` median 1.0 against an
AOI 85.8%. Backfilling all five one-hots for real (`scripts/analyze/backfill_worldcover.py`,
all 3,349 points resolved) moved the median 5.559% → 5.528% and the spread 0.776 → 0.758
points. Kept anyway, because 474 tree-covered arrays were genuinely mislabelled, but it is
not the answer.

**2. The PM2.5 / PM10 median-fill.** Stronger theory: those six columns are *all-NaN* in the
AOI matrix and get filled with a national median of 15.7–25 µg/m³, where coastal marine air
runs 4–8. The model would think these homes carry 2–3x the particulate load they do.

Wrong on the sign. Setting PM to clean-marine values moved the median the **wrong way**,
5.53% → 5.85%. Reproduce by overriding the `pm2_5_*` / `pm10_*` columns before
`bundle.predict()`.

---

## Live hypotheses, roughly in order of how cheap they are to test

1. **Training-set composition.** 891 station-years, and CA alone is 774 of them, dominated by
   Los Angeles (273) and San Bernardino (250) — desert and semi-desert. The model may simply
   have a high prior it cannot be argued out of by any feature this AOI can supply. Test:
   refit on a coastal/marine subset, or reweight, and see where the AOI lands.
2. **`measurement_type` is unused.** 881 of 891 rows are `"PV System"` (soiling inferred from
   plant performance dips), 10 are `"Soiling Station"` (direct instruments). Matched within
   county, direct instruments read **LOWER** by a median factor of 0.56. If PV-System rows
   carry an upward bias — degradation, availability, inverter clipping all read as "soiling" —
   then the whole label set is biased high and so is anything trained on it. This is the
   hypothesis I would test first: it is one column, it is already in the CSV, and it would
   explain the direction.
3. **`tilt_deg` is missing from the AOI matrix** and is in the model. Tilt drives rain
   self-cleaning; a wrong tilt prior biases the rain-reset term.
4. **The `kimber_iwsr_*` proxy features** are in the matrix and are physics-derived. Check
   whether they are anchored to the same `sl_sat=0.08 / k=15` parameters that
   `ECONOMICS_GROUNDING` flags as the dominant sensitivity. If so the model may be inheriting
   that calibration rather than learning around it.
5. **Spatial extrapolation.** The nearest training cluster is 38 km away and across a coastal
   range. Check the model's behaviour on held-out coastal clusters specifically, rather than
   the pooled spatial CV.

---

## What is already known and should not be re-derived

- **The ranking limitation is structural, not plumbing.** 58.1% of model importance sits on
  features effectively constant across the AOI. Supplying the absent ones was tested and
  moved the spread by 0.02 points. A model trained to separate 15 states has little left when
  shown 1,865 homes in one town. Fixing *ranking* likely needs a different feature class
  (per-roof, imagery-derived), not more of the same.
- **The dollar chain is now internally consistent.** `SYSTEM_DERATE = 0.84` is applied in
  `economics.py`, `dashboard.js` and `breakeven.html`, verified to 0.0998% across 3,362
  arrays. Do not change one without the others.
- **`BASE_SOILING_PCT` is 2.80** (coastal-county p50) and is fallback-only; the AOI runs on
  the regression head, so it does not affect these numbers.
- **The loss target is recoverable-only.** `(1 - iwsr) * 100`, and IWSR excludes the wash-only
  layer by construction. The moss/lichen channel is not in this model and cannot be added to
  it by feature engineering — it needs the separate physics path in
  `docs/AOI_CLEANING_TARGETING_PLAN.md`.

---

## How to reproduce the gap in one command

```bash
PYTHONPATH=. conda run -n solar-soiling python -c "
from src.risk.loss_model import load_bundle; import pandas as pd
b=load_bundle('runs/soiling/run_lossreg/loss_regressor')
X=pd.read_parquet('outputs/aoi/santa-cruz-w2-21cm/features/inference_matrix.parquet')
p=b.predict(X)['loss_pct_p50']
print(f'AOI p50 {p.median():.3f}%  spread {p.quantile(.9)-p.quantile(.1):.3f} pts')"
```
