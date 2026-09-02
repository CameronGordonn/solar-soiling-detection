# White paper — rewrite brief for Josh and Akshitha

**2026-08-23, Cameron.** Supersedes the review notes in
[`PAPER_METHODS_DRAFT.md`](PAPER_METHODS_DRAFT.md) (written 2026-07-29), which is now itself
partly stale: its Part A prose describes a detection stack that no longer exists, and its §2.3
describes a decision model whose central constant has since been measured and found 20x wrong.
Keep that file for the parts marked "still good" below; work from this one.

Every number here is measured and reproducible in-repo, with the source named. Where something
is unverified I say so, and those are flagged **⚠ do not publish yet**.

---

## 0. The frame, and why it changes

Josh's draft is organised as *"we built a thing that flags soiled panels."* Between 2026-07-29
and today the pipeline got better and the product got a verdict, and the verdict is negative.
Both halves are now measured well enough to defend, so the paper should say both.

We are also writing grant applications off the same body of work, and they use this structure.
The paper should match it, because a reviewer's first question is the same in both places:
*what do you actually know?*

**Act 1 — what is built and gated.** Detection works and is checkable. Tile-level box-F1 0.826
on test, 95% CI [0.798, 0.853], confidence tuned on validation and frozen before test was
touched. Apache-2.0, off AGPL. Area unbiased, median mask/GT area ratio 1.01. In the same
15 km² footprint where the old stack found 334 arrays we now find 3,362, and independently
measured recall against county permit records went from 10% to 74%.

**Act 2 — what was measured and disproved.** Two negative results, both controlled, both with
the mechanism identified. (a) A climatological soiling model trained on station-level labels
**cannot rank individual rooftops**, and we can show exactly why. (b) The cleaning-value product
**does not clear** in coastal Santa Cruz, zero of 1,865 sites, and the single constant that had
been carrying it was assumed at 0.90 and measured at 0.045. That verdict then survived its own
largest counter-correction, which is the property that makes a negative result publishable
rather than merely discouraging.

**Act 3 — what remains open, stated as a falsifiable proposal.** The channel our labels exclude
by construction (moss, lichen, algae, the stuff rain does not remove), the physics that says it
could be worth 1% to 21% of output, the sub-pixel measurement problem that stops us settling it
from the air, and the cheap ground experiments that would settle it, each with a written stop
rule.

> **Narrowed 2026-08-27 (added by Claude; Cameron to accept or cut).** The moss channel was
> built and run against real per-roof data, and its *economics* are now closed the same way
> dust's were: zero of 2,494 geometry-scored arrays clear a wash, best case losing about $30.
> Three causes, largest first: NEC 690.12(B)(2) has effectively mandated module-level
> electronics since 2019, which defuses the substring nonlinearity (21% string vs 8% MLPE for
> the same band); the $150 minimum service charge binds at every residential size against a
> 3.5 kW median array; and the 36 arrays with both pre-2017 hardware and NEM 2.0 are *smaller*
> than average. See `docs/PIPELINE_AUDIT_AND_PLAIN_REPORT_20260827.md` §2.
>
> What stays open is the **measurement**, and it is the stronger claim anyway: nobody has
> measured biological soiling's power cost in a Mediterranean climate. Porcar et al. 2018
> confirmed biofilm on panels in Berkeley but measured no power loss, and Santa Cruz's
> growth-weighted Time of Wetness is 2,444 h/yr against Berkeley's 2,468, a ratio of 0.99.
> Act 3 should propose the measurement, not the product.

**Consequence for the title.** "Flagging Soiled PVs" presupposes the flagging works at the level
the draft implies. Three options, in my order of preference:

1. *Rooftop solar soiling from public data alone: a detection pipeline, a per-home dollar model,
   and the limits of both*
2. *What public data can and cannot tell you about rooftop solar soiling*
3. *Detecting rooftop arrays and pricing their soiling without production data: results and
   negative results from a Santa Cruz pilot*

---

## 1. Kill list — claims that must come out

These are not style edits. Each one is a number a reviewer can check and find wrong.

| # | In the draft | Status | Replace with |
|---|---|---|---|
| K1 | "YOLO-based segmentation pipeline" (§1.3, §2.1) | **Dead.** We migrated off Ultralytics to escape AGPL | RF-DETR @728 + SAM2, both Apache-2.0. §2 below |
| K2 | "SAHI F1 0.570" (§2.1, §4.1) | **Not comparable to anything current.** Different labels (2.1x objects on the same footprint), different imagery, and the current path contains no SAHI at all | Tile-level box-F1 **0.826**, CI [0.798, 0.853]. §2 below |
| K3 | "the detector recovers about 10% (22 of 212)" (§2.1) | **Superseded, measured today** on the same denominator | **73.6%** (156/212) imaged-era, **55.3%** (240/434) all-years. §3 below |
| K4 | "334 rooftops mapped" (abstract, §3, §3.1) | **Superseded.** Also contradicts the live dashboard, which has shown 3,362 since 2026-08-13 | **3,362 arrays across 1,865 sites** |
| K5 | "the typical system loses about 7.2% of its output to summer soiling, or roughly $291 to $541 a year" (§3.1) | **No source exists.** I looked in 2026-07 and again today. It appears in no repo artifact, and it points the opposite way from our own economics | Delete. The honest number is AOI median **5.53%** modelled recoverable loss, which is itself above every nearby measurement (§5), worth roughly **$4 to $10/yr recovered by a wash** |
| K6 | "at a 3%-per-season loss on a 6 kW system the model returns no clean. A high-soiling home at 6% clears it... tens of dollars a year net" (§2.3) | **Wrong.** That arithmetic ran `recovery_frac = 0.90`, measured at 0.045 | **Zero of 1,865 sites clear**, at any rate up to $0.70/kWh. Nothing clears at 6% either. §4 below |
| K7 | "The pipeline's value is identifying the minority of arrays where cleaning pays, and that minority sits inland, not on the coast" (§2.3) | **Unmeasured.** We never scored an inland AOI. It is a plausible direction and it is stated as a finding | Either cut, or restate as a hypothesis with the mechanism (recovery rises with dry-spell length in absolute dollars, though the recovery *fraction* does not) |
| K8 | "By year three, two years of carryover have stacked up, and the persistent loss has doubled relative to year two" (§3.2) | **Tested and not supported.** 8.08 years of revenue-grade data on PVDAQ system 2107 show no detectable growth of a standing layer, CODS annual max soiling ratio pinned at 1.0000 in 8 of 8 years | §6 below. The compounding channel survives only in its localised form, which is nonlinear and *saturating*, not compounding |
| K9 | "Every stage in production works" (§4.1) | **Overclaim**, three paragraphs before a limitations section that omits the biggest limitation | Every stage runs; one of them returns "no" for every home in the AOI, and that is the result |
| K10 | "clears its validation gates" for the risk model (§4.1) | Defensible only with the interval printed | Point estimate 0.710 clears 0.70; 95% CI [0.676, 0.742] straddles it. Say both |
| K11 | "satellite array identification" (§1.3), "arial overview" (§3.1) | Wrong and a typo. NAIP and the county imagery are both aircraft-flown | "aerial" throughout. Global find-replace on "satellite" and "arial" |
| K12 | "Higher resolution... is expensive to obtain. Planet Labs and Maxar..." (§4.2) | **Too absolute, and we disproved it ourselves** | Santa Cruz County serves 2025 imagery free at 6.3 cm native. We ran the whole pilot on it. County and state imagery programmes are the realistic path, not commercial licensing |

