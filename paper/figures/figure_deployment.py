# figure_deployment.py
# Description: What the negative result looks like when it reaches a homeowner. The
# deployed per-roof diagnosis panel, cropped to exclude the map so no individual residence
# is identifiable in the paper.
from __future__ import annotations

from pathlib import Path

import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from figstyle import OUT, apply_style, save

SRC = Path(__file__).resolve().parents[1] / "assets/dashboard_panel.png"


def main() -> None:
    apply_style()
    img = mpimg.imread(SRC)
    h, w = img.shape[0], img.shape[1]
    fig, ax = plt.subplots(figsize=(3.3, 3.3 * h / w))
    ax.imshow(img)
    ax.axis("off")
    fig.tight_layout(pad=0.05)
    # The panel is a screenshot, so it embeds as a raster. Without capping the DPI the
    # figure alone was 1.5 MB, 78% of the Overleaf bundle, for no visible gain.
    fig.savefig(OUT / "fig_deployment.pdf", dpi=170)
    fig.savefig(OUT / "fig_deployment.png", dpi=170)
    print(f"wrote {OUT/'fig_deployment.pdf'} (dpi-capped)")
    plt.close(fig)


if __name__ == "__main__":
    main()
