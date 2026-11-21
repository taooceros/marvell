#!/usr/bin/env python3
"""MRV-135 Closed-Loop Scaling, C_line Inverted-U Window, and Same-Credit 256 B Intra-RPC Stride Diagnostic."""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt

from mrvplot import save

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs/mrv135-2026-11-21/runs.jsonl"
OVERFLOW_US = 20_000.0


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


def plot_with_overflow(ax, xs, ys, col, mk, ls, label, zorder=4):
    ax.plot(xs, ys, color=col, ls=ls, lw=2.0, zorder=zorder)
    xs_ok = [x for x, y in zip(xs, ys) if y < OVERFLOW_US]
    ys_ok = [y for y in ys if y < OVERFLOW_US]
    xs_ov = [x for x, y in zip(xs, ys) if y >= OVERFLOW_US]
    ys_ov = [y for y in ys if y >= OVERFLOW_US]
    if xs_ok:
        ax.plot(xs_ok, ys_ok, color=col, marker=mk, ls="None", ms=6.2,
                markerfacecolor=col, markeredgecolor=col, zorder=zorder + 1)
    if xs_ov:
        ax.plot(xs_ov, ys_ov, color=col, marker=mk, ls="None", ms=6.2,
                markerfacecolor="white", markeredgecolor=col, markeredgewidth=1.6, zorder=zorder + 1)
    ax.plot([], [], color=col, marker=mk, ls=ls, lw=2.0, ms=6.0, label=label)


