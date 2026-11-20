#!/usr/bin/env python3
"""MRV-115 Figure 1: Part 1 Open-Loop (open_echo) Latency-Throughput Curve (WC1 & WC4)."""
import collections
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv115")
RUNS = ROOT / "logs/mrv115-2026-11-20/runs.jsonl"

recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
for r in recs:
    assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
    assert r["host_pmu"]["min_pct"] == 100.0 and r["card_pmu"]["min_pct"] == 100.0

by_key = collections.defaultdict(list)
for r in recs:
    if r["mode"] == "open_echo":
        key = (r["part"], r["card_cores"], r["arrival"], r["target_mps"], r["shaping"], r["card_pause_ns"])
        by_key[key].append(r)


def med(key, fn):
    return statistics.median(fn(r) for r in by_key[key])


fig, axes = plt.subplots(1, 2, figsize=(14.0, 5.6), sharey=True)

# ---- Panel A: WC1 (1 card core, paced, 1 -> 30 M/s) ----
ax = axes[0]
rates1 = [1.0, 5.0, 10.0, 13.5, 16.0, 20.0, 25.0, 30.0]
series1 = [
    ("unshaped", "无整形 (W_cap=4032)", "#dc2626", "o", 2.3),
    ("credit_24", "主机信用 W_credit=24", "#d97706", "^", 1.9),
    ("credit_32", "主机信用 W_credit=32", "#1d4ed8", "s", 2.3),
    ("credit_48", "主机信用 W_credit=48", "#059669", "D", 2.1),
]

ax.axhline(2.0, color="#0f766e", ls=":", lw=1.4, label="目标线 p99 = 2.0 µs")
ax.axhspan(0.7, 2.0, color="#ccfbf1", alpha=0.35, zorder=0)

for shp, lbl, col, mk, lw in series1:
    xs, y_tot, y_wire = [], [], []
    for rate in rates1:
        k = ("p1_wc1_paced", 1, "paced", rate, shp, 0)
        if k in by_key:
            xs.append(rate)
            y_tot.append(med(k, lambda r: r["tot_us"]["p99"]))
            y_wire.append(med(k, lambda r: r["wire_us"]["p99"]))
    ax.plot(xs, y_tot, f"-{mk}", color=col, lw=lw, ms=5.6, label=f"{lbl} (端到端 p99)")
    if shp in ("unshaped", "credit_32"):
        ax.plot(xs, y_wire, f"--{mk}", color=col, lw=1.4, ms=4.0, alpha=0.72, label=f"{lbl} (线端 wire p99)")

# Mechanism control: WC1 20 M/s with card T_pause = 1000 ns
k_p1000 = ("p1_wc1_pause1000", 1, "paced", 20.0, "credit_48_p1000", 1000)
y_p1000 = med(k_p1000, lambda r: r["tot_us"]["p99"])
y_p1000w = med(k_p1000, lambda r: r["wire_us"]["p99"])
ax.scatter([20.0], [y_p1000], marker="*", s=200, color="#7c3aed", zorder=6, label="对照：WC1 + 卡退避 1 µs (C=48)")
ax.annotate(
    f"加卡退避 1 µs\n20 M/s 端到端 {y_p1000:.2f} µs\n(线端 {y_p1000w:.2f} µs)",
    xy=(20.0, y_p1000),
    xytext=(19.2, 0.78),
    arrowprops=dict(arrowstyle="->", color="#7c3aed", lw=1.3),
    fontsize=7.6,
    fontweight="bold",
    color="#5b21b6",
    bbox=dict(boxstyle="round,pad=0.2", fc="#f5f3ff", ec="#c4b5fd"),
)

# Annotate 20 M/s & 25 M/s
yu20 = med(("p1_wc1_paced", 1, "paced", 20.0, "unshaped", 0), lambda r: r["tot_us"]["p99"])
yc20 = med(("p1_wc1_paced", 1, "paced", 20.0, "credit_32", 0), lambda r: r["tot_us"]["p99"])
yc20w = med(("p1_wc1_paced", 1, "paced", 20.0, "credit_32", 0), lambda r: r["wire_us"]["p99"])
yu30 = med(("p1_wc1_paced", 1, "paced", 30.0, "unshaped", 0), lambda r: r["tot_us"]["p99"])
yc48_30 = med(("p1_wc1_paced", 1, "paced", 30.0, "credit_48", 0), lambda r: r["tot_us"]["p99"])

ax.annotate(
    f"无整形 20→30 M/s\n{yu20:.1f}→{yu30:.0f} µs",
    xy=(25.0, med(("p1_wc1_paced", 1, "paced", 25.0, "unshaped", 0), lambda r: r["tot_us"]["p99"])),
    xytext=(15.5, 1100.0),
    arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.2),
    fontsize=8.0,
    fontweight="bold",
    color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.2", fc="#fef2f2", ec="#fca5a5"),
)
ax.annotate(
    f"20 M/s C=32\n端到端 {yc20:.1f} / 线端 {yc20w:.2f} µs",
    xy=(20.0, yc20),
    xytext=(7.2, 8.5),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.2),
    fontsize=8.0,
    fontweight="bold",
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.2", fc="#eff6ff", ec="#93c5fd"),
)

