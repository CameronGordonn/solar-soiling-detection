# Grant narrative — draft

**2026-08-23. DRAFT for Cameron + Craig to shape; not a submission.** Every number carries
its source. Where a number is not yet validated it says so, because a reviewer who finds one
unsourced figure discounts all of them.

**Section 4's feasibility work is complete** as of 2026-08-23 and its findings are folded in.
The proposal survived it: the extraction runs, 12 of 12. Two things changed and both are now
reflected in §4 rather than discovered by a reviewer. The published residential feed is
*daily* aggregates, not the sub-daily series the scoping doc first assumed, and the system
coordinates are not roof-accurate, so the imagery-join phase was dropped. Provenance in
`docs/PVDAQ_LABEL_PIPELINE_SPEC.md` §3-§4.

> **Revised 2026-08-27, by Claude, for Cameron to accept or reject.** Three things moved
> since the 2026-08-23 pass and all three moved *against* us, so they are folded in here
> rather than left for a reviewer. (1) §2's cleaning verdict now covers the moss channel that
> was built specifically to rescue it, and still returns zero, per roof. (2) §4's
> signal-to-noise claim of 2.8x was measured across different metros and so contained climate
> variation the design removes; the within-cell figure is lower and is now stated with the
> precision measurement behind it. (3) A module temperature coefficient we had hardcoded, in
> two disagreeing versions, turned out to move a label by more than the noise floor; it is now
> resolved per system and folded into the budget. §4 also gains the NREL agreement check,
> which is the one thing that moved in our favour. **Nothing in your framing was rewritten:
> the changes are additive or replace a specific stale number.** Change log at the end of
> this session's notes.

---

## 0. The two-box explainer (read this first, it prevents the main misunderstanding)

Everything below turns on a distinction that is easy to miss and expensive to miss.

|  | **Box A — climatology** | **Box B — observation** |
|---|---|---|
| question | "what does a typical array in this region lose per year?" | "is *this* array dirty right now?" |
| input | weather, air quality, location | pixels, production telemetry |
| labels | regional station measurements | per-array measurements |
| we have | **this, and it works** | **this is what we propose to build** |

The model we operate today is Box A. It answers its own question well: spatial-CV AUC 0.712,
pooled out-of-year AUC 0.710, calibration retained out-of-year. It cannot answer Box B's
question, and **no amount of additional features will make it**, for a structural reason
given in §3.

Confusing the two is the single most common failure mode in this space: a Box A model gets
sold as a Box B product, homeowners get told to clean roofs the model never actually looked
at. We did that ourselves and caught it (§2). The proposal is to build Box B honestly.

---

## 1. What exists and is measured

**A gated rooftop-PV detector on public imagery.** RF-DETR @728 plus SAM2, Apache-2.0.

| metric | value |
|---|---|
| tile-level box F1 @ IoU 0.50 | **0.826** |
| 95% CI (bootstrap over tiles) | [0.798, 0.853] |
| precision / recall | 0.850 / 0.803 |
| ground-truth objects | 585 |
| operating point | conf tuned on val, **frozen before test was touched** |

Gate conditions were written down in advance (F1 >= 0.75, CI lower bound >= 0.70, recall
>= 0.70) and all three clear. Area is unbiased: median mask area / ground truth = 1.01, with
0% of masks grabbing more than twice the true footprint. Reproducible with one command
(`scripts/detect/eval_tile_f1.py`), on free public imagery.

**Measured roof geometry from public lidar.** Per-array tilt and azimuth fitted to USGS 3DEP
point clouds for 3,068 of 3,362 arrays (91.3%), median RMS residual 4 cm. Externally
validated against an independent source with nothing in common: installer-reported tilt in
PG&E interconnection paperwork gives median 19.0 degrees across 8,547 county installs; our
lidar plane fits give **19.0 degrees**.

**A published methodological trap worth the paper on its own.** Lidar tile X/Y are web
mercator metres while Z is true metres, so a plane fitted on raw coordinates reports tilt
18.9% too shallow, silently, on every roof, producing an entirely plausible distribution.
Anyone reproducing this class of work hits it. Ours is pinned by a regression test.

---

## 2. What we measured and disproved

This is the part most applicants leave out, and for a nonprofit it is an asset rather than an
embarrassment.

