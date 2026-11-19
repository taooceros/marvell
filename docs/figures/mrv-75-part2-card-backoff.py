#!/usr/bin/env python3
"""MRV-75 Figure 2: Part 2 Single-Variable Intervention A — Card Core Count & Empty-Poll Backoff T_pause."""
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


fig, axes = plt.subplots(1, 3, figsize=(16.0, 5.1))

# ---- Panel A: Card core count (1 / 2 / 4 cores) at W=64, T_pause=0 ----
ax = axes[0]
core_specs = [
    ("part1_xray", "wc1_w64", "1 卡核\n(WC1)"),
    ("part2_cores", "wc2_w64", "2 卡核\n(WC2)"),
    ("part1_xray", "wc4_w64", "4 卡核\n(WC4)"),
]
xc = np.arange(3)
mops_c = [med(e, c, lambda r: r["mops"]) for e, c, _ in core_specs]
snp_c = [med(e, c, lambda r: r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / (r["rounds"] + r["warmup"])) for e, c, _ in core_specs]
bat_c = [med(e, c, lambda r: sum(cc["taken"] for cc in r["card_pmu"]["cores"]) / max(1, sum(cc["busy_polls"] for cc in r["card_pmu"]["cores"]))) for e, c, _ in core_specs]

ax.bar(xc, mops_c, 0.52, color=["#059669", "#dc2626", "#b91c1c"], alpha=0.88)
for i, (e, c, _) in enumerate(core_specs):
    for r in by_key[(e, c)]:
        ax.scatter(i, r["mops"], color="black", s=16, alpha=0.5, zorder=4)
    ax.text(
        i,
        mops_c[i] + 1.0,
        f"{mops_c[i]:.1f} M/s\n嗅探 {snp_c[i]:.2f}/条\n批 {bat_c[i]:.1f} 格",
        ha="center",
        va="bottom",
        fontsize=8.5,
        fontweight="bold",
        color="#065f46" if i == 0 else "#991b1b",
    )
ax.set_xticks(xc)
ax.set_xticklabels([s[2] for s in core_specs], fontsize=9.5)
ax.set_ylim(0, 44)
ax.set_ylabel("闭环吞吐 (M msg/s)")
ax.set_title("A. 只变卡核数 (W=64)：≥ 2 核即坍缩至 13.1 M/s")

# ---- Panel B: T_pause sweep on WC4 W=64 (Throughput, RTT, h2c) ----
ax = axes[1]
pauses = [0, 200, 500, 1000, 1500, 1800, 2000, 3000]
xp = [p / 1000.0 for p in pauses]
mops_p = [med("part2_pause", f"pause_{p}ns", lambda r: r["mops"]) for p in pauses]
rtt_p = [med("part2_pause", f"pause_{p}ns", lambda r: r["rtt_p50_us"]) for p in pauses]

stamp_p = [0, 500, 1000, 1500, 1800, 2000, 3000]
xp_sp = [p / 1000.0 for p in stamp_p]
h2c_sp = [by_key[("part2_stamp", f"pause_{p}ns_sp")][0]["stamp_summary"]["all"]["h2c_p50_ns"] / 1000.0 for p in stamp_p]

l1, = ax.plot(xp, mops_p, "-o", color="#1d4ed8", lw=2.3, ms=6.5, label="闭环吞吐 (M/s)")
for p in pauses:
    for r in by_key[("part2_pause", f"pause_{p}ns")]:
        ax.scatter(p / 1000.0, r["mops"], color="#1d4ed8", s=16, alpha=0.45, zorder=3)
ax.axhline(13.1, color="#94a3b8", ls=":", lw=1.1)
ax.set_xlabel("卡核空轮询退避 T_pause (µs)")
ax.set_ylabel("吞吐 (M msg/s)", color="#1d4ed8")
ax.tick_params(axis="y", labelcolor="#1d4ed8")
ax.set_ylim(8, 34)
ax.set_xlim(-0.15, 3.25)

