"""MRV-117 Figure 3: Matched-throughput host CPU core utilization, active cycles/req,
and 5-step causal decomposition on 4 KiB @ 1.0 Mreq/s (8.19 GB/s bidir) from runs.jsonl.
"""
import collections
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import mrvplot

RUNS = Path(__file__).resolve().parents[2] / "logs/mrv117-2026-11-20/runs.jsonl"
rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]

idle_map = {r["rep"]: r["units_per_s"] for r in rows if r["part"] == "part0_cal"}
by_cfg = collections.defaultdict(list)
for r in rows:
    if r["part"] in ("part3_cpu", "part3_cpu_ctrl"):
        assert r["verified"], f"unverified: {r['cfg']}"
        by_cfg[r["cfg"]].append(r)


def net_cpu_list(cfg):
    return [(1.0 - r["app_work_per_s"] / idle_map[r["rep"]]) * 100.0 for r in by_cfg[cfg]]


def act_cyc_med(cfg):
    vals = [
        (1.0 - r["app_work_per_s"] / idle_map[r["rep"]]) * r["host_pmu"]["cycles"] / r["measured"]
        for r in by_cfg[cfg]
    ]
    return statistics.median(vals)


def llc_miss_med(cfg):
    return statistics.median([r["host_pmu"]["llc_load_misses"] / r["measured"] for r in by_cfg[cfg]])


fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.4, 6.1), gridspec_kw={"width_ratios": [1.35, 1.0]})

CASES = [
    ("mix_ads_r1000", "Mix-Ads\n1.0 M/s\n(0.94 GB/s)"),
    ("mix_ads_r2000", "Mix-Ads\n2.0 M/s\n(1.88 GB/s)"),
    ("mix_geo_r500", "Mix-Geo\n0.5 M/s\n(2.08 GB/s)"),
    ("mix_geo_r1000", "Mix-Geo\n1.0 M/s\n(4.16 GB/s)"),
    ("mix_4size_r1000", "Mix-4Size\n1.0 M/s\n(2.23 GB/s)"),
    ("4096_r1000", "纯 4 KiB\n1.0 M/s\n(8.19 GB/s)"),
]
X = np.arange(len(CASES))
W = 0.25

TIER_DEFS = [
    ("sdp", "(a) 传统 SDP (MTU 9000)", "#6B7280", -W),
    ("push", "(b) 纯 MOVDIR64B + WC", "#D97706", 0.0),
    ("directpush", "(c) DirectPush + 原生 DPI DMA", "#0F62FE", +W),
]

for tier, label, color, off in TIER_DEFS:
    meds = []
    for i, (tag, _) in enumerate(CASES):
        vals = net_cpu_list(f"{tier}_{tag}")
        meds.append(statistics.median(vals))
        ax1.scatter([X[i] + off] * len(vals), vals, color="#111827", s=16, zorder=6, alpha=0.65)
    bars = ax1.bar(X + off, meds, width=W * 0.90, color=color, alpha=0.90, label=label, zorder=3)
    for i, b in enumerate(bars):
        h = b.get_height()
        ax1.text(b.get_x() + b.get_width() / 2, h + 1.5, f"{h:.1f}%",
                 ha="center", va="bottom", fontsize=7.6, fontweight="bold" if tier == "directpush" else "normal",
                 color=color if tier == "directpush" else "#374151")

for i, (tag, _) in enumerate(CASES):
    sdp_m = statistics.median(net_cpu_list(f"sdp_{tag}"))
    dp_m = statistics.median(net_cpu_list(f"directpush_{tag}"))
    ratio = sdp_m / dp_m
    ax1.text(X[i], sdp_m + 7.5, f"省核 {ratio:.1f}×", ha="center", va="bottom",
             fontsize=8.5, fontweight="bold", color="#0F62FE",
             bbox=dict(boxstyle="round,pad=0.18", fc="#EEF4FF", ec="#93B4FD"))

ax1.set_xticks(X)
ax1.set_xticklabels([c[1] for c in CASES], fontsize=9.2)
ax1.set_ylabel("同吞吐下主机核占用率 net_cpu% (cpu40, 3.90 GHz)", fontsize=10.5)
ax1.set_title("A. 同吞吐并跑计算余量实测：DirectPush 降核耗 3.6×–7.9×", fontsize=11.3, fontweight="bold", pad=10)
ax1.set_ylim(0, 129)
ax1.legend(loc="upper center", ncol=3, fontsize=8.0, frameon=True, framealpha=0.96)

DECOMP = [
    ("sdp_4096_r1000", "传统 SDP\n(默认)", "#6B7280"),
    ("sdp_rpm_4096_r1000", "SDP +\nrx_poll_mem", "#9CA3AF"),
    ("sdp_rpm_zc_4096_r1000", "SDP + rpm\n+ 零拷贝", "#4B5563"),
    ("push_4096_r1000", "纯 MOVDIR64B\n+ WC (64行)", "#D97706"),
    ("directpush_4096_r1000", "DirectPush +\n原生 DPI DMA", "#0F62FE"),
]
Y = np.arange(len(DECOMP))
meds2 = [statistics.median(net_cpu_list(cfg)) for cfg, _, _ in DECOMP]
colors2 = [c for _, _, c in DECOMP]

ax2.barh(Y, meds2, height=0.58, color=colors2, alpha=0.92, zorder=3)
for i, (cfg, _, color) in enumerate(DECOMP):
    vals = net_cpu_list(cfg)
    ax2.scatter(vals, [Y[i]] * len(vals), color="#111827", s=18, zorder=6, alpha=0.7)
    cyc = act_cyc_med(cfg)
    llcm = llc_miss_med(cfg)
    ax2.text(meds2[i] + 2.0, Y[i], f"{meds2[i]:.1f}%  ({cyc:.0f} cyc/r · LLC缺 {llcm:.1f})",
             va="center", ha="left", fontsize=8.5,
             fontweight="bold" if cfg == "directpush_4096_r1000" else "normal",
             color="#0F62FE" if cfg == "directpush_4096_r1000" else "#1F2937")

ax2.set_yticks(Y)
ax2.set_yticklabels([d[1] for d in DECOMP], fontsize=9.4)
ax2.invert_yaxis()
ax2.set_xlabel("主机核占用率 net_cpu%（4 KiB @ 1.0 Mreq/s = 8.19 GB/s 双向）", fontsize=10)
ax2.set_title("B. 4 KiB 核耗因果拆解（去 MMIO / 去拷贝 / 去描述符环）", fontsize=11.3, fontweight="bold", pad=10)
ax2.set_xlim(0, 165)

fig.suptitle(
    "MRV-117 同吞吐主机 CPU 核占用与因果拆解（cpu40 3.90 GHz · 330.5 ns 无内存计算量子 · 128 MiB 留存池 · 零 DSA · n=3）",
    fontsize=11.8, fontweight="bold"
)
out = mrvplot.save(fig, "mrv-117-cpu-savings")
print("Saved:", out)
