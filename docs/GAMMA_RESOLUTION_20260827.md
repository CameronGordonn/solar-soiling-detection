# Closing the GAMMA_PDC question — 2026-08-27

The open problem handed over in `docs/HANDOFF_20260827.md`. Everything below is measured in
this repo; assumptions are labelled.

---

## The problem, restated

Two scripts hardcoded the module temperature coefficient of Pmax and disagreed:
`pvdaq_daily_srr_probe.py` at **-0.0045**, `washable_share_probe.py` at **-0.0035**, with a
comment in the first asserting they matched. Measured on system 10109 the gap is worth
**1.045 pts** of annual soiling label, against a method-noise SD of 0.72 and a within-cell
between-system spread of 1.41. One unvalidated constant had more leverage on a label than the
physical signal Phase 3 exists to detect, and every recorded label carried it.

## What was done

`src/risk/module_gamma.py` resolves the coefficient **per system** from PVDAQ's own module
metadata (`pvdaq/csv/system_metadata/<id>_system_metadata.json`) against pvlib's CEC module
database (21,535 modules, each with a measured `gamma_r`). Four tiers, each recorded on the
result so a fallback is never mistaken for a lookup:

| tier | share of 1,629 residential systems | what it means |
|---|---|---|
| `cec_module` | **43.3%** | part number matched, disambiguated by nameplate watts |
| `cec_manufacturer` | 35.2% | only a brand recorded; that maker's CEC median |
| `cec_technology` | 2.0% | PVDAQ `type` mapped to a CEC Technology class |
| `fleet_default` | **19.6%** | nothing usable recorded |

Reproduce: `scripts/analyze/pvdaq_module_gamma.py`. Per-system table at
`outputs/soiling/module_gamma_by_system.csv`.

## Answer 1: the recorded labels are biased, not invalidated

Fleet distribution of the resolved coefficient (n = 1,629, all residential <= 15 kW):

```
median -0.00430   mean -0.00422   SD 0.00045
p05    -0.00465   p95  -0.00310   min -0.00592   max -0.00303
```

Distance from the **-0.0045** every recorded label used: median |delta| **0.00020**, p90
0.00094, and only **9.0%** of systems sit 0.0010 or further out.

So the 0.0010 contrast that raised the alarm is a **p91 excursion, not a fleet-typical one**,
and -0.0045 was in fact the right central value: it is the CEC median for Mono-c-Si
(-0.450 %/degC) and Multi-c-Si (-0.4515) alike. **-0.0035 was the wrong number**, a modern
high-efficiency figure (SunPower/Panasonic HIT class) applied to a fleet that is mostly
2010-era x-Si. PVDAQ publishes no module record at all for system 2107, the one system
`washable_share_probe.py` scores, so nothing in its metadata ever supported -0.0035.

## Answer 2: gamma is a real noise term, and it is secondary

`pvdaq_method_noise.py` now runs a **2x2**, irradiance source x gamma, on the same 20 roofs
over the same years. Nothing real differs between arms, so everything that moves is method
error. In the repo's existing convention (SD of a paired difference):

| what varies | SD |
|---|---|
| irradiance source alone (Open-Meteo ERA5 vs PVGIS-NSRDB) | **0.78 pts** |
| gamma alone (resolved vs fleet), Open-Meteo path | 0.46 pts |
| gamma alone, PVGIS path | 0.63 pts |
| **irradiance + gamma, pooled over both diagonals** | **0.83 pts** |

Gamma accounts for **26.4%** of the two contrasts' variance. Real, and smaller than the
irradiance term. (An earlier hand calculation said 32.5%; that used the two *main effects*,
each averaged over the other factor, rather than the contrasts. `pvdaq_method_noise.py` prints
the contrast version and is the definition.)

**Two estimator corrections worth carrying, both of which change the number reported:**

1. **The old 0.72 was measured at n=19 with gamma held fixed.** The same statistic on this
   run's n=20 is **0.78**, so it was mildly optimistic even before gamma entered.
2. **A single corner-to-corner contrast is not a total-noise estimator.** The two diagonals
   of the 2x2 give **0.72** and **0.92** here, because the irradiance and gamma errors partly
   cancel along one of them and add along the other. Quoting either alone is luck. The script
   now pools both, giving 0.83.

A direct per-label sigma, taken as the spread across all four arms rather than as a
difference, is **0.51 pts** — consistent with 0.83/sqrt(2), as it should be. Note the repo's
convention has always quoted the *difference* SD, which is sqrt(2) larger than a per-label
sigma by construction. Keep the conventions apart when comparing to anything older.

