#!/usr/bin/env python3
"""MRV-112 Figure 2: Confounder Isolation Controls, Batch-Head RTT Bias & Cache Capacity Attribution
Plots directly from logs/mrv112-2026-11-22/runs.jsonl and stamps/sparse_wc4_w64_s4096.csv:
  Panel A: Throughput across 4 regimes (honestly marking ctrl_padnext as ineffective due to empty-poll flush)
  Panel B: RTT p50 / p99 across W=1, WC2 W=64, WC4 W=64 + per-packet CSV RTT p50 for WC4 4096 B (3.90 vs 3.49 us)
  Panel C (C1/C2): Card L2 refill & LLrd attribution separating ring-capacity shrink (c=65472->992) from stride
"""
import collections
import csv
import json
import pathlib
import statistics
import sys

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import mrvplot  # noqa: E402

LOG_DIR = ROOT / "logs/mrv112-2026-11-22"
RUNS = LOG_DIR / "runs.jsonl"
recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
by_key = collections.defaultdict(list)
for r in recs:
    by_key[(r["exp"], r["cond"])].append(r)


def med(exp, cond, fn):
    return statistics.median(fn(r) for r in by_key[(exp, cond)])


def l2_per_msg(r):
    return sum(c["l2d_cache_refill"] for c in r["card_pmu"]["cores"]) / (r["rounds"] + r["warmup"])


def llrd_per_msg(r):
    return sum(c["ll_cache_rd"] for c in r["card_pmu"]["cores"]) / (r["rounds"] + r["warmup"])


def csv_per_pkt_rtt_p50_us(r):
    p = ROOT / r["stamp_csv"]
    with p.open() as f:
        hdr = f.readline().strip()
        parts = dict(tok.split("=") for tok in hdr.lstrip("# ").split())
        tsc_hz = float(parts["tsc_hz"])
        rows = list(csv.DictReader(f))
    ns_h = 1e9 / tsc_hz
    rtts = [(float(x["seen_tsc"]) - float(x["send_tsc"])) * ns_h / 1000.0 for x in rows]
    return statistics.median(rtts)


STRIDES = [64, 128, 4096, 4224]
X_LABELS = ["64 B", "128 B", "4096 B", "4224 B\n(反别名)"]
xs = np.arange(len(STRIDES))

fig = plt.figure(figsize=(17.2, 5.5))
gs = gridspec.GridSpec(2, 3, width_ratios=[1.15, 1.15, 1.0], height_ratios=[1, 1], figure=fig)

# ---- Panel A: Throughput across 4 control regimes ----
ax = fig.add_subplot(gs[:, 0])
w = 0.19
regimes = [
    ("core_wc2_w64", lambda s: f"wc2_w64_s{s}", "主实验：满环 (~4 MiB)", "#dc2626", None),
    ("ctrl_matched_c", lambda s: f"wc2_w64_s{s}_c{992 if s == 4224 else 1020}", "等环长对照 (c=1020/992)", "#f59e0b", None),
    ("ctrl_padnext", lambda s: f"wc2_w64_pn_s{s}", "pad-next (空轮询回写未生效)", "#94a3b8", "///"),
    ("ctrl_wc4_w64", lambda s: f"wc4_w64_s{s}", "4 卡核 WC4 W=64", "#0f766e", None),
]
ax.axhspan(12.5, 15.6, color="#fee2e2", alpha=0.45, zorder=0)
ax.axhline(34.96, color="#64748b", ls="-.", lw=1.1, alpha=0.85)
ax.text(0.02, 35.6, "快态参考：WC1 W=64 (64 B) = 35.0 M/s", fontsize=7.6, color="#475569")

offsets = [-1.5 * w, -0.5 * w, 0.5 * w, 1.5 * w]
for off, (exp, cfn, lbl, col, hatch) in zip(offsets, regimes):
    ys = [med(exp, cfn(s), lambda r: r["mops"]) for s in STRIDES]
    bars = ax.bar(xs + off, ys, w, color=col, alpha=0.86, label=lbl, hatch=hatch,
                  edgecolor="#475569" if hatch else None, lw=0.6 if hatch else 0, zorder=3)
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.35, f"{h:.1f}", ha="center", va="bottom", fontsize=6.9, color="#1e293b")

ax.set_xticks(xs)
ax.set_xticklabels(X_LABELS, fontsize=8.4)
ax.set_ylim(0, 39.2)
ax.set_ylabel("闭环吞吐 (M msg/s)")
ax.set_title("A. 混杂因素排查：等环长、pad-next 实态与 4 核锁定")
ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.89), fontsize=7.1, frameon=True, facecolor="white", ncol=1)

