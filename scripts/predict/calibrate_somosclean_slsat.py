"""Calibrate SOMOSclean sl_sat and k against measured NREL IWSR.

Fits sl_sat (saturation ceiling) and optionally k (time constant) by
minimizing RMSE between SOMOSclean predicted IWSR and NREL measured IWSR
for California stations.

Reports:
  - Best sl_sat / k from grid search
  - RMSE / MAE on all CA rows and coastal CA rows separately
  - Comparison of predicted vs measured soiling loss distribution

Usage:
    PYTHONPATH=. python scripts/predict/calibrate_somosclean_slsat.py
    PYTHONPATH=. python scripts/predict/calibrate_somosclean_slsat.py --region coastal
    PYTHONPATH=. python scripts/predict/calibrate_somosclean_slsat.py --fit-k
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from risk.labels import somosclean_eqd_trajectory
from risk.weather_client import fetch_combined

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

NREL_PATH = ROOT / "data/external/nrel_soiling_map_annual.csv"
CACHE_DIR = ROOT / ".cache/soiling"

# Default fixed params (not being calibrated)
_FIXED = dict(
    heavy_rain_mm=10.0,
    rain_min_mm=1.0,
    pm10_dust_threshold=50.0,
    pm10_dust_scale=0.02,
)

# Grid search ranges
SL_SAT_GRID = [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.15, 0.20, 0.25]
K_GRID = [15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0]


def load_nrel_ca(coastal_only: bool = False) -> pd.DataFrame:
    df = pd.read_csv(NREL_PATH)
    # Drop censored rows — no measured IWSR to calibrate against
    if "iwsr_censored" in df.columns:
        df = df[~df["iwsr_censored"].astype(bool)].reset_index(drop=True)
    # California by longitude
    df = df[df["longitude"].between(-124.5, -114)].reset_index(drop=True)
    if coastal_only:
        # Coastal: lon < -121, central CA latitude
        df = df[(df["longitude"] < -121) & (df["latitude"].between(36, 39))].reset_index(drop=True)
    log.info("Loaded %d NREL rows (coastal_only=%s)", len(df), coastal_only)
    return df


def fetch_weather_for_row(lat: float, lon: float, year: int) -> pd.DataFrame | None:
    as_of = date(year, 12, 31)
    start = as_of - timedelta(days=364)
    try:
        daily = fetch_combined(lat, lon, start, as_of, cache_dir=CACHE_DIR)
        return daily
    except Exception as exc:
        log.warning("Weather fetch failed for (%.3f, %.3f, %d): %s", lat, lon, year, exc)
        return None


def predict_iwsr(daily: pd.DataFrame, sl_sat: float, k: float) -> float:
    _, sl_series = somosclean_eqd_trajectory(daily, sl_sat=sl_sat, k=k, **_FIXED)
    return float(1.0 - sl_series.iloc[-1])


def run_grid_search(
    records: list[dict],
    sl_sat_grid: list[float],
    k_grid: list[float],
    fit_k: bool = False,
) -> pd.DataFrame:
    """Grid search over sl_sat (and optionally k). Returns results DataFrame."""
    results = []
    k_values = k_grid if fit_k else [30.0]

    for k in k_values:
        for sl_sat in sl_sat_grid:
            errors = []
            for r in records:
                pred_iwsr = predict_iwsr(r["daily"], sl_sat=sl_sat, k=k)
                errors.append(pred_iwsr - r["iwsr_measured"])
            errors = np.array(errors)
            results.append({
                "sl_sat": sl_sat,
                "k": k,
                "rmse": float(np.sqrt(np.mean(errors**2))),
                "mae": float(np.mean(np.abs(errors))),
                "bias": float(np.mean(errors)),   # positive = model predicts too clean
                "n": len(errors),
            })

    return pd.DataFrame(results).sort_values("rmse")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", choices=["all_ca", "coastal"], default="all_ca",
                    help="Which CA stations to calibrate against (default: all_ca)")
    ap.add_argument("--fit-k", action="store_true", help="Also grid-search over k (slower)")
    args = ap.parse_args(argv)

    coastal_only = args.region == "coastal"
    df = load_nrel_ca(coastal_only=coastal_only)

    log.info("Fetching weather for %d station-years...", len(df))
    records = []
    for _, row in df.iterrows():
        daily = fetch_weather_for_row(float(row["latitude"]), float(row["longitude"]), int(row["year"]))
        if daily is None:
            continue
        records.append({
            "station_id": row["station_id"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "year": int(row["year"]),
            "iwsr_measured": float(row["iwsr"]),
            "daily": daily,
        })

    log.info("%d / %d station-years fetched successfully", len(records), len(df))
    if not records:
        log.error("No weather data fetched — cannot calibrate")
        return

    log.info("Running grid search (fit_k=%s)...", args.fit_k)
    results = run_grid_search(records, SL_SAT_GRID, K_GRID, fit_k=args.fit_k)

    print("\n=== Grid search results (top 10 by RMSE) ===")
    print(results.head(10).to_string(index=False))

    best = results.iloc[0]
    print(f"\n=== Best parameters ===")
    print(f"  sl_sat = {best['sl_sat']:.3f}")
    print(f"  k      = {best['k']:.1f}")
    print(f"  RMSE   = {best['rmse']:.4f}  (in IWSR units; ×100 = % output)")
    print(f"  MAE    = {best['mae']:.4f}")
    print(f"  Bias   = {best['bias']:+.4f}  ({'model too clean' if best['bias'] > 0 else 'model too dirty'})")
    print(f"  n      = {int(best['n'])}")

    # Distribution comparison at best params
    print("\n=== Soiling loss distribution: measured vs predicted at best params ===")
    measured_loss = np.array([1.0 - r["iwsr_measured"] for r in records])
    pred_loss = np.array([
        1.0 - predict_iwsr(r["daily"], sl_sat=best["sl_sat"], k=best["k"])
        for r in records
    ])
    for pct, label in [(50, "median"), (75, "p75"), (90, "p90"), (95, "p95"), (100, "max")]:
        m = np.percentile(measured_loss, pct)
        p = np.percentile(pred_loss, pct)
        print(f"  {label:6s}  measured={m:.3f} ({m*100:.1f}%)   predicted={p:.3f} ({p*100:.1f}%)")

    # Current defaults comparison
    print("\n=== Current defaults (sl_sat=0.10, k=30) for reference ===")
    cur = results[(results["sl_sat"] == 0.10) & (results["k"] == 30.0)]
    if not cur.empty:
        c = cur.iloc[0]
        print(f"  RMSE={c['rmse']:.4f}  MAE={c['mae']:.4f}  Bias={c['bias']:+.4f}")

    print(f"\n=== Recommended update ===")
    print(f"  Replace sl_sat=0.10 → sl_sat={best['sl_sat']:.2f} in:")
    print(f"    src/risk/physics_score.py  (_DEFAULT_PARAMS)")
    print(f"    src/risk/labels.py         (somosclean_panel_labels, somosclean_synthetic_labels)")
    if args.fit_k and best["k"] != 30.0:
        print(f"  Replace k=30.0 → k={best['k']:.1f} in the same locations")


if __name__ == "__main__":
    main()
