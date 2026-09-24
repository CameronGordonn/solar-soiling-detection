"""Shared publication style for every figure in the paper.

One module so the figures read as one visual system rather than six ad hoc
notebooks (rewrite brief 2026-08-23, section 9). Import `apply_style()` first,
then `save(fig, name)` to emit vector PDF + a PNG preview.
"""
from __future__ import annotations

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("FIG_OUT", Path(__file__).resolve().parents[1] / "build"))

# Categorical palette: colour-blind safe, legible in grayscale print.
INK = "#1A1A1A"
MUTED = "#6B7280"
GRID = "#D8DCE3"
BLUE = "#2A6FB5"
ORANGE = "#D1701A"
GREEN = "#2E7D5B"
RED = "#B03A3A"
PURPLE = "#6A4C93"
SERIES = (BLUE, ORANGE, GREEN, PURPLE, RED)


def apply_style() -> None:
    plt.rcParams.update({
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "pdf.fonttype": 42,          # embed TrueType, not Type 3 — required by most CS venues
        "ps.fonttype": 42,
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Nimbus Roman", "DejaVu Serif"],
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "axes.edgecolor": MUTED,
        "axes.labelcolor": INK,
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "grid.alpha": 0.9,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "xtick.color": INK,
        "ytick.color": INK,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "lines.linewidth": 1.6,
        "lines.markersize": 4,
    })


def save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        path = OUT / f"{name}.{ext}"
        fig.savefig(path)
        print(f"wrote {path}")
    plt.close(fig)
