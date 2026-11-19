#!/usr/bin/env python3
"""MRV-75 Figure 3: Part 3 Single-Variable Intervention B — Host max_send_burst vs host_gap_ns."""
import collections
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv75")
RUNS = ROOT / "logs/mrv75-2026-11-19/runs.jsonl"

recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
by_key = collections.defaultdict(list)
for r in recs:
    by_key[(r["exp"], r["cond"])].append(r)


def med(exp, cond, fn):
    return statistics.median(fn(r) for r in by_key[(exp, cond)])


fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.1))

# ---- Panel A: max_send_burst sweep (WC4 W=64, T_pause=0) ----
ax = axes[0]
bursts = [1, 2, 4, 8, 12, 16, 32, 64]
xb = np.arange(len(bursts))
mops_b = [med("part3_burst", f"burst_{b}", lambda r: r["mops"]) for b in bursts]
rtt_b = [med("part3_burst", f"burst_{b}", lambda r: r["rtt_p50_us"]) for b in bursts]
weff_b = [med("part3_burst", f"burst_{b}", lambda r: r["mops"] * r["rtt_mean_us"]) for b in bursts]

stamp_b = [1, 4, 8, 12, 16, 64]
xb_sp = [bursts.index(b) for b in stamp_b]
h2c_b = [by_key[("part3_stamp", f"burst_{b}_sp")][0]["stamp_summary"]["all"]["h2c_p50_ns"] / 1000.0 for b in stamp_b]

ax.axvspan(3.5, 7.4, color="#fee2e2", alpha=0.55, zorder=0)
l1, = ax.plot(xb, mops_b, "-o", color="#1d4ed8", lw=2.3, ms=6.5, label="闭环吞吐 (M/s)")
for idx, b in enumerate(bursts):
    for r in by_key[("part3_burst", f"burst_{b}")]:
        ax.scatter(idx, r["mops"], color="#1d4ed8", s=16, alpha=0.45, zorder=3)
ax.set_xticks(xb)
ax.set_xticklabels([f"{b}\n({w:.0f})" for b, w in zip(bursts, weff_b)], fontsize=8.8)
ax.set_xlabel("主机单轮最大发送突发 max_send_burst（括号为有效在途 W_eff）")
ax.set_ylabel("吞吐 (M msg/s)", color="#1d4ed8")
ax.tick_params(axis="y", labelcolor="#1d4ed8")
ax.set_ylim(5, 25)

ax2 = ax.twinx()
ax2.spines["top"].set_visible(False)
l2, = ax2.plot(xb, rtt_b, "--s", color="#dc2626", lw=2.0, ms=5.5, label="RTT p50 (µs)")
l3, = ax2.plot(xb_sp, h2c_b, ":^", color="#ea580c", lw=1.9, ms=5.5, label="去程 h2c p50 (µs)")
ax2.set_ylabel("延迟 (µs)", color="#dc2626")
ax2.tick_params(axis="y", labelcolor="#dc2626")
ax2.set_ylim(0, 5.0)
ax2.grid(False)

ax.axvline(3.5, color="#991b1b", ls="--", lw=1.1, alpha=0.8)
ax.annotate(
    "burst = 8（零卡改动）\n20.18 M/s (+54%)\nRTT 4.06 → 0.92 µs (-77%)\nh2c 3.64 → 0.66 µs",
    xy=(3, 20.18),
    xytext=(0.15, 20.0),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.4),
    fontsize=8.2,
    fontweight="bold",
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.3", fc="#eff6ff", ec="#93c5fd"),
)
ax.text(4.65, 22.8, "W_eff 越过 ~40 阈值\n跌回 13.1 M/s 慢态", fontsize=8.3, color="#991b1b", fontweight="bold")
ax.set_title("A. 限制单轮突发 max_send_burst：把 W_eff 稳在临界下")
ax.legend([l1, l2, l3], [l.get_label() for l in (l1, l2, l3)], loc="lower right", fontsize=8, frameon=True, facecolor="white")