---

## 2. Act 1, part one — detection, current numbers

**Stack.** RF-DETR @728 (Apache-2.0) plus an optional SAM2 mask stage (Apache-2.0). The
Ultralytics YOLOv11 stack is AGPL-3.0 and was a genuine obstacle to anyone deploying this;
migrating off it was a deliberate project goal and it is done. Gate A (parity with YOLOv11 on
the same chips) passed 2026-08-06; the full-train checkpoint passed the shipping gate
2026-08-07.

**Imagery.** Santa Cruz County 2025 aerial orthoimagery, 249 tiles, **14.97 km² actually
imaged** (the bounding box is 23.71 km², and the difference matters for any recall denominator).
True ground GSD **0.204 m**; note the tiles are stored in EPSG:3857 where the pixel size reads
0.256 m, inflated by 1/cos(37°). The county service natively serves **6.3 cm** and we
under-requested by 3.3x linear; that is a finding worth one sentence rather than an
embarrassment, because it means the ceiling on this method is higher than what we report.

**Dataset.** `data/yolo/scc21`: 976 chips (680 train / 100 val / 196 test), 3,894 chip polygons,
rebuilt 2026-08-07 from 244 of 249 tiles. **Two ground-truth counts exist and they are not
interchangeable** — chip-level val 385 / test 636, tile-level val 340 / test 585. The gate
scores tile-level. A tile-level F1 quoted against 636 is wrong by construction.

**The gate itself, which is the thing to describe in the paper.** It is defined by an executable
script (`scripts/detect/eval_tile_f1.py`), not by prose. Tile-level detection F1 at box-IoU
≥ 0.50, micro-averaged, measured through the production inference path (whole tile → 2×2 grid of
640 px chips → one pass per chip → NMS seam merge at IoU 0.55). Three protocol choices are worth
a paragraph each, because they are the parts most papers get wrong:

- **Confidence is tuned on validation and frozen, and test is the reported number.** Our own
  earlier manifest tuned on test and thereby spent the held-out split. The penalty for doing it
  honestly is small and now measured: test's own best-F1 is 0.8431 against 0.8260 at the
  val-frozen threshold.
- **Matching is on boxes, not masks**, so the gate is invariant to whether SAM2 is in the
  pipeline. SAM2 changes area accuracy, not what was detected. Conflating the two leaves you
  unable to say which stage regressed.
- **The 95% CI bootstraps over tiles, not objects.** Arrays within a tile are correlated;
  resampling objects gives an interval that is too narrow.

**Result.**

```
rfdetr_w2_20260807   conf* 0.50 tuned on val and frozen
test   P 0.850   R 0.803   F1 0.8260   95% CI [0.798, 0.853]   n_gt 585
gate   F1 >= 0.75  AND  CI-lower >= 0.70  AND  recall >= 0.70   -> PASS on all three
```

**A methods point worth making, because it generalises.** We also scored the previous checkpoint
through the identical path (F1 0.8015, CI [0.773, 0.830]). The two marginal intervals overlap
heavily and each point estimate sits inside the other's interval, so the naive comparison says
"within noise". That is the wrong test: both models were scored on the same 49 tiles, so the
comparison is paired. Bootstrapping the per-tile difference gives **+0.0246 F1, 95% CI [+0.0051,
+0.0441], P(W2 > W1) = 0.993**, concentrated in recall (+0.0479). Doubling the training set
produced a real gain that the marginal-CI comparison would have thrown away.

**Why SAM2 is in the product path, and the negative results around it.** The gate is box-based
and unaffected by SAM2. Masks are in the pipeline because **area is the product** — it drives
the m² → kW → $ chain. Measured on 60 validation arrays:

| prompt box | median IoU | median area/GT | roof-grab > 2× GT |
|---|---|---|---|
| box only, no SAM | 0.509 | 1.97 | 43.3% |
| **shrink 15% (production)** | **0.844** | **1.01** | **0.0%** |
| exact | 0.824 | 1.08 | 0.0% |
| dilate 25% | 0.758 | 1.28 | 1.7% |
| dilate 50% | 0.570 | 1.73 | 38.3% |

The whole-roof failure mode is a **prompt-box problem, not a mask problem**. Two attempts to beat
the crude 15% shrink were measured and both lost: background negative-point prompts help when the
box is bad and hurt when it is good (0.844 → 0.820, and they introduce roof-grab where there was
none), and multimask candidates with area-fit selection were worse everywhere (0.824 → 0.673 on
exact boxes; roof-grab 1.7% → 71.7% at dilate25). Both are good short paragraphs: they are the
kind of thing everyone tries and nobody reports.

⚠ **One honest caveat to include.** We did not measure inter-annotator agreement, because
Roboflow dedupes and one tile cannot go to two labellers. If two humans agree to only ~0.85 IoU
on a 26-pixel array, then 0.844 is the label-noise floor and further mask work is unmeasurable.
Say that we do not know which side of that line we are on.

---

## 3. Act 1, part two — the recall number, re-measured today

This is the biggest single improvement in the paper and it replaces its most damaging claim.

