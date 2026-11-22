"""MRV-125 Figure 2: Root-cause dissection from raw runs.jsonl:
  (a) Per-message host time budget (H2C pure TX vs closed-loop p1) + LLC/IMC PMU proving
      2048 B set-aliasing vs 2112 B odd stride & local single-core DRAM budget;
  (b) 2x2 factorial (Card Shaping x Batch/Per-packet TX RDTSC) isolating host MLP serialization.
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

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 6.0), gridspec_kw={"width_ratios": [1.18, 1.0]})

    # --- Panel (a): ns/msg decomposition (H2C ceiling vs CL p1) ---
    tiers_a = [
        ("64B_seq_4m", "64B seq\n4 MiB"),
        ("64B_rand_4m", "64B rand\n4 MiB"),
        ("2112B_rand_132m", "2112B rand\n132 MiB\n(反别名)"),
        ("2KiB_seq_128m", "2KiB seq\n128 MiB"),
        ("2KiB_cold_128m", "2KiB cold\n128 MiB"),
        ("2KiB_rand_128m", "2KiB rand\n128 MiB\n(2^11 别名)"),
        ("64B_rand_128m", "64B rand\n128 MiB\n(真容量)"),
    ]
    y = np.arange(len(tiers_a))
    h2c_ns, cl_ns, delta_ns = [], [], []
    llc_m, imc_rd, imc_wr, cl_mops = [], [], [], []
    for cfg, _ in tiers_a:
        rh = by_pc[("p4a_h2c_ceiling", cfg)]
        rc = by_pc[("p1_wc1_shaped_bts", cfg)]
        hn = statistics.median([r["ns_per_msg"] for r in rh])
        cn = statistics.median([r["ns_per_msg"] for r in rc])
        h2c_ns.append(hn)
        cl_ns.append(cn)
        delta_ns.append(cn - hn)
        cl_mops.append(statistics.median([r["mops"] for r in rc]))
        llc_m.append(statistics.median([r["host_pmu"]["llc_load_misses"] / r["measured"] for r in rc]))
        imc_rd.append(statistics.median([r["host_pmu"]["imc_cas_rd"] / r["measured"] for r in rc]))
        imc_wr.append(statistics.median([r["host_pmu"]["imc_cas_wr"] / r["measured"] for r in rc]))

    ax1.barh(y, h2c_ns, height=0.58, color="#0f766e", edgecolor="#115e59", linewidth=0.8,
             label="单向 H2C 纯 TX 上限（p4a：写池 + MOVDIR64B）", zorder=3)
    ax1.barh(y, delta_ns, left=h2c_ns, height=0.58, color="#99f6e4", edgecolor="#0f766e", linewidth=0.8,
             label="同核双向增量（RX 排空/校验/直方图 + ROB 竞争）", zorder=3)

    for i in range(len(tiers_a)):
        txt_h = f"{h2c_ns[i]:.1f} ns" if h2c_ns[i] >= 11.0 else f"{h2c_ns[i]:.1f}"
        ax1.text(h2c_ns[i] / 2, y[i], txt_h, ha="center", va="center",
                 fontsize=8.3, fontweight="bold", color="white")
        pmu_str = f"llcM={llc_m[i]:.2f}, IMC={imc_rd[i]:.2f}/{imc_wr[i]:.2f}"
        ax1.text(cl_ns[i] + 1.0, y[i] - 0.13, f"{cl_ns[i]:.1f} ns ({cl_mops[i]:.1f} M/s)",
                 ha="left", va="center", fontsize=8.8, fontweight="bold", color="#1e293b")
        ax1.text(cl_ns[i] + 1.0, y[i] + 0.17, pmu_str,
                 ha="left", va="center", fontsize=7.9, color="#475569")

    ax1.set_yticks(y)
    ax1.set_yticklabels([lbl for _, lbl in tiers_a], fontsize=9)
    ax1.invert_yaxis()
    ax1.set_xlim(0, 79)
    ax1.set_ylim(6.7, -0.75)
    ax1.set_xlabel("单消息主机核耗时分解（ns/msg，cpu40 3.90 GHz）", fontsize=10)
    ax1.set_title("(a) 主机本地 DRAM 税与 2^11 步长组相联别名剥离", fontsize=10.5, fontweight="bold", pad=8)
    # Place legend in wide-open upper-right area (bars 0..2 stop at ~23 ns, labels end by ~46 ns)
    ax1.legend(loc="upper right", bbox_to_anchor=(0.99, 0.99), frameon=True, facecolor="white", fontsize=8.1)

    # --- Panel (b): 2x2 factorial (Shaping x RDTSC stamp) ---
    tiers_b = [
        ("64B_seq_4m", "64B seq\n4 MiB"),
        ("64B_rand_4m", "64B rand\n4 MiB"),
        ("2112B_rand_132m", "2112B rand\n132 MiB"),
        ("2KiB_rand_128m", "2KiB rand\n128 MiB"),
    ]
    parts_b = [
        ("p2a_wc1_unsh_pkt", "p2a 无整形 + 逐包戳 (W=64)", "#cbd5e1", "#64748b"),
        ("p2c_wc1_unsh_bts", "p2c 无整形 + 批戳 (W=64)", "#94a3b8", "#334155"),
        ("p2b_wc1_shaped_pkt", "p2b 整形 + 逐包戳 (W=128)", "#5eead4", "#0f766e"),
        ("p1_wc1_shaped_bts", "p1 整形 + 批戳 (W=128)", "#0f766e", "#042f2e"),
    ]
    xb = np.arange(len(tiers_b))
    bw = 0.20

    for j, (part, lbl, col, ecol) in enumerate(parts_b):
        offset = (j - 1.5) * bw
        meds, alls = [], []
        for cfg, _ in tiers_b:
            vals = [r["mops"] for r in by_pc[(part, cfg)]]
            alls.append(vals)
            meds.append(statistics.median(vals))
        ax2.bar(xb + offset, meds, width=bw, color=col, edgecolor=ecol, linewidth=0.8, label=lbl, zorder=3)
        for i in range(len(tiers_b)):
            ax2.scatter([xb[i] + offset] * len(alls[i]), alls[i], color="#0f172a", s=11, zorder=5, alpha=0.8)
            dy = 0.8
            # Slight vertical stagger only where adjacent p2c/p2b bars are within 1.2 M/s
            if i in (1, 2) and j == 2:
                dy = 2.4
            ax2.text(xb[i] + offset, meds[i] + dy, f"{meds[i]:.1f}",
                     ha="center", va="bottom", fontsize=7.6,
                     fontweight="bold" if part == "p1_wc1_shaped_bts" else "normal",
                     color="#0f766e" if part == "p1_wc1_shaped_bts" else "#334155")

    ax2.annotate(
        "逐包 RDTSC MLP 税 -33%\n(44.4→29.7, 卡仍快态 cbat=28.9)",
        xy=(1 + 0.5 * bw, 33.8), xytext=(1.35, 57.5),
        fontsize=8.0, color="#0f766e", ha="center", va="center",
        arrowprops=dict(arrowstyle="->", color="#0f766e", lw=1.2, connectionstyle="arc3,rad=0.12"),
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#f0fdfa", edgecolor="#0f766e", lw=0.8)
    )
    ax2.annotate(
        "2KiB 逐包税 -36%\n(22.3→14.2 M/s,\ncbat=18.7 仍快态)",
        xy=(3 + 0.5 * bw, 16.3), xytext=(2.85, 36.0),
        fontsize=7.9, color="#334155", ha="center", va="center",
        arrowprops=dict(arrowstyle="->", color="#475569", lw=1.2),
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#f8fafc", edgecolor="#64748b", lw=0.8)
    )

    ax2.set_xticks(xb)
    ax2.set_xticklabels([lbl for _, lbl in tiers_b], fontsize=9.2)
    ax2.set_ylim(0, 68)
    ax2.set_ylabel("闭环吞吐（Mmsg/s）", fontsize=10)
    ax2.set_title("(b) 2×2 因子剥离：卡侧整形 × 逐包 RDTSC 串行化", fontsize=10.5, fontweight="bold", pad=8)
    ax2.legend(loc="upper right", frameon=True, facecolor="white", fontsize=7.9)

    fig.suptitle(
        "MRV-125 机理剥离：2048 B 组相联别名、单核双向预算与 Sapphire Rapids RDTSC+MOVDIR64B MLP 税（n=3 交错）",
        fontsize=11, y=0.99
    )

    png = save(fig, "mrv-125-dissection")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
