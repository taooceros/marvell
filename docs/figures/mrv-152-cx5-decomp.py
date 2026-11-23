#!/usr/bin/env python3
"""MRV-152 3-tier W=1 RTT breakdown: mercury CX5 self-loopback + CN106 eth0/NIX0/ARM + saturn host-NIC interface.

Reads raw verified JSONL data directly from:
  - logs/mrv152-2026-11-23/runs.jsonl  (mercury mlx5_2 CX5 hardware self-loopback, n=3)
  - /home/hongtao/dev/marvell/logs/mrv133-2026-11-21/runs.jsonl  (MRV-133 2-machine 100GbE, n=3)
  - /home/hongtao/dev/marvell-mrv118/logs/mrv118-2026-11-20/runs.jsonl  (MRV-118 CN106 NIX0+LBK0 CNTVCT, n=3)
"""

import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot

RUNS_152 = pathlib.Path(__file__).resolve().parents[2] / "logs/mrv152-2026-11-23/runs.jsonl"
RUNS_133 = pathlib.Path("/home/hongtao/dev/marvell/logs/mrv133-2026-11-21/runs.jsonl")
RUNS_118 = pathlib.Path("/home/hongtao/dev/marvell-mrv118/logs/mrv118-2026-11-20/runs.jsonl")


def fmt2(v: float) -> str:
    return f"{v + (1e-9 if v >= 0 else -1e-9):.2f}"


def load_jsonl(path: pathlib.Path, key_fn):
    by = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r["verified"], f"unverified run in {path}: {r}"
        by.setdefault(key_fn(r), []).append(r)
    return by


def med(vals):
    return statistics.median(vals)


