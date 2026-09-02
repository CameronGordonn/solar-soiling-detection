"""Quick comparison table for runs/soiling/<run>/metrics.json files.

Usage:
    python scripts/14_compare_soiling_runs.py                          # all runs
    python scripts/14_compare_soiling_runs.py run_d run_e run_f        # specific runs
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="*", help="Run names (default: all under runs/soiling/)")
    args = parser.parse_args()

    runs_dir = repo_root / "runs" / "soiling"
    if args.runs:
        names = args.runs
    else:
        names = sorted(p.name for p in runs_dir.iterdir() if p.is_dir())

    rows = []
    for name in names:
        m_path = runs_dir / name / "metrics.json"
        if not m_path.exists():
            continue
        m = json.loads(m_path.read_text())
        row = {
            "run": name,
            "n_folds": m.get("n_folds"),
            "target": m.get("target_mode", "binary"),
            "auc": round(m.get("mean_auc", float("nan")), 3),
            "ap": round(m.get("mean_ap", float("nan")), 3),
            "spearman": round(m["mean_spearman"], 3) if "mean_spearman" in m else None,
            "fold_aucs": [round(a, 3) for a in m.get("fold_aucs", [])],
            "holdout_year": m.get("holdout_year"),
            "holdout_auc": round(m["holdout_auc"], 3) if "holdout_auc" in m else None,
            "holdout_n": m.get("holdout_n"),
        }
        # Pooled leave-one-year-out holdout (the honest temporal gate) if recorded
        # by scripts/predict/holdout_ci.py --out-json. This is the metric the
        # Phase 2 gate is judged on, not the noisy single-year holdout_auc above.
        ci_path = runs_dir / name / "holdout_ci.json"
        if ci_path.exists():
            ci = json.loads(ci_path.read_text())
            lo, hi = ci.get("bootstrap_ci95", [float("nan"), float("nan")])
            row["ooy_auc"] = round(ci.get("pooled_auc", float("nan")), 3)
            row["ooy_ci95"] = [round(lo, 3), round(hi, 3)]
            row["ooy_ece"] = round(ci.get("calibration", {}).get("ece", float("nan")), 3)
            row["cal_ok"] = ci.get("calibration_retained")
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("run")
    pd.set_option("display.max_colwidth", 80)
    pd.set_option("display.width", 200)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
