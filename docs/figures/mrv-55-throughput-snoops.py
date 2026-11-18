"""MRV-55: Card core-to-core coherency granularity — throughput and TAD snoop counts across distances.

Reads logs/mrv55-2026-11-18/runs.jsonl (5 reps x 1.0 s per mode and distance on card lcores 3, 4)
and plots per-core operation rate and TAD snoop / L2 refill rates at distances 0 B, 64 B, 128 B,
256 B, and 4 KiB.

    uv run --with matplotlib python docs/figures/mrv-55-throughput-snoops.py
"""
import json
from collections import defaultdict
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv55-2026-11-18/runs.jsonl"

DISTS = [0, 64, 128, 256, 4096]
DIST_LABELS = ["0 B\n(同行)", "64 B\n(同128B块)", "128 B\n(邻块)", "256 B", "4 KiB\n(跨页)"]

records = [json.loads(line) for line in RUNS.read_text().splitlines()]
by_key = defaultdict(list)
for r in records:
    assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0, f"corrupt run: {r}"
    assert r["cpus"] == [3, 4] and r["tsc_hz"] == 1_000_000_000
    by_key[(r["mode"], r["dist_bytes"])].append(r)

fig, axes = plt.subplots(2, 2, figsize=(11.2, 7.6))
x_idx = list(range(len(DISTS)))


def plot_series(ax, mode, key_fn, label, color, marker="o", offset=0.0, ls="-"):
    means = []
    for i, d in enumerate(DISTS):
        rs = by_key[(mode, d)]
        assert len(rs) == 5, f"expected 5 reps for {mode} d={d}"
        ys = [key_fn(r) for r in rs]
        ax.scatter([i + offset] * len(ys), ys, s=24, color=color, marker=marker, alpha=0.7, zorder=3)
        means.append(sum(ys) / len(ys))
    ax.plot([i + offset for i in x_idx], means, color=color, lw=1.8, ls=ls, label=label, zorder=4)
    return means


# Panel (0, 0): (a) Write-only 64 B line throughput
ax = axes[0, 0]
m0 = plot_series(ax, "write_only", lambda r: r["core0_mops"], "卡核 3 (offset 0)", "#d62728", "o", -0.06)
m1 = plot_series(ax, "write_only", lambda r: r["core1_mops"], "卡核 4 (offset d)", "#1f77b4", "s", +0.06)
ax.annotate(f"0 B: {m0[0]:.1f} / {m1[0]:.1f} M/s", xy=(0, m1[0]), xytext=(0.35, 110),
            fontsize=8.5, arrowprops=dict(arrowstyle="->", color="#555555", lw=0.8))
ax.annotate(f"64 B..4 KiB: {m0[1]:.1f} M/s\n(与 128 B/4 KiB 完全相同)", xy=(1, m0[1]), xytext=(1.15, 185),
            fontsize=8.5, color="#222222", arrowprops=dict(arrowstyle="->", color="#555555", lw=0.8))
ax.set_xticks(x_idx, DIST_LABELS, fontsize=8.5)
ax.set_ylabel("每核写吞吐 (Mops/s)")
ax.set_ylim(0, 295)
ax.set_title("(a) 只写 64 B 行（Cell 发布，1 s × 5 次）", fontsize=10)
ax.legend(loc="lower right", fontsize=8.5, frameon=True)

# Panel (0, 1): (b) Read-modify-write 64 B line throughput
ax = axes[0, 1]
rmw0 = plot_series(ax, "rmw", lambda r: r["core0_mops"], "读改写 卡核 3", "#d62728", "o", -0.08)
rmw1 = plot_series(ax, "rmw", lambda r: r["core1_mops"], "读改写 卡核 4", "#1f77b4", "s", -0.02)
at0 = plot_series(ax, "atomic_rmw", lambda r: r["core0_mops"], "原子 RMW 卡核 3", "#ff7f0e", "^", +0.04, "--")
at1 = plot_series(ax, "atomic_rmw", lambda r: r["core1_mops"], "原子 RMW 卡核 4", "#2ca02c", "v", +0.10, "--")
ax.annotate("0 B: 7.7–14.4 M/s", xy=(0, rmw1[0]), xytext=(0.35, 24),
            fontsize=8.5, arrowprops=dict(arrowstyle="->", color="#555555", lw=0.8))
ax.annotate(f"64 B..4 KiB:\n读改写 {rmw0[1]:.2f} M/s\n原子 RMW {at0[1]:.2f} M/s", xy=(1, rmw0[1]), xytext=(1.3, 38),
            fontsize=8.5, arrowprops=dict(arrowstyle="->", color="#555555", lw=0.8))
