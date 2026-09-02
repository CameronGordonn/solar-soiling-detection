"""Statistically honest year-holdout evaluation for the Stage 2 soiling model.

The single-year (2022) holdout gate is a noisy point estimate: n=97 rows give an
AUC standard error of ~0.06, so 0.68 and 0.70 are indistinguishable. This tool
re-adjudicates the temporal-generalization question with proper uncertainty:

  * per candidate holdout year: point AUC, Hanley-McNeil SE, a 4000x row
    bootstrap 95% CI, and P(AUC >= 0.70) from the bootstrap distribution
  * a leave-one-year-out (rolling) holdout across EVERY panel year, then a
    *pooled* out-of-year AUC (each row scored the year it was held out) with its
    own Hanley-McNeil SE and bootstrap CI

It reuses the exact production training path (train_risk_model + spatial CV +
sample weights) and the CACHED training matrix — it never refetches weather, so
it does not touch the Open-Meteo quota wall.

Run:  PYTHONPATH=. conda run -n solar-soiling python scripts/predict/holdout_ci.py
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import roc_auc_score

from src.risk.risk_model import (
    SpatialCVConfig,
    impute_with_feature_medians,
    train_risk_model,
)
# Reuse the production sample-weighting logic verbatim (summary rows x0.5, etc.).
from scripts.predict.train_risk_model import _compute_sample_weights

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = 0.70


def hanley_mcneil_se(scores: np.ndarray, y: np.ndarray) -> float:
    """Standard error of an AUC via the Hanley-McNeil (1982) formula.

    SE = sqrt[(A(1-A) + (n_p-1)(Q1-A^2) + (n_n-1)(Q2-A^2)) / (n_p n_n)]
    with Q1 = A/(2-A), Q2 = 2A^2/(1+A).
    """
    y = np.asarray(y).astype(int)
    scores = np.asarray(scores, dtype=float)
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    a = float(roc_auc_score(y, scores))
    q1 = a / (2.0 - a)
    q2 = 2.0 * a * a / (1.0 + a)
    var = (
        a * (1.0 - a)
        + (n_pos - 1) * (q1 - a * a)
        + (n_neg - 1) * (q2 - a * a)
    ) / (n_pos * n_neg)
    return float(np.sqrt(max(var, 0.0)))


def bootstrap_auc_ci(
    scores: np.ndarray,
    y: np.ndarray,
    n_boot: int = 4000,
    seed: int = 42,
) -> tuple[float, float, float, int]:
    """Paired row bootstrap over held-out (y, score) pairs.

    Returns (ci_lo_2.5%, ci_hi_97.5%, P(AUC >= GATE), n_valid_resamples).
    Single-class resamples (no positive or no negative) are discarded.
    """
    y = np.asarray(y).astype(int)
    scores = np.asarray(scores, dtype=float)
    n = len(y)
    rng = np.random.default_rng(seed)
    aucs = np.empty(n_boot, dtype=float)
    valid = 0
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        yb = y[idx]
        if yb.min() == yb.max():
            continue
        aucs[valid] = roc_auc_score(yb, scores[idx])
        valid += 1
    aucs = aucs[:valid]
    if valid == 0:
        return float("nan"), float("nan"), float("nan"), 0
    lo, hi = np.percentile(aucs, [2.5, 97.5])
    p_ge_gate = float((aucs >= GATE).mean())
    return float(lo), float(hi), p_ge_gate, valid


def calibration_metrics(prob: np.ndarray, y: np.ndarray, n_bins: int = 10) -> dict:
    """Reliability of calibrated probabilities against outcomes.

    Returns Brier score, the base-rate reference Brier (a calibrated model must
    beat it), expected/maximum calibration error (ECE/MCE) over equal-width
    probability bins, and the per-bin reliability table.
    """
    prob = np.asarray(prob, dtype=float)
    y = np.asarray(y).astype(int)
    n = len(y)
    base_rate = float(y.mean())
    brier = float(np.mean((prob - y) ** 2))
    brier_baserate = float(np.mean((base_rate - y) ** 2))

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    # np.digitize: bin index 1..n_bins; clip the right edge into the last bin.
    idx = np.clip(np.digitize(prob, edges[1:-1], right=False), 0, n_bins - 1)
    ece = 0.0
    mce = 0.0
    table = []
    for b in range(n_bins):
        sel = idx == b
        n_b = int(sel.sum())
        if n_b == 0:
            continue
        conf = float(prob[sel].mean())
        acc = float(y[sel].mean())
        gap = abs(acc - conf)
        ece += (n_b / n) * gap
        mce = max(mce, gap)
        table.append({"bin": f"[{edges[b]:.1f},{edges[b+1]:.1f})",
                      "n": n_b, "mean_pred": conf, "obs_freq": acc})
    return {
        "n": n,
        "base_rate": base_rate,
        "brier": brier,
        "brier_baserate": brier_baserate,
        "ece": float(ece),
        "mce": float(mce),
        "reliability": table,
    }


def _load_config(model_config: str = "configs/soiling/model.yaml"):
    region_cfg = yaml.safe_load((REPO_ROOT / "configs/soiling/california.yaml").read_text())
    model_cfg = yaml.safe_load((REPO_ROOT / model_config).read_text())
    return region_cfg, model_cfg


def _preflight(paths: dict[str, Path]) -> None:
    """Fail on a missing hand-off artifact with an instruction, not a stack trace.

    Before this existed, a cold clone ran the very first command ONBOARDING asks for
    and died twelve frames deep in pandas.io.parquet on a FileNotFoundError, with no
    hint that the file is a deliberate hand-off artifact rather than something the
    newcomer broke. That is the worst possible first impression of a repo and it cost
    nothing to fix.
    """
    missing = {label: path for label, path in paths.items() if not path.exists()}
    if not missing:
        return
    lines = ["", "Cannot run: required hand-off artifacts are missing.", ""]
    for label, path in missing.items():
        try:
            shown = path.relative_to(REPO_ROOT)
        except ValueError:
            shown = path
        lines.append(f"  MISSING  {shown}   ({label})")
    lines += [
        "",
        "These are gitignored and are NOT produced by a clone. Get them with:",
        "",
        "    make check-data          # shows everything you are missing, by lane",
        "    # then follow the rclone pull in DATA.md",
        "",
        "Provenance and the regeneration command for every artifact: DATA.md.",
        "",
    ]
    raise SystemExit("\n".join(lines))


def _prepare(matrix_path: Path, feature_names_path: Path):
    _preflight({
        "cached training matrix": matrix_path,
        "feature list from the reference run": feature_names_path,
    })
    features = pd.read_parquet(matrix_path)
    feature_cols = json.loads(feature_names_path.read_text())
    missing = [c for c in feature_cols if c not in features.columns]
    if missing:
        raise ValueError(f"Feature matrix is missing {len(missing)} columns: {missing}")
    X = features[feature_cols].apply(pd.to_numeric, errors="coerce")
    y = features["label"].values.astype(int)
    return features, feature_cols, X, y


def _fit_and_score_holdout(
    X: pd.DataFrame,
    y: np.ndarray,
    features: pd.DataFrame,
    holdout_mask: np.ndarray,
    sample_weight: np.ndarray | None,
    model_cfg: dict,
    iwsr_thr: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Train on ~holdout_mask (production path); score the held-out rows.

    Mirrors the temporal-holdout branch of scripts/predict/train_risk_model.py
    (lines ~512-535): spatial-CV train_risk_model on the training years, then
    impute the held-out rows with the model's learned feature medians.

    Returns (raw_proba, calibrated_proba) for the held-out rows. The isotonic
    calibrator returned by train_risk_model is fit on the TRAINING years'
    out-of-fold scores, so applying it to a held-out year is a genuine
    out-of-year calibration test (does calibration survive temporal shift, not
    just ranking). AUC uses raw (rank-invariant); calibration uses the mapped
    probabilities. If no calibrator was produced, calibrated == raw.
    """
    cv_cfg = SpatialCVConfig(**model_cfg["spatial_cv"])
    X_train = X.loc[~holdout_mask].reset_index(drop=True)
    X_test = X.loc[holdout_mask].reset_index(drop=True)
    y_train = y[~holdout_mask]
    sw_train = sample_weight[~holdout_mask] if sample_weight is not None else None
    feats_train = features.loc[~holdout_mask].reset_index(drop=True)
    model, _metrics, calibrator = train_risk_model(
        X_train,
        y_train,
        lats=feats_train["latitude"].values,
        lons=feats_train["longitude"].values,
        xgb_params=model_cfg["xgboost"],
        cv=cv_cfg,
        target_mode=model_cfg.get("target_mode", "binary"),
        sample_weight=sw_train,
        iwsr_risk_threshold=iwsr_thr,
    )
    X_test_imp = impute_with_feature_medians(X_test, getattr(model, "feature_medians_", None))
    raw = model.predict_proba(X_test_imp)[:, 1]
    cal = np.clip(calibrator.predict(raw), 0.0, 1.0) if calibrator is not None else raw.copy()
    return raw, cal


