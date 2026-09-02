# Economics grounding pass — 2026-08-09

Grounding the SolarSoiled soiling-loss and cleaning-economics model in measured data.
Every constant in the dollar chain (`detected area → system_kw → annual_loss_usd →
clean/no-clean`) was audited, sourced, or explicitly marked unsourced.

**Headline: the product is uneconomic in coastal Santa Cruz, and the margin is not
close.** Zero of 1,865 sites produce a positive expected net, at any electricity rate up
to $0.70/kWh, at any system size, under 2,000 Monte Carlo draws each. This confirms
risk-register kill-risk **C1 (unit economics)**. The mechanism is not the one the audit
brief proposed, and the single dominant cause was an unsourced constant nobody had
measured.

> **Scope qualifier added 2026-08-10 (see §12).** That zero is measured on *recoverable*
> soiling, which is the only kind NREL's IWSR is defined to contain and the only kind this
> pipeline models. A permanent, wash-only layer is excluded by construction from the
> labels, from `sl_sat`, and from the 0.045. Quote the result as **"zero of 1,865 on
> recoverable soiling"** and name the unmeasured channel: at the largest permanent term
> the outside literature supports, most NEM 2.0 legacy homes in the AOI would flip
> positive.

---

## 1. What actually broke the model

| Constant | Was | Now | Factor | Direction |
|---|---|---|---|---|
| `recovery_frac` (professional) | 0.90 | **0.045** | **20x** | product worse |
| basic-clean price | $60 min | **$90** (team SKU) | — | set, not estimated |
| `BASE_RATE` | 0.25 $/kWh flat | **0.165** (NBT no-battery) | 1.5x | product worse |
| `RISK_TO_LOSS_PCT` | 8.0 × a probability | *removed* — regression head | n/a | category fix |
| `M2_PER_KW` | 5.67 (packing implicit) | **5.41** (packing measured 0.84) | 1.05x | product better |
| `PANEL_KW` | 0.32 | **0.44** | 1.38x | cost lower |
| per-polygon costing | 1 trip charge / polygon | 1 / **site** | 2.3x | product better |

The two corrections that make the product look worse are far larger than the three that
make it look better. **`recovery_frac = 0.90` was doing all the work**: with it restored,
44.8%–95.4% of the AOI is recommended to clean depending on billing regime; with the
measured value, 0%.

---

## 2. Reproduced audit findings

All three reproduce exactly.

- **NREL measured loss** (`nrel_soiling_map_annual.csv`, n=891): all p50 3.00 / p90 6.70;
  CA (n=774) p50 3.10 / p90 6.80; coastal CA (lon < −120.5, n=66) p50 **4.70** / p90 **9.05**.
- **Pipeline on Santa Cruz AOI**: `risk_score` p10/p50/p90 = 0.556/0.556/0.632; ×8 gives
  4.45/4.45/5.06. Variance collapsed to 0.61 points against a measured 5.6.
- **`breakeven_soiling_pct(professional, 5 kW)` = 6.7%**, and `breakeven_system_kw` at
  3% returns `None`.

---

## 3. Task 1 — electricity rate (`src/risk/rates.py`)

A lost kWh is worth one of two very different numbers, and a flat rate cannot express it.

**Sourced Santa Cruz stack** — PG&E E-TOU-C delivery + 3CE generation, non-CARE,
above-baseline marginal, incl. CCA adders and 8.5% City UUT. Reproduced from the
bill-reconciled tariff specs in the sibling `energy-advisor` repo (validated to
±$0.22/month across 11 consecutive real bills), each component carrying its Cal. P.U.C.
sheet citation:

| period | $/kWh | share of PV output |
|---|---:|---:|
| summer peak | 0.60554 | 2.9% |
| summer off-peak | 0.47320 | 37.7% |
| winter peak | 0.47087 | 2.6% |
| winter off-peak | 0.43860 | 56.8% |

**Only 5.5% of a south-facing array's annual output falls in the 4–9 p.m. peak window.**
Soiling scales production down in every hour, so ~94% of soiling-lost kWh are off-peak.

- Production-weighted **retail offset**: **$0.4573/kWh**
- Production-weighted **NBT export credit**: **$0.0392/kWh** — vs $0.0905 for the
  unweighted table mean, because ACC value is lowest exactly at midday when PV generates.
  *(Substitution flagged in code: the imported ACC table is SDG&E's; no PG&E table has
  been imported. Same CPUC methodology, level unverified for PG&E.)*

That is a **12× spread**, so the answer turns entirely on the midday self-consumption
share σ:

| regime | σ | $/kWh | % of AOI recommended to clean |
|---|---:|---:|---:|
| NEM 2.0 legacy (pre-2023-04-15) | 1.00 (by tariff) | 0.4573 | **0%** |
| NEM 3.0 / NBT + battery | 0.80 | 0.3737 | **0%** |
| NEM 3.0 / NBT, no battery | 0.30 | **0.1646** | **0%** |

The retired flat $0.25 implies σ = 0.50 — plausible for a battery home, too high for the
no-battery majority. **The sourced rate is *lower* than the assumption**, not higher.

**Sensitivity curve** (`scripts/analyze/rate_sensitivity.py`): swept $0.02 → $0.70/kWh in
$0.02 steps. Clean-share is **0.00% at every rate**. Under the legacy 0.90 recovery the
same sweep crosses 50% at $0.22/kWh — the curve exists only because of that constant.

**Segmentation result worth keeping:** a NEM 2.0 legacy home is worth **2.8×** more per
lost kWh than an identical NEM 3.0 home. That is the sharpest targeting signal found, and
it is a public records join (interconnection date), not a modelling problem.

---

## 4. Task 2 — regression head (`src/risk/loss_model.py`)

`RISK_TO_LOSS_PCT = 8.0` multiplied a **calibrated classification probability** by 8. A
probability answers "is this a high-soiling site?", not "how much did it lose?".

Replaced with three XGBoost quantile heads (`reg:quantileerror`, α = 0.10/0.50/0.90)
trained directly on `loss_pct = (1 − iwsr) × 100`, spatial-CV by 10 km cluster.
Reproduce: `scripts/predict/train_loss_regressor.py`.

