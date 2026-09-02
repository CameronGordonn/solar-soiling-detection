# Q2 Plan: Current Alignment

Canonical project-status doc. The active Stage 1 runbook lives in [docs/PHASE1_HANDOFF.md](PHASE1_HANDOFF.md) (diagnose-first + R0 retrain, week of 2026-05-04). Joint training is paused; v1/v2 forensic detail (29:1 ratio, optimizer=auto bug, RandAugment starburst) lives in commit `2424b4a` and the comments in `configs/yolo/experiments_joint_v2.yaml` for whenever ramp work resumes. Stage 2 in [docs/SOILING_STAGE2_GUIDE.md](SOILING_STAGE2_GUIDE.md). Other docs link here for live status; they do not restate it.

**Status flags used throughout (only these four):** `shipped` (delivered, in production use), `in progress` (active work), `queued` (planned, not started), `beta` (live but below GA quality bar).

## Snapshot — as of 2026-08-20

One-paragraph "where we stand" for anyone opening this cold; the phase tables below carry the detail.

- **Team:** two people now — **Cameron** (direction/product/business, Stage-1 training, Stage-2 [frozen]) and **Akshitha** (onboarded 2026-07; the permissive-detector migration + the product/infra surface). Lanes + conventions: [docs/TEAM.md](TEAM.md); ownership routes via [.github/CODEOWNERS](../.github/CODEOWNERS). Onboarding: [docs/ONBOARDING.md](ONBOARDING.md).
- **Stage 1 (detect): the GA gate is PASSED (2026-08-07).** `rfdetr_w2_20260807` on the full 21cm train set: conf\* 0.50 tuned on val and frozen, **test P 0.850 / R 0.803 / F1 0.8260, 95% CI [0.798, 0.853], n_gt 585** — clears all three conditions (F1 ≥ 0.75, CI-lower ≥ 0.70, recall ≥ 0.70). W2 beats the W1 anchor on a **paired** bootstrap (+0.0246 F1, CI [+0.0051, +0.0441], P(W2>W1)=0.993), a gain the overlapping marginal CIs would have dismissed as noise. RF-DETR @728 + SAM2, Apache-2.0 — [docs/PERMISSIVE_STACK_MIGRATION.md](PERMISSIVE_STACK_MIGRATION.md). **Registry flipped 2026-08-23: `production` -> `rfdetr-w2-20260807`** (Cameron's call). The decisive evidence was that RF-DETR had already produced the shipping data — the dashboard's 1,865 sites carry `model_version: rfdetr-w2-20260807` across all 249 tiles — so the old alias did not describe what shipped, and `api.py` served an AGPL checkpoint by default. R2 is now `stage1-60cm-legacy`, AGPL and eval-only. The 60cm constraint survives the flip and is documented in `models/registry.yaml`. The old "val SAHI F1 ≥ 0.65" is retired (note below).
- **Stage 2 (risk):** **GA-ready within its training geography** — all three gates clear (spatial-CV AUC **0.712** re-measured, pooled out-of-year AUC 0.710, calibration retained). Ships `beta` until Cameron flips the registry. **The 0.728 in older copies is `run_optionb`'s stored figure and does not reproduce** under the current `model.yaml`; quote 0.712 and describe the model as *at* its gate. **Known hard limit (measured 2026-08-30):** pooled out-of-region AUC **0.677**, worst region **0.548** — the model does not transfer to a region unlike its training geography, and "model work is done" means *on this label set*, not in general. See [CANONICAL_NUMBERS.md](CANONICAL_NUMBERS.md) and [PVDAQ_LANE_HANDOFF_20260831.md](PVDAQ_LANE_HANDOFF_20260831.md).
- **Economics (new since the last snapshot, and it is the headline):** the dollar chain was audited end-to-end 2026-08-09 and **the cleaning-value product does not clear in coastal Santa Cruz — 0 of 1,865 sites, max `prob_net_positive` 0.0000.** This is kill-risk **C1 confirmed**, not a bug. It has since survived its own largest counter-correction: measured tariff vintage (the AOI is 90.1% legacy NEM, raising the value of a lost kWh $0.1646 → $0.4284 and the AOI annual loss **+156%**) left the verdict unchanged. Best site in the AOI is **2.76× short** against real per-panel cost, and the shortfall **asymptotes near 3×** with system size, so size targeting is exhausted. Detection is the asset; the moss/lichen channel is the only untested route to closing the gap. See [ECONOMICS_GROUNDING_20260809.md](ECONOMICS_GROUNDING_20260809.md), [CRAIG_BRIEF_2026-08-19.md](CRAIG_BRIEF_2026-08-19.md), [AOI_CLEANING_TARGETING_PLAN.md](AOI_CLEANING_TARGETING_PLAN.md).
- **Product + outreach:** BBF site **live** on Cloudflare Pages (dashboard + calculator at `/tools/*`); the Santa Cruz **test-50 postcards mailed live 50/50** (2026-06-30). The FastAPI/Render API surface is deprecated and unused by the live dashboard.
- **Repo:** onboarding-infra + a `.gitignore`/clone-correctness truth-up landed 2026-07 so a fresh clone is code-complete and self-documenting.

## Next steps — post-2026-06 meeting

Agreed direction out of the 2026-06 review: advance the **residential funnel + consumer→cleaner marketplace** (the "own the data/funnel" wedge — see [docs/MEETING_BRIEF_2026-06.md](MEETING_BRIEF_2026-06.md)) and fold the product into the main **BBF** site. Three parallel workstreams:

### A. Website & service integration (BBF) — `shipped`
- ✅ SolarSoiled dashboard + calculator folded into the main **BBF** site, in a **Tools** section (`../BBF-Website/public/tools/`).
- ✅ Non-Squarespace stack: rebuilt on Next.js, **live on Cloudflare Pages** (`betterbehaviorfoundation.com`, DNS cutover 2026-06-30). The old GitHub Pages landing + Vercel are retired.
- Ongoing polish (not blocking): dashboard psychological-impact tweaks — sharpen the loss / recover-$ framing. Model honesty holds: the dashboard **ranks** soiling (relative risk + $ range), doesn't over-claim absolute %.
- Note: the dashboard is now self-contained on embedded `arrays_data.js`; it no longer calls the `/recommend-quick` Render endpoint (that API surface is deprecated).

### B. Mailer — `shipped` (pilot)
- ✅ **Test batch of 50 mailed live 50/50** on 2026-06-30 (`mailers_v13`, ~$47.52 prepaid credits); the `live_` key was rotated after send. **Do not re-run that batch** — a new campaign renders into a new mailers dir. Runbook: [docs/MAILER_PIPELINE.md](MAILER_PIPELINE.md).
- ✅ Mailer API pipeline (Lob) validated end-to-end; QR deep-link → dashboard verified for all 50 ids.
- _Reuse:_ `scripts/outreach/{select_targets,generate_mailers,mail_via_lob}.py`; rank targets by **net-$** (high-soiler tail) rather than raw risk. Next campaign is a business/scale decision, not a build task.

### C. Cleaning outreach — consumer→cleaner — `queued`
- Decide **how to connect a consumer to a cleaner** when cleaning is their chosen route (referral / lead-gen vs. marketplace vs. white-label) — this is the revenue wedge.
- **Stand up the data→cleaning pipeline**: qualified high-soiler leads (scored, ranked by net-$) → cleaner intake → feedback loop on actual recovery (`POST /feedback`).
- _Grounding:_ the light-pro opportunity model (`outputs/economics/lightpro_opportunity.md`) — cleaners want recurring lead flow; we supply qualified, ranked leads. Keep PII **private** (public SYNC.md gate).

## Project status

### Quarter phases — model + product milestones

| Phase | Goal | Done looks like | Status | Current |
|---|---|---|---|---|
| **Phase 1 — Panel detection** | A detector good enough to ship, on a permissive licence | **tile-level box-F1 ≥ 0.75 AND CI-lower ≥ 0.70 AND recall ≥ 0.70**, conf tuned on val and frozen, via `scripts/detect/eval_tile_f1.py` | **`GATE PASSED`** (2026-08-07); ships `beta` until the registry flips | **`rfdetr_w2_20260807`: test P 0.850 / R 0.803 / F1 0.8260, 95% CI [0.798, 0.853], n_gt 585 — all three conditions clear.** Full 21cm train set (170 tiles), dataset `data/yolo/scc21`. Beats W1 on a paired bootstrap (+0.0246 F1, P=0.993); marginal CIs overlap and would have called it noise. Two caveats carried forward: the val→test tuning penalty grew to 0.017 (W1: 0.003), and the chip→tile gap widened to 0.029 (W1: 0.008). **The retired SAHI-F1 numbers, R2's 0.570 included, are not comparable to this** — see the gate-wording note below |
| **Phase 2 — Soiling-risk model** | Honest leakage-free metrics on the patched pipeline | Spatial-CV AUC ≥ 0.70 **and** pooled out-of-year AUC ≥ 0.70 (point estimate; CI reported) **and** calibration retained out-of-year — see gate-wording note below | **`GA-ready`** (all three cleared); ships `beta` until Cameron flips the registry | **All three gate conditions cleared (2026-07-05, `scripts/predict/holdout_ci.py`; `run_optionb` 40-feature set + regularized `model.yaml`; recorded in `runs/soiling/run_optionb/holdout_ci.json`).** (1) **Spatial-CV AUC ≥ 0.70** ✓ — 0.728 as recorded for `run_optionb`, but **0.712 on re-measurement** under the current `model.yaml` (`abl_full40`, 2026-08-06), so quote 0.712 and call the margin ~1 point. (2) **Pooled out-of-year AUC 0.710** ✓ — the noisy single-year-2022 gate (AUC 0.680 ± 0.061, 95% CI [0.557, 0.797], P(≥0.70)=0.37, unresolvable at n=97) was replaced by a leave-one-year-out rolling holdout pooled across all 15 panel years (2008–2022, n=891): point 0.710, Hanley-McNeil SE 0.017, bootstrap 95% CI [0.676, 0.742], P(AUC≥0.70)=0.70. Point clears; CI straddles (lower 0.676) — temporal ranking is *at* the gate, not below it, and the "2.1 pts short" reading was measurement noise. (3) **Calibration retained out-of-year** ✓ — pooled OOY Brier 0.218 beats base-rate 0.250, ECE 0.046 (<0.10); the isotonic map (fit on training years) stays reliable on held-out years, so the product's calibrated probability / dollar figure survives temporal shift. Hyperparameters regularized (`max_depth=4, reg_lambda=5`, ref `run_regularized2022`); MERRA-2 exhausted; further AUC chasing is below measurement resolution. Re-derive: `PYTHONPATH=. conda run -n solar-soiling python scripts/predict/holdout_ci.py` (cached matrix, no weather refetch) |
| **Phase 3 — Product surface** | End-to-end AOI pipeline a partner can run | One CLI entrypoint; per-AOI namespace; manifest-versioned outputs; model registry; Stage1 → Stage2 contract test | `in progress` | CLI + manifests + registry `shipped` (Tiers 0–1, partial Tier 2); FastAPI backend + job queue + SSE streaming + `POST /feedback` + `GET /recommend-quick` `shipped` (`src/solarsoiled/api.py`); deployed on Render.com; Tier 2 named-scene resolution + AOI overlap detection `queued`; Tier 3 partial: `solarsoiled eval --report` HTML `shipped`; Dockerfile + partner example + Stage1 → Stage2 CI contract test `queued` |
| **Phase 4 — Homeowner outreach** | Physical-to-digital loop: risk scores → postcard → QR → dashboard → action | Top-50 postcard mailed; homeowner lands on personalized dashboard with energy calculator | `in progress` | Target selection `shipped` (script 20, parcel join); PDF mailers `shipped` (script 21, ReportLab + QR codes); Lob integration `shipped` (script 22, dry-run validated ~$75 for 50 cards); alt-model scoring `shipped` (script 23, SOMOSclean + Kimber from SQLite cache, 334/334 arrays); dashboard `shipped` (BBF site / Cloudflare Pages, 3-model tab switcher, QR deep-link `?id=<array_id>`, energy calculator); **test-50 physical send `shipped`** (`mailers_v13` sent live 50/50, 2026-06-30); BBF-site integration `shipped` |

Phase 1 gates Phase 3 (product needs a trustworthy detector). Phase 2 runs in parallel; stays `beta` until both AUC gates clear. Phase 3 plumbing consumes versioned outputs and quality metadata rather than hides model limitations.

**Phase 1 gate-wording change (2026-08-07).** The gate was "val **SAHI F1 ≥ 0.65**" (beta ≥ 0.55). It is **retired**, not merely re-tuned, because the thing it measured stopped existing: the 21cm relabeling sprint replaced the labels (2.1× the objects on the same val footprint), replaced the imagery (60cm NAIP → 25cm county 2025), and the RF-DETR path contains no SAHI at all — it re-runs the same 2×2 640px chip grid the model trained on and merges with NMS. Reporting a "SAHI F1" against the new stack would have been a category error dressed as a pass. **Every SAHI F1 in this repo's history, R2's 0.570 included, is uncomparable to anything measured from here on.**

The replacement is **executable rather than prose** — `scripts/detect/eval_tile_f1.py` is the definition of record, and `.claude/rules/stage1-detect.md` summarizes it. Metric: **tile-level detection F1 at box-IoU ≥ 0.50, micro-averaged**, measured through the production path (whole tile → chip grid → NMS seam merge), via the same `src/utils/det_match.py` matcher every other eval uses. Protocol fixes three things that were wrong or unstated before: (1) **conf is tuned on `val` and frozen, and `test` is the reported number** — the W1 manifest tuned conf on test, spending the held-out split and making its 0.809 an optimistic tuned-on figure; (2) **matching is on boxes, not masks**, so the gate is invariant to whether SAM2 is in the pipeline — SAM2 changes *area* accuracy, and conflating the two leaves you unable to say which stage regressed; (3) a **95% bootstrap CI resampled over tiles**, not objects, since arrays within a tile are correlated and object-resampling reports an interval that is too narrow. Pass requires all three of: **F1 ≥ 0.75**, **CI-lower ≥ 0.70**, **recall ≥ 0.70** (recall is the funnel — a missed array is a missed lead, while precision errors are filtered downstream by the permit join).

**Thresholds ratified by Cameron 2026-08-07: F1 ≥ 0.75, CI-lower ≥ 0.70, recall ≥ 0.70.** For provenance: the previously documented bar was **SAHI F1 ≥ 0.65** GA / 0.55 beta (plus a 0.70 mAP50 target in the registry's `known_limitations`); the 0.75 is new as of this date and is not a carry-over — no 0.75 appears in the repo before it. It is a **shipping floor**, not a model-selection tool, and is deliberately not re-raised when a checkpoint clears it.

**Anchor (measured 2026-08-07).** `rfdetr_w1_20260806.pth` through this exact path: conf\* 0.40 tuned on val, test **P 0.853 / R 0.756 / F1 0.8015, 95% CI [0.773, 0.830], n_gt 585 → PASS**. This establishes two things beyond the model itself. The **production path is not lossy** — 0.809 chip-level → 0.8015 tile-level, so the 4-window chip grid plus NMS seam merge reproduces chip-level scoring within 0.008 on differently-counted GT (585 vs 636), which was an open question about the inference design. And the **honest protocol is nearly free** — test's own best-F1 is 0.8044 against 0.8015 at the val-frozen threshold, a 0.003 penalty, with val and test independently selecting conf=0.40. Re-run the anchor whenever the eval path changes, so a change of metric can never be mistaken for a change of model.

**Phase 2 gate-wording change (2026-07-05).** The temporal-holdout gate was "single-year (2022) holdout AUC ≥ 0.70". At n=97 that is a coin-flip estimator (SE 0.061; the 0.70 gate sits 0.32 SE from the 0.680 point). It is replaced by a **pooled leave-one-year-out (rolling) out-of-year AUC ≥ 0.70**: hold out each of the 15 panel years in turn, train on all others (summary rows always in-train), and pool every row's out-of-its-year prediction (n=891). This gives an SE of 0.017 — a 3.5× tighter estimator on the same data and labels — and the model **clears it at 0.710**. We report the gate as a point estimate (matching the original intent) with its bootstrap 95% CI [0.676, 0.742] shown alongside for honesty; we deliberately did **not** adopt the stricter "lower-CI ≥ 0.70" criterion, which the model does not clear (0.676) and which would be a materially harder bar than the original point gate ever was. The gate also retains its **"calibration retained"** clause, now verified out-of-year: the same rolling holdout applies each training-year isotonic map to its held-out year and pools the calibrated probabilities (Brier 0.218 < base-rate 0.250, ECE 0.046) — AUC proving ranking survives temporal shift is necessary but not sufficient, since the product surfaces a *calibrated* probability/dollar figure, not a rank. Tool + full per-year breakdown + calibration table: `scripts/predict/holdout_ci.py` (recorded to `runs/soiling/<run>/holdout_ci.json` via `--out-json`; surfaced by `compare_runs.py`).

### Customer-readiness tiers — CLI / API surface

| Tier | Scope | Status |
|---|---|---|
| **Tier 0** — output manifests + dependency hygiene | `pyproject.toml` package; `manifest.json` from every artifact-producing script | `shipped` |
| **Tier 1** — `solarsoiled` CLI | `tile / detect / score / recommend / run / eval` subcommands; per-AOI output namespace | `shipped` |
| **Tier 2** — model registry + AOI primitive | `models/registry.yaml` resolving `production` / `latest` / aliases; AOI WGS84 + CRS + validity checks | `in progress` (registry + AOI hardening live; named-scene resolution + AOI-overlap detection `queued`) |
| **Tier 3** — partner UX polish | FastAPI backend + job queue + SSE + feedback + `/recommend-quick` endpoints; Render.com deployment; `solarsoiled eval --report` HTML; partner example; Stage1 → Stage2 CI contract test | `in progress` (FastAPI backend `shipped` and deployed; `eval --report` `shipped`; Dockerfile + partner example + CI contract test `queued`) |
| **Tier 4** — homeowner dashboard + outreach | Leaflet dashboard with 3-model tab switcher; QR deep-link; energy calculator; outreach scripts 20–23; physical mailers | Live dashboard; top-50 postcards mailed | `shipped` (dashboard + scripts 20–23 done; test-50 **mailed live 50/50 on 2026-06-30**) |

### Parallel tracks — Track C does not wait on Track A

| Track | Scope | Ship criterion | Status |
|---|---|---|---|
| **Track A — model quality** | Stage 1 permissive-stack retrain on 21cm labels; Stage 2 pooled out-of-year validation | Phase 1 + Phase 2 GA gates clear | **`done`** — both gates cleared (Stage 1 2026-08-07 at F1 0.8260; Stage 2 2026-07-05). Further model chasing is below measurement resolution in both stages; remaining work is product-side |
| **Track B — visibility surface** | BBF site Tools section (`../BBF-Website/public/tools/`): homeowner dashboard, breakeven calculator, design-partner surface | Live site + dashboard with 3-model comparison | `shipped` (built) — dashboard + calculator at `/tools/*`; `/recommend-quick` wired to Render backend; live at the DNS cutover |
| **Track C — beta API** | `/detect`, `/risk`, `/recommend`, `/health`, `/feedback`, `/jobs`, `/recommend-quick` endpoints with quality metadata + auth + metering | Reachable beta endpoints behind an API key | `in progress` — health, jobs, feedback, results, SSE streaming, `/recommend-quick` `shipped`; deployed on Render.com; metering stubs + standalone `/detect` and `/risk` endpoints `queued` |
| **Track D — physical outreach** | Top-50 postcards with QR codes → personalized dashboard | 50 cards in homeowners' hands | `shipped` (pilot) — scripts 20–23 built; **test-50 mailed live 50/50 on 2026-06-30** (`mailers_v13`); all 50 QR ids resolve in the live dashboard |

**Superseded 2026-08-07 — the paragraph below describes the YOLOv11/Duke plan, which no longer runs.** The active Stage 1 weight is `models/rfdetr_w2_20260807.pth` (RF-DETR @728 + SAM2, Apache-2.0), trained on `data/yolo/scc21` and passing the gate at F1 0.8260. Duke integration is abandoned and the joint-training ramp is not resuming. Kept because the failure analysis is still the reason we do not mix label conventions.

> _Historical:_ The Stage 1 base weight was the SAHI warm-start checkpoint (~0.65 NAIP test on `model.val()` at conf=0.10). After the 2026-05-03 joint over-prediction failure (mAP50 0.128 / R 0.76 / P <0.1) and Tyler's 2026-05-04 meeting, joint training was paused, and the path was: (1) diagnose-first RCA on the SAHI baseline; (2) R0 retrained from scratch on Santa Cruz NAIP with iterative Roboflow relabeling; (3) Duke ramp (R1+) only after R0 reproduced ≥0.55 baseline.

## Phase 1 Summary

> ⚠️ **HISTORICAL as of 2026-08-07. This section describes the YOLOv11 + Duke joint-training
> era, which ended.** The shipping detector is RF-DETR @728 + SAM2 on 21cm imagery
> (`data/yolo/scc21`), the gate is tile-level box-F1, and it passed at **0.8260**. Every
> mAP50 and SAHI-F1 figure below was measured on different labels, different imagery and a
> different architecture, and **none of them is comparable to a current number** — that
> incomparability is exactly the trap this repo has fallen into before. Kept because the
> forensics are still load-bearing: they are why NAIP whole-array and Duke per-panel labels
> are never mixed, and why hard negatives were ruled out. For the live runbook see
> [PERMISSIVE_STACK_MIGRATION.md](PERMISSIVE_STACK_MIGRATION.md) and
> [PHASE1_HANDOFF.md](PHASE1_HANDOFF.md).

### What we are doing

Stage 1 is YOLOv11 instance segmentation for solar arrays. The contract is simple: preserve CRS and affine metadata end to end so polygons can be exported back to world coordinates.

The pre-train-then-fine-tune plan failed (Duke-only pre-training transferred at 0.028 mAP50). Joint training failed twice: v1 collapsed both domains (NAIP 0.275 / Duke 0.010), and the 2026-05-03 cut hit mAP50 0.128 / R 0.76 / P <0.1 — over-prediction, the model hallucinating panels everywhere. Tyler's 2026-05-04 meeting redirected to **diagnose-first**: classify every TP/FN/FP on the existing SAHI baseline, find the failure-mode pattern, then retrain.

The diagnostic surfaced two compounding issues. (1) NAIP labels were drawn at 60 cm source resolution and miss small panels — the alone-tile FPs (65 of 360 total FPs concentrated on 20 GT-empty tiles, max 13 detections at conf 0.5 on `tile_000150`) are most likely real arrays the model found but our labels never recorded. (2) NAIP and Duke encode arrays differently — NAIP labels whole-array polygons (median 24 m²), Duke labels per-panel (median 1.7 m², KS=0.895 on area_m²). Joint training tries to bridge two label conventions. Active strategy: retrain R0 on Santa Cruz NAIP warm-started from `sahi_baseline_train7.pt` (preserves the small-panel prior 60 cm hand labels can't teach), iterate label fixes in Roboflow (the model surfaces label gaps via `05c` + `18`), only then ramp Duke (`02g_build_joint_v2_lists.py --naip-repeat <N>`) with hard regression stop-rule (`05e_ramp_eval.py` halts if NAIP test mAP50 drops by >0.07 vs 0.563).

### Datasets in scope

| Dataset | What it is | Why it matters | Status |
|---|---|---|---|
| NAIP Santa Cruz | 249 tiles, 174 train / 37 val / 38 test, about 360 arrays, 0.6 m GSD, YOLO polygons | This is the target domain and the current baseline source | `in progress` |
| Duke / Bradbury 160 px | 601 source images, 19,433 arrays, 2014-2015 NAIP vintage, GeoJSON converted to YOLO polygons, tiled to 160 px | Adds small-array signal that NAIP lacks | `in progress` (post-vintage-fix re-download) |
| NAIP San Jose | Older notes mention a separate ~100-tile NAIP set | Could help with small-array recall, but I have not confirmed the canonical files in the workspace | `queued` (canonical files not confirmed) |
| Connecticut Solar PV | 87 tiles, 1,611 arrays, 30 cm semantic masks | Useful diversity, lower priority than the current joint run | `queued` |
| BDAPPV | About 13k French aerial installations | Robustness data, later phase only | `queued` |

Active today in the YOLO path: `data/yolo/naip`, `data/yolo/duke_160`, and the generated `data/yolo/joint_v2` lists.

### Pipeline

1. NAIP tiles are created as 640x640 PNGs by `scripts/data/tile_naip_image.py`, recorded in `data/interim/tile_index.json`, and labeled via Roboflow back into `data/yolo/naip/`.
2. Duke imagery is downloaded by `scripts/data/download_duke_dataset.py` using the 2014-2015 vintage filter, converted by `scripts/data/convert_duke_dataset.py` into 160 px YOLO chips, and cleaned by `scripts/data/clean_duke_dataset.py`.
3. `scripts/data/audit_dataset.py`, `scripts/labeling/validate_labels.py`, and the visual inspection gates catch label/geometry problems before any training.
4. `scripts/data/build_joint_lists.py` creates the current mix: Duke once, NAIP repeated 29x, with NAIP-only validation so early stopping follows the target domain.
5. `scripts/detect/train.py` trains from YAML configs, `scripts/detect/eval_threshold_sweep.py` calibrates NAIP thresholds, `scripts/detect/infer.py` runs inference, and `scripts/detect/export_polygons_geojson.py` exports georeferenced polygons.

### Results so far

| Run | Setup | Result | Meaning |
|---|---|---|---|
| Baseline | NAIP Santa Cruz only | 0.563 test mAP50, precision about 0.53, recall about 0.56 | Production-safe Stage 1 baseline |
| SAHI warm start | Baseline weights with overlapping SAHI inference | about 65% on test | Current base weight for the oversampled Duke run |
| SAHI calibration | Best baseline weights with threshold sweep | 0.687 calibrated val mAP50 at conf 0.10 / iou 0.50, F1 0.653 | Shows the baseline can improve without retraining |
| Duke pre-train | Old Duke-only 320 px path | 0.028 transferred mAP50 | Abandoned |
| NAIP fine-tune after Duke pre-train | Fine-tune on NAIP after Duke-only pre-train | 0.424 | Worse than baseline |
| Joint v1 | Duke 160 px + NAIP, uniform sampling | NAIP 0.275, Duke 0.010 | Regression on both domains |
| Joint 2026-05-03 | Duke 160 px + NAIP, --naip-repeat 29, optimizer=auto bug fixed | NAIP test mAP50 0.128, P <0.1, R 0.76 | Over-prediction — model hallucinates panels everywhere |
| RCA on SAHI baseline | per_detection.csv on val+test at conf=0.05 | TP=125 FP=360 FN=101 (P=0.26 R=0.55); 65 of 360 FPs on 20 GT-empty tiles | Most alone-tile FPs are likely under-labeled real panels, not hallucinations |

### What we learned

Joint v1 failed because Duke dominated gradients. Joint v2 with the optimizer bug fixed still over-predicted because the per-panel Duke label convention pulled the prior toward dense detections, and NAIP's whole-array labels don't supply the negative signal at panel scale. Adding hard negatives is off the table — those "negatives" likely contain real panels the labels missed. The path forward is R0 retrain on patched NAIP labels (warm-started from the SAHI baseline so the small-panel detection prior survives), then ramp Duke only once R0 reproduces baseline. Until R0 lands, the 0.563 NAIP-only checkpoint remains production-safe; calibrated SAHI operating point is `conf=0.30, iou=0.50` (F1=0.536, P=0.703, R=0.433 from the 2026-05-04 sweep).

## Quarter dependencies

- Phase 1 gates Phase 3 because product outputs need a trustworthy detector.
- Phase 2 can proceed once Phase 1 outputs are frozen enough to build feature matrices, but it should remain beta until it clears GA.
- Phase 3 should consume versioned outputs and model-quality metadata instead of hiding current limitations.
