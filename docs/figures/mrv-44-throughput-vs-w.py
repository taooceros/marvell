"""MRV-44: single-host-core closed loop, throughput vs window W for each reply path, with the
verified repeats under the same settings and no experiment flag in use (MRV-17 data, logs/mrv17-2026-11-17/runs.jsonl). Host core 40 at 3.9 GHz,
uncore 2.2 GHz; 64 B, batch 1.

- Lines, filled points: the `OFFICIAL` sweep (card /root/bench/inline_reflector of 2026-11-13,
  host inline_pingpong of the MRV-17 change from the main checkout), one run per point.
- Hollow circles: every other run with the same card program and card arguments and the MRV-17
  host code without an experiment flag in use: `ALT`, `E1` (under `perf stat` counting), `LANDED
  ... noprobe`, `CAP ... cap32` (32 = the default reply cap), `Xbin ... nov13`.
- Triangles: the lease build on both sides (jj change kkvvxrou: `LEASE H1`).
- Direct paths in colour; SDP is the grey reference line (MRV-49).

    uv run --with matplotlib python docs/figures/mrv-44-throughput-vs-w.py [runs.jsonl]
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
MAIN_HOST = "/home/hongtao/dev/marvell/testbed/target/release/inline_pingpong"
TX_HOST = "/tmp/mrv17/tx/release/inline_pingpong"
PATHS = {  # reply path -> (label, colour, line style)
    "wc": ("写合并", "#dc2626", "-"),
    "store": ("直写", "#9333ea", "-"),
    "dpi": ("DPI", "#2563eb", "-"),
    "sdp": ("SDP 参照", "#9ca3af", "--"),
}


def path_of(card_args, host_cmd):
    if "--store-wc" in card_args:
        return "wc"
    if "--store" in card_args:
        return "store"
    if "--dma" in card_args:
        return "dpi"
    return "sdp" if "rx_poll_mem=1" in host_cmd else None  # SDP reference: the rx_poll_mem=1 line only


def verified(r):
    h = r["host"][0]
    assert r.get("rc", 0) == 0 and h["lost"] == h["bad"] == h["torn"] == 0, r["tag"]
    return int(h["window"]), h["mmsg"]


official = defaultdict(dict)  # (path, cores) -> {W: Mmsg/s}
repeats = defaultdict(list)  # (path, cores) -> [(W, Mmsg/s)]
lease = defaultdict(list)
for line in runs.read_text().splitlines():
    r = json.loads(line)
    if r.get("rc", 0) != 0 or not r.get("host") or not r.get("card_cmd"):
        continue
    tag, card_cmd, host_cmd = r["tag"], r["card_cmd"] + " ", r["host_cmd"]
    card, card_args = card_cmd.split()[0], card_cmd.split(" -- ")[1] if " -- " in card_cmd else ""
    cores = 4 if " -l 3-6 " in card_cmd else 1 if " -l 3 " in card_cmd else None
    path = path_of(card_args, host_cmd)
    if cores is None or path is None or "--poll-gap-ns" in card_cmd + host_cmd:
        continue
    if re.match(r"OFFICIAL (wc|store|dpi|sdp mempoll) n[14] H1$", tag):
        assert card == CARD and host_cmd.startswith(MAIN_HOST + " "), tag
        w, v = verified(r)
        official[(path, cores)][w] = v
    elif re.match(r"(ALT |E1 |LANDED .* noprobe |CAP .* cap32$|Xbin .* nov13$)", tag):
        host = next(t for t in host_cmd.split() if t.endswith("inline_pingpong"))
        assert card == CARD and host in (MAIN_HOST, TX_HOST), tag
        assert "--sample-card-next" not in host_cmd and not re.search(r"--reply-cap (?!32\b)", host_cmd), tag
        repeats[(path, cores)].append(verified(r))
    elif re.match(r"LEASE H1 ", tag):
        assert card.endswith("inline_reflector-mrv17-lease") and "--host-lcores 1" in host_cmd, tag
        lease[(path, cores)].append(verified(r))
assert len(official) == 5 and all(len(v) == 9 for v in official.values()), {k: len(v) for k, v in official.items()}

fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), sharey=True)
for ax, cores in zip(axes, (1, 4)):
    ax.axhspan(12, 14.5, color="#f59e0b", alpha=0.12, lw=0)
    for path, (label, color, ls) in PATHS.items():
        pts = official.get((path, cores))
        if pts:
            ws = sorted(pts)
            ax.plot(ws, [pts[w] for w in ws], marker="o", ms=4, color=color, linestyle=ls, lw=1.6 if path != "sdp" else 1.2,
                    label=f"{label}（每 W 一次）", zorder=3)
        if repeats.get((path, cores)):
            xs, ys = zip(*repeats[(path, cores)])
            ax.scatter(xs, ys, s=24, facecolors="none", edgecolors=color, lw=1, zorder=4, label=f"{label}，其他运行")
        if lease.get((path, cores)):
            xs, ys = zip(*lease[(path, cores)])
            ax.scatter(xs, ys, s=30, marker="^", facecolors="none", edgecolors=color, lw=1, zorder=4, label=f"{label}，租约版")
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 8, 16, 32, 64, 128, 256, 512, 1024], labels=["1", "8", "16", "32", "64", "128", "256", "512", "1024"])
    ax.set_xlabel("窗口 W（同时在途的请求数）")
    ax.set_title(f"卡 {cores} 核", fontsize=10)
    ax.set_ylim(0, 56)
    ax.legend(fontsize=7.5, loc="upper left")
axes[0].set_ylabel("吞吐 (Mmsg/s)")
fig.suptitle("单个主机核闭环吞吐 vs W，同设置、没开实验开关的重复运行（主机核 40 @3.9 GHz，uncore 2.2 GHz，64 B，批大小 1；lost/bad/torn 全 0）",
             fontsize=9.5)
print(save(fig, "mrv-44-throughput-vs-w"))