Out-of-fold (n=891, 40 features, panel rows only):

```
MAE 1.746 pts   RMSE 2.497   bias −0.236   Spearman 0.310
measured  p10 1.10 / p50 3.00 / p90 6.70    span 5.60
predicted p10 2.20 / p50 3.26 / p90 4.89    span 2.69
SPREAD RATIO 0.481   (sd ratio 0.398)
PI coverage 0.558  <-- raw quantile heads badly overconfident
```

The raw intervals under-covered by 24 points. Fixed with **conformalized quantile
regression** (Romano et al. 2019), calibrated on the out-of-fold predictions — the only
sample drawn under the deployment condition of an unseen spatial cluster:

```
CQR offset +0.92 pts/side  ->  PI coverage 0.802 (nominal 0.80), median width 4.92 pts
```

### The important negative result

**The regression head does not restore within-AOI spread, and the audit brief's proposed
mechanism is wrong.** Scored on the Santa Cruz AOI, predicted `loss_pct_p50` spans
**0.78 points** p10–p90 — against risk×8's 0.61. Barely moved.

Diagnosis (`feature_importances_` vs feature SDs):

- AOI feature standard deviations are **0.3%–23%** of the training SDs. The model's
  signal is weather/AQ, and weather does not vary across a 20 km coastal AOI.
- **58.1% of model importance sits on features that are effectively constant across the
  AOI** (measured 2026-08-12). **This supersedes the 21.8% originally written here**, which
  counted only the features *absent* from the AOI matrix (`worldcover_*`, `tilt_deg`,
  `month_of_year`, silently median-filled) and missed the all-NaN and low-variance ones.

So the variance collapse was **not** caused by the ×8 mapping. It is caused by the
training labels being *station-level*: NREL has no within-AOI variation to learn from, so
the model can only ever capture between-climate differences. Fixing the mapping was still
correct — it removes a category error and yields a real, calibrated interval — but it
does not and cannot fix the AOI spread.

**~~Actionable: supplying `tilt_deg` + WorldCover to the AOI feature matrix recovers 21.8%
of importance and is the cheapest available improvement.~~ RETRACTED.** Two things killed it.
(1) The share was re-measured at **58.1%**, not 21.8%, so it was never a small patch.
(2) **The experiment was run and it did nothing**: WorldCover was wired in properly, resolving
all points, and within-AOI spread did not move. See
[CRAIG_BRIEF_2026-08-19.md](CRAIG_BRIEF_2026-08-19.md) §"We wired WorldCover in properly and it
did nothing" and [SOILING_LEVEL_INVESTIGATION.md](SOILING_LEVEL_INVESTIGATION.md).

The limit is **structural**, not a plumbing gap: NREL labels are station-level, so there is no
within-AOI signal to learn. No feature supplied to the AOI matrix can create one. The real fix is
a label set with within-area variation, which is the PVDAQ lane
([PVDAQ_LANE_HANDOFF_20260831.md](PVDAQ_LANE_HANDOFF_20260831.md)).

### Follow-up: WorldCover was wired in, and it did NOT help (2026-08-09)

`sample_worldcover_batch()` now samples ESA WorldCover once per AOI (one STAC resolve +
one vectorized raster sample; 3,362/3,362 points from a single tile) and
`build_risk_features.py` writes the five one-hots. 14.1% of Santa Cruz arrays sit on a
`tree` pixel — real within-AOI variation.

Re-scoring with them supplied:

```
BEFORE (median-filled)  p10 5.08  p50 5.55  p90 5.86   span 0.78 pts
AFTER  (supplied)       p10 5.07  p50 5.53  p90 5.84   span 0.77 pts
  arrays on 'tree' pixels (n=474):    median predicted loss 5.36%
  arrays on 'built_up'   (n=2875):    median predicted loss 5.55%
```

**No improvement, and the sign is backwards** — tree-pixel arrays score *lower*, not
higher. The reason is instructive: the model learned `worldcover_tree` from NREL
*stations*, where a tree pixel means "forested rural region" (wetter air, less dust), not
"a tree overhanging this roof". The feature name is the same; the referent is not.

The plumbing fix is kept — median-filling a feature the model was trained on is wrong
regardless, and it matters for AOIs that genuinely span land-cover types — but **it does
not address the within-AOI discrimination blocker.** No feature learned from station-level
labels can, because the training set contains no examples of per-roof variation. Per-roof
overhang needs either a per-roof label source or a feature whose meaning does not shift
between the two contexts (e.g. LiDAR canopy height directly over the array polygon).

---

## 5. Task 3 — site clustering (`src/risk/site_cluster.py`)

Parcel-APN spatial join (15,169 Santa Cruz parcels) with a proximity union-find fallback
(edge-to-edge ≤ 2.5 m; wider than ridge/vent gaps, narrower than the 5 ft R-1 side setback).

| AOI | polygons | sites | fragmentation | multi-polygon sites |
|---|---:|---:|---:|---:|
| `santa-cruz-w2-21cm` (21cm) | 3,362 | 1,865 | **1.80×** | 48.1% (max 22) |
| `santa-cruz-outreach-v1` (60cm) | 334 | 257 | 1.30× | 15.2% |

Confirms the premise: 21cm nearly doubles fragmentation. Costing per polygon levies
`MIN_PRO_SERVICE` per fragment — a 90 m² roof split four ways costs **$600 instead of
$264**, a 2.3× overcharge for one truck roll. Economics now run on summed site area with
area-weighted loss; `site_primary` marks one row per site so totals never double-count.

---

### Permit APN join — audited 2026-08-09

The 7% permit-kW match rate is **not a join bug**; APN formats agree once
non-digits are stripped. Three separate effects, in order of size:

| | count | rate |
|---|---:|---:|
| permits county-wide | 8,780 (7,399 unique APNs) | |
| permit APNs inside the AOI parcel layer | 764 | 10.3% of permits |
| detected sites with **any** permit | 341 / 1,865 | **18.3%** |
| detected sites with a permit **kW** | 130 / 1,865 | **7.0%** |

