#!/usr/bin/env python3
"""Plot MRV-116 hardware PMU proof and pure cold-source ceiling controls:
  (a) W=64 Socket-1 IMC DRAM read/write CAS per msg + DDIO io_hit_itom rate (100.00%)
  (b) Pure unidirectional MOVDIR64B -> BAR4 saturation (H2C, ~155-175 M/s) across
      seq vs rand (defeating hardware prefetcher) + W=16 cliff-free random control.

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
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.2, 4.8))

    # --- Panel (a): W=64 IMC DRAM read/write + DDIO hit rate ---
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

    # Stagger top labels cleanly on the 128 MiB cluster so digits never touch
    label_specs = [
        (b1[3], -0.05, 0.025, C_TEAL),
        (b2[3],  0.00, 0.085, C_TEAL),
        (b3[3],  0.05, 0.025, C_BLUE),
        (b4[3],  0.02, 0.020, C_BLUE),
    ]
    for bar, dx, dy, col in label_specs:
        h = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width() / 2 + dx, h + dy, f"{h:.2f}", ha="center", va="bottom", fontsize=7.6, color=col, fontweight="bold")

    ax1.axvline(2.45, color=C_RED, ls=":", lw=1.3)
    ax1.text(2.40, 0.84, "60 MiB LLC\n溢出边界", ha="right", fontsize=7.8, color=C_RED)

    ax1.set_xticks(X_POS)
    ax1.set_xticklabels(X_LABELS, fontsize=8.8)
    ax1.set_ylim(0, 1.18)
    ax1.set_ylabel("Socket 1 IMC DRAM 流量（64 B 行 / 消息）", fontsize=9.3)
    ax1.set_title("(a) W=64 PMU 证据：128 MiB 真溢出至 DRAM，DDIO 仍 100.00%", fontsize=10.1, pad=8)

    ax1_r = ax1.twinx()
    ax1_r.spines["top"].set_visible(False)
    ax1_r.grid(False)
    ax1_r.plot(X_POS, fill_ddio, "-D", color=C_ORANGE, lw=2.0, ms=5.5, label="DDIO RX 命中率 (io_hit_itom)")
    ax1_r.plot(X_POS, cold_ddio, ":o", color=C_ORANGE, lw=1.4, ms=4.0, alpha=0.75)
    ax1_r.set_ylim(92.0, 101.2)
    ax1_r.set_ylabel("主机 DDIO 命中率（%）", fontsize=9.3, color=C_ORANGE)
    ax1_r.tick_params(axis="y", colors=C_ORANGE)
    ax1_r.annotate(
        "全部 12.58M 包\nDDIO = 100.00%\n(0 次 io_miss_itom)",
        xy=(3.0, fill_ddio[3]),
        xytext=(1.18, 94.0),
        fontsize=7.9,
        color="#9a3412",
        bbox=dict(boxstyle="round,pad=0.28", fc="#fff7ed", ec=C_ORANGE, lw=0.8),
        arrowprops=dict(arrowstyle="->", color=C_ORANGE, lw=1.0),
    )

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax1_r.get_legend_handles_labels()
    # Place legend cleanly in empty middle-left area below the 100% DDIO line
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", bbox_to_anchor=(0.01, 0.83), fontsize=7.2, framealpha=0.95)

    # --- Panel (b): Pure unidirectional H2C saturation (seq vs rand) ---
    h2c_series = [
        ("cold_seq", "纯冷源 顺序 (direct MOVDIR64B)", C_BLUE, "-s", 2.2),
        ("fill_seq", "写后留存 顺序 (写池+MOVDIR64B)", C_TEAL, "-o", 2.2),
        ("cold_rand", "纯冷源 随机无预取 (bijective rand)", C_BLUE, "--s", 1.8),
        ("fill_rand", "写后留存 随机无预取 (bijective rand)", C_ORANGE, "--^", 1.9),
    ]
    for prefix, label, color, style, lw in h2c_series:
        raw = group_vals(rows, "part3_h2c", prefix, lambda r: r["mops"])
        means = [statistics.mean(v) for v in raw]
        mins = [min(v) for v in raw]
        maxs = [max(v) for v in raw]
        yerr = [[m - lo for m, lo in zip(means, mins)], [hi - m for m, hi in zip(means, maxs)]]
        ax2.errorbar(X_POS, means, yerr=yerr, fmt=style, color=color, lw=lw, ms=6.0, capsize=3.0, label=label, zorder=4)

    # Reference line: W=64 closed-loop NIC rate (~47.5 M/s) — label placed cleanly ABOVE the line
    w64_ref = statistics.mean(group_vals(rows, "part1_w64", "fill_seq", lambda r: r["mops"])[0])
    ax2.axhline(w64_ref, color=C_SLATE, ls=":", lw=1.3, zorder=2)
    ax2.text(-0.18, w64_ref + 4.5, f"W=64 闭环吞吐线 ({w64_ref:.1f} M/s)", fontsize=7.9, color=C_SLATE)

    # Direct labels at 128 MiB
    c_seq_128 = statistics.mean(group_vals(rows, "part3_h2c", "cold_seq", lambda r: r["mops"])[3])
    f_seq_128 = statistics.mean(group_vals(rows, "part3_h2c", "fill_seq", lambda r: r["mops"])[3])
    c_rnd_128 = statistics.mean(group_vals(rows, "part3_h2c", "cold_rand", lambda r: r["mops"])[3])
    f_rnd_128 = statistics.mean(group_vals(rows, "part3_h2c", "fill_rand", lambda r: r["mops"])[3])

    ax2.text(3.06, c_seq_128, f"{c_seq_128:.1f} M/s\n(-0.2%)", fontsize=7.8, color=C_BLUE, va="center")
    ax2.text(3.06, f_seq_128, f"{f_seq_128:.1f} M/s\n(-17.5%)\n单核DRAM上限", fontsize=7.6, color=C_TEAL, va="center")
    ax2.text(3.06, c_rnd_128, f"{c_rnd_128:.1f} M/s\n(LLC miss 0.59)", fontsize=7.6, color=C_BLUE, va="center")
    ax2.text(3.06, f_rnd_128 + 3.0, f"{f_rnd_128:.1f} M/s\n(LLC miss 0.44)", fontsize=7.6, color=C_ORANGE, va="center")

    ax2.axvline(2.45, color=C_RED, ls=":", lw=1.3)
    ax2.text(2.42, 16.0, "60 MiB LLC\n边界", ha="right", fontsize=7.8, color=C_RED)

    ax2.set_xticks(X_POS)
    ax2.set_xticklabels(X_LABELS, fontsize=8.8)
    ax2.set_xlim(-0.25, 3.85)
    ax2.set_ylim(0, 196)
    ax2.set_ylabel("单向 MOVDIR64B 直推吞吐（Mmsg/s，64 B）", fontsize=9.3)
    ax2.set_title("(b) 单向直推满速探底（175 M/s）：仅在 128 MiB 暴露纯源读取上限", fontsize=10.2, pad=8)
    ax2.legend(loc="lower left", fontsize=7.4, framealpha=0.94)

    fig.suptitle(
        "MRV-116 硬件 PMU 与源内存上限验证：100% 采样无复用、0 缺页、2 MiB 大页（Socket 1，cpu40 3.90 GHz / uncore 2.20 GHz，n=3）",
        fontsize=9.4,
        y=0.99,
    )
    out = mrvplot.save(fig, "mrv-116-pmu-and-controls")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
