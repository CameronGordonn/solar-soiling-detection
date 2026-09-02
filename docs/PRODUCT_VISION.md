# SolarSoiled — Product Vision

North-star doc for where SolarSoiled is going, how the pieces fit together as a product, and which workstreams run in parallel. Complements the operational runbooks in `docs/`; does not duplicate them.

---

## Product thesis

SolarSoiled detects rooftop solar arrays from public aerial imagery, scores each array's soiling risk from environmental and structural context, and recommends when to clean. Revenue lives in the recommendation: detection is the funnel, risk is the hook, the cleaning window is what operators pay for.

---

## Current state

Live status lives in [docs/Q2_PLAN.md](Q2_PLAN.md) — see the "Project status" tables there. This table is a pointer to the surfaces; flag values mirror the canonical four (`shipped` / `in progress` / `queued` / `beta`).

| Surface | Pointer | Status |
|---|---|---|
| Stage 1 — detection | [docs/NAIP_ROBOFLOW_WORKFLOW.md](NAIP_ROBOFLOW_WORKFLOW.md), [docs/PHASE1_HANDOFF.md](PHASE1_HANDOFF.md) | `beta` — 56% mAP50 NAIP Santa Cruz; target 70%+ |
| Stage 2 — risk model | [docs/SOILING_STAGE2_GUIDE.md](SOILING_STAGE2_GUIDE.md) | `beta` — **0.712 spatial-CV** ✓ (re-measured; 0.728 is `run_optionb` as stored and does not reproduce), pooled out-of-year AUC **0.710** ✓, calibration retained ✓. All three gates cleared 2026-07-05. **MERRA-2 is exhausted, not pending** — `run_merged_merra2` hurt performance (0.716 CV / 0.666 holdout); do not retry. Known limit: does not generalize out-of-region (0.677 pooled) |
| Customer-readiness CLI + API | `src/solarsoiled/cli.py`, `src/solarsoiled/api.py`, [CLAUDE.md](../CLAUDE.md) | Tier 0 + Tier 1 `shipped`; Tier 2 `in progress`; Tier 3 `shipped` (FastAPI + Render deploy + `eval --report`); Tier 4 `shipped` (dashboard + outreach) |
| Site (hosts the tools) | `../BBF-Website` (Cloudflare Pages) | `in progress` — tools folded into the BBF site; live at `betterbehaviorfoundation.com` after the DNS cutover; Render API backend wired |
| Dashboard | `../BBF-Website/public/tools/dashboard.html` → `/tools/dashboard` | `shipped` — Leaflet map of **3,362 Santa Cruz array polygons across 1,865 sites**; 3-model tab switcher (XGBoost / SOMOSclean / Kimber); QR deep-link; energy calculator |
| Homeowner outreach | `scripts/outreach/`, `outputs/outreach/` | `in progress` — test-50 batch (`mailers_v13` / `detected_targets.csv`) ready; Lob send gated on live key |
| Operational runbook | [CLAUDE.md](../CLAUDE.md) | Source of truth for commands, paths, dataset status |

---

## Parallel tracks

Three workstreams run concurrently. **Track C does not wait on Track A** — we ship a beta API with honest quality metadata so design partners can integrate while the model improves.

| Track | Scope | Ship criterion | Rationale |
|---|---|---|---|
| **A — Model quality** | Stage 1 joint training (Duke 160px + NAIP), pseudo-labeling round, Stage 2 year-holdout validation | mAP50 ≥70% for Stage 1; year-holdout pass for Stage 2 | Sets the GA bar |
| **B — Visibility surface** | BBF site Tools section (`../BBF-Website/public/tools/`): homeowner dashboard, breakeven calculator, 3-model comparison | Live site + dashboard with working QR deep-link | `shipped` (built); live at the DNS cutover |
| **C — Beta API** | `/jobs`, `/health`, `/feedback`, `/results`, `/recommend-quick`, SSE streaming; deployed on Render | Reachable beta endpoints behind an API key | `shipped` — deployed at `https://solarsoiled-api.onrender.com` |
| **D — Physical outreach** | Top-50 postcards with QR codes → personalized dashboard; close the physical-to-digital loop | 50 cards in homeowners' hands | `in progress` — scripts 20–23 built; dry-run validated; awaiting send go/no-go |

### Beta/GA honesty model

- Every API response includes `{model_version, map50_current, beta: true | false, known_limitations: [...]}`.
- Landing page states current detection accuracy explicitly — a trust asset, not a liability.
- The `beta` flag flips to `false` when Track A clears its bars.
- Early users see real metrics, so their feedback is calibrated and their trust is earned.

---

## API surface (v0 beta contract)

Contract sketches, not final specs. Every response carries the metadata block described above.

### `POST /detect` — async job

