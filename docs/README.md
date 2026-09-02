# docs/ — index

What each doc is for, and which one is *canonical* for a given question. Start at the repo
[CLAUDE.md](../CLAUDE.md) for commands and paths. (`SESSION_STARTUP.md` at the repo root is an
*optional personal* scratchpad — gitignored, not in a fresh clone, and not a team source of
truth. See [TEAM.md](TEAM.md).)

**One rule for this directory:** when a number moves, fix it where it is canonical and mark the
superseded copy in place rather than deleting it. This repo has repeatedly been bitten by stale
metrics whose provenance nobody recorded — `map50_test=0.263`, the "0.396" SAHI F1, the
"~0.65 mAP50" that was never a current-labels figure.

## Status & direction (read these first)
| Doc | Canonical for |
|---|---|
| [CANONICAL_NUMBERS.md](CANONICAL_NUMBERS.md) | **Every headline number, its artifact, and its reproduce command.** Built 2026-08-31 from the artifacts on disk. **When a doc disagrees with this file, the doc is stale.** Start here before quoting any metric. |
| [Q2_PLAN.md](Q2_PLAN.md) | **Live project status** — phase tables, gates, current metrics. Single source of truth for "where are we." |
| [CRAIG_BRIEF_2026-08-19.md](CRAIG_BRIEF_2026-08-19.md) | **The current state of the business case in one document**, written to be read cold. The economics verdict, why it does not clear, what would change it. Start here if you want the short version. |
| [PRODUCT_VISION.md](PRODUCT_VISION.md) | North-star: product thesis, API contract sketch, monetization, parallel tracks. |
| [COMMERCIALIZATION.md](COMMERCIALIZATION.md) | Licensing constraints to clear before charging. The Ultralytics AGPL problem is **resolved**; what is still AGPL is listed here. |
| [TEAM.md](TEAM.md) | Lanes, ownership, conventions, what is and is not a team source of truth. |
| [CODEX_QUICKSTART_MAC.md](CODEX_QUICKSTART_MAC.md) | **Reading this repo with a coding agent** (macOS, VS Code, Codex). Why "no sandbox access" happens on a first session and why Read Only is the right mode. For understanding the project, not changing it. |
| [ONBOARDING.md](ONBOARDING.md) | **Start here if you are new.** §1 is a runnable setup path, validated in a throwaway clone 2026-08-26; three verification tiers and three lane quickstarts, each ending in a number you should see. |
| [../DATA.md](../DATA.md) | Every artifact that is not in git: R2 custody, the `rclone` pull, what is regenerable and with which command. |
| [COLD_START_AUDIT_20260823.md](COLD_START_AUDIT_20260823.md) | **Part B is the trap list** — the failure modes that produce plausible wrong numbers instead of errors. Part A is a fixed cold-start defect list, kept for the record. |
| [ONBOARDING_OVERHAUL_PLAN.md](ONBOARDING_OVERHAUL_PLAN.md) | What was wrong with onboarding, what was fixed, and the four steps only Cameron can do (R2 bucket, upload, restore test, credentials). |
| [MEETING_BRIEF_2026-06.md](MEETING_BRIEF_2026-06.md) | The 2026-06 wedge decision (residential funnel → consumer→cleaner marketplace, BBF integration). |

## Economics — the dollar chain
**This is the workstream that decides whether the product exists.** Read in this order.

| Doc | Canonical for |
|---|---|
| [ECONOMICS_GROUNDING_20260809.md](ECONOMICS_GROUNDING_20260809.md) | **The audit.** Every constant in the net-$ chain, sourced or explicitly marked UNSOURCED. Where `recovery_frac = 0.90` (measured 0.045) and `RISK_TO_LOSS_PCT = 8.0` came from and why they were wrong. |
| [AOI_CLEANING_TARGETING_PLAN.md](AOI_CLEANING_TARGETING_PLAN.md) | **The moss/lichen thesis and the ground-truth plan.** Substring shade physics, breakeven bars, the ranked experiment list, and the written stop rule that would kill the thesis. |
| [SOILING_LEVEL_INVESTIGATION.md](SOILING_LEVEL_INVESTIGATION.md) | Why the risk model cannot rank homes *within* an AOI — station-level labels, 58.1% of importance on features that are constant across the AOI. Structural, not a plumbing gap. |
| [HANDOFF_roof_geometry_for_paper.md](HANDOFF_roof_geometry_for_paper.md) | Roof tilt/azimuth from 3DEP lidar: method, validation, and the deliberate negative result on tilt-as-a-feature. Written for the paper. |

