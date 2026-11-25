#!/usr/bin/env python3
"""MRV-165 Figure 2: Multi-core card parallel refill (1 -> 2 -> 4 card cores) vs Host 128 MiB ring.

Reads verified Control 2 runs from logs/mrv165-2026-11-25/runs.jsonl.
Plots host wall-clock burst drain throughput (drain_wall_mops) for all series and marks the
steady-state PCIe Gen5 x4 64-byte write wire limit (179.0 M/s).
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

import mrvplot  # noqa: F401 - registers CJK font & white style

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv165-2026-11-25/runs.jsonl"
BURSTS = ["1MiB", "4MiB", "8MiB", "16MiB", "32MiB", "48MiB", "64MiB"]
BURST_XLABELS = ["1 MiB", "4 MiB", "8 MiB", "16 MiB", "32 MiB", "48 MiB", "64 MiB"]
PCIE_64B_CEILING = 128e9 * (128.0 / 130.0) / 8.0 / 88.0 / 1e6  # 179.02 M/s


def main():
    by_tag = defaultdict(list)
    for line in RUNS_PATH.read_text().splitlines():
        r = json.loads(line)
        if r["part"].startswith("control2"):
            assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
            by_tag[r["tag"]].append(r)

    x = np.arange(len(BURSTS), dtype=float)
    fig, ax = plt.subplots(figsize=(10.4, 5.5))

    # Subtle crossover band 32-48 MiB
    ax.axvspan(3.85, 5.15, color="#FEF3C7", alpha=0.45, zorder=0)
    ax.text(
        4.5, 302, "交叉点：32–48 MiB",
        ha="center", va="top", fontsize=9.8, color="#92400E",
    )

    # Physical wire ceiling line
    ax.axhline(PCIE_64B_CEILING, color="#1E293B", linestyle="--", linewidth=1.5, zorder=2)
    ax.text(
        6.35, PCIE_64B_CEILING + 8.0,
        f"PCIe Gen5×4 稳态上限 {PCIE_64B_CEILING:.1f} M/s（64 B/包）",
        ha="right", va="bottom", fontsize=9.3, color="#1E293B",
        bbox=dict(boxstyle="round,pad=0.20", fc="white", ec="none", alpha=0.85),
    )

    series = [
        ("scheme_a_128m", "主机 128 MiB 大环（方案 A）", "#C82829", "o", "-", 2.7, 0.0, 5),
        ("active_1c", "主动网卡 1 核补推", "#0F766E", "D", "-", 2.4, -0.07, 4),
        ("active_2c", "主动网卡 2 核并行补推", "#2563EB", "s", "--", 2.1, 0.0, 4),
        ("active_4c", "主动网卡 4 核并行补推", "#7C3AED", "^", ":", 2.1, +0.07, 4),
    ]

    for arm_key, label, color, marker, ls, lw, dx, z in series:
        meds = []
        for i, b in enumerate(BURSTS):
            rs = by_tag[f"{arm_key}_{b}"]
            vals = [r["drain_wall_mops"] for r in rs]
            meds.append(statistics.median(vals))
            ax.scatter(
                np.full(len(vals), i + dx),
                vals,
                color=color,
                marker=marker,
                s=22,
                alpha=0.28,
                edgecolors="none",
                zorder=z - 1,
            )
        ax.plot(
            x + dx,
            meds,
            color=color,
            marker=marker,
            linestyle=ls,
            linewidth=lw,
            markersize=6.8,
            markeredgecolor="white",
            markeredgewidth=0.7,
            label=label,
            zorder=z,
        )

    # Minimal clean callouts (only 2 crisp takeaways, no clutter)
    ax.annotate(
        "1 / 2 / 4 核完全重合：加核不变快\n8–64 MiB 卡净补推 177.4–177.6 M/s（99.1% 线速）",
        xy=(2.0, 181), xytext=(0.15, 146),
        fontsize=9.3, color="#0F766E",
        arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.3),
        bbox=dict(boxstyle="round,pad=0.32", fc="#F0FDFA", ec="#5EEAD4", alpha=0.96),
    )
    ax.annotate(
        "64 MiB 跌至 158.1 M/s\n（主动网卡反超 +12.6%）",
        xy=(5.96, 158.1), xytext=(4.05, 145),
        fontsize=9.2, color="#B91C1C",
        arrowprops=dict(arrowstyle="->", color="#C82829", lw=1.2),
        bbox=dict(boxstyle="round,pad=0.28", fc="white", ec="#FCA5A5", alpha=0.96),
    )

    ax.set_xlabel("单次突发积压大小（关闭并发 KV，每轮共 512 MiB）", fontsize=10.5, labelpad=7)
    ax.set_ylabel("突发排空吞吐（主机墙钟，百万条/秒）", fontsize=10.5)
    ax.set_xticks(x)
    ax.set_xticklabels(BURST_XLABELS, fontsize=10.2)
    ax.set_xlim(-0.42, 6.45)
    ax.set_ylim(135, 314)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(25))

    fig.suptitle(
        "多卡核并行补推（1 → 2 → 4 核）完全重合：单核已打满 PCIe Gen5×4 线速上限",
        fontsize=13.0, y=0.985,
    )
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(
        handles, labels,
        loc="upper center", bbox_to_anchor=(0.5, 0.928),
        ncol=4, frameon=False, fontsize=9.5, handlelength=2.2, columnspacing=1.3,
    )

    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.875))
    png = Path(__file__).resolve().parent / "mrv-165-multicore-refill.png"
    svg = Path(__file__).resolve().parent / "mrv-165-multicore-refill.svg"
    fig.savefig(png, dpi=200)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
