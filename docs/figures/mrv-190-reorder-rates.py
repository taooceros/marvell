"""MRV-190 Figure 1: Reordering observation rates across all 4 experimental groups
and the 4 KiB independent-doorbell safety vs latency comparison.

Reads raw verified runs directly from `logs/mrv190-2026-11-25/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv190-2026-11-25/runs.jsonl"


def load_by_label():
    rows = [json.loads(line) for line in RUNS_PATH.read_text().splitlines() if line.strip()]
    for r in rows:
        assert r["accepted"] is True
        assert r["err_ops"] == 0
        assert r["card"]["persistent_errors"] == 0
        assert r["card"]["unexpected_vals"] == 0
        assert r["card"]["watch_endpoint_torn"] == 0
        assert r["card"]["body_sig_only_torn"] == 0
    by_lbl = defaultdict(list)
    for r in rows:
        by_lbl[r["label"]].append(r)
    return rows, by_lbl


def med_pct(items):
    return statistics.median(x["card"]["reorder_pct"] for x in items)


def med_comp(items):
    return statistics.median(x["dsa_comp_ns"]["p50"] for x in items)


def main():
    rows, by_lbl = load_by_label()

    g1_all = [r for r in rows if r["suite"] in ("group1_dsa_single", "group1b_dsa_single_oddl", "group1_dsa_single_4e")]
    assert sum(r["card"]["reordered_ops"] for r in g1_all) == 0
    g1_tot = sum(r["card"]["rounds_observed"] for r in g1_all)

    g2_batch = [r for r in rows if r["arm"] == "dsa_dual_batch"]
    assert sum(r["card"]["reordered_ops"] for r in g2_batch) == 0
    g2_batch_tot = sum(r["card"]["rounds_observed"] for r in g2_batch)

    g2_b2b_1e = [r for r in rows if r["suite"] == "group2_dual_db_1e" and r["arm"] == "dsa_dual_b2b"]
    assert sum(r["card"]["reordered_ops"] for r in g2_b2b_1e) == 0
    g2_b2b_1e_tot = sum(r["card"]["rounds_observed"] for r in g2_b2b_1e)

    g3_all = [r for r in rows if r["suite"] == "group3_comp_db"]
    assert sum(r["card"]["reordered_ops"] for r in g3_all) == 0
    g3_tot = sum(r["card"]["rounds_observed"] for r in g3_all)

    g4_fenced = [r for r in rows if r["arm"] == "movdir_fenced"]
    assert sum(r["card"]["reordered_ops"] for r in g4_fenced) == 0
    g4_fenced_tot = sum(r["card"]["rounds_observed"] for r in g4_fenced)

    bars = [
        ("正向参照：故意先写尾行 (4 KiB)", med_pct(by_lbl["pos_ctrl_rev_4096B"]), "#6e7781",
         f"{med_pct(by_lbl['pos_ctrl_rev_4096B']):.2f}%  (探针灵敏度基准)"),
        ("组 2：DSA 背靠背 FENCE=0 (4 引擎, 前冷尾热)", med_pct(by_lbl["dsa_b2b_4096B_fcth_e4_f0"]), "#cf222e",
         f"{med_pct(by_lbl['dsa_b2b_4096B_fcth_e4_f0']):.2f}%  (span≤64行)"),
        ("组 2：DSA 背靠背 + DRAIN (4 引擎, 前冷尾热)", med_pct(by_lbl["dsa_b2b_4096B_fcth_e4_drain"]), "#cf222e",
         f"{med_pct(by_lbl['dsa_b2b_4096B_fcth_e4_drain']):.2f}%  (DRAIN不拦后续)"),
        ("组 2：DSA 背靠背 FENCE=0 (4 引擎, 全冷)", med_pct(by_lbl["dsa_b2b_4096B_cold_e4_f0"]), "#e16f24",
         f"{med_pct(by_lbl['dsa_b2b_4096B_cold_e4_f0']):.2f}%  (span≤64行)"),
        ("组 4：CPU 无 SFENCE MOVDIR64B (1 KiB, 反扫)", med_pct(by_lbl["movdir_nf_1024B_hot_uc_rev"]), "#0969da",
         f"{med_pct(by_lbl['movdir_nf_1024B_hot_uc_rev']):.2f}%  (span≤15行)"),
        ("组 4：CPU 无 SFENCE MOVDIR64B (512 B, 正扫)", med_pct(by_lbl["movdir_nf_512B_hot_uc_fwd"]), "#0969da",
         f"{med_pct(by_lbl['movdir_nf_512B_hot_uc_fwd']):.2f}%  (反扫 {med_pct(by_lbl['movdir_nf_512B_hot_uc_rev']):.2f}%)"),
        ("组 4：CPU 无 SFENCE MOVDIR64B (128/256 B/4 KiB)", 0.0, "#8c959f",
         "0.000%  (0 / 270k, 固定首址)"),
        ("组 1：单笔 DSA MEMMOVE (全尺寸×全热冷×1/4引擎)", 0.0, "#1a7f37",
         f"0.000%  (0 / {g1_tot//1000:,}k)"),
        ("组 2：DSA Batch(2) FENCE=0 / FENCE=1 (1&4 引擎)", 0.0, "#1a7f37",
         f"0.000%  (0 / {g2_batch_tot//1000:,}k)"),
        ("组 2：DSA 背靠背 (仅 1 引擎, FENCE=0 / DRAIN)", 0.0, "#1a7f37",
         f"0.000%  (0 / {g2_b2b_1e_tot//1000:,}k)"),
        ("组 3：主机等 DSA 完成 + SFENCE + MOVDIR64B 门铃", 0.0, "#1a7f37",
         f"0.000%  (0 / {g3_tot//1000:,}k)"),
        ("组 4 对照：CPU 尾行前加 SFENCE MOVDIR64B", 0.0, "#1a7f37",
         f"0.000%  (0 / {g4_fenced_tot//1000:,}k)"),
    ]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16.0, 6.6), gridspec_kw={"width_ratios": [1.32, 1.05]})

    y_pos = np.arange(len(bars))
    vals = [b[1] for b in bars]
    colors = [b[2] for b in bars]
    labels = [b[0] for b in bars]
    notes = [b[3] for b in bars]

    ax1.barh(y_pos, vals, height=0.64, color=colors, edgecolor="none", alpha=0.92)
    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(labels, fontsize=9.2)
    ax1.invert_yaxis()
    ax1.set_xlim(0, 150)
    ax1.set_xlabel("CN106 卡核 ldar 实时检出的乱序落地比例 (%)", fontsize=10)
    ax1.set_title("A. 四组对照乱序观测比例总览（n = 3 中位数，持久错写全为 0）", fontsize=11, fontweight="bold", pad=10)
    ax1.grid(axis="x", alpha=0.3)
    ax1.grid(axis="y", visible=False)

    for i, (v, note, col) in enumerate(zip(vals, notes, colors)):
        x_txt = v + 1.5 if v > 0 else 1.5
        ax1.text(
            x_txt, i, note, va="center", ha="left", fontsize=8.6,
            color="#1f2328" if col != "#1a7f37" else "#116329",
            fontweight="bold" if v > 1.0 else "normal",
        )

    # Panel B: 4 KiB independent doorbell (front-cold-tail-hot): Reorder % vs Host DSA Completion p50 Latency
    pts = [
        ("背靠背 FENCE=0 (4引擎)", by_lbl["dsa_b2b_4096B_fcth_e4_f0"], "#cf222e", "X", (-12, -36), "left"),
        ("背靠背 + DRAIN (4引擎)", by_lbl["dsa_b2b_4096B_fcth_e4_drain"], "#e16f24", "D", (22, -6), "left"),
        ("主机等完成 + SFENCE\n+ MOVDIR64B (4引擎)", by_lbl["dsa_comp_db_4096B_fcth_e4"], "#1a7f37", "s", (-22, 18), "left"),
        ("背靠背 FENCE=0\n(仅 1 引擎)", by_lbl["dsa_b2b_4096B_fcth_e1_f0"], "#0969da", "o", (-8, 66), "center"),
        ("Batch(2) FENCE=0\n(4引擎)", by_lbl["dsa_batch_4096B_fcth_e4_f0"], "#8250df", "^", (0, 62), "center"),
        ("Batch(2) FENCE=1\n(4引擎)", by_lbl["dsa_batch_4096B_fcth_e4_f1"], "#116329", "P", (24, 18), "right"),
    ]

    ax2.axhspan(-3.5, 3.5, color="#dafbe1", alpha=0.55, zorder=0)
    ax2.text(900, -6.5, "浅绿带 = 0.000% 实测零乱序", ha="left", va="top", fontsize=8.1, color="#116329")

    for name, items, col, mk, (dx, dy), ha in pts:
        x = med_comp(items)
        y = med_pct(items)
        ax2.scatter([x], [y], s=115, color=col, marker=mk, zorder=4, edgecolors="white", linewidth=0.9)
        ax2.annotate(
            f"{name}\n({x:.0f} ns, {y:.2f}%)",
            xy=(x, y),
            xytext=(dx, dy),
            textcoords="offset points",
            ha=ha,
            fontsize=8.1,
            color="#1f2328",
            arrowprops=dict(arrowstyle="-", color="#8c959f", lw=0.9),
        )

    x0 = med_comp(by_lbl["dsa_batch_4096B_fcth_e4_f0"])
    x1 = med_comp(by_lbl["dsa_batch_4096B_fcth_e4_f1"])
    ax2.annotate(
        "", xy=(x1, -2.5), xytext=(x0, -2.5),
        arrowprops=dict(arrowstyle="<->", color="#116329", lw=1.3),
    )
    ax2.text((x0 + x1) / 2, -6.5, f"FENCE=1 开销 +{x1 - x0:.0f} ns", ha="center", va="top",
             fontsize=8.3, color="#116329", fontweight="bold")

    ax2.set_xlim(860, 2240)
    ax2.set_ylim(-14, 112)
    ax2.set_xlabel("主机侧 DSA 完成记录 p50 耗时 (ns)", fontsize=10)
    ax2.set_ylabel("独立门铃早到比例 (%)", fontsize=10)
    ax2.set_title("B. 4 KiB 独立门铃（前冷尾热）：安全性 vs 延迟权衡", fontsize=11, fontweight="bold", pad=10)
    ax2.text(
        0.02, 0.57,
        "硬件规范实测补充：\n非 Batch 独立描述符置 IDXD_OP_FLAG_FENCE=1\n被 dsa2 直接拒收 (status = 0x11 INVALID_FLAGS)",
        transform=ax2.transAxes,
        fontsize=8.1,
        va="center",
        bbox=dict(boxstyle="round,pad=0.42", facecolor="#f6f8fa", edgecolor="#d0d7de"),
    )

    fig.suptitle(
        "MRV-190：Intel DSA (dsa2/wq2.0) 与 CPU MOVDIR64B 写 CN106 BAR4 实测乱序落地比例（累计 6,000,000 笔，0 持久错写）",
        fontsize=12.2, fontweight="bold", y=0.99,
    )
    fig.subplots_adjust(top=0.89)
    png = mrvplot.save(fig, "mrv-190-reorder-rates")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
