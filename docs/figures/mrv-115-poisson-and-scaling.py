#!/usr/bin/env python3
"""MRV-115 Figure 2: Poisson Arrivals (incl. WC4 20 M/s & unshaped_p1000) + U-shaped tot p99 & Intercept Scaling Law."""
import collections
import json
import pathlib
import statistics
import sys

import matplotlib.gridspec as gridspec
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
    if r["mode"] == "open_echo":
        key = (r["part"], r["card_cores"], r["arrival"], r["target_mps"], r["shaping"], r["card_pause_ns"])
        by_key[key].append(r)


def med(key, fn):
    return statistics.median(fn(r) for r in by_key[key])


fig = plt.figure(figsize=(14.8, 6.4))
gs = gridspec.GridSpec(2, 2, width_ratios=[1.18, 1.0], height_ratios=[0.92, 1.08], hspace=0.34, wspace=0.22)

# ---- Panel A: ALL tested Poisson groups (including WC4 20 M/s overload!) ----
ax = fig.add_subplot(gs[:, 0])
groups = [
    ("WC1\n10M", ("p1_wc1_poisson", 1, "poisson", 10.0, "unshaped", 0), ("p1_wc1_poisson", 1, "poisson", 10.0, "credit_32", 0)),
    ("WC1\n15M", ("p1_wc1_poisson", 1, "poisson", 15.0, "unshaped", 0), ("p1_wc1_poisson", 1, "poisson", 15.0, "credit_32", 0)),
    ("WC1\n20M", ("p1_wc1_poisson", 1, "poisson", 20.0, "unshaped", 0), ("p1_wc1_poisson", 1, "poisson", 20.0, "credit_32", 0)),
    ("WC4\n10M", ("p1_wc4_poisson", 4, "poisson", 10.0, "unshaped", 0), ("p1_wc4_poisson", 4, "poisson", 10.0, "burst8_c24", 0)),
    ("WC4\n13.5M", ("p1_wc4_poisson", 4, "poisson", 13.5, "unshaped", 0), ("p1_wc4_poisson", 4, "poisson", 13.5, "burst8_c24", 0)),
    ("WC4\n16M", ("p1_wc4_poisson", 4, "poisson", 16.0, "unshaped", 0), ("p1_wc4_poisson", 4, "poisson", 16.0, "burst8_c24", 0)),
    ("WC4 20M\n(C=24过载)", ("p1_wc4_poisson", 4, "poisson", 20.0, "unshaped", 0), ("p1_wc4_poisson", 4, "poisson", 20.0, "burst8_c24", 0)),
]

x = np.arange(len(groups))
w = 0.26
u_tot = [med(ku, lambda r: r["tot_us"]["p99"]) for _, ku, _ in groups]
s_tot = [med(ks, lambda r: r["tot_us"]["p99"]) for _, _, ks in groups]
s_wire = [med(ks, lambda r: r["wire_us"]["p99"]) for _, _, ks in groups]

b1 = ax.bar(x - w, u_tot, width=w, color="#ef4444", alpha=0.86, label="无整形 (W_cap=4032) 端到端 tot p99")
b2 = ax.bar(x, s_tot, width=w, color="#2563eb", alpha=0.90, label="主机信用 (WC1 C=32 / WC4 C=24) 端到端 tot p99")
b3 = ax.bar(x + w, s_wire, width=w, color="#93c5fd", alpha=0.90, hatch="//", edgecolor="#1e40af", label="主机信用 线端 wire p99（不含主机排队）")

# Overlay individual n=3 replicate dots on every bar
for i, (_, ku, ks) in enumerate(groups):
    for j, r in enumerate(by_key[ku]):
        ax.scatter(i - w + (j - 1) * 0.04, r["tot_us"]["p99"], color="#7f1d1d", s=14, zorder=5)
    for j, r in enumerate(by_key[ks]):
        ax.scatter(i + (j - 1) * 0.04, r["tot_us"]["p99"], color="#1e3a8a", s=14, zorder=5)
        ax.scatter(i + w + (j - 1) * 0.04, r["wire_us"]["p99"], color="#1d4ed8", s=12, zorder=5)

ax.axhline(2.0, color="#0f766e", ls=":", lw=1.4, label="目标线 p99 = 2.0 µs")
ax.set_yscale("log")
ax.set_ylim(0.7, 35000)
ax.set_xticks(x)
ax.set_xticklabels([g[0] for g in groups], fontsize=8.0)
ax.set_ylabel("泊松到达往返尾延迟 p99 (µs，对数坐标)")
ax.set_title("A. 泊松随机到达（含全部 7 档与 n=3 散点）：WC4 20M 暴露 wire 假象")

