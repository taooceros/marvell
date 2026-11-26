#!/usr/bin/env python3
"""Plot Figure 1 for MRV-212: 5-tier credit signaling CPU cost & control-loop RTT / 100GbE BDP."""

import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs" / "mrv212-2026-11-26" / "runs.jsonl"


def load_runs():
    by_gt = defaultdict(list)
    with RUNS.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            by_gt[(r["group"], r["tag"])].append(r["data"])
    return by_gt


def med(vals):
    return statistics.median(vals)


def main():
    by_gt = load_runs()

    # Verify zero bad/torn/pf on every plotted run
    for grp in ("A0_micro", "A2_rtt"):
        for (g, tag), rows in by_gt.items():
            if g != grp:
                continue
            assert len(rows) == 3, f"expected n=3 for {g}/{tag}, got {len(rows)}"
            for d in rows:
                assert d.get("page_faults", 0) == 0
                assert d.get("bad", 0) == 0 and d.get("torn", 0) == 0 and d.get("lost", 0) == 0
                assert d["host_pmu"]["min_pct"] == 100.0
                if d.get("card_pmu"):
                    assert d["card_pmu"]["min_pct"] == 100.0

    # Panel A: A0_micro body=512 (realistic B=32 spacing ~168 ns) delta cycles/batch vs off
    off_rows = by_gt[("A0_micro", "micro_b512_off")]
    off_med = med([d["cycles_per_batch"] for d in off_rows])

    tiers = [
        ("bar4-nofence", "BAR4 无 SFENCE\nMOVDIR64B", "#1f77b4"),
        ("hostmem-dpi", "卡侧读主机内存\n(DPI FromHost)", "#7f8c8d"),
        ("bar4-uc64", "BAR4 普通写\nUC write64", "#95a5a6"),
        ("bar4-sfence", "BAR4 带 SFENCE\nMOVDIR64B", "#d64541"),
    ]
    delta_batch, delta_min, delta_max = [], [], []
    for key, _, _ in tiers:
        vals = [d["cycles_per_batch"] - off_med for d in by_gt[("A0_micro", f"micro_b512_{key}")]]
        delta_batch.append(med(vals))
        delta_min.append(min(vals))
        delta_max.append(max(vals))

    # Panel B: A2_rtt p50 (us) and 100GbE BDP (KiB) for 64 B and 1500 B
    rtt_tiers = [
        ("bar4-nofence", "BAR4\n无 SFENCE"),
        ("bar4-sfence", "BAR4\n带 SFENCE"),
        ("bar4-uc64", "BAR4\nUC 64位"),
        ("hostmem-dpi", "卡侧读\n主机内存"),
    ]
    rtt64 = [med([d["p50_us"] for d in by_gt[("A2_rtt", f"credit_rtt_64B_{k}")]]) for k, _ in rtt_tiers]
    rtt1500 = [med([d["p50_us"] for d in by_gt[("A2_rtt", f"credit_rtt_1500B_{k}")]]) for k, _ in rtt_tiers]
    bdp64 = [med([d["bdp_100gbe_p50_kib"] for d in by_gt[("A2_rtt", f"credit_rtt_64B_{k}")]]) for k, _ in rtt_tiers]
    bdp1500 = [med([d["bdp_100gbe_p50_kib"] for d in by_gt[("A2_rtt", f"credit_rtt_1500B_{k}")]]) for k, _ in rtt_tiers]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.1))

    # ---- Left panel: Extra CPU cycles per 32-pkt credit batch ----
    y = np.arange(len(tiers))
    bars = ax1.barh(y, delta_batch, height=0.52, color=[c for _, _, c in tiers], edgecolor="#2c3e50", linewidth=0.6)
    ax1.errorbar(
        delta_batch,
        y,
        xerr=[
            [m - lo for m, lo in zip(delta_batch, delta_min)],
            [hi - m for m, hi in zip(delta_batch, delta_max)],
        ],
        fmt="none",
        ecolor="#222222",
        capsize=3,
        linewidth=1.1,
    )
    ax1.set_yticks(y)
    ax1.set_yticklabels([lbl for _, lbl, _ in tiers], fontsize=10)
    ax1.invert_yaxis()
    ax1.set_xlabel("每 32 包返还一次信用的主机新增周期（cycles/批，相对关闭信用）", fontsize=9.8)
    ax1.set_title("(a) 主机 CPU 信令开销（A0 微基准，B = 32，越低越好）", fontsize=10.9, pad=10)
    ax1.set_xlim(0, 620)
    ax1.axvline(64.0, color="#27ae60", linestyle="--", linewidth=1.2, alpha=0.85)
    ax1.text(
        74,
        1.52,
        "目标上限：2 周期/包（64 周期/批）",
        color="#1e8449",
        fontsize=9.2,
        va="center",
        bbox=dict(boxstyle="round,pad=0.25", fc="#eafaf1", ec="#27ae60", lw=0.7),
    )

    for idx, (bar, val) in enumerate(zip(bars, delta_batch)):
        per_pkt = val / 32.0
        extra = "  （比带 SFENCE 省 67×）" if idx == 0 else ("  （但控制环慢 +1.1 µs）" if idx == 1 else "")
        ax1.text(
            val + 10,
            bar.get_y() + bar.get_height() / 2,
            f"+{val:.1f} 周期/批\n(+{per_pkt:.2f} 周期/包){extra}",
            va="center",
            fontsize=9.2,
            color="#0b4f8c" if idx == 0 else "#2c3e50",
        )

    # ---- Right panel: Control-loop RTT tau_rtt & 100GbE BDP / descriptor slot lower bound ----
    x = np.arange(len(rtt_tiers))
    w = 0.36
    b1 = ax2.bar(x - w / 2, rtt64, width=w, label="64 B 小包", color="#1f77b4", edgecolor="#2c3e50", linewidth=0.6)
    b2 = ax2.bar(x + w / 2, rtt1500, width=w, label="1500 B 大包", color="#6baed6", edgecolor="#2c3e50", linewidth=0.6)
    b1[3].set_color("#95a5a6")
    b2[3].set_color("#bdc3c7")

    # Compute exact descriptor-slot BDP lower bounds R_min = lambda_pps * tau_rtt + B (B = 32)
    b_batch = 32
    rate_1500_mpps = 100e9 / (8.0 * (1500 + 20)) / 1e6  # 8.2237 Mpps
    rmin_1500_slots = rate_1500_mpps * rtt1500[0] + b_batch
    rmin_1500_kib = bdp1500[0] + (b_batch * 1500.0) / 1024.0
    rate_64_cn106_mpps = 90.0
    rate_64_line_mpps = 100e9 / (8.0 * (64 + 20)) / 1e6  # 148.8095 Mpps
    rmin_64_lo_slots = rate_64_cn106_mpps * rtt64[0] + b_batch
    rmin_64_hi_slots = rate_64_line_mpps * rtt64[0] + b_batch
    rmin_64_lo_kib = round(rmin_64_lo_slots) * 2
    rmin_64_hi_kib = round(rmin_64_hi_slots) * 2

    ax2.set_xticks(x)
    ax2.set_xticklabels([lbl for _, lbl in rtt_tiers], fontsize=9.8)
    ax2.set_ylabel("信用控制环中位 RTT  τ_rtt（µs）", fontsize=10)
    ax2.set_title("(b) 控制环 RTT 与最小热环下界（区分 1500 B / 64 B 槽位）", fontsize=10.6, pad=10)
    ax2.set_ylim(0, 5.85)
    ax2.legend(loc="upper left", ncol=2, frameon=True, fontsize=8.8)
    ax2_r = ax2.twinx()
    ax2_r.set_ylim(0, ax2.get_ylim()[1] * (12.5e3 / 1024.0))
    ax2_r.set_ylabel("12.5 GB/s 在途字节积（KiB）", fontsize=9.6, color="#555555")
    ax2_r.spines["top"].set_visible(False)
    ax2_r.grid(False)

    for bar, us in zip(b1, rtt64):
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            us + 0.08,
            f"{us:.2f} µs",
            ha="center",
            va="bottom",
            fontsize=8.3,
            color="#1a252f",
        )
    for bar, us, kib in zip(b2, rtt1500, bdp1500):
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            us + 0.08,
            f"{us:.2f} µs\n({kib:.0f} KiB)",
            ha="center",
            va="bottom",
            fontsize=8.2,
            color="#1a252f",
        )

    ax2.text(
        -0.40,
        4.42,
        f"每包 1 槽描述符下界 R_min = λ_pps·τ_rtt + B (B = 32)：\n"
        f"• 1500 B (8.2 Mpps)：~{round(rmin_1500_slots)} 槽 / {rmin_1500_kib:.1f} KiB (256–512 KiB 热环 = 3.3–6.6×)\n"
        f"• 64 B (90–148.8 Mpps)：{round(rmin_64_lo_slots)}–{round(rmin_64_hi_slots)} 槽 / {rmin_64_lo_kib}–{rmin_64_hi_kib} KiB mbuf 跨度",
        ha="left",
        va="center",
        fontsize=7.5,
        color="#0b4f8c",
        bbox=dict(boxstyle="round,pad=0.30", fc="#eef6fc", ec="#1f77b4", lw=0.8),
    )
    fig.suptitle(
        "MRV-212 对照 A：无 SFENCE BAR4 信用 A0 仅耗 +0.20 周期/包，控制环 RTT 仅 2.15–2.53 µs（n = 3，零错零缺页）",
        fontsize=11.6,
        y=0.97,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    png = mrvplot.HERE / "mrv-212-credit-cost-rtt.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-212-credit-cost-rtt.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
