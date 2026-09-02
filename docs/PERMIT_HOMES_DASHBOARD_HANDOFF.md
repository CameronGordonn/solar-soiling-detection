# Hand-off: permitted-homes layer for the public dashboard

**Status: designed, not built.** This is the executable spec for Thread 1 — score the county's
permitted solar homes and show them as their own layer on the public dashboard. Pairs with
`PERMIT_DETECTION_ENRICHMENT.md` (Thread 2, which reuses the geocoded points). Hand this to whoever
picks it up; it should not need re-deriving.

## Goal (locked design)

Show the ~1,300 AOI permitted homes as a **toggleable "permitted homes" point layer**, colored by
their **SOMOSclean** score, visually distinct from the detected-array polygons and their 3-model
view. Real, defensible per-home scores — **no faked or blank scores**.

## Environment

Conda env **`solar-soiling`** (base `python3` lacks geopandas). Run from the repo root:
`PYTHONPATH=. conda run -n solar-soiling python <script>`.

## Inputs

- `data/external/sc_solar_permits.csv` — 7,399 county-wide permit parcels. Filter to the AOI band
  (~1,300). Has a stated kW and a messy `situs_raw`; **no clean address** (city/ZIP often live in
  the description column, and some rows carry two addresses).
- AOI footprint: `outputs/aoi/santa-cruz-outreach-v1/aoi.geojson`.
- Existing geocode helper to adapt: `scripts/outreach/select_targets_from_permits.py` (`--geocode`)
  and `scripts/analyze/ingest_permits.py` for the situs cleanup.

## Step 1 — Geocode (US Census batch geocoder)

- Use the **US Census batch geocoder** (free, bulk-allowed, one request) — **not** Nominatim
  (the existing `--geocode` path uses Nominatim at ~1 req/s; swap the backend, keep the situs
  cleanup).
- ⚠️ **Watch the match rate** — `situs_raw` is messy; validate on a real run. Drop unmatched rows
  rather than guessing; record match count + the unmatched set.
- Output: permit rows + lat/lon points (private, has addresses).

## Step 2 — Score (SOMOSclean physics, the dashboard's `somos_score` model)

- `src/risk/physics_score.py::score_arrays(gdf, as_of=...)` →
  `scoring_method = "somosclean-physics-v1"`. This **is** the dashboard's SOMOSclean model.
- Build the input GeoDataFrame from the geocoded **points** (Point geometry is fine — `score_arrays`
  takes centroids of whatever geometry; CRS-agnostic, reprojects internally).
- ⚠️ **Not a single batched call.** It loops per-home with a `fetch_combined` weather API call each
  (physics_score.py:82-85). ~1,300 homes ≈ ~1,300 fetches, cached in `.cache/soiling`. Expect a
  slow-ish, rate-limit-sensitive run — not instant. Consider chunking / a resumable cache.
- **Score-field mapping for the dashboard:** the dashboard's "SOMOSclean" value = `score_arrays`'
  **`risk_score`** (terminal soiling ÷ `sl_sat`, normalized to [0,1]). Map permit-home
  `risk_score` → the same field the dashboard reads for arrays' `somos_score`. (Also available:
  `soiling_loss_pct`, `soiling_loss_annual_pct`, `eqd`, `last_rain_date`.)

## Step 3 — Private full output

- Write `permit_homes_scored.geojson` with **all** attributes (situs/address, APN, owner, kW, all
  score fields, point geometry). **Private — stays in solar-soiling-ml `outputs/`, never published.**

## Step 4 — Redacted public artifact (SYNC.md gate)

- Emit `permit_homes.js` = `window.PERMIT_HOMES = {FeatureCollection}` where each feature carries
  **only** `{ somos_score (the mapped risk_score), system_kw? }` + **Point** geometry. **No**
  situs/address/owner/APN.
- This mirrors the existing public `arrays_data.js` (`window.FALLBACK_ARRAYS`), which already ships
  precise array polygons + scores but **no** address/owner. Coordinates are not the sensitive piece;
  **address strings are** — keep them out.
- Run the publish through the **`SYNC.md` pre-publish safety gate** (lives in
  `solar-soiling-ml-public`) before anything leaves the private repo.

## Step 5 — Wire the layer into the dashboard (BBF-Website)

- Copy `permit_homes.js` into `BBF-Website/public/tools/` and add a `<script>` include in
  `dashboard.html` alongside `arrays_data.js`.
- In `public/tools/dashboard.js`: add a **toggleable point layer** from `window.PERMIT_HOMES`,
  colored by SOMOSclean on the same scale as the arrays' SOMOSclean, styled **distinctly** from the
  array polygons (points vs polygons; its own legend entry + layer toggle). Don't fold it into the
  detected-array 3-model toggle — it's a separate "permitted homes" layer.
- Reduced-motion safe; keep the tasteful signature interactions (per the x-factor-UI preference).

## Dependencies & gotchas

- API CORS for the Cloudflare host (`*.pages.dev`, `a134df1`) and QR→BBF (`fbb96fe`) are already
  pushed + live — not a blocker for this work.
- Geocode match rate is the main risk to the home count — report it.
- Score run cost/time — see the per-home loop caveat above.

## Verify before calling it done

- Match-rate + scored-count sanity (how many of ~1,300 survived geocode + scoring, NaN scores
  dropped or shown honestly).
- `permit_homes.js` contains **no** address/owner/APN (grep it).
- Layer toggles independently, colors match the SOMOSclean scale, distinct from arrays.
- Live dashboard on the Cloudflare host loads it without CORS errors.
