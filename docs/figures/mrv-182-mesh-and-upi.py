"""MRV-182 Figure 2: Socket 1 all-32-core Mesh hop profile + Socket 0 cross-UPI scan,
cross-UPI delta across 4 paths, and zero-root core-frequency sensitivity.

All plotted numbers are read directly from logs/mrv182-2026-11-24/runs.jsonl (n=3 interleaved,
0 mismatches, 0 page faults, 100.00% non-multiplexed host PMUs).
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv182-2026-11-24/runs.jsonl"


def load_grouped():
    by_sl = defaultdict(list)
    with RUNS_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            by_sl[(r["suite"], r["label"])].append(r)
    return by_sl


def med(vals):
    return statistics.median(vals)


def main():
    by_sl = load_grouped()

    # ---- Panel (a): All 32 Socket-1 physical cores + 8 Socket-0 cores on BAR4 line 0 (CHA 23) ----
    s1_cpus = list(range(32, 64))
    s0_cpus = [0, 4, 8, 12, 16, 20, 24, 28]

    s1_p50, s1_lo, s1_hi, s1_tor = [], [], [], []
    for c in s1_cpus:
        rs = by_sl[("core_scan_line0", f"cpu{c}")]
        p50s = [x["net_ns"]["p50"] for x in rs]
        s1_p50.append(med(p50s))
        s1_lo.append(min(p50s))
        s1_hi.append(max(p50s))
        s1_tor.append(med([x["cha_s1"]["tor_ns"] for x in rs]))

    s0_p50, s0_lo, s0_hi, s0_tor = [], [], [], []
    for c in s0_cpus:
        rs = by_sl[("core_scan_line0", f"cpu{c}")]
        p50s = [x["net_ns"]["p50"] for x in rs]
        s0_p50.append(med(p50s))
        s0_lo.append(min(p50s))
        s0_hi.append(max(p50s))
        s0_tor.append(med([x["cha_s0"]["tor_ns"] for x in rs]))

    # ---- Panel (b): Cross-UPI delta across 4 paths ----
    # 1. BAR4 line0 (CHA 23)
    upi_line0_p50 = med(s0_p50) - med(s1_p50)
    upi_line0_tor = med(s0_tor) - med(s1_tor)

    # 2. BAR4 4K walk (all 32 CHAs)
    w_s1_p50 = [med([x["net_ns"]["p50"] for x in by_sl[("core_scan_walk", f"cpu{c}")]]) for c in (32, 36, 40, 44, 48, 52, 56, 60)]
    w_s0_p50 = [med([x["net_ns"]["p50"] for x in by_sl[("core_scan_walk", f"cpu{c}")]]) for c in (0, 8, 16, 24)]
    w_s1_tor = [med([x["cha_s1"]["tor_ns"] for x in by_sl[("core_scan_walk", f"cpu{c}")]]) for c in (32, 36, 40, 44, 48, 52, 56, 60)]
    w_s0_tor = [med([x["cha_s0"]["tor_ns"] for x in by_sl[("core_scan_walk", f"cpu{c}")]]) for c in (0, 8, 16, 24)]
    upi_walk_p50 = med(w_s0_p50) - med(w_s1_p50)
    upi_walk_tor = med(w_s0_tor) - med(w_s1_tor)

    # 3. VF0 BAR0 OUT_CNTS (CHA 20)
    v_s1_p50 = [med([x["net_ns"]["p50"] for x in by_sl[("core_scan_vf0", f"cpu{c}")]]) for c in (32, 40, 48, 56)]
    v_s0_p50 = [med([x["net_ns"]["p50"] for x in by_sl[("core_scan_vf0", f"cpu{c}")]]) for c in (0, 16)]
    v_s1_tor = [med([x["cha_s1"]["tor_ns"] for x in by_sl[("core_scan_vf0", f"cpu{c}")]]) for c in (32, 40, 48, 56)]
    v_s0_tor = [med([x["cha_s0"]["tor_ns"] for x in by_sl[("core_scan_vf0", f"cpu{c}")]]) for c in (0, 16)]
    upi_vf0_p50 = med(v_s0_p50) - med(v_s1_p50)
    upi_vf0_tor = med(v_s0_tor) - med(v_s1_tor)

    # 4. Host WB DDR5 (remote S0 - local S1)
    hm_loc = med([x["net_ns"]["p50"] for x in by_sl[("host_mem", "host-wb-ddr5-local")]])
    hm_rem = med([x["net_ns"]["p50"] for x in by_sl[("host_mem", "host-wb-ddr5-remote")]])
    hm_loc_tor = med([x["cha"]["tor_residency_ns"] for x in by_sl[("host_mem", "host-wb-ddr5-local")]])
    hm_rem_tor = med([x["cha"]["tor_residency_ns"] for x in by_sl[("host_mem", "host-wb-ddr5-remote")]])
    upi_ddr_p50 = hm_rem - hm_loc
    upi_ddr_tor = hm_rem_tor - hm_loc_tor

    # ---- Panel (c): Zero-root core-frequency sensitivity (cold 2.20 GHz vs warm 3.90 GHz) ----
    cf_rows = []
    for label, arm, cpu in [("BAR4 首行\n(cpu41)", "bar4-l3-line0-8b", 41), ("BAR4 4K 走步\n(cpu42)", "bar4-l3-walk-8b", 42)]:
        rc = by_sl[("core_freq_cold", f"cpu{cpu}_{arm}_cold")]
        rw = by_sl[("core_freq_warm", f"cpu{cpu}_{arm}_warm")]
        cf_rows.append({
            "label": label,
            "gc": med([x["cpu_ghz"] for x in rc]),
            "gw": med([x["cpu_ghz"] for x in rw]),
            "pc": med([x["net_ns"]["p50"] for x in rc]),
            "pw": med([x["net_ns"]["p50"] for x in rw]),
            "tc": med([x["cha_s1"]["tor_ns"] for x in rc]),
            "tw": med([x["cha_s1"]["tor_ns"] for x in rw]),
        })

    # ---- Plot layout ----
    fig = plt.figure(figsize=(15.0, 6.8))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.55, 1.0], height_ratios=[1.15, 1.0], wspace=0.25, hspace=0.42)
    ax_scan = fig.add_subplot(gs[:, 0])
    ax_upi = fig.add_subplot(gs[0, 1])
    ax_clk = fig.add_subplot(gs[1, 1])

    # ==================== Panel (a): 32 S1 + 8 S0 Core Scan ====================
    x_s1 = np.arange(len(s1_cpus))
    x_s0 = np.arange(len(s0_cpus)) + len(s1_cpus) + 1.8

    # Highlight Socket 0 region
    ax_scan.axvspan(len(s1_cpus) + 0.5, x_s0[-1] + 1.0, color="#fff7ed", alpha=0.85, zorder=0)
    ax_scan.axvline(len(s1_cpus) + 0.5, color="#ea580c", ls="--", lw=1.2, alpha=0.8)

    # Socket 1 p50 & TOR
    ax_scan.errorbar(
        x_s1, s1_p50,
        yerr=[np.array(s1_p50) - np.array(s1_lo), np.array(s1_hi) - np.array(s1_p50)],
        fmt="o-", color="#0284c7", ms=4.5, lw=1.3, capsize=2, label="Socket 1 端到端 net p50 (cpu 32–63)"
    )
    ax_scan.plot(
        x_s1, s1_tor, "s--", color="#0f172a", mfc="white", ms=4.2, lw=1.0, alpha=0.85,
        label="Socket 1 CHA 23 TOR 驻留 (S1_TOR)"
    )

    # Socket 0 p50 & TOR
    ax_scan.errorbar(
        x_s0, s0_p50,
        yerr=[np.array(s0_p50) - np.array(s0_lo), np.array(s0_hi) - np.array(s0_p50)],
        fmt="o-", color="#ea580c", ms=5.0, lw=1.4, capsize=2, label="Socket 0 跨 UPI net p50 (cpu 0–28)"
    )
    ax_scan.plot(
        x_s0, s0_tor, "D--", color="#9a3412", mfc="white", ms=4.5, lw=1.0, alpha=0.9,
        label="Socket 0 CHA 23 TOR 驻留 (S0_TOR, upi_ncs=1.00)"
    )

    # Annotate Socket 1 Mesh span (min vs max) and 6-core TOR release boundary
    s1_min, s1_max = min(s1_p50), max(s1_p50)
    ax_scan.annotate(
        f"Socket 1 2D Mesh 跨度 = {s1_max - s1_min:.1f} ns\n近 CHA 23 (cpu 53–59): {s1_min:.1f} ns\n远端 (cpu 32–47): {s1_max:.1f} ns",
        xy=(24, s1_p50[24]),
        xytext=(12.0, 646.0),
        fontsize=8.0,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="#0284c7", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#0284c7", lw=1.0),
    )

    ax_scan.annotate(
        "6 核 TOR 低 ~8 ns 阶跃\n(cpu 37–39, 54–56)\n端到端 p50 不变，属片内环/释放边界",
        xy=(6, s1_tor[6]),
        xytext=(1.0, 581.5),
        fontsize=7.6,
        color="#334155",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#f8fafc", edgecolor="#94a3b8", lw=0.8),
        arrowprops=dict(arrowstyle="->", color="#475569", lw=0.9),
    )

    ax_scan.text(
        x_s0[3], 680.5,
        f"跨 Socket UPI\nS0 中位 {med(s0_p50):.1f} ns\n(vs S1 +{upi_line0_p50:.1f} ns)",
        ha="center", va="bottom", fontsize=8.0, fontweight="bold", color="#9a3412",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#ea580c", lw=0.9),
    )

    # X ticks: show every 2nd CPU on S1 + all 8 S0 CPUs
    xt_pos = list(x_s1[::2]) + list(x_s0)
    xt_lbl = [str(c) for c in s1_cpus[::2]] + [str(c) for c in s0_cpus]
    ax_scan.set_xticks(xt_pos)
    ax_scan.set_xticklabels(xt_lbl, fontsize=7.5, rotation=45)
    ax_scan.set_xlim(-1.0, x_s0[-1] + 1.0)
    ax_scan.set_ylim(575, 695)
    ax_scan.set_xlabel("绑定物理核编号（左：Socket 1 全 32 核 cpu 32–63；右：Socket 0 跨 UPI 8 核；全核实测 3.90 GHz）", fontsize=8.8)
    ax_scan.set_ylabel("净延迟 / CHA TOR 驻留 (ns)", fontsize=9.2)
    ax_scan.set_title("(a) 全物理核 Mesh 跳数与跨 Socket UPI 扫描（BAR4 首行 8 B，固定 CHA 23）", fontsize=10.3, fontweight="bold", pad=8)
    ax_scan.legend(loc="lower right", fontsize=7.5, frameon=True, facecolor="white", framealpha=0.94)
    ax_scan.grid(alpha=0.32)

    # ==================== Panel (b): Cross-UPI Delta Comparison ====================
    upi_labels = ["BAR4 首行\n(CHA 23)", "BAR4 4K 走步\n(全 32 CHA)", "VF0 BAR0\nOUT_CNTS", "Host WB DDR5\n(远端−本地)"]
    upi_p50_vals = [upi_line0_p50, upi_walk_p50, upi_vf0_p50, upi_ddr_p50]
    upi_tor_vals = [upi_line0_tor, upi_walk_tor, upi_vf0_tor, upi_ddr_tor]
    xu = np.arange(len(upi_labels))
    w = 0.35

    ax_upi.bar(xu - w / 2, upi_p50_vals, width=w, color="#ea580c", alpha=0.9, label="端到端 Δnet_p50 (ns)")
    ax_upi.bar(xu + w / 2, upi_tor_vals, width=w, color="#475569", alpha=0.85, label="CHA TOR ΔT_TOR (ns)")
    for i, (vp, vt) in enumerate(zip(upi_p50_vals, upi_tor_vals)):
        ax_upi.text(i - w / 2, vp + 1.5, f"+{vp:.1f}", ha="center", va="bottom", fontsize=7.9, fontweight="bold", color="#9a3412")
        ax_upi.text(i + w / 2, vt + 1.5, f"+{vt:.1f}", ha="center", va="bottom", fontsize=7.7, color="#334155")

    ax_upi.set_xticks(xu)
    ax_upi.set_xticklabels(upi_labels, fontsize=8.0)
    ax_upi.set_ylim(0, 82)
    ax_upi.set_ylabel("跨 Socket UPI 往返增量 (ns)", fontsize=8.8)
    ax_upi.set_title("(b) 跨 UPI 往返增量四路径交叉验真（全片均摊 ~58–65 ns）", fontsize=9.8, fontweight="bold", pad=8)
    ax_upi.legend(loc="upper left", fontsize=7.6, frameon=True, facecolor="white", framealpha=0.92)
    ax_upi.grid(axis="y", alpha=0.32)
    ax_upi.grid(axis="x", visible=False)

    # ==================== Panel (c): Zero-Root Core Clock Sensitivity ====================
    xc = np.arange(len(cf_rows))
    wc = 0.32
    d_net = [r["pc"] - r["pw"] for r in cf_rows]
    d_tor = [r["tc"] - r["tw"] for r in cf_rows]
    est_39 = [(r["pc"] - r["pw"]) / (1.0 / r["gc"] - 1.0 / r["gw"]) / 3.9 for r in cf_rows]

    ax_clk.bar(xc - wc / 2, d_net, width=wc, color="#0284c7", alpha=0.9, label="端到端 Δnet_p50 (2.20G − 3.90G)")
    ax_clk.bar(xc + wc / 2, d_tor, width=wc, color="#64748b", alpha=0.85, label="CHA TOR ΔT_TOR (应为 ~0 ns)")
    ax_clk.axhline(0, color="#334155", lw=0.9)

    for i, (dn, dt, e39) in enumerate(zip(d_net, d_tor, est_39)):
        ax_clk.text(i - wc / 2, dn + 0.4, f"+{dn:.1f} ns\n(3.9G 占~{e39:.1f} ns)", ha="center", va="bottom", fontsize=7.7, fontweight="bold", color="#0369a1")
        ax_clk.text(i + wc / 2, dt - 0.5 if dt < 0 else dt + 0.4, f"{dt:+.1f} ns", ha="center", va="top" if dt < 0 else "bottom", fontsize=7.7, color="#334155")

    ax_clk.set_xticks(xc)
    ax_clk.set_xticklabels([r["label"] for r in cf_rows], fontsize=8.3)
    ax_clk.set_ylim(-3.2, 13.5)
    ax_clk.set_ylabel("核频 2.20 vs 3.90 GHz 差值 (ns)", fontsize=8.8)
    ax_clk.set_title("(c) 无 root 核频敏感度（固定 Mesh 2.20 GHz，schedutil 冷/热态）", fontsize=9.8, fontweight="bold", pad=8)
    ax_clk.legend(loc="upper left", fontsize=7.4, frameon=True, facecolor="white", framealpha=0.92)
    ax_clk.grid(axis="y", alpha=0.32)
    ax_clk.grid(axis="x", visible=False)

    fig.suptitle(
        "MRV-182 实测：Sapphire Rapids 全物理核 2D Mesh 跳数、跨 UPI 路径验真与核频敏感度",
        fontsize=11.8,
        fontweight="bold",
        y=0.985,
    )
    out = save(fig, "mrv-182-mesh-and-upi")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
