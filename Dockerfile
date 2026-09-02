# ── SolarSoiled container ─────────────────────────────────────────────────────
# Build:
#   docker build -t solarsoiled .                          # CPU, full pipeline (10.1 GB)
#   docker build --build-arg EXTRAS=api -t solarsoiled:api .   # API only (3.68 GB)
#   docker build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu121 \
#     -t solarsoiled:gpu .                                 # CUDA 12.1
#
# ── CLI pipeline ──────────────────────────────────────────────────────────────
#   docker run --rm \
#     -e NASA_EARTHDATA_TOKEN=<token> \
#     -v $(pwd)/models:/app/models \
#     -v $(pwd)/runs:/app/runs \
#     -v $(pwd)/outputs:/app/outputs \
#     -v $(pwd)/.cache:/app/.cache \
#     -v $(pwd)/data/external:/app/data/external \
#     solarsoiled run \
#       --aoi "-122.05,36.90,-121.85,37.05" \
#       --weights production \
#       --soiling-model soiling_production \
#       --last-cleaned 2026-01-01 \
#       --partner-id smoketest
#
# ── API server ────────────────────────────────────────────────────────────────
#   docker run --rm -p 8000:8000 \
#     -e SOLARSOILED_API_KEY=<key> \
#     -e NASA_EARTHDATA_TOKEN=<token> \
#     -v $(pwd)/models:/app/models \
#     -v $(pwd)/runs:/app/runs \
#     -v $(pwd)/outputs:/app/outputs \
#     -v $(pwd)/.cache:/app/.cache \
#     -v $(pwd)/data/external:/app/data/external \
#     --entrypoint solarsoiled-api \
#     solarsoiled
#
# Or use docker-compose up api  (see docker-compose.yml)
#
# ── Run as yourself, not root ─────────────────────────────────────────────────
# Add  --user "$(id -u):$(id -g)"  to any run that writes to a mounted volume.
# The container is root, so without it every artifact it writes into outputs/ is
# owned by root on the host and you cannot edit or delete it without sudo. This
# has been true since the first image: outputs/aoi/smoketest-docker/ has been
# root-owned since 2026-05-14.
#
# NOTE: Render does NOT build this file. render.yaml declares `runtime: python`,
# so the deployed API is a native Python build; this image is for local and
# partner runs. Keep the two in sync by hand or move Render to runtime: docker.
#
# ── Volume mounts ─────────────────────────────────────────────────────────────
#   models/          checkpoints. `production` resolves to rfdetr_w2_20260807.pth
#                    (RF-DETR, Apache-2.0) -- a .pth, not a .pt. The legacy
#                    ultralytics .pt files are AGPL and eval-only.
#   runs/            soiling model artifacts (runs/soiling/<run>/{model.ubj,calibrator.joblib})
#   outputs/         pipeline writes tiles/, arrays.geojson, risk.geojson, etc. here
#   .cache/          weather + AQ API cache (~440 MB warm) AND the HuggingFace cache
#                    that SAM2's checkpoint lands in -- see HF_HOME below
#   data/external/   static features CSV (elevation, WorldCover, OSM distances)
#
# NASA_EARTHDATA_TOKEN (optional): enables MERRA-2 PM2.5/PM10 backfill (1980-present).
# Without it, AQ features are populated from CAMS (2022-present only).
# Register at https://urs.earthdata.nasa.gov
#
# ── Operating constraint on `--weights production` ────────────────────────────
# The W2 checkpoint is gated on Santa Cruz County 2025 21cm imagery ONLY. Inside
# that AOI `solarsoiled run` routes tiling off the resolved model's gsd_ground_m
# automatically. Outside it the county imagery service has no coverage; use
# `stage1-60cm-legacy` (AGPL, eval-only) or re-gate W2 at 60cm.
# See the constraint note in models/registry.yaml.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

# Which optional-dependency groups to install. Default installs the detector, so
# `solarsoiled detect --weights production` works out of the box. Drop to
# `api` for a lean API-only image. Re-measured 2026-09-02, AFTER ultralytics and
# sahi moved to the `legacy` extra: api,detect = 10.1 GB, api = 3.68 GB, so the
# detector stack costs 6.4 GB. The api image lost ~0.5 GB in that move, which is
# the AGPL stack no longer being installed for callers who only serve the API.
#
# HOW THOSE SIZES WERE MEASURED, because they are NOT portable. They come from
# `docker images` on a WSL2 box running Docker Desktop with the containerd
# snapshotter, which reports UNCOMPRESSED size. The same commit built on a
# GitHub runner (standard overlay2) reported the full image at 5.88 GB -- about
# half. Neither number is wrong; they measure different things.
#
# So read these as upper bounds and as RELATIVE figures. The ratio between the
# variants is the durable fact; the absolute gigabytes depend on your storage
# driver. If you need a real number for provisioning, measure on the host that
# will actually run it.
ARG EXTRAS=api,detect

