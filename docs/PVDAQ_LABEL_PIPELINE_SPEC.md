# Spec — per-array soiling labels from PVDAQ

**Written 2026-08-23, Cameron, as a handoff. Phase 0 executed the same day; results folded in
below.** Every number in §2 and §4 is measured against the live PVDAQ lake and reproducible
with the snippets in §9.

**Status: Phase 0 COMPLETE. 0a PASSES, 0b FAILS.** The bulk residential feed is *daily*
aggregates, not the 5-minute power this spec originally assumed, and the SRR approach works
on it anyway (12 of 12 systems, non-degenerate). Coordinates are neighborhood-accurate rather
than roof-accurate, so **Phase 5 is dropped**. Phase 1 is unblocked and much cheaper than the
original estimate.

---

## 1. The problem this solves, stated precisely

The Stage-2 risk model trains on `data/external/nrel_soiling_map_annual.csv`: **891 rows,
146 stations, 15 states**, where each row is one *station-year*. A station's IWSR is a
regional climatological measurement. Every rooftop in a station's catchment therefore shares
one label, by construction.

That is an **identifiability** problem, not a capacity problem. The training set contains
zero examples of two arrays under the same weather with different measured losses, so no
per-array feature has anything to fit against. This was confirmed experimentally: WorldCover
was wired in properly and within-AOI spread moved 0.78 -> 0.77 points with the sign
backwards (`docs/CRAIG_BRIEF_2026-08-19.md` §3). Adding more features to this label set is
not expected to work and has already been measured not working once.

PVDAQ changes the **label unit** from station to system. Two systems in the same half-degree
cell share weather, so any difference in their measured soiling loss is attributable to
array-level factors. That comparison does not exist in the current training set at all.

**What this does not do:** it does not by itself deliver within-city residential ranking for
Santa Cruz. It converts an unfixable identifiability problem into a tractable transfer
problem. Say it that way in any grant text; the stronger claim will not survive review.

**One thing discovered in Phase 0 that strengthens §1 considerably.** 881 of the 891 NREL
rows carry `station_id` of the form `5002_ac_power_inv_2012` with `measurement_type = "PV
System"`; only **10** rows are true soiling-station instruments. NREL's own map was built by
running this same class of method over inverter AC power. So the proposal is not "invent a
label"; it is "apply NREL's own method at a finer unit." Cite this when a reviewer asks why
we think the labels will be credible.

The flip side, and it must be stated: **Phase 2 is therefore a reproduction check, not an
independent validation.** See §5.

---

## 2. What is actually there (measured 2026-08-23)

Source: `https://oedi-data-lake.s3.amazonaws.com/pvdaq/csv/systems_20250729.csv`.
Anonymous S3, no API key, no account. 1,862 systems.

