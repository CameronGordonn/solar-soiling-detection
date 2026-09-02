# Handoff to Josh — measured roof geometry from 3DEP lidar

**2026-08-19, Cameron.** Everything below is measured and reproducible in-repo. Written for
the paper; take the numbers as-is and quote the caveats with them, they are load-bearing.

---

## 1. What this is, in three sentences

We fitted a plane to USGS 3DEP lidar returns inside each detected array polygon and recovered
per-array **tilt** and **downslope azimuth** for 3,068 of 3,362 arrays (91.3%). This converts
to a clear-sky plane-of-array (POA) irradiance multiplier, which is the first per-roof
physical quantity in the pipeline that actually varies within a site. It closes an assumption
`src/risk/economics.py` had carried as explicitly unmeasured, and the measured value lands
within 3% of what that comment guessed.

**It is a physics input, not a model feature.** §5 explains why that distinction matters and
why we deliberately did not add it to the risk model.

---

## 2. Data and method

**Source.** USGS 3DEP public Entwine Point Tile store, `CA_SantaCruzCounty_2020`
(`s3://usgs-lidar-public/`). 41.8 billion points, ~18 pts/m², classified, EPSG:3857. Free,
no key, no account.

Worth stating in the paper: **the headline 3DEP product is a bare-earth DEM and is useless
for this** by construction — roofs are removed from it. The work has to go to the point cloud.

**Access.** Plain HTTP against the EPT octree plus `laspy`; no PDAL. The octree walk is ~40
lines and avoids a C++/PROJ dependency. `src/risk/roof_geometry.py`.

**Fit.** Robust least-squares `z = ax + by + c` inside the polygon shrunk by 15%, first
returns only, with 2.5σ trimming over 3 iterations. Tilt is the normal's angle off vertical;
azimuth is the downslope bearing clockwise from north. A fit is **rejected** if RMS residual
> 0.25 m, inliers < 60%, or fewer than 12 points.

**The 15% shrink** is the same trick as the SAM2 prompt box: the polygons come from 2025
imagery and the cloud from a 2020 flight, so a sub-metre georegistration offset would
otherwise pull in eave, gutter or the neighbouring roof plane.

### The methodological trap — worth a paragraph in the paper

EPT X/Y are EPSG:3857 metres; Z is true metres. At Santa Cruz's latitude the mercator scale
factor is 1/cos(36.97°) = **1.2515**, so a plane fitted on raw coordinates reports tilt
**18.9% too shallow** — silently, on every roof, producing an entirely plausible-looking
distribution. `fit_plane()` de-inflates X/Y by cos(lat); `tests/test_roof_geometry.py` pins
the magnitude so the correction cannot be removed unnoticed. Anyone reproducing this on web-
mercator lidar will hit it.

---

## 3. Results

**Coverage:** 3,068 / 3,362 arrays (91.3%). The 8.7% that fail are ridge-straddling and
hip-roof mounts, rejected by the RMS threshold. **They are reported as null, never
median-filled** — see §5.

**Tilt:** p10 6.4°, p50 19.0°, p90 30.8°. **8.4%** of arrays sit below 5° (corrected 2026-08-30; the earlier 6.4% duplicated the p10 value — measured on `fit_ok` it is 8.38%, and 8.0% unfiltered). The distribution
lands on standard roof pitches (4:12 = 18.4°, 6:12 = 26.6°), which is a sanity signal, not
evidence.

**Azimuth:** modal south, with substantial east and west tails (S 123, SE 85, SW 67, E 63,
W 52 of a 450-array sample).

**POA multiplier** relative to a south-facing 20° roof, the orientation the dollar chain
implicitly assumed:

| | p10 | p50 | p90 | p10→p90 ratio |
|---|---|---|---|---|
| `poa_rel` | 0.772 | 0.918 | 1.004 | **1.301** |

A 500-array probe and the full 3,362-array run agree to three decimal places on all three
quantiles. That stability is worth one sentence.

