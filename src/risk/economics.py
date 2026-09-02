"""Per-array cleaning economics — turn a soiling-loss % into a net-$ decision.

Shared engine behind the grid report (``scripts/analyze/cleaning_economics.py``)
and the per-array recommendation (``src/solarsoiled/recommend.py``). Answers risk
**C1 (unit economics)** and implements **M2** (ship a $-based clean/wait call).

Pure-stdlib (no pandas/numpy) so it imports/runs anywhere. Dollar model:

    annual_loss_$ = system_kw * sun_hours * 365 * SYSTEM_DERATE * soiling_loss_fraction * elec_rate
    net_benefit_$ = annual_loss_$ * recovery_frac - cleaning_cost(system_kw)

--------------------------------------------------------------------------------
2026-08-09 GROUNDING PASS. Five of this module's constants were unsourced, and three
of them were wrong by more than an order of magnitude in the same direction. Each is
now sourced inline or explicitly marked ``UNSOURCED``. The headline corrections:

  ``BASE_RATE``      0.25 $/kWh (unsourced flat)  ->  regime-dependent, see risk.rates.
                     A lost kWh is worth full retail (~$0.457 in Santa Cruz) only if the
                     home would have self-consumed it; otherwise it is worth the NBT
                     export credit (~$0.039). Soiling losses land at midday, when a
                     solar home is most likely exporting, so the blended value for a
                     post-2023 no-battery customer is ~$0.165 — BELOW the old constant.

  ``recovery_frac``  0.90 of annual loss from one clean  ->  ~0.045 measured.
                     THE SINGLE LARGEST ERROR IN THE CHAIN, ~20x. One cleaning does not
                     keep an array clean for a year: at SOMOSclean k=15 the array
                     re-soils to its annual mean in ~2 weeks, and Santa Cruz gets 27-49
                     heavy-rain days a year that reset it for free. See risk.recovery.

  ``M2_PER_KW``      5.67 (implying 176 W/m2, no stated packing factor)  ->  derived
                     from a sourced module power density and an EXPLICIT packing factor.

The direction of all three corrections matters: the rate and recovery fixes make the
product look *worse*, and they are much larger than the constant refreshes that make it
look better. Reporting that is the point — see risk register C1.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import math

import random
from dataclasses import dataclass

try:  # tolerate either import convention (installed package vs. src/ on path)
    from risk.rates import (
        ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH, DEFAULT_REGIME, REGIMES,
        RETAIL_OFFSET_USD_PER_KWH, marginal_value_usd_per_kwh,
    )
except ImportError:  # pragma: no cover
    from src.risk.rates import (
        ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH, DEFAULT_REGIME, REGIMES,
        RETAIL_OFFSET_USD_PER_KWH, marginal_value_usd_per_kwh,
    )

DAYS_PER_YEAR = 365

# ── electricity value ─────────────────────────────────────────────────────────
# SOURCED. Default is the central value for the default billing regime (NBT, no
# battery): ~$0.165/kWh. Full derivation, citations and the retail/export split live in
# `risk.rates`; this is deliberately a thin re-export so there is one source of truth.
BASE_RATE = marginal_value_usd_per_kwh()

# Peak sun hours/day. SOURCED: NREL NSRDB/PVWatts long-run daily average GHI for coastal
# Santa Cruz County is ~5.3-5.7 kWh/m2/day; 5.5 is the midpoint and matches the value
# already used by the mailer and dashboard calculators, so changing it would silently
# desynchronise three surfaces. Kept at 5.5.
BASE_SUN = 5.5

# SYSTEM DERATE — added 2026-08-12. Until now this module multiplied kW x BASE_SUN x 365
# straight into dollars, i.e. it treated peak-sun-hours (a GHI figure, see above) as
# DELIVERED AC ENERGY. That implies 5.5 * 365 = 2,007.5 kWh/kWp/yr with no inverter,
# temperature, wiring or mismatch loss anywhere in the chain, and it is why the shipped
# numbers sat ~34% above the 1,500 kWh/kWp/yr that docs/AOI_CLEANING_TARGETING_PLAN.md
# assumed for the same calculation. Two surfaces disagreeing about the same quantity.
#
# Decomposition, so the next person can re-derive it:
#   PVWatts v8 default total system losses          14.08%  -> 0.8592
#   ...but that INCLUDES a 2% soiling term, and we
#      model soiling explicitly, so remove it:      /0.98   -> 0.8768
#   PVWatts default inverter efficiency             96%     -> x0.96
#                                                            = 0.8417
#
# The soiling division is the subtle part: applying PVWatts' 14% as-is would double-count
# soiling against the very quantity this module is built to price.
#
# MEASURED 2026-08-19, closing the residual this comment used to flag as open. The old
# text read: "this treats plane-of-array irradiance as equal to GHI ... a mixed residential
# fleet lands near 1.0 -- but no one has measured the orientation mix for this AOI."
#
# It has now been measured. Roof tilt and azimuth were fitted to USGS 3DEP lidar returns
# inside all 3,362 detected polygons (3,068 usable, 91.3%; src/risk/roof_geometry.py), and
# the fleet-mean annual clear-sky POA/GHI is 1.029 -- the guess was right to within 2.9%.
# So the flat treatment was nearly unbiased ACROSS the fleet, and materially wrong for an
# INDIVIDUAL home: per-roof POA/GHI runs 0.889 (p10) to 1.156 (p90).
#
# Net effect at the fleet mean: 5.5 * 1.029 * 365 * 0.84 = 1,735 kWh/kWp/yr, against a
# PVWatts-typical 1,400-1,600 for coastal-CA residential. Still mildly GENEROUS, which is
# the safe direction for a product whose conclusion is "do not clean".
SYSTEM_DERATE = 0.84

# Annual clear-sky plane-of-array irradiance for the reference orientation (20 deg, due
# south) divided by GHI at the AOI. Computed from rates._clearsky_poa over a full year;
# reproduce with scripts/analyze/fit_roof_planes.py. This is the factor BASE_SUN was
# missing: BASE_SUN is a GHI figure (see above) and every array is tilted.
POA_REF_OVER_GHI = 1.1507


def sun_hours_from_poa_rel(poa_rel: float | None, base_sun: float = BASE_SUN) -> float:
    """Per-roof peak-sun-hours from a measured orientation.

    ``poa_rel`` is annual clear-sky POA relative to a south-facing 20 deg roof, as emitted
    by ``scripts/analyze/fit_roof_planes.py``. ``None`` (no usable lidar fit, ~9% of
    arrays) falls back to ``base_sun`` unchanged, so a missing measurement can never be
    mistaken for a measured-average one.

    Two different baselines, and they move in opposite directions -- do not conflate them:

    * vs the south-20 REFERENCE: median measured ``poa_rel`` is 0.918, so a real roof
      almost always scores below the reference orientation.
    * vs the flat ``BASE_SUN`` this REPLACES: 69% of sites go UP, because ``BASE_SUN`` is a
      GHI figure and any tilted roof collects more than the horizontal. Measured on the
      Santa Cruz AOI the per-site ratio runs p10 0.93 / median 1.06 / p90 1.15, and the AOI
      total rises 3.0%.

    The net effect on the product conclusion is nil: 0 of 1,865 sites were worth cleaning
    before this change and 0 after. It sharpens who is least bad, it does not rescue anyone.
    """
    if poa_rel is None or not math.isfinite(float(poa_rel)) or float(poa_rel) <= 0:
        return float(base_sun)
    return float(base_sun) * POA_REF_OVER_GHI * float(poa_rel)

# Base-case annual soiling loss %. Fallback only: used when the regression head
# (risk.loss_model) cannot produce a prediction. See recommend.py, which flags it.
#
# CORRECTED 2026-08-12. Was 4.7, sourced to "coastal CA subset (longitude < -120.5,
# n=66)". Commit b846ad1 established that this subset contains ZERO coastal stations:
# all 66 rows are Merced, Sacramento, Santa Clara, Tehama and Alameda, i.e. a Central
# Valley p50 wearing a coastal label. Re-derived from the same file:
#
#   genuinely coastal CA counties (LA, Orange, San Diego, Ventura, Alameda)
#                                     n=332   p50 2.80   p90 5.69
#   all CA                            n=774   p50 3.10
#   two nearest stations to the AOI           4.00 (Santa Clara, 38 km)
#                                             3.10 (Alameda, 62 km)
#   RETRACTED inland anchor            n=66   p50 4.70
#
# CAVEAT, stated because the number is load-bearing: the coastal-county set is 82% Los
# Angeles rows, so it is Southern-California coastal rather than Central Coast, and the
# dataset contains no open-coast marine-layer station near Santa Cruz at all. The honest
# plausible range for this AOI is 2.8 to 4.0; every value in it is well below the 4.70
# this replaces.
BASE_SOILING_PCT = 2.80
COASTAL_CA_SOILING_P90 = 5.69

# ── array sizing: detected area -> kW ─────────────────────────────────────────
# SOURCED. A 2026 mainstream residential module is ~440 W in a 108-half-cut format of
# roughly 1.727 m x 1.118 m = 1.93 m2, i.e. ~228 W/m2 at 22.5% efficiency (Silfab
# SIL-440 QD, Canadian Solar / Clean Energy Reviews 2025-26 spec sheets). NREL's Q1-2024
# residential benchmark system uses 400 W over 1.9 m2 with frame = 21.1% = 210 W/m2.
# We take 220 W/m2 as the midpoint of the installed fleet: new installs are ~228, but a
# detected array may be several years old and lower-power.
PANEL_KW = 0.44                # ~440 W modules (2026 mainstream residential)
MODULE_AREA_M2 = 1.93          # module area including frame
MODULE_W_PER_M2 = 220.0        # midpoint of 2026 new-install (~228) and NREL 2024 (~210)

# PACKING FACTOR — the assumption this whole conversion turns on, stated explicitly
# because the old M2_PER_KW = 5.67 buried it.
#
# **Detected polygon area is the array ENVELOPE, not the module area.** The detector
# outlines the outer boundary of a panel block, which includes inter-module gaps
# (~10-25 mm), frame edges, and the rail overhang at the block ends. It excludes roof
# setbacks between separate blocks, because those become separate polygons (which is
# exactly why `risk.site_cluster` has to regroup them).
#
# MEASURED 2026-08-09 (was an unsourced 0.90 geometric estimate for a few hours).
# Joined the Santa Cruz permit registry (`data/external/sc_solar_permits.csv`, APN-keyed)
# to parcel-clustered detected sites: n=130 sites with both a permitted kW and a detected
# envelope area.
#
#   envelope m2/kW   p25 3.85   p50 5.44   p75 6.17
#   envelope W/m2    p25 162    p50 184    p75 260
#   => packing factor at 220 W/m2 modules:  p50 0.84
#
# So the geometric guess was ~7% optimistic, and the ORIGINAL 5.67 was closer to the
# truth (5.44) than the 5.05 that briefly replaced it. Reproduce with the permit join in
# scripts/analyze/rate_sensitivity.py (--permit-kw).
#
# CAVEAT on n=130: permitted kW is summed per APN, so a parcel with a later expansion
# permit can over-count kW; and the p25-p75 spread (3.85-6.17) is wide, mixing detection
# error with genuine module-vintage differences. The p50 is a defensible central value;
# the spread is why AREA_SCALE_1SIGMA stays at the measured 0.26.
PACKING_FACTOR = 0.84

#: Envelope m2 per kW. 1000 / (220 * 0.84) = 5.41, against a measured p50 of 5.44.
M2_PER_KW = 1000.0 / (MODULE_W_PER_M2 * PACKING_FACTOR)

# ── cost model (2026 estimates) ───────────────────────────────────────────────
# TEAM-SET PRICE POINTS (Cameron, 2026-08-09): $90 basic clean / $150 professional clean.
# These are the product's actual quoted prices, not estimates to be tuned — they are the
# two SKUs a partner cleaning company would sell, so the model must price against them.
# For a typical residential system (6 kW ~ 14 panels) the per-panel schedule lands below
# both floors, so these minimums ARE the residential price in practice.
MIN_PRO_SERVICE = 150.0        # professional clean (full scrub, roof access)
MIN_RINSE_SERVICE = 90.0       # basic clean (purified water + poles, no roof access)
RINSE_COST_FACTOR = 0.50       # basic ~ half a professional before the floor applies

# Professional $/panel by system size, declining with scale. SOURCED 2026-08-17.
#
# SUPERSEDES the UNSOURCED ladder (3,5,7,10,15,20,50,200 kW at 8.00..5.00 $/panel) that
# stood here until 2026-08-28. That one stopped declining at $5.00/panel from 200 kW up,
# so it charged the residential marginal rate for a crew-day job: the 1.4 MW roof in this
# AOI was quoted $16,390 for a 3,278-panel clean, about 3.3x the published price. The
# market rate keeps falling with scale because past a few hundred panels the bill is a
# crew-day, not per-panel handling.
#
# The replacement was researched and landed in the two web tools on 2026-08-17, whose
# comments said "KEEP IN SYNC with src/risk/economics.py". That never happened, so for
# eleven days the map and the calculator priced large systems off one ladder while every
# regenerated pipeline artifact priced them off another. This closes that gap; the web
# tools are unchanged and are now the thing this matches.
#
# Anchors (per panel, professional, rooftop unless noted):
#   residential CA           $5.00-12.00   californiasolarcare.org; Thumbtack CA quotes
#   marginal panel past 32   $2.50         Thumbtack (Window Genie schedule)
#   C&I rooftop <1 MW        $2.50         soilar.tech, plus mobilization
#   C&I rooftop >1 MW        $1.50         soilar.tech, plus mobilization
#   carport 1 MW             $1.50         soilar.tech
#   utility 10 MW            $0.75-1.75    soilar.tech
#   utility 101-250 MW       $0.37-0.75    soilar.tech
#
# Mobilization is not a separate term: below ~20 kW the truck-roll floors absorb it, and
# above that it is single-digit percent of a four-figure bill. RESIDENTIAL DOLLARS DO NOT
# MOVE — the median AOI array is 3.5 kW, where the $90/$150 floors bind and the ladder is
# never consulted. This changes commercial quotes only. The floors themselves are still
# the team's own price points and still decide most residential cases on their own, which
# ECONOMICS_GROUNDING section 10 flags as the open item this does NOT close.
# KEEP IN SYNC with BBF-Website/public/tools/{dashboard.js,breakeven.html}.
_RATE_KW = (3, 10, 20, 50, 100, 200, 500, 1000)
_RATE_USD = (8.0, 7.0, 5.5, 4.0, 3.0, 2.5, 2.0, 1.5)

# ── recovery: what one cleaning actually buys ─────────────────────────────────
# MEASURED, and the largest correction in this module. `risk.recovery` integrates the
# SOMOSclean daily trajectory between a cleaning date and the next natural reset, on two
# years of real Santa Cruz weather (Open-Meteo archive, 2024-08-01..2026-07-31, first
# year discarded as trajectory spin-up):
#
#   SOMOSclean annual-mean soiling loss over the evaluation year   5.06%
#     (validation: NREL coastal-CA measured p50 is 4.70% — the physics model reproduces
#      the measured level, so the ratio below rests on a calibrated trajectory)
#   BEST single-clean recovery (production-weighted, best date 2025-08-08)   0.045
#   MEAN over all candidate dates                                            0.016
#   WORST (clean just before a rain reset)                                   0.000
#
# Mechanism: at k=15 equivalent-days the array returns to its annual-mean soiling within
# about two weeks of a clean, and Santa Cruz saw 27 heavy-rain (>=10 mm) days in the
# evaluation year, each of which resets the trajectory for free. One cleaning therefore
# buys roughly one array-month of cleanliness out of twelve — not a year of it.
#
# The old 0.90 assumed a single clean captured 90% of the annual loss, which is only
# true in a climate with no rain reset and negligible re-soiling. Keeping it would
# overstate every recovery figure by ~20x.
DEFAULT_RECOVERY_PRO = 0.045      # measured best-date professional clean, coastal SCC
DEFAULT_RECOVERY_RINSE = 0.032    # same date at clean_efficacy=0.70 (measured)

#: Set True to restore the pre-2026-08-09 behaviour for A/B comparison only.
LEGACY_RECOVERY_PRO = 0.90
LEGACY_RECOVERY_RINSE = 0.70

# ── area / kW uncertainty ─────────────────────────────────────────────────────
# MEASURED. The same physical arrays came out 0.74x the area at 21cm vs 60cm imagery, so
# dollars moved ~26% from an imagery change alone with no physical change on the ground.
# Treated as a multiplicative log-normal on system_kw with that as a 1-sigma scale.
AREA_SCALE_1SIGMA = 0.26


def panel_count(system_kw: float) -> int:
    return max(1, round(system_kw / PANEL_KW))


def per_panel_rate(system_kw: float) -> float:
    """Professional cleaning $/panel at this system size (piecewise-linear, clamped)."""
    if system_kw <= _RATE_KW[0]:
        return _RATE_USD[0]
    if system_kw >= _RATE_KW[-1]:
        return _RATE_USD[-1]
    for i in range(1, len(_RATE_KW)):
        if system_kw <= _RATE_KW[i]:
            t = (system_kw - _RATE_KW[i - 1]) / (_RATE_KW[i] - _RATE_KW[i - 1])
            return _RATE_USD[i - 1] + t * (_RATE_USD[i] - _RATE_USD[i - 1])
    return _RATE_USD[-1]


def professional_cost(system_kw: float) -> float:
    return max(panel_count(system_kw) * per_panel_rate(system_kw), MIN_PRO_SERVICE)


def rinse_cost(system_kw: float) -> float:
    return max(RINSE_COST_FACTOR * professional_cost(system_kw), MIN_RINSE_SERVICE)


# Strategies: recovery share of annual loss + a cost-per-visit function of kW.
DEFAULT_SCENARIOS: dict[str, dict] = {
    "no_clean":      {"label": "No clean",     "recovery_frac": 0.00, "cost_fn": lambda kw: 0.0},
    "rinse_service": {"label": "Light-pro",    "recovery_frac": DEFAULT_RECOVERY_RINSE, "cost_fn": rinse_cost},
    "professional":  {"label": "Professional", "recovery_frac": DEFAULT_RECOVERY_PRO,   "cost_fn": professional_cost},
}

#: Pre-grounding scenarios, kept so the A/B in the report is reproducible.
LEGACY_SCENARIOS: dict[str, dict] = {
    "no_clean":      {"label": "No clean",     "recovery_frac": 0.00, "cost_fn": lambda kw: 0.0},
    "rinse_service": {"label": "Light-pro",    "recovery_frac": LEGACY_RECOVERY_RINSE, "cost_fn": rinse_cost},
    "professional":  {"label": "Professional", "recovery_frac": LEGACY_RECOVERY_PRO,   "cost_fn": professional_cost},
}


def annual_loss_usd(system_kw: float, sun_hours: float, soiling_frac: float, elec_rate: float,
                    derate: float = SYSTEM_DERATE) -> float:
    """Dollars of energy lost to soiling over a year if the array is never cleaned.

    ``sun_hours`` is GHI peak-sun-hours per day, so ``derate`` is what converts irradiance
    into delivered AC energy: inverter, temperature, wiring, mismatch and availability.
    It deliberately EXCLUDES a soiling term, because ``soiling_frac`` is that term. See
    ``SYSTEM_DERATE``. Pass ``derate=1.0`` to reproduce pre-2026-08-12 numbers.
    """
    annual_kwh = system_kw * sun_hours * DAYS_PER_YEAR * derate
    return annual_kwh * soiling_frac * elec_rate


def scenario_net(loss_usd: float, scen: dict, system_kw: float) -> tuple[float, float, float]:
    """Return (recovered_usd, cost_usd, net_benefit_usd) for one strategy."""
    recovered = loss_usd * scen["recovery_frac"]
    cost = scen["cost_fn"](system_kw)
    return recovered, cost, recovered - cost


def breakeven_system_kw(scen: dict, sun_hours: float, soiling_frac: float, elec_rate: float,
                        kw_max: float = 5000.0) -> float | None:
    """Smallest system (kW) at which this strategy nets > $0 (numeric — cost is kW-dependent)."""
    kw = 0.1
    while kw <= kw_max:
        loss = annual_loss_usd(kw, sun_hours, soiling_frac, elec_rate)
        if scenario_net(loss, scen, kw)[2] > 0:
            return round(kw, 1)
        kw += 0.1 if kw < 30 else 1.0
    return None


def breakeven_soiling_pct(scen: dict, system_kw: float, sun_hours: float, elec_rate: float,
                          pct_max: float = 30.0) -> float | None:
    """Lowest annual soiling-loss % at which this strategy nets > $0 for a given system."""
    pct = 0.1
    while pct <= pct_max:
        loss = annual_loss_usd(system_kw, sun_hours, pct / 100.0, elec_rate)
        if scenario_net(loss, scen, system_kw)[2] > 0:
            return round(pct, 1)
        pct += 0.1
    return None


def system_kw_from_area(area_m2: float | None) -> float | None:
    """Estimate system size from detected polygon area. Prefer a permitted kW when known.

    ``area_m2`` is the array ENVELOPE (see PACKING_FACTOR). At 21cm one house often
    resolves into several polygons — pass the SITE's summed area, not one fragment's,
    or the minimum service charge gets levied per fragment. See ``risk.site_cluster``.
    """
    if not area_m2 or area_m2 <= 0:
        return None
    return area_m2 / M2_PER_KW


def array_recommendation(
    loss_pct: float,
    system_kw: float,
    sun_hours: float = BASE_SUN,
    elec_rate: float = BASE_RATE,
    scenarios: dict[str, dict] | None = None,
) -> dict:
    """Net-$ cleaning decision for a single site.

    ``loss_pct`` is the predicted ANNUAL soiling-loss percent (e.g. 4.7). Returns the
    best strategy by net dollars, with ROI and payback for the chosen action. If no
    paid strategy clears $0, the recommendation is to not clean.
    """
    scens = scenarios or DEFAULT_SCENARIOS
    loss_usd = annual_loss_usd(system_kw, sun_hours, loss_pct / 100.0, elec_rate)

    per = {}
    for key, scen in scens.items():
        rec, cost, net = scenario_net(loss_usd, scen, system_kw)
        per[key] = {"recovered_usd": round(rec, 2), "cost_usd": round(cost, 2), "net_usd": round(net, 2)}

    best = max(scens, key=lambda k: per[k]["net_usd"])
    best_net = per[best]["net_usd"]
    worth = best != "no_clean" and best_net > 0

    roi = payback_years = None
    if worth:
        cost = per[best]["cost_usd"]
        recovered = per[best]["recovered_usd"]
        roi = round(best_net / cost, 2) if cost > 0 else None              # net $ per $ spent
        payback_years = round(cost / recovered, 2) if recovered > 0 else None

    return {
        "annual_loss_usd": round(loss_usd, 2),
        "worth_cleaning": worth,
        "recommended_action": best if worth else "no_clean",
        "expected_net_usd": best_net if worth else 0.0,
        "roi": roi,
        "payback_years": payback_years,
        "per_scenario": per,
    }


# ── uncertainty propagation ───────────────────────────────────────────────────
@dataclass(frozen=True)
class Uncertainty:
    """Per-input uncertainty for :func:`array_recommendation_mc`.

    Defaults are the measured/derived bands documented above. Every field is a source
    of dollar variance that the point estimate silently discards.
    """

    #: Loss-percent prediction interval from the regression head (risk.loss_model).
    #: Defaults to the coastal-CA measured spread when the model has not been run.
    loss_pct_p10: float | None = None
    loss_pct_p90: float | None = None
    #: 1-sigma multiplicative scale on system_kw. MEASURED at 0.26 from the 21cm-vs-60cm
    #: area shift (same arrays, 0.74x the area, so ~26% of dollars moved on imagery alone).
    area_scale_1sigma: float = AREA_SCALE_1SIGMA
    #: Electricity value band. Defaults to the active regime's sigma band.
    rate_lo: float | None = None
    rate_hi: float | None = None
    #: Recovery-fraction band. Defaults to (worst, best) measured over cleaning dates:
    #: a clean timed just before rain recovers ~0, a well-timed one ~0.045.
    recovery_lo_frac: float = 0.0
    recovery_hi_frac: float = DEFAULT_RECOVERY_PRO


def _sample_from_interval(rng: random.Random, p10: float, p50: float, p90: float) -> float:
    """Draw from a two-piece normal matched to (p10, p50, p90).

    Soiling loss is right-skewed, so a symmetric draw would misstate the upper tail —
    which is precisely the tail that decides whether cleaning ever pays. The two-piece
    (split) normal shares a mode at p50 and takes a different sigma on each side,
    each set so that the 10th/90th percentile lands on the supplied bound
    (z(0.90) = 1.2816).
    """
    z = 1.2815515655446004
    sigma_lo = max(1e-9, (p50 - p10) / z)
    sigma_hi = max(1e-9, (p90 - p50) / z)
    # Pick a side with probability proportional to each half's sigma, which is what
    # makes the resulting density continuous at the mode.
    if rng.random() < sigma_lo / (sigma_lo + sigma_hi):
        return p50 - abs(rng.gauss(0.0, sigma_lo))
    return p50 + abs(rng.gauss(0.0, sigma_hi))


def array_recommendation_mc(
    loss_pct: float,
    system_kw: float,
    sun_hours: float = BASE_SUN,
    elec_rate: float = BASE_RATE,
    scenarios: dict[str, dict] | None = None,
    unc: Uncertainty | None = None,
    *,
    n_samples: int = 2000,
    seed: int = 42,
    regime: str = DEFAULT_REGIME,
) -> dict:
    """Monte-Carlo net-$ decision carrying input uncertainty through to the dollars.

    The point estimate answers "what is the net benefit?". This answers the question the
    decision actually needs — **"what is the probability the net benefit is positive?"**
    — which is different whenever the inputs are uncertain enough to straddle zero, and
    here they always are.

    Returns the point-estimate fields plus ``expected_net_usd_p10/p50/p90`` and
    ``prob_net_positive`` for the recommended action, and flags
    ``decision_robust`` when the sign of the decision survives the uncertainty.

    Sampling (all independent, which is conservative in the sense of not manufacturing
    correlations we have not measured):
      * loss %  — two-piece normal through the p10/p50/p90 prediction interval
      * kW      — log-normal, 1-sigma = AREA_SCALE_1SIGMA (the 21cm/60cm area shift)
      * $/kWh   — uniform over the regime's sigma band
      * recovery— uniform over (recovery_lo_frac, recovery_hi_frac), i.e. cleaning-date timing
    """
    import math

    scens = scenarios or DEFAULT_SCENARIOS
    u = unc or Uncertainty()
    rng = random.Random(seed)

    # Default the loss interval to the measured coastal-CA spread scaled to this point
    # estimate, so an array with no model interval still carries honest uncertainty
    # rather than a false zero-variance claim.
    p50 = float(loss_pct)
    if u.loss_pct_p10 is None or u.loss_pct_p90 is None:
        p10 = p50 * (1.10 / 4.70)      # coastal-CA measured p10/p50 ratio
        p90 = p50 * (9.05 / 4.70)      # coastal-CA measured p90/p50 ratio
    else:
        p10, p90 = float(u.loss_pct_p10), float(u.loss_pct_p90)
    p10 = min(p10, p50)
    p90 = max(p90, p50)

    if u.rate_lo is None or u.rate_hi is None:
        reg = REGIMES[regime]
        rate_lo = marginal_value_usd_per_kwh(reg.sigma_lo, regime=regime)
        rate_hi = marginal_value_usd_per_kwh(reg.sigma_hi, regime=regime)
    else:
        rate_lo, rate_hi = float(u.rate_lo), float(u.rate_hi)
    if rate_lo > rate_hi:
        rate_lo, rate_hi = rate_hi, rate_lo

    point = array_recommendation(loss_pct, system_kw, sun_hours, elec_rate, scens)
    action = point["recommended_action"]

    # Also track, for each paid strategy, the distribution of its net — the recommended
    # action can change between draws, and reporting only the point action's spread
    # would understate the chance that *something* is worth doing.
    nets: dict[str, list[float]] = {k: [] for k in scens}
    best_nets: list[float] = []
    n_any_positive = 0
    sigma_ln = math.sqrt(math.log(1.0 + u.area_scale_1sigma ** 2))

    for _ in range(n_samples):
        s_loss = max(0.0, _sample_from_interval(rng, p10, p50, p90))
        s_kw = max(1e-6, system_kw * math.exp(rng.gauss(-0.5 * sigma_ln ** 2, sigma_ln)))
        s_rate = rng.uniform(rate_lo, rate_hi)
        s_recov = rng.uniform(u.recovery_lo_frac, u.recovery_hi_frac)

        loss_usd = annual_loss_usd(s_kw, sun_hours, s_loss / 100.0, s_rate)
        draw_best = 0.0
        for key, scen in scens.items():
            if key == "no_clean":
                nets[key].append(0.0)
                continue
            # Scale each paid strategy's recovery by the sampled timing factor, keeping
            # the rinse/professional ratio fixed at its measured value.
            ratio = (scen["recovery_frac"] / DEFAULT_RECOVERY_PRO) if DEFAULT_RECOVERY_PRO else 1.0
            rec = loss_usd * s_recov * ratio
            net = rec - scen["cost_fn"](s_kw)
            nets[key].append(net)
            draw_best = max(draw_best, net)
        best_nets.append(draw_best)
        if draw_best > 0:
            n_any_positive += 1

    def _q(vals: list[float], q: float) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
        return s[i]

    focus = nets.get(action if action != "no_clean" else
                     max((k for k in scens if k != "no_clean"),
                         key=lambda k: sum(nets[k]) / max(1, len(nets[k]))), [])
    prob_pos = (sum(1 for v in focus if v > 0) / len(focus)) if focus else 0.0

    out = dict(point)
    out.update({
        "mc_n_samples": n_samples,
        "loss_pct_p10": round(p10, 3),
        "loss_pct_p50": round(p50, 3),
        "loss_pct_p90": round(p90, 3),
        "rate_band_usd_per_kwh": [round(rate_lo, 4), round(rate_hi, 4)],
        "recovery_band": [u.recovery_lo_frac, u.recovery_hi_frac],
        "expected_net_usd_p10": round(_q(focus, 0.10), 2),
        "expected_net_usd_p50": round(_q(focus, 0.50), 2),
        "expected_net_usd_p90": round(_q(focus, 0.90), 2),
        "expected_net_usd_mean": round(sum(focus) / len(focus), 2) if focus else 0.0,
        "prob_net_positive": round(prob_pos, 4),
        "prob_any_action_positive": round(n_any_positive / n_samples, 4),
        # The decision is only worth acting on if its sign is stable under the inputs we
        # know we cannot pin down. 0.80 is a reporting convention, not a tuned threshold.
        "decision_robust": bool(prob_pos >= 0.80 or prob_pos <= 0.20),
    })
    return out