1. **Geography** — the parcel layer covers only the AOI (15,169 parcels) while permits are
   county-wide, so ~90% of permits are simply elsewhere.
2. **The kW field is optional and its availability collapsed.** Confirmed by year:
   2016–19 carry kW on 71–81% of permits, 2021–23 on 7–11%, 2024–26 on 13–18%. A form
   change around 2020, exactly as expected — not recoverable by better joining.
3. **Detection recall signal (worth a separate look):** of the 764 permitted parcels
   inside the AOI, only **341 (44.6%)** have a detected array. Caveats — permits include
   ground mounts, systems never built, battery/repair permits, and installs postdating the
   imagery — but it is a materially better figure than the ~10% imaged-era APN estimate
   recorded earlier, and the two used different methods.

**Usable now:** the 341 any-permit matches supply `mount` (roof vs ground) even without
kW; the 130 kW matches are the packing-factor calibration set used in §6.

## 6. Task 4 — array sizing

- `PANEL_KW` 0.32 → **0.44** (2026 mainstream 440 W residential module).
- `MODULE_W_PER_M2` = **220**, the midpoint of 2026 new-install (~228 W/m², 440 W over
  1.93 m², 22.5% efficiency) and NREL's Q1-2024 residential benchmark (400 W over 1.9 m²
  with frame, 21.1% = 210 W/m²).
- `PACKING_FACTOR` = **0.84**, now *measured* (see permit join above): n=130 sites with
  both a permitted kW and a detected envelope give p50 **5.44 m²/kW** / **184 W/m²**, i.e.
  packing 0.84 at 220 W/m² modules. The briefly-adopted 0.90 geometric guess was ~7%
  optimistic, and the ORIGINAL 5.67 was closer to truth than the 5.05 that replaced it. **Detected polygon area is the array
  envelope** — it includes inter-module gaps, frame edges and rail overhang, and excludes
  between-block setbacks (those become separate polygons, hence task 3).
- `M2_PER_KW` = 1000/(220 × 0.84) = **5.41**, against a measured p50 of 5.44. Derived, not hardcoded (tested).

`PACKING_FACTOR` is **no longer UNSOURCED**. Residual caveat: permitted kW is summed per
APN so a later expansion permit can over-count, and the p25–p75 spread (3.85–6.17 m²/kW) is
wide — which is precisely why `AREA_SCALE_1SIGMA` stays at the measured 0.26.

---

## 7. Task 5 — time-aware recovery (`src/risk/recovery.py`)

`recovery_frac = 0.90` assumed one cleaning captures 90% of a year's soiling loss. It
integrates the SOMOSclean daily trajectory between the cleaning date and the next natural
reset instead, on two years of real Open-Meteo weather with the first year discarded as
trajectory spin-up.

**Physics validation first:** SOMOSclean (sl_sat 0.08, k 15) gives an annual-mean soiling
loss of **5.06%** over the evaluation year, against NREL's measured coastal-CA p50 of
**4.70%**. The trajectory reproduces the measured level, so the recovery ratio rests on a
calibrated model.

```
Santa Cruz, 27 heavy-rain (>=10mm) days in the evaluation year
  BEST cleaning date  2025-08-08   recovery 0.045  (66 benefit days)
  mean over dates                  0.016
  worst                            0.000
  retired constant     0.900  ->  overstated by 20x
```

Mechanism: at k=15 equivalent-days the array returns to its annual-mean soiling within
about two weeks, and rain resets it 27 times a year for free. **One cleaning buys roughly
one array-month of cleanliness out of twelve.**

**Sensitivity — and it is robust.** k (the re-soiling constant) dominates, not climate:

| site | heavy-rain days/2yr | k=15 | k=30 | k=60 |
|---|---:|---:|---:|---:|
| Santa Cruz (coastal) | 49 | 0.045 | 0.096 | 0.163 |
| Bakersfield (Central Valley) | 21 | 0.034 | 0.059 | 0.102 |
| Phoenix (desert) | 17 | 0.038 | 0.076 | 0.125 |

Even at k=60 — four times the calibrated value, in a desert — best recovery is 0.163,
still 5.5× below the retired 0.90. And k=15 is the *better-calibrated* choice for coastal
CA: it reproduces the measured 4.70% annual loss (5.06%) where k=60 gives 3.01%. The two
validations agree, which is why this result is stated firmly.

---

## 8. Task 6 — uncertainty propagation

`array_recommendation_mc()` samples all four uncertain inputs and reports
`expected_net_usd_p10/p50/p90`, `prob_net_positive`, and `decision_robust`:

- **loss %** — two-piece (split) normal through the CQR prediction interval; right-skewed,
  because a symmetric draw would misstate exactly the upper tail that decides the call
- **kW** — log-normal, 1σ = **0.26**, *measured*: the same physical arrays came out 0.74×
  the area at 21cm vs 60cm, so ~26% of dollars moved on an imagery change alone
- **$/kWh** — uniform over the regime's σ band
- **recovery** — uniform over (0, 0.045), i.e. cleaning-date timing

On the full AOI: **max `prob_net_positive` across all 1,865 sites = 0.0000.** Not one
draw in 3.7 million was positive. `decision_robust = True` everywhere.

---

## 9. What would have to be true for this to work

At the measured recovery fraction, size cancels (per-panel cost and recovered value both
scale linearly in kW), so no system size rescues it — asserted in
`tests/test_economics.py`. Required conditions, all far outside anything measured:

- Breakeven soiling at 20 kW: **98.4%** (NBT), **43.4%** (battery), **35.4%** (NEM 2.0).
  The maximum annual loss anywhere in the 891-row NREL dataset is **22.9%**.
- Or a ~19× cheaper cleaning, or a ~19× higher recovery (i.e. the retired 0.90).

**The viable direction is not residential single-cleans.** What the numbers do support:

1. **Sites where rain does not reset** — the recovery fraction rises with k and with dry
   spells. Genuinely arid, high-dust, low-tilt, agricultural-adjacent sites, at C&I scale.
2. **NEM 2.0 legacy homes** — 2.8× the per-kWh value, identifiable from public
   interconnection dates.
3. **Validate `MIN_PRO_SERVICE`** — three real quotes. It is `UNSOURCED` and decides most
   residential cases on its own.
4. **Sell the diagnosis, not the cleaning** — the detection + area + parcel join is sound;
   it is the cleaning-value proposition that does not clear.

---

## 10. Caveats on this analysis

- **`MIN_PRO_SERVICE` ($150) / `MIN_RINSE_SERVICE` ($90)** are the team's set price points
  (Cameron, 2026-08-09), not estimates. The per-panel rate schedule and the NBT σ = 0.30
  remain `UNSOURCED` and are marked as such in code. `PACKING_FACTOR` is now measured.
- **The ACC export table is SDG&E's, not PG&E's.** Same CPUC methodology; level unverified.
- **The clear-sky production weight is analytic, not PVWatts.** PVWatts needs an NREL API
  key this machine does not have, and `energy-advisor`'s module correctly refuses to
  fabricate one. Verified to reduce exactly to the south-facing closed form (tested).
- **Recovery assumes one cleaning per year.** A multi-clean schedule would recover more in
  total but pays the trip charge each time; at a 19× shortfall this does not change the sign.
- **SOMOSclean k=15 is the dominant sensitivity** and is calibrated on 36 coastal-CA
  station-years. A direct measurement of re-soiling rate would be the highest-value
  follow-up.

---

## 11. Follow-up (2026-08-10) — the downstream tools, and one unresolved question

Found while explaining the 4.5% to a non-technical reader, which turned out to be a good
way to audit it.

### The grounding pass did not reach `breakeven.html`

The dashboard was rebuilt around `recovery_frac = 0.045`. The BBF site's other tool, the
public breakeven calculator, was not: it still ran the retired **0.70 / 0.90**. On its own
default settings (6.9 kW, Year 2, $0.16/kWh, light-pro) it returned breakeven **day 0** and
told the visitor to clean now, one click from a report card saying the opposite. Fixed in
BBF-Website `e2a3cd4`. Flagging it here because **this doc is where the constant changed,
and nothing in it lists the consumers of that constant.** Worth adding to the next audit:
grep the sibling repo for the retired value before closing the loop.

Two arithmetic defects in the same shared block, both also fixed there:

- **The implied loss percentage moved with the electricity rate.** The seasonal term was
  priced at the caller's rate; the persistent term carried a hardcoded $0.375/kWh inside
  `V_ANNUAL_PER_KW = (14000 * 0.375) / 8`. One sum, two electricity prices. $0.05/kWh
  implied a 41.7% output loss, $0.70/kWh implied 5.3%. Soiling is physical and cannot
  depend on a tariff.
- **`SEASON_DAYS = 180` accumulated in full, then divided by 365 days of production.** So
  the "annual" loss was the peak at the end of a six-month dry season. With the rate bug
  removed it still reads 8.5% at year 3 against the regression head's 5.56%.

### The unresolved question: what does the persistent term mean under 0.045?

**This one needs the ML side and I could not settle it from the site repo.** §7 measured
recovery by integrating the SOMOSclean daily trajectory from a cleaning date to the next
natural rain reset, and validated the trajectory against NREL's measured coastal-CA annual
loss (5.06% modelled vs 4.70% measured). The tools separately model a **persistent** term:
grime rain never removes, `YOY_PERSIST_PCT = 3.0`/yr, compounding with system age.

Those two cannot both be right as currently wired, and the two readings point opposite ways:

1. **If permanent grime is already inside the measured 4.70%** (it is a field measurement,
   so presumably it is), then SOMOSclean's 5.06% already contains it, `0.045` already
   accounts for it, and the tools' separate 3%/yr term **double-counts**.
2. **If SOMOSclean has no permanent component** and rain resets it to zero each time, then
   `0.045` is measured on seasonal dust only, and applying it to grime that rain *provably
   cannot remove* **understates** what a wash does for an older array. A wash is the only
   thing that clears that layer, so its recovery there should be near the 0.70/0.90 dirt-
   removal efficacy, not 0.045.

Reading 2 is the one that could move the sign for old systems, so it should be checked
before the "zero of 1,865" claim is defended in a funder conversation. Concretely: does the
SOMOSclean trajectory have a non-zero floor after a heavy-rain reset?

**Related structural point, independent of the above.** The breakeven page's curve is
`accumulated loss over d dry days` compared against the cleaning cost. Money already lost
over those d days is sunk and washing cannot bring it back; what a cleaning buys is future
output. §7's integral is correctly forward-looking. Swapping the constant fixes the sign
but leaves the page's shape a sunk-cost comparison, so the curve should probably be rebuilt
as "output recovered from today forward" rather than relabelled again.

### Two smaller corrections to this document

- **§9's "the recovery fraction rises with k and with dry spells" is loose on the second
  half.** §7's own sensitivity table shows that at the calibrated k=15, Santa Cruz (49
  heavy-rain days / 2yr) recovers **0.045** while Bakersfield (21) recovers 0.034 and
  Phoenix (17) recovers 0.038. Dryness does not raise the *fraction*, because the fraction
  is normalised by an annual loss that is itself much larger at a dusty site. The arid /
  C&I case in §9.1 still holds, but it is carried by **absolute dollars** (higher annual
  loss, more sun, lower cost per kW at scale), not by the recovery fraction. The BBF site
  had inherited the same wrong mechanism in its `/solar` copy.
- **The persistent term is uncapped.** `3%/yr × (year − 1)` with no saturation gives 21%
  from grime alone at year 8, against a maximum *total* measured loss of 22.9% anywhere in
  the 891-row NREL set. It needs a ceiling. `YOY_PERSIST_PCT = 3.0` is also still
  UNSOURCED and is not listed as such in §10.

---

## 12. Follow-up (2026-08-10) — the persistent term, settled