**We audited our own product's dollar chain end to end and it does not clear.** Across 1,865
detected sites in coastal Santa Cruz, **zero** show a positive expected net from a cleaning,
at any electricity rate up to $0.70/kWh, over 2,000 Monte Carlo draws each.

That first pass covered *dust*, the channel rain resets. The obvious objection was that it
tested the wrong mechanism: moss and lichen are alive, rain feeds them rather than removing
them, and a wash therefore buys years instead of a fortnight. We built that channel and ran
it. **It also returns zero**, now measured per roof rather than per region, on all 2,494
arrays carrying fitted lidar geometry. The best case in the AOI still loses about $30. Three
causes, in order of size:

1. **A safety code decided it.** NEC 690.12(B)(2), effective 2019-01-01, requires
   module-level rapid shutdown, which in California practice means microinverters or
   optimisers on essentially every system built since. That hardware exists to stop one
   shaded module dragging down its string, so it defuses most of the substring nonlinearity
   that made an edge band expensive: the same moss line costs about 21% on string wiring
   and about 8% with module-level electronics. This is a legal requirement, not a taste.
2. **The $150 minimum service charge binds at every residential size.** The median AOI array
   is 3.5 kW. The fixed call-out fee, not the dirt, decides most residential cases.
3. **The favourable roofs are the small ones.** Only 36 of 3,362 detected arrays match a
   permit showing both pre-2017 hardware and the legacy NEM 2.0 tariff, and those 36 are
   *smaller* than average (top-decile 5.5 kW against 7.9 kW AOI-wide). None clear even when
   string wiring is assumed for all of them.

**Scope, stated so it is not overclaimed.** All of this is about *recoverable* soiling, the
part a wash can undo. NREL's insolation-weighted soiling ratio is defined assuming perfect
cleaning, so a permanent wash-resistant layer is excluded from the labels, from our
saturation constant and from the recovery fraction by construction, not double-counted. We
have not measured the permanent channel, and we do not claim it is zero.

The cause was a single constant nobody had ever measured. `recovery_frac`, the share of a
year's soiling loss that one cleaning buys back, was assumed at **0.90**. Measured, it is
**0.045** — a 20x overstatement. At the calibrated re-soiling rate an array returns to its
annual mean in about two weeks, and Santa Cruz gets 27 heavy-rain resets a year for free. One
cleaning buys roughly one array-month of clean out of twelve. With 0.90 restored, 45 to 95%
of the AOI "should clean." That constant was the product.

**The result then survived its own largest counter-correction.** We measured tariff vintage
from public interconnection records (n = 7,536) and found the AOI is 90.1% legacy net
metering, not the successor tariff the chain had assumed. That raised the value of a lost kWh
2.6x and the AOI's annual loss total by 156%. The verdict did not move: still zero of 1,865.

A negative result that survives its own strongest attempted rescue is worth considerably more
than one that was never stress-tested. **We also found the same error live on our own public
calculator, telling visitors to clean, and fixed it.** We are asking to be funded partly on
the strength of having audited ourselves and published the result.

---

## 3. The structural finding that motivates the proposal

Our soiling model trains on 891 station-years from 146 monitoring stations across 15 states (1,002 rows total once the 111 summary-only censored rows are included).
Each row is a *station-year*: a regional climatological measurement. Every rooftop in a
station's catchment therefore shares one label, by construction.

**This makes per-roof prediction unidentifiable, not merely inaccurate.** The training set
contains zero examples of two arrays under the same weather with different measured losses,
so a per-array feature has no label variation to fit against.

We tested this rather than asserting it. Land-cover features were wired in properly, replacing
values that had been silently median-filled:

```
before (median-filled)   p10 5.08   p50 5.55   p90 5.86    spread 0.78 points
after  (properly supplied) p10 5.07   p50 5.53   p90 5.84    spread 0.77 points
```

Spread moved 0.01 points, and the sign came out backwards: arrays on tree pixels scored
*lower*, because the model learned "tree" from rural monitoring stations where it means
"forested region, wetter air, less dust", not "a tree shading this roof." Same feature name,
different referent.

**And the decomposition that reframes the whole question.** Across 1,710 residential sites,
the per-home dollar spread breaks down as:

| driver | share of variance | source |
|---|---|---|
| array size | **86.7%** | detection, already live |
| roof orientation | **11.1%** | lidar, already live |
| the soiling model | **2.2%** | the thing everyone tries to improve |

