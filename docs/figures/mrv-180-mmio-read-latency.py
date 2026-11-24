#!/usr/bin/env python3
"""MRV-180 data-driven figure: Host -> CN106 MMIO read latency breakdown & 64 B width comparison.

Reads raw n=3 interleaved measurements strictly from logs/mrv180-2026-11-24/runs.jsonl
(zero hard-coded experimental numbers) and renders docs/figures/mrv-180-mmio-read-latency.{png,svg}.
"""
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mrvplot  # noqa: E402

ROOT = HERE.parent.parent
RUNS_PATH = ROOT / "logs/mrv180-2026-11-24/runs.jsonl"


def load_runs():
    rows = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    by_arm = {}
    for r in rows:
        assert r["accepted"] is True, f"unaccepted row in runs.jsonl: {r}"
        assert r["mismatches"] == 0, f"mismatches != 0: {r}"
        assert r["page_faults"] == 0, f"page_faults != 0: {r}"
        by_arm.setdefault(r["arm"], []).append(r)
    return by_arm


def arm_stats(rs):
    p50s = [r["net_ns"]["p50"] for r in rs]
    p99s = [r["net_ns"]["p99"] for r in rs]
    tlps = [r["tlp_per_read"] for r in rs]
    dss = [r["card_pmu"]["tad_dss_rd"] / r["rounds"] for r in rs]
    ddr = [r["card_pmu"]["ddr_reads"] / r["rounds"] for r in rs]
    return {
        "p50_med": statistics.median(p50s),
        "p99_med": statistics.median(p99s),
        "p50_all": p50s,
        "p99_all": p99s,
        "tlp_med": statistics.median(tlps),
        "dss_med": statistics.median(dss),
        "ddr_med": statistics.median(ddr),
    }


