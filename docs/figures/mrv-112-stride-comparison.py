#!/usr/bin/env python3
"""MRV-112 Figure 1: Core Single-Variable BAR4 Request-Ring Stride Comparison
(64 B / 128 B / 4096 B + 4224 B anti-aliasing control), PMU Mechanism Evidence,
and Clock-Synced Cross-Core Arrival Spacing (Δcard_t_in).
"""
import collections
import csv
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

LOG_DIR = ROOT / "logs/mrv112-2026-11-22"
RUNS = LOG_DIR / "runs.jsonl"
STAMP_DIR = LOG_DIR / "stamps"

recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
by_key = collections.defaultdict(list)
for r in recs:
    by_key[(r["exp"], r["cond"])].append(r)


def med(exp, cond, fn):
    return statistics.median(fn(r) for r in by_key[(exp, cond)])


def tad_occ(r):
    return r["card_pmu"]["tad"]["tad_req_occ"]["val"] / (r["card_pmu"]["wall_s"] * 2.0e9)


def snp_per_msg(r):
    return r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / (r["rounds"] + r["warmup"])


def l2_per_msg(r):
    return sum(c["l2d_cache_refill"] for c in r["card_pmu"]["cores"]) / (r["rounds"] + r["warmup"])


def llrd_per_msg(r):
    return sum(c["ll_cache_rd"] for c in r["card_pmu"]["cores"]) / (r["rounds"] + r["warmup"])


STRIDES = [64, 128, 4096, 4224]
X_LABELS = ["64 B\n(基线紧排)", "128 B\n(跨 128 B 块)", "4096 B\n(跨 4 KiB 页)", "4224 B\n(4096+128 反别名)"]
xs = np.arange(len(STRIDES))

fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.2))

# ---- Panel A: Throughput across strides (WC2 W=64 core + WC2 W=128 + WC4 W=64) ----
ax = axes[0]
series = [
    ("core_wc2_w64", lambda s: f"wc2_w64_s{s}", "WC2 W=64（主实验，n=5）", "#dc2626", "o", "-"),
    ("ctrl_wc2_w128", lambda s: f"wc2_w128_s{s}", "WC2 W=128（深窗对照，n=3）", "#2563eb", "s", "--"),
    ("ctrl_wc4_w64", lambda s: f"wc4_w64_s{s}", "WC4 W=64（4 卡核对照，n=3）", "#0f766e", "^", ":"),
]
ax.axhspan(12.2, 15.6, color="#fee2e2", alpha=0.55, zorder=0)
ax.axhline(34.96, color="#475569", ls="-.", lw=1.2, alpha=0.85)
ax.text(0.05, 35.6, "MRV-75 快态参考：WC1 W=64 = 35.0 M/s（TAD 在途 1.05 条）", fontsize=7.6, color="#334155")

for exp, cond_fn, lbl, col, mk, ls in series:
    ys = [med(exp, cond_fn(s), lambda r: r["mops"]) for s in STRIDES]
    ax.plot(xs, ys, ls=ls, marker=mk, color=col, lw=2.2, ms=6.5, label=lbl, zorder=3)
    for i, s in enumerate(STRIDES):
        for r in by_key[(exp, cond_fn(s))]:
            ax.scatter(i, r["mops"], color=col, s=18, alpha=0.4, zorder=2)

# Annotate WC2 W=64 and WC4 W=64 medians cleanly
wc2_ys = [med("core_wc2_w64", f"wc2_w64_s{s}", lambda r: r["mops"]) for s in STRIDES]
wc4_ys = [med("ctrl_wc4_w64", f"wc4_w64_s{s}", lambda r: r["mops"]) for s in STRIDES]
for i, (y2, y4) in enumerate(zip(wc2_ys, wc4_ys)):
    dy = 8 if i != 2 else 9
    ax.annotate(f"{y2:.2f}", (i, y2), xytext=(0, dy), textcoords="offset points", ha="center", fontsize=8.3, fontweight="bold", color="#b91c1c")
    ax.annotate(f"{y4:.2f}", (i, y4), xytext=(0, -14), textcoords="offset points", ha="center", fontsize=7.8, color="#0f766e")

ax.annotate(
    "4096 B 偶发快态翻转\n(p99 仍锁 4.66 µs)；\n反别名 4224 B 落回 15.0 M/s",
    xy=(2, wc2_ys[2]),
    xytext=(1.72, 24.4),
    fontsize=7.6,
    color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.25", fc="#fff5f5", ec="#fca5a5"),
    arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.0),
)

ax.set_xticks(xs)
ax.set_xticklabels(X_LABELS, fontsize=8.3)
ax.set_ylim(8, 39.8)
ax.set_ylabel("闭环吞吐 (M msg/s)")
ax.set_title("A. 闭环吞吐：跨 128 B 与跨 4 KiB 页均未破慢态")
ax.legend(loc="upper left", bbox_to_anchor=(0.01, 0.84), fontsize=7.5, frameon=True, facecolor="white")

# ---- Panel B: Card PMU evidence (L2 / LLrd drop, yet TAD in-flight stays starved!) ----
ax = axes[1]
l2_vals = [med("core_wc2_w64", f"wc2_w64_s{s}", l2_per_msg) for s in STRIDES]
ll_vals = [med("core_wc2_w64", f"wc2_w64_s{s}", llrd_per_msg) for s in STRIDES]
snp_vals = [med("core_wc2_w64", f"wc2_w64_s{s}", snp_per_msg) for s in STRIDES]
tad_vals = [med("core_wc2_w64", f"wc2_w64_s{s}", tad_occ) for s in STRIDES]

