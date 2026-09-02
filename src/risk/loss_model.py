"""Regression head: predict ANNUAL SOILING-LOSS PERCENT, with a prediction interval.

Replaces ``economics.RISK_TO_LOSS_PCT`` — the constant 8.0 that multiplied a calibrated
*classification probability* by 8 to manufacture a loss percentage. That was a category
error twice over:

1. **A probability is not a magnitude.** ``P(this station-year is a high-soiling one)``
   answers a different question from ``how much did it lose?``. The classifier is trained
   to rank, and a calibrated ranker is free to compress magnitude information away
   entirely so long as the ordering survives.
2. **It destroys spread, which is the only thing the decision depends on.** Measured NREL
   annual losses run p10 1.10% / p50 3.00% / p90 6.70% (n=891) — a 5.6-point p10-p90
   span. Pushed through ``risk x 8``, the Santa Cruz AOI came out p10 4.45 / p50 4.45 /
   p90 5.06: a **0.6-point** span. Since the professional-clean breakeven sits at ~6.7%
   loss for a 5 kW system, a distribution that never reaches 6.7% recommends
   ``no_clean`` for essentially every array. **The collapsed variance, not the centre,
   is what made the product look uneconomic.**

This module trains directly on the measured target and reports its own spread.

TARGET.  ``loss_pct = (1 - iwsr) * 100`` from ``data/external/nrel_soiling_map_annual.csv``
(insolation-weighted soiling ratio; 891 station-years, 146 stations, 6 states, 2008-2022).

INTERVALS.  Three XGBoost quantile heads (``reg:quantileerror``, alpha = 0.10/0.50/0.90)
rather than one mean head plus a residual sigma. Soiling loss is right-skewed and
heteroscedastic — dry inland sites have both a higher mean and a wider spread — so a
symmetric interval around a mean would be wrong in both tails. The p50 head is the point
prediction; p10/p90 bracket it.

SPREAD VALIDATION IS A FIRST-CLASS METRIC HERE.  :func:`evaluate_spread` reports the
ratio of predicted to measured p10-p90 span on held-out folds. A model that nails the
mean and flattens the spread is useless for this product even at a good AUC, so
``spread_ratio`` is reported next to the accuracy numbers and gated on in the training
script. Note that *some* shrinkage is correct and unavoidable: a conditional-mean
prediction is legitimately narrower than the marginal distribution, because the
irreducible station-year noise is not predictable. The quantile heads are what let us
recover a defensible *predictive* spread on top of the conditional one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

#: Quantiles the three heads are trained at. p50 is the point estimate.
QUANTILES: tuple[float, float, float] = (0.10, 0.50, 0.90)

#: Physical clamp on predictions. Soiling loss cannot be negative; the upper bound is
#: set above the measured NREL maximum (22.9%) so it only ever catches extrapolation
#: blow-ups, never a real value.
LOSS_PCT_MIN = 0.0
LOSS_PCT_MAX = 25.0


def loss_pct_from_iwsr(iwsr: pd.Series | np.ndarray) -> np.ndarray:
    """Annual soiling-loss percent from the insolation-weighted soiling ratio."""
    return (1.0 - np.asarray(iwsr, dtype=float)) * 100.0


def conformal_offset(y_true: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                     alpha: float = 0.20) -> float:
    """Split-conformal (CQR) widening needed for [lo, hi] to reach 1-alpha coverage.

    Conformalized Quantile Regression, Romano, Patterson & Candès (NeurIPS 2019). The
    conformity score is the signed distance outside the predicted band,

        E_i = max(lo_i - y_i,  y_i - hi_i)

    (negative when the truth is comfortably inside). Widening both bounds by the
    ``ceil((n+1)(1-alpha))/n`` empirical quantile of E gives finite-sample marginal
    coverage >= 1-alpha under exchangeability alone — no distributional assumption.

    Why this is needed here: the raw quantile heads are fitted to reproduce *training*
    quantiles, so on a held-out SPATIAL cluster — a genuinely harder task than a random
    held-out row — they systematically under-cover. Measured on this data the raw band
    covered 0.558 against a nominal 0.80. Calibrating on out-of-fold predictions, which
    are produced under exactly the deployment condition (unseen cluster), transfers that
    difficulty into the interval width instead of hiding it.

    Note the offset can be NEGATIVE if the raw band over-covers, which correctly narrows it.
    """
    y = np.asarray(y_true, dtype=float)
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    ok = np.isfinite(y) & np.isfinite(lo) & np.isfinite(hi)
    y, lo, hi = y[ok], lo[ok], hi[ok]
    n = y.size
    if n == 0:
        return 0.0
    scores = np.maximum(lo - y, y - hi)
    # Finite-sample conformal rank; clipped so it stays a valid quantile level.
    level = min(1.0, np.ceil((n + 1) * (1.0 - alpha)) / n)
    return float(np.quantile(scores, level, method="higher"))


@dataclass
class LossModelBundle:
    """Three fitted quantile heads plus everything needed to score new rows."""

    models: dict[float, object]
    feature_names: list[str]
    feature_medians: dict[str, float]
    metrics: dict = field(default_factory=dict)
    #: CQR widening (percentage points) added to each side of the p10-p90 band.
    #: Calibrated on out-of-fold predictions; 0.0 means uncalibrated.
    conformal_offset_pct: float = 0.0

    def predict(self, X: pd.DataFrame, *, conformal: bool = True) -> pd.DataFrame:
        """Return a frame with ``loss_pct_p10 / loss_pct_p50 / loss_pct_p90``.

        Quantile heads are fitted independently, so nothing forces p10 <= p50 <= p90 on
        an unseen row. We sort the three predictions per row rather than leave a crossed
        interval, which would produce a negative-width band downstream.

        With ``conformal=True`` (default) the band is widened by the calibrated CQR
        offset so its coverage matches the nominal 80%. Pass ``False`` to inspect the
        raw heads.
        """
        Xa = align_features(X, self.feature_names, self.feature_medians)
        cols = {}
        for q in QUANTILES:
            raw = np.asarray(self.models[q].predict(Xa), dtype=float)
            cols[q] = np.clip(raw, LOSS_PCT_MIN, LOSS_PCT_MAX)
        stacked = np.sort(np.column_stack([cols[q] for q in QUANTILES]), axis=1)
        lo, mid, hi = stacked[:, 0], stacked[:, 1], stacked[:, 2]
        if conformal and self.conformal_offset_pct:
            lo = lo - self.conformal_offset_pct
            hi = hi + self.conformal_offset_pct
        return pd.DataFrame(
            {"loss_pct_p10": np.clip(lo, LOSS_PCT_MIN, LOSS_PCT_MAX),
             "loss_pct_p50": mid,
             "loss_pct_p90": np.clip(hi, LOSS_PCT_MIN, LOSS_PCT_MAX)},
            index=X.index,
        )


def align_features(X: pd.DataFrame, feature_names: Sequence[str],
                   medians: Mapping[str, float] | None) -> pd.DataFrame:
    """Reindex to the training feature order, filling gaps with training medians."""
    med = dict(medians or {})
    out = X.reindex(columns=list(feature_names))
    for c in out.columns:
        if c in med:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(med[c])
        else:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def spatial_clusters(lats: np.ndarray, lons: np.ndarray, cluster_km: float = 10.0) -> np.ndarray:
    """Greedy lat/lon bucketing → integer cluster labels.

    Mirrors ``risk_model._spatial_clusters`` so regression folds are comparable to the
    classifier's. Soiling is spatially autocorrelated; a random KFold would leak
    neighbouring stations across folds and flatter the model.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    deg_lat = cluster_km / 111.0
    deg_lon = cluster_km / (111.0 * max(0.1, np.cos(np.deg2rad(np.nanmean(lats)))))
    lat_bin = np.floor(lats / deg_lat).astype(int)
    lon_bin = np.floor(lons / deg_lon).astype(int)
    _, labels = np.unique(np.column_stack([lat_bin, lon_bin]), axis=0, return_inverse=True)
    return labels


