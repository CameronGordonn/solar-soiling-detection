"""Correct the loss model's LEVEL against soiling measured on nearby roofs.

THE PROBLEM THIS FIXES, which stood open and flagged from 2026-08-12 to 2026-08-30.
``econ_summary.json`` has carried this in ``known_limitations`` the whole time:

    LEVEL IS LIKELY BIASED HIGH AND THE CAUSE IS UNKNOWN. The AOI predicts ~5.53%
    annual soiling loss. The two nearest NREL stations read 4.00% (38 km) and
    3.10% (62 km) ... Treat the level as an upper bound until this is understood.

Two candidate causes were tested in August and both failed: back-filling the absent
``worldcover_*`` features moved the p10-p90 spread 0.776 -> 0.758 pts (i.e. nothing),
and setting PM2.5/PM10 to clean-marine values moved p50 the *wrong* way, 5.53% ->
5.85%. So this is not a plumbing gap with a fix waiting to be found.

WHY IT IS WORTH CORRECTING RATHER THAN CONTINUING TO FLAG. The 2026-08-19 tariff join
multiplied every published dollar figure by ~2.6. A proportional bias in the loss term
that was worth $78/yr on a median home is now worth $207/yr, on a public page whose
whole claim is that it tells homeowners the truth about their roof. "Documented as an
upper bound" is an acceptable state for a research number and not for a published one.

WHAT THE CORRECTION IS. A single multiplicative factor on predicted loss, measured by
``scripts/analyze/aoi_level_check.py`` against PVDAQ systems near the AOI. It does NOT
touch the ranking, the spread, or the interval width in relative terms -- there is
nothing to fix there, because the model is separately documented as unable to
discriminate within this AOI at all (0.76 pts p10-p90 across 1,865 sites). The level
is the only thing this claims to correct, and the level is the only thing that was
wrong.

WHY A SCALAR AND NOT A RETRAIN. A retrain would move the level by moving the fit, and
the fit's problem is its training geography, not its hyperparameters: 81% of NREL's
rows sit west of -114, and held out by region the model scores Arizona at chance
(``regional_holdout.json``). Rescaling to a locally measured level is the honest
description of what we can currently support -- "our national model, pinned to what
this coast actually measures" -- and it is reversible in one constant when the PVDAQ
per-array model replaces it.

THE EVIDENCE, measured 2026-08-30 (``outputs/soiling/aoi_level_check.json``):

    model AOI p50                                   5.53%
    PVDAQ measured median, within 120 km, n=118     2.76%   -> model is 2.00x
    PVDAQ measured median, within 25 km,  n=4       2.07%   -> model is 2.68x

The headline uses the 120 km band because n=118 carries the estimate and because it
is the SMALLER correction of the two, which is the conservative direction: it leaves
the published loss higher, and every downstream conclusion here is one that a higher
loss argues against. The in-county band agrees on direction and says the correction
may not be big enough.

CORROBORATION FROM A REFERENCE NOT USED TO DERIVE IT. Applying the factor puts the
AOI at **2.76%** against NREL's own coastal-California station p50 of **2.80%**
(recorded in ``econ_summary.json`` before any of this work). Those are independent
instruments and they land 0.04 pts apart.

THE CAVEAT THAT TRAVELS WITH THE NUMBER. PVDAQ and NREL agree where they overlap
(+0.16 pts, 95% CI [-0.62, +0.99], n=24) but 881 of NREL's 891 rows came from this
same class of method, so both inherit whatever it gets wrong systematically. This
corrects the model toward what the method measures nearby. It does not validate the
method.

WHAT DOES NOT CHANGE. The cleaning verdict. A *lower* loss makes a wash look worse,
and the wash already lost at the higher figure: rebuilding the AOI on the calibrated
level leaves zero of 1,865 sites with a positive expected net, as it must.

Re-measure and update ``AOI_LEVEL_FACTOR`` with:

    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/aoi_level_check.py
"""

from __future__ import annotations

import json
from pathlib import Path

#: Multiplicative correction applied to predicted annual soiling loss for the Santa
#: Cruz AOI. Measured 2026-08-30; see the module docstring for provenance. 1.0
#: disables the correction and restores the pre-2026-08-30 published level.
AOI_LEVEL_FACTOR = 0.500

#: What the factor was measured against, so a reader of a result never has to guess.
AOI_LEVEL_PROVENANCE = (
    "PVDAQ residential systems within 120 km of the AOI centroid, n=118, measured "
    "median 2.76% against the model's 5.53% (aoi_level_check.json, 2026-08-30). "
    "Corroborated by NREL coastal-CA station p50 2.80%, which was not used to derive "
    "it. Verdict unchanged: a lower loss makes cleaning look worse, not better."
)

#: This factor is specific to coastal Santa Cruz. It is NOT a global model correction
#: and must not be carried to another AOI without re-running the level check there --
#: the same script, pointed at that AOI's centroid, answers it.
AOI_LEVEL_SCOPE = "santa-cruz-w2-21cm"


def level_factor(aoi: str | None = None,
                 check_json: str | Path | None = None) -> tuple[float, str]:
    """Return ``(factor, provenance)`` for an AOI.

    Prefers a freshly measured factor from ``aoi_level_check.json`` when one is
    present and covers this AOI, so re-running the check is enough to update the
    pipeline. Falls back to the recorded constant.

    Any AOI other than the one the factor was measured for gets 1.0 and says so:
    silently applying a Santa Cruz correction to another county would be the same
    class of mistake this module exists to fix.
    """
    if aoi is not None and aoi != AOI_LEVEL_SCOPE:
        return 1.0, (f"no level calibration measured for {aoi!r}; "
                     f"AOI_LEVEL_FACTOR is scoped to {AOI_LEVEL_SCOPE!r}. Run "
                     f"scripts/analyze/aoi_level_check.py --aoi {aoi} to measure one.")

    if check_json is not None:
        p = Path(check_json)
        if p.is_file():
            try:
                d = json.loads(p.read_text())
                head = d.get("headline") or {}
                ratio = float(head.get("model_over_measured", 0))
                if ratio > 0 and d.get("aoi") == (aoi or AOI_LEVEL_SCOPE):
                    return (1.0 / ratio,
                            f"measured {p.name}: model {head.get('model_p50'):.2f}% vs "
                            f"PVDAQ median {head.get('measured_median'):.2f}% within "
                            f"{head.get('max_km'):.0f} km (n={head.get('n')})")
            except (ValueError, OSError, TypeError):
                pass  # fall through to the recorded constant

    return AOI_LEVEL_FACTOR, AOI_LEVEL_PROVENANCE
