"""MRV-230 Figure 2: On-chip causal ledger proving why pure DWQ-full dropping
(142 Mpps, 94.6% drop) spares co-located neighbors, whereas combining portal
flood + active 64 KiB DMA overflows CHA TOR (max_box 22.7-22.8) while leaving
22-28 GB/s of DDR5 memory bandwidth unused.

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

    # 7 regimes for Panel A (IMC vs STREAM)
    specs_a = [
        ("m230", "l1-spin_8c_b512", "8核空转\nl1-spin"),
        ("m230", "noop-stride_8c_b512", "NOOP 8核\n143M 0丢"),
        ("m230", "memmove-64k-stride_x64B_8c_b512", "64B 8核\n142M 95%丢"),
        ("m230", "memmove-64k-credit_8c_b512", "64K 守信用\n0.3M 0丢"),
        ("m230", "memmove-64k-stride_x64K_cc_8c_b512", "64K 写L3\n117M 99.9%丢"),
        ("m230", "memmove-64k-stride_8c_b512", "64K 写主存\nb=512"),
        ("m230", "memmove-64k-stride_8c_b0", "64K 写主存\nb=0 无fence"),
        ("m196", "bar4_8c_max_b512", "CN106 BAR4\n8核无控"),
    ]

    imc_vals, bw_vals = [], []
    for src, key, _ in specs_a:
        ds = m230 if src == "m230" else m196
        rs = ds[f"t6c__{key}"]
        imc_vals.append(med([r["imc"]["tot_gb_s"] for r in rs]))
        bw_vals.append(med([r["stream"]["mean_gb_s"] for r in rs]))

    # 6 regimes for Panel B (CHA TOR decomposition using exact wcilf + all passes on t6c)
    specs_b = [
        ("l1-spin_8c_b512", None, "8核空转\nl1-spin"),
        ("noop-stride_8c_b512", "noop-stride_8c_b512_wcilf", "NOOP 8核\n143M 0丢"),
        ("memmove-64k-stride_x64B_8c_b512", "memmove-64k-stride_x64B_8c_b512_wcilf", "64B 8核\n95%丢"),
        ("memmove-64k-credit_8c_b512", "memmove-64k-credit_8c_b512_wcilf", "64K 守信用\n0丢"),
        ("memmove-64k-stride_x64K_cc_8c_b512", "memmove-64k-stride_x64K_cc_8c_b512_wcilf", "64K 写L3\n99.9%丢"),
        ("memmove-64k-stride_8c_b512", "memmove-64k-stride_8c_b512_wcilf", "64K 写主存\nb=512"),
        ("memmove-64k-stride_8c_b0", "memmove-64k-stride_8c_b0_wcilf", "64K 写主存\nb=0"),
    ]

    wcilf_vals, nonw_vals, maxb_vals, tot_cha_vals = [], [], [], []
    for k_all, k_wc, _ in specs_b:
        ra = m230[f"t6c__{k_all}"]
        tot = med([r["cha"]["occ_entries"] for r in ra])
        maxb = med([r["cha"]["occ_max_box"] for r in ra])
        if k_wc is None:
            wc = 0.0
        else:
            rw = m230[f"t6c__{k_wc}"]
            wc = med([r["cha"]["occ_entries"] for r in rw])
        wcilf_vals.append(wc)
        nonw_vals.append(max(0.0, tot - wc))
        maxb_vals.append(maxb)
        tot_cha_vals.append(tot)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.4, 5.9), gridspec_kw={"width_ratios": [1.08, 1.0]})

    # ---- Panel A: DDR5 IMC vs STREAM BW ----
    x1 = np.arange(len(specs_a))
    w = 0.37
    ax1.axhline(63.7, color="#64748b", linestyle="--", lw=1.3, zorder=1)
    ax1.text(7.42, 65.4, "双通道 DDR5 上限 (~63.7 GB/s)", fontsize=8.4, color="#475569", fontweight="bold", ha="right")

    ax1.axvspan(3.55, 7.45, color="#fef2f2", alpha=0.55, zorder=0)
    ax1.axvline(3.55, color="#dc2626", linestyle=":", lw=1.0, alpha=0.7)

    b_imc = ax1.bar(x1 - w / 2, imc_vals, width=w, color="#94a3b8", alpha=0.90, label="DDR5 总线实际总流量 (IMC 读+写)", zorder=3)
    bw_cols = ["#1a7f37", "#1a7f37", "#1a7f37", "#d97706", "#ea580c", "#dc2626", "#b91c1c", "#881337"]
    b_bw = ax1.bar(x1 + w / 2, bw_vals, width=w, color=bw_cols, alpha=0.92, label="邻居 6 核 STREAM 带宽", zorder=3)

    for i in range(len(specs_a)):
        ax1.text(x1[i] - w / 2, imc_vals[i] + 1.0, f"{imc_vals[i]:.1f}", ha="center", va="bottom", fontsize=7.9, color="#475569", fontweight="bold")
        ax1.text(x1[i] + w / 2, bw_vals[i] + 1.0, f"{bw_vals[i]:.1f}", ha="center", va="bottom", fontsize=8.0, color=bw_cols[i], fontweight="bold")

    ax1.annotate(
        "内存通道还空着 22–28 GB/s，\n邻居带宽却已暴跌 41%–48%！",
        xy=(5.0, 41.8), xytext=(5.55, 55.2), ha="center", va="center",
        fontsize=8.5, fontweight="bold", color="#991b1b",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#dc2626", alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.2),
    )

    ax1.set_ylim(0, 74)
    ax1.set_xticks(x1)
    ax1.set_xticklabels([s[2] for s in specs_a], fontsize=8.2)
    ax1.set_ylabel("带宽 (GB/s)", fontsize=9.8)
    ax1.set_title("A. 内存总线拆账：64K 灌爆掉速不是 DDR5 不够，而是被片上卡死", fontsize=10.5, fontweight="bold", pad=10)
    ax1.legend(loc="lower left", fontsize=8.3, framealpha=0.95)

    # ---- Panel B: CHA TOR stacked (WCiLF + non-WCiLF) + max_box ----
    x2 = np.arange(len(specs_b))
    ax2.axvspan(3.55, 6.45, color="#fef2f2", alpha=0.55, zorder=0)
    ax2.axvline(3.55, color="#dc2626", linestyle=":", lw=1.0, alpha=0.7)

    ax2.bar(x2, wcilf_vals, width=0.54, color="#2563eb", alpha=0.86, label="门户写入占位 (CHA WCiLF)", zorder=3)
    ax2.bar(x2, nonw_vals, bottom=wcilf_vals, width=0.54, color="#f59e0b", alpha=0.84, label="活跃 DMA + 邻居访存占位 (非 WCiLF)", zorder=3)

    for i in range(len(specs_b)):
        # Place total CHA cleanly inside top of orange bar so it never collides with red max_box curve
        ax2.text(x2[i], tot_cha_vals[i] - 24, f"{tot_cha_vals[i]:.0f}", ha="center", va="top",
                 fontsize=8.3, fontweight="bold", color="#1e293b", zorder=4)
        if wcilf_vals[i] > 50:
            ax2.text(x2[i], wcilf_vals[i] / 2, f"{wcilf_vals[i]:.0f}", ha="center", va="center",
                     fontsize=8.0, fontweight="bold", color="white", zorder=4)

    ax2.set_ylim(0, 860)
    ax2.set_xticks(x2)
    ax2.set_xticklabels([s[2] for s in specs_b], fontsize=8.2)
    ax2.set_ylabel("32 颗 CHA TOR 合计排队槽位", fontsize=9.8)
    ax2.set_title("B. 片上 CHA TOR 拆账：门户 WCiLF 与活跃 DMA 叠加推高总占位与单盒峰值", fontsize=10.2, fontweight="bold", pad=10)

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.spines["right"].set_visible(True)
    ax2_r.plot(x2, maxb_vals, color="#991b1b", marker="D", lw=2.0, markersize=6.2, label="最忙单盒 CHA 峰值 (max_box)", zorder=5)
    for i, mb in enumerate(maxb_vals):
        if i in (0, 3):
            dy, va = 0.85, "bottom"
        elif i in (1, 2):
            dy, va = -1.35, "top"
        else:
            dy, va = 0.85, "bottom"
        ax2_r.text(x2[i], mb + dy, f"{mb:.1f}", ha="center", va=va, fontsize=8.3, fontweight="bold", color="#991b1b", zorder=6)

    ax2_r.set_ylim(0, 27.2)
    ax2_r.set_ylabel("最忙单盒 CHA 排队槽位 (max_box)", fontsize=9.5, color="#991b1b")
    ax2_r.grid(False)

    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2_r.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=7.9, framealpha=0.95)

    fig.suptitle(
        "MRV-230 因果账本（6 核 STREAM 共置）：64K 灌爆在 DDR5 未满时即暴跌，伴随 CHA 合计与单盒峰值双升",
        fontsize=11.6, fontweight="bold", y=0.975,
    )
    fig.tight_layout(rect=[0.0, 0.01, 1.0, 0.935])
    png = mrvplot.HERE / "mrv-230-causal-ledger.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-230-causal-ledger.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
