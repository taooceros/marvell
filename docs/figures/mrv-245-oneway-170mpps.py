"""MRV-245 Figure 1: One-way TX full-64 B verified scaling (zero doorbell, zero SFENCE)
comparing standard Linux MT_NORMAL_NC (0x44) u1 vs u4 against Normal-WB across K=1..12
card cores and H=1/2 host lanes, plus symmetric Host CHA TOR safety and Card L2/TAD/DDR5 ledger.

Reads raw verified n=3 runs directly from logs/mrv245-2026-11-27/runs.jsonl.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv245-2026-11-27/runs.jsonl"


def load_runs():
    by = defaultdict(list)
    for ln in RUNS_PATH.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        cp = r["card_pmu"]
        assert r["lost"] == 0 and r["bad_or_torn"] == 0
        assert sum(w["corrupt"] for w in cp["workers"]) == 0
        assert sum(w["partial"] for w in cp["workers"]) == 0
        assert sum(w["db_ahead_empty"] for w in cp["workers"]) == 0
        assert sum(w["db_ahead_partial"] for w in cp["workers"]) == 0
        assert r["host_min_pct"] >= 99.9 and cp["min_pct"] >= 99.9
        by[r["cond"]].append(r)
    return by


def stats(rs, fn):
    vals = [fn(r) for r in rs]
    return statistics.median(vals), min(vals), max(vals)


def card_per_pkt(r, metric):
    cp = r["card_pmu"]
    k = r["card_cores"]
    tak = max(1, sum(w["taken_win"] for w in cp["workers"]))
    if metric == "l2i":
        return sum(cp["core_pmu"][i]["l2d_cache_inval"] for i in range(k)) / tak
    if metric == "snp":
        return cp["tad"]["tad_snp_msh_out_any"]["val"] / tak
    if metric == "ddr":
        return cp["ddr"]["reads_total"] / tak
    raise ValueError(metric)


def main():
    by = load_runs()

    ceil_h1, _, _ = stats(by["tx_none_h1"], lambda r: r["mops"])
    ceil_h2, _, _ = stats(by["tx_none_h2"], lambda r: r["mops"])
    cha_h2_ceil, _, _ = stats(by["tx_none_h2"], lambda r: r["host_pmu"]["cha_inflight"])

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(14.4, 5.8), gridspec_kw={"width_ratios": [1.26, 1.08]}
    )

    # ------------------------------------------------------------------
    # Panel A: Throughput vs Card Cores K
    # ------------------------------------------------------------------
    ax1.axhline(ceil_h2, color="#334155", ls="--", lw=1.3, zorder=1)
    ax1.text(
        0.55, ceil_h2 + 3.0,
        f"H=2 卡静默主机直推基线：{ceil_h2:.1f} M/s ({ceil_h2*64/1000:.2f} GB/s)",
        fontsize=8.3, color="#334155", fontweight="bold",
    )
    ax1.axhline(ceil_h1, color="#64748b", ls=":", lw=1.3, zorder=1)
    ax1.text(
        0.55, ceil_h1 + 2.3,
        f"H=1 单主机核卡静默基线：{ceil_h1:.1f} M/s",
        fontsize=8.1, color="#475569",
    )

    # Symmetric over-provisioned warning zone for K >= 8 on H=1
    cha_nc_u1_k8 = stats(by["tx_nc_u1_h1_k8"], lambda r: r["host_pmu"]["cha_inflight"])[0]
    cha_nc_u4_k8 = stats(by["tx_nc_u4_h1_k8"], lambda r: r["host_pmu"]["cha_inflight"])[0]
    ax1.axvspan(6.6, 12.65, color="#fef2f2", alpha=0.55, zorder=0)
    ax1.text(
        9.65, 18,
        f"卡核超配区（K ≥ 8 对 H=1）\nNC 与 WB 均出现吞吐回落 &\n主机 CHA > 330（NC 达 {cha_nc_u4_k8:.0f}–{cha_nc_u1_k8:.0f}）",
        ha="center", va="bottom", fontsize=7.8, color="#991b1b",
        bbox=dict(boxstyle="round,pad=0.35", fc="#fff1f2", ec="#fca5a5", lw=0.8),
    )

    def plot_series(ks, cond_fmt, label, color, marker, ls="-", lw=2.2, ms=6.8, z=4):
        meds, lo, hi = [], [], []
        for k in ks:
            m, mn, mx = stats(by[cond_fmt.format(k=k)], lambda r: r["mops"])
            meds.append(m)
            lo.append(m - mn)
            hi.append(mx - m)
        ax1.errorbar(
            ks, meds, yerr=[lo, hi], label=label, color=color, marker=marker,
            ls=ls, lw=lw, markersize=ms, capsize=3.2, elinewidth=1.3, zorder=z,
        )
        return dict(zip(ks, meds))

    s_u4_h2 = plot_series(
        [4, 8, 12], "tx_nc_u4_h2_k{k}",
        "MT_NORMAL_NC · u4 (4槽并行, H=2)", "#047857", "D", lw=2.5, ms=7.2, z=6,
    )
    s_u4_h1 = plot_series(
        [1, 2, 3, 4, 8], "tx_nc_u4_h1_k{k}",
        "MT_NORMAL_NC · u4 (4槽并行, H=1)", "#16a34a", "o", lw=2.5, ms=7.2, z=5,
    )
    s_u1_h2 = plot_series(
        [8, 12], "tx_nc_u1_h2_k{k}",
        "MT_NORMAL_NC · u1 (单槽读, H=2)", "#0369a1", "^", ls="--", lw=1.9, ms=6.8, z=4,
    )
    s_u1_h1 = plot_series(
        [1, 2, 4, 8, 12], "tx_nc_u1_h1_k{k}",
        "MT_NORMAL_NC · u1 (单槽读, H=1)", "#0ea5e9", "s", lw=2.0, ms=6.2, z=4,
    )
    s_wb_h1 = plot_series(
        [1, 2, 4, 8], "tx_wb_u1_h1_k{k}",
        "对照：Normal-WB · u1 (H=1)", "#dc2626", "X", ls="-.", lw=1.9, ms=7.0, z=3,
    )

    pct_h2 = s_u4_h2[4] / ceil_h2 * 100.0
    ax1.annotate(
        f"4 核 u4 吃满 H=2 基线\n{s_u4_h2[4]:.1f} M/s ({pct_h2:.1f}%)",
        xy=(4, s_u4_h2[4]), xytext=(5.25, 195.0),
        fontsize=8.2, fontweight="bold", color="#047857",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#a7f3d0", lw=0.7, alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#047857", lw=1.2),
    )
    pct_h1 = s_u4_h1[4] / ceil_h1 * 100.0
    cha_u4_k4 = stats(by["tx_nc_u4_h1_k4"], lambda r: r["host_pmu"]["cha_inflight"])[0]
    ax1.annotate(
        f"4 核 u4 吃满 H=1 基线\n{s_u4_h1[4]:.1f} M/s ({pct_h1:.1f}%) · CHA {cha_u4_k4:.1f}",
        xy=(4, s_u4_h1[4]), xytext=(0.55, 195.0),
        fontsize=8.0, fontweight="bold", color="#15803d",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#bbf7d0", lw=0.7, alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#15803d", lw=1.2, connectionstyle="arc3,rad=-0.10"),
    )
    ax1.annotate(
        f"u1 4.00× 线性\n{s_u1_h1[4]:.1f} M/s",
        xy=(4, s_u1_h1[4]), xytext=(0.6, 134.0),
        fontsize=8.0, color="#0284c7",
        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#bae6fd", lw=0.7, alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#0284c7", lw=1.0, connectionstyle="arc3,rad=0.12"),
    )
    ax1.annotate(
        f"H=2, K=8 u1\n{s_u1_h2[8]:.1f} M/s ({s_u1_h2[8]/ceil_h2*100:.1f}%)",
        xy=(8, s_u1_h2[8]), xytext=(8.95, 195.0),
        fontsize=8.0, color="#0369a1",
        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#bae6fd", lw=0.7, alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#0369a1", lw=1.0),
    )
    ax1.annotate(
        f"WB 单向可达 {s_wb_h1[1]:.0f}–{s_wb_h1[8]:.0f} M/s\n(无 13.4 悬崖，但 K≥2 CHA 达 305–546)",
        xy=(4, s_wb_h1[4]), xytext=(4.35, 73.0),
        fontsize=7.6, color="#b91c1c",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#fecaca", lw=0.7, alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.0),
    )

    ax1.set_xticks([1, 2, 3, 4, 8, 12])
    ax1.set_xlim(0.4, 12.7)
    ax1.set_ylim(0, 224)
    ax1.set_xlabel("卡侧工作核数 K（CN106）", fontsize=10)
    ax1.set_ylabel("单向校验吞吐（Mpps，64 B 全校验）", fontsize=10)
    ax1.set_title("A. 单向直推扩展性：4 核 u4 吃满 H=1 / H=2 卡静默基线", fontsize=10.5, fontweight="bold", pad=8)
    ax1.legend(loc="lower center", bbox_to_anchor=(0.35, 0.02), fontsize=7.7, frameon=True, framealpha=0.95)

    # ------------------------------------------------------------------
    # Panel B: Symmetric Host CHA TOR safety + Card L2/TAD/DDR5 ledger (6 bars!)
    # ------------------------------------------------------------------
    specs = [
        ("tx_nc_u1_h1_k4", "NC u1\nH1, K=4", "#0ea5e9"),
        ("tx_nc_u4_h1_k4", "NC u4 ★\nH1, K=4", "#16a34a"),
        ("tx_nc_u4_h2_k4", "NC u4 ★\nH2, K=4", "#047857"),
        ("tx_nc_u4_h1_k8", "NC u4 超配\nH1, K=8", "#ea580c"),
        ("tx_nc_u1_h1_k8", "NC u1 超配\nH1, K=8", "#dc2626"),
        ("tx_wb_u1_h1_k4", "Normal-WB\nH1, K=4", "#991b1b"),
    ]
    x = np.arange(len(specs))
    chas, cha_lo, cha_hi = [], [], []
    mops_l, l2i_l, snp_l = [], [], []
    for cond, _, _ in specs:
        c_m, c_mn, c_mx = stats(by[cond], lambda r: r["host_pmu"]["cha_inflight"])
        chas.append(c_m)
        cha_lo.append(c_m - c_mn)
        cha_hi.append(c_mx - c_m)
        mops_l.append(stats(by[cond], lambda r: r["mops"])[0])
        l2i_l.append(stats(by[cond], lambda r: card_per_pkt(r, "l2i"))[0])
        snp_l.append(stats(by[cond], lambda r: card_per_pkt(r, "snp"))[0])

    bars = ax2.bar(
        x, chas, width=0.58, color=[s[2] for s in specs], alpha=0.88,
        yerr=[cha_lo, cha_hi], capsize=3.2, error_kw={"elinewidth": 1.2, "ecolor": "#1e293b"},
        zorder=3,
    )
    ax2.axhline(330, color="#dc2626", ls="--", lw=1.5, zorder=2)
    ax2.text(
        -0.44, 348,
        "Socket 1 邻居拥塞危险线\n(CHA TOR ≈ 330, MRV-230)",
        fontsize=7.9, color="#b91c1c", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.22", fc="#fff1f2", ec="#fca5a5", lw=0.7, alpha=0.95),
    )
    ax2.axhline(cha_h2_ceil, color="#475569", ls=":", lw=1.1, zorder=2)
    ax2.text(
        -0.44, cha_h2_ceil + 8,
        f"H=2 卡静默基线 ({cha_h2_ceil:.0f})",
        fontsize=7.6, color="#475569",
    )

    for i, (b, c_m, mp, l2, sn) in enumerate(zip(bars, chas, mops_l, l2i_l, snp_l)):
        top = c_m + cha_hi[i]
        badge = f"CHA {c_m:.0f}\n{mp:.1f}M/s\nL2 {l2:.2f}\n嗅探 {sn:.2f}"
        y_pos = top + 16
        if i == 2:
            y_pos = top + 30
        elif i == 4:
            y_pos = top + 14
        ax2.text(
            b.get_x() + b.get_width() / 2, y_pos, badge,
            ha="center", va="bottom", fontsize=7.1, color="#0f172a",
            linespacing=1.16,
            bbox=dict(boxstyle="round,pad=0.20", fc="white", ec="#cbd5e1", alpha=0.95, lw=0.6),
        )

    ax2.set_xticks(x)
    ax2.set_xticklabels([s[1] for s in specs], fontsize=8.0)
    ax2.set_xlim(-0.55, 5.55)
    ax2.set_ylim(0, 700)
    ax2.set_ylabel("主机 CHA TOR 在途占用（均值条目）", fontsize=10)
    ax2.set_title("B. 主机 CHA 水位与卡侧账本（NC 超配同样越过 330 线）", fontsize=10.5, fontweight="bold", pad=8)

    fig.suptitle(
        "MRV-245 · 标准 Linux MT_NORMAL_NC (0x44) 单向直推（零改 MAIR_EL1、零门铃、零 SFENCE）",
        fontsize=12.4, fontweight="bold", y=0.99,
    )
    fig.text(
        0.5, 0.01,
        "条件：BAR4 槽 15 (128 B 步长, ring=8192, W=6144, credit_step=128)，全 64 B 校验，全程 DDR5 读=0.00；cpu40 3.90 GHz (cpu41 schedutil 2.2–3.9 GHz)，uncore 2.20 GHz；n=3 中位数与 min–max",
        ha="center", fontsize=8.0, color="#475569",
    )
    fig.tight_layout(rect=[0, 0.035, 1, 0.945])
    png = mrvplot.HERE / "mrv-245-oneway-170mpps.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-245-oneway-170mpps.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
