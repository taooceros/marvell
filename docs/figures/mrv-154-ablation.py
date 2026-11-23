"""MRV-154 Chart 1: Dual-machine 100GbE W=1 single-variable ablation (64 B & 256 B).

Plotted directly from verified raw log:
    logs/mrv154-2026-11-23/runs.jsonl

Run:
    uv run --with matplotlib python docs/figures/mrv-154-ablation.py
"""
import json
import statistics
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

REPO = Path(__file__).resolve().parents[2]
RUNS_PATH = REPO / "logs" / "mrv154-2026-11-23" / "runs.jsonl"

rows = [json.loads(line) for line in RUNS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def by_cfg(cfg):
    rs = [r for r in rows if r["cfg"] == cfg]
    assert len(rs) == 3, f"expected 3 reps for {cfg}, got {len(rs)}"
    for r in rs:
        assert r["verified"], f"unverified row in {cfg}"
        assert r["client"]["lost"] == 0 and r["client"]["bad"] == 0 and r["client"]["torn"] == 0
        assert r["card"]["bad_wire"] == 0 and r["card"]["bad_host"] == 0 and r["card"]["torn_bar4"] == 0
        if r["host"] is not None:
            assert r["host"]["bad"] == 0 and r["host"]["torn"] == 0 and r["host"]["page_faults"] == 0
    return rs


def med(xs):
    return statistics.median(xs)


left_specs = [
    ("direct_baseline_64_w1", "64 B\n基线", "#C83E31"),
    ("direct_defer_free_64_w1", "64 B\n仅后移free", "#9CA3AF"),
    ("direct_bar4_first_64_w1", "64 B\nBAR4优先", "#1F2937"),
    ("direct_bar4_first_defer_64_w1", "64 B\n优先+后移", "#4B5563"),
    ("direct_skip_eth_64_w1", "64 B\n在途禁eth", "#6B7280"),
    ("direct_baseline_256_w1", "256 B\n基线", "#D97770"),
    ("direct_bar4_first_256_w1", "256 B\nBAR4优先", "#374151"),
]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.6), gridspec_kw={"width_ratios": [1.38, 1.0]})

# ---------------- Panel A: Card-internal CNTVCT p50 + p99 ----------------
xs = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.45, 6.45])
p50s, p99s, eps, colors, labels, rep_p50s = [], [], [], [], [], []

for cfg, lbl, col in left_specs:
    rs = by_cfg(cfg)
    p50_list = [r["card"]["host_rtt_p50_us"] for r in rs]
    p99_list = [r["card"]["host_rtt_p99_us"] for r in rs]
    ep_list = [r["card"]["stages"]["lite_inflight_eth_empty"] / max(r["card"]["rpcs_in"], 1) for r in rs]
    p50s.append(med(p50_list))
    p99s.append(med(p99_list))
    eps.append(med(ep_list))
    rep_p50s.append(p50_list)
    colors.append(col)
    labels.append(lbl)

ax1.bar(xs, p50s, width=0.66, color=colors, edgecolor="#111827", linewidth=0.7, zorder=3)

# Plot individual n=3 rep points + p99 whisker caps
for x, reps, p50, p99 in zip(xs, rep_p50s, p50s, p99s):
    ax1.scatter(x + np.array([-0.10, 0.0, 0.10]), reps, color="white", edgecolor="#111827", s=24, linewidth=0.9, zorder=5)
    ax1.plot([x, x], [max(reps), p99], color="#6B7280", linewidth=1.2, zorder=2)
    ax1.scatter([x], [p99], marker="_", color="#4B5563", s=70, linewidth=1.7, zorder=4)
    # p50 value cleanly inside top of bar (white bold), p99 small grey above whisker cap
    ax1.text(x, p50 - 0.11, f"{p50:.3f}", ha="center", va="center", fontsize=9.8, fontweight="bold", color="white", zorder=6)
    ax1.text(x, p99 + 0.025, f"p99 {p99:.3f}", ha="center", va="bottom", fontsize=7.6, color="#4B5563")

