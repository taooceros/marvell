#!/usr/bin/env python3
"""MRV-89 Exp 2 & Exp 3 chart: Address pattern (Exp 2) and multi-core scaling (Exp 3).

Reads raw verified runs from logs/mrv89-2026-11-19/runs.jsonl and writes
docs/figures/mrv-89-exp23-pattern-cores.{png,svg} using mrvplot styling.
"""
import collections
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mrvplot  # noqa: E402, F401
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

RUNS = ROOT / "logs/mrv89-2026-11-19/runs.jsonl"
PCIE_WIRE_MOPS = 179.02


def main():
    rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
    exp2 = collections.defaultdict(list)
    exp3 = collections.defaultdict(list)
    for r in rows:
        if r.get("exp") == "exp2_pattern":
            assert r["card"]["mismatches"] == 0 and r["card"]["torn_cells"] == 0
            assert r["host_min_pct"] >= 99.9 and r["card"]["min_pct"] >= 99.9
            exp2[r["cond"]].append(r)
        elif r.get("exp") == "exp3_cores":
            assert r["card"]["mismatches"] == 0 and r["card"]["torn_cells"] == 0
            assert r["host_min_pct"] >= 99.9 and r["card"]["min_pct"] >= 99.9
            exp3[r["cond"]].append(r)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.1))

    # --- Left panel: Exp 2 (Address pattern) ---
    p_order = ["seq64", "stride4k", "same64"]
    p_labels = ["顺序不同 64B 行\n(2 MiB · 32768 行)", "跨 4 KiB 步长\n(2 MiB · 512 行)", "同一 64B 地址\n(单行重复写)"]
    p_colors = ["#2563eb", "#3b82f6", "#94a3b8"]

    x1 = np.arange(len(p_order))
    p_mops, p_gbps, p_cha, p_chas, p_tad_occ = [], [], [], [], []
    for k in p_order:
        rs = exp2[k]
        p_mops.append(statistics.median(r["mops"] for r in rs))
        p_gbps.append(statistics.median(r["gbps"] for r in rs))
        p_cha.append(statistics.median(r["cha"]["inflight"] for r in rs))
        p_chas.append(int(statistics.median(r["cha"]["active_chas"] for r in rs)))
        p_tad_occ.append(statistics.median(
            r["card"]["tad"]["tad_req_occ"]["val"] / r["card"]["tad"]["tad_req_msh_in_any"]["val"]
            for r in rs
        ))

    ax1.axhline(PCIE_WIRE_MOPS, color="#64748b", ls="--", lw=1.3,
                label=f"Gen5 ×4 64B 线速上限 ({PCIE_WIRE_MOPS:.1f} M/s)")
    bars1 = ax1.bar(x1, p_mops, width=0.58, color=p_colors, edgecolor="#1e293b", linewidth=0.7, zorder=3)
    for i, k in enumerate(p_order):
        vals = [r["mops"] for r in exp2[k]]
        ax1.scatter([x1[i]] * len(vals), vals, color="#0f172a", s=18, zorder=5)

    for i, b in enumerate(bars1):
        h = b.get_height()
        ax1.text(
            b.get_x() + b.get_width() / 2,
            h + 5,
            f"{p_mops[i]:.2f} M/s\n({p_gbps[i]:.2f} GB/s)",
            ha="center", va="bottom", fontsize=8.4, fontweight="bold",
            color="#1e3a8a" if i < 2 else "#334155",
        )
        if i < 2:
            ax1.text(
                b.get_x() + b.get_width() / 2,
                h * 0.46,
                f"{p_chas[i]} CHA 活跃\nCHA 在途 {p_cha[i]:.0f} 条\nTAD occ={p_tad_occ[i]:.2f}",
                ha="center", va="center", fontsize=8.0, color="white", fontweight="bold",
            )
        else:
            ax1.text(
                b.get_x() + b.get_width() / 2,
                h + 36,
                f"仅 {p_chas[i]} 个 CHA 活跃\nCHA 在途 {p_cha[i]:.2f} 条\n停留 108.8 ns\n(慢 19.7×)",
                ha="center", va="bottom", fontsize=8.0, color="#475569",
                bbox=dict(boxstyle="round,pad=0.3", fc="#f8fafc", ec="#cbd5e1", lw=0.8),
            )

    ax1.set_xticks(x1)
    ax1.set_xticklabels(p_labels, fontsize=8.5)
    ax1.set_ylabel("直写速率 (M ops/s)", fontsize=9.5)
    ax1.set_ylim(0, 224)
    ax1.set_title("(a) 实验 2：目标地址分布（单核 cpu40, 无 SFENCE）", fontsize=10.5, fontweight="bold")
    ax1.legend(loc="upper right", fontsize=8, frameon=True, facecolor="white", framealpha=0.95)

    # --- Right panel: Exp 3 (Multi-core concurrency) ---
    c_order = ["1c", "2c_smt", "2c_phys", "4c_phys"]
    c_labels = [
        "1 核\n(cpu40)\n3.90 GHz",
        "2 核 SMT\n(40,104)\n3.90 GHz",
        "2 核物理\n(40,41)\n3.90 GHz",
        "4 核物理\n(40–43)\n3.89 GHz",
    ]
    x2 = np.arange(len(c_order))
    c_mops, c_gbps, c_cha, c_irp = [], [], [], []
    for k in c_order:
        rs = exp3[k]
        c_mops.append(statistics.median(r["mops"] for r in rs))
        c_gbps.append(statistics.median(r["gbps"] for r in rs))
        c_cha.append(statistics.median(r["cha"]["inflight"] for r in rs))
        c_irp.append(statistics.median(r["irp1"]["inflight"] for r in rs))

    ax2.axhline(PCIE_WIRE_MOPS, color="#64748b", ls="--", lw=1.3,
                label=f"Gen5 ×4 64B 线速上限 ({PCIE_WIRE_MOPS:.1f} M/s)")
    bars2 = ax2.bar(x2, c_mops, width=0.54, color="#2563eb", alpha=0.85,
                    edgecolor="#1e293b", linewidth=0.7, zorder=3, label="总直写吞吐 (M ops/s, 左轴)")
    for i, k in enumerate(c_order):
        vals = [r["mops"] for r in exp3[k]]
        ax2.scatter([x2[i]] * len(vals), vals, color="#0f172a", s=18, zorder=5)

    for i, b in enumerate(bars2):
        h = b.get_height()
        ax2.text(
            b.get_x() + b.get_width() / 2,
            78,
            f"{c_mops[i]:.2f} M/s\n{c_gbps[i]:.2f} GB/s\nIRP={c_irp[i]:.2f}",
            ha="center", va="center", fontsize=8.0, color="white", fontweight="bold",
        )

    ax2.set_xticks(x2)
    ax2.set_xticklabels(c_labels, fontsize=8.3)
    ax2.set_ylabel("总直写速率 (M ops/s)", fontsize=9.5, color="#1e3a8a")
    ax2.set_ylim(0, 224)
    ax2.set_title("(b) 实验 3：主机并发核数（互不重叠 64B 行, 无 SFENCE）", fontsize=10.5, fontweight="bold")

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.plot(x2, c_cha, "-D", color="#d97706", lw=2.2, ms=6.5, zorder=6,
               label="主机 CHA 平均在途条数 (右轴)")
    ax2_r.set_ylim(0, 405)
    ax2_r.set_ylabel("主机 CHA 平均在途并行条数", fontsize=9.5, color="#b45309")
    ax2_r.tick_params(axis="y", labelcolor="#b45309")
    ax2_r.grid(False)

    for i, v in enumerate(c_cha):
        ax2_r.annotate(
            f"{v:.0f} 条",
            xy=(x2[i], v),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=8.5,
            fontweight="bold",
            color="#92400e",
            bbox=dict(boxstyle="round,pad=0.18", fc="#fffbeb", ec="#f59e0b", lw=0.8),
        )

    h1, lab1 = ax2.get_legend_handles_labels()
    h2, lab2 = ax2_r.get_legend_handles_labels()
    ax2.legend(h1 + h2, lab1 + lab2, loc="lower left", fontsize=7.8,
               frameon=True, facecolor="white", framealpha=0.95)

    fig.suptitle(
        "MRV-89 实验 2 & 3：地址分布与多核并发对 MOVDIR64B 入卡并行度的影响（uncore 2.2 GHz · 卡核静默 · 3 次重复 err=0）",
        fontsize=10.2, y=0.975, color="#334155",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    png = HERE / "mrv-89-exp23-pattern-cores.png"
    fig.savefig(png, dpi=200)
    fig.savefig(HERE / "mrv-89-exp23-pattern-cores.svg")
    plt.close(fig)
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
