#!/usr/bin/env python3
"""Generate Figure 1 (Design Space) and Figure 2 (CEIO vs CreditRing Architecture)
in crisp publication style (300 DPI PNG + vector SVG).
"""
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT_DIR = Path(__file__).resolve().parent

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 8.0,
    "axes.edgecolor": "#334155",
    "axes.linewidth": 0.9,
    "svg.fonttype": "none",
})

C_TEAL = "#0F766E"
C_TEAL_BG = "#CCFBF1"
C_TEAL_LT = "#F0FDFA"
C_NAVY = "#1D4ED8"
C_NAVY_LT = "#EFF6FF"
C_RED = "#B91C1C"
C_RED_LT = "#FEF2F2"
C_AMBER = "#B45309"
C_AMBER_LT = "#FFFBEB"
C_SLATE = "#475569"
C_SLATE_LT = "#F8FAFC"
C_DARK = "#0F172A"


def box(ax, x, y, w, h, text, fc="white", ec=C_SLATE, lw=1.05,
        fs=7.0, weight="normal", tc=C_DARK, r=0.08, ls="-", zorder=3):
    p = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.02,rounding_size={r}",
        facecolor=fc, edgecolor=ec, linewidth=lw, linestyle=ls, zorder=zorder
    )
    ax.add_patch(p)
    if text:
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fs, fontweight=weight, color=tc, zorder=zorder + 1, linespacing=1.22)
    return p


def arrow(ax, x1, y1, x2, y2, color=C_SLATE, lw=1.2, ls="-", rad=0.0, ms=10, zorder=4):
    a = FancyArrowPatch(
        (x1, y1), (x2, y2),
        arrowstyle="-|>",
        connectionstyle=f"arc3,rad={rad}",
        color=color, linewidth=lw, linestyle=ls, mutation_scale=ms, zorder=zorder
    )
    ax.add_patch(a)
    return a


