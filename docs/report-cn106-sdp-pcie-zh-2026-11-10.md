# 主机 ⇄ CN106 经 PCIe（SDP）联网报告

日期：2026-11-10
主机：saturn（内核 6.17.7）
板卡：Marvell CN106 SmartNIC（crb106-pcie r4p1，固件 SDK12.25.10），主机 PCIe 地址 `99:00.0`

## 结果

主机现在可以经 PCIe 直接访问卡上的 Linux，不经过网线，也不经过单片机。

| 项目 | 情况 |
|---|---|
| 主机网口 | `ens1` = 10.10.0.1/24（Marvell `octeon_ep` 驱动） |
| 卡上网口 | `sdp1-0` = 10.10.0.2/24 |
| 延迟 | ping 约 0.2 ms；1472 字节、禁止分片的包能通 |
| 吞吐 | 经 ssh 约 340 MB/s。这个数字受 ssh 加密速度限制，不代表链路上限 |
| 登录 | `ssh root@10.10.0.2`，saturn 的公钥已加到卡上 |

## SDP 是什么

SDP 是 CN106 芯片里专门在"主机"和"卡内网络引擎"之间搬运数据包的硬件模块。卡插在主机的 PCIe 槽里，在主机看来，它就是一块网卡。

```
主机                             PCIe                       CN106 卡
octeon_ep 驱动 (ens1) ──── 99:00.0 (PEM0, EP 模式) ──── SDP ── 网络引擎 NIX ── sdp1-0 (卡上 Linux)
                                                        │
                                       控制程序 octep_cp_agent（卡上，用户态）
```

整条路分两部分：

- **数据通道**：主机驱动通过 BAR0 设置收发队列，数据包由 SDP 和主机内存之间直接搬运。卡上 Linux 看到的对应网口是 `sdp1-0`。
- **控制通道**：主机驱动和卡上的控制程序 `octep_cp_agent` 之间有一个"信箱"，放在主机能看到的 BAR4 窗口里，用来查询链路状态、MAC、MTU 等信息。控制程序还负责在 PCIe 配置空间里置"固件就绪"标志。主机驱动先等这个标志变成 1，再去读信箱。

这次涉及的卡上 PCI 设备：

| 设备 | 作用 |
|---|---|
| `0001:00:10.0` | PEM0，即 CN106 的 PCIe 控制器（工作在 EP 模式） |
| `0002:18:00.0`、`0002:19:00.0` | SDP 的两个 PF。信箱就放在 PF0 的 BAR4 里 |
| `0002:01:00.1` 起 | SDP 的 VF，卡上内核为它们建了网口 `sdp1-0` ~ `sdp1-8` |

主机侧的 BAR4 共 64 MiB，分成 16 个 4 MiB 的窗口，每个窗口指向卡内存的哪里，由 PEM0 的 `BAR4_INDEX` 寄存器决定。信箱在第 7 个窗口。

## 之前为什么失败

报错是 `octep_ctrl_mbox : Invalid magic number ffffffffffffffff`，意思是主机去读信箱开头的魔数，读到的不对。原因有两个，彼此独立，两个都要解决。

**1. 卡不允许主机访问它的内存（报错为全 F）**

- 卡的固件设置菜单（EBF）里，`PCIe EP Security → Allow PCIe Host to Access Octeon Memory` 是 0，即出厂默认的"不允许"。
- 主机每次读信箱，都被卡上的 IOMMU（SMMU）拦下，所以主机读到全 F。主机读取的同一时刻，卡的日志里多出一条 `arm-smmu-v3: event 0x02`（C_BAD_STREAMID，StreamID 0x3fd20）。
- 只改 Stream ID 列表没用。"Allow"这一项是总开关，也必须设为 1。

**2. 卡上自动加载的模块把第 7 个窗口指错了地方（报错为 0）**

- 卡开机时会自动加载 `pcie_marvell_cnxk_ep` 模块。它把第 7 个窗口改指到自己分配的一块空白内存。
- 而随固件带的控制程序（vfio 版本）把信箱写在固件原本映射的位置。结果主机读到的是 0。
- Marvell 只在 DAO 的内核文档里写了对策：`modprobe.blacklist=pcie_marvell_cnxk_ep` "is required only on cn10k platforms"（DAO 提交 `f5ee09e`，2025-06-27）。SDK 自带的 PCIe EP README 仍然让你加载这个模块。

