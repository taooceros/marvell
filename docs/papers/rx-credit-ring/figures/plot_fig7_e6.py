#!/usr/bin/env python3
"""Figure 7 (E6 / MRV-214): Design-space sensitivity guidelines."""
import json
import statistics
from collections import defaultdict
import numpy as np
import matplotlib.pyplot as plt
from paper_style import (
    LOGS_DIR, apply_style, clean_ax, save_fig,
    C_CREDIT, C_LARGE, C_CEIO, C_SHRING, C_BISECT, C_DARK, C_SAFE_BG,
)

apply_style()


def med(xs):
    return statistics.median(xs)


def load_runs():
    by_cfg = defaultdict(list)
    with open(LOGS_DIR / "mrv214-2026-11-26" / "runs.jsonl") as f:
        for line in f:
            r = json.loads(line)
            assert r["verified"] and r["host_pmu"]["page_faults"] == 0
            by_cfg[r["cfg"]].append(r)
    return by_cfg


def main():
    by_cfg = load_runs()
    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(11.2, 3.52), gridspec_kw={"width_ratios": [1.15, 1.12, 1.08], "wspace": 0.52}
    )

    # =========================================================================
    # (a) R_eff Starvation (left) & Working-Set Spillover Cliff (1500 B)
    # =========================================================================
    clean_ax(ax1)
    reffs = [16, 24, 32, 48, 64, 96, 128, 192, 256]
    reff_mib = [r * 2048 / (1024 * 1024) for r in reffs]
    reff_gbps = [med([x["rates"]["host_wire_gbps"] for x in by_cfg[f"s1a_1500b_r{r}_oq512_b8"]]) for r in reffs]

    ax1.axvspan(0.1875, 10.0, color=C_SAFE_BG, alpha=0.52, zorder=0)
    ax1.plot(reff_mib, reff_gbps, "o-", color=C_CREDIT, lw=1.8, ms=4.5, zorder=4)
    ax1.set_xscale("log", base=2)
    ax1.set_xlim(0.025, 200)
    ax1.set_xticks([0.03125, 0.25, 1, 4, 16, 64, 128])
    ax1.set_xticklabels(["32K", "256K", "1M", "4M", "16M", "64M", "128M"], fontsize=6.7)
    ax1.set_xlabel("In-Flight Window / Host Pool Span")
    ax1.set_ylabel("1500 B Rate (Gbps)", color=C_CREDIT)
    ax1.tick_params(axis="y", colors=C_CREDIT)
    ax1.set_ylim(0, 122)

    ax1_r = ax1.twinx()
    ax1_r.spines["top"].set_visible(False)
    ax1_r.spines["right"].set_color(C_LARGE)
    ax1_r.tick_params(axis="y", colors=C_LARGE, labelsize=7.7, length=3.0, pad=2.0)

    c1_cfgs = [
        ("s1c_1500b_fifo_6m_oq2048_r1024",   6),
        ("s1c_1500b_fifo_8m_oq2048_r1024",   8),
        ("s1c_1500b_fifo_10m_oq2048_r1024", 10),
        ("s1c_1500b_fifo_12m_oq2048_r1024", 12),
        ("s1c_1500b_fifo_32m_oq8192_r4096", 32),
        ("s1c_1500b_fifo_64m_oq8192_r4096", 64),
        ("s1c_1500b_fifo_128m_oq8192_r4096",128),
    ]
    c8_cfgs = [
        ("s1d_1500b_8q_oq256_4m",    4),
        ("s1d_1500b_8q_oq512_8m",    8),
        ("s1d_1500b_8q_oq1024_16m", 16),
        ("s1d_1500b_8q_oq2048_32m", 32),
        ("s1d_1500b_8q_oq4096_64m", 64),
        ("s1d_1500b_8q_oq8192_128m",128),
    ]
    nt_cfgs = [
        ("s1e_1500b_notouch_4m_oq1024_r512",  4),
        ("s1e_1500b_notouch_8m_oq1024_r512",  8),
        ("s1e_1500b_notouch_10m_oq1024_r512",10),
        ("s1e_1500b_notouch_12m_oq1024_r512",12),
        ("s1e_1500b_notouch_16m_oq1024_r512",16),
        ("s1e_1500b_notouch_32m_oq1024_r512",32),
    ]

    imc_tot = lambda rs: med([x["host_pmu"]["imc_rd_gbps"] + x["host_pmu"]["imc_wr_gbps"] for x in rs])
    ax1_r.plot([m for _, m in c8_cfgs], [imc_tot(by_cfg[c]) for c, _ in c8_cfgs],
               "s--", color=C_LARGE, lw=1.6, ms=4.5, zorder=4)
    ax1_r.plot([m for _, m in c1_cfgs], [imc_tot(by_cfg[c]) for c, _ in c1_cfgs],
               "^:", color=C_SHRING, lw=1.4, ms=4.2, zorder=3)
    ax1_r.plot([m for _, m in nt_cfgs], [imc_tot(by_cfg[c]) for c, _ in nt_cfgs],
               "v-", color=C_BISECT, lw=1.2, ms=3.8, zorder=3)

    pit2_y = imc_tot(by_cfg["s1c_1500b_pitfall2_r256_pool128m"])
    ax1_r.scatter([128], [pit2_y], marker="*", s=95, color=C_DARK, zorder=6)

    ax1_r.axvline(60, color=C_LARGE, ls=":", lw=0.95, zorder=2)
    ax1_r.text(44, 152, "60 MiB\nLLC cliff", ha="right", va="top", fontsize=6.4, color=C_LARGE, fontweight="bold")
    ax1.text(1.4, 115, "Safe Zone\nR_eff=128–256\nPool ≤10 MiB", ha="center", va="top",
             fontsize=6.5, color=C_CREDIT, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.18", fc="white", ec=C_CREDIT, lw=0.6, alpha=0.94))
    ax1_r.annotate(f"Pitfall 2:\n{pit2_y:.0f}G DRAM\n(DDIO 43.8%)",
                   xy=(128, pit2_y), xytext=(7.0, 90),
                   ha="center", fontsize=6.3, color=C_DARK, fontweight="bold",
                   bbox=dict(boxstyle="round,pad=0.15", fc="white", ec=C_DARK, lw=0.55),
                   arrowprops=dict(arrowstyle="->", color=C_DARK, lw=0.85))
    ax1_r.text(128, 181, "171.8G (8Q)", ha="right", va="bottom", fontsize=6.6, color=C_LARGE, fontweight="bold")

    ax1_r.set_ylim(-5, 208)
    ax1_r.set_ylabel("Host DRAM Traffic (Gbps)", fontsize=8.3, color=C_LARGE, labelpad=3)
    ax1.set_title("(a) Credit Knee & Working-Set Cliff", pad=8)

    # =========================================================================
    # (b) Credit Batch Granularity B: 64 B Rate Peak & 1500 B B <= R_eff/2
    # =========================================================================
    clean_ax(ax2)
    bs_64 = [8, 16, 32, 64, 128]
    mpps_64 = [med([x["rates"]["host_rx_mpps"] for x in by_cfg[f"s2a_uc64_4c1h_nofence_b{b}"]]) for b in bs_64]
    sf_32 = med([x["rates"]["host_rx_mpps"] for x in by_cfg["s2a_uc64_4c1h_sfence_b32"]])

    bs_r128 = [8, 16, 32, 64, 96, 128]
    bs_r64 = [8, 16, 32, 48, 64, 96]
    gbps_r128 = [med([x["rates"]["host_wire_gbps"] for x in by_cfg[f"s2b_1500b_r128_b{b}"]]) for b in bs_r128]
    gbps_r64 = [med([x["rates"]["host_wire_gbps"] for x in by_cfg[f"s2b_1500b_r64_b{b}"]]) for b in bs_r64]

    ax2.axvspan(28, 70, color=C_SAFE_BG, alpha=0.55, zorder=0)
    ax2.plot(bs_r128, gbps_r128, "s-", color=C_CREDIT, lw=1.8, ms=4.8, label="1500 B (R_eff=128)", zorder=4)
    ax2.plot(bs_r64,  gbps_r64,  "^--", color=C_SHRING, lw=1.45, ms=4.5, label="1500 B (R_eff=64)", zorder=3)

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.spines["right"].set_color(C_CEIO)
    ax2_r.tick_params(axis="y", colors=C_CEIO, labelsize=7.7, length=3.0, pad=2.0)
    ax2_r.plot(bs_64, mpps_64, "o-", color=C_CEIO, lw=1.75, ms=4.8, zorder=5)
    ax2_r.scatter([32], [sf_32], marker="x", s=55, color=C_LARGE, lw=1.8, zorder=6)
    ax2_r.text(35, sf_32 - 3.0, f"+SFENCE {sf_32:.1f}M", fontsize=6.2, color=C_LARGE, fontweight="bold")
    ax2_r.text(44, mpps_64[3] + 3.0, f"64 B peak: {mpps_64[3]:.1f}M", ha="center", fontsize=6.6, color=C_CEIO, fontweight="bold")
    ax2_r.annotate("≤1 wr/burst rule\n(B<64 loops 64/B\n→ 85% overwrite)",
                   xy=(8, mpps_64[0]), xytext=(26, 22),
                   ha="center", fontsize=6.2, color=C_CEIO, fontweight="bold",
                   bbox=dict(boxstyle="round,pad=0.15", fc="#EFF6FF", ec=C_CEIO, lw=0.55),
                   arrowprops=dict(arrowstyle="->", color=C_CEIO, lw=0.8))

    ax2.text(98, 102.0, "B > R_eff/2\nstarvation", ha="center", fontsize=6.4, color=C_CREDIT, fontweight="bold")
    ax2.set_xscale("log", base=2)
    ax2.set_xticks([8, 16, 32, 64, 128])
    ax2.set_xticklabels(["8", "16", "32", "64", "128"], fontsize=7.5)
    ax2.set_xlabel("Credit Batch Size B (packets)")
    ax2.set_ylabel("1500 B Rate (Gbps)", color=C_CREDIT)
    ax2.tick_params(axis="y", colors=C_CREDIT)
    ax2.set_ylim(0, 118)
    ax2_r.set_ylim(10, 88)
    ax2_r.set_ylabel("64 B Single-Core (Mpps)", fontsize=8.3, color=C_CEIO, labelpad=3)
    ax2.set_title("(b) Credit Batching B Tradeoff", pad=8)
    ax2.legend(loc="lower left", fontsize=6.3, borderpad=0.22, labelspacing=0.18)

    # =========================================================================
    # (c) On-Path SmartNIC Core Scaling (1 / 2 / 4 / 8 Card Cores)
    # =========================================================================
    clean_ax(ax3)
    ncs = [1, 2, 4, 8]
    xnc = np.arange(len(ncs))

    g1500 = [med([x["rates"]["host_wire_gbps"] for x in by_cfg[f"s3_1500b_c{nc}_h1_credit"]]) for nc in ncs]
    g254 = [med([x["rates"]["host_wire_gbps"] for x in by_cfg[f"s3_254b_c{nc}_h{min(nc,2)}_credit"]]) for nc in ncs]
    g64_cr = [med([x["rates"]["host_wire_gbps"] for x in by_cfg[f"s3_64b_c{nc}_h{min(nc,4)}_credit"]]) for nc in ncs]
    g64_off = [med([x["rates"]["host_wire_gbps"] for x in by_cfg[f"s3_64b_c{nc}_h{min(nc,4)}_off"]]) for nc in ncs]
    m64_cr8 = med([x["rates"]["host_rx_mpps"] for x in by_cfg["s3_64b_c8_h4_credit"]])
    m64_off8 = med([x["rates"]["host_rx_mpps"] for x in by_cfg["s3_64b_c8_h4_off"]])

    ax3.plot(xnc, g1500,  "s-",  color=C_CREDIT, lw=1.9, ms=5.0, label="1500 B Credit (hc=1)", zorder=4)
    ax3.plot(xnc, g254,   "o-",  color=C_CEIO,   lw=1.9, ms=5.0, label="254 B Credit (hc≤2)", zorder=4)
    ax3.plot(xnc, g64_cr, "^-",  color=C_BISECT, lw=1.8, ms=5.0, label="64 B Credit (hc≤4, 0 C2H)", zorder=4)
    ax3.plot(xnc, g64_off,"x--", color=C_LARGE,  lw=1.35,ms=5.0, label="64 B No-Cred (5.87M drop)", zorder=3)

    ax3.axhline(100.0, color=C_DARK, ls=":", lw=0.9, zorder=2)
    ax3.text(0.05, 102.5, "1c: 98.7G", fontsize=6.8, color=C_CREDIT, fontweight="bold")
    ax3.text(2.0, 102.5, "4c: 100.0G", ha="center", fontsize=6.8, color=C_CEIO, fontweight="bold")
    ax3.annotate(f"8c: {m64_cr8:.1f} vs {m64_off8:.1f} Mpps\n(+4.9%, 0 C2H drop)",
                 xy=(3, g64_cr[-1]), xytext=(0.82, 85),
                 ha="center", fontsize=6.4, color=C_BISECT, fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.18", fc="#F5F3FF", ec=C_BISECT, lw=0.6, alpha=0.95),
                 arrowprops=dict(arrowstyle="->", color=C_BISECT, lw=0.9))

    ax3.set_xticks(xnc)
    ax3.set_xticklabels([f"{c}c" for c in ncs])
    ax3.set_xlabel("CN106 SmartNIC ARM Cores")
    ax3.set_ylabel("Goodput to Host (Gbps)")
    ax3.set_ylim(0, 114)
    ax3.set_title("(c) SmartNIC Core Scaling", pad=8)
    ax3.legend(loc="lower right", fontsize=6.2, borderpad=0.22, labelspacing=0.20)

    save_fig(fig, "fig7_e6_guidelines")


if __name__ == "__main__":
    main()
