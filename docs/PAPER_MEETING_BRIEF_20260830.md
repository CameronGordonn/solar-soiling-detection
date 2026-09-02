# White paper — meeting brief, 2026-08-30

**Cameron, for the Sunday call with Josh.** Supplements
[`PAPER_REWRITE_BRIEF_20260823.md`](PAPER_REWRITE_BRIEF_20260823.md), which is still correct;
this file covers (a) what Josh's readability-pass draft already fixed, and (b) the seven results
that moved between 2026-08-23 and today. Shareable version:
https://claude.ai/code/artifact/c1c7e318-9292-4a44-8289-ec325b945910

---

## 1. Josh's new draft already fixed half the kill list

The attached PDF is not the 2026-08-05 draft. The readability pass Craig and Akshitha asked for
silently cleared six of the twelve kill items. Say that out loud so it gets credit and nobody
re-litigates settled ground.

| # | Claim in the draft | Status | Replacement |
|---|---|---|---|
| K1 | YOLOv11 segmentation stack (§2.1) | **Still in** | RF-DETR @728 + SAM2, both Apache-2.0 |
| K2 | SAHI F1 0.570 (§2.1) | **Still in** | Tile-level box F1 0.826, CI [0.798, 0.853] |
| K3 | ~10% permit recall (§4) | **Still in** | 73.6% pre-2022, 55.3% all years |
| K4 | "334 rooftop arrays" (abstract, §3) | **Still in** | 3,362 arrays across 1,865 sites |
| K5 | 7.2% loss / $291–541 a year | Cleared | Now "a risk level, not a hard percentage" |
| K6 | 3% no clean / 6% clears it | Cleared | Arithmetic gone; framing still implies flagging works |
| K7 | "that minority sits inland" | Cleared | — |
| K8 | Persistent soiling compounds, doubles by year three | **HARDENED** | See below — it is now the paper's spine |
| K9 | "Every stage in production works" | Cleared | §4 is properly hedged now |
| K10 | Risk model "clears its validation gates" | Half | Says "not by a comfortable margin"; still needs CI [0.676, 0.742] |
| K11 | "satellite" / "arial" | Cleared | Reads "flown by aircraft" throughout |
| K12 | Planet Labs / Maxar are expensive | Cleared | But see the resolution framing below |

### The one that got worse — lead with this

**K8 is no longer a stray sentence, it is the organizing metaphor.** Seasonal vs persistent
soiling, with persistent compounding, is load-bearing in §1.2, §2.3, §3.2 and the discussion.
"By year three… the persistent loss has roughly doubled from where it was the year before" is
the payoff of a three-section setup.

PVDAQ system 2107, 8.08 years of revenue-grade data with its own pyranometer: annual max soiling
ratio **1.0000 in 8 of 8 years**, honest bound on any standing layer 0 to 0.2 pts/yr.

So the rebuild is bigger than he thinks — not deleting a claim, replacing the spine. Hand him the
replacement in the same breath so he is not left with a hole: *the localised form saturates rather
than compounds, because a bypass diode caps the loss once a substring drops out.*

### Same shape, smaller

§2.1 still frames NAIP 0.6 m as *the* imagery and §4 makes sharper imagery the future ask. We ran
the whole pilot on county imagery at 6.3 cm native. "Resolution is the fixable problem" is now a
past-tense result, and the strongest before/after in the paper.

---

## 2. Seven things that moved since the punch list

None of these are in the email Josh has. Two change what he writes in Act 3.

**1. The moss channel we handed him as the open frontier is closed on economics.**
`aoi_cleaning_threshold.py`, all 2,494 geometry-scored arrays: **zero clear**, best case loses
~$30. Three causes, largest first: NEC 690.12(B)(2) has effectively mandated MLPE on CA
residential roofs since 2019, defusing the substring nonlinearity that made moss expensive (21%
string vs 8% MLPE for the same band); `MIN_PRO_SERVICE = $150` binds at every residential size
against a 3.5 kW median array; and only 36 of 3,362 arrays match a permit with both pre-2017
hardware and NEM 2.0, and those are *smaller* than average. The 08-23 email predicted a wash pays
if the roof carries a continuous band across several modules — that is now tested, and it does not
pay either. **He will write Act 3 wrong without this.**

**2. Mineral band is dead here too, on two independent lines.** AOI build:clear rain ratio 2.00
sits below every documented band site (2.24–4.80), and modelled on real precipitation the band
never accumulates (f = 0.007) because 25.5 heavy-rain days a year flush it. Three channels — dust,
mineral, biological — and all three fail the economics.

