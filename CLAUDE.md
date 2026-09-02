# CLAUDE.md — solar-soiling-ml

> **Session start:** the shared, canonical working state is [`docs/Q2_PLAN.md`](docs/Q2_PLAN.md)
> (phase tables, gates, metrics) plus the per-workstream handoff docs in `docs/` — read those first;
> they ship with the repo. `SESSION_STARTUP.md` (repo root) is an *optional personal* scratchpad:
> gitignored, so it's not in a fresh clone, and **not** a team source of truth (see
> [`docs/TEAM.md`](docs/TEAM.md)). Keep your own if you find it useful.

YOLOv11 polygon segmentation pipeline for detecting solar arrays in NAIP aerial imagery, plus an XGBoost soiling-risk model that scores each detected array.

**Stage 1 target:** the GA gate is **whatever `scripts/detect/eval_tile_f1.py` prints** — tile-level box-F1 @ IoU 0.50, micro-averaged, through the production chip-grid path, conf tuned on val and reported on test, with a bootstrap CI. Pass = F1 ≥ 0.75 **and** CI-lower ≥ 0.70 **and** recall ≥ 0.70. Full wording + rationale in [.claude/rules/stage1-detect.md](.claude/rules/stage1-detect.md).

> The old "val SAHI F1 ≥ 0.65" gate is **retired** (2026-08-07). It named a metric that no longer exists on this stack: the 21cm relabel changed the labels (2.1× objects on the same footprint) and the imagery, and the RF-DETR path has no SAHI in it. **Any SAHI F1 number in this repo's history, including R2's 0.570, is not comparable to a current one.**

**Current stack:** RF-DETR @728 (Apache-2.0) + optional SAM2 mask stage, on 2025 Santa Cruz County 21cm imagery. Gate A (parity vs YOLOv11) passed 2026-08-06. **`production` was flipped to `rfdetr-w2-20260807` on 2026-08-23** — the live dashboard's 1,865 sites were already produced by it (`outputs/aoi/santa-cruz-w2-21cm/detect/manifest.json`), so the alias now describes what ships. R2-cameron-20260509 is AGPL, eval-only, and reachable as `stage1-60cm-legacy`. **W2 is gated on 21cm Santa Cruz County imagery only, so the constraint is geographic, not plumbing:** `solarsoiled run` already routes tiling off the resolved model's `gsd_ground_m` (21cm county service below 0.35, NAIP otherwise) and `detect` refuses a GSD mismatch, so no flags or pre-staged tiles are needed inside the AOI. Outside it the county service has no coverage — use `stage1-60cm-legacy` (AGPL, eval-only) or re-gate W2 at 60cm. See the constraint note in `models/registry.yaml`. See [docs/PERMISSIVE_STACK_MIGRATION.md](docs/PERMISSIVE_STACK_MIGRATION.md).

**Stage 2:** per-array soiling risk (XGBoost on weather + location + structural features). Active label source: `nrel_merged` — **1,002 rows**: 891 annual panel rows (146 stations, panel years 2008–2022) + **111** summary-only censored rows, 257 stations, 15 states.

**Quote 0.712 spatial-CV AUC**, not 0.728. 0.712 (`abl_full40`) is the 40-feature production set re-measured 2026-08-06 under the current `model.yaml`; 0.728 is `run_optionb`'s stored figure and **does not reproduce**. The margin over the 0.70 gate is ~1 point, so Stage 2 is *at* its gate, not comfortably over. All three Phase-2 gates clear: spatial-CV ≥ 0.70, pooled out-of-year AUC **0.710** (CI [0.676, 0.742]), calibration retained.

> The old "holdout gate 2.1 pts short" line is **retired**. That single-year-2022 gate was replaced on 2026-07-05 because at n=97 it was measurement noise (SE 0.061), not a shortfall. Do not reinstate it.

