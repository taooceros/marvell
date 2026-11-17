"""MRV-17: who pauses matters. After a poll that found nothing, either the card's ring core waits
G ns before polling again (card program --poll-gap-ns G), or the host waits G ns before polling
the reply ring again (inline_pingpong --poll-gap-ns G). Write-combining replies, one fresh
reflector per run. The card program here is the diagnostic build with the empty-poll counters
(/root/bench/inline_reflector-mrv17, MRV-17 experiments change); the labels give how many request
cells the card core took per poll that found any (its own counter).

    uv run --with matplotlib python docs/figures/mrv-17-card-pause.py [runs.jsonl]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
runs = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs/mrv17-2026-11-17/runs.jsonl"

n1 = defaultdict(dict)  # (who, W) -> {gap ns: (Mmsg/s, cells per busy poll)}
n4 = defaultdict(dict)  # W -> {gap ns: (Mmsg/s, cells per busy poll)}


def gap_of(cmd):
    m = re.search(r"--poll-gap-ns (\d+)", cmd)
    return int(m.group(1)) if m else 0


for line in runs.read_text().splitlines():
    r = json.loads(line)
    t = r["tag"]
    if not re.match(r"X3b? wc n[14] (host|card)gap\d+$", t) or r.get("rc", 0) != 0:
        continue
    h = r["host"][0]
    assert h["lost"] == h["bad"] == h["torn"] == 0, t
    assert "inline_reflector-mrv17 " in r["card_cmd"], r["card_cmd"]
    w = int(h["window"])
    cores = r["card"]["cores"]
    per_busy = sum(c["per_busy"] for c in cores) / len(cores)
    host_gap, card_gap = gap_of(r["host_cmd"]), gap_of(r["card_cmd"])
    if t.startswith("X3b"):
        assert host_gap == 0
        n4[w][card_gap] = (h["mmsg"], per_busy)
        continue
    if host_gap == 0 and card_gap == 0:
        n1[("card", w)][0] = n1[("host", w)][0] = (h["mmsg"], per_busy)
    elif card_gap:
        n1[("card", w)][card_gap] = (h["mmsg"], per_busy)
    else:
        n1[("host", w)][host_gap] = (h["mmsg"], per_busy)

fig, (ax, bx) = plt.subplots(1, 2, figsize=(10.5, 4.4), sharey=True)
style = {("card", 64): ("#dc2626", "-", "卡核停，W=64"), ("card", 256): ("#dc2626", "--", "卡核停，W=256"),
         ("host", 64): ("#2563eb", "-", "主机停，W=64"), ("host", 256): ("#2563eb", "--", "主机停，W=256")}
for key, (color, ls, label) in style.items():
    gaps = sorted(n1[key])
    ax.plot(gaps, [n1[key][g][0] for g in gaps], marker="o", ms=4, color=color, linestyle=ls, label=label)
    if key[0] == "card":
        dy = 6 if key[1] == 256 else -12
        for g in gaps:
            ax.annotate(f"{n1[key][g][1]:.0f} 格", (g, n1[key][g][0]), textcoords="offset points", xytext=(4, dy),
                        fontsize=7, color=color)
ax.set_xlabel("空轮询之后停多久再轮询 (ns)")
ax.set_ylabel("吞吐 (Mmsg/s)")
ax.set_title("卡 1 核：停的是卡核还是主机核\n（写合并，主机核 40，64 B，批大小 1；标注 = 卡核每次取到的格数）", fontsize=9)
ax.legend(fontsize=8, loc="upper left")
for w, color in ((64, "#f59e0b"), (256, "#dc2626"), (1024, "#7c3aed")):
    gaps = sorted(n4[w])
    bx.plot(gaps, [n4[w][g][0] for g in gaps], marker="o", ms=4, color=color, label=f"卡核停，W={w}")
    dy = -12 if w == 64 else 6
    for g in gaps:
        bx.annotate(f"{n4[w][g][1]:.0f} 格", (g, n4[w][g][0]), textcoords="offset points", xytext=(4, dy),
                    fontsize=7, color=color)
bx.set_xlabel("卡核空轮询之后停多久 (ns)")
bx.set_title("卡 4 核：卡核停 0 / 1 / 3 µs\n（写合并，主机核 40；lost/bad/torn 全 0）", fontsize=9)
bx.legend(fontsize=8, loc="upper left")
print(save(fig, "mrv-17-card-pause"))
