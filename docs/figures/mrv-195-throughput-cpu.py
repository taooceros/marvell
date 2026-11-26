"""MRV-195 Figure 1: Single-core saturation throughput and matched-rate host CPU
cycles per request (spare-CPU accounting) across 6 real 100GbE KV workloads.

Reads raw verified n=3 runs directly from `logs/mrv195-2026-11-25/runs.jsonl`.
All numbers shown in the figure are computed dynamically from the raw jsonl.
"""
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv195-2026-11-25/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def r_half_up(x: float) -> int:
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def f1(x: float) -> str:
    sign = "-" if x < 0 else ""
    v = math.floor(abs(x) * 10.0 + 0.5) / 10.0
    return f"{sign}{v:.1f}"


def assert_verified(r):
    assert r.get("verified") is True, f"unverified record: {r.get('verify_errors')}"
    if r.get("part") == "part0_idle_cal":
        return
    c, h, k = r["client"], r["host"], r["card"]
    assert c["lost"] == 0 and c["bad"] == 0 and c["torn"] == 0 and c["send_err"] == 0
    if r["drive_mode"] == "poisson":
        assert c.get("window_cap_hits", 0) == 0
    eth_d = c.get("merc_ethtool_delta") or {}
    for ek in ("rx_out_of_buffer", "rx_discards_phy", "rx_crc_errors_phy"):
        assert eth_d.get(ek, 0) == 0
    assert k["bad_wire"] == 0 and k["bad_host"] == 0 and k["torn_bar4"] == 0 and k["dma_failed"] == 0
    assert k["eth_imissed"] == 0 and k["eth_ierrors"] == 0 and k["eth_oerrors"] == 0 and k["eth_tx_drops"] == 0
    assert h["bad"] == 0 and h["torn"] == 0 and h["kv_misses"] == 0 and h["tx_drops"] == 0
    assert h["page_faults"] == 0
    assert h["pmu"]["min_running_pct"] >= 99.5


def load_data():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    sat = defaultdict(list)
    aw = defaultdict(list)
    w1 = defaultdict(list)
    idle_cal = []
    used = []
    for r in rows:
        assert_verified(r)
        if r["part"] == "part0_idle_cal":
            idle_cal.append(r["cal"]["app_work_kps"])
        elif r["part"] == "part1_sat":
            sat[(r["policy"], r["workload"], r["key_dist"])].append(r)
            used.append(r)
        elif r["part"] == "part2a_w1":
            w1[r["policy"]].append(r)
        elif r["part"] == "part3a_appwork":
            assert r["app_work"] is True and r["aw_cyc_per_req"] is not None
            aw[(r["policy"], r["workload"], r["key_dist"])].append(r)
            used.append(r)
    return sat, aw, w1, idle_cal, used


