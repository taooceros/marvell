#!/usr/bin/env python3
"""MRV-137 Figure 3: 4C + C_line = 64 Host CPU Cost (--app-work, CBR, zero mid-stream
card control traffic) across H in {1, 2, 4} at 16 / 24 / 30 M lines/s.
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

    rates = [0, 4000, 6000, 7500]
    xlabels = ["0 (空轮询)", "16 M 行/s\n(4.0 Mreq/s)", "24 M 行/s\n(6.0 Mreq/s)", "30 M 行/s\n(7.5 Mreq/s)"]
    x = np.arange(len(rates))
    width = 0.25

    fig, axes = plt.subplots(1, 2, figsize=(13.2, 4.9))

    # ------------------------------------------------------------------
    # Panel (a): Total Host Core-Equivalents Consumed & Per-Core %
    # ------------------------------------------------------------------
    ax = axes[0]
    specs = [
        (1, "#d9480f", "H = 1 (cpu40)"),
        (2, "#1971c2", "H = 2 (cpus 40–41)"),
        (4, "#2b8a3e", "H = 4 (cpus 40–43)"),
    ]
    for idx, (h, col, lbl) in enumerate(specs):
        ys_cores = []
        ys_pct = []
        offs = x + (idx - 1) * width
        for r in rates:
            k = f"h{h}_zero_traffic_poll" if r == 0 else f"h{h}_cl64_cbr_work_r{r}"
            pts_wk = [row["steady_window"]["app_work_kps"] for row in cfgs[k]]
            pts_c = [max(0.0, h * (1.0 - w / idle_kps[h])) for w in pts_wk]
            ys_cores.append(med(pts_c))
            ys_pct.append(med([max(0.0, (1.0 - w / idle_kps[h]) * 100.0) for w in pts_wk]))
            ax.scatter([offs[rates.index(r)]] * len(pts_c), pts_c, color="#212529", s=15, zorder=5)
        ax.bar(offs, ys_cores, width=width * 0.92, color=col, alpha=0.88, edgecolor="#212529", lw=0.6, label=lbl, zorder=3)
        for xi, yc, yp in zip(offs, ys_cores, ys_pct):
            txt = f"{yc:.2f}核\n({yp:.0f}%)" if yc >= 0.05 else f"{yp:.1f}%"
            ax.text(xi, yc + 0.04, txt, ha="center", va="bottom", fontsize=7.0, fontweight="bold", color="#212529")

    ax.axhline(1.0, color="#d9480f", ls=":", lw=1.0, alpha=0.7)
    ax.text(0.02, 1.04, "1.0 满核线 (H=1 上限)", fontsize=7.6, color="#d9480f")
    ax.set_xticks(x)
    ax.set_xticklabels(xlabels, fontsize=8.4)
    ax.set_ylabel("主机等价占核数 (核，按同核数空载基准折算)")
    ax.set_title("(a) 4C + C_line = 64 主机占核成本 (括号 = 单核均值%)")
    ax.set_ylim(0, 2.25)
    ax.legend(loc="upper left", fontsize=8.0, frameon=True, framealpha=0.93)

    # ------------------------------------------------------------------
    # Panel (b): Artifact-Free CBR Tail Latency (tot p99 & wire p99)
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
            dy = 1.24 if h == 1 else (0.72 if h == 4 else 1.28)
            ax.text(xm, y * dy, f"{y:.1f} µs", ha="center", va="center", fontsize=7.6, color=col, fontweight="bold")

    ax.axhline(25.0, color="#868e96", ls="--", lw=1.0, zorder=1)
    ax.text(23.5, 29.5, "25 µs 线 (MRV-135 CBR 6–10 ms 伪影已归零)", ha="center", fontsize=7.5, color="#495057")
    ax.set_yscale("log")
    ax.set_xlabel("CBR 稳态到达速率 (M 行/s，256 B = 4 行/包)")
    ax.set_ylabel("端到端 tot p99 (µs，对数轴)")
    ax.set_title("(b) 隔离卡侧控制后 CBR + app_work 真实尾延迟")
    ax.set_xticks(xm_cbr)
    ax.set_xticklabels(["16 M\n(4.0 Mreq/s)", "24 M\n(6.0 Mreq/s)", "30 M\n(7.5 Mreq/s)"])
    ax.set_xlim(14.5, 31.5)
    ax.set_ylim(5.0, 2.5e3)
    ax.legend(loc="upper left", fontsize=7.8, frameon=True, framealpha=0.93)

    fig.suptitle(
        "MRV-137 4C + C_line = 64 主机占核与无伪影 CBR 尾延迟 (128 B 独占子环, W = 256, n = 3)",
        fontsize=10.3, fontweight="bold", y=0.985,
    )
    out = save(fig, "mrv-137-cpu-cost")
    print(f"Saved {out}")

if __name__ == "__main__":
    main()
