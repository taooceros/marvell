"""MRV-24 summary: does traffic in one direction slow the other on the direct paths?

(a) Host -> card MOVDIR64B rate alone vs with card -> host replies running at the same time
    (`inline_stream --dir bidir`), per reply mode, with the sending core on cpu40/104
    (performance governor, 3.9 GHz) or on a schedutil core measured at 2.07-2.12 GHz.
(b) Card -> host reply rate alone, while MOVDIR64B was still sending, and after it had
    finished. `bidir` sends N cells each way and stops each direction on its own; while
    MOVDIR64B is sending = IIO inbound line writes in the counter window that covers it,
    divided by that window (MOVDIR64B count in it / the printed MOVDIR64B rate); after =
    the rest of the N reply cells over the rest of the reply time (printed rates).

Data: logs/mrv24-2026-11-17/{runs.jsonl, shell-runs.jsonl, card.jsonl}. Every plotted run is
verified: host bad 0 / torn 0 where it checks replies, and the card instance that served it
exited with partial 0, corrupt 0, DMA failed 0 and counts equal to its runs' cells.

    uv run --with matplotlib python docs/figures/mrv-24-summary.py
"""
import json
import re
import statistics
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs/mrv24-2026-11-17"
FIXED = {40, 104}  # performance governor; every other core ran schedutil
SLOW_GHZ = 2.5     # schedutil sender measured below this (2.07-2.12 GHz in these runs)


def jsonl(name):
    return [json.loads(l) for l in (LOGS / name).read_text().splitlines()]


HARNESS = jsonl("runs.jsonl")
CARDS = jsonl("card.jsonl")
CARD_OF = {run: c for c in CARDS for run in c["runs"]}


def verified(rid, line):
    card = CARD_OF[rid]
    text = " ".join(card["exit_summary"])
    ok = (card["counts_match"] and "partial cells seen 0" in text and "corrupt payloads 0" in text
          and re.search(r"failed 0\)", text) is not None)
    if m := re.search(r"bad (\d+) torn (\d+)", line):
        ok = ok and m.groups() == ("0", "0")
    assert ok, rid
    return card


def mode(card):
    """(ring cores, source cores, reply mode) of an inline_reflector instance."""
    cmd = card["cmd"]
    roles = re.search(r"--roles ([\w,]+)", cmd)
    roles = roles.group(1).split(",") if roles else []
    how = "wc" if "--store-wc" in cmd else "dpi" if "--dma" in cmd else "device" if "--store" in cmd else "?"
    return roles.count("ring"), roles.count("source"), how


def h2c_runs():
    """MOVDIR64B stream runs (h2c alone and bidir) with sender core and its measured clock."""
    out = []
    for i, r in enumerate(HARNESS):
        args = r["args"]
        d = re.search(r"--dir (\w+)", args)
        if not d or d.group(1) not in ("h2c", "bidir") or "--via" in args:
            continue
        lcores = [int(c) for c in re.search(r"-l ([\d,]+)", args).group(1).split(",")]
        main = int(m.group(1)) if (m := re.search(r"--main-lcore (\d+)", args)) else min(lcores)
        sender = main if d.group(1) == "h2c" else next(c for c in lcores if c != main)
        out.append((f"R{i:02d}", d.group(1), sender, r.get("core", {}).get(f"CPU{sender}", {}).get("ghz"), r["out"][0]))
    for r in jsonl("shell-runs.jsonl"):
        if r["dir"] in ("h2c", "bidir") and "busy" not in r["label"]:
            out.append((r["id"], r["dir"], r["sender_cpu"], r["ghz"].get(f"CPU{r['sender_cpu']}"), r["out"]))
    runs = []
    for rid, d, sender, ghz, line in out:
        card = verified(rid, line)
        rate = float(re.search(r"card took all at ([\d.]+) M cells/s", line).group(1))
        runs.append({"id": rid, "dir": d, "sender": sender, "ghz": ghz, "rate": rate, "cfg": mode(card)})
    return runs


def printed(i, what):
    line = HARNESS[i]["out"][0]
    verified(f"R{i:02d}", line)
    pat = {"h2c": r"card took all at ([\d.]+)", "c2h alone": r"host took them at ([\d.]+)",
           "c2h bidir": r"\| c2h .* at ([\d.]+) M cells/s"}[what]
    return float(re.search(pat, line).group(1))


