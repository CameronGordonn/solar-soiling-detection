# figure_cleaning_value.py
# Description: The cleaning-value verdict on 149 measured rooftops. Left: the electricity
# price each system would need for one professional clean to break even, against real
# California tariffs. Right: the entire annual recoverable value per system against the
# cost of the service, showing the prize is smaller than any plausible price.
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from figstyle import REPO, BLUE, ORANGE, MUTED, RED, GREEN, INK, apply_style, save

sys.path.insert(0, str(REPO))

SRC = REPO / "outputs/soiling/audit/real_systems_economics.parquet"
NBT_RATE = 0.165        # rates.py, NBT no-battery blended marginal value
RETAIL_RATE = 0.4573    # rates.py, PG&E retail offset
BASE_SUN = 5.5


def main() -> None:
    apply_style()
    from src.risk.economics import MIN_PRO_SERVICE, annual_loss_usd, professional_cost

    d = pd.read_parquet(SRC)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 3.0),
                                  gridspec_kw={"width_ratios": [1, 1]})

    # --- Left: break-even tariff required, per system ------------------------
    be = np.sort(d.breakeven_rate_usd_kwh.to_numpy())
    be = be[np.isfinite(be)]
    ax.plot(be, np.arange(1, len(be) + 1) / len(be) * 100, color=BLUE, linewidth=1.8)
    for x, c, lab in ((NBT_RATE, ORANGE, "NBT \\$0.165"),
                      (RETAIL_RATE, GREEN, "retail \\$0.457")):
        ax.axvline(x, color=c, linestyle="--", linewidth=1.1)
        ax.text(x * 1.08, 62, lab, fontsize=7.2, color=c, rotation=90,
                va="center", ha="left")
    med = float(np.median(be))
    ax.axvline(med, color=RED, linestyle=":", linewidth=1.1)
    ax.annotate(f"median system\nneeds \\${med:.2f}/kWh", xy=(med, 50), xytext=(med * 1.35, 28),
                fontsize=7.5, color=RED,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.9),
                arrowprops=dict(arrowstyle="-", color=RED, linewidth=0.7))
    ax.set_xscale("log")
    ax.set_xlim(0.1, 20)
    ax.set_ylim(0, 100)
    ax.set_xlabel("electricity price needed to break even (\\$/kWh, log)")
    ax.set_ylabel("share of systems (%)")
    ax.set_title(f"Break-even tariff, {len(be)} measured rooftops", color=INK)

    # --- Right: the whole prize, against the cost of the service -------------
    # Value of ONE wash. By the cancellation of the paper's economics section this is
    # kW.h.eta.p.delta.d: the annual loss in the first factor is exactly the denominator of
    # recovery_frac in the second, so nothing here depends on a cleaning assumption. Sun
    # hours are the site's measured value, not the 5.5 constant, which is what made an
    # earlier version of this figure read $29.22 instead of $28.10.
    val_nbt = np.array([annual_loss_usd(r.capacity_kw, r.sun_hours, r.annual_loss_pct / 100.0,
                                        NBT_RATE) * r.recovery_frac for r in d.itertuples()])
    val_ret = np.array([annual_loss_usd(r.capacity_kw, r.sun_hours, r.annual_loss_pct / 100.0,
                                        RETAIL_RATE) * r.recovery_frac for r in d.itertuples()])
    bins = np.logspace(np.log10(0.5), np.log10(400), 34)
    ax2.hist(val_nbt, bins=bins, color=ORANGE, alpha=0.78, label="at NBT \\$0.165")
    ax2.hist(val_ret, bins=bins, color=GREEN, alpha=0.55, label="at retail \\$0.457")
    ax2.axvline(MIN_PRO_SERVICE, color=RED, linewidth=1.4)
    ax2.text(MIN_PRO_SERVICE * 1.08, ax2.get_ylim()[1] * 0.70,
             f"cost of one clean\n\\${MIN_PRO_SERVICE:.0f}", fontsize=7.2, color=RED)
    ax2.axvline(25, color=MUTED, linestyle=":", linewidth=1.0)
    ax2.text(25 * 0.93, ax2.get_ylim()[1] * 0.99, "a \\$25 clean", fontsize=7.0,
             color=MUTED, ha="right", va="top")
    ax2.set_xscale("log")
    ax2.set_xlabel("value of one wash (\\$)")
    ax2.set_ylabel("systems")
    ax2.set_title("What one wash returns, before its cost", color=INK)
    ax2.legend(loc="upper left")

    fig.tight_layout()
    save(fig, "fig_cleaning_value")
    print(f"median value per wash: NBT ${np.median(val_nbt):.2f}  "
          f"retail ${np.median(val_ret):.2f}  median breakeven ${med:.2f}/kWh")


if __name__ == "__main__":
    main()
