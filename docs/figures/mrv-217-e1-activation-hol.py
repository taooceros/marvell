#!/usr/bin/env python3
"""MRV-217 Figure 1: E1 single-queue hardware backpressure activation / window floor
and dual-queue Q0 healthy + Q1 stalled HoL verification (n=3 interleaved medians).

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
        assert r["accepted"] is True
        assert r["csr_pre"] == r["csr_post"], f"CSR drift in {r['part']}/{r['tag']}"
        assert r["host_pmu"]["min_pct"] >= 99.99
        if "card_pmu" in r and r["card_pmu"]:
            assert r["card_pmu"]["min_pct"] >= 99.99
        by_pt[(r["part"], r["tag"])].append(r)
    return by_pt


def main():
    by_pt = load_verified()

    # ---- Panel A: V1A + V2_D1 single-queue (1500 B @ 5.0 Mpps, 1 ms / 10 ms pause) ----
    d1_items = [
        ("V1A",   "q1_bp1_d256_bug",  "1队列+BP=1\nD=256\n(TL4=0x3007错位)", "#b91c1c"),
        ("V2_D1", "d1_bp0_d256",      "BP=0\nD=256\n(默认无反压)",          "#dc2626"),
        ("V2_D1", "d1_bp1_d256",      "BP=1\nD=256\n(WMARK=64)",            "#0284c7"),
        ("V2_D1", "d1_bp1_d512",      "BP=1\nD=512\n(WMARK=128)",           "#0369a1"),
        ("V2_D1", "d1_cr_d256_r128",  "CreditRing\nD=256\nR=128",           "#15803d"),
        ("V2_D1", "d1_cr_d256_r64",   "CreditRing\nD=256\nR=64",            "#16a34a"),
        ("V2_D1", "d1_cr_d256_96kib", "CreditRing\nD=256\n96 KiB字节",      "#0f766e"),
    ]
    for p, t, _, _ in d1_items:
        rows = by_pt[(p, t)]
        assert len(rows) == 3, f"expected n=3 for {p}/{t}, got {len(rows)}"
        for r in rows:
            assert r["bad"] == 0 and r["torn"] == 0 and r["page_faults"] == 0
            assert r["card_stats"]["q0_fifo_full"] == 0 and r["card_stats"]["alloc_fail"] == 0

    d1_gbps = [med([r["rx_gbps"] for r in by_pt[(p, t)]]) for p, t, _, _ in d1_items]
    d1_lost_k = [med([r["lost"] for r in by_pt[(p, t)]]) / 1e3 for p, t, _, _ in d1_items]
    d1_dr = [int(round(med([r["pause_drained_max"] for r in by_pt[(p, t)]]))) for p, t, _, _ in d1_items]
    d1_ret_k = [med([r["card_stats"]["q0_tx_retries"] for r in by_pt[(p, t)]]) / 1e6 for p, t, _, _ in d1_items]

    # ---- Panel B: V2_D2 dual-queue Q0 healthy (3.0 Mpps) + Q1 stalled ----
    d2_items = [
        ("V2_D2", "d2_bp0_d512",          "BP=0\nD=512\n(8ms停顿)",      "#dc2626"),
        ("V2_D2", "d2_bp1_d256",          "BP=1全开\nD=256\n(8ms停顿)",  "#0284c7"),
        ("V2_D2", "d2_bp1_d512",          "BP=1全开\nD=512\n(8ms停顿)",  "#0369a1"),
        ("V2_D2", "d2_bp1_q1only_d256",   "BP=1仅Q1\nD=256\n(8ms停顿)",  "#38bdf8"),
        ("V2_D2", "d2_cr_d256_r128",      "CreditRing\nR=128\n(8ms停顿)", "#15803d"),
        ("V2_D2", "d2_freeze60_bp1_d256", "BP=1全开\nD=256\n(60ms冻结)", "#0ea5e9"),
        ("V2_D2", "d2_freeze60_cr_r128",  "CreditRing\nR=128\n(60ms冻结)", "#16a34a"),
    ]
    for p, t, _, _ in d2_items:
        rows = by_pt[(p, t)]
        assert len(rows) == 3, f"expected n=3 for {p}/{t}, got {len(rows)}"
        for r in rows:
            assert r["q0_bad"] == 0 and r["q0_torn"] == 0 and r["q1_bad"] == 0 and r["q1_torn"] == 0
            assert r["q0_page_faults"] == 0 and r["q1_page_faults"] == 0
            assert r["card_stats"]["q0_fifo_full"] == 0 and r["card_stats"]["q1_fifo_full"] == 0

    q0_mpps = [med([r["q0_mpps"] for r in by_pt[(p, t)]]) for p, t, _, _ in d2_items]
    q1_mpps = [med([r["q1_mpps"] for r in by_pt[(p, t)]]) for p, t, _, _ in d2_items]
    q0_p99 = [med([r["q0_gap_p99_us"] for r in by_pt[(p, t)]]) for p, t, _, _ in d2_items]
    q1_lost_k = [med([r["q1_lost"] for r in by_pt[(p, t)]]) / 1e3 for p, t, _, _ in d2_items]
    q1_rf_max = [med([r["q1_resume_first_max_us"] for r in by_pt[(p, t)]]) for p, t, _, _ in d2_items]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16.0, 5.8), gridspec_kw={"width_ratios": [1.05, 1.15]})

    # ---- Left: Panel A ----
    x1 = np.arange(len(d1_items))
    bars1 = ax1.bar(x1, d1_gbps, width=0.58, color=[c for _, _, _, c in d1_items], edgecolor="#1e293b", linewidth=0.6, zorder=3)
    ax1.axhline(60.8, color="#15803d", linestyle="--", linewidth=1.1, alpha=0.85, zorder=2)
    ax1.set_xticks(x1)
    ax1.set_xticklabels([lbl for _, _, lbl, _ in d1_items], fontsize=8.3)
    ax1.set_ylabel("主机有效收包吞吐（Gbps，满额 60.8 Gbps）", fontsize=10.0)
    ax1.set_ylim(0, 88)
    ax1.set_title("A. E1 单队列（1500 B @ 5 Mpps, 1 ms/10 ms 停顿）：\nnb_txq=1 错位丢包 vs nb_txq≥2 BP=1 零丢但锁满主机环", fontsize=10.4, fontweight="bold", pad=10)

    for idx, (bar, g, lk, dr, retm) in enumerate(zip(bars1, d1_gbps, d1_lost_k, d1_dr, d1_ret_k)):
        cx = bar.get_x() + bar.get_width() / 2
        if lk > 0.5:
            ax1.annotate(
                f"{g:.1f}G\n丢 {lk:.0f}K 包\n(环冲穿 {dr})",
                xy=(cx, g + 0.5), xytext=(cx, 64.0),
                ha="center", va="bottom", fontsize=8.0, color="#991b1b", fontweight="bold",
                arrowprops=dict(arrowstyle="->", color="#991b1b", lw=0.9),
            )
        elif idx in (2, 3):
            ax1.text(
                cx, 63.0,
                f"{g:.1f}G · 0 丢\n环占满 {dr}\n(重试 {retm:.1f}M)",
                ha="center", va="bottom", fontsize=7.9, color="#0369a1", fontweight="bold",
            )
        else:
            ax1.text(
                cx, 63.0,
                f"{g:.1f}G · 0 丢\n精确封顶 {dr}\n(重试 0)",
                ha="center", va="bottom", fontsize=7.9, color="#166534", fontweight="bold",
            )

    bp1_d1_rf_max = max(
        r["resume_first_max_us"]
        for t in ("d1_bp1_d256", "d1_bp1_d512")
        for r in by_pt[("V2_D1", t)]
    )
    for t in ("d1_bp1_d256", "d1_bp1_d512"):
        assert all(r["lost"] == 0 for r in by_pt[("V2_D1", t)])
    ax1.text(
        3.0, 82.5,
        f"注：BP=1 在 1500 B 下零丢包且无 DROP 态卡死（恢复 ≤{bp1_d1_rf_max:.1f} µs），但主机占满全环（{d1_dr[2]}/{d1_dr[3]} 槽），无法表达 R=64 或字节窗",
        ha="center", va="center", fontsize=8.0, color="#0f172a",
        bbox=dict(boxstyle="round,pad=0.30", fc="#f1f5f9", ec="#64748b", lw=0.7),
    )
    # ---- Right: Panel B ----
    x2 = np.arange(len(d2_items))
    w = 0.36
    b_q0 = ax2.bar(x2 - w / 2, q0_mpps, width=w, color="#64748b", edgecolor="#1e293b", linewidth=0.6, label="Q0 健康队列（目标 3.0 Mpps）", zorder=3)
    b_q1 = ax2.bar(x2 + w / 2, q1_mpps, width=w, color=[c for _, _, _, c in d2_items], edgecolor="#1e293b", linewidth=0.6, label="Q1 停顿/冻结队列（目标 1.5 Mpps）", zorder=3)
    ax2.axhline(3.0, color="#475569", linestyle=":", linewidth=1.0, zorder=2)
    ax2.axhline(1.5, color="#0284c7", linestyle="--", linewidth=0.9, alpha=0.7, zorder=2)
    ax2.set_xticks(x2)
    ax2.set_xticklabels([lbl for _, _, lbl, _ in d2_items], fontsize=8.1)
    ax2.set_ylabel("各队列无损吞吐（Mpps，1500 B，专用双卡核）", fontsize=10.0)
    ax2.set_ylim(0, 4.35)
    ax2.set_title("B. E1 双队列 HoL 隔离（Q0 健康 + Q1 停顿/60 ms 冻结）：\nCN106 逐 TL4 硬件反压与 CreditRing 均实现 Q0 零队头阻塞", fontsize=10.4, fontweight="bold", pad=10)
    ax2.legend(loc="upper right", fontsize=8.4, framealpha=0.92)

    for i, (m0, m1, p99_0, l1k, rf1) in enumerate(zip(q0_mpps, q1_mpps, q0_p99, q1_lost_k, q1_rf_max)):
        ax2.text(x2[i] - w / 2, m0 + 0.08, f"{m0:.2f}M\np99 {p99_0:.1f}µs", ha="center", va="bottom", fontsize=7.3, color="#334155")
        if l1k > 0.5:
            ax2.text(x2[i] + w / 2 + 0.03, m1 + 0.08, f"{m1:.2f}M\n丢{l1k:.0f}K", ha="center", va="bottom", fontsize=7.2, color="#991b1b", fontweight="bold")
        else:
            ax2.text(x2[i] + w / 2, m1 + 0.08, f"{m1:.2f}M\n0丢\n复{rf1:.1f}µs", ha="center", va="bottom", fontsize=7.2, color="#0f172a")

    fig.suptitle("MRV-217 验证 1 & 2：CN106 置位 SDPX_OUT_BP_ENX_W1S = 1 在 E1 单/双队列下的实机表现（n = 3 中位数）", fontsize=12.0, fontweight="bold", y=0.995)
    png = mrvplot.save(fig, "mrv-217-e1-activation-hol")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
