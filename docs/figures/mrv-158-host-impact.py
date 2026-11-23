#!/usr/bin/env python3
"""MRV-158 Fig 2: Host-side impact as burst backlog grows from 64 KiB to 128 MiB.
Single variable: Scheme A (128 MiB host ring) vs Scheme B (active NIC backlog + 256 KiB host hot ring).
"""
import json
import pathlib
import statistics
import numpy as np

from mrvplot import plt, save

LOG_PATH = pathlib.Path(__file__).resolve().parents[2] / "logs/mrv158-2026-11-23/runs.jsonl"
SIZES = ["64KiB", "256KiB", "1MiB", "4MiB", "8MiB", "16MiB", "32MiB", "48MiB", "64MiB", "96MiB", "128MiB"]
SIZE_MIB = [64 / 1024, 256 / 1024, 1, 4, 8, 16, 32, 48, 64, 96, 128]
XTICK_VALS = [64 / 1024, 256 / 1024, 1, 4, 8, 16, 32, 64, 128]
XTICK_LBLS = ["64K", "256K", "1M", "4M", "8M", "16M", "32M", "64M", "128M"]


def series(rows, scheme, fn):
    meds, los, his = [], [], []
    for sz in SIZES:
        vals = [fn(r) for r in rows if r["scheme"] == scheme and r["size_label"] == sz]
        assert len(vals) == 3, f"expected 3 reps for {scheme}/{sz}"
        meds.append(statistics.median(vals))
        los.append(min(vals))
        his.append(max(vals))
    return np.array(meds), np.array(los), np.array(his)


def add_guides(ax):
    ax.axvline(8.0, color="#94a3b8", linestyle=":", linewidth=1.2, zorder=1)
    ax.axvline(60.0, color="#94a3b8", linestyle="--", linewidth=1.2, zorder=1)
    ax.set_xscale("log", base=2)
    ax.set_xticks(XTICK_VALS)
    ax.set_xticklabels(XTICK_LBLS, fontsize=9.5)
    ax.set_xlabel("突发积压大小 B（64 B 消息，固定到达速率 100 M/s）", fontsize=10)


