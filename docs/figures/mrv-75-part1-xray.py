#!/usr/bin/env python3
"""MRV-75 Figure 1: Part 1 Hardware Queue X-Ray (incl. tad_req_occ), WC4 Window Cliff & MRV-50 Pre-Touch Check."""
import collections
import csv
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
STAMP_DIR = ROOT / "logs/mrv75-2026-11-19/stamps"

recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
by_key = collections.defaultdict(list)
for r in recs:
    by_key[(r["exp"], r["cond"])].append(r)


def med(exp, cond, fn):
    return statistics.median(fn(r) for r in by_key[(exp, cond)])


fig, axes = plt.subplots(1, 3, figsize=(16.2, 5.1))

# ---- Panel A: Where do the in-flight messages sit? (Host CHA/IRP1 vs Card TAD vs Upstream PEM0/IOBN) ----
ax = axes[0]
states = [
    ("part1_xray", "wc4_w1", "WC4\nW=1\n(1.0 M/s)"),
    ("part1_xray", "wc4_w64", "WC4\nW=64 慢态\n(13.1 M/s)"),
    ("part1_wscan", "wc4_w128", "WC4\nW=128 慢态\n(13.1 M/s)"),
    ("part1_xray", "wc1_w64", "WC1\nW=64 快态\n(35.0 M/s)"),
    ("part1_xray", "wc1_w1024", "WC1\nW=1024\n(46.1 M/s)"),
    ("part1_wscan", "wc4_w1024", "WC4\nW=1024\n(30.4 M/s)"),
]
labels = [s[2] for s in states]
x = np.arange(len(states))
cha_inf = [med(e, c, lambda r: r["host_pmu"]["cha_inflight"]) for e, c, _ in states]
irp_inf = [med(e, c, lambda r: r["host_pmu"]["irp1_inflight_nom"]) for e, c, _ in states]
tad_inf = [med(e, c, lambda r: r["card_pmu"]["tad"]["tad_req_occ"]["val"] / (r["card_pmu"]["wall_s"] * 2.0e9)) for e, c, _ in states]
tot_inf = [med(e, c, lambda r: r["mops"] * r["rtt_mean_us"]) for e, c, _ in states]
card_and_ret = [max(0.0, t - c - i) for t, c, i in zip(tot_inf, cha_inf, irp_inf)]

w_bar = 0.58
ax.bar(x, cha_inf, w_bar, label="主机 CHA 在途 (IA_WCILF)", color="#2563eb")
ax.bar(x, irp_inf, w_bar, bottom=cha_inf, label="主机 IRP1 出站队列", color="#60a5fa")
ax.bar(
    x,
    card_and_ret,
    w_bar,
    bottom=[c + i for c, i in zip(cha_inf, irp_inf)],
    label="已离主机（堵在 TAD 上游 PEM0/IOBN）",
    color="#dc2626",
    alpha=0.82,
)
ax.set_yscale("symlog", linthresh=5.0)
ax.set_ylim(0, 4200)
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8.4)
ax.set_ylabel("平均在途消息数（条，对数刻度）")
ax.set_title("A. 队列 X 光：主机与 TAD 均空，堵在 PEM0/IOBN")
ax.legend(loc="upper left", fontsize=7.8, frameon=True, facecolor="white")

for idx, (t, c, td) in enumerate(zip(tot_inf, cha_inf, tad_inf)):
    if idx in (1, 2):
        ax.annotate(
            f"总 {t:.0f} 条\nCHA {c:.1f}\nTAD {td:.2f}",
            xy=(idx, t),
            xytext=(0, 5),
            textcoords="offset points",
            ha="center",
            fontsize=7.5,
            fontweight="bold",
            color="#991b1b",
        )
    elif idx == 4:
        ax.annotate(
            f"总 {t:.0f}\nCHA {c:.1f}",
            xy=(idx, t),
            xytext=(0, 5),
            textcoords="offset points",
            ha="center",
            fontsize=7.8,
            color="#1e40af",
        )
    elif idx == 5:
        ax.annotate(
            f"CHA\n{c:.0f}",
            xy=(idx, c),
            xytext=(0, -32),
            textcoords="offset points",
            ha="center",
            fontsize=7.8,
            fontweight="bold",
            color="white",
        )

# ---- Panel B: WC4 Window Scan Cliff (W=1..128) ----
ax = axes[1]
w_list = [
    ("part1_xray", "wc4_w1", 1),
    ("part1_wscan", "wc4_w16", 16),
    ("part1_wscan", "wc4_w32", 32),
    ("part1_wscan", "wc4_w40", 40),
    ("part1_wscan", "wc4_w48", 48),
    ("part1_xray", "wc4_w64", 64),
    ("part1_wscan", "wc4_w128", 128),
]
xs = np.arange(len(w_list))
w_labels = [str(w) for _, _, w in w_list]
mops_w = [med(e, c, lambda r: r["mops"]) for e, c, _ in w_list]
snp_w = [med(e, c, lambda r: r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / (r["rounds"] + r["warmup"])) for e, c, _ in w_list]
tad_w = [med(e, c, lambda r: r["card_pmu"]["tad"]["tad_req_occ"]["val"] / (r["card_pmu"]["wall_s"] * 2.0e9)) for e, c, _ in w_list]

