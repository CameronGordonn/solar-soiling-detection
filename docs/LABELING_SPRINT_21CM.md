# 21cm labeling sprint — Cameron + Akshitha

> ✅ **COMPLETE.** All 249 tiles were relabeled; `data/yolo/scc21` was rebuilt from Roboflow v2
> on 2026-08-07 (244 of 249 tiles — 5 seeded tiles left unreviewed: `tile_000063/099/147/210/248`).
> The detector trained on it passed the Stage 1 gate at tile-level box-F1 **0.8260**. This doc is
> kept as the **labeling convention of record** and as the reason older labels are not comparable
> — §1's note that inter-annotator agreement was deliberately *not* measured is still an open
> item, and it bounds how far mask quality can be pushed.

**Goal:** a clean, full-resolution label set over the whole Santa Cruz AOI on 2025 21cm county
imagery. **Was due end of day Wednesday 2026-08-05.** Kicked off Friday 2026-07-31.

**Scope:** 249 tiles = the exact footprint of `data/interim/tile_index.json` (15.0 km²), each ~1200 ×
1196 px at ~0.21 m/px ground. That is 25× the area the 205 permit chips cover, at 2.3× the linear resolution of the 60cm NAIP set.

**Why now:** the median labeled array is 13.5 m² (~3.7 m across) and 25% are under 8 m² (~2.8 m).
At 60cm those were 8 px and 6 px wide — too small to label honestly. At 21cm they are 18 px and 13 px. The labels aren't wrong so much as they were drawn blind.

---

## 1. Roles and the split

One project — **`solar_arrays_scc21_tiles`** — with each split divided evenly between the two of you.

| Set | Tiles | Cameron | Akshitha | Mode |
|---|---|---|---|---|
| **val** | 25 | 13 | 12 | fresh, blind |
| **test** | 49 | 25 | 24 | fresh, blind |
| **train** | 175 | 87 | 88 | seeded (2022 polygons pre-attached) |
| **total** | 249 | **125** | **124** | |

Halving each split separately (rather than one cut across all 249) means neither of you owns an
entire measurement split — a systematic quirk in one person's labeling then shows up in both halves
of val instead of *defining* val. Within a split, each half is a contiguous west/east block, so
shared tile edges stay down to one seam per split.

**Note:** we are not measuring inter-annotator agreement this sprint. Roboflow deduplicates identical
images within a project, so the same tile cannot be served to both of you here, and measuring
agreement would have meant a second project. §3's convention sync is the substitute: it catches
drift but does not quantify it. The consequence — we won't know the human ceiling on F1, so treat
the 0.65 GA gate as a target against labels of unmeasured self-consistency.

That dedup also makes the uploader safe to re-run: re-sending tiles already uploaded is a no-op.

**Why val/test get no seeds:** they are the measurement instrument. Every number we report — the
0.570 SAHI F1, the 0.65 gate — is F1 against those polygons. Anchoring an annotator to a 60cm-era
polygon they cannot independently justify bends the ruler we measure with.

---

## 2. Labeling convention — read before your first tile

1. **Whole-array polygons. NOT per-panel.** At 21cm you can see individual modules and it is very
   tempting to trace them. Do not. Per-panel is the Duke convention (median 1.7 m² vs our 24 m²,
   KS=0.895 on area) and mixing the two is what broke joint training in May. One contiguous roof
   array = one polygon.
2. **Split on physical separation, not module gaps.** Two roof planes with a ridge between them =
   two polygons. Rows of modules on the same plane with walkway gaps = one polygon.
3. **Trace the array boundary, not the roof.** Follow the panel edge; don't include surrounding
   shingle.
4. **Label what you can see in THIS imagery (2025).** Ignore what the seed says if the panel isn't
   there — a 2023 removal is a correct deletion, not a mistake.
5. **Ground-mount and carport arrays count.** Class is always 0 (`solar_array`).
6. **Don't label:** skylights, dark flat roof patches, pool covers, solar water heaters (no cell
   grid pattern), vehicle roofs.
7. **Unsure?** Tag the image `needs_review` and move on. Do not guess — a coin-flip polygon in val
   is worse than a missing one.

### Seeded tiles (train) specifically
The pre-drawn polygon is a **2022 hint on 2025 imagery**, not truth. For each one: confirm, nudge to
the real boundary, or delete. Then **scan the rest of the tile for arrays the seed set never had** —
that scan is the entire point of this exercise. Expect to *add* more than you correct.

---

## 3. Convention sync — do this Saturday, before anything else

