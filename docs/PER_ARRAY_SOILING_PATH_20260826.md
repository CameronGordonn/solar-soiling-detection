# Per-array soiling ranking: a measured, literature-anchored path

**2026-08-26.** Craig's ask is per-array soiling **ranking**, not detection. This is the
answer to whether that is achievable, what it requires, and what it is worth. Everything
below is measured in this repo or cited to a paper; assumptions are labelled as such.

> **Superseded in two places, 2026-08-27.** The precision numbers below were measured before
> the module temperature coefficient was resolved per system, so they mix a signal figure and a
> noise figure that came from different assumptions. Current values, from
> [`GAMMA_RESOLUTION_20260827.md`](GAMMA_RESOLUTION_20260827.md):
>
> | | in this doc | current |
> |---|---|---|
> | per-label method noise | 0.72 pts (irradiance only, n=19) | **0.83 pts** (irradiance + gamma, n=20, both diagonals) |
> | within-cell between-system spread | 1.41 pts (fleet-gamma labels) | **1.46 pts** (resolved-gamma refit, 66 of 75) |
> | signal-to-noise | 1.95x | **1.76x** |
>
> The **verdict does not change**: still above 1.0, still well powered for a feature model at
> n≈988, still not enough to rank two roofs against each other. Row 2 of the table below should
> read "Marginal, ~1.8:1 signal". The other rows are unaffected.
>
> Also correct as you read: a "shared-weather cell" is 0.5 degrees, about **56 km x 45 km** at
> this latitude. That is a metro area, not a neighbourhood, and two systems in one cell can be
> fifty kilometres apart.

**Verdict: yes, with a caveat about which question you are asking.**

| question | answer | basis |
|---|---|---|
| Can we learn *which array features* drive soiling? | **Yes, well powered** | n≈988 systems in 66 shared-weather cells |
| Can we rank *an individual roof* against its neighbour? | **Marginal, ~2:1 signal** | method noise 0.72 pts vs within-cell spread 1.41 pts |
| Does ranking *dust* soiling create product value? | **No** | breakeven exceeds 100% of output |
| Does ranking *biological* soiling create product value? | **Plausibly yes, for a named segment** | breakeven f=0.16-0.19 at NEM 2.0; model reaches f=0.24 under heavy canopy |

The last two rows are the important ones and they are the reason this document exists.

---

## 1. The four measurements

### 1a. Method noise is 0.72 pts, and the bootstrap was right all along
`scripts/analyze/pvdaq_method_noise.py`, n=18. Refit the same roof over the same years,
changing only the irradiance source: Open-Meteo (ERA5 reanalysis, our Perez transposition)
versus PVGIS (PVGIS-NSRDB satellite, POA served directly). Same roof and same period means
**zero real spatial variation**, so everything that moves is method error.

```
mean diff +0.18   SD 0.72 pts   max |diff| 1.54
bootstrap CI width claimed 0.66  ->  well calibrated (1.1x)
```

This resolves a bracket that was previously wrong at both ends. The 2.14-pt PVDAQ-vs-NREL
disagreement is **not** three-times-optimistic error bars; it is method noise (0.72) plus
genuine roof-to-station variation (~2.0). That residual is itself evidence that per-location
soiling really does vary, which is the premise of the whole programme.

⚠️ An n=3 preliminary run gave SD 0.12 and looked spectacular. It was luck. Quote 0.72.

### 1b. Labels are unbiased against NREL
`scripts/analyze/pvdaq_phase2_vs_nrel.py`, 24 systems within 5 km of an NREL station:

```
PVDAQ p50 2.54 pts | NREL p50 2.70 pts
paired diff +0.19   95% CI [-0.66, +0.99]   Wilcoxon p 0.46   Spearman +0.60
Csb subset (n=12): PVDAQ 2.91 | NREL 4.00
```

Caveat that must travel with this: 881 of NREL's 891 rows were themselves produced by this
class of method, so agreement is a **reproduction check on our implementation**, not
independent validation.

### 1c. Within-cell spread is 1.41 pts, and 67% of variance is available
`scripts/analyze/pvdaq_within_cluster.py`, 66 of 75 systems passing QC across 9 cells.
Within-cell between-system SD is **1.41 pts** (down from 2.03 across metros, so ~30% of the
naive spread was climate that fixed effects correctly absorb). **67% of total variance sits
within cells**, which is the share array-level features can explain.

