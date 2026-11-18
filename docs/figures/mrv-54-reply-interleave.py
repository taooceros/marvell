"""MRV-54: 2x2 experiment separating request cell ownership (by sequence vs by 128 B block)
from reply segment layout (contiguous per card core vs interleaved across two segments).

Reads logs/mrv54-2026-11-18/runs.jsonl and plots:
- Row 1: closed-loop throughput (Mmsg/s) for each W in [64, 128, 256, 1024] across the 4 groups.
- Row 2: host IIO line writes (0x84 / reply) and full-line ItoM ratio (unc_cha_tor_inserts.io_itom / reply).
- Row 3: host out-of-order wait rate (polls waited on a missing reply while a later one had landed, k / Mmsg)
         with empty-poll percentage (empty_pct) annotated above each bar.

    uv run --with matplotlib python /home/hongtao/dev/marvell-mrv54/docs/figures/mrv-54-reply-interleave.py
"""
import json
from collections import defaultdict
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv54-2026-11-18/runs.jsonl"

ORDER = ["base", "base-interleaved", "blocks-tag", "blocks-contig"]
LABEL = {
    "base": "按序号取\n+ 本段连续\n(G1)",
    "base-interleaved": "按序号取\n+ 跨段交错\n(G4)",
    "blocks-tag": "按 128B 块取\n+ 跨段交错\n(G2)",
    "blocks-contig": "按 128B 块取\n+ 本段连续\n(G3)",
}
COLOR = {
    "base": "#666666",
    "base-interleaved": "#1f77b4",
    "blocks-tag": "#d62728",
    "blocks-contig": "#e66101",
}

runs = defaultdict(list)
for line in RUNS.read_text().splitlines():
    if not line.strip():
        continue
    r = json.loads(line)
    h = r["host"]
    c = r["card"]
    iio = r["iio"]
    clk = r["clocks"]
    assert r["rc"] == 0, r["tag"]
    assert h["lost"] == 0 and h["bad"] == 0 and h["torn"] == 0, r["tag"]
    assert c["corrupt"] == 0 and c["reflected"] == h["rounds"] + h["warmup"], r["tag"]
    assert clk["cpu40_gov"] == "performance" and clk["uncore1_khz"] == "2200000", r["tag"]
    assert clk["cpu40_ghz_mean"] >= 3.75, r["tag"]
    runs[r["window"]].append(r)

windows = sorted(runs)
fig, axes = plt.subplots(3, len(windows), figsize=(3.85 * len(windows), 8.8), squeeze=False)

