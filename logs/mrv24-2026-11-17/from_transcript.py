"""Rebuild the MRV-24 records that only exist in the agent's session transcript.

runs.jsonl (next to this file) is the harness log of 2026-11-17. The runs started straight
from the shell (2026-11-16 23:53 to 2026-11-17 00:10 UTC) and the card's inline_reflector
start lines and exit summaries were only printed into the session, so this script takes
them from the session file and writes:

  shell-runs.jsonl  one record per inline_stream result line: the call's full command, the
                    inline_stream arguments of that run, sender/receiver CPU, the line as
                    printed (after the call's own grep/sed) and the clocks perf printed
                    after it (GHz = cycles / ref-cycles x 2.2, whole process, per CPU)
  card.jsonl        one record per inline_reflector instance: command, start lines, the
                    exit summary (verification counts), the runs it served, and whether
                    its counts equal the cells of those runs
  harness.py        the Python cells of the session that defined the harness, ran R00-R45
                    (writing runs.jsonl) and, in the last cell, computed instructions and
                    cycles per message from it; in session order, not runnable on its own
  shell-out/sNN.txt the session's verbatim output of each shell call (with its command,
                    tool call id and time), which shell-runs.jsonl is parsed from

  python3 logs/mrv24-2026-11-17/from_transcript.py [session.jsonl]
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SESSION = Path(sys.argv[1] if len(sys.argv) > 1 else
               "/home/hongtao/.omp/agent/sessions/-dev-marvell/"
               "2026-11-16T20-48-28-041Z_01a1e61e-4d89-7105-9607-01d1af580cdd/Mrv24Backpressure.jsonl")
E = "-a 0000:99:02.0 --file-prefix=mrv24 --log-level=lib.eal:error"

# Card instances in the order they ran (all /root/bench/inline_reflector, the 2026-11-13
# build, md5 76561e88b7e0648091307d8381c3a8df; pem_wc loaded from I1 until the end).
WC22 = "-l 3-6 -a 0002:01:00.2 --file-prefix=inl -- --roles ring,ring,source,source --store --store-wc"
CARD = {
    "I1": "-l 3-6 -a 0002:01:00.2 --file-prefix=inl -- --roles ring,ring,source,source --store --store-wc",
    "I2": "-l 3-4 -a 0002:01:00.2 --file-prefix=inl -- --roles ring,source --store --store-wc",
    "I3": WC22, "I4": WC22, "I5": WC22, "I6": WC22, "I7": WC22,
    "I8": "-l 3,4 -a 0002:01:00.2 -a 0000:06:00.1 -a 0000:06:00.2 --file-prefix=inl -- --roles ring,source "
          "--dma 0000:06:00.1,0000:06:00.2",
    "I9": "-l 3,4 -a 0002:01:00.2 --file-prefix=inl -- --roles ring,source --store",
    "I10": "-l 3,4 -a 0002:01:00.2 -a 0000:06:00.3 --file-prefix=inl -- --roles fetch,source "
           "--fetch-dma 0000:06:00.3 --store --store-wc",
    "I11": "-l 3 -a 0002:01:00.2 -a 0000:06:00.1 --file-prefix=inl -- --dma 0000:06:00.1",
    "I12": "-l 3-6 -a 0002:01:00.2 --file-prefix=inl -- --store --store-wc",
    "I13": WC22,
}
# Harness runs (runs.jsonl line index) per instance.
HARNESS = {"I2": range(0, 5), "I3": range(5, 10), "I4": [10], "I5": [11], "I6": [12], "I8": range(13, 17),
           "I9": range(17, 20), "I10": range(20, 23), "I11": range(23, 27), "I12": range(27, 34),
           "I13": range(34, 46)}

# Shell calls: (tool call id, card instance, runs in executed order). A run is
# (label, dir, inline_stream -l list, --main-lcore or None, cells). EAL's main lcore is
# the lowest one unless --main-lcore says otherwise; in bidir/split the main lcore takes
# the replies and the worker lcore sends.
H, C, B, S = "h2c", "c2h", "bidir", "split"
SHELL = {
    "s01": ("toolu_017Lt3X6YuQJcfzijiz6Pu2H", "I1", [("first run", H, "41", None, 50_000_000)]),
    "s02": ("toolu_01TeUNfzL6spbVsojWNtNq8r", "I1",
            [(f"lcore {l}", H, l, None, 100_000_000) for l in ("40", "41", "40", "41")]),
    "s03": ("toolu_0126bqinVQ8ybxW9kLEgE139", "I1",
            [("c2h", C, "41", None, 100_000_000), ("bidir send40 recv41", B, "40,41", 41, 100_000_000)] * 2),
    "s04": ("toolu_013zSTCnk3bCseKurbg1H8gK", "I1",
            [("send41 recv40", B, "40,41", None, 100_000_000), ("send40 recv41", B, "40,41", 41, 100_000_000)] * 3),
    "s05": ("toolu_015deqcprMvtWXJmR7XQVNN4", "I1",
            [("send40 recv41", S, "40,41", 41, 50_000_000), ("send41 recv40", S, "40,41", None, 50_000_000)] * 2),
    "s06": ("toolu_011qdoRxXPV6ZkrD3VKRVv7j", "I2",
            [("h2c send40", H, "40", None, 100_000_000), ("c2h recv41", C, "41", None, 100_000_000),
             ("bidir send40 recv41", B, "40,41", 41, 100_000_000),
             ("bidir send41 recv40", B, "40,41", None, 100_000_000)] * 2),
    "s07": ("toolu_01YQojYgGC3p8g7M5ycKqYJy", "I2",
            [("[cold 41] h2c alone on 41", H, "41", None, 50_000_000),
             ("[cold 41] h2c alone on 40", H, "40", None, 50_000_000),
             ("[cold 41] bidir send41 recv40", B, "40,41", None, 50_000_000),
             ("[warm 41] bidir send41 recv40", B, "40,41", None, 50_000_000),
             ("[cold 41] bidir send40 recv41", B, "40,41", 41, 50_000_000)] * 2),
    "s08": ("toolu_01NrrziGT8nhfQg89ecRMcmd", "I2",
            [("h2c alone main43", H, "43", None, 50_000_000),
             ("bidir send=worker43 recv=main42", B, "42,43", None, 50_000_000),
             ("bidir send=worker42 recv=main43", B, "42,43", 43, 50_000_000),
             ("h2c alone main42", H, "42", None, 50_000_000)] * 2),
    # Backgrounded as bg_118; its output came with the wait below.
    "s09": ("toolu_01C8sQkdN1x3wbwrMprkeUY1", "I2",
            [("bidir recv=main40 send=worker43", B, "40,43", None, 50_000_000),
             ("bidir recv=main42 send=worker41", B, "41,42", 42, 50_000_000),
             ("bidir recv=main40 send=worker41", B, "40,41", None, 50_000_000)] * 2),
    "s10": ("toolu_01PPqnUpBoxhUW4VDW5JHkq4", "I2", [("cpu41 clock trace", B, "40,41", None, 300_000_000)]),
    # Only the first run of this call counts: its later steps broke (a shell function
    # named `tr` called itself) and it was killed; the card counted no other traffic.
    "s11": ("toolu_016fsq4do5bJWbegcaUL19oa", "I2", [("h2c alone, cold cpu45 (main lcore)", H, "45", None, 300_000_000)]),
    "s12": ("toolu_01L61KpEyu5bcH7G2LKcASCB", "I3",
            [("idle 41", H, "40", None, 200_000_000), ("busy 41 (taskset -c 41 spin loop)", H, "40", None, 200_000_000)] * 2),
}
DELIVERED = {"s09": "toolu_01Xb1iYCEtPoyf2NUJ4T3tWh"}   # wait that returned bg_118's output
FREQ_TRACE = "toolu_01GbdEah7nvp3Gqdch58TASZ"            # per-interval CPU41 clock of s10

# Card start/exit outputs: (tool call id, "[card <tag>]" block or None for an ssh call).
EXIT_OF = {  # instance -> (call, block tag); the block's "prev:" lines are that instance's exit summary
    "I1": ("toolu_013RrRWFiRvWn7ArfAE3Ea92", None), "I2": ("toolu_01C1foHDznKjjPrhxjs6ynWC", "2+2 wc"),
    "I3": ("toolu_01H8JNiU4ougHG9zHgBYM9Xe", "2+2 wc fresh"),
    "I4": ("toolu_01H8JNiU4ougHG9zHgBYM9Xe", "2+2 wc (summary = c2h alone)"),
    "I5": ("toolu_01H8JNiU4ougHG9zHgBYM9Xe", "2+2 wc (summary = h2c alone)"),
    "I6": ("toolu_01H8JNiU4ougHG9zHgBYM9Xe", "2+2 wc (summary = bidir)"),
    "I7": ("toolu_01LAFSpH8h94VTuoFVY6HZXm", "1+1 dpi"), "I8": ("toolu_016yfmxKEyBtq4Zd3RvPgUDs", "1+1 device store"),
    "I9": ("toolu_016yfmxKEyBtq4Zd3RvPgUDs", "fetch+source wc"), "I10": ("toolu_016cYG6Hz8C7DECiaJNGhvVx", "1 ring dpi"),
    "I11": ("toolu_011o4o9yokNFuQDpUTukdSoG", "4 ring wc"), "I12": ("toolu_016eTt9izyttpAz76a34bmCx", "2+2 wc for per-box"),
    "I13": ("toolu_01NSbbU45XrZDi1hVEp5sRdp", None),
}
START_OF = {"I1": ("toolu_013FsSZQWHP6KDiAU1VnGSZu", None), "I2": ("toolu_013RrRWFiRvWn7ArfAE3Ea92", None)}
for inst, nxt in zip(list(EXIT_OF)[:-1], list(EXIT_OF)[1:]):
    if nxt not in START_OF:
        START_OF[nxt] = EXIT_OF[inst]
# After I13: the restore call (inline_reflector stopped, pem_wc unloaded, reflector-rs back)
# and the EDAC/SEI-UER check ~20 s later (backgrounded as bg_134, returned by a wait).
AFTER = ("toolu_01NSbbU45XrZDi1hVEp5sRdp", "toolu_019xdebG2rD9FiWqhe2fURmk")


def load(session):
    calls, results, order = {}, {}, []
    for line in session.read_text().splitlines():
        e = json.loads(line)
        m = e.get("message") if e.get("type") == "message" else None
        if not m:
            continue
        if m.get("role") == "assistant":
            for c in m["content"]:
                if c.get("type") == "toolCall":
                    calls[c["id"]] = (m.get("timestamp"), c["name"], c.get("arguments") or {})
                    order.append(c["id"])
        elif m.get("role") == "toolResult":
            results[m["toolCallId"]] = "\n".join(x.get("text", "") for x in m["content"] if x.get("type") == "text")
    return calls, results, order


def when(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main_lcore(lcores, main):
    cpus = [int(x) for x in lcores.split(",")]
    return main if main is not None else min(cpus), cpus


RESULT = re.compile(r"(h2c via |c2h \d card cores|bidir \d+|split via )")
GHZ = re.compile(r"CPU(\d+) ([\d.]+) GHz")


def shell_records(calls, results):
    recs = []
    for sid, (call, inst, plan) in SHELL.items():
        text = results[DELIVERED.get(sid, call)]
        lines = text.splitlines()
        found = []  # (result line, ghz dict)
        for i, l in enumerate(lines):
            if RESULT.search(l):
                ghz = {}
                if i + 1 < len(lines) and GHZ.search(lines[i + 1]):
                    ghz = {f"CPU{c}": float(g) for c, g in GHZ.findall(lines[i + 1])}
                found.append((l, ghz))
        if sid == "s11":
            found = found[:1]
        assert len(found) == len(plan), (sid, len(found), len(plan))
        for n, ((label, d, lcores, main, cells), (line, ghz)) in enumerate(zip(plan, found), 1):
            m, cpus = main_lcore(lcores, main)
            worker = [c for c in cpus if c != m]
            sender = m if d == H else (worker[0] if d in (B, S) else None)
            receiver = m if d in (C, B, S) else None
            args = f"-l {lcores}" + (f" --main-lcore {main}" if main is not None else "") + f" {E} -- --dir {d} -n {cells}"
            rec = {"id": f"{sid}-{n:02d}", "call": call, "call_time": when(calls[call][0]), "card": inst,
                   "label": label, "dir": d, "cells": cells, "inline_stream": args, "sender_cpu": sender,
                   "receiver_cpu": receiver, "out": line, "ghz": ghz, "cmd": calls[call][2]["command"]}
            if sid == "s10":
                rec["clock_trace_cpu41"] = results[FREQ_TRACE].splitlines()[0]
            recs.append(rec)
    return recs


def card_block(text, tag):
    """Lines of a "[card <tag>]" block printed by the harness's card() helper."""
    out, inside = {"prev": [], "new": []}, False
    for l in text.splitlines():
        if l.startswith("[card "):
            inside = l == f"[card {tag}]"
            continue
        m = re.match(r"\s+(prev|new): (.*)", l)
        if inside and m:
            out[m.group(1)].append(m.group(2))
        elif inside and l.strip() and not m:
            inside = False
    return out


