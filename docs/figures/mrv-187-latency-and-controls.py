#!/usr/bin/env python3
"""MRV-187 Figure 2: W=1 closed-loop latency (cross-checked wall ~= mean) and
single-variable mechanism controls (1/2/4 DSA engines, Batch B=1 vs B=8),
plotted strictly from logs/mrv187-2026-11-25/runs.jsonl (n=3 interleaved medians).
"""
import json
import pathlib
import statistics
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv187-2026-11-25/runs.jsonl"

SIZES = [64, 128, 256, 512, 1024, 2048, 4096, 16384, 65536]
SIZE_LABELS = ["64 B", "128 B", "256 B", "512 B", "1 KiB", "2 KiB", "4 KiB", "16 KiB", "64 KiB"]
X = np.arange(len(SIZES))


def load_runs():
    by_key = defaultdict(list)
    for line in RUNS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r["err_ops"] == 0 and r["verify"]["bad_bytes"] == 0 and r["host_pmu_min_pct"] == 100.0
        by_key[(r["suite"], r["label"])].append(r)
    return by_key


def med(vals):
    return statistics.median(vals)


def main():
    by_key = load_runs()

    # Panel A: Cross-checked W=1 latency (wall_ns - mean_ns = +16 ns on every point)
    dsa_bar4_p50 = [med([r["lat_ns"]["p50"] for r in by_key[("lat", f"dsa_bar4_{s}B")]]) for s in SIZES]
    dsa_host_p50 = [med([r["lat_ns"]["p50"] for r in by_key[("lat", f"dsa_host_{s}B")]]) for s in SIZES]

    rbf_sizes = [64, 4096, 65536]
    rbf_x = [SIZES.index(s) for s in rbf_sizes]
    dsa_rbf_p50 = [med([r["lat_ns"]["p50"] for r in by_key[("lat", f"dsa_bar4_rbf_{s}B")]]) for s in rbf_sizes]
    mov_rbf_p50 = [med([r["lat_ns"]["p50"] for r in by_key[("lat", f"movdir_bar4_rbf_{s}B")]]) for s in rbf_sizes]

    # Panel B: Single-variable controls (1/2/4 engines, B=1 vs B=8) at key sizes
    ctrl_sizes = [64, 256, 512, 4096]
    ctrl_labels = ["64 B", "256 B", "512 B", "4 KiB"]
    e1_b1 = [med([r["gb_s"] for r in by_key[("rate_size", f"dsa_bar4_uc_{s}B_w32")]]) for s in ctrl_sizes]
    e2_b1 = [med([r["gb_s"] for r in by_key[("engines", f"dsa_bar4_e2_{s}B_b1_w32")]]) for s in ctrl_sizes]
    e4_b1 = [med([r["gb_s"] for r in by_key[("engines", f"dsa_bar4_e4_{s}B_b1_w32")]]) for s in ctrl_sizes]
    e1_b8 = [med([r["gb_s"] for r in by_key[("batch_comp", f"dsa_bar4_{s}B_b8_w4")]]) for s in ctrl_sizes]
    e4_b8 = [med([r["gb_s"] for r in by_key[("engines", f"dsa_bar4_e4_{s}B_b8_w4")]]) for s in ctrl_sizes]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.8, 5.4))

    # ---- Left panel: W=1 Closed-Loop Latency ----
    ax1.plot(X, dsa_bar4_p50, "-", color="#0f62fe", marker="o", ms=6.2, lw=2.3, label="DSA → BAR4（主机完成 p50）")
    ax1.plot(X, dsa_host_p50, "--", color="#64748b", marker="s", ms=5.5, lw=1.8, label="DSA → 主机 RAM 对照（p50）")
    ax1.plot(rbf_x, dsa_rbf_p50, ":", color="#dc2626", marker="D", ms=6.5, lw=1.9, label="DSA → BAR4 + 8 B 读回确认（p50）")
    ax1.plot(rbf_x, mov_rbf_p50, "-.", color="#0d9488", marker="^", ms=6.5, lw=1.9, label="MOVDIR64B → BAR4 + 8 B 读回确认（p50）")

    ax1.set_yscale("log")
    ax1.set_xticks(X)
    ax1.set_xticklabels(SIZE_LABELS, fontsize=8.8)
    ax1.set_ylabel("单笔 W=1 中位延迟 p50（ns，对数刻度）", fontsize=10)
    ax1.set_xlabel("单笔传输尺寸", fontsize=10)
    ax1.set_ylim(380, 14500)

    ax1.annotate(
        f"≤ 4 KiB 投递写绕过 CHA 一致性\nBAR4 反快 {dsa_host_p50[0]-dsa_bar4_p50[0]:.0f} ns（{dsa_bar4_p50[0]:.0f} vs {dsa_host_p50[0]:.0f}）",
        xy=(0, dsa_bar4_p50[0]),
        xytext=(0.75, 420),
        fontsize=8.0,
        color="#0f62fe",
        bbox=dict(boxstyle="round,pad=0.25", fc="#eff6ff", ec="#93c5fd"),
        arrowprops=dict(arrowstyle="->", color="#0f62fe", lw=1.1),
    )
    ax1.annotate(
        f"64 KiB 读回确认：DSA {dsa_rbf_p50[-1]:.0f} ns\n仅比 MOVDIR64B（{mov_rbf_p50[-1]:.0f}）多 +{dsa_rbf_p50[-1]-mov_rbf_p50[-1]:.0f} ns",
        xy=(8, dsa_rbf_p50[-1]),
        xytext=(2.35, 2450),
        fontsize=8.1,
        color="#b91c1c",
        bbox=dict(boxstyle="round,pad=0.26", fc="#fef2f2", ec="#fca5a5"),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.15),
    )

    ax1.set_title("A. 单笔 W=1 延迟（壁钟交叉核验全通过）", fontsize=10.8, fontweight="bold", pad=8)
    ax1.legend(loc="upper left", fontsize=7.8, framealpha=0.96)

    # ---- Right panel: Engines & Batch Controls ----
    gx = np.arange(len(ctrl_sizes))
    w = 0.155
    b1 = ax2.bar(gx - 2 * w, e1_b1, width=w, color="#93c5fd", edgecolor="#2563eb", lw=0.8, label="1 引擎 · B=1 (W=32)")
    ax2.bar(gx - 1 * w, e2_b1, width=w, color="#60a5fa", edgecolor="#1d4ed8", lw=0.8, label="2 引擎 · B=1 (W=32)")
    ax2.bar(gx + 0 * w, e4_b1, width=w, color="#2563eb", edgecolor="#1e3a8a", lw=0.8, label="4 引擎 · B=1 (W=32)")
    b4 = ax2.bar(gx + 1 * w, e1_b8, width=w, color="#5eead4", edgecolor="#0f766e", lw=0.8, label="1 引擎 · Batch B=8")
    b5 = ax2.bar(gx + 2 * w, e4_b8, width=w, color="#0d9488", edgecolor="#115e59", lw=0.8, label="4 引擎 · Batch B=8")

    ax2.axhline(11.22, color="#dc2626", ls="--", lw=1.3, alpha=0.85)
    ax2.text(0.02, 11.42, "64 B TLP 实测线速顶 11.22 GB/s（1/2/4 引擎均无法突破）", fontsize=8.2, color="#dc2626", fontweight="bold")

    # Clean non-overlapping value labels
    for i in range(3):
        ax2.text(gx[i] - w, e1_b1[i] + 0.22, f"{e1_b1[i]:.2f}", ha="center", va="bottom", fontsize=7.6, color="#1d4ed8", fontweight="bold")
        ax2.text(gx[i] + 1 * w, e1_b8[i] + 0.22, f"{e1_b8[i]:.2f}", ha="center", va="bottom", fontsize=7.4, color="#0f766e", fontweight="bold")
        if abs(e4_b8[i] - e1_b8[i]) >= 0.4 or i == 2:
            ax2.text(gx[i] + 2.15 * w, e4_b8[i] + 0.22, f"{e4_b8[i]:.2f}", ha="center", va="bottom", fontsize=7.4, color="#115e59", fontweight="bold")
    # Single clean centered label over the 5 identical 4 KiB bars
    ax2.text(gx[3], 11.42, "全组 11.22", ha="center", va="bottom", fontsize=8.2, color="#0f172a", fontweight="bold")

    ax2.set_xticks(gx)
    ax2.set_xticklabels(ctrl_labels, fontsize=9.2)
    ax2.set_ylabel("有效载荷带宽（GB/s）", fontsize=10)
    ax2.set_xlabel("单笔 MEMMOVE 传输尺寸", fontsize=10)
    ax2.set_ylim(0, 14.8)
    ax2.set_title("B. 单变量机制对照：多引擎扩容 vs Batch 聚合", fontsize=10.8, fontweight="bold", pad=8)
    ax2.legend(loc="upper left", ncol=2, fontsize=7.6, framealpha=0.96)

    fig.suptitle(
        "MRV-187：小包 B=1 卡在单队列 9.85 M/s（Batch B=8 可提速 2.74×），大包 ≥ 4 KiB 锁死 PCIe 64 B TLP 线速",
        fontsize=11.2,
        fontweight="bold",
    )
    out = save(fig, "mrv-187-latency-and-controls")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
