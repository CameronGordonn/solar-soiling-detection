"""Guards against shipping a model onto imagery it was not trained for.

W5 audit item 1: `solarsoiled tile` fetches ~48cm NAIP and the 21cm RF-DETR checkpoints have
never seen it. That pairing does not crash -- it quietly detects worse -- so it is the most
likely way to ship a regression without noticing. These tests pin the two mechanisms that
prevent it: the GSD compatibility check, and the registry carrying the model's gated
operating point so nothing silently substitutes a different threshold.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
import typer

from solarsoiled.cli import GSD_TOLERANCE, _assert_gsd_compatible
from solarsoiled.registry import ResolvedWeights, resolve


def _index(tmp_path, gsd, name="idx.json"):
    p = tmp_path / name
    tiles = {"t.png": {"gsd_ground_m": gsd}} if gsd is not None else {"t.png": {}}
    p.write_text(json.dumps({"tiles": tiles}))
    return p


@pytest.fixture
def w2():
    return resolve("rfdetr-w2-20260807")


def test_w2_carries_its_gated_operating_point(w2):
    """conf is W2's val-frozen 0.50, NOT the CLI's old hardcoded 0.40 (which is W1's)."""
    assert w2.detector == "rf-detr"
    assert w2.conf == 0.50
    assert w2.iou_nms == 0.55
    assert w2.gsd_ground_m == pytest.approx(0.2044)


def test_matching_resolution_is_allowed(tmp_path, w2):
    _assert_gsd_compatible(w2, _index(tmp_path, 0.2043))  # the 21cm index's own rounding


def test_naip_tiles_are_refused_for_a_21cm_model(tmp_path, w2):
    """The actual regression this exists to stop: ~48cm NAIP into a 21cm detector."""
    with pytest.raises(typer.BadParameter, match="trained at"):
        _assert_gsd_compatible(w2, _index(tmp_path, 0.4795))


def test_finer_imagery_is_also_refused(tmp_path, w2):
    """6.3cm tiles are a mismatch too -- 'finer' is not automatically 'safe'."""
    with pytest.raises(typer.BadParameter):
        _assert_gsd_compatible(w2, _index(tmp_path, 0.0624))


def test_adhoc_weights_are_not_blocked(tmp_path, w2):
    """No registry GSD means nothing to compare; the guard must not invent a constraint."""
    adhoc = dataclasses.replace(w2, gsd_ground_m=None)
    _assert_gsd_compatible(adhoc, _index(tmp_path, 0.4795))


def test_index_without_gsd_defers_to_the_inference_preflight(tmp_path, w2):
    """rfdetr_infer.py hard-fails on a missing gsd_ground_m; this guard must not double-report."""
    _assert_gsd_compatible(w2, _index(tmp_path, None))


def test_missing_index_is_not_this_guards_error(tmp_path, w2):
    _assert_gsd_compatible(w2, tmp_path / "does_not_exist.json")


def test_tolerance_admits_rounding_but_not_a_resolution_change(w2):
    """Wide enough for 0.2043 vs 0.2044; far too narrow for NAIP's 2.3x."""
    assert abs(0.2043 - w2.gsd_ground_m) / w2.gsd_ground_m < GSD_TOLERANCE
    assert abs(0.4795 - w2.gsd_ground_m) / w2.gsd_ground_m > GSD_TOLERANCE
