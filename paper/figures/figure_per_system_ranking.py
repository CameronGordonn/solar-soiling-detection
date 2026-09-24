# figure_per_system_ranking.py
# Description: Within-cell pairwise concordance. Systems in the same 0.5-degree cell share
# weather, so a station-label model is pinned to 0.5 by construction. Per-system labels
# and per-system features clear it.
from __future__ import annotations

import json

import numpy as np
import matplotlib.pyplot as plt

from figstyle import REPO, BLUE, ORANGE, MUTED, RED, INK, apply_style, save

SRC = REPO / "outputs/soiling/audit/per_system_ranking.json"
ORDER = ["location only (station-model analogue)", "per-system only",
         "per-system + location"]
SHORT = {"location only (station-model analogue)": "location only\n(station-label analogue)",
         "per-system only": "per-system features\n(tilt, azimuth, kW, age)",
         "per-system + location": "per-system\n+ location"}


def main() -> None:
    apply_style()
    r = json.loads(SRC.read_text())
    res = r["results"]
    keys = [k for k in ORDER if k in res]
    vals = [res[k]["concordance"] for k in keys]

    fig, ax = plt.subplots(figsize=(4.8, 2.9))
    y = np.arange(len(keys))
    cols = [MUTED, BLUE, ORANGE]
    ax.barh(y, [v - 0.5 for v in vals], left=0.5, height=0.55,
            color=cols[:len(keys)])

    ci = res["per-system only"].get("ci95")
    if ci:
        i = keys.index("per-system only")
        ax.errorbar([vals[i]], [i], xerr=[[vals[i] - ci[0]], [ci[1] - vals[i]]],
                    fmt="none", ecolor=INK, elinewidth=1.1, capsize=3, zorder=5)

    ax.axvline(0.5, color=RED, linewidth=1.4)
    ax.text(0.4993, 1.02, "a station-label model cannot exceed this",
            fontsize=7.0, color=RED, va="center", ha="right", rotation=90)

    for yi, v in zip(y, vals):
        off = 0.003 if v >= 0.5 else -0.003
        ax.annotate(f"{v:.3f}", xy=(v + off, yi), va="center",
                    ha="left" if v >= 0.5 else "right",
                    fontsize=8, color=INK)

    ax.set_yticks(y)
    ax.set_yticklabels([SHORT[k] for k in keys], fontsize=7.6)
    ax.set_xlim(0.45, 0.64)
    ax.set_xlabel("within-cell pairwise concordance")
    ax.set_title(f"Ranking roofs that share weather\n"
                 f"{r['n_systems']} systems, {r['n_cells']} cells, "
                 f"{res['per-system only']['n_pairs']:,} same-cell pairs",
                 fontsize=9, color=INK)
    ax.grid(axis="y", visible=False)

    fig.tight_layout()
    save(fig, "fig_per_system_ranking")


if __name__ == "__main__":
    main()
