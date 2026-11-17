"""MRV-63 overview: best verified throughput of each direct path, one direction alone vs closed loop.

Upper block, one direction alone (open loop): `inline_stream --dir h2c|c2h`, one host core, 64 B
cells, one cell per MOVDIR64B / per card store, every cell checked. Data: logs/mrv24-2026-11-17
(runs.jsonl, shell-runs.jsonl, card.jsonl). Host -> card runs count only with the sending core on
cpu40/104 (performance governor, 3.9 GHz); card -> host runs were received on cpu41 (schedutil,
2.65-3.85 GHz where it was measured).

Lower block, closed loop: `inline_pingpong`, host core 40 (3.9 GHz) sends and receives, one
MOVDIR64B per request (batch 1), window W. Data: logs/mrv17-2026-11-17/runs.jsonl, the same
selection as mrv-44-throughput-vs-w.py: the `OFFICIAL` sweep, the repeats under the same settings
with no experiment flag in use, and the lease build (`LEASE H1`). Dots: every such run at
W = 64..512. Solid bar: the best run at any W. Hatched bar: the best run of the diagnostic card
build that pauses after an empty poll (`--poll-gap-ns`, tags X3 / X3b / X3c), one run per condition.

Every plotted run is verified (host lost/bad/torn 0, card partial/corrupt 0). The script prints the
table behind the chart.

    uv run --with matplotlib python docs/figures/mrv-63-overview.py
"""
import json
import re
from collections import defaultdict
from pathlib import Path

from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
L24 = ROOT / "logs/mrv24-2026-11-17"
L17 = ROOT / "logs/mrv17-2026-11-17"
FIXED = {40, 104}  # performance governor, 3.9 GHz; every other host core ran schedutil
SLOW_W = (64, 128, 256, 512)
LINK_GBS = 32e9 * 4 * (128 / 130) / 8 / 1e9  # PCIe Gen5 x4, one direction: 15.75 GB/s
LINK_64 = LINK_GBS * 1e9 / (64 + 24) / 1e6   # 64 B writes with 24 B of fixed cost each (MRV-22): 179 M/s


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---- one direction alone (MRV-24 data) ----------------------------------------------------
HARNESS = jsonl(L24 / "runs.jsonl")
CARD_OF = {run: card for card in jsonl(L24 / "card.jsonl") for run in card["runs"]}


def verified_stream(rid, line):
    card = CARD_OF[rid]
    text = " ".join(card["exit_summary"])
    ok = (card["counts_match"] and "partial cells seen 0" in text and "corrupt payloads 0" in text
          and re.search(r"failed 0\)", text) is not None)
    if m := re.search(r"bad (\d+) torn (\d+)", line):
        ok = ok and m.groups() == ("0", "0")
    assert ok, rid
    cmd = card["cmd"]
    roles = (m.group(1).split(",") if (m := re.search(r"--roles ([\w,]+)", cmd)) else [])
    how = "wc" if "--store-wc" in cmd else "dpi" if "--dma" in cmd else "store" if "--store" in cmd else "?"
    return roles.count("ring"), roles.count("source"), how


def stream_runs():
    """(row key, run id, M cells/s) of every verified run with one direction alone."""
    raw = []  # (id, dir, via fetch?, sender cpu, output line)
    for i, r in enumerate(HARNESS):
        a = r["args"]
        d = re.search(r"--dir (\w+)", a)
        if not d or d.group(1) not in ("h2c", "c2h"):
            continue
        lcores = [int(c) for c in re.search(r"-l ([\d,]+)", a).group(1).split(",")]
        raw.append((f"R{i:02d}", d.group(1), "--via fetch" in a, lcores[0], r["out"][0]))
    for r in jsonl(L24 / "shell-runs.jsonl"):
        if r["dir"] in ("h2c", "c2h"):
            raw.append((r["id"], r["dir"], "--via fetch" in r["cmd"], r["sender_cpu"], r["out"]))
    out = []
    for rid, d, fetch, sender, line in raw:
        ring, source, how = verified_stream(rid, line)
        if d == "h2c":
            if sender not in FIXED:
                continue  # the sending core's clock was not held
            rate = float(re.search(r"card took all at ([\d.]+) M cells/s", line).group(1))
            key = ("h2c", "fetch", 1) if fetch else ("h2c", "movdir", ring)
        else:
            rate = float(re.search(r"host took them at ([\d.]+) M cells/s", line).group(1))
            key = ("c2h", how, source)
        out.append((key, rid, rate))
    return out


