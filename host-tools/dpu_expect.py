#!/usr/bin/env python3
"""Throwaway serial expect driver; keeps the tty open across all steps.

usage: dpu_expect.py DEV LOGFILE STEPS_JSON
STEPS_JSON: [["send", "text\\r"], ["expect", "regex", timeout_s], ["sleep", s], ...]
Exits 2 on an expect timeout. Full transcript (ANSI-stripped) goes to LOGFILE.
"""
import json, os, re, select, sys, termios, time, tty

dev, logfile, steps = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
tty.setraw(fd)
a = termios.tcgetattr(fd)
a[4] = a[5] = termios.B115200
a[2] = (a[2] | termios.CLOCAL | termios.CREAD) & ~termios.CRTSCTS
termios.tcsetattr(fd, termios.TCSANOW, a)
log = open(logfile, "ab", buffering=0)
buf = b""


def pump(t):
    global buf
    r, _, _ = select.select([fd], [], [], t)
    if r:
        try:
            d = os.read(fd, 65536)
        except BlockingIOError:
            return
        buf += d
        log.write(d)


rc = 0
for st in steps:
    if st[0] == "send":
        os.write(fd, st[1].encode())
    elif st[0] == "sleep":
        end = time.time() + st[1]
        while time.time() < end:
            pump(0.1)
    elif st[0] == "expect":
        rx, end, start = re.compile(st[1].encode()), time.time() + st[2], len(buf)
        while time.time() < end and not rx.search(buf[start:]):
            pump(0.1)
        if not rx.search(buf[start:]):
            print(f"[timeout waiting for {st[1]!r}]")
            rc = 2
            break
        print(f"[matched {st[1]!r} after {st[2] - (end - time.time()):.1f}s]")
# drain briefly so the tail is visible
for _ in range(5):
    pump(0.1)
os.close(fd)
out = re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", buf).decode(errors="replace").replace("\r", "")
print(out[-6000:])
sys.exit(rc)
