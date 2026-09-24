# figure_regional_holdout.py
# Description: Per-region and pooled out-of-region AUC for four feature sets, showing that
# the classifier collapses toward chance on the largest held-out region whatever it is fed.
from __future__ import annotations

import json

import numpy as np
import matplotlib.pyplot as plt

from figstyle import REPO, BLUE, ORANGE, MUTED, RED, GREEN, apply_style, save

SRC = REPO / "outputs/soiling/regional_holdout.json"
# The re-run that adds the year axis and restores the production hyperparameters. The
# original script read them from a config key that does not exist, so it had always
# fitted a stock model. Both are plotted: the published fold is what the literature
# figure showed, the corrected one is what the prose now reports.
AUDIT = REPO / "outputs/soiling/audit/regional_holdout_audit.json"
RANDOM_SPLIT_REF = 0.73       # ~0.73 for a random (non-spatial) split, CANONICAL_NUMBERS.md


def main() -> None:
    apply_style()
    r = json.loads(SRC.read_text())
    sizes = {int(k): v for k, v in r["region_sizes"].items()}
    prod = r["results"]["production (40)"]
    per = {int(k): v for k, v in prod["per_region_auc"].items()}
    a = json.loads(AUDIT.read_text())
    corr = a["results"]["region_year|production"]
    corr_per = {int(k): v["auc"] for k, v in corr["per_region"].items()}

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 3.0),
                                  gridspec_kw={"width_ratios": [1.25, 1]})

    # --- Per-region AUC vs region size ---------------------------------------
    ks = sorted(per)
    n = np.array([sizes[k] for k in ks], dtype=float)
    a = np.array([per[k] for k in ks], dtype=float)
    ax.scatter(n, a, s=30 + 140 * n / n.max(), color=BLUE, alpha=0.85,
               edgecolors="white", linewidths=0.7, zorder=4)
    ax.axhline(0.5, color=MUTED, linestyle=":", linewidth=1.0)
    ax.axhline(RANDOM_SPLIT_REF, color=GREEN, linestyle="--", linewidth=1.0)
    ax.axhline(prod["pooled_auc"], color=RED, linestyle="-", linewidth=1.2)
    biggest = max(ks, key=lambda k: sizes[k])
    ax.annotate(f"largest region\nn={sizes[biggest]}, AUC {per[biggest]:.3f}",
                xy=(sizes[biggest], per[biggest]), xytext=(60, 0.40),
                fontsize=7.5, color=BLUE, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.88),
                arrowprops=dict(arrowstyle="-", color=BLUE, linewidth=0.7))
    ax.scatter(n, [corr_per.get(k, np.nan) for k in ks],
               s=30 + 140 * n / n.max(), facecolors="none", edgecolors=ORANGE,
               linewidths=1.1, zorder=5)
    ax.axhline(corr["pooled_auc"], color=ORANGE, linestyle="-", linewidth=1.2)
    ax.annotate(f"pooled {prod['pooled_auc']:.3f} published",
                xy=(35, prod["pooled_auc"] + 0.016),
                fontsize=7.0, color=RED, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.88))
    ax.annotate(f"pooled {corr['pooled_auc']:.3f} corrected",
                xy=(35, corr["pooled_auc"] - 0.047),
                fontsize=7.0, color=ORANGE, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.88))
    ax.annotate(f"random split $\\approx${RANDOM_SPLIT_REF:.2f}",
                xy=(6, RANDOM_SPLIT_REF + 0.016), fontsize=7.5, color=GREEN, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.88))
    ax.annotate("chance", xy=(7, 0.508), fontsize=7.5, color=MUTED, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.88))
    ax.set_xscale("log")
    ax.set_xlabel("Held-out region size (rows)")
    ax.set_ylabel("Out-of-region AUC")
    ax.set_ylim(0.35, 1.0)
    ax.set_title(f"{r['n_regions']} k-means regions: filled = published, open = corrected")

    # --- Feature sets --------------------------------------------------------
    order = ["weather+physics (27)", "  +location (30)", "  +landcover (35)",
             "trim: -tilt -deadAQ (33)", "production (40)"]
    order = [k for k in order if k in r["results"]]
    vals = [r["results"][k]["pooled_auc"] for k in order]
    labels = [k.strip().replace("production (40)", "production (40; 34 after AQ drop)")
              for k in order]
    y = np.arange(len(order))
    colors = [BLUE] * len(order)
    colors[-1] = ORANGE
    ax2.barh(y, vals, color=colors, height=0.6)
    ax2.axvline(0.5, color=MUTED, linestyle=":", linewidth=1.0)
    ax2.axvline(RANDOM_SPLIT_REF, color=GREEN, linestyle="--", linewidth=1.0)
    for yi, v in zip(y, vals):
        ax2.annotate(f"{v:.3f}", xy=(v, yi), xytext=(3, 0), textcoords="offset points",
                     va="center", fontsize=7.5, color=MUTED)
    ax2.set_yticks(y)
    ax2.set_yticklabels(labels, fontsize=7.5)
    ax2.set_xlim(0.45, 0.80)
    ax2.set_xlabel("Pooled out-of-region AUC")
    ax2.set_title("Adding features does not fix it\n"
                  "(published fold: no year axis, stock hyperparameters)",
                  fontsize=8.5)

    fig.tight_layout()
    save(fig, "fig_regional_holdout")


if __name__ == "__main__":
    main()
