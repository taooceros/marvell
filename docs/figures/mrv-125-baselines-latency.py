"""MRV-125 Figure 3: Deployment baselines (card-shaped vs zero-card-change pure host)
and clean unloaded W=1 latency with 4x pool-size warmup (p4b).
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv125-2026-11-22/runs.jsonl"


def load_rows():
    rows = [json.loads(line) for line in RUNS.read_text().splitlines() if line.strip()]
    for r in rows:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
        assert all(c["corrupt"] == 0 for c in r["card_pmu"]["cores"])
        assert r["host_pmu"]["page_faults"] == 0
    return rows


def main():
    rows = load_rows()
    by_pc = {}
    for r in rows:
        by_pc.setdefault((r["part"], r["cfg"]), []).append(r)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.4, 5.7), gridspec_kw={"width_ratios": [1.15, 1.0]})

    # --- Panel (a): Throughput vs p99 latency across regimes on key tiers ---
    regimes = [
        ("p1_wc1_shaped_bts", "p1 卡侧整形快态 (WC1 W=128, T_pause=1µs)", "#0f766e", "o", "-"),
        ("p3a_wc1_host_w32", "p3a 零改卡纯主机 (WC1 W=32, T_pause=0)", "#2563eb", "^", "--"),
        ("p3b_wc4_host_b8", "p3b 零改卡 4 卡核 (WC4 W=48, b=8, T_pause=0)", "#7c3aed", "D", "-."),
        ("p2a_wc1_unsh_pkt", "p2a 无整形基线 (WC1 W=64, 逐包戳)", "#94a3b8", "s", ":"),
    ]
    tier_seq = [
        ("64B_seq_4m", "64B seq"),
        ("64B_rand_4m", "64B rand"),
        ("2112B_rand_132m", "2112B rand"),
        ("2KiB_rand_128m", "2KiB rand"),
    ]

    for part, lbl, col, mk, ls in regimes:
        xs_med, ys_med = [], []
        for cfg, _ in tier_seq:
            rs = by_pc[(part, cfg)]
            xs = [r["mops"] for r in rs]
            ys = [r["tot_us"]["p99"] for r in rs]
            ax1.scatter(xs, ys, color=col, marker=mk, s=24, alpha=0.35, zorder=3)
            xs_med.append(statistics.median(xs))
            ys_med.append(statistics.median(ys))
        ax1.plot(xs_med, ys_med, color=col, marker=mk, linestyle=ls, linewidth=1.9, markersize=7.5,
                 markeredgecolor="white", markeredgewidth=0.7, label=lbl, zorder=4)

    ann_specs = [
        ("p1_wc1_shaped_bts", "64B_seq_4m", "64B seq\n50.9 M/s, 3.19µs", (-6, 26), "#0f766e"),
        ("p1_wc1_shaped_bts", "64B_rand_4m", "64B / 2112B rand\n44.4 / 43.9 M/s", (16, 32), "#0f766e"),
        ("p1_wc1_shaped_bts", "2KiB_rand_128m", "p1 2KiB rand\n22.3 M/s, 6.57µs", (46, 20), "#0f766e"),
        ("p3a_wc1_host_w32", "64B_seq_4m", "W=32 热\n18.0 M/s, 2.06µs", (46, -12), "#2563eb"),
        ("p3a_wc1_host_w32", "2KiB_rand_128m", "W=32 2KiB\n14.6 M/s, 3.94µs", (-22, 22), "#2563eb"),
        ("p3b_wc4_host_b8", "64B_seq_4m", "WC4 热\n20.6 M/s", (34, 8), "#7c3aed"),
        ("p3b_wc4_host_b8", "2KiB_rand_128m", "WC4 2KiB\n12.3 M/s, 2.89µs", (4, -28), "#7c3aed"),
        ("p2a_wc1_unsh_pkt", "64B_rand_4m", "p2a 4MiB 慢态坍缩\n14.7 M/s, 6.58µs", (4, 24), "#475569"),
        ("p2a_wc1_unsh_pkt", "64B_seq_4m", "p2a seq\n30.1 M/s, 5.63µs", (44, 6), "#475569"),
    ]
    for part, cfg, txt, (dx, dy), col in ann_specs:
        rs = by_pc[(part, cfg)]
        xm = statistics.median([r["mops"] for r in rs])
        ym = statistics.median([r["tot_us"]["p99"] for r in rs])
        ax1.annotate(
            txt, xy=(xm, ym), xytext=(dx, dy), textcoords="offset points",
            fontsize=8.0, color=col, ha="center", va="center",
            arrowprops=dict(arrowstyle="->", color=col, lw=0.85, alpha=0.75)
        )

    ax1.set_xlim(7, 57)
    ax1.set_ylim(1.2, 8.2)
    ax1.set_xlabel("闭环实测吞吐（Mmsg/s）", fontsize=10)
    ax1.set_ylabel("端到端尾延迟 tot p99（µs，单轮散点+中位轨迹）", fontsize=10)
    ax1.set_title("(a) 卡侧整形 vs 零改卡纯主机基线：吞吐–p99 权衡", fontsize=10.5, fontweight="bold", pad=8)
    ax1.legend(loc="lower right", frameon=True, facecolor="white", fontsize=8.0)

    # --- Panel (b): Unloaded W=1 latency (p4b_wc1_w1_unloaded, 4x warmup) ---
    w1_tiers = [
        ("64B_seq_4m", "64B seq\n4 MiB"),
        ("2KiB_seq_128m", "2KiB seq\n128 MiB"),
        ("64B_rand_4m", "64B rand\n4 MiB"),
        ("2112B_rand_132m", "2112B rand\n132 MiB"),
        ("2KiB_cold_128m", "2KiB cold\n128 MiB"),
        ("2KiB_rand_128m", "2KiB rand\n128 MiB"),
    ]
    x2 = np.arange(len(w1_tiers))
    p50_med, p99_med, p50_all, p99_all, llc_m = [], [], [], [], []
    for cfg, _ in w1_tiers:
        rs = by_pc[("p4b_wc1_w1_unloaded", cfg)]
        p50s = [r["tot_us"]["p50"] for r in rs]
        p99s = [r["tot_us"]["p99"] for r in rs]
        p50_all.append(p50s)
        p99_all.append(p99s)
        p50_med.append(statistics.median(p50s))
        p99_med.append(statistics.median(p99s))
        llc_m.append(statistics.median([r["host_pmu"]["llc_load_misses"] / r["measured"] for r in rs]))

    bw2 = 0.34
    ax2.bar(x2 - bw2 / 2, p50_med, width=bw2, color="#0f766e", edgecolor="#115e59", linewidth=0.8,
            label="空载 W=1 p50（µs）", zorder=3)
    ax2.bar(x2 + bw2 / 2, p99_med, width=bw2, color="#cbd5e1", edgecolor="#475569", linewidth=0.8,
            label="空载 W=1 p99（µs）", zorder=3)

    for i in range(len(w1_tiers)):
        ax2.scatter([x2[i] - bw2 / 2] * len(p50_all[i]), p50_all[i], color="#042f2e", s=14, zorder=5)
        ax2.scatter([x2[i] + bw2 / 2] * len(p99_all[i]), p99_all[i], color="#1e293b", s=14, zorder=5)
        ax2.text(x2[i] - bw2 / 2, p50_med[i] + 0.014, f"{p50_med[i]:.2f}",
                 ha="center", va="bottom", fontsize=8.3, fontweight="bold", color="#0f766e")
        ax2.text(x2[i] + bw2 / 2, p99_med[i] + 0.014, f"{p99_med[i]:.2f}",
                 ha="center", va="bottom", fontsize=8.1, color="#475569")

    ax2.set_xticks(x2)
    ax2.set_xticklabels([f"{lbl}\nllcM={m:.2f}" for (_, lbl), m in zip(w1_tiers, llc_m)], fontsize=8.4)
    ax2.set_ylim(0.75, 1.32)
    ax2.set_ylabel("空载 W=1 往返延迟（µs）", fontsize=10)
    ax2.set_title("(b) 空载 W=1 干净延迟（4× 满池热身，无热身伪影）", fontsize=10.5, fontweight="bold", pad=8)
    ax2.legend(loc="upper left", frameon=True, facecolor="white", fontsize=8.3)
    ax2.text(
        5.42, 1.295,
        "1/R − mean_RTT = 15.0 ns\n全 18 轮 RX DDIO = 100.00%",
        ha="right", va="top", fontsize=8.2, color="#0f766e",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#f0fdfa", edgecolor="#0f766e", lw=0.8)
    )

    fig.suptitle(
        "MRV-125 工程部署对照与空载锚点：零改卡纯主机基线（p3a/p3b）及 W=1 满池热身（p4b）",
        fontsize=11, y=0.99
    )

    png = save(fig, "mrv-125-baselines-latency")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
