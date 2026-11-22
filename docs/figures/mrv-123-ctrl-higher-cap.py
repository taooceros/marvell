#!/usr/bin/env python3
"""MRV-123 Figure 3: Part 3 Mechanism Controls at 20 M/s Poisson (W_credit > MAX_DRAIN=32, B in {1, 8, 16, 32})."""
import collections
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv123-2026-11-22/runs.jsonl"

recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
for r in recs:
    assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
    assert r["host_pmu"]["min_pct"] >= 99.99 and r["card_pmu"]["min_pct"] >= 99.99

by_key = collections.defaultdict(list)
for r in recs:
    if r["mode"] == "open_echo" and r["offered_mps"] == 20.0:
        by_key[(r["card_cores"], r["credit_cap"], r["release_batch"])].append(r)


def med(key, fn):
    return statistics.median(fn(r) for r in by_key[key])


def snp_per_taken(r):
    taken = sum(c["taken"] for c in r["card_pmu"]["cores"])
    return r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / taken


def tx_batch(r):
    return (r["rounds"] + r["warmup"]) / max(1, r["send_batches"])


def rx_batch(r):
    return (r["rounds"] + r["warmup"]) / max(1, r["polls"] - r["empty_polls"])


def busy_polls(r):
    return sum(c["busy_polls"] for c in r["card_pmu"]["cores"])


fig, axes = plt.subplots(1, 2, figsize=(14.6, 5.9))

# ---- Panel A: WC1 20 M/s Poisson across C in {32, 48, 64} and B in {1, 8, 16, 32} ----
ax = axes[0]
bs = [1, 8, 16, 32]
xb = np.arange(len(bs))

ax.axhspan(1.0, 2.5, color="#ccfbf1", alpha=0.35, zorder=0)
ax.axhline(2.5, color="#0f766e", ls=":", lw=1.4, label="20 M/s 端到端目标门槛 tot p99 = 2.5 µs")

wc1_specs = [
    (32, "C=32 端到端 tot p99（过载 17.5–17.9 M/s）", "#dc2626", "o", 2.0, "-"),
    (48, "C=48 端到端 tot p99（稳守 20.01 M/s）", "#1d4ed8", "s", 2.5, "-"),
    (64, "C=64 端到端 tot p99（稳守 20.01 M/s）", "#7c3aed", "D", 2.2, "-"),
]
for cap, lbl, col, mk, lw, ls in wc1_specs:
    ys_tot = [med((1, cap, b), lambda r: r["tot_us"]["p99"]) for b in bs]
    ax.plot(xb, ys_tot, f"{ls}{mk}", color=col, lw=lw, ms=6.2, label=lbl, zorder=5)
    for i, b in enumerate(bs):
        for r in by_key[(1, cap, b)]:
            ax.scatter(i, r["tot_us"]["p99"], color=col, marker=mk, s=24, alpha=0.5, zorder=6)

# Wire p99 dashed for pipelined C=48 and C=64
ys_w48 = [med((1, 48, b), lambda r: r["wire_us"]["p99"]) for b in bs]
ys_w64 = [med((1, 64, b), lambda r: r["wire_us"]["p99"]) for b in bs]
ax.plot(xb, ys_w48, "--s", color="#60a5fa", lw=1.6, ms=4.5, label="C=48 线端 wire p99（仅硬件，含批末 RDTSCP）", zorder=3)
ax.plot(xb, ys_w64, "--D", color="#c4b5fd", lw=1.5, ms=4.3, label="C=64 线端 wire p99（仅硬件）", zorder=3)

# Recompute exact PMU causality stats for annotation
bp_48_1 = med((1, 48, 1), busy_polls)
bp_48_32 = med((1, 48, 32), busy_polls)
bp_drop = (bp_48_32 / bp_48_1 - 1.0) * 100.0
snp_48_1 = med((1, 48, 1), snp_per_taken)
snp_48_32 = med((1, 48, 32), snp_per_taken)
rx_48_32 = med((1, 48, 32), rx_batch)
tot_48_32 = med((1, 48, 32), lambda r: r["tot_us"]["p99"])
wire_48_32 = med((1, 48, 32), lambda r: r["wire_us"]["p99"])

