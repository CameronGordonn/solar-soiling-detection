"""Public dashboard payload contract tests."""

from math import nan

from scripts.product.build_dashboard_data import (
    _conditional_persistent_economic_properties,
    _module_orientation_dashboard_properties,
    _persistent_dashboard_properties,
    _persistent_screen_dashboard_properties,
)


def test_persistent_payload_keeps_the_full_explainable_screen():
    recommendation = {
        "persistent_soiling_status": "inspection_candidate",
        "persistent_soiling_inspection_action": "inspect",
        "persistent_soiling_score": 0.81,
        "persistent_soiling_rank": 4,
        "persistent_soiling_top_decile": True,
        "low_tilt_score": 0.9,
        "canopy_exposure_score": 0.6,
        "canopy_frac": 0.25,
        "nearest_canopy_m": 3.5,
        "persistent_soiling_tilt_weight": 0.7,
        "persistent_soiling_canopy_weight": 0.3,
        "persistent_soiling_canopy_radius_m": 9.0,
        "persistent_soiling_top_fraction": 0.1,
        "persistent_soiling_loss_threshold_pct": 3.0,
        "persistent_soiling_value_usd_per_kwh": 0.4284,
        "persistent_soiling_group_kw": 10.0,
        "persistent_soiling_dollars_at_risk": 216.70,
        "persistent_soiling_two_year_loss_threshold_pct": 3.0,
        "persistent_soiling_two_year_horizon_years": 2,
        "persistent_soiling_two_year_dollars_at_risk": 433.40,
    }

    assert _persistent_dashboard_properties(recommendation) == recommendation


def test_persistent_payload_omits_missing_values_without_dropping_false():
    payload = _persistent_dashboard_properties({
        "persistent_soiling_status": "not_flagged",
        "persistent_soiling_top_decile": False,
        "persistent_soiling_score": None,
    })

    assert payload == {
        "persistent_soiling_status": "not_flagged",
        "persistent_soiling_top_decile": False,
    }


def test_raw_persistent_screen_is_publishable_without_a_recommendation_run():
    payload = _persistent_screen_dashboard_properties({
        "persistent_soiling_top_decile": False,
        "persistent_soiling_score": 0.42,
        "persistent_soiling_rank": 17,
        "low_tilt_score": 0.5,
        "canopy_exposure_score": 0.23,
        "canopy_frac": 0.0,
        "nearest_canopy_m": nan,
        "tilt_weight": 0.7,
        "canopy_weight": 0.3,
        "canopy_radius_m": 9,
        "inspection_fraction": 0.1,
    })

    assert payload == {
        "persistent_soiling_status": "not_flagged",
        "persistent_soiling_inspection_action": "none",
        "persistent_soiling_top_decile": False,
        "persistent_soiling_score": 0.42,
        "persistent_soiling_rank": 17,
        "low_tilt_score": 0.5,
        "canopy_exposure_score": 0.23,
        "canopy_frac": 0.0,
        "persistent_soiling_tilt_weight": 0.7,
        "persistent_soiling_canopy_weight": 0.3,
        "persistent_soiling_canopy_radius_m": 9.0,
        "persistent_soiling_top_fraction": 0.1,
    }


def test_raw_persistent_screen_prices_the_selected_group_not_the_whole_site():
    row = {"area_m2": 40.0, "sun_hours": 5.5, "usd_per_kwh": 0.4284}
    screen = {"persistent_soiling_top_decile": True}

    payload = _conditional_persistent_economic_properties(row, screen)

    assert payload["persistent_soiling_loss_threshold_pct"] == 3.0
    assert payload["persistent_soiling_group_kw"] == 7.39
    assert payload["persistent_soiling_dollars_at_risk"] > 0
    assert payload["persistent_soiling_two_year_loss_threshold_pct"] == 3.0
    assert payload["persistent_soiling_two_year_dollars_at_risk"] == (
        2 * payload["persistent_soiling_dollars_at_risk"]
    )


def test_module_orientation_keeps_its_measurement_method_and_unresolved_status():
    assert _module_orientation_dashboard_properties({
        "orientation": "portrait",
        "orientation_method": "short_axis_inferred",
    }) == {
        "module_orientation": "portrait",
        "module_orientation_method": "short_axis_inferred",
    }
    assert _module_orientation_dashboard_properties({
        "orientation": "unknown",
        "orientation_method": "insufficient_pixels",
    }) == {
        "module_orientation_method": "insufficient_pixels",
    }
