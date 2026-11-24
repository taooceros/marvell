"""MRV-182 Figure 1: Rigorous nanosecond-level stage decomposition of a single host-to-CN106
MMIO read on saturn, closed-loop CHA TOR verification, and multi-core concurrency scaling.

All plotted numbers are read directly from logs/mrv182-2026-11-24/runs.jsonl (n=3 interleaved,
0 mismatches, 0 page faults, 100.00% non-multiplexed host & card PMUs).

Boundary discipline:
- CfgRd0 differential (pread 99:00.0 - 98:01.0 = +209.1 ns) is plotted as a separate,
  non-additive proxy anchor (config-space and MMIO-space paths differ; direct 98:01.0/resource0
  MMIO mmap requires root and is not subtracted from VF0 BAR0).
- Inside the MMIO waterfall, every segment boundary is measured on the MMIO path itself:
  outside-TOR residual (net_mean - T_CHA_TOR), unsplit Host IIO/RP + Gen5x4 + PEM0->SDP block
  (T_CHA_TOR of VF0 BAR0 = 495.6 ns), BAR4 L3 increment (+105.5 ns p50 / +104.0 ns TOR), and
  CN106 DDR5 miss increment (+92.7 ns p50 / +101.2 ns TOR).
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv182-2026-11-24/runs.jsonl"


def load_grouped():
    by_sl = defaultdict(list)
    with RUNS_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            by_sl[(r["suite"], r["label"])].append(r)
    return by_sl


def med(vals):
    return statistics.median(vals)


def main():
    by_sl = load_grouped()

    def st(arm):
        rs = by_sl[("stage", arm)]
        return {
            "p50": med([x["net_ns"]["p50"] for x in rs]),
            "mean": med([x["net_ns"]["mean"] for x in rs]),
            "p99": med([x["net_ns"]["p99"] for x in rs]),
            "tor": med([x["cha"]["tor_residency_ns"] for x in rs]),
            "out_tor": med([x["net_ns"]["mean"] - x["cha"]["tor_residency_ns"] for x in rs]),
        }

    vf0 = st("vf0-bar0-8b")
    l3_line0 = st("bar4-l3-line0-8b")
    l3_8b = st("bar4-l3-walk-8b")
    l3_64b = st("bar4-l3-walk-64b")
    ddr_8b = st("bar4-ddr5-walk-8b")
    ddr_64b = st("bar4-ddr5-walk-64b")

    # Card TAD IFB net cycles per DDR5 miss (wall-rate-adjusted vs L3 walk)
    ifb_cyc_8b = []
    for l3r, ddrr in zip(by_sl[("stage", "bar4-l3-walk-8b")], by_sl[("stage", "bar4-ddr5-walk-8b")]):
        bg_rate = l3r["card_pmu"]["tad_ifb_occ"] / l3r["card_pmu"]["wall_ns"]
        extra_w = ddrr["card_pmu"]["wall_ns"] - l3r["card_pmu"]["wall_ns"]
        net_ifb = (ddrr["card_pmu"]["tad_ifb_occ"] - l3r["card_pmu"]["tad_ifb_occ"]) - bg_rate * extra_w
        net_dss = ddrr["card_pmu"]["tad_dss_rd"] - l3r["card_pmu"]["tad_dss_rd"]
        ifb_cyc_8b.append(net_ifb / net_dss)
    ifb_med = med(ifb_cyc_8b)

    # Host WB ladder
    hm = {
        arm: med([x["net_ns"]["p50"] for x in by_sl[("host_mem", arm)]])
        for arm in ("host-wb-l3", "host-wb-ddr5-local", "host-wb-ddr5-remote")
    }

    # Config pread differential (separate non-additive proxy anchor)
    cfg_rp = med([x["net_ns"]["p50"] for x in by_sl[("cfg_pread", "0000:98:01.0")]])
    cfg_ep = med([x["net_ns"]["p50"] for x in by_sl[("cfg_pread", "0000:99:00.0")]])
    cfg_vf = med([x["net_ns"]["p50"] for x in by_sl[("cfg_pread", "0000:99:02.0")]])
    d_cfg_pf = cfg_ep - cfg_rp
    d_cfg_vf = cfg_vf - cfg_rp

    # Strictly identifiable MMIO waterfall segments anchored exactly to endpoint net_p50:
    # VF0 net_p50 = 516.36 ns = (516.36 - 495.56 = 20.80 ns) + T_CHA_TOR (495.56 ns);
    # note net_mean - T_CHA_TOR = 21.76 ns (VF0) / 23.35 ns (BAR4 4K walk 8B).
    s_out_tor_bar = vf0["p50"] - vf0["tor"]  # 20.80 ns so stacked bar ends at exact vf0 p50
    s_unsplit_tor = vf0["tor"]               # 495.56 ns unsplit Host IIO/RP + Gen5x4 + PEM0->SDP CSR
    vf0_end = vf0["p50"]                     # 516.36 ns exact
    s_bar4_l3_p50 = l3_8b["p50"] - vf0["p50"]   # +105.45 ns (T_CHA_TOR diff +103.95 ns)
    s_ddr5_p50 = ddr_8b["p50"] - l3_8b["p50"]   # +92.73 ns (net_mean diff +101.57, T_CHA_TOR diff +101.18 ns)
    # Multi-core scaling (Suite C3)
    ks = [1, 2, 4, 8]
    agg_mops = [med([x["agg_mops"] for x in by_sl[("mlp", f"multicore-k{k}")]]) for k in ks]
    core_p50s = [med([x["mean_core_net_p50_ns"] for x in by_sl[("mlp", f"multicore-k{k}")]]) for k in ks]
    unf8_per = med([x["per_read_net_p50_ns"] for x in by_sl[("mlp", "uc-unfenced-8x")]])

    # ---- Figure Layout ----
    fig = plt.figure(figsize=(15.4, 7.8))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.38, 1.0], height_ratios=[1.05, 1.0], wspace=0.25, hspace=0.42)
    ax_wf = fig.add_subplot(gs[:, 0])
    ax_pmu = fig.add_subplot(gs[0, 1])
    ax_mlp = fig.add_subplot(gs[1, 1])

    # ==================== Panel (a): Rigorous Stage Anchors & Identifiable MMIO Waterfall ====================
    c_wb = "#94a3b8"
    c_cfg = "#d97706"
    c_out = "#0284c7"
    c_unsplit = "#475569"
    c_l3 = "#7c3aed"
    c_ddr = "#dc2626"

    rows = [
        ("Host WB 参照\n本地 L3 (8 MiB)", [(0, hm["host-wb-l3"], c_wb, False)], hm["host-wb-l3"], f"{hm['host-wb-l3']:.1f} ns（纯主机 Core⇄CHA L3）"),
        ("Host WB 参照\n本地 DDR5", [(0, hm["host-wb-ddr5-local"], c_wb, False)], hm["host-wb-ddr5-local"], f"{hm['host-wb-ddr5-local']:.1f} ns（主机本地 IMC）"),
        ("Host WB 参照\n跨 UPI 远端 DDR5", [(0, hm["host-wb-ddr5-remote"], c_wb, False)], hm["host-wb-ddr5-remote"], f"{hm['host-wb-ddr5-remote']:.1f} ns（+{hm['host-wb-ddr5-remote']-hm['host-wb-ddr5-local']:.1f} ns 跨 UPI）"),
        (
            "独立代理参照 (非可减)\nCfgRd0 链路+PEM0\n(pread 99:00−98:01)",
            [(0, d_cfg_pf, c_cfg, True)],
            d_cfg_pf,
            f"+{d_cfg_pf:.1f} ns（VF0 +{d_cfg_vf:.1f}；配置空间独立代理）",
        ),
        (
            "MMIO 段 1（TOR 外余量）\nCore UC + Core⇄CHA\n(net_mean − T_CHA_TOR)",
            [(0, l3_8b["out_tor"], c_out, False)],
            l3_8b["out_tor"],
            f"VF0 {vf0['out_tor']:.1f} / BAR4 {l3_8b['out_tor']:.1f} ns（核频占 ~5–11 ns）",
        ),
        (
            "MMIO 段 2（未细分上界块）\nCHA→IIO₁→RP→Gen5×4\n→PEM0→SDP (VF0 TOR)",
            [(s_out_tor_bar, s_unsplit_tor, c_unsplit, True)],
            vf0_end,
            f"TOR 直测 {s_unsplit_tor:.1f} ns（无 root 未剥离 RP BAR0）",
        ),
        (
            "实测端点：VF0 BAR0\nOUT_CNTS (8 B PRd)",
            [(0, s_out_tor_bar, c_out, False), (s_out_tor_bar, s_unsplit_tor, c_unsplit, True)],
            vf0_end,
            f"p50 = {vf0['p50']:.1f} / mean = {vf0['mean']:.1f} ns",
        ),
        (
            "MMIO 段 3（BAR4 相对差）\nBAR4_INDEX + IOBN\n+ TAD L3 (减 VF0)",
            [(vf0_end, s_bar4_l3_p50, c_l3, False)],
            l3_8b["p50"],
            f"+{s_bar4_l3_p50:.1f} ns p50（TOR 差 +{l3_8b['tor']-vf0['tor']:.1f} ns）",
        ),
        (
            "实测端点：BAR4 卡 L3\n4K 走步 (8 B / 64 B)",
            [
                (0, s_out_tor_bar, c_out, False),
                (s_out_tor_bar, s_unsplit_tor, c_unsplit, True),
                (vf0_end, s_bar4_l3_p50, c_l3, False),
            ],
            l3_8b["p50"],
            f"8 B = {l3_8b['p50']:.1f} / 64 B = {l3_64b['p50']:.1f} ns（p50）",
        ),
        (
            "MMIO 段 4（dc civac 差）\nTAD L3 Miss →\nCN106 6 通道 DDR5",
            [(l3_8b["p50"], s_ddr5_p50, c_ddr, False)],
            ddr_8b["p50"],
            f"+{s_ddr5_p50:.1f} ns p50 / +{ddr_8b['tor']-l3_8b['tor']:.1f} TOR（IFB {ifb_med:.0f} cyc）",
        ),
        (
            "全链路总计：BAR4 冷读\n卡 DDR5 (8 B / 64 B)",
            [
                (0, s_out_tor_bar, c_out, False),
                (s_out_tor_bar, s_unsplit_tor, c_unsplit, True),
                (vf0_end, s_bar4_l3_p50, c_l3, False),
                (l3_8b["p50"], s_ddr5_p50, c_ddr, False),
            ],
            ddr_8b["p50"],
            f"8 B = {ddr_8b['p50']:.1f} / 64 B = {ddr_64b['p50']:.1f} ns（p50）",
        ),
    ]

    y_pos = np.arange(len(rows))[::-1]
    for idx, (label, segs, end_x, note) in enumerate(rows):
        y = y_pos[idx]
        is_total = label.startswith("实测端点") or label.startswith("全链路总计")
        h = 0.62 if is_total else 0.54
        for left, width, color, hatched in segs:
            ax_wf.barh(
                y,
                width,
                left=left,
                height=h,
                color=color,
                edgecolor="white",
                linewidth=0.8,
                alpha=0.90 if is_total else 0.86,
                hatch="///" if hatched else None,
            )
        if idx in (4, 5, 7, 9):
            ax_wf.plot([end_x, end_x], [y - 0.34, y - 0.66], color="#64748b", ls=":", lw=1.0)
        ax_wf.text(
            end_x + 12,
            y,
            note,
            va="center",
            ha="left",
            fontsize=8.1,
            fontweight="bold" if is_total else "normal",
            color="#0f172a" if is_total else "#334155",
        )

    ax_wf.axhline(y_pos[2] - 0.5, color="#cbd5e1", ls="--", lw=1.1)
    ax_wf.axhline(y_pos[3] - 0.5, color="#f59e0b", ls=":", lw=1.1, alpha=0.8)
    ax_wf.set_yticks(y_pos)
    ax_wf.set_yticklabels([r[0] for r in rows], fontsize=8.1)
    ax_wf.set_xlim(0, 1210)
    ax_wf.set_xlabel("单次 8 B 串行读净延迟 / 可辨识段宽（ns，已扣空计时括号 12.7 ns，n = 3 交错中位数）", fontsize=9.0)
    ax_wf.set_title("(a) saturn + CN106 单次 MMIO Read 可辨识分段与独立代理锚点（斜线 = 未细分块 / 非可减代理）", fontsize=9.8, fontweight="bold", pad=8)
    ax_wf.grid(axis="x", alpha=0.35)
    ax_wf.grid(axis="y", visible=False)

    # ==================== Panel (b): End-to-End vs CHA TOR Closure ====================
    arms_b = [
        ("VF0 BAR0\n8 B\n(PRd)", vf0),
        ("BAR4 L3\n首行 8 B\n(PRd)", l3_line0),
        ("BAR4 L3\n4K 8 B\n(PRd)", l3_8b),
        ("BAR4 L3\n4K 64 B\n(UCRdF)", l3_64b),
        ("BAR4 DDR5\n4K 8 B\n(PRd)", ddr_8b),
        ("BAR4 DDR5\n4K 64 B\n(UCRdF)", ddr_64b),
    ]
    xb = np.arange(len(arms_b))
    means = [a[1]["mean"] for a in arms_b]
    tors = [a[1]["tor"] for a in arms_b]
    outs = [a[1]["out_tor"] for a in arms_b]

    ax_pmu.bar(xb, tors, width=0.54, color="#334155", alpha=0.88, label="CHA TOR 硬件驻留 (T_CHA_TOR)")
    ax_pmu.bar(xb, outs, bottom=tors, width=0.54, color="#0284c7", alpha=0.92, label="TOR 外余量 (net_mean − T_CHA_TOR)")
    for i, (m, t, c) in enumerate(zip(means, tors, outs)):
        ax_pmu.text(i, m + 10, f"{m:.1f}", ha="center", va="bottom", fontsize=7.9, fontweight="bold", color="#0f172a")
        ax_pmu.text(i, t * 0.50, f"{t:.0f}", ha="center", va="center", fontsize=7.8, color="white", fontweight="bold")
        ax_pmu.text(i, m + 44, f"Δ{c:.1f}", ha="center", va="bottom", fontsize=7.5, color="#0369a1")

    ax_pmu.set_xticks(xb)
    ax_pmu.set_xticklabels([a[0] for a in arms_b], fontsize=7.6)
    ax_pmu.set_ylim(0, 880)
    ax_pmu.set_ylabel("平均延迟 / 驻留 (ns)", fontsize=9.0)
    ax_pmu.set_title("(b) 端到端均值 vs CHA TOR (PRd / UCRdF) 100% 非复用闭环", fontsize=10.0, fontweight="bold", pad=8)
    ax_pmu.legend(loc="upper left", fontsize=7.6, frameon=True, facecolor="white", framealpha=0.94)
    ax_pmu.grid(axis="y", alpha=0.35)
    ax_pmu.grid(axis="x", visible=False)

    # ==================== Panel (c): Multi-Core Concurrency Scaling ====================
    ax_mlp2 = ax_mlp.twinx()
    ax_mlp2.spines["top"].set_visible(False)

    bar_x = np.arange(len(ks))
    ax_mlp.bar(bar_x, agg_mops, width=0.45, color="#0d9488", alpha=0.86, label="聚合读吞吐 (Mops/s)")
    ideal = [agg_mops[0] * k for k in ks]
    ax_mlp.plot(bar_x, ideal, color="#475569", ls="--", lw=1.2, label="理想线性扩展 (K × 1.54)")

    ax_mlp2.plot(bar_x, core_p50s, color="#dc2626", lw=2.0, marker="o", ms=6.0, label="单核净延迟 p50 (ns)")
    ax_mlp2.set_ylim(420, 770)
    ax_mlp2.set_ylabel("单核净延迟 p50 (ns)", fontsize=8.8, color="#dc2626")
    ax_mlp2.tick_params(axis="y", colors="#dc2626")

    for i, (m, lat) in enumerate(zip(agg_mops, core_p50s)):
        ax_mlp.text(i, m + 0.35, f"{m:.2f}", ha="center", va="bottom", fontsize=8.2, fontweight="bold", color="#0f766e")
        if i == len(ks) - 1:
            ax_mlp2.text(
                i, lat - 20, f"{lat:.1f} ns", ha="center", va="top", fontsize=7.8, color="white",
                bbox=dict(boxstyle="round,pad=0.18", facecolor="#b91c1c", edgecolor="none", alpha=0.92),
            )
        else:
            ax_mlp2.text(i, lat + 14, f"{lat:.1f} ns", ha="center", va="bottom", fontsize=7.8, color="#b91c1c")

    ax_mlp.set_xticks(bar_x)
    ax_mlp.set_xticklabels([f"K = {k} 核\n(Socket 1)" for k in ks], fontsize=8.2)
    ax_mlp.set_ylim(0, 18.0)
    ax_mlp.set_ylabel("BAR4 UC 聚合吞吐 (Mops/s)", fontsize=8.8, color="#0f766e")
    ax_mlp.tick_params(axis="y", colors="#0f766e")
    ax_mlp.set_title("(c) 多核并发 BAR4 UC 读扩展与单核 UC 串行化验证", fontsize=10.0, fontweight="bold", pad=8)
    ax_mlp.grid(axis="y", alpha=0.3)
    ax_mlp.grid(axis="x", visible=False)

    h1, l1 = ax_mlp.get_legend_handles_labels()
    h2, l2 = ax_mlp2.get_legend_handles_labels()
    ax_mlp.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=7.3, frameon=True, facecolor="white", framealpha=0.94)

    ax_mlp.text(
        0.02,
        0.47,
        f"单核无 fence 8×独立 UC 读：\n{unf8_per:.1f} ns/读 (W_eff = 1.00)\n多核 K=8 线性 8.0×、无排队",
        transform=ax_mlp.transAxes,
        fontsize=7.6,
        va="top",
        ha="left",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#f8fafc", edgecolor="#cbd5e1", lw=0.9),
    )

    fig.suptitle(
        "MRV-182 实测：saturn (Xeon 6438M @ 3.90 GHz / Mesh 2.20 GHz) ⇄ CN106 (Gen5×4 / 2.00 GHz) MMIO Read 逐级拆解",
        fontsize=11.8,
        fontweight="bold",
        y=0.985,
    )
    out = save(fig, "mrv-182-stage-waterfall")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