97.8% of the differentiation comes from two things detection already measures. Doubling the
soiling model's spread would add less than joining one public permit file.

---

## 4. What we propose  [feasibility complete 2026-08-23]

**Build the first per-array soiling label set from public data, and test whether array-level
features carry signal at all.**

Public production time series exist for **1,381 residential-scale PV systems** with
**10,656 system-years** of data, including **278 systems in the same climate class as our
study area**. Established methods (stochastic rate-and-recovery, and a published
soiling/degradation separation algorithm) extract per-system soiling loss from those series.

**The extraction is working.** Run against 12 residential systems in the target climate
class, spanning 4.7 to 9.5 years each, it returned a non-degenerate soiling fit for **12 of
12**: median annual loss 1.95 percentage points, between-system SD 2.10 points, median
per-label 95% CI width 0.71 points.

**And the labels agree with NREL's, which is the check a reviewer makes first.** Taking the
24 PVDAQ systems that sit within 5 km of an NREL soiling station and comparing like for like:
our median 2.54 points against their 2.70, paired difference **+0.16 points, 95% CI [-0.62,
+0.91]**, Wilcoxon p = 0.68, rank correlation +0.63. No detectable bias in either direction.
That is measured on the corrected per-system temperature coefficients; on the earlier
fleet-wide constant it read +0.19, CI [-0.66, +0.99], so the check survives the correction.

There is also a strong precedent argument, and it cuts both ways, so we state both halves.
**881 of the 891 station-year labels in NREL's published soiling map were themselves derived
from inverter AC power** by this same class of method; only 10 come from dedicated soiling
instruments. We are not inventing a label type, we are applying the established one at a
finer unit. The same fact means the paragraph above is a **reproduction check on our
implementation, not independent validation**, and we would say so in the paper before a
reviewer says it for us.

**How precise is one label, and is that precise enough?** This is the question the proposal
lives or dies on, so it was measured directly rather than inferred from the fitter's own error
bars. Refit the same roof over the same years, changing only the method: a different
irradiance source (PVGIS-NSRDB satellite instead of ERA5 reanalysis), and a different module
temperature coefficient. Nothing real differs between those estimates, so everything that
moves is our own error.

| what varies | SD of the paired difference |
|---|---|
| irradiance source alone | 0.78 points |
| temperature coefficient alone | 0.46 to 0.63 points |
| **both, pooled** | **0.83 points** |

Against a within-shared-weather-cell between-system spread of 1.46 points, that is a
**signal-to-noise ratio of about 1.8x**. Real roof-to-roof differences exceed our measurement
error, but not by a comfortable margin. The design is powered enough to fit a feature model
across roughly a thousand systems; it is **not** precise enough to rank two neighbouring
roofs against each other, and we would not claim otherwise.

**We corrected this figure downward ourselves, twice.** An earlier read of 12 systems gave
2.8x, but those systems spanned different metropolitan areas, so an unknown share of that
spread was climate rather than roof, which the cluster fixed effects would absorb anyway.
Restricting to within-cell contrasts brought it to 1.95x. Then the temperature coefficient
turned out to have been a single hardcoded, unvalidated constant, present in two versions in
our own code that differed by enough to move a label 1.045 points. Resolving it per system
from published module metadata against the CEC module database (43% matched to a named
module, 35% to a manufacturer, 20% falling back to a labelled default) folded that term into
the budget for the first time and took the ratio to 1.8x. Each correction made the number
worse and each is in the record.

**Why this is different from more regions.** Binning the residential systems geographically,
**66 clusters hold 5 or more of them each**, containing 988 systems and 7,484 system-years,
and 170 coordinates carry more than one system. Inside a cluster, weather is common-mode, so
any difference in measured soiling loss between two systems is attributable to array-level
factors. That contrast does not exist anywhere in the station data. It converts an
unidentifiable question into a measurable one.

Crucially, the array geometry actually varies inside those shared-weather cells: median
within-cell SD of **10.4 degrees of tilt** and 47 degrees of azimuth, with 63 of the 66 cells
above 5 degrees of tilt SD. Without that variation the fixed-effects design would be
unidentified, so it was checked before proposing the experiment.

