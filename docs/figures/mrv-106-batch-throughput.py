"""MRV-106 Figure 1: Sustained throughput (GB/s) and effective cycle time (ns / 4 KiB)
vs DSA Batch aggregation B at fixed total in-flight W = 64 4 KiB MEMMOVE ops.
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
rate_rows = [r for r in rows if r["mode"] == "rate"]

by_key = defaultdict(list)
for r in rate_rows:
    assert r["lost"] == 0 and r["bad"] == 0 and r["dyn_lost"] == 0 and r["dyn_bad"] == 0
    assert "mismatches=0 " in r["host_verify"] and "mismatches=0 " in r["dyn_verify"]
    by_key[(r["comp_target"], r["batch_size"])].append(r)

bs_bar4 = [1, 2, 4, 8, 16, 32]
x_idx = {b: i for i, b in enumerate(bs_bar4)}

bar4_gbs_med = [statistics.median(r["gb_s"] for r in by_key[("card_bar4", b)]) for b in bs_bar4]
bar4_cyc_med = [statistics.median(r["cycle_ns"] for r in by_key[("card_bar4", b)]) for b in bs_bar4]

bs_host = [1, 16]
host_gbs_med = [statistics.median(r["gb_s"] for r in by_key[("host_memzone", b)]) for b in bs_host]
host_cyc_med = [statistics.median(r["cycle_ns"] for r in by_key[("host_memzone", b)]) for b in bs_host]

ACCENT = "#1f6feb"
REF = "#57606a"
WARN = "#cf222e"
GREEN = "#1a7f37"

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.7))

# --- Left panel: Throughput (GB/s) ---
xs = [x_idx[b] for b in bs_bar4]
ax1.axhline(host_gbs_med[1], color=REF, ls="--", lw=1.2, alpha=0.75,
            label=f"HostMemzone B=16 参考 ({host_gbs_med[1]:.1f} GB/s)")
ax1.axhline(26.0, color=GREEN, ls=":", lw=1.2, alpha=0.75, label="MRV-106 成立判据 (26 GB/s)")

for b in bs_bar4:
    vals = [r["gb_s"] for r in by_key[("card_bar4", b)]]
    ax1.scatter([x_idx[b]] * len(vals), vals, color=ACCENT, alpha=0.35, s=26, zorder=3)
ax1.plot(xs, bar4_gbs_med, color=ACCENT, marker="o", lw=2.4, ms=7, zorder=4,
         label="CardBar4 反向 P2P (DSA Batch)")

for b in bs_host:
    vals = [r["gb_s"] for r in by_key[("host_memzone", b)]]
    ax1.scatter([x_idx[b]] * len(vals), vals, color=REF, alpha=0.35, s=24, marker="s", zorder=3)
ax1.scatter([x_idx[b] for b in bs_host], host_gbs_med, color=REF, marker="s", s=58, zorder=5,
            label="HostMemzone 对照 (DPI 读回)")

for b, y in zip(bs_bar4, bar4_gbs_med):
    ax1.annotate(f"{y:.1f}", (x_idx[b], y), xytext=(0, 9),
                 textcoords="offset points", ha="center", va="bottom",
                 fontsize=9.5, fontweight="bold", color=ACCENT)
ax1.annotate(f"对照 {host_gbs_med[0]:.1f}", (x_idx[1], host_gbs_med[0]), xytext=(10, -14),
             textcoords="offset points", ha="left", fontsize=8.5, color=REF)
ax1.annotate(f"对照 {host_gbs_med[1]:.1f}", (x_idx[16], host_gbs_med[1]), xytext=(0, -16),
             textcoords="offset points", ha="center", fontsize=8.5, color=REF)

ax1.set_xticks(xs)
ax1.set_xticklabels([f"B={b}\n({64//b}批)" for b in bs_bar4], fontsize=9)
ax1.set_xlabel("DSA Batch 聚合度 B（括号内为在途批数 W/B，总在途 W=64）", fontsize=9.5)
ax1.set_ylabel("单引擎吞吐 (GB/s，十进制 10^9 B/s)", fontsize=9.5)
ax1.set_ylim(10, 36.5)
ax1.set_title("持续吞吐：B=4 即达 512 KiB 热源单引擎上限 (~31.1 GB/s)", fontsize=10.2, fontweight="bold")
ax1.legend(loc="lower right", fontsize=8.2, frameon=True, framealpha=0.92)

# --- Right panel: Cycle time per 4 KiB (ns) ---
naive_cyc = [131.0 + (bar4_cyc_med[0] - 131.0) / b for b in bs_bar4]
ax2.plot(xs, naive_cyc, color=WARN, ls="--", lw=1.4, alpha=0.8,
         label="串行摊薄预测 131 + 137/B（无流水重叠）")
ax2.axhline(host_cyc_med[1], color=REF, ls=":", lw=1.2, alpha=0.75,
            label=f"HostMemzone B=16 ({host_cyc_med[1]:.1f} ns)")

for b in bs_bar4:
    vals = [r["cycle_ns"] for r in by_key[("card_bar4", b)]]
    ax2.scatter([x_idx[b]] * len(vals), vals, color=ACCENT, alpha=0.35, s=26, zorder=3)
ax2.plot(xs, bar4_cyc_med, color=ACCENT, marker="o", lw=2.4, ms=7, zorder=4,
         label="CardBar4 实测周期 (ns / 4 KiB)")
ax2.scatter([x_idx[b] for b in bs_host], host_cyc_med, color=REF, marker="s", s=58, zorder=5,
            label="HostMemzone 实测")

offsets_y2 = {1: 8, 2: -15, 4: -16, 8: -16, 16: -16, 32: 11}
for b, y in zip(bs_bar4, bar4_cyc_med):
    ax2.annotate(f"{y:.1f}", (x_idx[b], y), xytext=(0, offsets_y2[b]),
                 textcoords="offset points", ha="center",
                 va="bottom" if offsets_y2[b] > 0 else "top",
                 fontsize=9.5, fontweight="bold", color=ACCENT)

ax2.set_xticks(xs)
ax2.set_xticklabels([f"B={b}\n({64//b}批)" for b in bs_bar4], fontsize=9)
ax2.set_xlabel("DSA Batch 聚合度 B（固定 W=64 × 4 KiB MEMMOVE, CC=0）", fontsize=9.5)
ax2.set_ylabel("每 4 KiB 有效周期 (ns / 4 KiB)", fontsize=9.5)
ax2.set_ylim(105, 295)
ax2.set_title("周期拆解：B=4 起反向 P2P 泡沫被流水完全隐藏", fontsize=10.2, fontweight="bold")
ax2.legend(loc="upper right", fontsize=8.2, frameon=True, framealpha=0.92)

fig.suptitle("MRV-106：DSA Batch 消除 BAR4 反向 P2P 完成写泡沫（dsa2/wq2.0 单引擎，W=64，512 KiB 热工作集，CC=0）",
             fontsize=10.4, fontweight="bold", y=0.98)

out = mrvplot.save(fig, "mrv-106-batch-throughput")
print("Saved:", out)
