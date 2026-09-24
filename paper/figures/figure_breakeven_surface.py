# figure_breakeven_surface.py
# Description: Where cleaning would pay at all. Expected net for one professional clean
# over rain-reset frequency and the re-soiling time constant, at a deliberately generous
# tariff. The zero contour is the break-even boundary; the calibrated coastal operating
# point sits far inside the losing region.
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import colors

from figstyle import REPO, MUTED, RED, INK, apply_style, save

SRC = REPO / "outputs/soiling/audit/breakeven_surface.parquet"

# The boundary only EXISTS once three assumptions are stacked in the product's favour,
# and that is the point of the figure. At the measured median (5.7 kW, $0.165/kWh,
# sl_sat 0.08) not one cell of the grid is positive. Shown here: a 20 kW roof (3.5x the
# measured median), $0.70/kWh (4.2x the NBT default and above any real California
# tariff), and a 12-point saturated soiling level (1.5x the calibrated value).
RATE = 0.70
KW = 20.0
SL_SAT = 0.12
COASTAL_K = 15.0          # recovery.DEFAULT_PARAMS, calibrated on coastal-CA NREL data
MEASURED_RESETS = 16.5    # median observed resets/year across the 149 measured systems


def main() -> None:
    apply_style()
    d = pd.read_parquet(SRC)
    s = d[(d.rate_usd_kwh == RATE) & (d.system_kw == KW)
          & (d.sl_sat == SL_SAT) & (d.seasonal)]
    piv = s.pivot_table(index="k", columns="n_heavy", values="net_usd")
    X, Y = np.meshgrid(piv.columns.to_numpy(float), piv.index.to_numpy(float))
    Z = piv.to_numpy(float)

    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    lim = float(np.nanmax(np.abs(Z)))
    mesh = ax.pcolormesh(X, Y, Z, cmap="RdYlGn",
                         norm=colors.TwoSlopeNorm(vmin=-lim, vcenter=0.0, vmax=lim),
                         shading="gouraud")
    cs = ax.contour(X, Y, Z, levels=[0.0], colors=[INK], linewidths=1.6)
    ax.clabel(cs, fmt={0.0: "break even"}, fontsize=7.5, inline=True)

    ax.axhline(COASTAL_K, color=RED, linestyle="--", linewidth=1.2)
    ax.plot([MEASURED_RESETS], [COASTAL_K], marker="o", ms=6, color=RED,
            markeredgecolor="white", markeredgewidth=0.8, zorder=5)
    ax.annotate("measured operating point\n(k=15 d, 16.5 resets/yr)\nnegative on every"
                "\ngrid cell at 5.7 kW",
                xy=(MEASURED_RESETS, COASTAL_K), xytext=(19, 70),
                fontsize=7.2, color=RED,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.9),
                arrowprops=dict(arrowstyle="->", color=RED, linewidth=0.8))

    ax.set_yscale("log")
    ax.set_yticks([5, 10, 15, 30, 60, 120, 240, 365])
    ax.set_yticklabels(["5", "10", "15", "30", "60", "120", "240", "365"])
    ax.set_xlabel("heavy-rain resets per year")
    ax.set_ylabel("re-soiling time constant $k$ (days)")
    ax.set_title("Where one clean would pay, under three concessions", color=INK, pad=12)
    ax.text(0.5, 1.015,
            f"\\${RATE:.2f}/kWh \u00b7 {KW:.0f} kW \u00b7 {100*SL_SAT:.0f}-pt saturation",
            transform=ax.transAxes, ha="center", va="bottom",
            fontsize=7.4, color=MUTED)
    ax.grid(False)
    cb = fig.colorbar(mesh, ax=ax, pad=0.02)
    cb.set_label("expected net (\\$)", fontsize=8)
    cb.ax.tick_params(labelsize=7)

    fig.tight_layout()
    save(fig, "fig_breakeven_surface")
    pos = (Z > 0).sum()
    print(f"slice cells {Z.size}, positive {pos} ({100*pos/Z.size:.1f}%), "
          f"max net ${np.nanmax(Z):.0f}")


if __name__ == "__main__":
    main()
