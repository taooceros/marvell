"""Generate refreshed Chinese pure-NIC figures for MRV-120 Linear Checklist (zero DSA).

Generates:
  - mrv-120-e5-native-nic-dma.png (measured MRV-22/23/24/89/97)
  - mrv-120-e8-pure-nic-e2e-mockup.png (expected mockup MRV-117, pure-NIC DirectPush + Native NIC DPI DMA)
  - mrv-120-e9-real-nix-loopback-mockup.png (expected mockup MRV-118, real CN106 NIX hardware loopback)

Run:
    uv run --with matplotlib python /home/hongtao/dev/marvell-mrv120/docs/figures/mrv-120-purenic-cn.py
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
C_BLUE, C_DARK, C_RED, C_ORANGE, C_GREEN, C_GRAY = "#1f6feb", "#1b263b", "#d62828", "#f77f00", "#2a9d8f", "#6c757d"


def load_jsonl(p):
    return [json.loads(l) for l in p.open() if l.strip()]


def save_mockup(fig, name, subtitle):
    txt = f"预期示意 / EXPECTED MOCKUP — 待上卡实测（虚线/斜线柱为预期目标，非实测数据）  |  {subtitle}"
    fig.tight_layout(rect=[0, 0, 1, 0.89])
    fig.text(
        0.5, 0.968, txt, ha="center", va="top", fontsize=9.0, fontweight="bold", color="#7a2e00",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#ffe8cc", edgecolor=C_ORANGE, lw=1.4),
    )
    fig.savefig(HERE / f"{name}.png")
    fig.savefig(HERE / f"{name}.svg")
    plt.close(fig)


def make_e5_cn():
    r89 = load_jsonl(LOGS_MAIN / "mrv89-2026-11-19/runs.jsonl")
    movdir = statistics.median([r["mops"] for r in r89 if r["exp"] == "exp1_fence" and r["cond"] == "kinf"])
    r97 = load_jsonl(LOGS_MAIN / "mrv97-2026-11-19/runs.jsonl")
    wc_us = statistics.median([r["host"]["rtt_ns"]["p50"] for r in r97 if r["cond"] == "base"]) / 1000.0

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.3))
    modes = ["去程 H→C\nMOVDIR64B\n(64B 直推)", "回程 C→H\n卡核 WC\n(64B 直推)", "去程 H→C\n原生 Fetch\n(DPI 读主机)", "回程 C→H\n原生 DPI DMA\n(128B 大块)"]
    rates = [movdir, 177.6, 187.6, 201.6]
    pcts = [movdir / 179.02 * 100, 177.6 / 179.02 * 100, 187.6 / 207.0 * 100, 201.6 / 207.0 * 100]
    x = np.arange(len(modes))
    bars = ax1.bar(x, rates, color=[C_BLUE, C_BLUE, C_GREEN, C_GREEN], width=0.54)
    ax1.axhline(179.02, color=C_BLUE, ls="--", lw=1.2, alpha=0.8, label="Gen5 ×4 64B 事务上限 (179 M/s)")
    ax1.axhline(207.0, color=C_GREEN, ls=":", lw=1.3, alpha=0.85, label="Gen5 ×4 128B 事务上限 (207 M/s)")
    for bar, v, p in zip(bars, rates, pcts):
        ax1.text(bar.get_x() + bar.get_width() / 2, v + 3.5, f"{v:.1f}\n({p:.0f}%)", ha="center", va="bottom", fontsize=8.4, fontweight="bold")
    ax1.set_xticks(x)
    ax1.set_xticklabels(modes, fontsize=8.3)
    ax1.set_ylabel("单向链路饱和速率 (M 等效64B/s)")
    ax1.set_ylim(0, 245)
    ax1.set_title("(a) 直推与原生网卡 DPI DMA 双双打满 Gen5 ×4")
    ax1.legend(loc="lower right", fontsize=7.6)

    lmodes = ["传统 SDP 队列\n(BAR4+SDP)", "商用网卡原生\nDPI DMA\n(大载荷 ≥4KiB)", "DirectPush 直推\nMOVDIR64B+WC\n(小消息 ≤256B)"]
    lats = [2.40, 1.31, wc_us]
    lx = np.arange(len(lmodes))
    lbars = ax2.bar(lx, lats, color=[C_GRAY, C_GREEN, C_BLUE], width=0.50)
    for bar, v in zip(lbars, lats):
        ax2.text(bar.get_x() + bar.get_width() / 2, v + 0.06, f"{v:.2f} µs", ha="center", va="bottom", fontsize=9.0, fontweight="bold")
    ax2.text(
        0.54, 0.72,
        "同一商用 PCIe 网卡按包长分工：\n• ≤256 B 走 DirectPush (0.90 µs)\n• ≥4 KiB 走原生 DPI DMA (97% 线速)\n零主机 memcpy，零专用 CPU 加速器",
        transform=ax2.transAxes, ha="center", va="center", fontsize=8.2, color=C_DARK,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#eef5ff", edgecolor=C_BLUE, lw=0.9),
    )
    ax2.set_xticks(lx)
    ax2.set_xticklabels(lmodes, fontsize=8.4)
    ax2.set_ylabel("W=1 64 B 往返延迟 p50 (µs)")
    ax2.set_ylim(0, 2.85)
    ax2.set_title("(b) 单张商用网卡上的大小载荷互补分工")

    fig.suptitle("[E5 实测] 纯商用网卡原生 DPI DMA (201.6 M/s, 97%) + DirectPush (0.90 µs) — 源: MRV-22/23/24/89/97", fontsize=10.0, fontweight="bold")
    mrvplot.save(fig, "mrv-120-e5-native-nic-dma")


def make_e8_cn():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.5))
    ax1.plot([1, 3, 5, 7, 8.2], [3.2, 3.8, 5.1, 9.5, 24.0], "--s", color=C_GRAY, lw=2.2, ms=6, label="[预期] (a) 传统 SDP 队列")
    ax1.plot([1, 4, 8, 12, 14.5], [1.4, 1.6, 2.2, 4.6, 16.5], "--^", color=C_ORANGE, lw=2.2, ms=6.5, label="[预期] (b) 纯 MOVDIR64B+WC (大小包全直推)")
    ax1.plot([1, 5, 10, 15, 20, 24], [1.1, 1.2, 1.38, 1.72, 2.5, 5.5], "--o", color=C_BLUE, lw=2.6, ms=7, label="[预期] (c) DirectPush + 原生 NIC DPI DMA")
    ax1.set_xlabel("单主机核混合 RPC/KV 吞吐 (M req/s)")
    ax1.set_ylabel("端到端请求延迟 p99 (µs)")
    ax1.set_ylim(0, 26)
    ax1.set_title("(a) [预期] 纯网卡三档单变量消融：延迟—吞吐")
    ax1.legend(loc="upper right", fontsize=7.8)

    modes = ["(a) 传统\nSDP 队列", "(b) 纯直推\nMOVDIR64B+WC", "(c) DirectPush +\n原生 DPI DMA"]
    x = np.arange(len(modes))
    cyc_ctrl = np.array([320, 95, 95])
    cyc_bulk = np.array([260, 210, 20])
    ax2.bar(x, cyc_ctrl, width=0.50, color=C_BLUE, alpha=0.82, hatch="//", edgecolor="white", label="[预期] 主机小包/控制面收发周期")
    ax2.bar(x, cyc_bulk, width=0.50, bottom=cyc_ctrl, color=C_RED, alpha=0.78, hatch="\\\\", edgecolor="white", label="[预期] 主机大载荷传输周期")
    for i, t in enumerate(cyc_ctrl + cyc_bulk):
        ax2.text(x[i], t + 14, f"~{t} cyc/req", ha="center", fontsize=8.8, fontweight="bold")
    ax2.annotate("大载荷交由原生网卡\nDPI DMA 零主机拷贝\n（无需专用加速器）", xy=(2, 118), xytext=(0.95, 425), fontsize=8.3, color=C_GREEN, fontweight="bold", arrowprops=dict(arrowstyle="->", color=C_GREEN, lw=1.3))
    ax2.set_xticks(x)
    ax2.set_xticklabels(modes, fontsize=8.5)
    ax2.set_ylabel("每请求主机 CPU 周期数 (cycles/req)")
    ax2.set_ylim(0, 680)
    ax2.set_title("(b) [预期] 严格双端核数账本：每请求主机周期")
    ax2.legend(loc="upper right", fontsize=7.8)
    save_mockup(fig, "mrv-120-e8-pure-nic-e2e-mockup", "MRV-117 纯网卡混合 RPC/KV 消融（零 DSA）")


def make_e9_cn():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.5))
    bars = ["传统 SDP 队列\n(真实 CN106 NIX 回环)", "DirectPush + DPI DMA\n(真实 CN106 NIX 回环)"]
    x = np.arange(len(bars))
    nix_lbk = np.array([0.85, 0.85])
    iface = np.array([2.40, 0.90])
    ax1.bar(x, nix_lbk, width=0.44, color=C_GRAY, alpha=0.85, hatch="//", edgecolor="white", label="[预期] CN106 NIX 硬件收发 + LBK 回环")
    ax1.bar(x, iface, width=0.44, bottom=nix_lbk, color=[C_RED, C_BLUE], alpha=0.85, hatch="\\\\", edgecolor="white", label="[锚定] 卡⇄主机接口延迟 (2.40 vs 0.90 µs)")
    for i in range(2):
        tot = nix_lbk[i] + iface[i]
        ax1.text(x[i], tot + 0.1, f"~{tot:.2f} µs", ha="center", fontsize=9.3, fontweight="bold")
    ax1.annotate("过真实 NIX 流水线仍\n净省 ~1.5 µs（免外接网线）", xy=(0.88, 1.78), xytext=(0.24, 2.85), fontsize=8.4, color=C_GREEN, fontweight="bold", arrowprops=dict(arrowstyle="->", color=C_GREEN, lw=1.3))
    ax1.set_xticks(x)
    ax1.set_xticklabels(bars, fontsize=8.8)
    ax1.set_ylabel("端到端 NIX 硬件回环 RPC RTT (µs)")
    ax1.set_ylim(0, 4.1)
    ax1.set_title("(a) [预期] 真实 CN106 NIX 硬件回环延迟拆解")
    ax1.legend(loc="upper right", fontsize=7.6)

    ax2.plot([1, 3, 6, 9, 11], [3.2, 3.7, 4.8, 8.4, 19.5], "--s", color=C_RED, lw=2.2, ms=6.5, label="[预期] 真实 NIX 回环 + SDP 基线")
    ax2.plot([1, 4, 8, 12, 16, 19], [1.75, 1.85, 2.05, 2.45, 3.25, 5.8], "--o", color=C_BLUE, lw=2.5, ms=7, label="[预期] 真实 NIX 回环 + DirectPush (含整形)")
    ax2.set_xlabel("NIX 硬件包到达速率 (M pkt/s)")
    ax2.set_ylabel("端到端 p99 延迟 (µs)")
    ax2.set_ylim(0, 21.5)
    ax2.set_title("(b) [预期] 真实 NIX 突发到达下无 13 M/s 悬崖")
    ax2.legend(loc="upper left", fontsize=8.0)
    save_mockup(fig, "mrv-120-e9-real-nix-loopback-mockup", "MRV-118 CN106 真实 NIX 硬件回环 eth4/LBK（零 DSA）")


if __name__ == "__main__":
    make_e5_cn()
    make_e8_cn()
    make_e9_cn()
    print("Generated 3 refreshed Chinese pure-NIC figures.")
