#!/usr/bin/env python3
"""Throwaway expect driver over a zellij pane (session dpu).

usage: zj_expect.py PANE STEPS_JSON
STEPS: [["expect", regex, timeout_s], ["send", text], ["enter"], ["sleep", s]]
Prints the screen after each expect; exits 2 on timeout.
"""
import json, re, subprocess, sys, time

pane, steps = sys.argv[1], json.loads(sys.argv[2])
Z = ["timeout", "5", "zellij", "-s", "dpu", "action"]


def dump():
    return subprocess.run(Z + ["dump-screen", "-p", pane], capture_output=True, text=True).stdout


for st in steps:
    if st[0] == "send":
        subprocess.run(Z + ["write-chars", "-p", pane, st[1]], check=True)
    elif st[0] == "enter":
        subprocess.run(Z + ["write", "-p", pane, "13"], check=True)
    elif st[0] == "sleep":
        time.sleep(st[1])
    elif st[0] == "expect":
        rx, end = re.compile(st[1], re.M), time.time() + st[2]
        while time.time() < end:
            scr = dump()
            if rx.search(scr):
                break
            time.sleep(0.25)
        else:
            print(f"[timeout waiting for {st[1]!r}]\n" + "\n".join(l.rstrip() for l in dump().splitlines() if l.strip())[-1500:])
            sys.exit(2)
        print(f"[matched {st[1]!r}]")
print("\n".join(l.rstrip() for l in dump().splitlines() if l.strip())[-2500:])
