#!/usr/bin/env python3
"""Plot MRV-259 Figure 1: KvStore DirectPush (Normal-WB) vs (MT_NORMAL_NC + u1_ldpq) vs SDP fair-full
across H=1, 2, 4 host cores and W=1 / saturation workloads.
"""

import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

RUNS_PATH = pathlib.Path(__file__).resolve().parents[2] / "logs" / "mrv259-2026-11-28" / "runs.jsonl"


def load_runs():
    rows = []
    with open(RUNS_PATH) as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def agg(rows, app, pol, wl, hc, kc, w_cond, metric_fn):
    vals = []
    for r in rows:
        if r["app"] == app and r["policy"] == pol and r["workload"] == wl and r["host_cores"] == hc and r["card_cores"] == kc:
            if w_cond(r["window"]):
                vals.append(metric_fn(r))
    if not vals:
        return None, 0.0, 0.0
    med = statistics.median(vals)
    return med, med - min(vals), max(vals) - med


def main():
    rows = load_runs()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.2))

    # Left: KvStore Saturation Throughput (H=1 workloads + H=2,4 scaling)
    groups = [
        ("get64\nH=1", "get64", 1),
        ("get64\nH=2", "get64", 2),
        ("get64\nH=4", "get64", 4),
        ("get256\nH=1", "get256", 1),
        ("get256\nH=4", "get256", 4),
        ("put64\nH=1", "put64", 1),
        ("mix\nH=1", "mix", 1),
        ("mix\nH=4", "mix", 4),
    ]
    policies = [
        ("direct_wb", "DirectPush (Normal-WB)", "#1f77b4"),
        ("direct_nc_u1q", "DirectPush (MT_NORMAL_NC + u1_ldpq)", "#d62728"),
        ("sdp_fair", "SDP fair-full", "#7f7f7f"),
    ]
    x = np.arange(len(groups))
    w = 0.26
    for pi, (pol, label, col) in enumerate(policies):
        ys, lo, hi = [], [], []
        for _, wl, hc in groups:
            m, l, h = agg(rows, "kv", pol, wl, hc, 4, lambda win: win > 1, lambda r: r["client"]["achieved_mrps"])
            ys.append(m if m is not None else 0.0)
            lo.append(l)
            hi.append(h)
        bars = ax1.bar(x + (pi - 1) * w, ys, w, yerr=[lo, hi], capsize=2.5, label=label, color=col, alpha=0.9)
        for b, val in zip(bars, ys):
            if val > 0:
                ax1.text(b.get_x() + b.get_width() / 2, val + 0.35, f"{val:.1f}", ha="center", va="bottom", fontsize=7.5)

    ax1.set_xticks(x)
    ax1.set_xticklabels([g[0] for g in groups], fontsize=8.5)
    ax1.set_ylabel("饱和吞吐 (Mrps)")
    ax1.set_title("KvStore 100GbE 饱和吞吐（K=4 卡核，H=1/2/4 主机核）")
    ax1.legend(fontsize=8, loc="upper right")
    ax1.set_ylim(0, max(22.0, ax1.get_ylim()[1] * 1.15))

    # Right: W=1 Low-Load RTT (p50)
    w1_groups = [
        ("get64\nW=1", "get64"),
        ("get256\nW=1", "get256"),
        ("put4096\nW=1", "put4096"),
    ]
    x2 = np.arange(len(w1_groups))
    for pi, (pol, label, col) in enumerate(policies):
        ys, lo, hi = [], [], []
        for _, wl in w1_groups:
            m, l, h = agg(rows, "kv", pol, wl, 1, 4, lambda win: win == 1, lambda r: r["client"]["tot_p50_us"])
            ys.append(m if m is not None else 0.0)
            lo.append(l)
            hi.append(h)
        bars = ax2.bar(x2 + (pi - 1) * w, ys, w, yerr=[lo, hi], capsize=2.5, label=label, color=col, alpha=0.9)
        for b, val in zip(bars, ys):
            if val > 0:
                ax2.text(b.get_x() + b.get_width() / 2, val + 0.25, f"{val:.2f}", ha="center", va="bottom", fontsize=8)

    ax2.set_xticks(x2)
    ax2.set_xticklabels([g[0] for g in w1_groups], fontsize=9)
    ax2.set_ylabel("端到端 W=1 p50 RTT (us)")
    ax2.set_title("KvStore 低载 W=1 延迟（H=1, K=4, n=3 中位数）")
    ax2.legend(fontsize=8, loc="upper left")
    ax2.set_ylim(0, ax2.get_ylim()[1] * 1.18)

    mrvplot.save(fig, "mrv-259-kvstore-nc-breakthrough")


if __name__ == "__main__":
    main()
