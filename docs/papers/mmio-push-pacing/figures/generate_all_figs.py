#!/usr/bin/env python3
"""Generate all 6 publication-grade English vector PDF figures for
'The Missing Piece of MMIO Direct Push: Pacing' directly from raw runs.jsonl logs.
"""
import json
import os
import statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

LOG_BASE = "/home/hongtao/dev/marvell/logs"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 8.0,
    "axes.titlesize": 8.2,
    "axes.titleweight": "bold",
    "axes.labelsize": 7.8,
    "xtick.labelsize": 6.8,
    "ytick.labelsize": 7.0,
    "legend.fontsize": 6.3,
    "axes.linewidth": 0.75,
    "grid.linewidth": 0.45,
    "grid.alpha": 0.45,
    "lines.linewidth": 1.5,
    "lines.markersize": 4.2,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
})

C_NAVY   = "#1B4F72"
C_BLUE   = "#2E86AB"
C_TEAL   = "#1B9E77"
C_ORANGE = "#D95F02"
C_RED    = "#C0392B"
C_PURPLE = "#6C5B7B"
C_GRAY   = "#6E7B8B"
C_CLIFF  = "#FADBD8"


def load_jsonl(rel_path):
    with open(os.path.join(LOG_BASE, rel_path)) as f:
        return [json.loads(line) for line in f if line.strip()]


def med(vals):
    xs = [x for x in vals if x is not None]
    return statistics.median(xs) if xs else None


def style_ax(ax, grid_axis="y"):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if grid_axis:
        ax.grid(True, axis=grid_axis, linestyle="--", alpha=0.45, color="#B0BEC5")
    ax.set_axisbelow(True)