ax.set_yscale("log")
ax.set_ylim(0.7, 120000)
ax.set_xlim(0, 31.8)
ax.set_xlabel("开环设定到达速率 R (M msg/s，等距到达)")
ax.set_ylabel("开环往返尾延迟 p99 (µs，对数坐标)")
ax.set_title("A. 1 卡核 (WC1)：主机信用压降 16–30 M/s 尾延迟 6–64×")
ax.legend(loc="upper left", fontsize=7.4, frameon=True, facecolor="white", ncol=1)

# ---- Panel B: WC4 (4 card cores, paced, 1 -> 25 M/s) ----
ax = axes[1]
rates4 = [1.0, 5.0, 10.0, 13.5, 16.0, 20.0, 25.0]
ax.axhline(2.0, color="#0f766e", ls=":", lw=1.4, label="目标线 p99 = 2.0 µs")
ax.axhspan(0.7, 2.0, color="#ccfbf1", alpha=0.35, zorder=0)

series4 = [
    ("p1_wc4_paced", "unshaped", 0, "无整形 (W_cap=4032)", "#dc2626", "o", 2.3),
    ("p1_wc4_paced", "burst8_c24", 0, "主机突发=8 + 信用=24 (<W_crit)", "#1d4ed8", "s", 2.3),
    ("p1_wc4_paced", "burst8_c32", 0, "主机突发=8 + 信用=32 (>W_crit)", "#d97706", "^", 1.8),
    ("p1_wc4_pause1800", "p1800_unshaped", 1800, "对照：卡退避 T_pause=1.8 µs", "#7c3aed", "D", 2.1),
]

for part, shp, pns, lbl, col, mk, lw in series4:
    xs, y_tot, y_wire = [], [], []
    for rate in rates4:
        k = (part, 4, "paced", rate, shp, pns)
        if k in by_key:
            xs.append(rate)
            y_tot.append(med(k, lambda r: r["tot_us"]["p99"]))
            y_wire.append(med(k, lambda r: r["wire_us"]["p99"]))
    ax.plot(xs, y_tot, f"-{mk}", color=col, lw=lw, ms=5.6, label=f"{lbl} (端到端 p99)")
    if shp in ("unshaped", "burst8_c24"):
        ax.plot(xs, y_wire, f"--{mk}", color=col, lw=1.4, ms=4.0, alpha=0.72, label=f"{lbl} (线端 wire p99)")

yu135 = med(("p1_wc4_paced", 4, "paced", 13.5, "unshaped", 0), lambda r: r["tot_us"]["p99"])
ys135 = med(("p1_wc4_paced", 4, "paced", 13.5, "burst8_c24", 0), lambda r: r["tot_us"]["p99"])
ys135w = med(("p1_wc4_paced", 4, "paced", 13.5, "burst8_c24", 0), lambda r: r["wire_us"]["p99"])
yp25 = med(("p1_wc4_pause1800", 4, "paced", 25.0, "p1800_unshaped", 1800), lambda r: r["tot_us"]["p99"])

ax.annotate(
    f"13.5 M/s 无整形\np99 = {yu135:.1f} µs",
    xy=(13.5, yu135),
    xytext=(3.5, 115.0),
    arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.2),
    fontsize=8.0,
    fontweight="bold",
    color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.2", fc="#fef2f2", ec="#fca5a5"),
)
ax.annotate(
    f"13.5 M/s C=24\n端到端 {ys135:.2f} / 线端 {ys135w:.2f} µs",
    xy=(13.5, ys135),
    xytext=(1.8, 7.5),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.2),
    fontsize=7.9,
    fontweight="bold",
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.2", fc="#eff6ff", ec="#93c5fd"),
)
ax.annotate(
    f"卡退避 1.8 µs\n25 M/s p99={yp25:.2f} µs",
    xy=(25.0, yp25),
    xytext=(14.5, 0.79),
    arrowprops=dict(arrowstyle="->", color="#7c3aed", lw=1.2),
    fontsize=7.7,
    fontweight="bold",
    color="#5b21b6",
    bbox=dict(boxstyle="round,pad=0.2", fc="#f5f3ff", ec="#c4b5fd"),
)

ax.set_xlim(0, 26.8)
ax.set_xlabel("开环设定到达速率 R (M msg/s，等距到达)")
ax.set_title("B. 4 卡核 (WC4)：C=24 消除 13.5 M/s 悬崖，但在 18.8 M/s 封顶")
ax.legend(loc="upper left", fontsize=7.4, frameon=True, facecolor="white", ncol=1)

fig.suptitle(
    "MRV-115 Part 1：开环请求—回复 (open_echo) 端到端与线端 p99 延迟—吞吐曲线\n"
    "固定基线：MOVDIR64B → BAR4 + 卡 WC → 主机 DDIO，64 B，cpu40 3.90 GHz，uncore 2.20 GHz，卡 2.00 GHz（3 轮交错中位数，全 0 丢包）",
    fontsize=10.3,
    fontweight="bold",
)
out = mrvplot.save(fig, "mrv-115-open-loop-wc1-wc4")
print(f"Saved {out}")