The permit registry is independent ground truth the detector never trained on. I re-ran the
identical audit against the new stack, with the **same denominator** as the 2026-06 run: 434
geocoded permit points inside the imaged footprint, 212 of them pre-2022.

| metric | old stack (60 cm NAIP 2023, YOLOv11, 334 arrays) | **new stack (21 cm 2025, RF-DETR + SAM2, 3,362 arrays)** |
|---|---|---|
| APN recall, all in-footprint permits | 5.3% (23/434) | **55.3%** (240/434), Wilson CI 50.6–59.9% |
| APN recall, pre-2022 installs | 10.4% (22/212) | **73.6%** (156/212) |
| point method @ 15 m, all | 3.2% | 29.0%, CI 25.0–33.5% |
| point method @ 15 m, pre-2022 | 3.3% | 33.5%, CI 27.5–40.1% |

Reproduce:

```bash
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/permit_recall_audit.py \
    --partner-id santa-cruz-w2-21cm \
    --tile-index outputs/aoi/santa-cruz-w2-21cm/tiles/tile_index.json \
    --parcels data/external/santa_cruz_parcels/aoi_santa-cruz-outreach-v1.geojson \
    --out-dir outputs/economics
# report: outputs/economics/permit_recall_audit_w2_21cm.md (saved 2026-08-23)
```

**Four caveats, all of which belong in the paper next to the number.**

1. **Quote the APN method, not the point method.** The Census batch geocoder interpolates each
   address onto the street segment, so the points sit a median 22 m from the nearest detected
   array and zero of 126 matches are point-in-polygon. The point number is a geocode-quality
   floor, not a detection floor.
2. **Some of the gain could be spurious detections.** APN recall counts a permitted parcel as
   found if any detected array's interior point falls inside it, and we now emit 10x more
   polygons. Gate precision of 0.850 bounds how much of the gain that could explain, and it
   cannot explain most of it, but state the mechanism rather than let a reviewer find it.
3. ⚠ **The audit script still hard-codes `NAIP_VINTAGE_YEAR = 2022`** and its generated prose
   still says "NAIP vintage 2022", which is wrong for 2025 imagery. The pre-2022 split is
   therefore *conservative* — with a 2025 cutoff the imaged-era denominator is essentially all
   434 and recall is 55.3%. **Akshitha: make the vintage a per-run field read from the tile
   index before we publish this.**
4. **Permits are a valid recall probe and an invalid precision denominator.** 85% of the arrays
   we labelled by hand sit on parcels with no permit on file, because the registry starts in 2016
   and the residential boom began earlier. And structurally: 1,310 of 1,865 AOI sites sit in city
   APN books with **0.0% county-permit coverage**, because the City of Santa Cruz is a separate
   permitting authority. That is worth its own sentence as a reproducibility caveat — any study
   joining detections to permits needs to check jurisdiction before reporting coverage.

**The scale claim Tyler asked for lives here.** Same 249 tiles, same 14.97 km²: 334 arrays under
the old stack, 3,362 under the new one, resolving to 1,865 distinct sites after parcel-and-
proximity clustering. Be careful with the 10x: polygon counts are not directly comparable across
GSD, because 21 cm imagery fragments a roof into 1.80 polygons per site where 60 cm gave 1.30.
Sites are the comparable unit and the honest statement is that both the imagery and the detector
changed together.

---

## 4. Act 2, part one — the economics verdict

This replaces §2.3 of the draft entirely.

**The headline, always with its qualifier:** *zero of 1,865 detected Santa Cruz sites show a
positive expected net from a cleaning, on **recoverable** soiling, at any electricity rate up to
$0.70/kWh, at any system size, over 2,000 Monte Carlo draws each. Max `prob_net_positive` across
all sites = 0.0000. Not one draw in 3.7 million was positive.*

The qualifier is load-bearing. NREL defines IWSR as the insolation-weighted mean of daily soiling
ratios **assuming perfect cleaning**, so a wash-only layer is excluded from the labels by
construction. Our loss model trains on `(1 - IWSR) × 100` and therefore predicts recoverable
loss, not total output deficit. Everything in this section is silent about the other channel,
which is §6.

**The arithmetic on a generic 6 kW home**, which is the version that belongs in the paper because
a reader can check every line:

| step | value | source |
|---|---|---|
| annual production | 9,000 kWh | 1,500 kWh/kWp/yr, ⚠ PVWatts-typical, unmeasured for this AOI |
| annual recoverable soiling loss | 5.56% = 500 kWh | XGBoost quantile head, AOI median |
| share one wash recovers | **0.045** | measured, `src/risk/recovery.py` |
| output bought back | 22.5 kWh | |
| worth at NBT $0.1646/kWh | $3.71/yr | bill-reconciled tariff stack |
| worth at NEM 2.0 $0.4573/kWh | $10.29/yr | same |
| cost of the wash | $90 light-pro / $150 professional | our price points, ⚠ not market quotes |

**The one constant that was carrying the product.** `recovery_frac` was 0.90, the assumption that
one cleaning captures 90% of a year's loss. Nobody had ever measured it. Measured, it is
**0.045**, a 20x overstatement. Mechanism: at the calibrated re-soiling constant k = 15
equivalent-days an array returns to its annual-mean soiling in about two weeks, and Santa Cruz
gets 27 heavy-rain (≥10 mm) resets a year for free. One cleaning buys roughly one array-month of
clean out of twelve. It is validated two ways: the same trajectory reproduces NREL's measured
coastal-CA annual loss (model 5.06% against measured 4.70%), and it is robust to k — even at
k = 60, four times the calibrated value, in Phoenix, recovery is only 0.125.

With 0.90 restored, 44.8% to 95.4% of the AOI "should clean". That constant was the product.

**Three other corrections, all worth naming:**

- `RISK_TO_LOSS_PCT = 8.0` multiplied a *calibrated classification probability* by 8 to get a
  loss percentage. That is a category error, not a bad estimate. It is removed and replaced by
  three XGBoost quantile heads trained on `(1 - IWSR) × 100`, spatially cross-validated and
  **CQR-conformalised** (raw prediction-interval coverage 0.558 → 0.802; OOF MAE 1.75 points).
  There is deliberately no fallback path from risk score to loss percentage any more.
