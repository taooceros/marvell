"""MRV-246 Figure 1: CHA TOR / WCiLF core scaling (1-32 physical cores + 32t/48t/64t SMT)
for 4 KiB (64-line) noop-stride vs 64 B (1-line) noop-fixed on Intel DSA Portal wq2.0
(standalone 0-victim runs on Socket 1, zero neighbor confounding).

Left panel: Total CHA TOR and WCiLF occupancy vs attacker threads against the
330-430 cliff band and MRV-196 8c BAR4 (32,768-line) reference.
Right panel: Per-thread CHA WCiLF contribution vs downstream M2PCIe0/IRP0 saturation,
showing that portal throughput is already 100% saturated at 4-8 cores while additional
cores keep stuffing CHA TOR across all 32 boxes.

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

    topos = [
        ("1c", "1核", 1),
        ("4c", "4核", 4),
        ("8c", "8核", 8),
        ("12c", "12核", 12),
        ("16c", "16核", 16),
        ("20c", "20核", 20),
        ("24c", "24核", 24),
        ("31c", "31核", 31),
        ("32c", "32核", 32),
        ("32t_16x2", "32t\n16×2", 32),
        ("32t_24p8s", "32t\n24+8", 32),
        ("48t_24x2", "48t\n24×2", 48),
        ("64t_32x2", "64t\n32×2", 64),
    ]
    x = np.arange(len(topos))

    stride_wcilf, stride_all, stride_per_thr = [], [], []
    stride_irp, stride_m2p, stride_mpps = [], [], []
    for key, _, nthr in topos:
        rw = m246[f"none__noop-stride_{key}_wcilf"]
        ra = m246[f"none__noop-stride_{key}"]
        assert len(rw) == 3 and len(ra) == 3
        w = med([r["cha"]["occ_entries"] for r in rw])
        a = med([r["cha"]["occ_entries"] for r in ra])
        stride_wcilf.append(w)
        stride_all.append(a)
        stride_per_thr.append(w / nthr)
        stride_irp.append(med([r["irp0"]["occ_entries"] for r in rw]))
        stride_m2p.append(med([r["m2p0"]["ne_cha_ncb_duty_pct"] for r in rw]))
        stride_mpps.append(med([r["iio0"]["cpu_wr_mpps"] for r in rw]))

    fixed_keys = ["1c", "8c", "16c", "24c", "32c", "32t_16x2"]
    fixed_x = [i for i, (k, _, _) in enumerate(topos) if k in fixed_keys]
    fixed_wcilf = [med([r["cha"]["occ_entries"] for r in m246[f"none__noop-fixed_{k}_wcilf"]]) for k in fixed_keys]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.4, 6.1), gridspec_kw={"width_ratios": [1.14, 1.0]})

    # ---- Panel A: Total CHA TOR / WCiLF vs core count + SMT ----
    ax1.axhspan(330, 430, color="#fef3c7", alpha=0.80, zorder=1)
    ax1.axhline(330, color="#d97706", linestyle="--", lw=1.0, alpha=0.85, zorder=2)
    ax1.axhline(430, color="#d97706", linestyle="--", lw=1.0, alpha=0.85, zorder=2)
    ax1.text(12.35, 380, "起伤悬崖带 (330–430 槽)", ha="right", va="center",
             fontsize=8.5, fontweight="bold", color="#92400e",
             bbox=dict(boxstyle="round,pad=0.24", facecolor="#fffbeb", edgecolor="#f59e0b", alpha=0.95),
             zorder=4)

    ax1.axhline(bar4_8c_cha, color="#881337", linestyle=":", lw=1.6, zorder=2)
    ax1.text(0.15, bar4_8c_cha + 11, f"对照：CN106 8 核 BAR4 宽窗 = {bar4_8c_cha:.0f} 槽 (MRV-196)",
             ha="left", va="bottom", fontsize=8.4, fontweight="bold", color="#881337", zorder=4)

    ax1.axvline(8.5, color="#94a3b8", linestyle="-.", lw=1.0, alpha=0.75, zorder=1)
    ax1.text(4.0, 720, "纯物理核扩展 (1–32 核)", ha="center", va="top", fontsize=8.5, color="#475569", fontweight="bold")
    ax1.text(10.5, 720, "超线程 SMT", ha="center", va="top", fontsize=8.5, color="#475569", fontweight="bold")

    ax1.plot(x[:9], stride_all[:9], color="#94a3b8", linestyle="--", marker="s", lw=1.4, markersize=4.5,
             label="4 KiB (64 行) 总 TOR 占用（与 WCiLF 重合 >99.6%）", zorder=3)
    ax1.plot(x[9:], stride_all[9:], color="#94a3b8", linestyle="--", marker="s", lw=1.4, markersize=4.5, zorder=3)

    ax1.plot(x[:9], stride_wcilf[:9], color="#dc2626", marker="o", lw=2.5, markersize=6.8,
             label="4 KiB (64 行) CHA WCiLF 占用", zorder=5)
    ax1.plot(x[9:], stride_wcilf[9:], color="#dc2626", marker="o", lw=2.2, markersize=6.8, zorder=5)

    ax1.plot(fixed_x[:5], fixed_wcilf[:5], color="#0f766e", linestyle=":", marker="^", lw=2.0, markersize=6.5,
             label="64 B (1 行) CHA WCiLF（仅占 1/32 CHA）", zorder=4)
    ax1.scatter([fixed_x[5]], [fixed_wcilf[5]], color="#0f766e", marker="^", s=46, zorder=4)

    label_spec = [
        (0, 0.0, 18, "center", "bottom"),
        (1, -0.15, 16, "right", "bottom"),
        (2, 0.14, -24, "left", "top"),
        (3, -0.10, 18, "right", "bottom"),
        (4, 0.0, 18, "center", "bottom"),
        (6, 0.0, 18, "center", "bottom"),
        (8, 0.0, 18, "center", "bottom"),
        (9, 0.0, -24, "center", "top"),
        (10, 0.0, 18, "center", "bottom"),
        (11, 0.0, 18, "center", "bottom"),
        (12, 0.0, 18, "center", "bottom"),
    ]
    for idx, dx, dy, ha, va in label_spec:
        val = stride_wcilf[idx]
        ax1.text(x[idx] + dx, val + dy, f"{val:.0f}", ha=ha, va=va,
                 fontsize=8.3, fontweight="bold", color="#b91c1c", zorder=6)

    for fx, fv in zip(fixed_x, fixed_wcilf):
        if fx in (0, 2, 6, 8):
            ax1.text(fx, fv + 15, f"{fv:.1f}", ha="center", va="bottom",
                     fontsize=8.0, fontweight="bold", color="#0f766e")

    ax1.set_ylim(0, 745)
    ax1.set_xlim(-0.5, len(topos) - 0.45)
    ax1.set_xticks(x)
    ax1.set_xticklabels([t[1] for t in topos], fontsize=8.1)
    ax1.set_ylabel("全片 32 个 CHA 平均在途槽位 (entries)", fontsize=9.8)
    ax1.set_title("A. 全片 CHA 占用：4 KiB 并非被 64 行锁死，12→16 核直接破崖", fontsize=10.4, fontweight="bold", pad=10)
    ax1.legend(loc="upper left", bbox_to_anchor=(0.01, 0.84), fontsize=7.9, framealpha=0.95)

    # ---- Panel B: Per-thread CHA contribution + downstream saturation ----
    x_phys = np.arange(9)
    phys_lbls = [t[1] for t in topos[:9]]
    ax2_r = ax2.twinx()

    ax2.bar(x_phys, stride_per_thr[:9], width=0.52, color="#fecaca", edgecolor="#dc2626", lw=1.2,
            label="每核平均贡献 CHA WCiLF (槽/核，左轴)", zorder=3)
    for i, v in enumerate(stride_per_thr[:9]):
        if i == 0:
            ax2.text(x_phys[i], v - 2.2, f"{v:.1f}", ha="center", va="top",
                     fontsize=8.2, fontweight="bold", color="#991b1b")
        else:
            ax2.text(x_phys[i], v + 1.4, f"{v:.1f}", ha="center", va="bottom",
                     fontsize=8.2, fontweight="bold", color="#991b1b")

    ax2_r.plot(x_phys, stride_m2p[:9], color="#1e293b", marker="D", lw=2.0, markersize=5.5,
               label="M2PCIe0 占空比 (%，右轴)", zorder=5)
    ax2_r.plot(x_phys, [v * 10.0 for v in stride_irp[:9]], color="#64748b", linestyle="--",
               marker="s", lw=1.7, markersize=5.0,
               label="IRP0 在途占用 ×10 (槽×10，右轴)", zorder=4)

    ax2_r.annotate(
        f"4–32 核下游已 100% 满载\nIIO0 写速率恒为 {med(stride_mpps[1:9]):.1f} Mpps\nIRP0 恒为 {med(stride_irp[1:9]):.2f} 槽",
        xy=(4, stride_m2p[4]), xytext=(5.3, 56), ha="center", va="center",
        fontsize=8.3, fontweight="bold", color="#1e293b",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#475569", alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#475569", lw=1.2),
        zorder=6,
    )

    ax2.set_ylim(0, 88)
    ax2_r.set_ylim(0, 130)
    ax2.set_xticks(x_phys)
    ax2.set_xticklabels(phys_lbls, fontsize=8.4)
    ax2.set_ylabel("每核平均贡献 WCiLF 槽位 (entries / 核)", fontsize=9.6, color="#991b1b")
    ax2_r.set_ylabel("M2PCIe0 占空比 (%) / IRP0 占用×10", fontsize=9.4, color="#334155")
    ax2_r.spines["top"].set_visible(False)
    ax2_r.grid(False)
    ax2.set_title("B. 下游 4 核已满反压，但加核仍把多余排队塞进 32 个 CHA", fontsize=10.4, fontweight="bold", pad=10)

    h1, lb1 = ax2.get_legend_handles_labels()
    h2, lb2 = ax2_r.get_legend_handles_labels()
    ax2.legend(h1 + h2, lb1 + lb2, loc="upper right", fontsize=7.9, framealpha=0.95)

    fig.suptitle(
        "MRV-246：Intel DSA 4 KiB (64 行) Portal 1–32 核扩展（Socket 1 独立轮，n = 3 中位数，0 邻居干扰）",
        fontsize=11.6, fontweight="bold", y=0.99,
    )
    png = mrvplot.save(fig, "mrv-246-tor-core-scaling")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
