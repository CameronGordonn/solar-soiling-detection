# Craig call brief — the soiling economics problem

**2026-08-19.** Everything here is measured and reproducible in-repo. Sources for each
number are named inline so nothing has to be taken on trust.

---

## 1. The 30-second version

The detection half of the product works. The **cleaning-value** half does not clear, and it
is not close.

> **Zero of 1,865 detected Santa Cruz sites show a positive expected net from a cleaning — at
> any electricity rate up to $0.70/kWh, at any system size, over 2,000 Monte Carlo draws
> each. Max `prob_net_positive` across all sites = 0.0000. Not one draw in 3.7 million was
> positive.**

Always say it with the qualifier: **"zero of 1,865 on *recoverable* soiling"** — the dirt
rain takes off. That is the only channel this model measures, and the distinction is the
entire remaining opportunity (§5).

Source: `docs/ECONOMICS_GROUNDING_20260809.md` §8; reproduce with
`scripts/analyze/rate_sensitivity.py`. **Re-confirmed 2026-08-19** after the tariff-vintage
correction raised the AOI loss total by 156% — see §2. The verdict survived its own largest
upward correction, which is the strongest thing that can be said for it.

---

## 2. Why it doesn't clear — the arithmetic, on a 6 kW home

| step | value | where it comes from |
|---|---|---|
| annual production | 9,000 kWh | 1,500 kWh/kWp/yr — **UNMEASURED**, PVWatts-typical coastal CA |
| annual recoverable soiling loss | 5.56% = 500 kWh | XGBoost quantile regression head, AOI median |
| share one professional wash recovers | **0.045** | measured, `src/risk/recovery.py` |
| output actually bought back | **22.5 kWh** | |
| worth at NBT blended $0.1646/kWh | **$3.71/yr** | sourced tariff stack |
| worth at NEM 2.0 retail offset $0.4573/kWh | **$10.29/yr** | sourced tariff stack |
| cost of the wash | $90 light-pro / $150 professional | our set price points |

**Shortfall on this generic 6 kW home: 9x to 40x.**

### Update, same day: the tariff mix moves this a lot, and it still does not clear

The chain priced every site as NBT no-battery. That was an assumption nobody had tested.
CaliforniaDGStats publishes every Rule 21 interconnection; filtered to the five AOI zips,
residential NEM PV, n = 7,536:

| regime | share |
|---|---|
| NEM 2.0 | 52.5% |
| NEM 1.0 | 37.6% |
| **legacy total** | **90.1%** |
| NBT | 9.9% |

Population-blended value of a lost kWh: **$0.1646 -> $0.4284, a 2.60x understatement.**
**AOI annual recoverable loss total: +156%** ($671,618/yr across 1,865 sites).

**The verdict did not move: still 0 of 1,865, still `max prob_net_positive = 0.0000`.**

**But the gap is now a number worth arguing about.** Shortfall = modelled cleaning cost /
annual recovered dollars, all 1,865 sites, against the real per-panel cost schedule:

| system size | n sites | median shortfall | best in band |
|---|---|---|---|
| 0–5 kW | 877 | 13.81x | 7.95x |
| 5–10 kW | 796 | 7.40x | 4.15x |
| 10–15 kW | 119 | 4.45x | 3.56x |
| 15–30 kW | 35 | 4.10x | 3.25x |
| 30–60 kW | 16 | 3.63x | 3.30x |
| 60–150 kW | 12 | 3.58x | 3.03x |
| 150+ kW | 10 | 3.02x | **2.76x** |

**Best site in the entire AOI: 2.76x short** (apn:002-094-30, 196 kW). Not one of 1,865 pays.

Two things to say carefully here:

1. **Size targeting bought a 5x improvement and then stopped.** 13.8x median at
   residential scale down to 3.0x at 150 kW+ — but it *asymptotes* near 3x, because both
   recovered dollars and cleaning cost scale with panel count. The 0–5 kW band only looks
   worse because the $90/$150 **minimum charge** dominates a 12-panel job. Above roughly
   15 kW there is no more room in the cost model. **More size targeting will not close it.**
2. **Do not quote a large array's recovery against the $90 light-pro minimum.** That
   comparison makes the best 38.6 kW site look 1.41x short when the same site is **4.28x**
   short against its real 88-panel cost. It is the single easiest way to overstate how close
   this is, and it will not survive Craig asking one follow-up question.

