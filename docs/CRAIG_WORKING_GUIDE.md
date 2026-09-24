# Working in this repo as the owner, not the engineer

> ⚠️ **Stage 2 numbers in this document are superseded (2026-09-22).** The validation folds behind
> them leaked: the 10 km spatial fold and leave-one-year-out each hold out one axis and not the
> other, leaving the held-out year's stations in training for **88.7%** of rows. The honest joint
> out-of-station-and-year AUC is **0.622** (95% CI [0.571, 0.670]), not **0.710**; **lat/lon alone
> score 0.644**; the regional figures are **0.655** pooled / **0.527** largest region, not
> 0.677/0.548. Calibration is unaffected. Corrected numbers and artifacts:
> [CANONICAL_NUMBERS.md](CANONICAL_NUMBERS.md); full argument: `paper/paper.tex` §4.4.
> This document is kept as the record of what was believed at the time.


Written 2026-09-09 for Craig. The companion doc,
[CODEX_QUICKSTART_MAC.md](CODEX_QUICKSTART_MAC.md), covers **asking** the repo questions in Read
Only mode. This one covers **changing** things: what you can safely change yourself with an agent,
what you should never change, and where the line is.

Read that doc first if you have not. Everything here assumes the setup in its sections 1 to 3 is
already working: a clone in `~/repos/`, the folder itself open in VS Code, and the agent oriented
by [AGENTS.md](../AGENTS.md).

---

## 1. The one-paragraph version

There are four kinds of change in this repo, and they are not equally risky. **Docs and text** you
can do today, alone, with an agent. **Config numbers** you can do with an agent if you open a PR
and Akshitha reviews it. **Code** you can propose but should not merge. **Model training, data,
and anything that spends money or sends mail** is not a thing to do from a chat window at all,
regardless of how confidently an agent offers. The rest of this doc is that sentence with the
details filled in.

## 2. Before you change anything: the two rules that prevent most damage

**Rule 1: never work on `main`. Always work on a branch.**

`main` is not protected. GitHub will not stop you, because branch protection needs a paid plan and
this org is on the free one ([TEAM.md](TEAM.md), Git conventions). That means the only thing
between a bad edit and the live repo is you remembering this rule. Tell the agent, at the start of
every session where you intend to change something:

> Create a branch off main before you make any edits. Never commit to main. Use the branch naming
> convention in docs/TEAM.md.

**Rule 2: an agent's confidence is not evidence.**

This repo has stale numbers scattered through older docs, and an agent will quote them without
hesitating. [CANONICAL_NUMBERS.md](CANONICAL_NUMBERS.md) is the tiebreaker: if any doc disagrees
with it, the doc is wrong. Ask for a file and line behind every figure. If the agent cannot
produce one, the figure does not go in anything external.

## 3. What you can do yourself, safely

These are reversible, reviewable, and do not touch the model or the data.

| Task | How to ask for it | Why it is safe |
|---|---|---|
| Fix or rewrite a doc | "Rewrite docs/X.md for a non-technical reader. Do not change any number." | Text only. Git keeps the old version. |
| Get a plain-English summary of a workstream | "Summarise docs/Q2_PLAN.md into a one-page status I can send the board." | Read only. |
| Update status in `Q2_PLAN.md` | "Mark the Stage 2 lane as GA in docs/Q2_PLAN.md, matching what TEAM.md says." | Docs. This is genuinely your lane: you own direction. |
| Write a new handoff or brief | "Draft docs/BRIEF_<date>.md covering X, citing sources." | New file, nothing overwritten. |
| Read what a script does before asking someone to run it | "Explain scripts/predict/holdout_ci.py in plain English, step by step." | Read only. |

The pattern that makes these safe: **you are changing prose, not behaviour.** Nothing here can make
a number come out different.

## 4. What you can do with a PR and a reviewer

