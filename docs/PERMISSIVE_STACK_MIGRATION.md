# Permissive-Stack Migration Plan — Stage 1 Detection (AGPL escape)

> **Handoff doc for a fresh instance.** You did not see the conversation that produced this.
> Read it top-to-bottom, then `CLAUDE.md` + `.claude/rules/stage1-detect.md` before touching code.
> Status as of 2026-07-06.

---

## 0. Mission

Replace the **AGPL-licensed Ultralytics YOLOv11** Stage 1 detector with a **permissively-licensed
(Apache-2.0) stack**, without regressing detection quality on our data, and exploit **30cm NAIP**
imagery for the recall win we can't get at 60cm.

**Why:** Ultralytics' commercial license is ~$5k/yr for our team size, and AGPL's network clause
(§13) is triggered by our FastAPI SaaS (`solarsoiled-api.onrender.com`). Cameron has decided to
pursue the escape rather than pay.

**Target stack:**
- **Detector:** RF-DETR (Roboflow, Apache-2.0) — primary. YOLOX (Apache-2.0) — fallback only.
- **Mask/area stage:** SAM2 (`facebook/sam2-hiera-large`, Apache-2.0), or SAM3 if released +
  permissive. Role: box → crisp mask → `minAreaRect` → m². This is our *area* accuracy, a product
  differentiator (m² → energy → $).
- **Model topology:** ONE scale-robust model trained on 30cm **and** 60cm renders — NOT two weight
  sets — unless per-GSD eval proves a split is needed (see W3).

---

## 1. What you're inheriting

### The spike (already built + run)
`notebooks/detector_bakeoff_spike.ipynb` — reuses Drive `/content/drive/MyDrive/solar-soiling`
(`naip.zip` + `models/r2_cameron_20260509.pt`), extracts with `valid`→`val`, scores all detectors
through a verbatim port of `src/utils/det_match.match_predictions` (greedy IoU@0.5) + a self-
contained AP@0.50 + best-F1 conf sweep. Colab-Pro, GPU, ~10-min runs.

### Results so far (whole-tile, no SAHI, same tiles/evaluator)
| model | split | AP@50 | best-F1 | P | R |
|---|---|---|---|---|---|
| **R2-cameron** (incumbent, AGPL) | test | **0.616** | **0.645** | 0.77 | 0.41 |
| R2-cameron | val | 0.710 | 0.688 | 0.82 | 0.54 |
| RF-DETR (1st cold run, res 560, 60ep) | test | 0.415 | 0.467 | 0.66 | 0.33 |
| RF-DETR | val | 0.508 | 0.533 | 0.73 | 0.40 |

**Read:** RF-DETR is behind, but the comparison is **not yet fair** — R2 rode a domain warm-start
chain (sahi_baseline → R0 → R2, multiple relabel rounds); RF-DETR got one cold, low-resolution,
possibly-undertrained pass. Both are recall-limited (small-panel misses) — the exact thing 30cm
imagery and higher inference resolution fix. **Do not conclude anything until W1 gives RF-DETR a
fair run.** Also note: R2's registry `map50_test=0.263` is **wrong/stale** — trust the freshly
measured numbers above, not the registry metadata.

### W1 fair rematch — RESOLVED 2026-08-06 (21cm scc21 chips, matched resolution)

Both arms trained from their own base on the **same** 640px chips cropped at native 0.21 m/px
(`data/yolo/scc21`, 332 train / 100 val / 196 test), at matched magnification — RF-DETR at 728
(multiple of 56), YOLO at 736 (nearest multiple of 32). Scored through the one shared evaluator.

| model | split | AP@50 | best-F1 | @conf | F1@0.4 | P | R | n_gt |
|---|---|---|---|---|---|---|---|---|
| R2-cameron 60cm *(reference only)* | val | 0.182 | 0.262 | 0.20 | 0.216 | 0.649 | 0.130 | 385 |
| R2-cameron 60cm *(reference only)* | test | 0.280 | 0.369 | 0.15 | 0.222 | 0.689 | 0.132 | 636 |
| YOLOv11-seg @736 *(parity target)* | val | 0.764 | **0.759** | 0.35 | 0.750 | 0.800 | 0.706 | 385 |
| YOLOv11-seg @736 *(parity target)* | test | 0.834 | **0.798** | 0.30 | 0.786 | 0.850 | 0.731 | 636 |
| **RF-DETR @728** | val | 0.792 | 0.751 | 0.30 | 0.747 | 0.766 | 0.730 | 385 |
| **RF-DETR @728** | test | **0.850** | **0.809** | 0.25 | 0.803 | 0.857 | 0.755 | 636 |

