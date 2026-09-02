# docs/local — personal, never committed

Everything in this directory except this README is gitignored. It stays on the author's
machine.

**What belongs here:** email drafts, funding and grant correspondence, notes about people,
anything with a home address or phone number, and working material that is yours rather than
the project's.

**What does NOT belong here:** anything a teammate needs from a fresh clone. Runbooks,
specs, measured results and their provenance are project content and belong in `docs/`.
The test is simple: if someone cloning the repo tomorrow would be blocked without it, it is
not local.

Two supporting rules are in `.gitignore` alongside this directory, so a draft saved to the
wrong folder is still caught:

    docs/**/*_DRAFT.txt      any draft, anywhere under docs/
    docs/**/*_email_*.txt    any email, anywhere under docs/

`docs/outreach/cpra_city_email_READY.txt` is explicitly re-included because it was committed
before this convention existed and teammates may be relying on it. If it should be private
too, `git rm --cached` it in its own commit so the removal is visible in history.
