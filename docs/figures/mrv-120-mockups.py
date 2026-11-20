"""Generate unmistakable [EXPECTED MOCKUP] draft figures for unmeasured P0 experiments [E6-E10] in MRV-120.

Every mockup figure carries a bold orange warning banner ("预期示意 / EXPECTED MOCKUP — 尚未实测")
and uses dashed curves / hatched bars for expected values. Where real measured baselines exist
(e.g., MRV-70 unshaped p99, MRV-106 512 KiB hot LLC), they are plotted as solid lines from raw runs.jsonl
and explicitly tagged "[实测]".

Run:
    uv run --with matplotlib python /home/hongtao/dev/marvell-mrv120/docs/figures/mrv-120-mockups.py
"""
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mrvplot  # noqa: E402

LOGS_MAIN = pathlib.Path("/home/hongtao/dev/marvell/logs")

C_BLUE = "#1f6feb"
C_RED = "#d62828"
C_ORANGE = "#f77f00"
C_GREEN = "#2a9d8f"
C_GRAY = "#6c757d"
C_DARK = "#1b263b"


def save_mockup(fig, name, subtitle=""):
    txt = "预期示意 / EXPECTED MOCKUP — 待上卡实测（虚线/斜线柱为预期目标，非实测数据）"
    if subtitle:
        txt += f"  |  {subtitle}"
    fig.tight_layout(rect=[0, 0, 1, 0.89])
    fig.text(
        0.5, 0.968, txt,
        ha="center", va="top", fontsize=9.2, fontweight="bold", color="#7a2e00",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#ffe8cc", edgecolor=C_ORANGE, lw=1.4),
    )
    png = HERE / f"{name}.png"
    fig.savefig(png)
    fig.savefig(HERE / f"{name}.svg")
    plt.close(fig)
    return png


