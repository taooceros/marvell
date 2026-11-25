#!/usr/bin/env python3
"""MRV-187 Figure 1: Sustained throughput vs size (64 B..64 KiB) and hardware PMU
counter granularity across IIO0 / IIO1 stages, plotted strictly from
logs/mrv187-2026-11-25/runs.jsonl (n=3 interleaved medians, 100% non-mux PMU).
"""
import json
import pathlib
import statistics
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv187-2026-11-25/runs.jsonl"

SIZES = [64, 128, 256, 512, 1024, 2048, 4096, 16384, 65536]
SIZE_LABELS = ["64 B", "128 B", "256 B", "512 B", "1 KiB", "2 KiB", "4 KiB", "16 KiB", "64 KiB"]
X = np.arange(len(SIZES))


def load_runs():
    by_key = defaultdict(list)
    for line in RUNS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r["err_ops"] == 0 and r["verify"]["bad_bytes"] == 0 and r["host_pmu_min_pct"] == 100.0
        by_key[(r["suite"], r["label"])].append(r)
    return by_key


def med(vals):
    return statistics.median(vals)


def main():
    by_key = load_runs()

    dsa_bar4 = [med([r["gb_s"] for r in by_key[("rate_size", f"dsa_bar4_uc_{s}B_w32")]]) for s in SIZES]
    dsa_host = [med([r["gb_s"] for r in by_key[("rate_size", f"dsa_host_{s}B_w32")]]) for s in SIZES]
    movdir = [med([r["gb_s"] for r in by_key[("rate_size", f"movdir_bar4_uc_{s}B_sf0")]]) for s in SIZES]

    b8_sizes = [64, 256, 512, 1024, 4096]
    b8_x = [SIZES.index(s) for s in b8_sizes]
    b8_y = [med([r["gb_s"] for r in by_key[("batch_comp", f"dsa_bar4_{s}B_b8_w4")]]) for s in b8_sizes]

    # Observed PMU granularity (Bytes per counter event) on DSA -> BAR4 W=32
    b_loc_p2p = [
        med([r["completed_ops"] * r["size"] / r["host_pmu"]["iio0_loc_p2p"] for r in by_key[("rate_size", f"dsa_bar4_uc_{s}B_w32")]])
        for s in SIZES
    ]
    b_iio0_txn = [
        med([r["completed_ops"] * r["size"] / r["host_pmu"]["iio0_p2p_txn"] for r in by_key[("rate_size", f"dsa_bar4_uc_{s}B_w32")]])
        for s in SIZES
    ]
    b_iio1_tlp = [
        med([r["host_pmu"]["bytes_per_tlp"] for r in by_key[("rate_size", f"dsa_bar4_uc_{s}B_w32")]])
        for s in SIZES
    ]

    # Non-aligned verify_stream 512 B natural-boundary crossing ratios (rules out dsa2 64 B split)
    vs_192 = by_key[("verify_stream", "dsa_bar4_uc_192B")][0]
    vs_300 = by_key[("verify_stream", "dsa_bar4_uc_300B")][0]
    r192_loc = vs_192["host_pmu"]["iio0_loc_p2p"] / vs_192["completed_ops"]
    r300_loc = vs_300["host_pmu"]["iio0_loc_p2p"] / vs_300["completed_ops"]

    # IRP1 outbound re-entry amplification at 64 KiB saturation vs unsaturated baseline
    irp_64b = med([r["host_pmu"]["irp1_out_ins"] / r["host_pmu"]["iio1_out_tlp"] for r in by_key[("rate_size", "dsa_bar4_uc_64B_w32")]])
    irp_64k = med([r["host_pmu"]["irp1_out_ins"] / r["host_pmu"]["iio1_out_tlp"] for r in by_key[("rate_size", "dsa_bar4_uc_65536B_w32")]])

    # 16 KiB host RAM median op rate (half-up rounded to 2 decimals: 1.91 M/s)
    ram_16k_mops = med([r["mops_s"] for r in by_key[("rate_size", "dsa_host_16384B_w32")]])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.2, 5.6))

    # ---- Left panel: Sustained Throughput ----
    ax1.plot(X, dsa_host, ":", color="#64748b", marker="s", ms=5.5, lw=1.8, label="DSA → 主机 RAM 对照（1 引擎，W=32，固定 portal+0）")
    ax1.plot(X, movdir, "--", color="#1e293b", marker="^", ms=5.5, lw=1.8, label="CPU MOVDIR64B → BAR4（无 fence，32 行轮转）")
    ax1.plot(X, dsa_bar4, "-", color="#0f62fe", marker="o", ms=6.5, lw=2.4, label="DSA → BAR4（1 引擎，B=1，W=32，固定 portal+0）")
    ax1.plot(b8_x, b8_y, "-.", color="#0d9488", marker="D", ms=6.0, lw=2.0, label="DSA → BAR4（1 引擎，Batch B=8，W_sub=4）")

    ax1.axhline(11.457, color="#dc2626", ls=":", lw=1.3, alpha=0.85)
    ax1.text(0.05, 11.75, "Gen5×4 64 B 理论顶 11.46 GB/s", fontsize=7.9, color="#dc2626", fontweight="bold")

    # MRV-172 Stage 1 DPI ToHost reference line (105.5 Gbps = 13.19 GB/s, rules out link MPS = 64 B)
    ax1.axhline(13.19, color="#7c3aed", ls=(0, (4, 3)), lw=1.15, alpha=0.75)
    ax1.text(-0.22, 13.65, "MRV-172 Stage 1 DPI ToHost 13.19 GB/s", fontsize=7.4, color="#6d28d9", fontweight="bold")

    ax1.annotate(
        f"≥ 2 KiB 锁死 {dsa_bar4[6]:.2f} GB/s\n（与 MOVDIR64B {movdir[6]:.2f} GB/s 重合）",
        xy=(6, dsa_bar4[6]),
        xytext=(5.25, 17.0),
        fontsize=8.1,
        fontweight="bold",
        color="#0f62fe",
        bbox=dict(boxstyle="round,pad=0.3", fc="#eff6ff", ec="#93c5fd"),
        arrowprops=dict(arrowstyle="->", color="#0f62fe", lw=1.3),
    )
    ax1.annotate(
        f"Batch B=8 在 1 KiB 打满\n{b8_y[3]:.2f} GB/s（B=1 为 {dsa_bar4[4]:.2f}）",
        xy=(4, b8_y[3]),
        xytext=(1.45, 20.6),
        fontsize=8.1,
        color="#0f766e",
        bbox=dict(boxstyle="round,pad=0.28", fc="#f0fdfa", ec="#99f6e4"),
        arrowprops=dict(arrowstyle="->", color="#0d9488", lw=1.2, connectionstyle="arc3,rad=-0.16"),
    )
    ax1.annotate(
        f"16 KiB RAM: {dsa_host[7]:.2f} GB/s\n（中位 {ram_16k_mops:.2f} M/s）",
        xy=(7, dsa_host[7]),
        xytext=(6.05, 24.3),
        fontsize=7.8,
        color="#475569",
        bbox=dict(boxstyle="round,pad=0.25", fc="#f8fafc", ec="#cbd5e1"),
        arrowprops=dict(arrowstyle="->", color="#64748b", lw=1.1),
    )
    ax1.text(8, dsa_host[-1] + 0.9, f"{dsa_host[-1]:.1f}", ha="center", fontsize=8.4, color="#475569", fontweight="bold")

    ax1.set_xticks(X)
    ax1.set_xticklabels(SIZE_LABELS, fontsize=8.8)
    ax1.set_ylabel("有效载荷带宽（GB/s）", fontsize=10)
    ax1.set_xlabel("单笔 MEMMOVE 传输尺寸", fontsize=10)
    ax1.set_ylim(0, 36.5)
    ax1.set_title("A. 高并发持续写入带宽（W=32，n=3 中位数）", fontsize=10.6, fontweight="bold", pad=8)
    ax1.legend(loc="upper left", fontsize=7.5, framealpha=0.95)

    # ---- Right panel: Observed Bytes per Counter Event ----
    ax2.plot(X, b_loc_p2p, "-", color="#0f62fe", marker="o", ms=7.0, lw=2.4, label="IIO₀ loc_p2p 入口请求（B / 事件）")
    ax2.plot(X, b_iio0_txn, "--", color="#f59e0b", marker="s", ms=6.2, lw=2.2, label="IIO₀ p2p_txn（Mesh 64 B 行事务，非 TLP）")
    ax2.plot(X, b_iio1_tlp, "-", color="#dc2626", marker="x", ms=7.5, mew=2.0, lw=2.0, label="IIO₁ out_tlp 实测写包（B / PCIe TLP）")

    for i, val in enumerate(b_loc_p2p):
        if i in (0, 1, 2, 3, 6, 8):
            ax2.text(i, val + 18, f"{val:.0f} B", ha="center", fontsize=8.3, color="#0f62fe", fontweight="bold")

    ax2.annotate(
        f"dsa2 入口实发 ≤512 B（排除引擎拆 64 B）\n非对齐 192/300 B 跨 512 B 边界比 = {r192_loc:.3f} / {r300_loc:.3f}",
        xy=(4, 512.0),
        xytext=(2.95, 368),
        fontsize=7.9,
        fontweight="bold",
        color="#1d4ed8",
        bbox=dict(boxstyle="round,pad=0.3", fc="#eff6ff", ec="#93c5fd"),
        arrowprops=dict(arrowstyle="->", color="#2563eb", lw=1.25),
    )
    ax2.annotate(
        f"IIO₁ 出站严格 64.0 B / TLP（不二次合并；非 64 B 按 ceil(S/64)）\n"
        f"满速反压使 IRP₁ 出站重入 {irp_64b:.2f}×→{irp_64k:.2f}×\n"
        "resource4 vs resource4_wc 因 octep_main.c:1484 PAT 降级同为 UC-",
        xy=(5, 64.0),
        xytext=(2.35, 175),
        fontsize=7.7,
        fontweight="bold",
        color="#b91c1c",
        bbox=dict(boxstyle="round,pad=0.32", fc="#fef2f2", ec="#fca5a5"),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.35),
    )

    ax2.set_xticks(X)
    ax2.set_xticklabels(SIZE_LABELS, fontsize=8.8)
    ax2.set_ylabel("每事件 / 每 PCIe TLP 载荷字节数（B）", fontsize=10)
    ax2.set_xlabel("单笔 MEMMOVE 传输尺寸", fontsize=10)
    ax2.set_ylim(0, 710)
    ax2.set_yticks([0, 64, 128, 256, 384, 512])
    ax2.set_title("B. 硬件 PMU 实测计数粒度与反压（100% 非复用）", fontsize=10.6, fontweight="bold", pad=8)
    ax2.legend(loc="upper left", fontsize=7.7, framealpha=0.95)
    fig.suptitle(
        "MRV-187：dsa2 入口实发 ≤512 B，经 64 B Mesh 后 IIO₁ 锁死 64.0 B/TLP（≥2 KiB 封顶 11.22 GB/s）",
        fontsize=11.1,
        fontweight="bold",
    )
    out = save(fig, "mrv-187-throughput-tlp")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
