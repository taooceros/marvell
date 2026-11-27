#!/usr/bin/env python3
"""Plot MRV-235 Figure 1: Page-table attribute comparison (Normal-WB vs MT_NORMAL_NC 0x44 vs Inner-NC, Outer-WB 0xF4)."""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import mrvplot  # registers Droid Sans Fallback and shared rcParams

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv235-2026-11-27/runs.jsonl"


def load_verified():
    by_cond = {}
    with RUNS.open() as f:
        for line in f:
            r = json.loads(line)
            cp = r["card_pmu"]
            cor = sum(w["corrupt"] for w in cp["workers"])
            assert r["lost"] == 0 and r["bad_or_torn"] == 0 and cor == 0
            assert r["host_min_pct"] >= 99.9 and cp["min_pct"] >= 99.9
            by_cond.setdefault(r["cond"], []).append(r)
    return by_cond


def per_msg_pmu(r):
    cp = r["card_pmu"]
    k = r["card_cores"]
    tak = max(1, sum(w["taken_win"] for w in cp["workers"]))
    l1r = sum(cp["core_pmu"][i]["l1d_cache_refill"] for i in range(k)) / tak
    l2i = sum(cp["core_pmu"][i]["l2d_cache_inval"] for i in range(k)) / tak
    snp = cp["tad"]["tad_snp_msh_out_any"]["val"] / tak
    dss_rd = cp["tad"]["tad_req_msh_out_dss_rd"]["val"] / tak
    ddr_rd = cp["ddr"]["reads_total"] / tak
    return l1r, l2i, snp, dss_rd, ddr_rd


