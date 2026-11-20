#!/usr/bin/env python3
"""MRV-115 Figure 3: Part 2 Closed-Loop Monotonicity (W = 1..256, WC4 & WC1) with Honest tot p99 & Replicate Outliers."""
import collections
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv115")
RUNS = ROOT / "logs/mrv115-2026-11-20/runs.jsonl"

recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
for r in recs:
    assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0

by_key = collections.defaultdict(list)
for r in recs:
    if r["mode"] == "closed_loop":
        by_key[(r["card_cores"], r["shaping"], r["window"])].append(r)


def med(key, fn):
    return statistics.median(fn(r) for r in by_key[key])


ws = [1, 8, 16, 24, 32, 40, 48, 64, 128, 256]
xw = np.arange(len(ws))
ws_1s = [64, 128, 256]
x_1s = [ws.index(w) for w in ws_1s]

fig, axes = plt.subplots(1, 2, figsize=(14.4, 5.8))

# ---- Panel A: Throughput vs Window W ----
ax = axes[0]
ax.axvspan(3.5, 9.4, color="#fee2e2", alpha=0.30, zorder=0)

y_4u = [med((4, "unshaped", w), lambda r: r["mops"]) for w in ws]
ax.plot(xw, y_4u, "-o", color="#dc2626", lw=2.2, ms=5.6, label="WC4 无整形（W=24→32 跌入 13.1 M/s*）", zorder=4)

y_4s = [med((4, "credit_24", w), lambda r: r["mops"]) for w in ws]
ax.plot(xw, y_4s, "-s", color="#1d4ed8", lw=2.4, ms=5.8, label="WC4 主机信用 W_credit=24（稳守 18.7 M/s，+43%）", zorder=4)

y_1u = [med((1, "unshaped", w), lambda r: r["mops"]) for w in ws]
ax.plot(xw, y_1u, "--D", color="#64748b", lw=1.9, ms=5.0, label="WC1 无整形（W=64 达 50.4，W=128 坍缩至 15.1 M/s）", zorder=3)

y_1s = [med((1, "credit_64", w), lambda r: r["mops"]) for w in ws_1s]
ax.plot(x_1s, y_1s, "-^", color="#059669", lw=2.4, ms=6.6, label="WC1 主机信用 W_credit=64（W=128/256 中位 ~49 M/s）", zorder=5)

# Plot ALL n=3 replicate dots with slight x-offset so WC1 credit_64 rep0 (18.50) is NOT hidden behind WC4 credit_24 (18.70)
for cores, shp, col, mk, wlist, xoff in [
    (4, "unshaped", "#dc2626", "o", ws, -0.10),
    (4, "credit_24", "#1d4ed8", "s", ws, -0.05),
    (1, "unshaped", "#64748b", "D", ws, 0.05),
    (1, "credit_64", "#059669", "^", ws_1s, 0.14),
]:
    for w in wlist:
        i = ws.index(w)
        for r in by_key[(cores, shp, w)]:
            ax.scatter(i + xoff, r["mops"], color=col, marker=mk, s=22, alpha=0.55, zorder=6)

# Explicitly circle & call out WC1 W=64 credit_64 rep 0 (18.50 M/s, run_id=442)
ax.scatter([7.14], [18.50], s=130, facecolors="none", edgecolors="#059669", linewidths=2.0, zorder=8)
ax.annotate(
    "WC1 W=64 credit_64 rep 0\n单轮坍缩至 18.50 M/s！\n（W_credit=64 处启动双稳态边界）",
    xy=(7.14, 18.50),
    xytext=(5.15, 29.0),
    fontsize=7.5,
    color="#065f46",
    fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.26", fc="#ecfdf5", ec="#34d399", lw=1.0),
    arrowprops=dict(arrowstyle="->", color="#059669", lw=1.3),
)

