"""MRV-246 Figure 2: Co-located Socket-1 neighbor impact across 0-24 physical cores
+ 32t/48t SMT (6c STREAM Triad logical bandwidth AND real DDR5 IMC physical bandwidth
retention) and up to 31 physical cores + 62t SMT (16 MiB L3 pointer-chase p99 tail
latency), with same-core l1-spin frequency-droop controls and 64 B (1-line) noop-fixed
immunity curve.

Reads raw verified n=3 runs directly from:
  - logs/mrv246-2026-11-27/runs.jsonl
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


def med(xs):
    return statistics.median(xs)


def load_verified(path: Path):
    by = defaultdict(list)
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        assert r["accepted"] is True
        assert r["host_pmu_min_pct"] == 100.0
        assert r["page_faults"] == 0 and r["victim_page_faults"] == 0 and r["aggr_page_faults"] == 0
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
    m246 = load_verified(M246_RUNS)

    t_topos = [
        ("idle", "空载\n0核"),
        ("1c", "1核"),
        ("4c", "4核"),
        ("8c", "8核"),
        ("12c", "12核"),
        ("16c", "16核"),
        ("20c", "20核"),
        ("24c", "24核"),
        ("32t_16x2", "32t\n16×2"),
        ("32t_24p8s", "32t\n24+8"),
        ("48t_24x2", "48t\n24×2"),
    ]
    tx = np.arange(len(t_topos))
    t_base = med([r["stream"]["mean_gb_s"] for r in m246["t6c__idle"]])
    imc_base = med([r["imc"]["tot_gb_s"] for r in m246["t6c__idle"]])

    def get_t_ret(aggr, key):
        lbl = "t6c__idle" if key == "idle" else f"t6c__{aggr}_{key}"
        bw = med([r["stream"]["mean_gb_s"] for r in m246[lbl]])
        imc = med([r["imc"]["tot_gb_s"] for r in m246[lbl]])
        return bw / t_base * 100.0, bw, imc / imc_base * 100.0, imc

    t_stride_ret, t_stride_gb, t_stride_imc_ret, t_stride_imc_gb = zip(
        *[get_t_ret("noop-stride", k) for k, _ in t_topos]
    )

    spin_keys_t = ["idle", "1c", "8c", "16c", "24c", "32t_16x2", "48t_24x2"]
    tx_spin = [i for i, (k, _) in enumerate(t_topos) if k in spin_keys_t]
    t_spin_ret = [get_t_ret("l1-spin", k)[0] for k in spin_keys_t]

    fix_keys_t = ["idle", "1c", "8c", "16c", "24c", "32t_16x2"]
    tx_fix = [i for i, (k, _) in enumerate(t_topos) if k in fix_keys_t]
    t_fix_ret = [get_t_ret("noop-fixed", k)[0] for k in fix_keys_t]
    t_fix_imc_ret = [get_t_ret("noop-fixed", k)[2] for k in fix_keys_t]

    l_topos = [
        ("idle", "空载\n0核"),
        ("1c", "1核"),
        ("4c", "4核"),
        ("8c", "8核"),
        ("12c", "12核"),
        ("16c", "16核"),
        ("20c", "20核"),
        ("24c", "24核"),
        ("31c", "31核"),
        ("32t_16x2", "32t\n16×2"),
        ("32t_24p8s", "32t\n24+8"),
        ("48t_24x2", "48t\n24×2"),
        ("62t_31x2", "62t\n31×2"),
    ]
    lx = np.arange(len(l_topos))

    def get_l_p99(aggr, key):
        lbl = "llc__idle" if key == "idle" else f"llc__{aggr}_{key}"
        return med([r["chase"]["p99_ns"] for r in m246[lbl]])

    l_stride_p99 = [get_l_p99("noop-stride", k) for k, _ in l_topos]

    spin_keys_l = ["idle", "1c", "8c", "16c", "24c", "31c", "32t_16x2", "48t_24x2", "62t_31x2"]
    lx_spin = [i for i, (k, _) in enumerate(l_topos) if k in spin_keys_l]
    l_spin_p99 = [get_l_p99("l1-spin", k) for k in spin_keys_l]

    fix_keys_l = ["idle", "1c", "8c", "16c", "24c", "31c", "32t_16x2"]
    lx_fix = [i for i, (k, _) in enumerate(l_topos) if k in fix_keys_l]
    l_fix_p99 = [get_l_p99("noop-fixed", k) for k in fix_keys_l]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.8, 6.2), gridspec_kw={"width_ratios": [1.02, 1.08]})

    # ---- Panel A: 6c STREAM Triad logical BW + real DDR5 IMC physical BW ----
    ax1.axhline(100.0, color="#94a3b8", linestyle="-", lw=1.0, alpha=0.6, zorder=1)
    ax1.axvline(7.5, color="#94a3b8", linestyle="-.", lw=1.0, alpha=0.75, zorder=1)
    ax1.axvspan(3.6, 10.6, color="#fef2f2", alpha=0.50, zorder=0)
    ax1.text(3.5, 130, "物理核扩展", ha="center", va="top", fontsize=8.4, color="#475569", fontweight="bold")
    ax1.text(9.0, 130, "SMT（离散点）", ha="center", va="top", fontsize=8.4, color="#475569", fontweight="bold")

    ax1.plot(tx_spin[:5], t_spin_ret[:5], color="#64748b", linestyle="--", marker="s", lw=1.7, markersize=5.2,
             label="核内空转降频对照 (l1-spin，2.8 GHz 不掉速)", zorder=3)
    ax1.scatter(tx_spin[5:], t_spin_ret[5:], color="#64748b", marker="s", s=32, zorder=3)

    ax1.plot(tx_fix[:5], t_fix_ret[:5], color="#0f766e", linestyle=":", marker="^", lw=1.8, markersize=5.8,
             label="64 B 单行 STREAM（24核 119% 系 RFO→ItoM）", zorder=4)
    ax1.scatter([tx_fix[5]], [t_fix_ret[5]], color="#0f766e", marker="^", s=38, zorder=4)
    ax1.plot(tx_fix[:5], t_fix_imc_ret[:5], color="#14b8a6", linestyle="-.", marker="v", lw=1.3, markersize=4.5, alpha=0.85,
             label="64 B 单行实测 IMC 物理带宽（24核 = 89%）", zorder=3)

    ax1.plot(tx[:8], t_stride_ret[:8], color="#dc2626", marker="o", lw=2.5, markersize=6.8,
             label="4 KiB (64 行) 逻辑 STREAM 带宽", zorder=5)
    ax1.scatter(tx[8:], t_stride_ret[8:], color="#dc2626", marker="D", s=46, zorder=6)

    ax1.plot(tx[:8], t_stride_imc_ret[:8], color="#d97706", linestyle="--", marker="d", lw=2.0, markersize=6.0,
             label="4 KiB (64 行) 实测 IMC 物理带宽（12核已跌 -22%）", zorder=5)
    ax1.scatter(tx[8:], t_stride_imc_ret[8:], color="#d97706", marker="d", s=38, zorder=5)

    # Annotate 12c IMC early cliff masked by RFO->ItoM
    ax1.annotate(
        f"12核早期拐点：\n逻辑 STREAM 96%\n实测 IMC 已跌 -22.1%\n({t_stride_imc_gb[4]:.1f} GB/s)",
        xy=(tx[4], t_stride_imc_ret[4]), xytext=(1.9, 53), ha="center", va="center",
        fontsize=7.8, fontweight="bold", color="#92400e",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#fffbeb", edgecolor="#d97706", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#d97706", lw=1.2),
        zorder=7,
    )

    for idx in (3, 4, 5, 6, 7, 8, 9, 10):
        ret = t_stride_ret[idx]
        gb = t_stride_gb[idx]
        if idx == 3:
            ax1.text(tx[idx], ret - 5.5, f"{ret:.0f}%", ha="center", va="top",
                     fontsize=8.0, fontweight="bold", color="#b91c1c")
        elif idx == 4:
            ax1.text(tx[idx], ret + 4.0, f"{ret:.0f}%", ha="center", va="bottom",
                     fontsize=8.0, fontweight="bold", color="#b91c1c")
        elif idx == 5:
            ax1.text(tx[idx] + 0.14, ret + 4.5, f"{ret:.0f}%\n({gb:.1f}G)", ha="left", va="bottom",
                     fontsize=8.0, fontweight="bold", color="#b91c1c")
        elif idx == 6:
            ax1.text(tx[idx] + 0.08, ret + 6.5, f"{ret:.1f}%\n({gb:.1f}G)", ha="center", va="bottom",
                     fontsize=7.7, fontweight="bold", color="#991b1b")
        elif idx == 7:
            ax1.text(tx[idx] - 0.10, ret + 5.5, f"{ret:.1f}%\n({gb:.1f}G)", ha="center", va="bottom",
                     fontsize=7.7, fontweight="bold", color="#991b1b")
        elif idx == 8:
            ax1.text(tx[idx], ret + 7.5, f"{ret:.1f}%\n({gb:.1f}G)", ha="center", va="bottom",
                     fontsize=7.6, fontweight="bold", color="#991b1b")
        elif idx == 9:
            ax1.text(tx[idx], ret + 21.0, f"{ret:.1f}%\n({gb:.1f}G)", ha="center", va="bottom",
                     fontsize=7.5, fontweight="bold", color="#991b1b")
            ax1.annotate("", xy=(tx[idx], ret + 2.5), xytext=(tx[idx], ret + 19.5),
                         arrowprops=dict(arrowstyle="-", color="#dc2626", lw=0.8, alpha=0.6))
        elif idx == 10:
            ax1.text(tx[idx], ret + 6.0, f"{ret:.1f}%\n({gb:.1f}G)", ha="center", va="bottom",
                     fontsize=7.6, fontweight="bold", color="#991b1b")

    ax1.set_ylim(0, 136)
    ax1.set_xlim(-0.5, len(t_topos) - 0.45)
    ax1.set_xticks(tx)
    ax1.set_xticklabels([t[1] for t in t_topos], fontsize=7.9)
    ax1.set_ylabel(f"带宽保留率（STREAM 基线 {t_base:.1f} / IMC 基线 {imc_base:.1f} GB/s = 100%）", fontsize=9.2)
    ax1.set_title("A. 邻居 6 核 STREAM 与实测 IMC：12 核已跌 -22%，16 核断崖崩塌", fontsize=10.1, fontweight="bold", pad=10)
    ax1.legend(loc="lower left", fontsize=7.4, framealpha=0.95)

    # ---- Panel B: 16 MiB L3 pointer chase ----
    ax2.axvline(8.5, color="#94a3b8", linestyle="-.", lw=1.0, alpha=0.75, zorder=1)
    ax2.axvspan(4.5, 12.6, color="#fef2f2", alpha=0.50, zorder=0)

    ax2.plot(lx_spin[:6], l_spin_p99[:6], color="#64748b", linestyle="--", marker="s", lw=1.7, markersize=5.2,
             label="核内空转降频对照 (l1-spin)", zorder=3)
    ax2.scatter(lx_spin[6:], l_spin_p99[6:], color="#64748b", marker="s", s=32, zorder=3)

    ax2.plot(lx_fix[:6], l_fix_p99[:6], color="#0f766e", linestyle=":", marker="^", lw=1.8, markersize=5.8,
             label="64 B 单行狂发 (noop-fixed，31核 136 Mpps 网格对照)", zorder=4)
    ax2.scatter([lx_fix[6]], [l_fix_p99[6]], color="#0f766e", marker="^", s=38, zorder=4)

    ax2.plot(lx[:9], l_stride_p99[:9], color="#dc2626", marker="o", lw=2.5, markersize=6.8,
             label="4 KiB (64 行) 空包狂发 (noop-stride)", zorder=5)
    ax2.scatter(lx[9:], l_stride_p99[9:], color="#dc2626", marker="D", s=48, zorder=6)

    for idx in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
        v = l_stride_p99[idx]
        lbl_str = f"{v:.0f}" if v >= 100 else f"{v:.1f}"
        if idx == 3:
            ax2.text(lx[idx], v * 0.72, lbl_str, ha="center", va="top",
                     fontsize=8.0, fontweight="bold", color="#b91c1c")
        elif idx == 4:
            ax2.text(lx[idx] - 0.16, v * 1.18, lbl_str, ha="right", va="bottom",
                     fontsize=8.0, fontweight="bold", color="#b91c1c")
        elif idx == 9:
            ax2.text(lx[idx], v * 0.70, lbl_str, ha="center", va="top",
                     fontsize=8.0, fontweight="bold", color="#b91c1c")
        else:
            ax2.text(lx[idx], v * 1.24, lbl_str, ha="center", va="bottom",
                     fontsize=8.0, fontweight="bold", color="#b91c1c")

    ax2.set_yscale("log")
    ymax = max(l_stride_p99) * 2.6
    ax2.set_ylim(18, max(ymax, 6000))
    ax2.set_xlim(-0.5, len(l_topos) - 0.45)
    ax2.set_xticks(lx)
    ax2.set_xticklabels([t[1] for t in l_topos], fontsize=7.9)
    ax2.set_ylabel("16 MiB L3 追逐 p99 尾延迟 (ns，对数坐标，K=32 窗口)", fontsize=9.4)
    ax2.set_title("B. 邻居 16 MiB L3 尾延迟：一路推至 31 核 / 62t 暴涨数十至百倍", fontsize=10.1, fontweight="bold", pad=10)
    ax2.legend(loc="upper left", fontsize=7.8, framealpha=0.95)

    ax2.text(6.6, ax2.get_ylim()[1] * 0.85, "纯物理核扩展 (含 31 核)", ha="center", va="top",
             fontsize=8.2, color="#475569", fontweight="bold")
    ax2.text(10.5, ax2.get_ylim()[1] * 0.85, "SMT（离散点）", ha="center", va="top",
             fontsize=8.2, color="#475569", fontweight="bold")

    fig.suptitle(
        "MRV-246：16–32 核下 4 KiB NOOP Portal 对同 Socket 邻居的真实杀伤（严格隔离物理核，n = 3 中位数）",
        fontsize=11.4, fontweight="bold", y=0.99,
    )
    png = mrvplot.save(fig, "mrv-246-neighbor-impact")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
