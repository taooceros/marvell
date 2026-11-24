#!/usr/bin/env python3
"""MRV-161 Figure 3: Open-loop 64 B Poisson latency comparison (default vs --bar4-first vs --cqe-peek).

Reads raw verified runs directly from logs/mrv161-2026-11-24/runs.jsonl.
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv161-2026-11-24/runs.jsonl"

ARMS = [
    ("default", "default（原装 LDADDA）", "#64748b"),
    ("bar4_first", "--bar4-first（盲窗暂缓）", "#d97706"),
    ("cqe_peek", "--cqe-peek（缓存窥探）", "#1d6fa5"),
]

RATES = [
    (500, "0.5 M/s"),
    (1000, "1.0 M/s"),
    (1500, "1.5 M/s"),
]


def load_runs():
    rows = []
    for line in RUNS.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r.get("verified") is True
        rows.append(r)
    return rows


def stats(rows, cfg, getter):
    vals = [getter(r) for r in rows if r["cfg"] == cfg]
    assert len(vals) == 3, f"expected 3 reps for {cfg}, got {len(vals)}"
    med = statistics.median(vals)
    return med, med - min(vals), max(vals) - med, vals


def main():
    rows = load_runs()

    fig, axes = plt.subplots(1, 3, figsize=(14.6, 5.1))
    fig.suptitle(
        "MRV-161 开环泊松到达（64 B，λ = 0.5 / 1.0 / 1.5 Mreq/s）延迟对照：default vs --bar4-first vs --cqe-peek\n"
        "三策略共用同随机种子到达轨迹 · 每点 40,000 笔（+5,000 预热）· n=3 中位数（竖线为 min–max，圆点为单次重复）",
        fontsize=12.0,
        fontweight="bold",
        y=0.985,
    )

    panels = [
        (axes[0], "tot_p50_us", "A. 端到端中位延迟 p50", "mercury 端到端 p50 (µs)", 8.5),
        (axes[1], "tot_p95_us", "B. 端到端尾延迟 p95", "mercury 端到端 p95 (µs)", 11.6),
        (axes[2], "tot_p99_us", "C. 端到端高尾延迟 p99", "mercury 端到端 p99 (µs)", 15.6),
    ]

    x = np.arange(len(RATES))
    width = 0.26

    for ax, fld, title, ylabel, ymax in panels:
        for idx, (pol, label, color) in enumerate(ARMS):
            meds, lo, hi, all_vals = [], [], [], []
            for rk, _ in RATES:
                m, dlo, dhi, vals = stats(rows, f"direct_{pol}_64_p{rk}k", lambda r, f=fld: r["client"][f])
                meds.append(m)
                lo.append(dlo)
                hi.append(dhi)
                all_vals.append(vals)
            pos = x + (idx - 1) * width
            ax.bar(pos, meds, width=width * 0.90, color=color, alpha=0.88, label=label, zorder=3)
            ax.errorbar(pos, meds, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.1, capsize=2.5, zorder=4)
            for px, vals in zip(pos, all_vals):
                ax.scatter([px] * len(vals), vals, s=16, color="#0f172a", alpha=0.55, zorder=5)
            for px, m, dhi in zip(pos, meds, hi):
                top = m + dhi
                ax.text(
                    px,
                    top + ymax * 0.018,
                    f"{m:.3f}",
                    ha="center",
                    va="bottom",
                    fontsize=7.9,
                    fontweight="bold" if pol == "cqe_peek" else "normal",
                    color=color if pol != "default" else "#334155",
                )

        ax.set_xticks(x)
        ax.set_xticklabels([r[1] for r in RATES], fontsize=10)
        ax.set_xlabel("开环泊松目标到达率 λ", fontsize=9.8)
        ax.set_ylabel(ylabel, fontsize=9.8)
        ax.set_ylim(0, ymax)
        ax.set_title(title, fontsize=11, fontweight="bold", pad=8)

    axes[0].legend(loc="upper left", frameon=True, facecolor="white", framealpha=0.94, fontsize=8.6)

    # Dynamic callouts computed directly from runs.jsonl
    p500_b4_act = stats(rows, "direct_bar4_first_64_p500k", lambda r: r["card"]["bar4_first_activations"])[0]
    p500_tot_rpcs = stats(rows, "direct_bar4_first_64_p500k", lambda r: r["warmup"] + r["rounds"])[0]
    p500_b4_pct = p500_b4_act / p500_tot_rpcs * 100.0
    p500_def_95 = stats(rows, "direct_default_64_p500k", lambda r: r["client"]["tot_p95_us"])[0]
    p500_b4_95 = stats(rows, "direct_bar4_first_64_p500k", lambda r: r["client"]["tot_p95_us"])[0]
    p500_pk_95 = stats(rows, "direct_cqe_peek_64_p500k", lambda r: r["client"]["tot_p95_us"])[0]

    axes[1].annotate(
        f"0.5 M/s 盲窗触发 {p500_b4_pct:.1f}%\n"
        f"--bar4-first p95 +{p500_b4_95 - p500_def_95:.2f} µs\n"
        f"--cqe-peek 最低 {p500_pk_95:.3f} µs",
        xy=(0.0, p500_b4_95 + 0.65),
        xytext=(0.05, 9.60),
        fontsize=8.4,
        fontweight="bold",
        color="#92400e",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#fffbeb", edgecolor="#d97706", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#d97706", lw=1.2),
        zorder=6,
    )

    p1500_def_99, _, p1500_def_hi, _ = stats(rows, "direct_default_64_p1500k", lambda r: r["client"]["tot_p99_us"])
    p1500_pk_99 = stats(rows, "direct_cqe_peek_64_p1500k", lambda r: r["client"]["tot_p99_us"])[0]
    axes[2].annotate(
        f"1.5 M/s default 空轮询拖尾\n"
        f"{p1500_def_99:.3f} → {p1500_pk_99:.3f} µs (\u2212{p1500_def_99 - p1500_pk_99:.2f} µs)",
        xy=(1.74, p1500_def_99 + p1500_def_hi + 1.05),
        xytext=(-0.22, 13.75),
        fontsize=8.4,
        fontweight="bold",
        color="#1d6fa5",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#f0f7fc", edgecolor="#1d6fa5", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#1d6fa5", lw=1.2),
        zorder=6,
    )

    out = mrvplot.save(fig, "mrv-161-poisson")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
