#!/usr/bin/env python3
"""MRV-133 Fig 3: Matched-throughput saturn host CPU savings (dual-metric + Pareto)."""

import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot

RUNS = pathlib.Path("/home/hongtao/dev/marvell-mrv133/logs/mrv133-2026-11-21/runs.jsonl")


def load_runs():
    by_cfg = {}
    for line in RUNS.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r.get("verified"), f"Unverified run: {r['cfg']} rep={r['rep']}"
        by_cfg.setdefault(r["cfg"], []).append(r)
    return by_cfg


def main():
    by_cfg = load_runs()
    idle_kps = statistics.median([r["cal"]["app_work_kps"] for r in by_cfg["app_work_idle_cal"]])
    idle_ns = statistics.median([r["cal"]["ns_per_unit"] for r in by_cfg["app_work_idle_cal"]])
    cpu_ghz = statistics.median([r["pmu"]["cpu40_ghz"] for r in by_cfg["app_work_idle_cal"]])

    cases = [
        ("64", 1500, "64 B\n@1.50M/s"),
        ("1024", 800, "1 KiB\n@0.80M/s"),
        ("4096", 500, "4 KiB\n@0.50M/s"),
        ("mix_ads", 1200, "Mix-Ads\n@1.20M/s"),
        ("mix_geo", 600, "Mix-Geo\n@0.60M/s"),
    ]

    def metrics(cfg, rate_mrps):
        rs = by_cfg[cfg]
        wk = statistics.median([r["host"]["app_work_kps"] for r in rs])
        occ = max(0.0, (1.0 - wk / idle_kps) * 100.0)
        cyc_req = (occ / 100.0) * (cpu_ghz * 1e9) / (rate_mrps * 1e6)
        p50 = statistics.median([r["client"]["tot_p50_us"] for r in rs])
        ddio = statistics.median([r["pmu"]["ddio_hit_pct"] for r in rs])
        return occ, cyc_req, p50, ddio

    sdp_occ, rpm_occ, dp_occ = [], [], []
    sdp_cyc, rpm_cyc, dp_cyc = [], [], []
    sdp_p50, rpm_p50, dp_p50 = [], [], []
    rpm_ddio, dp_ddio = [], []

    for wl, rk, _ in cases:
        rate = rk / 1000.0
        o1, c1, p1, _ = metrics(f"sdp_cpu_{wl}_r{rk}", rate)
        o2, c2, p2, d2 = metrics(f"sdp_rpm_cpu_{wl}_r{rk}", rate)
        o3, c3, p3, d3 = metrics(f"direct_cpu_{wl}_r{rk}", rate)
        sdp_occ.append(o1); rpm_occ.append(o2); dp_occ.append(o3)
        sdp_cyc.append(c1); rpm_cyc.append(c2); dp_cyc.append(c3)
        sdp_p50.append(p1); rpm_p50.append(p2); dp_p50.append(p3)
        rpm_ddio.append(d2); dp_ddio.append(d3)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.6), gridspec_kw={"width_ratios": [1.32, 1.0]})

    C_SDP = "#d9534f"
    C_RPM = "#e67e22"
    C_DP = "#1f6feb"

    x = np.arange(len(cases))
    w = 0.26
    ax1.bar(x - w, sdp_occ, w * 0.92, color=C_SDP, alpha=0.88, label="传统 SDP 默认 (含 OUT_CNTS 空轮询底噪)")
    ax1.bar(x, rpm_occ, w * 0.92, color=C_RPM, alpha=0.88, label="SDP 强对照 (rx_poll_mem=1 [+zero-copy])")
    ax1.bar(x + w, dp_occ, w * 0.92, color=C_DP, alpha=0.92, label="DirectPush + 原生 DPI DMA (128 MiB 留存池)")

    mmio_floor = 550.0 / (550.0 + idle_ns) * 100.0
    ax1.axhline(mmio_floor, color="#b91c1c", ls="--", lw=1.2, alpha=0.75,
                label=f"默认 SDP 零流量 OUT_CNTS 底噪 (~{mmio_floor:.0f}%)")

    for i in range(len(cases)):
        ax1.text(x[i] - w, sdp_occ[i] + 1.2, f"{sdp_occ[i]:.1f}%", ha="center", va="bottom", fontsize=7.9, color="#991b1b")
        ax1.text(x[i], rpm_occ[i] + 1.2, f"{rpm_occ[i]:.1f}%\n({rpm_ddio[i]:.0f}%)", ha="center", va="bottom", fontsize=7.4, color="#9a3412")
        ax1.text(x[i] + w, dp_occ[i] + 1.2, f"{dp_occ[i]:.1f}%\n({dp_ddio[i]:.0f}%)", ha="center", va="bottom",
                 fontsize=7.5, fontweight="bold", color=C_DP)
        r_def = sdp_occ[i] / max(dp_occ[i], 0.1)
        r_str = rpm_occ[i] / max(dp_occ[i], 0.1)
        ax1.text(
            x[i],
            max(sdp_occ[i], rpm_occ[i], dp_occ[i]) + 10.5,
            f"vs默认 {r_def:.1f}×\nvs强对照 {r_str:.2f}×",
            ha="center",
            va="bottom",
            fontsize=7.5,
            fontweight="bold",
            color="#1e3a8a",
            bbox=dict(boxstyle="round,pad=0.18", fc="#eff6ff", ec="#bfdbfe", lw=0.7),
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels([c[2] for c in cases], fontsize=9.2)
    ax1.set_ylabel("saturn 服务端主机核占用率 (%，--app-work)", fontsize=9.8)
    ax1.set_ylim(0, 136)
    ax1.set_title("(a) 双口径主机核占用对比（括号内为 DDIO 命中率%）", fontsize=10.8, fontweight="bold")
    ax1.legend(loc="upper center", ncol=2, fontsize=7.4, frameon=True, facecolor="white", framealpha=0.95)

    # Panel (b): Two-step Pareto migration
    markers = ["o", "s", "^", "D", "P"]
    for i, (_, _, lbl) in enumerate(cases):
        short_lbl = lbl.replace("\n", " ")
        ax2.scatter(sdp_cyc[i], sdp_p50[i], s=65, marker=markers[i], color=C_SDP, zorder=4)
        ax2.scatter(rpm_cyc[i], rpm_p50[i], s=65, marker=markers[i], color=C_RPM, zorder=4)
        ax2.scatter(dp_cyc[i], dp_p50[i], s=85, marker=markers[i], color=C_DP, edgecolor="#0f172a", lw=0.7, zorder=5, label=short_lbl)
        ax2.annotate("", xy=(rpm_cyc[i], rpm_p50[i]), xytext=(sdp_cyc[i], sdp_p50[i]),
                     arrowprops=dict(arrowstyle="->", color=C_RPM, lw=1.2, ls="--", alpha=0.8))
        ax2.annotate("", xy=(dp_cyc[i], dp_p50[i]), xytext=(rpm_cyc[i], rpm_p50[i]),
                     arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.8, alpha=0.95))

    ax2.scatter([], [], s=65, color=C_SDP, label="红 = 默认 SDP")
    ax2.scatter([], [], s=65, color=C_RPM, label="橙 = SDP 强对照 (rpm[+zc])")
    ax2.scatter([], [], s=75, color=C_DP, edgecolor="#0f172a", label="蓝 = DirectPush + DPI DMA")
    ax2.set_xlim(-150, 6800)
    ax2.set_ylim(4.5, 32.0)
    ax2.set_xlabel("saturn 主机开销 (cycles / req，越左越省核)", fontsize=9.8)
    ax2.set_ylabel("真实双机端到端 p50 延迟 (µs，越低越快)", fontsize=9.8)
    ax2.set_title("(b) 两步 L 形 Pareto 迁移（左下为最优）", fontsize=10.8, fontweight="bold")
    ax2.legend(loc="lower right", fontsize=7.6, frameon=True, facecolor="white", ncol=2)

    fig.suptitle(
        f"MRV-133 真实双机 100GbE 同吞吐主机 CPU 省核（idle 基准 {idle_kps:.1f} k/s = {idle_ns:.1f} ns，cpu40 @ {cpu_ghz:.2f} GHz，n=3）",
        fontsize=11.3,
        fontweight="bold",
        y=0.99,
    )
    png = mrvplot.save(fig, "mrv-133-cpu")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
