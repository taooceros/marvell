#!/usr/bin/env python3
"""MRV-177 Figure 2: Isolated small packets (W=1, W=2) and burst-stop tail latency (1C & 4C).

Reads verified runs directly from logs/mrv177-2026-11-24/runs.jsonl.
"""
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv177-2026-11-24/runs.jsonl"


def load_verified():
    by_cfg = defaultdict(list)
    with RUNS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            cp = r["card_pmu"]
            hp = r["host_pmu"]
            corrupt = sum(c["corrupt"] for c in cp["cores"])
            assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0 and corrupt == 0
            assert hp["min_pct"] >= 99.99 and cp["min_pct"] >= 99.99
            by_cfg[(r["phase"], r["workload"], r["arm"])].append(r)
    return by_cfg


def med_err(vals):
    s = sorted(vals)
    m = s[len(s) // 2]
    return m, m - s[0], s[-1] - m


def main():
    by_cfg = load_verified()

    fig, axes = plt.subplots(2, 2, figsize=(13.6, 8.8))
    fig.suptitle(
        "MRV-177 孤立 1–2 小包与突发骤停尾包延迟（BAR4 64 B + pem_wc，n = 3 中位数与 min–max）",
        fontsize=13,
        fontweight="bold",
        y=0.98,
    )

    c_base = "#475569"
    c_bqp = "#D97706"
    c_bqw = "#7C3AED"
    c_dbp = "#DC2626"
    c_dba = "#0F766E"
    c_dbu = "#5EEAD4"

    iso_arms = [
        ("base", "Baseline\n(紧盯 next)", c_base),
        ("bq_probe", "Arm A\nbq_probe", c_bqp),
        ("bq_wait", "Arm A 变体\nbq_wait", c_bqw),
        ("db_pkt_f", "Arm B1\n逐包门铃", c_dbp),
        ("db_adp_uf", "Arm B2 无\nSFENCE*", c_dbu),
        ("db_adp_f", "Arm B2\n批合并门铃", c_dba),
    ]

    for row, phase in enumerate(["1C", "4C"]):
        ax = axes[row, 0]
        x = np.arange(len(iso_arms))
        w = 0.25

        w1_p50, w1_p50_lo, w1_p50_hi = [], [], []
        w1_p99, w1_p99_lo, w1_p99_hi = [], [], []
        w2_p99, w2_p99_lo, w2_p99_hi = [], [], []

        for arm, _, _ in iso_arms:
            rs1 = by_cfg[(phase, "w1", arm)]
            m, lo, hi = med_err([r["tot_us"]["p50"] for r in rs1])
            w1_p50.append(m); w1_p50_lo.append(lo); w1_p50_hi.append(hi)
            m, lo, hi = med_err([r["tot_us"]["p99"] for r in rs1])
            w1_p99.append(m); w1_p99_lo.append(lo); w1_p99_hi.append(hi)

            rs2 = by_cfg.get((phase, "w2", arm), [])
            if rs2:
                m, lo, hi = med_err([r["tot_us"]["p99"] for r in rs2])
                w2_p99.append(m); w2_p99_lo.append(lo); w2_p99_hi.append(hi)
            else:
                w2_p99.append(np.nan); w2_p99_lo.append(0.0); w2_p99_hi.append(0.0)

        colors = [c for _, _, c in iso_arms]
        ax.bar(
            x - w, w1_p50, width=w, color=colors, alpha=0.92, edgecolor="#1E293B", linewidth=0.7,
            yerr=[w1_p50_lo, w1_p50_hi], capsize=2.5, label="W=1 p50",
        )
        ax.bar(
            x, w1_p99, width=w, color=colors, alpha=0.55, edgecolor="#1E293B", linewidth=0.7, hatch="//",
            yerr=[w1_p99_lo, w1_p99_hi], capsize=2.5, label="W=1 p99",
        )
        valid_w2 = [i for i, v in enumerate(w2_p99) if not np.isnan(v)]
        ax.bar(
            [x[i] + w for i in valid_w2],
            [w2_p99[i] for i in valid_w2],
            width=w,
            color=[colors[i] for i in valid_w2],
            alpha=0.28,
            edgecolor="#1E293B",
            linewidth=0.7,
            hatch="..",
            yerr=[[w2_p99_lo[i] for i in valid_w2], [w2_p99_hi[i] for i in valid_w2]],
            capsize=2.5,
            label="W=2 p99",
        )

        for i in range(len(iso_arms)):
            ax.text(x[i] - w, w1_p50[i] + 0.025, f"{w1_p50[i]:.2f}", ha="center", va="bottom", fontsize=7.8, fontweight="bold")
            ax.text(x[i], w1_p99[i] + 0.025, f"{w1_p99[i]:.2f}", ha="center", va="bottom", fontsize=7.5, color="#334155")
            if not np.isnan(w2_p99[i]):
                ax.text(x[i] + w, w2_p99[i] + 0.025, f"{w2_p99[i]:.2f}", ha="center", va="bottom", fontsize=7.5, color="#475569")

        d_db = w1_p50[4] - w1_p50[0]
        d_sf = w1_p50[5] - w1_p50[4]
        d_tot = w1_p50[5] - w1_p50[0]
        ax.annotate(
            f"W=1 +{d_tot:.2f} µs\n(门铃 +{d_db:.2f} + SFENCE +{d_sf:.2f})",
            xy=(5 - w, w1_p50[5] + 0.085),
            xytext=(3.55, 1.54 if phase == "1C" else 1.62),
            ha="center",
            fontsize=8.1,
            fontweight="bold",
            color="#0F766E",
            bbox=dict(boxstyle="round,pad=0.28", facecolor="#F0FDFA", edgecolor="#0F766E", lw=0.9),
            arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.1),
        )

        ax.axhline(w1_p50[0], color=c_base, linestyle=":", linewidth=1.0, alpha=0.7)
        ax.set_xticks(x)
        ax.set_xticklabels([lbl for _, lbl, _ in iso_arms], fontsize=8.3)
        ax.set_ylabel("端到端 RTT (µs)", fontsize=9.5)
        ax.set_ylim(0.5, 1.80 if phase == "1C" else 1.86)
        sub_note = "（*db_adp_uf 仅测 W=1 拆解 SFENCE）" if row == 1 else ""
        ax.set_title(f"{phase} 孤立小包 W=1 / W=2 延迟{sub_note}", fontsize=10.5, fontweight="bold")
        if row == 0:
            ax.legend(loc="upper left", fontsize=8.2, frameon=True, ncol=3)

    bt_arms = [
        ("base", "Baseline\n(紧盯 next)", c_base),
        ("bq_probe", "Arm A\nbq_probe", c_bqp),
        ("bq_wait", "Arm A 变体\nbq_wait", c_bqw),
        ("db_pkt_f", "Arm B1\n逐包门铃", c_dbp),
        ("db_adp_f", "Arm B2\n批合并门铃", c_dba),
    ]

    for row, phase in enumerate(["1C", "4C"]):
        ax = axes[row, 1]
        x = np.arange(len(bt_arms))
        w = 0.25

        res_tail, res_t_lo, res_t_hi = [], [], []
        res_tail_p50 = []
        res_burst, res_b_lo, res_b_hi = [], [], []
        pau_tail, pau_t_lo, pau_t_hi = [], [], []

        for arm, _, _ in bt_arms:
            rs_res = by_cfg[(phase, "burst32_resid2", arm)]
            m, lo, hi = med_err([r["tail_us"]["p99"] for r in rs_res])
            res_tail.append(m); res_t_lo.append(lo); res_t_hi.append(hi)
            res_tail_p50.append(med_err([r["tail_us"]["p50"] for r in rs_res])[0])
            m, lo, hi = med_err([r["burst_us"]["p99"] for r in rs_res])
            res_burst.append(m); res_b_lo.append(lo); res_b_hi.append(hi)

            rs_pau = by_cfg[(phase, "burst32_pause_iso2", arm)]
            m, lo, hi = med_err([r["tail_us"]["p99"] for r in rs_pau])
            pau_tail.append(m); pau_t_lo.append(lo); pau_t_hi.append(hi)

        colors = [c for _, _, c in bt_arms]
        ax.bar(
            x - w, res_tail, width=w, color=colors, alpha=0.92, edgecolor="#1E293B", linewidth=0.7,
            yerr=[res_t_lo, res_t_hi], capsize=2.5, label="紧接残余 2 包 tail p99 (gap=0)",
        )
        ax.bar(
            x, res_burst, width=w, color=colors, alpha=0.45, edgecolor="#1E293B", linewidth=0.7, hatch="//",
            yerr=[res_b_lo, res_b_hi], capsize=2.5, label="同期突发体 32 包 burst p99",
        )
        ax.bar(
            x + w, pau_tail, width=w, color=colors, alpha=0.22, edgecolor="#1E293B", linewidth=0.7, hatch="..",
            yerr=[pau_t_lo, pau_t_hi], capsize=2.5, label="停顿 5 µs 后孤立 2 包 tail p99",
        )

        for i in range(len(bt_arms)):
            ax.text(x[i] - w, res_tail[i] + res_t_hi[i] + 0.08, f"{res_tail[i]:.2f}", ha="center", va="bottom", fontsize=7.8, fontweight="bold")
            ax.text(x[i], res_burst[i] + res_b_hi[i] + 0.08, f"{res_burst[i]:.2f}", ha="center", va="bottom", fontsize=7.5, color="#334155")
            ax.text(x[i] + w, pau_tail[i] + pau_t_hi[i] + 0.08, f"{pau_tail[i]:.2f}", ha="center", va="bottom", fontsize=7.5, color="#475569")

        ax.annotate(
            f"逐包门铃冲撞\nburst p99 = {res_burst[3]:.2f} µs",
            xy=(3, res_burst[3]),
            xytext=(1.75, 4.78),
            fontsize=8.0,
            color="#B91C1C",
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#FEF2F2", edgecolor="#DC2626", lw=0.8),
            arrowprops=dict(arrowstyle="->", color="#DC2626", lw=1.0),
        )
        dp50 = res_tail_p50[4] - res_tail_p50[0]
        if phase == "1C":
            ax.annotate(
                f"1C 紧接尾包 p99\n{res_tail[4]:.2f} vs Base {res_tail[0]:.2f} µs\n"
                f"(p50: {res_tail_p50[4]:.2f} vs {res_tail_p50[0]:.2f}, +{dp50:.2f})",
                xy=(4 - w, res_tail[4] + 0.36),
                xytext=(4.46, 3.60),
                ha="right",
                fontsize=7.3,
                color="#0F766E",
                fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.23", facecolor="#F0FDFA", edgecolor="#0F766E", lw=0.9),
                arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.0),
            )
        else:
            ax.annotate(
                f"4C 紧接尾包 p99 最优\n{res_tail[4]:.2f} vs Base {res_tail[0]:.2f} µs\n"
                f"(p50: {res_tail_p50[4]:.2f} vs {res_tail_p50[0]:.2f}, +{dp50:.2f})",
                xy=(4 - w, res_tail[4] + 0.36),
                xytext=(4.46, 3.60),
                ha="right",
                fontsize=7.3,
                color="#0F766E",
                fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.23", facecolor="#F0FDFA", edgecolor="#0F766E", lw=0.9),
                arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.0),
            )

        ax.set_xticks(x)
        ax.set_xticklabels([lbl for _, lbl, _ in bt_arms], fontsize=8.3)
        ax.set_ylabel("p99 延迟 (µs)", fontsize=9.5)
        ax.set_ylim(0, 6.2)
        bq_note = "fast_dt=0" if phase == "1C" else "fast_dt=480ns"
        ax.set_title(f"{phase} 突发骤停尾包 burst=32+2（bq_probe: {bq_note}）", fontsize=10.5, fontweight="bold")
        if row == 0:
            ax.legend(loc="upper left", fontsize=7.9, frameon=True)

    fig.subplots_adjust(top=0.91, hspace=0.34, wspace=0.18)
    png = save(fig, "mrv-177-small-pkt-tail")
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
