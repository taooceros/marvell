#!/usr/bin/env python3
"""Figure 5 (E3 / MRV-211): Same-CN106 head-to-head vs dual-path CEIO."""
import gzip
import json
import statistics
from collections import defaultdict
import numpy as np
import matplotlib.pyplot as plt
from paper_style import (
    LOGS_DIR, apply_style, clean_ax, save_fig,
    C_CREDIT, C_CEIO, C_LARGE, C_SHRING, C_DARK,
)

apply_style()


def med(xs):
    return statistics.median(xs)


def load_runs():
    rows = []
    with gzip.open(LOGS_DIR / "mrv211-2026-11-26" / "runs.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            assert r["accepted"] and r["client"]["lost"] == 0 and r["client"]["ooo"] == 0
            rows.append(r)
    return rows


def main():
    rows = load_runs()
    by_w1, by_w2, by_w3 = defaultdict(list), defaultdict(list), defaultdict(list)
    u_w2_runs, c_w2_runs = [], []

    for r in rows:
        sp = r["spec"]
        su, pol = sp["suite"], sp["policy"]
        if su == "w1":
            by_w1[(pol, sp["tag"])].append(r)
        elif su == "w2":
            if pol != "card_echo":
                by_w2[(pol, int(sp["slot_us"]), int(sp["total_flows"]))].append(r)
            if pol == "unified":
                u_w2_runs.append(r)
            elif pol == "ceio":
                c_w2_runs.append(r)
        elif su == "w3":
            by_w3[(pol, sp["tag"])].append(r)

    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(10.9, 3.48), gridspec_kw={"width_ratios": [1.14, 1.12, 1.06], "wspace": 0.30}
    )

    # =========================================================================
    # (a) Co-Located KV p99: DDIO Blowthrough (Large SDP) vs LLC Read Contention (CEIO)
    # =========================================================================
    clean_ax(ax1)
    cases = [
        ("w1", "w1_burst_512B",  "Burst\n512 B"),
        ("w1", "w1_burst_1024B", "Burst\n1024 B"),
        ("w1", "w1_burst_1400B", "Burst\n1400 B"),
        ("w3", "w3_mixed_1to1",  "RPC+Bulk\n1:1"),
        ("w3", "w3_mixed_1to3",  "RPC+Bulk\n1:3"),
    ]
    pols_a = [
        ("large_sdp", "Large SDP (DDIO 48–77%)",        C_LARGE),
        ("ceio",      "CEIO (DDIO >99.5%, 2K pool)",    C_CEIO),
        ("unified",   "CreditRing (ours, DDIO >99.97%)",C_CREDIT),
    ]
    xa = np.arange(len(cases))
    wa = 0.25
    leg_handles = []
    for pi, (pol, lbl, col) in enumerate(pols_a):
        vals = []
        for su, tag, _ in cases:
            rs = by_w1[(pol, tag)] if su == "w1" else by_w3[(pol, tag)]
            vals.append(med([r["host"]["coloc_kv"]["p99_ns"] for r in rs]))
        bars = ax1.bar(xa + (pi - 1) * wa, vals, width=wa, color=col, edgecolor="#1E293B", lw=0.55, label=lbl, zorder=3)
        leg_handles.append(bars)
        for b, v in zip(bars, vals):
            dx = 0.03 if pol == "ceio" else (0.055 if pol == "unified" else -0.02)
            ax1.text(b.get_x() + b.get_width() / 2 + dx, v + 4.5, f"{v:.0f}",
                     ha="center", va="bottom", fontsize=6.0,
                     fontweight="bold" if pol == "unified" else "normal", color=col,
                     bbox=dict(boxstyle="round,pad=0.03", fc="white", ec="none", alpha=0.82))

    ax1.axvline(2.5, color="#94A3B8", ls="--", lw=0.9)
    ax1.annotate("CEIO fast-pool\nLLC read contention\n(DDIO still 99.6–99.9%)",
                 xy=(2, 165), xytext=(1.55, 222),
                 ha="center", fontsize=6.6, color=C_CEIO, fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.18", fc="#EFF6FF", ec=C_CEIO, lw=0.6),
                 arrowprops=dict(arrowstyle="->", color=C_CEIO, lw=0.85))
    ax1.set_xticks(xa)
    ax1.set_xticklabels([lbl for _, _, lbl in cases], fontsize=7.2)
    ax1.set_ylabel("Co-Located 32 MiB KV p99 (ns)")
    ax1.set_ylim(0, 335)
    ax1.set_title("(a) Neighbor KV: DDIO vs LLC Read", pad=8)

    # =========================================================================
    # (b) Transient Flow Churn (64 -> 16,384 flows, 512 B)
    # =========================================================================
    clean_ax(ax2)
    flows = [64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384]
    slot_styles = [(1000, "-", "o"), (500, "--", "s"), (100, ":", "^")]
    u_all, c16_leads, c16_slows = [], [], []
    for slot_us, ls, mk in slot_styles:
        u_ys = [med([r["client"]["tx_gbps_wire"] for r in by_w2[("unified", slot_us, nf)]]) for nf in flows]
        c_ys = [med([r["client"]["tx_gbps_wire"] for r in by_w2[("ceio", slot_us, nf)]]) for nf in flows]
        u_all.extend(u_ys)
        c16_leads.append((u_ys[-1] / c_ys[-1] - 1.0) * 100.0)
        c16_slows.append(med([float(r["client"]["slow_frac_pct"]) for r in by_w2[("ceio", slot_us, 16384)]]))
        ax2.plot(flows, u_ys, ls=ls, marker=mk, ms=4.0, lw=1.65, color=C_CREDIT, zorder=4)
        ax2.plot(flows, c_ys, ls=ls, marker=mk, ms=4.0, lw=1.45, color=C_CEIO, zorder=3)

    ax2.axvline(256, color=C_SHRING, ls=":", lw=1.0, zorder=2)
    ax2.text(220, 32.2, "256-flow\nfast cap", ha="right", fontsize=6.8, color=C_SHRING, fontweight="bold")
    ax2.text(1024, 57.6, f"CreditRing: flat {min(u_all):.1f}–{max(u_all):.1f} Gbps\n(single-core SDP ceiling, O(1) state)",
             ha="center", va="bottom", fontsize=6.8, color=C_CREDIT, fontweight="bold")
    ax2.annotate(f"+{min(c16_leads):.1f}% to +{max(c16_leads):.1f}% vs CEIO\nat 16K flows ({min(c16_slows):.1f}–{max(c16_slows):.1f}% slow)",
                 xy=(16384, 42.1), xytext=(2800, 35.2),
                 ha="center", fontsize=6.6, color=C_CEIO, fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.18", fc="#EFF6FF", ec=C_CEIO, lw=0.6),
                 arrowprops=dict(arrowstyle="->", color=C_CEIO, lw=0.9))
    ax2.set_xscale("log", base=2)
    ax2.set_xticks([64, 256, 1024, 4096, 16384])
    ax2.set_xticklabels(["64", "256", "1K", "4K", "16K"], fontsize=7.6)
    ax2.set_xlabel("Total Active + Churning Flows (512 B)")
    ax2.set_ylabel("Single-Core Throughput (Gbps)")
    ax2.set_ylim(30, 64.5)
    ax2.set_title("(b) Transient Flow Churn (W2)", pad=8)

    # =========================================================================
    # (c) Linear Host CPU Cycle Decomposition
    # =========================================================================
    clean_ax(ax3)
    u_cyc = np.array([float(r["host"]["pmu"]["cycles"]) / float(r["host"]["rx_pkts"]) for r in u_w2_runs])
    c_slow = np.array([float(r["client"]["slow_frac_pct"]) / 100.0 for r in c_w2_runs])
    c_cyc = np.array([float(r["host"]["pmu"]["cycles"]) / float(r["host"]["rx_pkts"]) for r in c_w2_runs])

    slope, intercept = np.polyfit(c_slow, c_cyc, 1)
    pred = intercept + slope * c_slow
    r2 = 1.0 - np.sum((c_cyc - pred) ** 2) / np.sum((c_cyc - np.mean(c_cyc)) ** 2)
    u_mean = float(np.mean(u_cyc))
    fixed_tax = intercept - u_mean
    fixed_share = fixed_tax / (fixed_tax + slope) * 100.0
    slow_share = slope / (fixed_tax + slope) * 100.0

    ax3.scatter(c_slow * 100.0, c_cyc, s=18, color=C_CEIO, alpha=0.62, edgecolors="white", lw=0.35, zorder=4)
    xs_fit = np.linspace(0, 100, 50)
    ax3.plot(xs_fit, intercept + slope * (xs_fit / 100.0), "-", color=C_DARK, lw=1.6, zorder=5)
    ax3.axhline(u_mean, color=C_CREDIT, ls="--", lw=1.7, zorder=4)
    ax3.fill_between([0, 100], u_mean, intercept, color=C_LARGE, alpha=0.09, zorder=1)

    ax3.annotate("", xy=(5, intercept), xytext=(5, u_mean),
                 arrowprops=dict(arrowstyle="<->", color=C_LARGE, lw=1.3))
    ax3.text(10, (u_mean + intercept) / 2,
             f"Fixed 8Q + SW merge ring\n+{fixed_tax:.1f} cyc/pkt ({fixed_share:.0f}%)\neven at 0% slow path",
             va="center", ha="left", fontsize=6.8, color=C_LARGE, fontweight="bold")
    ax3.text(98, u_mean - 3.5, f"CreditRing: {u_mean:.1f} cyc/pkt",
             ha="right", va="top", fontsize=7.0, color=C_CREDIT, fontweight="bold")
    ax3.text(44, 476, f"cyc/pkt = {intercept:.1f} + {slope:.1f} · slow share\n(R² = {r2:.3f}; slow pull = +{slope:.1f} cyc, {slow_share:.0f}%)",
             ha="center", va="top", fontsize=6.6, color=C_DARK, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.20", fc="white", ec="#94A3B8", lw=0.65))
    ax3.set_xlim(-3, 103)
    ax3.set_ylim(318, 484)
    ax3.set_xlabel("CEIO Slow-Path Traffic Share (%)")
    ax3.set_ylabel("Host CPU Cost (cycles / pkt)")
    ax3.set_title("(c) Host CPU Tax Decomposition", pad=8)

    fig.legend(handles=leg_handles, loc="upper center", bbox_to_anchor=(0.5, 1.07),
               ncol=3, fontsize=8.3, frameon=True, columnspacing=1.5)
    save_fig(fig, "fig5_e3_ceio_h2h")


if __name__ == "__main__":
    main()
