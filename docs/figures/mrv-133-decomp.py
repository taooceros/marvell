#!/usr/bin/env python3
"""MRV-133 Fig 1: Real 2-machine 100GbE W=1 end-to-end RTT breakdown & CNTVCT cross-check.

Reads logs/mrv133-2026-11-21/runs.jsonl (n=3 interleaved replicates, verified=True).
Panel (a): Stacked W=1 p50 RTT (64 B, 256 B, 1 KiB, 4 KiB):
  - Fixed 100GbE network + CX5 + CN106 RPM0/NIX0 floor (`card_echo`)
  - Host-NIC PCIe interface overhead (`SDP` default, `SDP rx_poll_mem=1`, `DirectPush + DPI DMA`)
Panel (b): Dual-clock cross-check of the CN106 <-> saturn host-NIC round-trip:
  - mercury client differential `RTT(full) - RTT(card_echo)` vs CN106 ARM64 `CNTVCT_EL0` `host_rtt_p50_us`,
    plus warm `direct_mix_ads_w1` 64 B differential (`1.33 µs` vs `CNTVCT 1.31 µs`) explaining the +0.37 µs
    cold-start `sleep(60 ms)` offset on standalone `64 B`.
"""

import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot

RUNS = pathlib.Path(__file__).resolve().parents[2] / "logs/mrv133-2026-11-21/runs.jsonl"


def load_runs():
    by_cfg = {}
    for line in RUNS.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r.get("verified"), f"Unverified run: {r['cfg']} rep={r['rep']} {r.get('verify_errors')}"
        by_cfg.setdefault(r["cfg"], []).append(r)
    return by_cfg


def med(vals):
    return statistics.median(vals)


def fmt2(v):
    """Standard half-up rounding to 2 decimals (avoids Python banker's rounding ambiguity on .xx5)."""
    return f"{v + (1e-9 if v >= 0 else -1e-9):.2f}"