def main():
    m152 = load_jsonl(RUNS_152, lambda r: str(r["size"]))
    m133 = load_jsonl(RUNS_133, lambda r: r["cfg"])
    m118 = load_jsonl(RUNS_118, lambda r: r["cfg"])

    sizes = ["64", "256", "1024", "4096"]
    slabels = ["64 B\n(直推)", "256 B\n(直推)", "1 KiB\n(DPI)", "4 KiB\n(4×1K DPI)"]

    cx5 = [med([r["p50_us"] for r in m152[s]]) for s in sizes]
    echo = [med([r["client"]["tot_p50_us"] for r in m133[f"card_echo_{s}_w1"]]) for s in sizes]
    cn106 = [e - c for e, c in zip(echo, cx5)]
    sdp_tot = [med([r["client"]["tot_p50_us"] for r in m133[f"sdp_{s}_w1"]]) for s in sizes]
    dp_tot = [med([r["client"]["tot_p50_us"] for r in m133[f"direct_{s}_w1"]]) for s in sizes]
    sdp_if = [t - e for t, e in zip(sdp_tot, echo)]
    dp_if = [t - e for t, e in zip(dp_tot, echo)]
    dp_cnt = [med([r["card"]["host_rtt_p50_us"] for r in m133[f"direct_{s}_w1"]]) for s in sizes]

    m118_sizes = ["64", "256", "4096"]
    m118_idx = [0, 1, 3]
    m118_nix = [med([r["card_stats"]["nix_hw_all_us"]["p50"] for r in m118[f"directpush_nix_{s}_w1"]]) for s in m118_sizes]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.4), gridspec_kw={"width_ratios": [1.4, 1.0]})

    # Panel (a): Full 3-tier stacked breakdown
    x = np.arange(len(sizes))
    w = 0.34
    C_CX5 = "#475569"
    C_CN106 = "#cbd5e1"
    C_SDP = "#d9534f"
    C_DP = "#1f6feb"

    for offset, tot, iface, col, label in [
        (-w / 2 - 0.02, sdp_tot, sdp_if, C_SDP, "CN106 ⇄ saturn：传统 SDP"),
        (+w / 2 + 0.02, dp_tot, dp_if, C_DP, "CN106 ⇄ saturn：DirectPush + DPI"),
    ]:
        show_base = offset < 0
        ax1.bar(
            x + offset, cx5, w, color=C_CX5, alpha=0.90, edgecolor="white", lw=0.6,
            label="对端 mercury CX5 自环实测 (mlx5_2)" if show_base else None,
        )
        ax1.bar(
            x + offset, cn106, w, bottom=cx5, color=C_CN106, alpha=0.85, hatch="///",
            edgecolor="#64748b", lw=0.6,
            label="CN106 eth0/NIX0+ARM 核 + 3m DAC (card_echo − CX5)" if show_base else None,
        )
        ax1.bar(
            x + offset, iface, w, bottom=echo, color=col, alpha=0.92, edgecolor="white", lw=0.6,
            label=label,
        )
        for xi, c, n, iv, t in zip(x + offset, cx5, cn106, iface, tot):
            ax1.text(xi, t + 0.25, fmt2(t), ha="center", va="bottom", fontsize=8.4, fontweight="bold", color="#0f172a")
            ax1.text(xi, c / 2, fmt2(c), ha="center", va="center", fontsize=7.6, color="white", fontweight="bold")
            ax1.text(xi, c + n / 2, fmt2(n), ha="center", va="center", fontsize=7.5, color="#1e293b", fontweight="bold")
            ax1.text(xi, c + n + iv / 2, fmt2(iv), ha="center", va="center", fontsize=7.6, color="white", fontweight="bold")

    for i in range(len(sizes)):
        diff = dp_tot[i] - sdp_tot[i]
        y_top = max(sdp_tot[i], dp_tot[i]) + 1.15
        ax1.text(
            x[i], y_top, f"净省 {fmt2(diff)} µs",
            ha="center", va="bottom", fontsize=8.2, fontweight="bold", color=C_DP,
            bbox=dict(boxstyle="round,pad=0.20", fc="#eff6ff", ec="#bfdbfe", lw=0.8),
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels(slabels, fontsize=9.4)
    ax1.set_ylabel("真实双机端到端 W=1 p50 往返延迟 (µs)", fontsize=10)
    ax1.set_ylim(0, max(sdp_tot) * 1.28)
    ax1.set_title("(a) 三段实测拆解：mercury CX5 + CN106 桥接 + 主机接口", fontsize=10.6, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=7.8, frameon=True, facecolor="white", framealpha=0.95)

    # Panel (b): Independent cross-check of CN106 front-end (card_echo - CX5 vs MRV-118 NIX0+LBK0)
    xb = np.arange(len(m118_sizes))
    wb = 0.26
    cn106_sub = [cn106[i] for i in m118_idx]
    cx5_sub = [cx5[i] for i in m118_idx]
    dp_cnt_sub = [dp_cnt[i] for i in m118_idx]

    ax2.bar(xb - wb, cx5_sub, wb * 0.92, color=C_CX5, alpha=0.90, label="mercury CX5 自环实测 (MRV-152)")
    ax2.bar(xb, cn106_sub, wb * 0.92, color="#94a3b8", alpha=0.85, label="CN106 桥接+DAC (card_echo − CX5)")
    ax2.bar(xb + wb, m118_nix, wb * 0.92, color="#0f766e", alpha=0.82, hatch="..", edgecolor="#134e4a",
            label="纯 CN106 NIX0+LBK0 (MRV-118 CNTVCT)")

    for i in range(len(m118_sizes)):
        ax2.text(xb[i] - wb, cx5_sub[i] + 0.10, fmt2(cx5_sub[i]), ha="center", va="bottom", fontsize=8.0, color="#334155", fontweight="bold")
        ax2.text(xb[i], cn106_sub[i] + 0.10, fmt2(cn106_sub[i]), ha="center", va="bottom", fontsize=8.0, color="#475569", fontweight="bold")
        ax2.text(xb[i] + wb, m118_nix[i] + 0.10, fmt2(m118_nix[i]), ha="center", va="bottom", fontsize=8.0, color="#0f766e", fontweight="bold")

    ax2.axhline(dp_cnt_sub[0], color=C_DP, ls="--", lw=1.2, alpha=0.85)
    ax2.annotate(
        f"64 B DirectPush 主机接口 {fmt2(dp_cnt_sub[0])} µs\n≈ mercury CX5 自环 {fmt2(cx5_sub[0])} µs",
        xy=(xb[0] - wb, dp_cnt_sub[0] + 0.06),
        xytext=(0.45, 3.68),
        ha="center", va="bottom", fontsize=7.8, fontweight="bold", color=C_DP,
        bbox=dict(boxstyle="round,pad=0.22", fc="#eff6ff", ec="#bfdbfe", lw=0.8),
        arrowprops=dict(arrowstyle="->", color=C_DP, lw=1.1, connectionstyle="arc3,rad=0.20"),
    )

    ax2.set_xticks(xb)
    ax2.set_xticklabels(["64 B", "256 B", "4 KiB"], fontsize=9.5)
    ax2.set_ylabel("W=1 p50 耗时 (µs)", fontsize=10)
    ax2.set_ylim(0, 5.4)
    ax2.set_title("(b) 独立交叉核对：CN106 桥接 (差仅 40–80 ns)", fontsize=10.6, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=7.8, frameon=True, facecolor="white", framealpha=0.95)

    fig.suptitle(
        "MRV-152 对端 mercury CX5 自环实测与 100GbE 双机 W=1 延迟三段拆解 (n=3 中位数，零错验真)",
        fontsize=11.4, fontweight="bold", y=0.99,
    )
    png = mrvplot.save(fig, "mrv-152-cx5-decomp")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
