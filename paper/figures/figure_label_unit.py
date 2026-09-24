# figure_label_unit.py
# Description: Why station labels cannot rank roofs, shown rather than asserted. Left: every
# metered system inside one 0.5-degree cell, with the single label all of them would inherit
# from a station. Right: the same spread measured across every multi-system cell.
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from figstyle import REPO, BLUE, ORANGE, MUTED, RED, INK, apply_style, save

SRC = REPO / "outputs/soiling/pvdaq_fleet_labels.csv"
FOCUS = "66_-234"      # the largest cell: 103 residential systems
MIN_CELL = 5


def main() -> None:
    apply_style()
    d = pd.read_csv(SRC)
    d = d[d.capacity_kw.between(2, 15)].copy()
    d["cell"] = [f"{int(round(a / 0.5))}_{int(round(b / 0.5))}"
                 for a, b in zip(d.lat, d.lon)]

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.1, 2.95),
                                  gridspec_kw={"width_ratios": [1.15, 1]})

    # --- Left: one cell, every roof in it -----------------------------------
    g = d[d.cell == FOCUS]
    rng = np.random.default_rng(0)
    y = rng.normal(0, 0.22, len(g))
    ax.scatter(g.loss_pct, y, s=17, color=BLUE, alpha=0.7,
               edgecolors="white", linewidths=0.4, zorder=3)
    lab = float(g.loss_pct.median())
    ax.axvline(lab, color=RED, linewidth=1.8, zorder=4)
    ax.annotate("the one label a station\ngives all 103 roofs",
                xy=(lab, 0.92), xytext=(lab + 1.15, 0.86),
                fontsize=7.3, color=RED, va="center",
                arrowprops=dict(arrowstyle="->", color=RED, linewidth=0.8))
    ax.annotate(f"measured: {g.loss_pct.min():.2f} to {g.loss_pct.max():.2f} pts",
                xy=(0.5, -0.90), fontsize=7.3, color=INK, ha="center")
    ax.set_ylim(-1.15, 1.15)
    ax.spines["left"].set_visible(False)
    ax.set_yticks([])
    ax.set_xlabel("measured annual soiling loss (points)")
    ax.set_title(f"One \\SI{{0.5}}{{\\degree}} cell, {len(g)} metered roofs".replace(
        "\\SI{0.5}{\\degree}", "0.5$^\\circ$"), color=INK)

    # --- Right: is that cell unusual? ---------------------------------------
    sz = d.cell.value_counts()
    cells = [c for c in sz.index if sz[c] >= MIN_CELL]
    rec = []
    for c in cells:
        gg = d[d.cell == c]
        rec.append((float(gg.loss_pct.mean()), float(gg.loss_pct.std()), len(gg)))
    r = pd.DataFrame(rec, columns=["mean", "sd", "n"])
    ax2.scatter(r["mean"], r["sd"], s=12 + 90 * r["n"] / r["n"].max(),
                color=ORANGE, alpha=0.75, edgecolors="white", linewidths=0.5)
    lim = [0, max(r["mean"].max(), r["sd"].max()) * 1.05]
    ax2.plot(lim, lim, color=MUTED, linestyle="--", linewidth=1.0)
    ax2.annotate("spread equals the level", xy=(lim[1] * 0.52, lim[1] * 0.57),
                 fontsize=7.0, color=MUTED, rotation=38)
    ax2.set_xlim(lim); ax2.set_ylim(0, lim[1])
    ax2.set_xlabel("cell mean soiling loss (points)")
    ax2.set_ylabel("within-cell SD (points)")
    ax2.set_title(f"Every cell with $\\geq${MIN_CELL} roofs ({len(cells)} cells)",
                  color=INK)

    med = float((r["sd"] / r["mean"]).median())
    ax2.annotate(f"median within-cell SD is\n{100*med:.0f}% of the cell mean",
                 xy=(0.97, 0.06), xycoords="axes fraction", ha="right", va="bottom",
                 fontsize=7.2, color=INK,
                 bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=MUTED, alpha=0.92))

    fig.tight_layout()
    save(fig, "fig_label_unit")
    print(f"focus cell n={len(g)}  range {g.loss_pct.min():.2f}-{g.loss_pct.max():.2f}")
    print(f"cells >= {MIN_CELL}: {len(cells)}  median SD/mean {med:.3f}")


if __name__ == "__main__":
    main()