## Stage 1 — detection
| Doc | Canonical for |
|---|---|
| [PERMISSIVE_STACK_MIGRATION.md](PERMISSIVE_STACK_MIGRATION.md) | **The shipping detector.** RF-DETR @728 + SAM2 (Apache-2.0), W1–W6. Gate passed 2026-08-07 at tile-level box-F1 **0.8260**. |
| [PHASE1_HANDOFF.md](PHASE1_HANDOFF.md) | Detection runbook: RCA, threshold sweeps, relabel loop. |
| [LABELING_SPRINT_21CM.md](LABELING_SPRINT_21CM.md) | The 21cm relabel of all 249 tiles (**complete**) — convention, resolution-integrity rules, and the reason older labels are not comparable. |
| [HANDOFF_imagery_ingestion.md](HANDOFF_imagery_ingestion.md) | SCC 21cm imagery ingestion seam + dual-GSD guardrail. |
| [NAIP_ROBOFLOW_WORKFLOW.md](NAIP_ROBOFLOW_WORKFLOW.md) | NAIP tiling → Roboflow annotation pipeline reference. |
| [ROBOFLOW_IMPORT_RUNBOOK.md](ROBOFLOW_IMPORT_RUNBOOK.md) | Importing/exporting labels through Roboflow. |
| [HYPERPARAM_PLAYBOOK.md](HYPERPARAM_PLAYBOOK.md) | Training hyperparameter invariants + tuning decision table. |
| [RTX3060_SETUP_GUIDE.md](RTX3060_SETUP_GUIDE.md) | Local GPU training environment. |

## Stage 2 — soiling risk
| Doc | Canonical for |
|---|---|
| [SOILING_STAGE2_GUIDE.md](SOILING_STAGE2_GUIDE.md) | XGBoost risk model + physics scorers (SOMOSclean/Kimber): training, features, validation. Gates cleared 2026-07-05. |
| [PVDAQ_LANE_HANDOFF_20260831.md](PVDAQ_LANE_HANDOFF_20260831.md) | **Read before touching the soiling model.** The regional holdout: it scores **0.5313 on an unseen region** against 0.7569 for a random holdout of the same size, so "works in any region" is not supportable. Plus where the fleet extraction stopped and how to resume it. |
| [GAMMA_RESOLUTION_20260827.md](GAMMA_RESOLUTION_20260827.md) | How a hardcoded module temperature coefficient moved a label more than the signal, what resolving it per system did to the noise budget (S/N 1.95x → 1.76x), and the two estimator bugs found on the way. |

See also [SOILING_LEVEL_INVESTIGATION.md](SOILING_LEVEL_INVESTIGATION.md) above for the limit
on what this model can be asked to do. Model quality is **done** — further AUC chasing is below
measurement resolution.

## Product surface — outreach & permits
| Doc | Canonical for |
|---|---|
| [MAILER_PIPELINE.md](MAILER_PIPELINE.md) | Postcard pipeline (Lob). Test-50 sent live 2026-06-30 (`mailers_v13`). |
| [PERMIT_DETECTION_ENRICHMENT.md](PERMIT_DETECTION_ENRICHMENT.md) | Permit-driven detection enrichment (recall audit → targeted labeling). |
| [PERMIT_HOMES_DASHBOARD_HANDOFF.md](PERMIT_HOMES_DASHBOARD_HANDOFF.md) | Executable spec for the permit-homes dashboard layer (geocode → score → redacted publish). |
| [HANDOFF_dashboard_conversion.md](HANDOFF_dashboard_conversion.md) | Homeowner dashboard conversion (phone-first CTA, lead form, A/B) — BBF-Website surface. |

### outreach/ — things being sent to real people and agencies
| Doc | Canonical for |
|---|---|
| [outreach/CPRA_CITY_OF_SANTA_CRUZ_SOLAR_PERMITS.md](outreach/CPRA_CITY_OF_SANTA_CRUZ_SOLAR_PERMITS.md) | City records request for solar permit vintage — routing, what the public eTRAKiT portal does and does not give, and the send-ready draft. **Not yet sent.** |
| [outreach/CLEANING_QUOTE_CALL_SHEET.md](outreach/CLEANING_QUOTE_CALL_SHEET.md) | Three real cleaning companies, the six questions that source `MIN_PRO_SERVICE`, and the bound on what the answers can change. **Not yet called.** |

## Writing
| Doc | Canonical for |
|---|---|
| [PAPER_METHODS_DRAFT.md](PAPER_METHODS_DRAFT.md) | Methods section draft for the paper. |
| [HANDOFF_roof_geometry_for_paper.md](HANDOFF_roof_geometry_for_paper.md) | Roof-geometry results written up for Josh, with the lead ordering and one flagged provenance gap. |

The **dashboard + calculator live in the BBF site** (`../BBF-Website/public/tools/`), served at
`betterbehaviorfoundation.com/tools/*`. The retired `solarsoiled-landing/` GitHub Pages surface
has been removed and all `*.github.io` URLs are dead.

## archive/
Point-in-time notes kept for history, not maintained. **Every file in here carries a banner
saying what in it is no longer true** — several quote the retired SAHI-F1 gate, which is not
comparable to any current number. Current status always lives in [Q2_PLAN.md](Q2_PLAN.md).