- The electricity rate was a flat $0.25/kWh. The bill-reconciled PG&E E-TOU-C + 3CE stack
  (validated to ±$0.22/month over 11 consecutive real bills, each component carrying its CPUC
  sheet citation) gives a production-weighted retail offset of $0.4573/kWh and an NBT export
  credit of $0.0392/kWh, a 12x spread. Only **5.5%** of a south-facing array's annual output
  lands in the 4–9pm peak, so ~94% of soiling-lost kWh are off-peak.
- Costing ran per detected polygon rather than per site, which charged a four-way-split roof
  2.3x over.

**And then the counter-correction, which is the part that makes this publishable.** Pricing every
site as NBT was itself an untested assumption. CaliforniaDGStats publishes every Rule 21
interconnection; filtered to the five AOI zip codes, residential NEM PV, n = 7,536: **90.1% of
the AOI is legacy NEM** (52.5% NEM 2.0, 37.6% NEM 1.0). Population-blended value of a lost kWh
goes **$0.1646 → $0.4284, a 2.60x understatement**, and the AOI annual recoverable loss total
rises **156%** to $671,618/yr.

**The verdict did not move. Still 0 of 1,865, still max `prob_net_positive` = 0.0000.**

What did move is the size of the gap, and it is now a number worth arguing about. Shortfall,
defined as modelled cleaning cost over annual recovered dollars:

| system size | n sites | median shortfall | best in band |
|---|---|---|---|
| 0–5 kW | 877 | 13.81x | 7.95x |
| 5–10 kW | 796 | 7.40x | 4.15x |
| 10–15 kW | 119 | 4.45x | 3.56x |
| 15–30 kW | 35 | 4.10x | 3.25x |
| 30–60 kW | 16 | 3.63x | 3.30x |
| 60–150 kW | 12 | 3.58x | 3.03x |
| 150+ kW | 10 | 3.02x | **2.76x** |

Size targeting bought a 5x improvement and then **asymptotes near 3x**, because recovered dollars
and cleaning cost both scale with panel count; the small bands only look worse because the
minimum charge dominates a twelve-panel job. And it is not a pricing artifact: re-priced across
the published California market range, $5/panel to $20/panel, **zero sites pay at every price on
the board**. For the best residential site to break even the wash would have to cost under $30
against $30.55/yr recovered.

**How to write this.** It is a finding, not a defect, and it should be stated in the abstract, not
buried in a discussion section. It also agrees with Mejía and Kleissl's conclusion for the average
California site, reached independently and by a different route, which is worth saying — but do
not let that soften it into "cleaning rarely pays". Our result is stronger and narrower: on this
AOI, on recoverable soiling, at measured tariffs and measured recovery, it never pays.

---

## 5. Act 2, part two — why the model cannot rank individual roofs

This is the second negative result and it is the one that reframes what the paper is claiming.
It belongs in §2.2 as a limitation and in the discussion as a finding.

**The claim, stated precisely: the risk model is valid for *level* and not for *ranking homes
against each other*.**

Decomposing the per-home dollar figure across the 1,710 residential-scale sites (2–15 kW):

| driver | p10 → p90 | ratio | share of per-home dollar variance |
|---|---|---|---|
| **system size (kW), from detection** | 2.89 → 9.26 | 3.20x | **86.7%** |
| **roof orientation (POA), from lidar** | 0.77 → 1.00 | 1.30x | **11.1%** |
| soiling loss %, the XGBoost head | 5.07 → 5.83 | 1.15x | **2.2%** |
| tariff vintage (NEM 2.0 vs NBT) | — | 2.78x | a public-records join, not modelling |

97.8% of the spread comes from two things the *detector* measures. The soiling model contributes
2.2%. Doubling its spread would add less differentiation than joining one permit file.

**The mechanism, which is the part that makes this a result rather than a complaint.** The NREL
labels are station-level: 891 annual rows, 146 stations, 15 states. Across our 20 km coastal AOI
the feature standard deviations are **0.3% to 23%** of the training standard deviations, and
**58.1% of model importance sits on features that are effectively constant across the AOI**. A
model trained to separate 15 states has almost nothing left when shown 1,865 homes in one town.
No feature learned from station-level labels can fix per-roof ranking, because the training set
contains zero examples of per-roof variation.

**And we ran the controlled experiment rather than asserting it.** `worldcover_*`, `tilt_deg` and
`month_of_year` were missing from the AOI inference matrix and silently median-filled — 21.8% of
model importance being fabricated (re-measured 2026-08-12 at **58.1%** once all-NaN and
low-variance features were counted too). The obvious fix is to supply them properly. We did, for
WorldCover, resolving all 3,349 points:

```
BEFORE (median-filled)   p10 5.08   p50 5.55   p90 5.86   span 0.78 pts
AFTER  (supplied real)   p10 5.07   p50 5.53   p90 5.84   span 0.77 pts
```

**And the sign came out backwards.** Arrays on tree pixels scored *lower* (5.36%) than built-up
(5.55%). The reason is the whole point: the model learned `worldcover_tree` from NREL stations,
where a tree pixel means "forested rural region, wetter air, less dust", not "a tree overhanging
this roof". Same feature name, different referent. That single paragraph is, I think, the most
useful thing in the paper for anyone else building a transfer model from station data.

**The same failure predicted, and then confirmed, for tilt.** We measured per-array tilt and
azimuth from lidar (§7) and deliberately did **not** add it to the risk model. Three measured
reasons: the training interquartile range [7.49°, 12.00°] contains only 8.9% of our roofs and our
median roof sits at the 90.5th percentile of training; gradient-boosted trees do not extrapolate,
so the deepest tilt split is at 26.0° and predictions at 30°, 35° and 40° are identical, with
partial-dependence spread of 0.007 across the range where 73% of our roofs live; and the training
feature is effectively a station identifier, with 42.4% of rows at exactly 7.49°, only 24 unique
values, 99% of stations carrying exactly one value, and a 0.372 correlation with longitude. The
learned effect accordingly has the **wrong sign**, with steeper roofs predicting more soiling
where physics says the opposite.

Where tilt would legitimately enter is the physics, not the model: `physics_score.py` hard-codes
a 10 mm heavy-rain threshold for every array, and a 30° roof self-cleans on less rain than a 5°
one. Flag it as future work; we have not done it.