# =====================================================================
# Figure 0: Architecture & Dual Backpressure Loop Diagram
# =====================================================================
def make_fig0_architecture():
    fig, ax = plt.subplots(figsize=(7.2, 2.75))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 40)
    ax.axis("off")

    def box(x, y, w, h, text, fc="#F8FAFC", ec="#34495E", lw=0.9, fs=6.2, fw="normal", tc="#1A252F"):
        p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.30,rounding_size=0.7",
                           facecolor=fc, edgecolor=ec, linewidth=lw, zorder=3)
        ax.add_patch(p)
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fs, fontweight=fw, color=tc, zorder=4, linespacing=1.18)

    def arrow(x1, y1, x2, y2, color="#2C3E50", lw=1.2, ls="-", rad=0.0, ms=7.5):
        a = FancyArrowPatch((x1, y1), (x2, y2),
                            connectionstyle=f"arc3,rad={rad}",
                            arrowstyle=f"-|>,head_length={ms*0.55},head_width={ms*0.32}",
                            color=color, linewidth=lw, linestyle=ls, zorder=5)
        ax.add_patch(a)

    # Outer containers
    host_bg = FancyBboxPatch((0.6, 1.0), 54.4, 37.8, boxstyle="round,pad=0.25,rounding_size=0.9",
                             facecolor="#F4F8FB", edgecolor=C_BLUE, linewidth=1.1, zorder=1)
    ax.add_patch(host_bg)
    ax.text(1.8, 37.1, "Host Socket (Intel SPR Xeon 6438M, 32c, 2.2 GHz Mesh)",
            fontsize=6.7, fontweight="bold", color=C_NAVY, zorder=4)

    dsa_bg = FancyBboxPatch((58.5, 26.8), 40.9, 12.0, boxstyle="round,pad=0.25,rounding_size=0.9",
                            facecolor="#F4FBF7", edgecolor=C_TEAL, linewidth=1.1, zorder=1)
    ax.add_patch(dsa_bg)
    ax.text(59.7, 37.1, "On-Die Accelerator (Intel DSA wq2.0, IIO0)",
            fontsize=6.5, fontweight="bold", color="#117864", zorder=4)

    nic_bg = FancyBboxPatch((58.5, 1.0), 40.9, 24.4, boxstyle="round,pad=0.25,rounding_size=0.9",
                            facecolor="#FDF7F2", edgecolor=C_ORANGE, linewidth=1.1, zorder=1)
    ax.add_patch(nic_bg)
    ax.text(59.7, 23.7, "Off-Chip SmartNIC (Marvell CN106, Gen5 x4)",
            fontsize=6.5, fontweight="bold", color="#A04000", zorder=4)

    # Host components
    box(1.9, 19.0, 12.8, 15.2,
        "Producer LPs\nMOVDIR64B\n(Posted 64 B)\nLFB / Store Buf\n\nPacing Hook (1):\nrate + micro-burst\nor K=64/N split",
        fc="#EBF5FB", ec=C_NAVY, fw="bold", fs=5.8)

    box(1.9, 2.6, 12.8, 12.6,
        "Co-located\nNeighbors\n6c STREAM /\nL3 & DRAM\n(Shared TOR)",
        fc="#FFFFFF", ec=C_GRAY, fs=6.0)

    box(17.8, 13.8, 16.5, 20.4,
        "32x CHA 2D Mesh\n& Shared TOR\n(WCiLF + RFO/DRd)\n\nUniversal Cliff:\n330-430 entries\n(hottest box ~23)\n-> -67% to -97% BW",
        fc="#FDEDEC", ec=C_RED, lw=1.2, fs=5.9, fw="bold", tc="#78281F")

    box(17.8, 2.6, 16.5, 7.2,
        "2-ch DDR5 IMC\n(RPQ / WPQ, ~60 GB/s)\nItoM hides -22% at 12c",
        fc="#FFFFFF", ec=C_GRAY, fs=5.8)

    box(38.0, 27.2, 15.6, 7.6,
        "M2PCIe0 -> IRP0\n-> IIO0 (1 GHz)\n(143 Mpps cap)",
        fc="#E8F8F5", ec=C_TEAL, fs=6.0)

    box(38.0, 13.0, 15.6, 9.2,
        "M2PCIe1 -> IRP1\n-> IIO1 (1 GHz)\nPCIe Gen5 x4\n(175.5 Mpps cap)",
        fc="#FEF5E7", ec=C_ORANGE, fs=6.0)

    # Red backpressure callout neatly inside host lower-right empty corner
    box(37.4, 2.4, 16.8, 7.6,
        "Posted Backpressure:\nUnpaced push or stall\nrefills CHA TOR\n(>330-430 -> collapse)",
        fc="#FFF5F5", ec=C_RED, lw=0.9, fs=5.3, fw="bold", tc=C_RED)

    # DSA target components
    box(60.0, 28.0, 18.2, 7.4,
        "4 KiB Portal (64 lines)\n(LP, 64B line) limit\n12-32c -> 374-553 TOR",
        fc="#E8F8F5", ec=C_TEAL, fs=5.65)
    box(80.6, 28.0, 17.4, 7.4,
        "DWQ (128) + Engine\n94.6% HW drop still\n100% backpressures",
        fc="#FFFFFF", ec=C_TEAL, fs=5.65)

    # CN106 target components
    box(60.0, 12.6, 17.5, 9.4,
        "PEM0 / IOBN Bridge\n~387-slot posted buf\nLevel-1 WB lock:\n13.4 M/s snoop cliff",
        fc="#FDEBD0", ec=C_ORANGE, fs=5.6)

    box(79.8, 12.6, 18.2, 9.4,
        "48 MiB L3 TAD (PoC)\nMT_NORMAL_NC (0x44):\n0 snoop, 0 DDR5;\nEXLMN if over-polled",
        fc="#FFFFFF", ec="#B9770E", fs=5.5)

    box(60.0, 2.3, 38.0, 7.9,
        "24x ARMv8 Neoverse N2 Cores (2.00 GHz) — Receivers\n"
        "WB: 13.4M cliff  |  MT_NORMAL_NC: 160-174M (K=4)\n"
        "Coalesced DB + SFENCE: 105-118M (CHA 6-12)",
        fc="#FEF9E7", ec="#B9770E", fs=5.1)

    # Forward data arrows
    arrow(14.7, 26.0, 17.8, 26.0, C_NAVY, 1.3)
    arrow(14.7, 9.5,  17.8, 16.5, C_GRAY, 1.0)
    arrow(26.0, 13.8, 26.0, 9.8,  C_GRAY, 1.0)
    arrow(34.3, 28.5, 38.0, 31.2, C_TEAL, 1.3)
    arrow(34.3, 19.0, 38.0, 18.2, C_ORANGE, 1.3)
    arrow(53.6, 32.2, 60.0, 32.2, C_TEAL, 1.3)
    arrow(78.2, 31.7, 80.6, 31.7, C_TEAL, 1.1)
    arrow(53.6, 18.5, 60.0, 18.5, C_ORANGE, 1.3)
    arrow(77.5, 17.3, 79.8, 17.3, C_ORANGE, 1.1)
    arrow(88.9, 12.6, 88.9, 10.2, "#B9770E", 1.1)

    # Red backpressure arrows
    arrow(60.0, 29.6, 53.6, 29.6, C_RED, 1.4, ls="--")
    arrow(38.0, 28.6, 34.3, 26.2, C_RED, 1.4, ls="--")
    arrow(60.0, 15.2, 53.6, 15.2, C_RED, 1.4, ls="--")
    arrow(38.0, 15.2, 34.3, 16.5, C_RED, 1.4, ls="--")

    out_pdf = os.path.join(OUT_DIR, "fig0_architecture.pdf")
    fig.savefig(out_pdf)
    plt.close(fig)
    print("Wrote", out_pdf)


