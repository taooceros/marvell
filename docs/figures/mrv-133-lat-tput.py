#!/usr/bin/env python3
"""MRV-133 Fig 2: Real 2-machine 100GbE loaded latency-throughput curves (4 panels)."""

import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot

RUNS = pathlib.Path("/home/hongtao/dev/marvell-mrv133/logs/mrv133-2026-11-21/runs.jsonl")


def load_runs():
    by_cfg = {}
    for line in RUNS.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r.get("verified"), f"Unverified run: {r['cfg']} rep={r['rep']}"
        by_cfg.setdefault(r["cfg"], []).append(r)
    return by_cfg


def stats_of(by_cfg, cfg, key):
    vals = [r["client"][key] for r in by_cfg[cfg]]
    m = statistics.median(vals)
    return m, m - min(vals), max(vals) - m


def main():
    by_cfg = load_runs()
    C_SDP = "#d9534f"
    C_DP = "#1f6feb"
    C_ORG = "#e67e22"
    C_GRN = "#2e8b57"

    fig, axes = plt.subplots(2, 2, figsize=(13.6, 9.4))
    (ax_a, ax_b), (ax_c, ax_d) = axes

    def plot_ol(ax, prefix, rates, col, marker, ls, label, key="tot_p99_us"):
        xs, ys, lo, hi = [], [], [], []
        for r in rates:
            cfg = f"{prefix}{r}"
            if cfg not in by_cfg:
                continue
            xm, _, _ = stats_of(by_cfg, cfg, "achieved_mrps")
            ym, ylo, yhi = stats_of(by_cfg, cfg, key)
            xs.append(xm)
            ys.append(ym)
            lo.append(ylo)
            hi.append(yhi)
        ax.errorbar(xs, ys, yerr=[lo, hi], fmt=f"{marker}{ls}", color=col, lw=2.1, ms=6.0, capsize=3, label=label)
        return xs, ys

    # ---- (a) 64 B open-loop Poisson & B=8 micro-burst ----
    plot_ol(ax_a, "sdp_64_pois_r", [500, 1000, 1500, 2000, 2500, 2750], C_SDP, "s", "-", "SDP 64 B Poisson tot p99")
    plot_ol(ax_a, "direct_64_pois_r", [500, 1000, 1500, 2000, 2500, 3000, 3500], C_DP, "o", "-", "DirectPush 64 B Poisson tot p99")
    plot_ol(ax_a, "sdp_64_b8_r", [1000, 2000], C_SDP, "^", "--", "SDP 64 B 微突发 B=8 p99")
    plot_ol(ax_a, "direct_64_b8_r", [1000, 2000, 3000], C_DP, "D", "--", "DirectPush 64 B 微突发 B=8 p99")
    ax_a.set_xlabel("实测双机吞吐 (Mreq/s)", fontsize=9.8)
    ax_a.set_ylabel("端到端真实 tot p99 延迟 (µs，含 n=3 极差)", fontsize=9.8)
    ax_a.set_title("(a) 64 B 真实双机开环 Poisson 与 B=8 微突发 (W=64)", fontsize=10.8, fontweight="bold")
    ax_a.legend(loc="upper left", fontsize=8.2, frameon=True, facecolor="white")

    # ---- (b) Mix-Ads & Mix-Geo open-loop ----
    plot_ol(ax_b, "sdp_mix_ads_pois_r", [500, 1000, 1500], C_SDP, "s", "-", "SDP Mix-Ads (90/10) Poisson p99")
    plot_ol(ax_b, "direct_mix_ads_pois_r", [500, 1000, 1500, 2000], C_DP, "o", "-", "DirectPush Mix-Ads Poisson p99")
    plot_ol(ax_b, "sdp_mix_geo_pois_r", [300, 600, 900], C_ORG, "v", "--", "SDP Mix-Geo (50/50) Poisson p99")
    plot_ol(ax_b, "direct_mix_geo_pois_r", [300, 600, 900], C_GRN, "D", "--", "DirectPush Mix-Geo Poisson p99")
    plot_ol(ax_b, "direct_mix_geo_pois_r", [300, 600, 900], C_GRN, ".", ":", "DirectPush Mix-Geo 64 B 小包 p99", key="small_p99_us")
    sat_x, _, _ = stats_of(by_cfg, "direct_mix_geo_pois_r1200", "achieved_mrps")
    sat_y, _, _ = stats_of(by_cfg, "direct_mix_geo_pois_r1200", "tot_p99_us")
    ax_b.annotate(
        f"Mix-Geo 1.20 M/s 过载饱和\n({sat_x:.2f} M/s, p99={sat_y:.0f} µs ↑)",
        xy=(0.903, 63.85),
        xytext=(1.15, 62),
        arrowprops=dict(arrowstyle="<-", color=C_GRN, lw=1.3, ls=":"),
        fontsize=8.0,
        color="#14532d",
        bbox=dict(boxstyle="round,pad=0.22", fc="#f0fdf4", ec="#86efac"),
    )
    ax_b.set_ylim(0, 128)
    ax_b.set_xlabel("实测双机吞吐 (Mreq/s)", fontsize=9.8)
    ax_b.set_ylabel("端到端真实 tot p99 延迟 (µs，含 n=3 极差)", fontsize=9.8)
    ax_b.set_title("(b) 混合 RPC/KV（Mix-Ads / Mix-Geo）双机开环 Poisson (W=64)", fontsize=10.8, fontweight="bold")
    ax_b.legend(loc="upper right", fontsize=7.9, frameon=True, facecolor="white")

    # ---- (c) Closed-loop single-size throughput (peak W) ----
    cats = ["64 B\n(W=64)", "256 B\n(W=64)", "1 KiB\n(W=32)", "4 KiB\n(W=32)"]
    sdp_tp = [
        stats_of(by_cfg, "sdp_64_w64", "achieved_mrps")[0],
        stats_of(by_cfg, "sdp_256_w64", "achieved_mrps")[0],
        stats_of(by_cfg, "sdp_1024_w32", "achieved_mrps")[0],
        stats_of(by_cfg, "sdp_4096_w32", "achieved_mrps")[0],
    ]
    rpm_tp = [
        stats_of(by_cfg, "sdp_rpm_64_w64", "achieved_mrps")[0],
        np.nan,
        stats_of(by_cfg, "sdp_rpm_zc_1024_w32", "achieved_mrps")[0],
        stats_of(by_cfg, "sdp_rpm_zc_4096_w32", "achieved_mrps")[0],
    ]
    dp_tp = [
        stats_of(by_cfg, "direct_64_w64", "achieved_mrps")[0],
        stats_of(by_cfg, "direct_256_w64", "achieved_mrps")[0],
        stats_of(by_cfg, "direct_1024_w32", "achieved_mrps")[0],
        stats_of(by_cfg, "direct_4096_w32", "achieved_mrps")[0],
    ]
    xc = np.arange(len(cats))
    wc = 0.25
    ax_c.bar(xc - wc, sdp_tp, wc * 0.92, color=C_SDP, alpha=0.88, label="传统 SDP (默认)")
    ax_c.bar(xc, [0 if np.isnan(v) else v for v in rpm_tp], wc * 0.92, color=C_ORG, alpha=0.85, label="SDP 强对照 (rpm[+zc])")
    ax_c.bar(xc + wc, dp_tp, wc * 0.92, color=C_DP, alpha=0.92, label="DirectPush + DPI DMA")
    for i in range(len(cats)):
        ax_c.text(xc[i] - wc, sdp_tp[i] + 0.08, f"{sdp_tp[i]:.2f}", ha="center", va="bottom", fontsize=8.2)
        if not np.isnan(rpm_tp[i]):
            ax_c.text(xc[i], rpm_tp[i] + 0.08, f"{rpm_tp[i]:.2f}", ha="center", va="bottom", fontsize=8.2)
        ratio = dp_tp[i] / sdp_tp[i]
        ax_c.text(xc[i] + wc, dp_tp[i] + 0.08, f"{dp_tp[i]:.2f}\n({ratio:.2f}×)", ha="center", va="bottom",
                  fontsize=8.2, fontweight="bold", color=C_DP)
    bc_tp = stats_of(by_cfg, "direct_256_bcap8_w64", "achieved_mrps")[0]
    d2_tp = stats_of(by_cfg, "direct_256_dma256_w64", "achieved_mrps")[0]
    ax_c.plot(xc[1] + wc, bc_tp, "D", color="#10b981", ms=7, label=f"256 B bcap8 ({bc_tp:.2f} M/s)")
    ax_c.plot(xc[1] + wc, d2_tp, "^", color="#7c3aed", ms=7, label=f"256 B dma256 ({d2_tp:.2f} M/s)")

    ax_c.set_xticks(xc)
    ax_c.set_xticklabels(cats, fontsize=9.5)
    ax_c.set_ylabel("真实双机闭环峰值吞吐 (Mreq/s)", fontsize=9.8)
    ax_c.set_ylim(0, max(dp_tp) * 1.28)
    ax_c.set_title("(c) 单尺寸闭环峰值吞吐对照（含 256 B 整形与 SDP 强对照）", fontsize=10.8, fontweight="bold")
    ax_c.legend(loc="upper right", fontsize=7.8, frameon=True, facecolor="white")

    # ---- (d) Closed-loop Mix-Ads & Mix-Geo + 64 B small-packet p99 split ----
    ws = [1, 8, 16, 32]
    for wl, col_s, col_d, lbl in [
        ("mix_ads", C_SDP, C_DP, "Mix-Ads (90/10)"),
        ("mix_geo", C_ORG, C_GRN, "Mix-Geo (50/50)"),
    ]:
        xs_s = [stats_of(by_cfg, f"sdp_{wl}_w{w}", "achieved_mrps")[0] for w in ws]
        ys_s = [stats_of(by_cfg, f"sdp_{wl}_w{w}", "small_p99_us")[0] for w in ws]
        xs_d = [stats_of(by_cfg, f"direct_{wl}_w{w}", "achieved_mrps")[0] for w in ws]
        ys_d = [stats_of(by_cfg, f"direct_{wl}_w{w}", "small_p99_us")[0] for w in ws]
        ax_d.plot(xs_s, ys_s, "s--", color=col_s, lw=2.0, ms=6.0, label=f"SDP {lbl} 64 B 小包 p99")
        ax_d.plot(xs_d, ys_d, "o-", color=col_d, lw=2.3, ms=6.5, label=f"DirectPush {lbl} 64 B 小包 p99")

    s_geo_x, _, _ = stats_of(by_cfg, "sdp_mix_geo_w32", "achieved_mrps")
    s_geo_y, _, _ = stats_of(by_cfg, "sdp_mix_geo_w32", "small_p99_us")
    d_geo_x, _, _ = stats_of(by_cfg, "direct_mix_geo_w32", "achieved_mrps")
    d_geo_y, _, _ = stats_of(by_cfg, "direct_mix_geo_w32", "small_p99_us")
    ax_d.annotate(
        f"Mix-Geo W=32\n64B p99: {d_geo_y:.1f} vs {s_geo_y:.1f} µs (−{(1-d_geo_y/s_geo_y)*100:.0f}%)\n吞吐 {d_geo_x/s_geo_x:.2f}×",
        xy=(d_geo_x, d_geo_y),
        xytext=(1.22, 37.5),
        arrowprops=dict(arrowstyle="->", color=C_GRN, lw=1.4),
        fontsize=8.3,
        fontweight="bold",
        color="#14532d",
        bbox=dict(boxstyle="round,pad=0.25", fc="#f0fdf4", ec="#86efac"),
    )
    ax_d.set_ylim(0, 62)
    ax_d.set_xlabel("实测双机混合吞吐 (Mreq/s，W = 1, 8, 16, 32)", fontsize=9.8)
    ax_d.set_ylabel("混合负载中 64 B 小包端到端 p99 延迟 (µs)", fontsize=9.8)
    ax_d.set_title("(d) 混合负载闭环吞吐与 64 B 小包去队头阻塞 p99", fontsize=10.8, fontweight="bold")
    ax_d.legend(loc="upper right", fontsize=8.0, frameon=True, facecolor="white")

    fig.suptitle(
        "MRV-133 真实双机 100GbE 端到端加载曲线（mercury CX5 ⇄ CN106 eth0 RPM0+NIX0 ⇄ saturn，1H+1C，零 DSA，n=3）",
        fontsize=11.8,
        fontweight="bold",
        y=0.99,
    )
    png = mrvplot.save(fig, "mrv-133-lat-tput")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
