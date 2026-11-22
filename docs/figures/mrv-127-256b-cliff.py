#!/usr/bin/env python3
"""Figure 2 (MRV-127): 256 B & 512 B slow-cliff elimination + mixed <= 256 B threshold trade-off.

Reads raw verified runs directly from logs/mrv127-2026-11-22/runs.jsonl.
Revised per MRV-138 review:
  - Panel A adds exact W=1 Push vs DMA ratio (2.21x) and single-stream slow-cliff threshold (80->96 lines).
  - Panel B explicitly marks that NO mix256 config meets <= 2.5 us, calls out shr32 rep 0 bistable crash
    (2,195 us at 84 lines), attributes C_line=48 crash to concurrent DPI DMA lowering the slow-cliff
    threshold (13.76 M lines/s), and attributes t=256 overload to 3.5 M/s DMA saturating core 3 + extra sfence.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import mrvplot

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs/mrv127-2026-11-22/runs.jsonl"
MRV117_SDP_256_W32 = 4.508  # MRV-117 verified median (logs/mrv117-2026-11-20/runs.jsonl)


def load_by_cfg():
    by_cfg = defaultdict(list)
    with RUNS.open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("part") != "part0_cal":
                assert r["verified"]
                assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
                by_cfg[r["cfg"]].append(r)
    return by_cfg


def main():
    by_cfg = load_by_cfg()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.5))

    # ---- Panel A: 256 B W=32 single-variable C_line sweep vs dma_threshold=256 vs uncap ----
    items_a = [
        ("p2_256_w32_t1024_uncap_seq",  "t=1024\n无上限\n(128行)", "#c0392b"),
        ("p2_256_w32_t512_cl24_seq",    "t=512\nC=24\n(6包)",     "#4a90c2"),
        ("p2_256_w32_t512_cl32_seq",    "t=512\nC=32\n(8包)",     "#1b6ca8"),
        ("p2_256_w32_t512_cl40_seq",    "t=512\nC=40\n(10包)",    "#1b6ca8"),
        ("p2_256_w32_t512_cl48_seq",    "t=512\nC=48\n(12包)",    "#1b6ca8"),
        ("p2_256_w32_t512_cl64_seq",    "t=512\nC=64\n(16包)",    "#2c7fb8"),
        ("p2_256_w32_t512_cl80_seq",    "t=512\nC=80\n(边界)",    "#f39c12"),
        ("p2_256_w32_t512_cl96_seq",    "t=512\nC=96\n(坠崖)",    "#c0392b"),
        ("p2_256_w32_t256_dma_seq",     "t=256\nDPI\nDMA",        "#27ae60"),
    ]
    xs = np.arange(len(items_a))
    med_thr, med_p99 = [], []
    for i, (cfg, _, col) in enumerate(items_a):
        rs = by_cfg[cfg]
        thrs = [r["mreq_s"] for r in rs]
        p99s = [r["lat_small_tot_us"]["p99"] for r in rs]
        med_thr.append(statistics.median(thrs))
        med_p99.append(statistics.median(p99s))
        ax1.bar(i, med_thr[-1], width=0.62, color=col, alpha=0.86, edgecolor="#333", linewidth=0.7, zorder=3)
        ax1.scatter([i + j for j in (-0.12, 0.0, 0.12)], thrs, color="#111", s=16, alpha=0.75, zorder=5)
        ax1.text(i, max(thrs) + 0.16, f"{med_thr[-1]:.2f}", ha="center", va="bottom", fontsize=8.0, fontweight="bold")

    ax1.axhline(MRV117_SDP_256_W32, color="#555", linestyle="--", linewidth=1.3, zorder=4)
    ax1.text(-0.38, MRV117_SDP_256_W32 + 0.22, f"MRV-117 SDP ({MRV117_SDP_256_W32:.2f} M/s)",
             fontsize=8.0, color="#444", bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=1.2), zorder=7)

    ax1_r = ax1.twinx()
    ax1_r.plot(xs, med_p99, color="#8e44ad", marker="o", linestyle="-", linewidth=1.8, markersize=5.5, label="W=32 p99 延迟 (µs)", zorder=6)
    ax1_r.set_ylabel("256 B W=32 p99 往返延迟 (µs)", color="#8e44ad")
    ax1_r.tick_params(axis="y", colors="#8e44ad")
    ax1_r.set_ylim(0, 12.2)
    ax1_r.grid(False)
    ax1_r.spines["top"].set_visible(False)

    r512_push = statistics.median(r["mreq_s"] for r in by_cfg["p2_512_w32_t1024_push_rand"])
    r512_dma = statistics.median(r["mreq_s"] for r in by_cfg["p2_512_w32_t512_dma_rand"])
    w1_push = statistics.median(r["lat_small_tot_us"]["p50"] for r in by_cfg["p2_256_w1_t512_push_rand"])
    w1_dma = statistics.median(r["lat_small_tot_us"]["p50"] for r in by_cfg["p2_256_w1_t256_dma_rand"])
    cl32_rand = statistics.median(r["mreq_s"] for r in by_cfg["p2_256_w32_t512_cl32_rand"])
    cl32_rand_p99 = statistics.median(r["lat_small_tot_us"]["p99"] for r in by_cfg["p2_256_w32_t512_cl32_rand"])
    ax1.text(
        0.02, 0.97,
        f"512 B W=32: {r512_push:.2f} → {r512_dma:.2f} M/s ({r512_dma/r512_push:.2f}×)\n"
        f"256 B W=1 p50: Push {w1_push:.2f} vs DMA {w1_dma:.2f} µs ({w1_dma/w1_push:.2f}×)\n"
        f"256 B rand C=32: {cl32_rand:.2f} M/s (p99 {cl32_rand_p99:.2f} µs, 超 SDP +{(cl32_rand/MRV117_SDP_256_W32-1)*100:.1f}%)\n"
        f"单流无 DMA 慢崖: 80 → 96 行",
        transform=ax1.transAxes, va="top", ha="left", fontsize=7.8,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="#ccc", alpha=0.92),
    )

    ax1.set_xticks(xs)
    ax1.set_xticklabels([it[1] for it in items_a], fontsize=8.0)
    ax1.set_ylim(0, 11.2)
    ax1.set_ylabel("256 B 单核闭环吞吐 (Mreq/s，W=32)")
    ax1.set_title("A · 单流 256 B W=32 慢崖消除：C_line vs 降阈值 DMA", fontsize=10.7, fontweight="bold")
    ax1_r.legend(loc="upper right", fontsize=8.0, frameon=True)

    # ---- Panel B: Mixed <= 256 B (75% 64 B + 25% 256 B) + 4 KiB rand @ 1.5 M/s ----
    groups = [
        ("6M + 1.5M", "6m_1p5m"),
        ("8M + 1.5M", "8m_1p5m"),
    ]
    cfgs_b = [
        ("shr32_t1024",     "共享 W=32, t=1024（无 C_line）",           "#7f8c8d"),
        ("spl32_t512_cl32", "拆分 (32,32) t=512 C=32（最优，仍 >2.5 µs）", "#1b6ca8"),
        ("spl32_t512_cl48", "拆分 (32,32) t=512 C=48（并发 DMA 压低慢崖）", "#f39c12"),
        ("spl32_t256",      "拆分 (32,32) t=256（3.5M DMA 过载 + HoL）", "#27ae60"),
    ]
    x_base = np.arange(len(groups))
    bw = 0.19
    bar_pos = {}
    for ci, (ckey, clabel, col) in enumerate(cfgs_b):
        offset = (ci - 1.5) * bw
        for gi, (_, gtag) in enumerate(groups):
            rs = by_cfg[f"p3_mix256_{gtag}_{ckey}"]
            ys_t = [r["lat_small_tot_us"]["p99"] for r in rs]
            ys_w = [r["lat_small_wire_us"]["p99"] for r in rs]
            mt = statistics.median(ys_t)
            mw = statistics.median(ys_w)
            xpos = gi + offset
            bar_pos[(gtag, ckey)] = (xpos, mt, ys_t, rs)
            ax2.bar(xpos, mt, width=bw * 0.9, color=col, alpha=0.88, edgecolor="#333", linewidth=0.7,
                    label=clabel if gi == 0 else None, zorder=3)
            ax2.scatter([xpos + j for j in (-0.03, 0.0, 0.03)], ys_t, color="#111", s=16, alpha=0.72, zorder=5)
            ax2.plot([xpos - bw * 0.35, xpos + bw * 0.35], [mw, mw], color="#fff", linewidth=2.2, zorder=6)
            ax2.plot([xpos - bw * 0.35, xpos + bw * 0.35], [mw, mw], color="#111", linewidth=1.0, zorder=7)
            lbl_txt = f"{mt/1000:.2f}ms" if 1000 <= mt < 10000 else (f"{mt/1000:.1f}ms" if mt >= 10000 else f"{mt:.1f}")
            y_txt = mt * 1.24 if not (gtag == "8m_1p5m" and ckey == "spl32_t512_cl48") else mt * 1.32
            ax2.text(xpos, y_txt, lbl_txt, ha="center", va="bottom", fontsize=7.8, fontweight="bold")

    # Explicitly annotate the three critical review findings on 8M + 1.5M from raw runs.jsonl
    x_shr, _, ys_shr, rs_shr = bar_pos[("8m_1p5m", "shr32_t1024")]
    rep0_shr = rs_shr[0]["lat_small_tot_us"]["p99"]
    rep0_maxl = rs_shr[0]["max_push_lines_inflight"]
    ax2.annotate(
        f"rep 0: {rep0_shr/1000:.2f} ms\n({rep0_maxl} 行双稳态坠崖)",
        xy=(x_shr - 0.03, rep0_shr), xytext=(0.17, 1300),
        arrowprops=dict(arrowstyle="->", color="#555", lw=1.0), fontsize=7.6, color="#333",
        bbox=dict(facecolor="white", edgecolor="#ccc", alpha=0.92, pad=1.8),
    )

    x_cl48, mt_cl48, _, rs_cl48 = bar_pos[("8m_1p5m", "spl32_t512_cl48")]
    cl48_iio = statistics.median(r["host_pmu"]["iio1_cpu_wr"] / r["secs"] / 1e6 for r in rs_cl48)
    ax2.annotate(
        f"并发 DMA 拉低慢崖\n锁死 {cl48_iio:.2f} M行/s",
        xy=(x_cl48, mt_cl48), xytext=(0.35, 11500),
        arrowprops=dict(arrowstyle="->", color="#c07300", lw=1.0), fontsize=7.6, color="#9a5c00",
        bbox=dict(facecolor="white", edgecolor="#f0d5a8", alpha=0.92, pad=1.8),
    )

    x_t256, mt_t256, _, rs_t256 = bar_pos[("8m_1p5m", "spl32_t256")]
    t256_sm = statistics.median(r["achieved_small_mrps"] for r in rs_t256)
    ax2.annotate(
        f"sm={t256_sm:.2f} M/s\n3.5M DMA 满核\n+ 多余 sfence",
        xy=(x_t256, mt_t256 * 1.45), xytext=(1.38, 7500), ha="center",
        arrowprops=dict(arrowstyle="->", color="#1e7e34", lw=1.0), fontsize=7.4, color="#19692c",
        bbox=dict(facecolor="white", edgecolor="#b8e0c2", alpha=0.94, pad=1.8),
    )

    ax2.axhline(2.5, color="#2e7d32", linestyle="--", linewidth=1.1, alpha=0.85)
    ax2.text(0.50, 3.0, "2.5 µs 目标\n(全未达标)", ha="center", va="bottom", fontsize=7.6, color="#2e7d32",
             bbox=dict(facecolor="white", edgecolor="none", alpha=0.88, pad=1.0))
    ax2.set_yscale("log")
    ax2.set_ylim(1.0, 280000)
    ax2.set_xlim(-0.48, 1.72)
    ax2.set_xticks(x_base)
    ax2.set_xticklabels([g[0] for g in groups], fontsize=9.5, fontweight="bold")
    ax2.set_xlabel("混合小包流 (75% 64 B + 25% 256 B) + 4 KiB 随机 KV @ 1.5 Mreq/s")
    ax2.set_ylabel("小包流 (mix256) 端到端 tot p99 (µs，对数；横线=wire p99)")
    ax2.set_title("B · 含 256 B 开环混流：全数 >2.5 µs，t=512+C=32 相对最优", fontsize=10.7, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=7.6, frameon=True, framealpha=0.92)

    fig.suptitle(
        "MRV-127 256 B 慢崖消除与含 256 B 混流阈值权衡（1H + 1C + 2 DPI VFs，零 SDP，n=3 全散点 + 中位数）",
        fontsize=11.3, fontweight="bold", y=0.995,
    )
    out = mrvplot.save(fig, "mrv-127-256b-cliff")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
