#!/usr/bin/env python3
"""MRV-118 Figure 3: Matched-throughput Host CPU core savings through Real CN106 NIX0+LBK0."""

import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

RUNS = Path("/home/hongtao/dev/marvell-mrv118/logs/mrv118-2026-11-20/runs.jsonl")


def main():
    by = defaultdict(list)
    cals = []
    for line in RUNS.read_text().splitlines():
        r = json.loads(line)
        assert r["verified"], f"unverified run: {r.get('cfg')}"
        if r["part"] == "part0_cal":
            cals.append(r["units_per_s"])
        else:
            by[r["cfg"]].append(r)

    idle_u = statistics.median(cals)

    def med(cfg, fn):
        return statistics.median(fn(r) for r in by[cfg])

    def cpu_io_pct(cfg):
        aw = med(cfg, lambda r: r["app_work_per_s"])
        return (1.0 - aw / idle_u) * 100.0

    def io_cyc_per_req(cfg):
        pct = cpu_io_pct(cfg) / 100.0
        tot_cyc = med(cfg, lambda r: r["host_pmu"]["cycles"])
        rds = by[cfg][0]["rounds"]
        return tot_cyc * pct / rds

    groups = [
        ("64 B\n1.5 M/s\n(NIX0)", "sdp_nix_64_r1500", "sdp_rpm_nix_64_r1500", "directpush_nix_64_r1500", "rx_poll_mem"),
        ("4 KiB\n0.8 M/s\n(NIX0)", "sdp_nix_4096_r800", "sdp_zc_nix_4096_r800", "directpush_nix_4096_r800", "zero-copy"),
        ("Mix-Ads\n1.5 M/s\n(NIX0)", "sdp_nix_mix_ads_r1500", "sdp_rpm_nix_mix_ads_r1500", "directpush_nix_mix_ads_r1500", "rx_poll_mem"),
        ("Mix-Geo\n0.8 M/s\n(NIX0)", "sdp_nix_mix_geo_r800", "sdp_zc_nix_mix_geo_r800", "directpush_nix_mix_geo_r800", "zero-copy"),
        ("Mix-Ads\n1.5 M/s\n(No-NIX)", "sdp_nonix_mix_ads_r1500", None, "directpush_nonix_mix_ads_r1500", ""),
    ]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.0), gridspec_kw={"width_ratios": [1.4, 1.0]})

    # ---- Panel (a): Host CPU I/O occupancy (%) at matched throughput ----
    x = np.arange(len(groups))
    w = 0.25

    sdp_vals = [cpu_io_pct(g[1]) for g in groups]
    dp_vals = [cpu_io_pct(g[3]) for g in groups]

    # Plot SDP, Ctrl (where present), and DirectPush
    ax1.bar(x - w, sdp_vals, w, color="#64748B", edgecolor="#334155", lw=0.8, label="传统 SDP")
    ctrl_plotted_rpm = False
    ctrl_plotted_zc = False
    for i, g in enumerate(groups):
        if g[2] is not None:
            v = cpu_io_pct(g[2])
            if g[4] == "rx_poll_mem":
                lbl = "对照：SDP (rx_poll_mem=1)" if not ctrl_plotted_rpm else None
                ctrl_plotted_rpm = True
                col, ec = "#F59E0B", "#92400E"
            else:
                lbl = "对照：SDP (zero-copy 跳过 memcpy)" if not ctrl_plotted_zc else None
                ctrl_plotted_zc = True
                col, ec = "#CBD5E1", "#475569"
            ax1.bar(x[i], v, w, color=col, edgecolor=ec, lw=0.8, label=lbl)
            ax1.text(x[i], v + 1.3, f"{v:.1f}%", ha="center", va="bottom", fontsize=7.8, color="#334155", fontweight="bold")
    dp_xs = [x[i] + w if g[2] is not None else x[i] for i, g in enumerate(groups)]
    ax1.bar(dp_xs, dp_vals, w, color="#0F766E", edgecolor="#134E4A", lw=0.8, label="DirectPush + 原生 DPI DMA")

    for i in range(len(groups)):
        ax1.text(x[i] - w, sdp_vals[i] + 1.4, f"{sdp_vals[i]:.1f}%", ha="center", va="bottom",
                 fontsize=8.2, color="#334155", fontweight="bold")
        ax1.text(dp_xs[i], dp_vals[i] + 1.4, f"{dp_vals[i]:.1f}%", ha="center", va="bottom",
                 fontsize=8.4, color="#0F766E", fontweight="bold")
        ratio = sdp_vals[i] / dp_vals[i]
        badge_y = dp_vals[i] + (21.5 if i in (0, 2) else 11.5)
        ax1.text(dp_xs[i] + (0.04 if i in (0, 2) else 0.0), badge_y, f"省 {sdp_vals[i]-dp_vals[i]:.0f}pp\n({ratio:.1f}×)",
                 ha="center", va="bottom", fontsize=7.3, color="#0F766E", fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.18", facecolor="#F0FDFA", edgecolor="#99F6E4", lw=0.8))

    ax1.axvline(3.52, color="#94A3B8", ls=":", lw=1.2)
    ax1.text(4.0, 102.5, "无 NIX 不变性对照", ha="center", va="top", fontsize=8.0, color="#475569", style="italic")

    ax1.set_xticks(x)
    ax1.set_xticklabels([g[0] for g in groups], fontsize=9.0, fontweight="bold")
    ax1.set_ylabel("同吞吐下主机核 I/O 占用率（% of 1 core，越低越好）", fontsize=9.8)
    ax1.set_ylim(0, 126)
    ax1.set_title("(a) 同吞吐主机 CPU 占用率（1 − app_work / idle）", fontsize=10.5, fontweight="bold")
    ax1.legend(loc="upper center", ncol=2, fontsize=7.6, columnspacing=1.0, frameon=True, facecolor="white", framealpha=0.96)

    # ---- Panel (b): I/O cycles/req vs p50 latency Pareto shift (NIX0+LBK0 workloads) ----
    nix_cases = [
        ("64 B @ 1.5M", "sdp_nix_64_r1500", "directpush_nix_64_r1500", "o", (120, -0.45, "left", "top"), (110, -0.35, "left", "top")),
        ("4 KiB @ 0.8M", "sdp_nix_4096_r800", "directpush_nix_4096_r800", "s", (-120, 0.25, "right", "bottom"), (-60, 0.30, "left", "bottom")),
        ("Mix-Ads @ 1.5M", "sdp_nix_mix_ads_r1500", "directpush_nix_mix_ads_r1500", "^", (-120, 0.22, "right", "bottom"), (120, 0.25, "left", "bottom")),
        ("Mix-Geo @ 0.8M", "sdp_nix_mix_geo_r800", "directpush_nix_mix_geo_r800", "D", (120, -0.32, "left", "top"), (120, -0.28, "left", "top")),
    ]
    for lbl, scfg, dcfg, mk, soff, doff in nix_cases:
        sc, sl = io_cyc_per_req(scfg), med(scfg, lambda r: r["lat_all_us"]["p50"])
        dc, dl = io_cyc_per_req(dcfg), med(dcfg, lambda r: r["lat_all_us"]["p50"])
        ax2.annotate(
            "", xy=(dc, dl), xytext=(sc, sl),
            arrowprops=dict(arrowstyle="->", color="#94A3B8", lw=1.3, ls="--", shrinkA=6, shrinkB=6),
        )
        ax2.scatter([sc], [sl], marker=mk, s=72, color="#64748B", edgecolor="#1E293B", zorder=5)
        ax2.scatter([dc], [dl], marker=mk, s=82, color="#0F766E", edgecolor="#134E4A", zorder=6)
        ax2.text(sc + soff[0], sl + soff[1], f"{lbl}\n({sc:.0f} cyc)", fontsize=7.6, color="#334155",
                 ha=soff[2], va=soff[3])
        ax2.text(dc + doff[0], dl + doff[1], f"{dc:.0f} cyc", fontsize=7.8, color="#0F766E",
                 fontweight="bold", ha=doff[2], va=doff[3])

    ax2.scatter([], [], marker="o", s=60, color="#64748B", edgecolor="#1E293B", label="传统 SDP + NIX0")
    ax2.scatter([], [], marker="o", s=65, color="#0F766E", edgecolor="#134E4A", label="DirectPush+DPI + NIX0")
    ax2.set_xlabel("主机 I/O 折算周期（cycles / req，越低越好）", fontsize=9.8)
    ax2.set_ylabel("端到端 p50 延迟（µs，越低越好）", fontsize=9.8)
    ax2.set_xlim(0, 5650)
    ax2.set_ylim(3.5, 13.5)
    ax2.set_title("(b) 真实 NIX0 下延迟 × 主机 CPU 联合 Pareto 迁移", fontsize=10.5, fontweight="bold")
    ax2.legend(loc="lower right", fontsize=8.2, frameon=True, facecolor="white", framealpha=0.95)

    fig.suptitle(
        "MRV-118：真实 CN106 NIX0+LBK0 硬件回环下同吞吐主机 CPU 核数节省与算力余量\n"
        f"条件：1H (cpu40 3.90 GHz, 空闲基准 {idle_u/1e3:.1f} k/s) + 1C (2.00 GHz)，CBR 同速率，100% PMU，n=3 中位数",
        fontsize=10.5, fontweight="bold", y=0.99,
    )
    out = save(fig, "mrv-118-cpu")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
