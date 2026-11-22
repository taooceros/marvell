#!/usr/bin/env python3
"""Figure 1 (MRV-127): Pure 64 B open-loop independent Poisson mixed load — Shared vs Split dual credit windows.

Reads raw verified runs directly from logs/mrv127-2026-11-22/runs.jsonl.
Revised per MRV-138 review:
  - Explicitly scopes Panel A/B to pure 64 B small packets (not <= 256 B).
  - Notes missing C_line on shr32/shr64 controls and diagnoses 12 M shr64 crash as C_line slow-cliff
    confounder (63 lines, iio1_cpu_wr = 13.44 M lines/s) rather than large-packet HoL.
  - Uses exact unrounded median ratio (14,701x) and verifies W=64 saturation wire p99 (no slow state).
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
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.5))

    # ---- Panel A: Sweep 4 KiB random KV load (1.0 -> 2.5 Mreq/s) at fixed 64 B @ 8.0 Mreq/s ----
    lg_rates = [1.0, 1.5, 2.0, 2.2, 2.5]
    lg_tags = ["1p0m", "1p5m", "2p0m", "2p2m", "2p5m"]
    tag_map = dict(zip(lg_rates, lg_tags))

    series_a = [
        ("shr32", "共享单窗 W=32（无 C_line，MRV-117）", "#7f8c8d", "o", "--", [1.0, 1.5, 2.0, 2.2, 2.5]),
        ("shr64", "等总信用共享 W=64（无 C_line）", "#d95f02", "s", "-.", [1.5, 2.0, 2.2, 2.5]),
        ("spl16", "拆分 (16, 32) C_line=32", "#7570b3", "^", ":", [1.0, 1.5, 2.0, 2.2, 2.5]),
        ("spl32", "拆分 (32, 32) C_line=32", "#1b6ca8", "D", "-", [1.0, 1.5, 2.0, 2.2, 2.5]),
    ]

    for key, label, col, mk, ls, xs in series_a:
        meds_tot, meds_wire = [], []
        for x in xs:
            cfg = f"p3_8m_{tag_map[x]}_{key}"
            rs = by_cfg[cfg]
            assert all(r["small_profile"] == "64" for r in rs)
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

    shr64_25_tot = statistics.median(r["lat_small_tot_us"]["p99"] for r in by_cfg["p3_8m_2p5m_shr64"])
    shr64_25_wire = statistics.median(r["lat_small_wire_us"]["p99"] for r in by_cfg["p3_8m_2p5m_shr64"])
    shr64_25_lg = statistics.median(r["avg_large_inflight"] for r in by_cfg["p3_8m_2p5m_shr64"])
    spl32_25_tot = statistics.median(r["lat_small_tot_us"]["p99"] for r in by_cfg["p3_8m_2p5m_spl32"])
    spl32_15_tot = statistics.median(r["lat_small_tot_us"]["p99"] for r in by_cfg["p3_8m_1p5m_spl32"])
    ratio_25 = int(round(shr64_25_tot / spl32_25_tot))

    ax1.axhline(2.5, color="#2e7d32", linestyle="--", linewidth=1.1, alpha=0.8)
    ax1.text(1.56, 2.95, "2.5 µs 目标（纯 64 B）", fontsize=8.1, color="#2e7d32",
             bbox=dict(facecolor="white", edgecolor="none", alpha=0.82, pad=1.0))
    ax1.axhline(2.0, color="#2e7d32", linestyle=":", linewidth=1.0, alpha=0.7)
    ax1.text(1.62, 1.44, "2.0 µs", fontsize=7.9, color="#2e7d32")
    ax1.axvspan(2.14, 2.56, color="#fdecea", alpha=0.45, zorder=0)
    ax1.text(2.35, 0.68, "4 KiB 越过\n18.6 GB/s 饱和", ha="center", fontsize=8.1, color="#a93226")

    ax1.annotate(
        f"{shr64_25_tot/1000:.1f} ms（W=32/64 均崩）\nW=64 大包占 {shr64_25_lg:.1f}/64 槽\nwire {shr64_25_wire:.2f} µs 未陷慢态",
        xy=(2.5, shr64_25_tot), xytext=(1.15, 780),
        arrowprops=dict(arrowstyle="->", color="#7f8c8d", lw=1.0), fontsize=7.8, color="#444",
        bbox=dict(facecolor="white", edgecolor="#ddd", alpha=0.92, pad=2.0),
    )
    ax1.annotate(
        f"{spl32_25_tot:.2f} µs\n(↓{ratio_25:,}×)", xy=(2.5, spl32_25_tot), xytext=(2.18, 42.0),
        arrowprops=dict(arrowstyle="->", color="#1b6ca8", lw=1.2), fontsize=8.6, fontweight="bold", color="#1b6ca8",
        bbox=dict(facecolor="white", edgecolor="none", alpha=0.78, pad=1.2),
    )
    ax1.annotate(
        f"{spl32_15_tot:.2f} µs", xy=(1.5, spl32_15_tot), xytext=(1.22, 0.84),
        arrowprops=dict(arrowstyle="->", color="#1b6ca8", lw=1.0), fontsize=8.3, fontweight="bold", color="#1b6ca8",
    )

    ax1.set_yscale("log")
    ax1.set_ylim(0.55, 160000)
    ax1.set_xlim(0.92, 2.58)
    ax1.set_xticks(lg_rates)
    ax1.set_xlabel("4 KiB 随机页 KV 读到达率 (Mreq/s，固定纯 64 B @ 8.0 Mreq/s)")
    ax1.set_ylabel("纯 64 B 小包 tot p99 / wire p99 (µs，对数)")
    ax1.set_title("A · 固定 8 M/s 纯 64 B，扫 4 KiB 至饱和（纯 HoL 隔离验证）", fontsize=10.7, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=7.8, frameon=True, framealpha=0.92)

    # ---- Panel B: Sweep 64 B small rate (6 -> 12 Mreq/s) at fixed 4 KiB rand @ 1.5 Mreq/s ----
    sm_rates = [6.0, 8.0, 10.0, 12.0]
    series_b = [
        ("shr32", "共享单窗 W=32（无 C_line）", "#7f8c8d", "o", "--", [6.0, 8.0, 10.0, 12.0]),
        ("shr64", "等总信用共享 W=64（无 C_line）", "#d95f02", "s", "-.", [8.0, 12.0]),
        ("spl16", "拆分 (16, 32)（利特尔上限 ~9 M/s）", "#7570b3", "^", ":", [6.0, 8.0, 10.0, 12.0]),
        ("spl24", "拆分 (24, 32) C_line=32", "#2ca02c", "v", "-.", [8.0, 10.0]),
        ("spl32", "拆分 (32, 32) C_line=32", "#1b6ca8", "D", "-", [6.0, 8.0, 10.0, 12.0]),
    ]

    for key, label, col, mk, ls, xs in series_b:
        meds_tot, meds_wire = [], []
        for x in xs:
            cfg = f"p3_{int(x)}m_1p5m_{key}"
            rs = by_cfg[cfg]
            assert all(r["small_profile"] == "64" for r in rs)
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

    shr64_12_tot = statistics.median(r["lat_small_tot_us"]["p99"] for r in by_cfg["p3_12m_1p5m_shr64"])
    shr64_12_iio = statistics.median(r["host_pmu"]["iio1_cpu_wr"] / r["secs"] / 1e6 for r in by_cfg["p3_12m_1p5m_shr64"])
    shr64_12_maxl = int(statistics.median(r["max_push_lines_inflight"] for r in by_cfg["p3_12m_1p5m_shr64"]))
    shr32_12_tot = statistics.median(r["lat_small_tot_us"]["p99"] for r in by_cfg["p3_12m_1p5m_shr32"])
    spl16_12_tot = statistics.median(r["lat_small_tot_us"]["p99"] for r in by_cfg["p3_12m_1p5m_spl16"])
    spl32_12_tot = statistics.median(r["lat_small_tot_us"]["p99"] for r in by_cfg["p3_12m_1p5m_spl32"])
    spl32_12_wire = statistics.median(r["lat_small_wire_us"]["p99"] for r in by_cfg["p3_12m_1p5m_spl32"])
    spl32_10_tot = statistics.median(r["lat_small_tot_us"]["p99"] for r in by_cfg["p3_10m_1p5m_spl32"])

    ax2.axhline(2.5, color="#2e7d32", linestyle="--", linewidth=1.1, alpha=0.8)
    ax2.axvline(9.0, color="#7570b3", linestyle=":", linewidth=1.1, alpha=0.7)
    ax2.text(8.88, 1300, "W_small=16\n利特尔极限\n16/1.77≈9 M/s", ha="right", fontsize=7.8, color="#5b4c9a",
             bbox=dict(facecolor="white", edgecolor="none", alpha=0.88, pad=1.2))

    ax2.annotate(
        f"{spl16_12_tot/1000:.1f} ms（W_small=16 过载）",
        xy=(12.0, spl16_12_tot), xytext=(8.65, 92000),
        arrowprops=dict(arrowstyle="->", color="#7570b3", lw=1.0), fontsize=7.8, color="#5b4c9a",
    )
    ax2.annotate(
        f"{shr32_12_tot/1000:.1f} ms\n(W=32 过载)",
        xy=(12.0, shr32_12_tot), xytext=(10.22, 6800),
        arrowprops=dict(arrowstyle="->", color="#7f8c8d", lw=1.0), fontsize=7.8, color="#555",
        bbox=dict(facecolor="white", edgecolor="none", alpha=0.86, pad=1.0),
    )
    ax2.annotate(
        f"{shr64_12_tot/1000:.2f} ms（缺 C_line 混淆）\n{shr64_12_maxl} 行锁死 {shr64_12_iio:.2f} M行/s 慢崖",
        xy=(12.0, shr64_12_tot), xytext=(9.56, 1650),
        arrowprops=dict(arrowstyle="->", color="#d95f02", lw=1.1), fontsize=7.3, color="#b84a00",
        bbox=dict(facecolor="white", edgecolor="#f0c8a8", alpha=0.95, pad=1.8),
    )
    ax2.annotate(
        f"10/12M: {spl32_10_tot:.2f} / {spl32_12_tot:.2f} µs (>2.5 µs)\n12M 满载 wire {spl32_12_wire:.2f} µs",
        xy=(12.0, spl32_12_tot), xytext=(8.5, 0.72),
        arrowprops=dict(arrowstyle="->", color="#1b6ca8", lw=1.2), fontsize=8.0, fontweight="bold", color="#1b6ca8",
    )

    ax2.set_yscale("log")
    ax2.set_ylim(0.55, 220000)
    ax2.set_xlim(5.5, 12.6)
    ax2.set_xticks(sm_rates)
    ax2.set_xlabel("纯 64 B 小包泊松到达率 (Mreq/s，固定 4 KiB 随机 KV @ 1.5 Mreq/s)")
    ax2.set_ylabel("纯 64 B 小包 tot p99 / wire p99 (µs，对数)")
    ax2.set_title("B · 固定 1.5 M/s 4 KiB，扫纯 64 B 速率（利特尔边界与 C_line 混淆）", fontsize=10.7, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=7.7, frameon=True, framealpha=0.92)

    fig.suptitle(
        "MRV-127 纯 64 B 开环泊松混合负载（1H cpu40 3.90 GHz + 1C core 3 2.00 GHz + 2 DPI VFs，128 MiB 随机 64 页，n=3）",
        fontsize=11.3, fontweight="bold", y=0.995,
    )
    out = mrvplot.save(fig, "mrv-127-openmix-credit")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
