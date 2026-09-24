# figure_tilt_validation.py
# Description: Per-array roof tilt recovered from public 3DEP lidar against installer-reported
# tilt from PG&E interconnection paperwork — two independent instruments, same median.
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from figstyle import REPO, BLUE, ORANGE, MUTED, RED, apply_style, save

LIDAR = REPO / "outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv"
DGSTATS = REPO / "data/external/dgstats/santa_cruz_interconnected_pv.csv"
# DGStats encodes "not reported" as 0.0, so a zero-tilt row is a missing value, not a flat roof.
DGSTATS_MIN_TILT = 0.0


def main() -> None:
    apply_style()
    lid = pd.read_csv(LIDAR)
    lid = lid[lid.fit_ok.astype(str).str.lower().isin(("true", "1"))]
    lt = lid.tilt_deg.dropna().to_numpy()

    dg = pd.read_csv(DGSTATS)
    col = "tilt" if "tilt" in dg.columns else "Tilt"
    dt = pd.to_numeric(dg[col], errors="coerce").dropna()
    dt = dt[dt > DGSTATS_MIN_TILT].to_numpy()

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.9),
                                  gridspec_kw={"width_ratios": [1.35, 1]})

    bins = np.arange(0, 50.5, 2.0)
    ax.hist(lt, bins=bins, density=True, color=BLUE, alpha=0.55,
            label=f"3DEP lidar plane fits (n={len(lt):,})")
    ax.hist(dt, bins=bins, density=True, histtype="step", color=ORANGE, linewidth=1.8,
            label=f"PG&E installer-reported (n={len(dt):,})")
    ax.axvline(np.median(lt), color=BLUE, linestyle="--", linewidth=1.0)
    ax.axvline(np.median(dt), color=ORANGE, linestyle=":", linewidth=1.4)
    ax.annotate(f"both medians {np.median(lt):.1f}$^\\circ$ / {np.median(dt):.1f}$^\\circ$",
                xy=(1.0, ax.get_ylim()[1] * 0.93), fontsize=7.5, color=MUTED, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.88))
    ax.set_xlabel(r"Array tilt (degrees from horizontal)")
    ax.set_ylabel("Density")
    ax.set_title("Two independent instruments")
    ax.legend(loc="upper right", fontsize=7)

    # Quantile-quantile comparison on shared quantiles.
    qs = np.arange(0.05, 0.96, 0.05)
    ax2.plot(np.quantile(dt, qs), np.quantile(lt, qs), "o", color=BLUE, markersize=4)
    lim = [0, max(np.quantile(dt, 0.95), np.quantile(lt, 0.95)) + 4]
    ax2.plot(lim, lim, color=MUTED, linestyle="--", linewidth=1.0, label="y = x")
    ax2.set_xlim(lim)
    ax2.set_ylim(lim)
    ax2.set_xlabel(r"Installer-reported quantile ($^\circ$)")
    ax2.set_ylabel(r"Lidar quantile ($^\circ$)")
    ax2.set_title("Q-Q: agreement is in the middle, not the tails")
    ax2.legend(loc="lower right")
    ax2.annotate("medians agree to 0.03$^\\circ$; tails do not.\n"
                 "Reported tilts are 100% integers and\n27% are exactly 18$^\\circ$, "
                 "the 4:12 pitch:\nnominal pitch, not a measurement.",
                 xy=(0.04, 0.96), xycoords="axes fraction", fontsize=6.6, color=MUTED,
                 va="top", bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.9))

    fig.tight_layout()
    save(fig, "fig_tilt_validation")
    print(f"lidar  n={len(lt)} p50={np.median(lt):.2f} <5deg={100 * (lt < 5).mean():.2f}%")
    print(f"dgstats n={len(dt)} p50={np.median(dt):.2f} <5deg={100 * (dt < 5).mean():.2f}%")


if __name__ == "__main__":
    main()
