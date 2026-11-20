#!/usr/bin/env python3
"""Plot MRV-116 primary single-variable result: TX retention pool size (64 KiB -> 128 MiB)
vs relative throughput (with CC-NIC Fig. 15 literature range) and closed-loop latency (W=1 & W=64).

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
X_LABELS = ["64 KiB\n(L1/L2 热)", "4 MiB\n(L2 溢出/LLC 热)", "32 MiB\n(半 LLC)", "128 MiB\n(>60 MiB LLC)"]

C_TEAL = "#0f766e"
C_BLUE = "#2563eb"
C_SLATE = "#475569"
C_ORANGE = "#d97706"
C_RED = "#dc2626"


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
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.8, 4.7))

    # --- Panel (a): Normalized throughput (% of 64 KiB baseline) ---
    series_a = [
        ("part1_w64", "fill_seq", "W=64 闭环 写后留存 (fill_and_retain, ~47.5 M/s)", C_TEAL, "-o", 2.4),
        ("part1_w64", "cold_seq", "W=64 闭环 纯冷源对照 (cold_read_only, ~48.7 M/s)", C_BLUE, "--s", 2.0),
        ("part3_h2c", "cold_seq", "单向直推 纯冷源 (H2C MOVDIR64B, ~175 M/s)", C_SLATE, ":^", 1.9),
    ]

    # < 2% zero-degradation pass band
    ax1.axhspan(98.0, 102.0, color=C_TEAL, alpha=0.10, zorder=0, label="判据带：退化 < 2% (98%–102%)")
    # CC-NIC ASPLOS'24 Fig. 15 literature degradation zone (54% - 80%)
    ax1.axhspan(54.0, 80.0, color=C_ORANGE, alpha=0.13, zorder=0)
    ax1.axhline(80.0, color=C_ORANGE, ls="--", lw=1.1, alpha=0.75)
    ax1.axhline(54.0, color=C_RED, ls="--", lw=1.1, alpha=0.75)
    ax1.text(
        0.04,
        67.0,
        "文献对照：CC-NIC (ASPLOS'24 Fig.15)\n留存 TX 缓冲吞吐暴跌 -20% ~ -46% (剩余 80%–54%)",
        fontsize=8.3,
        color="#9a3412",
        va="center",
        bbox=dict(boxstyle="round,pad=0.3", fc="#fff7ed", ec=C_ORANGE, alpha=0.9, lw=0.8),
    )

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
            ms=6.5,
            capsize=3.5,
            label=label,
            zorder=4,
        )
        # Plot individual replicate dots
        for xi, vs in zip(X_POS, raw):
            ax1.scatter([xi] * len(vs), [v / base * 100.0 for v in vs], color=color, s=14, alpha=0.35, zorder=3)

    # Annotate exact 128 MiB values
    w64_fill = group_vals(rows, "part1_w64", "fill_seq", lambda r: r["mops"])
    w64_fill_128 = statistics.mean(w64_fill[3]) / statistics.mean(w64_fill[0]) * 100.0
    w64_cold = group_vals(rows, "part1_w64", "cold_seq", lambda r: r["mops"])
    w64_cold_128 = statistics.mean(w64_cold[3]) / statistics.mean(w64_cold[0]) * 100.0
    h2c_cold = group_vals(rows, "part3_h2c", "cold_seq", lambda r: r["mops"])
    h2c_cold_128 = statistics.mean(h2c_cold[3]) / statistics.mean(h2c_cold[0]) * 100.0

    ax1.annotate(
        f"写后留存: {w64_fill_128:.2f}% ({w64_fill_128-100:+.2f}%)\n纯冷源: {w64_cold_128:.2f}% ({w64_cold_128-100:+.2f}%)\n单向H2C: {h2c_cold_128:.2f}% ({h2c_cold_128-100:+.2f}%)",
        xy=(3.0, w64_fill_128),
        xytext=(1.70, 86.2),
        fontsize=8.2,
        color="#0f172a",
        bbox=dict(boxstyle="round,pad=0.32", fc="white", ec=C_TEAL, lw=0.9),
        arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=1.0, connectionstyle="arc3,rad=0.15"),
        zorder=6,
    )

    ax1.axvline(2.45, color=C_RED, ls=":", lw=1.4)
    ax1.text(2.48, 43.0, "Socket 1 LLC\n容量边界 (60 MiB)", fontsize=8.0, color=C_RED, va="bottom")

    ax1.set_xticks(X_POS)
    ax1.set_xticklabels(X_LABELS, fontsize=8.8)
    ax1.set_xlim(-0.3, 3.35)
    ax1.set_ylim(40, 112)
    ax1.set_ylabel("相对吞吐（% of 各自 64 KiB 基线）", fontsize=9.5)
    ax1.set_title("(a) 吞吐零退化：64 KiB → 128 MiB（单变量扫留存池）", fontsize=10.5, pad=8)
    ax1.legend(loc="lower left", fontsize=7.6, framealpha=0.94)

    # --- Panel (b): Closed-loop p50 & p99 latency across pool sizes (W=1 & W=64, seq) ---
    lat_series = [
        ("part1_w64", "fill_seq", "p99", "W=64 写后留存 p99", C_TEAL, "--^", 1.8),
        ("part1_w64", "fill_seq", "p50", "W=64 写后留存 p50", C_TEAL, "-o", 2.3),
        ("part1_w64", "cold_seq", "p50", "W=64 纯冷源 p50", C_BLUE, ":s", 1.8),
        ("part2_w1", "fill_seq", "p99", "W=1 写后留存 p99", C_SLATE, "--^", 1.6),
        ("part2_w1", "fill_seq", "p50", "W=1 写后留存 p50", C_SLATE, "-o", 2.1),
    ]

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
            ms=6.0,
            capsize=3.0,
            label=label,
            zorder=4,
        )

    # Annotate exact 64 KiB -> 128 MiB deltas
    w1_p50 = group_vals(rows, "part2_w1", "fill_seq", lambda r: r["lat_us"]["p50"])
    w1_p99 = group_vals(rows, "part2_w1", "fill_seq", lambda r: r["lat_us"]["p99"])
    w64_p50 = group_vals(rows, "part1_w64", "fill_seq", lambda r: r["lat_us"]["p50"])
    w64_p99 = group_vals(rows, "part1_w64", "fill_seq", lambda r: r["lat_us"]["p99"])

    d_w1_p50 = (statistics.mean(w1_p50[3]) / statistics.mean(w1_p50[0]) - 1.0) * 100.0
    d_w1_p99 = (statistics.mean(w1_p99[3]) / statistics.mean(w1_p99[0]) - 1.0) * 100.0
    d_w64_p50 = (statistics.mean(w64_p50[3]) / statistics.mean(w64_p50[0]) - 1.0) * 100.0
    d_w64_p99 = (statistics.mean(w64_p99[3]) / statistics.mean(w64_p99[0]) - 1.0) * 100.0

    ax2.text(
        3.03,
        statistics.mean(w64_p99[3]),
        f"{statistics.mean(w64_p99[3]):.3f} µs\n({d_w64_p99:+.1f}%)",
        fontsize=7.8,
        color=C_TEAL,
        va="center",
    )
    ax2.text(
        3.03,
        statistics.mean(w64_p50[3]),
        f"{statistics.mean(w64_p50[3]):.3f} µs\n({d_w64_p50:+.1f}%)",
        fontsize=7.8,
        color=C_TEAL,
        va="center",
    )
    ax2.text(
        3.03,
        statistics.mean(w1_p99[3]) + 0.025,
        f"{statistics.mean(w1_p99[3]):.3f} µs ({d_w1_p99:+.1f}%)",
        fontsize=7.6,
        color=C_SLATE,
        va="center",
    )
    ax2.text(
        3.03,
        statistics.mean(w1_p50[3]) - 0.035,
        f"{statistics.mean(w1_p50[3]):.3f} µs ({d_w1_p50:+.1f}%)",
        fontsize=7.6,
        color=C_SLATE,
        va="center",
    )

    ax2.axvline(2.45, color=C_RED, ls=":", lw=1.4)
    ax2.text(2.48, 0.72, "60 MiB LLC\n容量边界", fontsize=8.0, color=C_RED, va="bottom")

    ax2.set_xticks(X_POS)
    ax2.set_xticklabels(X_LABELS, fontsize=8.8)
    ax2.set_xlim(-0.25, 3.82)
    ax2.set_ylim(0.68, 1.82)
    ax2.set_ylabel("闭环 RTT 延迟（µs，1 ns 直方图）", fontsize=9.5)
    ax2.set_title("(b) 闭环延迟零退化：W=1 空载与 W=64 满载均 < 1%", fontsize=10.5, pad=8)
    ax2.legend(loc="upper left", fontsize=7.6, ncol=2, framealpha=0.94)

    fig.suptitle(
        "MRV-116 主机 TX 缓冲留存池（64 KiB→128 MiB，2 MiB 大页零缺页）：MOVDIR64B→BAR4 + 卡 WC1→DDIO（64 B，batch=1，3.90/2.20/2.00 GHz，n=3）",
        fontsize=8.8,
        y=0.985,
    )
    out = mrvplot.save(fig, "mrv-116-retention-throughput-latency")
    print(f"Saved {out}")

if __name__ == "__main__":
    main()