for bars in (b1, b2, b3):
    for rect in bars:
        h = rect.get_height()
        if h >= 1000:
            lbl = f"{h/1000:.1f}k"
        elif h >= 100:
            lbl = f"{h:.0f}"
        elif h >= 10:
            lbl = f"{h:.0f}"
        else:
            lbl = f"{h:.1f}"
        ax.text(rect.get_x() + rect.get_width() / 2, h * 1.16, lbl, ha="center", va="bottom", fontsize=6.9, fontweight="bold")

# Plot BOTH WC1 20M Poisson card-pause controls
k_up = ("p1_wc1_pause1000", 1, "poisson", 20.0, "unshaped_p1000", 1000)
k_cp = ("p1_wc1_pause1000", 1, "poisson", 20.0, "credit_48_p1000", 1000)
y_up = med(k_up, lambda r: r["tot_us"]["p99"])
y_cp = med(k_cp, lambda r: r["tot_us"]["p99"])
ax.scatter([1.92], [y_up], marker="*", s=180, color="#7c3aed", zorder=7,
           label=f"对照：WC1 20M 无整形+卡退避 1 µs ({y_up:.2f} µs)")
ax.scatter([2.10], [y_cp], marker="P", s=78, facecolors="white", edgecolors="#7c3aed", linewidths=1.6, zorder=7,
           label=f"对照：WC1 20M C=48+卡退避 1 µs ({y_cp:.2f} µs)")

ax.annotate(
    "仅达 18.7 M/s 过载！\ntot=9079 µs，但 wire=2.1 µs",
    xy=(6.0, 9079),
    xytext=(3.2, 6500),
    fontsize=7.6,
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.25", fc="#eff6ff", ec="#93c5fd", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.1),
)
ax.legend(loc="upper left", fontsize=6.9, frameon=True, facecolor="white")

# ---- Panel B1 (top right): U-shaped tot p99 vs W_credit at 20/25/30 M/s ----
ax1 = fig.add_subplot(gs[0, 1])
credits = np.array([24, 32, 48, 64])
shps = ["credit_24", "credit_32", "credit_48", "credit_64"]

for rate, col, mk, lbl in [
    (20.0, "#1d4ed8", "o", "WC1 20 M/s tot p99"),
    (25.0, "#059669", "s", "WC1 25 M/s tot p99"),
    (30.0, "#d97706", "^", "WC1 30 M/s tot p99"),
]:
    ys = [med(("p1_wc1_paced", 1, "paced", rate, s, 0), lambda r: r["tot_us"]["p99"]) for s in shps]
    achs = [med(("p1_wc1_paced", 1, "paced", rate, s, 0), lambda r: r["achieved_mps"]) for s in shps]
    ax1.plot(credits, ys, f"-{mk}", color=col, lw=2.0, ms=5.8, label=lbl, zorder=4)
    # Mark overloaded points (achieved < 0.995 * target) with hollow red rings
    for c, y, a, s in zip(credits, ys, achs, shps):
        for rec in by_key[("p1_wc1_paced", 1, "paced", rate, s, 0)]:
            ax1.scatter(c, rec["tot_us"]["p99"], color=col, s=16, alpha=0.4, zorder=3)
        if a < rate * 0.995:
            ax1.scatter(c, y, s=95, facecolors="none", edgecolors="#dc2626", linewidths=1.6, zorder=6)

ax1.axhline(2.0, color="#0f766e", ls=":", lw=1.2)
ax1.annotate(
    "C=24 在 20M 过载\n(19.4 M/s, 4075 µs)",
    xy=(24, 4075),
    xytext=(17.0, 55),
    fontsize=7.2,
    color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.22", fc="#fef2f2", ec="#fca5a5", lw=0.8),
    arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.0),
)
ax1.annotate(
    "U 型恶化（C=48→64）：\n20M: 7.73→20.0 µs\n25M: 25.3→56.6 µs",
    xy=(64, 20.0),
    xytext=(46.0, 260),
    fontsize=7.2,
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.22", fc="#eff6ff", ec="#93c5fd", lw=0.8),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.0),
)
ax1.scatter([], [], s=70, facecolors="none", edgecolors="#dc2626", linewidths=1.5, label="红圈：过载未达标")
ax1.set_yscale("log")
ax1.set_xlim(16, 70)
ax1.set_ylim(1.5, 350000)
ax1.set_ylabel("端到端 tot p99 (µs)")
ax1.set_title("B1. WC1 端到端 tot p99 呈 U 型：C 过小过载，C=64 拖长慢态排空")
ax1.legend(loc="upper right", fontsize=6.7, frameon=True, facecolor="white", ncol=2)

