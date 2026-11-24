"""MRV-172 data-driven plotting script (MRV-34).

Reads raw verified `n = 3` runs exclusively from `logs/mrv172-2026-11-24/runs.jsonl`
and generates 3 multi-panel charts (PNG + SVG) using the shared `mrvplot` style:
  1. `mrv-172-stage0-stage1`: Stage 0 CN106 eth0 RX ceiling & knee + Stage 1 PCIe C2H measured rates
  2. `mrv-172-stage2-e2e`   : Stage 2 full 100GbE E2E 1-core saturn RX (DirectPush vs SDP ism1 vs SDP rpm1)
  3. `mrv-172-accounting-ablation`: Per-segment packet conservation waterfall + SDP RX single-variable ablation

Run:
    uv run --with matplotlib python docs/figures/mrv-172-plots.py
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv172-2026-11-24/runs.jsonl"

C_DP = "#1f5aa6"      # Deep blue: DirectPush
C_ISM = "#d95f02"     # Orange: SDP official (ism_enable=1, rx_poll_mem=0)
C_RPM = "#7570b3"     # Purple: SDP requested (ism_enable=1, rx_poll_mem=1, DDIO rxd=512)
C_WIRE = "#2ca02c"    # Green: 100GbE wire / RPM0 MAC
C_NIX = "#c0392b"     # Red: CN106 NIX0 hardware ceiling
C_GRAY = "#6e7781"


def load_groups():
    groups = defaultdict(list)
    with open(RUNS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if not r.get("verified", False):
                continue
            groups[r["cfg"]].append(r)
    return groups


def med_minmax(rows, fn):
    vals = [fn(r) for r in rows]
    return statistics.median(vals), min(vals), max(vals)


def plot_stage0_stage1(g):
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16.2, 5.4))
    # --- Panel A: Stage 0 64 B L2 Core Scaling (RPM0 vs NIX0 vs SW) ---
    cores = [1, 2, 4, 6, 8, 12, 16]
    rpm_m, nix_m, sw_m, sw_lo, sw_hi = [], [], [], [], []
    for c in cores:
        rows = g[f"s0_uc64_c{c}"]
        rpm_m.append(med_minmax(rows, lambda r: r["rates"]["card_rpm_ok_mpps"])[0])
        nix_m.append(med_minmax(rows, lambda r: r["rates"]["card_nix_good_mpps"])[0])
        m, lo, hi = med_minmax(rows, lambda r: r["rates"]["card_eth_rx_mpps"])
        sw_m.append(m); sw_lo.append(m - lo); sw_hi.append(hi - m)

    ax1.axhline(148.81, color=C_WIRE, ls="--", lw=1.3, label="100GbE 64B 理论线速 (148.81M)")
    ax1.plot(cores, rpm_m, "^--", color=C_WIRE, lw=1.5, ms=5, label="CN106 RPM0 MAC 实收")
    ax1.plot(cores, nix_m, "s-", color=C_NIX, lw=2.0, ms=5.5, label="CN106 NIX0 硬件交付 (111.1M 顶)")
    ax1.errorbar(cores, sw_m, yerr=[sw_lo, sw_hi], fmt="o-", color=C_DP, lw=2.0, ms=5.5, capsize=3, label="CN106 软件验真收包")
    if "s0_uc64_c16_notouch" in g:
        nt = med_minmax(g["s0_uc64_c16_notouch"], lambda r: r["rates"]["card_eth_rx_mpps"])[0]
        ax1.scatter([16], [nt], marker="D", s=55, color="#111111", zorder=6, label=f"C=16 不碰载荷 ({nt:.1f}M)")
    ax1.annotate(
        f"RPM→NIX 硬件顶\n{nix_m[-1]:.2f} Mpps\n(9.00 sclk/包)",
        xy=(12, nix_m[5]), xytext=(5.5, 122),
        arrowprops=dict(arrowstyle="->", color=C_NIX, lw=1.2),
        fontsize=8.5, color=C_NIX, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=C_NIX, alpha=0.92),
    )
    ax1.set_title("A. Stage 0：64 B L2 核数扩展（纯网线 RX）", fontsize=10.5, fontweight="bold")
    ax1.set_xlabel("CN106 收包核数 C（cpus 4..）")
    ax1.set_ylabel("收包速率（Mpps）")
    ax1.set_xticks(cores)
    ax1.set_ylim(0, 162)
    ax1.legend(loc="lower right", fontsize=7.5, framealpha=0.92)

    # --- Panel B: Stage 0 Frame-Size Knee (L2 bytes -> Wire Gbps & Mpps) ---
    knee_cfgs = [
        ("s0_uc64_c16",   64),
        ("s0_uc94_c12",   94),
        ("s0_ud102_c12", 102),
        ("s0_ud110_c12", 110),
        ("s0_uc134_c12", 134),
        ("s0_ud254_c8",  254),
        ("s0_ud326_c8",  326),
        ("s0_ud1094_c4", 1094),
    ]
    xs_sz, wire_g, mpps_v = [], [], []
    for cfg, l2 in knee_cfgs:
        if cfg not in g:
            continue
        rows = g[cfg]
        mp = med_minmax(rows, lambda r: r["rates"]["card_eth_rx_mpps"])[0]
        wg = mp * (l2 + 20) * 8.0 / 1000.0
        xs_sz.append(l2); wire_g.append(wg); mpps_v.append(mp)

    l2_grid = np.linspace(64, 1100, 300)
    env_g = np.minimum(100.0, 111.111 * (l2_grid + 20) * 8.0 / 1000.0)
    ax2.plot(l2_grid, env_g, "--", color=C_NIX, lw=1.4, label="111.11 Mpps × 100GbE 理论包络")
    ax2.plot(xs_sz, wire_g, "o-", color=C_DP, lw=2.2, ms=6, label="CN106 实测软件收包线速")
    ax2.axvline(92.5, color=C_GRAY, ls=":", lw=1.2)
    ax2.annotate("RX 拐点\n92.5 B L2\n(TX 为 106 B)", xy=(94, wire_g[1]), xytext=(160, 82),
                 arrowprops=dict(arrowstyle="->", color=C_GRAY, lw=1.1),
                 fontsize=8.5, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=C_GRAY))
    ax2.set_xscale("log")
    ax2.set_xticks([64, 94, 110, 134, 254, 326, 1094])
    ax2.get_xaxis().set_major_formatter(plt.ScalarFormatter())
    ax2.set_xticklabels(["64", "94", "110", "134", "254", "326", "1094"], fontsize=8)
    ax2.set_title("B. Stage 0：帧长拐点（94 B 以上满 100G）", fontsize=10.5, fontweight="bold")
    ax2.set_xlabel("L2 帧长（字节，含 4 B FCS，对数轴）")
    ax2.set_ylabel("CN106 实收等效线速（Gbps）")
    ax2.set_ylim(68, 105)
    ax2.legend(loc="lower right", fontsize=8, framealpha=0.92)

    # --- Panel C: Stage 1 Pure PCIe C2H into 1 saturn core (cpu40) ---
    s1_items = [
        ("DP WC\n64B (C=4)",     "s1_dp_wc64_c4",           C_DP,  "mpps", ""),
        ("SDP ISM1\n64B (C=4)",  "s1_sdp_64_ism1_2048_c4",  C_ISM, "mpps", ""),
        ("SDP RPM1*\n64B (512)", "s1_sdp_64_rpm1_512_c4",   C_RPM, "mpps", "*"),
        ("DP DMA\n256B",         "s1_dp_dma256_c4",         C_DP,  "gbps", ""),
        ("DP DMA\n1 KiB",        "s1_dp_dma1024_c4",        C_DP,  "gbps", ""),
        ("DP DMA\n4 KiB",        "s1_dp_dma4096_c4",        C_DP,  "gbps", ""),
        ("SDP ISM1†\n1 KiB",     "s1_sdp_1024_ism1_2048_c4",C_ISM, "gbps", "†"),
        ("SDP RPM1†\n1 KiB",     "s1_sdp_1024_rpm1_512_c4", C_RPM, "gbps", "†"),
    ]
    labels, vals, lo_e, hi_e, cols, ann = [], [], [], [], [], []
    for lbl, cfg, col, mode, tag in s1_items:
        if cfg not in g:
            continue
        rows = g[cfg]
        m, lo, hi = med_minmax(rows, lambda r: r["rates"]["host_pay_gbps"])
        mp = med_minmax(rows, lambda r: r["rates"]["host_rx_mpps"])[0]
        labels.append(lbl); vals.append(m); lo_e.append(m - lo); hi_e.append(hi - m); cols.append(col)
        ann.append(f"{mp:.1f}M{tag}\n({m:.1f}G)" if mode == "mpps" else f"{m:.1f}G{tag}")

    x = np.arange(len(labels))
    bars = ax3.bar(x, vals, color=cols, yerr=[lo_e, hi_e], capsize=3, width=0.68, edgecolor="#222222", lw=0.6)
    ax3.axhline(100.0, color=C_WIRE, ls="--", lw=1.2, label="100 Gbps 参考线")
    for b, a in zip(bars, ann):
        ax3.text(
            b.get_x() + b.get_width() / 2, b.get_height() + 2.0, a,
            ha="center", va="bottom", fontsize=7.2, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.10", fc="white", ec="none", alpha=0.88),
        )

    # Callout for *64B SDP RPM rdtsc artifact
    ax3.annotate(
        "*64B RPM 含置外\nrdtsc (+54.5 cyc)",
        xy=(2.0, 31.5), xytext=(1.55, 76.0),
        ha="center", va="bottom",
        arrowprops=dict(arrowstyle="->", color=C_RPM, lw=1.1),
        fontsize=7.2, color="#3b2f6b", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.20", fc="#f5f3ff", ec=C_RPM, lw=0.8),
    )

    # Callout bracket for †SDP 1 KiB card-side copy + TLP limit (not host 1-core ceiling)
    iio_ism = med_minmax(g["s1_sdp_1024_ism1_2048_c4"], lambda r: r["host_pmu"]["iio1_inb_wr_gbps"])[0]
    iio_rpm = med_minmax(g["s1_sdp_1024_rpm1_512_c4"], lambda r: r["host_pmu"]["iio1_inb_wr_gbps"])[0]
    ax3.plot([5.76, 5.76, 7.28, 7.28], [106.0, 109.5, 109.5, 106.0], color="#475569", lw=1.0)
    ax3.text(
        6.52, 111.5,
        f"†卡 4 核拷贝+TLP 顶\nIIO1 {iio_ism:.0f}–{iio_rpm:.0f}G，非主机顶",
        ha="center", va="bottom", fontsize=7.0, color="#334155", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.18", fc="#fff7ed", ec=C_ISM, lw=0.8),
    )

    ax3.set_title("C. Stage 1：纯 PCIe 卡→1 核主机实测", fontsize=10.5, fontweight="bold", pad=8)
    ax3.set_ylabel("saturn 单核实收有效载荷（Gbps）")
    ax3.set_xticks(x)
    ax3.set_xticklabels(labels, fontsize=7.3)
    ax3.set_ylim(0, 134)
    ax3.legend(loc="upper left", fontsize=7.8, framealpha=0.92)

    fig.suptitle("MRV-172 Stage 0（纯网线 RX）与 Stage 1（纯 PCIe 卡→1 核主机）逐级实测（n=3 中位数，cpu40 @ 3.90 GHz）",
                 fontsize=11.0, fontweight="bold", y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    png = mrvplot.HERE / "mrv-172-stage0-stage1.png"
    fig.savefig(png); fig.savefig(mrvplot.HERE / "mrv-172-stage0-stage1.svg")
    plt.close(fig)
    return png


def plot_stage2_e2e(g):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.8, 5.8))
    sizes = [
        ("64B",   "64 B L2\n(UC 0 B)"),
        ("110B",  "110 B L2\n(40B UD·cpp=1)"),
        ("134B",  "134 B L2\n(72B UC·cpp=2)"),
        ("326B",  "326 B L2\n(256B·DMA)"),
        ("1094B", "1094 B L2\n(1KiB·DMA)"),
        ("4096B", "4×1094 B\n(4KiB·DMA)"),
    ]
    x = np.arange(len(sizes))
    w = 0.26

    # Panel A: E2E saturn 1-core RX wire-equivalent Gbps (DirectPush vs SDP ism1 vs SDP rpm1)
    for off, prefix, label, color in [
        (-w, "s2_dp_",       "DirectPush (WC / DPI ToHost)", C_DP),
        (0,  "s2_sdp_ism1_", "SDP (ism=1, rpm=0, rxd=2048)", C_ISM),
        (w,  "s2_sdp_rpm1_", "SDP (ism=1, rpm=1, DDIO 512)", C_RPM),
    ]:
        meds, los, his, tops = [], [], [], []
        for key, _ in sizes:
            rows = g[f"{prefix}{key}"]
            m, lo, hi = med_minmax(rows, lambda r: r["rates"]["host_wire_gbps"])
            meds.append(m); los.append(m - lo); his.append(hi - m); tops.append(hi)
        bars = ax1.bar(x + off, meds, width=w, color=color, yerr=[los, his], capsize=2.5,
                       label=label, edgecolor="#222222", lw=0.6)
        for idx, (b, m, top) in enumerate(zip(bars, meds, tops)):
            dy = 4.8 if (off == 0 and top > 95.0) else 1.5
            tag = "*" if (idx == 0 and prefix == "s2_sdp_rpm1_") else ""
            ax1.text(b.get_x() + b.get_width() / 2, top + dy, f"{m:.1f}{tag}", ha="center", va="bottom",
                     fontsize=6.5, fontweight="bold", color=color,
                     bbox=dict(boxstyle="round,pad=0.05", fc="white", ec="none", alpha=0.85))
    ax1.axhline(100.0, color=C_WIRE, ls="--", lw=1.3, label="100GbE 物理线速 (100 Gbps)")
    ax1.plot([-0.42, 0.42], [74.67, 74.67], color=C_NIX, ls=":", lw=1.8, label="64 B L2 NIX0 RX 顶 (74.7G)")
    ax1.set_title("A. Stage 2 端到端：1 颗主机核实收线速带宽（Gbps）", fontsize=10.5, fontweight="bold", pad=8)
    ax1.set_ylabel("saturn 1 核实收等效 100GbE 线速（Gbps）")
    ax1.set_xticks(x)
    ax1.set_xticklabels([s[1] for s in sizes], fontsize=7.9)
    ax1.set_ylim(0, 118)
    ax1.legend(loc="lower right", fontsize=7.8, framealpha=0.92)

    # Panel B: Packet rate (Mpps) + small-pkt speedup / large-pkt HARDWARE PMU (DDIO, IMC, sb_stall)
    dp_mpps, ism_mpps, rpm_mpps = [], [], []
    dp_ddio, dp_imc, dp_sb = [], [], []
    ism_ddio, ism_imc, ism_sb = [], [], []
    for key, _ in sizes:
        dp_mpps.append(med_minmax(g[f"s2_dp_{key}"], lambda r: r["rates"]["host_rx_mpps"])[0])
        ism_mpps.append(med_minmax(g[f"s2_sdp_ism1_{key}"], lambda r: r["rates"]["host_rx_mpps"])[0])
        rpm_mpps.append(med_minmax(g[f"s2_sdp_rpm1_{key}"], lambda r: r["rates"]["host_rx_mpps"])[0])
        dp_ddio.append(med_minmax(g[f"s2_dp_{key}"], lambda r: r["host_pmu"]["ddio_hit_pct"])[0])
        dp_imc.append(med_minmax(g[f"s2_dp_{key}"], lambda r: r["host_pmu"]["imc_wr_gbps"])[0])
        dp_sb.append(med_minmax(g[f"s2_dp_{key}"], lambda r: r["host_pmu"]["sb_stall_pct"])[0])
        ism_ddio.append(med_minmax(g[f"s2_sdp_ism1_{key}"], lambda r: r["host_pmu"]["ddio_hit_pct"])[0])
        ism_imc.append(med_minmax(g[f"s2_sdp_ism1_{key}"], lambda r: r["host_pmu"]["imc_wr_gbps"])[0])
        ism_sb.append(med_minmax(g[f"s2_sdp_ism1_{key}"], lambda r: r["host_pmu"]["sb_stall_pct"])[0])

    ax2.bar(x - w, dp_mpps, width=w, color=C_DP, label="DirectPush", edgecolor="#222222", lw=0.6)
    ax2.bar(x,     ism_mpps, width=w, color=C_ISM, label="SDP (ism=1, rpm=0)", edgecolor="#222222", lw=0.6)
    ax2.bar(x + w, rpm_mpps, width=w, color=C_RPM, label="SDP (ism=1, rpm=1, DDIO 512)", edgecolor="#222222", lw=0.6)

    for i in range(len(sizes)):
        ax2.text(x[i] - w, dp_mpps[i] + 1.8, f"{dp_mpps[i]:.1f}M", ha="center", va="bottom", fontsize=7.4, fontweight="bold", color=C_DP)
        if i <= 2:
            r_ism = dp_mpps[i] / max(ism_mpps[i], 1e-6)
            r_rpm = dp_mpps[i] / max(rpm_mpps[i], 1e-6)
            rpm_tag = "*" if i == 0 else ""
            ax2.text(x[i] + 0.05, max(dp_mpps[i], ism_mpps[i]) + 10.5,
                     f"{r_ism:.2f}× ISM\n{r_rpm:.2f}× RPM{rpm_tag}", ha="center", va="bottom",
                     fontsize=7.2, color="#222222",
                     bbox=dict(boxstyle="round,pad=0.20", fc="#f7f9fc", ec="#cbd5e1", lw=0.6))

    # Hardware PMU callouts for large packets (replacing distorted unweighted busy_poll_pct)
    # 326 B (i=3)
    ax2.annotate(
        f"PMU · 326 B\nDP: DDIO {dp_ddio[3]:.0f}% · sb {dp_sb[3]:.1f}%\nISM: DDIO {ism_ddio[3]:.0f}% · sb {ism_sb[3]:.1f}%",
        xy=(3.0, 38.0), xytext=(2.95, 72.0),
        ha="center", va="bottom",
        arrowprops=dict(arrowstyle="->", color="#64748b", lw=0.9),
        fontsize=6.9, color="#1e293b",
        bbox=dict(boxstyle="round,pad=0.22", fc="#f8fafc", ec="#94a3b8", lw=0.7),
    )
    # 1 KiB (i=4)
    ax2.annotate(
        f"PMU · 1 KiB\nDP: DDIO {dp_ddio[4]:.0f}% · IMC {dp_imc[4]:.1f}G · sb {dp_sb[4]:.1f}%\nISM: DDIO {ism_ddio[4]:.0f}% · IMC {ism_imc[4]:.1f}G · sb {ism_sb[4]:.0f}%",
        xy=(4.0, 14.0), xytext=(4.12, 42.0),
        ha="center", va="bottom",
        arrowprops=dict(arrowstyle="->", color="#64748b", lw=0.9),
        fontsize=6.6, color="#1e293b",
        bbox=dict(boxstyle="round,pad=0.22", fc="#eff6ff", ec=C_DP, lw=0.7),
    )
    # 4 KiB (i=5) — curved arrow clears the 1 KiB box on the right
    ax2.annotate(
        f"PMU · 4 KiB (非未加权轮询比)\nDP: DDIO {dp_ddio[5]:.0f}% · IMC {dp_imc[5]:.1f}G · sb {dp_sb[5]:.1f}%\nISM: DDIO {ism_ddio[5]:.0f}% · IMC {ism_imc[5]:.1f}G · sb {ism_sb[5]:.0f}%",
        xy=(5.14, 14.0), xytext=(4.70, 79.0),
        ha="center", va="bottom",
        arrowprops=dict(arrowstyle="->", color="#64748b", lw=0.9, connectionstyle="arc3,rad=-0.26"),
        fontsize=6.6, color="#1e293b",
        bbox=dict(boxstyle="round,pad=0.22", fc="#eff6ff", ec=C_DP, lw=0.7),
    )

    ax2.set_title("B. Stage 2 端到端：1 颗主机核包率（Mpps）与大包硬件 PMU 对比", fontsize=10.5, fontweight="bold", pad=8)
    ax2.set_ylabel("saturn 1 核实收并验真包率（Mpps）")
    ax2.set_xticks(x)
    ax2.set_xticklabels([s[1] for s in sizes], fontsize=7.9)
    ax2.set_ylim(0, 148)
    ax2.legend(loc="upper right", fontsize=7.8, framealpha=0.92)

    fig.suptitle("MRV-172 Stage 2 完整双机 100GbE 单向收包：1 颗主机核实测上限（DirectPush vs 最优 SDP，bad=torn=0，n=3 中位数）",
                 fontsize=11.0, fontweight="bold", y=0.98)
    fig.text(
        0.5, 0.012,
        "注：*64 B SDP RPM 受 uc64 置外 rdtsc() (+54.5 cyc/包) 拖累（110/134 B 无此伪影反超 ISM 2048）；100.1G 含 +0.09% SQ 排空尾窗；110 B 为 40 B UD (cpp=1)，134 B 为 64–72 B 载荷锚点 (cpp=2)。",
        ha="center", va="bottom", fontsize=8.0, color="#334155",
    )
    fig.tight_layout(rect=[0, 0.045, 1, 0.93])
    png = mrvplot.HERE / "mrv-172-stage2-e2e.png"
    fig.savefig(png); fig.savefig(mrvplot.HERE / "mrv-172-stage2-e2e.svg")
    plt.close(fig)
    return png


def plot_accounting_ablation(g):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.4, 5.4))

    # Panel A: Per-hop accounting on 64 B (sat vs paced95M) + 110 B + 134 B + 1 KiB
    cases = [
        ("64B 饱和\n(DP)",       "s2_dp_64B"),
        ("64B 限速\n95M (DP)",   "s2_dp_64B_paced95M_zl"),
        ("64B 饱和\n(SDP ISM)",  "s2_sdp_ism1_64B"),
        ("64B 饱和\n(SDP RPM*)", "s2_sdp_rpm1_64B"),
        ("110B\n(40B·DP)",       "s2_dp_110B"),
        ("134B\n(72B·DP)",       "s2_dp_134B"),
        ("1 KiB\n(DP)",          "s2_dp_1094B"),
    ]
    x = np.arange(len(cases))
    w = 0.2
    m_tx, c_rpm, c_nix, h_rx = [], [], [], []
    for _, cfg in cases:
        rows = g[cfg]
        m_tx.append(med_minmax(rows, lambda r: r["rates"]["merc_phy_mpps"])[0])
        c_rpm.append(med_minmax(rows, lambda r: r["rates"]["card_rpm_ok_mpps"])[0])
        c_nix.append(med_minmax(rows, lambda r: r["rates"]["card_nix_good_mpps"])[0])
        h_rx.append(med_minmax(rows, lambda r: r["rates"]["host_rx_mpps"])[0])

    ax1.bar(x - 1.5 * w, m_tx,  w, color=C_WIRE, label="mercury TX PHY", edgecolor="#222", lw=0.5)
    ax1.bar(x - 0.5 * w, c_rpm, w, color="#66c2a5", label="CN106 RPM0 MAC RX", edgecolor="#222", lw=0.5)
    ax1.bar(x + 0.5 * w, c_nix, w, color=C_NIX, label="CN106 NIX0 硬件交付", edgecolor="#222", lw=0.5)
    ax1.bar(x + 1.5 * w, h_rx,  w, color=C_DP, label="saturn 1 核实收验真", edgecolor="#222", lw=0.5)
    for i in range(len(cases)):
        tag = "*" if i == 3 else ""
        ax1.text(x[i] + 1.5 * w, h_rx[i] + 2.0, f"{h_rx[i]:.1f}{tag}", ha="center", va="bottom", fontsize=7.3, fontweight="bold", color=C_DP)
    ax1.set_title("A. 逐段流量守恒对账：丢包究竟发生在哪一段？", fontsize=10.5, fontweight="bold")
    ax1.set_ylabel("速率（Mpps）")
    ax1.set_xticks(x)
    ax1.set_xticklabels([c[0] for c in cases], fontsize=7.9)
    ax1.set_ylim(0, 178)
    ax1.legend(loc="upper right", fontsize=7.8, framealpha=0.92)

    # Panel B: Single-variable SDP RX ablation + Host PCIe write-size PMU (Stage 1 64 B + testpmd ref)
    abl_items = [
        ("testpmd\nism=0",       "tp_rx_64B_ism0_2048",     "#94a3b8", True,  ""),
        ("testpmd\nism=1",       "tp_rx_64B_ism1_2048",     C_ISM,     True,  ""),
        ("testpmd\nrpm=1,512",   "tp_rx_64B_rpm1_512",      C_RPM,     True,  ""),
        ("SDP 验真*\nrpm=1,512", "s1_sdp_64_rpm1_512_c4",   C_RPM,     False, "*"),
        ("SDP 验真\nism=1,512",  "s1_sdp_64_ism1_512_c4",   "#f59e0b", False, ""),
        ("SDP 验真\nism=1,2048", "s1_sdp_64_ism1_2048_c4",  C_ISM,     False, ""),
        ("DirectPush\nWC 64B",   "s1_dp_wc64_c4",           C_DP,      False, ""),
    ]
    al, av, alo, ahi, ac, atxt = [], [], [], [], [], []
    for lbl, cfg, col, is_tp, tag in abl_items:
        if cfg not in g:
            continue
        rows = g[cfg]
        if is_tp:
            m, lo, hi = med_minmax(rows, lambda r: r["host_rx_mpps"])
            extra = ""
        else:
            m, lo, hi = med_minmax(rows, lambda r: r["rates"]["host_rx_mpps"])
            wsz = med_minmax(rows, lambda r: r["host_pmu"]["iio1_inb_wr_avg_bytes"])[0]
            extra = f"\n({wsz:.0f}B/wr)"
        al.append(lbl); av.append(m); alo.append(m - lo); ahi.append(hi - m); ac.append(col)
        atxt.append(f"{m:.1f}M{tag}{extra}")

    xa = np.arange(len(al))
    bars = ax2.bar(xa, av, color=ac, yerr=[alo, ahi], capsize=3, width=0.64, edgecolor="#222", lw=0.6)
    for b, t in zip(bars, atxt):
        ax2.text(b.get_x() + b.get_width() / 2, b.get_height() + 2.5, t, ha="center", va="bottom", fontsize=7.4, fontweight="bold")

    # Bracket over the 3 dpdk-testpmd bars explaining card 4-core txonly cap
    ax2.plot([-0.32, -0.32, 2.32, 2.32], [72.0, 77.0, 77.0, 72.0], color="#475569", lw=1.0)
    ax2.text(
        1.0, 81.0,
        "卡侧 4 核 testpmd txonly 发包顶\n(missed=0，非主机 1 核 RX 上限)",
        ha="center", va="bottom", fontsize=7.4, color="#334155", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.24", fc="#f8fafc", ec="#94a3b8", lw=0.8),
    )

    # Callout for *64B SDP RPM rdtsc artifact
    ax2.annotate(
        "*uc64 置外 rdtsc() (+54.5 cyc/包)\n110/134 B 无此伪影反超 ISM 2048",
        xy=(3.0, 56.0), xytext=(2.85, 134.0),
        ha="center", va="bottom",
        arrowprops=dict(arrowstyle="->", color=C_RPM, lw=1.1),
        fontsize=7.4, color="#3b2f6b", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.24", fc="#f5f3ff", ec=C_RPM, lw=0.8),
    )

    ax2.set_title("B. 64 B 单变量消融：SDP ism/rx_poll_mem/环深 vs DirectPush", fontsize=10.5, fontweight="bold")
    ax2.set_ylabel("saturn 1 核实收包率（Mpps）")
    ax2.set_xticks(xa)
    ax2.set_xticklabels(al, fontsize=7.7)
    ax2.set_ylim(0, 206)

    fig.suptitle("MRV-172 逐段对账守恒与 64 B SDP 单变量消融（Stage 1 含 dpdk-testpmd 参考，n=3 中位数）",
                 fontsize=11.0, fontweight="bold", y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    png = mrvplot.HERE / "mrv-172-accounting-ablation.png"
    fig.savefig(png); fig.savefig(mrvplot.HERE / "mrv-172-accounting-ablation.svg")
    plt.close(fig)
    return png


def main():
    g = load_groups()
    print(f"Loaded {sum(len(v) for v in g.values())} verified runs across {len(g)} configs from {RUNS_PATH}")
    p1 = plot_stage0_stage1(g)
    p2 = plot_stage2_e2e(g)
    p3 = plot_accounting_ablation(g)
    print("Generated:", p1, p2, p3)


if __name__ == "__main__":
    main()
