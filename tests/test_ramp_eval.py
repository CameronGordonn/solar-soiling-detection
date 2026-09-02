"""Tests for ramp_eval.py — halt-rule logic and CSV row appending."""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import pytest

# Add repo root to path so scripts.detect.ramp_eval is importable.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.detect.ramp_eval import check_halt, append_curve_row  # noqa: E402


# ---------------------------------------------------------------------------
# check_halt — stop-rule boundary tests
# ---------------------------------------------------------------------------

class TestCheckHalt:
    BASELINE = 0.563
    HALT_DELTA = 0.07

    def test_no_regression_no_halt(self):
        halts, delta = check_halt(0.563, self.BASELINE, self.HALT_DELTA)
        assert not halts
        assert math.isclose(delta, 0.0)

    def test_small_regression_no_halt(self):
        # Δ = 0.563 - 0.50 = 0.063 < 0.07
        halts, delta = check_halt(0.50, self.BASELINE, self.HALT_DELTA)
        assert not halts
        assert math.isclose(delta, 0.063, rel_tol=1e-6)

    def test_exactly_at_threshold_no_halt(self):
        # Δ = 0.07 exactly — rule is strict >, so this should NOT halt
        halts, delta = check_halt(self.BASELINE - self.HALT_DELTA, self.BASELINE, self.HALT_DELTA)
        assert not halts

    def test_just_over_threshold_halts(self):
        # Δ = 0.563 - 0.492 = 0.071 > 0.07
        halts, delta = check_halt(0.492, self.BASELINE, self.HALT_DELTA)
        assert halts
        assert delta > self.HALT_DELTA

    def test_large_regression_halts(self):
        # Replicates the 2026-05-03 joint-training result (mAP50 ≈ 0.128)
        halts, delta = check_halt(0.128, self.BASELINE, self.HALT_DELTA)
        assert halts
        assert math.isclose(delta, self.BASELINE - 0.128, rel_tol=1e-6)

    def test_improvement_no_halt(self):
        # Better than baseline — negative delta
        halts, delta = check_halt(0.65, self.BASELINE, self.HALT_DELTA)
        assert not halts
        assert delta < 0

    def test_nan_naip_map_does_not_halt(self):
        # If eval failed and returned NaN, we must not trigger a false HALT
        halts, delta = check_halt(float("nan"), self.BASELINE, self.HALT_DELTA)
        assert not halts
        assert math.isnan(delta)

    def test_custom_baseline_and_delta(self):
        # Caller can override both params — used when baseline shifts after relabeling
        halts, delta = check_halt(0.40, baseline=0.50, halt_delta=0.05)
        assert halts
        assert math.isclose(delta, 0.10, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# append_curve_row — CSV accumulation
# ---------------------------------------------------------------------------

class TestAppendCurveRow:

    def _sample_row(self, step: str) -> dict:
        return {
            "step": step,
            "naip_repeat": 580,
            "weights": "best.pt",
            "naip_test_map50": 0.55,
            "duke_test_map50": 0.30,
            "naip_test_precision": 0.60,
            "naip_test_recall": 0.50,
            "naip_regression_delta": 0.013,
            "sahi_f1_val": float("nan"),
            "sahi_conf_val": float("nan"),
            "fn_rate_small_arrays": float("nan"),
            "fp_rate": 0.25,
        }

    def test_creates_file_with_header_on_first_write(self, tmp_path: Path):
        csv_path = tmp_path / "ramp_curve.csv"
        append_curve_row(csv_path, self._sample_row("R1"))
        assert csv_path.exists()
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 1
        assert rows[0]["step"] == "R1"

    def test_appends_without_duplicate_header(self, tmp_path: Path):
        csv_path = tmp_path / "ramp_curve.csv"
        append_curve_row(csv_path, self._sample_row("R1"))
        append_curve_row(csv_path, self._sample_row("R2"))
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 2
        assert rows[0]["step"] == "R1"
        assert rows[1]["step"] == "R2"

    def test_creates_parent_dir(self, tmp_path: Path):
        csv_path = tmp_path / "sub" / "dir" / "ramp_curve.csv"
        append_curve_row(csv_path, self._sample_row("R0"))
        assert csv_path.exists()

    def test_values_round_trip(self, tmp_path: Path):
        csv_path = tmp_path / "ramp_curve.csv"
        row = self._sample_row("R3")
        row["naip_test_map50"] = 0.612
        append_curve_row(csv_path, row)
        rows = list(csv.DictReader(csv_path.open()))
        assert float(rows[0]["naip_test_map50"]) == pytest.approx(0.612)
