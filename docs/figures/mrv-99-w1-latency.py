"""Plot MRV-99 W=1 latency (p50/p99 across NOP..1 MiB) and symmetric closed-loop
period 1/rate(W=1) comparing Host Memzone comp (Control) vs Card PF BAR4 slot 15
comp (Treatment). Reads raw data from logs/mrv99-2026-11-19/runs.jsonl.
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

fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(13.6, 5.1), gridspec_kw={"width_ratios": [1.45, 0.52, 1.05]})

C_CTRL = "#6b7280"
C_CTRL_LIGHT = "#d1d5db"
C_TREAT = "#0f766e"
C_TREAT_LIGHT = "#99f6e4"

# --- Panel 1: W=1 submit->comp p50 & p99 across NOP, 64B, 4KiB, 64KiB ---
cases = [("nop", 0, "NOP"), ("memmove", 64, "64 B"), ("memmove", 4096, "4 KiB"), ("memmove", 65536, "64 KiB")]
x = np.arange(len(cases))
w = 0.34

ctrl_p50 = [statistics.median([r["p50_ns"] for r in lat_g[(op, sz, "host_memzone")]]) / 1000.0 for op, sz, _ in cases]
ctrl_p99 = [statistics.median([r["p99_ns"] for r in lat_g[(op, sz, "host_memzone")]]) / 1000.0 for op, sz, _ in cases]
treat_p50 = [statistics.median([r["p50_ns"] for r in lat_g[(op, sz, "card_bar4")]]) / 1000.0 for op, sz, _ in cases]
treat_p99 = [statistics.median([r["p99_ns"] for r in lat_g[(op, sz, "card_bar4")]]) / 1000.0 for op, sz, _ in cases]

ax1.bar(x - w / 2, ctrl_p50, width=w, color=C_CTRL, label="对照组 主机 Memzone (p50)")
ax1.bar(x - w / 2, np.array(ctrl_p99) - np.array(ctrl_p50), bottom=ctrl_p50, width=w,
        color=C_CTRL_LIGHT, edgecolor=C_CTRL, hatch="///", linewidth=0.8, label="对照组 p99 尾部")
ax1.bar(x + w / 2, treat_p50, width=w, color=C_TREAT, label="实验组 卡 BAR4 槽 15 (p50)")
ax1.bar(x + w / 2, np.array(treat_p99) - np.array(treat_p50), bottom=treat_p50, width=w,
        color=C_TREAT_LIGHT, edgecolor=C_TREAT, hatch="///", linewidth=0.8, label="实验组 p99 尾部")

for i in range(len(cases)):
    ax1.text(x[i] - w / 2, ctrl_p99[i] + 0.08, f"{ctrl_p50[i]:.2f}\n({ctrl_p99[i]:.2f})",
             ha="center", va="bottom", fontsize=8.3, color="#374151")
    ax1.text(x[i] + w / 2, treat_p99[i] + 0.08, f"{treat_p50[i]:.2f}\n({treat_p99[i]:.2f})",
             ha="center", va="bottom", fontsize=8.3, fontweight="bold", color=C_TREAT)

ax1.annotate("消掉 1.21 µs DPI 轮询跳变\np99 −1.05 µs (−32%)",
             xy=(2 - w / 2 - 0.14, 2.85), xytext=(0.28, 3.65),
             arrowprops=dict(arrowstyle="->", color=C_TREAT, lw=1.3),
             fontsize=8.3, fontweight="bold", color=C_TREAT,
             bbox=dict(boxstyle="round,pad=0.28", fc="#f0fdfa", ec=C_TREAT, lw=0.9))

ax1.set_xticks(x)
ax1.set_xticklabels([c[2] for c in cases], fontsize=9.8)
ax1.set_ylabel("单次延迟 p50 / (p99) (µs)", fontsize=9.8)
ax1.set_ylim(0, 5.75)
ax1.set_title("w1_lat 提交→完成 (不含对照 t0 前 ~0.83 µs 清零)", fontsize=9.6, fontweight="bold")
ax1.legend(loc="upper left", fontsize=7.9, frameon=True, facecolor="white", framealpha=0.95)

# --- Panel 2: 1 MiB W=1 submit->comp p50 & p99 (300 rounds x 3 reps) ---
c1m_p50 = statistics.median([r["p50_ns"] for r in lat_g[("memmove", 1048576, "host_memzone")]]) / 1000.0
c1m_p99 = statistics.median([r["p99_ns"] for r in lat_g[("memmove", 1048576, "host_memzone")]]) / 1000.0
t1m_p50 = statistics.median([r["p50_ns"] for r in lat_g[("memmove", 1048576, "card_bar4")]]) / 1000.0
t1m_p99 = statistics.median([r["p99_ns"] for r in lat_g[("memmove", 1048576, "card_bar4")]]) / 1000.0

x1m = np.array([0.0])
w1m = 0.34
ax2.bar(x1m - w1m / 2, [c1m_p50], width=w1m, color=C_CTRL)
ax2.bar(x1m - w1m / 2, [c1m_p99 - c1m_p50], bottom=[c1m_p50], width=w1m,
        color=C_CTRL_LIGHT, edgecolor=C_CTRL, hatch="///", linewidth=0.8)
ax2.bar(x1m + w1m / 2, [t1m_p50], width=w1m, color=C_TREAT)
ax2.bar(x1m + w1m / 2, [t1m_p99 - t1m_p50], bottom=[t1m_p50], width=w1m,
        color=C_TREAT_LIGHT, edgecolor=C_TREAT, hatch="///", linewidth=0.8)

ax2.text(-w1m / 2, c1m_p99 + 0.8, f"{c1m_p50:.2f}\n({c1m_p99:.2f})", ha="center", va="bottom", fontsize=8.3, color="#374151")
ax2.text(w1m / 2, t1m_p99 + 0.8, f"{t1m_p50:.2f}\n({t1m_p99:.2f})", ha="center", va="bottom", fontsize=8.3, fontweight="bold", color=C_TREAT)
ax2.text(0.0, 21.0, "单笔边际搬运\n4K→64K: 30.8 GB/s\n64K→1M: 31.9 GB/s",
         ha="center", va="center", fontsize=7.8, fontweight="bold", color=C_TREAT,
         bbox=dict(boxstyle="round,pad=0.28", fc="#f0fdfa", ec=C_TREAT, lw=0.8))

ax2.set_xticks([0.0])
ax2.set_xticklabels(["1 MiB\n(300 轮×3)"], fontsize=9.5)
ax2.set_xlim(-0.52, 0.52)
ax2.set_ylim(0, 50.0)
ax2.set_ylabel("单次延迟 (µs)", fontsize=9.5)
ax2.set_title("1 MiB 组", fontsize=9.6, fontweight="bold")

# --- Panel 3: Symmetric W=1 closed-loop period 1/rate(W=1) ---
rcases = [("nop", 0, "NOP\n(W=1 闭环)"), ("memmove", 4096, "4 KiB MEMMOVE\n(W=1 闭环)")]
xr = np.arange(len(rcases))
wr = 0.34

ctrl_cyc = [statistics.median([r["cycle_ns"] for r in rate_g[(op, sz, "host_memzone")]]) / 1000.0 for op, sz, _ in rcases]
treat_cyc = [statistics.median([r["cycle_ns"] for r in rate_g[(op, sz, "card_bar4")]]) / 1000.0 for op, sz, _ in rcases]

ax3.bar(xr - wr / 2, ctrl_cyc, width=wr, color=C_CTRL, label="对照组 主机 Memzone")
ax3.bar(xr + wr / 2, treat_cyc, width=wr, color=C_TREAT, label="实验组 卡 BAR4 槽 15")

for i, (op, sz, _) in enumerate(rcases):
    hc = [r["cycle_ns"] / 1000.0 for r in rate_g[(op, sz, "host_memzone")]]
    bc = [r["cycle_ns"] / 1000.0 for r in rate_g[(op, sz, "card_bar4")]]
    ax3.scatter([xr[i] - wr / 2] * len(hc), hc, color="#111827", s=18, zorder=5)
    ax3.scatter([xr[i] + wr / 2] * len(bc), bc, color="#042f2e", s=18, zorder=5)
    pct = (treat_cyc[i] - ctrl_cyc[i]) / ctrl_cyc[i] * 100.0
    diff_us = treat_cyc[i] - ctrl_cyc[i]
    ax3.text(xr[i] - wr / 2, ctrl_cyc[i] + 0.10, f"{ctrl_cyc[i]:.2f} µs",
             ha="center", va="bottom", fontsize=9.0, color="#374151")
    ax3.text(xr[i] + wr / 2 + 0.04, treat_cyc[i] + 0.12, f"{treat_cyc[i]:.2f} µs\n({diff_us:+.2f} µs, {pct:.0f}%)",
             ha="center", va="bottom", fontsize=8.3, fontweight="bold", color=C_TREAT)

ax3.set_xticks(xr)
ax3.set_xticklabels([c[2] for c in rcases], fontsize=9.8)
ax3.set_xlim(-0.55, 1.65)
ax3.set_ylabel("闭环周期 1 / rate(W=1) (µs)", fontsize=9.8)
ax3.set_ylim(0, 3.85)
ax3.set_title("对称口径：闭环周期 1/rate (含清零，1.5 s×3)", fontsize=9.6, fontweight="bold")
ax3.legend(loc="upper left", fontsize=8.2, frameon=True, facecolor="white", framealpha=0.95)

fig.suptitle(
    "MRV-99：仅改 completion_addr（主机 Memzone vs 卡 BAR4 槽 15），卡 DPI 提交 · dsa2 1 引擎 · CC=0 · 零主机转发",
    fontsize=10.6,
    fontweight="bold",
)

png = save(fig, "mrv-99-w1-latency")
print(f"Saved {png}")