**Gate A: PASS.** RF-DETR is at parity with a freshly-trained YOLOv11-seg on identical data — ahead
on test (+0.016 AP50, +0.011 F1, +0.024 recall), behind by 0.008 F1 on val. Every gap is inside
sampling noise (recall SE ≈ 0.017 at n=636), which is what parity looks like. **The port is
justified on evidence; the AGPL dependency is no longer load-bearing for detection quality.**

RF-DETR's edge is specifically in **recall** (0.755 vs 0.731 at the operating point) — the axis the
whole 21cm relabel was meant to move.

### W2 — full train set + the production-path gate — **PASSED 2026-08-07**

The W1 table above was built on 83 of 175 train tiles. The relabeling sprint finished, the set was
rebuilt from Roboflow v2 (244 tiles; 5 unreviewed seeds skipped), and **train roughly doubled**:
332 → 680 chips, 1,107 → 2,873 chip polygons. Val and test are byte-identical to W1 (385 / 636 chip
GT), so this is a clean data-delta.

Chip-level, same notebook evaluator:

| model | split | AP@50 | best-F1 | @conf | P | R | n_gt |
|---|---|---|---|---|---|---|---|
| RF-DETR @728 W1 (83 tiles) | test | 0.850 | 0.809 | 0.25 | 0.857 | 0.755 | 636 |
| **RF-DETR @728 W2 (170 tiles)** | test | **0.883** | **0.855** | 0.40 | 0.840 | **0.869** | 636 |
| RF-DETR @728 W2 | val | 0.821 | 0.788 | 0.40 | 0.751 | 0.829 | 385 |

Tile-level, through the production chip-grid path — **this is the gate** (`eval_tile_f1.py`):

| model | conf\* (val-tuned) | test P | test R | test F1 | 95% CI | n_gt |
|---|---|---|---|---|---|---|
| W1 anchor | 0.40 | 0.853 | 0.756 | 0.8015 | [0.773, 0.830] | 585 |
| **W2** | 0.50 | 0.850 | **0.803** | **0.8260** | [0.798, 0.853] | 585 |

**Paired** bootstrap over the same 49 tiles: F1 **+0.0246** [+0.0051, +0.0441], P=0.993; recall
**+0.0479** [+0.0202, +0.0753], P=0.999. The marginal CIs overlap heavily and would have read as
"no difference" — the paired test is the right one for two models scored on identical tiles, and it
excludes zero. The extra labels bought real recall.

Also established: **the chip-grid + NMS seam merge is not lossy.** W1 scored 0.809 chip-level and
0.8015 tile-level — within 0.008 on differently-counted GT — so running 4 windows per tile and
merging neither drops nor duplicates detections. That was an open question about the W5 design.

**Caveats that must travel with the W1 numbers below:**
1. **Not comparable to the 0.570 SAHI F1 baseline.** Different labels (2.1× more objects on the
   same val footprint), different metric (chip-level whole-tile, *no SAHI*), different imagery.
   These numbers do **not** clear the 0.65 GA gate — that gate is SAHI F1 and remains unmeasured
   on 21cm. **[Superseded 2026-08-07:** the SAHI gate was retired and replaced by tile-level box F1
   from `scripts/detect/eval_tile_f1.py`. W2 **passed** it at test F1 0.8260. This caveat is kept
   because it was true of the W1 numbers when they were written.**]**
2. **`best_F1` is threshold-optimised on the eval split** and is therefore optimistic. `F1@0.4` is
   the honest fixed-operating-point column.
