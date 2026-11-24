#!/usr/bin/env python3
"""MRV-161 Figure 2: Primary 3-arm closed-loop comparison (default vs --bar4-first vs --cqe-peek).

Reads raw verified runs directly from logs/mrv161-2026-11-24/runs.jsonl.
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot  # noqa: F401 - applies shared style

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv161-2026-11-24/runs.jsonl"

ARMS = [
    ("default", "default（原装 LDADDA）", "#64748b"),
    ("bar4_first", "--bar4-first（盲窗暂缓）", "#d97706"),
    ("cqe_peek", "--cqe-peek（缓存窥探）", "#1d6fa5"),
]


def load_runs():
    rows = []
    for line in RUNS.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r.get("verified") is True, f"unverified run: {r.get('cfg')}"
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
        "MRV-161 主单变量闭环对照：default vs --bar4-first vs --cqe-peek（64 B / 256 B）\n"
        "双机 100GbE DirectPush · CN106 1C 2.0 GHz (scalar_enable=1) ⇄ saturn cpu40 3.9 GHz · 直推 batch=1 · n=3 中位数（竖线为 min–max）",
        fontsize=12.4,
        fontweight="bold",
        y=0.985,
    )

    # ------------------------------------------------------------------
    # Panel A: 64 B card CNTVCT p50 (W = 1, 2, 4, 8)
    # ------------------------------------------------------------------
    ax = axes[0, 0]
    ws_a = [1, 2, 4, 8]
    x_a = np.arange(len(ws_a))
    width = 0.255
    for idx, (pol, label, color) in enumerate(ARMS):
        meds, lo, hi = [], [], []
        for w in ws_a:
            m, dlo, dhi, _ = stats(rows, f"direct_{pol}_64_w{w}", lambda r: r["card"]["host_rtt_p50_us"])
            meds.append(m)
            lo.append(dlo)
            hi.append(dhi)
        pos = x_a + (idx - 1) * width
        bars = ax.bar(pos, meds, width=width * 0.90, color=color, alpha=0.9, label=label, zorder=3)
        ax.errorbar(pos, meds, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.1, capsize=2.5, zorder=4)
        for p, m, dhi in zip(pos, meds, hi):
            ax.text(
                p,
                m + dhi + 0.025,
                f"{m:.3f}",
                ha="center",
                va="bottom",
                fontsize=7.7,
                fontweight="bold" if pol == "cqe_peek" else "normal",
                color=color if pol == "cqe_peek" else "#334155",
            )

    ax.axhline(0.95, color="#dc2626", linestyle=":", linewidth=1.6, label="判定线 0.950 µs", zorder=2)
    ax.set_xticks(x_a)
    ax.set_xticklabels([f"W={w}" for w in ws_a], fontsize=10)
    ax.set_ylabel("卡内 PCIe 往返 CNTVCT p50 (µs)", fontsize=10)
    ax.set_ylim(0, 1.82)
    w1_pk_card = stats(rows, "direct_cqe_peek_64_w1", lambda r: r["card"]["host_rtt_p50_us"])[0]
    ax.set_title(f"A. 64 B 卡内 PCIe 往返（W=1 达标 {w1_pk_card:.3f} ≤ 0.950 µs）", fontsize=11, fontweight="bold", pad=8)
    ax.legend(loc="upper left", ncol=2, frameon=True, facecolor="white", framealpha=0.94, fontsize=8.3)

    # ------------------------------------------------------------------
    # Panel B: 64 B end-to-end mercury p99 (W = 1, 2, 4, 8) + p50 ticks
    # ------------------------------------------------------------------
    ax = axes[0, 1]
    ws_b = [1, 2, 4, 8]
    x_b = np.arange(len(ws_b))
    for idx, (pol, label, color) in enumerate(ARMS):
        p99s, lo, hi, p50s = [], [], [], []
        for w in ws_b:
            m99, dlo, dhi, _ = stats(rows, f"direct_{pol}_64_w{w}", lambda r: r["client"]["tot_p99_us"])
            m50, _, _, _ = stats(rows, f"direct_{pol}_64_w{w}", lambda r: r["client"]["tot_p50_us"])
            p99s.append(m99)
            lo.append(dlo)
            hi.append(dhi)
            p50s.append(m50)
        pos = x_b + (idx - 1) * width
        ax.bar(pos, p99s, width=width * 0.90, color=color, alpha=0.88, label=label, zorder=3)
        ax.errorbar(pos, p99s, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.1, capsize=2.5, zorder=4)
        for p, m50, m99, dhi in zip(pos, p50s, p99s, hi):
            ax.plot([p - width * 0.34, p + width * 0.34], [m50, m50], color="white", linewidth=2.2, zorder=5)
            ax.text(
                p,
                m99 + dhi + 0.14,
                f"{m99:.3f}",
                ha="center",
                va="bottom",
                fontsize=7.6,
                fontweight="bold" if (pol == "cqe_peek" or (pol == "bar4_first" and m99 > 7.9)) else "normal",
                color=color if pol != "default" else "#334155",
            )

    w2_b4_99_vals = stats(rows, "direct_bar4_first_64_w2", lambda r: r["client"]["tot_p99_us"])[3]
    w2_pk_99_vals = stats(rows, "direct_cqe_peek_64_w2", lambda r: r["client"]["tot_p99_us"])[3]
    w2_d99 = [p - b for p, b in zip(w2_pk_99_vals, w2_b4_99_vals)]
    w2_b4_99 = stats(rows, "direct_bar4_first_64_w2", lambda r: r["client"]["tot_p99_us"])[0]
    w4_def_99 = stats(rows, "direct_default_64_w4", lambda r: r["client"]["tot_p99_us"])[0]
    w4_b4_99 = stats(rows, "direct_bar4_first_64_w4", lambda r: r["client"]["tot_p99_us"])[0]
    w4_pk_99 = stats(rows, "direct_cqe_peek_64_w4", lambda r: r["client"]["tot_p99_us"])[0]
    ax.annotate(
        f"W=2 双包反相未撞窗\n--bar4-first p99 占优 {min(w2_d99):.2f}–{max(w2_d99):.2f} µs",
        xy=(1.0, w2_b4_99 + 0.56),
        xytext=(-0.38, 8.90),
        fontsize=8.1,
        fontweight="bold",
        color="#475569",
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#f8fafc", edgecolor="#94a3b8", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#64748b", lw=1.2),
        zorder=6,
    )
    ax.annotate(
        f"盲窗退化自 W=4 爆发\nW=4 p99 +{w4_b4_99 - w4_def_99:.2f} µs vs default\n--cqe-peek 省 {w4_b4_99 - w4_pk_99:.2f} µs",
        xy=(2.0, w4_b4_99 + 0.52),
        xytext=(1.68, 9.75),
        fontsize=8.3,
        fontweight="bold",
        color="#92400e",
        bbox=dict(boxstyle="round,pad=0.33", facecolor="#fffbeb", edgecolor="#d97706", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#d97706", lw=1.3),
        zorder=6,
    )
    ax.set_xticks(x_b)
    ax.set_xticklabels([f"W={w}" for w in ws_b], fontsize=10)
    ax.set_ylabel("端到端 mercury 延迟 (µs)（柱顶=p99，白横线=p50）", fontsize=9.8)
    ax.set_ylim(0, 12.1)
    ax.set_title("B. 64 B 端到端 p99 与 p50（W=2 反相未撞窗，W=4/8 根除盲窗飙升）", fontsize=10.6, fontweight="bold", pad=8)

    # ------------------------------------------------------------------
    # Panel C: 256 B control (W = 1, 4) end-to-end p50 and p99
    # ------------------------------------------------------------------
    ax = axes[1, 0]
    cats_c = [
        ("256B W=1\np50", 1, "tot_p50_us"),
        ("256B W=1\np99", 1, "tot_p99_us"),
        ("256B W=4\np50", 4, "tot_p50_us"),
        ("256B W=4\np99", 4, "tot_p99_us"),
    ]
    x_c = np.arange(len(cats_c))
    for idx, (pol, label, color) in enumerate(ARMS):
        meds, lo, hi = [], [], []
        for _, w, fld in cats_c:
            m, dlo, dhi, _ = stats(rows, f"direct_{pol}_256_w{w}", lambda r, f=fld: r["client"][f])
            meds.append(m)
            lo.append(dlo)
            hi.append(dhi)
        pos = x_c + (idx - 1) * width
        ax.bar(pos, meds, width=width * 0.90, color=color, alpha=0.9, label=label, zorder=3)
        ax.errorbar(pos, meds, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.1, capsize=2.5, zorder=4)
        for p, m, dhi in zip(pos, meds, hi):
            ax.text(
                p,
                m + dhi + 0.14,
                f"{m:.3f}",
                ha="center",
                va="bottom",
                fontsize=7.6,
                fontweight="bold" if pol == "cqe_peek" else "normal",
                color=color if pol != "default" else "#334155",
            )

    w4_256_def_99 = stats(rows, "direct_default_256_w4", lambda r: r["client"]["tot_p99_us"])[0]
    w4_256_b4_99 = stats(rows, "direct_bar4_first_256_w4", lambda r: r["client"]["tot_p99_us"])[0]
    w4_256_pk_99 = stats(rows, "direct_cqe_peek_256_w4", lambda r: r["client"]["tot_p99_us"])[0]
    ax.annotate(
        f"256B W=4 盲窗再现\n+{w4_256_b4_99 - w4_256_def_99:.2f} µs vs default\n--cqe-peek 省 {w4_256_b4_99 - w4_256_pk_99:.2f} µs",
        xy=(3.0, w4_256_b4_99 + 0.72),
        xytext=(0.95, 9.85),
        fontsize=8.6,
        fontweight="bold",
        color="#1d6fa5",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#f0f7fc", edgecolor="#1d6fa5", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#1d6fa5", lw=1.3),
        zorder=6,
    )
    ax.set_xticks(x_c)
    ax.set_xticklabels([c[0] for c in cats_c], fontsize=9.8)
    ax.set_ylabel("端到端 mercury 延迟 (µs)", fontsize=10)
    ax.set_ylim(0, 11.9)
    ax.set_title("C. 256 B 对照：端到端 p50 / p99（盲窗规律跨包长复现）", fontsize=11, fontweight="bold", pad=8)

    # ------------------------------------------------------------------
    # Panel D: Throughput delta vs default (%) across ALL closed-loop points
    # ------------------------------------------------------------------
    ax = axes[1, 1]
    pts_d = [
        ("64B\nW=1", 64, 1),
        ("64B\nW=2", 64, 2),
        ("64B\nW=4", 64, 4),
        ("64B\nW=8", 64, 8),
        ("64B\nW=32", 64, 32),
        ("256B\nW=1", 256, 1),
        ("256B\nW=4", 256, 4),
        ("256B\nW=32", 256, 32),
    ]
    x_d = np.arange(len(pts_d))
    w_d = 0.35
    base_rates = [stats(rows, f"direct_default_{sz}_w{w}", lambda r: r["client"]["achieved_mrps"])[0] for _, sz, w in pts_d]

    for idx, (pol, label, color) in enumerate([
        ("bar4_first", "--bar4-first vs default", "#d97706"),
        ("cqe_peek", "--cqe-peek vs default", "#1d6fa5"),
    ]):
        pcts, lo, hi = [], [], []
        for (_, sz, w), br in zip(pts_d, base_rates):
            m, _, _, vals = stats(rows, f"direct_{pol}_{sz}_w{w}", lambda r: r["client"]["achieved_mrps"])
            pvals = [(v / br - 1.0) * 100.0 for v in vals]
            pm = (m / br - 1.0) * 100.0
            pcts.append(pm)
            lo.append(pm - min(pvals))
            hi.append(max(pvals) - pm)
        pos = x_d + (idx - 0.5) * w_d
        ax.bar(pos, pcts, width=w_d * 0.90, color=color, alpha=0.9, label=label, zorder=3)
        ax.errorbar(pos, pcts, yerr=[lo, hi], fmt="none", ecolor="#0f172a", elinewidth=1.0, capsize=2.0, zorder=4)
        for p_i, (p, pm, dlo, dhi) in enumerate(zip(pos, pcts, lo, hi)):
            if pm >= 0:
                y_txt = pm + dhi + 0.35
                va = "bottom"
            else:
                y_txt = pm - dlo - 0.35
                va = "top"
            # Slight horizontal nudge on crowded last pair (256B W=32)
            x_txt = p
            if p_i == 7:
                x_txt = p - 0.08 if idx == 0 else p + 0.08
            elif p_i == 4:
                x_txt = p - 0.06 if idx == 0 else p + 0.06
            ax.text(
                x_txt,
                y_txt,
                f"{pm:+.1f}%",
                ha="center",
                va=va,
                fontsize=7.6,
                fontweight="bold" if (pol == "cqe_peek" or pm < -2.5) else "normal",
                color=color,
            )

    ax.axhline(0.0, color="#334155", linewidth=1.1, zorder=2)
    ax.axvline(4.5, color="#cbd5e1", linestyle="--", linewidth=1.1, zorder=1)
    ax.set_xticks(x_d)
    ax.set_xticklabels([f"{p[0]}\n({br:.3f})" for p, br in zip(pts_d, base_rates)], fontsize=8.3)
    ax.set_xlabel("负载点（括号内为 default 基线吞吐，单位 Mreq/s）", fontsize=9.2)
    ax.set_ylabel("闭环吞吐相对 default 变化 (%)", fontsize=10)
    ax.set_ylim(-8.5, 12.2)
    ax.set_title("D. 全并发闭环吞吐变化（--cqe-peek 全程零盲窗退化）", fontsize=11, fontweight="bold", pad=8)
    ax.legend(loc="upper right", frameon=True, facecolor="white", framealpha=0.94, fontsize=8.8)

    out = mrvplot.save(fig, "mrv-161-closed-loop")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