# =====================================================================
# Figure 1: Universal Host CHA TOR Collapse Law (MRV-196 + MRV-246)
# =====================================================================
def make_fig1_host_tor_collapse():
    r196 = [r for r in load_jsonl("mrv196-2026-11-25/runs.jsonl") if r.get("accepted")]
    r246 = [r for r in load_jsonl("mrv246-2026-11-27/runs.jsonl") if r.get("accepted")]

    b196 = defaultdict(list)
    for r in r196: b196[r["label"]].append(r)
    b246 = defaultdict(list)
    for r in r246: b246[r["label"]].append(r)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 2.65), gridspec_kw={"width_ratios": [1.05, 1.15]})
    plt.subplots_adjust(wspace=0.34)

    dsa_x = [1, 4, 8, 12, 16, 20, 24, 31, 48, 62]
    dsa_lbls = ["1c", "4c", "8c", "12c", "16c", "20c", "24c", "31c", "48t_24x2", "62t_31x2"]
    dsa_wcilf = [med([r["cha"]["occ_entries"] for r in b246[f"llc__noop-stride_{l}_wcilf"]]) for l in dsa_lbls]

    fix_x = [1, 8, 16, 24, 31]
    fix_lbls = ["1c", "8c", "16c", "24c", "31c"]
    fix_wcilf = []
    for l in fix_lbls:
        key = f"llc__noop-fixed_{l}_wcilf" if f"llc__noop-fixed_{l}_wcilf" in b246 else f"llc__noop-fixed_{l}"
        fix_wcilf.append(med([r["cha"]["occ_entries"] for r in b246[key]]))

    bar4_x = [1, 2, 4, 6, 8]
    bar4_lbls = ["1c", "2c", "4c", "6c", "8c"]
    bar4_wcilf = [med([r["cha"]["occ_entries"] for r in b196[f"llc__bar4_{l}_max_b512"]]) for l in bar4_lbls]

    ax1.axhspan(330, 430, color=C_CLIFF, alpha=0.65, zorder=1)
    ax1.text(39, 380, "Universal Collapse\nKnee (330-430)", fontsize=6.3, fontweight="bold",
             color=C_RED, ha="center", va="center", zorder=2)

    ax1.plot(bar4_x, bar4_wcilf, "-s", color=C_ORANGE, label="CN106 BAR4 (32,768 lines, b=512)")
    ax1.plot(dsa_x, dsa_wcilf, "-o", color=C_NAVY, label="Intel DSA 4 KiB (64 lines, stride)")
    ax1.plot(fix_x, fix_wcilf, "--^", color=C_TEAL, label="Intel DSA 64 B (1 line, fixed)")

    ax1.annotate("8c DSA: 319\n(just below knee)", xy=(8, dsa_wcilf[2]), xytext=(14, 225),
                 fontsize=6.1, color=C_NAVY, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_NAVY, lw=0.8))
    ax1.annotate("8c BAR4: 654", xy=(8, bar4_wcilf[-1]), xytext=(15, 635),
                 fontsize=6.1, color=C_ORANGE, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_ORANGE, lw=0.8))
    ax1.annotate("62t: 553", xy=(62, dsa_wcilf[-1]), xytext=(47, 600),
                 fontsize=6.1, color=C_NAVY, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_NAVY, lw=0.8))

    ax1.set_xlabel("Concurrent MMIO Push Threads (Cores / SMT)")
    ax1.set_ylabel("Socket CHA WCiLF Occupancy (entries)")
    ax1.set_title("(a) CHA WCiLF Scaling Across Two Targets")
    ax1.set_xlim(0, 65)
    ax1.set_ylim(0, 720)
    ax1.set_xticks([1, 8, 16, 24, 32, 48, 62])
    style_ax(ax1)
    ax1.legend(loc="lower right", frameon=True, facecolor="white", framealpha=0.94, edgecolor="#CFD8DC", fontsize=6.0)

    # Panel B
    base_s246 = med([r["stream"]["mean_gb_s"] for r in b246["t6c__idle"]])
    base_imc246 = med([r["imc"]["tot_gb_s"] for r in b246["t6c__idle"]])
    dsa_s_lbls = ["1c", "4c", "8c", "12c", "16c", "20c", "24c", "32t_16x2", "48t_24x2"]
    dsa_s_cha = [med([r["cha"]["occ_entries"] for r in b246[f"t6c__noop-stride_{l}_wcilf"]]) for l in dsa_s_lbls]
    dsa_s_bw  = [100.0 * med([r["stream"]["mean_gb_s"] for r in b246[f"t6c__noop-stride_{l}"]]) / base_s246 for l in dsa_s_lbls]
    dsa_s_imc = [100.0 * med([r["imc"]["tot_gb_s"] for r in b246[f"t6c__noop-stride_{l}"]]) / base_imc246 for l in dsa_s_lbls]

    base_s196 = med([r["stream"]["mean_gb_s"] for r in b196["t6c__idle"]])
    bar4_pts = [
        ("t6c__bar4_1c_max_b512", "llc__bar4_1c_max_b512"),
        ("t6c__bar4_4c_max_b512", "llc__bar4_4c_max_b512"),
        ("t6c__bar4_6c_max_b512", "llc__bar4_6c_max_b512"),
        ("t6c__bar4_8c_max_b512", "llc__bar4_8c_max_b512"),
    ]
    b4_cha = [med([r["cha"]["occ_entries"] for r in b196[lk]]) for _, lk in bar4_pts]
    b4_bw  = [100.0 * med([r["stream"]["mean_gb_s"] for r in b196[tk]]) / base_s196 for tk, _ in bar4_pts]

    dsa_l3_lbls = ["1c", "4c", "8c", "12c", "16c", "20c", "24c", "31c", "62t_31x2"]
    dsa_l3_cha = [med([r["cha"]["occ_entries"] for r in b246[f"llc__noop-stride_{l}_wcilf"]]) for l in dsa_l3_lbls]
    dsa_l3_p99 = [med([r["chase"]["p99_ns"] for r in b246[f"llc__noop-stride_{l}"]]) for l in dsa_l3_lbls]

    ax2.axvspan(330, 430, color=C_CLIFF, alpha=0.65, zorder=1)
    l1, = ax2.plot(dsa_s_cha, dsa_s_bw, "-o", color=C_NAVY, label="DSA STREAM BW (% idle)")
    l2, = ax2.plot(dsa_s_cha, dsa_s_imc, "--d", color=C_BLUE, label="DSA DDR5 IMC (% idle)")
    l3, = ax2.plot(b4_cha, b4_bw, "s", color=C_ORANGE, markersize=5.0, label="BAR4 STREAM (% idle)")

    ax2.annotate("12c ItoM mask:\nSTREAM -4.2%\nIMC -22.1%", xy=(dsa_s_cha[3], dsa_s_imc[3]),
                 xytext=(115, 62), fontsize=5.9, color=C_BLUE, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_BLUE, lw=0.8))

    ax2.set_xlabel("CHA WCiLF Occupancy (entries)")
    ax2.set_ylabel("Co-located 6c Bandwidth (% idle)")
    ax2.set_title("(b) Neighbor Collapse vs. CHA WCiLF")
    ax2.set_xlim(0, 700)
    ax2.set_ylim(0, 114)
    style_ax(ax2)

    ax2r = ax2.twinx()
    ax2r.spines["top"].set_visible(False)
    l4, = ax2r.plot(dsa_l3_cha, dsa_l3_p99, ":^", color=C_RED, linewidth=1.3, label="DSA 16 MiB L3 p99 (ns)")
    ax2r.set_yscale("log")
    ax2r.set_ylabel("16 MiB L3 Pointer-Chase p99 (ns)", color=C_RED)
    ax2r.tick_params(axis="y", labelcolor=C_RED)
    ax2r.set_ylim(18, 6500)

    lines = [l1, l2, l3, l4]
    labels = [l.get_label() for l in lines]
    ax2.legend(lines, labels, loc="center left", bbox_to_anchor=(0.01, 0.34), frameon=True,
               facecolor="white", framealpha=0.95, edgecolor="#CFD8DC", fontsize=5.7)

    out_pdf = os.path.join(OUT_DIR, "fig1_host_tor_collapse.pdf")
    fig.savefig(out_pdf)
    plt.close(fig)
    print("Wrote", out_pdf)