### Lead with these three (Cameron's call, 2026-08-19)

Josh — of everything below, these are the three worth building the paper around, in order:

1. **The two-method tilt validation** (immediately below). Two datasets with nothing in
   common agreeing to 0.0° on the median is the credibility anchor for the whole method.
2. **The 2.6× correction that did not move the answer** (§5 of `CRAIG_BRIEF_2026-08-19.md`).
   The dollar chain took its largest-ever upward revision — value of a lost kWh $0.1646 →
   $0.4284, AOI annual loss +156% — and the conclusion held at 0 of 1,865 sites. A negative
   result that survives its own strongest counter-correction is worth far more than one that
   was never stress-tested.
3. **The tilt-as-a-feature negative result** (§5 below). We measured a physical quantity,
   had every reason to add it to the model, and did not, for a stated structural reason.

### Result 0 — two independent methods, same median tilt

CaliforniaDGStats publishes installer-reported tilt from PG&E interconnection paperwork for
the same county. Nothing is shared with our method — different instrument, different decade,
different failure modes:

| source | n | p50 tilt |
|---|---|---|
| PG&E interconnection paperwork (CaliforniaDGStats) | 8,547 | **19.0°** |
| our 3DEP plane fits (`fit_ok` subset) | 3,068 | **19.0°** |

Our side reproduces exactly from `outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv` filtered to
`fit_ok`: n=3,068, p10 6.4°, p50 19.0°, p90 30.8°. (Note the *unfiltered* file gives n=3,330
and p50 19.6° — always state the filter, the two differ by 0.6°.)

✅ **Provenance gap CLOSED, 2026-08-30.** The DGStats side is now cached and reproducible:
`scripts/analyze/fetch_dgstats_tilt.py` pulls the Interconnected Project Sites Data Set,
filters it, and writes `data/external/dgstats/santa_cruz_interconnected_pv.csv` plus a
`provenance.json` carrying the source URL, the file list, the filter and every intermediate
count. `/data/` is gitignored, so both files are **force-added**; a `git rm` of them silently
un-reproduces the anchor.

**The filter, written down:** `Service County` contains SANTA CRUZ, `Technology Type` contains
Photovoltaic, `Customer Sector` = Residential, `Tilt` > 0, de-duplicated on `Application Id`.
No date filter (the extract spans approvals 1998-09-29 to 2026-05-29).

**The 0° question the old note flagged is answered, and it does move the median.** `Tilt` uses
**0.0 as "not reported"**, not as "flat array": 3,827 of 12,400 reported values (30.9%) are
exactly 0.0, and requiring `Mounting Method = Rooftop` drops that share to 1.7% — the zeros
are concentrated in rows where the mounting field was also left blank. Keeping them flattens
the median from **19.0° to 18.0°**. They are dropped. `Mounting Method` is *not* the primary
filter because it is itself null on ~30% of rows; it is reported as a robustness check, and
all five variants tried land on 19.0°.

**Re-measured against this cache:**

| source | n | p10 | p50 | p90 |
|---|---:|---:|---:|---:|
| PG&E interconnection paperwork (DGStats, 2026-07 release) | **8,573** | 14.0° | **19.0°** | 27.0° |
| our 3DEP plane fits (`fit_ok`) | 3,068 | 6.4° | **19.0°** | 30.8° |

Median difference **0.03°**. The n is 8,573 against the 8,547 quoted on 2026-08-19 — a 0.3%
drift from a newer release of the data set (this one is dated 2026-07-17 and runs through
May 2026), not from a change of filter. **Quote 8,573 and the script, not the 8,547.**

⚠️ **The medians agree; the tails do not, and the cause is this filter.** DGStats puts 0.78%
of arrays below 5° where the lidar puts **8.38%** (below 10°: 3.38% vs 14.67%). A genuinely
flat array is exactly the row an installer reports as 0.0, which the filter cannot separate
from "not reported" and therefore drops — so the paperwork's low tail is biased high *by
construction* and the lidar's is not. Do not explain this away as noise. Sub-5° arrays are
the population Mejía & Kleissl (2013) measured soiling ~5× faster, and the permit record
**structurally cannot see them**. Quote the medians as the agreement and the low tail as a
capability the lidar has and the paperwork does not.

