"""MRV-166 Figure 1: Strict single-variable ablation at 4 KiB @ 1.0 Mreq/s (8.19 GB/s bidir).

Plots from raw logs/mrv166-2026-11-23/runs.jsonl (n = 3 interleaved, cpu40 @ 3.90 GHz,
Socket 1 uncore 2.20 GHz, CN106 core 3 @ 2.00 GHz, lost = bad = torn = 0, PMU 100%).
Decomposes each bar into active per-packet CPU vs empty-poll overhead measured in part0_zero.
"""
import collections
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

RUNS = Path(__file__).resolve().parents[2] / "logs/mrv166-2026-11-23/runs.jsonl"
rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]

idle_map = {r["rep"]: r["units_per_s"] for r in rows if r["part"] == "part0_cal"}
idle_cyc_u = {r["rep"]: r["host_pmu"]["cycles"] / r["units"] for r in rows if r["part"] == "part0_cal"}

by_cfg = collections.defaultdict(list)
for r in rows:
    assert r["verified"], f"unverified: {r['cfg']}"
    by_cfg[(r["part"], r["cfg"])].append(r)

# Empty-poll cycles per empty poll from part0_zero
ep_cyc_per_poll = {}
for zcfg in ("zero_sdp_default", "zero_sdp_sync", "zero_sdp_async", "zero_sdp_fair", "zero_directpush"):
    rs = by_cfg[("part0_zero", zcfg)]
    ep_cyc_per_poll[zcfg] = statistics.median(
        [(r["host_pmu"]["cycles"] - r["app_work_units"] * idle_cyc_u[r["rep"]]) / r["polls"] for r in rs]
    )


def stats_for(cfg, zcfg):
    rs = by_cfg[("part2_4k_ablation", cfg)]
    assert len(rs) == 3
    for r in rs:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
    net_pcts = [(1.0 - r["app_work_per_s"] / idle_map[r["rep"]]) * 100.0 for r in rs]
    tot_cycs = [
        (1.0 - r["app_work_per_s"] / idle_map[r["rep"]]) * r["host_pmu"]["cycles"] / r["measured"]
        for r in rs
    ]
    ep_cycs = [r["empty_polls"] / r["measured"] * ep_cyc_per_poll[zcfg] for r in rs]
    act_cycs = [t - e for t, e in zip(tot_cycs, ep_cycs)]
    late_med = statistics.median([r["late_pct"] for r in rs])
    ddio_med = statistics.median([r["host_pmu"]["ddio_hit_pct"] for r in rs])
    imcw_med = statistics.median([r["host_pmu"]["imc_cas_wr"] / r["measured"] for r in rs])
    p50_med = statistics.median([r["lat_all_us"]["p50"] for r in rs])
    return {
        "net_pcts": net_pcts,
        "net_med": statistics.median(net_pcts),
        "tot_cycs": tot_cycs,
        "tot_med": statistics.median(tot_cycs),
        "ep_med": statistics.median(ep_cycs),
        "act_med": statistics.median(act_cycs),
        "late": late_med,
        "ddio": ddio_med,
        "imcw": imcw_med,
        "p50": p50_med,
    }


