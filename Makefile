.PHONY: help bootstrap check-data verify-handoff _probe test test-fast eval-production eval-r2 relabel-overlays train-r0 demo-aoi train-soiling score-aoi sahi-combined docker-build docker-smoke

# Default weights for eval targets (override: make eval-production WEIGHTS=models/my.pt)
CONDA_ENV ?= solar-soiling
WEIGHTS ?= production
RUN_NAME ?= production_eval

help:
	@echo ""
	@echo "solar-soiling-ml — common targets"
	@echo ""
	@echo "  bootstrap             Install the package + dev/api extras into the active env"
	@echo "  check-data            Tier 2: which hand-off artifacts are present, by lane (DATA.md)"
	@echo "  verify-handoff        Checksum the restore against setup/handoff/MANIFEST.json"
	@echo "  docker-build          Build the container image (CPU, full pipeline)"
	@echo "  docker-smoke          Assert the built image can actually import the detector"
	@echo "  test                  Run full test suite (requires conda env: solar-soiling)"
	@echo "  test-fast             Tier 1: 248 unit tests, ~60s, needs NO data — run this first"
	@echo "  eval-production       SAHI F1 sweep on production weights — the number that matters"
	@echo "  eval-r2               SAHI F1 sweep on R2 weights (r2_cameron_20260509)"
	@echo "  sahi-combined         Re-run the sweep with perform_standard_pred=true (full+slice)"
	@echo "  relabel-overlays      Render FP/FN overlays for the next Roboflow labeling batch"
	@echo "  train-r0              Retrain R0 on current NAIP labels (warm-start from SAHI baseline)"
	@echo "  demo-aoi              Run full pipeline on Santa Cruz test AOI (solarsoiled run)"
	@echo "  train-soiling         Retrain Stage 2 soiling model"
	@echo "  score-aoi             Score detected arrays for soiling risk"
	@echo ""
	@echo "  WEIGHTS override:     make eval-production WEIGHTS=models/r2_cameron_20260509.pt"
	@echo ""

# ── Setup ─────────────────────────────────────────────────────────────────────

# Install the package + dev/test/api extras into the *currently active* env.
# First-time env creation is in setup/setup_conda.sh (creates the `solar-soiling` conda env).
bootstrap:
	pip install -e ".[dev,api]"
	@echo ""
	@echo "Installed core + dev/api. Stage-1 detection ALSO needs the detect extra:"
	@echo "    pip install -e \".[dev,api,detect]\"     # rfdetr + sam2"
	@echo ""
	@echo "Next: 'make test-fast' (~60s, needs NO data -- your setup proof), then"
	@echo "'make check-data' to see which hand-off artifacts you still need (DATA.md)."

# Report presence of the gitignored data artifacts the pipeline needs, grouped by
# the lane that needs them and by the verification tier they unlock (ONBOARDING.md §1).
#
# Two things this target got wrong until 2026-08-23, both of which made it useless as the
# hand-off check it is used as:
#   1. It required `runs/soiling/run_latest/model.ubj`, a path that has NEVER existed on any
#      machine including Cameron's -- so "zero MISSING" was unreachable by construction. The
#      current run is `run_optionb` (registry alias `soiling_production`).
#   2. It reported `present models/*.pt` on a clone that received no hand-off at all, because
#      one AGPL-lineage .pt was tracked in git. That file is gone from HEAD; the check now
#      names the checkpoint the gate actually loads.
# It also asked for `data/yolo/naip` (the retired 60cm set) and never mentioned the 21cm tiles
# `scripts/detect/eval_tile_f1.py` reads. Provenance for every path is in DATA.md.
check-data:
	@echo "── Tier 2 · Stage 2 + paper lane (reproduces holdout AUC 0.710) ──────────────"
	@$(MAKE) --no-print-directory _probe PATHS="\
		outputs/soiling/training_matrix.parquet \
		runs/soiling/run_optionb/feature_names.json \
		runs/soiling/run_optionb/model.ubj \
		outputs/aoi/santa-cruz-w2-21cm/arrays.geojson"
	@echo "── Tier 2 · Stage 1 detection lane (reproduces test F1 0.826) ────────────────"
	@$(MAKE) --no-print-directory _probe PATHS="\
		models/rfdetr_w2_20260807.pth \
		data/interim/scc21_labelset/images \
		data/interim/scc21_labelset/tile_index_21cm.json \
		data/yolo/scc21/tile_labels \
		data/interim/tile_index.json"
	@echo "── Regenerable (see DATA.md for the command; absence is not a blocker) ───────"
	@$(MAKE) --no-print-directory _probe PATHS="\
		data/external/nrel_soiling_map_annual.csv \
		data/external/static_features.csv \
		data/yolo/scc21/images \
		data/external/osm"
	@echo ""
	@echo "Required = the two Tier 2 blocks. Zero MISSING there is the hand-off gate."
	@echo "Legacy 60cm assets (data/yolo/naip, models/*.pt) are eval-only history and"
	@echo "deliberately NOT checked -- the yolo11*/R2 weights are AGPL and never ship."

# Internal: report present/MISSING for each path in $(PATHS). Not a user-facing target.
_probe:
	@n=0; for p in $(PATHS); do 		if [ -e "$$p" ]; then echo "  present  $$p"; 		else echo "  MISSING  $$p"; n=$$((n+1)); fi ; 	done; 	if [ $$n -gt 0 ]; then echo "  -> $$n MISSING in this block"; fi

