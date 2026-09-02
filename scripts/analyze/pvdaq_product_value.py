"""Does a per-roof soiling label rescue the cleaning product?

The PVDAQ label work (`docs/PVDAQ_LABEL_PIPELINE_SPEC.md`) buys one thing: an
individual roof's soiling loss instead of its region's. This asks whether that
knowledge can flip the economic verdict, by comparing the breakeven soiling %
against the loss distribution actually MEASURED on residential Csb systems.

Answer, recorded in `docs/PVDAQ_PRODUCT_RELEVANCE_20260826.md` section 1: no.
Breakeven exceeds 100% of annual output at the measured recovery fraction, so no
physically possible soiling level clears the cost of one clean. The binding
constraint is `recovery_frac = 0.045`, not the loss estimate.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_product_value.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from scripts.analyze.pvdaq_daily_srr_probe import MIN_VALID_INTERVALS
from src.risk import economics as E

OBS = Path("outputs/soiling/pvdaq_0a_csb12.json")


def main() -> None:
    obs = json.load(open(OBS))
    loss = np.array([
        r["fits"]["energy"]["perfect_clean"]["loss_pct"]
        for r in obs["results"]
        # Filter on valid intervals, not on the soiling ratio. See the 10912 case
        # in the spec: SR 0.99899 off 2 intervals is a model-mismatch artefact.
        if r["fits"]["energy"]["perfect_clean"]["n_valid_intervals"] >= MIN_VALID_INTERVALS
    ])
    print(f"MEASURED PVDAQ residential Csb losses (n={len(loss)}): "
          f"p50 {np.median(loss):.2f}  p90 {np.percentile(loss, 90):.2f}  "
          f"max {loss.max():.2f} pts")
    print(f"BASE_SOILING_PCT assumed for the AOI: {E.BASE_SOILING_PCT:.2f} pts")
    print(f"BASE_RATE (NBT default): ${E.BASE_RATE:.4f}/kWh\n")

    scen = E.DEFAULT_SCENARIOS["professional"]
    nem2 = E.BASE_RATE * 2.8
    print("Breakeven annual soiling loss (%) for ONE professional clean to net > $0")
    print(f"{'system kW':>10} | {'NBT $0.165':>12} | {'NEM2 2.8x':>12} | {'$0.70/kWh':>12}")
    print("-" * 56)
    for kw in (3, 5, 7, 10, 15, 20, 30):
        row = []
        for rate in (E.BASE_RATE, nem2, 0.70):
            b = E.breakeven_soiling_pct(scen, kw, E.BASE_SUN, rate, pct_max=100.0)
            row.append(f"{b:.1f}" if b else ">100")
        print(f"{kw:>10} | {row[0]:>12} | {row[1]:>12} | {row[2]:>12}")

    print("\nSame, with the DISCREDITED legacy recovery_frac 0.90, for contrast:")
    legacy = dict(scen)
    legacy["recovery_frac"] = E.LEGACY_RECOVERY_PRO
    for kw in (5, 10, 20):
        b = E.breakeven_soiling_pct(legacy, kw, E.BASE_SUN, E.BASE_RATE, pct_max=100.0)
        print(f"  {kw:>2} kW at $0.165: breakeven {b if b else '>100'} pts")
    print("  -> the measured spread DOES reach these. The product only ever penciled")
    print("     because of the constant measured to be 20x wrong.")

    print("\nRequired loss (pts) as a function of recovery_frac, the constant that binds:")
    print(f"{'recovery':>9} |" + "".join(f"{k:>9}kW" for k in (5, 10, 20)))
    print("-" * 42)
    for rec in (0.045, 0.10, 0.125, 0.20, 0.35, 0.50, 0.90):
        s = dict(scen)
        s["recovery_frac"] = rec
        row = ""
        for kw in (5, 10, 20):
            b = E.breakeven_soiling_pct(s, kw, E.BASE_SUN, E.BASE_RATE, pct_max=200.0)
            row += f"{(f'{b:.1f}' if b else '>200'):>11}"
        tag = ("  <- coastal SCC (measured)" if rec == 0.045
               else "  <- Phoenix k=60 (measured)" if rec == 0.125 else "")
        print(f"{rec:>9.3f} |{row}{tag}")

    print("\nQUOTE THIS AS: breakeven exceeds 100% of annual output, so no physically")
    print("possible soiling level clears one clean. Do NOT quote the raw >100 figures;")
    print("they read as a units error. See docs/PVDAQ_PRODUCT_RELEVANCE_20260826.md.")


if __name__ == "__main__":
    main()
