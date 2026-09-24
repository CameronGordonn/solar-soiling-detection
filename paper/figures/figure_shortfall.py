# figure_shortfall.py
# Description: Cleaning cost divided by annual recovered dollars for all 1,865 Santa Cruz
# sites against system size, with the breakeven line at 1.0 that no site reaches.
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from figstyle import REPO, BLUE, ORANGE, MUTED, RED, GREEN, apply_style, save

sys.path.insert(0, str(REPO))
from src.risk.economics import professional_cost  # noqa: E402

SITES = REPO / "outputs/aoi/santa-cruz-w2-21cm/site_economics.csv"
RECOVERY_FRAC = 0.045     # measured, src/risk/recovery.py (was assumed 0.90)
BANDS = [(0, 5), (5, 10), (10, 15), (15, 30), (30, 60), (60, 150), (150, np.inf)]
BAND_LABELS = ["0-5", "5-10", "10-15", "15-30", "30-60", "60-150", "150+"]


def main() -> None:
    apply_style()
    d = pd.read_csv(SITES)
    d["cost_usd"] = d.system_kw.apply(professional_cost)
    d["recovered_usd"] = d.annual_loss_usd * RECOVERY_FRAC
    d["shortfall"] = d.cost_usd / d.recovered_usd

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 3.0),
                                  gridspec_kw={"width_ratios": [1.5, 1]})

    ax.scatter(d.system_kw, d.shortfall, s=7, alpha=0.30, color=BLUE,
               edgecolors="none", label=f"site (n={len(d):,})")
    ax.axhline(1.0, color=RED, linestyle="--", linewidth=1.2)
    ax.annotate("breakeven (cost = recovered)", xy=(0.6, 1.12), fontsize=7.5, color=RED)
    best = d.loc[d.shortfall.idxmin()]
    ax.plot([best.system_kw], [best.shortfall], "*", color=ORANGE, markersize=11, zorder=5)
    ax.annotate(f"best site {best.shortfall:.2f}x\n({best.system_kw:.0f} kW)",
                xy=(best.system_kw, best.shortfall), xytext=(60, 1.6),
                fontsize=7.5, color=ORANGE,
                arrowprops=dict(arrowstyle="-", color=ORANGE, linewidth=0.7))
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("System size (kW DC, from detected area)")
    ax.set_ylabel(r"Shortfall: cleaning cost $/$ annual recovered \$")
    ax.set_title("Every site, every size")
    ax.legend(loc="upper right")

    med = [d[(d.system_kw >= lo) & (d.system_kw < hi)].shortfall.median() for lo, hi in BANDS]
    bst = [d[(d.system_kw >= lo) & (d.system_kw < hi)].shortfall.min() for lo, hi in BANDS]
    n = [int(((d.system_kw >= lo) & (d.system_kw < hi)).sum()) for lo, hi in BANDS]
    x = np.arange(len(BANDS))
    ax2.bar(x - 0.19, med, width=0.38, color=BLUE, label="band median")
    ax2.bar(x + 0.19, bst, width=0.38, color=GREEN, label="band best")
    ax2.axhline(1.0, color=RED, linestyle="--", linewidth=1.2)
    for xi, (m, c) in enumerate(zip(med, n)):
        ax2.annotate(f"n={c}", xy=(xi, m), xytext=(0, 3), textcoords="offset points",
                     ha="center", fontsize=6.5, color=MUTED)
    ax2.set_xticks(x)
    ax2.set_xticklabels(BAND_LABELS, rotation=45, ha="right")
    ax2.set_yscale("log")
    ax2.set_xlabel("System size band (kW)")
    ax2.set_title("Size targeting asymptotes")
    ax2.legend(loc="upper right")

    fig.tight_layout()
    save(fig, "fig_shortfall")
    print(f"median shortfall by band: "
          f"{ {lab: round(m, 2) for lab, m in zip(BAND_LABELS, med)} }")
    print(f"global best {d.shortfall.min():.2f}x; sites clearing breakeven: "
          f"{int((d.shortfall <= 1).sum())}")


if __name__ == "__main__":
    main()
