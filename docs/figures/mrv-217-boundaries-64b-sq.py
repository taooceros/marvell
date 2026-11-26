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

    bp1_14m_losses = [r["lost"] for r in by_pt[("V1B", "64B_14M_fifo_bp1_d256")]]
    assert all(x > 0 for x in bp1_14m_losses), f"expected >0 skid loss in 64B_14M_fifo_bp1_d256, got {bp1_14m_losses}"
    for t in ("64B_14M_fifo_bp1_d512", "64B_14M_fifo_cr_d256",
              "64B_25M_nofifo_bp1_d256", "64B_25M_nofifo_bp1_d512", "64B_25M_nofifo_cr_d256"):
        assert all(r["lost"] == 0 for r in by_pt[("V1B", t)]), f"unexpected drop in V1B/{t}"

    lost_vals = [med([r["lost"] for r in by_pt[("V1B", t)]]) for t, _, _ in v1b_items]
    mpps_vals = [med([r["rx_mpps"] for r in by_pt[("V1B", t)]]) for t, _, _ in v1b_items]
    dr_vals = [int(round(med([r["pause_drained_max"] for r in by_pt[("V1B", t)]]))) for t, _, _ in v1b_items]
    is_cr = ["_cr_" in t for t, _, _ in v1b_items]

    # ---- Panel B: Directly measured software FIFO capture share (fifo_max_depth / burst_pkts) ----
    # Note: E2 card ran with `--sdp-tx-desc 2048` (4Q total 8,192 hw NIX SQ descriptors),
    # which absorbs ~7,800 packets under BP=1 before card software sees tx_burst stalls.
    e2_items = [
        ("bp1_d2048",   "BP=1 默认最小环\nD=2048\n(WM=256)"),
        ("bp1_d512",    "BP=1 + 卡FIFO\nD=512\n(WM=128)"),
        ("bp1_d256",    "BP=1 + 卡FIFO\nD=256\n(WM=64)"),
        ("credit_ring", "CreditRing\nD=512\n(BAR4 R=256)"),
    ]
    pct_16, pk_16 = [], []
    pct_128, pk_128 = [], []
    sq_samples = []
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
        if key in ("bp1_d512", "bp1_d256"):
            rxd = r16[0]["rx_desc"]
            wm = rxd // 4
            host_est = 4 * (rxd - wm)
            sq_samples.append(r16[0]["burst_pkts"] - f16 - host_est)
            sq_samples.append(r128[0]["burst_pkts"] - f128 - host_est)
        elif key == "credit_ring":
            assert all(r["card_stats"]["sdp_tx_stalls"] == 0 for r in r16 + r128)
            assert r16[0]["burst_pkts"] - f16 == 4 * r16[0]["r_eff"]
            assert r128[0]["burst_pkts"] - f128 == 4 * r128[0]["r_eff"]

    sq_est_round = int(round(med(sq_samples) / 100.0) * 100)
    assert 7500 <= sq_est_round <= 8100, f"unexpected 4Q hw SQ absorption estimate: {sq_est_round}"

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16.6, 6.0), gridspec_kw={"width_ratios": [1.14, 1.06]})

    # ---- Panel A ----
    x1 = np.arange(len(v1b_items))
    bars = ax1.bar(x1, mpps_vals, width=0.56, color=[c for _, _, c in v1b_items], edgecolor="#1e293b", linewidth=0.6, zorder=3)
    ax1.axvline(3.5, color="#94a3b8", linestyle="--", linewidth=1.0)
    ax1.set_xticks(x1)
    ax1.set_xticklabels([lbl for _, lbl, _ in v1b_items], fontsize=8.0)
    ax1.set_ylabel("主机实测有效收包率（Mpps，64 B 帧）", fontsize=9.8)
    ax1.set_ylim(0, max(mpps_vals) * 1.48)
    ax1.set_title("A. 64 B 高包率（零卡侧溢出）：WMARK=64 (D/4 补丁) 滑行边界 vs CreditRing", fontsize=10.1, fontweight="bold", pad=10)

    for bar, mp, lv, dr, cr_flag in zip(bars, mpps_vals, lost_vals, dr_vals, is_cr):
        cx = bar.get_x() + bar.get_width() / 2
        dr_tag = f"封顶 {dr}" if cr_flag else f"排空 {dr}"
        if lv > 0:
            lstr = f"丢 {lv/1e3:.0f}K" if lv >= 1000 else f"丢 {int(lv)} 包"
            ax1.text(cx, mp + 0.5, f"{mp:.1f}M\n{lstr}\n({dr_tag})", ha="center", va="bottom", fontsize=7.6, color="#991b1b", fontweight="bold")
        else:
            ax1.text(cx, mp + 0.5, f"{mp:.1f}M\n0 丢包\n({dr_tag})", ha="center", va="bottom", fontsize=7.6, color="#166534", fontweight="bold")

    ax1.text(
        1.5, max(mpps_vals) * 1.24,
        f"14M+FIFO 下 BP=1 D=256 (WM=64，来自 D/4 补丁)\n"
        f"3 轮稳定滑行丢 {min(bp1_14m_losses)}–{max(bp1_14m_losses)} 包；增至 D=512 (WM=128) 归零\n"
        f"CreditRing 入 SQ 前门控全程 0 丢（满 FIFO 过载边界见正文）",
        ha="center", va="center", fontsize=7.4, color="#0f172a",
        bbox=dict(boxstyle="round,pad=0.30", fc="#f8fafc", ec="#64748b", lw=0.7),
    )

    # ---- Panel B: Directly measured fifo_max_depth / burst_pkts (%) ----
    x2 = np.arange(len(e2_items))
    w = 0.36
    ax2.bar(x2 - w / 2, pct_16, width=w, color="#0284c7", edgecolor="#1e293b", linewidth=0.6, label="16 MiB 突发（实测 fifo_max_depth / 11,264 包）", zorder=3)
    ax2.bar(x2 + w / 2, pct_128, width=w, color="#15803d", edgecolor="#1e293b", linewidth=0.6, label="128 MiB 突发（实测 fifo_max_depth / 89,600 包）", zorder=3)

    ax2.set_xticks(x2)
    ax2.set_xticklabels([lbl for _, lbl in e2_items], fontsize=8.3)
    ax2.set_ylabel("实测卡侧软件 FIFO 峰值占比（%）", fontsize=9.8)
    ax2.set_ylim(0, 140)
    ax2.set_title("B. E2 软件 FIFO 峰值占比（当前配置 sdp_tx_desc=2048，4Q 共 8,192 槽）", fontsize=10.1, fontweight="bold", pad=10)
    ax2.legend(loc="upper left", bbox_to_anchor=(0.0, 0.86), fontsize=7.6, framealpha=0.92)

    for i in range(len(e2_items)):
        ax2.text(x2[i] - w / 2, pct_16[i] + 1.8, f"{pct_16[i]:.1f}%\n({pk_16[i]/1e3:.1f}K)", ha="center", va="bottom", fontsize=7.6, color="#0369a1", fontweight="bold")
        ax2.text(x2[i] + w / 2, pct_128[i] + 1.8, f"{pct_128[i]:.1f}%\n({pk_128[i]/1e3:.1f}K)", ha="center", va="bottom", fontsize=7.6, color="#166534", fontweight="bold")

    ax2.text(
        1.5, 130.5,
        f"注：16M 占比 {pct_16[1]:.1f}%–{pct_16[2]:.1f}% 受 sdp_tx_desc=2048 配置支配（4Q 硬件 SQ 实测吞约 {sq_est_round:,} 包），非不可调硬件常数；\n"
        f"架构差异在于准入位置：CreditRing 入 SQ 前门控令硬件 SQ 零积压（sdp_tx_stalls=0），保留软件优先级可见性",
        ha="center", va="center", fontsize=7.4, color="#0f172a",
        bbox=dict(boxstyle="round,pad=0.30", fc="#f1f5f9", ec="#64748b", lw=0.7),
    )

    fig.suptitle("MRV-217 实测边界对比：64 B 反应式滑行（WMARK=64）与 E2 软件 FIFO 占比（sdp_tx_desc=2048）", fontsize=11.5, fontweight="bold", y=0.995)
    png = mrvplot.save(fig, "mrv-217-boundaries-64b-sq")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
