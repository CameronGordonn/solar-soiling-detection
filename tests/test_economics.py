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
    PERSISTENT_SOILING_INTERVENTION_PCT,
    PERSISTENT_SOILING_TWO_YEAR_INTERVENTION_PCT,
    Uncertainty,
    annual_loss_usd,
    array_recommendation,
    array_recommendation_mc,
    breakeven_soiling_pct,
    breakeven_system_kw,
    professional_cost,
    persistent_soiling_dollars_at_risk,
    persistent_soiling_two_year_dollars_at_risk,
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


def test_persistent_dollars_at_risk_prices_the_disclosed_threshold_only():
    """Persistent-Soiling dollars use the same AC-energy arithmetic, not an expected loss."""
    assert PERSISTENT_SOILING_INTERVENTION_PCT == 3.0
    assert persistent_soiling_dollars_at_risk(6, 5.5) == pytest.approx(130.02, abs=0.02)
    assert round(
        persistent_soiling_dollars_at_risk(6, 5.5, persistent_loss_pct=1.5), 2
    ) == 65.01
    assert PERSISTENT_SOILING_TWO_YEAR_INTERVENTION_PCT == 3.0
    assert persistent_soiling_two_year_dollars_at_risk(6, 5.5) == pytest.approx(260.04, abs=0.02)
    with pytest.raises(ValueError, match="non-negative"):
        persistent_soiling_dollars_at_risk(6, 5.5, persistent_loss_pct=-1)
    with pytest.raises(ValueError, match="positive"):
        persistent_soiling_two_year_dollars_at_risk(6, 5.5, horizon_years=0)


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


# ── Seasonal-planning behaviour ───────────────────────────────────────────────
def test_small_coastal_residential_not_worth_cleaning():
    rec = array_recommendation(loss_pct=BASE_SOILING_PCT, system_kw=6)
    assert rec["worth_cleaning"] is False
    assert rec["recommended_action"] == "no_clean"
    assert rec["expected_net_usd"] == 0.0
    assert rec["per_scenario"]["rinse_service"]["net_usd"] < 0
    assert rec["per_scenario"]["professional"]["net_usd"] < 0


def test_dry_season_reset_recovery_derivation_still_reproduces():
    """The modelled dry-season figure, checked as the quantity it actually is.

    0.4944 is the share of DRY-SEASON accumulated cost avoided by a perfect early-July
    reset. It is a sound derivation and is kept. What it is NOT is the share of ANNUAL
    loss one wash recovers -- see the test below, which is the one that matters for the
    dollar chain.
    """
    import pandas as pd

    from risk.recovery import clearsky_daily_weight
    from risk.economics import (
        DRY_SEASON_RESET_RECOVERY,
        REGULAR_SOILING_DRY_SEASON_DAYS,
        REGULAR_SOILING_MIDSEASON_CLEAN_DAY,
    )

    assert REGULAR_SOILING_DRY_SEASON_DAYS == 180
    assert REGULAR_SOILING_MIDSEASON_CLEAN_DAY == 91
    dry_days = pd.date_range("2026-04-01", periods=REGULAR_SOILING_DRY_SEASON_DAYS, freq="D")
    output_weight = clearsky_daily_weight(dry_days)
    accumulated_loss = sum((day + 1) * weight for day, weight in enumerate(output_weight))
    remaining_output = sum(output_weight.iloc[REGULAR_SOILING_MIDSEASON_CLEAN_DAY:])
    expected_full_reset = (
        REGULAR_SOILING_MIDSEASON_CLEAN_DAY * remaining_output / accumulated_loss
    )

    assert expected_full_reset == pytest.approx(0.4944, abs=0.0001)
    assert DRY_SEASON_RESET_RECOVERY == pytest.approx(expected_full_reset, abs=0.0001)


