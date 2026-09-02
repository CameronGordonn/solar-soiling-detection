# Onboarding — solar-soiling-ml

The private source-of-truth repo for **SolarSoiled**: Stage 1 detects rooftop solar arrays in
aerial imagery (RF-DETR + SAM2 on 21cm Santa Cruz County imagery), Stage 2 scores each array's
soiling risk (XGBoost), and the product layer turns that into a dollar recommendation.

**§1 is a runnable path. Run it first, read the rest after.** Every command in §1 was executed in
a throwaway clone, in this order, on 2026-08-26, and the numbers quoted are the ones it printed.

---

## 1. Quickstart

```bash
git clone git@github.com:Better-Behavior-Foundation/solar-soiling-ml.git
cd solar-soiling-ml

bash setup/setup_conda.sh          # creates the `solar-soiling` env on python 3.10
                                   #   WITH_CUDA=1 bash setup/setup_conda.sh   on an NVIDIA box
conda activate solar-soiling
make bootstrap                     # pip install -e ".[dev,api]"

make test-fast                     # ── TIER 1 ──
# expect: 470 passed, 1 skipped, 2 deselected   (~3m; measured 2026-08-31)
```

**Stop here and make sure Tier 1 is green.** It needs no data, no credentials, and no GPU. It is
the only check that separates "my environment is broken" from "I am missing data", and until it
passes nothing below will tell you anything useful. Skips are expected and depend on what you have locally: `../BBF-Website` checked out alongside,
`SOLARSOILED_SMOKE_TILES` set, and a Stage-2 model that only arrives with the data pull.

**If your counts differ, suspect your install before anything else.**
`solarsoiled.paths.REPO_ROOT` follows the editable install, not your working directory, so tests
can find *another* clone's `models/registry.yaml` and its `model.ubj` and pass on somebody else's
data. Re-run `make bootstrap` from *this* directory. This is the single easiest way to get a green
signal that means nothing, and it is how the number in this document was wrong the first time it
was written.

