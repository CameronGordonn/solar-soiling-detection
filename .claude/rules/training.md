# Rules: Training Scripts

Applied when working on `scripts/detect/train*.py` or any training-related code.

- Always use `results[0].mp` and `results[0].mr` for metrics (v11 API). Never use v8-style `model.results[0].metrics`.
- Model weights path is always `runs/segment/train*/weights/best.pt` — never `last.pt` for evaluation.
- Batch size and imgsz must be parameterizable; never hardcode. Use presets from `configs/yolo/experiments.yaml`.
- Any new hyperparameter must be added to the YAML config, not hardcoded in the script.
- After modifying a training script, confirm it still accepts `--config` and `--model` CLI flags.
- Training runs create new directories automatically (`train`, `train2`, etc.) — do not delete or rename old runs without asking.
- Training entrypoints live in `scripts/detect/`: `train.py` (single run) and `train_experiment_matrix.py` (ramp matrix). They train YOLOv11 despite the historical `yolov8` naming elsewhere in the tree.
