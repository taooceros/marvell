#!/usr/bin/env python3
"""MRV-133 Fig 1: Real 2-machine 100GbE W=1 end-to-end RTT breakdown & CNTVCT cross-check.

Reads logs/mrv133-2026-11-21/runs.jsonl (n=3 interleaved replicates, verified=True).
Panel (a): Stacked W=1 p50 RTT (64 B, 256 B, 1 KiB, 4 KiB):
  - Fixed 100GbE network + CX5 + CN106 RPM0/NIX0 floor (`card_echo`)
  - Host-NIC PCIe interface overhead (`SDP` default, `SDP rx_poll_mem=1`, `DirectPush + DPI DMA`)
Panel (b): Dual-clock cross-check of the CN106 <-> saturn host-NIC round-trip:
  - mercury client differential `RTT(full) - RTT(card_echo)` vs CN106 ARM64 `CNTVCT_EL0` `host_rtt_p50_us`.
"""

import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot

RUNS = pathlib.Path("/home/hongtao/dev/marvell-mrv133/logs/mrv133-2026-11-21/runs.jsonl")


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

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.2, 5.2), gridspec_kw={"width_ratios": [1.35, 1.0]})

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
            ax1.text(xi, t + 0.25, f"{t:.2f}", ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#1e293b")

    # Annotate net savings (DirectPush vs SDP default)
    for i in range(len(sizes)):
        diff = dp_p50[i] - sdp_p50[i]
        pct = diff / sdp_p50[i] * 100.0
        y_top = max(sdp_p50[i], rpm_p50[i], dp_p50[i]) + 1.15
        ax1.text(
            x[i],
            y_top,
            f"净省 {diff:.2f} µs ({pct:.1f}%)",
            ha="center",
            va="bottom",
            fontsize=8.5,
            fontweight="bold",
            color=C_DP,
            bbox=dict(boxstyle="round,pad=0.2", fc="#eff6ff", ec="#bfdbfe", lw=0.8),
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels(slabels, fontsize=9.5)
    ax1.set_ylabel("真实双机端到端 W=1 p50 往返延迟 (µs)", fontsize=10)
    ax1.set_ylim(0, max(sdp_p50) * 1.24)
    ax1.set_title("(a) 真实 100GbE 双机 W=1 往返延迟拆解 (n=3 中位数)", fontsize=11, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=8.3, frameon=True, facecolor="white", framealpha=0.92)

    # Panel (b): Dual-clock cross-check of CN106 <-> saturn host-NIC hop
    sdp_diff = [s - e for s, e in zip(sdp_p50, echo_p50)]
    dp_diff = [d - e for d, e in zip(dp_p50, echo_p50)]
    w2 = 0.19
    ax2.bar(x - 1.5 * w2, sdp_diff, w2 * 0.92, color=C_SDP, alpha=0.88, label="SDP：mercury 差分 ΔRTT")
    ax2.bar(x - 0.5 * w2, sdp_hrtt, w2 * 0.92, color=C_SDP, alpha=0.42, hatch="..", edgecolor=C_SDP, label="SDP：CN106 CNTVCT 实测")
    ax2.bar(x + 0.5 * w2, dp_diff, w2 * 0.92, color=C_DP, alpha=0.92, label="DirectPush：mercury 差分 ΔRTT")
    ax2.bar(x + 1.5 * w2, dp_hrtt, w2 * 0.92, color=C_DP, alpha=0.42, hatch="..", edgecolor=C_DP, label="DirectPush：CN106 CNTVCT 实测")
    for i in range(len(sizes)):
        ax2.text(x[i] - 1.0 * w2, max(sdp_diff[i], sdp_hrtt[i]) + 0.18, f"{sdp_diff[i]:.2f}/{sdp_hrtt[i]:.2f}",
                 ha="center", va="bottom", fontsize=7.6, color="#991b1b")
        ax2.text(x[i] + 1.0 * w2, max(dp_diff[i], dp_hrtt[i]) + 0.18, f"{dp_diff[i]:.2f}/{dp_hrtt[i]:.2f}",
                 ha="center", va="bottom", fontsize=7.6, color="#1e40af", fontweight="bold")

    ax2.set_xticks(x)
    ax2.set_xticklabels(["64 B", "256 B", "1 KiB", "4 KiB"], fontsize=9.5)
    ax2.set_ylabel("CN106 ⇄ saturn 主机接口往返耗时 (µs)", fontsize=10)
    ax2.set_ylim(0, max(max(sdp_diff), max(sdp_hrtt)) * 1.28)
    ax2.set_title("(b) 双独立时钟交叉验真：mercury 差分 vs 卡 CNTVCT", fontsize=11, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=8.0, frameon=True, facecolor="white", framealpha=0.92)

    fig.suptitle(
        "MRV-133 真实双机 100GbE（mercury CX5 ⇄ 3m DAC ⇄ CN106 eth0 RPM0+NIX0 ⇄ saturn，1H+1C，零 DSA）W=1 延迟拆解",
        fontsize=11.5,
        fontweight="bold",
        y=0.99,
    )
    png = mrvplot.save(fig, "mrv-133-decomp")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
