#!/usr/bin/env bash
# Safe, reversible inspection and DDIO toggle helper for saturn's CN106 Root Port.
#
# Target hardware (verified on saturn):
#   Host CPU : Intel Xeon Gold 6438M (Sapphire Rapids, 4th Gen Xeon, Socket 1)
#   Root Port: 0000:98:01.0 (Intel PCIe Gen5 Port A, 8086:352a rev 04)
#   Child EP : 0000:99:00.0 (Marvell OCTEON 10 CN106xx, 177d:b900)
#   NOTE     : Do NOT touch 0000:98:00.0 (8086:09a2) offset 0x180 — that is VTD_BAR_CSR!
#
# Register notes:
#   - On Skylake/Cascade Lake, PCIe Root Port offset 0x180 is PERFCTRLSTS_0:
#       bit 7 (0x80) = use_allocating_flow_wr (1 = DDIO allocating writes on, 0 = off)
#       bit 3 (0x08) = nosnoopopwren
#   - On Ice Lake (Intel Doc #735086), bits 7:6 of PERFCTRLSTS_0 were marked Reserved;
#     Intel's public Sapphire Rapids Vol. 2 (ID 814094) omits PCIe Gen5 (8086:352a)
#     IIO registers. Therefore `status` MUST be run first to verify the actual capability
#     map and register contents on 0000:98:01.0 before attempting `off`.
#   - `off` refuses to write if 0x180 collides with any PCIe Extended Capability or if
#     bit 7 is already 0, saves the exact original 32-bit value to /tmp/mrv103_ddio_orig.hex
#     on first disable, clears only bit 7, and verifies hardware readback.
#   - `on` restores the exact saved 32-bit value from /tmp/mrv103_ddio_orig.hex.
#
# Usage:
#   sudo ./bench/host_ddio.sh status
#   sudo ./bench/host_ddio.sh off
#   sudo ./bench/host_ddio.sh on
set -euo pipefail

RP_BDF="0000:98:01.0"
EP_BDF="0000:99:00.0"
EXPECTED_RP_VID="0x8086"
EXPECTED_RP_DID="0x352a"
EXPECTED_EP_VID="0x177d"
EXPECTED_SEC_BUS="153"   # 0x99
REG_OFF="0x180"
BIT_ALLOC_WR_MASK=$((0x80))
BIT_NOSNOOP_MASK=$((0x08))
SAVE_FILE="/tmp/mrv103_ddio_orig.hex"
SOCKET1_CPU="40"
MSR_IIO_LLC_WAYS=$((0xc8b))

die() {
    echo "ERROR: $*" >&2
    exit 1
}

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        die "must be run with sudo/root (Linux hides PCIe config >=0x40 and /dev/cpu/*/msr from non-root)"
    fi
}

verify_topology() {
    local rp_sys="/sys/bus/pci/devices/${RP_BDF}"
    local ep_sys="/sys/bus/pci/devices/${EP_BDF}"
    [[ -d "$rp_sys" ]] || die "Root Port ${RP_BDF} not found in sysfs"
    [[ -d "$ep_sys" ]] || die "Endpoint ${EP_BDF} not found in sysfs"

    local rp_vid rp_did ep_vid sec_bus ep_parent
    rp_vid="$(cat "${rp_sys}/vendor")"
    rp_did="$(cat "${rp_sys}/device")"
    ep_vid="$(cat "${ep_sys}/vendor")"
    sec_bus="$(cat "${rp_sys}/secondary_bus_number")"
    ep_parent="$(basename "$(readlink -f "${ep_sys}/..")")"

    [[ "$rp_vid" == "$EXPECTED_RP_VID" ]] || die "${RP_BDF} vendor=${rp_vid}, expected ${EXPECTED_RP_VID}"
    [[ "$rp_did" == "$EXPECTED_RP_DID" ]] || die "${RP_BDF} device=${rp_did}, expected ${EXPECTED_RP_DID}"
    [[ "$ep_vid" == "$EXPECTED_EP_VID" ]] || die "${EP_BDF} vendor=${ep_vid}, expected ${EXPECTED_EP_VID}"
    [[ "$sec_bus" == "$EXPECTED_SEC_BUS" ]] || die "${RP_BDF} secondary_bus=${sec_bus}, expected ${EXPECTED_SEC_BUS} (0x99)"
    [[ "$ep_parent" == "$RP_BDF" ]] || die "${EP_BDF} parent is ${ep_parent}, expected ${RP_BDF}"

    command -v setpci >/dev/null 2>&1 || die "setpci not found in PATH"
    command -v lspci >/dev/null 2>&1 || die "lspci not found in PATH"
}

