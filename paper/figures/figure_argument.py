# figure_argument.py
# Description: The paper in one picture. Four questions the pipeline must answer in order,
# what each one actually returned, and the point at which the answer stops mattering.
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from figstyle import INK, MUTED, apply_style, save

GREEN_F, GREEN_E = "#E7F1EB", "#2E7D5B"
RED_F, RED_E = "#F7E9E9", "#B03A3A"
AMBER_F, AMBER_E = "#FBF0E3", "#D1701A"

STAGES = [
    ("1. Can we find the arrays?", "YES", GREEN_F, GREEN_E,
     "tile $F_1$ 0.826 [0.798, 0.853]\npermit recall 73.6%\npermissive models only"),
    ("2. Can we rank them by soiling?", "NO", RED_F, RED_E,
     "0.622, not the 0.710\nwe published\nlat/lon alone: 0.644"),
    ("3. Would per-system labels fix it?", "NOT YET", AMBER_F, AMBER_E,
     "0.573 vs a structural 0.5\nbut three label variants\nspan 8x in level, $\\rho$ 0.24-0.89"),
    ("4. Is a clean worth buying?", "NO", RED_F, RED_E,
     "\\$28.10 recovered per wash\nagainst a \\$150 service\nmedian needs \\$2.44/kWh"),
]


def main() -> None:
    apply_style()
    fig, ax = plt.subplots(figsize=(7.1, 2.55))
    ax.set_xlim(0, 100); ax.set_ylim(8, 100); ax.axis("off")

    w, gap = 21.5, 4.2
    x0, ytop, h = 1.0, 92.0, 46.0
    centers = []
    for i, (q, verdict, fc, ec, body) in enumerate(STAGES):
        x = x0 + i * (w + gap)
        centers.append(x + w / 2)
        ax.add_patch(FancyBboxPatch((x, ytop - h), w, h,
                                    boxstyle="round,pad=0.6,rounding_size=2",
                                    facecolor=fc, edgecolor=ec, linewidth=1.3))
        ax.text(x + w / 2, ytop - 6.5, q, ha="center", va="center",
                fontsize=7.8, color=INK, weight="bold", wrap=True)
        ax.text(x + w / 2, ytop - 17.5, verdict, ha="center", va="center",
                fontsize=13, color=ec, weight="bold")
        ax.text(x + w / 2, ytop - 33.5, body, ha="center", va="center",
                fontsize=6.9, color=INK, linespacing=1.45)
        if i < len(STAGES) - 1:
            ax.add_patch(FancyArrowPatch(
                (x + w + 0.3, ytop - h / 2), (x + w + gap - 0.3, ytop - h / 2),
                arrowstyle="-|>", mutation_scale=11, color=MUTED, linewidth=1.1))

    # The move that makes the paper's order the right order.
    bar_l, bar_r = centers[1] - w / 2, centers[3] + w / 2
    yb = 28.0
    ax.add_patch(FancyBboxPatch((bar_l, yb - 13.5), bar_r - bar_l, 15.5,
                                boxstyle="round,pad=0.5,rounding_size=2",
                                facecolor="white", edgecolor=MUTED,
                                linewidth=1.0, linestyle="--"))
    ax.text((bar_l + bar_r) / 2, yb - 2.0,
            "In this market, question 4 settles questions 2 and 3.",
            ha="center", va="center", fontsize=8.2, color=INK, weight="bold")
    ax.text((bar_l + bar_r) / 2, yb - 9.0,
            "A perfect ranker cannot create value that is not there: one wash recovers "
            "\\$28.10 and the service costs \\$150.\nThe modelling question is real, and it is "
            "downstream of an arithmetic one.",
            ha="center", va="center", fontsize=6.9, color=INK, linespacing=1.5)
    for cx in centers[1:]:
        ax.add_patch(FancyArrowPatch((cx, ytop - h - 0.5), (cx, yb + 2.5),
                                     arrowstyle="-", color=MUTED,
                                     linewidth=0.7, linestyle=":"))

    fig.tight_layout(pad=0.2)
    save(fig, "fig_argument")


if __name__ == "__main__":
    main()
