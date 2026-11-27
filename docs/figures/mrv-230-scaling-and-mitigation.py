"""MRV-230 Figure 3: (A) 1-8 core scaling (non-monotonic 4c DMA-starvation
rebound and 8c CHA collapse) and (B) 8-core IIO0 1.00 GHz half-duplex cycle
conservation (7.00 * R_portal + 1.65 * R_dma_pair ~= 1000 Mcycles/s) plus
mitigation comparison (SFENCE cadence vs single-line doorbell vs credit).

Reads raw verified n=3 runs directly from:
  - logs/mrv230-2026-11-27/runs.jsonl
  - logs/mrv230-2026-11-27/diag_runs.jsonl
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
M230_MAIN = ROOT / "logs/mrv230-2026-11-27/runs.jsonl"
M230_DIAG = ROOT / "logs/mrv230-2026-11-27/diag_runs.jsonl"
M196_MAIN = ROOT / "logs/mrv196-2026-11-25/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def load_verified(paths):
    by = defaultdict(list)
    for p in paths:
        for ln in p.read_text().splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            assert r["accepted"] is True and r["host_pmu_min_pct"] == 100.0
            assert r["page_faults"] == 0 and r["victim_page_faults"] == 0 and r["aggr_page_faults"] == 0
            if "drain_ok" in r:
                assert r["drain_ok"] is True and r["dsa_verify_errors"] == 0 and r["dsa2_pmu"]["cpl_err"] == 0
            if r["stream"]:
                assert r["stream"]["verify_errors"] == 0
            if r["chase"]:
                assert r["chase"]["bad_nodes"] == 0
            by[r["label"]].append(r)
    return by


def main():
    m230 = load_verified([M230_MAIN, M230_DIAG])
    m196 = load_verified([M196_MAIN])

    cores = [0, 1, 2, 4, 8]
    x_pos = np.arange(len(cores))
    x_lbl = ["空载\n0 核", "1 核", "2 核", "4 核", "8 核"]

    def series_m230(prefix):
        vals = [med([r["stream"]["mean_gb_s"] for r in m230["t6c__idle"]])]
        for nc in (1, 2, 4, 8):
            vals.append(med([r["stream"]["mean_gb_s"] for r in m230[f"t6c__{prefix}_{nc}c_b512"]]))
        return vals

    l1_bw = series_m230("l1-spin")
    noop_bw = series_m230("noop-stride")
    mm_bw = series_m230("memmove-64k-stride")
    cred_bw = series_m230("memmove-64k-credit")

    bar4_bw = [
        med([r["stream"]["mean_gb_s"] for r in m196["t6c__idle"]]),
        med([r["stream"]["mean_gb_s"] for r in m196["t6c__bar4_1c_max_b512"]]),
        med([r["stream"]["mean_gb_s"] for r in m196["t6c__bar4_2c_max_b512"]]),
        med([r["stream"]["mean_gb_s"] for r in m196["t6c__bar4_4c_max_b512"]]),
        med([r["stream"]["mean_gb_s"] for r in m196["t6c__bar4_8c_max_b512"]]),
    ]

    # Verify IIO0 1.00 GHz half-duplex cycle conservation across all saturated stride + llc/dram single_8c runs
    cons_errs = []
    for lb, rs in m230.items():
        if "wcilf" in lb:
            continue
        if ("stride" in lb) or lb.startswith(("llc__memmove-64k-single_8c", "dram__memmove-64k-single_8c")):
            for r in rs:
                rp = r["iio0"]["cpu_wr_mpps"]
                dp = r["iio0"]["dma_rd_dw"] / 16.0 / r["wall_s"] / 1e6
                ghz = r["iio0"]["ghz"]
                cons_errs.append(abs(7.00 * rp + 1.65 * dp - ghz * 1000.0) / (ghz * 1000.0) * 100.0)
    cons_max_err = max(cons_errs)
    cons_med_err = med(cons_errs)
    assert cons_max_err < 1.5, f"IIO0 cycle conservation max err {cons_max_err:.2f}% >= 1.5%"
    t6c_single_mpps = med([r["aggr_mpps"] for r in m230["t6c__memmove-64k-single_8c_b512"]])
    t6c_single_dma = med([r["iio0"]["dma_rd_gb_s"] for r in m230["t6c__memmove-64k-single_8c_b512"]])
    t6c_single_cyc = med([
        7.00 * r["iio0"]["cpu_wr_mpps"] + 1.65 * (r["iio0"]["dma_rd_dw"] / 16.0 / r["wall_s"] / 1e6)
        for r in m230["t6c__memmove-64k-single_8c_b512"]
    ])
    llc_single_mpps = med([r["aggr_mpps"] for r in m230["llc__memmove-64k-single_8c_b512"]])
    llc_single_dma = med([r["iio0"]["dma_rd_gb_s"] for r in m230["llc__memmove-64k-single_8c_b512"]])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.4, 6.2), gridspec_kw={"width_ratios": [1.04, 1.02]})

    # ---- Panel A: 1-8 core scaling ----
    ax1.plot(x_pos, l1_bw, color="#64748b", linestyle="--", marker="s", lw=1.8, markersize=5.5,
             label="同核数空转 (l1-spin, 3.9→3.5 GHz 不掉速)", zorder=3)
    ax1.plot(x_pos, noop_bw, color="#1a7f37", marker="o", lw=2.4, markersize=6.8,
             label="DSA 空包狂发 (noop-stride, 64 行窄窗)", zorder=4)
    ax1.plot(x_pos, mm_bw, color="#dc2626", marker="D", lw=2.5, markersize=6.8,
             label="DSA 64K 灌爆 (memmove-64k-stride)", zorder=5)
    ax1.plot(x_pos, cred_bw, color="#d97706", linestyle="-.", marker="^", lw=1.9, markersize=6.0,
             label="DSA 64K 守信用 (19.2 GB/s 双向 MEMMOVE*)", zorder=3)
    ax1.plot(x_pos, bar4_bw, color="#881337", linestyle=":", marker="x", lw=2.2, markersize=7.0,
             label="对照：CN106 无控 BAR4 宽窗 (MRV-196)", zorder=4)

    for i in (1, 2, 3, 4):
        if i == 1:
            ax1.text(x_pos[i] + 0.14, mm_bw[i] - 2.2, f"{mm_bw[i]:.1f}", ha="left", va="top",
                     fontsize=8.6, fontweight="bold", color="#b91c1c")
        else:
            dy = -3.4 if i == 2 else (2.4 if i == 3 else 2.6)
            va = "top" if dy < 0 else "bottom"
            ax1.text(x_pos[i], mm_bw[i] + dy, f"{mm_bw[i]:.1f}", ha="center", va=va,
                     fontsize=8.6, fontweight="bold", color="#b91c1c")
    ax1.text(4.0, noop_bw[4] + 1.8, f"{noop_bw[4]:.1f}", ha="center", va="bottom",
             fontsize=8.6, fontweight="bold", color="#116329")
    ax1.text(4.0, bar4_bw[4] - 2.4, f"{bar4_bw[4]:.1f}", ha="center", va="top",
             fontsize=8.6, fontweight="bold", color="#881337")

    ax1.annotate(
        "4 核 109 Mpps 抢占 ~77% IIO0 周期\n反噬饿死 DSA 自身 DMA (19→8.9 GB/s)\n内存释放，带宽暂反弹至 35.3",
        xy=(3, mm_bw[3]), xytext=(1.62, 39.0), ha="center", va="center",
        fontsize=7.9, fontweight="bold", color="#991b1b",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="white", edgecolor="#dc2626", alpha=0.94),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.2),
    )
    ax1.annotate(
        "8 核在内存仅 41.5G 时再砸回 28.2\n(纯攻击 CHA 489 跨崖；b=0 跌至 26.1)",
        xy=(4, mm_bw[4]), xytext=(2.98, 10.6), ha="center", va="center",
        fontsize=7.5, fontweight="bold", color="#991b1b",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#dc2626", alpha=0.94),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.2),
    )

    ax1.set_ylim(2, 60)
    ax1.set_xticks(x_pos)
    ax1.set_xticklabels(x_lbl, fontsize=9.0)
    ax1.set_ylabel("邻居 6 核 STREAM 内存带宽 (GB/s)", fontsize=9.8)
    ax1.set_title("A. 1–8 核扩展：NOOP 始终平线，64K 灌爆先抢内存→4核反噬→8核片上拥塞", fontsize=10.1, fontweight="bold", pad=10)
    ax1.legend(loc="lower left", fontsize=7.4, framealpha=0.95)

    # ---- Panel B: 8-core self-harm & mitigation (Portal Mpps + Effective DSA DMA GB/s) ----
    mit_specs = [
        ("noop-stride_8c_b512", "NOOP\n8核轮写"),
        ("memmove-64k-stride_x64B_8c_b512", "64B 灌爆\n95% 丢"),
        ("memmove-64k-stride_8c_b0", "64K 灌爆\nb=0 无fence"),
        ("memmove-64k-stride_8c_b512", "64K 灌爆\nb=512"),
        ("memmove-64k-stride_8c_b128", "64K 灌爆\nb=128"),
        ("memmove-64k-single_8c_b512", "64K 单行门铃\nsingle 8核†"),
        ("memmove-64k-credit_8c_b512", "64K 守信用\ncredit 8核"),
    ]
    x2 = np.arange(len(mit_specs))
    mpps_v, dma_v, llc_p99_v = [], [], []
    for key, _ in mit_specs:
        # Use LLC runs to show uncontended DSA DMA throughput (30.6 GB/s ceiling) and clean L3 p99
        lr = m230[f"llc__{key}"]
        mpps_v.append(med([r["aggr_mpps"] for r in lr]))
        dma_v.append(med([r["iio0"]["dma_rd_gb_s"] for r in lr]))
        llc_p99_v.append(med([r["chase"]["p99_ns"] for r in lr]))

    w = 0.36
    ax2.axvspan(1.55, 4.45, color="#fef2f2", alpha=0.55, zorder=0)
    ax2.axvspan(4.55, 6.45, color="#ecfdf5", alpha=0.60, zorder=0)

    ax2.bar(x2 - w / 2, mpps_v, width=w, color="#3b82f6", alpha=0.86, label="门户写入速率 (Mpps，左轴，L3 轮)", zorder=3)
    for i, mv in enumerate(mpps_v):
        ax2.text(x2[i] - w / 2, mv + 2.6, f"{mv:.0f}" if mv >= 1 else f"{mv:.1f}",
                 ha="center", va="bottom", fontsize=8.0, fontweight="bold", color="#1d4ed8")

    ax2.set_ylim(0, 248)
    ax2.set_xticks(x2)
    ax2.set_xticklabels([s[1] for s in mit_specs], fontsize=8.0)
    ax2.set_ylabel("8 核门户写入速率 (Mpps，L3 轮)", fontsize=9.6, color="#1d4ed8")
    ax2.set_title("B. 8 核 IIO0 周期守恒与缓解：狂发自砍 DMA 77%；单行/守信用保住 30G DMA", fontsize=9.9, fontweight="bold", pad=10)

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.spines["right"].set_visible(True)
    dma_cols = ["#94a3b8", "#94a3b8", "#dc2626", "#dc2626", "#ea580c", "#16a34a", "#16a34a"]
    ax2_r.bar(x2 + w / 2, dma_v, width=w, color=dma_cols, alpha=0.90, label="DSA 有效 DMA 单向吞吐 (GB/s，右轴，L3 轮)", zorder=3)
    for i, dv in enumerate(dma_v):
        if dv >= 0.5:
            ax2_r.text(x2[i] + w / 2, dv + 0.7, f"{dv:.1f}G", ha="center", va="bottom",
                       fontsize=8.1, fontweight="bold", color=dma_cols[i])

    ax2_r.set_ylim(0, 52)
    ax2_r.set_ylabel("DSA 有效 DMA 单向吞吐 (GB/s，读写各计)", fontsize=9.4, color="#15803d")
    ax2_r.grid(False)

    ax2.text(3.0, 152, "SFENCE (b=0/128/512) 无效：\n入向 110–116M 抢走 ~80% IIO0 周期\nDMA 跌至 7–9G，STREAM 掉 -44%~-48%",
             ha="center", va="center", fontsize=7.5, fontweight="bold", color="#991b1b",
             bbox=dict(boxstyle="round,pad=0.24", facecolor="white", edgecolor="#dc2626", alpha=0.94))
    ax2.text(5.12, 194, "单行门铃 / 守信用：\n保住 30G DMA 与 L3 p99 (29 ns)\n但 MEMMOVE 2×主存仍吃满 2 通道*",
             ha="center", va="center", fontsize=7.4, fontweight="bold", color="#116329",
             bbox=dict(boxstyle="round,pad=0.24", facecolor="white", edgecolor="#16a34a", alpha=0.94))
    ax2.text(
        3.0, 232,
        f"IIO0 1.00 GHz 饱和周期守恒：7.00·R_portal + 1.65·R_dma_rd ≈ 1000 Mcycles/s (147 饱和轮中位差 {cons_med_err:.2f}% / 最大 {cons_max_err:.2f}%†)",
        ha="center", va="center", fontsize=7.1, fontweight="bold", color="#1e3a8a",
        bbox=dict(boxstyle="round,pad=0.24", facecolor="#eff6ff", edgecolor="#3b82f6", alpha=0.95),
        zorder=7,
    )

    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2_r.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="upper left", bbox_to_anchor=(0.0, 0.90), fontsize=7.4, framealpha=0.95)

    fig.suptitle(
        "MRV-230 多核扩展与 IIO0 周期守恒：8 核狂发抢占端口周期自砍 DMA；单行/守信用保住 DMA 与 L3",
        fontsize=11.3, fontweight="bold", y=0.974,
    )
    fig.text(
        0.5, 0.008,
        f"*注：MEMMOVE 1 GB/s 载荷消耗 2 GB/s 主存（读+写），故单行/守信用在双通道 DDR5 (~63.7 GB/s) 上将 STREAM 压至 21.7 GB/s (-56.5%)；此机制反映片上 IIO0 半双工周期池，不同于独立 PCIe 全双工 SerDes。\n†守恒律覆盖全部 147 轮 IIO0 饱和点（全 stride + L3/主存 single-8核，R_dma_rd 按 dma_rd/64B 计）：L3 轮 single 8核达 {llc_single_mpps:.1f} Mpps + {llc_single_dma:.1f} GB/s（~1001 Mcycles/s）；\n  而 6 核 STREAM 轮 single 8核受 DDR5 限速仅 {t6c_single_dma:.1f} GB/s、门户止步单行串行 {t6c_single_mpps:.1f} Mpps（合计 ~{t6c_single_cyc:.0f} Mcycles/s，~15% IIO0 空闲），属 DRAM 受限点、落在饱和守恒线外。",
        ha="center", va="bottom", fontsize=7.4, color="#57606a",
    )
    fig.tight_layout(rect=[0.0, 0.105, 1.0, 0.935])
    png = mrvplot.HERE / "mrv-230-scaling-and-mitigation.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-230-scaling-and-mitigation.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