SUMMARY = re.compile(r"^(reflected|per core|stream writing|fetched)")


def card_records(calls, results, shell):
    recs = []
    for inst, cmd in CARD.items():
        call, tag = START_OF[inst]
        text = results[call]
        if tag:
            start = card_block(text, tag)["new"]
        else:
            start = [l for l in text.splitlines() if "BAR4 slot" in l or "stores to the host" in l][-1:]
        call_e, tag_e = EXIT_OF[inst]
        text_e = results[call_e]
        if tag_e:
            summary = card_block(text_e, tag_e)["prev"]
        else:
            summary = [l for l in text_e.splitlines() if SUMMARY.match(l)]
            if inst == "I1":  # the ssh output prints I1's summary before I2's start line
                summary = summary[:3]
        assert summary and summary[0].startswith("reflected"), inst
        runs = [f"R{i:02d}" for i in HARNESS.get(inst, [])] + [r["id"] for r in shell if r["card"] == inst]
        rec = {"id": inst, "cmd": f"/root/bench/inline_reflector {cmd}", "started_by": call,
               "start": start, "exit_by": call_e, "exit_summary": summary, "runs": runs}
        if inst == "I13":
            keep = re.compile(r"^(inline_reflector stopped|pem_wc unloaded|refl:|reflector running|"
                              r"\d+ /root/bench/reflector-rs|total_ue=|SEI-UER events:)")
            rec["after"] = [l.strip() for c in AFTER for l in results[c].splitlines() if keep.search(l.strip())]
        rec["counts_match"] = check_counts(rec)
        recs.append(rec)
    return recs


