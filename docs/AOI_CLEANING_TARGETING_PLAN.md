# Which Santa Cruz homes would actually benefit from a panel cleaning

**Living document.** Rewritten and sharpened each iteration; the decision log at the end is
append-only. Branch: `soiling/aoi-cleaning-targeting`.

Status: **v7, 2026-08-12.** Nothing in here has been acted on yet. No number in it ships.

---

## 1. The one-paragraph version

We can already say, with measurement behind it, that washing ordinary dust off a solar roof
in Santa Cruz does not pay: rain here does that job for free, and a $90 to $150 wash buys
back about $4 to $10 of electricity a year. That is true for all 1,865 solar homes we have
found in the area, and it is why this project has been telling people **not** to clean. But
that answer only covers dust. It says nothing about the stuff rain does not remove and in
fact encourages: moss and lichen creeping along the bottom edge of the panels, leaf litter
and sap under an overhanging tree, built-up bird droppings. Those are a different problem,
because of how panels are wired inside. We have now worked that wiring out properly, and it
says something quite specific: a thin line of moss along the bottom edge of four panels can
cost between 1% and 21% of a system's output, depending on how thick the line is, which way
up the panels are mounted, and how old they are. **The thickness is the part that decides
it, and it turns out to be the one thing our aerial photographs cannot measure** at any
resolution we have: the deciding difference is about 30 millimetres, and each pixel covers
78. So the next step is not more computing. It is an afternoon on foot with a camera,
photographing the mossiest panels in town from the pavement and measuring the line against
the panel frame. If the worst one out there is thinner than about 15 millimetres, then none
of this pays anywhere in Santa Cruz, and we will publish that and stop. If some are thicker,
then it is worth spending two days counting how common they are on the aerial imagery, and
then washing half of one volunteer's roof and leaving the other half, which is the cheapest
way anyone can settle what a wash is actually worth here. Until those things are done, our
answer for every home in Santa Cruz stays "leave it alone", and we expect it to stay that way
for most of them afterwards.

---

## 2. The one-page technical version

### The question the product cannot currently answer

The shipped pipeline emits one number for the whole AOI. `econ_summary.json` for
`santa-cruz-w2-21cm` records `n_sites_worth_cleaning: 0`, `max_prob_net_positive: 0.0`, and
its own `known_limitations[0]` says the loss model spreads only 0.78 points p10 to p90
across 1,865 sites, so it is "valid for level, NOT for ranking homes against each other."
There is therefore no mechanism in the product today that could pick a home. The global
verdict is the only verdict, and it is "no."

### Why the global verdict is right, and what it is right *about*

The arithmetic, on a 6 kW home at an assumed 1,500 kWh/kWp/yr (UNMEASURED, PVWatts-typical
for coastal CA; see the register in §7):

| step | value |
|---|---|
| annual production | 9,000 kWh |
| annual recoverable soiling loss (regression head, AOI) | 5.56% = 500 kWh |
| share one professional clean recovers (`DEFAULT_RECOVERY_PRO`) | 0.045 |
| output actually bought back | **22.5 kWh** |
| worth at NBT blended $0.1646/kWh | **$3.71/yr** |
| worth at NEM 2.0 retail offset $0.4573/kWh | **$10.29/yr** |
| cost of the wash | $90 light-pro / $150 professional |

The shortfall is 9x to 40x. This is not a close call and no plausible correction to `k`,
`sl_sat`, or the anchor closes it. Two separate audits push in opposite directions and
neither is large enough to matter here: the calibration anchor is too high (the "coastal-CA
p50 4.70%" is 66 of 66 inland Central Valley stations; genuinely coastal CA reads 2.80%,
which would make the product *worse*), and `k=15` is too small (it implies 0.51% loss on day
one after a wash, roughly 10x Mejia and Kleissl 2013, which would make it better). Fixing
either alone produces a confidently wrong number. Fixing both is not on the critical path,
because the answer stays "no" across the whole plausible range.

**The thing that is missing is not a better dust model.** NREL's IWSR is defined as the
insolation-weighted daily soiling ratio *assuming perfect cleaning between detected soiling
intervals*, so it contains only the rain-recoverable layer by construction. Anything a wash
removes and rain does not is outside the measurement, outside the labels, outside `sl_sat`,
and outside the 0.045. The pipeline carries that channel at `YOY_PERSIST_PCT = 0.0`.

That zero has one direct measurement behind it (`washable_share_probe.py`, §13 of
`ECONOMICS_GROUNDING_20260809.md`): PVDAQ 2107 shows 0.0 pts of standing layer, annual max
soiling ratio pinned at 1.0000 in 8 of 8 years, honest interval [0, 0.2] pts/yr. **That
measurement is real and it constrains the *uniform* persistent channel. It does not
constrain the localized one**, and this plan turns on the difference:

- 2107 is an 893 kW ground mount beside tilled farmland in Arbuckle, Csa climate, no tree
  canopy, plausibly washed on a schedule. It is the wrong instrument for moss.
- A whole-array soiling ratio is an *average*. Localized opaque soiling on a few modules is
  a small perturbation to an 893 kW array average and a large one to a 6 kW roof.
- Moss, lichen and algae are a coastal-Csb phenomenon driven by shade and moisture. 2107 has
  neither.

So the correct claim, unchanged from what the site already says, is **"zero of 1,865,
counting the dirt that rain takes off."** The open question is whether anything else is
there.

### The physics that decides whether it can ever pay

Uniform dust loss is roughly linear in transmission loss. Localized opaque soiling is not,
and the nonlinearity is what makes the whole question live. A shaded cell limits the current
of its series string; bypass diodes cut the affected substring out rather than let it
reverse-bias. So the loss from a few square centimetres is enormously larger than the area
fraction, **and it saturates at the substring**.

**v1 derived the size of that nonlinearity from a ratio of integers. It is now measured**, at
cell granularity, with a single-diode model carrying bypass diodes and reverse-bias
avalanche breakdown (`scripts/analyze/substring_shade_loss.py`, pvlib `bishop88`, 60-cell
295 W module, 20-module array, Ee = 800 W/m², Tcell = 45 C):

```
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/substring_shade_loss.py \
    --json outputs/analysis/substring_shade_loss.json
```

| occlusion | v1 claimed | full-cell, MLPE | full-cell, string | half-cut, MLPE |
|---|---|---|---|---|
| 1 dead substring | 1.7% | **1.75%** | 1.75% | 1.61% |
| 4 modules, 1 dead substring each | 6.7% | **7.00%** | 7.00% | 6.43% |
| 8 modules, 1 dead substring each | 13% | **14.00%** | 14.00% | 12.87% |
| 1 bird dropping over 60% of one cell | implied 1.7% | **0.93%** | 0.94% | 0.74% |

**The substring arithmetic survives**, and was slightly conservative. The dropping does not:
a dropping that covers most of one cell current-limits its substring rather than killing it,
costing 0.93%, which is below every breakeven bar below. v1's conclusion that scattered
droppings never pay is now measured rather than asserted.

Two things v1 got wrong.

**1. Mounting orientation changes the answer, and so does the module's cell architecture.**
A 60-cell module is 6 x 10 cells and its three substrings are pairs of 10-cell columns
running along the module's *long* axis. A moss band along the module's low edge therefore
hits **one** substring if the module is mounted landscape and **all three** if it is mounted
portrait. A **half-cut** module splits every cell in two and wires the halves as two
*parallel* half-strings, one at each end of the long axis, which changes the portrait case
again: the band hits only the lower half-string, the upper one keeps producing, and the
module floors near half output instead of zero. In landscape the split runs the wrong way and
half-cut buys nothing. Measured, same moss, same roof (edge band f = 0.5 across 4 modules):

| | full-cell, MLPE | full-cell, string | half-cut, MLPE | half-cut, string |
|---|---|---|---|---|
| landscape | 7.00% | 7.00% | 7.00% | 7.00% |
| portrait | 8.20% | **21.00%** | 4.57% | 15.88% |

At full occlusion (f = 1.0) the portrait gap is starker still: 20.00% MLPE full-cell against
10.00% half-cut, exactly the factor of two the parallel architecture predicts.

**Checked against an outside source at v6, because this was our own model agreeing with
itself.** PVsyst's module documentation describes the twin half-cut architecture as "2 sets
of 3 strings of half-cells connected in parallel, each pair of strings sharing the same
bypass diode", states that when the lower half is shaded "the upper sub-module continues
operating at half the total module current", and notes the benefit "is valid when modules are
positioned in portrait; for landscape orientation they behave like standard modules". All
three match what the model produced, including the landscape null result, which is the part
that would have been easiest to get wrong.

