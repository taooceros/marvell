"""Generate OSDI paper draft figures from verified raw runs.jsonl for MRV-120 Checklist [E1-E5, E11].

Run:
    uv run --with matplotlib python /home/hongtao/dev/marvell-mrv120/docs/figures/mrv-120-real-charts.py
"""
import json
import pathlib
import statistics
import sys
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mrvplot  # noqa: E402

LOGS_MAIN = pathlib.Path("/home/hongtao/dev/marvell/logs")
LOGS_114 = pathlib.Path("/home/hongtao/dev/marvell-mrv114/logs/mrv114-2026-11-19/runs.jsonl")


def load_jsonl(path: pathlib.Path):
    return [json.loads(line) for line in path.open() if line.strip()]


def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals)


# Colors: clean OSDI palette
C_BLUE = "#1f6feb"
C_DARK = "#1b263b"
C_RED = "#d62828"
C_ORANGE = "#f77f00"
C_GREEN = "#2a9d8f"
C_GRAY = "#6c757d"
C_LIGHT = "#adb5bd"


def make_e1():
    """[E1] MRV-89: SFENCE frequency vs MOVDIR64B rate & address/core scaling."""
    rows = load_jsonl(LOGS_MAIN / "mrv89-2026-11-19/runs.jsonl")
    by_cond = defaultdict(list)
    for r in rows:
        if r["exp"] != "idle_card":
            by_cond[(r["exp"], r["cond"])].append(r)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.3))

    # Left: exp1_fence
    ks = ["k1", "k2", "k4", "k8", "k16", "k32", "k64", "k128", "k256", "kinf"]
    labels = ["1\n(每写栅栏)", "2", "4", "8", "16", "32", "64", "128", "256", "∞\n(无栅栏)"]
    xs = np.arange(len(ks))
    mops = [med([r["mops"] for r in by_cond[("exp1_fence", k)]]) for k in ks]
    gbps = [med([r["gbps"] for r in by_cond[("exp1_fence", k)]]) for k in ks]

    ax1.plot(xs, mops, "-o", color=C_BLUE, lw=2.3, ms=6.5, label="实测单核入卡吞吐 (MRV-89)")
    ax1.axhline(179.02, color=C_GRAY, ls="--", lw=1.3, label="PCIe Gen5 ×4 64B 理论上限 (179.0 M/s)")
    ax1.scatter([0], [mops[0]], color=C_RED, s=95, zorder=5)
    ax1.annotate(
        f"K=1 每写加 SFENCE\n{mops[0]:.2f} M/s ({gbps[0]:.2f} GB/s)\n(CC-NIC Fig.2 陷阱)",
        xy=(0, mops[0]),
        xytext=(0.45, 78),
        fontsize=8.3,
        color=C_RED,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#fff5f5", edgecolor=C_RED, lw=0.8),
        arrowprops=dict(arrowstyle="->", color=C_RED, lw=1.2),
    )
    ax1.annotate(
        f"无栅栏 1 核满线速\n{mops[-1]:.1f} M/s ({gbps[-1]:.2f} GB/s, 97.9%)",
        xy=(9, mops[-1]),
        xytext=(4.9, 118),
        fontsize=8.3,
        color=C_BLUE,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#eef5ff", edgecolor=C_BLUE, lw=0.8),
        arrowprops=dict(arrowstyle="->", color=C_BLUE, lw=1.2),
    )
    ax1.set_xticks(xs)
    ax1.set_xticklabels(labels, fontsize=8.5)
    ax1.set_xlabel("每 K 笔 MOVDIR64B 插入一次 SFENCE（主机 1 核 cpu40, 64 B）")
    ax1.set_ylabel("主机 → 卡 BAR4 写入速率 (M cells/s)")
    ax1.set_ylim(0, 205)
    ax1.set_title("(a) 栅栏频率是 PCIe MMIO 慢的唯一元凶")
    ax1.legend(loc="lower right", fontsize=8)

    # Right: pattern & cores
    bar_specs = [
        ("same64\n(同64B行)", ("exp2_pattern", "same64"), C_RED),
        ("seq64\n(连续1核)", ("exp2_pattern", "seq64"), C_BLUE),
        ("stride4k\n(4KB跨页)", ("exp2_pattern", "stride4k"), C_BLUE),
        ("2c SMT\n(同物理核)", ("exp3_cores", "2c_smt"), C_GREEN),
        ("2c Phys\n(2物理核)", ("exp3_cores", "2c_phys"), C_GREEN),
        ("4c Phys\n(4物理核)", ("exp3_cores", "4c_phys"), C_GREEN),
    ]
    bx = np.arange(len(bar_specs))
    bvals = [med([r["mops"] for r in by_cond[k]]) for _, k, _ in bar_specs]
    bcols = [c for _, _, c in bar_specs]
    bars = ax2.bar(bx, bvals, color=bcols, width=0.58, alpha=0.9)
    ax2.axhline(179.02, color=C_GRAY, ls="--", lw=1.3)
    for bar, v in zip(bars, bvals):
        ax2.text(bar.get_x() + bar.get_width() / 2, v + 3.5, f"{v:.1f}", ha="center", va="bottom", fontsize=8.5, fontweight="bold")
    ax2.set_xticks(bx)
    ax2.set_xticklabels([s[0] for s in bar_specs], fontsize=8.2)
    ax2.set_ylabel("主机 → 卡 BAR4 写入速率 (M cells/s)")
    ax2.set_ylim(0, 205)
    ax2.set_title("(b) 单核轮转不同行即打满 Gen5 ×4，无需多核或大 Batch")

    fig.suptitle("[E1 实测] 无栅栏 MOVDIR64B 单核 batch=1 即达 175.2 M/s (11.21 GB/s, 97.9% 线速) — 源: logs/mrv89", fontsize=11, fontweight="bold")
    mrvplot.save(fig, "mrv-120-e1-movdir64b-fence")


