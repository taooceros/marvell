#!/usr/bin/env python3
"""MRV-123 Figure 1: Part 1 Closed-Loop De-Confounding (MAX_DRAIN=32 on WC4 & WC1)."""
import collections
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv123")
RUNS = ROOT / "logs/mrv123-2026-11-22/runs.jsonl"

recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
for r in recs:
    assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
    assert r["host_pmu"]["min_pct"] >= 99.99 and r["card_pmu"]["min_pct"] >= 99.99

by_key = collections.defaultdict(list)
for r in recs:
    if r["mode"] == "closed_loop":
        by_key[(r["part"], r["shaper"], r["window"])].append(r)


def med(key, fn):
    return statistics.median(fn(r) for r in by_key[key])


fig, axes = plt.subplots(1, 2, figsize=(14.0, 5.6))

# ---- Panel A: WC4 Closed-Loop (W = 1, 24, 32, 40, 48, 64) ----
ax = axes[0]
ws4 = [1, 24, 32, 40, 48, 64]
x4 = np.arange(len(ws4))

ax.axvspan(3.5, 5.4, color="#fee2e2", alpha=0.35, zorder=0)
ax.axhline(13.1, color="#991b1b", ls=":", lw=1.1, alpha=0.75)
ax.axhline(20.0, color="#0f766e", ls="--", lw=1.1, alpha=0.7, label="20 M/s 参考线")

y_u = [med(("p1_cl_wc4", "unshaped", w), lambda r: r["mops"]) for w in ws4]
y_b8 = [med(("p1_cl_wc4", "burst_8", w), lambda r: r["mops"]) for w in ws4]

ax.plot(x4, y_u, "-o", color="#dc2626", lw=2.3, ms=6.2,
        label="WC4 无整形（悬崖恢复至 W=40→48，18.80→13.16 M/s）", zorder=4)
ax.plot(x4, y_b8, "-s", color="#1d4ed8", lw=2.3, ms=6.2,
        label="WC4 burst_8（稳态复活，W=40/48 达 21.0 M/s）", zorder=5)

for shp, col, mk, xoff in [("unshaped", "#dc2626", "o", -0.08), ("burst_8", "#1d4ed8", "s", 0.08)]:
    for i, w in enumerate(ws4):
        for r in by_key[("p1_cl_wc4", shp, w)]:
            ax.scatter(i + xoff, r["mops"], color=col, marker=mk, s=24, alpha=0.55, zorder=6)

# Plot W=64 credit_32 B=1,8,16 points slightly offset around x=5
c32_specs = [
    ("c32_b1", "W=64 C=32 B=1 (18.74 M/s)", "#059669", "^", 4.82),
    ("c32_b8", "W=64 C=32 B=8 (18.26 M/s)", "#d97706", "v", 5.00),
    ("c32_b16", "W=64 C=32 B=16 (18.14 M/s)", "#7c3aed", "D", 5.18),
]
for shp, lbl, col, mk, xp in c32_specs:
    ym = med(("p1_cl_wc4", shp, 64), lambda r: r["mops"])
    ax.scatter([xp], [ym], color=col, marker=mk, s=75, edgecolors="white", linewidths=0.8, label=lbl, zorder=7)
    for r in by_key[("p1_cl_wc4", shp, 64)]:
        ax.scatter(xp, r["mops"], color=col, marker=mk, s=20, alpha=0.45, zorder=6)

ax.annotate(
    "W=32/40 稳守 18.8 M/s\n（剥离 MRV-115 W=24→32 混淆）",
    xy=(2.0, y_u[2]), xytext=(0.45, 22.8),
    fontsize=8.2, color="#991b1b", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#dc2626", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.1),
)
ax.annotate(
    "burst_8 复活\n20.99 M/s (bw=230k)",
    xy=(4.0, y_b8[4]), xytext=(2.85, 24.0),
    fontsize=8.2, color="#1d4ed8", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#1d4ed8", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.1),
)
ax.text(4.45, 11.4, "13.1 M/s 慢态", ha="center", fontsize=8.3, color="#991b1b", fontweight="bold")

ax.set_xticks(x4)
ax.set_xticklabels([str(w) for w in ws4])
ax.set_xlabel("闭环并发窗口 W（在途请求数）")
ax.set_ylabel("闭环吞吐 (M msg/s，64 B)")
ax.set_ylim(0, 27.5)
ax.set_title("A. WC4 闭环：恢复 MAX_DRAIN=32 将悬崖拉回 W=40→48 并复活 burst_8")
ax.legend(loc="lower right", fontsize=7.2, frameon=True, facecolor="white")

