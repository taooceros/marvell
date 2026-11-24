#!/usr/bin/env python3
"""MRV-161 Figure 4: Bare probe + exploratory --cqe-direct (zero-LDADDA) decomposition.

Clearly separates the exploratory --cqe-direct arm from the primary 3-arm comparison.
Reads all values directly from logs/mrv161-2026-11-24/runs.jsonl.
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv161-2026-11-24/runs.jsonl"


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

    fig, axes = plt.subplots(2, 2, figsize=(14.6, 9.4))
    fig.suptitle(
        "MRV-161 硬件探针与探索臂 --cqe-direct（零 LDADDA）两段拆解\n"
        "双机 100GbE · CN106 1C 2.0 GHz (scalar_enable=1) · 全零错（n=3 中位数，竖线为 min–max；--cqe-direct 为独立探索对照臂）",
        fontsize=12.4,
        fontweight="bold",
        y=0.985,
    )

    # ------------------------------------------------------------------
    # Panel A: Bare microbenchmark (log x-axis)
    # ------------------------------------------------------------------
    ax = axes[0, 0]
    probe_items = [
        ("默认 eth0 空轮询\n(LDADDA)", "cqe_probe_bare", lambda r: r["probe"]["eth_rx_empty_bare_ns"], "#64748b"),
        ("活包命中按需 LDADDA\n(hw_gate 15k 均值)", "hw_gate_cqe_peek_diag_64_w1", lambda r: r["card"]["cqe_peek"]["ldadda_mean_ns"], "#94a3b8"),
        ("rdtsc_after_loads\n(CNTVCT 读时钟)", "cqe_probe_bare", lambda r: r["probe"]["rdtsc_after_loads_bare_ns"], "#cbd5e1"),
        ("NPA alloc+free\n(片内硬件内存池)", "cqe_probe_bare", lambda r: r["probe"]["mbuf_alloc_free_bare_ns"], "#cbd5e1"),
        ("--cqe-direct 空轮询\n(ldar，探索)", "cqe_probe_bare", lambda r: r["probe"]["cqe_direct_empty_bare_ns"], "#0f766e"),
        ("--cqe-peek 空轮询\n(ldar，主方案)", "cqe_probe_bare", lambda r: r["probe"]["cqe_peek_empty_bare_ns"], "#1d6fa5"),
        ("BAR4 本地空轮询\n(ldar)", "cqe_probe_bare", lambda r: r["probe"]["bar4_poll_empty_bare_ns"], "#94a3b8"),
    ]
    y_pos = np.arange(len(probe_items))[::-1]
    meds_a = []
    for yi, (lbl, cfg, getter, col) in zip(y_pos, probe_items):
        m, dlo, dhi, _ = stats(rows, cfg, getter)
        meds_a.append(m)
        ax.barh(yi, m, height=0.62, color=col, alpha=0.92, zorder=3)
        ax.errorbar(m, yi, xerr=[[dlo], [dhi]], fmt="none", ecolor="#0f172a", elinewidth=1.0, capsize=2.5, zorder=4)
        extra = ""
        if "cqe-peek" in lbl:
            extra = f"（快 {meds_a[0] / m:.0f}×）"
        elif "cqe-direct" in lbl:
            extra = f"（快 {meds_a[0] / m:.0f}×）"
        ax.text(
            m * 1.15,
            yi,
            f"{m:.2f} ns{extra}",
            va="center",
            ha="left",
            fontsize=8.8,
            fontweight="bold" if ("cqe-peek" in lbl or "cqe-direct" in lbl or "默认" in lbl) else "normal",
            color=col if col in ("#1d6fa5", "#0f766e") else "#1e293b",
        )
    ax.set_xscale("log")
    ax.set_xlim(0.3, 4800)
    ax.set_yticks(y_pos)
    ax.set_yticklabels([p[0] for p in probe_items], fontsize=8.8)
    ax.set_xlabel("单次操作耗时（ns，对数刻度）", fontsize=9.8)
    ax.set_title("A. 片内裸空轮询与关键原语耗时（10,000 次）", fontsize=11, fontweight="bold", pad=8)

    # ------------------------------------------------------------------
    # Panel B: 64 B W=1 two-stage LDADDA latency savings (card_echo vs DirectPush)
    # ------------------------------------------------------------------
    ax = axes[0, 1]
    groups_b = [
        ("card_echo 64B W=1\n（纯网卡回环）", "card_echo_{pol}_64_w1"),
        ("DirectPush 64B W=1\n（双机含 saturn 主机）", "direct_{pol}_64_w1"),
    ]
    pols_b = [
        ("default", "default（原装 LDADDA）", "#64748b"),
        ("cqe_peek", "--cqe-peek（主方案）", "#1d6fa5"),
        ("cqe_direct", "--cqe-direct（探索：零 LDADDA）", "#0f766e"),
    ]
    x_b = np.arange(len(groups_b))
    w_b = 0.24
    vals_b = {}
    for idx, (pol, label, color) in enumerate(pols_b):
        meds, lo, hi = [], [], []
        for g_idx, (_, tmpl) in enumerate(groups_b):
            cfg = tmpl.format(pol=pol)
            m, dlo, dhi, _ = stats(rows, cfg, lambda r: r["client"]["tot_p50_us"])
            meds.append(m)
            lo.append(dlo)
            hi.append(dhi)
            vals_b[(g_idx, pol)] = m
        pos = x_b + (idx - 1) * w_b
        ax.bar(pos, meds, width=w_b * 0.90, color=color, alpha=0.92, label=label, zorder=3)
        ax.errorbar(pos, meds, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.1, capsize=2.5, zorder=4)
        for p, m, dhi in zip(pos, meds, hi):
            ax.text(
                p,
                m + dhi + 0.11,
                f"{m:.3f}",
                ha="center",
                va="bottom",
                fontsize=8.6,
                fontweight="bold" if pol != "default" else "normal",
                color=color if pol != "default" else "#334155",
            )

    d_echo = (vals_b[(0, "cqe_direct")] - vals_b[(0, "cqe_peek")]) * 1000
    d_dp_p1 = (vals_b[(1, "cqe_peek")] - vals_b[(1, "default")]) * 1000
    d_dp_p2 = (vals_b[(1, "cqe_direct")] - vals_b[(1, "cqe_peek")]) * 1000
    d_dp_tot = (vals_b[(1, "cqe_direct")] - vals_b[(1, "default")]) * 1000

    ax.annotate(
        f"省去命中 LDADDA\n{d_echo:+.0f} ns vs peek",
        xy=(0.24, vals_b[(0, "cqe_direct")] + 0.45),
        xytext=(-0.05, 5.20),
        fontsize=8.4,
        fontweight="bold",
        color="#0f766e",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#f0fdfa", edgecolor="#0f766e", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#0f766e", lw=1.2),
        zorder=6,
    )
    ax.annotate(
        f"两段累加 {d_dp_tot:+.0f} ns\n① 免空轮询 {d_dp_p1:+.0f} ns\n② 免命中 LDADDA {d_dp_p2:+.0f} ns",
        xy=(1.24, vals_b[(1, "cqe_direct")] + 0.48),
        xytext=(0.74, 6.50),
        fontsize=8.3,
        fontweight="bold",
        color="#0f766e",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#f0fdfa", edgecolor="#0f766e", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#0f766e", lw=1.2),
        zorder=6,
    )

    ax.set_xticks(x_b)
    ax.set_xticklabels([g[0] for g in groups_b], fontsize=9.8)
    ax.set_ylabel("mercury 端到端 p50 (µs)", fontsize=9.8)
    ax.set_ylim(0, 8.2)
    ax.set_title(f"B. 64 B W=1 两段 LDADDA 延迟拆解（{vals_b[(1, 'cqe_direct')]:.3f} µs）", fontsize=11, fontweight="bold", pad=8)
    ax.legend(loc="upper left", frameon=True, facecolor="white", framealpha=0.94, fontsize=8.3)

    # ------------------------------------------------------------------
    # Panel C: 64 B closed-loop throughput W = 4, 8, 32
    # ------------------------------------------------------------------
    ax = axes[1, 0]
    ws_c = [4, 8, 32]
    x_c = np.arange(len(ws_c))
    base_c = [stats(rows, f"direct_default_64_w{w}", lambda r: r["client"]["achieved_mrps"])[0] for w in ws_c]

    for idx, (pol, label, color) in enumerate(pols_b):
        meds, lo, hi = [], [], []
        for w in ws_c:
            m, dlo, dhi, _ = stats(rows, f"direct_{pol}_64_w{w}", lambda r: r["client"]["achieved_mrps"])
            meds.append(m)
            lo.append(dlo)
            hi.append(dhi)
        pos = x_c + (idx - 1) * w_b
        ax.bar(pos, meds, width=w_b * 0.90, color=color, alpha=0.92, label=label, zorder=3)
        ax.errorbar(pos, meds, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.1, capsize=2.5, zorder=4)
        for p, m, dhi, b0 in zip(pos, meds, hi, base_c):
            txt = f"{m:.3f}"
            if pol == "cqe_direct":
                pct = (m / b0 - 1.0) * 100.0
                txt = f"{m:.3f}\n({pct:+.1f}%)"
            ax.text(
                p,
                m + dhi + 0.06,
                txt,
                ha="center",
                va="bottom",
                fontsize=8.1,
                fontweight="bold" if pol == "cqe_direct" else "normal",
                color=color if pol != "default" else "#334155",
            )

    ax.set_xticks(x_c)
    ax.set_xticklabels([f"64B W={w}" for w in ws_c], fontsize=10)
    ax.set_ylabel("闭环吞吐 (Mreq/s)", fontsize=9.8)
    ax.set_ylim(0, 3.95)
    ax.set_title("C. 64 B 中高并发吞吐：消除命中 LDADDA 阻塞 BAR4", fontsize=11, fontweight="bold", pad=8)
    ax.legend(loc="upper left", frameon=True, facecolor="white", framealpha=0.94, fontsize=8.5)

    # ------------------------------------------------------------------
    # Panel D: 64 B open-loop Poisson mercury p50 (0.5 / 1.0 / 1.5 M/s)
    # ------------------------------------------------------------------
    ax = axes[1, 1]
    rates_d = [(500, "0.5 M/s"), (1000, "1.0 M/s"), (1500, "1.5 M/s")]
    x_d = np.arange(len(rates_d))
    for idx, (pol, label, color) in enumerate(pols_b):
        meds, lo, hi = [], [], []
        for rk, _ in rates_d:
            m, dlo, dhi, _ = stats(rows, f"direct_{pol}_64_p{rk}k", lambda r: r["client"]["tot_p50_us"])
            meds.append(m)
            lo.append(dlo)
            hi.append(dhi)
        pos = x_d + (idx - 1) * w_b
        ax.bar(pos, meds, width=w_b * 0.90, color=color, alpha=0.92, label=label, zorder=3)
        ax.errorbar(pos, meds, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.1, capsize=2.5, zorder=4)
        for p, m, dhi in zip(pos, meds, hi):
            ax.text(
                p,
                m + dhi + 0.12,
                f"{m:.3f}",
                ha="center",
                va="bottom",
                fontsize=8.2,
                fontweight="bold" if pol != "default" else "normal",
                color=color if pol != "default" else "#334155",
            )

    m_def_15 = stats(rows, "direct_default_64_p1500k", lambda r: r["client"]["tot_p50_us"])[0]
    m_dir_05 = stats(rows, "direct_cqe_direct_64_p500k", lambda r: r["client"]["tot_p50_us"])[0]
    m_dir_15 = stats(rows, "direct_cqe_direct_64_p1500k", lambda r: r["client"]["tot_p50_us"])[0]
    dir_15_p99_max = max(stats(rows, "direct_cqe_direct_64_p1500k", lambda r: r["client"]["tot_p99_us"])[3])
    ax.annotate(
        f"1.5 M/s 比 default 低 {m_def_15 - m_dir_15:.2f} µs\n"
        f"（注：rep 1 单次端到端离群点 p99={dir_15_p99_max:.1f} µs，\n  原因未定，卡内 p99 正常）",
        xy=(2.24, m_dir_15 + 0.68),
        xytext=(2.46, 7.85),
        ha="right",
        fontsize=8.1,
        color="#0f766e",
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#f0fdfa", edgecolor="#0f766e", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#0f766e", lw=1.2, connectionstyle="arc3,rad=-0.12"),
        zorder=6,
    )
    ax.set_xticks(x_d)
    ax.set_xticklabels([r[1] for r in rates_d], fontsize=10)
    ax.set_xlabel("开环泊松目标到达率 λ", fontsize=9.8)
    ax.set_ylabel("mercury 端到端 p50 (µs)", fontsize=9.8)
    ax.set_ylim(0, 9.7)
    ax.set_title(f"D. 64 B 开环泊松中位延迟（压至 {m_dir_05:.3f}–{m_dir_15:.3f} µs）", fontsize=11, fontweight="bold", pad=8)

    out = mrvplot.save(fig, "mrv-161-cqe-direct-decomp")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
