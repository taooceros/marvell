#!/usr/bin/env python3
"""MRV-231 Figure 2: Control 3 (ARM card read-ordering relaxation ldar vs ldr vs
ldr+dmb vs ldnp) and frontier-polling trigger contrast (1c 42.3 M/s vs >=2c 13.4 M/s).

All values plotted directly from logs/mrv231-2026-11-27/runs.jsonl (n=3 medians,
min-max error bars, verified 72/72 sent==done, lost=0, corrupt=0, partial=0,
PMU >=99.9%).
"""
import collections
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv231-2026-11-27/runs.jsonl"


def load_groups():
    groups = collections.defaultdict(list)
    total = 0
    with RUNS.open() as f:
        for line in f:
            r = json.loads(line)
            total += 1
            assert r["lost"] == 0
            if r["exp"] == "ctrl1_arm_snoop":
                assert r["corrupt"] == 0 and r["min_pct"] >= 99.9
                if r["pattern"] == "ring":
                    for pp in r["per_pair"]:
                        assert pp["sent_tot"] == pp["taken_tot"] and pp["corrupt"] == 0
            else:
                cp = r["card_pmu"]
                assert r["host_min_pct"] >= 99.9 and cp["min_pct"] >= 99.9
                assert sum(w["corrupt"] for w in cp["workers"]) == 0
                assert sum(w["partial"] for w in cp["workers"]) == 0
                for pl in r["per_lane"]:
                    assert pl["sent_tot"] == pl["done_tot"] and pl["lost"] == 0
            groups[(r["exp"], r["cond"])].append(r)
    assert total == 72, f"expected 72 runs, got {total}"
    return groups


def med_mm(vals):
    m = statistics.median(vals)
    return m, m - min(vals), max(vals) - m


