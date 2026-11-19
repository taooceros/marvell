import {
  C,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  PacketStream,
  Pill,
  SceneDef,
  Stage,
  appear,
  countUp,
  fade,
  prog,
  useRealT,
  useT,
} from "../lib";
import { HwBox, PathTag, PolyArrow, StatusBadge, TimeBlock } from "./dsa";

// <cues>
const cues: Cue[] = [
  [0.60, 11.04, "拆解延迟账本发现刺眼底噪：完成记录留在主机，卡每轮要发一笔 32 B DPI 同步读回，单次空读就要 **1.21 µs**！", "拆解延迟立刻看到大底噪：完成记录留在主机内存，卡每轮确认都要发一笔三十二字节 D P I 读回，单次纯空读就要一点二一微秒。"], // 0
  [11.39, 21.99, "这 **1.21 µs** 占 `W = 1 NOP` 的 **61%**；`4 KiB` 错过首轮读回便跨台阶等第二轮，抖动 **1,119 ns**、闭环 **3.13 µs**。", "这占了单并发空操作的百分之六十一；四 K B 拷贝错过首轮读回就得多等一整轮，抖动高达一千一百一十九纳秒，闭环被拖到三点一三微秒。"], // 1
  [22.34, 33.44, "单变量改动：保持纯 DPI 提交、只写门户和 `CC = 0`，唯一变化是把 `completion_addr` 改指**卡 PF BAR4 槽 15**。", "我们做严格单变量改动：固定纯 D P I 提交、只写门户和关闭缓存分配，只把完成地址从主机内存改指向卡 P F 的 B A R 四的十五号槽。"], // 2
  [33.79, 42.87, "租约保护下主机将 `BAR4` 槽 15 `mmap` 进休眠进程，`io_remap_pfn_range` 预填全部 **1,024** 个 `VM_PFNMAP` 页表项。", "租约保护下，主机把 B A R 四的十五号槽映射进休眠进程，内核同步预填全部一千零二十四个四 K B 页表项。"], // 3
  [43.22, 55.40, "`dsa2` 查 `dmar6` 直接命中，**反向 P2P** 把 32 B 完成记录直写卡 **L3 (TAD)**；卡核本地 `LDARB` 轮询、`1 ns` 清零。", "于是 d s a 二查 d m a r 六无需缺页，经反向 P 二 P 把三十二字节完成记录直写卡三级缓存 T A D；卡核本地轮询、一纳秒清零，彻底干掉读回。"], // 4
  [55.75, 67.57, "**102** 次零错：`W = 1` `NOP` 压至 **1.27 µs**（`-35.1%`），`4 KiB` **2.17 µs**，抖动 **163 ns**，`W = 96 NOP` 飙至 **23.01 M/s**！", "一百零二次实验零错误：单并发空操作降到一点二七微秒，四 K B 降到二点一七微秒，抖动压至一百六十三纳秒，高并发空操作更飙到每秒两千三百零一万次！"], // 5
];
const DUR = 68.97; // <dur>
// </cues>

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  // Beat opacities (strictly crossfaded, zero overlap)
  const opB01 = fade(t, T[0], T[2] - 0.1, 0.5);
  const kBar0 = prog(t, T[0] + 0.3, 1.0);
  const opB1 = fade(t, T[1], T[2] - 0.1, 0.5);
  const kPoll1 = prog(t, T[1] + 0.2, 0.7);
  const kPoll2 = prog(t, T[1] + 1.0, 0.8);

  const opB2 = fade(t, T[2], T[3] - 0.1, 0.5);
  const kB2Arr = prog(t, T[2] + 0.5, 0.9);

  const opB3 = fade(t, T[3], T[4] - 0.1, 0.5);
  const kB3Step1 = prog(t, T[3] + 0.2, 0.7);
  const kB3Step2 = prog(t, T[3] + 1.0, 0.8);
  const kB3Step3 = prog(t, T[3] + 1.8, 0.8);

  const opB4 = fade(t, T[4], T[5] - 0.1, 0.5);
  const kB4Rev = prog(t, T[4] + 0.3, 1.0);
  const kB4Cross = prog(t, T[4] + 1.6, 0.6);

  const opB5 = prog(t, T[5], 0.6);
  const kB5Bars = prog(t, T[5] + 0.4, 1.1);

  // Beat 4 reverse P2P polyline: dsa2 -> dmar6 -> PCIe -> BAR4 slot 15 -> L3 TAD -> Card Core
  const revPts: [number, number][] = [
    [1480, 320],
    [1175, 320],
    [740, 320],
    [450, 320],
  ];
  const oldPollPts: [number, number][] = [
    [450, 620],
    [1480, 620],
  ];

  const metrics = [
    {
      title: "W = 1 NOP 闭环 (1/rate)",
      before: "1.96 µs",
      after: `${countUp(t, T[5] + 0.3, 1.1, 1.27, 2, 1.96)} µs`,
      delta: "-35.1%",
      beforeFrac: 1.0,
      afterFrac: 1.27 / 1.96,
      color: K.rev,
    },
    {
      title: "W = 1 4 KiB 闭环 (1/rate)",
      before: "3.13 µs",
      after: `${countUp(t, T[5] + 0.4, 1.1, 2.17, 2, 3.13)} µs`,
      delta: "-30.6%",
      beforeFrac: 1.0,
      afterFrac: 2.17 / 3.13,
      color: K.rev,
    },
    {
      title: "4 KiB 尾抖动 (p99 − p50)",
      before: "1,119 ns",
      after: `${countUp(t, T[5] + 0.5, 1.1, 163, 0, 1119)} ns`,
      delta: "-85.4%",
      beforeFrac: 1.0,
      afterFrac: 163 / 1119,
      color: C.yellow,
    },
    {
      title: "W = 96 NOP 吞吐",
      before: "14.22 M/s",
      after: `${countUp(t, T[5] + 0.6, 1.1, 23.01, 2, 14.22)} M/s`,
      delta: "+61.9%",
      beforeFrac: 14.22 / 23.01,
      afterFrac: 1.0,
      color: C.cyan,
    },
  ];

  return (
    <>
      <Stage>
        {/* ==================== BEAT 0 & 1: Latency Floor & Polling Step ==================== */}
        {opB01 > 0.01 && (
          <g opacity={opB01}>
            {/* Top section: W=1 NOP (1.98 us) breakdown bar */}
            <rect x={70} y={145} width={1780} height={265} rx={16} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={106} y={190} fill={C.text} fontSize={26} fontWeight={700} fontFamily={FONT}>
              W = 1 NOP 单次延迟拆解（Host Memzone 对照组 · 总计 1.98 µs）
            </text>
            <text x={1814} y={190} textAnchor="end" fill={K.bubble} fontSize={24} fontWeight={700} fontFamily={MONO}>
              poll_only = 1,212 ns (61%)
            </text>

            {/* Stacked horizontal bar: x = 106..1814 (width 1708) */}
            {/* 0.77 us submit + DSA (39%) -> 666px; 1.21 us DPI read-back (61%) -> 1042px */}
            <TimeBlock
              x={106}
              y={220}
              w={666 * kBar0}
              h={96}
              label="正向 DPI 提交 + DSA 执行"
              sub="~0.77 µs (39%)"
              color={K.fwd}
            />
            <TimeBlock
              x={106 + 666 * kBar0}
              y={220}
              w={1042 * kBar0}
              h={96}
              label="卡 DPI 32 B 同步读回 Host Memzone"
              sub="1.21 µs (1,212 ns · 61% 底噪!)"
              color={K.bubble}
              hatch
            />

            {/* Dimension bracket underneath */}
            <line x1={106} y1={340} x2={1814} y2={340} stroke={C.dim} strokeWidth={2} />
            <line x1={106} y1={330} x2={106} y2={350} stroke={C.dim} strokeWidth={2} />
            <line x1={1814} y1={330} x2={1814} y2={350} stroke={C.dim} strokeWidth={2} />
            <text x={960} y={376} textAnchor="middle" fill={C.muted} fontSize={22} fontFamily={MONO}>
              0.00 µs ─────────────── 0.77 µs ─────────────────────────────── 1.98 µs (W=1 NOP p50)
            </text>

            {/* Bottom section (Beat 1): 4 KiB MEMMOVE Polling Quantization Step */}
            {opB1 > 0.01 && (
              <g opacity={opB1}>
                <rect x={70} y={440} width={1780} height={415} rx={16} fill={C.panel} stroke={K.bubble} strokeWidth={2} />
                <text x={106} y={486} fill={C.text} fontSize={26} fontWeight={700} fontFamily={FONT}>
                  4 KiB MEMMOVE 轮询量化台阶：错过 Poll #1 → 被迫多等一整轮！
                </text>
                <text x={1814} y={486} textAnchor="end" fill={C.yellow} fontSize={24} fontWeight={700} fontFamily={MONO}>
                  p99 = 3.30 µs · 抖动 p99−p50 = 1,119 ns
                </text>

                {/* Track 1: DSA execution (0..~1.5 us) */}
                <text x={106} y={556} fill={C.muted} fontSize={20} fontWeight={600} fontFamily={FONT}>
                  主机 DSA 拷贝
                </text>
                <TimeBlock
                  x={290}
                  y={522}
                  w={620 * kPoll1}
                  h={58}
                  label="4 KiB MEMMOVE 执行完成"
                  color={K.dsa}
                />

                {/* Track 2: Card DPI Polling Rounds */}
                <text x={106} y={648} fill={C.muted} fontSize={20} fontWeight={600} fontFamily={FONT}>
                  卡 DPI 轮询
                </text>
                <TimeBlock
                  x={290}
                  y={608}
                  w={540 * kPoll1}
                  h={72}
                  label="Poll #1 (1.21 µs)"
                  sub="未完成 · 空手而归!"
                  color={C.orange}
                  dashed
                />
                <TimeBlock
                  x={842}
                  y={608}
                  w={540 * kPoll2}
                  h={72}
                  label="Poll #2 (+1.21 µs)"
                  sub="跨台阶命中 → p99 = 3.30 µs"
                  color={K.bubble}
                  hatch
                />

                {/* Vertical miss marker */}
                {kPoll1 > 0.8 && (
                  <g opacity={prog(t, T[1] + 0.8, 0.4)}>
                    <line x1={830} y1={514} x2={830} y2={705} stroke={C.yellow} strokeWidth={3} strokeDasharray="8 6" />
                    <PathTag cx={830} cy={726} text="错过首轮采样点!" color={C.yellow} size={20} />
                  </g>
                )}

                {/* Right callout box */}
                <g opacity={kPoll2}>
                  <rect x={1425} y={530} width={385} height={280} rx={14} fill="#1b1420" stroke={K.bubble} strokeWidth={2} />
                  <text x={1455} y={576} fill={K.bubble} fontSize={24} fontWeight={800} fontFamily={FONT}>
                    Host Memzone 代价
                  </text>
                  <text x={1455} y={626} fill={C.text} fontSize={22} fontFamily={MONO}>
                    W=1 闭环 1/rate: 3.13 µs
                  </text>
                  <text x={1455} y={668} fill={C.text} fontSize={22} fontFamily={MONO}>
                    单笔 p50 / p99: 2.18 / 3.30 µs
                  </text>
                  <text x={1455} y={712} fill={C.yellow} fontSize={24} fontWeight={700} fontFamily={MONO}>
                    尾抖动: 1,119 ns
                  </text>
                  <text x={1455} y={760} fill={C.muted} fontSize={20} fontFamily={FONT}>
                    根源：卡主动跨 PCIe 读回完成记录
                  </text>
                </g>

                {/* Bottom time axis */}
                <line x1={290} y1={785} x2={1382} y2={785} stroke={C.dim} strokeWidth={2} />
                <text x={290} y={818} fill={C.muted} fontSize={20} fontFamily={MONO}>0 µs</text>
                <text x={830} y={818} textAnchor="middle" fill={C.orange} fontSize={20} fontFamily={MONO}>1.21 µs</text>
                <text x={1382} y={818} textAnchor="end" fill={K.bubble} fontSize={20} fontFamily={MONO}>3.30 µs (p99)</text>
              </g>
            )}
          </g>
        )}

        {/* ==================== BEAT 2: Single-Variable Comparison ==================== */}
        {opB2 > 0.01 && (
          <g opacity={opB2}>
            {/* Fixed control invariants banner */}
            <rect x={70} y={142} width={1780} height={64} rx={12} fill="#111a2e" stroke={C.cyan} strokeWidth={2} />
            <text x={960} y={183} textAnchor="middle" fill={C.cyan} fontSize={23} fontWeight={700} fontFamily={MONO}>
              严格单变量控制：固定纯 DPI 提交 (06:00.2) · VFIO_DMA_MAP_FLAG_WRITE · 单引擎 dsa2/wq2.0 · CC = 0
            </text>

            {/* TOP ROW: Control Group (Host Memzone) */}
            <rect x={70} y={232} width={1780} height={285} rx={16} fill={C.panel} stroke={K.hostComp} strokeWidth={2} />
            <text x={106} y={276} fill={K.hostComp} fontSize={26} fontWeight={800} fontFamily={FONT}>
              对照组：Host Memzone（旧路径）
            </text>

            <HwBox x={110} y={295} w={300} h={155} title="CN106 DPI" sub="06:00.2 写描述符" sub2="06:00.1 读完成记录" accent={K.card} />
            <HwBox x={770} y={295} w={300} h={155} title="Intel dsa2 / wq2.0" sub="单引擎执行拷贝" sub2="CC = 0" accent={K.dsa} />
            <HwBox x={1410} y={295} w={390} h={155} title="Host Memzone" sub="completion_addr 指向主机内存" sub2="卡须发 32 B DPI 读回 (1.21 µs)" accent={K.bubble} dashed />

            <PolyArrow pts={[[410, 365], [770, 365]]} color={K.fwd} w={4} k={kB2Arr} />
            <PathTag cx={590} cy={338} text="64 B 原子提交" color={K.fwd} size={18} />
            <PolyArrow pts={[[1070, 365], [1410, 365]]} color={K.hostComp} w={4} k={kB2Arr} />
            <PathTag cx={1240} cy={338} text="32 B 完成写" color={K.hostComp} size={18} />
            <PolyArrow pts={[[1605, 450], [1605, 488], [260, 488], [260, 450]]} color={K.bubble} w={4} dash="10 8" k={kB2Arr} />
            <PathTag cx={920} cy={488} text="卡 DPI 32 B 同步读回 (1.21 µs 底噪)" color={K.bubble} size={19} />

            {/* BOTTOM ROW: Experiment Group (Card BAR4 Slot 15) */}
            <rect x={70} y={548} width={1780} height={305} rx={16} fill="#0e1e1c" stroke={K.rev} strokeWidth={3} />
            <text x={106} y={592} fill={K.rev} fontSize={26} fontWeight={800} fontFamily={FONT}>
              实验组：Card PF BAR4 槽 15（唯一变量改动！）
            </text>

            <HwBox x={110} y={645} w={300} h={170} title="CN106 DPI" sub="06:00.2 写描述符" sub2="本地 LDARB 轮询" accent={K.card} active />
            <HwBox x={470} y={645} w={340} h={170} title="Card PF BAR4 槽 15" sub="completion_addr 改指此处" sub2="CN106 L3 (TAD) 直达" accent={K.rev} active badge="唯一改动" />
            <HwBox x={1160} y={645} w={300} h={170} title="Intel dsa2 / wq2.0" sub="单引擎执行拷贝" sub2="CC = 0 (完全相同)" accent={K.dsa} active />

            <PolyArrow pts={[[260, 645], [260, 614], [1310, 614], [1310, 645]]} color={K.fwd} w={4} k={kB2Arr} />
            <PathTag cx={960} cy={614} text="64 B 原子提交 (不变)" color={K.fwd} size={18} />
            <PolyArrow pts={[[1160, 756], [810, 756]]} color={K.rev} w={5} halo k={kB2Arr} />
            <PathTag cx={985} cy={792} text="反向 P2P 直写 32 B 完成记录" color={K.rev} size={19} />
            <PolyArrow pts={[[470, 756], [410, 756]]} color={K.rev} w={4} k={kB2Arr} />

            {/* Right highlight box */}
            <rect x={1510} y={626} width={300} height={180} rx={14} fill="#102924" stroke={K.rev} strokeWidth={2} />
            <text x={1660} y={678} textAnchor="middle" fill={K.rev} fontSize={24} fontWeight={800} fontFamily={FONT}>
              唯一变量
            </text>
            <text x={1660} y={722} textAnchor="middle" fill={C.text} fontSize={21} fontFamily={MONO}>
              completion_addr
            </text>
            <text x={1660} y={762} textAnchor="middle" fill={C.yellow} fontSize={20} fontWeight={700} fontFamily={FONT}>
              Host Mem → Card BAR4
            </text>
          </g>
        )}

        {/* ==================== BEAT 3: Lease Handshake + io_remap_pfn_range ==================== */}
        {opB3 > 0.01 && (
          <g opacity={opB3}>
            <rect x={70} y={145} width={1780} height={705} rx={16} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={106} y={194} fill={C.text} fontSize={28} fontWeight={800} fontFamily={FONT}>
              控制面一次性建立：租约握手 + 预填全部 1,024 个 VM_PFNMAP 页表项（零缺页）
            </text>

            {/* Step 1: Card PF BAR4 Slot 15 */}
            <HwBox
              x={110}
              y={245}
              w={370}
              h={230}
              title="1. Card PF 0000:99:00.0"
              sub="BAR4 = 16 × 4 MiB 槽位"
              sub2="槽 15 租约保护 · HPA 0x223ff3c00000"
              accent={K.card}
              active={kB3Step1 > 0.5}
              badge="Lease 租约"
            />

            {/* Step 2: Sleeping Host Holder Process */}
            <HwBox
              x={645}
              y={245}
              w={460}
              h={230}
              title="2. 主机休眠进程 mmap(BAR4 槽 15)"
              sub="io_remap_pfn_range() 一次性预填"
              sub2="1,024 × 4 KiB VM_PFNMAP PTEs"
              accent={C.cyan}
              active={kB3Step2 > 0.5}
              sleeping
            />

            {/* Step 3: dmar6 SVA Page Table */}
            <HwBox
              x={1275}
              y={245}
              w={535}
              h={230}
              title="3. IOMMU dmar6 (SVA PASID)"
              sub="共享休眠进程 mm_struct 页表"
              sub2="dsa2 访问 BAR4 槽 15 直接命中 · 零缺页！"
              accent={K.iommu6}
              active={kB3Step3 > 0.5}
              badge="0 Page Faults"
            />

            {/* Arrows between steps */}
            <PolyArrow pts={[[480, 360], [645, 360]]} color={K.card} w={4} k={kB3Step2} />
            <PathTag cx={562} cy={325} text="租约授权" color={K.card} size={18} />

            <PolyArrow pts={[[1105, 360], [1275, 360]]} color={K.iommu6} w={4} k={kB3Step3} />
            <PathTag cx={1190} cy={325} text="SVA 绑定" color={K.iommu6} size={18} />

            {/* Bottom visual: 1,024 pre-populated PTEs strip */}
            <g opacity={kB3Step2}>
              <rect x={110} y={535} width={1700} height={270} rx={14} fill="#0e1526" stroke={K.iommu6} strokeWidth={2} />
              <text x={145} y={582} fill={C.purple} fontSize={24} fontWeight={700} fontFamily={FONT}>
                io_remap_pfn_range 预填充的 1,024 × 4 KiB VM_PFNMAP 页表项（4 MiB 连续 BAR4 窗口）
              </text>
              <text x={1775} y={582} textAnchor="end" fill={K.rev} fontSize={22} fontWeight={700} fontFamily={MONO}>
                HPA: 0x223ff3c00000
              </text>

              {/* Grid of PTE cells */}
              {Array.from({ length: 16 }).map((_, i) => {
                const cellK = prog(t, T[3] + 1.0 + i * 0.06, 0.35);
                const cx = 145 + i * 102;
                return (
                  <g key={i} opacity={cellK}>
                    <rect
                      x={cx}
                      y={612}
                      width={92}
                      height={74}
                      rx={8}
                      fill={i === 15 ? "#14332a" : "#162038"}
                      stroke={i === 15 ? K.rev : C.purple}
                      strokeWidth={2}
                    />
                    <text x={cx + 46} y={644} textAnchor="middle" fill={C.text} fontSize={18} fontWeight={700} fontFamily={MONO}>
                      {i === 15 ? "PTE#1023" : `PTE#${i}`}
                    </text>
                    <text x={cx + 46} y={670} textAnchor="middle" fill={K.rev} fontSize={16} fontFamily={MONO}>
                      4 KiB
                    </text>
                  </g>
                );
              })}

              <text x={145} y={735} fill={C.muted} fontSize={22} fontFamily={FONT}>
                为什么必须预填？<tspan fill={C.text} fontWeight={700}>block_on_fault = 0</tspan> 下 dsa2 遇到未映射页会直接报错；预填全部 1,024 项后，数据面运行期间主机核全程休眠、零缺页！
              </text>
              <text x={145} y={778} fill={K.rev} fontSize={21} fontFamily={MONO}>
                open("/dev/dsa/wq2.0") 绑定 PASID → mmap(BAR4 slot 15) → 进程休眠 → 零主机 CPU 转发
              </text>
            </g>
          </g>
        )}

        {/* ==================== BEAT 4: Reverse PCIe P2P Datapath ==================== */}
        {opB4 > 0.01 && (
          <g opacity={opB4}>
            {/* Card Side */}
            <rect x={70} y={145} width={580} height={705} rx={18} fill="#141320" stroke={K.card} strokeWidth={2.5} />
            <text x={100} y={190} fill={K.card} fontSize={26} fontWeight={800} fontFamily={FONT}>
              Marvell CN106 DPU（卡侧）
            </text>

            {/* Host Side */}
            <rect x={1110} y={145} width={740} height={705} rx={18} fill="#11182c" stroke={K.host} strokeWidth={2.5} />
            <text x={1140} y={190} fill={K.host} fontSize={26} fontWeight={800} fontFamily={FONT}>
              Intel Xeon Host（saturn · NUMA 1）
            </text>

            {/* PCIe Gen5 x4 Center Bus */}
            <rect x={715} y={145} width={330} height={705} rx={16} fill="#0d1726" stroke={K.link} strokeWidth={2} strokeDasharray="10 6" />
            <text x={880} y={190} textAnchor="middle" fill={K.link} fontSize={24} fontWeight={800} fontFamily={MONO}>
              PCIe Gen5 x4
            </text>
            <text x={880} y={222} textAnchor="middle" fill={C.muted} fontSize={19} fontFamily={FONT}>
              同插槽双向 P2P
            </text>

            {/* Top Path: NEW Reverse P2P (Glowing Green) */}
            <HwBox x={1480} y={240} w={330} h={160} title="Intel dsa2 / wq2.0" sub="32 B 完成记录直发" sub2="反向 P2P MWr" accent={K.dsa} active />
            <HwBox x={1145} y={240} w={270} h={160} title="IOMMU dmar6" sub="SVA 页表直接命中" sub2="0 Page Faults" accent={K.iommu6} active />
            <HwBox x={105} y={240} w={345} h={160} title="Card L3 (TAD) / BAR4 槽 15" sub="32 B 完成记录直接落入 L3" sub2="无需经过主存" accent={K.rev} active badge="直达 L3" />

            <PolyArrow pts={revPts} color={K.rev} w={6} halo k={kB4Rev} />
            {kB4Rev > 0.7 && (
              <PacketStream pts={revPts} travel={rt * 190} gap={140} size={14} color={K.rev} />
            )}
            <PathTag cx={880} cy={282} text="反向 P2P · 32 B 完成记录" color={K.rev} size={20} />

            {/* Card Core Local Poll */}
            <HwBox x={105} y={445} w={510} h={145} title="CN106 lcore 3 本地轮询" sub="本地 LDARB 轮询 L3 · 1 ns 清零" sub2="零跨 PCIe 读 · 零清零写！" accent={K.rev} active />
            <PolyArrow pts={[[275, 400], [275, 445]]} color={K.rev} w={5} k={kB4Rev} />

            {/* Bottom Path: OLD DPI Read-back (Crossed out with Red X) */}
            <HwBox x={105} y={635} w={345} h={145} title="DPI 06:00.1 (FromHost)" sub="旧方案：32 B 同步读回" sub2="已彻底淘汰！" accent={K.bubble} dim />
            <HwBox x={1480} y={635} w={330} h={145} title="Host Memzone" sub="旧完成记录位置" sub2="不再轮询" accent={K.hostComp} dim />

            <PolyArrow pts={oldPollPts} color={K.bubble} w={4} dash="10 8" opacity={0.45} />
            {kB4Cross > 0.05 && (
              <g opacity={kB4Cross}>
                <StatusBadge cx={880} cy={620} ok={false} r={34} />
                <PathTag cx={880} cy={685} text="零 DPI 读回 · 零清零写 (砍掉 1.21 µs!)" color={K.bubble} size={21} />
              </g>
            )}
          </g>
        )}

        {/* ==================== BEAT 5: Before / After 4-Metric Comparison ==================== */}
        {opB5 > 0.01 && (
          <g opacity={opB5}>
            {/* Top summary badge */}
            <rect x={70} y={142} width={1780} height={64} rx={12} fill="#0f2622" stroke={K.rev} strokeWidth={2.5} />
            <text x={960} y={183} textAnchor="middle" fill={K.rev} fontSize={25} fontWeight={800} fontFamily={FONT}>
              反向 P2P 实测汇总：102 次运行 · 0 丢包 · 0 错位 · SWERROR = 0
            </text>

            {/* 4 Metric Cards (2x2 grid) */}
            {metrics.map((m, idx) => {
              const col = idx % 2;
              const row = Math.floor(idx / 2);
              const x = 70 + col * 905;
              const y = 232 + row * 315;
              const w = 875;
              const h = 290;
              const maxBarW = 470;
              return (
                <g key={m.title}>
                  <rect x={x} y={y} width={w} height={h} rx={16} fill={C.panel} stroke={m.color} strokeWidth={2.5} />
                  <text x={x + 32} y={y + 48} fill={C.text} fontSize={26} fontWeight={700} fontFamily={FONT}>
                    {m.title}
                  </text>
                  {/* Delta badge */}
                  <rect x={x + w - 165} y={y + 18} width={135} height={42} rx={10} fill="#102924" stroke={m.color} strokeWidth={2} />
                  <text x={x + w - 97} y={y + 46} textAnchor="middle" fill={m.color} fontSize={24} fontWeight={800} fontFamily={MONO}>
                    {m.delta}
                  </text>

                  {/* Before bar */}
                  <text x={x + 32} y={y + 114} fill={C.muted} fontSize={21} fontFamily={FONT}>
                    Host Memzone
                  </text>
                  <rect x={x + 205} y={y + 88} width={maxBarW * m.beforeFrac * kB5Bars} height={38} rx={8} fill={K.hostComp} opacity={0.55} />
                  <text x={x + 220 + maxBarW * m.beforeFrac * kB5Bars} y={y + 115} fill={C.muted} fontSize={22} fontWeight={700} fontFamily={MONO}>
                    {m.before}
                  </text>

                  {/* After bar */}
                  <text x={x + 32} y={y + 190} fill={m.color} fontSize={21} fontWeight={700} fontFamily={FONT}>
                    Card BAR4 P2P
                  </text>
                  <rect x={x + 205} y={y + 162} width={maxBarW * m.afterFrac * kB5Bars} height={44} rx={8} fill={m.color} />
                  <text x={x + 220 + maxBarW * m.afterFrac * kB5Bars} y={y + 193} fill={m.color} fontSize={28} fontWeight={800} fontFamily={MONO}>
                    {m.after}
                  </text>

                  {/* Bottom divider hint */}
                  <text x={x + 32} y={y + 256} fill={C.dim} fontSize={19} fontFamily={MONO}>
                    {idx === 2 ? "彻底消除 1.21 µs DPI 轮询台阶" : idx === 3 ? "43.5 ns/op · 反向 P2P 吞吐大涨" : "单并发对称闭环 1 / rate(W=1)"}
                  </text>
                </g>
              );
            })}
          </g>
        )}
      </Stage>
    </>
  );
}

export const S04: SceneDef = {
  id: "S04",
  title: "突破 2：反向 P2P 直写卡 BAR4",
  stage: 3,
  source: "单一变量：comp 主机 Memzone → 卡 BAR4 槽 15",
  dur: DUR,
  cues,
  C: Body,
};
