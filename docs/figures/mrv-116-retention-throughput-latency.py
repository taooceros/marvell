#!/usr/bin/env python3
"""Plot MRV-116 full transparent TX retention pool scan (64 KiB -> 128 MiB):
  (a) Normalized throughput across ALL key regimes:
      - W=64 closed-loop fill_seq (-0.87%, WC1 single card-core capped) & cold_seq (-0.01%)
      - Uncapped unidirectional H2C fill_seq (-17.48%, host-local DDR5 RFO+WB ceiling)
      - Cliff-free W=16 closed-loop fill_rand (-22.44%, local random DRAM latency; 32 MiB warmup caveat marked)
      - W=64 closed-loop fill_rand (-68.7% at 4 MiB due to PEM0 slow-state bifurcation, 0 LLC miss)
      With corrected CC-NIC ASPLOS'24 Fig. 15 Step 1 (-20%, TX retention only) and Step 1+2 (-50%, +4 KiB stride).
  (b) Closed-loop RTT latency (W=1 & W=64 seq vs W=1 & W=16 rand, with explicit note on W=64 rand 4.87 us slow state).

Reads raw verified runs directly from logs/mrv116-2026-11-20/runs.jsonl.
"""
import json
import pathlib
import statistics

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv116-2026-11-20/runs.jsonl"

SIZES = ["64k", "4m", "32m", "128m"]
X_POS = np.arange(len(SIZES), dtype=float)
X_LABELS = ["64 KiB\n(L1/L2 热)", "4 MiB\n(L2 溢出/LLC)", "32 MiB\n(半 LLC)", "128 MiB\n(>60 MiB LLC)"]

C_TEAL = "#0f766e"
C_BLUE = "#2563eb"
C_SLATE = "#475569"
C_ORANGE = "#d97706"
C_RED = "#dc2626"
C_MUTED = "#94a3b8"


def load_runs():
    rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
    for r in rows:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0, f"bad run {r['run_id']}"
        assert r["host_pmu"]["page_faults"] == 0, f"page faults in {r['run_id']}"
    return rows


def group_vals(rows, part, prefix, getter):
    out = []
    for sz in SIZES:
        cfg = f"{prefix}_{sz}"
        vals = [getter(r) for r in rows if r["part"] == part and r["cfg"] == cfg]
        assert len(vals) == 3, f"expected 3 reps for {part}/{cfg}, got {len(vals)}"
        out.append(vals)
    return out