# ---- Panel B: WC1 Closed-Loop Throughput & Host Send Batch (MAX_DRAIN=32 stop-and-wait) ----
ax = axes[1]
ws1 = [1, 16, 24, 32, 48, 64]
x1 = np.arange(len(ws1))

y_1m = [med(("p1_cl_wc1", "unshaped", w), lambda r: r["mops"]) for w in ws1]
y_1b = [med(("p1_cl_wc1", "unshaped", w), lambda r: (r["rounds"] + r["warmup"]) / max(1, r["send_batches"])) for w in ws1]

ax.axhline(20.0, color="#0f766e", ls="--", lw=1.1, alpha=0.7)
l1 = ax.plot(x1, y_1m, "-o", color="#1d4ed8", lw=2.4, ms=6.4, label="WC1 闭环吞吐 (M msg/s，左轴)", zorder=5)
for i, w in enumerate(ws1):
    for r in by_key[("p1_cl_wc1", "unshaped", w)]:
        ax.scatter(i, r["mops"], color="#1d4ed8", marker="o", s=24, alpha=0.55, zorder=6)

ax2 = ax.twinx()
ax2.spines["top"].set_visible(False)
ax.set_zorder(ax2.get_zorder() + 1)
ax.patch.set_visible(False)
l2 = ax2.bar(x1, y_1b, width=0.38, color="#e2e8f0", edgecolor="#94a3b8", lw=1.0, alpha=0.85,
             label="主机单批发送均值 msg/send_batch（右轴）", zorder=1)

for i, bval in enumerate(y_1b):
    ax2.text(i, bval + 0.7, f"{bval:.1f}", ha="center", va="bottom", fontsize=7.8, color="#475569", fontweight="bold")

ax.annotate(
    "W=32 == MAX_DRAIN=32：\n单次排空 30.8 条 → 停等往返\n吞吐封顶 18.53 M/s < 20 M/s",
    xy=(3, y_1m[3]), xytext=(0.15, 27.6),
    fontsize=8.0, color="#1e3a8a", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.3", fc="#eff6ff", ec="#1d4ed8", lw=1.0),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.2, connectionstyle="arc3,rad=0.18"),
)
ax.annotate(
    "W=48 > MAX_DRAIN=32：\n流水恢复 26.98 M/s",
    xy=(4, y_1m[4]), xytext=(3.15, 34.0),
    fontsize=8.0, color="#0f766e", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.28", fc="white", ec="#0f766e", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#0f766e", lw=1.1),
)

ax.set_xticks(x1)
ax.set_xticklabels([str(w) for w in ws1])
ax.set_xlabel("闭环并发窗口 W（在途请求数）")
ax.set_ylabel("闭环吞吐 (M msg/s，64 B)", color="#1d4ed8")
ax2.set_ylabel("主机单批发送均值 (条/批)", color="#475569")
ax.set_ylim(0, 39)
ax2.set_ylim(0, 39)
ax.set_title("B. WC1 闭环：W=32 时 MAX_DRAIN=32 排空整窗（30.8 条/批）拉低吞吐")

handles = l1 + [l2]
labels = [h.get_label() for h in handles]
ax.legend(handles, labels, loc="upper left", fontsize=7.4, frameon=True, facecolor="white")

fig.suptitle(
    "MRV-123 Part 1：恢复 MAX_DRAIN=32 剥离软件混淆（直通 MOVDIR64B + WC，64 B，T_pause=0，n=3 全散点）",
    fontsize=12.0, fontweight="bold", y=0.98,
)
fig.text(
    0.5, 0.008,
    "固定基线：cpu40 3.86–3.90 GHz，uncore 2.20 GHz，卡 2.00 GHz，批 1/32，MAX_DRAIN=32（排空后单次 RDTSCP）；全部 63 次闭环 lost=bad=torn=0，PMU 100%",
    ha="center", fontsize=8.0, color="#475569",
)
fig.tight_layout(rect=[0, 0.03, 1, 0.94])
png = mrvplot.HERE / "mrv-123-deconfound-closed-loop.png"
fig.savefig(png)
fig.savefig(mrvplot.HERE / "mrv-123-deconfound-closed-loop.svg")
plt.close(fig)
print(f"Saved {png}")
