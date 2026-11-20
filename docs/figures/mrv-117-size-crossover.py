"""MRV-117 Figure 1: Pure-size single-variable crossover (64 B -> 8 KiB) at W=1 and W=16.
Plots directly from logs/mrv117-2026-11-20/runs.jsonl using mrvplot.py.
"""
import collections
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import mrvplot

RUNS = Path(__file__).resolve().parents[2] / "logs/mrv117-2026-11-20/runs.jsonl"
rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
by_cfg = collections.defaultdict(list)
for r in rows:
    if r["part"] != "part0_cal":
        assert r["verified"], f"unverified run: {r['cfg']}"
        by_cfg[r["cfg"]].append(r)

SIZES = ["64", "256", "1024", "4096", "8192"]
SIZE_LABELS = ["64 B", "256 B", "1 KiB", "4 KiB", "8 KiB"]
X = np.arange(len(SIZES))

C_SDP = "#6B7280"
C_PUSH = "#D97706"
C_DP = "#0F62FE"
C_CTRL = "#374151"

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.4, 5.9))

# ---- Left panel: W=1 unloaded p50 RTT (us) ----
for tier, label, color, marker, ls, dx in [
    ("sdp", "(a) 传统 SDP (MTU 9000)", C_SDP, "s", "-", -0.06),
    ("push", "(b) 纯 MOVDIR64B + WC", C_PUSH, "^", "--", 0.0),
    ("directpush", "(c) DirectPush (≤256B 直推 + ≥1KiB DPI DMA)", C_DP, "o", "-", 0.06),
]:
    meds = []
    for i, s in enumerate(SIZES):
        rs = by_cfg[f"{tier}_{s}_w1"]
        vals = [r["lat_all_us"]["p50"] for r in rs]
        meds.append(statistics.median(vals))
        ax1.scatter([X[i] + dx] * len(vals), vals, color=color, alpha=0.45, s=24, zorder=4)
    ax1.plot(X, meds, color=color, marker=marker, lw=2.3, ms=7, ls=ls, label=label, zorder=5)

for idx, s in [(0, "64"), (3, "4096")]:
    vals = [r["lat_all_us"]["p50"] for r in by_cfg[f"sdp_rpm_{s}_w1"]]
    ax1.scatter([X[idx]] * len(vals), vals, color=C_CTRL, marker="D", s=38, facecolors="none", lw=1.4,
                label="对照: SDP rx_poll_mem=1" if idx == 0 else None, zorder=6)

ax1.annotate("64 B: 0.885 vs 4.955 µs\n(降 5.60×；比 rpm 降 5.21×)",
             xy=(0, 0.885), xytext=(0.08, 3.05),
             fontsize=9.1, fontweight="bold", color=C_DP,
             arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.4),
             bbox=dict(boxstyle="round,pad=0.3", fc="#EEF4FF", ec=C_DP, alpha=0.96))
ax1.annotate("W=1 串行 DMA 往返\n(3.39 µs 仍快于 SDP 6.34 µs)",
             xy=(3, 3.385), xytext=(1.65, 4.35),
             fontsize=8.8, color="#1E3A8A",
             arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.2),
             bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#93B4FD", alpha=0.96))

ax1.set_xticks(X)
ax1.set_xticklabels(SIZE_LABELS, fontsize=10.5)
ax1.set_ylabel("空载往返延迟 p50 (µs，W = 1)", fontsize=11)
ax1.set_xlabel("单条 RPC 载荷大小（128 MiB 留存池）", fontsize=10.5)
ax1.set_title("A. 空载延迟（W = 1）：小包降 5.6×，大包快 1.7–1.9×", fontsize=11.3, fontweight="bold", pad=10)
ax1.set_ylim(0, 8.3)
ax1.legend(loc="upper left", fontsize=8.6, frameon=True, framealpha=0.95)

