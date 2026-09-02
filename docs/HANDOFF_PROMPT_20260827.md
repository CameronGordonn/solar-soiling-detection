# Handoff prompt — paste this into a new session

---

Read `docs/HANDOFF_20260827.md` first, then `docs/BUG_AUDIT_20260827.md` and
`docs/PIPELINE_AUDIT_AND_PLAIN_REPORT_20260827.md`. They carry the measured state; do not
re-derive it.

**Context in one paragraph.** We can now extract per-roof soiling labels from public PVDAQ
production data, and they are validated against NREL (paired diff +0.19 pts, CI [-0.66,
+0.99], n=24). Measurement noise is SD 0.72 pts against a within-cell between-system spread of
1.41, so signal-to-noise is about 1.95x: enough to fit a feature model at n≈988, not enough to
rank two neighbouring roofs. Separately, the cleaning product is dead: zero of 2,494 AOI arrays
clear the cost of a wash, driven by NEC 690.12 effectively mandating MLPE since 2019 (which
defuses the substring nonlinearity), the $150 minimum service charge against a 3.5 kW median
array, and the fact that the 36 arrays with favourable hardware and tariff are smaller than
average. Craig wants per-array soiling **ranking** specifically; detection is solved.

**Your goal, in order.**

**1. Protect the work (do this first, it is fragile).**
24 files are uncommitted on `main`, including four bug fixes and ~6 hours of compute whose only
copy is seven JSONs under the gitignored `outputs/soiling/`. Branch off `main`, commit the code
with reasoning in the message (this repo's convention: say what the number supersedes and why),
and force-add the result JSONs plus
`outputs/aoi/santa-cruz-w2-21cm/array_install_era.csv` so the evidence behind the docs ships
with them. No Claude attribution in commits or PR bodies.

**2. Close the `GAMMA_PDC` question. This is the real blocker.**
The module temperature coefficient is hardcoded at -0.0045 in
`scripts/analyze/pvdaq_daily_srr_probe.py` and at -0.0035 in
`scripts/analyze/washable_share_probe.py`. Measured on system 10109 the choice moves the
annual soiling label by **1.045 pts** — larger than the 0.72 method-noise SD and comparable to
the entire 1.41 within-cell signal. Every recorded label used -0.0045 and none of them are
validated.

Fix it properly rather than documenting it again:
- `pvdaq/csv/system_metadata/<id>_system_metadata.json` carries module make and model for all
  1,381 residential systems. Parse it, and resolve a real coefficient per system (pvlib's CEC
  module database is already a dependency; fall back to a labelled default when a module is
  not found, and record the fallback rate).
- Re-run `scripts/analyze/pvdaq_method_noise.py` with gamma varying alongside the irradiance
  source, so the noise budget finally includes its largest known term.
- Re-run `scripts/analyze/pvdaq_within_cluster.py` and report the corrected signal-to-noise.
  **Expect it to fall below 1.95x.** If it drops under 1.0, Phase 3 as specified cannot resolve
  array-level effects and a null result would describe our own noise. Say so plainly if that
  is what the numbers show.

**3. Reconcile the outward-facing documents with what is now known.**
`docs/GRANT_NARRATIVE_DRAFT.md` §4 currently reads more positively than the evidence supports.
It was updated when Phase 0 passed but before the AOI came back at zero-clearing and before the
gamma problem surfaced. Cameron has a funding deadline, so this matters and it is his file:
propose edits, flag what you changed, and do not quietly rewrite his framing. The honest
positioning is that the *measurement* is the contribution (nobody has measured biological
soiling's power cost in a Mediterranean climate, and Santa Cruz is climatically
indistinguishable from Berkeley where biofilm on panels was confirmed), and that the cleaning
product is a clean, publishable negative.

Also unsent and awaiting Cameron: `docs/outreach/craig_followup_DRAFT.txt`. It contains a
correction Craig needs before he photographs anything (the earlier email told him the gap
between cells is 156 mm; 156 mm is the cell *width*, the gap is 2-3 mm, and the module frame at
35-40 mm is the reliable scale reference). Do not send it. Flag it.

**4. If time remains.** The next genuinely high-value free layer is module orientation
(portrait vs landscape) from the 6 cm imagery, because the substring physics swings 3x on it
(7.00% landscape vs 21.00% portrait-string for the same moss line). Everything downstream
carries that fork until it exists.

**How to work here.** Follow `.claude/rules/working-agreement.md`: act rather than hand back a
to-do list, proceed without asking on routine reversible work, and name real blockers
precisely. State provenance with every metric — what measured it, on which split, against how
many objects, and what it supersedes.

**One standing instruction, learned the hard way this session.** Every defect found so far, in
both new and old code, was **silent and optimistic**: nothing threw, nothing logged, and the
full test suite passed throughout. Two independent bugs came from `np.interp` clamping instead
of extrapolating; one came from a constant duplicated in two files drifting apart; one came
from a confident comment that was simply false. When a number looks good, test it before
reporting it. Sensitivity sweeps on hardcoded constants find more than code review does.

`docs/HANDOFF_20260827.md` has a "Traps" section covering the tooling gotchas (heredocs under
`conda run`, output buffering, background jobs dying at session end, PVGIS year limits,
Open-Meteo 429s). Read it before running anything long.
