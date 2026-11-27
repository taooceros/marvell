#!/usr/bin/env python3
"""MRV-236 Figure 4: Per-cell held fraction f and landing-interval CDF (held vs unheld).

Reads raw logs/mrv236-2026-11-27/runs.jsonl and sparse per-cell stamp CSVs (plain or .gz),
verifies lost=bad=torn=corrupt=0 and 100% non-multiplexed PMU, and saves
docs/figures/mrv-236-stamps-cdf.{png,svg}.
"""
from __future__ import annotations

import csv
import gzip
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv236-2026-11-27/runs.jsonl"


def open_stamp_csv(rel_path: str):
    p = ROOT / rel_path
    if p.exists():
        return p.open("rt")
    gz = Path(str(p) + ".gz")
    if gz.exists():
        return gzip.open(gz, "rt")
    raise FileNotFoundError(f"Missing stamp CSV: {p} or {gz}")


def load_gaps(recs: list[dict]) -> tuple[list[float], list[float], list[float]]:
    held_g: list[float] = []
    unheld_g: list[float] = []
    all_g: list[float] = []
    for r in recs:
        card_hz = float(r["card_pmu"]["tsc_hz"])
        ns_per_card = 1e9 / card_hz
        with open_stamp_csv(r["stamp_csv"]) as f:
            _hdr = f.readline()
            prev_cin: dict[int, float] = {}
            for row in csv.DictReader(f):
                seq = int(row["seq"])
                cin = float(row["card_t_in"])
                cwp = int(row["card_waited_polls"])
                if (seq - 1) in prev_cin:
                    gap = (cin - prev_cin[seq - 1]) * ns_per_card
                    all_g.append(gap)
                    if cwp > 0:
                        held_g.append(gap)
                    else:
                        unheld_g.append(gap)
                prev_cin[seq] = cin
    return held_g, unheld_g, all_g