| cut | n | system-years |
|---|---|---|
| all systems | 1,862 | — |
| QA pass | 1,564 | — |
| QA pass, >= 2 years | 1,527 | 11,686 |
| **residential scale (<= 15 kW)** | **1,381** | **10,656** |
| residential **and Csb** (Santa Cruz's own Koppen class) | **278** | **2,226** |

Median 8.1 years per system on that last row. Compare against the 891 station-years the
model trains on today.

**Where the data actually lives, because the obvious guess is a trap.** Bulk per-system time
series are at:

```
pvdaq/csv/pvdata/system_id=<id>/year=<yyyy>/<id>_ac_<start>_<end>.csv
```

1,853 system partitions, covering **all 1,381 residential systems**. Do **not** use
`pvdaq/parquet/pvdata/`: it is a partial mirror holding only 157 systems, 7 of them
residential, **zero** Csb, and it does not contain system 10109. An hour was lost to this.

**Geographic clustering.** The original draft binned all 1,527 QA-pass systems. Phase 3 runs
on residential only, so these are the numbers that size the experiment:

| 0.5-deg cells with... | cells | systems held | share of 1,381 |
|---|---|---|---|
| >= 2 residential systems | 156 | 1,216 | 88% |
| >= 3 residential systems | 102 | 1,108 | 80% |
| **>= 5 residential systems** | **66** | **988** | **72%** |
| >= 10 residential systems | 28 | 761 | 55% |

Csb only: **12 cells hold >= 5** systems, containing 226 of the 278.
**170** coordinates carry more than one system, up to **21** at a single point. (The earlier
draft said 173; 170 is correct.)

**The fixed-effects design has something to fit.** Within cells holding >= 5 residential
systems, the median within-cell SD is **10.4 deg of tilt**, **47.2 deg of azimuth** and
**2.66 kW of capacity**, and **63 of 66** such cells have a tilt SD above 5 deg. Array
geometry genuinely varies inside a shared-weather cell. This is the precondition for Phase 3
and nobody had checked it.

**Coastal California is present.** 730 systems in CA (5,158 system-years); 209 inside a
34-39N / 123.5-121W box (1,653 system-years). System `10109` sits at ZIP 95066, Scotts
Valley, inside the AOI. Most coastal entries are PVoutput.org residential uploads at 2-9 kW.

**Tilt is already in the metadata** for 1,353 of the 1,527, along with azimuth (1,357),
capacity, Koppen class and PVCZ indices. Residential Csb tilt runs p10 1.0 / p50 18.8 /
p90 27.0 degrees, so the near-flat mounts that `src/risk/tilt_response.py` predicts are the
best cleaning candidates are represented.

**Undocumented asset found in Phase 0.** `pvdaq/csv/system_metadata/` carries, for **all
1,381 residential systems** (and all 278 Csb), both a `<id>_system_metadata.json` (module
make/model/quantity, inverter, mount tilt/azimuth, timezone, elevation) and a
`<id>_sat_image.jpg`. The JSON is richer array-level structure than the systems table alone.
The images are what settled 0b in an afternoon.

**Corpus size, because the systems table misreports it.** `dataset_size_mb` is inflated the
same way `number_records` is (§3). Measured from actual S3 object sizes:

| cut | measured | what `dataset_size_mb` claims |
|---|---|---|
| 1,236 two-channel residential | **0.16 GB** (132 KB/system) | 21.3 GB |
| 145 multi-channel residential | **40 GB** (276 MB/system) | 60.7 GB |

The residential bulk is a **0.16 GB** download. This is the single biggest change to the
effort estimate in §6.

---

## 3. THE ORIGINAL CENTRAL RISK — resolved, and it resolved differently than expected

**1,236 of the 1,381 residential systems expose only 2 sensor channels.** That much was
right, and the metadata confirms the channels are AC power and AC energy.

**What was wrong: the cadence.** This spec originally read `number_records` (~102,000 per
system-year) off the systems table and concluded "roughly 5-minute data." That field counts
NREL's *internal pre-aggregation* records. What is actually **published** for the 2-channel
bulk is a **daily aggregate**, four columns:

```
Unnamed: 0, ac_power_inv_<id>_daily_max, ac_power_inv_<id>_daily_mean, ac_energy_inv_<id>_daily_sum
2016-01-01, 4.881,                       1.085050,                     10.578
```

~365 rows/year. Sampled 9 two-channel residential systems across different ID ranges plus
system 10109; **all daily, 100%**. Verified again on all 12 systems in the 0a run.

**Do not quote `number_records` or `dataset_size_mb` as facts about the published data.**
Both describe NREL's internal store. This is the error that fired the original §4a stop rule.

**The fallback this spec named is weaker than it sounds.** The ~145 richer residential
systems (11-26 channels) do run at genuine 5-minute cadence, but scanning all 145 metadata
files:

- **Zero carry an irradiance channel.** The richness is inverter *electrical* channels (AC/DC
  current, voltage, frequency, inverter temperature), not weather instruments. Modeled
  irradiance is required either way.
- **Zero are Csb**, and only 1 of the 209 coastal-box systems is among them.
- They hold 703 system-years against the residential 10,656.

So falling back to them abandons the target climate entirely. **Do not treat "use the rich
systems" as a safe retreat.** It is a different, smaller project.

Consequence for method: irradiance comes from a **model** (Open-Meteo archive is what 0a
used; NSRDB PSM3 and PVGIS are alternatives), and `scripts/analyze/washtest_power.py`
measured what that costs, a residential roof normalised against satellite irradiance being
**1.5 to 2x worse** than PVDAQ 2107.

Two reasons this is still worth doing anyway, and both belong in the writeup:

1. **The estimand is different and much more favourable.** `washtest_power.py` measured the
   MDE of a *single before/after step*. SRR fits the whole seasonal sawtooth across many
   soiling intervals, so systematic irradiance bias largely cancels in the *trend* even when
   it corrupts the level. A 5% single-step MDE does not imply a 5% annual-loss MDE. **0a
   confirms this empirically** (§4).
2. **`soiling_srr` wants daily input anyway.** It documents its arguments as
   "insolation-weighted daily aggregates." Daily publication is the granularity the fitter
   consumes. What is genuinely lost is sub-daily clear-sky filtering and temperature
   correction resolution, which raises noise but does not remove the signal.

---

## 4. Phase 0 — RESULTS (executed 2026-08-23)

Tool: `scripts/analyze/pvdaq_daily_srr_probe.py`. Output:
`outputs/soiling/pvdaq_0a_csb12.json`.

### 0a. Can `soiling_srr` fit the daily feed? **YES — 12 of 12.**

Method: published daily energy divided by an **hourly** Open-Meteo irradiance model,
transposed to plane-of-array with pvlib (Perez), temperature-corrected per hour, then summed
to the day. Summing the model hourly and dividing the measured daily total by it produces a
genuinely insolation-weighted daily PI, which is what `soiling_srr` asks for. `recenter=True`
absorbs the crude system model's offset, so omitted derates do not need to be right, only
time-invariant.

12 residential Csb systems (10109 plus 11 sampled at random), 4.7 to 9.5 years each.
**Irradiance was fetched at each system's own coordinate** (`--irradiance-at system`); the
tool now defaults to `cell` for the reason in §5, so pass the flag to reproduce these exact
numbers:

```
non-degenerate perfect_clean fits from daily energy:  12/12 (100%)
spec threshold was >= 30%                          -> PROCEED
perfect_clean annual loss:  median 1.98 pts   SD 2.03   range [0.10, 8.00]
per-label 95% CI width:     median 0.72 pts
valid soiling intervals:    median 20 of 162
```

**The number that matters for Phase 3.** After QC (n=11), between-system SD is **2.03 pts**
against a median per-label CI width of **0.72 pts**, a **spread-to-noise ratio of 2.8x**.
The labels are precise enough to tell systems apart. That is the precondition the whole
proposal rests on, and it is now measured rather than assumed.

**Three caveats, all of which must travel with that 2.8x.**

1. **The bootstrap CI understates total uncertainty.** Running the same systems through a
   `power_max`-based PI instead of the energy PI moves the answer by a median of **1.58 pts**
   with a cross-method correlation of only **0.50**. Method choice is nearly as large as the
   between-system spread. The honest framing: 2.8x is *within-method* precision; fold in
   method sensitivity and the margin is closer to 1.3x. Use the energy route (it is the
   principled one) and report the sensitivity.
2. **These 12 span different metros, so part of that spread is climate, not array.** Only 4
   cells held 2 systems each. Of the 3 usable within-cell pairs, 1 to 2 showed a difference
   exceeding the combined label noise. Suggestive, badly underpowered, and exactly what
   step 5 in §6 is for.
3. **A low valid-interval rate is the real cost of daily data.** Median 20 valid of 162.
   rdtools warns above 20% invalid, which these trip. `perfect_clean` is robust to it;
   `half_norm_clean` is not, and its estimates here are correspondingly wild (up to 22.6 pts).
   **Use `perfect_clean`**, which is also NREL's convention. This is a real agreement between
   the statistical requirement and the comparability requirement.

**QC rule learned the hard way.** System 10912 returned SR 0.99899 (0.10 pts) off just **2**
valid intervals with a PI median of 1.10, a model-mismatch artefact rather than a clean
array. Filter Phase 1 output on `n_valid_intervals >= 5`, **not** on the soiling ratio.
`MIN_VALID_INTERVALS` in the probe now encodes this.

### 0b. Are the coordinates roof-accurate? **NO. Phase 5 is dropped.**

1,322 of 1,527 give `site_location` as `ZIP: <5 digits>`, but with 1,062 distinct coordinates
among them, so they are jittered rather than snapped to a centroid. That much was right, and
it left the question open.

Settled using the `_sat_image.jpg` assets (§2), which are centered on the stored coordinate,
for 16 residential Csb systems. **In 4 of 16 the coordinate lands on something that is not a
building at all**: closed conifer canopy (10700), chaparral hillside (10707), a school
ballfield (10924), a bare dirt lot (11939). In the remaining 12 it lands in the correct
residential neighborhood but not on an identifiable array.

Per this spec's own decision rule: **keep the labels and PVDAQ's own tilt/azimuth/capacity,
drop Phase 5.** A geo join on these coordinates would produce confident garbage.

**A confounder this spec never raised, now visible.** Several Csb sites sit under heavy tree
canopy, 10109 included. Canopy shading will move a residential production series far more
than soiling does, and it is not in any feature we currently carry. See §5, Phase 3.

---

## 5. Phases 1-4

**Phase 1 — label extraction.**
Per system-year: fetch the published daily CSVs, join modeled irradiance, model cell
temperature with pvlib, normalise, run `soiling_srr`, emit annual soiling loss %.

Not optional:
- **Match NREL's convention.** `perfect_clean`, not the `half_norm_clean` default. 0a gives
  this a second, independent reason (§4, caveat 3).
- **Emit the same target.** `(1 - iwsr) * 100`, matching `src/risk/loss_model.py`.
- **Filter on `n_valid_intervals >= 5`.** See the 10912 case.
- **Fetch irradiance per 0.5-degree CELL, not per system** (`--irradiance-at cell`, the
  default). Open-Meteo quota is the binding constraint on this phase, not compute or storage,
  and this cuts ~1,381 fetches to **321** distinct residential cells.

  It is also the methodologically correct input for Phase 3, and not only the cheap one.
  Every system in a cluster then receives *identical* irradiance, so a within-cluster
  difference cannot be an artefact of two systems carrying different irradiance-model error.
  That is exactly the confound the fixed-effects design exists to remove.

  **But it is a real choice with a measured cost.** On system 10109, which sits 6.4 km from
  its cell centre, moving from system to cell coordinates shifts the label **1.80 -> 2.10
  pts**. That is inside the 0.72-pt per-label CI but it is systematic, not noise.
  **Use `--irradiance-at system` for Phase 2**, where the question is absolute agreement with
  a nearby NREL station rather than a within-cluster contrast. The flag exists so the two
  phases do not silently share the wrong default.

Store as a parquet keyed by `(system_id, year)` with fit diagnostics attached, so bad fits
can be filtered downstream rather than silently trusted.

**Phase 2 — RESULT: PASSES (measured 2026-08-26).** Our labels reproduce NREL's level on
shared ground. 24 residential systems within 5 km of an NREL station, each with >= 3 years of
year-overlap, run with `--irradiance-at system`
(`outputs/soiling/pvdaq_phase2_nearstation.json`, analysed by
`scripts/analyze/pvdaq_phase2_vs_nrel.py`):

```
24/24 fits pass QC.   PVDAQ p50 2.54 pts  |  NREL p50 2.70 pts
paired diff (PVDAQ - NREL): mean +0.19   95% CI [-0.66, +0.99]   Wilcoxon p 0.46
Spearman rank corr +0.60
Csb subset (n=12):  PVDAQ p50 2.91  |  NREL p50 4.00
```

The CI straddles zero, so **there is no detectable bias** and the earlier worry that
`recenter=True` might bias us low against NREL's convention is not supported.

**But the same run delivers a much more important negative, and it changes the Phase 3 power
estimate.** The paired PVDAQ-vs-NREL disagreement has **SD 2.14 pts**, which is **3.4x the
median bootstrap CI width of 0.63 pts.** The bootstrap interval badly understates true label
uncertainty. Part of that gap is genuine spatial variation (systems sit 0.3-3.7 km from their
station), so 2.14 is an upper bound on method noise and 0.63 a lower bound, but the true
figure is nowhere near 0.63.

Consequence for Phase 3, stated as a bracket rather than a point:

| per-label noise used | spread-to-noise | reading |
|---|---|---|
| bootstrap CI 0.72 pts | **2.8x** | optimistic; Phase 3 has signal |
| PVDAQ-vs-NREL SD 2.14 pts | **0.9x** | pessimistic; Phase 3 is underpowered |

**The bracket contains 1.0.** Do not quote 2.8x on its own. The within-cluster run is what
narrows this.

**Phase 2 — how it was framed, and the two caveats that stand.**
PVDAQ systems near NREL stations should reproduce that station's IWSR. Geography supports
it: **141 residential systems sit within 10 km** of an NREL station, 461 within 25 km, and
for Csb the median distance to the nearest station is **20 km**.

**Two corrections to how this phase was framed.** First, since 881 of 891 NREL rows were
themselves produced by this method (§1), agreement is a **reproduction check on our
implementation**, not independent validation. Do not claim otherwise in the paper. Second,
there is **no gold-standard end-to-end reproduction available**: the 140 systems behind
NREL's labels use a 5xxx `system_id` space and **none** of them are present in the current
PVDAQ publication. We cannot re-derive a known answer from raw data. Both facts are findings
worth stating, not problems to hide.

**Phase 3 — POWER CHECK RESULT (measured 2026-08-26). Viability is UNRESOLVED.**
75 residential systems sampled 8-per-cell across 10 Csb cells, `--irradiance-at cell`
(`outputs/soiling/pvdaq_0a_withincell75.json`, analysed by
`scripts/analyze/pvdaq_within_cluster.py`). 66 of 75 pass QC.

```
9 cells with >= 3 QC-passing systems
within-cell between-system SD, median   1.41 pts   (vs 2.03 across metros -> ~30% of the
                                                    earlier spread was climate, which the
                                                    fixed effects correctly absorb)
per-label 95% CI width, median          0.70 pts
within-cell share of total variance       67%      (available to array-level features)
```

**The bracket, and it contains 1.0:**

| per-label noise used | spread-to-noise | reading |
|---|---|---|
| bootstrap CI, 0.70 pts (within-method) | **2.55x** | Phase 3 has signal |
| Phase 2 PVDAQ-vs-NREL SD, 2.14 pts | **0.66x** | Phase 3 is underpowered |

Per-cell ratios run **0.48x to 3.94x**, so the cells disagree with each other as much as the
two noise estimates do. **Do not quote 2.55x alone in a proposal.** The honest statement is
that Phase 3's viability turns on which noise estimate is right, and that is not yet settled.

**What would settle it:** separate method noise from real spatial variation by repeat-fitting
the *same* system under perturbed irradiance sources (Open-Meteo vs NSRDB PSM3 vs PVGIS). The
spread across sources for one system is pure method noise with no spatial component. That is a
day of work and it converts the bracket into a number.

**Phase 3 — the design, unchanged.**
Within each 0.5-degree cluster, regress soiling loss on array-level features (tilt, azimuth,
capacity, mount) **with cluster fixed effects**. The fixed effects absorb climate entirely,
so surviving coefficients are the per-array signal. §2 confirms the within-cell geometry
variation needed to identify them.

Pre-register the stop rule: if within-cluster array-level features explain essentially
nothing with a tight CI, the per-roof thesis is dead, and that negative result is the paper.

**Add canopy shading as a covariate.** Per 0b, tree cover is a live confounder in exactly the
target climate, and we now have a satellite image for every residential system. Score canopy
fraction from those images and include it, or the tilt/azimuth coefficients will absorb
shading and be read as soiling. This is the cheapest available salvage of the dropped
Phase 5 and is worth more here than a roof-area join would have been.

**Phase 4 — integrate.**
Pool NREL + PVDAQ with sensible weighting and re-run the existing gates
(`scripts/predict/holdout_ci.py`, spatial CV).

**Trap:** grouped CV must group by **cluster**, not by station or by system. 170 coordinates
carry more than one system, one of them 21. Splitting those across folds leaks badly and
will produce a beautiful AUC that means nothing.

**~~Phase 5 — imagery join~~ — DROPPED.** 0b failed; see §4.

---

## 6. Effort (revised after Phase 0)

| phase | original | revised | why |
|---|---|---|---|
| 0 — feasibility | 0.5 day | **done** | executed 2026-08-23 |
| 1 — extraction | 1.5 to 3 weeks | **2 to 3 days** | corpus is 0.16 GB not 44; fit is ~25 s/system for one method; extractor already written |
| 2 — validation | 3 to 5 days | 3 to 5 days | unchanged |
| 3 — the experiment | 1 week | 1 to 1.5 weeks | canopy covariate added |
| 4 — integrate + re-gate | 1 week | 1 week | unchanged |
| ~~5 — imagery join~~ | 2 weeks | **dropped** | 0b failed |

**Phase 1 is quota-bound, not compute-bound.** ~1,236 systems at ~25 s/system single-threaded
is under 9 hours and parallelises. The constraint is Open-Meteo's free tier. Per-cell caching
(321 fetches) keeps this inside it; per-system fetching would not.

**Before Phase 1 at full scale, do the power check** (~60-100 systems chosen to fill cells
that already hold >= 5 residential systems). It converts the n=12 spread-to-noise result into
a real within-cluster estimate, and it is what determines whether Phase 3 can resolve anything.
That is the single highest-value next measurement.

---

## 7. What NOT to do

- Do not add features to the current risk model first. Measured, does not work, §1.
- Do not read cadence or corpus size off `number_records` / `dataset_size_mb`. §3.
- Do not use `pvdaq/parquet/pvdata/`. Partial mirror, 157 systems. §2.
- Do not use `soiling_srr` defaults. `perfect_clean`, §5.
- Do not accept a fit on `n_valid_intervals < 5`. §4.
- Do not treat the 145 multi-channel systems as a safe fallback. No irradiance, no Csb. §3.
- Do not attempt a geo/imagery join on PVDAQ coordinates. §4, 0b.
- Do not describe Phase 2 as independent validation. §5.
- Do not use 2025 PVDAQ 2107 data as a reference; it has known timezone and cadence defects
  (`docs/AOI_CLEANING_TARGETING_PLAN.md`).

---

## 8. Prior art in this repo

- `scripts/analyze/pvdaq_daily_srr_probe.py` — **the Phase 0 tool and the Phase 1 skeleton.**
  Daily-feed loader, per-cell irradiance cache, PI construction, both SRR branches,
  degeneracy QC.
- `scripts/analyze/washable_share_probe.py` — `soiling_srr` + CODS against PVDAQ 2107 over
  anonymous S3. Note it reads a *pyranometer* and the 2023-solar-data-prize path, so it does
  not transfer directly to the bulk; the convention handling is what to copy.
- `scripts/analyze/washtest_power.py` — the MDE arithmetic and the autocorrelation result.
- `src/risk/loss_model.py` — the target definition and the quantile/CQR head to retrain.
- `src/risk/roof_geometry.py` — 3DEP EPT access, and the mercator trap.

---

## 9. Reproduce

**Scoping numbers (§2):**

```python
import pandas as pd
u = "https://oedi-data-lake.s3.amazonaws.com/pvdaq/csv/systems_20250729.csv"
d = pd.read_csv(u)
for c in ["years", "dc_capacity_kW", "tilt", "latitude", "longitude",
          "available_sensor_channels", "number_records"]:
    d[c] = pd.to_numeric(d[c], errors="coerce")
ok = d[(d.qa_status.str.lower() == "pass") & (d.years >= 2)]
res = ok[ok.dc_capacity_kW <= 15]
print(len(ok), ok.years.sum(), len(res), res.years.sum())
print(res.available_sensor_channels.value_counts().head())

# Phase 3 sizing runs on RESIDENTIAL, not all QA-pass systems.
cell = (res.latitude / .5).round().astype(int).astype(str) + "_" + \
       (res.longitude / .5).round().astype(int).astype(str)
cc = res.groupby(cell).size()
print((cc >= 5).sum(), cc[cc >= 5].sum())          # -> 66, 988
```

**The cadence finding (§3) — the one that mattered:**

```python
import pandas as pd, requests, xml.etree.ElementTree as ET
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
B = "https://oedi-data-lake.s3.amazonaws.com"
r = requests.get(f"{B}/", params={"list-type": "2",
                                  "prefix": "pvdaq/csv/pvdata/system_id=10109/"})
k = [e.find("s3:Key", NS).text for e in ET.fromstring(r.content).findall("s3:Contents", NS)][0]
print(pd.read_csv(f"{B}/{k}").head())   # daily, 4 columns
```

**Phase 0a (§4):**

```bash
# --irradiance-at system reproduces the recorded run; `cell` is now the default
# and gives systematically different (not wrong) labels. See §5, Phase 1.
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_daily_srr_probe.py \
    --system 10109 10112 10341 10450 10477 10912 10979 11186 11656 11758 11881 11932 \
    --irradiance-at system \
    --out-json outputs/soiling/pvdaq_0a_csb12.json
```
