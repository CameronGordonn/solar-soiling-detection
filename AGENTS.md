# AGENTS.md

Tool-neutral guide for any coding agent (Codex, Cursor, Aider, Claude Code, …) working in this repo.
This file is the cross-agent entry point. Claude Code additionally auto-loads `CLAUDE.md` and
`.claude/rules/*.md`; if your agent doesn't, **read them manually** — they hold the same guidance,
scoped by area.

## What this is

Two-stage geospatial ML: **Stage 1** detects rooftop solar arrays in NAIP aerial imagery (YOLOv11
today, migrating to a permissive detector), **Stage 2** scores each array's soiling risk (XGBoost).
Product surface = a Typer CLI (`solarsoiled`) + a FastAPI backend.

## Orientation (read in this order)

1. `CLAUDE.md` — the repo map: commands, key paths, pipeline layout.
2. `docs/CANONICAL_NUMBERS.md` — **every headline number with its artifact and reproduce command.
   Read this before quoting any metric. If another doc disagrees with it, that doc is stale.**
3. `docs/README.md` — doc index (which doc is canonical for what).
4. `docs/Q2_PLAN.md` — live status, gates, current metrics.
5. `.claude/rules/*.md` — per-area operating rules (`stage1-detect`, `stage2-risk`, `product`,
   `data-pipeline`, `training`). Read the one matching the files you're touching.
6. `CONTRIBUTING.md` — setup, tests, PR flow. `DATA.md` — the gitignored data + how to get it.

## Setup, build, test

```bash
# First time: create the conda env (base python3 lacks geopandas)
bash setup/setup_conda.sh              # creates env `solar-soiling`
conda activate solar-soiling
make bootstrap                         # pip install -e ".[dev,api]"
make check-data                        # what gitignored data you still need (see DATA.md)

# Run scripts from repo root with PYTHONPATH=. so `src` imports resolve:
PYTHONPATH=. python scripts/<stage>/<script>.py

make test-fast    # unit tests (what CI runs); full: make test
```

Scripts are organized by pipeline stage under `scripts/` (`data/ detect/ analyze/ predict/ outreach/
labeling/`). The library is `src/` (`solarsoiled/`, `risk/`, `utils/`).

## Hard guardrails — do not violate

- **Money/outreach:** `scripts/outreach/mail_via_lob.py --send` mails real paid postcards. Never run
  a live send; `--dry-run` only. A batch already went out — never re-send one.
- **AGPL:** `yolo11*` / R2 weights are AGPL — eval-only, never shipped. The permissive detector must
  train from a permissive base (`docs/PERMISSIVE_STACK_MIGRATION.md`).
- **Geospatial:** `data/interim/tile_index.json` is sacred (CRS + affine). Never overwrite without
  reading. YOLO labels are polygon-segmentation, normalized, class 0.
- **Secrets/PII:** never commit `.env`, `keys.yaml`, `*.pt`, data, or caches (`.gitignore` covers
  them). Anything world-readable (BBF dashboard JS) carries no addresses/owner names/APNs/keys —
  identify arrays by ID only.
- **Don't retrain Stage 2** chasing AUC — it's GA-ready; gains are below measurement resolution.

## Conventions

- One workstream per branch/PR; `main` is protected (PR + 1 review). Commit subjects scoped by area
  (`detect:`, `soiling:`, `docs:`, `product:`). No AI-attribution trailers. Details: `docs/TEAM.md`.
- Metrics: Stage 1's production metric is **tile-level box F1** from `scripts/detect/eval_tile_f1.py`
  (0.8260 on test). **SAHI F1 is retired** — it named a metric that no longer exists on the RF-DETR
  stack, and no SAHI number is comparable to a current one. Stage 2 is spatial-CV (**0.712**, not the
  widely-copied 0.728) + pooled out-of-year AUC (0.710). Don't quote mAP50 as the headline. Every
  number and its provenance: `docs/CANONICAL_NUMBERS.md`.
