"""How panel tilt changes soiling accumulation and rain self-cleaning.

Until now every array in the pipeline used the same soiling trajectory regardless of how
steeply it sits: ``sl_sat`` and a flat ``heavy_rain_mm = 10.0`` for a 3-degree flat-roof
mount and a 35-degree gable alike. That is the one place measured tilt can change a
cleaning *verdict* rather than just a dollar total, because it moves how much recoverable
soiling exists in the first place.

Physical mechanism
------------------
Both effects are gravity-driven runoff:

1. **Deposition.** On a shallow panel, water sheets slowly and dries in place, leaving its
   particulate load behind; on a steep one, runoff carries particles off the glass. Flatter
   panels therefore reach a higher steady-state soiling level.
2. **Rain reset.** For the same reason a shallow panel needs a heavier rain event before
   runoff actually removes the accumulated layer rather than redistributing it.

Grounding
---------
The deposition curve is fitted to **Cano (2011)**, *Photovoltaic Modules: Effect of Tilt
Angle on Soiling* (MS thesis, Arizona State University) — nine mini-modules held at 0, 5,
10, 15, 20, 23, 30, 33 and 40 degrees over Jan-Mar 2011, one set washed every other day and
one never washed after day one. Reported mean insolation loss to soiling: **2.02% at 0deg,
1.05% at 23deg, 0.96% at 33deg.** Normalised to the 0deg module and fitted to

    f(theta) = a + (1 - a) * exp(-theta / theta0)

which passes through all three points at ``a = 0.4425``, ``theta0 = 11.64 deg``. The shape is
the physically right one: a steep fall from horizontal, flattening past ~25deg where runoff
is already efficient and further tilt buys little.

**Everything here is normalised to 1.0 at 20 degrees**, the tilt the whole dollar chain
already assumed (``rates.DEFAULT_TILT_DEG``). A 20deg roof therefore behaves EXACTLY as
before, which is what keeps the existing SOMOSclean calibration intact -- ``sl_sat=0.08``
and ``k=15`` were fitted against NREL coastal-CA station-years and reproduce their measured
4.70% annual loss (model 5.06%). Renormalising anywhere else would silently invalidate that.

What is solid and what is not
-----------------------------
* The **deposition** factor carries real measured numbers. Caveat: Cano is Mesa, Arizona --
  desert dust, not coastal marine aerosol and organics. The *shape* should transfer (it is
  runoff geometry); the amplitude may not.
* The **rain-threshold** factor reuses the same curve for a different mechanism, and that is
  a modelling assumption, not a measurement. Direction is well supported -- the literature
  puts full-cleaning thresholds anywhere from 0.3 to 20 mm/day and repeatedly finds the worst
  accumulation between 0 and 5 degrees -- but no study we have gives threshold *as a function
  of* tilt. Applying the curve keeps every value inside the published 0.3-20 mm envelope
  (18.4 mm at 0deg, 8.5 mm at 40deg). Treat the amplitude as ASSUMED and the sign as SOURCED.

Callers pass ``tilt_deg=None`` to disable the whole thing, which reproduces pre-2026-08-19
behaviour exactly.
"""

from __future__ import annotations

import math

#: Fitted to Cano (2011) Table/figure values, normalised to the 0-degree module.
CANO_ASYMPTOTE = 0.4425
CANO_SCALE_DEG = 11.641

#: Everything is expressed relative to this tilt so the existing calibration is preserved.
#: Must stay equal to rates.DEFAULT_TILT_DEG.
REFERENCE_TILT_DEG = 20.0

#: Clamp before evaluating. Below 0 is meaningless; above 60 the curve is flat anyway and
#: real fits that high are usually a wall or a failed plane, not a roof.
MIN_TILT_DEG, MAX_TILT_DEG = 0.0, 60.0


def _cano(tilt_deg: float) -> float:
    t = min(max(float(tilt_deg), MIN_TILT_DEG), MAX_TILT_DEG)
    return CANO_ASYMPTOTE + (1.0 - CANO_ASYMPTOTE) * math.exp(-t / CANO_SCALE_DEG)


_REF = _cano(REFERENCE_TILT_DEG)


def tilt_soiling_factor(tilt_deg: float | None) -> float:
    """Steady-state soiling multiplier vs a 20-degree roof. ``None`` -> 1.0.

    >>> round(tilt_soiling_factor(0), 3)     # flat roof soils far worse
    1.843
    >>> round(tilt_soiling_factor(20), 3)    # the reference, unchanged by construction
    1.0
    >>> round(tilt_soiling_factor(40), 3)
    0.849
    """
    if tilt_deg is None or not math.isfinite(float(tilt_deg)):
        return 1.0
    return _cano(tilt_deg) / _REF


def tilt_rain_threshold_factor(tilt_deg: float | None) -> float:
    """Multiplier on the rain thresholds that count as a clean. ``None`` -> 1.0.

    Same curve, same direction: a flat panel that accumulates 1.84x as much also needs a
    heavier event to shed it. See the module docstring on why the amplitude is ASSUMED.
    """
    return tilt_soiling_factor(tilt_deg)


def adjusted_params(params: dict, tilt_deg: float | None) -> dict:
    """Copy of a SOMOSclean parameter dict with the tilt response applied.

    Scales ``sl_sat`` (how dirty it gets) and both rain thresholds (what counts as a clean).
    ``k``, the re-soiling time constant, is deliberately left alone: Cano measured steady
    -state *level*, not rate, and inventing a rate dependence would not be grounded.
    """
    out = dict(params)
    if tilt_deg is None:
        return out
    s = tilt_soiling_factor(tilt_deg)
    r = tilt_rain_threshold_factor(tilt_deg)
    if "sl_sat" in out:
        out["sl_sat"] = float(out["sl_sat"]) * s
    for key in ("heavy_rain_mm", "rain_min_mm"):
        if key in out:
            out[key] = float(out[key]) * r
    return out