def main():
    rows = [json.loads(l) for l in LOG_PATH.open() if l.strip()]
    p2 = [r for r in rows if r["part"] == "part2_backlog"]
    for r in p2:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
        assert r["host_pmu"]["min_pct"] >= 99.99 and r["card_pmu"]["min_pct"] >= 99.99

    x = np.array(SIZE_MIB)
    c_a = "#475569"
    c_a_wr = "#94a3b8"
    c_b = "#0f766e"

    fig, axes = plt.subplots(1, 3, figsize=(15.2, 4.6))

    # (a) Host drain ns/cell
    ax = axes[0]
    a_m, a_lo, a_hi = series(p2, "scheme_a_128m", lambda r: r["drain_wall_ns_per_cell"])
    b_m, b_lo, b_hi = series(p2, "scheme_b_256k", lambda r: r["drain_wall_ns_per_cell"])
    ax.plot(x, a_m, "-o", color=c_a, lw=2.2, ms=5.5, label="方案 A：主机 128 MiB 大环")
    ax.fill_between(x, a_lo, a_hi, color=c_a, alpha=0.14)
    ax.plot(x, b_m, "-s", color=c_b, lw=2.4, ms=5.5, label="方案 B：留网卡 + 256 KiB 热环")
    ax.fill_between(x, b_lo, b_hi, color=c_b, alpha=0.15)
    add_guides(ax)
    ax.set_ylim(2.4, 6.9)
    ax.set_ylabel("主机排空单包耗时 (ns/包，越低越快)", fontsize=10.5)
    ax.set_title("(a) 主机排空单包耗时", fontsize=11.5, fontweight="bold")
    ax.text(8.0 * 1.08, 6.55, "DDIO\n8 MiB", fontsize=8.5, color="#64748b", va="top")
    ax.text(60.0 * 0.90, 6.55, "主机 L3\n60 MiB", fontsize=8.5, color="#64748b", va="top", ha="right")
    ax.annotate("≥48 MiB 反超\n6.21 → 5.69 ns", xy=(128, 5.69), xytext=(22, 3.05),
                arrowprops=dict(arrowstyle="->", color=c_b, lw=1.3),
                fontsize=9, fontweight="bold", color=c_b,
                bbox=dict(boxstyle="round,pad=0.25", fc="#f0fdfa", ec=c_b, lw=0.9))
    ax.legend(loc="lower left", fontsize=9, frameon=True, facecolor="white")

    # (b) Host LLC read misses (OCR.READS_TO_CORE.L3_MISS / cell, includes HW prefetch)
    ax = axes[1]
    a_m, a_lo, a_hi = series(p2, "scheme_a_128m", lambda r: r["host_pmu"]["ocr_l3_miss"] / r["measured_cells"])
    b_m, b_lo, b_hi = series(p2, "scheme_b_256k", lambda r: r["host_pmu"]["ocr_l3_miss"] / r["measured_cells"])
    ax.plot(x, a_m * 100, "-o", color=c_a, lw=2.2, ms=5.5, label="方案 A：主机 128 MiB 大环")
    ax.fill_between(x, a_lo * 100, a_hi * 100, color=c_a, alpha=0.14)
    ax.plot(x, b_m * 100, "-s", color=c_b, lw=2.4, ms=5.5, label="方案 B：留网卡 + 256 KiB 热环")
    add_guides(ax)
    ax.set_ylim(-4, 105)
    ax.set_ylabel("主机 LLC 读缺失率 (% / 包，含硬件预取)", fontsize=10.5)
    ax.set_title("(b) 主机 LLC 读缺失（保住主机缓存）", fontsize=11.5, fontweight="bold")
    ax.text(128, a_m[-1] * 100 + 3, f"{a_m[-1]*100:.1f}%", ha="right", fontsize=10, fontweight="bold", color=c_a)
    ax.text(128, 4.5, "全程 0.0%", ha="right", fontsize=10, fontweight="bold", color=c_b)
    ax.legend(loc="upper left", fontsize=9, frameon=True, facecolor="white")

    # (c) Host IMC DRAM reads & writes (64 B lines / cell)
    ax = axes[2]
    ar_m, ar_lo, ar_hi = series(p2, "scheme_a_128m", lambda r: r["host_pmu"]["imc_cas_rd"] / r["measured_cells"])
    aw_m, aw_lo, aw_hi = series(p2, "scheme_a_128m", lambda r: r["host_pmu"]["imc_cas_wr"] / r["measured_cells"])
    br_m, _, _ = series(p2, "scheme_b_256k", lambda r: r["host_pmu"]["imc_cas_rd"] / r["measured_cells"])
    bw_m, _, _ = series(p2, "scheme_b_256k", lambda r: r["host_pmu"]["imc_cas_wr"] / r["measured_cells"])

    ax.plot(x, ar_m, "-o", color=c_a, lw=2.2, ms=5.0, label="方案 A：主存读 (IMC RD)")
    ax.plot(x, aw_m, "--^", color=c_a_wr, lw=2.0, ms=5.0, label="方案 A：主存写 (IMC WR)")
    ax.plot(x, br_m, "-s", color=c_b, lw=2.3, ms=5.0, label="方案 B：主存读 (≈0.01)")
    ax.plot(x, bw_m, ":", color="#14b8a6", lw=2.0, label="方案 B：主存写 (≈0.01)")
    add_guides(ax)
    ax.set_ylim(-0.05, 1.15)
    ax.set_ylabel("主机 IMC 主存流量 (64 B 行/包)", fontsize=10.5)
    ax.set_title("(c) 主机主存读写（−98.9% DRAM 流量）", fontsize=11.5, fontweight="bold")
    ax.text(128, aw_m[-1] + 0.04, f"写 {aw_m[-1]:.2f}", ha="right", fontsize=9, color="#475569")
    ax.text(128, ar_m[-1] - 0.09, f"读 {ar_m[-1]:.2f}", ha="right", fontsize=9, fontweight="bold", color=c_a)
    ax.text(28, 0.08, "方案 B 读写合计 ≈ 0.02 行/包", ha="center", fontsize=9, fontweight="bold", color=c_b)
    ax.legend(loc="center left", fontsize=8.5, frameon=True, facecolor="white")

    fig.suptitle("图 2：突发积压 64 KiB → 128 MiB 单变量对比——把积压留在主动网卡，主机缓存零污染、主存读写近零（n=3 中位数与极值带）", fontsize=12.2, fontweight="bold", y=1.02)
    out = save(fig, "mrv-158-host-impact")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
