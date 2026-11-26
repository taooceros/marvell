"""MRV-206 Figure 1: Single-core saturation throughput, host CPU cycle breakdown
(unmodified upstream Memcached core vs transport + MMIO-LOCK ablation), and
fixed-rate spare host CPU headroom across real 100GbE Memcached 1.6.45 workloads.

Reads raw verified n=3 runs directly from `logs/mrv206-2026-11-26/runs.jsonl` and `env.json`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv206-2026-11-26/runs.jsonl"
ENV_PATH = ROOT / "logs/mrv206-2026-11-26/env.json"


def med(xs):
    return statistics.median(xs)


def assert_verified(r):
    assert r.get("verified") is True and not r.get("errors"), f"unverified: {r.get('run_id')}"
    c, h, k = r["client"], r["host"], r["card"]
    p = h["pmu"]
    assert c["lost"] == 0 and c["bad"] == 0 and c["proto_err"] == 0 and c["val_err"] == 0 and c["torn"] == 0
    assert h["bad"] == 0 and h["torn"] == 0 and h["mc_misses"] == 0 and h["mc_errors"] == 0
    assert h["page_faults"] == 0 and p["page_faults"] == 0 and p["min_running_pct"] >= 99.5
    assert h["sdp_rx_nombuf"] == 0 and h["sdp_imissed"] == 0
    assert k["bad_wire"] == 0 and k["bad_host"] == 0 and k["torn_bar4"] == 0 and k["dma_failed"] == 0


def main():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    env = json.loads(ENV_PATH.read_text())
    idle_kps = env["idle_cal"]["app_work_kps"]

    by_id = defaultdict(list)
    for r in rows:
        assert_verified(r)
        by_id[(r["section"], r["run_id"])].append(r)

    host_ghz = med([r["host"]["pmu"]["ghz"] for r in rows if r["section"] == "sat"])

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16.5, 5.4), gridspec_kw={"width_ratios": [1.55, 1.05, 0.95]})

    # ---------------- Panel A: Saturation Throughput (W=64, n=3) ----------------
    wls = [
        ("get8_zipf", "8 B 读\n(热点)"),
        ("small_mix_zipf", "小值混合\n(热点)"),
        ("etc_mix_zipf", "ETC 混合\n(热点)"),
        ("etc_mix_uniform", "ETC 混合\n(均匀)"),
        ("get128_zipf", "128 B 读\n(热点)"),
        ("get1024_zipf", "1 KiB 读\n(热点)"),
        ("get3840_zipf", "3.8 KiB 读\n(热点)"),
        ("set3840_zipf", "3.8 KiB 写\n(热点)"),
    ]
    pols = [
        ("sdp_fair", "SDP fair-full (4Q×512)", "#64748b"),
        ("direct_peek", "DirectPush (窥探版)", "#2563eb"),
        ("direct_safe", "DirectPush (安全直取版)", "#1a7f37"),
    ]
    x = np.arange(len(wls))
    w = 0.25
    sdp_meds = []
    safe_meds = []

    for pi, (pol, label, color) in enumerate(pols):
        meds, lo_err, hi_err = [], [], []
        for wkey, _ in wls:
            rs = by_id[("sat", f"sat_{pol}_{wkey}_w64")]
            assert len(rs) == 3
            vals = [r["client"]["achieved_mrps"] for r in rs]
            m = med(vals)
            meds.append(m)
            lo_err.append(m - min(vals))
            hi_err.append(max(vals) - m)
        if pol == "sdp_fair":
            sdp_meds = meds
        elif pol == "direct_safe":
            safe_meds = meds
        pos = x + (pi - 1) * w
        ax1.bar(
            pos, meds, width=w, color=color, label=label, edgecolor="white", linewidth=0.5,
            yerr=[lo_err, hi_err], capsize=2.5, error_kw={"elinewidth": 1.0, "ecolor": "#1f2937"},
        )

    for i, (ms, md) in enumerate(zip(sdp_meds, safe_meds)):
        m_peek = med([r["client"]["achieved_mrps"] for r in by_id[("sat", f"sat_direct_peek_{wls[i][0]}_w64")]])
        best_d = max(md, m_peek)
        pct = (best_d - ms) / ms * 100.0
        col = "#1a7f37" if pct >= 5.0 else "#475569"
        ax1.text(
            x[i] + 0.08, max(ms, best_d) + 0.14, f"{pct:+.1f}%",
            ha="center", va="bottom", fontsize=8.8, fontweight="bold", color=col,
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels([lb for _, lb in wls], fontsize=8.5)
    ax1.set_ylabel("单核饱和吞吐（Mrps）", fontsize=10)
    ax1.set_ylim(0, 4.95)
    ax1.set_title("(a) 真实原版 Memcached 单核饱和吞吐（W=64）", fontsize=10.8, fontweight="bold", pad=8)
    ax1.legend(loc="upper right", fontsize=8.2, frameon=True, framealpha=0.92)

    # ---------------- Panel B: CPU Cycles Breakdown (Core vs Transport + Ablation) ----------------
    b_items = [
        ("8 B 读\nSDP",        ("sat", "sat_sdp_fair_get8_zipf_w64"),              ("breakdown", "breakdown_sdp_fair_get8")),
        ("8 B 读\nDP 直取",     ("sat", "sat_direct_safe_get8_zipf_w64"),           ("breakdown", "breakdown_direct_safe_get8")),
        ("8 B 读\nDP 未批*",    ("ablation", "ablation_direct_peek_nobatch_get8"),  ("ablation", "ablation_direct_peek_nobatch_get8_atim")),
        ("ETC 混合\nSDP",      ("sat", "sat_sdp_fair_etc_mix_zipf_w64"),           ("breakdown", "breakdown_sdp_fair_etc_mix")),
        ("ETC 混合\nDP 直取",   ("sat", "sat_direct_safe_etc_mix_zipf_w64"),        ("breakdown", "breakdown_direct_safe_etc_mix")),
    ]
    bx = np.arange(len(b_items))
    mc_cycs, net_cycs, tot_cycs = [], [], []
    for _, sat_k, brk_k in b_items:
        sat_rs = by_id[sat_k]
        brk_rs = by_id[brk_k]
        assert len(sat_rs) == 3 and len(brk_rs) == 3
        tot = med([r["host"]["pmu"]["cycles_per_req"] for r in sat_rs])
        mc_ns = med([r["host"]["mc_exec_ns_per_req"] for r in brk_rs])
        ghz = med([r["host"]["pmu"]["ghz"] for r in brk_rs])
        mc_c = mc_ns * ghz
        net_c = max(0.0, tot - mc_c)
        mc_cycs.append(mc_c)
        net_cycs.append(net_c)
        tot_cycs.append(tot)

    bar_colors_mc = ["#94a3b8", "#86efac", "#fca5a5", "#94a3b8", "#86efac"]
    bar_colors_net = ["#475569", "#1a7f37", "#dc2626", "#475569", "#1a7f37"]
    ax2.bar(bx, mc_cycs, width=0.56, color=bar_colors_mc, edgecolor="white", label="原版 Memcached 核心")
    ax2.bar(bx, net_cycs, bottom=mc_cycs, width=0.56, color=bar_colors_net, edgecolor="white", label="主机–网卡通路开销")

    for i, (mc_c, net_c, tot) in enumerate(zip(mc_cycs, net_cycs, tot_cycs)):
        ax2.text(bx[i], mc_c * 0.5, f"{mc_c:.0f}", ha="center", va="center", fontsize=8.3, color="#111827", fontweight="bold")
        ax2.text(bx[i], mc_c + net_c * 0.5, f"{net_c:.0f}", ha="center", va="center", fontsize=8.3, color="white", fontweight="bold")
        ax2.text(bx[i], tot + 28, f"{tot:.0f}", ha="center", va="bottom", fontsize=8.8, fontweight="bold", color="#111827")

    ax2.axvline(2.5, color="#cbd5e1", linestyle="--", linewidth=1.0)
    ax2.set_xticks(bx)
    ax2.set_xticklabels([lb for lb, _, _ in b_items], fontsize=8.4)
    ax2.set_ylabel("每请求主机 CPU 周期（cyc/req）", fontsize=10)
    ax2.set_ylim(0, 1820)
    ax2.set_title("(b) 主机周期拆解与 MMIO–LOCK 消融*", fontsize=10.8, fontweight="bold", pad=8)
    ax2.legend(loc="upper left", fontsize=7.8, frameon=True, framealpha=0.92)
    ax2.annotate(
        "*未批提交：LOCK\n 被 MMIO 堵 +94ns",
        xy=(2.0, tot_cycs[2] + 95), xytext=(2.55, 1595),
        ha="center", va="bottom", fontsize=7.9, fontweight="bold", color="#b91c1c",
        arrowprops={"arrowstyle": "->", "color": "#b91c1c", "lw": 1.0},
    )
    s_cases = [
        ("get8_1500k", "8 B 读\n1.50M"),
        ("etc_mix_1200k", "ETC 混合\n1.20M"),
        ("get3840_600k", "3.8 KiB 读\n0.60M"),
    ]
    sx = np.arange(len(s_cases))
    sw = 0.26
    s_sdp, s_safe = [], []
    for pi, (pol, label, color) in enumerate(pols):
        pcts = []
        for sk, _ in s_cases:
            rs = by_id[("spare_cpu", f"spare_{pol}_{sk}")]
            assert len(rs) == 3
            pcts.append(med([r["host"]["app_work_kps"] for r in rs]) / idle_kps * 100.0)
        if pol == "sdp_fair":
            s_sdp = pcts
        elif pol == "direct_safe":
            s_safe = pcts
        pos = sx + (pi - 1) * sw
        ax3.bar(pos, pcts, width=sw, color=color, edgecolor="white", linewidth=0.5)

    for i, (ps, pd) in enumerate(zip(s_sdp, s_safe)):
        ax3.text(sx[i] - sw, ps + 1.5, f"{ps:.0f}%", ha="center", va="bottom", fontsize=8.0, color="#475569")
        ax3.text(sx[i] + sw, pd + 1.5, f"{pd:.0f}%", ha="center", va="bottom", fontsize=8.3, fontweight="bold", color="#1a7f37")
        ax3.text(sx[i] + 0.05, pd + 8.5, f"+{pd - ps:.0f} pp", ha="center", va="bottom", fontsize=8.6, fontweight="bold", color="#1a7f37")

    ax3.set_xticks(sx)
    ax3.set_xticklabels([lb for _, lb in s_cases], fontsize=8.8)
    ax3.set_ylabel("同速率下空闲算力占比（%）", fontsize=10)
    ax3.set_ylim(0, 88)
    ax3.set_title("(c) 固定泊松负载下剩余主机算力", fontsize=10.8, fontweight="bold", pad=8)

    fig.suptitle(
        f"MRV-206：双机 100GbE 原版 Memcached 1.6.45（0 行核心修改，saturn 1 核 {host_ghz:.2f} GHz ⇄ CN106 4 核，n=3 中位数）",
        fontsize=11.8, fontweight="bold", y=0.975,
    )
    fig.tight_layout(rect=[0.0, 0.01, 1.0, 0.925])
    png = mrvplot.HERE / "mrv-206-throughput-cpu.png"
    svg = mrvplot.HERE / "mrv-206-throughput-cpu.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