These change what the software does. Do them on a branch, open a pull request, and have Akshitha
review before merge. She owns every engineering lane ([TEAM.md](TEAM.md)).

- Business assumptions in `src/risk/rates.py` and the economics constants. If your view on the
  price of a cleaning or the value of a kWh changes, that is your call to make, but the change
  needs a second pair of eyes on how it propagates.
- Thresholds and settings in `configs/`. These are YAML, they read like settings files, and an
  agent can edit them accurately. What an agent cannot tell you is whether the resulting number is
  still comparable to the one it replaced.
- Registry flips in `models/registry.yaml`, `beta` to GA. TEAM.md assigns these to you explicitly.
  Ask the agent to show you the gate result first.

Ask for it like this:

> Make this change on a branch, open a PR with a description explaining why, and tag Akshitha for
> review. Do not merge it.

## 5. What you should not do from a chat window

Not "ask permission first." Just do not.

- **`scripts/outreach/mail_via_lob.py --send`.** Sends real, paid postcards to real addresses. A
  batch has already gone out; a re-send mails those people twice. `--dry-run` is the only safe form.
- **Training runs.** They need a GPU, hours of compute, and the 1.5 GB data bundle. An agent that
  offers to start one on your laptop is wrong about what your laptop can do.
- **Anything that deletes or overwrites data.** Specifically `data/interim/tile_index.json`, the
  label sets, and `runs/`. These are not reproducible. If an agent proposes deleting a file under
  those paths, stop and ask Akshitha.
- **`git push --force`, or any rewrite of history.**
- **Committing secrets, `.env`, model weights, or any address data.** The `.gitignore` covers these,
  but if `git status` ever shows one, stop.

Give the agent this list at the start of a session where it has write access. It will honour it.

## 6. The failure mode that cost you an hour last time

Last session was spent on Codex not being able to see or run anything. That is a solved problem and
the fix is a table, not a debugging session:
[CODEX_QUICKSTART_MAC.md](CODEX_QUICKSTART_MAC.md), Troubleshooting.

The three that account for most of it:

1. **Try closing and reopening VS Code first**, opening the repo folder before starting the agent
   session. A stale session root produces path errors that look like everything else.
2. **The clone must not be in Documents, Desktop, Downloads, iCloud, Google Drive, or Dropbox.**
   Run `pwd` in the terminal. If the path has `Mobile Documents`, `CloudStorage`, or a space in it,
   that alone is the whole problem. Move it to `~/repos/`.
3. **Read the errno before touching a sandbox setting.** `Operation not permitted` is a sandbox
   problem and sandbox settings help. `No such file or directory` is not, and they do nothing.

And the meta-rule: **the integrated terminal is not sandboxed.** If `pwd && head -5 AGENTS.md`
works there but the agent says it cannot, the machine is fine and the fault is inside the agent.
That single test splits the problem in half in ten seconds.

## 7. How to tell whether an agent has actually done what it said

Non-technical does not mean unable to verify. Three checks, in order of effort:

1. **`git status` and `git diff`.** Ask the agent to show you the diff and explain each change in
   one sentence. If the diff touches files it did not mention, that is the signal.
2. **`make test-fast`.** If it was green before a change and red after, the change broke
   something. This is the single most useful button you have, with one caveat you should read in
   section 10: on a clone without the data bundle, 18 of the number guards skip rather than run,
   and a skip looks identical to a pass. The `557 passed, 3 skipped, 2 deselected` figure in
   CLAUDE.md is the count *with* the data present; expect more skips than that without it.
3. **Reproduce a number.** `PYTHONPATH=. conda run -n solar-soiling python scripts/predict/holdout_ci.py`
   prints pooled out-of-year AUC 0.7095 in about a minute. If that still matches, the Stage 2 model
   is intact.

## 8. What to hand to Akshitha instead of doing yourself

