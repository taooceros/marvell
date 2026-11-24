#!/usr/bin/env python3
"""MRV-170 Figure 3: Hardened zero-LDADDA performance verification on real dual-machine 100GbE.

Single-variable 4-arm Latin-square interleaved n=3 comparison across 64 B / 256 B / 1.5 KiB (1536 B):
  - default           (stock DPDK scalar_enable=1 LDADDA)
  - cqe_peek          (MRV-161 coherent ldar peek + on-demand LDADDA + 128 B clear)
  - cqe_direct_wd0    (MRV-170 hardened TRUE zero-LDADDA: multi-seg + err/0-seg fallback + 128 B clear)
  - cqe_direct_wd4096 (MRV-170 hardened 1/4096 periodic LDADDA watchdog hybrid)

Reads raw verified runs directly from logs/mrv170-2026-11-24/perf_runs.jsonl.
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
PERF_RUNS = ROOT / "logs/mrv170-2026-11-24/perf_runs.jsonl"

ARMS = [
    ("default", "default（原装 LDADDA）", "#64748b"),
    ("cqe_peek", "--cqe-peek（按需 LDADDA）", "#0ea5e9"),
    ("cqe_direct_wd0", "加固纯零 LDADDA（wd=0）", "#1d6fa5"),
    ("cqe_direct_wd4096", "加固 1/4096 看门狗（wd=4096）", "#0f766e"),
]


def load_runs():
    rows = []
    for line in PERF_RUNS.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r.get("verified") is True, f"unverified run: {r.get('name')}"
        rows.append(r)
    return rows


def stats(rows, name, getter):
    vals = [getter(r) for r in rows if r["name"] == name]
    assert len(vals) == 3, f"expected 3 reps for {name}, got {len(vals)}"
    med = statistics.median(vals)
    return med, med - min(vals), max(vals) - med, vals


def main():
    rows = load_runs()

    fig, axes = plt.subplots(2, 2, figsize=(15.0, 9.6))
    fig.suptitle(
        "MRV-170 加固后双机 100GbE DirectPush 性能复测：相对 --cqe-peek 全 12/12 胜出\n"
        "CN106 1C 2.0 GHz (scalar_enable=1, 单段快路径) ⇄ saturn cpu40 3.9 GHz (Uncore 2.2 GHz) ⇄ mercury cpu0 (max_tx_batch=16) · n=3 中位数 (min–max)",
        fontsize=11.5,
        fontweight="bold",
        y=0.985,
    )

    # ------------------------------------------------------------------
    # Panel A: W = 1 End-to-End p50 Latency across 64 B / 256 B / 1.5 KiB
    # ------------------------------------------------------------------
    ax = axes[0, 0]
    sizes = [("64", "64 B\n(OP_PUSH)"), ("256", "256 B\n(OP_PUSH)"), ("1536", "1.5 KiB\n(2×768B OP_DMA)")]
    x_s = np.arange(len(sizes))
    width = 0.195
    for idx, (pol, label, color) in enumerate(ARMS):
        meds, lo, hi = [], [], []
        for wl, _ in sizes:
            m, dlo, dhi, _ = stats(rows, f"direct_{pol}_{wl}_w1", lambda r: r["client"]["tot_p50_us"])
            meds.append(m); lo.append(dlo); hi.append(dhi)
        pos = x_s + (idx - 1.5) * width
        ax.bar(pos, meds, width=width * 0.90, color=color, alpha=0.92, label=label, zorder=3)
        ax.errorbar(pos, meds, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.0, capsize=2.2, zorder=4)
        for p, m, dhi in zip(pos, meds, hi):
            ax.text(p, m + dhi + 0.14, f"{m:.3f}", ha="center", va="bottom", fontsize=7.2,
                    fontweight="bold" if pol.startswith("cqe_direct") else "normal",
                    color=color if pol != "default" else "#334155")

    d64_pk = round((stats(rows, "direct_cqe_peek_64_w1", lambda r: r["client"]["tot_p50_us"])[0] -
                    stats(rows, "direct_cqe_direct_wd4096_64_w1", lambda r: r["client"]["tot_p50_us"])[0]) * 1000)
    d256_pk = round((stats(rows, "direct_cqe_peek_256_w1", lambda r: r["client"]["tot_p50_us"])[0] -
                     stats(rows, "direct_cqe_direct_wd4096_256_w1", lambda r: r["client"]["tot_p50_us"])[0]) * 1000)
    d15_pk = round((stats(rows, "direct_cqe_peek_1536_w1", lambda r: r["client"]["tot_p50_us"])[0] -
                    stats(rows, "direct_cqe_direct_wd4096_1536_w1", lambda r: r["client"]["tot_p50_us"])[0]) * 1000)
    ax.set_xticks(x_s)
    ax.set_xticklabels([lbl for _, lbl in sizes], fontsize=9.2)
    ax.set_ylabel("mercury 端到端往返 p50 (µs)", fontsize=9.8)
    ax.set_ylim(0, 12.8)
    ax.legend(loc="upper left", ncol=2, frameon=True, facecolor="white", fontsize=7.8)
    ax.set_title(f"A. W=1 端到端 p50（wd=4096 较 --cqe-peek 降 {d64_pk} / {d256_pk} / {d15_pk} ns）", fontsize=10.2, fontweight="bold", pad=8)
    # ------------------------------------------------------------------
    # Panel B: 64 B Throughput across W = 1, 4, 8, 32
    # ------------------------------------------------------------------
    ax = axes[0, 1]
    ws = [1, 4, 8, 32]
    x_w = np.arange(len(ws))
    for idx, (pol, label, color) in enumerate(ARMS):
        meds, lo, hi = [], [], []
        for w in ws:
            m, dlo, dhi, _ = stats(rows, f"direct_{pol}_64_w{w}", lambda r: r["client"]["achieved_mrps"])
            meds.append(m); lo.append(dlo); hi.append(dhi)
        pos = x_w + (idx - 1.5) * width
        ax.bar(pos, meds, width=width * 0.90, color=color, alpha=0.92, label=label, zorder=3)
        ax.errorbar(pos, meds, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.0, capsize=2.2, zorder=4)
        for p, m, dhi in zip(pos, meds, hi):
            ax.text(p, m + dhi + 0.05, f"{m:.3f}", ha="center", va="bottom", fontsize=7.0,
                    fontweight="bold" if pol.startswith("cqe_direct") else "normal",
                    color=color if pol != "default" else "#334155")

    r64_w8_def = stats(rows, "direct_default_64_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r64_w8_pk = stats(rows, "direct_cqe_peek_64_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r64_w8_wd0 = stats(rows, "direct_cqe_direct_wd0_64_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r64_w8_wd4k = stats(rows, "direct_cqe_direct_wd4096_64_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r64_w32_def = stats(rows, "direct_default_64_w32", lambda r: r["client"]["achieved_mrps"])[0]
    r64_w32_wd0 = stats(rows, "direct_cqe_direct_wd0_64_w32", lambda r: r["client"]["achieved_mrps"])[0]
    ax.set_xticks(x_w)
    ax.set_xticklabels([f"W={w}" for w in ws], fontsize=9.5)
    ax.set_ylabel("端到端吞吐 (Mreq/s)", fontsize=9.8)
    ax.set_ylim(0, 3.85)
    ax.set_title(
        f"B. 64 B 吞吐（W=8 +{100*(r64_w8_wd0/r64_w8_def-1):.1f}% · W=32 +{100*(r64_w32_wd0/r64_w32_def-1):.1f}% vs def；W=8 +{100*(r64_w8_wd4k/r64_w8_pk-1):.1f}% vs peek）",
        fontsize=9.5, fontweight="bold", pad=8,
    )
    # ------------------------------------------------------------------
    # Panel C: 256 B Throughput across W = 1, 4, 8, 32
    # ------------------------------------------------------------------
    ax = axes[1, 0]
    for idx, (pol, label, color) in enumerate(ARMS):
        meds, lo, hi = [], [], []
        for w in ws:
            m, dlo, dhi, _ = stats(rows, f"direct_{pol}_256_w{w}", lambda r: r["client"]["achieved_mrps"])
            meds.append(m); lo.append(dlo); hi.append(dhi)
        pos = x_w + (idx - 1.5) * width
        ax.bar(pos, meds, width=width * 0.90, color=color, alpha=0.92, label=label, zorder=3)
        ax.errorbar(pos, meds, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.0, capsize=2.2, zorder=4)
        for p, m, dhi in zip(pos, meds, hi):
            ax.text(p, m + dhi + 0.04, f"{m:.3f}", ha="center", va="bottom", fontsize=7.0,
                    fontweight="bold" if pol.startswith("cqe_direct") else "normal",
                    color=color if pol != "default" else "#334155")
    r256_w8_def = stats(rows, "direct_default_256_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r256_w8_pk = stats(rows, "direct_cqe_peek_256_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r256_w8_wd4k = stats(rows, "direct_cqe_direct_wd4096_256_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r256_w32_pk = stats(rows, "direct_cqe_peek_256_w32", lambda r: r["client"]["achieved_mrps"])[0]
    r256_w32_wd4k = stats(rows, "direct_cqe_direct_wd4096_256_w32", lambda r: r["client"]["achieved_mrps"])[0]
    ax.set_xticks(x_w)
    ax.set_xticklabels([f"W={w}" for w in ws], fontsize=9.5)
    ax.set_ylabel("端到端吞吐 (Mreq/s)", fontsize=9.8)
    ax.set_ylim(0, 3.30)
    ax.set_title(
        f"C. 256 B 吞吐（W=8 +{100*(r256_w8_wd4k/r256_w8_def-1):.1f}% vs def；W=8 +{100*(r256_w8_wd4k/r256_w8_pk-1):.1f}% · W=32 +{100*(r256_w32_wd4k/r256_w32_pk-1):.1f}% vs peek）",
        fontsize=9.5, fontweight="bold", pad=8,
    )
    # ------------------------------------------------------------------
    # Panel D: 1.5 KiB (1536 B, 2x768 B OP_DMA) Throughput across W = 1, 4, 8, 32
    # ------------------------------------------------------------------
    ax = axes[1, 1]
    for idx, (pol, label, color) in enumerate(ARMS):
        meds, lo, hi = [], [], []
        for w in ws:
            m, dlo, dhi, _ = stats(rows, f"direct_{pol}_1536_w{w}", lambda r: r["client"]["achieved_mrps"])
            meds.append(m); lo.append(dlo); hi.append(dhi)
        pos = x_w + (idx - 1.5) * width
        ax.bar(pos, meds, width=width * 0.90, color=color, alpha=0.92, label=label, zorder=3)
        ax.errorbar(pos, meds, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.0, capsize=2.2, zorder=4)
        for p, m, dhi in zip(pos, meds, hi):
            ax.text(p, m + dhi + 0.03, f"{m:.3f}", ha="center", va="bottom", fontsize=7.0,
                    fontweight="bold" if pol.startswith("cqe_direct") else "normal",
                    color=color if pol != "default" else "#334155")
    r15_w8_def = stats(rows, "direct_default_1536_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r15_w8_pk = stats(rows, "direct_cqe_peek_1536_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r15_w8_wd0 = stats(rows, "direct_cqe_direct_wd0_1536_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r15_w8_wd4k = stats(rows, "direct_cqe_direct_wd4096_1536_w8", lambda r: r["client"]["achieved_mrps"])[0]
    r15_w32_pk = stats(rows, "direct_cqe_peek_1536_w32", lambda r: r["client"]["achieved_mrps"])[0]
    r15_w32_wd4k = stats(rows, "direct_cqe_direct_wd4096_1536_w32", lambda r: r["client"]["achieved_mrps"])[0]
    ax.set_xticks(x_w)
    ax.set_xticklabels([f"W={w}" for w in ws], fontsize=9.5)
    ax.set_ylabel("端到端吞吐 (Mreq/s)", fontsize=9.8)
    ax.set_ylim(0, 2.35)
    ax.text(
        0.02, 0.96,
        "加固对照要点：\n"
        "• wd=4096 相对 --cqe-peek 12/12 组 min–max 零重叠全胜\n"
        "• 扣 --sync-reset 偏移后相对 naive 加固净成本仅 ~0.5% (~5 ns)\n"
        "• wd=4096 与 wd=0 全档差 < 0.5%（生产须补空轮询超时触发）",
        transform=ax.transAxes,
        ha="left", va="top",
        fontsize=7.8,
        color="#0f172a",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#f8fafc", edgecolor="#94a3b8", lw=1.0),
        zorder=6,
    )
    ax.set_title(
        f"D. 1.5 KiB (2×768B OP_DMA) 吞吐（W=8 +{100*(r15_w8_wd0/r15_w8_def-1):.1f}% vs def；+{100*(r15_w8_wd4k/r15_w8_pk-1):.1f}% · W=32 +{100*(r15_w32_wd4k/r15_w32_pk-1):.1f}% vs peek）",
        fontsize=9.2, fontweight="bold", pad=8,
    )
    out = mrvplot.save(fig, "mrv-170-perf-comparison")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
