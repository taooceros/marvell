#!/usr/bin/env python3
"""MRV-231 Figure 1: Control 1 (CN106 on-chip ARM<->ARM TAD snoop rate >=731 M/s)
vs Control 2 (BAR4 slot-15 K=1/2/4 independent sub-ring scaling).

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
            assert r["lost"] == 0, f"lost != 0: {r}"
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

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.2))

    # -------------------------------------------------------------------------
    # Panel A: Control 1 — Pure on-chip ARM <-> ARM TAD total snoop rate
    # -------------------------------------------------------------------------
    ks = [1, 2, 4, 8]
    ring_snp, ring_lo, ring_hi, ring_msg = [], [], [], []
    hot_snp = []
    for k in ks:
        rs = g[("ctrl1_arm_snoop", f"arm_ring_K{k}")]
        assert len(rs) == 3
        snps = [r["tad"]["tad_snp_msh_out_any"]["val"] / 1e6 / (r["elapsed_ns"] / 1e9) for r in rs]
        m, lo, hi = med_mm(snps)
        ring_snp.append(m)
        ring_lo.append(lo)
        ring_hi.append(hi)
        ring_msg.append(statistics.median(r["mops"] for r in rs))

        hs = g[("ctrl1_arm_snoop", f"arm_hotline_K{k}")]
        assert len(hs) == 3
        hot_snp.append(statistics.median(
            r["tad"]["tad_snp_msh_out_any"]["val"] / 1e6 / (r["elapsed_ns"] / 1e9) for r in hs
        ))

    bar4_snp_ref = statistics.median(
        r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / 1e6 / (r["card_pmu"]["elapsed_ns"] / 1e9)
        for r in g[("ctrl2_bar4_scale", "cl_K1_2c")]
    )

    x1 = np.arange(len(ks))
    bars = ax1.bar(
        x1, ring_snp, width=0.52, color="#2563eb", alpha=0.88,
        yerr=[ring_lo, ring_hi], capsize=4, label="片上 ARM⇄ARM 64格环总嗅探"
    )
    ax1.plot(x1, hot_snp, "s--", color="#64748b", lw=1.8, ms=5.5, label="单行对撞总嗅探（参照）")
    ax1.axhline(bar4_snp_ref, color="#dc2626", ls="--", lw=2.0, label=f"BAR4 串行时实际嗅探（{bar4_snp_ref:.1f} M/s）")

    for b, snp, msg in zip(bars, ring_snp, ring_msg):
        ax1.text(
            b.get_x() + b.get_width() / 2, snp + 18,
            f"{snp:.0f} M/s\n({msg:.0f}M包/s)",
            ha="center", va="bottom", fontsize=8.8, fontweight="bold", color="#1e3a8a"
        )

    ax1.annotate(
        f"片上 TAD 实测 ≥ {ring_snp[-1]:.0f} M 嗅探/s\n"
        f"（K=8 仍线性、未触顶，达 BAR4 串行的 {ring_snp[-1]/bar4_snp_ref:.1f}×）",
        xy=(3, ring_snp[-1] * 0.72), xytext=(0.15, 515),
        fontsize=9.0, fontweight="bold", color="#b91c1c",
        bbox=dict(boxstyle="round,pad=0.35", fc="#fef2f2", ec="#dc2626", lw=1.1),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.3),
    )

    ax1.set_xticks(x1)
    ax1.set_xticklabels([f"K={k}\n({2*k} 核)" for k in ks])
    ax1.set_ylabel("卡侧 TAD 总嗅探速率（M 次/秒）")
    ax1.set_ylim(0, 880)
    ax1.set_title("对照 1：片上 ARM⇄ARM 实测 TAD 总嗅探（零 PCIe）\nK=1→8 线性升至 ≥731 M/s（未触顶），排除 TAD 嗅探瓶颈", fontsize=10.0, pad=8)
    ax1.legend(loc="upper left", fontsize=8.2, frameon=True)

    # -------------------------------------------------------------------------
    # Panel B: Control 2 — BAR4 slot 15 K=1/2/4 independent sub-rings
    # -------------------------------------------------------------------------
    c2_specs = [
        ("cl_K1_2c", "K=1\n(1主×2卡)", "#dc2626"),
        ("cl_K2_2c", "K=2\n(2主×4卡)", "#dc2626"),
        ("cl_K4_2c", "K=4\n(4主×8卡)", "#dc2626"),
        ("stripe_K4_1c_W64", "单主条带\nK=4(4卡)", "#ea580c"),
        ("cl_K4_1c", "K=4\n(4主×4卡)", "#f97316"),
    ]
    c2_tot, c2_lo, c2_hi, c2_perl = [], [], [], []
    for cond, _, _ in c2_specs:
        rs = g[("ctrl2_bar4_scale", cond)]
        assert len(rs) == 3
        m, lo, hi = med_mm([r["mops"] for r in rs])
        c2_tot.append(m)
        c2_lo.append(lo)
        c2_hi.append(hi)
        perl = statistics.median(r["per_lane"][0]["mops"] for r in rs)
        c2_perl.append((rs[0]["lanes"], perl))

    nopoll_1h = statistics.median(r["mops"] for r in g[("ctrl2_bar4_scale", "nopoll_K1_1h")])
    nopoll_4h = statistics.median(r["mops"] for r in g[("ctrl2_bar4_scale", "nopoll_K4_4h")])

    x2 = np.arange(len(c2_specs))
    ax2.plot([0, 1, 2], [c2_tot[0], c2_tot[0] * 2, c2_tot[0] * 4], ":o", color="#94a3b8", lw=2.0, ms=5,
             label="若卡在单核/单分片应按 K 线性放大（13→27→54）")

    bars2 = ax2.bar(
        x2, c2_tot, width=0.55,
        color=[c for _, _, c in c2_specs], alpha=0.88,
        yerr=[c2_lo, c2_hi], capsize=4, label="实测 BAR4 总吞吐（闭环 W=64/子环）"
    )
    ax2.axhline(c2_tot[0], color="#991b1b", ls="--", lw=1.4, alpha=0.8)

    for b, tot, (lanes, pl) in zip(bars2, c2_tot, c2_perl):
        lbl = f"{tot:.2f}\n({pl:.2f}×{lanes})" if lanes > 1 else f"{tot:.2f}"
        ax2.text(
            b.get_x() + b.get_width() / 2, tot + 1.5, lbl,
            ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#7f1d1d"
        )

    # Place callout cleanly in upper-left empty space (zero overlap with dotted curve!)
    ax2.text(
        0.03, 0.97,
        f"同槽卡不轮询直推基线：\n"
        f"• 1 主机核：{nopoll_1h:.1f} M/s（12.1×）\n"
        f"• 4 主机核：{nopoll_4h:.1f} M/s（13.1×）\n"
        f"串行态：CHA=1.38 条，TAD 在途=0.02 条",
        transform=ax2.transAxes, ha="left", va="top", fontsize=8.2,
        bbox=dict(boxstyle="round,pad=0.35", fc="#f8fafc", ec="#475569", lw=1.0),
    )

    ax2.set_xticks(x2)
    ax2.set_xticklabels([lbl for _, lbl, _ in c2_specs], fontsize=8.6)
    ax2.set_ylabel("BAR4 总吞吐（M 消息/秒，64 B）")
    ax2.set_ylim(0, 72)
    ax2.set_title("对照 2：BAR4 槽 15 内 K=1/2/4 独立子环（距 960 KiB）\n总吞吐死锁 13.4 M/s（精确 13.4/K）→ 全局共享串行点 [推断: PEM0/IOBN]", fontsize=9.8, pad=8)
    ax2.legend(loc="upper right", fontsize=7.9, frameon=True)

    fig.suptitle(
        "MRV-231：不是片上 TAD 嗅探上限（≥731 M/s），而是 ≥2 卡核追前沿触发的全局共享串行点 [推断: PEM0/IOBN]",
        fontsize=11.0, fontweight="bold", y=0.98
    )
    out = mrvplot.save(fig, "mrv-231-tad-vs-pem0-scaling")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
