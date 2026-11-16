#!/usr/bin/env python3
"""Read, or set, PEM0 BAR4_INDEX7 on CN106 — one 64-bit register only.

usage: bar4idx7.py [NEW_VALUE]
  0x2fef1 = firmware linear map (SDP RVU PF0 BAR4 + 28 MiB = agent mailbox)
NEVER iterate/list() the CSR mapping: reading unimplemented PEM offsets raises
SEI-UER SErrors (happened 2026-11-10).
"""
import ctypes, mmap, os, sys

fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x8e0000000000)
q = (ctypes.c_uint64 * 8192).from_buffer(m)
R = (0x700 >> 3) + 7
print("BAR4_INDEX7 =", hex(q[R]))
if len(sys.argv) > 1:
    q[R] = int(sys.argv[1], 0)
    print("BAR4_INDEX7 ->", hex(q[R]))
