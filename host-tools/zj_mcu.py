#!/usr/bin/env python3
"""Throwaway: type a command into the MCU (Zephyr) pane SLOWLY and only press Enter
if the echoed line matches exactly. The RT1176 shell UART drops characters on fast
input (no flow control), which once turned `devmem 0x40420004` into `devmem 0x40004`
and bus-faulted the MCU.

usage: zj_mcu.py PANE 'command' [TIMEOUT_S]
Exit 3 (line cleared with Ctrl-U, nothing executed) if the echo does not match.
"""
import re, subprocess, sys, time

pane, cmd = sys.argv[1], sys.argv[2]
timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 8
Z = ["timeout", "5", "zellij", "-s", "dpu", "action"]


def dump():
    return subprocess.run(Z + ["dump-screen", "-p", pane], capture_output=True, text=True).stdout


def last_line():
    lines = [l.rstrip() for l in dump().splitlines() if l.strip()]
    return lines[-1] if lines else ""


for i in range(0, len(cmd), 4):           # 4 chars at a time, then let the MCU drain
    subprocess.run(Z + ["write-chars", "-p", pane, cmd[i:i + 4]], check=True)
    time.sleep(0.15)
time.sleep(0.5)
echo = last_line()
if not echo.endswith("uart:~$ " + cmd) and not echo.endswith("uart:~$" + cmd) and not echo.endswith(cmd):
    subprocess.run(Z + ["write", "-p", pane, "21"], check=True)   # Ctrl-U: kill line
    print(f"[echo mismatch, NOT executed] got: {echo!r}")
    sys.exit(3)
if not re.search(r"uart:~\$ ?" + re.escape(cmd) + r"$", echo):
    subprocess.run(Z + ["write", "-p", pane, "21"], check=True)
    print(f"[echo not anchored at prompt, NOT executed] got: {echo!r}")
    sys.exit(3)
subprocess.run(Z + ["write", "-p", pane, "13"], check=True)
end = time.time() + timeout
while time.time() < end:
    time.sleep(0.3)
    lines = [l.rstrip() for l in dump().splitlines() if l.strip()]
    idx = [k for k, l in enumerate(lines) if l.endswith(cmd)]
    new = lines[idx[-1]:] if idx else lines[-5:]
    if len(new) > 1 and re.search(r"uart:~\$ ?$", new[-1] + " "):
        break
print("\n".join(new))