# ---- Panel B2 (bottom right): Intercept Scaling Law (wire p99.9 = 0.58 + W * 77.0 ns) ----
ax2 = fig.add_subplot(gs[1, 1])
wp999 = np.array([med(("p1_wc1_paced", 1, "paced", 20.0, s, 0), lambda r: r["wire_us"]["p999"]) for s in shps])
wp99 = np.array([med(("p1_wc1_paced", 1, "paced", 20.0, s, 0), lambda r: r["wire_us"]["p99"]) for s in shps])
slope, intercept = np.polyfit(credits, wp999, 1)  # ~0.0770 us/msg, 0.578 us
w_2us = (2.0 - intercept) / slope  # ~18.5

c_line = np.linspace(16, 70, 200)
ax2.plot(c_line, intercept + slope * c_line, "--", color="#7c3aed", lw=1.9,
         label=f"满窗慢态拟合：{intercept:.2f} µs + W_credit × {slope*1000:.1f} ns")
ax2.plot(credits, wp999, "-D", color="#7c3aed", lw=2.1, ms=6.2, label="实测 WC1 20M wire p99.9 (µs)")
ax2.plot(credits, wp99, "-o", color="#1d4ed8", lw=2.0, ms=5.8, label="实测 WC1 20M wire p99 (µs)")
for c, s in zip(credits, shps):
    for rec in by_key[("p1_wc1_paced", 1, "paced", 20.0, s, 0)]:
        ax2.scatter(c, rec["wire_us"]["p999"], color="#7c3aed", s=16, alpha=0.4)
        ax2.scatter(c, rec["wire_us"]["p99"], color="#1d4ed8", s=16, alpha=0.4)

# Mark C=24 as overloaded (achieved 19.38 M/s)
ax2.scatter([24], [wp99[0]], s=95, facecolors="none", edgecolors="#dc2626", linewidths=1.5, zorder=6)

# Shade the wide infeasible gap [18.5, 25]
ax2.axvspan(w_2us, 25.0, color="#fde68a", alpha=0.55, zorder=0)
ax2.axvline(w_2us, color="#7c3aed", ls=":", lw=1.3)
ax2.axvline(25.0, color="#b45309", ls=":", lw=1.3)
ax2.axhline(2.0, color="#0f766e", ls=":", lw=1.4)

ax2.annotate(
    f"宽不可行缺口 [{w_2us:.1f}, 25]：\n满窗 ≤ 2 µs 需 W ≤ 18\n20 M/s 利特尔下限 ≥ 25",
    xy=((w_2us + 25.0) / 2, 2.0),
    xytext=(27.5, 4.75),
    fontsize=7.4,
    color="#92400e",
    fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.25", fc="#fffbeb", ec="#f59e0b", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#b45309", lw=1.1),
)
for c, y999, y99 in zip(credits, wp999, wp99):
    ax2.text(c - 0.8, y999 + 0.22, f"{y999:.2f}", ha="right", va="bottom", fontsize=7.3, color="#5b21b6", fontweight="bold")
    if c == 24:
        ax2.text(c + 1.8, y99 - 0.34, f"{y99:.2f} (仅19.4M)", ha="left", va="top", fontsize=7.1, color="#991b1b", fontweight="bold")
    else:
        ax2.text(c + 1.2, y99 - 0.24, f"{y99:.2f}", ha="left", va="top", fontsize=7.2, color="#1e3a8a")

ax2.set_xlim(16, 70)
ax2.set_ylim(0.8, 6.4)
ax2.set_xlabel("主机在途信用上限 W_credit（条）")
ax2.set_ylabel("线端延迟 wire (µs)")
ax2.set_title("B2. 含回程截距的慢态排空上界：wire p99.9 = 0.58 µs + W × 77.0 ns")
ax2.legend(loc="lower right", fontsize=6.9, frameon=True, facecolor="white")

fig.suptitle(
    "MRV-115：泊松随机到达全档位验证、U 型端到端退化与含截距硬件标度律\n"
    "固定基线：MOVDIR64B + WC，64 B，cpu40 3.90 GHz，uncore 2.20 GHz，卡 2.00 GHz，MAX_DRAIN=8（中位数 + 全部 n=3 单轮散点）",
    fontsize=10.8,
    fontweight="bold",
)
fig.subplots_adjust(left=0.055, right=0.985, top=0.875, bottom=0.095)
png = mrvplot.HERE / "mrv-115-poisson-and-scaling.png"
fig.savefig(png)
fig.savefig(mrvplot.HERE / "mrv-115-poisson-and-scaling.svg")
plt.close(fig)
print(f"Saved {png}")