ax.axvspan(3.5, 6.4, color="#fee2e2", alpha=0.55, zorder=0)
l1, = ax.plot(xs, mops_w, "-o", color="#1d4ed8", lw=2.2, ms=6, label="闭环吞吐 (M/s)")
for idx, (e, c, _) in enumerate(w_list):
    for r in by_key[(e, c)]:
        ax.scatter(idx, r["mops"], color="#1d4ed8", s=16, alpha=0.45, zorder=3)
ax.set_xticks(xs)
ax.set_xticklabels(w_labels)
ax.set_xlabel("并发窗口 W（WC4，4 卡核，T_pause = 0）")
ax.set_ylabel("吞吐 (M msg/s)", color="#1d4ed8")
ax.tick_params(axis="y", labelcolor="#1d4ed8")
ax.set_ylim(0, 27)

ax2 = ax.twinx()
ax2.spines["top"].set_visible(False)
l2, = ax2.plot(xs, snp_w, "--s", color="#dc2626", lw=2.0, ms=5.5, label="卡 TAD 嗅探 (次/条)")
l3, = ax2.plot(xs, tad_w, ":D", color="#059669", lw=2.0, ms=5.5, label="卡 TAD 在途 (条)")
ax2.set_ylabel("卡 TAD 嗅探 (次/条) / TAD 在途 (条)", color="#dc2626")
ax2.tick_params(axis="y", labelcolor="#dc2626")
ax2.set_ylim(0, 6.2)
ax2.grid(False)

ax.axvline(3.5, color="#991b1b", ls="--", lw=1.1, alpha=0.8)
ax.text(
    3.62,
    25.8,
    "W=48 坍缩：13.1 M/s 锁死\nTAD 在途反跌 1.03→0.15 条",
    fontsize=7.8,
    color="#991b1b",
    fontweight="bold",
    va="top",
    bbox=dict(boxstyle="round,pad=0.25", fc="#fff5f5", ec="#fca5a5"),
)
ax.set_title("B. WC4 随窗口 W 坍缩：TAD 在途反跌 5–10×")
ax.legend([l1, l2, l3], [l.get_label() for l in (l1, l2, l3)], loc="upper left", fontsize=7.8, frameon=True, facecolor="white")

# ---- Panel C: MRV-50 Pre-Touch Verification across seq=20512 (4 reps) ----
ax = axes[2]
rep_colors = ["#2563eb", "#0891b2", "#4f46e5", "#0284c7"]
for rep in range(4):
    csv_p = STAMP_DIR / f"prefault_wc4_w64_rep{rep}.csv"
    with csv_p.open() as f:
        hdr = f.readline().strip()
        parts = dict(tok.split("=") for tok in hdr.lstrip("# ").split())
        tsc_hz = float(parts["tsc_hz"])
        sb_t, sb_c, _ = [float(v) for v in parts["sb"].split(",")]
        sa_t, sa_c, _ = [float(v) for v in parts["sa"].split(",")]
        slope = (sa_t - sb_t) / (sa_c - sb_c)
        ns_h = 1e9 / tsc_hz
        rows = list(csv.DictReader(f))
    seqs, h2cs, c2hs = [], [], []
    step = 32
    for i in range(0, min(2500, len(rows)) - step, step):
        chunk = rows[i : i + step]
        seqs.append(int(chunk[step // 2]["seq"]))
        h2cs.append(statistics.median((sb_t + slope * (float(r["card_t_in"]) - sb_c) - float(r["send_tsc"])) * ns_h / 1000.0 for r in chunk))
        c2hs.append(statistics.median((float(r["seen_tsc"]) - (sb_t + slope * (float(r["card_t_out"]) - sb_c))) * ns_h / 1000.0 for r in chunk))
    ax.plot(seqs, h2cs, color=rep_colors[rep], lw=1.4, alpha=0.85, label="去程 h2c (4次重复)" if rep == 0 else None)
    ax.plot(seqs, c2hs, color="#16a34a", lw=1.3, alpha=0.75, label="回程 c2h (4次重复)" if rep == 0 else None)

for s_mark in [20512, 21024, 21536, 22048]:
    ax.axvline(s_mark, color="#dc2626" if s_mark == 20512 else "#94a3b8", ls="--", lw=1.2 if s_mark == 20512 else 0.8, alpha=0.8)
ax.text(
    20555,
    5.05,
    "seq=20512（原缺页翻转点）\n4/4 无尖峰、无翻转",
    fontsize=8.2,
    color="#991b1b",
    fontweight="bold",
    va="top",
    bbox=dict(boxstyle="round,pad=0.25", fc="#fff5f5", ec="#fca5a5"),
)
ax.set_xlim(20000, 22500)
ax.set_ylim(0, 5.3)
ax.set_xlabel("消息序号 seq（WC4 W=64，真 4 KiB 非零预触页）")
ax.set_ylabel("单向停留时间 (µs)")
ax.set_title("C. MRV-50 预触页复核：4/4 稳定在去程排队态")
ax.legend(loc="center right", fontsize=8, frameon=True, facecolor="white")

fig.suptitle("MRV-75 Part 1：硬件队列 X 光（MOVDIR64B + WC，64 B，cpu40 3.9 GHz，uncore 2.2 GHz，PMU 100% 无复用）", fontsize=11.5, fontweight="bold")
out = mrvplot.save(fig, "mrv-75-part1-xray")
print(f"Saved {out}")