# ---- closed loop (MRV-17 data) -------------------------------------------------------------
CARD = "/root/bench/inline_reflector"


def path_of(card_args, host_cmd):
    if "--store-wc" in card_args:
        return "wc"
    if "--store" in card_args:
        return "store"
    if "--dma" in card_args:
        return "dpi"
    return "sdp" if "rx_poll_mem=1" in host_cmd else None  # SDP reference: the rx_poll_mem=1 line only


def closed_runs():
    """Per (path, card cores): runs without a pause as (W, Mmsg/s, tag), and with a card pause as
    (W, Mmsg/s, pause ns, tag)."""
    plain, paused = defaultdict(list), defaultdict(list)
    for r in jsonl(L17 / "runs.jsonl"):
        if r.get("rc", 0) != 0 or not r.get("host") or not r.get("card_cmd") or "inline_pingpong" not in r["host_cmd"]:
            continue
        tag, card_cmd, host_cmd = r["tag"], r["card_cmd"] + " ", r["host_cmd"]
        card_args = card_cmd.split(" -- ")[1] if " -- " in card_cmd else ""
        cores = 4 if " -l 3-6 " in card_cmd else 1 if " -l 3 " in card_cmd else None
        path = path_of(card_args, host_cmd)
        if cores is None or path is None:
            continue
        h = r["host"][0]
        gap = re.search(r"--poll-gap-ns (\d+)", card_cmd)
        ok = h["lost"] == h["bad"] == h["torn"] == 0 and r["card"]["partial"] == 0 and r["card"]["corrupt"] == 0
        if re.match(r"X3[bc]? ", tag) and gap and int(gap.group(1)) > 0 and "--poll-gap-ns" not in host_cmd:
            assert ok and "inline_reflector-mrv17 " in card_cmd, tag
            paused[(path, cores)].append((int(h["window"]), h["mmsg"], int(gap.group(1)), tag))
        elif "--poll-gap-ns" in card_cmd + host_cmd or "--sample-card-next" in host_cmd:
            continue
        elif re.match(r"OFFICIAL (wc|store|dpi|sdp mempoll) n[14] H1$", tag) or re.match(
                r"(ALT |E1 |LANDED .* noprobe |CAP .* cap32$|Xbin .* nov13$)", tag):
            assert ok and card_cmd.split()[0] == CARD, tag
            assert not re.search(r"--reply-cap (?!32\b)", host_cmd), tag
            plain[(path, cores)].append((int(h["window"]), h["mmsg"], tag))
        elif re.match(r"LEASE H1 ", tag):
            assert ok and "--host-lcores 1" in host_cmd, tag
            plain[(path, cores)].append((int(h["window"]), h["mmsg"], tag))
    return plain, paused


STREAMS = defaultdict(list)
for key, rid, rate in stream_runs():
    STREAMS[key].append((rate, rid))
PLAIN, PAUSED = closed_runs()

BLUE, RED, DARK, GREY, AMBER = "#2563eb", "#dc2626", "#1f2937", "#9ca3af", "#f59e0b"
ONE_WAY = [  # (key, label, colour); grey = reference rows (DPI)
    (("h2c", "movdir", 1), "去程 MOVDIR64B → 卡 1 核收", BLUE),
    (("h2c", "movdir", 2), "去程 MOVDIR64B → 卡 2 核收", BLUE),
    (("c2h", "wc", 1), "回程 写合并直写，卡 1 核写", RED),
    (("c2h", "wc", 2), "回程 写合并直写，卡 2 核写", RED),
    (("c2h", "store", 1), "回程 普通直写，卡 1 核写", RED),
    (("h2c", "fetch", 1), "参照：去程 fetch（卡 DPI 读）", GREY),
    (("c2h", "dpi", 1), "参照：回程 DPI 写，卡 1 核", GREY),
]
CLOSED = [  # grey = reference rows (DPI, SDP)
    (("wc", 1), "闭环 写合并回程，卡 1 核", DARK),
    (("wc", 4), "闭环 写合并回程，卡 4 核", DARK),
    (("store", 4), "闭环 普通直写回程，卡 4 核", DARK),
    (("dpi", 4), "参照：闭环 DPI 回程，卡 4 核", GREY),
    (("sdp", 4), "参照：闭环 SDP 回程，卡 4 核", GREY),
]

