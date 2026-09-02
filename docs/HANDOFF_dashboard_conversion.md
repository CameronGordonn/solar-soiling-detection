# Handoff — Homeowner dashboard conversion (product surface)

**Lane:** Akshitha (product surface — API, dashboard, outreach funnel, A/B). **Written:** 2026-07-13.
Pick-up-cold doc for the dashboard-conversion workstream. Canonical status: [`Q2_PLAN.md`](Q2_PLAN.md).

## Why this exists

The dashboard is the last step of the funnel: postcard → QR scan → **dashboard** → homeowner
requests a cleaning or calls. The 50 Santa Cruz test postcards (`mailers_v13`, mailed 2026-06-30) all
point here via a per-array QR (`dashboard.html?id=<array_id>`). The audience skews older and
non-technical, so the dashboard must get them to a **call / lead** with minimal friction. A
2026-07-13 review found the high-intent path was buried and the contact identity was inconsistent
with the mailers.

## Repo / files

Lives in **`BBF-Website`** (separate repo), not solar-soiling-ml:
- `public/tools/dashboard.html` — structure
- `public/tools/dashboard.js` — behavior (array select, `?id=` deep-link, lead capture)
- `public/tools/dashboard.css` — styling
- Mailer side (this repo): `scripts/outreach/generate_mailers.py` — the QR + printed contact details
  (`PHONE = "(831) 216-8749"`, `EMAIL = "betterbehaviorfoundation@gmail.com"`).

## What changed on 2026-07-13 (already done, in branch `dashboard-conversion-fixes`)

1. **Unified contact identity (bug fix).** Dashboard lead-capture emails went to
   `solarsoil.app@gmail.com`; the printed postcards say `betterbehaviorfoundation@gmail.com` /
   (831) 216-8749. All three `mailto:` in `dashboard.js` now use the BBF inbox so the dashboard
   matches what recipients were told.
2. **Phone-first CTA stack in the hero** (`#impact-actions` in `dashboard.html`). Order by intent:
   `📞 Call us — (831) 216-8749` (`tel:`, high-contrast white pill, loudest) → **Get this roof
   cleaned →** (`#btn-lead`, solid green) → optional form (hidden) → the calculator demoted to a
   quiet underline link (`.cta-secondary`). Previously the calculator was the loudest button and the
   lead button was hidden inside a collapsed panel — the funnel was inverted.
3. **Lead button promoted out of the collapsed `#recalc-section`.** `#btn-lead` now lives top-level in
   the hero; the refine panel only refines the recommendation now. (Same click handler — it reads the
   on-screen numbers at click time and preserves `?id=`.)
4. **QR `?id=` deep-link verified.** Loading `dashboard.html?id=0` populates the report and focuses
   the array on the map directly — no manual map hunt. `generate_mailers.py` already emits these URLs.

## Next steps (your lane)

1. **Hosted form — LIVE.** `CLEANING_FORM_URL` in `dashboard.js` is set to
   `https://forms.gle/Dav9cQ7p9B8w92Y77` and the "Request a cleaning — 2-min form →" CTA
   (`#lead-form-link`) now renders (verified). `.cta-form[hidden]` keeps it hidden if the constant is
   ever blanked. **Why a form at all:** `mailto:` silently dead-ends on desktops with no mail client
   configured — a real fraction of this audience.
   - **Per-roof prefill (nice-to-have, not done):** in the Google Form → ⋮ → **Get pre-filled link**,
     fill a dummy Array ID, submit, and copy the resulting URL — it contains `entry.<number>=`. Then
     have the dashboard append `&entry.<number>=<arrayId>` (the selected id is already in scope where
     `#lead-form-link` is wired) so the form opens with the roof's ID pre-filled and leads tie back to
     the mapped array automatically.
2. **Instrument conversion.** We currently can't measure scan → lead. Add lightweight,
   privacy-clean event counting (respecting the PII/secret gate — no addresses/APNs client-side) for:
   QR land, CTA impressions, call-tap, lead-submit. This is the metric that tells us if any of this
   worked.
3. **A/B the CTA order.** Phone-first vs form-first vs "get cleaned"-first. Keep variants simple; the
   sample is small (50 cards) so treat results as directional, not significant.
4. **Permit-homes layer is intentionally NOT loaded** (see the comment in `dashboard.html` near the
   `mailer_homes.js`/`permit_homes.js` scripts). Re-add it only once `permit_homes.js` carries real
   scores from a full geocode→score run — it's gated on the Open-Meteo quota
   ([`project_openmeteo_quota`]).

## Guardrails

- **PII/secret gate** (`SYNC.md` in the public repo) applies to everything world-readable here — the
  dashboard ships client-side JS. Identify arrays by **ID only**; no addresses, owner names, APNs, or
  keys. An address file leaked once and had to be history-scrubbed.
- **Deploys are Cameron's call** (Cloudflare Pages) — open a PR; don't self-deploy.
- **Never re-run a sent Lob batch.** `mailers_v13` already went out 50/50.
