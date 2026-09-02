"""Tests for src/risk/loss_model.py — the iwsr regression head and its intervals."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("xgboost")
pytest.importorskip("sklearn")

from risk.loss_model import (  # noqa: E402
    QUANTILES, align_features, conformal_offset, evaluate_spread, loss_pct_from_iwsr,
    spatial_clusters, train_loss_regressor,
)


def test_loss_pct_from_iwsr():
    got = loss_pct_from_iwsr(pd.Series([1.0, 0.95, 0.90]))
    assert got == pytest.approx([0.0, 5.0, 10.0])


def test_align_features_fills_with_training_medians():
    X = pd.DataFrame({"a": [1.0, None], "extra": [9, 9]})
    out = align_features(X, ["a", "b"], {"a": 5.0, "b": 7.0})
    assert list(out.columns) == ["a", "b"]          # reordered, "extra" dropped
    assert out["a"].tolist() == [1.0, 5.0]           # NaN -> median
    assert out["b"].tolist() == [7.0, 7.0]           # absent -> median


def test_spatial_clusters_group_nearby_points():
    lats = np.array([36.97, 36.971, 40.0])
    lons = np.array([-122.03, -122.031, -120.0])
    lab = spatial_clusters(lats, lons, cluster_km=10.0)
    assert lab[0] == lab[1] != lab[2]


# ── the metric that matters ───────────────────────────────────────────────────
def test_evaluate_spread_catches_a_flattened_distribution():
    """A model that predicts the mean for everyone must score spread_ratio ~ 0.

    This is the ``risk_score x 8`` failure mode in miniature: perfect on the centre,
    useless for a decision.
    """
    rng = np.random.default_rng(0)
    y = rng.gamma(2.0, 2.0, size=500)
    flat = np.full_like(y, float(np.median(y)))
    m = evaluate_spread(y, flat - 0.1, flat, flat + 0.1)
    assert m["spread_ratio"] < 0.02
    assert m["pi_coverage"] < 0.10


def test_evaluate_spread_rewards_a_faithful_distribution():
    rng = np.random.default_rng(1)
    y = rng.gamma(2.0, 2.0, size=500)
    m = evaluate_spread(y, y - 1.0, y, y + 1.0)     # perfect point prediction
    assert m["spread_ratio"] == pytest.approx(1.0, abs=1e-9)
    assert m["mae"] == pytest.approx(0.0, abs=1e-9)


def test_conformal_offset_restores_nominal_coverage():
    rng = np.random.default_rng(2)
    y = rng.normal(5.0, 2.0, size=2000)
    lo = np.full_like(y, 4.8)                        # far too narrow
    hi = np.full_like(y, 5.2)
    assert np.mean((y >= lo) & (y <= hi)) < 0.15
    off = conformal_offset(y, lo, hi, alpha=0.20)
    assert off > 0
    cov = np.mean((y >= lo - off) & (y <= hi + off))
    assert cov == pytest.approx(0.80, abs=0.03)


def test_conformal_offset_can_narrow_an_overwide_band():
    rng = np.random.default_rng(3)
    y = rng.normal(5.0, 1.0, size=2000)
    lo = np.full_like(y, -50.0)
    hi = np.full_like(y, 60.0)
    assert conformal_offset(y, lo, hi, alpha=0.20) < 0


def test_conformal_offset_handles_empty_input():
    assert conformal_offset(np.array([]), np.array([]), np.array([])) == 0.0


# ── end-to-end on a small synthetic problem ───────────────────────────────────
def _synthetic(n=400, seed=0):
    rng = np.random.default_rng(seed)
    lat = rng.uniform(33, 40, n)
    lon = rng.uniform(-123, -115, n)
    driver = (lon + 123) / 8.0                       # dry inland = higher loss
    loss = 1.0 + 8.0 * driver + rng.normal(0, 1.0, n)
    loss = np.clip(loss, 0.05, 24.0)
    return pd.DataFrame({
        "latitude": lat, "longitude": lon,
        "feat_a": driver + rng.normal(0, 0.05, n),
        "feat_b": rng.normal(0, 1, n),
        "iwsr": 1.0 - loss / 100.0,
    })


def test_train_produces_ordered_intervals_and_reasonable_spread():
    df = _synthetic()
    bundle = train_loss_regressor(df, ["feat_a", "feat_b"], n_folds=4, cluster_km=200.0)
    pred = bundle.predict(df)
    assert (pred["loss_pct_p10"] <= pred["loss_pct_p50"] + 1e-9).all()
    assert (pred["loss_pct_p50"] <= pred["loss_pct_p90"] + 1e-9).all()
    assert (pred["loss_pct_p10"] >= 0).all()
    # feat_a carries real signal, so out-of-fold spread must not collapse
    assert bundle.metrics["oof"]["spread_ratio"] > 0.5
    assert bundle.metrics["oof"]["spearman"] > 0.8


def test_conformal_calibration_is_stored_and_applied():
    df = _synthetic(seed=5)
    bundle = train_loss_regressor(df, ["feat_a", "feat_b"], n_folds=4, cluster_km=200.0)
    raw = bundle.predict(df, conformal=False)
    cal = bundle.predict(df, conformal=True)
    width_raw = (raw["loss_pct_p90"] - raw["loss_pct_p10"]).mean()
    width_cal = (cal["loss_pct_p90"] - cal["loss_pct_p10"]).mean()
    if bundle.conformal_offset_pct > 0:
        assert width_cal > width_raw
    # the point prediction is untouched by calibration
    assert cal["loss_pct_p50"].equals(raw["loss_pct_p50"])


def test_predictions_are_clamped_to_physical_range():
    df = _synthetic(seed=7)
    bundle = train_loss_regressor(df, ["feat_a", "feat_b"], n_folds=4, cluster_km=200.0)
    wild = pd.DataFrame({"feat_a": [1e6, -1e6], "feat_b": [1e6, -1e6]})
    pred = bundle.predict(wild)
    assert (pred.to_numpy() >= 0).all()
    assert (pred.to_numpy() <= 25.0).all()


def test_quantiles_are_the_documented_three():
    assert QUANTILES == (0.10, 0.50, 0.90)