One more limit that survives: the two samples are not the same arrays — DGStats is county-wide
permits, the lidar is the 15 km² imaged AOI — so this is agreement on central tendency, and no
paired test is available without an address join.

### Result 1 — orientation is ~5x the per-home differentiator the soiling model is

Log-variance decomposition across the 1,710 residential-scale sites (2–15 kW):

| driver | p10 → p90 | ratio | share of per-home dollar variance |
|---|---|---|---|
| System size (kW), from detection | 2.89 → 9.26 | 3.20x | 86.7% |
| **Roof orientation (POA)** | **0.77 → 1.00** | **1.30x** | **11.1%** |
| Soiling loss %, the XGBoost head | 5.07 → 5.83 | 1.15x | 2.2% |

Caveat to state: `poa_rel` is per-array from the fitted subset, while size and loss are
per-site, so 11.1% is an estimate rather than a like-for-like decomposition. Multi-array
sites will average slightly, pulling it down a little.

### Result 2 — it closes a documented open assumption, and confirms the guess

`src/risk/economics.py:87-93` states the residual explicitly: *"this treats plane-of-array
irradiance as equal to GHI... a mixed residential fleet lands near 1.0 — but no one has
measured the orientation mix for this AOI."*

Measured fleet mean **POA/GHI = 0.894 × 1.1507 = 1.029**. The guess was right to within
2.9%. This is a nice result to report honestly: the assumption was sound, and we can now say
so instead of flagging it.

Consequence for the dollar chain: the flat `BASE_SUN = 5.5` overstates the **typical** roof
by 9.1% (median `poa_rel` 0.918) even though it is nearly unbiased fleet-wide. Direction
matters — south-20° is near-optimal, so real orientation can almost only revise a site
**downward**. **This hardens the zero-of-1,865 result rather than rescuing it.**

### Result 3 — it is racking angle for pre-2020 installs, roof pitch for later ones

**This result was re-measured on 2026-08-19 and the earlier n=14 version was wrong. Do not
quote the n=14 numbers (median +0.219 m, "100% of arrays proud"); they came from a biased
subset.** The old test demanded a *clean* planar ring around each polygon, which selected
for arrays that stand proud. Widening it (fixed-width metric annulus, harder trimming,
looser planarity gate) took n from 14 to **533** and moved the population median from
+0.219 m to **+0.055 m**, with only 51% standing more than 5 cm proud.

Taken alone that looks like a retreat. Conditioned on install date it is a much stronger
result. The 3DEP flight is 2020, so an array installed **before** it was itself scanned and
one installed **after** it was not — the permit dates
(`scripts/analyze/join_permit_vintage.py`) split the population on a variable that has
nothing to do with lidar, and the standoff step appears exactly where physics says:

| install era | n | median standoff | p25 | p75 | >10 cm |
|---|---|---|---|---|---|
| **pre-flight** (laser saw modules) | 37 | **+0.145 m** | +0.082 | +0.262 | **65%** |
| ambiguous (2020) | 25 | +0.010 m | +0.001 | +0.023 | 12% |
| **post-flight** (laser saw bare roof) | 54 | **+0.013 m** | −0.015 | +0.076 | **17%** |

```
pre − post median difference  +0.132 m
bootstrap 95% CI              [+0.084, +0.208]
Mann-Whitney one-sided        p < 0.0001
```

+0.145 m is a mounting rail. +0.013 m is nothing. **So `roof_planes.csv` reports true
racking angle where the array predates the flight and roof pitch where it does not**, which
is what the physics predicted before the permit join existed. This is the cleanest natural
experiment in the handoff — the splitting variable is independent of the measurement.

