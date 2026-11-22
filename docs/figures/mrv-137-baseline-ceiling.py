#!/usr/bin/env python3
"""MRV-137 Figure 2: Single-Variable 1H Baseline Upgrade Ladder, Same-Window Closed-Loop W
Comparison vs 4C card_echo, and C_line Lockstep Ceiling (true T_cycle = C_line / mu vs post-write artifact).
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

    ce_w64 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["card_echo_4c_w64"]])
    ce_w256 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["card_echo_4c_w256"]])
    h4_w64 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["h4_cl64_w64"]])
    h4_w256 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["h4_cl64_w256"]])
    h4_cl0 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["h4_s128_cl0_w256"]])
    h1_cl0 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["h1_s128_cl0_w256"]])

    fig, axes = plt.subplots(1, 3, figsize=(16.4, 5.2))

    # ------------------------------------------------------------------
    # Panel (a): Step-by-Step Baseline Ablation Ladder (W=256, C_line=64)
    # ------------------------------------------------------------------
    ax = axes[0]
    ladder = [
        ("h1_s64_cl64_txp_shr_w256",  "1H 紧排64B*\n+tx_pool\n共享64", "#c92a2a"),
        ("h1_s128_cl64_txp_shr_w256", "1H 128B步长\n+tx_pool\n共享64", "#e67700"),
        ("h1_s128_cl64_shr_w256",     "1H 128B步长\n旁路tx_pool\n共享64", "#f08c00"),
        ("h1_cl64_w256",              "1H 128B步长\n旁路tx_pool\n每卡16行", "#1971c2"),
        ("h4_cl64_w256",              "4H 128B步长\n旁路tx_pool\n1:1每卡16", "#2b8a3e"),
    ]
    xs = list(range(len(ladder)))
    vals, cols, labels, llcs, sbs = [], [], [], [], []
    for k, lbl, col in ladder:
        rs = cfgs[k]
        vals.append(med([r["client"]["achieved_mrps"] * 4.0 for r in rs]))
        llcs.append(med([r["pmu"]["active_llc_load_misses"] / r["pmu"]["dt_s"] / 1e6 for r in rs]))
        sbs.append(med([r["pmu"]["active_sb_stall_mean_pct"] for r in rs]))
        cols.append(col)
        labels.append(lbl)

    ax.bar(xs, vals, width=0.60, color=cols, alpha=0.88, edgecolor="#212529", lw=0.7, zorder=3)
    for i, (k, _, _col) in enumerate(ladder):
        pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[k]]
        ax.scatter([i] * len(pts), pts, color="#212529", s=16, zorder=4)
        if vals[i] >= 31.0:
            ax.text(
                i, vals[i] - 1.5,
                f"{vals[i]:.1f}\nLLC:{llcs[i]:.1f}M\nSB:{sbs[i]:.1f}%",
                ha="center", va="top", fontsize=7.1, fontweight="bold", color="#ffffff", zorder=5,
            )
        else:
            ax.text(
                i, vals[i] + 0.9,
                f"{vals[i]:.1f}\nLLC:{llcs[i]:.1f}M\nSB:{sbs[i]:.1f}%",
                ha="center", va="bottom", fontsize=7.1, fontweight="bold", color="#212529", zorder=5,
            )

    ax.axhline(h4_cl0, color="#1971c2", ls="-.", lw=1.1, zorder=2)
    ax.text(-0.42, h4_cl0 + 0.9, f"同窗 W=256 无整形: {h4_cl0:.1f} M/s (card_echo = {ce_w256:.1f})",
            fontsize=7.3, color="#1971c2")
    ax.text(-0.42, 42.0,
            f"同窗比: 33.5 / 57.5 = {h4_w256/ce_w256*100:.1f}%  (*64B 起点为慢态点, MRV-135 C=48 为 22.5)",
            fontsize=6.7, color="#495057",
            bbox=dict(boxstyle="round,pad=0.22", fc="#f8f9fa", ec="#ced4da", lw=0.7))

    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=7.3)
    ax.set_ylabel("闭环吞吐 (M 行/s，W = 256, C_line = 64)")
    ax.set_title("(a) 1H→4H 基线消融阶梯 (W = 256)")
    ax.set_ylim(0, 54.0)

    # ------------------------------------------------------------------
    # Panel (b): Closed-Loop W Scaling (Same-Window Comparison vs card_echo)
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
    ax.scatter([256], [h4_cl0], color="#862e9c", marker="P", s=70, zorder=6, label=f"H = 4 无整形 W=256 ({h4_cl0:.1f})")

    ax.scatter([64, 256], [ce_w64, ce_w256], color="#495057", marker="*", s=125, zorder=6, label="4C card_echo 同窗对照")

    ax.annotate(
        f"card_echo W=64: {ce_w64:.1f} (窗口顶)\n同窗 H=4 占 {h4_w64/ce_w64*100:.1f}%",
        xy=(64, ce_w64), xytext=(86, 42.5),
        fontsize=7.1, color="#212529", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.22", fc="#f8f9fa", ec="#868e96", lw=0.7),
        arrowprops=dict(arrowstyle="->", color="#495057", lw=0.9),
    )
    ax.annotate(
        f"card_echo W=256: {ce_w256:.1f}\n同窗 H=4 占 {h4_w256/ce_w256*100:.1f}%",
        xy=(256, ce_w256), xytext=(156, 54.0),
        fontsize=7.1, color="#212529", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.22", fc="#f8f9fa", ec="#868e96", lw=0.7),
        arrowprops=dict(arrowstyle="->", color="#495057", lw=0.9),
    )
    ax.annotate(
        f"无整形 W=128 慢崖\n{ys_u[1]:.1f} M/s (CHA=36)",
        xy=(128, ys_u[1]), xytext=(142, 8.2),
        fontsize=7.1, color="#c92a2a",
        arrowprops=dict(arrowstyle="->", color="#c92a2a", lw=0.9),
    )

    ax.set_xlabel("客户端总并发窗口 W (4 QP 均分)")
    ax.set_ylabel("闭环吞吐 (M 行/s)")
    ax.set_title("(b) 闭环吞吐 vs W (同窗对比 card_echo)")
    ax.set_xticks([32, 64, 128, 256])
    ax.set_xlim(20, 274)
    ax.set_ylim(5, 63)
    ax.legend(loc="upper left", fontsize=6.7, frameon=True, framealpha=0.93)

    # ------------------------------------------------------------------
    # Panel (c): C_line Lockstep Ceiling & True T_cycle (W=256 closed-loop only)
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
    xs4, ys4, tcyc4 = [], [], []
    for cl, k in h4_cls:
        pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[k]]
        mu = med(pts)
        xs4.append(cl); ys4.append(mu); tcyc4.append(cl / mu)
        ax.scatter([cl] * len(pts), pts, color="#2b8a3e", s=16, alpha=0.40, zorder=3)
    ax.plot(xs4, ys4, color="#2b8a3e", marker="^", lw=2.1, ms=6.5, label="H = 4 吞吐 (左轴，仅 W=256 闭环)", zorder=4)

    xs1, ys1 = [], []
    for cl, k in h1_cls:
        pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[k]]
        xs1.append(cl); ys1.append(med(pts))
        ax.scatter([cl] * len(pts), pts, color="#d9480f", s=16, alpha=0.40, zorder=3)
    ax.plot(xs1, ys1, color="#d9480f", marker="o", ls="--", lw=2.0, ms=6.2, label="H = 1 吞吐 (左轴)", zorder=4)

    ax.axhline(h4_cl0, color="#1971c2", ls="-.", lw=1.1, zorder=2)
    ax.text(44, h4_cl0 + 0.85, f"无整形 W=256 裕量: H=1 {h1_cl0:.1f} / H=4 {h4_cl0:.1f} (card_echo {ce_w256:.1f})",
            fontsize=6.9, color="#1971c2")

    # Right axis: true lockstep T_cycle = C_line / mu
    ax2 = ax.twinx()
    ax2.spines["top"].set_visible(False)
    ax2.plot(xs4, tcyc4, color="#862e9c", marker="s", ls=":", lw=1.9, ms=5.5,
             label="H = 4 真实锁步 T_cycle = C_line / μ (右轴)", zorder=3)
    for cl, tc in zip(xs4, tcyc4):
        dy = 0.16 if cl != 64 else -0.25
        ax2.text(cl, tc + dy, f"{tc:.2f}µs", ha="center", va="center", fontsize=6.9, color="#862e9c", fontweight="bold")

    ax.annotate(
        f"H=1 在 C=80 跌慢态\n({ys1[2]:.1f} M/s, hrtt 67 µs)",
        xy=(80, ys1[2]), xytext=(45, 22.8),
        fontsize=6.9, color="#d9480f", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.22", fc="#fff4e6", ec="#d9480f", lw=0.8),
        arrowprops=dict(arrowstyle="->", color="#d9480f", lw=0.9),
    )
    ax.annotate(
        f"C=128: {ys4[-1]:.1f} M/s\n(仅闭环, 未测 B=8/CBR)",
        xy=(128, ys4[-1]), xytext=(68, 42.2),
        fontsize=6.9, color="#2b8a3e", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.20", fc="#ebfbee", ec="#2b8a3e", lw=0.7),
        arrowprops=dict(arrowstyle="->", color="#2b8a3e", lw=0.9),
    )
    ax2.text(
        45, 0.25,
        "Step 2b 置外致整窗锁步 (cw/RPC = 1/K):\nC=64 实为 T_cycle = 1.91 µs (非写后采样 1.19 µs)",
        fontsize=6.7, color="#862e9c",
        bbox=dict(boxstyle="round,pad=0.22", fc="#f8f0fc", ec="#862e9c", lw=0.7),
    )

    ax.set_xlabel("全局在途行信用上限 C_line (行，每卡核 C_line / 4)")
    ax.set_ylabel("闭环吞吐 (M 行/s，W = 256)")
    ax2.set_ylabel("整窗锁步周期 T_cycle (µs)", color="#862e9c")
    ax2.tick_params(axis="y", labelcolor="#862e9c")
    ax.set_title("(c) C_line 整窗锁步天花板与 T_cycle")
    ax.set_xticks([48, 64, 80, 96, 128])
    ax.set_xlim(42, 134)
    ax.set_ylim(14, 53.5)
    ax2.set_ylim(0, 4.3)

    h1_l, l1 = ax.get_legend_handles_labels()
    h2_l, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1_l + h2_l, l1 + l2, loc="lower right", bbox_to_anchor=(1.0, 0.16), fontsize=6.5, frameon=True, framealpha=0.93)

    fig.suptitle(
        "MRV-137 基线消融、同窗比核正与整窗锁步天花板 (128 B 独占子环 + 旁路 tx_pool, n = 3)",
        fontsize=10.2, fontweight="bold", y=0.985,
    )
    out = save(fig, "mrv-137-baseline-ceiling")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