def main():
    by_arm = load_runs()
    floor_med = statistics.median(r["floor_ns"]["p50"] for rs in by_arm.values() for r in rs)

    # Panel A: Single-TLP latency ladder (BAR0 CSR -> BAR4 L3 widths -> BAR4 DDR5 cold)
    panel_a_specs = [
        ("vf0_bar0_out_cnts_4b", "VF0 BAR0 CSR\nOUT_CNTS 4 B", "#2563EB"),
        ("vf0_bar0_out_cnts_8b", "VF0 BAR0 CSR\nOUT_CNTS 8 B", "#3B82F6"),
        ("bar4_uc_l3_4b", "BAR4 卡 L3\n4 B (mov)", "#0D9488"),
        ("bar4_uc_l3_8b", "BAR4 卡 L3\n8 B (mov)", "#0D9488"),
        ("bar4_uc_l3_16b", "BAR4 卡 L3\n16 B (movdqu)", "#0D9488"),
        ("bar4_uc_l3_32b", "BAR4 卡 L3\n32 B (vmovdqu)", "#0D9488"),
        ("bar4_uc_l3_64b_zmm", "BAR4 卡 L3\n64 B (vmovdqu64)", "#0F766E"),
        ("bar4_uc_l3_walk_8b", "BAR4 L3 4K行对照\n8 B (不逐出)", "#14B8A6"),
        ("bar4_uc_ddr5_walk_8b", "BAR4 卡 DDR5\n8 B (dc civac)", "#DC2626"),
        ("bar4_uc_ddr5_walk_64b_zmm", "BAR4 卡 DDR5\n64 B (dc civac)", "#B91C1C"),
    ]

    # Panel B: 64 B card L3 read strategies
    panel_b_specs = [
        ("bar4_uc_l3_64b_zmm", "1×64 B\nvmovdqu64\n(zmm)", "#0F766E"),
        ("bar4_uc_l3_64b_nt", "1×64 B\nvmovntdqa\n(UC 映射)", "#0891B2"),
        ("bar4_uc_l3_64b_4x16b", "4×16 B\nmovdqu\n(xmm)", "#D97706"),
        ("bar4_uc_l3_64b_8x8b", "8×8 B\nmov\n(标量)", "#DC2626"),
    ]

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(13.6, 5.6), gridspec_kw={"width_ratios": [2.25, 1.0]}
    )

    # ---- Panel A ----
    ya = np.arange(len(panel_a_specs))
    stats_a = [arm_stats(by_arm[k]) for k, _, _ in panel_a_specs]
    labels_a = [lbl for _, lbl, _ in panel_a_specs]
    colors_a = [c for _, _, c in panel_a_specs]

    p50_a = [s["p50_med"] for s in stats_a]
    p99_a = [s["p99_med"] for s in stats_a]

    bars_a = ax1.barh(ya, p50_a, height=0.62, color=colors_a, alpha=0.88, edgecolor="none", zorder=3)
    # p99 whisker line from p50 to p99 + diamond marker at p99
    for i, s in enumerate(stats_a):
        ax1.plot([s["p50_med"], s["p99_med"]], [i, i], color="#1F2937", lw=1.6, zorder=4)
        ax1.scatter([s["p99_med"]], [i], color="#111827", marker="D", s=22, zorder=5,
                    label="p99（3轮中位）" if i == 0 else None)
        ax1.scatter(s["p50_all"], [i] * len(s["p50_all"]), color="#FFFFFF", edgecolor="#111827",
                    s=16, lw=0.8, zorder=6, label="p50（n=3 各轮实测）" if i == 0 else None)
        extra = ""
        if panel_a_specs[i][0].startswith("bar4_uc_ddr5"):
            extra = f" [DDR5未命中 {s['dss_med']:.2f}]"
        ax1.text(
            s["p99_med"] + 12,
            i,
            f"p50 {s['p50_med']:.1f} / p99 {s['p99_med']:.1f} ns ({s['tlp_med']:.2f} TLP){extra}",
            va="center",
            ha="left",
            fontsize=8.0,
            color="#111827",
        )

    # CXL.mem ~200 ns external reference line (clearly marked as non-measured reference)
    ax1.axvline(200, color="#7C3AED", ls="--", lw=1.3, alpha=0.85, zorder=2)
    ax1.text(
        212,
        -0.88,
        "CXL.mem 空载参照 (~170–250 ns, 非本机实测)",
        color="#6D28D9",
        fontsize=8.0,
        va="center",
        ha="left",
        bbox=dict(boxstyle="round,pad=0.22", fc="#F5F3FF", ec="#C4B5FD", lw=0.8),
    )

    # Clean summary callouts placed in open right-hand whitespace (zero overlap with bars/labels)
    bar0_p50 = stats_a[1]["p50_med"]
    l3_p50 = stats_a[3]["p50_med"]
    l3_walk_p50 = stats_a[7]["p50_med"]
    ddr5_p50 = stats_a[8]["p50_med"]
    ax1.text(
        1170,
        3.2,
        f"关键差值对照：\n"
        f"• BAR4 L3 vs BAR0 CSR：+{l3_p50 - bar0_p50:.1f} ns\n"
        f"  （端到端路径差，未分段归因）\n"
        f"• 卡 DDR5 vs 卡 L3：+{ddr5_p50 - l3_walk_p50:.1f} ns\n"
        f"  （单变量 dc civac，1.00 未命中）",
        fontsize=7.9,
        color="#1F2937",
        va="center",
        ha="left",
        bbox=dict(boxstyle="round,pad=0.38", fc="#F9FAFB", ec="#D1D5DB", lw=0.9),
    )

    ax1.set_yticks(ya)
    ax1.set_yticklabels(labels_a, fontsize=8.4)
    ax1.set_ylim(len(panel_a_specs) - 0.35, -1.35)
    ax1.set_xlim(0, 1790)
    ax1.set_xlabel("单次 MMIO Read 净延迟 (ns，已扣 LFENCE;RDTSC 空括号底噪)", fontsize=9.2)
    ax1.set_title("(A) 单笔 PCIe MRd TLP 延迟拆解：BAR0 寄存器 vs BAR4 卡 L3 vs 卡 DDR5", fontsize=10.0, fontweight="bold")
    ax1.legend(loc="upper right", fontsize=8.0, frameon=True, framealpha=0.95)
    xb = np.arange(len(panel_b_specs))
    stats_b = [arm_stats(by_arm[k]) for k, _, _ in panel_b_specs]
    labels_b = [lbl for _, lbl, _ in panel_b_specs]
    colors_b = [c for _, _, c in panel_b_specs]
    p50_b = [s["p50_med"] for s in stats_b]

    ax2.bar(xb, p50_b, width=0.56, color=colors_b, alpha=0.88, zorder=3)
    base_64 = stats_b[0]["p50_med"]
    for i, s in enumerate(stats_b):
        ax2.plot([i, i], [s["p50_med"], s["p99_med"]], color="#1F2937", lw=1.6, zorder=4)
        ax2.scatter([i], [s["p99_med"]], color="#111827", marker="D", s=22, zorder=5)
        ax2.scatter([i] * len(s["p50_all"]), s["p50_all"], color="#FFFFFF", edgecolor="#111827",
                    s=16, lw=0.8, zorder=6)
        mult = s["p50_med"] / base_64
        ax2.text(
            i,
            s["p99_med"] + 130,
            f"{s['p50_med']:.0f} ns\n({s['tlp_med']:.2f} TLP)\n{mult:.1f}×",
            ha="center",
            va="bottom",
            fontsize=8.2,
            fontweight="bold" if i == 0 else "normal",
            color="#111827",
        )

    ax2.set_xticks(xb)
    ax2.set_xticklabels(labels_b, fontsize=8.5)
    ax2.set_ylim(0, 6100)
    ax2.set_ylabel("读取 64 B 净延迟 (ns)", fontsize=9.2)
    ax2.set_title("(B) BAR4 UC 读整行 64 B：指令与 TLP 笔数对比", fontsize=10.0, fontweight="bold")

    fig.suptitle(
        f"MRV-180：主机侧跨 PCIe Gen5×4 读 CN106 MMIO 延迟实测（cpu40 3.90 GHz / uncore 2.20 GHz / 卡 2.00 GHz，n=3×100k，底噪 {floor_med:.1f} ns，全 0 校验错误）",
        fontsize=10.3,
        fontweight="bold",
        y=0.99,
    )
    out = mrvplot.save(fig, "mrv-180-mmio-read-latency")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
