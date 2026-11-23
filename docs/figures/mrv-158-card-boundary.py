#!/usr/bin/env python3
"""MRV-158 Fig 3: Card-side single-hop refill latency and 48 MiB card L3 / DDR5 boundary (Scheme B)."""
import json
import pathlib
import statistics
import numpy as np

from mrvplot import plt, save

LOG_PATH = pathlib.Path(__file__).resolve().parents[2] / "logs/mrv158-2026-11-23/runs.jsonl"
REFILL_SIZES = ["512KiB", "1MiB", "4MiB", "16MiB", "32MiB", "48MiB", "64MiB", "96MiB", "128MiB"]
REFILL_MIB = [0.5, 1, 4, 16, 32, 48, 64, 96, 128]

DRAIN_SIZES = ["1MiB", "4MiB", "8MiB", "16MiB", "32MiB", "48MiB", "64MiB", "96MiB", "128MiB"]
DRAIN_MIB = [1, 4, 8, 16, 32, 48, 64, 96, 128]


def main():
    rows = [json.loads(l) for l in LOG_PATH.open() if l.strip()]
    p1 = [r for r in rows if r["part"] == "part1_zero_backlog"]
    p2b = [r for r in rows if r["part"] == "part2_backlog" and r["scheme"] == "scheme_b_256k"]
    p3 = [r for r in rows if r["part"] == "part3_refill_hop"]
    for r in p1 + p2b + p3:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
        assert r["host_pmu"]["min_pct"] >= 99.99 and r["card_pmu"]["min_pct"] >= 99.99

    w1_on_p50 = statistics.median(r["lat_us"]["p50"] for r in p1 if r["tag"] == "w1_credit_on")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.8, 4.9))
    c_teal = "#0f766e"
    c_slate = "#475569"
    c_amber = "#b45309"

    # ---- Panel (a): Single-hop refill latency vs backlog ----
    xr = np.array(REFILL_MIB)
    p50s, p99s, f_meds, f_los, f_his = [], [], [], [], []
    for sz in REFILL_SIZES:
        rs = [r for r in p3 if r["size_label"] == sz]
        assert len(rs) == 3
        p50s.append(statistics.median(r["hop_us"]["p50"] for r in rs))
        p99s.append(statistics.median(r["hop_us"]["p99"] for r in rs))
        f_all = [v for r in rs for v in r["first_hops_us"]]
        f_meds.append(statistics.median(f_all))
        f_los.append(min(f_all))
        f_his.append(max(f_all))

    ax1.axvline(48.0, color="#94a3b8", linestyle="--", linewidth=1.3, zorder=1)
    ax1.axhline(w1_on_p50, color="#94a3b8", linestyle=":", linewidth=1.3, zorder=1, label=f"空载 W=1 参考 ({w1_on_p50:.3f} µs)")
    ax1.plot(xr, p50s, "-s", color=c_teal, lw=2.4, ms=5.2, label="后续连续补推 p50（0.905 µs）")
    ax1.plot(xr, p99s, "--o", color="#14b8a6", lw=1.8, ms=4.5, label="后续连续补推 p99（0.945 µs）")
    ax1.plot(xr, f_meds, "-^", color=c_amber, lw=1.9, ms=5.2, label="停顿恢复首包冷读中位数 (n=12)")
    ax1.fill_between(xr, f_los, f_his, color=c_amber, alpha=0.12)

    ax1.set_xscale("log", base=2)
    ax1.set_xticks([0.5, 1, 4, 16, 32, 64, 128])
    ax1.set_xticklabels(["512K", "1M", "4M", "16M", "32M", "64M", "128M"], fontsize=9.5)
    ax1.set_ylim(0.76, 1.50)
    ax1.set_xlabel("突发积压大小 B（方案 B，主机热环固定 256 KiB）", fontsize=10)
    ax1.set_ylabel("还额 → 新包落盘单跳耗时 (µs)", fontsize=10.5)
    ax1.set_title("(a) 还额补推单跳延迟（全程走亚微秒快路径）", fontsize=11.5, fontweight="bold", pad=8)
    ax1.text(48.0 * 0.90, 1.46, "卡侧 L3\n48 MiB", ha="right", va="top", fontsize=9, color="#64748b")
    ax1.annotate(
        f"首包中位 {f_meds[-1]:.3f} µs",
        xy=(128, f_meds[-1]),
        xytext=(24, 1.24),
        ha="center",
        arrowprops=dict(arrowstyle="->", color=c_amber, lw=1.2),
        fontsize=9.0,
        fontweight="bold",
        color=c_amber,
    )
    ax1.text(128, 0.84, "后续连续补推全程 0.905 µs", ha="right", fontsize=9.3, fontweight="bold", color=c_teal)
    ax1.legend(loc="upper left", fontsize=8.4, frameon=True, facecolor="white")

    # ---- Panel (b): Card drain rate (upper band) + Card L3 miss & DDR traffic (lower band) ----
    xd = np.array(DRAIN_MIB)
    c_drn, c_drn_lo, c_drn_hi = [], [], []
    c_llm, c_ddr_r, c_ddr_w = [], [], []
    for sz in DRAIN_SIZES:
        rs = [r for r in p2b if r["size_label"] == sz]
        assert len(rs) == 3
        drns = [r["card_pmu"]["drain_mops"] for r in rs]
        c_drn.append(statistics.median(drns))
        c_drn_lo.append(min(drns))
        c_drn_hi.append(max(drns))
        c_llm.append(statistics.median(r["card_pmu"]["core"]["ll_cache_miss_rd"] / r["measured_cells"] for r in rs))
        c_ddr_r.append(statistics.median(r["card_pmu"]["ddr_reads"] / r["measured_cells"] for r in rs))
        c_ddr_w.append(statistics.median(r["card_pmu"]["ddr_writes"] / r["measured_cells"] for r in rs))

    ax2.axvline(48.0, color="#94a3b8", linestyle="--", linewidth=1.3, zorder=1)
    l1, = ax2.plot(xd, c_drn, "-s", color=c_teal, lw=2.5, ms=5.2, label="卡侧排空速率 (左轴，M 条/s)")
    ax2.fill_between(xd, c_drn_lo, c_drn_hi, color=c_teal, alpha=0.14)
    ax2.set_xscale("log", base=2)
    ax2.set_xticks([1, 4, 8, 16, 32, 64, 128])
    ax2.set_xticklabels(["1M", "4M", "8M", "16M", "32M", "64M", "128M"], fontsize=9.5)
    ax2.set_ylim(100, 198)
    ax2.set_xlabel("突发积压大小 B（超出 256 KiB 部分留存卡内）", fontsize=10)
    ax2.set_ylabel("卡侧排空速率 (M 条/s，64 B)", fontsize=10.5, color=c_teal)
    ax2.tick_params(axis="y", labelcolor=c_teal)

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.grid(False)
    l2, = ax2_r.plot(xd, c_ddr_r, "-o", color=c_slate, lw=2.0, ms=4.8, label="卡 DDR5 读 (右轴，64 B 次/包)")
    l3, = ax2_r.plot(xd, c_ddr_w, "--^", color="#94a3b8", lw=1.8, ms=4.5, label="卡 DDR5 写 (右轴，64 B 次/包)")
    l4, = ax2_r.plot(xd, c_llm, ":d", color=c_amber, lw=1.9, ms=4.5, label="卡 L3 读缺失 (右轴，128 B 行/包)")
    ax2_r.set_ylim(-0.08, 1.68)
    ax2_r.set_yticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2])
    ax2_r.set_ylabel("卡侧 L3 缺失 / DDR5 读写 (次/包)", fontsize=10.5, color=c_slate)

    ax2.text(48.0 * 0.88, 152, "卡侧 L3\n48 MiB", ha="right", va="top", fontsize=9, color="#64748b")
    ax2.text(1.1, 183.5, "全程平稳 175.4–177.0 M/s (11.2–11.3 GB/s)", ha="left", fontsize=9.2, fontweight="bold", color=c_teal)
    ax2_r.text(
        128,
        c_ddr_r[-1] + 0.09,
        f"DDR 读/写 {c_ddr_r[-1]:.2f}/{c_ddr_w[-1]:.2f}",
        ha="right",
        fontsize=8.8,
        fontweight="bold",
        color=c_slate,
        bbox=dict(boxstyle="square,pad=0.14", fc="white", ec="none", alpha=0.90),
    )
    ax2_r.text(128, 0.17, f"L3 缺失 {c_llm[-1]:.2f}", ha="right", fontsize=8.8, fontweight="bold", color=c_amber)

    ax2.set_title("(b) 跨过 48 MiB 卡 L3 后的排空速率与卡 DDR 边界", fontsize=11.5, fontweight="bold", pad=8)
    ax2.legend(handles=[l1, l2, l3, l4], loc="center left", bbox_to_anchor=(0.01, 0.43), fontsize=8.3, frameon=True, facecolor="white")

    fig.suptitle(
        "图 3：方案 B 卡侧快路径与 48 MiB L3 边界——还额单跳恒定 0.905 µs，溢出由卡 6 通道 DDR5 兜底且排空不掉速（n=3）",
        fontsize=11.8,
        fontweight="bold",
        y=0.985,
    )
    out = save(fig, "mrv-158-card-boundary")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