def check_counts(rec):
    """Whether the instance's exit counts equal the cells of the runs it served: h2c cells
    taken without reply (`sunk`), card -> host stream cells (`sourced`), replies
    (`reflected`; inline_pingpong adds 20000 warmup rounds) and fetch-ring cells (`fetched`)."""
    want = {"sunk": 0, "sourced": 0, "reflected": 0, "fetched": 0}
    for run in rec["runs"]:
        if run.startswith("R"):
            r = HARNESS_RUNS[int(run[1:])]
            n = int(re.search(r"-n (\d+)", r["args"]).group(1))
            if r["label"].startswith("closed"):
                want["reflected"] += n + 20_000
                continue
            d, fetch = re.search(r"--dir (\w+)", r["args"]).group(1), "--via fetch" in r["args"]
        else:
            r = SHELL_RUNS[run]
            n, d, fetch = r["cells"], r["dir"], False
        want["sunk"] += n if d in (H, B) else 0
        want["sourced"] += n if d in (C, B) else 0
        want["reflected"] += n if d == S else 0
        want["fetched"] += n if fetch else 0
    text = " ".join(rec["exit_summary"])
    got = {"sunk": int(re.search(r"sunk (\d+)", text).group(1)),
           "sourced": int(re.search(r"sourced (\d+)", text).group(1)),
           "reflected": int(re.search(r"^reflected (\d+)", text).group(1)),
           "fetched": int(m.group(1)) if (m := re.search(r"fetched (\d+)", text)) else 0}
    if got != want:
        print(f"{rec['id']}: card counted {got}, runs add up to {want}")
    return got == want


