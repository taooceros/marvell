"""MRV-213 Figure 2: 1500 B full-100GbE burst drain rate distinguishing gross apparent drain
(including 1,024 pre-staged packets in host hot ring) from net PCIe SDP FIFO drain
(fifo_deq / drain_s = 95.7 -> 103.3 Gbps, 16->128 MiB marginal slope 103.45 Gbps wire /
102.09 Gbps L2, host IIO 12.95 GB/s > 11.46 GB/s 64 B TLP wall), host DDIO LLC hit rate,
and Host IMC vs CN106 DDR5 memory-traffic offloading across burst sizes (n=3 medians).

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
BURST_LABELS = ["64K*", "256K*", "1M", "4M", "16M", "32M", "64M", "128M"]


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
        else:
            assert cs["sdp_tx_drops"] == 0 and cs["sdp_tx_stalls"] == 0 and cs["fifo_enq"] == 0


def main():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    by_key = defaultdict(list)
    for r in rows:
        assert_verified(r)
        if r["part"] == "e2_1500b_kv32m":
            by_key[(r["arm_tag"], r["burst_label"])].append(r)

    x = np.arange(len(BURST_ORDER))
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(17.8, 5.85), gridspec_kw={"width_ratios": [1.26, 1.02, 1.10]})

    # ---------------- Panel A: 1500B Consumer Drain Throughput (lossless arms) ----------------
    # Distinguish apparent total drain (includes 1,024 pre-staged hot-ring pkts) from net PCIe SDP FIFO drain.
    host_arms = [
        ("large_mono", "① 大环单调池 (主机冷DRAM)", "#dc2626", "o",  "-",  1.9),
        ("large_lifo", "③ 大环LIFO池 (主机冷DRAM)", "#f97316", "s",  "--", 1.7),
    ]
    ax1.axhline(100.0, color="#0284c7", linestyle="--", linewidth=1.2, zorder=1)
    ax1.text(0.05, 107.0, "100GbE 满线速", color="#0369a1", fontsize=8.2, fontweight="bold")
    ax1.axhline(90.9, color="#b91c1c", linestyle=":", linewidth=1.1, zorder=1)
    ax1.text(0.05, 81.0, "MRV-165 64B WC 墙 (90.9G)", color="#991b1b", fontsize=7.6, va="top")
    ax1.axvline(2.35, color="#cbd5e1", linestyle=":", linewidth=1.1, zorder=0)

    for tag, label, col, mk, ls, lw in host_arms:
        ys = [med([r["drain_wire_gbps"] for r in by_key[(tag, b)]]) for b in BURST_ORDER]
        ax1.plot(x, ys, label=label, color=col, marker=mk, linestyle=ls, linewidth=lw, markersize=5.2)

    cr_gross = [med([r["drain_wire_gbps"] for r in by_key[("credit_ring", b)]]) for b in BURST_ORDER]
    pcie_bursts = BURST_ORDER[3:]  # 4MiB .. 128MiB where card FIFO holds burst tail
    x_pcie = x[3:]
    cr_net_w = [
        med([r["card_stats"]["fifo_deq"] * r["wire_bytes"] * 8.0 / r["drain_s"] / 1e9 for r in by_key[("credit_ring", b)]])
        for b in pcie_bursts
    ]
    cr_net_l2 = [
        med([r["card_stats"]["fifo_deq"] * r["l2_bytes"] * 8.0 / r["drain_s"] / 1e9 for r in by_key[("credit_ring", b)]])
        for b in pcie_bursts
    ]
    dp16 = med([r["card_stats"]["fifo_deq"] / r["bursts"] for r in by_key[("credit_ring", "16MiB")]])
    dp128 = med([r["card_stats"]["fifo_deq"] / r["bursts"] for r in by_key[("credit_ring", "128MiB")]])
    dt16 = med([r["drain_s"] / r["bursts"] for r in by_key[("credit_ring", "16MiB")]])
    dt128 = med([r["drain_s"] / r["bursts"] for r in by_key[("credit_ring", "128MiB")]])
    slope_wire = (dp128 - dp16) * 1520.0 * 8.0 / (dt128 - dt16) / 1e9
    slope_l2 = (dp128 - dp16) * 1500.0 * 8.0 / (dt128 - dt16) / 1e9

    iio_128_gbs = med([
        (r["host_pmu"]["iio_inb_wr_bytes"] / r["rx_pkts"]) * (r["rx_pkts"] / r["drain_s"]) / 1e9
        for r in by_key[("credit_ring", "128MiB")]
    ])

    ax1.plot(
        x, cr_gross, label="⑤ CreditRing 表观总排空",
        color="#4ade80", marker="d", linestyle="--", linewidth=1.7, markersize=5.2, zorder=4,
    )
    ax1.plot(
        x_pcie, cr_net_w, label="⑤ CreditRing 经PCIe净排空",
        color="#15803d", marker="D", linestyle="-", linewidth=2.5, markersize=5.8, zorder=6,
    )

    lm_128 = med([r["drain_wire_gbps"] for r in by_key[("large_mono", "128MiB")]])
    ax1.annotate(
        f"① 主机冷DRAM: {lm_128:.1f}G (非PCIe)",
        xy=(x[-1], lm_128), xytext=(3.85, 224),
        fontsize=7.7, color="#dc2626", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.0),
    )
    ax1.text(0.06, 355, "≤1 MiB 命中主机L3\n(核内 L3 直接消费)", fontsize=7.8, color="#475569")

    # Zoomed inset for 4M..128M placed cleanly below top 2-col legend and above red/orange curves
    axins = ax1.inset_axes([0.41, 0.41, 0.57, 0.41])
    axins.set_facecolor("#ffffff")
    axins.grid(True, alpha=0.28)
    xi = np.arange(len(pcie_bursts))
    axins.axhline(126.03, color="#64748b", linestyle="-.", linewidth=1.1, zorder=1)
    axins.text(3.98, 127.6, "PCIe Gen5×4 裸上限 126.0G", color="#475569", fontsize=6.6, fontweight="bold", ha="right")
    axins.axhline(100.0, color="#0284c7", linestyle="--", linewidth=1.1, zorder=1)
    axins.axhline(90.9, color="#b91c1c", linestyle=":", linewidth=1.0, zorder=1)
    axins.text(3.98, 89.2, "64B WC 墙 90.9G", color="#991b1b", fontsize=6.5, ha="right", va="top")

    axins.plot(xi, cr_gross[3:], color="#4ade80", marker="d", linestyle="--", linewidth=1.6, markersize=4.4, zorder=3)
    axins.plot(xi, cr_net_w, color="#15803d", marker="D", linestyle="-", linewidth=2.3, markersize=4.8, zorder=5)

    axins.annotate(
        f"表观 {cr_gross[3]:.1f}G (含热环1,024包)",
        xy=(0, cr_gross[3]), xytext=(0.45, 155.5),
        fontsize=6.7, color="#16a34a", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#16a34a", lw=0.9),
    )
    axins.text(0.08, cr_net_w[0] - 2.2, f"{cr_net_w[0]:.1f}", fontsize=6.8, color="#15803d", fontweight="bold", va="top")
    axins.text(1.0, cr_net_w[1] - 2.4, f"{cr_net_w[1]:.1f}", fontsize=6.8, color="#15803d", fontweight="bold", ha="center", va="top")
    axins.annotate(
        f"净 {cr_net_w[-1]:.1f}G",
        xy=(4.0, cr_net_w[-1]), xytext=(3.15, 113.5),
        fontsize=7.0, color="#15803d", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#15803d", lw=0.9),
    )
    axins.text(
        0.98, 0.78,
        f"16→128M 斜率 {slope_wire:.2f}G (L2 {slope_l2:.2f}G)\nIIO {iio_128_gbs:.2f} > 11.46 GB/s (64B TLP墙)",
        transform=axins.transAxes, ha="right", va="top", fontsize=6.4, color="#14532d", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.20", facecolor="#f0fdf4", edgecolor="#15803d", lw=0.75),
    )
    axins.set_xticks(xi)
    axins.set_xticklabels(["4M", "16M", "32M", "64M", "128M"], fontsize=7.0)
    axins.set_ylim(81, 168)
    axins.tick_params(axis="y", labelsize=7.0)
    axins.set_title("放大：表观总排空 vs 经 PCIe 净排空 (Gbps)", fontsize=7.3, fontweight="bold", pad=2.5)

    ax1.annotate(
        f"⑤ 128 MiB 经 PCIe 净排空: {cr_net_w[-1]:.1f} Gbps (L2 {cr_net_l2[-1]:.1f}G)\n表观总排空 {cr_gross[-1]:.1f} Gbps，满 100GbE 线速",
        xy=(x[-1], cr_net_w[-1]), xytext=(2.60, 14),
        fontsize=7.7, color="#15803d", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.24", facecolor="#f0fdf4", edgecolor="#15803d", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#15803d", lw=1.1),
    )
    ax1.set_xticks(x)
    ax1.set_xticklabels(BURST_LABELS)
    ax1.set_xlabel("突发规模（1500 B 帧；*实发均 256 包 / 375 KiB）")
    ax1.set_ylabel("消费排空速率（Gbps 线速等效）")
    ax1.set_ylim(0, 695)
    ax1.set_title("A. 1500 B 表观 vs 经 PCIe 净排空（64 B 见图 3B）", fontsize=10.5, fontweight="bold")
    ax1.legend(loc="upper center", ncol=2, fontsize=6.9, framealpha=0.92, columnspacing=0.8, handletextpad=0.4)
    # ---------------- Panel B: Host DDIO LLC Hit Rate (%) ----------------
    ddio_arms = [
        ("large_mono",  "① 大环单调池",            "#dc2626", "o",  "-",  2.0),
        ("large_lifo",  "③ 大环LIFO池",            "#f97316", "s",  "--", 1.8),
        ("native_bp",   "④ 原生SDP (512槽, BP_EN=0)", "#64748b", "v",  "-.", 1.5),
        ("credit_ring", "⑤ CreditRing",            "#1a7f37", "D",  "-",  2.6),
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
    ax2.set_xlabel("突发规模（1500 B 帧；*实发均 256 包 / 375 KiB）")
    ax2.set_ylabel("主机 PCIe→L3 DDIO 命中率（%）")
    ax2.set_ylim(-3, 108)
    ax2.set_title("B. 主机 DDIO 命中率（32×CHA 实测）", fontsize=11.0, fontweight="bold")
    ax2.legend(loc="center left", fontsize=7.9, framealpha=0.92)

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
        f"MRV-213（1500 B 满线速 100GbE，n=3 中位数）：经 PCIe 净排空达 {cr_net_w[-1]:.1f} Gbps（斜率 {slope_wire:.2f}G），DDIO {cr_ddio:.1f}% 且主机 IMC 锐减 {ratio_128:.0f}×",
        fontsize=11.2, fontweight="bold", y=0.99,
    )
    out = mrvplot.save(fig, "mrv-213-drain-ddio-imc")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
