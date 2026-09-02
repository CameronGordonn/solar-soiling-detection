# White paper — Section 2 (Methods) draft + review notes

For Josh's draft "Flagging Soiled PVs Through Public Data for Rooftop Array Detection and Soiling Risk".
Written 2026-07-29. Every number below is traceable to a repo artifact — see §C.

- **Part A** — paste-ready Section 2 prose (2.1 detection, 2.2 risk scoring, 2.3 decision model, 2.4 design choices).
- **Part B** — feedback on the rest of the draft, ordered by how much it matters.
- **Part C** — number-provenance table, so no figure in the paper is un-sourced.

---

# Part A — Section 2: Methods

## 2.1 Array detection

**Imagery.** We use USDA National Agriculture Imagery Program (NAIP) orthoimagery, which is
public-domain, aircraft-flown, and distributed at roughly 0.6 m ground sample distance. The pilot
area of interest is a ~22 km box over the Live Oak / Pleasure Point / east Santa Cruz side of the
city (approximately −122.10, 36.85 to −121.85, 37.05 in WGS84), covered by the 2023 NAIP flight.
Imagery is tiled into 640 × 640 pixel chips; each chip's coordinate reference system and affine
transform are recorded in a tile index so that any pixel-space detection can be projected back to
latitude and longitude without ambiguity. The pilot AOI comprises 249 tiles.

**Labeling.** Arrays are annotated as polygons (not bounding boxes) in Roboflow, because array
extent is what feeds the downstream area and geometry features. The current corpus is 248 tiles
and roughly 1,100 polygons, split into train (174 tiles), validation (25 tiles), and test (49
tiles). Splits are held fixed across experiments. Labels have been revised over several rounds:
the validation and test splits have been fully relabeled, and train relabeling is ongoing.

**Model.** We train YOLOv11-small with a polygon segmentation head (Ultralytics, released 2024) —
one class, "array". Training uses SGD at a base learning rate of 0.001, mosaic augmentation at 0.5,
and RandAugment disabled; the automatic optimizer setting is deliberately avoided because it
silently substitutes AdamW and overrides the configured learning rate. Runs warm-start from an
earlier checkpoint on the same corpus rather than from COCO weights. Hyperparameters live in
version-controlled YAML and are committed alongside each run's metrics.

**Inference and evaluation.** Small residential arrays occupy few pixels at 0.6 m, so whole-tile
inference alone under-recalls them. Production inference is therefore two-pass: a whole-tile
forward pass, followed by a sliced-inference pass (SAHI) whose detections are added only where they
do not overlap an existing detection. Our headline metric is the F1 of that combined output, matched
to ground truth by polygon IoU. We report it in preference to mAP50, which we treat only as a fast
regression signal.

> **Stale as of 2026-08-07, flagged by the cross-doc audit 2026-08-31.** The paragraph below
> describes the **60cm YOLOv11 + SAHI** stack. That checkpoint (R2-cameron-20260509) is now
> `stage1-60cm-legacy`: AGPL, eval-only, not shipped. The shipping detector is RF-DETR @728 + SAM2
> at **test tile-level box F1 0.8260**, and no SAHI number is comparable to it. Rewrite guidance:
> [PAPER_REWRITE_BRIEF_20260823.md](PAPER_REWRITE_BRIEF_20260823.md); numbers:
> [CANONICAL_NUMBERS.md](CANONICAL_NUMBERS.md).

On the validation split (140 ground-truth polygons), the **legacy 60cm** checkpoint reaches **SAHI F1
0.570** at its calibrated operating point (confidence 0.20, IoU 0.50; precision 0.538, recall
0.607). At the more conservative threshold used for outreach (confidence 0.40), the same checkpoint
trades recall for precision: **F1 0.538**, precision 0.723, recall 0.429. `model.val()` mAP50 is
approximately 0.26. We report both operating points because they are genuinely different products:
the high-precision setting is appropriate when a false positive means mailing a postcard to a house
with no solar array, and the high-recall setting is appropriate when the goal is coverage.

**Independent recall check.** Detector recall measured against held-out labels overstates recall
against the real install base, because both the labels and the model see the same imagery. We
therefore cross-checked detections against Santa Cruz County building-permit records, which the
model never trained on, matched by parcel APN rather than by geocoded point (address geocodes sit a
median 87 m from the roof and would depress the estimate for reasons unrelated to detection). Of
the permitted solar parcels that fall inside the imaged footprint and pre-date the imagery, the
detector recovers approximately **10%** (22 of 212). This is the single largest known weakness in
the pipeline and we discuss it in §4.2. Two caveats keep the number interpretable in both
directions: about half of the in-footprint permits post-date the 2023 imagery and cannot be
detected at all, and conversely, permits are not a census — 85% of arrays we labeled by hand sit on
parcels with no permit on file, because the permit registry begins in 2016 and California's
residential boom began earlier. Permits are a valid independent recall probe and an invalid
precision denominator.

