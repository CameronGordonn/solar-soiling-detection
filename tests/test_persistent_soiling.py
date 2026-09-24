import pytest

from src.risk.persistent_soiling import (
    canopy_exposure_score,
    flag_top_fraction,
    low_tilt_score,
    persistent_soiling_score,
)


def test_low_tilt_score_is_bounded_and_monotonic():
    assert low_tilt_score(0) == 1.0
    assert low_tilt_score(35) == 0.0
    assert low_tilt_score(10) > low_tilt_score(25)
    # The evidence-based curve falls fastest near horizontal, not linearly.
    assert low_tilt_score(5) - low_tilt_score(10) > low_tilt_score(25) - low_tilt_score(30)
    assert low_tilt_score(None) == 0.0


def test_canopy_exposure_uses_proximity_or_amount():
    assert canopy_exposure_score(0.0, 0.0) == 1.0
    assert canopy_exposure_score(0.8, 9.0) == 0.8
    assert canopy_exposure_score(0.0, None) == 0.0


def test_score_uses_requested_70_30_weighting():
    assert persistent_soiling_score(0.0, 1.0, 0.0) == pytest.approx(1.0)
    assert persistent_soiling_score(0.0, 0.0, None) == pytest.approx(0.7)
    assert persistent_soiling_score(35.0, 0.0, 0.0) == pytest.approx(0.3)


def test_top_decile_flags_ceiling_of_aoi_count():
    import pandas as pd

    ranked = flag_top_fraction(pd.DataFrame({"score": [0.9] * 11}))
    assert ranked["persistent_soiling_top_decile"].sum() == 2
    assert ranked["persistent_soiling_rank"].tolist() == list(range(1, 12))
