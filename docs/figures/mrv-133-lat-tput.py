#!/usr/bin/env python3
"""MRV-133 Fig 2: Real 2-machine 100GbE loaded latency-throughput curves (4 panels).

Revisions:
- Explicitly labels default `SDP` (without `rx_poll_mem=1`) on open-loop (a, b) and mixed closed-loop (d).
- Panel (b): adds symmetric `SDP 默认 Mix-Geo 64 B 小包 p99` and all 12 verified `Mix-Ads B=8` micro-burst runs.
- Panel (c): clarifies `1C` card-core bridge ceiling (`card_echo 64B = 6.33 M/s`), `256 B = 12.77 M 行/s < 13.4 M`
  cliff, and labels `bcap8` accurately as local per-poll drain cap (not MRV-115 in-flight line credit).
- Panel (d): plots BOTH `64 B 小包 p99` AND overall `tot p99` for `Mix-Ads` & `Mix-Geo`, transparently showing
  the `Mix-Geo W=32` `tot p99` trade-off (`56.5 > 51.7 µs`) alongside the `64 B` HoL reduction (`30.4 vs 50.4 µs`).
"""

import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot

RUNS = pathlib.Path(__file__).resolve().parents[2] / "logs/mrv133-2026-11-21/runs.jsonl"


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

    fig, axes = plt.subplots(2, 2, figsize=(14.0, 9.8))
    (ax_a, ax_b), (ax_c, ax_d) = axes

    def plot_ol(ax, prefix, rates, col, marker, ls, label, key="tot_p99_us", lw=2.0, ms=5.8, mfc=None, alpha=0.95):
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
        kw = dict(fmt=f"{marker}{ls}", color=col, lw=lw, ms=ms, capsize=3, alpha=alpha, label=label)
        if mfc is not None:
            kw["mfc"] = mfc
        ax.errorbar(xs, ys, yerr=[lo, hi], **kw)
        return xs, ys

    # ---- (a) 64 B open-loop Poisson & B=8 micro-burst ----
    plot_ol(ax_a, "sdp_64_pois_r", [500, 1000, 1500, 2000, 2500, 2750], C_SDP, "s", "-", "SDP 默认 64 B Poisson tot p99")
    plot_ol(ax_a, "direct_64_pois_r", [500, 1000, 1500, 2000, 2500, 3000, 3500], C_DP, "o", "-", "DirectPush 64 B Poisson tot p99")
    plot_ol(ax_a, "sdp_64_b8_r", [1000, 2000], C_SDP, "^", "--", "SDP 默认 64 B 微突发 B=8 p99")
    plot_ol(ax_a, "direct_64_b8_r", [1000, 2000, 3000], C_DP, "D", "--", "DirectPush 64 B 微突发 B=8 p99")
    ax_a.annotate(
        "rep=0 偶发尖峰 144 µs\n(另两轮 18.7 / 19.1 µs)",
        xy=(2.992, 144.05),
        xytext=(1.75, 126),
        arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.1, ls=":"),
        fontsize=7.6,
        color="#1e3a8a",
        bbox=dict(boxstyle="round,pad=0.2", fc="#eff6ff", ec="#bfdbfe", lw=0.8),
    )
    ax_a.set_ylim(0, 156)
    ax_a.set_xlabel("实测双机吞吐 (Mreq/s)", fontsize=9.8)
    ax_a.set_ylabel("端到端真实 tot p99 延迟 (µs，含 n=3 极差)", fontsize=9.8)
    ax_a.set_title("(a) 64 B 开环 Poisson 与 B=8 微突发 (W=64；1C 卡核上限；单轮 29–69 ms)", fontsize=10.2, fontweight="bold")
    ax_a.legend(loc="upper left", fontsize=7.9, frameon=True, facecolor="white", framealpha=0.94)

    # ---- (b) Mix-Ads (Poisson + B=8) & Mix-Geo (Poisson tot + symmetric 64 B small p99) ----
    plot_ol(ax_b, "sdp_mix_ads_pois_r", [500, 1000, 1500], C_SDP, "s", "-", "SDP 默认 Mix-Ads Poisson tot p99")
    plot_ol(ax_b, "direct_mix_ads_pois_r", [500, 1000, 1500, 2000], C_DP, "o", "-", "DirectPush Mix-Ads Poisson tot p99")
    plot_ol(ax_b, "sdp_mix_ads_b8_r", [1000, 1500], C_SDP, "^", "--", "SDP 默认 Mix-Ads B=8 微突发 p99", lw=1.7, ms=5.5, mfc="white")
    plot_ol(ax_b, "direct_mix_ads_b8_r", [1000, 1500], C_DP, "D", "--", "DirectPush Mix-Ads B=8 微突发 p99", lw=1.7, ms=5.5, mfc="white")
    plot_ol(ax_b, "sdp_mix_geo_pois_r", [300, 600, 900], C_ORG, "v", "-", "SDP 默认 Mix-Geo Poisson tot p99")
    plot_ol(ax_b, "direct_mix_geo_pois_r", [300, 600, 900], C_GRN, "D", "-", "DirectPush Mix-Geo Poisson tot p99")
    plot_ol(ax_b, "sdp_mix_geo_pois_r", [300, 600, 900], C_ORG, "v", ":", "SDP 默认 Mix-Geo 64 B 小包 p99", key="small_p99_us", lw=1.6, ms=5.0, mfc="white")
    plot_ol(ax_b, "direct_mix_geo_pois_r", [300, 600, 900], C_GRN, "o", ":", "DirectPush Mix-Geo 64 B 小包 p99", key="small_p99_us", lw=1.8, ms=5.0, mfc="white")

    sat_x, _, _ = stats_of(by_cfg, "direct_mix_geo_pois_r1200", "achieved_mrps")
    sat_y, _, _ = stats_of(by_cfg, "direct_mix_geo_pois_r1200", "tot_p99_us")
    ax_b.annotate(
        f"DirectPush Mix-Geo 1.20 M/s 过载\n({sat_x:.2f} M/s, p99={sat_y:.0f} µs ↑)",
        xy=(0.903, 63.85),
        xytext=(1.12, 68),
        arrowprops=dict(arrowstyle="<-", color=C_GRN, lw=1.2, ls=":"),
        fontsize=7.6,
        color="#14532d",
        bbox=dict(boxstyle="round,pad=0.22", fc="#f0fdf4", ec="#86efac"),
    )
    ax_b.annotate(
        "Mix-Geo 0.90 M/s 64 B 小包 p99:\nDirectPush 30.7 vs SDP 96.5 µs (−68%)",
        xy=(0.895, 96.45),
        xytext=(0.25, 114),
        arrowprops=dict(arrowstyle="->", color=C_ORG, lw=1.2),
        fontsize=7.5,
        fontweight="bold",
        color="#14532d",
        bbox=dict(boxstyle="round,pad=0.22", fc="#f0fdf4", ec="#86efac"),
    )
    ax_b.set_ylim(0, 148)
    ax_b.set_xlabel("实测双机吞吐 (Mreq/s)", fontsize=9.8)
    ax_b.set_ylabel("端到端真实 p99 延迟 (µs，含 n=3 极差)", fontsize=9.8)
    ax_b.set_title("(b) 混合 RPC/KV 开环 Poisson + Mix-Ads B=8 微突发 (W=64；SDP 默认)", fontsize=10.2, fontweight="bold")
    ax_b.legend(loc="upper right", fontsize=7.0, frameon=True, facecolor="white", framealpha=0.94, ncol=1)

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
    echo_64_tp = stats_of(by_cfg, "card_echo_64_w64", "achieved_mrps")[0]
    xc = np.arange(len(cats))
    wc = 0.25
    ax_c.bar(xc - wc, sdp_tp, wc * 0.92, color=C_SDP, alpha=0.88, label="传统 SDP (默认)")
    ax_c.bar(xc, [0 if np.isnan(v) else v for v in rpm_tp], wc * 0.92, color=C_ORG, alpha=0.85, label="SDP 强对照 (rpm[+zc])")
    ax_c.bar(xc + wc, dp_tp, wc * 0.92, color=C_DP, alpha=0.92, label="DirectPush + DPI DMA")
    for i in range(len(cats)):
        ax_c.text(xc[i] - wc, sdp_tp[i] + 0.08, f"{sdp_tp[i]:.2f}", ha="center", va="bottom", fontsize=8.1)
        if not np.isnan(rpm_tp[i]):
            ax_c.text(xc[i], rpm_tp[i] + 0.08, f"{rpm_tp[i]:.2f}", ha="center", va="bottom", fontsize=8.1)
        ratio = dp_tp[i] / sdp_tp[i]
        ax_c.text(
            xc[i] + wc,
            dp_tp[i] + 0.08,
            f"{dp_tp[i]:.2f}\n({ratio:.2f}×)",
            ha="center",
            va="bottom",
            fontsize=8.1,
            fontweight="bold",
            color=C_DP,
        )
    bc_tp = stats_of(by_cfg, "direct_256_bcap8_w64", "achieved_mrps")[0]
    d2_tp = stats_of(by_cfg, "direct_256_dma256_w64", "achieved_mrps")[0]
    ax_c.plot(xc[1] + wc, bc_tp, "D", color="#10b981", ms=6.8, label=f"256 B 局部限8行 bcap8 ({bc_tp:.2f} M/s，非在途信用)")
    ax_c.plot(xc[1] + wc, d2_tp, "^", color="#7c3aed", ms=6.8, label=f"256 B 强制 DPI dma256 ({d2_tp:.2f} M/s)")

    ax_c.text(
        0.02,
        0.95,
        f"1C 卡核上限参考：纯卡反射 64 B = {echo_64_tp:.2f} M/s\n256 B 直推 = {dp_tp[1]*4:.2f} M 行/s (< 13.4 M 慢崖)",
        transform=ax_c.transAxes,
        ha="left",
        va="top",
        fontsize=7.6,
        color="#334155",
        bbox=dict(boxstyle="round,pad=0.22", fc="#f8fafc", ec="#cbd5e1", lw=0.8),
    )
    ax_c.set_xticks(xc)
    ax_c.set_xticklabels(cats, fontsize=9.4)
    ax_c.set_ylabel("真实双机闭环峰值吞吐 (Mreq/s)", fontsize=9.8)
    ax_c.set_ylim(0, max(dp_tp) * 1.38)
    ax_c.set_title("(c) 单尺寸闭环峰值吞吐（1C 卡核桥接上限；含 SDP 强对照）", fontsize=10.2, fontweight="bold")
    ax_c.legend(loc="upper right", fontsize=7.3, frameon=True, facecolor="white", framealpha=0.94)

    # ---- (d) Closed-loop Mix-Ads & Mix-Geo: BOTH 64 B small_p99 AND overall tot_p99 ----
    ws = [1, 8, 16, 32]
    for wl, col_s, col_d, lbl in [
        ("mix_ads", C_SDP, C_DP, "Mix-Ads (90/10)"),
        ("mix_geo", C_ORG, C_GRN, "Mix-Geo (50/50)"),
    ]:
        xs_s = [stats_of(by_cfg, f"sdp_{wl}_w{w}", "achieved_mrps")[0] for w in ws]
        ys_s_sm = [stats_of(by_cfg, f"sdp_{wl}_w{w}", "small_p99_us")[0] for w in ws]
        ys_s_tot = [stats_of(by_cfg, f"sdp_{wl}_w{w}", "tot_p99_us")[0] for w in ws]
        xs_d = [stats_of(by_cfg, f"direct_{wl}_w{w}", "achieved_mrps")[0] for w in ws]
        ys_d_sm = [stats_of(by_cfg, f"direct_{wl}_w{w}", "small_p99_us")[0] for w in ws]
        ys_d_tot = [stats_of(by_cfg, f"direct_{wl}_w{w}", "tot_p99_us")[0] for w in ws]

        ax_d.plot(xs_s, ys_s_sm, "s--", color=col_s, lw=2.0, ms=5.8, label=f"SDP 默认 {lbl} 64 B 小包 p99")
        ax_d.plot(xs_d, ys_d_sm, "o-", color=col_d, lw=2.3, ms=6.2, label=f"DirectPush {lbl} 64 B 小包 p99")
        ax_d.plot(xs_s, ys_s_tot, "s:", color=col_s, lw=1.4, ms=5.0, mfc="white", alpha=0.85, label=f"SDP 默认 {lbl} 总体 tot p99")
        ax_d.plot(xs_d, ys_d_tot, "o:", color=col_d, lw=1.6, ms=5.2, mfc="white", alpha=0.90, label=f"DirectPush {lbl} 总体 tot p99")

    s_geo_x, _, _ = stats_of(by_cfg, "sdp_mix_geo_w32", "achieved_mrps")
    s_geo_sm, _, _ = stats_of(by_cfg, "sdp_mix_geo_w32", "small_p99_us")
    s_geo_tot, _, _ = stats_of(by_cfg, "sdp_mix_geo_w32", "tot_p99_us")
    d_geo_x, _, _ = stats_of(by_cfg, "direct_mix_geo_w32", "achieved_mrps")
    d_geo_sm, _, _ = stats_of(by_cfg, "direct_mix_geo_w32", "small_p99_us")
    d_geo_tot, _, _ = stats_of(by_cfg, "direct_mix_geo_w32", "tot_p99_us")
    d_geo_lg, _, _ = stats_of(by_cfg, "direct_mix_geo_w32", "large_p99_us")
    s_geo_lg, _, _ = stats_of(by_cfg, "sdp_mix_geo_w32", "large_p99_us")

    ax_d.annotate(
        f"Mix-Geo W=32 (吞吐 {d_geo_x/s_geo_x:.2f}×)\n"
        f"• 64 B 小包 p99: {d_geo_sm:.1f} vs {s_geo_sm:.1f} µs (−{(1-d_geo_sm/s_geo_sm)*100:.0f}%)\n"
        f"• 总体 tot p99: {d_geo_tot:.1f} vs {s_geo_tot:.1f} µs\n"
        f"  (大包 {d_geo_lg:.1f} > {s_geo_lg:.1f} µs，因高吞吐填满 W=32)",
        xy=(d_geo_x, d_geo_tot),
        xytext=(1.12, 41.0),
        arrowprops=dict(arrowstyle="->", color=C_GRN, lw=1.3),
        fontsize=7.5,
        color="#14532d",
        bbox=dict(boxstyle="round,pad=0.24", fc="#f0fdf4", ec="#86efac"),
    )
    ax_d.set_ylim(0, 74)
    ax_d.set_xlabel("实测双机混合吞吐 (Mreq/s，W = 1, 8, 16, 32)", fontsize=9.8)
    ax_d.set_ylabel("端到端 p99 延迟 (µs：实线=64 B 小包，虚空点=总体)", fontsize=9.5)
    ax_d.set_title("(d) 混合负载闭环：64 B 小包去队头阻塞 vs 总体 tot p99 (SDP 默认)", fontsize=10.2, fontweight="bold")
    ax_d.legend(loc="upper right", fontsize=6.8, frameon=True, facecolor="white", framealpha=0.94, ncol=2)

    fig.suptitle(
        "MRV-133 真实双机 100GbE 端到端加载曲线（mercury CX5 ⇄ CN106 eth0 RPM0+NIX0 ⇄ saturn，1H+1C，零 DSA，n=3）",
        fontsize=11.6,
        fontweight="bold",
        y=0.99,
    )
    png = mrvplot.save(fig, "mrv-133-lat-tput")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
