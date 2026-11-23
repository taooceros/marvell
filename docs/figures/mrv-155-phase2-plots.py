#!/usr/bin/env python3
"""Generate MRV-155 Phase 2 charts directly from raw logs/mrv155-2026-11-23/runs_phase2.jsonl.

Run with:
    uv run --with matplotlib python docs/figures/mrv-155-phase2-plots.py
"""

import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS_P2 = ROOT / "logs/mrv155-2026-11-23/runs_phase2.jsonl"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

C_DP = "#1f6feb"     # DirectPush blue
C_SDP = "#d95f02"    # Optimal SDP orange
C_CEIL = "#4a5568"   # Hardware ceiling dark grey
C_GRAY = "#9aa5b1"   # Light grey
C_GREEN = "#2b8a3e"  # Accent green for 100G wire-rate line


def load_runs(path: pathlib.Path):
    by_cfg = {}
    all_rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        all_rows.append(r)
        if r.get("part") == "part3_content_check":
            assert r["verified"] and r["result"]["ok"] == 1000 and r["result"]["bad"] == 0
        elif r.get("part") == "part1_stage1_testpmd_ref":
            assert r["verified"] and r["card_rx_missed"] == 0
        else:
            c = r["card"]
            m = r["mercury"]
            hp = r["host_pmu"]
            tp = r["tad_pmu"]
            assert c["bad"] == 0 and c["torn"] == 0 and c["dma_failed"] == 0 and c["tx_drops"] == 0, r["cfg"]
            if r["spec"]["stage"] in (1, 2) and r["spec"]["kind"] == "direct":
                assert c["lost"] == 0, f"BAR4 lost in {r['cfg']}"
            assert m["bad"] == 0 and m["cqe_err"] == 0, f"mercury bad in {r['cfg']}"
            assert hp["min_running_pct"] >= 99.9 and tp["min_running_pct"] >= 99.9, f"PMU mux in {r['cfg']}"
            if r["host"] is not None:
                assert hp["cpu40_ghz"] >= 3.80, f"cpu40_ghz low in {r['cfg']}"
        by_cfg.setdefault(r["cfg"], []).append(r)
    return by_cfg, all_rows


def med(vals):
    return statistics.median(vals)


def phy_mpps(rows):
    return med([r["mercury"]["phy"]["rx_mpps"] for r in rows])


def phy_wire(rows):
    return med([r["mercury"]["phy"]["wire_gbps"] for r in rows])


def merc_rx_mpps(rows):
    return med([r["mercury"]["rx_mpps"] for r in rows])


def merc_wire(rows):
    return med([r["mercury"]["wire_gbps"] for r in rows])


def s1_card_mpps(rows):
    return med([r["card"]["pkts_in"] / r["host"]["elapsed_s"] / 1e6 for r in rows])


def save_clean(fig, name):
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.92])
    png = mrvplot.HERE / f"{name}.png"
    fig.savefig(png, bbox_inches="tight", pad_inches=0.16)
    fig.savefig(mrvplot.HERE / f"{name}.svg", bbox_inches="tight", pad_inches=0.16)
    plt.close(fig)
    return png


