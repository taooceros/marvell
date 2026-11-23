"""Plot MRV-99 W=1 closed-loop period (1/rate) and single-op p50/p99 latency
from raw logs/mrv99-2026-11-19/runs.jsonl.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs" / "mrv99-2026-11-19" / "runs.jsonl"

rows = [json.loads(line) for line in RUNS.read_text().splitlines() if line.strip()]

for r in rows:
    assert r.get("lost", 0) == 0 and r.get("bad", 0) == 0 and r.get("warmup_bad", 0) == 0
    assert "mismatches=0" in r["host_verify"]
    assert "swerror=00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000" in r["host_verify"]

lat_g = defaultdict(list)
rate_g = defaultdict(list)
for r in rows:
    if r["mode"] == "w1_lat":
        lat_g[(r["op"], r["size"], r["comp_target"])].append(r)
    elif r["mode"] == "rate" and r["window"] == 1:
        rate_g[(r["op"], r["size"], r["comp_target"])].append(r)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.2, 4.9), gridspec_kw={"width_ratios": [1.0, 1.35]})

C_CTRL = "#6b7280"
C_CTRL_LIGHT = "#d1d5db"
C_TREAT = "#0f766e"
C_TREAT_LIGHT = "#99f6e4"

# --- Left: Symmetric W=1 closed-loop period 1/rate(W=1) ---
rcases = [("nop", 0, "NOP\n（空操作）"), ("memmove", 4096, "4 KiB 拷贝\n（MEMMOVE）")]
xr = np.arange(len(rcases))
wr = 0.34

ctrl_cyc = [statistics.median([r["cycle_ns"] for r in rate_g[(op, sz, "host_memzone")]]) / 1000.0 for op, sz, _ in rcases]
treat_cyc = [statistics.median([r["cycle_ns"] for r in rate_g[(op, sz, "card_bar4")]]) / 1000.0 for op, sz, _ in rcases]

ax1.bar(xr - wr / 2, ctrl_cyc, width=wr, color=C_CTRL, label="对照组：写主机内存")
ax1.bar(xr + wr / 2, treat_cyc, width=wr, color=C_TREAT, label="实验组：直写卡侧 BAR4")

for i, (op, sz, _) in enumerate(rcases):
    hc = [r["cycle_ns"] / 1000.0 for r in rate_g[(op, sz, "host_memzone")]]
    tc = [r["cycle_ns"] / 1000.0 for r in rate_g[(op, sz, "card_bar4")]]
    ax1.scatter([xr[i] - wr / 2] * len(hc), hc, color="#111827", s=16, zorder=4)
    ax1.scatter([xr[i] + wr / 2] * len(tc), tc, color="#042f2e", s=16, zorder=4)

    pct = (treat_cyc[i] - ctrl_cyc[i]) / ctrl_cyc[i] * 100.0
    ax1.text(xr[i] - wr / 2, ctrl_cyc[i] + 0.08, f"{ctrl_cyc[i]:.2f} µs",
             ha="center", va="bottom", fontsize=9.0, color="#374151")
    ax1.text(xr[i] + wr / 2 + 0.05, treat_cyc[i] + 0.08, f"{treat_cyc[i]:.2f} µs\n({pct:.0f}%)",
             ha="center", va="bottom", fontsize=9.0, fontweight="bold", color=C_TREAT)

ax1.set_xticks(xr)
ax1.set_xticklabels([c[2] for c in rcases], fontsize=9.8)
ax1.set_xlim(-0.55, 1.6)
ax1.set_ylabel("完整一轮耗时 (µs，越低越好)", fontsize=9.8)
ax1.set_ylim(0, 3.85)
ax1.set_title("单笔串行完整周期（发任务→等完成→清零）", fontsize=10.2, fontweight="bold")
ax1.legend(loc="upper left", fontsize=8.5, frameon=True, facecolor="white", framealpha=0.95)

# --- Right: W=1 submit->comp p50 & p99 across NOP, 64B, 4KiB, 64KiB ---
cases = [("nop", 0, "NOP"), ("memmove", 64, "64 B"), ("memmove", 4096, "4 KiB"), ("memmove", 65536, "64 KiB")]
x = np.arange(len(cases))
w = 0.34

ctrl_p50 = [statistics.median([r["p50_ns"] for r in lat_g[(op, sz, "host_memzone")]]) / 1000.0 for op, sz, _ in cases]
ctrl_p99 = [statistics.median([r["p99_ns"] for r in lat_g[(op, sz, "host_memzone")]]) / 1000.0 for op, sz, _ in cases]
treat_p50 = [statistics.median([r["p50_ns"] for r in lat_g[(op, sz, "card_bar4")]]) / 1000.0 for op, sz, _ in cases]
treat_p99 = [statistics.median([r["p99_ns"] for r in lat_g[(op, sz, "card_bar4")]]) / 1000.0 for op, sz, _ in cases]

ax2.bar(x - w / 2, ctrl_p50, width=w, color=C_CTRL, label="对照组 p50（中位数）")
ax2.bar(x - w / 2, np.array(ctrl_p99) - np.array(ctrl_p50), bottom=ctrl_p50, width=w,
        color=C_CTRL_LIGHT, edgecolor=C_CTRL, hatch="///", linewidth=0.8, label="对照组 p99 尾部")
ax2.bar(x + w / 2, treat_p50, width=w, color=C_TREAT, label="实验组 p50（中位数）")
ax2.bar(x + w / 2, np.array(treat_p99) - np.array(treat_p50), bottom=treat_p50, width=w,
        color=C_TREAT_LIGHT, edgecolor=C_TREAT, hatch="///", linewidth=0.8, label="实验组 p99 尾部")

for i in range(len(cases)):
    ax2.text(x[i] - w / 2, ctrl_p99[i] + 0.09, f"{ctrl_p50[i]:.2f}\n({ctrl_p99[i]:.2f})",
             ha="center", va="bottom", fontsize=8.5, color="#374151")
    ax2.text(x[i] + w / 2, treat_p99[i] + 0.09, f"{treat_p50[i]:.2f}\n({treat_p99[i]:.2f})",
             ha="center", va="bottom", fontsize=8.5, fontweight="bold", color=C_TREAT)

ax2.annotate("消掉 1.21 µs 读回跳变\np99 降 1.05 µs (−32%)",
             xy=(2 - w / 2, 2.78), xytext=(0.45, 3.85),
             arrowprops=dict(arrowstyle="->", color=C_TREAT, lw=1.3),
             fontsize=8.8, fontweight="bold", color=C_TREAT,
             bbox=dict(boxstyle="round,pad=0.3", fc="#f0fdfa", ec=C_TREAT, lw=0.9))

ax2.set_xticks(x)
ax2.set_xticklabels([c[2] for c in cases], fontsize=9.8)
ax2.set_ylabel("单次提交→完成延迟 p50 / (p99) (µs)", fontsize=9.8)
ax2.set_ylim(0, 5.75)
ax2.set_title("单次延迟与长尾抖动（斜线为 p99−p50 尾部）", fontsize=10.2, fontweight="bold")
ax2.legend(loc="upper left", fontsize=8.0, frameon=True, facecolor="white", framealpha=0.95)

fig.suptitle(
    "MRV-99 单并发（W=1）延迟对比：仅改完成记录写向（主机内存 vs 卡侧 BAR4），零主机 CPU 转发",
    fontsize=11.0, fontweight="bold", y=0.99,
)

png = save(fig, "mrv-99-w1-latency")
print(f"Saved {png}")
