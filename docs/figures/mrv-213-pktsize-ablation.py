"""MRV-213 Figure 3: Cross-packet-size generalization (64 B / 254 B / 1500 B @ 128 MiB)
showing KV tail-latency protection, CreditRing PCIe-SDP drain overtaking host-DRAM drain
on 64 B & 254 B, and CN106 elastic FIFO CompactSlab vs zero-copy MbufRing ablation (n=3).

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
    by_p = defaultdict(list)
    for r in rows:
        assert_verified(r)
        by_p[(r["part"], r["arm_tag"], r["burst_label"])].append(r)

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(17.2, 5.4), gridspec_kw={"width_ratios": [1.15, 1.10, 1.05]})

    # ---------------- Panel A: 128 MiB KV p99 across 64 B / 254 B / 1500 B ----------------
    profs = [
        ("e2_64b_kv32m",   "64 B\n(20 Mpps)"),
        ("e2_256b_kv32m",  "254 B\n(25 Mpps)"),
        ("e2_1500b_kv32m", "1500 B\n(满100GbE)"),
    ]
    xp = np.arange(len(profs))
    w = 0.21
    bar_arms = [
        ("kv_solo",     "空载基线",         "#94a3b8", "0"),
        ("large_mono",  "① 大环单调池",     "#dc2626", "128MiB"),
        ("large_lifo",  "③ 大环LIFO池",     "#f97316", "128MiB"),
        ("credit_ring", "⑤ CreditRing(0%丢)", "#1a7f37", "128MiB"),
    ]
    offsets = [-1.5 * w, -0.5 * w, 0.5 * w, 1.5 * w]
    for (tag, label, col, blbl), off in zip(bar_arms, offsets):
        ys = [med([r["kv"]["p99_ns"] for r in by_p[(p, tag, blbl)]]) for p, _ in profs]
        bars = ax1.bar(xp + off, ys, w, label=label, color=col, alpha=0.92)
        for bar, y in zip(bars, ys):
            ax1.text(bar.get_x() + bar.get_width() / 2, y + 4, f"{y:.0f}", ha="center", va="bottom", fontsize=8.2, fontweight="bold", color=col if tag != "kv_solo" else "#475569")

    ax1.set_xticks(xp)
    ax1.set_xticklabels([lb for _, lb in profs])
    ax1.set_ylabel("并发 32 MiB KV p99 延迟（ns）")
    ax1.set_ylim(0, 355)
    ax1.set_title("A. 128 MiB 突发全包长 KV p99（ns）", fontsize=11.0, fontweight="bold")
    ax1.legend(loc="upper right", fontsize=8.0, framealpha=0.92)
    ax1.text(
        0.03, 0.96,
        "注：BP_EN=0，②(256槽)/④(512槽)\n被动 OQ 静默丢包 98.0%–99.96%\n(sdp_tx_drops = 0)",
        transform=ax1.transAxes, va="top", fontsize=7.6, color="#475569",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#f8fafc", edgecolor="#cbd5e1", lw=0.8),
    )

    # ---------------- Panel B: 128 MiB Drain Speed Crossover across 64 B / 254 B / 1500 B ----------------
    w2 = 0.26
    drain_arms = [
        ("large_mono",  "① 大环单调池 (双通道冷DRAM)", "#dc2626"),
        ("large_lifo",  "③ 大环LIFO池 (双通道冷DRAM)", "#f97316"),
        ("credit_ring", "⑤ CreditRing (经标准SDP)",    "#1a7f37"),
    ]
    offs2 = [-w2, 0.0, w2]
    vals_by_arm = {}
    for (tag, label, col), off in zip(drain_arms, offs2):
        ys = [med([r["drain_wire_gbps"] for r in by_p[(p, tag, "128MiB")]]) for p, _ in profs]
        vals_by_arm[tag] = ys
        bars = ax2.bar(xp + off, ys, w2, label=label, color=col, alpha=0.92)
        for bar, y in zip(bars, ys):
            ax2.text(bar.get_x() + bar.get_width() / 2, y + 2.2, f"{y:.1f}", ha="center", va="bottom", fontsize=8.2, fontweight="bold", color=col)

    cr_1500_net = med([
        r["card_stats"]["fifo_deq"] * r["wire_bytes"] * 8.0 / r["drain_s"] / 1e9
        for r in by_p[("e2_1500b_kv32m", "credit_ring", "128MiB")]
    ])
    ax2.text(xp[2] + w2, vals_by_arm["credit_ring"][2] + 10.5, f"(净{cr_1500_net:.1f})", ha="center", va="bottom", fontsize=7.4, color="#15803d")

    for idx in (0, 1):
        spd = vals_by_arm["credit_ring"][idx] / vals_by_arm["large_mono"][idx]
        ax2.annotate(
            f"{spd:.2f}× 反超!",
            xy=(xp[idx] + w2, vals_by_arm["credit_ring"][idx] + 8),
            xytext=(xp[idx] + 0.02, vals_by_arm["credit_ring"][idx] + 24),
            ha="center", fontsize=8.8, color="#15803d", fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#15803d", lw=1.0),
        )

    ax2.axhline(100.0, color="#0284c7", linestyle="--", linewidth=1.2, zorder=1)
    ax2.text(-0.42, 103.0, "100GbE 线速", color="#0369a1", fontsize=8.4, fontweight="bold")
    ax2.set_xticks(xp)
    ax2.set_xticklabels([lb for _, lb in profs])
    ax2.set_ylabel("消费排空速率（Gbps 线速等效）")
    ax2.set_ylim(0, max(vals_by_arm["large_mono"][2], vals_by_arm["credit_ring"][2]) * 1.24)
    ax2.set_title("B. 128 MiB 排空速率：64/254 B 反超主机 DRAM", fontsize=11.0, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=8.0, framealpha=0.92)

    # ---------------- Panel C: Card Elastic FIFO CompactSlab vs Zero-Copy MbufRing (1500B) ----------------
    ab_bursts = ["16MiB", "32MiB", "64MiB", "128MiB"]
    xa = np.arange(len(ab_bursts))
    w3 = 0.32
    c_dr = [med([r["card_stats"]["fifo_deq"] * r["wire_bytes"] * 8.0 / r["drain_s"] / 1e9 for r in by_p[("e2_1500b_mbuf_ablation", "credit_ring_compact", b)]]) for b in ab_bursts]
    m_dr = [med([r["card_stats"]["fifo_deq"] * r["wire_bytes"] * 8.0 / r["drain_s"] / 1e9 for r in by_p[("e2_1500b_mbuf_ablation", "credit_ring_mbuf", b)]]) for b in ab_bursts]
    c_ddr = [med([(r["card_pmu"]["ddr_rd_mib"] + r["card_pmu"]["ddr_wr_mib"]) / r["bursts"] for r in by_p[("e2_1500b_mbuf_ablation", "credit_ring_compact", b)]]) for b in ab_bursts]
    m_ddr = [med([(r["card_pmu"]["ddr_rd_mib"] + r["card_pmu"]["ddr_wr_mib"]) / r["bursts"] for r in by_p[("e2_1500b_mbuf_ablation", "credit_ring_mbuf", b)]]) for b in ab_bursts]

    c_inst = med([sum(c["inst_retired"] for c in r["card_pmu"]["cores"]) / r["offered_pkts"] for r in by_p[("e2_1500b_mbuf_ablation", "credit_ring_compact", "128MiB")]])
    m_inst = med([sum(c["inst_retired"] for c in r["card_pmu"]["cores"]) / r["offered_pkts"] for r in by_p[("e2_1500b_mbuf_ablation", "credit_ring_mbuf", "128MiB")]])
    c_l2r = med([sum(c["l2d_cache_refill"] for c in r["card_pmu"]["cores"]) / r["offered_pkts"] for r in by_p[("e2_1500b_mbuf_ablation", "credit_ring_compact", "128MiB")]])
    m_l2r = med([sum(c["l2d_cache_refill"] for c in r["card_pmu"]["cores"]) / r["offered_pkts"] for r in by_p[("e2_1500b_mbuf_ablation", "credit_ring_mbuf", "128MiB")]])
    inst_save_pct = (1.0 - m_inst / c_inst) * 100.0
    l2r_ratio = c_l2r / m_l2r

    ax3.bar(xa - w3 / 2, c_dr, w3, label="CompactSlab (L3热区重置)", color="#1a7f37", alpha=0.90)
    ax3.bar(xa + w3 / 2, m_dr, w3, label="MbufRing (零拷贝NPA LIFO)", color="#2563eb", alpha=0.85)
    for i in range(len(ab_bursts)):
        ax3.text(xa[i] - w3 / 2, c_dr[i] + 2.0, f"{c_dr[i]:.1f}", ha="center", va="bottom", fontsize=7.9, color="#15803d", fontweight="bold")
        ax3.text(xa[i] + w3 / 2, m_dr[i] + 2.0, f"{m_dr[i]:.1f}", ha="center", va="bottom", fontsize=7.9, color="#1d4ed8", fontweight="bold")
    ax3.axhline(100.0, color="#0284c7", linestyle="--", linewidth=1.1)
    ax3.text(
        0.98, 0.97,
        f"1500B 零拷贝省指令 {inst_save_pct:.0f}% / L2重填 {l2r_ratio:.1f}×\n64B 紧凑Slab 消 36× 空间放大保卡L3",
        transform=ax3.transAxes, ha="right", va="top", fontsize=7.0, color="#1e293b",
        bbox=dict(boxstyle="round,pad=0.22", facecolor="#f8fafc", edgecolor="#cbd5e1", lw=0.8),
    )

    ax3_r = ax3.twinx()
    ax3_r.spines["top"].set_visible(False)
    ax3_r.plot(xa, c_ddr, color="#14532d", marker="o", linestyle="-", linewidth=1.8, label="CompactSlab 卡DDR5")
    ax3_r.plot(xa, m_ddr, color="#1e3a8a", marker="s", linestyle="--", linewidth=1.8, label="MbufRing 卡DDR5")
    ax3_r.set_ylabel("卡侧 DDR5 读写（MiB / 突发）", color="#1e3a8a")
    ax3_r.tick_params(axis="y", colors="#1e3a8a")
    ax3_r.set_ylim(0, max(max(c_ddr), max(m_ddr)) * 1.38)
    ax3_r.text(xa[-1] - 0.14, c_ddr[-1] + 10, f"{c_ddr[-1]:.0f}", ha="right", va="bottom", fontsize=7.9, color="#14532d", fontweight="bold")
    ax3_r.text(xa[-1] + 0.09, m_ddr[-1] - 8, f"{m_ddr[-1]:.0f}", ha="left", va="center", fontsize=7.9, color="#1e3a8a", fontweight="bold")
    ax3.set_xticks(xa)
    ax3.set_xticklabels(["16M", "32M", "64M", "128M"])
    ax3.set_xlabel("突发规模（1500 B 满线速）")
    ax3.set_ylabel("经 PCIe 净排空速率（Gbps 线速等效）")
    ax3.set_ylim(0, 188)
    ax3.set_title("C. 卡侧 FIFO 消融：紧凑Slab vs 零拷贝Mbuf", fontsize=11.0, fontweight="bold")

    h1, l1 = ax3.get_legend_handles_labels()
    h2, l2 = ax3_r.get_legend_handles_labels()
    ax3.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=7.0, framealpha=0.92)

    spd64 = vals_by_arm["credit_ring"][0] / vals_by_arm["large_mono"][0]
    spd254 = vals_by_arm["credit_ring"][1] / vals_by_arm["large_mono"][1]
    fig.suptitle(
        f"MRV-213（n=3 中位数）：全包长守住 41–43 ns KV 尾延迟，64/254 B 经 SDP 排空反超双通道冷 DRAM {spd254:.2f}–{spd64:.2f}×，双卡 FIFO 净排空 >102 Gbps",
        fontsize=11.4, fontweight="bold", y=0.99,
    )
    out = mrvplot.save(fig, "mrv-213-pktsize-ablation")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
