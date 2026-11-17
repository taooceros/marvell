"""MRV-24 review answer: what the card -> host stream (c2h) does while MOVDIR64B (h2c) is still
being sent, and how many MOVDIR64B wait in the host CHAs.

`inline_stream --dir bidir -n N` sends N cells each way and stops each direction on its own,
so the faster one ends first and the printed rate of the slower one is a whole-run average.
(a) c2h alone, c2h over the whole bidir run (both printed by the program), and c2h while h2c
    was still running: IIO inbound line writes in the counter window that covers the h2c
    phase, divided by its length (MOVDIR64B count in the window / printed h2c rate). For the
    fetch control the h2c phase is cut from the per-100 ms counter file (the fetch core keeps
    reading after it is done, so the window there spans the whole run). The plain-store reply
    mode is left out: it lands as ~5 partial writes per cell, so the counter is not cells.
(b) average number of MOVDIR64B in the CHA TORs (WCiLF occupancy / window cycles at the
    pinned 2.2 GHz uncore clock; the CHA events were multiplexed, enabled 40-48% of the time).

Data: logs/mrv24-2026-11-17/{runs.jsonl, card.jsonl, perf/R22-uncore-per-socket.csv}.

    uv run --with matplotlib python docs/figures/mrv-24-overlap.py
"""
import json
import re
import statistics
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs/mrv24-2026-11-17"
UNCORE_HZ = 2.2e9
RUNS = [json.loads(l) for l in (LOGS / "runs.jsonl").read_text().splitlines()]
CARD = {run: c for c in map(json.loads, (LOGS / "card.jsonl").read_text().splitlines()) for run in c["runs"]}


def run(i):
    """Run R<i>, checked like every plotted run: host bad/torn 0, card instance clean."""
    r, card = RUNS[i], CARD[f"R{i:02d}"]
    text = " ".join(card["exit_summary"])
    assert (card["counts_match"] and "partial cells seen 0" in text and "corrupt payloads 0" in text
            and re.search(r"failed 0\)", text)), i
    if m := re.search(r"bad (\d+) torn (\d+)", r["out"][0]):
        assert m.groups() == ("0", "0"), i
    return r


def printed(i, what):
    line = run(i)["out"][0]
    pat = {"h2c": r"card took all at ([\d.]+)", "c2h alone": r"host took them at ([\d.]+)",
           "c2h bidir": r"\| c2h .* at ([\d.]+) M cells/s"}[what]
    return float(re.search(pat, line).group(1))


def window(i):
    """Seconds of the counter window: its MOVDIR64B count / the printed h2c rate."""
    return run(i)["tot"]["iio_mmio_wr_txn"] / (printed(i, "h2c") * 1e6)


def c2h_overlap(i):
    return run(i)["tot"]["iio_inb_wr_txn"] / window(i) / 1e6


def c2h_overlap_fetch(i=22):
    """Per-interval inbound line writes of the fetch bidir run, over its h2c phase."""
    rows = {}
    for line in (LOGS / "perf/R22-uncore-per-socket.csv").read_text().splitlines():
        f = line.split(";")
        if len(f) > 6 and f[1] == "S1" and f[5] == "uncore_iio/event=0x84,ch_mask=0xff,fc_mask=7,umask=1/":
            rows[float(f[0])] = float(f[3])
    ts = sorted(rows)
    start = next(t for t in ts if rows[t] > 0)  # first interval with traffic (partly)
    phase = 2e8 / (printed(i, "h2c") * 1e6)
    rates = [rows[t] / (t - p) / 1e6 for p, t in zip(ts, ts[1:]) if start < t <= start + phase]
    return statistics.median(rates)


GROUPS = [  # name, c2h alone runs, bidir runs, overlap values
    ("写合并\n卡 1 收 + 1 写", [2], [3], [c2h_overlap(3)]),
    ("DPI 写\n卡 1 收 + 1 写", [14], [15], [c2h_overlap(15)]),
    ("写合并\n卡 2 收 + 2 写", [6, 10, 36, 37, 42, 43], [7, 9], [c2h_overlap(7), c2h_overlap(9)]),
    ("去程 fetch + 写合并回程\n卡 1 fetch + 1 写", [21], [22], [c2h_overlap_fetch()]),
]
CATS = [("回程单独（程序打印）", "#2563eb"), ("双向整次平均（程序打印）", "#94a3b8"),
        ("双向、去程还在发的那段（IIO 入站写计数）", "#dc2626")]

