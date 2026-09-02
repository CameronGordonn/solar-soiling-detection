"""How much of a year's soiling loss does ONE cleaning actually recover?

``economics.DEFAULT_SCENARIOS`` used ``recovery_frac = 0.90`` for a professional clean —
"one visit recovers 90% of the annual loss". That is not physical, and it is generous in
a coastal climate specifically:

* A cleaning resets soiling to zero **on the day it happens**. It does nothing about the
  soiling that already accrued in the months before, and nothing about the months after
  once the array has re-soiled.
* **Rain does the same job for free.** In Santa Cruz the wet season (Nov-Mar) delivers
  heavy-rain days that fully reset the SOMOSclean trajectory. A cleaning in October is
  worth little because rain would have reset the array within weeks anyway; the same
  cleaning in May captures the whole dry season.

So the recoverable share is a *function of the cleaning date*, bounded above by the share
of annual soiling loss that falls between that date and the next natural reset. The
correct quantity is a ratio of integrals over the daily trajectory:

    recovery_frac(t_clean) = ∫[t_clean, t_reset] SL_dirty(t) dt / ∫[year] SL_dirty(t) dt

where ``t_reset`` is the first heavy-rain day after ``t_clean`` (beyond which the two
trajectories, cleaned and not, have re-converged) and ``SL_dirty`` is the counterfactual
no-clean trajectory from :func:`risk.labels.somosclean_eqd_trajectory`. The cleaned
trajectory is the same model restarted from ``eqD = 0`` on the cleaning date, so the
numerator is really ∫(SL_dirty - SL_cleaned), which is what :func:`recovery_fraction`
computes exactly rather than approximating.

An imperfect clean is handled by ``clean_efficacy``: a light rinse leaves some residue,
modelled as resetting eqD to ``(1 - efficacy) * eqD`` rather than to 0.

**This lowers recovery a lot in a rain-reset climate, and that is the honest answer.**
Measured on Santa Cruz weather, the best possible single-clean recovery is well under
the 0.90 the old constant assumed — see ``scripts/analyze/recovery_calendar.py``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Mapping

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

#: SOMOSclean parameters, mirroring physics_score._DEFAULT_PARAMS (sl_sat=0.08, k=15
#: calibrated against 36 coastal-CA NREL station-years; see .claude/rules/stage2-risk.md).
DEFAULT_PARAMS: dict[str, float] = {
    "sl_sat": 0.08,
    "k": 15.0,
    "heavy_rain_mm": 10.0,
    "rain_min_mm": 1.0,
    "pm10_dust_threshold": 50.0,
    "pm10_dust_scale": 0.02,
}


def _trajectory(daily: pd.DataFrame, params: Mapping[str, float]) -> pd.Series:
    from risk.labels import somosclean_eqd_trajectory

    kw = {k: params[k] for k in DEFAULT_PARAMS if k in params}
    _, sl = somosclean_eqd_trajectory(daily, **kw)
    return sl


def _trajectory_with_clean(daily: pd.DataFrame, params: Mapping[str, float],
                           clean_idx: int, efficacy: float) -> pd.Series:
    """Re-run the trajectory, resetting eqD at ``clean_idx`` to ``(1-efficacy) * eqD``.

    Re-implemented here rather than reusing ``somosclean_eqd_trajectory`` because that
    function has no hook for an exogenous reset mid-series, and copying the six-line
    recurrence is cheaper and clearer than adding a parameter to a function that four
    other call sites depend on.
    """
    p = {**DEFAULT_PARAMS, **params}
    sl_sat, k = float(p["sl_sat"]), float(p["k"])
    heavy, rmin = float(p["heavy_rain_mm"]), float(p["rain_min_mm"])
    thr, scale = float(p["pm10_dust_threshold"]), float(p["pm10_dust_scale"])

    precip = pd.to_numeric(daily["precipitation_sum"], errors="coerce").fillna(0.0).to_numpy()
    if "pm10" in daily:
        pm = pd.to_numeric(daily["pm10"], errors="coerce")
        pm10 = pm.fillna(pm.median() if not pm.isna().all() else 0.0).to_numpy()
    else:
        pm10 = np.zeros(len(daily))

    sl = np.zeros(len(daily))
    eq = 0.0
    for i in range(len(daily)):
        pr = precip[i]
        if pr >= heavy:
            f = 0.0
        elif pr >= rmin:
            f = 1.0 - (pr - rmin) / (heavy - rmin)
        elif pm10[i] > thr:
            f = 1.0 + scale * (pm10[i] - thr)
        else:
            f = 1.0
        eq = f * (eq + 1.0)
        if i == clean_idx:
            eq *= (1.0 - efficacy)
        sl[i] = sl_sat * (1.0 - np.exp(-eq / k))
    return pd.Series(sl, index=daily.index, name="soiling_loss_cleaned")


def recovery_fraction(
    daily: pd.DataFrame,
    clean_date: date,
    *,
    params: Mapping[str, float] | None = None,
    clean_efficacy: float = 1.0,
    production_weight: pd.Series | None = None,
) -> dict:
    """Share of the year's soiling loss that one cleaning on ``clean_date`` recovers.

    ``daily`` must span the full evaluation year and carry ``precipitation_sum``
    (``pm10`` optional), indexed by date — the frame
    :func:`risk.weather_client.fetch_combined` returns.

    ``production_weight`` optionally weights each day by expected PV output, so that a
    percentage point of soiling in June counts for more than one in December. Without it
    every day counts equally, which understates the value of a spring clean. Pass
    ``None`` to skip.

    Returns the recovery fraction plus the diagnostics needed to explain it.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    if "precipitation_sum" not in daily:
        raise ValueError("recovery_fraction needs a precipitation_sum column")

    idx = pd.to_datetime(daily.index)
    target = pd.Timestamp(clean_date)
    if target < idx[0] or target > idx[-1]:
        raise ValueError(f"clean_date {clean_date} outside weather window "
                         f"{idx[0].date()}..{idx[-1].date()}")
    clean_idx = int(np.searchsorted(idx.values, target.to_datetime64(), side="left"))

    sl_dirty = _trajectory(daily, p).to_numpy()
    sl_clean = _trajectory_with_clean(daily, p, clean_idx, clean_efficacy).to_numpy()

    w = (np.ones(len(daily)) if production_weight is None
         else pd.to_numeric(production_weight, errors="coerce")
                .reindex(daily.index).fillna(0.0).to_numpy())

    denom = float(np.sum(sl_dirty * w))
    saved = float(np.sum((sl_dirty - sl_clean) * w))
    frac = (saved / denom) if denom > 0 else 0.0

    # When does the benefit end? First day at/after the clean where the two trajectories
    # have re-converged (a heavy-rain reset, or full re-soiling).
    diff = sl_dirty - sl_clean
    after = np.where(diff[clean_idx:] <= 1e-9)[0]
    reset_i = int(clean_idx + after[0]) if after.size else len(daily) - 1
    heavy = float(p["heavy_rain_mm"])
    precip = pd.to_numeric(daily["precipitation_sum"], errors="coerce").fillna(0.0).to_numpy()
    next_rain = np.where(precip[clean_idx:] >= heavy)[0]

    return {
        "recovery_frac": max(0.0, min(1.0, frac)),
        "clean_date": str(pd.Timestamp(clean_date).date()),
        "benefit_days": int(reset_i - clean_idx),
        "days_to_next_heavy_rain": int(next_rain[0]) if next_rain.size else None,
        "annual_mean_sl_pct": float(np.mean(sl_dirty) * 100.0),
        "sl_at_clean_pct": float(sl_dirty[clean_idx] * 100.0),
        "production_weighted": production_weight is not None,
        "clean_efficacy": clean_efficacy,
    }