def _make_head(quantile: float, params: Mapping) -> object:
    import xgboost as xgb

    p = dict(params)
    p.update(objective="reg:quantileerror", quantile_alpha=quantile)
    return xgb.XGBRegressor(**p)


def evaluate_spread(y_true: np.ndarray, p10: np.ndarray, p50: np.ndarray,
                    p90: np.ndarray) -> dict:
    """Does the predicted distribution have the measured distribution's SPREAD?

    Three distinct questions, all reported because they fail differently:

    * ``spread_ratio`` — predicted p10-p90 span of the POINT predictions vs the measured
      span. This is the number that killed the old ``risk x 8`` path (0.6 / 5.6 = 0.11).
      Cannot legitimately reach 1.0 (see module docstring) but must not collapse.
    * ``pi_coverage`` — fraction of held-out truths inside [p10, p90]. Nominal 0.80. This
      is the honest test of the interval, and it is what the per-array band is sold on.
    * ``pi_width_*`` — the interval has to be narrow enough to be actionable as well as
      wide enough to be honest.
    """
    y = np.asarray(y_true, dtype=float)
    ok = np.isfinite(y)
    y, p10, p50, p90 = y[ok], np.asarray(p10)[ok], np.asarray(p50)[ok], np.asarray(p90)[ok]
    if y.size == 0:
        return {}
    meas_span = float(np.percentile(y, 90) - np.percentile(y, 10))
    pred_span = float(np.percentile(p50, 90) - np.percentile(p50, 10))
    return {
        "n": int(y.size),
        "measured_p10": float(np.percentile(y, 10)),
        "measured_p50": float(np.percentile(y, 50)),
        "measured_p90": float(np.percentile(y, 90)),
        "measured_sd": float(np.std(y, ddof=1)) if y.size > 1 else 0.0,
        "pred_p10": float(np.percentile(p50, 10)),
        "pred_p50": float(np.percentile(p50, 50)),
        "pred_p90": float(np.percentile(p50, 90)),
        "pred_sd": float(np.std(p50, ddof=1)) if p50.size > 1 else 0.0,
        "measured_p10_p90_span": meas_span,
        "pred_p10_p90_span": pred_span,
        "spread_ratio": (pred_span / meas_span) if meas_span > 0 else float("nan"),
        "sd_ratio": (float(np.std(p50, ddof=1)) / float(np.std(y, ddof=1)))
                    if y.size > 1 and np.std(y, ddof=1) > 0 else float("nan"),
        "pi_coverage": float(np.mean((y >= p10) & (y <= p90))),
        "pi_width_mean": float(np.mean(p90 - p10)),
        "pi_width_median": float(np.median(p90 - p10)),
        "mae": float(np.mean(np.abs(p50 - y))),
        "rmse": float(np.sqrt(np.mean((p50 - y) ** 2))),
        "bias": float(np.mean(p50 - y)),
        # Spearman on the point prediction — comparable to the classifier's ranking job.
        "spearman": float(pd.Series(p50).corr(pd.Series(y), method="spearman")),
    }