## 2.2 Soiling risk scoring

The detector answers "where are the arrays". The risk model answers "which of them is losing the
most output to soiling", using only public data.

**Label source.** We train against the NREL PV Soiling Map (Micheli, Deceglie, and Muller), which
publishes an insolation-weighted soiling ratio (IWSR) derived from measured production at
instrumented US stations. IWSR is the fraction of expected insolation-weighted energy actually
delivered, so lower is dirtier. We use the annual panel release — one observation per station-year —
which gives **891 station-year rows across 146 stations in 15 states**, spanning 2008–2022. A further
**109 stations are reported only as summary rows** whose IWSR is censored at ">0.99" (that is,
known-clean but without an annual breakdown); we retain these at the censoring value and downweight
them to half the weight of a panel row, which brings the training set to **1,000 rows across 15
states**. Rows are binarized at **IWSR < 0.97 → at-risk**, a threshold calibrated against the
measured-IWSR distribution rather than chosen for class balance; the resulting base rate is 49.4%,
so the classes are close to even without reweighting.

Using station data as the label source is the pipeline's central methodological compromise, and we
state it plainly: we train on instrumented ground stations and apply the result to residential
rooftops. That transfer is unvalidated. It is also the only option available without production
data, which is precisely the constraint this paper is about.

**Features.** Each row carries **40 features** in four families:

1. *Weather*, from the Open-Meteo historical archive at the station's coordinates, aggregated over
   trailing 7-, 30-, and 90-day windows ending at the observation date: precipitation sum, maximum
   10 m wind speed, mean relative humidity, and maximum temperature. Two additional rain-timing
   features carry most of the seasonal signal: consecutive dry-day streak, and days since the last
   ≥5 mm rain event — the physical proxy for "has this array been rinsed lately".
2. *Air quality*, over the same three windows: PM2.5 and PM10 means. These stand in for deposition
   flux, which is not directly observable at this scale.
3. *Location and structure*: latitude, longitude, month of year, array tilt, elevation, land-cover
   class, distance to the nearest highway, distance to the nearest agricultural parcel, and five
   ESA WorldCover fractional covers (cropland, built-up, bare, tree, grass). Highway and
   agricultural proximity are included as proxies for the two dominant anthropogenic dust sources.
4. *Physics proxies*: per-row soiling trajectories computed from two published empirical models —
   Kimber (2007), which accumulates loss linearly with PM2.5 and resets fully on rain, and
   SOMOSclean (Micheli et al.), which models complementary exponential growth toward a saturation
   ceiling with PM10-accelerated deposition and rain-proportional partial cleaning. Each enters as
   an instantaneous value plus 7/30/90-day means. SOMOSclean's saturation and rate parameters were
   re-fit to coastal California NREL stations (saturation 0.08, k = 15) rather than left at the
   published Spanish-plant values.

Including physics proxies as *features* while the *labels* come from measured NREL IWSR is
deliberate and leakage-free. We note it explicitly because the converse configuration is not:
earlier ablations that used SOMOSclean-derived labels while retaining the SOMOSclean feature
produced a cross-validated AUC of exactly 1.0 — the feature was the label. Those runs are discarded
and reported here only as a cautionary result.

**Model.** Gradient-boosted trees (XGBoost), 200 estimators, maximum depth 4, learning rate 0.05,
subsample 0.8, column subsample 0.8, L2 regularization 5.0. Depth and L2 were tuned specifically
against overfitting: 40 features on roughly 900 rows overfit visibly at depth 5, and the shallower,
more strongly regularized configuration improved spatially cross-validated AUC by about 0.007 with
a variance of ±0.004 across seven random seeds. Predicted probabilities are mapped through an
isotonic calibrator so that the number surfaced to a homeowner can be read as a probability rather
than a ranking score.

**Validation — spatial.** Stations within 10 km are clustered and assigned to the same fold, so no
fold is scored on a station whose near neighbour was in training; without this, geographically
adjacent stations inflate the estimate. Over 5 such folds the model reaches **mean AUC 0.728** as recorded (**0.712 on re-measurement under the current config** — quote 0.712)
(folds: 0.698, 0.813, 0.705, 0.698, 0.725) and **mean average precision 0.659**.