ax.annotate(
    f"最佳点：C=48, B=32 (rx_bat={rx_48_32:.1f})\n"
    f"tot p99 = {tot_48_32:.2f} µs (-41%), wire = {wire_48_32:.2f} µs\n"
    f"忙轮询 {bp_drop:.1f}%，snp/taken {snp_48_1:.2f}→{snp_48_32:.2f}\n"
    f"仍超 2.5 µs 门槛 2.8×",
    xy=(3, tot_48_32), xytext=(1.42, 85),
    fontsize=7.6, color="#1e3a8a", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.28", fc="#eff6ff", ec="#1d4ed8", lw=1.0),
    arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.2),
)
ax.scatter([1], [42.52], s=105, facecolors="none", edgecolors="#7c3aed", linewidths=1.6, zorder=8)
ax.annotate(
    "C=64 B=8 r1 离群 42.5 µs",
    xy=(1, 42.52), xytext=(0.05, 118),
    fontsize=7.4, color="#6d28d9",
    bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#7c3aed", lw=0.8),
    arrowprops=dict(arrowstyle="->", color="#7c3aed", lw=0.9),
)
ax.text(1.5, 29000, "C=32 停等过载区（15.6 → 19.4 ms）", ha="center", fontsize=8.0, color="#991b1b", fontweight="bold")

ax.set_yscale("log")
ax.set_ylim(1.0, 85000)
ax.set_xticks(xb)
ax.set_xticklabels([f"B={b}" for b in bs])
ax.set_xlabel("主机信用滞回批释放阈值 B（T_pause = 0，max_send_burst = 0）")
ax.set_ylabel("20 M/s 泊松往返尾延迟 p99 (µs，对数坐标)")
ax.set_title("A. WC1 20 M/s 对照：W_credit > 32 后 B=32 降 41–46%，最优仍 7.01 µs")
ax.legend(loc="upper left", bbox_to_anchor=(0.01, 0.87), fontsize=6.7, frameon=True, facecolor="white")

# ---- Panel B: WC4 20 M/s Poisson — Throughput (bars) + tot p99 (right log axis) ----
ax = axes[1]
wc4_items = [
    (32, 1, "C32\nB1"),
    (32, 8, "C32\nB8"),
    (32, 16, "C32\nB16"),
    (32, 32, "C32\nB32"),
    (40, 1, "C40\nB1"),
    (40, 8, "C40\nB8"),
    (40, 16, "C40\nB16"),
    (48, 1, "C48\nB1"),
    (48, 8, "C48\nB8"),
    (48, 16, "C48\nB16"),
]
x4 = np.arange(len(wc4_items))
achs = [med((4, c, b), lambda r: r["achieved_mps"]) for c, b, _ in wc4_items]
p99s = [med((4, c, b), lambda r: r["tot_us"]["p99"]) for c, b, _ in wc4_items]

bar_colors = [
    "#94a3b8", "#94a3b8", "#94a3b8", "#94a3b8",
    "#059669", "#10b981", "#6ee7b7",
    "#dc2626", "#f97316", "#2563eb",
]
ax.bar(x4, achs, width=0.62, color=bar_colors, edgecolor="#334155", lw=0.8, alpha=0.82, zorder=2,
       label="实测达成吞吐 achieved_mps（左轴）")
for i, (c, b, _) in enumerate(wc4_items):
    for r in by_key[(4, c, b)]:
        ax.scatter(i, r["achieved_mps"], color="#0f172a", s=16, alpha=0.65, zorder=4)
    if c == 32:
        ax.text(i, achs[i] - 1.5, f"{achs[i]:.1f}", ha="center", va="top", fontsize=7.3, color="#0f172a", fontweight="bold")
    elif c == 48 and b == 16:
        ax.text(i, achs[i] - 1.5, f"{achs[i]:.1f}", ha="center", va="top", fontsize=7.3, color="white", fontweight="bold")
    else:
        ax.text(i, achs[i] + 0.4, f"{achs[i]:.1f}", ha="center", va="bottom", fontsize=7.3, color="#1e293b", fontweight="bold")

