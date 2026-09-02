"""Stage 1 → Stage 2 contract test.

Verifies that the output of the detection stage (arrays.geojson) is correctly
consumed by the soiling risk pipeline and that both risk.geojson and
recommendations.json conform to their expected schemas.

Uses a pre-baked fixture arrays.geojson (3 small polygons in Santa Cruz) so
no NAIP download or YOLO inference is required. Skipped unless the soiling
model weights are present (runs/soiling/run_optionb/model.ubj).

WEATHER IS SERVED FROM THE ON-DISK CACHE ONLY (`--cache-only`), added
2026-09-01. These tests previously called Open-Meteo live, which made the
"fast, needs-no-data" tier non-hermetic: on 2026-09-01 both of them failed
purely because the free-tier hourly quota was exhausted by an unrelated
backfill, and `make test-fast` -- the first command ONBOARDING tells a new
person to run -- went red for a reason that had nothing to do with the code.

Cache-only also makes the assertion stronger, not weaker: the run is pinned to
the same weather bytes the cached production results were built from, so a
change in the score means a change in OUR code rather than a different day's
ERA5 pull. If the cache is cold the tests SKIP with instructions, because
"you have not warmed the cache" is not a contract violation.

Run:
    pytest tests/test_stage2_contract.py -v
Or to run all smoke + contract tests:
    pytest tests/test_smoke_run.py tests/test_stage2_contract.py -v
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from solarsoiled.paths import AoiPaths
from solarsoiled.registry import RegistryError, resolve_soiling

FIXTURE_ARRAYS = Path(__file__).parent / "fixtures" / "arrays_santa_cruz.geojson"
PARTNER_ID = "smoke-stage2-contract"

# The weather cache is keyed on the REQUESTED DATE RANGE, and `as_of` defaults to
# today -- so an unpinned run asks for a range nobody has fetched and misses the
# cache every single day, no matter how warm it is. Pinning to the date the
# production feature matrix was built (outputs/aoi/santa-cruz-w2-21cm/features/
# inference_matrix.parquet, 2026-08-12) makes these tests reuse the exact ERA5
# bytes the live site's numbers came from. Verified 2026-09-01: 2026-08-12 serves
# all 3 fixture arrays from cache; 08-11 and 08-09 miss, which is what tells you
# the key really is the date and not the cell.
AS_OF = "2026-08-12"
SOILING_ALIAS = "soiling_smoketest"

# Required output schemas
_RISK_REQUIRED_PROPS = {"array_id", "risk_score", "area_m2"}
_RECOMMEND_REQUIRED_KEYS = {"window_start", "confidence", "rule_fired", "inputs", "beta"}


def _skip_if_weather_cache_cold(exc: Exception) -> None:
    """Turn a cold-cache failure into a skip, but let real breakage fail loudly."""
    msg = str(exc)
    if "CacheMiss" in msg or "cache" in msg.lower():
        pytest.skip(
            "weather cache is cold for the fixture cells (36.95,-122.05) and "
            "(37.00,-122.05). Warm it once with a networked run of "
            "scripts/analyze/build_risk_features.py (without --cache-only), or "
            "restore .cache/soiling/openmeteo.sqlite from the hand-off. "
            f"Underlying: {msg[:200]}"
        )
    raise exc


def _soiling_weights_available() -> bool:
    try:
        resolve_soiling(SOILING_ALIAS)
        return True
    except RegistryError:
        return False


@pytest.fixture(autouse=True)
def clean_output():
    paths = AoiPaths(PARTNER_ID)
    if paths.root.exists():
        shutil.rmtree(paths.root)
    yield
    if paths.root.exists():
        shutil.rmtree(paths.root)


@pytest.mark.skipif(
    not _soiling_weights_available(),
    reason=f"register '{SOILING_ALIAS}' in models/registry.yaml and ensure model.ubj is present",
)
def test_score_produces_valid_risk_geojson():
    """score stage: arrays.geojson → risk.geojson with risk_score on every feature."""
    import sys
    import importlib.util

    from solarsoiled.paths import REPO_ROOT

    paths = AoiPaths(PARTNER_ID)
    paths.ensure_root()
    paths.features_dir.mkdir(parents=True, exist_ok=True)

    # Copy fixture arrays into the expected location
    shutil.copy2(FIXTURE_ARRAYS, paths.arrays_geojson)

    resolved = resolve_soiling(SOILING_ALIAS)

    # extract_features
    script_path = REPO_ROOT / "scripts" / "analyze" / "extract_array_features.py"
    spec = importlib.util.spec_from_file_location("_extract", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.extract_features(
        input_geojson=paths.arrays_geojson,
        out_table=paths.array_features_parquet,
        out_geo=paths.array_features_geo_parquet,
    )
    assert paths.array_features_geo_parquet.is_file(), "array_features.geo.parquet not produced"

    # build_features
    script_path = REPO_ROOT / "scripts" / "analyze" / "build_risk_features.py"
    spec = importlib.util.spec_from_file_location("_build", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        mod.main([
            "--config", str(REPO_ROOT / "configs" / "soiling" / "california.yaml"),
            "--features-config", str(REPO_ROOT / "configs" / "soiling" / "features.yaml"),
            "--arrays", str(paths.array_features_geo_parquet),
            "--out", str(paths.inference_matrix),
            "--as-of", AS_OF,
            "--cache-only",
        ])
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is a cold cache
        _skip_if_weather_cache_cold(exc)
    assert paths.inference_matrix.is_file(), "inference_matrix.parquet not produced"

    # predict_risk
    script_path = REPO_ROOT / "scripts" / "predict" / "predict_risk.py"
    spec = importlib.util.spec_from_file_location("_predict", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main([
        "--model", str(resolved.path),
        "--features", str(paths.inference_matrix),
        "--arrays", str(paths.array_features_geo_parquet),
        "--out", str(paths.risk_geojson),
    ])

    assert paths.risk_geojson.is_file(), "risk.geojson not produced"
    fc = json.loads(paths.risk_geojson.read_text())
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 3, f"expected 3 features, got {len(fc['features'])}"
    for feat in fc["features"]:
        props = feat["properties"]
        missing = _RISK_REQUIRED_PROPS - props.keys()
        assert not missing, f"risk.geojson feature missing props: {missing}"
        score = props["risk_score"]
        assert 0.0 <= score <= 1.0, f"risk_score {score} out of [0, 1]"


@pytest.mark.skipif(
    not _soiling_weights_available(),
    reason=f"register '{SOILING_ALIAS}' in models/registry.yaml and ensure model.ubj is present",
)
def test_recommend_produces_valid_schema():
    """Full pipeline: fixture arrays → score → recommend; check output schemas."""
    from solarsoiled.recommend import (
        recommend_cleaning,
        recommend_per_array,
        write_array_recommendations,
        write_recommendation,
    )

    # Run score first (reuse logic above via a direct call)
    # Shortcut: use an existing risk.geojson from outputs if available,
    # otherwise run the score stage inline.
    import sys
    import importlib.util
    from solarsoiled.paths import REPO_ROOT

    paths = AoiPaths(PARTNER_ID)
    paths.ensure_root()
    paths.features_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURE_ARRAYS, paths.arrays_geojson)

    resolved = resolve_soiling(SOILING_ALIAS)

    for script_key, func_name, argv in [
        ("analyze/extract_array_features.py", "extract_features", None),
        ("analyze/build_risk_features.py", "main", [
            "--config", str(REPO_ROOT / "configs" / "soiling" / "california.yaml"),
            "--features-config", str(REPO_ROOT / "configs" / "soiling" / "features.yaml"),
            "--arrays", str(paths.array_features_geo_parquet),
            "--out", str(paths.inference_matrix),
            "--as-of", AS_OF,
            "--cache-only",
        ]),
        ("predict/predict_risk.py", "main", [
            "--model", str(resolved.path),
            "--features", str(paths.inference_matrix),
            "--arrays", str(paths.array_features_geo_parquet),
            "--out", str(paths.risk_geojson),
        ]),
    ]:
        sp = REPO_ROOT / "scripts" / script_key
        spec = importlib.util.spec_from_file_location(f"_{script_key}", sp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if argv is None:
            mod.extract_features(
                input_geojson=paths.arrays_geojson,
                out_table=paths.array_features_parquet,
                out_geo=paths.array_features_geo_parquet,
            )
        else:
            try:
                mod.main(argv)
            except Exception as exc:  # noqa: BLE001 - re-raised unless it is a cold cache
                _skip_if_weather_cache_cold(exc)

    assert paths.risk_geojson.is_file()

    # recommend
    payload = recommend_cleaning(
        risk_geojson=paths.risk_geojson,
        last_cleaned=date(2025, 12, 1),
        aoi_centroid=(36.974, -122.030),
        risk_threshold=0.6,
    )
    write_recommendation(paths.recommendations_json, payload)

    array_rows = recommend_per_array(
        paths.risk_geojson, payload, risk_threshold=0.6
    )
    write_array_recommendations(paths.array_recommendations_json, array_rows)

    # Schema checks
    assert paths.recommendations_json.is_file()
    rec = json.loads(paths.recommendations_json.read_text())
    missing = _RECOMMEND_REQUIRED_KEYS - rec.keys()
    assert not missing, f"recommendations.json missing keys: {missing}"

    assert paths.array_recommendations_json.is_file()
    arr_recs = json.loads(paths.array_recommendations_json.read_text())
    assert isinstance(arr_recs, list)
    assert len(arr_recs) == 3
    for row in arr_recs:
        assert "array_id" in row
        assert "risk_score" in row