def main():
    g = load_groups()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.1))

    # -------------------------------------------------------------------------
    # Panel A: Control 3 — Card read-ordering relaxation on exact 13.4 M/s anchor
    # -------------------------------------------------------------------------
    c3_specs = [
        ("order_ldar", "ldar\n(Acquire 基线)", "#dc2626"),
        ("order_ldr", "ldr\n(纯弱序、零屏障)", "#2563eb"),
        ("order_ldr_dmb", "ldr + dmb ishld\n(命中后轻屏障)", "#0284c7"),
        ("order_ldnp", "ldnp + dmb ishld\n(非临时提示)", "#0d9488"),
    ]
    mops_med, mops_lo, mops_hi, snp_pm = [], [], [], []
    for cond, _, _ in c3_specs:
        rs = g[("ctrl3_read_order", cond)]
        assert len(rs) == 3
        m, lo, hi = med_mm([r["mops"] for r in rs])
        mops_med.append(m)
        mops_lo.append(lo)
        mops_hi.append(hi)
        s_per_m = [
            r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"]
            / sum(w["taken_win"] for w in r["card_pmu"]["workers"])
            for r in rs
        ]
        snp_pm.append(statistics.median(s_per_m))

    x1 = np.arange(len(c3_specs))
    bars = ax1.bar(
        x1, mops_med, width=0.52,
        color=[c for _, _, c in c3_specs], alpha=0.88,
        yerr=[mops_lo, mops_hi], capsize=4
    )
    ax1.axhline(mops_med[0], color="#991b1b", ls="--", lw=1.4, alpha=0.75)

    for b, m, sp in zip(bars, mops_med, snp_pm):
        ax1.text(
            b.get_x() + b.get_width() / 2, m + 0.55,
            f"{m:.2f} M/s\n({sp:.2f} 嗅探/包)",
            ha="center", va="bottom", fontsize=8.7, fontweight="bold", color="#1e293b"
        )

    ax1.text(
        0.50, 0.94,
        "四档几乎完全重合（13.43–13.47 M/s，极差 0.3%）\n"
        "普通 ldr / ldnp 仍把轮询行拉入私有 L1D/L2，照样触发 ~3.2 次/包嗅探",
        transform=ax1.transAxes, ha="center", va="top", fontsize=8.4, fontweight="bold",
        color="#7f1d1d",
        bbox=dict(boxstyle="round,pad=0.35", fc="#fef2f2", ec="#dc2626", lw=1.0),
    )

    ax1.set_xticks(x1)
    ax1.set_xticklabels([lbl for _, lbl, _ in c3_specs], fontsize=8.6)
    ax1.set_ylabel("闭环吞吐（M 消息/秒，K=1, 2卡核, W=64）")
    ax1.set_ylim(0, 22)
    ax1.set_title("对照 3：卡侧读序松弛（同槽单变量对照）\n放宽 ARM 读序（ldar → ldr / ldnp）无法打破串行", fontsize=10.0, pad=8)

    # -------------------------------------------------------------------------
    # Panel B: 1c vs >=2c trigger contrast + single-host cpu40 3.9 GHz cross-check
    # -------------------------------------------------------------------------
    comp_specs = [
        ("cl_K1_1c", "K=1\n1主×1卡", "#059669"),
        ("stripe_K2_1c", "单主条带\nK=2(2卡)", "#0284c7"),
        ("cl_K2_1c", "K=2\n2主×2卡", "#ea580c"),
        ("cl_K4_1c", "K=4\n4主×4卡", "#ea580c"),
        ("stripe_K4_1c_W64", "单主条带\nK=4(4卡)", "#dc2626"),
        ("cl_K4_2c", "K=4\n4主×8卡", "#dc2626"),
    ]
    tot_v, emp_v, tinf_v = [], [], []
    for cond, _, _ in comp_specs:
        rs = g[("ctrl2_bar4_scale", cond)]
        tot_v.append(statistics.median(r["mops"] for r in rs))
        emp_v.append(statistics.median(
            sum(w["empty_win"] for w in r["card_pmu"]["workers"]) / 1e6 / (r["card_pmu"]["elapsed_ns"] / 1e9)
            for r in rs
        ))
        tinf_v.append(statistics.median(
            r["card_pmu"]["tad"]["tad_req_occ"]["val"] / (r["card_pmu"]["elapsed_ns"] / 1e9 * 2.0e9)
            for r in rs
        ))

    x2 = np.arange(len(comp_specs))
    bars2 = ax2.bar(x2, tot_v, width=0.55, color=[c for _, _, c in comp_specs], alpha=0.88, label="BAR4 总吞吐（M/s）")
    ax2.axhline(13.42, color="#991b1b", ls="--", lw=1.4, alpha=0.75, label="全局共享串行平台（~13.4 M/s）")

    for b, m, emp, ti in zip(bars2, tot_v, emp_v, tinf_v):
        ax2.text(
            b.get_x() + b.get_width() / 2, m + 1.0,
            f"{m:.1f}\n空轮询 {emp:.0f}M\nTAD {ti:.2f}",
            ha="center", va="bottom", fontsize=7.6, color="#1e293b"
        )

    ax2.set_xticks(x2)
    ax2.set_xticklabels([lbl for _, lbl, _ in comp_specs], fontsize=8.2)
    ax2.set_ylabel("BAR4 总吞吐（M 消息/秒，闭环 W=64）")
    ax2.set_ylim(0, 58)
    ax2.set_title("触发相变：1卡核未追前沿 42.3 M/s，≥2卡核空轮询即锁死 13.4 M/s\n（单主机核 cpu40 3.9 GHz 条带亦锁 13.4 M/s，排除主机核频干扰）", fontsize=9.7, pad=8)
    ax2.legend(loc="upper right", fontsize=8.1, frameon=True)

    fig.suptitle(
        "MRV-231：ARM 弱序读无效，≥2 卡核紧轮询写前沿即锁入 ~13.4 M/s 全局串行（TAD 在途反跌 7.5×）",
        fontsize=11.0, fontweight="bold", y=0.98
    )
    out = mrvplot.save(fig, "mrv-231-read-order-and-summary")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