def main() -> None:
    by_arm: dict[str, list[dict]] = {}
    for line in RUNS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if "stamp_csv" not in r:
            continue
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
        assert all(c["corrupt"] == 0 for c in r["card_pmu"]["cores"])
        assert r["host_pmu"]["min_pct"] >= 99.9 and r["card_pmu"]["min_pct"] >= 99.9
        by_arm.setdefault(r["arm"], []).append(r)

    bar_arms = [a for a in ("B1_sp", "S2_sp", "B", "A1", "A2", "A3", "A4", "WC4_B") if a in by_arm]
    bar_labels = {
        "B1_sp": "B1_sp\nWC1快态",
        "S2_sp": "S2_sp\nWC1+影子\n(仅记环核)",
        "B": "B\nWC2基线",
        "A1": "A1\n去next共享",
        "A2": "A2\nldr",
        "A3": "A3\nwfe",
        "A4": "A4\ncivac",
        "WC4_B": "WC4_B\n4核基线",
    }
    bar_colors = {
        "B1_sp": "#059669",
        "S2_sp": "#F97316",
        "B": "#4B5563",
        "A1": "#2563EB",
        "A2": "#6B7280",
        "A3": "#D97706",
        "A4": "#DC2626",
        "WC4_B": "#1F2937",
    }

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.2, 5.4), gridspec_kw={"width_ratios": [1.0, 1.12]})
    x = np.arange(len(bar_arms))
    f_meds = [statistics.median([r["stamp_summary"]["f_held"] for r in by_arm[a]]) * 100.0 for a in bar_arms]
    ax1.bar(
        x,
        f_meds,
        width=0.60,
        color=[bar_colors[a] for a in bar_arms],
        edgecolor="#111827",
        linewidth=0.7,
        zorder=3,
    )
    for i, a in enumerate(bar_arms):
        fs = [r["stamp_summary"]["f_held"] * 100.0 for r in by_arm[a]]
        mops_m = statistics.median([r["mops"] for r in by_arm[a]])
        ax1.scatter(np.linspace(i - 0.12, i + 0.12, len(fs)), fs, color="#111827", s=16, zorder=4)
        ax1.text(
            i,
            max(fs) + 2.2,
            f"{f_meds[i]:.1f}%\n({mops_m:.1f}M)",
            ha="center",
            va="bottom",
            fontsize=7.8,
            color="#111827",
            fontweight="bold",
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels([bar_labels[a] for a in bar_arms], fontsize=8.0)
    ax1.set_ylabel("环核空轮询持有比例 f (%)", fontsize=10)
    ax1.set_ylim(0, 118)
    ax1.set_title("逐格持有比例 f = P(card_waited_polls > 0)", fontsize=10.4, pad=8)

    def plot_cdf(vals: list[float], label: str, color: str, ls: str = "-", lw: float = 1.9):
        if not vals:
            return
        arr = np.sort(np.asarray(vals, dtype=float))
        y = np.linspace(0.0, 1.0, len(arr))
        p50 = float(np.median(arr))
        ax2.plot(arr, y, label=f"{label} (p50={p50:.0f} ns, n={len(arr)})", color=color, linestyle=ls, linewidth=lw)

    if "B1_sp" in by_arm:
        b1_h, b1_u, _ = load_gaps(by_arm["B1_sp"])
        plot_cdf(b1_u, "B1_sp 快态 未空轮询格 (含戳61/均32ns)", "#059669", ls="-", lw=2.3)
        if len(b1_h) >= 20:
            plot_cdf(b1_h, "B1_sp 快态 空轮询格", "#10B981", ls="--", lw=1.6)

    if "B" in by_arm:
        b_h, b_u, _ = load_gaps(by_arm["B"])
        plot_cdf(b_h, "B (WC2 13.1M) 空轮询格 (cw>0)", "#1D4ED8", ls="-", lw=2.2)
        plot_cdf(b_u, "B (WC2 13.1M) 未空轮询格 (cw=0)", "#60A5FA", ls="--", lw=1.8)

    if "A1" in by_arm:
        a1_h, _, _ = load_gaps(by_arm["A1"])
        plot_cdf(a1_h, "A1 (去next共享) 空轮询格", "#7C3AED", ls=":", lw=1.9)

    if "WC4_B" in by_arm:
        w4_h, _, _ = load_gaps(by_arm["WC4_B"])
        plot_cdf(w4_h, "WC4_B (4核 13.1M) 空轮询格", "#111827", ls="-.", lw=1.8)

    if "S2_sp" in by_arm:
        _, _, s2_all = load_gaps(by_arm["S2_sp"])
        plot_cdf(s2_all, "S2_sp (影子守front+0, 含戳62/均79ns)", "#DC2626", ls="-", lw=1.9)

    ax2.axvline(76.4, color="#DC2626", linestyle=":", linewidth=1.3, alpha=0.85)
    ax2.text(80.0, 0.965, "76.4 ns (1/13.1 M/s)", fontsize=8.3, color="#DC2626", fontweight="bold", va="center")
    ax2.set_xlim(-50, 260)
    ax2.set_ylim(0, 1.02)
    ax2.set_xlabel("相邻序列号看到间隔 Δcard_t_in = card_t_in[seq] − card_t_in[seq−1] (ns)", fontsize=9.5)
    ax2.set_ylabel("累积分布 CDF", fontsize=10)
    ax2.set_title("逐格间隔 CDF：被空轮询格 80–81 ns（B1_sp 均 32 ns）", fontsize=10.4, pad=8)
    ax2.legend(loc="lower right", fontsize=7.3, frameon=True, facecolor="white", framealpha=0.93)

    fig.suptitle(
        "MRV-236 逐格稀疏戳分析（4 字段：首次轮询/完整时刻/空轮询次数/核号，W=64, 64 B, batch=1, n=3 汇合）",
        fontsize=10.6,
        fontweight="bold",
        y=0.98,
    )
    out = save(fig, "mrv-236-stamps-cdf")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
