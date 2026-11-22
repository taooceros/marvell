#!/usr/bin/env python3
"""Figure 3 (MRV-127): Random 2 MiB page-offset KV access (within 64-page IOTLB) + DirectPush >=4 us CPU headroom.

Reads raw verified runs directly from logs/mrv127-2026-11-22/runs.jsonl.
Revised per MRV-138 review:
  - Panel A scopes the 0.0% degradation strictly to 128 MiB (64 x 2 MiB pages within hardware IOTLB
    hot set) and 3-line payload sampling (not multi-GiB IOTLB miss or full-4 KiB payload consumption).
  - Panel B explicitly discloses Part 4 confounders: missing same-total-credit shr64 control (32 vs 64
    total slots) and admit-before-poll_rx loop order (shr32 wire p99 6.84 us < spl32 7.92 us), and fixes
    bar label overlap.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import mrvplot

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs/mrv127-2026-11-22/runs.jsonl"


def load_data():
    cals = {}
    by_cfg = defaultdict(list)
    with RUNS.open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("part") == "part0_cal":
                cals[r["rep"]] = r
            else:
                assert r["verified"]
                assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
                by_cfg[r["cfg"]].append(r)
    return cals, by_cfg


def main():
    cals, by_cfg = load_data()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.4))

    # ---- Panel A: Sequential (0.2% page switch) vs Uniform Random (98.4%, 64/64 x 2 MiB pages) ----
    pairs = [
        ("4 KiB DMA\nW=1",  "p1_4k_w1_seq",  "p1_4k_w1_rand",  "bidir_gbps"),
        ("4 KiB DMA\nW=16", "p1_4k_w16_seq", "p1_4k_w16_rand", "bidir_gbps"),
        ("4 KiB DMA\nW=32", "p1_4k_w32_seq", "p1_4k_w32_rand", "bidir_gbps"),
    ]
    xs = np.arange(len(pairs) + 1)
    bw = 0.34

    for i, (lbl, c_seq, c_rnd, metric) in enumerate(pairs):
        ys_s = [r[metric] for r in by_cfg[c_seq]]
        ys_r = [r[metric] for r in by_cfg[c_rnd]]
        ms, mr = statistics.median(ys_s), statistics.median(ys_r)
        delta_pct = (mr - ms) / ms * 100.0
        delta_str = "0.0%" if abs(delta_pct) < 0.05 else f"{delta_pct:+.1f}%"
        ax1.bar(i - bw / 2, ms, width=bw * 0.92, color="#95a5a6", edgecolor="#333", linewidth=0.7,
                label="顺序步进（跨页 0.2%）" if i == 0 else None, zorder=3)
        ax1.bar(i + bw / 2, mr, width=bw * 0.92, color="#1b6ca8", edgecolor="#333", linewidth=0.7,
                label="随机页偏移（64/64 页, 跨页 98.4%）" if i == 0 else None, zorder=3)
        ax1.scatter([i - bw / 2 + j for j in (-0.04, 0, 0.04)], ys_s, color="#111", s=16, zorder=5)
        ax1.scatter([i + bw / 2 + j for j in (-0.04, 0, 0.04)], ys_r, color="#111", s=16, zorder=5)
        ax1.text(i, max(ys_s + ys_r) + 0.55, f"{mr:.2f} GB/s\n({delta_str})", ha="center", va="bottom",
                 fontsize=8.2, fontweight="bold", color="#1b6ca8")

    # Right-most slot: 64 B Push W=16 contrast (Mreq/s on right y-axis)
    ax1_r = ax1.twinx()
    s64 = [r["mreq_s"] for r in by_cfg["p1_64_w16_seq"]]
    r64 = [r["mreq_s"] for r in by_cfg["p1_64_w16_rand"]]
    m_s64, m_r64 = statistics.median(s64), statistics.median(r64)
    llc_s64 = statistics.median(r["host_pmu"]["llc_load_misses"] / r["measured"] for r in by_cfg["p1_64_w16_seq"])
    llc_r64 = statistics.median(r["host_pmu"]["llc_load_misses"] / r["measured"] for r in by_cfg["p1_64_w16_rand"])
    d64 = (m_r64 - m_s64) / m_s64 * 100.0
    i64 = 3
    ax1_r.bar(i64 - bw / 2, m_s64, width=bw * 0.92, color="#bdc3c7", hatch="//", edgecolor="#333", linewidth=0.7, zorder=3)
    ax1_r.bar(i64 + bw / 2, m_r64, width=bw * 0.92, color="#5fa2ce", hatch="//", edgecolor="#333", linewidth=0.7, zorder=3)
    ax1_r.scatter([i64 - bw / 2 + j for j in (-0.04, 0, 0.04)], s64, color="#111", s=16, zorder=5)
    ax1_r.scatter([i64 + bw / 2 + j for j in (-0.04, 0, 0.04)], r64, color="#111", s=16, zorder=5)
    ax1_r.text(i64, max(s64 + r64) + 0.35, f"{m_s64:.2f}→{m_r64:.2f} M/s\n({d64:+.1f}%, LLCm {llc_s64:.2f}→{llc_r64:.2f})",
               ha="center", va="bottom", fontsize=7.8, fontweight="bold", color="#333")
    ax1_r.set_ylabel("64 B OP_PUSH 吞吐 (Mreq/s，斜线柱)", color="#333")
    ax1_r.set_ylim(0, 14.8)
    ax1_r.grid(False)
    ax1_r.spines["top"].set_visible(False)

    llc_4k_loads = statistics.median(r["host_pmu"]["llc_loads"] / r["measured"] for r in by_cfg["p1_4k_w16_rand"])
    ax1.axvline(2.5, color="#888", linestyle=":", linewidth=1.1)
    ax1.text(
        0.015, 0.72,
        "4 KiB DMA（128 MiB）:\n"
        "• 64 页（IOTLB 内）\n"
        f"• LLCm 0.00（3行 {llc_4k_loads:.2f}读）\n"
        "• DDIO 100% · 退化 0%\n"
        "• 多 GiB 待后续子单",
        transform=ax1.transAxes, fontsize=7.2, va="top",
        bbox=dict(boxstyle="round,pad=0.30", facecolor="white", edgecolor="#1b6ca8", alpha=0.95),
    )

    ax1.set_xticks(xs)
    ax1.set_xticklabels([p[0] for p in pairs] + ["64 B Push\nW=16（对照）"], fontsize=8.8)
    ax1.set_ylim(0, 26.8)
    ax1.set_ylabel("4 KiB DPI DMA 双向带宽 (GB/s)")
    ax1.set_title("A · 128 MiB 池（64 页 IOTLB 内）：顺序 vs 随机 2 MiB 偏移", fontsize=10.6, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=8.0, frameon=True)

    # ---- Panel B: DirectPush-only CPU headroom with batch=16 (~5.31 us >= 4 us) compute quantum ----
    workloads_b = [
        ("4 KiB @ 1.0 M/s\n(8.2 GB/s 随机 KV)", None, "p4_aw16_0m_1p0m_spl32", "p4_aw0_0m_1p0m_spl32"),
        ("4 M 64 B + 1.0 M 4K\n(8.7 GB/s 混流)", "p4_aw16_4m_1p0m_shr32", "p4_aw16_4m_1p0m_spl32", "p4_aw0_4m_1p0m_spl32"),
        ("4 M 64 B + 1.5 M 4K\n(12.8 GB/s 混流)", "p4_aw16_4m_1p5m_shr32", "p4_aw16_4m_1p5m_spl32", "p4_aw0_4m_1p5m_spl32"),
    ]
    xb = np.arange(len(workloads_b))
    bw2 = 0.35

    def net_cpu_vals(cfg):
        return [(1.0 - r["app_work_per_s"] / cals[r["rep"]]["units_per_s"]) * 100.0 for r in by_cfg[cfg]]

    for i, (wlbl, c_shr, c_spl, c_aw0) in enumerate(workloads_b):
        spl_vals = net_cpu_vals(c_spl)
        m_spl = statistics.median(spl_vals)
        spl_p99 = statistics.median(
            (r["lat_small_tot_us"] or r["lat_large_tot_us"])["p99"] for r in by_cfg[c_spl]
        )
        aw0_p99 = statistics.median(
            (r["lat_small_tot_us"] or r["lat_large_tot_us"])["p99"] for r in by_cfg[c_aw0]
        )
        if c_shr is not None:
            shr_vals = net_cpu_vals(c_shr)
            m_shr = statistics.median(shr_vals)
            shr_p99 = statistics.median(r["lat_small_tot_us"]["p99"] for r in by_cfg[c_shr])
            x_shr = i - bw2 / 2 - 0.02
            x_spl = i + bw2 / 2 + 0.02
            ax2.bar(x_shr, m_shr, width=bw2 * 0.88, color="#95a5a6", edgecolor="#333", linewidth=0.7,
                    label="共享 W=32（总 32 槽，无 shr64 对照）" if i == 1 else None, zorder=3)
            ax2.scatter([x_shr + j for j in (-0.04, 0, 0.04)], shr_vals, color="#111", s=16, zorder=5)
            ax2.text(x_shr, max(shr_vals) + 1.5, f"{m_shr:.1f}%\ntot {shr_p99:.1f}µs", ha="center", va="bottom", fontsize=7.6, color="#444")
            ax2.bar(x_spl, m_spl, width=bw2 * 0.88, color="#1b6ca8", edgecolor="#333", linewidth=0.7, zorder=3)
            ax2.scatter([x_spl + j for j in (-0.04, 0, 0.04)], spl_vals, color="#111", s=16, zorder=5)
            ax2.text(x_spl + 0.02, max(spl_vals) + 1.5, f"{m_spl:.1f}%\ntot {spl_p99:.1f}µs\n(aw0 {aw0_p99:.1f}µs)",
                     ha="center", va="bottom", fontsize=7.5, fontweight="bold", color="#1b6ca8")
        else:
            x_spl = i
            ax2.bar(x_spl, m_spl, width=bw2 * 0.88, color="#1b6ca8", edgecolor="#333", linewidth=0.7,
                    label="拆分 (32, 32)（总 64 槽，b16=5.31 µs）", zorder=3)
            ax2.scatter([x_spl + j for j in (-0.04, 0, 0.04)], spl_vals, color="#111", s=16, zorder=5)
            ax2.text(x_spl, max(spl_vals) + 1.5, f"{m_spl:.1f}%\ntot {spl_p99:.1f}µs\n(aw0 {aw0_p99:.1f}µs)",
                     ha="center", va="bottom", fontsize=7.6, fontweight="bold", color="#1b6ca8")

    shr_w99 = statistics.median(r["lat_small_wire_us"]["p99"] for r in by_cfg["p4_aw16_4m_1p5m_shr32"])
    spl_w99 = statistics.median(r["lat_small_wire_us"]["p99"] for r in by_cfg["p4_aw16_4m_1p5m_spl32"])
    ax2.text(
        0.015, 0.78,
        "Part 4 混淆说明：\n"
        "• 缺 shr64 等总信用对照\n"
        "  （32 vs 64 总槽位）\n"
        "• 先准入后 poll_rx()\n"
        "  放大 32 槽满窗忙等\n"
        f"• wire p99: shr32 {shr_w99:.2f}\n"
        f"  < spl32 {spl_w99:.2f} µs",
        transform=ax2.transAxes, fontsize=7.2, va="top", color="#555",
        bbox=dict(boxstyle="round,pad=0.30", facecolor="#fffdf5", edgecolor="#d4b46a", alpha=0.96),
    )

    ax2.set_xticks(xb)
    ax2.set_xticklabels([w[0] for w in workloads_b], fontsize=8.8)
    ax2.set_xlim(-0.58, 2.55)
    ax2.set_ylim(0, 76)
    ax2.set_ylabel("主机核网络占用 net_cpu%（越低 CPU 余量越大）")
    ax2.set_title("B · DirectPush 自身 ≥4 µs 量子核占用（零 SDP，含混淆披露）", fontsize=10.6, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=7.8, frameon=True)

    fig.suptitle(
        "MRV-127 128 MiB 随机页偏移验证与 DirectPush ≥4 µs 计算量子核余量（1H + 1C + 2 DPI VFs，n=3）",
        fontsize=11.3, fontweight="bold", y=0.995,
    )
    out = mrvplot.save(fig, "mrv-127-rand-pool-cpu")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
