#!/usr/bin/env python3
"""Kernel-path UDP echo on the card (baseline for the DPDK reflector)."""
import socket

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("10.10.0.2", 9000))
while True:
    d, a = s.recvfrom(2048)
    s.sendto(d, a)