def _fmt(v: float, nd: int = 3) -> str:
    return "  nan" if v != v else f"{v:.{nd}f}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--matrix", default="outputs/soiling/training_matrix.parquet")
    p.add_argument("--feature-names", default="runs/soiling/run_optionb/feature_names.json")
    p.add_argument("--model-config", default="configs/soiling/model.yaml",
                   help="Hyperparameter/CV config. Point at an alternative to evaluate a "
                        "different XGBoost block (e.g. the pre-regularization one) on the "
                        "same matrix.")
    p.add_argument("--n-boot", type=int, default=4000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--min-year-n", type=int, default=20,
                   help="Minimum held-out rows for a year to get its own reported CI.")
    p.add_argument("--out-json", default=None,
                   help="Write the pooled result (AUC/CI/calibration) to this path, e.g. "
                        "runs/soiling/run_optionb/holdout_ci.json, to record the gate.")
    args = p.parse_args()

    region_cfg, model_cfg = _load_config(args.model_config)
    iwsr_thr = float(region_cfg["iwsr_risk_threshold"])

    features, feature_cols, X, y = _prepare(
        REPO_ROOT / args.matrix, REPO_ROOT / args.feature_names
    )
    sample_weight = _compute_sample_weights(features, model_cfg.get("sample_weights", {}))

    is_summary = features.get("is_summary")
    if is_summary is None:
        is_summary = pd.Series(False, index=features.index)
    is_summary = is_summary.fillna(False).astype(bool).values
    years_all = features["year"].values.astype(int)

    # Panel years only (summary rows carry as_of=this-year and are not a real
    # observation year — they stay in every training set, never a holdout).
    panel_years = sorted({int(yv) for yv, s in zip(years_all, is_summary) if not s})

    print("=" * 78)
    print("Stage 2 soiling — year-holdout AUC with uncertainty")
    print(f"matrix           : {args.matrix}  ({len(features)} rows, "
          f"{int((~is_summary).sum())} panel + {int(is_summary.sum())} summary)")
    print(f"features         : {len(feature_cols)} (from {args.feature_names})")
    print(f"model config     : max_depth={model_cfg['xgboost'].get('max_depth')}, "
          f"reg_lambda={model_cfg['xgboost'].get('reg_lambda')}, "
          f"n_estimators={model_cfg['xgboost'].get('n_estimators')}")
    print(f"bootstrap        : {args.n_boot}x, seed={args.seed}, gate AUC>={GATE}")
    print("=" * 78)

    # ---- Per-year rolling leave-one-year-out holdout -----------------------
    header = (f"{'year':>6} {'n':>4} {'pos':>4} {'neg':>4} {'AUC':>7} "
              f"{'HM-SE':>7} {'95% CI (bootstrap)':>22} {'P>=.70':>7}")
    print("\nPer-year holdout (train on all OTHER years incl. summary rows, score the held-out year):")
    print(header)
    print("-" * len(header))

    pooled_scores = np.full(len(features), np.nan)
    pooled_cal = np.full(len(features), np.nan)
    for yr in panel_years:
        holdout_mask = (years_all == yr) & (~is_summary)
        n = int(holdout_mask.sum())
        y_h = y[holdout_mask]
        n_pos = int((y_h == 1).sum())
        n_neg = int((y_h == 0).sum())

        scores_h, cal_h = _fit_and_score_holdout(
            X, y, features, holdout_mask, sample_weight, model_cfg, iwsr_thr
        )
        pooled_scores[holdout_mask] = scores_h
        pooled_cal[holdout_mask] = cal_h

        if n_pos == 0 or n_neg == 0:
            print(f"{yr:>6} {n:>4} {n_pos:>4} {n_neg:>4} {'   —':>7} "
                  f"{'   —':>7} {'single-class (no AUC)':>22} {'   —':>7}")
            continue
        auc = float(roc_auc_score(y_h, scores_h))
        se = hanley_mcneil_se(scores_h, y_h)
        if n >= args.min_year_n:
            lo, hi, p_ge, _ = bootstrap_auc_ci(scores_h, y_h, args.n_boot, args.seed)
            ci = f"[{_fmt(lo)}, {_fmt(hi)}]"
            pstr = _fmt(p_ge, 2)
        else:
            ci = "(n<min; skipped)"
            pstr = "   —"
        print(f"{yr:>6} {n:>4} {n_pos:>4} {n_neg:>4} {_fmt(auc):>7} "
              f"{_fmt(se):>7} {ci:>22} {pstr:>7}")

    # ---- Pooled out-of-year AUC -------------------------------------------
    pooled_mask = ~np.isnan(pooled_scores)
    y_pool = y[pooled_mask]
    s_pool = pooled_scores[pooled_mask]
    n_pool = int(pooled_mask.sum())
    n_pos = int((y_pool == 1).sum())
    n_neg = int((y_pool == 0).sum())
    pooled_auc = float(roc_auc_score(y_pool, s_pool))
    pooled_se = hanley_mcneil_se(s_pool, y_pool)
    lo, hi, p_ge, nboot_valid = bootstrap_auc_ci(s_pool, y_pool, args.n_boot, args.seed)

    print("\n" + "=" * 78)
    print("POOLED out-of-year AUC (every panel row scored the year it was held out)")
    print("=" * 78)
    print(f"  n rows            : {n_pool}  ({n_pos} pos / {n_neg} neg)  "
          f"across {len(panel_years)} years {panel_years[0]}–{panel_years[-1]}")
    print(f"  point AUC         : {pooled_auc:.4f}")
    print(f"  Hanley-McNeil SE  : {pooled_se:.4f}")
    print(f"  normal-approx 95% : [{pooled_auc - 1.96*pooled_se:.3f}, "
          f"{pooled_auc + 1.96*pooled_se:.3f}]")
    print(f"  bootstrap 95% CI  : [{lo:.3f}, {hi:.3f}]  ({nboot_valid} valid resamples)")
    print(f"  P(AUC >= {GATE})     : {p_ge:.3f}  (bootstrap fraction)")
    print()
    if lo >= GATE:
        verdict = f"CLEARS: pooled out-of-year 95% CI lower bound {lo:.3f} >= {GATE}."
    elif hi < GATE:
        verdict = f"FAILS: pooled out-of-year 95% CI is entirely below {GATE} (upper {hi:.3f})."
    else:
        verdict = (f"STRADDLES {GATE}: pooled CI [{lo:.3f}, {hi:.3f}] contains the gate; "
                   f"point {pooled_auc:.3f}, P(>= {GATE})={p_ge:.2f}. "
                   f"Temporal generalization is not statistically distinguishable from the gate.")
    print("  VERDICT:", verdict)
    print("=" * 78)

    # ---- Pooled out-of-year CALIBRATION -----------------------------------
    # AUC above only proves ranking survives temporal shift. The product shows a
    # calibrated probability / dollar figure, so the gate also requires
    # "calibration retained": the isotonic map (fit on training-year OOF scores)
    # must stay reliable on held-out years.
    cal_pool = pooled_cal[pooled_mask]
    cal = calibration_metrics(cal_pool, y_pool, n_bins=10)
    print("\n" + "=" * 78)
    print("POOLED out-of-year CALIBRATION (isotonic map fit on training years, applied OOY)")
    print("=" * 78)
    print(f"  base rate (obs)   : {cal['base_rate']:.3f}")
    print(f"  Brier             : {cal['brier']:.4f}  (base-rate ref {cal['brier_baserate']:.4f}; "
          f"lower is better, must beat ref)")
    print(f"  ECE / MCE         : {cal['ece']:.4f} / {cal['mce']:.4f}  (expected/max calibration error)")
    print(f"  {'bin':>12} {'n':>5} {'mean_pred':>10} {'obs_freq':>9}")
    for r in cal["reliability"]:
        print(f"  {r['bin']:>12} {r['n']:>5} {r['mean_pred']:>10.3f} {r['obs_freq']:>9.3f}")
    beats_ref = cal["brier"] < cal["brier_baserate"]
    cal_ok = beats_ref and cal["ece"] < 0.10
    cal_verdict = (
        f"RETAINED: Brier {cal['brier']:.3f} beats base-rate {cal['brier_baserate']:.3f} "
        f"and ECE {cal['ece']:.3f} < 0.10 out-of-year."
        if cal_ok else
        f"DEGRADED: "
        + ("Brier does not beat base-rate. " if not beats_ref else "")
        + (f"ECE {cal['ece']:.3f} >= 0.10. " if cal['ece'] >= 0.10 else "")
        + "Calibrated probabilities drift across years — the dollar figure is affected."
    )
    print("\n  CALIBRATION VERDICT:", cal_verdict)
    print("=" * 78)

    if args.out_json:
        out_path = REPO_ROOT / args.out_json if not Path(args.out_json).is_absolute() else Path(args.out_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "method": "pooled leave-one-year-out (rolling) holdout; each panel row scored "
                      "the year it was held out; train on all other years incl. summary rows",
            "matrix": args.matrix,
            "feature_names": args.feature_names,
            "n_features": len(feature_cols),
            "model_config_path": args.model_config,
            "model_config": {
                "max_depth": model_cfg["xgboost"].get("max_depth"),
                "reg_lambda": model_cfg["xgboost"].get("reg_lambda"),
                "n_estimators": model_cfg["xgboost"].get("n_estimators"),
                "spatial_cv": model_cfg["spatial_cv"],
            },
            "n_boot": args.n_boot,
            "seed": args.seed,
            "panel_years": panel_years,
            "pooled_auc": pooled_auc,
            "pooled_n": n_pool,
            "pooled_pos": n_pos,
            "pooled_neg": n_neg,
            "hanley_mcneil_se": pooled_se,
            "bootstrap_ci95": [lo, hi],
            "p_auc_ge_gate": p_ge,
            "gate": GATE,
            "auc_verdict": verdict,
            "calibration": cal,
            "calibration_retained": bool(cal_ok),
        }
        out_path.write_text(json.dumps(payload, indent=2))
        print(f"\nWrote pooled result → {out_path}")


if __name__ == "__main__":
    main()