# Reference lines placed in completely clear whitespace
ax1.axhline(1.00, color="#1F2937", linestyle="--", linewidth=1.15, zorder=2)
ax1.text(
    4.725, 1.00, "判定线 1.00 µs", ha="center", va="center", fontsize=8.8, fontweight="bold", color="#1F2937",
    bbox=dict(boxstyle="round,pad=0.18", facecolor="white", edgecolor="#9CA3AF", lw=0.7), zorder=7,
)
ax1.axhline(1.32, color="#9CA3AF", linestyle=":", linewidth=1.2, zorder=2)
ax1.text(
    3.48, 1.335, "CX5 全栈硬件自环 1.32 µs\n（含 Verbs/RoCE，保守上界参照）", ha="center", va="bottom", fontsize=8.1, color="#4B5563",
    bbox=dict(boxstyle="round,pad=0.16", facecolor="white", edgecolor="none", alpha=0.90), zorder=6,
)

# Separator between 64 B and 256 B
ax1.axvline(4.725, color="#D1D5DB", linestyle="-", linewidth=1.0, zorder=1)

# Annotate in-flight empty eth_rx polls/RPC at bar bottoms
for x, ep in zip(xs, eps):
    ep_txt = f"{ep:.2f}次" if ep >= 0.05 else f"{ep:.3f}次"
    ax1.text(x, 0.06, ep_txt, ha="center", va="bottom", fontsize=8.6, color="white", fontweight="bold", zorder=6)

# Clean top brackets matching Panel B grammar
# 64 B: baseline (x=0) -> bar4_first (x=2)
d64_ns = int(round((p50s[2] - p50s[0]) * 1000))
y_b64 = 1.60
ax1.plot([0.0, 0.0, 2.0, 2.0], [p99s[0] + 0.13, y_b64, y_b64, p99s[2] + 0.13], color="#C83E31", lw=1.4, zorder=6)
ax1.text(1.0, y_b64 + 0.025, f"−{abs(d64_ns)} ns", ha="center", va="bottom", fontsize=10.8, fontweight="bold", color="#C83E31")

# 256 B: baseline (x=5.45) -> bar4_first (x=6.45)
d256_ns = int(round((p50s[6] - p50s[5]) * 1000))
y_b256 = 1.65
ax1.plot([5.45, 5.45, 6.45, 6.45], [p99s[5] + 0.12, y_b256, y_b256, p99s[6] + 0.12], color="#C83E31", lw=1.4, zorder=6)
ax1.text(5.95, y_b256 + 0.025, f"−{abs(d256_ns)} ns", ha="center", va="bottom", fontsize=10.4, fontweight="bold", color="#C83E31")

ax1.set_xticks(xs)
ax1.set_xticklabels(labels, fontsize=9.2)
ax1.set_ylabel("卡内裸 PCIe 环往返 CNTVCT p50 (µs)", fontsize=10.5)
ax1.set_ylim(0, 1.82)
ax1.set_xlim(-0.58, 7.05)
ax1.set_title("A. 卡内往返单变量消融（柱顶标 p50，柱底标在途空 eth_rx 次数）", fontsize=10.8, fontweight="bold", pad=10)

# ---------------- Panel B: mercury end-to-end RTT decomposition ----------------
echo_64 = med([r["client"]["tot_p50_us"] for r in by_cfg("card_echo_64_w1")])
base_64 = med([r["client"]["tot_p50_us"] for r in by_cfg("direct_baseline_64_w1")])
bf_64 = med([r["client"]["tot_p50_us"] for r in by_cfg("direct_bar4_first_64_w1")])

echo_256 = med([r["client"]["tot_p50_us"] for r in by_cfg("card_echo_256_w1")])
base_256 = med([r["client"]["tot_p50_us"] for r in by_cfg("direct_baseline_256_w1")])
bf_256 = med([r["client"]["tot_p50_us"] for r in by_cfg("direct_bar4_first_256_w1")])