Each of you labels your **first 5 tiles**, then swap: open the other person's 5 in Roboflow and read
them as if you were reviewing. Get on a call and settle polygon tightness, the whole-array split
rule, and the ground-mount/carport edge cases.

Half an hour here prevents two people producing 8 hours each of mutually inconsistent labels. Label
your own five *before* looking at the other person's — reading first just copies their convention
without either of you noticing it was a choice.

---

## 4. Resolution integrity — the rules

The current 60cm dataset is already a casualty: `tile_index.json` says the source tiles are 512×512,
but every image in `data/yolo/naip/images/` is **640×640 JPEG**. A Roboflow version was generated
with *Resize: Stretch 640×640*, which upscaled and re-encoded them. Apply that same setting to a
1200px 21cm tile and it **downscales to 640 — discarding 47% of the linear resolution** we are
fetching. That is the single biggest risk in this sprint, and it's a project setting, not code.

**Acquisition** — enforced by `fetch_scc_imagery.py`, verified by `build_21cm_labelset.py`:
- Request in EPSG:2227 at native 0.208 m/px. Requesting in EPSG:3857 would bake the 1.25× Mercator
  inflation at this latitude into the resample.
- Never request more pixels than native — upsampling fabricates detail.
- Every tile is checked: 3857 bounds match the NAIP footprint within 0.5 m, ground GSD ≤ 0.30 m,
  ≥ 900 px per side, pixel std > 3 (catches blank/no-data responses). **Any failure blocks upload.**

**Storage and handoff:**
- **PNG, never JPEG.** JPEG's 8×8 DCT block is 1.7 m on the ground at 21cm — roughly half a
  residential array.
- uint8 RGB, alpha dropped, **no contrast stretch or tone-mapping** (it would shift the pixel statistics away from the NAIP set we still train on).
- `tile_index_21cm.json` carries CRS + affine + bounds for every tile. Required by
  `.claude/rules/data-pipeline.md`; the re-chip step needs it.

**Roboflow:**
- Annotators always work against the **original upload** at full resolution — annotation quality is safe regardless of project settings.
- Resizing happens at **version generation**, not upload. When generating: **Preprocessing → Resize: None.** Leave Auto-Orient at default (a no-op on our EXIF-less PNGs).
- **Do not use Roboflow's exported images.** Export annotations only and pair them with our local PNGs (§5). This makes the whole JPEG/resize question moot forever.

**Downstream:**
- Chip 1200px → 640px by **cropping at native resolution**, never resizing. Feeding a 1200px image
  to a model at `imgsz=640` silently resizes it and throws away everything above.
- 21cm/2025 lives in its own dataset dir. **Never write into `data/yolo/naip/`.**

**Vintage:**
- Everything is tagged `vintage:scc_2025`. A 2024 install labeled here scores as a false positive
  against 2022 NAIP — keep the two label sets separate, evaluate per-vintage.
- **Freeze the current val set before relabeling** so 0.570 SAHI F1 stays comparable for one cycle.

---

## 5. The commands

```bash
# 1. Fetch all 249 tiles at 21cm (~2 h, one export per NAIP footprint).
#    Resumable: rerunning skips tiles already on disk, so a dropped connection costs only the
#    remaining tiles. Transient timeouts retry with backoff. --overwrite forces a refetch.
PYTHONPATH=. conda run -n solar-soiling python scripts/data/fetch_scc_imagery.py --all \
  --service-url "https://sccgis.santacruzcountyca.gov/server/rest/services/Cache/Imagery_2025/MapServer/export"

# 2. Package + integrity-QA + assign. Exits non-zero if any tile fails.
PYTHONPATH=. conda run -n solar-soiling python scripts/data/build_21cm_labelset.py

# 3. Preview the upload plan (no API calls, no key needed)
PYTHONPATH=. conda run -n solar-soiling python scripts/data/upload_21cm_to_roboflow.py --dry-run

# 4. Smoke-test 4 tiles, eyeball them in Roboflow, then send the rest
PYTHONPATH=. conda run -n solar-soiling python scripts/data/upload_21cm_to_roboflow.py --limit 4
PYTHONPATH=. conda run -n solar-soiling python scripts/data/upload_21cm_to_roboflow.py

# 5. WEDNESDAY — rebuild locally from the export's labels + our lossless PNGs, chipped to 640px
#    at native resolution. Writes data/yolo/scc21/: YOLO-seg txt + COCO json + tile_index_chips.json
#    + import_qa.json.
PYTHONPATH=. conda run -n solar-soiling python scripts/data/import_21cm_from_roboflow.py \
  --export-dir ~/Downloads/solar_arrays_scc21_tiles.v1i.yolov11
```

