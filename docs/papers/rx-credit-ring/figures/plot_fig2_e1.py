#!/usr/bin/env python3
"""Figure 2 (E1 / MRV-212): Unfenced BAR4 MMIO credit signaling microbenchmarks."""
import json
import statistics
from collections import defaultdict
import numpy as np
import matplotlib.pyplot as plt
from paper_style import (
    LOGS_DIR, apply_style, clean_ax, save_fig,
    C_CREDIT, C_LARGE, C_CEIO, C_SHRING, C_SMALL, C_DARK, C_GRAY, C_SAFE_BG, C_WARN_BG,
)

apply_style()


def load_runs():
    by_gt = defaultdict(list)
    with open(LOGS_DIR / "mrv212-2026-11-26" / "runs.jsonl") as f:
        for line in f:
            r = json.loads(line)
            assert r["ok"], f"unverified mrv212 run: {r['group']}/{r['tag']}"
            by_gt[(r["group"], r["tag"])].append(r["data"])
    return by_gt


def med(xs):
    return statistics.median(xs)


def main():
    by_gt = load_runs()
    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(11.1, 3.45), gridspec_kw={"width_ratios": [1.02, 1.30, 1.10], "wspace": 0.54}
    )

    # =========================================================================
    # (a) Host CPU Cost per Packet (A0 micro, B=32) + Control-Loop RTT (1500 B)
    # =========================================================================
    clean_ax(ax1)
    off_med = med([d["cycles_per_batch"] for d in by_gt[("A0_micro", "micro_b512_off")]])
    tiers = [
        ("bar4-nofence", "Unfenced\nMOVDIR", C_CREDIT),
        ("bar4-sfence",  "Fenced\nMOVDIR",   C_LARGE),
        ("bar4-uc64",    "Uncached\nwrite64",C_SHRING),
        ("hostmem-dpi",  "Card DPI\nRead",   C_CEIO),
    ]
    cyc_pkt, rtt_1500 = [], []
    for k, _, _ in tiers:
        db = med([d["cycles_per_batch"] - off_med for d in by_gt[("A0_micro", f"micro_b512_{k}")]])
        cyc_pkt.append(db / 32.0)
        rtt_1500.append(med([d["p50_us"] for d in by_gt[("A2_rtt", f"credit_rtt_1500B_{k}")]]))

    x1 = np.arange(len(tiers))
    bars = ax1.bar(x1, cyc_pkt, width=0.52, color=[c for _, _, c in tiers], edgecolor="#1E293B", lw=0.7, zorder=3)
    for b, cp in zip(bars, cyc_pkt):
        ax1.text(b.get_x() + b.get_width() / 2, cp + 0.4, f"+{cp:.2f}",
                 ha="center", va="bottom", fontsize=7.9, fontweight="bold", color=C_DARK)

    ax1_r = ax1.twinx()
    ax1_r.spines["top"].set_visible(False)
    ax1_r.spines["right"].set_color(C_CEIO)
    ax1_r.tick_params(axis="y", colors=C_CEIO, labelsize=7.7, length=3.0, pad=2.0)
    ax1_r.plot(x1, rtt_1500, "o--", color=C_CEIO, ms=5.0, lw=1.25, zorder=5)
    offsets = [(0, 0.25), (0, -0.40), (0, 0.25), (-0.06, 0.22)]
    for xi, rtt, (dx, dy) in zip(x1, rtt_1500, offsets):
        ax1_r.text(xi + dx, rtt + dy, f"{rtt:.2f} µs", ha="center", va="bottom" if dy > 0 else "top",
                   fontsize=7.2, color=C_CEIO, fontweight="bold",
                   bbox=dict(boxstyle="round,pad=0.07", fc="white", ec="none", alpha=0.84))
    ax1_r.set_ylim(0, 5.5)
    ax1_r.set_ylabel("Control RTT (1500 B, µs)", fontsize=8.3, color=C_CEIO)

    ax1.set_xticks(x1)
    ax1.set_xticklabels([lbl for _, lbl, _ in tiers], fontsize=7.5)
    ax1.set_ylabel("Host CPU Cost (cyc/pkt, B=32)")
    ax1.set_ylim(0, 21.8)
    ax1.set_title("(a) Signaling Cost & Control RTT", pad=8)
    sf_x = cyc_pkt[1] / cyc_pkt[0]
    uc_x = cyc_pkt[2] / cyc_pkt[0]
    bdp_kib = med([d["bdp_100gbe_p50_kib"] for d in by_gt[("A2_rtt", "credit_rtt_1500B_bar4-nofence")]]) + 32 * 1500 / 1024.0
    bdp_slots = int(round(8.22 * rtt_1500[0] + 32))
    ax1.text(0.03, 0.96, f"{sf_x:.1f}× / {uc_x:.1f}× cheaper (fenced / UC)\n1500 B BDP floor: {bdp_kib:.1f} KiB ({bdp_slots} slots)",
             transform=ax1.transAxes, fontsize=6.5, va="top", color=C_CREDIT, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.20", fc="#F0FDFA", ec=C_CREDIT, lw=0.75))
    # =========================================================================
    # (b) Neighbor Interference Boundary (Control B)
    # =========================================================================
    clean_ax(ax2)
    b_conds = [
        ("idle",                "Idle"),
        ("bar4_1c_0.5M_nf",     "1c\n0.5M"),
        ("bar4_1c_7.0M_nf",     "1c\n7M"),
        ("bar4_8c_7.0M_nf",     "8c\n7M"),
        ("opus_1c_full_sf512",  "Full\nSF512"),
        ("opus_1c_full_sf2048", "Full\nSF2K"),
        ("opus_1c_full_sf8192", "Full\nSF8K"),
        ("opus_1c_full_nf",     "Full\nNoSF"),
    ]
    llc_ns = [med([d["victim_res"]["net_mean_ns"] for d in by_gt[("B", f"llc_k32__{k}")]]) for k, _ in b_conds]
    m2p_pct = [med([d["m2p_ne_pct"] for d in by_gt[("B", f"llc_k32__{k}")]]) for k, _ in b_conds]
    cha_occ = [med([d["cha_occ_all"] for d in by_gt[("B", f"llc_k32__{k}")]]) for k, _ in b_conds]
    x2 = np.arange(len(b_conds))
    ax2.axvspan(-0.48, 3.48, color=C_SAFE_BG, alpha=0.58, zorder=0)
    ax2.axvspan(6.52, 7.48, color=C_WARN_BG, alpha=0.68, zorder=0)

    col2 = [C_SMALL] + [C_CREDIT] * 3 + [C_SHRING] * 3 + [C_LARGE]
    ax2.bar(x2, llc_ns, width=0.58, color=col2, edgecolor="#1E293B", lw=0.65, zorder=3)
    ax2.set_xticks(x2)
    ax2.set_xticklabels([lbl for _, lbl in b_conds], fontsize=7.0)
    ax2.set_ylabel("Co-Located L3 Latency (ns)")
    ax2.set_ylim(0, 98)
    ax2.set_title("(b) Neighbor Isolation vs MMIO Cliff", pad=8)

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.spines["right"].set_color(C_DARK)
    ax2_r.tick_params(axis="y", colors=C_DARK, labelsize=7.7, length=3.0, pad=2.0)
    ax2_r.plot(x2, m2p_pct, "D-", color=C_DARK, ms=4.2, lw=1.2, zorder=5)
    ax2_r.axhline(94.0, color=C_LARGE, ls=":", lw=1.0, zorder=4)
    ax2_r.text(4.7, 97.5, ">94% cliff", fontsize=6.9, color=C_LARGE, fontweight="bold")
    ax2_r.set_ylim(-5, 120)
    ax2_r.set_ylabel("PCIe Ingress Busy (%)", fontsize=8.2, color=C_DARK, labelpad=3)

    max_safe_m2p = max(m2p_pct[1:4])
    max_safe_pct = max((v / llc_ns[0] - 1.0) * 100.0 for v in llc_ns[1:4])
    cliff_pct = (llc_ns[-1] / llc_ns[0] - 1.0) * 100.0
    ax2.text(1.5, 93, f"Credit Safe Zone\n0.5–7 Mpps (≤{max_safe_m2p:.1f}% busy)\n≤+{max_safe_pct:.2f}% L3 impact",
             ha="center", va="top", fontsize=6.8, color=C_CREDIT, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.20", fc="white", ec=C_CREDIT, lw=0.65, alpha=0.95))
    ax2.annotate(f"{llc_ns[-1]:.1f} ns (+{cliff_pct:.1f}%)\n{m2p_pct[-1]:.1f}% busy; mesh {cha_occ[-2]:.0f}→{cha_occ[-1]:.0f}",
                 xy=(7, llc_ns[-1]), xytext=(2.45, 53),
                 fontsize=6.6, color=C_LARGE, fontweight="bold", ha="center",
                 bbox=dict(boxstyle="round,pad=0.18", fc="white", ec=C_LARGE, lw=0.65, alpha=0.96),
                 arrowprops=dict(arrowstyle="->", color=C_LARGE, lw=1.0))
    # =========================================================================
    # (c) Host-Pause Ablation (D1 1Q & D2 2Q, 1500 B)
    # =========================================================================
    clean_ax(ax3)
    d_items = [
        ("D1", "1500B_5M_native_bp_d256",    "Pass.\n256",  C_LARGE),
        ("D1", "1500B_5M_native_bp_d512",    "Pass.\n512",  C_SHRING),
        ("D1", "1500B_5M_bar4_nofence_R256", "Cred.\nR=256",C_CREDIT),
        ("D1", "1500B_5M_bar4_nofence_R64",  "Cred.\nR=64", C_CREDIT),
        ("D1", "1500B_5M_bar4_nofence_96KiB","Cred.\n96KiB",C_CREDIT),
        ("D2", "1500B_native_bp_d512",       "2Q P.\n512",  C_LARGE),
        ("D2", "1500B_bar4_nofence_R128",    "2Q C.\nR=128",C_CREDIT),
    ]
    lost_k, res_max = [], []
    for g, tag, _, _ in d_items:
        rows = by_gt[(g, tag)]
        if g == "D1":
            lost_k.append(med([d["meas_seq_gap_lost"] for d in rows]) / 1e3)
            res_max.append(int(med([d["pause_drained_max"] for d in rows])))
        else:
            lost_k.append(med([d["q1_meas_seq_gap_lost"] for d in rows]) / 1e3)
            res_max.append(int(med([d["q1_drained_max"] for d in rows])))

    x3 = np.arange(len(d_items))
    ax3.bar(x3, lost_k, width=0.58, color=[c for _, _, _, c in d_items], edgecolor="#1E293B", lw=0.65, zorder=3)
    ax3.axvline(4.5, color="#94A3B8", ls="--", lw=0.95, zorder=2)

    for xi, (lk, rm) in enumerate(zip(lost_k, res_max)):
        if lk > 0:
            ax3.text(xi, lk + 18, f"{lk:.0f}K", ha="center", va="bottom",
                     fontsize=7.6, fontweight="bold", color=C_LARGE)
        else:
            ax3.text(xi, 20, f"0\n≤{rm}p", ha="center", va="bottom",
                     fontsize=6.2, fontweight="bold", color=C_CREDIT, linespacing=1.08)

    ax3.set_xticks(x3)
    ax3.set_xticklabels([lbl for _, _, lbl, _ in d_items], fontsize=6.9)
    ax3.set_ylabel("Silent Host Drops (K pkts/s)")
    ax3.set_ylim(0, 810)
    ax3.set_title("(c) Host-Pause Drops: Passive vs Credit", pad=8)
    ax3.text(2.0, 775, "1Q (1 ms / 10 ms)", ha="center", va="top", fontsize=7.4, color=C_GRAY, fontweight="bold")
    ax3.text(5.55, 775, "2Q (8 ms)", ha="center", va="top", fontsize=7.4, color=C_GRAY, fontweight="bold")

    save_fig(fig, "fig2_e1_signaling")


if __name__ == "__main__":
    main()
