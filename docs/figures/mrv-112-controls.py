#!/usr/bin/env python3
"""MRV-112 Figure 2: Confounder Isolation Controls
(Matched ring_cells=1020/992, --pad-next-128 control-line isolation,
W=1 unloaded wire RTT parity, and WC2 W=64 RTT p50 vs p99 bistability signature).
"""
import collections
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import mrvplot  # noqa: E402

RUNS = ROOT / "logs/mrv112-2026-11-22/runs.jsonl"
recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
by_key = collections.defaultdict(list)
for r in recs:
    by_key[(r["exp"], r["cond"])].append(r)


def med(exp, cond, fn):
    return statistics.median(fn(r) for r in by_key[(exp, cond)])


STRIDES = [64, 128, 4096, 4224]
X_LABELS = ["64 B", "128 B", "4096 B", "4224 B\n(反别名)"]
xs = np.arange(len(STRIDES))

fig, axes = plt.subplots(1, 2, figsize=(13.6, 4.9))

# ---- Panel A: Throughput across 4 control regimes ----
ax = axes[0]
w = 0.19
regimes = [
    ("core_wc2_w64", lambda s: f"wc2_w64_s{s}", "主实验：满环 (~4 MiB)", "#dc2626"),
    ("ctrl_matched_c", lambda s: f"wc2_w64_s{s}_c{992 if s == 4224 else 1020}", "等环长对照 (c=1020/992)", "#f59e0b"),
    ("ctrl_padnext", lambda s: f"wc2_w64_pn_s{s}", "隔离 next_offset 128 B", "#2563eb"),
    ("ctrl_wc4_w64", lambda s: f"wc4_w64_s{s}", "4 卡核 WC4 W=64", "#0f766e"),
]
ax.axhspan(12.5, 15.6, color="#fee2e2", alpha=0.45, zorder=0)
ax.axhline(34.96, color="#64748b", ls="-.", lw=1.1, alpha=0.85)
ax.text(0.02, 35.6, "快态参考：WC1 W=64 = 35.0 M/s", fontsize=7.8, color="#475569")

offsets = [-1.5 * w, -0.5 * w, 0.5 * w, 1.5 * w]
for off, (exp, cfn, lbl, col) in zip(offsets, regimes):
    ys = [med(exp, cfn(s), lambda r: r["mops"]) for s in STRIDES]
    bars = ax.bar(xs + off, ys, w, color=col, alpha=0.86, label=lbl, zorder=3)
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.35, f"{h:.1f}", ha="center", va="bottom", fontsize=7.1, color="#1e293b")

ax.set_xticks(xs)
ax.set_xticklabels(X_LABELS, fontsize=8.8)
ax.set_ylim(0, 39.0)
ax.set_ylabel("闭环吞吐 (M msg/s)")
ax.set_title("A. 混杂因素排查：环长、next 伪共享与 4 核锁定")
ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.89), fontsize=7.8, frameon=True, facecolor="white", ncol=2)

# ---- Panel B: RTT p50 vs p99 across W=1, WC2 W=64, WC4 W=64 ----
ax = axes[1]
w1_p50 = [med("ctrl_w1", f"wc2_w1_s{s}", lambda r: r["rtt_p50_us"]) for s in STRIDES]
w2_p50 = [med("core_wc2_w64", f"wc2_w64_s{s}", lambda r: r["rtt_p50_us"]) for s in STRIDES]
w2_p99 = [med("core_wc2_w64", f"wc2_w64_s{s}", lambda r: r["rtt_p99_us"]) for s in STRIDES]
w4_p50 = [med("ctrl_wc4_w64", f"wc4_w64_s{s}", lambda r: r["rtt_p50_us"]) for s in STRIDES]
w4_p99 = [med("ctrl_wc4_w64", f"wc4_w64_s{s}", lambda r: r["rtt_p99_us"]) for s in STRIDES]

ax.plot(xs, w2_p99, "-s", color="#991b1b", lw=2.2, ms=6.2, label="WC2 W=64 RTT p99（慢态上界）")
ax.plot(xs, w2_p50, "--o", color="#dc2626", lw=2.2, ms=6.2, label="WC2 W=64 RTT p50")
ax.plot(xs, w4_p99, "-^", color="#065f46", lw=1.8, ms=5.5, alpha=0.85, label="WC4 W=64 RTT p99")
ax.plot(xs, w4_p50, ":v", color="#0f766e", lw=2.0, ms=5.8, label="WC4 W=64 RTT p50")
ax.plot(xs, w1_p50, "-d", color="#475569", lw=1.8, ms=5.5, label="空载基线 W=1 RTT p50（零开销）")

for i in range(len(STRIDES)):
    ax.annotate(f"{w2_p99[i]:.2f}", (i, w2_p99[i]), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=7.8, fontweight="bold", color="#991b1b")
    ax.annotate(f"{w2_p50[i]:.2f}", (i, w2_p50[i]), xytext=(-12 if i == 2 else 0, -14), textcoords="offset points", ha="center", fontsize=7.8, color="#dc2626")
    ax.annotate(f"{w1_p50[i]:.2f}", (i, w1_p50[i]), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=7.6, color="#475569")

ax.annotate(
    "4096 B p50 降至 2.7 µs\n但 p99 仍锁在 4.66 µs 慢态！",
    xy=(2, w2_p99[2]),
    xytext=(0.75, 5.42),
    fontsize=7.8,
    color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.25", fc="#fff5f5", ec="#fca5a5"),
    arrowprops=dict(arrowstyle="->", color="#991b1b", lw=1.0),
)

ax.set_xticks(xs)
ax.set_xticklabels(X_LABELS, fontsize=8.8)
ax.set_ylim(0.3, 6.25)
ax.set_ylabel("端到端闭环 RTT (µs)")
ax.set_title("B. 延迟证据：W=1 空载一致，W=64 尾延迟全线锁死 ~4.7–4.9 µs")
ax.legend(loc="center left", bbox_to_anchor=(0.01, 0.38), fontsize=7.7, frameon=True, facecolor="white")

fig.suptitle(
    "MRV-112 对照检验：等环长、隔离控制行 next_offset、4 卡核与 W=1 空载延迟",
    fontsize=11.0,
    fontweight="bold",
)
out = mrvplot.save(fig, "mrv-112-controls")
print(f"Saved {out}")
