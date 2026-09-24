"""Regression tests for the raw-3DEP canopy proxy used by the Persistent Soiling screen."""

import numpy as np

from scripts.analyze.rank_moss_candidates import candidate_canopy_mask, supported_canopy_mask


def test_canopy_proxy_handles_unclassified_multi_return_3dep_data():
    points = np.array(
        [(1, 2), (1, 1), (3, 1), (2, 3)],
        dtype=[("cls", "u1"), ("nret", "u1")],
    ).view(np.recarray)
    mask = candidate_canopy_mask(points, np.array([3.0, 3.0, 3.0, 3.0]))

    # Santa Cruz encodes trees as class 1, so elevated multi-return points must
    # remain visible; a labelled vegetation return is also retained. Ground never is.
    assert mask.tolist() == [True, False, True, False]


def test_canopy_proxy_requires_height_above_the_panel_plane():
    points = np.array([(1, 3)], dtype=[("cls", "u1"), ("nret", "u1")]).view(np.recarray)
    assert candidate_canopy_mask(points, np.array([2.5])).tolist() == [False]


def test_canopy_proxy_requires_a_supported_multi_square_metre_footprint():
    points = np.array(
        # Three candidate returns in each of three adjacent 1m cells.
        [(0.1, 0.1), (0.2, 0.2), (0.3, 0.3),
         (1.1, 0.1), (1.2, 0.2), (1.3, 0.3),
         (2.1, 0.1), (2.2, 0.2), (2.3, 0.3),
         # One isolated candidate must not create a zero-distance tree.
         (8.1, 8.1)],
        dtype=[("x", "f8"), ("y", "f8")],
    ).view(np.recarray)
    mask = supported_canopy_mask(
        points, np.ones(len(points), dtype=bool), mercator_to_true_m=1.0
    )

    assert mask.tolist() == [True] * 9 + [False]