bx = np.array([0.0, 0.85, 2.25, 3.10])
echo_vals = np.array([echo_64, echo_64, echo_256, echo_256])
tot_vals = np.array([base_64, bf_64, base_256, bf_256])
host_inc = tot_vals - echo_vals
top_cols = ["#C83E31", "#1F2937", "#D97770", "#374151"]

ax2.bar(bx, echo_vals, width=0.58, color="#E5E7EB", edgecolor="#6B7280", linewidth=0.7, zorder=3)
ax2.bar(bx, host_inc, bottom=echo_vals, width=0.58, color=top_cols, edgecolor="#111827", linewidth=0.7, zorder=3)

for x, cfg in zip(bx, ["direct_baseline_64_w1", "direct_bar4_first_64_w1", "direct_baseline_256_w1", "direct_bar4_first_256_w1"]):
    reps = [r["client"]["tot_p50_us"] for r in by_cfg(cfg)]
    ax2.scatter(x + np.array([-0.08, 0.0, 0.08]), reps, color="white", edgecolor="#111827", s=22, linewidth=0.9, zorder=5)

for x, e, inc, tot in zip(bx, echo_vals, host_inc, tot_vals):
    ax2.text(x, e * 0.5, f"{e:.3f}", ha="center", va="center", fontsize=9.2, color="#374151", fontweight="bold")
    ax2.text(x, e + inc * 0.5, f"+{inc:.3f}", ha="center", va="center", fontsize=9.0, color="white", fontweight="bold")
    ax2.text(x, tot + 0.13, f"{tot:.3f} µs", ha="center", va="bottom", fontsize=9.8, fontweight="bold", color="#111827")

for (x0, x1, y0, y1, d_ns) in [
    (0.0, 0.85, base_64, bf_64, int(round((bf_64 - base_64) * 1000))),
    (2.25, 3.10, base_256, bf_256, int(round((bf_256 - base_256) * 1000))),
]:
    y_top = max(y0, y1) + 0.65
    ax2.plot([x0, x0, x1, x1], [y0 + 0.45, y_top, y_top, y1 + 0.45], color="#C83E31", lw=1.4)
    ax2.text((x0 + x1) / 2, y_top + 0.08, f"−{abs(d_ns)} ns", ha="center", va="bottom", fontsize=10.8, fontweight="bold", color="#C83E31")

ax2.axvline(1.55, color="#D1D5DB", linestyle="-", linewidth=1.0, zorder=1)
ax2.set_xticks(bx)
ax2.set_xticklabels(["64 B\n基线", "64 B\nBAR4优先", "256 B\n基线", "256 B\nBAR4优先"], fontsize=9.3)
ax2.set_ylabel("mercury 端到端 RTT p50 (µs)", fontsize=10.5)
ax2.set_ylim(0, 8.95)
ax2.set_xlim(-0.55, 3.65)
legend_handles = [
    mpatches.Patch(facecolor="#E5E7EB", edgecolor="#6B7280", label="网线 + card_echo 基底"),
    mpatches.Patch(facecolor="#C83E31", edgecolor="#111827", label="基线主机往返增量"),
    mpatches.Patch(facecolor="#1F2937", edgecolor="#111827", label="BAR4优先主机往返增量"),
]
ax2.legend(handles=legend_handles, loc="upper left", fontsize=8.5, frameon=True, facecolor="white", edgecolor="#D1D5DB")
ax2.set_title("B. mercury 端到端 RTT 同步 1:1 下降（同轮冷频差分）", fontsize=10.4, fontweight="bold", pad=10)

fig.suptitle(
    "MRV-154 真实双机 100GbE 单变量消融（W = 1, batch = 1, 1C@2.0 GHz ⇄ cpu40@3.90 GHz, Uncore 2.20 GHz, n = 3 零错验真）",
    fontsize=11.4,
    fontweight="bold",
    y=0.99,
)
save(fig, "mrv-154-ablation")
print("Saved mrv-154-ablation.{png,svg}")