The counts themselves move as the suite grows — they were 246/5 when this doc was written and are
470/1 as of 2026-08-31. A higher count is not a problem; a count that changes when you re-run
`make bootstrap` is. Current expected values live in
[CANONICAL_NUMBERS.md](CANONICAL_NUMBERS.md#repo-health).

```bash
# ── TIER 2 ── the data hand-off. No credential beyond your GitHub access; see DATA.md
gh release download handoff-v1 --repo Better-Behavior-Foundation/solar-soiling-ml \
    -p 'handoff-stage2.tar' -D /tmp/handoff
tar xf /tmp/handoff/handoff-stage2.tar -C .        # 2.4 MB, unlocks the Stage-2 lane
make check-data                    # zero MISSING in the blocks your lane needs
make verify-handoff                # checksums the restore against setup/handoff/MANIFEST.json

# ── TIER 3 ── reproduce a known number (see §4 for your lane's)
PYTHONPATH=. conda run -n solar-soiling python scripts/predict/holdout_ci.py
# expect: POOLED out-of-year AUC point 0.7095, ~60s

make test                          # full suite, once you have data
```

If a Tier-3 script cannot find a file it will tell you which one and point you back at
`make check-data`. It will not hand you a pandas traceback; that was fixed on 2026-08-26.

### Container path (optional)

The conda path above is the supported one. The container exists for partner runs and for anyone who
would rather not build a geospatial env by hand. It is **not** a way to skip the data pull — the
image carries code only, and every model and dataset arrives through the same five mounts.

```bash
make docker-build     # ~10 min cold. RF-DETR + SAM2 included. Size is storage-
                      # driver dependent (10.1 GB local, 5.88 GB in CI).
make docker-smoke     # the check that matters — see below
```

`docker-smoke` imports rfdetr and sam2 and resolves `production` through the registry. It needs no
weights and no data, so run it immediately after the build. It exists because from 2026-05-26 to
2026-09-01 the image installed only the `api` extra: it built, started, and served `--help`
perfectly, then died on `ModuleNotFoundError: rfdetr` the moment anyone ran the shipping detector,
since rfdetr and sam2 are lazy imports. Nothing in CI built the image, so the drift went unnoticed
for three months. **A green `--help` is not evidence the container works.**

Three things that will bite you, all measured on 2026-09-01:

- **Mount all five volumes** (`models/ runs/ outputs/ .cache/ data/external/`). Missing `runs/` or
  `data/external/` fails at the scoring stage, long after startup, not at launch.
- **Pass `--user "$(id -u):$(id -g)"`** on anything that writes. The container is root, so without
  it every artifact it drops into `outputs/` is root-owned on the host and needs sudo to remove.
  `outputs/aoi/smoketest-docker/` has been root-owned since 2026-05-14 for exactly this reason.
  This only works on an image built after 2026-09-01: an arbitrary host UID has no entry in the
  container's `/etc/passwd`, and torch calls `getpass.getuser()` at detector load, so `--user` used
  to die on `KeyError: getpwuid(): uid not found: 1000` before scoring a single chip. The image now
  sets `USER` and `HOME`, which `getpass.getuser()` consults ahead of the password database.
- **Use `--entrypoint solarsoiled`** for CLI runs. The default `CMD` is the API server.

What has actually been verified in the container: detection on 3 cached 21cm SCC tiles produced 109
polygons via `rfdetr-w2-20260807` + SAM2, and Stage-2 scoring of the 3,362-array production matrix
came out **bitwise identical to the host** (max |Δrisk_score| = 0.0) even though the image runs
sklearn 1.9.0 against a calibrator pickled under 1.7.2. Detection is also byte-identical across two
independent container runs. Not yet exercised: the weather-feature
fetch, blocked by the Open-Meteo hourly quota — which blocks the **host** identically, so it is a
quota limit rather than a container one. Note that a quota exhaustion currently surfaces as a bare
`ValueError: Found array with 0 sample(s)` from sklearn's isotonic stage, *after* detection has
already run; if you see that, check the log for `Open-Meteo hourly quota exceeded` above it.

---

## 2. Guardrails — read before running anything

These are the "don't learn this the hard way" items.

- **⚠️ Lob is live money.** `scripts/outreach/mail_via_lob.py --send` prints and mails real
  postcards (~$1–2 each). `mailers_v13` already went out 50/50 on 2026-06-30 — **never re-run a
  sent batch.** `--dry-run` is always safe.
- **Open-Meteo free tier is quota-walled** (~600 homes/day). Bulk scoring hits the wall; quota
  gates are built in (wait-for-reset, resumable). Don't blow the daily quota on a test run.
- **Stage 2 is GA-ready — leave it.** Further AUC/feature/hyperparameter chasing is below
  measurement resolution (SE 0.017). Don't retrain the risk model chasing decimals.
- **Ship no AGPL-derivative weights.** The `yolo11*` / R2 checkpoints are AGPL and stay eval-only.
  One of them, `models/yolov8s_solar_array_v1.pt`, was tracked in git from before the ignore rule
  existed and was removed from `HEAD` on 2026-08-26. **It is still in history** — a full rewrite
  was deliberately not done. If this repo ever goes public, that decision has to be revisited.
- **`data/interim/tile_index.json` is sacred** — CRS + affine transforms. Never overwrite without
  reading. GSD is derived from its affine. The 21cm tiles have their **own** index
  (`data/interim/scc21_labelset/tile_index_21cm.json`); they are not interchangeable.
- **PII/secret gate on anything public.** The BBF dashboard ships client-side JS — no addresses,
  owner names, APNs, or keys. Identify arrays by ID only. (An address file leaked once and had to
  be history-scrubbed.) See `SYNC.md` in the public repo for the full gate.
- **`main` is NOT protected.** Branch protection is unavailable on private repos under the org's
  free plan (GitHub returns `403 Upgrade to GitHub Pro`, confirmed 2026-08-17; Cameron decided to
  stay free). "PR + ≥1 approval, no direct push" is a convention nothing enforces. Nothing will
  stop a direct push or a force-push to `main`. A teammate who assumes the guardrail exists is
  exactly how we lose work.

---

## 3. Read in this order

1. [`README.md`](../README.md) — what the system is and why.
2. [`CLAUDE.md`](../CLAUDE.md) — commands, key paths, pipeline layout. The map of the repo.
3. [`DATA.md`](../DATA.md) — every artifact that is not in git: custody, how to pull, what is
   regenerable and with which command.
4. [`docs/Q2_PLAN.md`](Q2_PLAN.md) — **live status**: phase tables, gates, current metrics.
5. [`docs/TEAM.md`](TEAM.md) — who owns what, branches, PRs, reviews.
6. [`docs/COLD_START_AUDIT_20260823.md`](COLD_START_AUDIT_20260823.md) — **Part B is the trap
   list**: the failure modes that produce plausible wrong numbers instead of errors. Read it once
   now and again the first time a number surprises you.
7. Your lane's runbook in [`.claude/rules/`](../.claude/rules/).

When a doc and the code disagree, the **code and `Q2_PLAN.md` win** — and please fix the doc in
the same PR.

---

## 4. Your lane

Each lane ends in a number. A quickstart that ends in "it ran" teaches nothing.

### Research / paper

No Stage-1 data, no GPU. Pull `handoff-stage2.tar` (2.4 MB).

```bash
PYTHONPATH=. conda run -n solar-soiling python scripts/predict/holdout_ci.py
```

**You should see** pooled out-of-year AUC **0.7095** and `CALIBRATION VERDICT: RETAINED`. Runs in
about a minute. The AUC is deterministic; the calibration numbers move a little between runs
(Brier ~0.215–0.218, ECE ~0.03–0.05), so treat the verdict line as the check, not the decimals.
Then: [`HANDOFF_roof_geometry_for_paper.md`](HANDOFF_roof_geometry_for_paper.md) and
[`PAPER_METHODS_DRAFT.md`](PAPER_METHODS_DRAFT.md).

### Detection / CV

Pull `handoff-stage1-gate.tar` (725 MB) and install the detector extra:

```bash
pip install -e ".[dev,api,detect]"      # rfdetr + sam2; NOT in the default install
PYTHONPATH=. conda run -n solar-soiling python scripts/detect/eval_tile_f1.py \
    --weights models/rfdetr_w2_20260807.pth --run-name my_first_gate
```

**You should see** `conf*=0.50` tuned on val, then test **P 0.850 / R 0.803 / F1 0.826**, 95% CI
[0.798, 0.853], n_gt 585, **gate: PASS**. About 5–6 minutes on CPU for 74 tiles. If you reproduce
that, you own Stage 1. Then: [`.claude/rules/stage1-detect.md`](../.claude/rules/stage1-detect.md)
and [`PERMISSIVE_STACK_MIGRATION.md`](PERMISSIVE_STACK_MIGRATION.md).

To **retrain** rather than evaluate, also pull `handoff-stage1-train.tar` (809 MB) and rebuild the
COCO layout RF-DETR wants: `python scripts/data/build_rfdetr_dataset.py`. Training runs on Colab.

### Product / outreach

Pull `handoff-stage2.tar` and `handoff-aoi.tar` (the live 1,865-site run), then read the Lob and Open-Meteo guardrails in §2 **before** running
anything in `scripts/outreach/`.

```bash
make demo-aoi          # tile -> detect -> score -> recommend, on the Santa Cruz test AOI
```

Verified end to end on 2026-08-26 with `--weights production`: it tiles from the 21cm county
service, detects with `rfdetr-w2-20260807`, scores, and writes recommendations. **You do not need
to pass `--imagery`.** `run` picks the imagery the model needs from its registered
`gsd_ground_m` ([cli.py](../src/solarsoiled/cli.py) — under 0.35 gets county tiles, everything else
NAIP), and `detect` refuses a GSD mismatch rather than quietly regressing.

The real constraint is **geographic, not plumbing**: the county service is Santa Cruz only, so
`production` is scoped to that AOI. Anywhere else needs a 60cm checkpoint
(`stage1-60cm-legacy`, which is AGPL and eval-only) or a retrain. If you read that
`cli.py tile` fetches 60cm and a new AOI needs pre-staged tiles, that note predates the
auto-routing in `run` and is stale.

Two things you will see in the log and should not chase: MERRA-2 fetches returning `410 Gone` (the
backfill is optional and deliberately not rebuilt), and "Inference matrix missing 5 features;
imputing from training medians" (a known Stage-2 limitation, written up in
[`.claude/rules/stage2-risk.md`](../.claude/rules/stage2-risk.md)).

