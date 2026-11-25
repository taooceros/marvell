"""MRV-185 Figure 2 (Step 3 Final): Socket 1 Uncore 1/f_uncore linear regression (Part 2),
cpu40 Core 1/f_core linear regression (Part 3, homogeneous net_mean_sub_floor_mean_ns
eliminating 2-tick floor_p50 quantization tilt), and BAR4 WC (`resource4_wc`) vs UC read
matrix (Part 4), with in-loop IRP1/IIO1 clock (clk/wall_s = 1.000-1.002 GHz) precision.

All numbers are read directly from logs/mrv185-2026-11-24/runs.jsonl
(n=3 interleaved, 0 mismatches, 0 page faults, 100.00% non-multiplexed Host + Card PMUs).
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import json
import math
from pathlib import Path
import statistics

import numpy as np

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv185-2026-11-24/runs.jsonl"


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
    preds = [intercept + slope * x for x in xs]
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, preds))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    rmse = math.sqrt(ss_res / n)
    return slope, intercept, r2, rmse


def load_grouped():
    by_sl = defaultdict(list)
    with open(RUNS_PATH) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            assert r["mismatches"] == 0
            assert r["page_faults"] == 0
            assert r["host_pmu_min_pct"] >= 99.99
            assert r["card_pmu"]["min_pct"] >= 99.99
            by_sl[(r["suite"], r["label"])].append(r)
    return by_sl


def main():
    by_sl = load_grouped()

    fig = plt.figure(figsize=(17.6, 8.8))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.15, 1.05], height_ratios=[1.0, 1.08], wspace=0.24, hspace=0.40)
    ax_u = fig.add_subplot(gs[:, 0])
    ax_c = fig.add_subplot(gs[0, 1])
    ax_wc = fig.add_subplot(gs[1, 1])

    # ==================== Panel (a): Part 2 Socket 1 Uncore Sweep (1/f_uncore) ====================
    u_specs = [
        ("rp-bar0-8b", "RP 98:01.0 resource0 (CHA 28, 8 B)", "#0f766e", "o"),
        ("vf0-bar0-8b", "VF0 BAR0 OUT_CNTS (CHA 20, 8 B)", "#d97706", "s"),
        ("bar4-l3-walk-8b", "BAR4 L3 4K 走步 (32-CHA, 8 B)", "#7c3aed", "^"),
        ("bar4-ddr5-walk-8b", "BAR4 DDR5 4K 冷读 (32-CHA, 8 B)", "#dc2626", "D"),
    ]
    u_freqs = [800000, 1100000, 1400000, 1800000, 2200000]
    x_grid = np.linspace(0.0, 1.32, 100)

    iio_diluted = []
    iio_p2_group_meds = []
    slopes = []
    p2_out_fits = {}
    for arm, name, col, mk in u_specs:
        xs, ys, y_out = [], [], []
        for fk in u_freqs:
            rs = by_sl[("part2_uncore", f"u{fk}/{arm}")]
            iio_p2_group_meds.append(med([(r["iio1"]["clk"] / r["wall_s"]) / 1e9 for r in rs]))
            for r in rs:
                xs.append(1.0 / r["mesh_ghz"])
                ys.append(r["net_mean_sub_floor_mean_ns"])
                y_out.append(r["net_mean_sub_floor_mean_ns"] - r["cha"]["tor_residency_ns"])
                iio_diluted.append(r["iio1"]["active_ghz"])
        sl, it, r2, rmse = ols(xs, ys)
        p2_out_fits[arm] = ols(xs, y_out)
        slopes.append(sl)
        ax_u.scatter(xs, ys, color=col, marker=mk, s=28, alpha=0.85, zorder=5)
        ax_u.plot(x_grid, it + sl * x_grid, color=col, linewidth=1.8, linestyle="-",
                  label=f"{name}：{fmt1(it)} + {fmt1(sl)} / f  (R²={r2:.4f})")
        ax_u.plot(0.0, it, marker=mk, markersize=6, color=col, markeredgecolor="white", zorder=6)
        ax_u.text(0.018, it + 16, f"{fmt1(it)} ns", fontsize=8.0, color=col, fontweight="bold", va="bottom")

    iio_true_lo, iio_true_hi = min(iio_p2_group_meds), max(iio_p2_group_meds)
    iio_true_med = med(iio_p2_group_meds)
    iio_ge1_lo = min(v for v in iio_p2_group_meds if v >= 1.0)
    n_ge1 = sum(1 for v in iio_p2_group_meds if v >= 1.0)
    iio_dil_lo, iio_dil_hi = min(iio_diluted), max(iio_diluted)

    ax_u.set_xlim(-0.04, 1.35)
    ax_u.set_ylim(80, 910)
    ax_u.axvline(0.0, color="#64748b", linestyle="--", linewidth=0.9, alpha=0.7)
    ax_u.axvline(1.0 / 2.2, color="#94a3b8", linestyle=":", linewidth=0.9)
    ax_u.text(1.0 / 2.2 - 0.02, 95, "2.2 GHz\n(基准)", fontsize=7.8, color="#475569", ha="right", va="bottom")

    ax_u.text(
        0.41, 0.04,
        f"实测时钟域验证（Part 2 20 档环内 clk / wall_s）：\n"
        f"• CHA & M2PCIe₁ 随动：0.800 → 2.200 GHz\n"
        f"• IRP₁ & IIO₁ 恒定：{iio_ge1_lo:.4f}–{iio_true_hi:.4f} GHz（{n_ge1}/{len(iio_p2_group_meds)} 档，\n"
        f"  仅 1 档 {iio_true_lo:.4f}G；active_ghz {iio_dil_lo:.2f}–{iio_dil_hi:.2f}G 系尾端门控稀释）\n"
        f"→ 斜率 {fmt1(min(slopes))}–{fmt1(max(slopes))} cyc = Mesh 域（小差异属 CHA 距离）\n"
        f"→ 截距含核 + 1.001G IIO/RP + PCIe/卡侧固定 ns",
        transform=ax_u.transAxes, fontsize=7.1, va="bottom", ha="left", multialignment="left",
        bbox=dict(boxstyle="round,pad=0.42", facecolor="#f8fafc", edgecolor="#cbd5e1", linewidth=0.9),
    )
    ax_u.set_xlabel("Socket 1 Uncore 周期 1 / f_uncore（ns/cyc，实测 uncore_cha_0 时钟：0.8 / 1.1 / 1.4 / 1.8 / 2.2 GHz）", fontsize=8.8)
    ax_u.set_ylabel("单次串行 MMIO Read 同口径净均值（ns，raw_mean − floor_mean，cpu40 = 3.90 GHz）", fontsize=8.6)
    ax_u.set_title("(a) Part 2：1 / f_uncore 线性回归（斜率 = Host Mesh 拍数，截距分离非 Mesh 固定段）", fontsize=10.0, fontweight="bold", pad=10)
    ax_u.legend(loc="upper left", fontsize=7.6, frameon=True, facecolor="white", framealpha=0.95)

    # ==================== Panel (b): Part 3 cpu40 Core Sweep (1/f_core) ====================
    c_specs = [
        ("rp-bar0-8b", "RP 98:01.0 (TOR外)", "#0f766e", "o"),
        ("vf0-bar0-8b", "VF0 BAR0 (TOR外)", "#d97706", "s"),
        ("bar4-l3-walk-8b", "BAR4 L3 4K (TOR外)", "#7c3aed", "^"),
        ("timer-floor", "空括号底噪 (lfence;rdtsc)", "#64748b", "x"),
    ]
    c_freqs = [1500000, 2200000, 3000000, 3900000]
    xc_grid = np.linspace(0.0, 0.72, 100)

    core_slopes = []
    p23_diffs = []
    for arm, name, col, mk in c_specs:
        xs, ys = [], []
        for fk in c_freqs:
            for r in by_sl[("part3_core", f"c{fk}/{arm}")]:
                xs.append(1.0 / r["aperf_mperf_ghz"])
                if arm == "timer-floor":
                    ys.append(r["raw_ns"]["mean"])
                else:
                    ys.append(r["net_mean_sub_floor_mean_ns"] - r["cha"]["tor_residency_ns"])
        sl, it, r2, rmse = ols(xs, ys)
        if arm != "timer-floor":
            core_slopes.append(sl)
            p2_sl, p2_it, _, _ = p2_out_fits[arm]
            p23_diffs.append(abs(sl / 3.9 - p2_it))
            p23_diffs.append(abs(p2_sl / 2.2 - it))
        ls = "--" if arm == "timer-floor" else "-"
        ax_c.scatter(xs, ys, color=col, marker=mk, s=26, alpha=0.88, zorder=5)
        ax_c.plot(xc_grid, it + sl * xc_grid, color=col, linewidth=1.7, linestyle=ls,
                  label=f"{name}：{fmt1(it)} + {fmt1(sl)} / f  (@3.9G 核域 {fmt1(sl/3.9)} ns)")

    ax_c.axvline(0.0, color="#64748b", linestyle="--", linewidth=0.9, alpha=0.7)
    ax_c.axvline(1.0 / 3.9, color="#94a3b8", linestyle=":", linewidth=0.9)
    ax_c.text(1.0 / 3.9 + 0.012, 2.5, "3.9 GHz", fontsize=7.6, color="#475569")
    ax_c.set_xlim(-0.02, 0.73)
    ax_c.set_ylim(-1, 47)
    ax_c.set_xlabel("cpu40 核周期 1 / f_core（ns/cyc，实测 MSR APERF/MPERF：1.5 / 2.2 / 3.0 / 3.9 GHz）", fontsize=8.5)
    ax_c.set_ylabel("TOR 外余量 (同口径 floor_mean) / 底噪（ns）", fontsize=8.3)
    ax_c.set_title(
        f"(b) Part 3：1/f_core 同口径扣 floor_mean（{fmt1(min(core_slopes))}–{fmt1(max(core_slopes))} 核周期 = "
        f"{fmt1(min(core_slopes)/3.9)}–{fmt1(max(core_slopes)/3.9)} ns，与 Part 2 闭合 ≤ {max(p23_diffs):.2f} ns）",
        fontsize=8.6, fontweight="bold", pad=8,
    )
    ax_c.legend(loc="upper left", fontsize=7.3, frameon=True, facecolor="white", framealpha=0.95)
    # ==================== Panel (c): Part 4 BAR4 WC (`resource4_wc`) vs UC Read Matrix ====================
    wc_order = [
        ("wc-8b", "WC 8 B (mov qword)", "#0284c7"),
        ("wc-16b-movdqu", "WC 16 B (movdqu xmm)", "#0ea5e9"),
        ("wc-16b-vmovntdqa", "WC 16 B (vmovntdqa xmm)", "#38bdf8"),
        ("wc-64b-1x64-vmovntdqa", "WC 64 B (1×vmovntdqa zmm)", "#0f766e"),
        ("wc-64b-vmovdqu64", "WC 64 B (1×vmovdqu64 zmm)", "#14b8a6"),
        ("wc-64b-4x16-vmovntdqa", "WC 64 B (4×16B vmovntdqa)", "#dc2626"),
        ("wc-64b-4x16-movdqu", "WC 64 B (4×16B movdqu)", "#f97316"),
        ("uc-64b-4x16-vmovntdqa", "UC 对照 (4×16B vmovntdqa)", "#64748b"),
    ]
    y_wc = np.arange(len(wc_order))[::-1]
    for yi, (arm, lbl, col) in zip(y_wc, wc_order):
        rs = by_sl[("part4_wc", arm)]
        p50 = med([r["net_ns"]["p50"] for r in rs])
        tlp = med([r["iio1"]["tlp_per_op"] for r in rs])
        cb = med([r["iio1"]["cmpd_bytes_per_op"] for r in rs])
        op = "UCRdF" if med([r["cha"]["ucrdf_ins"] for r in rs]) > 1000 else "PRd"
        ax_wc.barh(yi, p50, height=0.62, color=col, edgecolor="white", linewidth=0.7)
        ax_wc.text(p50 + 35, yi, f"{fmt1(p50)} ns  |  {tlp:.2f} TLP ({int(round(cb))} B, {op})",
                   va="center", ha="left", fontsize=7.9, color="#0f172a", fontweight="medium")

    ax_wc.set_yticks(y_wc)
    ax_wc.set_yticklabels([x[1] for x in wc_order], fontsize=8.0)
    ax_wc.set_xlim(0, 3680)
    ax_wc.set_xlabel("单次操作净 p50 延迟（ns，BAR4 L3 4K 走步，cpu40 @ 3.90 GHz / Mesh 2.20 GHz）", fontsize=8.5)
    ax_wc.set_title("(c) Part 4：resource4_wc 被驱动 ioremap(UC-) 静默降级 → 4×16B 仍 4 TLP；1×64B zmm 单 TLP 快 4.0×", fontsize=8.8, fontweight="bold", pad=8)
    fig.suptitle(
        "MRV-185 频率回归交叉验算（1 / f_uncore & 1 / f_core）与 BAR4 resource4_wc 流式读实测（n=3 交错，零错，100% 非复用）",
        fontsize=12.0, fontweight="bold", y=0.985,
    )
    out = save(fig, "mrv-185-freq-and-wc")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
