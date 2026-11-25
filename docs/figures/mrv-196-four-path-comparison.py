"""MRV-196 Figure 2: Four-path physical isolation comparison at ~160 Mpps (10.24 GB/s):
MOVDIR64B -> BAR4 MMIO vs Host Cacheable Ring + CN106 DPI FromHost Pull (16 MiB DDIO/LLC
and 256 MiB DRAM) vs Socket 1 Host-RAM Cacheable Ring (RAM-WB) vs NT-Store (RAM-NT),
with 100% non-multiplexed IMC R/W GB/s, CHA_TOR_OCC_ALL, and L3 miss hardware ledger.

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


def deg_pct(by_lbl, vtag, aggr):
    base_items = by_lbl[f"{vtag}__idle"]
    items = by_lbl[f"{vtag}__{aggr}"]
    assert len(items) == 3, (vtag, aggr, len(items))
    if items[0]["chase"]:
        b = med([r["chase"]["net_mean_ns"] for r in base_items])
        vals = [(r["chase"]["net_mean_ns"] - b) / b * 100.0 for r in items]
    else:
        b = med([r["stream"]["mean_gb_s"] for r in base_items])
        vals = [(b - r["stream"]["mean_gb_s"]) / b * 100.0 for r in items]
    return med(vals), min(vals), max(vals)


def main():
    by_lbl = load_verified()

    paths = [
        ("bar4_1c_160m_b512", "BAR4 MMIO\n1核 @ 160m"),
        ("bar4_1c_max_b512", "BAR4 MMIO\n1核满速 ~175m"),
        ("dpi_pull_16m_1c_160m", "主存+DPI 16M\nDDIO热环 (1核)"),
        ("ram_nt_256m_1c_160m", "主存 NT 256M\n绕LLC流写 (1核)"),
        ("ram_wb_256m_1c_160m", "主存 WB 256M\nRFO+回写 (1核*)"),
        ("ram_wb_256m_4c_160m", "主存 WB 256M\n4核推齐 160m"),
        ("dpi_pull_256m_1c_160m", "主存+DPI 256M\n跨DRAM (1核*)"),
        ("dpi_pull_256m_4c_160m", "主存+DPI 256M\n4核推齐 160m"),
    ]
    x = np.arange(len(paths))
    xlabels = [p[1] for p in paths]

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(16.4, 6.9), gridspec_kw={"width_ratios": [1.22, 1.0]}
    )

    # ---- Panel A: Grouped degradation bars across 4 victims ----
    ax1.axhspan(-3.0, 5.0, color="#dafbe1", alpha=0.55, zorder=0)
    ax1.axhline(5.0, color="#1a7f37", linestyle="--", lw=1.1, zorder=1)
    ax1.axhline(0.0, color="#57606a", lw=0.8, zorder=1)
    ax1.text(0.02, 5.7, "假设阈值线 (+5% 退化)", fontsize=8.2, color="#116329", fontweight="bold")

    victims = [
        ("dram", "DRAM 256M 延迟上升 (%)", "#0969da"),
        ("t1c", "STREAM 1核 带宽下降 (%)", "#e16f24"),
        ("t6c", "STREAM 6核 带宽下降 (%)", "#cf222e"),
        ("llc", "LLC 16M 延迟上升 (%)", "#8250df"),
    ]
    w = 0.19
    offsets = [-1.5 * w, -0.5 * w, 0.5 * w, 1.5 * w]
    for (vtag, vlbl, col), off in zip(victims, offsets):
        ys, lo, hi = [], [], []
        for aggr, _ in paths:
            m, mn, mx = deg_pct(by_lbl, vtag, aggr)
            ys.append(m)
            lo.append(max(0.0, m - mn))
            hi.append(max(0.0, mx - m))
        ax1.bar(
            x + off, ys, width=w, color=col, alpha=0.90, label=vlbl, zorder=3,
            yerr=[lo, hi], capsize=2.2, error_kw={"lw": 0.9, "ecolor": "#1f2328"},
        )
        if vtag in ("t6c", "dram"):
            for xi, yv in zip(x + off, ys):
                if abs(yv) >= 1.5 or xi < 1.5:
                    va = "bottom" if yv >= 0 else "top"
                    dy = 0.9 if yv >= 0 else -0.8
                    ax1.text(
                        xi, yv + dy, f"{yv:+.1f}%", ha="center", va=va,
                        fontsize=7.0, color=col, fontweight="bold", rotation=90 if abs(yv) > 4 else 0,
                    )

    ax1.set_xticks(x)
    ax1.set_xticklabels(xlabels, fontsize=8.2, rotation=20, ha="right")
    ax1.set_ylim(-5.5, 51.5)
    ax1.set_ylabel("同 Socket 共置 Victim 相对静默基线退化幅度 (%)", fontsize=9.6)
    ax1.set_title("A. 同等 ~160 Mpps 载荷下四路物理通路对共置应用的干扰对比", fontsize=10.6, fontweight="bold", pad=10)
    ax1.legend(loc="upper left", fontsize=8.4, framealpha=0.95)
    ax1.text(
        0.015, 0.61,
        "*注：① 6 核 STREAM 饱和双通道 DDR5 (~58~61 GB/s) 时，\n"
        "  1 核主存 WB / DPI-256M 写线程自身亦被 IMC 反压掉速至\n"
        "  ~58~64 Mpps（卡端 DPI 降至 ~7.0 GB/s）；4 核推齐\n"
        "  160 Mpps 后 6 核 STREAM 退化扩大至 -32.5%~-41.0%。\n"
        "  ② 主存 WB 4 核组有 1 轮触发 LLC 14% 驱逐 (+45% 极值)，\n"
        "  BAR4 MMIO 绕开 LLC，全程 L3 Miss 增量为 0%。",
        transform=ax1.transAxes, fontsize=7.6, va="center",
        bbox=dict(boxstyle="round,pad=0.36", facecolor="#f6f8fa", edgecolor="#d0d7de"),
    )

    # ---- Panel B: Hardware PMU Ledger under DRAM-chase victim ----
    imc_rd = [med([r["imc"]["rd_gb_s"] for r in by_lbl[f"dram__{a}"]]) for a, _ in paths]
    imc_wr = [med([r["imc"]["wr_gb_s"] for r in by_lbl[f"dram__{a}"]]) for a, _ in paths]
    l3_miss = [med([r["victim_core_pmu"]["l3_load_miss_pct"] for r in by_lbl[f"dram__{a}"]]) for a, _ in paths]
    idle_l3m = med([r["victim_core_pmu"]["l3_load_miss_pct"] for r in by_lbl["dram__idle"]])

    bw = 0.54
    ax2.bar(x, imc_rd, width=bw, color="#0969da", alpha=0.88, label="Socket 1 IMC 读带宽 (GB/s)", zorder=3)
    ax2.bar(x, imc_wr, bottom=imc_rd, width=bw, color="#e16f24", alpha=0.88, label="Socket 1 IMC 写带宽 (GB/s)", zorder=3)
    for i, (r_v, w_v) in enumerate(zip(imc_rd, imc_wr)):
        tot = r_v + w_v
        ax2.text(i, tot + 0.6, f"{tot:.1f}", ha="center", va="bottom", fontsize=8.0, fontweight="bold", color="#1f2328")

    ax2.set_xticks(x)
    ax2.set_xticklabels(xlabels, fontsize=8.2, rotation=20, ha="right")
    ax2.set_ylim(0, 34.5)
    ax2.set_ylabel("Socket 1 双通道 DDR5 IMC 实测带宽 (GB/s，共置 DRAM 追逐)", fontsize=9.4)
    ax2.set_title("B. 硬件账本：BAR4 绕开 IMC 与 LLC，主存/DPI 路径直接消耗 DDR5", fontsize=10.6, fontweight="bold", pad=10)

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.spines["right"].set_visible(True)
    ax2_r.plot(x, l3_miss, color="#1f2328", marker="D", lw=1.8, markersize=5.2,
               label="Victim DRAM 追逐 L3 Load Miss (%)", zorder=5)
    ax2_r.axhline(idle_l3m, color="#6e7781", linestyle=":", lw=1.1)
    ax2_r.text(-0.35, 86.6, f"虚线 = 静默基线 L3 Miss ({idle_l3m:.1f}%)", fontsize=7.9, color="#57606a", ha="left",
               bbox=dict(boxstyle="round,pad=0.22", facecolor="white", edgecolor="#d0d7de", alpha=0.92))
    ax2_r.set_ylim(84, 102.5)
    ax2_r.set_ylabel("Victim L3 Load Miss (%)", fontsize=9.4, color="#1f2328")
    ax2_r.grid(False)

    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2_r.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8.0, framealpha=0.95)

    fig.suptitle(
        "MRV-196 图 2：四路物理通路同速率（~160 Mpps / 10.24 GB/s）干扰与 IMC/LLC 硬件账本对比（n = 3 交错中位数）",
        fontsize=12.0, fontweight="bold", y=0.99,
    )
    fig.subplots_adjust(top=0.89, bottom=0.19, wspace=0.26)
    png = mrvplot.save(fig, "mrv-196-four-path-comparison")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
