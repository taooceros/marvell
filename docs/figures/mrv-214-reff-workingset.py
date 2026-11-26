#!/usr/bin/env python3
"""MRV-214 Fig 1: R_eff left BDP starvation knee and right working-set spill boundaries."""
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv214-2026-11-26/runs.jsonl"


def load_runs():
    rows = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    by_cfg = {}
    for r in rows:
        assert r["verified"], f"unverified run: {r['cfg']}"
        assert r["host_pmu"]["page_faults"] == 0
        assert r["host_final"]["bad"] == 0 and r["host_final"]["torn"] == 0
        assert r["card_final"]["wire_bad"] == 0 and r["card_final"]["wire_torn"] == 0
        assert r["card_final"]["credit_torn"] == 0
        if r["spec"]["credit"] != "off":
            assert r["c2h_lost"] == 0
        by_cfg.setdefault(r["cfg"], []).append(r)
    return by_cfg


def stats3(rs, fn):
    vals = [fn(x) for x in rs]
    med = statistics.median(vals)
    return med, med - min(vals), max(vals) - med


def main():
    by_cfg = load_runs()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.4))
    fig.suptitle(
        "MRV-214 扫描一：有效信用上限 R_eff 左 BDP 饥饿拐点与右工作集溢出边界",
        fontsize=12.8,
        fontweight="bold",
        y=0.985,
    )
    fig.text(
        0.5,
        0.935,
        "100GbE 1500 B UDP · 标准 SDP (ism_enable=0) + 无 SFENCE BAR4 信用环 · saturn 3.9 GHz (Socket-1 L3 60 MiB 规格) · n=3 中位数(min–max) · 全程 0 丢包",
        ha="center",
        fontsize=9.2,
        color="#444444",
    )

    # ---------------- Panel A: Left BDP starvation knee ----------------
    reffs = [16, 24, 32, 48, 64, 96, 128, 192, 256]
    ys, lo, hi, stalls = [], [], [], []
    for r in reffs:
        rs = by_cfg[f"s1a_1500b_r{r}_oq512_b8"]
        m, l, h = stats3(rs, lambda x: x["rates"]["host_wire_gbps"])
        ys.append(m); lo.append(l); hi.append(h)
        stalls.append(statistics.median([x["rates"]["steady_credit_stalls"] for x in rs]))

    rs_oq256 = by_cfg["s1a_1500b_r128_oq256_b8"]
    m256, l256, h256 = stats3(rs_oq256, lambda x: x["rates"]["host_wire_gbps"])

    ax1.axvspan(14, 72, color="#fdecea", alpha=0.65, zorder=0)
    ax1.axvspan(90, 290, color="#eaf7ee", alpha=0.65, zorder=0)
    ax1.axhline(98.6, color="#666666", linestyle="--", linewidth=1.1, zorder=1)

    ax1.errorbar(
        reffs, ys, yerr=[lo, hi], fmt="o-", color="#1f5aa6", linewidth=2.2, markersize=6.5,
        capsize=3.5, label="单 OQ (rx_desc=512, B=8)", zorder=3,
    )
    ax1.errorbar(
        [128], [m256], yerr=[[l256], [h256]], fmt="D", color="#138a56", markersize=7.5,
        capsize=3.5, label=f"最小硬件环 (rx_desc=256, 0.5 MiB): {m256:.1f}G", zorder=4,
    )

    ax1.set_xscale("log", base=2)
    ax1.set_xticks(reffs)
    ax1.set_xticklabels([str(r) for r in reffs])
    ax1.set_xlim(14, 290)
    ax1.set_ylim(0, 112)
    ax1.set_xlabel("每队列有效信用上限 R_eff（包槽数，每槽 2 KiB）", fontsize=10)
    ax1.set_ylabel("主机有效接收线速（Gbps，含 20 B 线开销）", fontsize=10)
    ax1.set_title("A. 左拐点：R_eff 低于 BDP 时卡侧信用饥饿掉速", fontsize=10.8, fontweight="bold", pad=8)

    ax1.annotate(
        f"R_eff=16 (32 KiB)\n{ys[0]:.1f}G · 停顿 {stalls[0]/1e3:.0f}k 次",
        xy=(16, ys[0]), xytext=(21, 14), fontsize=8.6, color="#a61c1c",
        arrowprops=dict(arrowstyle="->", color="#a61c1c", lw=1.0),
    )
    ax1.annotate(
        f"R_eff=64 (128 KiB)\n{ys[4]:.1f}G · 仍有 1.6k 停顿",
        xy=(64, ys[4]), xytext=(24, 84), fontsize=8.6, color="#a61c1c",
        arrowprops=dict(arrowstyle="->", color="#a61c1c", lw=1.0),
    )
    ax1.annotate(
        f"满速拐点 R_eff ≥ 96\n({ys[5]:.1f} Gbps，停顿趋零)",
        xy=(96, ys[5]), xytext=(108, 66), fontsize=9.0, fontweight="bold", color="#136f45",
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#136f45", alpha=0.92),
        arrowprops=dict(arrowstyle="->", color="#136f45", lw=1.2),
    )
    ax1.legend(loc="lower right", fontsize=8.6, framealpha=0.92)

    # ---------------- Panel B: Right working-set spill boundaries ----------------
    def collect_series(cfgs):
        xs, ys_tot, lo_t, hi_t, ddios, wrs = [], [], [], [], [], []
        for c in cfgs:
            rs = by_cfg[c]
            xs.append(statistics.median([x["rates"]["measured_iova_unique_mib"] for x in rs]))
            m, l, h = stats3(rs, lambda x: x["host_pmu"]["imc_rd_gbps"] + x["host_pmu"]["imc_wr_gbps"])
            ys_tot.append(m); lo_t.append(l); hi_t.append(h)
            ddios.append(statistics.median([x["host_pmu"]["ddio_hit_pct"] for x in rs]))
            wrs.append(statistics.median([x["host_pmu"]["imc_wr_gbps"] for x in rs]))
        return xs, ys_tot, lo_t, hi_t, ddios, wrs

    s1e_cfgs = [f"s1e_1500b_notouch_{t}_oq1024_r512" for t in ("4m", "6m", "8m", "10m", "12m", "16m", "32m")]
    x_e, y_e, lo_e, hi_e, dd_e, wr_e = collect_series(s1e_cfgs)

    s1bc_cfgs = [
        "s1b_1500b_oq512_r256_b8",
        "s1b_1500b_oq1024_r512_b8",
        "s1b_1500b_oq2048_r1024_b8",
        "s1c_1500b_fifo_6m_oq2048_r1024",
        "s1c_1500b_fifo_8m_oq2048_r1024",
        "s1c_1500b_fifo_10m_oq2048_r1024",
        "s1c_1500b_fifo_12m_oq2048_r1024",
        "s1b_1500b_oq8192_r4096_b8",
        "s1c_1500b_fifo_32m_oq8192_r4096",
        "s1c_1500b_fifo_64m_oq8192_r4096",
        "s1c_1500b_fifo_128m_oq8192_r4096",
    ]
    x_c, y_c, lo_c, hi_c, dd_c, _ = collect_series(s1bc_cfgs)

    s1d_cfgs = [
        "s1d_1500b_8q_oq256_4m",
        "s1d_1500b_8q_oq512_8m",
        "s1d_1500b_8q_oq1024_16m",
        "s1d_1500b_8q_oq2048_32m",
        "s1d_1500b_8q_oq4096_64m",
        "s1d_1500b_8q_oq8192_128m",
    ]
    x_d, y_d, lo_d, hi_d, dd_d, _ = collect_series(s1d_cfgs)
    x_p2, y_p2, _, _, dd_p2, _ = collect_series(["s1c_1500b_pitfall2_r256_pool128m"])

    ax2.axvspan(0.85, 10.0, color="#eaf7ee", alpha=0.60, zorder=0)
    ax2.axvline(12.0, color="#1f5aa6", linestyle=":", linewidth=1.3, zorder=1)
    ax2.axvline(60.0, color="#c0392b", linestyle="--", linewidth=1.4, zorder=1)

    ax2.errorbar(
        x_e, y_e, yerr=[lo_e, hi_e], fmt="^-", color="#1f5aa6", linewidth=2.0, markersize=6,
        capsize=3, label=f"1Q 仅读头尾 (touch=0): 12–16 MiB 起缓增 (32M 仍 {dd_e[-1]:.1f}%)", zorder=4,
    )
    ax2.errorbar(
        x_c, y_c, yerr=[lo_c, hi_c], fmt="o-", color="#d97706", linewidth=2.0, markersize=5.5,
        capsize=3, label="1Q 全包读 (touch=64): ≤32 MiB 零溢出，过 60 MiB 陡升", zorder=3,
    )
    ax2.errorbar(
        x_d, y_d, yerr=[lo_d, hi_d], fmt="s--", color="#15803d", linewidth=2.0, markersize=5.5,
        capsize=3, label="8Q 全包读 (8 核聚合 4→128 MiB)", zorder=3,
    )
    ax2.scatter(
        x_p2, y_p2, marker="*", s=190, color="#dc2626", edgecolor="black", linewidth=0.6,
        label=f"反例(坑点2): R_eff=256 但 128 MiB 大池 ({y_p2[0]:.1f}G)", zorder=5,
    )

    ax2.set_xscale("log", base=2)
    ax2.set_xticks([1, 2, 4, 8, 16, 32, 64, 128])
    ax2.set_xticklabels(["1", "2", "4", "8", "16", "32", "64", "128"])
    ax2.set_xlim(0.85, 165)
    ax2.set_ylim(-4, 206)
    ax2.set_xlabel("实测独立 mbuf DMA 工作集（MiB，IovaTracker 去重槽数 × 2 KiB）", fontsize=10)
    ax2.set_ylabel("主机 Socket-1 DRAM 总带宽（IMC 读+写，Gbps）", fontsize=10)
    ax2.set_title("B. 右边界：仅读头尾 12–16 MiB 缓增，真正陡崖在 ~60 MiB 全 LLC", fontsize=10.5, fontweight="bold", pad=8)

    ax2.text(12.8, 183, "touch=0 缓增起点\n~12–16 MiB", fontsize=8.1, color="#1f5aa6", fontweight="bold", ha="left")
    ax2.text(64.0, 183, "Socket-1 全 LLC\n60 MiB 规格", fontsize=8.2, color="#c0392b", fontweight="bold", ha="left")
    ax2.text(2.8, 160, "实测零溢写区\n(≤10 MiB, DDIO≥99.7%)", fontsize=8.4, color="#136f45", fontweight="bold", ha="center",
             bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#136f45", alpha=0.9))

    ax2.annotate(
        f"touch=0 12→16→32 MiB 缓增\nIMC写 {wr_e[4]:.1f}→{wr_e[5]:.1f}→{wr_e[6]:.1f}G (仍 {dd_e[-1]:.1f}% DDIO)",
        xy=(32, y_e[-1]), xytext=(5.5, 41), fontsize=8.0, color="#1f5aa6",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#1f5aa6", alpha=0.9),
        arrowprops=dict(arrowstyle="->", color="#1f5aa6", lw=1.0),
    )
    ax2.annotate(
        f"8Q 128 MiB\n{y_d[-1]:.1f}G (DDIO {dd_d[-1]:.1f}%)",
        xy=(128, y_d[-1]), xytext=(16.5, 146), fontsize=8.5, fontweight="bold", color="#15803d",
        bbox=dict(boxstyle="round,pad=0.24", fc="white", ec="#15803d", alpha=0.92),
        arrowprops=dict(arrowstyle="->", color="#15803d", lw=1.1),
    )
    ax2.annotate(
        f"1Q / 坑点2 (128 MiB)\n{y_c[-1]:.1f}G / {y_p2[0]:.1f}G\n(DDIO {dd_c[-1]:.1f}% / {dd_p2[0]:.1f}%)",
        xy=(128, y_p2[0]), xytext=(14.0, 88), fontsize=8.2, color="#b91c1c",
        bbox=dict(boxstyle="round,pad=0.25", fc="#fff5f5", ec="#dc2626", alpha=0.92),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.0),
    )
    ax2.legend(loc="upper left", bbox_to_anchor=(0.01, 0.72), fontsize=7.6, framealpha=0.92)

    fig.tight_layout(rect=[0, 0, 1, 0.91])
    out = mrvplot.save(fig, "mrv-214-reff-workingset")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
