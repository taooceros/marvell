#!/usr/bin/env python3
"""Plot MRV-259 Figure 2: Left = Card TAD PMU (EXLMN/RPC & SNP/RPC) for WB vs NC;
Right = Zero-mod Upstream Memcached 1.6.45 (H=1 & H=4) A/B Throughput & p50/p99 RTT.
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


def get_tad_per_rpc(r, key):
    c = r["card"]
    tad = c.get("tad_pmu") or c.get("tad") or {}
    rpcs = max(c.get("rpcs_out", 1), 1)
    return tad.get(key, 0) / rpcs


def main():
    rows = load_runs()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.2))

    # Left: Card-measured host_rtt_p50_us on KvStore get64 (H=1,2,4) & Memcached get8 (H=1,4)
    pmu_groups = [
        ("KV get64\nH=1", "kv", "get64", 1),
        ("KV get64\nH=2", "kv", "get64", 2),
        ("KV get64\nH=4", "kv", "get64", 4),
        ("MC get8\nH=1", "mc", "get8", 1),
        ("MC get8\nH=4", "mc", "get8", 4),
    ]
    x = np.arange(len(pmu_groups))
    w = 0.34
    for pi, (pol, label, col) in enumerate([
        ("direct_wb", "DirectPush (Normal-WB)", "#1f77b4"),
        ("direct_nc_u1q", "DirectPush (MT_NORMAL_NC + u1_ldpq)", "#d62728"),
    ]):
        ys, lo, hi = [], [], []
        for _, app, wl, hc in pmu_groups:
            m, l, h = agg(rows, app, pol, wl, hc, 4, lambda win: win > 1, lambda r: r["card"].get("host_rtt_p50_us", 0.0))
            ys.append(m if m is not None else 0.0)
            lo.append(l)
            hi.append(h)
        bars = ax1.bar(x + (pi - 0.5) * w, ys, w, yerr=[lo, hi], capsize=2.5, label=label, color=col, alpha=0.9)
        for b, val in zip(bars, ys):
            ax1.text(b.get_x() + b.get_width() / 2, val + 0.15, f"{val:.2f}", ha="center", va="bottom", fontsize=8)

    ax1.set_xticks(x)
    ax1.set_xticklabels([g[0] for g in pmu_groups], fontsize=8.5)
    ax1.set_ylabel("卡侧实测 host_rtt p50 (us)")
    ax1.set_title("卡→主机→卡 往返延迟（饱和 W=256，WB vs NC）")
    ax1.legend(fontsize=8, loc="upper right")
    ax1.set_ylim(0, max(5.0, ax1.get_ylim()[1] * 1.2))

    # Right: Memcached 1.6.45 (H=1 & H=4) Saturation Throughput
    mc_groups = [
        ("get8\nH=1", "get8", 1),
        ("get8\nH=4", "get8", 4),
        ("etc_mix\nH=1", "etc_mix", 1),
        ("etc_mix\nH=4", "etc_mix", 4),
    ]
    policies = [
        ("direct_wb", "DirectPush (Normal-WB)", "#1f77b4"),
        ("direct_nc_u1q", "DirectPush (MT_NORMAL_NC + u1_ldpq)", "#d62728"),
        ("sdp_fair", "SDP fair-full", "#7f7f7f"),
    ]
    x2 = np.arange(len(mc_groups))
    w2 = 0.26
    for pi, (pol, label, col) in enumerate(policies):
        ys, lo, hi = [], [], []
        for _, wl, hc in mc_groups:
            m, l, h = agg(rows, "mc", pol, wl, hc, 4, lambda win: win > 1, lambda r: r["client"]["achieved_mrps"])
            ys.append(m if m is not None else 0.0)
            lo.append(l)
            hi.append(h)
        bars = ax2.bar(x2 + (pi - 1) * w2, ys, w2, yerr=[lo, hi], capsize=2.5, label=label, color=col, alpha=0.9)
        for b, val in zip(bars, ys):
            if val > 0:
                ax2.text(b.get_x() + b.get_width() / 2, val + 0.2, f"{val:.2f}", ha="center", va="bottom", fontsize=8)

    ax2.set_xticks(x2)
    ax2.set_xticklabels([g[0] for g in mc_groups], fontsize=9)
    ax2.set_ylabel("饱和吞吐 (Mrps)")
    ax2.set_title("零修改上游 Memcached 1.6.45 吞吐（H=1 vs H=4, K=4）")
    ax2.legend(fontsize=8, loc="upper right")
    ax2.set_ylim(0, max(14.0, ax2.get_ylim()[1] * 1.18))

    mrvplot.save(fig, "mrv-259-pmu-and-memcached-ab")


if __name__ == "__main__":
    main()
