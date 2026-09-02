# Permit-driven detection enrichment

**Thread 2.** Thread 1 scores the permitted homes for the public dashboard (a real per-home
SOMOSclean score; see the dashboard "permitted homes" layer). Thread 2 turns the *same* geocoded
known-solar points around and uses them as **detector fuel** — an independent ground-truth signal
the detector never trained on.

The county solar-permit registry (`data/external/sc_solar_permits.csv`, 7,399 parcels county-wide)
is a list of homes that **almost certainly have a panel** — sourced from building permits, fully
independent of our NAIP detector. Once geocoded to points, that list is leverage in four ways,
below.

> Grounding: read `outputs/economics/permit_detection_recall.md` first. The raw AOI overlap is
> **36 / 764 permitted-and-detected**, but that 5% is *not* a recall number — the coverage
> denominator (what was actually tiled + imaged) is unknown, and permits only start in 2016 while
> CA's residential boom was 2013–2016. Everything below exists to convert that messy overlap into
> a defensible metric and a prioritized work queue.

## 1. Independent recall audit

The detector has never seen the permit registry, so permit points are clean ground truth for
recall — *if* we fix the denominator.

- Intersect geocoded permit points with the **`tile_index` footprint** (the area actually run
  through detection), not the AOI bbox. Permitted parcels that were never imaged cannot be
  detected and must be excluded from the denominator.
- Recall = (permit points inside the tiled footprint that have a detected array within tolerance)
  / (all permit points inside the tiled footprint).
- Match tolerance: point-in-array-polygon, else nearest-array within ~15 m of the parcel centroid
  (geocode + roof-offset slack). Record the match distance so we can sanity-check the tolerance.
- Output: a real recall number with a CI, plus the confusion split below.

## 2. Split the misses by permit year

A "permitted but undetected" parcel is one of three things; the permit **year** is the cheapest
discriminator:

- **Real miss** (Stage-1 relabel target, risk M1) — permit year is within the NAIP imagery
  vintage, the parcel is inside the tiled footprint, yet no array was detected. These are genuine
  recall failures worth labeling.
- **Post-imagery install** — permit year is *after* the NAIP capture date. The panel legitimately
  isn't in our imagery; not a detector failure. Exclude from recall, but flag for re-imaging.
- **Outside tiled footprint** — already removed in §1, but year still helps audit edge cases.

Cross old install years (≤ imagery vintage) against the misses to isolate the true-miss set. That
set, not the raw 728-parcel "permitted-but-undetected" queue from the recall doc, is the labeling
budget.

## 3. Targeted auto-labeling

For each **real miss** with a usable geocode, generate a pre-seeded label candidate:

- Pull the NAIP chip at the permit point and emit a weak box/centroid seed (the permit asserts a
  panel is there — high prior). Route through the existing relabel/validation queue rather than
  trusting it blind: a human confirms or rejects, but starts from a hint instead of a blank tile.
- This is far cheaper than blind tile review and directly raises Stage-1 recall where we know we're
  weak. Feed confirmed labels back into the next training matrix
  (`scripts/detect/train_experiment_matrix.py`).
- Keep auto-seeds tagged with their provenance (`source=permit_seed`) so they never silently
  inflate eval metrics.

## 4. Where-to-tile prior

Permit density is a free, panel-correlated prior for **where to spend the next tiling/imagery
budget**:

- Rasterize permit-point density across the county; high-density cells we haven't tiled yet are the
  highest-yield next AOIs (most real arrays per tile-dollar).
- Combines with the §2 "post-imagery install" flags: areas with many recent permits and stale NAIP
  are prime re-imaging candidates.
- Output a ranked cell list to drive the next `tile_index` expansion.

## Inputs / artifacts

- In: `data/external/sc_solar_permits.csv`, geocoded permit points (Thread 1 output), the
  `tile_index` footprint, detected `arrays.geojson`, NAIP chips.
- Reuse: `scripts/analyze/ingest_permits.py`, `scripts/analyze/join_permits_to_arrays.py`,
  `scripts/outreach/select_targets_from_permits.py` (`--geocode`).
- Out: corrected recall metric (+CI), the three-way miss split, a provenance-tagged auto-label
  batch, and a ranked tile-expansion list.

## Sequencing

Thread 1 (geocode + score for the dashboard) produces the geocoded points this thread consumes, so
land Thread 1 first. None of §1–§4 changes the public dashboard; this thread is internal detector
improvement.