ax.set_xticks(x_idx, DIST_LABELS, fontsize=8.5)
ax.set_ylabel("每核 RMW 吞吐 (Mops/s)")
ax.set_ylim(0, 90)
ax.set_title("(b) 读改写 64 B 行（普通 RMW 与 LSE 原子 RMW，1 s × 5 次）", fontsize=10)
ax.legend(loc="lower right", fontsize=8, frameon=True, ncol=2)

# Panel (1, 0): (c1) Poll + Bystander write throughput
ax = axes[1, 0]
p0 = plot_series(ax, "poll_bystander", lambda r: r["core0_mops"], "卡核 3 轮询 (offset 0)", "#d62728", "o", -0.05)
p1 = plot_series(ax, "poll_bystander", lambda r: r["core1_mops"], "卡核 4 连续写 (offset d)", "#1f77b4", "s", +0.05)
ax.annotate(f"0 B: 轮询 {p0[0]:.1f} / 写 {p1[0]:.1f} M/s", xy=(0, p0[0]), xytext=(0.4, 150),
            fontsize=8.5, arrowprops=dict(arrowstyle="->", color="#555555", lw=0.8))
ax.annotate(f"64 B..4 KiB: 轮询 {p0[1]:.1f} M/s，写 {p1[1]:.1f} M/s", xy=(1, p0[1]), xytext=(1.2, 430),
            fontsize=8.5, arrowprops=dict(arrowstyle="->", color="#555555", lw=0.8))
ax.set_xticks(x_idx, DIST_LABELS, fontsize=8.5)
ax.set_ylabel("每核速率 (Mops/s)")
ax.set_ylim(0, 630)
ax.set_title("(c1) 贴近请求环：核 3 轮询 offset 0 + 核 4 连续写 offset d", fontsize=10)
ax.legend(loc="center right", fontsize=8.5, frameon=True)

# Panel (1, 1): Card TAD snoop rate & dual-core L2 refill rate (normalized by active window secs)
ax = axes[1, 1]
idle_snp = [r["tad"]["tad_snp_msh_out_any"]["per_s"] / 1e6 for r in by_key[("idle", 0)]]
idle_snp_mean = sum(idle_snp) / len(idle_snp)
ax.axhline(idle_snp_mean, color="#666666", lw=1.2, ls=":", label=f"全卡空闲背景嗅探 ({idle_snp_mean:.2f} M/s)")

s_wo = plot_series(ax, "write_only", lambda r: r["tad"]["tad_snp_msh_out_any"]["per_s"] / 1e6,
                   "TAD 嗅探：只写", "#d62728", "o", -0.08)
s_rmw = plot_series(ax, "rmw", lambda r: r["tad"]["tad_snp_msh_out_any"]["per_s"] / 1e6,
                    "TAD 嗅探：读改写", "#1f77b4", "s", -0.02)
s_pb = plot_series(ax, "poll_bystander", lambda r: r["tad"]["tad_snp_msh_out_any"]["per_s"] / 1e6,
                   "TAD 嗅探：轮询+写", "#2ca02c", "^", +0.04)
l2_wo = plot_series(
    ax,
    "write_only",
    lambda r: (r["core0_pmu"]["l2d_cache_refill"]["val"] + r["core1_pmu"]["l2d_cache_refill"]["val"]) / r["secs"] / 1e6,
    "双核 L2 重填和 (val/secs)：只写",
    "#9467bd",
    "D",
    +0.10,
    "--",
)
ax.annotate("0 B: TAD 嗅探 15.9–17.8 M/s\n双核 L2 重填 val/secs 15.6–17.6\n(按 PMU per_s 为 15.5–17.4 M/s)",
            xy=(0, s_wo[0]), xytext=(0.26, 12.0),
            fontsize=7.8, arrowprops=dict(arrowstyle="->", color="#555555", lw=0.8))
ax.annotate(f"64 B..4 KiB: TAD 嗅探 {s_wo[1]:.2f}–{s_rmw[1]:.2f} M/s（= 空闲背景）\n双核 L2 重填 < 0.01 M/s，tad_alloc < 0.004 M/s\n（驻留私有 L1/L2，核间 DTG 64 B 无伪共享）",
            xy=(1, s_wo[1]), xytext=(1.02, 4.8),
            fontsize=8.2, arrowprops=dict(arrowstyle="->", color="#555555", lw=0.8))
ax.set_xticks(x_idx, DIST_LABELS, fontsize=8.5)
ax.set_ylabel("事件速率 (M/s)")
ax.set_ylim(-0.8, 20.5)
ax.set_title("卡侧 TAD 嗅探 (tad_snp_msh_out_any) 与双核 L2 重填速率", fontsize=10)
ax.legend(loc="upper right", fontsize=7.8, frameon=True)

fig.suptitle("MRV-55：卡核 3、4 (1.97 GHz) 在 hugepage 内存中按不同距离操作 64 B 行（每条跑 1.0 s × 5 次，lost/bad/torn = 0）",
             fontsize=10.5)
print(save(fig, "mrv-55-throughput-snoops"))
