#!/usr/bin/env python3
"""Throwaway: re-run the MCU shell's previous command N times via history (Up arrow),
verifying the recalled line before each Enter, and collect 'Read value' outputs.

usage: zj_mcu_repeat.py PANE 'expected command' N [gap_s]
"""
import re, subprocess, sys, time

pane, cmd, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
gap = float(sys.argv[4]) if len(sys.argv) > 4 else 0.2
Z = ["timeout", "5", "zellij", "-s", "dpu", "action"]


def lines():
    out = subprocess.run(Z + ["dump-screen", "-p", pane], capture_output=True, text=True).stdout
    return [l.rstrip() for l in out.splitlines() if l.strip()]


vals = []
for i in range(n):
    subprocess.run(Z + ["write", "-p", pane, "27", "91", "65"], check=True)  # Up arrow
    time.sleep(0.3)
    last = lines()[-1]
    if not re.search(r"uart:~\$ ?" + re.escape(cmd) + r"$", last):
        subprocess.run(Z + ["write", "-p", pane, "21"], check=True)          # Ctrl-U
        print(f"[recall mismatch, stopped] got {last!r}")
        break
    subprocess.run(Z + ["write", "-p", pane, "13"], check=True)
    time.sleep(0.6)
    ls = lines()
    rv = [l for l in ls[-4:] if "Read value" in l]
    vals.append(rv[-1].split()[-1] if rv else "?")
    time.sleep(gap)
print(" ".join(vals))