> **⚠ Uploading a batch does not create a labeling job.** Step 4 puts tiles in a *batch*; until
> someone converts that batch into a **job** and assigns a labeler, the tiles are invisible under
> Annotate. Both train batches sat unassigned for three days in W31 because of this. After
> uploading, check `GET /{workspace}/{project}/batches` — any batch with `numJobs: 0` is a batch
> nobody can see — and confirm each job's `labeler` is the person who is meant to do it.

> **⚠ Roboflow counts a seeded prediction as an annotation.** The moment the train jobs were
> created they reported 136/175 "done" — 136 being exactly the number of tiles that got a 2022
> seed, none of which a human had opened. Job progress therefore **cannot** measure seeded batches:
> a seeded tile reads as finished from every angle (job counter, `unannotated`, export contents)
> except comparing its geometry to the seed. Step 5 does that comparison and **refuses to import**
> any train tile identical to its seed; pass `--skip-untouched-seeds` to build from the reviewed
> tiles and leave the rest out. Track seeded progress by diffing against the seed, not by the
> counter.

**Roboflow project:** everything goes to **`solar_arrays_scc21_tiles`** (Instance Segmentation,
already created). Keep it separate from `solar_arrays_scc_21cm` — that project holds the 205
panel-centred 50 m permit chips, and an export mixing 50 m chips with 245 m tiles is unusable as an
eval set.

> **⚠ Two project settings to change in the UI — the API cannot set either.**
> Roboflow created this project with **`preprocessing.resize = "Stretch to" 432×432`** by default.
> That is the exact failure that produced the 640×640 JPEG 60cm set, only worse: generating a
> version with it would squash 1200 px down to 432. Set **Preprocessing → Resize: None**. It does
> not affect uploads or the annotator (both use the original), and step 5 discards Roboflow's
> images anyway — but leave it and the first person to generate a version gets junk.
> The project was also created **public**. County imagery is public record, but the labels and AOI
> choice are ours; flip it to private unless you want it visible.

Batches arrive named `w31-<annotator>-<split>` (e.g. `w31-akshitha-train`); open yours under
**Annotate**.

**On the Wednesday export:** download *YOLOv11 Instance Segmentation* with **Preprocessing → Resize:
None**. Step 5 uses only the `.txt` files from it and pairs them with our local PNGs, so Roboflow's
images are discarded — that is what makes the JPEG/resize question moot. `import_qa.json` reports
which tiles came back with no labels (unfinished batches) and flags per-panel drift if the median
polygon area collapses against the 60cm set's 13.5 m².

**Backbone-agnostic on purpose.** Step 5 emits YOLO-seg `.txt` *and* COCO `instances_*.json` from the
same polygons: ultralytics eats the former, RF-DETR the latter (W1 in
[PERMISSIVE_STACK_MIGRATION.md](PERMISSIVE_STACK_MIGRATION.md)). Nothing in this sprint commits us to
a detector — that migration doc's own guardrail 1 is "data > model, and resolution > architecture
here," which is precisely the bet being made this week.

---

## 6. Schedule

| Day | Cameron | Akshitha |
|---|---|---|
| **Fri 7/31** | Fetch, package, QA, upload — **done**, batches are live | — |
| **Sat 8/1** | Convention sync: 5 tiles solo, swap, then a call (~30 min) | Same |
| **Sun 8/2** | Buffer / optional early start | Buffer |
| **Mon 8/3** | val + test (37 fresh tiles) | val + test (37) |
| **Tue 8/4** | train, first ~45 | Same |
| **Wed 8/5** | Finish train (~43); export; rebuild; QA gates | Finish train (~42) |

Rough budget: ~8–9 hours each across five days. Fresh tiles run ~3 min, seeded ~2 min, and expect the
21cm pass to surface noticeably more objects per tile than the 60cm labels held — that surplus is the
recall win, so don't rush past it to hit the clock.

---

## 7. Definition of done

- [ ] 249 tiles labeled, zero left in any Roboflow batch
- [ ] Export rebuilt against local PNGs; no image is JPEG and none is 640×640-from-1200
- [ ] COCO `instances_*.json` emitted alongside the YOLO txt, so W1 RF-DETR needs no reconversion
- [ ] `tile_index_21cm.json` covers every emitted tile; bounds round-trip within 0.5 m
- [ ] Old 60cm val frozen as a comparison baseline
- [ ] Polygon area distribution sanity-checked against the 60cm set — a median collapsing toward
      ~2 m² means someone drifted into per-panel labeling and that half needs redoing
