"""MRV-185 Figure 1 (Step 3 Final): Complete 6-stage nanosecond MMIO Read waterfall
(homogeneous raw_mean - floor_mean, warm-IIO1 vs isolated VF0-RP bound, de-confounded
CfgRd0 differential), triple cross-verification closure, and 100.00% non-multiplexed
M2PCIe_1 (2x counter scale -> 1.00 cyc = 0.45 ns) / IRP_1 (1.00 cyc = 1.00 ns @ 1.001 GHz)
pass-through residency calibration (Part 1 + Trick 2).

All numbers are computed directly from logs/mrv185-2026-11-24/runs.jsonl and runs_tricks.jsonl
(n=3 interleaved, 0 mismatches, 0 page faults, 100.00% non-multiplexed Host + Card PMUs).
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import json
import math
from pathlib import Path
import statistics

from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv185-2026-11-24/runs.jsonl"
TRICKS_PATH = ROOT / "logs/mrv185-2026-11-24/runs_tricks.jsonl"


def fmt1(val: float) -> str:
    return str(Decimal(f"{val:.6f}").quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def med(xs):
    return statistics.median(xs)


def ols(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    se_slope = math.sqrt((ss_res / (n - 2)) / sxx)
    se_int = math.sqrt((ss_res / (n - 2)) * (1.0 / n + mx * mx / sxx))
    return slope, intercept, se_slope, se_int


def load_grouped(path: Path):
    by_sl = defaultdict(list)
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            assert r["mismatches"] == 0, f"mismatches != 0 in {r['label']}"
            assert r["page_faults"] == 0, f"page_faults != 0 in {r['label']}"
            assert r["host_pmu_min_pct"] >= 99.99, f"host mux in {r['label']}"
            assert r["card_pmu"]["min_pct"] >= 99.99, f"card mux in {r['label']}"
            by_sl[(r["suite"], r["label"])].append(r)
    return by_sl


def main():
    by_sl = load_grouped(RUNS_PATH)
    by_tr = load_grouped(TRICKS_PATH)

    def st(arm):
        rs = by_sl[("part1_stage", f"cpu40/{arm}")]
        return {
            "p50": med([x["net_ns"]["p50"] for x in rs]),
            "mean": med([x["net_mean_sub_floor_mean_ns"] for x in rs]),
            "raw_mean": med([x["raw_ns"]["mean"] for x in rs]),
            "tor": med([x["cha"]["tor_residency_ns"] for x in rs]),
            "out": med([x["net_mean_sub_floor_mean_ns"] - x["cha"]["tor_residency_ns"] for x in rs]),
        }

    rp4 = st("rp-bar0-4b")
    rp8 = st("rp-bar0-8b")
    vf0 = st("vf0-bar0-8b")
    l3_8b = st("bar4-l3-walk-8b")
    l3_64b = st("bar4-l3-walk-64b")
    ddr_8b = st("bar4-ddr5-walk-8b")
    ddr_64b = st("bar4-ddr5-walk-64b")

    floor_mean_39 = med([x["floor_ns"]["mean"] for x in by_sl[("part1_stage", "cpu40/rp-bar0-8b")]])
    floor_p50_39 = med([x["floor_ns"]["p50"] for x in by_sl[("part1_stage", "cpu40/rp-bar0-8b")]])

    # Trick 1A same-CHA BAR4 control (CHA 20 = VF0)
    t1a_vf0_mean = med([x["net_ns"]["mean"] for x in by_tr[("trick1a_same_cha", "vf0-bar0-8b")]])
    t1a_cha20_mean = med([x["net_ns"]["mean"] for x in by_tr[("trick1a_same_cha", "bar4-l3-cha20-8b")]])
    same_cha20_delta = t1a_cha20_mean - t1a_vf0_mean

    # Trick 1C warm-IIO1 VF0 - RP structural bound (same-condition raw_mean diff & p50 diff)
    t1c_vf0_k1 = by_tr[("trick1c_interfere", "vf0-aggr-rp-k1")]
    vf0_warm_p50_delta = med([x["net_ns"]["p50"] for x in t1c_vf0_k1]) - rp8["p50"]
    vf0_warm_mean_delta = med([x["raw_ns"]["mean"] for x in t1c_vf0_k1]) - rp8["raw_mean"]

    # Trick 3 de-confounded CfgRd0 pure-hardware differential (common pci_conf1_read lock cancels in ΔH)
    t3_rp0_m = med([x["net_ns"]["mean"] for x in by_tr[("trick3_cfg", "0000:98:01.0/off0")]])
    t3_rp64_m = med([x["net_ns"]["mean"] for x in by_tr[("trick3_cfg", "0000:98:01.0/off64")]])
    t3_pf0_m = med([x["net_ns"]["mean"] for x in by_tr[("trick3_cfg", "0000:99:00.0/off0")]])
    t3_pf64_m = med([x["net_ns"]["mean"] for x in by_tr[("trick3_cfg", "0000:99:00.0/off64")]])
    cfg_clean_delta_mean = (t3_pf0_m - t3_pf64_m) - (t3_rp0_m - t3_rp64_m)
    t3_rp0_p = med([x["net_ns"]["p50"] for x in by_tr[("trick3_cfg", "0000:98:01.0/off0")]])
    t3_rp64_p = med([x["net_ns"]["p50"] for x in by_tr[("trick3_cfg", "0000:98:01.0/off64")]])
    t3_pf0_p = med([x["net_ns"]["p50"] for x in by_tr[("trick3_cfg", "0000:99:00.0/off0")]])
    t3_pf64_p = med([x["net_ns"]["p50"] for x in by_tr[("trick3_cfg", "0000:99:00.0/off64")]])
    cfg_clean_delta_p50 = (t3_pf0_p - t3_pf64_p) - (t3_rp0_p - t3_rp64_p)

    # Part 2 OLS regressions (1/f_uncore, homogeneous net_mean_sub_floor_mean_ns) across 5 freqs x 3 reps
    p2_fits = {}
    for arm in ("rp-bar0-8b", "vf0-bar0-8b", "bar4-l3-walk-8b", "bar4-ddr5-walk-8b"):
        xs, y_mean, y_tor, y_out = [], [], [], []
        for fk in (800000, 1100000, 1400000, 1800000, 2200000):
            for r in by_sl[("part2_uncore", f"u{fk}/{arm}")]:
                xs.append(1.0 / r["mesh_ghz"])
                y_mean.append(r["net_mean_sub_floor_mean_ns"])
                y_tor.append(r["cha"]["tor_residency_ns"])
                y_out.append(r["net_mean_sub_floor_mean_ns"] - r["cha"]["tor_residency_ns"])
        p2_fits[arm] = {
            "mean": ols(xs, y_mean),
            "tor": ols(xs, y_tor),
            "out": ols(xs, y_out),
        }

    # Part 3 OLS regressions (1/f_core, homogeneous net_mean_sub_floor_mean_ns) across 4 freqs x 3 reps
    p3_fits = {}
    for arm in ("rp-bar0-8b", "vf0-bar0-8b", "bar4-l3-walk-8b"):
        xs, y_out = [], []
        for fk in (1500000, 2200000, 3000000, 3900000):
            for r in by_sl[("part3_core", f"c{fk}/{arm}")]:
                xs.append(1.0 / r["aperf_mperf_ghz"])
                y_out.append(r["net_mean_sub_floor_mean_ns"] - r["cha"]["tor_residency_ns"])
        p3_fits[arm] = ols(xs, y_out)

    # Max two-way Part 2 <-> Part 3 closure across all 3 endpoints
    max_p23_closure = max(
        max(
            abs(p3_fits[arm][0] / 3.9 - p2_fits[arm]["out"][1]),
            abs(p2_fits[arm]["out"][0] / 2.2 - p3_fits[arm][1]),
        )
        for arm in ("rp-bar0-8b", "vf0-bar0-8b", "bar4-l3-walk-8b")
    )

    # Homogeneous additive 6-stage breakdown (8 B, cpu40 @ 3.9/2.2 GHz)
    s1_core_rp = p3_fits["rp-bar0-8b"][0] / 3.9
    s2_mesh_out_rp = rp8["out"] - s1_core_rp
    s3_mesh_tor_rp = p2_fits["rp-bar0-8b"]["tor"][0] / 2.2
    s4_iio_rp = rp8["tor"] - s3_mesh_tor_rp
    s5_link_sdp_bound = vf0["mean"] - rp8["mean"]
    s6a_iobn_l3 = l3_8b["mean"] - vf0["mean"]
    s6b_ddr5 = ddr_8b["mean"] - l3_8b["mean"]
    # ---- Figure Layout ----
    fig = plt.figure(figsize=(18.0, 9.4))
    gs = fig.add_gridspec(
        2, 2,
        width_ratios=[1.48, 1.0],
        height_ratios=[1.06, 1.0],
        wspace=0.22,
        hspace=0.44,
        left=0.13,
        right=0.98,
        top=0.90,
        bottom=0.16,
    )
    ax_wf = fig.add_subplot(gs[:, 0])
    ax_cc = fig.add_subplot(gs[0, 1])
    ax_irp = fig.add_subplot(gs[1, 1])

    # ==================== Panel (a): 6-Stage Additive Waterfall ====================
    c_s1 = "#0369a1"   # Stage 1: Core UC (3.9 GHz)
    c_s2 = "#38bdf8"   # Stage 2: Core<->CHA Mesh (2.2 GHz, outside TOR)
    c_s3 = "#0f766e"   # Stage 3: CHA TOR<->M2PCIe1 Mesh (2.2 GHz)
    c_s4 = "#14b8a6"   # Stage 4: IRP1 + IIO1 (1.0 GHz) + RP 98:01.0 reg
    c_s5 = "#d97706"   # Stage 5: Host TL/DLL/PHY + Link + PEM0->SDP - RP reg
    c_s6a = "#7c3aed"  # Stage 6a: BAR4_INDEX + IOBN + TAD (48 MiB L3)
    c_s6b = "#dc2626"  # Stage 6b: TAD L3 Miss -> 6-ch DDR5
    c_old = "#64748b"  # MRV-182 old unsplit 495 ns block

    def segs_rp(total_mean, tor, s3):
        out = total_mean - tor
        s1 = s1_core_rp
        s2 = max(0.0, out - s1)
        s4 = max(0.0, tor - s3)
        return [
            (0.0, s1, c_s1, ""),
            (s1, s2, c_s2, ""),
            (s1 + s2, s3, c_s3, ""),
            (s1 + s2 + s3, s4, c_s4, ""),
        ]

    rp_end = rp8["mean"]
    vf0_end = vf0["mean"]
    l3_end = l3_8b["mean"]
    ddr_end = ddr_8b["mean"]

    rows = [
        (
            "MRV-182 旧基线\nVF0 BAR0 8 B\n（495 ns 黑盒未劈）",
            [
                (0.0, vf0["out"], "#0284c7", ""),
                (vf0["out"], vf0["tor"], c_old, "///"),
            ],
            vf0_end,
            vf0["p50"],
            f"mean {fmt1(vf0_end)} / p50 {fmt1(vf0['p50'])} ns",
            [(vf0["out"] + vf0["tor"] / 2, f"旧未拆分块 {fmt1(vf0['tor'])} ns", "white", 8.8)],
        ),
        (
            "端点 A：片上 RP BAR0\n98:01.0 resource0 4 B\n（片上 RP 内部掉头）",
            segs_rp(rp4["mean"], rp4["tor"], s3_mesh_tor_rp),
            rp4["mean"],
            rp4["p50"],
            f"mean {fmt1(rp4['mean'])} / p50 {fmt1(rp4['p50'])} ns",
            [(rp4["out"] + s3_mesh_tor_rp + (rp4["tor"] - s3_mesh_tor_rp) / 2, f"IIO+RP\n{fmt1(rp4['tor'] - s3_mesh_tor_rp)}", "white", 7.8)],
        ),
        (
            "端点 A：片上 RP BAR0\n98:01.0 resource0 8 B\n（Host+RP 锚点，CHA 28）",
            segs_rp(rp8["mean"], rp8["tor"], s3_mesh_tor_rp),
            rp_end,
            rp8["p50"],
            f"mean {fmt1(rp_end)} / p50 {fmt1(rp8['p50'])} ns  (TOR {fmt1(rp8['tor'])})",
            [
                (rp8["out"] + s3_mesh_tor_rp / 2, f"{fmt1(s3_mesh_tor_rp)}", "white", 7.8),
                (rp8["out"] + s3_mesh_tor_rp + s4_iio_rp / 2, f"IIO+RP\n{fmt1(s4_iio_rp)}", "white", 7.8),
            ],
        ),
        (
            "端点 B：CN106 VF0\nBAR0 OUT_CNTS 8 B\n（CHA 20，劈开 495 ns）",
            segs_rp(rp8["mean"], rp8["tor"], s3_mesh_tor_rp)
            + [(rp_end, s5_link_sdp_bound, c_s5, "")],
            vf0_end,
            vf0["p50"],
            f"mean {fmt1(vf0_end)} / p50 {fmt1(vf0['p50'])} ns  (VF0−RP +{fmt1(s5_link_sdp_bound)})",
            [
                (rp_end + s5_link_sdp_bound / 2, f"TL/DLL/PHY + 链路 + PEM0→SDP\n− RP 寄存器：+{fmt1(s5_link_sdp_bound)} ns", "white", 7.7),
            ],
        ),
        (
            "端点 C：BAR4 L3 热读\n4K 走步 8 B (PRd)\n（+BAR4_INDEX+IOBN+TAD）",
            segs_rp(rp8["mean"], rp8["tor"], s3_mesh_tor_rp)
            + [
                (rp_end, s5_link_sdp_bound, c_s5, ""),
                (vf0_end, s6a_iobn_l3, c_s6a, ""),
            ],
            l3_end,
            l3_8b["p50"],
            f"mean {fmt1(l3_end)} / p50 {fmt1(l3_8b['p50'])} ns  (同CHA20 +{fmt1(same_cha20_delta)}; 64B {fmt1(l3_64b['p50'])})",
            [
                (rp_end + s5_link_sdp_bound / 2, f"+{fmt1(s5_link_sdp_bound)}", "white", 8.5),
                (vf0_end + s6a_iobn_l3 / 2, f"+{fmt1(s6a_iobn_l3)}", "white", 8.5),
            ],
        ),
        (
            "端点 D：BAR4 DDR5 冷读\n4K 走步 8 B (dc civac)\n（完整 6 段全链路分解）",
            segs_rp(rp8["mean"], rp8["tor"], s3_mesh_tor_rp)
            + [
                (rp_end, s5_link_sdp_bound, c_s5, ""),
                (vf0_end, s6a_iobn_l3, c_s6a, ""),
                (l3_end, s6b_ddr5, c_s6b, ""),
            ],
            ddr_end,
            ddr_8b["p50"],
            f"mean {fmt1(ddr_end)} / p50 {fmt1(ddr_8b['p50'])} ns  (64B p50 {fmt1(ddr_64b['p50'])})",
            [
                (rp_end + s5_link_sdp_bound / 2, f"+{fmt1(s5_link_sdp_bound)}", "white", 8.5),
                (vf0_end + s6a_iobn_l3 / 2, f"+{fmt1(s6a_iobn_l3)}", "white", 8.5),
                (l3_end + s6b_ddr5 / 2, f"+{fmt1(s6b_ddr5)}", "white", 8.5),
            ],
        ),
    ]

    ys = np.arange(len(rows))[::-1]
    bar_h = 0.56
    for y, (_, segs, end_x, p50_x, note, inner_labels) in zip(ys, rows):
        for left, width, color, hatch in segs:
            ax_wf.barh(
                y, width, left=left, height=bar_h, color=color,
                edgecolor="white", linewidth=0.8, hatch=hatch if hatch else None,
            )
        ax_wf.plot(p50_x, y, marker="D", markersize=5.5, color="#0f172a", markeredgecolor="white", markeredgewidth=0.8, zorder=6)
        ax_wf.text(end_x + 10, y, note, va="center", ha="left", fontsize=8.3, color="#0f172a", fontweight="medium")
        for lx, ltxt, lcol, lfs in inner_labels:
            bbox_kw = dict(boxstyle="round,pad=0.22", facecolor="#334155", edgecolor="none", alpha=0.88) if "旧未拆分块" in ltxt else None
            ax_wf.text(lx, y, ltxt, va="center", ha="center", fontsize=lfs, color=lcol, fontweight="bold", bbox=bbox_kw)

    # Vertical dashed guide at RP 98:01.0 Host+RP anchor (205.9 ns)
    ax_wf.axvline(rp_end, color="#0f766e", linestyle="--", linewidth=1.1, alpha=0.78)
    ax_wf.text(rp_end + 8, len(rows) - 0.36, f"RP 98:01.0 锚点 {fmt1(rp_end)} ns", fontsize=8.4, color="#0f766e", fontweight="bold", va="bottom")

    # Callout for warm-IIO1 bound & Trick 3 de-confounded pure-hardware CfgRd0 differential
    # Placed in the wide-open right whitespace beside the shorter RP rows (zero overlap!)
    ax_wf.text(
        0.985, 0.79,
        f"段 5 物理边界与巧招 3 独立交叉界：\n"
        f"• 孤立态 VF0−RP (+{fmt1(s5_link_sdp_bound)} ns) 含 ~18–23 ns 稀疏唤醒差\n"
        f"• 同等热 IIO₁ 下纯结构差：+{fmt1(vf0_warm_p50_delta)} p50 / +{fmt1(vf0_warm_mean_delta)} mean ns\n"
        f"• 巧招 3 纯硬件 CfgRd0 差分 ΔH(99:00.0−98:01.0) =\n"
        f"  +{fmt1(cfg_clean_delta_mean)} mean / +{fmt1(cfg_clean_delta_p50)} p50 ns",
        transform=ax_wf.transAxes, va="top", ha="right", multialignment="left", fontsize=7.4, color="#334155",
        bbox=dict(boxstyle="round,pad=0.40", facecolor="#fffbeb", edgecolor="#f59e0b", linewidth=0.9),
    )
    ax_wf.set_yticks(ys)
    ax_wf.set_yticklabels([r[0] for r in rows], fontsize=8.6)
    ax_wf.set_xlim(0, 1085)
    ax_wf.set_ylim(-0.55, len(rows) - 0.15)
    ax_wf.set_xlabel(
        f"单次串行 MMIO Read 净延迟（ns，均值扣 floor_mean {fmt1(floor_mean_39)} ns / p50 扣 floor_p50 {fmt1(floor_p50_39)} ns；柱 = 均值，◆ = 净 p50）",
        fontsize=8.6,
    )
    ax_wf.set_title("(a) 劈开 495 ns 黑盒：完整 6 段 MMIO Read 瀑布分解（cpu40 @ 3.90 GHz / Mesh 2.20 GHz）", fontsize=10.4, fontweight="bold", pad=10)

    # Legend cleanly placed BELOW Panel (a) x-axis (ZERO overlap with bottom bar!)
    legend_handles = [
        Patch(facecolor=c_s1, label=f"段 1：Host Core UC (3.9G, {fmt1(s1_core_rp)} ns)"),
        Patch(facecolor=c_s2, label=f"段 2：Core⇄CHA Mesh (2.2G, {fmt1(s2_mesh_out_rp)} ns)"),
        Patch(facecolor=c_s3, label=f"段 3：CHA TOR⇄M2P Mesh (2.2G, {fmt1(s3_mesh_tor_rp)} ns)"),
        Patch(facecolor=c_s4, label=f"段 4：IRP₁+IIO₁(1.001G)+RP 寄存器 ({fmt1(s4_iio_rp)} ns)"),
        Patch(facecolor=c_s5, label=f"段 5：VF0−RP 孤立 (+{fmt1(s5_link_sdp_bound)} ns；热 IIO₁ +{fmt1(vf0_warm_p50_delta)} p50)"),
        Patch(facecolor=c_s6a, label=f"段 6a：BAR4_INDEX+IOBN+TAD L3 (+{fmt1(s6a_iobn_l3)} ns; 同CHA20 +{fmt1(same_cha20_delta)})"),
        Patch(facecolor=c_s6b, label=f"段 6b：TAD Miss→卡 DDR5 (+{fmt1(s6b_ddr5)} ns)"),
        Line2D([0], [0], marker="D", color="none", markerfacecolor="#0f172a", markeredgecolor="white", markersize=6, label="净 p50 锚点"),
    ]
    ax_wf.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.48, -0.105),
        fontsize=7.6,
        frameon=True,
        facecolor="white",
        edgecolor="#cbd5e1",
        ncol=2,
        columnspacing=1.2,
    )

    # ==================== Panel (b): Triple Cross-Verification Closure ====================
    stages_cc = [
        ("段1\nCore UC", p3_fits["rp-bar0-8b"][0] / 3.9, p2_fits["rp-bar0-8b"]["out"][1]),
        ("段2\nCore⇄CHA", p2_fits["rp-bar0-8b"]["out"][0] / 2.2, p3_fits["rp-bar0-8b"][1]),
        ("段3\nCHA⇄M2P", p2_fits["rp-bar0-8b"]["tor"][0] / 2.2, p2_fits["bar4-l3-walk-8b"]["tor"][0] / 2.2),
        ("段4\nIIO+RP", rp8["tor"] - p2_fits["rp-bar0-8b"]["tor"][0] / 2.2, p2_fits["rp-bar0-8b"]["tor"][1]),
        ("段5\nVF0−RP界", vf0["mean"] - rp8["mean"], p2_fits["vf0-bar0-8b"]["tor"][1] - p2_fits["rp-bar0-8b"]["tor"][1]),
        ("段6a\nBAR4 L3", l3_8b["mean"] - vf0["mean"], p2_fits["bar4-l3-walk-8b"]["tor"][1] - p2_fits["vf0-bar0-8b"]["tor"][1]),
        ("段6b\nTAD→DDR5", ddr_8b["mean"] - l3_8b["mean"], p2_fits["bar4-ddr5-walk-8b"]["tor"][1] - p2_fits["bar4-l3-walk-8b"]["tor"][1]),
    ]
    x_cc = np.arange(len(stages_cc))
    w_cc = 0.36
    v_m1 = [s[1] for s in stages_cc]
    v_m2 = [s[2] for s in stages_cc]
    deltas = [abs(a - b) for a, b in zip(v_m1, v_m2)]
    max_delta = max(deltas)

    ax_cc.bar(x_cc - w_cc / 2, v_m1, width=w_cc, color="#0284c7", label="主路实测（端点差分 / 扫频斜率）")
    ax_cc.bar(x_cc + w_cc / 2, v_m2, width=w_cc, color="#f59e0b", label="独立交叉核验（扫频截距 / 另一路径）")
    for xi, a, b, diff in zip(x_cc, v_m1, v_m2, deltas):
        ax_cc.text(xi, max(a, b) + 6, f"Δ{fmt1(diff)}", ha="center", va="bottom", fontsize=7.8, color="#334155", fontweight="bold")
    ax_cc.set_xticks(x_cc)
    ax_cc.set_xticklabels([s[0] for s in stages_cc], fontsize=8.0)
    ax_cc.set_ylim(0, 368)
    ax_cc.set_ylabel("分段耗时（ns）", fontsize=8.8)
    ax_cc.set_title(
        f"(b) 三路交叉闭环（同口径 floor_mean：段 1/2 全三端 ≤ {max_p23_closure:.2f} ns，全 6 段 ≤ {fmt1(max_delta)} ns）",
        fontsize=9.2, fontweight="bold", pad=8,
    )
    ax_cc.legend(loc="upper left", fontsize=7.8, frameon=True, facecolor="white")

    # ==================== Panel (c): Trick 2 + Part 1 Host IO-Stack Hop Residency ====================
    irp_arms = [
        ("RP BAR0\n(8 B)", "rp-bar0-8b"),
        ("VF0 BAR0\n(8 B)", "vf0-bar0-8b"),
        ("BAR4 L3\n(4K 8 B)", "bar4-l3-walk-8b"),
        ("BAR4 DDR5\n(4K 8 B)", "bar4-ddr5-walk-8b"),
    ]
    x_irp = np.arange(len(irp_arms))
    w_i = 0.15
    vals_m2p_ne = []
    vals_irp_txs_ins = []
    vals_irp_txc_ncs = []
    vals_irp_txs_occ = []
    vals_irp_drs_m2p23 = []
    m2p_scale_ratios = []
    irp_true_ghzs = []
    for _, arm in irp_arms:
        r_st = by_sl[("part1_stage", f"cpu40/{arm}")]
        r_h3 = by_tr[("trick2_hops", f"hop3/{arm}")]
        r_h5 = by_tr[("trick2_hops", f"hop5/{arm}")]
        r_h1 = by_tr[("trick2_hops", f"hop1/{arm}")]
        vals_m2p_ne.append(med([r["m2pcie1"]["cha_ncs_ne"] / r["rounds"] for r in r_st]))
        vals_irp_txs_ins.append(med([r["io_bank"]["irp_per_op"]["txs_ins_ncs"] for r in r_h5]))
        vals_irp_txc_ncs.append(med([r["io_bank"]["irp_per_op"]["txc_bl_ncs_occ"] for r in r_h3]))
        vals_irp_txs_occ.append(med([r["irp1"]["c0"] / r["rounds"] for r in r_st]))
        vals_irp_drs_m2p23.append(
            med([r["io_bank"]["irp_per_op"]["txc_bl_drs_occ"] for r in r_h1])
            + med([
                r["io_bank"]["m2p_per_op"]["txc_ne_pmm_distress_0"]
                + r["io_bank"]["m2p_per_op"]["txc_ne_pmm_distress_1"]
                for r in r_h5
            ])
        )
        m2p_scale_ratios.extend([r["io_bank"]["m2p"]["rxc_ne_all"] / r["m2pcie1"]["clk"] for r in r_h3])
        irp_true_ghzs.extend([(r["irp1"]["clk"] / r["wall_s"]) / 1e9 for r in r_st])

    m2p_scale = med(m2p_scale_ratios)
    m2p_cnt_med = med(vals_m2p_ne)
    m2p_true_cyc = m2p_cnt_med / m2p_scale
    m2p_true_ns = m2p_true_cyc / 2.2
    irp_occ_med = med(vals_irp_txc_ncs)
    irp_ghz_med = med(irp_true_ghzs)
    irp_true_ns = irp_occ_med / irp_ghz_med

    ax_irp.bar(
        x_irp - 2.0 * w_i, vals_m2p_ne, width=w_i, color="#0f766e",
        label=f"M2P₁ 0x10 去程 ({m2p_cnt_med:.2f} 计 ÷ {m2p_scale:.0f}× = {m2p_true_cyc:.2f} 拍 = {m2p_true_ns:.2f} ns)",
    )
    ax_irp.bar(x_irp - 1.0 * w_i, vals_irp_txs_ins, width=w_i, color="#0284c7", label="IRP₁ 去程 txs_ins_ncs (0x0e, 1.00×)")
    ax_irp.bar(
        x_irp + 0.0 * w_i, vals_irp_txc_ncs, width=w_i, color="#7c3aed",
        label=f"IRP₁ BL NCS txc_occ (0x0a, {irp_occ_med:.2f} 拍 = {irp_true_ns:.2f} ns @ {irp_ghz_med:.3f}G)",
    )
    ax_irp.bar(x_irp + 1.0 * w_i, vals_irp_txs_occ, width=w_i, color="#dc2626", label="IRP₁ txs_req_occ (0x0c ≈ 0)")
    ax_irp.bar(x_irp + 2.0 * w_i, vals_irp_drs_m2p23, width=w_i, color="#94a3b8", label="IRP₁ txc_drs & M2P 0x23 (u=0x08/0x80 = 0)")

    for xi, vne in zip(x_irp, vals_m2p_ne):
        ax_irp.text(xi - 2.0 * w_i, vne + 0.06, f"{vne:.2f}", ha="center", va="bottom", fontsize=7.2, color="#0f766e", fontweight="bold")
    for xi, v0c in zip(x_irp, vals_irp_txs_occ):
        ax_irp.text(xi + 1.25 * w_i, v0c + 0.06, f"{v0c:.3f}", ha="center", va="bottom", fontsize=6.7, color="#dc2626", fontweight="bold")

    ax_irp.axhline(1.0, color="#64748b", linestyle=":", linewidth=0.9)
    ax_irp.axhline(2.0, color="#0f766e", linestyle=":", linewidth=0.9, alpha=0.6)
    ax_irp.set_xticks(x_irp)
    ax_irp.set_xticklabels([a[0] for a in irp_arms], fontsize=8.2)
    ax_irp.set_ylim(0, 3.45)
    ax_irp.set_ylabel("每笔 MMIO 读的队列计数 / 驻留拍", fontsize=8.6)
    ax_irp.set_title(
        f"(c) 巧招 2 + IRP₁：M2P₁ 0x10 ({m2p_scale:.0f}×倍率→{m2p_true_ns:.2f} ns) 与 IRP₁ BL ({irp_true_ns:.2f} ns) 为纯过站",
        fontsize=8.6, fontweight="bold", pad=8,
    )
    ax_irp.legend(loc="upper center", fontsize=6.4, frameon=True, facecolor="white", ncol=2, columnspacing=0.6)
    fig.suptitle(
        "MRV-185 劈开 495 ns 黑盒：6 段完整 MMIO Read 纳秒分解、三路交叉闭环与片内过站标定（n=3 交错，零错，100% 非复用）",
        fontsize=12.0, fontweight="bold", y=0.965,
    )

    # Save directly without tight_layout overriding explicit gridspec margins.
    png = Path(__file__).resolve().parent / "mrv-185-six-stage-waterfall.png"
    svg = Path(__file__).resolve().parent / "mrv-185-six-stage-waterfall.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Wrote {png} and {svg}")


if __name__ == "__main__":
    main()