**3. What survives in Act 3 is the measurement, not the product.** Nobody has measured biological
soiling's power *cost* in a Mediterranean climate. Porcar et al. 2018 confirmed biofilm on panels
in Berkeley but measured no power loss; Santa Cruz growth-weighted Time of Wetness is 2,444 h/yr
vs Berkeley's 2,468, ratio 0.99.

**4. A third negative result, and it is the strongest version of the ranking limitation.**
Leave-one-major-region-out, 3 seeds, 4 held-out regions: production (40 features) 0.7252; land
cover worth **+0.033**, the largest block effect measured; raw coordinates worth **−0.001**,
nothing, despite longitude being the highest-gain production feature at 7.9%. That is what a
coordinate memoriser looks like. Held out whole, **Arizona scores 0.51–0.53 for every feature
set** — chance. Upgrades §2.2 from "cannot rank two homes in a town" to "cannot rank a region it
has not seen, and location features are memorisation." Caveat travels with it: n = 4 regions,
bootstrap puts zero inside every interval, and the reason n is 4 is the finding underneath the
finding — 81% of training rows sit west of −114.

**5. Three precision numbers moved against us**, and older docs still quote the old ones: method
noise 0.72 → **0.83** pts, within-cell spread 1.41 → **1.46** pts, S/N 1.95× → **1.76×**. Still
above 1.0 — enough to fit a feature model at n ≈ 988, still not enough to rank two neighbouring
roofs. Wording fix worth catching first: a "shared-weather cell" is 0.5°, roughly 56 × 45 km.
Several docs and both Craig drafts called that a "neighbourhood," overstating the contrast by
about three orders of magnitude in area.

**6. PVDAQ is no longer a proposal, it is running.** 1,629 residential systems, ~11,700
system-years, 16 Köppen classes, 50.5% west of −114 and 42.5% east of −100, humid subtropical the
largest class — a climate NREL barely covers. Agrees with NREL on shared ground: **+0.16 pts, 95%
CI [−0.62, +0.91], n = 24.** Caveat must always travel with it: 881 of NREL's 891 rows came from
this same class of method, so it is a reproduction check, not independent validation.

**7. Tilt: the anchor now reproduces, and it grew a second result.** *Closed 2026-08-30 —
`scripts/analyze/fetch_dgstats_tilt.py`, branch `paper/dgstats-tilt-provenance`.* PG&E
interconnection paperwork p50 **19.0°** against 3DEP lidar `fit_ok` p50 **19.0°**, difference
0.03°, instruments a decade apart with nothing in common. Quote **n = 8,573**, not the 8,547 from
08-19 — a 0.3% drift from a newer release, not a change of filter.

The filter, written down: `Service County` contains SANTA CRUZ, `Technology Type` contains
Photovoltaic, `Customer Sector` = Residential, `Tilt` > 0, de-duplicated on `Application Id`, no
date filter. **The 0° convention is real and it moves the median.** `Tilt` uses 0.0 as "not
reported": 30.9% of rows are exactly 0.0, and requiring `Mounting Method = Rooftop` drops that to
1.7%, which is the evidence they are nulls. Keep them and the median flattens 19.0 → 18.0. All
five filter variants tried land on 19.0.

**And the tails do not agree, for a reason internal to the filter.** DGStats puts 0.78% of arrays
below 5°; the lidar puts **8.38%**. A genuinely flat array is exactly the row an installer reports
as 0.0, which no filter can separate from "not reported" — so the paperwork's low tail is biased
high *by construction* and the lidar's is not. Sub-5° arrays are the population Mejía & Kleissl
measured soiling ~5× faster. **The permit record structurally cannot see the group the paper cares
most about, and the lidar can.** Quote the medians as the agreement and the low tail as a
capability. (This also corrected a figure in the 08-19 handoff: "6.4% of arrays below 5°"
duplicated the p10 value; it is 8.4%.)

> **Sign off before quoting.** The moss narrowing and the grant-narrative edits are marked "added
> by Claude, for Cameron to accept or reject" in `PAPER_REWRITE_BRIEF_20260823.md:28` and
> `GRANT_NARRATIVE_DRAFT.md`. Accept or cut them before presenting them to Josh as settled.

---

## 2b. Three tilt results that are in no draft

`docs/HANDOFF_roof_geometry_for_paper.md` was written for Josh on 2026-08-19 and none of it
reached the draft. Its §5 asked him to lead with three things; two are below.

