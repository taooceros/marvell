import ctypes, mmap, os
# Read-only dump of RPM2 LMAC0 1G PCS (MTI LPCS) registers on CN106.
BASE = 0x87e0e2000000
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BASE + 0x30000)
q = (ctypes.c_uint64 * 8192).from_buffer(m)
names = {0: "CONTROL", 1: "STATUS", 4: "DEV_ABILITY", 5: "PARTNER_ABILITY", 6: "AN_EXPANSION", 0x12: "LINK_TIMER_0", 0x13: "LINK_TIMER_1", 0x14: "IF_MODE"}
for r, n in names.items():
    print("LPCS0 %-16s (reg %#04x) = %#06x" % (n, r, q[r] & 0xffff))
