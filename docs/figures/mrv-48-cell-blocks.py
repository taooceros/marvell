"""MRV-48: closed-loop throughput, request cells owned by 128 B block vs by sequence.

Reads logs/mrv48-2026-11-17/runs.jsonl (runs tagged `M48b`/`M48c`: card build with O(1)
stepping, sha256 in each record) and plots every run as a dot, grouped by card cores and W.
`M48 ...` runs (first build, whose --cell-blocks step searched with `% 65533` per message)
are not plotted.

    uv run --with matplotlib python docs/figures/mrv-48-cell-blocks.py
"""
import json
import re
from collections import defaultdict
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv48-2026-11-17/runs.jsonl"
SHA = "4c6c508e560b18772cd97cb66e45b13b7fb5ff29fdc436addc1819460e50a26b"
TAG = re.compile(r"^M48[bc] wc n(?P<n>\d) W(?P<w>\d+) (?P<v>\S+) pass(?P<p>\d+)$")
FLAGS = {"base": "", "pairs": "--seq-pairs", "blocks": "--cell-blocks", "blocks+priv": "--cell-blocks --private-progress"}
LABEL = {"base": "按序号\n轮流", "pairs": "按序号\n两格一组", "blocks": "按 128 B 块\n两格一组", "blocks+priv": "按 128 B 块\n+ 不写进度字"}
ORDER = list(FLAGS)

runs = defaultdict(list)
for line in RUNS.read_text().splitlines():
    r = json.loads(line)
    m = TAG.match(r.get("tag", ""))
    if not m:
        continue
    n, w, v = int(m["n"]), int(m["w"]), m["v"]
    h = r["host"][0]
    assert r["rc"] == 0 and h["lost"] == h["bad"] == h["torn"] == 0, r["tag"]
    assert r.get("card_sha256") == SHA, r["tag"]
    card = r["card_cmd"]
    assert "--store --store-wc" in card and f"-l {'3-6' if n == 4 else '3'} " in card, r["tag"]
    for k, f in FLAGS.items():
        if f:
            assert (f in card) == (k == v or (k == "blocks" and v == "blocks+priv")), r["tag"]
    assert f"-w {w} " in r["host_cmd"] and "-l 40 " in r["host_cmd"], r["tag"]
    # Read right after the host exits: governor and uncore hold, cpu40's current clock may already idle.
    assert r["clocks"]["cpu40_gov"] == "performance" and r["clocks"]["uncore1_khz"] == "2200000", r["tag"]
    runs[(n, w)].append((v, h["mmsg"]))

groups = sorted(runs, key=lambda k: (-k[0], k[1]))
fig, axes = plt.subplots(1, len(groups), figsize=(3.3 * len(groups), 4.2), squeeze=False)
for ax, (n, w) in zip(axes[0], groups):
    present = [v for v in ORDER if any(x == v for x, _ in runs[(n, w)])]
    for i, v in enumerate(present):
        ys = [y for x, y in runs[(n, w)] if x == v]
        color = "#888888" if v == "base" else "#d62728"
        ax.scatter([i] * len(ys), ys, s=28, color=color, alpha=0.75, zorder=3)
        ax.hlines(sum(ys) / len(ys), i - 0.25, i + 0.25, color=color, lw=2, zorder=4)
    base = [y for x, y in runs[(n, w)] if x == "base"]
    ax.axhline(sum(base) / len(base), color="#888888", lw=0.8, ls="--", zorder=1)
    ax.set_xticks(range(len(present)), [LABEL[v] for v in present], fontsize=8)
    ax.set_xlim(-0.5, len(present) - 0.5)
    ax.set_title(f"卡 {n} 核，W={w}", fontsize=10)
    ys = [y for _, y in runs[(n, w)]]
    lo, hi = min(ys), max(ys)
    pad = max(0.3, (hi - lo) * 0.15)
    ax.set_ylim(lo - pad, hi + pad)
axes[0][0].set_ylabel("闭环吞吐 (Mmsg/s)")
fig.suptitle("请求格怎么分给卡核：写合并回程，64 B，主机核 40（performance 调速器），uncore 2.2 GHz，批大小 1，并发 = W；"
             "每点一次运行，横线 = 平均", fontsize=9)
print(save(fig, "mrv-48-cell-blocks"))