So the correct sentence is: **the gap closed from ~40x to ~2.8x, and it closed by correcting
the tariff and by looking at bigger roofs — not by any change in the physics.** Closing the
last 2.8x is what §4 is about.

### The single number that was carrying the whole product

`recovery_frac` was **0.90** — the assumption that one cleaning captures 90% of a year's
soiling loss. Nobody had ever measured it. Measured, it is **0.045 — a 20x overstatement.**

Mechanism: at the calibrated re-soiling constant k=15 equivalent-days, an array returns to
its annual-mean soiling in **about two weeks**, and Santa Cruz gets **27 heavy-rain (≥10 mm)
resets a year for free**. One cleaning buys roughly **one array-month of clean out of twelve**.

Validation, and the reason I state this firmly: the same SOMOSclean trajectory reproduces
NREL's measured coastal-CA annual loss — model 5.06% vs measured 4.70%. And it is robust to
k: even at k=60 (4x the calibrated value) **in Phoenix**, recovery is only 0.125, still 7x
below the retired 0.90.

With 0.90 restored, 44.8–95.4% of the AOI "should clean." That constant was the product.

Source: §7 + §1. Reproduce: `scripts/analyze/recovery_calendar.py`.

### Four other constants that were wrong

| constant | was | now | direction |
|---|---|---|---|
| `RISK_TO_LOSS_PCT = 8.0` | multiplied a **calibrated classification probability** by 8 | removed — replaced by a real regression head | category error |
| `BASE_RATE` | $0.25/kWh flat | $0.165 NBT -> **$0.4284 AOI-blended** (90.1% legacy, measured) | worse, then better |
| per-polygon costing | 1 trip charge per polygon | 1 per **site** | better (2.3x) |
| `PANEL_KW` / `M2_PER_KW` | 0.32 / 5.67 | 0.44 / **5.41** (packing 0.84, measured on 130 permit-kW matches) | slightly better |

The two corrections that make it worse are far larger than the three that make it better.

### The electricity rate is *lower* than we assumed, not higher

Bill-reconciled PG&E E-TOU-C + 3CE stack (validated to ±$0.22/month over 11 consecutive real
bills, each component carrying its Cal. P.U.C. sheet citation):

- Production-weighted **retail offset: $0.4573/kWh**
- Production-weighted **NBT export credit: $0.0392/kWh** — a **12x spread**

**Only 5.5% of a south-facing array's annual output lands in the 4–9pm peak.** Soiling scales
every hour down, so ~94% of soiling-lost kWh are off-peak, and the export credit is lowest
exactly at midday when PV generates. The retired flat $0.25 implied 50% midday
self-consumption — plausible for a battery home, too high for the no-battery majority.

**The one genuinely valuable finding here: a NEM 2.0 legacy home is worth 2.8x more per lost
kWh than an identical NEM 3.0 home.** That is a public-records join on interconnection date,
not a modelling problem. Sharpest targeting signal in the project.

Caveat to disclose: the ACC export table imported is **SDG&E's** standing in for PG&E's —
same CPUC methodology, level unverified.

---

## 3. Would tilt angle help? Measured answer: no, and we know why

**Short version: adding tilt won't move it, and we have a controlled experiment proving the
mechanism.**

`tilt_deg` sits in the trained model but was **missing from the AOI feature matrix** and
silently median-filled. Together with `worldcover_*` and `month_of_year` that is **21.8% of
model importance** being fabricated. Obvious fix, right?

> Re-measured 2026-08-12: the share of importance sitting on AOI-constant features is **58.1%**,
> not 21.8% — the smaller figure counted only the fully-absent features and missed the all-NaN and
> low-variance ones. It makes the point below stronger, not weaker.

**We wired WorldCover in properly and it did nothing:**

```
BEFORE (median-filled)  p10 5.08  p50 5.55  p90 5.86   span 0.78 pts
AFTER  (supplied)       p10 5.07  p50 5.53  p90 5.84   span 0.77 pts
```

And **the sign came out backwards** — arrays on tree pixels scored *lower* (5.36%) than
built-up (5.55%). The reason is the important part: the model learned `worldcover_tree` from
NREL **stations**, where a tree pixel means "forested rural region — wetter air, less dust",
**not** "a tree overhanging this roof." Same feature name, different referent.

