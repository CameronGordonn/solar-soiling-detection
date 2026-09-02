"""Regression tests for the Null Island bug.

This repo stores tile_index transforms in TWO orderings and nothing recorded which was which:

    data/interim/tile_index.json             GDAL   [c, a, b, f, d, e]
    .../scc21_labelset/tile_index_21cm.json  Affine [a, b, c, d, e, f]

Reading one with the other's convention does not raise. It relocates every detection to the
Gulf of Guinea, which is how the first end-to-end pilot run produced 109 polygons spanning
[-179.8, -0.0003, 179.6, 90.0] while reporting success. The failure is invisible to anything
that only counts detections, so it is pinned here rather than trusted to review.
"""

from __future__ import annotations

import pytest
from rasterio.transform import Affine

from src.utils.tile_metadata import affine_from_entry, ground_gsd_m


# Real entries, copied from the two indices on disk.
NAIP_GDAL = {"transform": [-13588070.4, 0.6, 0.0, 4432896.0, 0.0, -0.6]}
SCC_AFFINE = {"transform": [0.2557868442957993, 0.0, -13588992.0,
                            0.0, -0.25685618729112564, 4433510.399999999]}


def test_gdal_ordered_entry_resolves_to_its_recorded_origin():
    assert affine_from_entry(NAIP_GDAL) * (0, 0) == pytest.approx((-13588070.4, 4432896.0))


def test_affine_ordered_entry_resolves_to_its_recorded_origin():
    """The bug: from_gdal on this entry yields (0.2557, 0.0) -- Null Island."""
    assert affine_from_entry(SCC_AFFINE) * (0, 0) == pytest.approx((-13588992.0, 4433510.4))


def test_the_two_orderings_do_not_collide():
    """Both must resolve to Santa Cruz, not one of them to the Atlantic."""
    for entry in (NAIP_GDAL, SCC_AFFINE):
        x, y = affine_from_entry(entry) * (0, 0)
        assert -1.4e7 < x < -1.3e7, f"x={x} is not in Web Mercator Santa Cruz"
        assert 4.3e6 < y < 4.5e6, f"y={y} is not in Web Mercator Santa Cruz"


def test_explicit_order_overrides_inference():
    entry = {"transform": SCC_AFFINE["transform"], "transform_order": "affine"}
    assert affine_from_entry(entry) == Affine(*SCC_AFFINE["transform"])
    entry = {"transform": NAIP_GDAL["transform"], "transform_order": "gdal"}
    assert affine_from_entry(entry) == Affine.from_gdal(*NAIP_GDAL["transform"])


def test_unknown_explicit_order_raises():
    with pytest.raises(ValueError, match="unknown transform_order"):
        affine_from_entry({"transform": NAIP_GDAL["transform"], "transform_order": "xyzzy"})


def test_wrong_length_transform_raises():
    with pytest.raises(ValueError, match="6 elements"):
        affine_from_entry({"transform": [1, 2, 3]})


# --- ground GSD: the other units bug, from the same family --------------------------------

def test_mercator_inflation_is_undone():
    """transform.a in 3857 reads 0.2558; the ground truth is 0.2044. Quoting the affine
    directly overstates area by 1/cos(lat)^2 = 1.57x, straight into the dollar figure."""
    tf = Affine(0.2557868442957993, 0.0, -13588992.0, 0.0, -0.25685618729112564, 4433510.4)
    assert ground_gsd_m(tf, "EPSG:3857", center_y=4432742.0) == pytest.approx(0.2044, abs=1e-4)


def test_us_feet_are_not_metres():
    """EPSG:2227 is ftUS. Reading 0.20833 ft as metres is how a labeling sprint got run at
    21cm on a service that serves 6.3cm."""
    tf = Affine(0.20833, 0, 0, 0, -0.20833, 0)
    assert ground_gsd_m(tf, "EPSG:2227") == pytest.approx(0.0635, abs=1e-4)


def test_mercator_without_latitude_raises_rather_than_guessing():
    tf = Affine(0.2557868442957993, 0.0, -13588992.0, 0.0, -0.25685618729112564, 4433510.4)
    with pytest.raises(ValueError, match="center_y"):
        ground_gsd_m(tf, "EPSG:3857")
