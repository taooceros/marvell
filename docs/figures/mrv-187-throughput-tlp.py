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

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.8, 5.4))

    # ---- Left panel: Sustained Throughput ----
    ax1.plot(X, dsa_host, ":", color="#64748b", marker="s", ms=5.5, lw=1.8, label="DSA → 主机 RAM 对照（1 引擎，W=32）")
    ax1.plot(X, movdir, "--", color="#1e293b", marker="^", ms=5.5, lw=1.8, label="CPU MOVDIR64B → BAR4（无 fence）")
    ax1.plot(X, dsa_bar4, "-", color="#0f62fe", marker="o", ms=6.5, lw=2.4, label="DSA → BAR4（1 引擎，B=1，W=32）")
    ax1.plot(b8_x, b8_y, "-.", color="#0d9488", marker="D", ms=6.0, lw=2.0, label="DSA → BAR4（1 引擎，Batch B=8）")

    ax1.axhline(11.457, color="#dc2626", ls=":", lw=1.3, alpha=0.8)
    ax1.text(0.05, 12.15, "Gen5×4 64 B 理论顶 11.46 GB/s", fontsize=8.4, color="#dc2626", fontweight="bold")

    ax1.annotate(
        f"≥ 2 KiB 锁死 {dsa_bar4[6]:.2f} GB/s\n（与 MOVDIR64B {movdir[6]:.2f} GB/s 重合）",
        xy=(6, dsa_bar4[6]),
        xytext=(3.75, 16.5),
        fontsize=8.6,
        fontweight="bold",
        color="#0f62fe",
        bbox=dict(boxstyle="round,pad=0.3", fc="#eff6ff", ec="#93c5fd"),
        arrowprops=dict(arrowstyle="->", color="#0f62fe", lw=1.3),
    )
    ax1.annotate(
        f"Batch B=8 在 1 KiB 打满\n{b8_y[3]:.2f} GB/s（B=1 为 {dsa_bar4[4]:.2f}）",
        xy=(4, b8_y[3]),
        xytext=(0.15, 19.6),
        fontsize=8.3,
        color="#0f766e",
        bbox=dict(boxstyle="round,pad=0.28", fc="#f0fdfa", ec="#99f6e4"),
        arrowprops=dict(arrowstyle="->", color="#0d9488", lw=1.2),
    )
    ax1.text(8, dsa_host[-1] + 0.9, f"{dsa_host[-1]:.1f}", ha="center", fontsize=8.5, color="#475569", fontweight="bold")

    ax1.set_xticks(X)
    ax1.set_xticklabels(SIZE_LABELS, fontsize=8.8)
    ax1.set_ylabel("有效载荷带宽（GB/s）", fontsize=10)
    ax1.set_xlabel("单笔 MEMMOVE 传输尺寸", fontsize=10)
    ax1.set_ylim(0, 35.5)
    ax1.set_title("A. 高并发持续写入带宽（W=32，n=3 中位数）", fontsize=10.8, fontweight="bold", pad=8)
    ax1.legend(loc="upper left", fontsize=8.0, framealpha=0.95)

    # ---- Right panel: Observed Bytes per Counter Event ----
    ax2.plot(X, b_loc_p2p, "-", color="#0f62fe", marker="o", ms=7.0, lw=2.4, label="IIO₀ loc_p2p 计数粒度（B / 事件）")
    ax2.plot(X, b_iio0_txn, "--", color="#f59e0b", marker="s", ms=6.2, lw=2.2, label="IIO₀ p2p_txn 计数粒度（B / 事务）")
    ax2.plot(X, b_iio1_tlp, "-", color="#dc2626", marker="x", ms=7.5, mew=2.0, lw=2.0, label="IIO₁ out_tlp 实测写包（B / PCIe TLP）")

    for i, val in enumerate(b_loc_p2p):
        if i in (0, 1, 2, 3, 6, 8):
            ax2.text(i, val + 18, f"{val:.0f} B", ha="center", fontsize=8.4, color="#0f62fe", fontweight="bold")
    ax2.annotate(
        "全尺寸 PCIe 写包严格 64.0 B / TLP\n（UC/WC 相同，非 64 B 尺寸按 ceil(S/64) 拆包）",
        xy=(5, 64.0),
        xytext=(2.1, 225),
        fontsize=8.6,
        fontweight="bold",
        color="#b91c1c",
        bbox=dict(boxstyle="round,pad=0.32", fc="#fef2f2", ec="#fca5a5"),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.4),
    )

    ax2.set_xticks(X)
    ax2.set_xticklabels(SIZE_LABELS, fontsize=8.8)
    ax2.set_ylabel("每事件 / 每 PCIe TLP 载荷字节数（B）", fontsize=10)
    ax2.set_xlabel("单笔 MEMMOVE 传输尺寸", fontsize=10)
    ax2.set_ylim(0, 595)
    ax2.set_yticks([0, 64, 128, 256, 384, 512])
    ax2.set_title("B. 硬件 PMU 实测计数粒度（100% 非复用）", fontsize=10.8, fontweight="bold", pad=8)
    ax2.legend(loc="center right", bbox_to_anchor=(0.99, 0.72), fontsize=8.1, framealpha=0.95)

    fig.suptitle(
        "MRV-187：DSA 直写 CN106 BAR4 在 ≥ 2 KiB 打满 11.22 GB/s，但链路上全尺寸锁定 64 B TLP",
        fontsize=11.4,
        fontweight="bold",
    )
    out = save(fig, "mrv-187-throughput-tlp")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
