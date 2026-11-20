#!/usr/bin/env python3
"""MRV-115 Figure 1: Part 1 Open-Loop (open_echo) True End-to-End Latency vs Achieved Throughput (WC1 & WC4)."""
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


fig, axes = plt.subplots(1, 2, figsize=(14.4, 6.0), sharey=True)

# ---- Panel A: WC1 (1 card core, paced, target 1 -> 30 M/s) ----
ax = axes[0]
rates1 = [1.0, 5.0, 10.0, 13.5, 16.0, 20.0, 25.0, 30.0]
series1 = [
    ("unshaped", "无整形 (W_cap=4032)", "#dc2626", "o", 2.3, "-"),
    ("credit_24", "主机信用 W_credit=24 (19.4 M/s 封顶)", "#d97706", "^", 2.0, "-"),
    ("credit_32", "主机信用 W_credit=32 (24.8 M/s 封顶)", "#1d4ed8", "s", 2.4, "-"),
    ("credit_48", "主机信用 W_credit=48 (可达 30 M/s)", "#059669", "D", 2.3, "-"),
    ("credit_64", "主机信用 W_credit=64 (U 型恶化)", "#64748b", "v", 1.8, "--"),
]

ax.axhline(2.0, color="#0f766e", ls=":", lw=1.4, label="目标线 tot p99 = 2.0 µs")
ax.axhspan(0.65, 2.0, color="#ccfbf1", alpha=0.35, zorder=0)
ax.axvline(13.5, color="#0f766e", ls="--", lw=1.1, alpha=0.55)
ax.text(13.1, 0.72, "WC1 达标区 ≤ 13.5 M/s", ha="right", va="bottom", fontsize=7.6, color="#0f766e", fontweight="bold")

for shp, lbl, col, mk, lw, ls in series1:
    xs, ys = [], []
    for r in rates1:
        k = ("p1_wc1_paced", 1, "paced", r, shp, 0)
        if k in by_key:
            xs.append(med(k, lambda x: x["achieved_mps"]))
            ys.append(med(k, lambda x: x["tot_us"]["p99"]))
            # Plot all n=3 individual replicate points
            for rec in by_key[k]:
                ax.scatter(
                    rec["achieved_mps"],
                    rec["tot_us"]["p99"],
                    color=col,
                    marker=mk,
                    s=20,
                    alpha=0.38,
                    zorder=3,
                    edgecolors="none",
                )
    ax.plot(xs, ys, f"{ls}{mk}", color=col, lw=lw, ms=5.6, label=lbl, zorder=4)

# Plot BOTH 20 M/s card-pause controls: unshaped_p1000 & credit_48_p1000
k_up1000 = ("p1_wc1_pause1000", 1, "paced", 20.0, "unshaped_p1000", 1000)
k_cp1000 = ("p1_wc1_pause1000", 1, "paced", 20.0, "credit_48_p1000", 1000)
y_up1000 = med(k_up1000, lambda x: x["tot_us"]["p99"])
y_cp1000 = med(k_cp1000, lambda x: x["tot_us"]["p99"])
ax.scatter([19.85], [y_up1000], marker="*", s=220, color="#7c3aed", zorder=7,
           label=f"对照：无整形 + 卡退避 1 µs ({y_up1000:.2f} µs)")
ax.scatter([20.20], [y_cp1000], marker="P", s=90, facecolors="white", edgecolors="#7c3aed", linewidths=1.8, zorder=7,
           label=f"对照：C=48 + 卡退避 1 µs ({y_cp1000:.2f} µs)")