# ---- Panel B: RTT p50 vs p99 + WC4 4096 B per-packet CSV p50 ----
ax = fig.add_subplot(gs[:, 1])
w1_p50 = [med("ctrl_w1", f"wc2_w1_s{s}", lambda r: r["rtt_p50_us"]) for s in STRIDES]
w2_p50 = [med("core_wc2_w64", f"wc2_w64_s{s}", lambda r: r["rtt_p50_us"]) for s in STRIDES]
w2_p99 = [med("core_wc2_w64", f"wc2_w64_s{s}", lambda r: r["rtt_p99_us"]) for s in STRIDES]
w4_p50 = [med("ctrl_wc4_w64", f"wc4_w64_s{s}", lambda r: r["rtt_p50_us"]) for s in STRIDES]
w4_p99 = [med("ctrl_wc4_w64", f"wc4_w64_s{s}", lambda r: r["rtt_p99_us"]) for s in STRIDES]

r85 = by_key[("stamp_wc4", "wc4_w64_s4096_sp")][0]
wc4_4096_csv_p50 = csv_per_pkt_rtt_p50_us(r85)

ax.plot(xs, w2_p99, "-s", color="#991b1b", lw=2.1, ms=6.0, label="WC2 W=64 RTT p99（慢态上界）")
ax.plot(xs, w2_p50, "--o", color="#dc2626", lw=2.1, ms=6.0, label="WC2 W=64 RTT p50")
ax.plot(xs, w4_p99, "-^", color="#065f46", lw=1.7, ms=5.3, alpha=0.85, label="WC4 W=64 RTT p99")
ax.plot(xs, w4_p50, ":v", color="#0f766e", lw=2.0, ms=5.6, label="WC4 W=64 RTT p50（批首计时）")
ax.scatter(2, wc4_4096_csv_p50, marker="D", s=64, color="#2563eb", edgecolor="white", lw=0.7, zorder=6,
           label=f"WC4 4096B 逐包 CSV p50 ({wc4_4096_csv_p50:.2f} µs)")
ax.plot(xs, w1_p50, "-d", color="#475569", lw=1.7, ms=5.3, label="空载基线 W=1 RTT p50（零开销）")

for i in range(len(STRIDES)):
    ax.annotate(f"{w2_p99[i]:.2f}", (i, w2_p99[i]), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=7.6, fontweight="bold", color="#991b1b")
    ax.annotate(f"{w2_p50[i]:.2f}", (i, w2_p50[i]), xytext=(-12 if i == 2 else 0, -14), textcoords="offset points", ha="center", fontsize=7.5, color="#dc2626")
    ax.annotate(f"{w1_p50[i]:.2f}", (i, w1_p50[i]), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=7.4, color="#475569")

ax.annotate(f"{w4_p50[2]:.2f}", (2, w4_p50[2]), xytext=(-16, -3), textcoords="offset points", ha="right", fontsize=7.4, fontweight="bold", color="#0f766e")
ax.annotate(f"{wc4_4096_csv_p50:.2f}", (2, wc4_4096_csv_p50), xytext=(8, -3), textcoords="offset points", ha="left", fontsize=7.4, fontweight="bold", color="#1d4ed8")

ax.annotate(
    "4096 B p99 全线锁 4.66–4.72 µs",
    xy=(2, w2_p99[2]),
    xytext=(0.55, 5.52),
    fontsize=7.3,
    color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.22", fc="#fff5f5", ec="#fca5a5"),
    arrowprops=dict(arrowstyle="->", color="#991b1b", lw=0.9),
)
ax.annotate(
    f"WC4 4096B 批首 {w4_p50[2]:.2f} vs 逐包 {wc4_4096_csv_p50:.2f} µs：\n"
    "乱序减半→收包批 8→15，压 W_eff + 批首偏置 ~0.4 µs",
    xy=(2, w4_p50[2]),
    xytext=(0.05, 1.24),
    fontsize=6.7,
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.22", fc="#eff6ff", ec="#93c5fd"),
    arrowprops=dict(arrowstyle="->", color="#2563eb", lw=0.9),
)

ax.set_xticks(xs)
ax.set_xticklabels(X_LABELS, fontsize=8.4)
ax.set_ylim(0.3, 6.25)
ax.set_ylabel("端到端闭环 RTT (µs)")
ax.set_title("B. 延迟证据与 4096 B 批首计时偏置")
ax.legend(loc="center left", bbox_to_anchor=(0.01, 0.43), fontsize=6.4, frameon=True, facecolor="white")

