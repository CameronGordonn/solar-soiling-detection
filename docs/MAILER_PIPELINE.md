# How the Mailer Works — from aerial pixel to postcard in a mailbox

> **Vintage note (added 2026-08-31).** Every **334** in this document is the **60cm
> `santa-cruz-outreach-v1` pilot**, which is two generations superseded. The live AOI is the
> 21cm `santa-cruz-w2-21cm` run: **3,362 array polygons across 1,865 sites**. The 334-vs-7,399
> permit-gap reasoning below still stands as reasoning; the detection count in it does not.
> Current counts: [CANONICAL_NUMBERS.md](CANONICAL_NUMBERS.md#aoi-and-product).


A complete, plain-language walk-through of how a house gets chosen, found, printed on a
postcard, and mailed through Lob — what data feeds each step, what year that data is from,
where the pipeline can improve, and how we go from **334 detected panels** in the 60cm pilot to the
**7,399 solar parcels** the county's public records already show.

---

## TL;DR

1. We tile recent **NAIP aerial imagery** of a Santa Cruz area-of-interest (AOI) and run a
   **YOLO detector** that outlines rooftop solar arrays → **334 arrays** in the pilot AOI.
2. Each array is **scored** (soiling risk + an estimated annual dollar loss) from weather,
   air-quality, location, and the array's geometry.
3. `select_targets.py` ranks arrays by **recoverable net dollars**, applies an optional
   residential size filter, then **finds the house**: it spatially joins each array to the
   **county parcel map** to get the owner name + mailing address (falling back to
   reverse-geocoding the rooftop's lat/lon).
4. `generate_mailers.py` prints a **6×4 postcard PDF** per array — the dollar headline, a
   soiling-risk badge, BBF branding, a QR code to that array's dashboard page, and the
   mailing address.
5. `mail_via_lob.py` hands each PDF + address to **Lob.com**, which prints and mails it.
   Dry-run validates addresses and estimates cost (~$1.50/card); `--send` commits.

**The big opportunity:** detection found 334 arrays inside one ~22 km box. The county's
**building-permit records already list 7,399 unique solar parcels (2016–2026)** — roughly
**20× more**. We don't need the ML model to *find* those homes; we already have their parcel
IDs. The fastest "way larger sweep" is to **target the permit registry directly** and use
detection to enrich and size each array, not to discover it. Details in §6.

---

## 1. Detection — finding the panels in the sky

**Script:** `scripts/detect/` (train/infer/export) → `outputs/aoi/<partner>/arrays.geojson`
**Input data:** NAIP aerial imagery (USDA, ~0.6 m / 60 cm per pixel), tiled to 640×640 px
chips. Geospatial metadata (CRS + affine transform per tile) lives in
`data/interim/tile_index.json` (249 tiles in the pilot).
**Model:** YOLOv11 polygon segmentation. Production weights `R2-cameron-20260509`
(val **SAHI F1 0.570** at the calibrated conf=0.20/iou=0.50, re-baselined 2026-07-06;
`model.val()` mAP50 ≈ 0.26 — SAHI F1 is the tracked metric).

What happens: each tile is run through the detector; detected array polygons are converted
back to world coordinates (lat/lon) using the tile's affine transform and written to
`arrays.geojson` with an `array_id`, a polygon, and a detection `confidence`. For the pilot
AOI `santa-cruz-outreach-v1` (bounds ≈ **−122.10, 36.85 → −121.85, 37.05**, a ~22 km box over
the Live Oak / Pleasure Point / east-Santa-Cruz side), this yields **334 arrays**.

> **Imagery vintage: NAIP 2023** (`scripts/data/tile_naip_image.py` requests `year=2023`).
> So "what panels exist" is **as of 2023** — anything permitted in 2024–2026 (a large share of
> the recent boom) is not yet in the pixels. Refreshing to the newest NAIP is a concrete
> improvement (§7).

---

## 2. Scoring — how dirty, and how many dollars

**Script:** `scripts/analyze/build_risk_features.py` → `scripts/predict/predict_risk.py`
→ `outputs/aoi/<partner>/risk.geojson`
**Input data:**
- The array's own **geometry** (area_m², perimeter, compactness, neighbor density).
- **Weather + air quality** for the rooftop's location, as a rolling 7/30/90-day and 365-day
  window ending at the scoring date — so this is **recent (≈ last 12 months) weather**, e.g.
  2025–2026 for a score run today (precipitation, wind, humidity, PM2.5/PM10).
- Static **location features** (elevation, land cover, distance to highways/agriculture).

**Output per array:** a `risk_score` (0–1) from the XGBoost model (`run_optionb`; quote **0.712**, not 0.728 — see [CANONICAL_NUMBERS.md](CANONICAL_NUMBERS.md)
spatial-CV AUC) and, when the regression head is used, a `pred_loss_pct` (estimated annual
% energy lost to soiling). `area_m²` is also carried through.

This is where "soiling" becomes a number. The honest caveat: the model **ranks** soiling
well, but absolute %/$ are not yet field-validated (risk **M1**), and Santa Cruz is a *low*-
soiling coastal area, so residential dollar figures here are modest.

---

## 3. Choosing the house — ranking by recoverable dollars

**Script:** `scripts/outreach/select_targets.py` → `outputs/outreach/<partner>_topN.csv`

For every array in `risk.geojson` it computes the **net dollars cleaning would recover**, via
the shared economics engine (`src/risk/economics.py`):

- `system_kw` = a **permitted kW** if we have one for that parcel, else estimated from the
  detected area (`area_m² ÷ 5.67`).
- `loss_pct` = the model's `pred_loss_pct` if present, else the physics `soiling_loss_annual_pct`,
  else a `risk × 15` fallback.
- `expected_net_usd` = `array_recommendation(loss_pct, system_kw)` — the best of
  *no-clean / light-pro / professional* after subtracting the (per-panel) cleaning cost.

Then it:
1. **Filters by size** if `--min-kw` / `--max-kw` are passed (e.g. a homeowner mailer targets
   ~4–15 kW so postcards go to houses, not commercial roofs).
2. **Filters to actionable** arrays (`action == clean` or `risk_score ≥ 0.5`).
3. **Ranks by `expected_net_usd`** (the dollars at stake), risk as a tiebreaker — *not* raw
   risk. On the pilot this floats real high-value roofs to the top instead of tiny systems.
4. Takes the **top N**.

This is the answer to "how does it *choose* a house": **the house with the most recoverable
cleaning dollars wins**, subject to the size filter.

---

## 4. Finding the address — turning a rooftop polygon into a mailing label

Still in `select_targets.py`. A detected array is just a polygon at a lat/lon — it has no
owner or street address. Two mechanisms, in order:

1. **County parcel join (preferred).** The script downloads/caches the **Santa Cruz County
   Assessor parcel map** (`data/external/santa_cruz_parcels/`) and does a spatial join: the
   array's centroid is matched to the parcel polygon that contains it, yielding the **owner
   name + mailing address** straight from the assessor record. Pass `--parcel-shp` to use a
   local copy.
2. **Reverse-geocode fallback.** If a parcel isn't matched, it reverse-geocodes the rooftop
   lat/lon via **Nominatim (OpenStreetMap)** to a street address (no owner name, rate-limited
   to 1 req/s).

It also builds the **QR deep link** (`dashboard.html?id=<array_id>`) so the postcard links to
that specific array's page. Output columns: `array_id, expected_net_usd, system_kw, loss_pct,
risk_score, area_m2, owner_name, mailing_address, lat, lon, qr_url`.

> ⚠️ **Address quality is the current weak point.** Reverse-geocoding often returns a
> street-only result ("Younglove Avenue") with no house number — which **Lob will reject as
> undeliverable**. The parcel shapefile gives precise owner + mailing addresses and is the fix
> for any real send (§7).

---

## 5. Printing the card — what goes on the postcard

**Script:** `scripts/outreach/generate_mailers.py` → `outputs/outreach/mailers_*/<array_id>.pdf`
A **two-page** 6"×4" postcard PDF per array (page 1 = front, page 2 = back; 150 DPI proof /
300 DPI print, with print bleed). Lob requires a separate front + back, so each PDF carries both.

**Theme:** matched to the betterbehaviorfoundation.com site palette (`public/tools/styles.css`
`:root`) — forest-green background `#1a2e1a`, bright-green accent `#3d9e3d`, hero-green `#7ec87e`
for the $ figure. (The site's CSS vars are *named* navy/teal/gold but their values are greens.)
Print font is Helvetica as a neutral stand-in for the site's web-only Geist.

**Front (marketing):**
- **Brand:** "BETTER BEHAVIOR FOUNDATION", website `betterbehaviorfoundation.com`, phone `(831) 216-8749`.
- **Dollar headline:** "Your solar panels may be losing **$X per year** to dust and pollen…".
  The $ is `_dollars_lost(risk, area)` = `(area ÷ 5.67) × 5.5 sun-hours × 365 ×
  recovery% × $0.28/kWh`, where `recovery%` is **1.75 / 3 / 5%** by risk bucket
  (calibrated to the UCSD 2013 cleaning study).
- **Risk badge:** "HIGH" / "ELEVATED" soiling (kept warm amber/red so the warning reads).
- **QR code:** deep-links to the array's dashboard page. No address on the front.

**Back (address side, `_draw_back`):** white background (so Lob's black recipient address + IMB
barcode stay legible), return address top-left (from `SOLARSOILED_RETURN_ADDRESS`), a short
message in the left column. The **right half and bottom-right are left blank** — that's the zone
Lob stamps the recipient address + barcode into.

> ⚠️ **One inconsistency to fix:** the card's dollar figure uses the simpler *risk → recovery*
> formula (electricity rate **$0.28**, 5.5 sun-hours), while targeting now ranks on the richer
> **net-$ economics**. The postcard should show the *same* number the model ranked on (§7).

---

## 6. Mailing it — Lob

**Script:** `scripts/outreach/mail_via_lob.py`
For each row in the targets CSV it finds `mailers_dir/<array_id>.pdf`, **splits the 2-page PDF
into front + back buffers** (`_split_front_back`), parses the destination address, and calls
**`lob.Postcard.create()`** with `front`, `back`, `size="4x6"` (Lob's name for a 6×4 landscape
card), `use_type="marketing"` (both required by Lob's API), and an **`Idempotency-Key` header**
= `"{mailers_dir.name}:{array_id}"` so an accidental/partial re-run is deduped by Lob rather than
re-charged. Requires `LOB_API_KEY` in `.env` (`test_…` is free and doesn't mail; `live_…` mails and
bills ~$0.95/card — measured on the `mailers_v13` live send, $47.52/50).

⚠️ Two dry-run modes (see §9): `mail_via_lob.py` **without** `--send` only parses addresses
locally — it never contacts Lob. The real pre-flight is a **`test_` key + `--send`**: free, mails
nothing, but actually creates each postcard, runs Lob's deliverability check, and leaves a
previewable proof in the dashboard. Run order:

```bash
PYTHONPATH=. conda run -n solar-soiling python scripts/outreach/build_detected_targets.py --top 50
PYTHONPATH=. conda run -n solar-soiling python scripts/outreach/generate_mailers.py \
    --targets outputs/outreach/detected_targets.csv --out-dir outputs/outreach/mailers_v13
PYTHONPATH=. conda run -n solar-soiling python scripts/outreach/mail_via_lob.py \
    --targets outputs/outreach/detected_targets.csv --mailers-dir outputs/outreach/mailers_v13 --send  # test_ key = free dry run
#   swap to a live_ key (+ set the real return address) and re-run to actually mail
```

---

## 7. What year is every piece of data?

| Data | Vintage | Used for |
|---|---|---|
| NAIP aerial imagery | **2023** (USDA ~0.6 m) | Detecting panels |
| Detector training labels | NAIP hand-labels + Duke/Bradbury **2014–2015** | The YOLO model |
| Weather (precip/wind/humidity) | **Rolling last ~12 months** to score date (≈2025–26) | Soiling score |
| Air quality (PM2.5/PM10, Open-Meteo) | **~2022→present only** (no history before) | Soiling score |
| Static features (elevation, land cover, OSM) | Current | Soiling score |
| Building-permit registry | **2016–2026** (8,780 permits, 7,399 parcels) | Sizing + the larger sweep |
| Parcel / assessor map | Current county data | Owner + address |
| Cleaning cost + economics model | **2026** market estimates | Net-$ ranking + card |
| Electricity rate / sun-hours on card | $0.28/kWh, 5.5 h (assumptions) | Card dollar figure |

---

## 8. The gap — 334 detected vs 7,399 permitted (and how to close it)

**The numbers.** Detection found **334 arrays** inside the pilot AOI (one ~22 km box). The
county's public building-permit records — which we already parsed into
`data/external/sc_solar_permits.csv` — contain **8,780 solar permits across 7,399 unique
parcels, 2016–2026**. Public records show **far more** solar than the ML pass surfaced.

**Why the gap exists (causes, with the actual join numbers):**
1. **Footprint.** The detection AOI is a small slice of Santa Cruz County. Most of the 7,399
   permitted parcels are simply *outside the box we tiled.* Biggest factor.
2. **Imagery age.** NAIP is **2023**; ~40% of the 2016–2026 permits are 2024–2026 and won't
   appear in 2023 pixels yet.
3. **Recall.** Within the AOI the detector misses some arrays (small panels, shadows, 0.6 m).
4. **Low direct overlap today.** A spatial join of the 334 detected arrays → parcels (APN) →
   permits matches **only 45 of 334** to a solar permit (24 with a stated kW). The detected set
   and the permitted set are largely *disjoint here* — a mix of the above plus APN/format and
   coverage gaps. Treat permits and detection as **complementary**, not the same population.

### How to get a *way* larger sweep — prioritized

**A. Target the permit registry directly (the big pool — needs one new piece).**
We have **7,399 known solar parcels** (APN + the permit's raw situs text), so we don't need ML
to *find* them. The missing piece is a clean mailing address: the downloaded parcel map has
**only APN + geometry (no owner/address)**, and the permit `situs_raw` is unstructured (owner +
address + description mixed, with some PO boxes). So the sweep needs an **address-resolution
step** — parse/clean situs into a street address (or geocode the parcel centroid), drop
non-mailable rows — then score each via **`scripts/predict/score_address.py`** (geocode → real
local weather → soiling loss% → net-$) and net-$ rank. That turns the pool from 334 into
**thousands**. *Build the address-resolver, and the 20× sweep is in reach.*

**B. Use detection to enrich, not discover.** Run the detector over each permitted parcel to
confirm the array still exists, measure its **footprint/size** (better than the permit kW for
soiling geometry), and catch arrays that have *no* permit (older or unpermitted). The permit
list and the detector become a cross-check: permitted-and-detected (confirmed), permitted-not-
detected (recall gap or post-imagery install), detected-not-permitted (find new ones).

**C. Expand the detection AOI to the whole county.** Tile all of Santa Cruz County NAIP (the
pipeline is county-agnostic by construction) so detection coverage matches the permit registry.

**D. Refresh NAIP to the latest vintage** so 2024–2026 installs are visible.

**E. Improve recall** (the R0 retrain + relabel already on the Stage-1 roadmap) so within-
footprint misses drop.

**The punchline:** the permit data *is* the larger sweep. Wiring **permit registry → address
→ `score_address` → net-$ rank → mailer** is the highest-leverage change, and most of the
pieces already exist.

---

## 9. Where the pipeline can be improved (running list)

**Done in this pass (2026-06):** ✅ card dollar figure now uses the **same economics** the
targeting ranks on (`generate_mailers` reads `system_kw`/`loss_pct`); ✅ the overstated
`risk × 15` loss fallback is now physics-consistent **`risk × 8`** (single source:
`economics.RISK_TO_LOSS_PCT`); ✅ NAIP vintage confirmed (**2023**); ✅ a permit-registry sweep
foundation shipped (`scripts/outreach/select_targets_from_permits.py`); ✅ **geocoder
deliverability fix** — `_geocode` no longer drops a result when Nominatim returns a ZIP with
`city=None` (it falls back to the ZIP-only form, still Lob-verifiable), so far more permitted
parcels survive to a mailable address.

### Test-50 send — current batch is `mailers_v13` (detected-rooftop path)

> **Path changed (2026-06-25).** The send no longer comes from forward-geocoding permit situs
> (the old `mailers_v10` route, kept below for history). The current deliverable is built from
> **detected arrays, with mailing addresses resolved via the county parcel service** — see
> `scripts/outreach/build_detected_targets.py` ("smart APN reader", commit `c912cce`). The QR on
> each card deep-links to the BBF dashboard (`betterbehaviorfoundation.com/tools/dashboard.html?id=<array_id>`).

Current generation (50 cards):

```bash
# 1. Detected arrays → addresses via county parcels → outputs/outreach/detected_targets.csv
#    (array_id, expected_net_usd, system_kw, loss_pct, risk_score, area_m2, apn, year,
#     owner_name, mailing_address, qr_url)
PYTHONPATH=. conda run -n solar-soiling python scripts/outreach/build_detected_targets.py --top 50

# 2. Render the 50 PDFs (+ all_mailers.pdf) — QR already points at the BBF dashboard
PYTHONPATH=. python scripts/outreach/generate_mailers.py \
    --targets outputs/outreach/detected_targets.csv --out-dir outputs/outreach/mailers_v13
```

**State (2026-06-30): ✅ SENT LIVE — 50/50, 0 skipped, 0 failed.** The `mailers_v13` batch was
mailed via a `live_` key on 2026-06-30 (all 50 `psc_…` IDs returned). **Actual cost $47.52**
(~$0.95/card — the earlier ~$1.50/card was an over-estimate), funded from **Lob prepaid credits**
(Auto Pay is intentionally *not* configured, so no card auto-charges — mail holds until credits
cover it; this is the spend gate). The `live_` key was **rotated/deleted after the send**.
**⚠️ Do NOT re-run `--send` on this batch** — the idempotency key (below) dedupes a same-dir
re-run, but a genuinely new campaign must render into a **new** mailers dir. Prior to the live send,
the test-mode dry run passed 50/50; `mailers_v13/` holds 50 two-page green-themed cards (front +
back), all confirmed deliverable by Lob's test-mode `Postcard.create`.
`lob` 4.5.4 installed in the `solar-soiling` conda env; `.env` (gitignored) has the test
`LOB_API_KEY`. **Decision: mail with NO postal return address** — Lob accepts a postcard with no
`from_address` (verified), so `_parse_return_address` returns None when `SOLARSOILED_RETURN_ADDRESS`
is unset/placeholder and `mail_via_lob` omits `from_address`. Instead the card directs people to
"Call or text Craig at (831) 216-8749". Fixes/decisions this pass: two-sided card (Lob requires
front+back), `size="4x6"`, `use_type="marketing"`, green site theme, **situs-address +
owner-occupied targeting** (all 50 are Santa Cruz owner-occupied homes), front decluttered (name
once + phone + mission), back redesigned as a left brand panel, **501(c)(3) badge** (status via
fiscal sponsorship under Ecologistics), and foundation email on the back.

⚠️ **`mail_via_lob.py` WITHOUT `--send` does NOT contact Lob** — it only parses addresses locally.
The real pre-flight is **`test_` key + `--send`** (free, mails nothing, creates previewable proofs
+ runs Lob's deliverability check):

```bash
# NOTE: run this in an ACTIVATED env, not `conda run` — `conda run` doesn't attach a TTY, so the
# live y/N prompt hits EOFError. Use:  conda activate solar-soiling && PYTHONPATH=. python …
PYTHONPATH=. python scripts/outreach/mail_via_lob.py \
    --targets outputs/outreach/detected_targets.csv --mailers-dir outputs/outreach/mailers_v13 --send
#   test_ key = free proof; swap .env to a live_ key and re-run for the live send.
#   Actual live cost was ~$47.52 for 50 (~$0.95/card). No return address needed (placeholder in
#   .env → Lob mails without one). With a live_ key the script prompts y/N before charging.
#   Each card carries an Idempotency-Key = "{mailers_dir.name}:{array_id}" so a re-run is deduped
#   by Lob, never double-charged. Funding = Lob prepaid credits (no Auto Pay card).
```

`verify_addresses.py` gives a standalone deliverability table but only with a **live** key — its
`lob.USVerification` returns canned `undeliverable` on a test key, so the test-mode `--send` above
is the authoritative check until the live key is in.

**Who we mail (situs + owner-occupied).** `build_detected_targets.py` now addresses the **situs**
(the property the panels sit on — always inside the Santa Cruz AOI via `SITEADD/SITCITY/SITZIP`),
**not** the owner's mailing address, which for absentee owners is often out of state (Denver,
Detroit, …). It also keeps **only owner-occupied parcels** (`HOMEOWNER='HOE'`) by default —
absentee parcels are rentals/LLCs where the resident isn't the decision-maker, so the soiling
pitch rarely converts. Pool: of 257 resolved parcels, **144 owner-occupied** with a mailable situs
(87 absentee skipped) — ample headroom for 50. Pass `--include-absentee` to override.

**Guaranteeing a full 50 deliverable (drop-and-backfill).** Build a **buffer** above 50
(`build_detected_targets.py --top 80`), run the test-mode `--send` over it, drop any `FAIL` rows,
and keep the top 50 by `expected_net_usd`. The current `mailers_v13` was finalized this way — with
the situs/owner-occupied set, **all 80 buffer cards were deliverable (0 dropped)**.

Older mailer dirs (`mailers`, `mailers_v2`–`mailers_v12`, `mailers_test*`) and the permit-geocode
CSVs (`test50_permit_geocoded.csv`, `permit_targets_geocoded.csv`) are superseded scratch — safe to
clear once the live send is confirmed.

<details><summary>Retired: permit-situs forward-geocode path (mailers_v10)</summary>

The earlier route forward-geocoded permit situs because the AOI parcel file had only `APN` +
geometry (no owner/address). `build_detected_targets.py` now joins detected arrays to the county
parcel *service* for owner+address directly, so this is no longer used.

```bash
PYTHONPATH=. python scripts/outreach/select_targets_from_permits.py \
    --top 70 --min-kw 4 --max-kw 15 --geocode \
    --out outputs/outreach/permit_targets_geocoded.csv     # → 56 mailable of 70
# trimmed to the 50 that parse with house#+ZIP → outputs/outreach/test50_permit_geocoded.csv
PYTHONPATH=. python scripts/outreach/generate_mailers.py \
    --targets outputs/outreach/test50_permit_geocoded.csv --out-dir outputs/outreach/mailers_v10
```
</details>


- **Use parcel data for every send** — reverse-geocoded street-only addresses get rejected by
  Lob. Download the assessor parcel shapefile and pass `--parcel-shp`.
- **Unify the card dollar figure with the targeting economics** — the postcard should print the
  same net-$ the model ranked on, not the older risk→recovery estimate.
- **Replace the `risk × 15` loss fallback** with the regression head's `pred_loss_pct` once it
  beats the naive baseline (needs the historical-AQ fix + M1 site validation).
- **Confirm + refresh NAIP vintage**, and record it in the manifest so "what exists" has a date.
- **Scale targeting off the permit registry** (§8A) instead of the 334-array AOI.
- **Close the feedback loop** — `POST /feedback` exists; wire actual post-clean results back so
  net-$ and recovery assumptions get calibrated.
- **Honest dollars** — Santa Cruz is low-soiling; the residential card figures are modest and
  unvalidated. The bigger money (and a cleaner pitch) is inland / commercial (per the economics
  work), so consider where the mailer is pointed.