**Validation — temporal.** Spatial cross-validation does not test whether the model generalizes to
a year it has not seen, which is the deployment condition. Our initial design held out 2022 as a
single test year, and it returned AUC 0.679 on 97 rows (0.680 under the regularized config). That estimate is not usable: its
Hanley–McNeil standard error is 0.061, its 95% confidence interval is [0.557, 0.797], and our 0.70
acceptance threshold sits 0.32 standard errors away. A 97-row year cannot distinguish 0.68 from 0.70,
and we report this because it is the kind of number that gets over-read in either direction.

We replaced it with a rolling leave-one-year-out evaluation: hold out each of the 15 panel years in
turn, train on all others (summary rows always in training), and pool every row's
out-of-its-own-year prediction. Pooled over n = 891 (440 at-risk, 451 not), this gives **AUC 0.710,
standard error 0.017, bootstrap 95% CI [0.676, 0.742]** — a 3.5× tighter estimator on identical
data and labels. The point estimate clears 0.70; the interval straddles it. Our reading is that
temporal generalization is *at* our acceptance threshold, not comfortably above it. The weakest
individual year is 2019 (AUC 0.626); years 2008–2012 are close to single-class and contribute
little.

**Validation — calibration.** Ranking correctly is necessary but not sufficient, because the product
surfaces a calibrated probability and a dollar figure rather than a rank. We therefore also evaluate
calibration out-of-year, fitting each training year's isotonic map and applying it to the held-out
year: pooled **Brier score 0.218** against a base-rate reference of 0.250, **expected calibration
error 0.046**, maximum calibration error 0.126. Calibration survives temporal shift.

**A negative result worth reporting.** We attempted to improve the air-quality features by
substituting NASA MERRA-2 reanalysis aerosol fields for the Open-Meteo product. It degraded both
metrics (spatial CV 0.716, single-year holdout 0.666) at the cost of a 1.3 GB local cache, and we
abandoned it. Our interpretation is that MERRA-2's coarse grid (roughly 50 km) averages away exactly
the local deposition contrast the model is trying to exploit.

## 2.3 Cleaning decision model

A risk score is not a decision. Converting one to the other requires separating two soiling regimes
that behave differently over time. *Seasonal* soiling is rinsed off by winter rain, so its loss is
bounded by a single dry season and does not accumulate. *Persistent* soiling — pollen films,
biological growth, cemented mineral deposits — is not fully removed by rain, so a fraction of each
year's loss carries into the next and compounds. The decision model projects both over a one-to-five
year horizon from the array's modeled soiling rate and the time since it was last cleaned, prices
the recovered energy at a user-supplied electricity rate and system size, and compares the result
to the cost of the cleaning method chosen. It recommends cleaning only when projected recovery
exceeds cost.

The honest consequence of running this arithmetic is that in a coastal, rain-reset market like Santa
Cruz, a typical residential system does **not** clear the bar: at a 3%-per-season loss on a 6 kW
system, the model returns "no clean". A high-soiling home at 6% clears it, but modestly (on the order
of tens of dollars per year net at a $75–90 service price). This is the same conclusion Mejía and
Kleissl reach for the average California site, arrived at independently, and it is a finding rather
than a defect: the value of the pipeline is identifying the minority of arrays for which cleaning
does pay, and that minority is concentrated inland, not on the coast.

## 2.4 Tools and design choices

Every input is free and public: NAIP for imagery, Open-Meteo for weather and air quality, ESA
WorldCover and OpenStreetMap for land cover and proximity, USGS for elevation, county parcel and
permit records for addresses, and NREL for labels. This was a design constraint, not a budget
accident — a pipeline that depends on a licensed data feed cannot be replicated by the researchers
or municipalities most likely to want it.

Two consequences follow. First, we chose polygon segmentation over bounding-box detection because
array area feeds the dollar estimate; a box would over-state area on non-rectangular roofs. Second,
we cache every external API response in a local SQLite store keyed by location and date, which makes
runs reproducible and keeps us inside free-tier rate limits. That limit is real: the free
weather-archive tier caps bulk scoring at roughly 600 homes per day, which is currently the binding
constraint on scaling the pilot beyond one AOI.

---

# Part B — Review notes on the rest of the draft

## Must fix before this goes anywhere

