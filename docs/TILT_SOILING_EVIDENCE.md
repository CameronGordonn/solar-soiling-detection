# Tilt And PV Soiling: Evidence For The Persistent Soiling Screen

## Decision

Tilt is a **causal physical modifier** of particle retention, runoff, and
bottom-edge band formation. It should not be treated as a learned NREL feature:
the NREL tilt field is station metadata and cannot identify a per-roof effect.

Tilt is not, by itself, evidence that an array has persistent contamination. No
study found here estimates a coefficient that trades racking angle against tree
canopy for persistent-soiling loss. Consequently, the Persistent Soiling screen's 70/30 tilt /
canopy blend is a product-policy default, not an evidence-derived model weight.

## Direct Evidence

| Study | Setting and design | Relevant finding | What it supports |
|---|---|---|---|
| [Cano (2011)](https://core.ac.uk/download/pdf/79560086.pdf) | Arizona field exposure, matched clean and unclean mini-modules, 0-40 degrees, Jan-Mar | Mean soiling loss was 2.02% at 0 degrees, 1.05% at 23 degrees, and 0.96% at 33 degrees. | A nonlinear physical prior: most of the tilt effect is near horizontal, then flattens. |
| [Google (2009)](https://green.googleblog.com/2009/07/should-you-spring-clean-your-solar_31.html) | Operational comparison at Google's 1.6 MW Mountain View installation: flat carports beside a sand field versus tilted rooftops | Cleaning the flat carports after 15 months doubled output; another cleaning eight months later added 36%. Google reported little cleaning benefit on its tilted rooftops, where rain was sufficient. | A compelling real-world flat-versus-tilted contrast. It cannot fit a numeric angle coefficient because array location and nearby sand are confounded with tilt. |
| [Mejia and Kleissl (2013)](https://doi.org/10.1016/j.solener.2013.06.028) | 186 California PV sites using production data | Sites below 5 degrees averaged roughly five times the dry-period soiling rate of steeper groups. | The <5 degree tail is operationally important in California. It is recoverable soiling, not proof of persistent material. |
| [Sanz-Saiz et al. (2022)](https://doi.org/10.1016/j.jclepro.2021.130041) | 15-month Madrid rooftop coupon exposure, 8 and 35 degrees | Yearly optical loss: 1.49% at 8 degrees vs 1.04% at 35; fitted rain thresholds: 5.3 vs 3.3 mm/day. | Tilt changes both loading and rain cleaning, but the effect above the near-flat regime can be modest and site-specific. |
| [Islam et al. (2024)](https://doi.org/10.1016/j.seta.2024.103890) | One-year, individually monitored panels in Dhaka, 0-90 degrees | Dry-season soiling rate declined from 1.21%/day at 0 degrees to 0.79%/day at 30 degrees and 0.05%/day at 90 degrees. | The direction is robust; the magnitude is climate-dependent and cannot be copied to coastal California. |
| [Zhao et al. (2021)](https://doi.org/10.3389/fenrg.2021.665411) | Commercial rooftop field observations of bottom-edge bands | Low tilt and the raised module frame create a stagnant bottom edge; light-to-moderate rain can transport material into the band, while heavy rain is needed to clear it. | Low tilt is especially relevant to a separate persistent **edge-band** mechanism. |

## What The Evidence Does Not Support

- A universal rain-cleaning threshold. Published thresholds vary across sites and
  contaminant types; applying one number to every roof is an assumption.
- A linear tilt penalty. Cano and the California data make the low-angle tail more
  important than ordinary residential tilt differences.
- A global 70% tilt / 30% canopy coefficient for persistent loss. There are no
  outcome labels that identify that trade-off.
- Treating the NREL/XGBoost tilt feature as physics. Its values are station-level,
  sparse, and confounded with geography.

## Implementation

`src/risk/tilt_response.py` holds the Cano-based physical factor used by the
recoverable-soiling and edge-band physics paths. The Persistent Soiling inspection screen maps
that same measured curve into a bounded `low_tilt_score`: 1 at 0 degrees and 0 at
35 degrees, with a steep early decline. It does **not** turn that score into a
persistent-loss percentage.

The screen writes `low_tilt_score`, `canopy_exposure_score`, their policy weights,
and the resulting ranked `persistent_soiling_score` separately. This permits a
future inspection dataset to fit or reject the weighting rather than hiding it in
a single opaque score.

## Recommended Research Design

Keep two persistent mechanisms separate during validation:

1. **Bottom-edge mineral band:** low tilt, rainfall transport/flush pattern,
   module orientation, and electrical topology. This can occur without a tree.
2. **Biological or sticky contamination:** canopy/leaf exposure, time-of-wetness,
   slow drying, and low tilt. Here angle is a modifier, not sufficient evidence.

Collect blinded roof-level inspections from high and low values of each component.
Then fit a calibrated model to observed persistent material or cleaned-vs-unclean
generation recovery. Until then, use the score only to prioritize inspection.