def train_loss_regressor(
    df: pd.DataFrame,
    feature_names: Sequence[str],
    *,
    target_col: str = "iwsr",
    lat_col: str = "latitude",
    lon_col: str = "longitude",
    params: Mapping | None = None,
    n_folds: int = 5,
    cluster_km: float = 10.0,
    sample_weight: np.ndarray | None = None,
) -> LossModelBundle:
    """Fit the three quantile heads with spatial CV, returning fold + full-fit metrics."""
    from sklearn.model_selection import GroupKFold

    base = dict(n_estimators=400, max_depth=4, learning_rate=0.05, subsample=0.8,
                colsample_bytree=0.8, reg_lambda=5.0, random_state=42)
    base.update(params or {})

    y = loss_pct_from_iwsr(df[target_col])
    keep = np.isfinite(y)
    df, y = df.loc[keep].copy(), y[keep]
    if sample_weight is not None:
        sample_weight = np.asarray(sample_weight, dtype=float)[keep]

    medians = {c: float(pd.to_numeric(df[c], errors="coerce").median())
               for c in feature_names if c in df.columns}
    X = align_features(df, feature_names, medians)

    clusters = spatial_clusters(df[lat_col].to_numpy(), df[lon_col].to_numpy(), cluster_km)
    n_unique = len(np.unique(clusters))
    oof = {q: np.full(len(df), np.nan) for q in QUANTILES}

    if n_unique < n_folds:
        logger.warning("Only %d spatial clusters for %d folds — skipping CV", n_unique, n_folds)
        fold_metrics: list[dict] = []
    else:
        gkf = GroupKFold(n_splits=n_folds)
        fold_metrics = []
        for fold, (tr, te) in enumerate(gkf.split(X, y, groups=clusters)):
            preds = {}
            for q in QUANTILES:
                m = _make_head(q, base)
                m.fit(X.iloc[tr], y[tr],
                      sample_weight=None if sample_weight is None else sample_weight[tr])
                p = np.clip(np.asarray(m.predict(X.iloc[te]), dtype=float),
                            LOSS_PCT_MIN, LOSS_PCT_MAX)
                preds[q] = p
                oof[q][te] = p
            stacked = np.sort(np.column_stack([preds[q] for q in QUANTILES]), axis=1)
            fm = evaluate_spread(y[te], stacked[:, 0], stacked[:, 1], stacked[:, 2])
            fm["fold"] = fold
            fm["n_test_clusters"] = int(len(np.unique(clusters[te])))
            fold_metrics.append(fm)
            logger.info("fold %d  n=%d  MAE %.2f  spread_ratio %.2f  coverage %.2f",
                        fold, fm["n"], fm["mae"], fm["spread_ratio"], fm["pi_coverage"])

    # Out-of-fold pooled metrics — the honest headline (every row scored by a model
    # that never saw its spatial cluster).
    oof_stacked = np.sort(np.column_stack([oof[q] for q in QUANTILES]), axis=1)
    have = np.isfinite(oof_stacked).all(axis=1)
    oof_metrics = (evaluate_spread(y[have], oof_stacked[have, 0], oof_stacked[have, 1],
                                   oof_stacked[have, 2]) if have.any() else {})

    # Calibrate the interval on the out-of-fold predictions — the only sample produced
    # under the deployment condition (a spatial cluster the model never saw).
    offset = (conformal_offset(y[have], oof_stacked[have, 0], oof_stacked[have, 2],
                               alpha=1.0 - (QUANTILES[2] - QUANTILES[0]))
              if have.any() else 0.0)
    oof_conformal = (evaluate_spread(y[have], oof_stacked[have, 0] - offset,
                                     oof_stacked[have, 1], oof_stacked[have, 2] + offset)
                     if have.any() else {})

    # Refit each head on everything for the shipped artifact.
    final = {}
    for q in QUANTILES:
        m = _make_head(q, base)
        m.fit(X, y, sample_weight=sample_weight)
        final[q] = m

    metrics = {
        "target": "annual_soiling_loss_pct = (1 - iwsr) * 100",
        "n_rows": int(len(df)),
        "n_spatial_clusters": int(n_unique),
        "cluster_km": cluster_km,
        "quantiles": list(QUANTILES),
        "params": base,
        "oof": oof_metrics,
        "oof_conformal": oof_conformal,
        "conformal_offset_pct": offset,
        "folds": fold_metrics,
    }
    return LossModelBundle(models=final, feature_names=list(feature_names),
                           feature_medians=medians, metrics=metrics,
                           conformal_offset_pct=offset)


