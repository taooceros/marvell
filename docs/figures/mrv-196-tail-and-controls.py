"""MRV-196 Figure 3: Tail latency (p50/p99/p99.9 across LLC, DRAM K=8, and DRAM K=1),
SFENCE batch ablation (512 vs 128), and controlled checks (l1-spin core frequency &
same-core SMT sibling cpu40 vs cpu104).

Reads raw verified n=3 runs directly from `logs/mrv196-2026-11-25/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv196-2026-11-25/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def load_verified():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    by_lbl = defaultdict(list)
    for r in rows:
        assert r["accepted"] is True
        assert r["host_pmu_min_pct"] == 100.0
        assert r["page_faults"] == 0
        assert r["victim_page_faults"] == 0
        assert r["aggr_page_faults"] == 0
        if r["chase"]:
            assert r["chase"]["bad_nodes"] == 0
        if r["stream"]:
            assert r["stream"]["verify_errors"] == 0
        by_lbl[r["label"]].append(r)
    return by_lbl


def main():
    by_lbl = load_verified()

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(16.2, 6.8), gridspec_kw={"width_ratios": [1.24, 1.0]}
    )

    # ---- Panel A: LLC & DRAM p50 / p99 / p99.9 across key operating points ----
    conds = [
        ("idle", "静默基线\n(0 Mpps)"),
        ("l1spin_8c", "8核纯算\n(核频对照)"),
        ("bar4_1c_160m_b512", "BAR4 1核\n160 Mpps"),
        ("bar4_8c_165m_b128", "BAR4 8核限速\n165m b=128"),
        ("dpi_pull_256m_1c_160m", "主存+DPI\n256M 1核"),
        ("bar4_8c_max_b128", "BAR4 8核满速\nb=128"),
        ("bar4_8c_165m_b512", "BAR4 8核限速\n165m b=512"),
        ("bar4_8c_max_b512", "BAR4 8核满速\nb=512"),
    ]
    y = np.arange(len(conds))
    ylabels = [c[1] for c in conds]

    for i, (aggr, _) in enumerate(conds):
        llc_it = by_lbl[f"llc__{aggr}"]
        dram_it = by_lbl[f"dram__{aggr}"]
        assert len(llc_it) == 3 and len(dram_it) == 3

        l50 = med([r["chase"]["p50_ns"] for r in llc_it])
        l99 = med([r["chase"]["p99_ns"] for r in llc_it])
        l999 = med([r["chase"]["p999_ns"] for r in llc_it])

        d50 = med([r["chase"]["p50_ns"] for r in dram_it])
        d99 = med([r["chase"]["p99_ns"] for r in dram_it])
        d999 = med([r["chase"]["p999_ns"] for r in dram_it])

        yl = i - 0.17
        yd = i + 0.17

        ax1.plot([l50, l999], [yl, yl], color="#8250df", lw=2.2, alpha=0.72, zorder=2)
        ax1.scatter([l50], [yl], color="#8250df", marker="o", s=46, zorder=4,
                    label="LLC 16M p50 (K=32)" if i == 0 else None)
        ax1.scatter([l99], [yl], color="#8250df", marker="s", s=54, edgecolors="#1f2328", lw=0.7, zorder=5,
                    label="LLC 16M p99 (K=32)" if i == 0 else None)
        ax1.scatter([l999], [yl], color="#8250df", marker=">", s=44, alpha=0.85, zorder=4,
                    label="LLC 16M p99.9 (K=32)" if i == 0 else None)

        ax1.plot([d50, d999], [yd, yd], color="#cf222e", lw=2.2, alpha=0.72, zorder=2)
        ax1.scatter([d50], [yd], color="#0969da", marker="o", s=46, zorder=4,
                    label="DRAM 256M p50 (K=8)" if i == 0 else None)
        ax1.scatter([d99], [yd], color="#cf222e", marker="s", s=54, edgecolors="#1f2328", lw=0.7, zorder=5,
                    label="DRAM 256M p99 (K=8)" if i == 0 else None)
        ax1.scatter([d999], [yd], color="#e16f24", marker=">", s=44, alpha=0.85, zorder=4,
                    label="DRAM 256M p99.9 (K=8)" if i == 0 else None)

        ax1.text(l999 * 1.07, yl, f"p50={l50:.1f} / p99={l99:.1f}", va="center", ha="left", fontsize=7.5, color="#6639ba")
        ax1.text(d999 * 1.07, yd, f"p50={d50:.0f} / p99={d99:.0f}", va="center", ha="left", fontsize=7.5, color="#a40e26")

    ax1.set_xscale("log")
    ax1.set_xlim(20, 6200)
    ax1.set_xticks([25, 50, 100, 200, 500, 1000, 2000, 4000])
    ax1.set_xticklabels(["25", "50", "100", "200", "500", "1000", "2000", "4000"], fontsize=8.8)
    ax1.set_yticks(y)
    ax1.set_yticklabels(ylabels, fontsize=8.6)
    ax1.invert_yaxis()
    ax1.set_xlabel("单跳访存延迟（ns，对数刻度；全部直方图 p99/p99.9 零溢出）", fontsize=9.6)
    ax1.set_title("A. 尾延迟分布（p50 / p99 / p99.9）与 SFENCE 批大小消融", fontsize=10.6, fontweight="bold", pad=10)
    # Place legend & K=1 inset cleanly in upper-right empty space (rows 0..4, x > 700)
    ax1.legend(loc="upper right", fontsize=7.7, ncol=1, framealpha=0.96)
    ax1.grid(axis="y", visible=False)

    k1_idle = med([r["chase"]["p99_ns"] for r in by_lbl["dram_k1__idle"]])
    k1_160 = med([r["chase"]["p99_ns"] for r in by_lbl["dram_k1__bar4_1c_160m_b512"]])
    k1_8c = med([r["chase"]["p99_ns"] for r in by_lbl["dram_k1__bar4_8c_max_b512"]])
    k1_rec = med([r["chase"]["p99_ns"] for r in by_lbl["dram_k1__bar4_8c_165m_b128"]])
    k1_dpi = med([r["chase"]["p99_ns"] for r in by_lbl["dram_k1__dpi_pull_256m_1c_160m"]])
    ax1.text(
        0.985, 0.50,
        f"K=1 单跳 DRAM 抽检 (p99):\n"
        f"• 静默基线: {k1_idle:.0f} ns\n"
        f"• BAR4 1核 160m: {k1_160:.0f} ns\n"
        f"• BAR4 8核限速 b=128: {k1_rec:.0f} ns\n"
        f"• DPI-256M 1核: {k1_dpi:.0f} ns\n"
        f"• BAR4 8核满速 b=512: {k1_8c:.0f} ns",
        transform=ax1.transAxes, ha="right", va="center", fontsize=7.5,
        bbox=dict(boxstyle="round,pad=0.36", facecolor="#f6f8fa", edgecolor="#d0d7de"),
    )

    # ---- Panel B: Cross-core l1-spin freq control & Same-core SMT (cpu40 vs cpu104) ----
    smt_rows = [
        ("跨物理核\nBAR4 1核满速\n(cpu40 vs 52)", "bar4_1c_max_b512"),
        ("跨物理核\n8核纯算对照\n(cpu40 vs 52..59)", "l1spin_8c"),
        ("同物理核 SMT\n兄弟核纯算\n(cpu40 vs 104)", "smt_l1spin"),
        ("同物理核 SMT\nBAR4 160m\n(cpu40 vs 104)", "smt_bar4_160m_b512"),
        ("同物理核 SMT\nBAR4 满速\n(cpu40 vs 104)", "smt_bar4_max_b512"),
    ]
    x2 = np.arange(len(smt_rows))
    w2 = 0.24

    def get_t1c_deg(aggr):
        b = med([r["stream"]["mean_gb_s"] for r in by_lbl["t1c__idle"]])
        m = med([r["stream"]["mean_gb_s"] for r in by_lbl[f"t1c__{aggr}"]])
        return (b - m) / b * 100.0

    def get_chase_deg(vtag, aggr):
        b = med([r["chase"]["net_mean_ns"] for r in by_lbl[f"{vtag}__idle"]])
        m = med([r["chase"]["net_mean_ns"] for r in by_lbl[f"{vtag}__{aggr}"]])
        return (m - b) / b * 100.0

    t1c_d = [get_t1c_deg(a) for _, a in smt_rows]
    llc_d = [get_chase_deg("llc", a) for _, a in smt_rows]
    dram_d = [get_chase_deg("dram", a) for _, a in smt_rows]
    bar4_mpps_t1c = [med([r["aggr_mpps"] for r in by_lbl[f"t1c__{a}"]]) for _, a in smt_rows]
    bar4_mpps_llc = [med([r["aggr_mpps"] for r in by_lbl[f"llc__{a}"]]) for _, a in smt_rows]

    ax2.axhspan(-3.0, 5.0, color="#dafbe1", alpha=0.45, zorder=0)
    ax2.axhline(5.0, color="#1a7f37", linestyle="--", lw=1.0, zorder=1)
    ax2.axhline(0.0, color="#57606a", lw=0.8, zorder=1)

    ax2.bar(x2 - w2, llc_d, width=w2, color="#8250df", alpha=0.88, label="LLC 16M 延迟变化 (%)", zorder=3)
    ax2.bar(x2, dram_d, width=w2, color="#0969da", alpha=0.88, label="DRAM 256M 延迟变化 (%)", zorder=3)
    ax2.bar(x2 + w2, t1c_d, width=w2, color="#e16f24", alpha=0.90, label="STREAM 1核 带宽下降 (%)", zorder=3)

    for i in range(len(smt_rows)):
        dy_off = 0.25 if t1c_d[i] >= 0 else -0.35
        va_t = "bottom" if t1c_d[i] >= 0 else "top"
        ax2.text(x2[i] + w2 + (0.04 if i == 0 else 0.0), t1c_d[i] + dy_off, f"{t1c_d[i]:+.1f}%",
                 ha="center", va=va_t, fontsize=7.6, color="#9a3412", fontweight="bold")

    ax2.set_xticks(x2)
    ax2.set_xticklabels([r[0] for r in smt_rows], fontsize=8.0)
    ax2.set_ylim(-5.2, 12.2)
    ax2.set_ylabel("Victim 相对静默基线退化幅度 (%)", fontsize=9.5)
    ax2.set_title("B. 跨核核频对照与同核 SMT 边界（含 BAR4 自身实测速率）", fontsize=10.6, fontweight="bold", pad=10)

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.spines["right"].set_visible(True)
    b_idx = [0, 3, 4]
    ax2_r.plot(b_idx, [bar4_mpps_t1c[i] for i in b_idx], color="#cf222e", marker="X", lw=1.8, markersize=7.5,
               linestyle="--", label="BAR4 实测发送速率 (共置 STREAM 1核, Mpps)", zorder=5)
    ax2_r.plot(b_idx, [bar4_mpps_llc[i] for i in b_idx], color="#116329", marker="^", lw=1.8, markersize=6.5,
               linestyle=":", label="BAR4 实测发送速率 (共置 LLC 追逐, Mpps)", zorder=5)
    for i in b_idx:
        y_red_off = -18 if i == 0 else -12
        y_grn_off = 10 if i == 0 else 8
        ax2_r.text(i, bar4_mpps_t1c[i] + y_red_off, f"{bar4_mpps_t1c[i]:.1f}M", ha="center", va="top",
                   fontsize=7.8, color="#a40e26", fontweight="bold")
        ax2_r.text(i, bar4_mpps_llc[i] + y_grn_off, f"{bar4_mpps_llc[i]:.1f}M", ha="center", va="bottom",
                   fontsize=7.8, color="#116329", fontweight="bold")

    ax2_r.set_ylim(0, 245)
    ax2_r.set_ylabel("BAR4 实测写速率 (Mpps)", fontsize=9.5, color="#1f2328")
    ax2_r.grid(False)

    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2_r.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=7.6, framealpha=0.95)

    fig.suptitle(
        "MRV-196 图 3：尾延迟、SFENCE 批大小消融与受控边界（核频对照 + 同核 SMT 兄弟线程隔离边界，n = 3）",
        fontsize=12.0, fontweight="bold", y=0.99,
    )
    fig.subplots_adjust(top=0.89, bottom=0.14, wspace=0.28)
    png = mrvplot.save(fig, "mrv-196-tail-and-controls")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
