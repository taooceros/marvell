#!/usr/bin/env python3
"""MRV-137 Core Single-Variable Figure: Host Core Scaling H in {1, 2, 4} under
128 B Exclusive BAR4 Sub-Rings, C_line = 64 (16/card), W = 256 (64/QP), 256 B B = 8,
with full already-measured 1H unshaped control (h1_s128_cl0_b8) and wire vs tot decomposition.
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt

from mrvplot import save

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs/mrv137-2026-11-22/runs.jsonl"


def load_runs():
    rows = []
    with RUNS.open() as f:
        for line in f:
            r = json.loads(line)
            assert r.get("verified"), f"Unverified run: {r.get('cfg')} rep={r.get('rep')}"
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

    rates = [4000, 5000, 6000, 7000, 8000, 8500, 9000]
    unshaped_rates = [4000, 6000, 8000, 9000]

    ce_w256 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["card_echo_4c_w256"]])
    h4_cap = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["h4_cl64_w256"]])
    h4_cl0_w256 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["h4_s128_cl0_w256"]])
    ratio_w256 = h4_cap / ce_w256 * 100.0

    series = [
        ("h1_cl64_shr", "H = 1 共享信用池 (对照)", "#868e96", "x", ":",  [4000, 6000, 7000, 8000]),
        ("h1_s128_cl0", "H = 1 无整形 C_line = 0 (对照)", "#c92a2a", "D", "--", unshaped_rates),
        ("h1_cl64",     "H = 1 (每卡核 16 行)",    "#d9480f", "o", "-",  rates),
        ("h2_cl64",     "H = 2 (每卡核 16 行)",    "#1971c2", "s", "-",  rates),
        ("h4_cl64",     "H = 4 (1:1 绑核 16 行)",  "#2b8a3e", "^", "-",  rates),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(16.2, 5.2))

    # ------------------------------------------------------------------
    # Panel (a): Achieved Throughput (M lines/s) vs Target Arrival Rate
    # ------------------------------------------------------------------
    ax = axes[0]
    ax.plot([15.0, 37.5], [15.0, 37.5], color="#ced4da", ls=":", lw=1.2, label="理想线性 (y = x)")
    ax.axhline(h4_cap, color="#2b8a3e", ls="-.", lw=1.1, alpha=0.75, zorder=1)
    ax.text(
        15.4, h4_cap + 0.65,
        f"C_line = 64 锁步顶: {h4_cap:.2f} M 行/s (占同窗 W=256 card_echo {ratio_w256:.1f}%)",
        fontsize=7.3, color="#2b8a3e", fontweight="bold",
    )
    ax.text(
        15.4, 38.6,
        f"同窗 W=256 闭环上限: card_echo {ce_w256:.1f} / 无整形 {h4_cl0_w256:.1f} M 行/s",
        fontsize=7.3, color="#495057",
        bbox=dict(boxstyle="round,pad=0.22", fc="#f8f9fa", ec="#ced4da", lw=0.7),
    )

    for prefix, label, col, mk, ls, rs in series:
        xs, ys = [], []
        for r in rs:
            k = f"{prefix}_b8_r{r}"
            if k not in cfgs:
                continue
            xm = r * 4.0 / 1000.0
            pts = [row["client"]["achieved_mrps"] * 4.0 for row in cfgs[k]]
            xs.append(xm)
            ys.append(med(pts))
            ax.scatter([xm] * len(pts), pts, color=col, s=16, alpha=0.38, zorder=3)
        z = 5 if prefix == "h1_s128_cl0" else 4
        ax.plot(xs, ys, color=col, marker=mk, ls=ls, lw=2.0, ms=6.0, label=label, zorder=z)

    u36 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["h1_s128_cl0_b8_r9000"]])
    ax.annotate(
        f"1H 无整形 36M:\n{u36:.2f} M/s (未过载)",
        xy=(36.0, u36), xytext=(24.8, 36.1),
        fontsize=7.2, color="#c92a2a", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#c92a2a", lw=0.9),
    )

    ax.set_xlabel("目标到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("实测端到端吞吐 (M 行/s)")
    ax.set_title("(a) 吞吐与容量边界 (B = 8, W = 256)")
    ax.set_xlim(15.0, 37.2)
    ax.set_ylim(14.5, 40.6)
    ax.legend(loc="lower right", fontsize=7.4, frameon=True, framealpha=0.94)

    # ------------------------------------------------------------------
    # Panel (b): End-to-End tot p99 & Datapath wire p99 (log scale)
    # ------------------------------------------------------------------
    ax = axes[1]
    ax.axvspan(h4_cap, 37.5, color="#fff5f5", alpha=0.80, zorder=0)
    ax.axvline(h4_cap, color="#c92a2a", ls=":", lw=1.1, zorder=1)
    ax.text(h4_cap + 0.25, 15.5, "C=64 过载区\n(λ > μ)", fontsize=7.0, color="#c92a2a", va="bottom")
    ax.axhline(25.0, color="#868e96", ls="--", lw=1.0, zorder=1)
    ax.text(15.4, 21.8, "25 µs 目标线", fontsize=7.3, color="#495057", va="top")

    for prefix, label, col, mk, ls, rs in series:
        xs, ys = [], []
        for r in rs:
            k = f"{prefix}_b8_r{r}"
            if k not in cfgs:
                continue
            xm = r * 4.0 / 1000.0
            pts = [row["client"]["tot_p99_us"] for row in cfgs[k]]
            xs.append(xm)
            ys.append(med(pts))
            ax.scatter([xm] * len(pts), pts, color=col, s=14, alpha=0.35, zorder=3)
        ax.plot(xs, ys, color=col, ls=ls, lw=2.0, zorder=4)
        for x, y in zip(xs, ys):
            if prefix == "h1_s128_cl0":
                ov = False
            elif prefix == "h1_cl64_shr":
                ov = x > 28.5
            else:
                ov = x > h4_cap
            ax.plot(
                x, y, color=col, marker=mk, ls="None", ms=6.0,
                markerfacecolor="white" if ov else col, markeredgecolor=col,
                markeredgewidth=1.5 if ov else 0.8, zorder=5,
            )
        ax.plot([], [], color=col, marker=mk, ls=ls, lw=2.0, ms=5.8, label=label)

    # Overlay H=4 wire p99 (explicitly includes DDIO rx_ring credit wait)
    xs_w, ys_w = [], []
    for r in rates:
        k = f"h4_cl64_b8_r{r}"
        if k in cfgs:
            xs_w.append(r * 4.0 / 1000.0)
            ys_w.append(med([row["client"]["wire_p99_us"] for row in cfgs[k]]))
    ax.plot(xs_w, ys_w, color="#2b8a3e", ls="--", lw=1.5, alpha=0.90, zorder=3,
            label="H = 4 wire p99 (含 DDIO 暂留)")

    # Annotations computed from raw runs
    y1_28 = med([r["client"]["tot_p99_us"] for r in cfgs["h1_cl64_b8_r7000"]])
    y4_28 = med([r["client"]["tot_p99_us"] for r in cfgs["h4_cl64_b8_r7000"]])
    y4_32 = med([r["client"]["tot_p99_us"] for r in cfgs["h4_cl64_b8_r8000"]])
    w4_32 = med([r["client"]["wire_p99_us"] for r in cfgs["h4_cl64_b8_r8000"]])
    preq_32 = (y4_32 - w4_32) / y4_32 * 100.0
    u_32 = med([r["client"]["tot_p99_us"] for r in cfgs["h1_s128_cl0_b8_r8000"]])
    u_36 = med([r["client"]["tot_p99_us"] for r in cfgs["h1_s128_cl0_b8_r9000"]])

    ax.annotate(
        f"28M: H=1 {y1_28:.0f} →\nH=4 {y4_28:.1f} µs (−{y1_28/y4_28:.1f}×)",
        xy=(28.0, y4_28), xytext=(15.4, 155.0),
        fontsize=6.9, color="#2b8a3e", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.22", fc="#ebfbee", ec="#2b8a3e", lw=0.8),
        arrowprops=dict(arrowstyle="->", color="#2b8a3e", lw=0.9),
    )
    ax.annotate(
        f"32M H=4: {preq_32:.0f}% 为发送前排队\n(tot {y4_32:.0f} vs wire {w4_32:.1f} µs)",
        xy=(32.0, y4_32), xytext=(22.0, 920.0),
        fontsize=6.9, color="#1971c2", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.22", fc="#e7f5ff", ec="#1971c2", lw=0.8),
        arrowprops=dict(arrowstyle="->", color="#1971c2", lw=0.9),
    )
    ax.annotate(
        f"1H 无整形: 32M {u_32:.1f} µs (低 {y4_32/u_32:.1f}×), 36M 稳态 {u_36:.0f} µs",
        xy=(32.0, u_32), xytext=(15.4, 11.2),
        fontsize=6.6, color="#c92a2a", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.20", fc="#fff5f5", ec="#c92a2a", lw=0.8),
        arrowprops=dict(arrowstyle="->", color="#c92a2a", lw=0.9),
    )

    ax.set_yscale("log")
    ax.set_xlabel("目标到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("端到端 tot p99 / wire p99 (µs，对数轴)")
    ax.set_title("(b) B = 8 尾延迟：tot p99 vs wire p99 (空心=过载)")
    ax.set_xlim(15.0, 37.2)
    ax.set_ylim(9.2, 5.5e5)
    ax.legend(loc="upper left", fontsize=6.7, frameon=True, framealpha=0.93)

    # ------------------------------------------------------------------
    # Panel (c): Card host_rtt p99 + Host CHA In-Flight (trade-off vs unshaped)
    # ------------------------------------------------------------------
    ax = axes[2]
    for prefix, label, col, mk, ls, rs in [
        ("h1_cl64",     "H = 1 hrtt p99 (16/卡)",   "#d9480f", "o", "-",  rates),
        ("h2_cl64",     "H = 2 hrtt p99 (16/卡)",   "#1971c2", "s", "-",  rates),
        ("h4_cl64",     "H = 4 hrtt p99 (16/卡)",   "#2b8a3e", "^", "-",  rates),
        ("h1_s128_cl0", "1H 无整形 C=0 hrtt p99",   "#c92a2a", "D", "--", unshaped_rates),
    ]:
        xs, ys = [], []
        for r in rs:
            k = f"{prefix}_b8_r{r}"
            if k not in cfgs:
                continue
            xm = r * 4.0 / 1000.0
            pts = [row["card"]["host_rtt_p99_us"] for row in cfgs[k]]
            xs.append(xm)
            ys.append(med(pts))
            ax.scatter([xm] * len(pts), pts, color=col, s=14, alpha=0.35, zorder=3)
        ax.plot(xs, ys, color=col, marker=mk, ls=ls, lw=1.9, ms=5.8, label=label, zorder=4)

    # Secondary axis: Host CHA in-flight for H=4 (C=64) vs unshaped (C=0)
    ax2 = ax.twinx()
    ax2.spines["top"].set_visible(False)
    for prefix, col, ls, lbl, rs in [
        ("h1_s128_cl0", "#862e9c", ":", "1H 无整形 CHA 在途 (右轴)", unshaped_rates),
        ("h4_cl64",     "#0ca678", ":", "H = 4 C=64 CHA 在途 (右轴)", rates),
    ]:
        xs, ys = [], []
        for r in rs:
            k = f"{prefix}_b8_r{r}"
            if k in cfgs:
                xs.append(r * 4.0 / 1000.0)
                ys.append(med([row["pmu"]["cha_inflight"] for row in cfgs[k]]))
        ax2.plot(xs, ys, color=col, ls=ls, lw=1.9, marker=".", ms=4.8, label=lbl, zorder=2)

    u_cha32 = med([r["pmu"]["cha_inflight"] for r in cfgs["h1_s128_cl0_b8_r8000"]])
    h4_cha32 = med([r["pmu"]["cha_inflight"] for r in cfgs["h4_cl64_b8_r8000"]])
    ax2.annotate(
        f"无整形 32M CHA={u_cha32:.1f}\n(H=4 C=64 仅 {h4_cha32:.2f})",
        xy=(32.0, u_cha32), xytext=(25.5, 95.5),
        fontsize=6.8, color="#862e9c", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#862e9c", lw=0.9),
    )

    ax.set_xlabel("目标到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("卡测 host_rtt p99 (µs)")
    ax2.set_ylabel("主机 CHA 在途事务数 (条)", color="#862e9c")
    ax2.tick_params(axis="y", labelcolor="#862e9c")
    ax.set_title("(c) 慢态毛刺消除与无整形 CHA 倒灌代价")
    ax.set_xlim(15.0, 37.2)
    ax.set_ylim(0, 84)
    ax2.set_ylim(0, 114)

    h1_l, l1 = ax.get_legend_handles_labels()
    h2_l, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1_l + h2_l, l1 + l2, loc="upper left", fontsize=6.7, frameon=True, framealpha=0.93)

    fig.suptitle(
        "MRV-137 核心单变量：128 B 独占子环 + C_line = 64 (16/卡) + W = 256 下主机核数 H ∈ {1, 2, 4} 与无整形对照 (256 B, B = 8, n = 3)",
        fontsize=10.1, fontweight="bold", y=0.985,
    )
    out = save(fig, "mrv-137-scale-h")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
