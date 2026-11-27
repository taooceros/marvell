#!/usr/bin/env python3
"""MRV-236 Figure 1: Group A single-variable comparison (WC2 W=64) + Group L W=1 RTT cost.

Reads raw logs/mrv236-2026-11-27/runs.jsonl, verifies lost=bad=torn=corrupt=0 and
100% non-multiplexed PMU for every plotted run, and saves docs/figures/mrv-236-group-a.{png,svg}.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv236-2026-11-27/runs.jsonl"


def load_verified_runs() -> dict[str, list[dict]]:
    by_arm: dict[str, list[dict]] = {}
    for line in RUNS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("group") == "idle":
            continue
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0, f"Integrity fail: {r['arm']}"
        cp = r["card_pmu"]
        hp = r["host_pmu"]
        assert all(c["corrupt"] == 0 for c in cp["cores"]), f"Corrupt payload: {r['arm']}"
        assert hp["min_pct"] >= 99.9 and cp["min_pct"] >= 99.9, f"PMU mux: {r['arm']}"
        by_arm.setdefault(r["arm"], []).append(r)
    return by_arm


def arm_metrics(recs: list[dict]) -> dict[str, float | list[float]]:
    mops = [r["mops"] for r in recs]
    rtt50 = [r["rtt_p50_us"] for r in recs]
    snp, inval_tot, l2_tot = [], [], []
    for r in recs:
        tot = r["rounds"] + r["warmup"]
        cp = r["card_pmu"]
        snp.append(cp["tad"]["tad_snp_msh_out_any"]["val"] / tot)
        inval_tot.append(sum(c.get("l2d_cache_inval", 0) for c in cp["cores"]) / tot)
        l2_tot.append(sum(c["l2d_cache_refill"] for c in cp["cores"]) / tot)
    return {
        "mops_med": statistics.median(mops),
        "mops_all": mops,
        "rtt50_med": statistics.median(rtt50),
        "snp_med": statistics.median(snp),
        "inval_med": statistics.median(inval_tot),
        "l2_med": statistics.median(l2_tot),
    }


def main() -> None:
    by_arm = load_verified_runs()
    order = ["B", "A1", "A2", "A3", "A4", "WC4_B"]
    labels = [
        "B 基线\n(WC2 ldar\n每批写next)",
        "A1 退出写next\n(--next-publish\nexit)",
        "A2 普通读\n(--poll\nldr)",
        "A3 睡等唤醒\n(--poll\nwfe)",
        "A4 空轮询逐出\n(--poll\ncivac)",
        "WC4 对照\n(4核 ldar\nMRV-75对齐)",
    ]
    l_map = {"B": "L_B", "A2": "L_A2", "A3": "L_A3", "A4": "L_A4"}

    stats = {k: arm_metrics(by_arm[k]) for k in order}
    l_stats = {k: arm_metrics(by_arm[v]) for k, v in l_map.items() if v in by_arm}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.2, 5.4), gridspec_kw={"width_ratios": [1.08, 1.02]})
    x = np.arange(len(order))

    colors_mops = ["#4B5563", "#2563EB", "#6B7280", "#D97706", "#DC2626", "#9CA3AF"]
    mops_vals = [stats[k]["mops_med"] for k in order]
    ax1.bar(x, mops_vals, width=0.55, color=colors_mops, edgecolor="#1F2937", linewidth=0.7, zorder=3)
    for i, k in enumerate(order):
        ys = stats[k]["mops_all"]
        xs = np.linspace(i - 0.12, i + 0.12, len(ys))
        ax1.scatter(xs, ys, color="#111827", s=18, zorder=4)
        txt = f"{stats[k]['mops_med']:.2f}"
        if k in l_stats:
            txt += f"\nW1:{l_stats[k]['rtt50_med']:.2f}µs"
        ax1.text(i, max(ys) + 0.75, txt, ha="center", va="bottom", fontsize=7.9, color="#111827", fontweight="bold")

    ax1.axhline(25.0, color="#059669", linestyle="--", linewidth=1.2, zorder=2)
    ax1.text(len(order) - 0.52, 25.55, "跳出判定线 25 M/s", ha="right", va="bottom", fontsize=8.5, color="#059669", fontweight="bold")
    ax1.axhline(13.1, color="#6B7280", linestyle=":", linewidth=1.0, zorder=2)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=8.2)
    ax1.set_ylabel("闭环吞吐 (Mmsg/s, 64 B)", fontsize=10)
    ax1.set_ylim(0, 29.5)
    ax1.set_title("A 组吞吐：去 next 共享 / 改轮询方式均未跳出 13.1 M/s", fontsize=10.4, pad=8)

    w = 0.35
    snp_vals = [stats[k]["snp_med"] for k in order]
    inv_vals = [stats[k]["inval_med"] for k in order]
    ax2.bar(x - w / 2, snp_vals, width=w, color="#1D4ED8", edgecolor="#1E3A8A", linewidth=0.7, label="TAD 嗅探 / 条 (tad_snp_msh_out_any)", zorder=3)
    ax2.bar(x + w / 2, inv_vals, width=w, color="#F59E0B", edgecolor="#92400E", linewidth=0.7, label="环核 L2 作废之和 / 条 (L2D_CACHE_INVAL 0x58)", zorder=3)
    for i, k in enumerate(order):
        ax2.text(i - w / 2, snp_vals[i] + 0.09, f"{snp_vals[i]:.2f}", ha="center", va="bottom", fontsize=8.0, color="#1E3A8A", fontweight="bold")
        ax2.text(i + w / 2, inv_vals[i] + 0.09, f"{inv_vals[i]:.2f}", ha="center", va="bottom", fontsize=8.0, color="#92400E", fontweight="bold")

    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, fontsize=8.2)
    ax2.set_ylabel("每条消息次数 (次 / 条)", fontsize=10)
    ax2.set_ylim(0, max(snp_vals) * 1.25)
    ax2.set_title("A 组一致性开销：A1 减约 0.9 次嗅探仍串行，A3/A4 未消作废", fontsize=10.4, pad=8)
    ax2.legend(loc="upper left", fontsize=8.2, frameon=True, facecolor="white", framealpha=0.92)

    fig.suptitle(
        "MRV-236 A 组单变量对照（WC2, 64 B, batch=1, W=64, MOVDIR64B+WC, 主机 3.90 GHz / uncore 2.20 GHz / 卡 2.00 GHz, n=3）",
        fontsize=10.6,
        fontweight="bold",
        y=0.98,
    )
    out = save(fig, "mrv-236-group-a")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
