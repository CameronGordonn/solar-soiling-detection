# figure_pipeline.py
# Description: Block diagram of the end-to-end pipeline, annotating each stage with its
# public input, licence and the artifact it emits.
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from figstyle import BLUE, ORANGE, GREEN, PURPLE, MUTED, INK, apply_style, save

# (x, y, w, h, title, detail, colour)
BOXES = [
    (0.02, 0.62, 0.20, 0.20, "Aerial imagery",
     "SCC 2025 ortho\n0.204 m GSD\n249 tiles, 14.93 km$^2$", BLUE),
    (0.27, 0.62, 0.20, 0.20, "Tiling + CRS index",
     "640 px chips\naffine + EPSG kept\nper tile", BLUE),
    (0.52, 0.62, 0.20, 0.20, "RF-DETR @728",
     "Apache-2.0\n2$\\times$2 chip grid\nNMS seam merge", BLUE),
    (0.77, 0.62, 0.21, 0.20, "SAM2 mask stage",
     "Apache-2.0\nbox shrunk 15%\narea/GT = 1.01", BLUE),
    (0.77, 0.36, 0.21, 0.18, "Site clustering",
     "parcel APN +\nproximity\n3,362 $\\to$ 1,865", GREEN),
    (0.52, 0.36, 0.20, 0.18, "Roof geometry",
     "USGS 3DEP lidar\ntilt + azimuth\n3,068 fits", GREEN),
    (0.27, 0.36, 0.20, 0.18, "Feature assembly",
     "Open-Meteo, WorldCover\nOSM, USGS DEM\n40 feats (34 in production)", ORANGE),
    (0.02, 0.36, 0.20, 0.18, "Soiling risk model",
     "XGBoost + isotonic\nNREL IWSR labels\n1,002 rows", ORANGE),
    (0.02, 0.09, 0.20, 0.18, "Loss regressor",
     "3 quantile heads\nCQR intervals\nMAE 1.75 pts", ORANGE),
    (0.27, 0.09, 0.20, 0.18, "Economics (MC)",
     "sourced tariffs\n$28.10 per wash measured\n2,000 draws/site", PURPLE),
    (0.52, 0.09, 0.20, 0.18, "Decision",
     "0 of 1,865 sites\nclear a wash", PURPLE),
    (0.77, 0.09, 0.21, 0.18, "Delivery",
     "public dashboard\n+ 50 postcards", PURPLE),
]

ARROWS = [
    ((0.22, 0.72), (0.27, 0.72)), ((0.47, 0.72), (0.52, 0.72)),
    ((0.72, 0.72), (0.77, 0.72)),
    ((0.875, 0.62), (0.875, 0.54)),
    ((0.77, 0.45), (0.72, 0.45)), ((0.52, 0.45), (0.47, 0.45)),
    ((0.27, 0.45), (0.22, 0.45)),
    ((0.12, 0.36), (0.12, 0.27)),
    ((0.22, 0.18), (0.27, 0.18)), ((0.47, 0.18), (0.52, 0.18)),
    ((0.72, 0.18), (0.77, 0.18)),
]


def main() -> None:
    apply_style()
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0.02, 0.92)

    for x, y, w, h, title, detail, colour in BOXES:
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                                    boxstyle="round,pad=0.006,rounding_size=0.012",
                                    linewidth=1.1, edgecolor=colour,
                                    facecolor=colour, alpha=0.10, zorder=2))
        ax.text(x + w / 2, y + h - 0.035, title, ha="center", va="center",
                fontsize=8.2, color=INK, fontweight="bold", zorder=3)
        ax.text(x + w / 2, y + h / 2 - 0.028, detail, ha="center", va="center",
                fontsize=6.4, color=MUTED, linespacing=1.45, zorder=3)

    for (x0, y0), (x1, y1) in ARROWS:
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>",
                                     mutation_scale=9, linewidth=1.0,
                                     color=MUTED, zorder=1))

    ax.text(0.5, 0.875, "Every input is free and public; every stage is reproducible "
                        "from a committed artifact",
            ha="center", fontsize=7.6, color=MUTED, style="italic")
    # Colour key, centred: the colours group stages, they do not label columns.
    key = (("detection", BLUE), ("roof geometry", GREEN),
           ("soiling risk", ORANGE), ("economics + delivery", PURPLE))
    xpos = 0.145
    for label, colour in key:
        ax.add_patch(FancyBboxPatch((xpos, 0.038), 0.018, 0.018,
                                    boxstyle="round,pad=0.002,rounding_size=0.004",
                                    linewidth=0.9, edgecolor=colour,
                                    facecolor=colour, alpha=0.35, zorder=3))
        ax.text(xpos + 0.026, 0.047, label, fontsize=7.2, color=colour,
                va="center", fontweight="bold")
        xpos += 0.030 + 0.0125 * len(label)

    fig.tight_layout()
    save(fig, "fig_pipeline")


if __name__ == "__main__":
    main()