# OpenCV runtime libs (libgl1 + libglib2.0-0).
# rasterio/GDAL/PROJ/GEOS are bundled in their pip wheels — no apt packages needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── torch first: big layer, rarely changes ────────────────────────────────────
#
# PINNED TO 2.10.0 (was 2.4.0 until 2026-09-01). Two reasons:
#
#   1. MEASURED: `sam2` declares `torch>=2.5.1` / `torchvision>=0.20.1`, so the
#      old 2.4.0 pin is below sam2's floor and cannot coexist with the `detect`
#      extra added below -- pip must re-resolve torch. Re-resolution would come
#      from the DEFAULT index, not ${TORCH_INDEX}, and PyPI's default Linux torch
#      wheel is the CUDA build; that is how CI filled its disk on 2026-08-30
#      (Errno 28, no space left). Pinning at a version that already satisfies
#      sam2 means `pip install -e` sees it satisfied and never re-resolves.
#      (The CUDA-wheel outcome is the documented CI precedent, not something
#      re-observed here -- what was verified on 2026-09-01 is the version floor
#      and that this pin survives the install: `torch 2.10.0+cpu`, cuda build
#      None, in the built image.)
#
#   2. 2.10.0/0.25.0 is the pair the solar-soiling conda env actually runs, and
#      therefore the one the Stage 1 gate (F1 0.8260) was measured under. An
#      image on a different torch is not the configuration we published numbers
#      for. Bump both together, and re-run eval_tile_f1.py if you do.
#
# Previously noted here and now FIXED: the image used to carry ~454 MB of
# nvidia-nccl-cu12, which `xgboost` requires unconditionally on Linux. As of
# 2026-09-01 pyproject.toml installs `xgboost-cpu` on Linux instead, and the
# image dropped 11.2 GB -> 9.99 GB. Mac keeps plain `xgboost` (xgboost-cpu
# publishes no macOS wheels) and never had the nccl dependency anyway.
RUN pip install --no-cache-dir torch==2.10.0 torchvision==0.25.0 --index-url ${TORCH_INDEX}

# ── install package (deps pulled from pyproject.toml) ────────────────────────
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e ".[${EXTRAS}]"

# ── scripts + configs (imported by CLI as library calls) ─────────────────────
COPY scripts/ scripts/
COPY configs/ configs/

# ── model registry (resolves --weights names; checkpoints are runtime mounts) ─
COPY models/registry.yaml models/registry.yaml

# SAM2 fetches facebook/sam2-hiera-large from the HuggingFace hub on first use
# (scripts/detect/rfdetr_infer.py::load_sam). Default HF_HOME is /root/.cache,
# which is NOT a volume -- the ~900 MB checkpoint would be re-downloaded on every
# `docker run --rm`. Pointing it into the already-mounted .cache/ makes it
# persist, and lets an air-gapped host pre-seed it instead of needing network.
ENV HF_HOME=/app/.cache/huggingface

# ultralytics writes a settings file on first import and warns that
# /root/.config/Ultralytics is not writable, then falls back to /tmp anyway.
# Pointing it at /tmp up front removes a 4-line warning from every container run.
# (ultralytics is the AGPL legacy 60cm path -- it is imported, not shipped-on.)
ENV YOLO_CONFIG_DIR=/tmp/Ultralytics

# The repo's import convention is `PYTHONPATH=. python scripts/...` (CLAUDE.md),
# because scripts/ do `from src.risk... import` and `src` is not an installed
# package -- only `solarsoiled` is (pyproject packages.find where=["src"]).
# The `solarsoiled` CLI papers over this by inserting REPO_ROOT into sys.path
# itself, so the CLI always worked; running any research script directly did
# not. Measured 2026-09-01: `python scripts/predict/predict_risk.py` in the
# container died on `ModuleNotFoundError: No module named 'src'` until this was
# set. WORKDIR is /app, so this is the container spelling of `PYTHONPATH=.`.
ENV PYTHONPATH=/app

# Make `--user "$(id -u):$(id -g)"` actually work. Running as an arbitrary host
# UID leaves that UID absent from the image's /etc/passwd, and torch's inductor
# calls getpass.getuser() -> pwd.getpwuid(os.getuid()), which raises
# `KeyError: getpwuid(): uid not found: 1000` and kills the run at detector load.
# Measured 2026-09-01: without this, --user fails before a single chip is scored.
# getpass.getuser() checks LOGNAME/USER/LNAME/USERNAME *before* the password
# database, so naming the user here satisfies it for any UID. HOME is set for the
# same reason -- an unmapped UID gets no home, and libraries that write dotfiles
# fall over. Neither affects root runs, where these are just cosmetic.
ENV USER=solarsoiled
ENV HOME=/tmp

VOLUME ["/app/models", "/app/runs", "/app/outputs", "/app/.cache", "/app/data/external"]

# Default: API server (used by docker-compose up api).
# For CLI pipeline use: docker run solarsoiled solarsoiled run --aoi ...
CMD ["sh", "-c", "uvicorn solarsoiled.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
