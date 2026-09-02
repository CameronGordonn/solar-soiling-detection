"""Tests for src/risk/rates.py — the sourced electricity-value model.

Pure-stdlib module; these run without the geo/ML stack.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from risk import rates  # noqa: E402


def test_clearsky_reduces_to_the_south_facing_closed_form():
    """The general Duffie & Beckman AOI expression must reduce at azimuth = 180.

    This guards the exact bug caught during development: an azimuth formulation with a
    sign error still produced plausible-looking output (8.4% peak share instead of
    5.5%), so only checking the reduction catches it.
    """
    lat, lon, tilt = rates.SITE_LAT, rates.SITE_LON, 20.0
    phi, beta = math.radians(lat), math.radians(tilt)
    for doy in (15, 105, 196, 288):
        b = math.radians(360.0 * (doy - 81) / 364.0)
        eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
        dec = math.radians(23.45 * math.sin(math.radians(360.0 * (284 + doy) / 365.0)))
        for hour in range(24):
            solar_t = hour + 0.5 + (4.0 * (lon - rates.TZ_MERIDIAN) + eot) / 60.0
            omega = math.radians(15.0 * (solar_t - 12.0))
            cosz = (math.sin(phi) * math.sin(dec)
                    + math.cos(phi) * math.cos(dec) * math.cos(omega))
            if cosz <= 0.02:
                continue
            ref = (math.sin(dec) * math.sin(phi - beta)
                   + math.cos(dec) * math.cos(phi - beta) * math.cos(omega))
            am = min(20.0, max(1.0, 1.0 / cosz))
            expected = max(0.0, ref) * 1353.0 * 0.7 ** (am ** 0.678)
            got = rates._clearsky_poa(0, doy, hour, lat, lon, tilt, 180.0)
            assert got == pytest.approx(expected, abs=1e-6)


def test_production_shares_sum_to_one():
    s = rates.PRODUCTION_SHARE
    assert sum(s.values()) == pytest.approx(1.0, abs=1e-9)
    assert set(s) == set(rates.TOU_TOTAL_USD_PER_KWH)


def test_soiling_losses_land_overwhelmingly_off_peak():
    """The structural fact that makes a flat rate wrong: PV barely generates 4-9pm."""
    peak = rates.PRODUCTION_SHARE["summer_peak"] + rates.PRODUCTION_SHARE["winter_peak"]
    assert peak < 0.10, "south-facing array should put <10% of output in the 4-9pm peak"


def test_west_facing_shifts_output_into_the_peak_window():
    west = rates.production_shares(azimuth_deg=270.0)
    peak_w = west["summer_peak"] + west["winter_peak"]
    peak_s = rates.PRODUCTION_SHARE["summer_peak"] + rates.PRODUCTION_SHARE["winter_peak"]
    assert peak_w > peak_s * 1.5


def test_export_credit_is_far_below_retail():
    assert rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH < rates.RETAIL_OFFSET_USD_PER_KWH / 5


def test_production_weighting_lowers_the_export_credit():
    """Midday is when exports are worth least, so weighting must reduce the mean."""
    assert (rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH
            < rates.ACC_EXPORT_FLAT_MEAN_USD_PER_KWH)


def test_marginal_value_interpolates_between_export_and_retail():
    assert rates.marginal_value_usd_per_kwh(0.0) == pytest.approx(
        rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH)
    assert rates.marginal_value_usd_per_kwh(1.0) == pytest.approx(
        rates.RETAIL_OFFSET_USD_PER_KWH)
    mid = rates.marginal_value_usd_per_kwh(0.5)
    assert (rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH < mid
            < rates.RETAIL_OFFSET_USD_PER_KWH)


def test_sigma_is_clamped_to_unit_interval():
    assert rates.marginal_value_usd_per_kwh(-3.0) == pytest.approx(
        rates.marginal_value_usd_per_kwh(0.0))
    assert rates.marginal_value_usd_per_kwh(9.0) == pytest.approx(
        rates.marginal_value_usd_per_kwh(1.0))


def test_nem2_legacy_is_worth_far_more_per_lost_kwh_than_nbt():
    """The segmentation result: identical homes, ~2.8x different soiling cost."""
    nem2 = rates.marginal_value_usd_per_kwh(regime="nem2_legacy")
    nbt = rates.marginal_value_usd_per_kwh(regime="nbt_no_battery")
    assert nem2 / nbt > 2.0


def test_regime_bands_are_ordered():
    for key in rates.REGIMES:
        lo, c, hi = rates.regime_value_band(key)
        assert lo <= c <= hi


def test_retail_stack_is_above_the_eia_state_average():
    """Sanity, not a bug: EIA's figure is average revenue/kWh, ours is marginal."""
    assert (rates.RETAIL_OFFSET_USD_PER_KWH
            > rates.EIA_CA_RESIDENTIAL_AVG_USD_PER_KWH)
    for v in rates.TOU_TOTAL_USD_PER_KWH.values():
        assert 0.2 < v < 1.0, "a CA residential volumetric rate outside this range is a typo"