## Answer 3: signal-to-noise falls, and stays above 1

```
within-cell between-system spread   1.46 pts   (resolved-gamma refit, n=66 of 75)
  / irradiance-only noise  0.78  =  1.87x      (supersedes the quoted 1.95x)
  / irradiance + gamma     0.83  =  1.76x      <- the honest figure
```

**It does not drop below 1.0.** Phase 3 as specified can still resolve array-level effects
across ~988 systems, but the margin is thinner than advertised, and a per-feature coefficient
should be expected to come back with a wide interval. What it cannot do, and could not do at
1.95x either, is **rank two neighbouring roofs against each other**.

**Done, and the prediction held.** The 1.41 was measured on fleet-gamma labels. Refitting all
75 within-cell systems with resolved gamma (66 pass QC, 9 cells of >= 3) moves the spread to
**1.46 pts**, up 0.05. As expected: on that cohort the resolved coefficients sit close to
-0.0045 (median |delta| 0.00020, only 6.7% beyond 0.0010). 65% of variance remains within
cells. Fits at `outputs/soiling/pvdaq_0a_withincell75_gamma.json`.

## What changed in the code

- `src/risk/module_gamma.py` — new; the resolver and the labelled default.
- `pvdaq_daily_srr_probe.py` — resolves per system; `--gamma fleet` reproduces old labels.
- `pvdaq_method_noise.py` — 2x2 factorial, pooled-diagonal estimator, per-label sigma.
- `pvdaq_within_cluster.py` — **reads** the noise SD from JSON instead of carrying a copy of
  0.72, and says loudly when it falls back.
- `washable_share_probe.py` — resolves through the same module. System 2107 has no module
  record, so it now uses the labelled default -0.0045. Its recorded outputs were produced at
  -0.0035, so it was **re-measured**; see below.

## Answer 4: system 2107's load-bearing conclusion is unchanged

`washable_share_probe.py` re-run end to end at the corrected -0.0045
(`outputs/soiling/washable_share_2107_gamma0045.txt`):

| quantity | recorded, at -0.0035 | re-measured, at -0.0045 |
|---|---|---|
| **CODS annual max soiling ratio** | **1.0000 in 8 of 8 years** | **1.0000 in 8 of 8 years** |
| RdTools YoY degradation | -0.162 %/yr, CI [-0.520, +0.175] | **-0.076 %/yr, CI [-0.408, +0.358]** |
| intervals starting at >= 0.99 | 86% of 44 | 82% of 40 |
| CODS degradation | — | -0.228 %/yr, CI [-1.139, +0.683] |

**The result the docs actually lean on survives untouched.** "No detectable growth of a
standing layer, CODS pinned at 1.0000 in 8 of 8 years" is the claim that kills the uniform
persistent-layer hypothesis, and gamma does not move it at all.

**The degradation figure does move**, from -0.162 to -0.076 %/yr, and every doc quoting
-0.162 is quoting a number produced with the wrong coefficient. Both intervals straddle zero,
so neither is distinguishable from no degradation and the qualitative reading is the same:
far shallower than the Jordan et al. 2016 x-Si field median of -0.5 to -0.6 %/yr. Quote
**-0.076 %/yr [-0.408, +0.358]**.

Note the interval count also moved, 44 to 40, which gamma alone should not do — the probe has
had other edits since the recorded run. Treat the re-measured column as current and the old
column as superseded rather than as a controlled one-variable contrast.

Still do **not** quote the `perfect_clean`/`half_norm_clean` gap (4.826 pts at the corrected
gamma, ~7 before). It is one-sided by construction; trap 8 in the handoff.
- `heavy_rain_mm` and friends — the same bug class, latent in four places, each with a comment
  claiming it matched the others. All now read `recovery.DEFAULT_PARAMS`.
- `tests/test_module_gamma.py` — AST guard over `src/` and `scripts/`: a gamma literal outside
  its owning module fails the suite. Plus value guards on the rain constants.

## The general lesson, since it keeps repeating

Every defect found in this repo across the last two sessions has been **silent and
optimistic**: nothing threw, nothing logged, the suite passed. This one was found by a
sensitivity sweep on a hardcoded constant, not by reading the code — the code looked fine, and
the comment that was wrong looked most reassuring of all. Two of the four scripts touched here
still carried a comment asserting agreement that had never held.

Sweep the constants. Do not trust a comment that says two numbers match; assert it in a test.