**The mercator trap — a methods contribution.** EPT X/Y are EPSG:3857 metres, Z is true metres.
At this latitude the scale factor is 1.2515, so a plane fitted on raw coordinates reports tilt
**18.9% too shallow — silently, on every roof, producing an entirely plausible distribution**.
`fit_plane()` de-inflates by cos(lat) and a test pins the magnitude. Anyone reproducing this on
web-mercator lidar hits it and nothing warns them.

**The natural experiment.** The 3DEP flight is 2020, so an array installed before it was scanned
and one installed after was not. Split on permit dates — a variable independent of the
measurement — and the standoff step lands where physics says: pre-flight n=37, median +0.145 m,
65% above 10 cm; post-flight n=54, +0.013 m, 17%. Difference **+0.132 m, 95% CI [+0.084, +0.208],
p < 0.0001**. So `roof_planes.csv` reports racking angle where the array predates the flight and
roof pitch where it does not. Practical consequence: standoff above ~0.08 m tells you which you
have without a permit, which matters because 83% of sites have no permit record.

**Tilt as a feature is a third negative result.** Measured on 91.3% of arrays, every reason to add
it to the model, deliberately not added. Three measured reasons: training IQR [7.49°, 12.00°]
contains **8.9%** of our roofs and our median roof sits at the **90.5th percentile** of training;
GBTs do not extrapolate, so the deepest tilt split is 26.0° and predictions at 30/35/40° are
identical, with partial-dependence spread 0.007 where 73% of our roofs live against 0.023 where
91% of training lives; and the training feature is a station identifier, not physics — 42.4% of
rows at exactly 7.49°, 24 unique values across 1,000 rows, 99% of stations with one value,
correlation 0.372 with longitude, and a learned effect with the **wrong sign**. Pre-empt the
intuitive objection: tilt varies *more* across our AOI than across training (SD ratio 1.382). It
fails on support and referent, not variance.

**Why this matters to his argument.** §1.2 leans on Mejía & Kleissl's "under five degrees soils
five times faster" and §4 says we cannot measure tilt from a flat overhead photo. Both are now out
of date in opposite directions: we can measure it, from free public lidar, on 91.3% of arrays —
and having measured it, it still does not help the risk model. §4 currently promises tilt as
future work that would sharpen the score; it is done, and it does not.

Also restore the variance decomposition, dropped in the last pass: system size 86.7%, roof
orientation **11.1%**, soiling model 2.2%. The 11.1% comes from this lidar work. Caveat:
`poa_rel` is per-array while size and loss are per-site, so it is an estimate, not a like-for-like
decomposition. And measured fleet mean POA/GHI is 1.029 — `economics.py`'s documented guess was
right to within 2.9% — but the flat `BASE_SUN = 5.5` overstates the *typical* roof by 9.1%.
South-20° is near-optimal, so real orientation can almost only revise a site downward, which
hardens the zero-of-1,865 rather than rescuing it.

---

## 3. What Josh needs to leave the meeting with

- [ ] **A title.** Pick one of the three. He cannot restructure without knowing what the paper claims.
- [ ] **The three-act spine, confirmed explicitly.** Act 2 carries equal weight; negative results are not a limitations section.
- [ ] **The spine replacement, delivered with the bad news.** Persistent-compounding out, saturating localised form in. Do not leave him to find the replacement himself.
- [ ] **Division of labour, and explicit credit for the readability pass.** The general-audience direction is compatible — same voice, different spine. Say so or he will think the pass was wasted. Then settle who owns each of the five figures.
- [ ] **The actual paper deadline.** He said "approaching fast" and never named a date; 2026-08-31 is the repo hand-off, not his.
- [ ] **One standing rule:** no number in the paper that is not reproducible from a named script or artifact in-repo. That rule is what the rewrite is for — it is what kept the 7.2% and the 0.90 recovery constant alive for months.

---

## Provenance

Detection and recall numbers are on `main`. Moss, regional-holdout and PVDAQ results are committed
on `soiling/pvdaq-labels-and-moss-audit`, where the `outputs/` JSONs were force-added because the
directory is gitignored.

```
docs/PAPER_REWRITE_BRIEF_20260823.md              the twelve kill items
docs/HANDOFF_20260827.md                          moss verdict, three channels, precision budget
docs/GAMMA_RESOLUTION_20260827.md                 per-system temperature coefficient
docs/PIPELINE_AUDIT_AND_PLAIN_REPORT_20260827.md  §2, the per-roof cleaning answer
scripts/analyze/permit_recall_audit.py            73.6% / 55.3%
scripts/analyze/aoi_cleaning_threshold.py         zero of 2,494
scripts/analyze/regional_holdout.py               land cover +0.033, coordinates -0.001
scripts/detect/eval_tile_f1.py                    the detection gate, which is the definition
```
