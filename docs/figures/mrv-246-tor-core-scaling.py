"""MRV-246 Figure 1: CHA TOR / WCiLF core scaling (1-32 physical cores + 32t/48t/64t SMT)
for 4 KiB (64-line) noop-stride vs 64 B (1-line) noop-fixed on Intel DSA Portal wq2.0
(standalone 0-victim runs on Socket 1, zero neighbor confounding).

Left panel: Total CHA WCiLF occupancy vs true numeric physical-core axis (1..32) plus
separated discrete SMT points (no misleading line across 32t 16x2 vs 24+8), against the
330-430 cliff band and MRV-196 8c BAR4 (32,768-line) reference.
Right panel: Two-stage bend mechanism — per-core WCiLF contribution (left axis, first
bend at 4c->8c) vs per-CHA box min/max occupancy (right axis, hottest CHA hitting the
~22.9-entry hardware ceiling at 16c-32c while downstream IIO0/IRP0/M2PCIe0 are already
100% saturated from 4c onward).

Reads raw verified n=3 runs directly from:
  - logs/mrv246-2026-11-27/runs.jsonl
  - logs/mrv196-2026-11-25/runs.jsonl
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
M246_RUNS = ROOT / "logs/mrv246-2026-11-27/runs.jsonl"
M196_RUNS = ROOT / "logs/mrv196-2026-11-25/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def load_verified(path: Path, is_m246: bool = True):
    by = defaultdict(list)
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        assert r["accepted"] is True
        assert r["host_pmu_min_pct"] == 100.0
        assert r["page_faults"] == 0 and r["victim_page_faults"] == 0 and r["aggr_page_faults"] == 0
        if is_m246:
            assert r["victim_aggr_phys_overlap"] == 0
            assert r["drain_ok"] is True
            assert r["dsa_comp_errors"] == 0 and r["dsa_verify_errors"] == 0
            assert r["dsa2_pmu"]["cpl_err"] == 0
            assert r["dsa2_pmu"]["dwq_full"] == 0
        if r.get("chase"):
            assert r["chase"]["bad_nodes"] == 0
        if r.get("stream"):
            assert r["stream"]["verify_errors"] == 0
        by[r["label"]].append(r)
    return by


def main():
    m246 = load_verified(M246_RUNS, is_m246=True)
    m196 = load_verified(M196_RUNS, is_m246=False)

    bar4_8c_cha = med([r["cha"]["occ_entries"] for r in m196["dram__bar4_8c_max_b512"]])

    phys_topos = [
        ("1c", 1),
        ("4c", 4),
        ("8c", 8),
        ("12c", 12),
        ("16c", 16),
        ("20c", 20),
        ("24c", 24),
        ("31c", 31),
        ("32c", 32),
    ]
    smt_topos = [
        ("32t_16x2", "32t\n16×2", 32, 37.5),
        ("32t_24p8s", "32t\n24+8", 32, 42.5),
        ("48t_24x2", "48t\n24×2", 48, 47.5),
        ("64t_32x2", "64t\n32×2", 64, 52.5),
    ]

    px = [c for _, c in phys_topos]
    p_wcilf, p_all, p_per_core, p_bmax, p_bmin = [], [], [], [], []
    p_irp, p_m2p, p_mpps = [], [], []
    for key, ncore in phys_topos:
        rw = m246[f"none__noop-stride_{key}_wcilf"]
        ra = m246[f"none__noop-stride_{key}"]
        assert len(rw) == 3 and len(ra) == 3
        w = med([r["cha"]["occ_entries"] for r in rw])
        a = med([r["cha"]["occ_entries"] for r in ra])
        p_wcilf.append(w)
        p_all.append(a)
        p_per_core.append(w / ncore)
        p_bmax.append(med([r["cha"]["occ_max_box"] for r in rw]))
        p_bmin.append(med([r["cha"]["occ_min_box"] for r in rw]))
        p_irp.append(med([r["irp0"]["occ_entries"] for r in rw]))
        p_m2p.append(med([r["m2p0"]["ne_cha_ncb_duty_pct"] for r in rw]))
        p_mpps.append(med([r["iio0"]["cpu_wr_mpps"] for r in rw]))

    sx = [xpos for _, _, _, xpos in smt_topos]
    s_wcilf = [med([r["cha"]["occ_entries"] for r in m246[f"none__noop-stride_{k}_wcilf"]]) for k, _, _, _ in smt_topos]

    fixed_phys = [("1c", 1), ("8c", 8), ("16c", 16), ("24c", 24), ("32c", 32)]
    fx = [c for _, c in fixed_phys]
    f_wcilf = [med([r["cha"]["occ_entries"] for r in m246[f"none__noop-fixed_{k}_wcilf"]]) for k, _ in fixed_phys]
    f_smt_32 = med([r["cha"]["occ_entries"] for r in m246["none__noop-fixed_32t_16x2_wcilf"]])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.6, 6.2), gridspec_kw={"width_ratios": [1.14, 1.0]})

    # ---- Panel A: True numeric physical-core x-axis + discrete SMT markers ----
    ax1.axhspan(330, 430, color="#fef3c7", alpha=0.80, zorder=1)
    ax1.axhline(330, color="#d97706", linestyle="--", lw=1.0, alpha=0.85, zorder=2)
    ax1.axhline(430, color="#d97706", linestyle="--", lw=1.0, alpha=0.85, zorder=2)
    ax1.text(54.2, 380, "起伤悬崖带 (330–430 槽)", ha="right", va="center",
             fontsize=8.4, fontweight="bold", color="#92400e",
             bbox=dict(boxstyle="round,pad=0.24", facecolor="#fffbeb", edgecolor="#f59e0b", alpha=0.95),
             zorder=4)

    ax1.axhline(bar4_8c_cha, color="#881337", linestyle=":", lw=1.6, zorder=2)
    ax1.text(0.5, bar4_8c_cha + 11, f"对照：CN106 8 核 BAR4 宽窗 = {bar4_8c_cha:.0f} 槽 (MRV-196)",
             ha="left", va="bottom", fontsize=8.3, fontweight="bold", color="#881337", zorder=4)

    ax1.axvline(34.8, color="#94a3b8", linestyle="-.", lw=1.1, alpha=0.8, zorder=1)
    ax1.text(16.5, 725, "纯物理核扩展 (1–32 核，等距核数轴)", ha="center", va="top",
             fontsize=8.4, color="#475569", fontweight="bold")
    ax1.text(45.0, 725, "超线程 SMT（离散点）", ha="center", va="top",
             fontsize=8.4, color="#475569", fontweight="bold")

    ax1.plot(px, p_all, color="#94a3b8", linestyle="--", marker="s", lw=1.3, markersize=4.2,
             label="4 KiB (64 行) 总 TOR 占用（与 WCiLF 重合 >99.6%）", zorder=3)
    ax1.plot(px, p_wcilf, color="#dc2626", marker="o", lw=2.5, markersize=6.8,
             label="4 KiB (64 行) CHA WCiLF 占用", zorder=5)

    # Discrete SMT markers with subtle stems (no misleading connecting line across 32t topologies)
    for xp, val in zip(sx, s_wcilf):
        ax1.vlines(xp, 0, val, color="#fca5a5", linestyle=":", lw=1.1, zorder=2)
    ax1.scatter(sx, s_wcilf, color="#dc2626", marker="D", s=54, zorder=6,
                label="4 KiB SMT 离散拓扑（32t/48t/64t）")

    ax1.plot(fx, f_wcilf, color="#0f766e", linestyle=":", marker="^", lw=2.0, markersize=6.5,
             label="64 B (1 行) CHA WCiLF（仅占 1/32 CHA）", zorder=4)
    ax1.scatter([sx[0]], [f_smt_32], color="#0f766e", marker="^", s=48, zorder=5)

    p_labels = [
        (0, 0.0, 18, "center", "bottom"),
        (1, 0.0, 18, "center", "bottom"),
        (2, 0.4, -22, "left", "top"),
        (3, -0.3, 18, "right", "bottom"),
        (4, 0.0, 18, "center", "bottom"),
        (6, 0.0, 18, "center", "bottom"),
        (8, 0.0, 18, "center", "bottom"),
    ]
    for idx, dx, dy, ha, va in p_labels:
        ax1.text(px[idx] + dx, p_wcilf[idx] + dy, f"{p_wcilf[idx]:.0f}", ha=ha, va=va,
                 fontsize=8.2, fontweight="bold", color="#b91c1c", zorder=6)

    for idx, (xp, val) in enumerate(zip(sx, s_wcilf)):
        dy = -24 if idx == 0 else 18
        va = "top" if idx == 0 else "bottom"
        ax1.text(xp, val + dy, f"{val:.0f}", ha="center", va=va,
                 fontsize=8.2, fontweight="bold", color="#b91c1c", zorder=6)

    for cx, fv in zip(fx, f_wcilf):
        if cx in (1, 8, 24, 32):
            ax1.text(cx, fv + 15, f"{fv:.1f}", ha="center", va="bottom",
                     fontsize=7.9, fontweight="bold", color="#0f766e")
    ax1.text(sx[0], f_smt_32 + 15, f"{f_smt_32:.1f}", ha="center", va="bottom",
             fontsize=7.9, fontweight="bold", color="#0f766e")

    ax1.set_ylim(0, 745)
    ax1.set_xlim(-1.0, 55.5)
    xticks = [1, 4, 8, 12, 16, 20, 24, 32] + sx
    xtick_labels = ["1", "4", "8", "12", "16", "20", "24", "32"] + [t[1] for t in smt_topos]
    ax1.set_xticks(xticks)
    ax1.set_xticklabels(xtick_labels, fontsize=8.0)
    ax1.set_xlabel("攻击物理核数（左） / SMT 拓扑（右）", fontsize=9.2)
    ax1.set_ylabel("全片 32 个 CHA 平均在途槽位 (entries)", fontsize=9.6)
    ax1.set_title("A. 全片 CHA 占用：4 KiB 并非被 64 行锁死，12→16 核直接破崖", fontsize=10.3, fontweight="bold", pad=10)
    ax1.legend(loc="upper left", bbox_to_anchor=(0.01, 0.85), fontsize=7.8, framealpha=0.95)

    # ---- Panel B: Two-stage bend — per-core contribution vs per-CHA box min/max ----
    x_idx = np.arange(len(phys_topos))
    phys_lbls = [f"{c}核" for _, c in phys_topos]
    ax2_r = ax2.twinx()

    ax2.bar(x_idx, p_per_core, width=0.52, color="#fecaca", edgecolor="#dc2626", lw=1.2,
            label="每核平均贡献 WCiLF (槽/核，左轴)", zorder=3)
    for i, v in enumerate(p_per_core):
        if i == 0:
            ax2.text(x_idx[i], v - 2.2, f"{v:.1f}", ha="center", va="top",
                     fontsize=8.1, fontweight="bold", color="#991b1b")
        else:
            ax2.text(x_idx[i], v + 1.3, f"{v:.1f}", ha="center", va="bottom",
                     fontsize=8.1, fontweight="bold", color="#991b1b")

    ax2_r.fill_between(x_idx, p_bmin, p_bmax, color="#cbd5e1", alpha=0.45, zorder=2,
                       label="32 个 CHA 盒间分布区间 (min–max，右轴)")
    ax2_r.plot(x_idx, p_bmax, color="#1e293b", marker="D", lw=2.1, markersize=5.5,
               label="最热 CHA 盒 WCiLF (槽，右轴)", zorder=5)
    ax2_r.plot(x_idx, p_bmin, color="#64748b", linestyle="--", marker="s", lw=1.5, markersize=4.5,
               label="最冷 CHA 盒 WCiLF (槽，右轴)", zorder=4)
    ax2_r.axhline(22.9, color="#7f1d1d", linestyle=":", lw=1.3, zorder=3)
    ax2_r.text(8.35, 22.1, "单盒硬顶 ≈ 22.9 槽", ha="right", va="top",
               fontsize=8.0, fontweight="bold", color="#7f1d1d")

    ax2_r.annotate(
        f"4–32 核下游已 100% 满载 (dwq_full = 0)\nIIO0 恒定 {med(p_mpps[1:]):.1f} Mpps · IRP0 = {med(p_irp[1:]):.2f} · M2PCIe0 = 99.9%",
        xy=(4, p_bmax[4]), xytext=(2.4, 32.5), ha="center", va="center",
        fontsize=7.9, fontweight="bold", color="#1e293b",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#475569", alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#475569", lw=1.1),
        zorder=6,
    )

    ax2.set_ylim(0, 94)
    ax2_r.set_ylim(0, 37)
    ax2.set_xticks(x_idx)
    ax2.set_xticklabels(phys_lbls, fontsize=8.3)
    ax2.set_ylabel("每核平均贡献 WCiLF 槽位 (entries / 核)", fontsize=9.4, color="#991b1b")
    ax2_r.set_ylabel("单个 CHA 盒在途 WCiLF (entries / box)", fontsize=9.4, color="#1e293b")
    ax2_r.spines["top"].set_visible(False)
    ax2_r.grid(False)
    ax2.set_title("B. 两段折弯：4→8 核 Store Buffer 偏斜降速，16 核最热盒触顶 22.9 槽", fontsize=10.1, fontweight="bold", pad=10)

    h1, lb1 = ax2.get_legend_handles_labels()
    h2, lb2 = ax2_r.get_legend_handles_labels()
    ax2.legend(h1 + h2, lb1 + lb2, loc="upper right", bbox_to_anchor=(0.99, 0.99), fontsize=7.6, framealpha=0.95)

    fig.suptitle(
        "MRV-246：Intel DSA 4 KiB (64 行) Portal 1–32 核扩展（Socket 1 独立轮，n = 3 中位数，0 邻居干扰）",
        fontsize=11.5, fontweight="bold", y=0.99,
    )
    png = mrvplot.save(fig, "mrv-246-tor-core-scaling")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
