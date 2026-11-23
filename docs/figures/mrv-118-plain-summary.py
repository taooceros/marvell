#!/usr/bin/env python3
"""MRV-118 plain-Chinese 3-panel summary chart plotted directly from verified runs.jsonl."""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import statistics

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

REPO = Path(__file__).resolve().parent.parent.parent
RUNS = REPO / "logs/mrv118-2026-11-20/runs.jsonl"


def f2(val: float) -> str:
    return str(Decimal(f"{val:.6f}").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def load_data():
    by = defaultdict(list)
    for line in RUNS.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        assert r["verified"], f"unverified run: {r.get('cfg')}"
        assert r.get("lost", 0) == 0 and r.get("bad", 0) == 0 and r.get("torn", 0) == 0
        by[r["cfg"]].append(r)

    def med(cfg, fn):
        return statistics.median(fn(r) for r in by[cfg])

    def vals(cfg, fn):
        return [fn(r) for r in by[cfg]]

    return by, med, vals


def main():
    by, med, vals = load_data()
    idle_ups = med("app_work_idle_cal", lambda r: r["units_per_s"])

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(15.4, 5.2))
    fig.suptitle(
        "MRV-118：经过 CN106 真实网卡硬件回环后，DirectPush 的延迟、抗压与省核收益均完整保留\n"
        "固定条件：1 颗主机核 (3.9 GHz) + 1 颗网卡核 (2.0 GHz)，纯商用网卡（零 Intel DSA），每档 n = 3 轮零错中位数",
        fontsize=11.5,
        fontweight="bold",
        y=0.99,
    )

    c_sdp = "#64748b"
    c_sdp_light = "#cbd5e1"
    c_rpm = "#d97706"
    c_dp = "#0f766e"
    c_dp_light = "#99f6e4"

    # ------------------------------------------------------------------
    # Panel (a): W=1 round-trip latency stacked breakdown
    # ------------------------------------------------------------------
    sizes = ["64", "256", "4096"]
    size_labels = ["64 B", "256 B", "4 KiB"]
    x = np.arange(len(sizes))
    w = 0.34

    sdp_iface = [med(f"sdp_nonix_{s}_w1", lambda r: r["lat_all_us"]["p50"]) for s in sizes]
    sdp_tot = [med(f"sdp_nix_{s}_w1", lambda r: r["lat_all_us"]["p50"]) for s in sizes]
    sdp_nix = [t - i for t, i in zip(sdp_tot, sdp_iface)]

    dp_iface = [med(f"directpush_nonix_{s}_w1", lambda r: r["lat_all_us"]["p50"]) for s in sizes]
    dp_tot = [med(f"directpush_nix_{s}_w1", lambda r: r["lat_all_us"]["p50"]) for s in sizes]
    dp_nix = [t - i for t, i in zip(dp_tot, dp_iface)]

    ax1.bar(x - w / 2 - 0.02, sdp_iface, w, color=c_sdp, edgecolor="#334155", linewidth=0.8, label="传统 SDP：主机⇄网卡接口")
    ax1.bar(x - w / 2 - 0.02, sdp_nix, w, bottom=sdp_iface, color=c_sdp_light, edgecolor="#475569", linewidth=0.8, hatch="///", label="网卡硬件回环增量（固定）")
    ax1.bar(x + w / 2 + 0.02, dp_iface, w, color=c_dp, edgecolor="#115e59", linewidth=0.8, label="DirectPush：主机⇄网卡接口")
    ax1.bar(x + w / 2 + 0.02, dp_nix, w, bottom=dp_iface, color=c_dp_light, edgecolor="#0f766e", linewidth=0.8, hatch="///")

    rpm_64 = med("sdp_rpm_nix_64_w1", lambda r: r["lat_all_us"]["p50"])
    rpm_4k = med("sdp_rpm_nix_4096_w1", lambda r: r["lat_all_us"]["p50"])
    ax1.scatter(
        [0 - w / 2 - 0.02, 2 - w / 2 - 0.02],
        [rpm_64, rpm_4k],
        marker="D",
        s=50,
        color=c_rpm,
        edgecolor="#78350f",
        zorder=6,
        label="优化对照 SDP（内存轮询）",
    )

    for i in range(len(sizes)):
        xs = x[i] - w / 2 - 0.02
        xd = x[i] + w / 2 + 0.02
        ax1.text(xs, sdp_tot[i] + 0.18, f"{f2(sdp_tot[i])}", ha="center", va="bottom", fontsize=9, fontweight="bold", color="#334155")
        ax1.text(xd, dp_tot[i] + 0.18, f"{f2(dp_tot[i])}", ha="center", va="bottom", fontsize=9, fontweight="bold", color=c_dp)
        ax1.text(xs, sdp_iface[i] / 2, f"{f2(sdp_iface[i])}", ha="center", va="center", fontsize=8, color="white", fontweight="bold")
        ax1.text(xs, sdp_iface[i] + sdp_nix[i] / 2, f"+{f2(sdp_nix[i])}", ha="center", va="center", fontsize=8, color="#1e293b", fontweight="bold")
        ax1.text(xd, dp_iface[i] / 2, f"{f2(dp_iface[i])}", ha="center", va="center", fontsize=8, color="white", fontweight="bold")
        ax1.text(xd, dp_iface[i] + dp_nix[i] / 2, f"+{f2(dp_nix[i])}", ha="center", va="center", fontsize=8, color="#115e59", fontweight="bold")

        diff = dp_tot[i] - sdp_tot[i]
        pct = diff / sdp_tot[i] * 100.0
        box_y = max(sdp_tot[i], dp_tot[i]) + 1.75
        ax1.annotate(
            f"{diff:.2f} µs ({pct:.1f}%)",
            xy=(xd, dp_tot[i] + 0.65),
            xytext=(x[i] + 0.06, box_y),
            ha="center",
            fontsize=8.3,
            fontweight="bold",
            color=c_dp,
            bbox=dict(boxstyle="round,pad=0.22", fc="#f0fdfa", ec=c_dp, lw=0.9),
            arrowprops=dict(arrowstyle="->", color=c_dp, lw=1.0),
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels(size_labels, fontsize=9.5, fontweight="bold")
    ax1.set_ylabel("单包往返中位延迟 p50 (µs，越低越好)", fontsize=9.5)
    ax1.set_ylim(0, 14.8)
    ax1.set_title("(a) 单包延迟（W = 1）：过真网卡后优势保留", fontsize=10.2, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=7.6, framealpha=0.95)

    # ------------------------------------------------------------------
    # Panel (b): 64 B open-loop Poisson load curve
    # ------------------------------------------------------------------
    sdp_rates = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500]
    dp_rates = [500, 1000, 1500, 2000, 2500, 3000, 4000, 5000, 6000]

    sx = [med(f"sdp_nix_64_poisson_r{r}", lambda d: d["mreq_s"]) for r in sdp_rates]
    sp50 = [med(f"sdp_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p50"]) for r in sdp_rates]
    sp99 = [med(f"sdp_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in sdp_rates]
    sp99_lo = [sp99[i] - min(vals(f"sdp_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) for i, r in enumerate(sdp_rates)]
    sp99_hi = [max(vals(f"sdp_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) - sp99[i] for i, r in enumerate(sdp_rates)]

    dx = [med(f"directpush_nix_64_poisson_r{r}", lambda d: d["mreq_s"]) for r in dp_rates]
    dp50 = [med(f"directpush_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p50"]) for r in dp_rates]
    dp99 = [med(f"directpush_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in dp_rates]
    dp99_lo = [dp99[i] - min(vals(f"directpush_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) for i, r in enumerate(dp_rates)]
    dp99_hi = [max(vals(f"directpush_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) - dp99[i] for i, r in enumerate(dp_rates)]

    ax2.plot(sx, sp50, "-o", color=c_sdp, lw=1.9, ms=4.5, label="传统 SDP 中位 p50")
    ax2.errorbar(sx, sp99, yerr=[sp99_lo, sp99_hi], fmt="--s", color=c_sdp, lw=1.6, ms=4.5, capsize=3, label="传统 SDP 尾延迟 p99")
    ax2.plot(dx, dp50, "-o", color=c_dp, lw=2.1, ms=4.8, label="DirectPush 中位 p50")
    ax2.errorbar(dx, dp99, yerr=[dp99_lo, dp99_hi], fmt="--s", color=c_dp, lw=1.8, ms=4.8, capsize=3, label="DirectPush 尾延迟 p99")

    # 1C peak reference line
    dp_peak = med("directpush_nix_64_w64", lambda d: d["mreq_s"])
    ax2.axvline(dp_peak, color="#64748b", ls=":", lw=1.2)
    ax2.text(
        dp_peak - 0.08,
        22.0,
        f"1 卡核上限\n{dp_peak:.2f} M/s",
        ha="right",
        va="center",
        fontsize=7.8,
        color="#475569",
        bbox=dict(boxstyle="round,pad=0.18", fc="#ffffff", ec="none", alpha=0.88),
    )

    # SDP 5.0M saturation extension (actual p99=325 us, off-chart)
    sdp_5m_p99 = med("sdp_nix_64_poisson_r5000", lambda d: d["lat_all_us"]["p99"])
    sdp_5m_p50 = med("sdp_nix_64_poisson_r5000", lambda d: d["lat_all_us"]["p50"])
    ax2.annotate(
        "",
        xy=(4.92, 72.5),
        xytext=(sx[-1], sp99[-1]),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.5, ls="--"),
    )
    ax2.annotate(
        f"传统 SDP @ 5.0 M/s 饱和\np50 = {sdp_5m_p50:.0f} / p99 = {sdp_5m_p99:.0f} µs ↑",
        xy=(4.74, 56.0),
        xytext=(0.45, 47.0),
        fontsize=8.2,
        fontweight="bold",
        color="#b91c1c",
        bbox=dict(boxstyle="round,pad=0.25", fc="#fef2f2", ec="#dc2626", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.0),
    )

    dp_5m_p99 = med("directpush_nix_64_poisson_r5000", lambda d: d["lat_all_us"]["p99"])
    ax2.annotate(
        f"DirectPush @ 5.0 M/s\np99 仅 {dp_5m_p99:.1f} µs",
        xy=(5.0, dp_5m_p99),
        xytext=(1.50, 24.5),
        fontsize=8.3,
        fontweight="bold",
        color=c_dp,
        bbox=dict(boxstyle="round,pad=0.25", fc="#f0fdfa", ec=c_dp, lw=0.9),
        arrowprops=dict(arrowstyle="->", color=c_dp, lw=1.1),
    )

    ax2.set_xlabel("实际吞吐（百万请求/秒, Mreq/s）", fontsize=9.5)
    ax2.set_ylabel("端到端延迟（µs，含发包排队）", fontsize=9.5)
    ax2.set_xlim(0.2, 6.95)
    ax2.set_ylim(0, 74)
    ax2.set_title("(b) 64 B 随机到达（泊松）：5 M/s 抗压对比", fontsize=10.2, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=7.6, framealpha=0.95)

    # ------------------------------------------------------------------
    # Panel (c): Matched-throughput Host CPU usage (lower is better)
    # ------------------------------------------------------------------
    def cpu_busy(cfg):
        return (1.0 - med(cfg, lambda r: r["app_work_per_s"]) / idle_ups) * 100.0

    groups = [
        ("64 B\n1.5 M/s", "sdp_nix_64_r1500", "sdp_rpm_nix_64_r1500", "directpush_nix_64_r1500"),
        ("Mix-Ads\n1.5 M/s", "sdp_nix_mix_ads_r1500", "sdp_rpm_nix_mix_ads_r1500", "directpush_nix_mix_ads_r1500"),
        ("4 KiB\n0.8 M/s", "sdp_nix_4096_r800", None, "directpush_nix_4096_r800"),
        ("Mix-Geo\n0.8 M/s", "sdp_nix_mix_geo_r800", None, "directpush_nix_mix_geo_r800"),
    ]
    gx = np.arange(len(groups))
    bw = 0.25

    sdp_b = [cpu_busy(g[1]) for g in groups]
    dp_b = [cpu_busy(g[3]) for g in groups]

    ax3.axhline(62.5, color="#dc2626", ls="--", lw=1.2, alpha=0.85, label="默认 SDP 空等寄存器底噪 ≈62.5%")

    ax3.bar(gx - bw, sdp_b, bw, color=c_sdp, edgecolor="#334155", linewidth=0.8, label="传统 SDP（默认）")
    # Plot only the valid measured strong-baseline SDP bars
    rpm_xs, rpm_ys = [], []
    for i, g in enumerate(groups):
        if g[2] is not None:
            rpm_xs.append(gx[i])
            rpm_ys.append(cpu_busy(g[2]))
    ax3.bar(rpm_xs, rpm_ys, bw, color=c_rpm, edgecolor="#92400e", linewidth=0.8, label="优化对照 SDP（内存轮询）")
    ax3.bar(gx + bw, dp_b, bw, color=c_dp, edgecolor="#115e59", linewidth=0.8, label="DirectPush + 自带 DMA")

    for i, g in enumerate(groups):
        ax3.text(gx[i] - bw, sdp_b[i] + 1.5, f"{sdp_b[i]:.1f}%", ha="center", va="bottom", fontsize=8, fontweight="bold", color="#334155")
        ax3.text(gx[i] + bw, dp_b[i] + 1.5, f"{dp_b[i]:.1f}%", ha="center", va="bottom", fontsize=8.3, fontweight="bold", color=c_dp)
        if g[2] is not None:
            rb = cpu_busy(g[2])
            ax3.text(gx[i], rb + 1.5, f"{rb:.1f}%", ha="center", va="bottom", fontsize=8, fontweight="bold", color="#92400e")
            ratio = rb / dp_b[i]
            ax3.annotate(
                f"比优化\n再省 {ratio:.2f}×",
                xy=(gx[i] + bw, dp_b[i] + 5.5),
                xytext=(gx[i] + 0.24, rb + 13.5),
                ha="center",
                va="bottom",
                fontsize=7.5,
                fontweight="bold",
                color=c_dp,
                bbox=dict(boxstyle="round,pad=0.18", fc="#f0fdfa", ec=c_dp, lw=0.8),
                arrowprops=dict(arrowstyle="->", color=c_dp, lw=0.9),
            )
        else:
            ratio_def = sdp_b[i] / dp_b[i]
            ax3.text(
                gx[i] + 0.20,
                dp_b[i] + 13.0,
                f"比默认省\n{ratio_def:.1f}×\n(缺优化对照)",
                ha="center",
                va="bottom",
                fontsize=7.0,
                color="#475569",
                bbox=dict(boxstyle="round,pad=0.18", fc="#f8fafc", ec="#94a3b8", lw=0.8),
            )

    ax3.set_xticks(gx)
    ax3.set_xticklabels([g[0] for g in groups], fontsize=9, fontweight="bold")
    ax3.set_ylabel("同吞吐下主机核 I/O 占用率（%，越低越好）", fontsize=9.5)
    ax3.set_xlim(-0.55, 3.68)
    ax3.set_ylim(0, 128)
    ax3.set_title("(c) 同吞吐主机 CPU 占用：过真网卡收益不变", fontsize=10.2, fontweight="bold")
    ax3.legend(loc="upper right", fontsize=7.3, framealpha=0.95)

    out = save(fig, "mrv-118-plain-summary")
    print("Saved", out)


if __name__ == "__main__":
    main()
