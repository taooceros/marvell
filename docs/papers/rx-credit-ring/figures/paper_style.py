"""Shared publication matplotlib styling for CreditRing (USENIX/SIGCOMM two-column).

Figures are rendered on a ~10.6 in wide canvas with 9.0-10.0 pt typography so that
when scaled to 7.0 in (\\textwidth) in two-column USENIX layout, text lands at a
crisp 6.0-7.0 pt with generous spacing and zero label/axis collisions.
"""
from pathlib import Path
import matplotlib.pyplot as plt

FIG_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIG_DIR.parents[3]
LOGS_DIR = REPO_ROOT / "logs"

# Colorblind-safe, high-contrast publication palette
C_CREDIT = "#0F766E"     # Deep Teal: CreditRing (ours)
C_CREDIT_LT = "#CCFBF1"
C_CREDIT_BG = "#F0FDFA"

C_LARGE = "#B91C1C"      # Crimson: privRing / Large SDP
C_LARGE_LT = "#FEE2E2"

C_CEIO = "#1D4ED8"       # Royal Blue: CEIO / CEIO-style
C_CEIO_LT = "#DBEAFE"

C_SHRING = "#B45309"     # Amber: ShRing
C_SHRING_LT = "#FEF3C7"

C_BISECT = "#6D28D9"     # Purple: rxBisect / Large LIFO
C_BISECT_LT = "#EDE9FE"

C_SMALL = "#64748B"      # Slate: Small SDP (drop) / Passive Small
C_SMALL_LT = "#F1F5F9"

C_DARK = "#0F172A"
C_GRAY = "#475569"
C_GRID = "#CBD5E1"
C_SAFE_BG = "#DCFCE7"
C_WARN_BG = "#FEE2E2"


def apply_style():
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 9.0,
        "axes.titlesize": 9.8,
        "axes.titleweight": "bold",
        "axes.labelsize": 9.0,
        "axes.edgecolor": "#334155",
        "axes.linewidth": 0.85,
        "axes.facecolor": "white",
        "figure.facecolor": "white",
        "xtick.labelsize": 8.2,
        "ytick.labelsize": 8.2,
        "xtick.color": "#1E293B",
        "ytick.color": "#1E293B",
        "legend.fontsize": 8.4,
        "legend.frameon": True,
        "legend.facecolor": "white",
        "legend.edgecolor": "#CBD5E1",
        "legend.framealpha": 0.96,
        "grid.color": C_GRID,
        "grid.linewidth": 0.6,
        "grid.linestyle": "--",
        "grid.alpha": 0.75,
        "savefig.dpi": 300,
        "svg.fonttype": "none",
    })


def clean_ax(ax, grid_axis="y"):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(length=3.5, width=0.8, pad=2.5)
    if grid_axis:
        ax.grid(True, axis=grid_axis, zorder=0)
        ax.set_axisbelow(True)


def save_fig(fig, stem):
    png = FIG_DIR / f"{stem}.png"
    svg = FIG_DIR / f"{stem}.svg"
    fig.savefig(png, dpi=300, bbox_inches="tight", pad_inches=0.06)
    fig.savefig(svg, bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    print(f"Saved {png.name} and {svg.name}")