**1. §2 bullet 1 and §1.3 — the model is YOLOv11, not YOLOv8.** Also, NAIP is flown by aircraft, so
"satellite array identification" in §1.3 is wrong and contradicts the abstract, which correctly says
"aerial". Use "aerial imagery" throughout.

**2. §4.1 — the F1 0.570 claim needs its operating point, or it is not reproducible.** F1 0.570 holds
at confidence 0.20; the pipeline that actually produced the mailed postcards ran at confidence 0.40,
where F1 is 0.538 — below our own 0.55 beta threshold. As written, the sentence pairs a metric from
one configuration with a deployment from another. Either state both operating points (my §2.1 does)
or re-run the outreach path at 0.20. Not fixing this is the single most likely thing to get the paper
picked apart.

**3. §4.1 cites "section 2.2" for the risk model gates, and §4.2 promises limitations, but the gate
result itself is overstated.** The pooled out-of-year AUC is 0.710 with a 95% CI of [0.676, 0.742] —
the point estimate clears 0.70 and the interval straddles it. "Clears its validation gates" is
defensible only if the CI is printed next to it. Say "clears on the point estimate, with the interval
straddling the threshold".

**4. §5 first paragraph — "the typical system loses about 7.2% of its output to summer soiling, or
roughly $291 to $541 a year" has no source I can find in our artifacts, and it contradicts our own
economics analysis**, which puts typical coastal loss near 3% and returns a "no clean"
recommendation at that level. A reader who accepts $291–541/yr as typical will reasonably ask why we
are not telling every homeowner to clean. Either trace the figure and reconcile it with the decision
model, or replace it with the coastal 3% number and lead with the honest version: most Santa Cruz
roofs are not worth cleaning, and the pipeline's job is finding the ones that are. I think the honest
version is the stronger paper — it makes the targeting the contribution instead of the loss estimate.

**5. §4.2 is missing the biggest limitation: recall.** Resolution is discussed as a
false-positive/labeling problem, but the measured consequence is a miss problem — roughly 10% recall
against permit records on the imaged footprint, 334 detected arrays against 7,399 permitted solar
parcels county-wide. Right now §4.1 says "every stage in production works" three paragraphs before a
limitations section that omits the one number a reviewer will most want. Draft text is in my §2.1;
it should be restated as a limitation with the two caveats intact (half those permits post-date the
imagery; permits are not a census).

## Should fix

**6. Imagery staleness is a separate limitation from resolution.** The pixels are NAIP 2023; permits
run through 2026. Any array installed in the last two years is invisible to us regardless of
resolution, and that includes a large share of the recent boom. Worth its own short paragraph in
§4.2.

**7. Abstract says "nearly 350 rooftop arrays"; the real number is 334**, which §3 and §5 both state
correctly. Use 334 in the abstract.

**8. §1.2's strongest fact deserves a callback in the limitations.** Mejía and Kleissl's finding that
arrays tilted below five degrees soil about five times faster is the best argument in the
introduction for per-array scoring — and we cannot measure per-rooftop tilt from 0.6 m nadir imagery.
Our model has a tilt feature only because NREL supplies it for instrumented stations; at deployment
that feature is imputed. Naming this makes the paper look self-aware rather than exposed, and it
sets up "tilt from lidar or oblique imagery" as concrete future work.

**9. §3.3 — the mailing is real; give it its date and its status.** 50 postcards shipped live through
Lob on 2026-06-30, 50 of 50 accepted, $47.52 total. If no responses have landed yet, say so
explicitly rather than "we are actively tracking the results" — a stated null-so-far is more credible
than an open-ended present tense. Also worth one sentence: response rate on a 50-card drop cannot
resolve anything statistically, so this is a deliverability and mechanics test, not a behavioral
result.

**10. Add a data-provenance and ethics sentence.** We mail homeowners at addresses joined from county
parcel records to arrays detected in public imagery. That is entirely public data and lawful, but a
reviewer will want to see that we know it, and a sentence on what we do not retain costs nothing.

**11. §4.2 — quantify the resolution argument.** "60 cm imagery, which translates to 60 cm per pixel"
is redundant; the persuasive version is the pixel count. A 20 m² residential array is roughly 55
pixels total at 0.6 m, which is the same footprint as a parked car. That is why the confusion is
structural rather than a labeling lapse.

## Nice to have

**12. Define IWSR on first use** if Section 2 lands as drafted, and state the direction (lower is
dirtier) — it is counterintuitive.

**13. Report the MERRA-2 negative result** (§2.2 above). Journals ask for it, and it saves the next
group the 1.3 GB.

