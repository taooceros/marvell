#!/usr/bin/env python3
"""MRV-228 Figure 1: Scenario A (minority slow flow stall + healthy 200 krps short RPCs).

100% dynamically computed from `logs/mrv228-2026-11-27/runs.jsonl`.
Shows:
  - Panel A: Healthy RPC p99 across F = 16 / 64 / 256 / 1,024 flows (4 ms stall)
             with explicit sent-load ratio disclosure for `large_sdp` (BP=0)
  - Panel B: Healthy RPC p99 across stall durations 1 / 4 / 10 ms (F = 256)
  - Panel C: Co-located 32 MiB KV p99 & host DRAM bandwidth (F = 256, 4 ms stall)
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

from matplotlib.lines import Line2D
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np

import mrvplot

REPO = Path(__file__).resolve().parents[2]
RUNS_PATH = REPO / "logs" / "mrv228-2026-11-27" / "runs.jsonl"

ARMS = [
    ("per_flow_cr", "逐流信用环（本方案）", "#0072B2", "o", "-", 2.8, 7.5, 6),
    ("bp1_stop", "BP=1 停抽（护缓存）", "#D55E00", "s", "-", 2.2, 6.5, 5),
    ("bp1_drain", "BP=1 抽干到主机", "#E69F00", "^", "-", 2.2, 6.8, 4),
    ("large_sdp", "大环直通 (BP=0)", "#64748B", "D", "--", 2.0, 6.0, 3),
    ("ceio", "CEIO-style (双路径切道)", "#7E57C2", "P", "-", 2.2, 7.0, 4),
]

SHORT_LABELS = {
    "per_flow_cr": "逐流信用环\n(本方案)",
    "bp1_stop": "BP=1 停抽\n(护缓存)",
    "ceio": "CEIO-style\n(双路径)",
    "large_sdp": "大环直通\n(BP=0)",
    "bp1_drain": "BP=1 抽干\n到主机",
}


def med(xs):
    return statistics.median(xs)


def med_err(xs):
    m = med(xs)
    return m, m - min(xs), max(xs) - m


def fmt_us(v, _pos=None):
    if v >= 1000:
        ms = v / 1000.0
        return f"{ms:g} ms"
    return f"{v:g} µs"


def load_verified():
    by_key = defaultdict(list)
    total = 0
    for ln in RUNS_PATH.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        total += 1
        assert r["accepted"] is True
        assert r["csr_pre"] == r["csr_post"]
        assert r["host"]["pmu"]["min_running_pct"] >= 99.95
        assert r["host"]["pmu"]["page_faults"] == 0
        assert r["host"]["coloc_kv"]["bad"] == 0
        assert r["host"]["coloc_kv"]["page_faults"] == 0
        if r["arm"] != "kv_solo":
            assert r["client"]["bad"] == 0
            assert r["client"]["torn"] == 0
            assert r["client"]["ooo"] == 0
            assert r["card_pmu"]["min_pct"] >= 99.95
            assert r["merc_phy_delta"]["rx_crc_errors_phy"] == 0
            assert r["merc_phy_delta"]["tx_global_pause"] == 0
            assert r["merc_phy_delta"]["rx_global_pause"] == 0
            if r["arm"] != "large_sdp":
                assert r["client"]["lost"] == 0
        by_key[(r["part"], r["tag"], r["arm"])].append(r)
    assert total == 168, f"Expected 168 verified runs, got {total}"
    return by_key


def main():
    by = load_verified()
    kv_solo_p99 = med([r["host"]["coloc_kv"]["p99_ns"] for r in by[("S0", "kv_solo", "kv_solo")]])

    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(16.4, 5.8), gridspec_kw={"width_ratios": [1.22, 1.0, 1.08]}
    )

    # ------------------------------------------------------------------
    # Panel A: F = 16 / 64 / 256 / 1,024 @ 4 ms stall (Healthy RPC p99)
    # ------------------------------------------------------------------
    flows = [16, 64, 256, 1024]
    xf = np.arange(len(flows))
    legend_handles = []
    legend_labels = []

    for arm, label, color, marker, ls, lw, ms, z in ARMS:
        ys, lo, hi = [], [], []
        for f in flows:
            vals = [r["client"]["rpc_p99_us"] for r in by[("A1_FLOWS", f"A1_F{f}_s4ms", arm)]]
            m, e_lo, e_hi = med_err(vals)
            ys.append(m)
            lo.append(e_lo)
            hi.append(e_hi)
        eb = ax1.errorbar(
            xf, ys, yerr=[lo, hi],
            color=color, marker=marker, linestyle=ls, linewidth=lw,
            markersize=ms, capsize=3.5, elinewidth=1.2,
            markeredgecolor="white", markeredgewidth=0.7, zorder=z, label=label,
        )
        legend_handles.append(eb)
        legend_labels.append(label)

    ax1.axhline(100.0, color="#16A34A", linestyle=":", linewidth=1.3, alpha=0.85, zorder=2)
    ax1.text(-0.24, 114.0, "100 µs SLO 线", fontsize=8.8, color="#15803D", fontweight="bold")

    ax1.set_yscale("log")
    ax1.set_ylim(22, 68000)
    ax1.set_yticks([50, 100, 500, 1000, 5000, 10000, 30000])
    ax1.yaxis.set_major_formatter(FuncFormatter(fmt_us))
    ax1.set_xticks(xf)
    ax1.set_xticklabels(["16", "64", "256", "1,024"], fontsize=10.5)
    ax1.set_xlim(-0.32, 3.34)
    ax1.set_xlabel("总并发流数 F（共享 4 条硬件队列）", fontsize=11.0, labelpad=6)
    ax1.set_ylabel("健康短 RPC 尾延迟 p99（对数轴）", fontsize=11.0)
    ax1.set_title("A. 多流扩展（4 ms 卡顿）：健康 RPC 尾延迟", fontsize=12.0, fontweight="bold", pad=10)
    ax1.tick_params(axis="both", labelsize=10.0)
    ax1.grid(True, which="major", alpha=0.28)
    ax1.grid(False, which="minor")

    # Dynamic callouts on Panel A (including exact sent/offered ratio for large_sdp!)
    pf_1024 = med([r["client"]["rpc_p99_us"] for r in by[("A1_FLOWS", "A1_F1024_s4ms", "per_flow_cr")]])
    bp_1024 = med([r["client"]["rpc_p99_us"] for r in by[("A1_FLOWS", "A1_F1024_s4ms", "bp1_stop")]])
    bd_256 = med([r["client"]["rpc_p99_us"] for r in by[("A1_FLOWS", "A1_F256_s4ms", "bp1_drain")]])
    ce_1024 = med([r["client"]["rpc_p99_us"] for r in by[("A1_FLOWS", "A1_F1024_s4ms", "ceio")]])
    ls_losses = [med([r["client_loss_pct"] for r in by[("A1_FLOWS", f"A1_F{f}_s4ms", "large_sdp")]]) for f in flows]
    ls_load_pcts = [
        100.0
        * med([r["client"]["sent"] for r in by[("A1_FLOWS", f"A1_F{f}_s4ms", "large_sdp")]])
        / med([r["client"]["sent"] for r in by[("A1_FLOWS", f"A1_F{f}_s4ms", "per_flow_cr")]])
        for f in flows
    ]

    ax1.annotate(
        f"本方案全程 {pf_1024:.0f} µs\n比 BP=1 停抽快 {bp_1024/pf_1024:.0f}×（0 丢包）",
        xy=(1.0, pf_1024), xytext=(0.15, 200),
        fontsize=8.9, fontweight="bold", color="#005B8E",
        bbox=dict(boxstyle="round,pad=0.26", facecolor="#E0F2FE", edgecolor="#0072B2", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#0072B2", lw=1.1, connectionstyle="arc3,rad=0.12"),
        zorder=8,
    )
    ax1.text(
        1.48, bd_256 * 1.48,
        f"BP=1 抽干：{bd_256/1000:.1f} ms（缓存冲穿后池满反触背压）",
        fontsize=8.7, fontweight="bold", color="#B45309", ha="center",
    )
    ax1.text(
        1.15, bp_1024 * 1.32,
        f"BP=1 停抽：{bp_1024/1000:.2f} ms（整队列堵死）",
        fontsize=8.9, fontweight="bold", color="#C2410C", ha="center",
    )
    ax1.annotate(
        f"大环直通：丢包 {min(ls_losses):.1f}%–{max(ls_losses):.1f}%\n仅发 {min(ls_load_pcts):.1f}%–{max(ls_load_pcts):.1f}% 负载(幸存包)",
        xy=(0.0, med([r["client"]["rpc_p99_us"] for r in by[("A1_FLOWS", "A1_F16_s4ms", "large_sdp")]])),
        xytext=(-0.25, 620),
        fontsize=8.2, color="#334155", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.24", facecolor="#F1F5F9", edgecolor="#64748B", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#64748B", lw=1.0),
        zorder=8,
    )
    ax1.annotate(
        f"CEIO >256 流\n快表溢出 → {ce_1024/1000:.2f} ms",
        xy=(2.95, ce_1024 * 0.92), xytext=(1.38, 720),
        fontsize=8.5, fontweight="bold", color="#6D28D9",
        bbox=dict(boxstyle="round,pad=0.24", facecolor="#F3E8FF", edgecolor="#7E57C2", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#7E57C2", lw=1.1, connectionstyle="arc3,rad=-0.12"),
        zorder=8,
    )

    # ------------------------------------------------------------------
    # Panel B: Stall duration sweep (1 / 4 / 10 ms @ F = 256)
    # ------------------------------------------------------------------
    stalls = [
        ("A2_STALL", "A2_F256_s1ms", "1 ms"),
        ("A1_FLOWS", "A1_F256_s4ms", "4 ms"),
        ("A2_STALL", "A2_F256_s10ms", "10 ms"),
    ]
    xs = np.arange(len(stalls))

    for arm, label, color, marker, ls, lw, ms, z in ARMS:
        ys, lo, hi = [], [], []
        for part, tag, _ in stalls:
            vals = [r["client"]["rpc_p99_us"] for r in by[(part, tag, arm)]]
            m, e_lo, e_hi = med_err(vals)
            ys.append(m)
            lo.append(e_lo)
            hi.append(e_hi)
        ax2.errorbar(
            xs, ys, yerr=[lo, hi],
            color=color, marker=marker, linestyle=ls, linewidth=lw,
            markersize=ms, capsize=3.5, elinewidth=1.2,
            markeredgecolor="white", markeredgewidth=0.7, zorder=z,
        )

    ax2.axhline(100.0, color="#16A34A", linestyle=":", linewidth=1.3, alpha=0.85, zorder=2)
    ax2.set_yscale("log")
    ax2.set_ylim(22, 68000)
    ax2.set_yticks([50, 100, 500, 1000, 5000, 10000, 30000])
    ax2.yaxis.set_major_formatter(FuncFormatter(fmt_us))
    ax2.set_xticks(xs)
    ax2.set_xticklabels([s[2] for s in stalls], fontsize=10.5)
    ax2.set_xlim(-0.32, 2.34)
    ax2.set_xlabel("慢流卡顿持续时长（F = 256）", fontsize=11.0, labelpad=6)
    ax2.set_title("B. 卡顿扫频：BP=1 线性黑屏 vs 本方案免疫", fontsize=12.0, fontweight="bold", pad=10)
    ax2.tick_params(axis="both", labelsize=10.0)
    ax2.grid(True, which="major", alpha=0.28)
    ax2.grid(False, which="minor")

    bp_1 = med([r["client"]["rpc_p99_us"] for r in by[("A2_STALL", "A2_F256_s1ms", "bp1_stop")]])
    bp_10 = med([r["client"]["rpc_p99_us"] for r in by[("A2_STALL", "A2_F256_s10ms", "bp1_stop")]])
    pf_1 = med([r["client"]["rpc_p99_us"] for r in by[("A2_STALL", "A2_F256_s1ms", "per_flow_cr")]])
    pf_10 = med([r["client"]["rpc_p99_us"] for r in by[("A2_STALL", "A2_F256_s10ms", "per_flow_cr")]])

    ax2.annotate(
        f"BP=1 停抽随卡顿线性飙升\n{bp_1/1000:.2f} → {bp_10/1000:.2f} ms（慢 {bp_10/pf_10:.0f}×）",
        xy=(1.95, bp_10 * 0.88), xytext=(0.12, 340),
        fontsize=8.8, fontweight="bold", color="#C2410C",
        bbox=dict(boxstyle="round,pad=0.26", facecolor="#FFF7ED", edgecolor="#D55E00", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#D55E00", lw=1.1, connectionstyle="arc3,rad=0.18"),
        zorder=8,
    )
    ax2.text(
        1.0, 29.0,
        f"本方案稳守 {pf_1:.0f}–{pf_10:.0f} µs（卡顿期 200 krps 零掉速）",
        fontsize=8.8, fontweight="bold", color="#005B8E", ha="center",
    )

    # ------------------------------------------------------------------
    # Panel C: Co-located 32 MiB KV p99 & host DRAM BW (F = 256, 4 ms)
    # ------------------------------------------------------------------
    c_order = ["per_flow_cr", "bp1_stop", "ceio", "large_sdp", "bp1_drain"]
    color_map = {a[0]: a[2] for a in ARMS}
    xc = np.arange(len(c_order))

    ref_sent_256 = med([r["client"]["sent"] for r in by[("A1_FLOWS", "A1_F256_s4ms", "per_flow_cr")]])
    kv_meds, kv_lo, kv_hi, imc_meds, loss_meds, load_pcts = [], [], [], [], [], []
    for arm in c_order:
        rs = by[("A1_FLOWS", "A1_F256_s4ms", arm)]
        m, e_lo, e_hi = med_err([r["host"]["coloc_kv"]["p99_ns"] for r in rs])
        kv_meds.append(m)
        kv_lo.append(e_lo)
        kv_hi.append(e_hi)
        imc_meds.append(med([r["host"]["pmu"]["imc_total_gbps"] for r in rs]))
        loss_meds.append(med([r["client_loss_pct"] for r in rs]))
        load_pcts.append(100.0 * med([r["client"]["sent"] for r in rs]) / ref_sent_256)

    ax3.bar(
        xc, kv_meds, width=0.56,
        color=[color_map[a] for a in c_order],
        edgecolor="#1E293B", linewidth=0.8, alpha=0.92, zorder=3,
    )
    ax3.errorbar(
        xc, kv_meds, yerr=[kv_lo, kv_hi],
        fmt="none", ecolor="#0F172A", elinewidth=1.2, capsize=3.5, zorder=4,
    )
    ax3.axhline(kv_solo_p99, color="#334155", linestyle="--", linewidth=1.2, alpha=0.85, zorder=2)

    solo_handle = Line2D([0], [0], color="#334155", linestyle="--", lw=1.3, label=f"空载 32 MiB KV 基线（{kv_solo_p99:.0f} ns）")
    ax3.legend(handles=[solo_handle], loc="upper left", fontsize=8.8, frameon=True, facecolor="white", edgecolor="#CBD5E1")

    ax3.set_ylim(0, 340)
    ax3.set_xticks(xc)
    ax3.set_xticklabels([SHORT_LABELS[a] for a in c_order], fontsize=9.2)
    ax3.set_ylabel("共置 32 MiB KV 尾延迟 p99（ns，越低越好）", fontsize=11.0)
    ax3.set_title("C. 主机缓存隔离：共置 KV p99（F = 256）", fontsize=12.0, fontweight="bold", pad=10)
    ax3.tick_params(axis="y", labelsize=10.0)
    ax3.grid(True, axis="y", alpha=0.28)
    ax3.grid(False, axis="x")

    for i, (arm, kv, hi, imc, loss, lpct) in enumerate(zip(c_order, kv_meds, kv_hi, imc_meds, loss_meds, load_pcts)):
        top = kv + hi
        if arm == "bp1_drain":
            ax3.text(
                i, top + 6,
                f"{kv:.0f} ns ({kv/kv_solo_p99:.1f}×)\n主存 {imc:.1f} GB/s\n({lpct:.0f}% 实发)",
                ha="center", va="bottom", fontsize=8.4, fontweight="bold", color="#B45309",
            )
        elif arm == "large_sdp":
            ax3.text(
                i, top + 6,
                f"{kv:.0f} ns\n丢包 {loss:.1f}%\n(仅 {lpct:.1f}% 负载)",
                ha="center", va="bottom", fontsize=8.3, fontweight="bold", color="#475569",
            )
        else:
            ax3.text(
                i, top + 6,
                f"{kv:.0f} ns\n{imc:.2f} GB/s",
                ha="center", va="bottom", fontsize=8.7,
                fontweight="bold" if arm == "per_flow_cr" else "normal",
                color="#005B8E" if arm == "per_flow_cr" else "#1E293B",
            )

    fig.suptitle(
        "图 1 · 场景 A（慢流卡顿）：原生 BP=1 陷入「堵死健康流」或「冲穿缓存」两难，逐流信用环双赢",
        fontsize=14.2, fontweight="bold", y=0.975, color="#0F172A",
    )
    fig.text(
        0.5, 0.918,
        "4 条标准 SDP 队列共享 16–1,024 条并发流（每队列 1 条 48 Gbps 慢流周期卡顿 + 其余 200 krps 健康短 RPC，n = 3 中位数及极差）",
        ha="center", va="center", fontsize=10.2, color="#475569",
    )
    fig.legend(
        legend_handles, legend_labels,
        loc="upper center", bbox_to_anchor=(0.5, 0.892),
        ncol=5, frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1",
        fontsize=10.2, handlelength=2.2, columnspacing=1.5,
    )

    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.815])
    png = mrvplot.HERE / "mrv-228-fig1-scenario-a-hol.png"
    svg = mrvplot.HERE / "mrv-228-fig1-scenario-a-hol.svg"
    fig.savefig(png, dpi=200)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} ({png.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
