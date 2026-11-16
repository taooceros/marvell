#!/usr/bin/env python3
"""Throwaway: type a line into a zellij pane (session dpu) and print what appears after it.

usage: zj.py PANE_ID 'command' [TIMEOUT_S] [DONE_REGEX]
"""
import re, subprocess, sys, time

pane, text = sys.argv[1], sys.argv[2]
timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 15
done = re.compile(sys.argv[4] if len(sys.argv) > 4 else r"(# |\$ )$")
Z = ["timeout", "5", "zellij", "-s", "dpu", "action"]


def dump():
    return subprocess.run(Z + ["dump-screen", "-p", pane],
                          capture_output=True, text=True).stdout.rstrip()


if text:
    subprocess.run(Z + ["write-chars", "-p", pane, text], check=True)
subprocess.run(Z + ["write", "-p", pane, "13"], check=True)
# screen is a full-screen app: anchor on the echoed command line
key = text[:30]
end, new = time.time() + timeout, []
while time.time() < end:
    time.sleep(0.4)
    lines = dump().splitlines()
    idx = [i for i, l in enumerate(lines) if key and key in l]
    new = lines[idx[-1]:] if idx else lines[-5:]
    if len(new) > 1 and done.search(new[-1].rstrip() + " "):
        break
print("\n".join(l.rstrip() for l in new))