def make_e6_mockup():
    """[E6] MRV-116: TX Buffer Retention (64 KiB -> 128 MiB) zero degradation vs CC-NIC Fig. 15."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.5))

    # Left: CC-NIC Fig. 15 published relative degradation (literature numbers)
    cc_labels = ["CC-NIC\n全优化基线", "禁用缓冲复用\n(模拟TX留存)", "再禁用128B\n小缓冲切片", "再禁用NIC\n共享缓冲池"]
    cc_vals = [100, 80, 54, 27]  # CC-NIC ASPLOS'24 Fig. 15 (-20%, -46%, -73%)
    x1 = np.arange(len(cc_labels))
    bars1 = ax1.bar(x1, cc_vals, color=[C_GRAY, C_ORANGE, C_RED, "#9d0208"], width=0.55)
    for bar, v in zip(bars1, cc_vals):
        delta = "" if v == 100 else f"\n({v - 100}%)"
        ax1.text(bar.get_x() + bar.get_width() / 2, v + 2.2, f"{v}%{delta}", ha="center", va="bottom", fontsize=8.5, fontweight="bold")
    ax1.set_xticks(x1)
    ax1.set_xticklabels(cc_labels, fontsize=8.3)
    ax1.set_ylabel("相对吞吐 (% of 各自基线)")
    ax1.set_ylim(0, 122)
    ax1.set_title("(a) 文献对照：CC-NIC Fig.15 一致性 Cache Retention 塌方")

    # Right: Expected flat curve for our non-coherent Push across 64 KiB -> 128 MiB (> 60 MiB LLC)
    pools = ["64 KiB\n(热 L1/L2)", "4 MiB\n(热 LLC)", "32 MiB\n(半 LLC)", "128 MiB\n(>60MiB LLC\n模拟TCP重传)"]
    xp = np.arange(len(pools))
    exp_hot_ret = [100.0, 99.6, 99.3, 98.9]  # expected < 2% drop
    ax2.axhspan(98, 102, color=C_GREEN, alpha=0.14, label="预期判据区：退化 < 2%")
    ax2.plot(xp, exp_hot_ret, "--o", color=C_GREEN, lw=2.5, ms=7.5, label="[预期] 本文无一致性直推 (写后留存)")
    ax2.axvline(2.5, color=C_RED, ls=":", lw=1.3)
    ax2.text(2.55, 65, "Socket 1 LLC\n容量边界\n(60 MiB)", fontsize=8.2, color=C_RED)
    for i, v in enumerate(exp_hot_ret):
        ax2.text(xp[i], v + 2.5, f"~{v:.1f}%", ha="center", fontsize=8.8, fontweight="bold", color=C_GREEN)
    ax2.set_xticks(xp)
    ax2.set_xticklabels(pools, fontsize=8.3)
    ax2.set_ylabel("相对吞吐 (% of 64 KiB 基线)")
    ax2.set_ylim(0, 122)
    ax2.set_title("(b) [预期] MRV-116：无一致性直推留存 TX 缓冲零退化")
    ax2.legend(loc="lower left", fontsize=8.2)

    save_mockup(fig, "mrv-120-e6-tx-retention-mockup", "MRV-116")


def make_e7_mockup():
    """[E7] MRV-115: REAL MRV-70 open_echo p50/p99 explosion vs EXPECTED shaped p99 <= 2 us."""
    r70 = [json.loads(l) for l in (LOGS_MAIN / "mrv70-2026-11-18/runs.jsonl").open() if l.strip()]
    rates_raw = [5, 10, 13.5, 20, 25, 30]
    real_p50, real_p99 = [], []
    for rt in rates_raw:
        sub = [r for r in r70 if r.get("series") == "open_echo" and r.get("card_cfg") == "1c_wc_duplex" and r.get("h2c_target_mps") == rt]
        real_p50.append(statistics.median([r["echo_tot_us"]["p50"] for r in sub]))
        real_p99.append(statistics.median([r["echo_tot_us"]["p99"] for r in sub]))

    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    ax.plot(rates_raw, real_p99, "-o", color=C_RED, lw=2.5, ms=7, label="[实测 MRV-70] 无整形开环 p99（13.5 M/s 处跳升至 39.2 µs）")
    ax.plot(rates_raw, real_p50, "-s", color=C_ORANGE, lw=2.0, ms=6, label="[实测 MRV-70] 无整形开环 p50")

    # Expected shaped curves (dashed mockup)
    rates_exp = [5, 10, 13.5, 20, 25, 30]
    exp_p99 = [1.15, 1.35, 1.55, 1.85, 2.35, 3.60]
    exp_p50 = [0.95, 1.05, 1.12, 1.25, 1.45, 1.90]
    ax.plot(rates_exp, exp_p99, "--^", color=C_GREEN, lw=2.5, ms=7.5, label="[预期 MRV-115] 突发/信用整形后 p99（20 M/s ≤ 2.0 µs）")
    ax.plot(rates_exp, exp_p50, ":d", color=C_BLUE, lw=2.0, ms=6, label="[预期 MRV-115] 整形后 p50")

    ax.axhline(2.0, color=C_GREEN, ls="-.", lw=1.2, alpha=0.8)
    ax.text(26.0, 2.25, "目标线 p99 = 2.0 µs", fontsize=8.5, color=C_GREEN, fontweight="bold")
    ax.annotate("实测尾延迟悬崖\n39.2 → 70.0 µs", xy=(13.5, real_p99[2]), xytext=(14.5, 22), fontsize=8.8, color=C_RED, fontweight="bold", arrowprops=dict(arrowstyle="->", color=C_RED, lw=1.3))

    ax.set_yscale("log")
    ax.set_ylim(0.7, 120)
    ax.set_xlim(3.5, 31.5)
    ax.set_xlabel("开环请求-响应到达率 (M msg/s, 64 B, 主机 1 核)")
    ax.set_ylabel("端到端往返延迟 (µs，对数坐标)")
    ax.set_title("[E7 实测基线 + 预期目标] MRV-115：主机在途突发/信用整形消除开环尾延迟悬崖", fontsize=10.4, fontweight="bold", pad=12)
    ax.legend(loc="upper left", fontsize=8.0)

    save_mockup(fig, "mrv-120-e7-tail-shaping-mockup", "实线=MRV-70 实测，虚线=MRV-115 预期")


def make_e8_mockup():
    """[E8] MRV-117: SplitDirect 3-step mixed RPC/KV ablation (SDP+CPU -> Push -> SplitDirect)."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.5))

    # Left: Expected latency-throughput under mixed Ads/Geo (80% small <=256B, 20% large 4KiB)
    r_sdp = [1, 3, 5, 7, 8.2]
    l_sdp = [3.2, 3.8, 5.1, 9.5, 24.0]
    r_push = [1, 4, 8, 12, 14.5]
    l_push = [1.4, 1.6, 2.1, 4.2, 16.0]
    r_sd = [1, 5, 10, 16, 22, 26]
    l_sd = [1.1, 1.2, 1.35, 1.65, 2.4, 5.2]

    ax1.plot(r_sdp, l_sdp, "--s", color=C_GRAY, lw=2.2, ms=6, label="[预期] (a) SDP + 主机 CPU memcpy")
    ax1.plot(r_push, l_push, "--^", color=C_ORANGE, lw=2.2, ms=6.5, label="[预期] (b) 纯直推 Push (大包仍由主机拷贝)")
    ax1.plot(r_sd, l_sd, "--o", color=C_BLUE, lw=2.6, ms=7, label="[预期] (c) SplitDirect (Push + 卡直驱 P2P-DSA)")
    ax1.set_xlabel("单主机核混合 RPC/KV 吞吐 (M req/s)")
    ax1.set_ylabel("端到端请求延迟 p99 (µs)")
    ax1.set_ylim(0, 26)
    ax1.set_title("(a) [预期] 三档单变量消融：延迟—吞吐曲线")
    ax1.legend(loc="upper right", fontsize=8.0)

    # Right: Expected host CPU cycles / req breakdown (strict host+card core accounting)
    modes = ["(a) SDP +\nCPU memcpy", "(b) 纯直推\nPush-Only", "(c) SplitDirect\nPush + P2P-DSA"]
    x = np.arange(len(modes))
    cyc_ctrl = np.array([320, 95, 95])
    cyc_copy = np.array([290, 290, 0])
    ax2.bar(x, cyc_ctrl, width=0.5, color=C_BLUE, alpha=0.8, hatch="//", edgecolor="white", label="[预期] 主机接口收发/控制开销")
    ax2.bar(x, cyc_copy, width=0.5, bottom=cyc_ctrl, color=C_RED, alpha=0.75, hatch="\\\\", edgecolor="white", label="[预期] 主机大对象 memcpy 拷贝开销")
    totals = cyc_ctrl + cyc_copy
    for i, t in enumerate(totals):
        ax2.text(x[i], t + 15, f"~{t} cyc/req", ha="center", fontsize=9, fontweight="bold")
    ax2.annotate("省掉全部主机\n大载荷拷贝周期!", xy=(2, 95), xytext=(1.2, 460), fontsize=8.6, color=C_GREEN, fontweight="bold", arrowprops=dict(arrowstyle="->", color=C_GREEN, lw=1.3))
    ax2.set_xticks(x)
    ax2.set_xticklabels(modes, fontsize=8.6)
    ax2.set_ylabel("每请求主机 CPU 周期数 (cycles/req)")
    ax2.set_ylim(0, 720)
    ax2.set_title("(b) [预期] 严格双端核数账本：每请求主机 CPU 周期")
    ax2.legend(loc="upper right", fontsize=8.0)

    save_mockup(fig, "mrv-120-e8-splitdirect-e2e-mockup", "MRV-117 SplitDirect 端到端混合负载")


