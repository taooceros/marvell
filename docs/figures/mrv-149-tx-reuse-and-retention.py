#!/usr/bin/env python3
"""MRV-149 summary chart plotted directly from verified raw runs:
- Panel (a): Immediate stack buffer reuse (stack_0) vs host-retained sequential pool (64 KiB -> 128 MiB)
  from logs/mrv116-2026-11-20/runs.jsonl (64 B, 2 MiB hugepages, cpu40 3.90 GHz / uncore 2.20 GHz / CN106 2.00 GHz).
- Panel (b): Random/large-stride retention pool attribution (N=65,536 & true 128 MiB overflow)
  from logs/mrv125-2026-11-22/runs.jsonl (p1 shaped W=128 vs p2c unshaped batch-ts W=64 vs p2a unshaped pkt-ts W=64),
  plus aggregate RX DDIO hit rate across all 297 verified closed-loop runs (MRV-116 + MRV-125).
"""
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean, median

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
LOG_116 = ROOT / "logs/mrv116-2026-11-20/runs.jsonl"
LOG_125 = ROOT / "logs/mrv125-2026-11-22/runs.jsonl"


def load_verified(path: Path):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in rows:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0, f"error run: {r['run_id']}"
        assert r.get("card_pmu", {}).get("corrupt", 0) == 0, f"corrupt run: {r['run_id']}"
        pf = r.get("host_pmu", {}).get("page_faults", 0)
        assert pf == 0, f"page faults {pf} in {r['run_id']}"
    return rows