> **Known limit, measured 2026-08-30:** the model does **not** generalize to an unseen region — pooled out-of-region AUC **0.677**, worst region 0.548 against a random-split ~0.73. 81% of NREL rows sit west of -114. Never claim it works "in any region". See [docs/PVDAQ_LANE_HANDOFF_20260831.md](docs/PVDAQ_LANE_HANDOFF_20260831.md).

**Every headline number in this repo, with its artifact and reproduce command, is in [docs/CANONICAL_NUMBERS.md](docs/CANONICAL_NUMBERS.md). When a doc disagrees with it, the doc is stale.**

---

## Pipeline Structure

Scripts are organized by pipeline stage under `scripts/`:

```
scripts/data/      — data prep: tiling NAIP, Roboflow import/export, Duke dataset
scripts/detect/    — Stage 1: train, infer, evaluate, RCA, threshold sweep, export GeoJSON
scripts/analyze/   — bridge: extract array features, build risk feature matrix
scripts/predict/   — Stage 2: train/score XGBoost risk model, validate, calibrate
scripts/outreach/  — select targets, generate mailers, send via Lob
scripts/labeling/  — label QA: disagreement overlays, vintage audit, bucket renders
scripts/research/  — exploratory (pseudo-labels, SAM masks; not in active pipeline)
```

**New here?** [docs/ONBOARDING.md](docs/ONBOARDING.md) §1 is a runnable setup path: `bash setup/setup_conda.sh` -> `make bootstrap` -> `make test-fast` (**470 passed, 1 skipped, 2 deselected, ~3m** as of 2026-08-31; needs no data). Data custody and the `rclone` pull are in [DATA.md](DATA.md).

**How to work here (read first):** [@.claude/rules/working-agreement.md](.claude/rules/working-agreement.md) — use the connected tools instead of handing back manual steps; proceed without asking on routine reversible work; name real blockers precisely.

Domain runbooks: [@.claude/rules/stage1-detect.md](.claude/rules/stage1-detect.md) | [@.claude/rules/stage2-risk.md](.claude/rules/stage2-risk.md) | [@.claude/rules/product.md](.claude/rules/product.md)

---

## Core Principles

1. **Data > Model** — label quality beats architecture complexity; audit before training
2. **Config-driven** — hyperparameters live in YAML, not scripts; commit config with results
3. **Geospatial first** — `tile_index.json` is sacred; CRS + affine must survive every round-trip
4. **Traceability** — every run produces unique weights in `runs/segment/<name>/`; use `best.pt` not `last.pt`
5. **Hardware flexibility** — presets: `laptop` (batch=1, imgsz=512), `small` (batch=8, imgsz=640), `medium` (batch=4, imgsz=768)
6. **API stability** — ultralytics **>=8.3** (the package tops out at 8.4.x; **there is no ultralytics 11.x**. "v11" here names the *model* family, `yolo11s-seg`, never the package version. `requirements.txt` pinned `>=11.0` until 2026-08-26, which made `pip install -r requirements.txt` unresolvable for everyone). Metrics via `results[0].mp` / `results[0].mr`. AGPL: legacy 60cm eval path only.

---

## Partner-facing CLI (`solarsoiled`)

```bash
# End-to-end on one AOI (tile → detect → score → recommend)
solarsoiled run \
  --aoi "minx,miny,maxx,maxy"          \  # or a GeoJSON polygon path
  --weights production                  \  # registered name from models/registry.yaml
  --soiling-model runs/soiling/run_latest/model.ubj \
  --last-cleaned 2026-01-01             \
  --partner-id smoketest

# Re-run only score + recommend on cached upstream artifacts
solarsoiled run --aoi <…> --weights <…> --soiling-model <…> \
  --last-cleaned 2026-01-01 --partner-id smoketest \
  --skip-tile --skip-detect

# Each stage as a subcommand (tile / detect / score / recommend / eval)
solarsoiled detect --aoi <…> --weights <…> --partner-id smoketest

# Build a self-contained HTML quality report from an existing eval run
solarsoiled eval --weights <name-or-path> --report \
  --report-dir outputs/eval/<run-name> \
  --report-out outputs/eval/<run-name>/report.html

# Start FastAPI backend (deployed at https://solarsoiled-api.onrender.com)
solarsoiled-api                         # or: uvicorn solarsoiled.api:app --reload
```

