"""Guards on the AOI loss-level calibration.

The calibration is a single multiplicative constant applied to every published
soiling percentage and therefore to every published dollar figure. Three ways it
could go wrong silently, one test each:

  1. It gets applied to an AOI it was never measured for. The factor is fitted to
     PVDAQ systems near Santa Cruz; carrying it to another county would be the same
     class of mistake it exists to correct.
  2. It drifts away from the measurement without anyone re-running the check.
  3. It silently becomes a no-op (or an inversion) through an editing accident.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.risk.level_calibration import (
    AOI_LEVEL_FACTOR, AOI_LEVEL_SCOPE, level_factor,
)

REPO = Path(__file__).resolve().parents[1]
CHECK_JSON = REPO / "outputs/soiling/aoi_level_check.json"


def test_factor_is_a_real_correction_not_a_noop_or_inversion():
    """Between 0 and 1: the model reads HIGH, so the correction must pull down."""
    assert 0.2 < AOI_LEVEL_FACTOR < 1.0, (
        "the measured bias is the model over-predicting, so the factor belongs in "
        "(0, 1). A value at or above 1.0 would raise an already-high level."
    )


def test_other_aois_get_no_calibration_and_are_told_why():
    factor, why = level_factor("some-other-county")
    assert factor == 1.0
    assert "aoi_level_check" in why or "scoped" in why


def test_scoped_aoi_gets_the_correction():
    factor, _ = level_factor(AOI_LEVEL_SCOPE)
    assert factor == pytest.approx(AOI_LEVEL_FACTOR, abs=1e-9)


def test_none_aoi_falls_back_to_the_recorded_constant():
    """A caller that does not name an AOI gets the constant, not silence."""
    factor, why = level_factor(None)
    assert factor == pytest.approx(AOI_LEVEL_FACTOR, abs=1e-9)
    assert why


@pytest.mark.skipif(not CHECK_JSON.is_file(),
                    reason="aoi_level_check.json not built in this tree")
def test_constant_still_matches_the_measurement_on_disk():
    """The recorded constant must not drift from what the check last measured.

    Tolerance is deliberately loose: the check re-runs as more PVDAQ systems land
    and the factor will move a little. This catches a constant left behind after a
    materially different measurement, not ordinary sampling movement.
    """
    d = json.loads(CHECK_JSON.read_text())
    ratio = float(d["headline"]["model_over_measured"])
    measured = 1.0 / ratio
    assert measured == pytest.approx(AOI_LEVEL_FACTOR, abs=0.10), (
        f"AOI_LEVEL_FACTOR is {AOI_LEVEL_FACTOR} but aoi_level_check.json implies "
        f"{measured:.3f}. Re-run scripts/analyze/aoi_level_check.py and update the "
        f"constant, or explain the gap."
    )


@pytest.mark.skipif(not CHECK_JSON.is_file(),
                    reason="aoi_level_check.json not built in this tree")
def test_measurement_carries_its_reproduction_check_caveat():
    """The PVDAQ/NREL shared-method caveat must travel with the number."""
    d = json.loads(CHECK_JSON.read_text())
    assert "caveat" in d and "method" in d["caveat"].lower()
    assert d["headline"]["n"] >= 20, "headline band is too thin to calibrate on"