def plot_fig1_stage_roofline(by):
    """Fig 1: Stage-by-Stage 64 B ceiling decomposition + CN106 NIX0/RPM0 roofline knee."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.1), gridspec_kw={"width_ratios": [1.18, 1.0]})

    nix_ceil = phy_mpps(by["s0_raw64_reuse_c4"])
    labels = [
        "Stage 1\n1H→BAR4\nDirectPush",
        "Stage 0\nCN106 NIX0\n硬件硅顶",
        "Stage 2 E2E\n1H DirectPush\n→mercury",
        "Stage 1\n1H SDP 裸发\ntestpmd ISM=1",
        "Stage 1\n1H SDP 验真\nISM=1",
        "Stage 2 E2E\n1H SDP 验真\n→mercury",
    ]
    vals = [
        s1_card_mpps(by["s1_dp_64B_c4_normal"]),
        nix_ceil,
        phy_mpps(by["s2_dp_raw64_c4"]),
        med([r["card_rx_mpps"] for r in by["tp_sdp_1H_64B_ism1"]]),
        s1_card_mpps(by["s1_sdp_64B_ism1_c4"]),
        phy_mpps(by["s2_sdp_raw64_ism1_c4"]),
    ]
    colors = [C_DP, C_CEIL, C_DP, "#f0a060", C_SDP, C_SDP]
    hatches = ["", "//", "", "//", "", ""]
    x = np.arange(len(labels))
    bars = ax1.bar(x, vals, width=0.56, color=colors, edgecolor="#222222", linewidth=0.8)
    for b, h in zip(bars, hatches):
        if h:
            b.set_hatch(h)

    ax1.axhline(148.81, color=C_GREEN, linestyle="--", linewidth=1.3, label="100GbE 64 B 理论线速 (148.81 Mpps)")
    ax1.axhline(nix_ceil, color=C_CEIL, linestyle=":", linewidth=1.2,
                label=f"CN106 NIX0/RPM0 硬件顶 ({nix_ceil:.1f} Mpps)")

    for xi, v in zip(x, vals):
        ax1.text(xi, v + 3.2, f"{v:.1f}", ha="center", va="bottom", fontsize=9.5, fontweight="bold")

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=8.3)
    ax1.set_ylabel("64 B L2 帧包率 (Mpps)")
    ax1.set_ylim(0, 195)
    ax1.set_title("A. 64 B 单向逐级拆解（严格 1 颗 saturn 主机核 cpu40）", fontsize=10.4, fontweight="bold")
    ax1.legend(loc="upper right", fontsize=8.0, frameon=True, facecolor="white", framealpha=0.95)

    sizes = [64, 84, 100, 106, 128, 256, 512, 1024, 1500]
    s0_wire = [phy_wire(by[f"s0_raw{s}_percore_c4"]) for s in sizes]

    ax2.plot(sizes, s0_wire, "o-", color=C_CEIL, linewidth=2.0, markersize=5.8, label="Stage 0 CN106 NIX0 线速上限 (C=4)")
    s2_sizes = [64, 110, 128, 256]
    s2_dp_wire = [
        phy_wire(by["s2_dp_raw64_c4"]),
        phy_wire(by["s2_dp_raw110_c4"]),
        phy_wire(by["s2_dp_raw128_c4"]),
        phy_wire(by["s2_dp_raw256_c6"]),
    ]
    s2_sdp_wire = [phy_wire(by[f"s2_sdp_raw{s}_ism1_c4"]) for s in s2_sizes]
    ax2.plot(s2_sizes, s2_dp_wire, "s-", color=C_DP, linewidth=2.0, markersize=6.2, label="Stage 2 E2E 1H DirectPush (C=4; 256B C=6)")
    ax2.plot(s2_sizes, s2_sdp_wire, "^--", color=C_SDP, linewidth=1.8, markersize=6.0, label="Stage 2 E2E 1H 最优 SDP ISM=1 (C=4)")

    ax2.axhline(100.0, color=C_GREEN, linestyle="--", linewidth=1.2, alpha=0.85)
    ax2.axvline(106, color=C_GRAY, linestyle=":", linewidth=1.2)
    ax2.annotate(
        "硬件拐点 106 B L2\n(100 Mpps × 126 B = 100 G)",
        xy=(106, 100.0), xytext=(220, 106.0),
        arrowprops=dict(arrowstyle="->", color="#444444", lw=1.0),
        fontsize=8.0, color="#333333",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#cccccc", alpha=0.95),
    )
    ax2.annotate(
        f"64 B UDP 载荷 (110 B L2)\n1H DirectPush: {phy_wire(by['s2_dp_raw110_c4']):.2f} Gbps",
        xy=(110, phy_wire(by["s2_dp_raw110_c4"])), xytext=(190, 50),
        arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.1),
        fontsize=8.2, color=C_DP, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=C_DP, alpha=0.95),
    )

    ax2.set_xscale("log", base=2)
    ax2.set_xticks([64, 128, 256, 512, 1024, 1500])
    ax2.set_xticklabels(["64", "128", "256", "512", "1024", "1500"])
    ax2.set_xlabel("以太网 L2 帧长 (Bytes，含 4 B FCS)")
    ax2.set_ylabel("mercury PHY 实测物理线速 (Gbps)")
    ax2.set_ylim(0, 122)
    ax2.set_title("B. CN106 NIX0 100 Mpps 拐点与端到端线速曲线", fontsize=10.4, fontweight="bold")
    ax2.legend(loc="lower right", fontsize=7.6, frameon=True, facecolor="white", framealpha=0.95)

    fig.suptitle(
        "MRV-155 Phase 2：1 颗主机核单向 64 B 冲 100 Gbps 的逐级拆解（n = 3 中位数，cpu40 3.90 GHz / Uncore 2.20 GHz）",
        fontsize=11.0, fontweight="bold", y=0.97,
    )
    return save_clean(fig, "mrv-155-stage-roofline")


def plot_fig2_unidir_e2e(by):
    """Fig 2: Full E2E unidirectional bandwidth across Raw L2/UDP and RoCEv2 UD granularities."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.2))

    raw_sz = [64, 110, 128, 256]
    raw_lbl = ["64 B L2\n(18 B 载荷)", "110 B L2\n(64 B UDP 载荷)", "128 B L2\n(82 B 载荷)", "256 B L2\n(210 B 载荷)"]
    dp_w = [
        phy_wire(by["s2_dp_raw64_c4"]),
        phy_wire(by["s2_dp_raw110_c4"]),
        phy_wire(by["s2_dp_raw128_c4"]),
        phy_wire(by["s2_dp_raw256_c6"]),
    ]
    sdp_w = [phy_wire(by[f"s2_sdp_raw{s}_ism1_c4"]) for s in raw_sz]
    dp_m = [
        phy_mpps(by["s2_dp_raw64_c4"]),
        phy_mpps(by["s2_dp_raw110_c4"]),
        phy_mpps(by["s2_dp_raw128_c4"]),
        phy_mpps(by["s2_dp_raw256_c6"]),
    ]
    sdp_m = [phy_mpps(by[f"s2_sdp_raw{s}_ism1_c4"]) for s in raw_sz]

    x = np.arange(len(raw_sz))
    w = 0.36
    ax1.bar(x - w / 2, dp_w, w, color=C_DP, edgecolor="#222", linewidth=0.8, label="1H DirectPush (C=4; 256B C=6)")
    ax1.bar(x + w / 2, sdp_w, w, color=C_SDP, edgecolor="#222", linewidth=0.8, label="1H 最优 SDP (ISM=1, C=4)")
    ax1.axhline(100.0, color=C_GREEN, linestyle="--", linewidth=1.2, label="100GbE 物理线速 (100 Gbps)")

    for i in range(len(raw_sz)):
        ax1.text(x[i] - w / 2, dp_w[i] + 2.0, f"{dp_w[i]:.1f}G\n({dp_m[i]:.1f}M)", ha="center", va="bottom", fontsize=7.9, color=C_DP, fontweight="bold")
        ax1.text(x[i] + w / 2, sdp_w[i] + 2.0, f"{sdp_w[i]:.1f}G\n({sdp_m[i]:.1f}M)", ha="center", va="bottom", fontsize=7.9, color=C_SDP)

    ax1.set_xticks(x)
    ax1.set_xticklabels(raw_lbl, fontsize=8.4)
    ax1.set_ylabel("mercury PHY 实测物理线速 (Gbps)")
    ax1.set_ylim(0, 142)
    ax1.set_title("A. 标准以太网 / UDP 帧（1H saturn → CN106 → mercury）", fontsize=10.3, fontweight="bold")
    ax1.legend(loc="upper center", ncol=2, fontsize=7.7, frameon=True, facecolor="white", framealpha=0.96)

    roce_sz = [64, 256, 1024, 4096]
    roce_lbl = ["64 B 载荷\n(134 B L2)", "256 B 载荷\n(326 B L2)", "1 KiB 载荷\n(DPI DMA)", "4 KiB 载荷\n(4×1K DPI)"]
    dp_rw = [phy_wire(by[f"s2_dp_roce{s}_c4"]) for s in roce_sz]
    dp_ru = [merc_wire(by[f"s2_dp_roce{s}_c4"]) for s in roce_sz]
    sdp_rw = [phy_wire(by[f"s2_sdp_roce{s}_ism1_c4"]) for s in roce_sz]
    s0_rw = [phy_wire(by[f"s0_roce{s}_percore_c4"]) for s in roce_sz]

    x2 = np.arange(len(roce_sz))
    ax2.bar(x2 - w / 2, dp_rw, w, color=C_DP, edgecolor="#222", linewidth=0.8, label="1H DirectPush (mercury PHY)")
    ax2.bar(x2 + w / 2, sdp_rw, w, color=C_SDP, edgecolor="#222", linewidth=0.8, label="1H 最优 SDP ISM=1 (mercury PHY)")
    ax2.plot(x2 - w / 2, dp_ru, "D", color="#111111", markersize=5.8, zorder=5, label="DirectPush 16核用户态实收")
    ax2.plot(x2, s0_rw, "_", color=C_CEIL, markersize=18, markeredgewidth=2.0, zorder=4, label="Stage 0 4核卡直发上限")
    ax2.axhline(100.0, color=C_GREEN, linestyle="--", linewidth=1.2)

    for i in range(len(roce_sz)):
        ax2.text(x2[i] - w / 2, dp_rw[i] + 2.2, f"{dp_rw[i]:.1f}G", ha="center", va="bottom", fontsize=8.3, color=C_DP, fontweight="bold")
        ax2.text(x2[i] + w / 2, sdp_rw[i] + 2.2, f"{sdp_rw[i]:.1f}G", ha="center", va="bottom", fontsize=8.3, color=C_SDP)

    ax2.annotate(
        f"CX5 16核 UD RX 顶: {merc_rx_mpps(by['s2_dp_roce64_c4']):.1f}M ({dp_ru[0]:.1f}G)",
        xy=(0 - w / 2, dp_ru[0]), xytext=(0.25, 118),
        arrowprops=dict(arrowstyle="->", color="#222", lw=1.0, connectionstyle="arc3,rad=0.14"),
        fontsize=8.0, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#666", alpha=0.96),
    )

    ax2.set_xticks(x2)
    ax2.set_xticklabels(roce_lbl, fontsize=8.4)
    ax2.set_ylabel("mercury 实测物理线速 (Gbps)")
    ax2.set_ylim(0, 142)
    ax2.set_title("B. RoCEv2 UD 全粒度单向带宽（对端 16 核用户态 + PHY）", fontsize=10.3, fontweight="bold")
    ax2.legend(loc="lower right", fontsize=7.5, frameon=True, facecolor="white", framealpha=0.96)

    fig.suptitle(
        "MRV-155 Phase 2：1 颗主机核（saturn cpu40）单向端到端 100GbE 实测（DirectPush vs 最优 SDP，n = 3）",
        fontsize=11.0, fontweight="bold", y=0.97,
    )
    return save_clean(fig, "mrv-155-unidir-e2e")


