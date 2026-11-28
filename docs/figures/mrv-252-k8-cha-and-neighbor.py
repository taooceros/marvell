#!/usr/bin/env python3
"""MRV-252 Fig 2: K=4 vs over-provisioned K=8 host CHA WCiLF and co-located Socket-1 neighbor impact."""
import json, statistics
from collections import defaultdict
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import mrvplot

mrvplot.style() if hasattr(mrvplot, "style") else None

ROOT = Path(__file__).resolve().parents[2]
ROWS = [json.loads(l) for l in (ROOT / "logs/mrv252-2026-11-28/runs.jsonl").open()]
BY_COND = defaultdict(list)
for r in ROWS:
    assert r["lost"] == 0 and r["bad_or_torn"] == 0 and r.get("card_stats", {}).get("corrupt", 0) == 0
    BY_COND[r["cond"]].append(r)

def med(cond, fn):
    return statistics.median([fn(r) for r in BY_COND[cond]])

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.6), facecolor="white")
fig.suptitle("MRV-252：过配 K=8 的主机 CHA 占用与 Socket 1 邻居实测（6c STREAM + 16 MiB L3, n=3）", fontsize=12.5, fontweight="bold", y=0.98)

# (a) Host CHA WCiLF occupancy: K=4 vs K=8 one-way
conds_a = ["k4_ow_u4_ldpq", "k4_ow_u1_ldpq", "k4_ow_adapt-exp", "k8_ow_u4_ldpq", "k8_ow_adapt-50", "k8_ow_adapt-exp"]
lbls_a = ["K=4\nu4_ldpq", "K=4\nu1_ldpq", "K=4\nadapt-exp", "K=8\nu4_ldpq", "K=8\nadapt-50", "K=8\nadapt-exp"]
cols_a = ["#3498db", "#1f77b4", "#27ae60", "#e74c3c", "#e67e22", "#c0392b"]
cha_vals = [med(c, lambda r: r["host_pmu"]["cha_inflight"]) for c in conds_a]
mops_vals = [med(c, lambda r: r["mops"]) for c in conds_a]

bars = ax1.bar(range(len(conds_a)), cha_vals, color=cols_a, width=0.58)
ax1.axhline(330, color="#c0392b", ls="--", lw=1.2, label="CHA 330 悬崖线")
for i, (cv, mv) in enumerate(zip(cha_vals, mops_vals)):
    ax1.text(i, cv + 10, f"{cv:.0f}\n({mv:.0f}M/s)", ha="center", va="bottom", fontsize=8)
ax1.set_xticks(range(len(conds_a)))
ax1.set_xticklabels(lbls_a, fontsize=8.5)
ax1.set_ylabel("主机 CHA WCiLF 平均在途数")
ax1.set_ylim(0, 520)
ax1.set_title("(a) 单向满载：K=8 无论是否退避均跨过 CHA 330", fontsize=10.5)
ax1.legend(frameon=False, fontsize=8.5, loc="upper left")
ax1.grid(axis="y", alpha=0.3)

# (b) Co-located Socket-1 neighbor: 6c STREAM GB/s & 16 MiB L3 p99 ns
conds_b = ["nb_idle", "nb_k4_ow_u4_ldpq", "nb_k4_ow_adapt-exp", "nb_k8_ow_u4_ldpq", "nb_k8_ow_adapt-exp"]
lbls_b = ["空载基线\n(CHA 0)", "K=4 u4\n(CHA 27)", "K=4 adapt\n(CHA 20)", "K=8 u4\n(CHA 383)", "K=8 adapt\n(CHA 374)"]
str_vals = [med(c, lambda r: r["stream_gbps"]) for c in conds_b]
l3_vals = [med(c, lambda r: r["l3_p99_ns"]) for c in conds_b]
idle_str = str_vals[0]

x = np.arange(len(conds_b))
b1 = ax2.bar(x - 0.18, str_vals, width=0.35, color="#2e86c1", label="6c STREAM 带宽 (GB/s)")
ax2_r = ax2.twinx()
b2 = ax2_r.bar(x + 0.18, l3_vals, width=0.35, color="#e67e22", label="16 MiB L3 p99 (ns)")

for i, (sv, lv) in enumerate(zip(str_vals, l3_vals)):
    pct = (sv / idle_str) * 100.0
    ax2.text(i - 0.18, sv + 0.8, f"{sv:.1f}\n({pct:.0f}%)", ha="center", va="bottom", fontsize=7.5, color="#1b4f72")
    ax2_r.text(i + 0.18, lv + 8, f"{lv:.0f}", ha="center", va="bottom", fontsize=8, color="#a04000")

ax2.set_xticks(x)
ax2.set_xticklabels(lbls_b, fontsize=8.5)
ax2.set_ylabel("6c STREAM Triad 带宽 (GB/s)", color="#1b4f72")
ax2_r.set_ylabel("16 MiB L3 指针追踪 p99 (ns)", color="#a04000")
ax2.set_ylim(0, 62)
ax2_r.set_ylim(0, 600)
ax2.set_title("(b) Socket 1 邻居影响：K=4 零干扰，K=8 带宽 -12% / p99 +61%", fontsize=10.5)
l1, lb1 = ax2.get_legend_handles_labels()
l2, lb2 = ax2_r.get_legend_handles_labels()
ax2.legend(l1 + l2, lb1 + lb2, frameon=False, fontsize=8, loc="upper left")
ax2.grid(axis="y", alpha=0.3)

fig.tight_layout(rect=[0, 0, 1, 0.93])
out_base = ROOT / "docs/figures/mrv-252-k8-cha-and-neighbor"
fig.savefig(f"{out_base}.png", dpi=180)
fig.savefig(f"{out_base}.svg")
print("Wrote", out_base)