# =====================================================================
# Figure 2: Two-Level Device-Side Self-Collapse & Breakthrough (MRV-235/236/245)
# =====================================================================
def make_fig2_device_self_collapse():
    r235 = load_jsonl("mrv235-2026-11-27/runs.jsonl")
    r236 = load_jsonl("mrv236-2026-11-27/runs.jsonl")
    r245 = [r for r in load_jsonl("mrv245-2026-11-27/runs.jsonl") if "mops" in r]

    b235 = defaultdict(list)
    for r in r235: b235[r["cond"]].append(r)
    b236 = defaultdict(list)
    for r in r236: b236[r["arm"]].append(r)
    b245 = defaultdict(list)
    for r in r245: b245[r["cond"]].append(r)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 2.70), gridspec_kw={"width_ratios": [1.0, 1.2]})
    plt.subplots_adjust(wspace=0.40)

    # Panel A
    cats = [
        "WB\nK=1",
        "WB K=1\n+Shdw",
        "WB\nK=2",
        "NC K=2\nW=64",
        "NC K=2\nW=256",
        "NC K=4\n1H TX",
        "NC K=4\n2H TX",
    ]
    vals = [
        med([r["mops"] for r in b236["B1"]]),
        med([r["mops"] for r in b236["S2"]]),
        med([r["mops"] for r in b235["wb64k_k2_w64"]]),
        med([r["mops"] for r in b235["nc44_1p_k2_w64"]]),
        med([r["mops"] for r in b245["cl_nc_u1q_h1_k2_w256"]]),
        med([r["mops"] for r in b245["tx_nc_u4_h1_k4"]]),
        med([r["mops"] for r in b245["tx_nc_u4_h2_k4"]]),
    ]
    colors = [C_GRAY, C_RED, C_RED, C_TEAL, C_TEAL, C_NAVY, C_NAVY]
    x = np.arange(len(cats))
    bars = ax1.bar(x, vals, width=0.58, color=colors, edgecolor="#2C3E50", linewidth=0.6)
    for bar, v in zip(bars, vals):
        ax1.text(bar.get_x() + bar.get_width()/2, v + 3.5, f"{v:.1f}",
                 ha="center", va="bottom", fontsize=6.0, fontweight="bold")

    ax1.axhline(13.4, color=C_RED, linestyle=":", linewidth=1.0)
    ax1.annotate("13.4 M/s\nsnoop cliff", xy=(1.5, 13.4), xytext=(1.0, 68),
                 fontsize=6.0, color=C_RED, fontweight="bold", ha="center",
                 arrowprops=dict(arrowstyle="->", color=C_RED, lw=0.8))

    ax1.set_xticks(x)
    ax1.set_xticklabels(cats, fontsize=5.3)
    ax1.set_ylabel("Verified Message Rate (Mmsg/s)")
    ax1.set_title("(a) Level-1 Snoop Cliff vs. NC (0x44)")
    ax1.set_ylim(0, 200)
    style_ax(ax1)

    # Panel B
    tx_labels = ["u4\nK=1", "u4\nK=2", "u4 K=4\nsweet", "u4 K=8\nover", "u1 K=8\nover", "CL u1\nK=2", "CL u4\nK=2*", "CL u1\nK=8*"]
    tx_conds = [
        "tx_nc_u4_h1_k1", "tx_nc_u4_h1_k2", "tx_nc_u4_h1_k4", "tx_nc_u4_h1_k8",
        "tx_nc_u1_h1_k8", "cl_nc_u1q_h1_k2_w256", "cl_nc_u4_h1_k2_w256", "cl_nc_u1_h1_k8_w256"
    ]
    tx_mops = [med([r["mops"] for r in b245[c]]) for c in tx_conds]
    tx_cha  = [med([r["host_pmu"]["cha_inflight"] for r in b245[c]]) for c in tx_conds]

    x2 = np.arange(len(tx_labels))
    bar_cols = [C_BLUE, C_BLUE, C_TEAL, C_ORANGE, C_ORANGE, C_TEAL, C_RED, C_RED]
    bars2 = ax2.bar(x2, tx_mops, width=0.56, color=bar_cols, edgecolor="#2C3E50", linewidth=0.6, alpha=0.92)
    for bar, v in zip(bars2, tx_mops):
        ax2.text(bar.get_x() + bar.get_width()/2, v + 3.5, f"{v:.1f}",
                 ha="center", va="bottom", fontsize=5.8, fontweight="bold")

    ax2.axvline(4.5, color="#7F8C8D", linestyle="--", linewidth=0.9)
    ax2.text(2.0, 205, "One-Way TX (1H)", ha="center", fontsize=6.2, fontweight="bold", color=C_NAVY)
    ax2.text(6.1, 205, "Closed-Loop W=256", ha="center", fontsize=6.2, fontweight="bold", color=C_RED)

    ax2.set_xticks(x2)
    ax2.set_xticklabels(tx_labels, fontsize=5.7)
    ax2.set_ylabel("Throughput (Mmsg/s)")
    ax2.set_title("(b) Level-2 EXLMN & Host CHA Spillover")
    ax2.set_ylim(0, 222)
    style_ax(ax2)

    ax2r = ax2.twinx()
    ax2r.spines["top"].set_visible(False)
    ax2r.axhspan(330, 430, color=C_CLIFF, alpha=0.35, zorder=0)
    ax2r.plot(x2[:5], tx_cha[:5], "-D", color=C_RED, markersize=4.2, linewidth=1.3, label="Host CHA TOR (entries)")
    ax2r.annotate("CHA=521\n(>cliff)", xy=(4, tx_cha[4]), xytext=(0.45, 535),
                  fontsize=5.9, fontweight="bold", color=C_RED,
                  arrowprops=dict(arrowstyle="->", color=C_RED, lw=0.8))
    ax2r.set_ylabel("Host CHA WCiLF (entries)", color=C_RED)
    ax2r.tick_params(axis="y", labelcolor=C_RED)
    ax2r.set_ylim(0, 680)
    ax2r.legend(loc="upper right", bbox_to_anchor=(0.99, 0.87), frameon=True, facecolor="white", fontsize=5.8)

    out_pdf = os.path.join(OUT_DIR, "fig2_device_self_collapse.pdf")
    fig.savefig(out_pdf)
    plt.close(fig)
    print("Wrote", out_pdf)


