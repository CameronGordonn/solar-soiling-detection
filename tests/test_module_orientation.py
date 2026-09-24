"""Unit tests for panel-layout classification without requesting county imagery."""

import pytest

from scripts.analyze.module_orientation import GSD_M, MODULE_LONG_M, MODULE_SHORT_M, classify


def _measurement(pitch1_m, pitch2_m, theta1=90.0, theta2=0.0):
    return {
        "theta1_deg": theta1,
        "pitch1_px": pitch1_m / GSD_M,
        "ac1": 0.7,
        "theta2_deg": theta2,
        "pitch2_px": pitch2_m / GSD_M,
        "ac2": 0.6,
    }


def test_classify_uses_a_measured_long_module_axis():
    result = classify(
        _measurement(MODULE_LONG_M, MODULE_SHORT_M), tilt_deg=10.0, azimuth_deg=180.0
    )

    assert result["orientation"] == "portrait"
    assert result["orientation_method"] == "long_axis_measured"
    assert result["long_axis_upslope_frac"] == pytest.approx(1.0)


def test_classify_recovers_layout_from_one_clear_short_axis_and_row_spacing():
    result = classify(
        _measurement(MODULE_SHORT_M, 2.45), tilt_deg=10.0, azimuth_deg=180.0
    )

    assert result["orientation"] == "landscape"
    assert result["orientation_method"] == "short_axis_inferred"
    assert result["long_axis_upslope_frac"] == pytest.approx(0.0)


def test_classify_refuses_two_ambiguous_short_axis_matches():
    result = classify(
        _measurement(MODULE_SHORT_M, MODULE_SHORT_M), tilt_deg=10.0, azimuth_deg=180.0
    )

    assert result["orientation"] == "unknown"
    assert result["orientation_method"] == "unresolved"
    assert "both pitches match a module short axis" in result["reason"]