ax.axhline(20.0, color="#0f766e", ls="--", lw=1.2, label="目标到达率 20.0 M/s")
ax.axhline(13.1, color="#991b1b", ls=":", lw=1.1, alpha=0.75)

ax2 = ax.twinx()
ax2.spines["top"].set_visible(False)
ax2.plot(x4, p99s, "-o", color="#0f172a", lw=2.0, ms=5.5, mfc="white", mew=1.8,
         label="端到端 tot p99（右轴，对数 µs）", zorder=6)
for i, (c, b, _) in enumerate(wc4_items):
    for r in by_key[(4, c, b)]:
        ax2.scatter(i, r["tot_us"]["p99"], color="#0f172a", s=18, alpha=0.45, zorder=5)

ax2.set_yscale("log")
ax2.set_ylim(10, 120000)
ax.set_ylim(0, 26.8)

tx_40_16 = med((4, 40, 16), tx_batch)
tx_48_16 = med((4, 48, 16), tx_batch)
snp_48_1_w4 = med((4, 48, 1), snp_per_taken)
snp_48_16_w4 = med((4, 48, 16), snp_per_taken)

ax.annotate(
    f"仅 C40 B1/B8 达 20.0 M/s (36.3/63.2 µs)\n"
    f"B16 下冲至 ~{40 - tx_40_16:.1f} 在途、无突发限狂发 {tx_40_16:.1f} 条/批\n"
    f"致降容过载 (19.6 M/s)",
    xy=(6, 19.61), xytext=(0.15, 22.1),
    fontsize=7.3, color="#065f46", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.25", fc="#ecfdf5", ec="#059669", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#059669", lw=1.0, connectionstyle="arc3,rad=-0.08"),
)
ax.annotate(
    f"C48 破锁 (B=1→16, tx={tx_48_16:.1f} 条/批)：\n"
    f"13.6→18.8 M/s (+38%), TAD 0.26→1.20\n"
    f"snp/taken {snp_48_1_w4:.2f}→{snp_48_16_w4:.2f}；未测 B32/C64",
    xy=(9, 18.82), xytext=(6.05, 4.0),
    fontsize=7.0, color="#1e3a8a", fontweight="bold", zorder=9,
    bbox=dict(boxstyle="round,pad=0.25", fc="#eff6ff", ec="#2563eb", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#2563eb", lw=1.0, connectionstyle="arc3,rad=-0.12"),
)
ax.set_xticks(x4)
ax.set_xticklabels([lbl for _, _, lbl in wc4_items], fontsize=7.8)
ax.set_xlabel("WC4 20 M/s 泊松已测配置（信用上限 C × 滞回阈值 B；未测 C48 B32 与 C64）")
ax.set_ylabel("实测稳态吞吐 achieved_mps (M msg/s)")
ax2.set_ylabel("真实端到端 tot p99 (µs，对数坐标)")
ax.set_title("B. WC4 20 M/s 已测对照（未测 C48 B32 / C64）：均远超 2.5 µs")

h1, lab1 = ax.get_legend_handles_labels()
h2, lab2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, lab1 + lab2, loc="lower left", fontsize=6.7, frameon=True, facecolor="white")

fig.suptitle(
    "MRV-123 Part 3 机理对照：W_credit > 32 时滞回可削峰/破锁，但 T_pause=0 + 无突发上限仍远超 2.5 µs（n=3 全散点）",
    fontsize=11.3, fontweight="bold", y=0.98,
)
fig.text(
    0.5, 0.008,
    "全部滞回运行设 max_send_burst=0 且 MAX_DRAIN=32（开闸重入突发达 32–34 条）；B 增大缩短卡忙时、压低 snp/taken，但 T_pause=0 排空即 77 M/s 盯格致重入再触 76 ns 串行；snp 按 card_pmu.taken 计，PMU 100%",
    ha="center", fontsize=7.5, color="#475569",
)
fig.tight_layout(rect=[0, 0.03, 1, 0.94])
png = mrvplot.HERE / "mrv-123-ctrl-higher-cap.png"
fig.savefig(png)
fig.savefig(mrvplot.HERE / "mrv-123-ctrl-higher-cap.svg")
plt.close(fig)
print(f"Saved {png}")
