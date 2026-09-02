"""Tests for the per-array cleaning-economics core (src/risk/economics.py).

Pure-stdlib module, so these run without the geo/ML stack.

2026-08-09: three tests here asserted product behaviour that only held under the
retired ``recovery_frac = 0.90`` constant (one cleaning recovers 90% of a year's
soiling loss). That constant was measured wrong by ~20x — see ``src/risk/recovery.py``.
Those assertions are preserved verbatim against ``LEGACY_SCENARIOS`` so the A/B stays
pinned and the regression is explicit, and new tests assert what the grounded model
actually says. A test suite that had been updated to simply match the new numbers would
have hidden the size of the correction.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from risk.economics import (  # noqa: E402
    AREA_SCALE_1SIGMA,
    BASE_RATE,
    BASE_SOILING_PCT,
    DEFAULT_RECOVERY_PRO,
    DEFAULT_SCENARIOS,
    LEGACY_SCENARIOS,
    M2_PER_KW,
    PACKING_FACTOR,
    Uncertainty,
    annual_loss_usd,
    array_recommendation,
    array_recommendation_mc,
    breakeven_soiling_pct,
    breakeven_system_kw,
    professional_cost,
    rinse_cost,
    scenario_net,
    system_kw_from_area,
)


# ── unchanged core arithmetic ─────────────────────────────────────────────────
def test_annual_loss_formula():
    # 6 kW, 5.5 GHI sun-h, 3% loss, $0.25/kWh.
    # SYSTEM_DERATE (0.84) was added 2026-08-12: before it, this chain treated
    # peak-sun-hours as delivered AC energy and implied 2,007.5 kWh/kWp/yr.
    assert round(annual_loss_usd(6, 5.5, 0.03, 0.25), 2) == 75.88
    # derate=1.0 reproduces the pre-fix number exactly, so the size of the
    # correction stays visible rather than being absorbed into a new constant.
    assert round(annual_loss_usd(6, 5.5, 0.03, 0.25, derate=1.0), 2) == 90.34


def test_derate_excludes_soiling_to_avoid_double_counting():
    """PVWatts' 14% default bundles a 2% soiling term; this module prices soiling
    itself via ``soiling_frac``, so the derate must have it divided back out."""
    from risk.economics import SYSTEM_DERATE

    pvwatts_default = 0.8592          # 14.08% total system losses
    expected = pvwatts_default / 0.98 * 0.96   # remove soiling, apply inverter eff.
    assert SYSTEM_DERATE == pytest.approx(expected, abs=0.005)
    # A derate that still carried PVWatts' soiling term would be ~2% lower.
    assert SYSTEM_DERATE > pvwatts_default * 0.96


def test_base_soiling_pct_is_a_coastal_figure():
    """Guards the 2026-08-12 correction: BASE_SOILING_PCT was 4.70, sourced to a
    'coastal CA' subset that commit b846ad1 showed is 66/66 inland Central Valley
    stations. Genuinely coastal CA reads p50 2.80."""
    assert BASE_SOILING_PCT == pytest.approx(2.80, abs=0.01)
    # The whole defensible range for this AOI (coastal p50 2.80 to nearest-station
    # 4.00) sits below the retracted inland anchor.
    assert BASE_SOILING_PCT < 4.70


def test_per_panel_cost_has_minimum_and_bulk_discount():
    # small system hits the minimum service charge; large system gets a lower $/panel
    assert professional_cost(3) == 150.0          # 7 panels * $8 = $56 -> floored to $150
    assert rinse_cost(3) >= 50.0                   # rinse minimum
    assert professional_cost(500) > professional_cost(50)   # bigger system costs more in total
    # ...but cheaper per panel at scale (bulk discount)
    from risk.economics import per_panel_rate
    assert per_panel_rate(5) > per_panel_rate(50)


def test_no_clean_is_zero_baseline():
    rec, cost, net = scenario_net(500.0, DEFAULT_SCENARIOS["no_clean"], system_kw=10)
    assert (rec, cost, net) == (0.0, 0.0, 0.0)


def test_recommendation_picks_max_net_scenario():
    rec = array_recommendation(loss_pct=4, system_kw=12, sun_hours=5.5, elec_rate=0.25)
    nets = {k: v["net_usd"] for k, v in rec["per_scenario"].items()}
    best = max(nets, key=nets.get)
    if nets[best] > 0 and best != "no_clean":
        assert rec["recommended_action"] == best
    else:
        assert rec["recommended_action"] == "no_clean"


# ── LEGACY behaviour, pinned so the A/B stays reproducible ────────────────────
# These are the pre-2026-08-09 assertions, run against LEGACY_SCENARIOS. They document
# what the product claimed when recovery_frac was 0.90/0.70 and the rate was a flat 0.25.
def test_legacy_large_high_soiling_site_was_worth_cleaning():
    rec = array_recommendation(loss_pct=6, system_kw=40, sun_hours=5.5, elec_rate=0.25,
                               scenarios=LEGACY_SCENARIOS)
    assert rec["worth_cleaning"] is True
    assert rec["recommended_action"] in ("rinse_service", "professional")
    assert rec["expected_net_usd"] > 0
    assert rec["roi"] is not None and rec["roi"] > 0
    assert rec["payback_years"] is not None and rec["payback_years"] > 0


def test_legacy_professional_breakeven_above_lightpro():
    pro = breakeven_system_kw(LEGACY_SCENARIOS["professional"], 5.5, 0.08, 0.25)
    light = breakeven_system_kw(LEGACY_SCENARIOS["rinse_service"], 5.5, 0.08, 0.25)
    assert pro is not None and light is not None
    assert pro > light > 0


def test_legacy_lightpro_won_for_high_soiling_residential():
    rec = array_recommendation(loss_pct=6, system_kw=6, sun_hours=5.5, elec_rate=0.25,
                               scenarios=LEGACY_SCENARIOS)
    assert rec["recommended_action"] == "rinse_service"
    assert rec["expected_net_usd"] > 0


# ── GROUNDED behaviour: what the measured constants actually imply ────────────
def test_small_coastal_residential_not_worth_cleaning():
    rec = array_recommendation(loss_pct=BASE_SOILING_PCT, system_kw=6)
    assert rec["worth_cleaning"] is False
    assert rec["recommended_action"] == "no_clean"
    assert rec["expected_net_usd"] == 0.0
    assert rec["per_scenario"]["rinse_service"]["net_usd"] < 0
    assert rec["per_scenario"]["professional"]["net_usd"] < 0


def test_measured_recovery_is_an_order_of_magnitude_below_legacy():
    """The single largest correction in the grounding pass — guard it explicitly."""
    assert DEFAULT_RECOVERY_PRO == pytest.approx(0.045, abs=0.005)
    assert LEGACY_SCENARIOS["professional"]["recovery_frac"] / DEFAULT_RECOVERY_PRO > 15


def test_no_system_size_rescues_the_economics_at_measured_recovery():
    """Size cancels: per-panel cost and recovered value both scale linearly in kW.

    This is the C1 kill-risk result. At the measured recovery fraction there is no
    system size at which a single annual professional clean pays for itself, at the
    measured coastal-CA soiling level and any of the sourced rates.
    """
    for rate in (0.1646, 0.3737, 0.4573):   # NBT no-battery / NBT+battery / NEM 2.0
        assert breakeven_system_kw(
            DEFAULT_SCENARIOS["professional"], 5.5, BASE_SOILING_PCT / 100.0, rate
        ) is None


def test_breakeven_soiling_exceeds_anything_ever_measured():
    """Required soiling to break even is far above the NREL maximum of 22.9%."""
    be = breakeven_soiling_pct(DEFAULT_SCENARIOS["professional"], 20, 5.5, 0.4573,
                               pct_max=100.0)
    assert be is not None and be > 22.9


# ── sourced constants ─────────────────────────────────────────────────────────
def test_base_rate_comes_from_the_sourced_rate_model():
    from risk import rates

    assert BASE_RATE == pytest.approx(rates.marginal_value_usd_per_kwh(), rel=1e-9)
    # A lost kWh is worth full retail only if self-consumed; the blend must sit between.
    assert (rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH
            < BASE_RATE
            < rates.RETAIL_OFFSET_USD_PER_KWH)


def test_area_to_kw_states_its_packing_factor():
    # M2_PER_KW must be derived from module power density x packing, not hardcoded.
    assert M2_PER_KW == pytest.approx(1000.0 / (220.0 * PACKING_FACTOR), rel=1e-9)
    # measured against the permit join: envelope p50 5.44 m2/kW (n=130 sites)
    assert M2_PER_KW == pytest.approx(5.44, abs=0.25)
    assert 4.5 < M2_PER_KW < 6.0
    # 40 m2 envelope -> ~7.9 kW at 220 W/m2 and 0.90 packing
    assert system_kw_from_area(40.0) == pytest.approx(40.0 / M2_PER_KW, rel=1e-9)
    assert system_kw_from_area(0) is None
    assert system_kw_from_area(None) is None


# ── uncertainty propagation ───────────────────────────────────────────────────
def test_mc_reports_a_band_and_a_probability():
    rec = array_recommendation_mc(BASE_SOILING_PCT, 6.0, n_samples=800, seed=7)
    assert rec["expected_net_usd_p10"] <= rec["expected_net_usd_p50"] <= rec["expected_net_usd_p90"]
    assert 0.0 <= rec["prob_net_positive"] <= 1.0
    # deeply negative case: the decision should be robust, not marginal
    assert rec["prob_net_positive"] < 0.20
    assert rec["decision_robust"] is True


def test_mc_is_deterministic_for_a_given_seed():
    a = array_recommendation_mc(5.0, 8.0, n_samples=500, seed=11)
    b = array_recommendation_mc(5.0, 8.0, n_samples=500, seed=11)
    assert a["expected_net_usd_p50"] == b["expected_net_usd_p50"]
    assert a["prob_net_positive"] == b["prob_net_positive"]


def test_mc_widens_with_a_wider_loss_interval():
    narrow = array_recommendation_mc(
        5.0, 8.0, unc=Uncertainty(loss_pct_p10=4.8, loss_pct_p90=5.2),
        n_samples=1500, seed=3, scenarios=LEGACY_SCENARIOS)
    wide = array_recommendation_mc(
        5.0, 8.0, unc=Uncertainty(loss_pct_p10=1.5, loss_pct_p90=12.0),
        n_samples=1500, seed=3, scenarios=LEGACY_SCENARIOS)
    nspan = narrow["expected_net_usd_p90"] - narrow["expected_net_usd_p10"]
    wspan = wide["expected_net_usd_p90"] - wide["expected_net_usd_p10"]
    assert wspan > nspan


def test_area_uncertainty_reflects_the_measured_imagery_shift():
    """21cm vs 60cm moved the same arrays to 0.74x area -> ~26% of dollars.

    REWRITTEN 2026-08-12. The previous version let loss, rate and recovery all vary
    and asserted that adding area uncertainty widened the p10-p90 span. That span is
    dominated by the recovery band, so the area contribution sat inside Monte-Carlo
    noise and the assertion flipped sign on an unrelated change to the dollar scale.
    It was measuring the right property through far too much noise.

    Pinning every other input makes area the ONLY source of variance, so the test now
    fails if and only if area uncertainty stops propagating.
    """
    assert AREA_SCALE_1SIGMA == pytest.approx(0.26, abs=0.01)
    pinned = dict(loss_pct_p10=6.0, loss_pct_p90=6.0, rate_lo=0.25, rate_hi=0.25,
                  recovery_lo_frac=DEFAULT_RECOVERY_PRO,
                  recovery_hi_frac=DEFAULT_RECOVERY_PRO)
    zero = array_recommendation_mc(
        6.0, 30.0, unc=Uncertainty(area_scale_1sigma=0.0, **pinned),
        n_samples=4000, seed=5, scenarios=DEFAULT_SCENARIOS)
    measured = array_recommendation_mc(
        6.0, 30.0, unc=Uncertainty(area_scale_1sigma=AREA_SCALE_1SIGMA, **pinned),
        n_samples=4000, seed=5, scenarios=DEFAULT_SCENARIOS)
    zspan = zero["expected_net_usd_p90"] - zero["expected_net_usd_p10"]
    mspan = measured["expected_net_usd_p90"] - measured["expected_net_usd_p10"]
    assert zspan == pytest.approx(0.0, abs=1e-6)   # nothing else is varying
    assert mspan > 50.0                             # measured ~112 on a 30 kW system
