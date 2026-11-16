#!/bin/sh
# Bring up the PCIe EP control plane on CN106 (SDK13.26.10 firmware, kernel
# 6.12.51, octep_cp_agent 26.10 VFIO build) so the host's octeon_ep driver can
# attach. Runs at every card boot as octep-cp-agent.service
# (`octep_up.sh --foreground`: the agent stays in the unit and logs to the
# journal); by hand it starts the agent in the background. AGENT=/path
# overrides the binary.
#
# Prerequisite (EBF, saved once): PCIe Configuration -> PCIe EP Security:
#   A) Allow PCIe Host to Access Octeon Memory = 1
#   S) Host Stream IDs for PEM0 = 0x3ffff
# Without it the host's BAR4 reads fault in the card SMMU (C_BAD_STREAMID)
# and octeon_ep logs "Invalid magic number ffffffffffffffff".
set -e

PEM=0001:00:10.0
SDP0=0002:18:00.0
SDP1=0002:19:00.0
LOG=/tmp/octep-cp-log.txt
AGENT=${AGENT:-octep_cp_agent}
CFG=${CFG:-/usr/bin/cn106xx.cfg}

pgrep -x octep_cp_agent >/dev/null && { echo "octep_cp_agent already running"; exit 0; }

# The shipped agent is the VFIO build: its control mailbox lives in SDP RVU
# PF0 BAR4 at +28 MiB, which firmware exposes as host BAR4 index 7.
# pcie_marvell_cnxk_ep would repoint indexes 7..13 to its own pages; it is
# blacklisted (/etc/modprobe.d/blacklist-pcie-marvell-cnxk-ep.conf), so index 7
# should already be on the firmware map. Fall back to fixing it if not.
grep -q '^pcie_marvell_cnxk_ep ' /proc/modules && echo "WARNING: pcie_marvell_cnxk_ep is loaded (blacklist missing?)"
python3 - <<'EOF'
import ctypes, mmap, os
PEM0_CSR = 0x8e0000000000          # PEM0 BAR0
SDP_BAR4 = 0xbfa000000             # SDP RVU PF0 BAR4 (EA), firmware map base
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=PEM0_CSR)
q = (ctypes.c_uint64 * 8192).from_buffer(m)   # index single registers only
I = 0x700 >> 3                     # PEM_BAR4_INDEX(0)
lin = lambda k: ((SDP_BAR4 + k * 0x400000) >> 22) << 4 | 1
if q[I + 6] != lin(6) or q[I + 15] != lin(15):
    raise SystemExit("PEM0 BAR4 map is not the expected firmware layout; not touching it")
if q[I + 7] == lin(7):
    print("PEM0 BAR4_INDEX7 ok (%#x)" % lin(7))
else:
    print("PEM0 BAR4_INDEX7 %#x -> %#x" % (q[I + 7], lin(7)))
    q[I + 7] = lin(7)
EOF

for d in $PEM $SDP0 $SDP1; do
    if [ "$(basename "$(readlink -f /sys/bus/pci/devices/$d/driver)")" != vfio-pci ]; then
        echo vfio-pci > /sys/bus/pci/devices/$d/driver_override
        echo $d > /sys/bus/pci/drivers_probe
    fi
done

if [ "${1:-}" = --foreground ]; then
    exec "$AGENT" "$CFG" -- --sdp_rvu_pf $SDP0,$SDP1 --pem_dev $PEM
fi
nohup "$AGENT" "$CFG" -- \
    --sdp_rvu_pf $SDP0,$SDP1 --pem_dev $PEM > $LOG 2>&1 &
sleep 3
grep -m1 "fw ready" $LOG || { tail -20 $LOG; exit 1; }
