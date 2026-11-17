"""MRV-24: MOVDIR64B (host -> card) rate alone vs with card -> host replies flowing at the same
time (inline_stream bidir), per reply mode, with the sending core on cpu40/104 (performance
governor, 3.9 GHz) or on a schedutil core whose measured clock stayed below 2.5 GHz.

(a) per reply mode: bars = median, dots = runs.
(b) card 1 ring + 1 source core: rate vs the sending core's measured clock (perf cycles /
    ref-cycles x 2.2 GHz over the whole process), hollow = alone, filled = bidir.

Data: logs/mrv24-2026-11-17/runs.jsonl (harness runs R00-R45), shell-runs.jsonl (runs started
from the shell) and card.jsonl (card instances, their exit counts and the runs each served).
Every plotted run has bad 0 / torn 0 where the host checks (c2h legs) and an instance exit
summary with partial 0, corrupt 0, failed 0 and counts equal to its runs' cells.

    uv run --with matplotlib python docs/figures/mrv-24-sender-clock.py
"""
import json
import re
import statistics
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs/mrv24-2026-11-17"
FIXED = {40, 104}  # performance governor (bench/host_power.sh pin); every other core schedutil
COLD_GHZ = 2.5     # schedutil sender measured below this (2.07-2.12 GHz in these runs)


def jsonl(name):
    return [json.loads(l) for l in (LOGS / name).read_text().splitlines()]


def card_config(card):
    """(ring cores, source cores, reply mode) of an inline_reflector instance."""
    cmd = card["cmd"]
    roles = re.search(r"--roles ([\w,]+)", cmd)
    roles = roles.group(1).split(",") if roles else []
    mode = "wc" if "--store-wc" in cmd else "dpi" if "--dma" in cmd else "device" if "--store" in cmd else "?"
    return roles.count("ring"), roles.count("source"), mode


def verified(line, card):
    text = " ".join(card["exit_summary"])
    ok = (card["counts_match"] and "partial cells seen 0" in text and "corrupt payloads 0" in text
          and re.search(r"failed 0\)", text) is not None)
    if m := re.search(r"bad (\d+) torn (\d+)", line):
        ok = ok and m.groups() == ("0", "0")
    return ok


def runs():
    cards = jsonl("card.jsonl")
    card_of = {run: c for c in cards for run in c["runs"]}
    out = []
    for i, r in enumerate(jsonl("runs.jsonl")):
        rid, args = f"R{i:02d}", r["args"]
        if "--dir" not in args or "--via fetch" in args:  # closed loop / fetch: not MOVDIR64B streams
            continue
        d = re.search(r"--dir (\w+)", args).group(1)
        lcores = [int(c) for c in re.search(r"-l ([\d,]+)", args).group(1).split(",")]
        main = int(m.group(1)) if (m := re.search(r"--main-lcore (\d+)", args)) else min(lcores)
        sender = main if d == "h2c" else next((c for c in lcores if c != main), None)
        ghz = r.get("core", {}).get(f"CPU{sender}", {}).get("ghz")
        out.append({"id": rid, "dir": d, "sender": sender, "ghz": ghz, "line": r["out"][0],
                    "card": card_of[rid], "label": r["label"]})
    for r in jsonl("shell-runs.jsonl"):
        out.append({"id": r["id"], "dir": r["dir"], "sender": r["sender_cpu"],
                    "ghz": r["ghz"].get(f"CPU{r['sender_cpu']}"), "line": r["out"],
                    "card": card_of[r["id"]], "label": r["label"]})
    keep = []
    for r in out:
        if r["dir"] not in ("h2c", "bidir") or "busy" in r["label"]:  # busy: a spinning neighbour core
            continue
        assert verified(r["line"], r["card"]), r["id"]
        r["rate"] = float(re.search(r"card took all at ([\d.]+) M cells/s", r["line"]).group(1))
        r["ring"], r["source"], r["mode"] = card_config(r["card"])
        keep.append(r)
    return keep


RUNS = runs()
GB = 64 / 1000  # M cells/s -> GB/s
GROUPS = [((1, 1, "wc"), "写合并卡核直写\n卡 1 收 + 1 写"), ((1, 1, "dpi"), "DPI 写\n卡 1 收 + 1 写"),
          ((1, 1, "device"), "普通卡核直写（Device 映射）\n卡 1 收 + 1 写"),
          ((2, 2, "wc"), "写合并卡核直写\n卡 2 收 + 2 写（主机发送核是瓶颈）")]
