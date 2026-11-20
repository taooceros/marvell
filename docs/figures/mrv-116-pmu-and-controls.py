#!/usr/bin/env python3
"""Plot MRV-116 hardware PMU evidence & methodological diagnostics (2x2):
  (a) W=64 seq Socket-1 IMC DRAM read/write CAS per msg + 100.00% DDIO hit rate (noting WC1 card-core ceiling)
  (b) Uncapped unidirectional H2C saturation (seq vs rand, 175 -> 52 M/s) vs WC1 closed-loop ceiling
  (c) W=64 fill_rand 4 MiB slow-state bifurcation PMU diagnosis (busy_batch 31.4->1.89, tad_snp 0.99->2.32, LLC miss=0)
  (d) 32 MiB (<60 MiB LLC) warmup-ratio artifact diagnosis across H2C (8x), W=64 (4x), W=16 (0.5x), W=1 (0.04x)

Reads raw verified runs directly from logs/mrv116-2026-11-20/runs.jsonl.
"""
import json
import pathlib
import statistics

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv116-2026-11-20/runs.jsonl"

SIZES = ["64k", "4m", "32m", "128m"]
X_POS = np.arange(len(SIZES), dtype=float)
X_LABELS = ["64 KiB\n(L1/L2)", "4 MiB\n(LLC)", "32 MiB\n(半 LLC)", "128 MiB\n(>60 MiB LLC)"]

C_TEAL = "#0f766e"
C_TEAL_L = "#5eead4"
C_BLUE = "#2563eb"
C_BLUE_L = "#93c5fd"
C_ORANGE = "#d97706"
C_RED = "#dc2626"
C_SLATE = "#475569"


def load_runs():
    rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
    for r in rows:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
        assert r["host_pmu"]["min_pct"] >= 99.99 and r["card_pmu"]["min_pct"] >= 99.99
    return rows


def group_vals(rows, part, prefix, getter):
    out = []
    for sz in SIZES:
        cfg = f"{prefix}_{sz}"
        vals = [getter(r) for r in rows if r["part"] == part and r["cfg"] == cfg]
        assert len(vals) == 3, f"expected 3 reps for {part}/{cfg}, got {len(vals)}"
        out.append(vals)
    return out


