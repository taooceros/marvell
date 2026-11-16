# Host ⇄ CN106 networking over PCIe (octeon_ep)

Date: 2026-11-10
Host: saturn (kernel 6.17.7)
Card: Marvell CN106 SmartNIC (crb106-pcie r4p1, SDK12.25.10), host PCIe `99:00.0`

## Result

The host can now reach the card's Linux directly over PCIe. No cabling or MCU is involved.

| | |
|---|---|
| Host interface | `ens1` = 10.10.0.1/24 (Marvell `octeon_ep` driver) |
| Card interface | `sdp1-0` = 10.10.0.2/24 |
| Latency | ~0.2 ms ping; full-size (1472 B) pings pass |
| Throughput | ~340 MB/s through ssh (limited by ssh encryption, not the link) |
| Login | `ssh root@10.10.0.2` (saturn's key is installed on the card) |

## Why it failed before

The error was `octep_ctrl_mbox : Invalid magic number ffffffffffffffff`. At startup the host driver reads a "handshake" block that the card's control agent (`octep_cp_agent`) places in a memory window the host can see (BAR4). There were two independent problems. Both must be fixed.

**1. The card refused host access to its memory.** This produces the `ffffffffffffffff` error.

- In the card firmware setup menu (EBF), *PCIe EP Security → Allow PCIe Host to Access Octeon Memory* was `0`, which is the factory default.
- Each host read was blocked by the card's IOMMU. At the exact moment of the host's read, the card logged `arm-smmu-v3: event 0x02` (C_BAD_STREAMID, StreamID 0x3fd20).
- Setting only the stream-ID list is not enough. The "Allow" switch must also be 1.

**2. A preloaded kernel module points the window at the wrong memory.** This produces `Invalid magic number 0`.

- The card auto-loads `pcie_marvell_cnxk_ep`. It redirects window slot 7 to an empty page of its own.
- The shipped agent writes the handshake where the firmware's original mapping expects it. As a result, the host reads zeros.
- Marvell documents the fix only in DAO's kernel guide: `modprobe.blacklist=pcie_marvell_cnxk_ep` "is required only on cn10k platforms" (DAO commit `f5ee09e`, 2025-06-27). The SDK's own PCIe EP README still says to load the module.

We confirmed both causes with a controlled A/B test. In both runs, EBF access was allowed and the agent was running:

| Window slot 7 | Host result |
|---|---|
| Left as set by the module (`0x4611`) | `Invalid magic number 0` |
| Restored to firmware value (`0x2fef1`) | Works, `ens1` comes up |

## What was changed

**Card firmware (EBF, saved, persistent):**

- *PCIe EP Security → Allow PCIe Host to Access Octeon Memory* = **1**
- *Host Stream IDs for PEM0* = **0x3ffff** (allow all; was 0x30000)

**Card, persistent:** `/etc/modprobe.d/blacklist-pcie-marvell-cnxk-ep.conf` (`blacklist pcie_marvell_cnxk_ep`; copy in `card-scripts/`). With the module gone, window slot 7 keeps the firmware value. The card has no initramfs, so this file is enough.

**Card, at every boot:** `/root/octep_up.sh` (copy in `card-scripts/`). It does three things:

1. Checks window slot 7 and restores the firmware value only if something changed it (it warns if the module is loaded).
2. Hands the PEM and two SDP devices to `vfio-pci`. The shipped agent is the VFIO build and needs this.
3. Starts the agent:

   ```
   octep_cp_agent /usr/bin/cn106xx.cfg -- --sdp_rvu_pf 0002:18:00.0,0002:19:00.0 --pem_dev 0001:00:10.0
   ```

Then the card interface is configured: `ip link set sdp1-0 up; ip addr add 10.10.0.2/24 dev sdp1-0`.

**Host:** the driver from Marvell's `pcie_ep_octeon_host` repo builds cleanly for 6.17.7, although its README says it supports kernels only up to 6.6. Load it after the card agent is running:

```
sudo insmod ~/dev/marvell/pcie_ep_octeon_host/drivers/octeon_ep/octeon_ep.ko
sudo ip link set ens1 up; sudo ip addr add 10.10.0.1/24 dev ens1
```

## Things to know

- **`rmmod octeon_ep` reboots the whole card** once the driver is up. This is the driver's design: on unload it triggers a chip reset. Host BAR addresses survive the reset.
- **After a failed attempt, the next load hangs** on `Firmware ready status = 2`. The failed driver leaves that status behind. Restart the agent on the card (`pkill -INT -x octep_cp_agent; /root/octep_up.sh`), and the waiting driver continues on its own.
- **Order matters:** start the card agent first, then load the host driver.
- **The host MAC is `00:00:00:01:01:00`**, because the PF MAC in `cn106xx.cfg` has only 5 bytes. It is harmless, and you can fix it in the config.
- **Incident:** a register-dump mistake on my side caused 14 recoverable hardware errors (SEI-UER) on the card. There was no lasting effect. Card register access now goes through a single-register helper (`/root/bar4idx7.py`).

## Not yet persistent

- Card: the agent and the `sdp1-0` address must be redone after every card boot.
- Host: the module and the `ens1` address must be redone after every host reboot.

Suggested next step: a systemd unit on the card (run `octep_up.sh` and configure `sdp1-0`), and on the host, module install plus a static address for `ens1`.
