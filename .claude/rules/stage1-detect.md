# Rules: Stage 1 — Detection Pipeline

Applied when working on `scripts/detect/`, `scripts/data/`, `scripts/labeling/`, `src/utils/`, or anything touching YOLO training, inference, evaluation, or label quality.

## Current status

- **Active stack:** RF-DETR @728 **+ SAM2** on 21cm SCC imagery — the permissive-stack migration,
  Gate A passed 2026-08-06. See `docs/PERMISSIVE_STACK_MIGRATION.md`.
- **SAM2 is in the production path (decided 2026-08-07).** Not because the detector needs it — the
  gate is box-based and unaffected — but because **area is the product**. Re-measured on the rebuilt
  labels (n=60 val arrays, `outputs/eval/sam_containment_ab.json`):

  | prompt box | med IoU | med area/GT | roof-grab >2×GT |
  |---|---|---|---|
  | box only (no SAM) | 0.509 | 1.97 | 43.3% |
  | **shrink 15% (production default)** | **0.844** | **1.01** | **0.0%** |
  | exact | 0.824 | 1.08 | 0.0% |
  | dilate 25% | 0.758 | 1.28 | 1.7% |
  | dilate 50% | 0.570 | 1.73 | 38.3% |

  Run `rfdetr_infer.py` **without** `--no-sam` for anything product-facing. **The roof-grab is a
  prompt-box problem, not a mask problem** — at the production 15% shrink it is 0% and only appears
  once the box is oversized. Do not add mask post-processing to fix it; fix the box.

- **Two attempts to beat the prompt-box default, both measured, both rejected (2026-08-07).** The
  standing question was whether "shrink the box 15%" is a crude preset that something more
  principled should beat. Answer so far: no.

  | | shrink15 (prod) | exact | dilate25 | dilate50 |
  |---|---|---|---|---|
  | baseline med IoU | **0.844** | 0.824 | 0.758 | 0.570 |
  | + negative points | 0.820 | 0.834 | 0.783 | **0.624** |
  | + containment | 0.750 | 0.673 | 0.403 | 0.295 |

  **Negative points** (`--neg-points`, background points ringed outside the box — a semantic
  assertion rather than a geometric constraint) behave exactly as theory predicts: they *help when
  the box is bad* (dilate50 roof-grab 38.3% → 26.7%) and *hurt when it is good* (shrink15 0.844 →
  0.820, and they introduce a 1.7% roof-grab where there was 0%). With an already-shrunk box the
  ring sits near the true panel edge and clips real array pixels. **Keep off unless box quality
  degrades** — if a future detector emits looser boxes, this is the first thing to re-test.

  **The reason presets keep winning is that the prompt box is already good.** Roof-grab at the
  operating point is 0% and median area/GT is 1.01 — area is essentially unbiased, which is what
  the m² → kW → $ chain needs. The residual at 0.844 IoU is boundary precision on ~26 px objects.

  ⚠️ **We may be at the label-noise floor and cannot currently tell.** The 21cm sprint deliberately
  did not measure inter-annotator agreement (`docs/LABELING_SPRINT_21CM.md` §1 — Roboflow dedupes,
  so one tile cannot go to two labelers). If two humans agree to only ~0.85 IoU on a 26 px array,
  0.844 is the ceiling and further mask work is unmeasurable. **Measure the human ceiling before
  spending more on masks.**

- **Mask "containment" was tried and REJECTED (2026-08-07).** Multimask candidates + clipping to the
  detector box + area-fit selection measured *worse everywhere*: 0.824 → 0.673 median IoU on exact
  boxes, and roof-grab 1.7% → 71.7% at dilate25. Cause: selecting the candidate whose area matches
  the detector box amplifies box error in precisely the regime the change targeted, and the clip
  window is anchored to that same wrong box. Available behind `--mask-containment`, off by default,
  kept only so the negative result stays reproducible. **Do not re-enable without a new measurement.**

- **SAM2 cost is 9.55 s/chip on this CPU box, not 30.6** — the earlier figure was a different
  machine/state. Full 249-tile AOI ≈ **2.7 h** on CPU, not 8.5 h. Still an offline batch step, but
  a materially cheaper one.
- **Dataset:** `data/yolo/scc21` — 976 chips (train 680 / val 100 / test 196), 3,894 chip polygons,
  rebuilt 2026-08-07 from Roboflow v2 (244 of 249 tiles; 5 unreviewed seeds skipped).
- **Two GT counts, and they are not interchangeable.** Chip-level: val **385** / test **636** (what
  the W1 table scored). Tile-level (`tile_labels/`): val **340** / test **585** — the gate's n_gt.
  The difference is arrays the ~80px chip overlap double-counts plus ones the seams split. A
  tile-level F1 quoted against 636 is wrong by construction.
- **Legacy 60cm production model:** R2-cameron-20260509 — val SAHI F1 **0.570**. Eval baseline only;
  it is AGPL and does not ship. Its numbers are **not comparable** to anything below.