# Python cells inside the harness window that only searched old sessions (no runs).
NOT_HARNESS = {"toolu_01XETJRB6mkpRrD2KvJn7GmN", "toolu_01RP1qXAGAuMxrTnR9cCupbs"}


def harness_cells(calls, order):
    """The harness cells from its first definition to the last analysis, in session order."""
    first = next(k for k in order if calls[k][1] == "eval" and "def perf_run" in calls[k][2].get("code", ""))
    last = next(k for k in order if calls[k][2].get("i") == "Posting conclusion and moving MRV-24 to In Review")
    cells = [k for k in order if calls[k][1] == "eval" and calls[first][0] <= calls[k][0] < calls[last][0]
             and k not in NOT_HARNESS]
    out = ['"""MRV-24 harness cells as they ran in the agent session, in order: definitions (EV event',
           'encodings, run/derive, card() which restarted the card\'s inline_reflector over ssh), the',
           'cells that ran R00-R45 and appended them to runs.jsonl, and a last cell computing',
           'instructions and cycles per message from the results. Not runnable on its own."""']
    for i, k in enumerate(cells, 1):
        ts, _, args = calls[k]
        out.append(f"\n# %% cell {i}: {when(ts)} {k} {args.get('title', '')}\n{args['code'].rstrip()}")
    return "\n".join(out) + "\n"


