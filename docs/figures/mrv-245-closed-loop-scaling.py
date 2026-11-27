"""MRV-245 Figure 2: Same zero-doorbell, zero-SFENCE MT_NORMAL_NC (0x44) path across
W=1 unloaded RTT and high-concurrency closed-loop ping-pong (W=64/256/512, H=1/2),
highlighting optimal core matching vs over-provisioned closed-loop collapse boundary.

Reads raw verified n=3 runs directly from logs/mrv245-2026-11-27/runs.jsonl.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv245-2026-11-27/runs.jsonl"


def load_runs():
    by = defaultdict(list)
    for ln in RUNS_PATH.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        cp = r["card_pmu"]
        assert r["lost"] == 0 and r["bad_or_torn"] == 0
        assert sum(w["corrupt"] for w in cp["workers"]) == 0
        assert sum(w["partial"] for w in cp["workers"]) == 0
        assert sum(w["db_ahead_empty"] for w in cp["workers"]) == 0
        assert sum(w["db_ahead_partial"] for w in cp["workers"]) == 0
        assert r["host_min_pct"] >= 99.9 and cp["min_pct"] >= 99.9
        by[r["cond"]].append(r)
    return by


def stats(rs, fn):
    vals = [fn(r) for r in rs]
    return statistics.median(vals), min(vals), max(vals)


def main():
    by = load_runs()

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(13.8, 5.7), gridspec_kw={"width_ratios": [1.0, 1.32]}
    )

    # ------------------------------------------------------------------
    # Panel A: W=1 unloaded RTT (p50 bars + p99 markers)
    # ------------------------------------------------------------------
    w1_specs = [
        ("cl_wb_u1_k1_w1", "Normal-WB (u1, K=1)", "#94a3b8"),
        ("cl_wb_u1_k4_w1", "Normal-WB (u1, K=4)", "#64748b"),
        ("cl_nc_u1_k1_w1", "MT_NORMAL_NC (u1, K=1)", "#16a34a"),
        ("cl_nc_u1_k4_w1", "MT_NORMAL_NC (u1, K=4)", "#15803d"),
        ("cl_nc_u1_k8_w1", "MT_NORMAL_NC (u1, K=8)", "#047857"),
        ("cl_nc_u4_k1_w1", "MT_NORMAL_NC (u4, K=1)", "#0284c7"),
        ("cl_nc_u4_k4_w1", "MT_NORMAL_NC (u4, K=4)", "#0369a1"),
    ]
    y = np.arange(len(w1_specs))
    p50s, p99s = [], []
    for cond, _, _ in w1_specs:
        p50s.append(stats(by[cond], lambda r: r["rtt_p50_ns"])[0])
        p99s.append(stats(by[cond], lambda r: r["rtt_p99_ns"])[0])

    ax1.barh(y, p50s, height=0.55, color=[s[2] for s in w1_specs], alpha=0.86, label="p50 RTT", zorder=3)
    ax1.scatter(p99s, y, color="#0f172a", marker="D", s=34, zorder=5, label="p99 RTT")
    for yi, a, b in zip(y, p50s, p99s):
        ax1.plot([a, b], [yi, yi], color="#334155", lw=1.2, zorder=4)
        ax1.text(
            b + 7, yi, f"{a:.0f} / {b:.0f} ns",
            va="center", ha="left", fontsize=8.3, color="#0f172a",
            fontweight="bold" if yi in (2, 3, 4) else "normal",
        )

    ax1.axvline(900, color="#15803d", ls="--", lw=1.1, alpha=0.75, zorder=2)
    ax1.text(903, -0.58, "900 ns 参考线", fontsize=7.9, color="#15803d")

    ax1.set_yticks(y)
    ax1.set_yticklabels([s[1] for s in w1_specs], fontsize=8.6)
    ax1.invert_yaxis()
    ax1.set_xlim(820, 1135)
    ax1.set_xlabel("空载单包往返延迟 RTT（ns，RDTSCP 实测）", fontsize=10)
    ax1.set_title("A. 空载 W=1 RTT：MT_NORMAL_NC 与 WB 持平（~900–905 ns）", fontsize=10.4, fontweight="bold", pad=8)
    ax1.legend(loc="upper right", fontsize=8.0, frameon=True, framealpha=0.95)

    # ------------------------------------------------------------------
    # Panel B: High-concurrency closed-loop throughput across W=64, 256, 512
    # ------------------------------------------------------------------
    wx = np.array([0.0, 1.0, 2.0])

    def plot_w_curve(conds, xs, label, color, marker, ls="-", lw=2.3, ms=7.0, z=4):
        meds, lo, hi = [], [], []
        for c in conds:
            m, mn, mx = stats(by[c], lambda r: r["mops"])
            meds.append(m)
            lo.append(m - mn)
            hi.append(mx - m)
        ax2.errorbar(
            xs, meds, yerr=[lo, hi], label=label, color=color, marker=marker,
            ls=ls, lw=lw, markersize=ms, capsize=3.5, elinewidth=1.3, zorder=z,
        )
        return meds

    m_h2_k4 = plot_w_curve(
        ["cl_nc_u1_h2_k4_w64", "cl_nc_u1_h2_k4_w256", "cl_nc_u1_h2_k4_w512"],
        wx, "MT_NORMAL_NC u1 · H=2, K=4（核数匹配）", "#047857", "D", lw=2.5, ms=7.2, z=6,
    )
    plot_w_curve(
        ["cl_nc_u1_h1_k2_w64", "cl_nc_u1_h1_k2_w256", "cl_nc_u1_h1_k2_w512"],
        wx, "MT_NORMAL_NC u1 · H=1, K=2（核数匹配）", "#0284c7", "o", lw=2.3, ms=6.8, z=5,
    )
    plot_w_curve(
        ["cl_nc_u1_h1_k4_w64", "cl_nc_u1_h1_k4_w256", "cl_nc_u1_h1_k4_w512"],
        wx, "MT_NORMAL_NC u1 · H=1, K=4（略超配）", "#64748b", "s", ls="--", lw=1.7, ms=5.8, z=4,
    )
    m_wb_k4 = plot_w_curve(
        ["cl_wb_raw_k4_w64", "cl_wb_raw_k4_w256", "cl_wb_raw_k4_w512"],
        wx, "对照：Normal-WB 无控 · H=1, K=4（闭环墙）", "#dc2626", "X", ls="-.", lw=1.9, ms=7.0, z=3,
    )

    u1q_m, u1q_mn, u1q_mx = stats(by["cl_nc_u1q_h1_k2_w256"], lambda r: r["mops"])
    u1q_p50 = stats(by["cl_nc_u1q_h1_k2_w256"], lambda r: r["rtt_p50_ns"])[0]
    u1q_p99 = stats(by["cl_nc_u1q_h1_k2_w256"], lambda r: r["rtt_p99_ns"])[0]
    u1q_cha = stats(by["cl_nc_u1q_h1_k2_w256"], lambda r: r["host_pmu"]["cha_inflight"])[0]
    ax2.errorbar(
        [1.0], [u1q_m], yerr=[[u1q_m - u1q_mn], [u1q_mx - u1q_m]],
        color="#16a34a", marker="*", markersize=12, ls="none", capsize=3.5, zorder=7,
        label="MT_NORMAL_NC u1q (NEON) · H=1, K=2",
    )

    u4_h2_m, u4_h2_mn, u4_h2_mx = stats(by["cl_nc_u4_h2_k2_w512"], lambda r: r["mops"])
    u4_h2_cha = stats(by["cl_nc_u4_h2_k2_w512"], lambda r: r["host_pmu"]["cha_inflight"])[0]
    ax2.errorbar(
        [2.0], [u4_h2_m], yerr=[[u4_h2_m - u4_h2_mn], [u4_h2_mx - u4_h2_m]],
        color="#10b981", marker="P", markersize=8.5, ls="none", capsize=3.5, zorder=7,
        label="MT_NORMAL_NC u4 · H=2, K=2 (CHA=8.9)",
    )

    over_pts = [
        (0.94, "cl_nc_u1_h1_k8_w256"),
        (1.06, "cl_nc_u4_h1_k2_w256"),
        (2.0, "cl_nc_u1_h2_k8_w512"),
    ]
    ox = [p[0] for p in over_pts]
    om = [stats(by[p[1]], lambda r: r["mops"])[0] for p in over_pts]
    ax2.scatter(
        ox, om, facecolors="#fffbeb", edgecolors="#d97706", marker="o", s=58, lw=1.8, zorder=6,
        label="工程边界：卡核超配闭环坍缩 (exlmn≈0.8–1.1, l2i=0)",
    )

    h2_p50 = stats(by["cl_nc_u1_h2_k4_w512"], lambda r: r["rtt_p50_ns"])[0]
    h2_max = stats(by["cl_nc_u1_h2_k4_w512"], lambda r: r["mops"])[2]
    ax2.annotate(
        f"H=2, K=4 达 {m_h2_k4[2]:.1f} M/s\n(峰 {h2_max:.1f}, p50 {h2_p50/1000:.2f} µs)",
        xy=(2.0, m_h2_k4[2]), xytext=(1.15, 130.5),
        fontsize=8.3, fontweight="bold", color="#047857",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#a7f3d0", lw=0.7, alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#047857", lw=1.2),
    )
    wb_ratio = u1q_m / m_wb_k4[1]
    ax2.annotate(
        f"H=1 最优 (u1q): {u1q_m:.1f} M/s ({wb_ratio:.1f}× WB)\np50 {u1q_p50/1000:.2f} / p99 {u1q_p99/1000:.2f} µs, CHA {u1q_cha:.1f}",
        xy=(1.0, u1q_m), xytext=(-0.14, 79.0),
        fontsize=7.9, fontweight="bold", color="#15803d",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#bbf7d0", lw=0.7, alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#15803d", lw=1.2),
    )
    ax2.annotate(
        f"H=2, K=2 u4: {u4_h2_m:.1f} M/s\n仅 2 卡核, CHA {u4_h2_cha:.1f}",
        xy=(2.0, u4_h2_m), xytext=(1.22, 74.5),
        fontsize=8.0, color="#059669",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#a7f3d0", lw=0.7, alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#059669", lw=1.0),
    )
    ax2.annotate(
        f"超配闭环坍缩 ({om[0]:.1f} / {om[1]:.1f} / {om[2]:.1f} M/s)\n观测：l2i=0, exlmn=0.82–1.07/p",
        xy=(1.06, om[1]), xytext=(1.10, 31.0),
        fontsize=7.6, color="#b45309",
        bbox=dict(boxstyle="round,pad=0.2", fc="#fffbeb", ec="#fcd34d", lw=0.7, alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#d97706", lw=1.0),
    )
    ax2.text(
        1.42, m_wb_k4[1] - 7.5,
        f"Normal-WB 闭环墙：{m_wb_k4[1]:.1f} M/s",
        fontsize=8.0, color="#b91c1c", fontweight="bold",
    )

    ax2.set_xticks(wx)
    ax2.set_xticklabels(["W = 64", "W = 256", "W = 512"], fontsize=9.6)
    ax2.set_xlim(-0.22, 2.25)
    ax2.set_ylim(0, 152)
    ax2.set_xlabel("闭环并发窗口 W（在途请求数，每包 64 B 双向校验）", fontsize=10)
    ax2.set_ylabel("闭环乒乓吞吐（Mpps）", fontsize=10)
    ax2.set_title("B. 高并发闭环吞吐：按主机供给配卡核最高 76–120 M/s", fontsize=10.6, fontweight="bold", pad=8)
    ax2.legend(loc="upper left", fontsize=7.1, frameon=True, framealpha=0.95)

    fig.suptitle(
        "MRV-245 · 同一零门铃/零 SFENCE 通路兼顾 ~900 ns 空载延迟与高并发闭环",
        fontsize=12.4, fontweight="bold", y=0.99,
    )
    fig.text(
        0.5, 0.01,
        "条件：BAR4 槽 15 + PEM0 WC 回复（regions 248–255），ring=4096，全程 enable_custom_mair=0、L2 作废=0.00、DDR5 读=0.00；cpu40 3.90 GHz（H=2 cpu41 schedutil 2.2–3.9 GHz）；n=3 中位数与 min–max",
        ha="center", fontsize=8.0, color="#475569",
    )
    fig.tight_layout(rect=[0, 0.035, 1, 0.945])
    png = mrvplot.HERE / "mrv-245-closed-loop-scaling.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-245-closed-loop-scaling.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