Anything that starts with "why is this number what it is", "can we make the model better", or
"can we run this on a new county". Those are lanes she owns, and the honest answer to the third one
is currently no: the detector is gated on 21cm Santa Cruz County imagery only, and the soiling model
does not generalise to an unseen region (out-of-region AUC 0.677 against about 0.73 for a random
split). Both limits are structural, not bugs waiting to be fixed by a prompt.

## 9. The money pipeline, specifically

You own the business assumptions, so this is the chain you are actually responsible for. Every link
is a real constant in a real file, and the values below were read out of the code on 2026-09-09.

| # | Step | Where | Value today | Provenance |
|---|---|---|---|---|
| 1 | Detected polygon area to kW | `economics.py` `PACKING_FACTOR` | **0.84**, so 5.41 m2/kW | MEASURED, n=130 sites joined to the Santa Cruz permit registry by APN. Envelope p50 5.44 m2/kW |
| 2 | kW to annual kWh | `economics.py` `BASE_SUN` 5.5, `SYSTEM_DERATE` 0.84 | | NREL benchmark derate |
| 3 | Soiling loss percent | `loss_model.py`, floor `BASE_SOILING_PCT` | **2.80%** | Coastal-CA p50 from the NREL file, n=332 |
| 4 | Lost kWh to dollars | `rates.py` | retail offset **$0.4573**, NBT export **$0.0392**, AOI blended **~$0.428** | Bill-reconciled tariff specs; export rate is the SDG&E ACC table |
| 5 | How much a clean recovers | `economics.py` `DEFAULT_RECOVERY_PRO` | **0.045** | MEASURED via `recovery.py`; the same model reproduces NREL's coastal-CA 4.70% annual loss |
| 6 | Cost of the clean | `MIN_PRO_SERVICE` 150, `MIN_RINSE_SERVICE` 90 | | TEAM-SET price points, i.e. yours |
| 7 | Net, with uncertainty | `array_recommendation_mc()` | | Monte Carlo over loss PI, area sigma 0.26, rate band |

**The conclusion that chain produces: zero of 1,865 sites in the AOI show a positive expected net,
at any rate up to $0.70/kWh, at any system size, over 2,000 draws each.** That is
[ECONOMICS_GROUNDING_20260809.md](ECONOMICS_GROUNDING_20260809.md), and it is kill-risk C1
confirmed rather than a bug someone can fix.

**Why that is factually accurate, and the one qualifier that must travel with it.** It is on
*recoverable* soiling only. NREL defines its soiling ratio assuming perfect cleaning, so a
permanent wash-only layer is excluded from the labels, from the physics, and from the 0.045 by
construction. Nothing in this repo measures the permanent channel. Quote it as "zero of 1,865 on
recoverable soiling", never as "zero of 1,865". The probe is
`scripts/analyze/persistent_soiling_probe.py`.

**Still marked UNSOURCED in the code, so treat as opinion not fact:** `MIN_PRO_SERVICE` (which
decides most residential cases single-handedly, so three real quotes would change more than any
modelling), NBT sigma 0.30, and the ACC export table being SDG&E's standing in for PG&E's.

**The mailing side.** `scripts/outreach/mail_via_lob.py` is dry-run by default and prints a cost
estimate at **~$1.50 a postcard**. `--send` charges the card and mails real people. A batch has
already gone out. There is no undo.

## 10. Why "a numeric mistake is silent" is a measured claim

This is the load-bearing sentence in section 11, so here is the evidence rather than the assertion.
Every one of these was a constant that was wrong, in production, for a while, in the direction that
flattered the product:

| Constant | Was | Is | Error |
|---|---|---|---|
| `recovery_frac` | 0.90 | 0.045 | **20x**. This one alone was carrying the product; with 0.90 restored, 44.8 to 95.4% of the AOI looks worth cleaning |
| `RISK_TO_LOSS_PCT` | 8.0 | removed | Multiplied a calibrated probability by 8 to get a loss percent, which is not a thing you can do |
| `BASE_SOILING_PCT` | 4.70 | 2.80 | A Central Valley p50 wearing a coastal label |
| tariff assumption | all NBT | 90.1% legacy | **2.60x** undervaluation of a lost kWh, caught against n=7,536 real interconnections |
| `NBT_START_DATE` | 2023-04-14 | 2023-04-15 | One day, tested with `>=`, so **2.78x** wrong for applications landing exactly on the sunset date, which is where the filing rush piles up |
| per-panel rate ladder | floored $5.00 | declining | Quoted a 1.4 MW roof $16,390 for one clean, **3.3x** the published market price |
| cost unit | per polygon | per site | Overcharged a 4-way-split roof **2.3x**, $600 against $264 |
| `PACKING_FACTOR` | 0.90 guess | 0.84 measured | Small, but note the intermediate "fix" (5.05 m2/kW) was *further* from the truth than the original it replaced |

Two things to take from the table. **The distribution is not symmetric:** these errors ran 2x to
20x, and they ran in the optimistic direction, because an assumption nobody has measured tends to
be the one that made the model work. And **none of them were caught by review or by tests.** Every
single one was caught by measuring against an external source: the permit registry, the
interconnection dataset, the CPUC decision, the NREL file, a published price list.

**What guards them now, precisely.** The ones above are pinned individually in `tests/` by
regression tests that name the correction they guard, so an agent that edits `BASE_SOILING_PCT` or
`DEFAULT_RECOVERY_PRO` turns `make test-fast` **red**. That is a real safety net and you should
lean on it.

**Where the net has a hole, and this is the part worth knowing.**
`tests/test_canonical_numbers.py` is the guard that checks every headline number against the
artifact that produced it, in both directions. Each of its cases skips when the artifact is absent,
which is deliberate so a fresh clone stays green. Measured on a clean checkout on 2026-09-09:

```
with the hand-off data:      38 passed
on a clone without data:     20 passed, 18 skipped
```

CI runs on a runner with no data, so **all 18 of those number guards are inert in CI**, and they
are inert on your laptop too unless you pull the 1.5 GB bundle. Until 2026-08-31 they ran on
Cameron's machine. They now run nowhere by default.

So the accurate version of the rule is narrower and more useful than "tests will not catch it":

- A constant someone has **already been burned by** is pinned. Editing it goes red.
- A **new** constant, a number that lives in a doc, or anything checked only by those 18 cases is
  not. Editing it goes green, in CI and on your machine.

That is the mechanism. Not agent incompetence, and not yours: a guard that skips silently looks
exactly like a guard that passed.

## 11. Realistic expectations

You will be good at: reading the project through an agent, keeping the docs true, drafting briefs,
making the business-assumption calls, and catching when a doc and reality have drifted apart. That
is real value and it is not a consolation prize. Docs drift is this repo's most persistent failure
mode and it is the exact thing an owner with an agent is well positioned to fix.

You will be bad at: knowing when an agent has quietly done something wrong with a number. Not
because of the tooling, and not because of you. An agent that edits the correct file with the
wrong constant produces a clean diff and, for anything not already pinned, a green test run.
Section 10 is the evidence that this is the repo's actual historical failure mode rather than a
hypothetical: eight constants, wrong by 2x to 20x, none caught by review or tests, all caught by
someone measuring against an outside source. This is why section 4 has a reviewer in it and
section 3 does not.

The line between the two is whether a mistake announces itself. Prose mistakes do: a wrong sentence
reads wrong. Numeric ones do not, and this repo has an eight-row table proving it.

**One live example, as of today.** CLAUDE.md still states `PACKING_FACTOR` 0.90; the code says
0.84, measured against 130 permits. Neither file is lying and no test is red. The doc simply did
not move when the measurement did, which is the whole failure mode in one line, and it is the kind
of thing you are genuinely well placed to catch.
