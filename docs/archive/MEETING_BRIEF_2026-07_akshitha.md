> ⚠️ **ARCHIVED 2026-08-20.** Point-in-time onboarding brief for Akshitha (2026-07).
> Its Stage-1 line quotes `val SAHI F1 0.570 (GA gate 0.65, beta 0.55)` — that metric and
> that gate were both retired on 2026-08-07 and are not comparable to current numbers.
> Current status: [Q2_PLAN.md](../Q2_PLAN.md). Team lanes: [TEAM.md](../TEAM.md).

---

# Kickoff brief — Akshitha onboarding (2026-07-15)

A one-page orientation for the intro meeting. Full detail lives in the linked canonical docs; this
is the 5-minute "where things stand." Onboarding steps: [`ONBOARDING.md`](../ONBOARDING.md).
Ownership + process: [`TEAM.md`](../TEAM.md).

## What SolarSoiled is

Two-stage geospatial ML, county-agnostic, runs on any NAIP-covered AOI:
- **Stage 1 — detection:** find rooftop solar arrays in NAIP aerial imagery.
- **Stage 2 — soiling risk:** score each detected array's soiling risk from weather / air quality /
  land use / structural features.
- **Product loop:** detect → score → recommend cleaning → postcard → QR → homeowner dashboard.

## Where each phase stands (canonical: [`Q2_PLAN.md`](../Q2_PLAN.md))

| Phase | Status | Headline |
|---|---|---|
| 1 — Panel detection | in progress (`beta`) | R2 detector: val **SAHI F1 0.570** (GA gate 0.65, beta 0.55). Bottleneck is sparse **train** labels; val/test relabeled. |
| 2 — Soiling-risk model | **GA-ready** (`beta` until registry flip) | All three gates clear: spatial-CV AUC 0.728, pooled out-of-year AUC 0.710, calibration retained. **Model work is done.** |
| 3 — Product surface | in progress | CLI + FastAPI + job queue + registry shipped; deployed on Render. |
| 4 — Homeowner outreach | shipped (pilot) | Santa Cruz test-50 postcards mailed live; BBF dashboard + calculator live on Cloudflare Pages. |

## The two big open threads

1. **Stage-1 permissive-stack migration** — replace AGPL Ultralytics YOLOv11 with a permissive
   (Apache-2.0) stack (RF-DETR primary + SAM2 for area/m²), to escape the ~$5k/yr license triggered
   by our SaaS. Direction is **decided**; the open tactical question is whether RF-DETR reaches
   quality parity (the "fair rematch," decision gate W1). Full sequenced plan (W1–W6):
   [`PERMISSIVE_STACK_MIGRATION.md`](../PERMISSIVE_STACK_MIGRATION.md). **Proposed Akshitha lane.**
2. **Detector recall** — the real ceiling is a data/resolution problem (small-panel misses). Levers:
   targeted train relabeling (fed by the permit miss-review queue) and exploiting 30cm NAIP.

## Proposed lanes (discuss)

- **Cameron:** direction/product/business, Stage-1 training runs (Colab Pro), Stage-2 (frozen).
- **Akshitha:** the permissive-detector migration (RF-DETR + SAM + the port), the product/infra
  surface (API, dashboard, outreach A/B), and codifying reusable infra for faster ramp-up.

## First-week suggestions

- Run the Stage-2 verify command (reproduces the GA number in seconds) to confirm env + data handoff.
- Read the migration plan and the Stage-1 rules; sanity-check the RF-DETR W1 setup.
- Pick one infra papercut from onboarding and fix it — that *is* the "make ramp-up easier" mandate.
