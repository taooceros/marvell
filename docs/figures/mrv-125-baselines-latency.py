"""MRV-125 Figure 3: Deployment baselines (p1 shaped vs p2c unshaped batch-stamp vs
zero-card-change pure host p3a/p3b vs p2a) with explicit p50->p99 trade-off on
2KiB_rand_128m, tx_stamp_batch bias note, and clean unloaded W=1 latency (p4b).
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

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.0, 6.2), gridspec_kw={"width_ratios": [1.22, 1.0]})

    # --- Panel (a): Throughput vs p99 (trajectories) + p50->p99 vertical stems on 2KiB_rand_128m ---
    regimes = [
        ("p1_wc1_shaped_bts", "p1 卡侧整形 (WC1 W=128, T_pause=1µs, 批戳)", "#0f766e", "o", "-"),
        ("p2c_wc1_unsh_bts", "p2c 无整形+批戳 (WC1 W=64, T_pause=0)", "#d97706", "P", "-"),
        ("p3a_wc1_host_w32", "p3a 零改卡纯主机 (WC1 W=32, T_pause=0)", "#2563eb", "^", "--"),
        ("p3b_wc4_host_b8", "p3b 零改卡 4 核 (WC4 W=48, b=8, T_pause=0)", "#7c3aed", "D", "-."),
        ("p2a_wc1_unsh_pkt", "p2a 原基线 (WC1 W=64, T_pause=0, 逐包戳)", "#94a3b8", "s", ":"),
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
            ax1.scatter(xs, ys, color=col, marker=mk, s=22, alpha=0.32, zorder=3)
            xs_med.append(statistics.median(xs))
            ys_med.append(statistics.median(ys))
        ax1.plot(xs_med, ys_med, color=col, marker=mk, linestyle=ls, linewidth=1.9, markersize=7.2,
                 markeredgecolor="white", markeredgewidth=0.7, label=lbl, zorder=4)

        # Draw p50 -> p99 vertical stem at 2KiB_rand_128m to expose p50 blowup (p2c 2.91 -> p1 5.75 us)
        rs_2k = by_pc[(part, "2KiB_rand_128m")]
        xm = statistics.median([r["mops"] for r in rs_2k])
        p50m = statistics.median([r["tot_us"]["p50"] for r in rs_2k])
        p99m = statistics.median([r["tot_us"]["p99"] for r in rs_2k])
        ax1.plot([xm, xm], [p50m, p99m], color=col, linestyle="-", linewidth=1.6, alpha=0.72, zorder=3)
        ax1.scatter([xm], [p50m], facecolors="white", edgecolors=col, marker=mk, s=48, linewidths=1.5, zorder=6)

    ax1.scatter([], [], facecolors="white", edgecolors="#334155", marker="o", s=44, linewidths=1.4,
                label="空心标+竖线 = 2KiB_rand 对应 tot p50 → p99")

    ann_specs = [
        ("p1_wc1_shaped_bts", "64B_seq_4m", "p1 64B seq\n50.9, p99=3.19", (-8, 25), "#0f766e"),
        ("p1_wc1_shaped_bts", "64B_rand_4m", "p1 64B/2112B\n44.4 / 43.9", (10, 28), "#0f766e"),
        ("p1_wc1_shaped_bts", "2KiB_rand_128m", "p1 2KiB: 22.3 M/s\np50=5.75 (+98%), p99=6.57", (72, 24), "#0f766e"),
        ("p2c_wc1_unsh_bts", "2KiB_rand_128m", "p2c 2KiB: 19.2 M/s\np50=2.91, p99=6.43", (76, 6), "#b45309"),
        ("p2c_wc1_unsh_bts", "64B_seq_4m", "p2c seq\n32.9, p99=5.04", (44, 24), "#b45309"),
        ("p2a_wc1_unsh_pkt", "64B_rand_4m", "p2a 4MiB 慢态\n14.7, p99=6.58", (-10, 25), "#475569"),
        ("p3a_wc1_host_w32", "64B_seq_4m", "p3a 热 18.0\np99=2.06", (48, -14), "#2563eb"),
        ("p3a_wc1_host_w32", "2KiB_rand_128m", "p3a 2KiB: 14.6\np50=2.06/p99=3.94", (-34, 24), "#2563eb"),
        ("p3b_wc4_host_b8", "64B_seq_4m", "p3b WC4 热\n20.6 M/s", (38, 12), "#7c3aed"),
        ("p3b_wc4_host_b8", "2KiB_rand_128m", "p3b WC4 2KiB: 12.3\np50=1.47/p99=2.89", (58, -10), "#7c3aed"),
    ]
    for part, cfg, txt, (dx, dy), col in ann_specs:
        rs = by_pc[(part, cfg)]
        xm = statistics.median([r["mops"] for r in rs])
        ym = statistics.median([r["tot_us"]["p99"] for r in rs])
        if cfg == "2KiB_rand_128m" and part in ("p2c_wc1_unsh_bts", "p3b_wc4_host_b8"):
            ym = statistics.median([r["tot_us"]["p50"] for r in rs])
        ax1.annotate(
            txt, xy=(xm, ym), xytext=(dx, dy), textcoords="offset points",
            fontsize=7.6, color=col, ha="center", va="center",
            arrowprops=dict(arrowstyle="->", color=col, lw=0.85, alpha=0.8)
        )

    ax1.text(
        56.2, 8.22,
        "注：tx_stamp_batch 批首打戳在 p1 2KiB 下将 ~0.35 µs 均值 / ~0.70 µs 批末\n"
        "主机发送排队计入 wire_us（对 tot_us 偏保守）；静态 T_pause=1 µs 另伤轻载",
        ha="right", va="top", fontsize=7.4, color="#334155",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#fffbeb", edgecolor="#d97706", lw=0.8, alpha=0.95)
    )

    ax1.set_xlim(6, 57)
    ax1.set_ylim(0.9, 8.45)
    ax1.set_xlabel("闭环实测吞吐（Mmsg/s）", fontsize=10)
    ax1.set_ylabel("端到端延迟 tot p99 轨迹与 2KiB p50（µs）", fontsize=10)
    ax1.set_title("(a) 卡侧整形 vs 无整形批戳 p2c vs 零改卡纯主机：吞吐–延迟权衡", fontsize=10.1, fontweight="bold", pad=8)
    ax1.legend(loc="lower right", frameon=True, facecolor="white", fontsize=7.5)

    # --- Panel (b): Unloaded W=1 latency (p4b_wc1_w1_unloaded, 4x warmup) ---
    w1_tiers = [
        ("64B_seq_4m", "64B seq\n4 MiB"),
        ("2KiB_seq_128m", "2KiB seq\n128 MiB"),
        ("64B_rand_4m", "64B rand\n4 MiB"),
        ("2112B_rand_132m", "2112B rand\n(4MiB活跃)"),
        ("2KiB_cold_128m", "2KiB cold\n128 MiB"),
        ("2KiB_rand_128m", "2KiB rand\n(2^11别名)"),
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
            label="空载 W=1 p50（µs，T_pause=0）", zorder=3)
    ax2.bar(x2 + bw2 / 2, p99_med, width=bw2, color="#cbd5e1", edgecolor="#475569", linewidth=0.8,
            label="空载 W=1 p99（µs，T_pause=0）", zorder=3)

    for i in range(len(w1_tiers)):
        ax2.scatter([x2[i] - bw2 / 2] * len(p50_all[i]), p50_all[i], color="#042f2e", s=14, zorder=5)
        ax2.scatter([x2[i] + bw2 / 2] * len(p99_all[i]), p99_all[i], color="#1e293b", s=14, zorder=5)
        ax2.text(x2[i] - bw2 / 2, p50_med[i] + 0.014, f"{p50_med[i]:.2f}",
                 ha="center", va="bottom", fontsize=8.3, fontweight="bold", color="#0f766e")
        ax2.text(x2[i] + bw2 / 2, p99_med[i] + 0.014, f"{p99_med[i]:.2f}",
                 ha="center", va="bottom", fontsize=8.1, color="#475569")

    ax2.set_xticks(x2)
    ax2.set_xticklabels([f"{lbl}\nllcM={m:.2f}" for (_, lbl), m in zip(w1_tiers, llc_m)], fontsize=8.2)
    ax2.set_ylim(0.75, 1.32)
    ax2.set_ylabel("空载 W=1 往返延迟（µs）", fontsize=10)
    ax2.set_title("(b) 空载 W=1 干净延迟（T_pause=0, 4× 满池热身）", fontsize=10.1, fontweight="bold", pad=8)
    ax2.legend(loc="upper left", frameon=True, facecolor="white", fontsize=8.1)
    ax2.text(
        5.42, 1.295,
        "1/R − mean_RTT = 15.0 ns\n全 18 轮 RX DDIO = 100.00%",
        ha="right", va="top", fontsize=8.1, color="#0f766e",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#f0fdfa", edgecolor="#0f766e", lw=0.8)
    )

    fig.suptitle(
        "MRV-125 工程部署对照与空载锚点：补入 p2c 无整形批戳、显式呈现 p1 延迟代价及 W=1 满池热身（p4b）",
        fontsize=10.7, y=0.99
    )

    png = save(fig, "mrv-125-baselines-latency")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