def plot_fig3_ablations(by):
    """Fig 3: CN106 multi-core & NPA aura ablation + Stage 1 BAR4 & SDP ISM ablations."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.0))

    cs = [1, 2, 3, 4, 6, 8, 12, 16]
    pc = [phy_mpps(by[f"s0_raw64_percore_c{c}"]) for c in cs]
    ax1.plot(cs, pc, "o-", color=C_DP, linewidth=2.0, markersize=6, label="每核独立 mempool (per-core NPA aura)")

    sh_c = [4, 8]
    sh_v = [phy_mpps(by[f"s0_raw64_shared_c{c}"]) for c in sh_c]
    ax1.plot(sh_c, sh_v, "s--", color=C_SDP, linewidth=1.7, markersize=6.5, label="全核共享 mempool (shared NPA aura)")

    ru_c = [2, 4, 8]
    ru_v = [phy_mpps(by[f"s0_raw64_reuse_c{c}"]) for c in ru_c]
    ax1.plot(ru_c, ru_v, "^:", color=C_CEIL, linewidth=1.7, markersize=6.5, label="零释放 refcnt reuse (旁路 NPA 释放)")

    ax1.axhline(100.0, color=C_GREEN, linestyle="--", linewidth=1.1, alpha=0.85, label="100.0 Mpps (1.0 GHz sclk 硬件顶)")
    ax1.set_xticks(cs)
    ax1.set_xlabel("CN106 发包 ARM 核数 / eth0 TX 队列数 C")
    ax1.set_ylabel("mercury PHY 实测 64 B 包率 (Mpps)")
    ax1.set_ylim(0, 118)
    ax1.set_title("A. Stage 0：CN106 NIX0 核数扩展与 NPA 池消融", fontsize=10.3, fontweight="bold")
    ax1.legend(loc="lower right", fontsize=7.8, frameon=True, facecolor="white", framealpha=0.95)

    clabels = [
        "Stage 1\ndoorbell\nonly",
        "Stage 1\nnormal\n全读验真",
        "Stage 1\nldnp\n非临时读",
        "Stage 2\nDP\nstride 64",
        "Stage 2\nDP\nstride 128",
        "Stage 2\nSDP\nISM=0",
        "Stage 2\nSDP\nISM=1 C4",
        "Stage 2\nSDP\nISM=1 C8",
    ]
    cvals = [
        s1_card_mpps(by["s1_dp_64B_c4_doorbell-only"]),
        s1_card_mpps(by["s1_dp_64B_c4_normal"]),
        s1_card_mpps(by["s1_dp_64B_c4_ldnp"]),
        phy_mpps(by["s2_dp_raw64_c4"]),
        phy_mpps(by["s2_dp_raw64_s128_c4"]),
        phy_mpps(by["s2_sdp_raw64_ism0_c4"]),
        phy_mpps(by["s2_sdp_raw64_ism1_c4"]),
        phy_mpps(by["s2_sdp_raw64_ism1_c8"]),
    ]
    ccols = [C_GRAY, C_DP, C_DP, C_DP, "#5a9bf5", "#f0a060", C_SDP, C_SDP]
    x = np.arange(len(clabels))
    ax2.bar(x, cvals, width=0.60, color=ccols, edgecolor="#222", linewidth=0.7)
    for xi, v in zip(x, cvals):
        ax2.text(xi, v + 3.0, f"{v:.1f}", ha="center", va="bottom", fontsize=8.2, fontweight="bold")
    ax2.set_xticks(x)
    ax2.set_xticklabels(clabels, fontsize=7.6)
    ax2.set_ylabel("64 B 包率 (Mpps)")
    ax2.set_ylim(0, 190)
    ax2.set_title("B. 单变量消融：BAR4 读模式 / 步长 与 SDP ISM / 核数", fontsize=10.3, fontweight="bold")

    fig.suptitle(
        "MRV-155 Phase 2：关键硬件机制单变量消融（批门铃+SFENCE 消除 BAR4 snoop 退化；NIX0 100 Mpps 硅限）",
        fontsize=10.9, fontweight="bold", y=0.97,
    )
    return save_clean(fig, "mrv-155-ablations")


def main():
    by, rows = load_runs(RUNS_P2)
    print(f"Loaded {len(rows)} hardware-verified runs across {len(by)} configs.")
    p1 = plot_fig1_stage_roofline(by)
    p2 = plot_fig2_unidir_e2e(by)
    p3 = plot_fig3_ablations(by)
    print("Wrote:", p1, p2, p3)


if __name__ == "__main__":
    main()
