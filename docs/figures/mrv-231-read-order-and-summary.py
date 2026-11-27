#!/usr/bin/env python3
"""MRV-231 Figure 2: Control 3 (Normal WB ARM read-instruction relaxation ldar vs ldr vs
ldr+dmb vs ldnp; page-table Normal-NC / Inner-NC Outer-WB untested) and frontier-polling
trigger contrast (cl_K1_1c 42.25 M/s, stripe_K2_1c 20.43 M/s self-throttling, and
cl_K4_1c / stripe_K4_1c cross-ring HoL interlock at 3.35-3.46 M/s per ring).

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


def active_l1d_per_pkt(r):
    cp = r["card_pmu"]
    tot_taken = sum(w["taken_win"] for w in cp["workers"])
    active_cpus = set(range(4, 4 + len(cp["workers"])))
    l1d = sum(c["l1d_cache_refill"] for c in cp["core_pmu"] if c["cpu"] in active_cpus)
    return l1d / tot_taken


def main():
    g = load_groups()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.6))

    # -------------------------------------------------------------------------
    # Panel A: Control 3 — Normal WB ARM read-instruction relaxation
    # -------------------------------------------------------------------------
    c3_specs = [
        ("order_ldar", "ldar\n(Acquire 基线)", "#dc2626"),
        ("order_ldr", "ldr\n(纯弱序、零屏障)", "#2563eb"),
        ("order_ldr_dmb", "ldr + dmb ishld\n(命中后轻屏障)", "#0284c7"),
        ("order_ldnp", "ldnp + dmb ishld\n(非临时提示)", "#0d9488"),
    ]
    mops_med, mops_lo, mops_hi, snp_pm, l1d_pm = [], [], [], [], []
    for cond, _, _ in c3_specs:
        rs = g[("ctrl3_read_order", cond)]
        assert len(rs) == 3
        m, lo, hi = med_mm([r["mops"] for r in rs])
        mops_med.append(m)
        mops_lo.append(lo)
        mops_hi.append(hi)
        snp_pm.append(statistics.median(
            r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"]
            / sum(w["taken_win"] for w in r["card_pmu"]["workers"])
            for r in rs
        ))
        l1d_pm.append(statistics.median(active_l1d_per_pkt(r) for r in rs))

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
            ha="center", va="bottom", fontsize=8.6, fontweight="bold", color="#1e293b"
        )

    spread_pct = (max(mops_med) - min(mops_med)) / mops_med[0] * 100.0
    ax1.text(
        0.50, 0.96,
        f"Normal WB 页四档几乎重合（{min(mops_med):.2f}–{max(mops_med):.2f} M/s，极差 {spread_pct:.1f}%）\n"
        f"普通 ldr / ldnp 照常分配 L1D（{min(l1d_pm):.2f}–{max(l1d_pm):.2f} 重填/包，~3.2 嗅探/包）\n"
        f"注：页表级 Normal-NC / Inner-NC Outer-WB 本次未测",
        transform=ax1.transAxes, ha="center", va="top", fontsize=8.2, fontweight="bold",
        color="#7f1d1d",
        bbox=dict(boxstyle="round,pad=0.38", fc="#fef2f2", ec="#dc2626", lw=1.0),
    )

    ax1.set_xticks(x1)
    ax1.set_xticklabels([lbl for _, lbl, _ in c3_specs], fontsize=8.5)
    ax1.set_ylabel("闭环吞吐（M 消息/秒，Normal WB, K=1, 2卡核, W=64）")
    ax1.set_ylim(0, 24.5)
    ax1.set_title(
        "对照 3：Normal WB 读指令松弛对照（槽 15 同条件）\n"
        "指令级 ldar→ldr/ldnp 无效（页表级 Normal-NC / Inner-NC Outer-WB 未测）",
        fontsize=9.5, pad=8,
    )

    # -------------------------------------------------------------------------
    # Panel B: Frontier-polling trigger contrast, stripe_K2_1c self-throttling,
    # and cl_K4_1c / stripe_K4_1c cross-ring HoL interlock
    # -------------------------------------------------------------------------
    comp_specs = [
        ("cl_K1_1c", "K=1\n1主×1卡", "#059669"),
        ("stripe_K2_1c", "单主条带\nK=2(2卡)", "#0284c7"),
        ("cl_K2_1c", "K=2\n2主×2卡", "#ea580c"),
        ("cl_K4_1c", "K=4\n4主×4卡", "#ea580c"),
        ("stripe_K4_1c_W64", "单主条带\nK=4(4卡)", "#dc2626"),
        ("cl_K4_2c", "K=4\n4主×8卡", "#dc2626"),
    ]
    tot_v, emp_v, tinf_v, burst_v, cha_v, snp_v, pl_v = [], [], [], [], [], [], []
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
        burst_v.append(statistics.median(
            sum(w["taken_win"] for w in r["card_pmu"]["workers"])
            / sum(w["busy_win"] for w in r["card_pmu"]["workers"])
            for r in rs
        ))
        cha_v.append(statistics.median(r["host_pmu"]["cha_inflight"] for r in rs))
        snp_v.append(statistics.median(
            r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"]
            / sum(w["taken_win"] for w in r["card_pmu"]["workers"])
            for r in rs
        ))
        pl_v.append(statistics.median(r["per_lane"][0]["mops"] for r in rs))

    serial_ref = statistics.median(r["mops"] for r in g[("ctrl2_bar4_scale", "cl_K1_2c")])
    x2 = np.arange(len(comp_specs))
    bars2 = ax2.bar(
        x2, tot_v, width=0.55,
        color=[c for _, _, c in comp_specs], alpha=0.88,
        label="BAR4 总吞吐（M/s）"
    )
    ax2.axhline(
        serial_ref, color="#991b1b", ls="--", lw=1.4, alpha=0.75,
        label=f"槽 15 共享串行平台（~{serial_ref:.1f} M/s）"
    )

    for b, m, emp, ti in zip(bars2, tot_v, emp_v, tinf_v):
        ax2.text(
            b.get_x() + b.get_width() / 2, m + 0.9,
            f"{m:.2f}\n空轮询 {emp:.0f}M\nTAD {ti:.2f}",
            ha="center", va="bottom", fontsize=7.4, fontweight="bold", color="#1e293b"
        )

    # Annotation 1: cl_K1_1c = 42.25 M/s cold-slot landing mechanism
    ax2.annotate(
        f"cl_K1_1c = {tot_v[0]:.2f} M/s：单核忙于回包\n"
        f"忙批 {burst_v[0]:.1f} 包冷落盘（{snp_v[0]:.2f} 嗅探/包）",
        xy=(0.28, 39.5), xytext=(0.88, 51.8),
        fontsize=7.9, fontweight="bold", color="#065f46",
        bbox=dict(boxstyle="round,pad=0.32", fc="#ecfdf5", ec="#059669", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#059669", lw=1.25),
    )

    # Annotation 2: stripe_K2_1c = 20.43 M/s host self-throttling
    ax2.annotate(
        f"stripe_K2_1c = {tot_v[1]:.2f} M/s：\n"
        f"主机逐包轮询回复环自节流\n"
        f"（CHA={cha_v[1]:.2f}，忙批 {burst_v[1]:.2f}，{snp_v[1]:.2f} 嗅探/包）",
        xy=(1.15, 27.6), xytext=(1.60, 38.6),
        fontsize=7.8, fontweight="bold", color="#075985",
        bbox=dict(boxstyle="round,pad=0.32", fc="#f0f9ff", ec="#0284c7", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#0284c7", lw=1.25),
    )

    # Annotation 3: cl_K4_1c / stripe_K4_1c cross-ring HoL interlock
    ax2.annotate(
        f"cl_K4_1c / stripe_K4_1c 每环仍仅 1 卡核：\n"
        f"跨环队头阻塞互锁至 {pl_v[4]:.2f}–{pl_v[3]:.2f} M/s/环\n"
        f"（忙批塌缩至 {burst_v[4]:.2f}–{burst_v[3]:.2f}，坐实共享入向队列）",
        xy=(3.55, 21.0), xytext=(2.25, 26.8),
        fontsize=7.7, fontweight="bold", color="#9a3412",
        bbox=dict(boxstyle="round,pad=0.32", fc="#fff7ed", ec="#ea580c", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#ea580c", lw=1.25),
    )

    ax2.set_xticks(x2)
    ax2.set_xticklabels([lbl for _, lbl, _ in comp_specs], fontsize=8.2)
    ax2.set_ylabel("BAR4 总吞吐（M 消息/秒，闭环 W=64）")
    ax2.set_ylim(0, 65)
    ax2.set_title(
        "触发相变与跨环互锁：单核冷落盘 42.25 M/s，紧追前沿无节流锁入 13.4 M/s\n"
        "（单主机核 cpu40 3.9 GHz 条带 K=4 亦锁 13.4 M/s，排除主机核频干扰）",
        fontsize=9.4, pad=8,
    )
    ax2.legend(loc="upper right", fontsize=7.9, frameon=True)

    fig.suptitle(
        f"MRV-231：Normal WB 读指令松弛无效（页表 NC 未测），紧追前沿无节流即锁入槽 15 共享 ~{serial_ref:.1f} M/s 串行（TAD 在途由 {tinf_v[0]:.2f} 跌至 {tinf_v[-1]:.2f} 条）",
        fontsize=10.1, fontweight="bold", y=0.985,
    )
    out = mrvplot.save(fig, "mrv-231-read-order-and-summary")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
