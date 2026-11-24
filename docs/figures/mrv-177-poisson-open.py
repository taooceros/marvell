#!/usr/bin/env python3
"""MRV-177 Figure 4: Open-loop Poisson 10-25 M/s end-to-end p99,
steady-state achieved_mid80_mps, and host CHA / doorbell rate decomposition.

Reads verified runs directly from logs/mrv177-2026-11-24/runs.jsonl.
"""
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot  # noqa: F401 - registers CJK font & white background style

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv177-2026-11-24/runs.jsonl"


def load_verified():
    by_cfg = defaultdict(list)
    with RUNS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            cp = r["card_pmu"]
            hp = r["host_pmu"]
            corrupt = sum(c["corrupt"] for c in cp["cores"])
            assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0 and corrupt == 0
            assert hp["min_pct"] >= 99.99 and cp["min_pct"] >= 99.99
            by_cfg[(r["phase"], r["workload"], r["arm"])].append(r)
    return by_cfg


def med_err(vals):
    s = sorted(vals)
    m = s[len(s) // 2]
    return m, m - s[0], s[-1] - m


def main():
    by_cfg = load_verified()

    fig, axes = plt.subplots(2, 2, figsize=(14.2, 9.8))
    fig.suptitle(
        "MRV-177 开环泊松（10–25 M/s）打穿 [18, 25] 死局：对称 C_line=64 主对照与无整形对照（n = 3 中位数）",
        fontsize=12.8,
        fontweight="bold",
        y=0.985,
    )

    rates = [("p10", 10.0), ("p13.5", 13.5), ("p16", 16.0), ("p20", 20.0), ("p25", 25.0)]
    xs = np.array([r[1] for r in rates])

    top_specs = [
        ("base_c64", "Baseline (C_line=64)", "#475569", "o", "-", 1.7),
        ("bq_probe_c64", "Arm A bq_probe (C_line=64)", "#D97706", "s", "-", 1.6),
        ("bq_wait_c64", "Arm A 变体 bq_wait (C_line=64)", "#7C3AED", "^", "-", 1.6),
        ("db_pkt_f_c64", "Arm B1 逐包门铃 (C_line=64)", "#DC2626", "v", "-", 1.6),
        ("db_adp_f_c64", "Arm B2 批合并 (C_line=64)", "#0F766E", "D", "-", 2.5),
        ("base", "Baseline (无整形)", "#94A3B8", "o", "--", 1.4),
        ("db_adp_f", "Arm B2 批合并 (无整形)", "#0284C7", "P", "--", 1.9),
        ("base_c32", "Baseline C_line=32 (MRV-123)", "#64748B", "x", ":", 1.3),
    ]

    top_handles, top_labels = [], []
    for col, phase in enumerate(["1C", "4C"]):
        ax = axes[0, col]
        ax.axhline(2.0, color="#0F766E", linestyle=":", linewidth=1.1, alpha=0.75)
        ax.text(25.4, 1.32, "2 µs 目标线", fontsize=8.0, color="#0F766E", ha="right")

        for arm, lbl, color, mk, ls, lw in top_specs:
            ys, los, his = [], [], []
            for wl, _ in rates:
                m, lo, hi = med_err([r["tot_us"]["p99"] for r in by_cfg[(phase, wl, arm)]])
                ys.append(m); los.append(lo); his.append(hi)
            line, = ax.plot(
                xs, ys, label=lbl, color=color, marker=mk, linestyle=ls,
                linewidth=lw, markersize=6.2 if "db_adp" in arm else 5.2,
                zorder=5 if "db_adp_f_c64" == arm else (4 if arm == "db_adp_f" else 3),
            )
            if col == 0:
                top_handles.append(line)
                top_labels.append(lbl)
            if arm in ("db_adp_f_c64", "db_adp_f"):
                ax.errorbar(xs, ys, yerr=[los, his], fmt="none", ecolor=color, elinewidth=0.9, capsize=2.2, alpha=0.5, zorder=4)

        if phase == "1C":
            p20_un = med_err([r["tot_us"]["p99"] for r in by_cfg[("1C", "p20", "db_adp_f")]])[0]
            p25_un = med_err([r["tot_us"]["p99"] for r in by_cfg[("1C", "p25", "db_adp_f")]])[0]
            p20_c64 = med_err([r["tot_us"]["p99"] for r in by_cfg[("1C", "p20", "db_adp_f_c64")]])[0]
            c64_25_rs = by_cfg[("1C", "p25", "db_adp_f_c64")]
            r2_run = max(c64_25_rs, key=lambda r: r["tot_us"]["p99"])
            no_stall_p999 = sorted(r["tot_us"]["p999"] for r in c64_25_rs if r is not r2_run)
            cap_1c_c64 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w64", "db_adp_f")]])[0]
            catchup = cap_1c_c64 - 25.0
            ax.annotate(
                f"1C 无整形 20/25 M/s:\n{p20_un:.2f} / {p25_un:.2f} µs\n(C_line=64 20M: {p20_c64:.2f} µs)",
                xy=(25.0, p25_un), xytext=(22.0, 520.0),
                ha="center",
                fontsize=7.7, fontweight="bold", color="#0F766E",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#F0FDFA", edgecolor="#0F766E", lw=0.9),
                arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.0),
            )
            ax.annotate(
                f"C_line=64 25M r2 ({r2_run['tot_us']['p99']:.1f} µs):\n"
                f"单次 {r2_run['wire_us']['max']:.2f} µs 系统线停顿 + {catchup:.2f} M/s 追赶余量\n"
                f"(无停顿 r0/r1 p99.9 = {no_stall_p999[0]:.2f}–{no_stall_p999[1]:.2f} µs)",
                xy=(25.0, r2_run["tot_us"]["p99"]), xytext=(20.7, 11.2),
                ha="center",
                fontsize=6.7, fontweight="bold", color="#0F766E",
                bbox=dict(boxstyle="round,pad=0.20", facecolor="#ECFDF5", edgecolor="#0F766E", lw=0.8),
                arrowprops=dict(arrowstyle="->", color="#0F766E", lw=0.9),
            )
        else:
            p20_c64 = med_err([r["tot_us"]["p99"] for r in by_cfg[("4C", "p20", "db_adp_f_c64")]])[0]
            p25_c64 = med_err([r["tot_us"]["p99"] for r in by_cfg[("4C", "p25", "db_adp_f_c64")]])[0]
            p25_un = med_err([r["tot_us"]["p99"] for r in by_cfg[("4C", "p25", "db_adp_f")]])[0]
            c64_all = [r["tot_us"]["p99"] for r in by_cfg[("4C", "p20", "db_adp_f_c64")] + by_cfg[("4C", "p25", "db_adp_f_c64")]]
            ax.annotate(
                f"4C C_line=64 20/25 M/s:\n{p20_c64:.2f} / {p25_c64:.2f} µs\n(n=3 极紧 [{min(c64_all):.2f}, {max(c64_all):.2f}])",
                xy=(25.0, p25_c64), xytext=(20.4, 11.5),
                ha="center",
                fontsize=7.9, fontweight="bold", color="#0F766E",
                bbox=dict(boxstyle="round,pad=0.26", facecolor="#F0FDFA", edgecolor="#0F766E", lw=0.9),
                arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.0),
            )
            ax.annotate(
                f"4C 无整形高在途 (>64)\n25M 退化至 {p25_un/1000:.1f} ms\n(128B L3 共行 + L2 预取越界)",
                xy=(25.0, p25_un), xytext=(22.3, 420.0),
                ha="center",
                fontsize=7.2, color="#0369A1", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.22", facecolor="#F0F9FF", edgecolor="#0284C7", lw=0.8),
                arrowprops=dict(arrowstyle="->", color="#0284C7", lw=1.0),
            )

        ax.set_yscale("log")
        ax.set_ylim(1.0, 9e5)
        ax.set_xlim(9.5, 25.8)
        ax.set_xticks(xs)
        ax.set_xticklabels([f"{v:g}" for v in xs], fontsize=9.0)
        ax.set_xlabel("开环泊松目标速率 (Mmsg/s)", fontsize=9.3)
        ax.set_ylabel("端到端 tot p99 (µs，对数)", fontsize=9.3)
        bq_note = "fast_dt=0" if phase == "1C" else "fast_dt=480ns"
        ax.set_title(f"{phase} 开环泊松端到端 p99（bq_probe: {bq_note}）", fontsize=10.5, fontweight="bold")

    fig.legend(
        top_handles, top_labels, loc="upper center", bbox_to_anchor=(0.5, 0.948),
        ncol=4, fontsize=8.1, frameon=True, columnspacing=1.2, handlelength=2.2,
    )

    # Bottom-left: Steady-state achieved_mid80_mps vs offered rate
    ax = axes[1, 0]
    ax.plot([9.5, 25.5], [9.5, 25.5], color="#94A3B8", linestyle="--", linewidth=1.2, label="理想 y = x（稳态跟满）")
    ax.axvspan(18.0, 25.0, color="#FEF3C7", alpha=0.35, zorder=0)
    ax.text(21.5, 2.2, "MRV-123 [18, 25] 区间", ha="center", fontsize=8.2, color="#92400E", fontweight="bold")

    mid_specs = [
        ("4C", "db_adp_f_c64", "4C Arm B2 批合并 (C_line=64)", "#0F766E", "D", "-", 2.4),
        ("1C", "db_adp_f", "1C Arm B2 批合并 (无整形)", "#0284C7", "P", "--", 2.0),
        ("4C", "bq_wait_c64", "4C Arm A 变体 bq_wait (C_line=64)", "#7C3AED", "^", "-", 1.6),
        ("4C", "base_c32", "4C Baseline C_line=32 (MRV-123)", "#64748B", "x", ":", 1.6),
        ("4C", "base_c64", "4C Baseline (C_line=64)", "#475569", "o", "-", 1.7),
        ("4C", "bq_probe_c64", "4C Arm A bq_probe (C_line=64)", "#D97706", "s", "-", 1.5),
        ("4C", "db_pkt_f_c64", "4C Arm B1 逐包门铃 (C_line=64)", "#DC2626", "v", "-", 1.6),
    ]
    for ph, arm, lbl, color, mk, ls, lw in mid_specs:
        ys, los, his = [], [], []
        for wl, _ in rates:
            m, lo, hi = med_err([r["achieved_mid80_mps"] for r in by_cfg[(ph, wl, arm)]])
            ys.append(m); los.append(lo); his.append(hi)
        ax.errorbar(xs, ys, yerr=[los, his], label=lbl, color=color, marker=mk, linestyle=ls, linewidth=lw, markersize=5.8, capsize=2.5)

    m25_full = med_err([r["achieved_mid80_mps"] for r in by_cfg[("4C", "p25", "db_adp_f_c64")]])[0]
    q99_4c_c64 = med_err([r["queue_us"]["p99"] for r in by_cfg[("4C", "p25", "db_adp_f_c64")]])[0]
    q99_1c_un = med_err([r["queue_us"]["p99"] for r in by_cfg[("1C", "p25", "db_adp_f")]])[0]
    ax.annotate(
        f"4C(C=64) / 1C(无整形) 跟满 {m25_full:.2f} M/s\nqueue p99: {q99_4c_c64:.2f} / {q99_1c_un:.2f} µs",
        xy=(25.0, m25_full), xytext=(19.0, 24.1),
        ha="center",
        fontsize=7.7, fontweight="bold", color="#0F766E",
        bbox=dict(boxstyle="round,pad=0.24", facecolor="#F0FDFA", edgecolor="#0F766E", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.0),
    )
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{v:g}" for v in xs], fontsize=9.0)
    ax.set_xlim(9.5, 25.8)
    ax.set_ylim(0, 27.5)
    ax.set_xlabel("开环泊松目标速率 (Mmsg/s)", fontsize=9.3)
    ax.set_ylabel("中段 80% 稳态吞吐 achieved_mid80 (Mmsg/s)", fontsize=9.2)
    ax.set_title("中段 80% 稳态吞吐：对称 C_line=64 下仅批合并门铃跟满 20/25 M/s", fontsize=10.1, fontweight="bold")
    ax.legend(loc="upper left", fontsize=7.5, frameon=True)

    # Bottom-right: 25 M/s hardware PMU comparison (Host CHA inflight + Doorbell rate / pkts_per_db)
    ax = axes[1, 1]
    pmu_cases = [
        ("1C Base\n无整形", "1C", "p25", "base", "#475569"),
        ("1C Arm A\nbq_probe 无整形", "1C", "p25", "bq_probe", "#D97706"),
        ("1C Arm B2\n批合并无整形", "1C", "p25", "db_adp_f", "#0F766E"),
        ("4C Base\n无整形", "4C", "p25", "base", "#64748B"),
        ("4C Arm B2\n批合并 C=64", "4C", "p25", "db_adp_f_c64", "#0284C7"),
        ("4C Arm B1\n逐包 C=64*", "4C", "p25", "db_pkt_f_c64", "#DC2626"),
    ]
    px = np.arange(len(pmu_cases))
    cha_vals, cha_lo, cha_hi = [], [], []
    dbr_vals, ppdb_vals = [], []
    for _, ph, wl, arm, _ in pmu_cases:
        rs = by_cfg[(ph, wl, arm)]
        m, lo, hi = med_err([r["host_pmu"]["cha_inflight"] for r in rs])
        cha_vals.append(m); cha_lo.append(lo); cha_hi.append(hi)
        dbr_vals.append(med_err([r["db_rate_mps"] for r in rs])[0])
        ppdb_vals.append(med_err([r["pkts_per_db"] for r in rs])[0])

    ax.bar(
        px, cha_vals, width=0.55, color=[c[4] for c in pmu_cases],
        edgecolor="#1E293B", linewidth=0.7, yerr=[cha_lo, cha_hi], capsize=3, alpha=0.88,
    )
    ax.set_yscale("log")
    ax.set_ylim(0.5, 650)
    for i, v in enumerate(cha_vals):
        extra = ""
        if dbr_vals[i] > 0:
            extra = f"\ndb {dbr_vals[i]:.2f}M/s\n({ppdb_vals[i]:.0f}包/铃)"
        ax.text(px[i], v * 1.18, f"CHA {v:.1f}{extra}", ha="center", va="bottom",
                fontsize=7.5, fontweight="bold" if "Arm B2" in pmu_cases[i][0] else "normal",
                color="#0F766E" if "Arm B2" in pmu_cases[i][0] else "#1E293B")

    pkt_rs = by_cfg[("4C", "p25", "db_pkt_f_c64")]
    pkt_mid80 = med_err([r["achieved_mid80_mps"] for r in pkt_rs])[0]
    pkt_snp = med_err([
        r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / max(1, sum(c["taken"] for c in r["card_pmu"]["cores"]))
        for r in pkt_rs
    ])[0]
    ax.text(
        0.98, 0.94,
        f"*4C 逐包门铃因 1W4R 冲撞\n实得仅 {pkt_mid80:.2f} M/s，故 CHA 低；\nsnoop 达 {pkt_snp:.2f}/包且触发 TAD 重试",
        transform=ax.transAxes, ha="right", va="top", fontsize=7.6, color="#991B1B",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#FEF2F2", edgecolor="#DC2626", lw=0.8),
    )

    ax.set_xticks(px)
    ax.set_xticklabels([c[0] for c in pmu_cases], fontsize=8.0)
    ax.set_ylabel("主机 CHA 在途事务数（对数）", fontsize=9.3)
    ax.set_title("25 M/s 主机入向桥拥塞（CHA 在途）与门铃合并频次拆解", fontsize=10.3, fontweight="bold")

    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.87])
    out_dir = Path(__file__).resolve().parent
    png = out_dir / "mrv-177-poisson-open.png"
    fig.savefig(png)
    fig.savefig(out_dir / "mrv-177-poisson-open.svg")
    plt.close(fig)
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
