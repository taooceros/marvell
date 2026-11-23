"""MRV-166 Figure 2: Clean 3-size comparison (64 B / 1 KiB / 4 KiB) of host CPU cost and W=1 latency.

Plots from raw logs/mrv166-2026-11-23/runs.jsonl (n = 3 interleaved, cpu40 @ 3.90 GHz,
Socket 1 uncore 2.20 GHz, CN106 core 3 @ 2.00 GHz, lost = bad = torn = 0, PMU 100%).
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

ep_cyc_per_poll = {}
for zcfg in ("zero_sdp_default", "zero_sdp_sync", "zero_sdp_async", "zero_sdp_fair", "zero_directpush"):
    rs = by_cfg[("part0_zero", zcfg)]
    ep_cyc_per_poll[zcfg] = statistics.median(
        [(r["host_pmu"]["cycles"] - r["app_work_units"] * idle_cyc_u[r["rep"]]) / r["polls"] for r in rs]
    )

def cyc_vals(part, cfg):
    rs = by_cfg[(part, cfg)]
    assert len(rs) == 3
    for r in rs:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
    return [
        (1.0 - r["app_work_per_s"] / idle_map[r["rep"]]) * r["host_pmu"]["cycles"] / r["measured"]
        for r in rs
    ]

def act_med(part, cfg, zcfg):
    rs = by_cfg[(part, cfg)]
    return statistics.median([
        (1.0 - r["app_work_per_s"] / idle_map[r["rep"]]) * r["host_pmu"]["cycles"] / r["measured"]
        - r["empty_polls"] / r["measured"] * ep_cyc_per_poll[zcfg]
        for r in rs
    ])


def rx_b_med(part, cfg):
    rs = by_cfg[(part, cfg)]
    return statistics.median([r["measured"] / max(1, r["polls"] - r["empty_polls"]) for r in rs])

def pct_med(part, cfg):
    rs = by_cfg[(part, cfg)]
    return statistics.median([(1.0 - r["app_work_per_s"] / idle_map[r["rep"]]) * 100.0 for r in rs])


def lat_p50_vals(cfg):
    rs = by_cfg[("part1_w1", cfg)]
    assert len(rs) == 3
    return [r["lat_all_us"]["p50"] for r in rs]


# Measured CN106 software dual-line tail-poll serial tax on zero-late-tail 64 B and 1 KiB (B=1)
TAIL_TAX_B1 = {
    "64": act_med("part3_size_cpu", "sdp_async_64_r1500", "zero_sdp_async") - act_med("part3_size_cpu", "sdp_sync_64_r1500", "zero_sdp_sync"),
    "1024": act_med("part3_size_cpu", "sdp_async_1024_r1000", "zero_sdp_async") - act_med("part3_size_cpu", "sdp_sync_1024_r1000", "zero_sdp_sync"),
}
TAIL_TAX_B1["4096"] = TAIL_TAX_B1["1024"]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.8, 6.7), gridspec_kw={"width_ratios": [1.46, 1.0]})

# ---- Panel A: Per-packet host CPU cycles across 64 B / 1 KiB / 4 KiB ----
SIZES = [
    ("64", "r1500", "part3_size_cpu", "64 B\n@ 1.5 M/s\n(0.19 GB/s)"),
    ("1024", "r1000", "part3_size_cpu", "1 KiB\n@ 1.0 M/s\n(2.05 GB/s)"),
    ("4096", "r1000", "part2_4k_ablation", "4 KiB\n@ 1.0 M/s\n(8.19 GB/s)"),
]
X = np.arange(len(SIZES))
W = 0.145

BAR_DEFS = [
    ("sdp_sync", "SDP (sync-tail, B=1)", "#94A3B8", -2.5 * W, None),
    ("sdp_async", "SDP (async-tail, B=1)", "#64748B", -1.5 * W, "///"),
    ("sdp_fair", "SDP (fair-full, B=1)", "#1E3A8A", -0.5 * W, None),
    ("directpush", "DirectPush (B=1)", "#0F62FE", +0.5 * W, None),
    ("sdp_fair_b8", "SDP (fair-full, B=8)", "#475569", +1.5 * W, ".."),
    ("directpush_b8", "DirectPush (B=8)", "#38BDF8", +2.5 * W, None),
]

for key, label, color, off, hatch in BAR_DEFS:
    meds = []
    for i, (sz, rtag, part, _) in enumerate(SIZES):
        cfg = f"{key}_{sz}_{rtag}"
        vals = cyc_vals(part, cfg)
        m = statistics.median(vals)
        meds.append(m)
        ax1.scatter([X[i] + off] * len(vals), vals, color="#0F172A", s=14, zorder=6, alpha=0.75)
    bars = ax1.bar(
        X + off, meds, width=W * 0.88, color=color, alpha=0.92,
        edgecolor="#0F172A" if hatch else "none", hatch=hatch, lw=0.7 if hatch else 0.0,
        label=label, zorder=3,
    )
    for b in bars:
        h = b.get_height()
        ax1.text(
            b.get_x() + b.get_width() / 2, h + 22, f"{h:.0f}",
            ha="center", va="bottom", fontsize=7.5,
            fontweight="bold" if key in ("sdp_fair", "directpush", "directpush_b8") else "normal",
            color="#0F62FE" if "directpush" in key else "#1E293B",
        )

# Layered ratio callouts above each size group (total / net active / minus dual-line tail serial tax)
for i, (sz, rtag, part, _) in enumerate(SIZES):
    f1 = statistics.median(cyc_vals(part, f"sdp_fair_{sz}_{rtag}"))
    d1 = statistics.median(cyc_vals(part, f"directpush_{sz}_{rtag}"))
    f8 = statistics.median(cyc_vals(part, f"sdp_fair_b8_{sz}_{rtag}"))
    d8 = statistics.median(cyc_vals(part, f"directpush_b8_{sz}_{rtag}"))
    a_f1 = act_med(part, f"sdp_fair_{sz}_{rtag}", "zero_sdp_fair")
    a_d1 = act_med(part, f"directpush_{sz}_{rtag}", "zero_directpush")
    a_f8 = act_med(part, f"sdp_fair_b8_{sz}_{rtag}", "zero_sdp_fair")
    a_d8 = act_med(part, f"directpush_b8_{sz}_{rtag}", "zero_directpush")
    stk_f1 = a_f1 - TAIL_TAX_B1[sz]
    stk_f8 = a_f8 - TAIL_TAX_B1[sz] / rx_b_med(part, f"sdp_fair_b8_{sz}_{rtag}")
    top_h = max(
        statistics.median(cyc_vals(part, f"sdp_sync_{sz}_{rtag}")),
        statistics.median(cyc_vals(part, f"sdp_async_{sz}_{rtag}")),
        f1,
    )
    txt = (
        f"B=1: 总 {f1/d1:.2f}× | 净 {a_f1/a_d1:.2f}× | 扣尾税* {stk_f1/a_d1:.2f}×\n"
        f"B=8: 总 {f8/d8:.2f}× | 净 {a_f8/a_d8:.2f}× | 扣尾税* {stk_f8/a_d8:.2f}×"
    )
    lift = 310 if sz == "64" else 170
    ax1.text(
        X[i], top_h + lift, txt,
        ha="center", va="bottom", fontsize=7.5, fontweight="bold", color="#1E3A8A",
        bbox=dict(boxstyle="round,pad=0.24", fc="#EEF4FF", ec="#0F62FE", lw=1.0, alpha=0.96),
    )

# Accurate physical attribution for 64 B B=8 DirectPush drop (computed from raw PMU)
llc_b1 = statistics.median([r["host_pmu"]["llc_loads"] / r["measured"] for r in by_cfg[("part3_size_cpu", "directpush_64_r1500")]])
llc_b8 = statistics.median([r["host_pmu"]["llc_loads"] / r["measured"] for r in by_cfg[("part3_size_cpu", "directpush_b8_64_r1500")]])
d8_64 = statistics.median(cyc_vals("part3_size_cpu", "directpush_b8_64_r1500"))
ax1.annotate(
    f"批收割触发 +256 B RX 槽\nL2 步长预取 + 循环摊薄\n(llc_loads {llc_b1:.2f}→{llc_b8:.2f})",
    xy=(X[0] + 2.5 * W, d8_64 + 85),
    xytext=(X[0] + 2.15 * W, 620),
    ha="center", va="bottom", fontsize=7.1, color="#0369A1", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.20", fc="#F0F9FF", ec="#0284C7", lw=0.9),
    arrowprops=dict(arrowstyle="->", color="#0284C7", lw=1.1, connectionstyle="arc3,rad=-0.10"),
)

ax1.text(
    0.015, 0.77,
    "*扣尾税 [推算] = 扣 CN106 双行读尾串行税后剩 DPDK 栈软件税（1–4 KiB 对标裸环同构）\n单次业务 τ_app 扩至 5 µs [推算] 总倍率 1.35×–1.81×（扣空轮询净处理 1.33×–1.79×）",
    transform=ax1.transAxes, ha="left", va="top", fontsize=7.2, color="#475569",
    bbox=dict(boxstyle="round,pad=0.22", fc="#F8FAFC", ec="#CBD5E1", lw=0.8),
)

ax1.set_xticks(X)
ax1.set_xticklabels([s[3] for s in SIZES], fontsize=9.2)
ax1.set_ylabel("每包主机核消耗周期 cyc/包 (cpu40 @ 3.90 GHz)", fontsize=10.0)
ax1.set_title("A. 挤干水分后三档包长主机核耗时（区分总 / 净 / 扣尾税*倍率）", fontsize=10.3, fontweight="bold", pad=10)
ax1.set_ylim(0, 2080)
ax1.legend(loc="upper left", ncol=2, fontsize=7.6, frameon=True, framealpha=0.96)

# ---- Panel B: W=1 unloaded RTT latency p50 across 64 B / 1 KiB / 4 KiB ----
X2 = np.arange(3)
W2 = 0.24
LAT_DEFS = [
    ("sdp_def", "SDP (默认 OUT_CNTS)", "#94A3B8", -W2),
    ("sdp_fair", "SDP (fair-full 异步包尾)", "#1E3A8A", 0.0),
    ("directpush", "DirectPush", "#0F62FE", +W2),
]

for key, label, color, off in LAT_DEFS:
    meds = []
    for i, sz in enumerate(("64", "1024", "4096")):
        vals = lat_p50_vals(f"{key}_{sz}_w1")
        m = statistics.median(vals)
        meds.append(m)
        ax2.scatter([X2[i] + off] * len(vals), vals, color="#0F172A", s=16, zorder=6, alpha=0.75)
    bars = ax2.bar(X2 + off, meds, width=W2 * 0.88, color=color, alpha=0.92, label=label, zorder=3)
    for b in bars:
        h = b.get_height()
        ax2.text(
            b.get_x() + b.get_width() / 2, h + 0.12, f"{h:.3f}",
            ha="center", va="bottom", fontsize=7.7,
            fontweight="bold" if key == "directpush" else "normal",
            color="#0F62FE" if key == "directpush" else "#1E293B",
        )

for i, sz in enumerate(("64", "1024", "4096")):
    sf = statistics.median(lat_p50_vals(f"sdp_fair_{sz}_w1"))
    sd = statistics.median(lat_p50_vals(f"sdp_def_{sz}_w1"))
    dp = statistics.median(lat_p50_vals(f"directpush_{sz}_w1"))
    ax2.text(
        X2[i], max(sd, sf) + 0.55, f"快 {sf/dp:.2f}×\n(vs默认 {sd/dp:.2f}×)",
        ha="center", va="bottom", fontsize=8.0, fontweight="bold",
        color="#0F62FE" if sz == "64" else "#334155",
        bbox=dict(
            boxstyle="round,pad=0.22",
            fc="#EEF4FF" if sz == "64" else "#F8FAFC",
            ec="#0F62FE" if sz == "64" else "#94A3B8",
            lw=1.1 if sz == "64" else 0.8,
        ),
    )

ax2.set_xticks(X2)
ax2.set_xticklabels(["64 B\n(小包直推)", "1 KiB\n(DPI DMA)", "4 KiB\n(DPI DMA)"], fontsize=9.2)
ax2.set_ylabel("空载 W=1 往返延迟 p50 (µs)", fontsize=10.0)
ax2.set_title("B. 同卡空载 W=1 往返延迟（64 B 比同卡 SDP 快 5.24×）", fontsize=10.3, fontweight="bold", pad=10)
ax2.set_ylim(0, 7.9)
ax2.legend(loc="upper left", fontsize=8.2, frameon=True, framealpha=0.96)

fig.suptitle(
    "MRV-166 干净对比总览：异步包尾 + 公平对齐 SDP vs DirectPush（64 B / 1 KiB / 4 KiB · 1H+1C · 零 DSA · n=3）",
    fontsize=11.5, fontweight="bold", y=0.985,
)
out = mrvplot.save(fig, "mrv-166-size-comparison")
print("Saved:", out)
