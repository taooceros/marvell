#!/usr/bin/env python3
"""MRV-217 Figure 3: Directly measured operational boundaries between CN106 hardware
backpressure (BP=1) and explicit BAR4 CreditRing (n=3 interleaved medians):
  Panel A: High-packet-rate 64 B WMARK=64 skid boundary (14 Mpps + FIFO & 25 Mpps no-FIFO,
           zero card FIFO overflow) comparing BP=1 D=256 vs BP=1 D=512 vs CreditRing D=256.
  Panel B: Directly measured card software elastic FIFO peak (`card_stats.fifo_max_depth`)
           as % of `burst_pkts` at 16 MiB (11,264 pkts) and 128 MiB (89,600 pkts) —
           100% directly measured quantities only.

100% dynamically computed from `logs/mrv217-2026-11-26/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs" / "mrv217-2026-11-26" / "runs.jsonl"


def med(xs):
    return statistics.median(xs)


def load_verified():
    by_pt = defaultdict(list)
    for ln in RUNS.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        assert r["accepted"] is True and r["csr_pre"] == r["csr_post"]
        by_pt[(r["part"], r["tag"])].append(r)
    return by_pt


def main():
    by_pt = load_verified()

    # ---- Panel A: V1B 64 B high-rate skid boundary ----
    v1b_items = [
        ("64B_14M_fifo_bp0_d256",   "14M+FIFO\nBP=0\nD=256",          "#dc2626"),
        ("64B_14M_fifo_bp1_d256",   "14M+FIFO\nBP=1\nD=256\n(WM=64)", "#0284c7"),
        ("64B_14M_fifo_bp1_d512",   "14M+FIFO\nBP=1\nD=512\n(WM=128)", "#0369a1"),
        ("64B_14M_fifo_cr_d256",    "14M+FIFO\nCreditRing\nR=128",    "#15803d"),
        ("64B_25M_nofifo_bp1_d256", "25M直发\nBP=1\nD=256\n(WM=64)",  "#f59e0b"),
        ("64B_25M_nofifo_bp1_d512", "25M直发\nBP=1\nD=512\n(WM=128)", "#0ea5e9"),
        ("64B_25M_nofifo_cr_d256",  "25M直发\nCreditRing\nR=128",     "#16a34a"),
    ]
    for t, _, _ in v1b_items:
        rows = by_pt[("V1B", t)]
        assert len(rows) == 3, f"expected n=3 for V1B/{t}"
        for r in rows:
            assert r["bad"] == 0 and r["torn"] == 0 and r["page_faults"] == 0
            assert r["card_stats"]["q0_fifo_full"] == 0 and r["card_stats"]["alloc_fail"] == 0

    lost_vals = [med([r["lost"] for r in by_pt[("V1B", t)]]) for t, _, _ in v1b_items]
    mpps_vals = [med([r["rx_mpps"] for r in by_pt[("V1B", t)]]) for t, _, _ in v1b_items]
    dr_vals = [int(round(med([r["pause_drained_max"] for r in by_pt[("V1B", t)]]))) for t, _, _ in v1b_items]

    # ---- Panel B: Directly measured software FIFO capture share (fifo_max_depth / burst_pkts) ----
    e2_items = [
        ("bp1_d2048",   "BP=1 未改驱动\nD=2048\n(WM=256)"),
        ("bp1_d512",    "BP=1 + 卡FIFO\nD=512\n(WM=128)"),
        ("bp1_d256",    "BP=1 + 卡FIFO\nD=256\n(WM=64)"),
        ("credit_ring", "CreditRing\nD=512\n(BAR4 R=256)"),
    ]
    pct_16, pk_16 = [], []
    pct_128, pk_128 = [], []
    for key, _ in e2_items:
        r16 = by_pt[("V3_E2", f"16MiB_{key}")]
        r128 = by_pt[("V3_E2", f"128MiB_{key}")]
        assert len(r16) == 3 and len(r128) == 3
        f16 = med([r["card_stats"]["fifo_max_depth"] for r in r16])
        f128 = med([r["card_stats"]["fifo_max_depth"] for r in r128])
        pct_16.append(100.0 * f16 / r16[0]["burst_pkts"])
        pk_16.append(f16)
        pct_128.append(100.0 * f128 / r128[0]["burst_pkts"])
        pk_128.append(f128)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.8, 5.6), gridspec_kw={"width_ratios": [1.18, 1.0]})

    # ---- Panel A ----
    x1 = np.arange(len(v1b_items))
    bars = ax1.bar(x1, mpps_vals, width=0.58, color=[c for _, _, c in v1b_items], edgecolor="#1e293b", linewidth=0.6, zorder=3)
    ax1.axvline(3.5, color="#94a3b8", linestyle="--", linewidth=1.0)
    ax1.set_xticks(x1)
    ax1.set_xticklabels([lbl for _, lbl, _ in v1b_items], fontsize=8.0)
    ax1.set_ylabel("主机实测有效收包率（Mpps，64 B 帧）", fontsize=9.8)
    ax1.set_ylim(0, max(mpps_vals) * 1.40)
    ax1.set_title("A. 64 B 高包率（零卡侧溢出）：WMARK=64 滑行边界 vs CreditRing", fontsize=10.3, fontweight="bold", pad=10)

    for bar, mp, lv, dr in zip(bars, mpps_vals, lost_vals, dr_vals):
        cx = bar.get_x() + bar.get_width() / 2
        if lv > 0:
            lstr = f"丢 {lv/1e3:.0f}K" if lv >= 1000 else f"丢 {int(lv)} 包"
            ax1.text(cx, mp + 0.5, f"{mp:.1f}M\n{lstr}\n(环 {dr})", ha="center", va="bottom", fontsize=7.8, color="#991b1b", fontweight="bold")
        else:
            ax1.text(cx, mp + 0.5, f"{mp:.1f}M\n0 丢包\n(环 {dr})", ha="center", va="bottom", fontsize=7.8, color="#166534", fontweight="bold")

    # ---- Panel B: Directly measured fifo_max_depth / burst_pkts (%) ----
    x2 = np.arange(len(e2_items))
    w = 0.36
    ax2.bar(x2 - w / 2, pct_16, width=w, color="#0284c7", edgecolor="#1e293b", linewidth=0.6, label="16 MiB 突发（实测 fifo_max_depth / 11,264 包）", zorder=3)
    ax2.bar(x2 + w / 2, pct_128, width=w, color="#15803d", edgecolor="#1e293b", linewidth=0.6, label="128 MiB 突发（实测 fifo_max_depth / 89,600 包）", zorder=3)

    ax2.set_xticks(x2)
    ax2.set_xticklabels([lbl for _, lbl in e2_items], fontsize=8.4)
    ax2.set_ylabel("实测卡侧软件 FIFO 峰值占比（%）", fontsize=9.8)
    ax2.set_ylim(0, 122)
    ax2.set_title("B. E2 实测软件 FIFO 峰值占比（其余滞留主机环与硬件管线）", fontsize=10.3, fontweight="bold", pad=10)
    ax2.legend(loc="upper left", fontsize=7.9, framealpha=0.92)

    for i in range(len(e2_items)):
        ax2.text(x2[i] - w / 2, pct_16[i] + 1.8, f"{pct_16[i]:.1f}%\n({pk_16[i]/1e3:.1f}K)", ha="center", va="bottom", fontsize=7.7, color="#0369a1", fontweight="bold")
        ax2.text(x2[i] + w / 2, pct_128[i] + 1.8, f"{pct_128[i]:.1f}%\n({pk_128[i]/1e3:.1f}K)", ha="center", va="bottom", fontsize=7.7, color="#166534", fontweight="bold")

    fig.suptitle("MRV-217 实测边界对比：64 B 高包率 WMARK=64 滑行丢包与 E2 卡侧软件 FIFO 可控占比", fontsize=11.6, fontweight="bold", y=0.995)
    png = mrvplot.save(fig, "mrv-217-boundaries-64b-sq")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
