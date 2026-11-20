"""MRV-117 Figure 2: Mixed RPC/KV workloads (Mix-Ads 90/10, Mix-Geo 50/50, Mix-4Size)
Latency-throughput curves across W in {1, 4, 8, 16, 32} from runs.jsonl.
"""
import collections
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import mrvplot

RUNS = Path(__file__).resolve().parents[2] / "logs/mrv117-2026-11-20/runs.jsonl"
rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
by_cfg = collections.defaultdict(list)
for r in rows:
    if r["part"] == "part2_mixed":
        assert r["verified"], f"unverified: {r['cfg']}"
        by_cfg[r["cfg"]].append(r)

WS = [1, 4, 8, 16, 32]
WORKLOADS = [
    ("mix_ads", "Mix-Ads (90% 64B + 10% 4KiB · 均 467B)"),
    ("mix_geo", "Mix-Geo (50% 64B + 50% 4KiB · 均 2080B)"),
    ("mix_4size", "Mix-4Size (60% 64B + 20% 256B + 15% 4K + 5% 8K)"),
]
TIERS = [
    ("sdp", "(a) 传统 SDP (MTU 9000)", "#6B7280", "s", "-"),
    ("push", "(b) 纯 MOVDIR64B + WC", "#D97706", "^", "--"),
    ("directpush", "(c) DirectPush + 原生 DPI DMA", "#0F62FE", "o", "-"),
]

fig, axes = plt.subplots(2, 3, figsize=(16.0, 9.1))

for col, (wl, title) in enumerate(WORKLOADS):
    ax_top = axes[0, col]
    ax_bot = axes[1, col]

    for tier, label, color, marker, ls in TIERS:
        xs_mreq, ys_sm99 = [], []
        xs_gbps, ys_all50, ys_all99 = [], [], []
        for w in WS:
            rs = by_cfg[f"{tier}_{wl}_w{w}"]
            mreqs = [r["mreq_s"] for r in rs]
            gbps = [r["bidir_gbps"] for r in rs]
            sm99s = [r["lat_small_us"]["p99"] for r in rs]
            all50s = [r["lat_all_us"]["p50"] for r in rs]
            all99s = [r["lat_all_us"]["p99"] for r in rs]

            ax_top.scatter(mreqs, sm99s, color=color, alpha=0.45, s=22, zorder=4)
            ax_bot.scatter(gbps, all99s, color=color, alpha=0.40, s=20, zorder=4)

            xs_mreq.append(statistics.median(mreqs))
            ys_sm99.append(statistics.median(sm99s))
            xs_gbps.append(statistics.median(gbps))
            ys_all50.append(statistics.median(all50s))
            ys_all99.append(statistics.median(all99s))

        ax_top.plot(xs_mreq, ys_sm99, color=color, marker=marker, lw=2.3, ms=6.5, ls=ls, label=label, zorder=5)
        ax_bot.plot(xs_gbps, ys_all99, color=color, marker=marker, lw=2.2, ms=6.0, ls=ls,
                    label=f"{label} p99" if col == 0 else None, zorder=5)
        ax_bot.plot(xs_gbps, ys_all50, color=color, marker=marker, lw=1.4, ms=4.2, ls=":", alpha=0.8,
                    label=f"{label} p50" if col == 0 else None, zorder=4)

        if tier == "directpush":
            if wl == "mix_4size":
                tx, ty = 6.45, 7.8
            elif wl == "mix_geo":
                tx, ty = 3.25, 4.9
            else:
                tx, ty = xs_mreq[-1] * 0.72, ys_sm99[-1] + 2.5
            ax_top.annotate(f"W=32\n{xs_mreq[-1]:.2f} M/s\n{ys_sm99[-1]:.2f} µs",
                            xy=(xs_mreq[-1], ys_sm99[-1]),
                            xytext=(tx, ty),
                            fontsize=8.3, fontweight="bold", color=color,
                            arrowprops=dict(arrowstyle="->", color=color, lw=1.2),
                            bbox=dict(boxstyle="round,pad=0.22", fc="#EEF4FF", ec=color, alpha=0.96))
        elif tier == "push" and wl == "mix_geo":
            ax_top.annotate(f"纯直推 HoL+慢态塌方\nW=16: {xs_mreq[3]:.2f}M/s, {ys_sm99[3]:.1f}µs\nW=32: {xs_mreq[4]:.2f}M/s, {ys_sm99[4]:.1f}µs",
                            xy=(xs_mreq[3], ys_sm99[3]),
                            xytext=(0.18, 16.5),
                            fontsize=8.2, fontweight="bold", color="#B45309",
                            arrowprops=dict(arrowstyle="->", color=color, lw=1.2),
                            bbox=dict(boxstyle="round,pad=0.22", fc="#FFFBEB", ec=color, alpha=0.96))

    ax_top.set_title(title, fontsize=11, fontweight="bold", pad=8)
    ax_top.set_xlabel("吞吐 (Mreq/s，W = 1→4→8→16→32)", fontsize=9.8)
    if col == 0:
        ax_top.set_ylabel("小包 (≤256 B) 端到端 p99 延迟 (µs)", fontsize=10.5)
    ax_top.set_ylim(0, 27.5 if wl == "mix_geo" else 13.0)
    if col == 0:
        ax_top.legend(loc="upper right", fontsize=8.4, frameon=True, framealpha=0.95)

    ax_bot.set_xlabel("双向有效载荷吞吐 (GB/s，W = 1→4→8→16→32)", fontsize=9.8)
    if col == 0:
        ax_bot.set_ylabel("全局端到端 p99 (实/虚) & p50 (点线) (µs)", fontsize=10.2)
    ax_bot.set_ylim(0, 28.0 if wl == "mix_geo" else (18.5 if wl == "mix_4size" else 11.5))
    if col == 1:
        ax_bot.annotate("DirectPush W=16:\n18.68 GB/s 双向\np50=3.75 / p99=6.92 µs",
                        xy=(18.68, 6.92), xytext=(11.8, 18.8),
                        fontsize=8.3, fontweight="bold", color="#0F62FE",
                        arrowprops=dict(arrowstyle="->", color="#0F62FE", lw=1.2),
                        bbox=dict(boxstyle="round,pad=0.22", fc="#EEF4FF", ec="#0F62FE", alpha=0.96))
    if col == 0:
        ax_bot.legend(loc="upper right", fontsize=7.6, ncol=1, frameon=True, framealpha=0.95)

fig.suptitle(
    "MRV-117 混合尺寸 RPC/KV 负载三档阶梯消融（1 主机核 cpu40 + 1 卡核 · 128 MiB 留存池 · MAX_DRAIN=32 · 零 DSA · n=3 交错全点）",
    fontsize=12.0, fontweight="bold"
)
out = mrvplot.save(fig, "mrv-117-mixed-workloads")
print("Saved:", out)
