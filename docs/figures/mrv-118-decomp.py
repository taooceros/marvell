#!/usr/bin/env python3
"""MRV-118 Figure 1: W=1 end-to-end latency stage breakdown (No-NIX vs Real CN106 NIX0+LBK0)."""

import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

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
    sizes = ["64", "256", "4096"]
    size_labels = ["64 B", "256 B", "4 KiB"]

    sdp_nonix = [med(f"sdp_nonix_{s}_w1", lambda r: r["lat_all_us"]["p50"]) for s in sizes]
    dp_nonix = [med(f"directpush_nonix_{s}_w1", lambda r: r["lat_all_us"]["p50"]) for s in sizes]
    sdp_nix = [med(f"sdp_nix_{s}_w1", lambda r: r["lat_all_us"]["p50"]) for s in sizes]
    dp_nix = [med(f"directpush_nix_{s}_w1", lambda r: r["lat_all_us"]["p50"]) for s in sizes]
    sdp_nix_hw = [med(f"sdp_nix_{s}_w1", lambda r: r["card_stats"]["nix_hw_all_us"]["p50"]) for s in sizes]
    dp_nix_hw = [med(f"directpush_nix_{s}_w1", lambda r: r["card_stats"]["nix_hw_all_us"]["p50"]) for s in sizes]
    sdp_dnix = [a - b for a, b in zip(sdp_nix, sdp_nonix)]
    dp_dnix = [a - b for a, b in zip(dp_nix, dp_nonix)]

    rpm_64 = med("sdp_rpm_nix_64_w1", lambda r: r["lat_all_us"]["p50"])
    rpm_4096 = med("sdp_rpm_nix_4096_w1", lambda r: r["lat_all_us"]["p50"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.2, 4.9), gridspec_kw={"width_ratios": [1.35, 1.0]})

    # ---- Panel (a): Stacked W=1 latency decomposition ----
    x = np.arange(len(sizes))
    w = 0.34

    # SDP stacked bars
    ax1.bar(x - w / 2 - 0.02, sdp_nonix, w, color="#64748B", edgecolor="#334155", linewidth=0.8,
            label="传统 SDP：卡⇄主机接口（No-NIX）")
    ax1.bar(x - w / 2 - 0.02, sdp_dnix, w, bottom=sdp_nonix, color="#CBD5E1", edgecolor="#475569", linewidth=0.8,
            hatch="//", label="传统 SDP：NIX0+LBK0 硬件回环增量")

    # DirectPush stacked bars
    ax1.bar(x + w / 2 + 0.02, dp_nonix, w, color="#0F766E", edgecolor="#134E4A", linewidth=0.8,
            label="DirectPush+DPI：卡⇄主机接口（No-NIX）")
    ax1.bar(x + w / 2 + 0.02, dp_dnix, w, bottom=dp_nonix, color="#99F6E4", edgecolor="#0F766E", linewidth=0.8,
            hatch="//", label="DirectPush+DPI：NIX0+LBK0 硬件回环增量")

    # SDP rx_poll_mem=1 control diamonds
    ax1.scatter([0 - w / 2 - 0.02, 2 - w / 2 - 0.02], [rpm_64, rpm_4096],
                marker="D", s=46, color="#D97706", edgecolor="#78350F", zorder=6,
                label="对照：SDP (rx_poll_mem=1) + NIX0")

    # Value annotations & net advantage callouts
    for i in range(len(sizes)):
        xs = x[i] - w / 2 - 0.02
        xd = x[i] + w / 2 + 0.02
        ax1.text(xs, sdp_nix[i] + 0.18, f"{sdp_nix[i]:.2f}", ha="center", va="bottom",
                 fontsize=9.5, fontweight="bold", color="#334155")
        ax1.text(xd, dp_nix[i] + 0.18, f"{dp_nix[i]:.2f}", ha="center", va="bottom",
                 fontsize=9.5, fontweight="bold", color="#0F766E")
        # Inside bar labels
        ax1.text(xs, sdp_nonix[i] / 2, f"{sdp_nonix[i]:.2f}", ha="center", va="center",
                 fontsize=8.2, color="white", fontweight="bold")
        ax1.text(xs, sdp_nonix[i] + sdp_dnix[i] / 2, f"+{sdp_dnix[i]:.2f}", ha="center", va="center",
                 fontsize=8.0, color="#1E293B", fontweight="bold")
        if dp_nonix[i] >= 1.1:
            ax1.text(xd, dp_nonix[i] / 2, f"{dp_nonix[i]:.2f}", ha="center", va="center",
                     fontsize=8.2, color="white", fontweight="bold")
        else:
            ax1.text(xd, dp_nonix[i] / 2, f"{dp_nonix[i]:.2f}", ha="center", va="center",
                     fontsize=7.6, color="white", fontweight="bold")
        ax1.text(xd, dp_nonix[i] + dp_dnix[i] / 2, f"+{dp_dnix[i]:.2f}", ha="center", va="center",
                 fontsize=8.0, color="#134E4A", fontweight="bold")

        # Net savings arrow & label above DP bar
        diff = sdp_nix[i] - dp_nix[i]
        pct = diff / sdp_nix[i] * 100.0
        y_top = sdp_nix[i] + 1.05
        ax1.annotate(
            f"−{diff:.2f} µs (−{pct:.1f}%)",
            xy=(xd, dp_nix[i] + 0.72),
            xytext=(x[i] + 0.04, y_top),
            ha="center", va="bottom",
            fontsize=8.8, fontweight="bold", color="#0F766E",
            bbox=dict(boxstyle="round,pad=0.22", facecolor="#F0FDFA", edgecolor="#0F766E", lw=0.9),
            arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.0, shrinkA=2, shrinkB=2),
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels(size_labels, fontsize=10.5, fontweight="bold")
    ax1.set_ylabel("端到端 W=1 p50 RTT (µs)", fontsize=10.5)
    ax1.set_ylim(0, 15.2)
    ax1.set_title("(a) 端到端延迟堆叠拆解：接口 vs NIX0+LBK0", fontsize=11, fontweight="bold", pad=8)
    ax1.legend(loc="upper left", ncol=2, fontsize=7.2, columnspacing=0.9, frameon=True, facecolor="white", framealpha=0.96)
    # ---- Panel (b): Independent timer cross-check (Host ΔNIX vs Card CNTVCT NIX HW) ----
    xb = np.arange(len(sizes))
    wb = 0.19
    ax2.bar(xb - 1.5 * wb, sdp_dnix, wb, color="#94A3B8", edgecolor="#334155", lw=0.7,
            label="SDP：主机 ΔNIX (有−无 NIX)")
    ax2.bar(xb - 0.5 * wb, sdp_nix_hw, wb, color="#475569", edgecolor="#1E293B", lw=0.7,
            label="SDP：卡侧 CNTVCT NIX 硬件实测")
    ax2.bar(xb + 0.5 * wb, dp_dnix, wb, color="#5EEAD4", edgecolor="#0F766E", lw=0.7,
            label="DirectPush：主机 ΔNIX (有−无 NIX)")
    ax2.bar(xb + 1.5 * wb, dp_nix_hw, wb, color="#0F766E", edgecolor="#134E4A", lw=0.7,
            label="DirectPush：卡侧 CNTVCT NIX 硬件实测")

    for i in range(len(sizes)):
        for off, val, col in [
            (-1.5 * wb, sdp_dnix[i], "#475569"),
            (-0.5 * wb, sdp_nix_hw[i], "#1E293B"),
            (0.5 * wb, dp_dnix[i], "#0F766E"),
            (1.5 * wb, dp_nix_hw[i], "#134E4A"),
        ]:
            ax2.text(xb[i] + off, val + 0.08, f"{val:.2f}", ha="center", va="bottom", fontsize=7.6, color=col,
                     fontweight="bold")

    ax2.set_xticks(xb)
    ax2.set_xticklabels(size_labels, fontsize=10.5, fontweight="bold")
    ax2.set_ylabel("NIX0+LBK0 硬件回环阶段耗时 (µs)", fontsize=10.5)
    ax2.set_ylim(0, 5.4)
    ax2.set_title("(b) 主机差分 ΔNIX 与卡侧 CNTVCT 硬件计时交叉验真", fontsize=11, fontweight="bold", pad=8)
    ax2.legend(loc="upper left", fontsize=7.6, frameon=True, facecolor="white", framealpha=0.95)

    fig.suptitle(
        "MRV-118：经 CN106 真实 NIX0+LBK0 硬件回环（0002:03:00.0）后 W=1 端到端延迟拆解\n"
        "条件：1H (cpu40 3.90 GHz, Uncore 2.20 GHz) + 1C (CN106 2.00 GHz)，128 MiB TX 池，n=3 轮交错中位数，全项零错零丢",
        fontsize=10.5, fontweight="bold", y=0.99,
    )
    out = save(fig, "mrv-118-decomp")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
