"""Summarise the merged PVDAQ fleet labels: n, geography, and the NREL comparison.

A separate FILE rather than a heredoc, because `conda run ... python - <<'PY'` writes
nothing and reports success -- trap 1 in docs/HANDOFF_20260827.md, which the unattended
finisher hit: it merged and uploaded correctly and silently produced no summary.

Geography is the point. The label set this replaces is 81.0% west of -114 and 5.2%
east of -100, and a model trained on it scores 0.5313 on an unseen region.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

LABELS = Path("outputs/soiling/pvdaq_fleet_labels.json")
NREL = {"n": 1002, "pct_west_of_-114": 81.0, "pct_east_of_-100": 5.2}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--min-valid", type=int, default=5)
    ap.add_argument("--out-csv", type=Path,
                    default=Path("outputs/soiling/pvdaq_fleet_labels.csv"))
    ap.add_argument("--out-json", type=Path,
                    default=Path("outputs/soiling/pvdaq_fleet_summary.json"))
    args = ap.parse_args()

    results = json.load(open(args.labels))["results"]
    rows = []
    for r in results:
        pc = r.get("fits", {}).get("energy", {}).get("perfect_clean", {})
        if "loss_pct" not in pc or pc.get("degenerate", True):
            continue
        if pc.get("n_valid_intervals", 0) < args.min_valid:
            continue
        m = r["meta"]
        rows.append(dict(
            system_id=m["system_id"], lat=m["lat"], lon=m["lon"],
            climate=m["climate"], years=m.get("years"),
            capacity_kw=m.get("capacity_kw"), tilt=m.get("tilt"),
            azimuth=m.get("azimuth"), loss_pct=pc["loss_pct"],
            ci_lo=100 * (1 - pc["ci95"][1]), ci_hi=100 * (1 - pc["ci95"][0]),
            n_valid_intervals=pc["n_valid_intervals"],
            gamma_pdc=m.get("gamma_pdc"), gamma_tier=m.get("gamma_tier"),
        ))
    f = pd.DataFrame(rows)
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    f.to_csv(args.out_csv, index=False)

    west = 100 * (f.lon < -114).mean()
    east = 100 * (f.lon > -100).mean()
    summary = {
        "n_labels": int(len(f)),
        "n_systems_attempted": len(results),
        "koppen_classes": int(f.climate.nunique()),
        "pct_west_of_-114": round(float(west), 1),
        "pct_east_of_-100": round(float(east), 1),
        "loss_pct": {"p10": round(float(f.loss_pct.quantile(.1)), 2),
                     "p50": round(float(f.loss_pct.median()), 2),
                     "p90": round(float(f.loss_pct.quantile(.9)), 2)},
        "gamma_tier_counts": {k: int(v) for k, v in f.gamma_tier.value_counts().items()},
        "reference_nrel": NREL,
        "eastern_representation_vs_nrel": round(float(east) / NREL["pct_east_of_-100"], 1),
    }
    json.dump(summary, open(args.out_json, "w"), indent=2)

    print(f"labels {len(f)}   Koppen {summary['koppen_classes']}")
    print(f"  west of -114  {west:.1f}%   (NREL {NREL['pct_west_of_-114']}%)")
    print(f"  east of -100  {east:.1f}%   (NREL {NREL['pct_east_of_-100']}%)  "
          f"-> {summary['eastern_representation_vs_nrel']}x the eastern representation")
    print(f"  loss pts  p10 {summary['loss_pct']['p10']}  "
          f"p50 {summary['loss_pct']['p50']}  p90 {summary['loss_pct']['p90']}")
    print(f"  gamma tiers: {summary['gamma_tier_counts']}")
    print(f"\nwrote {args.out_csv}\nwrote {args.out_json}")


if __name__ == "__main__":
    main()
