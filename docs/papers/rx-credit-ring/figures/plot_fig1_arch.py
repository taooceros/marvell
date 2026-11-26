#!/usr/bin/env python3
"""Figure 1: Architectural contrast between (a) Off-Path Dual-Path CEIO and
(b) CreditRing (On-Path Elastic FIFO + Standard NIC DMA + Unfenced 64 B MMIO Credits).
"""
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from paper_style import (
    apply_style, save_fig,
    C_CREDIT, C_CREDIT_LT, C_CREDIT_BG,
    C_CEIO, C_CEIO_LT,
    C_LARGE, C_LARGE_LT,
    C_SHRING, C_SHRING_LT,
    C_DARK, C_GRAY,
)

apply_style()


def rbox(ax, x, y, w, h, text, fc="white", ec=C_GRAY, lw=1.05,
         fs=7.6, weight="normal", tc=C_DARK, r=0.10, ls="-", zorder=3):
    p = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.03,rounding_size={r}",
        facecolor=fc, edgecolor=ec, linewidth=lw, linestyle=ls, zorder=zorder,
    )
    ax.add_patch(p)
    if text:
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fs, fontweight=weight, color=tc, zorder=zorder + 1, linespacing=1.22)
    return p


def arr(ax, x1, y1, x2, y2, color=C_GRAY, lw=1.35, ls="-", rad=0.0, ms=10.0, zorder=5):
    a = FancyArrowPatch(
        (x1, y1), (x2, y2),
        arrowstyle=f"-|>,head_length={ms*0.44:.2f},head_width={ms*0.25:.2f}",
        connectionstyle=f"arc3,rad={rad}",
        color=color, linewidth=lw, linestyle=ls, zorder=zorder,
    )
    ax.add_patch(a)
    return a


