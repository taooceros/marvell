"""MRV-195 Figure 2: Open-loop Poisson load vs end-to-end p99 tail latency
knee curves (zero coordinated omission) for 64 B GET (Zipf) and Mixed small/large
KV workload (Zipf, showing small-request p99 and overall p99).

Plots strictly full n=3 verified median points from `logs/mrv195-2026-11-25/runs.jsonl`
and marks overload knees directly from `logs/mrv195-2026-11-25/attempts.jsonl`.
All numbers shown in the figure are computed dynamically from the raw jsonl files.
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
ATTS_PATH = ROOT / "logs/mrv195-2026-11-25/attempts.jsonl"


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
    runs = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    atts = [json.loads(ln) for ln in ATTS_PATH.read_text().splitlines() if ln.strip()]

    curves = defaultdict(lambda: defaultdict(list))
    w1 = defaultdict(list)
    for r in runs:
        assert_verified(r)
        p = r["part"]
        if p in ("part2b_get64_zipf", "part2b_mix_zipf", "part2b_mix_uniform"):
            curves[(p, r["policy"])][int(round(r["rate_krps"]))].append(r)
        elif p == "part2a_w1" and r["workload"] == "get64":
            w1[r["policy"]].append(r)

    ov_info = {}
    ov_by_pp = defaultdict(lambda: defaultdict(list))
    for a in atts:
        if a["part"] in ("part2b_get64_zipf", "part2b_mix_zipf") and a.get("overload"):
            ov_by_pp[(a["part"], a["policy"])][int(round(a["rate_krps"]))].append(a)
    for key, by_r in ov_by_pp.items():
        for rk in sorted(by_r):
            if len(curves[key].get(rk, [])) < 3:
                has_drop = any(a["client"]["lost"] > 0 for a in by_r[rk])
                ov_info[key] = (rk / 1000.0, has_drop)
                break

    return curves, w1, ov_info


def full_n3_xy(by_rate, metric, n_req):
    xs, ys = [], []
    for rk in sorted(by_rate):
        rs = by_rate[rk]
        if len(rs) < n_req:
            continue
        xs.append(med([r["client"]["achieved_mrps"] for r in rs]))
        ys.append(med([r["client"][metric] for r in rs]))
    return np.array(xs), np.array(ys)


def main():
    curves, w1, ov_info = load_data()

    n_reps = len(w1["sdp_fair"])
    sdp_queues = int(med([r["card_queues"] for r in w1["sdp_fair"]]))

    pols = [
        ("sdp_fair", f"最优异步 SDP（{sdp_queues} 队列）", "#64748b", "s"),
        ("direct_peek", "DirectPush（标准窥探版）", "#2563eb", "^"),
        ("direct_safe", "DirectPush（安全直取版）", "#1a7f37", "o"),
    ]

    g64_sdp_max = max(rk for rk, rs in curves[("part2b_get64_zipf", "sdp_fair")].items() if len(rs) == n_reps)
    g64_ds_max = max(rk for rk, rs in curves[("part2b_get64_zipf", "direct_safe")].items() if len(rs) == n_reps)
    g64_cap_pct = (g64_ds_max - g64_sdp_max) / g64_sdp_max * 100.0

    g64_sdp_p99 = med([r["client"]["tot_p99_us"] for r in curves[("part2b_get64_zipf", "sdp_fair")][g64_sdp_max]])
    g64_ds_p99 = med([r["client"]["tot_p99_us"] for r in curves[("part2b_get64_zipf", "direct_safe")][g64_sdp_max]])
    g64_p99_drop = (g64_ds_p99 - g64_sdp_p99) / g64_sdp_p99 * 100.0
    g64_ds_top_p99 = med([r["client"]["tot_p99_us"] for r in curves[("part2b_get64_zipf", "direct_safe")][g64_ds_max]])

    mix_sdp_max = max(rk for rk, rs in curves[("part2b_mix_zipf", "sdp_fair")].items() if len(rs) == n_reps)
    mix_ds_max = max(rk for rk, rs in curves[("part2b_mix_zipf", "direct_safe")].items() if len(rs) == n_reps)
    mix_cap_pct = (mix_ds_max - mix_sdp_max) / mix_sdp_max * 100.0

    mix_sdp_sm = med([r["client"]["small_p99_us"] for r in curves[("part2b_mix_zipf", "sdp_fair")][mix_sdp_max]])
    mix_ds_sm = med([r["client"]["small_p99_us"] for r in curves[("part2b_mix_zipf", "direct_safe")][mix_sdp_max]])
    mix_sm_drop = (mix_ds_sm - mix_sdp_sm) / mix_sdp_sm * 100.0
    mix_ds_top_sm = med([r["client"]["small_p99_us"] for r in curves[("part2b_mix_zipf", "direct_safe")][mix_ds_max]])
    mix_ds_top_tot = med([r["client"]["tot_p99_us"] for r in curves[("part2b_mix_zipf", "direct_safe")][mix_ds_max]])

    # Compute mix_uniform matched-rate small-request p99 drop dynamically from runs.jsonl
    mu_sdp_max = max(rk for rk, rs in curves[("part2b_mix_uniform", "sdp_fair")].items() if len(rs) == n_reps)
    mu_sdp_sm = med([r["client"]["small_p99_us"] for r in curves[("part2b_mix_uniform", "sdp_fair")][mu_sdp_max]])
    mu_ds_sm = med([r["client"]["small_p99_us"] for r in curves[("part2b_mix_uniform", "direct_safe")][mu_sdp_max]])
    mu_sm_drop = (mu_ds_sm - mu_sdp_sm) / mu_sdp_sm * 100.0

    w1_sdp = med([r["client"]["tot_p99_us"] for r in w1["sdp_fair"]])
    w1_ds = med([r["client"]["tot_p99_us"] for r in w1["direct_safe"]])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.0, 6.2))

    # ---- Panel A: 64 B GET (Zipf) ----
    for pol, label, col, mk in pols:
        xs, ys = full_n3_xy(curves[("part2b_get64_zipf", pol)], "tot_p99_us", n_reps)
        w1_x = med([r["client"]["achieved_mrps"] for r in w1[pol]])
        w1_y = med([r["client"]["tot_p99_us"] for r in w1[pol]])
        ax1.plot([w1_x], [w1_y], marker=mk, markersize=6.8, markerfacecolor="white",
                 markeredgecolor=col, markeredgewidth=1.9, linestyle="none", zorder=5)
        ax1.plot([w1_x, xs[0]], [w1_y, ys[0]], color=col, linewidth=1.2, linestyle=":", alpha=0.65, zorder=2)
        ax1.plot(xs, ys, marker=mk, markersize=7.0, linewidth=2.5, color=col, label=label, zorder=4)

    sdp_ov_64, sdp_drop_64 = ov_info[("part2b_get64_zipf", "sdp_fair")]
    ds_ov_64, ds_drop_64 = ov_info[("part2b_get64_zipf", "direct_safe")]
    ax1.axvline(sdp_ov_64, color="#dc2626", linestyle="--", linewidth=1.5, alpha=0.85, zorder=1)
    ax1.text(
        sdp_ov_64 - 0.16, 28.6,
        f"SDP 在 {f1(sdp_ov_64)}M\n{'队列溢出丢包' if sdp_drop_64 else '达并发上限'}",
        ha="right", va="top", fontsize=9.2, fontweight="bold", color="#b91c1c",
        bbox=dict(boxstyle="round,pad=0.30", facecolor="#fef2f2", edgecolor="#fca5a5", alpha=0.96),
    )
    ax1.axvline(ds_ov_64, color="#1a7f37", linestyle="--", linewidth=1.4, alpha=0.78, zorder=1)
    ax1.text(
        ds_ov_64 - 0.16, 22.8,
        f"DirectPush 在 {f1(ds_ov_64)}M\n{'丢包' if ds_drop_64 else '触并发上限（零丢包）'}",
        ha="right", va="top", fontsize=8.9, fontweight="bold", color="#116329",
        bbox=dict(boxstyle="round,pad=0.30", facecolor="#f0fdf4", edgecolor="#86efac", alpha=0.96),
    )

    x_cmp = g64_sdp_max / 1000.0
    ax1.annotate(
        "", xy=(x_cmp, g64_ds_p99 + 0.55), xytext=(x_cmp, g64_sdp_p99 - 0.55),
        arrowprops=dict(arrowstyle="<->", color="#116329", lw=1.9),
    )
    ax1.text(
        x_cmp - 0.25, (g64_sdp_p99 + g64_ds_p99) / 2.0 + 0.3,
        f"{f1(g64_sdp_p99)} → {f1(g64_ds_p99)} µs\n({f1(g64_p99_drop)}%)",
        ha="right", va="center", fontsize=9.6, fontweight="bold", color="#116329",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="white", edgecolor="#86efac", alpha=0.97),
        zorder=6,
    )
    ax1.annotate(
        f"{f1(g64_ds_max / 1000.0)}M 仍仅\n{f1(g64_ds_top_p99)} µs",
        xy=(g64_ds_max / 1000.0, g64_ds_top_p99 - 0.5),
        xytext=(g64_ds_max / 1000.0, 4.6),
        ha="center", va="center",
        fontsize=9.2, fontweight="bold", color="#116329",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#f0fdf4", edgecolor="#86efac", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#1a7f37", lw=1.5),
        zorder=6,
    )
    ax1.text(0.08, w1_sdp + 2.3, f"空载 {f1(w1_sdp)} µs", ha="left", va="bottom", fontsize=8.6, color="#475569")
    ax1.text(0.15, w1_ds - 2.4, f"空载 {f1(w1_ds)} µs", ha="left", va="top", fontsize=8.6, color="#116329", fontweight="bold")

    ax1.set_xlim(-0.25, ds_ov_64 + 0.65)
    ax1.set_ylim(0, 30.2)
    ax1.set_xlabel("每秒发送请求数（百万请求/秒，开环泊松）", fontsize=10.5, labelpad=8)
    ax1.set_ylabel("端到端 p99 尾延迟（µs，越低越好）", fontsize=10.5)
    ax1.set_title(
        f"A. 纯 64 B 读（热点）：尾延迟降 {f1(abs(g64_p99_drop))}%，无损上限高 {r_half_up(g64_cap_pct)}%",
        fontsize=11.4, fontweight="bold", pad=12,
    )
    ax1.legend(loc="upper left", frameon=True, facecolor="white", edgecolor="#cbd5e1", framealpha=0.96, fontsize=9.3)

    # ---- Panel B: Mixed workload (Zipf) ----
    for pol, label, col, mk in pols:
        xs, ys = full_n3_xy(curves[("part2b_mix_zipf", pol)], "small_p99_us", n_reps)
        ax2.plot(xs, ys, marker=mk, markersize=6.8, linewidth=2.5, color=col, label=f"{label}（小请求）", zorder=4)

    for pol, lbl_dash, col in [
        ("sdp_fair", "SDP 整体 p99（含大包）", "#64748b"),
        ("direct_safe", "DirectPush 安全直取 整体 p99", "#1a7f37"),
    ]:
        xs, ys = full_n3_xy(curves[("part2b_mix_zipf", pol)], "tot_p99_us", n_reps)
        ax2.plot(xs, ys, linestyle="--", linewidth=1.7, color=col, alpha=0.78, label=lbl_dash, zorder=3)

    sdp_ov_m, sdp_drop_m = ov_info[("part2b_mix_zipf", "sdp_fair")]
    ds_ov_m, ds_drop_m = ov_info[("part2b_mix_zipf", "direct_safe")]
    ax2.axvline(sdp_ov_m, color="#dc2626", linestyle="--", linewidth=1.5, alpha=0.85, zorder=1)
    ax2.text(
        sdp_ov_m - 0.08, 46.2,
        f"SDP 在 {f1(sdp_ov_m)}M\n{'队列溢出丢包' if sdp_drop_m else '达并发上限'}",
        ha="right", va="top", fontsize=9.1, fontweight="bold", color="#b91c1c",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#fef2f2", edgecolor="#fca5a5", alpha=0.96),
        zorder=6,
    )
    ax2.axvline(ds_ov_m, color="#1a7f37", linestyle="--", linewidth=1.4, alpha=0.78, zorder=1)
    ax2.text(
        ds_ov_m - 0.08, 46.2,
        f"DirectPush 在 {f1(ds_ov_m)}M\n{'丢包' if ds_drop_m else '触并发上限（零丢包）'}",
        ha="right", va="top", fontsize=8.8, fontweight="bold", color="#116329",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#f0fdf4", edgecolor="#86efac", alpha=0.96),
        zorder=6,
    )

    xm_cmp = mix_sdp_max / 1000.0
    ax2.annotate(
        "", xy=(xm_cmp, mix_ds_sm + 0.65), xytext=(xm_cmp, mix_sdp_sm - 0.65),
        arrowprops=dict(arrowstyle="<->", color="#116329", lw=1.9),
        zorder=5,
    )
    ax2.annotate(
        f"{f1(xm_cmp)}M 小请求：\n{f1(mix_sdp_sm)} → {f1(mix_ds_sm)} µs ({f1(mix_sm_drop)}%)",
        xy=(xm_cmp, (mix_sdp_sm + mix_ds_sm) / 2.0),
        xytext=(2.35, 5.2),
        ha="center", va="center",
        fontsize=9.1, fontweight="bold", color="#116329",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="white", edgecolor="#86efac", alpha=0.97),
        arrowprops=dict(arrowstyle="->", color="#116329", lw=1.4, connectionstyle="arc3,rad=0.16"),
        zorder=6,
    )
    ax2.annotate(
        f"{f1(mix_ds_max / 1000.0)}M 无损：\n小请求 {f1(mix_ds_top_sm)} / 整体 {f1(mix_ds_top_tot)} µs",
        xy=(mix_ds_max / 1000.0, mix_ds_top_sm - 0.6),
        xytext=(4.95, 10.2),
        ha="center", va="center",
        fontsize=8.9, fontweight="bold", color="#116329",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#f0fdf4", edgecolor="#86efac", alpha=0.96),
        arrowprops=dict(arrowstyle="->", color="#1a7f37", lw=1.5),
        zorder=6,
    )

    ax2.set_xlim(0.2, ds_ov_m + 0.42)
    ax2.set_ylim(0, 48.0)
    ax2.set_xlabel("每秒发送请求数（百万请求/秒，开环泊松）", fontsize=10.5, labelpad=8)
    ax2.set_ylabel("端到端 p99 尾延迟（µs，越低越好）", fontsize=10.5)
    ax2.set_title(
        f"B. 混合大小值（热点）：小请求尾延迟降 {f1(abs(mix_sm_drop))}%，无损上限高 {r_half_up(mix_cap_pct)}%",
        fontsize=11.4, fontweight="bold", pad=12,
    )
    ax2.legend(loc="upper left", frameon=True, facecolor="white", edgecolor="#cbd5e1", framealpha=0.96, fontsize=8.6)

    fig.suptitle(
        f"MRV-195 开环泊松尾延迟拐点：DirectPush 尾延迟砍半，无损承载上限高 {r_half_up(min(g64_cap_pct, mix_cap_pct))}%–{r_half_up(max(g64_cap_pct, mix_cap_pct))}%",
        fontsize=13.0, fontweight="bold", y=0.97,
    )
    fig.text(
        0.5, 0.015,
        f"仅绘全部 {n_reps}/{n_reps} 轮零错包通过点的中位数（零协调省略，空心点为空载单请求基线）；过载虚线由原始重试日志标出（DirectPush 全程零非物理错包丢包）。\n"
        f"SDP 混合负载中小请求与整体 p99 几乎重合（大包阻塞队列）；DirectPush 小请求直推绕开大包 DMA。均匀键趋势一致（{f1(mu_sdp_max / 1000.0)}M 小请求降 {f1(abs(mu_sm_drop))}%）。",
        ha="center", va="bottom", fontsize=8.7, color="#475569",
    )
    fig.tight_layout(rect=[0.0, 0.075, 1.0, 0.925])

    png = mrvplot.HERE / "mrv-195-poisson-knee.png"
    svg = mrvplot.HERE / "mrv-195-poisson-knee.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
