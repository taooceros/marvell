import {
  Axes,
  C,
  Cue,
  FONT,
  K,
  MONO,
  PacketStream,
  SceneDef,
  SeqRow,
  SeqState,
  Series,
  Stage,
  appear,
  countUp,
  fade,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { HwBox, InfTag, PathTag, PolyArrow, StatusBadge } from "./dsa";

// <cues>
const cues: Cue[] = [
  [0.60, 8.48, "固定同一个 `portal_iova`，只换卡侧提交原语这一单一变量：对比**卡核写合并 WC** 与 **DPI DMA 64 B**。", "固定同一个门户地址，单一变量对比卡核写合并 W C 与卡侧 D P I 引擎的六十四字节 D M A 写。"], // 0
  [8.83, 17.49, "卡核 **PEM0 WC** 用四条 16 B 存储凑包，单并发 `NOP` 延迟低至 **1.30 µs**，比卡 DPI 提交快 `0.68 µs`。", "卡核写合并四条十六字节凑一包，单并发空操作延迟低至一点三零微秒，比 D P I 提交快了零点六八微秒。"], // 1
  [17.84, 28.18, "但批量压测立刻露馅：`wc-none` 丢 **0.048%**、`wc-dgh` 丢 **0.609%**、`wc-dsb` 丢 **1.217%**，且 `SWERROR = 0`。", "但批量验证立刻露馅：三种写合并变体全部静默丢包，从百分之零点零四八到百分之一点二一七，而且硬件错误寄存器始终为零。"], // 2
  [28.53, 39.23, "我们推断 ARM 写合并属机会性聚集：拆成 `< 64 B` 或粘连成 `> 64 B` 的包会被 DWQ 静默忽略，按序回收立刻卡死。", "我们推断 A R M 写合并只是机会性聚集，一旦拆成小于或粘连成大于六十四字节的包，就会被专用队列静默丢弃，按序回收时丢一笔就卡死。"], // 3
  [39.58, 50.42, "改用卡侧 **DPI DMA 64 B 硬件原子写** 发往同一门户：`24,900` 次单并发与超 **1.82 亿** 次流水操作全部 **0 丢包**！", "改用卡侧 D P I 引擎硬件原子封包六十四字节写，发往同一门户：两万四千九百次单并发和超一点八二亿次流水操作全部零丢包、零错误。"], // 4
  [50.77, 63.93, "热 LLC（`≤ 768 KiB`，`CC = 1`）跑出 **14.23 M/s** `NOP` 与 **29.53 GB/s** 搬运；但读回让总 PCIe 流量比达 `6–7×`。", "热三级缓存下，纯 D P I 跑出每秒一千四百二十三万次空操作和二十九点五三 G B 每秒搬运；描述符仅零点四六 G B 每秒，但读回与清零让总 P C I e 流量比达六到七倍。"], // 5
];
const DUR = 65.33; // <dur>
// </cues>

const DROP_DATA = [
  { label: "wc-dsb", sub: "固定 off=0", pct: 1.217, frac: "28 / 2,300", color: C.red },
  { label: "wc-dgh", sub: "8行轮转", pct: 0.609, frac: "14 / 2,300", color: C.orange },
  { label: "wc-none", sub: "8行轮转", pct: 0.048, frac: "12 / 24,900", color: C.yellow },
  { label: "DPI DMA", sub: "64 B 原子", pct: 0.0, frac: "0 / 24,900", color: C.green },
];

const NOP_SERIES: [number, number][] = [
  [1, 0.51],
  [8, 3.41],
  [32, 8.88],
  [64, 12.66],
  [96, 14.23],
];

const COPY_SERIES: [number, number][] = [
  [1, 1.27],
  [8, 7.5],
  [32, 22.6],
  [64, 29.0],
  [96, 29.53],
];

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  const kHeader = prog(t, T[0], 0.5);
  const kBeat0 = fade(t, T[0], T[1] - 0.1, 0.5);
  const kBeat1 = fade(t, T[1], T[2] - 0.1, 0.5);
  const kBeat2 = fade(t, T[2], T[3] - 0.1, 0.5);
  const kBeat3 = fade(t, T[3], T[4] - 0.1, 0.5);
  const kBeat4 = fade(t, T[4], T[5] - 0.1, 0.5);
  const kBeat5 = prog(t, T[5], 0.6);

  // Active highlight in header
  const wcSpotlight = t >= T[1] && t < T[4];
  const dpiSpotlight = t >= T[4];

  // Beat 5 chart scales
  const sxA = linScale(0, 100, 155, 620);
  const syA = linScale(0, 16, 785, 330);
  const sxB = linScale(0, 100, 775, 1240);
  const syB = linScale(0, 34, 785, 330);

  // Beat 3 sequence row states (head-of-line stall at seq #2)
  const seqState = (i: number): SeqState => {
    if (i < 2) return "got";
    if (i === 2) return "lost";
    return "sent";
  };

  return (
    <>
      <Stage>
        {/* ==================== BEAT 0: SINGLE-VARIABLE SETUP ==================== */}
        {kBeat0 > 0.01 && (
          <g opacity={kBeat0}>
            <HwBox
              x={110}
              y={270}
              w={370}
              h={180}
              title="方案 A：卡核 PEM0 WC"
              sub="Neoverse-N2 4 × 16 B STP"
              sub2="ARM 写合并缓冲区凑包"
              accent={K.wc}
              active={true}
              badge="变量 A"
            />
            <HwBox
              x={110}
              y={520}
              w={370}
              h={180}
              title="方案 B：DPI DMA 64 B"
              sub="0000:06:00.2 (ToHost)"
              sub2="硬件引擎单笔 64 B 原子写"
              accent={K.dpi}
              active={true}
              badge="变量 B"
            />

            <HwBox
              x={1010}
              y={395}
              w={340}
              h={180}
              title="IOMMU dmar0"
              sub="VF0 group 286"
              sub2="portal_iova: 0x117fe00000"
              accent={K.iommu0}
              active={true}
              badge="固定不变"
            />
            <HwBox
              x={1430}
              y={395}
              w={370}
              h={180}
              title="Intel DSA (dsa2/wq2.0)"
              sub="专用队列 DWQ · BAR2+0x1000"
              sub2="要求单笔 64 B 对齐原子 MWr"
              accent={K.dsa}
              active={true}
              badge="固定目标"
            />

            <PolyArrow
              pts={[
                [480, 360],
                [745, 360],
                [745, 455],
                [1010, 455],
              ]}
              color={K.wc}
              w={4.5}
              k={prog(t, T[0] + 0.4, 0.7)}
              halo
            />
            <PacketStream
              pts={[
                [480, 360],
                [745, 360],
                [745, 455],
                [1010, 455],
              ]}
              travel={rt * 155}
              gap={64}
              size={12}
              color={K.wc}
            />

            <PolyArrow
              pts={[
                [480, 610],
                [745, 610],
                [745, 515],
                [1010, 515],
              ]}
              color={K.dpi}
              w={4.5}
              k={prog(t, T[0] + 0.6, 0.7)}
              halo
            />
            <PacketStream
              pts={[
                [480, 610],
                [745, 610],
                [745, 515],
                [1010, 515],
              ]}
              travel={rt * 155}
              gap={64}
              size={12}
              color={K.dpi}
            />

            <PolyArrow
              pts={[
                [1350, 485],
                [1430, 485],
              ]}
              color={K.link}
              w={4.5}
              k={prog(t, T[0] + 0.9, 0.5)}
              halo
            />
            <PathTag cx={745} cy={312} text="同一 PCIe Gen5 x4 路径" color={K.link} size={20} />
          </g>
        )}

        {/* ==================== BEAT 1: PEM0 WC 4x16B + W=1 NOP LATENCY ==================== */}
        {kBeat1 > 0.01 && (
          <g opacity={kBeat1}>
            {/* Left panel frame: PEM0 WC 4x16B gathering */}
            <rect
              x={80}
              y={236}
              width={840}
              height={610}
              rx={18}
              fill="#101729"
              stroke={K.wc}
              strokeWidth={2}
              strokeOpacity={0.75}
            />
            <text
              x={114}
              y={284}
              fill={K.wc}
              fontFamily={FONT}
              fontSize={26}
              fontWeight={800}
            >
              卡核 PEM0 WC：4 × 16 B STP 凑 64 B 包
            </text>

            {/* 4 x 16B STP blocks */}
            {[0, 1, 2, 3].map((idx) => {
              const kBlk = prog(t, T[1] + 0.25 + idx * 0.18, 0.4);
              const bx = 120 + idx * 188;
              return (
                <g key={idx} opacity={kBlk}>
                  <rect
                    x={bx}
                    y={334}
                    width={168}
                    height={84}
                    rx={12}
                    fill={`${K.wc}20`}
                    stroke={K.wc}
                    strokeWidth={2}
                  />
                  <text
                    x={bx + 84}
                    y={372}
                    textAnchor="middle"
                    fill={C.text}
                    fontFamily={MONO}
                    fontSize={21}
                    fontWeight={700}
                  >
                    STP #{idx}
                  </text>
                  <text
                    x={bx + 84}
                    y={402}
                    textAnchor="middle"
                    fill={K.wc}
                    fontFamily={MONO}
                    fontSize={18}
                  >
                    16 B
                  </text>
                  <PolyArrow
                    pts={[
                      [bx + 84, 418],
                      [bx + 84, 496],
                    ]}
                    color={K.wc}
                    w={3}
                    k={kBlk}
                    head={10}
                  />
                </g>
              );
            })}

            {/* WC Buffer box */}
            <rect
              x={120}
              y={506}
              width={732}
              height={102}
              rx={14}
              fill="#19233c"
              stroke={K.wc}
              strokeWidth={2.5}
            />
            <text
              x={486}
              y={550}
              textAnchor="middle"
              fill={C.text}
              fontFamily={FONT}
              fontSize={24}
              fontWeight={800}
            >
              ARM Write-Combining Buffer (期望凑齐 64 B 整行)
            </text>
            <text
              x={486}
              y={584}
              textAnchor="middle"
              fill={C.muted}
              fontFamily={MONO}
              fontSize={19}
            >
              直写 PEM0 BAR → 省去 DPI 引擎提交开销
            </text>

            {/* Arrow out to dsa2 */}
            <PolyArrow
              pts={[
                [486, 608],
                [486, 692],
              ]}
              color={K.wc}
              w={4}
              k={prog(t, T[1] + 0.9, 0.5)}
              halo
            />
            <HwBox
              x={216}
              y={700}
              w={540}
              h={112}
              title="dsa2/wq2.0 门户 (portal_iova)"
              sub="单并发 W=1 NOP 延迟仅 1.30 µs"
              accent={K.dsa}
              active={true}
            />

            {/* Right panel frame: W=1 NOP latency bars */}
            <rect
              x={960}
              y={236}
              width={880}
              height={610}
              rx={18}
              fill="#101729"
              stroke={C.line}
              strokeWidth={2}
            />
            <text
              x={996}
              y={284}
              fill={C.text}
              fontFamily={FONT}
              fontSize={26}
              fontWeight={800}
            >
              W = 1 单并发 NOP 延迟对比 (p50, CC = 1)
            </text>

            {[
              { name: "Host MOVDIR64B", val: 0.4, color: K.host, tag: "主机本地基线" },
              { name: "Card PEM0 WC", val: 1.3, color: K.wc, tag: "比 DPI 快 0.68 µs" },
              { name: "Card DPI DMA 64 B", val: 1.98, color: K.dpi, tag: "硬件 DMA 引擎" },
            ].map((row, i) => {
              const kBar = prog(t, T[1] + 0.4 + i * 0.25, 0.7);
              const ry = 344 + i * 142;
              const maxW = 540;
              const bw = (row.val / 2.2) * maxW * kBar;
              return (
                <g key={row.name}>
                  <text
                    x={996}
                    y={ry}
                    fill={C.text}
                    fontFamily={FONT}
                    fontSize={23}
                    fontWeight={700}
                  >
                    {row.name}
                  </text>
                  <text
                    x={1804}
                    y={ry}
                    textAnchor="end"
                    fill={row.color}
                    fontFamily={FONT}
                    fontSize={20}
                    fontWeight={700}
                  >
                    {row.tag}
                  </text>
                  <rect
                    x={996}
                    y={ry + 18}
                    width={maxW}
                    height={52}
                    rx={10}
                    fill="#182138"
                  />
                  <rect
                    x={996}
                    y={ry + 18}
                    width={Math.max(8, bw)}
                    height={52}
                    rx={10}
                    fill={row.color}
                  />
                  <text
                    x={996 + Math.max(8, bw) + 18}
                    y={ry + 53}
                    fill={row.color}
                    fontFamily={MONO}
                    fontSize={28}
                    fontWeight={800}
                  >
                    {row.val.toFixed(2)} µs
                  </text>
                </g>
              );
            })}

            {/* Highlight callout inside right panel */}
            <rect
              x={996}
              y={758}
              width={808}
              height={62}
              rx={12}
              fill={`${K.wc}18`}
              stroke={K.wc}
              strokeWidth={1.5}
            />
            <text
              x={1400}
              y={797}
              textAnchor="middle"
              fill={K.wc}
              fontFamily={FONT}
              fontSize={22}
              fontWeight={700}
            >
              表面看 PEM0 WC 延迟更低（−0.68 µs）——但批量压测立刻露馅！
            </text>
          </g>
        )}

        {/* ==================== BEAT 2: SILENT DROP RATE BAR CHART ==================== */}
        {kBeat2 > 0.01 && (
          <g opacity={kBeat2}>
            {/* Left: Bar chart panel */}
            <rect
              x={80}
              y={236}
              width={1180}
              height={610}
              rx={18}
              fill="#101729"
              stroke={C.line}
              strokeWidth={2}
            />
            <text
              x={116}
              y={284}
              fill={C.text}
              fontFamily={FONT}
              fontSize={26}
              fontWeight={800}
            >
              DWQ 静默丢包率对比（W = 1 批量验证）
            </text>

            {/* Horizontal baseline & grid */}
            {[0, 0.4, 0.8, 1.2].map((v) => {
              const gy = 732 - (v / 1.4) * 380;
              return (
                <g key={v}>
                  <line
                    x1={168}
                    y1={gy}
                    x2={1210}
                    y2={gy}
                    stroke={C.line}
                    strokeWidth={v === 0 ? 2 : 1}
                    strokeDasharray={v === 0 ? undefined : "5 6"}
                  />
                  <text
                    x={154}
                    y={gy + 7}
                    textAnchor="end"
                    fill={C.muted}
                    fontFamily={MONO}
                    fontSize={19}
                  >
                    {v.toFixed(1)}%
                  </text>
                </g>
              );
            })}

            {/* 4 bars */}
            {DROP_DATA.map((d, i) => {
              const kBar = prog(t, T[2] + 0.3 + i * 0.22, 0.7);
              const bx = 228 + i * 246;
              const bw = 148;
              const bh = d.pct > 0 ? Math.max(12, (d.pct / 1.4) * 380 * kBar) : 6 * kBar;
              const by = 732 - bh;
              return (
                <g key={d.label}>
                  <rect
                    x={bx}
                    y={by}
                    width={bw}
                    height={bh}
                    rx={8}
                    fill={d.color}
                  />
                  <text
                    x={bx + bw / 2}
                    y={by - 38}
                    textAnchor="middle"
                    fill={d.color}
                    fontFamily={MONO}
                    fontSize={26}
                    fontWeight={800}
                    opacity={kBar}
                  >
                    {d.pct.toFixed(3)}%
                  </text>
                  <text
                    x={bx + bw / 2}
                    y={by - 12}
                    textAnchor="middle"
                    fill={C.muted}
                    fontFamily={MONO}
                    fontSize={18}
                    opacity={kBar}
                  >
                    {d.frac}
                  </text>
                  <text
                    x={bx + bw / 2}
                    y={768}
                    textAnchor="middle"
                    fill={C.text}
                    fontFamily={MONO}
                    fontSize={22}
                    fontWeight={700}
                  >
                    {d.label}
                  </text>
                  <text
                    x={bx + bw / 2}
                    y={798}
                    textAnchor="middle"
                    fill={C.muted}
                    fontFamily={FONT}
                    fontSize={19}
                  >
                    {d.sub}
                  </text>
                </g>
              );
            })}

            {/* Right: SWERROR = 0 red alarm panel */}
            <rect
              x={1294}
              y={236}
              width={546}
              height={610}
              rx={18}
              fill="rgba(249, 65, 68, 0.08)"
              stroke={C.red}
              strokeWidth={2.5}
            />
            <StatusBadge cx={1567} cy={326} ok={false} r={38} opacity={kBeat2} />
            <text
              x={1567}
              y={416}
              textAnchor="middle"
              fill={C.red}
              fontFamily={MONO}
              fontSize={42}
              fontWeight={900}
            >
              SWERROR = 0
            </text>
            <text
              x={1567}
              y={464}
              textAnchor="middle"
              fill={C.text}
              fontFamily={FONT}
              fontSize={26}
              fontWeight={800}
            >
              静默丢弃 · 零硬件报错
            </text>
            <line x1={1344} y1={500} x2={1790} y2={500} stroke={C.red} strokeOpacity={0.35} strokeWidth={1.5} />
            <text x={1344} y={552} fill={C.text} fontFamily={FONT} fontSize={22}>
              • 三种 WC 变体全部出现丢包
            </text>
            <text x={1344} y={602} fill={C.text} fontFamily={FONT} fontSize={22}>
              • 即使 8 行轮转 + 无屏障仍丢 0.048%
            </text>
            <text x={1344} y={652} fill={C.text} fontFamily={FONT} fontSize={22}>
              • DSA 硬件不置位任何错误寄存器
            </text>
            <rect
              x={1344}
              y={704}
              width={446}
              height={96}
              rx={12}
              fill="rgba(67, 197, 158, 0.12)"
              stroke={C.green}
              strokeWidth={2}
            />
            <text
              x={1567}
              y={744}
              textAnchor="middle"
              fill={C.green}
              fontFamily={FONT}
              fontSize={22}
              fontWeight={800}
            >
              对照：DPI DMA 64 B
            </text>
            <text
              x={1567}
              y={780}
              textAnchor="middle"
              fill={C.text}
              fontFamily={MONO}
              fontSize={22}
              fontWeight={700}
            >
              0 / 24,900 (0.000% 丢包)
            </text>
          </g>
        )}

        {/* ==================== BEAT 3: WC COALESCING MECHANISM + HOL STALL ==================== */}
        {kBeat3 > 0.01 && (
          <g opacity={kBeat3}>
            {/* Upper panel: ARM WC opportunistic split/merge */}
            <rect
              x={80}
              y={236}
              width={1760}
              height={326}
              rx={18}
              fill="#101729"
              stroke={K.wc}
              strokeWidth={2}
              strokeOpacity={0.8}
            />
            <text
              x={116}
              y={282}
              fill={K.wc}
              fontFamily={FONT}
              fontSize={26}
              fontWeight={800}
            >
              根因机制：ARM 写合并属机会性聚集，无法保证单笔 64 B 原子 TLP
            </text>
            <InfTag x={1620} y={254} text="[INF] 推断" opacity={kBeat3} />

            {/* Left box: ARM WC Buffer */}
            <HwBox
              x={120}
              y={324}
              w={340}
              h={196}
              title="ARM WC Buffer"
              sub="4 × 16 B STP 写入"
              sub2="受微架构冲刷时机影响"
              accent={K.wc}
              active={true}
            />

            {/* Middle split/merge TLP examples */}
            <g opacity={prog(t, T[3] + 0.4, 0.6)}>
              {/* Case 1: Split < 64 B */}
              <rect
                x={570}
                y={328}
                width={215}
                height={76}
                rx={10}
                fill={`${C.red}20`}
                stroke={C.red}
                strokeWidth={2}
              />
              <text x={677} y={362} textAnchor="middle" fill={C.red} fontFamily={MONO} fontSize={21} fontWeight={800}>
                拆包 TLP &lt; 64 B
              </text>
              <text x={677} y={390} textAnchor="middle" fill={C.muted} fontFamily={MONO} fontSize={18}>
                例：32 B + 32 B
              </text>

              {/* Case 2: Merge > 64 B */}
              <rect
                x={570}
                y={434}
                width={215}
                height={76}
                rx={10}
                fill={`${C.orange}20`}
                stroke={C.orange}
                strokeWidth={2}
              />
              <text x={677} y={468} textAnchor="middle" fill={C.orange} fontFamily={MONO} fontSize={21} fontWeight={800}>
                粘连 TLP &gt; 64 B
              </text>
              <text x={677} y={496} textAnchor="middle" fill={C.muted} fontFamily={MONO} fontSize={18}>
                例：128 B 连续写
              </text>

              <PolyArrow pts={[[460, 396], [568, 366]]} color={C.red} w={3.5} k={prog(t, T[3] + 0.3, 0.5)} />
              <PolyArrow pts={[[460, 446], [568, 472]]} color={C.orange} w={3.5} k={prog(t, T[3] + 0.3, 0.5)} />
            </g>

            {/* Right: DWQ silently drops */}
            <PolyArrow pts={[[785, 366], [970, 404]]} color={C.red} w={3.5} dash="6 5" k={prog(t, T[3] + 0.8, 0.5)} />
            <PolyArrow pts={[[785, 472], [970, 444]]} color={C.orange} w={3.5} dash="6 5" k={prog(t, T[3] + 0.8, 0.5)} />

            <HwBox
              x={976}
              y={324}
              w={380}
              h={196}
              title="dsa2/wq2.0 DWQ 门户"
              sub="仅接受严格 64 B 对齐原子写"
              sub2="非 64 B 写 → 静默忽略 (SWERROR=0)"
              accent={C.red}
              active={true}
            />
            <StatusBadge cx={1420} cy={422} ok={false} r={30} opacity={prog(t, T[3] + 1.0, 0.4)} />
            <text x={1468} y={412} fill={C.red} fontFamily={FONT} fontSize={24} fontWeight={800}>
              整笔描述符蒸发
            </text>
            <text x={1468} y={448} fill={C.muted} fontFamily={FONT} fontSize={20}>
              完成记录永远不会写回
            </text>

            {/* Lower panel: Head-of-line stall SeqRow */}
            <rect
              x={80}
              y={586}
              width={1760}
              height={264}
              rx={18}
              fill="#101729"
              stroke={C.red}
              strokeWidth={2}
              strokeOpacity={0.75}
            />
            <text
              x={116}
              y={634}
              fill={C.text}
              fontFamily={FONT}
              fontSize={24}
              fontWeight={800}
            >
              按序回收队列（In-Order Reclaim）—— 丢一笔即卡死整条流水线
            </text>
            <SeqRow
              x={140}
              y={668}
              n={10}
              start={0}
              cell={112}
              gap={18}
              state={seqState}
              show={(i) => prog(t, T[3] + 0.6 + i * 0.1, 0.35)}
              label={(i) => `#${i}`}
              fontSize={26}
            />
            <text x={140} y={818} fill={C.green} fontFamily={FONT} fontSize={21} fontWeight={700}>
              #0 #1 已完成回收
            </text>
            <text x={400} y={818} fill={C.red} fontFamily={FONT} fontSize={22} fontWeight={800}>
              ▲ #2 静默丢失 → 卡核死等 #2 完成，后续 #3..#9 全部阻塞！
            </text>
          </g>
        )}

        {/* ==================== BEAT 4: DPI DMA 64 B HARDWARE ATOMIC ==================== */}
        {kBeat4 > 0.01 && (
          <g opacity={kBeat4}>
            {/* Upper hardware flow */}
            <rect
              x={80}
              y={236}
              width={1760}
              height={272}
              rx={18}
              fill="#101729"
              stroke={K.dpi}
              strokeWidth={2.5}
            />
            <HwBox
              x={130}
              y={284}
              w={390}
              h={176}
              title="卡侧 DPI DMA 引擎"
              sub="0000:06:00.2 (ToHost VF)"
              sub2="硬件原子封包 · 严格 64 B MWr"
              accent={K.dpi}
              active={true}
              badge="硬件保证"
            />
            <HwBox
              x={820}
              y={296}
              w={300}
              h={152}
              title="IOMMU dmar0"
              sub="VF0 group 286"
              sub2="portal_iova → HPA"
              accent={K.iommu0}
              active={true}
            />
            <HwBox
              x={1310}
              y={284}
              w={390}
              h={176}
              title="Intel DSA (dsa2/wq2.0)"
              sub="每笔 64 B 对齐原子送达"
              sub2="100% 接收 · 零丢包"
              accent={C.green}
              active={true}
              badge="0 LOST"
            />

            <PolyArrow pts={[[520, 372], [820, 372]]} color={K.dpi} w={5} k={prog(t, T[4] + 0.3, 0.6)} halo />
            <PacketStream pts={[[520, 372], [820, 372]]} travel={rt * 175} gap={58} size={14} color={K.dpi} />
            <PathTag cx={670} cy={336} text="原子 64 B Posted MWr" color={K.dpi} size={19} mono />

            <PolyArrow pts={[[1120, 372], [1310, 372]]} color={C.green} w={5} k={prog(t, T[4] + 0.6, 0.5)} halo />
            <PacketStream pts={[[1120, 372], [1310, 372]]} travel={rt * 175} gap={52} size={14} color={C.green} />
            <StatusBadge cx={1764} cy={372} ok={true} r={30} opacity={prog(t, T[4] + 0.9, 0.4)} />
          </g>
        )}

        {/* ==================== BEAT 5: SUSTAINED SCALING CHARTS ==================== */}
        {kBeat5 > 0.01 && (
          <g opacity={kBeat5}>
            {/* Chart A: NOP M ops/s */}
            <rect
              x={80}
              y={236}
              width={574}
              height={614}
              rx={18}
              fill="#101729"
              stroke={K.dpi}
              strokeWidth={2}
            />
            <text x={112} y={282} fill={K.dpi} fontFamily={FONT} fontSize={24} fontWeight={800}>
              NOP 持续吞吐 (M ops/s)
            </text>
            <Axes
              x0={155}
              x1={620}
              y0={785}
              y1={330}
              sx={sxA}
              sy={syA}
              xticks={[[1, "1"], [32, "32"], [64, "64"], [96, "96"]]}
              yticks={[[0, "0"], [4, "4"], [8, "8"], [12, "12"], [16, "16"]]}
              xlabel="并发深度 W"
              ylabel="M ops/s"
              size={19}
            />
            <Series
              pts={NOP_SERIES}
              sx={sxA}
              sy={syA}
              k={prog(t, T[5] + 0.3, 1.2)}
              color={K.dpi}
              w={5}
              r={7}
            />
            <text
              x={sxA(96) - 10}
              y={syA(14.23) - 18}
              textAnchor="end"
              fill={K.dpi}
              fontFamily={MONO}
              fontSize={24}
              fontWeight={800}
              opacity={prog(t, T[5] + 1.2, 0.4)}
            >
              14.23 M/s
            </text>

            {/* Chart B: 4 KiB MEMMOVE GB/s */}
            <rect
              x={684}
              y={236}
              width={590}
              height={614}
              rx={18}
              fill="#101729"
              stroke={C.green}
              strokeWidth={2}
            />
            <text x={716} y={282} fill={C.green} fontFamily={FONT} fontSize={24} fontWeight={800}>
              4 KiB MEMMOVE (GB/s · 热 LLC, CC=1)
            </text>
            <Axes
              x0={775}
              x1={1240}
              y0={785}
              y1={330}
              sx={sxB}
              sy={syB}
              xticks={[[1, "1"], [32, "32"], [64, "64"], [96, "96"]]}
              yticks={[[0, "0"], [10, "10"], [20, "20"], [30, "30"]]}
              xlabel="并发深度 W"
              ylabel="GB/s"
              size={19}
            />
            <Series
              pts={COPY_SERIES}
              sx={sxB}
              sy={syB}
              k={prog(t, T[5] + 0.45, 1.2)}
              color={C.green}
              w={5}
              r={7}
            />
            <text
              x={sxB(96) - 10}
              y={syB(29.53) - 18}
              textAnchor="end"
              fill={C.green}
              fontFamily={MONO}
              fontSize={24}
              fontWeight={800}
              opacity={prog(t, T[5] + 1.35, 0.4)}
            >
              29.53 GB/s
            </text>
          </g>
        )}
      </Stage>

      {/* ==================== PERSISTENT TOP COMPARISON HEADER (y = 132..212) ==================== */}
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 132,
          width: 1760,
          height: 78,
          borderRadius: 14,
          background: "rgba(20, 27, 46, 0.94)",
          border: `1.5px solid ${C.line}`,
          padding: "0 28px",
          boxSizing: "border-box",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          opacity: kHeader,
          fontFamily: FONT,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span style={{ fontSize: 20, color: C.muted, fontWeight: 700 }}>单一变量对照</span>
          <span style={{ color: C.dim }}>|</span>
          <span style={{ fontSize: 20, color: C.text }}>
            固定目标 <span style={{ fontFamily: MONO, color: K.iommu0 }}>portal_iova (dsa2/wq2.0)</span>
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <div
            style={{
              padding: "8px 18px",
              borderRadius: 10,
              background: wcSpotlight ? `${K.wc}24` : "transparent",
              border: `1.5px solid ${wcSpotlight ? K.wc : C.line}`,
              color: wcSpotlight ? K.wc : C.muted,
              fontSize: 20,
              fontWeight: 800,
            }}
          >
            卡核 PEM0 WC (4×16 B)
          </div>
          <span style={{ fontSize: 20, fontWeight: 800, color: C.dim }}>VS</span>
          <div
            style={{
              padding: "8px 18px",
              borderRadius: 10,
              background: dpiSpotlight ? `${K.dpi}24` : "transparent",
              border: `1.5px solid ${dpiSpotlight ? K.dpi : C.line}`,
              color: dpiSpotlight ? K.dpi : C.muted,
              fontSize: 20,
              fontWeight: 800,
            }}
          >
            DPI DMA 64 B (06:00.2)
          </div>
        </div>
      </div>

      {/* ==================== BEAT 0: BOTTOM SUMMARY ==================== */}
      {kBeat0 > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 110,
            top: 742,
            width: 1690,
            height: 102,
            borderRadius: 14,
            background: "rgba(20, 27, 46, 0.92)",
            border: `1.5px solid ${C.line}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 36px",
            boxSizing: "border-box",
            opacity: kBeat0,
            fontFamily: FONT,
          }}
        >
          <span style={{ fontSize: 23, color: C.text, fontWeight: 700 }}>
            同插槽、同门户、同 <span style={{ fontFamily: MONO, color: K.iommu0 }}>dmar0</span> 映射 —— 只替换卡侧 64 B 提交原语
          </span>
          <span style={{ fontSize: 21, color: K.link, fontWeight: 700 }}>
            先测延迟，再做批量可靠性验证
          </span>
        </div>
      )}

      {/* ==================== BEAT 4: BIG GREEN VERIFICATION STATS ==================== */}
      {kBeat4 > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 80,
            top: 536,
            width: 1760,
            display: "flex",
            gap: 28,
            opacity: kBeat4,
            fontFamily: FONT,
          }}
        >
          <div
            style={{
              flex: 1,
              height: 304,
              borderRadius: 18,
              background: "rgba(67, 197, 158, 0.10)",
              border: `2px solid ${C.green}`,
              padding: "30px 32px",
              boxSizing: "border-box",
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              ...appear(t, T[4] + 0.3, undefined, 16),
            }}
          >
            <div style={{ fontSize: 22, color: C.muted, fontWeight: 700 }}>W = 1 单并发批量验证</div>
            <div style={{ fontFamily: MONO, fontSize: 64, fontWeight: 900, color: C.green }}>
              0 / 24,900
            </div>
            <div style={{ fontSize: 22, color: C.text }}>
              丢包率 <span style={{ fontFamily: MONO, color: C.green, fontWeight: 800 }}>0.000%</span> · 彻底告别 WC 静默丢包
            </div>
          </div>

          <div
            style={{
              flex: 1.15,
              height: 304,
              borderRadius: 18,
              background: "rgba(67, 197, 158, 0.14)",
              border: `2.5px solid ${C.green}`,
              padding: "30px 32px",
              boxSizing: "border-box",
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              ...appear(t, T[4] + 0.55, undefined, 16),
            }}
          >
            <div style={{ fontSize: 22, color: C.muted, fontWeight: 700 }}>持续流水线压测总计</div>
            <div style={{ fontFamily: MONO, fontSize: 64, fontWeight: 900, color: C.green }}>
              &gt; {countUp(t, T[4] + 0.6, 1.2, 1.82, 2)} 亿
            </div>
            <div style={{ fontSize: 22, color: C.text }}>
              实测 <span style={{ fontFamily: MONO, color: C.yellow }}>182,913,304</span> 次操作 · 0 lost / 0 bad
            </div>
          </div>

          <div
            style={{
              flex: 0.85,
              height: 304,
              borderRadius: 18,
              background: "rgba(76, 201, 240, 0.10)",
              border: `2px solid ${K.dpi}`,
              padding: "30px 32px",
              boxSizing: "border-box",
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              ...appear(t, T[4] + 0.8, undefined, 16),
            }}
          >
            <div style={{ fontSize: 22, color: C.muted, fontWeight: 700 }}>DSA 硬件错误寄存器</div>
            <div style={{ fontFamily: MONO, fontSize: 58, fontWeight: 900, color: K.dpi }}>
              SWERROR = 0
            </div>
            <div style={{ fontSize: 22, color: C.text }}>
              生产护栏：全面禁用 <span style={{ fontFamily: MONO, color: K.wc }}>pem_wc</span>
            </div>
          </div>
        </div>
      )}

      {/* ==================== BEAT 5: RIGHT CALLOUT PANEL ==================== */}
      {kBeat5 > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 1304,
            top: 236,
            width: 536,
            height: 614,
            display: "flex",
            flexDirection: "column",
            gap: 20,
            opacity: kBeat5,
            fontFamily: FONT,
          }}
        >
          <div
            style={{
              borderRadius: 18,
              background: "rgba(20, 27, 46, 0.95)",
              border: `2px solid ${C.green}`,
              padding: "24px 28px",
              boxSizing: "border-box",
            }}
          >
            <div style={{ fontSize: 20, color: C.muted, fontWeight: 700 }}>
              纯 DPI 流水线峰值（W = 96, 热 LLC ≤ 768 KiB）
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 18 }}>
              <div>
                <div style={{ fontFamily: MONO, fontSize: 44, fontWeight: 900, color: K.dpi }}>
                  {countUp(t, T[5] + 0.3, 1.2, 14.23, 2)}
                </div>
                <div style={{ fontSize: 19, color: C.muted }}>M ops/s (NOP)</div>
              </div>
              <div>
                <div style={{ fontFamily: MONO, fontSize: 44, fontWeight: 900, color: C.green }}>
                  {countUp(t, T[5] + 0.4, 1.2, 29.53, 2)}
                </div>
                <div style={{ fontSize: 19, color: C.muted }}>GB/s (4 KiB 搬运)</div>
              </div>
            </div>
          </div>

          <div
            style={{
              flex: 1,
              borderRadius: 18,
              background: "rgba(249, 199, 79, 0.09)",
              border: `2px solid ${C.yellow}`,
              padding: "24px 28px",
              boxSizing: "border-box",
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
            }}
          >
            <div style={{ fontSize: 23, fontWeight: 800, color: C.yellow }}>
              隐藏瓶颈：主机完成记录的 PCIe 读回开销
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 14, fontSize: 21, color: C.text }}>
              <div>
                • 64 B 描述符净流量：仅{" "}
                <span style={{ fontFamily: MONO, color: K.dpi, fontWeight: 800 }}>0.46 GB/s</span>
              </div>
              <div>
                • 含完成区清零 + DPI 轮询读回：总 PCIe 流量比达{" "}
                <span style={{ fontFamily: MONO, color: C.yellow, fontWeight: 800 }}>~6–7×</span>
              </div>
              <div>
                • 且单次 DPI 32 B 读回底噪高达{" "}
                <span style={{ fontFamily: MONO, color: C.red, fontWeight: 800 }}>1.21 µs</span>
              </div>
            </div>
            <div
              style={{
                padding: "12px 18px",
                borderRadius: 12,
                background: "#0c1324",
                border: `1.5px solid ${K.rev}`,
                fontSize: 20,
                fontWeight: 700,
                color: K.rev,
                textAlign: "center",
              }}
            >
              下一节：反向 P2P 直写卡内存，彻底砍掉读回！
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export const S03: SceneDef = {
  id: "S03",
  title: "踩坑 1：写合并丢包与 DPI 原子写",
  stage: 2,
  source: "单一变量：PEM0 WC vs DPI DMA 64 B · 同门户",
  dur: DUR,
  cues,
  C: Body,
};
