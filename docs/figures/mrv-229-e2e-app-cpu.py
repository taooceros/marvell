#!/usr/bin/env python3
"""MRV-229 Figure 2: Real end-to-end application speedup (honest Amdahl compression
including weak/flat large-value workloads) and host CPU cycle savings vs fair async
SDP (fair-full), including exact DPDK stack share on 1-4 KiB.

100% dynamically computed from archived raw verified logs (zero hardcoded numbers):
  - logs/mrv195-2026-11-25/runs.jsonl   (real 100GbE light KV sat & open-loop Poisson knee)
  - logs/mrv206-2026-11-26/runs.jsonl   (real 100GbE unmodified Memcached 1.6.45 sat, knee & net active CPU)
  - logs/mrv206-2026-11-26/env.json     (Memcached idle calibration)
  - logs/mrv166-2026-11-23/runs.jsonl   (same-card host CPU cycles & DPDK stack share vs fair-full SDP)

Run:
    uv run --with matplotlib python docs/figures/mrv-229-e2e-app-cpu.py
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

C_SDP = "#64748b"    # Fair async SDP slate grey
C_PEEK = "#2563eb"   # DirectPush cqe-peek blue
C_SAFE = "#16a34a"   # DirectPush cqe-direct-safe emerald


def med(xs):
    return statistics.median(xs)


def load_mrv195():
    path = ROOT / "logs/mrv195-2026-11-25/runs.jsonl"
    sat = defaultdict(list)
    pois = defaultdict(lambda: defaultdict(list))
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        assert r.get("verified") is True
        p = r.get("part")
        if p == "part1_sat":
            assert r["client"]["lost"] == 0 and r["client"]["bad"] == 0
            sat[(r["policy"], r["workload"], r["key_dist"])].append(r)
        elif p in ("part2b_get64_zipf", "part2b_mix_zipf"):
            assert r["client"]["lost"] == 0 and r["client"].get("window_cap_hits", 0) == 0
            pois[(p, r["policy"])][int(round(r["rate_krps"]))].append(r)

    def sat_mrps(pol, wl, kd):
        rs = sat[(pol, wl, kd)]
        assert len(rs) == 3
        return med([r["client"]["achieved_mrps"] for r in rs])

    def max_n3_mrps(part, pol):
        return max(rk for rk, rs in pois[(part, pol)].items() if len(rs) == 3) / 1000.0

    pols = ("sdp_fair", "direct_peek", "direct_safe")
    return {
        "sat_get64": {p: sat_mrps(p, "get64", "zipf") for p in pols},
        "sat_get4096": {p: sat_mrps(p, "get4096", "zipf") for p in pols},
        "knee_get64": {p: max_n3_mrps("part2b_get64_zipf", p) for p in pols},
        "knee_mix": {p: max_n3_mrps("part2b_mix_zipf", p) for p in pols},
    }


def load_mrv206():
    runs_path = ROOT / "logs/mrv206-2026-11-26/runs.jsonl"
    env_path = ROOT / "logs/mrv206-2026-11-26/env.json"
    rows = [json.loads(ln) for ln in runs_path.read_text().splitlines() if ln.strip()]
    env = json.loads(env_path.read_text())
    idle_kps = env["idle_cal"]["app_work_kps"]

    by_id = defaultdict(list)
    pois = defaultdict(lambda: defaultdict(list))
    for r in rows:
        assert r.get("verified") is True
        c, h, k = r["client"], r["host"], r["card"]
        assert c["lost"] == 0 and c["bad"] == 0 and c["torn"] == 0
        assert h["bad"] == 0 and h["torn"] == 0 and k["bad_wire"] == 0
        by_id[(r["section"], r["run_id"])].append(r)
        if r["section"] == "poisson":
            assert c.get("window_cap_hits", 0) == 0
            pois[(r["policy"], r["workload"])][int(round(r["rate_krps"]))].append(r)

    # Empty-poll wall time and cycle regression (exact MRV-206 Fig 1 method)
    ep_ns, ep_cyc = {}, {}
    for pol in ("sdp_fair", "direct_peek", "direct_safe"):
        pts = [r for r in rows if r["section"] == "poisson" and r["policy"] == pol and r["rate_krps"] in (200, 500, 1000)]
        xs = [r["host"]["rx_rpcs"] / r["host"]["dt_s"] for r in pts]
        ys = [r["host"]["empty_polls"] / r["host"]["dt_s"] for r in pts]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        ep_ns[pol] = 1e9 / (my - slope * mx)

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

    def sat_mrps(pol, wkey):
        rs = by_id[("sat", f"sat_{pol}_{wkey}_w64")]
        assert len(rs) == 3
        return med([r["client"]["achieved_mrps"] for r in rs])

    def spare_net_cyc(pol, sk):
        rs = by_id[("spare_cpu", f"spare_{pol}_{sk}")]
        assert len(rs) == 3
        return med([
            (1.0 - r["host"]["app_work_kps"] / idle_kps - r["host"]["empty_polls"] * ep_ns[pol] / (r["host"]["dt_s"] * 1e9))
            * r["host"]["pmu"]["ghz"] * 1000.0 / r["client"]["achieved_mrps"]
            for r in rs
        ])

    def sat_net_cyc(pol, wkey):
        rs = by_id[("sat", f"sat_{pol}_{wkey}_w64")]
        return med([
            r["host"]["pmu"]["cycles"] / r["host"]["rx_rpcs"]
            - (r["host"]["empty_polls"] / r["host"]["rx_rpcs"]) * ep_cyc[pol]
            for r in rs
        ])

    pols = ("sdp_fair", "direct_peek", "direct_safe")
    get8_n3 = {p: max(rk for rk, rs in pois[(p, "get8")].items() if len(rs) == 3) / 1000.0 for p in pols}
    etc_n3 = {p: max(rk for rk, rs in pois[(p, "etc_mix")].items() if len(rs) == 3) / 1000.0 for p in ("sdp_fair", "direct_safe")}
    etc_peek_n2 = max(rk for rk, rs in pois[("direct_peek", "etc_mix")].items() if len(rs) >= 2) / 1000.0
    etc_peek_stall_mrps = min(rk for rk, rs in pois[("direct_peek", "etc_mix")].items() if 0 < len(rs) < 3) / 1000.0

    spare_g8_rate = med([r["rate_krps"] for r in by_id[("spare_cpu", "spare_sdp_fair_get8_1500k")]]) / 1000.0
    spare_etc_rate = med([r["rate_krps"] for r in by_id[("spare_cpu", "spare_sdp_fair_etc_mix_1200k")]]) / 1000.0

    return {
        "sat_get8": {p: sat_mrps(p, "get8_zipf") for p in pols},
        "sat_get128": {p: sat_mrps(p, "get128_zipf") for p in pols},
        "sat_etc": {p: sat_mrps(p, "etc_mix_zipf") for p in pols},
        "sat_get3840": {p: sat_mrps(p, "get3840_zipf") for p in pols},
        "knee_get8": get8_n3,
        "knee_etc": {
            "sdp_fair": etc_n3["sdp_fair"],
            "direct_peek_n2": etc_peek_n2,
            "direct_safe": etc_n3["direct_safe"],
            "peek_stall_mrps": etc_peek_stall_mrps,
        },
        "spare_get8": {p: spare_net_cyc(p, "get8_1500k") for p in ("sdp_fair", "direct_safe")},
        "spare_etc": {p: spare_net_cyc(p, "etc_mix_1200k") for p in ("sdp_fair", "direct_safe")},
        "spare_g8_rate": spare_g8_rate,
        "spare_etc_rate": spare_etc_rate,
        "sat_net_get8": {p: sat_net_cyc(p, "get8_zipf") for p in ("sdp_fair", "direct_safe")},
        "sat_net_etc": {p: sat_net_cyc(p, "etc_mix_zipf") for p in ("sdp_fair", "direct_safe")},
    }


def load_mrv166_cpu():
    path = ROOT / "logs/mrv166-2026-11-23/runs.jsonl"
    rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    idle_map = {r["rep"]: r["units_per_s"] for r in rows if r["part"] == "part0_cal"}
    idle_cyc_u = {r["rep"]: r["host_pmu"]["cycles"] / r["units"] for r in rows if r["part"] == "part0_cal"}
    by = defaultdict(list)
    for r in rows:
        assert r.get("verified") is True
        by[(r["part"], r["cfg"])].append(r)

    ep_cyc = {}
    for zcfg in ("zero_sdp_sync", "zero_sdp_async", "zero_sdp_fair", "zero_directpush"):
        rs = by[("part0_zero", zcfg)]
        ep_cyc[zcfg] = med([(r["host_pmu"]["cycles"] - r["app_work_units"] * idle_cyc_u[r["rep"]]) / r["polls"] for r in rs])

    def tot_cyc(part, cfg):
        rs = by[(part, cfg)]
        assert len(rs) == 3
        for r in rs:
            assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
        return med([(1.0 - r["app_work_per_s"] / idle_map[r["rep"]]) * r["host_pmu"]["cycles"] / r["measured"] for r in rs])

    def act_cyc(part, cfg, zcfg):
        rs = by[(part, cfg)]
        return med([
            (1.0 - r["app_work_per_s"] / idle_map[r["rep"]]) * r["host_pmu"]["cycles"] / r["measured"]
            - r["empty_polls"] / r["measured"] * ep_cyc[zcfg]
            for r in rs
        ])

    # Compute exact DPDK software stack share of the 1 KiB & 4 KiB cycle gap (after removing CN106 dual-line tail-poll tax)
    tail_tax_1k = act_cyc("part3_size_cpu", "sdp_async_1024_r1000", "zero_sdp_async") - act_cyc("part3_size_cpu", "sdp_sync_1024_r1000", "zero_sdp_sync")
    f1_1k, d1_1k = tot_cyc("part3_size_cpu", "sdp_fair_1024_r1000"), tot_cyc("part3_size_cpu", "directpush_1024_r1000")
    af1_1k, ad1_1k = act_cyc("part3_size_cpu", "sdp_fair_1024_r1000", "zero_sdp_fair"), act_cyc("part3_size_cpu", "directpush_1024_r1000", "zero_directpush")
    dpdk_cyc_1k = (af1_1k - tail_tax_1k) - ad1_1k
    dpdk_share_1k = dpdk_cyc_1k / (f1_1k - d1_1k) * 100.0

    f1_4k, d1_4k = tot_cyc("part2_4k_ablation", "sdp_fair_4096_r1000"), tot_cyc("part2_4k_ablation", "directpush_4096_r1000")
    af1_4k, ad1_4k = act_cyc("part2_4k_ablation", "sdp_fair_4096_r1000", "zero_sdp_fair"), act_cyc("part2_4k_ablation", "directpush_4096_r1000", "zero_directpush")
    dpdk_cyc_4k = (af1_4k - tail_tax_1k) - ad1_4k
    dpdk_share_4k = dpdk_cyc_4k / (f1_4k - d1_4k) * 100.0

    return {
        "64_b1": (tot_cyc("part3_size_cpu", "sdp_fair_64_r1500"), tot_cyc("part3_size_cpu", "directpush_64_r1500")),
        "64_b8": (tot_cyc("part3_size_cpu", "sdp_fair_b8_64_r1500"), tot_cyc("part3_size_cpu", "directpush_b8_64_r1500")),
        "1k_b1": (f1_1k, d1_1k),
        "4k_b1": (f1_4k, d1_4k),
        "dpdk_cyc_1k": dpdk_cyc_1k,
        "dpdk_cyc_4k": dpdk_cyc_4k,
        "dpdk_share_1k": dpdk_share_1k,
        "dpdk_share_4k": dpdk_share_4k,
    }


def main():
    d195 = load_mrv195()
    d206 = load_mrv206()
    d166 = load_mrv166_cpu()

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(16.4, 7.6), gridspec_kw={"width_ratios": [1.14, 1.0]}
    )

    # =========================================================================
    # Panel A: Full honest spectrum of 100GbE E2E speedup (including weak/flat)
    # =========================================================================
    kv_rows = [
        (
            "轻 KV · 64 B 饱和 (MRV-195)",
            d195["sat_get64"]["direct_peek"] / d195["sat_get64"]["sdp_fair"],
            d195["sat_get64"]["direct_safe"] / d195["sat_get64"]["sdp_fair"],
            f"{d195['sat_get64']['sdp_fair']:.1f}→{d195['sat_get64']['direct_safe']:.1f}M",
            False,
        ),
        (
            "轻 KV · 64 B 拐点 (MRV-195)",
            d195["knee_get64"]["direct_peek"] / d195["knee_get64"]["sdp_fair"],
            d195["knee_get64"]["direct_safe"] / d195["knee_get64"]["sdp_fair"],
            f"{d195['knee_get64']['sdp_fair']:.1f}→{d195['knee_get64']['direct_safe']:.1f}M",
            False,
        ),
        (
            "轻 KV · 混合拐点 (MRV-195)",
            d195["knee_mix"]["direct_peek"] / d195["knee_mix"]["sdp_fair"],
            d195["knee_mix"]["direct_safe"] / d195["knee_mix"]["sdp_fair"],
            f"{d195['knee_mix']['sdp_fair']:.1f}→{d195['knee_mix']['direct_safe']:.1f}M",
            False,
        ),
        (
            "轻 KV · 4 KiB 饱和 (MRV-195)",
            d195["sat_get4096"]["direct_peek"] / d195["sat_get4096"]["sdp_fair"],
            d195["sat_get4096"]["direct_safe"] / d195["sat_get4096"]["sdp_fair"],
            f"{d195['sat_get4096']['sdp_fair']:.2f}→{d195['sat_get4096']['direct_safe']:.2f}M",
            False,
        ),
    ]
    mc_rows = [
        (
            "原版 MC · ETC 拐点 (MRV-206)",
            d206["knee_etc"]["direct_peek_n2"] / d206["knee_etc"]["sdp_fair"],
            d206["knee_etc"]["direct_safe"] / d206["knee_etc"]["sdp_fair"],
            f"{d206['knee_etc']['sdp_fair']:.1f}→{d206['knee_etc']['direct_safe']:.1f}M",
            True,
        ),
        (
            "原版 MC · ETC 饱和 (MRV-206)",
            d206["sat_etc"]["direct_peek"] / d206["sat_etc"]["sdp_fair"],
            d206["sat_etc"]["direct_safe"] / d206["sat_etc"]["sdp_fair"],
            f"{d206['sat_etc']['sdp_fair']:.2f}→{d206['sat_etc']['direct_safe']:.2f}M",
            False,
        ),
        (
            "原版 MC · 8 B 饱和 (MRV-206)",
            d206["sat_get8"]["direct_peek"] / d206["sat_get8"]["sdp_fair"],
            d206["sat_get8"]["direct_safe"] / d206["sat_get8"]["sdp_fair"],
            f"{d206['sat_get8']['sdp_fair']:.2f}→{d206['sat_get8']['direct_safe']:.2f}M",
            False,
        ),
        (
            "原版 MC · 8 B 拐点 (MRV-206)",
            d206["knee_get8"]["direct_peek"] / d206["knee_get8"]["sdp_fair"],
            d206["knee_get8"]["direct_safe"] / d206["knee_get8"]["sdp_fair"],
            f"均达 {d206['knee_get8']['direct_safe']:.1f}M 顶",
            False,
        ),
        (
            "原版 MC · 3.8 KiB 饱和 (MRV-206)",
            d206["sat_get3840"]["direct_peek"] / d206["sat_get3840"]["sdp_fair"],
            d206["sat_get3840"]["direct_safe"] / d206["sat_get3840"]["sdp_fair"],
            f"{d206['sat_get3840']['sdp_fair']:.2f}→{d206['sat_get3840']['direct_safe']:.2f}M",
            False,
        ),
        (
            "原版 MC · 128 B 饱和 (MRV-206)",
            d206["sat_get128"]["direct_peek"] / d206["sat_get128"]["sdp_fair"],
            d206["sat_get128"]["direct_safe"] / d206["sat_get128"]["sdp_fair"],
            f"{d206['sat_get128']['sdp_fair']:.2f}→{d206['sat_get128']['direct_safe']:.2f}M",
            False,
        ),
    ]
    app_rows = kv_rows + mc_rows

    # Generous vertical gap between Light KV (4 rows) and Unmodified Memcached (6 rows)
    y_kv = np.array([10.5, 9.5, 8.5, 7.5])
    y_mc = np.array([5.7, 4.7, 3.7, 2.7, 1.7, 0.7])
    y = np.concatenate([y_kv, y_mc])
    h = 0.36

    ax1.axvline(1.0, color=C_SDP, linestyle="--", linewidth=1.6, zorder=2, label="公平异步 SDP (1.0×)")

    for i, (lbl, r_peek, r_safe, detail, peek_n2) in enumerate(app_rows):
        yi = y[i]
        ax1.barh(
            yi + h / 2, r_peek, height=h * 0.88, color=C_PEEK,
            alpha=0.68 if peek_n2 else 0.92,
            hatch="//" if peek_n2 else None, edgecolor="white",
            label="DirectPush 窥探版" if i == 0 else None,
        )
        ax1.barh(
            yi - h / 2, r_safe, height=h * 0.88, color=C_SAFE, alpha=0.92, edgecolor="white",
            label="DirectPush 安全直取版" if i == 0 else None,
        )
        pk_tag = f"{r_peek:.2f}× (2/3轮*)" if peek_n2 else f"{r_peek:.2f}×"
        ax1.text(r_peek + 0.018, yi + h / 2, pk_tag, va="center", ha="left",
                 fontsize=8.6, fontweight="bold", color=C_PEEK)
        ax1.text(r_safe + 0.018, yi - h / 2, f"{r_safe:.2f}× ({detail})", va="center", ha="left",
                 fontsize=8.6, fontweight="bold", color=C_SAFE if r_safe >= 1.03 else "#475569")

    # Dynamically compute range annotations for both groups (zero hardcoding)
    kv_safe_vals = [r[2] for r in kv_rows]
    mc_safe_vals = [r[2] for r in mc_rows]
    ax1.axhline(6.6, color="#cbd5e1", linestyle=":", linewidth=1.4)
    ax1.text(
        1.96, 6.72,
        f"↑ 轻 KV：小/混合 {min(kv_safe_vals[:3]):.2f}×–{max(kv_safe_vals[:3]):.2f}×，4 KiB 降至 {kv_safe_vals[3]:.2f}×",
        ha="right", va="bottom", fontsize=8.5, fontweight="bold", color="#334155",
    )
    ax1.text(
        1.96, 6.48,
        f"↓ 原版 Memcached：Amdahl 压缩至 {min(mc_safe_vals):.2f}×–{max(mc_safe_vals):.2f}×（含大值/平手项）",
        ha="right", va="top", fontsize=8.5, fontweight="bold", color="#475569",
    )

    ax1.set_yticks(y)
    ax1.set_yticklabels([r[0] for r in app_rows], fontsize=9.0, fontweight="bold")
    ax1.set_xlabel("相对公平异步 SDP 的端到端加速比（倍，1.0× = 持平）", fontsize=10.0)
    ax1.set_xlim(0.75, 2.00)
    ax1.set_ylim(0.0, 11.2)
    ax1.set_title("A. 真实双机 100GbE 应用端到端加速比（完整全景）", fontsize=11.4, fontweight="bold", pad=28)
    ax1.legend(
        loc="lower center", bbox_to_anchor=(0.48, 1.005), ncol=3,
        fontsize=8.6, frameon=True, facecolor="white", framealpha=0.95,
    )

    # =========================================================================
    # Panel B: Host CPU Cycles Saved per Packet / Request (vs SDP fair-full)
    # =========================================================================
    cpu_rows = [
        ("64 B\n单包 B=1\n(MRV-166)", d166["64_b1"][0], d166["64_b1"][1], False),
        ("64 B\n批收 B=8\n(MRV-166)", d166["64_b8"][0], d166["64_b8"][1], False),
        ("1 KiB*\n单包 B=1\n(MRV-166)", d166["1k_b1"][0], d166["1k_b1"][1], False),
        ("4 KiB*\n单包 B=1\n(MRV-166)", d166["4k_b1"][0], d166["4k_b1"][1], False),
        ("原版 MC\n8 B 读†\n(MRV-206)", d206["spare_get8"]["sdp_fair"], d206["spare_get8"]["direct_safe"], True),
        ("原版 MC\nETC 混合†\n(MRV-206)", d206["spare_etc"]["sdp_fair"], d206["spare_etc"]["direct_safe"], True),
    ]

    x2 = np.arange(len(cpu_rows))
    w2 = 0.36
    sdp_c = [r[1] for r in cpu_rows]
    dp_c = [r[2] for r in cpu_rows]

    ax2.bar(x2 - w2 / 2, sdp_c, width=w2 * 0.90, color=C_SDP, label="公平异步 SDP (fair-full)")
    ax2.bar(x2[:4] + w2 / 2, dp_c[:4], width=w2 * 0.90, color=C_PEEK, label="DirectPush（同卡裸接口）")
    ax2.bar(x2[4:] + w2 / 2, dp_c[4:], width=w2 * 0.90, color=C_SAFE, label="DirectPush（原版 Memcached）")

    mc_spare_drops = []
    for i, (_, sc, dc, is_mc) in enumerate(cpu_rows):
        drop_pct = (sc - dc) / sc * 100.0
        if is_mc:
            mc_spare_drops.append(drop_pct)
        col = C_SAFE if is_mc else C_PEEK
        ax2.text(x2[i] - w2 / 2, sc + 26, f"{sc:.0f}", ha="center", va="bottom",
                 fontsize=8.8, fontweight="bold", color="#334155")
        ax2.text(x2[i] + w2 / 2, dc + 26, f"{dc:.0f}", ha="center", va="bottom",
                 fontsize=8.8, fontweight="bold", color=col)
        ax2.text(
            x2[i], max(sc, dc) + 145, f"-{drop_pct:.0f}%",
            ha="center", va="bottom", fontsize=9.8, fontweight="bold", color=col,
            bbox=dict(boxstyle="round,pad=0.22", fc="#f0fdf4" if is_mc else "#eff6ff", ec=col, lw=1.0),
        )

    ax2.axvline(3.5, color="#cbd5e1", linestyle=":", linewidth=1.3)
    ax2.set_xticks(x2)
    ax2.set_xticklabels([r[0] for r in cpu_rows], fontsize=8.8, fontweight="bold")
    ax2.set_ylabel("每包 / 每请求主机 CPU 周期 (cyc，越低越省核)", fontsize=10.0)
    ax2.set_ylim(0, 2120)
    ax2.set_title("B. 主机核 CPU 周期节省（对比公平异步 SDP）", fontsize=11.4, fontweight="bold", pad=10)
    ax2.legend(loc="upper left", fontsize=8.4, frameon=True, facecolor="white", framealpha=0.95)

    sat_g8_drop = (d206["sat_net_get8"]["sdp_fair"] - d206["sat_net_get8"]["direct_safe"]) / d206["sat_net_get8"]["sdp_fair"] * 100.0
    sat_etc_drop = (d206["sat_net_etc"]["sdp_fair"] - d206["sat_net_etc"]["direct_safe"]) / d206["sat_net_etc"]["sdp_fair"] * 100.0

    fig.suptitle(
        "图 2 · 端到端应用与主机省核：从轻 KV 到零改原版 Memcached 1.6.45",
        fontsize=13.5, fontweight="bold", y=0.985,
    )
    fig.text(
        0.5, 0.010,
        f"注：*MRV-166 的 1–4 KiB 总周期差中，{d166['dpdk_share_1k']:.0f}%–{d166['dpdk_share_4k']:.0f}%（{d166['dpdk_cyc_1k']:.0f}–{d166['dpdk_cyc_4k']:.0f} cyc）来自 SDP 的 DPDK 协议栈软件开销；"
        f"左图 2/3 轮* 指窥探版 1 轮在 {d206['knee_etc']['peek_stall_mrps']:.1f}M 遇偶发尖峰（零丢包）。\n"
        f"†MRV-206 为固定泊松速率（{d206['spare_g8_rate']:.1f}M / {d206['spare_etc_rate']:.1f}M）扣空轮询后的净活跃周期（降 {min(mc_spare_drops):.0f}%–{max(mc_spare_drops):.0f}%）；"
        f"饱和态含完整原版 Memcached 核心总净周期降约 {(sat_etc_drop + sat_g8_drop) / 2:.0f}%（{sat_etc_drop:.1f}% / {sat_g8_drop:.1f}%）。",
        ha="center", va="bottom", fontsize=8.4, color="#475569", linespacing=1.35,
    )

    fig.tight_layout(rect=[0.0, 0.068, 1.0, 0.94])
    png = mrvplot.HERE / "mrv-229-e2e-app-cpu.png"
    svg = mrvplot.HERE / "mrv-229-e2e-app-cpu.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")
    print(f"  MRV-166 DPDK stack share: 1KiB={d166['dpdk_share_1k']:.1f}% ({d166['dpdk_cyc_1k']:.1f} cyc), 4KiB={d166['dpdk_share_4k']:.1f}% ({d166['dpdk_cyc_4k']:.1f} cyc)")
    print(f"  KV safe range: small/mix {min(kv_safe_vals[:3]):.2f}x-{max(kv_safe_vals[:3]):.2f}x, 4KiB {kv_safe_vals[3]:.2f}x")
    print(f"  MC safe range: {min(mc_safe_vals):.2f}x-{max(mc_safe_vals):.2f}x")


if __name__ == "__main__":
    main()
