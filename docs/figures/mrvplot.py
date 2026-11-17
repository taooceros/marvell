"""Shared style for MRV data plots (MRV-34): experiment data goes to Linear as a chart made
from the raw run data, never as hand-drawn numbers.

Use from a per-issue script next to this file (`docs/figures/mrv-<N>-<topic>.py`) that reads the
run data under `logs/` and calls `save(fig, "mrv-<N>-<topic>")`:

    uv run --with matplotlib python docs/figures/mrv-<N>-<topic>.py
    linear-app figure docs/figures/mrv-<N>-<topic>.png "<alt>"

Chinese labels render through the system's Droid Sans Fallback; white background so the PNG
reads in Linear's light and dark themes.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

# matplotlib's font scan misses the system CJK fallback; register it by file.
CJK = Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf")
if CJK.exists():
    font_manager.fontManager.addfont(str(CJK))

plt.rcParams.update({
    "font.family": ["DejaVu Sans", "Droid Sans Fallback"],  # a family list = per-glyph fallback
    "svg.fonttype": "none",
    "axes.unicode_minus": False,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "figure.dpi": 100,
    "savefig.dpi": 200,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

HERE = Path(__file__).resolve().parent


def save(fig, name):
    """Writes docs/figures/<name>.png (and .svg for the repo) and returns the PNG path."""
    fig.tight_layout()
    png = HERE / f"{name}.png"
    fig.savefig(png)
    fig.savefig(HERE / f"{name}.svg")
    plt.close(fig)
    return png