def make_e9_mockup():
    """[E9] MRV-100: Working set sweep 512 KiB (REAL MRV-106) -> 256 MiB Cold DRAM (EXPECTED)."""
    r106 = [json.loads(l) for l in (LOGS_MAIN / "mrv106-2026-11-19/runs.jsonl").open() if l.strip()]
    real_bar_b4 = statistics.median([r["gb_s"] for r in r106 if r["mode"] == "rate" and r["comp_target"] == "card_bar4" and r["batch_size"] == 4])
    real_hmz_b1 = statistics.median([r["gb_s"] for r in r106 if r["mode"] == "rate" and r["comp_target"] == "host_memzone" and r["batch_size"] == 1])

    fig, ax = plt.subplots(figsize=(9.0, 4.5))
    ws_labels = ["512 KiB\n(热 LLC 实测)", "16 MiB\n(中 LLC)", "64 MiB\n(压穿 LLC)", "128 MiB\n(冷 DDR5)", "256 MiB\n(全冷 DRAM)"]
    x = np.arange(len(ws_labels))

    exp_card_p2p = [real_bar_b4, 28.5, 22.0, 18.5, 18.0]
    exp_host_dsa = [real_hmz_b1 + 1.0, 29.0, 22.5, 19.0, 18.5]
    exp_host_cpu = [24.0, 21.0, 14.0, 11.5, 11.0]

    ax.plot(x, exp_card_p2p, "--o", color=C_BLUE, lw=2.5, ms=7, label="[预期] 卡直驱 DSA Batch B=4 (0 主机核)")
    ax.plot(x, exp_host_dsa, "--s", color=C_GREEN, lw=2.1, ms=6.5, label="[预期] 主机 MOVDIR64B → DSA 基线 (占 1 主机核)")
    ax.plot(x, exp_host_cpu, ":^", color=C_GRAY, lw=2.0, ms=6.5, label="[预期] 主机 1 核软件 memcpy (占 1 主机核)")
    # Highlight real measured anchor at x=0
    ax.scatter([0], [real_bar_b4], color=C_BLUE, s=110, zorder=6, edgecolor="black", lw=1.2)
    ax.annotate(f"[实测 MRV-106]\n{real_bar_b4:.2f} GB/s", xy=(0, real_bar_b4), xytext=(0.3, 33.0), fontsize=8.5, color=C_BLUE, fontweight="bold", arrowprops=dict(arrowstyle="->", color=C_BLUE))
    ax.axvline(2.0, color=C_RED, ls=":", lw=1.4)
    ax.text(2.08, 6.0, "Socket 1 LLC\n边界 (60 MiB)", fontsize=8.5, color=C_RED)
    ax.axhline(15.75, color=C_ORANGE, ls="-.", lw=1.2)
    ax.text(3.05, 16.2, "CN106 PCIe Gen5 ×4 物理带宽 15.75 GB/s", fontsize=8.2, color=C_ORANGE, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(ws_labels, fontsize=8.6)
    ax.set_ylabel("主机内存 4 KiB 有效搬运带宽 (GB/s)")
    ax.set_ylim(0, 38)
    ax.set_title("[E9 实测锚点 + 预期曲线] MRV-100：冷 DRAM (>128 MiB) 下卡直驱 P2P-DSA 仍紧贴主机 DSA 基线", fontsize=10.2, fontweight="bold", pad=12)
    ax.legend(loc="lower left", fontsize=8.0)

    save_mockup(fig, "mrv-120-e9-cold-dram-mockup", "首点=MRV-106 实测，16–256 MiB=MRV-100 预期")


def make_e10_mockup():
    """[E10] MRV-118: Real Ethernet NIX RX/TX end-to-end RTT breakdown (SDP vs SplitDirect)."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.4))

    # Left: Stacked RTT breakdown over real eth0 NIX hardware
    bars = ["传统 SDP 队列\n(走真 eth0 NIX)", "SplitDirect 直推\n(走真 eth0 NIX)"]
    x = np.arange(len(bars))
    wire_nix = np.array([1.2, 1.2])   # expected physical wire + MAC/NIX RX+TX
    iface = np.array([2.4, 0.90])     # known SDP ~2.4 us vs Direct ~0.90 us
    ax1.bar(x, wire_nix, width=0.45, color=C_GRAY, alpha=0.85, hatch="//", edgecolor="white", label="[预期] 物理线缆 + CN106 NIX 硬件收发")
    ax1.bar(x, iface, width=0.45, bottom=wire_nix, color=[C_RED, C_BLUE], alpha=0.85, hatch="\\\\", edgecolor="white", label="[锚定] 卡⇄主机接口延迟 (SDP 2.4 vs 直推 0.9 µs)")
    for i in range(2):
        tot = wire_nix[i] + iface[i]
        ax1.text(x[i], tot + 0.1, f"~{tot:.2f} µs", ha="center", fontsize=9.5, fontweight="bold")
    ax1.annotate("端到端净省 ~1.5 µs\n且无慢态悬崖", xy=(0.88, 2.12), xytext=(0.35, 3.15), fontsize=8.8, color=C_GREEN, fontweight="bold", arrowprops=dict(arrowstyle="->", color=C_GREEN, lw=1.3))
    ax1.set_xticks(x)
    ax1.set_xticklabels(bars, fontsize=9)
    ax1.set_ylabel("端到端以太网 RPC 往返延迟 (µs)")
    ax1.set_ylim(0, 4.3)
    ax1.set_title("(a) [预期] 过真以太网口 NIX 后接口延迟收益保留")
    ax1.legend(loc="upper right", fontsize=7.8)

    # Right: Expected real-network throughput-latency curve
    r_sdp = [1, 3, 6, 9, 11]
    l_sdp = [3.6, 4.1, 5.2, 8.8, 20.0]
    r_sd = [1, 4, 8, 12, 16, 19]
    l_sd = [2.1, 2.2, 2.4, 2.8, 3.6, 6.2]
    ax2.plot(r_sdp, l_sdp, "--s", color=C_RED, lw=2.2, ms=6.5, label="[预期] 真网口 NIX + SDP 基线")
    ax2.plot(r_sd, l_sd, "--o", color=C_BLUE, lw=2.5, ms=7, label="[预期] 真网口 NIX + SplitDirect (含整形)")
    ax2.set_xlabel("物理以太网到达速率 (M pkt/s, 64 B / 混合包)")
    ax2.set_ylabel("端到端 p99 延迟 (µs)")
    ax2.set_ylim(0, 22)
    ax2.set_title("(b) [预期] 真实网络突发到达下无 13 M/s 悬崖")
    ax2.legend(loc="upper left", fontsize=8.2)

    save_mockup(fig, "mrv-120-e10-real-nix-mockup", "MRV-118 需物理网线/对端机接线")


if __name__ == "__main__":
    make_e6_mockup()
    make_e7_mockup()
    make_e8_mockup()
    make_e9_mockup()
    make_e10_mockup()
    print("Generated all 5 expected mockup figures cleanly.")