def save_bundle(bundle: LossModelBundle, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for q, m in bundle.models.items():
        m.save_model(out_dir / f"loss_q{int(q * 100):02d}.ubj")
    (out_dir / "feature_names.json").write_text(json.dumps(bundle.feature_names, indent=2))
    (out_dir / "feature_medians.json").write_text(json.dumps(bundle.feature_medians, indent=2))
    (out_dir / "metrics.json").write_text(json.dumps(bundle.metrics, indent=2, default=float))
    (out_dir / "conformal.json").write_text(
        json.dumps({"conformal_offset_pct": bundle.conformal_offset_pct,
                    "nominal_coverage": QUANTILES[2] - QUANTILES[0]}, indent=2))
    return out_dir


def load_bundle(model_dir: Path) -> LossModelBundle:
    import xgboost as xgb

    model_dir = Path(model_dir)
    models = {}
    for q in QUANTILES:
        m = xgb.XGBRegressor()
        m.load_model(model_dir / f"loss_q{int(q * 100):02d}.ubj")
        models[q] = m
    names = json.loads((model_dir / "feature_names.json").read_text())
    medians = json.loads((model_dir / "feature_medians.json").read_text())
    metrics_path = model_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else {}
    conf_path = model_dir / "conformal.json"
    offset = (json.loads(conf_path.read_text()).get("conformal_offset_pct", 0.0)
              if conf_path.is_file() else 0.0)
    return LossModelBundle(models=models, feature_names=names, feature_medians=medians,
                           metrics=metrics, conformal_offset_pct=float(offset))
