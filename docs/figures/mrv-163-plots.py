#!/usr/bin/env python3
"""Plot MRV-163 single-variable NIX0 TM tree comparison (ROC_NIX_TM_DEFAULT vs ROC_NIX_TM_RLIMIT)
directly from raw `logs/mrv163-2026-11-24/runs.jsonl`.

Run:
    uv run --with matplotlib python docs/figures/mrv-163-plots.py
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs" / "mrv163-2026-11-24" / "runs.jsonl"


def load_runs():
    rows = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    assert len(rows) == 48, f"expected 48 runs, got {len(rows)}"
    assert all(r["verified"] for r in rows), "found unverified runs"
    by_cfg = defaultdict(list)
    for r in rows:
        by_cfg[r["cfg"]].append(r)
    return rows, by_cfg


def stats_phy(rs):
    vals = [r["mercury_phy"]["rx_mpps"] for r in rs]
    med = statistics.median(vals)
    return med, med - min(vals), max(vals) - med


def main():
    rows, by_cfg = load_runs()
    sclk_mhz = rows[0]["card"]["sclk_mhz"]
    rclk_mhz = rows[0]["card"]["rclk_mhz"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.0, 5.4), gridspec_kw={"width_ratios": [1.12, 1.05]})

    # ------------------------------------------------------------------
    # Panel A: Primary single-variable comparison across C = 1, 2, 4, 6, 8
    # ------------------------------------------------------------------
    cs = [1, 2, 4, 6, 8]
    def_med, def_lo, def_hi = [], [], []
    rlm_med, rlm_lo, rlm_hi = [], [], []
    for c in cs:
        m, lo, hi = stats_phy(by_cfg[f"s0_64B_percore_default_c{c}"])
        def_med.append(m)
        def_lo.append(lo)
        def_hi.append(hi)
        m, lo, hi = stats_phy(by_cfg[f"s0_64B_percore_rlimit_c{c}"])
        rlm_med.append(m)
        rlm_lo.append(lo)
        rlm_hi.append(hi)

    c_grey = "#64748b"
    c_blue = "#2563eb"
    c_dark = "#1e293b"

    # Reference ceilings
    ax1.axhline(148.81, color="#94a3b8", linestyle=":", linewidth=1.3, zorder=1)
    ax1.text(1.08, 145.5, "100GbE 64 B 理论线速  148.81 Mpps", fontsize=8.8, color="#64748b", va="top")

    ax1.axhline(100.0, color="#475569", linestyle="--", linewidth=1.2, zorder=1)
    ax1.annotate(
        f"共用 TL4[0]→TL1[0] 单口实测上限 100.0 Mpps\n"
        f"实读 sclk = {sclk_mhz} MHz → 10.0 拍/包（所在级 [推算]）",
        xy=(2.85, 100.4),
        xytext=(0.95, 121.0),
        fontsize=8.3,
        color="#334155",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#f8fafc", edgecolor="#cbd5e1", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#64748b", lw=1.1, connectionstyle="arc3,rad=0.12"),
        va="center",
    )

    ax1.errorbar(
        cs,
        def_med,
        yerr=[def_lo, def_hi],
        fmt="o--",
        color=c_grey,
        linewidth=2.0,
        markersize=7,
        capsize=3.5,
        label="默认单 SMQ[0]（ROC_NIX_TM_DEFAULT）",
        zorder=3,
    )
    ax1.errorbar(
        cs,
        rlm_med,
        yerr=[rlm_lo, rlm_hi],
        fmt="s-",
        color=c_blue,
        linewidth=2.4,
        markersize=7.2,
        capsize=3.5,
        label="每队列独立 SMQ（ROC_NIX_TM_RLIMIT）",
        zorder=4,
    )

    # Shaded recovery gap at C = 6, 8
    ax1.fill_between(cs[2:], def_med[2:], rlm_med[2:], color=c_blue, alpha=0.09, zorder=2)

    # Point value labels
    for i, c in enumerate(cs):
        if c in (1, 2):
            ax1.text(c + 0.14, def_med[i] - 3.0, f"{def_med[i]:.1f}", fontsize=8.8, color=c_grey)
        elif c == 4:
            ax1.text(c, rlm_med[i] + 3.2, f"{rlm_med[i]:.2f}", fontsize=9.2, fontweight="bold", color=c_blue, ha="center")
            ax1.text(c, def_med[i] - 6.8, f"{def_med[i]:.2f}", fontsize=8.8, color=c_grey, ha="center")
        else:
            ax1.text(c, rlm_med[i] + 3.2, f"{rlm_med[i]:.2f}", fontsize=9.2, fontweight="bold", color=c_blue, ha="center")
            ax1.text(c, def_med[i] - 6.8, f"{def_med[i]:.2f}", fontsize=9.0, fontweight="bold", color=c_grey, ha="center")

    # Recovery arrow at C = 8
    gain_c8 = (rlm_med[-1] / def_med[-1] - 1.0) * 100.0
    ax1.annotate(
        "",
        xy=(8.0, rlm_med[-1] - 1.8),
        xytext=(8.0, def_med[-1] + 1.8),
        arrowprops=dict(arrowstyle="->", color=c_blue, lw=1.6),
    )
    ax1.text(
        7.82,
        0.5 * (rlm_med[-1] + def_med[-1]),
        f"+{gain_c8:.1f}%\n消除回落",
        fontsize=8.8,
        fontweight="bold",
        color=c_blue,
        ha="right",
        va="center",
    )

    ax1.set_title("A. 单变量核数扩展（64 B L2，每核独立池 + FAST_FREE）", fontsize=10.8, fontweight="bold", pad=10)
    ax1.set_xlabel("CN106 发送核数 / TX 队列数 C", fontsize=10)
    ax1.set_ylabel("对端 mercury PHY 实测包率（Mpps）", fontsize=10)
    ax1.set_xticks(cs)
    ax1.set_xlim(0.6, 8.6)
    ax1.set_ylim(20, 156)
    ax1.legend(loc="lower right", frameon=True, facecolor="white", framealpha=0.92, fontsize=8.8)

    # ------------------------------------------------------------------
    # Panel B: Controls at C = 4 & C = 8 (per-core, zero-shaper rlimit-q0, reuse)
    # ------------------------------------------------------------------
    groups = [
        ("C=4\n每核池", [
            ("默认\n单SMQ", "s0_64B_percore_default_c4", c_grey, "//"),
            ("独立SMQ\nRLIMIT", "s0_64B_percore_rlimit_c4", c_blue, ""),
            ("独立SMQ\n仅q0设100G", "s0_64B_percore_rlimitq0_c4", "#60a5fa", ""),
        ]),
        ("C=4\n旁路NPA", [
            ("默认\n单SMQ", "s0_64B_reuse_default_c4", c_grey, "//"),
            ("独立SMQ\nRLIMIT", "s0_64B_reuse_rlimit_c4", c_blue, ""),
        ]),
        ("C=8\n每核池", [
            ("默认\n单SMQ", "s0_64B_percore_default_c8", c_grey, "//"),
            ("独立SMQ\nRLIMIT", "s0_64B_percore_rlimit_c8", c_blue, ""),
            ("独立SMQ\n仅q0设100G", "s0_64B_percore_rlimitq0_c8", "#60a5fa", ""),
        ]),
        ("C=8\n旁路NPA", [
            ("默认\n单SMQ", "s0_64B_reuse_default_c8", c_grey, "//"),
            ("独立SMQ\nRLIMIT", "s0_64B_reuse_rlimit_c8", c_blue, ""),
        ]),
    ]

    x_pos = []
    group_centers = []
    group_labels = []
    cur_x = 0.0
    width = 0.78

    ax2.axhline(100.0, color="#475569", linestyle="--", linewidth=1.2, zorder=1)
    legend_seen = {}

    for g_label, items in groups:
        xs = [cur_x + i * (width + 0.14) for i in range(len(items))]
        group_centers.append(np.mean(xs))
        group_labels.append(g_label)
        for x, (lbl, cfg, col, hatch) in zip(xs, items):
            m, lo, hi = stats_phy(by_cfg[cfg])
            bar_key = lbl.replace("\n", " ")
            bar_lbl = bar_key if bar_key not in legend_seen else None
            legend_seen[bar_key] = True
            ax2.bar(
                x,
                m,
                width=width,
                color=col,
                edgecolor="#334155" if col == c_grey else "#1d4ed8",
                linewidth=0.8,
                hatch=hatch,
                alpha=0.88 if col != c_grey else 0.72,
                label=bar_lbl,
                zorder=3,
            )
            ax2.errorbar(x, m, yerr=[[lo], [hi]], fmt="none", ecolor=c_dark, capsize=3, lw=1.1, zorder=4)
            ax2.text(
                x,
                m + 1.4,
                f"{m:.2f}",
                ha="center",
                va="bottom",
                fontsize=7.9,
                fontweight="bold" if col != c_grey else "normal",
                color=c_dark if col != c_grey else "#475569",
            )
        cur_x = xs[-1] + 1.42

    ax2.set_title("B. 排除限速器与 NPA 干扰控制对照（C = 4 vs C = 8）", fontsize=10.8, fontweight="bold", pad=10)
    ax2.set_xticks(group_centers)
    ax2.set_xticklabels(group_labels, fontsize=9.2)
    ax2.set_ylabel("对端 mercury PHY 实测包率（Mpps）", fontsize=10)
    ax2.set_ylim(65, 114)
    ax2.legend(loc="lower center", ncol=3, frameon=True, facecolor="white", framealpha=0.92, fontsize=8.3)

    fig.suptitle(
        f"MRV-163：CN106 eth0 单 SMQ[0] vs 每队列独立 SMQ（64 B L2，burst=64，关闭流控，rclk={rclk_mhz}/sclk={sclk_mhz} MHz，n=3 中位数）",
        fontsize=11.4,
        fontweight="bold",
        y=0.99,
    )
    png = save(fig, "mrv-163-smq-scaling")
    print("Saved:", png)


if __name__ == "__main__":
    main()
