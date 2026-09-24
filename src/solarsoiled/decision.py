"""The cleaning decision, served as arithmetic rather than as a model.

This module backs ``POST /decision`` and ``GET /breakeven``. It answers one question —
*is it worth paying to clean this array?* — and it is deliberately the narrowest useful
surface this project can expose.

Why this and not a risk score
-----------------------------
Three measured results constrain what an honest API here can offer.

1. **The risk model cannot rank roofs.** Its labels are measured at *stations*, so every
   roof in a catchment inherits one number. Validation folds that held out site or year
   but not both reported 0.710; under a joint fold the model scores 0.622, and latitude
   and longitude alone score 0.644. Serving a per-roof ranking would sell a capability
   that does not exist. See ``docs/CANONICAL_NUMBERS.md``.

2. **There is no risk-score to loss-percent mapping any more.** ``RISK_TO_LOSS_PCT = 8.0``
   multiplied a calibrated *classification probability* by 8 and was removed outright.
   Nothing here reconstructs it: callers pass a loss percentage or accept the documented
   regional default, and the response says which happened.

3. **The dollars survive what the modelling does not.** Annual loss cancels between the
   value of a clean and the recovery fraction's denominator, so the break-even answer is
   stable under labelling assumptions that move the recovery fraction eightfold. That is
   the quantity worth serving.

So this endpoint is pure arithmetic over sourced constants. It loads **no model weights**,
which is also why it stays available when the soiling registry does not resolve.

A note on recovery fractions
----------------------------
Any fraction reported here is the **dry-season planning scenario** from
``risk.economics`` (a mid-season clean against a 180-day dry season), not the *annual*
recovery fraction the paper brackets at 0.031–0.217. They have different denominators and
are not comparable. ``recovery_basis`` in the response names which one is in play, because
quoting one as the other is exactly the error this project has made before.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.risk import economics as E
from src.risk import rates as R

#: Scenario keys that represent actually doing something, in escalating cost order.
ACTION_SCENARIOS = ("rinse_service", "professional")

# ── which recovery fraction to pair with the caller's loss ───────────────────────────
#
# THIS IS THE FIELD THAT DECIDES THE ANSWER, and the repo carries two values for it that
# differ by a factor of ten. Both describe "the share of a year's soiling loss that one
# wash gets back", so pairing the wrong one with a loss percentage silently moves the
# verdict.
#
#   measured (default)  professional 0.045, rinse 0.032. The empirically grounded pair:
#                       docs/ECONOMICS_GROUNDING_20260809.md, and what the live BBF
#                       breakeven calculator ships. Corroborated independently by the
#                       paper, which measures 0.0634 (median, half-norm basis) from 505
#                       observed cleaning events on 149 metered California systems.
#
#   seasonal_planning   professional 0.445, rinse 0.346, from DRY_SEASON_RESET_RECOVERY:
#                       a MODELLED April-September scenario assuming a perfect early-July
#                       reset. It is the share of DRY-SEASON cost avoided, ~10x the
#                       measured pair, and the optimistic one. It was the library default
#                       from 2026-09-04 to 2026-09-24 and that was a regression; it is
#                       kept only so work done in that window stays reproducible.
#
# Measured on the paper's own median metered system (5.72 kW, 9.53% annual loss, retail
# rate), seasonal_planning returns "clean it, +$47.80" where the paper's measured basis
# returns a break-even tariff of $2.44/kWh and no action that pays. The default is
# therefore the measured pair: an API must not contradict the project's own measurement.
MEASURED_RECOVERY = {"professional": 0.045, "rinse_service": 0.032}

#: The modelled alternative, derived explicitly rather than read from DEFAULT_SCENARIOS.
#: Reading the module default was a latent trap: when the default was restored to the
#: measured pair on 2026-09-24 this basis silently became identical to `measured`, and
#: only a test comparing the two caught it. Deriving it here keeps the contrast real.
SEASONAL_PLANNING_RECOVERY = {
    "professional": E.DRY_SEASON_RESET_RECOVERY * E.PROFESSIONAL_CLEAN_EFFICACY,
    "rinse_service": E.DRY_SEASON_RESET_RECOVERY * E.RINSE_CLEAN_EFFICACY,
}
RECOVERY_BASES = ("measured", "seasonal_planning")


def scenarios_for(basis: str, recovery_frac: float | None = None) -> dict[str, dict]:
    """Scenario table for a recovery basis, leaving cost functions untouched.

    ``recovery_frac`` overrides the basis with a caller-supplied value, which is how a
    caller who has *measured* recovery on a specific roof gets an answer specific to it
    rather than to a regional scalar. Supplying it reproduces the paper's per-system
    figures exactly: fed each of its 149 metered systems' own recovery, this returns the
    paper's $2.44/kWh median break-even to the cent.

    The rinse scenario is scaled by the ratio of removal efficacies (70/90), since an
    observed recovery is almost always from a full professional clean.
    """
    if basis not in RECOVERY_BASES:
        raise DecisionError(f"Unknown recovery_basis {basis!r}. Known: {list(RECOVERY_BASES)}")
    if recovery_frac is not None:
        if not 0.0 < recovery_frac <= 1.0:
            raise DecisionError("recovery_frac must be in (0, 1]")
        ratio = E.RINSE_CLEAN_EFFICACY / E.PROFESSIONAL_CLEAN_EFFICACY
        overrides = {"professional": recovery_frac, "rinse_service": recovery_frac * ratio}
    elif basis == "seasonal_planning":
        overrides = SEASONAL_PLANNING_RECOVERY
    else:
        overrides = MEASURED_RECOVERY
    return {
        key: {**scen, "recovery_frac": overrides.get(key, scen["recovery_frac"])}
        for key, scen in E.DEFAULT_SCENARIOS.items()
    }

#: Returned on every response. These are the limits a caller has to know to use the
#: number responsibly, and they travel with the payload rather than living in a doc.
STANDING_LIMITATIONS = (
    "Estimates soiling LEVEL, not a ranking between roofs. Public soiling labels are "
    "measured at weather stations, so per-roof ordering is not supported.",
    "Recoverable (washable) soiling only. A permanent soiling layer is excluded by "
    "construction from the labels this chain is grounded in, and is not measured here.",
    "Grounded on coastal California. The underlying model does not generalise to an "
    "unseen region (pooled out-of-region AUC 0.655, largest region 0.527).",
    "MIN_PRO_SERVICE ($150) is UNSOURCED and decides most residential cases on its own. "
    "Get local quotes before treating a verdict as final.",
)


class DecisionError(ValueError):
    """Caller supplied an unusable combination of inputs."""


@dataclass
class DecisionInputs:
    """Resolved, validated inputs plus a record of how each was obtained.

    ``provenance`` exists because the most dangerous field here is a default the caller
    did not realise they accepted. A verdict computed on ``BASE_SOILING_PCT`` is a very
    different claim from one computed on a measured loss, and the response says which.
    """

    system_kw: float
    loss_pct: float
    sun_hours: float
    elec_rate: float
    regime: str | None
    loss_pct_p10: float | None = None
    loss_pct_p90: float | None = None
    recovery_basis: str = "measured"
    recovery_frac: float | None = None
    provenance: dict[str, str] = field(default_factory=dict)


def resolve_inputs(
    *,
    system_kw: float | None = None,
    area_m2: float | None = None,
    loss_pct: float | None = None,
    loss_pct_p10: float | None = None,
    loss_pct_p90: float | None = None,
    sun_hours: float | None = None,
    elec_rate: float | None = None,
    regime: str | None = None,
    install_date: str | None = None,
    recovery_basis: str = "measured",
    recovery_frac: float | None = None,
) -> DecisionInputs:
    """Normalise the several ways a caller can describe a roof into one input set.

    Raises :class:`DecisionError` with a message naming the offending field, so the API
    can return a 422 that tells the caller what to change.
    """
    prov: dict[str, str] = {}

    # ---- system size -------------------------------------------------------------
    if system_kw is not None and area_m2 is not None:
        raise DecisionError("Pass system_kw or area_m2, not both — they would disagree.")
    if system_kw is not None:
        if system_kw <= 0:
            raise DecisionError("system_kw must be > 0")
        prov["system_kw"] = "caller"
    elif area_m2 is not None:
        if area_m2 <= 0:
            raise DecisionError("area_m2 must be > 0")
        system_kw = E.system_kw_from_area(area_m2)
        if not system_kw:
            raise DecisionError("area_m2 did not yield a usable system size")
        # Detected polygon area is the ENVELOPE. At 21cm one house often resolves into
        # several polygons, so a caller passing one fragment gets a fragment's verdict.
        prov["system_kw"] = (
            f"derived from area_m2={area_m2:g} via PACKING_FACTOR; pass the SITE's summed "
            "area, not one polygon, or the minimum service charge is levied per fragment"
        )
    else:
        raise DecisionError("One of system_kw or area_m2 is required.")

    # ---- electricity value -------------------------------------------------------
    if elec_rate is not None and (regime or install_date):
        raise DecisionError("Pass elec_rate, or regime/install_date, not both.")
    if elec_rate is not None:
        if elec_rate <= 0:
            raise DecisionError("elec_rate must be > 0")
        resolved_regime = None
        prov["elec_rate"] = "caller"
    elif install_date:
        rate, source = R.marginal_value_for_site(install_date)
        elec_rate, resolved_regime = rate, R.regime_for_install_date(install_date)
        prov["elec_rate"] = f"{source} (install_date={install_date})"
    else:
        resolved_regime = regime or R.DEFAULT_REGIME
        if resolved_regime not in R.REGIMES:
            raise DecisionError(
                f"Unknown regime {resolved_regime!r}. Known: {sorted(R.REGIMES)}"
            )
        _, elec_rate, _ = R.regime_value_band(resolved_regime)
        prov["elec_rate"] = (
            f"regime:{resolved_regime}"
            + (" (default — a NEM 2.0 legacy home is worth ~2.8x more per lost kWh)"
               if not regime else "")
        )

    # ---- soiling loss ------------------------------------------------------------
    if loss_pct is None:
        loss_pct = E.BASE_SOILING_PCT
        prov["loss_pct"] = (
            f"BASE_SOILING_PCT default ({E.BASE_SOILING_PCT}%) — a coastal-California "
            "regional level, NOT a measurement of this roof"
        )
    else:
        if loss_pct < 0:
            raise DecisionError("loss_pct must be >= 0")
        prov["loss_pct"] = "caller"
    if (loss_pct_p10 is None) != (loss_pct_p90 is None):
        raise DecisionError("Pass both loss_pct_p10 and loss_pct_p90, or neither.")
    if loss_pct_p10 is not None and not (loss_pct_p10 <= loss_pct <= loss_pct_p90):
        raise DecisionError("Require loss_pct_p10 <= loss_pct <= loss_pct_p90.")

    if sun_hours is None:
        sun_hours = E.BASE_SUN
        prov["sun_hours"] = f"BASE_SUN default ({E.BASE_SUN})"
    elif sun_hours <= 0:
        raise DecisionError("sun_hours must be > 0")
    else:
        prov["sun_hours"] = "caller"

    if recovery_basis not in RECOVERY_BASES:
        raise DecisionError(
            f"Unknown recovery_basis {recovery_basis!r}. Known: {list(RECOVERY_BASES)}"
        )
    if recovery_frac is not None:
        if not 0.0 < recovery_frac <= 1.0:
            raise DecisionError("recovery_frac must be in (0, 1]")
        prov["recovery_basis"] = (
            f"caller-supplied recovery_frac={recovery_frac:g}, overriding the "
            f"{recovery_basis!r} default — the most specific basis available"
        )
    else:
        prov["recovery_basis"] = (
            "measured (ECONOMICS_GROUNDING; corroborated by the paper at 0.0634 median over "
            "505 observed cleans)" if recovery_basis == "measured"
            else "seasonal_planning — MODELLED, ~10x the measured pair and the optimistic one"
        )

    return DecisionInputs(
        system_kw=float(system_kw),
        loss_pct=float(loss_pct),
        sun_hours=float(sun_hours),
        elec_rate=float(elec_rate),
        regime=resolved_regime,
        loss_pct_p10=loss_pct_p10,
        loss_pct_p90=loss_pct_p90,
        recovery_basis=recovery_basis,
        recovery_frac=recovery_frac,
        provenance=prov,
    )


def breakeven_tariff_usd_per_kwh(inp: DecisionInputs, scenario: str) -> float | None:
    """The electricity price at which this scenario would just pay for itself.

    ``annual_loss_usd`` is linear in ``elec_rate`` and the service cost is not a function
    of it, so the break-even price is a closed form rather than a search:
    ``rate * cost / recovered``. Returns ``None`` when nothing is recovered (no loss, or
    a zero-recovery scenario), because then no finite price makes it pay.
    """
    scen = scenarios_for(inp.recovery_basis, inp.recovery_frac)[scenario]
    loss = E.annual_loss_usd(
        inp.system_kw, inp.sun_hours, inp.loss_pct / 100.0, inp.elec_rate
    )
    recovered, cost, _ = E.scenario_net(loss, scen, inp.system_kw)
    if recovered <= 0:
        return None
    return round(inp.elec_rate * cost / recovered, 4)


def _thresholds(inp: DecisionInputs) -> dict[str, Any]:
    """What would have to be true for cleaning to pay, per action.

    This is the half of the answer a bare verdict throws away. "No" is not actionable;
    "no, and you would need $2.44/kWh or a 34 kW array" tells the caller how far outside
    the paying region they are, which is the paper's break-even surface in scalar form.
    """
    out: dict[str, Any] = {}
    for key in ACTION_SCENARIOS:
        scen = scenarios_for(inp.recovery_basis, inp.recovery_frac)[key]
        out[key] = {
            "breakeven_tariff_usd_per_kwh": breakeven_tariff_usd_per_kwh(inp, key),
            "breakeven_system_kw": E.breakeven_system_kw(
                scen, inp.sun_hours, inp.loss_pct / 100.0, inp.elec_rate
            ),
            "breakeven_soiling_pct": E.breakeven_soiling_pct(
                scen, inp.system_kw, inp.sun_hours, inp.elec_rate
            ),
        }
    return out


def decide(inp: DecisionInputs, *, n_samples: int = 2000, seed: int = 42) -> dict[str, Any]:
    """Full decision payload: verdict, dollars, uncertainty, thresholds, limits.

    The Monte Carlo propagates loss-percent interval, area (1-sigma 0.26, the measured
    21cm-vs-60cm shift), rate band and recovery timing. ``decision_robust`` is the field
    that matters most: it says the verdict held across the draws, not merely at the point
    estimate.
    """
    scens = scenarios_for(inp.recovery_basis, inp.recovery_frac)
    unc = E.Uncertainty(
        loss_pct_p10=inp.loss_pct_p10,
        loss_pct_p90=inp.loss_pct_p90,
        rate_lo=None,
        rate_hi=None,
        # The MC's own recovery band must come from the SAME basis as the scenarios,
        # or the point estimate and the interval describe different worlds.
        recovery_lo_frac=scens["rinse_service"]["recovery_frac"],
        recovery_hi_frac=scens["professional"]["recovery_frac"],
    )
    mc = E.array_recommendation_mc(
        loss_pct=inp.loss_pct,
        system_kw=inp.system_kw,
        sun_hours=inp.sun_hours,
        elec_rate=inp.elec_rate,
        scenarios=scens,
        unc=unc,
        n_samples=n_samples,
        seed=seed,
        regime=inp.regime or R.DEFAULT_REGIME,
    )

    per_scenario = mc.get("per_scenario", {})
    best_key, best_net = None, None
    for key in ACTION_SCENARIOS:
        net = per_scenario.get(key, {}).get("net_usd")
        if net is not None and (best_net is None or net > best_net):
            best_key, best_net = key, net

    return {
        "verdict": mc.get("recommended_action", "no_clean"),
        "worth_cleaning": bool(mc.get("worth_cleaning", False)),
        "annual_loss_usd": mc.get("annual_loss_usd"),
        "best_action": best_key,
        "best_action_net_usd": best_net,
        "per_scenario": per_scenario,
        "thresholds": _thresholds(inp),
        "uncertainty": {
            "prob_net_positive": mc.get("prob_net_positive"),
            "prob_any_action_positive": mc.get("prob_any_action_positive"),
            "decision_robust": mc.get("decision_robust"),
            "expected_net_usd_p10": mc.get("expected_net_usd_p10"),
            "expected_net_usd_p50": mc.get("expected_net_usd_p50"),
            "expected_net_usd_p90": mc.get("expected_net_usd_p90"),
            "loss_pct_p10": mc.get("loss_pct_p10"),
            "loss_pct_p90": mc.get("loss_pct_p90"),
            "rate_band_usd_per_kwh": mc.get("rate_band_usd_per_kwh"),
            "mc_n_samples": mc.get("mc_n_samples"),
            "seed": seed,
        },
        "inputs": {
            "system_kw": round(inp.system_kw, 3),
            "loss_pct": inp.loss_pct,
            "sun_hours": inp.sun_hours,
            "elec_rate_usd_per_kwh": round(inp.elec_rate, 4),
            "regime": inp.regime,
            "recovery_basis": inp.recovery_basis,
            "recovery_frac": inp.recovery_frac,
        },
        "provenance": inp.provenance,
        # The single field most able to move the verdict, so it is stated, not implied.
        "recovery_basis": (
            "measured: 0.045 professional / 0.032 rinse, the share of a YEAR's soiling "
            "loss one wash recovers (ECONOMICS_GROUNDING; the paper measures 0.0634 "
            "median over 505 observed cleans and corroborates it). Small because rain "
            "already resets the array ~27 times a year here."
            if inp.recovery_basis == "measured" else
            "seasonal_planning: 0.445 professional / 0.346 rinse, a MODELLED "
            "April-September scenario assuming a perfect early-July reset. This is the "
            "share of DRY-SEASON cost avoided, not of annual cost, so pairing it with an "
            "annual loss overstates recovery ~10x; it can return 'clean it' where the "
            "measured basis does not. Kept only to reproduce work done while it was "
            "wrongly the library default (2026-09-04 to 2026-09-24)."
        ),
        "recovery_band": mc.get("recovery_band"),
        "unsourced_constants": ["PACKING_FACTOR", "MIN_PRO_SERVICE", "per-panel rate schedule"],
        "limitations": list(STANDING_LIMITATIONS),
    }
