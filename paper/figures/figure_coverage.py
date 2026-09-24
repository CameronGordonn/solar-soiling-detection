# figure_coverage.py
# Description: Where the labels actually are. NREL soiling stations and the metered rooftops
# used for the economics, plotted together, with the longitude concentration that limits
# every geographic claim in the paper.
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from figstyle import REPO, BLUE, ORANGE, MUTED, RED, INK, apply_style, save

MATRIX = REPO / "outputs/soiling/training_matrix.parquet"
FLEET = REPO / "outputs/soiling/audit/real_systems_economics.parquet"
CUT = -114.0


def main() -> None:
    apply_style()
    m = pd.read_parquet(MATRIX)
    m = m[m.label.notna()].dropna(subset=["latitude", "longitude"])
    n = m[~m.is_summary]          # 891 annual panel rows, what the temporal folds use
    c = m[m.is_summary]           # 111 censored summary-only rows, one per station
    f = pd.read_parquet(FLEET)

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.1, 2.85),
                                  gridspec_kw={"width_ratios": [1.5, 1]})

    ax.scatter(n.longitude, n.latitude, s=13, color=BLUE, alpha=0.45,
               edgecolors="none", label=f"NREL panel rows (n={len(n):,})")
    ax.scatter(c.longitude, c.latitude, s=15, color=MUTED, alpha=0.55, marker="x",
               linewidths=0.8, label=f"censored summary rows (n={len(c)})")
    ax.scatter(f.lon, f.lat, s=20, color=ORANGE, alpha=0.85, marker="^",
               edgecolors="white", linewidths=0.3,
               label=f"metered rooftops, economics (n={len(f)})")
    ax.axvline(CUT, color=RED, linestyle="--", linewidth=1.2)
    ax.text(CUT + 0.8, 48.6, f"{CUT:.0f}$^\\circ$", fontsize=7.2, color=RED, ha="left")
    ax.set_xlim(-127, -66); ax.set_ylim(23, 50)
    ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
    ax.set_title("Where the soiling labels are", color=INK)
    ax.legend(loc="lower left", fontsize=6.9)

    # Rows over-state the concentration because western stations have longer records.
    # The station-level figure is the honest measure of geographic diversity.
    west_n = 100 * (n.longitude < CUT).mean()
    west_c = 100 * (c.longitude < CUT).mean()
    west_f = 100 * (f.lon < CUT).mean()
    bins = np.arange(-127, -64, 3)
    ax2.hist(n.longitude, bins=bins, color=BLUE, alpha=0.6,
             label=f"panel rows: {west_n:.0f}% west")
    ax2.hist(c.longitude, bins=bins, color=MUTED, alpha=0.6,
             label=f"summary rows: {west_c:.0f}% west")
    ax2.hist(f.lon, bins=bins, color=ORANGE, alpha=0.75,
             label=f"rooftops: {west_f:.0f}% west")
    ax2.axvline(CUT, color=RED, linestyle="--", linewidth=1.2)
    ax2.set_xlabel("longitude"); ax2.set_ylabel("rows / systems")
    ax2.set_title("The east is censored rows only", color=INK)
    ax2.legend(loc="upper right", fontsize=6.9)

    fig.tight_layout()
    save(fig, "fig_coverage")
    print(f"panel {west_n:.1f}% west   summary {west_c:.1f}% west   "
          f"rooftops {west_f:.1f}% west")


if __name__ == "__main__":
    main()