def best_clean_date(
    daily: pd.DataFrame,
    *,
    params: Mapping[str, float] | None = None,
    clean_efficacy: float = 1.0,
    production_weight: pd.Series | None = None,
    stride_days: int = 7,
) -> dict:
    """Scan candidate cleaning dates and return the best, plus the full curve.

    ``stride_days=7`` keeps this to ~52 trajectory re-runs per site instead of 365; the
    recovery curve is smooth on a weekly scale, so the loss from striding is far below
    the model's own uncertainty.
    """
    idx = pd.to_datetime(daily.index)
    curve = []
    for i in range(0, len(daily), stride_days):
        r = recovery_fraction(daily, idx[i].date(), params=params,
                              clean_efficacy=clean_efficacy,
                              production_weight=production_weight)
        curve.append((idx[i].date(), r["recovery_frac"], r["benefit_days"]))
    if not curve:
        return {"best": None, "curve": []}
    best = max(curve, key=lambda t: t[1])
    return {
        "best_date": str(best[0]),
        "best_recovery_frac": best[1],
        "best_benefit_days": best[2],
        "worst_recovery_frac": min(c[1] for c in curve),
        "mean_recovery_frac": float(np.mean([c[1] for c in curve])),
        "curve": [{"date": str(d), "recovery_frac": f, "benefit_days": b} for d, f, b in curve],
    }


def clearsky_daily_weight(index: pd.DatetimeIndex, lat: float = 36.97,
                          lon: float = -122.03, tilt_deg: float = 20.0) -> pd.Series:
    """Daily clear-sky production weight, for ``production_weight=``.

    Reuses the hour-level clear-sky model in :mod:`risk.rates` (same geometry, same
    citation) summed to daily totals, so the recovery calculation and the rate
    calculation weight time consistently instead of each inventing its own season shape.
    """
    from risk.rates import _clearsky_poa

    vals = []
    for ts in index:
        doy = int(ts.dayofyear)
        vals.append(sum(_clearsky_poa(0, doy, h, lat, lon, tilt_deg, 180.0) for h in range(24)))
    return pd.Series(vals, index=index, name="clearsky_daily")
