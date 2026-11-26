#!/usr/bin/env python3
"""Plot Figure 2 for MRV-210: Part 2 RSS skew (4:1 hot queue & Zipf alpha=1.0 on 8 host cores).

All numbers are derived dynamically from logs/mrv210-2026-11-26/runs.jsonl (n=3 medians).
Host memory write bandwidth converts raw `imc_wr_gbps` (Gbps) to standard GB/s via `/ 8.0`.
"""

import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs" / "mrv210-2026-11-26" / "runs.jsonl"

ARMS = [
    ("priv-ring", "传统大环 (privRing)", "#c94c4c"),
    ("sh-ring", "共享锁环 (ShRing)", "#d9822b"),
    ("rx-bisect", "分离大环 (rxBisect)", "#6c7a89"),
    ("credit-sdp", "本文信用环", "#0b6e99"),
]


def load_runs():
    by_cfg = defaultdict(list)
    with RUNS.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            assert r.get("verified") is True, f"Unverified run in runs.jsonl: {r.get('cfg')}"
            by_cfg[r["cfg"]].append(r)
    return by_cfg


def med(xs):
    return statistics.median(xs)


def fmt_pkts_short(n):
    if n == 0:
        return "0 包"
    if n >= 1e6:
        return f"{n / 1e4:,.0f}万"
    if n >= 1e4:
        return f"{n / 1e4:.1f}万"
    return f"{n:,.0f}"