ax.annotate(
    "WC1 W=128/256：\n15.1→48.8 M/s (3.2×)",
    xy=(8.0, 48.82),
    xytext=(3.2, 43.0),
    fontsize=7.6,
    color="#065f46",
    bbox=dict(boxstyle="round,pad=0.25", fc="#ecfdf5", ec="#6ee7b7", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#059669", lw=1.1),
)
ax.annotate(
    "WC4 C=24 全程稳守 18.7 M/s\n（W=32..256 全部 n=3 零坍缩）",
    xy=(5.0, 18.67),
    xytext=(0.5, 26.0),
    fontsize=7.5,
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.25", fc="#eff6ff", ec="#93c5fd", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.1),
)

ax.axhline(13.1, color="#991b1b", ls=":", lw=1.1, alpha=0.8)
ax.text(5.1, 10.4, "WC4 无整形 13.1 M/s 慢态区", fontsize=7.9, color="#991b1b", fontweight="bold")
ax.set_xticks(xw)
ax.set_xticklabels([str(w) for w in ws])
ax.set_xlabel("闭环应用并发窗口 W（在途请求数）")
ax.set_ylabel("闭环吞吐 (M msg/s)")
ax.set_ylim(0, 58)
ax.set_title("A. 闭环吞吐：WC4 (C=24) 稳守 18.7 M/s，WC1 (C=64) 存启动双稳态")
ax.legend(loc="upper left", fontsize=7.0, frameon=True, facecolor="white")

# ---- Panel B: Honest Caller tot p99 (solid) vs Hardware wire p99 (dashed) ----
ax = axes[1]
ax.axhline(2.0, color="#0f766e", ls=":", lw=1.4, label="参考线 p99 = 2.0 µs")
ax.axhspan(0.7, 2.0, color="#ccfbf1", alpha=0.32, zorder=0)

# Primary solid lines = TRUE caller end-to-end tot p99
ax.plot(xw, [med((4, "unshaped", w), lambda r: r["tot_us"]["p99"]) for w in ws], "-o", color="#dc2626", lw=2.2, ms=5.4,
        label="WC4 无整形 端到端 tot p99", zorder=4)
ax.plot(xw, [med((4, "credit_24", w), lambda r: r["tot_us"]["p99"]) for w in ws], "-s", color="#1d4ed8", lw=2.4, ms=5.6,
        label="WC4 W_credit=24 端到端 tot p99（含主机排队）", zorder=5)
ax.plot(xw, [med((4, "credit_24", w), lambda r: r["wire_us"]["p99"]) for w in ws], "--s", color="#60a5fa", lw=1.7, ms=4.4,
        label="WC4 W_credit=24 线端 wire p99（仅硬件）", zorder=3)

ax.plot(xw, [med((1, "unshaped", w), lambda r: r["tot_us"]["p99"]) for w in ws], "--D", color="#64748b", lw=1.8, ms=4.8,
        label="WC1 无整形 端到端 tot p99", zorder=3)
ax.plot(x_1s, [med((1, "credit_64", w), lambda r: r["tot_us"]["p99"]) for w in ws_1s], "-^", color="#059669", lw=2.4, ms=6.2,
        label="WC1 W_credit=64 端到端 tot p99（含主机排队）", zorder=5)
ax.plot(x_1s, [med((1, "credit_64", w), lambda r: r["wire_us"]["p99"]) for w in ws_1s], "--^", color="#34d399", lw=1.7, ms=4.6,
        label="WC1 W_credit=64 线端 wire p99（仅硬件）", zorder=3)

# Plot ALL n=3 replicate dots for tot p99
for cores, shp, col, mk, wlist, xoff in [
    (4, "unshaped", "#dc2626", "o", ws, -0.08),
    (4, "credit_24", "#1d4ed8", "s", ws, 0.0),
    (1, "unshaped", "#64748b", "D", ws, 0.06),
    (1, "credit_64", "#059669", "^", ws_1s, 0.12),
]:
    for w in wlist:
        i = ws.index(w)
        for r in by_key[(cores, shp, w)]:
            ax.scatter(i + xoff, r["tot_us"]["p99"], color=col, marker=mk, s=20, alpha=0.55, zorder=6)

# Explicitly highlight WC1 W=64 credit_64 rep 0 (tot p99 = 5.43 us)
ax.scatter([7.12], [5.43], s=120, facecolors="none", edgecolors="#059669", linewidths=1.9, zorder=8)
ax.annotate(
    "WC1 W=64 C=64 rep 0\ntot p99 = 5.43 µs",
    xy=(7.12, 5.43),
    xytext=(4.1, 8.2),
    fontsize=7.4,
    color="#065f46",
    fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.23", fc="#ecfdf5", ec="#34d399", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#059669", lw=1.2),
)

ax.annotate(
    "WC4 W=256 受利特尔法则约束：\n端到端 tot p99=20.3 vs 20.8 µs (-2.3%)\n（p50 降 32%；仅 wire 锁平 2.12 µs）",
    xy=(9.0, 20.27),
    xytext=(4.95, 25.0),
    fontsize=7.1,
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.23", fc="#eff6ff", ec="#93c5fd", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.1),
)
ax.annotate(
    "WC1 W=256 C=64：\n端到端 tot p99 20.8→6.20 µs (-70%)\n线端 wire p99 锁平 1.51 µs",
    xy=(9.0, 6.20),
    xytext=(3.2, 0.78),
    fontsize=7.2,
    color="#065f46",
    bbox=dict(boxstyle="round,pad=0.24", fc="#ecfdf5", ec="#6ee7b7", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#059669", lw=1.1),
)

ax.set_yscale("log")
ax.set_ylim(0.68, 56)
ax.set_xticks(xw)
ax.set_xticklabels([str(w) for w in ws])
ax.set_xlabel("闭环应用并发窗口 W（在途请求数）")
ax.set_ylabel("闭环往返尾延迟 p99 (µs，对数坐标)")
ax.set_title("B. 闭环尾延迟：端到端 tot p99 受利特尔法则约束，信用仅锁平 wire")
ax.legend(loc="upper left", fontsize=6.8, frameon=True, facecolor="white")

fig.suptitle(
    "MRV-115 Part 2：闭环并发窗口扫描（W = 1 → 256）吞吐与端到端/线端尾延迟\n"
    "固定基线：MOVDIR64B→BAR4 + 卡 WC→DDIO，64 B，cpu40 3.90 GHz，uncore 2.20 GHz，卡 2.00 GHz，MAX_DRAIN=8（中位线 + 全部 n=3 散点，全 0 丢包）",
    fontsize=10.6,
    fontweight="bold",
)
fig.text(
    0.5,
    0.008,
    "*注：本轮 inline.rs 将 MAX_DRAIN 从 32 降至 8，使 WC4 无整形坍缩提前至 W=24→32（MRV-75 MAX_DRAIN=32 为 W=40→48）并令纯 burst_8 在稳态失效。",
    ha="center",
    fontsize=7.8,
    color="#475569",
)
fig.tight_layout(rect=[0, 0.028, 1, 0.94])
png = mrvplot.HERE / "mrv-115-closed-loop-monotonicity.png"
fig.savefig(png)
fig.savefig(mrvplot.HERE / "mrv-115-closed-loop-monotonicity.svg")
plt.close(fig)
print(f"Saved {png}")