fig, (ax, bx) = plt.subplots(1, 2, figsize=(14, 5.4), gridspec_kw={"width_ratios": [1.25, 1]})
width = 0.26
for g, (name, alone, bidir, overlap) in enumerate(GROUPS):
    series = [[printed(i, "c2h alone") for i in alone], [printed(i, "c2h bidir") for i in bidir], overlap]
    for c, ((label, color), vals) in enumerate(zip(CATS, series)):
        x = g + (c - 1) * width
        med = statistics.median(vals)
        ax.bar(x, med, width * 0.92, color=color, alpha=0.4, label=label if g == 0 else None)
        ax.scatter([x] * len(vals), vals, s=12, color=color, zorder=3)
        ax.text(x, med + 1.5, f"{med:.1f}", ha="center", va="bottom", fontsize=8)
ax.set_xticks(range(len(GROUPS)), [g[0] for g in GROUPS], fontsize=8)
ax.set_ylabel("回程（M 格/s，64 B/格）")
ax.secondary_yaxis("right", functions=(lambda v: v * 0.064, lambda v: v / 0.064)).set_ylabel("GB/s")
ax.set_ylim(0, 135)
ax.set_title("(a) 回程：单独、双向整次平均、去程还在发时（每边 2 亿格，发送核 40/104 固定 3.9 GHz）", fontsize=9.5)
ax.legend(fontsize=8, loc="upper left")

POINTS = [  # label, runs, colour
    ("1+1 单独", [1, 13, 17], "#2563eb"), ("1+1 双向", [3, 15], "#16a34a"),
    ("1+1 双向，发送核 41（平均 3.69 GHz）", [4], "#16a34a"), ("1+1 普通直写回程，双向", [19], "#ea580c"),
    ("1+1 DPI 回程，双向，发送核 47 只有 2.12 GHz", [16], "#dc2626"),
    ("2+2 单独", [5, 8], "#2563eb"), ("2+2 双向", [7, 9], "#16a34a")]
for k, (label, ids, color) in enumerate(POINTS):
    stats = []
    for i in ids:
        t = run(i)["tot"]
        entries = t["cha_occ_wcilf"] / (UNCORE_HZ * window(i))
        ns = t["cha_occ_wcilf"] / t["cha_ins_wcilf"] / UNCORE_HZ * 1e9
        bx.scatter(k, entries, s=30, color=color, zorder=3)
        stats.append((entries, ns))
    span = lambda v, fmt: fmt.format(min(v)) if max(v) - min(v) < 1 else f"{fmt.format(min(v))}–{fmt.format(max(v))}"
    runs_txt = " ".join(f"R{i:02d}" for i in ids)
    bx.annotate(f"{span([e for e, _ in stats], '{:.0f}')} 条\n{span([n for _, n in stats], '{:.0f}')} ns\n{runs_txt}",
                (k, statistics.median(e for e, _ in stats)), textcoords="offset points", xytext=(7, -10), fontsize=7)
bx.set_yscale("log")
bx.set_ylim(5, 1500)
bx.set_xlim(-0.5, len(POINTS) - 0.2)
bx.set_xticks(range(len(POINTS)), [p[0] for p in POINTS], fontsize=7, rotation=30, ha="right")
bx.set_ylabel("MOVDIR64B 在 CHA 里平均几条（推算，对数）")
bx.set_title("(b) 去程在主机 CHA 里平均几条：分时计数推算（占用 ÷ 时间），含义未确认；旁注每条平均停留",
             fontsize=9.5)
fig.suptitle("MRV-24：双向时两个方向各跑 2 亿格，快的一边先停；回程要按「去程还在发」的那段看", fontsize=10.5)
print(save(fig, "mrv-24-overlap"))
