"""MRV-15 reproduction: closed-loop throughput vs window W for 1/2/4 card cores, SDP replies,
counted by OUT_CNTS (dashed) or rx_poll_mem=1 (solid). Data: the `repro` runs (group A) in
the MRV-15 runs.jsonl.

    uv run --with matplotlib python docs/figures/mrv-15-sdp-repro.py [runs.jsonl]
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
runs = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs/mrv15-2026-11-17/runs.jsonl"

def ncores(spec):
    """Card lcore list as given to EAL -l: "3", "3,4", "3-6", "3,9,15,21"."""
    n = 0
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        n += int(hi or lo) - int(lo) + 1
    return n


points = defaultdict(list)  # (card cores, mem) -> [(W, Mmsg/s)]
for line in runs.read_text().splitlines():
    r = json.loads(line)
    if r.get("group") != "A" or r["rc"] != 0:
        continue
    h = r["host"]
    assert h["lost"] == h["bad"] == h["torn"] == 0, r["id"]
    points[(ncores(r["card_cores"]), r["mem"])].append((r["window"], h["mmsg"]))

fig, ax = plt.subplots(figsize=(7, 4.2))
colors = {1: "#2563eb", 2: "#16a34a", 4: "#dc2626"}
for (cores, mem), pts in sorted(points.items()):
    by_w = defaultdict(list)
    for w, m in pts:
        by_w[w].append(m)
    ws = sorted(by_w)
    ax.plot(ws, [statistics.median(by_w[w]) for w in ws], marker="o",
            color=colors.get(cores, "gray"), linestyle="-" if mem else "--",
            label=f"卡 {cores} 核，{'rx_poll_mem=1' if mem else '读 OUT_CNTS'}")
ax.set_xscale("log", base=2)
ax.set_xlabel("窗口 W（同时在途的请求数）")
ax.set_ylabel("吞吐 (Mmsg/s)")
ax.set_title("MRV-15：SDP 回程闭环吞吐 vs W（主机 1 核 cpu40 3.9 GHz，64 B，批大小 1）", fontsize=10)
ax.legend(fontsize=8, ncol=2)
print(save(fig, "mrv-15-sdp-repro"))