Consistent with it, 32.6% of arrays (n=331) differ from their underlying roof plane by more
than 5°, i.e. are non-flush or tilt-racked, where pitch would be the wrong answer.

**Practical consequence:** you do not need the permit to know which you have. Standoff is
measurable per array, so an array with standoff above ~0.08 m is being measured at its
module plane regardless of whether we can date it. That matters because 83% of sites have no
permit record at all.

**Remaining caveat:** 533 of 3,362 (15.9%) yield a usable standoff. The binding filter is
"ring not planar" (2,790 arrays), because most polygons fill their roof face and the annulus
spills onto the next plane. That is a coverage limit, not a bias in the surviving estimate —
but it is a limit, and the era-split n's (37 and 54) are small.

### Result 4 — internal consistency

Of 15 array pairs sharing a roof: **40% co-planar** (median tilt agreement 0.5°) and **40%
exact gable mirrors** (Δazimuth > 155°). Chance would put ~14% in each bucket. This is the
strongest evidence that the fit measures real geometry rather than noise, and it is
independent of the synthetic validation.

---

## 4. Validation summary

| check | result |
|---|---|
| synthetic planes, known tilt/azimuth | recovered to <0.2° and <1° |
| standoff step, pre- vs post-flight installs | +0.132 m, 95% CI [+0.084, +0.208], p<0.0001 |
| mercator correction disabled | tilt understated 18.9%, as predicted |
| median RMS residual on real arrays | **0.040 m** over ~270 points/array |
| same-roof pairs co-planar or mirrored | 80% (vs ~28% by chance) |
| 500-array probe vs full 3,362 run | quantiles agree to 3 dp |
| unit tests | 9 pass; full suite 218 pass, 1 skip |

---

## 5. Why we did NOT add tilt to the risk model

This is worth a paragraph in the paper because it is a clean, measured example of a feature
that looks obviously useful and is not.

The intuitive objection is that tilt barely varies across a small AOI. **That objection is
wrong here** — AOI/training SD ratio is **1.382**, so tilt varies *more* across our AOI than
across NREL's training set. The real reasons are three, all measured:

1. **Support barely overlaps.** The training interquartile range is [7.49°, 12.00°] and
   contains **8.9%** of our roofs. Our median roof, 19.3°, sits at the **90.5th percentile**
   of training.
2. **Gradient-boosted trees do not extrapolate.** The deepest tilt split in the model is at
   26.0°; predictions at 30°, 35° and 40° are *identical*. Partial-dependence spread across
   15–40°, where 73% of our roofs live, is **0.007**; across 0–15°, where 91% of training
   lives, it is 0.023. The signal sits precisely where our roofs are not.
3. **The training feature is a station identifier, not physics.** 42.4% of training rows sit
   at exactly 7.49° (an imputed fill, not a pitch anyone builds), only 24 unique values
   across 1,000 rows, **99% of stations have exactly one tilt value**, and tilt correlates
   0.372 with longitude. Accordingly the learned effect has the **wrong sign** — steeper
   roofs predict *more* soiling, where physics says steep panels shed water and dust better.

This is the same failure as `worldcover_tree` (§ `docs/CRAIG_BRIEF_2026-08-19.md`): same
feature name, different referent. Reproduce all three with the partial-dependence and
support-overlap checks described in §7.

**Where tilt would legitimately enter the model:** `src/risk/physics_score.py` hardcodes
`heavy_rain_mm = 10.0` for every array. A 30° roof self-cleans on less rain than a 5° one,
and SOMOSclean's rain-reset term is the natural home for that. Imposing the relationship from
physics sidesteps all three problems above. **Not done — flag as future work.**

---

## 6. What shipped