### The structural blocker, stated precisely

**The NREL labels are station-level.** 891 annual rows, 146 stations, 15 states. AOI feature
standard deviations are **0.3%–23%** of the training SDs — weather does not vary across a
20 km coastal AOI. **58.1% of model importance sits on features effectively constant across
the AOI.**

So: a model trained to separate 15 states has almost nothing left when shown 1,865 homes in
one town. **No feature learned from station-level labels can fix per-roof ranking, because
the training set contains zero examples of per-roof variation.** Tilt is exactly this class
of feature. Expect the same ~0.02-point result WorldCover gave.

### And here is the number that reframes the whole question

Across the 1,710 residential-scale sites (2–15 kW):

| driver | p10 → p90 | ratio | **share of per-home dollar variance** |
|---|---|---|---|
| **System size (kW)** — from detection | 2.89 → 9.26 | **3.20x** | **86.7%** |
| **Roof orientation (POA)** — from 3DEP lidar | 0.77 → 1.00 | **1.30x** | **11.1%** |
| Soiling loss % — the XGBoost head | 5.07 → 5.83 | 1.15x | **2.2%** |
| Tariff vintage (NEM 2.0 vs NBT) | — | 2.78x | data join, not modelling |

**97.8% of the per-home dollar spread comes from two things detection already measures —
array size and roof orientation. The soiling model contributes 2.2%. Doubling its spread
would add less differentiation than joining one permit file.**

Roof orientation is new as of 2026-08-19 (per-array tilt and azimuth fitted to USGS 3DEP
lidar, 3,068 of 3,362 arrays). It is a **third** driver, not a re-slicing of the first: the
earlier two-way split in `SOILING_LEVEL_INVESTIGATION.md` read 97.3% / 2.5%, and orientation
came out of size's share, not the soiling model's. Caveat to state if pressed: `poa_rel` is
per-array while size and loss are per-site, so 11.1% is an estimate rather than a
like-for-like decomposition.

Source: `docs/HANDOFF_roof_geometry_for_paper.md` Result 1 (supersedes the two-way table in
`docs/SOILING_LEVEL_INVESTIGATION.md`).

---

## 4. Moss / lichen — this is the live channel, and the physics is now measured

Rain removes dust. **Rain does not remove moss, lichen, algae, leaf litter, sap or built-up
bird droppings — and shade plus moisture actively encourage them.** NREL's IWSR metric is
defined as the insolation-weighted daily soiling ratio *assuming perfect cleaning*, so this
entire channel is **outside the labels, outside `sl_sat`, outside the 0.045, by
construction.** It cannot be added to the current model by feature engineering.

### Why it could matter enormously: the loss is wildly nonlinear

A shaded cell current-limits its whole series string; bypass diodes cut the substring out.
So a few square centimetres costs vastly more than its area fraction — **and it saturates at
the substring.** Modelled at cell granularity with pvlib `bishop88` (single-diode + bypass
diodes + reverse-bias avalanche breakdown), 60-cell 295 W module, 20-module array:

| occlusion | full-cell MLPE | full-cell string |
|---|---|---|
| 1 dead substring | 1.75% | 1.75% |
| **4 modules, 1 dead substring each** | **7.00%** | 7.00% |
| 8 modules | 14.00% | 14.00% |
| 1 bird dropping over 60% of one cell | **0.93%** | 0.94% |

**A thin moss line along the bottom edge of four panels is worth 1% to 21% of system output**,
depending on band thickness, mounting orientation and module era. Scattered bird droppings
never clear the bar at any tariff — that is now measured, not asserted.

Mounting orientation matters a lot (same moss, same roof, edge band f=0.5 across 4 modules):

| | full-cell MLPE | full-cell string | half-cut MLPE |
|---|---|---|---|
| landscape | 7.00% | 7.00% | 7.00% |
| **portrait** | 8.20% | **21.00%** | 4.57% |

Cross-checked against **PVsyst's** module documentation on the twin half-cut architecture —
it matches on all three points, including the landscape null result. (Qualitative agreement;
the directly comparable studies are paywalled.)

### The breakeven bars

