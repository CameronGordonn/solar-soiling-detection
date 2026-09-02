"""Phase 2 — do our PVDAQ labels agree with NREL's, on the same ground?

Why this runs BEFORE Phase 1 scales. Our first 12-system read gave a median
annual soiling loss of 1.98 pts, while the Stage-2 XGBoost head predicts ~5.5 pts
for Santa Cruz homes and NREL's own coastal-CA measurement is 4.70. If our
extraction is biased low against NREL's convention, scaling it to 1,236 systems
produces 1,236 wrong labels. This is the cheapest possible check: PVDAQ systems
sitting within 5 km of an NREL station should reproduce that station's IWSR.

Read the result carefully. Per `PVDAQ_LABEL_PIPELINE_SPEC.md` section 1, 881 of the
891 NREL rows were themselves produced by this class of method over inverter AC
power. So agreement is a REPRODUCTION CHECK ON OUR IMPLEMENTATION, not an
independent validation. Disagreement is still highly informative; agreement is
weaker evidence than it looks.

A structural caveat that may explain a low bias, and which this script cannot
remove: we run `soiling_srr` with `recenter=True`, which normalises to each
array's own first-364-day median. That measures the sawtooth AMPLITUDE, not the
absolute mean deficit, while NREL's IWSR is closer to absolute. See
`docs/PVDAQ_PRODUCT_RELEVANCE_20260826.md` section 2 for the same mechanism in
another guise.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_phase2_vs_nrel.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from scripts.analyze.pvdaq_daily_srr_probe import MIN_VALID_INTERVALS

DEFAULT_FITS = Path("outputs/soiling/pvdaq_phase2_nearstation.json")
DEFAULT_PAIRS = Path("outputs/soiling/phase2_pairs.csv")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fits", type=Path, default=DEFAULT_FITS)
    ap.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    args = ap.parse_args()

    fits = json.load(open(args.fits))
    pairs = pd.read_csv(args.pairs).set_index("sid")

    rows = []
    for r in fits["results"]:
        pc = r.get("fits", {}).get("energy", {}).get("perfect_clean", {})
        if "loss_pct" not in pc:
            continue
        sid = r["meta"]["system_id"]
        if sid not in pairs.index:
            continue
        p = pairs.loc[sid]
        rows.append(dict(
            sid=sid, climate=r["meta"]["climate"], kw=r["meta"]["capacity_kw"],
            pvdaq=pc["loss_pct"],
            lo=100 * (1 - pc["ci95"][1]), hi=100 * (1 - pc["ci95"][0]),
            n_valid=pc["n_valid_intervals"],
            nrel=float(p.st_loss), km=float(p.km), station=str(p.station),
        ))
    d = pd.DataFrame(rows)
    if d.empty:
        print("no overlapping fits")
        return
    d["ciw"] = d.hi - d.lo
    d["diff"] = d.pvdaq - d.nrel
    ok = d[d.n_valid >= MIN_VALID_INTERVALS].copy()

    print(d[["sid", "climate", "kw", "km", "station", "pvdaq", "ciw", "nrel",
             "diff", "n_valid"]].round(2).to_string(index=False))
    print(f"\nfits: {len(d)}   passing QC (n_valid >= {MIN_VALID_INTERVALS}): {len(ok)}")
    if len(ok) < 3:
        print("too few QC-passing fits to conclude anything")
        return

    print(f"\n  PVDAQ loss  p50 {ok.pvdaq.median():.2f}  mean {ok.pvdaq.mean():.2f} pts")
    print(f"  NREL  loss  p50 {ok.nrel.median():.2f}  mean {ok.nrel.mean():.2f} pts")
    print(f"  paired diff (PVDAQ - NREL): mean {ok['diff'].mean():+.2f}  "
          f"median {ok['diff'].median():+.2f}  SD {ok['diff'].std():.2f} pts")

    # Paired test: the two estimates describe the SAME location, so the unit is
    # the pair, not the sample. Same discipline as the W1/W2 detection comparison.
    t, p = stats.wilcoxon(ok.pvdaq, ok.nrel) if len(ok) >= 6 else (np.nan, np.nan)
    # Seed ONCE. Constructing default_rng(0) inside the loop makes every resample
    # identical and collapses the interval to a point -- it did exactly that on the
    # first run and produced a spurious "CI excludes zero" verdict.
    rng = np.random.default_rng(0)
    dv = ok["diff"].values
    boot = [rng.choice(dv, len(dv), replace=True).mean() for _ in range(5000)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(f"  bootstrap 95% CI on mean difference: [{lo:+.2f}, {hi:+.2f}] pts")
    if not np.isnan(p):
        print(f"  Wilcoxon signed-rank p = {p:.4f}")
    print(f"  Spearman rank corr PVDAQ vs NREL: {ok.pvdaq.corr(ok.nrel, method='spearman'):+.2f}")

    print("\nVERDICT")
    if lo <= 0 <= hi:
        print("  CI on the mean difference straddles zero -> no detectable bias.")
        print("  The label pipeline reproduces NREL's level on shared ground.")
    elif hi < 0:
        print(f"  PVDAQ reads LOW by {-ok['diff'].mean():.2f} pts, CI excludes zero.")
        print("  Our labels are biased against NREL's convention. Do NOT scale Phase 1")
        print("  until this is explained -- the recenter=True mechanism in")
        print("  docs/PVDAQ_PRODUCT_RELEVANCE_20260826.md section 2 is the first suspect.")
    else:
        print(f"  PVDAQ reads HIGH by {ok['diff'].mean():.2f} pts, CI excludes zero.")
        print("  Investigate before scaling.")
    print("\n  Reminder: 881/891 NREL rows came from this same class of method, so")
    print("  agreement is a reproduction check, not independent validation.")


if __name__ == "__main__":
    main()
