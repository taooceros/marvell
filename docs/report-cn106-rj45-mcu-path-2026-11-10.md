# Host ⇄ CN106 networking over the board RJ45 port (via the MCU)

Date: 2026-11-10
Host: saturn (Intel I350 `enp23s0f1` cabled to the card's RJ45 port)
Card: Marvell CN106 SmartNIC (crb106-pcie r4p1, SDK12.25.10)

## Result

| Link | Status |
|---|---|
| Host ⇄ MCU | Works |
| Card Linux ⇄ MCU | Works at 10 Mb/s (pings both ways; 1000-byte pings 20/20, ~3 ms) |
| **Host ⇄ card Linux** | **Does not work.** The MCU firmware cannot forward traffic between its two ports |

This path cannot give host-to-card access on the current MCU firmware. The PCIe path (separate report) now provides that access.

## How the path is wired

```
Host I350 ──RJ45── 88E1512 PHY ── MCU port 1 (1G)
                                   MCU (NXP RT1176, Zephyr, FW 1.6.2)
                                   MCU port 2 (10/100) ── 88E1111 PHY ──SGMII── CN106 eth4
```

The MCU has two separate Ethernet ports. It sits in the middle, so traffic from the host to the card must pass *through* the MCU firmware.

## What we found

**1. The card ⇄ MCU link was down because of an autonegotiation mismatch.**

- The CN106 side waits for the SGMII link handshake (autonegotiation). The 88E1111 PHY between the card and the MCU has that handshake disabled, so it never answers.
- `ethtool autoneg off` is ignored by this firmware version (SDK12.25.10).
- Forcing the link on in the CN106 registers is also not enough. A loopback test showed that the card firmware (ATF) blocks all transmit until the handshake completes.

**2. Fix for card ⇄ MCU.**

- Enabled the handshake on the 88E1111, writing to it through the MCU (this needed a temporary MCU pin remap to reach the PHY's management bus).
- Set the CN106 side to advertise 10 Mb/s full duplex, link up.
- The link came up, and the card firmware itself reported `eth4 NIC Link is UP`. Pings work in both directions.
- 100 Mb/s did not work: the MCU received only CRC-error frames.

**3. The MCU never forwards.**

- Live tests:
  - The host's ARP and IPv6 neighbor lookups for the card all failed.
  - With routed IPv6 subnets configured on all three devices, the host reached both MCU addresses but not the card.
  - The MCU counted those packets as dropped, with zero forwarded.
- Firmware analysis: both MCU firmware images were dumped (read-only) and examined. Neither contains a bridge, IP forwarding, or any relay.

**4. No newer MCU firmware is available publicly.**

- Marvell's public download portal lists only CN106/CN103 firmware, not MCU firmware.
- No MCU source code is published (upstream Zephyr, Marvell GitHub).

## Options

| Option | Effort | Risk | Notes |
|---|---|---|---|
| Ask Marvell FAE for a bridging MCU firmware | Waiting time | Lowest | The official route |
| Use PCIe instead | Done | — | Already working, and much faster |

**Recommendation:** use PCIe for day-to-day access. Pursue the RJ45 path only if out-of-band management is needed, e.g. to reach the card when its Linux or the PCIe link is down.

## Current state

Every setting from this work was temporary.

- The card-side link settings and the `eth4` addresses were lost when the card rebooted during the PCIe work.
- The 88E1111 change lasts until a host power cycle.
- The MCU and host test addresses are lost on their next reset.

To reproduce card ⇄ MCU, run the recipe in the skill notes (`marvell-octeon-dpu-serial-access`, section "WORKING RECIPE").

## Incidents

- Two recoverable hardware errors (SError) occurred on the card from my register reads: one used the wrong access width, the other was a register sweep. There was no lasting effect.
- Earlier in the work, the MCU halted twice: once from a known-bad shell command (`env list`), once from characters dropped on its serial console. Each time, a host power cycle recovered it.
- Since then, MCU input goes through a slow, echo-verified typing helper.

Full detail: `report-cn106-mgmt-path-2026-11-10.md` (bilingual).