| regime | wash | recovered output needed to break even |
|---|---|---|
| NEM 1.0/2.0 ($0.4573) | light-pro $90 | **2.2%** |
| NEM 1.0/2.0 | professional $150 | **3.6%** |
| NBT no-battery ($0.1646) | light-pro $90 | **6.1%** |
| NBT no-battery | professional $150 | **10.1%** |

**A wash pays here only if the roof carries continuous edge occlusion across several
modules.** That is a falsifiable, specific prediction about what a qualifying roof looks
like, and it is exactly the thing aerial imagery can see and a dust model cannot.

### The challenge: the deciding variable is sub-pixel

**We cannot measure band thickness from the air at any resolution we have.** The deciding
difference is about **30 mm**; our best imagery is **6 cm native / 78 mm per pixel** (249/249
AOI tiles already on disk at 0.078 m/px). We can resolve panel-level darkening, canopy
overhang and debris. We cannot resolve the one number that decides it.

**So the next step is not more computing.** It is an afternoon on foot with a camera,
photographing the mossiest arrays in town from the street and measuring the band against the
module frame (35–40 mm) or cell pitch (156 mm) for scale. It is a **max statistic**, not a
sample — the question is whether f reaches 0.2–0.3 *anywhere in this town*.

**Written stop rule:** if the thickest continuous band findable after a full afternoon in the
high-canopy neighbourhoods is below **f = 0.1 (~16 mm)**, the whole thesis is dead on
physics, and the honest output is a short public writeup saying so.

Source: `docs/AOI_CLEANING_TARGETING_PLAN.md` v7; `scripts/analyze/substring_shade_loss.py`.

---

## 5. What ground truth we'd need

### What we already measured, on a real array

The persistent-layer question was settled directly, not argued. **PVDAQ system 2107
("Farm Solar Array", Arbuckle CA)** — 893 kW, 8.08 years, revenue-grade AC meter next to a
class-A POA pyranometer. Free, anonymous, no NREL API key needed (OEDI open data lake).

| quantity | result |
|---|---|
| degradation (no wash touches it) | **−0.162 %/yr**, 95% CI [−0.520, +0.175] — RdTools YoY |
| standing layer resets don't clear | **0.0 pts**; 86% of 44 intervals start at ≥0.99 |
| growth of that layer with age | **none detectable** — CODS annual max soiling ratio pinned at exactly 1.0000 in **8 of 8 years** |
| honest interval | **[0, 0.2] pts/yr**, upper end = total degradation, a bound not an estimate |

> **Corrected 2026-08-27:** the degradation figure above was produced with the module
> temperature coefficient at -0.0035, which PVDAQ's metadata does not support for system
> 2107 (it publishes no module record, so the labelled default -0.0045 applies). Re-measured:
> **-0.076 %/yr, 95% CI [-0.408, +0.358]**. The conclusion is unchanged in both direction and
> reading, and **the CODS 1.0000-in-8-of-8 result that the persistent-layer claim rests on does
> not move at all**. See `docs/GAMMA_RESOLUTION_20260827.md` Answer 4.

This kills the uniform persistent-layer hypothesis on a site that *should* soil harder than
the AOI. It does **not** constrain the localized coastal moss channel — 2107 is a ground
mount beside farmland, Csa climate, no tree canopy, and a whole-array ratio averages away a
few bad modules on an 893 kW system.

### The hard constraint that reorders every experiment

`scripts/analyze/washtest_power.py`, on 2024 data (225 usable days):

**A conventional before/after wash test on one array saturates at ~5% of output minimum
detectable effect, and does not improve with more days.** Weather arrives in multi-day
blocks, so autocorrelation rises at almost exactly the rate extra samples would have helped.
And that is a *floor* — 2107 has an on-site pyranometer and 893 kW of spatial averaging; a
residential roof normalised against satellite irradiance is 1.5–2x worse.

Consequences:
1. **"Wash 20 random homes and measure the average" is underpowered AND aimed at the wrong
   estimand.** Random Santa Cruz roofs are mostly clean; it spends a season and $3,000
   measuring a number we already know is near zero.
2. A conventional test can just about resolve the **NBT** bar (6.1–10.1%). It **cannot**
   resolve the **NEM 2.0** bar (2.2–3.6%) — the case that matters — without pooling 6–12
   homes and a full dry season.
