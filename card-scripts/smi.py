import ctypes, mmap, os, sys, time
# Read-only clause-22 MDIO via CN106 SMI (mdio-cavium regs). usage: smi.py BUS PHY REG...
bus, phy, regs = int(sys.argv[1]), int(sys.argv[2], 0), [int(r, 0) for r in sys.argv[3:]]
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x87e005000000)
q = (ctypes.c_uint64 * 8192).from_buffer(m)
b = (0x3800 + 0x80 * bus) // 8          # SMI_CMD=+0 WR=+1 RD=+2 CLK=+3 EN=+4 (qwords)
clk, en = q[b + 3], q[b + 4]
print("SMI%d EN=%#x CLK=%#x mode=%s" % (bus, en, clk, "c45" if clk >> 24 & 1 else "c22"))
if not en & 1:
    sys.exit("SMI disabled; not touching it")
if clk >> 24 & 1:
    q[b + 3] = clk & ~(1 << 24)   # c22 for our reads; restored below
for r in regs:
    q[b] = (1 << 16) | (phy << 8) | r
    for _ in range(1000):
        d = q[b + 2]
        if not d >> 17 & 1: break
        time.sleep(0.0005)
    print("bus%d phy%d reg%-2d = %s" % (bus, phy, r, ("0x%04x" % (d & 0xffff)) if d >> 16 & 1 else "no-valid (%#x)" % d))
q[b + 3] = clk   # restore original SMI_CLK (mode)
print("restored CLK=%#x" % q[b + 3])
