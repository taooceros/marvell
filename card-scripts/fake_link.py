import ctypes, mmap, os, sys, time
# Emulate the ATF async LINK_CHANGE event for RPM2 LMAC0 so the AF/PF driver marks eth4 up.
# Event word: ACK=1, TYPE=ASYNC(0), ID=LINK_CHANGE(1)<<3, UP<<9, FDUPLEX<<10, SPEED(CGX_LINK_100M=2)<<11, LMAC_TYPE=SGMII(0)
BASE = 0x87e0e2000000
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BASE)
q = (ctypes.c_uint64 * 8192).from_buffer(m)
EVT, SW_INT, SW_INT_W1S = 0x1050 // 8, 0x180 // 8, 0x188 // 8
up = (sys.argv[1] if len(sys.argv) > 1 else "up") == "up"
word = 0x1 | (1 << 3) | (int(up) << 9) | (1 << 10) | (2 << 11)
print("before: EVENT_REG=%#x SW_INT=%#x" % (q[EVT], q[SW_INT]))
if q[EVT] != 0:
    sys.exit("event register busy (firmware event pending); not touching it")
q[EVT] = word
q[SW_INT_W1S] = 1
time.sleep(0.5)
print("after:  EVENT_REG=%#x SW_INT=%#x (handler acks -> 0)" % (q[EVT], q[SW_INT]))
