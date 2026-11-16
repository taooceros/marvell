#!/usr/bin/env python3
"""Throwaway: clause-22 MDIO READ on the MCU's ENET_1G MDIO bus (RT1176, base 0x40420000)
through the Zephyr shell `devmem`, typed via zj_mcu.py (echo-verified).

usage: mcu_mdio.py PANE PHY REG [REG ...]
For each reg: write MMFR (0x40420040) = ST01|OP10(read)|PA|RA|TA10, read MMFR, clear EIR.MII (W1C 0x00800000).
Read-only towards the PHY. Prints 'phyP regR = 0xVVVV'.
"""
import subprocess, sys

pane, phy, regs = sys.argv[1], int(sys.argv[2], 0), [int(r, 0) for r in sys.argv[3:]]
MMFR, EIR = "0x40420040", "0x40420004"


def mcu(cmd):
    r = subprocess.run(["python3", "/tmp/zj_mcu.py", pane, cmd, "6"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"aborted: {r.stdout.strip()}")
    return r.stdout


for reg in regs:
    word = 0x60020000 | (phy << 23) | (reg << 18)
    mcu(f"devmem {MMFR} 32 0x{word:08X}")
    out = mcu(f"devmem {MMFR} 32")
    mcu(f"devmem {EIR} 32 0x800000")
    val = [l for l in out.splitlines() if "Read value" in l]
    raw = int(val[-1].split()[-1], 16) if val else None
    if raw is None or (raw >> 16) != (word >> 16):
        print(f"phy{phy} reg{reg}: unexpected MMFR {hex(raw) if raw is not None else '?'} (another transaction raced?)")
    else:
        print(f"phy{phy} reg{reg} = 0x{raw & 0xffff:04x}")
