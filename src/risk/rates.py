"""What a soiling-lost kWh is actually worth — sourced CA rates, not a flat guess.

This module exists because ``economics.BASE_RATE = 0.25 $/kWh`` was unsourced and,
more importantly, **structurally wrong in a way a flat rate cannot express**. Two
facts, both measured below, make the flat rate untenable:

1. **Soiling losses land at midday.** Only ~5.5% of a south-facing Santa Cruz array's
   annual output falls in PG&E E-TOU-C's 4-9 p.m. peak window (:data:`PRODUCTION_SHARE`,
   derived by :func:`production_shares`). Soiling scales production down roughly
   proportionally in every hour, so the lost kWh are ~94% off-peak.
2. **A lost kWh is worth one of two very different numbers.** If the home would have
   *self-consumed* it, losing it means importing at full retail — in Santa Cruz that is
   **$0.44-0.61/kWh** (:data:`TOU_TOTAL_USD_PER_KWH`). If the home would have *exported*
   it, losing it forfeits only the NBT/NEM-3.0 export credit — production-weighted
   **~$0.039/kWh** (:data:`ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH`). That is a **~12x
   spread**, and it is the single largest uncertainty in the whole dollar chain.

So the marginal value of a lost kWh is

    value = sigma * retail_offset + (1 - sigma) * export_credit

where ``sigma`` is the share of *midday* production the home self-consumes. Everything
downstream ($/yr, clean/no-clean) is roughly linear in this number, so
:func:`marginal_value_usd_per_kwh` returns it explicitly rather than burying a constant.

**The billing regime dominates sigma.** A NEM 2.0 legacy customer (interconnected before
2023-04-15) nets exports against imports at full retail, so every lost kWh costs retail
regardless of when it occurred — ``sigma`` is effectively 1.0. A post-2023 Net Billing
Tariff customer without a battery self-consumes only a minority of midday output. These
two homes, physically identical, differ ~2.5x in what soiling costs them. See
:data:`REGIMES`.

Pure-stdlib (``math`` only) so it imports anywhere ``economics`` does, and so the
clear-sky weighting is reproducible in-process rather than being a pasted magic number.

--------------------------------------------------------------------------------
SOURCING NOTE. The Santa Cruz TOU stack below is reproduced from the bill-reconciled
tariff specs in the sibling ``energy-advisor`` repo, which validates them against real
PG&E + 3CE statements to within +/-$0.22/month across 11 consecutive bills. Each
component carries its Cal. P.U.C. sheet citation. Numbers that could NOT be sourced are
marked ``UNSOURCED`` in a comment rather than left bare, per the repo constraint.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ── site geometry (Santa Cruz pilot AOI) ──────────────────────────────────────
# Centroid of the Santa Cruz County pilot AOI. Used only to weight hours by
# clear-sky irradiance; the weighting is insensitive to ~0.5 deg of position.
SITE_LAT = 36.97
SITE_LON = -122.03
TZ_MERIDIAN = -120.0          # Pacific Standard Time reference meridian
DEFAULT_TILT_DEG = 20.0       # typical residential roof pitch (4:12 ~ 18.4 deg, 5:12 ~ 22.6 deg)
DEFAULT_AZIMUTH_DEG = 180.0   # south-facing; see production_shares(azimuth_deg=...)

# ── retail rate stack: PG&E E-TOU-C delivery + 3CE generation, Santa Cruz ─────
# Volumetric ($/kWh) totals by season/TOU period, non-CARE ("standard") customer,
# INCLUDING the CCA adders and the 8.5% City of Santa Cruz Utility Users' Tax.
#
# Built from (all effective dates current as of 2026-08-09):
#   delivery      PG&E E-TOU-C, Cal. P.U.C. Sheet 61364-E (Advice 7846-E), eff. 2026-03-01
#                   summer peak 0.52240 / off 0.39940; winter peak 0.39757 / off 0.36757
#   + PCIA        2018 vintage, +0.03679/kWh (flat)
#   + gen credit  UNBUNDLING OF E-TOU-C TOTAL RATES, Sheet 61126-E: -(Generation - Bundled
#                   PCIA); summer -0.19771/-0.09471, winter -0.12699/-0.10031
#   + franchise   Schedule E-FFS, Sheet 60706-E (Advice 7797-E), residential 2018 vintage,
#                   +0.00059/kWh
#   generation    3CE "3Cchoice" MBRETCH1 matched to E-TOU-C, rate sheet eff. 2026-02-15
#                   summer 0.19573/0.09376; winter 0.12572/0.09930
#   + CEC tax     +0.0003/kWh statewide
#   x UUT         City of Santa Cruz Utility Users' Tax, 8.5% of the pre-tax subtotal
#
# Reproduce: scripts/analyze/rate_sensitivity.py --show-stack
#
# CAVEAT (sourced, deliberately NOT folded into the numbers): E-TOU-C also carries a
# Baseline Credit of -0.08140/kWh on usage within the baseline allowance (Territory T,
# 7.1 kWh/day summer / 12.9 winter). Whether a marginal avoided import falls inside or
# outside the allowance depends on household consumption, which we do not know per-array.
# These totals are the ABOVE-baseline (marginal) rate, which is the right one for a
# household whose annual usage exceeds its allowance — the common case for a solar home.
# Below-baseline usage would be ~$0.088/kWh cheaper (0.0814 x 1.085).
TOU_TOTAL_USD_PER_KWH: dict[str, float] = {
    "summer_peak":    0.60554,
    "summer_offpeak": 0.47320,
    "winter_peak":    0.47087,
    "winter_offpeak": 0.43860,
}

# PG&E E-TOU-C peak window: 4-9 p.m. EVERY day (no weekday/weekend split).
PEAK_HOURS = (16, 17, 18, 19, 20)
SUMMER_MONTHS = (6, 7, 8, 9)   # PG&E summer = Jun 1 - Sep 30

# Statewide reference point, for sanity-checking the Santa Cruz stack against the average.
# EIA Electric Power Monthly Table 5.6.A (released 2026-07-23), May 2026:
# California residential average 33.25 c/kWh; U.S. total residential 18.44 c/kWh.
# NOTE this is an AVERAGE revenue-per-kWh (total bill / total kWh, so it includes fixed
# charges spread over usage). It is NOT the marginal volumetric rate and must not be used
# as one — the Santa Cruz marginal rates above are correctly higher.
EIA_CA_RESIDENTIAL_AVG_USD_PER_KWH = 0.3325
EIA_US_RESIDENTIAL_AVG_USD_PER_KWH = 0.1844

# ── export compensation under the Net Billing Tariff (NEM 3.0) ────────────────
# Production-weighted mean of the published 576-value ACC export table (delivery +
# generation), weighted by the clear-sky POA profile below — i.e. the average credit an
# array actually earns on the kWh it exports, not the flat table mean.
#
# SOURCE + KNOWN SUBSTITUTION: the table used is SDG&E's "Legacy 2026 Export Pricing"
# (LY2026 NBT Pricing Upload, sdge.com/solar/solar-billing-plan/export-pricing, retrieved
# 2026-07-23; methodology PG&E Schedule NBT Sheet 57352-E). **A PG&E ACC table has not
# been imported**, so this is SDG&E's climate-zone values standing in for PG&E's. Both
# derive from the same CPUC Avoided Cost Calculator and the shape (near-zero midday,
# spike on late-summer evenings) is common to both, but the level is not verified for
# PG&E. Treat as order-of-magnitude, and re-derive when a PG&E table lands.
ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH = 0.0392   # SDG&E NBT-2026 vintage, prod-weighted
ACC_EXPORT_FLAT_MEAN_USD_PER_KWH = 0.0905       # same table, unweighted 8760 mean

# Why the two differ by 2.3x: ACC value is lowest at midday (grid is solar-saturated) and
# highest on summer evenings. Weighting by when a PV array actually generates therefore
# roughly halves the credit. Any model that quotes the flat mean overstates export value.

# ── self-consumption share (sigma) by billing regime ──────────────────────────
# sigma = share of MIDDAY production the home consumes itself rather than exporting.
# This is the dominant free parameter in the whole dollar chain; it is a range, not a
# point, and the sensitivity curve in scripts/analyze/rate_sensitivity.py sweeps it.


@dataclass(frozen=True)
class Regime:
    """A billing regime and the midday self-consumption share it implies."""

    key: str
    label: str
    sigma: float          # midday self-consumption share, central estimate
    sigma_lo: float       # plausible low
    sigma_hi: float       # plausible high
    note: str


REGIMES: dict[str, Regime] = {
    # NEM 2.0 (interconnected before 2023-04-15) nets exports against imports at full
    # retail over the billing period, so a lost kWh costs full retail whenever it occurred.
    # sigma is 1.0 by TARIFF CONSTRUCTION, not by behaviour — no uncertainty band needed.
    "nem2_legacy": Regime(
        "nem2_legacy", "NEM 2.0 legacy (pre-2023-04-15)", 1.00, 1.00, 1.00,
        "Exports net against imports at retail; every lost kWh costs retail. "
        "Cal. P.U.C. Schedule NEM2; legacy period runs 20 years from PTO.",
    ),
    # Net Billing Tariff, no storage. UNSOURCED FOR SANTA CRUZ SPECIFICALLY: we have no
    # metered PV+load intervals for a local solar home. The 0.30 central value is the
    # midday-hours self-consumption of a typical no-battery residential profile; whole-day
    # self-sufficiency is often quoted ~35-40% (e.g. detached-house PV self-consumption
    # literature), but the MIDDAY share is lower than the daily average because midday is
    # exactly when generation most exceeds load. Range kept wide to reflect that this is
    # an assumption, not a measurement.
    "nbt_no_battery": Regime(
        "nbt_no_battery", "NEM 3.0 / NBT, no battery", 0.30, 0.20, 0.45,
        "UNSOURCED central value — no metered local PV+load pair. Midday self-consumption "
        "is below whole-day self-sufficiency (~35-40% in the literature).",
    ),
    # NBT with storage: the battery time-shifts midday surplus into the evening peak, so
    # most midday production is effectively self-consumed. UNSOURCED: depends on battery
    # size vs. array size and on dispatch strategy.
    "nbt_battery": Regime(
        "nbt_battery", "NEM 3.0 / NBT, with battery", 0.80, 0.65, 0.95,
        "UNSOURCED — depends on battery/array sizing and dispatch. Storage shifts midday "
        "surplus to the evening peak, so most midday kWh avoid an import.",
    ),
}

DEFAULT_REGIME = "nbt_no_battery"

# ── MEASURED tariff mix for the AOI (2026-08-19) ────────────────────────────────
# The chain priced every site as NBT no-battery, which was an assumption nobody had
# tested. CaliforniaDGStats publishes every Rule 21 interconnection 1982-present
# (californiadgstats.ca.gov, Interconnected Project Sites Data Set). Filtered to the five
# AOI zip codes, residential NEM PV, n = 7,536:
#
#     NEM 2.0   52.5%
#     NEM 1.0   37.6%     -> 90.1% on a LEGACY tariff
#     NBT        9.9%
#
# NEM 1.0 and 2.0 both credit exports at retail, so they share the `nem2_legacy` regime.
# The mix is stable across all five zips (legacy 88.9-91.1%), so a single AOI-wide prior
# is defensible; it is still a POPULATION prior, not a per-home fact -- see
# `marginal_value_for_site`, which prefers a real install date whenever one exists.
#
# The dataset carries no address or APN, which is exactly why the City of Santa Cruz
# records request (docs/outreach/) is still the only route to per-home tariff vintage for
# the 1,310 city-jurisdiction sites.
AOI_TARIFF_MIX: dict[str, float] = {"nem2_legacy": 0.901, "nbt_no_battery": 0.099}

#: Population-blended value of one soiling-lost kWh under that mix. ~$0.428 against the
#: $0.165 the chain assumed -- a 2.60x understatement.
#: Both are hard cliffs in a 2.78x multiplier, so both were VERIFIED against source on
#: 2026-08-19 rather than left encoded from memory.
#:
#: NEM1_CLOSE_DATE -- CORRECT as encoded. PG&E hit its 5% NEM cap on 2016-12-15 and began
#: enrolling all new solar customers in NEM 2.0 that day (CALSSA, "PG&E Begins NEM 2.0",
#: 2016-12-14). Note this date is documentation only: NEM 1.0 and 2.0 both credit exports
#: at retail and share the `nem2_legacy` regime, so it changes no price here.
#:
#: NBT_START_DATE -- WAS WRONG BY ONE DAY, fixed 2026-08-19. CPUC D.22-12-056 (adopted
#: 2022-12-15) applies the net billing tariff to interconnection applications submitted
#: **on or after 2023-04-15**, and expressly does not affect customers who applied **by
#: 2023-04-14**. The constant read 2023-04-14 and is tested with `>=`, which put an
#: application dated exactly 2023-04-14 on NBT when the decision leaves it on NEM 2.0 --
#: i.e. it undervalued that site's lost kWh by 2.78x. One day wide, and only for
#: applications landing on the sunset date itself, but that date is exactly where the
#: filing rush piles up, so it is not a day where errors are rare.
#: The surrounding prose ("interconnected before 2023-04-15") was right all along; the
#: constant was the thing that disagreed with it.
NEM1_CLOSE_DATE = "2016-12-15"
NBT_START_DATE = "2023-04-15"


def regime_for_install_date(install_date) -> str | None:
    """Tariff regime from a real interconnection/permit date. ``None`` if unknown.

    NEM 1.0 and 2.0 are priced identically here (both credit exports at retail), so the
    only boundary that changes the answer is the NBT transition.
    """
    if install_date is None:
        return None
    import pandas as pd

    d = pd.to_datetime(install_date, errors="coerce")
    if d is None or pd.isna(d):
        return None
    return "nbt_no_battery" if d >= pd.Timestamp(NBT_START_DATE) else "nem2_legacy"


def marginal_value_for_site(install_date=None) -> tuple[float, str]:
    """(value of a lost kWh, provenance) for one site.

    Uses the site's own install date when we have one; otherwise falls back to the
    MEASURED AOI population mix rather than to the old blanket-NBT assumption. Returns the
    provenance string so a caller can report which sites are priced on evidence.
    """
    r = regime_for_install_date(install_date)
    if r is not None:
        return marginal_value_usd_per_kwh(regime=r), f"install_date:{r}"
    blended = sum(w * marginal_value_usd_per_kwh(regime=k)
                  for k, w in AOI_TARIFF_MIX.items())
    return blended, "aoi_tariff_mix"



# ── clear-sky production weighting ────────────────────────────────────────────
def _clearsky_poa(hour_of_year: int, day_of_year: int, hour: int,
                  lat: float, lon: float, tilt: float, azimuth: float) -> float:
    """Clear-sky plane-of-array irradiance (W/m2) for one hour. A WEIGHT, not a forecast.

    Textbook solar geometry (Cooper declination + equation of time) with the
    Meinel & Meinel (1976) clear-sky beam attenuation ``DNI = 1353 * 0.7 ** AM**0.678``.
    Diffuse is ignored, which biases the weight slightly toward clear midday hours.

    This is deliberately NOT a PVWatts call. PVWatts would give a better profile but
    requires an NREL API key and a network fetch; a station-independent analytic weight
    keeps this module pure-stdlib and deterministic. It is used only to answer "what
    fraction of annual output falls in each TOU period", where an analytic clear-sky
    profile and a measured one agree closely — the TOU boundaries are hours apart, not
    minutes, and cloud losses are near-uniform across the daylight hours that matter.
    """
    b = math.radians(360.0 * (day_of_year - 81) / 364.0)
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)   # minutes
    solar_t = hour + 0.5 + (4.0 * (lon - TZ_MERIDIAN) + eot) / 60.0          # hour centre
    omega = math.radians(15.0 * (solar_t - 12.0))
    dec = math.radians(23.45 * math.sin(math.radians(360.0 * (284 + day_of_year) / 365.0)))
    # gamma = surface azimuth measured from SOUTH, west positive (Duffie & Beckman sign
    # convention); the caller's `azimuth` is the usual clockwise-from-north bearing.
    phi, beta, gamma = math.radians(lat), math.radians(tilt), math.radians(azimuth - 180.0)

    cosz = math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(omega)
    if cosz <= 0.02:                      # sun down / horizon grazing
        return 0.0
    # Angle of incidence on a tilted surface — Duffie & Beckman, "Solar Engineering of
    # Thermal Processes", Eq. 1.6.2. Written out in full rather than via solar azimuth so
    # it is unambiguous about sign; for gamma = 0 it reduces to the familiar south-facing
    # form sin(dec)sin(phi-beta) + cos(dec)cos(phi-beta)cos(omega), which
    # tests/test_rates.py asserts.
    sd, cd = math.sin(dec), math.cos(dec)
    sp, cp = math.sin(phi), math.cos(phi)
    sb, cb = math.sin(beta), math.cos(beta)
    sg, cg = math.sin(gamma), math.cos(gamma)
    cos_aoi = (
        sd * sp * cb
        - sd * cp * sb * cg
        + cd * cp * cb * math.cos(omega)
        + cd * sp * sb * cg * math.cos(omega)
        + cd * sb * sg * math.sin(omega)
    )
    if cos_aoi <= 0.0:
        return 0.0
    am = min(20.0, max(1.0, 1.0 / cosz))
    dni = 1353.0 * 0.7 ** (am ** 0.678)
    return cos_aoi * dni


_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def production_shares(lat: float = SITE_LAT, lon: float = SITE_LON,
                      tilt_deg: float = DEFAULT_TILT_DEG,
                      azimuth_deg: float = DEFAULT_AZIMUTH_DEG) -> dict[str, float]:
    """Fraction of annual clear-sky production falling in each season/TOU period.

    Returns keys matching :data:`TOU_TOTAL_USD_PER_KWH`. Sums to 1.0.
    """
    totals = {k: 0.0 for k in TOU_TOTAL_USD_PER_KWH}
    doy = 0
    hoy = 0
    for month, ndays in enumerate(_DAYS_IN_MONTH, start=1):
        summer = month in SUMMER_MONTHS
        for _ in range(ndays):
            doy += 1
            for hour in range(24):
                poa = _clearsky_poa(hoy, doy, hour, lat, lon, tilt_deg, azimuth_deg)
                hoy += 1
                if poa <= 0.0:
                    continue
                peak = hour in PEAK_HOURS
                key = f"{'summer' if summer else 'winter'}_{'peak' if peak else 'offpeak'}"
                totals[key] += poa
    tot = sum(totals.values())
    return {k: v / tot for k, v in totals.items()} if tot > 0 else totals


#: Derived once at import (~8760 cheap trig evaluations, a few ms). Santa Cruz,
#: 20 deg tilt, due south. Reproduce with ``production_shares()``.
PRODUCTION_SHARE: dict[str, float] = production_shares()


def retail_offset_usd_per_kwh(shares: dict[str, float] | None = None) -> float:
    """Production-weighted retail rate — value of a lost kWh the home WOULD have used.

    This is the correct weighting for soiling: losses scale production down in every
    hour, so the average retail rate they displace is weighted by *when the array
    generates*, not by when the household consumes.
    """
    s = shares or PRODUCTION_SHARE
    return sum(TOU_TOTAL_USD_PER_KWH[k] * s.get(k, 0.0) for k in TOU_TOTAL_USD_PER_KWH)


#: ~$0.457/kWh for Santa Cruz. Note this is ABOVE the CA average (0.3325) because the
#: EIA figure is average revenue per kWh across all customers and rate classes, while
#: this is the marginal above-baseline volumetric rate in a high-cost CCA territory.
RETAIL_OFFSET_USD_PER_KWH = retail_offset_usd_per_kwh()


def marginal_value_usd_per_kwh(
    self_consumed_share: float | None = None,
    *,
    regime: str = DEFAULT_REGIME,
    retail: float | None = None,
    export: float | None = None,
) -> float:
    """$/kWh value of one soiling-lost kWh.

    ``self_consumed_share`` (sigma) overrides the regime's central value when given.
    Everything downstream is linear in this, so it is the first thing to sweep.
    """
    reg = REGIMES[regime]
    sigma = reg.sigma if self_consumed_share is None else float(self_consumed_share)
    sigma = min(1.0, max(0.0, sigma))
    r = RETAIL_OFFSET_USD_PER_KWH if retail is None else float(retail)
    x = ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH if export is None else float(export)
    return sigma * r + (1.0 - sigma) * x


def regime_value_band(regime: str = DEFAULT_REGIME) -> tuple[float, float, float]:
    """(lo, central, hi) $/kWh for a regime, from its sigma band."""
    reg = REGIMES[regime]
    return (
        marginal_value_usd_per_kwh(reg.sigma_lo, regime=regime),
        marginal_value_usd_per_kwh(reg.sigma, regime=regime),
        marginal_value_usd_per_kwh(reg.sigma_hi, regime=regime),
    )


#: Population-blended value of one soiling-lost kWh under AOI_TARIFF_MIX. ~$0.428 against
#: the $0.165 the chain assumed for every site -- a 2.60x understatement.
AOI_BLENDED_USD_PER_KWH = marginal_value_for_site(None)[0]
