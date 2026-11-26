"""MRV-213 Figure 2: 1500 B full-100GbE burst drain rate (breaking MRV-165 PEM0 WC wall
and achieving full >100GbE line-rate drain), host DDIO LLC hit rate, and Host IMC vs CN106
DDR5 memory-traffic offloading across burst sizes (n=3 medians).

100% dynamically computed from `logs/mrv213-2026-11-26/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv213-2026-11-26/runs.jsonl"

BURST_ORDER = ["64KiB", "256KiB", "1MiB", "4MiB", "16MiB", "32MiB", "64MiB", "128MiB"]
BURST_LABELS = ["64K", "256K", "1M", "4M", "16M", "32M", "64M", "128M"]


def med(xs):
    return statistics.median(xs)


def assert_verified(r):
    assert r.get("verified") is True and not r.get("hard_errors") and not r.get("clock_errors")
    hp = r["host_pmu"]
    assert hp["min_pct"] >= 99.99
    if r["arm"] != "kv-solo":
        cp, cs, ms = r["card_pmu"], r["card_stats"], r["mercury"]
        assert hp["rx"]["page_faults"] == 0 and r["bad"] == 0 and r["torn"] == 0
        assert cp["min_pct"] >= 99.99 and cs["wire_bad"] == 0 and cs["wire_torn"] == 0 and cs["credit_torn"] == 0
        assert cs["eth"]["imissed"] == 0 and cs["fifo_drops"] == 0 and cs["eth_rx_pkts"] == r["offered_pkts"]
        assert ms["phy"]["tx_pause"] == 0 and ms["phy"]["rx_pause"] == 0
        if r["arm"] not in ("small-drop", "native-bp"):
            assert r["lost_pkts"] == 0 and r["gaps"] == 0 and r["ooo"] == 0


def main():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    by_key = defaultdict(list)
    for r in rows:
        assert_verified(r)
        if r["part"] == "e2_1500b_kv32m":
            by_key[(r["arm_tag"], r["burst_label"])].append(r)

    x = np.arange(len(BURST_ORDER))
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(17.2, 5.45), gridspec_kw={"width_ratios": [1.15, 1.05, 1.15]})

    # ---------------- Panel A: 1500B Consumer Drain Throughput (lossless arms) ----------------
    # Only show lossless arms (arms 1, 3, 5) — arms 2/4 dropped 92-99% of the burst.
    lossless_arms = [
        ("large_mono",  "① 大环单调池 (主机内存排空)",   "#dc2626", "o",  "-",  2.0),
        ("large_lifo",  "③ 大环LIFO池 (主机内存排空)",   "#f97316", "s",  "--", 1.8),
        ("credit_ring", "⑤ CreditRing (经标准SDP排空)",  "#1a7f37", "D",  "-",  2.6),
    ]
    ax1.axhline(100.0, color="#0284c7", linestyle="--", linewidth=1.3, zorder=1)
    ax1.text(0.05, 108.0, "100GbE 满线速 (100 Gbps)", color="#0369a1", fontsize=8.6, fontweight="bold")
    ax1.axhline(90.9, color="#b91c1c", linestyle=":", linewidth=1.2, zorder=1)
    ax1.text(0.05, 76.0, "MRV-165 64B WC 字节上限\n(90.9 Gbps；小包仍高于SDP)", color="#991b1b", fontsize=7.9, va="top")
    ax1.axvline(2.35, color="#cbd5e1", linestyle=":", linewidth=1.1, zorder=0)

    for tag, label, col, mk, ls, lw in lossless_arms:
        ys = [med([r["drain_wire_gbps"] for r in by_key[(tag, b)]]) for b in BURST_ORDER]
        ax1.plot(x, ys, label=label, color=col, marker=mk, linestyle=ls, linewidth=lw, markersize=5.8)

    cr_128 = med([r["drain_wire_gbps"] for r in by_key[("credit_ring", "128MiB")]])
    cr_128_l2 = med([r["drain_l2_gbps"] for r in by_key[("credit_ring", "128MiB")]])
    lm_128 = med([r["drain_wire_gbps"] for r in by_key[("large_mono", "128MiB")]])
    ax1.annotate(
        f"⑤ 128 MiB: {cr_128:.1f} Gbps (L2 {cr_128_l2:.1f}G)\n1500 B 按字节达 100GbE 满线速",
        xy=(x[-1], cr_128), xytext=(3.10, 20),
        fontsize=8.4, color="#1a7f37", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#f0fdf4", edgecolor="#1a7f37", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#1a7f37", lw=1.1),
    )
    ax1.annotate(
        f"① 已入主机冷DRAM 读速: {lm_128:.1f} Gbps\n(非网卡/PCIe 传输通路)",
        xy=(x[-1], lm_128), xytext=(2.85, 255),
        fontsize=8.3, color="#dc2626", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.0),
    )
    ax1.text(0.10, 415, "≤1 MiB 命中主机L3\n(主机核L3内消费)", fontsize=8.3, color="#475569")
    ax1.set_xticks(x)
    ax1.set_xticklabels(BURST_LABELS)
    ax1.set_xlabel("突发规模（1500 B 帧）")
    ax1.set_ylabel("消费排空速率（Gbps 线速等效）")
    ax1.set_ylim(0, max(560, med([r["drain_wire_gbps"] for r in by_key[("credit_ring", "1MiB")]]) + 50))
    ax1.set_title("A. 1500 B 按字节达 100GbE 线速（64 B 见图 3B）", fontsize=10.6, fontweight="bold")
    ax1.legend(loc="upper right", fontsize=8.0, framealpha=0.92)
    # ---------------- Panel B: Host DDIO LLC Hit Rate (%) ----------------
    ddio_arms = [
        ("large_mono",  "① 大环单调池",   "#dc2626", "o",  "-",  2.0),
        ("large_lifo",  "③ 大环LIFO池",   "#f97316", "s",  "--", 1.8),
        ("native_bp",   "④ 原生SDP(丢包)", "#64748b", "v",  "-.", 1.5),
        ("credit_ring", "⑤ CreditRing",   "#1a7f37", "D",  "-",  2.6),
    ]
    for tag, label, col, mk, ls, lw in ddio_arms:
        ys = [med([r["host_pmu"]["ddio_hit_pct"] for r in by_key[(tag, b)]]) for b in BURST_ORDER]
        ax2.plot(x, ys, label=label, color=col, marker=mk, linestyle=ls, linewidth=lw, markersize=5.8)

    cr_ddio = med([r["host_pmu"]["ddio_hit_pct"] for r in by_key[("credit_ring", "128MiB")]])
    lm_ddio = med([r["host_pmu"]["ddio_hit_pct"] for r in by_key[("large_mono", "128MiB")]])
    ax2.annotate(
        f"⑤ CreditRing: {cr_ddio:.1f}%",
        xy=(x[-1], cr_ddio), xytext=(1.6, 78),
        fontsize=9.3, color="#1a7f37", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.22", facecolor="#f0fdf4", edgecolor="#1a7f37", lw=0.8),
        arrowprops=dict(arrowstyle="->", color="#1a7f37", lw=1.1),
    )
    ax2.annotate(
        f"①/③ 大环塌至 {lm_ddio:.1f}%",
        xy=(x[-1], lm_ddio), xytext=(x[-1] - 3.6, 20),
        fontsize=9.2, color="#dc2626", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.0),
    )
    ax2.set_xticks(x)
    ax2.set_xticklabels(BURST_LABELS)
    ax2.set_xlabel("突发规模（1500 B 帧）")
    ax2.set_ylabel("主机 PCIe→L3 DDIO 命中率（%）")
    ax2.set_ylim(-3, 108)
    ax2.set_title("B. 主机 DDIO 命中率（32×CHA 实测）", fontsize=11.0, fontweight="bold")
    ax2.legend(loc="center left", fontsize=8.2, framealpha=0.92)

    # ---------------- Panel C: Memory Traffic Offload per Burst (Host IMC vs Card DDR5) ----------------
    mem_bursts = ["16MiB", "32MiB", "64MiB", "128MiB"]
    xm = np.arange(len(mem_bursts))
    w = 0.25

    def per_burst_host_imc(tag, b):
        return med([
            (r["host_pmu"]["imc_all_rd_mib"] + r["host_pmu"]["imc_all_wr_mib"]) / r["bursts"]
            for r in by_key[(tag, b)]
        ])

    def per_burst_card_ddr(tag, b):
        return med([
            (r["card_pmu"]["ddr_rd_mib"] + r["card_pmu"]["ddr_wr_mib"]) / r["bursts"]
            for r in by_key[(tag, b)]
        ])

    lm_imc = [per_burst_host_imc("large_mono", b) for b in mem_bursts]
    cr_imc = [per_burst_host_imc("credit_ring", b) for b in mem_bursts]
    cr_ddr = [per_burst_card_ddr("credit_ring", b) for b in mem_bursts]

    b1 = ax3.bar(xm - w, lm_imc, w, label="① 大环：主机 DRAM (IMC)", color="#dc2626", alpha=0.88)
    b2 = ax3.bar(xm,     cr_imc, w, label="⑤ CreditRing：主机 DRAM (IMC)", color="#1a7f37", alpha=0.95)
    b3 = ax3.bar(xm + w, cr_ddr, w, label="⑤ CreditRing：卡侧 DDR5 卸载", color="#3b82f6", alpha=0.80)

    for bars, col in [(b1, "#991b1b"), (b2, "#15803d"), (b3, "#1d4ed8")]:
        for bar in bars:
            h = bar.get_height()
            if h >= 1.0:
                ax3.text(bar.get_x() + bar.get_width() / 2, h + 4, f"{h:.0f}", ha="center", va="bottom", fontsize=7.8, color=col, fontweight="bold")
            else:
                ax3.text(bar.get_x() + bar.get_width() / 2, h + 4, f"{h:.1f}", ha="center", va="bottom", fontsize=7.5, color=col)

    ratio_128 = lm_imc[-1] / max(cr_imc[-1], 0.01)
    ax3.text(
        0.04, 0.94,
        f"128 MiB 单次突发主机 DRAM 削减 {ratio_128:.0f}×\n(≤16 MiB 驻留 CN106 48 MiB L3，\n >48 MiB 平滑卸至卡 6 通道 DDR5)",
        transform=ax3.transAxes, va="top", ha="left", fontsize=8.4, color="#1e293b",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#f8fafc", edgecolor="#cbd5e1", lw=0.9),
    )

    ax3.set_xticks(xm)
    ax3.set_xticklabels(["16M", "32M", "64M", "128M"])
    ax3.set_xlabel("突发规模（1500 B 帧）")
    ax3.set_ylabel("单次突发内存读写量（MiB / 突发）")
    ax3.set_ylim(0, max(max(lm_imc), max(cr_ddr)) * 1.18)
    ax3.set_title("C. 主机 IMC 削减与板卡 L3/DDR5 卸载", fontsize=11.0, fontweight="bold")
    ax3.legend(loc="upper left", bbox_to_anchor=(0.0, 0.72), fontsize=7.9, framealpha=0.92)

    fig.suptitle(
        f"MRV-213（1500 B 满线速 100GbE，n=3 中位数）：大帧按字节达 100GbE 线速（64 B 小包仍低于 WC），DDIO {cr_ddio:.1f}% 且主机 IMC 锐减 {ratio_128:.0f}×",
        fontsize=11.3, fontweight="bold", y=0.99,
    )
    out = mrvplot.save(fig, "mrv-213-drain-ddio-imc")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
