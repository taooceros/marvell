#!/usr/bin/env python3
"""Plot Figure 2 for MRV-212: Socket-1 neighbor zero-interference boundary vs 1-core unthrottled NO-SFENCE blast."""

import json
import statistics
from collections import defaultdict
from pathlib import Path

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

    conds = [
        ("idle", "空闲\n基线"),
        ("bar4_1c_0.5M_nf", "1核\n0.5M"),
        ("bar4_1c_3.5M_nf", "1核\n3.5M"),
        ("bar4_1c_7.0M_nf", "1核\n7.0M"),
        ("bar4_4c_7.0M_nf", "4核\n7.0M"),
        ("bar4_8c_7.0M_nf", "8核\n7.0M"),
        ("opus_1c_full_sf512", "满速\nSF\n512"),
        ("opus_1c_full_sf2048", "满速\nSF\n2048"),
        ("opus_1c_full_sf8192", "满速\nSF\n8192"),
        ("opus_1c_full_nf", "满速\n无\nSFENCE"),
    ]

    for vtag in ("llc_k32", "stream_6c"):
        for atag, _ in conds:
            rows = by_gt[("B", f"{vtag}__{atag}")]
            assert len(rows) == 3, f"expected n=3 for B/{vtag}__{atag}, got {len(rows)}"
            for d in rows:
                assert d["host_pmu_min_pct"] == 100.0
                assert d["card_pmu"]["min_pct"] == 100.0
                assert d["aggr_page_faults"] == 0
                vr = d["victim_res"]
                assert vr["page_faults"] == 0
                assert vr.get("bad_nodes", 0) == 0 and vr.get("verify_errors", 0) == 0
                ver = d["verify"]
                assert ver["bad"] == 0 and ver["torn"] == 0 and ver["zero"] == 0

    llc_mean = [med([d["victim_res"]["net_mean_ns"] for d in by_gt[("B", f"llc_k32__{a}")]]) for a, _ in conds]
    llc_p99 = [med([d["victim_res"]["p99_ns"] for d in by_gt[("B", f"llc_k32__{a}")]]) for a, _ in conds]
    llc_cha = [med([d["cha_occ_all"] for d in by_gt[("B", f"llc_k32__{a}")]]) for a, _ in conds]
    llc_m2p = [med([d["m2p_ne_pct"] for d in by_gt[("B", f"llc_k32__{a}")]]) for a, _ in conds]
    stream_bw = [med([d["victim_res"]["mean_gb_s"] for d in by_gt[("B", f"stream_6c__{a}")]]) for a, _ in conds]
    stream_min = [min([d["victim_res"]["mean_gb_s"] for d in by_gt[("B", f"stream_6c__{a}")]]) for a, _ in conds]
    stream_max = [max([d["victim_res"]["mean_gb_s"] for d in by_gt[("B", f"stream_6c__{a}")]]) for a, _ in conds]
    stream_m2p = [med([d["m2p_ne_pct"] for d in by_gt[("B", f"stream_6c__{a}")]]) for a, _ in conds]
    full_mpps = med([d["actual_mpps"] for d in by_gt[("B", "llc_k32__opus_1c_full_nf")]])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.4))
    x = np.arange(len(conds))
    labels = [lbl for _, lbl in conds]

    # ---- Left panel: 16 MiB LLC latency (mean & p99, K=32 window) ----
    ax1.axvspan(-0.5, 5.5, color="#eafaf1", alpha=0.82, zorder=0)
    ax1.axvspan(5.5, 9.5, color="#fdedec", alpha=0.82, zorder=0)
    ax1.axvline(5.5, color="#888888", linestyle="--", linewidth=1.0)

    ax1.plot(x, llc_mean, "o-", color="#1f77b4", linewidth=2.2, markersize=6.2, label="平均访存延迟")
    ax1.plot(x, llc_p99, "s--", color="#d64541", linewidth=1.8, markersize=5.5, label="p99 尾延迟（K = 32 窗口）")

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=8.2)
    ax1.set_ylabel("同 Socket 邻居 16 MiB L3 访存延迟（ns）", fontsize=10)
    ax1.set_title("(a) 邻居 L3 延迟（K = 32 窗口）：真实信用平直，无 SFENCE 满速越悬崖", fontsize=10.3, pad=10)
    ax1.set_xlim(-0.55, 9.55)
    ax1.set_ylim(0, 315)
    ax1.legend(loc="upper left", frameon=True, fontsize=8.8)
    ax1.text(
        2.5,
        152,
        f"真实信用速率（0.5–7 Mpps，无 SFENCE）\n"
        f"1–8 核延迟变化 < 0.3%（严格零干扰）\n"
        f"M2PCIe1 RxC ≤ {max(llc_m2p[:6]):.1f}%  ·  CHA 总占用 ≤ {max(llc_cha[:6]):.1f}",
        ha="center",
        va="center",
        fontsize=8.6,
        color="#196f3d",
        bbox=dict(boxstyle="round,pad=0.32", fc="white", ec="#27ae60", lw=0.75, alpha=0.94),
    )
    ax1.text(
        9.40,
        274,
        f"单核满速 (~{full_mpps:.0f} Mpps) L3 实测反转（SF8192 → 无 SFENCE）：\n"
        f"M2PCIe1 RxC {llc_m2p[8]:.1f}% → {llc_m2p[9]:.1f}% (>94% 零排空悬崖)\n"
        f"CHA_TOR_OCC_ALL 反降 {llc_cha[8]:.1f} → {llc_cha[9]:.1f} (破 ≤330 线)",
        ha="right",
        va="center",
        multialignment="left",
        fontsize=7.5,
        color="#922b21",
        bbox=dict(boxstyle="round,pad=0.28", fc="#fff8f7", ec="#d64541", lw=0.75, alpha=0.95),
    )

    ax1.text(0.05, llc_mean[0] + 15, f"{llc_mean[0]:.1f} ns", fontsize=8.6, color="#1f77b4")
    ax1.text(4.55, llc_mean[5] + 15, f"{llc_mean[5]:.1f} ns", fontsize=8.6, color="#1f77b4")
    ax1.annotate(
        f"均值 {llc_mean[-1]:.1f} ns\n(+{(llc_mean[-1]/llc_mean[0]-1)*100:.0f}%)",
        xy=(9, llc_mean[-1]),
        xytext=(5.85, 114),
        arrowprops=dict(arrowstyle="->", color="#1f77b4", lw=1.1),
        fontsize=8.8,
        color="#0b4f8c",
    )
    ax1.annotate(
        f"p99 {llc_p99[-1]:.0f} ns\n(K=32 窗口)",
        xy=(9, llc_p99[-1]),
        xytext=(5.80, 186),
        arrowprops=dict(arrowstyle="->", color="#d64541", lw=1.1),
        fontsize=8.8,
        color="#922b21",
    )

    # ---- Right panel: 6-core STREAM Triad memory bandwidth ----
    ax2.axvspan(-0.5, 5.5, color="#eafaf1", alpha=0.82, zorder=0)
    ax2.axvspan(5.5, 9.5, color="#fdedec", alpha=0.82, zorder=0)
    ax2.axvline(5.5, color="#888888", linestyle="--", linewidth=1.0)

    bar_colors = ["#7f8c8d"] + ["#1f77b4"] * 5 + ["#95a5a6"] * 3 + ["#d64541"]
    ax2.bar(x, stream_bw, width=0.60, color=bar_colors, edgecolor="#2c3e50", linewidth=0.6, zorder=3)
    ax2.errorbar(
        x,
        stream_bw,
        yerr=[
            [m - lo for m, lo in zip(stream_bw, stream_min)],
            [hi - m for m, hi in zip(stream_bw, stream_max)],
        ],
        fmt="none",
        ecolor="#222222",
        capsize=2.5,
        linewidth=1.0,
        zorder=4,
    )
    ax2.axhline(stream_bw[0], color="#555555", linestyle=":", linewidth=1.0, zorder=2)

    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, fontsize=8.2)
    ax2.set_ylabel("同 Socket 邻居 6 核内存带宽（GB/s）", fontsize=10)
    ax2.set_title("(b) 邻居内存带宽：1–8 核信用零掉速，单核无 SFENCE 满速掉速 25%", fontsize=10.5, pad=10)
    ax2.set_ylim(0, 66)

    # Place labels cleanly away from the 50.0 GB/s baseline dotted line
    ax2.text(0, stream_bw[0] + 1.5, f"{stream_bw[0]:.1f}", ha="center", va="bottom", fontsize=8.8, color="#2c3e50")
    ax2.text(
        4.75,
        stream_bw[5] + 1.5,
        f"{stream_bw[5]:.1f}\n({(stream_bw[5]-stream_bw[0])/stream_bw[0]*100:+.1f}%)",
        ha="center",
        va="bottom",
        fontsize=8.8,
        color="#0b4f8c",
    )
    ax2.annotate(
        f"{stream_bw[8]:.1f} ({(stream_bw[8]-stream_bw[0])/stream_bw[0]*100:+.1f}%)\nM2P {stream_m2p[8]:.1f}%",
        xy=(8, stream_bw[8] + 0.6),
        xytext=(7.75, stream_bw[0] + 2.4),
        ha="center",
        va="bottom",
        fontsize=8.2,
        color="#444444",
        arrowprops=dict(arrowstyle="->", color="#666666", lw=0.9, shrinkA=1, shrinkB=2),
    )
    ax2.text(
        9,
        stream_bw[9] + 1.5,
        f"{stream_bw[9]:.1f}\n({(stream_bw[9]-stream_bw[0])/stream_bw[0]*100:+.1f}%)\nM2P {stream_m2p[9]:.1f}%",
        ha="center",
        va="bottom",
        fontsize=8.1,
        color="#922b21",
    )
    fig.suptitle(
        "MRV-212 对照 B：无 SFENCE 信用在 0.5–7 Mpps（1–8 核）严格零干扰，但单核满速无 SFENCE 会打爆同 Socket 邻居",
        fontsize=11.8,
        y=0.97,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    png = mrvplot.HERE / "mrv-212-neighbor-boundary.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-212-neighbor-boundary.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
