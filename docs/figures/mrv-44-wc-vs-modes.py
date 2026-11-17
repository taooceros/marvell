"""MRV-44: does write-combining raise the closed loop? Throughput vs window W for each reply path,
card 1 core and card 4 cores side by side, from the same batch of runs (the `OFFICIAL` runs of
MRV-17: card /root/bench/inline_reflector of 2026-11-13, one fresh reflector per run; host
inline_pingpong of the MRV-17 change from the main checkout, cpu40 3.9 GHz, uncore 2.2 GHz; 64 B,
batch 1; one run per point). Only write-combining was run with 1 card core in that batch; its
hollow points are the `ALT` runs (same card program and arguments, the same host change rebuilt
at 01:07, W=128/256/512 in alternating order, 4 passes).

    uv run --with matplotlib python docs/figures/mrv-44-wc-vs-modes.py [runs.jsonl]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
runs = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs/mrv17-2026-11-17/runs.jsonl"

CARD = "/root/bench/inline_reflector"
HOST = "/home/hongtao/dev/marvell/testbed/target/release/inline_pingpong"
# reply path -> (label, colour, line style); same colours as mrv-17-throughput-vs-w.py
PATHS = {
    "wc": ("写合并（每条 1 次 64 B 写）", "#dc2626", "-"),
    "store": ("直写（每条 4 次 16 B 写）", "#9333ea", "-"),
    "dpi": ("DPI（卡上 DMA 引擎写）", "#2563eb", "-"),
    "sdp mempoll": ("SDP，主机轮询内存", "#16a34a", "-"),
    "sdp outcnts": ("SDP，主机读 OUT_CNTS", "#16a34a", "--"),
}
CARD_ARGS = {"wc": "--store --store-wc", "store": "--store", "dpi": "--dma ", "sdp mempoll": "", "sdp outcnts": ""}


def ok(r):
    h = r["host"][0]
    assert r.get("rc", 0) == 0 and h["lost"] == h["bad"] == h["torn"] == 0, r["tag"]
    assert r["card_cmd"].split()[0] == CARD and r["host_cmd"].split()[0] == HOST, r["tag"]
    return h


sweep = defaultdict(dict)  # (path, card cores) -> {W: Mmsg/s}
alt = []  # (W, Mmsg/s), write-combining, card 1 core
for line in runs.read_text().splitlines():
    r = json.loads(line)
    if m := re.match(r"OFFICIAL (wc|store|dpi|sdp mempoll|sdp outcnts) n([14]) H1$", r["tag"]):
        h = ok(r)
        path, cores = m.group(1), int(m.group(2))
        args = r["card_cmd"].split(" -- ")[1].strip() if " -- " in r["card_cmd"] else ""
        assert args.startswith(CARD_ARGS[path]) and (path != "store" or "--store-wc" not in args), (r["tag"], args)
        assert ("rx_poll_mem=1" in r["host_cmd"]) == (path == "sdp mempoll"), r["tag"]
        assert f" -l {'3' if cores == 1 else '3-6'} " in r["card_cmd"], r["tag"]
        sweep[(path, cores)][int(h["window"])] = h["mmsg"]
    elif re.match(r"ALT wc n1 W\d+ pass\d$", r["tag"]):
        assert r["card_cmd"].split()[0] == CARD and r["card_cmd"].endswith("-- --store --store-wc") and " -l 3 " in r["card_cmd"]
        h = r["host"][0]
        assert r.get("rc", 0) == 0 and h["lost"] == h["bad"] == h["torn"] == 0, r["tag"]
        alt.append((int(h["window"]), h["mmsg"]))
assert len(alt) == 12 and all(len(v) == 9 for v in sweep.values()) and len(sweep) == 6, (len(alt), sweep.keys())

fig, (one, four) = plt.subplots(1, 2, figsize=(11.5, 4.7), sharey=True)
for ax, cores in ((one, 1), (four, 4)):
    ax.axhspan(12, 14.5, color="#f59e0b", alpha=0.12, lw=0)
    ax.text(1.1, 15.2, "13 M/s 平台", fontsize=8, color="#b45309")
    for path, (label, color, ls) in PATHS.items():
        if (path, cores) not in sweep:
            continue
        pts = sweep[(path, cores)]
        ws = sorted(pts)
        ax.plot(ws, [pts[w] for w in ws], marker="o", ms=4, color=color, linestyle=ls, label=label)
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 8, 16, 32, 64, 128, 256, 512, 1024], labels=["1", "8", "16", "32", "64", "128", "256", "512", "1024"])
    ax.set_xlabel("窗口 W（同时在途的请求数）")
    ax.set_ylim(0, 56)
one.scatter([w for w, _ in alt], [v for _, v in alt], s=26, facecolors="none", edgecolors="#dc2626", zorder=3,
            label="写合并，另外交替跑的 4 轮")
one.set_title("卡 1 核（这批只测了写合并）", fontsize=10)
four.set_title("卡 4 核", fontsize=10)
one.set_ylabel("吞吐 (Mmsg/s)")
one.legend(fontsize=8, loc="upper left")
four.legend(fontsize=8, loc="upper left")
fig.suptitle("回复怎么写 vs 闭环吞吐：同一批运行，主机 1 核（核 40 @3.9 GHz，uncore 2.2 GHz），64 B，批大小 1；"
             "实心点每点一次运行，lost/bad/torn 全 0", fontsize=9.5)
print(save(fig, "mrv-44-wc-vs-modes"))
