# figure_sam_promptbox.py
# Description: Mask quality and area fidelity of the SAM2 stage across prompt-box
# perturbations, showing that whole-roof over-segmentation is a prompt-box failure.
from __future__ import annotations

import json

import numpy as np
import matplotlib.pyplot as plt

from figstyle import REPO, BLUE, ORANGE, MUTED, RED, GREEN, apply_style, save

SRC = REPO / "outputs/eval/sam_containment_ab.json"
# Baseline (no containment) variants, ordered from tightest prompt box to loosest.
VARIANTS = [("box_only", "box only\n(no SAM)"), ("shrink15", "shrink 15%\n(production)"),
            ("exact", "exact"), ("dilate10", "dilate 10%"),
            ("dilate25", "dilate 25%"), ("dilate50", "dilate 50%")]


def main() -> None:
    apply_style()
    s = json.loads(SRC.read_text())
    summ = s["summary"]
    keys = [k for k, _ in VARIANTS if k in summ]
    labels = [lab for k, lab in VARIANTS if k in summ]
    iou = np.array([summ[k]["median_iou"] for k in keys])
    area = np.array([summ[k]["median_area_vs_gt"] for k in keys])
    grab = np.array([summ[k]["roofgrab_rate"] * 100 for k in keys])
    x = np.arange(len(keys))
    prod = keys.index("shrink15") if "shrink15" in keys else None

    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.8))

    def bars(ax, vals, color, title, ylabel, ref=None, reflab=None, fmt="{:.3f}"):
        cols = [MUTED] * len(vals)
        for i in range(len(vals)):
            cols[i] = color
        if prod is not None:
            cols[prod] = ORANGE
        ax.bar(x, vals, color=cols, width=0.65)
        if ref is not None:
            ax.axhline(ref, color=RED, linestyle="--", linewidth=1.0)
            if reflab:
                ax.annotate(reflab, xy=(-0.45, ref * 1.03), fontsize=7, color=RED)
        for xi, v in zip(x, vals):
            ax.annotate(fmt.format(v), xy=(xi, v), xytext=(0, 2),
                        textcoords="offset points", ha="center", fontsize=6.5, color=MUTED)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=6.5)
        ax.set_title(title)
        ax.set_ylabel(ylabel)

    bars(axes[0], iou, BLUE, "Mask agreement", "Median IoU vs GT")
    bars(axes[1], area, GREEN, "Area fidelity", "Median mask area / GT area",
         ref=1.0, reflab="unbiased", fmt="{:.2f}")
    bars(axes[2], grab, BLUE, "Whole-roof failures", "Roof-grab rate (% > 2$\\times$ GT)",
         fmt="{:.1f}")
    axes[0].set_ylim(0, 1.0)
    fig.suptitle(f"SAM2 prompt-box sensitivity (n={s['n']} validation arrays, "
                 f"orange = production default)", fontsize=8.5, y=1.03)
    fig.tight_layout()
    save(fig, "fig_sam_promptbox")


if __name__ == "__main__":
    main()