# Key annotations (placed strictly in open whitespace)
ax.annotate(
    "无整形 13.5 M/s\nrep1 跳至 36.4 µs",
    xy=(13.5, 36.39),
    xytext=(2.5, 140),
    fontsize=7.5,
    color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.25", fc="#fef2f2", ec="#fca5a5", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.1),
)
ax.annotate(
    "20 M/s 主机信用：\nC=32/48 tot 7.91/7.73 µs\n(wire 2.69/3.83)\nC=64 恶化至 20.0 µs",
    xy=(20.0, 7.91),
    xytext=(22.0, 3.0),
    fontsize=7.3,
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.25", fc="#eff6ff", ec="#93c5fd", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.1),
)
ax.annotate(
    "卡退避 1 µs 单独起效：\nunshaped=2.30 vs C=48=2.35 µs",
    xy=(19.85, y_up1000),
    xytext=(17.2, 0.76),
    fontsize=7.3,
    color="#5b21b6",
    bbox=dict(boxstyle="round,pad=0.22", fc="#f5f3ff", ec="#c4b5fd", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#7c3aed", lw=1.1),
)
ax.annotate(
    "C=24 在 19.4 M/s 过载\ntot 4075→47087 µs\n(wire 假象 1.86 µs)",
    xy=(19.37, 29965),
    xytext=(12.2, 60000),
    fontsize=7.2,
    color="#92400e",
    bbox=dict(boxstyle="round,pad=0.24", fc="#fffbeb", ec="#fcd34d", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#d97706", lw=1.1),
)

ax.set_yscale("log")
ax.set_ylim(0.65, 220000)
ax.set_xlim(0, 32.5)
ax.set_xlabel("实测达成吞吐 achieved_mps (M msg/s，等距到达)*")
ax.set_ylabel("真实端到端往返尾延迟 tot p99 (µs，对数坐标)")
ax.set_title("A. 1 卡核 (WC1)：仅 ≤ 13.5 M/s 达标 ≤ 2 µs；20 M/s 端到端最优 7.73 µs")
ax.legend(loc="upper left", fontsize=6.7, frameon=True, facecolor="white", ncol=1)

# ---- Panel B: WC4 (4 card cores, paced, target 1 -> 25 M/s) ----
ax = axes[1]
rates4 = [1.0, 5.0, 10.0, 13.5, 16.0, 20.0, 25.0]
ax.axhline(2.0, color="#0f766e", ls=":", lw=1.4, label="目标线 tot p99 = 2.0 µs")
ax.axhspan(0.65, 2.0, color="#ccfbf1", alpha=0.35, zorder=0)
ax.axvline(10.0, color="#0f766e", ls="--", lw=1.1, alpha=0.55)
ax.text(9.6, 0.72, "WC4 达标区 ≤ 10 M/s", ha="right", va="bottom", fontsize=7.6, color="#0f766e", fontweight="bold")

series4 = [
    ("p1_wc4_paced", "unshaped", 0, "无整形 (W_cap=4032)", "#dc2626", "o", 2.3, "-"),
    ("p1_wc4_paced", "burst8_c24", 0, "主机突发=8 + 信用 C=24 (18.8 M/s 封顶)", "#1d4ed8", "s", 2.4, "-"),
    ("p1_wc4_paced", "burst8_c32", 0, "主机突发=8 + 信用 C=32 (坍缩至 13.4 M/s)", "#d97706", "^", 2.0, "-"),
    ("p1_wc4_pause1800", "p1800_unshaped", 1800, "对照：无整形 + 卡退避 1.8 µs (仅测 10–25 M/s)", "#7c3aed", "D", 2.1, "-"),
]

for part, shp, pns, lbl, col, mk, lw, ls in series4:
    xs, ys = [], []
    for r in rates4:
        k = (part, 4, "paced", r, shp, pns)
        if k in by_key:
            xs.append(med(k, lambda x: x["achieved_mps"]))
            ys.append(med(k, lambda x: x["tot_us"]["p99"]))
            for rec in by_key[k]:
                ax.scatter(
                    rec["achieved_mps"],
                    rec["tot_us"]["p99"],
                    color=col,
                    marker=mk,
                    s=24 if shp == "p1800_unshaped" else 20,
                    alpha=0.45 if shp == "p1800_unshaped" else 0.38,
                    zorder=3,
                    edgecolors="none",
                )
    ax.plot(xs, ys, f"{ls}{mk}", color=col, lw=lw, ms=5.6, label=lbl, zorder=4)

# Highlight WC4 20 M/s p1800 rep 0 outlier explicitly (all boxes in clean open whitespace)
ax.scatter([20.09], [12.43], s=95, facecolors="none", edgecolors="#7c3aed", linewidths=1.8, zorder=7)
ax.annotate(
    "p1800 20M rep 0 崩盘：\ntot=12.4 (p99.9=518 µs)\n无信用兜底打满 4032",
    xy=(20.09, 12.43),
    xytext=(19.2, 42),
    fontsize=7.3,
    color="#5b21b6",
    bbox=dict(boxstyle="round,pad=0.24", fc="#f5f3ff", ec="#c4b5fd", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#7c3aed", lw=1.1),
)
ax.annotate(
    "p1800 10 M/s 轻载惩罚：\np50 1.28→2.27, p99=2.86 µs",
    xy=(10.0, 2.86),
    xytext=(0.7, 9.5),
    fontsize=7.3,
    color="#5b21b6",
    bbox=dict(boxstyle="round,pad=0.23", fc="#f5f3ff", ec="#c4b5fd", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#7c3aed", lw=1.1),
)
ax.annotate(
    "13.5→16 M/s C=24：\n消悬崖但 tot 4.36→25.0 µs\n20/25M 在 18.8 M/s 过载",
    xy=(18.76, 8483.66),
    xytext=(0.7, 280),
    fontsize=7.4,
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.24", fc="#eff6ff", ec="#93c5fd", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.1),
)
ax.annotate(
    "C=32 滑入 13.4 M/s 慢态\n(注：受 MAX_DRAIN=8 影响)",
    xy=(13.38, 63701),
    xytext=(15.5, 58000),
    fontsize=7.3,
    color="#92400e",
    bbox=dict(boxstyle="round,pad=0.24", fc="#fffbeb", ec="#fcd34d", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#d97706", lw=1.1),
)

ax.set_xlim(0, 27.2)
ax.set_xlabel("实测达成吞吐 achieved_mps (M msg/s，等距到达)*")
ax.set_title("B. 4 卡核 (WC4)：仅 ≤ 10 M/s 达标 ≤ 2 µs；C=24 在 18.8 M/s 封顶")
ax.legend(loc="upper left", fontsize=6.7, frameon=True, facecolor="white", ncol=1)

fig.suptitle(
    "MRV-115 Part 1：开环请求—回复 (open_echo) 真实端到端尾延迟 tot p99 vs 实测吞吐\n"
    "固定基线：MOVDIR64B→BAR4 + 卡 WC→DDIO，64 B，cpu40 3.90 GHz，uncore 2.20 GHz，卡 2.00 GHz，MAX_DRAIN=8（中位线 + 全部 n=3 单轮散点，全 0 丢包）",
    fontsize=10.8,
    fontweight="bold",
)
fig.text(
    0.5,
    0.008,
    "*注：无整形 25/30 M/s 实测吞吐略高于设定率（25.5/30.8 M/s），源于 OpenLoop 停止新到达后积压尾队列高速排空。",
    ha="center",
    fontsize=7.8,
    color="#475569",
)
fig.tight_layout(rect=[0, 0.028, 1, 0.94])
png = mrvplot.HERE / "mrv-115-open-loop-wc1-wc4.png"
fig.savefig(png)
fig.savefig(mrvplot.HERE / "mrv-115-open-loop-wc1-wc4.svg")
plt.close(fig)
print(f"Saved {png}")
