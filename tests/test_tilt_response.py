"""Guards on the tilt -> soiling response.

The property that must never break: the response is normalised at 20 degrees, because the
SOMOSclean calibration (sl_sat=0.08, k=15) was fitted against NREL coastal-CA station-years
and reproduces their measured 4.70% annual loss. Renormalising anywhere else silently
invalidates that validation without any test failing.
"""

import pytest

from src.risk import rates
from src.risk.tilt_response import (
    REFERENCE_TILT_DEG, adjusted_params, tilt_soiling_factor,
)

BASE = {"sl_sat": 0.08, "heavy_rain_mm": 10.0, "rain_min_mm": 1.0, "k": 15.0}


def test_reference_tilt_is_a_no_op():
    assert tilt_soiling_factor(REFERENCE_TILT_DEG) == pytest.approx(1.0, abs=1e-9)
    p = adjusted_params(BASE, REFERENCE_TILT_DEG)
    assert p["sl_sat"] == pytest.approx(BASE["sl_sat"])
    assert p["heavy_rain_mm"] == pytest.approx(BASE["heavy_rain_mm"])


def test_reference_matches_the_chains_default_tilt():
    """If these drift apart the calibration silently moves. See module docstring."""
    assert REFERENCE_TILT_DEG == rates.DEFAULT_TILT_DEG


def test_none_disables_everything():
    assert tilt_soiling_factor(None) == 1.0
    assert adjusted_params(BASE, None) == BASE


def test_monotone_decreasing_in_tilt():
    vals = [tilt_soiling_factor(t) for t in (0, 5, 10, 15, 20, 25, 30, 40, 50)]
    assert all(a > b for a, b in zip(vals, vals[1:]))


def test_matches_cano_2011_measurements():
    """Fitted curve must still reproduce the three published points, ratio to 0 deg."""
    f0 = tilt_soiling_factor(0.0)
    assert tilt_soiling_factor(23.0) / f0 == pytest.approx(1.05 / 2.02, abs=0.01)
    assert tilt_soiling_factor(33.0) / f0 == pytest.approx(0.96 / 2.02, abs=0.01)


def test_flat_roof_needs_heavier_rain_not_lighter():
    """The sign that is easy to get backwards: shallow panels shed LESS readily."""
    flat = adjusted_params(BASE, 0.0)
    steep = adjusted_params(BASE, 40.0)
    assert flat["heavy_rain_mm"] > BASE["heavy_rain_mm"] > steep["heavy_rain_mm"]
    assert flat["sl_sat"] > BASE["sl_sat"] > steep["sl_sat"]


def test_stays_inside_the_published_rain_threshold_envelope():
    """Literature spans 0.3-20 mm/day for a full-cleaning threshold; do not leave it."""
    for t in (0, 10, 20, 30, 45, 60):
        assert 0.3 <= adjusted_params(BASE, t)["heavy_rain_mm"] <= 20.0


def test_k_is_left_alone():
    """Cano measured steady-state level, not re-soiling rate. Do not invent a rate term."""
    assert adjusted_params(BASE, 0.0)["k"] == BASE["k"]


def test_extreme_tilts_are_clamped_not_extrapolated():
    assert tilt_soiling_factor(75.0) == tilt_soiling_factor(60.0)
    assert tilt_soiling_factor(-10.0) == tilt_soiling_factor(0.0)