That source also exposed a real discrepancy in the implementation: v3 put a bypass diode on
every substring and paralleled the half-strings at the module terminals, where the documented
circuit puts one shared diode across each parallel *pair*. Those are different circuits, so
v6 implemented the documented one and made it the default (`--diode-per-substring` restores
the old behaviour). **Measured, it changes nothing that matters**: MLPE results are identical
to three decimal places in every scenario, and the only movement anywhere is a single-module
portrait string case, 4.79% to 5.25%. No figure in this document moved. Recorded because a
verification that finds nothing is only worth anything if it is reported the same way as one
that finds something.

The agreement with the literature is qualitative. It confirms the architecture and the
direction and rough size of each effect; it is not a numerical validation, and no full text
was read (the directly comparable studies are paywalled).

**Half-cut has been the dominant residential product since roughly 2019.** So install year
now carries *two* independent effects and they point the same way as the tariff:

| install era | tariff | cell architecture | inverter |
|---|---|---|---|
| pre-2019 | NEM 1.0/2.0, $0.4573 | full-cell, worst portrait case | often string, amplifies |
| 2019 to 2023-04 | NEM 2.0, $0.4573 | half-cut, roughly halves portrait loss | mostly MLPE |
| post-2023-04 | NBT, $0.1646 | half-cut | MLPE |

**The oldest arrays are worth the most per lost kWh and lose the most per unit of soiling.**
That is a compounding targeting signal rather than two independent weak ones. **It is also
mostly unavailable for this AOI**, which v3 assumed the opposite of and v4 measured; see
§3.5. The measurable full-cell segment is 97 sites, not thousands, and the reason the rest is
missing is systematic rather than random.

Both orientation and, at 6 cm, module count and module size are readable from imagery.
Orientation is a required field in the Option 1 rubric (§3.3); the topology comes from the
permit year, not from the picture.

**2. The string-inverter amplification is not what v1 assumed.** v1 said "direction certain,
magnitude unmeasured, plausibly 1.5x to 3x". Measured, it is **1.00x** for a fully dead
substring: the bypass diode conducts, the string current is untouched, and MLPE buys nothing.
The amplification exists only in a *partial*-occlusion window, and inside that window it
exceeds v1's upper bound (4 modules, portrait):

| band covers fraction f of the bottom cell row | MLPE | string | amplification |
|---|---|---|---|
| f = 0.1 | 0.63% | 1.85% | 2.9x |
| f = 0.2 | 2.15% | 9.10% | **4.2x** |
| f = 0.3 | 4.01% | 18.39% | **4.6x** |
| f = 0.5 | 8.20% | 21.00% | 2.6x |
| f = 1.0 | 20.00% | 21.00% | 1.05x |

So install-year-as-topology-proxy matters most for *thin* moss lines and hardly at all for
thick ones, which is the reverse of the intuition v1 was running on.

**The loss fraction is irradiance-invariant**, which is what allows any of this to become an
annual energy number without a year-long simulation. Across Ee = 200 to 1,000 W/m² the
4-module landscape case moves from 7.01% to 7.00%, the full-cell portrait case from 8.39% to
8.13%, and the half-cut portrait case from 4.59% to 4.56%. The instantaneous DC loss fraction
is the annual energy loss fraction, for as long as the moss is present.

**None of this is a recovery figure.** Every number in this section is what localized
occlusion *costs while it is there*. What a wash gets back is a different quantity: it
requires that the wash removes the growth, and that the growth stays off long enough to
matter. Neither is measured, and Option 2 is the only thing in this plan that can measure
them. A reader who leaves §2 with "21%" in their head as a recoverable figure has misread it;
the recoverable figure is the one this project has been publishing all along, and it is
$3.71 to $10.29 a year.

Against the breakeven bars, computed the same way as the table above:

| regime | wash | recovered output needed to break even |
|---|---|---|
| NEM 1.0/2.0 ($0.4573/kWh) | light-pro $90 | **2.2%** |
| NEM 1.0/2.0 | professional $150 | **3.6%** |
| NBT no-battery ($0.1646/kWh) | light-pro $90 | **6.1%** |
| NBT no-battery | professional $150 | **10.1%** |

**This is the whole plan in two tables.** A wash pays here only if the roof carries
*continuous edge occlusion across several modules*, and the tariff alone makes it **2.8x**
easier on a NEM 1.0/2.0 home ($0.4573 / $0.1646). Scattered bird droppings do not clear the
bar at any tariff, at any wash price. That is a falsifiable, specific prediction about what a
qualifying roof looks like, and it is exactly the thing aerial imagery can see and a dust
model cannot.

(v1 quoted 4.6x here. That was 10.1% / 2.2%, which compares the NBT *professional* bar to the
NEM 2.0 *light-pro* bar and so folds the wash price into a number labelled as the tariff
effect. Holding the wash fixed, the tariff ratio is 2.8x either way.)

One amplifier and one multiplier, both still short of measured:

- **Inverter topology** is measured above and is **conditional, not a blanket multiplier**:
  1.00x once a substring is fully out, up to 4.6x in the partial-occlusion window. NEC 690.12
  module-level rapid shutdown pushed CA residential toward MLPE from roughly 2017, so install
  year remains a usable proxy for topology, but it should be applied only to roofs whose
  occlusion reads as partial. What is still UNMEASURED is the *distribution of f* on real
  roofs, which is what decides whether the AOI sits in the 4.6x window at all.
- **Duration.** The 0.045 recovery fraction is small because rain resets dust within weeks.
  Moss does not grow back in weeks. A wash that clears a biological layer buys a benefit
  window measured in seasons, so the same percentage recovery is worth several times more.
  How long is unmeasured.

### What we already have that nobody has used

- **6 cm imagery for the entire AOI, on disk.** `outputs/fetch_6cm.log`: 249/249 tiles
  fetched at 0.064 m native, reprojected to 0.078 m/px EPSG:3857. At that GSD a 1.65 m
  module is ~21 px. Panel-level darkening, leaf litter, debris and canopy overhang are
  resolvable. Individual bird droppings (~1 px) are not.
- **`worldcover_tree`**, already in `location_features.py` and already noted there as "the
  strongest available proxy for the tree overhang that drives non-rain-resettable soiling."
  It is a 10 m raster, so it is a stratification variable, not a per-roof answer.
- **Permit vintage for 324 of the 1,865 AOI sites, and structurally none for the rest.**
  Measured at v4 by `scripts/analyze/permit_era_split.py`; see §3.5, which is the honest
  version of what this data can and cannot do. v1 quoted "5,944 PV permit rows over 5,443
  unique APNs, 3,975 (67%) pre-cutoff" here. Those three numbers are **not reproducible from
  the file under any filter** and they described the county, not the AOI. Replaced.
- **A measured experiment-design constraint**, produced at v1; see §3.

---

## 3. Ground truth plan: how to measure what a wash recovers here

### 3.0 The constraint that reorders everything (measured, v1)

New script: `scripts/analyze/washtest_power.py`. It measures the day-to-day scatter of an
irradiance-normalised daily production series on PVDAQ 2107, which is the noise any
before/after wash test has to beat, and converts it into a minimum detectable effect.

```
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/washtest_power.py --year 2024
```

Result (2024, hourly-mean integration, dimmest 25% of days dropped, 225 usable days):

| window each side | sigma (frac) | lag-1 rho | MDE, % of output | $/yr on a 6 kW home, NBT \| NEM2 |
|---:|---:|---:|---:|---|
| 7  | 0.029 | +0.14 | 5.05% | $75 \| $208 |
| 14 | 0.036 | +0.32 | 5.30% | $79 \| $218 |
| 30 | 0.044 | +0.55 | 5.86% | $87 \| $241 |
| 60 | 0.049 | +0.63 | 5.22% | $77 \| $215 |

**The MDE does not improve with more days.** Weather arrives in multi-day blocks, so the
lag-1 autocorrelation rises with the detrending window at almost exactly the rate that
extra samples would have helped. A single-array before/after test saturates at roughly a
**5% of output** detection limit, and that is a *floor*: 2107 has an on-site pyranometer, a
single plane of array, no roof obstructions, and 893 kW of spatial averaging. A residential
roof normalised against satellite irradiance is strictly worse, plausibly 1.5x to 2x.

Set that against the breakeven bars. A conventional before/after test on one home can just
about resolve whether a wash clears the **NBT** bar (6.1% to 10.1%). It **cannot** resolve
the **NEM 1.0/2.0** bar (2.2% to 3.6%), which is the case that actually matters, without
pooling something like 6 to 12 homes, at $150 each and one dry season of calendar time.

