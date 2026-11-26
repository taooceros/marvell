"""MRV-206 Figure 1: Single-core saturation throughput, host CPU cycle breakdown
(unmodified upstream Memcached core vs net active transport + residual empty-poll
+ MMIO-LOCK ablation), and fixed-rate spare host CPU headroom (raw 263 ns quantum
vs net after deducting short-quantum empty-poll tax) across real 100GbE Memcached 1.6.45.

100% dynamically computed from `logs/mrv206-2026-11-26/runs.jsonl` and `env.json`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
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


def regress_empty_poll_cost(rows):
    """Estimate 4-queue empty-poll wall time (ns) and cycle cost per empty poll
    directly from no-background-work Poisson runs at 200k-1000k.
    """
    ep_ns, ep_cyc = {}, {}
    for pol in ("sdp_fair", "direct_peek", "direct_safe"):
        # Wall-clock time per empty poll via pooled 200k/500k/1000k intercept of (ep/dt) vs (rx/dt)
        pts = [
            r for r in rows
            if r["section"] == "poisson" and r["policy"] == pol and r["rate_krps"] in (200, 500, 1000)
        ]
        xs = [r["host"]["rx_rpcs"] / r["host"]["dt_s"] for r in pts]
        ys = [r["host"]["empty_polls"] / r["host"]["dt_s"] for r in pts]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        ep_ns[pol] = 1e9 / (my - slope * mx)

        # Cycle cost per empty poll via 3-workload 200k vs 1000k slope of (cycles/rx) vs (ep/rx)
        c_slopes = []
        for wl in ("get8", "get128", "etc_mix"):
            r200 = [r for r in pts if r["workload"] == wl and r["rate_krps"] == 200]
            r1000 = [r for r in pts if r["workload"] == wl and r["rate_krps"] == 1000]
            if r200 and r1000:
                e1 = statistics.mean([r["host"]["empty_polls"] / r["host"]["rx_rpcs"] for r in r200])
                e2 = statistics.mean([r["host"]["empty_polls"] / r["host"]["rx_rpcs"] for r in r1000])
                c1 = statistics.mean([r["host"]["pmu"]["cycles"] / r["host"]["rx_rpcs"] for r in r200])
                c2 = statistics.mean([r["host"]["pmu"]["cycles"] / r["host"]["rx_rpcs"] for r in r1000])
                c_slopes.append((c1 - c2) / (e1 - e2))
        ep_cyc[pol] = statistics.mean(c_slopes)
    return ep_ns, ep_cyc


def main():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    env = json.loads(ENV_PATH.read_text())
    idle_kps = env["idle_cal"]["app_work_kps"]
    tau_app_ns = 1e6 / idle_kps

    by_id = defaultdict(list)
    for r in rows:
        assert_verified(r)
        by_id[(r["section"], r["run_id"])].append(r)

    host_ghz = med([r["host"]["pmu"]["ghz"] for r in rows if r["section"] == "sat"])
    ep_ns, ep_cyc = regress_empty_poll_cost(rows)

    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(17.2, 5.95), gridspec_kw={"width_ratios": [1.62, 1.05, 1.03]}
    )

    # ---------------- Panel A: Saturation Throughput (W=64, n=3) ----------------
    # Identify workloads where DirectPush host busy% < 95% at W=64 (partially W/RTT-limited)
    wls_meta = [
        ("get8_zipf", "8 B 读\n(热点)"),
        ("small_mix_zipf", "小值混合\n(热点)"),
        ("etc_mix_zipf", "ETC 混合\n(热点)"),
        ("etc_mix_uniform", "ETC 混合\n(均匀)"),
        ("get128_zipf", "128 B 读\n(热点)"),
        ("get1024_zipf", "1 KiB 读\n(热点)"),
        ("get3840_zipf", "3.8 KiB 读\n(热点)"),
        ("set3840_zipf", "3.8 KiB 写\n(热点)"),
    ]
    wls = []
    partial_busy_vals = []
    for wkey, lb in wls_meta:
        busys = [
            med([
                r["host"]["busy_ns_per_req"] * r["host"]["rx_rpcs"] / (r["host"]["dt_s"] * 1e9) * 100.0
                for r in by_id[("sat", f"sat_{pol}_{wkey}_w64")]
            ])
            for pol in ("direct_peek", "direct_safe")
        ]
        if max(busys) < 95.0:
            partial_busy_vals.extend(busys)
            lines = lb.split("\n")
            lb = f"{lines[0]}†\n{lines[1]}"
        wls.append((wkey, lb))
    pols = [
        ("sdp_fair", "SDP fair-full (4Q×512)", "#64748b"),
        ("direct_peek", "DirectPush (窥探版 · 同卡侧)", "#2563eb"),
        ("direct_safe", "DirectPush (安全直取版)", "#1a7f37"),
    ]
    x = np.arange(len(wls))
    w = 0.25
    sdp_meds, peek_meds, safe_meds = [], [], []

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
        elif pol == "direct_peek":
            peek_meds = meds
        elif pol == "direct_safe":
            safe_meds = meds
        pos = x + (pi - 1) * w
        ax1.bar(
            pos, meds, width=w, color=color, label=label, edgecolor="white", linewidth=0.5,
            yerr=[lo_err, hi_err], capsize=2.3, error_kw={"elinewidth": 1.0, "ecolor": "#1f2937"},
        )

    # Unified non-cherry-picked annotations: top bold = safe vs SDP; blue parens = peek vs SDP (pure host-interface)
    for i, (ms, mp, md) in enumerate(zip(sdp_meds, peek_meds, safe_meds)):
        pct_safe = (md - ms) / ms * 100.0
        pct_peek = (mp - ms) / ms * 100.0
        top_y = max(ms, mp, md)
        col_safe = "#1a7f37" if pct_safe >= 5.0 else ("#b91c1c" if pct_safe < 0 else "#475569")
        ax1.text(
            x[i] + 0.04, top_y + 0.34, f"{pct_safe:+.1f}%",
            ha="center", va="bottom", fontsize=8.4, fontweight="bold", color=col_safe,
        )
        ax1.text(
            x[i] + 0.04, top_y + 0.12, f"({pct_peek:+.1f}%)",
            ha="center", va="bottom", fontsize=7.4, color="#1d4ed8",
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels([lb for _, lb in wls], fontsize=8.3)
    ax1.set_ylabel("单核饱和吞吐（Mrps）", fontsize=9.8)
    ax1.set_ylim(0, 5.25)
    ax1.set_title("(a) 真实原版 Memcached 单核饱和吞吐（W=64）", fontsize=10.5, fontweight="bold", pad=8)
    ax1.legend(loc="upper right", fontsize=7.9, frameon=True, framealpha=0.94,
               title="顶标：绿/红=直取版，蓝括号=窥探版 vs SDP", title_fontsize=7.3)

    # ---------------- Panel B: CPU Cycles Breakdown (Core + Net Active + Residual Empty Poll) ----------------
    b_items = [
        ("8 B 读\nSDP",      "sdp_fair",    ("sat", "sat_sdp_fair_get8_zipf_w64"),              ("breakdown", "breakdown_sdp_fair_get8")),
        ("8 B 读\nDP 直取",   "direct_safe", ("sat", "sat_direct_safe_get8_zipf_w64"),           ("breakdown", "breakdown_direct_safe_get8")),
        ("8 B 读\nDP 未批*",  "direct_peek", ("ablation", "ablation_direct_peek_nobatch_get8"),  ("ablation", "ablation_direct_peek_nobatch_get8_atim")),
        ("ETC 混合\nSDP",    "sdp_fair",    ("sat", "sat_sdp_fair_etc_mix_zipf_w64"),           ("breakdown", "breakdown_sdp_fair_etc_mix")),
        ("ETC 混合\nDP 直取", "direct_safe", ("sat", "sat_direct_safe_etc_mix_zipf_w64"),        ("breakdown", "breakdown_direct_safe_etc_mix")),
    ]
    bx = np.arange(len(b_items))
    mc_cycs, active_net_cycs, ep_cycs_b, tot_cycs = [], [], [], []
    for _, pol, sat_k, brk_k in b_items:
        sat_rs = by_id[sat_k]
        brk_rs = by_id[brk_k]
        assert len(sat_rs) == 3 and len(brk_rs) == 3
        tot = med([r["host"]["pmu"]["cycles_per_req"] for r in sat_rs])
        mc_ns = med([r["host"]["mc_exec_ns_per_req"] for r in brk_rs])
        ghz = med([r["host"]["pmu"]["ghz"] for r in brk_rs])
        mc_c = mc_ns * ghz
        ep_c = med([r["host"]["empty_polls"] / r["host"]["rx_rpcs"] for r in sat_rs]) * ep_cyc[pol]
        act_net = max(0.0, tot - mc_c - ep_c)
        mc_cycs.append(mc_c)
        active_net_cycs.append(act_net)
        ep_cycs_b.append(ep_c)
        tot_cycs.append(tot)

    bar_colors_mc = ["#94a3b8", "#86efac", "#fca5a5", "#94a3b8", "#86efac"]
    bar_colors_net = ["#475569", "#1a7f37", "#dc2626", "#475569", "#1a7f37"]
    bar_colors_ep = ["#cbd5e1", "#bbf7d0", "#fecaca", "#cbd5e1", "#bbf7d0"]

    ax2.bar(bx, mc_cycs, width=0.56, color=bar_colors_mc, edgecolor="white")
    ax2.bar(bx, active_net_cycs, bottom=mc_cycs, width=0.56, color=bar_colors_net, edgecolor="white")
    bottom_ep = [m + n for m, n in zip(mc_cycs, active_net_cycs)]
    ax2.bar(bx, ep_cycs_b, bottom=bottom_ep, width=0.56, color=bar_colors_ep, hatch="///", edgecolor="#64748b", linewidth=0.4)

    for i, (mc_c, act_c, ep_c, tot) in enumerate(zip(mc_cycs, active_net_cycs, ep_cycs_b, tot_cycs)):
        ax2.text(bx[i], mc_c * 0.5, f"{mc_c:.0f}", ha="center", va="center", fontsize=8.1, color="#111827", fontweight="bold")
        ax2.text(bx[i], mc_c + act_c * 0.5, f"{act_c:.0f}", ha="center", va="center", fontsize=8.1, color="white", fontweight="bold")
        ax2.text(bx[i], tot + 25, f"{tot:.0f}", ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#111827")

    ax2.axvline(2.5, color="#cbd5e1", linestyle="--", linewidth=1.0)
    ax2.set_xticks(bx)
    ax2.set_xticklabels([lb for lb, _, _, _ in b_items], fontsize=8.2)
    ax2.set_ylabel("每请求主机 CPU 周期（cyc/req）", fontsize=9.8)
    ax2.set_ylim(0, 1920)
    ax2.set_title("(b) 主机周期拆解（扣 W=64 空转）与消融*", fontsize=10.5, fontweight="bold", pad=8)
    b_handles = [
        Patch(facecolor="#94a3b8", edgecolor="white", label="原版 Memcached 核心"),
        Patch(facecolor="#475569", edgecolor="white", label="净活跃通路 [推断]"),
        Patch(facecolor="#cbd5e1", edgecolor="#64748b", hatch="///", label="W=64 残余空轮询 [推断]"),
    ]
    ax2.legend(handles=b_handles, loc="upper left", fontsize=7.5, frameon=True, framealpha=0.94)

    mc_peek_ns = med([r["host"]["mc_exec_ns_per_req"] for r in by_id[("breakdown", "breakdown_direct_peek_get8")]])
    mc_nb_ns = med([r["host"]["mc_exec_ns_per_req"] for r in by_id[("ablation", "ablation_direct_peek_nobatch_get8_atim")]])
    lock_delta_ns = mc_nb_ns - mc_peek_ns

    get8_dp_busy_net = (
        med([r["host"]["busy_ns_per_req"] * r["host"]["pmu"]["ghz"] for r in by_id[("sat", "sat_direct_safe_get8_zipf_w64")]])
        - mc_cycs[1]
    )
    get8_net_drop_ep = (active_net_cycs[1] - active_net_cycs[0]) / active_net_cycs[0] * 100.0
    get8_net_drop_busy = (get8_dp_busy_net - active_net_cycs[0]) / active_net_cycs[0] * 100.0

    ax2.annotate(
        f"*未批提交：LOCK\n被 MMIO 堵 +{lock_delta_ns:.0f} ns",
        xy=(2.0, tot_cycs[2] + 95), xytext=(2.85, 1665),
        ha="center", va="bottom", fontsize=7.6, fontweight="bold", color="#b91c1c",
        arrowprops={"arrowstyle": "->", "color": "#b91c1c", "lw": 1.0},
    )
    ax2.text(
        0.55, 1170,
        f"8 B 净通路 [推断]\n{active_net_cycs[0]:.0f}→{active_net_cycs[1]:.0f} ({get8_net_drop_ep:.1f}%)\n(busy ~{get8_dp_busy_net:.0f}, {get8_net_drop_busy:.1f}%)",
        ha="center", va="bottom", fontsize=7.1, fontweight="bold", color="#1a7f37",
        bbox={"boxstyle": "round,pad=0.22", "facecolor": "#f0fdf4", "edgecolor": "#86efac", "lw": 0.7},
    )
    # ---------------- Panel C: Fixed-Rate Spare CPU (Raw 263 ns vs Net after Empty-Poll Tax) ----------------
    s_cases = [
        ("get8_1500k", "8 B 读\n1.50M"),
        ("etc_mix_1200k", "ETC 混合\n1.20M"),
        ("get3840_600k", "3.8 KiB 读\n0.60M"),
    ]
    sx = np.arange(len(s_cases))
    sw = 0.26
    s_raw, s_tax, s_net, s_act_cyc = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)

    hatched_tint = {"sdp_fair": "#cbd5e1", "direct_peek": "#bfdbfe", "direct_safe": "#bbf7d0"}
    for pi, (pol, label, color) in enumerate(pols):
        for sk, _ in s_cases:
            rs = by_id[("spare_cpu", f"spare_{pol}_{sk}")]
            assert len(rs) == 3
            raw_sp = med([r["host"]["app_work_kps"] / idle_kps * 100.0 for r in rs])
            tax_pp = med([r["host"]["empty_polls"] * ep_ns[pol] / (r["host"]["dt_s"] * 1e9) * 100.0 for r in rs])
            net_sp = med([
                (r["host"]["app_work_kps"] / idle_kps + r["host"]["empty_polls"] * ep_ns[pol] / (r["host"]["dt_s"] * 1e9)) * 100.0
                for r in rs
            ])
            net_c = med([
                (1.0 - r["host"]["app_work_kps"] / idle_kps - r["host"]["empty_polls"] * ep_ns[pol] / (r["host"]["dt_s"] * 1e9))
                * r["host"]["pmu"]["ghz"] * 1000.0 / r["client"]["achieved_mrps"]
                for r in rs
            ])
            s_raw[pol].append(raw_sp)
            s_tax[pol].append(tax_pp)
            s_net[pol].append(net_sp)
            s_act_cyc[pol].append(net_c)

        pos = sx + (pi - 1) * sw
        ax3.bar(pos, s_raw[pol], width=sw, color=color, edgecolor="white", linewidth=0.5)
        ax3.bar(
            pos, s_tax[pol], bottom=s_raw[pol], width=sw,
            color=hatched_tint[pol], hatch="///", edgecolor=color, linewidth=0.55,
        )

    net_pp_diffs, raw_pp_diffs, act_cyc_drops = [], [], []
    for i in range(len(s_cases)):
        rs_s, ns_s, cs_s = s_raw["sdp_fair"][i], s_net["sdp_fair"][i], s_act_cyc["sdp_fair"][i]
        rs_d, ns_d, cs_d = s_raw["direct_safe"][i], s_net["direct_safe"][i], s_act_cyc["direct_safe"][i]
        d_raw = rs_d - rs_s
        d_net = ns_d - ns_s
        d_cyc = (cs_d - cs_s) / cs_s * 100.0
        raw_pp_diffs.append(d_raw)
        net_pp_diffs.append(d_net)
        act_cyc_drops.append(d_cyc)

        # Raw spare inside solid bars; net spare at cap tops
        ax3.text(sx[i] - sw, rs_s * 0.5, f"{rs_s:.0f}%", ha="center", va="center", fontsize=7.4, color="white", fontweight="bold")
        ax3.text(sx[i] + sw, rs_d * 0.5, f"{rs_d:.0f}%", ha="center", va="center", fontsize=7.4, color="white", fontweight="bold")
        ax3.text(sx[i] - sw, ns_s + 1.2, f"{ns_s:.1f}%", ha="center", va="bottom", fontsize=7.3, color="#475569", fontweight="bold")
        ax3.text(sx[i] + sw, ns_d + 1.2, f"{ns_d:.1f}%", ha="center", va="bottom", fontsize=7.5, color="#1a7f37", fontweight="bold")

        ax3.text(
            sx[i] + 0.04, ns_d + 7.5,
            f"净 +{d_net:.1f} pp\n活周期 {d_cyc:.1f}%",
            ha="center", va="bottom", fontsize=7.9, fontweight="bold", color="#1a7f37",
        )

    c_handles = [
        Patch(facecolor="#64748b", edgecolor="white", label=f"实测空闲算力 ({tau_app_ns:.0f} ns 量子)"),
        Patch(facecolor="#cbd5e1", edgecolor="#64748b", hatch="///", label="扣空轮询税 [推断]"),
    ]
    ax3.legend(handles=c_handles, loc="upper left", fontsize=7.5, frameon=True, framealpha=0.94)
    ax3.set_xticks(sx)
    ax3.set_xticklabels([lb for _, lb in s_cases], fontsize=8.5)
    ax3.set_ylabel("同速率下空闲算力占比（%）", fontsize=9.8)
    ax3.set_ylim(0, 102)
    ax3.set_title("(c) 固定泊松剩余算力（原始 vs 扣空轮询净值）", fontsize=10.3, fontweight="bold", pad=8)

    fig.suptitle(
        f"MRV-206：双机 100GbE 原版 Memcached 1.6.45（0 行核心修改，saturn 1 核 {host_ghz:.2f} GHz ⇄ CN106 4 核，n=3 中位数）",
        fontsize=11.8, fontweight="bold", y=0.975,
    )
    fig.text(
        0.5, 0.008,
        f"注：(a) 顶标统一为安全直取版 vs SDP，蓝括号为同卡侧窥探版（纯主机接口增益）；† 标示 W=64 下主机核忙时 {min(partial_busy_vals):.0f}%–{max(partial_busy_vals):.0f}%（部分受 W/RTT 并发约束，直取版降延迟再转化 +3%–5%）。\n"
        f"(b)(c) 由无背景泊松 200k–1000k 回归得 4 队列空轮询成本 {ep_cyc['sdp_fair']:.1f} vs {ep_cyc['direct_safe']:.1f} cyc/轮（{ep_ns['sdp_fair']:.1f} vs {ep_ns['direct_safe']:.1f} ns）；"
        f"同速率净空余算力 +{net_pp_diffs[0]:.1f} / +{net_pp_diffs[1]:.1f} / +{net_pp_diffs[2]:.1f} pp（原始 {tau_app_ns:.0f} ns 量子 +{raw_pp_diffs[0]:.1f} / +{raw_pp_diffs[1]:.1f} / +{raw_pp_diffs[2]:.1f} pp），净活跃周期 {act_cyc_drops[0]:.1f}% / {act_cyc_drops[1]:.1f}% / {act_cyc_drops[2]:.1f}%。",
        ha="center", va="bottom", fontsize=7.7, color="#475569",
    )
    fig.tight_layout(rect=[0.0, 0.068, 1.0, 0.93])
    png = mrvplot.HERE / "mrv-206-throughput-cpu.png"
    svg = mrvplot.HERE / "mrv-206-throughput-cpu.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")
    print(f"  ep_cyc: sdp={ep_cyc['sdp_fair']:.1f}, peek={ep_cyc['direct_peek']:.1f}, safe={ep_cyc['direct_safe']:.1f}")
    print(f"  ep_ns : sdp={ep_ns['sdp_fair']:.2f}, peek={ep_ns['direct_peek']:.2f}, safe={ep_ns['direct_safe']:.2f}")
    print(f"  get8 active_net: sdp={active_net_cycs[0]:.1f}, dp_ep={active_net_cycs[1]:.1f}, dp_busy={get8_dp_busy_net:.1f} ({get8_net_drop_busy:.1f}%)")
    print(f"  spare net_pp: {[round(v, 1) for v in net_pp_diffs]}, net_cyc_drop: {[round(v, 1) for v in act_cyc_drops]}")


if __name__ == "__main__":
    main()
