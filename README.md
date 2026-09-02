# SolarSoiled

End-to-end geospatial ML system for detecting rooftop solar arrays in public aerial imagery and scoring per-array soiling risk. **Two-stage architecture**: RF-DETR polygon detection + SAM2 masks on county aerial imagery (Stage 1) → XGBoost risk model trained on weather reanalysis, air quality, land use, and structural features (Stage 2), feeding a fully-sourced net-dollar economics chain. Designed county-agnostic; runs on any AOI with public aerial coverage.

**Why it matters**: Solar panel soiling (dust, pollen, aerosols) reduces energy output measurably, and nobody knows which roofs it is worth acting on. Answering that from public data, without site visits, is the problem this system solves end to end: raw GeoTIFF → array polygons → per-array risk → dollars.

**What we found when we grounded the dollars, stated up front because it shapes everything below:** across 1,865 detected Santa Cruz sites, **zero show a positive expected net from a cleaning** on recoverable soiling, at any electricity rate up to $0.70/kWh. The constant that had been carrying the product (`recovery_frac`, the share of a year's loss one wash buys back) was assumed at 0.90 and **measured at 0.045**. That result then survived its own largest counter-correction: measuring tariff vintage raised the value of a lost kWh by 2.6× and the AOI annual loss total by **156%**, and the verdict did not move. Full audit: [`docs/ECONOMICS_GROUNDING_20260809.md`](docs/ECONOMICS_GROUNDING_20260809.md); the short version is [`docs/CRAIG_BRIEF_2026-08-19.md`](docs/CRAIG_BRIEF_2026-08-19.md).

This is a negative commercial result carried openly rather than buried. **The detection stack is the asset** — it passed its shipping gate and it is licence-clean — and the honest product today is a *diagnosis* ("here is what your array loses, and no, do not pay to clean it"), not a cleaning lead list.

> **New to the team? Start with [`docs/ONBOARDING.md`](docs/ONBOARDING.md)** — env setup, the data/secrets
> handoff, and what to read in what order (it takes under an hour to get productive). Ownership + how we
> work: [`docs/TEAM.md`](docs/TEAM.md). This README is the *what/why*; those are the *how-to-start*.

**Current metrics** — honest, methodology explained in [Results](#results):
- Stage 1: **GA gate PASSED 2026-08-07.** `rfdetr_w2_20260807` scores **tile-level box-F1 0.8260** on test (P 0.850 / R 0.803, 95% CI [0.798, 0.853], n_gt 585), clearing all three conditions — F1 ≥ 0.75, CI-lower ≥ 0.70, recall ≥ 0.70 — with confidence tuned on val and **frozen before test was touched**. RF-DETR @728 + SAM2, Apache-2.0. ⚠️ **Every SAHI-F1 figure in this repo's history, including R2's 0.570, is measured on different labels, different imagery and a different architecture, and is not comparable to this number.**
- Stage 2: **GA-ready within its training geography** — `run_optionb`, all three gates clear on honest evaluation: spatial-CV AUC **0.712** (re-measured 2026-08-06; the older 0.728 is `run_optionb`'s stored figure and does not reproduce), pooled out-of-year AUC **0.710** (rolling leave-one-year-out, n=891, CI [0.676, 0.742]), calibration retained out-of-year. Further tuning is below measurement resolution. **It does not generalize to an unseen region**: pooled out-of-region AUC 0.677, worst region 0.548. See [docs/CANONICAL_NUMBERS.md](docs/CANONICAL_NUMBERS.md).
- Stage 2, the limit worth knowing: the labels are **station-level**, so the model is valid for *level* and **not for ranking homes against each other**. Across 1,710 residential sites, array size (from detection) drives **86.7%** of the per-home dollar spread, roof orientation **11.1%**, and the risk model **2.2%**. See [`docs/SOILING_LEVEL_INVESTIGATION.md`](docs/SOILING_LEVEL_INVESTIGATION.md).

**Live product** — the dashboard + calculator now live in the **BBF site** (`../BBF-Website`, Cloudflare Pages), folded into its Tools section; the old GitHub Pages landing is retired:
- Site → `https://betterbehaviorfoundation.com` (✅ **live** — DNS cutover to Cloudflare Pages completed 2026-06-30)
- Dashboard → `/tools/dashboard` — **3,362 array polygons across 1,865 sites**, 3-model risk comparison, QR deep-link, energy calculator
- Breakeven calculator → `/tools/calculator`
- API at `https://solarsoiled-api.onrender.com` (use `/health/live` to verify — root returns 404 by design)

Every output carries `model_version`, `beta`, and `known_limitations` — quality metadata is in the output contract, not a footnote.

---

## Architecture

```
County aerial imagery (21cm) / NAIP GeoTIFF (0.6m GSD)
       │
       ▼
  Tiling + CRS preservation          640×640 PNG chips; affine + CRS logged in tile_index.json
       │
       ▼
  Roboflow annotation pipeline        polygon segmentation labels (whole-array convention)
       │
       ▼
  RF-DETR @728 training               Apache-2.0 detector; dataset data/yolo/scc21
  (permissive stack)                  gate = tile-level box-F1 via eval_tile_f1.py
       │
       ▼
  Production inference                whole tile → 2×2 grid of 640px chips → one pass per
  scripts/detect/rfdetr_infer.py      chip → NMS seam merge at IoU 0.55
       │
       ▼
  SAM2 mask stage                     prompt box shrunk 15%; area is the product, and this
  (on by default)                     is what makes area unbiased (median area/GT = 1.01)
       │
       ▼
  Per-detection RCA                   one row per TP/FP/FN with size, density, edge, confidence;
  scripts/detect/per_detection_rca.py failure-mode buckets → targeted Roboflow relabeling
       │
       ▼
  GeoJSON polygon export              georeferenced array footprints, CRS round-tripped end-to-end
       │
       ▼
  Feature engineering                 ERA5 weather · CAMS PM2.5/PM10 · ESA WorldCover ·
  scripts/analyze/build_risk_features.py  OSM proximity · Kimber IWSR physics prior (as feature)
       │
       ▼
  XGBoost soiling-risk model          10km spatial GroupKFold · isotonic calibration ·
  scripts/predict/train_risk_model.py --holdout-year temporal validation
       │
       ▼
  solarsoiled CLI                     tile / detect / score / recommend / run / eval subcommands
  Per-AOI outputs + manifest.json     model_version + inputs_hash + beta flag on every artifact
       │
       ▼
  Partner dashboard + outreach        Leaflet risk map · 3-model comparison · QR postcards
  ../BBF-Website/public/tools/        physical-to-digital loop: postcard → dashboard → action
```

---

## Key Engineering Choices

**The gate is an executable script, not a sentence.** `scripts/detect/eval_tile_f1.py` *is* the Stage 1 gate definition: tile-level box-F1 at IoU ≥ 0.50, micro-averaged, measured through the real production path (whole tile → chip grid → NMS seam merge). Three things it fixes that prose gates kept getting wrong: confidence is **tuned on val and frozen** before test is scored (an earlier manifest tuned on test and spent the held-out split); matching is on **boxes, not masks**, so the gate is invariant to whether SAM2 is running and you can tell which stage regressed; and the 95% CI **bootstraps over tiles, not objects**, because arrays within a tile are correlated and object-resampling reports an interval that is too narrow.

**Compare paired models with a paired test.** W2 vs W1 marginal CIs overlap heavily and each point estimate sits inside the other's interval, so eyeballing them says "noise" — and that is the wrong test, because both were scored on the same 49 tiles. Bootstrapping the per-tile *difference* gives +0.0246 F1, 95% CI [+0.0051, +0.0441], P(W2 > W1) = 0.993. Doubling the training set produced a real gain that the naive read would have discarded.

**Prompt-box quality, not mask post-processing, is what makes area correct.** Area feeds the m² → kW → $ chain, so a mask that grabs roof is a pricing error. Shrinking the SAM2 prompt box 15% gives median IoU 0.844 and **0% roof-grab**, against 0.509 and 43.3% for boxes alone. Two more principled alternatives (negative points, mask containment) were implemented, measured, and **rejected** — containment made it worse everywhere. The negative results are kept reproducible behind flags rather than deleted.

**10km spatial GroupKFold to prevent geographic leakage.** Soiling rate is spatially autocorrelated — neighboring weather stations share climate signal. Random CV leaks across spatial neighbors and inflates AUC. We cluster the NREL stations (255 in the source CSVs, 257 in the training matrix) into 10km bins via KMeans and hold out whole bins. The gap between spatial-CV AUC (0.712) and random-CV AUC (~0.74) quantifies the leakage that naive splitting would mask. **10 km clusters do not test regional transfer** — that needs whole-region holdout, which the model fails (0.677 pooled).

**Warm-start from prior best checkpoint, not COCO weights.** R0 retraining warm-starts from the best available checkpoint rather than COCO pretrained weights. The prior checkpoint learned to detect arrays at 0.6m GSD — a signal that hand labels at source resolution can't teach from scratch reliably (small arrays are frequently under-labeled at 60cm). Warm-starting preserves this prior while labels improve iteratively.

**Kimber IWSR physics prior as a feature, not a label source.** The Kimber 2007 Incident Weighted Soiling Rate model gives a physics-derived soiling estimate per station. Rather than using Kimber rates as training labels (which would cap model accuracy at the physics model's error floor), we include the Kimber-derived rate as one input feature. XGBoost can learn to up-weight this prior where NREL station density is sparse and discount it where empirical data is dense.

**Isotonic calibration for actionable risk scores.** Raw XGBoost predicted probabilities are miscalibrated for sparse geographic data — model confidence doesn't match empirical outcome rates. Isotonic regression (monotone, non-parametric) is fit on a held-out calibration fold post-training. Calibrated probabilities feed directly into the cleaning recommendation engine, where overconfidence would cause systematically early or late recommendations.

**Every constant in the dollar chain is sourced or explicitly marked UNSOURCED.** The chain was audited end to end in 2026-08 after a single unmeasured constant (`recovery_frac = 0.90`, measured 0.045) turned out to be carrying the entire product. `RISK_TO_LOSS_PCT = 8.0`, which multiplied a *calibrated classification probability* by 8, was removed outright and replaced by conformalised XGBoost quantile regression on real loss percentages. Electricity rates are bill-reconciled to ±$0.22/month over 11 real PG&E bills, each component carrying its CPUC sheet citation. What remains unsourced is named in code and in the docs rather than quietly assumed.

**Per-detection RCA harness for targeted label correction.** Instead of bulk-reviewing tiles, inference runs at low confidence (conf=0.05) and emits one row per TP/FP/FN with size, density, edge-proximity, and confidence metadata. Failure-mode buckets (alone-tile FPs, small FNs, high-confidence errors) drive targeted Roboflow relabeling batches. This approach diagnosed that 65 of 360 FPs were concentrated on 20 GT-empty tiles — likely real arrays the original 60cm labels missed, not model hallucinations — informing relabeling priority without wasted review cycles.

---

## Datasets

| Dataset | Scale | Source | Role |
|---|---|---|---|
| **SCC 2025 aerial (21cm)** | 976 chips (680/100/196), 3,894 polygons | Santa Cruz County MapServer | **Primary detection domain** (`data/yolo/scc21`) — all 249 tiles relabeled at full resolution |
| NAIP Santa Cruz | 248 tiles, ~1,000 labeled arrays, 0.6m GSD | USDA NAIP via Roboflow | Legacy 60cm domain; labels not comparable to the 21cm set |
| **USGS 3DEP lidar** | per-array tilt + azimuth, 3,068 / 3,362 arrays (91.3%) | USGS 3DEP, free | Roof geometry → per-roof plane-of-array irradiance. Median plane-fit residual 4 cm |
| **CaliforniaDGStats** | 7,536 AOI interconnections; 8,547 with reported tilt | californiadgstats.ca.gov, free | Tariff vintage (AOI is 90.1% legacy NEM) + independent tilt validation |
| NREL PVDAQ system 2107 | 893 kW, 8.08 years, revenue-grade meter + class-A pyranometer | OEDI open data lake, free | Persistent-soiling probe and wash-test power analysis |
| Duke / Bradbury | 601 source images, ~19,400 array polygons, 0.3m GSD | Duke Energy / Figshare | Joint-curriculum experiment — **abandoned** (scale-mixing hurt); R2 is NAIP-only |
| NREL soiling database | 255 stations, ~15 years panel-level soiling measurements | NREL public API | Stage 2 training labels |
| Open-Meteo ERA5 reanalysis | Historical weather per station (temp, humidity, wind, precip) | Open-Meteo OPeNDAP (1940–present) | Stage 2 weather features |
| CAMS global atmosphere | PM2.5, PM10 per station | Copernicus / MERRA-2 OPeNDAP (1980–present) | Stage 2 air quality features |
| ESA WorldCover 2021 | 10m land cover classification | ESA | Stage 2 land use features |
| OpenStreetMap | Road network, agricultural land boundaries | Overpass API | Stage 2 proximity features |

All external data fetches are disk-cached. Weather and air quality data streams via OPeNDAP — no bulk download required.

---

## Results

| Stage | Metric | Why this metric | Value |
|---|---|---|---|
| Stage 1 | **tile-level box-F1 @ IoU 0.50** | The gate. Production path, conf tuned on val and frozen, micro-averaged | **0.8260** ✓ (`rfdetr_w2_20260807`, 95% CI [0.798, 0.853], n_gt 585) |
| Stage 1 | test precision / recall | Recall is the funnel — a missed array is a missed lead | P **0.850** / R **0.803** ✓ |
| Stage 1 | SAM2 mask area vs GT | Area feeds the dollar chain, so bias here is a pricing error | median area/GT **1.01**, roof-grab **0%** |
| Stage 2 | **Spatial-CV AUC** | 10km GroupKFold prevents geographic leakage; conservative *within-geography* estimate | **0.712** ✓ (0.728 = `run_optionb` as stored, does not reproduce) |
| Stage 2 | **Pooled out-of-year AUC** | Rolling leave-one-year-out across 15 panel years (n=891); temporal generalization | **0.710** ✓ (95% CI [0.676, 0.742]) |
| Stage 2 | Out-of-year calibration | Each training-year isotonic map applied to its held-out year | Brier 0.218 < 0.250 base-rate ✓ |

**Gates**: Stage 1 GA requires **all three** of tile-F1 ≥ 0.75, CI-lower ≥ 0.70, recall ≥ 0.70 — **all clear**. Stage 2 GA: spatial-CV ≥ 0.70 **and** pooled out-of-year ≥ 0.70 **and** calibration retained — **all clear**. Both ship `beta` until the registry is flipped.

`production` resolves to **`rfdetr-w2-20260807`** (RF-DETR + SAM2, Apache-2.0), flipped 2026-08-23. The live dashboard's 1,865 sites were already produced by it, so the alias describes what ships rather than what preceded it. R2 stays registered as `stage1-60cm-legacy` for the 60cm path; it is AGPL and evaluation-only. **The live constraint is geographic, not licensing**: W2 is gated on Santa Cruz County 21cm imagery, and outside that AOI the county service has no coverage. See [`docs/COMMERCIALIZATION.md`](docs/COMMERCIALIZATION.md).

---

## Product

### Live surfaces

| Surface | URL | Status |
|---|---|---|
| BBF site (hosts the tools) | `https://betterbehaviorfoundation.com` | ✅ Live on Cloudflare Pages (2026-06-30) |
| Homeowner dashboard | `/tools/dashboard` | ✅ Live (in `../BBF-Website/public/tools`) |
| Breakeven calculator | `/tools/calculator` | ✅ Live |
| FastAPI backend | `https://solarsoiled-api.onrender.com` | Live (free tier, ~30s cold start) |

### Dashboard

Interactive Leaflet map of **3,362 Santa Cruz array polygons (1,865 sites)** with three-tab model switcher — XGBoost ML (0.712 CV AUC), SOMOSclean physics (ENEL exponential accumulation), and Kimber 2007 (linear PM2.5 deposition + rain reset). Features:

- **QR deep-link**: physical postcard → `dashboard.html?id=<array_id>` → auto-select array with pulse animation
- **Array detail panel**: risk score gauge, area/tilt/confidence stats, per-model comparison table
- **Energy calculator**: client-side JS; inputs system_kw + electricity_rate + sun_hours → annual kWh loss + dollar loss
- **Recalculate**: calls `/recommend-quick` on Render backend to update cleaning window when homeowner adjusts last-cleaned date

### API (beta)

All responses carry `{model_version, beta, known_limitations}`.

| Endpoint | Description |
|---|---|
| `GET /health/live` | Process up |
| `GET /health/ready` | Models loaded, API keys present |
| `POST /jobs` | Submit async AOI detect + score job |
| `GET /jobs/{id}` | Poll job status + result URL |
| `GET /results/{partner_id}` | Fetch cached AOI results |
| `POST /feedback` | Submit post-clean energy reading (free; feeds model retraining) |
| `GET /recommend-quick` | Re-run cleaning recommendation from cached risk scores |
| `GET /events/{job_id}` | SSE stream for real-time job progress |

### Outreach pipeline (Santa Cruz pilot)

Physical-to-digital loop: risk scores → postcard → QR code → personalized dashboard → cleaning action.

- **Target selection** (`scripts/outreach/build_detected_targets.py`) — detected arrays ranked by recoverable net-$ (`expected_net_usd`) with a residential size filter, **owner-occupied only**, situs address via county parcel join. (`select_targets.py` is the older `0.6·risk + 0.4·confidence` path.)
- **PDF mailers** (`scripts/outreach/generate_mailers.py`) — 6×4" postcard per array with risk stats, dollar loss estimate, QR code → personalized dashboard URL
- **Lob integration** (`scripts/outreach/mail_via_lob.py`) — print-and-mail API; `--send` commits, with a per-card `Idempotency-Key` so re-runs are deduped (never double-charged). **✅ `mailers_v13` sent live 50/50 on 2026-06-30 — $47.52 total (~$0.95/card), funded via Lob prepaid credits (no Auto Pay card).**
- **Alt-model scoring** (`scripts/outreach/score_alternative_models.py`) — scores all arrays with SOMOSclean + Kimber from SQLite cache; no API calls required

---

## What's Built

**Stage 1 — Detection** (shipping path)

- RF-DETR @728 training on `data/yolo/scc21` (Apache-2.0) — the permissive stack, gate passed at tile-F1 0.8260
- Production inference (`scripts/detect/rfdetr_infer.py`) — whole tile → 2×2 grid of 640px chips → one pass per chip → NMS seam merge at IoU 0.55. Run **without** `--no-sam` for anything product-facing
- SAM2 mask stage with a 15%-shrunk prompt box — median mask IoU 0.844, median area/GT 1.01, 0% roof-grab
- Gate harness (`scripts/detect/eval_tile_f1.py`) — *is* the gate definition; emits `gate.json`, `threshold_sweep.csv`, `detections.json` with a tile-bootstrapped CI

_Legacy 60cm path, retained for baseline evaluation only and **AGPL, not shipping**:_

- YOLOv11 polygon segmentation training with YAML-driven experiment matrix (`scripts/detect/train_experiment_matrix.py`) — named experiment cuts from a single config, results auto-namespaced under `runs/segment/<name>/`
- Whole-tile inference with optional SAHI supplementary pass (`scripts/detect/infer.py`) — `--sahi` runs whole-tile first then adds non-overlapping SAHI detections for small-panel recovery
- Per-detection RCA harness (`scripts/detect/per_detection_rca.py`) — one row per TP/FP/FN with size, density, edge-proximity, confidence; `--summarize` produces `failure_modes.json`
- Failure-mode bucket overlays (`scripts/labeling/bucket_overlays.py`) — renders top-N PNGs per bucket (alone-tile FPs, small FNs, high-conf errors, or arbitrary `--bucket-expr`)
- Ramp eval helper (`scripts/detect/ramp_eval.py`) — per-curriculum-step eval, appends to `ramp_curve.csv`, prints HALT on NAIP regression exceeding threshold
- Domain equivalence baseline (`scripts/data/compare_naip_duke_distributions.py`) — KS tests + distribution plots for NAIP vs Duke label conventions

**Stage 2 — Risk Model**

- Feature engineering from five external sources with per-station alignment and disk caching (`scripts/analyze/build_risk_features.py`, `scripts/analyze/build_static_features.py`)
- XGBoost training with spatial GroupKFold, isotonic calibration, `--holdout-year` temporal validation (`scripts/predict/train_risk_model.py`)
- Run comparison table (`scripts/analyze/compare_soiling_runs.py`) — side-by-side metrics across named training runs
- NREL validation (`scripts/predict/validate_against_nrel.py`) — independent check against held-out station measurements

**Product / CLI**

- `solarsoiled` CLI — `tile / detect / score / recommend / run / eval` subcommands; `run --aoi <bbox-or-geojson>` chains all stages; per-AOI output namespace under `outputs/aoi/<partner_id>/`
- Model registry (`models/registry.yaml`) — resolves named aliases (`production`, `latest`, …) and ad-hoc `.pt` paths; `model_version` tagged on every output
- Manifest contract (`src/solarsoiled/manifest.py`) — every artifact-producing stage writes a sibling `manifest.json` with inputs hash, model SHA256, beta flag, known limitations
- HTML eval report (`solarsoiled eval --report`) — single-file report with PR curve, F1-colored sweep table, failure-mode tables, base64-embedded overlay PNGs; no inference re-run required
- FastAPI backend (`src/solarsoiled/api.py`) — async job queue, SSE streaming, `POST /feedback`, `GET /recommend-quick`; deployed on Render.com

---

## What's In Progress

- **Tariff vintage for the 1,310 city-jurisdiction sites.** A legacy-NEM home is worth **2.78×** more per lost kWh, which is the sharpest per-home targeting signal in the project, and it comes from a public-records join rather than from modelling. The county archive will never cover these — the split is jurisdictional, not age-related (city APN books: 0.0% county coverage; county books: 62.5%). Records request drafted at [`docs/outreach/`](docs/outreach/).
- **The moss / lichen channel.** Rain removes dust but not moss, lichen, algae or bird droppings, so that entire loss channel sits **outside** what the NREL labels, `sl_sat`, and the measured 0.045 recovery describe. It is the only remaining route to per-home differentiation and to closing the economics gap. The deciding variable (edge-band thickness, ~30 mm) is **sub-pixel at our best 6cm imagery**, so the next step is ground photography, not more compute. Written stop rule that would kill the thesis: [`docs/AOI_CLEANING_TARGETING_PLAN.md`](docs/AOI_CLEANING_TARGETING_PLAN.md).
- **Paper.** Roof-geometry results are written up and ready for hand-off: [`docs/HANDOFF_roof_geometry_for_paper.md`](docs/HANDOFF_roof_geometry_for_paper.md).

_(Done and **not to be revisited**: Stage 1 and Stage 2 model work are both finished — further chasing is below measurement resolution in both. **Duke joint-curriculum, MERRA-2, mask containment, and SAM2 negative points were each tried and dropped** — measured as neutral or harmful. The Santa Cruz mailer `mailers_v13` (50 cards) went out live 2026-06-30.)_

---

## Quick Start

### Docker

```bash
# CPU build, full pipeline (~10 min cold). Includes RF-DETR + SAM2.
# Size depends on your storage driver: 10.1 GB local (containerd), 5.88 GB in CI
# (overlay2). See the Dockerfile for why, and measure on your own host.
make docker-build
make docker-smoke        # asserts the image can import the detector — see below

# API-only image: no rfdetr/sam2, ~6.4 GB smaller than the full build.
docker build --build-arg EXTRAS=api -t solarsoiled:api .

# GPU build (CUDA 12.1)
docker build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu121 -t solarsoiled:gpu .

# End-to-end on an AOI. --user keeps artifacts owned by you, not root; it needs
# the USER/HOME env baked into the image on 2026-09-01 (torch's getpass.getuser()
# raises KeyError for a UID absent from the container's /etc/passwd without it).
docker run --rm --user "$(id -u):$(id -g)" \
  -v $(pwd)/models:/app/models \
  -v $(pwd)/runs:/app/runs \
  -v $(pwd)/outputs:/app/outputs \
  -v $(pwd)/.cache:/app/.cache \
  -v $(pwd)/data/external:/app/data/external \
  --entrypoint solarsoiled solarsoiled:local run \
    --aoi "-122.05,36.90,-121.85,37.05" \
    --weights production \
    --soiling-model soiling_production \
    --last-cleaned 2026-01-01 \
    --partner-id smoketest
```

**Mount all five volumes.** `runs/` carries the Stage-2 model and `data/external/` the static
features; omitting either fails at the scoring stage, not at startup. `production` resolves to
`rfdetr_w2_20260807.pth` — a `.pth`, not a `.pt`; the legacy ultralytics `.pt` files are AGPL and
eval-only. The default `CMD` is the API server, so pass `--entrypoint solarsoiled` for CLI runs.

**`make docker-smoke` is the check that matters.** Until 2026-09-01 the image installed only the
`api` extra, so it built fine, started fine and served `--help` fine, then died on
`ModuleNotFoundError: rfdetr` the moment anyone ran the shipping detector — because rfdetr and sam2
are lazy imports. A smoke test built on `--help` would have passed for the three months it was
broken. `docker-smoke` imports rfdetr and sam2 explicitly and resolves `production` through the
registry, and needs no weights or data.

**Verified in the container, 2026-09-01** (3 cached 21cm SCC tiles): detect produced 109 polygons
via `rfdetr-w2-20260807` + SAM2, **byte-identical across two independent runs**, and Stage-2 scoring
of the 3,362-array production matrix came out **bitwise identical to the host** (max |Δrisk_score| =
0.0) despite the container running sklearn 1.9.0 against a calibrator pickled under 1.7.2. The one
link not exercised is the weather-feature fetch, blocked by the Open-Meteo hourly quota — **on the
host too**, so it is a quota limit, not a container one. When that quota trips, the run dies at
sklearn's isotonic stage with a bare `ValueError: Found array with 0 sample(s)` *after* detection has
already completed; look above it for `Open-Meteo hourly quota exceeded`.
See [`docs/ONBOARDING.md`](docs/ONBOARDING.md) §1 for the runnable path.

The `.cache/` mount (~440 MB after warm-up) persists weather data across runs, and also holds the
HuggingFace cache — `HF_HOME` points into it so SAM2's ~900 MB checkpoint downloads once rather
than on every `--rm` run. Outputs land in `outputs/aoi/<partner_id>/`.

### Local (dev)

> A fresh clone is **code only** — the data, model weights, and secrets are gitignored
> ([`DATA.md`](DATA.md) lists them; get them from Cameron per [`docs/ONBOARDING.md`](docs/ONBOARDING.md)).
> Once the env + data are in place, `PYTHONPATH=. conda run -n solar-soiling python scripts/predict/holdout_ci.py`
> reproduces the Stage-2 GA number in seconds — a good "is my setup working" check.

```bash
cd setup/ && bash setup_conda.sh
conda activate solar-soiling
pip install -e .   # registers the solarsoiled CLI

# Full pipeline on one AOI
solarsoiled run \
  --aoi "minx,miny,maxx,maxy" \
  --weights production \
  --soiling-model runs/soiling/run_latest/model.ubj \
  --last-cleaned 2026-01-01 \
  --partner-id smoketest

# Re-run only score + recommend on cached upstream artifacts
solarsoiled run --aoi <…> --weights <…> --soiling-model <…> \
  --last-cleaned 2026-01-01 --partner-id smoketest \
  --skip-tile --skip-detect

# Reproduce the Stage 1 gate (the number of record)
PYTHONPATH=. python scripts/detect/eval_tile_f1.py \
    --weights models/rfdetr_w2_20260807.pth --run-name gate_check

# Reproduce the economics verdict
PYTHONPATH=. python scripts/analyze/rate_sensitivity.py --show-stack
PYTHONPATH=. python scripts/analyze/recovery_calendar.py        # the measured 0.045
PYTHONPATH=. python scripts/analyze/rebuild_aoi_economics.py --aoi santa-cruz-w2-21cm

# Stage 2
PYTHONPATH=. python scripts/predict/train_risk_model.py --run-name run_latest

# Legacy 60cm YOLO path (AGPL, evaluation only)
PYTHONPATH=. python scripts/data/audit_dataset.py --config configs/yolo/dataset_audit.yaml
PYTHONPATH=. python scripts/detect/train.py --model models/yolo11s-seg.pt --epochs 50
```

Model weights are gitignored — place `.pth` / `.pt` files in `models/` manually.

---

## Documentation

| Doc | Purpose |
|---|---|
| [docs/ONBOARDING.md](docs/ONBOARDING.md) | **Start here** — env, data/secrets handoff, read order |
| [docs/TEAM.md](docs/TEAM.md) | Ownership + how we work (branches, PRs, CODEOWNERS, parallel work) |
| [docs/README.md](docs/README.md) | Full doc index — which doc is canonical for each question |
| [docs/PERMISSIVE_STACK_MIGRATION.md](docs/PERMISSIVE_STACK_MIGRATION.md) | Stage-1 AGPL-escape plan (RF-DETR + SAM2), W1–W6 |
| [docs/HANDOFF_dashboard_conversion.md](docs/HANDOFF_dashboard_conversion.md) · [docs/HANDOFF_imagery_ingestion.md](docs/HANDOFF_imagery_ingestion.md) | Pick-up-cold workstream handoffs (product surface · 21cm imagery) |
| [docs/SOILING_STAGE2_GUIDE.md](docs/SOILING_STAGE2_GUIDE.md) | Stage 2 risk model — features, training, validation |
| [docs/NAIP_ROBOFLOW_WORKFLOW.md](docs/NAIP_ROBOFLOW_WORKFLOW.md) | Full tile → label → train → export reference |
| [docs/ROBOFLOW_IMPORT_RUNBOOK.md](docs/ROBOFLOW_IMPORT_RUNBOOK.md) | Step-by-step Roboflow import + retrain checklist |
| [docs/PHASE1_HANDOFF.md](docs/PHASE1_HANDOFF.md) | Stage 1 active retrain runbook — R0 iterations, label batches, gates |
| [docs/CRAIG_BRIEF_2026-08-19.md](docs/CRAIG_BRIEF_2026-08-19.md) | **The business case in one document**, written to be read cold |
| [docs/ECONOMICS_GROUNDING_20260809.md](docs/ECONOMICS_GROUNDING_20260809.md) | The dollar-chain audit — every constant, sourced or marked UNSOURCED |
| [docs/AOI_CLEANING_TARGETING_PLAN.md](docs/AOI_CLEANING_TARGETING_PLAN.md) | Moss/lichen thesis, substring shade physics, ranked experiments + stop rule |
| [docs/SOILING_LEVEL_INVESTIGATION.md](docs/SOILING_LEVEL_INVESTIGATION.md) | Why the risk model can't rank homes within an AOI (structural, not fixable by features) |
| [docs/HANDOFF_roof_geometry_for_paper.md](docs/HANDOFF_roof_geometry_for_paper.md) | Roof tilt/azimuth from 3DEP lidar — method, two-method validation, paper notes |
| [docs/COMMERCIALIZATION.md](docs/COMMERCIALIZATION.md) | Licensing — AGPL resolved by the backbone swap; what is still exposed |
| [docs/PRODUCT_VISION.md](docs/PRODUCT_VISION.md) | Strategy, beta/GA contract, customer-readiness arc |
| [docs/Q2_PLAN.md](docs/Q2_PLAN.md) | Current roadmap and workstream status |
| [docs/HYPERPARAM_PLAYBOOK.md](docs/HYPERPARAM_PLAYBOOK.md) | Training hyperparameter rationale |

---

## Stack

Python 3.11 · PyTorch 2.0+ · **RF-DETR (Apache-2.0)** · **SAM2** · XGBoost · GDAL / rasterio · laspy (3DEP lidar) · pvlib · RdTools · Roboflow · Open-Meteo ERA5 · CAMS / MERRA-2 · ESA WorldCover · OpenStreetMap Overpass · FastAPI · Typer · ReportLab · Lob.com · Leaflet · Cloudflare Pages · Render.com · Docker · scikit-learn (isotonic calibration, GroupKFold)

**Licence: Apache-2.0.**

[YOLOv11 / ultralytics](https://docs.ultralytics.com/) and SAHI are AGPL-3.0. Since 2026-09-01 they live in an optional `legacy` extra: not installed by default, and **absent from the deployed API image**. Nothing on the shipping path links them, which is what makes the Apache-2.0 licence above honest rather than aspirational. Verified by measurement — `import solarsoiled.cli` loaded 98 ultralytics submodules before that change and loads 0 after.

They remain available for evaluating the legacy 60cm baseline: `pip install -e ".[legacy]"`. See [`docs/COMMERCIALIZATION.md`](docs/COMMERCIALIZATION.md).
