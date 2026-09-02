"""v1 cleaning-recommendation engine.

Implements the rule-based recommend stage from
``docs/PRODUCT_VISION.md`` ("Cleaning recommendation engine — staged").
Returns a payload that matches the v0 beta ``/recommend`` API contract.

v1 is intentionally simple: a hard-coded rule over (risk_score, 7-day rain
forecast, days_since_clean). v2 will swap in an ML-based recovery model
once the feedback loop has populated training rows.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

import geopandas as gpd

from solarsoiled.manifest import write_manifest

try:  # shared economics core (pure-stdlib); tolerate either import convention
    from risk.economics import (
        BASE_RATE, BASE_SOILING_PCT, BASE_SUN, Uncertainty, array_recommendation,
        sun_hours_from_poa_rel,
        array_recommendation_mc, system_kw_from_area,
    )
    from risk.site_cluster import assign_sites
except ImportError:  # pragma: no cover
    from src.risk.economics import (
        BASE_RATE, BASE_SOILING_PCT, BASE_SUN, Uncertainty, array_recommendation,
        sun_hours_from_poa_rel,
        array_recommendation_mc, system_kw_from_area,
    )
    from src.risk.site_cluster import assign_sites

logger = logging.getLogger(__name__)

# RISK_TO_LOSS_PCT was REMOVED on 2026-08-09. It multiplied a calibrated classification
# probability by 8 to manufacture a loss percentage — a category error that also
# collapsed the predicted spread to ~0.6 points against a measured 5.6. There is no
# risk_score -> loss_pct fallback any more, deliberately: an array with no predicted loss
# now falls back to the measured coastal-CA median (BASE_SOILING_PCT) and is flagged
# `loss_pct_source="base_rate_fallback"`, so a missing model is visible rather than
# silently imputed from the wrong quantity. See src/risk/loss_model.py.
_LOSS_FALLBACK_PCT = BASE_SOILING_PCT


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


# Recovery ranges calibrated against UCSD 2013 soiling study and TechLabs 2023
# findings: one cleaning/year recovers ~1.5-3% for typical residential arrays;
# exception cases (agriculture/highway proximity, low tilt, bird activity) reach 3-7%.
_RECOVERY_BUCKETS: tuple[tuple[float, str, tuple[float, float]], ...] = (
    (0.50, "low",    (1.0, 2.5)),
    (0.75, "medium", (2.0, 4.0)),
    (1.01, "high",   (3.0, 7.0)),
)

# Exception thresholds that escalate a recommendation to professional cleaning.
_EXCEPTION_DISTANCE_AGRICULTURE_M = 500.0
_EXCEPTION_DISTANCE_HIGHWAY_M = 200.0
_EXCEPTION_TILT_DEG = 5.0       # panels at or below this angle don't self-clean in rain
_EXCEPTION_SYSTEM_KW = 8.0      # above this size professional ROI is more likely


def _cleaning_method(
    risk: float,
    tilt_deg: float | None = None,
    dist_agriculture_m: float | None = None,
    dist_highway_m: float | None = None,
    area_m2: float | None = None,
    system_kw: float | None = None,
) -> tuple[str, str]:
    """Return (method, rationale) for the cleaning recommendation.

    "diy"          — garden hose, 15 min, no cost; appropriate for most residential
    "professional" — worth hiring a service; appropriate for large systems or
                     arrays with exception-level environmental exposure
    """
    # Prefer the caller's SITE kW; fall back to this polygon's own area only when no
    # site grouping was supplied. M2_PER_KW is imported, not redefined — the local 5.67
    # copy here had already drifted from the shared constant.
    if system_kw is None:
        system_kw = system_kw_from_area(area_m2)

    exceptions = []
    if tilt_deg is not None and tilt_deg <= _EXCEPTION_TILT_DEG:
        exceptions.append("low tilt — rain doesn't self-clean")
    if dist_agriculture_m is not None and dist_agriculture_m < _EXCEPTION_DISTANCE_AGRICULTURE_M:
        exceptions.append("near agricultural fields")
    if dist_highway_m is not None and dist_highway_m < _EXCEPTION_DISTANCE_HIGHWAY_M:
        exceptions.append("near highway")
    if system_kw is not None and system_kw >= _EXCEPTION_SYSTEM_KW:
        exceptions.append(f"large system ({system_kw:.1f} kW)")
    if risk >= 0.75:
        exceptions.append("high soiling risk score")

    if exceptions:
        return "professional", "; ".join(exceptions)
    return "diy", "typical residential — rinse with garden hose on a cool morning"


def _bucket(risk: float) -> tuple[str, tuple[float, float]]:
    for upper, name, recovery in _RECOVERY_BUCKETS:
        if risk < upper:
            return name, recovery
    return "high", _RECOVERY_BUCKETS[-1][2]


def _aggregate_risk(risks: Iterable[float]) -> float:
    """AOI-level risk = 90th percentile of per-array risk (ceiling rank)."""
    vals = sorted(float(r) for r in risks if r is not None)
    if not vals:
        return 0.0
    idx = min(len(vals) - 1, int(0.9 * len(vals)))
    return vals[idx]


def _default_forecast_fn(lat: float, lon: float, days: int) -> list[float]:
    """Hit Open-Meteo /forecast for daily precipitation_sum (mm).

    Caller-injectable so unit tests don't touch the network.
    """
    import requests

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum",
        "forecast_days": int(days),
        "timezone": "UTC",
    }
    resp = requests.get(FORECAST_URL, params=params, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    return [float(v or 0.0) for v in payload["daily"]["precipitation_sum"]]


def _longest_dry_stretch_end(
    rain_per_day_mm: list[float],
    start: date,
    *,
    daily_threshold_mm: float,
) -> date | None:
    """Return the last date of the longest run of dry days, or None."""
    best_len = 0
    best_end_idx: int | None = None
    cur_len = 0
    cur_start: int | None = None
    for i, r in enumerate(rain_per_day_mm):
        if r < daily_threshold_mm:
            if cur_start is None:
                cur_start = i
            cur_len = i - cur_start + 1
            if cur_len > best_len:
                best_len = cur_len
                best_end_idx = i
        else:
            cur_len = 0
            cur_start = None
    if best_end_idx is None:
        return None
    return start + timedelta(days=best_end_idx)


def recommend_cleaning(
    risk_geojson: Path,
    last_cleaned: date,
    aoi_centroid: tuple[float, float],
    *,
    risk_threshold: float = 0.6,
    rain_mm_threshold: float = 5.0,
    min_days_since_clean: int = 150,
    forecast_days: int = 7,
    forecast_fn: Callable[[float, float, int], list[float]] | None = None,
    today: date | None = None,
) -> dict:
    """Return a recommendation payload matching the v0 beta /recommend contract.

    ``risk_geojson`` is the output of script 11 (``risk.geojson``), one feature
    per detected array with a ``risk_score`` property. ``aoi_centroid`` is
    ``(lat, lon)`` in WGS84. ``forecast_fn`` is injectable for tests.
    """
    today = today or datetime.utcnow().date()
    forecast_fn = forecast_fn or _default_forecast_fn

    gdf = gpd.read_file(risk_geojson)
    if "risk_score" not in gdf.columns:
        raise ValueError(f"{risk_geojson} has no risk_score column")
    aoi_risk = _aggregate_risk(gdf["risk_score"].tolist())
    confidence, recovery_range = _bucket(aoi_risk)

    days_since_clean = (today - last_cleaned).days
    rain_forecast = forecast_fn(aoi_centroid[0], aoi_centroid[1], forecast_days)
    total_rain_mm = sum(rain_forecast)
    dry_end = _longest_dry_stretch_end(
        rain_forecast,
        start=today,
        daily_threshold_mm=rain_mm_threshold / max(forecast_days, 1),
    )

    rule_fired = "below_risk_threshold"
    window_start: date | None = None
    window_end: date | None = None

    if aoi_risk < risk_threshold:
        rule_fired = "below_risk_threshold"
    elif days_since_clean < min_days_since_clean:
        rule_fired = "below_age_threshold"
    elif total_rain_mm >= rain_mm_threshold:
        rule_fired = "deferred_due_to_rain"
    else:
        rule_fired = "weather_window_open"
        window_start = today + timedelta(days=1)
        window_end = dry_end if dry_end and dry_end > window_start else (today + timedelta(days=forecast_days))

    # AOI-level method uses aggregate risk only (no per-array feature access here)
    method, method_rationale = _cleaning_method(risk=aoi_risk)

    return {
        "window_start": window_start.isoformat() if window_start else None,
        "window_end": window_end.isoformat() if window_end else None,
        "expected_recovery_pct": list(recovery_range),
        "confidence": confidence,
        "cleaning_method": method,
        "cleaning_method_rationale": method_rationale,
        "rule_fired": rule_fired,
        "model_version": "recommend-v1-rule",
        "beta": True,
        "known_limitations": [
            "Rule-based v1; expected_recovery_pct calibrated from UCSD 2013 study (1-7% per cleaning)",
            "Single AOI-centroid forecast (no spatial aggregation across arrays)",
            "cleaning_method uses AOI-level risk only; per-array method uses local features",
        ],
        "inputs": {
            "aoi_risk_p90": float(aoi_risk),
            "n_arrays": int(len(gdf)),
            "days_since_clean": int(days_since_clean),
            "total_rain_7d_mm": float(total_rain_mm),
            "last_cleaned": last_cleaned.isoformat(),
            "today": today.isoformat(),
        },
    }


def _site_economics(
    gdf,
    *,
    sun_hours: float,
    elec_rate: float,
    parcels=None,
    monte_carlo: bool = True,
) -> tuple[dict, dict]:
    """Cost each SITE once, not each polygon. Returns (per_array_econ, per_site_econ).

    At 21cm one house resolves into several polygons (measured 1.80 polygons/site on the
    Santa Cruz AOI, up to 22 on one parcel). Costing each polygon separately levies the
    ``MIN_PRO_SERVICE`` trip charge once per fragment and sizes each fragment from its
    own small area, so it overstates cost and understates the bulk discount — both
    pushing toward ``no_clean``. Grouping to parcels fixes both.

    Each array row carries its SITE's economics, plus ``site_primary`` on the largest
    polygon of the site so a caller can sum over primaries without double-counting.
    """
    import pandas as pd

    gdf = assign_sites(gdf, parcels=parcels)
    per_array: dict = {}
    per_site: dict = {}

    for site_id, grp in gdf.groupby("site_id", sort=False):
        area = pd.to_numeric(grp.get("area_m2"), errors="coerce").fillna(0.0)
        site_area = float(area.sum())
        w = area.to_numpy() if site_area > 0 else None

        permit = pd.to_numeric(grp.get("permit_kw"), errors="coerce") if "permit_kw" in grp else None
        permit_kw = float(permit.max()) if permit is not None and permit.notna().any() else None
        site_kw = permit_kw if permit_kw else system_kw_from_area(site_area)

        def _wavg(col: str):
            if col not in grp.columns:
                return None
            v = pd.to_numeric(grp[col], errors="coerce").to_numpy(dtype=float)
            import numpy as np
            ok = np.isfinite(v)
            if not ok.any():
                return None
            ww = w[ok] if w is not None else None
            return float(np.average(v[ok], weights=ww))

        p50 = _wavg("loss_pct_p50")
        if p50 is not None:
            loss_source = "regression_head"
            p10, p90 = _wavg("loss_pct_p10"), _wavg("loss_pct_p90")
        elif _wavg("soiling_loss_annual_pct") is not None:
            p50, loss_source = _wavg("soiling_loss_annual_pct"), "physics_somosclean"
            p10 = p90 = None
        else:
            p50, loss_source = _LOSS_FALLBACK_PCT, "base_rate_fallback"
            p10 = p90 = None

        # Measured roof orientation, area-weighted across the site's polygons. A gable
        # house genuinely has two orientations and pays one truck roll, so the site's
        # production is the area-weighted blend -- not the largest polygon's, and not a
        # median fill. Sites with no usable lidar fit keep the caller's flat `sun_hours`;
        # see sun_hours_from_poa_rel, which refuses to invent a value.
        site_poa_rel = _wavg("poa_rel")
        site_sun_hours = (sun_hours_from_poa_rel(site_poa_rel, sun_hours)
                          if site_poa_rel is not None else sun_hours)

        if not site_kw:
            econ = None
        elif monte_carlo:
            econ = array_recommendation_mc(
                p50, site_kw, site_sun_hours, elec_rate,
                unc=Uncertainty(loss_pct_p10=p10, loss_pct_p90=p90),
            )
        else:
            econ = array_recommendation(p50, site_kw, site_sun_hours, elec_rate)

        rec = {
            "site_id": site_id,
            "site_source": grp["site_source"].iloc[0],
            "site_n_polygons": int(len(grp)),
            "site_area_m2": round(site_area, 2),
            "system_kw": round(site_kw, 2) if site_kw else None,
            "system_kw_source": "permit" if permit_kw else ("area_estimate" if site_area else None),
            "loss_pct": round(p50, 2) if p50 is not None else None,
            "loss_pct_p10": round(p10, 2) if p10 is not None else None,
            "loss_pct_p90": round(p90, 2) if p90 is not None else None,
            "loss_pct_source": loss_source,
            "poa_rel": round(site_poa_rel, 4) if site_poa_rel is not None else None,
            "sun_hours": round(site_sun_hours, 3),
            "sun_hours_source": "lidar_orientation" if site_poa_rel is not None else "flat_ghi_fallback",
            "expected_net_usd": econ["expected_net_usd"] if econ else None,
            "economic_action": econ["recommended_action"] if econ else None,
            "roi": econ["roi"] if econ else None,
            "payback_years": econ["payback_years"] if econ else None,
            "worth_cleaning": econ["worth_cleaning"] if econ else None,
        }
        if econ and monte_carlo:
            rec.update({
                "expected_net_usd_p10": econ["expected_net_usd_p10"],
                "expected_net_usd_p90": econ["expected_net_usd_p90"],
                "prob_net_positive": econ["prob_net_positive"],
                "decision_robust": econ["decision_robust"],
            })
        per_site[site_id] = rec

        primary_idx = area.idxmax() if len(area) else None
        for idx in grp.index:
            per_array[idx] = {**rec, "site_primary": bool(idx == primary_idx)}

    return per_array, per_site


def recommend_per_array(
    risk_geojson: Path,
    aoi_recommendation: dict,
    *,
    risk_threshold: float = 0.6,
    sun_hours: float = BASE_SUN,
    elec_rate: float = BASE_RATE,
    parcels=None,
    monte_carlo: bool = True,
) -> list[dict]:
    """Return a list of per-array recommendation dicts.

    Uses the AOI-level weather window from aoi_recommendation but gates
    each array independently by its own risk_score.

    **Economics are computed per SITE** (parcel, else proximity cluster) and attached to
    every array on that site; ``site_primary`` marks one array per site so totals can be
    summed without double-counting. See :func:`_site_economics`.

    Each item: {array_id, risk_score, cleaning_window, priority, action}
      cleaning_window: "YYYY-MM-DD → YYYY-MM-DD" or None
      priority: "high" | "medium" | "low"
      action: "clean" | "monitor"
    """
    gdf = gpd.read_file(risk_geojson)
    econ_by_row, _ = _site_economics(gdf, sun_hours=sun_hours, elec_rate=elec_rate,
                                     parcels=parcels, monte_carlo=monte_carlo)

    window_start = aoi_recommendation.get("window_start")
    window_end = aoi_recommendation.get("window_end")
    rule_fired = aoi_recommendation.get("rule_fired", "")

    window_open = rule_fired == "weather_window_open" and window_start

    rows = []
    for row_idx, row in gdf.iterrows():
        score = float(row.get("risk_score") or 0.0)
        aid = int(row.get("array_id", -1))

        # Pull exception features from GeoDataFrame if present
        tilt = float(row["tilt_deg"]) if "tilt_deg" in gdf.columns and row.get("tilt_deg") is not None else None
        dist_ag = float(row["distance_to_agriculture_m"]) if "distance_to_agriculture_m" in gdf.columns and row.get("distance_to_agriculture_m") is not None else None
        dist_hw = float(row["distance_to_highway_m"]) if "distance_to_highway_m" in gdf.columns and row.get("distance_to_highway_m") is not None else None
        area = float(row["area_m2"]) if "area_m2" in gdf.columns and row.get("area_m2") is not None else None

        # Site-level dollars: the polygon's economics are its SITE's economics (one
        # truck roll per property), computed once in _site_economics above.
        econ_fields = econ_by_row.get(row_idx, {})

        # The "large system" exception must be judged on the SITE, not the fragment —
        # a 12 kW house split into five 21cm polygons would otherwise never trigger it.
        site_kw = econ_fields.get("system_kw")
        method, method_rationale = _cleaning_method(
            risk=score, tilt_deg=tilt, dist_agriculture_m=dist_ag,
            dist_highway_m=dist_hw, area_m2=area, system_kw=site_kw,
        )

        if not window_open or score < risk_threshold:
            priority = "low" if score < 0.5 else "medium"
            rows.append({
                "array_id": aid,
                "risk_score": round(score, 3),
                "cleaning_window": None,
                "cleaning_method": method,
                "cleaning_method_rationale": method_rationale,
                "priority": priority,
                "action": "monitor",
                "action_source": "risk_rule",
                "action_reason": ("weather window closed" if not window_open
                                  else f"risk_score {score:.3f} below threshold {risk_threshold}"),
                **econ_fields,
            })
        else:
            _, (recovery_lo, recovery_hi) = _bucket(score)
            # DOLLARS ARE AUTHORITATIVE (resolved 2026-08-09, Cameron).
            #
            # The v1 risk rule (risk_score >= threshold + open weather window) and the
            # net-$ engine disagree on almost every site after the economics grounding
            # pass: the rule says "clean", the dollars say the visit loses money. M2 is
            # "ship a $-based clean/wait call", and the product's job is to surface the
            # OUTLIERS where cleaning genuinely pays — so emitting action="clean" beside
            # expected_net_usd < 0 would be the product recommending against its own
            # thesis, thousands of times per AOI.
            #
            # The risk rule is retained as a NECESSARY condition (it encodes the weather
            # window — do not send a crew before rain) but is no longer SUFFICIENT.
            # `risk_score`, `priority` and `rule_fired` are still reported unchanged, so
            # the downgrade is auditable rather than silent.
            #
            # `worth_cleaning is None` (no system_kw, so no economics) falls through to
            # the risk rule: absence of a dollar figure is not evidence against cleaning.
            worth = econ_fields.get("worth_cleaning")
            economically_blocked = worth is False
            priority = "high" if score >= 0.75 else "medium"
            row = {
                "array_id": aid,
                "risk_score": round(score, 3),
                "cleaning_window": None if economically_blocked else f"{window_start} → {window_end}",
                "cleaning_method": method,
                "cleaning_method_rationale": method_rationale,
                "priority": "low" if economically_blocked else priority,
                "action": "monitor" if economically_blocked else "clean",
                "action_source": "economics" if economically_blocked else "risk_rule+economics",
                "action_reason": (
                    "weather window open and risk above threshold, but no cleaning "
                    "strategy clears $0 at this site's size and predicted loss"
                    if economically_blocked else
                    "weather window open, risk above threshold, and net benefit positive"
                ),
                **econ_fields,
            }
            if not economically_blocked:
                row["expected_recovery_pct"] = [recovery_lo, recovery_hi]
            rows.append(row)

    # Surface the highest-value arrays first: clean actions, then net-$ desc, then risk.
    rows.sort(key=lambda r: (r["action"] != "clean", -(r.get("expected_net_usd") or 0.0), -r["risk_score"]))
    return rows


def write_array_recommendations(out_path: Path, rows: list[dict]) -> Path:
    """Write array_recommendations.json — list of per-array dicts."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return out_path


def write_recommendation(
    out_path: Path,
    payload: dict,
    *,
    upstream_manifest: Path | None = None,
) -> Path:
    """Write the recommend payload + sibling manifest.json."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    inputs: list[str] = []
    if upstream_manifest and Path(upstream_manifest).is_file():
        inputs.append(str(upstream_manifest))
    write_manifest(
        out_path.parent,
        stage="recommend",
        model_version=payload.get("model_version", "recommend-v1-rule"),
        model_weights=None,
        inputs=inputs,
        beta=bool(payload.get("beta", True)),
        metrics={
            "aoi_risk_p90": payload["inputs"]["aoi_risk_p90"],
            "n_arrays": payload["inputs"]["n_arrays"],
            "days_since_clean": payload["inputs"]["days_since_clean"],
            "total_rain_7d_mm": payload["inputs"]["total_rain_7d_mm"],
        },
        known_limitations=list(payload.get("known_limitations", [])),
        extra={
            "rule_fired": payload["rule_fired"],
            "recommendations_json": str(out_path),
        },
        filename="manifest.recommend.json",
    )
    return out_path
