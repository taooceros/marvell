#!/usr/bin/env python3
"""MRV-120 Executive Summary Chart: Real 2-Host 100GbE Verified Results
(mercury CX5 <-> 3m DAC <-> CN106 eth0 RPM0+NIX0 <-> saturn).

Plots strictly from raw verified runs in:
  - logs/mrv133-2026-11-21/runs.jsonl (348 runs, W=1 RTT & SDP controls)
  - logs/mrv135-2026-11-21/runs.jsonl (198 runs, clean 1H+1C CPU & 4C RSS cliff escape)
  - logs/mrv137-2026-11-22/runs.jsonl (213 runs, 128B exclusive sub-ring H={1,2,4} & lockstep bound)

Usage:
    uv run --with matplotlib python docs/figures/mrv-120-final-scorecard.py
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import mrvplot  # noqa: E402
from mrvplot import plt, save  # noqa: E402

PURE_CYC_PER_UNIT = 966.0


def load_verified(rel_path: str):
    p = ROOT / rel_path
    by_cfg = defaultdict(list)
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r.get("verified"), f"Unverified run in {rel_path}: {r.get('cfg')}"
        by_cfg[r["cfg"]].append(r)
    return by_cfg


def med(vals):
    return statistics.median(vals)


def pmu966_occ(row):
    wk = row["host"]["app_work_kps"] * 1000.0
    cyc_rate = row["pmu"]["cycles"] / row["pmu"]["dt_s"]
    return max(0.0, 1.0 - (wk * PURE_CYC_PER_UNIT) / cyc_rate) * 100.0


def main():
    g133 = load_verified("logs/mrv133-2026-11-21/runs.jsonl")
    g135 = load_verified("logs/mrv135-2026-11-21/runs.jsonl")
    g137 = load_verified("logs/mrv137-2026-11-22/runs.jsonl")

    idle_135 = med([r["cal"]["app_work_kps"] for r in g135["app_work_idle_cal"]])

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(15.8, 4.9))

    C_BLUE = "#1f6feb"
    C_TEAL = "#0f766e"
    C_GREEN = "#2a9d8f"
    C_ORANGE = "#d97706"
    C_RED = "#dc2626"
    C_GRAY = "#64748b"
    C_LIGHT = "#cbd5e1"
    C_DARK = "#0f172a"

    # =================================================================
    # Panel (a): Real 2-Host 100GbE W=1 Stacked RTT (MRV-133)
    # =================================================================
    sz_specs = [
        ("64 B\n(直推)", "card_echo_64_w1", "sdp_rpm_64_w1", "direct_64_w1"),
        ("256 B\n(直推)", "card_echo_256_w1", "sdp_rpm_256_w1", "direct_256_w1"),
        ("1 KiB\n(DPI)", "card_echo_1024_w1", "sdp_rpm_1024_w1", "direct_1024_w1"),
        ("4 KiB\n(DPI)", "card_echo_4096_w1", "sdp_rpm_4096_w1", "direct_4096_w1"),
    ]
    sx = np.arange(len(sz_specs))
    bw = 0.35
    echo = [med([r["client"]["tot_p50_us"] for r in g133[s[1]]]) for s in sz_specs]
    sdp = [med([r["client"]["tot_p50_us"] for r in g133[s[2]]]) for s in sz_specs]
    dp = [med([r["client"]["tot_p50_us"] for r in g133[s[3]]]) for s in sz_specs]

    ax1.bar(sx - bw / 2, echo, width=bw, color=C_LIGHT, edgecolor=C_GRAY, lw=0.7, label="固定 100GbE 网线+网口底噪")
    ax1.bar(sx - bw / 2, [a - b for a, b in zip(sdp, echo)], bottom=echo, width=bw, color=C_RED, alpha=0.82, label="主机接口：SDP (rx_poll_mem=1)")
    ax1.bar(sx + bw / 2, echo, width=bw, color=C_LIGHT, edgecolor=C_GRAY, lw=0.7)
    ax1.bar(sx + bw / 2, [a - b for a, b in zip(dp, echo)], bottom=echo, width=bw, color=C_BLUE, alpha=0.92, label="主机接口：DirectPush + DPI DMA")

    for i in range(len(sz_specs)):
        diff = dp[i] - sdp[i]
        ax1.text(sx[i] - bw / 2, sdp[i] + 0.25, f"{sdp[i]:.2f}", ha="center", va="bottom", fontsize=7.8, color=C_RED, fontweight="bold")
        ax1.text(sx[i] + bw / 2, dp[i] + 0.25, f"{dp[i]:.2f}\n({diff:+.2f})", ha="center", va="bottom", fontsize=7.5, color=C_BLUE, fontweight="bold")

    ax1.set_xticks(sx)
    ax1.set_xticklabels([s[0] for s in sz_specs], fontsize=8.4)
    ax1.set_ylabel("双机 100GbE W=1 往返延迟 p50 (µs)")
    ax1.set_ylim(0, 19.2)
    ax1.set_title("(a) 真实 100GbE 延迟：64 B 净省 4.26 µs (−41.7%)", fontsize=9.6, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=7.2, framealpha=0.94)

    # =================================================================
    # Panel (b): Real 2-Host 100GbE Clean Host CPU Savings (MRV-133 + MRV-135)
    # =================================================================
    cpu_cases = [
        ("64 B\n@1.50M/s", "sdp_cpu_64_r1500", "sdp_rpm_cpu_64_r1500", "direct1c_cpu_64_r1500"),
        ("Mix-Ads\n@1.20M/s", "sdp_cpu_mix_ads_r1200", "sdp_rpm_cpu_mix_ads_r1200", "direct1c_cpu_mix_ads_r1200"),
        ("4 KiB\n@0.50M/s", "sdp_cpu_4096_r500", "sdp_rpm_cpu_4096_r500", "direct1c_cpu_4096_r500"),
    ]
    cx = np.arange(len(cpu_cases))
    cw = 0.25
    u_def = [med([pmu966_occ(r) for r in g133[c[1]]]) for c in cpu_cases]
    u_str = [med([pmu966_occ(r) for r in g133[c[2]]]) for c in cpu_cases]
    u_dp = [med([(1.0 - r["host"]["app_work_kps"] / idle_135) * 100.0 for r in g135[c[3]]]) for c in cpu_cases]

    ax2.bar(cx - cw, u_def, width=cw, color=C_LIGHT, edgecolor=C_GRAY, lw=0.7, label="默认 SDP（含 ~68% MMIO 空轮询）")
    ax2.bar(cx, u_str, width=cw, color=C_ORANGE, alpha=0.90, label="强对照 SDP (rx_poll_mem=1[+zc])")
    ax2.bar(cx + cw, u_dp, width=cw, color=C_BLUE, alpha=0.94, label="DirectPush + DPI DMA（干净版）")

    for i in range(len(cpu_cases)):
        ax2.text(cx[i] - cw, u_def[i] + 1.5, f"{u_def[i]:.1f}%", ha="center", va="bottom", fontsize=7.6, color=C_GRAY, fontweight="bold")
        ax2.text(cx[i], u_str[i] + 1.5, f"{u_str[i]:.1f}%", ha="center", va="bottom", fontsize=7.6, color="#9a3412", fontweight="bold")
        ratio = u_str[i] / u_dp[i]
        ax2.text(cx[i] + cw, u_dp[i] + 1.5, f"{u_dp[i]:.1f}%\n({ratio:.2f}×\nvs强对照)", ha="center", va="bottom", fontsize=7.3, color=C_BLUE, fontweight="bold")

    ax2.set_xticks(cx)
    ax2.set_xticklabels([c[0] for c in cpu_cases], fontsize=8.4)
    ax2.set_ylabel("同吞吐 saturn 主机核占用 (%)")
    ax2.set_ylim(0, 118)
    ax2.set_title("(b) 真实 100GbE 省核：相对强对照实省 1.61–3.24×", fontsize=9.6, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=7.1, framealpha=0.94)

    # =================================================================
    # Panel (c): Real 2-Host 100GbE 4C RSS 256 B Scaling & Honest Ceiling
    # =================================================================
    r4_items = [
        ("无整形\nW=64\n(MRV-135)", med([r["client"]["achieved_mrps"] * 4 for r in g135["sub_cl0_b8_r4000"]]), C_RED, "锁死悬崖"),
        ("C_line=48\n1H+4C\n(MRV-135)", med([r["client"]["achieved_mrps"] * 4 for r in g135["sub_cl48_b8_r6000"]]), C_ORANGE, "打破悬崖"),
        ("128B子环\n1H C=64\n(MRV-137)", med([r["client"]["achieved_mrps"] * 4 for r in g137["h1_cl64_w256"]]), C_BLUE, "单核99.1%"),
        ("128B子环\n4H C=64\n(MRV-137)", med([r["client"]["achieved_mrps"] * 4 for r in g137["h4_cl64_w256"]]), C_TEAL, "锁步1.91µs"),
        ("无整形\n4H W=256\n(MRV-137)", med([r["client"]["achieved_mrps"] * 4 for r in g137["h4_s128_cl0_w256"]]), C_GREEN, "达83.8%"),
        ("card_echo\n4C W=256\n(无PCIe)", med([r["client"]["achieved_mrps"] * 4 for r in g137["card_echo_4c_w256"]]), C_GRAY, "同窗上限"),
    ]
    rx = np.arange(len(r4_items))
    rvals = [x[1] for x in r4_items]
    rcols = [x[2] for x in r4_items]
    rbars = ax3.bar(rx, rvals, color=rcols, width=0.58, alpha=0.92)
    ax3.axhline(13.4, color=C_RED, ls="--", lw=1.1, alpha=0.75, label="PEM0 13.4 M 行/s 慢崖")
    for bar, (_, v, _, note) in zip(rbars, r4_items):
        ax3.text(bar.get_x() + bar.get_width() / 2, v + 1.0, f"{v:.1f}\n{note}", ha="center", va="bottom", fontsize=7.0, fontweight="bold")
    ax3.set_xticks(rx)
    ax3.set_xticklabels([x[0] for x in r4_items], fontsize=7.3)
    ax3.set_ylabel("真实 100GbE 256 B 速率 (M 64B行/s)")
    ax3.set_ylim(0, 72)
    ax3.set_title("(c) 4 核 RSS 突围：13.3→33.5–48.2 M 行/s", fontsize=9.6, fontweight="bold")
    ax3.legend(loc="upper left", fontsize=7.2, framealpha=0.94)

    fig.suptitle(
        "MRV-120 真实双机 100GbE（mercury CX5 ⇄ 3m DAC ⇄ CN106 eth0 ⇄ saturn）核心实测总览（759 轮零错，MRV-133/135/137）",
        fontsize=10.6, fontweight="bold", y=0.99,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    png = HERE / "mrv-120-final-scorecard.png"
    fig.savefig(png)
    fig.savefig(HERE / "mrv-120-final-scorecard.svg")
    plt.close(fig)
    print("Saved", png)


if __name__ == "__main__":
    main()
