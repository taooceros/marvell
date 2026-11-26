#!/usr/bin/env python3
"""Generate Figure 5: Single-host-core 100GbE RX end-to-end line-rate saturation
and large-frame DDIO / host DDR5 efficiency.

Reads verified raw n=3 runs directly from logs/mrv172-2026-11-24/runs.jsonl.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

OUT_DIR = Path(__file__).resolve().parent
RUNS_PATH = Path("/home/hongtao/dev/marvell/logs/mrv172-2026-11-24/runs.jsonl")

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 8.5,
    "axes.edgecolor": "#334155",
    "axes.linewidth": 0.85,
    "axes.grid": True,
    "grid.color": "#E2E8F0",
    "grid.linestyle": "--",
    "grid.linewidth": 0.65,
    "svg.fonttype": "none",
})

C_TEAL = "#0F766E"
C_AMBER = "#D97706"
C_SLATE = "#64748B"
C_RED = "#B91C1C"


def med(rows, fn):
    return statistics.median(fn(r) for r in rows)


def main():
    g = defaultdict(list)
    for ln in RUNS_PATH.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("verified"):
            g[r["cfg"]].append(r)

    sizes = ["64B", "110B", "134B", "326B", "1094B", "4096B"]
    slabs = ["64 B\n(min)", "110 B\n(1 cell)", "134 B\n(2 cells)", "326 B\n(DMA)", "1094 B\n(DMA)", "4096 B\n(DMA)"]
    x = np.arange(len(sizes), dtype=float)
    w = 0.26

    dp_g = [med(g[f"s2_dp_{s}"], lambda r: r["rates"]["host_wire_gbps"]) for s in sizes]
    dp_m = [med(g[f"s2_dp_{s}"], lambda r: r["rates"]["host_rx_mpps"]) for s in sizes]
    ism_g = [med(g[f"s2_sdp_ism1_{s}"], lambda r: r["rates"]["host_wire_gbps"]) for s in sizes]
    rpm_g = [med(g[f"s2_sdp_rpm1_{s}"], lambda r: r["rates"]["host_wire_gbps"]) for s in sizes]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 3.35), gridspec_kw={"width_ratios": [1.45, 1.0]})

    ax1.bar(x - w, dp_g, width=w, color=C_TEAL, edgecolor="white", lw=0.6, label="DirectPush+CR (WC + <=2 MiB DMA)")
    ax1.bar(x, ism_g, width=w, color=C_AMBER, edgecolor="white", lw=0.6, label="SDP Tuned (ISM, 2048 desc)")
    ax1.bar(x + w, rpm_g, width=w, color=C_SLATE, edgecolor="white", lw=0.6, label="SDP Tuned (RPM, 512 desc)")

    ax1.axhline(100.0, color="#1E293B", ls="--", lw=1.2, zorder=2)
    ax1.text(2.98, 114.5, "100GbE Line Rate (100.0 Gbps)", ha="center", va="bottom",
             fontsize=7.0, fontweight="bold", color="#1E293B")

    ax1.hlines(74.67, -0.45, 0.45, colors=C_RED, linestyles=":", lw=1.5, zorder=4)
    ax1.text(0.0, 77.5, "CN106 NIX0 cap\n(111.1 Mpps)", ha="center", va="bottom",
             fontsize=6.6, color=C_RED, fontweight="bold")
    ax1.text(x[0] - w, 62.0, "111.1M", ha="center", va="center",
             fontsize=6.4, fontweight="bold", color="white", rotation=90)

    for i, (gval, mval) in enumerate(zip(dp_g, dp_m)):
        if i == 0:
            continue
        elif i in (1, 2):
            lbl = f"{gval:.1f}G\n({mval:.0f}M)"
        else:
            lbl = f"{gval:.1f}G"
        ax1.text(x[i] - w, gval + 1.8, lbl, ha="center", va="bottom",
                 fontsize=6.5, fontweight="bold", color=C_TEAL)

    ax1.annotate("1.75-1.86x vs SDP\n(0 C2H drops)", xy=(1 - w, 99.5), xytext=(1.55, 76),
                 fontsize=6.9, color=C_TEAL, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=1.0),
                 bbox=dict(boxstyle="round,pad=0.18", fc="#F0FDFA", ec="#5EEAD4", alpha=0.95))

    ax1.set_title("(a) Single-Host-Core 100GbE RX Wire Rate", fontsize=9.1, fontweight="bold", pad=6)
    ax1.set_xlabel("Ethernet Frame Size (L2 bytes; host core pinned at 3.90 GHz)", fontsize=8.3)
    ax1.set_ylabel("Delivered Wire Rate (Gbps)", fontsize=8.3)
    ax1.set_xticks(x)
    ax1.set_xticklabels(slabs, fontsize=7.4)
    ax1.set_ylim(0, 126)
    ax1.legend(loc="lower right", fontsize=6.6, frameon=True, framealpha=0.95)

    lsz = ["1094B", "4096B"]
    xl = np.arange(len(lsz), dtype=float)
    w2 = 0.25

    imc_dp = [med(g[f"s2_dp_{s}"], lambda r: r["host_pmu"]["imc_rd_gbps"] + r["host_pmu"]["imc_wr_gbps"]) for s in lsz]
    imc_ism = [med(g[f"s2_sdp_ism1_{s}"], lambda r: r["host_pmu"]["imc_rd_gbps"] + r["host_pmu"]["imc_wr_gbps"]) for s in lsz]
    imc_rpm = [med(g[f"s2_sdp_rpm1_{s}"], lambda r: r["host_pmu"]["imc_rd_gbps"] + r["host_pmu"]["imc_wr_gbps"]) for s in lsz]

    ddio_dp = [med(g[f"s2_dp_{s}"], lambda r: r["host_pmu"]["ddio_hit_pct"]) for s in lsz]
    ddio_ism = [med(g[f"s2_sdp_ism1_{s}"], lambda r: r["host_pmu"]["ddio_hit_pct"]) for s in lsz]
    ddio_rpm = [med(g[f"s2_sdp_rpm1_{s}"], lambda r: r["host_pmu"]["ddio_hit_pct"]) for s in lsz]

    ax2.bar(xl - w2, imc_dp, width=w2, color=C_TEAL, edgecolor="white", lw=0.6, label="DirectPush+CR (100.0% DDIO)")
    ax2.bar(xl, imc_ism, width=w2, color=C_AMBER, edgecolor="white", lw=0.6, label="SDP ISM 2048 (88.2-88.6% DDIO)")
    ax2.bar(xl + w2, imc_rpm, width=w2, color=C_SLATE, edgecolor="white", lw=0.6, label="SDP RPM 512 (99.7-99.9% DDIO)")

    for i in range(2):
        ax2.text(xl[i] - w2, imc_dp[i] + 0.35, f"{imc_dp[i]:.2f}G\n({ddio_dp[i]:.1f}%)",
                 ha="center", va="bottom", fontsize=6.7, fontweight="bold", color=C_TEAL)
        ax2.text(xl[i], imc_ism[i] + 0.35, f"{imc_ism[i]:.1f}G\n({ddio_ism[i]:.1f}%)",
                 ha="center", va="bottom", fontsize=6.7, fontweight="bold", color=C_AMBER)
        ax2.text(xl[i] + w2, imc_rpm[i] + 0.35, f"{imc_rpm[i]:.2f}G\n({ddio_rpm[i]:.1f}%)",
                 ha="center", va="bottom", fontsize=6.7, color=C_SLATE)

    ax2.annotate("22.5x less host DRAM\n@ 100GbE line rate", xy=(1 - w2, imc_dp[1] + 2.2), xytext=(0.12, 8.6),
                 fontsize=7.2, color=C_TEAL, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=1.05),
                 bbox=dict(boxstyle="round,pad=0.2", fc="#F0FDFA", ec="#5EEAD4", alpha=0.95))

    ax2.set_title("(b) Large-Frame Host DRAM & DDIO @ 100G", fontsize=9.1, fontweight="bold", pad=6)
    ax2.set_xlabel("Frame Size (all 3 schemes at 100.1 Gbps wire)", fontsize=8.3)
    ax2.set_ylabel("Host IMC DRAM Bandwidth (GB/s)", fontsize=8.3)
    ax2.set_xticks(xl)
    ax2.set_xticklabels(["1094 B (1 KiB pay)", "4096 B (4 KiB pay)"], fontsize=8.0)
    ax2.set_ylim(0, 16.8)
    ax2.legend(loc="upper left", fontsize=6.5, frameon=True, framealpha=0.95)

    for ax in (ax1, ax2):
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.tight_layout(pad=0.4, w_pad=1.1)
    fig.savefig(OUT_DIR / "fig5_mrv172_rx_linerate.png", dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT_DIR / "fig5_mrv172_rx_linerate.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("Generated fig5_mrv172_rx_linerate.{png,svg}")


if __name__ == "__main__":
    main()
