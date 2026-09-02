# Handoff — High-res (21 cm) imagery ingestion for the recall win

**Lane:** Akshitha (permissive-stack migration + reusable infra). **Written:** 2026-07-13.
Companion to [`PERMISSIVE_STACK_MIGRATION.md`](PERMISSIVE_STACK_MIGRATION.md) (the canonical W1–W6
plan) — this doc zooms in on the **imagery-ingestion seam** (its W2) and the scale-mixing guardrail.

## Why this matters

Stage-1's real ceiling is **small-panel recall**, and that's a *resolution* problem, not a model
problem. NAIP is ~60 cm; the Santa Cruz County `Imagery_2025` service is **~21 cm** — ~2.3× finer
linear resolution, so **~5× more pixels on the same panel**. This is plausibly the single biggest
recall lever available, and it feeds directly into the permissive-detector rebuild (you're retraining
on a permissive base anyway — the right moment to make it resolution-aware).

## What already exists

- **`scripts/data/fetch_scc_imagery.py`** — verified SCC endpoint ingestion. Downloads per-tile
  footprints (~21 cm), georeferences from the **request bbox** (not embedded georef), reprojects to
  **EPSG:3857** to align with `data/interim/tile_index.json`. Covers download → georef → reproject
  only. Has a `--dry-run` offline geometry self-test (no network) so the coordinate math is
  verifiable without the live endpoint.
- **`data/interim/tile_index.json`** — sacred (CRS + affine; GSD derives from the affine). Never
  overwrite without reading; new imagery must produce a `tile_index`-equivalent for its tiles.
- The migration plan's target topology: **one scale-robust detector** trained on high-res **and**
  60 cm renders (not two weight sets) — *unless* per-GSD eval proves a split is needed (plan step W3).

## The dual-GSD guardrail — read before you train on mixed resolutions

We have a concrete, expensive precedent: **Phase 4 (Duke 30 cm) integration was abandoned** because
naively mixing GSDs failed. Josh's joint Duke+NAIP run showed the failure signature — **high recall,
very low precision** (the detector over-predicted). So:

1. **`model.val()` mAP50 and recall are NOT sufficient stop-criteria.** Track **precision**
   explicitly; a resolution/scale change that lifts recall while tanking precision is a regression,
   not a win. This is the Phase-4 lesson encoded as a rule.
2. The plan intends a single scale-robust model — that's fine, but **W3's per-GSD eval is
   non-negotiable**: evaluate high-res and 60 cm splits separately before declaring parity. If the
   single model can't hold precision on both, split by GSD.
3. **Vintage noise is a separate trap.** SCC imagery is 2025; our labels are 2022. Panels get
   added/removed in between. `fetch_scc_imagery.py` deliberately stops before re-rendering labels onto
   2025 imagery for exactly this reason — any re-tile + label re-render must be eyeballed per-AOI
   first, or you inject false positives/negatives that look like model error.

## Scaling — the strategic fork (raise with Cameron)

The moment recall depends on SCC's 21 cm service, the product stops being "runs on any NAIP AOI" and
becomes "great in Santa Cruz, NAIP-grade elsewhere." That's fine for the pilot, but it changes the
expansion story: **scaling = acquiring each county's imagery endpoint**, not just pointing at NAIP.
The reusable-infra win here is a **county → tiles → tile_index adapter** that turns "add a new county"
into config, not code — `fetch_scc_imagery.py` is the first instance of that pattern; generalize it
(pluggable service URL, per-county CRS/GSD, the same bbox-derived georef discipline). Decide
consciously whether SolarSoiled is a Santa-Cruz-first product that scales county-by-county, or a
NAIP-national product where 21 cm is a Santa-Cruz-only booster.

> **Update 2026-08-17 (Cameron):** 21 cm is **not** Santa-Cruz-only. At least 13 other US counties
> publish their own aerial surveys at about that resolution, which makes the county-by-county fork
> above a real option rather than a one-county dead end. We have **identified** them, not acquired
> them: no imagery is in hand and nothing has been run. The count is not yet written down anywhere
> else, so pin it before quoting a number. The BBF site's `/solar` coverage ladder was rewritten
> against this on the same date, and `BBF-Website/data/solar.ts` carries the matching note.

## First moves

1. Run the offline self-test: `PYTHONPATH=. conda run -n solar-soiling python
   scripts/data/fetch_scc_imagery.py --dry-run --tiles tile_000000.png` — confirms the georef →
   reproject → bounds math before touching the network.
2. Read W2–W3 of the migration plan; sanity-check where high-res tiles enter the training set and how
   per-GSD eval is wired.
3. Keep precision in every eval table you produce from here on — it's the guardrail metric.