3. **Train was 83 of 175 source tiles** (Akshitha's 71 seeded tiles were unlabeled at build time).
   Both arms saw identical data, so the *relative* verdict holds; re-run for the absolute numbers.
4. R2's row is a 60cm-trained model on 21cm imagery — a domain-shift reference, not a competitor.

Reproduce: `notebooks/w1_rematch_scc21.ipynb` (Colab, GPU). Artifacts:
`rfdetr_w1_20260806.pth` + `rfdetr_w1_20260806_manifest.json` in Drive `BBF Materials/models/`.

### The architectural fact that makes the port feasible
The product path is **file-boundaried**: `scripts/detect/infer.py` writes YOLO-format normalized
polygon `.txt` labels → `scripts/detect/export_polygons_geojson.py` reads them → `arrays.geojson`.
**Nothing downstream imports ultralytics** — `src/solarsoiled/` (CLI/API/product) and all of Stage 2
consume files, not model objects. So the port only has to reproduce the `.txt` output contract.

### AGPL blast radius (files importing `ultralytics`, all Stage 1)
```
scripts/detect/{train,train_experiment_matrix,evaluate,ramp_eval,infer,
                sahi_threshold_sweep,eval_threshold_sweep}.py
scripts/labeling/{bucket_overlays,label_disagreement}.py
scripts/research/08_pseudo_label.py        # research, ignore
src/utils/rca.py
```
Serving-critical subset (must be swapped for the product): `infer.py` +
`rca.py` inference helpers. The rest is dev-time (training/eval tooling).

---

## 2. Guardrails (do not violate)

1. **Data > model, and resolution > architecture here.** Our recall ceiling is a data/GSD problem,
   not an architecture problem. Don't rat-hole on detector tuning if the win is in 30cm data.
2. **Ship no AGPL-derivative weights.** R2/`yolo11*` weights are AGPL assets; a fine-tune inherits
   the license. The permissive model must be trained from a permissive base (RF-DETR/DINOv2, YOLOX).
   R2 stays only as an eval *baseline*, never in the shipped product.
3. **The `.txt`/GeoJSON file boundary is sacred.** Do not make anything under `src/solarsoiled/` or
   Stage 2 import the new detector library. Reproduce the label-file contract instead.
4. **Evidence-gated decisions.** Don't split into two weight sets, and don't abandon RF-DETR, without
   a per-GSD / fair-run measurement. Let the eval force the call.
5. `data/interim/tile_index.json` is sacred (CRS/affine). GSD is derived from its affine.

---

## 3. Workstreams (sequenced)

### W1 — Fair detector rematch — ✅ **PASSED 2026-08-06** *(see §1 for the result table)*
**Outcome:** RF-DETR @728 reached parity with a freshly-trained YOLOv11-seg @736 on identical 21cm
chips (test 0.850/0.809 vs 0.834/0.798). Gate A clears; W2–W6 are unblocked. Re-run once the full
train set lands to firm up the absolute numbers.

**Objective (as originally written):** give RF-DETR a fair run and see if it reaches R2 parity.
- In the notebook's RF-DETR cell: `RFDETRBase(resolution=728)` (must be a multiple of 56; default
  560), `batch_size=4, grad_accum_steps=4` for VRAM, raise `EPOCHS` (~100, watch overfit at 171 imgs).
- **Convergence check:** RF-DETR evaluates the `valid` split each epoch — confirm val AP plateaued;
  if still climbing at the last epoch, it was undertrained — raise epochs.
- Re-score through the notebook evaluator; compare to R2 row.
- **Gate A (see §4).**

### W2 — Dual-GSD dataset build
**Objective:** produce 30cm + 60cm renders of every labeled array **without re-labeling** (labels are
geospatial polygons; resample from `tile_index.json` CRS/affine).
- New script `scripts/data/build_multiscale_tiles.py`: for each labeled array, render tiles at both
  ~0.3m and ~0.6m GSD; re-tile at the correct chip footprint (640px = 192m @30cm vs 384m @60cm);
  emit a `tile_index.json`-equivalent per GSD (see `.claude/rules/data-pipeline.md`).
- Acquire 30cm NAIP for target CA counties (Cameron to confirm which — see §6).
- CORRECTION (2026-07-10, Cameron): R2 is **NAIP-only**. The earlier claim that R2 is joint
  NAIP(60cm)+Duke(30cm) is **false** — the Duke(30cm) integration never worked and was abandoned
  months ago. So scale-mixing is **NOT** proven on this data; W2/W3 multiscale is an unproven bet
  here (still the standard ML approach, but treat it as a hypothesis to validate at W3 eval, not a
  known-good).

