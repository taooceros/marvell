#!/usr/bin/env python3
"""Figure 1 (MRV-127): Open-loop independent Poisson mixed load — Shared vs Split dual credit windows.

Reads raw verified runs directly from logs/mrv127-2026-11-22/runs.jsonl.
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


def load_by_cfg():
    by_cfg = defaultdict(list)
    with RUNS.open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("part") != "part0_cal":
                assert r["verified"], f"unverified: {r['cfg']}"
                assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
                assert r["host_pmu"]["page_faults"] == 0
                by_cfg[r["cfg"]].append(r)
    return by_cfg


def main():
    by_cfg = load_by_cfg()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.2, 5.4))

    # ---- Panel A: Sweep 4 KiB random KV load (1.0 -> 2.5 Mreq/s) at fixed 64 B @ 8.0 Mreq/s ----
    lg_rates = [1.0, 1.5, 2.0, 2.2, 2.5]
    lg_tags = ["1p0m", "1p5m", "2p0m", "2p2m", "2p5m"]
    tag_map = dict(zip(lg_rates, lg_tags))

    series_a = [
        ("shr32", "共享单窗 W=32（MRV-117）", "#7f8c8d", "o", "--", [1.0, 1.5, 2.0, 2.2, 2.5]),
        ("shr64", "等总信用共享 W=64", "#d95f02", "s", "-.", [1.5, 2.0, 2.2, 2.5]),
        ("spl16", "拆分 (16, 32) C_line=32", "#7570b3", "^", ":", [1.0, 1.5, 2.0, 2.2, 2.5]),
        ("spl32", "拆分 (32, 32) C_line=32", "#1b6ca8", "D", "-", [1.0, 1.5, 2.0, 2.2, 2.5]),
    ]

    for key, label, col, mk, ls, xs in series_a:
        meds_tot, meds_wire = [], []
        for x in xs:
            cfg = f"p3_8m_{tag_map[x]}_{key}"
            rs = by_cfg[cfg]
            ys_t = [r["lat_small_tot_us"]["p99"] for r in rs]
            ys_w = [r["lat_small_wire_us"]["p99"] for r in rs]
            meds_tot.append(statistics.median(ys_t))
            meds_wire.append(statistics.median(ys_w))
            jitter = np.linspace(-0.025, 0.025, len(ys_t))
            ax1.scatter([x + j for j in jitter], ys_t, color=col, s=22, alpha=0.45, zorder=3)
        ax1.plot(xs, meds_tot, color=col, marker=mk, linestyle=ls, linewidth=2.2, markersize=6.5, label=label, zorder=4)
        if key == "spl32":
            ax1.plot(
                xs, meds_wire, color=col, marker="o", linestyle=":", linewidth=1.5, markersize=4.5,
                alpha=0.85, label="拆分 (32, 32) wire p99（在途硬件）", zorder=4
            )

    ax1.axhline(2.5, color="#2e7d32", linestyle="--", linewidth=1.1, alpha=0.8)
    ax1.text(1.57, 2.85, "2.5 µs 目标", fontsize=8.4, color="#2e7d32",
             bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=1.0))
    ax1.axhline(2.0, color="#2e7d32", linestyle=":", linewidth=1.0, alpha=0.7)
    ax1.text(1.62, 1.48, "2.0 µs", fontsize=8.0, color="#2e7d32")
    ax1.axvspan(2.14, 2.56, color="#fdecea", alpha=0.45, zorder=0)
    ax1.text(2.35, 0.72, "4 KiB 越过\n18.6 GB/s 饱和", ha="center", fontsize=8.3, color="#a93226")

    ax1.annotate("40.5 ms\n(W=32/64 均崩)", xy=(2.5, 40500), xytext=(1.96, 22000),
                 arrowprops=dict(arrowstyle="->", color="#7f8c8d", lw=1.0), fontsize=8.4, color="#555")
    ax1.annotate("2.75 µs\n(↓14,727×)", xy=(2.5, 2.75), xytext=(2.18, 42.0),
                 arrowprops=dict(arrowstyle="->", color="#1b6ca8", lw=1.2), fontsize=8.8, fontweight="bold", color="#1b6ca8",
                 bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=1.2))
    ax1.annotate("2.44 µs", xy=(1.5, 2.44), xytext=(1.26, 0.85),
                 arrowprops=dict(arrowstyle="->", color="#1b6ca8", lw=1.0), fontsize=8.4, fontweight="bold", color="#1b6ca8")

    ax1.set_yscale("log")
    ax1.set_ylim(0.55, 130000)
    ax1.set_xlim(0.92, 2.58)
    ax1.set_xticks(lg_rates)
    ax1.set_xlabel("4 KiB 随机页 KV 读到达率 (Mreq/s，固定 64 B @ 8.0 Mreq/s)")
    ax1.set_ylabel("小包 (64 B) 端到端 tot p99 / wire p99 (µs，对数)")
    ax1.set_title("A · 固定 8 M/s 小包，扫 4 KiB 负载至链路饱和（HoL 隔离验证）", fontsize=11, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=8.2, frameon=True, framealpha=0.92)

    # ---- Panel B: Sweep 64 B small rate (6 -> 12 Mreq/s) at fixed 4 KiB rand @ 1.5 Mreq/s ----
    sm_rates = [6.0, 8.0, 10.0, 12.0]
    series_b = [
        ("shr32", "共享单窗 W=32", "#7f8c8d", "o", "--", [6.0, 8.0, 10.0, 12.0]),
        ("shr64", "等总信用共享 W=64", "#d95f02", "s", "-.", [8.0, 12.0]),
        ("spl16", "拆分 (16, 32)（利特尔上限 ~9 M/s）", "#7570b3", "^", ":", [6.0, 8.0, 10.0, 12.0]),
        ("spl24", "拆分 (24, 32) C_line=32", "#2ca02c", "v", "-.", [8.0, 10.0]),
        ("spl32", "拆分 (32, 32) C_line=32", "#1b6ca8", "D", "-", [6.0, 8.0, 10.0, 12.0]),
    ]

    for key, label, col, mk, ls, xs in series_b:
        meds_tot, meds_wire = [], []
        for x in xs:
            cfg = f"p3_{int(x)}m_1p5m_{key}"
            rs = by_cfg[cfg]
            ys_t = [r["lat_small_tot_us"]["p99"] for r in rs]
            ys_w = [r["lat_small_wire_us"]["p99"] for r in rs]
            meds_tot.append(statistics.median(ys_t))
            meds_wire.append(statistics.median(ys_w))
            jitter = np.linspace(-0.12, 0.12, len(ys_t))
            ax2.scatter([x + j for j in jitter], ys_t, color=col, s=22, alpha=0.45, zorder=3)
        ax2.plot(xs, meds_tot, color=col, marker=mk, linestyle=ls, linewidth=2.2, markersize=6.5, label=label, zorder=4)
        if key == "spl32":
            ax2.plot(
                xs, meds_wire, color=col, marker="o", linestyle=":", linewidth=1.5, markersize=4.5,
                alpha=0.85, label="拆分 (32, 32) wire p99", zorder=4
            )

    ax2.axhline(2.5, color="#2e7d32", linestyle="--", linewidth=1.1, alpha=0.8)
    ax2.axvline(9.0, color="#7570b3", linestyle=":", linewidth=1.1, alpha=0.7)
    ax2.text(9.08, 620, "W_small=16\n利特尔极限\n16/1.77≈9 M/s", fontsize=8.1, color="#5b4c9a",
             bbox=dict(facecolor="white", edgecolor="none", alpha=0.82, pad=1.2))

    ax2.annotate("70.5 ms\n(W_small=16 过载)", xy=(12.0, 70500), xytext=(9.45, 58000),
                 arrowprops=dict(arrowstyle="->", color="#7570b3", lw=1.0), fontsize=8.2, color="#5b4c9a")
    ax2.annotate("19.3 ms (W=32)\n3.87 ms (W=64 慢态)", xy=(12.0, 19335), xytext=(9.25, 7200),
                 arrowprops=dict(arrowstyle="->", color="#7f8c8d", lw=1.0), fontsize=8.2, color="#555",
                 bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=1.0))
    ax2.annotate("7.08 µs (满载 13.5 M/s)\nwire 2.77 µs", xy=(12.0, 7.08), xytext=(9.4, 0.72),
                 arrowprops=dict(arrowstyle="->", color="#1b6ca8", lw=1.2), fontsize=8.5, fontweight="bold", color="#1b6ca8")

    ax2.set_yscale("log")
    ax2.set_ylim(0.55, 160000)
    ax2.set_xlim(5.5, 12.6)
    ax2.set_xticks(sm_rates)
    ax2.set_xlabel("64 B 小包泊松到达率 (Mreq/s，固定 4 KiB 随机 KV @ 1.5 Mreq/s)")
    ax2.set_ylabel("小包 (64 B) 端到端 tot p99 / wire p99 (µs，对数)")
    ax2.set_title("B · 固定 1.5 M/s 大包，扫 64 B 到达率（W_small 利特尔法则边界）", fontsize=11, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=8.0, frameon=True, framealpha=0.92)

    fig.suptitle(
        "MRV-127 开环独立泊松混合负载（1H cpu40 3.90 GHz + 1C core 3 2.00 GHz + 2 DPI VFs，128 MiB 随机大页，n=3 全散点 + 中位数）",
        fontsize=11.5, fontweight="bold", y=0.995,
    )
    out = mrvplot.save(fig, "mrv-127-openmix-credit")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
