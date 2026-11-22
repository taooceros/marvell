"""MRV-125 Figure 1: Core single-variable progression + diagnostic controls
(p1_wc1_shaped_bts vs p2a_wc1_unsh_pkt) plotted directly from logs/mrv125-2026-11-22/runs.jsonl.
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv125-2026-11-22/runs.jsonl"

TIERS = [
    ("64B_seq_4m", "64B seq\n4 MiB\n(热基线)"),
    ("64B_rand_4m", "64B rand\n4 MiB\n(乱序热)"),
    ("2KiB_rand_128m", "2KiB rand\n128 MiB\n(核心目标)"),
    ("2112B_rand_132m", "2112B rand\n132 MiB\n(反别名)"),
    ("64B_rand_128m", "64B rand\n128 MiB\n(真容量)"),
    ("2KiB_seq_128m", "2KiB seq\n128 MiB\n(顺序步长)"),
    ("2KiB_cold_128m", "2KiB cold\n128 MiB\n(只读冷源)"),
]


def load_rows():
    rows = [json.loads(line) for line in RUNS.read_text().splitlines() if line.strip()]
    for r in rows:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
        assert all(c["corrupt"] == 0 for c in r["card_pmu"]["cores"])
        assert r["host_pmu"]["page_faults"] == 0
    return rows


def card_batch(r):
    cores = r["card_pmu"]["cores"]
    return sum(c["taken"] for c in cores) / max(1, sum(c["busy_polls"] for c in cores))


def tad_snp(r):
    return r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / r["measured"]


def main():
    rows = load_rows()
    by_pc = {}
    for r in rows:
        by_pc.setdefault((r["part"], r["cfg"]), []).append(r)

    cl_rows = [r for r in rows if r["workload"] == "closed_loop"]
    min_ddio = min(r["host_pmu"]["ddio_hit_pct"] for r in cl_rows)

    x = np.arange(len(TIERS))
    w = 0.36

    p1_mops_med, p2a_mops_med = [], []
    p1_mops_all, p2a_mops_all = [], []
    p1_cbat_med, p2a_cbat_med = [], []
    p1_cbat_all, p2a_cbat_all = [], []
    p1_snp_med, p2a_snp_med = [], []

    for cfg, _ in TIERS:
        r1 = by_pc[("p1_wc1_shaped_bts", cfg)]
        r2 = by_pc[("p2a_wc1_unsh_pkt", cfg)]
        p1_mops_all.append([r["mops"] for r in r1])
        p2a_mops_all.append([r["mops"] for r in r2])
        p1_mops_med.append(statistics.median(p1_mops_all[-1]))
        p2a_mops_med.append(statistics.median(p2a_mops_all[-1]))

        p1_cbat_all.append([card_batch(r) for r in r1])
        p2a_cbat_all.append([card_batch(r) for r in r2])
        p1_cbat_med.append(statistics.median(p1_cbat_all[-1]))
        p2a_cbat_med.append(statistics.median(p2a_cbat_all[-1]))

        p1_snp_med.append(statistics.median([tad_snp(r) for r in r1]))
        p2a_snp_med.append(statistics.median([tad_snp(r) for r in r2]))

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11.4, 8.1), sharex=True, gridspec_kw={"height_ratios": [1.18, 1.0]}
    )

    c_p1 = "#0f766e"
    c_p2a = "#94a3b8"
    c_snp1 = "#0d9488"
    c_snp2 = "#dc2626"

    for ax in (ax1, ax2):
        ax.axvspan(-0.55, 2.5, color="#f0fdfa", alpha=0.45, zorder=0)
        ax.axvline(2.5, color="#cbd5e1", linestyle="--", linewidth=1.2, zorder=1)

    # Panel 1: Throughput
    b1 = ax1.bar(
        x - w / 2, p1_mops_med, width=w, color=c_p1, edgecolor="#115e59", linewidth=0.8,
        label="整形快态 p1（WC1 W=128, T_pause=1 µs, 批 TX 戳）", zorder=3
    )
    b2 = ax1.bar(
        x + w / 2, p2a_mops_med, width=w, color=c_p2a, edgecolor="#475569", linewidth=0.8,
        label="无整形基线 p2a（WC1 W=64, T_pause=0, 逐包戳）", zorder=3
    )

    for i in range(len(TIERS)):
        ax1.scatter([x[i] - w / 2] * len(p1_mops_all[i]), p1_mops_all[i],
                    color="#042f2e", s=16, zorder=5, alpha=0.85)
        ax1.scatter([x[i] + w / 2] * len(p2a_mops_all[i]), p2a_mops_all[i],
                    color="#1e293b", s=16, zorder=5, alpha=0.85)

    for rect, val in zip(b1, p1_mops_med):
        ax1.text(rect.get_x() + rect.get_width() / 2, val + 1.1, f"{val:.1f}",
                 ha="center", va="bottom", fontsize=9.5, fontweight="bold", color="#0f766e")
    for rect, val in zip(b2, p2a_mops_med):
        ax1.text(rect.get_x() + rect.get_width() / 2, val + 1.1, f"{val:.1f}",
                 ha="center", va="bottom", fontsize=9, color="#475569")

    ax1.set_ylim(0, 64)
    ax1.set_ylabel("闭环吞吐（Mmsg/s，64 B 报文）", fontsize=10.5)
    ax1.set_title(
        "MRV-125：整形快态 vs 无整形基线的全 7 档闭环吞吐与卡侧慢态 PMU 证据\n"
        "基线：cpu40 3.90 GHz / uncore 2.20 GHz / CN106 2.00 GHz / MOVDIR64B→BAR4 + WC→DDIO / MAX_DRAIN=32 / 2 MiB 大页 / n=3 交错",
        fontsize=10.5, pad=28
    )
    ax1.text(0.95, 59.5, "核心三档单变量对照（N=65,536）", ha="center", va="center",
             fontsize=9.5, fontweight="bold", color="#0f766e")
    ax1.text(4.5, 59.5, "机理剥离对照（反别名 / 真容量 / 顺序 / 只读）", ha="center", va="center",
             fontsize=9.5, color="#475569")

    p1_min_ddio = min(r["host_pmu"]["ddio_hit_pct"] for r in cl_rows if r["part"] == "p1_wc1_shaped_bts")
    ax1.text(
        6.45, 53.5,
        f"全 144 轮闭环 RX DDIO ≥ {min_ddio:.3f}%\n(p1 最低单轮 {p1_min_ddio:.3f}%, 零慢态坍缩)",
        ha="right", va="top", fontsize=9, color="#0f766e",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#ffffff", edgecolor="#0f766e", alpha=0.95)
    )
    ax1.legend(loc="lower center", bbox_to_anchor=(0.5, 1.005), ncol=2, frameon=False, fontsize=9.2)

    # Panel 2: Card busy_batch (bars) + tad_snp/msg (right axis)
    ax2.bar(
        x - w / 2, p1_cbat_med, width=w, color="#5eead4", edgecolor="#0f766e", linewidth=0.8,
        label="卡侧批大小 busy_batch（p1 整形，满批=32）", zorder=3
    )
    ax2.bar(
        x + w / 2, p2a_cbat_med, width=w, color="#cbd5e1", edgecolor="#64748b", linewidth=0.8,
        label="卡侧批大小 busy_batch（p2a 无整形）", zorder=3
    )
    for i in range(len(TIERS)):
        ax2.scatter([x[i] - w / 2] * len(p1_cbat_all[i]), p1_cbat_all[i], color="#0f766e", s=14, zorder=5)
        ax2.scatter([x[i] + w / 2] * len(p2a_cbat_all[i]), p2a_cbat_all[i], color="#334155", s=14, zorder=5)
        if i in (2, 6):
            ax2.text(x[i] - w / 2, p1_cbat_med[i] - 3.2, f"{p1_cbat_med[i]:.1f}",
                     ha="center", va="center", fontsize=8.8, fontweight="bold", color="#042f2e", zorder=7)
        else:
            ax2.text(x[i] - w / 2, p1_cbat_med[i] + 1.0, f"{p1_cbat_med[i]:.1f}",
                     ha="center", va="bottom", fontsize=8.8, fontweight="bold", color="#0f766e", zorder=7)
        if i == 0:
            ax2.text(x[i] + w / 2, p2a_cbat_med[i] - 2.6, f"{p2a_cbat_med[i]:.1f}",
                     ha="center", va="center", fontsize=8.5, fontweight="bold", color="#1e293b", zorder=7)
        else:
            ax2.text(x[i] + w / 2, p2a_cbat_med[i] + 1.0, f"{p2a_cbat_med[i]:.1f}",
                     ha="center", va="bottom", fontsize=8.6, color="#475569", zorder=7,
                     bbox=dict(boxstyle="round,pad=0.10", facecolor="white", edgecolor="none", alpha=0.82))

    ax2.axhline(3.0, color="#dc2626", linestyle=":", linewidth=1.1, zorder=2)
    ax2.text(6.48, 6.4, "慢态区 (cbat ≈ 2)", ha="right", va="bottom", fontsize=8.5, color="#dc2626",
             bbox=dict(boxstyle="round,pad=0.18", facecolor="white", edgecolor="none", alpha=0.88))
    ax2.set_ylim(0, 40)
    ax2.set_ylabel("卡侧批大小 busy_batch（条/忙轮询）", fontsize=10)

    ax2r = ax2.twinx()
    ax2r.spines["top"].set_visible(False)
    ax2r.grid(False)
    ax2r.plot(x - w / 2, p1_snp_med, color=c_snp1, marker="o", linewidth=1.8, markersize=6,
              label="卡 TAD 探听 tad_snp/msg（p1，≤0.94）", zorder=6)
    ax2r.plot(x + w / 2, p2a_snp_med, color=c_snp2, marker="s", linestyle="--", linewidth=1.8, markersize=5.5,
              label="卡 TAD 探听 tad_snp/msg（p2a，最高 2.42）", zorder=6)
    ax2r.set_ylim(0, 2.75)
    ax2r.set_ylabel("卡 TAD 探听 tad_snp / msg", fontsize=10, color="#334155")

    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2r.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="lower center", bbox_to_anchor=(0.5, 1.01),
               ncol=2, frameon=False, fontsize=8.7)

    ax2.set_xticks(x)
    ax2.set_xticklabels([lbl for _, lbl in TIERS], fontsize=9.5)
    ax2.set_xlim(-0.55, len(TIERS) - 0.45)

    png = save(fig, "mrv-125-core-tiers")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