def main():
    by_cfg = load_runs()
    sizes = ["64", "256", "1024", "4096"]
    slabels = ["64 B\n(单帧直推)", "256 B\n(单帧直推)", "1 KiB\n(单帧 DPI)", "4 KiB\n(4×1K → 4K DPI)"]

    echo_p50 = [med([r["client"]["tot_p50_us"] for r in by_cfg[f"card_echo_{s}_w1"]]) for s in sizes]
    sdp_p50 = [med([r["client"]["tot_p50_us"] for r in by_cfg[f"sdp_{s}_w1"]]) for s in sizes]
    rpm_p50 = [med([r["client"]["tot_p50_us"] for r in by_cfg[f"sdp_rpm_{s}_w1"]]) for s in sizes]
    dp_p50 = [med([r["client"]["tot_p50_us"] for r in by_cfg[f"direct_{s}_w1"]]) for s in sizes]

    sdp_hrtt = [med([r["card"]["host_rtt_p50_us"] for r in by_cfg[f"sdp_{s}_w1"]]) for s in sizes]
    dp_hrtt = [med([r["card"]["host_rtt_p50_us"] for r in by_cfg[f"direct_{s}_w1"]]) for s in sizes]

    # Warm 64 B differential from direct_mix_ads_w1 (runs 3s after stack start, avoids mercury cold sleep offset)
    warm_64_p50 = med([r["client"]["small_p50_us"] for r in by_cfg["direct_mix_ads_w1"]])
    warm_64_diff = warm_64_p50 - echo_p50[0]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.4), gridspec_kw={"width_ratios": [1.35, 1.0]})

    # Panel (a): Stacked end-to-end RTT
    x = np.arange(len(sizes))
    w = 0.25
    C_NET = "#94a3b8"
    C_SDP = "#d9534f"
    C_RPM = "#e67e22"
    C_DP = "#1f6feb"

    for offset, tot, col, label in [
        (-w, sdp_p50, C_SDP, "传统 SDP (默认)"),
        (0.0, rpm_p50, C_RPM, "SDP (rx_poll_mem=1)"),
        (+w, dp_p50, C_DP, "DirectPush + 原生 DPI DMA"),
    ]:
        net_part = [min(e, t) for e, t in zip(echo_p50, tot)]
        if_part = [max(0.0, t - e) for e, t in zip(echo_p50, tot)]
        ax1.bar(
            x + offset,
            net_part,
            w * 0.92,
            color=C_NET,
            alpha=0.55,
            hatch="///",
            edgecolor="white",
            label="100GbE 实网 + CX5 + RPM0/NIX0 (card_echo)" if offset == -w else None,
        )
        ax1.bar(x + offset, if_part, w * 0.92, bottom=net_part, color=col, alpha=0.92, label=label)
        for xi, t in zip(x + offset, tot):
            ax1.text(xi, t + 0.24, fmt2(t), ha="center", va="bottom", fontsize=8.4, fontweight="bold", color="#1e293b")

    # Annotate net savings (DirectPush vs SDP default & vs rpm=1 where distinct)
    for i in range(len(sizes)):
        diff = dp_p50[i] - sdp_p50[i]
        diff_rpm = dp_p50[i] - rpm_p50[i]
        pct = diff / sdp_p50[i] * 100.0
        y_top = max(sdp_p50[i], rpm_p50[i], dp_p50[i]) + 1.18
        if sizes[i] == "1024":
            txt = f"净省 {fmt2(diff)} µs ({pct:.1f}%)\n静态 1K 阈值 (vs rpm {fmt2(diff_rpm)})"
            fc, ec, tc = "#fffbeb", "#fcd34d", "#92400e"
        elif sizes[i] == "4096":
            txt = f"净省 {fmt2(diff)} µs ({pct:.1f}%)\n(vs rpm {fmt2(diff_rpm)} µs)"
            fc, ec, tc = "#eff6ff", "#bfdbfe", C_DP
        else:
            txt = f"净省 {fmt2(diff)} µs ({pct:.1f}%)"
            fc, ec, tc = "#eff6ff", "#bfdbfe", C_DP
        ax1.text(
            x[i],
            y_top,
            txt,
            ha="center",
            va="bottom",
            fontsize=8.0,
            fontweight="bold",
            color=tc,
            bbox=dict(boxstyle="round,pad=0.22", fc=fc, ec=ec, lw=0.8),
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels(slabels, fontsize=9.4)
    ax1.set_ylabel("真实双机端到端 W=1 p50 往返延迟 (µs)", fontsize=10)
    ax1.set_ylim(0, max(sdp_p50) * 1.32)
    ax1.set_title("(a) 真实 100GbE 双机 W=1 往返延迟拆解 (n=3 中位数)", fontsize=10.8, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=8.2, frameon=True, facecolor="white", framealpha=0.94)

    # Panel (b): Dual-clock cross-check of CN106 <-> saturn host-NIC hop
    sdp_diff = [s - e for s, e in zip(sdp_p50, echo_p50)]
    dp_diff = [d - e for d, e in zip(dp_p50, echo_p50)]
    w2 = 0.19
    ax2.bar(x - 1.5 * w2, sdp_diff, w2 * 0.92, color=C_SDP, alpha=0.88, label="SDP 默认：mercury 差分 ΔRTT")
    ax2.bar(x - 0.5 * w2, sdp_hrtt, w2 * 0.92, color=C_SDP, alpha=0.40, hatch="..", edgecolor=C_SDP, label="SDP 默认：CN106 CNTVCT 实测")
    ax2.bar(x + 0.5 * w2, dp_diff, w2 * 0.92, color=C_DP, alpha=0.92, label="DirectPush：mercury 差分 ΔRTT")
    ax2.bar(x + 1.5 * w2, dp_hrtt, w2 * 0.92, color=C_DP, alpha=0.40, hatch="..", edgecolor=C_DP, label="DirectPush：CN106 CNTVCT 实测")

    # Warm 64 B marker showing cold-start sleep(60ms) explains the +0.37 us gap
    ax2.plot(
        x[0] + 0.5 * w2,
        warm_64_diff,
        marker="D",
        ms=6.5,
        mfc="white",
        mec="#0f172a",
        mew=1.5,
        zorder=6,
        label=f"热态 Mix-Ads W=1 64 B 差分 ({fmt2(warm_64_diff)} µs)",
    )
    ax2.annotate(
        f"热态差分 {fmt2(warm_64_diff)} µs\n与 CNTVCT 差仅 {(warm_64_diff - dp_hrtt[0])*1e3:.0f} ns",
        xy=(x[0] + 0.5 * w2 + 0.05, warm_64_diff + 0.02),
        xytext=(x[0] + 0.42, 3.45),
        arrowprops=dict(arrowstyle="->", color="#334155", lw=1.1, connectionstyle="arc3,rad=-0.28"),
        fontsize=7.6,
        color="#1e293b",
        bbox=dict(boxstyle="round,pad=0.2", fc="#f8fafc", ec="#cbd5e1", lw=0.8),
    )

    for i in range(len(sizes)):
        ax2.text(
            x[i] - 1.0 * w2,
            max(sdp_diff[i], sdp_hrtt[i]) + 0.22,
            f"{fmt2(sdp_diff[i])}/{fmt2(sdp_hrtt[i])}",
            ha="center",
            va="bottom",
            fontsize=7.6,
            color="#991b1b",
        )
        ax2.text(
            x[i] + 1.0 * w2,
            max(dp_diff[i], dp_hrtt[i]) + 0.22,
            f"{fmt2(dp_diff[i])}/{fmt2(dp_hrtt[i])}",
            ha="center",
            va="bottom",
            fontsize=7.6,
            color="#1e40af",
            fontweight="bold",
        )

    ax2.set_xticks(x)
    ax2.set_xticklabels(["64 B", "256 B", "1 KiB", "4 KiB"], fontsize=9.5)
    ax2.set_ylabel("CN106 ⇄ saturn 主机接口往返耗时 (µs)", fontsize=10)
    ax2.set_ylim(0, max(max(sdp_diff), max(sdp_hrtt)) * 1.32)
    ax2.set_title("(b) 双独立时钟交叉验真：mercury 差分 vs 卡 CNTVCT", fontsize=10.8, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=7.6, frameon=True, facecolor="white", framealpha=0.94)

    fig.suptitle(
        "MRV-133 真实双机 100GbE（mercury CX5 ⇄ 3m DAC ⇄ CN106 eth0 RPM0+NIX0 ⇄ saturn，1H+1C，零 DSA）W=1 延迟拆解",
        fontsize=11.4,
        fontweight="bold",
        y=0.99,
    )
    png = mrvplot.save(fig, "mrv-133-decomp")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
