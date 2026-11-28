#!/usr/bin/env python3
"""MRV-253 Fig 1: (Physical Core WCB, 64B line) budget law & full 142.9 Mpps IIO0 saturation."""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv253-2026-11-28/runs.jsonl"
OUT_PNG = Path(__file__).with_suffix(".png")
OUT_SVG = Path(__file__).with_suffix(".svg")
plt.rcParams["axes.unicode_minus"] = False


def load_medians():
    by_lbl = defaultdict(list)
    with RUNS.open() as f:
        for ln in f:
            r = json.loads(ln)
            by_lbl[r["label"]].append(r)
    med = {}
    for lbl, rows in by_lbl.items():
        r0 = rows[0]
        med[lbl] = {
            "victim_tag": r0["victim_tag"],
            "aggr": r0["aggr"],
            "topo": r0["topo"],
            "part": r0["partition_mode"],
            "k": r0["lines_per_core"],
            "sum_k": r0["budget_sum_k"],
            "n_thr": r0["aggr_n_threads"],
            "wcilf": statistics.median(x["cha"]["occ_entries"] for x in rows),
            "wcilf_max_box": statistics.median(x["cha"]["occ_max_box"] for x in rows),
            "iio_mpps": statistics.median(x["iio0"]["cpu_wr_mpps"] for x in rows),
            "aggr_mpps": statistics.median(x["aggr_mpps"] for x in rows),
        }
    return med