# Verify a RESTORE against the tracked manifest (setup/handoff/MANIFEST.json).
# `check-data` answers "is the file there"; this answers "are these the bytes that
# produced 0.826". Run it after an rclone pull, before trusting any number.
verify-handoff:
	PYTHONPATH=. python scripts/data/build_handoff_bundle.py --verify

# ── Container ─────────────────────────────────────────────────────────────────

DOCKER_TAG ?= solarsoiled:local

docker-build:
	docker build -t $(DOCKER_TAG) .

# The regression this exists to catch: until 2026-09-01 the image installed only
# the `api` extra, so it started fine, served `--help` fine, and then died on
# `ModuleNotFoundError: rfdetr` the moment anyone ran the SHIPPING detector. A
# smoke test that only checks `--help` would have passed that whole time, so this
# imports rfdetr and sam2 explicitly and resolves `production` through the
# registry. No weights or data needed — it never runs inference.
docker-smoke:
	@docker run --rm --entrypoint python $(DOCKER_TAG) -c "\
import rfdetr, sam2, solarsoiled; \
from solarsoiled.registry import resolve; \
r = resolve('production'); \
assert r.detector == 'rf-detr', r.detector; \
print('OK  rfdetr', rfdetr.__version__ if hasattr(rfdetr,'__version__') else '?', '| production ->', r.detector, r.path)"
	@docker run --rm --entrypoint solarsoiled $(DOCKER_TAG) --help > /dev/null && echo "OK  solarsoiled --help"

# ── Tests ─────────────────────────────────────────────────────────────────────

# The env name is a variable, not a constant, for one reason: it was hardcoded, so
# `make test-fast` ran against the `solar-soiling` env no matter which env you had
# built or activated. A newcomer who named theirs anything else got a green suite
# that had tested somebody else's environment.
test:
	conda run -n $(CONDA_ENV) python -m pytest tests/ -v

test-fast:
	conda run -n $(CONDA_ENV) python -m pytest tests/ -v -m "not integration and not smoke and not contract"

# ── Stage 1 eval ─────────────────────────────────────────────────────────────

eval-production:
	PYTHONPATH=. python scripts/detect/sahi_threshold_sweep.py \
		--weights $(WEIGHTS) \
		--config configs/yolo/thresholds_sahi.yaml \
		--run-name production_eval

eval-r2:
	PYTHONPATH=. python scripts/detect/sahi_threshold_sweep.py \
		--weights models/r2_cameron_20260509.pt \
		--config configs/yolo/thresholds_sahi.yaml \
		--run-name r2_cameron_20260509_reeval

# Re-run eval with SAHI combined mode (full-tile + slice merge).
# Edit configs/yolo/thresholds_sahi_combined.yaml to set perform_standard_pred: true,
# or flip it manually before running.
sahi-combined:
	@if ! grep -q 'perform_standard_pred: true' configs/yolo/thresholds_sahi.yaml; then \
		echo ""; \
		echo "WARNING: perform_standard_pred is not 'true' in configs/yolo/thresholds_sahi.yaml"; \
		echo "Edit the config first, then re-run this target."; \
		echo ""; \
		exit 1; \
	fi
	PYTHONPATH=. python scripts/detect/sahi_threshold_sweep.py \
		--weights $(WEIGHTS) \
		--config configs/yolo/thresholds_sahi.yaml \
		--run-name $(RUN_NAME)_combined

# ── Label audit ───────────────────────────────────────────────────────────────

relabel-overlays:
	PYTHONPATH=. python scripts/detect/per_detection_rca.py \
		--weights $(WEIGHTS) \
		--data data/yolo/naip/data.yaml \
		--splits val test \
		--sahi --conf 0.05 --iou 0.50 \
		--run-name $(RUN_NAME)
	PYTHONPATH=. python scripts/labeling/bucket_overlays.py \
		--csv outputs/eval/$(RUN_NAME)/per_detection.csv \
		--bucket confident_fp --top 20
	PYTHONPATH=. python scripts/labeling/bucket_overlays.py \
		--csv outputs/eval/$(RUN_NAME)/per_detection.csv \
		--bucket worst_small_fn --top 20
	@echo ""
	@echo "Overlays written to outputs/label_viz/$(RUN_NAME)/"

# ── Training ──────────────────────────────────────────────────────────────────

train-r0:
	PYTHONPATH=. python scripts/detect/train_experiment_matrix.py \
		--config configs/yolo/experiments_joint_v2_ramp.yaml \
		--experiment R0 \
		--data data/yolo/naip/data.yaml

# ── End-to-end demo ───────────────────────────────────────────────────────────

# Santa Cruz test AOI (small bbox for smoke test)
AOI ?= -122.05,36.97,-121.98,37.03
PARTNER ?= smoketest
LAST_CLEANED ?= 2025-12-01
SOILING_MODEL ?= soiling_production

demo-aoi:
	solarsoiled run \
		--aoi "$(AOI)" \
		--weights production \
		--soiling-model $(SOILING_MODEL) \
		--last-cleaned $(LAST_CLEANED) \
		--partner-id $(PARTNER)

# ── Stage 2 soiling ───────────────────────────────────────────────────────────

train-soiling:
	PYTHONPATH=. python scripts/predict/train_risk_model.py --run-name run_latest

score-aoi:
	PYTHONPATH=. python scripts/predict/predict_risk.py \
		--model runs/soiling/run_latest/model.ubj