**One more honesty item that belongs here.** Our AOI median prediction is **5.53%**, and every
nearby measurement is lower: the nearest NREL station (38 km) reads 4.00%, genuinely coastal
California stations read a median of 2.80%. We predict above every measurement, by 1.5 to 2.7
points, which is a 38% to 96% overstatement on the dollar chain. It does not change the verdict —
the shortfall is 9x to 40x and a two-point correction is nowhere near enough — but publishing an
inflated loss figure cuts directly against the credibility of a paper whose main result is
arguing itself out of a revenue conclusion. Two candidate causes are documented and untested:
training-set composition (774 of 891 rows are California, dominated by Los Angeles and San
Bernardino, which is desert), and the unused `measurement_type` column, where the 10 direct
soiling-station rows read *lower* than the 881 PV-System rows by a median factor of 0.56 within
the same county.

---

## 6. Act 3 — the channel we cannot see, and the experiments that would settle it

This replaces the "persistent soiling compounds" story in §2.3 and §3.2, which as written is not
supported by our own measurement.

**What we tested.** PVDAQ system 2107 ("Farm Solar Array", Arbuckle CA): 893 kW, 8.08 years,
revenue-grade AC meter beside a class-A POA pyranometer, free and anonymous from the OEDI open
data lake. A site that should soil *harder* than our AOI.

| quantity | result |
|---|---|
| degradation, which no wash touches | −0.162 %/yr, 95% CI [−0.520, +0.175] (RdTools YoY) |
| standing layer that resets do not clear | **0.0 points**; 86% of 44 intervals start at ≥0.99 |
| growth of that layer with age | **none detectable**; CODS annual max soiling ratio pinned at exactly 1.0000 in 8 of 8 years |
| honest bound | **[0, 0.2] points/yr**, upper end being total degradation — a bound, not an estimate |

> **Corrected 2026-08-27:** the degradation figure above was produced with the module
> temperature coefficient at -0.0035, which PVDAQ's metadata does not support for system
> 2107 (it publishes no module record, so the labelled default -0.0045 applies). Re-measured:
> **-0.076 %/yr, 95% CI [-0.408, +0.358]**. The conclusion is unchanged in both direction and
> reading, and **the CODS 1.0000-in-8-of-8 result that the persistent-layer claim rests on does
> not move at all**. See `docs/GAMMA_RESOLUTION_20260827.md` Answer 4.

That kills the *uniform* persistent-layer hypothesis. It does **not** constrain the localised
coastal moss channel: 2107 is a ground mount beside farmland in a Csa climate with no tree canopy,
and a whole-array ratio averages away a few bad modules on an 893 kW system.

**What survives, and why it could matter enormously.** Rain removes dust. Rain does not remove
moss, lichen, algae, leaf litter, sap or built-up droppings, and shade plus moisture actively
encourage them. That channel is outside the IWSR labels by construction, so nothing in §4 or §5
speaks to it. And the loss is wildly nonlinear: a shaded cell current-limits its series string
until a bypass diode cuts the substring out, so a few square centimetres costs far more than its
area fraction, and then **saturates at the substring** rather than compounding. Modelled at cell
granularity with pvlib `bishop88` on a 60-cell 295 W module, 20-module array:

| occlusion | loss |
|---|---|
| one dead substring | 1.75% |
| four modules, one dead substring each | **7.00%** |
| eight modules | 14.00% |
| one bird dropping over 60% of one cell | 0.93% |
| four modules, edge band, portrait, full-cell string wiring | **21.00%** |

Against breakeven bars of 2.2% (NEM 2.0, $90 wash) to 10.1% (NBT, $150 wash), **a wash pays here
only if the roof carries continuous edge occlusion across several modules.** That is a
falsifiable, specific prediction about what a qualifying roof looks like, and it is exactly the
thing aerial imagery can see and a dust model cannot.

**The measurement problem, stated as the honest blocker.** The deciding variable is band
thickness, and the difference between "does not matter" and "pays" is about **30 mm**. Our best
imagery is 6.3 cm native. We can resolve panel-level darkening, canopy overhang and debris. We
cannot resolve the one number that decides it.

**So the proposal is ground truth, not more computing**, and it is ordered by confidence gained
per dollar, with stop rules written before the data:

1. **Ground-photo survey of edge-band thickness.** $0, one afternoon. Photograph the mossiest
   arrays in town from public land and measure the band against the module frame (35–40 mm) or
   cell pitch (156 mm) for scale. It is a max statistic, not a sample: the question is whether
   the occluded fraction reaches 0.2–0.3 anywhere in this town. **Written stop rule: if the
   thickest continuous band findable after a full afternoon is below f = 0.1 (~16 mm), the thesis
   is dead on physics and the honest output is a short public writeup saying so.**
2. **Split-array wash on one volunteer roof with microinverters or optimisers.** ~$150, ~1.5%
   minimum detectable effect in two weeks, because weather is common-mode and cancels between the
   halves.
3. **Per-array soiling labels from public production data.** This is the real gap the grant
   proposes to close: run RdTools `soiling_srr` over PVDAQ's residential feed to produce the
   first per-array soiling label set derived from public production data, plus opt-in
   residential telemetry, and then test whether array-level features carry any signal at all.
   The hypothesis is falsifiable and the extraction now runs: 12 of 12 residential systems in
   the target climate class returned non-degenerate fits on 2026-08-23
   (`scripts/analyze/pvdaq_daily_srr_probe.py`).

   **Do not write "~44 GB" here.** That figure describes the 145 multi-channel systems
   (40 GB measured), which are a different and much smaller population than the 1,381
   residential systems the proposal is about. The residential corpus is **1,381 systems /
   10,656 system-years / 0.16 GB**, published as daily aggregates. `dataset_size_mb` and
   `number_records` in the PVDAQ systems table both describe NREL's internal store, not the
   published data, and overstate it by ~100x. A reviewer can pull the same CSV we did.

**And a power result that reorders every experiment, which is worth reporting on its own.** A
conventional before/after wash test on a single array **saturates at ~5% of output minimum
detectable effect and does not improve with more days**, because weather arrives in multi-day
blocks and autocorrelation rises at almost exactly the rate extra samples would have helped. That
is a *floor*, measured on a site with an on-site pyranometer and 893 kW of spatial averaging; a
residential roof normalised against satellite irradiance is 1.5x to 2x worse. Consequences: the
obvious study ("wash 20 random homes and measure the average") is both underpowered and aimed at
the wrong estimand, and the experiment only becomes feasible when aimed at roofs where a large
effect is predicted. Selecting on visible soiling is not a shortcut, it is what makes the study
possible.