def make_e2():
    """[E2] MRV-97: Real hardware latency vs CXL-NIC/PCIe + 3-segment cache/DDIO ablation."""
    rows = load_jsonl(LOGS_MAIN / "mrv97-2026-11-19/runs.jsonl")
    p50 = {}
    for cond in ["base", "A_cold_src", "B0_card_hot_wait", "B1_card_cold_pre", "B2_card_dram_ctrl", "C0_host_hot_wait", "C1_host_cold_pre", "C2_host_dram_ctrl"]:
        p50[cond] = med([r["host"]["rtt_ns"]["p50"] for r in rows if r["cond"] == cond])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.6, 4.4))

    # Left: Real hardware 64B RTT comparison (no UPI bandwidth; clearly separate real HW vs UPI emulation)
    items = [
        ("E810 PCIe\n(CC-NIC 文献)", 3809.0, C_LIGHT),
        ("CN106 SDP\n(同卡传统队列)", 2400.0, C_LIGHT),
        ("CX6 PCIe\n(CC-NIC 文献)", 2116.0, C_LIGHT),
        ("CXL-NIC\nCC-NIC设计\n(MICRO'25真CXL)", 2100.0, C_ORANGE),
        ("CXL-NIC\n最优设计\n(MICRO'25真CXL)", 1300.0, C_ORANGE),
        ("本文直推\n商用 PCIe Gen5\n(MRV-97 实测)", p50["base"], C_BLUE),
    ]
    y = np.arange(len(items))
    vals = [x[1] for x in items]
    cols = [x[2] for x in items]
    bars = ax1.barh(y, vals, color=cols, height=0.58)
    for bar, v in zip(bars, vals):
        ax1.text(v + 55, bar.get_y() + bar.get_height() / 2, f"{v:.0f} ns", va="center", fontsize=8.8, fontweight="bold")
    ax1.set_yticks(y)
    ax1.set_yticklabels([x[0] for x in items], fontsize=8.3)
    ax1.set_xlim(0, 4450)
    ax1.set_xlabel("64 B 最小往返延迟 RTT (ns，越低越好)")
    ax1.set_title("(a) 真实硬件 64 B 往返延迟：非一致 PCIe 反超真 CXL 网卡")
    ax1.invert_yaxis()

    # Right: 3-segment cache/DDIO shortcut proof from MRV-97
    d_host_src = p50["A_cold_src"] - p50["base"]
    d_card_l3 = p50["B2_card_dram_ctrl"] - p50["B1_card_cold_pre"]
    d_host_ddio = p50["C2_host_dram_ctrl"] - p50["C1_host_cold_pre"]
    seg_names = [
        "主机源行命中\nL1/LLC 捷径\n(base vs A_cold)",
        "卡侧接收行\n直入卡 L3 捷径\n(B1 vs B2 DRAM)",
        "回程 WC 经 DDIO\n直入主机 LLC\n(C1 vs C2 DRAM)",
    ]
    deltas = [d_host_src, d_card_l3, d_host_ddio]
    sx = np.arange(len(seg_names))
    sbars = ax2.bar(sx, deltas, color=[C_BLUE, C_GREEN, C_DARK], width=0.52)
    for bar, d in zip(sbars, deltas):
        ax2.text(bar.get_x() + bar.get_width() / 2, d + 7, f"+{d:.1f} ns", ha="center", va="bottom", fontsize=9.5, fontweight="bold")
    ax2.set_xticks(sx)
    ax2.set_xticklabels(seg_names, fontsize=8.5)
    ax2.set_ylabel("踢出缓存改为读 DRAM 的额外延迟惩罚 (ns)")
    ax2.set_ylim(0, 365)
    ax2.set_title(f"(b) 实测三段缓存/DDIO 捷径全开（合计省 {sum(deltas):.0f} ns）")
    ax2.text(
        0.5, 0.94,
        f"空载基准 RTT p50 = {p50['base']:.1f} ns (5轮最小 897.3 ns)\n无需 CXL 一致性协议即直达双端缓存",
        transform=ax2.transAxes, ha="center", va="top", fontsize=8.4,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#eef5ff", edgecolor=C_BLUE, lw=1),
    )

    fig.suptitle("[E2 实测] 商用 PCIe Gen5 直推达 898 ns，低于真 CXL-NIC (1.3–2.1 µs) 且三段缓存捷径全开 — 源: logs/mrv97", fontsize=10.8, fontweight="bold")
    mrvplot.save(fig, "mrv-120-e2-latency-anchors")


