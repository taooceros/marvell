#!/usr/bin/env python3
"""MRV-229 Figure 1: Raw interface latency and single-core 100GbE throughput
(DirectPush vs tuned SDP).

100% dynamically computed from archived raw verified logs:
  - logs/mrv166-2026-11-23/runs.jsonl          (same-card 64 B W=1 latency)
  - logs/mrv195-2026-11-25/runs.jsonl          (dual-machine 100GbE 64 B W=1 latency)
  - logs/mrv155-2026-11-23/runs_phase2.jsonl   (single-core 100GbE TX: 64 B / 256 B / 4 KiB)
  - logs/mrv172-2026-11-24/runs.jsonl          (single-core 100GbE RX: 64 B / 256 B / 4 KiB)

Run:
    uv run --with matplotlib python docs/figures/mrv-229-raw-latency-tput.py
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

C_SDP = "#64748b"       # Tuned SDP slate grey
C_SDP_ALT = "#94a3b8"   # Secondary SDP (static testpmd / pure PCIe)
C_PEEK = "#2563eb"      # DirectPush blue (standard / cqe-peek)
C_SAFE = "#16a34a"      # DirectPush emerald (cqe-direct-safe)
C_WIRE = "#059669"      # 100GbE line rate reference


def med(xs):
    return statistics.median(xs)


def load_mrv166():
    path = ROOT / "logs/mrv166-2026-11-23/runs.jsonl"
    by = defaultdict(list)
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        assert r.get("verified") is True
        by[(r["part"], r["cfg"])].append(r)
    for cfg in ("sdp_fair_64_w1", "directpush_64_w1"):
        rs = by[("part1_w1", cfg)]
        assert len(rs) == 3
        for r in rs:
            assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
    return {
        "sdp_64_p50": med([r["lat_all_us"]["p50"] for r in by[("part1_w1", "sdp_fair_64_w1")]]),
        "dp_64_p50": med([r["lat_all_us"]["p50"] for r in by[("part1_w1", "directpush_64_w1")]]),
    }


def load_mrv195_w1():
    path = ROOT / "logs/mrv195-2026-11-25/runs.jsonl"
    by = defaultdict(list)
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        assert r.get("verified") is True
        if r.get("part") == "part2a_w1" and r.get("workload") == "get64":
            c = r["client"]
            assert c["lost"] == 0 and c["bad"] == 0 and c["torn"] == 0
            by[r["policy"]].append(r)
    for p in ("sdp_fair", "direct_peek", "direct_safe"):
        assert len(by[p]) == 3
    return {
        "sdp_p50": med([r["client"]["tot_p50_us"] for r in by["sdp_fair"]]),
        "peek_p50": med([r["client"]["tot_p50_us"] for r in by["direct_peek"]]),
        "safe_p50": med([r["client"]["tot_p50_us"] for r in by["direct_safe"]]),
    }


def load_mrv155():
    path = ROOT / "logs/mrv155-2026-11-23/runs_phase2.jsonl"
    by = defaultdict(list)
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        by[r["cfg"]].append(r)

    def phy_mpps(cfg):
        rs = by[cfg]
        assert len(rs) == 3
        for r in rs:
            assert r["card"]["bad"] == 0 and r["card"]["torn"] == 0 and r["mercury"]["bad"] == 0
        return med([r["mercury"]["phy"]["rx_mpps"] for r in rs])

    def phy_wire(cfg):
        rs = by[cfg]
        assert len(rs) == 3
        for r in rs:
            assert r["card"]["bad"] == 0 and r["card"]["torn"] == 0 and r["mercury"]["bad"] == 0
        return med([r["mercury"]["phy"]["wire_gbps"] for r in rs])

    tp_rs = by["tp_sdp_1H_64B_ism1"]
    assert len(tp_rs) == 3 and all(r["verified"] and r["card_rx_missed"] == 0 for r in tp_rs)
    s1_sdp_rs = by["s1_sdp_64B_ism1_c4"]
    assert len(s1_sdp_rs) == 3
    s1_sdp_mpps = med([r["card"]["pkts_in"] / r["host"]["elapsed_s"] / 1e6 for r in s1_sdp_rs])

    host_ghz = med([
        r["host_pmu"]["cpu40_ghz"]
        for cfg in ("s2_dp_raw64_c4", "s2_sdp_raw64_ism1_c4")
        for r in by[cfg]
    ])
    return {
        "host_ghz": host_ghz,
        "sdp_tx64_dyn": phy_mpps("s2_sdp_raw64_ism1_c4"),
        "sdp_tx64_s1_dyn": s1_sdp_mpps,
        "sdp_tx64_static": med([r["card_rx_mpps"] for r in tp_rs]),
        "dp_tx64": phy_mpps("s2_dp_raw64_c4"),
        "sdp_tx256_gbps": phy_wire("s2_sdp_roce256_ism1_c4"),
        "dp_tx256_gbps": phy_wire("s2_dp_roce256_c4"),
        "sdp_tx4k_gbps": phy_wire("s2_sdp_roce4096_ism1_c4"),
        "dp_tx4k_gbps": phy_wire("s2_dp_roce4096_c4"),
    }


def load_mrv172():
    path = ROOT / "logs/mrv172-2026-11-24/runs.jsonl"
    by = defaultdict(list)
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("verified"):
            by[r["cfg"]].append(r)

    def rx_stats(cfg):
        rs = by[cfg]
        assert len(rs) == 3
        return (
            med([r["rates"]["host_rx_mpps"] for r in rs]),
            med([r["rates"]["host_wire_gbps"] for r in rs]),
            med([r["segment_drops"]["c2h_to_host_drop"] for r in rs]),
        )

    dp64_mpps, _, dp64_c2h_drop = rx_stats("s2_dp_64B")
    assert dp64_c2h_drop == 0, f"expected zero c2h drop for s2_dp_64B, got {dp64_c2h_drop}"
    sdp64_e2e_mpps, _, sdp64_e2e_drop = rx_stats("s2_sdp_ism1_512_64B")
    assert sdp64_e2e_drop > 0
    sdp64_s1_mpps, _, sdp64_s1_drop = rx_stats("s1_sdp_64_ism1_2048_c4")
    assert sdp64_s1_drop > 0

    _, dp256_gbps, _ = rx_stats("s2_dp_326B")
    _, sdp256_gbps, _ = rx_stats("s2_sdp_ism1_326B")
    _, dp4k_gbps, _ = rx_stats("s2_dp_4096B")
    _, sdp4k_gbps, _ = rx_stats("s2_sdp_ism1_4096B")

    return {
        "sdp_rx64_e2e": sdp64_e2e_mpps,
        "sdp_rx64_s1": sdp64_s1_mpps,
        "dp_rx64": dp64_mpps,
        "sdp_rx256_gbps": sdp256_gbps,
        "dp_rx256_gbps": dp256_gbps,
        "sdp_rx4k_gbps": sdp4k_gbps,
        "dp_rx4k_gbps": dp4k_gbps,
    }


def main():
    d166 = load_mrv166()
    d195 = load_mrv195_w1()
    d155 = load_mrv155()
    d172 = load_mrv172()

    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(16.0, 5.8), gridspec_kw={"width_ratios": [1.05, 1.12, 1.0]}
    )

    # =========================================================================
    # Panel A: Unloaded 64 B RTT latency (W=1, lower is faster)
    # =========================================================================
    w1 = 0.25
    # Group 0: Same-card (2 bars centered at x=0)
    x0_sdp, x0_dp = -0.15, +0.15
    # Group 1: Dual-machine 100GbE (3 bars centered at x=1.08)
    x1_c = 1.08
    x1_sdp, x1_peek, x1_safe = x1_c - w1, x1_c, x1_c + w1

    ax1.bar([x0_sdp], [d166["sdp_64_p50"]], width=0.26, color=C_SDP, label="调优 SDP")
    ax1.bar([x0_dp], [d166["dp_64_p50"]], width=0.26, color=C_PEEK, label="DirectPush（标准 / 窥探版）")

    ax1.bar([x1_sdp], [d195["sdp_p50"]], width=w1 * 0.90, color=C_SDP)
    ax1.bar([x1_peek], [d195["peek_p50"]], width=w1 * 0.90, color=C_PEEK)
    ax1.bar([x1_safe], [d195["safe_p50"]], width=w1 * 0.90, color=C_SAFE, label="DirectPush（安全直取版）")

    for xv, val, col in [
        (x0_sdp, d166["sdp_64_p50"], "#334155"),
        (x0_dp, d166["dp_64_p50"], C_PEEK),
        (x1_sdp, d195["sdp_p50"], "#334155"),
        (x1_peek, d195["peek_p50"], C_PEEK),
        (x1_safe, d195["safe_p50"], C_SAFE),
    ]:
        ax1.text(xv, val + 0.22, f"{val:.2f}", ha="center", va="bottom",
                 fontsize=10.0, fontweight="bold", color=col)

    sp_same = d166["sdp_64_p50"] / d166["dp_64_p50"]
    sp_peek = d195["sdp_p50"] / d195["peek_p50"]
    sp_safe = d195["sdp_p50"] / d195["safe_p50"]

    ax1.text(
        0.0, d166["sdp_64_p50"] + 1.35,
        f"快 {sp_same:.1f}×\n(-{(1 - 1 / sp_same) * 100:.0f}%)",
        ha="center", va="bottom", fontsize=10.2, fontweight="bold", color=C_PEEK,
        bbox=dict(boxstyle="round,pad=0.30", fc="#eff6ff", ec=C_PEEK, lw=1.1),
    )
    ax1.text(
        x1_c, d195["sdp_p50"] + 1.15,
        f"快 {sp_peek:.1f}×–{sp_safe:.1f}×\n(-{(1 - 1 / sp_peek) * 100:.0f}% ~ -{(1 - 1 / sp_safe) * 100:.0f}%)",
        ha="center", va="bottom", fontsize=10.2, fontweight="bold", color=C_SAFE,
        bbox=dict(boxstyle="round,pad=0.30", fc="#f0fdf4", ec=C_SAFE, lw=1.1),
    )

    ax1.set_xticks([0.0, x1_c])
    ax1.set_xticklabels(["同卡 PCIe 往返\n(MRV-166)", "双机 100GbE 往返\n(MRV-195)"], fontsize=10.2, fontweight="bold")
    ax1.set_ylabel("空载 64 B 往返延迟 p50 (µs，越低越快)", fontsize=10.2)
    ax1.set_ylim(0, 13.8)
    ax1.set_xlim(-0.55, 1.62)
    ax1.set_title("A. 空载 64 B 往返延迟（W=1）", fontsize=11.5, fontweight="bold", pad=10)
    ax1.legend(loc="upper left", fontsize=8.8, frameon=True, facecolor="white", framealpha=0.95)

    # =========================================================================
    # Panel B: Single-core 64 B packet rate (TX & RX, honest MRV-205 bounds)
    # =========================================================================
    wb = 0.25
    # TX centered at 0.0, RX centered at 1.15
    tx_c, rx_c = 0.0, 1.15

    # TX bars
    ax2.bar([tx_c - wb], [d155["sdp_tx64_dyn"]], width=wb * 0.88, color=C_SDP, label="SDP（动态验真 / 端到端）")
    ax2.bar(
        [tx_c], [d155["sdp_tx64_static"]], width=wb * 0.88, color=C_SDP_ALT,
        hatch="//", edgecolor="#334155", linewidth=0.7, label="SDP 上限参考（静态发 / 纯PCIe收）",
    )
    ax2.bar([tx_c + wb], [d155["dp_tx64"]], width=wb * 0.88, color=C_PEEK, label="DirectPush")

    # RX bars
    ax2.bar(
        [rx_c - wb], [d172["sdp_rx64_e2e"]], width=wb * 0.88, color=C_SDP,
        hatch="xx", edgecolor="#334155", linewidth=0.7,
    )
    ax2.bar(
        [rx_c], [d172["sdp_rx64_s1"]], width=wb * 0.88, color=C_SDP_ALT,
        hatch="xx", edgecolor="#334155", linewidth=0.7,
    )
    ax2.bar([rx_c + wb], [d172["dp_rx64"]], width=wb * 0.88, color=C_PEEK)

    for xv, val, col, sub in [
        (tx_c - wb, d155["sdp_tx64_dyn"], "#334155", "动态"),
        (tx_c, d155["sdp_tx64_static"], "#475569", "静态"),
        (tx_c + wb, d155["dp_tx64"], C_PEEK, ""),
        (rx_c - wb, d172["sdp_rx64_e2e"], "#334155", "有丢包"),
        (rx_c, d172["sdp_rx64_s1"], "#475569", "有丢包"),
        (rx_c + wb, d172["dp_rx64"], C_PEEK, "卡→主机\n零丢包"),
    ]:
        lbl = f"{val:.1f}\n{sub}" if sub else f"{val:.1f}"
        ax2.text(xv, val + 2.2, lbl, ha="center", va="bottom",
                 fontsize=9.2, fontweight="bold", color=col, linespacing=1.05)

    tx_lo = d155["dp_tx64"] / d155["sdp_tx64_static"]
    tx_hi = d155["dp_tx64"] / d155["sdp_tx64_dyn"]
    rx_lo = d172["dp_rx64"] / d172["sdp_rx64_s1"]
    rx_hi = d172["dp_rx64"] / d172["sdp_rx64_e2e"]

    ax2.text(
        tx_c, d155["dp_tx64"] + 13.5,
        f"快 {tx_lo:.2f}×–{tx_hi:.2f}×",
        ha="center", va="bottom", fontsize=10.2, fontweight="bold", color=C_PEEK,
        bbox=dict(boxstyle="round,pad=0.28", fc="#eff6ff", ec=C_PEEK, lw=1.1),
    )
    ax2.text(
        rx_c, d172["dp_rx64"] + 25.0,
        f"快 {rx_lo:.2f}×–{rx_hi:.2f}×\n卡→主机零丢包",
        ha="center", va="bottom", fontsize=9.8, fontweight="bold", color=C_PEEK,
        bbox=dict(boxstyle="round,pad=0.28", fc="#eff6ff", ec=C_PEEK, lw=1.1),
    )

    ax2.set_xticks([tx_c, rx_c])
    ax2.set_xticklabels(["64 B 单核发包 TX\n(MRV-155 / 205)", "64 B 单核收包 RX\n(MRV-172 / 205)"], fontsize=10.2, fontweight="bold")
    ax2.set_ylabel("单主机核包率上限 (Mpps，越高越快)", fontsize=10.2)
    ax2.set_ylim(0, 168)
    ax2.set_xlim(-0.55, 1.70)
    ax2.set_title("B. 单核 64 B 包率上限（含调优与丢包口径）", fontsize=11.5, fontweight="bold", pad=10)
    ax2.legend(loc="upper left", fontsize=8.4, frameon=True, facecolor="white", framealpha=0.95)

    # =========================================================================
    # Panel C: Single-core 256 B & 4 KiB wire bandwidth (Gbps)
    # =========================================================================
    cats = [
        ("256 B\n发包 TX", d155["sdp_tx256_gbps"], d155["dp_tx256_gbps"]),
        ("4 KiB\n发包 TX", d155["sdp_tx4k_gbps"], d155["dp_tx4k_gbps"]),
        ("256 B\n收包 RX", d172["sdp_rx256_gbps"], d172["dp_rx256_gbps"]),
        ("4 KiB\n收包 RX", d172["sdp_rx4k_gbps"], d172["dp_rx4k_gbps"]),
    ]
    xc = np.arange(len(cats))
    wc = 0.34
    sdp_g = [c[1] for c in cats]
    dp_g = [c[2] for c in cats]

    ax3.axhline(100.0, color=C_WIRE, linestyle="--", linewidth=1.4, zorder=1, label="100GbE 满线速")
    ax3.bar(xc - wc / 2 - 0.015, sdp_g, width=wc * 0.88, color=C_SDP, zorder=2, label="调优 SDP")
    ax3.bar(xc + wc / 2 + 0.015, dp_g, width=wc * 0.88, color=C_PEEK, zorder=2, label="DirectPush")

    lbl_bg = dict(boxstyle="round,pad=0.08", fc="white", ec="none", alpha=0.82)
    for i, (_, sv, dv) in enumerate(cats):
        ax3.text(xc[i] - wc / 2 - 0.02, sv + 1.8, f"{sv:.1f}", ha="center", va="bottom",
                 fontsize=8.6, fontweight="bold", color="#334155", bbox=lbl_bg, zorder=4)
        ax3.text(xc[i] + wc / 2 + 0.02, dv + 1.8, f"{dv:.1f}", ha="center", va="bottom",
                 fontsize=8.6, fontweight="bold", color=C_PEEK, bbox=lbl_bg, zorder=4)
        diff_pct = (dv - sv) / sv * 100.0
        tag = f"+{diff_pct:.0f}%" if diff_pct >= 3.0 else "均满线"
        tag_col = C_PEEK if diff_pct >= 3.0 else "#475569"
        ax3.text(xc[i], max(sv, dv) + 10.5, tag, ha="center", va="bottom",
                 fontsize=9.6, fontweight="bold", color=tag_col, zorder=4)

    ax3.set_xticks(xc)
    ax3.set_xticklabels([c[0] for c in cats], fontsize=9.8, fontweight="bold")
    ax3.set_ylabel("单主机核实测线速带宽 (Gbps)", fontsize=10.2)
    ax3.set_ylim(0, 130)
    ax3.set_title("C. 单核中大包线速（256 B / 4 KiB 载荷）", fontsize=11.5, fontweight="bold", pad=10)
    ax3.legend(loc="lower right", fontsize=8.6, frameon=True, facecolor="white", framealpha=0.95)

    fig.suptitle(
        "图 1 · 底层接口对比：DirectPush 相比调优 SDP 的延迟与单核 100GbE 吞吐",
        fontsize=13.5, fontweight="bold", y=0.985,
    )
    fig.text(
        0.5, 0.012,
        f"数据来源：MRV-166 / MRV-195 / MRV-155 / MRV-172 原始验证日志（n=3 中位数，单主机核 cpu40 @ {d155['host_ghz']:.1f} GHz）。"
        f"对齐 MRV-205 口径：TX 并列动态验真 {d155['sdp_tx64_dyn']:.1f} 与静态复用 {d155['sdp_tx64_static']:.1f} Mpps；"
        f"RX 标明 SDP {d172['sdp_rx64_e2e']:.1f}–{d172['sdp_rx64_s1']:.1f} Mpps 为有丢包上限（DP 卡→主机段丢包为 0）。",
        ha="center", va="bottom", fontsize=8.8, color="#475569",
    )

    fig.tight_layout(rect=[0.0, 0.055, 1.0, 0.935])
    png = mrvplot.HERE / "mrv-229-raw-latency-tput.png"
    svg = mrvplot.HERE / "mrv-229-raw-latency-tput.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")
    print(f"  Same-card 64B W=1: SDP {d166['sdp_64_p50']:.3f} vs DP {d166['dp_64_p50']:.3f} us ({sp_same:.2f}x)")
    print(f"  Dual-100G 64B W=1: SDP {d195['sdp_p50']:.3f} vs peek {d195['peek_p50']:.3f} ({sp_peek:.2f}x) / safe {d195['safe_p50']:.3f} ({sp_safe:.2f}x)")
    print(f"  64B TX: SDP dyn {d155['sdp_tx64_dyn']:.2f} (S1 {d155['sdp_tx64_s1_dyn']:.2f}) / static {d155['sdp_tx64_static']:.2f} vs DP {d155['dp_tx64']:.2f} Mpps ({tx_lo:.2f}x-{tx_hi:.2f}x)")
    print(f"  64B RX: SDP e2e {d172['sdp_rx64_e2e']:.2f} / s1 {d172['sdp_rx64_s1']:.2f} (drop-capped) vs DP {d172['dp_rx64']:.2f} Mpps lossless ({rx_lo:.2f}x-{rx_hi:.2f}x)")


if __name__ == "__main__":
    main()
