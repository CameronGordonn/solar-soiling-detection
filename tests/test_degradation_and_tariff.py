"""Guards on the two age-keyed corrections added 2026-08-19.

Both are driven by an install date and pull in opposite directions: a legacy NEM home is
worth 2.78x more per lost kWh and is also older, so more degraded. The failure mode these
tests exist to catch is a missing date silently becoming a *confident* answer in either
direction -- an unknown-age array aged as if it were new, or an unknown-tariff site priced
as if it were NBT.
"""

import pytest

from src.risk import rates
from src.risk.degradation import (
    DEGRADATION_BAND_PCT_PER_YR, MEDIAN_DEGRADATION_PCT_PER_YR,
    PVDAQ_2107_DEGRADATION_PCT_PER_YR, age_years, module_w_per_m2, production_factor,
)


class TestDegradation:
    def test_default_is_the_literature_median_not_our_single_site(self):
        """Jordan & Kurtz ~2,000 rates, not one 893 kW ground mount in Arbuckle."""
        assert MEDIAN_DEGRADATION_PCT_PER_YR == 0.5
        assert PVDAQ_2107_DEGRADATION_PCT_PER_YR < MEDIAN_DEGRADATION_PCT_PER_YR
        lo, hi = DEGRADATION_BAND_PCT_PER_YR
        assert lo < MEDIAN_DEGRADATION_PCT_PER_YR < hi

    def test_unknown_age_is_never_aged(self):
        assert production_factor(None) == 1.0
        assert age_years(None) is None
        assert age_years("not a date") is None

    def test_compounds_rather_than_subtracts(self):
        """0.5%/yr for 20 yr is 0.905, not 0.90 -- the published rates are compounding."""
        assert production_factor(20) == pytest.approx(0.9046, abs=1e-4)
        assert production_factor(20) > 1 - 20 * 0.005

    def test_monotone_and_bounded(self):
        vals = [production_factor(a) for a in (0, 5, 10, 20, 40)]
        assert all(a > b for a, b in zip(vals, vals[1:]))
        assert all(0 < v <= 1 for v in vals)

    def test_negative_age_clamps(self):
        assert production_factor(-3) == 1.0

    def test_band_brackets_the_default(self):
        lo, hi = DEGRADATION_BAND_PCT_PER_YR
        assert production_factor(15, hi) < production_factor(15) < production_factor(15, lo)

    def test_vintage_curve_is_monotone_and_none_safe(self):
        assert module_w_per_m2(None) is None
        ws = [module_w_per_m2(y) for y in (2008, 2012, 2016, 2020, 2024, 2026)]
        assert all(a < b for a, b in zip(ws, ws[1:]))
        assert module_w_per_m2(1990) == module_w_per_m2(2008)   # clamped, not extrapolated
        assert module_w_per_m2(2040) == module_w_per_m2(2026)


class TestTariff:
    def test_mix_is_a_distribution(self):
        assert sum(rates.AOI_TARIFF_MIX.values()) == pytest.approx(1.0, abs=1e-9)
        assert all(k in rates.REGIMES for k in rates.AOI_TARIFF_MIX)

    def test_measured_mix_is_legacy_dominated(self):
        """DGStats, 5 AOI zips, n=7,536: 90.1% NEM 1.0/2.0."""
        assert rates.AOI_TARIFF_MIX["nem2_legacy"] == pytest.approx(0.901, abs=1e-3)

    def test_blend_sits_between_the_two_regimes(self):
        lo = rates.marginal_value_usd_per_kwh(regime="nbt_no_battery")
        hi = rates.marginal_value_usd_per_kwh(regime="nem2_legacy")
        assert lo < rates.AOI_BLENDED_USD_PER_KWH < hi

    def test_unknown_date_falls_back_to_the_measured_mix_not_to_nbt(self):
        """The bug this replaces: every undated site was silently priced as NBT."""
        v, src = rates.marginal_value_for_site(None)
        assert src == "aoi_tariff_mix"
        assert v == pytest.approx(rates.AOI_BLENDED_USD_PER_KWH)
        assert v > rates.marginal_value_usd_per_kwh(regime="nbt_no_battery")

    @pytest.mark.parametrize("d,regime", [
        ("2009-05-01", "nem2_legacy"),      # NEM 1.0 era, priced as legacy retail
        ("2019-03-01", "nem2_legacy"),
        ("2023-04-13", "nem2_legacy"),
        # Verified against CPUC D.22-12-056 on 2026-08-19. This case previously asserted
        # 2023-04-14 was NBT, matching a constant that was itself a day early. The
        # decision does not affect applications submitted BY 2023-04-14, so the sunset
        # date is the last legacy day, not the first NBT day.
        ("2023-04-14", "nem2_legacy"),      # last legacy day
        ("2023-04-15", "nbt_no_battery"),   # the cliff itself
        ("2025-01-01", "nbt_no_battery"),
    ])
    def test_cliff_is_on_the_documented_date(self, d, regime):
        assert rates.regime_for_install_date(d) == regime
        _, src = rates.marginal_value_for_site(d)
        assert src == f"install_date:{regime}"

    def test_legacy_is_worth_about_2_8x_nbt(self):
        ratio = (rates.marginal_value_usd_per_kwh(regime="nem2_legacy")
                 / rates.marginal_value_usd_per_kwh(regime="nbt_no_battery"))
        assert ratio == pytest.approx(2.78, abs=0.05)


class TestNemCliffDates:
    """The two tariff cliff dates, pinned to source.

    Both were encoded from memory and flagged in-code as unverified. Verified against
    source 2026-08-19; NBT_START_DATE moved 2023-04-14 -> 2023-04-15. These assertions
    exist so the boundary cannot drift back by a day unnoticed -- a one-day error here
    reprices a site by 2.78x, and it lands on the exact date applications pile up.
    """

    def test_nbt_starts_april_15_2023_per_d2212056(self):
        # CPUC D.22-12-056: NBT applies to applications submitted ON OR AFTER 2023-04-15.
        assert rates.NBT_START_DATE == "2023-04-15"

    def test_pge_nem1_closed_2016_12_15(self):
        # PG&E hit its 5% NEM cap 2016-12-15. Documentation only -- NEM 1.0 and 2.0 share
        # the nem2_legacy regime -- but wrong dates get copied into slides.
        assert rates.NEM1_CLOSE_DATE == "2016-12-15"

    def test_april_14_2023_is_still_legacy(self):
        # The regression this fixes: the sunset date itself belongs to NEM 2.0, not NBT.
        assert rates.regime_for_install_date("2023-04-14") == "nem2_legacy"

    def test_april_15_2023_is_nbt(self):
        assert rates.regime_for_install_date("2023-04-15") == "nbt_no_battery"

    def test_the_one_day_is_worth_2_78x(self):
        # State the stake, so a future edit sees what it is trading.
        v14, _ = rates.marginal_value_for_site("2023-04-14")
        v15, _ = rates.marginal_value_for_site("2023-04-15")
        assert v14 / v15 == pytest.approx(2.78, abs=0.05)

    def test_unknown_date_falls_back_to_measured_aoi_mix(self):
        v, prov = rates.marginal_value_for_site(None)
        assert prov == "aoi_tariff_mix"
        assert v == pytest.approx(0.4284, abs=5e-3)
