#!/usr/bin/env python3
"""MRV-123 Figure 2: Part 2 Primary Single-Variable Open-Loop Poisson Sweep (W_credit=32, B in {1, 8, 16} vs unshaped)."""
import collections
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt

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
    if r["part"] in ("p2_wc1_poisson", "p2_wc4_poisson"):
        by_key[(r["part"], r["shaper"], r["offered_mps"])].append(r)


def med(key, fn):
    return statistics.median(fn(r) for r in by_key[key])


rates = [1.0, 5.0, 10.0, 13.5, 16.0, 20.0]
series = [
    ("unshaped", "无整形 (W_cap=4032)", "#dc2626", "o", 2.0, "--"),
    ("c32_b1", "W_credit=32, B=1（逐笔释放）", "#64748b", "s", 2.1, "-"),
    ("c32_b8", "W_credit=32, B=8（排空 8 条）", "#d97706", "^", 2.1, "-"),
    ("c32_b16", "W_credit=32, B=16（排空 16 条）", "#1d4ed8", "D", 2.4, "-"),
]

fig, axes = plt.subplots(1, 2, figsize=(14.4, 5.9), sharey=True)

panels = [
    (axes[0], "p2_wc1_poisson", "A. 1 卡核 (WC1，泊松 1→20 M/s)：20 M/s 下 C=32 停等过载于 17.6–17.9 M/s"),
    (axes[1], "p2_wc4_poisson", "B. 4 卡核 (WC4，泊松 1→20 M/s)：20 M/s 下 C=32 停等过载于 17.7–18.2 M/s"),
]

for ax, part, title in panels:
    ax.axhspan(0.8, 1.85, color="#ccfbf1", alpha=0.35, zorder=0)
    ax.axhline(1.85, color="#0f766e", ls=":", lw=1.2, label="轻载门槛 1.85 µs")
    ax.axhline(2.5, color="#0369a1", ls="-.", lw=1.2, label="20 M/s 目标门槛 2.5 µs")

    for shp, lbl, col, mk, lw, ls in series:
        xs, ys = [], []
        for rt in rates:
            k = (part, shp, rt)
            xs.append(med(k, lambda r: r["achieved_mps"]))
            ys.append(med(k, lambda r: r["tot_us"]["p99"]))
            for r in by_key[k]:
                ax.scatter(r["achieved_mps"], r["tot_us"]["p99"], color=col, marker=mk, s=22, alpha=0.5, zorder=6)
        ax.plot(xs, ys, f"{ls}{mk}", color=col, lw=lw, ms=5.8, label=lbl, zorder=4)

    ax.set_yscale("log")
    ax.set_ylim(0.8, 55000)
    ax.set_xlim(0.2, 21.6)
    ax.set_xlabel("实测稳态达成吞吐 achieved_mps (M msg/s，泊松到达)")
    ax.set_title(title, fontsize=10.2)
    ax.legend(loc="upper left", fontsize=7.2, frameon=True, facecolor="white")

axes[0].set_ylabel("真实端到端往返尾延迟 tot p99 (µs，对数坐标)")

# Panel A annotations (WC1)
ax = axes[0]
ax.annotate(
    "1–10 M/s 零滞回惩罚\n(10 M/s=2.50 µs 源于 MAX_DRAIN=32)",
    xy=(10.0, 2.50), xytext=(1.5, 9.5),
    fontsize=7.8, color="#334155",
    bbox=dict(boxstyle="round,pad=0.28", fc="white", ec="#94a3b8", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#64748b", lw=1.0),
)
ax.annotate(
    "20 M/s 全部过载 (<18 M/s)\nB=1: 17.88 M/s, 15.6 ms\nB=8: 17.70 M/s, 17.3 ms\nB=16: 17.64 M/s, 17.8 ms",
    xy=(17.64, 17774.13), xytext=(8.2, 3200),
    fontsize=7.9, color="#1e3a8a", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.3", fc="#eff6ff", ec="#1d4ed8", lw=1.0),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.1),
)
ax.annotate(
    "13.5 M/s 无整形\n单轮分化 2.9–31.7 µs",
    xy=(13.51, 21.81), xytext=(9.3, 120),
    fontsize=7.6, color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#dc2626", lw=0.8),
    arrowprops=dict(arrowstyle="->", color="#dc2626", lw=0.9),
)

# Panel B annotations (WC4)
ax = axes[1]
ax.scatter([10.0], [1056.58], s=110, facecolors="none", edgecolors="#64748b", linewidths=1.7, zorder=8)
ax.annotate(
    "r0 (run_id=55): 1056.6 µs\n单次 1.96 ms OS 抢占离群\n(r1/r2 = 2.52 / 2.51 µs)",
    xy=(10.0, 1056.58), xytext=(1.4, 240),
    fontsize=7.6, color="#475569",
    bbox=dict(boxstyle="round,pad=0.28", fc="white", ec="#64748b", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#64748b", lw=1.0),
)
ax.annotate(
    "20 M/s 全部过载 (<18.2 M/s)\nB=1: 18.16 M/s, 13.3 ms\nB=8: 17.83 M/s, 16.2 ms\nB=16: 17.69 M/s, 17.3 ms",
    xy=(17.69, 17340.55), xytext=(8.6, 2600),
    fontsize=7.9, color="#1e3a8a", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.3", fc="#eff6ff", ec="#1d4ed8", lw=1.0),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.1),
)
ax.annotate(
    "13.5–16 M/s C=32 压制 CHA\n44–45 → 3.3–5.6 µs\n但 B=1/8/16 重合",
    xy=(13.51, 3.31), xytext=(1.4, 14.0),
    fontsize=7.6, color="#0f766e",
    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#0f766e", lw=0.8),
    arrowprops=dict(arrowstyle="->", color="#0f766e", lw=0.9),
)

fig.suptitle(
    "MRV-123 Part 2 主实验：固定 W_credit=32 单变量扫 B ∈ {1, 8, 16}（直通 64 B，MAX_DRAIN=32，T_pause=0，n=3 全散点）",
    fontsize=11.8, fontweight="bold", y=0.98,
)
fig.text(
    0.5, 0.008,
    "横轴为稳态活跃到达窗实测吞吐 achieved_mps（排除尾排空，最大 achieved/offered_win = 1.0003）；纵轴为真实端到端 tot p99；全部 144 次运行 lost=bad=torn=0，PMU 100%",
    ha="center", fontsize=8.0, color="#475569",
)
fig.tight_layout(rect=[0, 0.03, 1, 0.94])
png = mrvplot.HERE / "mrv-123-poisson-c32.png"
fig.savefig(png)
fig.savefig(mrvplot.HERE / "mrv-123-poisson-c32.svg")
plt.close(fig)
print(f"Saved {png}")
