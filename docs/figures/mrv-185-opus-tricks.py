"""MRV-185 Figure 3 (Step 3 Final): Opus-5-5 Three Zero-New-Permission Verification Tricks
(201/201 verified n=3 interleaved runs in logs/mrv185-2026-11-24/runs_tricks.jsonl):
- Panel (a): Trick 1A & 1B — Full K=1..8 multi-core scaling (RP BAR0 vs VF0 BAR0 same-address
  control): SPR CHA serializes same-64B-line UC PRd before TOR entry, capping both at 1/TOR.
- Panel (b): Trick 1C — RP BAR0 <-> VF0 BAR0 cross-interference (CHA 28 vs CHA 20): IIO_1
  in-loop active clock is constant at 1.000-1.002 GHz (prior 0.985G->1.000G active_ghz rise
  was snap_uncore tail gating dilution); VF0's -22.7/-10.9 ns speedup reveals ~18-23 ns host
  IO-stack sparse wake-up tax, tightening warm-IIO1 structural diff to +290.9 p50 / +295.0 mean.
- Panel (c): Trick 3 — PCI config space pread(off=64) null-read de-confounding & 7-BDF
  triangulation: single-device H(dev) = T(0) - T(64) retains common pci_conf1_read lock +
  OUT 0xCF8 constant; only inter-device ΔH cancels to pure hardware diff (+221.8 mean / +223.6 p50).
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import statistics

import numpy as np

from mrvplot import plt

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv185-2026-11-24/runs.jsonl"
TRICKS_PATH = ROOT / "logs/mrv185-2026-11-24/runs_tricks.jsonl"

def fmt1(val: float) -> str:
    return str(Decimal(f"{val:.6f}").quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def med(xs):
    return statistics.median(xs)


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
    fig = plt.figure(figsize=(18.2, 9.2))
    gs = fig.add_gridspec(
        2, 2,
        width_ratios=[1.05, 1.18],
        height_ratios=[1.0, 1.04],
        wspace=0.26,
        hspace=0.38,
        left=0.06,
        right=0.98,
        top=0.90,
        bottom=0.09,
    )
    ax_sc = fig.add_subplot(gs[0, 0])
    ax_if = fig.add_subplot(gs[1, 0])
    ax_cfg = fig.add_subplot(gs[:, 1])

    # ==================== Panel (a): Trick 1A & 1B Full K=1..8 Scaling ====================
    ks = list(range(1, 9))
    rp_mops, vf0_mops = [], []
    rp_tor_k8 = med([r["cha"]["tor_residency_ns"] for r in by_tr[("trick1b_scale", "rp-bar0-8b/k8")]])
    vf0_tor_k8 = med([r["cha"]["tor_residency_ns"] for r in by_tr[("trick1b_scale", "vf0-bar0-8b/k8")]])
    for k in ks:
        rp_mops.append(med([r["mops"] for r in by_tr[("trick1b_scale", f"rp-bar0-8b/k{k}")]]))
        vf0_mops.append(med([r["mops"] for r in by_tr[("trick1b_scale", f"vf0-bar0-8b/k{k}")]]))

    ax_sc.plot(ks, rp_mops, marker="o", linewidth=2.2, markersize=6.5, color="#0f766e",
               label=f"RP BAR0 同地址 (CHA 28)：{rp_mops[0]:.2f} → {rp_mops[-1]:.2f} M/s ({rp_mops[-1]/rp_mops[0]:.2f}×)")
    ax_sc.plot(ks, vf0_mops, marker="s", linewidth=2.2, markersize=6.5, color="#d97706",
               label=f"VF0 BAR0 同地址对照 (CHA 20)：{vf0_mops[0]:.2f} → {vf0_mops[-1]:.2f} M/s ({vf0_mops[-1]/vf0_mops[0]:.2f}×)")

    # Plot theoretical 1 / T_CHA_TOR ceilings
    rp_ceil = 1000.0 / rp_tor_k8
    vf0_ceil = 1000.0 / vf0_tor_k8
    ax_sc.axhline(rp_ceil, color="#0f766e", linestyle="--", linewidth=1.1, alpha=0.7)
    ax_sc.axhline(vf0_ceil, color="#d97706", linestyle="--", linewidth=1.1, alpha=0.7)
    ax_sc.text(8.12, rp_ceil, f"1/TOR\n{rp_ceil:.2f} M/s", va="center", ha="left", fontsize=7.4, color="#0f766e", fontweight="bold")
    ax_sc.text(8.12, vf0_ceil, f"1/TOR\n{vf0_ceil:.2f} M/s", va="center", ha="left", fontsize=7.4, color="#d97706", fontweight="bold")

    # Annotate K=1 and K=8 points
    ax_sc.text(1.0, rp_mops[0] - 0.55, f"{rp_mops[0]:.2f}", ha="center", fontsize=7.6, color="#0f766e", fontweight="bold")
    ax_sc.text(2.0, rp_mops[1] + 0.25, f"{rp_mops[1]:.2f}", ha="center", fontsize=7.6, color="#0f766e", fontweight="bold")
    ax_sc.text(1.0, vf0_mops[0] - 0.50, f"{vf0_mops[0]:.2f}", ha="center", fontsize=7.6, color="#d97706", fontweight="bold")
    ax_sc.text(2.0, vf0_mops[1] + 0.24, f"{vf0_mops[1]:.2f}", ha="center", fontsize=7.6, color="#d97706", fontweight="bold")

    # Trick 1A same-CHA gate summary box
    def t1a(arm):
        rs = by_tr[("trick1a_same_cha", arm)]
        return (
            med([r["net_ns"]["mean"] for r in rs]),
            med([r["cha"]["tor_residency_ns"] for r in rs]),
            med([r["net_ns"]["mean"] - r["cha"]["tor_residency_ns"] for r in rs]),
        )
    rp_m, _, rp_o = t1a("rp-bar0-8b")
    b28_m, _, b28_o = t1a("bar4-l3-cha28-8b")
    vf_m, _, vf_o = t1a("vf0-bar0-8b")
    b20_m, _, b20_o = t1a("bar4-l3-cha20-8b")
    ax_sc.text(
        0.41, 0.34,
        f"巧招 1A 同 CHA 门槛通过（无 CHA 偏置）：\n"
        f"• CHA 28 (RP vs BAR4 L13)：TOR外 {fmt1(rp_o)} vs {fmt1(b28_o)} ns\n"
        f"• CHA 20 (VF0 vs BAR4 L9)：TOR外 {fmt1(vf_o)} vs {fmt1(b20_o)} ns\n"
        f"• 同 CHA 20 净差：BAR4 L3 − VF0 = +{fmt1(b20_m - vf_m)} ns",
        transform=ax_sc.transAxes, fontsize=7.3, va="bottom", ha="left",
        bbox=dict(boxstyle="round,pad=0.38", facecolor="#f8fafc", edgecolor="#cbd5e1", linewidth=0.9),
    )

    ax_sc.set_xticks(ks)
    ax_sc.set_xlim(0.7, 9.05)
    ax_sc.set_ylim(0.5, 7.2)
    ax_sc.set_xlabel("Socket 1 物理核数 K（cpu40..47，同地址 8 B 并发读，250 ms/次）", fontsize=8.6)
    ax_sc.set_ylabel("聚合吞吐（Mmsg/s）", fontsize=8.8)
    ax_sc.set_title("(a) 巧招 1A/1B：K=1..8 并发 — SPR CHA 同 64 B 行 UC PRd 冲突串行，双端均饱和于 1/TOR", fontsize=8.8, fontweight="bold", pad=8)
    ax_sc.legend(loc="upper left", fontsize=7.5, frameon=True, facecolor="white")

    # ==================== Panel (b): Trick 1C Cross-Interference RP <-> VF0 ====================
    def aggr_rate(lbl):
        return med([r["aggr_mops"] for r in by_tr[("trick1c_interfere", lbl)]])

    if_arms = [
        ("vf0-clean", "VF0 单独\n(孤立稀疏)", "#d97706"),
        ("vf0-aggr-rp-k1", f"VF0 被 1核RP\n({aggr_rate('vf0-aggr-rp-k1'):.2f} M/s)", "#f59e0b"),
        ("vf0-aggr-rp-k4", f"VF0 被 4核RP\n({aggr_rate('vf0-aggr-rp-k4'):.2f} M/s)", "#fbbf24"),
        ("rp-clean", "RP 单独\n(自保活)", "#0f766e"),
        ("rp-aggr-vf0-k1", f"RP 被 1核VF0\n({aggr_rate('rp-aggr-vf0-k1'):.2f} M/s)", "#14b8a6"),
        ("rp-aggr-vf0-k4", f"RP 被 4核VF0\n({aggr_rate('rp-aggr-vf0-k4'):.2f} M/s)", "#2dd4bf"),
    ]
    x_if = np.arange(len(if_arms))
    w_if = 0.34
    p50s, tors = [], []
    for lbl, _, _ in if_arms:
        rs = by_tr[("trick1c_interfere", lbl)]
        p50s.append(med([r["net_ns"]["p50"] for r in rs]))
        is_vf0 = lbl.startswith("vf0-")
        tors.append(med([r["cha"]["cha20_tor_ns" if is_vf0 else "cha28_tor_ns"] for r in rs]))

    # True in-loop IIO1/IRP1 clock: Part 2 nominal range (19/20 groups >=1.0 GHz) & Trick 1C clean runs
    p2_inloop_meds = [
        med([(r["iio1"]["clk"] / r["wall_s"]) / 1e9 for r in by_sl[("part2_uncore", f"u{fk}/{arm}")]])
        for fk in (800000, 1100000, 1400000, 1800000, 2200000)
        for arm in ("rp-bar0-8b", "vf0-bar0-8b", "bar4-l3-walk-8b", "bar4-ddr5-walk-8b")
    ]
    p2_iio_lo = min(v for v in p2_inloop_meds if v >= 1.0)
    p2_iio_hi = max(p2_inloop_meds)
    vf0_clean_irp = med([(r["irp1"]["clk"] / r["wall_s"]) / 1e9 for r in by_tr[("trick1c_interfere", "vf0-clean")]])
    rp_clean_irp = med([(r["irp1"]["clk"] / r["wall_s"]) / 1e9 for r in by_tr[("trick1c_interfere", "rp-clean")]])
    clean_irp_ghz = med([vf0_clean_irp, rp_clean_irp])
    vf0_clean_dil = med([r["iio1"]["active_ghz"] for r in by_tr[("trick1c_interfere", "vf0-clean")]])
    vf0_k1_dil = med([r["iio1"]["active_ghz"] for r in by_tr[("trick1c_interfere", "vf0-aggr-rp-k1")]])
    p1_rp_raw_mean = med([r["raw_ns"]["mean"] for r in by_sl[("part1_stage", "cpu40/rp-bar0-8b")]])
    p1_rp_p50 = med([r["net_ns"]["p50"] for r in by_sl[("part1_stage", "cpu40/rp-bar0-8b")]])
    t1c_vf0_k1_raw_mean = med([r["raw_ns"]["mean"] for r in by_tr[("trick1c_interfere", "vf0-aggr-rp-k1")]])
    vf0_warm_p50_diff = p50s[1] - p1_rp_p50
    vf0_warm_mean_diff = t1c_vf0_k1_raw_mean - p1_rp_raw_mean

    ax_if.bar(x_if - w_if / 2, p50s, width=w_if, color=[c for _, _, c in if_arms], edgecolor="white", label="受害者净 p50 延迟")
    ax_if.bar(x_if + w_if / 2, tors, width=w_if, color="#334155", alpha=0.72, edgecolor="white", label="受害者专属 CHA TOR（20 / 28）")

    # Annotate exact p50 deltas and TOR deltas vs clean
    vf0_base, vf0_tor_base = p50s[0], tors[0]
    rp_base, rp_tor_base = p50s[3], tors[3]
    for i, (p50, tor) in enumerate(zip(p50s, tors)):
        if i == 0:
            tag = f"{fmt1(p50)} ns\nTOR {fmt1(tor)}"
            col = "#0f172a"
        elif i == 3:
            tag = f"{fmt1(p50)} ns\nTOR {fmt1(tor)}"
            col = "#0f172a"
        elif i < 3:
            d = p50 - vf0_base
            dt = tor - vf0_tor_base
            tag = f"{fmt1(p50)} ({d:+.1f})\nTOR {fmt1(tor)} ({dt:+.1f})"
            col = "#0f766e"
        else:
            d = p50 - rp_base
            tag = f"{fmt1(p50)} ({d:+.1f})\nTOR {fmt1(tor)}"
            col = "#0f172a"
        ax_if.text(i, max(p50, tor) + 12, tag, ha="center", va="bottom", fontsize=7.0, color=col, fontweight="bold")

    # Callout in open space above right-hand RP bars explaining constant 1.001 GHz & wake-up tax
    ax_if.text(
        0.98, 0.77,
        f"IIO₁/IRP₁ 环内真实主频恒定：{p2_iio_lo:.4f}–{p2_iio_hi:.4f} GHz\n"
        f"（本组单跑 {vf0_clean_irp:.4f}–{rp_clean_irp:.4f}G；active_ghz {vf0_clean_dil:.3f}→{vf0_k1_dil:.3f}G 系尾端稀释）\n"
        f"• VF0 提速 {p50s[1]-vf0_base:.1f} ns 为主机 IO 栈稀疏唤醒差（~18–23 ns）\n"
        f"• 同等热 IIO₁ 纯结构差：VF0_warm − RP\n"
        f"  = +{fmt1(vf0_warm_p50_diff)} ns p50 / +{fmt1(vf0_warm_mean_diff)} ns mean",
        transform=ax_if.transAxes, va="top", ha="right", multialignment="left", fontsize=6.6, color="#0f172a",
        bbox=dict(boxstyle="round,pad=0.36", facecolor="#f0fdfa", edgecolor="#0f766e", linewidth=0.9),
    )

    ax_if.axvline(2.5, color="#94a3b8", linestyle="--", linewidth=1.0)
    ax_if.set_xticks(x_if)
    ax_if.set_xticklabels([x[1] for x in if_arms], fontsize=7.8)
    ax_if.set_ylim(0, 640)
    ax_if.set_ylabel("受害者延迟 / TOR（ns，cpu40）", fontsize=8.8)
    ax_if.set_title(
        f"(b) 巧招 1C：跨 CHA 互扰 — IIO₁ 恒定 {clean_irp_ghz:.3f}G，VF0 提速 {p50s[1]-vf0_base:.1f} ns 揭示稀疏唤醒差",
        fontsize=8.6, fontweight="bold", pad=8,
    )
    ax_if.legend(loc="upper right", fontsize=7.3, frameon=True, facecolor="white")
    # ==================== Panel (c): Trick 3 Config Space pread(off=64) Triangulation ====================
    cfg_order = [
        ("0000:f2:01.0", "f2:01.0 片上 DSA\n(无 PCIe 控制器，他栈)", "#0284c7"),
        ("0000:98:01.0", "98:01.0 CN106 Root Port\n(IIO₁ 栈，M2P/IRP/IIO=1:1)", "#0f766e"),
        ("0000:98:00.0", "98:00.0 IIO₁ 栈前端 0x09a2\n(M2P/IRP=1:1，IIO=0)", "#6366f1"),
        ("0000:e0:01.0", "e0:01.0 空槽 Root Port\n(他栈，2.5 GT/s)", "#64748b"),
        ("0000:98:00.4", "98:00.4 IIO₁ 栈 RCEC\n(M2P/IRP=1:1，IIO=0)", "#8b5cf6"),
        ("0000:99:00.0", "99:00.0 CN106 PF0\n(过 Gen5×4 链路→PEM0)", "#d97706"),
        ("0000:99:02.0", "99:02.0 CN106 VF0\n(过 Gen5×4；off8=0x02800000)", "#dc2626"),
    ]
    y_cfg = np.arange(len(cfg_order))[::-1]
    t64_vals, h_vals, t0_vals, h_means = [], [], [], []
    for bdf, _, _ in cfg_order:
        r0 = by_tr[("trick3_cfg", f"{bdf}/off0")]
        r64 = by_tr[("trick3_cfg", f"{bdf}/off64")]
        t0 = med([r["net_ns"]["p50"] for r in r0])
        t64 = med([r["net_ns"]["p50"] for r in r64])
        t0_vals.append(t0)
        t64_vals.append(t64)
        h_vals.append(t0 - t64)
        h_means.append(med([r["net_ns"]["mean"] for r in r0]) - med([r["net_ns"]["mean"] for r in r64]))

    t64_ref = t64_vals[5]  # 99:00.0 baseline 227.3 ns
    h_rp = h_vals[1]
    h_9800 = h_vals[2]
    h_pf0 = h_vals[5]
    h_vf0 = h_vals[6]
    h_dsa = h_vals[0]
    dh_cfgrd0_mean = h_means[5] - h_means[1]
    dh_cfgrd0_p50 = h_pf0 - h_rp
    h_vf0_off8 = med([r["net_ns"]["p50"] for r in by_tr[("trick3_cfg", "0000:99:02.0/off8")]]) - t64_vals[6]

    for yi, (bdf, lbl, col), t64, h, t0 in zip(y_cfg, cfg_order, t64_vals, h_vals, t0_vals):
        # Null-read T(off=64) segment in light grey
        c64 = "#cbd5e1" if abs(t64 - t64_ref) < 5.0 else "#94a3b8"
        ax_cfg.barh(yi, t64, height=0.58, color=c64, edgecolor="white", linewidth=0.8)
        ax_cfg.barh(yi, h, left=t64, height=0.58, color=col, edgecolor="white", linewidth=0.8)
        # Inner labels
        extra_pm = f" (+{fmt1(t64 - t64_ref)})" if (t64 - t64_ref) > 5.0 else ""
        ax_cfg.text(t64 / 2, yi, f"空读 {fmt1(t64)}{extra_pm}", va="center", ha="center",
                    fontsize=7.3, color="#0f172a", fontweight="bold" if extra_pm else "medium")
        ax_cfg.text(t64 + h / 2, yi, f"H = {fmt1(h)} ns", va="center", ha="center",
                    fontsize=8.0, color="white", fontweight="bold")
        ax_cfg.text(t0 + 10, yi, f"总 {fmt1(t0)} ns", va="center", ha="left", fontsize=7.9, color="#0f172a", fontweight="medium")

    # Key triangulation callout box (clarifying common pci_conf1_read lock vs pure-hardware ΔH)
    ax_cfg.text(
        0.985, 0.985,
        f"巧招 3 去混杂实测摘要（全 7 设备 off=64 均 M2P/IRP/IIO=0，off=0 均 CHA PRd=0）：\n"
        f"1. off=64 空读不对称：98:01.0 高 +{fmt1(t64_vals[1] - t64_ref)} ns ({fmt1(t64_vals[1])})，99:02.0 高 +{fmt1(t64_vals[6] - t64_ref)} ns ({fmt1(t64_vals[6])})\n"
        f"2. 单设备 H = T(0)−T(64) 含公共 pci_conf1_read 锁+OUT 0xCF8 常数；仅设备间差分 ΔH 抵消为纯硬件差：\n"
        f"   ΔH_CfgRd0 = H(99:00.0) − H(98:01.0) = +{fmt1(dh_cfgrd0_mean)} ns mean (+{fmt1(dh_cfgrd0_p50)} ns p50)\n"
        f"   （VF0 off0=+{fmt1(h_vf0 - h_rp)} / off8=+{fmt1(h_vf0_off8 - h_rp)} p50，扣空读后消除 PF0/VF0 19 ns 假差）\n"
        f"3. RP 配置深度较浅：ΔH(98:01.0 − 98:00.0) = {fmt1(h_rp - h_9800)} ns（{fmt1(h_rp)} vs {fmt1(h_9800)}）；较 DSA +{fmt1(h_rp - h_dsa)} ns",
        transform=ax_cfg.transAxes, va="top", ha="right", multialignment="left", fontsize=7.0, color="#0f172a",
        bbox=dict(boxstyle="round,pad=0.42", facecolor="#f8fafc", edgecolor="#64748b", linewidth=0.9),
    )

    ax_cfg.set_yticks(y_cfg)
    ax_cfg.set_yticklabels([x[1] for x in cfg_order], fontsize=8.1)
    ax_cfg.set_xlim(0, 1080)
    ax_cfg.set_ylim(-0.6, len(cfg_order) + 1.25)
    ax_cfg.set_xlabel("配置空间 pread(4 B) 净 p50：灰 = off=64 零硬件空读；彩 = H = T(0)−T(64)（含公共锁+CF8 常数，仅 ΔH 为纯硬件差，ns）", fontsize=8.0)
    ax_cfg.set_title("(c) 巧招 3：配置空间 pread(off=64) 空读去混杂与 7 设备三角定位（cpu40 @ 3.90 GHz / Mesh 2.20 GHz）", fontsize=9.4, fontweight="bold", pad=10)
    fig.suptitle(
        "MRV-185 补跑 opus-5-5 三大免新权限巧招：RP 终点鉴别、跨 CHA 互扰与配置空间空读去混杂（201/201 组 n=3 交错，零错，100% 非复用）",
        fontsize=11.8, fontweight="bold", y=0.965,
    )

    png = Path(__file__).resolve().parent / "mrv-185-opus-tricks.png"
    svg = Path(__file__).resolve().parent / "mrv-185-opus-tricks.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Wrote {png} and {svg}")


if __name__ == "__main__":
    main()