Two consequences, and they are the reason this section is ordered the way it is:

1. **"Wash 20 random homes and measure the average recovery" is both underpowered and aimed
   at the wrong estimand.** Randomly sampled Santa Cruz roofs are mostly clean, so it would
   spend a season and $3,000 measuring a number we already know is near zero.
2. **The experiment only becomes feasible if it is aimed at roofs where a large effect is
   predicted.** Selecting on visible localized soiling is not a shortcut, it is what makes
   the study possible at all. Which forces the imagery read to come first.

### 3.1 The options, ranked by confidence gained per dollar

| # | Option | Cash | Cameron time | Calendar | What it can establish | What it cannot |
|---|---|---|---|---|---|---|
| **1** | **Blind prevalence read of the 6 cm imagery already on disk** | **$0** | ~2 days | 1 week | How many of 1,865 roofs show continuous edge occlusion, canopy overhang, or debris, with a CI. Whether Tier A is even non-empty. Whether a rubric two people agree on exists (Cohen's κ) | Nothing about dollars. Nothing about what is under a tree canopy that hides the array |
| **2** | **Split-array wash on ONE volunteer with microinverters or optimizers** | ~$150 + recruiting | ~3 days | 4 to 6 weeks | Per-module recovery, directly, at roughly 1.5% MDE in two weeks, because weather is common-mode and cancels. Whether the effect is concentrated in specific modules | The string-inverter amplification (MLPE isolates it, so this measures the *lower* bound). One home is one home |
| **3** | **Whole-array before/after on 6 to 12 string-inverter homes, staggered wash dates** | $900 to $1,800 | ~2 weeks spread | one dry season | The amplified case, and a pooled AOI-level recovery estimate at ~2 to 3% MDE. The estimand the product actually ships | Cannot start before Option 1 says which homes. Locked to May-October; the 2026 window is nearly closed |
| **4** | **Imagery detector validated against ground photos** | $0 cash, labeling time | 6 to 11 hrs labeling + training | 3 to 4 weeks | Whether the Option-1 rubric can be automated to the whole AOI and at what precision | Nothing about dollars. Depends entirely on Option 1 producing a usable rubric |
| **5** | **Inverter APIs with opt-in, observational (no wash)** | $0 | ~1 week | ongoing | Baseline per-module dispersion across many homes; finds anomalous modules without touching anything. A cheap standing recruitment channel | Cannot attribute a dispersion to soiling rather than to shading, orientation, or a failing module. Correlational only |
| **6** | **More PVDAQ re-analysis (`pvdaq_2107` and siblings)** | $0 | ~2 days | 1 week | Further constraints on the *uniform* persistent layer. Already largely spent by §13 | Cannot say anything about localized coastal moss. Wrong climate, wrong mount, wrong scale |
| **7** | **Permit and parcel mining (`sc_solar_permits.csv`)** | $0 | done, ~1 day spent | done | RUN AT v4, see §3.5. Tariff vintage and install-era topology for **324 of 1,865** sites; the compounding full-cell segment is **97** | Not ground truth on recovery. And **structurally blind to 69% of the AOI**: the city is a separate permitting authority |
| **8** | **Acquire City of Santa Cruz permit records** | $0 to a records-request fee | ~1 day + wait | 2 to 6 weeks | Vintage for the **1,310** city sites now at zero coverage, taking AOI coverage from 17% to ~88% and reaching the oldest housing stock. Check SolarAPP+ first, it may already be public | Nothing about recovery. Depends on what the city publishes and in what format |
| **9** | **Ground-photo survey of edge-band thickness, from the street** | **$0** | **~1 afternoon** | **1 day** | **The distribution of f, the one variable the physics says decides everything.** Directly, with a ruler or a module frame for scale. Can kill Tier A outright | Not a prevalence estimate. A street sample is biased toward street-facing roofs and toward whatever the photographer notices |

### 3.2 What to do first, and why

**This ordering changed at v5.** v1 put the imagery read first, on the reasoning that every
other option is conditioned on it. Three iterations of measurement have undercut that: the
imagery cannot grade severity (§5, the band is sub-pixel), the physics says severity is the
whole question (§2), and the permit segmentation turned out to cover 17% of the AOI (§3.5).
The cheapest decisive experiment is no longer the one that was first.

**Today, in this order:**

**0. Fire Option 8, the city records request.** Five minutes of effort and a 2 to 6 week
external wait, so it is pure loss to leave it until later. It does not block anything and
nothing blocks it.

**1. Option 9, the ground-photo survey. An afternoon, $0, and it can kill Tier A outright.**
This is the reordering. The entire Tier A thesis reduces to one unmeasured quantity: does the
edge band on real Santa Cruz roofs reach f = 0.2 to 0.3, the point where it clears a
breakeven bar? Everything else in this document is downstream of that number and nobody has
looked at a single roof.

The decisive version of this is cheap because **it is a question about the upper tail, not
the mean.** We do not need a representative sample. We need to know whether f *ever* gets
that high here. So: walk or drive the high-canopy neighbourhoods, photograph every array with
visible edge growth, and measure the band against the module frame (a standard frame is 35 to
40 mm, and the cell pitch is 156 mm, so both give scale without carrying a ruler up a
ladder). **If the worst band findable from the street sits below f = 0.1, Tier A is dead on
physics and this project should stop here and say so.** A max statistic over an
opportunistic sample answers that, and no amount of imagery work can.

It is also the cheapest way to test the §5 detectability claim, because every photographed
roof can be matched against its own 6 cm chip. That is Option 4's validation set, obtained as
a free by-product.

**2. Option 1, the blind imagery prevalence read**, but only if Option 9 shows f reaching the
threshold. If it does, Option 1's two days are well spent and it now answers a well-posed
question: *how many* roofs carry a band we already know can matter. If Option 9 comes back at
f < 0.1, Option 1 is two days spent counting something that cannot pay, and should be
skipped.

**3. Option 2, the split-array wash**, on a candidate from Option 9 or Option 1. Still the
cheapest thing that can produce a dollar number, and still 3 to 4x more powerful per home
than Option 3, because differencing two halves of one roof cancels the weather.

Option 3 only if 1 and 2 both come back positive. Option 5 is worth standing up early
regardless, since it is free and it is how Option 2 finds its volunteer.

**What this ordering is protecting against.** The failure mode for a project like this one is
spending two days on a beautiful stratified imagery read, finding 8% prevalence, feeling
encouraged, and only then discovering that the bands are 15 mm wide and none of them ever
mattered. Option 9 costs an afternoon and forecloses that.

### 3.2b Protocol for Option 9 (the one to actually start with)

Deliberately short, because it is an afternoon, not a study. The point is a decision, not a
dataset.

1. **Pick the ground.** The high-canopy neighbourhoods, which `worldcover_tree` already
   ranks, plus anywhere with mature trees and north-facing or low-slope roofs. Shade and
   moisture are what grow moss; sun-baked south-facing roofs are not where to look.
2. **Photograph opportunistically, and do not sample randomly.** This is a max statistic. The
   question is whether f reaches 0.2 to 0.3 *anywhere in this town*, so photograph the worst
   arrays findable and skip the clean ones. A representative sample would answer a question
   nobody is asking yet.
3. **Get scale into every frame.** The module frame is 35 to 40 mm and the cell pitch is
   156 mm, so either gives f without a ruler. Shoot square-on to the module plane where
   possible; a raking angle foreshortens the band and biases f *upward*, which is the
   dangerous direction.
4. **Record, per array:** the estimated band thickness in mm, module orientation (portrait or
   landscape), approximate module count, whether growth is continuous across module widths or
   scattered, and the street-level cross-street only. **No addresses, no APNs, no house
   numbers in frame.** These are photographs of private homes taken from public land, so
   store them outside both repos and treat them as §6 material from the first shutter press.
5. **Stop rule, written before going out.** If, after a full afternoon in the areas most
   likely to show it, the thickest continuous band found is **below f = 0.1 (about 16 mm)**,
   then Tier A does not exist in this AOI at any prevalence, and the honest output is a short
   public writeup saying exactly that. That is a good outcome and it should be published with
   the same energy as a positive one would be.
6. **If bands do reach f = 0.2 or more**, note where, and those arrays become the Option 2
   recruitment shortlist and the Option 4 validation set. Match each one back to its own 6 cm
   chip to test the §5 detectability claim directly, which costs nothing extra.

What this cannot do: it is not a prevalence estimate, and it must never be written up as one.
A street sample sees street-facing roofs and whatever the photographer noticed. Prevalence is
Option 1's job and requires the blind protocol below.

### 3.3 Protocol for Option 1 (run it second, after Option 9)

The failure mode is obvious and it is confirmation bias, so the protocol is built against
it:

1. **Pre-register the rubric before looking at any roof.** Four ordered classes, written
   definitions plus 3 reference chips each:
   - **L0** clean: no visible occlusion, panel field uniform.
   - **L1** scattered: isolated spots or streaks, not continuous, not edge-aligned.
   - **L2** edge-continuous: a darkened band along the low edge of one or more modules,
     continuous across at least one module width. *This is the class the physics says
     matters.*
   - **L3** gross: debris field, branch litter, or occlusion over a substantial panel area.
   - Plus a separate binary: **canopy overhang within 2 m of the array**.
   - Plus **module mounting orientation** (portrait / landscape / mixed / cannot tell),
     recorded per array. Added at v2: the physics in §2 makes the same edge band worth
     7.00% landscape and up to 21.00% portrait, so an L2 read without orientation cannot be
     costed. It is readable at 6 cm and costs the reader nothing extra.
   - Plus **U** unreadable (array occluded by tree, image artefact, deep shadow).
2. **Sample 200 sites stratified by `worldcover_tree`**, oversampling the high-canopy
   stratum, and record the stratum weights so the prevalence estimate can be reweighted back
   to the 1,865.
3. **Include ~20 negative controls**: sites with a permit issued in the last 24 months and
   zero canopy. If those score L2 or L3, the rubric is picking up something that is not moss
   and the whole read is invalid.
4. **Two independent readers, blind to each other and to the stratum.** Report Cohen's κ.
   **Gate: if κ < 0.60 the rubric is not usable**, and no detector trained on it will be
   either. Revise the rubric and re-read rather than proceeding.
5. **Report prevalence of L2/L3 with a Wilson interval**, reweighted to the AOI, and report
   the U rate honestly as a separate denominator. Sites we cannot read are not clean sites.

Output: an internal, salted-hash-keyed CSV (see §6) and a one-page result. **No addresses,
no APNs, no owner names, in the repo or anywhere else.**

### 3.4 Protocol sketch for Option 2 (split-array)

Recruit one homeowner with microinverters or DC optimizers (per-module reporting), an L2/L3
roof from Option 1, and willingness to have half the array washed. Baseline 14 days of
per-module 15-minute energy. Wash a contiguous half chosen so both halves span the same
rows and orientations. Continue 14 to 30 days. The estimator is the *ratio* of washed-half
to unwashed-half energy, before versus after, which cancels irradiance, temperature and
cloud transients because they are simultaneous, and needs no irradiance data at all.
Expected σ on that daily ratio is roughly 1 to 2% (UNMEASURED; pin it from the first
baseline fortnight before committing to a window length), giving an MDE near 1.5% at 30 days
each side, which resolves the 2.2% NEM 2.0 light-pro bar.

Honest limitation, stated up front: MLPE isolates localized shading, so this measures the
**lower bound** of the localized-soiling loss. A string-inverter home would read higher. The
design is most powerful exactly where the hypothesized effect is weakest. That is a reason
to run Option 3 afterwards, not a reason to skip Option 2.

Consent, retention and the fact that results may appear in aggregate in a public writeup all
have to be in writing before any data is pulled. See §6.

### 3.5 What the permit data can actually segment (measured, v4)

v1 and v3 both treated permit vintage as a free, AOI-wide segmentation variable. It is not.
`scripts/analyze/permit_era_split.py`:

```
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/permit_era_split.py \
    --filter-sensitivity --json outputs/analysis/permit_era_split.json
```

**Only 324 of the 1,865 scored AOI sites (17.4%) join to any permit record.** The other 1,541
have none. And the missingness is not attrition, it is a jurisdiction boundary.

**Verified at v7 against the parcel layer rather than inferred from the join.** Taking every
parcel in `data/external/santa_cruz_parcels/`, not just the ones with a detected array, and
computing the county-permit match rate per APN book ordered by longitude:

| APN book | parcels | with a county permit | rate | book centroid |
|---|---|---|---|---|
| 002, 003, 004, 005, 006, 007, 010, 011 | **9,246** | **0** | **0.00%** | lon -122.051 to -122.004 |
| 027, 026, 029, 028, 031, 032, 033 | 5,036 | 764 | 11.5 to 18.3% | lon -121.997 to -121.963 |
| 034 | 887 | **0** | **0.00%** | lon -121.962 |

**The boundary is a clean line at longitude -122.00**, and 9,246 parcels returning exactly
zero permits is not explicable by attrition under any hypothesis. West of the line is the
**City of Santa Cruz**, which permits its own construction; `sc_solar_permits.csv` is built
from *County* "Building Permits Issued" PDFs.

**Book 034 is a second jurisdiction and v4 missed it.** It sits east of the line, immediately
beside book 033 which permits at 18.3%, and returns zero across 887 parcels. That is
**Capitola**, also its own permitting authority. v4 also missed book **005** on the city side,
having read the prefix list off the AOI join rather than the parcel layer.

Corrected AOI impact:

| jurisdiction | AOI sites | matched to a permit |
|---|---|---|
| City of Santa Cruz (8 books) | **1,310** | 0 |
| Capitola (book 034) | 11 | 0 |
| County (permitted books) | 535 | 341 |

So the city is **70% of the AOI** and the single request that matters; Capitola is 11 sites
and can be a footnote. **The residual 194 unmatched sites inside county-permitted books is
the honest size of the pre-2016-plus-parse-failure population**, which v6 left as an open
question and this resolves: it is 194 sites, not a large unknown. Every one of them is a
*detected array*, so it has solar and no permit record.

For the 324 that do join, one row per parcel at its earliest permit:

| era | AOI sites | share | residential-scale (2 to 15 kW) |
|---|---|---|---|
| 2016 to 2018, NEM 1.0/2.0, full-cell | **97** | 29.9% | 87 |
| 2019 to 2023-04-13, NEM 2.0, half-cut | 150 | 46.3% | 143 |
| 2023-04-14 onward, NBT, half-cut | 77 | 23.8% | 71 |

Sensitivity to the half-cut transition year, which is a market shift and not a cutover: the
full-cell segment is 66 sites at 2018, 97 at 2019, 132 at 2020.

**Three consequences.**

1. **The compounding segment measurable today is 97 sites, not thousands.** That is small
   enough to change the shape of the programme: it is a list you could work by hand, and it
   is too small to justify training a detector against on its own.
2. **The two data gaps compound in the worst direction.** The county file starts in 2016, so
   pre-2016 NEM 1.0 installs are missing from the county portion; the city portion is missing
   entirely. Both gaps preferentially remove *old* arrays, which is exactly the segment the
   physics says is worth the most. Every number above is a **floor**.
3. **There is a concrete, cheap unblock**: City of Santa Cruz permit records, via the city's
   portal or a public records request. That is data acquisition, not analysis, and it would
   take coverage from 17% of the AOI to roughly 88%. It belongs on the list as its own task.
   Note the city runs residential PV through **SolarAPP+** (NREL's automated permitting
   platform), so some of this may already be published and not need a request at all. Check
   that before filing. Capitola is a separate, much smaller second request worth 11 sites.

**On v1's permit counts.** v1 stated "5,944 PV permit rows over 5,443 unique APNs ... 3,975
(67%)". No filter reproduces that triple: the file yields 8,780 rows / 7,399 parcels
unfiltered, 5,904 / 5,332 under the widest defensible PV filter, 5,558 / 5,154 on parsed
mount, 2,810 / 2,645 on description text. The 67% share is reproducible; the counts are not.
More importantly the figure was county-wide and was being read as though it segmented the
AOI. `permit_era_split.py` documents its filter in the docstring and prints the sensitivity,
so the next person can see which one produced any number quoted from it.

---

## 4. Tiered segmentation, replacing the single global verdict

Every tier states its trigger, its confidence, and its dollar range. **Where a dollar range
is unmeasured it says unmeasured.** The counts are the honest current state, which is that
the tiering machinery does not yet exist and Tier A is empty.

### Tier A: clean now

- **Trigger (all of):** L2 or L3 on 6 cm imagery, **confirmed by a ground photo** (not
  optional as of v2: §5 shows the band's thickness is sub-pixel, so imagery establishes
  presence and cannot establish severity); permit vintage before 2023-04-14; array not
  obviously MLPE. **Record mounting orientation**, because portrait and landscape differ on
  the same band.
- **Rank within the tier by install era (v3).** Pre-2019 full-cell portrait arrays are the
  highest-value candidates on three compounding grounds: the tariff (2.8x), the cell
  architecture (up to 2x on the portrait case), and string-inverter topology (up to 4.6x
  where occlusion is partial). Post-2019 half-cut arrays in landscape are the weakest
  candidates and should not be contacted first, even at the same L2 read.
- **Confidence today: none.** The trigger requires a per-roof imagery read nobody has done
  and a recovery measurement nobody has made.
- **Dollar range: UNMEASURED.** The physics is now measured (§2) and bounds a qualifying roof
  at 1.75% per dead substring, 7.00% for a landscape edge band across 4 modules, and up to
  21.00% for the portrait case on a string inverter. **That is a bound on the loss, not an
  estimate of the recovery**, and the two are different numbers: nothing here says a wash
  removes the moss, or for how long. Until Option 2 returns, this tier quotes **no dollar
  figure at all**, and per §5 no dollar figure may ever be attached to an imagery-only read.
- **Current population: 0 of 1,865.** Not "small". Zero. Nothing in the AOI qualifies today
  because the qualifying evidence does not exist yet.

### Tier B: clear the cause, then re-check next season

- **Trigger:** canopy overhang within 2 m of the array, or L3 debris, or L1 scattered
  occlusion, regardless of tariff vintage.
- **Recommended action: trim the branch and clear the litter.** Free, or the price of an
  afternoon. Explicitly **not** a cleaning referral.
- **Confidence: moderate**, and it does not need to be higher, because the action costs the
  homeowner nothing and removes the upstream cause rather than treating the symptom. The
  decision is robust to the uncertainty in a way Tier A's is not.
- **Dollar range: UNMEASURED**, and the honest framing is that the value is in preventing
  the L2 state rather than in recovering output today.
- **Expected population:** unknown until Option 1 runs. `worldcover_tree` gives a coarse
  prior that is not a per-roof answer.
- **This tier is where the nonprofit is on firmest ground**, both scientifically and
  governance-wise: free advice, no commercial party involved, no claim that needs a dollar
  figure behind it.

### Tier C: leave it alone

- **Trigger:** L0 on imagery, no canopy overhang. Which is to say, the default.
- **Confidence: high.** This is the measured result and it survives every correction
  currently on the table. Dust in Santa Cruz is rain-reset, and the one direct measurement
  of a persistent uniform layer (PVDAQ 2107, §13) found 0.0 pts/yr with a [0, 0.2] interval.
- **Dollar range: a $90 to $150 wash returns $3.71 to $10.29/yr**, so it loses $80 to $146
  in year one. Negative, and not marginally.
- **Expected population: the large majority of 1,865, and this must not be lost.** It is the
  project's most credible finding and it was arrived at by talking itself out of revenue.

### Tier D: cannot tell

- **Trigger:** array not readable from imagery (canopy occlusion, shadow, artefact), or no
  array polygon at adequate confidence, or no permit record.
- **Confidence: n/a by construction.**
- **Action: say so.** Do not fold these into Tier C. A roof we cannot see is not a clean
  roof, and silently defaulting unknowns to "leave it alone" would be the same category of
  error as the 4.70% anchor: a number that looks like a measurement and is not.
- **Measured at v4, and it is most of the AOI on the permit axis.** 1,541 of 1,865 sites
  (82.6%) have no permit record, so they cannot be placed in an era at all: **1,310 because
  their parcel is in the City of Santa Cruz** and this file is the county's, **11 in
  Capitola** for the same reason, and **194** because the file starts in 2016 or the row
  failed to parse. Both gaps preferentially remove *old* arrays, the highest-value Tier A
  candidates. Jurisdiction boundary verified against the parcel layer at v7. See §3.5.
- This does not make those sites Tier D outright, since imagery still reads them. It means
  they cannot be **ranked** by the compounding value signal, only flagged. Option 8 is the
  fix and it should be started immediately.

---

## 5. The detection work that feeds the tiers

### What to detect, and from what

| Signal | Imagery | Feasible at that GSD? | Feeds |
|---|---|---|---|
| Canopy overhang within 2 m of array | 21 cm county, or 6 cm | Yes, comfortably | Tier B |
| Debris field / branch litter on array | 6 cm (0.078 m/px) | Yes | Tier B, Tier A |
| Continuous darkened band along module low edge | 6 cm | **Sub-pixel, and now quantified as the crux.** See below | **Tier A** |
| Individual bird droppings | 6 cm | No, ~1 px. And per §2 they do not clear the bar anyway | nothing |
| Within-array module-to-module reflectance dispersion | 6 cm | Yes, and this is the most robust construct available | Tier A candidate generation |
| Uniform dust | any | **No, and it never will be.** This is the point: it is invisible from the air and it is also the channel that does not pay | nothing |

### The detectability problem got harder at v2, not easier

v1 asked whether a "10 cm moss strip at ~1.3 px" is detectable. The §2 measurement replaces
that guess with a threshold, and the threshold is **thinner than the strip v1 was worried
about**. On a 156 mm cell:

| band covers f of the bottom cell row | physical band | at 7.8 cm/px | at 21 cm/px | 4-module loss, full-cell portrait (MLPE) | same, half-cut |
|---|---|---|---|---|---|
| f = 0.1 | 16 mm | 0.20 px | 0.07 px | 0.63%, below every bar | 0.37% |
| **f = 0.2** | **31 mm** | **0.40 px** | 0.15 px | 2.15%, clears the NEM 2.0 light-pro bar | 1.34%, below it |
| **f = 0.3** | **47 mm** | **0.60 px** | 0.22 px | 4.01%, clears the NEM 2.0 professional bar | 2.41% |
| f = 0.5 | 78 mm | 1.00 px | 0.37 px | 8.20% | 4.57% |

**The decision boundary sits at 0.4 to 0.6 px on a full-cell portrait roof, and moves further
out of reach on a half-cut one**, where f has to reach roughly 0.3 before the NEM 2.0
light-pro bar is cleared at all. That is below the sampling limit of the best imagery we
have, so a per-roof read cannot resolve *how thick* the band is, which is the variable that
decides whether the roof qualifies and whether the string-inverter amplification applies.
Two consequences, and they are the most important open items in this document:

1. **The Option 1 rubric cannot grade severity, only presence.** L2 has to mean "a band is
   there", and the honest costing of an L2 roof spans 0.63% to 8.20% depending on an
   unresolvable f. Do not let the rubric imply otherwise.
2. **This is why the Option 2 split-array wash matters more than the imagery**, and it is an
   argument for reordering. A sub-pixel band that is invisible in aerial imagery is perfectly
   visible in a ground photo from the street, and it is fully resolved by the wash itself.
   The imagery read finds *candidates*; only the ground photo and the wash can grade them.

This does not kill Tier A. Presence of an edge band is still detectable as a low-contrast
line running the full ~21 px module width, and the dispersion measure below does not depend
on resolving the band's thickness. But it does mean **no dollar figure should ever be
attached to an imagery-only read**, which tightens §4's Tier A rule rather than loosening it.

**The within-array dispersion measure is the one to build first.** Rasterize each array
polygon, estimate the module grid, and compute the dispersion of per-module median
reflectance *within* that array. It is a relative measure, so it cancels the nuisances that
wreck absolute radiometry across a single-date mosaic: sun angle, BRDF, atmospheric state,
module model, tilt. A roof where one row of modules is systematically darker than its
neighbours is exactly the L2 hypothesis, and it needs no training labels to compute.

### Labeling effort

If Option 1 returns an L2/L3 prevalence near 8%, a randomly drawn 200-chip label set
contains ~16 positives, which trains nothing. Realistically:

- Target ~1,500 roof chips labeled, sampled **non-randomly**: canopy-stratified, plus
  hard-negative mining from the dispersion measure above, to reach a few hundred positives.
- At ~20 s/chip that is **8 to 9 hours** of one person, which against 20 hrs/week of capacity
  is roughly half a week. Roboflow is already in the stack
  (`docs/NAIP_ROBOFLOW_WORKFLOW.md`, `scripts/labeling/`).
- Ground photos for a validation subset: ~30 roofs, obtainable from the street or from
  volunteer homeowners. This is the only thing that makes the imagery labels mean anything
  physical, and it should be budgeted from the start rather than promised later.

### What accuracy is good enough to act on

Set by the cost of an error, not by a leaderboard:

- **A false positive costs a homeowner contact that turns out to be wrong**, on a roof where
  we told them something was there and it was not. For a nonprofit whose credibility rests
  on having talked itself out of a revenue conclusion, that is the expensive error.
- **A false negative costs a missed lead**, which costs this project nothing it currently
  has.

So: **precision ≥ 0.80 on the Tier A flag, at whatever recall falls out.** Recall is not
gated. And the accuracy bar is partly a *communication* design rather than a model metric:
the output shown to a homeowner should be the image chip of their own roof with the flagged
region marked, plus what we think it is, plus an explicit "look for yourself" and an explicit
statement that we have not measured what it costs. A detector that hands over evidence needs
lower accuracy than one that hands over a verdict.

Reuse the Stage 1 evaluation discipline rather than inventing one: box-level matching
through `src/utils/det_match.py`, threshold tuned on val and frozen, test reported once,
bootstrap CI resampled over **tiles** rather than objects. The same reasons apply.

---

## 6. Connecting homeowners with cleaners: mechanics, privacy, governance

### Privacy, designed in rather than bolted on

The artifact that must never exist in public is a list of addresses that need cleaning. The
site has already shipped one leak of this shape (1,856 real parcel numbers in a dashboard
payload, fixed in `13185d3`).

- **Every internal artifact is keyed by `site_key = HMAC-SHA256(salt, APN)` truncated**, with
  the salt in a secret store, never in either repo, and never in a manifest. The
  APN-to-site_key mapping lives in exactly one gitignored file.
- **Published artifacts carry `site_key` only**, and only where a public artifact needs a
  join key at all. Prefer publishing nothing per-site.
- **Extend the BBF PII guard from the specific leak to the general shape:** reject APN
  patterns, street-address patterns, and owner-name-shaped fields, not just the field that
  leaked last time.
- **Homeowner-supplied data (inverter API, photos, consent forms) never enters either
  repo.** Separate store, written retention period, deletion on request.
- The Option 1 output is a stratified sample, so it is a *research* dataset, not a lead
  list. Keep it that way: do not extend the read to all 1,865 until there is a decided
  answer to the governance question below.

### Governance: flagged, not resolved

This project sits under Ecologistics, an active 501(c)(3) fiscal sponsor that holds the
budget. Referring homeowners to paid cleaners is commercial activity and it is not the
project's call to make. The specific questions, for Cameron to take to Ecologistics and to
counsel, listed in increasing order of seriousness:

1. **Is a free, unpaid, non-exclusive referral to a list of local cleaners consistent with
   the exempt purpose?** Plausibly yes, as consumer education. Still needs an answer on the
   record.
2. **Does directing qualified leads to specific commercial cleaners create private benefit?**
   A non-exclusive published list is a very different question from a routed lead.
3. **The 15% take modelled in `outputs/economics/lightpro_opportunity.md` is
   straightforwardly commercial revenue** and would be unrelated business income at best. If
   that is the direction, it likely belongs in a separate entity, not in the nonprofit.
4. **Is there a university IRB position** on collecting production data and photographs from
   named homeowners, given the funding? Probably light-touch, but ask before recruiting, not
   after.

**Recommended default while those are open**, which is a recommendation and not a decision:
publish the *method and the finding*, including the finding that most roofs should be left
alone; give any individual homeowner who asks the image of their own roof and an honest
"unmeasured"; and connect nobody to anybody until Option 2 has produced a number and the
board has answered question 1. The /solar pages carry no commercial claims today and nothing
in this plan changes that.

### The mechanics, if and when it is cleared

Deliberately thin, because the governance answer determines the shape and there is no point
building ahead of it. The minimum viable version is a published list of local cleaners with
no routing, no exclusivity, no fee, and no per-lead relationship. Anything richer than that
(intake, routing, recurring billing, the `POST /feedback` recovery loop sketched in
`Q2_PLAN.md` Track C) is a decision for after §3 Option 2 returns.

---

## 7. Register: what is measured, what is assumed

| Quantity | Value | Status |
|---|---|---|
| AOI recoverable soiling loss | 5.56% | Modelled (regression head), anchor known biased high |
| One-clean recovery, dust channel | 0.045 | Measured on Santa Cruz weather via `recovery.py`; depends on `k=15`, which is itself suspect |
| Uniform persistent layer | 0.0 pts/yr, [0, 0.2] | **Measured**, PVDAQ 2107, wrong climate and mount for the AOI |
| Localized opaque soiling prevalence in AOI | unknown | **UNMEASURED.** Option 1 |
| Recovery from washing localized soiling | unknown | **UNMEASURED.** Option 2 |
| Daily normalised-production noise floor | sigma 0.029 to 0.049, rho +0.14 to +0.63 | **Measured (v1)**, PVDAQ 2107 2024; a floor, not the residential value |
| Single-array before/after MDE | ~5% of output | **Measured (v1)**; a floor |
| Split-array daily-ratio sigma | assumed 1 to 2% | **UNMEASURED.** Pin from the first baseline fortnight |
| Array loss per dead substring | 1.75% of 20 modules | **Measured (v2)**, `substring_shade_loss.py`. Was a 1/60 derivation |
| Edge band, 4 modules, landscape | 7.00% | **Measured (v2)**, MLPE and string identical, and (v3) identical for half-cut |
| Edge band, 4 modules, portrait, full-cell | 8.20% MLPE / 21.00% string | **Measured (v2)** |
| Edge band, 4 modules, portrait, half-cut | 4.57% MLPE / 15.88% string | **Measured (v3)**, architecture **corroborated against PVsyst docs and re-run on the documented diode circuit (v6)**, unchanged |
| AOI sites with any permit vintage | 324 of 1,865 (17.4%) | **Measured (v4)**, `permit_era_split.py`. Of the 1,541 missing: **1,310 City of Santa Cruz, 11 Capitola, 194 pre-2016 or parse failure** (v7, verified against the parcel layer) |
| AOI full-cell (2016 to 2018) segment | 97 sites, 87 residential | **Measured (v4)**. A **floor**: both data gaps preferentially remove older arrays |
| AOI half-cut NEM 2.0 (2019 to 2023-04) segment | 150 sites | **Measured (v4)** |
| Vintage of the 1,310 City of Santa Cruz AOI sites | unknown | **UNMEASURABLE from current data.** Requires Option 8. Boundary verified at v7: 9,246 city-book parcels, zero county permits |
| v1's "5,944 rows / 5,443 APNs / 3,975 pre-cutoff" | not reproducible | **RETRACTED (v4).** No filter yields that triple, and it was county-wide being read as AOI-wide |
| Bird dropping, 60% of one cell | 0.93% | **Measured (v2)**. Below every breakeven bar |
| String-inverter amplification vs MLPE | 1.00x saturated, up to 4.6x partial | **Measured (v2)**. v1's "1.5x to 3x blanket" was wrong in both directions |
| Irradiance dependence of the loss fraction | none, 200 to 1,000 W/m2 | **Measured (v2)**. This is what lets a DC fraction be quoted as an annual energy fraction |
| Distribution of band thickness f on real roofs | unknown | **UNMEASURED**, and sub-pixel at 7.8 cm/px. Decides whether the 4.6x window applies at all |
| Recovery from washing a band (as opposed to the loss it causes) | unknown | **UNMEASURED.** Option 2. The physics bounds the loss and says nothing about the recovery |
| Benefit duration of a biological-layer wash | assumed seasons | **UNMEASURED** |
| Specific yield | 1,500 kWh/kWp/yr | **UNMEASURED here.** PVWatts-typical for coastal CA |
| Electricity rate | $0.1646 NBT / $0.4573 NEM 2.0 | Sourced, `src/risk/rates.py`, bill-reconciled |
| `MIN_PRO_SERVICE` $150, light-pro $90 | | `MIN_PRO_SERVICE` still **UNSOURCED**; it decides most residential cases alone. Get three local quotes |
| NEM vintage split of permitted parcels, county-wide | 3,679 of 5,332 (69%) before 2023-04-14 | **Re-derived (v4)**, replacing v1's unreproducible "3,975 of 5,944". Known bias in both: NEM eligibility follows *interconnection application* date, not permit issue date, so 2023 understates the NEM 2.0 share |

### Side finding, recorded for `ECONOMICS_GROUNDING_20260809.md` §13

§13 left the 2025 PVDAQ 2107 anomaly open ("worth ten minutes before anyone quotes the annual
table"). Ten minutes spent, partially resolved:

- The 2025 irradiance export is stamped `utc_measured_on` in **UTC** while the meter file
  beside it stays in **local time** (verified: meter peaks at hour 12, irradiance at hour 20).
  Binning both by naive calendar day misaligns them by eight hours. Now corrected in
  `washtest_power.py`.
- The 2025 irradiance export also has a **bimodal cadence**, per-day sample counts clustering
  at 95 and 240, so summing raw samples makes each daily total depend on how often the logger
  wrote. Now corrected by integrating hourly means.
- **Neither fixes it.** Post-correction 2025 scatter is still ~11% against 2024's ~4%.
  Something else is wrong with the 2025 vintage. **Do not use 2025 PVDAQ 2107 data for
  soiling analysis until this is understood**, and treat §13's 2025 annual-median drop
  (0.894) as suspect rather than physical.

---

## 8. Decision log (append only)

### v1, 2026-08-11

- **Created.** Branch `soiling/aoi-cleaning-targeting`.
- **Reframed the question** from "model dust better than NREL" to "detect the conditions dust
  models cannot see." Rationale: the dust answer is right and no correction to the anchor or
  to `k` moves it, so continuing to refine it cannot change a single homeowner's decision.
- **Added the substring arithmetic** (§2) rather than asserting that localized soiling is
  nonlinear. It is nonlinear *and it saturates*, and the saturation is what makes the answer
  interesting: a qualifying roof needs continuous edge occlusion across roughly 4 modules,
  not a few droppings. That is a falsifiable prediction about what a Tier A roof looks like,
  and it is the thing that would survive a cleaning company arguing every roof qualifies.
- **Measured the experiment-design constraint instead of assuming it.** Wrote
  `scripts/analyze/washtest_power.py` against PVDAQ 2107. The finding that a single-array
  before/after test saturates near a 5% MDE regardless of window length, because of weather
  autocorrelation, is what forced the ordering in §3.2: the imagery read has to come before
  the wash test, because an unselected wash test is underpowered against the tariff regime
  that matters.
- **Promoted the split-array design over conventional before/after** for the first dollar
  measurement, on the grounds that differencing two halves of one roof cancels the dominant
  noise term outright and needs no irradiance data. Recorded its limitation honestly: MLPE
  isolation means it measures the lower bound.
- **Kept Tier C as the headline** and stated Tier A's population as 0, not "small". The
  temptation in a document like this is to imply the flagged tier already exists.
- **Added Tier D** so unreadable roofs are not silently absorbed into "leave it alone".
- **Flagged the pre-2016 permit gap**: NEM 1.0 homes, the highest-value targets, are absent
  from the permit file entirely.
- **Fixed two data defects in PVDAQ 2107's 2025 files** (timezone, cadence) and recorded that
  neither explains §13's open 2025 anomaly.
- **Did not resolve** the governance question, per the constraint. Recommended a default and
  named who has to decide.

### v2, 2026-08-12

Attacked weakest part 1 of v1: the substring arithmetic. Wrote
`scripts/analyze/substring_shade_loss.py`, a cell-level single-diode model (pvlib
`bishop88`, bypass diodes, reverse-bias avalanche breakdown, 60-cell 295 W module, 20-module
array) and replaced the derivation with measurement.

- **v1's substring arithmetic survives and was slightly conservative**: 1.75% measured
  against 1.7% claimed for one dead substring, 7.00% against 6.7% for four. Tier A is not
  killed on physics. This was the outcome that would have been cheapest to discover and it
  did not happen, so the imagery spend is still justified.
- **Corrected the bird-dropping case from an implied 1.7% to a measured 0.93%.** A dropping
  covering most of a cell current-limits its substring rather than killing it. v1's
  conclusion that droppings never pay is unchanged and is now measured.
- **Found the orientation effect, which v1 did not model at all.** The three substrings run
  along the module's long axis, so a low-edge band hits one substring in landscape and all
  three in portrait: 7.00% versus 8.20% MLPE, and 7.00% versus 21.00% on a string inverter.
  Same moss, up to 3x the cost, decided by a mounting choice that is visible at 6 cm. Added
  orientation as a required field in the §3.3 rubric and to the Tier A trigger.
- **Killed v1's "string inverters amplify this 1.5x to 3x, direction certain".** Measured, it
  is 1.00x once a substring is fully bypassed, and it reaches 4.6x in a partial-occlusion
  window, so v1 was wrong about both the universality and the ceiling. The install-year
  topology proxy now applies only to roofs whose occlusion reads as partial.
- **Checked irradiance invariance**, because "you computed one operating point" is the
  obvious rebuttal. The loss fraction moves by less than 0.3 points across Ee = 200 to 1,000
  W/m², so the instantaneous DC fraction is quotable as an annual energy fraction.
- **Made the detectability problem quantitative, and it got worse.** The decision boundary is
  f = 0.2 to 0.3, a 31 to 47 mm band, which is 0.40 to 0.60 px at 7.8 cm/px. Imagery can
  establish that a band is present and cannot establish how thick it is, so an L2 read alone
  spans 0.63% to 8.20%. Made ground-photo confirmation mandatory in the Tier A trigger rather
  than optional, and wrote in that no dollar figure may attach to an imagery-only read.
- **Fixed a number-provenance error in §2.** v1's "4.6x more easily on a NEM home" was
  10.1% / 2.2%, which compares the NBT professional bar to the NEM 2.0 light-pro bar and so
  buries the wash price inside a figure labelled as the tariff effect. Holding the wash fixed
  the tariff ratio is 2.8x. Same category of error as the 4.70% anchor: a ratio that looks
  like one thing and is another.
- **Did not change** the Tier C verdict, the option ordering, or any dollar figure, because
  nothing measured here bears on recovery. The physics bounds the *loss*; only Option 2 can
  measure what a wash gets back.

### v3, 2026-08-12

Attacked weakest part 4 of v2: the model assumed a 3-substring full-cell module, and half-cut
modules have been the dominant residential product since roughly 2019. Extended
`substring_shade_loss.py` to carry two parallel half-strings (series aggregation sums V at
common I; parallel sums I at common V), holding the module's electrical spec fixed so the
comparison isolates topology from module choice.

- **Half-cut roughly halves the portrait penalty and changes nothing in landscape**, exactly
  as the parallel architecture predicts. Portrait f = 0.5 across 4 modules: 8.20% to 4.57%
  MLPE, 21.00% to 15.88% string. At f = 1.0 it is 20.00% against 10.00%, a clean factor of
  two. Landscape is 7.00% either way, because the split runs across the band rather than
  along it.
- **This turned two weak segmentation signals into one compounding one.** Install year now
  carries the tariff (2.8x), the cell architecture (up to 2x), and the inverter topology
  (up to 4.6x where occlusion is partial), all pointing the same direction. The oldest arrays
  are worth the most per lost kWh *and* lose the most per unit of soiling. Added the era
  table to §2 and a ranking rule inside Tier A.
- **It also makes the Tier D permit gap worse in a specific way.** The single highest-value
  segment is pre-2016 NEM 1.0 full-cell portrait, and `sc_solar_permits.csv` starts in 2016,
  so that segment is missing from the data entirely rather than merely under-counted.
- **Detectability got harder again.** On a half-cut roof the band must reach f = 0.3 before
  it clears even the NEM 2.0 light-pro bar, against f = 0.2 full-cell, so the decision
  boundary moves further below the 0.4 px sampling limit. Updated the §5 table with both.
- **Also cleared weakest part 1** while in §2, since it was cheap and adjacent: added an
  explicit paragraph that every figure in the section is what occlusion *costs while present*
  and none of it is a recovery figure, naming $3.71 to $10.29/yr as the recoverable number
  that actually ships. The 21.00% was the most misreadable number in the document.
- **Did not change** any dollar figure, the option ordering, or Tier C.

### v4, 2026-08-12

Attacked weakest part 2 of v3: the era split was derivable from a file on disk and had not
been derived. Wrote `scripts/analyze/permit_era_split.py`. The answer is smaller and more
structural than expected, and it moves a load-bearing assumption.

- **Only 324 of 1,865 AOI sites (17.4%) join to any permit record**, and the missingness is a
  jurisdiction boundary rather than attrition. Seven AOI APN prefixes covering 1,288 sites
  return **zero** matches across an 8,780-row, 11-year file. (v7 corrected this to eight
  books and 1,310 sites, plus Capitola as a second authority; see the v7 entry.) `sc_solar_permits.csv` is built
  from *County* permit PDFs and the City of Santa Cruz permits its own construction, so 69%
  of the AOI was never in this file. Checked the obvious alternative first: APN formats are
  identical on both sides and the normalized join equals the raw join, so it is not a
  formatting bug.
- **The compounding full-cell segment is 97 sites, 87 of them residential.** v3 implied this
  was the free, large, already-available signal. It is free and it is real, and it is two
  orders of magnitude smaller than the framing suggested. Small enough to work by hand, too
  small to train a detector against on its own.
- **Both data gaps push the same way.** The county file starts in 2016 and the city is absent
  entirely, and both preferentially remove *old* arrays, which the v3 physics says are worth
  the most. Every era count in §3.5 is a floor, and it is labelled as one.
- **Added Option 8**, acquiring City of Santa Cruz permit records, and promoted it ahead of
  everything in §3.2 that does not already need to run. It is the only item with a multi-week
  external wait, it costs almost nothing, and it is the difference between segmenting 324
  sites and segmenting most of the AOI. Firing the records request is now the first action.
- **Retracted v1's permit counts.** "5,944 PV permit rows over 5,443 unique APNs, 3,975 (67%)
  pre-cutoff" is not reproducible: the file gives 8,780 / 7,399 unfiltered, 5,904 / 5,332 on
  the widest defensible PV filter, 5,558 / 5,154 on parsed mount, 2,810 / 2,645 on
  description text. The 67% share reproduces; the counts do not. Worse, it was a county-wide
  figure sitting in a document about the AOI. `permit_era_split.py` documents its filter and
  prints the sensitivity table so this cannot recur silently.
- **Did not change** any dollar figure, the physics, or Tier C.

### v5, 2026-08-12

Attacked weakest part 2 of v4: §1 and §3.2 still carried v1's recommendation while three
iterations of measurement had moved the ground under it. This iteration produced no new
measurement and changed the plan more than any iteration that did.

- **Reordered §3.2, which had the wrong thing first.** v1 put the imagery prevalence read
  first because everything else depends on it. That reasoning is now defeated three ways:
  imagery cannot grade severity (v2, sub-pixel), severity is the entire question (v2 and v3
  physics), and the permit segmentation covers 17% of the AOI (v4). The cheapest decisive
  experiment stopped being the first one and nobody had noticed.
- **Added Option 9, a ground-photo survey of band thickness, and put it first.** An
  afternoon, $0, and it measures f directly against the module frame. The argument that makes
  it cheap: **this is a question about the upper tail, not the mean.** A representative sample
  is not needed to answer "does f ever reach 0.2 here". An opportunistic max statistic does,
  and if the worst street-visible band is below f = 0.1 then Tier A is dead on physics and
  the project should say so and stop. It also yields Option 4's ground-truth validation set
  as a by-product, since every photographed roof can be matched to its own 6 cm chip.
- **Demoted Option 1 to second** and made it conditional. Two days spent counting how common
  a band is, before knowing whether any band is thick enough to matter, is the specific
  failure this ordering forecloses.
- **Put Option 8 at step zero.** Five minutes of effort against a 2 to 6 week external wait
  is pure loss to defer, and it blocks nothing.
- **Rewrote §1 against the non-technical gate.** It still described v1's plan and it now
  carries the actual recommendation, the 30 mm versus 78 mm pixel problem in plain words, and
  the stopping rule. That gate is one of the five and §1 is the only thing it grades, so
  leaving it stale meant the document was failing a gate it was not checking.
- **Measured nothing.** Recorded because the protocol prizes measurement and this iteration
  was pure coherence work. It was still the highest-value thing available: the document was
  recommending, in its most-read paragraph, a first step its own later sections had
  contradicted.

### v6, 2026-08-12, final desk iteration

Attacked weakest part 5 of v5, the last item on the queue that did not need a person: the
half-cut geometry was asserted from architecture rather than verified. It is now verified,
and it survived.

- **Corroborated the architecture against PVsyst's module documentation**, which independently
  states the three things the model asserts: two parallel sets of three half-cell strings with
  a shared bypass diode per pair, the lower-half shading case leaving the upper sub-module
  producing, and the benefit applying in portrait but not landscape. The landscape null was
  the easiest part to have got wrong and it matches.
- **Found and fixed a real circuit discrepancy, which turned out not to matter.** v3 gave
  every substring its own diode and paralleled the half-strings at the terminals; the
  documented circuit shares one diode across each parallel pair. v6 implements the documented
  one as the default. MLPE results are identical to three decimals throughout, and the single
  affected number is a one-module portrait string case moving 4.79% to 5.25%. Nothing in the
  document changed. `--diode-per-substring` keeps the old behaviour reproducible.
- **No figure moved, and that is the result.** Reported at the same length as a finding would
  have been, because a verification is only worth running if a null gets written down.
- **The agreement is qualitative and labelled as such.** Directly comparable published studies
  exist and are paywalled; none was read, and §2 says so rather than implying a numerical
  validation this does not have.

**Three cleanups on the way out**, found by re-reading the whole document rather than a
section: §7 still carried v1's retracted "3,975 of 5,944" as a **Measured** row three lines
below the row retracting it, now replaced with the re-derived 3,679 of 5,332; two em dashes
had crept in against the style rule; and **Option 9 was step one of the plan with no protocol
written** while Options 1 and 2 both had one, which is now §3.2b including its stop rule and
its PII handling.

**The loop stops here.** The remaining queue is four items and every one of them needs
something this desk cannot supply: an afternoon outdoors with a camera (Option 9, and it is
now step one of the plan), a records request to the City of Santa Cruz (Option 8), a second
human reader for the rubric pilot, or a full season of elapsed time for benefit duration.
Two consecutive iterations have now produced no change to any conclusion, and the protocol's
instruction in that situation is to say so and stop rather than pad. The document's own next
step is a physical one.

#### Known weakest parts of v6, and who has to do them

**Every item here needs a person, an external party, or elapsed time.** Six iterations have
exhausted what the data on disk can settle. Ordered by what unblocks the most:

1. **Option 9, the ground-photo survey. Cameron, one afternoon.** It is step one of the plan
   and it decides whether Tier A exists at all. Everything below is downstream of it, and a
   negative result ends the programme cleanly and publishably.
2. **Option 8, the City of Santa Cruz records request. Cameron, five minutes.** The 2 to 6
   week clock does not start until it is sent, and it blocks nothing in the meantime.
3. **The Option 1 rubric pilot. Needs a second reader, so it needs Akshitha.** v2 and v3 both
   added fields to a rubric nobody has tested for inter-reader agreement. Twenty chips, two
   readers, before committing two days.
4. **Benefit duration. One season, unavoidably.** The entire argument for the wash-only layer
   is that it does not come back in weeks. That is still assumed, it feeds the dollar bars,
   and only calendar time can settle it. Specific yield is the smaller sibling and could be
   pinned from a volunteer's inverter data during Option 2.

One desk item survives, and it is small: **the 57 to 66% join rate in the county-jurisdiction
prefixes is unexplained.** Those parcels are in the right file and still miss a third of the
time. Pre-2016 installs are the likely cause, but if it is instead a parsing failure in
`ingest_permits.py` then §3.5's counts are too low for a fixable reason. It does not gate
anything, which is why it is not above.

### v7, 2026-08-12

Verified v4's jurisdiction inference instead of trusting it. v4 read its prefix list off the
AOI join, which only sees parcels that have a detected array; v7 recomputed the county-permit
match rate over **every parcel in the parcel layer**, ordered by longitude. The inference was
right and the specifics were wrong in three ways.

- **The boundary is a clean line at longitude -122.00.** West of it, eight APN books hold
  9,246 parcels with **exactly zero** county solar permits. East of it, seven books permit at
  11.5 to 18.3%. Zero across 9,246 parcels is not attrition under any hypothesis, so the City
  of Santa Cruz reading is now verified rather than inferred.
- **v4 missed book 005** on the city side, so the city figure is **1,310 AOI sites, not
  1,288**, and eight books rather than seven.
- **v4 missed a second jurisdiction entirely.** Book 034 sits east of the line, immediately
  beside book 033 which permits at 18.3%, and returns zero across 887 parcels. That is
  **Capitola**. It is only 11 AOI sites, so it changes no conclusion, but v6 had explicitly
  cleared Capitola on weaker evidence and that was wrong.
- **Resolved the open question from v6's queue.** The unexplained 57 to 66% join rate inside
  county books is **194 AOI sites**, all of them detected arrays with no permit record, so
  pre-2016 installs or a parse failure. Bounded, small, and it gates nothing.
- **Sharpened Option 8's value**: it takes AOI permit coverage from 17% to roughly 88%, not
  "roughly quadruple". Added the note that the city runs residential PV through SolarAPP+, so
  part of this may already be public and should be checked before a records request is filed.

Nothing here touches the physics, the dollar chain, or Tier C.