### W3 — Unified scale-robust model + per-GSD eval
**Objective:** train ONE detector on 30cm+60cm renders with scale augmentation; decide unified-vs-split
by measurement.
- Train the W1-winning detector on the combined multiscale set.
- Evaluate **separately** on held-out 30cm and 60cm sets.
- **Decision:** if one GSD regime lags badly → *then* build a dedicated weight set for it and route by
  GSD. Otherwise ship the unified model. Default is unified (half the maintenance, 2× effective data,
  matches ML norm — note: no in-house precedent, since the Duke scale-mix was abandoned; validate it).

### W4 — Mask/area stage (SAM)
**Objective:** wire box → SAM mask → `minAreaRect` → m² and validate area accuracy.

#### Measured 2026-08-06 — SAM2 passes, but the prompt box must be *under*-sized
Run on 21cm scc21 val chips, SAM2 `hiera-large`, CPU, `multimask_output=False`. The whole-roof
blowout observed in early spot-checks is a **prompt-box size** failure, not a SAM2 failure:

| prompt box | med IoU | med area/GT | roof-grab (>2× GT area) |
|---|---|---|---|
| shrink 15% | **0.824** | 1.03 | 0% |
| exact GT box | 0.809 | 1.13 | 0% |
| dilate 25% | 0.733 | 1.30 | 0% |
| **dilate 50%** | 0.551 | 1.80 | **47.5%** |
| dilate 25% + shift 15% | 0.642 | 1.40 | 15% |
| shift 15% (no dilate) | 0.774 | 1.08 | 0% |

Shifted boxes are safe; **oversized boxes are not** — past ~25% dilation SAM2 escapes onto the roof
plane. A no-SAM control (box used directly as the polygon) scores only **0.509 IoU / 78.5% area
error**, so SAM2 earns its place decisively — do not drop it.

**Two rules for the production path:** (1) shrink each detector box ~10–15% before prompting — SAM
expands to the true boundary anyway; (2) guard — if mask area > 2× prompt-box area, fall back to the
box. Area is the m² → kW → $ input, so a 1.8× blowout is a 1.8× dollar error.

**Cost:** `set_image` **30.6 s/chip** on CPU vs `predict` **0.14 s/box** — cost is per *chip*, not per
box. Skip chips with zero detections and reuse one `set_image` across all boxes in a chip. Full
996-chip AOI ≈ **8.6 h CPU**, minutes on GPU.

- Reproduce: `scripts/` probes in the W4 scratch work; the notebook's SAM section measures the same
  ceiling (GT-box-prompted mask vs GT polygon: IoU + area error).
- Productionize as `scripts/analyze/` (or a mask step in the detect path): prompt SAM with the
  detector's boxes, fit `minAreaRect`, write area into the array feature record. Pull per-tile GSD
  from `tile_index.json` for real m².
- SAM3 slots into the identical `set_image` / `predict(box=...)` API if released + Apache — swap two
  lines.

### W5 wiring gaps — audited 2026-08-07, BEFORE flipping any alias

