# Persistent Soiling Screen

## Purpose

The existing Stage 2 model estimates **recoverable, rain-resettable soiling** from
weather and broad location. It cannot identify an individual roof with a persistent
organic or sticky layer because its NREL labels are station-level IWSR observations.

The Persistent Soiling screen is a separate per-array inspection-priority layer. It looks for the
tails: arrays whose physical setting makes persistent contamination plausible, even
when their neighbourhood has ordinary weather-driven soiling.

It does not predict a loss percentage, assert that an array is dirty, or automatically
recommend paid cleaning. Those claims require outcome labels that do not yet exist.

## Current Heuristic

`scripts/analyze/rank_moss_candidates.py` reads detected array polygons and their
lidar-derived roof planes. For every array with a usable plane it measures:

- **Low-tilt score**: flat arrays rank higher because runoff and rain cleaning are
  less effective. Its curve follows Cano's measured rapid drop from horizontal to
  roughly 20-25 degrees, rather than a linear tilt score. It is not calibrated to
  persistent loss.
- **Canopy-exposure score (30%)**: lidar vegetation returns at least 2.5 m above the
  array plane within 9 m. The score uses both the fraction of nearby canopy returns and
  the nearest canopy distance.

The current blend is 70% low tilt and 30% canopy exposure, but those are **explicit
product-policy defaults**, not fitted coefficients. They are passed to the script as
`--tilt-weight` and `--canopy-weight`, and are recorded in every output row. The score
is ranked within an AOI and the highest-scoring 10% are flagged as
`persistent_soiling_top_decile`. This makes it a sampling and field-inspection list,
not a population prevalence estimate.

## Relationship To Regular Soiling

The layers remain separate because they have different meanings and evidence:

| Layer | What it estimates | Evidence | Output |
|---|---|---|---|
| Regular Soiling | Weather-driven dust and ordinary deposits that rain resets | NREL station-year IWSR | calibrated risk/loss estimate |
| Persistent Soiling | Per-array conditions associated with layers rain may not remove | lidar geometry and stated heuristic weights | inspection priority only |

Do not add the Persistent Soiling score to a Regular Soiling loss percentage or present it as an expected dollar
loss until inspections or generation data establish a calibration curve. For a flagged
panel group, the product may show **conditional dollars at risk**: the annual energy value
if a site visit confirms sustained persistent loss at the disclosed 3% annual scenario,
or the same 3% loss sustained across two years, priced with that selected group's
traced capacity and roof yield. This is a scenario value, not a probability-weighted
expected saving, and it does not trigger the Regular Soiling cleaning recommendation.

Run the screen into `persistent_soiling_screen.csv`, then run `solarsoiled recommend`.
The CLI, API `/recommend-quick` response, and dashboard payload join the sidecar by
stable `array_id`. The first validation step is to inspect a blinded sample from the
flagged and unflagged groups, record a consistent persistent-soiling rubric, and then
estimate whether the screen enriches the observed persistent-soiling rate.
