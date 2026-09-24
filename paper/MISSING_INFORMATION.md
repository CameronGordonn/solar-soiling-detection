# Missing information — open items before submission

Ordered by how much they block. Each names the repo file most likely to hold the answer.

## Blocking

**M1 — The introduction's four external statistics are unverified.**
2,383 GW / 510 GW (IRENA 2026), >8% of world electricity (IEA 2026), 83 bn kWh (EIA 2023),
6 M installations (SEIA 2026). None appears in any repo artifact; they came in with the
draft. *Where to look:* nowhere in-repo — these need the primary sources pulled and the
access dates recorded. The EIA citation is dated 2023 for a 2024 projection, which a
reviewer will flag.

**M2 — The permit recall audit still hard-codes a 2022 imagery vintage against 2025 imagery.**
`outputs/economics/permit_recall_audit_w2_21cm.md` states "NAIP vintage: 2022 (every
tile_index source is `naip_sc_2022_*.tif`)", but the AOI tile index records
`vintage: scc_2025`, `gsd_ground_m: 0.2043`. The 73.6% imaged-era figure is therefore
**conservative** (with a 2025 cutoff the denominator is essentially all 434 and recall is
55.3%), so the direction is safe — but the generated prose is wrong and will not survive
review. *Fix:* make the vintage a per-run field read from the tile index in
`scripts/analyze/permit_recall_audit.py`, then re-run.

**M3 — Response count for the 50-card mailing.**
The paper says the mailing was a deliverability test and cannot resolve a response rate.
It should still state the count to date, including if it is zero. *Where to look:*
`docs/MAILER_PIPELINE.md`, `outputs/aoi/*/feedback.json`, or the Lob dashboard. A stated
null is more credible than an open-ended present tense.

## Should resolve

**M4 — Detection example strip (the resolution argument) has no figure.**
The one figure a reviewer will most want — one tile at 60 cm beside the same tile at
21 cm, with GT, detections and SAM2 masks overlaid — is not generated, because the full
21 cm tiles (`data/interim/scc21_labelset/images`) and `tile_labels/` are part of the
`stage1-gate` handoff group and are **not present in this working copy**.
*Fix:* `gh release download` per `DATA.md`, then extend `scripts/labeling/bucket_overlays.py`.

**M5 — The shortfall table was recomputed and disagrees with the 2026-08-23 brief.**
`docs/PAPER_REWRITE_BRIEF_20260823.md` §4 gives 13.81× → 3.02×, best 2.76×. Recomputing
from `site_economics.csv` today gives 45.9× → 5.5×, best 3.60×, with band counts matching
exactly (877/796/119/35/16/12/10). The difference is the ×0.500 level calibration applied
2026-08-30, which postdates the brief. **The paper uses the current figures.** Someone
should confirm the brief is stale rather than the calibration being misapplied, and mark
the brief in place.

**M6 — The "15 states" / "six states" conflict is resolved but under-documented.**
Panel rows (891) span 6 states; the source station CSV (255 stations) spans 15; the merged
matrix carries 257 stations. `docs/CANONICAL_NUMBERS.md` says "15 states" next to the
891-row count, which reads as attributing 15 states to the panel rows. *Fix:* split that
row in CANONICAL_NUMBERS.

**M7 — Author order and ORCIDs.**
Names and contact confirmed by Cameron 2026-09-22: Cameron Gordon, Joshua Ramirez,
Akshitha Nagaraj, Craig Fellers; `betterbehaviorfoundation@gmail.com`;
`betterbehaviorfoundation.com`. Still open: author *order* (currently the order given,
not a contribution ranking) and ORCIDs, if the venue wants them.

**M8 — Venue and format not chosen.**
Currently generic two-column `article`. Section order, length limit, anonymisation and
citation style all depend on the target. `docs/PAPER_REWRITE_BRIEF_20260823.md` §0 lists
three candidate titles; the paper uses a variant of the first.

## Known-unknowable (state, do not chase)

- **Inter-annotator agreement on masks** cannot be measured on the current labelling
  platform (it deduplicates; one tile cannot go to two labellers). So we cannot say whether
  0.844 median IoU is the model ceiling or the label-noise floor. Stated as a limitation.
- **No ground-truth production data exists for any scored rooftop.** This is the central
  unvalidated transfer and no in-repo work closes it; the PVDAQ lane
  (`docs/PVDAQ_LANE_HANDOFF_20260831.md`) is the proposal, and its gate is specified but
  not yet runnable.
- **The biological/wash-only channel has never been measured in this climate.** Excluded
  from the IWSR labels by construction.