---

## 7. New material for the methods section — roof geometry from lidar

Full detail is in [`HANDOFF_roof_geometry_for_paper.md`](HANDOFF_roof_geometry_for_paper.md),
which was written for exactly this purpose. The short version for the paper, in the order I would
lead with:

**What it is.** A plane fitted to USGS 3DEP lidar returns inside each detected array polygon,
recovering per-array tilt and downslope azimuth for **3,068 of 3,362 arrays (91.3%)**. Free
public point cloud, plain HTTP against the EPT octree, no PDAL and no account. Worth stating that
the *headline* 3DEP product is a bare-earth DEM and is useless for this by construction, because
roofs are removed from it.

**The credibility anchor: two independent methods, same median.** CaliforniaDGStats publishes
installer-reported tilt from PG&E interconnection paperwork for the same county. Different
instrument, different decade, different failure modes, nothing shared with our method.

| source | n | p50 tilt |
|---|---|---|
| PG&E interconnection paperwork | 8,547 | **19.0°** |
| our 3DEP plane fits (`fit_ok` subset) | 3,068 | **19.0°** |

⚠ **Blocker before this can lead anything.** The DGStats side is **not cached in this repo** —
there is no file behind the 8,547 or the 19.0°, and no script regenerates it. Every other number
in this brief reproduces from a committed artifact; this one does not. **Akshitha, this is task
one:** pull the Interconnected Project Sites data set, cache the county extract under `data/`,
write a script, and document the filter (county, residential, PV, date range, and how nulls and
0°-tilt "not reported" rows were handled, because a 0° convention would move a median). Until
that lands, quote it as an unverified cross-check, never as validation.

**The methodological trap, which is a genuine contribution.** EPT X/Y are EPSG:3857 metres while
Z is true metres. At Santa Cruz's latitude the mercator scale factor is 1/cos(36.97°) = 1.2515,
so a plane fitted on raw coordinates reports tilt **18.9% too shallow, silently, on every roof,
producing an entirely plausible-looking distribution**. Anyone reproducing this on web-mercator
lidar will hit it. We de-inflate X/Y by cos(lat) and a unit test pins the magnitude so the
correction cannot be removed unnoticed.

**The natural experiment.** The lidar flight is 2020, so an array installed before it was itself
scanned and one installed after it was not. Permit dates split the population on a variable with
nothing to do with lidar, and the standoff step appears exactly where physics says: pre-flight
median +0.145 m (65% above 10 cm), post-flight +0.013 m (17%), difference +0.132 m, bootstrap 95%
CI [+0.084, +0.208], Mann-Whitney p < 0.0001. So the fit reports true racking angle where the
array predates the flight and roof pitch where it does not. Caveat honestly: n = 37 and 54, and
only 15.9% of arrays yield a usable standoff at all.

**Other validation worth a table:** synthetic planes recovered to <0.2° tilt and <1° azimuth;
median RMS residual 0.040 m over ~270 points per array; of 15 array pairs sharing a roof, 80% are
either co-planar (median agreement 0.5°) or exact gable mirrors, against ~28% expected by chance;
a 500-array probe and the full 3,362-array run agree to three decimal places on all quantiles.

**And the result that hardens the negative finding.** Measured fleet-mean POA/GHI is 1.029,
confirming to within 2.9% an assumption `economics.py` had carried as explicitly unmeasured. But
the *typical* roof reads 0.918 against a south-20° reference, and since south-20° is near-optimal
in this latitude band, real orientation can almost only revise a site downward. Wiring it in moved
the AOI total by +3.0% and the conclusion by nothing: 0 of 1,865 before, 0 of 1,865 after.

**Failed fits are reported as null, never median-filled.** 1,763 of 1,865 sites (94.5%) carry
measured orientation; the rest keep the flat default and are flagged. Worth one sentence, because
median-filling is exactly the failure mode §5 is about.

---

## 8. Section-by-section punch list

Owner column: **J** = Josh (prose), **A** = Akshitha (figures, artifacts, verification),
**C** = Cameron (numbers, gates, final read).

| § | Action | Owner |
|---|---|---|
| Title | Pick from the three options in §0, or propose a fourth | J + C |
| Abstract | Rewrite last. Must now carry three results, not one: detection works and is gated; the cleaning-value product does not clear and here is the constant that was carrying it; the open channel and the experiment that would settle it. Replace "nearly 350" with 3,362 arrays / 1,865 sites | J |
| 1.1 Motivation | Keep. Numbers are sourced and current | — |
| 1.2 Soiling and the call | Keep the Mejía and Kleissl framing, it is doing real work. But the tilt-below-five-degrees line now has a payoff: we can measure per-roof tilt from public lidar, and 6.4% of our arrays sit below 5°. Forward-reference it | J |
| 1.3 Pipeline scope | Rewrite. "satellite" → aerial; YOLO → RF-DETR + SAM2; add the lidar geometry stage and the economics stage as first-class pipeline components rather than a downstream calculator | J |
| 2.1 Detection | Replace wholesale with §2 above | J from C's text |
| 2.1 recall check | Replace with §3 above, all four caveats intact | J from C's text |
| 2.2 Risk scoring | Mostly survives. `PAPER_METHODS_DRAFT.md` Part A §2.2 is still accurate on label source, features, model, spatial CV, leave-one-year-out and calibration. **Add** the "valid for level, not for ranking" limitation from §5 above, and the MERRA-2 negative result if it is not already in | J |
| 2.3 Decision model | **Delete and rewrite** from §4 above. This is the largest single change in the paper | J from C's text |
| 2.4 Tools and design | Keep. Update the imagery sentence per K12, and add lidar and CaliforniaDGStats to the free-inputs list | J |
| **2.5 (new) Roof geometry** | New subsection from §7 above | J from C's text |
| 3 Pilot deployment | Update counts to 3,362 / 1,865. Reframe as "what the pipeline produced on a real AOI", not as a product launch | J |
| 3.1 Dashboard | Cut K5 entirely. The dashboard's honest claim is a per-home diagnosis: this is your array, this is its size and orientation, this is what it loses, and no, do not pay to clean it. Note that the orientation line ("SSW-facing, 19° tilt, catches ~8% less sun than ideal") is the first individualised fact the product shows that a homeowner can verify from their own driveway | J |
| 3.2 Calculator | Rewrite the persistent-soiling mechanism per K8 and §6 | J |
| 3.3 Outreach | Per Tyler: fold into the pipeline description, do not present it as an experiment. Facts: 50 postcards rendered and mailed live through Lob on 2026-06-30, 50 of 50 accepted, $47.52, QR deep-link verified for all 50 ids, delivered 13–14 July. Say plainly that a 50-card drop cannot resolve a response rate statistically, so this was a deliverability and mechanics test. **C: supply the response count to date, including if it is zero** | J + C |
| 4.1 What it represents | Rewrite per K9. The claim is not "every stage works", it is "every stage runs on public data, and the answer it returns is no" | J |
| 4.2 Limitations | Restructure into: (a) label transfer from stations to rooftops, unvalidated and central; (b) ranking, per §5; (c) the model reads high against every nearby measurement; (d) recall, now 74% with the four caveats; (e) imagery staleness as separate from resolution; (f) unsourced constants, listed in §10 below; (g) inter-annotator agreement never measured | J |
| **4.3 (new)** | Move the resolution discussion here as a *finding* rather than an apology: at 60 cm a 20 m² array is ~55 pixels, the same footprint as a parked car, which is why the confusion was structural rather than a labelling lapse. At 21 cm it largely goes away, and the county serves 6.3 cm we have not yet used | J |
| 5 Future path | Rewrite from §6 above, as a ranked and costed plan with stop rules | J from C's text |
| **6 (new) Results** | The draft has no results section; §4.1 carries results in prose. Add one with the tables from §2, §3, §5, §7 | J + A |
| **Reproducibility statement** | Every number traces to a committed artifact and the commands are in §11 below | A |
| References | The draft has none. Needs a full list | J |