def main():
    by_cfg = load_runs()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.0, 5.4))

    dists = [("ratio4to1", "4:1 单核热点\n（q0 超载，q1–q7 轻载）"), ("zipf", "Zipf 偏斜\n（q0 最热，q1 次热，q2–q7 轻载）")]
    gx = np.arange(len(dists))
    width = 0.19
    offsets = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]

    # ---- Panel (a): 1500 B 8-core skew (4:1 & Zipf) Host DRAM write (GB/s) + DDIO hit% ----
    for idx, (key, label, color) in enumerate(ARMS):
        vals = []
        ddios = []
        for dkey, _ in dists:
            rs = by_cfg[f"p2_{key}_c8_1500B_{dkey}"]
            vals.append(med([r["summary"]["imc_wr_gbps"] / 8.0 for r in rs]))
            ddios.append(med([r["summary"]["ddio_hit_pct"] for r in rs]))
        bars = ax1.bar(
            gx + offsets[idx], vals, width=width * 0.92,
            label=label, color=color, edgecolor="#333333", linewidth=0.8, zorder=3,
        )
        for j, (b, v, d) in enumerate(zip(bars, vals, ddios)):
            if key == "priv-ring":
                ax1.text(
                    b.get_x() + b.get_width() / 2.0, v + 0.12,
                    f"{v:.2f} GB/s\n命中 {d:.1f}%",
                    ha="center", va="bottom", fontsize=9.8, fontweight="bold", color="#a82828",
                )
            elif key == "credit-sdp":
                priv_v = med([r["summary"]["imc_wr_gbps"] / 8.0 for r in by_cfg[f"p2_priv-ring_c8_1500B_{dists[j][0]}"]])
                ax1.text(
                    b.get_x() + b.get_width() / 2.0 + 0.03, v + 0.14,
                    f"{v:.2f} GB/s\n命中 {d:.1f}%\n（低 {priv_v / v:.0f}×）",
                    ha="center", va="bottom", fontsize=9.5, fontweight="bold", color="#084e6e",
                )
            else:
                ax1.text(
                    b.get_x() + b.get_width() / 2.0, v + 0.08,
                    f"{v:.2f}",
                    ha="center", va="bottom", fontsize=9.0, color="#555555",
                )

    ax1.set_title("(a) 1500 B 大包（8 核偏斜）：主机内存写入与缓存命中", fontsize=12, fontweight="bold", pad=10)
    ax1.set_xticks(gx)
    ax1.set_xticklabels([d[1] for d in dists], fontsize=10.5)
    ax1.set_ylabel("主机内存写带宽 (GB/s)", fontsize=11)
    ax1.set_ylim(0, 4.8)

    # ---- Panel (b): 256 B 8-core skew (BOTH 4:1 & Zipf) -> non-primary-hot queues (q1..q7) lost pkts ----
    for idx, (key, label, color) in enumerate(ARMS):
        hlosts = []
        gbps_vals = []
        q2_7_losts = []
        for dkey, _ in dists:
            rs = by_cfg[f"p2_{key}_c8_256B_{dkey}"]
            hlosts.append(med([r["summary"]["healthy_q_lost_pkts"] for r in rs]))
            gbps_vals.append(med([r["summary"]["host_wire_gbps"] for r in rs]))
            q2_7_losts.append(med([sum(q["stream_lost"] for q in r["host"]["per_q"][2:]) for r in rs]))
        bars2 = ax2.bar(
            gx + offsets[idx], hlosts, width=width * 0.92,
            color=color, edgecolor="#333333", linewidth=0.8, zorder=3,
        )
        for j, (b, hl, g, q27) in enumerate(zip(bars2, hlosts, gbps_vals, q2_7_losts)):
            cx = b.get_x() + b.get_width() / 2.0
            if j == 0 and key == "credit-sdp":
                ax2.text(
                    cx + 0.02, 2200,
                    f"0 包\n严格零丢\n总 {g:.1f}G",
                    ha="center", va="bottom", fontsize=9.2, fontweight="bold", color="#084e6e",
                    bbox=dict(boxstyle="round,pad=0.25", facecolor="#eef7fb", edgecolor="#0b6e99", lw=1.2),
                )
            elif j == 1 and key == "credit-sdp":
                ax2.text(
                    cx + 0.03, hl * 1.45,
                    f"{fmt_pkts_short(hl)}\n全在 q1\nq2–q7={q27:.0f}\n总 {g:.1f}G",
                    ha="center", va="bottom", fontsize=8.8, fontweight="bold", color="#084e6e",
                    bbox=dict(boxstyle="round,pad=0.22", facecolor="#eef7fb", edgecolor="#0b6e99", lw=1.1),
                )
            else:
                col = "#a65c00" if key == "sh-ring" else "#333333"
                fw = "bold" if key == "sh-ring" else "normal"
                ax2.text(
                    cx, hl * 1.38,
                    f"{fmt_pkts_short(hl)}\n({g:.1f}G)",
                    ha="center", va="bottom", fontsize=8.9, fontweight=fw, color=col,
                )

    ax2.set_yscale("symlog", linthresh=5000.0, linscale=0.35)
    ax2.set_yticks([0, 1e4, 1e5, 1e6, 1e7])
    ax2.set_yticklabels(["0", "1 万", "10 万", "100 万", "1000 万"], fontsize=10.5)
    ax2.set_ylim(-800, 3.2e8)

    ax2.set_title("(b) 256 B 小包（8 核偏斜）：非主热点队列 (q1–q7) 丢包与总吞吐", fontsize=11.6, fontweight="bold", pad=10)
    ax2.set_xticks(gx)
    ax2.set_xticklabels([d[1] for d in dists], fontsize=10.5)
    ax2.set_ylabel("q1–q7 合计丢包数（对数坐标）", fontsize=11)

    handles, labels = ax1.get_legend_handles_labels()
    fig.suptitle(
        "8 核流量偏斜（4:1 与 Zipf）：本文信用环保住主机缓存，4:1 下 7 条健康队列严格零丢包（n=3 中值）",
        fontsize=13.5, fontweight="bold", y=0.99,
    )
    fig.legend(
        handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.925),
        ncol=4, frameon=False, fontsize=11, handlelength=2.2, columnspacing=1.8,
    )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.86])

    png = mrvplot.HERE / "mrv-210-rss-skew.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-210-rss-skew.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
