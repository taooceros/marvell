#!/usr/bin/env python3
"""Figure 3 (E2 / MRV-213): Real 100GbE + Standard SDP burst protection, DRAM offload,
net PCIe SDP line-rate draining, and cross-packet-size generalization.
"""
import json
import statistics
from collections import defaultdict
import numpy as np
import matplotlib.pyplot as plt
from paper_style import (
    LOGS_DIR, apply_style, clean_ax, save_fig,
    C_CREDIT, C_LARGE, C_BISECT, C_SHRING, C_SMALL, C_CEIO, C_DARK, C_GRAY,
)

apply_style()

BURSTS = ["64KiB", "256KiB", "1MiB", "4MiB", "16MiB", "32MiB", "64MiB", "128MiB"]
BURST_LBLS = ["64K*", "256K*", "1M", "4M", "16M", "32M", "64M", "128M"]


def med(xs):
    return statistics.median(xs)


def load_runs():
    rows = []
    with open(LOGS_DIR / "mrv213-2026-11-26" / "runs.jsonl") as f:
        for line in f:
            r = json.loads(line)
            assert r["verified"] and r["bad"] == 0 and r["torn"] == 0
            rows.append(r)
    return rows


def main():
    rows = load_runs()
    by_1500 = defaultdict(list)
    by_128m = defaultdict(list)
    kv_solo_1500 = []
    for r in rows:
        if r["part"] == "e2_1500b_kv32m":
            if r["arm_tag"] == "kv_solo":
                kv_solo_1500.append(r["kv"]["p99_ns"])
            else:
                by_1500[(r["arm_tag"], r["burst_label"])].append(r)
        if r["burst_label"] == "128MiB" and r["part"] in ("e2_64b_kv32m", "e2_256b_kv32m", "e2_1500b_kv32m"):
            by_128m[(r["part"], r["arm_tag"])].append(r)

    solo_p99 = med(kv_solo_1500)

    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(10.9, 3.42), gridspec_kw={"width_ratios": [1.14, 1.10, 1.10], "wspace": 0.30}
    )

    # =========================================================================
    # (a) 1500 B Co-Located 32 MiB KV p99 vs Burst Size
    # =========================================================================
    clean_ax(ax1)
    x = np.arange(len(BURSTS))
    series = [
        ("large_mono",  "LargeMono (0% loss)",    C_LARGE,  "s", "--", 1.65),
        ("large_lifo",  "LargeLifo (0% loss)",    C_BISECT, "v", "-.", 1.50),
        ("native_bp",   "NativeBP (35–98% drop)", C_SHRING, "D", ":",  1.35),
        ("small_drop",  "SmallDrop (72–99% drop)",C_SMALL,  "x", ":",  1.30),
        ("credit_ring", "CreditRing (0% loss)",   C_CREDIT, "o", "-",  2.10),
    ]
    for tag, lbl, col, mk, ls, lw in series:
        xs_i, ys_i = [], []
        for idx, b in enumerate(BURSTS):
            rs = by_1500.get((tag, b))
            if rs:
                xs_i.append(idx)
                ys_i.append(med([r["kv"]["p99_ns"] for r in rs]))
        ax1.plot(xs_i, ys_i, marker=mk, ls=ls, lw=lw, ms=4.8, color=col, label=lbl,
                 zorder=4 if tag == "credit_ring" else 3)

    ax1.axhline(solo_p99, color=C_GRAY, ls="--", lw=0.9, zorder=2)
    ax1.text(0.1, solo_p99 - 9, f"Idle KV baseline: {solo_p99:.0f} ns", fontsize=7.0, color=C_GRAY)
    ax1.annotate("LargeLifo fails at 375K\n(72 MiB pre-posted ring)",
                 xy=(1, med([r["kv"]["p99_ns"] for r in by_1500[("large_lifo", "256KiB")]])),
                 xytext=(2.3, 83), fontsize=7.0, color=C_BISECT, fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.16", fc="white", ec=C_BISECT, lw=0.6, alpha=0.94),
                 arrowprops=dict(arrowstyle="->", color=C_BISECT, lw=0.9))
    ax1.text(7.05, 50, "CreditRing: 41–43 ns\n0.00% loss across all bursts",
             ha="right", va="bottom", fontsize=7.2, color=C_CREDIT, fontweight="bold")

    ax1.set_xticks(x)
    ax1.set_xticklabels(BURST_LBLS, fontsize=7.3, rotation=20)
    ax1.set_xlabel("1500 B Burst Size (100GbE line rate)")
    ax1.set_ylabel("Co-Located 32 MiB KV p99 (ns)")
    ax1.set_ylim(20, 232)
    ax1.set_title("(a) 1500 B Co-Located KV Tail & Loss", pad=8)
    ax1.legend(loc="upper left", fontsize=6.9, handlelength=1.9, borderpad=0.32, labelspacing=0.24)

    # =========================================================================
    # (b) 1500 B Host DRAM vs SmartNIC Two-Tier Offload (MiB / burst)
    # =========================================================================
    clean_ax(ax2)
    mem_bursts = ["4MiB", "16MiB", "32MiB", "64MiB", "128MiB"]
    mem_lbls = ["4M", "16M", "32M", "64M", "128M"]
    xm = np.arange(len(mem_bursts))
    w = 0.27

    def host_imc_mib(tag, b):
        return med([(r["host_pmu"]["imc_all_rd_mib"] + r["host_pmu"]["imc_all_wr_mib"]) / r["bursts"]
                    for r in by_1500[(tag, b)]])

    def card_ddr_mib(tag, b):
        return med([(r["card_pmu"]["ddr_rd_mib"] + r["card_pmu"]["ddr_wr_mib"]) / r["bursts"]
                    for r in by_1500[(tag, b)]])

    lm_imc = [host_imc_mib("large_mono", b) for b in mem_bursts]
    cr_imc = [host_imc_mib("credit_ring", b) for b in mem_bursts]
    cr_ddr = [card_ddr_mib("credit_ring", b) for b in mem_bursts]

    ax2.bar(xm - w, lm_imc, w, color=C_LARGE, edgecolor="#1E293B", lw=0.55, label="LargeMono: Host IMC", zorder=3)
    ax2.bar(xm,     cr_imc, w, color=C_CREDIT, edgecolor="#1E293B", lw=0.55, label="CreditRing: Host IMC", zorder=3)
    ax2.bar(xm + w, cr_ddr, w, color=C_CEIO, alpha=0.85, edgecolor="#1E293B", lw=0.55, label="CreditRing: NIC DDR5", zorder=3)

    ax2.set_yscale("log")
    ax2.set_ylim(0.02, 2200)
    ax2.set_xticks(xm)
    ax2.set_xticklabels(mem_lbls, fontsize=8.0)
    ax2.set_xlabel("1500 B Burst Size")
    ax2.set_ylabel("DRAM Traffic (MiB / burst, log)")
    ax2.set_title("(b) Host Offload & NIC L3 → DDR5", pad=8)
    ax2.legend(loc="upper left", fontsize=7.0, borderpad=0.32, labelspacing=0.24)

    ratio_128 = lm_imc[-1] / cr_imc[-1]
    ax2.annotate(f"Host IMC −{ratio_128:.0f}×\n(2.97 vs 181.2 MiB;\nDDIO 99.5%)",
                 xy=(4, cr_imc[-1]), xytext=(3.10, 0.085),
                 ha="center", fontsize=6.6, fontweight="bold", color=C_CREDIT,
                 bbox=dict(boxstyle="round,pad=0.18", fc="#F0FDFA", ec=C_CREDIT, lw=0.65, alpha=0.96),
                 arrowprops=dict(arrowstyle="->", color=C_CREDIT, lw=0.95))
    ax2.annotate("≤16M in NIC L3\n(0.04 MiB DDR5)",
                 xy=(1 + w, max(cr_ddr[1], 0.04)), xytext=(0.90, 0.30),
                 ha="center", fontsize=6.5, fontweight="bold", color=C_CEIO,
                 bbox=dict(boxstyle="round,pad=0.16", fc="#EFF6FF", ec=C_CEIO, lw=0.6, alpha=0.95),
                 arrowprops=dict(arrowstyle="->", color=C_CEIO, lw=0.85))

    # =========================================================================
    # (c) Net PCIe SDP Drain & 128 MiB Cross-Packet-Size Generalization
    # =========================================================================
    clean_ax(ax3)
    sizes = [
        ("e2_64b_kv32m",   "64 B\n(20M cap)"),
        ("e2_256b_kv32m",  "254 B\n(25M cap)"),
        ("e2_1500b_kv32m", "1500 B\n(100G line)"),
    ]
    lm_drain, cr_net_drain, imc_x = [], [], []
    for part, _ in sizes:
        lm_rs = by_128m[(part, "large_mono")]
        cr_rs = by_128m[(part, "credit_ring")]
        lm_drain.append(med([r["drain_wire_gbps"] for r in lm_rs]))
        cr_net_drain.append(med([r["card_stats"]["fifo_deq"] * r["wire_bytes"] * 8.0 / r["drain_s"] / 1e9 for r in cr_rs]))
        lm_i = med([(r["host_pmu"]["imc_all_rd_mib"] + r["host_pmu"]["imc_all_wr_mib"]) / r["bursts"] for r in lm_rs])
        cr_i = med([(r["host_pmu"]["imc_all_rd_mib"] + r["host_pmu"]["imc_all_wr_mib"]) / r["bursts"] for r in cr_rs])
        imc_x.append(lm_i / cr_i)

    x3 = np.arange(len(sizes))
    w3 = 0.34
    ax3.bar(x3 - w3 / 2, lm_drain, w3, color=C_LARGE, alpha=0.88, edgecolor="#1E293B", lw=0.6, label="LargeMono (cold host DRAM)")
    ax3.bar(x3 + w3 / 2, cr_net_drain, w3, color=C_CREDIT, edgecolor="#1E293B", lw=0.6, label="CreditRing (net PCIe SDP)")

    ax3.axhline(100.0, color=C_DARK, ls="--", lw=1.0, zorder=2)
    ax3.text(-0.46, 103.5, "100GbE line rate", fontsize=6.9, color=C_DARK, fontweight="bold")
    ax3.axhline(90.9, color=C_SHRING, ls=":", lw=1.0, zorder=2)
    ax3.text(-0.46, 81.5, "64B TLP wall", fontsize=6.7, color=C_SHRING, fontweight="bold")

    for i in range(3):
        ax3.text(x3[i] - w3 / 2, lm_drain[i] + 2.5, f"{lm_drain[i]:.1f}", ha="center", va="bottom", fontsize=7.3, color=C_LARGE)
        ax3.text(x3[i] + w3 / 2, cr_net_drain[i] + 2.5, f"{cr_net_drain[i]:.1f}", ha="center", va="bottom",
                 fontsize=7.6, color=C_CREDIT, fontweight="bold")
        if i == 0:
            spd = cr_net_drain[i] / lm_drain[i]
            ax3.text(x3[i] + w3 / 2, cr_net_drain[i] + 15.0, f"{spd:.2f}×\n−{imc_x[i]:.0f}× IMC",
                     ha="center", va="bottom", fontsize=6.7, color=C_CREDIT, fontweight="bold")
        elif i == 1:
            spd = cr_net_drain[i] / lm_drain[i]
            ax3.text(x3[i] + w3 / 2, 108.0, f"{spd:.2f}×\n−{imc_x[i]:.0f}× IMC",
                     ha="center", va="bottom", fontsize=6.7, color=C_CREDIT, fontweight="bold")
        else:
            ax3.text(x3[i] + 0.04, cr_net_drain[i] + 16.0, f"Full 100G\n−{imc_x[i]:.0f}× IMC",
                     ha="left", va="bottom", fontsize=6.4, color=C_CREDIT, fontweight="bold", bbox=dict(boxstyle="round,pad=0.06", fc="white", ec="none", alpha=0.85))

    ax3.set_xticks(x3)
    ax3.set_xticklabels([lbl for _, lbl in sizes], fontsize=7.5)
    ax3.set_xlabel("Frame Size (128 MiB Burst)")
    ax3.set_ylabel("Drain Throughput (Gbps wire)")
    ax3.set_ylim(0, 195)
    ax3.set_xlim(-0.56, 2.82)
    ax3.set_title("(c) Net PCIe Drain & Packet Sizes", pad=8)
    ax3.legend(loc="upper left", fontsize=6.8, borderpad=0.30, labelspacing=0.22)

    save_fig(fig, "fig3_e2_burst_100g")


if __name__ == "__main__":
    main()