---

## 9. Figures — Tyler's specific ask

He asked for figures showing the whole pipeline and how the pieces fit. Five, in priority order.
**Akshitha owns generating these from repo artifacts; Josh owns the captions.**

1. **Pipeline block diagram.** Imagery → tiling with CRS preservation → RF-DETR → SAM2 masks →
   georeferenced polygons → site clustering → lidar geometry → weather/AQ features → risk model →
   economics → dashboard and postcard. Annotate each edge with what it carries and each box with
   its licence and cost. The point of the figure is that every input is free and public. The ASCII
   version in `README.md` is the content; it needs to become a real figure.
2. **Detection example strip.** One tile at 60 cm beside the same tile at 21 cm, with ground
   truth, detections and SAM2 masks overlaid. This makes the resolution argument in one look.
   Source: `scripts/labeling/bucket_overlays.py` and the eval overlays.
3. **The shortfall chart.** Cleaning cost over annual recovered dollars against system size, all
   1,865 sites, with the breakeven line at 1.0 and the best site annotated at 2.76x. This is the
   figure that carries the negative result, and it should appear early.
4. **Variance decomposition.** The 86.7 / 11.1 / 2.2 split, as a simple bar. Pair it with the
   before/after WorldCover spans so the controlled experiment is visible.
5. **Tilt distributions overlaid**, ours against the DGStats installer-reported distribution.
   Blocked on the caching task in §7.

Charts should follow one visual system, not five. If you want them consistent, generate them all
from one small plotting module rather than ad hoc notebooks.

---

## 10. Unsourced constants — disclose, do not hide

The paper should carry a short subsection listing these. It costs nothing and it is the
difference between a reviewer trusting the rest of the numbers and not.

- **`MIN_PRO_SERVICE` = $150 and the per-panel schedule** ($8.00/panel at 3 kW declining to $5.00
  at 200 kW, with $150/$90 floors). Our price points, not market quotes. This constant decides
  most residential cases on its own. Published California market pricing runs $5–12/panel with
  $120–200 typical for a 10–20 panel system, so our schedule sits at or below the bottom of the
  range and currently *flatters* the product. C: three real quotes is a phone call and it is still
  worth making, though §4 shows the answer it can produce is bounded.
- **1,500 kWh/kWp/yr** production anchor. PVWatts-typical, never measured for this AOI.
- **`PACKING_FACTOR` 0.90** and **NBT σ = 0.30** (midday self-consumption share).
- **The ACC export table is SDG&E's**, standing in for PG&E's. Same CPUC methodology, level
  unverified.
- **`sl_sat` was fitted against the wrong side of the trajectory** — the objective scored 31
  December, mid wet season, against an annual-mean measured IWSR. It does not affect the
  annual-mean-to-annual-mean validation, but a refit would not be expected to return 0.08.
- ⚠ **The NEM cliff dates** (`NEM1_CLOSE`, `NBT_START`) are encoded from memory and flagged
  in-code as needing verification against the CPUC decision before publication.
- ⚠ **The CaliforniaDGStats tilt cross-check** (§7).

---

## 11. Provenance — where each number comes from

Supersedes Part C of `PAPER_METHODS_DRAFT.md`. Anything not listed here that appears in the paper
needs a source before submission.