def main():
    sat, aw, w1, idle_cal, used = load_data()
    wls = [
        ("get64", "zipf", "64 B 读（热点）*"),
        ("get256", "zipf", "256 B 读（热点）*"),
        ("get1024", "zipf", "1 KiB 读（热点）"),
        ("get4096", "zipf", "4 KiB 读（热点）"),
        ("mix", "zipf", "混合大小值（热点）"),
        ("mix", "uniform", "混合大小值（均匀）"),
    ]

    # Dynamic configuration metadata from raw runs
    host_ghz = med([r["host"]["pmu"]["ghz"] for r in used])
    card_cores = int(med([r["card"]["n_cores"] for r in used]))
    cli_threads = int(med([r["client"]["threads"] for r in used]))
    sdp_queues = int(med([r["card_queues"] for r in sat[("sdp_fair", "get64", "zipf")]]))
    n_reps = len(sat[("direct_safe", "get64", "zipf")])

    pols = [
        ("sdp_fair", f"最优异步 SDP（{sdp_queues} 队列）", "#64748b"),
        ("direct_peek", "DirectPush（标准窥探版）", "#2563eb"),
        ("direct_safe", "DirectPush（安全直取版）", "#1a7f37"),
    ]

    tp_pcts = []
    cpu_save_pcts = []
    for wl, kd, _ in wls:
        for p in ("sdp_fair", "direct_peek", "direct_safe"):
            assert len(sat[(p, wl, kd)]) == n_reps
            assert len(aw[(p, wl, kd)]) == n_reps
        v_s = med([r["client"]["achieved_mrps"] for r in sat[("sdp_fair", wl, kd)]])
        v_d = med([r["client"]["achieved_mrps"] for r in sat[("direct_safe", wl, kd)]])
        tp_pcts.append((v_d - v_s) / v_s * 100.0)
        c_s = med([r["aw_cyc_per_req"] for r in aw[("sdp_fair", wl, kd)]])
        c_d = med([r["aw_cyc_per_req"] for r in aw[("direct_safe", wl, kd)]])
        cpu_save_pcts.append((c_s - c_d) / c_s * 100.0)

    put4k_gbps = med([r["client"]["wire_tx_gbps"] for r in sat[("direct_safe", "put4096", "zipf")]])
    put64_s = med([r["client"]["achieved_mrps"] for r in sat[("sdp_fair", "put64", "zipf")]])
    put64_d = med([r["client"]["achieved_mrps"] for r in sat[("direct_safe", "put64", "zipf")]])
    put64_pct = (put64_d - put64_s) / put64_s * 100.0

    # Host busy% in Part 1 saturation: shows 64/256 B DirectPush is card-core capped while >=1 KiB/mix saturates host core
    small_busy = [
        med([r["host"]["busy_ns_per_req"] * r["host"]["rx_rpcs"] / (r["host"]["dt_s"] * 1e9) * 100.0
             for r in sat[("direct_safe", wl, "zipf")]])
        for wl in ("get64", "get256")
    ]
    large_busy = [
        med([r["host"]["busy_ns_per_req"] * r["host"]["rx_rpcs"] / (r["host"]["dt_s"] * 1e9) * 100.0
             for r in sat[("direct_safe", wl, kd)]])
        for wl, kd in (("get1024", "zipf"), ("get4096", "zipf"), ("mix", "zipf"), ("mix", "uniform"))
    ]

    # Short-quantum empty-poll tax estimate from W=1 runs and net host CPU savings
    tau_app_ns = 1e6 / med(idle_cal)
    ep_cyc_sdp = med([
        (r["host"]["pmu"]["cycles"] - r["host"]["busy_ns_per_req"] * r["host"]["rx_rpcs"] * r["host"]["pmu"]["ghz"])
        / r["host"]["empty_polls"]
        for r in w1["sdp_fair"]
    ])
    ep_cyc_ds = med([
        (r["host"]["pmu"]["cycles"] - r["host"]["busy_ns_per_req"] * r["host"]["rx_rpcs"] * r["host"]["pmu"]["ghz"])
        / r["host"]["empty_polls"]
        for r in w1["direct_safe"]
    ])
    net_save_pcts = []
    for wl, kd, _ in wls:
        net_s = med([
            r["aw_cyc_per_req"] - (r["host"]["empty_polls"] / r["host"]["rx_rpcs"]) * ep_cyc_sdp
            for r in aw[("sdp_fair", wl, kd)]
        ])
        net_d = med([
            r["aw_cyc_per_req"] - (r["host"]["empty_polls"] / r["host"]["rx_rpcs"]) * ep_cyc_ds
            for r in aw[("direct_safe", wl, kd)]
        ])
        net_save_pcts.append((net_s - net_d) / net_s * 100.0)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.0, 6.2))
    y = np.arange(len(wls))
    h = 0.25

    # ---- Panel A: Single-core saturation throughput ----
    ax1.grid(axis="y", visible=False)
    ax1.grid(axis="x", alpha=0.28)
    max_x1 = 0.0
    for pi, (pol, label, col) in enumerate(pols):
        vals = [med([r["client"]["achieved_mrps"] for r in sat[(pol, wl, kd)]]) for wl, kd, _ in wls]
        max_x1 = max(max_x1, max(vals))
        offs = y + (pi - 1) * h
        ax1.barh(offs, vals, height=h * 0.90, color=col, alpha=0.92, label=label, zorder=3)

    for i, (wl, kd, _) in enumerate(wls):
        v_sdp = med([r["client"]["achieved_mrps"] for r in sat[("sdp_fair", wl, kd)]])
        v_ds = med([r["client"]["achieved_mrps"] for r in sat[("direct_safe", wl, kd)]])
        pct = (v_ds - v_sdp) / v_sdp * 100.0
        sign = "+" if pct >= 0 else ""
        tag = f"{v_ds:.2f}  ({sign}{f1(pct)}%)"
        col_txt = "#116329" if pct >= 0 else "#b91c1c"
        ax1.text(
            v_sdp + 0.30, i - h, f"{v_sdp:.2f}",
            va="center", ha="left", fontsize=9.0, color="#475569",
        )
        ax1.text(
            v_ds + 0.30, i + h, tag,
            va="center", ha="left", fontsize=9.8, fontweight="bold", color=col_txt,
        )

    ax1.set_xlim(0, max_x1 * 1.38)
    ax1.set_yticks(y)
    ax1.set_yticklabels([w[2] for w in wls], fontsize=10.5, fontweight="bold")
    ax1.invert_yaxis()
    ax1.set_xlabel(f"端到端饱和吞吐（百万请求/秒，1 核主机 ⇄ {card_cores} 核网卡，越长越好）", fontsize=10.3, labelpad=8)
    ax1.set_title(
        f"A. 端到端饱和吞吐：小请求提速 {r_half_up(tp_pcts[0])}%，混合负载提速 {r_half_up(tp_pcts[5])}%–{r_half_up(tp_pcts[4])}%",
        fontsize=11.4, fontweight="bold", pad=12,
    )
    ax1.legend(loc="lower right", frameon=True, facecolor="white", edgecolor="#cbd5e1", framealpha=0.96, fontsize=9.5)

    # ---- Panel B: Matched-rate host CPU cycles/req ----
    ax2.grid(axis="y", visible=False)
    ax2.grid(axis="x", alpha=0.28)
    max_x2 = 0.0
    for pi, (pol, label, col) in enumerate(pols):
        vals = [med([r["aw_cyc_per_req"] for r in aw[(pol, wl, kd)]]) for wl, kd, _ in wls]
        max_x2 = max(max_x2, max(vals))
        offs = y + (pi - 1) * h
        ax2.barh(offs, vals, height=h * 0.90, color=col, alpha=0.92, label=label, zorder=3)

    rate_labels = []
    for i, (wl, kd, lbl) in enumerate(wls):
        rk = med([r["rate_krps"] for r in aw[("direct_safe", wl, kd)]]) / 1000.0
        r_txt = f"{rk:.0f}M" if abs(rk - round(rk)) < 0.05 else f"{rk:.1f}M"
        base_lbl = lbl.rstrip("*")
        rate_labels.append(f"{base_lbl}\n同速 @{r_txt}")
        c_sdp = med([r["aw_cyc_per_req"] for r in aw[("sdp_fair", wl, kd)]])
        c_ds = med([r["aw_cyc_per_req"] for r in aw[("direct_safe", wl, kd)]])
        diff_pct = (c_ds - c_sdp) / c_sdp * 100.0
        sign = "+" if diff_pct > 0 else ""
        lbl_tag = f"{r_half_up(c_ds)}  ({sign}{f1(diff_pct)}%)"
        col_txt = "#116329" if diff_pct <= 0 else "#b91c1c"
        ax2.text(
            c_sdp + max_x2 * 0.015, i - h, f"{r_half_up(c_sdp)}",
            va="center", ha="left", fontsize=9.0, color="#475569",
        )
        ax2.text(
            c_ds + max_x2 * 0.015, i + h, lbl_tag,
            va="center", ha="left", fontsize=9.8, fontweight="bold", color=col_txt,
        )

    ax2.set_xlim(0, max_x2 * 1.38)
    ax2.set_yticks(y)
    ax2.set_yticklabels(rate_labels, fontsize=9.8, fontweight="bold")
    ax2.invert_yaxis()
    ax2.set_xlabel("同速率主机每请求真实 CPU 周期（剩余算力法，越短越好）", fontsize=10.5, labelpad=8)
    ax2.set_title(
        f"B. 同速率主机 CPU 周期：小请求省 {r_half_up(cpu_save_pcts[0])}%，4 KiB 仍省 {r_half_up(cpu_save_pcts[3])}%",
        fontsize=11.4, fontweight="bold", pad=12,
    )

    fig.suptitle(
        f"MRV-195 核心结论：真实 100GbE KV 服务下，DirectPush 提速高达 {r_half_up(max(tp_pcts))}%、主机省核 {r_half_up(min(cpu_save_pcts))}%–{r_half_up(max(cpu_save_pcts))}%",
        fontsize=13.0, fontweight="bold", y=0.97,
    )
    fig.text(
        0.5, 0.015,
        f"固定配置：单核主机（实测 {host_ghz:.2f} GHz，内存哈希表）⇄ {card_cores} 核网卡 ⇄ {cli_threads} 线程客户端；每项 {n_reps} 轮交错中位数，全零错包；另测 64 B 写 +{f1(put64_pct)}%、4 KiB 写同触 {f1(put4k_gbps)} Gbps 线速。\n"
        f"*注：64/256 B 下 SDP 受 {sdp_queues} 队列上限约束，DirectPush 受 {card_cores} 核网卡上限约束（主机核占 {r_half_up(min(small_busy))}%–{r_half_up(max(small_busy))}%）；≥ 1 KiB 与混合负载打满主机单核（{r_half_up(min(large_busy))}%）。右图 {r_half_up(tau_app_ns)} ns 短量子含空轮询税（~{r_half_up(ep_cyc_sdp)} vs {r_half_up(ep_cyc_ds)} 周期/轮），扣税推算净省核 {f1(min(net_save_pcts))}%–{f1(max(net_save_pcts))}%。",
        ha="center", va="bottom", fontsize=8.1, color="#475569",
    )
    fig.tight_layout(rect=[0.0, 0.075, 1.0, 0.925])

    png = mrvplot.HERE / "mrv-195-throughput-cpu.png"
    svg = mrvplot.HERE / "mrv-195-throughput-cpu.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