**Signal-to-noise = 1.41 / 0.72 = 1.95x.** Per-cell it ranges 0.48x to 3.94x.
*(Superseded 2026-08-27: 1.46 / 0.83 = **1.76x**, per-cell 0.62x to 3.85x. See the banner at
the top of this file.)*

What that means, precisely:
- **For fitting a feature model** (does tilt matter? does canopy?): strong. Coefficient
  precision scales with sqrt(n) and n≈988 is available.
- **For ranking one roof against its neighbour**: you can separate a top-quartile roof from
  a bottom-quartile roof; you cannot separate adjacent roofs. Say it that way to Craig.

### 1d. The fleet-scale degradation test FAILED. Do not use it.
`scripts/analyze/pvdaq_degradation_vs_wetness.py` asked whether wetter climates show steeper
apparent degradation, on the theory that accumulating biofilm is visible to a year-on-year
method even though `recenter=True` hides a day-one layer. It returned Spearman -0.53,
p=0.030, n=17, in the predicted direction.

**It is rejected on two grounds and the p-value is irrelevant.** First, the fitted
degradation rates are not physically credible: median **-2.39 %/yr** against a literature
consensus near -0.5 %/yr, with two systems fitting *positive*. Daily data with modeled
irradiance and no clear-sky filtering does not support a YoY degradation fit. Second, and
fatally, wetness is collinear with geography in this sample (driest bins are California,
wettest are Cfa/Cfb), so module vintage, install practice, cloudiness and data quality all
covary with the predictor. The design cannot separate biofilm from data quality.

The salvage is to ask the same question **within one cell** using canopy as the varying term,
which is Phase 3. It does not stand as an independent test. A plausibility gate is now in the
script so the result cannot be quoted accidentally.

---

## 2. Two channels, and only one is alive

This is the single most important distinction in the project and it was previously blurred.

There are **three** channels, not two, and separating the second from the third was an error
I made and then caught. Modelled on real AOI weather by `src/risk/band_soiling.py` and
`scripts/analyze/band_channel_economics.py`:

| | dust | mineral bottom-edge band | biological |
|---|---|---|---|
| what it is | dust on open glass | dust transported into the frame trap | moss, lichen, algae, biofilm |
| driver | deposition | LIGHT rain transport (Zhao) | Time of Wetness (growth) |
| heavy rain | resets it | **flushes the band** | **feeds it, never removes it** |
| `recovery_frac` | **0.045** (measured) | **0.36** (modelled) | **0.51-0.61** (modelled) |
| f reached in AOI | n/a | **0.007** | **0.17 open / 0.24 heavy canopy** |
| verdict here | dead | **dead in Santa Cruz** | **live** |

**The mineral band is dead in Santa Cruz, on two independent lines.** Its build:clear rain
ratio is 2.00, below every site where bands were documented (2.24-4.80,
`band_rain_regime.py`); and modelling it on ten years of real AOI precipitation, the band
never accumulates (f reaches 0.007) because 25.5 heavy-rain days a year keep flushing it.
Do not build on the Zhao mechanism here.

**Biological soiling is a different physical process and it survives both objections.** Moss
and biofilm are attached, growing organisms: rain does not flush them, it feeds them, and
the only reset is mechanical cleaning. That is what makes the economics work.

⚠️ **Correction to `CRAIG_BRIEF_2026-08-19.md` §4.** Its breakeven bars of 2.2-10.1% assume
`recovery_frac = 1.0`. Modelled against AOI wetness, one wash actually recovers **0.51-0.61**
of a year's biological loss, because biofilm partially regrows inside the year. The corrected
bars for a 5 kW system at $150 are **6.4% at NEM 2.0** and **17.9% at NBT**, not 3.6% and
10.1%. Still transformative against dust's >100%, but quote the corrected numbers.

**Recolonisation, sourced:** algal biofilm becomes "clearly recognisable after twelve months"
in one monitored case and imperceptible after a year in another; lichen was "only poorly
re-established at the end of a 54-month monitoring period". Commercial practice reapplies
every 2-3 years. Our modelled 0.6 is therefore likely conservative, and amortising a wash
over 2-3 years would improve it further.

---

## 3. The physics is real and the literature confirms the magnitude

