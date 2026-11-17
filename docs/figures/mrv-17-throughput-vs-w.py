"""MRV-17: single-host-core closed loop, throughput vs window W for each reply path (the
`OFFICIAL` runs: card /root/bench/inline_reflector of 2026-11-13, one fresh reflector per run;
host inline_pingpong built from the MRV-17 change on cpu40 3.9 GHz, uncore 2.2 GHz; 64 B, batch 1).

    uv run --with matplotlib python docs/figures/mrv-17-throughput-vs-w.py [runs.jsonl]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
runs = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs/mrv17-2026-11-17/runs.jsonl"

LABEL = {
    "wc n1": "写合并，卡 1 核",
    "wc n4": "写合并，卡 4 核",
    "dpi n4": "DPI，卡 4 核",
    "store n4": "直写，卡 4 核",
    "sdp mempoll n4": "SDP + rx_poll_mem，卡 4 核",
    "sdp outcnts n4": "SDP 读 OUT_CNTS，卡 4 核",
}
STYLE = {
    "wc n1": ("#dc2626", "-"),
    "wc n4": ("#dc2626", "--"),
    "dpi n4": ("#2563eb", "-"),
    "store n4": ("#9333ea", "-"),
    "sdp mempoll n4": ("#16a34a", "-"),
    "sdp outcnts n4": ("#16a34a", "--"),
}

points = defaultdict(dict)  # path -> {W: Mmsg/s}
for line in runs.read_text().splitlines():
    r = json.loads(line)
    m = re.match(r"OFFICIAL (.+) H1$", r["tag"])
    if not m or r.get("rc", 0) != 0:
        continue
    h = r["host"][0]
    assert h["lost"] == h["bad"] == h["torn"] == 0, r["tag"]
    assert r["card_cmd"].startswith("/root/bench/inline_reflector "), r["card_cmd"]
    points[m.group(1)][int(h["window"])] = h["mmsg"]

fig, ax = plt.subplots(figsize=(7.6, 4.6))
for path in LABEL:
    ws = sorted(points[path])
    color, ls = STYLE[path]
    ax.plot(ws, [points[path][w] for w in ws], marker="o", ms=4, color=color, linestyle=ls, label=LABEL[path])
ax.axhspan(12, 14.5, color="#f59e0b", alpha=0.12, lw=0)
ax.text(1.1, 13.3, "13 M/s 平台", fontsize=8, color="#b45309", va="center")
ax.set_xscale("log", base=2)
ax.set_xticks([1, 8, 16, 32, 64, 128, 256, 512, 1024], labels=["1", "8", "16", "32", "64", "128", "256", "512", "1024"])
ax.set_xlabel("窗口 W（同时在途的请求数，批大小 1）")
ax.set_ylabel("吞吐 (Mmsg/s，×0.064 = GB/s 单方向)")
ax.set_title("单个主机核闭环吞吐 vs W（主机核 40 @3.9 GHz，uncore 2.2 GHz，64 B；每点一次运行，lost/bad/torn 全 0）",
             fontsize=9)
ax.legend(fontsize=8, ncol=2, loc="upper left")
print(save(fig, "mrv-17-throughput-vs-w"))