def c2h_phases(i, n=200e6):
    """Reply rate while MOVDIR64B was sending, and after it had finished (bidir run R<i>)."""
    tot, h2c, c2h = HARNESS[i]["tot"], printed(i, "h2c"), printed(i, "c2h bidir")
    window = tot["iio_mmio_wr_txn"] / (h2c * 1e6)
    during = tot["iio_inb_wr_txn"] / window / 1e6
    t_h2c, t_c2h = n / (h2c * 1e6), n / (c2h * 1e6)
    after = (n - during * 1e6 * t_h2c) / (t_c2h - t_h2c) / 1e6
    return during, after


RUNS = h2c_runs()
GROUPS_A = [((1, 1, "wc"), "写合并\n卡 1 收 + 1 写"), ((1, 1, "dpi"), "DPI 写\n卡 1 收 + 1 写"),
            ((1, 1, "device"), "普通直写\n卡 1 收 + 1 写"), ((2, 2, "wc"), "写合并\n卡 2 收 + 2 写")]
slow = [r["ghz"] for r in RUNS if r["sender"] not in FIXED and r["ghz"] and r["ghz"] < SLOW_GHZ]
CATS_A = [("单独，发送核 3.9 GHz", "#9ca3af", lambda r: r["dir"] == "h2c" and r["sender"] in FIXED),
          ("双向，发送核 3.9 GHz", "#2563eb", lambda r: r["dir"] == "bidir" and r["sender"] in FIXED),
          (f"双向，发送核 {min(slow):.2f}–{max(slow):.2f} GHz（schedutil）", "#dc2626",
           lambda r: r["dir"] == "bidir" and r["sender"] not in FIXED and r["ghz"] and r["ghz"] < SLOW_GHZ)]

GROUPS_B = [("写合并\n卡 1 收 + 1 写", [2], [3]), ("DPI 写\n卡 1 收 + 1 写", [14], [15]),
            ("写合并\n卡 2 收 + 2 写", [6, 10, 36, 37, 42, 43], [7, 9])]
CATS_B = [("单独", "#9ca3af"), ("去程还在发时", "#dc2626"), ("去程发完后", "#2563eb")]


def bars(ax, x, vals, color, label):
    med = statistics.median(vals)
    ax.bar(x, med, 0.24, color=color, alpha=0.45, label=label)
    ax.scatter([x] * len(vals), vals, s=10, color=color, zorder=3)
    ax.text(x, med + 2, f"{med:.0f}", ha="center", va="bottom", fontsize=9)


fig, (ax, bx) = plt.subplots(1, 2, figsize=(13, 5), gridspec_kw={"width_ratios": [1.3, 1]})
for g, (cfg, _) in enumerate(GROUPS_A):
    for c, (label, color, pick) in enumerate(CATS_A):
        x = g + (c - 1) * 0.26
        vals = [r["rate"] for r in RUNS if r["cfg"] == cfg and pick(r)]
        if vals:
            bars(ax, x, vals, color, label if g == 0 else None)
        else:  # schedutil-sender runs exist for 2+2 but their clock was not measured
            unclocked = any(r["cfg"] == cfg and r["dir"] == "bidir" and r["sender"] not in FIXED and not r["ghz"]
                            for r in RUNS)
            ax.text(x, 3, "频率没测" if unclocked else "没测", ha="center", va="bottom", fontsize=8, color="gray")
ax.set_xticks(range(len(GROUPS_A)), [name for _, name in GROUPS_A], fontsize=9)
ax.set_ylabel("去程 MOVDIR64B（M 格/s）")
ax.set_ylim(0, 180)
ax.set_title("(a) 去程：回程同时跑，掉不掉？", fontsize=11)
ax.legend(fontsize=8.5, loc="upper left")

for g, (_, alone, bidir) in enumerate(GROUPS_B):
    phases = [c2h_phases(i) for i in bidir]
    series = [[printed(i, "c2h alone") for i in alone], [p[0] for p in phases], [p[1] for p in phases]]
    for c, ((label, color), vals) in enumerate(zip(CATS_B, series)):
        bars(bx, g + (c - 1) * 0.26, vals, color, label if g == 0 else None)
bx.set_xticks(range(len(GROUPS_B)), [g[0] for g in GROUPS_B], fontsize=9)
bx.set_ylabel("回程（M 格/s）")
bx.set_ylim(0, 180)
bx.set_title("(b) 回程：去程还在发时掉不掉？（发送核 3.9 GHz）", fontsize=11)
bx.legend(fontsize=8.5, loc="upper left")
fig.suptitle("MRV-24：64 B/格；主机 MOVDIR64B 每条 1 格；每次 0.5–2 亿格，双向时两边各自发完就停；每格都校验",
             fontsize=10)
print(save(fig, "mrv-24-summary"))