两个原因都做了对照验证。下表第二、三行是对照测试，测试时 EBF 已放行，控制程序都在运行：

| EBF 放行 | 第 7 个窗口 | 主机结果 |
|---|---|---|
| 否 | 已修正 | `Invalid magic number ffffffffffffffff`，卡上 SMMU 报错 |
| 是 | 模块设的值 `0x4611` | `Invalid magic number 0`，SMMU 不报错 |
| 是 | 固件原值 `0x2fef1` | 正常，出现 `ens1`，能 ping 能 ssh |

## 做了哪些改动

**卡的固件设置（EBF，已保存，永久生效）**

- `Allow PCIe Host to Access Octeon Memory` = **1**
- `Host Stream IDs for PEM0` = **0x3ffff**（放行所有主机访问，原来是 0x30000）

**卡上，永久生效：** `/etc/modprobe.d/blacklist-pcie-marvell-cnxk-ep.conf`（内容是 `blacklist pcie_marvell_cnxk_ep`，副本在 `card-scripts/`）。模块不再加载，第 7 个窗口就保持固件原值。卡没有 initramfs，这个文件就够了。

**卡上，每次开机都要运行一次：`/root/octep_up.sh`**（副本在 `card-scripts/`）。它做三件事：

1. 检查第 7 个窗口，只有被改过时才改回固件原值（模块意外加载时会打印警告）。
2. 把 PEM0 和两个 SDP PF 交给 `vfio-pci` 驱动，这是 vfio 版控制程序的前提。
3. 启动控制程序：

   ```
   octep_cp_agent /usr/bin/cn106xx.cfg -- --sdp_rvu_pf 0002:18:00.0,0002:19:00.0 --pem_dev 0001:00:10.0
   ```

之后给卡上网口配地址：`ip link set sdp1-0 up; ip addr add 10.10.0.2/24 dev sdp1-0`。

**主机**：驱动来自 Marvell 的 `pcie_ep_octeon_host` 仓库。它的说明写只支持到 6.6 内核，但在 6.17.7 上直接编译通过。等卡上控制程序起来后再加载：

```
sudo insmod ~/dev/marvell/pcie_ep_octeon_host/drivers/octeon_ep/octeon_ep.ko
sudo ip link set ens1 up; sudo ip addr add 10.10.0.1/24 dev ens1
```

## 注意事项

- **卸载驱动会让整张卡重启。** 驱动正常工作时执行 `rmmod octeon_ep`，驱动会写"整芯片复位"寄存器，这是驱动本身的设计。主机侧的 BAR 地址不受影响。
- **失败一次后，下次加载会一直等。** 初始化失败时，主机驱动把"固件就绪"标志留成了 2，下一次加载就一直显示 `Firmware ready status = 2`。解决办法是重启卡上的控制程序：`pkill -INT -x octep_cp_agent; /root/octep_up.sh`。正在等待的驱动会自己继续。
- **顺序**：先起卡上的控制程序，再加载主机驱动。
- **主机网口的 MAC 是 `00:00:00:01:01:00`**，因为 `cn106xx.cfg` 里 PF 的 MAC 只写了 5 个字节。不影响使用，需要的话改配置文件。
- **事故记录**：我有一次读寄存器时误读了整页 PEM 寄存器，引起 14 次可恢复的硬件错误（SEI-UER），没有留下影响。之后卡上寄存器一律用只访问单个寄存器的 `/root/bar4idx7.py`。

## 还没做成开机自动生效

- **卡**：每次开机都要重新运行控制程序脚本，并重新配 `sdp1-0` 的地址。
- **主机**：每次重启都要重新加载模块，并重新配 `ens1` 的地址。

建议下一步：卡上做一个 systemd 服务，运行 `octep_up.sh` 并配好 `sdp1-0`；主机上把模块装进系统、开机自动加载，并给 `ens1` 配固定地址。
