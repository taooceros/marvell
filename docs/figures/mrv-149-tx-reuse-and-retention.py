#!/usr/bin/env python3
"""MRV-149 plain-Chinese 3-panel summary chart plotted directly from verified raw runs:
- Panel (a): Unidirectional push (MRV-116 part3_h2c): immediate 64 B stack reuse vs sequential retention.
- Panel (b): Closed-loop round-trip (MRV-116 part1_w64): scaling sequential retention pool 64 KiB -> 128 MiB.
- Panel (c): Closed-loop random & stride attribution (MRV-125 p1_wc1_shaped_bts, congestion avoided):
  isolating host CPU cache set conflict (2048 B vs 2112 B) vs true 128 MiB DDR5 overflow.
"""
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean, median

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
LOG_116 = ROOT / "logs/mrv116-2026-11-20/runs.jsonl"
LOG_125 = ROOT / "logs/mrv125-2026-11-22/runs.jsonl"


def load_verified(path: Path):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in rows:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0, f"error run: {r['run_id']}"
        assert r.get("card_pmu", {}).get("corrupt", 0) == 0, f"corrupt run: {r['run_id']}"
        pf = r.get("host_pmu", {}).get("page_faults", 0)
        assert pf == 0, f"page faults {pf} in {r['run_id']}"
    return rows


