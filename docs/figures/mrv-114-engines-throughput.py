#!/usr/bin/env python3
"""MRV-114 Figure 1: 1 vs 2 vs 4 DSA engine scaling and per-engine silicon dispatch proof."""
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
    dsa_pmu = {}
    for r in rows:
        if r["mode"] != "rate":
            continue
        prof = r["pmu"]["profile"]
        if prof == "main" and r["batch_size"] == 1:
            main_rate[(r["comp_target"], r["op"], r["engines"])].append(r)
        elif prof == "dsa":
            dsa_pmu[(r["comp_target"], r["op"], r["batch_size"], r["engines"])] = r

    engines = [1, 2, 4]
    x = np.arange(len(engines))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.8), gridspec_kw={"width_ratios": [1.28, 1.0]})

    # --- Left panel: Throughput vs active engines ---
    bar4_meds = [statistics.median(r["gb_s"] for r in main_rate[("card_bar4", "memmove", e)]) for e in engines]
    host_meds = [statistics.median(r["gb_s"] for r in main_rate[("host_memzone", "memmove", e)]) for e in engines]
    b4_vals = [dsa_pmu[("card_bar4", "memmove", 4, e)]["gb_s"] for e in engines]

    ax1.axhline(bar4_meds[0] * 2.0, color="#c92a2a", linestyle=":", linewidth=1.3, alpha=0.65)
    ax1.text(-0.24, bar4_meds[0] * 2.0 + 1.8, "若单引擎排空预期 (~30.6 GB/s)", color="#c92a2a", fontsize=8.5, ha="left")

    ax1.plot(x, host_meds, marker="s", linewidth=2.2, markersize=7, color="#3b5bdb", label="HostMemzone B=1（主机内存完成）")
    ax1.plot(x, b4_vals, marker="D", linewidth=1.6, linestyle="--", markersize=6, color="#868e96", label="CardBar4 B=4（Batch 对照，n=1）")
    ax1.plot(x, bar4_meds, marker="o", linewidth=2.6, markersize=8, color="#d9480f", label="CardBar4 B=1（反向 P2P 完成）")

    # Overlay individual rep dots
    for i, e in enumerate(engines):
        for r in main_rate[("card_bar4", "memmove", e)]:
            ax1.scatter(i, r["gb_s"], color="#d9480f", s=22, alpha=0.45, zorder=4)
        for r in main_rate[("host_memzone", "memmove", e)]:
            ax1.scatter(i, r["gb_s"], color="#3b5bdb", s=22, alpha=0.45, zorder=4)

    for i, (b1, hm, b4) in enumerate(zip(bar4_meds, host_meds, b4_vals)):
        delta = "" if i == 0 else f" (+{(b1 / bar4_meds[0] - 1) * 100:.1f}%)"
        ax1.annotate(f"{b1:.2f} GB/s{delta}", (i, b1), textcoords="offset points", xytext=(0, 10),
                     ha="center", fontsize=8.6, fontweight="bold", color="#d9480f")
        if i < 2:
            ax1.annotate(f"{hm:.2f}", (i, hm), textcoords="offset points", xytext=(0, -16),
                         ha="center", fontsize=8.5, color="#3b5bdb")
        else:
            ax1.annotate(f"{hm:.2f}", (i, hm), textcoords="offset points", xytext=(0, 10),
                         ha="center", fontsize=8.5, color="#3b5bdb")
            ax1.annotate(f"{b4:.2f}", (i, b4), textcoords="offset points", xytext=(0, -16),
                         ha="center", fontsize=8.2, color="#6c757d")

    ax1.set_xticks(x)
    ax1.set_xticklabels(["1 引擎\n(engine2.0)", "2 引擎\n(engine2.0-1)", "4 引擎\n(engine2.0-3)"])
    ax1.set_xlim(-0.32, 2.32)
    ax1.set_ylabel("吞吐量 (GB/s, 10⁹ B/s)")
    ax1.set_ylim(0, 37.5)
    ax1.set_title("4 KiB MEMMOVE 吞吐随 DSA 引擎数变化 (W=64, CC=0)")
    ax1.legend(loc="lower center", fontsize=8.3, frameon=True, framealpha=0.95)

    # --- Right panel: Per-engine silicon dispatch (EV_NUM_DESC_DISPATCH_WQ) ---
    eng_colors = ["#d9480f", "#f76707", "#fd7e14", "#ffa94d"]
    bottoms = np.zeros(len(engines))
    for eng_idx in range(4):
        vals = []
        for e in engines:
            r = dsa_pmu[("card_bar4", "memmove", 1, e)]
            c = r["pmu"]["counters"][f"dsa_eng{eng_idx}_disp"]
            vals.append(c["val"] / (c["ns"] / 1e9) / 1e6)
        ax2.bar(x, vals, width=0.50, bottom=bottoms, color=eng_colors[eng_idx],
                edgecolor="white", linewidth=0.8, label=f"engine2.{eng_idx}")
        for i, v in enumerate(vals):
            if v > 0.4:
                ax2.text(i, bottoms[i] + v / 2.0, f"{v:.2f}", ha="center", va="center",
                         color="white", fontsize=8.5, fontweight="bold")
        bottoms += np.array(vals)

    for i, tot in enumerate(bottoms):
        ax2.text(i, tot + 0.12, f"合计 {tot:.2f} M/s", ha="center", va="bottom", fontsize=8.8, fontweight="bold", color="#212529")

    ax2.set_xticks(x)
    ax2.set_xticklabels(["1 引擎", "2 引擎", "4 引擎"])
    ax2.set_ylabel("各引擎硬件派发速率 (Mops/s)")
    ax2.set_ylim(0, 5.15)
    ax2.set_title("CardBar4 B=1：dsa2 硬件 PMU 各引擎均分派发")
    ax2.legend(loc="upper right", ncol=2, fontsize=8.1, frameon=True, framealpha=0.95)

    fig.suptitle("MRV-114：增加 DSA 引擎（1→2→4）均匀分摊派发，但 CardBar4 B=1 仍锁死 ~15.5 GB/s",
                 fontsize=11.5, fontweight="bold", y=0.98)
    out = mrvplot.save(fig, "mrv-114-engines-throughput")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