read_pci_u32() {
    local bdf="$1"
    local off="$2"
    local raw
    raw="$(setpci -s "$bdf" "${off}.L")"
    printf '%u' "$((16#${raw}))"
}

# Walk PCIe Extended Capability chain (starting at 0x100) and verify REG_OFF (0x180)
# does not collide with any extended capability header or known capability body.
check_ext_caps_and_print() {
    local verbose="${1:-0}"
    python3 - "$RP_BDF" "$((REG_OFF))" "$verbose" <<'PY'
import struct, sys

bdf = sys.argv[1]
target_off = int(sys.argv[2])
verbose = int(sys.argv[3])

with open(f"/sys/bus/pci/devices/{bdf}/config", "rb") as f:
    cfg = f.read()

if len(cfg) < 0x1000:
    print(f"ERROR: config space length is only {len(cfg)} bytes (need 4096)", file=sys.stderr)
    sys.exit(1)

# Standard PCIe Extended Capability minimum sizes (bytes) for overlap checking
KNOWN_SIZES = {
    0x0001: 0x48,  # AER (up to 0x48 with root error status/id)
    0x0002: 0x20,  # VC
    0x0003: 0x0c,  # Device Serial Number
    0x000b: 0x10,  # VSEC (actual length encoded in header+4)
    0x000d: 0x10,  # ACS
    0x001d: 0x28,  # DPC
    0x001f: 0x0c,  # PTM
}

seen = set()
off = 0x100
caps = []
while off and off >= 0x100 and off < len(cfg) and off not in seen:
    seen.add(off)
    hdr = struct.unpack_from("<I", cfg, off)[0]
    if hdr == 0 or hdr == 0xffffffff:
        break
    cap_id = hdr & 0xffff
    ver = (hdr >> 16) & 0xf
    nxt = (hdr >> 20) & 0xffc
    size = KNOWN_SIZES.get(cap_id, 0x10)
    if cap_id == 0x000b:  # VSEC: bits [31:20] of dword at off+4 is length
        vhdr = struct.unpack_from("<I", cfg, off + 4)[0]
        vlen = (vhdr >> 20) & 0xfff
        if vlen >= 8:
            size = vlen
    caps.append((off, cap_id, ver, size, nxt))
    off = nxt

if verbose:
    print("PCIe Extended Capability chain:")
    for o, cid, ver, sz, nxt in caps:
        print(f"  [0x{o:03x}] id=0x{cid:04x} ver={ver} est_end=0x{o+sz-1:03x} next=0x{nxt:03x}")

for o, cid, ver, sz, nxt in caps:
    if o <= target_off < o + sz or o <= target_off + 3 < o + sz:
        print(
            f"ERROR: target offset 0x{target_off:03x} collides with PCIe Ext Cap "
            f"id=0x{cid:04x} at [0x{o:03x}..0x{o+sz-1:03x}]!",
            file=sys.stderr,
        )
        sys.exit(2)
PY
}

read_msr_c8b() {
    python3 - "$SOCKET1_CPU" "$MSR_IIO_LLC_WAYS" <<'PY'
import os, struct, sys

cpu = int(sys.argv[1])
msr = int(sys.argv[2])
path = f"/dev/cpu/{cpu}/msr"
if not os.path.exists(path):
    print(f"MSR 0x{msr:x} (cpu{cpu}): {path} not present (try modprobe msr)")
    sys.exit(0)
try:
    fd = os.open(path, os.O_RDONLY)
    try:
        raw = os.pread(fd, 8, msr)
        val = struct.unpack("<Q", raw)[0]
        ways = bin(val).count("1")
        print(f"MSR 0x{msr:x} (IIO_LLC_WAYS, cpu{cpu}): 0x{val:016x} (set bits={ways})")
    finally:
        os.close(fd)
except OSError as e:
    print(f"MSR 0x{msr:x} (cpu{cpu}): read error: {e}")
PY
}

cmd_status() {
    verify_topology
    echo "=== Topology verified: ${RP_BDF} (8086:352a) -> ${EP_BDF} (177d:b900) ==="
    lspci -vvv -s "${RP_BDF}" | grep -E 'PCI bridge|Region 0|Capabilities:|DevCtl:|LnkSta:' || true
    echo ""
    check_ext_caps_and_print 1 || true
    echo ""
    echo "=== ${RP_BDF} config space 0x160..0x19c (32-bit dwords) ==="
    local off val
    for off in 0x160 0x164 0x168 0x16c 0x170 0x174 0x178 0x17c 0x180 0x184 0x188 0x18c 0x190 0x194 0x198 0x19c; do
        val="$(read_pci_u32 "$RP_BDF" "$off")"
        printf '  %s = 0x%08x' "$off" "$val"
        if [[ "$off" == "$REG_OFF" ]]; then
            local b7=$(( (val & BIT_ALLOC_WR_MASK) ? 1 : 0 ))
            local b3=$(( (val & BIT_NOSNOOP_MASK) ? 1 : 0 ))
            printf '   <-- bit7(use_allocating_flow_wr)=%d, bit3(nosnoopopwren)=%d' "$b7" "$b3"
        fi
        printf '\n'
    done
    echo ""
    read_msr_c8b
    echo ""
    if [[ -f "$SAVE_FILE" ]]; then
        echo "Saved original value (${SAVE_FILE}): $(cat "$SAVE_FILE")"
    else
        echo "Saved original value (${SAVE_FILE}): <none>"
    fi
}