> **Status 2026-08-26: items 1, 2 and 4 are CLOSED; item 3 stands.** Kept as written because it is
> a dated audit and the record of why the flip waited. `95d3368` (2026-08-07) made
> `solarsoiled run` resolve the model's `gsd_ground_m` and tile from the 21cm county service below
> 0.35 (item 1), gave `fetch_scc_imagery.py` an `--aoi` (item 2), and routed `cli.py detect` on the
> resolved model's `detector` field (item 4). Verified end to end on 2026-08-26: `run --weights
> production` on a small SCC bbox tiled `scc21`, detected 36 arrays as `rfdetr-w2-20260807`, scored
> and recommended. **Do not cite item 1 as a live constraint** — several docs did, and sent people
> to `--skip-tile` for no reason. What survives is geographic: the county service covers Santa Cruz
> only.

The detector is ready (gate passed, 0.826). The *product pipeline* is not, and the gap is bigger
than "point the tiler somewhere else". Four concrete items, in the order they bite:

1. **`cli.py tile` fetches 60cm NAIP.** It calls `scripts.data.tile_naip_image`. W2 has never seen
   60cm. Flipping the `production` alias without fixing this feeds a 21cm-trained model 60cm tiles
   — the single most likely way to ship a silent regression.
2. **`fetch_scc_imagery.py` cannot tile an arbitrary AOI.** It is *tile-index driven*: it re-fetches
   a 21cm version of each footprint already in `data/interim/tile_index.json` (`--tiles`, `--all`,
   `--tile-index`). There is no `--aoi`. It was built to re-image 249 known footprints for the
   labeling sprint, not to serve as the AOI tiler. Generalizing it means adding bbox → 21cm tile
   grid → `tile_index` emission.
3. **The NAIP tile index has no `gsd_ground_m`.** Fields are `bounds / crs / height / source /
   transform / width`; the 21cm index adds `gsd_ground_m`, `naip_tile`, `vintage`. `rfdetr_infer.py`
   *requires* `gsd_ground_m` and **skips any tile lacking it** — so pointing it at an existing AOI's
   tiles yields an empty `arrays.geojson` with only a warning. Fails quietly, which is worse than
   failing loudly.
4. **`cli.py detect` calls the ultralytics `infer.py`.** It needs to route to `rfdetr_infer.py`
   when the resolved weights are an RF-DETR checkpoint.

**Two ways forward, and they are different products.** For the Santa Cruz pilot the 249-tile 21cm
set already exists and covers the AOI (15.0 km²), so production can run on it today and only items
3–4 matter. Serving an *arbitrary* AOI at 21cm requires item 2, which is real work. County imagery
is Santa Cruz only regardless — there is no 21cm outside it, so "anywhere" implies NAIP and a
GSD-robustness result we do not have.

### W5 — The port (swap ultralytics behind the file boundary)
**Objective:** the shipped product runs the permissive detector; downstream untouched.
- New `scripts/detect/rfdetr_infer.py` mirroring `infer.py`'s **output contract**: YOLO-format `.txt`
  labels in `runs/segment/<name>/labels/` + `manifest.json`. Then `export_polygons_geojson.py` and all
  of Stage 2 work unchanged.
- Point `src/solarsoiled/cli.py::detect` at the new infer (it currently imports
  `scripts.detect.infer.main`).
- **SAHI question — RESOLVED 2026-08-06 as (b), and it is not really SAHI.** Measured on the scc21
  labels (n=2128 objects, 640px chips @ 0.208 m/px ground): median object longest side is **26.7 px**
  and **84% of objects are COCO-small**. RF-DETR trained on 640 chips upscaled to its 728 input, so
  it learned a median object of **30.3 px**. Feeding a whole 1200px tile to the 728 net downscales
  0.61×, making that median object **16.2 px — 0.53× of the appearance it was trained on**, with the
  p10 object falling to ~9 px. That is a severe scale shift precisely in the size class that is 84%
  of the dataset.

  The fix is not a library: `import_21cm_from_roboflow.py` already chips each 1200px tile into a
  **2×2 grid of 640px chips** (`tile_000000_r0c0…r1c1`, ~80px overlap, 4 per parent tile). Running
  inference over those same 4 windows reproduces training appearance **exactly (1.00×)**. So
  `rfdetr_infer.py` re-uses the existing chipping, runs 4 windows per tile, and merges with NMS
  across the seams — no SAHI dependency, no adapter, and deterministic tile↔chip geometry from
  `tile_index_chips.json`. Whole-tile 728 inference should not be used.
- Add an RF-DETR predictor path to `src/utils/rca.py` so `per_detection_rca.py` /
  `sahi_threshold_sweep.py` work on the new model (parallel to the ultralytics path; keep both).

### W6 — Licensing cleanup, registry, docs
- Add the new permissive model to `models/registry.yaml`; bump `aliases.latest`/`production` when it
  clears the gate. Mark R2 `beta`/eval-only.
- Remove/guard ultralytics as a *runtime* dependency of the served product (keep it as a dev/eval
  extra only if needed for the R2 baseline).
- Update `CLAUDE.md`, `.claude/rules/stage1-detect.md`, `docs/Q2_PLAN.md`.
- **Verify** (see `verify` skill): drive `solarsoiled detect` end-to-end on an AOI and confirm
  `arrays.geojson` is produced by the permissive path.

---

## 4. Decision gate A (after W1)

**Question:** does a fairly-trained permissive detector reach R2-cameron's freshly-measured quality
on the **test** split (AP@50 ≈ 0.60+, best-F1 ≈ 0.63+)?

- **Reaches parity (within noise):** proceed W2→W6. The port is justified on both licensing *and*
  quality.
- **Closes most of the gap but not all:** continue to W2/W3 — 30cm data is the likely closer; re-gate
  after the unified multiscale model.
- **Still far behind after a fair run + 30cm:** escalate to Cameron. The choice becomes a business
  tradeoff — pay Ultralytics $5k/yr, accept a quality hit for licensing freedom, or invest in labels
  (which lifts *any* backbone). Do not silently ship a regression.

---

## 5. Reference (paths, facts, commands)

| What | Where |
|---|---|
| Spike notebook | `notebooks/detector_bakeoff_spike.ipynb` |
| Colab Drive | `/content/drive/MyDrive/solar-soiling` (`naip.zip`, `models/r2_cameron_20260509.pt`) |
| Matcher (eval truth) | `src/utils/det_match.py` (greedy IoU@0.5) |
| Product infer (to mirror) | `scripts/detect/infer.py` → `.txt` labels |
| GeoJSON export (boundary) | `scripts/detect/export_polygons_geojson.py` |
| CLI detect entrypoint | `src/solarsoiled/cli.py::detect` |
| Registry | `models/registry.yaml` |
| Dataset counts | train=171 val=27 test=50 |
| Prod thresholds | conf=0.40, iou=0.50 |

**Licence checks — SAM3 resolved 2026-08-06.** SAM 3 **was** released (2025-11-19,
`facebookresearch/sam3`, SAM 3.1 after) under Meta's bespoke **"SAM License"**, not Apache-2.0 —
but read on the merits it is **not a blocker for us**: you own your derivative works, commercial use
is permitted with no user/revenue thresholds, and the share-alike-ish clause ("if you distribute SAM
Materials or derivative works to a third party you may only do so under the terms of this Agreement")
triggers on **distribution**, which we don't do — we run a SaaS and ship GeoJSON. That is the exact
*opposite* of AGPL §13, which triggers on network use. SAM3 is therefore **less** constraining for
SolarSoiled than the licence we are escaping. ITAR/military/nuclear/weapons and no-reverse-engineering
restrictions are irrelevant here. Two real deltas vs Apache-2.0, worth knowing: **no patent grant**,
and **Meta may unilaterally modify the Agreement** (§8), plus a litigation-termination clause.

**So licence does not decide SAM2-vs-SAM3 — measurement should.** The reason not to swap *today* is
priority, not law: §W4 shows our mask error is driven by **oversized prompt boxes**, which no mask
model fixes, and SAM2 is already at 0.824 median IoU on ~26px arrays (near annotation noise). SAM3's
headline advance is concept/text prompting, which we don't need since we already have boxes. Fix the
prompting first, then A/B SAM3 on the same probe and adopt it if it measurably wins.

Still to confirm: RF-DETR is Apache-2.0. Note `rfdetr` training deps need the extra: `pip install "rfdetr[train,loggers]"`.
For RF-DETR serving throughput call `model.optimize_for_inference(dtype=torch.float16)`.

---

## 6. Non-goals / separate threads

- **Permit-sweep + racking-angle** work is a *separate workstream* (Stage 2 structural features /
  product path) — not part of this migration. Don't entangle.
- Don't retrain things that already work (Stage 2 risk model is GA-ready; leave it).

## 7. Open questions for Cameron (surface early)

1. Which CA counties' 30cm NAIP to acquire first (highest-value AOIs)?
2. Is DETR CPU-inference latency acceptable on the Render serving box, or budget a small GPU?
3. Confirm the RF-DETR-vs-YOLOX call — is RF-DETR (if it clears Gate A) the committed detector?
