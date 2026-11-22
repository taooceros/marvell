"""MRV-125 Figure 2: Root-cause dissection from raw runs.jsonl:
  (a) Per-message host time budget (H2C pure TX vs closed-loop p1) + LLC/IMC/fb_pct PMU proving
      2048 B (2^11) set-aliasing vs 2112 B odd stride (4.00 MiB touched) & true 128 MiB overflow;
  (b) Four-group comparison isolating strictly single-variable inline RDTSC MLP tax (p1 vs p2b,
      p2c vs p2a) while explicitly marking the coupled W (64->128) + T_pause (0->1 us) step.
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
        ("2112B_rand_132m", "2112B rand\n132 MiB\n(4 MiB 活跃行)"),
        ("2KiB_seq_128m", "2KiB seq\n128 MiB\n(2^11 顺序)"),
        ("2KiB_cold_128m", "2KiB cold\n128 MiB\n(只读冷源)"),
        ("2KiB_rand_128m", "2KiB rand\n128 MiB\n(2^11 别名)"),
        ("64B_rand_128m", "64B rand\n128 MiB\n(真 128 MiB)"),
    ]
    y = np.arange(len(tiers_a))
    h2c_ns, cl_ns, delta_ns = [], [], []
    llc_m, imc_rd, imc_wr, cl_mops, h2c_fb = [], [], [], [], []
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
        h2c_fb.append(statistics.median([r["host_pmu"]["fb_pct"] for r in rh]))

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

    ax1.text(
        78.5, 2.15,
        f"p4a LFB 满载率 (fb_pct):\n"
        f"FillRetain 2KiB/真128M: {h2c_fb[5]:.2f}% / {h2c_fb[6]:.2f}%\n"
        f"(卡 Store Buffer RFO 排空)\n"
        f"ColdRead 只读: {h2c_fb[4]:.2f}%",
        ha="right", va="top", fontsize=7.6, color="#334155",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#f8fafc", edgecolor="#94a3b8", lw=0.8)
    )

    ax1.set_yticks(y)
    ax1.set_yticklabels([lbl for _, lbl in tiers_a], fontsize=8.7)
    ax1.invert_yaxis()
    ax1.set_xlim(0, 80)
    ax1.set_ylim(6.7, -0.75)
    ax1.set_xlabel("单消息主机核耗时分解（ns/msg，cpu40 3.90 GHz）", fontsize=10)
    ax1.set_title("(a) 主机本地 DRAM 税：2^11 别名 vs 4 MiB 活跃奇数行 vs 真 128 MiB", fontsize=9.8, fontweight="bold", pad=8)
    ax1.legend(loc="upper right", bbox_to_anchor=(0.99, 0.99), frameon=True, facecolor="white", fontsize=8.0)

    # --- Panel (b): Four-group comparison (non-orthogonal W=64->128 + T_pause vs single-var RDTSC) ---
    tiers_b = [
        ("64B_seq_4m", "64B seq\n4 MiB"),
        ("64B_rand_4m", "64B rand\n4 MiB"),
        ("2112B_rand_132m", "2112B rand\n(4 MiB 活跃)"),
        ("2KiB_rand_128m", "2KiB rand\n128 MiB"),
    ]
    parts_b = [
        ("p2a_wc1_unsh_pkt", "p2a: W=64, T_pause=0, 逐包戳", "#cbd5e1", "#64748b"),
        ("p2c_wc1_unsh_bts", "p2c: W=64, T_pause=0, 批戳", "#94a3b8", "#334155"),
        ("p2b_wc1_shaped_pkt", "p2b: W=128, T_pause=1µs, 逐包戳", "#5eead4", "#0f766e"),
        ("p1_wc1_shaped_bts", "p1: W=128, T_pause=1µs, 批戳", "#0f766e", "#042f2e"),
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
            if i in (1, 2) and j == 2:
                dy = 2.4
            ax2.text(xb[i] + offset, meds[i] + dy, f"{meds[i]:.1f}",
                     ha="center", va="bottom", fontsize=7.6,
                     fontweight="bold" if part == "p1_wc1_shaped_bts" else "normal",
                     color="#0f766e" if part == "p1_wc1_shaped_bts" else "#334155")

    ax2.annotate(
        "单变量逐包 RDTSC 税 -33%\n(p1 vs p2b 固定 W=128/1µs,\n44.4→29.7, cbat=28.9)",
        xy=(1 + 0.5 * bw, 34.0), xytext=(1.15, 57.5),
        fontsize=7.6, color="#0f766e", ha="center", va="center",
        arrowprops=dict(arrowstyle="->", color="#0f766e", lw=1.2, connectionstyle="arc3,rad=0.10"),
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#f0fdfa", edgecolor="#0f766e", lw=0.8)
    )
    ax2.annotate(
        "2KiB 增幅拆解 (非正交):\n"
        "p2a→p2c 去逐包戳占 64.8% (+5.75)\n"
        "p2c→p1 混变 W(64→128)+T_pause\n"
        "仅再增 +16.3% (+3.12), p50 翻倍",
        xy=(3 - 0.5 * bw, 20.8), xytext=(2.82, 55.0),
        fontsize=7.5, color="#334155", ha="center", va="center",
        arrowprops=dict(arrowstyle="->", color="#475569", lw=1.2, connectionstyle="arc3,rad=-0.10"),
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#fffbeb", edgecolor="#d97706", lw=0.9)
    )

    ax2.set_xticks(xb)
    ax2.set_xticklabels([lbl for _, lbl in tiers_b], fontsize=8.9)
    ax2.set_ylim(0, 79)
    ax2.set_ylabel("闭环吞吐（Mmsg/s）", fontsize=10)
    ax2.set_title("(b) 四组非正交对比：单变量 RDTSC 税 vs 耦合 W+退避增幅", fontsize=9.8, fontweight="bold", pad=8)
    ax2.legend(loc="upper center", ncol=2, frameon=True, facecolor="white", fontsize=7.4, columnspacing=0.9)
    fig.suptitle(
        "MRV-125 机理剥离：2^11 切片组别名、4 MiB 活跃 vs 真 128 MiB 溢出、单变量 RDTSC 税与 W+T_pause 混淆标注（n=3 交错）",
        fontsize=10.5, y=0.99
    )
    png = save(fig, "mrv-125-dissection")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