ax2 = ax.twinx()
ax2.spines["top"].set_visible(False)
l2, = ax2.plot(xp, rtt_p, "--s", color="#dc2626", lw=2.0, ms=5.5, label="RTT p50 (µs)")
l3, = ax2.plot(xp_sp, h2c_sp, ":^", color="#ea580c", lw=1.9, ms=5.5, label="去程 h2c p50 (µs)")
ax2.set_ylabel("延迟 (µs)", color="#dc2626")
ax2.tick_params(axis="y", labelcolor="#dc2626")
ax2.set_ylim(0, 6.2)
ax2.grid(False)

ax.annotate(
    "T_pause = 1.8 µs 破墙!\n26.81 M/s (+105%)\nRTT 4.06 → 1.70 µs",
    xy=(1.8, 26.81),
    xytext=(1.88, 28.8),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.4),
    fontsize=8.1,
    fontweight="bold",
    color="#1e3a8a",
    bbox=dict(boxstyle="round,pad=0.3", fc="#eff6ff", ec="#93c5fd"),
)
ax.set_title("B. 只变卡核退避 T_pause (WC4 W=64)：吞吐翻倍")
ax.legend([l1, l2, l3], [l.get_label() for l in (l1, l2, l3)], loc="lower left", fontsize=8, frameon=True, facecolor="white")

# ---- Panel C: Card-side microscopic mechanism vs T_pause ----
ax = axes[2]
snp_p = [med("part2_pause", f"pause_{p}ns", lambda r: r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / (r["rounds"] + r["warmup"])) for p in pauses]
l2_p = [med("part2_pause", f"pause_{p}ns", lambda r: sum(cc["l2d_cache_refill"] for cc in r["card_pmu"]["cores"]) / (r["rounds"] + r["warmup"])) for p in pauses]
bat_p = [med("part2_pause", f"pause_{p}ns", lambda r: sum(cc["taken"] for cc in r["card_pmu"]["cores"]) / max(1, sum(cc["busy_polls"] for cc in r["card_pmu"]["cores"]))) for p in pauses]

l1, = ax.plot(xp, snp_p, "-s", color="#dc2626", lw=2.2, ms=6, label="TAD 嗅探 (次/条)")
l2, = ax.plot(xp, l2_p, "-^", color="#ea580c", lw=2.0, ms=6, label="卡 L2 重填 (次/条)")
ax.set_xlabel("卡核空轮询退避 T_pause (µs)")
ax.set_ylabel("每条消息 TAD 嗅探 / L2 重填", color="#dc2626")
ax.tick_params(axis="y", labelcolor="#dc2626")
ax.set_ylim(0, 5.0)

ax2 = ax.twinx()
ax2.spines["top"].set_visible(False)
l3, = ax2.plot(xp, bat_p, "--o", color="#059669", lw=2.2, ms=6, label="卡核忙轮询批量 (格/轮)")
ax2.axhline(16.0, color="#059669", ls=":", lw=1.0, alpha=0.6)
ax2.text(0.08, 16.3, "W/4 = 16 格满波", fontsize=7.8, color="#065f46")
ax2.set_ylabel("卡每核忙轮询批量 (格/轮)", color="#059669")
ax2.tick_params(axis="y", labelcolor="#059669")
ax2.set_ylim(0, 18.5)
ax2.grid(False)

ax.set_title("C. 破墙机制：嗅探降 59%、L2 重填降 88%、攒满 15 格")
ax.legend([l1, l2, l3], [l.get_label() for l in (l1, l2, l3)], loc="center right", fontsize=8, frameon=True, facecolor="white")

fig.suptitle("MRV-75 Part 2：单变量干预 A —— 卡核数与卡空轮询退避 T_pause（固定 MOVDIR64B + WC，W=64，每档 3 次重复）", fontsize=11.5, fontweight="bold")
out = mrvplot.save(fig, "mrv-75-part2-card-backoff")
print(f"Saved {out}")