def main():
    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(10.8, 4.85), gridspec_kw={"height_ratios": [1.0, 1.04], "hspace": 0.22}
    )

    for ax in (ax_top, ax_bot):
        ax.set_xlim(0, 13.4)
        ax.set_ylim(-0.08, 2.86)
        ax.axis("off")

    # =========================================================================
    # (a) Off-Path Dual-Path Architecture (CEIO)
    # =========================================================================
    ax_top.text(0.06, 2.72,
                "(a) Off-Path Dual-Path Architecture (CEIO): Reactive Flow-Table Rerouting & Host Merge Ring",
                fontsize=9.6, fontweight="bold", color=C_CEIO, va="center")

    rbox(ax_top, 0.06, 0.14, 5.65, 2.26, "", fc="#F8FAFC", ec="#94A3B8", lw=0.95, r=0.12, zorder=1)
    ax_top.text(0.22, 2.22, "Off-Path SmartNIC (ASIC + Sidecar ARM / DRAM)",
                fontsize=8.0, fontweight="bold", color=C_GRAY, va="center")

    ax_top.plot([6.55, 6.55], [0.16, 2.24], color="#94A3B8", ls=":", lw=1.2, zorder=2)
    ax_top.text(6.55, 2.38, "PCIe Bus", fontsize=7.5, color=C_GRAY, ha="center", va="center", fontweight="bold")

    rbox(ax_top, 7.36, 0.14, 5.96, 2.26, "", fc="#F8FAFC", ec="#94A3B8", lw=0.95, r=0.12, zorder=1)
    ax_top.text(7.52, 2.22, "Host Socket (Xeon LLC / 2-Way DDIO)",
                fontsize=8.0, fontweight="bold", color=C_GRAY, va="center")

    rbox(ax_top, 0.22, 0.88, 1.04, 0.90, "100GbE\nMAC +\nRMT", fc="white", ec=C_CEIO, lw=1.1, fs=7.6, weight="bold")
    rbox(ax_top, 1.66, 1.34, 1.76, 0.66, "Fast DMA Path\n(Passive Bypass)", fc=C_CEIO_LT, ec=C_CEIO, lw=1.0, fs=7.4)
    rbox(ax_top, 1.66, 0.32, 1.76, 0.68, "Slow-Path Buffer\n(NIC DRAM Ring)", fc=C_SHRING_LT, ec=C_SHRING, lw=1.0, fs=7.4)
    rbox(ax_top, 3.80, 0.32, 1.74, 0.68, "Flow Controller\nRMT Rewrite &\nPhase Pause", fc=C_LARGE_LT, ec=C_LARGE, lw=1.0, fs=7.1, tc=C_LARGE)

    arr(ax_top, 1.29, 1.52, 1.63, 1.64, color=C_CEIO, lw=1.3)
    arr(ax_top, 1.29, 1.07, 1.63, 0.70, color=C_SHRING, lw=1.3, ls="--")
    arr(ax_top, 3.77, 0.66, 3.45, 0.66, color=C_LARGE, lw=1.1)
    arr(ax_top, 4.66, 1.03, 2.86, 1.32, color=C_LARGE, lw=1.1, ls=":", rad=-0.16)
    ax_top.text(4.62, 1.27, "pause fast DMA\nto preserve order", fontsize=6.9, color=C_LARGE,
                ha="center", va="center", style="italic")

    rbox(ax_top, 7.54, 1.34, 1.90, 0.66, "Fast RX Rings\n2048-slot pool\n(LLC read contend)", fc=C_CEIO_LT, ec=C_CEIO, lw=1.0, fs=7.1)
    rbox(ax_top, 7.54, 0.32, 1.90, 0.68, "Slow Pull Ring\nasync_recv()\nDMA Fetch", fc=C_SHRING_LT, ec=C_SHRING, lw=1.0, fs=7.1)
    rbox(ax_top, 9.82, 0.68, 1.68, 1.00, "2P1C SW\nMerge Ring\n(+75 cyc/pkt\nfixed CPU tax)", fc=C_LARGE_LT, ec=C_LARGE, lw=1.1, fs=7.2, tc=C_LARGE)
    rbox(ax_top, 11.82, 0.68, 1.34, 1.00, "App /\nMemcached\n(custom API)", fc="white", ec=C_GRAY, lw=1.0, fs=7.2)

    arr(ax_top, 3.45, 1.68, 7.50, 1.68, color=C_CEIO, lw=1.55)
    ax_top.text(5.96, 1.90, "Fast DMA writes", fontsize=7.4, color=C_CEIO, ha="center", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.92))

    arr(ax_top, 3.45, 0.44, 7.50, 0.44, color=C_SHRING, lw=1.35, ls="--", rad=0.13)
    ax_top.text(6.55, 0.00, "Slow-path DMA pull + per-flow credit sync", fontsize=7.1, color=C_SHRING,
                ha="center", va="center", bbox=dict(boxstyle="round,pad=0.10", fc="white", ec="none", alpha=0.92))

    arr(ax_top, 9.47, 1.62, 9.79, 1.34, color=C_CEIO, lw=1.2)
    arr(ax_top, 9.47, 0.68, 9.79, 0.98, color=C_SHRING, lw=1.2, ls="--")
    arr(ax_top, 11.53, 1.18, 11.79, 1.18, color=C_DARK, lw=1.25)

    # =========================================================================
    # (b) CreditRing (Ours)
    # =========================================================================
    ax_bot.text(0.06, 2.72,
                "(b) CreditRing (Ours): Single Unified On-Path Buffer + Standard NIC DMA + Unfenced 64 B MMIO Credits",
                fontsize=9.6, fontweight="bold", color=C_CREDIT, va="center")

    rbox(ax_bot, 0.06, 0.14, 5.65, 2.26, "", fc=C_CREDIT_BG, ec=C_CREDIT, lw=1.2, r=0.12, zorder=1)
    ax_bot.text(0.22, 2.22, "On-Path SmartNIC (e.g., Marvell OCTEON 10 CN106)",
                fontsize=8.0, fontweight="bold", color=C_CREDIT, va="center")

    ax_bot.plot([6.55, 6.55], [0.16, 2.24], color="#94A3B8", ls=":", lw=1.2, zorder=2)
    ax_bot.text(6.55, 2.38, "PCIe Bus", fontsize=7.5, color=C_GRAY, ha="center", va="center", fontweight="bold")

    rbox(ax_bot, 7.36, 0.14, 5.96, 2.26, "", fc=C_CREDIT_BG, ec=C_CREDIT, lw=1.2, r=0.12, zorder=1)
    ax_bot.text(7.52, 2.22, "Host Socket (Standard DPDK DMA + DDIO Hot Ring)",
                fontsize=8.0, fontweight="bold", color=C_CREDIT, va="center")

    rbox(ax_bot, 0.22, 1.16, 0.88, 0.84, "100GbE\nMAC +\nRSS", fc="white", ec=C_CREDIT, lw=1.1, fs=7.3, weight="bold")
    rbox(ax_bot, 1.36, 1.16, 1.92, 0.84, "Per-Queue Elastic FIFO\n48 MiB L3 + 6-ch DDR5\n(absorbs bursts & stalls)", fc="white", ec=C_CREDIT, lw=1.1, fs=6.8)
    rbox(ax_bot, 3.54, 1.16, 2.00, 0.84, "O(1) Credit Gate\nsent − taken < R_eff\n(flow-count agnostic)", fc=C_CREDIT_LT, ec=C_CREDIT, lw=1.2, fs=6.7, weight="bold", tc=C_CREDIT)

    rbox(ax_bot, 0.22, 0.28, 3.06, 0.62, "Zero RMT rewrite   |   Zero phase pause\nZero 2P1C merge    |   Zero new host API",
         fc="white", ec="#64748B", lw=0.85, fs=6.8, tc=C_DARK)
    rbox(ax_bot, 3.54, 0.28, 2.00, 0.62, "BAR4 Per-Core Line\n64 B/core in local L3\n(monotonic seqlock)",
         fc="#ECFDF5", ec=C_CREDIT, lw=0.95, fs=6.7, ls="--")

    rbox(ax_bot, 7.54, 1.16, 2.04, 0.84, "Standard NIC DMA\nHot Ring (512 desc)\nPool ≤10 MiB (DDIO)",
         fc=C_CREDIT_LT, ec=C_CREDIT, lw=1.2, fs=6.7, weight="bold", tc=C_DARK)
    rbox(ax_bot, 9.84, 1.16, 1.60, 0.84, "Unmodified App\nMemcached 1.6.45\n/ Stateful NFV",
         fc="white", ec=C_CREDIT, lw=1.1, fs=6.8)
    rbox(ax_bot, 11.66, 1.16, 1.48, 0.84, "Co-Located\n32 MiB Tenant\n(41–43 ns p99)",
         fc="#FEFCE8", ec="#CA8A04", lw=1.05, fs=6.8)

    rbox(ax_bot, 7.54, 0.28, 3.90, 0.62, "Unfenced 64 B MMIO (MOVDIR64B → BAR4)\n≤1 wr/burst (B=32–64)  |  +0.20 cyc/pkt  |  ≤7 Mpps",
         fc="#ECFDF5", ec=C_CREDIT, lw=1.05, fs=6.5, weight="bold", tc=C_CREDIT)
    rbox(ax_bot, 11.66, 0.28, 1.48, 0.62, "DDIO 96–100%\nHost IMC\n−61× to −160×",
         fc=C_CREDIT_LT, ec=C_CREDIT, lw=0.95, fs=6.7, weight="bold", tc=C_CREDIT)

    arr(ax_bot, 1.13, 1.58, 1.33, 1.58, color=C_CREDIT, lw=1.35)
    arr(ax_bot, 3.31, 1.58, 3.51, 1.58, color=C_CREDIT, lw=1.35)
    arr(ax_bot, 5.57, 1.58, 7.50, 1.58, color=C_CREDIT, lw=1.75)
    ax_bot.text(6.55, 1.91, "Normal SDP DMA\n(95.7–103.3 Gbps)", fontsize=6.9, color=C_CREDIT,
                ha="center", va="center", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.95))

    arr(ax_bot, 9.61, 1.58, 9.81, 1.58, color=C_DARK, lw=1.3)
    arr(ax_bot, 8.56, 1.13, 8.56, 0.93, color=C_CREDIT, lw=1.2)
    arr(ax_bot, 7.50, 0.59, 5.57, 0.59, color=C_CREDIT, lw=1.5)
    ax_bot.text(6.55, 0.90, "Credit return\n(no SFENCE)", fontsize=6.8, color=C_CREDIT,
                ha="center", va="center", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.10", fc="white", ec="none", alpha=0.95))
    arr(ax_bot, 4.54, 0.93, 4.54, 1.13, color=C_CREDIT, lw=1.2)

    save_fig(fig, "fig1_architecture")


if __name__ == "__main__":
    main()
