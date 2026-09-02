#!/usr/bin/env python3
"""When is a cleaning worth anything? Recovery vs cleaning date, from real weather.

Task 5 of the 2026-08-09 economics grounding pass. ``recovery_frac = 0.90`` claimed one
cleaning recovers 90% of a year's soiling loss. That is not physical: the array re-soils
within weeks, and in a coastal climate rain resets it for free. This script integrates
the SOMOSclean daily trajectory between a candidate cleaning date and the next natural
reset (``src/risk/recovery.py``) and prints the recovery calendar.

    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/recovery_calendar.py
    ... --lat 35.37 --lon -119.02 --label "Bakersfield"   # compare a drier site
    ... --k 30                                            # re-soiling time-constant sweep

Uses TWO years of weather and evaluates only the second: the trajectory starts at
eqD = 0, so without a spin-up year the early dates report a spuriously clean array and
therefore a spuriously low recovery.

Outputs under --out-dir (default outputs/economics/recovery/):
    recovery_calendar.csv   one row per candidate cleaning date
    summary.json            best/mean/worst + the constants this justifies
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))


def main(argv=None) -> int:
    import numpy as np
    import pandas as pd

    from risk.labels import somosclean_eqd_trajectory
    from risk.recovery import (
        DEFAULT_PARAMS, clearsky_daily_weight, recovery_fraction,
    )
    from risk.weather_client import fetch_combined

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, default=36.97)
    ap.add_argument("--lon", type=float, default=-122.03)
    ap.add_argument("--label", default="Santa Cruz CA (coastal)")
    ap.add_argument("--start", default="2024-08-01", help="first day of the SPIN-UP year")
    ap.add_argument("--end", default="2026-07-31", help="last day of the evaluation year")
    ap.add_argument("--eval-from", default="2025-08-01",
                    help="first candidate cleaning date (everything before is spin-up)")
    ap.add_argument("--stride-days", type=int, default=7)
    ap.add_argument("--k", type=float, default=None,
                    help="SOMOSclean re-soiling time constant (default 15, coastal-CA calibrated)")
    ap.add_argument("--efficacy", type=float, default=1.0,
                    help="cleaning efficacy; 0.70 approximates a light rinse")
    ap.add_argument("--no-production-weight", action="store_true")
    ap.add_argument("--out-dir", type=Path, default=REPO / "outputs/economics/recovery")
    ap.add_argument("--cache-dir", type=Path, default=REPO / ".cache/soiling")
    args = ap.parse_args(argv)

    params = dict(DEFAULT_PARAMS)
    if args.k is not None:
        params["k"] = args.k

    d0 = date.fromisoformat(args.start)
    d1 = date.fromisoformat(args.end)
    daily = fetch_combined(args.lat, args.lon, d0, d1, cache_dir=args.cache_dir)
    daily.index = pd.to_datetime(daily.index)

    weight = None if args.no_production_weight else clearsky_daily_weight(
        daily.index, lat=args.lat, lon=args.lon)

    _, sl = somosclean_eqd_trajectory(daily, **{k: params[k] for k in DEFAULT_PARAMS})
    evalyr = sl.loc[args.eval_from:]
    heavy = int((daily["precipitation_sum"].loc[args.eval_from:]
                 >= params["heavy_rain_mm"]).sum())

    print(f"=== {args.label} ({args.lat:.2f}, {args.lon:.2f}) ===")
    print(f"  weather {d0}..{d1}  |  evaluation year from {args.eval_from}")
    print(f"  heavy-rain (>= {params['heavy_rain_mm']:.0f} mm) days in eval year: {heavy}")
    print(f"  SOMOSclean k = {params['k']:.0f}, sl_sat = {params['sl_sat']:.3f}")
    print(f"  annual-mean soiling loss: {evalyr.mean()*100:.2f}%  (max {evalyr.max()*100:.2f}%)")
    print("    NREL measured reference — coastal CA p50 4.70% / p90 9.05%, all-station p50 3.00%")

    rows = []
    last = pd.Timestamp(daily.index[-1]) - pd.Timedelta(days=7)
    for ts in pd.date_range(args.eval_from, last, freq=f"{args.stride_days}D"):
        r = recovery_fraction(daily, ts.date(), params=params,
                              clean_efficacy=args.efficacy, production_weight=weight)
        rows.append({"clean_date": r["clean_date"],
                     "recovery_frac": round(r["recovery_frac"], 5),
                     "benefit_days": r["benefit_days"],
                     "days_to_next_heavy_rain": r["days_to_next_heavy_rain"],
                     "sl_at_clean_pct": round(r["sl_at_clean_pct"], 3)})

    fracs = [r["recovery_frac"] for r in rows]
    best = max(rows, key=lambda r: r["recovery_frac"])
    summary = {
        "label": args.label, "lat": args.lat, "lon": args.lon,
        "k": params["k"], "sl_sat": params["sl_sat"],
        "clean_efficacy": args.efficacy,
        "production_weighted": not args.no_production_weight,
        "heavy_rain_days_eval_year": heavy,
        "annual_mean_sl_pct": round(float(evalyr.mean() * 100), 3),
        "best_date": best["clean_date"],
        "best_recovery_frac": best["recovery_frac"],
        "best_benefit_days": best["benefit_days"],
        "mean_recovery_frac": round(float(np.mean(fracs)), 5),
        "worst_recovery_frac": round(float(np.min(fracs)), 5),
        "legacy_recovery_frac": 0.90,
        "overstatement_factor": round(0.90 / best["recovery_frac"], 1) if best["recovery_frac"] > 0 else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    print(f"\n  BEST cleaning date  {best['clean_date']}  recovery {best['recovery_frac']:.3f} "
          f"({best['benefit_days']} benefit days)")
    print(f"  mean over dates     {summary['mean_recovery_frac']:.3f}")
    print(f"  worst               {summary['worst_recovery_frac']:.3f}")
    print(f"  retired constant    0.900  -> overstated by {summary['overstatement_factor']}x")
    print("\n  month-by-month (first candidate of each month):")
    seen = set()
    for r in rows:
        m = r["clean_date"][:7]
        if m in seen:
            continue
        seen.add(m)
        bar = "#" * int(round(r["recovery_frac"] * 400))
        print(f"    {r['clean_date']}  {r['recovery_frac']:.3f}  "
              f"{r['benefit_days']:3d}d  {bar}")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = args.label.split()[0].lower()
    with (out / f"recovery_calendar_{stem}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (out / f"summary_{stem}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[ok] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