- Active runbook: `docs/PERMISSIVE_STACK_MIGRATION.md`. **`docs/PHASE1_HANDOFF.md` is the YOLO/SAHI-era runbook and is superseded** — its "GA gate = val SAHI F1 ≥ 0.65" is retired. Its relabel-loop method is still sound; its metric and gate are not. Numbers: `docs/CANONICAL_NUMBERS.md`.
- Train relabeling complete except 5 seeded tiles (`tile_000063/099/147/210/248`)

## Before you can run any of this

`rfdetr` and `sam2` are **not** in the default install — they live in the `detect` extra, declared
2026-08-26 after an audit found the detection lane could not run from a clean setup at all:

```bash
pip install -e ".[dev,api,detect]"
```

The data this lane needs is the `stage1-gate` group in the hand-off bucket (724 MB): the checkpoint,
the **full 21cm tiles** at `data/interim/scc21_labelset/images` (what `eval_tile_f1.py` reads — not
the chips), the 21cm tile index, and `tile_labels/`. See `DATA.md`.

## The GA gate (restated 2026-08-07)

The old gate — "val SAHI F1 ≥ 0.65" — is **retired**. It named a metric that no longer exists on
this stack: different labels (2.1× the objects on the same footprint), different imagery (21cm vs
60cm), and no SAHI in the path at all. Carrying the number forward would have made a
non-comparison look like a pass.

**The gate is now whatever `scripts/detect/eval_tile_f1.py` prints.** That script is the
definition; this section is a summary of it.

```bash
PYTHONPATH=. conda run -n solar-soiling python scripts/detect/eval_tile_f1.py \
    --weights models/<checkpoint>.pth --run-name <name>
# -> outputs/eval/<name>/{gate.json, threshold_sweep.csv, detections.json}
```

**Metric:** tile-level detection **F1 at box-IoU ≥ 0.50**, micro-averaged over the split, measured
through the production inference path (full tile → 2×2 grid of 640px chips → one RF-DETR pass per
chip → NMS merge at IoU 0.55 across the seams). Matching is `src/utils/det_match.py`, unchanged.

**Protocol:**
- **conf is tuned on `val` and frozen**, then applied to `test`, which is the reported number. The
  W1 manifest tuned conf on test and thereby spent the held-out split; that is corrected here.
- **Boxes, not masks.** The gate is invariant to whether SAM2 is in the pipeline. SAM2 changes
  *area* accuracy, not what was detected — measure it separately (`sam_mask_probe.py`), or you
  cannot tell which stage regressed.
- **Micro-averaged**, so a 30-array tile outweighs a 1-array tile.
- **95% CI by bootstrap over tiles**, not objects — arrays within a tile are correlated, and
  resampling objects would report an interval that is too narrow.

**Pass requires all three — ratified by Cameron 2026-08-07:**

| # | Condition | Why this one |
|---|---|---|
| 1 | test F1 ≥ **0.75** (point) | The shipping bar |
| 2 | test F1 95% CI lower bound ≥ **0.70** | A point estimate alone can't clear a gate at n=585; same discipline as the Stage 2 gate |
| 3 | test recall ≥ **0.70** | Recall is the funnel: a missed array is a missed lead. Precision errors get filtered by the downstream permit join; misses are unrecoverable |

**Provenance of the 0.75, so nobody re-derives it wrong.** The *previously documented* Stage 1 gate
was **SAHI F1 ≥ 0.65** GA / ≥ 0.55 beta (plus a 0.70 mAP50 target in the registry's
`known_limitations`). The 0.75 is new as of 2026-08-07 and was set by Cameron when the metric moved
to tile-level box F1 — it is not a carry-over of any earlier number, and no 0.75 appears anywhere in
this repo's history before that date. It is a **shipping floor**, not a model-selection tool: a
checkpoint clearing it is shippable, and it is deliberately not re-raised each time one passes.

**GATE RESULT — `rfdetr_w2_20260807` PASSES (2026-08-07).** Full 21cm train set (170 tiles).

```
models/rfdetr_w2_20260807.pth   conf* 0.50 (tuned on val)
test   P 0.850   R 0.803   F1 0.8260   95% CI [0.798, 0.853]   n_gt 585   -> PASS (all three)
```

**Anchor: W1 checkpoint through this exact path (measured 2026-08-07).**

```
models/rfdetr_w1_20260806.pth   conf* 0.40 (tuned on val)
test   P 0.853   R 0.756   F1 0.8015   95% CI [0.773, 0.830]   n_gt 585   -> PASS
```

**W2 vs W1 — use the PAIRED test, not the marginal CIs.** The two marginal intervals overlap
heavily ([0.773, 0.830] vs [0.798, 0.853]) and each model's point estimate sits inside the other's
interval, so eyeballing them says "within noise" — and that is **the wrong test**. Both models were
scored on the same 49 tiles, so the comparison is paired and the correct statistic is a bootstrap
over the per-tile *difference*:

```
F1     +0.0246   95% CI [+0.0051, +0.0441]   P(W2 > W1) = 0.993
recall +0.0479   95% CI [+0.0202, +0.0753]   P(W2 > W1) = 0.999
```

