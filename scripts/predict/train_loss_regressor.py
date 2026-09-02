#!/usr/bin/env python3
"""Train the annual-soiling-loss regression head (replaces RISK_TO_LOSS_PCT = 8.0).

Reads the cached training matrix (no Open-Meteo refetch), fits three XGBoost quantile
heads on ``loss_pct = (1 - iwsr) * 100`` with spatial CV, and reports whether the
predicted distribution reproduces the MEASURED distribution's spread — the property the
old ``risk_score x 8`` path destroyed, and the one the clean/no-clean decision depends on.

    PYTHONPATH=. conda run -n solar-soiling python scripts/predict/train_loss_regressor.py \
        --run-name run_lossreg

Outputs ``runs/soiling/<run>/loss_regressor/{loss_q10,loss_q50,loss_q90}.ubj``,
``feature_names.json``, ``feature_medians.json``, ``metrics.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from src.risk.loss_model import (  # noqa: E402
    evaluate_spread, loss_pct_from_iwsr, save_bundle, train_loss_regressor,
)

DEFAULT_MATRIX = REPO / "outputs/soiling/training_matrix.parquet"

# Columns that are identifiers, targets, or leak the target. `label` is the binarised
# target; `iwsr*` IS the target. The kimber/somosclean proxies are physics estimates of
# the target itself — including them is what produced the CV AUC=1.0 leakage runs
# documented in .claude/rules/stage2-risk.md, so they are excluded by default.
DROP_ALWAYS = {
    "station_id", "label", "iwsr", "iwsr_lower", "iwsr_upper", "is_feedback",
    "is_summary", "as_of", "nlcd_class",
}
PROXY_COLS = {
    "kimber_iwsr_proxy", "kimber_iwsr_7d_mean", "kimber_iwsr_30d_mean", "kimber_iwsr_90d_mean",
    "somosclean_sl_proxy", "somosclean_sl_7d_mean", "somosclean_sl_30d_mean",
}


def build_features(df: pd.DataFrame, include_proxies: bool) -> list[str]:
    drop = set(DROP_ALWAYS)
    if not include_proxies:
        drop |= PROXY_COLS
    cols = [c for c in df.columns
            if c not in drop and pd.api.types.is_numeric_dtype(df[c])]
    return cols


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    ap.add_argument("--run-name", default="run_lossreg")
    ap.add_argument("--runs-dir", type=Path, default=REPO / "runs/soiling")
    ap.add_argument("--n-folds", type=int, default=5)
    ap.add_argument("--cluster-km", type=float, default=10.0)
    ap.add_argument("--include-proxies", action="store_true",
                    help="include kimber/somosclean physics proxies (LEAKAGE RISK — see "
                         ".claude/rules/stage2-risk.md; off by default)")
    ap.add_argument("--panel-only", action="store_true", default=True,
                    help="drop summary-only censored rows (default: on)")
    ap.add_argument("--min-spread-ratio", type=float, default=0.35,
                    help="warn if the out-of-fold predicted p10-p90 span falls below "
                         "this fraction of the measured span")
    args = ap.parse_args(argv)

    df = pd.read_parquet(args.matrix)
    n_all = len(df)
    if args.panel_only and "is_summary" in df.columns:
        df = df[~df["is_summary"].astype(bool)].copy()
    df = df[np.isfinite(pd.to_numeric(df["iwsr"], errors="coerce"))].copy()
    print(f"[data] {args.matrix.name}: {n_all} rows -> {len(df)} usable panel rows")

    feats = build_features(df, args.include_proxies)
    print(f"[data] {len(feats)} features"
          f"{' (INCLUDING physics proxies — leakage risk)' if args.include_proxies else ''}")

    y = loss_pct_from_iwsr(df["iwsr"])
    print(f"[data] measured loss_pct: p10 {np.percentile(y,10):.2f}  p50 {np.percentile(y,50):.2f}"
          f"  p90 {np.percentile(y,90):.2f}  sd {np.std(y, ddof=1):.2f}")

    bundle = train_loss_regressor(
        df, feats, n_folds=args.n_folds, cluster_km=args.cluster_km,
    )

    oof = bundle.metrics.get("oof", {})
    print("\n=== out-of-fold (spatial CV) ===")
    print(f"  n                 {oof.get('n')}")
    print(f"  MAE               {oof.get('mae', float('nan')):.3f} pts")
    print(f"  RMSE              {oof.get('rmse', float('nan')):.3f} pts")
    print(f"  bias              {oof.get('bias', float('nan')):+.3f} pts")
    print(f"  Spearman          {oof.get('spearman', float('nan')):.3f}")
    print(f"  measured p10/50/90 {oof.get('measured_p10',0):.2f} / "
          f"{oof.get('measured_p50',0):.2f} / {oof.get('measured_p90',0):.2f}"
          f"   span {oof.get('measured_p10_p90_span',0):.2f}")
    print(f"  predicted p10/50/90 {oof.get('pred_p10',0):.2f} / "
          f"{oof.get('pred_p50',0):.2f} / {oof.get('pred_p90',0):.2f}"
          f"   span {oof.get('pred_p10_p90_span',0):.2f}")
    print(f"  SPREAD RATIO      {oof.get('spread_ratio', float('nan')):.3f}"
          f"   (sd ratio {oof.get('sd_ratio', float('nan')):.3f})")
    print(f"  PI coverage       {oof.get('pi_coverage', float('nan')):.3f}  (nominal 0.80)")
    print(f"  PI width med/mean {oof.get('pi_width_median', float('nan')):.2f} / "
          f"{oof.get('pi_width_mean', float('nan')):.2f} pts")

    conf = bundle.metrics.get("oof_conformal", {})
    print(f"\n=== after CQR conformal calibration (offset "
          f"{bundle.conformal_offset_pct:+.2f} pts/side) ===")
    print(f"  PI coverage       {conf.get('pi_coverage', float('nan')):.3f}  (nominal 0.80)")
    print(f"  PI width med/mean {conf.get('pi_width_median', float('nan')):.2f} / "
          f"{conf.get('pi_width_mean', float('nan')):.2f} pts")

    # Anchor: what the retired risk x 8 path would have produced on these same rows.
    sr = oof.get("spread_ratio", float("nan"))
    if np.isfinite(sr) and sr < args.min_spread_ratio:
        print(f"\n[WARN] spread_ratio {sr:.3f} < {args.min_spread_ratio} — the predicted "
              "distribution is materially flatter than the measured one. The decision "
              "layer will under-recommend cleaning. Do not ship without explaining this.")

    out = Path(args.runs_dir) / args.run_name / "loss_regressor"
    bundle.metrics["source_matrix"] = str(args.matrix)
    bundle.metrics["include_proxies"] = bool(args.include_proxies)
    bundle.metrics["panel_only"] = bool(args.panel_only)
    save_bundle(bundle, out)
    print(f"\n[ok] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
