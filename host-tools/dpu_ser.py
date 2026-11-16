#!/usr/bin/env python3
"""Throwaway serial helper: send a string to a tty, read until regex or timeout.

usage: dpu_ser.py DEV TIMEOUT_S PATTERN SEND
SEND supports backslash escapes (e.g. 'root\r').
"""
import os, re, select, sys, termios, time, tty

dev, timeout, pattern, send = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
send = send.encode().decode("unicode_escape").encode()

fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
tty.setraw(fd)
a = termios.tcgetattr(fd)
a[4] = a[5] = termios.B115200
a[2] = (a[2] | termios.CLOCAL | termios.CREAD) & ~termios.CRTSCTS
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIFLUSH)

os.write(fd, send)
buf, end, rx = b"", time.time() + timeout, re.compile(pattern.encode()) if pattern else None
while time.time() < end:
    r, _, _ = select.select([fd], [], [], 0.2)
    if not r:
        continue
    try:
        buf += os.read(fd, 65536)
    except BlockingIOError:
        continue
    if rx and rx.search(buf[-256:]):
        break
os.close(fd)
out = re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", buf).decode(errors="replace")
sys.stdout.write(out.replace("\r", ""))
print("\n[matched]" if rx and rx.search(buf[-256:]) else "\n[timeout]")