The interval excludes zero: doubling the train set produced a **real** gain, concentrated in recall,
and the gain would have been dismissed as noise by the naive comparison. Reproduce with
`outputs/eval/rfdetr_w2/paired_vs_w1.txt` (built from the two cached `detections.json`).

**Two honest caveats on W2.** (1) The tuning penalty grew: test's own best-F1 is **0.8431** vs
**0.8260** at the val-frozen conf — 0.017, against W1's 0.003 — so val and test disagree more about
where the operating point is. 0.8260 is the number to quote. (2) The chip→tile gap widened too:
0.855 chip-level → 0.826 tile-level (0.029) where W1 lost only 0.008. Worth watching, not yet worth
explaining.

Two things this establishes. **The production path is not lossy:** 0.809 chip-level → 0.8015
tile-level, so running 4 windows and merging across seams reproduces chip-level scoring to within
0.008 on differently-counted GT. **The honest protocol is nearly free:** test's own best-F1 is
0.8044 vs 0.8015 at the val-frozen conf, a 0.003 penalty, and val and test independently pick
conf=0.40. Re-run the anchor whenever the eval path changes, so a change of metric can never be
mistaken for a change of model.

## Research scripts — detect pipeline

```bash
# Audit labels before training
python scripts/data/audit_dataset.py --config configs/yolo/dataset_audit.yaml

# Compare NAIP/Duke distributions (domain equivalence baseline, no inference)
python scripts/data/compare_naip_duke_distributions.py

# Train (single run)
python scripts/detect/train.py --model models/yolo11s-seg.pt --epochs 50

# Train (experiment matrix — use this for R0/R1/R2 ramp runs)
python scripts/detect/train_experiment_matrix.py --config configs/yolo/experiments_joint_v2_ramp.yaml --experiment R0

# Infer + export GeoJSON
python scripts/detect/infer.py
python scripts/detect/export_polygons_geojson.py
```

## Eval flow — correct order

```bash
# 1. Quick mAP50 check (fast, not the production metric)
python scripts/detect/evaluate.py --weights <path> --split val

# 2. Per-detection RCA at low conf + optional bucket overlays (one SAHI pass)
python scripts/detect/per_detection_rca.py \
    --weights <path> --sahi --conf 0.05 --run-name <name> \
    --render-buckets        # auto-renders confident_fp / worst_small_fn / large_fp

# 3. Production threshold sweep (slow — re-runs SAHI per conf/iou combo)
python scripts/detect/sahi_threshold_sweep.py --weights <path> \
    --config configs/yolo/thresholds_sahi.yaml --run-name <name>

# 4. Ramp step eval (calls model.val() + RCA internally; appends ramp_curve.csv)
python scripts/detect/ramp_eval.py --run R0 --weights <path>
```

Core inference + matching logic lives in `src/utils/rca.py` — imported by both `per_detection_rca.py` and `sahi_threshold_sweep.py`. `compute_sahi_confusion_matrix.py` is only for pre-exported label files; `per_detection_rca.py` is the richer tool for active work.

## Label audit / bucket overlays

```bash
# Bucket overlay rendering (consumes per_detection.csv from RCA)
python scripts/labeling/bucket_overlays.py \
    --csv outputs/eval/<run_name>/per_detection.csv --bucket confident_fp --top 20

# Label disagreement overlays with Esri inset
python scripts/labeling/label_disagreement.py --weights <path> --split test --esri-inset

# Vintage audit — verify tile filenames match Roboflow
python scripts/labeling/vintage_audit.py \
    --data-root data/yolo/naip --tile-index data/interim/tile_index.json
```

## Key paths — Stage 1

| What | Where |
|------|-------|
| Best weights | `runs/segment/<run_name>/weights/best.pt` |
| Experiment configs | `configs/yolo/experiments_joint_v2_ramp.yaml` (active ramp), `experiments.yaml` (prod) |
| Threshold configs | `configs/yolo/thresholds_sahi.yaml` |
| Eval outputs | `outputs/eval/<run_name>/{per_detection.csv, sahi_threshold_sweep.csv, failure_modes.json}` |
| Ramp curve | `outputs/eval/ramp_curve.csv` |
| Label viz | `outputs/label_viz/<run_name>/<bucket>/*.png` |
| RCA + matching | `src/utils/rca.py`, `src/utils/det_match.py` |
| Overlay renderer | `src/utils/overlay_render.py` |
| Training helpers | `src/utils/train_utils.py` |

## Hyperparameter invariants (must survive any YAML edit)

```yaml
optimizer: SGD          # NEVER auto — silently overrides lr0 to AdamW(0.002)
lr0: 0.001
auto_augment: null      # default randaugment caused starburst artifacts on 160px Duke
erasing: 0.0
translate: 0.0
mosaic: 0.5
copy_paste: 0.0
```

R0 warm-starts from `models/sahi_baseline_train7.pt`. R1+ should warm-start from R0's best.pt (update the ramp YAML when advancing).