# =====================================================================
# Figure 3: Ordering Hazards & Receiver Regime Head-to-Head (MRV-190/177/245)
# =====================================================================
def make_fig3_ordering_and_doorbell():
    r190 = [r for r in load_jsonl("mrv190-2026-11-25/runs.jsonl") if r.get("accepted")]
    r177 = [r for r in load_jsonl("mrv177-2026-11-24/runs.jsonl") if r.get("status") == "pass"]
    r245 = [r for r in load_jsonl("mrv245-2026-11-27/runs.jsonl") if "mops" in r]

    b190 = defaultdict(list)
    for r in r190: b190[r["label"]].append(r)
    b177 = defaultdict(list)
    for r in r177: b177[(r["phase"], r["workload"], r["arm"], r["credit_group"])].append(r)
    b245 = defaultdict(list)
    for r in r245: b245[r["cond"]].append(r)

    def pct190(lbl):
        rs = b190[lbl]
        return 100.0 * sum(r["card"]["reordered_ops"] for r in rs) / max(1, sum(r["card"]["rounds_observed"] for r in rs))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 2.60), gridspec_kw={"width_ratios": [1.0, 1.2]})
    plt.subplots_adjust(wspace=0.35)

    o_labels = [
        "512B\nno-F",
        "1KiB\nno-F",
        "DSA 4e\nF=0",
        "DSA 4e\n+DRAIN",
        "CPU\n+SFENCE",
        "DSA\nF=1",
    ]
    o_vals = [
        pct190("movdir_nf_512B_hot_uc_fwd"),
        pct190("movdir_nf_1024B_hot_uc_rev"),
        pct190("dsa_b2b_4096B_fcth_e4_f0"),
        pct190("dsa_b2b_4096B_fcth_e4_drain"),
        0.0,
        pct190("dsa_batch_4096B_fcth_e4_f1"),
    ]
    o_cols = [C_ORANGE, C_ORANGE, C_RED, C_RED, C_TEAL, C_TEAL]
    x = np.arange(len(o_labels))
    bars = ax1.bar(x, o_vals, width=0.56, color=o_cols, edgecolor="#2C3E50", linewidth=0.6)
    for bar, v in zip(bars, o_vals):
        txt = f"{v:.1f}%" if v >= 1.0 else "0.00%"
        ax1.text(bar.get_x() + bar.get_width()/2, v + 2.5, txt,
                 ha="center", va="bottom", fontsize=6.1, fontweight="bold",
                 color=C_TEAL if v == 0 else "#1A252F")

    ax1.set_xticks(x)
    ax1.set_xticklabels(o_labels, fontsize=5.8)
    ax1.set_ylabel("Transient Reordering Rate (%)")
    ax1.set_title("(a) Pre-IIO Reordering Hazards")
    ax1.set_ylim(0, 115)
    style_ax(ax1)

    # Panel B
    regimes = [
        "Raw WB\n(K=4)",
        "B-Queue\nprobe",
        "Per-Pkt\nDB (1C)",
        "SW Pace\n(Hyst)",
        "Coalesced\nDB+F",
        "MT_NC\n(0x44)",
    ]
    cl_vals = [
        med([r["mops"] for r in b245["cl_wb_raw_k4_w256"]]),
        med([r["mops"] for r in b177[("1C", "w64", "bq_probe", "unshaped")]]),
        med([r["mops"] for r in b177[("1C", "w128", "db_pkt_f", "unshaped")]]),
        med([r["mops"] for r in b245["cl_wb_hyst32b16_k4_w256"]]),
        med([r["mops"] for r in b245["cl_wb_db32_k1_w256"]]),
        med([r["mops"] for r in b245["cl_nc_u1q_h1_k2_w256"]]),
    ]
    tx_vals = [
        med([r["mops"] for r in b245["tx_wb_u1_h1_k4"]]),
        0.0,
        0.0,
        med([r["mops"] for r in b245["tx_wb_pace32g200_k4"]]),
        med([r["mops"] for r in b245["tx_wb_db128_k2"]]),
        med([r["mops"] for r in b245["tx_nc_u4_h1_k4"]]),
    ]

    x2 = np.arange(len(regimes))
    w = 0.36
    b_cl = ax2.bar(x2 - w/2, cl_vals, width=w, color=C_BLUE, edgecolor="#2C3E50", linewidth=0.55, label="Closed-Loop (1H)")
    b_tx = ax2.bar(x2 + w/2, tx_vals, width=w, color=C_NAVY, edgecolor="#2C3E50", linewidth=0.55, label="One-Way TX (1H)")

    for bar, v in zip(b_cl, cl_vals):
        ax2.text(bar.get_x() + bar.get_width()/2, v + 2.5, f"{v:.1f}", ha="center", va="bottom", fontsize=5.7, fontweight="bold", color=C_BLUE)
    for bar, v in zip(b_tx, tx_vals):
        if v > 0:
            ax2.text(bar.get_x() + bar.get_width()/2, v + 2.5, f"{v:.0f}", ha="center", va="bottom", fontsize=5.7, fontweight="bold", color=C_NAVY)

    ax2.annotate("CHA=546!", xy=(0 + w/2, tx_vals[0]), xytext=(0.65, 132),
                 fontsize=5.8, color=C_RED, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_RED, lw=0.75))
    ax2.annotate("CHA=12\n(0 snoop)", xy=(4 + w/2, tx_vals[4]), xytext=(2.85, 146),
                 fontsize=5.8, color=C_TEAL, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=0.75))
    ax2.annotate("CHA=28\n(0 SFENCE)", xy=(5 + w/2, tx_vals[5]), xytext=(3.85, 180),
                 fontsize=5.8, color=C_NAVY, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_NAVY, lw=0.75))

    ax2.set_xticks(x2)
    ax2.set_xticklabels(regimes, fontsize=5.8)
    ax2.set_ylabel("Throughput (Mmsg/s)")
    ax2.set_title("(b) Receiver Consumption Regimes")
    ax2.set_ylim(0, 206)
    style_ax(ax2)
    ax2.legend(loc="upper left", frameon=True, facecolor="white", fontsize=5.9)

    out_pdf = os.path.join(OUT_DIR, "fig3_ordering_and_doorbell.pdf")
    fig.savefig(out_pdf)
    plt.close(fig)
    print("Wrote", out_pdf)


