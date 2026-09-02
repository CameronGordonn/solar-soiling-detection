# Team — ownership & working conventions

How we work now that this is a multi-person project. New here? Start with
[`docs/ONBOARDING.md`](ONBOARDING.md).

## Who owns what

| Area | Owner | Notes |
|---|---|---|
| Overall direction, product, business | **Cameron** | Roadmap, partner/outreach decisions, registry `beta→GA` flips. |
| Stage-1 detector training runs | **Cameron** | Runs on Colab Pro. R0 retrain + relabel loop. |
| Stage-1 permissive-stack migration (RF-DETR + SAM + the port) | **Akshitha** | The AGPL-escape workstream — CV research + infra. Runbook: [`PERMISSIVE_STACK_MIGRATION.md`](PERMISSIVE_STACK_MIGRATION.md). |
| Product surface — API, dashboard, outreach funnel, A/B | **Akshitha** | FastAPI backend, BBF dashboard, conversion experiments. |
| Reusable infra / faster ramp-up | **Akshitha** | Codifying what we've learned into tooling future projects reuse. |
| Stage-2 soiling-risk model | **Cameron** | GA-ready; largely frozen. Product-side work only from here. |

This is a starting split from the 2026-07 kickoff — adjust as we go. The intent is
**non-overlapping lanes**: Cameron drives the detector training + product/business, Akshitha owns
the permissive-detector migration and the product/infra surface.

## Working state — where "what's going on right now" lives

Historically the live working state was a **personal, gitignored** `SESSION_STARTUP.md` (Cameron's
scratchpad + AI session context). That doesn't transfer to a teammate. For shared state:

- **Canonical status** → [`Q2_PLAN.md`](Q2_PLAN.md) (phase tables, gates, metrics). Keep it current.
- **Per-workstream handoffs** → a dated doc in `docs/` written to be picked up cold (the migration
  plan is the model). Update it as the work moves; prune completed lines.
- **`SESSION_STARTUP.md` stays personal** — anyone may keep their own gitignored one, but it is not a
  source of truth for the team.

## Git conventions

- **`main` is NOT protected, and this line used to claim the opposite.** Branch protection and
  rulesets are unavailable on private repos under the org's free plan (GitHub returns `403 Upgrade
  to GitHub Pro or make this repository public`, confirmed 2026-08-17; Cameron decided to stay on
  the free plan and accept it). So "PR + ≥1 approval, no direct push" is a **convention we hold
  ourselves to**, not something GitHub enforces — nothing stops a direct push or a force-push.
  Review routing is automatic via [`.github/CODEOWNERS`](../.github/CODEOWNERS), which mirrors the
  lane split above, but CODEOWNERS without protection only *requests* review; it cannot block a
  merge.
- **One workstream per branch / PR.** Don't mix (e.g.) the detector migration with a docs truth-up or
  the permit sweep. Small, reviewable PRs.
- **Branch naming:** `<area>-<short-desc>` (e.g. `stage1-rfdetr-rematch`, `stage2-ga-finalize`,
  `product-dashboard-ab`).
- **Commit messages:** imperative subject scoped by area (`soiling:`, `detect:`, `docs:`,
  `analyze:`, `product:`), body explaining *why*. No AI-attribution trailers.
- **Never commit** secrets, PII, `*.pt`/`*.pth` weights, `.env`, caches, or data — the `.gitignore`
  covers these; if `git status` shows one, stop. One exception exists and it is a mistake, not a
  precedent: `models/yolov8s_solar_array_v1.pt` (23 MB, AGPL lineage) was committed **before** the
  ignore rule existed, and `.gitignore` has no effect on already-tracked files. It was removed from
  `HEAD` on 2026-08-26 and remains in history.

### Working in parallel without stepping on each other

The lanes above are the real anti-collision mechanism — we mostly edit different files. To keep it
that way in git:

- **Rebase before you start:** `git pull --rebase origin main` at the top of each work session, and
  again before you open the PR. Keep branches short-lived so they don't drift.
- **Never force-push a shared branch**, and never push to `main` (PR only). Force-push is fine only on
  your own un-reviewed feature branch.
- **The one file we both edit is [`docs/Q2_PLAN.md`](Q2_PLAN.md)** (canonical status). Keep those edits
  small and land them fast rather than sitting on a big status rewrite; CODEOWNERS routes its review to
  Cameron so status changes are seen. If you hit a conflict there, it's almost always a clean
  hand-merge — take both sides.
- If two PRs will touch the same file, say so in the PR description so the second one rebases.

## Deploys & external actions (ask first)

- **Pushing to `main`** and **production deploys** (Render API, Cloudflare/BBF site) are Cameron's
  call until we agree otherwise.
- **Live Lob sends** and anything that spends money or mails a real person: Cameron approves.
- The **PII/secret gate** (public repo `SYNC.md`) applies to anything world-readable — especially the
  BBF client-side JS.