def make_fig1():
    """Single-column crisp 2D design-space figure echoing CEIO Fig. 1,
    adding the path-unification / simplicity dimension."""
    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    ax.set_xlim(0, 10.4)
    ax.set_ylim(0, 6.7)
    ax.axis("off")

    arrow(ax, 1.05, 0.72, 10.15, 0.72, color=C_DARK, lw=1.3, ms=11)
    arrow(ax, 1.05, 0.72, 1.05, 6.45, color=C_DARK, lw=1.3, ms=11)

    ax.text(5.55, 0.25, "Host LLC Isolation & Burst Elasticity  (Zero Drops + Zero Leaky DMA)",
            ha="center", va="center", fontsize=7.9, fontweight="bold", color=C_DARK)
    ax.text(0.36, 3.55, "Datapath & Control Simplicity\n(No Steering, No Path Split)",
            ha="center", va="center", fontsize=7.8, fontweight="bold", color=C_DARK, rotation=90)

    ax.plot([5.35, 5.35], [0.85, 6.30], color="#CBD5E1", ls=":", lw=1.0, zorder=1)
    ax.plot([1.18, 10.05], [3.55, 3.55], color="#CBD5E1", ls=":", lw=1.0, zorder=1)

    opt = FancyBboxPatch((5.50, 3.70), 4.55, 2.55, boxstyle="round,pad=0.03,rounding_size=0.14",
                         facecolor=C_TEAL_LT, edgecolor="#5EEAD4", lw=1.0, ls="--", zorder=1)
    ax.add_patch(opt)
    ax.text(9.92, 6.13, "Optimal Regime", ha="right", va="top",
            fontsize=7.0, fontweight="bold", color=C_TEAL, zorder=2)

    box(ax, 1.32, 4.45, 3.75, 1.48,
        "Large Host Ring / Ideal LIFO\n(128 MiB in host RAM)\nLeaky DMA: 91% LLC miss;\nKV p99 degrades 3.7-6.3x",
        fc=C_RED_LT, ec="#FCA5A5", lw=1.05, fs=6.6, tc="#7F1D1D", r=0.10)

    box(ax, 1.32, 2.40, 3.75, 1.42,
        "Host-Capped Fixed Rings\n(ShRing, rxBisect, Passive 256 KiB)\nHost RAM bound: 75-99.8% burst\ndrops or frequent CCA cutbacks",
        fc=C_AMBER_LT, ec="#FCD34D", lw=1.05, fs=6.5, tc="#78350F", r=0.10)

    box(ax, 1.32, 1.00, 3.75, 1.08,
        "Reactive Rate Control\n(HostCC, RHCC)\nReacts after LLC/IIO misses occur",
        fc=C_SLATE_LT, ec="#CBD5E1", lw=1.05, fs=6.6, tc=C_SLATE, r=0.10)

    box(ax, 5.58, 1.00, 4.38, 2.25,
        "Dual-Path On-NIC Buffering (CEIO)\nProactive credits + NIC DRAM slow path\nFast/slow split, RMT rule churn,\nphase-exclusivity pause, 2P1C SW merge,\nhost async_recv() pull, >1K-flow cliff",
        fc=C_NAVY_LT, ec="#93C5FD", lw=1.1, fs=6.4, tc="#1E3A8A", r=0.10)

    box(ax, 5.65, 3.92, 4.24, 1.92,
        "CreditRing (This Work)\nSingle Unified Credit-Gated Push Path\n256 KiB L3 hot ring + NIC L3/DDR5 FIFO\n0% drops, 100% DDIO, O(1) credit state\nNo steering, no phase pause, no SW merge",
        fc=C_TEAL_BG, ec=C_TEAL, lw=1.65, fs=6.15, weight="bold", tc="#134E4A", r=0.12)

    arrow(ax, 5.10, 2.95, 5.55, 2.45, color=C_NAVY, lw=1.1, ls="--", ms=9)
    arrow(ax, 7.75, 3.28, 7.75, 3.88, color=C_TEAL, lw=1.6, ms=11)
    ax.text(7.90, 3.58, "Unify into\n1 push FIFO", ha="left", va="center",
            fontsize=6.45, fontweight="bold", color=C_TEAL)

    fig.tight_layout(pad=0.2)
    fig.savefig(OUT_DIR / "fig1_design_space.png", dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT_DIR / "fig1_design_space.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("Generated fig1_design_space.{png,svg}")


def make_fig2():
    """Full 2-column architectural comparison: (a) CEIO dual-path vs (b) CreditRing unified push."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.8, 4.05), gridspec_kw={"width_ratios": [1.0, 1.08]})
    for ax in (ax1, ax2):
        ax.set_xlim(0, 10.6)
        ax.set_ylim(0, 7.0)
        ax.axis("off")

    # ================= PANEL (a): CEIO Dual-Path =================
    ax1.text(5.3, 6.72, "(a) Prior Art: CEIO Dual-Path Elastic Buffering",
             ha="center", va="center", fontsize=9.2, fontweight="bold", color="#1E3A8A")

    box(ax1, 0.15, 0.35, 4.45, 5.95, "", fc="#F8FAFC", ec="#94A3B8", lw=1.0, ls="--", r=0.14, zorder=1)
    ax1.text(2.38, 6.08, "SmartNIC (e.g., BlueField-3)", ha="center", va="center",
             fontsize=7.4, fontweight="bold", color=C_SLATE)

    box(ax1, 5.55, 0.35, 4.90, 5.95, "", fc="#F8FAFC", ec="#94A3B8", lw=1.0, ls="--", r=0.14, zorder=1)
    ax1.text(8.00, 6.08, "Host Server (CPU + Shared LLC)", ha="center", va="center",
             fontsize=7.4, fontweight="bold", color=C_SLATE)

    ax1.text(5.08, 6.26, "PCIe", ha="center", va="center", fontsize=7.0, fontweight="bold", color="#64748B")

    box(ax1, 0.35, 4.70, 1.50, 1.00, "Network\nFlows\n(f1..fn)", fc="white", ec=C_SLATE, fs=6.7)
    box(ax1, 2.28, 4.65, 2.10, 1.10, "RMT Flow\nSteering Table\n(per-flow rules)", fc=C_NAVY_LT, ec=C_NAVY, fs=6.6, tc="#1E3A8A")
    box(ax1, 0.35, 2.60, 3.65, 1.15, "Per-Flow Credit Controller\n(polls rule hits + host head;\nrewrites RMT rules & timers)", fc="#FEF3C7", ec="#D97706", fs=6.4, tc="#78350F")
    box(ax1, 0.55, 0.65, 3.65, 1.15, "Slow-Path Ring\n(On-NIC DRAM Buffer)\nHeld until phase pause", fc=C_RED_LT, ec="#EF4444", fs=6.6, tc="#7F1D1D")

    box(ax1, 5.80, 4.65, 2.40, 1.10, "Fast-Path Ring\n(Host LLC)\nPaused on drain", fc=C_NAVY_LT, ec=C_NAVY, fs=6.6, tc="#1E3A8A")
    box(ax1, 5.80, 2.52, 2.40, 1.38, "2P1C Software\nMerge Ring\n(unifies fast &\nslow rings)", fc="#FEF3C7", ec="#D97706", fs=6.5, tc="#78350F")
    box(ax1, 5.80, 0.65, 2.40, 1.15, "CEIO Driver\n(async_recv()\nhost DMA pull)", fc=C_RED_LT, ec="#EF4444", fs=6.6, tc="#7F1D1D")
    box(ax1, 8.65, 2.56, 1.58, 1.30, "Host\nCPU\nApp", fc="white", ec=C_DARK, lw=1.2, fs=7.2, weight="bold")

    box(ax1, 8.55, 0.65, 1.72, 1.28,
        "Added Cost:\nRMT churn\nPhase pause\n2P1C merge",
        fc="#FFF1F2", ec="#FDA4AF", lw=0.9, fs=6.25, tc="#9F1239", r=0.08)

    arrow(ax1, 1.88, 5.20, 2.25, 5.20, color=C_DARK, lw=1.2, ms=9)
    arrow(ax1, 4.41, 5.20, 5.76, 5.20, color=C_NAVY, lw=1.5, ms=10)
    ax1.text(5.08, 5.38, "Fast\nDMA", ha="center", va="bottom", fontsize=6.4, color=C_NAVY, fontweight="bold")

    arrow(ax1, 4.25, 4.62, 4.25, 1.83, color=C_RED, lw=1.35, ls="--", ms=9)
    ax1.text(4.42, 3.22, "No credit", ha="center", va="center", fontsize=6.1, color=C_RED, rotation=90)

    arrow(ax1, 2.75, 3.78, 3.05, 4.62, color=C_AMBER, lw=1.15, ls=":", ms=9)
    ax1.text(2.15, 4.18, "Rewrite\nrules", ha="center", va="center", fontsize=6.0, color=C_AMBER)

    arrow(ax1, 4.23, 1.22, 5.76, 1.22, color=C_RED, lw=1.45, ls="--", ms=10)
    ax1.text(5.00, 0.98, "Host DMA\nread pull", ha="center", va="top", fontsize=6.2, color=C_RED, fontweight="bold")

    arrow(ax1, 7.00, 4.62, 7.00, 3.94, color=C_NAVY, lw=1.35, ms=9)
    arrow(ax1, 7.00, 1.83, 7.00, 2.49, color=C_RED, lw=1.35, ls="--", ms=9)
    arrow(ax1, 8.23, 3.21, 8.62, 3.21, color=C_DARK, lw=1.3, ms=10)

    # ================= PANEL (b): CreditRing Unified Push =================
    ax2.text(5.3, 6.72, "(b) CreditRing: Single Unified Credit-Gated Push Path",
             ha="center", va="center", fontsize=9.2, fontweight="bold", color=C_TEAL)

    box(ax2, 0.15, 0.35, 4.62, 5.95, "", fc=C_TEAL_LT, ec="#5EEAD4", lw=1.1, ls="--", r=0.14, zorder=1)
    ax2.text(2.46, 6.08, "Active NIC (CN106: 48 MiB L3 + 6-ch DDR5)", ha="center", va="center",
             fontsize=7.25, fontweight="bold", color=C_TEAL)

    box(ax2, 5.52, 0.35, 4.93, 5.95, "", fc="#F8FAFC", ec="#94A3B8", lw=1.0, ls="--", r=0.14, zorder=1)
    ax2.text(7.98, 6.08, "Host Server (Xeon 60 MiB L3, 2-way DDIO)", ha="center", va="center",
             fontsize=7.25, fontweight="bold", color=C_SLATE)

    ax2.text(5.14, 6.26, "PCIe", ha="center", va="center", fontsize=7.0, fontweight="bold", color="#64748B")

    box(ax2, 0.32, 4.35, 1.20, 1.15, "100GbE\nWire RX\n(All Flows)", fc="white", ec=C_SLATE, fs=6.6)
    box(ax2, 1.85, 4.02, 2.72, 1.72,
        "On-NIC Elastic FIFO\n48 MiB L3 (<=32 MiB:\nzero system DRAM)\n-> 6-ch DDR5 (>48 MiB:\n100% host offload)",
        fc="white", ec=C_TEAL, lw=1.25, fs=6.45, tc="#134E4A")
    box(ax2, 1.85, 2.30, 2.72, 1.18,
        "O(1) Credit Gate\npushed - taken < R\n(NIC-local cached counter;\n+6 ns zero-backlog cost)",
        fc=C_TEAL_BG, ec=C_TEAL, lw=1.35, fs=6.45, weight="bold", tc="#134E4A")
    box(ax2, 0.35, 0.65, 4.22, 0.95,
        "TAKEN Credit Register (BAR4 MMIO)\nUpdated in batches (1 posted write / 512 cells)",
        fc="#F0FDF4", ec="#16A34A", lw=1.05, fs=6.4, tc="#14532D")

    # Host L3-pinned RX ring ONLY (no fabricated 5 MiB RX pool!)
    box(ax2, 5.72, 2.35, 2.66, 3.40, "", fc="#ECFDF5", ec=C_TEAL, lw=1.15, ls="-", r=0.10, zorder=2)
    ax2.text(7.05, 5.56, "2-Way DDIO (100% Hit)", ha="center", va="center",
             fontsize=6.7, fontweight="bold", color=C_TEAL, zorder=3)
    box(ax2, 5.88, 3.88, 2.34, 1.42,
        "256 KiB RX Hot Ring\n(4,096 x 64 B cells)\nInline WC push\n(head/tail seq guards)",
        fc="white", ec=C_TEAL, lw=1.35, fs=6.45, weight="bold", tc="#134E4A", zorder=3)
    box(ax2, 5.88, 2.52, 2.34, 1.15,
        "Large-Frame Extension\nCompact DPI DMA RX\n(<=2 MiB total; 100%\nDDIO @ 100GbE)",
        fc=C_NAVY_LT, ec=C_NAVY, lw=1.05, ls="--", fs=6.25, tc="#1E3A8A", zorder=3)

    box(ax2, 8.78, 4.02, 1.46, 1.32, "Host RX\nCore\n(polls 1\nFIFO ring)", fc="white", ec=C_DARK, lw=1.2, fs=6.55, weight="bold")
    box(ax2, 8.78, 2.35, 1.46, 1.32, "Co-Located\n32 MiB KV\n(p99 41 ns\nuntouched)", fc="#F0FDF4", ec="#16A34A", lw=1.1, fs=6.35, tc="#14532D")

    box(ax2, 5.72, 0.65, 4.52, 0.95,
        "Host DDR5: -98.9% Traffic (971.5 -> 10.7 MiB)\nZero path switch  |  Zero SW merge  |  0.0% drops",
        fc="#F0FDFA", ec=C_TEAL, lw=1.05, fs=6.35, weight="bold", tc="#134E4A")

    arrow(ax2, 1.55, 4.92, 1.82, 4.92, color=C_DARK, lw=1.2, ms=9)
    arrow(ax2, 3.21, 4.00, 3.21, 3.51, color=C_TEAL, lw=1.35, ms=9)

    arrow(ax2, 4.60, 3.10, 5.85, 4.58, color=C_TEAL, lw=1.7, rad=-0.06, ms=10)
    arrow(ax2, 4.60, 2.82, 5.85, 3.08, color=C_NAVY, lw=1.25, ls="--", rad=0.04, ms=9)
    ax2.text(5.14, 4.42, "Unified\nFIFO\npush", ha="center", va="center", fontsize=6.4, fontweight="bold", color=C_TEAL)

    arrow(ax2, 8.25, 4.66, 8.75, 4.66, color=C_DARK, lw=1.25, ms=9)
    arrow(ax2, 8.25, 3.15, 8.75, 4.30, color=C_NAVY, lw=1.05, ls="--", rad=0.08, ms=9)

    ax2.plot([10.26, 10.36, 10.36, 5.14, 5.14], [4.55, 4.55, 1.94, 1.94, 1.12], color="#16A34A", lw=1.35, zorder=4)
    arrow(ax2, 5.14, 1.12, 4.60, 1.12, color="#16A34A", lw=1.35, ms=10)
    ax2.text(7.72, 1.94, "Batched TAKEN credit (posted MMIO)", ha="center", va="center",
             fontsize=6.25, fontweight="bold", color="#15803D",
             bbox=dict(boxstyle="round,pad=0.14", fc="white", ec="#BBF7D0", lw=0.7), zorder=5)
    arrow(ax2, 3.21, 1.63, 3.21, 2.27, color="#16A34A", lw=1.35, ms=9)

    fig.tight_layout(pad=0.2, w_pad=0.5)
    fig.savefig(OUT_DIR / "fig2_architecture.png", dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT_DIR / "fig2_architecture.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("Generated fig2_architecture.{png,svg}")


if __name__ == "__main__":
    make_fig1()
    make_fig2()
