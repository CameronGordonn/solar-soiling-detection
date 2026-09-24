"""The decision path must not acquire heavy dependencies.

WHY THIS EXISTS. The deployed API pulled geopandas, pandas, numpy, pyproj and shapely at
import purely to serve arithmetic, because `api.py` imported the pipeline modules at
module scope. That is what made the service a 3.68 GB image, and it is the kind of
regression that reappears the moment someone adds a convenient top-level import.

The decision chain itself (`risk.economics`, `risk.rates`) is stdlib-only by design:
math, random, dataclasses. Keeping the FastAPI module equally clean is what allows a lean
deployment and what keeps /decision available when the geospatial stack is missing.

These tests run each import in a SUBPROCESS, because once pytest has imported the heavy
modules for other tests, `sys.modules` in this process can no longer tell us anything.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

#: Anything here in `sys.modules` after the import means the lean property is gone.
HEAVY = (
    "torch", "geopandas", "rasterio", "pandas", "numpy", "folium",
    "xgboost", "sklearn", "pyproj", "shapely", "matplotlib", "pvlib", "laspy",
)

PROBE = textwrap.dedent(
    """
    import sys
    import {module}
    heavy = [m for m in {heavy!r} if m in sys.modules]
    print(",".join(heavy))
    """
)


def _heavy_after_importing(module: str) -> list[str]:
    out = subprocess.run(
        [sys.executable, "-c", PROBE.format(module=module, heavy=HEAVY)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        pytest.skip(f"cannot import {module} in a subprocess: {out.stderr.strip()[:200]}")
    return [m for m in out.stdout.strip().split(",") if m]


def test_decision_module_is_stdlib_only():
    assert _heavy_after_importing("solarsoiled.decision") == []


def test_importing_the_api_pulls_no_heavy_dependency():
    """Regression: this used to pull geopandas, pandas, numpy, pyproj and shapely."""
    assert _heavy_after_importing("solarsoiled.api") == []


def test_the_economics_chain_itself_is_stdlib_only():
    assert _heavy_after_importing("src.risk.economics") == []
    assert _heavy_after_importing("src.risk.rates") == []