- `src/risk/roof_geometry.py` — EPT reader + plane fit
- `scripts/analyze/fit_roof_planes.py` — runner + POA interpolator
- `tests/test_roof_geometry.py` — 9 tests, incl. the mercator guard
- `scripts/product/build_dashboard_data.py` — payload join
- Dashboard now shows e.g. *"SSW-facing, 19° tilt · catches ~8% less sun than ideal"*,
  hidden where the fit failed. **This is the first individualised, independently verifiable
  fact the product has shown a homeowner** — they can check the compass direction from their
  own driveway. Relevant to the "sell the diagnosis, not the cleaning" framing.

**Wired 2026-08-19.** `economics.py` now takes per-roof sun-hours via
`sun_hours_from_poa_rel()`, area-weighted across each site's polygons, with sites lacking a
usable fit keeping flat `BASE_SUN` rather than a median fill. 1,763 of 1,865 sites (94.5%)
carry measured orientation.

**Measured effect on the dollar chain.** Per-site `annual_loss_usd` ratio p10 0.931 / median
1.056 / p90 1.152; AOI total **+3.0%** (predicted +2.9% from the fleet mean). Note the sign
carefully, because it is easy to state backwards: against the *south-20 reference* most
roofs score low (median `poa_rel` 0.918), but against the *flat `BASE_SUN`* it replaces
**69% of sites go up**, since `BASE_SUN` is a GHI figure and any tilted roof beats the
horizontal.

**The conclusion is unchanged: 0 of 1,865 sites worth cleaning before, 0 after.** Worth
saying explicitly in the paper — the negative result survives its own largest correction.

Also new: `scripts/analyze/join_permit_vintage.py` assigns tariff vintage (NEM 1.0 / 2.0 /
NBT) and the lidar-era flag. Current coverage is poor and that is itself a finding: 341 of
1,865 sites (18.3%) match a county permit and **81.7% are unknown** — but the cause was
tested rather than assumed, and it is **jurisdiction, not age**. By APN book: city books
001–011 hold **1,310 sites with 0.0% county-permit coverage**, county books 027–033 hold 546
sites with **62.5%**. Twenty city-book parcels queried live against the County's public
portal returned valid pages with empty permit tables while county-book controls returned 2–8
permits each. So the County simply never permitted those homes and no county archive will
ever cover them; the City records request
(`docs/outreach/CPRA_CITY_OF_SANTA_CRUZ_SOLAR_PERMITS.md`) is the only route to 70% of the
AOI. Worth a sentence in the paper as a reproducibility caveat: **permit-derived tariff
vintage is structurally unavailable for the majority of this AOI**, and any study joining
detections to permit records needs to check jurisdiction before reporting coverage.

Bonus sanity check from that join: permit kW vs area-estimated kW over 130 matched sites has
**median ratio 1.01** (p10 0.40, p90 1.52). The detector's area→kW conversion is unbiased at
the median.

---

## 7. Reproduce

```bash
# full run (~20 min, ~2 GB cache)
PYTHONPATH=. python3 scripts/analyze/fit_roof_planes.py \
    --arrays outputs/aoi/santa-cruz-w2-21cm/arrays.geojson \
    --out    outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv

PYTHONPATH=. python3 -m pytest tests/test_roof_geometry.py -q
PYTHONPATH=. python3 scripts/product/build_dashboard_data.py --out-dir /tmp/preview
```

Commits: `5e39e1b` (measurement), `2b068b8` (dashboard payload), branch
`roof-tilt-from-3dep`. BBF-Website: `efbbfa1`, branch `fix/system-derate-sync`, **not
pushed**.

---

## 8. Two things I would not overclaim

1. **The era-split n's are small** (37 pre-flight, 54 post-flight) even though the pooled
   standoff sample is 533, and only 15.9% of arrays yield a usable standoff at all. The
   effect is large and the p-value tiny, but do not describe the coverage as good.
2. **The 11.1% variance share** mixes per-array and per-site quantities (§3, Result 1).
3. **The NEM cliff dates** (`NEM1_CLOSE`, `NBT_START`) are encoded from memory and flagged
   in-code as needing verification against the CPUC decision before publication.

Everything else in here I would defend as-is.