ITEMS = [
    ("sdp_def_4096_r1000", "zero_sdp_default", "1. 默认 SDP\n(OUT_CNTS + copy)", "#6B7280", "#D1D5DB"),
    ("sdp_rpm_copy_4096_r1000", "zero_sdp_sync", "2. +rx_poll_mem=1\n(去 MMIO，仍 copy)", "#94A3B8", "#E2E8F0"),
    ("sdp_sync_4096_r1000", "zero_sdp_sync", "3. SDP (sync-tail)\n(+zero_copy，复现基线)", "#475569", "#CBD5E1"),
    ("sdp_async_4096_r1000", "zero_sdp_async", "4. SDP (async-tail)\n(唯一改包尾异步轮询)", "#0284C7", "#BAE6FD"),
    ("sdp_async_ism_4096_r1000", "zero_sdp_async", "5. +ism_enable=1\n(TX ISM 完成计数)", "#0369A1", "#BAE6FD"),
    ("sdp_async_ism_rx512_4096_r1000", "zero_sdp_fair", "6. +rx512 & 独立池\n(RX 环装进 DDIO)", "#0369A1", "#BAE6FD"),
    ("sdp_fair_4096_r1000", "zero_sdp_fair", "7. SDP (fair-full)\n(+自然 TX 批量，B=1)", "#1E3A8A", "#BFDBFE"),
    ("directpush_4096_r1000", "zero_directpush", "8. DirectPush (B=1)\n(原生 DPI DMA + 64B CQE)", "#0F62FE", "#93C5FD"),
    # Separator gap before B=8 sensitivity pair
    ("sdp_fair_b8_4096_r1000", "zero_sdp_fair", "9. SDP (fair-full, B=8)\n(每 8 包 1 次 TX 门铃)", "#334155", "#CBD5E1"),
    ("directpush_b8_4096_r1000", "zero_directpush", "10. DirectPush (B=8)\n(每 8 包 1 次 SFENCE)", "#0F62FE", "#93C5FD"),
]

y_pos = [0, 1, 2, 3, 4, 5, 6, 7, 8.5, 9.5]
data = [stats_for(cfg, zcfg) for cfg, zcfg, _, _, _ in ITEMS]

fig, ax = plt.subplots(figsize=(13.6, 7.6))

# Divider line for B=8 sensitivity
ax.axhline(7.75, color="#94A3B8", ls="--", lw=1.1, zorder=2)
ax.text(
    5550, 7.62, "▲ 平滑逐包到达 (pace_batch = 1)     ▼ 批量发包敏感性 (pace_batch = 8)",
    ha="right", va="bottom", fontsize=8.4, color="#475569", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.22", fc="#F8FAFC", ec="#CBD5E1"),
)

# Subtle highlight band on the single-variable sync -> async transition
ax.axhspan(2.55, 3.45, color="#F0F9FF", zorder=0)

for i, (cfg, _, label, c_act, c_ep) in enumerate(ITEMS):
    d = data[i]
    y = y_pos[i]
    # Active per-packet cycles bar
    ax.barh(y, d["act_med"], height=0.62, color=c_act, alpha=0.92, zorder=3)
    # Stacked empty-poll overhead bar
    ax.barh(
        y, d["ep_med"], left=d["act_med"], height=0.62, color=c_ep,
        edgecolor=c_act, hatch="///", lw=0.7, alpha=0.90, zorder=3,
    )
    # Individual n=3 replicate total dots
    ax.scatter(d["tot_cycs"], [y] * len(d["tot_cycs"]), color="#0F172A", s=18, zorder=6, alpha=0.80)

    # Rich single-variable right-side callout
    if cfg == "sdp_def_4096_r1000":
        note = f"{d['tot_med']:.0f} cyc ({d['net_med']:.1f}%) · OUT_CNTS MMIO + 4K 拷贝 · p50 {d['p50']:.2f} µs"
    elif cfg == "sdp_rpm_copy_4096_r1000":
        note = f"{d['tot_med']:.0f} cyc ({d['net_med']:.1f}%) · 去 MMIO (−1513 cyc) · late {d['late']:.1f}%"
    elif cfg == "sdp_sync_4096_r1000":
        note = f"{d['tot_med']:.0f} cyc ({d['net_med']:.1f}%) · late={d['late']:.1f}% 等尾自旋 · DDIO {d['ddio']:.1f}%"
    elif cfg == "sdp_async_4096_r1000":
        diff = data[2]["tot_med"] - d["tot_med"]
        note = f"{d['tot_med']:.0f} cyc ({d['net_med']:.1f}%) · late→0.0% (−{diff:.0f} cyc / −{data[2]['net_med']-d['net_med']:.1f} pp!)"
    elif cfg == "sdp_async_ism_4096_r1000":
        note = f"{d['tot_med']:.0f} cyc ({d['net_med']:.1f}%) · TX ISM 省 ~4 cyc · DDIO {d['ddio']:.1f}%"
    elif cfg == "sdp_async_ism_rx512_4096_r1000":
        note = f"{d['tot_med']:.0f} cyc ({d['net_med']:.1f}%) · DDIO 45%→100.0% · DRAM写 35.6→{d['imcw']:.1f}/包"
    elif cfg == "sdp_fair_4096_r1000":
        note = f"{d['tot_med']:.0f} cyc ({d['net_med']:.1f}%) · 净处理 {d['act_med']:.0f} + 空轮询 {d['ep_med']:.0f} · p50 {d['p50']:.2f} µs"
    elif cfg == "directpush_4096_r1000":
        r_tot = data[6]["tot_med"] / d["tot_med"]
        r_act = data[6]["act_med"] / d["act_med"]
        note = f"{d['tot_med']:.0f} cyc ({d['net_med']:.1f}%) · vs fair-full 省 {r_tot:.2f}×（净处理 {r_act:.2f}×）· p50 {d['p50']:.2f} µs"
    elif cfg == "sdp_fair_b8_4096_r1000":
        note = f"{d['tot_med']:.0f} cyc ({d['net_med']:.1f}%) · 净处理 {d['act_med']:.0f} + 空轮询 {d['ep_med']:.0f} · p50 {d['p50']:.2f} µs (+2.1 µs)"
    else:
        r_tot = data[8]["tot_med"] / d["tot_med"]
        r_act = data[8]["act_med"] / d["act_med"]
        note = f"{d['tot_med']:.0f} cyc ({d['net_med']:.1f}%) · vs fair-full(B=8) 省 {r_tot:.2f}×（净处理 {r_act:.2f}×）· p50 {d['p50']:.2f} µs"

    is_key = cfg in ("sdp_async_4096_r1000", "sdp_fair_4096_r1000", "directpush_4096_r1000", "directpush_b8_4096_r1000")
    ax.text(
        d["tot_med"] + 55, y, note,
        va="center", ha="left", fontsize=8.4,
        fontweight="bold" if is_key else "normal",
        color="#0F62FE" if "directpush" in cfg else ("#0369A1" if cfg == "sdp_async_4096_r1000" else "#1E293B"),
    )

