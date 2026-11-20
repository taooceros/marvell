#!/usr/bin/env python3
"""MRV-118 Figure 3: Matched-throughput Host CPU core savings through Real CN106 NIX0+LBK0
(Dual-metric disclosure: default SDP + OUT_CNTS MMIO floor + measured rx_poll_mem=1 strong controls)."""

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
    quantum_ns = 1e9 / idle_u

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
        ("64 B\n1.5 M/s\n(NIX0)", "sdp_nix_64_r1500", "sdp_rpm_nix_64_r1500", "directpush_nix_64_r1500", "rpm"),
        ("4 KiB\n0.8 M/s\n(NIX0)", "sdp_nix_4096_r800", "sdp_zc_nix_4096_r800", "directpush_nix_4096_r800", "zc_no_rpm"),
        ("Mix-Ads\n1.5 M/s\n(NIX0)", "sdp_nix_mix_ads_r1500", "sdp_rpm_nix_mix_ads_r1500", "directpush_nix_mix_ads_r1500", "rpm"),
        ("Mix-Geo\n0.8 M/s\n(NIX0)", "sdp_nix_mix_geo_r800", "sdp_zc_nix_mix_geo_r800", "directpush_nix_mix_geo_r800", "zc_no_rpm"),
        ("Mix-Ads\n1.5 M/s\n(No-NIX)", "sdp_nonix_mix_ads_r1500", None, "directpush_nonix_mix_ads_r1500", ""),
    ]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.5), gridspec_kw={"width_ratios": [1.46, 1.0]})

    # ---- Panel (a): Dual-metric Host CPU I/O occupancy (%) ----
    x = np.arange(len(groups))
    w = 0.24

    sdp_vals = [cpu_io_pct(g[1]) for g in groups]
    dp_vals = [cpu_io_pct(g[3]) for g in groups]

    # Zero-load OUT_CNTS MMIO empty-poll floor (~550 ns / (550 + 330.6 ns) ≈ 62.5%)
    mmio_floor = 550.0 / (550.0 + quantum_ns) * 100.0
    ax1.axhline(
        mmio_floor, color="#DC2626", ls="--", lw=1.35, alpha=0.88, zorder=2,
        label=f"≈{mmio_floor:.1f}% 默认 SDP 零流量 OUT_CNTS 空轮询底噪（{quantum_ns:.1f} ns 量子）",
    )

    ax1.bar(x - w, sdp_vals, w, color="#64748B", edgecolor="#334155", lw=0.8,
            label="传统 SDP (默认 MMIO 轮询)", zorder=3)

    ctrl_plotted_rpm = False
    ctrl_plotted_zc = False
    for i, g in enumerate(groups):
        if g[2] is not None:
            v = cpu_io_pct(g[2])
            if g[4] == "rpm":
                lbl = "强对照：SDP (rx_poll_mem=1)" if not ctrl_plotted_rpm else None
                ctrl_plotted_rpm = True
                ax1.bar(x[i], v, w, color="#F59E0B", edgecolor="#92400E", lw=0.85, label=lbl, zorder=3)
                ax1.text(x[i], v + 1.3, f"{v:.1f}%", ha="center", va="bottom",
                         fontsize=7.8, color="#92400E", fontweight="bold")
            else:
                lbl = "不完整对照：SDP 仅 zc（漏开 rx_poll_mem=1）" if not ctrl_plotted_zc else None
                ctrl_plotted_zc = True
                ax1.bar(x[i], v, w, color="#E2E8F0", edgecolor="#991B1B", lw=0.85, hatch="///",
                        label=lbl, zorder=3)
                ax1.text(x[i], v + 1.3, f"{v:.1f}%", ha="center", va="bottom",
                         fontsize=7.6, color="#991B1B", fontweight="bold")
                ax1.text(x[i], 46.5, "漏开\nrpm", ha="center", va="center",
                         fontsize=6.8, color="#991B1B", fontweight="bold",
                         bbox=dict(boxstyle="round,pad=0.14", facecolor="white", edgecolor="#FCA5A5", lw=0.7))

    dp_xs = [x[i] + w if g[2] is not None else x[i] for i, g in enumerate(groups)]
    ax1.bar(dp_xs, dp_vals, w, color="#0F766E", edgecolor="#134E4A", lw=0.85,
            label="DirectPush + 原生 DPI DMA", zorder=3)

    for i, g in enumerate(groups):
        ax1.text(x[i] - w, sdp_vals[i] + 1.4, f"{sdp_vals[i]:.1f}%", ha="center", va="bottom",
                 fontsize=8.0, color="#334155", fontweight="bold")
        ax1.text(dp_xs[i], dp_vals[i] + 1.4, f"{dp_vals[i]:.1f}%", ha="center", va="bottom",
                 fontsize=8.1, color="#0F766E", fontweight="bold")

        r_def = sdp_vals[i] / dp_vals[i]
        if g[4] == "rpm":
            r_ctrl = cpu_io_pct(g[2]) / dp_vals[i]
            badge = f"vs 默认 {r_def:.1f}×\nvs rpm {r_ctrl:.2f}×"
            by_pos = 40.5 if i == 0 else 44.5
            bx_pos = x[i] + 0.14
            bc, ec = "#F0FDFA", "#0F766E"
        elif g[4] == "zc_no_rpm":
            proj_txt = "[推断]~2.34×" if i == 1 else "[推断]~1.8–2.3×"
            badge = f"vs 默认 {r_def:.1f}×\n{proj_txt}"
            by_pos = dp_vals[i] + 10.5
            bx_pos = dp_xs[i] + 0.14
            bc, ec = "#F8FAFC", "#0F766E"
        else:
            badge = f"vs 默认 {r_def:.1f}×\n无 NIX 不变"
            by_pos = dp_vals[i] + 11.5
            bx_pos = dp_xs[i] + 0.12
            bc, ec = "#F0FDFA", "#99F6E4"

        ax1.text(bx_pos, by_pos, badge,
                 ha="center", va="bottom", fontsize=6.4, color="#0F766E", fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.17", facecolor=bc, edgecolor=ec, lw=0.8), zorder=5)

    # Compact tag on the 62.5% floor line inside the open right-side gap (zero bar overlap)
    ax1.text(
        4.22, mmio_floor + 2.0,
        f"≈{mmio_floor:.1f}% 底噪",
        ha="center", va="bottom", fontsize=7.2, fontweight="bold", color="#B91C1C",
        bbox=dict(boxstyle="round,pad=0.14", facecolor="#FEF2F2", edgecolor="#FCA5A5", lw=0.8),
        zorder=6,
    )

    ax1.axvline(3.64, color="#94A3B8", ls=":", lw=1.2)
    ax1.text(4.26, 105.0, "无 NIX\n不变性对照", ha="center", va="top", fontsize=7.6, color="#475569", style="italic")

    ax1.set_xticks(x)
    ax1.set_xticklabels([g[0] for g in groups], fontsize=8.8, fontweight="bold")
    ax1.set_ylabel("同吞吐下主机核 I/O 占用率（% of 1 core，越低越好）", fontsize=9.6)
    ax1.set_ylim(0, 134)
    ax1.set_xlim(-0.55, 4.58)
    ax1.set_title("(a) 双口径同吞吐主机 CPU 占用（默认 SDP vs 强对照）", fontsize=10.4, fontweight="bold")
    ax1.legend(loc="upper center", ncol=2, fontsize=6.9, columnspacing=0.85, frameon=True, facecolor="white", framealpha=0.96)

    # ---- Panel (b): I/O cycles/req vs p50 latency Pareto shift (including measured SDP rx_poll_mem=1) ----
    two_step = [
        ("64 B @ 1.5M", "sdp_nix_64_r1500", "sdp_rpm_nix_64_r1500", "directpush_nix_64_r1500", "o",
         (130, -0.35, "left", "top"), (-95, 0.25, "right", "bottom"), (-35, -0.35, "center", "top")),
        ("Mix-Ads @ 1.5M", "sdp_nix_mix_ads_r1500", "sdp_rpm_nix_mix_ads_r1500", "directpush_nix_mix_ads_r1500", "^",
         (100, 0.25, "left", "bottom"), (110, 0.25, "left", "bottom"), (180, 0.08, "left", "center")),
    ]
    for lbl, scfg, rcfg, dcfg, mk, soff, roff, doff in two_step:
        sc, sl = io_cyc_per_req(scfg), med(scfg, lambda r: r["lat_all_us"]["p50"])
        rc, rl = io_cyc_per_req(rcfg), med(rcfg, lambda r: r["lat_all_us"]["p50"])
        dc, dl = io_cyc_per_req(dcfg), med(dcfg, lambda r: r["lat_all_us"]["p50"])

        ax2.annotate("", xy=(rc, rl), xytext=(sc, sl),
                     arrowprops=dict(arrowstyle="->", color="#94A3B8", lw=1.2, ls="--", shrinkA=6, shrinkB=6))
        ax2.annotate("", xy=(dc, dl), xytext=(rc, rl),
                     arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.6, shrinkA=6, shrinkB=6))

        ax2.scatter([sc], [sl], marker=mk, s=68, color="#64748B", edgecolor="#1E293B", zorder=5)
        ax2.scatter([rc], [rl], marker=mk, s=74, color="#F59E0B", edgecolor="#78350F", zorder=6)
        ax2.scatter([dc], [dl], marker=mk, s=82, color="#0F766E", edgecolor="#134E4A", zorder=7)

        ax2.text(sc + soff[0], sl + soff[1], f"{lbl}\n({sc:.0f} cyc)", fontsize=7.4, color="#334155", ha=soff[2], va=soff[3])
        ax2.text(rc + roff[0], rl + roff[1], f"rpm {rc:.0f}", fontsize=7.3, color="#92400E", fontweight="bold", ha=roff[2], va=roff[3])
        ax2.text(dc + doff[0], dl + doff[1], f"{dc:.0f} cyc", fontsize=7.6, color="#0F766E", fontweight="bold", ha=doff[2], va=doff[3])

    one_step = [
        ("4 KiB @ 0.8M", "sdp_nix_4096_r800", "directpush_nix_4096_r800", "s",
         (-120, 0.25, "right", "bottom"), (-135, -0.04, "right", "center")),
        ("Mix-Geo @ 0.8M", "sdp_nix_mix_geo_r800", "directpush_nix_mix_geo_r800", "D",
         (90, -0.35, "left", "top"), (170, -0.18, "left", "top")),
    ]
    for lbl, scfg, dcfg, mk, soff, doff in one_step:
        sc, sl = io_cyc_per_req(scfg), med(scfg, lambda r: r["lat_all_us"]["p50"])
        dc, dl = io_cyc_per_req(dcfg), med(dcfg, lambda r: r["lat_all_us"]["p50"])
        ax2.annotate("", xy=(dc, dl), xytext=(sc, sl),
                     arrowprops=dict(arrowstyle="->", color="#94A3B8", lw=1.2, ls=":", shrinkA=6, shrinkB=6))
        ax2.scatter([sc], [sl], marker=mk, s=68, color="#64748B", edgecolor="#1E293B", zorder=5)
        ax2.scatter([dc], [dl], marker=mk, s=82, color="#0F766E", edgecolor="#134E4A", zorder=7)
        ax2.text(sc + soff[0], sl + soff[1], f"{lbl}\n({sc:.0f} cyc)", fontsize=7.4, color="#334155", ha=soff[2], va=soff[3])
        ax2.text(dc + doff[0], dl + doff[1], f"{dc:.0f} cyc", fontsize=7.6, color="#0F766E", fontweight="bold", ha=doff[2], va=doff[3])

    ax2.scatter([], [], marker="o", s=58, color="#64748B", edgecolor="#1E293B", label="传统 SDP (默认) + NIX0")
    ax2.scatter([], [], marker="o", s=62, color="#F59E0B", edgecolor="#78350F", label="强对照 SDP (rx_poll_mem=1) + NIX0")
    ax2.scatter([], [], marker="o", s=64, color="#0F766E", edgecolor="#134E4A", label="DirectPush+DPI + NIX0")
    ax2.set_xlabel("主机 I/O 折算周期（cycles / req，越低越好）", fontsize=9.6)
    ax2.set_ylabel("端到端 p50 延迟（µs，越低越好）", fontsize=9.6)
    ax2.set_xlim(-180, 5600)
    ax2.set_ylim(3.6, 13.6)
    ax2.set_title("(b) 真实 NIX0 下延迟 × 主机 CPU 两步 Pareto 迁移", fontsize=10.4, fontweight="bold")
    ax2.legend(loc="lower right", fontsize=7.5, frameon=True, facecolor="white", framealpha=0.95)

    fig.suptitle(
        "MRV-118：真实 CN106 NIX0+LBK0 硬件回环下同吞吐主机 CPU 核数节省（双口径披露）\n"
        f"条件：1H (cpu40 3.90 GHz, 空闲基准 {idle_u/1e3:.1f} k/s = {quantum_ns:.1f} ns/unit) + 1C (2.00 GHz)，CBR 同速率，100% PMU，n=3 中位数",
        fontsize=10.2, fontweight="bold", y=0.99,
    )
    out = save(fig, "mrv-118-cpu")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
