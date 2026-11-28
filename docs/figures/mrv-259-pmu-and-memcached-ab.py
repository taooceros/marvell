#!/usr/bin/env python3
"""Plot MRV-259 Figure 2: Left = Card-measured host_rtt p50 (split KV get64 vs MC get8 axes);
Right = Zero-mod Upstream Memcached 1.6.45 (H=1 & H=4) A/B Saturation Throughput.
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
        return None, 0.0, 0.0, 0
    med = statistics.median(vals)
    return med, med - min(vals), max(vals) - med, len(vals)


def main():
    rows = load_runs()
    fig = plt.figure(figsize=(14.5, 5.2))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.15, 0.85, 1.45], wspace=0.28)
    ax1a = fig.add_subplot(gs[0, 0])
    ax1b = fig.add_subplot(gs[0, 1])
    ax2 = fig.add_subplot(gs[0, 2])

    # Panel A: Card-measured host_rtt_p50_us on KvStore get64 (H=1,2,4) — dedicated 0-5 us scale
    kv_groups = [
        ("KV get64\nH=1", "kv", "get64", 1),
        ("KV get64\nH=2", "kv", "get64", 2),
        ("KV get64\nH=4", "kv", "get64", 4),
    ]
    w = 0.34
    wb_nc = [
        ("direct_wb", "DirectPush (Normal-WB)", "#1f77b4"),
        ("direct_nc_u1q", "DirectPush (MT_NORMAL_NC + u1_ldpq)", "#d62728"),
    ]
    x1a = np.arange(len(kv_groups))
    for pi, (pol, label, col) in enumerate(wb_nc):
        ys, lo, hi = [], [], []
        for _, app, wl, hc in kv_groups:
            m, l, h, _ = agg(rows, app, pol, wl, hc, 4, lambda win: win > 1, lambda r: r["card"].get("host_rtt_p50_us", 0.0))
            ys.append(m if m is not None else 0.0)
            lo.append(l)
            hi.append(h)
        bars = ax1a.bar(x1a + (pi - 0.5) * w, ys, w, yerr=[lo, hi], capsize=2.5, label=label, color=col, alpha=0.9)
        for b, val in zip(bars, ys):
            ax1a.text(b.get_x() + b.get_width() / 2, val + 0.10, f"{val:.2f}", ha="center", va="bottom", fontsize=8)

    ax1a.set_xticks(x1a)
    ax1a.set_xticklabels([g[0] for g in kv_groups], fontsize=8.5)
    ax1a.set_ylabel("卡侧实测 host_rtt p50 (us)")
    ax1a.set_title("KvStore 卡→主机→卡延迟（W=256）")
    ax1a.legend(fontsize=7.5, loc="upper left")
    ax1a.set_ylim(0, 5.0)

    # Panel B: Card-measured host_rtt_p50_us on Memcached get8 (H=1,4) — dedicated 0-72 us scale
    mc_rtt_groups = [
        ("MC get8\nH=1", "mc", "get8", 1),
        ("MC get8\nH=4", "mc", "get8", 4),
    ]
    x1b = np.arange(len(mc_rtt_groups))
    for pi, (pol, label, col) in enumerate(wb_nc):
        ys, lo, hi = [], [], []
        for _, app, wl, hc in mc_rtt_groups:
            m, l, h, _ = agg(rows, app, pol, wl, hc, 4, lambda win: win > 1, lambda r: r["card"].get("host_rtt_p50_us", 0.0))
            ys.append(m if m is not None else 0.0)
            lo.append(l)
            hi.append(h)
        bars = ax1b.bar(x1b + (pi - 0.5) * w, ys, w, yerr=[lo, hi], capsize=2.5, label=label, color=col, alpha=0.9)
        for b, val, h_err in zip(bars, ys, hi):
            ax1b.text(b.get_x() + b.get_width() / 2, val + h_err + 1.2, f"{val:.2f}", ha="center", va="bottom", fontsize=8)

    ax1b.set_xticks(x1b)
    ax1b.set_xticklabels([g[0] for g in mc_rtt_groups], fontsize=8.5)
    ax1b.set_ylabel("卡侧实测 host_rtt p50 (us)")
    ax1b.set_title("Memcached 卡→主机→卡延迟（W=256）")
    ax1b.set_ylim(0, 76.0)

    # Panel C: Memcached 1.6.45 (H=1 & H=4) Saturation Throughput
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
        ys, lo, hi, ns = [], [], [], []
        for _, wl, hc in mc_groups:
            m, l, h, n = agg(rows, "mc", pol, wl, hc, 4, lambda win: win > 1, lambda r: r["client"]["achieved_mrps"])
            ys.append(m if m is not None else 0.0)
            lo.append(l)
            hi.append(h)
            ns.append(n)
        bars = ax2.bar(x2 + (pi - 1) * w2, ys, w2, yerr=[lo, hi], capsize=2.5, label=label, color=col, alpha=0.9)
        for b, val, n in zip(bars, ys, ns):
            if val > 0:
                txt = f"{val:.2f}\n(n={n})" if n < 3 else f"{val:.2f}"
                ax2.text(b.get_x() + b.get_width() / 2, val + 0.2, txt, ha="center", va="bottom", fontsize=7.5)

    ax2.set_xticks(x2)
    ax2.set_xticklabels([g[0] for g in mc_groups], fontsize=9)
    ax2.set_ylabel("饱和吞吐 (Mrps)")
    ax2.set_title("零修改上游 Memcached 1.6.45 吞吐（H=1 vs H=4, K=4）")
    ax2.legend(fontsize=7.5, loc="upper right")
    ax2.set_ylim(0, max(14.5, ax2.get_ylim()[1] * 1.18))

    mrvplot.save(fig, "mrv-259-pmu-and-memcached-ab")

if __name__ == "__main__":
    main()
