# figure_threshold_sweep.py
# Description: Precision/recall/F1 against detector confidence on val and test, showing the
# val-tuned frozen operating point and the size of the honest-protocol tuning penalty.
from __future__ import annotations

import pandas as pd
import matplotlib.pyplot as plt

from figstyle import REPO, SERIES, BLUE, ORANGE, GREEN, MUTED, RED, apply_style, save

SWEEP = REPO / "outputs/eval/rfdetr_w2/threshold_sweep.csv"
CONF_STAR = 0.50          # tuned on val, frozen before test was scored
GATE_F1 = 0.75


def main() -> None:
    apply_style()
    df = pd.read_csv(SWEEP)
    val = df[df.split == "val"].sort_values("conf")
    test = df[df.split == "test"].sort_values("conf")

    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.9), sharey=True)
    for ax, sub, title in ((axes[0], val, "Validation (tuning split)"),
                           (axes[1], test, "Test (reported split)")):
        ax.plot(sub.conf, sub.precision, color=BLUE, label="Precision")
        ax.plot(sub.conf, sub.recall, color=ORANGE, label="Recall")
        ax.plot(sub.conf, sub.f1, color=GREEN, label="F1", linewidth=2.2)
        ax.axvline(CONF_STAR, color=MUTED, linestyle="--", linewidth=1.0)
        ax.axhline(GATE_F1, color=RED, linestyle=":", linewidth=1.0)
        ax.set_title(title)
        ax.set_xlabel(r"Confidence threshold $c$")
        ax.set_xlim(0.05, 0.9)
        ax.set_ylim(0, 1.02)

    axes[0].set_ylabel("Score")
    # Annotate the frozen operating point and the tuning penalty on the test panel.
    star = test[test.conf == CONF_STAR].iloc[0]
    best = test.loc[test.f1.idxmax()]
    axes[1].plot([CONF_STAR], [star.f1], "o", color=GREEN, zorder=5)
    axes[1].plot([best.conf], [best.f1], "s", color=MUTED, zorder=5, markersize=4)
    axes[1].annotate(f"frozen $c^*$=0.50\nF1 {star.f1:.4f}",
                     xy=(CONF_STAR, star.f1), xytext=(0.58, 0.55),
                     fontsize=7.5, color=GREEN,
                     arrowprops=dict(arrowstyle="-", color=GREEN, linewidth=0.7))
    axes[1].annotate(f"test-tuned optimum\nF1 {best.f1:.4f} (+{best.f1 - star.f1:.4f})",
                     xy=(best.conf, best.f1), xytext=(0.10, 0.28),
                     fontsize=7.5, color=MUTED,
                     arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=0.7))
    axes[0].annotate("gate: F1 $\\geq$ 0.75", xy=(0.06, GATE_F1 + 0.02),
                     fontsize=7.5, color=RED)
    axes[0].legend(loc="lower left", ncol=1)

    fig.tight_layout()
    save(fig, "fig_threshold_sweep")


if __name__ == "__main__":
    main()