3. **The experiment only becomes feasible aimed at roofs where a large effect is predicted.**
   Selecting on visible soiling isn't a shortcut, it's what makes the study possible.

### The plan, ranked by confidence gained per dollar

| # | Option | Cash | My time | Calendar | What it settles |
|---|---|---|---|---|---|
| **0** | **City of Santa Cruz permit records request** | $0–fee | ~1 day + wait | 2–6 wks | Tariff vintage for the **1,310** city sites at zero coverage today: AOI coverage 17% → ~88%. Fire it now, it blocks nothing |
| **1** | **Ground-photo survey of edge-band thickness** | **$0** | **1 afternoon** | **1 day** | **The distribution of f — the one variable the physics says decides everything. Can kill the thesis outright** |
| **2** | Split-array wash, ONE volunteer with microinverters/optimizers | ~$150 | ~3 days | 4–6 wks | Per-module recovery directly, ~1.5% MDE in two weeks, because weather is common-mode and cancels between halves |
| **3** | Blind prevalence read of the 6 cm imagery already on disk | $0 | ~2 days | 1 wk | How many of 1,865 roofs show continuous edge occlusion, with a CI + inter-rater κ. Only worth it if #1 comes back positive |
| **4** | Whole-array before/after on 6–12 string-inverter homes | $900–1,800 | ~2 wks | one dry season | The estimand the product actually ships. Locked to May–Oct; **the 2026 window is nearly closed** |
| **5** | Inverter APIs, opt-in, observational | $0 | ~1 wk | ongoing | Per-module dispersion across many homes; free standing recruitment channel for #2 |

**Ordering rationale:** the failure mode is spending two days on a beautiful stratified
imagery read, finding 8% prevalence, feeling encouraged, then discovering the bands are
15 mm and never mattered. Option 1 costs an afternoon and forecloses that.

**Privacy:** Option 1 photographs private homes from public land. No addresses, no APNs, no
house numbers in frame; cross-street only; stored outside both repos.

---

## 6. What's still unsourced — disclose these

- **`MIN_PRO_SERVICE` ($150)** — our set price point, not a market quote. **It decides most
  residential cases on its own.** Getting three real quotes is a phone call, and it is still
  worth making — but **as of 2026-08-19 the answer it can produce is bounded, and it is not
  a rescue.** Published CA market pricing runs $5–12/panel (some sources $10–20), with
  $120–200 typical for a 10–20 panel residential system. Our schedule is $8.00/panel at 3 kW
  declining to $5.00 at 200 kW, with $150/$90 floors — i.e. **at or below the bottom of the
  published range**, so it currently flatters the product. Re-priced across all 1,865 sites:

  | cleaning price | best site, basic rinse | sites that pay |
  |---|---|---|
  | market low ($5/panel, $120 min) | 2.47x short | **0** |
  | **repo today ($8→5/panel, $90 min)** | **2.76x short** | **0** |
  | market mid ($10/panel, $175 min) | 4.84x short | **0** |
  | market high ($20/panel, $250 min) | 9.40x short | **0** |

  **Zero at every price on the board.** The most favourable published rate in California
  improves the best site from 2.76x to 2.47x and changes no decision. For the best
  *residential* site to break even the clean would have to cost under **$30**, against
  $30.55/yr recovered — there is no market at that price and there will not be one. Make the
  calls to close the provenance gap honestly, not because the number is in play.
- **1,500 kWh/kWp/yr** production anchor — PVWatts-typical, never measured for this AOI.
- **NBT σ = 0.30** (midday self-consumption share) and the per-panel rate schedule.
- **ACC export table is SDG&E's**, substituting for PG&E's.
- **`sl_sat` was fitted to the wrong side of the trajectory** — the objective scored 31
  December (mid wet season) against an *annual-mean* measured IWSR. Doesn't affect §2's
  annual-mean-to-annual-mean validation, but a refit wouldn't be expected to return 0.08.
- **Two calibration audits point opposite ways and neither is big enough to matter**: the
  "coastal-CA p50 4.70%" anchor is actually 66 of 66 *inland Central Valley* stations
  (genuinely coastal CA reads 2.80% — makes the product *worse*); and k=15 implies ~10x the
  day-one loss of Mejia & Kleissl 2013 (makes it better). Fixing either alone produces a
  confidently wrong number. **The answer stays "no" across the whole plausible range**, so
  it is not on the critical path.

