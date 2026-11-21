#!/usr/bin/env python3
"""MRV-135 Core Single-Variable Figure: 256 B B=8 Micro-Bursts (4C eth0 RSS, W=64)
Unshaped (C_line=0) vs Card-Cursor In-Flight Line Credit Shaping (C_line=40/48/64).
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

RUNS = Path("/home/hongtao/dev/marvell-mrv135/logs/mrv135-2026-11-21/runs.jsonl")


def load_runs():
    rows = []
    with RUNS.open() as f:
        for line in f:
            r = json.loads(line)
            assert r.get("verified"), f"Unverified run in {RUNS}: {r.get('cfg')} rep={r.get('rep')}"
            rows.append(r)
    return rows


def by_cfg(rows):
    d = {}
    for r in rows:
        d.setdefault(r["cfg"], []).append(r)
    return d


def med(vals):
    return statistics.median(vals)


def main():
    rows = load_runs()
    cfgs = by_cfg(rows)

    rates = [2000, 3000, 3500, 4000, 4500, 5000, 5500, 6000]
    x_mlines = [r * 4.0 / 1000.0 for r in rates]

    series = [
        ("cl0", "无整形 (仅 W = 64)", "#c92a2a", "o", "-"),
        ("cl40", "行信用整形 C_line = 40", "#1971c2", "s", "-"),
        ("cl48", "行信用整形 C_line = 48", "#2b8a3e", "^", "--"),
        ("cl64", "边界对照 C_line = 64", "#e67700", "D", ":"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(15.2, 4.7))

    # Panel (a): Achieved Line Rate (M lines/s) vs Target Line Rate
    ax = axes[0]
    ax.plot([7.5, 24.5], [7.5, 24.5], color="#adb5bd", ls=":", lw=1.2, label="理想线性 (y = x)")
    ax.axhline(13.4, color="#868e96", ls="--", lw=1.1)
    ax.text(8.0, 13.75, "13.4 M 行/s 慢崖线", fontsize=8.5, color="#495057")

    for tag, label, col, mk, ls in series:
        xs, ys = [], []
        for r, xm in zip(rates, x_mlines):
            k = f"sub_{tag}_b8_r{r}"
            if k not in cfgs:
                continue
            pts = [row["client"]["achieved_mrps"] * 4.0 for row in cfgs[k]]
            xs.append(xm)
            ys.append(med(pts))
            ax.scatter([xm] * len(pts), pts, color=col, s=16, alpha=0.45, zorder=3)
        ax.plot(xs, ys, color=col, marker=mk, ls=ls, lw=2.0, ms=6, label=label, zorder=4)

    ax.set_xlabel("目标到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("实测端到端吞吐 (M 行/s)")
    ax.set_title("(a) 吞吐跨慢崖能力 (B = 8, W = 64)")
    ax.set_xlim(7.2, 24.8)
    ax.set_ylim(6.5, 25.5)
    ax.legend(loc="upper left", fontsize=8.2, frameon=True, framealpha=0.92)

    # Panel (b): True End-to-End tot p99 (us, log scale) vs Target Line Rate
    ax = axes[1]
    ax.axvline(13.4, color="#868e96", ls="--", lw=1.1)
    ax.text(13.65, 14.0, "13.4 M 行/s", fontsize=8.5, color="#495057")

    for tag, label, col, mk, ls in series:
        xs, ys = [], []
        for r, xm in zip(rates, x_mlines):
            k = f"sub_{tag}_b8_r{r}"
            if k not in cfgs:
                continue
            pts = [row["client"]["tot_p99_us"] for row in cfgs[k]]
            xs.append(xm)
            ys.append(med(pts))
            ax.scatter([xm] * len(pts), pts, color=col, s=16, alpha=0.45, zorder=3)
        ax.plot(xs, ys, color=col, marker=mk, ls=ls, lw=2.0, ms=6, label=label, zorder=4)

    # Annotate key 4.0 Mreq/s (16 M lines/s) drop
    if "sub_cl0_b8_r4000" in cfgs and "sub_cl40_b8_r4000" in cfgs:
        u16 = med([r["client"]["tot_p99_us"] for r in cfgs["sub_cl0_b8_r4000"]])
        c16 = med([r["client"]["tot_p99_us"] for r in cfgs["sub_cl40_b8_r4000"]])
        ax.annotate(
            f"16 M 行/s:\n{u16/1000:.0f} ms → {c16:.1f} µs",
            xy=(16.0, c16),
            xytext=(14.2, 3200),
            fontsize=8.2,
            color="#1971c2",
            fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#1971c2", lw=1.2),
            bbox=dict(boxstyle="round,pad=0.25", fc="#e7f5ff", ec="#1971c2", lw=0.8),
        )

    ax.set_yscale("log")
    ax.set_xlabel("目标到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("真实端到端 tot p99 (µs，对数轴)")
    ax.set_title("(b) 真实端到端尾延迟 tot p99")
    ax.set_xlim(7.2, 24.8)
    ax.set_ylim(10, 1e6)

    # Panel (c): Card-side CNTVCT host_rtt p99 + Host IIO/CHA Write Ratio (inset/dual)
    ax = axes[2]
    ax.axvline(13.4, color="#868e96", ls="--", lw=1.1)
    for tag, label, col, mk, ls in series:
        xs, ys = [], []
        for r, xm in zip(rates, x_mlines):
            k = f"sub_{tag}_b8_r{r}"
            if k not in cfgs:
                continue
            pts = [row["card"]["host_rtt_p99_us"] for row in cfgs[k]]
            xs.append(xm)
            ys.append(med(pts))
            ax.scatter([xm] * len(pts), pts, color=col, s=16, alpha=0.45, zorder=3)
        ax.plot(xs, ys, color=col, marker=mk, ls=ls, lw=2.0, ms=6, label=label, zorder=4)

    # Secondary axis: IIO CPU Write / BAR4 line ratio for cl0 & cl40
    ax2 = ax.twinx()
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(True)
    ax2.spines["right"].set_color("#868e96")
    for tag, col in [("cl0", "#c92a2a"), ("cl40", "#1971c2")]:
        xs, r_iio, r_cha = [], [], []
        for r, xm in zip(rates, x_mlines):
            k = f"sub_{tag}_b8_r{r}"
            if k not in cfgs:
                continue
            xs.append(xm)
            r_iio.append(med([row["steady_window"]["iio_cpu_wr_ratio"] for row in cfgs[k]]))
            r_cha.append(med([row["steady_window"]["cha_wcilf_ratio"] for row in cfgs[k]]))
        ax2.plot(xs, r_iio, color=col, ls=":", lw=1.4, alpha=0.75,
                 label=f"IIO/行 ({'无整形' if tag=='cl0' else 'C=40'})")
        ax2.plot(xs, r_cha, color=col, ls="-.", lw=1.1, alpha=0.60,
                 label=f"CHA/行 ({'无整形' if tag=='cl0' else 'C=40'})")
    ax2.set_ylabel("IIO & CHA WCILF / BAR4 行比值", color="#495057", fontsize=8.6)
    ax2.set_ylim(0.5, 1.5)
    ax2.tick_params(axis="y", colors="#495057")

    ax.set_xlabel("目标到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("卡侧 CNTVCT 主机往返 p99 (µs)")
    ax.set_title("(c) PCIe/BAR4 往返 p99 与 IIO 行倍率")
    ax.set_xlim(7.2, 24.8)
    ax.set_ylim(0, 17.8)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2[:2], l1 + ["IIO/CHA 行比 = 1.00× (无整形)", "IIO/CHA 行比 = 1.00× (C=40)"],
              loc="lower right", fontsize=7.2, frameon=True, framealpha=0.93)

    fig.suptitle(
        "MRV-135 核心单变量：真实双机 100GbE 4C RSS (256 B, B = 8, W = 64) 无整形 vs 卡侧游标行信用整形 (n = 3 交错全零错)",
        fontsize=11.5,
        y=0.99,
    )
    out = save(fig, "mrv-135-burst-cliff")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