def make_e3():
    """[E3] MRV-76 + MRV-55: Frontier write-band conflict & 64B card L3 coherence."""
    r76 = load_jsonl(LOGS_MAIN / "mrv76-2026-11-18/runs.jsonl")
    g76 = defaultdict(list)
    for r in r76:
        g76[(r["poll_gap_mode"], r["poll_gap_ns"])].append(r["host"]["host_issued_mps"])

    r55 = load_jsonl(LOGS_MAIN / "mrv55-2026-11-18/runs.jsonl")
    g55 = defaultdict(list)
    for r in r55:
        if r["mode"] == "atomic_rmw":
            g55[r["dist_bytes"]].append(r["core0_mops"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.3))

    # Left: MRV-76 write-band conflict vs cold L3 reads
    specs = [
        ("write-band\n(读在途写带\n3 µs)", ("write-band", 3000), C_RED),
        ("none\n(无停顿紧追\n0 ns)", ("none", 0), C_ORANGE),
        ("spin\n(纯空转\n3 µs)", ("spin", 3000), C_BLUE),
        ("cold-priv\n(穿透L3读私有\n3 µs)", ("cold-private", 3000), C_GREEN),
        ("far-ring\n(跨4KB冷读环\n3 µs)", ("far-ring-4k", 3000), C_GREEN),
    ]
    x = np.arange(len(specs))
    vals = [med(g76[k]) for _, k, _ in specs]
    cols = [c for _, _, c in specs]
    bars = ax1.bar(x, vals, color=cols, width=0.56)
    for bar, v in zip(bars, vals):
        ax1.text(bar.get_x() + bar.get_width() / 2, v + 3, f"{v:.1f}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    ax1.set_xticks(x)
    ax1.set_xticklabels([s[0] for s in specs], fontsize=8.3)
    ax1.set_ylabel("主机 → 卡 MOVDIR64B 速率 (M/s)")
    ax1.set_ylim(0, 188)
    ax1.set_title("(a) MRV-76：拖慢入卡的是「在途写带前沿嗅探」，非 L3 读带宽")

    # Right: MRV-55 64B vs 128B coherence granularity
    dists = [0, 64, 128, 256]
    mops55 = [med(g55[d]) for d in dists]
    dx = np.arange(len(dists))
    cols55 = [C_RED, C_GREEN, C_GREEN, C_GREEN]
    bars2 = ax2.bar(dx, mops55, color=cols55, width=0.52)
    for bar, v in zip(bars2, mops55):
        ax2.text(bar.get_x() + bar.get_width() / 2, v + 1.8, f"{v:.1f}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    ax2.set_xticks(dx)
    ax2.set_xticklabels(["0 B\n(同行竞争)", "64 B\n(相邻行)", "128 B", "256 B"], fontsize=8.8)
    ax2.set_ylabel("卡双核原子 RMW 吞吐 (Mops/s)")
    ax2.set_ylim(0, 82)
    ax2.set_title("(b) MRV-55：卡侧 L3 一致性粒度严格为 64 B（64 B 距即完全解除冲突）")

    fig.suptitle("[E3 实测] 真 DPU 微架构陷阱 I：写带前沿嗅探冲突 (113.5 vs 162.6 M/s) 与 64 B L3 粒度 — 源: logs/mrv76, mrv55", fontsize=10.5, fontweight="bold")
    mrvplot.save(fig, "mrv-120-e3-frontier-conflict")


def make_e4():
    """[E4] MRV-75: PEM0/IOBN 76.4 ns (13.1 M/s) cliff & dual-sided fixes."""
    rows = load_jsonl(LOGS_MAIN / "mrv75-2026-11-19/runs.jsonl")
    by_c = defaultdict(list)
    for r in rows:
        if r["exp"] != "idle_card":
            m = r.get("mops")
            if m is not None:
                by_c[(r["exp"], r["cond"])].append(m)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.3))

    # Left: W scan showing the 13.1 M/s cliff
    w_items = [
        ("W=1", ("part1_xray", "wc4_w1")),
        ("W=16", ("part1_wscan", "wc4_w16")),
        ("W=32", ("part1_wscan", "wc4_w32")),
        ("W=40", ("part1_wscan", "wc4_w40")),
        ("W=48", ("part1_wscan", "wc4_w48")),
        ("W=64", ("part1_xray", "wc4_w64")),
        ("W=128", ("part1_wscan", "wc4_w128")),
        ("W=1024", ("part1_wscan", "wc4_w1024")),
    ]
    wx = np.arange(len(w_items))
    wvals = [med(by_c[k]) for _, k in w_items]
    ax1.plot(wx, wvals, "-o", color=C_DARK, lw=2.2, ms=6.5)
    ax1.axvspan(3.6, 6.4, color=C_RED, alpha=0.13, label="PEM0/IOBN 串行慢态悬崖 (~13.1 M/s, 76.4 ns/条)")
    for i in [2, 4, 5, 7]:
        ax1.text(wx[i], wvals[i] + 1.2, f"{wvals[i]:.1f}", ha="center", fontsize=8.5, fontweight="bold", color=C_RED if i in (4, 5) else C_DARK)
    ax1.set_xticks(wx)
    ax1.set_xticklabels([x[0] for x in w_items], fontsize=8.5)
    ax1.set_ylabel("闭环往返吞吐 (M msg/s, WC4)")
    ax1.set_ylim(0, 36)
    ax1.set_title("(a) 并发 W 扫频：W=48–128 触发 13.1 M/s 入向串行悬崖")
    ax1.legend(loc="upper left", fontsize=8)

    # Right: Dual-sided fixes at W=64
    fix_items = [
        ("无干预\nWC4 W=64", ("part1_xray", "wc4_w64"), C_RED),
        ("主机等距\ngap=20ns\n(无效)", ("part3_gap", "gap_20ns"), C_GRAY),
        ("主机突发上限\nburst=8\n(+54%)", ("part3_burst", "burst_8"), C_BLUE),
        ("卡侧退避\nT_pause=1.8µs\n(+105%)", ("part2_pause", "pause_1800ns"), C_GREEN),
        ("卡侧单核对照\nWC1 W=64", ("part1_xray", "wc1_w64"), C_DARK),
    ]
    fx = np.arange(len(fix_items))
    fvals = [med(by_c[k]) for _, k, _ in fix_items]
    fcols = [c for _, _, c in fix_items]
    fbars = ax2.bar(fx, fvals, color=fcols, width=0.56)
    for bar, v in zip(fbars, fvals):
        ax2.text(bar.get_x() + bar.get_width() / 2, v + 0.9, f"{v:.1f}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    ax2.set_xticks(fx)
    ax2.set_xticklabels([x[0] for x in fix_items], fontsize=8.2)
    ax2.set_ylabel("闭环往返吞吐 (M msg/s, W=64)")
    ax2.set_ylim(0, 40)
    ax2.set_title("(b) W=64 双边破局：突发上限/退避跳出慢态，均匀降速无效")

    fig.suptitle("[E4 实测] 真 DPU 微架构陷阱 II：PEM0/IOBN 76.4 ns 串行悬崖与双边破局 — 源: logs/mrv75", fontsize=10.6, fontweight="bold")
    mrvplot.save(fig, "mrv-120-e4-pem0-cliff-fixes")


def make_e5():
    """[E5] MRV-95 + MRV-99 + MRV-106: Zero-host-CPU Card->DSA P2P & Batch B=1..32."""
    r95 = load_jsonl(LOGS_MAIN / "mrv95-2026-11-19/runs.jsonl")
    r99 = load_jsonl(LOGS_MAIN / "mrv99-2026-11-19/runs.jsonl")
    r106 = load_jsonl(LOGS_MAIN / "mrv106-2026-11-19/runs.jsonl")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.3))

    # Left: W=1 latency across paths (NOP & 4KiB MEMMOVE)
    host_nop = med([r["p50_ns"] for r in r95 if r["submitter"] == "host_movdir64b" and r["mode"] == "w1_lat" and r["op"] == "nop"])
    host_4k = med([r["p50_ns"] for r in r95 if r["submitter"] == "host_movdir64b" and r["mode"] == "w1_lat" and r["op"] == "memmove" and r["size"] == 4096])
    hmz_nop = med([r["p50_ns"] for r in r99 if r["mode"] == "w1_lat" and r["comp_target"] == "host_memzone" and r["op"] == "nop"])
    hmz_4k = med([r["p50_ns"] for r in r99 if r["mode"] == "w1_lat" and r["comp_target"] == "host_memzone" and r["op"] == "memmove" and r["size"] == 4096])
    bar_nop = med([r["p50_ns"] for r in r99 if r["mode"] == "w1_lat" and r["comp_target"] == "card_bar4" and r["op"] == "nop"])
    bar_4k = med([r["p50_ns"] for r in r99 if r["mode"] == "w1_lat" and r["comp_target"] == "card_bar4" and r["op"] == "memmove" and r["size"] == 4096])

    cats = ["NOP\n(纯门户+完成)", "4 KiB MEMMOVE\n(主机内存拷贝)"]
    x = np.arange(len(cats))
    w = 0.25
    b1 = ax1.bar(x - w, [host_nop / 1000, host_4k / 1000], width=w, color=C_LIGHT, label="主机 MOVDIR64B (需主机核)")
    b2 = ax1.bar(x, [hmz_nop / 1000, hmz_4k / 1000], width=w, color=C_ORANGE, label="卡 DPI→DSA + DPI 读回 (0 主机核)")
    b3 = ax1.bar(x + w, [bar_nop / 1000, bar_4k / 1000], width=w, color=C_BLUE, label="卡 DPI→DSA + 反向 P2P BAR4 (0 主机核)")
    for grp in (b1, b2, b3):
        for bar in grp:
            v = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width() / 2, v + 0.05, f"{v:.2f}µs", ha="center", va="bottom", fontsize=8.2, fontweight="bold")
    ax1.set_xticks(x)
    ax1.set_xticklabels(cats, fontsize=9)
    ax1.set_ylabel("W=1 单笔完成延迟 p50 (µs)")
    ax1.set_ylim(0, 2.75)
    ax1.set_title("(a) 双向 P2P 消除 DPI 轮询读回：NOP 1.99→1.34 µs")
    ax1.legend(loc="upper left", fontsize=7.6)

    # Right: MRV-106 Batch B=1..32 throughput recovery
    bs = [1, 2, 4, 8, 16, 32]
    gbs_bar = [med([r["gb_s"] for r in r106 if r["mode"] == "rate" and r["comp_target"] == "card_bar4" and r["batch_size"] == b]) for b in bs]
    gbs_hmz_b1 = med([r["gb_s"] for r in r106 if r["mode"] == "rate" and r["comp_target"] == "host_memzone" and r["batch_size"] == 1])
    gbs_hmz_b16 = med([r["gb_s"] for r in r106 if r["mode"] == "rate" and r["comp_target"] == "host_memzone" and r["batch_size"] == 16])

    bx = np.arange(len(bs))
    ax2.plot(bx, gbs_bar, "-o", color=C_BLUE, lw=2.4, ms=7, label="反向 P2P BAR4 (0 主机核)")
    ax2.axhline(gbs_hmz_b1, color=C_ORANGE, ls="--", lw=1.4, label=f"HostMemzone B=1 基线 ({gbs_hmz_b1:.2f} GB/s)")
    ax2.axhline(gbs_hmz_b16, color=C_GRAY, ls=":", lw=1.4, label=f"HostMemzone B=16 基线 ({gbs_hmz_b16:.2f} GB/s)")
    for i, v in enumerate(gbs_bar):
        ax2.text(bx[i], v + 0.9, f"{v:.1f}", ha="center", fontsize=8.5, fontweight="bold", color=C_BLUE)
    ax2.annotate("B=1: +131 ns\n反向 P2P 泡沫", xy=(0, gbs_bar[0]), xytext=(0.55, 11.5), fontsize=8.3, color=C_RED, fontweight="bold", arrowprops=dict(arrowstyle="->", color=C_RED))
    ax2.annotate("B=4..16 彻底摊薄\n31.1–31.3 GB/s!", xy=(2, gbs_bar[2]), xytext=(2.2, 23.0), fontsize=8.5, color=C_GREEN, fontweight="bold", arrowprops=dict(arrowstyle="->", color=C_GREEN))
    ax2.set_xticks(bx)
    ax2.set_xticklabels([f"B={b}" for b in bs], fontsize=8.8)
    ax2.set_xlabel("DSA 硬件 Batch 聚合大小 B（总在途 W=64, 4 KiB MEMMOVE, CC=0）")
    ax2.set_ylabel("零主机核搬运吞吐 (GB/s)")
    ax2.set_ylim(8, 36.5)
    ax2.set_title("(b) MRV-106：DSA Batch B≥4 满血恢复至 31.32 GB/s")
    ax2.legend(loc="lower right", fontsize=7.6)

    fig.suptitle("[E5 实测] 零主机核卡直驱 DSA 双向 P2P：仅靠 Batch B≥4 实现 31.32 GB/s — 源: logs/mrv95, mrv99, mrv106", fontsize=10.6, fontweight="bold")
    mrvplot.save(fig, "mrv-120-e5-p2p-dsa-batch")


