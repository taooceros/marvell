"""MRV-196 Figure 3: Clean 2-panel rate ladder, multi-core backpressure cliff,
and on-chip queue depth causal curve (no raw PMU register names, no unverified
capacity line).

Reads raw verified n=3 runs directly from `logs/mrv196-2026-11-25/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv196-2026-11-25/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def load_verified():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    by_lbl = defaultdict(list)
    for r in rows:
        assert r["accepted"] is True
        assert r["host_pmu_min_pct"] == 100.0
        assert r["page_faults"] == 0
        assert r["victim_page_faults"] == 0
        assert r["aggr_page_faults"] == 0
        assert r["ram_verify_errors"] == 0
        if r["chase"]:
            assert r["chase"]["bad_nodes"] == 0
        if r["stream"]:
            assert r["stream"]["verify_errors"] == 0
        if r["aggr"] == "bar4":
            assert r["card_pmu"]["min_pct"] == 100.0
            cv = r["card_verify"]
            assert cv["zero_lines"] == 0 and cv["torn_lines"] == 0 and cv["bad_lines"] == 0
        by_lbl[r["label"]].append(r)
    return by_lbl


def main():
    by_lbl = load_verified()

    stages = [
        ("idle", "空载\n0M"),
        ("bar4_1c_50m_b512", "1核\n50M"),
        ("bar4_1c_100m_b512", "1核\n100M"),
        ("bar4_1c_160m_b512", "1核\n160M"),
        ("bar4_1c_max_b512", "1核满速\n175M"),
        ("bar4_2c_max_b512", "2核\n无控"),
        ("bar4_4c_max_b512", "4核\n无控"),
        ("bar4_6c_max_b512", "6核\n无控"),
        ("bar4_8c_max_b512", "8核\n无控"),
        ("bar4_8c_165m_b128", "8核限速\nb=128"),
    ]
    x = np.arange(len(stages))
    xlabels = [s[1] for s in stages]

    bw_m, bw_lo, bw_hi = [], [], []
    cha_m, rxc_m, irp_m = [], [], []
    for aggr, _ in stages:
        t6 = [r["stream"]["mean_gb_s"] for r in by_lbl[f"t6c__{aggr}"]]
        m = med(t6)
        bw_m.append(m)
        bw_lo.append(max(0.0, m - min(t6)))
        bw_hi.append(max(0.0, max(t6) - m))

        llc_it = by_lbl[f"llc__{aggr}"]
        cha_m.append(med([r["cha"]["occ_entries"] for r in llc_it]))
        rxc_m.append(med([r["m2p1"]["ne_cha_ncb_duty_pct"] for r in llc_it]))
        irp_m.append(med([r["irp1"]["occ_entries"] for r in llc_it]))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.6, 5.4))

    for ax in (ax1, ax2):
        ax.axvspan(-0.45, 4.45, color="#dafbe1", alpha=0.35, zorder=0)
        ax.axvspan(4.55, 8.45, color="#ffebe9", alpha=0.35, zorder=0)
        ax.axvspan(8.55, 9.45, color="#ddf4ff", alpha=0.40, zorder=0)
        ax.axvline(4.5, color="#94a3b8", linestyle="--", lw=1.0, zorder=1)
        ax.axvline(8.5, color="#94a3b8", linestyle="--", lw=1.0, zorder=1)

    # ---- Left Panel: Neighbor 6-core memory bandwidth (plain linear GB/s) ----
    ax1.errorbar(
        x, bw_m, yerr=[bw_lo, bw_hi], color="#0f172a", marker="o", lw=2.4,
        markersize=7.0, capsize=3.0, zorder=4,
    )
    # Color key regime markers cleanly
    ax1.scatter(x[:5], bw_m[:5], color="#1a7f37", s=58, zorder=5)
    ax1.scatter(x[5:9], bw_m[5:9], color="#cf222e", s=58, zorder=5)
    ax1.scatter(x[9:], bw_m[9:], color="#0969da", s=64, zorder=5)

    for idx_show in [0, 4, 5, 6, 7, 8, 9]:
        col = "#116329" if idx_show <= 4 else ("#a40e26" if idx_show <= 8 else "#0550ae")
        dy = 2.0 if idx_show not in (6, 7, 8) else -4.0
        va = "bottom" if dy > 0 else "top"
        ax1.text(idx_show, bw_m[idx_show] + dy, f"{bw_m[idx_show]:.1f}",
                 ha="center", va=va, fontsize=9.0, fontweight="bold", color=col)

    ax1.text(2.0, 56.5, "单核推到顶：一条平线", ha="center", va="center",
             fontsize=9.2, fontweight="bold", color="#116329")
    ax1.text(6.5, 56.5, "多核无控：掉崖", ha="center", va="center",
             fontsize=9.2, fontweight="bold", color="#a40e26")
    ax1.text(9.0, 56.5, "恢复", ha="center", va="center",
             fontsize=9.2, fontweight="bold", color="#0550ae")

    ax1.set_ylim(0, 61.5)
    ax1.set_xticks(x)
    ax1.set_xticklabels(xlabels, fontsize=8.6)
    ax1.set_ylabel("邻居 6 核内存带宽 (GB/s)", fontsize=10.0)
    ax1.set_title("A. 现象：单核 0 → 175 Mpps 不掉速，多核无控后暴跌", fontsize=11.0, fontweight="bold", pad=10)

    # ---- Right Panel: On-chip CHA queue depth + M2PCIe1 inbound buffer non-empty % ----
    bar_cols = ["#94a3b8"] * 5 + ["#f59e0b"] + ["#dc2626"] * 3 + ["#0969da"]
    ax2.bar(x, cha_m, width=0.56, color=bar_cols, alpha=0.85, label="片上中转队列排队条数（32 颗 CHA 合计）", zorder=3)
    for idx_show in [3, 4, 5, 6, 7, 8, 9]:
        dy_bar = 6 if idx_show in (3, 4) else 15
        ax2.text(idx_show, cha_m[idx_show] + dy_bar, f"{cha_m[idx_show]:.0f}",
                 ha="center", va="bottom", fontsize=8.6, fontweight="bold", color="#1e293b")

    ax2.set_ylim(0, 910)
    ax2.set_xticks(x)
    ax2.set_xticklabels(xlabels, fontsize=8.6)
    ax2.set_ylabel("片上中转队列排队条数（槽位）", fontsize=10.0)
    ax2.set_title("B. 原因：IRP1 先顶满 → 缓冲积压 → 倒灌片上队列", fontsize=11.0, fontweight="bold", pad=10)

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.spines["right"].set_visible(True)
    ax2_r.plot(x, rxc_m, color="#0f172a", marker="s", lw=2.0, markersize=5.2, linestyle="--",
               label="M2PCIe1 入向缓冲非空占比 (%)", zorder=5)
    ax2_r.set_ylim(0, 112)
    ax2_r.set_yticks([0, 25, 50, 75, 100])
    ax2_r.set_ylabel("M2PCIe1 入向缓冲非空占比 (%)", fontsize=9.8, color="#0f172a")
    ax2_r.grid(False)

    ax2_r.annotate(
        f"拐点：IRP1 先顶满\n({irp_m[4]:.1f} → {irp_m[5]:.1f} / ~8 槽)\n缓冲非空 {rxc_m[4]:.1f}% → {rxc_m[5]:.1f}%",
        xy=(5, rxc_m[5]), xytext=(2.15, 66), ha="center", va="center",
        fontsize=8.6, fontweight="bold", color="#991b1b",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="white", edgecolor="#dc2626", alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.3, connectionstyle="arc3,rad=-0.12"),
    )

    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2_r.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8.3, framealpha=0.95)

    fig.suptitle(
        "MRV-196 速率阶梯与片上排队因果：不超链路不排队，超发即堵满（n = 3 交错中位数）",
        fontsize=12.6, fontweight="bold", y=0.98,
    )
    fig.subplots_adjust(top=0.85, bottom=0.14, left=0.06, right=0.93, wspace=0.22)
    png = mrvplot.save(fig, "mrv-196-rate-and-queue")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