# ---- Panel C1 & C2: Cache counter attribution (stride vs 64x ring capacity shrink) ----
cache_series = [
    ("core_wc2_w64", lambda s: f"wc2_w64_s{s}", "满环 WC2 (c=65472→992)", "#dc2626", "o", "-"),
    ("ctrl_matched_c", lambda s: f"wc2_w64_s{s}_c{992 if s == 4224 else 1020}", "等环长 WC2 (c=1020/992)", "#f59e0b", "s", "--"),
    ("ctrl_wc4_w64", lambda s: f"wc4_w64_s{s}", "满环 WC4 (4 核)", "#0f766e", "^", ":"),
    ("ctrl_w1", lambda s: f"wc2_w1_s{s}", "空载 W=1", "#64748b", "d", "-."),
]

ax_l2 = fig.add_subplot(gs[0, 2])
for exp, cfn, lbl, col, mk, ls in cache_series:
    ys = [med(exp, cfn(s), l2_per_msg) for s in STRIDES]
    ax_l2.plot(xs, ys, ls=ls, marker=mk, color=col, lw=1.8, ms=4.8, label=lbl)
l2_full = [med("core_wc2_w64", f"wc2_w64_s{s}", l2_per_msg) for s in STRIDES]
l2_mc = [med("ctrl_matched_c", f"wc2_w64_s{s}_c{992 if s == 4224 else 1020}", l2_per_msg) for s in STRIDES]
l2_mc_drop = (l2_mc[1] - l2_mc[0]) / l2_mc[0] * 100.0
ax_l2.annotate(f"等环长 64→128 B\n仅降 {l2_mc_drop:.1f}% (满环 -29.6%)", xy=(1, l2_mc[1]), xytext=(1.32, 1.48),
               fontsize=6.5, color="#b45309", bbox=dict(boxstyle="round,pad=0.18", fc="#fffbeb", ec="#fcd34d"),
               arrowprops=dict(arrowstyle="->", color="#d97706", lw=0.8))
ax_l2.set_xticks(xs)
ax_l2.set_xticklabels(["", "", "", ""])
ax_l2.set_xlim(-0.30, 3.22)
ax_l2.set_ylim(1.35, 3.35)
ax_l2.set_ylabel("L2 缺失 (次/条)", fontsize=8.2)
ax_l2.set_title("C. 缓存归因剥离：步长 vs 64× 环容量缩减", fontsize=9.6)
ax_l2.legend(loc="upper right", fontsize=6.2, frameon=True, facecolor="white", ncol=1)

ax_ll = fig.add_subplot(gs[1, 2])
for exp, cfn, lbl, col, mk, ls in cache_series:
    ys = [med(exp, cfn(s), llrd_per_msg) for s in STRIDES]
    ax_ll.plot(xs, ys, ls=ls, marker=mk, color=col, lw=1.8, ms=4.8, label=lbl)
ll_full64 = med("core_wc2_w64", "wc2_w64_s64", llrd_per_msg)
ll_mc64 = med("ctrl_matched_c", "wc2_w64_s64_c1020", llrd_per_msg)
ll_shrink_pct = (ll_mc64 - ll_full64) / ll_full64 * 100.0
ax_ll.annotate("", xy=(-0.14, ll_mc64), xytext=(-0.14, ll_full64),
               arrowprops=dict(arrowstyle="<->", color="#b45309", lw=1.2))
ax_ll.annotate(f"仅缩环 c 降 {ll_shrink_pct:.1f}%\n(占总降幅 76%)", xy=(-0.10, (ll_full64 + ll_mc64) / 2),
               xytext=(0.72, 1.80), fontsize=6.5, color="#b45309",
               bbox=dict(boxstyle="round,pad=0.18", fc="#fffbeb", ec="#fcd34d"),
               arrowprops=dict(arrowstyle="->", color="#d97706", lw=0.8))
ax_ll.set_xticks(xs)
ax_ll.set_xticklabels(X_LABELS, fontsize=8.0)
ax_ll.set_xlim(-0.30, 3.22)
ax_ll.set_ylim(1.65, 3.65)
ax_ll.set_ylabel("LLC 读 LLrd (次/条)", fontsize=8.2)

fig.suptitle(
    "MRV-112 对照检验：混杂因素排查、4096 B 批首计时偏置与缓存容量效应剥离",
    fontsize=10.8,
    fontweight="bold",
)
out = mrvplot.save(fig, "mrv-112-controls")
print(f"Saved {out}")