# =====================================================================
# Figure 4: Pacing Recovery & Micro-Burst Phase Diagram (MRV-196)
# =====================================================================
def make_fig4_pacing_recovery():
    r196 = [r for r in load_jsonl("mrv196-2026-11-25/runs.jsonl") if r.get("accepted")]
    b196 = defaultdict(list)
    for r in r196: b196[r["label"]].append(r)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 2.65), gridspec_kw={"width_ratios": [1.22, 0.98]})
    plt.subplots_adjust(wspace=0.52)

    cases = [
        ("Idle",            "t6c__idle",                 "llc__idle"),
        ("1c BAR4\n175M",   "t6c__bar4_1c_max_b512",     "llc__bar4_1c_max_b512"),
        ("8c Raw\nb=512",   "t6c__bar4_8c_max_b512",     "llc__bar4_8c_max_b512"),
        ("8c Rate\n165M",   "t6c__bar4_8c_165m_b512",    "llc__bar4_8c_165m_b512"),
        ("8c Paced\nb=128", "t6c__bar4_8c_165m_b128",    "llc__bar4_8c_165m_b128"),
        ("DMA\n16M 1c",     "t6c__dpi_pull_16m_1c_160m", "llc__dpi_pull_16m_1c_160m"),
        ("DMA\n256M 4c",    "t6c__dpi_pull_256m_4c_160m","llc__dpi_pull_256m_4c_160m"),
    ]
    bw_vals  = [med([r["stream"]["mean_gb_s"] for r in b196[tk]]) for _, tk, _ in cases]
    p99_vals = [med([r["chase"]["p99_ns"] for r in b196[lk]]) for _, _, lk in cases]
    x = np.arange(len(cases))
    cols = [C_GRAY, C_BLUE, C_RED, C_ORANGE, C_TEAL, C_PURPLE, C_PURPLE]

    bars = ax1.bar(x, bw_vals, width=0.56, color=cols, edgecolor="#2C3E50", linewidth=0.6, alpha=0.92, label="6c STREAM (GB/s)")
    for bar, v in zip(bars, bw_vals):
        ax1.text(bar.get_x() + bar.get_width()/2, v + 1.4, f"{v:.1f}",
                 ha="center", va="bottom", fontsize=5.8, fontweight="bold")

    ax1.set_xticks(x)
    ax1.set_xticklabels([c[0] for c in cases], fontsize=5.7)
    ax1.set_ylabel("6c STREAM Triad (GB/s)")
    ax1.set_title("(a) Pacing vs. Unpaced & Host DMA")
    ax1.set_ylim(0, 64)
    style_ax(ax1)

    ax1r = ax1.twinx()
    ax1r.spines["top"].set_visible(False)
    ax1r.plot(x, p99_vals, "--D", color="#900C3F", markersize=4.0, linewidth=1.25, label="L3 p99 (ns)")
    ax1r.set_yscale("log")
    ax1r.set_ylabel("16 MiB L3 p99 (ns)", color="#900C3F", labelpad=3)
    ax1r.tick_params(axis="y", labelcolor="#900C3F")
    ax1r.set_ylim(18, 1500)
    ax1r.annotate("601 ns\n(20x)", xy=(2, p99_vals[2]), xytext=(2.45, 760),
                  fontsize=5.8, fontweight="bold", color="#900C3F")
    ax1r.annotate("29.3 ns\n(0% loss)", xy=(4, p99_vals[4]), xytext=(4.85, 76),
                  fontsize=5.5, fontweight="bold", color=C_TEAL,
                  bbox=dict(boxstyle="round,pad=0.18", fc="white", ec=C_TEAL, lw=0.65, alpha=0.95),
                  arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=0.8))

    # Panel B
    cores_b512 = [1, 2, 4, 6, 8]
    cha_b512 = [med([r["cha"]["occ_entries"] for r in b196[f"llc__bar4_{c}c_max_b512"]]) for c in cores_b512]
    cores_b128 = [1, 4, 6, 8]
    cha_b128 = [med([r["cha"]["occ_entries"] for r in b196[f"llc__bar4_{c}c_max_b128"]]) for c in cores_b128]
    cha_8c_165_b512 = med([r["cha"]["occ_entries"] for r in b196["llc__bar4_8c_165m_b512"]])
    cha_8c_165_b128 = med([r["cha"]["occ_entries"] for r in b196["llc__bar4_8c_165m_b128"]])

    ax2.axhspan(330, 430, color=C_CLIFF, alpha=0.65, zorder=1)
    ax2.text(3.6, 380, "330-430 Cliff", fontsize=6.0, fontweight="bold", color=C_RED, ha="center", va="center")

    ax2.plot(cores_b512, cha_b512, "-s", color=C_RED, label="Max rate, b=512")
    ax2.plot(cores_b128, cha_b128, "-o", color=C_ORANGE, label="Max rate, b=128")
    ax2.scatter([8], [cha_8c_165_b512], marker="X", s=50, color=C_PURPLE, zorder=6, label="8c 165M, b=512 (535)")
    ax2.scatter([8], [cha_8c_165_b128], marker="*", s=90, color=C_TEAL, zorder=6, label="8c 165M, b=128 (114)")

    ax2.annotate("Paced:\nCHA=114", xy=(8, cha_8c_165_b128), xytext=(4.8, 82),
                 fontsize=6.0, fontweight="bold", color=C_TEAL,
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=0.9))

    ax2.set_xlabel("Concurrent BAR4 Push Cores")
    ax2.set_ylabel("CHA TOR Occupancy (entries)", labelpad=3)
    ax2.set_title("(b) Micro-Burst (b) x Rate Control")
    ax2.set_xlim(0.5, 8.7)
    ax2.set_ylim(0, 720)
    ax2.set_xticks([1, 2, 4, 6, 8])
    style_ax(ax2)
    ax2.legend(loc="upper left", frameon=True, facecolor="white", framealpha=0.94, fontsize=5.7)

    out_pdf = os.path.join(OUT_DIR, "fig4_pacing_recovery.pdf")
    fig.savefig(out_pdf)
    plt.close(fig)
    print("Wrote", out_pdf)


