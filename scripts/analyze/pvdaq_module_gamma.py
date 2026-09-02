"""Resolve gamma_pdc per PVDAQ system, and report how far it moves from the old constant.

Closes the open problem in `docs/HANDOFF_20260827.md`: every recorded PVDAQ soiling
label was produced with `GAMMA_PDC` hardcoded fleet-wide at -0.0045, unvalidated, and
the two scripts in the tree disagreed by 0.0010 -- a gap worth 1.045 pts of annual
soiling label on system 10109, against a 0.72 pt method-noise SD.

This does not argue about the constant. It looks the coefficient up per system from
PVDAQ's own module metadata against pvlib's CEC database (see `src/risk/module_gamma.py`
for the resolution ladder), then reports:

  * the fallback rate at every tier, so the residual guess is sized rather than hidden
  * the fleet distribution of the resolved coefficient
  * `delta_vs_old`, the per-system distance from the -0.0045 the labels actually used

The third is the number that decides whether the recorded labels have to be thrown
away. A fleet whose resolved coefficients cluster tightly on -0.0045 means the old
constant was a good central value and the labels survive; a wide spread means they do not.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_module_gamma.py \
        --out-json outputs/soiling/module_gamma.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.risk.module_gamma import (
    FLEET_DEFAULT_GAMMA, fetch_system_metadata, primary_module, resolve_gamma,
)

SYSTEMS_CSV = "https://oedi-data-lake.s3.amazonaws.com/pvdaq/csv/systems_20250729.csv"
OLD_CONSTANT = -0.0045  # what pvdaq_daily_srr_probe.py used for every recorded label


def build_table(max_kw: float = 15.0) -> pd.DataFrame:
    systems = pd.read_csv(SYSTEMS_CSV)
    systems["kw"] = pd.to_numeric(systems.dc_capacity_kW, errors="coerce")
    res = systems[systems.kw <= max_kw]

    rows = []
    for sid, kw in zip(res.system_id.astype(int), res.kw):
        meta = fetch_system_metadata(int(sid))
        m = primary_module(meta)
        watts = None
        try:
            q = float(m.get("quantity") or 0)
            if q > 0 and np.isfinite(kw):
                watts = float(kw) * 1000.0 / q
        except (TypeError, ValueError):
            pass
        r = resolve_gamma(m.get("manufacturer", ""), m.get("model", ""),
                          m.get("type", ""), module_watts=watts, system_id=int(sid))
        d = r.as_dict()
        d["capacity_kw"] = float(kw) if np.isfinite(kw) else None
        d["module_watts"] = watts
        rows.append(d)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-kw", type=float, default=15.0)
    ap.add_argument("--out-json", type=Path, default=None)
    ap.add_argument("--out-csv", type=Path,
                    default=Path("outputs/soiling/module_gamma_by_system.csv"))
    args = ap.parse_args()

    d = build_table(args.max_kw)
    print(f"residential systems (<= {args.max_kw:g} kW): {len(d)}\n")

    tiers = d.tier.value_counts()
    print("RESOLUTION TIER")
    for t, n in tiers.items():
        print(f"  {t:<20s} {n:5d}  {100 * n / len(d):5.1f}%")
    fallback = (d.tier == "fleet_default").mean()
    named = (d.tier == "cec_module").mean()
    print(f"\n  resolved to a NAMED MODULE: {100 * named:.1f}%")
    print(f"  fell through to the fleet default: {100 * fallback:.1f}%")

    g = d.gamma_pdc
    print(f"\nRESOLVED gamma_pdc  (per degC, fraction)")
    print(f"  median {g.median():+.5f}   mean {g.mean():+.5f}   SD {g.std():.5f}")
    print(f"  p05 {g.quantile(.05):+.5f}   p95 {g.quantile(.95):+.5f}   "
          f"min {g.min():+.5f}  max {g.max():+.5f}")

    d["delta_vs_old"] = d.gamma_pdc - OLD_CONSTANT
    a = d.delta_vs_old.abs()
    print(f"\nDISTANCE FROM THE CONSTANT THE RECORDED LABELS USED ({OLD_CONSTANT})")
    print(f"  median |delta| {a.median():.5f}   p90 {a.quantile(.90):.5f}   "
          f"max {a.max():.5f}")
    print(f"  share within 0.0002 of it: {100 * (a <= 0.0002).mean():.1f}%")
    print(f"  share within 0.0005 of it: {100 * (a <= 0.0005).mean():.1f}%")
    print(f"  the -0.0035/-0.0045 gap that raised this question is 0.0010; "
          f"{100 * (a >= 0.0010).mean():.1f}% of systems are that far out")

    if args.out_csv:
        args.out_csv.parent.mkdir(parents=True, exist_ok=True)
        d.to_csv(args.out_csv, index=False)
        print(f"\nwrote {args.out_csv}")
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        summary = {
            "n_systems": int(len(d)),
            "old_constant": OLD_CONSTANT,
            "fleet_default": FLEET_DEFAULT_GAMMA,
            "tier_counts": {k: int(v) for k, v in tiers.items()},
            "tier_share": {k: float(v / len(d)) for k, v in tiers.items()},
            "gamma": {"median": float(g.median()), "mean": float(g.mean()),
                      "sd": float(g.std()), "p05": float(g.quantile(.05)),
                      "p95": float(g.quantile(.95)), "min": float(g.min()),
                      "max": float(g.max())},
            "delta_vs_old": {"median_abs": float(a.median()),
                             "p90_abs": float(a.quantile(.90)),
                             "max_abs": float(a.max()),
                             "share_within_0.0002": float((a <= 0.0002).mean()),
                             "share_within_0.0005": float((a <= 0.0005).mean()),
                             "share_at_or_beyond_0.0010": float((a >= 0.0010).mean())},
        }
        json.dump(summary, open(args.out_json, "w"), indent=2)
        print(f"wrote {args.out_json}")


if __name__ == "__main__":
    main()