def main():
    rows = load_runs()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.2, 5.4))

    # --- Panel (a): Normalized throughput across ALL key fill_and_retain & seq regimes ---
    series_a = [
        ("part1_w64", "fill_seq",  "W=64 闭环 写后留存·顺序 (47.5→47.1 M/s, WC1 封顶)", C_TEAL,   "-o",  2.4),
        ("part1_w64", "cold_seq",  "W=64 闭环 纯冷源·顺序 (48.7→48.7 M/s)",              C_SLATE,  ":s",  1.8),
        ("part3_h2c", "fill_seq",  "H2C 单向 写后留存·顺序 (155.2→128.1 M/s, 无卡封顶)",  C_BLUE,   "-^",  2.1),
        ("part4_w16", "fill_rand", "W=16 闭环 写后留存·随机 (13.2→10.2 M/s, 无慢态)",     C_ORANGE, "--D", 2.0),
        ("part1_w64", "fill_rand", "W=64 闭环 写后留存·随机 (43.2→13.5 M/s, 4M 慢态)",    C_RED,    "--v", 2.0),
    ]

    # < 2% pass band
    ax1.axhspan(98.0, 102.0, color=C_TEAL, alpha=0.11, zorder=0, label="判据带：退化 < 2% (98%–102%)")

    # Corrected CC-NIC ASPLOS'24 Fig. 15 reference lines (Step 1 = -20%, Step 1+2 = -50%)
    ax1.axhline(80.0, color="#b45309", ls="--", lw=1.35, alpha=0.85, zorder=1)
    ax1.text(
        0.38,
        78.5,
        "CC-NIC Fig.15 第 1 步\n(仅 TX 留存, 56 核): 80% (-20%)",
        fontsize=7.0,
        color="#9a3412",
        va="top",
        bbox=dict(boxstyle="square,pad=0.10", fc="white", ec="none", alpha=0.88),
    )
    ax1.axhline(50.0, color=C_MUTED, ls=":", lw=1.2, alpha=0.9, zorder=1)
    ax1.text(
        0.84,
        51.0,
        "CC-NIC 第 1+2 步（叠 4 KiB 步长）：50% (-50%)",
        fontsize=7.2,
        color="#64748b",
        va="bottom",
        bbox=dict(boxstyle="square,pad=0.12", fc="white", ec="none", alpha=0.88),
    )

    rel_128 = {}
    for part, prefix, label, color, style, lw in series_a:
        raw = group_vals(rows, part, prefix, lambda r: r["mops"])
        base = statistics.mean(raw[0])
        means = [statistics.mean(v) / base * 100.0 for v in raw]
        mins = [min(v) / base * 100.0 for v in raw]
        maxs = [max(v) / base * 100.0 for v in raw]
        yerr = [
            [m - lo for m, lo in zip(means, mins)],
            [hi - m for m, hi in zip(means, maxs)],
        ]
        ax1.errorbar(
            X_POS,
            means,
            yerr=yerr,
            fmt=style,
            color=color,
            lw=lw,
            ms=6.0,
            capsize=3.2,
            label=label,
            zorder=4,
        )
        for xi, vs in zip(X_POS, raw):
            ax1.scatter([xi] * len(vs), [v / base * 100.0 for v in vs], color=color, s=13, alpha=0.35, zorder=3)
        rel_128[(part, prefix)] = means

    # Right-edge direct labels at 128 MiB (cleanly staggered, zero overlap)
    y_w64_fill = rel_128[("part1_w64", "fill_seq")][3]
    y_h2c_fill = rel_128[("part3_h2c", "fill_seq")][3]
    y_w16_rand = rel_128[("part4_w16", "fill_rand")][3]
    y_w64_rand = rel_128[("part1_w64", "fill_rand")][3]
    y_w64_rand_4m = rel_128[("part1_w64", "fill_rand")][1]
    y_w16_rand_32m = rel_128[("part4_w16", "fill_rand")][2]

    ax1.text(3.08, y_w64_fill + 3.2, f"W=64 顺序 {y_w64_fill-100:+.2f}%\n(WC1 单卡核封顶)", fontsize=7.6, color=C_TEAL, va="center", fontweight="bold")
    ax1.text(3.08, y_h2c_fill + 3.2, f"H2C 顺序 {y_h2c_fill-100:+.1f}%\n(本地 DDR5 上限)", fontsize=7.6, color=C_BLUE, va="center", fontweight="bold")
    ax1.text(3.08, y_w16_rand - 5.2, f"W=16 随机 {y_w16_rand-100:+.1f}%\n(本地 DRAM 延迟)", fontsize=7.6, color=C_ORANGE, va="center", fontweight="bold")
    ax1.text(3.08, y_w64_rand + 1.0, f"W=64 随机 {y_w64_rand-100:+.1f}%\n(PEM0 慢态)", fontsize=7.6, color=C_RED, va="center", fontweight="bold")

    # Callout for 4 MiB slow-state bifurcation & 32 MiB warmup caveat
    ax1.annotate(
        f"4 MiB 坠入 PEM0 慢态\n({y_w64_rand_4m-100:+.1f}%, LLC miss=0)",
        xy=(1.0, y_w64_rand_4m),
        xytext=(1.22, 39.0),
        fontsize=7.5,
        color=C_RED,
        bbox=dict(boxstyle="round,pad=0.25", fc="#fef2f2", ec=C_RED, lw=0.8),
        arrowprops=dict(arrowstyle="->", color=C_RED, lw=0.95),
        zorder=6,
    )
    ax1.annotate(
        "* 32 MiB 热身 0.5 圈\n(伪 LLC miss=0.40)",
        xy=(2.0, y_w16_rand_32m),
        xytext=(1.60, 62.0),
        fontsize=7.1,
        color="#9a3412",
        bbox=dict(boxstyle="round,pad=0.22", fc="#fffbeb", ec=C_ORANGE, lw=0.75),
        arrowprops=dict(arrowstyle="->", color=C_ORANGE, lw=0.9),
        zorder=6,
    )

    ax1.axvline(2.45, color=C_RED, ls=":", lw=1.35)
    ax1.text(2.42, 22.0, "60 MiB LLC\n容量边界", fontsize=7.6, color=C_RED, ha="right", va="bottom")

    ax1.set_xticks(X_POS)
    ax1.set_xticklabels(X_LABELS, fontsize=8.5)
    ax1.set_xlim(-0.30, 4.18)
    ax1.set_ylim(20, 114)
    ax1.set_ylabel("相对吞吐（% of 各自 64 KiB 基线）", fontsize=9.3)
    ax1.set_title("(a) 全口径相对吞吐：闭环顺序/随机与无卡封顶 H2C", fontsize=10.3, pad=8)
    ax1.legend(
        loc="upper center",
        bbox_to_anchor=(0.50, -0.17),
        ncol=2,
        fontsize=7.3,
        framealpha=0.95,
    )

    # --- Panel (b): Closed-loop RTT latency (seq vs cliff-free rand) ---
    lat_series = [
        ("part1_w64", "fill_seq",  "p99", "W=64 顺序留存 p99", C_TEAL,   "--^", 1.8),
        ("part1_w64", "fill_seq",  "p50", "W=64 顺序留存 p50", C_TEAL,   "-o",  2.3),
        ("part4_w16", "fill_rand", "p50", "W=16 随机留存 p50 (*32M 热身 0.5×)", C_ORANGE, "--D", 2.0),
        ("part2_w1",  "fill_rand", "p50", "W=1 随机留存 p50 (*32M 热身 0.04×)", C_RED,    "--v", 1.9),
        ("part2_w1",  "fill_seq",  "p99", "W=1 顺序留存 p99",  C_SLATE,  ":^",  1.6),
        ("part2_w1",  "fill_seq",  "p50", "W=1 顺序留存 p50",  C_SLATE,  "-o",  2.1),
    ]

    lat_means = {}
    for part, prefix, q, label, color, style, lw in lat_series:
        raw = group_vals(rows, part, prefix, lambda r, q=q: r["lat_us"][q])
        means = [statistics.mean(v) for v in raw]
        mins = [min(v) for v in raw]
        maxs = [max(v) for v in raw]
        yerr = [
            [m - lo for m, lo in zip(means, mins)],
            [hi - m for m, hi in zip(means, maxs)],
        ]
        ax2.errorbar(
            X_POS,
            means,
            yerr=yerr,
            fmt=style,
            color=color,
            lw=lw,
            ms=5.8,
            capsize=3.0,
            label=label,
            zorder=4,
        )
        lat_means[(part, prefix, q)] = means

    # Mark 32 MiB warmup caveat stars on W=16 rand & W=1 rand
    ax2.text(2.0, lat_means[("part4_w16", "fill_rand", "p50")][2] + 0.035, "*", ha="center", fontsize=11, color=C_ORANGE, fontweight="bold")
    ax2.text(2.0, lat_means[("part2_w1",  "fill_rand", "p50")][2] + 0.030, "*", ha="center", fontsize=11, color=C_RED, fontweight="bold")

    # Clean staggered right-edge labels
    def add_lat_lbl(key, dy, color, mode="pct"):
        m0, m3 = lat_means[key][0], lat_means[key][3]
        if mode == "ns":
            dn = (m3 - m0) * 1000.0
            txt = f"{m3:.3f} µs ({dn:+.0f} ns)"
        else:
            dp = (m3 / m0 - 1.0) * 100.0
            txt = f"{m3:.3f} µs ({dp:+.1f}%)"
        ax2.text(3.07, m3 + dy, txt, fontsize=7.6, color=color, va="center", fontweight="bold" if color in (C_TEAL, C_ORANGE, C_RED) else "normal")

    add_lat_lbl(("part1_w64", "fill_seq",  "p99"),  0.000, C_TEAL,   "pct")
    add_lat_lbl(("part4_w16", "fill_rand", "p50"),  0.038, C_ORANGE, "ns")
    add_lat_lbl(("part1_w64", "fill_seq",  "p50"), -0.028, C_TEAL,   "pct")
    add_lat_lbl(("part2_w1",  "fill_rand", "p50"),  0.032, C_RED,    "ns")
    add_lat_lbl(("part2_w1",  "fill_seq",  "p99"), -0.005, C_SLATE,  "pct")
    add_lat_lbl(("part2_w1",  "fill_seq",  "p50"), -0.045, C_SLATE,  "pct")

    # Explicit non-omission banner for W=64 rand slow state (above axis range)
    ax2.text(
        0.02,
        0.96,
        "注：W=64 随机留存 ≥4 MiB 坠入 PEM0 慢态\n(p50 = 4.61–4.87 µs，超出本轴；见左图红线)",
        transform=ax2.transAxes,
        fontsize=7.6,
        color=C_RED,
        va="top",
        bbox=dict(boxstyle="round,pad=0.28", fc="#fef2f2", ec=C_RED, lw=0.8, alpha=0.95),
    )

    ax2.axvline(2.45, color=C_RED, ls=":", lw=1.35)
    ax2.text(2.42, 0.78, "60 MiB LLC\n容量边界", fontsize=7.6, color=C_RED, ha="right", va="bottom")

    ax2.set_xticks(X_POS)
    ax2.set_xticklabels(X_LABELS, fontsize=8.5)
    ax2.set_xlim(-0.25, 4.18)
    ax2.set_ylim(0.75, 1.88)
    ax2.set_ylabel("闭环 RTT 延迟（µs，1 ns 直方图）", fontsize=9.3)
    ax2.set_title("(b) 闭环延迟：顺序留存 < 1% vs 随机本地 DRAM 税", fontsize=10.3, pad=8)
    ax2.legend(
        loc="upper center",
        bbox_to_anchor=(0.50, -0.17),
        ncol=2,
        fontsize=7.3,
        framealpha=0.95,
    )

    fig.suptitle(
        "MRV-116 主机 TX 留存池（64 KiB→128 MiB，2 MiB 大页零缺页，64 B 紧凑槽）：MOVDIR64B→BAR4 + 卡 WC1→DDIO（batch=1，3.90/2.20/2.00 GHz，n=3）",
        fontsize=8.9,
        y=0.985,
    )
    fig.subplots_adjust(bottom=0.24, top=0.88, left=0.06, right=0.98, wspace=0.22)
    # Save directly without tight_layout clobbering bottom legends
    png = mrvplot.HERE / "mrv-116-retention-throughput-latency.png"
    fig.savefig(png, bbox_inches="tight", pad_inches=0.14)
    fig.savefig(mrvplot.HERE / "mrv-116-retention-throughput-latency.svg", bbox_inches="tight", pad_inches=0.14)
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
