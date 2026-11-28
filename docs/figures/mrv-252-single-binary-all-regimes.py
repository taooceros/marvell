#!/usr/bin/env python3
"""MRV-252 Fig 1: Single-binary four-regime comparison (u4_ldpq vs u1_ldpq vs adapt-50/100/exp)."""
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

MODES = ["u4_ldpq", "u1_ldpq", "adapt-50", "adapt-100", "adapt-exp"]
LABELS = ["static-u4\n(u4_ldpq)", "static-u1q\n(u1_ldpq)", "adapt-50\n(50 ns)", "adapt-100\n(100 ns)", "adapt-exp\n(0→100 ns)"]
COLORS = ["#7f8c8d", "#1f77b4", "#e67e22", "#d35400", "#27ae60"]

fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.6), facecolor="white")
fig.suptitle("MRV-252：MT_NORMAL_NC 单二进制四工况对比（H=1, K=4, n=3 中位数）", fontsize=13, fontweight="bold", y=0.98)

# (a) W=1 RTT p50
ax = axes[0, 0]
vals = [med(f"k4_w1_{m}", lambda r: r["rtt_p50_ns"]) for m in MODES]
bars = ax.bar(range(len(MODES)), vals, color=COLORS, width=0.6)
ax.axhline(915, color="#c0392b", ls="--", lw=1.1, label="915 ns 目标线")
for i, v in enumerate(vals):
    ax.text(i, v + 22, f"{v:.0f}", ha="center", va="bottom", fontsize=9)
ax.set_xticks(range(len(MODES)))
ax.set_xticklabels(LABELS, fontsize=8.5)
ax.set_ylabel("W=1 往返延迟 p50 (ns)")
ax.set_ylim(0, 1450)
ax.set_title("(a) 空载 W=1 往返延迟（u1_ldpq 最快 905 ns）", fontsize=10.5)
ax.legend(frameon=False, fontsize=8.5, loc="upper left")
ax.grid(axis="y", alpha=0.3)

# (b) One-way W=6144 mops & CHA
ax = axes[0, 1]
mops_ow = [med(f"k4_ow_{m}", lambda r: r["mops"]) for m in MODES]
cha_ow = [med(f"k4_ow_{m}", lambda r: r["host_pmu"]["cha_inflight"]) for m in MODES]
bars = ax.bar(range(len(MODES)), mops_ow, color=COLORS, width=0.6)
ax.axhline(158, color="#c0392b", ls="--", lw=1.1, label="158 M/s 目标线")
for i, (m, c) in enumerate(zip(mops_ow, cha_ow)):
    ax.text(i, m + 2.5, f"{m:.1f}\n(CHA {c:.0f})", ha="center", va="bottom", fontsize=8)
ax.set_xticks(range(len(MODES)))
ax.set_xticklabels(LABELS, fontsize=8.5)
ax.set_ylabel("单向满载吞吐 (Mmsg/s)")
ax.set_ylim(0, 195)
ax.set_title("(b) 单向满载 W=6144（五者均保住 ~160 M/s）", fontsize=10.5)
ax.legend(frameon=False, fontsize=8.5, loc="lower right")
ax.grid(axis="y", alpha=0.3)

# (c) Closed-loop W=64/256/512
ax = axes[1, 0]
ws = [64, 256, 512]
x = np.arange(len(ws))
w_bar = 0.15
for idx, (m, lbl, col) in enumerate(zip(MODES, ["static-u4", "static-u1q", "adapt-50", "adapt-100", "adapt-exp"], COLORS)):
    ys = [med(f"k4_cl_w{w}_{m}", lambda r: r["mops"]) for w in ws]
    off = (idx - 2) * w_bar
    ax.bar(x + off, ys, width=w_bar, color=col, label=lbl)
    if m in ("u4_ldpq", "u1_ldpq", "adapt-exp"):
        for xi, y in zip(x + off, ys):
            ax.text(xi, y + 1.2, f"{y:.1f}", ha="center", va="bottom", fontsize=7.5)
ax.set_xticks(x)
ax.set_xticklabels([f"W={w}" for w in ws], fontsize=9.5)
ax.set_ylabel("闭环吞吐 (Mmsg/s)")
ax.set_ylim(0, 74)
ax.set_title("(c) 闭环 W=64/256/512（仅 u1_ldpq 避免 20 M/s 坍缩）", fontsize=10.5)
ax.legend(frameon=False, fontsize=8, ncol=2, loc="upper left")
ax.grid(axis="y", alpha=0.3)

# (d) Closed-loop W=256 TAD req vs EXLMN
ax = axes[1, 1]
tad_req = [med(f"k4_cl_w256_{m}", lambda r: r["card_pmu"]["tad"]["tad_req_msh_in_any"]["val"] / 1e6) for m in MODES]
exlmn = [med(f"k4_cl_w256_{m}", lambda r: r["card_pmu"]["tad"]["tad_snp_msh_out_exlmn"]["val"] / 1e6) for m in MODES]
x = np.arange(len(MODES))
ax.bar(x - 0.18, tad_req, width=0.35, color="#5dade2", label="TAD 总请求 (M/0.8s)")
ax2 = ax.twinx()
ax2.bar(x + 0.18, exlmn, width=0.35, color="#e74c3c", label="EXLMN 冲突 (M/0.8s)")
for i, (tr, ex) in enumerate(zip(tad_req, exlmn)):
    ax.text(i - 0.18, tr + 5, f"{tr:.0f}", ha="center", va="bottom", fontsize=8, color="#2471a3")
    ax2.text(i + 0.18, ex + 0.35, f"{ex:.1f}", ha="center", va="bottom", fontsize=8, color="#c0392b")
ax.set_xticks(x)
ax.set_xticklabels(LABELS, fontsize=8.5)
ax.set_ylabel("TAD 总请求 (M / 0.8 s)", color="#2471a3")
ax2.set_ylabel("EXLMN 冲突 (M / 0.8 s)", color="#c0392b")
ax.set_ylim(0, 320)
ax2.set_ylim(0, 22)
ax.set_title("(d) 闭环 W=256：adapt 砍半 TAD 请求但未降 EXLMN", fontsize=10.5)
lines1, labels1 = ax.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax.legend(lines1 + lines2, labels1 + labels2, frameon=False, fontsize=8, loc="upper right")
ax.grid(axis="y", alpha=0.3)

fig.tight_layout(rect=[0, 0, 1, 0.95])
out_base = ROOT / "docs/figures/mrv-252-single-binary-all-regimes"
fig.savefig(f"{out_base}.png", dpi=180)
fig.savefig(f"{out_base}.svg")
print("Wrote", out_base)