def main():
    r116 = load_verified(LOG_116)
    r125 = load_verified(LOG_125)

    # Aggregate verified RX DDIO hit rate across all 297 closed-loop runs (153 + 144)
    cl_116 = [r for r in r116 if r["part"] != "part3_h2c"]
    cl_125 = [r for r in r125 if r["part"] != "p4a_h2c_ceiling"]
    assert len(cl_116) == 153 and len(cl_125) == 144
    tot_hit = sum(r["host_pmu"]["cha_io_hit_itom"] for r in cl_116 + cl_125)
    tot_miss = sum(r["host_pmu"]["cha_io_miss_itom"] for r in cl_116 + cl_125)
    tot_itom = tot_hit + tot_miss
    ddio_pct = 100.0 * tot_hit / tot_itom

    g116 = defaultdict(list)
    for r in r116:
        g116[(r["part"], r["cfg"])].append(r)

    g125 = defaultdict(list)
    for r in r125:
        g125[(r["part"], r["cfg"])].append(r)

    # Verify zero DDIO miss in 128 MiB retention subsets
    miss_116_128m = sum(r["host_pmu"]["cha_io_miss_itom"] for r in g116[("part1_w64", "fill_seq_128m")])
    miss_125_128m = sum(r["host_pmu"]["cha_io_miss_itom"] for r in g125[("p1_wc1_shaped_bts", "64B_rand_128m")])
    assert miss_116_128m == 0 and miss_125_128m == 0

    c_teal = "#0f766e"
    c_teal_dark = "#115e59"
    c_slate = "#64748b"
    c_slate_light = "#94a3b8"
    c_pitfall = "#e2e8f0"
    c_red = "#dc2626"

    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(15.4, 5.15), gridspec_kw={"width_ratios": [0.85, 1.05, 1.25]}
    )

    # ---------------- Panel (a): Unidirectional push (immediate reuse vs sequential retention) ----------------
    cfgs_a = [
        ("stack_0", "不留存\n原地复用 64 B", c_teal),
        ("fill_seq_32m", "顺序留存\n32 MiB (缓存内)", c_slate),
        ("fill_seq_128m", "顺序留存\n128 MiB (超缓存)", c_slate_light),
    ]
    xa = np.arange(len(cfgs_a))
    vals_a = [mean(x["mops"] for x in g116[("part3_h2c", c)]) for c, _, _ in cfgs_a]
    pts_a = [[x["mops"] for x in g116[("part3_h2c", c)]] for c, _, _ in cfgs_a]

    ax1.axhline(179.02, color="#64748b", linestyle=":", linewidth=1.3, zorder=1)
    ax1.text(2.48, 183.8, "PCIe Gen5 ×4 理论线速 (179.0)", ha="right", fontsize=8.3, color="#475569")

    bars_a = ax1.bar(xa, vals_a, width=0.66, color=[col for _, _, col in cfgs_a], zorder=3)
    for i in range(len(cfgs_a)):
        ax1.scatter([xa[i]] * len(pts_a[i]), pts_a[i], color="#0f172a", s=14, zorder=5)
        ax1.text(
            xa[i], vals_a[i] + 3.5, f"{vals_a[i]:.1f}",
            ha="center", va="bottom",
            fontsize=9.8 if i == 0 else 9.2,
            fontweight="bold" if i == 0 else "semibold",
            color=c_teal if i == 0 else "#334155",
            bbox=dict(boxstyle="square,pad=0.06", fc="#ffffff", ec="none", alpha=0.88) if i == 0 else None,
            zorder=6,
        )

    # Crisp inline labels inside bars with comfortable padding
    ax1.text(
        0, vals_a[0] * 0.48, "97.7% 线速\n无需缓冲池",
        ha="center", va="center", fontsize=8.0, fontweight="bold", color="#ffffff", zorder=4,
    )
    ax1.text(
        2, vals_a[2] * 0.48, "主机单核\n内存上限",
        ha="center", va="center", fontsize=8.0, fontweight="semibold", color="#0f172a", zorder=4,
    )

    ax1.set_xticks(xa)
    ax1.set_xticklabels([lbl for _, lbl, _ in cfgs_a], fontsize=8.7)
    ax1.set_ylim(0, 206)
    ax1.set_xlim(-0.55, 2.55)
    ax1.set_ylabel("单向推送吞吐（百万条/秒，越高越好）", fontsize=9.6)
    ax1.set_title("(a) 单向直推：原地复用打满线速", fontsize=10.8, fontweight="bold", pad=10)

    # ---------------- Panel (b): Closed-loop sequential retention 64 KiB -> 128 MiB ----------------
    cfgs_b = [
        ("stack_0", "原地复用\n(64 B)"),
        ("fill_seq_64k", "64 KiB"),
        ("fill_seq_4m", "4 MiB"),
        ("fill_seq_32m", "32 MiB"),
        ("fill_seq_128m", "128 MiB\n(超 60M 缓存)"),
    ]
    xb = np.arange(len(cfgs_b))
    vals_b = [mean(x["mops"] for x in g116[("part1_w64", c)]) for c, _ in cfgs_b]
    pts_b = [[x["mops"] for x in g116[("part1_w64", c)]] for c, _ in cfgs_b]
    w1_p50 = mean(x["lat_us"]["p50"] for x in g116[("part2_w1", "fill_seq_128m")])
    drop_b = 100.0 * (vals_b[-1] - vals_b[1]) / vals_b[1]

    ax2.axvline(3.5, color=c_red, linestyle="--", linewidth=1.2, alpha=0.75, zorder=1)
    ax2.text(3.43, 35.5, "主机 60 MiB\n缓存上限", ha="right", va="center", fontsize=8.4, color="#b91c1c")

    ax2.plot(xb, vals_b, color=c_teal, linewidth=2.5, marker="o", markersize=7.0, zorder=4)
    for i in range(len(cfgs_b)):
        ax2.scatter([xb[i]] * len(pts_b[i]), pts_b[i], color="#042f2e", s=14, alpha=0.65, zorder=5)
        ax2.text(
            xb[i], vals_b[i] + 1.8, f"{vals_b[i]:.1f}",
            ha="center", va="bottom", fontsize=9.2, fontweight="bold", color=c_teal_dark,
        )

    summary_b = (
        f"留存池放大 2000 倍（64 KiB → 128 MiB）\n"
        f"• 吞吐几乎不动：47.5 → 47.1 M/s（仅 {drop_b:.1f}%）\n"
        f"• 空载单包延迟始终 {w1_p50:.2f} µs（+0.0%）\n"
        f"• 网卡回写主机缓存命中 100%（0 次未命中）"
    )
    ax2.text(
        0.04, 0.10, summary_b, transform=ax2.transAxes, ha="left", va="bottom",
        fontsize=8.3, color="#0f172a", linespacing=1.45,
        bbox=dict(boxstyle="round,pad=0.45", fc="#f0fdfa", ec=c_teal, lw=1.1),
    )

    ax2.set_xticks(xb)
    ax2.set_xticklabels([lbl for _, lbl in cfgs_b], fontsize=8.7)
    ax2.set_ylim(0, 60)
    ax2.set_xlim(-0.4, 4.45)
    ax2.set_ylabel("双向往返吞吐（百万条/秒，越高越好）", fontsize=9.6)
    ax2.set_title("(b) 顺序留存放大到 128 MiB：吞吐不动", fontsize=10.8, fontweight="bold", pad=10)

    # ---------------- Panel (c): Random / 2 KiB stride pool attribution (single clean series) ----------------
    cfgs_c = [
        ("64B_seq_4m", "紧凑顺序\n4 MiB", c_slate, None, None),
        ("64B_rand_4m", "紧凑乱序\n4 MiB", c_slate, None, None),
        ("2KiB_rand_128m", "隔 2048 B\n乱序", c_pitfall, "#475569", "///"),
        ("2112B_rand_132m", "隔 2112 B\n乱序", c_teal, None, None),
        ("64B_rand_128m", "真 128 MiB\n全随机", c_slate_light, None, None),
    ]
    xc = np.arange(len(cfgs_c))
    vals_c = [median(x["mops"] for x in g125[("p1_wc1_shaped_bts", c)]) for c, *_ in cfgs_c]
    pts_c = [[x["mops"] for x in g125[("p1_wc1_shaped_bts", c)]] for c, *_ in cfgs_c]

    for i, (_, _, col, ec, hatch) in enumerate(cfgs_c):
        ax3.bar(
            xc[i], vals_c[i], width=0.64,
            color=col, edgecolor=ec if ec else col,
            linewidth=1.2 if ec else 0.0, hatch=hatch, zorder=3,
        )
        ax3.scatter([xc[i]] * len(pts_c[i]), pts_c[i], color="#0f172a", s=14, zorder=5)
        ax3.text(
            xc[i], vals_c[i] + 1.3, f"{vals_c[i]:.1f}",
            ha="center", va="bottom",
            fontsize=9.8 if i == 3 else 9.2,
            fontweight="bold" if i in (2, 3) else "semibold",
            color=c_teal if i == 3 else "#334155",
        )

    # Causal annotations in clean open space
    ax3.annotate(
        "多隔 64 B 错开缓存槽\n立刻翻倍恢复",
        xy=(3 - 0.20, vals_c[3] + 0.8),
        xytext=(2.0, 50.8),
        ha="center", va="center",
        fontsize=8.3, fontweight="bold", color=c_teal_dark,
        bbox=dict(boxstyle="round,pad=0.32", fc="#f0fdfa", ec=c_teal, lw=1.0),
        arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=-0.16", color=c_teal, lw=1.4),
    )
    ax3.text(
        2, vals_c[2] * 0.46, "2 的幂步长\n缓存撞槽",
        ha="center", va="center", fontsize=7.6, fontweight="bold", color="#1e293b",
        bbox=dict(boxstyle="round,pad=0.18", fc="#ffffff", ec="none", alpha=0.92),
        zorder=6,
    )
    ax3.annotate(
        "真超 60M 缓存：\n主机内存上限\n(网卡命中仍 100%)",
        xy=(4, vals_c[4] + 4.8),
        xytext=(3.98, 43.0),
        ha="center", va="center",
        fontsize=8.1, color="#334155",
        bbox=dict(boxstyle="round,pad=0.32", fc="#f8fafc", ec="#94a3b8", lw=0.9),
        arrowprops=dict(arrowstyle="->", color="#475569", lw=1.1),
    )

    ax3.set_xticks(xc)
    ax3.set_xticklabels([lbl for _, lbl, *_ in cfgs_c], fontsize=8.6)
    ax3.set_ylim(0, 62)
    ax3.set_xlim(-0.55, 4.65)
    ax3.set_ylabel("双向往返吞吐（百万条/秒，越高越好）", fontsize=9.6)
    ax3.set_title("(c) 2 KiB 乱序池掉速拆因：全是主机本地原因", fontsize=10.8, fontweight="bold", pad=10)

    fig.suptitle(
        "MRV-149 实测结果：小包直推可立刻复用打满线速；主机大池留存不产生任何网卡一致性开销",
        fontsize=12.0, fontweight="bold", y=0.985,
    )
    fig.text(
        0.5, 0.922,
        f"固定条件：64 B 消息、1 核主机 (3.9 GHz) + 1 核网卡 (2.0 GHz)、硬件 IOMMU 开启；"
        f"跨 297 轮闭环（{tot_itom/1e8:.2f} 亿次网卡回写）0 错包，主机缓存命中 {ddio_pct:.5f}%",
        ha="center", va="center", fontsize=9.2, color="#475569",
    )

    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.89))
    out_png = Path(__file__).resolve().parent / "mrv-149-tx-reuse-and-retention.png"
    out_svg = Path(__file__).resolve().parent / "mrv-149-tx-reuse-and-retention.svg"
    fig.savefig(out_png)
    fig.savefig(out_svg)
    plt.close(fig)
    print(f"Saved {out_png} & {out_svg}; DDIO={ddio_pct:.6f}% ({tot_miss}/{tot_itom})")


if __name__ == "__main__":
    main()