for col, w in enumerate(windows):
    ax_top = axes[0][col]
    ax_mid = axes[1][col]
    ax_bot = axes[2][col]
    w_runs = runs[w]

    all_mmsg = [r["host"]["mmsg"] for r in w_runs]
    lo, hi = min(all_mmsg), max(all_mmsg)
    span = max(0.18, hi - lo)
    pad = span * 0.32

    # Row 1: Throughput (Mmsg/s)
    for i, g in enumerate(ORDER):
        g_runs = [r for r in w_runs if r["group"] == g]
        ys = [r["host"]["mmsg"] for r in g_runs]
        if not ys:
            continue
        col_g = COLOR[g]
        ax_top.scatter([i] * len(ys), ys, s=32, color=col_g, alpha=0.8, zorder=3)
        mean_y = sum(ys) / len(ys)
        ax_top.hlines(mean_y, i - 0.25, i + 0.25, color=col_g, lw=2.2, zorder=4)
        top_y = max(ys) + span * 0.06
        ax_top.text(i, top_y, f"{mean_y:.2f}", ha="center", va="bottom", fontsize=8.5, color=col_g)
        if w == 1024 and g == "base":
            ex62 = [r["host"]["mmsg"] for r in g_runs if r["clocks"]["cpu40_ghz_mean"] >= 3.88]
            dip = min(ys)
            ax_top.annotate(
                f"#62 (3.80GHz)\n剔除后均 {sum(ex62)/len(ex62):.2f}",
                xy=(i, dip),
                xytext=(i + 0.02, dip - 0.95),
                fontsize=7.2,
                color="#444444",
                arrowprops={"arrowstyle": "->", "color": "#888888", "lw": 0.8},
            )

    base_ys = [r["host"]["mmsg"] for r in w_runs if r["group"] == "base"]
    if base_ys:
        ax_top.axhline(sum(base_ys) / len(base_ys), color="#666666", lw=0.9, ls="--", zorder=1)

    ax_top.set_ylim(lo - pad * 0.65, hi + pad * 1.15)
    ax_top.set_xticks(range(len(ORDER)), [""] * len(ORDER))
    ax_top.set_xlim(-0.55, len(ORDER) - 0.45)
    title_suffix = "（快慢双态参照）" if w == 1024 else "（慢状态）"
    ax_top.set_title(f"卡 4 核，W = {w}{title_suffix}", fontsize=10)

    # Row 2: IIO line writes (0x84) & full-line ItoM per reply
    for i, g in enumerate(ORDER):
        itom_ys = [r["iio"]["itom_per_reply"] for r in w_runs if r["group"] == g]
        lwr_ys = [r["iio"]["line_writes_per_reply"] for r in w_runs if r["group"] == g]
        if not itom_ys:
            continue
        col_g = COLOR[g]
        mean_itom = sum(itom_ys) / len(itom_ys)
        mean_lwr = sum(lwr_ys) / len(lwr_ys)
        ax_mid.bar(
            i - 0.14,
            mean_lwr,
            width=0.25,
            color="#cccccc",
            edgecolor="#777777",
            lw=0.8,
            label="IIO 行写 (0x84)/条" if (col == 0 and i == 0) else None,
            zorder=2,
        )
        ax_mid.bar(
            i + 0.14,
            mean_itom,
            width=0.25,
            color=col_g,
            alpha=0.85,
            label="整行 ItoM/条" if (col == 0 and i == 0) else None,
            zorder=3,
        )
        ax_mid.text(i, max(mean_lwr, mean_itom) + 0.015, f"{mean_itom:.4f}", ha="center", va="bottom", fontsize=7.5, color="#222")

    ax_mid.axhline(1.0, color="#444444", lw=0.8, ls=":", zorder=1)
    ax_mid.set_ylim(0.0, 1.16)
    ax_mid.set_xticks(range(len(ORDER)), [""] * len(ORDER))
    ax_mid.set_xlim(-0.55, len(ORDER) - 0.45)
    if col == 0:
        ax_mid.legend(loc="lower right", fontsize=7.5, framealpha=0.9)

    # Row 3: Host out-of-order wait rate (blocked_per_mmsg in k/Mmsg) & empty_pct annotation
    blk_all = [r["host"]["blocked_per_mmsg"] / 1000.0 for r in w_runs]
    blk_hi = max(blk_all) if blk_all else 1.0
    for i, g in enumerate(ORDER):
        g_runs = [r for r in w_runs if r["group"] == g]
        blks = [r["host"]["blocked_per_mmsg"] / 1000.0 for r in g_runs]
        emps = [r["host"]["empty_pct"] for r in g_runs]
        if not blks:
            continue
        col_g = COLOR[g]
        mean_blk = sum(blks) / len(blks)
        mean_emp = sum(emps) / len(emps)
        ax_bot.bar(i, mean_blk, width=0.44, color=col_g, alpha=0.75, zorder=2)
        ax_bot.scatter([i] * len(blks), blks, s=18, color="#222222", alpha=0.7, zorder=3)
        ax_bot.text(
            i,
            max(blks) + blk_hi * 0.04,
            f"{mean_blk:.1f}k\n(空 {mean_emp:.1f}%)",
            ha="center",
            va="bottom",
            fontsize=7.3,
            color="#222",
        )

    ax_bot.set_ylim(0.0, blk_hi * 1.32)
    ax_bot.set_xticks(range(len(ORDER)), [LABEL[g] for g in ORDER], fontsize=7.8)
    ax_bot.set_xlim(-0.55, len(ORDER) - 0.45)

axes[0][0].set_ylabel("闭环吞吐 (Mmsg/s)")
axes[1][0].set_ylabel("每条回复 PCIe 写事件")
axes[2][0].set_ylabel("乱序等待 (k/百万条)")
fig.suptitle(
    "MRV-54 2×2 实验：请求取法（按序号 vs 按 128 B 块）× 回复落点（本段连续 vs 跨段交错）\n"
    "写合并回程 (--store --store-wc)，64 B，主机核 40 (~3.90 GHz, performance)，卡 4 核 (2.0 GHz)，uncore 2.2 GHz，批 1，每组 4 次 × 20M 轮",
    fontsize=9.5,
)
print(save(fig, "mrv-54-reply-interleave"))
