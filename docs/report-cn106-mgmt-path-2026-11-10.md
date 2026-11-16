# CN106 管理口（经单片机）调试报告 / CN106 Management Path (via MCU) Investigation Report

日期 / Date: 2026-11-10
主机 / Host: saturn (Supermicro SYS-621H-TN12R, Ubuntu 22.04, kernel 6.17.7)
板卡 / Card: Marvell EV-PCIE-CN106-2P100, crb106-pcie r4p1, MV-CN10624-B0-AAP, PCIe 99:00.0

---

## 1. 结论摘要 / Executive summary

**中文**

- 目标是不改硬件，让主机经 RJ45 → 单片机 → 卡内 Linux 连通（最终 `ssh root@192.168.3.41`）。
- **卡 ↔ 单片机**：已打通，两个方向都能 ping，1000 字节的包 20/20，往返约 3 ms，速率 10M。用的是运行时设置，重启后失效。
- **主机 ↔ 单片机**：通。
- **主机 ↔ 卡**：不通。根因是单片机固件 1.6.2 **根本没有转发能力**：没有二层桥接，没有 IPv6 路由转发，也没有应用层中转。这一点已由实测和固件静态分析双重确认。
- 公开渠道（Marvell 公开下载门户、Marvell GitHub、Zephyr 上游）都**找不到**更新的单片机固件或源码。

**English**

