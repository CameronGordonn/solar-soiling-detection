"""Tests for src/risk/recovery.py — time-aware cleaning recovery.

Uses synthetic weather so these are deterministic and network-free.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from risk.recovery import (  # noqa: E402
    DEFAULT_PARAMS, best_clean_date, clearsky_daily_weight, recovery_fraction,
)


def _weather(days: int = 365, rain_every: int | None = None, rain_mm: float = 20.0,
             start: str = "2025-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=days, freq="D")
    precip = np.zeros(days)
    if rain_every:
        precip[::rain_every] = rain_mm
    return pd.DataFrame({"precipitation_sum": precip, "pm10": np.full(days, 20.0)},
                        index=idx)


def test_no_rain_gives_far_higher_recovery_than_frequent_rain():
    """The core physical claim: rain does the cleaning for free."""
    dry = _weather(rain_every=None)
    wet = _weather(rain_every=10)
    mid = pd.Timestamp("2025-06-01").date()
    r_dry = recovery_fraction(dry, mid)["recovery_frac"]
    r_wet = recovery_fraction(wet, mid)["recovery_frac"]
    assert r_dry > r_wet


def test_recovery_is_never_the_legacy_ninety_percent_in_a_rainy_year():
    """A single clean cannot recover 90% of annual loss when rain resets monthly."""
    wet = _weather(rain_every=30)
    best = best_clean_date(wet, stride_days=14)
    assert best["best_recovery_frac"] < 0.30


def test_cleaning_just_after_rain_is_worthless():
    """Rain already did the job — a clean two days later has almost nothing to remove.

    (The naive expectation is the opposite: that cleaning *after* the wet season is the
    smart move. It is not, on this model: the array is at eqD ~ 1 immediately after a
    reset, so the cleaned and uncleaned trajectories are nearly identical for weeks.)
    """
    w = _weather(days=200, rain_every=None)
    w.iloc[100, w.columns.get_loc("precipitation_sum")] = 50.0   # one heavy rain, day 100
    just_after = recovery_fraction(w, w.index[101].date())["recovery_frac"]
    saturated_with_dry_run = recovery_fraction(w, w.index[140].date())["recovery_frac"]
    assert saturated_with_dry_run > just_after * 2


def test_rain_truncates_the_benefit_of_a_clean():
    """The same clean, on the same soiling state, is worth less if rain follows it."""
    dry = _weather(days=200, rain_every=None)
    with_rain = dry.copy()
    with_rain.iloc[100, with_rain.columns.get_loc("precipitation_sum")] = 50.0

    day = dry.index[98].date()
    r_dry = recovery_fraction(dry, day)
    r_rain = recovery_fraction(with_rain, day)
    assert r_rain["recovery_frac"] < r_dry["recovery_frac"]
    assert r_rain["benefit_days"] < r_dry["benefit_days"]


def test_partial_efficacy_recovers_less():
    dry = _weather(rain_every=None)
    d = pd.Timestamp("2025-06-01").date()
    full = recovery_fraction(dry, d, clean_efficacy=1.0)["recovery_frac"]
    rinse = recovery_fraction(dry, d, clean_efficacy=0.7)["recovery_frac"]
    assert 0 < rinse < full


def test_zero_efficacy_recovers_nothing():
    dry = _weather(rain_every=None)
    r = recovery_fraction(dry, pd.Timestamp("2025-06-01").date(), clean_efficacy=0.0)
    assert r["recovery_frac"] == pytest.approx(0.0, abs=1e-9)


def test_recovery_is_bounded():
    for rain_every in (None, 5, 30, 90):
        w = _weather(rain_every=rain_every)
        for day in ("2025-02-15", "2025-06-01", "2025-11-01"):
            f = recovery_fraction(w, pd.Timestamp(day).date())["recovery_frac"]
            assert 0.0 <= f <= 1.0


def test_slower_resoiling_raises_recovery():
    """k is the dominant free parameter — a slower re-soil holds the benefit longer."""
    w = _weather(rain_every=60)
    d = pd.Timestamp("2025-06-05").date()
    fast = recovery_fraction(w, d, params={**DEFAULT_PARAMS, "k": 15.0})["recovery_frac"]
    slow = recovery_fraction(w, d, params={**DEFAULT_PARAMS, "k": 60.0})["recovery_frac"]
    assert slow > fast


def test_clean_date_outside_the_window_raises():
    w = _weather(days=100)
    with pytest.raises(ValueError):
        recovery_fraction(w, pd.Timestamp("2030-01-01").date())


def test_missing_precipitation_raises():
    bad = pd.DataFrame({"pm10": [1.0, 2.0]},
                       index=pd.date_range("2025-01-01", periods=2, freq="D"))
    with pytest.raises(ValueError):
        recovery_fraction(bad, pd.Timestamp("2025-01-01").date())


def test_production_weight_favours_summer_cleans():
    """A summer clean should look better once days are weighted by PV output."""
    w = _weather(rain_every=None)
    weight = clearsky_daily_weight(w.index)
    summer = pd.Timestamp("2025-06-01").date()
    winter = pd.Timestamp("2025-12-01").date()
    s_un = recovery_fraction(w, summer)["recovery_frac"]
    w_un = recovery_fraction(w, winter)["recovery_frac"]
    s_pw = recovery_fraction(w, summer, production_weight=weight)["recovery_frac"]
    w_pw = recovery_fraction(w, winter, production_weight=weight)["recovery_frac"]
    # production weighting must improve summer's standing relative to winter
    assert (s_pw / max(w_pw, 1e-9)) > (s_un / max(w_un, 1e-9))


def test_clearsky_daily_weight_peaks_in_summer():
    idx = pd.date_range("2025-01-01", periods=365, freq="D")
    wgt = clearsky_daily_weight(idx)
    assert wgt.idxmax().month in (5, 6, 7)
    assert wgt.idxmin().month in (11, 12, 1)
