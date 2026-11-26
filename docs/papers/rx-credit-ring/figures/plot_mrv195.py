#!/usr/bin/env python3
"""Generate Figure 6: Dual-host 100GbE real in-memory RPC/KV end-to-end evaluation
(saturation throughput, open-loop Poisson p99 knees, and 4 MiB hot vs 128 MiB retained TX pool spill).

Reads ALL verified n=3 runs directly from logs/mrv195-2026-11-25/runs.jsonl.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

OUT_DIR = Path(__file__).resolve().parent
RUNS_PATH = Path("/home/hongtao/dev/marvell/logs/mrv195-2026-11-25/runs.jsonl")

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
C_NAVY = "#1D4ED8"
C_SLATE = "#64748B"
C_RED = "#B91C1C"


def med(xs):
    return statistics.median(xs)


def main():
    rows = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    sat = defaultdict(list)
    pois = defaultdict(list)
    p128 = defaultdict(list)

    for r in rows:
        assert r.get("verified") is True
        p = r["part"]
        if p == "part1_sat":
            sat[(r["policy"], r["workload"], r["key_dist"])].append(r)
        elif p in ("part2b_get64_zipf", "part2b_mix_zipf"):
            pois[(p, r["policy"], r["rate_krps"])].append(r)
        elif p == "part3b_pool128m":
            p128[(r["workload"], r["key_dist"])].append(r)

    hot_tx_mib = int(round(med([r["tx_pool_slots"] for r in sat[("direct_safe", "get1024", "zipf")]]) * 4096 / (1024 * 1024)))
    big_tx_mib = int(round(med([r["tx_pool_slots"] for r in p128[("get1024", "zipf")]]) * 4096 / (1024 * 1024)))
    assert hot_tx_mib == 4 and big_tx_mib == 128

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(11.6, 3.35))

    # --- Panel (a) ---
    wls = [
        ("get64", "zipf", "64B\nGET*"),
        ("get256", "zipf", "256B\nGET*"),
        ("get1024", "zipf", "1KiB\nGET"),
        ("get4096", "zipf", "4KiB\nGET"),
        ("mix", "zipf", "Mix\nZipf"),
        ("mix", "uniform", "Mix\nUnif"),
    ]
    x = np.arange(len(wls), dtype=float)
    w = 0.27

    s_vals = [med([r["client"]["achieved_mrps"] for r in sat[("sdp_fair", wl, kd)]]) for wl, kd, _ in wls]
    p_vals = [med([r["client"]["achieved_mrps"] for r in sat[("direct_peek", wl, kd)]]) for wl, kd, _ in wls]
    d_vals = [med([r["client"]["achieved_mrps"] for r in sat[("direct_safe", wl, kd)]]) for wl, kd, _ in wls]

    ax1.bar(x - w, s_vals, width=w, color=C_SLATE, edgecolor="white", lw=0.6, label="SDP Fair (4Q async)")
    ax1.bar(x, p_vals, width=w, color=C_NAVY, edgecolor="white", lw=0.6, label="DirectPush+CR (peek)")
    ax1.bar(x + w, d_vals, width=w, color=C_TEAL, edgecolor="white", lw=0.6, label="DirectPush+CR (safe)")

    for i, (sv, dv) in enumerate(zip(s_vals, d_vals)):
        pct = (dv - sv) / sv * 100.0
        ax1.text(x[i] + w, dv + 0.45, f"+{pct:.0f}%", ha="center", va="bottom",
                 fontsize=6.8, fontweight="bold", color=C_TEAL)

    ax1.set_title("(a) 1-Core 100GbE KV Saturation", fontsize=9.0, fontweight="bold", pad=6)
    ax1.set_xlabel("Workload (*4-core CN106 capped)", fontsize=8.1)
    ax1.set_ylabel("Throughput (M RPCs/s)", fontsize=8.3)
    ax1.set_xticks(x)
    ax1.set_xticklabels([t[2] for t in wls], fontsize=7.2)
    ax1.set_ylim(0, 22.0)
    ax1.legend(loc="upper right", fontsize=6.45, frameon=True, framealpha=0.95)

    # --- Panel (b) ---
    def curve(part, pol, field):
        rks = sorted(k[2] for k in pois if k[0] == part and k[1] == pol)
        xs = [rk / 1000.0 for rk in rks]
        ys = [med([r["client"][field] for r in pois[(part, pol, rk)]]) for rk in rks]
        return xs, ys

    x_g_s, y_g_s = curve("part2b_get64_zipf", "sdp_fair", "tot_p99_us")
    x_g_d, y_g_d = curve("part2b_get64_zipf", "direct_safe", "tot_p99_us")
    x_m_s, y_m_s = curve("part2b_mix_zipf", "sdp_fair", "small_p99_us")
    x_m_d, y_m_d = curve("part2b_mix_zipf", "direct_safe", "small_p99_us")

    ax2.plot(x_m_s, y_m_s, "s--", color=C_RED, lw=1.8, ms=4.3, label="Mix small p99: SDP Fair")
    ax2.plot(x_m_d, y_m_d, "D-", color=C_NAVY, lw=2.1, ms=4.3, label="Mix small p99: DirectPush+CR")
    ax2.plot(x_g_s, y_g_s, "^--", color=C_SLATE, lw=1.8, ms=4.4, label="64B GET p99: SDP Fair")
    ax2.plot(x_g_d, y_g_d, "o-", color=C_TEAL, lw=2.2, ms=4.5, label="64B GET p99: DirectPush+CR")

    ax2.annotate("-49.7% small p99\n@ 3.5M (32->16 us)", xy=(3.5, 16.4), xytext=(0.25, 34.0),
                 fontsize=6.6, color=C_NAVY, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_NAVY, lw=0.95),
                 bbox=dict(boxstyle="round,pad=0.16", fc="#EFF6FF", ec="#93C5FD", alpha=0.95))
    ax2.annotate("-63.5% p99 @ 7.5M\n(24.4 -> 8.9 us)", xy=(7.5, 9.1), xytext=(5.1, 15.8),
                 fontsize=6.7, color=C_TEAL, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=0.95),
                 bbox=dict(boxstyle="round,pad=0.16", fc="#F0FDFA", ec="#5EEAD4", alpha=0.95))

    ax2.set_title("(b) Open-Loop Poisson p99 Latency", fontsize=9.0, fontweight="bold", pad=6)
    ax2.set_xlabel("Offered Rate (M RPCs/s)", fontsize=8.3)
    ax2.set_ylabel("End-to-End p99 Latency (us)", fontsize=8.3)
    ax2.set_xlim(0, 11.1)
    ax2.set_ylim(0, 41.5)
    ax2.legend(loc="upper right", fontsize=6.1, frameon=True, framealpha=0.95)

    # --- Panel (c): Retained Reply TX Pool (4 MiB L3-Hot vs 128 MiB Spill) ---
    wls_c = [("get1024", "zipf", "1 KiB\nGET"), ("get4096", "zipf", "4 KiB\nGET"), ("mix", "zipf", "Mix\nZipf")]
    xc = np.arange(len(wls_c), dtype=float)
    wc = 0.26

    g_sdp = [med([r["host"]["pmu"]["imc_gbps"] for r in sat[("sdp_fair", wl, kd)]]) for wl, kd, _ in wls_c]
    g_hot = [med([r["host"]["pmu"]["imc_gbps"] for r in sat[("direct_safe", wl, kd)]]) for wl, kd, _ in wls_c]
    g_big = [med([r["host"]["pmu"]["imc_gbps"] for r in p128[(wl, kd)]]) for wl, kd, _ in wls_c]
    tp_drops = [
        (med([r["client"]["achieved_mrps"] for r in p128[(wl, kd)]]) -
         med([r["client"]["achieved_mrps"] for r in sat[("direct_safe", wl, kd)]])) /
        med([r["client"]["achieved_mrps"] for r in sat[("direct_safe", wl, kd)]]) * 100.0
        for wl, kd, _ in wls_c
    ]

    ax3.bar(xc - wc, g_sdp, width=wc, color=C_SLATE, edgecolor="white", lw=0.6, label="SDP Fair (compact pool)")
    ax3.bar(xc, g_hot, width=wc, color=C_TEAL, edgecolor="white", lw=0.6, label=f"DirectPush+CR ({hot_tx_mib} MiB TX pool)")
    ax3.bar(xc + wc, g_big, width=wc, color=C_RED, edgecolor="white", lw=0.6, label=f"DirectPush+CR ({big_tx_mib} MiB TX pool)")

    for i in range(len(wls_c)):
        ratio = g_big[i] / g_hot[i]
        ax3.text(xc[i] + wc, g_big[i] + 0.35, f"{g_big[i]:.1f}G\n({ratio:.0f}x)\n{tp_drops[i]:.0f}% tp",
                 ha="center", va="bottom", fontsize=6.4, fontweight="bold", color=C_RED, linespacing=1.08)
        ax3.text(xc[i], g_hot[i] + 0.35, f"{g_hot[i]:.2f}", ha="center", va="bottom",
                 fontsize=6.5, fontweight="bold", color=C_TEAL)

    ax3.set_title("(c) Retained Reply TX Pool: 4 vs 128 MiB", fontsize=9.0, fontweight="bold", pad=6)
    ax3.set_xlabel("Zipf KV Workload (saturation)", fontsize=8.3)
    ax3.set_ylabel("Host IMC DRAM Bandwidth (GB/s)", fontsize=8.3)
    ax3.set_xticks(xc)
    ax3.set_xticklabels([t[2] for t in wls_c], fontsize=7.6)
    ax3.set_ylim(0, 19.2)
    ax3.legend(loc="upper left", fontsize=6.25, frameon=True, framealpha=0.95)

    for ax in (ax1, ax2, ax3):
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.tight_layout(pad=0.4, w_pad=1.0)
    fig.savefig(OUT_DIR / "fig6_mrv195_rpc_e2e.png", dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT_DIR / "fig6_mrv195_rpc_e2e.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("Generated fig6_mrv195_rpc_e2e.{png,svg}")


if __name__ == "__main__":
    main()