def main():
    r116 = load_verified(LOG_116)
    r125 = load_verified(LOG_125)

    # Aggregate verified RX DDIO hit rate across all 297 closed-loop runs (153 + 144)
    cl_116 = [r for r in r116 if r["part"] != "part3_h2c"]
    cl_125 = [r for r in r125 if r["part"] != "p4a_h2c_ceiling"]
    assert len(cl_116) == 153 and len(cl_125) == 144
    tot_hit = sum(r["host_pmu"]["cha_io_hit_itom"] for r in cl_116 + cl_125)
    tot_miss = sum(r["host_pmu"]["cha_io_miss_itom"] for r in cl_116 + cl_125)
    tot_itom = tot_hit + tot_miss
    ddio_pct = 100.0 * tot_hit / tot_itom

    # Group MRV-116
    g116 = defaultdict(list)
    for r in r116:
        g116[(r["part"], r["cfg"])].append(r)

    # Group MRV-125
    g125 = defaultdict(list)
    for r in r125:
        g125[(r["part"], r["cfg"])].append(r)

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(13.4, 5.4), gridspec_kw={"width_ratios": [1.0, 1.18]}
    )

    # ---------------- Panel (a): stack_0 immediate reuse vs sequential retention ----------------
    x_labels_a = ["原地复用\n(64 B 栈)", "64 KiB\n(L2 内)", "4 MiB\n(LLC 内)", "32 MiB\n(半 LLC)", "128 MiB\n(>60M LLC)"]
    xs = np.arange(len(x_labels_a))

    h2c_fill_cfgs = ["stack_0", "fill_seq_64k", "fill_seq_4m", "fill_seq_32m", "fill_seq_128m"]
    h2c_cold_cfgs = ["stack_0", "cold_seq_64k", "cold_seq_4m", "cold_seq_32m", "cold_seq_128m"]
    w64_fill_cfgs = ["stack_0", "fill_seq_64k", "fill_seq_4m", "fill_seq_32m", "fill_seq_128m"]

    def series_vals(part, cfgs):
        means = [mean(x["mops"] for x in g116[(part, c)]) for c in cfgs]
        pts = [[x["mops"] for x in g116[(part, c)]] for c in cfgs]
        return means, pts

    y_h2c_fill, pts_h2c_fill = series_vals("part3_h2c", h2c_fill_cfgs)
    y_h2c_cold, pts_h2c_cold = series_vals("part3_h2c", h2c_cold_cfgs)
    y_w64_fill, pts_w64_fill = series_vals("part1_w64", w64_fill_cfgs)

    # Line rate reference (PCIe Gen5 x4 64B MWr = 179.02 M/s)
    ax1.axhline(179.02, color="#94a3b8", linestyle=":", linewidth=1.2, zorder=1)
    ax1.text(0.05, 182.5, "PCIe Gen5 ×4 64 B 理论线速 (179.0 M/s)", fontsize=8.8, color="#64748b")

    # LLC boundary
    ax1.axvline(3.5, color="#dc2626", linestyle="--", linewidth=1.1, alpha=0.65, zorder=1)
    ax1.text(3.45, 96, "60 MiB\nLLC 边界", ha="right", va="center", fontsize=8.8, color="#b91c1c")

    # Plot curves + individual rep points
    c_teal = "#0f766e"
    c_slate = "#475569"
    c_blue = "#1d4ed8"

    ax1.plot(xs, y_h2c_cold, color=c_slate, linestyle="--", marker="s", markersize=5.5, linewidth=1.8,
             label="单向直推 H2C（只读冷源对照）", zorder=3)
    ax1.plot(xs, y_h2c_fill, color=c_teal, linestyle="-", marker="o", markersize=6.5, linewidth=2.4,
             label="单向直推 H2C（原地复用 / 写后留存）", zorder=4)
    ax1.plot(xs, y_w64_fill, color=c_blue, linestyle="-", marker="^", markersize=6.2, linewidth=2.2,
             label="闭环往返 W=64（单卡核封顶）", zorder=4)

    for i in range(len(xs)):
        ax1.scatter([xs[i]] * len(pts_h2c_fill[i]), pts_h2c_fill[i], color=c_teal, s=16, alpha=0.5, zorder=5)
        ax1.scatter([xs[i]] * len(pts_w64_fill[i]), pts_w64_fill[i], color=c_blue, s=16, alpha=0.5, zorder=5)

    # Highlight stack_0 immediate reuse
    ax1.annotate(
        f"原地复用 {y_h2c_fill[0]:.1f} M/s\n(97.7% 线速 · 0 池分配)",
        xy=(0, y_h2c_fill[0]), xytext=(0.45, 134),
        fontsize=8.8, fontweight="bold", color=c_teal,
        bbox=dict(boxstyle="round,pad=0.3", fc="#f0fdfa", ec=c_teal, lw=1.0),
        arrowprops=dict(arrowstyle="->", color=c_teal, lw=1.2),
    )

    # Annotate 128 MiB H2C fill drop (local DDR5 ceiling, relative to 64 KiB)
    drop_h2c = 100.0 * (y_h2c_fill[-1] - y_h2c_fill[1]) / y_h2c_fill[1]
    ax1.annotate(
        f"{y_h2c_fill[-1]:.1f} M/s（相对 64K {drop_h2c:.1f}%）\n单核本地 DDR5 上限",
        xy=(4, y_h2c_fill[-1]), xytext=(1.95, 108),
        fontsize=8.5, color="#0f172a",
        bbox=dict(boxstyle="round,pad=0.28", fc="#f8fafc", ec="#94a3b8", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#475569", lw=1.1),
    )

    # Annotate W=64 closed-loop stability (relative to 64 KiB)
    drop_w64 = 100.0 * (y_w64_fill[-1] - y_w64_fill[1]) / y_w64_fill[1]
    ax1.annotate(
        f"64K→128M: {y_w64_fill[1]:.1f}→{y_w64_fill[-1]:.1f} M/s ({drop_w64:.2f}%)\n原地复用 {y_w64_fill[0]:.1f} M/s · 0 次 DDIO miss",
        xy=(4, y_w64_fill[-1]), xytext=(1.15, 62),
        fontsize=8.4, color=c_blue,
        bbox=dict(boxstyle="round,pad=0.28", fc="#eff6ff", ec="#93c5fd", lw=0.9),
        arrowprops=dict(arrowstyle="->", color=c_blue, lw=1.1),
    )

    ax1.set_xticks(xs)
    ax1.set_xticklabels(x_labels_a, fontsize=9)
    ax1.set_ylim(0, 198)
    ax1.set_ylabel("吞吐 (Mmsg/s，64 B 消息)", fontsize=10)
    ax1.set_title("(a) 原地复用 vs 顺序留存池（MRV-116，n=3）", fontsize=11, fontweight="bold", pad=10)
    ax1.legend(loc="lower left", fontsize=8.3, frameon=True, framealpha=0.95)

    # ---------------- Panel (b): MRV-125 random & 2 KiB stride attribution ----------------
    cfgs_b = [
        ("64B_seq_4m", "64 B 顺序\n(4M 活跃)"),
        ("64B_rand_4m", "64 B 乱序\n(4M 活跃)"),
        ("2KiB_rand_128m", "2048 B 乱序\n(2^11 组冲突)"),
        ("2112B_rand_132m", "2112 B 乱序\n(奇数行·4M)"),
        ("64B_rand_128m", "64 B 乱序\n(真 128M 溢出)"),
    ]
    xb = np.arange(len(cfgs_b))
    w = 0.26

    p1_meds, p1_pts = [], []
    p2c_meds, p2c_pts = [], []
    p2a_meds, p2a_pts = [], []
    for c, _ in cfgs_b:
        p1_meds.append(median(x["mops"] for x in g125[("p1_wc1_shaped_bts", c)]))
        p1_pts.append([x["mops"] for x in g125[("p1_wc1_shaped_bts", c)]])
        p2c_meds.append(median(x["mops"] for x in g125[("p2c_wc1_unsh_bts", c)]))
        p2c_pts.append([x["mops"] for x in g125[("p2c_wc1_unsh_bts", c)]])
        p2a_meds.append(median(x["mops"] for x in g125[("p2a_wc1_unsh_pkt", c)]))
        p2a_pts.append([x["mops"] for x in g125[("p2a_wc1_unsh_pkt", c)]])

    b1 = ax2.bar(xb - w, p1_meds, width=w, color="#0f766e", label="整形快态 p1（W=128, 卡退避 1 µs, 批戳）", zorder=3)
    b2 = ax2.bar(xb, p2c_meds, width=w, color="#475569", label="无整形批戳 p2c（W=64, 零退避, 批戳）", zorder=3)
    b3 = ax2.bar(xb + w, p2a_meds, width=w, color="#cbd5e1", edgecolor="#64748b", linewidth=0.8,
                 label="原无整形基线 p2a（W=64, 逐包 RDTSC）", zorder=3)

    for i in range(len(cfgs_b)):
        ax2.scatter([xb[i] - w] * len(p1_pts[i]), p1_pts[i], color="#042f2e", s=11, zorder=5)
        ax2.scatter([xb[i]] * len(p2c_pts[i]), p2c_pts[i], color="#0f172a", s=11, zorder=5)
        ax2.scatter([xb[i] + w] * len(p2a_pts[i]), p2a_pts[i], color="#334155", s=11, zorder=5)
        ax2.text(xb[i] - w, p1_meds[i] + 1.0, f"{p1_meds[i]:.1f}", ha="center", va="bottom",
                 fontsize=8.3, fontweight="bold", color="#0f766e")
        ax2.text(xb[i], p2c_meds[i] + 1.0, f"{p2c_meds[i]:.1f}", ha="center", va="bottom",
                 fontsize=7.8, color="#334155")
        ax2.text(xb[i] + w, p2a_meds[i] + 1.0, f"{p2a_meds[i]:.1f}", ha="center", va="bottom",
                 fontsize=7.8, color="#64748b")

    # Callout badge: zero NIC coherence tax across all 297 closed-loop runs
    badge = (
        f"跨 MRV-116 + MRV-125 全 297 轮闭环\n"
        f"总计 {tot_itom/1e8:.2f} 亿次主机 RX 回写\n"
        f"DDIO 命中率 {ddio_pct:.5f}% · 零 NIC Snoop"
    )
    ax2.text(
        0.985, 0.975, badge, transform=ax2.transAxes, ha="right", va="top",
        fontsize=8.3, fontweight="bold", color="#0f766e",
        bbox=dict(boxstyle="round,pad=0.35", fc="#f0fdfa", ec="#0f766e", lw=1.1),
    )

    # Compact causal callouts in open space
    ax2.annotate(
        "错开 1 行\n消除组冲突",
        xy=(3 - w - 0.04, p1_meds[3] - 1.5), xytext=(2.05, 42.5),
        fontsize=8.2, color="#0f766e", ha="center",
        arrowprops=dict(arrowstyle="->", color="#0f766e", lw=1.1),
    )
    ax2.annotate(
        "真超 LLC：\n本地 DDR5 税",
        xy=(4 - w, p1_meds[4] + 3.2), xytext=(4.0, 36.5),
        fontsize=8.2, color="#334155", ha="center",
        arrowprops=dict(arrowstyle="->", color="#475569", lw=1.1),
    )

    ax2.set_xticks(xb)
    ax2.set_xticklabels([lbl for _, lbl in cfgs_b], fontsize=8.8)
    ax2.set_ylim(0, 72)
    ax2.set_ylabel("闭环吞吐 (Mmsg/s，64 B 消息)", fontsize=10)
    ax2.set_title("(b) 乱序大池掉速拆因：均为主机本地/桥拥塞，非一致性税（MRV-125，n=3）",
                  fontsize=10.8, fontweight="bold", pad=10)
    ax2.legend(loc="upper left", bbox_to_anchor=(0.0, 0.99), fontsize=7.9, frameon=True, framealpha=0.94)

    fig.suptitle(
        "MRV-149 实测总览：MOVDIR64B → BAR4 + 卡 WC → DDIO（64 B、batch=1、2 MiB 大页、3.90/2.20/2.00 GHz、全量 0 错）",
        fontsize=11.5, fontweight="bold", y=0.995,
    )
    out = save(fig, "mrv-149-tx-reuse-and-retention")
    print(f"Saved {out}; DDIO={ddio_pct:.6f}% ({tot_miss}/{tot_itom})")


if __name__ == "__main__":
    main()
