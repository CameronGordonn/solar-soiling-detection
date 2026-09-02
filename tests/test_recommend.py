"""Unit tests for solarsoiled.recommend.

Network-free: ``forecast_fn`` is injected with deterministic test fixtures
so all five rule_fired branches are covered.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from solarsoiled.recommend import recommend_cleaning, write_recommendation

# last_cleaned date used by tests that need to pass the age gate (>150 days before today)
_LAST_CLEANED_OLD = date(2025, 11, 1)   # 180 days before 2026-04-30
_TODAY = date(2026, 4, 30)


def _write_risk_geojson(tmp_path: Path, scores: list[float]) -> Path:
    geoms = [Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)]) for i in range(len(scores))]
    gdf = gpd.GeoDataFrame({"risk_score": scores, "geometry": geoms}, crs="EPSG:4326")
    out = tmp_path / "risk.geojson"
    gdf.to_file(out, driver="GeoJSON")
    return out


def _fake_forecast(rain_pattern: list[float]):
    def _fn(lat, lon, days):
        return list(rain_pattern[:days]) + [0.0] * max(days - len(rain_pattern), 0)
    return _fn


def test_below_risk_threshold(tmp_path: Path):
    risk = _write_risk_geojson(tmp_path, [0.1, 0.2, 0.3])
    p = recommend_cleaning(
        risk_geojson=risk,
        last_cleaned=_LAST_CLEANED_OLD,
        aoi_centroid=(36.95, -122.05),
        forecast_fn=_fake_forecast([0.0] * 7),
        today=_TODAY,
    )
    assert p["rule_fired"] == "below_risk_threshold"
    assert p["window_start"] is None
    assert p["confidence"] == "low"


def test_below_age_threshold(tmp_path: Path):
    risk = _write_risk_geojson(tmp_path, [0.8, 0.9])
    p = recommend_cleaning(
        risk_geojson=risk,
        last_cleaned=date(2026, 4, 20),  # 10 days ago — well below 150-day threshold
        aoi_centroid=(36.95, -122.05),
        forecast_fn=_fake_forecast([0.0] * 7),
        today=_TODAY,
    )
    assert p["rule_fired"] == "below_age_threshold"
    assert p["window_start"] is None
    assert p["confidence"] == "high"


def test_deferred_due_to_rain(tmp_path: Path):
    risk = _write_risk_geojson(tmp_path, [0.7, 0.8])
    p = recommend_cleaning(
        risk_geojson=risk,
        last_cleaned=_LAST_CLEANED_OLD,
        aoi_centroid=(36.95, -122.05),
        forecast_fn=_fake_forecast([5.0, 5.0, 5.0, 0.0, 0.0, 0.0, 0.0]),  # 15mm total
        today=_TODAY,
    )
    assert p["rule_fired"] == "deferred_due_to_rain"
    assert p["window_start"] is None


def test_weather_window_open(tmp_path: Path):
    risk = _write_risk_geojson(tmp_path, [0.7, 0.8])
    p = recommend_cleaning(
        risk_geojson=risk,
        last_cleaned=_LAST_CLEANED_OLD,
        aoi_centroid=(36.95, -122.05),
        forecast_fn=_fake_forecast([0.0] * 7),
        today=_TODAY,
    )
    assert p["rule_fired"] == "weather_window_open"
    assert p["window_start"] == "2026-05-01"
    assert p["window_end"] == "2026-05-06"  # 0..6 indexed from today, end of 7-day stretch
    assert p["confidence"] == "high"
    assert p["expected_recovery_pct"] == [3.0, 7.0]  # high bucket, calibrated from UCSD study
    assert p["cleaning_method"] in ("diy", "professional")


def test_payload_shape_matches_contract(tmp_path: Path):
    risk = _write_risk_geojson(tmp_path, [0.7])
    p = recommend_cleaning(
        risk_geojson=risk,
        last_cleaned=_LAST_CLEANED_OLD,
        aoi_centroid=(36.95, -122.05),
        forecast_fn=_fake_forecast([0.0] * 7),
        today=_TODAY,
    )
    for key in (
        "window_start",
        "window_end",
        "expected_recovery_pct",
        "confidence",
        "cleaning_method",
        "cleaning_method_rationale",
        "rule_fired",
        "model_version",
        "beta",
        "known_limitations",
    ):
        assert key in p, f"missing key {key}"
    assert p["beta"] is True


def test_aggregate_uses_p90(tmp_path: Path):
    risk = _write_risk_geojson(tmp_path, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.95])
    # p90 of 10 values is the index 9 (0-indexed) — 0.95
    p = recommend_cleaning(
        risk_geojson=risk,
        last_cleaned=_LAST_CLEANED_OLD,
        aoi_centroid=(36.95, -122.05),
        forecast_fn=_fake_forecast([0.0] * 7),
        today=_TODAY,
    )
    assert p["inputs"]["aoi_risk_p90"] >= 0.9
    assert p["rule_fired"] == "weather_window_open"


def test_write_recommendation_round_trip(tmp_path: Path):
    risk = _write_risk_geojson(tmp_path, [0.7])
    payload = recommend_cleaning(
        risk_geojson=risk,
        last_cleaned=_LAST_CLEANED_OLD,
        aoi_centroid=(36.95, -122.05),
        forecast_fn=_fake_forecast([0.0] * 7),
        today=_TODAY,
    )
    out = write_recommendation(tmp_path / "recommendations.json", payload)
    on_disk = json.loads(out.read_text())
    assert on_disk["rule_fired"] == payload["rule_fired"]
    # Sibling manifest written alongside.
    assert (tmp_path / "manifest.recommend.json").is_file()


# ── economics authority over the v1 risk rule (resolved 2026-08-09) ───────────
def _write_sites_geojson(tmp_path: Path, specs: list[tuple[float, float, float]]) -> Path:
    """specs: (risk_score, area_m2, loss_pct_p50). Polygons are far apart = one site each."""
    import math

    rows, geoms = [], []
    for i, (score, area, loss) in enumerate(specs):
        # metres -> degrees, so area_m2 is roughly honoured and sites never merge
        side = math.sqrt(area) / 111_320.0
        x = i * 0.01
        geoms.append(Polygon([(x, 0), (x + side, 0), (x + side, side), (x, side)]))
        rows.append({"array_id": i, "risk_score": score, "area_m2": area,
                     "loss_pct_p50": loss, "loss_pct_p10": loss * 0.5,
                     "loss_pct_p90": loss * 1.5})
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    out = tmp_path / "risk_sites.geojson"
    gdf.to_file(out, driver="GeoJSON")
    return out


_OPEN_WINDOW = {"window_start": "2026-05-01", "window_end": "2026-05-07",
                "rule_fired": "weather_window_open"}


def test_high_risk_but_uneconomic_site_is_downgraded_to_monitor(tmp_path: Path):
    """The v1 risk rule alone would say "clean"; the dollars say no, and dollars win.

    Before 2026-08-09 this emitted action="clean" alongside expected_net_usd < 0 on
    essentially every site in the AOI — the product recommending against its own thesis.
    """
    from solarsoiled.recommend import recommend_per_array

    risk = _write_sites_geojson(tmp_path, [(0.95, 40.0, 4.7)])
    rows = recommend_per_array(risk, _OPEN_WINDOW)
    r = rows[0]
    assert r["risk_score"] == 0.95           # risk is reported unchanged...
    assert r["worth_cleaning"] is False
    assert r["action"] == "monitor"          # ...but does not drive the action
    assert r["action_source"] == "economics"
    assert r["cleaning_window"] is None
    assert "no cleaning strategy clears $0" in r["action_reason"]


def test_economically_positive_site_still_cleans(tmp_path: Path):
    """The downgrade must not be unconditional — an outlier that pays still says clean.

    This is the case the product exists to find, so it is asserted explicitly. A very
    large, very high-loss site under the legacy recovery clears $0.
    """
    from risk.economics import LEGACY_SCENARIOS
    from solarsoiled.recommend import _site_economics

    gdf = gpd.read_file(_write_sites_geojson(tmp_path, [(0.95, 4000.0, 20.0)]))
    _, per_site = _site_economics(gdf, sun_hours=5.5, elec_rate=0.4573,
                                  monte_carlo=False)
    rec = list(per_site.values())[0]
    assert rec["system_kw"] > 100
    # under the measured recovery even this does not pay — that is the C1 finding —
    # so the positive branch is exercised against the legacy scenarios.
    from risk.economics import array_recommendation
    legacy = array_recommendation(20.0, rec["system_kw"], 5.5, 0.4573, LEGACY_SCENARIOS)
    assert legacy["worth_cleaning"] is True


def test_low_risk_site_reports_the_risk_rule_as_its_source(tmp_path: Path):
    from solarsoiled.recommend import recommend_per_array

    risk = _write_sites_geojson(tmp_path, [(0.10, 40.0, 4.7)])
    rows = recommend_per_array(risk, _OPEN_WINDOW)
    assert rows[0]["action"] == "monitor"
    assert rows[0]["action_source"] == "risk_rule"
    assert "below threshold" in rows[0]["action_reason"]


def test_site_economics_charges_one_trip_per_parcel(tmp_path: Path):
    """Four fragments of one roof must not be billed four minimum service charges."""
    from solarsoiled.recommend import _site_economics

    gdf = gpd.read_file(_write_sites_geojson(
        tmp_path, [(0.8, 30.0, 4.7), (0.8, 25.0, 4.7), (0.8, 20.0, 4.7), (0.8, 15.0, 4.7)]))
    # force them onto one parcel
    parcels = gpd.GeoDataFrame(
        {"APN": ["001-001-01"]},
        geometry=[gdf.geometry.union_all().buffer(0.05)], crs="EPSG:4326")
    per_array, per_site = _site_economics(gdf, sun_hours=5.5, elec_rate=0.4573,
                                          parcels=parcels, monte_carlo=False)
    assert len(per_site) == 1
    site = list(per_site.values())[0]
    assert site["site_n_polygons"] == 4
    assert site["site_area_m2"] == pytest.approx(90.0, rel=0.02)
    # exactly one row is the site primary, so AOI totals never double-count
    assert sum(1 for v in per_array.values() if v["site_primary"]) == 1
