# Rules: Product — CLI, API, Outreach

Applied when working on `src/solarsoiled/`, `scripts/outreach/`, the FastAPI backend, or the homeowner dashboard/outreach pipeline.

## FastAPI backend

```bash
solarsoiled-api                            # registered entrypoint
# or:
uvicorn solarsoiled.api:app --reload
```

Deployed at `https://solarsoiled-api.onrender.com`. Endpoints: `/jobs`, `/health`, `/feedback`, `/results`, `/recommend-quick`, SSE streaming. Auth via `SOLARSOILED_API_KEY` env var (optional for local dev). Source: `src/solarsoiled/api.py`, job queue in `src/solarsoiled/jobs.py`.

## `src/solarsoiled/` package

| Module | Purpose |
|--------|---------|
| `cli.py` | Typer CLI: `tile / detect / score / recommend / run / eval / viz` subcommands |
| `api.py` | FastAPI backend — async jobs, SSE, `/feedback`, artifact serving |
| `jobs.py` | Async thread-pool job runner + SSE event queue |
| `manifest.py` | Writes `manifest.json` for every artifact dir |
| `registry.py` | Resolves `--weights production` / `latest` / aliases from `models/registry.yaml` |
| `aoi.py` | AOI parsing (bbox or GeoJSON) + WGS84 validation |
| `paths.py` | `AoiPaths` — per-AOI artifact namespace under `outputs/aoi/<partner_id>/` |
| `recommend.py` | v1 rule-based cleaning recommendation engine |
| `viz.py` | Folium HTML risk map from `risk.geojson` |
| `eval_report.py` | Single-file HTML eval report (PR curve, F1 table, overlay PNGs) |

## Outreach pipeline (Santa Cruz pilot)

```bash
PYTHONPATH=. python scripts/outreach/select_targets.py --partner-id santa-cruz-outreach-v1 --top 50
PYTHONPATH=. python scripts/outreach/generate_mailers.py --targets outputs/outreach/santa_cruz_top50.csv
PYTHONPATH=. python scripts/outreach/mail_via_lob.py --targets outputs/outreach/santa_cruz_top50.csv --dry-run   # validate + cost estimate
PYTHONPATH=. python scripts/outreach/mail_via_lob.py --targets outputs/outreach/santa_cruz_top50.csv --send      # commit mailing (~$1-2/card)
```

Output paths: `outputs/outreach/{santa_cruz_top50.csv, alt_scores.json, mailers/<id>.pdf, all_mailers.pdf}`

## Dashboard

Live at `https://betterbehaviorfoundation.com/tools/dashboard` (Cloudflare, served from the
`Better-Behavior-Foundation/BBF-Website` repo, `public/tools/`). The old GitHub Pages URLs are dead
— Pages was never wanted and is disabled as of 2026-08-17. Static GeoJSON embedded in
`solarsoiled-landing/arrays_data.js`. Features: 3-model tab switcher (XGBoost / SOMOSclean / Kimber), QR deep-link (`?id=<array_id>`), energy calculator, `/recommend-quick` Recalculate button.

## Partner AOI output namespace

```
outputs/aoi/<partner_id>/
  aoi.geojson
  tiles/
  detect/
  arrays.geojson
  features/
  risk.geojson
  recommendations.json
  array_recommendations.json
  feedback.json
  risk_map.html
  manifest.json
```

## Model registry

`models/registry.yaml` — add a new cut by appending an entry under `models:` and bumping `aliases.latest`. Registered names: `production`, `latest`, `stage1-v0.5-baseline`, `yolo11s-base`. Filesystem paths pass through as `model_version="ad-hoc:<sha12>"`.
