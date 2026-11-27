#!/usr/bin/env python3
"""MRV-228 Figure 2: Scenario B (16–64 MiB wire-speed elephant bursts + 200 krps healthy RPCs
+ co-located 32 MiB KV tenant).

100% dynamically computed from `logs/mrv228-2026-11-27/runs.jsonl`.
Shows:
  - Panel A: Pareto showdown (Healthy RPC p99 vs co-located 32 MiB KV p99, 64 MiB burst)
  - Panel B: 16 MiB vs 64 MiB burst healthy RPC p99 + verified 0.00% loss across all 5 arms
  - Panel C: Co-located 32 MiB KV p99 / p999 & host DDIO hit rate (64 MiB burst, F = 256)
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

from matplotlib.lines import Line2D
import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np

import mrvplot

REPO = Path(__file__).resolve().parents[2]
RUNS_PATH = REPO / "logs" / "mrv228-2026-11-27" / "runs.jsonl"

ARMS = [
    ("per_flow_cr", "逐流信用环（本方案）", "#0072B2", "o"),
    ("bp1_stop", "BP=1 停抽（护缓存）", "#D55E00", "s"),
    ("bp1_drain", "BP=1 抽干到主机", "#E69F00", "^"),
    ("large_sdp", "大环直通 (BP=0)", "#64748B", "D"),
    ("ceio", "CEIO-style (双路径切道)", "#7E57C2", "P"),
]

SHORT_LABELS = {
    "per_flow_cr": "逐流信用环\n(本方案)",
    "bp1_stop": "BP=1 停抽\n(护缓存)",
    "ceio": "CEIO-style\n(双路径)",
    "bp1_drain": "BP=1 抽干\n到主机",
    "large_sdp": "大环直通\n(BP=0)",
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


def fmt_val_us(v):
    if v >= 1000:
        return f"{v/1000:.1f}ms"
    return f"{v:.0f}µs"


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
    assert total == 168
    return by_key


def main():
    by = load_verified()
    solo = by[("S0", "kv_solo", "kv_solo")]
    kv_solo_p99 = med([r["host"]["coloc_kv"]["p99_ns"] for r in solo])
    kv_solo_p999 = med([r["host"]["coloc_kv"]["p999_ns"] for r in solo])

    scen_b_losses = [
        r["client_loss_pct"]
        for (part, _, _), rs in by.items()
        if part in ("B1_FLOWS", "B2_BURST16")
        for r in rs
    ]
    max_b_loss = max(scen_b_losses)

    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(16.4, 5.8), gridspec_kw={"width_ratios": [1.15, 1.05, 1.12]}
    )

    # ------------------------------------------------------------------
    # Panel A: Pareto (Healthy RPC p99 vs Co-located 32 MiB KV p99)
    # ------------------------------------------------------------------
    win_box = patches.FancyBboxPatch(
        (50, 20), 110, 42,
        boxstyle="round,pad=0.02,rounding_size=4",
        facecolor="#DCFCE7", edgecolor="#16A34A", linewidth=1.2, linestyle="--", alpha=0.65, zorder=1,
    )
    ax1.add_patch(win_box)
    ax1.text(55, 68, "双赢区", fontsize=9.2, fontweight="bold", color="#15803D")

    ax1.axhline(kv_solo_p99, color="#475569", linestyle="--", linewidth=1.0, alpha=0.65, zorder=1)
    ax1.axvline(100.0, color="#16A34A", linestyle=":", linewidth=1.1, alpha=0.7, zorder=1)

    legend_handles = []
    legend_labels = []
    pts_256 = {}

    for arm, label, color, marker in ARMS:
        rs = by[("B1_FLOWS", "B1_F256_b64m", arm)]
        rx = med([r["client"]["rpc_p99_us"] for r in rs])
        ky = med([r["host"]["coloc_kv"]["p99_ns"] for r in rs])
        pts_256[arm] = (rx, ky)
        # Keep both large_sdp (diamond) and bp1_drain (triangle) clearly visible at (96 us, 231/235 ns)
        if arm == "per_flow_cr":
            sz, zo = 190, 8
        elif arm == "large_sdp":
            sz, zo = 175, 5
        elif arm == "bp1_drain":
            sz, zo = 105, 7
        else:
            sz, zo = 135, 6
        sc = ax1.scatter(
            [rx], [ky], s=sz,
            color=color, marker=marker, edgecolors="#0F172A", linewidths=1.0,
            zorder=zo, label=label,
        )
        legend_handles.append(sc)
        legend_labels.append(label)

    # CEIO trajectory F=64 -> F=256 -> F=1024
    ce_64_x = med([r["client"]["rpc_p99_us"] for r in by[("B1_FLOWS", "B1_F64_b64m", "ceio")]])
    ce_64_y = med([r["host"]["coloc_kv"]["p99_ns"] for r in by[("B1_FLOWS", "B1_F64_b64m", "ceio")]])
    ce_1024_x = med([r["client"]["rpc_p99_us"] for r in by[("B1_FLOWS", "B1_F1024_b64m", "ceio")]])
    ce_1024_y = med([r["host"]["coloc_kv"]["p99_ns"] for r in by[("B1_FLOWS", "B1_F1024_b64m", "ceio")]])
    ax1.scatter([ce_64_x], [ce_64_y], s=75, facecolors="white", edgecolors="#7E57C2", marker="P", linewidths=1.5, zorder=4)
    ax1.scatter([ce_1024_x], [ce_1024_y], s=90, facecolors="#E9D5FF", edgecolors="#7E57C2", marker="P", linewidths=1.4, zorder=4)
    ax1.annotate(
        "", xy=(pts_256["ceio"][0] * 0.76, pts_256["ceio"][1] + 4), xytext=(ce_64_x * 1.25, ce_64_y + 10),
        arrowprops=dict(arrowstyle="->", color="#7E57C2", lw=1.3, ls="--", connectionstyle="arc3,rad=-0.16"),
    )

    ax1.set_xscale("log")
    ax1.set_xlim(42, 75000)
    ax1.set_xticks([50, 100, 500, 1000, 5000, 30000])
    ax1.xaxis.set_major_formatter(FuncFormatter(fmt_us))
    ax1.set_ylim(0, 300)
    ax1.set_xlabel("健康短 RPC 尾延迟 p99（对数轴，越左越好）", fontsize=10.8, labelpad=6)
    ax1.set_ylabel("共置 32 MiB KV 尾延迟 p99（ns，越低越好）", fontsize=10.8)
    ax1.set_title("A. 帕累托总决战（64 MiB 突发，F = 256）", fontsize=12.0, fontweight="bold", pad=10)
    ax1.tick_params(axis="both", labelsize=9.8)
    ax1.grid(True, which="major", alpha=0.28)
    ax1.grid(False, which="minor")

    pf_x, pf_y = pts_256["per_flow_cr"]
    bp_x, bp_y = pts_256["bp1_stop"]
    bd_x, bd_y = pts_256["bp1_drain"]
    ls_x, ls_y = pts_256["large_sdp"]
    ce_x, ce_y = pts_256["ceio"]

    ax1.annotate(
        f"逐流信用环（本方案）\n{pf_x:.0f} µs / {pf_y:.0f} ns（F=16–1,024 全稳）",
        xy=(pf_x * 1.05, pf_y + 5), xytext=(220, 145),
        fontsize=8.9, fontweight="bold", color="#005B8E",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#E0F2FE", edgecolor="#0072B2", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#0072B2", lw=1.2, connectionstyle="arc3,rad=0.14"),
        zorder=9,
    )
    ax1.annotate(
        f"BP=1 抽干 / 大环直通\n{bd_x:.0f} µs，但 KV 炸至 {ls_y:.0f}–{bd_y:.0f} ns ({bd_y/kv_solo_p99:.1f}×)",
        xy=(bd_x * 1.12, bd_y), xytext=(360, 245),
        fontsize=8.8, fontweight="bold", color="#B45309",
        bbox=dict(boxstyle="round,pad=0.26", facecolor="#FFFBEB", edgecolor="#E69F00", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#E69F00", lw=1.1),
        zorder=9,
    )
    ax1.annotate(
        f"BP=1 停抽：KV 保 {bp_y:.0f} ns\nRPC 堵至 {bp_x/1000:.1f} ms ({bp_x/pf_x:.0f}×)",
        xy=(bp_x, bp_y + 6), xytext=(2200, 186),
        fontsize=8.7, fontweight="bold", color="#C2410C",
        bbox=dict(boxstyle="round,pad=0.26", facecolor="#FFF7ED", edgecolor="#D55E00", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#D55E00", lw=1.1, connectionstyle="arc3,rad=-0.12"),
        zorder=9,
    )
    ax1.text(
        2100, 92,
        f"CEIO：F≤64 仅 {ce_64_x:.0f} µs\nF=256 飙 {ce_x/1000:.1f} ms / F=1024 飙 {ce_1024_x/1000:.1f} ms",
        fontsize=8.3, fontweight="bold", color="#6D28D9", ha="center",
        bbox=dict(boxstyle="round,pad=0.22", facecolor="#FAF5FF", edgecolor="#C4B5FD", lw=0.8),
        zorder=8,
    )

    # ------------------------------------------------------------------
    # Panel B: 16 MiB vs 64 MiB burst Healthy RPC p99 (F = 256)
    # ------------------------------------------------------------------
    b_order = ["per_flow_cr", "bp1_drain", "large_sdp", "ceio", "bp1_stop"]
    color_map = {a[0]: a[2] for a in ARMS}
    xb = np.arange(len(b_order))
    w = 0.37

    b16_m, b16_lo, b16_hi = [], [], []
    b64_m, b64_lo, b64_hi = [], [], []
    for arm in b_order:
        m16, l16, h16 = med_err([r["client"]["rpc_p99_us"] for r in by[("B2_BURST16", "B2_F256_b16m", arm)]])
        m64, l64, h64 = med_err([r["client"]["rpc_p99_us"] for r in by[("B1_FLOWS", "B1_F256_b64m", arm)]])
        b16_m.append(m16); b16_lo.append(l16); b16_hi.append(h16)
        b64_m.append(m64); b64_lo.append(l64); b64_hi.append(h64)

    ax2.bar(
        xb - w / 2, b16_m, width=w,
        color=[color_map[a] for a in b_order], alpha=0.45,
        edgecolor="#1E293B", linewidth=0.8, hatch="///", zorder=3,
    )
    ax2.bar(
        xb + w / 2, b64_m, width=w,
        color=[color_map[a] for a in b_order], alpha=0.95,
        edgecolor="#1E293B", linewidth=0.8, zorder=3,
    )
    ax2.errorbar(xb - w / 2, b16_m, yerr=[b16_lo, b16_hi], fmt="none", ecolor="#0F172A", elinewidth=1.0, capsize=2.6, zorder=4)
    ax2.errorbar(xb + w / 2, b64_m, yerr=[b64_lo, b64_hi], fmt="none", ecolor="#0F172A", elinewidth=1.0, capsize=2.6, zorder=4)

    ax2.axhline(100.0, color="#16A34A", linestyle=":", linewidth=1.2, alpha=0.85, zorder=2)
    ax2.set_yscale("log")
    ax2.set_ylim(35, 130000)
    ax2.set_yticks([50, 100, 500, 1000, 5000, 10000, 50000])
    ax2.yaxis.set_major_formatter(FuncFormatter(fmt_us))
    ax2.set_xticks(xb)
    ax2.set_xticklabels([SHORT_LABELS[a] for a in b_order], fontsize=9.0)
    ax2.set_ylabel("健康短 RPC 尾延迟 p99（对数轴）", fontsize=10.8)
    ax2.set_title("B. 16 MiB vs 64 MiB 突发：RPC 尾延迟与丢包", fontsize=12.0, fontweight="bold", pad=10)
    ax2.tick_params(axis="y", labelsize=9.8)
    ax2.grid(True, axis="y", which="major", alpha=0.28)
    ax2.grid(False, which="minor")
    ax2.grid(False, axis="x")

    p16 = patches.Patch(facecolor="#94A3B8", alpha=0.45, hatch="///", edgecolor="#1E293B", label="16 MiB 突发")
    p64 = patches.Patch(facecolor="#475569", alpha=0.95, edgecolor="#1E293B", label="64 MiB 突发")
    ax2.legend(handles=[p16, p64], loc="upper left", fontsize=8.8, frameon=True, facecolor="white", edgecolor="#CBD5E1")
    # Place 0.00% loss badge in the wide open empty pocket over bars 1 & 2 (x = 0.44, y = 0.96)
    ax2.text(
        0.53, 0.96, f"场景 B 丢包率\n5 档均 {max_b_loss:.2f}%",
        transform=ax2.transAxes, ha="center", va="top", fontsize=8.8, fontweight="bold", color="#15803D",
        bbox=dict(boxstyle="round,pad=0.26", facecolor="#DCFCE7", edgecolor="#16A34A", lw=0.9),
    )

    for i, (v16, h16, v64, h64, arm) in enumerate(zip(b16_m, b16_hi, b64_m, b64_hi, b_order)):
        if v16 < 200 and v64 < 200:
            # Stagger tiny 100us labels cleanly so left/right bars never collide
            ax2.text(i - w / 2, (v16 + h16) * 1.48, fmt_val_us(v16), ha="center", va="bottom", fontsize=7.6, color="#475569")
            ax2.text(
                i + w / 2, (v64 + h64) * 1.12, fmt_val_us(v64),
                ha="center", va="bottom", fontsize=8.0,
                fontweight="bold", color="#005B8E" if arm == "per_flow_cr" else "#0F172A",
            )
        else:
            ax2.text(i - w / 2, (v16 + h16) * 1.18, fmt_val_us(v16), ha="center", va="bottom", fontsize=7.8, color="#334155")
            ax2.text(
                i + w / 2, (v64 + h64) * 1.18, fmt_val_us(v64),
                ha="center", va="bottom", fontsize=8.2, fontweight="bold", color="#0F172A",
            )

    # ------------------------------------------------------------------
    # Panel C: Co-located KV p99 / p999 + DDIO hit rate (64 MiB, F = 256)
    # ------------------------------------------------------------------
    c_order = ["per_flow_cr", "bp1_stop", "ceio", "bp1_drain", "large_sdp"]
    xc = np.arange(len(c_order))

    kv99_m, kv99_lo, kv99_hi, kv999_m, ddio_m = [], [], [], [], []
    for arm in c_order:
        rs = by[("B1_FLOWS", "B1_F256_b64m", arm)]
        m99, l99, h99 = med_err([r["host"]["coloc_kv"]["p99_ns"] for r in rs])
        kv99_m.append(m99); kv99_lo.append(l99); kv99_hi.append(h99)
        kv999_m.append(med([r["host"]["coloc_kv"]["p999_ns"] for r in rs]))
        ddio_m.append(med([r["host"]["pmu"]["ddio_hit_pct"] for r in rs]))

    ax3.bar(
        xc, kv999_m, width=0.56,
        color=[color_map[a] for a in c_order], alpha=0.25,
        edgecolor=[color_map[a] for a in c_order], linewidth=1.1, linestyle="--", zorder=2,
    )
    ax3.bar(
        xc, kv99_m, width=0.56,
        color=[color_map[a] for a in c_order], alpha=0.92,
        edgecolor="#1E293B", linewidth=0.8, zorder=3,
    )
    ax3.errorbar(xc, kv99_m, yerr=[kv99_lo, kv99_hi], fmt="none", ecolor="#0F172A", elinewidth=1.1, capsize=3.0, zorder=4)

    ax3.axhline(kv_solo_p99, color="#334155", linestyle="--", linewidth=1.1, alpha=0.85, zorder=2)
    ax3.axhline(kv_solo_p999, color="#64748B", linestyle=":", linewidth=1.0, alpha=0.75, zorder=2)

    ax3.set_ylim(0, 390)
    ax3.set_xticks(xc)
    ax3.set_xticklabels([SHORT_LABELS[a] for a in c_order], fontsize=9.0)
    ax3.set_ylabel("共置 32 MiB KV 尾延迟（ns，实心 p99 / 虚框 p999）", fontsize=10.5)
    ax3.set_title("C. 缓存隔离：KV p99/p999 与 DDIO 命中率", fontsize=12.0, fontweight="bold", pad=10)
    ax3.tick_params(axis="y", labelsize=9.8)
    ax3.grid(True, axis="y", alpha=0.28)
    ax3.grid(False, axis="x")

    leg_handles_c = [
        patches.Patch(facecolor="#475569", alpha=0.92, edgecolor="#1E293B", label="KV p99（实心）"),
        patches.Patch(facecolor="#475569", alpha=0.25, edgecolor="#475569", linestyle="--", label="KV p999（虚框）"),
        Line2D([0], [0], color="#334155", linestyle="--", lw=1.1, label=f"空载 p99={kv_solo_p99:.0f} ns"),
        Line2D([0], [0], color="#64748B", linestyle=":", lw=1.1, label=f"空载 p999={kv_solo_p999:.0f} ns"),
    ]
    ax3.legend(handles=leg_handles_c, loc="upper left", fontsize=8.3, frameon=True, facecolor="white", edgecolor="#CBD5E1")

    for i, (arm, v99, h99, v999, ddio) in enumerate(zip(c_order, kv99_m, kv99_hi, kv999_m, ddio_m)):
        if v99 >= 80:
            ax3.text(i, v99 * 0.5, f"p99\n{v99:.0f} ns", ha="center", va="center", fontsize=8.6, fontweight="bold", color="white", zorder=5)
        else:
            ax3.text(i, v99 * 0.5, f"{v99:.0f}", ha="center", va="center", fontsize=8.6, fontweight="bold", color="white", zorder=5)
        top_ref = max(v999, v99 + h99)
        ax3.text(i, top_ref + 6, f"p999: {v999:.0f}", ha="center", va="bottom", fontsize=8.0, color="#334155")
        if ddio >= 99.0:
            bg, fg, ec = "#DCFCE7", "#15803D", "#16A34A"
        elif ddio >= 50.0:
            bg, fg, ec = "#FEF3C7", "#B45309", "#D97706"
        else:
            bg, fg, ec = "#FEE2E2", "#B91C1C", "#DC2626"
        ax3.text(
            i, top_ref + 36, f"DDIO\n{ddio:.1f}%",
            ha="center", va="bottom", fontsize=8.2, fontweight="bold", color=fg,
            bbox=dict(boxstyle="round,pad=0.22", facecolor=bg, edgecolor=ec, lw=0.9),
        )

    fig.suptitle(
        "图 2 · 场景 B（大象突发）：5 档同台对比健康 RPC、丢包率、DDIO 与共置 KV，唯有逐流信用环进双赢区",
        fontsize=14.0, fontweight="bold", y=0.975, color="#0F172A",
    )
    fig.text(
        0.5, 0.918,
        "4 条硬件队列遭遇 16–64 MiB 线速大象突发（主机 16 Gbps 消费）+ 200 krps 健康短 RPC + 同 Socket 32 MiB KV（n = 3 中位数及极差）",
        ha="center", va="center", fontsize=10.2, color="#475569",
    )
    fig.legend(
        legend_handles, legend_labels,
        loc="upper center", bbox_to_anchor=(0.5, 0.892),
        ncol=5, frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1",
        fontsize=10.2, handletextpad=0.5, columnspacing=1.5,
    )

    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.815])
    png = mrvplot.HERE / "mrv-228-fig2-scenario-b-burst.png"
    svg = mrvplot.HERE / "mrv-228-fig2-scenario-b-burst.svg"
    fig.savefig(png, dpi=200)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} ({png.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
