"""Feature-group ablation for the Stage 2 soiling risk model.

Answers "how much of the model's skill comes from the Kimber physics prior
versus the learned weather/AQ/static features?" on the CURRENT patched pipeline
(per-fold median imputation, 10 km GroupKFold), so the numbers are comparable to
production rather than to the pre-patch 2026-04-23 runs.

Every variant goes through the exact production training path
(`train_risk_model` + `SpatialCVConfig` + `_compute_sample_weights`) against the
CACHED training matrix — no Open-Meteo refetch, so this never touches the quota
wall. Only the feature list and the XGBoost hyperparameters vary.

Each variant is saved as a normal run dir under runs/soiling/<name>/ so that
scripts/predict/holdout_ci.py can be pointed at its feature_names.json to get
the pooled leave-one-year-out AUC alongside the spatial-CV mean.

Run:
  PYTHONPATH=. conda run -n solar-soiling python scripts/predict/ablate_features.py --all
  PYTHONPATH=. conda run -n solar-soiling python scripts/predict/ablate_features.py --variant abl_no_kimber
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.risk.risk_model import SpatialCVConfig, save_model, train_risk_model
# Reuse the production sample-weighting logic verbatim (summary rows x0.5, etc.).
from scripts.predict.train_risk_model import _compute_sample_weights

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

# The physics priors, as emitted by feature_engineering.build_feature_row.
KIMBER_FEATURES = [
    "kimber_iwsr_proxy",
    "kimber_iwsr_7d_mean",
    "kimber_iwsr_30d_mean",
    "kimber_iwsr_90d_mean",
]
SOMOSCLEAN_FEATURES = [
    "somosclean_sl_proxy",
    "somosclean_sl_7d_mean",
    "somosclean_sl_30d_mean",
]

# The 2026-04-23 nrel_kimber_feature set (the historical 0.684 run), minus its
# `as_of` column: that is a date string, so pd.to_numeric coerced it to all-NaN
# and median imputation collapsed it to a constant — a dead column either way.
OLD29_FEATURES = [
    "elevation_m", "nlcd_class", "distance_to_highway_m", "distance_to_agriculture_m",
    "precip_7d_mm", "precip_30d_mm", "precip_90d_mm",
    "dry_day_streak", "days_since_rain_5mm",
    "wind_speed_10m_max_7d_mean", "wind_speed_10m_max_30d_mean", "wind_speed_10m_max_90d_mean",
    "relative_humidity_2m_mean_7d_mean", "relative_humidity_2m_mean_30d_mean",
    "relative_humidity_2m_mean_90d_mean",
    "temperature_2m_max_7d_mean", "temperature_2m_max_30d_mean", "temperature_2m_max_90d_mean",
    "pm2_5_7d_mean", "pm2_5_30d_mean", "pm2_5_90d_mean",
    "pm10_7d_mean", "pm10_30d_mean", "pm10_90d_mean",
] + KIMBER_FEATURES

# Pre-regularization XGBoost block (configs/soiling/model.yaml before ad947bf,
# 2026-07-05): depth 5, XGBoost-default reg_lambda=1.
OLD_XGB_PARAMS = {
    "n_estimators": 200,
    "max_depth": 5,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
}

# name -> (base feature set, features to drop, hyperparameter set, description)
#   base "full"  = run_optionb's 40-feature list
#   base "old29" = the 2026-04-23 feature list above
#   hp   "new"   = current configs/soiling/model.yaml (depth 4, reg_lambda 5)
#   hp   "old"   = OLD_XGB_PARAMS
VARIANTS: dict[str, dict] = {
    "abl_full40": dict(
        base="full", drop=[], hp="new",
        desc="run_optionb 40-feature set, current model.yaml — reference"),
    "abl_no_kimber": dict(
        base="full", drop=KIMBER_FEATURES, hp="new",
        desc="reference minus the 4 Kimber IWSR features"),
    "abl_no_physics": dict(
        base="full", drop=KIMBER_FEATURES + SOMOSCLEAN_FEATURES, hp="new",
        desc="reference minus ALL physics priors (Kimber + SOMOSclean)"),
    "abl_physics_only": dict(
        base="full", keep=KIMBER_FEATURES + SOMOSCLEAN_FEATURES, hp="new",
        desc="physics priors alone (Kimber + SOMOSclean), no learned features"),
    "abl_kimber_only": dict(
        base="full", keep=KIMBER_FEATURES, hp="new",
        desc="the 4 Kimber features alone"),
    "abl_full40_oldhp": dict(
        base="full", drop=[], hp="old",
        desc="reference feature set under PRE-regularization hyperparameters"),
    "abl_old28_newhp": dict(
        base="old29", drop=[], hp="new",
        desc="2026-04-23 feature set under CURRENT hyperparameters"),
    "abl_old28_oldhp": dict(
        base="old29", drop=[], hp="old",
        desc="2026-04-23 feature set under PRE-regularization hyperparameters"),
}


def _resolve_features(spec: dict, full_cols: list[str]) -> list[str]:
    base = full_cols if spec["base"] == "full" else list(OLD29_FEATURES)
    if "keep" in spec:
        return [c for c in base if c in set(spec["keep"])]
    drop = set(spec.get("drop", []))
    return [c for c in base if c not in drop]


def run_variant(
    name: str,
    spec: dict,
    features: pd.DataFrame,
    full_cols: list[str],
    model_cfg: dict,
    iwsr_thr: float,
    seeds: list[int],
    save: bool,
) -> dict:
    """Spatial-CV a single feature/hyperparameter variant on the cached matrix."""
    cols = _resolve_features(spec, full_cols)
    missing = [c for c in cols if c not in features.columns]
    if missing:
        raise ValueError(f"{name}: matrix is missing {missing}")

    X = features[cols].apply(pd.to_numeric, errors="coerce")
    y = features["label"].values.astype(int)
    sample_weight = _compute_sample_weights(features, model_cfg.get("sample_weights", {}))
    cv_cfg = SpatialCVConfig(**model_cfg["spatial_cv"])
    base_params = dict(model_cfg["xgboost"]) if spec["hp"] == "new" else dict(OLD_XGB_PARAMS)

    per_seed: list[float] = []
    primary = None
    for seed in seeds:
        params = {**base_params, "random_state": seed}
        model, metrics, calibrator = train_risk_model(
            X, y,
            lats=features["latitude"].values,
            lons=features["longitude"].values,
            xgb_params=params,
            cv=cv_cfg,
            target_mode=model_cfg.get("target_mode", "binary"),
            sample_weight=sample_weight,
            iwsr_risk_threshold=iwsr_thr,
        )
        per_seed.append(float(metrics["mean_auc"]))
        if primary is None:  # seeds[0] is the reported run
            primary = (model, metrics, calibrator)

    model, metrics, calibrator = primary
    metrics = dict(metrics)
    metrics.update({
        "variant": name,
        "description": spec["desc"],
        "n_features": len(cols),
        "hyperparameters": base_params,
        "seed_primary": seeds[0],
        "seeds": seeds,
        "mean_auc_per_seed": per_seed,
        "mean_auc_seedmean": float(np.mean(per_seed)),
        "mean_auc_seedsd": float(np.std(per_seed, ddof=1)) if len(per_seed) > 1 else 0.0,
        "source": "cached outputs/soiling/training_matrix.parquet (no weather refetch); "
                  "production train_risk_model path",
    })
    if save:
        out_dir = REPO_ROOT / "runs" / "soiling" / name
        save_model(model, out_dir, feature_names=cols, metrics=metrics, calibrator=calibrator)
        logger.warning("saved %s (%d features) → %s", name, len(cols), out_dir)
    return metrics


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--matrix", default="outputs/soiling/training_matrix.parquet")
    p.add_argument("--reference-features", default="runs/soiling/run_optionb/feature_names.json",
                   help="Feature list defining the 'full' base set.")
    p.add_argument("--model-config", default="configs/soiling/model.yaml")
    p.add_argument("--region-config", default="configs/soiling/california.yaml")
    p.add_argument("--variant", action="append", default=None,
                   help="Variant name (repeatable). Default: all.")
    p.add_argument("--all", action="store_true", help="Run every variant.")
    p.add_argument("--seeds", default="42,0,1,2,3,4,5",
                   help="Comma-separated XGBoost seeds; the first is the reported run.")
    p.add_argument("--no-save", action="store_true", help="Skip writing runs/soiling/<name>/.")
    p.add_argument("--out-json", default=None, help="Write the summary table to this path.")
    args = p.parse_args()

    region_cfg = yaml.safe_load((REPO_ROOT / args.region_config).read_text())
    model_cfg = yaml.safe_load((REPO_ROOT / args.model_config).read_text())
    iwsr_thr = float(region_cfg["iwsr_risk_threshold"])
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    features = pd.read_parquet(REPO_ROOT / args.matrix)
    full_cols = json.loads((REPO_ROOT / args.reference_features).read_text())
    full_cols = [c for c in full_cols if c in features.columns]

    names = list(VARIANTS) if (args.all or not args.variant) else args.variant
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise SystemExit(f"Unknown variant(s): {unknown}. Known: {list(VARIANTS)}")

    print("=" * 96)
    print("Stage 2 soiling — feature-group ablation (current patched pipeline)")
    print(f"matrix   : {args.matrix}  ({len(features)} rows, "
          f"{int((~features['is_summary'].fillna(False)).sum())} panel + "
          f"{int(features['is_summary'].fillna(False).sum())} summary)")
    print(f"spatial CV: {model_cfg['spatial_cv']['n_folds']} folds, "
          f"cluster_km={model_cfg['spatial_cv']['cluster_km']}, "
          f"per-fold median imputation (patched)")
    print(f"seeds    : {seeds}  (first = reported)")
    print("=" * 96)

    results = []
    for name in names:
        res = run_variant(name, VARIANTS[name], features, full_cols,
                          model_cfg, iwsr_thr, seeds, save=not args.no_save)
        results.append(res)

    hdr = (f"{'variant':>18} {'nfeat':>6} {'hp':>4} {'CV AUC':>8} "
           f"{'seed-mean':>10} {'sd':>7}   description")
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in results:
        hp = "d4L5" if r["hyperparameters"].get("reg_lambda") == 5.0 else "d5L1"
        print(f"{r['variant']:>18} {r['n_features']:>6} {hp:>4} {r['mean_auc']:>8.4f} "
              f"{r['mean_auc_seedmean']:>10.4f} {r['mean_auc_seedsd']:>7.4f}   {r['description']}")

    if args.out_json:
        out_path = Path(args.out_json)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2))
        print(f"\nWrote summary → {out_path}")


if __name__ == "__main__":
    main()
