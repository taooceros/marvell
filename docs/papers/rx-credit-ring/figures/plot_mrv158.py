#!/usr/bin/env python3
"""Generate Figure 3: Zero-pollution host cache, system-wide DRAM conservation/offload
duality, and constant sub-microsecond refill hop across 64 KiB -> 128 MiB bursts.

Reads verified raw n=3 runs directly from logs/mrv158-2026-11-23/runs.jsonl.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

OUT_DIR = Path(__file__).resolve().parent
RUNS_PATH = Path("/home/hongtao/dev/marvell/logs/mrv158-2026-11-23/runs.jsonl")

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
C_RED = "#B91C1C"
C_NAVY = "#1D4ED8"
C_AMBER = "#D97706"
C_SLATE = "#475569"

SIZES = ["64KiB", "256KiB", "1MiB", "4MiB", "8MiB", "16MiB", "32MiB", "48MiB", "64MiB", "96MiB", "128MiB"]
XLABS = ["64K", "256K", "1M", "4M", "8M", "16M", "32M", "48M", "64M", "96M", "128M"]
REFILL_SIZES = ["512KiB", "1MiB", "4MiB", "16MiB", "32MiB", "48MiB", "64MiB", "96MiB", "128MiB"]
REFILL_XLABS = ["512K", "1M", "4M", "16M", "32M", "48M", "64M", "96M", "128M"]


def med(xs):
    return statistics.median(xs)


def main():
    rows = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    assert len(rows) == 108
    for r in rows:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0

    g2 = defaultdict(list)
    g3 = defaultdict(list)
    for r in rows:
        if r["part"] == "part2_backlog":
            g2[(r["scheme"], r["size_label"])].append(r)
        elif r["part"] == "part3_refill_hop":
            g3[r["size_label"]].append(r)

    x = np.arange(len(SIZES), dtype=float)

    a_miss = [med([r["host_pmu"]["ocr_l3_miss"] / r["host_pmu"]["ocr_l3_all"] * 100.0 for r in g2[("scheme_a_128m", s)]]) for s in SIZES]
    b_miss = [med([r["host_pmu"]["ocr_l3_miss"] / r["host_pmu"]["ocr_l3_all"] * 100.0 for r in g2[("scheme_b_256k", s)]]) for s in SIZES]
    a_ddio = [med([r["host_pmu"]["ddio_hit_pct"] for r in g2[("scheme_a_128m", s)]]) for s in SIZES]
    b_ddio = [med([r["host_pmu"]["ddio_hit_pct"] for r in g2[("scheme_b_256k", s)]]) for s in SIZES]

    a_host_lpp = [med([(r["host_pmu"]["imc_cas_rd"] + r["host_pmu"]["imc_cas_wr"]) / r["measured_cells"] for r in g2[("scheme_a_128m", s)]]) for s in SIZES]
    b_host_lpp = [med([(r["host_pmu"]["imc_cas_rd"] + r["host_pmu"]["imc_cas_wr"]) / r["measured_cells"] for r in g2[("scheme_b_256k", s)]]) for s in SIZES]
    b_card_lpp = [med([(r["card_pmu"]["ddr_reads"] + r["card_pmu"]["ddr_writes"]) / r["measured_cells"] for r in g2[("scheme_b_256k", s)]]) for s in SIZES]

    xr = np.arange(len(REFILL_SIZES), dtype=float)
    hop_p50 = [med([r["hop_us"]["p50"] for r in g3[s]]) for s in REFILL_SIZES]
    hop_p99 = [med([r["hop_us"]["p99"] for r in g3[s]]) for s in REFILL_SIZES]
    first_med = [med([statistics.median(r["first_hops_us"]) for r in g3[s]]) for s in REFILL_SIZES]

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(11.6, 3.30))

    # --- Panel (a) ---
    ax1.plot(x, a_miss, "o-", color=C_RED, lw=2.0, ms=4.5, label="Host 128 MiB: LLC miss %")
    ax1.plot(x, a_ddio, "s--", color="#F97316", lw=1.6, ms=4.0, label="Host 128 MiB: DDIO hit %")
    ax1.plot(x, b_miss, "D-", color=C_TEAL, lw=2.2, ms=4.5, label="CreditRing: LLC miss %")
    ax1.plot(x, b_ddio, "^--", color="#0EA5E9", lw=1.6, ms=4.2, label="CreditRing: DDIO hit %")
    ax1.axvline(4.0, color="#94A3B8", ls=":", lw=1.1)
    ax1.text(4.15, 43, "2-way DDIO\n(8 MiB)", fontsize=6.9, color=C_SLATE, va="center")
    ax1.annotate("91.2% miss\n16.3% DDIO", xy=(10, a_miss[-1]), xytext=(6.2, 74),
                 fontsize=7.1, color=C_RED, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_RED, lw=1.0),
                 bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#FCA5A5", alpha=0.95))
    ax1.annotate("0.01% miss / 100% DDIO", xy=(7, 0.8), xytext=(2.8, 10.5),
                 fontsize=7.0, color=C_TEAL, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=1.0),
                 bbox=dict(boxstyle="round,pad=0.18", fc="#F0FDFA", ec="#5EEAD4", alpha=0.95))
    ax1.set_title("(a) Host Cache Pollution & DDIO Hit Rate", fontsize=9.0, fontweight="bold", pad=6)
    ax1.set_xlabel("Burst Backlog Size", fontsize=8.3)
    ax1.set_ylabel("Percentage (%)", fontsize=8.3)
    ax1.set_xticks(x)
    ax1.set_xticklabels(XLABS, fontsize=7.0, rotation=32)
    ax1.set_ylim(-5, 110)
    ax1.legend(loc="upper left", fontsize=6.45, frameon=True, framealpha=0.95, bbox_to_anchor=(0.01, 0.90))

    # --- Panel (b) ---
    ax2.axvspan(-0.4, 6.45, color="#ECFDF5", alpha=0.55, zorder=0)
    ax2.axvspan(6.45, 10.4, color="#EFF6FF", alpha=0.55, zorder=0)
    ax2.axvline(6.45, color=C_NAVY, ls=":", lw=1.2)
    ax2.plot(x, a_host_lpp, "o-", color=C_RED, lw=2.0, ms=4.5, label="Host 128 MiB: Host IMC")
    ax2.plot(x, b_host_lpp, "D-", color=C_TEAL, lw=2.2, ms=4.5, label="CreditRing: Host IMC")
    ax2.plot(x, b_card_lpp, "s--", color=C_NAVY, lw=1.9, ms=4.3, label="CreditRing: NIC 6-ch DDR5")
    ax2.text(3.0, 2.40, "NIC 48 MiB L3 absorbs\n(True DRAM elimination)",
             ha="center", va="top", fontsize=6.9, color=C_TEAL, fontweight="bold")
    ax2.text(8.45, 2.40, "NIC DDR5 offload\n(Conserved 2x off-host)",
             ha="center", va="top", fontsize=6.9, color=C_NAVY, fontweight="bold")
    ax2.annotate("-98.9% host DRAM\n(971.5 -> 10.7 MiB)", xy=(10, b_host_lpp[-1]), xytext=(4.5, 0.36),
                 fontsize=7.0, color=C_TEAL, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=1.05),
                 bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#5EEAD4", alpha=0.95))
    ax2.set_title("(b) System DRAM Conservation & Offload", fontsize=9.0, fontweight="bold", pad=6)
    ax2.set_xlabel("Burst Backlog Size", fontsize=8.3)
    ax2.set_ylabel("DRAM Traffic (64 B lines / pkt)", fontsize=8.3)
    ax2.set_xticks(x)
    ax2.set_xticklabels(XLABS, fontsize=7.0, rotation=32)
    ax2.set_xlim(-0.4, 10.4)
    ax2.set_ylim(-0.10, 2.52)
    ax2.legend(loc="center left", fontsize=6.55, frameon=True, framealpha=0.95, bbox_to_anchor=(0.01, 0.62))

    ax3.axvline(5.0, color=C_NAVY, ls=":", lw=1.2)
    ax3.text(5.12, 1.25, "48 MiB\nNIC L3", fontsize=7.0, color=C_NAVY, va="top", fontweight="bold")
    ax3.plot(xr, first_med, "^--", color=C_AMBER, lw=1.8, ms=4.8, label="First cold hop after pause")
    ax3.plot(xr, hop_p99, "s-", color=C_NAVY, lw=1.7, ms=4.2, label="Steady refill hop (p99)")
    ax3.plot(xr, hop_p50, "D-", color=C_TEAL, lw=2.2, ms=4.5, label="Steady refill hop (p50)")
    ax3.annotate("Flat 0.905 us p50\n(p99 0.945 us) across L3",
                 xy=(6.5, hop_p50[6]), xytext=(0.45, 0.785),
                 fontsize=7.0, color=C_TEAL, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=1.0),
                 bbox=dict(boxstyle="round,pad=0.2", fc="#F0FDFA", ec="#5EEAD4", alpha=0.95))
    ax3.annotate("1.163 us cold\nNIC DDR5 hit", xy=(8, first_med[-1]), xytext=(5.20, 1.075),
                 fontsize=6.9, color=C_AMBER, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_AMBER, lw=1.0),
                 bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="#FCD34D", alpha=0.95))
    ax3.set_title("(c) Credit-Return Refill Latency", fontsize=9.0, fontweight="bold", pad=6)
    ax3.set_xlabel("Queued On-NIC Backlog Size", fontsize=8.3)
    ax3.set_ylabel("Credit-Return to Arrival Hop (us)", fontsize=8.3)
    ax3.set_xticks(xr)
    ax3.set_xticklabels(REFILL_XLABS, fontsize=7.1, rotation=30)
    ax3.set_ylim(0.74, 1.29)
    ax3.legend(loc="upper left", fontsize=6.6, frameon=True, framealpha=0.95)

    for ax in (ax1, ax2, ax3):
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.tight_layout(pad=0.4, w_pad=1.0)
    fig.savefig(OUT_DIR / "fig3_mrv158_burst_duality.png", dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT_DIR / "fig3_mrv158_burst_duality.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("Generated fig3_mrv158_burst_duality.{png,svg}")


if __name__ == "__main__":
    main()
