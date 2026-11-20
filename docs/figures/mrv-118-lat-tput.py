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


def load_medians():
    by = defaultdict(list)
    for line in RUNS.read_text().splitlines():
        r = json.loads(line)
        assert r["verified"], f"unverified run: {r.get('cfg')}"
        by[r["cfg"]].append(r)

    def med(cfg, fn):
        return statistics.median(fn(r) for r in by[cfg])

    return by, med


def main():
    by, med = load_medians()
    fig, axes = plt.subplots(2, 2, figsize=(12.4, 8.6))
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

    dp_x = [med(f"directpush_nix_64_poisson_r{r}", lambda d: d["mreq_s"]) for r in dp_p64_r]
    dp_p50 = [med(f"directpush_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p50"]) for r in dp_p64_r]
    dp_p99 = [med(f"directpush_nix_64_poisson_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in dp_p64_r]

    ax_p64.plot(sdp_x, sdp_p50, "-o", color=c_sdp, lw=1.8, ms=4.5, label="SDP+NIX Poisson p50")
    ax_p64.plot(sdp_x, sdp_p99, "--s", color=c_sdp, lw=1.8, ms=4.5, alpha=0.85, label="SDP+NIX Poisson p99")
    ax_p64.plot(dp_x, dp_p50, "-o", color=c_dp, lw=2.2, ms=5.0, label="DirectPush+NIX Poisson p50")
    ax_p64.plot(dp_x, dp_p99, "--s", color=c_dp, lw=2.2, ms=5.0, alpha=0.9, label="DirectPush+NIX Poisson p99")

    # Also plot B=8 microburst p99 markers
    bs_r = [1000, 2000]
    bd_r = [1000, 2000, 3000, 4000]
    ax_p64.scatter(
        [med(f"sdp_nix_64_burst8_r{r}", lambda d: d["mreq_s"]) for r in bs_r],
        [med(f"sdp_nix_64_burst8_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in bs_r],
        marker="^", s=58, color=c_b_sdp, zorder=6, label="SDP+NIX B=8 微突发 p99",
    )
    ax_p64.scatter(
        [med(f"directpush_nix_64_burst8_r{r}", lambda d: d["mreq_s"]) for r in bd_r],
        [med(f"directpush_nix_64_burst8_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in bd_r],
        marker="^", s=58, color=c_b_dp, zorder=6, label="DirectPush+NIX B=8 微突发 p99",
    )

    # Annotate SDP 5.0 M/s saturation explosion off-scale
    sdp_5m_p50 = med("sdp_nix_64_poisson_r5000", lambda d: d["lat_all_us"]["p50"])
    sdp_5m_p99 = med("sdp_nix_64_poisson_r5000", lambda d: d["lat_all_us"]["p99"])
    ax_p64.annotate(
        f"SDP @ 5.0 M/s 饱和飙升\np50={sdp_5m_p50:.0f} / p99={sdp_5m_p99:.0f} µs ↑",
        xy=(4.55, 34.5), xytext=(2.85, 42.5),
        fontsize=8.2, fontweight="bold", color="#991B1B",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#FEF2F2", edgecolor="#DC2626", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#DC2626", lw=1.2),
    )
    ax_p64.annotate(
        f"DirectPush @ 5.0 M/s\np99={dp_p99[-2]:.1f} µs (无慢态悬崖)",
        xy=(5.0, dp_p99[-2]), xytext=(3.6, 22.5),
        fontsize=8.2, fontweight="bold", color=c_dp,
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#F0FDFA", edgecolor=c_dp, lw=0.9),
        arrowprops=dict(arrowstyle="->", color=c_dp, lw=1.1),
    )

    ax_p64.set_xlabel("实际吞吐 (Mreq/s)", fontsize=10)
    ax_p64.set_ylabel("端到端真实延迟 tot = t_recv − t_sched (µs)", fontsize=10)
    ax_p64.set_xlim(0.2, 6.4)
    ax_p64.set_ylim(0, 52)
    ax_p64.set_title("(a) 64 B 开环到达：Poisson 与 B=8 微突发（W=64）", fontsize=10.5, fontweight="bold")
    ax_p64.legend(loc="upper left", fontsize=7.4, frameon=True, facecolor="white", framealpha=0.94)

    # ---- Panel (b): Mix-Ads (90/10) Open-Loop Poisson & B=8 Microburst ----
    sdp_pm_r = [500, 1000, 1500, 2000, 2500]
    dp_pm_r = [500, 1000, 1500, 2000, 2500, 3500]
    ax_pmix.plot(
        [med(f"sdp_nix_mix_ads_poisson_r{r}", lambda d: d["mreq_s"]) for r in sdp_pm_r],
        [med(f"sdp_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p50"]) for r in sdp_pm_r],
        "-o", color=c_sdp, lw=1.8, ms=4.5, label="SDP+NIX Poisson p50",
    )
    ax_pmix.plot(
        [med(f"sdp_nix_mix_ads_poisson_r{r}", lambda d: d["mreq_s"]) for r in sdp_pm_r],
        [med(f"sdp_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in sdp_pm_r],
        "--s", color=c_sdp, lw=1.8, ms=4.5, alpha=0.85, label="SDP+NIX Poisson p99",
    )
    ax_pmix.plot(
        [med(f"directpush_nix_mix_ads_poisson_r{r}", lambda d: d["mreq_s"]) for r in dp_pm_r],
        [med(f"directpush_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p50"]) for r in dp_pm_r],
        "-o", color=c_dp, lw=2.2, ms=5.0, label="DirectPush+NIX Poisson p50",
    )
    ax_pmix.plot(
        [med(f"directpush_nix_mix_ads_poisson_r{r}", lambda d: d["mreq_s"]) for r in dp_pm_r],
        [med(f"directpush_nix_mix_ads_poisson_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in dp_pm_r],
        "--s", color=c_dp, lw=2.2, ms=5.0, alpha=0.9, label="DirectPush+NIX Poisson p99",
    )
    ax_pmix.scatter(
        [med(f"sdp_nix_mix_ads_burst8_r{r}", lambda d: d["mreq_s"]) for r in (1000, 1500)],
        [med(f"sdp_nix_mix_ads_burst8_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in (1000, 1500)],
        marker="^", s=58, color=c_b_sdp, zorder=6, label="SDP+NIX B=8 微突发 p99",
    )
    ax_pmix.scatter(
        [med(f"directpush_nix_mix_ads_burst8_r{r}", lambda d: d["mreq_s"]) for r in (1000, 1500, 2500)],
        [med(f"directpush_nix_mix_ads_burst8_r{r}", lambda d: d["lat_all_us"]["p99"]) for r in (1000, 1500, 2500)],
        marker="^", s=58, color=c_b_dp, zorder=6, label="DirectPush+NIX B=8 微突发 p99",
    )

    p50_s25 = med("sdp_nix_mix_ads_poisson_r2500", lambda d: d["lat_all_us"]["p50"])
    p50_d25 = med("directpush_nix_mix_ads_poisson_r2500", lambda d: d["lat_all_us"]["p50"])
    p99_s25 = med("sdp_nix_mix_ads_poisson_r2500", lambda d: d["lat_all_us"]["p99"])
    p99_d25 = med("directpush_nix_mix_ads_poisson_r2500", lambda d: d["lat_all_us"]["p99"])
    ax_pmix.annotate(
        f"2.5 M/s 净优势：\np50 −{p50_s25 - p50_d25:.2f} µs\np99 −{p99_s25 - p99_d25:.2f} µs",
        xy=(2.5, p50_d25), xytext=(2.62, 6.2),
        fontsize=8.2, fontweight="bold", color=c_dp,
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#F0FDFA", edgecolor=c_dp, lw=0.9),
        arrowprops=dict(arrowstyle="->", color=c_dp, lw=1.1),
    )
    ax_pmix.set_xlabel("实际吞吐 (Mreq/s)", fontsize=10)
    ax_pmix.set_ylabel("端到端真实延迟 tot = t_recv − t_sched (µs)", fontsize=10)
    ax_pmix.set_xlim(0.2, 3.85)
    ax_pmix.set_ylim(0, 23.5)
    ax_pmix.set_title("(b) Mix-Ads (90% 64 B / 10% 4 KiB) 开环 Poisson 与微突发", fontsize=10.5, fontweight="bold")
    ax_pmix.legend(loc="upper left", ncol=2, fontsize=7.2, columnspacing=0.9, frameon=True, facecolor="white", framealpha=0.95)

    # ---- Panel (c): Closed-Loop Pure Sizes (64 B & 4096 B, W=1..64) ----
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
        # Label peak W point
        ax_c_pure.text(dx[-1] + 0.08, dp50[-1] - 0.5, f"W={ws[-1]}\n{dx[-1]:.2f}M/s", fontsize=7.6, color=col_d, fontweight="bold")
        ax_c_pure.text(sx[-1] + 0.08, sp99[-1] + 0.2, f"W={ws[-1]}", fontsize=7.4, color=col_s)

    ax_c_pure.set_xlabel("闭环吞吐 (Mreq/s)", fontsize=10)
    ax_c_pure.set_ylabel("端到端延迟 (µs)", fontsize=10)
    ax_c_pure.set_xlim(0, 7.6)
    ax_c_pure.set_ylim(0, 23)
    ax_c_pure.set_title("(c) 闭环单尺寸曲线：64 B (W=1..64) 与 4 KiB (W=1..32)", fontsize=10.5, fontweight="bold")
    ax_c_pure.legend(loc="upper right", fontsize=7.2, ncol=2, frameon=True, facecolor="white", framealpha=0.94)

    # ---- Panel (d): Closed-Loop Mixed RPC/KV (Mix-Ads & Mix-Geo, W=1,8,16,32) ----
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
        ax_c_mix.plot(sx, sp50, "-o", color=col_s, lw=1.8, ms=4.5, label=f"SDP+NIX {lbl} p50")
        ax_c_mix.plot(sx, sp99, "--s", color=col_s, lw=1.4, ms=3.8, alpha=0.75, label=f"SDP+NIX {lbl} p99")
        ax_c_mix.plot(dx, dp50, "-o", color=col_d, lw=2.2, ms=5.0, label=f"DirectPush+NIX {lbl} p50")
        ax_c_mix.plot(dx, dp99, "--s", color=col_d, lw=1.6, ms=4.0, alpha=0.8, label=f"DirectPush+NIX {lbl} p99")
        ax_c_mix.text(dx[-1] + 0.08, dp50[-1] - 0.35, f"{lbl} W=32\n{dx[-1]:.2f}M/s", fontsize=7.6, color=col_d, fontweight="bold")
        if wl == "mix_ads":
            ax_c_mix.text(sx[-1] - 0.04, sp50[-1] + 0.45, f"SDP {sx[-1]:.2f}M/s", fontsize=7.3, color=col_s, ha="right")
        else:
            ax_c_mix.text(sx[-1] + 0.06, sp50[-1] + 0.25, f"SDP {sx[-1]:.2f}M/s", fontsize=7.3, color=col_s, ha="left")

    ax_c_mix.set_xlabel("闭环吞吐 (Mreq/s)", fontsize=10)
    ax_c_mix.set_ylabel("端到端延迟 (µs)", fontsize=10)
    ax_c_mix.set_xlim(0, 5.1)
    ax_c_mix.set_ylim(0, 19.5)
    ax_c_mix.set_title("(d) 闭环混合 RPC/KV 曲线：Mix-Ads (90/10) 与 Mix-Geo (50/50)", fontsize=10.5, fontweight="bold")
    ax_c_mix.legend(loc="lower right", fontsize=7.2, ncol=2, frameon=True, facecolor="white", framealpha=0.94)

    fig.suptitle(
        "MRV-118：真实 CN106 NIX0+LBK0 硬件回环下的加载延迟—吞吐曲线（纯商用 PCIe NIC，零 DSA）\n"
        "条件：1H (cpu40 3.90 GHz, Uncore 2.20 GHz) + 1C (CN106 2.00 GHz)，MAX_DRAIN=32，n=3 轮交错中位数，全项零错零丢",
        fontsize=10.5, fontweight="bold", y=0.99,
    )
    out = save(fig, "mrv-118-lat-tput")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