# Legend for active vs empty-poll hatch
from matplotlib.patches import Patch
legend_handles = [
    Patch(facecolor="#475569", label="实色：单包净收发/管理周期 (active cyc/包)"),
    Patch(facecolor="#E2E8F0", edgecolor="#475569", hatch="///", label="斜线：空轮询开销 (empty_polls × 零载单次空轮询)"),
    Patch(facecolor="#0F62FE", label="蓝色：DirectPush (原生 DPI DMA + 64 B CQE)"),
]
ax.legend(handles=legend_handles, loc="center right", fontsize=8.3, frameon=True, framealpha=0.96, bbox_to_anchor=(0.99, 0.56))

ax.set_yticks(y_pos)
ax.set_yticklabels([x[2] for x in ITEMS], fontsize=8.7)
ax.invert_yaxis()
ax.set_xlim(0, 5650)
ax.set_xlabel("每包主机核消耗周期 cyc/包（4 KiB @ 1.0 Mreq/s = 8.19 GB/s 双向 · 满核预算 3900 cyc/包）", fontsize=10.0)

# Secondary top x-axis in net_cpu%
ax_top = ax.twiny()
ax_top.set_xlim(0, 5650 / 3900.0 * 100.0)
ax_top.set_xlabel("同吞吐下主机核占用率 net_cpu% (cpu40 @ 3.90 GHz)", fontsize=9.6, color="#475569")
ax_top.spines["top"].set_visible(True)
ax_top.spines["top"].set_color("#CBD5E1")
ax_top.spines["right"].set_visible(False)
ax_top.grid(False)

fig.suptitle(
    "MRV-166 4 KiB 单变量逐档消融：同步等尾水分 (−459 cyc) → DDIO 对齐 (100%) → 干净 DirectPush 省 1.99×（B=8 省 1.86×）",
    fontsize=11.4, fontweight="bold", y=0.985,
)
out = mrvplot.save(fig, "mrv-166-4k-ablation")
print("Saved:", out)
