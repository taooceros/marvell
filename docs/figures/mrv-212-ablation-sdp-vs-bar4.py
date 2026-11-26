#!/usr/bin/env python3
"""Plot Figure 3 for MRV-212: Core ablation of native SDP backpressure vs explicit NO-SFENCE BAR4 credit."""

import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

import mrvplot

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs" / "mrv212-2026-11-26" / "runs.jsonl"


def load_runs():
    by_gt = defaultdict(list)
    with RUNS.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            by_gt[(r["group"], r["tag"])].append(r["data"])
    return by_gt


def med(vals):
    return statistics.median(vals)


def main():
    by_gt = load_runs()

    d1_arms = [
        ("1500B_5M_native_bp_d256", "原生 SDP\n(无反压 256槽)", "#d64541", False),
        ("1500B_5M_native_bp_d512", "原生 SDP\n(无反压 512槽)", "#e67e22", False),
        ("1500B_5M_bar4_nofence_R256", "BAR4 信用\nR=256", "#1f77b4", True),
        ("1500B_5M_bar4_nofence_R128", "BAR4 信用\nR=128", "#2980b9", True),
        ("1500B_5M_bar4_nofence_R64", "BAR4 信用\nR=64", "#3498db", True),
        ("1500B_5M_bar4_nofence_96KiB", "BAR4 信用\n96 KiB 字节", "#16a085", True),
    ]

    for tag, _, _, lossless in d1_arms:
        rows = by_gt[("D1", tag)]
        assert len(rows) == 3, f"expected n=3 for D1/{tag}"
        for d in rows:
            assert d["page_faults"] == 0 and d["bad"] == 0 and d["torn"] == 0
            assert d["host_pmu"]["min_pct"] == 100.0 and d["card_pmu"]["min_pct"] == 100.0
            assert d["card_stats"]["q0_fifo_full"] == 0 and d["card_stats"]["alloc_fail"] == 0
            if lossless:
                assert d["lost"] == 0 and d["meas_seq_gap_lost"] == 0 and d["ooo"] == 0

    d2_arms = [
        ("1500B_native_bp_d512", "原生 SDP\n(无反压 512槽)", False),
        ("1500B_bar4_nofence_R128", "BAR4 信用\n(R = 128)", True),
        ("1500B_bar4_nofence_R256", "BAR4 信用\n(R = 256)", True),
    ]
    for tag, _, lossless in d2_arms:
        rows = by_gt[("D2", tag)]
        assert len(rows) == 3, f"expected n=3 for D2/{tag}"
        for d in rows:
            assert d["q0_page_faults"] == 0 and d["q1_page_faults"] == 0
            assert d["q0_bad"] == 0 and d["q0_torn"] == 0 and d["q1_bad"] == 0 and d["q1_torn"] == 0
            assert d["q0_lost"] == 0
            assert d["host_pmu"]["min_pct"] == 100.0 and d["card_pmu"]["min_pct"] == 100.0
            assert d["card_stats"]["q0_fifo_full"] == 0 and d["card_stats"]["q1_fifo_full"] == 0
            if lossless:
                assert d["q1_lost"] == 0 and d["q1_meas_seq_gap_lost"] == 0

    d1_gbps = [med([d["rx_gbps"] for d in by_gt[("D1", t)]]) for t, _, _, _ in d1_arms]
    d1_lost_k = [med([d["meas_seq_gap_lost"] for d in by_gt[("D1", t)]]) / 1e3 for t, _, _, _ in d1_arms]
    d1_dr_max = [int(med([d["pause_drained_max"] for d in by_gt[("D1", t)]])) for t, _, _, _ in d1_arms]

    d2_q0_mpps = [med([d["q0_mpps"] for d in by_gt[("D2", t)]]) for t, _, _ in d2_arms]
    d2_q1_mpps = [med([d["q1_mpps"] for d in by_gt[("D2", t)]]) for t, _, _ in d2_arms]
    d2_q1_lost_k = [med([d["q1_meas_seq_gap_lost"] for d in by_gt[("D2", t)]]) / 1e3 for t, _, _ in d2_arms]
    d2_q1_dr_max = [int(med([d["q1_drained_max"] for d in by_gt[("D2", t)]])) for t, _, _ in d2_arms]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.4), gridspec_kw={"width_ratios": [1.34, 1.0]})

    # ---- Left panel: D1 single-queue (1500 B @ 5.0 Mpps, 1 ms pause / 10 ms awake) ----
    x1 = np.arange(len(d1_arms))
    bars1 = ax1.bar(x1, d1_gbps, width=0.58, color=[c for _, _, c, _ in d1_arms], edgecolor="#2c3e50", linewidth=0.6, zorder=3)
    ax1.axhline(60.8, color="#27ae60", linestyle="--", linewidth=1.0, alpha=0.85, zorder=2)
    ax1.set_xticks(x1)
    ax1.set_xticklabels([lbl for _, lbl, _, _ in d1_arms], fontsize=8.8)
    ax1.set_ylabel("主机有效收包吞吐（Gbps，1500 B 满额 60.8 Gbps）", fontsize=9.8)
    ax1.set_title("(a) 单队列偶发停顿 1 ms/10 ms：被动小环静默丢包 vs BAR4 零丢包", fontsize=10.4, pad=10)
    ax1.set_ylim(0, 86)

    for bar, gbps, lost_k, dr in zip(bars1, d1_gbps, d1_lost_k, d1_dr_max):
        cx = bar.get_x() + bar.get_width() / 2
        if lost_k > 0:
            # Place loss callout cleanly above the 60.8 Gbps line with pointer to bar top
            ax1.annotate(
                f"{gbps:.1f} Gbps\n丢 {lost_k:.0f}K 包\n(环冲穿 {dr})",
                xy=(cx, gbps + 0.4),
                xytext=(cx, 63.2),
                ha="center",
                va="bottom",
                fontsize=8.5,
                color="#922b21",
                arrowprops=dict(arrowstyle="->", color="#922b21", lw=0.95, shrinkA=1, shrinkB=2),
            )
        else:
            ax1.text(
                cx,
                62.4,
                f"{gbps:.1f} Gbps\n0 丢包\n(封顶 {dr} 包)",
                ha="center",
                va="bottom",
                fontsize=8.5,
                color="#0b4f8c",
            )

    ax1.text(
        2.5,
        80.8,
        "注：octep_cp_agent 未置位 SDPX_OUT_BP_ENX_W1S，实测原生 SDP 退化为无反压被动小环（tx_retries = 0）",
        ha="center",
        va="center",
        fontsize=8.1,
        color="#7b241c",
        bbox=dict(boxstyle="round,pad=0.28", fc="#fdf2e9", ec="#d35400", lw=0.75),
    )

    # ---- Right panel: D2 dual-queue (Q0 healthy 3.0 Mpps + Q1 8 ms paused 1.5 Mpps) ----
    x2 = np.arange(len(d2_arms))
    w = 0.34
    bq0 = ax2.bar(x2 - w / 2, d2_q0_mpps, width=w, color="#7f8c8d", edgecolor="#2c3e50", linewidth=0.6)
    bq1 = ax2.bar(x2 + w / 2, d2_q1_mpps, width=w, color=["#d64541", "#1f77b4", "#2980b9"], edgecolor="#2c3e50", linewidth=0.6)

    ax2.axhline(3.0, color="#555555", linestyle=":", linewidth=0.9, alpha=0.7)
    ax2.axhline(1.5, color="#1f77b4", linestyle="--", linewidth=0.9, alpha=0.7)
    ax2.set_xticks(x2)
    ax2.set_xticklabels([lbl for _, lbl, _ in d2_arms], fontsize=9.8)
    ax2.set_ylabel("各队列实测无损吞吐（Mpps，1500 B）", fontsize=10)
    ax2.set_title("(b) 双队列：Q1 丢包 639K→0；Q0 p99 不变（最大间隔 9.6→49 µs）", fontsize=10.0, pad=10)
    ax2.set_ylim(0, 4.25)
    handles = [
        mpatches.Patch(facecolor="#7f8c8d", edgecolor="#2c3e50", label="Q0 健康队列 (3.0M)"),
        mpatches.Patch(facecolor="#d64541", edgecolor="#2c3e50", label="Q1 停顿队列 (被动小环丢包)"),
        mpatches.Patch(facecolor="#1f77b4", edgecolor="#2c3e50", label="Q1 停顿队列 (BAR4 零丢)"),
    ]
    ax2.legend(handles=handles, loc="upper center", ncol=2, frameon=True, fontsize=8.4)

    for bar, q0 in zip(bq0, d2_q0_mpps):
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            q0 + 0.06,
            f"{q0:.2f}M\n(0 丢)",
            ha="center",
            va="bottom",
            fontsize=8.5,
            color="#2c3e50",
        )
    for bar, q1, lost_k, dr in zip(bq1, d2_q1_mpps, d2_q1_lost_k, d2_q1_dr_max):
        if lost_k > 0:
            txt = f"{q1:.2f}M\n丢 {lost_k:.0f}K 包\n(冲穿 {dr})"
            col = "#922b21"
        else:
            txt = f"{q1:.2f}M\n0 丢包\n(封顶 {dr})"
            col = "#0b4f8c"
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            q1 + 0.06,
            txt,
            ha="center",
            va="bottom",
            fontsize=8.6,
            color=col,
        )

    fig.suptitle(
        "MRV-212 对照 D：CN106 原生 SDP（未置位 SDPX_OUT_BP_ENX_W1S）退化为无反压小环丢包，BAR4 信用零丢且精确封顶",
        fontsize=11.3,
        y=0.97,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    png = mrvplot.HERE / "mrv-212-ablation-sdp-vs-bar4.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-212-ablation-sdp-vs-bar4.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