**Our model reproduces** (`scripts/analyze/substring_shade_loss.py`, pvlib `bishop88`
single-diode with bypass diodes and reverse-bias breakdown, 60-cell 295 W, 20-module array):

| occlusion | full-cell MLPE | full-cell string |
|---|---|---|
| edge band f=0.2, portrait, 4 modules | 2.15% | **9.10%** |
| edge band f=0.3, portrait, 4 modules | 4.01% | **18.39%** |
| edge band f=0.5, portrait, 4 modules | 8.20% | **21.00%** |
| edge band f=0.5, landscape, 4 modules | 7.00% | 7.00% |

**Independent literature agreement**, and it is close:

- Gostein et al. (2015): bottom-edge soiling covering **0.5% of module area caused 9% power
  loss** at ~5° tilt. Our f=0.2 portrait/string case gives **9.10%**.
- Zhao et al. 2021, *Characterization of Soiling Bands on the Bottom Edges of PV Modules*
  (Front. Energy Res. 9:665411): mean **4.7%** loss with peaks to **20%** at ~4° tilt,
  portrait, Kaifeng; cites **20-26.7%** for landscape modules at ~3° tilt in Shanxi.

The mechanism is geometric and established: the module frame stands 1-3 mm proud of the
glass, making the bottom edge a stagnant trap, and low tilt deepens the effect.

