"""MRV-17, the user's question "W=1024 used to be 24, why 50 now": the two numbers are different
setups, not one setup that changed.
Left: write-combining replies, throughput vs W with 1 vs 4 card cores, same host program and card
program (`OFFICIAL` runs), plus the same two W=1024 points on the leased build (`LEASE H1`).
Right: W=1024, 1 card core: host programs built from main before MRV-17, from the MRV-17 change,
and from main with only MRV-17's `RingReplies` (the bisection), each run a fresh reflector.

    uv run --with matplotlib python docs/figures/mrv-17-w1024-cores-build.py [runs.jsonl]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
runs = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs/mrv17-2026-11-17/runs.jsonl"

sweep = defaultdict(dict)  # card cores -> {W: Mmsg/s}
lease = {}  # card cores -> Mmsg/s at W=1024
builds = defaultdict(list)  # host build -> [Mmsg/s] at W=1024, 1 card core
BUILD = {
    "mainline": "main 上 MRV-17 之前的主机程序",
    "B2_ringreplies": "main + 只换 MRV-17 的 RingReplies",
    "B_inline_rs": "main + MRV-17 的 inline.rs",
    "A_closed_loop": "main + MRV-17 的 closed_loop.rs",
    "mrv17": "MRV-17 的主机程序",
    "mrv17side": "MRV-17 的主机程序",
    "mrv17main": "MRV-17 的主机程序",
}


def ok(h):
    return h["lost"] == h["bad"] == h["torn"] == 0


for line in runs.read_text().splitlines():
    r = json.loads(line)
    t = r["tag"]
    if r.get("rc", 0) != 0 or not r.get("host"):
        continue
    h = r["host"][0]
    if m := re.match(r"OFFICIAL wc n(\d) H1$", t):
        assert ok(h), t
        sweep[int(m.group(1))][int(h["window"])] = h["mmsg"]
    elif m := re.match(r"LEASE H1 wc n(\d) W1024$", t):
        assert ok(h), t
        lease[int(m.group(1))] = h["mmsg"]
    elif m := re.match(r"(?:XhostAB2|XhostAB3|Xbisect|Xbisect2) wc n1 W1024 (\S+)$|Xdiv (\S+)$", t):
        assert ok(h) and int(h["window"]) == 1024 and " -l 3 " in r["card_cmd"], t
        builds[BUILD[m.group(1) or m.group(2)]].append(h["mmsg"])

fig, (ax, bx) = plt.subplots(1, 2, figsize=(10.5, 4.4), gridspec_kw={"width_ratios": [1.25, 1]})
for cores, color in ((1, "#dc2626"), (4, "#2563eb")):
    ws = sorted(sweep[cores])
    ax.plot(ws, [sweep[cores][w] for w in ws], marker="o", ms=4, color=color, label=f"卡 {cores} 核（11-13 卡程序）")
    if cores in lease:
        ax.plot([1024], [lease[cores]], marker="*", ms=12, color=color, linestyle="none",
                label=f"卡 {cores} 核，W=1024 租约版重测")
ax.set_xscale("log", base=2)
ax.set_xticks([1, 8, 16, 32, 64, 128, 256, 512, 1024], labels=["1", "8", "16", "32", "64", "128", "256", "512", "1024"])
ax.set_xlabel("窗口 W（批大小 1）")
ax.set_ylabel("吞吐 (Mmsg/s)")
ax.set_title("写合并回程：卡 1 核 vs 卡 4 核\n（主机核 40，同一个主机程序，64 B；lost/bad/torn 全 0）", fontsize=9)
ax.legend(fontsize=8, loc="upper left")

order = ["main 上 MRV-17 之前的主机程序", "main + MRV-17 的 closed_loop.rs", "main + MRV-17 的 inline.rs",
         "main + 只换 MRV-17 的 RingReplies", "MRV-17 的主机程序"]
for i, name in enumerate(order):
    ys = builds[name]
    bx.scatter([i] * len(ys), ys, s=22, color="#dc2626" if "之前" in name or "closed_loop" in name else "#16a34a",
               alpha=0.8, zorder=3)
    bx.text(i, max(ys) + 1.5, f"{len(ys)} 次", ha="center", fontsize=7)
bx.axhline(sweep[4][1024], color="#2563eb", linestyle="--", lw=1)
bx.text(len(order) - 0.6, sweep[4][1024] + 1, "卡 4 核 W=1024", color="#2563eb", fontsize=7, ha="right")
short = {"main 上 MRV-17 之前的主机程序": "MRV-17 之前\n（main）", "main + MRV-17 的 closed_loop.rs": "main +\nclosed_loop.rs",
         "main + MRV-17 的 inline.rs": "main +\ninline.rs", "main + 只换 MRV-17 的 RingReplies": "main +\nRingReplies",
         "MRV-17 的主机程序": "MRV-17"}
bx.set_xticks(range(len(order)), labels=[short[o] for o in order], fontsize=8)
bx.set_xlabel("主机程序（「main +」= main 上只换进 MRV-17 的这一个文件 / 函数）", fontsize=8)
bx.set_ylim(0, 60)
bx.set_ylabel("吞吐 (Mmsg/s)")
bx.set_title("W=1024、卡 1 核：主机程序不同版本\n（卡上同一个未加统计的程序，每次重启）", fontsize=9)
print(save(fig, "mrv-17-w1024-cores-build"))