- Input: `{aoi: bbox | scene_id, vintage: "2014-2015" | "latest"}`. Vintage is mandatory because Bradbury-derived training data is 2014–2015.
- Returns `202 Accepted` with a `job_id`.
- `GET /detect/jobs/{job_id}` returns status + result URL on completion.
- Result payload: GeoJSON of array polygons + metadata block.
- Async because county-scale detection is hours; sync is wrong by construction.

### `POST /risk` — sync

- Input: `{polygons: GeoJSON}` OR `{detect_job_id: str}` (avoid round-tripping large GeoJSON when we already have it).
- Returns per-array `{risk_score, model_version, features_version, scored_at, beta, known_limitations}`.
- Default returns the most recent daily batch score. `force_rescore=true` triggers a synchronous rescore (premium, metered).

### `POST /recommend` — sync, subscription-gated

- Input: polygons + `{last_cleaned: date, operator_constraints?: {...}}`.
- Returns `{window_start, window_end, expected_recovery_pct: [low, high], confidence, rule_fired, model_version, beta}`.
- v1 is rule-based (see next section). v2 is ML-based.

### `GET /recommend-quick` — sync, dashboard-facing (shipped)

- Input: `array_id`, `last_cleaned` (date), `partner_id`.
- Loads cached `risk.geojson` for the partner, re-runs `recommend_cleaning()`, returns updated `window_start`, `confidence`, `rule_fired`.
- Used by the homeowner dashboard when a user adjusts "when did you last clean?" without a full re-score.
- Deployed at `https://solarsoiled-api.onrender.com/recommend-quick`.

### `GET /health/live` and `GET /health/ready`

- `/health/live` — process is up.
- `/health/ready` — models and calibrators are loaded, external API keys (NREL, Open-Meteo) present, downstream dependencies reachable.

### Cross-cutting

- API-key auth from day one.
- Per-scan metering on `/detect` and `/risk`.
- `model_version` in every response (XGBoost + YOLO weights both tagged).

---

## Cleaning recommendation engine — staged

### v1 — rule-based

- Rule: `risk_score > T AND forecast_rain_7d < R_mm AND days_since_clean > D` → recommend window `[today+1, today+forecast_dry_stretch_end]`.
- Inputs available today: risk_score (Stage 2), Open-Meteo forecast (already integrated), `last_cleaned` from client.
- Output must include `confidence` (bucketed from risk_score) and `rule_fired` (which threshold dominated) — operators need to understand *why*.
- `expected_recovery_pct` is a **range**, not a point estimate. v1 doesn't have enough ground truth for a credible point.

### v2 — ML-based

- Optimize `expected_recovery_kWh − cleaning_cost` over a rolling calendar.
- Depends on the feedback loop below to train against.

---

## Track B — Site + Dashboard

The tools now live in the **BBF site** (`../BBF-Website`, Cloudflare Pages static export), served
under `/tools/` and live at `betterbehaviorfoundation.com` after the DNS cutover. The retired
`solarsoiled-landing/` GitHub Pages surface has been removed. `../BBF-Website/public/tools/` contains:
- `dashboard.html` — interactive homeowner dashboard: Leaflet map of **3,362 Santa Cruz array polygons (1,865 sites)**, 3-model tab switcher, array detail panel (QR deep-linkable via `?id=<array_id>`), energy/money calculator
- `breakeven.html` — branded cleaning-breakeven calculator (seasonal + persistent soiling)
- `arrays_data.js` — pre-embedded GeoJSON with `risk_score` (XGBoost), `somos_score` (SOMOSclean physics), `kimber_score` (Kimber 2007) per array; no backend call on load
- `mailer_homes.js` — QR-landing points for the test-mailer recipients (score + system size only; no address)

---

## Dashboard (shipped)

Leaflet map, static GeoJSON embedding, no backend required on load. Features:
- **Three-model tab switcher**: XGBoost ML (0.712 CV AUC) | SOMOSclean physics (ENEL exponential accumulation model) | Kimber 2007 (linear PM2.5 deposition + rain reset)
- **QR deep-link**: physical postcard → `dashboard.html?id=<array_id>` → auto-select + highlight array with pulse animation
- **Array detail panel**: risk score gauge, area/tilt/confidence stats, "Compare all models" table, "Recalculate" form calling `/recommend-quick`
- **Energy calculator**: client-side JS; inputs system_kw + electricity_rate + sun_hours → outputs annual kWh loss + dollar loss + "recover $X/year if cleaned today"

---

## Data feedback loop

Operators cleaning panels and reporting post-clean output is the long-term moat.

- Schema: `{array_id, cleaned_at, pre_clean_kWh_7d, post_clean_kWh_7d, notes?}`.
- Submission endpoint is **free** — incentive to contribute.
- Each submission becomes a training row for the v2 recommendation model and a calibration check for Stage 2.
- **`POST /feedback` is live** (`src/solarsoiled/api.py`). Records land in `outputs/aoi/<partner_id>/feedback.json`. `scripts/predict/train_risk_model.py` picks them up automatically on next retrain via glob of `outputs/aoi/*/feedback.json` — partner clean events flow directly into the model with `feedback_weight_multiplier=3.0`.

