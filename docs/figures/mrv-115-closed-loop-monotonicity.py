#!/usr/bin/env python3
"""MRV-115 Figure 3: Part 2 Closed-Loop Monotonicity (W = 1..256, WC4 & WC1 Unshaped vs Host Credit Cap)."""
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

fig, axes = plt.subplots(1, 2, figsize=(14.0, 5.4))

# ---- Panel A: Throughput vs Window W ----
ax = axes[0]
ax.axvspan(3.5, 9.4, color="#fee2e2", alpha=0.35, zorder=0)

# WC4 unshaped
y_4u = [med((4, "unshaped", w), lambda r: r["mops"]) for w in ws]
ax.plot(xw, y_4u, "-o", color="#dc2626", lw=2.3, ms=5.8, label="WC4 无整形（W=24→32 跌入 13.1 M/s）")

# WC4 credit_24
y_4s = [med((4, "credit_24", w), lambda r: r["mops"]) for w in ws]
ax.plot(xw, y_4s, "-s", color="#1d4ed8", lw=2.4, ms=5.8, label="WC4 主机信用 W_credit=24（零卡改动）")

# WC1 unshaped
y_1u = [med((1, "unshaped", w), lambda r: r["mops"]) for w in ws]
ax.plot(xw, y_1u, "--D", color="#64748b", lw=1.9, ms=5.2, label="WC1 无整形（W=64 达 50.4 M/s，W=128 坍缩至 15.1 M/s）")

# WC1 credit_64 (W = 64, 128, 256; matches W=1..64 below 64)
ws_1s = [64, 128, 256]
x_1s = [ws.index(w) for w in ws_1s]
y_1s = [med((1, "credit_64", w), lambda r: r["mops"]) for w in ws_1s]
ax.plot(x_1s, y_1s, "-^", color="#059669", lw=2.4, ms=6.8, label="WC1 主机信用 W_credit=64（W=128/256 保持 ~49 M/s）")

# Individual rep dots
for cores, shp, col, wlist in [
    (4, "unshaped", "#dc2626", ws),
    (4, "credit_24", "#1d4ed8", ws),
    (1, "unshaped", "#64748b", ws),
    (1, "credit_64", "#059669", ws_1s),
]:
    for w in wlist:
        i = ws.index(w)
        for r in by_key[(cores, shp, w)]:
            ax.scatter(i, r["mops"], color=col, s=13, alpha=0.35, zorder=3)

ax.annotate(
    "WC4 信用=24 消除悬崖\n全程稳守 18.7 M/s (+43%)",
    xy=(7, y_4s[7]),
    xytext=(4.2, 23.0),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.2),
    fontsize=8.0,
    fontweight="bold",
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.22", fc="#eff6ff", ec="#93c5fd"),
)
ax.annotate(
    "WC1 信用=64 消除 W=128 悬崖\n15.1 → 48.8 M/s (3.2×)",
    xy=(8, y_1s[1]),
    xytext=(4.0, 42.5),
    arrowprops=dict(arrowstyle="->", color="#059669", lw=1.2),
    fontsize=8.0,
    fontweight="bold",
    color="#065f46",
    bbox=dict(boxstyle="round,pad=0.22", fc="#ecfdf5", ec="#6ee7b7"),
)

ax.axhline(13.1, color="#991b1b", ls=":", lw=1.1, alpha=0.8)
ax.text(5.2, 10.6, "WC4 无整形 13.1 M/s 慢态坍缩区", fontsize=8.0, color="#991b1b", fontweight="bold")
ax.set_xticks(xw)
ax.set_xticklabels([str(w) for w in ws])
ax.set_xlabel("闭环应用并发窗口 W（在途请求数）")
ax.set_ylabel("闭环吞吐 (M msg/s)")
ax.set_ylim(0, 58)
ax.set_title("A. 闭环吞吐单调性：主机信用上限彻底消除 WC4 与 WC1 坍缩悬崖")
ax.legend(loc="upper left", fontsize=7.4, frameon=True, facecolor="white")

