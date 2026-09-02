"""Tests for src/risk/site_cluster.py — polygon -> site grouping before costing."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

gpd = pytest.importorskip("geopandas")
from shapely.geometry import Polygon, box  # noqa: E402

from risk.site_cluster import (  # noqa: E402
    DEFAULT_MAX_GAP_M, aggregate_to_sites, assign_sites, assign_sites_by_proximity,
    cluster_diagnostics,
)

# EPSG:3310 is metres, so squares built here have literal metre dimensions.
CRS = "EPSG:3310"


def _squares(specs):
    """specs: list of (x, y, size). Returns a GeoDataFrame with area_m2."""
    geoms = [box(x, y, x + s, y + s) for x, y, s in specs]
    g = gpd.GeoDataFrame({"array_id": range(len(geoms))}, geometry=geoms, crs=CRS)
    g["area_m2"] = g.geometry.area
    return g


def test_adjacent_roof_blocks_merge_into_one_site():
    # two 4x4 m blocks separated by a 1 m ridge gap -> one roof
    g = _squares([(0, 0, 4), (5, 0, 4)])
    sites = assign_sites_by_proximity(g, max_gap_m=DEFAULT_MAX_GAP_M)
    assert sites.nunique() == 1


def test_neighbouring_houses_stay_separate():
    # 8 m apart — wider than any setback, must not merge
    g = _squares([(0, 0, 4), (12, 0, 4)])
    sites = assign_sites_by_proximity(g, max_gap_m=DEFAULT_MAX_GAP_M)
    assert sites.nunique() == 2


def test_merging_is_transitive_across_a_chain():
    # A-B and B-C are each within the gap; A-C is not. All three are one roof.
    g = _squares([(0, 0, 4), (5, 0, 4), (10, 0, 4)])
    sites = assign_sites_by_proximity(g, max_gap_m=DEFAULT_MAX_GAP_M)
    assert sites.nunique() == 1


def test_parcel_join_beats_proximity_and_falls_back():
    # Two blocks 8 m apart but on the SAME parcel -> one site (proximity would split).
    g = _squares([(0, 0, 4), (12, 0, 4), (500, 500, 4)])
    parcels = gpd.GeoDataFrame(
        {"APN": ["001-002-03"]},
        geometry=[box(-5, -5, 25, 25)],
        crs=CRS,
    )
    out = assign_sites(g, parcels=parcels)
    assert out.loc[0, "site_id"] == out.loc[1, "site_id"] == "apn:001-002-03"
    assert out.loc[0, "site_source"] == "parcel"
    # the far polygon is outside every parcel -> proximity fallback, distinct site
    assert out.loc[2, "site_source"] == "proximity"
    assert out.loc[2, "site_id"] != out.loc[0, "site_id"]


def test_representative_point_is_used_for_concave_shapes():
    """A C-shaped polygon's centroid can fall outside it; the join must not miss."""
    c_shape = Polygon([(0, 0), (10, 0), (10, 3), (3, 3), (3, 7), (10, 7),
                       (10, 10), (0, 10)])
    g = gpd.GeoDataFrame({"array_id": [0]}, geometry=[c_shape], crs=CRS)
    g["area_m2"] = g.geometry.area
    parcels = gpd.GeoDataFrame({"APN": ["X"]}, geometry=[box(-1, -1, 11, 11)], crs=CRS)
    out = assign_sites(g, parcels=parcels)
    assert out.loc[0, "site_id"] == "apn:X"


def test_aggregate_sums_area_and_area_weights_loss():
    g = _squares([(0, 0, 10), (11, 0, 2)])     # 100 m2 and 4 m2
    g["site_id"] = "s1"
    g["loss_pct_p50"] = [5.0, 1.0]
    agg = aggregate_to_sites(g)
    assert len(agg) == 1
    assert agg.loc[0, "area_m2"] == pytest.approx(104.0)
    assert agg.loc[0, "n_polygons"] == 2
    # area-weighted, so dominated by the 100 m2 block, not the midpoint 3.0
    assert agg.loc[0, "loss_pct_p50"] == pytest.approx((5 * 100 + 1 * 4) / 104)


def test_costing_a_site_once_beats_costing_each_fragment():
    """The bug this module exists to fix, asserted in dollars."""
    from risk.economics import DEFAULT_SCENARIOS, professional_cost, system_kw_from_area

    fragments = [30.0, 25.0, 20.0, 15.0]          # m2, one roof split four ways
    per_fragment = sum(professional_cost(system_kw_from_area(a)) for a in fragments)
    per_site = professional_cost(system_kw_from_area(sum(fragments)))
    # Each fragment is small enough to hit MIN_PRO_SERVICE, so the fragmented cost is
    # 4 x $150 = $600, while the 90 m2 site (~17.8 kW, 40 panels) prices off the bulk
    # schedule at ~$264 — a 2.3x overcharge for the same single truck roll.
    assert per_fragment == pytest.approx(600.0)
    assert per_fragment / per_site > 2.0
    assert DEFAULT_SCENARIOS["professional"]["cost_fn"](
        system_kw_from_area(sum(fragments))) == per_site


def test_diagnostics_report_fragmentation():
    g = _squares([(0, 0, 4), (5, 0, 4), (100, 0, 4)])
    g["site_id"] = assign_sites_by_proximity(g)
    g["site_source"] = "proximity"
    d = cluster_diagnostics(g)
    assert d["n_polygons"] == 3
    assert d["n_sites"] == 2
    assert d["fragmentation_factor"] == pytest.approx(1.5)
    assert d["polygons_per_site_max"] == 2
