"""MRV-117 Figure 1: Pure-size single-variable crossover (64 B -> 8 KiB) at W=1, W=16,
and W=32 small-packet slow-cliff disclosure (part1_size_w32) from runs.jsonl.
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
C_WARN = "#DC2626"

fig, (ax1, ax2, ax3) = plt.subplots(
    1, 3, figsize=(17.6, 5.9), gridspec_kw={"width_ratios": [1.0, 1.15, 0.85]}
)

# ---- Panel A: W=1 unloaded p50 RTT (us) ----
for tier, label, color, marker, ls, dx in [
    ("sdp", "(a) 传统 SDP (MTU 9000)", C_SDP, "s", "-", -0.06),
    ("push", "(b) 纯 MOVDIR64B + WC", C_PUSH, "^", "--", 0.0),
    ("directpush", "(c) DirectPush (≤256B 直推 + ≥1KiB DPI)", C_DP, "o", "-", 0.06),
]:
    meds = []
    for i, s in enumerate(SIZES):
        rs = by_cfg[f"{tier}_{s}_w1"]
        vals = [r["lat_all_us"]["p50"] for r in rs]
        meds.append(statistics.median(vals))
        ax1.scatter([X[i] + dx] * len(vals), vals, color=color, alpha=0.45, s=22, zorder=4)
    ax1.plot(X, meds, color=color, marker=marker, lw=2.2, ms=6.5, ls=ls, label=label, zorder=5)

for idx, s in [(0, "64"), (3, "4096")]:
    vals = [r["lat_all_us"]["p50"] for r in by_cfg[f"sdp_rpm_{s}_w1"]]
    ax1.scatter([X[idx]] * len(vals), vals, color=C_CTRL, marker="D", s=36, facecolors="none", lw=1.4,
                label="对照: SDP rx_poll_mem=1" if idx == 0 else None, zorder=6)

dp64_w1 = statistics.median([r["lat_all_us"]["p50"] for r in by_cfg["directpush_64_w1"]])
sdp64_w1 = statistics.median([r["lat_all_us"]["p50"] for r in by_cfg["sdp_64_w1"]])
rpm64_w1 = statistics.median([r["lat_all_us"]["p50"] for r in by_cfg["sdp_rpm_64_w1"]])
dp4k_w1 = statistics.median([r["lat_all_us"]["p50"] for r in by_cfg["directpush_4096_w1"]])
sdp4k_w1 = statistics.median([r["lat_all_us"]["p50"] for r in by_cfg["sdp_4096_w1"]])

ax1.annotate(
    f"64 B: {dp64_w1:.3f} vs {sdp64_w1:.3f} µs\n(降 {sdp64_w1/dp64_w1:.2f}×；比 rpm 降 {rpm64_w1/dp64_w1:.2f}×)",
    xy=(0, dp64_w1), xytext=(0.08, 3.05),
    fontsize=8.5, fontweight="bold", color=C_DP,
    arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.3),
    bbox=dict(boxstyle="round,pad=0.28", fc="#EEF4FF", ec=C_DP, alpha=0.96),
)
ax1.annotate(
    f"W=1 串行 DMA 往返\n({dp4k_w1:.2f} µs 仍快于 SDP {sdp4k_w1:.2f} µs)",
    xy=(3, dp4k_w1), xytext=(1.45, 4.45),
    fontsize=8.2, color="#1E3A8A",
    arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.2),
    bbox=dict(boxstyle="round,pad=0.24", fc="white", ec="#93B4FD", alpha=0.96),
)

ax1.set_xticks(X)
ax1.set_xticklabels(SIZE_LABELS, fontsize=10)
ax1.set_ylabel("空载往返延迟 p50 (µs，W = 1)", fontsize=10.5)
ax1.set_xlabel("单条 RPC 载荷大小（128 MiB 留存池）", fontsize=10)
ax1.set_title("A. 空载延迟（W = 1）：小包降 5.6×", fontsize=11, fontweight="bold", pad=10)
ax1.set_ylim(0, 8.3)
ax1.legend(loc="upper left", fontsize=7.9, frameon=True, framealpha=0.95)

# ---- Panel B: W=16 bidirectional payload GB/s ----
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
        ax2.scatter([X[i] + dx] * len(vals), vals, color=color, alpha=0.55, s=24, zorder=4)
    ax2.plot(X, meds, color=color, marker=marker, lw=2.2, ms=6.5, ls=ls, label=label, zorder=5)

for idx, cfg, lbl, mk in [
    (3, "sdp_zc_4096_w16", "对照: SDP 零拷贝 (跳过 memcpy)", "P"),
    (4, "sdp_zc_8192_w16", None, "P"),
    (0, "sdp_rpm_64_w16", "对照: SDP rx_poll_mem=1", "D"),
    (3, "sdp_rpm_4096_w16", None, "D"),
]:
    vals = [r["bidir_gbps"] for r in by_cfg[cfg]]
    ax2.scatter([X[idx] - 0.14] * len(vals), vals, color=C_CTRL, marker=mk, s=42, facecolors="none", lw=1.35,
                label=lbl, zorder=6)

ax2.axvspan(1.38, 1.62, color="#DBEAFE", alpha=0.55, zorder=1)
ax2.text(1.50, 12.6, "并发交叉点\n(256B→1KiB)", ha="center", va="center", fontsize=8.2, fontweight="bold", color="#1E40AF",
         bbox=dict(boxstyle="round,pad=0.22", fc="#EFF6FF", ec="#93B4FD", alpha=0.95))

push1k = statistics.median([r["mreq_s"] for r in by_cfg["push_1024_w16"]])
push1k_gb = statistics.median([r["bidir_gbps"] for r in by_cfg["push_1024_w16"]])
dp1k = statistics.median([r["mreq_s"] for r in by_cfg["directpush_1024_w16"]])
dp4k_gb = statistics.median([r["bidir_gbps"] for r in by_cfg["directpush_4096_w16"]])
dp4k_mr = statistics.median([r["mreq_s"] for r in by_cfg["directpush_4096_w16"]])
sdp8k_zc = statistics.median([r["bidir_gbps"] for r in by_cfg["sdp_zc_8192_w16"]])

ax2.annotate(
    f"1 KiB 纯直推坠入桥慢态\n{push1k:.2f} M/s vs DMA {dp1k:.2f} M/s ({dp1k/push1k:.1f}×)",
    xy=(2, push1k_gb), xytext=(0.02, 7.6),
    fontsize=8.1, color="#B45309", fontweight="bold",
    arrowprops=dict(arrowstyle="->", color=C_PUSH, lw=1.25),
    bbox=dict(boxstyle="round,pad=0.24", fc="#FFFBEB", ec=C_PUSH, alpha=0.96),
)
ax2.annotate(
    f"4 KiB: {dp4k_gb:.2f} GB/s ({dp4k_mr:.2f} M/s)\nLLC缺 0.00 · 空轮询 97%",
    xy=(3, dp4k_gb), xytext=(1.68, 20.1),
    fontsize=8.1, color=C_DP, fontweight="bold",
    arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.25),
    bbox=dict(boxstyle="round,pad=0.24", fc="#EEF4FF", ec=C_DP, alpha=0.96),
)
ax2.annotate(
    "8 KiB SDP/zc 满核跑满\n(空轮询 0–3% vs DP 99%)",
    xy=(4, sdp8k_zc), xytext=(2.68, 4.8),
    fontsize=7.7, color="#374151",
    arrowprops=dict(arrowstyle="->", color="#6B7280", lw=1.0),
    bbox=dict(boxstyle="round,pad=0.22", fc="#F9FAFB", ec="#9CA3AF", alpha=0.96),
)
ax2.annotate(
    "256 B 双稳态\n(rep2=3.9 GB/s)",
    xy=(1.05, 3.90), xytext=(0.02, 4.4),
    fontsize=7.7, color="#4B5563",
    arrowprops=dict(arrowstyle="->", color="#6B7280", lw=1.0),
)

ax2.set_xticks(X)
ax2.set_xticklabels(SIZE_LABELS, fontsize=10)
ax2.set_ylabel("双向有效载荷吞吐 (GB/s，W = 16)", fontsize=10.5)
ax2.set_xlabel("单条 RPC 载荷大小（128 MiB 留存池）", fontsize=10)
ax2.set_title("B. 并发吞吐（W = 16）：≥1 KiB 原生 DPI 完胜", fontsize=11, fontweight="bold", pad=10)
ax2.set_ylim(0, 23.2)
ax2.legend(loc="upper left", fontsize=7.6, frameon=True, framealpha=0.95)

# ---- Panel C: W=32 small-packet concurrency & 256 B slow-cliff disclosure (part1_size_w32) ----
W32_SIZES = ["64", "256"]
W32_LABELS = ["64 B\n(1 行/条 · 32 行在途)", "256 B\n(4 行/条 · 128 行在途)"]
X3 = np.arange(len(W32_SIZES))
BW = 0.25

for tier, label, color, off in [
    ("sdp", "(a) 传统 SDP", C_SDP, -BW),
    ("push", "(b) 纯直推", C_PUSH, 0.0),
    ("directpush", "(c) DirectPush (阈值1KiB)", C_DP, +BW),
]:
    meds = []
    p99s = []
    for i, s in enumerate(W32_SIZES):
        rs = by_cfg[f"{tier}_{s}_w32"]
        mreqs = [r["mreq_s"] for r in rs]
        meds.append(statistics.median(mreqs))
        p99s.append(statistics.median([r["lat_all_us"]["p99"] for r in rs]))
        ax3.scatter([X3[i] + off] * len(mreqs), mreqs, color="#111827", s=18, zorder=6, alpha=0.7)
    bars = ax3.bar(X3 + off, meds, width=BW * 0.88, color=color, alpha=0.90, label=label, zorder=3)
    for i, b in enumerate(bars):
        h = b.get_height()
        txt_col = C_WARN if (s := W32_SIZES[i]) == "256" and tier in ("push", "directpush") else (
            color if tier == "directpush" else "#374151"
        )
        ax3.text(
            b.get_x() + b.get_width() / 2, h + 0.35,
            f"{h:.2f}\n({p99s[i]:.1f}µs)",
            ha="center", va="bottom", fontsize=7.4,
            fontweight="bold" if (tier == "directpush" or (s == "256" and tier == "sdp")) else "normal",
            color=txt_col,
        )

sdp256_w32 = statistics.median([r["mreq_s"] for r in by_cfg["sdp_256_w32"]])
dp256_w32 = statistics.median([r["mreq_s"] for r in by_cfg["directpush_256_w32"]])
sdp256_p99 = statistics.median([r["lat_all_us"]["p99"] for r in by_cfg["sdp_256_w32"]])
dp256_p99 = statistics.median([r["lat_all_us"]["p99"] for r in by_cfg["directpush_256_w32"]])
dp64_w32 = statistics.median([r["mreq_s"] for r in by_cfg["directpush_64_w32"]])
sdp64_w32 = statistics.median([r["mreq_s"] for r in by_cfg["sdp_64_w32"]])

# Reference line: 1 KiB DPI DMA W=16 throughput beats unshaped 256 B W=32!
ax3.axhline(dp1k, color="#2563EB", ls=":", lw=1.25, alpha=0.80, zorder=2)
ax3.annotate(
    f"参考: 1KiB DMA W=16 ({dp1k:.2f} M/s)",
    xy=(0.56, dp1k), xytext=(0.94, 7.2),
    fontsize=7.3, color="#1E40AF", ha="center",
    arrowprops=dict(arrowstyle="->", color="#2563EB", lw=0.9),
    bbox=dict(boxstyle="round,pad=0.16", fc="#EFF6FF", ec="#93B4FD", alpha=0.95),
)

ax3.annotate(
    f"64 B: DP 领先 {dp64_w32/sdp64_w32:.2f}×\n({dp64_w32:.2f} vs {sdp64_w32:.2f} M/s)",
    xy=(0 + BW + 0.08, dp64_w32), xytext=(0.66, 17.8),
    fontsize=7.6, fontweight="bold", color=C_DP,
    arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.15),
    bbox=dict(boxstyle="round,pad=0.22", fc="#EEF4FF", ec=C_DP, alpha=0.96),
)
ax3.annotate(
    f"256 B W=32 慢崖反超\n(未接 MRV-115 信用限流)\n"
    f"DP 锁死 {dp256_w32*4:.1f} M行/s ({dp256_w32:.2f}M, p99 {dp256_p99:.1f}µs)\n"
    f"SDP 反超 +{(sdp256_w32/dp256_w32-1)*100:.1f}% ({sdp256_w32:.2f}M, p99 {sdp256_p99:.1f}µs)",
    xy=(1 + BW, dp256_w32 + 1.65), xytext=(0.43, 11.4),
    fontsize=7.3, fontweight="bold", color=C_WARN,
    arrowprops=dict(arrowstyle="->", color=C_WARN, lw=1.25),
    bbox=dict(boxstyle="round,pad=0.24", fc="#FEF2F2", ec=C_WARN, alpha=0.97),
)

ax3.set_xticks(X3)
ax3.set_xticklabels(W32_LABELS, fontsize=9.3)
ax3.set_ylabel("吞吐 (Mreq/s，W = 32；柱顶括号为 p99)", fontsize=10)
ax3.set_xlabel("小包高并发实测（part1_size_w32 全点）", fontsize=9.8)
ax3.set_title("C. W = 32 小包与 256 B 慢崖反超披露", fontsize=11, fontweight="bold", pad=10)
ax3.set_xlim(-0.52, 1.62)
ax3.set_ylim(0, 22.0)
ax3.legend(loc="upper left", fontsize=7.6, frameon=True, framealpha=0.95)

fig.suptitle(
    "MRV-117 纯尺寸单变量交叉点与 W=32 慢崖披露（1 主机核 cpu40 3.90 GHz + 1 卡核 2.00 GHz · 128 MiB 留存池 · 零 DSA · n=3）",
    fontsize=11.8, fontweight="bold"
)
out = mrvplot.save(fig, "mrv-117-size-crossover")
print("Saved:", out)