**The hypothesis is falsifiable and we commit to publishing either outcome.** Regressing
per-system soiling loss on array-level features with cluster fixed effects either finds
signal or does not. If it does not, the per-roof premise underlying a whole category of
commercial soiling products is wrong, and that is worth publishing. We have pre-registered
the stop rule.

**Honest limitations, stated before a reviewer finds them.** These systems are geographically
sparse. 1,236 of the 1,381 expose only two channels, AC power and AC energy, with **no
on-site irradiance**, so irradiance must be modeled, which costs precision we have separately
quantified. Those channels are published as **daily aggregates**, not sub-daily series, which
removes clear-sky filtering and shows up as a low valid-interval rate in the fits; it is why
we use the `perfect_clean` branch, which is robust to it and is also NREL's own convention.
The fitter's own bootstrap interval understates our
uncertainty by roughly 1.2x, which is why the table above uses refits rather than error bars;
changing how the performance index is constructed moves a label by ~1.6 points on top of
that, so method sensitivity is reported alongside every label. System coordinates are neighborhood-accurate
rather than roof-accurate, so we do **not** attempt to join these labels to aerial imagery.
Tree canopy is a live confounder in the target climate and enters the model as a covariate
rather than being assumed away. This work moves the label unit from region to array; it does
not by itself deliver within-city residential ranking.

---

## 5. Why a nonprofit is the right vehicle

Not boilerplate. Three specific reasons this work is *better* done here.

1. **Negative results are publishable for us and unpublishable for a competitor.** A
   panel-cleaning company cannot fund, run or release the finding in §2. We can, and the
   finding is directly useful to homeowners who are currently sold cleanings on numbers like
   the 0.90 we disproved.
2. **"Donate your solar data to research" is a recruitment channel a vendor cannot use.**
   The per-roof label problem is ultimately solved by opt-in production telemetry. Households
   share data with a research nonprofit that publishes; they do not share it with a lead
   generator.
3. **Everything is built on free public data** — national aerial imagery, public lidar, public
   interconnection records, an open production data lake. Methods and code are reproducible by
   anyone, which is the output, not a side effect.

---

## 6. Outputs

- **Methods paper.** Per-array roof geometry from public lidar, the two-source tilt
  validation, the mercator trap, and the detection gate protocol.
- **Negative-results paper.** The cleaning economics chain, the 20x constant, the
  counter-correction that failed to overturn it, and the moss channel that was built
  specifically to rescue the result and did not.
- **The label set and extraction code**, released, so the per-array soiling question is
  answerable by others.
- **Public tooling** already live: a homeowner-facing dashboard reporting an individualized
  performance figure with a truthful recommendation attached, including "do not clean."

---

## 7. The ask

`[Craig — amount, term, and use of funds. The technical scope above is roughly one FTE for
two to three quarters, weighted toward the label work in §4.]`

---

## Provenance

Every figure above is reproducible from the private repository; §1-§3 numbers are recorded
with their measuring script, split, threshold and object count in
`docs/CRAIG_BRIEF_2026-08-19.md`, `docs/ECONOMICS_GROUNDING_20260809.md` and
`.claude/rules/stage1-detect.md`. §4's scoping is in
`docs/PVDAQ_LABEL_PIPELINE_SPEC.md`; its feasibility phase was **executed and its results
recorded on 2026-08-23** (`outputs/soiling/pvdaq_0a_csb12.json`, reproducible with
`scripts/analyze/pvdaq_daily_srr_probe.py`). §4's agreement with NREL is
`scripts/analyze/pvdaq_phase2_vs_nrel.py` over `outputs/soiling/pvdaq_phase2_nearstation.json`;
the precision table is `scripts/analyze/pvdaq_method_noise.py` (2x2, irradiance source x
temperature coefficient, n=20 systems) and the within-cell spread is
`scripts/analyze/pvdaq_within_cluster.py`. The per-system temperature coefficients come from
`src/risk/module_gamma.py` and are tabulated for all 1,629 residential systems in
`outputs/soiling/module_gamma_by_system.csv`.

Unsourced or assumed values are tracked as such in `CRAIG_BRIEF_2026-08-19.md` §6 and must
not be quoted as measured: the production anchor of 1,500 kWh/kWp/yr, the minimum
professional service price, the per-panel rate schedule, and a substituted utility export
table.