def test_the_default_recovery_is_the_measured_annual_value():
    """The constant that has been wrong twice, pinned.

    HISTORY, so that changing this fails loudly and with context:

      0.90    original guess; overstated recovery ~20x.
      0.045   63fcbfc 2026-08-09, MEASURED. Produced the published "zero of 1,865".
      0.445   187161f 2026-09-04, empty commit body. Silently substituted
              DRY_SEASON_RESET_RECOVERY * efficacy -- a DRY-SEASON share used as an
              ANNUAL one, 9.9x too large. Re-running the AOI on it would have reported
              80 of 1,865 sites worth cleaning instead of zero.
      0.045   restored 2026-09-24.

    The earlier version of this test asserted the 0.445 derivation, so the suite was
    defending the regression instead of catching it. It now asserts the measured value
    and, separately, that the two quantities have not been collapsed back together.
    """
    from risk.economics import (
        DEFAULT_RECOVERY_PRO,
        DEFAULT_RECOVERY_RINSE,
        DRY_SEASON_RESET_RECOVERY,
        PROFESSIONAL_CLEAN_EFFICACY,
    )

    assert DEFAULT_RECOVERY_PRO == pytest.approx(0.045, abs=1e-6)
    assert DEFAULT_RECOVERY_RINSE == pytest.approx(0.032, abs=1e-6)
    assert DEFAULT_SCENARIOS["professional"]["recovery_frac"] == pytest.approx(0.045, abs=1e-6)
    assert DEFAULT_SCENARIOS["rinse_service"]["recovery_frac"] == pytest.approx(0.032, abs=1e-6)

    # The regression was exactly this substitution. Assert it cannot recur silently.
    assert DEFAULT_RECOVERY_PRO != pytest.approx(
        DRY_SEASON_RESET_RECOVERY * PROFESSIONAL_CLEAN_EFFICACY, abs=1e-6
    ), "annual default has been set to the dry-season figure again"

    # Corroboration: the paper measures 0.0634 over 505 observed cleans. Same order.
    assert 0.02 < DEFAULT_RECOVERY_PRO < 0.10


def test_average_regular_soiling_still_does_not_clear_a_small_residential_visit():
    """The update preserves the intended policy: average dust is a baseline, not a blanket clean."""
    rec = array_recommendation(BASE_SOILING_PCT, 6.0, sun_hours=5.5, elec_rate=0.4284)
    assert rec["worth_cleaning"] is False
    assert rec["per_scenario"]["rinse_service"]["net_usd"] < 0


def test_no_residential_system_clears_at_any_plausible_soiling_level():
    """What the measured recovery actually implies, replacing a claim it does not support.

    The previous version of this test asserted that a 20 kW system clears below 22.9%
    soiling. That held only under the 0.445 regression; at the measured 0.045 it is false,
    and asserting it would have silently re-justified the wrong constant.

    The true shape, at NEM 2.0 retail and 5.5 peak sun hours:

        6 kW   needs 72.1% annual soiling   (physically absurd)
       20 kW   needs 35.7%
       50 kW   needs 26.3%
      100 kW   needs 19.7%

    So the "heavy soiler" tail this project was built to flag does not pay at residential
    scale -- which is the finding, not a gap. Only large commercial arrays under extreme,
    unrecovered soiling get close, and nothing in the Santa Cruz AOI is in that regime.
    """
    # Residential: no plausible soiling level clears.
    assert breakeven_soiling_pct(DEFAULT_SCENARIOS["professional"], 6, 5.5, 0.4573,
                                 pct_max=30.0) is None
    # Even a 20 kW array needs more than the 30-point ceiling.
    assert breakeven_soiling_pct(DEFAULT_SCENARIOS["professional"], 20, 5.5, 0.4573,
                                 pct_max=30.0) is None
    # The threshold falls with size but stays far outside anything measured here.
    big = breakeven_soiling_pct(DEFAULT_SCENARIOS["professional"], 100, 5.5, 0.4573,
                                pct_max=100.0)
    assert big is not None and 15.0 < big < 25.0

    # And at the AOI's own average soiling, no system size clears at all.
    assert breakeven_system_kw(DEFAULT_SCENARIOS["professional"], 5.5,
                               BASE_SOILING_PCT / 100.0, 0.4573) is None


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