**The finding that inverts our model's assumption.** Zhao et al. report that
**light-to-moderate rain makes the band THICKER** (it mobilises dust off the open glass and
deposits it in the trap, where "raindrops have little effect on particles deposited at the
bottom"), and only **heavy rain** clears it. Our `src/risk/recovery.py` treats every ≥10 mm
event as a reset. For the band channel, sub-threshold rain is a *loading* event. That is a
concrete, implementable correction.

---

## 4. Does it apply HERE? Two free tests, and they disagree usefully

### Mineral bands: weakly supported for Santa Cruz
`scripts/analyze/band_rain_regime.py`. If light rain builds the band and heavy rain clears
it, the relevant statistic is the build:clear ratio, not annual rainfall.

```
site                              mm/yr  light/yr  heavy/yr  ratio
Santa Cruz CA (AOI)                 863      51.1      25.5   2.00
Guangzhou CN (bands observed)      2255     162.4      72.6   2.24
Kaifeng CN  (bands observed)        668      87.5      19.4   4.51
Xi'an CN    (bands observed)        682     100.3      20.9   4.80
```

Santa Cruz sits **below every documented band site**, though only just below Guangzhou. So
the *mineral* band mechanism transfers weakly. Do not lead with it.

### Biological growth: strongly supported for Santa Cruz
`scripts/analyze/biological_growth_potential.py`. Moss and biofilm are not deposited, they
**grow**, so the controlling variable is Time of Wetness, the ISO 9223 measure (hours with
RH ≥ 80% and T > 0 °C) that is standard for atmospheric corrosion and facade biofilm. Two
refinements are added: dew hours (within 2 °C of dewpoint, capturing coastal marine layer,
which a rainfall model cannot see) and a Q10 metabolic weighting above a 5 °C floor. The
known weakness of bare ISO 9223 is that ambient RH differs from surface RH, which is exactly
what the dew term addresses.

```
site                                        growth-weighted TOW h/yr
Sao Paulo BR   (Shirakawa: 11% loss @ 18 months)      4519
Berkeley CA    (biofilm CONFIRMED on panels)          2468
Santa Cruz CA  (AOI)                                  2444   <- ratio to Berkeley 0.99
Arbuckle CA    (PVDAQ 2107: NO standing layer, 8 yrs)  720
Phoenix AZ                                             336
```

**Santa Cruz is climatically indistinguishable from Berkeley for biological growth (0.99),
and Berkeley is where sub-aerial biofilm on solar panels was confirmed** (Porcar et al. 2018;
organisms characterised, **no power loss measured**, and the paper itself reaches for
Shirakawa's Brazilian 11% figure for want of a local one). Santa Cruz is
**3.4x** the wetness of Arbuckle, which is the one site where we measured no standing layer,
so that null explicitly does not transfer.

The facade literature independently confirms the per-surface predictors already in
`scripts/analyze/rank_moss_candidates.py`: north-facing aspect, shading, and slow drying
after wetting are the recognised drivers of algal growth on buildings. Our weights
(0.60 canopy / 0.25 low tilt / 0.15 north) are unvalidated in magnitude but correct in
direction and in ordering.

**The gap this sits in:** biofilm on PV has measured power loss in the tropics (São Paulo,
11% at 18 months, 58% coverage) and confirmed presence in coastal California, but **nobody
has measured its power cost in a Mediterranean climate.** That is a real, fundable,
publishable gap, and it is the one Craig is pointing at.

---

## 5. The free-data stack for putting moss on a map

Everything below is already in the repo or is one script away. No paid data.

| layer | variable | source | status |
|---|---|---|---|
| regional | growth-weighted TOW | Open-Meteo hourly, free | **built** (`biological_growth_potential.py`) |
| regional | light:heavy rain ratio | Open-Meteo daily, free | **built** (`band_rain_regime.py`) |
| per-roof | canopy fraction (9 m radius, returns >2.5 m above the fitted plane) | USGS 3DEP lidar, free | **built**, 2,494 arrays scored |
| per-roof | tilt, azimuth | 3DEP plane fits | **built**, 3,068 of 3,362 arrays |
| per-roof | array polygon + area | RF-DETR + SAM2 on NAIP | **built**, F1 0.826 |
| per-roof | module orientation (portrait/landscape) | 21 cm imagery | **not built** — decides 7% vs 21% |
| per-roof | install era (half-cut after ~2019) | county permit records | **not built** — decides the architecture |
| loss | band f → % output | pvlib substring model | **built** (`substring_shade_loss.py`) |
| truth | band thickness | ground photos | **pending Craig** |

**The two unbuilt per-roof layers are the highest-value additions**, because the physics says
they swing the answer by 3x (landscape 7.00% vs portrait-string 21.00% for the same moss).
Both are free. Module orientation is resolvable at 6 cm; install era is a permit join we
already do.

**The structural reason this approach works where the XGBoost model failed.** The risk model
tried to *learn* per-roof effects from station labels that contain no per-roof variation, and
it measurably failed (WorldCover went in properly and moved within-AOI spread 0.78 → 0.77
with the sign backwards). This stack instead **imposes** relationships known independently:
substring physics from pvlib, tilt response from Cano (2011), wetness from ISO 9223. That is
the same move `src/risk/tilt_response.py` already makes, and it sidesteps the identifiability
problem rather than fighting it.

---

## 6. What to build, in order

**Step 1 — module orientation and install era (2-3 days).** Classify portrait vs landscape
from the 21 cm chips for the 3,362 detected arrays, and join permit year for half-cut era.
These are free and they are worth 3x in the loss model. Without them every moss estimate
carries a 3x fork.

**Step 2 — correct `src/risk/recovery.py` for the band channel (1 day).** Sub-threshold rain
currently does nothing; per Zhao et al. it should *load* the band. Add a second accumulation
term driven by the light-rain count with heavy rain as the only reset. Keep the dust channel
exactly as measured; this is an additional channel, not a replacement.

**Step 3 — the ground survey (one afternoon, Craig's photos).** Still the only thing that
closes band thickness, which is sub-pixel at 78 mm/px against a ~30 mm deciding difference.
It is a **max statistic**: the question is whether f reaches 0.2-0.3 anywhere in town.
**Existing written stop rule, unchanged: if the thickest continuous band findable after a
full afternoon in high-canopy neighbourhoods is below f = 0.1 (~16 mm), the thesis is dead on
physics and the honest output is a short public writeup saying so.**

**Step 4 — Phase 3 as specified, with canopy as a covariate (1-1.5 weeks).** Within-cell
regression of measured soiling loss on array features with cluster fixed effects. Now
justified: 1.95x signal-to-noise, 67% of variance within cells, n≈988. Canopy is not optional
— several PVDAQ Csb sites sit under heavy tree cover, and shading would otherwise be absorbed
into the tilt and azimuth coefficients and read as soiling.

**Step 5 — extract the label set at scale (2-3 days).** 1,236 systems, 0.16 GB, per-cell
irradiance to stay inside the free tier. Only worth doing after step 4 confirms the design.

---

## 6b. The target segment, which is now specific

Running the biological channel through `src/risk/economics.py` for a 5 kW system at $150
(`scripts/analyze/band_channel_economics.py`), the band coverage `f` needed to break even:

| mounting | NBT $0.165 | NEM 2.0 $0.461 |
|---|---|---|
| portrait, string inverter | 0.30 | **0.16** |
| landscape, string inverter | never | **0.19** |
| portrait, MLPE (microinverters/optimisers) | never | 0.41 |

The modelled coverage reached in the AOI is **0.166 on an open roof and 0.240 under heavy
canopy**. So the qualifying roof is specific and falsifiable:

> **NEM 1.0/2.0 tariff + string inverter (pre-~2019 install) + heavy canopy + low tilt.**

Every one of those is knowable from free data we already hold or can join: tariff vintage
from interconnection date, inverter architecture from permit records, canopy from 3DEP
lidar (already computed for 2,494 arrays), tilt from the same plane fits. **MLPE roofs and
NBT-tariff roofs do not qualify at any plausible band thickness** and should be excluded
before anyone is contacted, which is itself a useful result: it shrinks the addressable set
to something a survey can actually check.

---

## 7. Stop rules, written before the results

- **Ground survey:** f < 0.1 anywhere in town → biological thesis dead, publish the negative.
- **Phase 3:** if array features explain essentially nothing within cells with a tight CI →
  the per-roof premise is wrong, publish that. With 1.95x SNR and n≈988 a null would be
  informative rather than merely underpowered, which was **not** true a day ago.
- **Any per-roof loss claim** must state module orientation and era, or carry the 3x fork
  explicitly.
- **Do not quote** the fleet degradation correlation (§1d), the n=3 method-noise figure of
  0.12, or the `perfect_clean`/`half_norm_clean` gap of ~7 pts. All three look like results
  and none are.

---

## 8. Honest summary for Craig

Per-array ranking is achievable. Signal is about twice measurement noise per roof, which is
enough to sort roofs into groups and enough to learn what drives the differences, but not
enough to rank two neighbours confidently. That is a real capability and it is worth stating
at that precision rather than louder.

The value is not in ranking dust, which is measured and dead. Nor is it the mineral
bottom-edge band, which two independent lines say Santa Cruz's rain flushes. It is the
biological channel: rain feeds it rather than clearing it, it sits outside every label we
have by construction, its physics is nonlinear enough to matter (9-21% for a thin edge
band), its magnitude is confirmed in adjacent literature, and our climate supports it as
strongly as the one place it has been confirmed on panels.

The qualifying roof is now specific enough to falsify: NEM 2.0 tariff, string inverter,
heavy canopy, low tilt, and a band reaching f ≈ 0.16. Three of those five are already
computed. The fourth is a permit join. The fifth is an afternoon with a camera, and it has
a written kill threshold.

Nobody has measured biological soiling's power cost in a Mediterranean climate. That is the
gap, it is reachable with free data, and it is what Craig is pointing at.

---

## Reproduce

```bash
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_method_noise.py \
    --system 10109 10112 10251 10477 11758 11881 10063 11539 10158 12023 \
             10068 11964 10666 11670 10817 12028 10415 10349 11066 12308 \
    --out-json outputs/soiling/method_noise.json
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_phase2_vs_nrel.py
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_within_cluster.py
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/band_rain_regime.py
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/biological_growth_potential.py
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/substring_shade_loss.py
```

## Sources

- Zhao et al. 2021, *Characterization of Soiling Bands on the Bottom Edges of PV Modules*,
  Frontiers in Energy Research 9:665411.
- Shirakawa et al. 2015, *Microbial colonization affects the efficiency of photovoltaic
  panels in a tropical environment*, J. Environ. Manage. (Sao Paulo, Brazil). Coverage
  42/53/58% at 6/12/18 months; power loss 7% at 12 months, 11% at 18.
- Porcar M, Louie KB, Kosina SM, Van Goethem MW, Bowen BP, Tanner K, Northen TR (2018),
  *Microbial Ecology on Solar Panels in Berkeley, CA, United States*, Front. Microbiol.
  9:3043. Organisms characterised; **no power loss measured**.
- ISO 9223, time-of-wetness definition.
- Gostein et al. 2015, bottom-edge soiling, **as cited in Zhao et al. 2021** (the primary
  was not read directly; verify before it goes in a paper).
- Cano 2011, *Photovoltaic Modules: Effect of Tilt Angle on Soiling* (already in
  `src/risk/tilt_response.py`).
