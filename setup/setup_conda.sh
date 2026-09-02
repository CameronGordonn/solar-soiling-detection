#!/bin/bash

# Create the `solar-soiling` conda environment.
#
#   bash setup/setup_conda.sh        # then: conda activate solar-soiling && make bootstrap
#
# Three things this script got wrong until 2026-08-26, each found by a cold-clone
# audit and each one that would have cost a newcomer hours:
#
#   1. It created python=3.11. Every number in this repo was produced on 3.10, and
#      pyproject only says ">=3.10", so nothing complained -- geopandas/rasterio/
#      xgboost simply resolved to different wheels and any breakage looked like the
#      newcomer's fault. Pinned to 3.10 below.
#   2. It installed `pytorch-cuda=11.8` unconditionally under `set -e`. On a CPU-only
#      box, a Mac, or any machine without an NVIDIA driver that aborts the whole
#      script at the halfway point. CUDA is now opt-in via WITH_CUDA=1.
#   3. It verified `import rasterio` without ever installing rasterio -- it arrived
#      only if the optional geoai install happened to succeed. Installed explicitly.
#
# Local GPU is optional: Stage-1 training runs on Colab, and everything else
# (tests, Stage 2, the eval gate) runs on CPU.

set -euo pipefail

ENV_NAME="${ENV_NAME:-solar-soiling}"
PY_VERSION="3.10"          # the tested version -- see note 1 above
WITH_CUDA="${WITH_CUDA:-0}"

echo "=== solar-soiling environment setup ==="
echo "    env      : $ENV_NAME"
echo "    python   : $PY_VERSION"
echo "    cuda     : $([ "$WITH_CUDA" = "1" ] && echo "yes (WITH_CUDA=1)" || echo "no (set WITH_CUDA=1 for an NVIDIA box)")"
echo ""

if ! command -v conda &> /dev/null; then
    echo "ERROR: conda not found. Install Miniforge/Miniconda first." >&2
    exit 1
fi

if conda env list | grep -qE "^${ENV_NAME}\s"; then
    echo "Found existing environment: $ENV_NAME (reusing)"
    existing_py="$(conda run -n "$ENV_NAME" python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)"
    if [ "$existing_py" != "$PY_VERSION" ]; then
        echo "WARNING: existing env is python $existing_py, this repo is tested on $PY_VERSION."
        echo "         Recreate with: conda env remove -n $ENV_NAME && bash setup/setup_conda.sh"
    fi
else
    echo "Creating environment: $ENV_NAME (python $PY_VERSION)"
    conda create -y -n "$ENV_NAME" "python=$PY_VERSION"
fi

eval "$(conda shell.bash hook)"
conda activate "$ENV_NAME"

echo ""
echo "=== Geospatial stack (conda-forge: GDAL/PROJ are painful via pip) ==="
conda install -y -c conda-forge \
    geopandas \
    rasterio \
    shapely \
    pyproj \
    scikit-image \
    pyyaml

echo ""
echo "=== PyTorch ==="
if [ "$WITH_CUDA" = "1" ]; then
    conda install -y -c pytorch -c nvidia pytorch pytorch-cuda=11.8
else
    # CPU build. Sufficient for the test suite, Stage 2, and the eval gate.
    conda install -y -c pytorch pytorch cpuonly
fi

echo ""
echo "=== Verifying the conda layer ==="
python - <<'PYEOF'
import sys
import geopandas, rasterio, shapely, torch, skimage
print("  python    :", ".".join(map(str, sys.version_info[:3])))
print("  geopandas :", geopandas.__version__)
print("  rasterio  :", rasterio.__version__)
print("  shapely   :", shapely.__version__)
print("  torch     :", torch.__version__, "(cuda)" if torch.cuda.is_available() else "(cpu)")
PYEOF

echo ""
echo "=== Conda layer OK ==="
echo ""
echo "Next, in order:"
echo "  conda activate $ENV_NAME"
echo "  make bootstrap        # pip-installs the package + dev/api extras"
echo "  make test-fast        # 248 tests, ~60s, needs NO data -- this is your setup proof"
echo ""
echo "Then see docs/ONBOARDING.md §1 for the data pull and the lane you are on."
