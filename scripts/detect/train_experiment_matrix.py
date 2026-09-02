#!/usr/bin/env python3
"""Run a matrix of YOLO segmentation experiments from a YAML config."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import yaml


DEFAULT_TRAIN = "scripts/detect/train.py"
DEFAULT_EVAL = "scripts/detect/evaluate.py"


def resolve_best_weights(repo_root: Path, run_name: str) -> Path | None:
    for candidate in [
        repo_root / "runs" / "segment" / run_name / "weights" / "best.pt",
        repo_root / "runs" / "segment" / "runs" / "segment" / run_name / "weights" / "best.pt",
    ]:
        if candidate.exists():
            return candidate
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run matrix training experiments")
    parser.add_argument("--config", type=str, default="configs/yolo/experiments.yaml")
    parser.add_argument("--data", type=str, default=None)
    parser.add_argument("--experiment", type=str, default=None)
    parser.add_argument("--train-script", type=str, default=DEFAULT_TRAIN)
    parser.add_argument("--eval-script", type=str, default=DEFAULT_EVAL)
    parser.add_argument("--results-csv", type=str, default="outputs/eval/experiment_results.csv")
    parser.add_argument("--metrics-dir", type=str, default="outputs/eval/metrics")
    parser.add_argument("--skip-eval", action="store_true")
    parser.add_argument("--eval-only", action="store_true")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print resolved experiment config and training command without running anything.")
    parser.add_argument("--list", dest="list_only", action="store_true",
                        help="List experiment names in the config and exit.")
    return parser.parse_args()


def run_command(cmd: List[str], exp_name: str = "") -> int:
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        label = f"[{exp_name}] " if exp_name else ""
        print(f"  {label}FAILED (exit {result.returncode}) — check the training log above for details.",
              file=sys.stderr)
    return result.returncode


_BASELINE_WEIGHT = "sahi_baseline_train7.pt"
_NAIP_ONLY_STEP = "R0"


def _validate_experiment_models(experiments: List[Dict], repo_root: Path) -> None:
    """Warn if a Duke-ramp step still points at the baseline weight instead of R0's best.pt.

    R1+ should warm-start from R0's converged weights, not the SAHI baseline — using the
    baseline would discard all relabeling improvements baked into R0. This check catches
    the common mistake of forgetting to update the model: field when resuming the ramp.
    """
    for exp in experiments:
        name = exp.get("name", "")
        model = exp.get("model", "")
        model_path = Path(model) if Path(model).is_absolute() else repo_root / model
        if not model_path.exists():
            print(
                f"  WARNING [{name}]: model not found: {model}\n"
                f"    Resolve the path before training or this step will fail immediately.",
                file=sys.stderr,
            )
        # R1+ should not warm-start from the baseline — that discards R0's label fixes.
        if name != _NAIP_ONLY_STEP and Path(model).name == _BASELINE_WEIGHT:
            print(
                f"  WARNING [{name}]: model is still set to '{_BASELINE_WEIGHT}'.\n"
                f"    R1+ should warm-start from R0's converged best.pt, not the SAHI baseline.\n"
                f"    Update the model: field in the ramp config before running this step.",
                file=sys.stderr,
            )


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]

    config_path = Path(args.config).expanduser().resolve()
    train_script = Path(args.train_script).expanduser().resolve()
    eval_script = Path(args.eval_script).expanduser().resolve()
    results_csv = Path(args.results_csv).expanduser().resolve()
    metrics_dir = Path(args.metrics_dir).expanduser().resolve()

    if not config_path.exists():
        raise FileNotFoundError(f"Experiment config not found: {config_path}")
    if not train_script.exists():
        raise FileNotFoundError(f"Training script not found: {train_script}")
    if not args.skip_eval and not eval_script.exists():
        raise FileNotFoundError(f"Evaluation script not found: {eval_script}")

    with config_path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    experiments = config.get("experiments", [])
    if not experiments:
        raise ValueError("No experiments found in config")
    if args.experiment:
        experiments = [exp for exp in experiments if exp.get("name") == args.experiment]
        if not experiments:
            raise ValueError(f"Experiment not found in config: {args.experiment}")

    if args.list_only:
        for exp in experiments:
            print(exp.get("name", "?"))
        return 0

    _validate_experiment_models(experiments, repo_root)

    results_csv.parent.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, object]] = []

    for idx, exp in enumerate(experiments, start=1):
        exp_name = exp.get("name", f"exp_{idx:02d}")
        model = exp.get("model", "yolov8s-seg.pt")
        preset = exp.get("preset", "small")
        epochs = int(args.epochs if args.epochs is not None else exp.get("epochs", 50))
        degrees = float(exp.get("degrees", 0.0))
        shear = float(exp.get("shear", 0.0))
        perspective = float(exp.get("perspective", 0.0))
        scale = float(exp.get("scale", 0.5))
        mosaic = float(exp.get("mosaic", 1.0))
        close_mosaic = int(exp.get("close_mosaic", 10))
        copy_paste = float(exp.get("copy_paste", 0.0))
        cos_lr = bool(exp.get("cos_lr", False))
        lr0 = exp.get("lr0")
        lrf = exp.get("lrf")
        optimizer = exp.get("optimizer")
        # Sentinel distinguishes "key absent" (use ultralytics default) from
        # "key present with null value" (force-disable). exp.get() collapses
        # both to None, so use `in` to tell them apart.
        auto_augment = exp["auto_augment"] if "auto_augment" in exp else "__missing__"
        erasing = exp.get("erasing")
        translate = exp.get("translate")
        data_path = exp.get("data") or args.data or config.get("data")

        if args.dry_run:
            model_path = Path(model)
            if not model_path.is_absolute():
                model_path = repo_root / model_path
            model_status = "OK" if model_path.exists() else "MISSING"
            data_display = data_path or "(none)"
            print(f"  experiment : {exp_name}")
            print(f"  model      : {model}  [{model_status}]")
            print(f"  data       : {data_display}")
            print(f"  epochs     : {epochs}  preset={preset}")
            print(f"  optimizer  : {optimizer or '(default)'}  lr0={lr0 or '(default)'}")
            print()
            continue

        train_rc = 0
        if not args.eval_only:
            train_cmd = [
                sys.executable, str(train_script),
                "--name", exp_name, "--model", model,
                "--rtx3060-preset", preset, "--epochs", str(epochs),
                "--degrees", str(degrees), "--shear", str(shear),
                "--perspective", str(perspective), "--scale", str(scale),
                "--mosaic", str(mosaic), "--close-mosaic", str(close_mosaic),
                "--copy-paste", str(copy_paste),
                *(["--cos-lr"] if cos_lr else []),
                *(["--lr0", str(float(lr0))] if lr0 is not None else []),
                *(["--lrf", str(float(lrf))] if lrf is not None else []),
                *(["--optimizer", str(optimizer)] if optimizer is not None else []),
                *([] if auto_augment == "__missing__"
                   else ["--auto-augment", "none" if auto_augment is None else str(auto_augment)]),
                *(["--erasing", str(float(erasing))] if erasing is not None else []),
                *(["--translate", str(float(translate))] if translate is not None else []),
            ]
            if data_path:
                train_cmd.extend(["--data", str(Path(data_path).expanduser().resolve())])
            train_rc = run_command(train_cmd, exp_name)

        row: Dict[str, object] = {
            "experiment": exp_name, "model": model, "preset": preset, "epochs": epochs,
            "degrees": degrees, "shear": shear, "perspective": perspective, "scale": scale,
            "mosaic": mosaic, "close_mosaic": close_mosaic, "copy_paste": copy_paste,
            "cos_lr": cos_lr, "lr0": lr0 if lr0 is not None else "",
            "lrf": lrf if lrf is not None else "",
            "optimizer": optimizer if optimizer is not None else "",
            "train_return_code": train_rc,
            "status": "train_failed" if train_rc != 0 else ("eval_only" if args.eval_only else "trained"),
            "seg_map50": "", "seg_map50_95": "", "seg_precision": "", "seg_recall": "", "seg_f1": "",
        }

        if train_rc == 0 and not args.skip_eval:
            weights = resolve_best_weights(repo_root, exp_name)
            metrics_json = metrics_dir / f"{exp_name}.json"
            if weights is None:
                row["eval_return_code"] = 1
                row["status"] = "eval_failed"
                rows.append(row)
                continue
            eval_cmd = [
                sys.executable, str(eval_script),
                "--weights", str(weights), "--name", f"eval_{exp_name}",
                "--metrics-json", str(metrics_json),
            ]
            if data_path:
                eval_cmd.extend(["--data", str(Path(data_path).expanduser().resolve())])
            eval_rc = run_command(eval_cmd, exp_name)
            row["eval_return_code"] = eval_rc
            if eval_rc == 0 and metrics_json.exists():
                seg = json.loads(metrics_json.read_text(encoding="utf-8")).get("seg", {})
                row.update({
                    "seg_map50": seg.get("map50", ""), "seg_map50_95": seg.get("map50_95", ""),
                    "seg_precision": seg.get("precision", ""), "seg_recall": seg.get("recall", ""),
                    "seg_f1": seg.get("f1", ""), "status": "ok",
                })
            elif eval_rc != 0:
                row["status"] = "eval_failed"
        elif train_rc == 0:
            row["status"] = "train_only"

        rows.append(row)

    fieldnames = [
        "experiment", "model", "preset", "epochs", "degrees", "shear", "perspective",
        "scale", "mosaic", "close_mosaic", "copy_paste", "cos_lr", "lr0", "lrf",
        "optimizer",
        "train_return_code", "eval_return_code", "status",
        "seg_map50", "seg_map50_95", "seg_precision", "seg_recall", "seg_f1",
    ]
    with results_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved experiment summary: {results_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