def shell_outputs(calls, results):
    """{sNN: verbatim session text of the call (and of the wait that returned it)}."""
    out = {}
    for sid, (call, _, _) in SHELL.items():
        ts, _, args = calls[call]
        parts = [f"# session {SESSION.name}, tool call {call}, {when(ts)}", f"$ {args['command']}", results[call]]
        if sid in DELIVERED:
            parts += [f"# output returned later by tool call {DELIVERED[sid]}:", results[DELIVERED[sid]]]
        if sid == "s10":
            ts2, _, args2 = calls[FREQ_TRACE]
            parts += [f"# tool call {FREQ_TRACE}, {when(ts2)}", f"$ {args2['command']}", results[FREQ_TRACE]]
        out[sid] = "\n".join(parts).rstrip() + "\n"
    return out


if __name__ == "__main__":
    calls, results, order = load(SESSION)
    HARNESS_RUNS = [json.loads(l) for l in (HERE / "runs.jsonl").read_text().splitlines()]
    shell = shell_records(calls, results)
    SHELL_RUNS = {r["id"]: r for r in shell}
    card = card_records(calls, results, shell)
    (HERE / "shell-runs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in shell))
    (HERE / "card.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in card))
    (HERE / "harness.py").write_text(harness_cells(calls, order))
    (HERE / "shell-out").mkdir(exist_ok=True)
    for sid, text in shell_outputs(calls, results).items():
        (HERE / "shell-out" / f"{sid}.txt").write_text(text)
    print(f"{len(shell)} shell runs, {len(card)} card instances, counts match for "
          f"{sum(r['counts_match'] for r in card)}")