def make_e11():
    """[E11] MRV-114 (PRELIMINARY): 1 vs 2 vs 4 DSA engines cannot bypass B=1 131 ns bubble."""
    rows = load_jsonl(LOGS_114)
    engs = [1, 2, 4]
    bar_b1 = [med([r["gb_s"] for r in rows if r["mode"] == "rate" and r["engines"] == e and r["comp_target"] == "card_bar4" and r["op"] == "memmove" and r["batch_size"] == 1]) for e in engs]
    bar_b4 = [med([r["gb_s"] for r in rows if r["mode"] == "rate" and r["engines"] == e and r["comp_target"] == "card_bar4" and r["op"] == "memmove" and r["batch_size"] == 4]) for e in engs]
    hmz_b1 = [med([r["gb_s"] for r in rows if r["mode"] == "rate" and r["engines"] == e and r["comp_target"] == "host_memzone" and r["op"] == "memmove" and r["batch_size"] == 1]) for e in engs]

    fig, ax = plt.subplots(figsize=(9.2, 4.8))
    x = np.arange(len(engs))
    w = 0.24
    b1 = ax.bar(x - w, bar_b1, width=w, color=C_RED, label="CardBar4 B=1 (15.28 → 15.51 → 15.53 GB/s)")
    b2 = ax.bar(x, bar_b4, width=w, color=C_BLUE, label="CardBar4 B=4 (30.75 / 30.96 / 28.21 GB/s)")
    b3 = ax.bar(x + w, hmz_b1, width=w, color=C_GRAY, label="HostMemzone B=1 (29.98 GB/s 恒定)")

    for grp in (b1, b2, b3):
        for bar in grp:
            v = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, v + 0.7, f"{v:.1f}", ha="center", va="bottom", fontsize=9, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(["1 引擎\n(100%)", "2 引擎\n(PMU 50/50% 均分)", "4 引擎\n(PMU 25%×4 均分)"], fontsize=9)
    ax.set_ylabel("4 KiB MEMMOVE 吞吐 (GB/s, W=64, CC=0)")
    ax.set_ylim(0, 42)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 0.99), ncol=2, fontsize=8.1, frameon=True)
    ax.annotate(
        "增加至 4 引擎仅 +1.6%！\n硬件加引擎无法绕过 ~131 ns 泡沫\n→ Batch B≥4 是结构必需",
        xy=(1 - w, bar_b1[1]),
        xytext=(0.48, 21.8),
        fontsize=8.3,
        color=C_RED,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#fff5f5", edgecolor=C_RED, lw=0.9),
        arrowprops=dict(arrowstyle="->", color=C_RED, lw=1.3),
    )
    ax.set_title("[E11 实测待评审] MRV-114：单 WQ 增配 1→4 个 DSA 引擎仍被 B=1 锁在 ~15.5 GB/s", fontsize=10.4, fontweight="bold", pad=10)
    fig.tight_layout(rect=[0, 0, 1, 0.89])
    fig.text(
        0.5, 0.968,
        "PRELIMINARY — MRV-114 Phase 2 实测 (57 轮零错，正在 MRV-119 评审中)  |  源: marvell-mrv114/logs/mrv114",
        ha="center", va="top", fontsize=8.8, fontweight="bold",
        color="#7f4f24", bbox=dict(boxstyle="round,pad=0.3", facecolor="#fff3cd", edgecolor=C_ORANGE, lw=1.3),
    )
    png = HERE / "mrv-120-e11-mrv114-engines.png"
    fig.savefig(png)
    fig.savefig(HERE / "mrv-120-e11-mrv114-engines.svg")
    plt.close(fig)


if __name__ == "__main__":
    make_e1()
    make_e2()
    make_e3()
    make_e4()
    make_e5()
    make_e11()
    print("Generated all 6 real-data figures cleanly.")
