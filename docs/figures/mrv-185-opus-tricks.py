"""MRV-185 Figure 3: Opus-5-5 Three Zero-New-Permission Verification Tricks
(201/201 verified n=3 interleaved runs in logs/mrv185-2026-11-24/runs_tricks.jsonl):
- Panel (a): Trick 1A & 1B — Full K=1..8 multi-core scaling (RP BAR0 vs VF0 BAR0 same-address
  control): both saturate near 1/T_CHA_TOR under ordered same-address reads, plus same-CHA BAR4 gate.
- Panel (b): Trick 1C — RP BAR0 <-> VF0 BAR0 cross-interference (CHA 28 vs CHA 20): no material
  cross-endpoint penalty on RP (+0.9 ns), while VF0 speeds up by -22.7/-10.9 ns concurrently
  with IIO_1 active clock rising 0.985 -> 1.000 GHz.
- Panel (c): Trick 3 — PCI config space pread(off=64) null-read de-confounding & 7-BDF triangulation,
  measuring off=64 kernel/PM asymmetry (+17.3 ns on 98:01.0, +20.9 ns on 99:02.0), shallow RP
  config depth (H(98:01.0)-H(98:00.0) = -29.1 ns), and cross-link CfgRd0 differential
  (+223.6 ns p50 / +221.8 ns mean).
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import statistics

import numpy as np

from mrvplot import plt

ROOT = Path(__file__).resolve().parents[2]
TRICKS_PATH = ROOT / "logs/mrv185-2026-11-24/runs_tricks.jsonl"


def fmt1(val: float) -> str:
    return str(Decimal(f"{val:.6f}").quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def med(xs):
    return statistics.median(xs)


def load_tricks():
    by_sl = defaultdict(list)
    with open(TRICKS_PATH) as f:
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
    by_tr = load_tricks()

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
    ax_sc.set_title("(a) 巧招 1A/1B：K=1..8 并发扩展 — RP 与 VF0 同地址均饱和于 1/TOR（同址串行一致）", fontsize=9.2, fontweight="bold", pad=8)
    ax_sc.legend(loc="upper left", fontsize=7.5, frameon=True, facecolor="white")

    # ==================== Panel (b): Trick 1C Cross-Interference RP <-> VF0 ====================
    if_arms = [
        ("vf0-clean", "VF0 单独\n(无干扰)", "#d97706"),
        ("vf0-aggr-rp-k1", "VF0 被 1核RP\n(4.42 M/s)", "#f59e0b"),
        ("vf0-aggr-rp-k4", "VF0 被 4核RP\n(5.63 M/s)", "#fbbf24"),
        ("rp-clean", "RP 单独\n(无干扰)", "#0f766e"),
        ("rp-aggr-vf0-k1", "RP 被 1核VF0\n(1.86 M/s)", "#14b8a6"),
        ("rp-aggr-vf0-k4", "RP 被 4核VF0\n(2.06 M/s)", "#2dd4bf"),
    ]
    x_if = np.arange(len(if_arms))
    w_if = 0.34
    p50s, tors, iios = [], [], []
    for lbl, _, _ in if_arms:
        rs = by_tr[("trick1c_interfere", lbl)]
        p50s.append(med([r["net_ns"]["p50"] for r in rs]))
        is_vf0 = lbl.startswith("vf0-")
        tors.append(med([r["cha"]["cha20_tor_ns" if is_vf0 else "cha28_tor_ns"] for r in rs]))
        iios.append(med([r["iio1"]["active_ghz"] for r in rs]))

    bars_p50 = ax_if.bar(x_if - w_if / 2, p50s, width=w_if, color=[c for _, _, c in if_arms], edgecolor="white", label="受害者净 p50 延迟")
    bars_tor = ax_if.bar(x_if + w_if / 2, tors, width=w_if, color="#334155", alpha=0.72, edgecolor="white", label="受害者专属 CHA TOR（20 / 28）")

    # Annotate exact deltas vs clean and IIO clock
    vf0_base = p50s[0]
    rp_base = p50s[3]
    for i, (p50, tor, iio) in enumerate(zip(p50s, tors, iios)):
        if i == 0 or i == 3:
            tag = f"{fmt1(p50)} ns\n(IIO {iio:.3f}G)"
            col = "#0f172a"
        elif i < 3:
            d = p50 - vf0_base
            tag = f"{fmt1(p50)} ({d:+.1f})\nIIO {iio:.4f}G"
            col = "#0f766e"
        else:
            d = p50 - rp_base
            tag = f"{fmt1(p50)} ({d:+.1f})\nIIO {iio:.4f}G"
            col = "#0f172a"
        ax_if.text(i, max(p50, tor) + 12, tag, ha="center", va="bottom", fontsize=7.2, color=col, fontweight="bold")

    ax_if.axvline(2.5, color="#94a3b8", linestyle="--", linewidth=1.0)
    ax_if.set_xticks(x_if)
    ax_if.set_xticklabels([x[1] for x in if_arms], fontsize=7.8)
    ax_if.set_ylim(0, 625)
    ax_if.set_ylabel("受害者延迟 / TOR（ns，cpu40）", fontsize=8.8)
    ax_if.set_title("(b) 巧招 1C：跨 CHA 互扰（RP⇄VF0）— 无有害跨端退化，VF0 快 22.7 ns 且 IIO₁ 升至 1.000G", fontsize=8.9, fontweight="bold", pad=8)
    ax_if.legend(loc="upper right", fontsize=7.4, frameon=True, facecolor="white")

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
    t64_vals, h_vals, t0_vals = [], [], []
    for bdf, _, _ in cfg_order:
        t0 = med([r["net_ns"]["p50"] for r in by_tr[("trick3_cfg", f"{bdf}/off0")]])
        t64 = med([r["net_ns"]["p50"] for r in by_tr[("trick3_cfg", f"{bdf}/off64")]])
        t0_vals.append(t0)
        t64_vals.append(t64)
        h_vals.append(t0 - t64)

    t64_ref = t64_vals[5]  # 99:00.0 baseline 227.3 ns
    h_rp = h_vals[1]
    h_9800 = h_vals[2]
    h_pf0 = h_vals[5]
    h_vf0 = h_vals[6]
    h_dsa = h_vals[0]
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
        ax_cfg.text(t64 + h / 2, yi, f"硬件 H = {fmt1(h)} ns", va="center", ha="center",
                    fontsize=8.0, color="white", fontweight="bold")
        ax_cfg.text(t0 + 10, yi, f"总 {fmt1(t0)} ns", va="center", ha="left", fontsize=7.9, color="#0f172a", fontweight="medium")

    # Key triangulation callout box
    ax_cfg.text(
        0.985, 0.98,
        f"巧招 3 去混杂实测摘要（全 7 设备 off=64 均 M2P/IRP/IIO=0，off=0 均 CHA PRd=0）：\n"
        f"1. off=64 空读不对称：98:01.0 高 +{fmt1(t64_vals[1] - t64_ref)} ns，99:02.0 高 +{fmt1(t64_vals[6] - t64_ref)} ns\n"
        f"2. 去混杂跨链路 CfgRd0 差分：H(99:00.0) − H(98:01.0) = +{fmt1(h_pf0 - h_rp)} ns p50\n"
        f"   （VF0 off0=+{fmt1(h_vf0 - h_rp)} / off8=+{fmt1(h_vf0_off8 - h_rp)} ns，扣空读后 PF0 与 VF0 消除 19 ns 假差）\n"
        f"3. RP 配置深度较浅：H(98:01.0) 比同栈 98:00.0 低 {fmt1(h_rp - h_9800)} ns，比片上 DSA 高 +{fmt1(h_rp - h_dsa)} ns",
        transform=ax_cfg.transAxes, va="top", ha="right", fontsize=7.5, color="#0f172a",
        bbox=dict(boxstyle="round,pad=0.45", facecolor="#f8fafc", edgecolor="#64748b", linewidth=0.9),
    )

    ax_cfg.set_yticks(y_cfg)
    ax_cfg.set_yticklabels([x[1] for x in cfg_order], fontsize=8.1)
    ax_cfg.set_xlim(0, 1080)
    ax_cfg.set_ylim(-0.6, len(cfg_order) + 0.95)
    ax_cfg.set_xlabel("单次配置空间 pread(4 B) 净 p50 延迟分解：灰色 = off=64 零硬件空读；彩色 = 纯硬件 H = T(0) − T(64)（ns）", fontsize=8.5)
    ax_cfg.set_title("(c) 巧招 3：配置空间 pread(off=64) 零硬件空读去混杂与 7 设备三角定位（cpu40 @ 3.90 GHz / Mesh 2.20 GHz）", fontsize=9.6, fontweight="bold", pad=10)

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