def main():
    rows = load_runs()
    cfgs = by_cfg(rows)

    fig, axes = plt.subplots(1, 3, figsize=(15.8, 4.9))

    # Panel (a): Closed-loop W=4..128 throughput (M lines/s) + Part 4 W=64/128 stride refs
    ax = axes[0]
    ws_all = [4, 8, 16, 24, 32, 48, 64, 128]
    ws_c = [32, 48, 64, 128]
    ws_s = [64, 128]

    for tag, ws, label, col, mk, ls in [
        ("sub_cl0", ws_all, "默认紧排 无整形 (C=0)", "#c92a2a", "o", "-"),
        ("sub_cl40", ws_c, "默认紧排 C_line = 40", "#1971c2", "s", "-"),
        ("sub_cl48", ws_c, "默认紧排 C_line = 48", "#2b8a3e", "^", "--"),
        ("shr_cl0", ws_s, "256B步长 无整形 (C=0)", "#f08c00", "D", ":"),
        ("shr_cl48", ws_s, "256B步长 C_line = 48", "#0b7285", "v", "-."),
    ]:
        xs, ys = [], []
        for w in ws:
            k = f"{tag}_w{w}"
            if k not in cfgs:
                continue
            pts = [r["client"]["achieved_mrps"] * 4.0 for r in cfgs[k]]
            xs.append(w)
            ys.append(med(pts))
            ax.scatter([w] * len(pts), pts, color=col, s=15, alpha=0.42, zorder=3)
        ax.plot(xs, ys, color=col, marker=mk, ls=ls, lw=1.9, ms=5.8, label=label, zorder=4)

    ax.axhline(13.4, color="#868e96", ls="--", lw=1.1)
    ax.text(4, 13.8, "13.4 M 慢崖", fontsize=8.0, color="#495057")

    if "sub_cl0_w128" in cfgs and "shr_cl0_w64" in cfgs and "shr_cl48_w128" in cfgs:
        cha0 = med([r["pmu"]["cha_inflight"] for r in cfgs["sub_cl0_w128"]])
        y0 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["sub_cl0_w128"]])
        ys0_64 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["shr_cl0_w64"]])
        ys0_128 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["shr_cl0_w128"]])
        y48 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["sub_cl48_w128"]])
        ys48 = med([r["client"]["achieved_mrps"] * 4.0 for r in cfgs["shr_cl48_w128"]])
        cha48 = med([r["pmu"]["cha_inflight"] for r in cfgs["sub_cl48_w128"]])
        ax.annotate(
            f"W=128 倒灌: CHA={cha0:.1f}",
            xy=(128, y0), xytext=(55, 9.6),
            fontsize=7.6, color="#c92a2a",
            arrowprops=dict(arrowstyle="->", color="#c92a2a", lw=1.0),
        )
        ax.annotate(
            f"W=64: {ys0_64:.1f} → W=128: {ys0_128:.1f} M",
            xy=(64, ys0_64), xytext=(72, 18.0),
            fontsize=7.2, color="#d9480f",
            arrowprops=dict(arrowstyle="->", color="#d9480f", lw=1.0),
        )
        ax.annotate(
            f"CHA={cha48:.1f}–2.4 ({y48:.1f} / {ys48:.1f} M)",
            xy=(128, ys48), xytext=(42, 25.3),
            fontsize=7.6, color="#0b7285", fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#0b7285", lw=1.0),
        )

    ax.set_xlabel("闭环并发窗口 W (256 B RPC，4 队列汇总)")
    ax.set_ylabel("实测端到端吞吐 (M 行/s)")
    ax.set_title("(a) 闭环慢崖坍缩与信用消除")
    ax.set_xlim(0, 136)
    ax.set_ylim(0, 27.6)
    ax.legend(loc="lower right", fontsize=7.2, frameon=True, framealpha=0.93)

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
        ax.axhline(u64, color="#c92a2a", ls="--", lw=1.5, label=f"无整形 W=64 ({u64:.2f} M)")

    ax.axvspan(38, 50, color="#e7f5ff", alpha=0.65, zorder=1)
    ax.text(44, 24.7, "最优窗口\n40–48 行", ha="center", fontsize=8.0, color="#1971c2", fontweight="bold")
    ax.text(26.5, 22.2, "信用受限\n(τ_credit≈1.7 µs)", ha="center", fontsize=7.5, color="#495057")
    ax.annotate(
        "C=64 重入慢崖",
        xy=(64, 14.3), xytext=(51.0, 21.8),
        fontsize=7.6, color="#e67700", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#e67700", lw=1.1),
    )

    ax2 = ax.twinx()
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(True)
    ax2.spines["right"].set_color("#868e96")
    ax2.plot(xs, ys_hrtt, color="#868e96", marker="o", ls=":", lw=1.6, ms=5, label="卡侧 host_rtt p50 (µs)")
    ax2.set_ylabel("卡侧 host_rtt p50 (µs)", color="#495057", fontsize=8.6)
    ax2.set_ylim(0, 24.5)
    ax2.tick_params(axis="y", colors="#495057")

    ax.set_xlabel("全局在途行信用上限 C_line (64 B 行)")
    ax.set_ylabel("实测端到端吞吐 (M 行/s)")
    ax.set_title("(b) W = 64 单变量 C_line 倒 U 形约束")
    ax.set_xlim(12, 68)
    ax.set_ylim(3.5, 27.0)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="lower center", fontsize=7.4, frameon=True, framealpha=0.93)

    # Panel (c): Part 4 Same-Credit Diagnostic — 256 B intra-RPC stride (bar4_shared) vs tight subring
    ax = axes[2]
    ax.axhspan(OVERFLOW_US, 1e7, color="#f1f3f5", alpha=0.88, zorder=0)
    ax.axhline(OVERFLOW_US, color="#868e96", ls=":", lw=1.2, zorder=1)
    ax.text(24.3, 5.8e6, "≥ 20 ms 溢出区\n(空心 = 1.5 s max_ns)", fontsize=7.2, color="#495057", ha="right", va="top")

    diag_rates = [4000, 4500, 5000, 5500, 6000]
    dx = [r * 4.0 / 1000.0 for r in diag_rates]
    for prefix, label, col, mk, ls in [
        ("sub_cl0", "默认紧排 C=0", "#c92a2a", "o", "-"),
        ("shr_cl0", "256B步长 C=0", "#f08c00", "D", "--"),
        ("sub_cl48", "默认紧排 C=48", "#2b8a3e", "^", "--"),
        ("shr_cl48", "256B步长 C=48", "#0b7285", "s", "-"),
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
        plot_with_overflow(ax, xs, ys, col, mk, ls, label, zorder=4)

    if "sub_cl48_b8_r5000" in cfgs and "shr_cl48_b8_r5500" in cfgs:
        ax.annotate(
            "同 C=48:\n20M 消除 21× 亚稳抖动\n22M: 2075→134 µs\n(TAD 驻留 -67%)",
            xy=(22.0, 134.1),
            xytext=(16.5, 420),
            fontsize=7.3,
            color="#0b7285",
            fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#0b7285", lw=1.0),
            bbox=dict(boxstyle="round,pad=0.25", fc="#e3fafc", ec="#0b7285", lw=0.8),
        )

    ax.set_yscale("log")
    ax.set_xlabel("目标到达速率 (M 行/s，B = 8, W = 64)")
    ax.set_ylabel("端到端 tot p99 (µs，对数轴)")
    ax.set_title("(c) 边界诊断：256 B 包内步长 (同信用 C_line=48 对照)")
    ax.set_xlim(15.5, 24.5)
    ax.set_ylim(18, 9.5e6)
    ax.legend(loc="upper left", fontsize=7.1, frameon=True, framealpha=0.93, ncol=1)

    fig.suptitle(
        "MRV-135 闭环单调性、C_line 双边倒 U 形边界与 256 B 包内步长同信用诊断 (4C eth0 RSS, n = 3 零错)",
        fontsize=11.3,
        y=0.99,
    )
    out = save(fig, "mrv-135-closed-u")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
