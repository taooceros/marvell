"""MRV-230 Figure 2: On-chip causal ledger showing (A) DDR5 IMC vs 6c STREAM
bandwidth (22-28 GB/s DDR5 left idle during 64K flood collapse), and (B) pure
aggressor-dominated CHA TOR occupancy from DRAM-chase rounds (victim adds only
~1.3 slots, removing the 208-slot STREAM baseline confounding) against the
unified 330-430 CHA TOR cliff band, overlaid with 6c STREAM IMC RPQ starvation.

Reads raw verified n=3 runs directly from:
  - logs/mrv230-2026-11-27/runs.jsonl
  - logs/mrv230-2026-11-27/diag_runs.jsonl
  - logs/mrv196-2026-11-25/runs.jsonl
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
M230_MAIN = ROOT / "logs/mrv230-2026-11-27/runs.jsonl"
M230_DIAG = ROOT / "logs/mrv230-2026-11-27/diag_runs.jsonl"
M196_MAIN = ROOT / "logs/mrv196-2026-11-25/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def load_verified(paths):
    by = defaultdict(list)
    for p in paths:
        for ln in p.read_text().splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            assert r["accepted"] is True and r["host_pmu_min_pct"] == 100.0
            assert r["page_faults"] == 0 and r["victim_page_faults"] == 0 and r["aggr_page_faults"] == 0
            if "drain_ok" in r:
                assert r["drain_ok"] is True and r["dsa_verify_errors"] == 0 and r["dsa2_pmu"]["cpl_err"] == 0
            if r["stream"]:
                assert r["stream"]["verify_errors"] == 0
            if r["chase"]:
                assert r["chase"]["bad_nodes"] == 0
            by[r["label"]].append(r)
    return by


def main():
    m230 = load_verified([M230_MAIN, M230_DIAG])
    m196 = load_verified([M196_MAIN])

    # 8 aligned regimes for both Panel A (t6c IMC vs STREAM) and Panel B (dram CHA cliff vs t6c IMC RPQ)
    specs = [
        ("m230", "l1-spin_8c_b512", "8核空转\nl1-spin"),
        ("m230", "noop-stride_8c_b512", "NOOP 8核\n143M 0丢"),
        ("m230", "memmove-64k-stride_x64B_8c_b512", "64B 8核\n142M 95%丢"),
        ("m230", "memmove-64k-credit_8c_b512", "64K 守信用\n0.3M 0丢"),
        ("m230", "memmove-64k-stride_x64K_cc_8c_b512", "64K 写L3\n117M 99.9%丢"),
        ("m230", "memmove-64k-stride_8c_b512", "64K 写主存\nb=512"),
        ("m230", "memmove-64k-stride_8c_b0", "64K 写主存\nb=0 无fence"),
        ("m196", "bar4_8c_max_b512", "CN106 BAR4\n8核无控"),
    ]

    imc_vals, bw_vals, rpq_vals, dram_cha_vals = [], [], [], []
    for src, key, _ in specs:
        ds = m230 if src == "m230" else m196
        rs_t = ds[f"t6c__{key}"]
        rs_d = ds[f"dram__{key}"]
        imc_vals.append(med([r["imc"]["tot_gb_s"] for r in rs_t]))
        bw_vals.append(med([r["stream"]["mean_gb_s"] for r in rs_t]))
        rpq_vals.append(med([r["imc"]["rpq_entries"] for r in rs_t]))
        dram_cha_vals.append(med([r["cha"]["occ_entries"] for r in rs_d]))

    cc_llc_cha = med([r["cha"]["occ_entries"] for r in m230["llc__memmove-64k-stride_x64K_cc_8c_b512"]])
    wcilf_noop = med([r["cha"]["occ_entries"] for r in m230["t6c__noop-stride_8c_b512_wcilf"]])
    wcilf_64b = med([r["cha"]["occ_entries"] for r in m230["t6c__memmove-64k-stride_x64B_8c_b512_wcilf"]])
    wcilf_64k = med([r["cha"]["occ_entries"] for r in m230["t6c__memmove-64k-stride_8c_b512_wcilf"]])
    wcilf_64k_b0 = med([r["cha"]["occ_entries"] for r in m230["t6c__memmove-64k-stride_8c_b0_wcilf"]])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.6, 6.2), gridspec_kw={"width_ratios": [1.0, 1.06]})

    # ---- Panel A: DDR5 IMC vs STREAM BW ----
    x = np.arange(len(specs))
    w = 0.37
    ax1.axhline(63.7, color="#64748b", linestyle="--", lw=1.3, zorder=1)
    ax1.text(7.42, 65.4, "双通道 DDR5 上限 (~63.7 GB/s)", fontsize=8.4, color="#475569", fontweight="bold", ha="right")

    ax1.axvspan(3.55, 7.45, color="#fef2f2", alpha=0.55, zorder=0)
    ax1.axvline(3.55, color="#dc2626", linestyle=":", lw=1.0, alpha=0.7)

    ax1.bar(x - w / 2, imc_vals, width=w, color="#94a3b8", alpha=0.90, label="DDR5 总线实际总流量 (IMC 读+写)", zorder=3)
    bw_cols = ["#1a7f37", "#1a7f37", "#1a7f37", "#d97706", "#ea580c", "#dc2626", "#b91c1c", "#881337"]
    ax1.bar(x + w / 2, bw_vals, width=w, color=bw_cols, alpha=0.92, label="邻居 6 核 STREAM 带宽", zorder=3)

    for i in range(len(specs)):
        ax1.text(x[i] - w / 2, imc_vals[i] + 1.0, f"{imc_vals[i]:.1f}", ha="center", va="bottom", fontsize=7.9, color="#475569", fontweight="bold")
        ax1.text(x[i] + w / 2, bw_vals[i] + 1.0, f"{bw_vals[i]:.1f}", ha="center", va="bottom", fontsize=8.0, color=bw_cols[i], fontweight="bold")

    ax1.annotate(
        "内存通道还空着 22–28 GB/s，\n邻居带宽却已暴跌 41%–48%！",
        xy=(5.0, 41.8), xytext=(5.55, 55.2), ha="center", va="center",
        fontsize=8.4, fontweight="bold", color="#991b1b",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#dc2626", alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.2),
    )

    ax1.set_ylim(0, 74)
    ax1.set_xticks(x)
    ax1.set_xticklabels([s[2] for s in specs], fontsize=8.0)
    ax1.set_ylabel("带宽 (GB/s)", fontsize=9.8)
    ax1.set_title("A. 内存总线拆账（6 核 STREAM 轮）：不是 DDR5 不够，而是被片上卡死", fontsize=10.2, fontweight="bold", pad=10)
    ax1.legend(loc="lower left", fontsize=8.2, framealpha=0.95)

    # ---- Panel B: Pure-aggressor CHA TOR (DRAM round) vs 330-430 cliff + t6c IMC RPQ starvation ----
    ax2.axvspan(3.55, 7.45, color="#fef2f2", alpha=0.45, zorder=0)
    ax2.axvline(3.55, color="#dc2626", linestyle=":", lw=1.0, alpha=0.7)
    ax2.axhspan(330, 430, color="#fef3c7", alpha=0.78, zorder=1)
    ax2.axhline(330, color="#d97706", linestyle="--", lw=1.0, alpha=0.85, zorder=2)
    ax2.axhline(430, color="#d97706", linestyle="--", lw=1.0, alpha=0.85, zorder=2)
    ax2.text(
        -0.38, 380, "跨通路起伤悬崖区\n(330–430 槽)",
        ha="left", va="center", fontsize=8.1, fontweight="bold", color="#92400e",
        bbox=dict(boxstyle="round,pad=0.2", facecolor="#fffbeb", edgecolor="#f59e0b", alpha=0.92),
        zorder=4,
    )

    cha_cols = ["#94a3b8", "#2563eb", "#2563eb", "#d97706", "#ea580c", "#dc2626", "#b91c1c", "#881337"]
    ax2.bar(x, dram_cha_vals, width=0.52, color=cha_cols, alpha=0.84, label="纯攻击方 CHA TOR 总占位（主存追逐轮，邻居仅 ~1.3 槽）", zorder=3)

    for i, cv in enumerate(dram_cha_vals):
        if i in (1, 2):
            # Keep 320/313 cleanly inside top of bar so the 330 dashed cliff line stays crisp above them
            ax2.text(x[i], cv - 14, f"{cv:.0f}", ha="center", va="top", fontsize=8.4, fontweight="bold", color="white", zorder=5)
        else:
            ax2.text(x[i], cv + 12, f"{cv:.0f}", ha="center", va="bottom", fontsize=8.4, fontweight="bold", color="#1e293b", zorder=5)

    # Highlight the unified cliff proof on 64K-CC: llc round stays at 317 (<330, safe) while dram round jumps to 472 (>430, cliff)
    ax2.scatter([4], [cc_llc_cha], s=72, marker="o", facecolor="white", edgecolor="#1d4ed8", linewidth=2.0,
                label=f"64K 写L3 在 L3 追逐轮仅 {cc_llc_cha:.0f} 槽 (<330，源留 L3 不掉崖)", zorder=6)
    ax2.annotate(
        f"L3 轮 {cc_llc_cha:.0f}\n(<330 安全)",
        xy=(4.0, cc_llc_cha), xytext=(2.98, 292), ha="center", va="center",
        fontsize=7.6, fontweight="bold", color="#1d4ed8",
        bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#3b82f6", alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.1),
        zorder=7,
    )
    ax2.set_ylim(0, 810)
    ax2.set_xticks(x)
    ax2.set_xticklabels([s[2] for s in specs], fontsize=8.0)
    ax2.set_ylabel("32 颗 CHA TOR 纯攻击方占位槽位（主存轮）", fontsize=9.6)
    ax2.set_title("B. 跨通路统一 CHA 悬崖（330–430 槽）与邻居内存读队列 (RPQ) 塌缩", fontsize=10.2, fontweight="bold", pad=10)

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.spines["right"].set_visible(True)
    ax2_r.plot(x, rpq_vals, color="#991b1b", marker="D", lw=2.1, markersize=6.0,
               label="6 核 STREAM 轮内存读队列 IMC RPQ 槽位（右轴，越低越饿死）", zorder=6)
    for i, rv in enumerate(rpq_vals):
        if i <= 3:
            ax2_r.text(x[i], rv + 2.4, f"{rv:.1f}", ha="center", va="bottom",
                       fontsize=8.2, fontweight="bold", color="#991b1b", zorder=7)
        else:
            ax2_r.text(x[i], rv - 3.6, f"{rv:.1f}", ha="center", va="top",
                       fontsize=8.2, fontweight="bold", color="#991b1b",
                       bbox=dict(boxstyle="round,pad=0.14", facecolor="white", edgecolor="none", alpha=0.86),
                       zorder=7)

    ax2_r.set_ylim(0, 74)
    ax2_r.set_ylabel("6 核 STREAM 轮内存读队列 IMC RPQ 槽位", fontsize=9.4, color="#991b1b")
    ax2_r.grid(False)

    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2_r.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=7.6, framealpha=0.95)

    fig.suptitle(
        "MRV-230 因果账本：4 KiB 窄窗将纯洪峰锁在 313–320 槽悬崖下；跨过 430 槽即饿死邻居 IMC 读队列",
        fontsize=11.5, fontweight="bold", y=0.972,
    )
    fig.text(
        0.5, 0.012,
        f"注：Panel B 柱高取主存追逐轮 CHA 总占位（剥离 6 核 STREAM 自身 ~208 槽基线，避免堆叠柱视觉失真）；6 核 STREAM 轮实测 WCiLF 拆账为 NOOP {wcilf_noop:.0f} / 64B {wcilf_64b:.0f} / 64K {wcilf_64k:.0f}–{wcilf_64k_b0:.0f} 槽（最忙单盒 19.0–19.4 → 22.7–22.8）。\n当攻击方 CHA 跨过 330–430 悬崖（472–489；b=128 达 499；CN106 宽窗达 654），邻居 STREAM 读请求无法入队，IMC RPQ 从 45.5 塌缩至 18.3–22.1（CN106 降至 8.2）。",
        ha="center", va="bottom", fontsize=7.9, color="#57606a",
    )
    fig.tight_layout(rect=[0.0, 0.085, 1.0, 0.935])
    png = mrvplot.HERE / "mrv-230-causal-ledger.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-230-causal-ledger.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