---

## Freshness policy

- Risk scored **daily** via a cron job over active AOIs.
- `/risk` returns the most recent batch score by default.
- `force_rescore=true` triggers a synchronous rescore, premium-tier, counts against metering quota.
- Every response includes `scored_at` so clients can judge staleness.

---

## Monetization — product-decision guidance

Not a commitment; shapes API and output design.

- `/detect` + `/risk`: per-scan metered. Free tier (e.g., N scans/month) for design partners.
- `/recommend`: subscription-gated — per-MW annual or per-site.
- `force_rescore`: premium add-on.
- Feedback submission: free, always.
- **Consumer→cleaner marketplace (active wedge, post-2026-06):** the durable revenue line is **connecting qualified homeowners to cleaners** — we supply scored, net-$-ranked, high-soiler leads; cleaners want recurring lead flow (70–80% of first-time clients recur). Lead-gen referral or marketplace take, hosted in the **BBF** site's Tools section. The economics behind it: `outputs/economics/lightpro_opportunity.md`. Roadmap: [Q2_PLAN.md](Q2_PLAN.md#next-steps--post-2026-06-meeting).

---

## Customer-readiness arc (Tier 0–3)

The current code is research-shaped — 14 numbered scripts each with their own argparse and output convention. That's correct for "Cameron and Josh debug a model" and wrong for "we have a partner AOI on Friday and need full Stage 1 → Stage 2 in one command with versioned outputs and beta metadata." Below in priority order; tiers are largely independent and can run in parallel with model work.

- **Tier 0 — output manifest + dependency hygiene. `shipped`.** `pyproject.toml` registers the package; `src/solarsoiled/manifest.py` writes a sibling `manifest.json` from every artifact-producing script (02/04/05/06/09/10/11). Schema mirrors the v0 beta API response: `{schema_version, stage, model_version, model_weights_sha256, inputs_hash, generated_at, beta, metrics, known_limitations}`.
- **Tier 1 — `solarsoiled` CLI. `shipped`.** Typer entrypoint registered as a console script (`pip install -e .` exposes `solarsoiled`). Subcommands `tile / detect / score / recommend / run / eval`; `run --aoi <bbox-or-geojson>` chains all four. The 14 scripts stay and still work standalone; the CLI imports their `main(argv=…)` functions as library calls. Per-AOI namespace: `outputs/aoi/<partner_id>/{aoi.geojson, tiles/, detect/, arrays.geojson, features/, risk.geojson, recommendations.json, manifest.json}`. Recommend engine is rule-based v1 with injectable forecast for tests; covered by 13 unit tests across `tests/test_aoi.py` and `tests/test_recommend.py`. End-to-end smoke against a fresh Stage 1 checkpoint is `queued` for the next training cut.
- **Tier 2 — model registry + AOI primitive. `in progress`.** `models/registry.yaml` is the catalog; `src/solarsoiled/registry.py` resolves `--weights production`, `--weights stage1-v0.5-baseline`, etc. through it for `detect`, `run`, and `eval`. Filesystem paths still resolve as ad-hoc passthrough (`model_version="ad-hoc:<sha12>"`) so partners can point at an arbitrary `.pt` without first editing the registry. AOI primitive hardened with WGS84 lon/lat range checks, explicit non-WGS84 GeoJSON rejection, and shapely validity checks. Next: named-scene resolution and AOI overlap detection.
- **Tier 3 — partner UX polish. `in progress`.** FastAPI backend **`shipped`** (`src/solarsoiled/api.py`) — async job queue, SSE progress streaming, `POST /feedback`, artifact serving, CORS, API-key auth; start with `solarsoiled-api` (registered entrypoint). `solarsoiled eval --report` **`shipped`** (`src/solarsoiled/eval_report.py`, 6 tests) — single-file HTML report (PR curve, F1-colored sweep table, failure-mode tables, base64-embedded overlay PNGs, per-tile worst-offenders, sibling `manifest.json`) produced from existing eval artifacts with no inference re-run. Invoke: `solarsoiled eval --weights <name> --report --report-dir outputs/eval/<run-name>`. Still queued: Dockerfile so a partner runs `docker run solarsoiled:latest run --aoi <bbox>`; `examples/partner_engagement/` worked example; Stage1 → Stage2 contract test on a fixture AOI in CI (the skip-marked harness at `tests/test_smoke_run.py` is the building block — flips on once a `smoketest` registry entry + `SOLARSOILED_SMOKE_TILES` env var are present).

---

## Non-functional principles

- County-agnostic by construction.
- Reproducible training (configs committed with results).
- Minimal external dependencies.
- Modular codebase (detection, features, modeling, API as separable concerns).
- **Honest by default** — beta flags and quality metadata in every response.
