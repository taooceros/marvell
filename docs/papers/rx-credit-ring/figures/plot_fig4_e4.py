#!/usr/bin/env python3
"""Figure 4 (E4 / MRV-210): Multi-core 1->8 scaling, 254 B scaling & 4:1 RSS skew,
and single slow-core stall isolation (1/5/10 ms) vs privRing, ShRing, and rxBisect.
"""
import json
import statistics
from collections import defaultdict
import numpy as np
import matplotlib.pyplot as plt
from paper_style import (
    LOGS_DIR, apply_style, clean_ax, save_fig,
    C_CREDIT, C_LARGE, C_SHRING, C_BISECT, C_DARK, C_GRAY,
)

apply_style()


def med(xs):
    return statistics.median(xs)


def load_runs():
    by_cfg = defaultdict(list)
    with open(LOGS_DIR / "mrv210-2026-11-26" / "runs.jsonl") as f:
        for line in f:
            r = json.loads(line)
            assert r["verified"]
            by_cfg[r["cfg"]].append(r)
    return by_cfg


def main():
    by_cfg = load_runs()
    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(10.8, 3.45), gridspec_kw={"width_ratios": [1.02, 1.08, 1.20], "wspace": 0.30}
    )

    arms = [
        ("priv-ring",  "privRing (per-core large)",  C_LARGE,  "s", "--"),
        ("sh-ring",    "ShRing* (static shared)",    C_SHRING, "D", ":"),
        ("rx-bisect",  "rxBisect (Ax + Bx=8192)",    C_BISECT, "v", "-."),
        ("credit-sdp", "CreditRing (ours)",          C_CREDIT, "o", "-"),
    ]

    # =========================================================================
    # (a) Uniform 1->8 Host Cores (1500 B): Host IMC Write BW (GB/s)
    # =========================================================================
    clean_ax(ax1)
    cores = [1, 2, 4, 8]
    xc = np.arange(len(cores))
    handles = []
    for arm, lbl, col, mk, ls in arms:
        ys = [med([r["summary"]["imc_wr_gbps"] / 8.0 for r in by_cfg[f"p1_{arm}_c{c}_1500B_unif"]]) for c in cores]
        lw = 2.1 if arm == "credit-sdp" else 1.45
        h, = ax1.plot(xc, ys, marker=mk, ls=ls, lw=lw, ms=5.0, color=col, label=lbl,
                      zorder=4 if arm == "credit-sdp" else 3)
        handles.append(h)

    priv8 = med([r["summary"]["imc_wr_gbps"] / 8.0 for r in by_cfg["p1_priv-ring_c8_1500B_unif"]])
    priv8_kv = med([r["summary"]["kv_p99_ns"] for r in by_cfg["p1_priv-ring_c8_1500B_unif"]])
    cred8 = med([r["summary"]["imc_wr_gbps"] / 8.0 for r in by_cfg["p1_credit-sdp_c8_1500B_unif"]])
    cred8_kv = med([r["summary"]["kv_p99_ns"] for r in by_cfg["p1_credit-sdp_c8_1500B_unif"]])

    ax1.annotate(f"privRing: {priv8:.2f} GB/s\nDDIO 56.4%, KV {priv8_kv:.0f} ns",
                 xy=(3, priv8), xytext=(0.15, 4.95),
                 fontsize=7.2, color=C_LARGE, fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.20", fc="#FEF2F2", ec=C_LARGE, lw=0.65),
                 arrowprops=dict(arrowstyle="->", color=C_LARGE, lw=0.95))
    ax1.annotate(f"CreditRing: {cred8:.2f} GB/s (−18×)\n98.0% DDIO, KV {cred8_kv:.0f} ns",
                 xy=(3, cred8), xytext=(0.15, 2.25),
                 fontsize=7.2, color=C_CREDIT, fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.20", fc="#F0FDFA", ec=C_CREDIT, lw=0.65),
                 arrowprops=dict(arrowstyle="->", color=C_CREDIT, lw=0.95))

    ax1.set_xticks(xc)
    ax1.set_xticklabels([f"{c}c" for c in cores])
    ax1.set_xlabel("Host Receive Cores (1500 B @ 100G)")
    ax1.set_ylabel("Host IMC Write BW (GB/s)")
    ax1.set_ylim(-0.25, 6.2)
    ax1.set_title("(a) Uniform Core Scaling (1500 B)", pad=8)

    # =========================================================================
    # (b) 254 B Scaling (1->8c) + 8c 4:1 Skew
    # =========================================================================
    clean_ax(ax2)
    for arm, lbl, col, mk, ls in arms:
        ys = [med([r["summary"]["host_wire_gbps"] for r in by_cfg[f"p1_{arm}_c{c}_256B_unif"]]) for c in cores]
        lw = 2.1 if arm == "credit-sdp" else 1.45
        ax2.plot(xc, ys, marker=mk, ls=ls, lw=lw, ms=5.0, color=col,
                 zorder=4 if arm == "credit-sdp" else 3)

    ax2.axvline(3.48, color="#94A3B8", ls="--", lw=0.9)
    for arm, lbl, col, mk, ls in arms:
        y_sk = med([r["summary"]["host_wire_gbps"] for r in by_cfg[f"p2_{arm}_c8_256B_ratio4to1"]])
        ax2.plot([3.92], [y_sk], marker=mk, ms=6.0, color=col, zorder=5)

    sh8 = med([r["summary"]["host_wire_gbps"] for r in by_cfg["p1_sh-ring_c8_256B_unif"]])
    cr8 = med([r["summary"]["host_wire_gbps"] for r in by_cfg["p1_credit-sdp_c8_256B_unif"]])
    ax2.text(3.0, cr8 + 4.5, f"{cr8:.1f}G", ha="center", fontsize=7.6, color=C_CREDIT, fontweight="bold")
    ax2.annotate(f"ShRing*: {sh8:.1f}G\n(lock contention)", xy=(3, sh8), xytext=(1.55, 29),
                 fontsize=7.0, color=C_SHRING, fontweight="bold", ha="center",
                 arrowprops=dict(arrowstyle="->", color=C_SHRING, lw=0.9))
    ax2.text(2.15, 7.5, "8c 4:1 Skew: CreditRing achieves\n0 drops on all 7 healthy queues",
             ha="center", va="bottom", fontsize=6.8, color=C_CREDIT, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.22", fc="#F0FDFA", ec=C_CREDIT, lw=0.7))

    ax2.set_xticks([0, 1, 2, 3, 3.92])
    ax2.set_xticklabels(["1c", "2c", "4c", "8c", "8c\n4:1"], fontsize=7.8)
    ax2.set_xlabel("Host Cores / Distribution (254 B)")
    ax2.set_ylabel("Received Throughput (Gbps)")
    ax2.set_ylim(0, 114)
    ax2.set_xlim(-0.35, 4.42)
    ax2.set_title("(b) 254 B Scaling & 4:1 Skew", pad=8)

    # =========================================================================
    # (c) Single Slow-Core Stall Isolation (8 cores: 1/5/10 ms stalls)
    # =========================================================================
    clean_ax(ax3)
    stall_cfgs = [
        ("1500B", "stall1ms",  "1500B\n1 ms"),
        ("1500B", "stall5ms",  "1500B\n5 ms"),
        ("1500B", "stall10ms", "1500B\n10 ms"),
        ("256B",  "stall5ms",  "254B\n5 ms"),
        ("256B",  "stall10ms", "254B\n10 ms"),
    ]
    xs3 = np.arange(len(stall_cfgs))
    w3 = 0.20

    for ai, (arm, lbl, col, _, _) in enumerate(arms):
        losses = [med([r["summary"]["e2e_loss_pct"] for r in by_cfg[f"p3_{arm}_c8_{fr}_{st}"]])
                  for fr, st, _ in stall_cfgs]
        plot_vals = [max(v, 0.004) if v > 0 else 0.0 for v in losses]
        bars = ax3.bar(xs3 + (ai - 1.5) * w3, plot_vals, width=w3, color=col, edgecolor="#1E293B", lw=0.45, zorder=3)
        for bi, (b, v) in enumerate(zip(bars, losses)):
            cx = b.get_x() + b.get_width() / 2
            if arm == "credit-sdp":
                ax3.text(cx, 0.008, "0%", ha="center", va="bottom",
                         fontsize=6.8, color=C_CREDIT, fontweight="bold", rotation=90)
            elif arm == "sh-ring" and bi in (2, 3, 4):
                ax3.text(cx, v * 1.18, f"{v:.1f}%", ha="center", va="bottom",
                         fontsize=6.7, color=C_SHRING, fontweight="bold")
            elif arm == "rx-bisect" and bi in (2, 4):
                ax3.text(cx + 0.11, v * 1.24, f"{v:.2f}%", ha="center", va="bottom",
                         fontsize=6.4, color=C_BISECT, fontweight="bold", bbox=dict(boxstyle="round,pad=0.04", fc="white", ec="none", alpha=0.82))

    ax3.set_yscale("symlog", linthresh=0.05)
    ax3.set_ylim(0, 260)
    ax3.set_yticks([0, 0.1, 1, 10, 50])
    ax3.set_yticklabels(["0%", "0.1%", "1%", "10%", "50%"], fontsize=7.6)
    ax3.set_xticks(xs3)
    ax3.set_xticklabels([lbl for _, _, lbl in stall_cfgs], fontsize=7.3)
    ax3.axvline(2.5, color="#94A3B8", ls="--", lw=0.9)
    ax3.set_xlabel("Single Slow-Core Stall Duration (8c)")
    ax3.set_ylabel("End-to-End Packet Loss (%)")
    ax3.set_title("(c) Slow-Core Stall Isolation (8c)", pad=8)
    ax3.text(1.10, 175, "CreditRing: 0.00% loss\nKV 35–37 ns (all 8 stalls)",
             ha="center", va="top", fontsize=7.0, color=C_CREDIT, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.22", fc="#F0FDFA", ec=C_CREDIT, lw=0.7))

    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 1.07),
               ncol=4, fontsize=8.4, frameon=True, columnspacing=1.5, handlelength=2.1)
    save_fig(fig, "fig4_e4_multicore")


if __name__ == "__main__":
    main()
