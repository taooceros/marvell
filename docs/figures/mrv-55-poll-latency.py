"""MRV-55: Poll-write new-value arrival latency distributions across distances 0 B..4 KiB.

Reads logs/mrv55-2026-11-18/runs.jsonl (modes `poll_bystander_lat` and `poll_pingpong`,
5 reps x 500,000 rounds per distance, lost/bad/torn = 0) and plots the empirical CDFs
and quantile / snoop summaries, highlighting both the 0 B -> 64 B true-sharing jump and
the intra-4 KiB page (64/128/256 B) vs cross-page (4 KiB) spatial prefetcher tail shift.

    uv run --with matplotlib python docs/figures/mrv-55-poll-latency.py
"""
import json
from collections import defaultdict
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv55-2026-11-18/runs.jsonl"

DISTS = [0, 64, 128, 256, 4096]
DIST_LABELS = ["0 B\n(同行)", "64 B\n(同128B块)", "128 B\n(同页邻块)", "256 B\n(同页)", "4 KiB\n(跨页)"]
COLORS = {
    0: "#d62728",
    64: "#1f77b4",
    128: "#2ca02c",
    256: "#ff7f0e",
    4096: "#555555",
}
STYLES = {
    0: ("-", 2.2),
    64: ("-", 2.0),
    128: ("--", 1.7),
    256: ("-.", 1.6),
    4096: (":", 2.2),
}

records = [json.loads(line) for line in RUNS.read_text().splitlines()]
by_key = defaultdict(list)
for r in records:
    assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
    by_key[(r["mode"], r["dist_bytes"])].append(r)

idle_snp_per_s = sum(r["tad"]["tad_snp_msh_out_any"]["per_s"] for r in by_key[("idle", 0)]) / 5

fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.8))

# Left panel: CDF of burst+notification latency (`burst_cdf_ns`) in `poll_bystander_lat`
ax = axes[0]
for d in DISTS:
    rs = by_key[("poll_bystander_lat", d)]
    assert len(rs) == 5
    probs = [pt[0] for pt in rs[0]["burst_cdf_ns"]]
    avg_xs = [sum(r["burst_cdf_ns"][i][1] for r in rs) / len(rs) for i in range(len(probs))]
    ls, lw = STYLES[d]
    tag = "同行" if d == 0 else ("同128B块" if d == 64 else ("同4KiB页" if d < 4096 else "跨4KiB页"))
    lbl = f"{d} B ({tag}): p50={avg_xs[50]:.0f}, p70={avg_xs[70]:.0f}, p99={avg_xs[99]:.0f} ns"
    ax.plot(avg_xs, probs, color=COLORS[d], ls=ls, lw=lw, label=lbl)

bbox_cfg = dict(boxstyle="round,pad=0.22", facecolor="white", alpha=0.88, edgecolor="none")
ax.annotate("跨 4 KiB 页 (无页内预取耦合):\np50=238 ns, p70=254 ns, p99=355 ns",
            xy=(253.8, 0.70), xytext=(156, 0.83),
            fontsize=8.0, color="#333333", bbox=bbox_cfg, arrowprops=dict(arrowstyle="->", color="#555555", lw=0.9))
ax.annotate("同 4 KiB 页三组 (64 / 128 / 256 B) 重合:\np50=238 ns，但 p70=272–275 ns、p99=396–405 ns\n(核 3 缺失触发页内预取 +0.06..0.08 次/轮)",
            xy=(288.0, 0.75), xytext=(246, 0.44),
            fontsize=8.0, color="#1f77b4", bbox=bbox_cfg, arrowprops=dict(arrowstyle="->", color="#1f77b4", lw=0.9))
ax.annotate("0 B (同 64 B 行真共享):\np50 = 323 ns (+85 ns)",
            xy=(323, 0.68), xytext=(342, 0.68),
            fontsize=8.2, color="#d62728", bbox=bbox_cfg, arrowprops=dict(arrowstyle="->", color="#d62728", lw=0.9))
ax.set_xlim(150, 460)
ax.set_ylim(0, 1.02)
ax.set_xlabel("从 8 次写 offset d + 写通知格到轮询核读到新值的延迟 (ns)")
ax.set_ylabel("累积概率 (CDF)")
ax.set_title("(c) 轮询核读到新值的延迟分布 CDF（5 × 50 万轮）", fontsize=10)
ax.legend(loc="lower right", fontsize=7.6, frameon=True)

# Right panel: p50 / p70 / p90 / p99 latency & TAD snoops per round vs distance
ax = axes[1]
x_idx = list(range(len(DISTS)))

for key_fn, lbl, color, marker, ls in [
    (lambda r: r["burst_lat_ns"]["p99"], "邻写+通知 p99 (ns)", "#9467bd", "^", ":"),
    (lambda r: r["burst_lat_ns"]["p90"], "邻写+通知 p90 (ns)", "#ff7f0e", "s", "--"),
    (lambda r: dict(r["burst_cdf_ns"])[0.70], "邻写+通知 p70 (ns)", "#2ca02c", "v", "-."),
    (lambda r: r["burst_lat_ns"]["p50"], "邻写+通知 p50 (ns)", "#d62728", "o", "-"),
    (lambda r: r["lat_ns"]["p50"], "纯末次通知 p50 (ns)", "#8c564b", "P", "--"),
]:
    means = []
    for i, d in enumerate(DISTS):
        rs = by_key[("poll_bystander_lat", d)]
        ys = [key_fn(r) for r in rs]
        ax.scatter([i - 0.04] * len(ys), ys, s=20, color=color, marker=marker, alpha=0.7, zorder=3)
        means.append(sum(ys) / len(ys))
    ax.plot(x_idx, means, color=color, lw=1.6, ls=ls, label=lbl, zorder=4)

pp_means = []
for i, d in enumerate(DISTS):
    rs = by_key[("poll_pingpong", d)]
    ys = [r["lat_ns"]["p50"] for r in rs]
    ax.scatter([i + 0.04] * len(ys), ys, s=20, color="#1f77b4", marker="D", alpha=0.7, zorder=3)
    pp_means.append(sum(ys) / len(ys))
ax.plot(x_idx, pp_means, color="#1f77b4", lw=1.6, ls="-", label="两行乒乓单向 p50 (ns)", zorder=4)

ax.text(0.03, 106,
        "每轮净 TAD 嗅探 (总嗅探) / 核 4 L2 重填：\n"
        "• 0 B (同行)：净 6.01 (总 6.47) / 核 4 L2 = 3.00 次/轮\n"
        "• 64/128/256 B (同页)：净 4.12–4.17 (总 4.53–4.58) / 核 4 L2 = 2.06–2.08\n"
        "• 4 KiB (跨页)：净 4.01 (总 4.42) / 核 4 L2 = 2.00 次/轮",
        fontsize=7.6, bbox=dict(boxstyle="round,pad=0.3", facecolor="#f8f8f8", edgecolor="#cccccc"))

ax.set_xticks(x_idx, DIST_LABELS, fontsize=8.5)
ax.set_ylabel("单向读到新值延迟 (ns)")
ax.set_ylim(100, 460)
ax.set_title("各距离下的分位延迟与同页预取尾延迟台阶（每点 50 万轮）", fontsize=10)
ax.legend(loc="upper right", fontsize=7.6, frameon=True, ncol=2)

fig.suptitle("MRV-55：卡核 3 轮询 offset 0、卡核 4 写 offset d 时的单向可见延迟分布（cntvct 1.0 GHz，lost/bad/torn = 0）",
             fontsize=10.5)
print(save(fig, "mrv-55-poll-latency"))