| claim | value | source |
|---|---|---|
| Detection gate, test | F1 0.8260, P 0.850, R 0.803, CI [0.798, 0.853], n_gt 585 | `outputs/eval/rfdetr_w2/gate.json`; re-run `scripts/detect/eval_tile_f1.py` |
| W1 anchor, same path | F1 0.8015, CI [0.773, 0.830] | `outputs/eval/rfdetr_w1_anchor/gate.json` |
| Paired W2 − W1 | +0.0246 F1, CI [+0.0051, +0.0441], P = 0.993 | `outputs/eval/rfdetr_w2/paired_vs_w1.txt` |
| SAM2 prompt-box A/B | shrink15 IoU 0.844, area/GT 1.01, roof-grab 0.0%, n=60 | `outputs/eval/sam_containment_ab.json` |
| Negative-point A/B | shrink15 0.844 → 0.820 | `outputs/eval/sam_negpoints_ab.json` |
| Dataset | 976 chips, 3,894 polygons; tile GT val 340 / test 585 | `data/yolo/scc21` |
| Imaged footprint | 249 tiles, 14.97 km², GSD 0.204 m true ground, vintage scc_2025 | `outputs/aoi/santa-cruz-w2-21cm/tiles/tile_index.json` |
| Arrays and sites | 3,362 arrays → 1,865 sites (334 under the old stack, same footprint) | `outputs/aoi/santa-cruz-w2-21cm/arrays.geojson`; `outputs/aoi/santa-cruz-outreach-v1/arrays.geojson` |
| Permit recall, APN | 55.3% (240/434) all-years; 73.6% (156/212) pre-2022 | `outputs/economics/permit_recall_audit_w2_21cm.md`, 2026-08-23 |
| Permit recall, old stack | 5.3% / 10.4% | `outputs/economics/permit_recall_audit.md` |
| Labelled arrays with no permit | 85% (889/1048) | `outputs/economics/labeled_vs_permit.md` |
| City-book permit coverage | 1,310 sites at 0.0%; county books 62.5% | `scripts/analyze/join_permit_vintage.py` |
| Spatial-CV AUC | 0.728 mean as stored; **re-measures at 0.712** under current `model.yaml` (`abl_full40`) — quote 0.712 | `runs/soiling/run_optionb/metrics.json`, `runs/soiling/abl_full40/metrics.json` |
| Pooled out-of-year AUC | 0.710, n=891, SE 0.017, CI [0.676, 0.742] | `runs/soiling/run_optionb/holdout_ci.json` |
| Out-of-year calibration | Brier 0.218 vs base rate 0.250, ECE 0.046, MCE 0.126 | same |
| Loss regressor | OOF MAE 1.75 pts, PI coverage 0.558 → 0.802 after CQR | `runs/soiling/run_lossreg/` |
| Variance decomposition | 86.7 / 11.1 / 2.2 across 1,710 sites | `HANDOFF_roof_geometry_for_paper.md` Result 1 |
| WorldCover controlled test | span 0.78 → 0.77 pts; tree 5.36% < built-up 5.55% | `scripts/analyze/backfill_worldcover.py` |
| AOI vs station loss | 5.53% modelled vs 4.00% at 38 km, 2.80% coastal CA p50 | `SOILING_LEVEL_INVESTIGATION.md` |
| Economics verdict | 0 of 1,865, max prob_net_positive 0.0000, 2,000 draws each | `scripts/analyze/rate_sensitivity.py` |
| `recovery_frac` | assumed 0.90, measured 0.045 | `src/risk/recovery.py`; `scripts/analyze/recovery_calendar.py` |
| Tariff mix | 90.1% legacy NEM, n=7,536; $0.1646 → $0.4284; loss total +156% | CaliforniaDGStats Rule 21, 5 AOI zips |
| Rate stack | retail $0.4573, NBT export $0.0392, 5.5% of output in peak | `src/risk/rates.py`, 11 reconciled bills |
| Shortfall by size | 13.81x → 3.02x; best site 2.76x at 196 kW | `scripts/analyze/rate_sensitivity.py` |
| Price sensitivity | 0 sites pay from $5/panel to $20/panel | same |
| Roof geometry | 3,068/3,362 fits; tilt p50 19.0°; POA p50 0.918; fleet POA/GHI 1.029 | `outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv` |
| Mercator trap | tilt understated 18.9% if uncorrected | `tests/test_roof_geometry.py` |
| Standoff experiment | +0.132 m, CI [+0.084, +0.208], p<0.0001, n=37/54 | `scripts/analyze/panel_standoff_probe.py` |
| PVDAQ 2107 | degradation −0.162 %/yr; standing layer 0.0 pts; CODS 1.0000 in 8/8 | `scripts/analyze/washable_share_probe.py` |
| Wash-test power | ~5% MDE, does not improve with days | `scripts/analyze/washtest_power.py --year 2024` |
| Substring physics | 7.00% for four modules; 21.00% portrait string; 0.93% one dropping | `scripts/analyze/substring_shade_loss.py` |
| Breakeven bars | 2.2% / 3.6% / 6.1% / 10.1% | `AOI_CLEANING_TARGETING_PLAN.md` |
| MERRA-2 negative result | 0.716 CV / 0.666 holdout, abandoned, 1.3 GB | `runs/soiling/run_merged_merra2/` |
| Leakage runs, discarded | CV AUC 1.0, SOMOSclean label + feature | `run_somosclean_v2_calibrated`, `_v4` |
| Mailing | 50/50 live via Lob, 2026-06-30, $47.52 | `MAILER_PIPELINE.md` |
| Open-Meteo free tier | ~600 homes/day | quota gate in the scoring path |

---

## 12. Sequence, so this does not deadlock

1. **C** delivers this brief plus paste-ready prose for §2.1, §2.3, §2.5 and §5 of the paper.
   Done, this document.
2. **A** starts the two blocking artifact tasks immediately, because both gate figures:
   cache the CaliforniaDGStats extract (§7) and fix the vintage constant in the recall audit (§3).
3. **J** does the structural pass first — title, section order, kill list — before touching
   sentences. Rewriting prose that is going to be deleted is the main way this eats a week.
4. **J + A** on figures 1–4 in parallel with the prose. Figure 5 waits on task 2.
5. **C** does a numbers pass over the full draft against §11 before anything leaves the team.
6. Then the voice pass, then Tyler and Craig.

**What I will not do:** rewrite Josh's prose. The sections above are content and numbers, meant to
be rewritten in his voice, not pasted verbatim.

---

## 13. Things not to overclaim

1. **The 10x detection jump is imagery and detector together**, not the detector alone, and
   polygon counts are not comparable across GSD. Sites are the comparable unit.
2. **The recall gain is measured on a permit denominator that is not a census**, and part of it
   could in principle come from spurious detections. Gate precision of 0.850 bounds that.
3. **The 11.1% orientation share mixes per-array and per-site quantities** and is an estimate, not
   a like-for-like decomposition.
4. **The tilt cross-check is unverified** until the DGStats extract is cached.
5. **The economics verdict is about recoverable soiling only.** Say the qualifier every time. The
   channel it excludes is the entire remaining opportunity and we have not measured it.
6. **We do not know the label-noise floor on masks**, so 0.844 median IoU may be the ceiling.
7. **The moss physics is modelled, not observed on our roofs.** Nothing in §6 has been seen in
   Santa Cruz yet. That is what the afternoon with a camera is for.