- Goal: without hardware changes, reach card Linux from the host via RJ45 → MCU → card (ultimately `ssh root@192.168.3.41`).
- **Card ↔ MCU**: working. Ping succeeds in both directions, 1000-byte pings 20/20, ~3 ms RTT, link at 10M. It relies on runtime settings that are lost on reboot.
- **Host ↔ MCU**: working.
- **Host ↔ card**: not working. Root cause: MCU firmware 1.6.2 has **no forwarding capability at all**: no L2 bridge, no IPv6 routing/forwarding, and no application-level relay. Both live tests and static firmware analysis confirm this.
- Public sources (Marvell's public download portal, Marvell GitHub, upstream Zephyr) contain **no** newer MCU firmware and no source.

---

## 2. 拓扑与版本 / Topology and versions

```
Host enp23s0f1 (I350, 1G)
   │  RJ45 cable
88E1512 (MDIO addr 1)  ─────────  MCU NXP RT1176 (Zephyr)  ENET_1G  = MCU iface 1
                                   │  (no forwarding in FW 1.6.2)
                                  ENET 10/100 ─────── 88E1111 (MDIO addr 0)  = MCU iface 2
                                                        │  SGMII
                                  CN106 GSERM2.L0 / PORTM5 / RPM2 LMAC0  →  Linux eth4
```

| 组件 / Component | 版本 / Version |
|---|---|
| CN106 firmware (SPI0 and SPI1) | SDK12.25.10 (EBF 12.25.10, ATF BL31 v2.8 Marvell-12.25.10, U-Boot 2023.01-12.25.10), boots from SPI0_CS0 |
| Card Linux | 6.6.46-ge93307798e13 (built on mercury) |
| EBF port layout | PORTM0-3 25GAUI_C2M, PORTM4 DISABLED, PORTM5 GSERM2.L0 SGMII |
| MCU firmware | 1.6.2-7c10f02, board `cn103_pcie_crb/mimxrt1176/cm7`, Zephyr 3.6.99 |
| 88E1111 | ID 0x0141/0x0CC2, HWCFG_MODE 0xE |

地址 / Addresses: MCU iface1 `fe80::4260:5aff:fe02:ed6e` (MAC 40:60:5A:02:ED:6E); MCU iface2 `fe80::4:9fff:fe48:838a` (MAC 02:04:9F:48:83:8A); card eth4 MAC `de:c6:5f:e7:85:35` (random each boot), `fe80::dcc6:5fff:fee7:8535`.

---

## 3. 调查过程与证据 / Findings and evidence

### 3.1 卡侧 SGMII 自动协商 / CN106-side SGMII autonegotiation

**中文**：CN106 的 SGMII PCS 默认开启协商（CONTROL `0x1140`，IF_MODE `0x0003`），但对端 88E1111 从不回应（PARTNER_ABILITY 一直是 `0x0000`）。`ethtool ... autoneg off` 无效：12.25.10 的 ATF 对这个端口忽略关闭协商的请求。直接改 PCS 寄存器强制 100M、关闭协商后，链路同步（STATUS `0x000d`），**单片机 → 卡**方向能收到完好的包。

**English**: The CN106 SGMII PCS autonegotiates by default (CONTROL `0x1140`, IF_MODE `0x0003`), but the 88E1111 never answers (PARTNER_ABILITY stays `0x0000`). `ethtool ... autoneg off` has no effect: the 12.25.10 ATF ignores AN-off for this port. Forcing the PCS directly (100M, AN off) achieves sync (STATUS `0x000d`), and **MCU → card** frames arrive intact.

### 3.2 ATF 的"发送闸门" / The ATF transmit gate

**中文**：用 SerDes 近端环回实验证明，**ATF 只在 PCS 报告协商完成时才放行发送**：
- 环回中自协商完成 → ATF 报 `Link is UP`，自己发的包能绕回来。
- 关闭协商 → MAC 计数显示"已发送"，但实际一个包也出不去。
- 协商完成后再关闭协商 → 约 0.4 秒内包仍能发出（3 个包里 2 个绕回），等 ATF 下一次轮询报 `Link is DOWN` 后被拦住。

所以光强制 PCS 不够，必须让协商真正完成。为了让 Linux 认为有链路，我写了 `fake_link.py`，模拟 ATF 的链路事件，只骗过 Linux，不影响 ATF。

**English**: SerDes near-end loopback experiments prove that **the ATF only allows transmission when the PCS reports AN complete**:
- Self-negotiation completes in loopback → ATF reports `Link is UP` and the card's own frames loop back.
- AN disabled → the MAC counts frames as transmitted, but nothing actually leaves.
- AN disabled after completion → frames still pass for ~0.4 s (2 of 3 looped back), then the ATF polls, reports `Link is DOWN`, and blocks them.

So forcing the PCS is not enough; AN must really complete. To give Linux a carrier, I wrote `fake_link.py`, which emulates the ATF link event; it fools Linux only, not the ATF.

### 3.3 88E1111 工作正常，只是没开协商 / The 88E1111 works; its AN is just disabled

**中文**：
- 短暂打开 ATF 闸门后往单片机发包：协商出的速率是 10M 时，单片机收到 19 个 CRC 错误包；速率是 100M 时，收到 2 个完全正确的包。说明 88E1111 两个方向都能转发。
- 88E1111 的管理线接在单片机 10/100 网口的 MDIO 引脚（GPIO_AD_32/33）上。我把这两个引脚临时切换到单片机 1G 网口的 MDIO 控制器上（IOMUXC `0x400E818C`/`0x400E8190` 改成 9，`0x400E84C8` 改成 3，用完还原），读到了 88E1111 的寄存器：
  - SerDes 页寄存器 0 = `0x01C0`：协商关闭。
  - 寄存器 17 = `0x4C10`：已连接，100M，半双工。
  - 寄存器 27 = `0x848E`：模式码 `0xE`。
- **更正**：之前认为单片机 10/100 网口的 MDIO 卡死了，这是错的。实际上单片机固件一直在定期读 88E1111，每次读完最后一步都写寄存器 22 = 0，所以 MDIO 命令寄存器看起来总是停在 `0x505a0000`。

**English**:
- Sending to the MCU while the ATF gate was briefly open: with the negotiated speed at 10M, the MCU received 19 CRC-error frames; at 100M it received 2 perfect frames. So the 88E1111 forwards in both directions.
- The 88E1111's management lines sit on the MCU 10/100 port's MDIO pins (GPIO_AD_32/33). I temporarily remuxed those two pins to the MCU's 1G MDIO controller (IOMUXC `0x400E818C`/`0x400E8190` set to 9, `0x400E84C8` set to 3, restored afterwards) and read the 88E1111 registers:
  - SerDes-page reg 0 = `0x01C0`: AN disabled.
  - reg 17 = `0x4C10`: link up, 100M, half duplex.
  - reg 27 = `0x848E`: mode code `0xE`.
- **Correction**: my earlier claim that the MCU 10/100 MDIO was hung was wrong. The MCU firmware polls the 88E1111 periodically and ends every poll by writing reg 22 = 0, so the MDIO command register always appears to read `0x505a0000`.

### 3.4 打通卡 ↔ 单片机 / Making card ↔ MCU work

**中文**：
1. 88E1111：在第 1 页（`reg22=1`）写寄存器 0 = `0x13C0`（开协商并重启协商），回读为 `0x11C0`。
2. CN106 PCS：DEV_ABILITY = `0x9001`（已连接、全双工、10M），IF_MODE = `0x0003`，CONTROL = `0x1340`。

结果：STATUS `0x002d`，PARTNER `0x4001`；ATF 自己报 `eth4 NIC Link is UP`，不再需要假链路；单片机报 `eth_nxp_enet_mac: Link is up`。
- 卡 → 单片机 5/5；1000 字节的包 20/20，往返 2.4/3.2/4.1 ms。
- 单片机 → 卡 3/3。
- 能力字里必须带"已连接"位（bit 15），否则单片机会把 iface 2 关掉（ping 报 `-5`）。
- 试过 100M（`0xD401` 配合 `0x0005`）：卡能收，但卡发出的包按协商结果用 10M 节奏发送，单片机收到的全是 CRC 错误。所以暂时停在 10M。

**English**:
1. 88E1111: on page 1 (`reg22=1`), write reg 0 = `0x13C0` (enable and restart AN); reads back `0x11C0`.
2. CN106 PCS: DEV_ABILITY = `0x9001` (link up, full duplex, 10M), IF_MODE = `0x0003`, CONTROL = `0x1340`.

Result: STATUS `0x002d`, PARTNER `0x4001`; the ATF itself reports `eth4 NIC Link is UP`, so no fake link is needed; the MCU logs `eth_nxp_enet_mac: Link is up`.
- Card → MCU 5/5; 1000-byte pings 20/20, RTT 2.4/3.2/4.1 ms.
- MCU → card 3/3.
- The link bit (bit 15) must be set in the ability word, otherwise the MCU takes iface 2 down (ping fails with `-5`).
- A 100M attempt (`0xD401` with `0x0005`) failed: the card receives fine, but its TX follows the negotiated 10M timing, so the MCU sees only CRC errors. The link therefore stays at 10M for now.

### 3.5 单片机不转发 / The MCU does not forward

**中文**：
- 实测：
  - 主机 ping 单片机 3/3。
  - 主机对卡的 ARP 和 IPv6 邻居解析全部 FAILED，卡侧没收到任何包。
  - 另配 IPv6 子网 `fd00:1::/64`、`fd00:2::/64` 让单片机做路由：主机能 ping 到单片机两侧地址，但到卡 `fd00:2::41` 是 0/5。单片机把这些包计为 iface 1 "IPv6 drop"，`forwarded` 一直是 0，哪怕它的邻居表里卡的地址正处于 `reachable` 状态。
- 静态分析：经串口只读读出单片机两个镜像槽的固件。运行中那份经 SHA256 校验，另一份读取完整、但没完成校验。两份都没有 `net_eth_bridge` 模块、没有 `net bridge` 命令、没有开启 IPv6 转发，也没有应用层中转。两个网口都没开混杂模式。应用模块只有：`mrvl_ap`（CN106 上电/启动设备/升级）、`mrvl_redfish`、`mrvl_rest_api`、`mrvl_http_server`、`rmcp`（只回 ASF ping）等。

**English**:
- Live tests:
  - Host pings the MCU 3/3.
  - The host's ARP and IPv6 neighbor resolution for the card all FAILED, and the card received nothing.
  - With extra IPv6 subnets `fd00:1::/64` and `fd00:2::/64` so the MCU could route: the host reaches both MCU addresses, but the card `fd00:2::41` gets 0/5. The MCU counts those packets as iface-1 "IPv6 drop" with `forwarded` = 0, even while its neighbor table holds the card's address in the `reachable` state.
- Static analysis: both MCU image slots were dumped read-only over serial. The running image's SHA256 verified; the other slot was read completely but not verified. Neither contains a `net_eth_bridge` module, a `net bridge` command, enabled IPv6 forwarding, or any application relay. Neither Ethernet port is in promiscuous mode. The app modules are only `mrvl_ap` (CN106 power, boot source, updates), `mrvl_redfish`, `mrvl_rest_api`, `mrvl_http_server`, `rmcp` (ASF ping only) and similar.

### 3.6 固件来源 / Firmware sources

**中文**：
- **Marvell 公开下载门户**：脚本直接访问会被 CDN 拦截，用无头 Chrome 可以。列出了 CN106 FW 从 SDK12.24.07 到 SDK13.26.10 共 28 个版本，CN103 共 26 个，**没有任何单片机固件**。最新的 SDK13.26.10 我已下载核对过。
- **12.25.02 与 12.25.10 对比**：ATF 里负责"SGMII 关闭协商/速率/双工"的函数在两版中逻辑相同。有变化的是 ECP 固件（12.25.02 → 12.25.07）和命令处理函数（3095 → 3156 条指令）。仅凭二进制无法断定降级能解决问题。
- **Zephyr 上游、Marvell GitHub**：都没有这块板的单片机固件源码或板级配置。

**English**:
- **Marvell public download portal**: scripted access is blocked by the CDN, but headless Chrome works. It lists 28 CN106 FW versions (SDK12.24.07 to SDK13.26.10) and 26 CN103 versions, and **no MCU firmware at all**. I downloaded and checked the newest, SDK13.26.10.
- **12.25.02 vs 12.25.10**: the ATF function that handles SGMII AN-off, speed and duplex has identical logic in both. What changed is the ECP firmware (12.25.02 → 12.25.07) and the command handler (3095 → 3156 instructions). The binaries alone cannot show whether a downgrade would help.
- **Upstream Zephyr and Marvell GitHub**: neither has this board's MCU firmware source or board configuration.

---

## 4. 当前状态（均为临时）/ Current state (all temporary)

| 位置 / Where | 设置 / Setting | 何时失效 / Lost on |
|---|---|---|
| 88E1111 | 第 1 页寄存器 0 = `0x11C0`（SerDes 协商开启）/ page-1 reg0 = `0x11C0` (SerDes AN on) | 主机断电重启 / host power cycle |
| CN106 PCS | DEV_ABILITY `0x9001`, IF_MODE `0x0003` | 卡重启或 ATF 重新初始化链路 / card reboot or ATF link re-init |
| Card eth4 | `192.168.3.41/24`, `fd00:2::41/64`, route `fd00:1::/64 via fd00:2::2` | 卡重启 / card reboot |
| MCU | `fd00:1::2` (iface 1), `fd00:2::2` (iface 2)；10/100 口的硬件统计计数已打开 / 10/100 MIB counters enabled | 单片机重启 / MCU reset |
| Host enp23s0f1 | `192.168.3.1/24`, `fd00:1::1/64`, route `fd00:2::/64 via fd00:1::2` | 主机重启 / host reboot |

**中文**：执行过程中卡上出现过两次"可恢复硬件错误"（SError），都由我用 /dev/mem 读寄存器引起：一次是访问宽度用错，一次是一批寄存器读取。卡没有挂，也没有后续影响。本次会话中单片机没有出过问题。

**English**: Two recoverable hardware errors (SError) occurred on the card during this work, both caused by my /dev/mem register reads: once from the wrong access width, once from a batch of register reads. The card did not crash and there was no lasting effect. The MCU had no problems during this session.

---

## 5. 可选方案 / Options

| 方案 / Option | 内容 / What | 优点 / Pros | 风险与代价 / Risks and cost |
|---|---|---|---|
| A. 向 Marvell FAE 要固件 / Ask Marvell FAE | 要支持桥接的单片机固件，或启用方法 / Request an MCU firmware that bridges, or how to enable it | 正规、风险最低 / Official, lowest risk | 时间不确定 / Unknown turnaround |
| B. PCIe（octeon_ep）| 主机编译 `octeon_ep` 模块（内核源码树已就绪），卡上运行 `octep_cp_agent` / Build `octeon_ep` for the host (kernel tree is ready) and run `octep_cp_agent` on the card | 不动硬件，速度远高于管理口 / No hardware change; far faster than the management port | 主机需要 sudo 加载模块 / Needs sudo on the host to load the module |

**建议 / Recommendation**：
- **中文**：A 与 B 并行。要尽快通，先做 B。
- **English**: Run A in parallel with B. For the quickest result, do B first.

---

## 6. 附录 / Appendix

### 6.1 复现步骤（卡 ↔ 单片机）/ Reproduction steps (card ↔ MCU)

1. 单片机引脚切换 / MCU pin remux：`devmem 0x400e818c 32 0x9`; `devmem 0x400e8190 32 0x9`; `devmem 0x400e84c8 32 0x3`。
2. 写 88E1111（经 1G 控制器的 MDIO 命令寄存器 `0x40420040`，写操作字 = `0x50020000 | phy<<23 | reg<<18 | data`，之后写 `0x800000` 到 `0x40420004` 清标志）/ Write 88E1111 via the ENET_1G MMFR `0x40420040` (write word = `0x50020000 | phy<<23 | reg<<18 | data`, then W1C `0x800000` to `0x40420004`)：`reg22=1`, `reg0=0x13C0`, `reg22=0`。
3. 按相反顺序还原三个寄存器 / Restore the three registers in reverse order (3, 3, 1)。
4. CN106（RPM2 BAR0 `0x87e0e2000000`，LPCS 位于 +0x30000，16 位寄存器、8 字节间隔，只能 64 位访问）/ CN106 (RPM2 BAR0 `0x87e0e2000000`, LPCS at +0x30000, 16-bit registers on an 8-byte stride, 64-bit access only)：r4 = `0x9001`, r0x14 = `0x0003`, r0 = `0x1340`。

### 6.2 文件 / Files

- `~/.local/share/dpu-tools/`:
  - `mcu_fw_1.6.2_image0.bin`, `mcu_fw_1.6.2_image1.bin`, `mcu_mcuboot_partition.bin`
  - `cdp_fetch.py`（门户下载 / portal downloader）, `mcu_dump_parse.py`
  - `zj*.py`, `mcu_mdio.py`
- `~/.cache/marvell-fw/CN106_FW_SDK13.26.10.tgz`；12.25.02 / 12.25.10 在 / are in `~/.cache/tern/attachments/`。
- 卡上脚本 / Card scripts `/root/`:
  - 寄存器查看与强制设置 / register inspection and forcing: `lpcs.py`, `lpcs_force.py`, `lpcs_phyrole.py`
  - 假链路事件 / fake link event: `fake_link.py`
  - 测试 / tests: `rx_test.py`, `lb_test2.py`, `window_test.py`
  - MDIO 读取 / MDIO reads: `smi.py`, `smi_slow.py`
- 技能记录 / Skill notes: `~/.omp/agent/managed-skills/marvell-octeon-dpu-serial-access/SKILL.md`。