The live surface is the BBF dashboard (`betterbehaviorfoundation.com/tools/dashboard`), served
from the `BBF-Website` repo. The FastAPI/Render API is deprecated and the dashboard does not use
it.

---

## 5. Access you will be granted

| What | Notes |
|---|---|
| GitHub `Better-Behavior-Foundation` org | Membership is the unit; it grants `solar-soiling-ml` **and** `BBF-Website`. |
| The data hand-off | **No extra credential.** It is a GitHub Release on this repo (`handoff-v1`), so org membership is the access. See `DATA.md`. |
| Colab Pro | Stage-1 training runs. |
| Cloudflare Pages | The live dashboard. |
| Roboflow | Label sets. The only route back to `data/yolo/scc21` if it is ever lost. |
| Secrets (`.env`, `keys.yaml`) | Password-manager vault, **never Slack/email**. Templates: `.env.example`, `keys.example.yaml`. |

The **public** repo (`solar-soiling-ml-public`) is archived read-only as of 2026-07. Ignore it
except as a reference snapshot.

---

## 6. Where the active work is

`Q2_PLAN.md` and the dated per-workstream handoff docs in `docs/`. The headline as of 2026-08:
Stage 1 passed its GA gate and the registry now points at the permissive checkpoint; Stage 2 is
GA-ready and frozen; and the **economics audit found the cleaning-value product does not clear in
coastal Santa Cruz** (0 of 1,865 sites). That last one is a confirmed kill-risk, not a bug —
[`ECONOMICS_GROUNDING_20260809.md`](ECONOMICS_GROUNDING_20260809.md). Detection is the asset.

---

## 7. This document has been validated by exactly one person

Cameron wrote §1 and ran it in a throwaway clone. **Nobody else has.** The gaps in an onboarding
doc are precisely the things its author stopped noticing, which is how this file went months
without mentioning `setup/setup_conda.sh` at all.

**If you are the first newcomer: treat §1 as a test, not as instructions.** Log every point where
you had to guess, ask, or look elsewhere, and fix it in the same PR as your first real change.
That log is worth more than anything else you will produce in your first week.