def main():
    rows = load_runs()
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(13.0, 8.6))

    # =========================================================================
    # Panel (a): W=64 seq IMC DRAM read/write + DDIO hit rate + WC1 ceiling note
    # =========================================================================
    w = 0.20
    fill_rd = [statistics.mean(v) for v in group_vals(rows, "part1_w64", "fill_seq", lambda r: r["host_pmu"]["imc_cas_rd"] / r["measured"])]
    fill_wr = [statistics.mean(v) for v in group_vals(rows, "part1_w64", "fill_seq", lambda r: r["host_pmu"]["imc_cas_wr"] / r["measured"])]
    cold_rd = [statistics.mean(v) for v in group_vals(rows, "part1_w64", "cold_seq", lambda r: r["host_pmu"]["imc_cas_rd"] / r["measured"])]
    cold_wr = [statistics.mean(v) for v in group_vals(rows, "part1_w64", "cold_seq", lambda r: r["host_pmu"]["imc_cas_wr"] / r["measured"])]
    fill_ddio = [statistics.mean(v) for v in group_vals(rows, "part1_w64", "fill_seq", lambda r: r["host_pmu"]["ddio_hit_pct"])]
    cold_ddio = [statistics.mean(v) for v in group_vals(rows, "part1_w64", "cold_seq", lambda r: r["host_pmu"]["ddio_hit_pct"])]

    b1 = ax1.bar(X_POS - 1.5 * w, fill_rd, width=w, color=C_TEAL, label="写后留存 IMC DRAM 读 (RFO)")
    b2 = ax1.bar(X_POS - 0.5 * w, fill_wr, width=w, color=C_TEAL_L, edgecolor=C_TEAL, lw=0.8, label="写后留存 IMC DRAM 写回 (脏逐出)")
    b3 = ax1.bar(X_POS + 0.5 * w, cold_rd, width=w, color=C_BLUE, label="纯冷源 IMC DRAM 读 (干净读)")
    b4 = ax1.bar(X_POS + 1.5 * w, cold_wr, width=w, color=C_BLUE_L, edgecolor=C_BLUE, lw=0.8, label="纯冷源 IMC DRAM 写回 (近零)")

    for bar, dx, dy, col in [
        (b1[3], -0.05, 0.025, C_TEAL),
        (b2[3],  0.00, 0.090, C_TEAL),
        (b3[3],  0.05, 0.025, C_BLUE),
        (b4[3],  0.02, 0.020, C_BLUE),
    ]:
        h = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width() / 2 + dx, h + dy, f"{h:.2f}", ha="center", va="bottom", fontsize=7.6, color=col, fontweight="bold")

    ax1.axvline(2.45, color=C_RED, ls=":", lw=1.3)
    ax1.text(2.40, 0.85, "60 MiB LLC\n溢出边界", ha="right", fontsize=7.6, color=C_RED)

    ax1.set_xticks(X_POS)
    ax1.set_xticklabels(X_LABELS, fontsize=8.4)
    ax1.set_ylim(0, 1.18)
    ax1.set_ylabel("Socket 1 IMC DRAM 流量（64 B 行 / 消息）", fontsize=9.0)
    ax1.set_title("(a) W=64 顺序留存 PMU：真溢出 DRAM + 100.00% DDIO", fontsize=10.0, pad=8)

    ax1_r = ax1.twinx()
    ax1_r.spines["top"].set_visible(False)
    ax1_r.grid(False)
    ax1_r.plot(X_POS, fill_ddio, "-D", color=C_ORANGE, lw=2.0, ms=5.5, label="DDIO RX 命中率 (io_hit_itom)")
    ax1_r.plot(X_POS, cold_ddio, ":o", color=C_ORANGE, lw=1.4, ms=4.0, alpha=0.75)
    ax1_r.set_ylim(92.0, 101.2)
    ax1_r.set_ylabel("主机 DDIO 命中率（%）", fontsize=9.0, color=C_ORANGE)
    ax1_r.tick_params(axis="y", colors=C_ORANGE)

    # Compute exact card cycles & DDR5 GB/s for fill_seq_128m
    f128_rows = [r for r in rows if r["part"] == "part1_w64" and r["cfg"] == "fill_seq_128m"]
    c_cyc = statistics.mean(r["card_pmu"]["cores"][0]["cpu_cycles"] / r["card_pmu"]["cores"][0]["taken"] for r in f128_rows)
    c_bb = statistics.mean(r["card_pmu"]["cores"][0]["taken"] / r["card_pmu"]["cores"][0]["busy_polls"] for r in f128_rows)
    ddr_gbs = statistics.mean((r["host_pmu"]["imc_cas_rd"] + r["host_pmu"]["imc_cas_wr"]) * 64.0 / r["secs"] / 1e9 for r in f128_rows)

    ax1_r.annotate(
        f"128M 全 12.58M 包 DDIO=100.00%\n(0 次 io_miss_itom；DDR5 仅 {ddr_gbs:.2f} GB/s)\n受单卡核 WC1 封顶 ({c_cyc:.1f} cyc, batch {c_bb:.1f}/32)",
        xy=(3.0, fill_ddio[3]),
        xytext=(0.48, 93.3),
        fontsize=7.1,
        color="#9a3412",
        bbox=dict(boxstyle="round,pad=0.26", fc="#fff7ed", ec=C_ORANGE, lw=0.8),
        arrowprops=dict(arrowstyle="->", color=C_ORANGE, lw=1.0),
    )

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax1_r.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", bbox_to_anchor=(0.01, 0.84), fontsize=7.0, framealpha=0.95)

    # =========================================================================
    # Panel (b): Uncapped unidirectional H2C saturation (seq vs rand)
    # =========================================================================
    h2c_series = [
        ("cold_seq",  "纯冷源 顺序 (direct MOVDIR64B)", C_BLUE,   "-s",  2.2),
        ("fill_seq",  "写后留存 顺序 (写池+MOVDIR64B)",   C_TEAL,   "-o",  2.2),
        ("cold_rand", "纯冷源 随机无预取 (bijective)",    C_BLUE,   "--s", 1.8),
        ("fill_rand", "写后留存 随机无预取 (bijective)",  C_ORANGE, "--^", 1.9),
    ]
    for prefix, label, color, style, lw in h2c_series:
        raw = group_vals(rows, "part3_h2c", prefix, lambda r: r["mops"])
        means = [statistics.mean(v) for v in raw]
        mins = [min(v) for v in raw]
        maxs = [max(v) for v in raw]
        yerr = [[m - lo for m, lo in zip(means, mins)], [hi - m for m, hi in zip(means, maxs)]]
        ax2.errorbar(X_POS, means, yerr=yerr, fmt=style, color=color, lw=lw, ms=5.8, capsize=3.0, label=label, zorder=4)

    w64_ref = statistics.mean(group_vals(rows, "part1_w64", "fill_seq", lambda r: r["mops"])[0])
    ax2.axhline(w64_ref, color=C_SLATE, ls=":", lw=1.35, zorder=2)
    ax2.text(-0.18, w64_ref + 4.5, f"W=64 单卡核 WC1 封顶线 ({w64_ref:.1f} M/s)", fontsize=7.7, color=C_SLATE, fontweight="bold")

    c_seq_128 = statistics.mean(group_vals(rows, "part3_h2c", "cold_seq",  lambda r: r["mops"])[3])
    f_seq_128 = statistics.mean(group_vals(rows, "part3_h2c", "fill_seq",  lambda r: r["mops"])[3])
    c_rnd_128 = statistics.mean(group_vals(rows, "part3_h2c", "cold_rand", lambda r: r["mops"])[3])
    f_rnd_128 = statistics.mean(group_vals(rows, "part3_h2c", "fill_rand", lambda r: r["mops"])[3])
    c_rnd_lm = statistics.mean(group_vals(rows, "part3_h2c", "cold_rand", lambda r: r["host_pmu"]["llc_load_misses"] / r["measured"])[3])
    f_rnd_lm = statistics.mean(group_vals(rows, "part3_h2c", "fill_rand", lambda r: r["host_pmu"]["llc_load_misses"] / r["measured"])[3])

    ax2.text(3.06, c_seq_128,       f"{c_seq_128:.1f} M/s (-0.2%)", fontsize=7.6, color=C_BLUE, va="center")
    ax2.text(3.06, f_seq_128,       f"{f_seq_128:.1f} M/s (-17.5%)\n本地 DDR5+PCIe 19.0 GB/s", fontsize=7.4, color=C_TEAL, va="center", fontweight="bold")
    ax2.text(3.06, c_rnd_128,       f"{c_rnd_128:.1f} M/s\n(LLC miss {c_rnd_lm:.2f})", fontsize=7.4, color=C_BLUE, va="center")
    ax2.text(3.06, f_rnd_128 + 3.0, f"{f_rnd_128:.1f} M/s\n(LLC miss {f_rnd_lm:.2f})", fontsize=7.4, color=C_ORANGE, va="center")

    ax2.axvline(2.45, color=C_RED, ls=":", lw=1.3)
    ax2.text(2.42, 14.0, "60 MiB LLC\n边界", ha="right", fontsize=7.6, color=C_RED)

    ax2.set_xticks(X_POS)
    ax2.set_xticklabels(X_LABELS, fontsize=8.4)
    ax2.set_xlim(-0.25, 4.10)
    ax2.set_ylim(0, 196)
    ax2.set_ylabel("单向 MOVDIR64B 直推吞吐（Mmsg/s，64 B）", fontsize=9.0)
    ax2.set_title("(b) 单向无卡封顶 H2C 探底：128 MiB 暴露纯主机内存上限", fontsize=10.0, pad=8)
    ax2.legend(loc="lower left", fontsize=7.1, framealpha=0.94)

    # =========================================================================
    # Panel (c): W=64 fill_rand 4 MiB PEM0 slow-state bifurcation PMU diagnosis
    # =========================================================================
    bb_rand = [statistics.mean(v) for v in group_vals(rows, "part1_w64", "fill_rand", lambda r: r["card_pmu"]["cores"][0]["taken"] / r["card_pmu"]["cores"][0]["busy_polls"])]
    snp_rand = [statistics.mean(v) for v in group_vals(rows, "part1_w64", "fill_rand", lambda r: r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / r["measured"])]
    lm_rand = [statistics.mean(v) for v in group_vals(rows, "part1_w64", "fill_rand", lambda r: r["host_pmu"]["llc_load_misses"] / r["measured"])]
    ll_rand = [statistics.mean(v) for v in group_vals(rows, "part1_w64", "fill_rand", lambda r: r["host_pmu"]["llc_loads"] / r["measured"])]

    bars_bb = ax3.bar(X_POS, bb_rand, width=0.42, color="#cbd5e1", edgecolor=C_SLATE, lw=1.0, label="卡核 busy_batch（每轮收包数，上限 32）", zorder=2)
    for xi, val in zip(X_POS, bb_rand):
        ax3.text(xi, val + 0.8, f"{val:.1f}", ha="center", va="bottom", fontsize=7.8, color=C_SLATE, fontweight="bold")

    ax3.set_xticks(X_POS)
    ax3.set_xticklabels(X_LABELS, fontsize=8.4)
    ax3.set_ylim(0, 42)
    ax3.set_ylabel("卡侧单核 busy_batch（条 / 轮询）", fontsize=9.0, color=C_SLATE)
    ax3.set_title("(c) W=64 随机留存 4 MiB 慢态分岔诊断（LLC 未溢出）", fontsize=10.0, pad=8)

    ax3_r = ax3.twinx()
    ax3_r.spines["top"].set_visible(False)
    ax3_r.grid(False)
    l_snp = ax3_r.plot(X_POS, snp_rand, "-o", color=C_RED, lw=2.1, ms=6.0, label="卡 TAD snoop / 消息（快态≈1.0）", zorder=5)
    l_ll  = ax3_r.plot(X_POS, ll_rand,  ":s", color=C_ORANGE, lw=1.8, ms=5.2, label="主机 LLC-loads / 消息（L2 miss）", zorder=4)
    l_lm  = ax3_r.plot(X_POS, lm_rand,  "--^", color=C_BLUE, lw=2.0, ms=5.8, label="主机 LLC-load-misses / 消息", zorder=5)
    ax3_r.set_ylim(-0.10, 3.25)
    ax3_r.set_ylabel("计数（次 / 消息）", fontsize=9.0, color=C_RED)
    ax3_r.tick_params(axis="y", colors=C_RED)

    ax3_r.annotate(
        f"4 MiB: LLC miss={lm_rand[1]:.3f}，L2 miss ({ll_rand[1]:.2f}) 扰动节拍\n→ batch {bb_rand[0]:.1f}→{bb_rand[1]:.2f}，tad_snp {snp_rand[0]:.2f}→{snp_rand[1]:.2f}",
        xy=(1.0, snp_rand[1]),
        xytext=(1.05, 2.66),
        fontsize=7.3,
        color="#991b1b",
        bbox=dict(boxstyle="round,pad=0.26", fc="#fef2f2", ec=C_RED, lw=0.8),
        arrowprops=dict(arrowstyle="->", color=C_RED, lw=1.0),
        zorder=6,
    )

    h3, l3 = ax3.get_legend_handles_labels()
    h3r, l3r = ax3_r.get_legend_handles_labels()
    ax3.legend(h3 + h3r, l3 + l3r, loc="center", bbox_to_anchor=(0.48, 0.36), fontsize=6.9, framealpha=0.96)

    # =========================================================================
    # Panel (d): 32 MiB (<60 MiB LLC) warmup-ratio artifact across parts
    # =========================================================================
    parts_32m = [
        ("part3_h2c", "H2C\n热身 4.19M\n(8.0× 池槽)"),
        ("part1_w64", "W=64\n热身 2.10M\n(4.0× 池槽)"),
        ("part4_w16", "W=16\n热身 262K\n(0.5× 池槽)"),
        ("part2_w1",  "W=1\n热身 20K\n(0.04× 池槽)"),
    ]
    x4 = np.arange(len(parts_32m), dtype=float)
    fill_32m_lm = []
    cold_32m_lm = []
    for part, _ in parts_32m:
        f_vals = [r["host_pmu"]["llc_load_misses"] / r["measured"] for r in rows if r["part"] == part and r["cfg"] == "fill_rand_32m"]
        c_vals = [r["host_pmu"]["llc_load_misses"] / r["measured"] for r in rows if r["part"] == part and r["cfg"] == "cold_rand_32m"]
        assert len(f_vals) == 3 and len(c_vals) == 3
        fill_32m_lm.append(statistics.mean(f_vals))
        cold_32m_lm.append(statistics.mean(c_vals))

    bw = 0.34
    bf = ax4.bar(x4 - bw / 2, fill_32m_lm, width=bw, color=C_ORANGE, label="fill_rand_32m (写后留存)")
    bc = ax4.bar(x4 + bw / 2, cold_32m_lm, width=bw, color=C_BLUE,   label="cold_rand_32m (纯冷源)")
    for bars, col in [(bf, "#9a3412"), (bc, "#1d4ed8")]:
        for bar in bars:
            h = bar.get_height()
            ax4.text(bar.get_x() + bar.get_width() / 2, h + 0.018, f"{h:.3f}", ha="center", va="bottom", fontsize=7.6, color=col, fontweight="bold")

    ax4.axvline(1.5, color=C_RED, ls="--", lw=1.25)
    ax4.axvspan(1.5, 3.55, color="#fef2f2", alpha=0.55, zorder=0)
    ax4.text(0.75, 0.72, "热身 ≥ 4× 池槽\n真实 LLC 命中\n(miss ≈ 0)", ha="center", fontsize=7.8, color=C_TEAL,
             bbox=dict(boxstyle="round,pad=0.25", fc="#f0fdfa", ec=C_TEAL, lw=0.8))
    ax4.text(2.50, 0.85, "热身 < 1× 池槽 → 伪 LLC 缺失\n(32 MiB < 60 MiB LLC 仍出现高 miss)", ha="center", fontsize=7.6, color="#991b1b",
             bbox=dict(boxstyle="round,pad=0.25", fc="#fff1f2", ec=C_RED, lw=0.8))

    ax4.set_xticks(x4)
    ax4.set_xticklabels([lbl for _, lbl in parts_32m], fontsize=8.2)
    ax4.set_xlim(-0.55, 3.55)
    ax4.set_ylim(0, 0.98)
    ax4.set_ylabel("主机 LLC-load-misses（次 / 消息）", fontsize=9.0)
    ax4.set_title("(d) 方法学披露：32 MiB 随机池热身覆盖率与伪 LLC 缺失", fontsize=10.0, pad=8)
    ax4.legend(loc="upper left", fontsize=7.5, framealpha=0.95)

    fig.suptitle(
        "MRV-116 硬件 PMU 证据、单核内存上限与非顺序/热身诊断：100% 采样无复用、0 缺页、2 MiB 大页（cpu40 3.90 / uncore 2.20 / 卡 2.00 GHz，n=3）",
        fontsize=9.1,
        y=0.988,
    )
    out = mrvplot.save(fig, "mrv-116-pmu-and-controls")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
