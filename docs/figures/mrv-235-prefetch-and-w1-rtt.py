#!/usr/bin/env python3
"""Plot MRV-235 Figure 2: W=1 single-message RTT cost & card-side prefetch / Dual-VA empirical answer."""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv235-2026-11-27/runs.jsonl"


def load_verified():
    by_cond = {}
    with RUNS.open() as f:
        for line in f:
            r = json.loads(line)
            cp = r["card_pmu"]
            cor = sum(w["corrupt"] for w in cp["workers"])
            par = sum(w["partial"] for w in cp["workers"])
            assert r["lost"] == 0 and r["bad_or_torn"] == 0 and cor == 0 and par == 0
            assert r["host_min_pct"] >= 99.9 and cp["min_pct"] >= 99.9
            by_cond.setdefault(r["cond"], []).append(r)
    return by_cond


def pmu_metrics(r):
    cp = r["card_pmu"]
    k = r["card_cores"]
    tak = max(1, sum(w["taken_win"] for w in cp["workers"]))
    l2i = sum(cp["core_pmu"][i]["l2d_cache_inval"] for i in range(k)) / tak
    ddr = cp["ddr"]["reads_total"] / tak
    return l2i, ddr


def main():
    by_cond = load_verified()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.0, 6.4), gridspec_kw={"width_ratios": [0.90, 1.38]})

    # Panel A: W=1 single-message RTT (K=1, n=3)
    w1_items = [
        ("Normal-WB (0xFF)\nldar [基线]", "wb64k_ldar_k1_w1", "#555555"),
        ("Normal-WB (0xFF)\n1-pass 64B", "wb64k_1p_k1_w1", "#888888"),
        ("Inner-NC Outer-WB\n16B 仅头字", "f4_hdr16_k1_w1", "#2A9D8F"),
        ("MT_NORMAL_NC (0x44)\n1-pass 64B", "nc44_1p_k1_w1", "#1D6FA5"),
        ("Inner-NC Outer-WB (0xF4)\n1-pass 64B", "f4_1p_k1_w1", "#D62828"),
        ("MT_NORMAL_NC (0x44)\n2-step ldar", "nc44_ldar_k1_w1", "#E9A03B"),
        ("Inner-NC Outer-WB (0xF4)\n2-step ldar", "f4_ldar_k1_w1", "#E76F51"),
        ("Dual-VA (NC轮询+WB读)\n3.88 MiB环 / 1.94 MiB偶行", "f4_dualwb_3875k_k1_w1", "#9B2226"),
    ]

    base_rtt = statistics.median(r["rtt_p50_ns"] for r in by_cond["wb64k_ldar_k1_w1"])
    y_a = np.arange(len(w1_items))[::-1]
    for y, (lbl, cond, col) in zip(y_a, w1_items):
        rtts = [r["rtt_p50_ns"] for r in by_cond[cond]]
        invs = [r["inv_rate_ns"] for r in by_cond[cond]]
        med = statistics.median(rtts)
        inv_m = statistics.median(invs)
        delta = med - base_rtt
        ax1.barh(y, med, height=0.62, color=col, alpha=0.86, zorder=3)
        ax1.scatter(rtts, [y] * len(rtts), color="black", s=14, zorder=4)
        ax1.scatter([inv_m], [y], color="#FFD166", edgecolor="black", marker="D", s=26, zorder=5)
        sign = f"+{delta:.0f} ns" if delta >= 0.5 else (f"{delta:.0f} ns" if delta <= -0.5 else "基线")
        ax1.text(max(med, inv_m) + 6, y, f"{med:.0f} ns ({sign})", va="center", ha="left", fontsize=8.4)

    ax1.set_yticks(y_a)
    ax1.set_yticklabels([x[0] for x in w1_items], fontsize=8.3)
    ax1.set_xlim(800, 1080)
    ax1.set_xlabel("W = 1 单条消息往返延迟 RTT p50 (ns，菱形 = 1 / 速率复核)", fontsize=9.8)
    ax1.set_title("A. W = 1 轻载 RTT 代价（K = 1 卡核，n = 3）", fontsize=10.8)

    # Panel B: Card-side Prefetch & Dual-VA strategies at K=2, W=64 (Inner-NC, Outer-WB 0xF4)
    p_items = [
        ("16B 仅头字 ldp (无预取)", "f4_hdr16_k2_w64", "#2A9D8F"),
        ("整 64B · 1-pass ldp (无预取)", "f4_1p_k2_w64", "#1D6FA5"),
        ("整 64B · 2-step nodmb ldp", "f4_nodmb_k2_w64", "#457B9D"),
        ("整 64B · 2-step ldar (MRV-231)", "f4_ish_k2_w64", "#6C9BD2"),
        ("整 64B · ldr+dmb (prfm无预取基线)", "f4_ldrdmb_k2_w64", "#5B84B1"),
        ("落地后 prfm pldl3keep (NC VA)", "f4_post_prfm_l3_k2", "#8FB8DE"),
        ("落地后 prfm pldl1keep (NC VA)", "f4_post_prfm_l1nc_k2", "#8FB8DE"),
        ("未落地前沿 prfm pldl1keep (NC VA)", "f4_front_prfm_l1nc_k2", "#8FB8DE"),
        ("Normal-WB 基线 (0xFF, 对比)", "wb64k_k2_w64", "#666666"),
        ("落地后 prfm pldl1keep + WB读", "f4_post_prfm_l1wb_k2", "#D62828"),
        ("未落地前沿 prfm pldl1keep (WB VA)", "f4_front_prfm_l1wb_k2", "#D62828"),
        ("Dual-VA 轮询NC+读WB (512 KiB环)", "f4_dualwb_512k_k2", "#B51717"),
        ("Dual-VA 轮询NC+读WB (3.88 MiB环/1.94 MiB偶行)", "f4_dualwb_3875k_k2", "#9B2226"),
        ("Dual-VA + 读后 dc civac (冲出L3)", "f4_dualwb_civac_k2", "#6A040F"),
    ]

    y_b = np.arange(len(p_items))[::-1]
    ax2.axvline(13.4, color="#A8A8A8", linestyle="--", linewidth=1.0, zorder=1)
    ldrdmb_med = statistics.median(r["mops"] for r in by_cond["f4_ldrdmb_k2_w64"])
    y_by_cond = {cond: y for y, (_, cond, _) in zip(y_b, p_items)}
    ax2.plot(
        [ldrdmb_med, ldrdmb_med],
        [y_by_cond["f4_front_prfm_l1nc_k2"] - 0.36, y_by_cond["f4_ldrdmb_k2_w64"] + 0.36],
        color="#2B4C7E", linestyle=":", linewidth=1.3, zorder=4,
    )
    for y, (lbl, cond, col) in zip(y_b, p_items):
        mops_list = [r["mops"] for r in by_cond[cond]]
        med = statistics.median(mops_list)
        l2i = statistics.median(pmu_metrics(r)[0] for r in by_cond[cond])
        ddr = statistics.median(pmu_metrics(r)[1] for r in by_cond[cond])
        is_prfm_base = cond == "f4_ldrdmb_k2_w64"
        ax2.barh(
            y, med, height=0.64, color=col, alpha=0.90, zorder=3,
            edgecolor="#2B4C7E" if is_prfm_base else "none",
            linewidth=1.1 if is_prfm_base else 0.0,
        )
        ax2.scatter(mops_list, [y] * len(mops_list), color="black", s=12, zorder=4)
        extra = f"L2作废={l2i:.2f}"
        if ddr >= 0.05:
            extra += f", DDR5读={ddr:.2f}"
        txt_x = max(med + 0.7, 14.3) if med < 13.8 else med + 0.7
        ax2.text(
            txt_x, y, f"{med:.2f} M/s ({extra})", va="center", ha="left", fontsize=8.0,
            color="#9B2226" if col.startswith(("#D6", "#B5", "#9B", "#6A")) else "#222222",
            fontweight="normal",
            bbox=dict(facecolor="white", edgecolor="none", pad=0.8, alpha=0.85),
            zorder=5,
        )
    ax2.set_yticks(y_b)
    ax2.set_yticklabels([x[0] for x in p_items], fontsize=8.2)
    ax2.set_xlim(0, 62)
    ax2.set_xlabel("闭环吞吐 (M 消息/秒，K = 2 卡核，W = 64，n = 3)", fontsize=9.8)
    ax2.set_title("B. 卡侧预取与双虚拟映射实测：无需预取，任何 WB 访问均重爆嗅探串行", fontsize=10.8)

    fig.suptitle(
        "MRV-235：回应卡侧是否需要 prefetch —— 1-pass 整 64 B 读仅增 +12 ns 轻载延迟，且禁碰任何 WB 别名才能避免 L2 作废串行回爆",
        fontsize=10.8, y=0.975,
    )
    fig.tight_layout(rect=[0, 0.01, 1, 0.935])
    png = mrvplot.HERE / "mrv-235-prefetch-and-w1-rtt.png"
    svg = mrvplot.HERE / "mrv-235-prefetch-and-w1-rtt.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
