#!/usr/bin/env python3
"""MRV-114 Figure 2: Reverse-P2P NOP ceiling (5.2x) vs MEMMOVE and Socket-1 IIO/IRP queue occupancy."""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv114-2026-11-19/runs.jsonl"


def main():
    rows = [json.loads(line) for line in RUNS.read_text().splitlines() if line.strip()]
    for r in rows:
        assert r.get("lost", 0) == 0 and r.get("bad", 0) == 0
        assert r.get("dyn_lost", 0) == 0 and r.get("dyn_bad", 0) == 0

    main_rate = defaultdict(list)
    for r in rows:
        if r["mode"] == "rate" and r["pmu"]["profile"] == "main" and r["batch_size"] == 1:
            main_rate[(r["comp_target"], r["op"], r["engines"])].append(r)

    engines = [1, 2, 4]
    x = np.arange(len(engines))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.8), gridspec_kw={"width_ratios": [1.05, 1.2]})

    # --- Left panel: Reverse-P2P completion write rate (NOP vs 4 KiB MEMMOVE) ---
    nop_rates = [statistics.median(r["mops_s"] for r in main_rate[("card_bar4", "nop", e)]) for e in engines]
    mem_rates = [statistics.median(r["mops_s"] for r in main_rate[("card_bar4", "memmove", e)]) for e in engines]

    w = 0.34
    b_nop = ax1.bar(x - w / 2, nop_rates, width=w, color="#495057", label="CardBar4 B=1 NOP（零数据搬运）")
    b_mem = ax1.bar(x + w / 2, mem_rates, width=w, color="#d9480f", label="CardBar4 B=1 4 KiB MEMMOVE")

    for rect, v in zip(b_nop, nop_rates):
        ax1.text(rect.get_x() + rect.get_width() / 2, v + 0.45, f"{v:.2f}", ha="center", va="bottom",
                 fontsize=8.6, fontweight="bold", color="#343a40")
    for rect, v in zip(b_mem, mem_rates):
        ax1.text(rect.get_x() + rect.get_width() / 2, v + 0.45, f"{v:.2f}", ha="center", va="bottom",
                 fontsize=8.6, fontweight="bold", color="#d9480f")

    ax1.annotate("同一反向 P2P 通道\n可达 5.2×（51.4 ns/笔）", xy=(1 - w / 2 + 0.14, 15.8), xytext=(1.42, 11.2),
                 arrowprops=dict(arrowstyle="->", color="#495057", lw=1.2),
                 fontsize=8.5, color="#212529", ha="center",
                 bbox=dict(boxstyle="round,pad=0.3", fc="#f8f9fa", ec="#adb5bd"))

    ax1.set_xticks(x)
    ax1.set_xticklabels(["1 引擎", "2 引擎", "4 引擎"])
    ax1.set_ylabel("反向 P2P 完成写速率 (Mops/s)")
    ax1.set_ylim(0, 26.8)
    ax1.set_title("同一反向 P2P 通道：NOP vs 4 KiB MEMMOVE")
    ax1.legend(loc="upper right", fontsize=8.2, frameon=True, framealpha=0.95)

    # --- Right panel: Socket-1 dual-stack IRP queue occupancy (2 engines; 1/4 engines identical) ---
    conds = [
        ("CardBar4 MEMMOVE", ("card_bar4", "memmove", 2), "#d9480f"),
        ("HostMemzone MEMMOVE", ("host_memzone", "memmove", 2), "#3b5bdb"),
        ("CardBar4 NOP", ("card_bar4", "nop", 2), "#495057"),
    ]
    metrics = [
        ("dsa_irp_0\nFAF 占用", "dsa_irp_faf_occ", "dsa_iio_clk"),
        ("dsa_irp_0\nTxS 占用", "dsa_irp_txs_occ", "dsa_iio_clk"),
        ("card_irp_1\nTxS 占用", "card_irp_txs_occ", "card_iio_clk"),
    ]
    mx = np.arange(len(metrics))
    bw = 0.24
    for ci, (label, key, color) in enumerate(conds):
        rs = main_rate[key]
        vals = [
            statistics.median(r["pmu"]["counters"][cn]["val"] / r["pmu"]["counters"][clk]["val"] for r in rs)
            for _, cn, clk in metrics
        ]
        bars = ax2.bar(mx + (ci - 1) * bw, vals, width=bw, color=color, label=label)
        for rect, v in zip(bars, vals):
            ax2.text(rect.get_x() + rect.get_width() / 2, v + 0.015, f"{v:.2f}",
                     ha="center", va="bottom", fontsize=8.0, color=color, fontweight="bold")

    ax2.set_xticks(mx)
    ax2.set_xticklabels([m[0] for m in metrics])
    ax2.set_ylabel("平均队列占用（条 / IIO 周期）")
    ax2.set_ylim(0, 0.72)
    ax2.set_title("Socket 1 双栈 IRP 队列占用（2 引擎；M2PCIe NCB 拒绝 = 0）")
    ax2.legend(loc="upper right", fontsize=8.0, frameon=True, framealpha=0.95)
    ax2.text(0.03, 0.90, "CardBar4 MEMMOVE\n双栈 IRP 均 ≤ 0.24",
             transform=ax2.transAxes, ha="left", va="top", fontsize=8.3, color="#d9480f",
             bbox=dict(boxstyle="round,pad=0.3", fc="#fff4e6", ec="#ffa94d"))

    fig.suptitle("MRV-114：反向 P2P 通道未饱和（NOP 达 19.44 Mops/s，MEMMOVE 时双栈 IRP 均极空）",
                 fontsize=11.5, fontweight="bold", y=0.98)
    out = mrvplot.save(fig, "mrv-114-pmu-queues")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
