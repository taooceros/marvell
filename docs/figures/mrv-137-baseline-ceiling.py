#!/usr/bin/env python3
"""MRV-137 Figure 2: Single-Variable 1H Baseline Upgrade Ladder, Closed-Loop W Scaling,
and C_line Credit-Loop Ceiling vs 48 M lines/s Unshaped Hardware Headroom.
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

    fig, axes = plt.subplots(1, 3, figsize=(16.0, 5.0))

    # ------------------------------------------------------------------
    # Panel (a): Step-by-Step Single-Variable Upgrade Ladder (W=256, C_line=64)
    # ------------------------------------------------------------------
    ax = axes[0]
    ladder = [
        ("h1_s64_cl64_txp_shr_w256",  "1H 紧排64B\n+tx_pool\n共享64", "#c92a2a"),
        ("h1_s128_cl64_txp_shr_w256", "1H 128B步长\n+tx_pool\n共享64", "#e67700"),
        ("h1_s128_cl64_shr_w256",     "1H 128B步长\n旁路tx_pool\n共享64", "#f08c00"),
        ("h1_cl64_w256",              "1H 128B步长\n旁路tx_pool\n每卡16行", "#1971c2"),
        ("h4_cl64_w256",              "4H 128B步长\n旁路tx_pool\n1:1每卡16", "#2b8a3e"),
    ]
    xs = list(range(len(ladder)))
    vals = []
    cols = []
    labels = []
    llcs = []
    sbs = []
    for k, lbl, col in ladder:
        rs = cfgs[k]
        vals.append(med([r["client"]["achieved_mrps"] * 4.0 for r in rs]))
        llcs.append(med([r["pmu"]["active_llc_load_misses"] / r["pmu"]["dt_s"] / 1e6 for r in rs]))
        sbs.append(med([r["pmu"]["active_sb_stall_mean_pct"] for r in rs]))
        cols.append(col)
        labels.append(lbl)

    bars = ax.bar(xs, vals, width=0.60, color=cols, alpha=0.88, edgecolor="#212529", lw=0.7, zorder=3)
    for i, (k, _, col) in enumerate(ladder):
        pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[k]]
        ax.scatter([i] * len(pts), pts, color="#212529", s=16, zorder=4)
        if vals[i] >= 31.0:
            ax.text(
                i, vals[i] - 1.4,
                f"{vals[i]:.1f}\nLLC:{llcs[i]:.1f}M\nSB:{sbs[i]:.1f}%",
                ha="center", va="top", fontsize=7.2, fontweight="bold", color="#ffffff", zorder=5,
            )
        else:
            ax.text(
                i, vals[i] + 0.8,
                f"{vals[i]:.1f}\nLLC:{llcs[i]:.1f}M\nSB:{sbs[i]:.1f}%",
                ha="center", va="bottom", fontsize=7.2, fontweight="bold", color="#212529", zorder=5,
            )
    ce_w64 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["card_echo_4c_w64"]])
    ax.axhline(ce_w64, color="#495057", ls="--", lw=1.1, zorder=2)
    ax.text(-0.35, ce_w64 + 0.9, f"4C card_echo W=64 ({ce_w64:.1f} M/s)", fontsize=7.6, color="#495057")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=7.5)
    ax.set_ylabel("闭环吞吐 (M 行/s，W = 256, C_line = 64)")
    ax.set_title("(a) 1H→4H 单变量基线消融阶梯")
    ax.set_ylim(0, 44.5)

    # ------------------------------------------------------------------
    # Panel (b): Closed-Loop W Scaling (H=1/2/4 C_line=64 vs Unshaped C_line=0)
    # ------------------------------------------------------------------
    ax = axes[1]
    ws_cl = [32, 64, 128, 256]
    for h, col, mk, lbl in [
        (1, "#d9480f", "o", "H = 1, C_line = 64 (16/卡)"),
        (2, "#1971c2", "s", "H = 2, C_line = 64 (16/卡)"),
        (4, "#2b8a3e", "^", "H = 4, C_line = 64 (16/卡)"),
    ]:
        ys = []
        for w in ws_cl:
            pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[f"h{h}_cl64_w{w}"]]
            ys.append(med(pts))
            ax.scatter([w] * len(pts), pts, color=col, s=15, alpha=0.38, zorder=3)
        ax.plot(ws_cl, ys, color=col, marker=mk, lw=2.0, ms=6.0, label=lbl, zorder=4)

    ws_u = [64, 128, 256]
    ys_u = []
    for w in ws_u:
        pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[f"h1_s128_cl0_w{w}"]]
        ys_u.append(med(pts))
        ax.scatter([w] * len(pts), pts, color="#c92a2a", s=15, alpha=0.38, zorder=3)
    ax.plot(ws_u, ys_u, color="#c92a2a", marker="D", ls="--", lw=1.9, ms=5.8, label="H = 1 无整形 C_line = 0", zorder=4)

    ce_w256 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["card_echo_4c_w256"]])
    ax.scatter([64, 256], [ce_w64, ce_w256], color="#495057", marker="*", s=110, zorder=6, label="4C card_echo 物理上限")
    ax.annotate(f"card_echo W=256\n{ce_w256:.1f} M/s", xy=(256, ce_w256), xytext=(178, ce_w256 - 4.5),
                fontsize=7.5, color="#495057", arrowprops=dict(arrowstyle="->", color="#495057", lw=0.9))
    ax.annotate(f"无整形 W=128 慢崖\n{ys_u[1]:.1f} M/s (CHA=36)", xy=(128, ys_u[1]), xytext=(136, 8.2),
                fontsize=7.4, color="#c92a2a", arrowprops=dict(arrowstyle="->", color="#c92a2a", lw=0.9))

    ax.set_xlabel("客户端总并发窗口 W (4 QP 均分)")
    ax.set_ylabel("闭环吞吐 (M 行/s)")
    ax.set_title("(b) 闭环吞吐 vs W (128 B 独占子环)")
    ax.set_xticks([32, 64, 128, 256])
    ax.set_xlim(20, 272)
    ax.set_ylim(5, 62)
    ax.legend(loc="upper left", fontsize=7.3, frameon=True, framealpha=0.93)

    # ------------------------------------------------------------------
    # Panel (c): C_line Ceiling Sweep at W=256 (H=1 vs H=4, proving credit-loop ceiling)
    # ------------------------------------------------------------------
    ax = axes[2]
    h4_cls = [
        (48,  "h4_s128_cl48_pcc_w256"),
        (64,  "h4_cl64_w256"),
        (80,  "h4_s128_cl80_pcc_w256"),
        (96,  "h4_s128_cl96_pcc_w256"),
        (128, "h4_s128_cl128_pcc_w256"),
    ]
    h1_cls = [
        (48, "h1_s128_cl48_pcc_w256"),
        (64, "h1_cl64_w256"),
        (80, "h1_s128_cl80_pcc_w256"),
    ]
    xs4, ys4 = [], []
    for cl, k in h4_cls:
        pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[k]]
        xs4.append(cl); ys4.append(med(pts))
        ax.scatter([cl] * len(pts), pts, color="#2b8a3e", s=16, alpha=0.40, zorder=3)
    ax.plot(xs4, ys4, color="#2b8a3e", marker="^", lw=2.1, ms=6.5, label="H = 4 (1:1 绑核，每卡 C/4)", zorder=4)

    xs1, ys1 = [], []
    for cl, k in h1_cls:
        pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[k]]
        xs1.append(cl); ys1.append(med(pts))
        ax.scatter([cl] * len(pts), pts, color="#d9480f", s=16, alpha=0.40, zorder=3)
    ax.plot(xs1, ys1, color="#d9480f", marker="o", ls="--", lw=2.0, ms=6.2, label="H = 1 (单核轮询，每卡 C/4)", zorder=4)

    h4_cl0 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["h4_s128_cl0_w256"]])
    h1_cl0 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["h1_s128_cl0_w256"]])
    ax.axhline(h4_cl0, color="#1971c2", ls="-.", lw=1.2, zorder=2)
    ax.text(46, h4_cl0 + 0.9, f"无整形 W=256 物理通路裕量: H=1 {h1_cl0:.1f} / H=4 {h4_cl0:.1f} M/s", fontsize=7.4, color="#1971c2")
    ax.axhline(ce_w64, color="#495057", ls="--", lw=1.0, zorder=1)
    ax.text(46, ce_w64 + 0.7, f"card_echo W=64 ({ce_w64:.1f})", fontsize=7.4, color="#495057")

    if len(ys1) >= 3:
        ax.annotate(
            f"H=1 在 C=80 跌回慢态\n({ys1[2]:.1f} M/s, hrtt 67 µs)",
            xy=(80, ys1[2]), xytext=(46, 20.5),
            fontsize=7.4, color="#d9480f", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", fc="#fff4e6", ec="#d9480f", lw=0.8),
            arrowprops=dict(arrowstyle="->", color="#d9480f", lw=1.0),
        )
    ax.annotate(
        f"H=4 C=128 跨过\n36.8 线 ({ys4[-1]:.1f} M/s)",
        xy=(128, ys4[-1]), xytext=(86, 41.5),
        fontsize=7.5, color="#2b8a3e", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#2b8a3e", lw=1.0),
    )

    ax.set_xlabel("全局在途行信用上限 C_line (行，每卡核 C_line / 4)")
    ax.set_ylabel("闭环吞吐 (M 行/s，W = 256)")
    ax.set_title("(c) C_line 信用环路天花板 vs 通路裕量")
    ax.set_xticks([48, 64, 80, 96, 128])
    ax.set_xlim(42, 134)
    ax.set_ylim(14, 53)
    ax.legend(loc="lower right", fontsize=7.4, frameon=True, framealpha=0.93)

    fig.suptitle(
        "MRV-137 基线消融与天花板拆解：128 B 独占子环 + 旁路 tx_pool + 信用环路边界 (W = 256, n = 3)",
        fontsize=10.3, fontweight="bold", y=0.985,
    )
    out = save(fig, "mrv-137-baseline-ceiling")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
