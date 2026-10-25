# docs/

Method write-ups, audits and working notes for the project. Most were written while the work was
happening, so older ones can be out of date. When a number here disagrees with
[CANONICAL_NUMBERS.md](CANONICAL_NUMBERS.md) or the papers in [`paper/`](../paper/), the doc is
stale.

**Rule for this directory:** when a number moves, fix it where it is canonical and mark the
superseded copy in place rather than deleting it.

## Start here

| Doc | What it covers |
|---|---|
| [CANONICAL_NUMBERS.md](CANONICAL_NUMBERS.md) | Every headline number, the artifact it comes from, and the command that reproduces it. |
| [BUSINESS_BRIEF.md](BUSINESS_BRIEF.md) | The business case in one document: the economics verdict, why it does not clear, and what would change it. |
| [PIPELINE_AUDIT_AND_PLAIN_REPORT_20260827.md](PIPELINE_AUDIT_AND_PLAIN_REPORT_20260827.md) | The whole project in plain English, plus the five errors an audit of my own work found and how they moved the conclusion. |
| [DATA.md](DATA.md) | Every artifact that is not in git, what it is, and what can be regenerated. |
| [MIRROR.md](MIRROR.md) | What this public snapshot leaves out and why. |

## Economics

| Doc | What it covers |
|---|---|
| [ECONOMICS_GROUNDING_20260809.md](ECONOMICS_GROUNDING_20260809.md) | The audit of every constant in the dollar calculation, each sourced or marked `UNSOURCED`. |
| [RECOVERY_RECONCILIATION_PLAN.md](RECOVERY_RECONCILIATION_PLAN.md) | How two recovery-fraction values ten times apart ended up in shipping code, and how they were reconciled on 0.045. |
| [AOI_CLEANING_TARGETING_PLAN.md](AOI_CLEANING_TARGETING_PLAN.md) | The moss and lichen thesis, substring shade physics, breakeven bars, the ranked experiment list and the stop rule. |
| [PERSISTENT_SOILING_SCREEN.md](PERSISTENT_SOILING_SCREEN.md) | The lidar inspection screen: 70% low tilt, 30% nearby canopy, top 10% of arrays flagged for inspection. |
| [TILT_SOILING_EVIDENCE.md](TILT_SOILING_EVIDENCE.md) | Evidence review behind the racking-angle term in that screen. |

## Stage 1: detection

| Doc | What it covers |
|---|---|
| [PERMISSIVE_STACK_MIGRATION.md](PERMISSIVE_STACK_MIGRATION.md) | The shipping detector: RF-DETR and SAM2 (Apache-2.0). Gate passed 2026-08-07 at tile-level box F1 0.8260. |
| [PERMIT_DETECTION_ENRICHMENT.md](PERMIT_DETECTION_ENRICHMENT.md) | Using building permits as an independent recall check, then as a source of targeted labels. |
| [NAIP_ROBOFLOW_WORKFLOW.md](NAIP_ROBOFLOW_WORKFLOW.md) | NAIP tiling and the Roboflow annotation pipeline. |
| [HYPERPARAM_PLAYBOOK.md](HYPERPARAM_PLAYBOOK.md) | Training hyperparameter invariants and a tuning decision table. |
| [RTX3060_SETUP_GUIDE.md](RTX3060_SETUP_GUIDE.md) | Local GPU training environment. |

## Stage 2: soiling risk

| Doc | What it covers |
|---|---|
| [SOILING_STAGE2_GUIDE.md](SOILING_STAGE2_GUIDE.md) | The XGBoost risk model and the physics scorers (SOMOSclean, Kimber): training, features, validation. Its gate figures were measured on leaking folds; see CANONICAL_NUMBERS.md. |
| [SOILING_LEVEL_INVESTIGATION.md](SOILING_LEVEL_INVESTIGATION.md) | Why the model cannot rank homes within an area: station-level labels, with 58.1% of importance on features that are constant across the area. |
| [PVDAQ_LABEL_PIPELINE_SPEC.md](PVDAQ_LABEL_PIPELINE_SPEC.md) | Building per-array soiling labels from NREL PVDAQ telemetry. |
| [PVDAQ_PRODUCT_RELEVANCE_20260826.md](PVDAQ_PRODUCT_RELEVANCE_20260826.md) | Two measured negatives on what better per-roof labels can and cannot do for the product. |
| [GAMMA_RESOLUTION_20260827.md](GAMMA_RESOLUTION_20260827.md) | How a hardcoded temperature coefficient moved a label more than the signal did, and what fixing it per system changed. |

## Product, licensing and code quality

| Doc | What it covers |
|---|---|
| [PRODUCT_VISION.md](PRODUCT_VISION.md) | Product thesis, API contract sketch and the CLI/API build-out. |
| [COMMERCIALIZATION.md](COMMERCIALIZATION.md) | Licensing constraints to clear before charging. The AGPL detector dependency is resolved. |
| [MAILER_PIPELINE.md](MAILER_PIPELINE.md) | The postcard pipeline. 50 cards were mailed on 2026-06-30 as a deliverability test. |
| [BUG_AUDIT_20260827.md](BUG_AUDIT_20260827.md) | A bug audit across 158 Python files after five bugs turned up in one session. |

The homeowner dashboard and breakeven calculator are hosted on the Better Behavior Foundation site
at <https://betterbehaviorfoundation.com/tools/dashboard.html>. Their source lives in a separate
private repository.