`--weights` accepts a registered model name (`production`, `latest`, …) from `models/registry.yaml` or a filesystem path to a `.pt`. Outputs land under `outputs/aoi/<partner_id>/`. Every artifact dir carries a `manifest.json` with model_version + inputs_hash + beta flag.

---

## Key Paths

| What | Where |
|------|-------|
| NAIP images | `data/yolo/naip/images/{train,val,test}/` |
| YOLO labels | `data/yolo/naip/labels/{train,val,test}/` |
| Geospatial metadata | `data/interim/tile_index.json` |
| Best weights | `runs/segment/<run_name>/weights/best.pt` |
| Experiment configs | `configs/yolo/experiments.yaml` (prod), `experiments_laptop.yaml` (CPU) |
| Risk model library | `src/risk/` — feature_engineering, risk_model, labels, weather_client |
| Product/CLI/API layer | `src/solarsoiled/` — cli, api, manifest, registry, recommend, viz |
| Shared utilities | `src/utils/` — det_match, rca, overlay_render, tile_metadata, train_utils |
| NREL labels | `data/external/nrel_soiling_map_annual.csv` — gitignored |
| Static features | `data/external/static_features.csv` — gitignored |
| MERRA-2 AQ cache | `.cache/soiling/merra2.sqlite` — gitignored |
| Partner AOI outputs | `outputs/aoi/<partner_id>/{aoi.geojson, tiles/, detect/, arrays.geojson, features/, risk.geojson, recommendations.json, manifest.json}` |
| Outreach outputs | `outputs/outreach/` |
| Live dashboard | BBF site — `../BBF-Website/public/tools/dashboard.html` (live via Cloudflare at `https://betterbehaviorfoundation.com/tools/dashboard`; DNS cutover done, verified 200 on 2026-08-17) |

---

## Dataset Status (as of 2026-06-22)

- Train: 171 images (130 with arrays), 695 arrays — relabeled across multiple rounds
- Val: 27 images (21 with arrays), 140 objects | Test: 50 images (34 with arrays), 219 objects
- Total: 248 images / ~1,054 labeled polygons (counts drift with each relabel round — these are live on-disk numbers)
- Format: YOLOv11 polygon segmentation (normalized 0-1 coords, class=0)
- Recommended thresholds (SAHI production path): conf=0.40, iou=0.50

---

## Quarter direction

Status + direction live in [docs/Q2_PLAN.md](docs/Q2_PLAN.md) (canonical live status) and
[docs/PRODUCT_VISION.md](docs/PRODUCT_VISION.md) (north-star). See [docs/README.md](docs/README.md)
for the full doc index. **Read on demand** — these are linked, not auto-loaded, to keep per-session
context lean.

## Notes

- Model naming: Ultralytics uses `yolo11*` (no `v`). Configs point to `models/yolo11s-seg.pt` and `models/yolo11m-seg.pt`.
- Base weights (`.pt`/`.pth`) are gitignored — pull them from the R2 hand-off bucket, see [DATA.md](DATA.md). One exception, and it is a mistake rather than a pattern: `models/yolov8s_solar_array_v1.pt` was committed before the ignore rule existed, so the rule never applied to it. Removed from `HEAD` on 2026-08-26; still in history.
- Reference docs: `docs/NAIP_ROBOFLOW_WORKFLOW.md` (pipeline reference), `docs/RTX3060_SETUP_GUIDE.md` (GPU env). Read on demand.
- Ultralytics YOLOv11 docs: https://docs.ultralytics.com/
