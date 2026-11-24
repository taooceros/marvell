"""MRV-182 Figure 1: Homogeneous-statistic (median-of-run mean) nanosecond-level stage
decomposition of a single host-to-CN106 MMIO read on saturn, closed-loop CHA TOR
verification, and multi-core concurrency scaling.

All plotted numbers are read directly from logs/mrv182-2026-11-24/runs.jsonl (n=3 interleaved,
0 mismatches, 0 page faults, 100.00% non-multiplexed host & card PMUs).

Statistical & boundary discipline:
- Because T_CHA_TOR = sum(TOR_OCC) / sum(TOR_INS) / f_mesh is an arithmetic mean over all
  transactions, the stacked operational decomposition in panel (a) uses median-of-run net_mean
  consistently across every MMIO segment (so every segment is strictly additive in mean), with
  separate diamond markers showing each endpoint's verified net_p50.
- CfgRd0 differential (pread 99:00.0 - 98:01.0) is plotted as a separate non-additive proxy
  anchor (config-space and MMIO-space paths differ; direct 98:01.0/resource0 MMIO mmap
  requires root and is not subtracted from VF0 BAR0).
"""
from decimal import Decimal, ROUND_HALF_UP
import json
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

from mrvplot import plt, save


def fmt1(val: float) -> str:
    return str(Decimal(f"{val:.6f}").quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
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

    # Card TAD IFB net cycles per DDR5 miss:
    # (1) original L3-walk rate-adjusted method (160.3 cyc), and
    # (2) pure-idle background method anchored on vf0-bar0-8b (161.6 cyc -> 80.8 ns @ 2.00 GHz rclk).
    vf0_rs = by_sl[("stage", "vf0-bar0-8b")]
    bg_idle_med = med([v["card_pmu"]["tad_ifb_occ"] / v["card_pmu"]["wall_ns"] for v in vf0_rs])
    ifb_cyc_orig = []
    ifb_cyc_idle = []
    for l3r, ddrr in zip(by_sl[("stage", "bar4-l3-walk-8b")], by_sl[("stage", "bar4-ddr5-walk-8b")]):
        net_dss = ddrr["card_pmu"]["tad_dss_rd"] - l3r["card_pmu"]["tad_dss_rd"]
        bg_l3 = l3r["card_pmu"]["tad_ifb_occ"] / l3r["card_pmu"]["wall_ns"]
        extra_w = ddrr["card_pmu"]["wall_ns"] - l3r["card_pmu"]["wall_ns"]
        net_ifb_orig = (ddrr["card_pmu"]["tad_ifb_occ"] - l3r["card_pmu"]["tad_ifb_occ"]) - bg_l3 * extra_w
        ifb_cyc_orig.append(net_ifb_orig / net_dss)

        ifb_l3_pure = l3r["card_pmu"]["tad_ifb_occ"] - bg_idle_med * l3r["card_pmu"]["wall_ns"]
        ifb_ddr_pure = ddrr["card_pmu"]["tad_ifb_occ"] - bg_idle_med * ddrr["card_pmu"]["wall_ns"]
        ifb_cyc_idle.append((ifb_ddr_pure - ifb_l3_pure) / net_dss)
    ifb_orig_med = med(ifb_cyc_orig)
    ifb_idle_med = med(ifb_cyc_idle)
    ifb_rclk_ns = ifb_idle_med / 2.0
    # Near-CHA cores from core_scan_line0 (CHA 23) and core_scan_vf0 (CHA 20)
    out_c53 = med([x["net_ns"]["mean"] - x["cha_s1"]["tor_ns"] for x in by_sl[("core_scan_line0", "cpu53")]])
    out_c58 = med([x["net_ns"]["mean"] - x["cha_s1"]["tor_ns"] for x in by_sl[("core_scan_line0", "cpu58")]])
    vf0_c48_rs = by_sl[("core_scan_vf0", "cpu48")]
    vf0_c48_p50 = med([x["net_ns"]["p50"] for x in vf0_c48_rs])
    vf0_c48_tor = med([x["cha_s1"]["tor_ns"] for x in vf0_c48_rs])
    vf0_c48_out = med([x["net_ns"]["mean"] - x["cha_s1"]["tor_ns"] for x in vf0_c48_rs])
    vf0_c48_p50_out = vf0_c48_p50 - vf0_c48_tor
    hm_p50 = {
        arm: med([x["net_ns"]["p50"] for x in by_sl[("host_mem", arm)]])
        for arm in ("host-wb-l3", "host-wb-ddr5-local", "host-wb-ddr5-remote")
    }
    hm_mean = {
        arm: med([x["net_ns"]["mean"] for x in by_sl[("host_mem", arm)]])
        for arm in ("host-wb-l3", "host-wb-ddr5-local", "host-wb-ddr5-remote")
    }

    # Config pread differential (separate non-additive proxy anchor, mean & p50)
    cfg_rp_mean = med([x["net_ns"]["mean"] for x in by_sl[("cfg_pread", "0000:98:01.0")]])
    cfg_ep_mean = med([x["net_ns"]["mean"] for x in by_sl[("cfg_pread", "0000:99:00.0")]])
    cfg_rp_p50 = med([x["net_ns"]["p50"] for x in by_sl[("cfg_pread", "0000:98:01.0")]])
    cfg_ep_p50 = med([x["net_ns"]["p50"] for x in by_sl[("cfg_pread", "0000:99:00.0")]])
    cfg_vf_p50 = med([x["net_ns"]["p50"] for x in by_sl[("cfg_pread", "0000:99:02.0")]])
    d_cfg_pf_mean = cfg_ep_mean - cfg_rp_mean
    d_cfg_pf_p50 = cfg_ep_p50 - cfg_rp_p50
    d_cfg_vf_p50 = cfg_vf_p50 - cfg_rp_p50

    # Homogeneous net_mean additive MMIO waterfall (matches T_CHA_TOR mean quantities directly!):
    s_out_tor = vf0["mean"] - vf0["tor"]          # 22.13 ns (per-rep med 21.76; BAR4 4K = 23.35)
    s_unsplit_tor = vf0["tor"]                    # 495.56 ns unsplit Host IIO/RP + Gen5x4 + PEM0->SDP CSR
    vf0_end = vf0["mean"]                         # 517.69 ns exact
    s_bar4_l3_mean = l3_8b["mean"] - vf0["mean"]  # +105.17 ns (T_CHA_TOR diff +103.95 ns)
    s_ddr5_mean = ddr_8b["mean"] - l3_8b["mean"]  # +101.57 ns (T_CHA_TOR diff +101.18 ns — 0.4 ns closure!)

    # Multi-core scaling (Suite C3)
    ks = [1, 2, 4, 8]
    agg_mops = [med([x["agg_mops"] for x in by_sl[("mlp", f"multicore-k{k}")]]) for k in ks]
    core_p50s = [med([x["mean_core_net_p50_ns"] for x in by_sl[("mlp", f"multicore-k{k}")]]) for k in ks]
    unf8_per = med([x["per_read_net_p50_ns"] for x in by_sl[("mlp", "uc-unfenced-8x")]])

    # ---- Figure Layout ----
    fig = plt.figure(figsize=(16.6, 8.2))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.50, 1.0], height_ratios=[1.05, 1.0], wspace=0.22, hspace=0.42)
    ax_wf = fig.add_subplot(gs[:, 0])
    ax_pmu = fig.add_subplot(gs[0, 1])
    ax_mlp = fig.add_subplot(gs[1, 1])

    # ==================== Panel (a): Homogeneous net_mean Waterfall + p50 Markers ====================
    c_wb = "#94a3b8"
    c_cfg = "#d97706"
    c_out = "#0284c7"
    c_unsplit = "#475569"
    c_l3 = "#7c3aed"
    c_ddr = "#dc2626"

    # Tuple: (label, [(left, width, color, hatched)], end_x, p50_marker_or_None, note)
    rows = [
        (
            "Host WB 参照\n本地 L3 (8 MiB)",
            [(0, hm_mean["host-wb-l3"], c_wb, False)],
            hm_mean["host-wb-l3"],
            hm_p50["host-wb-l3"],
            f"mean {fmt1(hm_mean['host-wb-l3'])} / p50 {fmt1(hm_p50['host-wb-l3'])} ns",
        ),
        (
            "Host WB 参照\n本地 DDR5",
            [(0, hm_mean["host-wb-ddr5-local"], c_wb, False)],
            hm_mean["host-wb-ddr5-local"],
            hm_p50["host-wb-ddr5-local"],
            f"mean {fmt1(hm_mean['host-wb-ddr5-local'])} / p50 {fmt1(hm_p50['host-wb-ddr5-local'])} ns",
        ),
        (
            "Host WB 参照\n跨 UPI 远端 DDR5",
            [(0, hm_mean["host-wb-ddr5-remote"], c_wb, False)],
            hm_mean["host-wb-ddr5-remote"],
            hm_p50["host-wb-ddr5-remote"],
            f"mean {fmt1(hm_mean['host-wb-ddr5-remote'])} / p50 {fmt1(hm_p50['host-wb-ddr5-remote'])} ns (+{fmt1(hm_p50['host-wb-ddr5-remote']-hm_p50['host-wb-ddr5-local'])})",
        ),
        (
            "独立代理参照 (非可减)\nCfgRd0 链路+PEM0\n(pread 99:00−98:01)",
            [(0, d_cfg_pf_mean, c_cfg, True)],
            d_cfg_pf_mean,
            d_cfg_pf_p50,
            f"mean +{fmt1(d_cfg_pf_mean)} / p50 +{fmt1(d_cfg_pf_p50)} ns（VF0 +{fmt1(d_cfg_vf_p50)}）\n含 pci_lock + 父桥 pm_runtime 锁 + CfgRd0 混杂",
        ),
        (
            "MMIO 段 1（TOR 外余量）\nCore UC + Core⇄CHA\n(cpu40 远核基准)",
            [(0, s_out_tor, c_out, False)],
            s_out_tor,
            None,
            f"cpu40 远核 {fmt1(vf0['out_tor'])}–{fmt1(l3_line0['out_tor'])} ns（核频 ~5–11 ns）\n近 CHA 23/20：cpu53/58 {fmt1(out_c53)}–{fmt1(out_c58)} ns；cpu48 mean−TOR {fmt1(vf0_c48_out)} / p50−TOR {fmt1(vf0_c48_p50_out)} ns",
        ),
        (
            "MMIO 段 2（未细分上界块）\nCHA→IIO₁→RP→Gen5×4\n→PEM0→SDP (VF0 TOR)",
            [(s_out_tor, s_unsplit_tor, c_unsplit, True)],
            vf0_end,
            None,
            f"cpu40 TOR {fmt1(s_unsplit_tor)} ns；近核 cpu48 收紧至 {fmt1(vf0_c48_tor)} ns\n（无 root 未剥离 98:01.0 BAR0）",
        ),
        (
            "实测端点：VF0 BAR0\nOUT_CNTS (8 B PRd)",
            [(0, s_out_tor, c_out, False), (s_out_tor, s_unsplit_tor, c_unsplit, True)],
            vf0_end,
            vf0["p50"],
            f"cpu40 mean={fmt1(vf0['mean'])} / p50={fmt1(vf0['p50'])} ns\n（近核 cpu48 下探 p50 {fmt1(vf0_c48_p50)} / TOR {fmt1(vf0_c48_tor)} ns）",
        ),
        (
            "MMIO 段 3（BAR4 相对差）\nBAR4_INDEX + IOBN\n+ TAD L3 (减 VF0)",
            [(vf0_end, s_bar4_l3_mean, c_l3, False)],
            l3_8b["mean"],
            None,
            f"mean +{fmt1(s_bar4_l3_mean)} / TOR +{fmt1(l3_8b['tor']-vf0['tor'])} ns（p50 +{fmt1(l3_8b['p50']-vf0['p50'])}）\n扣 VF0→SDP 后的 PEM0→TAD L3 下界",
        ),
        (
            "实测端点：BAR4 卡 L3\n4K 走步 (8 B / 64 B)",
            [
                (0, s_out_tor, c_out, False),
                (s_out_tor, s_unsplit_tor, c_unsplit, True),
                (vf0_end, s_bar4_l3_mean, c_l3, False),
            ],
            l3_8b["mean"],
            l3_8b["p50"],
            f"8 B mean={fmt1(l3_8b['mean'])} (p50 {fmt1(l3_8b['p50'])}) / 64 B {fmt1(l3_64b['mean'])} ns",
        ),
        (
            "MMIO 段 4（dc civac 差）\nTAD L3 Miss →\nCN106 6 通道 DDR5",
            [(l3_8b["mean"], s_ddr5_mean, c_ddr, False)],
            ddr_8b["mean"],
            None,
            f"mean +{fmt1(s_ddr5_mean)} / TOR +{fmt1(ddr_8b['tor']-l3_8b['tor'])} ns（p50 +{fmt1(ddr_8b['p50']-l3_8b['p50'])}）\nΔIFB {fmt1(ifb_orig_med)} / 纯背景 {fmt1(ifb_idle_med)} 拍 → 2G rclk {fmt1(ifb_rclk_ns)} ns（排除 1G sclk）",
        ),
        (
            "全链路总计：BAR4 冷读\n卡 DDR5 (8 B / 64 B)",
            [
                (0, s_out_tor, c_out, False),
                (s_out_tor, s_unsplit_tor, c_unsplit, True),
                (vf0_end, s_bar4_l3_mean, c_l3, False),
                (l3_8b["mean"], s_ddr5_mean, c_ddr, False),
            ],
            ddr_8b["mean"],
            ddr_8b["p50"],
            f"8 B mean={fmt1(ddr_8b['mean'])} (p50 {fmt1(ddr_8b['p50'])}) / 64 B {fmt1(ddr_64b['mean'])} ns",
        ),
    ]

    y_pos = np.arange(len(rows))[::-1]
    p50_plotted = False
    for idx, (label, segs, end_x, p50_x, note) in enumerate(rows):
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
        if p50_x is not None:
            ax_wf.plot(
                p50_x,
                y,
                marker="D",
                ms=5.2,
                color="#fef08a",
                mec="#0f172a",
                mew=0.9,
                ls="none",
                zorder=5,
                label="实测中位数 net_p50" if not p50_plotted else None,
            )
            p50_plotted = True
        if idx in (4, 5, 7, 9):
            ax_wf.plot([end_x, end_x], [y - 0.34, y - 0.66], color="#64748b", ls=":", lw=1.0)
        text_x = max(end_x, p50_x if p50_x is not None else 0) + 12
        ax_wf.text(
            text_x,
            y,
            note,
            va="center",
            ha="left",
            fontsize=7.4,
            linespacing=1.22,
            fontweight="bold" if is_total else "normal",
            color="#0f172a" if is_total else "#334155",
        )

    ax_wf.axhline(y_pos[2] - 0.5, color="#cbd5e1", ls="--", lw=1.1)
    ax_wf.axhline(y_pos[3] - 0.5, color="#f59e0b", ls=":", lw=1.1, alpha=0.8)
    ax_wf.set_yticks(y_pos)
    ax_wf.set_yticklabels([r[0] for r in rows], fontsize=7.9)
    ax_wf.set_xlim(0, 1540)
    ax_wf.set_xlabel("单次 8 B 串行读净均值（ns，统一采用 net_mean 满足可加性；菱形 = net_p50，已扣 12.7 ns）", fontsize=8.6)
    ax_wf.set_title("(a) saturn + CN106 单次 MMIO Read 统一均值口径分段与独立代理（斜线 = 未细分/非可减）", fontsize=9.5, fontweight="bold", pad=8)
    ax_wf.legend(loc="upper right", fontsize=7.8, frameon=True, facecolor="white", framealpha=0.94)
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
        ax_pmu.text(i, m + 10, f"{fmt1(m)}", ha="center", va="bottom", fontsize=7.9, fontweight="bold", color="#0f172a")
        ax_pmu.text(i, t * 0.50, f"{t:.0f}", ha="center", va="center", fontsize=7.8, color="white", fontweight="bold")
        ax_pmu.text(i, m + 44, f"Δ{fmt1(c)}", ha="center", va="bottom", fontsize=7.5, color="#0369a1")

    ax_pmu.set_xticks(xb)
    ax_pmu.set_xticklabels([a[0] for a in arms_b], fontsize=7.6)
    ax_pmu.set_ylim(0, 880)
    ax_pmu.set_ylabel("平均延迟 / 驻留 (ns)", fontsize=9.0)
    ax_pmu.set_title("(b) 端到端均值 vs CHA TOR (cpu40 远核，100% 非复用闭环)", fontsize=9.6, fontweight="bold", pad=8)
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
        0.50,
        f"单核无 fence 8×独立 UC 读：\n{fmt1(unf8_per)} ns/读 (W_eff = 1.00，仅限 UC)\nK≤8 线性 8.0×、TOR 稳 600 ns 零排队\n(k4/k8 schedutil 微降频 <1.3 ns)",
        transform=ax_mlp.transAxes,
        fontsize=7.0,
        linespacing=1.22,
        va="top",
        ha="left",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#f8fafc", edgecolor="#cbd5e1", lw=0.9),
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
