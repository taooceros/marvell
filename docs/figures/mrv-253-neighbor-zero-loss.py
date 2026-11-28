#!/usr/bin/env python3
"""MRV-253 Fig 2: Zero net neighbor loss (STREAM WB/IMC/NT/RO + 16 MiB L3 p99) with disjoint partitioning."""
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
            "stream": statistics.median(x["stream"]["mean_gb_s"] for x in rows) if r0["stream"] else None,
            "imc": statistics.median(x["imc"]["tot_gb_s"] for x in rows),
            "p99": statistics.median(x["chase"]["p99_ns"] for x in rows) if r0["chase"] else None,
            "wcilf": statistics.median(x["cha"]["occ_entries"] for x in rows),
        }
    return med


def main():
    med = load_medians()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.4), dpi=180, facecolor="white")
    fig.suptitle(
        "MRV-253：专属缓存行切分（K=2 / K=4）消除邻核带宽坍塌与 L3 尾延迟，实现 ~0% 净邻居损失",
        fontsize=13.2, fontweight="bold", y=0.98, color="#111827",
    )

    # --- Left Panel: 6c STREAM retention (% of same-core l1-spin control) ---
    ax1.set_facecolor("white")
    groups = [
        ("t6c", "16c", "16核\nWB 逻辑"),
        ("t6c_imc", "16c", "16核\nDDR5 物理"),
        ("t6c", "24c", "24核\nWB 逻辑"),
        ("t6c_imc", "24c", "24核\nDDR5 物理"),
        ("t6c_nt", "24c", "24核\nNT 写对照"),
        ("t6c_ro", "24c", "24核\n纯读对照"),
        ("t6c", "48t_24x2", "48线程\nWB 逻辑"),
    ]
    x = np.arange(len(groups))
    w = 0.25

    def get_val(kind, topo, arm_suf):
        if kind == "t6c_imc":
            lbl = f"t6c__{arm_suf}_{topo}"
            return med[lbl]["imc"]
        lbl = f"{kind}__{arm_suf}_{topo}"
        return med[lbl]["stream"]

    for idx, (arm_suf, name, col) in enumerate([
        ("noop-stride_shared64", "共享 64 行 (基线)", "#DC2626"),
        ("noop-stride_disjK2", "专属切分 K=2", "#059669"),
        ("noop-stride_disjK4", "专属切分 K=4", "#0D9488"),
    ]):
        pcts = []
        for kind, topo, _ in groups:
            base = get_val(kind, topo, "l1-spin")
            val = get_val(kind, topo, arm_suf)
            pcts.append(100.0 * val / base if base > 0 else 0.0)
        bars = ax1.bar(x + (idx - 1) * w, pcts, width=w, color=col, label=name, alpha=0.92, edgecolor="white", lw=0.5, zorder=3)
        for b, v in zip(bars, pcts):
            y_off = 1.6 if idx != 1 else 5.2
            ax1.text(b.get_x() + b.get_width() / 2, v + y_off, f"{v:.0f}%", ha="center", va="bottom", fontsize=7.2, color=col, fontweight="bold")

    ax1.axhline(100.0, color="#374151", ls="--", lw=1.2, label="同核 l1-spin 控温基线 (100%)")
    ax1.set_xticks(x)
    ax1.set_xticklabels([g[2] for g in groups], fontsize=8.8)
    ax1.set_ylim(0, 118)
    ax1.set_ylabel("相对同核 l1-spin 基线的带宽保留率（%）", fontsize=10.5, fontweight="bold")
    ax1.set_title("A. 6 核 STREAM 带宽保留（含 DDR5 物理带宽与 NT/纯读对照）", fontsize=11.0, fontweight="bold", pad=8)
    ax1.grid(True, axis="y", ls="--", alpha=0.3)
    ax1.legend(loc="center right", bbox_to_anchor=(0.99, 0.52), fontsize=8.5, framealpha=0.96, edgecolor="#D1D5DB")

    # --- Right Panel: 16 MiB L3 chase p99 latency (ns, log scale) ---
    ax2.set_facecolor("white")
    topos_b = ["16c", "24c", "31c", "48t_24x2", "62t_31x2"]
    xlabels_b = ["16核", "24核", "31核", "48线程\n(24x2)", "62线程\n(31x2)"]
    xb = np.arange(len(topos_b))
    wb = 0.20

    for idx, (arm_suf, name, col) in enumerate([
        ("l1-spin", "l1-spin 控温基线", "#6B7280"),
        ("noop-stride_disjK2", "专属切分 K=2", "#059669"),
        ("noop-stride_disjK4", "专属切分 K=4", "#0D9488"),
        ("noop-stride_shared64", "共享 64 行 (基线)", "#DC2626"),
    ]):
        vals = [med[f"llc__{arm_suf}_{t}"]["p99"] for t in topos_b]
        bars = ax2.bar(xb + (idx - 1.5) * wb, vals, width=wb, color=col, label=name, alpha=0.92, edgecolor="white", lw=0.5, zorder=3)
        for b, v in zip(bars, vals):
            txt = f"{v:.0f}" if v >= 100 else f"{v:.1f}"
            mult = 1.18 if idx == 1 else 1.05
            ax2.text(b.get_x() + b.get_width() / 2, v * mult, txt, ha="center", va="bottom", fontsize=6.8, color=col, fontweight="bold")

    ax2.set_yscale("log")
    ax2.set_xticks(xb)
    ax2.set_xticklabels(xlabels_b, fontsize=9.2)
    ax2.set_ylim(10, 8000)
    ax2.set_ylabel("16 MiB L3 指针追逐 p99 延迟（ns，对数轴）", fontsize=10.5, fontweight="bold")
    ax2.set_xlabel("攻击端核数 / SMT 拓扑", fontsize=10.5, fontweight="bold")
    ax2.set_title("B. 16 MiB L3 邻居尾延迟：专属切分几乎完全贴合控温基线", fontsize=11.0, fontweight="bold", pad=8)
    ax2.grid(True, axis="y", which="major", ls="--", alpha=0.3)
    ax2.legend(loc="upper left", fontsize=8.5, framealpha=0.95, edgecolor="#D1D5DB")

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(OUT_PNG, dpi=180, facecolor="white")
    fig.savefig(OUT_SVG, facecolor="white")
    print(f"Saved {OUT_PNG} and {OUT_SVG}")


if __name__ == "__main__":
    main()