cmd_off() {
    verify_topology
    check_ext_caps_and_print 0

    local orig b7 b3
    orig="$(read_pci_u32 "$RP_BDF" "$REG_OFF")"
    b7=$(( (orig & BIT_ALLOC_WR_MASK) ? 1 : 0 ))
    b3=$(( (orig & BIT_NOSNOOP_MASK) ? 1 : 0 ))
    printf 'Current %s %s = 0x%08x (bit7=%d, bit3=%d)\n' "$RP_BDF" "$REG_OFF" "$orig" "$b7" "$b3"

    if [[ "$b7" -eq 0 ]]; then
        die "Refusing to write: bit 7 of ${RP_BDF} ${REG_OFF} is ALREADY 0 (either DDIO is already disabled, or bit 7 is reserved/unused on Sapphire Rapids 8086:352a — do NOT force writes without datasheet confirmation)."
    fi

    if [[ ! -f "$SAVE_FILE" ]]; then
        printf '0x%08x\n' "$orig" > "$SAVE_FILE"
        chmod 0644 "$SAVE_FILE"
        echo "Saved original value 0x$(printf '%08x' "$orig") to ${SAVE_FILE}"
    else
        echo "Keeping existing saved original value in ${SAVE_FILE}: $(cat "$SAVE_FILE")"
    fi

    local target after
    target=$(( orig & ~BIT_ALLOC_WR_MASK ))
    setpci -s "$RP_BDF" "${REG_OFF}.L=$(printf '%08x' "$target")"
    after="$(read_pci_u32 "$RP_BDF" "$REG_OFF")"

    printf '%s %s: 0x%08x -> target 0x%08x -> readback 0x%08x\n' \
        "$RP_BDF" "$REG_OFF" "$orig" "$target" "$after"

    if [[ "$after" -ne "$target" ]]; then
        echo "WARNING: readback (0x$(printf '%08x' "$after")) != target (0x$(printf '%08x' "$target"))!" >&2
        echo "         Hardware masked/ignored the write (bit 7 may be RO/BIOS-locked on Sapphire Rapids)." >&2
        exit 3
    fi
    echo "OK: bit 7 cleared on ${RP_BDF} ${REG_OFF}. Restore with: sudo $0 on"
}

cmd_on() {
    verify_topology
    check_ext_caps_and_print 0

    [[ -f "$SAVE_FILE" ]] || die "No saved original value at ${SAVE_FILE}; refusing to guess a hardcoded bitmask."

    local saved_hex saved cur after
    saved_hex="$(tr -d '[:space:]' < "$SAVE_FILE")"
    [[ "$saved_hex" =~ ^0x[0-9a-fA-F]{8}$ ]] || die "Invalid saved hex in ${SAVE_FILE}: ${saved_hex}"
    saved=$((saved_hex))

    cur="$(read_pci_u32 "$RP_BDF" "$REG_OFF")"
    setpci -s "$RP_BDF" "${REG_OFF}.L=$(printf '%08x' "$saved")"
    after="$(read_pci_u32 "$RP_BDF" "$REG_OFF")"

    printf '%s %s: 0x%08x -> restore 0x%08x -> readback 0x%08x\n' \
        "$RP_BDF" "$REG_OFF" "$cur" "$saved" "$after"

    if [[ "$after" -ne "$saved" ]]; then
        die "Restore verification FAILED (readback=0x$(printf '%08x' "$after"), expected ${saved_hex})! Keeping ${SAVE_FILE}."
    fi
    rm -f "$SAVE_FILE"
    echo "OK: restored ${RP_BDF} ${REG_OFF} to exact original value ${saved_hex} and removed ${SAVE_FILE}."
}

require_root
case "${1:-}" in
    status) cmd_status ;;
    off)    cmd_off ;;
    on)     cmd_on ;;
    *)
        echo "Usage: sudo $0 {status|off|on}" >&2
        exit 2
        ;;
esac
