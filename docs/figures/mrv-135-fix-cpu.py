#!/usr/bin/env python3
"""MRV-135 Part 0+1 Harness Fix Verification:
Directly compares MRV-133 raw runs (before Fix 1 & Fix 2) vs MRV-135 raw runs (after Fix 1 & Fix 2)
for idle calibration, zero-traffic empty-poll overhead, and 1H+1C DirectPush host CPU occupancy.
Note: Part 1 CBR runs conserved 99.3% RPCs (CPU occupancy valid <0.1 pp), while run_mrv135.py's
cbr branch retained mid-run card control commands causing a tot p99 artifact (disclosed in text).
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

REPO = Path(__file__).resolve().parents[2]
RUNS_135 = REPO / "logs/mrv135-2026-11-21/runs.jsonl"
RUNS_133 = REPO / "logs/mrv133-2026-11-21/runs.jsonl"


def load_by_cfg(path: Path):
    d = {}
    with path.open() as f:
        for line in f:
            r = json.loads(line)
            assert r.get("verified"), f"Unverified in {path}: {r.get('cfg')}"
            d.setdefault(r["cfg"], []).append(r)
    return d


def med(v):
    return statistics.median(v)


def pmu966_occ(row):
    wk = row["host"]["app_work_kps"] * 1000.0
    cyc_rate = row["pmu"]["cycles"] / row["pmu"]["dt_s"]
    return (1.0 - (wk * 966.0) / cyc_rate) * 100.0


def main():
    c135 = load_by_cfg(RUNS_135)
    c133 = load_by_cfg(RUNS_133)

    idle_133 = med([r["cal"]["app_work_kps"] for r in c133["app_work_idle_cal"]])
    idle_135 = med([r["cal"]["app_work_kps"] for r in c135["app_work_idle_cal"]])
    zt_135 = med([r["host"]["app_work_kps"] for r in c135["direct_zero_traffic_poll"]])

    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.8))

    # Panel (a): Calibration & empty-poll rate verification
    ax = axes[0]
    labels = [
        "MRV-133 空闲校准\n(逐次 elapsed())",
        "纯 966 cyc 理论\n(@ 3.90 GHz)",
        "MRV-135 空闲校准\n(64步 rdtsc)",
        "MRV-135 零流量\nDirectPush 空轮询",
    ]
    vals = [idle_133, 3.9e9 / 966.0 / 1e3, idle_135, zt_135]
    cols = ["#e67700", "#868e96", "#1971c2", "#2b8a3e"]
    x = np.arange(len(labels))
    bars = ax.bar(x, vals, width=0.55, color=cols, alpha=0.88, edgecolor="#343a40", lw=0.8)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 18, f"{v:.1f}\nk/s",
                ha="center", va="bottom", fontsize=8.5, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.3)
    ax.set_ylabel("app_work 量子速率 (k units/s)")
    ax.set_ylim(3600, 4220)
    ax.set_title("(a) 框架修复验证：消除 -5.0% 校准偏差与空轮询开销")

    # Panel (b): 1H+1C DirectPush host CPU occupancy (before vs after Fix 1+2, plus MRV-133 SDP rpm[+zc] ref)
    ax = axes[1]
    cases = [
        ("64 B\n@ 1.50 M/s", "direct_cpu_64_r1500", "sdp_rpm_cpu_64_r1500", "direct1c_cpu_64_r1500"),
        ("Mix-Ads\n@ 1.20 M/s", "direct_cpu_mix_ads_r1200", "sdp_rpm_cpu_mix_ads_r1200", "direct1c_cpu_mix_ads_r1200"),
        ("4 KiB\n@ 0.50 M/s", "direct_cpu_4096_r500", "sdp_rpm_cpu_4096_r500", "direct1c_cpu_4096_r500"),
    ]
    x = np.arange(len(cases))
    w = 0.25

    sdp_ref = [med([pmu966_occ(r) for r in c133[k_sdp]]) for _, _, k_sdp, _ in cases]
    dp_old = [med([pmu966_occ(r) for r in c133[k_old]]) for _, k_old, _, _ in cases]
    dp_new_cal = [med([(1.0 - r["host"]["app_work_kps"] / idle_135) * 100.0 for r in c135[k_new]])
                  for _, _, _, k_new in cases]
    dp_new_pmu = [med([pmu966_occ(r) for r in c135[k_new]]) for _, _, _, k_new in cases]

    ax.bar(x - w, sdp_ref, width=w, color="#adb5bd", edgecolor="#495057", lw=0.8,
           label="MRV-133 强对照 SDP rpm[+zc] (参照)")
    ax.bar(x, dp_old, width=w, color="#74c0fc", edgecolor="#1971c2", lw=0.8, hatch="//",
           label="MRV-133 DirectPush (修复前, 纯966复算)")
    ax.bar(x + w, dp_new_cal, width=w, color="#1971c2", edgecolor="#0b3d91", lw=0.8,
           label="MRV-135 DirectPush (修复后, 64步校准)")

    for i, (sr, do, dn, dnp) in enumerate(zip(sdp_ref, dp_old, dp_new_cal, dp_new_pmu)):
        ax.text(x[i] - w, sr + 0.45, f"{sr:.1f}%", ha="center", va="bottom", fontsize=8.2, color="#495057")
        ax.text(x[i], do + 0.45, f"{do:.1f}%", ha="center", va="bottom", fontsize=8.2, color="#1971c2")
        ratio = sr / dn
        ax.text(x[i] + w, dn + 0.45, f"{dn:.1f}%\n({ratio:.2f}×)", ha="center", va="bottom",
                fontsize=8.2, color="#0b3d91", fontweight="bold")
        ax.scatter([x[i] + w], [dnp], color="#ffd43b", edgecolor="#343a40", s=28, zorder=5,
                   label="MRV-135 纯966 PMU 复算" if i == 0 else None)

    ax.text(
        0.02, 0.96,
        "注: CBR 仅用于占核复核 (总包守恒 99.3%);\ncbr 分支含卡侧控制轮询尾延迟伪影 (见正文)",
        transform=ax.transAxes, ha="left", va="top", fontsize=7.2, color="#495057",
        bbox=dict(boxstyle="round,pad=0.25", fc="#f8f9fa", ec="#ced4da", lw=0.7),
    )

    ax.set_xticks(x)
    ax.set_xticklabels([c[0] for c in cases], fontsize=9.0)
    ax.set_ylabel("saturn 服务端主机核占用 (% of 1 core)")
    ax.set_ylim(0, 30.0)
    ax.set_title("(b) 1H+1C 纯 DirectPush 修复前后占核对比 (零新增 SDP)")
    ax.legend(loc="upper right", fontsize=7.3, frameon=True, framealpha=0.93)

    fig.suptitle(
        "MRV-135 前置框架修复验证：空闲校准、空轮询开销与 1H+1C DirectPush 真实主机占核 (n = 3 零错)",
        fontsize=11.0,
        y=0.99,
    )
    out = save(fig, "mrv-135-fix-cpu")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