# ---- Panel B: Latency (tot p99 & wire p99) vs Window W ----
ax = axes[1]
ax.axhline(2.0, color="#0f766e", ls=":", lw=1.4, label="目标线 p99 = 2.0 µs")
ax.axhspan(0.7, 2.0, color="#ccfbf1", alpha=0.35, zorder=0)

ax.plot(xw, [med((4, "unshaped", w), lambda r: r["tot_us"]["p99"]) for w in ws], "-o", color="#dc2626", lw=2.3, ms=5.6, label="WC4 无整形 p99")
ax.plot(xw, [med((4, "credit_24", w), lambda r: r["wire_us"]["p99"]) for w in ws], "-s", color="#1d4ed8", lw=2.4, ms=5.8, label="WC4 W_credit=24 线端 wire p99")
ax.plot(xw, [med((4, "credit_24", w), lambda r: r["tot_us"]["p99"]) for w in ws], "--s", color="#1d4ed8", lw=1.4, ms=4.0, alpha=0.72, label="WC4 W_credit=24 端到端 p99 (含主机队列)")
ax.plot(xw, [med((1, "unshaped", w), lambda r: r["tot_us"]["p99"]) for w in ws], "--D", color="#64748b", lw=1.8, ms=5.0, label="WC1 无整形 p99")
ax.plot(x_1s, [med((1, "credit_64", w), lambda r: r["wire_us"]["p99"]) for w in ws_1s], "-^", color="#059669", lw=2.4, ms=6.6, label="WC1 W_credit=64 线端 wire p99")
ax.plot(x_1s, [med((1, "credit_64", w), lambda r: r["tot_us"]["p99"]) for w in ws_1s], "--^", color="#059669", lw=1.4, ms=4.5, alpha=0.72, label="WC1 W_credit=64 端到端 p99 (含主机队列)")

ax.annotate(
    "WC4 wire p99 锁平\n2.12–2.13 µs (W=32..256)",
    xy=(8, med((4, "credit_24", 128), lambda r: r["wire_us"]["p99"])),
    xytext=(4.3, 2.55),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.2),
    fontsize=7.8,
    fontweight="bold",
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.2", fc="#eff6ff", ec="#93c5fd"),
)
ax.annotate(
    "WC1 wire p99 锁平 1.51 µs\n总 p99 降 3.4× (20.8→6.2 µs)",
    xy=(9, med((1, "credit_64", 256), lambda r: r["wire_us"]["p99"])),
    xytext=(4.5, 0.85),
    arrowprops=dict(arrowstyle="->", color="#059669", lw=1.2),
    fontsize=7.8,
    fontweight="bold",
    color="#065f46",
    bbox=dict(boxstyle="round,pad=0.2", fc="#ecfdf5", ec="#6ee7b7"),
)

ax.set_yscale("log")
ax.set_ylim(0.7, 36)
ax.set_xticks(xw)
ax.set_xticklabels([str(w) for w in ws])
ax.set_xlabel("闭环应用并发窗口 W（在途请求数）")
ax.set_ylabel("闭环往返尾延迟 p99 (µs，对数坐标)")
ax.set_title("B. 闭环尾延迟 p99：主机信用把线端延迟彻底锁平在 ~1.5–2.1 µs")
ax.legend(loc="upper left", fontsize=7.2, frameon=True, facecolor="white")

fig.suptitle(
    "MRV-115 Part 2：闭环并发窗口扫描（W = 1 → 256）吞吐单调性与尾延迟对比\n"
    "固定基线：MOVDIR64B → BAR4 + 卡 WC → 主机 DDIO，64 B，cpu40 3.90 GHz，uncore 2.20 GHz，卡 2.00 GHz（3 轮交错中位数，全 0 丢包）",
    fontsize=10.3,
    fontweight="bold",
)
out = mrvplot.save(fig, "mrv-115-closed-loop-monotonicity")
print(f"Saved {out}")
