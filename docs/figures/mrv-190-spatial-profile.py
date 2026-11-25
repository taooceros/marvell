"""MRV-190 Figure 2: Observed per-64 B-line miss/visibility profiles for CPU unfenced
MOVDIR64B (512 B & 1 KiB) and 4-engine back-to-back DSA 4 KiB + independent doorbell.

Reads raw verified runs directly from `logs/mrv190-2026-11-25/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv190-2026-11-25/runs.jsonl"


def load_by_label():
    rows = [json.loads(line) for line in RUNS_PATH.read_text().splitlines() if line.strip()]
    for r in rows:
        assert r["accepted"] is True and r["card"]["persistent_errors"] == 0
    by_lbl = defaultdict(list)
    for r in rows:
        by_lbl[r["label"]].append(r)
    return by_lbl


def pooled_per_line_pct(items):
    tot_n = sum(x["card"]["rounds_observed"] for x in items)
    n_chk = items[0]["card"]["check_lines"]
    counts = [sum(x["card"]["per_line_miss"][i] for x in items) for i in range(n_chk)]
    return np.array([c * 100.0 / tot_n for c in counts]), counts, tot_n


def med_pct(items):
    return statistics.median(x["card"]["reorder_pct"] for x in items)


def main():
    by_lbl = load_by_label()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.4, 5.9), gridspec_kw={"width_ratios": [1.05, 1.15]})

    # ---- Panel A: CPU unfenced MOVDIR64B (1 KiB & 512 B, forward vs reverse scan) ----
    p1024_rev, _, _ = pooled_per_line_pct(by_lbl["movdir_nf_1024B_hot_uc_rev"])
    p1024_fwd, _, _ = pooled_per_line_pct(by_lbl["movdir_nf_1024B_hot_uc_fwd"])
    p512_rev, _, _ = pooled_per_line_pct(by_lbl["movdir_nf_512B_hot_uc_rev"])
    p512_fwd, _, _ = pooled_per_line_pct(by_lbl["movdir_nf_512B_hot_uc_fwd"])

    m1024_rev = med_pct(by_lbl["movdir_nf_1024B_hot_uc_rev"])
    m1024_fwd = med_pct(by_lbl["movdir_nf_1024B_hot_uc_fwd"])
    m512_rev = med_pct(by_lbl["movdir_nf_512B_hot_uc_rev"])
    m512_fwd = med_pct(by_lbl["movdir_nf_512B_hot_uc_fwd"])

    x16 = np.arange(15)
    w = 0.36
    ax1.bar(x16 - w / 2, p1024_rev, width=w, color="#0969da", alpha=0.9,
            label=f"1 KiB 反扫 (14→0，中位数 {m1024_rev:.2f}%)")
    ax1.bar(x16 + w / 2, p1024_fwd, width=w, color="#54aeff", alpha=0.85,
            label=f"1 KiB 正扫 (0→14，中位数 {m1024_fwd:.2f}%)")

    x8 = np.arange(7)
    ax1.plot(x8, p512_fwd, "o-", color="#cf222e", lw=1.8, ms=5.5,
             label=f"512 B 正扫 (0→6，中位数 {m512_fwd:.2f}%)")
    ax1.plot(x8, p512_rev, "s--", color="#e16f24", lw=1.5, ms=5.0,
             label=f"512 B 反扫 (6→0，中位数 {m512_rev:.2f}%)")

    ax1.annotate(
        f"512 B 集中滞后行 2\n({p512_fwd[2]:.2f}% / {p512_rev[2]:.2f}%)",
        xy=(2, p512_fwd[2]), xytext=(2.8, 5.5),
        fontsize=8.4, color="#cf222e", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#cf222e", lw=1.0),
    )
    ax1.annotate(
        f"1 KiB 滞后主峰行 8–10、14\n(行 9 达 {p1024_rev[9]:.2f}%，行 14 达 {p1024_rev[14]:.2f}%，跨度达行 0)",
        xy=(9, p1024_rev[9]), xytext=(5.5, 6.22),
        fontsize=8.2, color="#0969da", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#0969da", lw=1.0),
    )

    ax1.set_xticks(x16)
    ax1.set_xticklabels([str(i) for i in x16], fontsize=8.8)
    ax1.set_xlim(-0.7, 14.7)
    ax1.set_ylim(0, 7.3)
    ax1.set_xlabel("前序 64 B 缓存行索引（尾行分别为 512 B 的行 7 与 1 KiB 的行 15）", fontsize=9.6)
    ax1.set_ylabel("该行未落地观测比例 (%)", fontsize=10)
    ax1.set_title("A. 组 4：CPU 无 SFENCE MOVDIR64B 逐行未落地（BAR4+0，90,000 笔）", fontsize=10.2, fontweight="bold", pad=10)
    ax1.legend(loc="upper left", fontsize=8.2, frameon=True, facecolor="white", framealpha=0.92)

    # ---- Panel B: 4-engine DSA back-to-back 4 KiB + independent doorbell ----
    pb_fcth, c_fcth, n_fcth = pooled_per_line_pct(by_lbl["dsa_b2b_4096B_fcth_e4_f0"])
    pb_cold, _, _ = pooled_per_line_pct(by_lbl["dsa_b2b_4096B_cold_e4_f0"])
    conv_p50 = statistics.median(x["card"]["conv_ns"]["p50"] for x in by_lbl["dsa_b2b_4096B_fcth_e4_f0"])

    tail_idx = list(range(54, 64))
    x_labels = ["0..53\n(均值)"] + [str(i) for i in tail_idx]
    vals_fcth = [float(np.mean(pb_fcth[:54]))] + [float(pb_fcth[i]) for i in tail_idx]
    vals_cold = [float(np.mean(pb_cold[:54]))] + [float(pb_cold[i]) for i in tail_idx]

    xp = np.arange(len(x_labels))
    w2 = 0.38
    colors_f = ["#8c959f"] + ["#cf222e" if i % 2 == 1 else "#0969da" for i in tail_idx]
    colors_c = ["#afb8c1"] + ["#ff8182" if i % 2 == 1 else "#80ccff" for i in tail_idx]

    ax2.bar(xp - w2 / 2, vals_fcth, width=w2, color=colors_f, alpha=0.95)
    ax2.bar(xp + w2 / 2, vals_cold, width=w2, color=colors_c, alpha=0.85)

    for idx_pos, line_i in enumerate(tail_idx, start=1):
        vf = pb_fcth[line_i]
        if line_i in (58, 59, 60, 61, 62, 63):
            txt_col = "#cf222e" if line_i % 2 == 1 else "#0969da"
            ax2.text(
                idx_pos - w2 / 2, vf + 1.8, f"{vf:.1f}%",
                ha="center", va="bottom",
                fontsize=8.0, fontweight="bold", color=txt_col,
            )

    ax2.text(
        0, vals_fcth[0] + 2.5, f"{vals_fcth[0]:.2f}%\n(全64行\n均未到)",
        ha="center", va="bottom", fontsize=7.6, color="#57606a",
    )

    lb_62_61 = (c_fcth[61] - c_fcth[62]) * 100.0 / n_fcth
    lb_60_59 = (c_fcth[59] - c_fcth[60]) * 100.0 / n_fcth
    ax2.text(
        0.03, 0.72,
        "独立门铃外部触发快照边际下界（反扫 63→0，单引擎搬运 4 KiB）：\n"
        f"• 行 62 已可见，更晚读出的行 61 仍缺：≥ {lb_62_61:.1f}%\n"
        f"• 行 60 已可见，更晚读出的行 59 仍缺：≥ {lb_60_59:.1f}%\n"
        f"• 全部缺失行零错收敛（快照后重读 p50 = {conv_p50:.0f} ns，含计测底噪；持久错 0）",
        transform=ax2.transAxes,
        fontsize=8.0,
        va="top",
        bbox=dict(boxstyle="round,pad=0.45", facecolor="#fff8c5", edgecolor="#d4a72c", alpha=0.92),
    )

    legend_handles = [
        Patch(facecolor="#cf222e", label="前冷尾热：奇数行 (左柱)"),
        Patch(facecolor="#0969da", label="前冷尾热：偶数行 (左柱)"),
        Patch(facecolor="#ff8182", label="源全冷：奇数行 (右柱)"),
        Patch(facecolor="#80ccff", label="源全冷：偶数行 (右柱)"),
    ]
    ax2.set_xticks(xp)
    ax2.set_xticklabels(x_labels, fontsize=8.6)
    ax2.set_ylim(0, 114)
    ax2.set_xlabel("4 KiB 载荷内 64 B 缓存行索引（反扫顺序：63 → 62 → … → 0）", fontsize=9.6)
    ax2.set_ylabel("独立门铃抵达时该行尚未落地比例 (%)", fontsize=10)
    ax2.set_title("B. 组 2：DSA 4 引擎背靠背（FENCE=0）逐行缺失分布（90,000 笔汇总）", fontsize=10.4, fontweight="bold", pad=10)
    ax2.legend(handles=legend_handles, loc="upper left", ncol=2, fontsize=7.8, frameon=True, facecolor="white", framealpha=0.92)

    fig.suptitle(
        "MRV-190：逐 64 B 行未落地实测分布 — CPU 无 SFENCE MOVDIR64B vs DSA 4 引擎背靠背独立门铃",
        fontsize=12.0, fontweight="bold", y=0.99,
    )
    fig.subplots_adjust(top=0.88)
    png = mrvplot.save(fig, "mrv-190-spatial-profile")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
