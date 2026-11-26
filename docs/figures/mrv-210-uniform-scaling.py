#!/usr/bin/env python3
"""Plot Figure 1 for MRV-210: Part 1 uniform scaling (1 -> 2 -> 4 -> 8 host cores).

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
    ("priv-ring", "传统大环 (privRing)", "#c94c4c", "o", "--", 2.2),
    ("sh-ring", "共享锁环 (ShRing)", "#d9822b", "^", "-.", 2.2),
    ("rx-bisect", "分离大环 (rxBisect)", "#6c7a89", "s", ":", 2.2),
    ("credit-sdp", "本文信用环", "#0b6e99", "D", "-", 3.0),
]


def load_runs():
    kv_solo = []
    by_cfg = defaultdict(list)
    with RUNS.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            assert r.get("verified") is True, f"Unverified run in runs.jsonl: {r.get('cfg')}"
            if r["part"] == "part0_kv_solo":
                kv_solo.append(r)
            else:
                by_cfg[r["cfg"]].append(r)
    return kv_solo, by_cfg


def med(xs):
    return statistics.median(xs)


def main():
    kv_solo, by_cfg = load_runs()
    cores = [1, 2, 4, 8]
    x_pos = np.arange(len(cores))

    fig, axes = plt.subplots(1, 3, figsize=(15.4, 5.0))
    ax1, ax2, ax3 = axes

    # ---- Panel (a): 1500 B Host DRAM write bandwidth (GB/s) vs core count ----
    for key, label, color, marker, ls, lw in ARMS:
        ys = []
        for nc in cores:
            rs = by_cfg[f"p1_{key}_c{nc}_1500B_unif"]
            ys.append(med([r["summary"]["imc_wr_gbps"] / 8.0 for r in rs]))
        z = 5 if key == "credit-sdp" else 3
        ax1.plot(
            x_pos, ys, label=label, color=color, marker=marker,
            linestyle=ls, linewidth=lw, markersize=7.5, zorder=z,
        )

    priv_1c_gbs = med([r["summary"]["imc_wr_gbps"] / 8.0 for r in by_cfg["p1_priv-ring_c1_1500B_unif"]])
    priv_8c_gbs = med([r["summary"]["imc_wr_gbps"] / 8.0 for r in by_cfg["p1_priv-ring_c8_1500B_unif"]])
    priv_8c_ddio = med([r["summary"]["ddio_hit_pct"] for r in by_cfg["p1_priv-ring_c8_1500B_unif"]])
    cred_8c_gbs = med([r["summary"]["imc_wr_gbps"] / 8.0 for r in by_cfg["p1_credit-sdp_c8_1500B_unif"]])
    cred_8c_ddio = med([r["summary"]["ddio_hit_pct"] for r in by_cfg["p1_credit-sdp_c8_1500B_unif"]])
    ratio_1to8 = priv_8c_gbs / priv_1c_gbs
    ratio_vs_cred = priv_8c_gbs / cred_8c_gbs

    ax1.annotate(
        f"{priv_8c_gbs:.2f} GB/s（涨 {ratio_1to8:.0f}×）\n缓存命中跌至 {priv_8c_ddio:.1f}%",
        xy=(3, priv_8c_gbs),
        xytext=(0.15, 4.45),
        fontsize=10.2,
        fontweight="bold",
        color="#a82828",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#fff5f5", edgecolor="#c94c4c", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#c94c4c", lw=1.4),
    )
    ax1.annotate(
        f"本文 {cred_8c_gbs:.2f} GB/s（低 {ratio_vs_cred:.0f}×）\n缓存命中 {cred_8c_ddio:.1f}%",
        xy=(3, cred_8c_gbs),
        xytext=(2.42, 0.82),
        ha="center",
        fontsize=9.8,
        fontweight="bold",
        color="#084e6e",
        bbox=dict(boxstyle="round,pad=0.30", facecolor="#eef7fb", edgecolor="#0b6e99", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#0b6e99", lw=1.5),
    )
    ax1.set_title("(a) 1500 B 大包：主机内存写入膨胀", fontsize=12, fontweight="bold", pad=10)
    ax1.set_xticks(x_pos)
    ax1.set_xticklabels([f"{c} 核" for c in cores], fontsize=11)
    ax1.set_xlabel("主机接收核数（100G 满载均匀流）", fontsize=11)
    ax1.set_ylabel("主机内存写带宽 (GB/s)", fontsize=11)
    ax1.set_xlim(-0.2, 3.25)
    ax1.set_ylim(-0.25, 6.2)

    # ---- Panel (b): 1500 B 8-core Concurrent 32 MiB KV p99 tail latency (bars=median, dots=n=3 reps) ----
    kv_base_reps = [r["kv"]["p99_ns"] for r in kv_solo]
    kv_base = med(kv_base_reps)
    bar_labels = ["空载\n基线", "传统大环\n(privRing)", "共享锁环\n(ShRing)", "分离大环\n(rxBisect)", "本文\n信用环"]
    bar_colors = ["#95a5a6", "#c94c4c", "#d9822b", "#6c7a89", "#0b6e99"]
    bar_vals = [kv_base]
    rep_lists = [kv_base_reps]
    for key, _, _, _, _, _ in ARMS:
        rs = by_cfg[f"p1_{key}_c8_1500B_unif"]
        vals_r = [r["summary"]["kv_p99_ns"] for r in rs]
        bar_vals.append(med(vals_r))
        rep_lists.append(vals_r)

    bx = np.arange(len(bar_labels))
    bars = ax2.bar(bx, bar_vals, width=0.56, color=bar_colors, edgecolor="#333333", linewidth=0.8, zorder=3)
    ax2.axhline(kv_base, color="#666666", linestyle="--", linewidth=1.2, zorder=2)

    jitter = [-0.12, 0.0, 0.12]
    for i, reps in enumerate(rep_lists):
        xs_j = [bx[i] + j for j in jitter[:len(reps)]]
        ax2.scatter(xs_j, sorted(reps), s=28, facecolor="white", edgecolor="#111111", linewidth=1.2, zorder=6)

    for i, (b, v, reps) in enumerate(zip(bars, bar_vals, rep_lists)):
        hi_cnt = sum(1 for x in reps if x >= 50)
        if i == 1:
            txt = f"{v:.0f} ns\n({hi_cnt}/3 高档)"
            col = "#a82828"
            fw = "bold"
            y_pos = max(reps) + 4.0
        elif i == 4:
            txt = f"中值 {v:.0f} ns\n({hi_cnt}/3 偶发)"
            col = "#084e6e"
            fw = "bold"
            y_pos = max(reps) + 4.0
        else:
            txt = f"{v:.0f} ns"
            col = "#333333"
            fw = "normal"
            y_pos = max(reps) + 4.0
        ax2.text(
            b.get_x() + b.get_width() / 2.0, y_pos, txt,
            ha="center", va="bottom", fontsize=9.8, color=col, fontweight=fw,
        )

    ax2.text(
        0.02, 0.965, "空心圆点 = 单轮 (n=3)；L3 缺失 > 1.07% 跃入 DRAM",
        transform=ax2.transAxes, fontsize=8.2, color="#444444", va="top",
    )
    ax2.set_title("(b) 1500 B 8 核：并发 KV 尾延迟 (p99)", fontsize=12, fontweight="bold", pad=10)
    ax2.set_xticks(bx)
    ax2.set_xticklabels(bar_labels, fontsize=10)
    ax2.set_ylabel("同主机 32 MiB KV p99 延迟 (ns)", fontsize=11)
    ax2.set_ylim(0, 132)

    # ---- Panel (c): 256 B small-packet throughput scaling (1 -> 8 cores) ----
    for key, label, color, marker, ls, lw in ARMS:
        ys = []
        for nc in cores:
            rs = by_cfg[f"p1_{key}_c{nc}_256B_unif"]
            ys.append(med([r["summary"]["host_wire_gbps"] for r in rs]))
        z = 5 if key == "credit-sdp" else 3
        ax3.plot(
            x_pos, ys, color=color, marker=marker,
            linestyle=ls, linewidth=lw, markersize=7.5, zorder=z,
        )

    sh_4c = med([r["summary"]["host_wire_gbps"] for r in by_cfg["p1_sh-ring_c4_256B_unif"]])
    sh_8c = med([r["summary"]["host_wire_gbps"] for r in by_cfg["p1_sh-ring_c8_256B_unif"]])
    cred_4c_256 = med([r["summary"]["host_wire_gbps"] for r in by_cfg["p1_credit-sdp_c4_256B_unif"]])
    cred_8c_256 = med([r["summary"]["host_wire_gbps"] for r in by_cfg["p1_credit-sdp_c8_256B_unif"]])
    cred_8c_loss = med([r["summary"]["e2e_loss_pct"] for r in by_cfg["p1_credit-sdp_c8_256B_unif"]])
    priv_4c_256 = med([r["summary"]["host_wire_gbps"] for r in by_cfg["p1_priv-ring_c4_256B_unif"]])
    priv_8c_256 = med([r["summary"]["host_wire_gbps"] for r in by_cfg["p1_priv-ring_c8_256B_unif"]])
    priv_8c_loss = med([r["summary"]["e2e_loss_pct"] for r in by_cfg["p1_priv-ring_c8_256B_unif"]])

    ax3.axhline(100.0, color="#666666", linestyle="--", linewidth=1.1, zorder=2)
    ax3.text(-0.14, 103.5, "100 Gbps 线速", fontsize=9.2, color="#555555")

    ax3.annotate(
        f"本文 8 核 {cred_8c_256:.1f}G（FIFO 丢 {cred_8c_loss:.2f}%）\n追平大环 {priv_8c_256:.1f}G（丢 {priv_8c_loss:.2f}%）\n~45 Mpps 读墙 + 卡 L3 溢出",
        xy=(3, cred_8c_256),
        xytext=(-0.16, 78.5),
        fontsize=7.5,
        fontweight="bold",
        color="#084e6e",
        bbox=dict(boxstyle="round,pad=0.22", facecolor="#eef7fb", edgecolor="#0b6e99", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#0b6e99", lw=1.4, connectionstyle="arc3,rad=-0.05"),
    )
    ax3.annotate(
        f"静态共享锁（含小池缓存双锁放大）\n4 核 {sh_4c:.1f} → 8 核 {sh_8c:.1f} Gbps",
        xy=(3, sh_8c),
        xytext=(0.72, 14.0),
        fontsize=9.1,
        fontweight="bold",
        color="#a65c00",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#fff8ee", edgecolor="#d9822b", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#d9822b", lw=1.4),
    )

    ax3.set_title("(c) 256 B 小包：多核接收吞吐扩展", fontsize=12, fontweight="bold", pad=10)
    ax3.set_xticks(x_pos)
    ax3.set_xticklabels([f"{c} 核" for c in cores], fontsize=11)
    ax3.set_xlabel("主机接收核数（45.8 Mpps 均匀流）", fontsize=11)
    ax3.set_ylabel("主机实收吞吐 (Gbps)", fontsize=11)
    ax3.set_xlim(-0.2, 3.2)
    ax3.set_ylim(0, 118)

    handles, labels = ax1.get_legend_handles_labels()
    fig.suptitle(
        "均匀流量扩核（1 → 8 核）：本文信用环守住主机缓存，8 核小包追平大环（n=3 中值）",
        fontsize=13.8, fontweight="bold", y=0.99,
    )
    fig.legend(
        handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.925),
        ncol=4, frameon=False, fontsize=11, handlelength=2.4, columnspacing=1.8,
    )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.86])

    png = mrvplot.HERE / "mrv-210-uniform-scaling.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-210-uniform-scaling.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