fig, ax = plt.subplots(figsize=(12.5, 7.2))
ys, labels = [], []
y = 0.0
print("one direction alone (M cells/s): best, range, runs")
for key, label, color in ONE_WAY:
    vals = sorted(STREAMS[key])
    assert vals, key
    best, best_id = vals[-1]
    ax.barh(y, best, 0.56, color=color, alpha=0.35, zorder=2)
    ax.scatter([v for v, _ in vals], [y] * len(vals), s=9, color=color, zorder=3)
    ax.text(best + 2.5, y, f"{best:.1f}", va="center", fontsize=10.5)
    print(f"  {label}: best {best:.2f} ({best_id}), range {vals[0][0]:.2f}-{best:.2f}, n={len(vals)}")
    ys.append(y)
    labels.append(label)
    y += 1
last_write_row = 4  # the link limit below is for 64 B writes: rows 0..4
split = y - 0.42
y += 0.25
top_closed = y - 0.5
print("closed loop (Mmsg/s): best without pause, W=64..512 range, best with a card pause")
for key, label, color in CLOSED:
    runs = PLAIN[key]
    assert runs and PAUSED.get(key), key
    slow = [v for w, v, _ in runs if w in SLOW_W]
    w_best, best, tag_best = max(runs, key=lambda t: t[1])
    w_p, v_p, gap, tag_p = max(PAUSED[key], key=lambda t: t[1])
    ax.barh(y - 0.17, best, 0.34, color=color, alpha=0.35, zorder=2)
    ax.scatter(slow, [y - 0.17] * len(slow), s=9, color=color, zorder=3)
    ax.text(best + 2.5, y - 0.17, f"{best:.1f}　W={w_best}", va="center", fontsize=10.5)
    ax.barh(y + 0.21, v_p, 0.26, facecolor="none", edgecolor=color, hatch="////", lw=0.9, zorder=2)
    ax.text(v_p + 2.5, y + 0.21, f"{v_p:.1f}　卡核停 {gap // 1000} µs，W={w_p}", va="center", fontsize=9, color="#4b5563")
    print(f"  {label}: best {best:.2f} W={w_best} ({tag_best}); W=64..512 {min(slow):.2f}-{max(slow):.2f} (n={len(slow)}); "
          f"pause {gap} ns: {v_p:.2f} W={w_p} ({tag_p})")
    ys.append(y)
    labels.append(label)
    y += 1
bottom = y - 0.5

# the ~13 M/s regime (closed loop only), the block separator, the link limit for 64 B writes
ax.fill_betweenx([top_closed, bottom], 12, 14.5, color=AMBER, alpha=0.3, lw=0, zorder=1)
ax.text(13.25, bottom + 0.06, "~13", color="#b45309", fontsize=11, ha="center", va="top", fontweight="bold")
ax.axhline(split, color="#d1d5db", lw=0.8)
ax.plot([LINK_64, LINK_64], [-0.45, last_write_row + 0.45], color="#6b7280", lw=1.0, ls=":", zorder=1)
ax.text(LINK_64 + 1.5, 2.0, f"64 B 写的\n链路上限\n{LINK_64:.0f}", fontsize=8.5, color="#6b7280", ha="left", va="center")

ax.set_yticks(ys, labels, fontsize=10)
ax.set_ylim(bottom + 0.5, -0.6)
ax.set_xlim(0, 215)
ax.set_xlabel("吞吐（M 条/s，一条 64 B）")
ax.grid(axis="y", visible=False)
top = ax.secondary_xaxis("top", functions=(lambda v: v * 64 / 1000, lambda g: g * 1000 / 64))
top.set_xlabel("GB/s", fontsize=9)
ax.legend(handles=[
    Patch(facecolor=DARK, alpha=0.35, label="实心条：核对过的最好一次（闭环标出 W）"),
    Line2D([], [], marker="o", ls="", ms=3.5, color=DARK, label="点：每次运行（闭环只画 W=64–512）"),
    Patch(facecolor="white", edgecolor=DARK, hatch="////", label="斜线条：卡核空轮询后先停一下的最好一次\n（诊断版卡程序，每个条件只跑 1 次）"),
    Patch(facecolor=AMBER, alpha=0.3, label="~13 M 条/s 这一档"),
], fontsize=8.5, loc="lower right", bbox_to_anchor=(1.0, 0.03), framealpha=1.0)
ax.set_title("各直接通路的吞吐：一个方向单独跑 vs 闭环\n"
             "2026-11-17；64 B；主机 1 个核，每条 MOVDIR64B 1 条；发送核 / 闭环主机核 3.9 GHz，uncore 2.2 GHz；所有运行 lost/bad/torn 0",
             fontsize=10, pad=10)
print(save(fig, "mrv-63-overview"))
