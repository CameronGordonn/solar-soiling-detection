# figure_risk_validation.py
# Description: Out-of-year reliability diagram for the soiling classifier beside its
# pooled leave-one-year-out AUC and bootstrap interval against the 0.70 acceptance gate.
from __future__ import annotations

import json

import numpy as np
import matplotlib.pyplot as plt

import json as _json

from figstyle import REPO, BLUE, ORANGE, MUTED, RED, GREEN, apply_style, save

HOLDOUT = REPO / "runs/soiling/run_optionb/holdout_ci.json"
GATE = 0.70


def main() -> None:
    apply_style()
    h = json.loads(HOLDOUT.read_text())
    cal = h["calibration"]
    rel = cal["reliability"]

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 3.1),
                                  gridspec_kw={"width_ratios": [1, 1]})

    # --- Reliability diagram -------------------------------------------------
    mp = np.array([b["mean_pred"] for b in rel])
    of = np.array([b["obs_freq"] for b in rel])
    nb = np.array([b["n"] for b in rel], dtype=float)
    ax.plot([0, 1], [0, 1], color=MUTED, linestyle="--", linewidth=1.0,
            label="perfect calibration")
    ax.scatter(mp, of, s=18 + 120 * nb / nb.max(), color=BLUE, alpha=0.85,
               edgecolors="white", linewidths=0.6, zorder=4, label="decile bin (area $\\propto$ n)")
    ax.plot(mp, of, color=BLUE, linewidth=1.0, alpha=0.55, zorder=3)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Mean predicted probability (out-of-year)")
    ax.set_ylabel("Observed at-risk frequency")
    ax.set_title("Calibration survives temporal shift")
    ax.annotate(f"Brier {cal['brier']:.3f} (base rate {cal['brier_baserate']:.3f})\n"
                f"ECE {cal['ece']:.3f}   MCE {cal['mce']:.3f}   n={cal['n']}",
                xy=(0.04, 0.80), fontsize=7.5, color=MUTED)
    ax.legend(loc="lower right")

    # --- AUC against the gate ------------------------------------------------
    auc = h["pooled_auc"]
    lo, hi = h["bootstrap_ci95"]
    ax2.errorbar([0], [auc], yerr=[[auc - lo], [hi - auc]], fmt="o", color=BLUE,
                 capsize=5, capthick=1.2, elinewidth=1.4, markersize=7,
)
    # The same model under a fold that holds out site AND year. Plotted beside the
    # published figure because showing only the leaking interval was the single most
    # misleading thing in an earlier draft of this paper.
    _rec = _json.loads((REPO / "outputs/soiling/audit/reconciliation.json").read_text())
    _b = _rec["bootstrap"]["joint_station_and_year_out|full_40"]
    _j = _rec["results"]["joint_station_and_year_out|full_40"]["pooled_panel_only"]
    _jlo, _jhi = _b["ci95"]
    ax2.errorbar([1.0], [_j], yerr=[[_j - _jlo], [_jhi - _j]], fmt="s", color=ORANGE,
                 capsize=5, capthick=1.2, elinewidth=1.4, markersize=7,
)
    ax2.annotate(f"AUC {_j:.3f}\n95% CI [{_jlo:.3f}, {_jhi:.3f}]\n"
                 f"P(AUC$\\geq$0.70) = {_b['p_ge_070']:.3f}",
                 xy=(1.16, _j + 0.012), fontsize=7.5, color=ORANGE,
                 ha="left", va="bottom")
    ax2.axhline(GATE, color=RED, linestyle="--", linewidth=1.2)
    ax2.axhline(0.5, color=MUTED, linestyle=":", linewidth=1.0)
    ax2.annotate(f"acceptance gate {GATE:.2f}", xy=(-0.44, GATE + 0.010),
                 fontsize=7.5, color=RED, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.88))
    ax2.annotate("chance", xy=(-0.44, 0.512), fontsize=7.5, color=MUTED, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.88))
    ax2.annotate(f"AUC {auc:.3f}   (n={h['pooled_n']})\n"
                 f"95% CI [{lo:.3f}, {hi:.3f}]\n"
                 f"SE {h['hanley_mcneil_se']:.3f}   "
                 f"P(AUC$\\geq$0.70) = {h['p_auc_ge_gate']:.2f}",
                 xy=(0.16, hi + 0.014), fontsize=7.2, color=BLUE,
                 ha="left", va="bottom")
    ax2.set_xlim(-0.5, 2.6)
    ax2.set_ylim(0.45, 0.90)
    ax2.set_xticks([0, 1.0])
    ax2.set_xticklabels(["leave-one-year-out\n(88.7% of rows leak site)",
                         "out-of-site\nAND out-of-year"], fontsize=7.0)
    ax2.set_ylabel("AUC")
    ax2.set_title("The leaking fold, and the corrected one")

    fig.tight_layout()
    save(fig, "fig_risk_validation")


if __name__ == "__main__":
    main()
