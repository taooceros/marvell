#!/usr/bin/env python3
"""MRV-137 Figure 3: 4C + C_line = 64 Host CPU Cost (--app-work, CBR, zero mid-stream
card control traffic) across H in {1, 2, 4} at 16 / 24 / 30 M lines/s, with H = 4
forwarding vs lockstep credit-spin waste breakdown (71.2% at 30 M/s) and H = 1 wire p99.
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs/mrv137-2026-11-22/runs.jsonl"


def load_runs():
    rows = []
    with RUNS.open() as f:
        for line in f:
            r = json.loads(line)
            assert r.get("verified"), f"Unverified run: {r.get('cfg')} rep={r.get('rep')}"
            rows.append(r)
    return rows


def by_cfg(rows):
    d = {}
    for r in rows:
        d.setdefault(r["cfg"], []).append(r)
    return d


def med(vals):
    return statistics.median(vals)


def main():
    rows = load_runs()
    cfgs = by_cfg(rows)

    idle_kps = {
        h: med([r["cal"]["total_app_work_kps"] for r in cfgs[f"idle_cal_h{h}"]])
        for h in (1, 2, 4)
    }
    idle_cpw = {
        h: med([r["pmu"]["active_cycles"] / (r["cal"]["total_app_work_kps"] * 1000.0 * r["pmu"]["dt_s"])
                for r in cfgs[f"idle_cal_h{h}"]])
        for h in (1, 2, 4)
    }

    rates = [0, 4000, 6000, 7500]
    xlabels = ["0 (空轮询\n周期归一化)", "16 M 行/s\n(4.0 Mreq/s)", "24 M 行/s\n(6.0 Mreq/s)", "30 M 行/s\n(7.5 Mreq/s)"]
    x = np.arange(len(rates))
    width = 0.25

    # Compute H=4 linear forwarding slope from 16 M/s (4.0 Mrps)
    h4_16_cores = med([4.0 * (1.0 - r["steady_window"]["app_work_kps"] / idle_kps[4])
                       for r in cfgs["h4_cl64_cbr_work_r4000"]])
    fwd_slope = h4_16_cores / 4.0  # cores per Mreq/s (~0.0816, ~317 cyc/RPC)

    fig, axes = plt.subplots(1, 2, figsize=(13.6, 5.1))

    # ------------------------------------------------------------------
    # Panel (a): Total Host Core-Equivalents Consumed & Lockstep Spin Breakdown
    # ------------------------------------------------------------------
    ax = axes[0]
    specs = [
        (1, "#d9480f", "H = 1 (cpu40)"),
        (2, "#1971c2", "H = 2 (cpus 40–41)"),
        (4, "#2b8a3e", "H = 4 (cpus 40–43)"),
    ]
    h4_30_tot, h4_30_fwd, h4_30_spin = 0.0, 0.0, 0.0
    h1_30_tot = 0.0
    for idx, (h, col, lbl) in enumerate(specs):
        ys_cores = []
        ys_pct = []
        offs = x + (idx - 1) * width
        for r_i, r in enumerate(rates):
            k = f"h{h}_zero_traffic_poll" if r == 0 else f"h{h}_cl64_cbr_work_r{r}"
            if r == 0:
                # Cycle-normalized zero-traffic poll overhead (removes idle_cal freq drift)
                pts_pct = [max(0.0, (row["pmu"]["active_cycles"] / (row["steady_window"]["app_work_kps"] * 1000.0 * row["pmu"]["dt_s"]) / idle_cpw[h] - 1.0) * 100.0)
                           for row in cfgs[k]]
                pts_c = [h * p / 100.0 for p in pts_pct]
            else:
                pts_wk = [row["steady_window"]["app_work_kps"] for row in cfgs[k]]
                pts_c = [max(0.0, h * (1.0 - w / idle_kps[h])) for w in pts_wk]
                pts_pct = [max(0.0, (1.0 - w / idle_kps[h]) * 100.0) for w in pts_wk]
            ys_cores.append(med(pts_c))
            ys_pct.append(med(pts_pct))
            ax.scatter([offs[r_i]] * len(pts_c), pts_c, color="#212529", s=15, zorder=6)

        if h == 1:
            h1_30_tot = ys_cores[3]

        if h != 4:
            ax.bar(offs, ys_cores, width=width * 0.92, color=col, alpha=0.88,
                   edgecolor="#212529", lw=0.6, label=lbl, zorder=3)
        else:
            # Split H=4 at 24M & 30M into linear forwarding estimate vs lockstep credit spin waste
            fwd_bars = [
                ys_cores[0],
                ys_cores[1],
                min(ys_cores[2], fwd_slope * 6.0),
                min(ys_cores[3], fwd_slope * 7.5),
            ]
            spin_bars = [max(0.0, tot - f) for tot, f in zip(ys_cores, fwd_bars)]
            h4_30_tot, h4_30_fwd, h4_30_spin = ys_cores[3], fwd_bars[3], spin_bars[3]
            ax.bar(offs, fwd_bars, width=width * 0.92, color=col, alpha=0.90,
                   edgecolor="#212529", lw=0.6, label=f"{lbl} 转发", zorder=3)
            ax.bar(offs, spin_bars, bottom=fwd_bars, width=width * 0.92, color="#b2f2bb",
                   hatch="///", edgecolor="#2b8a3e", lw=0.8, label="H = 4 锁步信用空转 (自旋浪费)", zorder=4)

        for xi, yc, yp in zip(offs, ys_cores, ys_pct):
            txt = f"{yc:.2f}核\n({yp:.0f}%)" if yc >= 0.05 else f"{yp:.1f}%"
            ax.text(xi, yc + 0.04, txt, ha="center", va="bottom", fontsize=6.9, fontweight="bold", color="#212529")

    spin_pct = h4_30_spin / h4_30_tot * 100.0
    ax.annotate(
        f"30M H=4 ({h4_30_tot:.2f} 核 = H=1 的 {h4_30_tot/h1_30_tot:.2f}×):\n"
        f"实际转发 ~{h4_30_fwd:.2f} 核 + 信用空转 ~{h4_30_spin:.2f} 核 ({spin_pct:.1f}%)",
        xy=(x[3] + width - 0.08, 1.82), xytext=(0.45, 1.72),
        fontsize=7.1, color="#2b8a3e", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", fc="#ebfbee", ec="#2b8a3e", lw=0.8),
        arrowprops=dict(arrowstyle="->", color="#2b8a3e", lw=1.0),
    )

    ax.axhline(1.0, color="#d9480f", ls=":", lw=1.0, alpha=0.7)
    ax.text(0.02, 1.04, "1.0 满核线 (H=1 上限)", fontsize=7.4, color="#d9480f")
    ax.set_xticks(x)
    ax.set_xticklabels(xlabels, fontsize=8.1)
    ax.set_ylabel("主机等价占核数 (核，按同核数空载基准折算)")
    ax.set_title("(a) 4C + C_line = 64 主机占核与锁步自旋拆解")
    ax.set_ylim(0, 2.45)
    ax.legend(loc="upper left", fontsize=7.4, frameon=True, framealpha=0.94)

    # ------------------------------------------------------------------
    # Panel (b): Artifact-Free CBR Tail Latency (tot p99 & H=1 wire p99)
    # ------------------------------------------------------------------
    ax = axes[1]
    cbr_rates = [4000, 6000, 7500]
    xm_cbr = [r * 4.0 / 1000.0 for r in cbr_rates]
    for h, col, mk, lbl in [
        (1, "#d9480f", "o", "H = 1 tot p99 (CBR + app_work)"),
        (2, "#1971c2", "s", "H = 2 tot p99 (CBR + app_work)"),
        (4, "#2b8a3e", "^", "H = 4 tot p99 (CBR + app_work)"),
    ]:
        ys = []
        for r, xm in zip(cbr_rates, xm_cbr):
            pts = [row["client"]["tot_p99_us"] for row in cfgs[f"h{h}_cl64_cbr_work_r{r}"]]
            ys.append(med(pts))
            ax.scatter([xm] * len(pts), pts, color=col, s=16, alpha=0.40, zorder=3)
        ax.plot(xm_cbr, ys, color=col, marker=mk, lw=2.1, ms=6.2, label=lbl, zorder=4)
        for xm, y in zip(xm_cbr, ys):
            dy = 1.25 if h == 1 else (0.72 if h == 4 else 1.28)
            ax.text(xm, y * dy, f"{y:.1f} µs", ha="center", va="center", fontsize=7.5, color=col, fontweight="bold")

    # Overlay H=1 wire p99 to show pre-send queueing vs datapath
    ys_h1w = [med([row["client"]["wire_p99_us"] for row in cfgs[f"h1_cl64_cbr_work_r{r}"]]) for r in cbr_rates]
    ax.plot(xm_cbr, ys_h1w, color="#d9480f", ls="--", lw=1.4, alpha=0.85, zorder=3,
            label="H = 1 wire p99 (H=2/4 均与 tot 重合)")
    ax.text(30.0, ys_h1w[-1] * 0.76, f"wire {ys_h1w[-1]:.1f}µs", ha="center", va="center",
            fontsize=7.0, color="#d9480f")

    ax.axhline(25.0, color="#868e96", ls="--", lw=1.0, zorder=1)
    ax.text(23.2, 21.0, "25 µs 线 (MRV-135 CBR 6–10 ms 伪影已归零)", ha="center", va="top", fontsize=7.3, color="#495057")
    ax.set_yscale("log")
    ax.set_xlabel("CBR 稳态到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("端到端 tot p99 / wire p99 (µs，对数轴)")
    ax.set_title("(b) 隔离卡侧控制后 CBR + app_work 真实尾延迟")
    ax.set_xticks(xm_cbr)
    ax.set_xticklabels(["16 M\n(4.0 Mreq/s)", "24 M\n(6.0 Mreq/s)", "30 M\n(7.5 Mreq/s)"])
    ax.set_xlim(14.5, 31.8)
    ax.set_ylim(5.0, 2.5e3)
    ax.legend(loc="upper left", fontsize=7.4, frameon=True, framealpha=0.94)

    fig.suptitle(
        "MRV-137 4C + C_line = 64 主机占核拆解与无伪影 CBR 尾延迟 (128 B 独占子环, W = 256, n = 3)",
        fontsize=10.2, fontweight="bold", y=0.985,
    )
    out = save(fig, "mrv-137-cpu-cost")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
