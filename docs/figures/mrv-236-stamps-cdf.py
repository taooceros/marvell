#!/usr/bin/env python3
"""MRV-236 Figure 4: Per-cell held fraction f, negative-gap root cause (host CHA overtaking vs CIVAC lag),
and raw vs physical-arrival-order landing-interval CDF.

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


def analyze_arm_stamps(recs: list[dict]) -> dict:
    held_g: list[float] = []
    unheld_g: list[float] = []
    all_g: list[float] = []
    phys_inner_g: list[float] = []
    cross64_rep_meds: list[float] = []

    neg_all_rep_pcts: list[float] = []
    hh_ge40_neg = 0
    hh_ge40_tot = 0
    all_ge40_neg = 0
    all_ge40_tot = 0

    for r in recs:
        card_hz = float(r["card_pmu"]["tsc_hz"])
        ns_c = 1e9 / card_hz
        host_hz = 2.2e9

        with open_stamp_csv(r["stamp_csv"]) as f:
            hdr = f.readline().strip()
            for tok in hdr.split():
                if tok.startswith("tsc_hz="):
                    host_hz = float(tok.split("=")[1])
            ns_h = 1e9 / host_hz

            by_seq: dict[int, tuple[float, int, float]] = {}
            windows: list[list[float]] = []
            cur_win: list[float] = []
            prev_seq: int | None = None

            for row in csv.DictReader(f):
                seq = int(row["seq"])
                cin = float(row["card_t_in"])
                cwp = int(row["card_waited_polls"])
                stsc = float(row["send_tsc"])
                by_seq[seq] = (cin, cwp, stsc)

                if prev_seq is not None and seq == prev_seq + 1:
                    cur_win.append(cin)
                else:
                    if cur_win:
                        windows.append(cur_win)
                    cur_win = [cin]
                prev_seq = seq
            if cur_win:
                windows.append(cur_win)

        c64_rep: list[float] = []
        rep_neg = 0
        rep_tot = 0
        for seq, (cin, cwp, stsc) in by_seq.items():
            if (seq - 64) in by_seq:
                c64_rep.append((cin - by_seq[seq - 64][0]) * ns_c / 64.0)
            if (seq - 1) in by_seq:
                pcin, pcwp, pstsc = by_seq[seq - 1]
                gap = (cin - pcin) * ns_c
                dsend = (stsc - pstsc) * ns_h
                all_g.append(gap)
                if cwp > 0:
                    held_g.append(gap)
                else:
                    unheld_g.append(gap)
                rep_tot += 1
                if gap < 0:
                    rep_neg += 1
                if dsend >= 40.0:
                    all_ge40_tot += 1
                    if gap < 0:
                        all_ge40_neg += 1
                    if pcwp > 0 and cwp > 0:
                        hh_ge40_tot += 1
                        if gap < 0:
                            hh_ge40_neg += 1

        if c64_rep:
            cross64_rep_meds.append(statistics.median(c64_rep))
        if rep_tot:
            neg_all_rep_pcts.append(100.0 * rep_neg / rep_tot)

        # Restore physical arrival order within each 8-cell sparse window; use interior gaps
        # to eliminate boundary swaps with cells outside the sampled window.
        for w in windows:
            if len(w) >= 4:
                sw = sorted(w)
                for a, b in zip(sw[1:-1], sw[2:-1]):
                    phys_inner_g.append((b - a) * ns_c)

    return {
        "held_g": held_g,
        "unheld_g": unheld_g,
        "all_g": all_g,
        "phys_inner_g": phys_inner_g,
        "cross64_med": statistics.median(cross64_rep_meds) if cross64_rep_meds else float("nan"),
        "neg_all_med": statistics.median(neg_all_rep_pcts) if neg_all_rep_pcts else 0.0,
        "all_ge40_pct": 100.0 * all_ge40_neg / all_ge40_tot if all_ge40_tot else 0.0,
        "hh_ge40_pct": 100.0 * hh_ge40_neg / hh_ge40_tot if hh_ge40_tot else 0.0,
        "hh_ge40_neg": hh_ge40_neg,
        "hh_ge40_tot": hh_ge40_tot,
    }


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
    arm_data = {a: analyze_arm_stamps(by_arm[a]) for a in bar_arms}

    bar_labels = {
        "B1_sp": "B1_sp\nWC1快态",
        "S2_sp": "S2_sp\nWC1+影\n(仅环核)",
        "B": "B\nWC2基线",
        "A1": "A1\n去next",
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

    fig, axes = plt.subplots(2, 2, figsize=(13.6, 8.8))
    ax1, ax2, ax3, ax4 = axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]

    # (a) Held fraction f
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
            max(fs) + 2.4,
            f"{f_meds[i]:.1f}%\n({mops_m:.1f}M)",
            ha="center",
            va="bottom",
            fontsize=7.7,
            color="#111827",
            fontweight="bold",
        )
    ax1.set_xticks(x)
    ax1.set_xticklabels([bar_labels[a] for a in bar_arms], fontsize=7.9)
    ax1.set_ylabel("环核空轮询持有比例 f (%)", fontsize=9.6)
    ax1.set_ylim(0, 120)
    ax1.set_title("(a) 逐格持有比例 f = P(card_waited_polls > 0)", fontsize=10.2, pad=7)

    # (b) Negative-gap root cause: all pairs vs Δsend >= 40 ns (all / held->held)
    neg_arms = ["B", "A1", "A2", "A3", "WC4_B", "A4"]
    neg_labels = ["B\nWC2", "A1\n去next", "A2\nldr", "A3\nwfe", "WC4_B\n4核", "A4\ncivac"]
    xn = np.arange(len(neg_arms))
    w = 0.27
    v_all = [arm_data[a]["neg_all_med"] for a in neg_arms]
    v_ge40 = [arm_data[a]["all_ge40_pct"] for a in neg_arms]
    v_hh40 = [arm_data[a]["hh_ge40_pct"] for a in neg_arms]

    ax2.bar(xn - w, v_all, width=w, color="#4B5563", edgecolor="#111827", linewidth=0.7, label="全部相邻格 (含同批 Δsend < 25 ns)", zorder=3)
    ax2.bar(xn, v_ge40, width=w, color="#F59E0B", edgecolor="#92400E", linewidth=0.7, label="分批 Δsend ≥ 40 ns (全部格)", zorder=3)
    ax2.bar(xn + w, v_hh40, width=w, color="#DC2626", edgecolor="#7F1D1D", linewidth=0.7, label="分批 Δsend ≥ 40 ns (held→held)", zorder=3)
    for i, a in enumerate(neg_arms):
        ax2.text(i - w, v_all[i] + 0.6, f"{v_all[i]:.1f}%", ha="center", va="bottom", fontsize=7.3, color="#1F2937", fontweight="bold")
        dx_o = 0.02 if a == "WC4_B" else 0.0
        dx_r = 0.12 if a == "WC4_B" else 0.03
        ax2.text(i + dx_o, v_ge40[i] + 0.6, f"{v_ge40[i]:.1f}%", ha="center", va="bottom", fontsize=6.7, color="#92400E", fontweight="bold")
        ax2.text(i + w + dx_r, v_hh40[i] + 0.6, f"{v_hh40[i]:.1f}%", ha="center", va="bottom", fontsize=6.7, color="#991B1B", fontweight="bold")

    ax2.annotate(
        "主机 <25 ns 跨 32×CHA 超车：\nB/A1/WC4 ≥40 ns 时 held→held 严格 0.0%",
        xy=(1.28, 2.5),
        xytext=(0.08, 22.2),
        fontsize=7.7,
        color="#1F2937",
        bbox={"boxstyle": "round,pad=0.3", "fc": "#F9FAFB", "ec": "#D1D5DB"},
        arrowprops={"arrowstyle": "->", "color": "#4B5563", "lw": 0.9},
    )
    ax2.annotate(
        "dc civac+dsb ish\n观察滞后 (≥40 ns 仍 24.5%)",
        xy=(5.04, v_ge40[5] + 2.3),
        xytext=(2.68, 32.2),
        fontsize=7.5,
        color="#991B1B",
        fontweight="bold",
        bbox={"boxstyle": "round,pad=0.25", "fc": "#FEF2F2", "ec": "#FCA5A5"},
        arrowprops={"arrowstyle": "->", "color": "#DC2626", "lw": 1.0, "connectionstyle": "arc3,rad=-0.26"},
    )
    ax2.set_xticks(xn)
    ax2.set_xticklabels(neg_labels, fontsize=8.2)
    ax2.set_ylabel("相邻负差占比 Δcard_t_in < 0 (%)", fontsize=9.6)
    ax2.set_ylim(0, 38.8)
    ax2.set_title("(b) 负差归因：~14% 源于主机同批跨 CHA 超车，A4 叠 CIVAC 滞后", fontsize=10.2, pad=7)
    ax2.legend(loc="upper left", fontsize=7.3, frameon=True, facecolor="white", framealpha=0.94)

    def plot_cdf(ax, vals: list[float], label: str, color: str, ls: str = "-", lw: float = 1.9):
        if not vals:
            return
        arr = np.sort(np.asarray(vals, dtype=float))
        y = np.linspace(0.0, 1.0, len(arr))
        p50 = float(np.median(arr))
        ax.plot(arr, y, label=f"{label} (p50={p50:.0f} ns)", color=color, linestyle=ls, linewidth=lw)

    # (c) Raw seq-order Δcard_t_in CDF
    b1_c64 = arm_data["B1_sp"]["cross64_med"]
    s2_c64 = arm_data["S2_sp"]["cross64_med"]
    plot_cdf(ax3, arm_data["B1_sp"]["unheld_g"], f"B1_sp 未空轮询 (跨64均{b1_c64:.1f}ns)", "#059669", ls="-", lw=2.2)
    plot_cdf(ax3, arm_data["S2_sp"]["all_g"], f"S2_sp 影子守front (跨64均{s2_c64:.1f}ns)", "#F97316", ls="-", lw=1.9)
    plot_cdf(ax3, arm_data["B"]["held_g"], "B (WC2) cw>0 按seq", "#1D4ED8", ls="-", lw=2.0)
    plot_cdf(ax3, arm_data["WC4_B"]["held_g"], "WC4_B (4核) cw>0 按seq", "#111827", ls="-.", lw=1.9)
    plot_cdf(ax3, arm_data["A4"]["all_g"], "A4 (civac) 全部格 按seq", "#DC2626", ls="--", lw=1.8)

    ax3.axvline(0.0, color="#6B7280", linestyle=":", linewidth=1.0, alpha=0.8)
    ax3.axvline(76.4, color="#DC2626", linestyle=":", linewidth=1.2, alpha=0.85)
    ax3.text(79.5, 0.96, "76.4 ns (1/13.1M)", fontsize=8.0, color="#DC2626", fontweight="bold", va="center")
    ax3.annotate(
        "超车互换产生 -76 ns\n与 +153 ns 双倍台阶",
        xy=(153.0, 0.80),
        xytext=(158.0, 0.56),
        fontsize=7.6,
        color="#1F2937",
        arrowprops={"arrowstyle": "->", "color": "#4B5563", "lw": 0.9},
    )
    ax3.set_xlim(-95, 250)
    ax3.set_ylim(0, 1.02)
    ax3.set_xlabel("按 seq 序相邻间隔 Δcard_t_in[seq − (seq−1)] (ns)", fontsize=9.3)
    ax3.set_ylabel("累积分布 CDF", fontsize=9.6)
    ax3.set_title("(c) 按 seq 序间隔 CDF：跨 CHA 超车拉出负尾与 153 ns 右偏", fontsize=10.2, pad=7)
    ax3.legend(loc="lower right", fontsize=7.4, frameon=True, facecolor="white", framealpha=0.94)

    # (d) Physical arrival order restored (8-cell window sorted by card_t_in, interior gaps)
    plot_cdf(ax4, arm_data["B"]["phys_inner_g"], "B (WC2 13.11M) 物理序", "#1D4ED8", ls="-", lw=2.1)
    plot_cdf(ax4, arm_data["A1"]["phys_inner_g"], "A1 (去next 13.12M) 物理序", "#7C3AED", ls=":", lw=2.0)
    plot_cdf(ax4, arm_data["A2"]["phys_inner_g"], "A2 (ldr 13.10M) 物理序", "#6B7280", ls="--", lw=1.7)
    plot_cdf(ax4, arm_data["WC4_B"]["phys_inner_g"], "WC4_B (4核 13.11M) 物理序", "#111827", ls="-.", lw=2.0)
    plot_cdf(ax4, arm_data["A3"]["phys_inner_g"], "A3 (wfe 12.41M) 物理序", "#D97706", ls="-", lw=2.1)

    ax4.axvline(76.4, color="#1D4ED8", linestyle=":", linewidth=1.2, alpha=0.85)
    ax4.text(72.0, 0.92, "76.4 ns\n(13.1 M/s)", fontsize=7.8, color="#1D4ED8", fontweight="bold", ha="right", va="top")
    ax4.axvline(80.6, color="#D97706", linestyle=":", linewidth=1.2, alpha=0.85)
    ax4.annotate(
        "80.6 ns (A3 12.41 M/s)",
        xy=(80.6, 0.44),
        xytext=(106.0, 0.44),
        fontsize=7.8,
        color="#B45309",
        fontweight="bold",
        va="center",
        bbox={"boxstyle": "round,pad=0.22", "fc": "#FFFBEB", "ec": "#FCD34D"},
        arrowprops={"arrowstyle": "->", "color": "#D97706", "lw": 1.0},
    )
    ax4.set_xlim(0, 180)
    ax4.set_ylim(0, 1.02)
    ax4.set_xlabel("8 格窗按卡侧到达时刻排序后内部间隔 Δt_arrival (ns)", fontsize=9.3)
    ax4.set_ylabel("累积分布 CDF", fontsize=9.6)
    ax4.set_title("(d) 还原物理到达序：中位收敛回 75–78 ns（A3 = 81 ns），微宏观闭合", fontsize=10.2, pad=7)
    ax4.legend(loc="lower right", fontsize=7.7, frameon=True, facecolor="white", framealpha=0.94)
    fig.suptitle(
        "MRV-236 逐格稀疏戳分析：持有比例、跨 CHA 乱序归因与物理到达序闭合（W=64, 64 B, batch=1, n=3 汇合）",
        fontsize=10.8,
        fontweight="bold",
        y=0.985,
    )
    out = save(fig, "mrv-236-stamps-cdf")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
