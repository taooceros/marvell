#!/usr/bin/env python3
"""MRV-158 Fig 1: Zero-backlog credit-check overhead (credit check OFF vs ON)."""
import json
import pathlib
import statistics
import numpy as np

from mrvplot import plt, save

LOG_PATH = pathlib.Path(__file__).resolve().parents[2] / "logs/mrv158-2026-11-23/runs.jsonl"


def main():
    rows = [json.loads(l) for l in LOG_PATH.open() if l.strip()]
    p1 = {}
    for r in rows:
        if r["part"] == "part1_zero_backlog":
            assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
            assert r["host_pmu"]["min_pct"] >= 99.99 and r["card_pmu"]["min_pct"] >= 99.99
            p1.setdefault(r["tag"], []).append(r)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.6, 4.8))
    c_off = "#64748b"
    c_on = "#0f766e"

    # Panel A: W=1 RTT (p50 & p99)
    w1_off = p1["w1_credit_off"]
    w1_on = p1["w1_credit_on"]
    x = np.array([0, 1])
    w = 0.32
    off_vals = [
        statistics.median(r["lat_us"]["p50"] for r in w1_off),
        statistics.median(r["lat_us"]["p99"] for r in w1_off),
    ]
    on_vals = [
        statistics.median(r["lat_us"]["p50"] for r in w1_on),
        statistics.median(r["lat_us"]["p99"] for r in w1_on),
    ]
    off_mean = statistics.median(r["lat_us"]["mean"] for r in w1_off)
    on_mean = statistics.median(r["lat_us"]["mean"] for r in w1_on)
    mean_diff_ns = (on_mean - off_mean) * 1000.0

    b1 = ax1.bar(x - w / 2, off_vals, width=w, color=c_off, label="关闭额度检查", zorder=3)
    b2 = ax1.bar(x + w / 2, on_vals, width=w, color=c_on, label="打开额度检查", zorder=3)
    for idx, metric in enumerate(["p50", "p99"]):
        for r in w1_off:
            ax1.scatter(x[idx] - w / 2, r["lat_us"][metric], color="#1e293b", s=18, zorder=4)
        for r in w1_on:
            ax1.scatter(x[idx] + w / 2, r["lat_us"][metric], color="#042f2e", s=18, zorder=4)

    for rect, val in zip(b1, off_vals):
        ax1.text(rect.get_x() + rect.get_width() / 2, val + 0.025, f"{val:.3f}", ha="center", va="bottom", fontsize=10, color="#334155")
    for rect, val, base in zip(b2, on_vals, off_vals):
        diff_ns = (val - base) * 1000.0
        ax1.text(
            rect.get_x() + rect.get_width() / 2,
            val + 0.025,
            f"{val:.3f}\n(+{diff_ns:.0f} ns)",
            ha="center",
            va="bottom",
            fontsize=9.5,
            fontweight="bold",
            color=c_on,
        )

    ax1.text(
        0.02,
        0.95,
        f"均值：{off_mean:.3f} → {on_mean:.3f} µs（+{mean_diff_ns:.0f} ns）",
        transform=ax1.transAxes,
        va="top",
        ha="left",
        fontsize=9.2,
        color="#334155",
        bbox=dict(boxstyle="round,pad=0.25", fc="#f8fafc", ec="#cbd5e1", lw=0.8),
    )
    ax1.set_xticks(x)
    ax1.set_xticklabels(["中位延迟 p50", "尾延迟 p99"], fontsize=11)
    ax1.set_ylabel("空载往返延迟 (µs)", fontsize=11)
    ax1.set_ylim(0, 1.24)
    ax1.set_title("(a) 零积压单包往返延迟（W=1）", fontsize=11.8, fontweight="bold", pad=8)
    ax1.legend(loc="lower right", frameon=True, facecolor="white")

    # Panel B: Throughput (W=32 closed-loop OFF vs ON, plus C2H 20M full-verified stream ON)
    w32_off = p1["w32_credit_off"]
    w32_on = p1["w32_credit_on"]
    st_on = p1["stream_credit_on"]

    m_w32_off = statistics.median(r["mops"] for r in w32_off)
    m_w32_on = statistics.median(r["mops"] for r in w32_on)
    m_st_on = statistics.median(r["mops"] for r in st_on)
    st_gbps = statistics.median(r["gbps"] for r in st_on)
    w32_pct = (m_w32_on - m_w32_off) / m_w32_off * 100.0

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.grid(False)

    ax2.bar([0.0], [m_w32_off], width=0.32, color=c_off, zorder=3, label="关闭额度检查")
    ax2.bar([0.38], [m_w32_on], width=0.32, color=c_on, zorder=3, label="打开额度检查")
    for r in w32_off:
        ax2.scatter(0.0, r["mops"], color="#1e293b", s=18, zorder=4)
    for r in w32_on:
        ax2.scatter(0.38, r["mops"], color="#042f2e", s=18, zorder=4)

    ax2_r.bar([1.32], [m_st_on], width=0.36, color="#0d9488", alpha=0.9, zorder=3)
    for r in st_on:
        ax2_r.scatter(1.32, r["mops"], color="#042f2e", s=18, zorder=4)

    ax2.text(0.0, m_w32_off + 0.35, f"{m_w32_off:.2f}\nM/s", ha="center", va="bottom", fontsize=10, color="#334155")
    ax2.text(
        0.38,
        m_w32_on + 0.35,
        f"{m_w32_on:.2f}\nM/s (+{w32_pct:.1f}%)",
        ha="center",
        va="bottom",
        fontsize=9.5,
        fontweight="bold",
        color=c_on,
    )
    ax2_r.text(
        1.32,
        m_st_on + 6.0,
        f"{m_st_on:.1f} M/s\n({st_gbps:.2f} GB/s)\nDDIO 100%",
        ha="center",
        va="bottom",
        fontsize=9.5,
        fontweight="bold",
        color="#0f766e",
    )

    ax2.axvline(0.86, color="#cbd5e1", linestyle="--", linewidth=1.2)
    ax2.set_xticks([0.19, 1.32])
    ax2.set_xticklabels(["双向闭环（W=32）\n关闭 vs 打开额度", "单向推流上限验证（单柱）\n仅测开额度 · 20M 全校验"], fontsize=10.0)
    ax2.set_ylabel("双向闭环吞吐 (M 条/s)", fontsize=10.5, color="#334155")
    ax2_r.set_ylabel("单向推流吞吐 (M 条/s)", fontsize=10.5, color="#0f766e")
    ax2.set_ylim(0, 17.8)
    ax2_r.set_ylim(0, 238.0)
    ax2.set_title("(b) 零积压吞吐（W=32 对照 + 开额度推流单柱验证）", fontsize=11.2, fontweight="bold", pad=8)

    fig.suptitle(
        "图 1：零积压时打开额度检查几乎零代价（64 B、256 KiB 环、1 主机核 3.90 GHz + 1 卡核 2.00 GHz、n=3）",
        fontsize=12.0,
        fontweight="bold",
        y=0.985,
    )
    out = save(fig, "mrv-158-zero-backlog")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
