import ctypes, mmap, os, sys, time
# RUNTIME experiment: force RPM2 LMAC0 SGMII PCS to 100M full, no in-band AN. Undo: reboot or `ip link set eth4 down/up`.
BASE = 0x87e0e2000000
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BASE + 0x30000)
q = (ctypes.c_uint64 * 8192).from_buffer(m)
print("before CONTROL=%#06x STATUS=%#06x IF_MODE=%#06x PARTNER=%#06x" % (q[0] & 0xffff, q[1] & 0xffff, q[0x14] & 0xffff, q[5] & 0xffff))
mode = sys.argv[1] if len(sys.argv) > 1 else "100"
q[0x14] = {"100": 0x0005, "1000": 0x0009, "10": 0x0001}[mode]   # sgmii_ena=1, use_sgmii_an=0, speed
q[0] = (q[0] & 0xffff) & ~(1 << 12)                               # AN disable
time.sleep(1)
print("after  CONTROL=%#06x STATUS=%#06x IF_MODE=%#06x PARTNER=%#06x" % (q[0] & 0xffff, q[1] & 0xffff, q[0x14] & 0xffff, q[5] & 0xffff))