cold = [r["ghz"] for r in RUNS if r["sender"] not in FIXED and r["ghz"] and r["ghz"] < COLD_GHZ]
CATS = [("alone", "#2563eb", "单独（只有去程），发送核 40/104 固定 3.9 GHz",
         lambda r: r["dir"] == "h2c" and r["sender"] in FIXED),
        ("bidir", "#16a34a", "双向（同时有回程），发送核 40/104 固定 3.9 GHz",
         lambda r: r["dir"] == "bidir" and r["sender"] in FIXED),
        ("cold", "#dc2626",
         f"双向，发送核在 schedutil 核上、实测只有 {min(cold):.2f}–{max(cold):.2f} GHz",
         lambda r: r["dir"] == "bidir" and r["sender"] not in FIXED and r["ghz"] and r["ghz"] < COLD_GHZ)]

fig, (ax, bx) = plt.subplots(1, 2, figsize=(14, 5.6), gridspec_kw={"width_ratios": [1.45, 1]})
width = 0.26
for g, (cfg, name) in enumerate(GROUPS):
    for c, (_, color, label, pick) in enumerate(CATS):
        x = g + (c - 1) * width
        vals = [r["rate"] for r in RUNS if (r["ring"], r["source"], r["mode"]) == cfg and pick(r)]
        if not vals:
            # cold slot: schedutil-sender runs whose clock was not measured are not plotted
            unclocked = c == 2 and any((r["ring"], r["source"], r["mode"]) == cfg and r["dir"] == "bidir"
                                       and r["sender"] not in FIXED and not r["ghz"] for r in RUNS)
            ax.text(x, 4, "频率没测" if unclocked else "未测", ha="center", va="bottom", fontsize=8,
                    color="gray", rotation=90)
            continue
        med = statistics.median(vals)
        ax.bar(x, med, width * 0.92, color=color, alpha=0.35, label=label if g == 0 else None)
        ax.scatter([x + (i - (len(vals) - 1) / 2) * 0.012 for i in range(len(vals))], vals, s=10,
                   color=color, zorder=3)
        ax.text(x, med + 2, f"{med:.1f}", ha="center", va="bottom", fontsize=8)
ax.set_xticks(range(len(GROUPS)), [name for _, name in GROUPS], fontsize=8)
ax.set_ylabel("去程 MOVDIR64B（M 格/s，卡收完为准）")
ax.secondary_yaxis("right", functions=(lambda v: v * GB, lambda v: v / GB)).set_ylabel("GB/s（64 B/格）")
ax.set_ylim(0, 175)
ax.set_title("(a) 每种回程：单独 vs 双向（柱 = 中位数，点 = 每次运行）", fontsize=10)
ax.legend(fontsize=8, loc="upper left")

colors = {"wc": "#2563eb", "dpi": "#ea580c", "device": "#16a34a"}
names = {"wc": "写合并", "dpi": "DPI 写", "device": "普通直写"}
for mode in colors:
    for d, filled in (("h2c", False), ("bidir", True)):
        pts = [(r["ghz"], r["rate"]) for r in RUNS
               if (r["ring"], r["source"], r["mode"]) == (1, 1, mode) and r["dir"] == d and r["ghz"]]
        if pts:
            bx.scatter(*zip(*pts), s=28, facecolors=colors[mode] if filled else "none", edgecolors=colors[mode],
                       linewidths=1.2, label=f"{names[mode]}，{'双向' if filled else '单独'}（{len(pts)} 次）")
bx.set_xlabel("发送核平均频率（GHz，perf cycles / ref-cycles × 2.2，整个进程）")
bx.set_ylabel("去程 MOVDIR64B（M 格/s）")
bx.secondary_yaxis("right", functions=(lambda v: v * GB, lambda v: v / GB)).set_ylabel("GB/s")
bx.set_title("(b) 卡 1 收 + 1 写：去程速率 vs 发送核实测频率\n空心 = 单独，实心 = 双向；2.1 GHz 附近只有双向的点",
             fontsize=10)
bx.legend(fontsize=8, loc="lower right")
fig.suptitle("MRV-24：回程同时跑时 MOVDIR64B 变不变慢？64 B/格；主机每条 MOVDIR64B 1 格、不等回复（去程在途 ≤ 65533 格）；"
             "卡收请求核每轮取 ≤ 32 格；回程在途 ≤ 4096 格；每格都校验", fontsize=10)
print(save(fig, "mrv-24-sender-clock"))
