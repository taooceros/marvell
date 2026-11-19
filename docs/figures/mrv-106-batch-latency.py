"""MRV-106 Figure 2: W=1 single-batch latency (p50 & p99) vs B and
throughput-vs-latency Pareto frontier (W=64 GB/s vs W=1 p50).
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import mrvplot

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs" / "mrv106-2026-11-19" / "runs.jsonl"

rows = [json.loads(line) for line in RUNS.read_text().splitlines() if line.strip()]
lat_rows = [r for r in rows if r["mode"] == "w1_lat"]
rate_rows = [r for r in rows if r["mode"] == "rate"]

by_lat = defaultdict(list)
for r in lat_rows:
    assert r["bad"] == 0 and r["dyn_lost"] == 0 and r["dyn_bad"] == 0
    by_lat[(r["comp_target"], r["batch_size"])].append(r)

by_rate = defaultdict(list)
for r in rate_rows:
    assert r["lost"] == 0 and r["bad"] == 0 and r["dyn_lost"] == 0 and r["dyn_bad"] == 0
    by_rate[(r["comp_target"], r["batch_size"])].append(r)

bs_bar4 = [1, 2, 4, 8, 16, 32]
bs_host = [1, 16]

bar4_p50 = [statistics.median(r["p50_ns"] for r in by_lat[("card_bar4", b)]) for b in bs_bar4]
bar4_p99 = [statistics.median(r["p99_ns"] for r in by_lat[("card_bar4", b)]) for b in bs_bar4]
bar4_gbs = [statistics.median(r["gb_s"] for r in by_rate[("card_bar4", b)]) for b in bs_bar4]

host_p50 = [statistics.median(r["p50_ns"] for r in by_lat[("host_memzone", b)]) for b in bs_host]
host_p99 = [statistics.median(r["p99_ns"] for r in by_lat[("host_memzone", b)]) for b in bs_host]
host_gbs = [statistics.median(r["gb_s"] for r in by_rate[("host_memzone", b)]) for b in bs_host]

ACCENT = "#1f6feb"
REF = "#57606a"
ORANGE = "#d97706"

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.7))

# --- Left panel: W=1 single-batch p50 & p99 latency vs B ---
ax1.plot(bs_bar4, [v / 1000.0 for v in bar4_p50], color=ACCENT, marker="o", lw=2.2, ms=6.5,
         label="CardBar4 p50")
ax1.plot(bs_bar4, [v / 1000.0 for v in bar4_p99], color=ACCENT, marker="^", lw=1.4, ls="--", ms=5.5, alpha=0.8,
         label="CardBar4 p99 (p99−p50 ≈ 0.20 µs)")
ax1.scatter(bs_host, [v / 1000.0 for v in host_p50], color=REF, marker="s", s=56, zorder=5,
            label="HostMemzone p50（不含 t0 前 ~0.84 µs 清零）")
ax1.scatter(bs_host, [v / 1000.0 for v in host_p99], color=ORANGE, marker="D", s=52, zorder=5,
            label="HostMemzone p99（p99−p50 ≈ 1.12 µs）")

for b, p50, p99 in zip(bs_host, host_p50, host_p99):
    ax1.plot([b, b], [p50 / 1000.0, p99 / 1000.0], color=ORANGE, lw=1.8, alpha=0.75, zorder=4)

lat_ann = {
    1: (bar4_p50[0], (4, -18), "left"),
    4: (bar4_p50[2], (14, -14), "left"),
    16: (bar4_p50[4], (0, -18), "center"),
    32: (bar4_p50[5], (-8, -18), "center"),
}
for b, (p50, (dx, dy), ha) in lat_ann.items():
    ax1.annotate(f"{p50/1000:.2f} µs", (b, p50 / 1000.0), xytext=(dx, dy),
                 textcoords="offset points", ha=ha, fontsize=8.8, fontweight="bold", color=ACCENT)

ax1.annotate(f"Host p99: {host_p99[1]/1000:.2f} µs", (16, host_p99[1] / 1000.0), xytext=(0, 10),
             textcoords="offset points", ha="center", va="bottom", fontsize=8.4, color=ORANGE, fontweight="bold")

ax1.set_xlabel("DSA Batch 聚合度 B（单批 4 KiB 拷贝数）", fontsize=9.5)
ax1.set_ylabel("单批往返延迟 (W=1, µs)", fontsize=9.5)
ax1.set_xlim(-1, 35)
ax1.set_ylim(1.5, 7.6)
ax1.set_xticks(bs_bar4)
ax1.set_title("单批延迟：CardBar4 紧尾；Batch 取指 +0.33 µs，斜率 ~136 ns/4K", fontsize=9.8, fontweight="bold")
ax1.legend(loc="upper left", fontsize=7.6, frameon=True, framealpha=0.92)

# --- Right panel: Throughput (W=64) vs Latency (W=1 p50) Pareto ---
ax2.plot([v / 1000.0 for v in bar4_p50], bar4_gbs, color=ACCENT, marker="o", lw=2.2, ms=7, zorder=4,
         label="CardBar4 (B = 1 → 32)")
ax2.scatter([v / 1000.0 for v in host_p50], host_gbs, color=REF, marker="s", s=62, zorder=5,
            label="HostMemzone 对照 (B = 1, 16)")

ax2.scatter([bar4_p50[2] / 1000.0], [bar4_gbs[2]], color="#1a7f37", s=160, facecolors="none", lw=2.2, zorder=6)
ax2.annotate("甜点 B=4\n31.1 GB/s · 2.84 µs", (bar4_p50[2] / 1000.0, bar4_gbs[2]),
             xytext=(16, -30), textcoords="offset points", ha="left", fontsize=8.8, fontweight="bold",
             color="#1a7f37", arrowprops=dict(arrowstyle="->", color="#1a7f37", lw=1.2))

label_pos = {1: (18, -3), 2: (18, -3), 8: (0, 9), 16: (0, 9), 32: (-4, -16)}
for b, p50, gbs in zip(bs_bar4, bar4_p50, bar4_gbs):
    if b == 4:
        continue
    dx, dy = label_pos[b]
    ax2.annotate(f"B={b}", (p50 / 1000.0, gbs), xytext=(dx, dy),
                 textcoords="offset points", ha="center", fontsize=8.8, color=ACCENT, fontweight="bold")

ax2.annotate("Host B=1\n30.0 GB/s", (host_p50[0] / 1000.0, host_gbs[0]), xytext=(2, 10),
             textcoords="offset points", ha="center", va="bottom", fontsize=8.3, color=REF)
ax2.annotate("Host B=16\n31.1 GB/s", (host_p50[1] / 1000.0, host_gbs[1]), xytext=(8, -24),
             textcoords="offset points", ha="left", fontsize=8.3, color=REF)

ax2.set_xlabel("单批往返中位延迟 W=1 p50 (µs，越左越低)", fontsize=9.5)
ax2.set_ylabel("持续吞吐 W=64 (GB/s，越上越高)", fontsize=9.5)
ax2.set_xlim(1.7, 7.2)
ax2.set_ylim(13, 35.0)
ax2.set_title("吞吐–延迟权衡：B=4 仅 +0.76 µs 即达 99.4% 满带", fontsize=10.2, fontweight="bold")
ax2.legend(loc="lower right", fontsize=8.2, frameon=True, framealpha=0.92)

fig.suptitle("MRV-106：DSA Batch 单批延迟与吞吐–延迟权衡（dsa2/wq2.0，512 KiB 热工作集，CC=0，3 次交替中位数）",
             fontsize=10.4, fontweight="bold", y=0.98)
out = mrvplot.save(fig, "mrv-106-batch-latency")
print("Saved:", out)
