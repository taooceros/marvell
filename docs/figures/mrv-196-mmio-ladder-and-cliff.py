"""MRV-196 Figure 1: BAR4 MMIO rate ladder (>= 150 Mpps steady-state zero interference)
vs multi-core unpaced CHA TOR backpressure cliff (SFENCE batch 512 vs 128) and
software-paced recovery (8c @ 165 Mpps).

Reads raw verified n=3 runs directly from `logs/mrv196-2026-11-25/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv196-2026-11-25/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def load_verified():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    by_lbl = defaultdict(list)
    for r in rows:
        assert r["accepted"] is True
        assert r["host_pmu_min_pct"] == 100.0
        assert r["page_faults"] == 0
        assert r["victim_page_faults"] == 0
        assert r["aggr_page_faults"] == 0
        assert r["ram_verify_errors"] == 0
        if r["chase"]:
            assert r["chase"]["bad_nodes"] == 0
        if r["stream"]:
            assert r["stream"]["verify_errors"] == 0
        if r["aggr"] == "bar4":
            assert r["card_pmu"]["min_pct"] == 100.0
            cv = r["card_verify"]
            assert cv["zero_lines"] == 0 and cv["torn_lines"] == 0 and cv["bad_lines"] == 0
        by_lbl[r["label"]].append(r)
    return by_lbl


def deg_pct(by_lbl, vtag, aggr):
    """Positive % = degradation (higher latency or lower bandwidth)."""
    base_items = by_lbl[f"{vtag}__idle"]
    items = by_lbl[f"{vtag}__{aggr}"]
    assert len(items) == 3, (vtag, aggr, len(items))
    if items[0]["chase"]:
        b = med([r["chase"]["net_mean_ns"] for r in base_items])
        vals = [(r["chase"]["net_mean_ns"] - b) / b * 100.0 for r in items]
    else:
        b = med([r["stream"]["mean_gb_s"] for r in base_items])
        vals = [(b - r["stream"]["mean_gb_s"]) / b * 100.0 for r in items]
    return med(vals), min(vals), max(vals)


def main():
    by_lbl = load_verified()

    stages = [
        ("idle", "0 Mpps\n(静默)"),
        ("l1spin_8c", "8核纯算\n(核频对照)"),
        ("bar4_1c_50m_b512", "1核\n50 Mpps"),
        ("bar4_1c_100m_b512", "1核\n100 Mpps"),
        ("bar4_1c_160m_b512", "1核\n160 Mpps"),
        ("bar4_1c_max_b512", "1核满速\n~175 Mpps"),
        ("bar4_2c_max_b512", "2核满速\nb=512"),
        ("bar4_4c_max_b512", "4核满速\nb=512"),
        ("bar4_6c_max_b512", "6核满速\nb=512"),
        ("bar4_8c_max_b512", "8核满速\nb=512"),
        ("bar4_8c_max_b128", "8核满速\nb=128"),
        ("bar4_8c_165m_b512", "8核限速165\nb=512"),
        ("bar4_8c_165m_b128", "8核限速165\nb=128"),
    ]
    x = np.arange(len(stages))
    xlabels = [s[1] for s in stages]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(15.4, 8.8), sharex=True, gridspec_kw={"height_ratios": [1.18, 1.0]}
    )

    for ax in (ax1, ax2):
        ax.axvspan(-0.45, 5.45, color="#dafbe1", alpha=0.35, zorder=0)
        ax.axvspan(5.55, 9.45, color="#ffebe9", alpha=0.35, zorder=0)
        ax.axvspan(9.55, 12.45, color="#ddf4ff", alpha=0.35, zorder=0)
        ax.axvline(5.5, color="#8c959f", linestyle="--", lw=1.0, zorder=1)
        ax.axvline(9.5, color="#8c959f", linestyle="--", lw=1.0, zorder=1)

    # ---- Panel A: Victim degradation % ----
    ax1.axhspan(-5.0, 5.0, color="#1a7f37", alpha=0.12, zorder=1)
    ax1.axhline(5.0, color="#1a7f37", linestyle=":", lw=1.2, zorder=2)
    ax1.axhline(0.0, color="#57606a", lw=0.8, zorder=1)

    series_spec = [
        ("t6c", "STREAM Triad 6核 带宽下降 (%)", "#cf222e", "o", "-"),
        ("dram", "DRAM 256 MiB 延迟上升 (%)", "#0969da", "s", "-"),
        ("llc", "LLC 16 MiB 延迟上升 (%)", "#8250df", "^", "-"),
        ("t1c", "STREAM Triad 1核 带宽下降 (%)", "#e16f24", "D", "--"),
    ]
    all_pts = {}
    for vtag, lbl, col, mk, ls in series_spec:
        ys, lo, hi = [], [], []
        for aggr, _ in stages:
            m, mn, mx = deg_pct(by_lbl, vtag, aggr)
            ys.append(m)
            lo.append(max(0.0, m - mn))
            hi.append(max(0.0, mx - m))
        all_pts[vtag] = ys
        ax1.errorbar(
            x, ys, yerr=[lo, hi], label=lbl, color=col, marker=mk, linestyle=ls,
            linewidth=2.0, markersize=6.0, capsize=3.0, zorder=4,
        )

    ax1.set_yscale("symlog", linthresh=10.0, linscale=1.1)
    ax1.set_ylim(-6.5, 760.0)
    ax1.set_yticks([-5, 0, 5, 10, 20, 50, 100, 200, 400])
    ax1.set_yticklabels(["-5%", "0%", "+5%", "+10%", "+20%", "+50%", "+100%", "+200%", "+400%"], fontsize=9.0)
    ax1.set_ylabel("同 Socket 共置 Victim 退化幅度 (%)\n（正值=变慢/降速，浅绿带=±5% 阈值）", fontsize=9.8)
    ax1.set_title(
        "A. 同 Socket 共置应用退化幅度：单核 ≥ 150 Mpps 零退化（< 1%），多核无节流越过 175.5 Mpps 触发悬崖，限速 + b=128 归零",
        fontsize=10.6, fontweight="bold", pad=8,
    )

    # Regime header boxes strictly above the highest curve (+345%)
    ax1.text(2.5, 530, "区 1：单核稳态高带宽 MMIO\n(50~175 Mpps，全指标 < 1% 退化)",
             ha="center", va="center", fontsize=8.6, color="#116329", fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.32", facecolor="white", edgecolor="#1a7f37", alpha=0.95))
    ax1.text(7.5, 560, "区 2：多核无节流超发\n(>175.5 Mpps 链路反压悬崖)",
             ha="center", va="center", fontsize=8.6, color="#a40e26", fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.32", facecolor="white", edgecolor="#cf222e", alpha=0.95))
    ax1.text(11.0, 530, "区 3：单变量缓解对照\n(缩批 b=128 vs 限速 165 Mpps)",
             ha="center", va="center", fontsize=8.6, color="#0550ae", fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.32", facecolor="white", edgecolor="#0969da", alpha=0.95))

    # Clean whitespace callouts (zero overlap with curves!)
    llc_8c = all_pts["llc"][9]
    dram_8c = all_pts["dram"][9]
    t6c_8c = all_pts["t6c"][9]
    ax1.annotate(
        f"8核满速 (b=512):\nLLC +{llc_8c:.0f}%, DRAM +{dram_8c:.0f}%\nT6c -{t6c_8c:.0f}%",
        xy=(9, llc_8c), xytext=(6.05, 225), ha="center", va="center",
        fontsize=8.1, color="#a40e26", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="white", edgecolor="#cf222e", alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#cf222e", lw=1.1, connectionstyle="arc3,rad=-0.10"),
    )
    t6c_rec = all_pts["t6c"][12]
    ax1.annotate(
        f"8核限速 + b=128:\n全指标归零 (T6c {-t6c_rec:+.2f}%)",
        xy=(12, t6c_rec), xytext=(11.95, 160), ha="center", va="center",
        fontsize=8.1, color="#116329", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="white", edgecolor="#1a7f37", alpha=0.95),
        arrowprops=dict(arrowstyle="->", color="#1a7f37", lw=1.1),
    )
    ax1.legend(loc="upper left", bbox_to_anchor=(0.01, 0.72), fontsize=8.5, framealpha=0.95, ncol=2)

    # ---- Panel B: CHA_TOR_OCC_ALL + M2PCIe1 duty ----
    cha_llc = [med([r["cha"]["occ_entries"] for r in by_lbl[f"llc__{a}"]]) for a, _ in stages]
    cha_t6c = [med([r["cha"]["occ_entries"] for r in by_lbl[f"t6c__{a}"]]) for a, _ in stages]
    m2p_duty = [med([r["m2p1"]["ne_cha_ncb_duty_pct"] for r in by_lbl[f"llc__{a}"]]) for a, _ in stages]

    w = 0.36
    ax2.bar(x - w / 2, cha_llc, width=w, color="#0969da", alpha=0.88,
            label="CHA TOR 占用 (共置 LLC Victim, 全 32 颗 CHA 之和)", zorder=3)
    ax2.bar(x + w / 2, cha_t6c, width=w, color="#e16f24", alpha=0.85,
            label="CHA TOR 占用 (共置 STREAM 6核, 全 32 颗 CHA 之和)", zorder=3)
    ax2.axhline(640, color="#cf222e", linestyle=":", lw=1.2, zorder=2)
    ax2.text(3.35, 668, "32 颗 CHA TOR 满载悬崖 (~20 槽/CHA = 640 槽)",
             fontsize=8.3, color="#a40e26", fontweight="bold")

    ax2.set_ylim(0, 820)
    ax2.set_ylabel("Socket 1 CHA TOR 占用（总槽位数）", fontsize=9.8, color="#1f2328")
    ax2.set_xticks(x)
    ax2.set_xticklabels(xlabels, fontsize=8.6)

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    ax2_r.spines["right"].set_visible(True)
    ax2_r.plot(x, m2p_duty, color="#1f2328", marker="o", lw=1.9, markersize=5.0, linestyle="-",
               label="M2PCIe1 出口占空比 (%, 已除 2x 硬件倍率)", zorder=5)
    ax2_r.set_ylim(0, 122)
    ax2_r.set_ylabel("M2PCIe1 出口占空比 (%)", fontsize=9.5, color="#1f2328")
    ax2_r.grid(False)

    for idx_k in [4, 5, 7, 9, 10, 11, 12]:
        v_l = cha_llc[idx_k]
        v_t = cha_t6c[idx_k]
        if idx_k in (4, 5):
            ax2.text(idx_k - w / 2, v_l + 55, f"{v_l:.0f}", ha="center", va="bottom",
                     fontsize=7.6, color="#0550ae", fontweight="bold")
        else:
            ax2.text(idx_k - w / 2, v_l + 12, f"{v_l:.0f}", ha="center", va="bottom",
                     fontsize=7.6, color="#0550ae", fontweight="bold")
        ax2.text(idx_k + w / 2, v_t + 12, f"{v_t:.0f}", ha="center", va="bottom",
                 fontsize=7.6, color="#9a3412", fontweight="bold")

    ax2.set_title(
        "B. 100% 非复用硬件 PMU 因果账本：CHA_TOR_OCC_ALL 积压与 M2PCIe1 出口占空比精确对齐退化拐点",
        fontsize=10.6, fontweight="bold", pad=8,
    )
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2_r.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8.3, framealpha=0.95)

    fig.suptitle(
        "MRV-196 图 1：BAR4 MMIO 写速率阶梯与多核 CHA TOR 反压悬崖（Socket 1 独立物理核，Uncore 2.2 GHz，n = 3 交错中位数及极值）",
        fontsize=12.0, fontweight="bold", y=0.992,
    )
    fig.subplots_adjust(top=0.92, hspace=0.22)
    png = mrvplot.save(fig, "mrv-196-mmio-ladder-and-cliff")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
