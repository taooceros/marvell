#!/usr/bin/env python3
"""MRV-137 Core Single-Variable Figure: Host Core Scaling H in {1, 2, 4} under
128 B Exclusive BAR4 Sub-Rings, C_line = 64 (16/card), W = 256 (64/QP), 256 B B = 8.
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
    x_mlines = [r * 4.0 / 1000.0 for r in rates]

    ce_w64 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["card_echo_4c_w64"]])
    h4_cap = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["h4_cl64_w256"]])

    series = [
        ("h1_cl64_shr", "H = 1 共享信用池 (对照)", "#868e96", "x", ":", [4000, 6000, 7000, 8000]),
        ("h1_cl64",     "H = 1 (每卡核 16 行)",    "#d9480f", "o", "-", rates),
        ("h2_cl64",     "H = 2 (每卡核 16 行)",    "#1971c2", "s", "-", rates),
        ("h4_cl64",     "H = 4 (1:1 绑核 16 行)",  "#2b8a3e", "^", "-", rates),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(15.8, 5.0))

    # ------------------------------------------------------------------
    # Panel (a): Achieved Throughput (M lines/s) vs Target Arrival Rate
    # ------------------------------------------------------------------
    ax = axes[0]
    ax.plot([15.0, 37.5], [15.0, 37.5], color="#ced4da", ls=":", lw=1.2, label="理想线性 (y = x)")
    ax.axhline(ce_w64, color="#495057", ls="--", lw=1.2, zorder=1)
    ax.text(15.6, ce_w64 + 0.55, f"4C card_echo (W=64): {ce_w64:.1f} M 行/s", fontsize=7.9, color="#495057")
    ax.axhline(h4_cap, color="#2b8a3e", ls="-.", lw=1.0, alpha=0.65, zorder=1)
    ax.text(15.6, h4_cap - 1.35, f"C_line = 64 信用上限: {h4_cap:.1f} M 行/s", fontsize=7.8, color="#2b8a3e")

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
        ax.plot(xs, ys, color=col, marker=mk, ls=ls, lw=2.0, ms=6.0, label=label, zorder=4)

    ax.set_xlabel("目标到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("实测端到端吞吐 (M 行/s)")
    ax.set_title("(a) 吞吐与容量边界 (B = 8, W = 256)")
    ax.set_xlim(15.0, 37.2)
    ax.set_ylim(14.5, 40.0)
    ax.legend(loc="lower right", fontsize=7.8, frameon=True, framealpha=0.93)

    # ------------------------------------------------------------------
    # Panel (b): End-to-End tot p99 & Datapath wire p99 (log scale)
    # ------------------------------------------------------------------
    ax = axes[1]
    ax.axvspan(h4_cap, 37.5, color="#fff5f5", alpha=0.85, zorder=0)
    ax.axvline(h4_cap, color="#c92a2a", ls=":", lw=1.1, zorder=1)
    ax.text(h4_cap + 0.35, 18.0, "过载区\n(λ > μ)", fontsize=7.4, color="#c92a2a", va="bottom")
    ax.axhline(25.0, color="#868e96", ls="--", lw=1.0, zorder=1)
    ax.text(15.5, 21.5, "25 µs 目标线", fontsize=7.6, color="#495057", va="top")

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
        # Filled markers below capacity, open markers in overload (λ > μ)
        ax.plot(xs, ys, color=col, ls=ls, lw=2.0, zorder=4)
        for x, y in zip(xs, ys):
            ov = (x > h4_cap and prefix != "h1_cl64_shr") or (prefix == "h1_cl64_shr" and x > 28.5)
            ax.plot(
                x, y, color=col, marker=mk, ls="None", ms=6.0,
                markerfacecolor="white" if ov else col, markeredgecolor=col,
                markeredgewidth=1.5 if ov else 0.8, zorder=5,
            )
        ax.plot([], [], color=col, marker=mk, ls=ls, lw=2.0, ms=5.8, label=label)

    # Overlay H=4 wire p99
    xs_w, ys_w = [], []
    for r in rates:
        k = f"h4_cl64_b8_r{r}"
        if k in cfgs:
            xs_w.append(r * 4.0 / 1000.0)
            ys_w.append(med([row["client"]["wire_p99_us"] for row in cfgs[k]]))
    ax.plot(xs_w, ys_w, color="#2b8a3e", ls="--", lw=1.4, alpha=0.85, zorder=3, label="H = 4 wire p99 (含 DDIO 暂留)")

    if "h1_cl64_b8_r7000" in cfgs and "h4_cl64_b8_r7000" in cfgs:
        y1 = med([r["client"]["tot_p99_us"] for r in cfgs["h1_cl64_b8_r7000"]])
        y4 = med([r["client"]["tot_p99_us"] for r in cfgs["h4_cl64_b8_r7000"]])
        ratio = y1 / max(y4, 0.1)
        ax.annotate(
            f"28M: H=1 {y1:.0f} →\nH=4 {y4:.1f} µs (−{ratio:.1f}×)",
            xy=(28.0, y4), xytext=(15.4, 150),
            fontsize=7.4, color="#2b8a3e", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", fc="#ebfbee", ec="#2b8a3e", lw=0.8),
            arrowprops=dict(arrowstyle="->", color="#2b8a3e", lw=1.0),
        )
    ax.set_yscale("log")
    ax.set_xlabel("目标到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("端到端 tot p99 / wire p99 (µs，对数轴)")
    ax.set_title("(b) B = 8 微突发尾延迟 (空心 = 过载 λ > μ)")
    ax.set_xlim(15.0, 37.2)
    ax.set_ylim(14.0, 4.0e5)
    ax.legend(loc="upper left", fontsize=7.3, frameon=True, framealpha=0.93)

    # ------------------------------------------------------------------
    # Panel (c): Card host_rtt p99 + Host CHA In-Flight (proves phase stability & zero backpressure)
    # ------------------------------------------------------------------
    ax = axes[2]
    for prefix, label, col, mk, ls, rs in [
        ("h1_cl64", "H = 1 hrtt p99 (16/卡)", "#d9480f", "o", "-", rates),
        ("h2_cl64", "H = 2 hrtt p99 (16/卡)", "#1971c2", "s", "-", rates),
        ("h4_cl64", "H = 4 hrtt p99 (16/卡)", "#2b8a3e", "^", "-", rates),
        ("h1_s128_cl0", "无整形 C=0 hrtt p99", "#868e96", "D", "--", [4000, 6000, 8000, 9000]),
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
        ("h1_s128_cl0", "#c92a2a", ":", "无整形 CHA 在途 (右轴)", [4000, 6000, 8000, 9000]),
        ("h4_cl64",     "#0ca678", ":", "H = 4 C=64 CHA 在途 (右轴)", rates),
    ]:
        xs, ys = [], []
        for r in rs:
            k = f"{prefix}_b8_r{r}"
            if k in cfgs:
                xs.append(r * 4.0 / 1000.0)
                ys.append(med([row["pmu"]["cha_inflight"] for row in cfgs[k]]))
        ax2.plot(xs, ys, color=col, ls=ls, lw=1.8, marker=".", ms=4.5, label=lbl, zorder=2)

    ax.set_xlabel("目标到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("卡测 host_rtt p99 (µs)")
    ax2.set_ylabel("主机 CHA 在途事务数 (条)", color="#c92a2a")
    ax2.tick_params(axis="y", labelcolor="#c92a2a")
    ax.set_title("(c) 慢态毛刺消除 (hrtt p99) 与 CHA 倒灌对比")
    ax.set_xlim(15.0, 37.2)
    ax.set_ylim(0, 82)
    ax2.set_ylim(0, 105)

    h1_l, l1 = ax.get_legend_handles_labels()
    h2_l, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1_l + h2_l, l1 + l2, loc="upper left", fontsize=7.0, frameon=True, framealpha=0.92)

    fig.suptitle(
        "MRV-137 核心单变量：固定 128 B 独占子环 + C_line = 64 (16/卡) + W = 256，切换主机核数 H ∈ {1, 2, 4} (256 B, B = 8, n = 3)",
        fontsize=10.3, fontweight="bold", y=0.985,
    )
    out = save(fig, "mrv-137-scale-h")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
