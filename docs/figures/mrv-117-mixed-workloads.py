"""MRV-117 Figure 2: Mixed RPC/KV workloads (Mix-Ads 90/10, Mix-Geo 50/50, Mix-4Size)
Latency-throughput curves across W in {1, 4, 8, 16, 32} + Little's Law shared-window
occupancy breakdown on Mix-Geo from runs.jsonl.
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

fig, axes = plt.subplots(2, 3, figsize=(16.2, 9.2))

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
                tx, ty = 6.35, 7.8
                txt = f"W=32\n{xs_mreq[-1]:.2f} M/s\n{ys_sm99[-1]:.2f} µs"
            elif wl == "mix_geo":
                tx, ty = 3.05, 5.1
                txt = f"W=32: {xs_mreq[-1]:.2f} M/s\n小包 p99={ys_sm99[-1]:.2f} µs\n(闭环共享窗 L小≈2.76)"
            else:
                tx, ty = xs_mreq[-1] * 0.70, ys_sm99[-1] + 2.5
                txt = f"W=32\n{xs_mreq[-1]:.2f} M/s\n{ys_sm99[-1]:.2f} µs"
            ax_top.annotate(txt,
                            xy=(xs_mreq[-1], ys_sm99[-1]),
                            xytext=(tx, ty),
                            fontsize=8.1, fontweight="bold", color=color,
                            arrowprops=dict(arrowstyle="->", color=color, lw=1.2),
                            bbox=dict(boxstyle="round,pad=0.22", fc="#EEF4FF", ec=color, alpha=0.96))
        elif tier == "push" and wl == "mix_geo":
            ax_top.annotate(f"纯直推 HoL+慢态塌方\nW=16: {xs_mreq[3]:.2f}M/s, {ys_sm99[3]:.1f}µs\nW=32: {xs_mreq[4]:.2f}M/s, {ys_sm99[4]:.1f}µs",
                            xy=(xs_mreq[3], ys_sm99[3]),
                            xytext=(0.12, 16.5),
                            fontsize=8.0, fontweight="bold", color="#B45309",
                            arrowprops=dict(arrowstyle="->", color=color, lw=1.2),
                            bbox=dict(boxstyle="round,pad=0.22", fc="#FFFBEB", ec=color, alpha=0.96))

    # Inset on Mix-Geo top panel: Little's Law shared-window occupancy (W=16 -> W=32)
    if wl == "mix_geo":
        l_stats = {}
        for w in (16, 32):
            rs = by_cfg[f"directpush_mix_geo_w{w}"]
            l_sm = statistics.median([r["small_cnt"] / r["secs"] / 1e6 * r["lat_small_us"]["mean"] for r in rs])
            l_lg = statistics.median([r["large_cnt"] / r["secs"] / 1e6 * r["lat_large_us"]["mean"] for r in rs])
            r_sm = statistics.median([r["small_cnt"] / r["secs"] / 1e6 for r in rs])
            l_stats[w] = (l_sm, l_lg, l_sm + l_lg, l_lg / (l_sm + l_lg) * 100.0, r_sm)

        ax_ins = ax_top.inset_axes([0.62, 0.56, 0.36, 0.36])
        ax_ins.set_facecolor("white")
        ys = [0, 1]
        l_sms = [l_stats[16][0], l_stats[32][0]]
        l_lgs = [l_stats[16][1], l_stats[32][1]]
        ax_ins.barh(ys, l_sms, height=0.46, color="#60A5FA", label="小包 L_small")
        ax_ins.barh(ys, l_lgs, left=l_sms, height=0.46, color="#1D4ED8", label="4KiB DMA")
        for idx, w in enumerate((16, 32)):
            l_sm, l_lg, l_tot, pct_lg, r_sm = l_stats[w]
            bar_lbl = f"{pct_lg:.1f}%" if w == 16 else f"{l_lg:.1f} ({pct_lg:.1f}%)"
            ax_ins.text(l_sm + l_lg / 2, idx, bar_lbl,
                        ha="center", va="center", fontsize=6.8, fontweight="bold", color="white")
            ax_ins.text(l_tot + 0.7, idx, f"L小={l_sm:.2f}",
                        ha="left", va="center", fontsize=6.8, fontweight="bold", color="#1E3A8A")
        ax_ins.set_yticks(ys)
        ax_ins.set_yticklabels(["W=16", "W=32"], fontsize=7.4, fontweight="bold")
        ax_ins.invert_yaxis()
        ax_ins.set_xlim(0, 44.5)
        ax_ins.set_ylim(1.55, -0.75)
        ax_ins.set_xlabel("利特尔法则在途槽位 (L=λ·τ)", fontsize=6.9)
        ax_ins.set_title("共享窗准入分解 (DP Mix-Geo)", fontsize=7.3, fontweight="bold", color="#1E3A8A", pad=3)
        ax_ins.tick_params(axis="x", labelsize=6.8)
        ax_ins.grid(axis="x", alpha=0.25)
        ax_ins.grid(axis="y", visible=False)
        ax_ins.legend(loc="upper right", fontsize=6.2, frameon=True, framealpha=0.95, handlelength=1.0)
    ax_top.set_title(title, fontsize=11, fontweight="bold", pad=8)
    ax_top.set_xlabel("吞吐 (Mreq/s，W = 1→4→8→16→32)", fontsize=9.8)
    if col == 0:
        ax_top.set_ylabel("小包 (≤256 B) 端到端 p99 延迟 (µs)", fontsize=10.5)
    if wl == "mix_geo":
        ax_top.set_xlim(0, 4.95)
    ax_top.set_ylim(0, 28.5 if wl == "mix_geo" else 13.0)
    if col == 0:
        ax_top.legend(loc="upper right", fontsize=8.4, frameon=True, framealpha=0.95)

    ax_bot.set_xlabel("双向有效载荷吞吐 (GB/s，W = 1→4→8→16→32)", fontsize=9.8)
    if col == 0:
        ax_bot.set_ylabel("全局端到端 p99 (实/虚) & p50 (点线) (µs)", fontsize=10.2)
    ax_bot.set_ylim(0, 28.0 if wl == "mix_geo" else (18.5 if wl == "mix_4size" else 11.5))
    if col == 1:
        w16_gb = statistics.median([r["bidir_gbps"] for r in by_cfg["directpush_mix_geo_w16"]])
        w16_p50 = statistics.median([r["lat_all_us"]["p50"] for r in by_cfg["directpush_mix_geo_w16"]])
        w16_p99 = statistics.median([r["lat_all_us"]["p99"] for r in by_cfg["directpush_mix_geo_w16"]])
        w32_lg99 = statistics.median([r["lat_large_us"]["p99"] for r in by_cfg["directpush_mix_geo_w32"]])
        ax_bot.annotate(
            f"DirectPush W=16:\n{w16_gb:.2f} GB/s 双向\np50={w16_p50:.2f} / p99={w16_p99:.2f} µs\n(W=32 4K p99→{w32_lg99:.2f} µs)",
            xy=(w16_gb, w16_p99), xytext=(9.8, 17.6),
            fontsize=8.0, fontweight="bold", color="#0F62FE",
            arrowprops=dict(arrowstyle="->", color="#0F62FE", lw=1.2),
            bbox=dict(boxstyle="round,pad=0.22", fc="#EEF4FF", ec="#0F62FE", alpha=0.96),
        )
    if col == 0:
        ax_bot.legend(loc="upper right", fontsize=7.6, ncol=1, frameon=True, framealpha=0.95)

fig.suptitle(
    "MRV-117 混合尺寸 RPC/KV 负载三档阶梯消融（1 主机核 cpu40 + 1 卡核 · 128 MiB 留存池 · 闭环共享窗 W · 零 DSA · n=3）",
    fontsize=11.8, fontweight="bold"
)
out = mrvplot.save(fig, "mrv-117-mixed-workloads")
print("Saved:", out)
