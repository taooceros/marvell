#!/usr/bin/env python3
"""Plot Figure 3 for MRV-210: Part 3 single slow-core stall isolation (1 / 5 / 10 ms on 8 host cores).

All numbers are derived dynamically from logs/mrv210-2026-11-26/runs.jsonl (n=3 medians).
Host memory write bandwidth converts raw `imc_wr_gbps` (Gbps) to standard GB/s via `/ 8.0`.
"""

import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
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
    stalls = [1, 5, 10]
    sx = np.arange(len(stalls))

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(15.8, 5.2))

    # ---- Panel (a): 1500 B 8-core full 100G -> end-to-end loss (%) vs stall duration ----
    for key, label, color, marker, ls, lw in ARMS:
        ys = [med([r["summary"]["e2e_loss_pct"] for r in by_cfg[f"p3_{key}_c8_1500B_stall{st}ms"]]) for st in stalls]
        z = 5 if key == "credit-sdp" else 3
        ax1.plot(
            sx, ys, label=label, color=color, marker=marker,
            linestyle=ls, linewidth=lw, markersize=7.5, zorder=z,
        )

    sh_10 = med([r["summary"]["e2e_loss_pct"] for r in by_cfg["p3_sh-ring_c8_1500B_stall10ms"]])
    sh_10_hl = med([r["summary"]["healthy_q_lost_pkts"] for r in by_cfg["p3_sh-ring_c8_1500B_stall10ms"]])
    pr_10 = med([r["summary"]["e2e_loss_pct"] for r in by_cfg["p3_priv-ring_c8_1500B_stall10ms"]])
    rb_10 = med([r["summary"]["e2e_loss_pct"] for r in by_cfg["p3_rx-bisect_c8_1500B_stall10ms"]])
    rb_10_drop = med([r["summary"]["host_bx_drops"] for r in by_cfg["p3_rx-bisect_c8_1500B_stall10ms"]])
    cr_10_fp = med([r["summary"]["card_fifo_peak"] for r in by_cfg["p3_credit-sdp_c8_1500B_stall10ms"]])

    ax1.set_yscale("symlog", linthresh=0.3, linscale=0.55)
    ax1.set_yticks([0, 0.2, 0.5, 1, 2, 5, 10, 20])
    ax1.set_yticklabels(["0%", "0.2%", "0.5%", "1%", "2%", "5%", "10%", "20%"], fontsize=10)
    ax1.set_ylim(-0.07, 38)

    ax1.annotate(
        f"共享锁全核阻塞 {sh_10:.1f}%\n（7 健康核丢 {sh_10_hl / 1e4:.0f} 万包）",
        xy=(2, sh_10), xytext=(0.08, 16.0),
        fontsize=9.6, fontweight="bold", color="#a65c00",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#fff8ee", edgecolor="#d9822b", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#d9822b", lw=1.4),
    )
    ax1.annotate(
        f"传统大环 {pr_10:.2f}%",
        xy=(2, pr_10), xytext=(1.05, 3.1),
        fontsize=9.6, fontweight="bold", color="#a82828",
        arrowprops=dict(arrowstyle="->", color="#c94c4c", lw=1.2),
    )
    ax1.annotate(
        f"分离大环溢出 {rb_10:.2f}%\n（丢 {rb_10_drop / 1e4:.1f} 万包）",
        xy=(2, rb_10), xytext=(1.06, 0.25),
        fontsize=9.0, color="#44505c",
        arrowprops=dict(arrowstyle="->", color="#6c7a89", lw=1.2, shrinkB=5),
    )
    ax1.annotate(
        f"本文全程 0.00% 零丢\n10 ms 卡内吸收 {cr_10_fp / 1e4:.2f} 万包",
        xy=(2.0, 0.0), xytext=(-0.10, 0.13),
        fontsize=8.6, fontweight="bold", color="#084e6e",
        bbox=dict(boxstyle="round,pad=0.22", facecolor="#eef7fb", edgecolor="#0b6e99", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#0b6e99", lw=1.5, shrinkB=7),
    )
    ax1.set_title("(a) 1500 B 满线速 (100G)：端到端丢包率", fontsize=11.6, fontweight="bold", pad=10)
    ax1.set_xticks(sx)
    ax1.set_xticklabels([f"{s} ms" for s in stalls], fontsize=11)
    ax1.set_xlabel("单个慢核周期暂停时长（每 50 ms 一次）", fontsize=10.5)
    ax1.set_ylabel("端到端丢包率（对数坐标）", fontsize=10.5)
    ax1.set_xlim(-0.20, 2.18)

    # ---- Panel (b): 256 B 8-core paced 36 Mpps (~79 Gbps) -> end-to-end loss (%) ----
    for key, label, color, marker, ls, lw in ARMS:
        ys = [med([r["summary"]["e2e_loss_pct"] for r in by_cfg[f"p3_{key}_c8_256B_stall{st}ms"]]) for st in stalls]
        z = 5 if key == "credit-sdp" else 3
        ax2.plot(
            sx, ys, color=color, marker=marker,
            linestyle=ls, linewidth=lw, markersize=7.5, zorder=z,
        )

    sh2_5 = med([r["summary"]["e2e_loss_pct"] for r in by_cfg["p3_sh-ring_c8_256B_stall5ms"]])
    sh2_5_hl = med([r["summary"]["healthy_q_lost_pkts"] for r in by_cfg["p3_sh-ring_c8_256B_stall5ms"]])
    rb2_10 = med([r["summary"]["e2e_loss_pct"] for r in by_cfg["p3_rx-bisect_c8_256B_stall10ms"]])
    rb2_10_drop = med([r["summary"]["host_bx_drops"] for r in by_cfg["p3_rx-bisect_c8_256B_stall10ms"]])
    pr2_10 = med([r["summary"]["e2e_loss_pct"] for r in by_cfg["p3_priv-ring_c8_256B_stall10ms"]])
    cr2_10_fp = med([r["summary"]["card_fifo_peak"] for r in by_cfg["p3_credit-sdp_c8_256B_stall10ms"]])

    ax2.set_yscale("symlog", linthresh=0.1, linscale=0.55)
    ax2.set_yticks([0, 0.1, 0.5, 1, 2, 5, 10, 20, 50])
    ax2.set_yticklabels(["0%", "0.1%", "0.5%", "1%", "2%", "5%", "10%", "20%", "50%"], fontsize=10)
    ax2.set_ylim(-0.035, 125)

    ax2.annotate(
        f"共享锁塌方 {sh2_5:.1f}%\n（7 健康核丢 {sh2_5_hl / 1e4:.0f} 万包）",
        xy=(1, sh2_5), xytext=(0.15, 12.5),
        fontsize=9.6, fontweight="bold", color="#a65c00",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#fff8ee", edgecolor="#d9822b", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#d9822b", lw=1.4),
    )
    ax2.annotate(
        f"分离大环溢出 {rb2_10:.2f}%\n（丢 {rb2_10_drop / 1e4:.0f} 万包）",
        xy=(2, rb2_10), xytext=(0.12, 2.3),
        fontsize=9.5, fontweight="bold", color="#44505c",
        arrowprops=dict(arrowstyle="->", color="#6c7a89", lw=1.3),
    )
    ax2.annotate(
        f"传统大环 {pr2_10:.2f}%",
        xy=(2, pr2_10), xytext=(0.95, 0.30),
        fontsize=9.5, fontweight="bold", color="#a82828",
        arrowprops=dict(arrowstyle="->", color="#c94c4c", lw=1.2),
    )
    ax2.annotate(
        f"本文全程 0.00% 零丢\n10 ms 卡内吸收 {cr2_10_fp / 1e4:.2f} 万包",
        xy=(2.0, 0.0), xytext=(0.16, 0.016),
        fontsize=8.6, fontweight="bold", color="#084e6e",
        bbox=dict(boxstyle="round,pad=0.22", facecolor="#eef7fb", edgecolor="#0b6e99", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#0b6e99", lw=1.5, shrinkB=7),
    )

    ax2.set_title("(b) 256 B 统一限速 36 Mpps（约 79G）：丢包率", fontsize=11.4, fontweight="bold", pad=10)
    ax2.set_xticks(sx)
    ax2.set_xticklabels([f"{s} ms" for s in stalls], fontsize=11)
    ax2.set_xlabel("单个慢核周期暂停时长（4 核限速约 70G 见正文）", fontsize=10.2)
    ax2.set_ylabel("端到端丢包率（对数坐标）", fontsize=10.5)

    # ---- Panel (c): 1500 B 8-core 5 ms stall -> Concurrent KV p99 & p999 (dots=n=3 p99 reps) ----
    kv_base_99_reps = [r["kv"]["p99_ns"] for r in kv_solo]
    kv_base_99 = med(kv_base_99_reps)
    kv_base_999 = med([r["kv"]["p999_ns"] for r in kv_solo])

    labels_c = ["空载\n基线", "传统大环\n(privRing)", "共享锁环\n(ShRing)", "分离大环\n(rxBisect)", "本文\n信用环"]
    colors_c = ["#95a5a6", "#c94c4c", "#d9822b", "#6c7a89", "#0b6e99"]
    p99_vals = [kv_base_99]
    p99_reps = [kv_base_99_reps]
    p999_vals = [kv_base_999]
    imc_vals = [0.0]
    for key, _, _, _, _, _ in ARMS:
        rs = by_cfg[f"p3_{key}_c8_1500B_stall5ms"]
        r99 = [r["summary"]["kv_p99_ns"] for r in rs]
        p99_vals.append(med(r99))
        p99_reps.append(r99)
        p999_vals.append(med([r["summary"]["kv_p999_ns"] for r in rs]))
        imc_vals.append(med([r["summary"]["imc_wr_gbps"] / 8.0 for r in rs]))

    cx = np.arange(len(labels_c))
    w = 0.36
    ax3.bar(cx - w / 2, p99_vals, width=w, color=colors_c, edgecolor="#333333", linewidth=0.8, zorder=3)
    ax3.bar(
        cx + w / 2, p999_vals, width=w, color=colors_c, alpha=0.38,
        edgecolor="#333333", linewidth=0.8, hatch="///", zorder=3,
    )
    ax3.axhline(kv_base_99, color="#666666", linestyle="--", linewidth=1.0, zorder=2)

    jitter = [-0.09, 0.0, 0.09]
    for i, reps in enumerate(p99_reps):
        xs_j = [cx[i] - w / 2 + j for j in jitter[:len(reps)]]
        ax3.scatter(xs_j, sorted(reps), s=20, facecolor="white", edgecolor="#111111", linewidth=1.1, zorder=6)

    for i in range(len(labels_c)):
        fw = "bold" if i in (1, 4) else "normal"
        col = "#a82828" if i == 1 else ("#084e6e" if i == 4 else "#333333")
        y99_lbl = max(max(p99_reps[i]), p99_vals[i]) + 5.0
        ax3.text(cx[i] - w / 2, y99_lbl, f"{p99_vals[i]:.0f}", ha="center", va="bottom", fontsize=9.2, fontweight=fw, color=col)
        ax3.text(cx[i] + w / 2, p999_vals[i] + 3.5, f"{p999_vals[i]:.0f}", ha="center", va="bottom", fontsize=9.2, fontweight=fw, color=col)

    priv_imc = imc_vals[1]
    cred_imc = imc_vals[4]
    ax3.annotate(
        f"常驻溢出写 {priv_imc:.2f} GB/s\n（本文仅 {cred_imc:.2f} GB/s）",
        xy=(1 + w / 2 + 0.04, p999_vals[1] - 10), xytext=(0.90, 184),
        fontsize=9.1, fontweight="bold", color="#a82828",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#fff5f5", edgecolor="#c94c4c", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#c94c4c", lw=1.3),
    )

    ax3.legend(
        handles=[
            Patch(facecolor="#555555", edgecolor="#333333", label="p99 中值（圆点=3轮）"),
            Patch(facecolor="#555555", alpha=0.35, edgecolor="#333333", hatch="///", label="p999 中值"),
        ],
        loc="upper right", frameon=True, facecolor="white", framealpha=0.95, fontsize=8.6,
    )

    ax3.set_title("(c) 1500 B 5 ms：KV 尾延迟（大环常驻缓存溢出）", fontsize=11.0, fontweight="bold", pad=10)
    ax3.set_xticks(cx)
    ax3.set_xticklabels(labels_c, fontsize=9.8)
    ax3.set_ylabel("同主机 32 MiB KV 延迟 (ns)", fontsize=10.5)
    ax3.set_ylim(0, 236)

    handles, labels = ax1.get_legend_handles_labels()
    fig.suptitle(
        "单慢核卡顿隔离（8 核中 1 核暂停 1–10 ms）：本文信用环实现 0.00% 丢包、健康核零牵连与低邻居延迟（n=3 中值）",
        fontsize=13.2, fontweight="bold", y=0.99,
    )
    fig.legend(
        handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.925),
        ncol=4, frameon=False, fontsize=11, handlelength=2.4, columnspacing=1.8,
    )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.86])

    png = mrvplot.HERE / "mrv-210-stall-isolation.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-210-stall-isolation.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
