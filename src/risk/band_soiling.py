"""Bottom-edge band soiling: the channel where light rain LOADS instead of cleans.

Why this module exists separately from `src.risk.recovery`
----------------------------------------------------------
`recovery._trajectory_with_clean` models the dust channel, and its rain response
is a partial clean:

    pr >= heavy (10 mm)   -> f = 0.0                              full reset
    rmin <= pr < heavy    -> f = 1 - (pr-rmin)/(heavy-rmin)       PARTIAL CLEAN
    pr < rmin, high PM10  -> f > 1                                accelerated load
    otherwise             -> f = 1.0                              normal load

That is correct for dust on the open glass and it is measured
(`docs/ECONOMICS_GROUNDING_20260809.md`). It is **backwards** for the bottom-edge
band, and the sign flip is the whole point of this module.

Zhao et al. 2021, "Characterization of Soiling Bands on the Bottom Edges of PV
Modules" (Front. Energy Res. 9:665411), on a controlled cleaned-vs-control pair:

  * A module frame stands 1-3 mm proud of the front glass, so the bottom edge is a
    stagnant trap; lower tilt makes the trap more effective.
  * LIGHT-TO-MODERATE rain mobilises dust off the open glass and carries it INTO
    the trap. The stagnant water buffers the drop impact, so "raindrops have little
    effect on particles deposited at the bottom". Bands "gradually become thicker"
    through such seasons.
  * Only HEAVY rain clears the band.

So for this channel a sub-threshold rain day is a LOADING event, not a partial
reset. Everything else about the Kimber/SOMOSclean saturation form is reused, so
the two channels stay comparable and only the rain response differs.

What is sourced and what is not
-------------------------------
SOURCED
  * The sign of the light-rain response, and heavy rain as the only reset (Zhao).
  * Low tilt increases accumulation: `src.risk.tilt_response` fits Cano (2011),
    a flat module reaching ~1.84x the steady-state soiling of a 20 deg one.
  * The loss mapping from band coverage f to % of array output:
    `scripts/analyze/substring_shade_loss.py`, pvlib `bishop88` at cell
    granularity. Independently corroborated: Gostein et al. 2015 measured 9% loss
    from a band covering 0.5% of module area; our f=0.2 portrait/string case gives
    9.10%.

ASSUMED, and the ground survey exists to settle it
  * `F_SAT`, the band coverage fraction a roof saturates at. This is THE unknown.
    The written stop rule is f < 0.1 anywhere in town kills the thesis.
  * `K_BAND`, the loading time constant in equivalent light-rain events.
  * That mineral and biological band material load and clear alike. Zhao et al.
    did not separate them and we do not either.

Do not quote any absolute dollar figure out of this module until `F_SAT` is
measured. It is an ordering and sensitivity device.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from src.risk.tilt_response import tilt_soiling_factor

#: mm/day at or above which a rain event flushes the band. READ from
#: `recovery.DEFAULT_PARAMS` rather than re-declared: the two channels must agree on
#: what a heavy day is or their outputs cannot be compared, and a comment asserting
#: that they agree is not the same as their agreeing. The gamma incident of
#: 2026-08-27 was exactly this, two files with a constant apart and a comment
#: claiming they matched.
from src.risk.recovery import DEFAULT_PARAMS as _RECOVERY_PARAMS  # noqa: E402

HEAVY_RAIN_MM = float(_RECOVERY_PARAMS["heavy_rain_mm"])
#: mm/day below which a day contributes no transport at all (trace/dew).
TRACE_MM = 0.2

#: ASSUMED. Equivalent loading events to reach 1/e of saturation.
K_BAND = 25.0
#: ASSUMED, and the single most important unknown in the whole thesis.
#: Band coverage fraction of the bottom cell row at saturation. The ground survey
#: measures this. f=0.1 is the documented kill threshold; f=0.2-0.3 is where the
#: substring model says it starts to bite hard.
#:
#: GEOMETRY CAVEAT. The substring model assumes a CONTINUOUS band along the bottom
#: cell row. Practitioner reports of moss on panels describe growth concentrated at
#: the bottom frame edge "where moisture sits", but also appearing on the side
#: frames and mid-glass, i.e. patchy rather than a clean stripe. This matters less
#: than it sounds for PORTRAIT modules, where the bottom row spans all three
#: substrings at two cells each, so occluding one cell per substring reproduces
#: most of the band effect. It matters more for LANDSCAPE, where the band must run
#: the length of a single substring to cut it. Patchiness therefore biases the
#: landscape cases optimistic and the portrait cases less so.
F_SAT = 0.30

#: Dry days still load the band by direct deposition, but far less efficiently
#: than a transport event, because nothing is carrying material downslope.
DRY_LOAD_WEIGHT = 0.15


def band_trajectory(
    daily: pd.DataFrame,
    tilt_deg: float | None = None,
    clean_idx: int | None = None,
    efficacy: float = 1.0,
    k: float = K_BAND,
    f_sat: float = F_SAT,
) -> pd.Series:
    """Daily band coverage fraction f in [0, f_sat].

    ``daily`` needs a ``precipitation_sum`` column in mm and a DatetimeIndex.
    ``tilt_deg`` scales loading via the Cano curve; pass None to disable.
    ``clean_idx`` is the positional index of a wash, ``efficacy`` how much of the
    band it removes (1.0 = a scrub that takes the band off entirely).

    The rain response is the inverse of the dust channel's, which is the point.
    """
    precip = (pd.to_numeric(daily["precipitation_sum"], errors="coerce")
              .fillna(0.0).to_numpy())
    # Cano (2011): a flat module accumulates ~1.84x a 20 deg one. The band is a
    # gravity-and-runoff phenomenon, so tilt acts on the loading rate.
    tf = 1.0 if tilt_deg is None else tilt_soiling_factor(tilt_deg)

    f = np.zeros(len(precip))
    load = 0.0
    for i, pr in enumerate(precip):
        if pr >= HEAVY_RAIN_MM:
            load = 0.0                      # flush
        elif pr >= TRACE_MM:
            # THE SIGN FLIP. Transport scales with how much rain fell without
            # reaching flush strength: a 9 mm day moves far more material into
            # the trap than a 0.5 mm day, and neither clears it.
            load += tf * (pr - TRACE_MM) / (HEAVY_RAIN_MM - TRACE_MM)
        else:
            load += tf * DRY_LOAD_WEIGHT
        if clean_idx is not None and i == clean_idx:
            load *= (1.0 - efficacy)
        f[i] = f_sat * (1.0 - np.exp(-load / k))
    return pd.Series(f, index=daily.index, name="band_coverage_f")


#: Band coverage f -> % of ARRAY output lost, for 4 affected modules in a 20-module
#: array. Measured by `scripts/analyze/substring_shade_loss.py` (pvlib bishop88,
#: 60-cell 295 W). Reproduce with that script; do not edit by hand.
#
# The f=0.0 anchors are REQUIRED, not decoration. `np.interp` clamps rather than
# extrapolates below its first x, so without them every coverage under 0.1 returned
# the f=0.1 loss (1.85% on portrait/string) instead of approaching zero. That made
# a clean and an unwashed roof score identically at low coverage and drove
# `bio_recovery_fraction` to exactly 0.000. Zero occlusion must cost zero.
_LOSS_TABLE: dict[str, dict[float, float]] = {
    # (orientation, topology, inverter) -> {f: array loss %}
    "landscape_full_mlpe":   {0.0: 0.0, 0.1: 0.79, 0.2: 2.35, 0.3: 4.23, 0.5: 7.00, 1.0: 7.00},
    "landscape_full_string": {0.0: 0.0, 0.1: 2.12, 0.2: 7.00, 0.3: 7.00, 0.5: 7.00, 1.0: 7.00},
    "portrait_full_mlpe":    {0.0: 0.0, 0.1: 0.63, 0.2: 2.15, 0.3: 4.01, 0.5: 8.20, 1.0: 8.20},
    "portrait_full_string":  {0.0: 0.0, 0.1: 1.85, 0.2: 9.10, 0.3: 18.39, 0.5: 21.00, 1.0: 21.00},
}


def band_loss_pct(f: float, case: str = "portrait_full_string",
                  n_modules: int = 4, array_modules: int = 20) -> float:
    """Interpolate array output loss (%) for band coverage ``f``.

    ``case`` selects orientation, cell topology and inverter architecture. It
    matters enormously: the same moss line costs 7.00% landscape and 21.00%
    portrait-on-a-string-inverter. Neither module orientation nor install era is
    currently extracted per roof, so a per-roof estimate carries a ~3x fork until
    they are. See `docs/PER_ARRAY_SOILING_PATH_20260826.md` section 6, step 1.
    """
    if case not in _LOSS_TABLE:
        raise KeyError(f"unknown case {case!r}; have {sorted(_LOSS_TABLE)}")
    tbl = _LOSS_TABLE[case]
    xs = np.array(sorted(tbl))
    ys = np.array([tbl[x] for x in xs])
    base = float(np.interp(np.clip(f, 0.0, 1.0), xs, ys))
    # The table is for 4 affected modules in a 20-module array; scale linearly in
    # affected-module count. The substring model supports linearity: portrait/string
    # gives 5.25% at 1 module, 21.00% at 4, 40.02% at 8.
    #
    # DO NOT cap at n_modules/array_modules. An earlier version did, and it
    # truncated the portrait/string f=0.5 case from 21.00% to 20.00% -- destroying
    # precisely the nonlinearity this module exists to represent. With a string
    # inverter, shaded modules current-limit the WHOLE series string, so array loss
    # legitimately EXCEEDS the affected modules' area share. That is the effect,
    # not an error to clamp away.
    scaled = base * (n_modules / 4.0)
    if "mlpe" in case:
        # MLPE genuinely does bound loss at the affected modules' share, because
        # each module MPPTs independently.
        return float(np.clip(scaled, 0.0, 100.0 * n_modules / array_modules))
    return float(np.clip(scaled, 0.0, 100.0))


def band_recovery_fraction(
    daily: pd.DataFrame,
    clean_idx: int,
    tilt_deg: float | None = None,
    horizon_days: int = 365,
    case: str = "portrait_full_string",
    n_modules: int = 4,
    **kw,
) -> Mapping[str, float]:
    """Fraction of a year's band loss that one wash recovers.

    The dust-channel analogue is `recovery.recovery_fraction`, which measured
    0.045 for coastal Santa Cruz. The band channel should score far higher,
    because only heavy rain resets it and a scrub removes it outright. That
    difference is the entire economic case, so it is computed the same way for
    a like-for-like comparison.
    """
    end = min(clean_idx + horizon_days, len(daily))
    win = slice(clean_idx, end)
    f_no = band_trajectory(daily, tilt_deg, None, **kw).to_numpy()[win]
    f_cl = band_trajectory(daily, tilt_deg, clean_idx, **kw).to_numpy()[win]
    loss_no = np.array([band_loss_pct(x, case, n_modules) for x in f_no])
    loss_cl = np.array([band_loss_pct(x, case, n_modules) for x in f_cl])
    denom = loss_no.sum()
    return {
        "recovery_frac": float((loss_no - loss_cl).sum() / denom) if denom > 0 else 0.0,
        "mean_loss_pct_no_clean": float(loss_no.mean()),
        "mean_loss_pct_cleaned": float(loss_cl.mean()),
        "f_final_no_clean": float(f_no[-1]) if len(f_no) else 0.0,
        "days": int(end - clean_idx),
    }


# ── the BIOLOGICAL channel, which is not the same thing ───────────────────────
# The functions above model Zhao et al.'s MINERAL band: dust transported into the
# frame trap by light rain, flushed out by heavy rain. Running Santa Cruz through
# it gives a band that never accumulates (f_end ~0.007), because 25.5 heavy-rain
# days a year keep flushing it. That is a real negative for the mineral mechanism
# here, and it agrees with `band_rain_regime.py`, where the AOI's build:clear
# ratio of 2.00 sits below every site where mineral bands were documented.
#
# Biological soiling does NOT work that way, and conflating the two was an error
# worth naming. Moss, lichen and sub-aerial biofilm are ATTACHED, GROWING
# organisms. Rain does not flush them off; rain feeds them. The controlling
# variable is not particle transport but Time of Wetness, and the only reset is
# mechanical cleaning. That difference is the entire economic case, because it is
# what makes `recovery_frac` near 1 instead of 0.045.
#
# Sourced:
#   * Growth, not deposition: Porcar et al. 2018 (Front. Microbiol. 9:3043)
#     characterise sub-aerial biofilm on panels in Berkeley; Shirakawa et al. 2015
#     measure coverage building 42/53/58% over 6/12/18 months in Sao Paulo with
#     power loss 7% then 11%.
#   * Wetness as the limiting variable: ISO 9223 time-of-wetness, and the facade
#     biofilm literature, where north aspect, shading and slow drying are the
#     recognised drivers.
#   * Slow recolonisation after cleaning: algal biofilm ~12 months to become
#     recognisable; lichen "only poorly re-established" after 54 months.
# Assumed:
#   * K_BIO, and that Shirakawa's tropical growth rate scales linearly with
#     growth-weighted TOW. This is the weakest link and the survey is what tests it.

#: ASSUMED, AND UNANCHORED. Growth-weighted wet hours to reach 1/e of saturation.
#:
#: An earlier comment claimed this was anchored to Shirakawa's 53% coverage at 12
#: months. That was a CATEGORY ERROR and is retracted. Shirakawa measured the
#: fraction of WHOLE PANEL AREA carrying organic matter, producing loss by diffuse
#: optical attenuation (58% coverage -> 11% loss, an attenuation factor near 0.19,
#: i.e. the film is fairly transparent). Our `f` is something else entirely: the
#: fraction of the BOTTOM CELL ROW occluded by a band, producing loss by substring
#: bypass. The two are different quantities with different loss physics and one
#: cannot calibrate the other.
#:
#: 6000 h is therefore a round number chosen to put AOI coverage in a plausible
#: range over a decade, not a measurement. Treat every absolute output of
#: `bio_trajectory` as a sensitivity axis. `scripts/analyze/aoi_cleaning_threshold.py`
#: sweeps it for exactly this reason.
K_BIO_HOURS = 6000.0


def roof_equilibrium_f(shade_factor: float, f_sat: float = F_SAT) -> float:
    """Equilibrium biofilm coverage for a roof's own drying environment.

    CORRECTION, 2026-08-27. An earlier version let `shade_factor` scale only the
    GROWTH RATE, with every roof converging on the same `f_sat`. Over a decade of
    AOI wetness that saturated all 2,494 arrays at f = 0.30 and erased the
    per-roof variation the whole exercise exists to find. It is also empirically
    false: plenty of roofs carry no visible moss after fifteen years.

    Biofilm coverage is a balance of growth against MORTALITY -- desiccation and
    UV on a sunny, steep, well-drained panel, which do not merely slow growth,
    they kill it. So the drying environment sets the EQUILIBRIUM, and a roof below
    the persistence floor supports essentially none.

    Still assumed in shape, but assumed in the physically right place.
    """
    # Below this multiplier a surface dries fast enough that biofilm does not
    # persist at all. 1.0 is the regional-average roof.
    floor = 1.05
    if shade_factor <= floor:
        return 0.0
    # Saturating response above the floor, reaching f_sat only for the wettest,
    # most shaded roofs.
    return float(f_sat * (1.0 - np.exp(-2.5 * (shade_factor - floor))))


def bio_trajectory(
    wet_hours_daily: pd.Series,
    clean_idx: int | None = None,
    efficacy: float = 1.0,
    k_hours: float = K_BIO_HOURS,
    f_sat: float = F_SAT,
    shade_factor: float = 1.0,
) -> pd.Series:
    """Daily biological coverage fraction. NOT reset by rain, only by cleaning.

    ``wet_hours_daily`` is growth-weighted wet hours per day (see
    `scripts/analyze/biological_growth_potential.py` for the weighting).
    ``shade_factor`` is the per-roof modifier: canopy, low tilt and north aspect
    all lengthen the wet period on that surface. 1.0 is the regional average roof.
    It sets both how fast the film grows AND, via `roof_equilibrium_f`, how much
    the roof can sustain at all.
    """
    wh = pd.to_numeric(wet_hours_daily, errors="coerce").fillna(0.0).to_numpy()
    f_eq = roof_equilibrium_f(shade_factor, f_sat)
    if f_eq <= 0.0:
        return pd.Series(np.zeros(len(wh)), index=wet_hours_daily.index,
                         name="bio_coverage_f")
    f = np.zeros(len(wh))
    acc = 0.0
    for i, h in enumerate(wh):
        acc += shade_factor * h
        if clean_idx is not None and i == clean_idx:
            acc *= (1.0 - efficacy)
        f[i] = f_eq * (1.0 - np.exp(-acc / k_hours))
    return pd.Series(f, index=wet_hours_daily.index, name="bio_coverage_f")


def bio_recovery_fraction(
    wet_hours_daily: pd.Series,
    clean_idx: int,
    horizon_days: int = 365,
    case: str = "portrait_full_string",
    n_modules: int = 4,
    **kw,
) -> Mapping[str, float]:
    """Fraction of a year's biological loss recovered by one mechanical clean.

    Expect this to be close to 1 where the dust channel gives 0.045, because
    nothing in the weather puts the biofilm back inside a year.
    """
    end = min(clean_idx + horizon_days, len(wet_hours_daily))
    win = slice(clean_idx, end)
    f_no = bio_trajectory(wet_hours_daily, None, **kw).to_numpy()[win]
    f_cl = bio_trajectory(wet_hours_daily, clean_idx, **kw).to_numpy()[win]
    loss_no = np.array([band_loss_pct(x, case, n_modules) for x in f_no])
    loss_cl = np.array([band_loss_pct(x, case, n_modules) for x in f_cl])
    denom = loss_no.sum()
    return {
        "recovery_frac": float((loss_no - loss_cl).sum() / denom) if denom > 0 else 0.0,
        "mean_loss_pct_no_clean": float(loss_no.mean()),
        "mean_loss_pct_cleaned": float(loss_cl.mean()),
        "f_final_no_clean": float(f_no[-1]) if len(f_no) else 0.0,
        "days": int(end - clean_idx),
    }
