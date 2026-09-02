# Commercialization constraints

Durable legal/licensing constraints to resolve **before charging customers**. Not
quarter-specific — this outlives any single plan, so it lives on its own rather than inside a
status doc.

## Ultralytics AGPL-3.0 — RESOLVED 2026-08-07, by swapping the backbone

**The constraint was real and it is now cleared for the shipping path.** Stage 1 detection ran
on Ultralytics YOLOv11 (**AGPL-3.0**), whose network-use clause makes it unsuitable for a
closed commercial or SaaS offering. Two routes were open: (a) buy an Ultralytics commercial
licence, or (b) swap the backbone to a permissive one.

**We took (b).** The shipping detector is **RF-DETR @728 (Apache-2.0) + SAM2**, and it did not
cost quality — it passed the Stage 1 GA gate on 2026-08-07 at tile-level box-F1 **0.8260**
(95% CI [0.798, 0.853], n_gt 585), clearing all three conditions. Full history:
[PERMISSIVE_STACK_MIGRATION.md](PERMISSIVE_STACK_MIGRATION.md).

### What is still AGPL, and therefore still cannot ship

The constraint is cleared for the *new* stack, not retroactively for everything in the tree.
One live exposure remains (the first was resolved 2026-08-23):

1. **RESOLVED 2026-08-23 — `production` now resolves to `rfdetr-w2-20260807` (Apache-2.0).**
   `tests/test_registry.py::test_production_alias_is_permissively_licensed` asserts this so
   it cannot silently regress. R2 remains in the registry as `stage1-60cm-legacy` for the
   60cm path; it is AGPL and eval-only. **The domain constraint is unchanged and is geographic** — W2 is
   gated on 21cm SCC imagery, and `solarsoiled run` already tiles from the county service for
   it (routing on `gsd_ground_m` since `95d3368`), so no pre-staging or `--skip-tile` is needed
   inside the AOI. Outside Santa Cruz County the service has no coverage: use
   `stage1-60cm-legacy` (AGPL, eval-only) or re-gate W2 at 60cm. Superseded text follows.

   <details><summary>Superseded text (kept so the decision is auditable)</summary>

   > `R2-cameron-20260509` is still the registered `production` alias in
   > `models/registry.yaml`, and it is AGPL. It is eval-only and must not ship. Flipping the
   > registry to the RF-DETR checkpoint is the remaining action item — until that happens, a
   > caller who resolves `--weights production` gets the AGPL model. This is the one thing on
   > this page that can still bite.

   </details>
2. **The public snapshot (`Better-Behavior-Foundation/SolarSoiled`) ships under AGPL-3.0**,
   consistent with the Ultralytics dependency it mirrored. It is a read-only reference
   surface, not a grant of commercial reuse. Archived read-only as of the 2026-07 repo
   cleanup; see the private repo's `SYNC.md`. Re-licensing it is a separate decision and
   should not be assumed to follow automatically from the backbone swap.

### Scripts that still import Ultralytics

⚠️ **CORRECTION, measured 2026-09-01: "not on the shipping path" is FALSE at the packaging
level, and this section understated the exposure.** Two facts, both verified:

1. `ultralytics` is a **base dependency** in `pyproject.toml` (not an extra), so a plain
   `pip install -e .` installs AGPL-3.0 code for everyone.
2. `import solarsoiled.cli` — the shipped entrypoint — pulls in **98 ultralytics submodules**.
   `cli.py` imports `scripts.detect.evaluate` and friends at module level, and those do
   `from ultralytics import YOLO` at module level in turn. Reproduce:
   `python -c "import sys, solarsoiled.cli; print(len([m for m in sys.modules if m.startswith('ultralytics')]))"`

So the *model* is permissive but the *package* still links AGPL code on every import, while
`pyproject.toml` declares `license = { text = "MIT" }` and the repo has **no LICENSE file at
all**. Those three facts cannot all be right at once. Flipping the `production` alias fixed
the weights question; it did not fix this one.

The fix is mechanical, not architectural: make the ultralytics imports in `cli.py` lazy — the
same treatment `rfdetr` and `sam2` already get, which is why *they* can live in an extra — and
move `ultralytics` + `sahi` into a `legacy` extra. Until that is done, treat the distributable
as AGPL-encumbered regardless of what the metadata says, and **do not** relicense anything to
MIT on the strength of the backbone swap alone.

The scripts below remain in the tree for evaluating the legacy baseline:

- `scripts/detect/train.py`
- `scripts/detect/train_experiment_matrix.py`
- `scripts/detect/infer.py`
- `scripts/detect/eval_threshold_sweep.py`
- `scripts/detect/per_detection_rca.py`
- `scripts/detect/sahi_threshold_sweep.py`
- `scripts/detect/ramp_eval.py`

The rest of the pipeline — CLI, Stage 2 risk model, manifest system, API — is
backbone-agnostic and always was.

## SAM 3 is not a drop-in

SAM 3 was released in November 2025 but under a **custom Meta SAM Licence, not Apache-2.0**.
Staying on SAM2 is a deliberate licensing choice, not inertia. Re-check the licence terms
before any upgrade.

## Before charging, also confirm

- **Imagery terms.** NAIP is public domain; the 2025 Santa Cruz County 21cm imagery is served
  from a county MapServer and its terms of use have **not** been reviewed for commercial
  redistribution. Derived polygons are probably fine; re-serving tiles is probably not.
- **Street View imagery is Google-licensed** and must not be republished — it is also imagery
  of private homes. See the privacy note in `scripts/analyze/streetview_survey.py`.
- **PII.** Address- and APN-level data never reaches the public repo or the public site; the
  gate is the public repo's `SYNC.md`, and `scripts/product/build_dashboard_data.py` enforces
  it in code by refusing to write if an APN would be published.