# ---- Panel B: host_gap_ns sweep (WC4 W=64, max_send_burst=64, T_pause=0) ----
ax = axes[1]
gaps = [0, 10, 20, 40, 60]
xg = np.arange(len(gaps))
mops_g = [med("part3_gap", f"gap_{g}ns", lambda r: r["mops"]) for g in gaps]
snp_g = [med("part3_gap", f"gap_{g}ns", lambda r: r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / (r["rounds"] + r["warmup"])) for g in gaps]
h2c_g = [
    by_key[("part3_stamp", "burst_64_sp" if g == 0 else f"gap_{g}ns_sp")][0]["stamp_summary"]["all"]["h2c_p50_ns"] / 1000.0
    for g in gaps
]
c2h_g = [
    by_key[("part3_stamp", "burst_64_sp" if g == 0 else f"gap_{g}ns_sp")][0]["stamp_summary"]["all"]["c2h_p50_ns"] / 1000.0
    for g in gaps
]

l1, = ax.plot(xg, mops_g, "-o", color="#1d4ed8", lw=2.3, ms=6.5, label="闭环吞吐 (M/s)")
for idx, g in enumerate(gaps):
    for r in by_key[("part3_gap", f"gap_{g}ns")]:
        ax.scatter(idx, r["mops"], color="#1d4ed8", s=16, alpha=0.45, zorder=3)
ax.set_xticks(xg)
ax.set_xticklabels([f"{g} ns\n(嗅探 {s:.1f})" for g, s in zip(gaps, snp_g)], fontsize=8.8)
ax.set_xlabel("主机逐条最小发送间隔 host_gap_ns（括号为卡 TAD 嗅探/条）")
ax.set_ylabel("吞吐 (M msg/s)", color="#1d4ed8")
ax.tick_params(axis="y", labelcolor="#1d4ed8")
ax.set_ylim(5, 25)

ax2 = ax.twinx()
ax2.spines["top"].set_visible(False)
l2, = ax2.plot(xg, h2c_g, "--^", color="#dc2626", lw=2.0, ms=6, label="去程 h2c p50 (µs)")
l3, = ax2.plot(xg, c2h_g, ":D", color="#059669", lw=2.0, ms=5.5, label="回程 c2h p50 (µs)")
ax2.set_ylabel("单向停留时间 (µs)", color="#dc2626")
ax2.tick_params(axis="y", labelcolor="#dc2626")
ax2.set_ylim(0, 5.0)
ax2.grid(False)

ax.text(
    0.25,
    19.8,
    "10–40 ns 仍填满 W=64\n全程锁死 12.9–13.0 M/s\n嗅探高达 4.4–4.5 次/条",
    fontsize=8.3,
    color="#991b1b",
    fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.3", fc="#fef2f2", ec="#fca5a5"),
)
ax2.annotate(
    "60 ns 主机限速：\n排队转到回程主机环\n(h2c 0.54, c2h 4.17 µs)",
    xy=(4, 4.17),
    xytext=(2.05, 3.85),
    arrowprops=dict(arrowstyle="->", color="#059669", lw=1.3),
    fontsize=8.0,
    color="#065f46",
    bbox=dict(boxstyle="round,pad=0.25", fc="#ecfdf5", ec="#6ee7b7"),
)
ax.set_title("B. 只拉大发送间隔 host_gap_ns：无法跳出 13 M/s")
ax.legend([l1, l2, l3], [l.get_label() for l in (l1, l2, l3)], loc="lower center", fontsize=8, frameon=True, facecolor="white")

fig.suptitle("MRV-75 Part 3：单变量干预 B —— 主机发包节流对比（WC4 W=64，T_pause = 0，每档 3 次重复）", fontsize=11.5, fontweight="bold")
out = mrvplot.save(fig, "mrv-75-part3-host-pacing")
print(f"Saved {out}")
