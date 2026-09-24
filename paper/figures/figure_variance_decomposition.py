# figure_variance_decomposition.py
# Description: Share of the per-home dollar spread contributed by each driver, beside the
# within-AOI spread of the soiling head itself — the evidence that the risk model
# describes level rather than ranking homes against each other.
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from figstyle import REPO, BLUE, ORANGE, GREEN, MUTED, RED, apply_style, save

SITES = REPO / "outputs/aoi/santa-cruz-w2-21cm/site_economics.csv"
# Variance shares measured over the 1,710 residential-scale (2-15 kW) sites;
# docs/HANDOFF_roof_geometry_for_paper.md, Result 1.
SHARES = [("System size (kW)\nfrom the detector", 86.7, BLUE),
          ("Roof orientation (POA)\nfrom lidar", 11.1, GREEN),
          ("Soiling loss %\nfrom the risk model", 2.2, ORANGE)]
RESIDENTIAL = (2.0, 15.0)


def main() -> None:
    apply_style()
    d = pd.read_csv(SITES)
    res = d[(d.system_kw >= RESIDENTIAL[0]) & (d.system_kw <= RESIDENTIAL[1])]

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.9),
                                  gridspec_kw={"width_ratios": [1, 1.15]})

    labels = [s[0] for s in SHARES]
    vals = [s[1] for s in SHARES]
    cols = [s[2] for s in SHARES]
    y = np.arange(len(SHARES))[::-1]
    ax.barh(y, vals, color=cols, height=0.55)
    for yi, v in zip(y, vals):
        ax.annotate(f"{v:.1f}%", xy=(v, yi), xytext=(4, 0), textcoords="offset points",
                    va="center", fontsize=8, color=MUTED)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7.5)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of per-home dollar variance (%)")
    ax.set_title(f"97.8% is what the detector measures\n(n={len(res):,} residential sites)",
                 fontsize=8.5)

    # Distribution of the soiling head across the AOI.
    x = res.loss_pct_p50.to_numpy()
    p10, p50, p90 = np.percentile(x, [10, 50, 90])
    ax2.hist(x, bins=40, color=ORANGE, alpha=0.65, edgecolor="white", linewidth=0.3)
    for v, c, lab in ((p10, MUTED, "p10"), (p50, RED, "p50"), (p90, MUTED, "p90")):
        ax2.axvline(v, color=c, linestyle="--", linewidth=1.1)
        ax2.annotate(f"{lab} {v:.2f}%", xy=(v, ax2.get_ylim()[1] * 0.98),
                     rotation=90, fontsize=7, color=c, ha="right", va="top", bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.88))
    ax2.set_xlabel("Predicted annual recoverable soiling loss (%)")
    ax2.set_ylabel("Sites")
    ax2.set_title(f"Whole AOI spans {p90 - p10:.2f} points\n"
                  f"(p90/p10 ratio {p90 / p10:.2f}$\\times$)", fontsize=8.5)

    fig.tight_layout()
    save(fig, "fig_variance_decomposition")
    print(f"residential sites {len(res)}; p10 {p10:.3f} p50 {p50:.3f} p90 {p90:.3f} "
          f"span {p90 - p10:.3f} pts ratio {p90 / p10:.3f}")


if __name__ == "__main__":
    main()
