# Contributing

For humans working on solar-soiling-ml. New here? Start with [`docs/ONBOARDING.md`](docs/ONBOARDING.md)
for the full first-day walkthrough; this is the quick reference. Using an AI coding agent? See
[`AGENTS.md`](AGENTS.md).

## Setup

```bash
bash setup/setup_conda.sh          # one-time: creates conda env `solar-soiling`
conda activate solar-soiling
make bootstrap                     # pip install -e ".[dev,api]"
make check-data                    # report which gitignored data you still need
make test-fast                     # verify: unit tests should pass
```

Base `python3` lacks the geospatial stack — always work inside the `solar-soiling` env. Data is not
in git; see [`DATA.md`](DATA.md) for what each artifact is and how to obtain it (Cameron hands over
the provided ones; the rest you regenerate).

## Running things

Run from the repo root with `PYTHONPATH=.` so `src` imports resolve. `make help` lists the common
targets (eval, training, demo AOI, etc.). Scripts live under `scripts/<stage>/`; the library under
`src/`. `CLAUDE.md` is the authoritative map of paths and commands.

## Tests

- `make test-fast` — unit tests only. **This is what CI runs on every PR** (see
  `.github/workflows/ci.yml`); keep it green.
- `make test` — full suite, including `smoke`/`integration`/`contract` tests that need weights/data
  on disk (won't pass in a bare checkout).
- New behavior should come with a test. Mark tests that need weights/data/network with
  `@pytest.mark.smoke` / `integration` / `contract` so CI skips them.

## Branches & PRs

- **`main` is protected** — no direct pushes; land work via PR with at least one review.
- **One workstream per branch/PR.** Don't mix a feature with a docs truth-up or an unrelated fix.
- Branch names: `<area>-<short-desc>` (e.g. `stage1-rfdetr-rematch`, `product-dashboard-ab`).
- Commit subjects scoped by area (`detect:`, `soiling:`, `analyze:`, `docs:`, `product:`), body
  explains *why*. No AI-attribution trailers.
- Never commit secrets, PII, `*.pt` weights, `.env`, caches, or data. If `git status` shows one, stop.

Ownership map and the deploy/live-action approval rules are in [`docs/TEAM.md`](docs/TEAM.md).

## Area rules

Operating rules for each part of the pipeline live in [`.claude/rules/`](.claude/rules/) — read the
one matching what you're touching before changing it:

| Working on… | Read |
|---|---|
| YOLO train/infer/eval, label QA | `.claude/rules/stage1-detect.md`, `.claude/rules/training.md` |
| Soiling risk model, features, weather/AQ | `.claude/rules/stage2-risk.md` |
| CLI, API, outreach, dashboard | `.claude/rules/product.md` |
| Tiling, CRS, Roboflow, geospatial data | `.claude/rules/data-pipeline.md` |

(These are named `.claude/` for historical reasons and are auto-loaded by Claude Code; they're
plain markdown — read them regardless of your editor.)