w = 0.25
b1 = ax.bar(xs - w, l2_vals, w, color="#3b82f6", alpha=0.85, label="卡核 L2 缺失 (次/条)")
b2 = ax.bar(xs, ll_vals, w, color="#64748b", alpha=0.85, label="卡核 LLC 读 (LLrd/条)")
b3 = ax.bar(xs + w, snp_vals, w, color="#f59e0b", alpha=0.85, label="卡 TAD 嗅探 (次/条)")

for bars, fmt in [(b1, "{:.2f}"), (b2, "{:.2f}"), (b3, "{:.2f}")]:
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.06, fmt.format(h), ha="center", va="bottom", fontsize=7.0, color="#1e293b")

ax.set_xticks(xs)
ax.set_xticklabels(X_LABELS, fontsize=8.3)
ax.set_ylim(0, 5.25)
ax.set_ylabel("每条消息卡侧 PMU 计数 (次/条)")

ax2 = ax.twinx()
ax2.spines["top"].set_visible(False)
l_tad, = ax2.plot(xs, tad_vals, "-D", color="#dc2626", lw=2.2, ms=6.5, label="卡 TAD 在途并发 (条，右轴)")
ax2.axhline(1.05, color="#dc2626", ls="--", lw=1.0, alpha=0.6)
ax2.text(1.98, 1.09, "快态 TAD > 1.0 条", ha="center", fontsize=7.6, color="#991b1b",
         bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
for i, tv in enumerate(tad_vals):
    ax2.annotate(f"{tv:.2f}", (i, tv), xytext=(0, 7), textcoords="offset points", ha="center", fontsize=7.8, fontweight="bold", color="#b91c1c")
ax2.set_ylim(0, 1.56)
ax2.set_ylabel("卡 TAD 在途并发 tad_req_occ (条)", color="#dc2626")
ax2.tick_params(axis="y", labelcolor="#dc2626")
ax2.grid(False)

h1, l1 = ax.get_legend_handles_labels()
h2, l2_lbl = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2_lbl, loc="upper right", fontsize=7.3, frameon=True, facecolor="white")
ax.set_title("B. WC2 W=64 PMU：L2/LLrd 确降，但 TAD 仍空转")

# ---- Panel C: Cross-core arrival spacing Δcard_t_in (sorted within 8-msg sparse bursts) ----
ax = axes[2]
stamp_groups = [
    ("stamp_wc2", "wc2_w64_s64_sp", "WC2 64B", "#dc2626"),
    ("stamp_wc2", "wc2_w64_s128_sp", "WC2 128B", "#ea580c"),
    ("stamp_wc2", "wc2_w64_s4096_sp", "WC2 4096B", "#f59e0b"),
    ("stamp_wc2", "wc2_w64_s4224_sp", "WC2 4224B", "#b91c1c"),
    ("stamp_wc4", "wc4_w64_s64_sp", "WC4 64B", "#0f766e"),
    ("stamp_wc4", "wc4_w64_s128_sp", "WC4 128B", "#0d9488"),
    ("stamp_wc4", "wc4_w64_s4096_sp", "WC4 4096B", "#14b8a6"),
    ("stamp_wc4", "wc4_w64_s4224_sp", "WC4 4224B", "#047857"),
]
cx = np.arange(len(stamp_groups))
p50s, p05s, p90s, h2cs = [], [], [], []
for exp, cond, _, _ in stamp_groups:
    r = by_key[(exp, cond)][0]
    ss = r["stamp_summary"]
    p50s.append(ss["dcin_sorted_p50_ns"])
    p05s.append(ss["dcin_sorted_p05_ns"])
    p90s.append(ss["dcin_sorted_p90_ns"])
    h2cs.append(ss["h2c_p50_ns"] / 1000.0)

ax.axhspan(70, 80, color="#fee2e2", alpha=0.55, zorder=0)
ax.axhline(76.4, color="#991b1b", ls="--", lw=1.1, alpha=0.8)
ax.text(5.45, 134.0, "红带：MRV-75 串行节拍 76.4 ns", ha="center", fontsize=7.5, color="#991b1b",
        bbox=dict(boxstyle="round,pad=0.22", fc="#fff5f5", ec="#fca5a5"))

for i, (_, _, lbl, col) in enumerate(stamp_groups):
    ax.vlines(i, p05s[i], p90s[i], color=col, lw=2.0, alpha=0.55, zorder=2)
    ax.scatter(i, p50s[i], color=col, s=55, zorder=3)
    ax.text(i, p90s[i] + 3.0, f"{p50s[i]:.0f}ns\n({h2cs[i]:.1f}µs)", ha="center", va="bottom", fontsize=7.0, color="#1e293b")

ax.axvline(3.5, color="#94a3b8", ls=":", lw=1.2)
ax.set_xticks(cx)
ax.set_xticklabels([g[2] for g in stamp_groups], rotation=28, ha="right", fontsize=7.8)
ax.set_ylim(0, 152)
ax.set_ylabel("跨核到达间隔 Δcard_t_in (ns，p05–p90 与中位数)")
ax.set_title("C. 对时到达节拍（括号为去程 h2c 中位数）")

fig.suptitle(
    "MRV-112：BAR4 请求环步长单变量对比（MOVDIR64B + WC，64 B，cpu40 3.9 GHz，uncore 2.2 GHz，卡 2.0 GHz，PMU 100% 无复用）",
    fontsize=11.0,
    fontweight="bold",
)
out = mrvplot.save(fig, "mrv-112-stride-comparison")
print(f"Saved {out}")
