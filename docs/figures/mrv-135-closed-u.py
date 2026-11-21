#!/usr/bin/env python3
"""MRV-135 Closed-Loop Scaling, C_line Inverted-U Window, and Intra-RPC Stride Diagnostic."""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt

from mrvplot import save

RUNS = Path("/home/hongtao/dev/marvell-mrv135/logs/mrv135-2026-11-21/runs.jsonl")


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

    fig, axes = plt.subplots(1, 3, figsize=(15.4, 4.7))

    # Panel (a): Closed-loop W=4..128 throughput (M lines/s) + CHA inflight annotation
    ax = axes[0]
    ws_all = [4, 8, 16, 24, 32, 48, 64, 128]
    ws_c = [32, 48, 64, 128]

    for tag, ws, label, col, mk, ls in [
        ("sub_cl0", ws_all, "无整形 (C_line = 0)", "#c92a2a", "o", "-"),
        ("sub_cl40", ws_c, "C_line = 40 (卡游标)", "#1971c2", "s", "-"),
        ("sub_cl48", ws_c, "C_line = 48 (卡游标)", "#2b8a3e", "^", "--"),
    ]:
        xs, ys = [], []
        for w in ws:
            k = f"{tag}_w{w}"
            if k not in cfgs:
                continue
            pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[k]]
            xs.append(w)
            ys.append(med(pts))
            ax.scatter([w] * len(pts), pts, color=col, s=16, alpha=0.45, zorder=3)
        ax.plot(xs, ys, color=col, marker=mk, ls=ls, lw=2.0, ms=6, label=label, zorder=4)

    ax.axhline(13.4, color="#868e96", ls="--", lw=1.1)
    ax.text(5, 13.75, "13.4 M 行/s 慢崖", fontsize=8.3, color="#495057")

    if "sub_cl0_w128" in cfgs and "sub_cl48_w128" in cfgs:
        cha0 = med([r["pmu"]["cha_inflight"] for r in cfgs["sub_cl0_w128"]])
        cha48 = med([r["pmu"]["cha_inflight"] for r in cfgs["sub_cl48_w128"]])
        y0 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["sub_cl0_w128"]])
        y48 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["sub_cl48_w128"]])
        ax.annotate(
            f"W=128 倒灌:\nCHA={cha0:.1f} 条",
            xy=(128, y0), xytext=(72, 7.5),
            fontsize=8.0, color="#c92a2a",
            arrowprops=dict(arrowstyle="->", color="#c92a2a", lw=1.1),
        )
        ax.annotate(
            f"CHA={cha48:.1f} 条\n({y48:.1f} M 行/s)",
            xy=(128, y48), xytext=(68, y48 + 1.5),
            fontsize=8.0, color="#2b8a3e", fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#2b8a3e", lw=1.1),
        )

    ax.set_xlabel("闭环并发窗口 W (256 B RPC，4 队列汇总)")
    ax.set_ylabel("实测端到端吞吐 (M 行/s)")
    ax.set_title("(a) 闭环慢崖坍缩与信用消除")
    ax.set_xlim(0, 136)
    ax.set_ylim(0, 27.5)
    ax.legend(loc="lower right", fontsize=8.0, frameon=True, framealpha=0.92)

    # Panel (b): Inverted-U C_line sweep at fixed W = 64
    ax = axes[1]
    cls = [16, 24, 32, 40, 48, 64]
    xs, ys_ml, ys_hrtt = [], [], []
    for cl in cls:
        k = f"sub_cl{cl}_w64"
        if k not in cfgs:
            continue
        pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[k]]
        hpts = [r["card"]["host_rtt_p50_us"] for r in cfgs[k]]
        xs.append(cl)
        ys_ml.append(med(pts))
        ys_hrtt.append(med(hpts))
        ax.scatter([cl] * len(pts), pts, color="#1971c2", s=20, alpha=0.5, zorder=3)

    ax.plot(xs, ys_ml, color="#1971c2", marker="s", lw=2.2, ms=6.5, label="吞吐 (M 行/s)", zorder=4)
    if "sub_cl0_w64" in cfgs:
        u64 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["sub_cl0_w64"]])
        ax.axhline(u64, color="#c92a2a", ls="--", lw=1.5, label=f"无整形 W=64 ({u64:.2f} M 行/s)")

    ax.axvspan(38, 50, color="#e7f5ff", alpha=0.65, zorder=1)
    ax.text(44, 24.5, "最优窗口\n40–48 行", ha="center", fontsize=8.2, color="#1971c2", fontweight="bold")
    ax.text(24.5, 21.8, "信用饥饿\n(Little下限)", ha="center", fontsize=7.8, color="#495057")
    ax.annotate(
        "C=64 重入慢崖",
        xy=(64, 14.3), xytext=(51.5, 22.0),
        fontsize=7.8, color="#e67700", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#e67700", lw=1.1),
    )

    ax2 = ax.twinx()
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(True)
    ax2.spines["right"].set_color("#868e96")
    ax2.plot(xs, ys_hrtt, color="#868e96", marker="o", ls=":", lw=1.6, ms=5, label="卡侧 host_rtt p50 (µs)")
    ax2.set_ylabel("卡侧 host_rtt p50 (µs)", color="#495057", fontsize=8.8)
    ax2.set_ylim(0, 24)
    ax2.tick_params(axis="y", colors="#495057")

    ax.set_xlabel("全局在途行信用上限 C_line (64 B 行)")
    ax.set_ylabel("实测端到端吞吐 (M 行/s)")
    ax.set_title("(b) W = 64 单变量 C_line 倒 U 形约束")
    ax.set_xlim(12, 68)
    ax.set_ylim(7, 26.5)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="lower left", fontsize=7.6, frameon=True, framealpha=0.92)

    # Panel (c): Part 4 Diagnostic — Intra-RPC 256 B cell spacing (`bar4_shared`) + C_line=48 vs subring
    ax = axes[2]
    diag_rates = [4000, 4500, 5000, 5500, 6000]
    dx = [r * 4.0 / 1000.0 for r in diag_rates]
    for prefix, label, col, mk, ls in [
        ("sub_cl0", "默认紧排 无整形", "#c92a2a", "o", "-"),
        ("sub_cl40", "默认紧排 C_line=40", "#1971c2", "s", "-"),
        ("shr_cl0", "256B间隔 无整形", "#f08c00", "D", "--"),
        ("shr_cl48", "256B间隔 C_line=48", "#0b7285", "^", "-"),
    ]:
        xs, ys = [], []
        for r, xm in zip(diag_rates, dx):
            k = f"{prefix}_b8_r{r}"
            if k not in cfgs:
                continue
            pts = [row["client"]["tot_p99_us"] for row in cfgs[k]]
            xs.append(xm)
            ys.append(med(pts))
            ax.scatter([xm] * len(pts), pts, color=col, s=16, alpha=0.45, zorder=3)
        ax.plot(xs, ys, color=col, marker=mk, ls=ls, lw=2.0, ms=6, label=label, zorder=4)

    ax.set_yscale("log")
    ax.set_xlabel("目标到达速率 (M 行/s，B = 8, W = 64)")
    ax.set_ylabel("真实端到端 tot p99 (µs，对数轴)")
    ax.set_title("(c) 边界诊断：256 B 格间隔消除 128 B 块争用")
    ax.set_xlim(15.5, 24.5)
    ax.set_ylim(15, 6e6)
    ax.legend(loc="upper left", fontsize=7.6, frameon=True, framealpha=0.92, ncol=2)

    fig.suptitle(
        "MRV-135 闭环单调性、C_line 双边倒 U 形边界与 256 B 格间距诊断 (4C eth0 RSS, n = 3 零错)",
        fontsize=11.5,
        y=0.99,
    )
    out = save(fig, "mrv-135-closed-u")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