def main():
    by_cond = load_verified()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.8), gridspec_kw={"width_ratios": [1.18, 1.0]})

    x_pos = np.array([0, 1, 2], dtype=float)

    series = [
        ("Normal-WB (0xFF) · ldar [MRV-231基线]", ["wb64k_k1_w64", "wb64k_k2_w64", "wb64k_k4_w64"],
         "#555555", "^", "--", 2.0, -0.05),
        ("Normal-WB (0xFF) · 1-pass 64B ldp", ["wb64k_1p_k1_w64", "wb64k_1p_k2_w64", "wb64k_1p_k4_w64"],
         "#9A9A9A", "v", ":", 1.8, 0.05),
        ("MT_NORMAL_NC (0x44, 零改MAIR) · ldar", ["nc44_k1_w64", "nc44_k2_w64", "nc44_k4_w64"],
         "#2A9D8F", "s", "--", 2.0, -0.04),
        ("Inner-NC, Outer-WB (0xF4) · ldar", ["f4_ish_k1_w64", "f4_ish_k2_w64", "f4_ish_k4_w64"],
         "#1D6FA5", "D", ":", 1.8, 0.04),
        ("MT_NORMAL_NC (0x44, 零改MAIR) · 1-pass ldp", ["nc44_1p_k1_w64", "nc44_1p_k2_w64", "nc44_1p_k4_w64"],
         "#D62828", "o", "-", 2.5, -0.03),
        ("Inner-NC, Outer-WB (0xF4) · 1-pass ldp", ["f4_1p_k1_w64", "f4_1p_k2_w64", "f4_1p_k4_w64"],
         "#003049", "x", "-", 1.9, 0.03),
    ]

    ax1.axhline(13.4, color="#B8B8B8", linestyle="-.", linewidth=1.1, zorder=1)
    ax1.text(2.30, 15.1, "WB 多核串行墙 (~13.4 M/s)", fontsize=8.3, color="#666666", ha="right")

    for label, conds, color, marker, ls, lw, dx in series:
        meds = []
        for i, c in enumerate(conds):
            vals = [r["mops"] for r in by_cond[c]]
            meds.append(statistics.median(vals))
            ax1.scatter([x_pos[i] + dx] * len(vals), vals, color=color, s=20, alpha=0.45, zorder=3)
        ax1.plot(x_pos + dx, meds, color=color, marker=marker, linestyle=ls, linewidth=lw,
                 markersize=7.0 if marker != "x" else 8.0, label=label, zorder=4)

    wb_k1 = statistics.median(r["mops"] for r in by_cond["wb64k_k1_w64"])
    wb1p_k1 = statistics.median(r["mops"] for r in by_cond["wb64k_1p_k1_w64"])
    wb_k2 = statistics.median(r["mops"] for r in by_cond["wb64k_k2_w64"])
    wb1p_k2 = statistics.median(r["mops"] for r in by_cond["wb64k_1p_k2_w64"])
    wb_k4 = statistics.median(r["mops"] for r in by_cond["wb64k_k4_w64"])
    ldar_k1 = statistics.median(r["mops"] for r in by_cond["f4_ish_k1_w64"])
    ldar_k2 = statistics.median(r["mops"] for r in by_cond["f4_ish_k2_w64"])
    ldar_k4 = statistics.median(r["mops"] for r in by_cond["f4_ish_k4_w64"])
    p1_k1 = statistics.median(r["mops"] for r in by_cond["f4_1p_k1_w64"])
    p1_k2 = statistics.median(r["mops"] for r in by_cond["f4_1p_k2_w64"])
    p1_k4_nc = statistics.median(r["mops"] for r in by_cond["nc44_1p_k4_w64"])
    p1_k4_f4 = statistics.median(r["mops"] for r in by_cond["f4_1p_k4_w64"])

    ax1.annotate(f"WB {wb_k1:.1f} / {wb1p_k1:.1f}", (0, wb_k1), xytext=(10, 6), textcoords="offset points",
                 fontsize=8.6, color="#444444")
    ax1.annotate(f"WB 坍缩 {wb_k2:.1f}", (1, wb_k2), xytext=(-32, -18), textcoords="offset points",
                 fontsize=8.6, color="#444444")
    ax1.annotate(f"{wb_k4:.1f}", (2, wb_k4), xytext=(-14, -18), textcoords="offset points",
                 fontsize=8.6, color="#444444")
    ax1.annotate(f"{ldar_k1:.1f}", (0, ldar_k1), xytext=(12, -11), textcoords="offset points",
                 fontsize=8.5, color="#1D6FA5")
    ax1.annotate(f"{ldar_k2:.1f}", (1, ldar_k2), xytext=(12, -4), textcoords="offset points",
                 fontsize=8.8, color="#1D6FA5")
    ax1.annotate(f"{ldar_k4:.1f}", (2, ldar_k4), xytext=(10, -14), textcoords="offset points",
                 fontsize=8.8, color="#1D6FA5")
    ax1.annotate(f"{p1_k1:.1f}\n(单核约WB一半)", (0, p1_k1), xytext=(-28, 22), textcoords="offset points",
                 fontsize=8.4, color="#D62828")
    ax1.annotate(f"{p1_k2:.1f} (+{(p1_k2/wb1p_k2 - 1)*100:.0f}%)", (1, p1_k2), xytext=(-64, 10), textcoords="offset points",
                 fontsize=9.2, color="#D62828")
    ax1.annotate(f"{p1_k4_f4:.1f}–{p1_k4_nc:.1f} M/s\n(0x44 ≡ 0xF4)", (2, p1_k4_nc), xytext=(-92, 7), textcoords="offset points",
                 fontsize=9.0, color="#D62828")

    ax1.set_xticks(x_pos)
    ax1.set_xticklabels(["K = 1 卡核", "K = 2 卡核", "K = 4 卡核"], fontsize=10)
    ax1.set_xlim(-0.32, 2.38)
    ax1.set_ylim(0, 54)
    ax1.set_ylabel("闭环吞吐 (M 消息/秒，64 B 整格校验)", fontsize=10.2)
    ax1.set_title("A. 闭环吞吐随卡核数扩展（W = 64, 批 = 1, 128 B 步长, n = 3）", fontsize=10.5)
    ax1.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=2, fontsize=8.0, frameon=True, framealpha=0.95)

    # Panel B: Hardware PMU proof at K=2, W=64
    pmu_conds = [
        ("Normal-WB (0xFF, ldar)", "wb64k_k2_w64", "#555555"),
        ("MT_NORMAL_NC (0x44, 1-pass)", "nc44_1p_k2_w64", "#2A9D8F"),
        ("Inner-NC, Outer-WB (0xF4, 1-pass)", "f4_1p_k2_w64", "#D62828"),
    ]
    metric_names = ["L1D 填充\nl1d_refill", "L2D 嗅探作废\n0x58 L2D_INVAL", "TAD 总嗅探\ntad_snp_out", "DDR5 穿透读\nDSS_RD / DDR5"]
    x_m = np.arange(len(metric_names))
    w_bar = 0.25

    for idx, (lbl, cond, col) in enumerate(pmu_conds):
        rows = [per_msg_pmu(r) for r in by_cond[cond]]
        vals = [
            statistics.median(x[0] for x in rows),
            statistics.median(x[1] for x in rows),
            statistics.median(x[2] for x in rows),
            statistics.median(x[4] for x in rows),
        ]
        bars = ax2.bar(x_m + (idx - 1) * w_bar, vals, width=w_bar * 0.90, color=col, alpha=0.88, label=lbl, zorder=3)
        for b, v in zip(bars, vals):
            ax2.text(b.get_x() + b.get_width() / 2, v + 0.06, f"{v:.2f}",
                     ha="center", va="bottom", fontsize=8.5,
                     color=col if idx > 0 else "#333333")

    ax2.set_xticks(x_m)
    ax2.set_xticklabels(metric_names, fontsize=9)
    ax2.set_ylim(0, 3.15)
    ax2.set_ylabel("每条消息硬件计数 (次 / 消息，100% 非复用)", fontsize=10.2)
    ax2.set_title("B. K = 2, W = 64 硬件 PMU（零 L1/L2 嗅探 + 零 DDR5 穿透）", fontsize=10.5)
    ax2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=1, fontsize=8.2, frameon=True, framealpha=0.95)

    fig.suptitle(
        "MRV-235：MT_NORMAL_NC (0x44) ≡ Inner-NC, Outer-WB (0xF4) 消除 L1/L2 嗅探并解除多核 13.4 M/s 串行（单核约 WB 一半，零 DDR5 穿透）",
        fontsize=11.0, y=0.975,
    )
    fig.tight_layout(rect=[0, 0.02, 1, 0.935])
    png = mrvplot.HERE / "mrv-235-page-table-breakthrough.png"
    svg = mrvplot.HERE / "mrv-235-page-table-breakthrough.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
