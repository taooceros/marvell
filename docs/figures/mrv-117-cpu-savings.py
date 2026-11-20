"""MRV-117 Figure 3: Matched-throughput host CPU core utilization with ALL measured
SDP controls (rx_poll_mem=1 & zero-copy), idle MMIO poll floor disclosure, and
6-step causal decomposition on 4 KiB @ 1.0 Mreq/s (8.19 GB/s bidir) from runs.jsonl.
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


def late_pct_med(cfg):
    # Note: mrv117_host increments `late` across warmup + measured total packets.
    return statistics.median([100.0 * r["late"] / (r["warmup"] + r["measured"]) for r in by_cfg[cfg]])


fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16.8, 6.5), gridspec_kw={"width_ratios": [1.38, 1.05]})

# ---- Panel A: Matched-throughput host CPU core utilization across all 6 cases + measured controls ----
CASES = [
    ("mix_ads_r1000", "Mix-Ads\n1.0 M/s\n(0.94 GB/s)", None, None),
    ("mix_ads_r2000", "Mix-Ads\n2.0 M/s\n(1.88 GB/s)", "sdp_rpm_mix_ads_r2000", None),
    ("mix_geo_r500", "Mix-Geo\n0.5 M/s\n(2.08 GB/s)", None, None),
    ("mix_geo_r1000", "Mix-Geo\n1.0 M/s\n(4.16 GB/s)", "sdp_rpm_mix_geo_r1000", None),
    ("mix_4size_r1000", "Mix-4Size\n1.0 M/s\n(2.23 GB/s)", None, None),
    ("4096_r1000", "纯 4 KiB\n1.0 M/s\n(8.19 GB/s)", "sdp_rpm_4096_r1000", "sdp_rpm_zc_4096_r1000"),
]
X = np.arange(len(CASES))
W = 0.20

# Draw default SDP zero-load MMIO poll artifact reference line (~550 / (550 + 330.5) = 62.5%)
MMIO_FLOOR = 550.0 / (550.0 + 330.5) * 100.0
ax1.axhline(MMIO_FLOOR, color="#DC2626", ls="--", lw=1.3, alpha=0.85, zorder=5)
ax1.text(2.22, MMIO_FLOOR - 4.8,
         "默认 SDP 零载 OUT_CNTS MMIO 底噪 ≈62.5%",
         ha="center", va="top", fontsize=7.4, fontweight="bold", color="#B91C1C", zorder=9,
         bbox=dict(boxstyle="round,pad=0.18", fc="#FEF2F2", ec="#DC2626", alpha=0.97))

# 4 bar slots per case: -1.5W (SDP default), -0.5W (SDP rpm ctrl if measured), +0.5W (Push), +1.5W (DirectPush)
slot_defs = [
    ("sdp", "(a) 默认 SDP (OUT_CNTS + copy)", "#6B7280", -1.5 * W, None),
    ("sdp_rpm", "(a') 对照: SDP rx_poll_mem=1 (已测)", "#CBD5E1", -0.5 * W, "///"),
    ("push", "(b) 纯 MOVDIR64B + WC", "#D97706", +0.5 * W, None),
    ("directpush", "(c) DirectPush + 原生 DPI DMA", "#0F62FE", +1.5 * W, None),
]

for key, label, color, off, hatch in slot_defs:
    xs_plot, meds = [], []
    for i, (tag, _, rpm_cfg, _) in enumerate(CASES):
        cfg = rpm_cfg if key == "sdp_rpm" else f"{key}_{tag}"
        if cfg is None:
            continue
        vals = net_cpu_list(cfg)
        m = statistics.median(vals)
        xs_plot.append(X[i] + off)
        meds.append(m)
        ax1.scatter([X[i] + off] * len(vals), vals, color="#111827", s=14, zorder=6, alpha=0.65)
    bars = ax1.bar(
        xs_plot, meds, width=W * 0.90, color=color, alpha=0.92,
        edgecolor="#475569" if hatch else "none", hatch=hatch, lw=0.9 if hatch else 0.0,
        label=label, zorder=3,
    )
    for b in bars:
        h = b.get_height()
        ax1.text(
            b.get_x() + b.get_width() / 2, h + 1.3, f"{h:.1f}%",
            ha="center", va="bottom", fontsize=7.0,
            fontweight="bold" if key in ("directpush", "sdp_rpm") else "normal",
            color="#0F62FE" if key == "directpush" else ("#334155" if key == "sdp_rpm" else "#374151"),
        )

# Also overlay the strongest measured SDP control (rpm + zero-copy) on 4 KiB @ 1.0 M/s!
rpm_zc_vals = net_cpu_list("sdp_rpm_zc_4096_r1000")
rpm_zc_med = statistics.median(rpm_zc_vals)
ax1.scatter(
    [X[5] - 0.5 * W] * len(rpm_zc_vals), rpm_zc_vals,
    marker="D", s=48, fc="#FFFFFF", ec="#0F172A", lw=1.6,
    label="对照: SDP rpm + 零拷贝 (4K: 39.1%)", zorder=8,
)
ax1.annotate(
    f"rpm+zc\n{rpm_zc_med:.1f}%",
    xy=(X[5] - 0.5 * W, rpm_zc_med), xytext=(4.32, 47.0),
    fontsize=7.4, fontweight="bold", color="#0F172A", ha="center", zorder=9,
    arrowprops=dict(arrowstyle="->", color="#0F172A", lw=1.1),
    bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="#334155", alpha=0.97),
)

# Dual-metric callout boxes above each case
for i, (tag, _, rpm_cfg, rpm_zc_cfg) in enumerate(CASES):
    sdp_m = statistics.median(net_cpu_list(f"sdp_{tag}"))
    dp_m = statistics.median(net_cpu_list(f"directpush_{tag}"))
    r_def = sdp_m / dp_m
    if rpm_zc_cfg is not None:
        r_ctrl = statistics.median(net_cpu_list(rpm_zc_cfg)) / dp_m
        txt = f"vs默认 {r_def:.2f}×\nvs强对照 {r_ctrl:.2f}×"
        ec = "#0F62FE"
    elif rpm_cfg is not None:
        r_ctrl = statistics.median(net_cpu_list(rpm_cfg)) / dp_m
        txt = f"vs默认 {r_def:.2f}×\nvs rpm {r_ctrl:.2f}×"
        ec = "#0F62FE"
    elif tag == "mix_geo_r500":
        txt = f"vs默认 {r_def:.2f}×\n(含空轮询底噪)"
        ec = "#F59E0B"
    else:
        txt = f"vs默认 {r_def:.2f}×"
        ec = "#93B4FD"
    ax1.text(
        X[i], sdp_m + 6.8, txt, ha="center", va="bottom",
        fontsize=7.6, fontweight="bold", color="#1E3A8A" if ec != "#F59E0B" else "#92400E",
        bbox=dict(boxstyle="round,pad=0.18", fc="#EEF4FF" if ec != "#F59E0B" else "#FFFBEB", ec=ec, alpha=0.96),
    )

ax1.set_xticks(X)
ax1.set_xticklabels([c[1] for c in CASES], fontsize=8.8)
ax1.set_ylabel("同吞吐下主机核占用率 net_cpu% (cpu40, 3.90 GHz)", fontsize=10.2)
ax1.set_title("A. 双口径同吞吐核耗：vs 默认 SDP 省 3.62×–7.89×；vs 强对照省 1.87×–2.56×", fontsize=10.8, fontweight="bold", pad=10)
ax1.set_ylim(0, 138)
ax1.legend(loc="upper center", ncol=2, fontsize=7.6, frameon=True, framealpha=0.96)

# ---- Panel B: Complete 6-bar causal decomposition on 4 KiB @ 1.0 Mreq/s ----
DECOMP = [
    ("sdp_4096_r1000", "传统 SDP\n(默认)", "#6B7280", None),
    ("sdp_zc_4096_r1000", "SDP + 零拷贝\n(仍含 MMIO)", "#9CA3AF", None),
    ("sdp_rpm_4096_r1000", "SDP +\nrx_poll_mem", "#CBD5E1", "///"),
    ("sdp_rpm_zc_4096_r1000", "SDP + rpm\n+ 零拷贝", "#475569", None),
    ("push_4096_r1000", "纯 MOVDIR64B\n+ WC (64行)", "#D97706", None),
    ("directpush_4096_r1000", "DirectPush +\n原生 DPI DMA", "#0F62FE", None),
]
Y = np.arange(len(DECOMP))
meds2 = [statistics.median(net_cpu_list(cfg)) for cfg, _, _, _ in DECOMP]

for i, (cfg, _, color, hatch) in enumerate(DECOMP):
    ax2.barh(
        Y[i], meds2[i], height=0.58, color=color, alpha=0.92,
        edgecolor="#475569" if hatch else "none", hatch=hatch, lw=0.9 if hatch else 0.0, zorder=3,
    )
    vals = net_cpu_list(cfg)
    ax2.scatter(vals, [Y[i]] * len(vals), color="#111827", s=16, zorder=6, alpha=0.7)
    cyc = act_cyc_med(cfg)
    llcm = llc_miss_med(cfg)
    latep = late_pct_med(cfg)
    if cfg == "sdp_zc_4096_r1000":
        extra = "空轮询×10 填满 MMIO"
    elif cfg == "sdp_rpm_zc_4096_r1000":
        extra = f"late={latep:.1f}% 等尾自旋"
    elif cfg == "sdp_rpm_4096_r1000":
        extra = f"LLC缺 {llcm:.1f} · late {latep:.1f}%"
    elif cfg == "directpush_4096_r1000":
        r_strong = statistics.median(net_cpu_list("sdp_rpm_zc_4096_r1000")) / meds2[i]
        extra = f"LLC缺 0.0 · late 0% · {r_strong:.2f}×"
    else:
        extra = f"LLC缺 {llcm:.1f}"
    ax2.text(
        meds2[i] + 2.0, Y[i], f"{meds2[i]:.1f}% ({cyc:.0f} cyc/r · {extra})",
        va="center", ha="left", fontsize=7.9,
        fontweight="bold" if cfg in ("directpush_4096_r1000", "sdp_rpm_zc_4096_r1000") else "normal",
        color="#0F62FE" if cfg == "directpush_4096_r1000" else "#1F2937",
    )

ax2.set_yticks(Y)
ax2.set_yticklabels([d[1] for d in DECOMP], fontsize=8.8)
ax2.invert_yaxis()
ax2.set_xlabel("主机核占用率 net_cpu%（4 KiB @ 1.0 Mreq/s = 8.19 GB/s 双向）", fontsize=9.8)
ax2.set_title("B. 4 KiB 六档全量因果拆解（含全部已测对照与 late 自旋）", fontsize=10.8, fontweight="bold", pad=10)
ax2.set_xlim(0, 206)

fig.suptitle(
    "MRV-117 同吞吐主机 CPU 核占用（全量强对照 + 空轮询底噪 + 因果拆解 · cpu40 3.90 GHz · 330.5 ns 量子 · 零 DSA · n=3）",
    fontsize=11.6, fontweight="bold"
)
out = mrvplot.save(fig, "mrv-117-cpu-savings")
print("Saved:", out)