**For the live claim: "zero of 1,865" is correct about the soiling this project measures,
and that is a narrower statement than it has been making.** The permanent channel is not
inside the 0.045, it is not inside NREL's 4.70%, and it is not inside `sl_sat`. It is
absent from everything here by construction, so the AOI result should be quoted as *zero
of 1,865 on recoverable soiling* rather than as a flat zero. At the largest permanent
term the outside literature can support, most NEM 2.0 legacy homes in the AOI would flip
positive, so the unqualified version is not safe in a funder conversation.

### What reproduces

- `scripts/analyze/recovery_calendar.py` reproduces §7 exactly, to every printed digit:
  27 heavy-rain days, annual-mean soiling **5.06%**, best clean date 2025-08-08,
  **recovery 0.045**, mean 0.016, worst 0.000.
- `pytest tests/test_recovery.py tests/test_economics.py` — **29 passed**.
- The MERRA-2 leg 401s on this machine (no `NASA_EARTHDATA_TOKEN`), so PM10 falls back to
  its median. The numbers above are therefore the no-AQ path, and they match §7, which
  means §7 was also measured on that path.

### The answer: absent by construction, not absorbed into `sl_sat`

§11 framed this as absorbed-vs-absent and expected the answer to rest on a modelling
argument. It rests on a definition instead. NREL's soiling map defines the quantity it
publishes as:

> the insolation-weighted mean of all daily soiling ratios **assuming perfect cleaning and
> no soiling between detected soiling intervals**

Perfect cleaning is an *assumption of the metric*, not a property of the arrays. A layer
that survives a wash is outside IWSR's definition. `sl_sat = 0.08` was grid-fitted against
IWSR, so it cannot have absorbed a permanent component: there was none in the target.
Degradation is handled separately and upstream, by RdTools' year-on-year estimator, which
is where a slowly accumulating layer would land instead.

Four independent reads on the raw NREL JSON agree, all in
`scripts/analyze/persistent_soiling_probe.py`:

| # | check | result | reading |
|---|---|---|---|
| 1 | censoring vs observation span | 109/255 stations (42.7%) report IWSR `>0.99`, i.e. under 1% *total* annual loss; **18 of them were observed 5+ years** | a 3%/yr permanent layer implies 12 pts by year 5. Not in this quantity |
| 2 | within-station year-over-year slope | all stations **−0.157 ± 0.036 pts/yr**, 95% CI [−0.228, −0.087] (n=891, 146 stations); coastal CA −0.041 ± 0.213, CI [−0.458, +0.375] (n=36) | measured loss does not grow with age at all. +3.0 is far outside both CIs |
| 3 | upper bound | 891 annual values, max **exactly 1.0000**, none above; summary max 0.988 | a ratio pinned at the clean state, not at factory output |
| 4 | sawtooth closure | annual loss reconstructs from the monthly soiling *rates* alone under full recovery: implied reset interval p50 **61 days** (p10 46, p90 83), **100%** inside a plausible 7–180 day window | the measured level needs no floor term to explain it |

Check 4 is the one that closes it numerically: the published loss level and the published
soiling *rates* are consistent with a pure sawtooth that returns to 1.0 every two months,
with nothing left over.

### Consequence 1: §7 stands, with a narrower scope than it states

The 5.06%-vs-4.70% validation in §7 is like-for-like, both sides being the recoverable
sawtooth. **0.045 is correct, and it is the recovery fraction for seasonal soiling.** The
same scope limit runs through the whole Stage 2 chain, because the regression head trains
on `loss_pct = (1 - iwsr) * 100`: `src/risk/loss_model.py` predicts *recoverable* soiling
loss, not total output deficit. That should be said wherever those percentages are quoted.

### Consequence 2: the site's 3%/yr does not double-count, so do not delete it as one

§11's reading 1 prescribed deleting the term as a double-count of the 0.045. **That
prescription is wrong** and acting on it would have removed the only representation of a
real channel. The two terms describe disjoint things: 0.045 is the share of the
rain-resettable sawtooth one wash captures, and the persistent term is a layer rain never
removes, which the sawtooth measurement excludes by definition.

The term still cannot ship as it stands, for three reasons that do not all push the same
way:

1. **Unsourced and uncapped.** `3%/yr × (year − 1)` gives 21 pts at year 8 against a
   maximum *total* measured loss of 22.9 pts anywhere in the 891-row NREL set.
2. **The wrong recovery fraction is applied to it.** `breakeven.html` multiplies
   `R_persist` by the same `recovery` (0.045) as the seasonal term. If the layer is
   permanent then a wash is the only thing that clears it, so its recovery is near 1.0,
   not 0.045. The page currently understates this channel by ~22×.
3. **It decides the page's answer on its own.** See below.

Errors 1 and 2 point opposite ways and partly cancel: 3.0 pts/yr at 0.045 recovery is
0.135 effective pts/yr, against ~0.6 pts/yr for a defensible rate at full recovery. So the
page is currently ~4× too *low* on this channel while using a rate ~5× too *high*.
**Fixing either one alone swings the answer hard in one direction. They have to move
together.**

### Consequence 3: how old, and how many of the 1,865

Section 5 of the probe inverts the economics: for each AOI site, the percentage points of
permanent, wash-only soiling needed before one professional clean nets positive. 1,857 of
the 1,865 sites carry a regression-head loss estimate (kW p50 5.18, loss p50 5.56%). The
seasonal channel currently credits **$7.37/site/yr** (NBT) against a mean cost of
**$190.86**, so nearly the whole cost has to come from the permanent channel:

| | NBT no-battery ($0.1646) | NEM 2.0 legacy ($0.4573) |
|---|---:|---:|
| permanent pts needed to break even (p10 / p50 / p90) | 4.6 / **8.5** / 17.2 | 1.5 / **2.9** / 6.0 |
| age needed at 3.0 pts/yr (the site's term) | 3.8 yrs | 2.0 yrs |
| age needed at 0.6 pts/yr (upper bound, below) | 15.2 yrs | **5.9 yrs** |
| age needed at 0.375 pts/yr (check 2's 95% upper) | 23.7 yrs | 8.8 yrs |
| share clearing by age 10 at 0.6 pts/yr | 19.1% | **86.3%** |
| share clearing by age 10 at 0.375 pts/yr | 0.5% | 60.2% |

Ages are for the **first** wash of a never-washed array, crediting one year of the
recovered layer. Two other framings bracket it:

- **A clean repeated every year never clears on this channel at NBT.** In steady state a
  yearly wash removes only one year's accumulation, so it needs the *rate* to reach 8.5
  pts/yr at the median site. Even the site's inflated 3.0 fails. At NEM 2.0 the bar is 2.9
  pts/yr, which the site's 3.0 clears, but that is two unsourced numbers meeting, not a
  result.
- Crediting the whole undiscounted stream until the layer regrows is the most generous
  reading available and pulls the 0.6 pts/yr NBT median from 15.2 down to **6.3 years**.

**AOI ages.** 130 of the 1,857 sites join a PV permit year: p10 3, **p50 8**, max 10. The
registry starts in 2016, so ages are right-censored at 10 and the true median is older.
The AOI is therefore squarely inside the range where the NEM 2.0 row above flips.

**Why 0.6 pts/yr is the ceiling, and why it is still too high.** Nothing in this repo
measures a permanent layer, so the bound has to come from outside. Any permanent,
age-compounding output loss must appear in the field-measured degradation rate, and
Jordan & Kurtz's compendium (>11,000 rates, ~200 studies) puts x-Si at a median of
**0.5–0.6%/yr**, mean 0.8–0.9%/yr. The 0.6 row above therefore assumes **all** field
degradation is washable grime, which it is not: that number is dominated by encapsulant
discoloration, cell cracking, interconnect failure and PID, none of which a wash touches.
Treat 0.6 as a bound that is certainly too loose, not an estimate. The washable share is
unmeasured, and it is the number the product turns on.

### What would settle it

Direct and cheap, in rough order of value:

1. **Run RdTools `soiling_srr` on a long coastal-CA series with `method='perfect_clean'`
   against the fitted `'half_norm_clean'` default.** NREL's map takes the perfect-clean
   branch; the gap between the two is an estimate of incomplete recovery, from the same
   toolchain that produced our labels.
2. **Skomedal & Deceglie's CODS**, also in RdTools, separates degradation, soiling and
   seasonality self-consistently. The residual it assigns to neither is the candidate.
3. **Pre/post-wash production on one old array**, against its own post-rain baseline
   rather than against nameplate. One site with an inverter API settles the sign.

Until one of those exists, both the site's "clean now" and this document's "zero of 1,865"
rest on the same unmeasured constant, pointing opposite ways.

### The follow-up on BBF-Website (not made here)

Both surfaces, both uncapped, both applying the seasonal recovery to the permanent term:

- `public/tools/dashboard.js:109` — `const YOY_PERSIST_PCT = 3.0;` used at
  `dashboard.js:186-188` (`persistBase * Math.max(0, year - 1)`, no ceiling).
- `public/tools/breakeven.html:487, 514-517` — `plossPct` defaults to 3.0,
  `R_persist = R_persist_base * yearsOfAccumulation`, no ceiling. Line 523,
  `if (R_persist * recovery >= cost) breakevenDay = 0;`, is the persistent term deciding
  the page's answer by itself.
- `public/tools/breakeven.html:386-387` — the explainer prose still carries the retired
  `$0.375/kWh` inside `R_persist($)` and still worked through "0.03 × $4,528 × 2 = $272,
  which already exceeds a typical cleaning cost". The code was fixed in `e2a3cd4`; this
  copy was not, so the page now explains a formula it no longer runs.

Recommended for that follow-up: cap the term, cut the rate to something sourced or to
zero pending measurement, apply a recovery near 1.0 rather than 0.045 to whatever
survives, and label it on the page as unmeasured. Not deleted, and not retuned in
isolation.

### Two notes on this repo's own calibration

- **`sl_sat` was fitted to the wrong side of the trajectory.** Both
  `scripts/predict/calibrate_somosclean.py:54-65` and
  `calibrate_somosclean_slsat.py:80-82` score a candidate by
  `1.0 - sl_series.iloc[-1]`, the **terminal day** of the window (31 December, mid wet
  season in coastal CA), against an **annual-mean** measured IWSR. The objective compares
  a single day to a year. It happens not to matter for §7, whose validation compares
  annual mean to annual mean and lands at 5.06% vs 4.70%, but the fit is not optimising
  the quantity it names and a refit would not be expected to return 0.08.
- The answer to §11's direct question, for the record: **the SOMOSclean trajectory has no
  non-zero floor after a heavy-rain reset, and neither does the measurement it was
  calibrated against.** The first half was already known; the second half is the finding.

---

## 13. Follow-up (2026-08-11): the layer, measured on a real array

**§12 could only bound the wash-only layer from outside the project. This measures it,
on eight years of a revenue-grade meter next to a class-A pyranometer, and finds no
layer to measure.** Every read on that array says the soiling ratio returns to its clean
state at least once in every one of the eight years, and the annual median improves over
the middle six rather than declining with age. That is the first direct evidence for the
0 pts/yr the site now ships, and it comes from a site that should soil harder than
anything in the AOI.

It does not close the question. It is one array, ground-mounted beside Central Valley
farmland rather than on a coastal roof, and the method cannot tell a rain reset from a
paid wash. Both branches of that ambiguity happen to point the same way, which is why the
result is stated firmly anyway. See "what this does not settle" below.

### The data gate, which §12 and the task brief both expected to fail

It does not fail, and the reason it was expected to is out of date. **PVDAQ does not need
an NREL API key.** The whole archive is mirrored on the OEDI open data lake, served
anonymously over HTTPS:

```
https://oedi-data-lake.s3.amazonaws.com/pvdaq/
```

§10's note that this machine has no NREL key is still true and no longer blocking. What
is in there, catalogued 2026-08-11 by walking the bucket:

| | |
|---|---|
| systems with metadata | **1,853** |
| carrying only `ac_energy_kwh` + `ac_power_w` (PVOutput.org residential feeds) | **1,454** |
| carrying a plane-of-array pyranometer, in the main CSV release | **2** (both SAS, Cary NC, 2020-2022 only) |
| inside the California bounding box, still reporting | 838, of which 485 have 10+ years |
| in the coastal Central CA box (36.2-37.7 N, west of 121.4 W) | 113, longest 24 years |

So the obvious move, running this on a long coastal-CA series, is **not** available: all
113 of those are energy-only PVOutput feeds with no irradiance, no module temperature and
no wind. RdTools' sensor workflow needs measured POA, and a clear-sky substitute on a
self-reported residential feed with unrecorded shading, inverter swaps and cleanings would
produce numbers whose spread is the clear-sky model, not the physics.

The usable series is in the 2023 Solar Data Prize collection, which is a separate prefix
in the same bucket and is properly instrumented:

**PVDAQ system 2107, "Farm Solar Array", Arbuckle CA** (38.996 N, 122.134 W). 893 kW DC,
fixed 25° tilt / 180° azimuth, mono-Si Hyundai HiS-M310TI, ABB string inverters.
Revenue-grade AC meter, one POA pyranometer, ambient temperature and wind. Köppen **Csa**,
the hot-summer sibling of coastal Santa Cruz's Csb. Usable span **2017-12-01 to
2025-12-31, 8.08 years**, 64.2% daily coverage after RdTools' filters (56-77% per year
except 2017's partial 26%).

One data defect worth recording: the column the metadata calls
`ambient_temperature` is **Fahrenheit**, not Celsius. Its 99th percentile is 97.2 and its
winter midnights sit near 40. Taken at face value it puts the array 20 °C too hot all
year, and the whole error lands in the temperature correction and therefore in the
degradation term. `scripts/analyze/washable_share_probe.py` decides the unit from the data
rather than the metadata and says which it picked.

### What reproduces

```bash
pip install -e ".[soiling-validation]"      # rdtools 3.2.1, pvlib 0.15.2
PYTHONPATH=. python scripts/analyze/washable_share_probe.py
```

`pytest tests/test_recovery.py tests/test_economics.py`: **29 passed**, unchanged, after
the install. Nothing under `src/` imports rdtools.

### Read 3: degradation, the share a wash cannot touch

| estimator | rate | 95% CI |
|---|---:|---|
| RdTools year-on-year, sensor-normalised | **−0.076 %/yr** (was −0.162; re-measured 2026-08-27 at the corrected gamma −0.0045) | [−0.408, +0.358] |
| CODS | −0.20 to −0.31 %/yr | [−1.29, +0.62] |
| Jordan et al. 2016, x-Si field median | −0.5 to −0.6 %/yr | (literature) |

This array degrades at about a third of the literature median, and the CI reaches zero.
That matters for §12's ceiling: 0.6 pts/yr was constructed by assuming *all* x-Si field
degradation is washable grime. On this array there is only 0.16-0.31 %/yr of total
degradation to hand out, so even the false-by-construction assumption cannot fund a
0.6 pts/yr washable layer here.

The CODS point estimate wanders by about 0.1 points between runs at `reps=128` despite a
fixed numpy seed, because it bootstraps internally. Quote the YoY figure; treat CODS's as
corroboration.

### Read 1: `perfect_clean` vs `half_norm_clean`, and why the gap is not the answer

| method | insolation-weighted mean SR | 95% CI | loss |
|---|---:|---|---:|
| `perfect_clean` (the branch NREL's map takes) | 0.96990 | [0.96770, 0.97215] | 3.01 pts |
| `half_norm_clean` (RdTools default) | 0.93080 | [0.91696, 0.94230] | 6.92 pts |
| **gap** | **3.91 pts** | non-overlapping | |

3.91 points is a **level**, not a rate. Dividing it by the eight years of record would be
wrong: it is the average distance between the two estimators, not something that
accumulated.

**And it is not evidence of incomplete recovery.** `half_norm_clean` draws each interval's
starting point as `1 − |N(0, (1 − inferred_recovery)/3)|`, which is one-sided: it can only
land at or below 1.0. It collapses to exactly 1.0 only when every inferred recovery is
complete, so it will always return an SR at or below `perfect_clean`'s. The gap is the
prior, driven by the detector's own inferred recoveries, and those inherit the 64% daily
coverage: a reset observed three days late reads as a reset that did not finish.

Read the intervals directly instead. Performance at the **start** of each of the 44 valid
intervals, on the recentered scale where 1.0 is a full recovery:

```
p10 0.9630   p50 1.1055   p90 1.1564   max 1.1959   share >= 0.99:  86%
```

The median interval starts **above** 1.0 and 86% start at or above 0.99. The resets in
this record restore the array completely. The 3.91-point gap is an artefact of the
estimator's prior operating on the 14% tail.

### Read 2: CODS, which is the one that settles it

CODS separates degradation, soiling and seasonality, so its soiling-ratio series carries
no degradation by construction. If a layer survives every reset and builds with age, the
**best day of each year** has to get worse. It does not:

| year | n | max SR | p95 | p50 |
|---|---:|---:|---:|---:|
| 2018 | 365 | **1.0000** | 1.0000 | 0.8891 |
| 2019 | 365 | **1.0000** | 1.0000 | 0.9437 |
| 2020 | 366 | **1.0000** | 1.0000 | 0.9522 |
| 2021 | 365 | **1.0000** | 1.0000 | 0.9555 |
| 2022 | 365 | **1.0000** | 1.0000 | 0.9739 |
| 2023 | 365 | **1.0000** | 1.0000 | 0.9789 |
| 2024 | 366 | **1.0000** | 1.0000 | 0.9740 |
| 2025 | 363 | **1.0000** | 1.0000 | 0.8938 |

Maximum pinned at exactly 1.0000 in all eight years, and the 95th percentile with it. This
is §12's check 3 ("891 annual values, max exactly 1.0000") run again on a different array,
at daily resolution, with degradation explicitly removed. Same answer.

The annual median *rises* from 0.889 to 0.979 over 2018-2023, which is the opposite of
accumulation and is most likely commissioning-year effects washing out. 2025 falls back to
0.894 and I have not established why; it is the year whose source files switch to
UTC stamps and a 15-minute grid, so a data-format cause is as likely as a physical one.

A straight line through the whole series gives **−0.833 pts/yr**, i.e. the ratio
*improving* with age, OLS 95% [−0.974, −0.693]. Do not quote that number. The series is not
linear in time, so the fit is a summary of 2018 and 2025 rather than of a trend, and the
OLS interval ignores the autocorrelation a sawtooth necessarily has. The annual table is
the evidence; the regression is a footnote.

### The answer

| quantity | value | how measured |
|---|---:|---|
| degradation, which no wash touches | **−0.076 %/yr** [−0.408, +0.358] (supersedes −0.162, wrong gamma) | RdTools YoY, 8.08 yr |
| standing layer the resets do not clear | **0.0 pts**, 86% of resets complete | interval start points |
| growth of that layer with age | **none detectable**; annual max pinned at 1.0000 in 8/8 years | CODS |
| §12's outside ceiling on the growth | 0.6 pts/yr | Jordan et al., assumption known false |

**As pts/yr with an interval, which is what the brief asked for: 0.0 pts/yr of washable
layer, and the data will not support more than about 0.2 pts/yr even if every point of
this array's measured degradation were washable grime, which it is not.** The honest
interval is [0, 0.2] pts/yr on this array, where the upper end is the total degradation
rate and is there as a bound rather than an estimate.

### What this does not settle

- **One array, wrong microclimate.** Arbuckle is a ground mount beside tilled farmland,
  about 130 km inland of the AOI, in Csa rather than Csb. It should soil *harder* than a
  Santa Cruz roof, so a null here is a strong null, but it is one site and it is not a
  rooftop.
- **The method cannot tell a rain reset from a paid wash,** and an 893 kW commercial array
  plausibly gets washed. Both readings support the same conclusion, which is why this
  section states one anyway: if the resets are rain, rain clears the layer and there is
  nothing permanent; if the resets are washes, then a wash returns the array to exactly
  1.0, which is the `PERSIST_RECOVERY = 1.0` the site now assumes. There is no third
  reading in which the site's current configuration is wrong.
- **The share a wash recovers is still not measured directly.** Nothing in PVDAQ records a
  cleaning. Item 3 of §12's list, pre/post-wash production on one old array against its own
  post-rain baseline, is still the thing that would close it, and it is still cheap.
- **2025.** Unexplained drop in the annual median, in the year whose files change format.
  Worth ten minutes before anyone quotes the annual table in public.

### Consequence for the live claim

"Zero of 1,865 on recoverable soiling" is unchanged and is now better supported: the one
direct measurement available says there is no meaningful non-recoverable channel to add,
on a site chosen to be harsher than the AOI. **Keep the qualifier anyway.** It costs a
clause, it is still true that this project measures only the rain-resettable layer, and
one Central Valley ground mount is not a coastal rooftop. The BBF site's wording as of
2026-08-11, "none of them, counting the dirt that rain takes off", is the right level of
claim and this section does not license dropping the second half of it.

The site's shipped configuration, `YOY_PERSIST_PCT = 0.0` with `PERSIST_RECOVERY = 1.0`
and a 10-point cap, is consistent with everything above. Nothing here asks for a change to
it.

---

## Reproduce

```bash
PYTHONPATH=. python scripts/analyze/rate_sensitivity.py --show-stack
PYTHONPATH=. python scripts/analyze/rate_sensitivity.py --risk-file risk_lossreg.geojson
PYTHONPATH=. python scripts/analyze/rate_sensitivity.py --legacy-recovery   # the A/B
PYTHONPATH=. python scripts/analyze/recovery_calendar.py
PYTHONPATH=. python scripts/analyze/persistent_soiling_probe.py       # §12
pip install -e ".[soiling-validation]"                                # §13, rdtools
PYTHONPATH=. python scripts/analyze/washable_share_probe.py           # §13
PYTHONPATH=. python scripts/predict/train_loss_regressor.py --run-name run_lossreg
pytest tests/test_economics.py tests/test_rates.py tests/test_recovery.py \
       tests/test_site_cluster.py tests/test_loss_model.py
```

Sources (§13): [OEDI PVDAQ open data lake](https://data.openei.org/submissions/4568)
(`https://oedi-data-lake.s3.amazonaws.com/pvdaq/`, anonymous, no API key) ·
PVDAQ system 2107 "Farm Solar Array", Arbuckle CA, 2023 DOE Solar Data Prize ·
[RdTools 3.2.1](https://rdtools.readthedocs.io/) `TrendAnalysis`, `soiling_srr`,
`soiling_cods` ·
[Skomedal & Deceglie, Combined Degradation and Soiling (CODS), IEEE J. Photovolt. 10(6) 1788-1796 (2020)](https://ieeexplore.ieee.org/document/9184543)

Sources (§12): [NREL PV Soiling Map](https://www.nrel.gov/pv/soiling) (the IWSR definition
and the JSON this repo ingests) ·
[RdTools `soiling_srr`](https://rdtools.readthedocs.io/en/stable/generated/rdtools.soiling.soiling_srr.html) ·
[Jordan et al., Compendium of photovoltaic degradation rates, Prog. Photovolt. 24(7) 978-989 (2016)](https://onlinelibrary.wiley.com/doi/abs/10.1002/pip.2744)

Sources: [EIA Electric Power Monthly T5.6.A](https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a) ·
[NREL Q1-2024 PV cost benchmark](https://docs.nrel.gov/docs/fy25osti/92536.pdf) ·
[CPUC ACC / SDG&E export pricing](https://www.sdge.com/solar/solar-billing-plan/export-pricing) ·
PG&E Cal. P.U.C. Sheets 61364-E / 61126-E / 60706-E · 3CE residential rate sheet 2026-02-15
