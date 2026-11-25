"""MRV-196 Figure 1: Headline 3-second verdict comparing 4 regimes on co-located
Socket-1 neighbor memory performance (6-core STREAM Triad bandwidth drop and
LLC 16 MiB p99 latency).

Reads raw verified n=3 runs directly from `logs/mrv196-2026-11-25/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv196-2026-11-25/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def load_verified():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    by_lbl = defaultdict(list)
    for r in rows:
        assert r["accepted"] is True
        assert r["host_pmu_min_pct"] == 100.0
        assert r["page_faults"] == 0
        assert r["victim_page_faults"] == 0
        assert r["aggr_page_faults"] == 0
        assert r["ram_verify_errors"] == 0
        if r["chase"]:
            assert r["chase"]["bad_nodes"] == 0
        if r["stream"]:
            assert r["stream"]["verify_errors"] == 0
        if r["aggr"] == "bar4":
            assert r["card_pmu"]["min_pct"] == 100.0
            cv = r["card_verify"]
            assert cv["zero_lines"] == 0 and cv["torn_lines"] == 0 and cv["bad_lines"] == 0
        if r["aggr"] == "dpi-pull":
            assert r["card_pmu"]["min_pct"] == 100.0
            ds = r["dpi_stats"]
            assert ds["ok"] is True and ds["failed"] == 0 and ds["copies"] > 0
        by_lbl[r["label"]].append(r)
    return by_lbl


def main():
    by_lbl = load_verified()

    t6c_base = med([r["stream"]["mean_gb_s"] for r in by_lbl["t6c__idle"]])
    llc_p99_base = med([r["chase"]["p99_ns"] for r in by_lbl["llc__idle"]])
    dram_base = med([r["chase"]["net_mean_ns"] for r in by_lbl["dram__idle"]])

    # 5 clean rows covering the 4 regimes
    rows_spec = [
        ("bar4_1c_max_b512", "1 核满速 MMIO\n(175 Mpps)", "#1a7f37", "ok"),
        ("bar4_8c_max_b512", "8 核无控超发 MMIO\n(撞死 175.5 Mpps 上限)", "#cf222e", "cliff"),
        ("bar4_8c_165m_b128", "8 核限速 + 小步长\n(165 Mpps, b=128)", "#1a7f37", "ok"),
        ("dpi_pull_16m_1c_160m", "传统 DMA · 16M 环\n(冲刷 L3 缓存)", "#d97706", "dma"),
        ("dpi_pull_256m_1c_160m", "传统 DMA · 256M 环*\n(抢双通道内存)", "#d97706", "dma256"),
    ]

    t6c_4c_dpi = med([r["stream"]["mean_gb_s"] for r in by_lbl["t6c__dpi_pull_256m_4c_160m"]])
    t6c_4c_drop = (t6c_base - t6c_4c_dpi) / t6c_base * 100.0

    y = np.arange(len(rows_spec))
    bw_drops, bw_gbs, llc_p99s, dram_incs, colors = [], [], [], [], []
    for aggr, _, col, _ in rows_spec:
        gb = med([r["stream"]["mean_gb_s"] for r in by_lbl[f"t6c__{aggr}"]])
        drop = (t6c_base - gb) / t6c_base * 100.0
        p99 = med([r["chase"]["p99_ns"] for r in by_lbl[f"llc__{aggr}"]])
        dr = med([r["chase"]["net_mean_ns"] for r in by_lbl[f"dram__{aggr}"]])
        dr_inc = (dr - dram_base) / dram_base * 100.0
        bw_drops.append(drop)
        bw_gbs.append(gb)
        llc_p99s.append(p99)
        dram_incs.append(dr_inc)
        colors.append(col)

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(13.8, 5.6), sharey=True, gridspec_kw={"width_ratios": [1.08, 1.0]}
    )

    bar_h = 0.54

    # ---- Left Panel: 6-core memory bandwidth drop (shorter = better) ----
    ax1.axvline(5.0, color="#1a7f37", linestyle="--", lw=1.2, alpha=0.8, zorder=1)
    ax1.text(5.8, -0.46, "5% 干扰阈值线", fontsize=8.5, color="#1a7f37", va="center", ha="left")
    ax1.grid(axis="y", visible=False)
    ax1.grid(axis="x", alpha=0.28)

    for i, (aggr, _, col, kind) in enumerate(rows_spec):
        val = max(0.0, bw_drops[i])
        if kind == "dma256":
            # Light extension showing 4-core 160 Mpps matched rate
            ax1.barh(i, t6c_4c_drop, height=bar_h, color="#fed7aa", edgecolor="#d97706",
                     lw=1.0, linestyle="--", zorder=2)
            ax1.barh(i, val, height=bar_h, color=col, alpha=0.92, zorder=3)
            ax1.text(
                t6c_4c_drop + 1.4, i,
                f"-{val:.1f}% (1核*)\n-{t6c_4c_drop:.1f}% (4核推齐)",
                va="center", ha="left", fontsize=9.2, fontweight="bold", color="#b45309",
            )
        else:
            draw_w = max(val, 0.5) if kind == "ok" else val
            ax1.barh(i, draw_w, height=bar_h, color=col, alpha=0.90, zorder=3)
            if kind == "ok":
                tag = "几乎零干扰" if i == 0 else "恢复零干扰"
                sign_txt = f"{-bw_drops[i]:+.1f}%" if abs(bw_drops[i]) >= 0.05 else "0.0%"
                ax1.text(
                    6.4, i, f"{sign_txt}  ({tag})",
                    va="center", ha="left", fontsize=9.8, fontweight="bold", color="#116329",
                )
            elif kind == "cliff":
                ax1.text(
                    val + 1.4, i, f"-{val:.1f}%",
                    va="center", ha="left", fontsize=11.0, fontweight="bold", color="#a40e26",
                )
            else:
                ax1.text(
                    val + 1.4, i, f"-{val:.1f}%",
                    va="center", ha="left", fontsize=10.2, fontweight="bold", color="#b45309",
                )

    ax1.set_xlim(0, 88)
    ax1.set_xticks([0, 20, 40, 60, 80])
    ax1.set_xticklabels(["0%", "-20%", "-40%", "-60%", "-80%"], fontsize=9.5)
    ax1.set_yticks(y)
    ax1.set_yticklabels([r[1] for r in rows_spec], fontsize=10.0, fontweight="bold")
    ax1.invert_yaxis()
    ax1.set_xlabel("邻居 6 核内存带宽下降（相对空载 49.8 GB/s，越短越好）", fontsize=9.8, labelpad=8)
    ax1.set_title("A. 邻居内存带宽：正常与限速 MMIO 几乎不掉速", fontsize=11.2, fontweight="bold", pad=12)

    # ---- Right Panel: LLC p99 tail latency (shorter = better) ----
    ax2.axvline(llc_p99_base, color="#6e7781", linestyle=":", lw=1.2, zorder=1)
    ax2.text(llc_p99_base + 14, -0.46, f"空载基线 {llc_p99_base:.1f} ns", fontsize=8.5, color="#57606a", va="center")
    ax2.grid(axis="y", visible=False)
    ax2.grid(axis="x", alpha=0.28)

    for i, (aggr, _, col, kind) in enumerate(rows_spec):
        p99 = llc_p99s[i]
        bar_col = "#64748b" if kind in ("dma", "dma256") else col
        ax2.barh(i, p99, height=bar_h, color=bar_col, alpha=0.88, zorder=3)
        if kind == "cliff":
            ratio = p99 / llc_p99_base
            ax2.text(
                p99 + 14, i, f"{p99:.0f} ns  ({ratio:.0f}x 暴涨)",
                va="center", ha="left", fontsize=10.8, fontweight="bold", color="#a40e26",
            )
        elif kind == "ok":
            ax2.text(
                p99 + 14, i, f"{p99:.1f} ns  (无变化)",
                va="center", ha="left", fontsize=9.6, fontweight="bold", color="#116329",
            )
        else:
            ax2.text(
                p99 + 14, i, f"{p99:.1f} ns   (但主存延迟 +{dram_incs[i]:.1f}%)",
                va="center", ha="left", fontsize=9.3, color="#b45309", fontweight="bold",
            )
    ax2.set_xlim(0, 820)
    ax2.set_xticks([0, 200, 400, 600])
    ax2.set_xticklabels(["0", "200 ns", "400 ns", "600 ns"], fontsize=9.5)
    ax2.set_xlabel("邻居 L3 缓存尾延迟 p99（16 MiB 随机读，越短越好）", fontsize=9.8, labelpad=8)
    ax2.set_title("B. 邻居 L3 尾延迟：仅多核无控超发时暴涨 20 倍", fontsize=11.2, fontweight="bold", pad=12)

    fig.suptitle(
        "MRV-196 核心结论：单核满速写网卡（175 Mpps）对同 CPU 邻居几乎零干扰，远优于传统主存 DMA",
        fontsize=12.6, fontweight="bold", y=0.965,
    )
    fig.text(
        0.5, 0.018,
        "*注：本机 Socket 1 为 2 通道 DDR5 (~60 GB/s)，256M 环抢内存通道的降幅仅适用于双通道（8 通道 ~240 GB/s 未实测）；"
        "1 核 256M 环同时被内存反压掉至 58 Mpps。其余与通道数无关。",
        ha="center", va="bottom", fontsize=8.3, color="#57606a",
    )
    fig.tight_layout(rect=[0.0, 0.075, 1.0, 0.92])
    png = mrvplot.HERE / "mrv-196-headline-verdict.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-196-headline-verdict.svg")
    plt.close(fig)
    print(f"Saved {png}")

if __name__ == "__main__":
    main()