---

## 7. Other things worth raising with Craig

1. **The detection stack is the asset, and it passed its gate.** RF-DETR @728 + SAM2,
   Apache-2.0 (we're off AGPL). Test tile-level box-F1 **0.826**, 95% CI [0.798, 0.853],
   n=585 GT — clears all three gate conditions (F1 ≥ 0.75, CI-lower ≥ 0.70, recall ≥ 0.70),
   with conf tuned on val and frozen before touching test. Area is unbiased (median area/GT
   = 1.01, 0% roof-grab). **This works and is sellable.**

2. **The honest pivot: sell the diagnosis, not the cleaning.** Detection + area + parcel join
   is sound. The product today can truthfully tell a homeowner *"your array loses ~$87/yr to
   soiling; a $150 clean recovers about $4 of it; don't."* That is a real individualized
   performance stat with money attached. What it can't do is produce a **lead list**, because
   nothing in it ever says yes.

3. **The market may be elsewhere, and the physics says where.** Recovery rises with dry
   spells in absolute dollars: genuinely arid, high-dust, agricultural-adjacent, **C&I-scale**
   sites. Coastal residential single-cleans are the worst possible starting market and we
   picked it because it's where we live. (Careful: the recovery *fraction* does not rise with
   dryness — Phoenix reads 0.038 vs Santa Cruz 0.045, because it's normalised by a much larger
   annual loss. The arid case is carried by absolute dollars, more sun, and lower cost per kW
   at scale.)

4. **Publishing the negative result is worth something.** A rigorously measured "cleaning your
   coastal solar panels doesn't pay, here's the arithmetic" is credible content, is true, and
   is the opposite of what every panel-cleaning company says. It also protects us: we found a
   live calculator on our own site telling visitors to clean on day 0 while our report card
   said don't, because it still ran the retired 0.90. Already fixed.

5. **Ask Craig for:** (a) a line on C&I / agricultural site access, (b) any contact with an
   inverter-API-equipped homeowner willing to donate half a roof, (c) a sanity check on
   whether a diagnosis-only product has a buyer, (d) whether the negative result changes the
   funding conversation, given we need funding by end of summer.

6. **Deadline pressure worth naming:** Option 4 is locked to the May–October dry season. If
   the moss channel is going to be settled with a dollar number this year, Options 0 and 1
   have to happen in the next couple of weeks.

---

## Reproduce everything

```bash
PYTHONPATH=. python scripts/analyze/rate_sensitivity.py --show-stack
PYTHONPATH=. python scripts/analyze/rate_sensitivity.py --legacy-recovery   # the 0.90 A/B
PYTHONPATH=. python scripts/analyze/recovery_calendar.py                    # the 0.045
PYTHONPATH=. python scripts/analyze/persistent_soiling_probe.py             # NREL probe
PYTHONPATH=. python scripts/analyze/washable_share_probe.py                 # PVDAQ 2107
PYTHONPATH=. python scripts/analyze/substring_shade_loss.py                 # moss physics
PYTHONPATH=. python scripts/analyze/washtest_power.py --year 2024           # the 5% MDE
pytest tests/test_economics.py tests/test_rates.py tests/test_recovery.py
```

**Full detail:** `docs/ECONOMICS_GROUNDING_20260809.md` (the audit) ·
`docs/AOI_CLEANING_TARGETING_PLAN.md` (the moss thesis + ground-truth plan) ·
`docs/SOILING_LEVEL_INVESTIGATION.md` (why the model can't rank homes)

**External sources:** NREL PV Soiling Map (IWSR definition) · NREL Q1-2024 PV cost benchmark ·
Jordan et al., *Compendium of PV degradation rates*, Prog. Photovolt. 24(7) 978-989 (2016) ·
Skomedal & Deceglie, *CODS*, IEEE J. Photovolt. 10(6) 1788-1796 (2020) · RdTools 3.2.1 ·
pvlib `bishop88` · PVsyst half-cut module documentation · CPUC ACC / SDG&E export pricing ·
PG&E Cal. P.U.C. Sheets 61364-E / 61126-E / 60706-E · 3CE residential rate sheet 2026-02-15 ·
OEDI PVDAQ open data lake (system 2107, 2023 DOE Solar Data Prize)
