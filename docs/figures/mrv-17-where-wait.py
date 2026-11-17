"""MRV-17: where the W messages of the window are, at W=128 (13 M/s regime) and W=1024 (fast).
Every 50k replies the host (inline_pingpong --sample-card-next 50000, MRV-17 experiments change)
noted how many requests it had sent and replies it had taken, read the card core's progress word
through BAR4 (the card updates it after it has issued a batch's reply stores), and read the head
word of every reply cell it had not taken yet. Card: /root/bench/inline_reflector (2026-11-13),
1 core, write-combining replies. Bars: median over the samples of both runs (first quarter of
each run skipped). The probe itself shifts throughput a little (W=128: 13.6 / 14.6 vs 13.1 / 13.1
Mmsg/s without it), so the split is approximate.

    uv run --with matplotlib python docs/figures/mrv-17-where-wait.py [runs.jsonl]
"""
import json
import re
import statistics
import sys
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
runs = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs/mrv17-2026-11-17/runs.jsonl"
outdir = runs.parent / "out"

parts = {}  # W -> dict of component -> [values]
rates = {}
for line in runs.read_text().splitlines():
    r = json.loads(line)
    m = re.match(r"LANDED wc n1 W(\d+) probe rep\d$", r["tag"])
    if not m or int(m.group(1)) not in (128, 1024) or r["rc"] != 0:
        continue
    h = r["host"][0]
    assert h["lost"] == h["bad"] == h["torn"] == 0, r["tag"]
    w = int(m.group(1))
    out = (outdir / Path(r["out_path"]).name).read_text()
    raw = re.search(r"card-next raw \(sent,seen,acked,found,landed,next_landed,us\): (.*)", out).group(1)
    samples = [tuple(int(v) for v in s.split(",")) for s in raw.split()]
    d = parts.setdefault(w, {"behind": [], "issued": [], "landed": [], "inflight": []})
    for sent, seen, acked, _found, landed, _nl, _us in samples:
        d["behind"].append(sent - acked)
        d["issued"].append(max(acked - seen - landed, 0))
        d["landed"].append(landed)
        d["inflight"].append(sent - seen)
    rates.setdefault(w, []).append(h["mmsg"])

fig, ax = plt.subplots(figsize=(7.6, 4.9))
labels, x = [], []
comp = [("behind", "主机已发、卡还没发出回复（发出数 − 卡的进度字）", "#2563eb"),
        ("issued", "卡已发出回复的写、主机内存里还没有（相减得到，近似）", "#dc2626"),
        ("landed", "回复头已在主机内存、主机还没取（扫回复环）", "#16a34a")]
for i, w in enumerate(sorted(parts)):
    bottom = 0
    for key, name, color in comp:
        v = statistics.median(parts[w][key])
        ax.bar(i, v, bottom=bottom, color=color, width=0.55, label=name if i == 0 else None)
        if v >= 8:
            ax.text(i, bottom + v / 2, f"{v:.0f}", ha="center", va="center", color="white", fontsize=9)
        bottom += v
    med = {k: statistics.median(parts[w][k]) for k, _, _ in comp}
    ax.text(i, bottom + 15, f"在途中位 {statistics.median(parts[w]['inflight']):.0f}（卡落后 {med['behind']:.0f}，"
            f"未到 {med['issued']:.0f}，已到未取 {med['landed']:.0f}）", ha="center", fontsize=8)
    labels.append(f"W={w}\n{' / '.join(f'{m:.1f}' for m in rates[w])} Mmsg/s")
    x.append(i)
ax.set_xticks(x, labels=labels)
ax.set_ylabel("消息条数（各次采样的中位数）")
ax.set_ylim(0, 1150)
ax.set_title("窗口里的消息停在哪：慢状态（W=128）vs 快状态（W=1024）\n（写合并，卡 1 核，主机核 40，64 B，批大小 1；lost/bad/torn 全 0）",
             fontsize=9)
ax.legend(fontsize=8, loc="upper left")
fig.text(0.01, 0.01, "注：三个读数不是同一时刻读的（先扫回复环，再经 BAR4 读卡的进度字），红色一段是相减得到的；\n"
         "加了采样后 W=128 的吞吐也变了一点（13.6 / 14.6 对不采样的 13.1 / 13.1），所以只是近似的位置。", fontsize=7, color="#4b5563")
fig.tight_layout(rect=(0, 0.07, 1, 1))
fig.tight_layout = lambda **kw: None  # keep the room for the note
print(save(fig, "mrv-17-where-wait"))
