"""Command-level tests for SolarSoiled CLI input guards."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import solarsoiled.cli as cli


def test_score_requires_detect_output_not_its_own_output(monkeypatch):
    """A new AOI needs arrays.geojson before score can create risk.geojson."""
    paths = SimpleNamespace(
        arrays_geojson=Path("__missing_arrays__.geojson"),
        risk_geojson=Path("__missing_risk__.geojson"),
        features_dir=Path("__missing_features__"),
    )
    monkeypatch.setattr(cli, "_resolve_soiling_model", lambda _spec: object())
    monkeypatch.setattr(cli, "_resolve_aoi", lambda _aoi, _partner_id: (None, paths))

    result = CliRunner().invoke(
        cli.app,
        ["score", "--aoi", "-122.05,36.95,-122.00,37.00", "--soiling-model", "stub"],
    )

    assert result.exit_code != 0
    assert f"missing {paths.arrays_geojson}" in result.output
    assert "solarsoiled detect" in result.output