# ---- Right panel: W=16 bidirectional payload GB/s ----
for tier, label, color, marker, ls, dx in [
    ("sdp", "(a) 传统 SDP (MTU 9000)", C_SDP, "s", "-", -0.07),
    ("push", "(b) 纯 MOVDIR64B + WC", C_PUSH, "^", "--", 0.0),
    ("directpush", "(c) DirectPush + 原生 DPI DMA", C_DP, "o", "-", 0.07),
]:
    meds = []
    for i, s in enumerate(SIZES):
        rs = by_cfg[f"{tier}_{s}_w16"]
        vals = [r["bidir_gbps"] for r in rs]
        meds.append(statistics.median(vals))
        ax2.scatter([X[i] + dx] * len(vals), vals, color=color, alpha=0.55, s=28, zorder=4)
    ax2.plot(X, meds, color=color, marker=marker, lw=2.3, ms=7, ls=ls, label=label, zorder=5)

for idx, s, cfg, lbl, mk in [
    (3, "4096", "sdp_zc_4096_w16", "对照: SDP 零拷贝 (跳过 memcpy)", "P"),
    (4, "8192", "sdp_zc_8192_w16", None, "P"),
    (3, "4096", "sdp_rpm_4096_w16", "对照: SDP rx_poll_mem=1", "D"),
]:
    vals = [r["bidir_gbps"] for r in by_cfg[cfg]]
    ax2.scatter([X[idx] - 0.13] * len(vals), vals, color=C_CTRL, marker=mk, s=46, facecolors="none", lw=1.4,
                label=lbl, zorder=6)

ax2.axvspan(1.38, 1.62, color="#DBEAFE", alpha=0.55, zorder=1)
ax2.text(1.50, 13.6, "并发交叉点\n(256B→1KiB)", ha="center", va="center", fontsize=8.5, fontweight="bold", color="#1E40AF",
         bbox=dict(boxstyle="round,pad=0.22", fc="#EFF6FF", ec="#93B4FD", alpha=0.95))

ax2.annotate("1 KiB 纯直推坠入桥慢态\n0.94 M/s vs DMA 3.92 M/s (4.2×)",
             xy=(2, 1.93), xytext=(0.02, 8.6),
             fontsize=8.5, color="#B45309", fontweight="bold",
             arrowprops=dict(arrowstyle="->", color=C_PUSH, lw=1.3),
             bbox=dict(boxstyle="round,pad=0.25", fc="#FFFBEB", ec=C_PUSH, alpha=0.96))
ax2.annotate("4 KiB: 18.83 GB/s (2.30 M/s)\nLLC 缺失 0.00/req · 空轮询 97%",
             xy=(3, 18.83), xytext=(1.75, 19.6),
             fontsize=8.5, color=C_DP, fontweight="bold",
             arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.3),
             bbox=dict(boxstyle="round,pad=0.25", fc="#EEF4FF", ec=C_DP, alpha=0.96))
ax2.annotate("256 B 双稳态\n(rep2=3.9 GB/s)",
             xy=(1.05, 3.90), xytext=(0.02, 4.8),
             fontsize=8.1, color="#4B5563",
             arrowprops=dict(arrowstyle="->", color="#6B7280", lw=1.0))

ax2.set_xticks(X)
ax2.set_xticklabels(SIZE_LABELS, fontsize=10.5)
ax2.set_ylabel("双向有效载荷吞吐 (GB/s，W = 16)", fontsize=11)
ax2.set_xlabel("单条 RPC 载荷大小（128 MiB 留存池）", fontsize=10.5)
ax2.set_title("B. 并发吞吐（W = 16）：≥1 KiB 原生 DPI DMA 完胜直推与 SDP", fontsize=11.3, fontweight="bold", pad=10)
ax2.set_ylim(0, 23.0)
ax2.legend(loc="upper left", fontsize=8.2, frameon=True, framealpha=0.95)

fig.suptitle(
    "MRV-117 纯尺寸单变量交叉点扫描（1 主机核 cpu40 3.90 GHz + 1 卡核 2.00 GHz · 128 MiB 留存池 · 零 DSA · n=3 交错全点）",
    fontsize=11.8, fontweight="bold"
)
out = mrvplot.save(fig, "mrv-117-size-crossover")
print("Saved:", out)
