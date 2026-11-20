#!/usr/bin/env python3
"""MRV-118 Figure 2: Loaded Latency-Throughput Curves through Real CN106 NIX0+LBK0
(Open-Loop Poisson & B=8 Microbursts + Closed-Loop 64B/4KiB/Mix-Ads/Mix-Geo)."""

import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

from mrvplot import save

RUNS = Path("/home/hongtao/dev/marvell-mrv118/logs/mrv118-2026-11-20/runs.jsonl")


def load_data():
    by = defaultdict(list)
    for line in RUNS.read_text().splitlines():
        r = json.loads(line)
        assert r["verified"], f"unverified run: {r.get('cfg')}"
        by[r["cfg"]].append(r)

    def med(cfg, fn):
        return statistics.median(fn(r) for r in by[cfg])

    def vals(cfg, fn):
        return [fn(r) for r in by[cfg]]

    return by, med, vals


def main():
    by, med, vals = load_data()
    fig, axes = plt.subplots(2, 2, figsize=(13.0, 9.2))
    (ax_p64, ax_pmix), (ax_c_pure, ax_c_mix) = axes

    c_sdp = "#64748B"
    c_dp = "#0F766E"
    c_b_sdp = "#B45309"
    c_b_dp = "#0369A1"

    # ---- Panel (a): 64 B Open-Loop Poisson & B=8 Microburst (W=64, tot = t_recv - t_sched) ----
    sdp_p64_r = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500]
    dp_p64_r = [500, 1000, 1500, 2000, 2500, 3000, 4000, 5000, 6000]

    sdp_x = [med(f"sdp_nix_64_poisson_r{r}", lambda d: d["mreq_s"]) for r in sdp_p64_r]
    sdp_p50 = [med(f"sdp_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p50"]) for r in sdp_p64_r]
    sdp_p99 = [med(f"sdp_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in sdp_p64_r]
    sdp_p99_lo = [sdp_p99[i] - min(vals(f"sdp_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) for i, r in enumerate(sdp_p64_r)]
    sdp_p99_hi = [max(vals(f"sdp_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) - sdp_p99[i] for i, r in enumerate(sdp_p64_r)]

    dp_x = [med(f"directpush_nix_64_poisson_r{r}", lambda d: d["mreq_s"]) for r in dp_p64_r]
    dp_p50 = [med(f"directpush_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p50"]) for r in dp_p64_r]
    dp_p99 = [med(f"directpush_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in dp_p64_r]
    dp_p99_lo = [dp_p99[i] - min(vals(f"directpush_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) for i, r in enumerate(dp_p64_r)]
    dp_p99_hi = [max(vals(f"directpush_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) - dp_p99[i] for i, r in enumerate(dp_p64_r)]

    ax_p64.plot(sdp_x, sdp_p50, "-o", color=c_sdp, lw=1.8, ms=4.2, label="SDP+NIX Poisson p50")
    ax_p64.errorbar(sdp_x, sdp_p99, yerr=[sdp_p99_lo, sdp_p99_hi], fmt="--s", color=c_sdp, lw=1.7, ms=4.2,
                    capsize=3, elinewidth=1.1, alpha=0.88, label="SDP+NIX Poisson p99 (n=3 极差)")
    ax_p64.plot(dp_x, dp_p50, "-o", color=c_dp, lw=2.2, ms=4.6, label="DirectPush+NIX Poisson p50")
    ax_p64.errorbar(dp_x, dp_p99, yerr=[dp_p99_lo, dp_p99_hi], fmt="--s", color=c_dp, lw=2.1, ms=4.6,
                    capsize=3, elinewidth=1.2, alpha=0.92, label="DirectPush+NIX Poisson p99 (n=3 极差)")

    # Individual n=3 replicate hollow markers at near-saturation high-spread points
    dp_6m_reps = sorted(vals("directpush_nix_64_poisson_r6000", lambda d: d["lat_all_us"]["p99"]))
    dp_6m_wire = med("directpush_nix_64_poisson_r6000", lambda d: d["lat_wire_all_us"]["p99"])
    dp_6m_x = dp_x[-1]
    ax_p64.scatter([dp_6m_x] * len(dp_6m_reps), dp_6m_reps, marker="o", s=34,
                   facecolors="white", edgecolors=c_dp, linewidths=1.3, zorder=7)

    sdp_45_reps = sorted(vals("sdp_nix_64_poisson_r4500", lambda d: d["lat_all_us"]["p99"]))
    sdp_45_x = sdp_x[-1]
    ax_p64.scatter([sdp_45_x] * len(sdp_45_reps), sdp_45_reps, marker="o", s=30,
                   facecolors="white", edgecolors=c_sdp, linewidths=1.2, zorder=7)

    # B=8 microburst p99 markers
    bs_r = [1000, 2000]
    bd_r = [1000, 2000, 3000, 4000]
    ax_p64.scatter(
        [med(f"sdp_nix_64_burst8_r{r}", lambda d: d["mreq_s"]) for r in bs_r],
        [med(f"sdp_nix_64_burst8_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in bs_r],
        marker="^", s=54, color=c_b_sdp, zorder=6, label="SDP+NIX B=8 微突发 p99",
    )
    ax_p64.scatter(
        [med(f"directpush_nix_64_burst8_r{r}", lambda d: d["mreq_s"]) for r in bd_r],
        [med(f"directpush_nix_64_burst8_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in bd_r],
        marker="^", s=54, color=c_b_dp, zorder=6, label="DirectPush+NIX B=8 微突发 p99",
    )

    # Single-card-core NIX0 ceiling line (6.63 M lines/s = 49% of 13.4 M/s slow cliff)
    nix_1c_cap = med("directpush_nix_64_w64", lambda d: d["mreq_s"])
    ax_p64.axvline(nix_1c_cap, color="#475569", ls=":", lw=1.4, zorder=2)
    ax_p64.text(
        nix_1c_cap - 0.10, 81.0,
        f"1C NIX0 峰值 {nix_1c_cap:.2f} M 行/s\n（仅达 13.4 M/s 慢崖 49%）",
        ha="right", va="top", fontsize=7.2, fontweight="bold", color="#334155",
        bbox=dict(boxstyle="round,pad=0.20", facecolor="#F8FAFC", edgecolor="#CBD5E1", lw=0.8),
    )

    # SDP 5.0 M/s saturation callout (placed cleanly in middle-left open space below legend)
    sdp_5m_p50 = med("sdp_nix_64_poisson_r5000", lambda d: d["lat_all_us"]["p50"])
    sdp_5m_p99 = med("sdp_nix_64_poisson_r5000", lambda d: d["lat_all_us"]["p99"])
    ax_p64.annotate(
        f"SDP @ 5.0 M/s 饱和飙升\np50={sdp_5m_p50:.0f} / p99={sdp_5m_p99:.0f} µs ↑\n(4.5M n=3: {sdp_45_reps[0]:.1f}/{sdp_45_reps[1]:.1f}/{sdp_45_reps[2]:.1f})",
        xy=(sdp_45_x, sdp_45_reps[2]), xytext=(0.45, 45.5),
        fontsize=7.3, fontweight="bold", color="#991B1B",
        bbox=dict(boxstyle="round,pad=0.23", facecolor="#FEF2F2", edgecolor="#DC2626", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#DC2626", lw=1.0),
    )
    # DirectPush 5.0 M/s callout (cleanly in lower-middle open space)
    ax_p64.annotate(
        f"DirectPush @ 5.0 M/s\np99={dp_p99[-2]:.1f} µs (未达慢崖)",
        xy=(dp_x[-2], dp_p99[-2]), xytext=(0.95, 25.0),
        fontsize=7.4, fontweight="bold", color=c_dp,
        bbox=dict(boxstyle="round,pad=0.20", facecolor="#F0FDFA", edgecolor=c_dp, lw=0.9),
        arrowprops=dict(arrowstyle="->", color=c_dp, lw=1.0),
    )
    # DirectPush 6.0 M/s n=3 replicate spread callout (cleanly above SDP 4.5M and left of 6.0M)
    ax_p64.annotate(
        f"6.0 M/s (91% 1C 上限) n=3:\ntot p99 = {dp_6m_reps[0]:.2f} / {dp_6m_reps[1]:.2f} / {dp_6m_reps[2]:.2f} µs\n(wire p99 稳在 {dp_6m_wire:.2f} µs)",
        xy=(dp_6m_x, dp_6m_reps[1]), xytext=(3.15, 56.5),
        fontsize=7.1, color="#134E4A", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.21", facecolor="#F0FDFA", edgecolor="#5EEAD4", lw=0.9),
        arrowprops=dict(arrowstyle="->", color=c_dp, lw=1.0),
    )

    ax_p64.set_xlabel("实际吞吐 (Mreq/s)", fontsize=10)
    ax_p64.set_ylabel("端到端真实延迟 tot = t_recv − t_sched (µs)", fontsize=10)
    ax_p64.set_xlim(0.2, 7.05)
    ax_p64.set_ylim(0, 84)
    ax_p64.set_title("(a) 64 B 开环到达：Poisson (含 n=3 极差) 与 B=8 微突发 (W=64)", fontsize=10.2, fontweight="bold")
    ax_p64.legend(loc="upper left", ncol=2, fontsize=6.5, columnspacing=0.8, frameon=True, facecolor="white", framealpha=0.95)

    # ---- Panel (b): Mix-Ads (90/10) Open-Loop Poisson & B=8 Microburst ----
    sdp_pm_r = [500, 1000, 1500, 2000, 2500]
    dp_pm_r = [500, 1000, 1500, 2000, 2500, 3500]

    sp_mx = [med(f"sdp_nix_mix_ads_poisson_r{r}", lambda d: d["mreq_s"]) for r in sdp_pm_r]
    sp_mp50 = [med(f"sdp_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p50"]) for r in sdp_pm_r]
    sp_mp99 = [med(f"sdp_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in sdp_pm_r]
    sp_mlo = [sp_mp99[i] - min(vals(f"sdp_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) for i, r in enumerate(sdp_pm_r)]
    sp_mhi = [max(vals(f"sdp_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) - sp_mp99[i] for i, r in enumerate(sdp_pm_r)]

    dp_mx = [med(f"directpush_nix_mix_ads_poisson_r{r}", lambda d: d["mreq_s"]) for r in dp_pm_r]
    dp_mp50 = [med(f"directpush_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p50"]) for r in dp_pm_r]
    dp_mp99 = [med(f"directpush_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in dp_pm_r]
    dp_mlo = [dp_mp99[i] - min(vals(f"directpush_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) for i, r in enumerate(dp_pm_r)]
    dp_mhi = [max(vals(f"directpush_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p99"])) - dp_mp99[i] for i, r in enumerate(dp_pm_r)]

    ax_pmix.plot(sp_mx, sp_mp50, "-o", color=c_sdp, lw=1.8, ms=4.5, label="SDP+NIX Poisson p50")
    ax_pmix.errorbar(sp_mx, sp_mp99, yerr=[sp_mlo, sp_mhi], fmt="--s", color=c_sdp, lw=1.8, ms=4.5,
                     capsize=2.5, alpha=0.85, label="SDP+NIX Poisson p99")
    ax_pmix.plot(dp_mx, dp_mp50, "-o", color=c_dp, lw=2.2, ms=5.0, label="DirectPush+NIX Poisson p50")
    ax_pmix.errorbar(dp_mx, dp_mp99, yerr=[dp_mlo, dp_mhi], fmt="--s", color=c_dp, lw=2.2, ms=5.0,
                     capsize=2.5, alpha=0.9, label="DirectPush+NIX Poisson p99")
    ax_pmix.scatter(
        [med(f"sdp_nix_mix_ads_burst8_r{r}", lambda d: d["mreq_s"]) for r in (1000, 1500)],
        [med(f"sdp_nix_mix_ads_burst8_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in (1000, 1500)],
        marker="^", s=56, color=c_b_sdp, zorder=6, label="SDP+NIX B=8 微突发 p99",
    )
    ax_pmix.scatter(
        [med(f"directpush_nix_mix_ads_burst8_r{r}", lambda d: d["mreq_s"]) for r in (1000, 1500, 2500)],
        [med(f"directpush_nix_mix_ads_burst8_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in (1000, 1500, 2500)],
        marker="^", s=56, color=c_b_dp, zorder=6, label="DirectPush+NIX B=8 微突发 p99",
    )

    p50_s25 = med("sdp_nix_mix_ads_poisson_r2500", lambda d: d["lat_all_us"]["p50"])
    p50_d25 = med("directpush_nix_mix_ads_poisson_r2500", lambda d: d["lat_all_us"]["p50"])
    p99_s25 = med("sdp_nix_mix_ads_poisson_r2500", lambda d: d["lat_all_us"]["p99"])
    p99_d25 = med("directpush_nix_mix_ads_poisson_r2500", lambda d: d["lat_all_us"]["p99"])
    ax_pmix.annotate(
        f"2.5 M/s 净优势：\np50 −{p50_s25 - p50_d25:.2f} µs\np99 −{p99_s25 - p99_d25:.2f} µs",
        xy=(2.5, p50_d25), xytext=(2.62, 6.4),
        fontsize=8.0, fontweight="bold", color=c_dp,
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#F0FDFA", edgecolor=c_dp, lw=0.9),
        arrowprops=dict(arrowstyle="->", color=c_dp, lw=1.1),
    )
    ax_pmix.set_xlabel("实际吞吐 (Mreq/s)", fontsize=10)
    ax_pmix.set_ylabel("端到端真实延迟 tot = t_recv − t_sched (µs)", fontsize=10)
    ax_pmix.set_xlim(0.2, 3.85)
    ax_pmix.set_ylim(0, 23.5)
    ax_pmix.set_title("(b) Mix-Ads (90% 64 B / 10% 4 KiB) 开环 Poisson 与微突发", fontsize=10.2, fontweight="bold")
    ax_pmix.legend(loc="upper left", ncol=2, fontsize=7.0, columnspacing=0.9, frameon=True, facecolor="white", framealpha=0.95)

    # ---- Panel (c): Closed-Loop Pure Sizes (64 B & 4096 B, W=1..64) + sdp_zc_nix_4096_w16 ----
    for wl, lbl, col_s, col_d, ws in [
        ("64", "64 B", "#64748B", "#0F766E", [1, 8, 16, 32, 64]),
        ("4096", "4 KiB", "#94A3B8", "#0284C7", [1, 8, 16, 32]),
    ]:
        sx = [med(f"sdp_nix_{wl}_w{w}", lambda d: d["mreq_s"]) for w in ws]
        sp50 = [med(f"sdp_nix_{wl}_w{w}", lambda d: d["lat_all_us"]["p50"]) for w in ws]
        sp99 = [med(f"sdp_nix_{wl}_w{w}", lambda d: d["lat_all_us"]["p99"]) for w in ws]
        dx = [med(f"directpush_nix_{wl}_w{w}", lambda d: d["mreq_s"]) for w in ws]
        dp50 = [med(f"directpush_nix_{wl}_w{w}", lambda d: d["lat_all_us"]["p50"]) for w in ws]
        dp99 = [med(f"directpush_nix_{wl}_w{w}", lambda d: d["lat_all_us"]["p99"]) for w in ws]
        ax_c_pure.plot(sx, sp50, "-o", color=col_s, lw=1.8, ms=4.5, label=f"SDP+NIX {lbl} p50")
        ax_c_pure.plot(sx, sp99, "--s", color=col_s, lw=1.4, ms=3.8, alpha=0.75, label=f"SDP+NIX {lbl} p99")
        ax_c_pure.plot(dx, dp50, "-o", color=col_d, lw=2.2, ms=5.0, label=f"DirectPush+NIX {lbl} p50")
        ax_c_pure.plot(dx, dp99, "--s", color=col_d, lw=1.6, ms=4.0, alpha=0.8, label=f"DirectPush+NIX {lbl} p99")
        if wl == "64":
            ax_c_pure.text(dx[-1] + 0.08, dp50[-1] - 0.7, f"W=64 (1C 上限)\n{dx[-1]:.2f}M/s", fontsize=7.4, color=col_d, fontweight="bold")
        else:
            ax_c_pure.text(dx[-1] + 0.08, dp50[-1] - 0.4, f"W=32\n{dx[-1]:.2f}M/s", fontsize=7.4, color=col_d, fontweight="bold")
        ax_c_pure.text(sx[-1] + 0.08, sp99[-1] + 0.2, f"W={ws[-1]}", fontsize=7.3, color=col_s)

    # Verified closed-loop control sdp_zc_nix_4096_w16
    zc_x = med("sdp_zc_nix_4096_w16", lambda d: d["mreq_s"])
    zc_p50 = med("sdp_zc_nix_4096_w16", lambda d: d["lat_all_us"]["p50"])
    zc_p99 = med("sdp_zc_nix_4096_w16", lambda d: d["lat_all_us"]["p99"])
    ax_c_pure.scatter([zc_x], [zc_p50], marker="D", s=58, color="#D97706", edgecolor="#78350F", zorder=7,
                      label="对照：SDP zc 4 KiB W=16 (p50)")
    ax_c_pure.scatter([zc_x], [zc_p99], marker="D", s=50, facecolors="white", edgecolor="#D97706", lw=1.5, zorder=7,
                      label="对照：SDP zc 4 KiB W=16 (p99)")
    ax_c_pure.annotate(
        f"SDP zc W=16\n{zc_x:.2f}M, p50={zc_p50:.1f} µs",
        xy=(zc_x, zc_p50), xytext=(3.35, 15.2),
        fontsize=7.3, fontweight="bold", color="#92400E",
        bbox=dict(boxstyle="round,pad=0.20", facecolor="#FFFBEB", edgecolor="#D97706", lw=0.8),
        arrowprops=dict(arrowstyle="->", color="#D97706", lw=1.0),
    )

    ax_c_pure.set_xlabel("闭环吞吐 (Mreq/s)", fontsize=10)
    ax_c_pure.set_ylabel("端到端延迟 (µs)", fontsize=10)
    ax_c_pure.set_xlim(0, 7.8)
    ax_c_pure.set_ylim(0, 24.8)
    ax_c_pure.set_title("(c) 闭环单尺寸曲线：64 B (W=1..64) 与 4 KiB (W=1..32，含 SDP zc)", fontsize=10.2, fontweight="bold")
    ax_c_pure.legend(loc="upper right", fontsize=6.7, ncol=2, frameon=True, facecolor="white", framealpha=0.95)

    # ---- Panel (d): Closed-Loop Mixed RPC/KV (Mix-Ads & Mix-Geo) + Mix-Geo 64 B small-packet p99 ----
    for wl, lbl, col_s, col_d in [
        ("mix_ads", "Mix-Ads", "#64748B", "#0F766E"),
        ("mix_geo", "Mix-Geo", "#94A3B8", "#0284C7"),
    ]:
        ws = [1, 8, 16, 32]
        sx = [med(f"sdp_nix_{wl}_w{w}", lambda d: d["mreq_s"]) for w in ws]
        sp50 = [med(f"sdp_nix_{wl}_w{w}", lambda d: d["lat_all_us"]["p50"]) for w in ws]
        sp99 = [med(f"sdp_nix_{wl}_w{w}", lambda d: d["lat_all_us"]["p99"]) for w in ws]
        dx = [med(f"directpush_nix_{wl}_w{w}", lambda d: d["mreq_s"]) for w in ws]
        dp50 = [med(f"directpush_nix_{wl}_w{w}", lambda d: d["lat_all_us"]["p50"]) for w in ws]
        dp99 = [med(f"directpush_nix_{wl}_w{w}", lambda d: d["lat_all_us"]["p99"]) for w in ws]
        ax_c_mix.plot(sx, sp50, "-o", color=col_s, lw=1.8, ms=4.5, label=f"SDP {lbl} p50")
        ax_c_mix.plot(sx, sp99, "--s", color=col_s, lw=1.4, ms=3.8, alpha=0.75, label=f"SDP {lbl} p99")
        ax_c_mix.plot(dx, dp50, "-o", color=col_d, lw=2.2, ms=5.0, label=f"DP {lbl} p50")
        ax_c_mix.plot(dx, dp99, "--s", color=col_d, lw=1.6, ms=4.0, alpha=0.8, label=f"DP {lbl} p99")
        if wl == "mix_ads":
            ax_c_mix.text(dx[-1] + 0.08, dp50[-1] - 0.35, f"Mix-Ads W=32\n{dx[-1]:.2f}M/s", fontsize=7.4, color=col_d, fontweight="bold")
            ax_c_mix.text(sx[-1] - 0.04, sp50[-1] - 0.9, f"SDP {sx[-1]:.2f}M/s", fontsize=7.1, color=col_s, ha="right")
        else:
            ax_c_mix.text(dx[-1] + 0.08, dp50[-1] + 0.05, f"Mix-Geo W=32\n{dx[-1]:.2f}M/s", fontsize=7.4, color=col_d, fontweight="bold")
            ax_c_mix.text(sx[-1] - 0.05, sp50[-1] + 0.55, f"SDP {sx[-1]:.2f}M/s", fontsize=7.1, color=col_s, ha="right")

    # Mix-Geo 64 B small-packet p99 (true HoL elimination)
    ws_geo = [1, 8, 16, 32]
    dp_geo_x = [med(f"directpush_nix_mix_geo_w{w}", lambda d: d["mreq_s"]) for w in ws_geo]
    dp_geo_s99 = [med(f"directpush_nix_mix_geo_w{w}", lambda d: d["lat_small_us"]["p99"]) for w in ws_geo]
    sdp_geo_x32 = med("sdp_nix_mix_geo_w32", lambda d: d["mreq_s"])
    sdp_geo_s99_32 = med("sdp_nix_mix_geo_w32", lambda d: d["lat_small_us"]["p99"])
    dp_geo_s99_32 = dp_geo_s99[-1]
    small_drop_pct = (sdp_geo_s99_32 - dp_geo_s99_32) / sdp_geo_s99_32 * 100.0

    ax_c_mix.plot(dp_geo_x, dp_geo_s99, ":*", color="#7C3AED", lw=1.7, ms=8.5, zorder=7,
                  label="DP Mix-Geo 64 B 小包 p99")
    ax_c_mix.scatter([sdp_geo_x32], [sdp_geo_s99_32], marker="*", s=110, color="#DC2626", edgecolor="#7F1D1D", lw=0.6, zorder=8,
                     label="SDP Mix-Geo W=32 小包 p99")
    ax_c_mix.annotate(
        f"Mix-Geo W=32 64 B 小包 p99：\n"
        f"DP {dp_geo_s99_32:.2f} vs SDP {sdp_geo_s99_32:.2f} µs (−{small_drop_pct:.1f}%)\n"
        f"（总体 p99 重合因 4 KiB 占 68% 槽位）",
        xy=(dp_geo_x[-1], dp_geo_s99_32), xytext=(3.18, 16.5),
        ha="left", va="center",
        fontsize=7.0, fontweight="bold", color="#5B21B6",
        bbox=dict(boxstyle="round,pad=0.20", facecolor="#F5F3FF", edgecolor="#7C3AED", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#7C3AED", lw=1.0, connectionstyle="arc3,rad=-0.16"),
    )

    ax_c_mix.set_xlabel("闭环吞吐 (Mreq/s)", fontsize=10)
    ax_c_mix.set_ylabel("端到端延迟 (µs)", fontsize=10)
    ax_c_mix.set_xlim(0, 5.45)
    ax_c_mix.set_ylim(0, 24.5)
    ax_c_mix.set_title("(d) 闭环混合 RPC/KV：Mix-Ads (90/10) 与 Mix-Geo (50/50，含小包 p99)", fontsize=10.2, fontweight="bold")
    ax_c_mix.legend(loc="upper center", fontsize=6.5, ncol=3, columnspacing=0.8, frameon=True, facecolor="white", framealpha=0.95)

    fig.suptitle(
        "MRV-118：真实 CN106 NIX0+LBK0 硬件回环下的加载延迟—吞吐曲线（纯商用 PCIe NIC，零 DSA）\n"
        "条件：1H (cpu40 3.90 GHz, Uncore 2.20 GHz) + 1C (CN106 2.00 GHz)，MAX_DRAIN=32，n=3 交错中位数（未测 256 B 加载与 Mix-Geo 开环）",
        fontsize=10.2, fontweight="bold", y=0.99,
    )
    out = save(fig, "mrv-118-lat-tput")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