# =====================================================================
# Figure 5: End-to-End 100GbE Application Payoff (MRV-195 & MRV-206)
# =====================================================================
def make_fig5_e2e_apps():
    r195 = [r for r in load_jsonl("mrv195-2026-11-25/runs.jsonl") if r.get("verified") and r.get("part") != "part0_idle_cal"]
    r206 = [r for r in load_jsonl("mrv206-2026-11-26/runs.jsonl") if r.get("verified")]
    with open(os.path.join(LOG_BASE, "mrv206-2026-11-26/env.json")) as f:
        env206 = json.load(f)
    idle_aw206 = env206["idle_cal"]["app_work_kps"]

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(7.2, 2.45), gridspec_kw={"width_ratios": [1.12, 1.05, 0.98]})
    plt.subplots_adjust(wspace=0.40)

    def sat195(pol, wl):
        return med([r["client"]["achieved_mrps"] for r in r195
                    if r["part"] == "part1_sat" and r["policy"] == pol and r["workload"] == wl and r["key_dist"] == "zipf"])
    def sat206(pol, wl):
        return med([r["client"]["achieved_mrps"] for r in r206
                    if r["section"] == "sat" and r["policy"] == pol and r["workload"] == wl and r["key_dist"] == "zipf"])

    wls = ["KV\n64B", "KV\n256B", "KV\nMix", "MC\n8B", "MC\nETC"]
    sdp_v = [sat195("sdp_fair", "get64"), sat195("sdp_fair", "get256"), sat195("sdp_fair", "mix"),
             sat206("sdp_fair", "get8"),  sat206("sdp_fair", "etc_mix")]
    dir_v = [sat195("direct_safe", "get64"), sat195("direct_safe", "get256"), sat195("direct_safe", "mix"),
             sat206("direct_safe", "get8"),  sat206("direct_safe", "etc_mix")]

    x = np.arange(len(wls))
    w = 0.36
    ax1.bar(x - w/2, sdp_v, width=w, color=C_GRAY, edgecolor="#2C3E50", linewidth=0.55, label="SDP fair-full")
    ax1.bar(x + w/2, dir_v, width=w, color=C_TEAL, edgecolor="#2C3E50", linewidth=0.55, label="DirectPush")
    for i, (s, d) in enumerate(zip(sdp_v, dir_v)):
        gain = (d - s) / s * 100.0
        ax1.text(i + w/2, d + 0.45, f"+{gain:.0f}%", ha="center", va="bottom",
                 fontsize=5.8, fontweight="bold", color=C_TEAL)

    ax1.axvline(2.5, color="#B0BEC5", linestyle="--", linewidth=0.8)
    ax1.set_xticks(x)
    ax1.set_xticklabels(wls, fontsize=6.1)
    ax1.set_ylabel("Single-Core Throughput (Mrps)")
    ax1.set_title("(a) 100GbE Saturation")
    ax1.set_ylim(0, 22.0)
    style_ax(ax1)
    ax1.legend(loc="upper right", frameon=True, facecolor="white", fontsize=5.8)

    # Panel B
    def get_curve206(pol):
        by_r = defaultdict(list)
        for r in r206:
            if r["section"] == "poisson" and r["policy"] == pol and r["workload"] == "etc_mix" and r["key_dist"] == "zipf":
                by_r[r["rate_krps"]].append(r)
        xs, ys = [], []
        for rk in sorted(by_r):
            xs.append(rk / 1000.0)
            ys.append(med([r["client"]["small_p99_us"] for r in by_r[rk]]))
        return xs, ys

    xs_sdp, ys_sdp = get_curve206("sdp_fair")
    xs_pk,  ys_pk  = get_curve206("direct_peek")
    xs_sf,  ys_sf  = get_curve206("direct_safe")

    ax2.plot(xs_sdp, ys_sdp, "-s", color=C_GRAY, label="SDP fair-full")
    ax2.plot(xs_pk,  ys_pk,  "--^", color=C_BLUE, label="DirectPush (peek)")
    ax2.plot(xs_sf,  ys_sf,  "-o", color=C_TEAL, label="DirectPush (safe)")
    ax2.annotate("+28.6%\nknee", xy=(2.7, ys_sf[xs_sf.index(2.7)]), xytext=(2.05, 11.2),
                 fontsize=6.0, fontweight="bold", color=C_TEAL,
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=0.85))
    ax2.set_xlabel("Offered Load (Mrps, Poisson)")
    ax2.set_ylabel("Small-Req p99 (us)")
    ax2.set_title("(b) Memcached ETC Mix")
    ax2.set_xlim(0.1, 3.15)
    ax2.set_ylim(5, 34)
    style_ax(ax2)
    ax2.legend(loc="upper left", frameon=True, facecolor="white", fontsize=5.7)

    # Panel C
    def spare206(pol, wl):
        rs = [r for r in r206 if r["section"] == "spare_cpu" and r["policy"] == pol and r["workload"] == wl and r["key_dist"] == "zipf"]
        return 100.0 * med([r["host"]["app_work_kps"] for r in rs]) / idle_aw206

    def spare195(pol, wl):
        rs = [r for r in r195 if r["part"] == "part3a_appwork" and r["policy"] == pol and r["workload"] == wl and r["key_dist"] == "zipf"]
        return 100.0 * (1.0 - med([r["aw_cpu_frac"] for r in rs]))

    c_labels = ["KV 64B\n@4.5M", "KV Mix\n@2.5M", "MC 8B\n@1.5M", "MC ETC\n@1.2M"]
    sp_sdp = [spare195("sdp_fair", "get64"), spare195("sdp_fair", "mix"),
              spare206("sdp_fair", "get8"),  spare206("sdp_fair", "etc_mix")]
    sp_dir = [spare195("direct_safe", "get64"), spare195("direct_safe", "mix"),
              spare206("direct_safe", "get8"),  spare206("direct_safe", "etc_mix")]

    x3 = np.arange(len(c_labels))
    ax3.bar(x3 - w/2, sp_sdp, width=w, color=C_GRAY, edgecolor="#2C3E50", linewidth=0.55, label="SDP")
    ax3.bar(x3 + w/2, sp_dir, width=w, color=C_NAVY, edgecolor="#2C3E50", linewidth=0.55, label="DirectPush")
    for i, (s, d) in enumerate(zip(sp_sdp, sp_dir)):
        ax3.text(i + w/2, d + 2.0, f"+{d-s:.0f}pp", ha="center", va="bottom",
                 fontsize=5.6, fontweight="bold", color=C_NAVY)

    ax3.set_xticks(x3)
    ax3.set_xticklabels(c_labels, fontsize=5.6)
    ax3.set_ylabel("Free Host CPU Headroom (%)")
    ax3.set_title("(c) Spare CPU Headroom")
    ax3.set_ylim(0, 96)
    style_ax(ax3)
    ax3.legend(loc="upper right", ncol=2, frameon=True, facecolor="white", fontsize=5.6)

    out_pdf = os.path.join(OUT_DIR, "fig5_e2e_apps.pdf")
    fig.savefig(out_pdf)
    plt.close(fig)
    print("Wrote", out_pdf)


if __name__ == "__main__":
    make_fig0_architecture()
    make_fig1_host_tor_collapse()
    make_fig2_device_self_collapse()
    make_fig3_ordering_and_doorbell()
    make_fig4_pacing_recovery()
    make_fig5_e2e_apps()
    print("All 6 figures regenerated successfully.")