**14. §5 Future Path — add two concrete items.** (a) Move off Ultralytics YOLO, which is AGPL-3.0
licensed and therefore a genuine obstacle to anyone deploying this commercially, toward a
permissively licensed detector; (b) higher-resolution *public* imagery — Santa Cruz County publishes
a 0.256 m/px imagery service, which is materially better than NAIP and free, so the "higher
resolution is expensive" framing in §4.2 is too absolute. County and state imagery programs are the
realistic path, not Planet or Maxar.

**15. Typo, §5 first line: "arial overview" → "aerial".** Same word as issue 1, worth a global
find-replace on "arial"/"satellite".

**16. The draft has no results section and no reference list.** §4.1 carries the results in prose. If
this is going anywhere peer-reviewed it needs both, plus a reproducibility statement — every number
in Part A traces to a committed artifact and I can supply the paths.

---

# Part C — number provenance

| Claim | Value | Source |
|---|---|---|
| Val SAHI F1, calibrated | 0.570 (P 0.538 / R 0.607) @ conf 0.20, iou 0.50 | `outputs/eval/r2_cameron_20260509_val_20260705/sahi_threshold_sweep_best.json` |
| Val SAHI F1, deployed | 0.538 (P 0.723 / R 0.429) @ conf 0.40, iou 0.50 | same dir, `sahi_threshold_sweep.csv` |
| Val ground truth | 140 polygons | same sweep (tp 85 + fn 55) |
| mAP50 | ≈0.26 | `model.val()`, regression signal only |
| Dataset splits | 174 / 25 / 49 tiles, ≈1,100 polygons | live count, `data/yolo/naip/labels/*` |
| Permit recall (imaged era, APN) | 10.4% (22/212) | `outputs/economics/permit_recall_audit.md` |
| Labeled arrays with no permit | 85% (889/1048) | `outputs/economics/labeled_vs_permit.md` |
| Detected arrays, pilot AOI | 334 (249 tiles, NAIP 2023) | `docs/MAILER_PIPELINE.md` |
| County permitted solar parcels | 7,399 (2016–2026) | same |
| Spatial-CV AUC | 0.728 mean as stored, 5 folds, 10 km clusters. **Re-measures at 0.712** under the current `model.yaml` (`abl_full40`, 2026-08-06) — quote 0.712 | `runs/soiling/run_optionb/metrics.json`, `runs/soiling/abl_full40/metrics.json` |
| Spatial-CV AP | 0.659 | same |
| Single-year 2022 holdout | AUC **0.679**, n=97 (`run_optionb`); 0.680 is `run_regularized2022`, a different run | `run_optionb/metrics.json:holdout_auc` = 0.67886. SE 0.061, CI [0.557, 0.797] from `holdout_ci.json` |
| Pooled out-of-year AUC | 0.710, n=891, SE 0.017, CI [0.676, 0.742] | `runs/soiling/run_optionb/holdout_ci.json` |
| Out-of-year calibration | Brier 0.218 (base rate 0.250), ECE 0.046, MCE 0.126 | same |
| Label rows | 891 panel (146 stations) + **111** summary = **1,002** rows, 257 stations, 15 states | `outputs/soiling/training_matrix.parquet` (counted, not quoted) |
| Label threshold | IWSR < 0.97 | `configs/soiling/california.yaml` |
| Feature count | 40 | `runs/soiling/run_optionb/feature_names.json` |
| XGBoost config | 200 est, depth 4, lr 0.05, subsample 0.8, colsample 0.8, λ=5.0 | `configs/soiling/model.yaml` |
| SOMOSclean params | sl_sat 0.08, k 15 (re-fit to coastal CA) | `configs/soiling/features.yaml` |
| MERRA-2 negative result | 0.716 CV / 0.666 holdout, abandoned | `runs/soiling/run_merged_merra2/` |
| Leakage runs (discarded) | CV AUC 1.0, SOMOSclean label + feature | `run_somosclean_v2_calibrated`, `_v4` |
| Mailing | 50/50 live via Lob, 2026-06-30, $47.52 | commit `87adfeb`, `docs/MAILER_PIPELINE.md` |
| Coastal economics | 3% typical → no_clean; 6% → +$51/yr net | `outputs/economics/` meeting metrics |
| Open-Meteo free-tier cap | ≈600 homes/day | quota gate in scoring path |

**Unresolved:** the 7.2% loss and $291–541/yr figures in §5 do not appear in any repo artifact I
could find. They need a source before publication (see issue 4).