def main():
    med = load_medians()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.4), dpi=180, facecolor="white")
    fig.suptitle(
        "MRV-253：按核切分 4 KiB Portal 缓存行，以硬件预算律锁定队列占用并跑满 142.9 Mpps 线速",
        fontsize=13.5, fontweight="bold", y=0.98, color="#111827",
    )

    # --- Left Panel: CHA WCiLF vs Budget Σ_i K_i ---
    ax1.set_facecolor("white")
    ax1.axhspan(0, 200, color="#ECFDF5", alpha=0.85, zorder=0)
    ax1.axhspan(330, 860, color="#FEF2F2", alpha=0.85, zorder=0)
    ax1.axhline(200, color="#059669", ls="--", lw=1.2, alpha=0.8)
    ax1.axhline(330, color="#DC2626", ls="--", lw=1.2, alpha=0.8)
    ax1.text(10, 175, "安全区 (< 200 项，零邻居退化)", color="#047857", fontsize=9, fontweight="bold")
    ax1.text(10, 355, "悬崖坍塌区 (> 330 项，邻核延迟暴涨)", color="#B91C1C", fontsize=9, fontweight="bold")

    # Theoretical y = x bound up to 256
    xs_line = np.array([8, 16, 32, 64, 128, 256, 512])
    ax1.plot(xs_line, xs_line, color="#6B7280", ls=":", lw=1.4, label="理论预算上界 (WCiLF = Σ K)", zorder=2)

    style_map = {
        ("disjoint", 1): ("#2563EB", "o", "专属切分 K=1"),
        ("disjoint", 2): ("#059669", "s", "专属切分 K=2 (推荐)"),
        ("disjoint", 4): ("#0D9488", "^", "专属切分 K=4"),
        ("disjoint", 8): ("#D97706", "D", "专属切分 K=8"),
        ("shared", 64): ("#DC2626", "X", "共享 64 行 (基线坍塌)"),
    }

    # Plot llc-chase + none (covers 8c, 16c, 24c, 31c, 32c, 32t, 48t, 62t)
    plotted = set()
    for lbl, d in sorted(med.items()):
        if d["aggr"] != "noop-stride" or d["victim_tag"] not in ("llc", "none"):
            continue
        key = (d["part"], d["k"])
        if key not in style_map:
            continue
        color, marker, leg = style_map[key]
        ax1.scatter(
            d["sum_k"], d["wcilf"],
            c=color, marker=marker, s=68, edgecolors="white", linewidths=0.7,
            label=leg if key not in plotted else None, zorder=4,
        )
        plotted.add(key)

    ax1.set_xscale("log", base=2)
    ax1.set_xticks([8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096])
    ax1.set_xticklabels(["8", "16", "32", "64", "128", "256", "512", "1024", "2048", "4096"], fontsize=8.5)
    ax1.set_xlim(6.5, 4800)
    ax1.set_ylim(0, 820)
    ax1.set_xlabel("名义 (线程, 64B 行) 预算 Σ K（对数刻度；同核 SMT 撞行减半）", fontsize=10.0, fontweight="bold")
    ax1.set_ylabel("全片在途写入队列占用 CHA WCiLF（项）", fontsize=10.5, fontweight="bold")
    ax1.set_title("A. 硬件预算律：真实串行化键为 (物理核 WCB, 64B 行)", fontsize=11.2, fontweight="bold", pad=8)
    ax1.grid(True, which="major", ls="--", alpha=0.3)
    ax1.legend(loc="upper left", fontsize=8.5, framealpha=0.95, edgecolor="#D1D5DB")

    # Annotate the 3 SMT-sibling collision points (same physical core WCB merge)
    ax1.annotate(
        "3 处同核 SMT 撞行\n(32t K=4/8, 48t K=8)\nWCB 合并: 0.64–0.68×ΣK",
        xy=(256, 168.7), xytext=(480, 75),
        fontsize=7.8, color="#92400E", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", fc="#FFFBEB", ec="#D97706", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#D97706", lw=1.1, connectionstyle="arc3,rad=-0.15"),
        zorder=5,
    )
    # Annotate 62t K=8 early onset (WCiLF=307.5 < 330, max_box=15.3, +8.6% L3 p99)
    ax1.annotate(
        "62t K=8: WCiLF=307.5\nmax_box=15.3 (+8.6% L3 p99)",
        xy=(496, 307.5), xytext=(620, 225),
        fontsize=7.6, color="#9A3412", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.28", fc="#FFF7ED", ec="#EA580C", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#EA580C", lw=1.1, connectionstyle="arc3,rad=0.12"),
        zorder=5,
    )
    ax1.text(
        8.5, 430,
        "注：预算与背景 NT 写共享池（6 核 AVX2 NT 单独占 ~80 项）",
        fontsize=7.6, color="#374151",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#D1D5DB", alpha=0.94),
    )

    # --- Right Panel: IIO0 Mpps across core counts for K=1, K=2, K=4 vs shared-64 ---
    ax2.set_facecolor("white")
    topos = ["8c", "16c", "24c", "31c", "32c", "48t_24x2", "62t_31x2"]
    xlabels = ["8核", "16核", "24核", "31核", "32核\n(独享)", "48线程\n(24x2)", "62线程\n(31x2)"]
    x = np.arange(len(topos))
    w = 0.20

    series = [
        ("disjK1", "专属 K=1", "#2563EB"),
        ("disjK2", "专属 K=2", "#059669"),
        ("disjK4", "专属 K=4", "#0D9488"),
        ("shared64", "共享 64 行", "#DC2626"),
    ]

    for idx, (suf, name, col) in enumerate(series):
        vals = []
        for t in topos:
            vtag = "none" if t == "32c" else "llc"
            lbl = f"{vtag}__noop-stride_{suf}_{t}"
            vals.append(med[lbl]["iio_mpps"] if lbl in med else 0.0)
        bars = ax2.bar(x + (idx - 1.5) * w, vals, width=w, color=col, label=name, alpha=0.92, edgecolor="white", lw=0.5, zorder=3)
        if suf == "disjK2":
            for b, v in zip(bars, vals):
                if v > 135:
                    ax2.text(b.get_x() + b.get_width() / 2, v + 2.8, f"{v:.1f}", ha="center", va="bottom", fontsize=7.6, color=col, fontweight="bold")
        if suf == "disjK1":
            b0, v0 = bars[0], vals[0]
            ax2.text(b0.get_x() + b0.get_width() / 2 - 0.04, v0 + 2.8, f"{v0:.1f}", ha="center", va="bottom", fontsize=7.6, color=col, fontweight="bold")

    ax2.axhline(142.9, color="#111827", ls="--", lw=1.2, label="142.9 Mpps 硬件线速上限")
    ax2.set_xticks(x)
    ax2.set_xticklabels(xlabels, fontsize=9)
    ax2.set_ylim(0, 192)
    ax2.set_ylabel("DSA 端口实际吞吐 IIO0（Mpps）", fontsize=10.5, fontweight="bold")
    ax2.set_xlabel("攻击端核数 / SMT 拓扑", fontsize=10.5, fontweight="bold")
    ax2.set_title("B. 端口吞吐：K=2 (≥8核) / K=1 (≥16核) 跑满 100% 线速", fontsize=11.5, fontweight="bold", pad=8)
    ax2.grid(True, axis="y", ls="--", alpha=0.3)
    ax2.legend(loc="upper center", bbox_to_anchor=(0.5, 0.99), fontsize=8.2, framealpha=0.96, edgecolor="#D1D5DB", ncol=3)
    ax2.text(
        0.02, 0.04,
        "注：静态 K=2/4 在 1–4 活跃核偏斜突发下存在单核限速 (~10–11 Mpps/行)",
        transform=ax2.transAxes, fontsize=7.5, color="#374151",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#D1D5DB", alpha=0.92),
    )

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(OUT_PNG, dpi=180, facecolor="white")
    fig.savefig(OUT_SVG, facecolor="white")
    print(f"Saved {OUT_PNG} and {OUT_SVG}")


if __name__ == "__main__":
    main()
