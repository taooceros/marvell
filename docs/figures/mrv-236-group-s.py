#!/usr/bin/env python3
"""MRV-236 Figure 2: Group S single-variable comparison (WC1 W=64, next_publish=exit, unstamped steady state).

Reads raw logs/mrv236-2026-11-27/runs.jsonl, verifies lost=bad=torn=corrupt=0 and
100% non-multiplexed PMU for every plotted run, and saves docs/figures/mrv-236-group-s.{png,svg}.
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
        if r.get("group") != "S":
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
    snp, ring_inv, shad_inv = [], [], []
    for r in recs:
        tot = r["rounds"] + r["warmup"]
        cp = r["card_pmu"]
        snp.append(cp["tad"]["tad_snp_msh_out_any"]["val"] / tot)
        ring_inv.append(sum(c.get("l2d_cache_inval", 0) for c in cp["cores"]) / tot)
        shad_inv.append(sum(s.get("l2d_cache_inval", 0) for s in cp.get("shadows", [])) / tot)
    return {
        "mops_med": statistics.median(mops),
        "mops_all": mops,
        "rtt50_med": statistics.median(rtt50),
        "snp_med": statistics.median(snp),
        "ring_inv_med": statistics.median(ring_inv),
        "shad_inv_med": statistics.median(shad_inv),
    }


def main() -> None:
    by_arm = load_verified_runs()
    order = ["B1", "S1", "S2", "S3"]
    labels = [
        "B1 单核基线\n(WC1 稳态\n仅核3)",
        "S1 影子守私有行\n(读SHADOW_NEXT\n自旋私有128 B)",
        "S2 影子守前沿格\n(自旋环格\nfront+0)",
        "S3 影子守前沿+4\n(自旋环格\nfront+4)",
    ]
    stats = {k: arm_metrics(by_arm[k]) for k in order}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.8, 5.4), gridspec_kw={"width_ratios": [1.0, 1.08]})
    x = np.arange(len(order))

    colors = ["#059669", "#10B981", "#DC2626", "#B91C1C"]
    mops_vals = [stats[k]["mops_med"] for k in order]
    ax1.bar(x, mops_vals, width=0.52, color=colors, edgecolor="#1F2937", linewidth=0.7, zorder=3)
    for i, k in enumerate(order):
        ys = stats[k]["mops_all"]
        xs = np.linspace(i - 0.12, i + 0.12, len(ys))
        ax1.scatter(xs, ys, color="#111827", s=20, zorder=4)
        ax1.text(
            i,
            max(ys) + 1.1,
            f"{stats[k]['mops_med']:.2f} M/s\n(RTT {stats[k]['rtt50_med']:.2f} µs)",
            ha="center",
            va="bottom",
            fontsize=8.3,
            color="#111827",
            fontweight="bold",
        )

    ax1.axhline(13.1, color="#6B7280", linestyle="--", linewidth=1.1, zorder=2)
    ax1.annotate(
        "WC2/WC4 串行线 13.1 M/s",
        xy=(2.5, 13.1),
        xytext=(2.5, 22.8),
        ha="center",
        va="bottom",
        fontsize=8.3,
        color="#4B5563",
        arrowprops={"arrowstyle": "->", "color": "#6B7280", "lw": 0.9},
    )
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=8.5)
    ax1.set_ylabel("闭环吞吐 (Mmsg/s, 64 B)", fontsize=10)
    ax1.set_ylim(0, max(mops_vals) * 1.24)
    ax1.set_title("S 组吞吐：影子核守住环格即把 35 M/s 拉跌至 ~12.6 M/s", fontsize=10.4, pad=8)

    w = 0.34
    snp_vals = [stats[k]["snp_med"] for k in order]
    rinv_vals = [stats[k]["ring_inv_med"] for k in order]
    sinv_vals = [stats[k]["shad_inv_med"] for k in order]

    ax2.bar(x - w / 2 - 0.02, snp_vals, width=w, color="#1D4ED8", edgecolor="#1E3A8A", linewidth=0.7, label="TAD 嗅探 / 条 (tad_snp_msh_out_any)", zorder=3)
    ax2.bar(x + w / 2 + 0.02, rinv_vals, width=w, color="#F59E0B", edgecolor="#92400E", linewidth=0.7, label="环核 3 L2 作废 / 条 (0x58)", zorder=3)
    ax2.bar(
        x + w / 2 + 0.02,
        sinv_vals,
        width=w,
        bottom=rinv_vals,
        color="#EF4444",
        edgecolor="#7F1D1D",
        linewidth=0.7,
        label="影子核 4 L2 作废 / 条 (0x58)",
        zorder=3,
    )
    for i, k in enumerate(order):
        ax2.text(i - w / 2 - 0.02, snp_vals[i] + 0.09, f"{snp_vals[i]:.2f}", ha="center", va="bottom", fontsize=8.2, color="#1E3A8A", fontweight="bold")
        r_r, s_r = round(rinv_vals[i], 2), round(sinv_vals[i], 2)
        tot_inv = rinv_vals[i] + sinv_vals[i]
        lbl = f"{r_r + s_r:.2f}" if s_r < 0.1 else f"{r_r + s_r:.2f}\n({r_r:.2f}+{s_r:.2f})"
        ax2.text(i + w / 2 + 0.04, tot_inv + 0.09, lbl, ha="center", va="bottom", fontsize=7.3, color="#7F1D1D", fontweight="bold")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, fontsize=8.5)
    ax2.set_ylabel("每条消息次数 (次 / 条)", fontsize=10)
    ax2.set_ylim(0, max(snp_vals) * 1.26)
    ax2.set_title("S 组嗅探与分核 L2 作废：S2/S3 影子核吃下 ~1.3–1.4 次/条", fontsize=10.4, pad=8)
    ax2.legend(loc="upper left", fontsize=8.2, frameon=True, facecolor="white", framealpha=0.92)

    fig.suptitle(
        "MRV-236 S 组单变量对照（WC1, next_publish=exit, 64 B, batch=1, W=64, 稳态无戳, 主机 3.90 GHz / 卡 2.00 GHz, n=3）",
        fontsize=10.6,
        fontweight="bold",
        y=0.98,
    )
    out = save(fig, "mrv-236-group-s")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
