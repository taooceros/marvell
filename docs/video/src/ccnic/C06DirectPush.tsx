import React from "react";
import {
  Arrow,
  C,
  Card,
  Cue,
  FONT,
  MONO,
  PacketStream,
  Pill,
  SceneDef,
  Stage,
  clamp01,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import { CK, HBarChart, HBarRow, HeroStat, RingStrip, VerdictRow } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 10.04, "Datacenter traffic mixes small RPCs (**≤ 256 B**) and bulk payloads (**1–4 KiB**). Pushing 4 KiB line-by-line wastes CPU and clogs BAR4.", "Report four assembles the complete interface for mixed workloads. Pushing four kilobytes line by line wastes host C P U and clogs the B A R four window."], // 0
  [10.39, 26.79, "We **removed Intel DSA** in MRV-120: every commercial NIC ships native **DMA** (`DPI DMA` hits **201.6 M lines/s**, **97%** line rate).", "We removed Intel D S A completely to stay universal. Every commercial P C I express NIC already ships native D M A—on C N one oh six, D P I D M A reaches ninety-seven percent of line rate with zero special C P U accelerators."], // 1
  [27.14, 41.60, "**DirectPush** splits by size: **≤ 256 B** goes inline (**0.885 µs**); **≥ 512 B** pushes one **64 B command** and card **DPI DMA** moves data zero-copy.", "DirectPush splits cleanly by size: up to two hundred fifty-six bytes goes inline in zero point eight eight five microseconds; five hundred twelve bytes and up pushes a single sixty-four byte command for zero-copy card D P I D M A."], // 2
  [41.95, 59.61, "Bare-ring **4 KiB** ablation: native **DPI DMA** cuts host CPU **2.86×** (**46.6% → 16.3%**), zeroes LLC misses, and lifts `Mix-Geo` **7.76 → 18.68 GB/s**.", "Switching four kilobytes from full push to native D P I D M A cuts host C P U two point eight six times—from forty-six point six to sixteen point three percent—zeroes L L C misses, and lifts Mix-Geo from seven point seven six to eighteen point six eight gigabytes per second."], // 3
  [59.96, 76.56, "**Dual credit windows** (`W_small=32`, `W_large=32`) shield 64 B under saturated **18.6 GB/s** 4 KiB: **40.5 ms → 2.75 µs** (**14,701×** HoL isolation).", "Under open-loop mixed loads, splitting small and large credits into two independent windows slashes sixty-four byte tail latency from forty point five milliseconds to two point seven five microseconds—fourteen thousand seven hundred times lower under saturated four kilobyte traffic."], // 4
  [76.91, 88.67, "`98.4%` random cross-page KV in **128 MiB** sees **0.0%** DMA drop (honest note: 64 × 2 MiB hugepages fit inside the hot IOTLB).", "Random cross-page key-value access across one hundred twenty-eight megabytes sees zero D M A degradation—noting honestly that sixty-four two-megabyte pages fit inside the hot I O T L B."], // 5
];
const DUR = 90.07; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Act 0: Cue 0 (Mixed traffic choking single BAR4 push pipe)
  const cue0Op = fade(t, T[0], T[1], 0.5);
  const k0 = prog(t, T[0], 0.7);

  // Act 1: Cue 1 (Intel DSA removed vs Native NIC DPI DMA)
  const cue1Op = fade(t, T[1], T[2], 0.5);
  const k1 = prog(t, T[1], 0.75);
  const k1Strike = prog(t, T[1] + 0.35, 0.65);
  const k1Dpi = prog(t, T[1] + 0.65, 0.75);

  // Act 2: Cues 2..5 (Evolving DirectPush Dual-Path Architecture + Proof Panels)
  const act2Op = prog(t, T[2], 0.5);
  const k2 = prog(t, T[2], 0.75);
  const k2Cards = fade(t, T[2], T[3], 0.45);
  const kShrink = prog(t, T[3], 0.75);
  const k3Panel = fade(t, T[3], T[4], 0.5);
  const k3 = prog(t, T[3], 0.75);
  const k4Panel = prog(t, T[4], 0.5);
  const k4 = prog(t, T[4], 0.8);
  const k4Note = fade(t, T[4], T[5], 0.45);
  const k5 = prog(t, T[5], 0.65);

  // Continuous ambient motion
  const pulse = 0.5 + 0.5 * Math.sin(rt * 6.5);
  const fastTravel = rt * 230;
  const dmaTravel = rt * 195;
  const slowTravel = rt * 85;

  // Interpolated Dual-Path Architecture geometry (full height in Cue 2 -> compact top band in Cues 3..5)
  const archY = lerp(140, 134, kShrink);
  const archH = lerp(404, 304, kShrink);
  const lane1Y = lerp(206, 184, kShrink);
  const laneH = lerp(148, 108, kShrink);
  const laneGap = lerp(24, 16, kShrink);
  const lane2Y = lane1Y + laneH + laneGap;
  const lane1Mid = lane1Y + laneH / 2;
  const lane2Mid = lane2Y + laneH / 2;

  // Cue 3 Chart data: Left (4 KiB Host CPU) & Right (Mix-Geo GB/s)
  const cpuRows: HBarRow[] = [
    {
      label: "Full Direct Push",
      sub: "14.7 LLC miss/pkt",
      value: 46.6,
      valueText: "46.6%",
      color: CK.card,
      k: prog(t, T[3] + 0.25, 0.7),
    },
    {
      label: "DirectPush (DMA)",
      sub: "0.00 LLC miss/pkt",
      value: 16.3,
      valueText: "16.3%",
      color: CK.dma,
      k: prog(t, T[3] + 0.65, 0.7),
      tag: "2.86× less CPU",
    },
  ];

  const mixRows: HBarRow[] = [
    {
      label: "Full Direct Push",
      sub: "64 B p99: 19.5 µs",
      value: 7.76,
      valueText: "7.76 GB/s",
      color: CK.card,
      k: prog(t, T[3] + 0.35, 0.7),
    },
    {
      label: "DirectPush Hybrid",
      sub: "p99 1.50 µs · 2.41×",
      value: 18.68,
      valueText: "18.68 GB/s",
      color: CK.dma,
      k: prog(t, T[3] + 0.75, 0.7),
    },
  ];

  return (
    <>
      <Stage>
        {/* ================= CUE 0: Mixed Traffic Choking Single BAR4 Pipe ================= */}
        {cue0Op > 0.01 && (
          <g opacity={cue0Op}>
            <rect x={60} y={138} width={1800} height={732} rx={18} fill={C.panel} stroke={CK.card} strokeWidth={2.2} />
            <text x={94} y={184} fontSize={28} fontWeight={800} fill={CK.card} fontFamily={FONT}>
              The Mixed-Traffic Problem: Pushing 4 KiB Line-by-Line Clogs BAR4
            </text>
            <text x={94} y={218} fontSize={21} fill={C.muted} fontFamily={MONO}>
              Datacenter workloads combine tiny latency-critical RPCs (64–256 B) with bulk 1–4 KiB payloads
            </text>

            {/* Left: Mixed workload source */}
            <rect x={94} y={258} width={380} height={422} rx={16} fill="#10182d" stroke={CK.host} strokeWidth={2.2} />
            <text x={284} y={298} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.host} fontFamily={FONT}>
              Mixed App Traffic
            </text>

            {/* Small RPC group */}
            <rect x={122} y={326} width={324} height={134} rx={12} fill="#0e2233" stroke={CK.push} strokeWidth={2} />
            <text x={284} y={358} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.push} fontFamily={FONT}>
              Small RPCs (≤ 256 B)
            </text>
            <text x={284} y={388} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={MONO}>
              1–4 cache lines · latency bound
            </text>
            <g transform="translate(174, 408)">
              {[0, 1, 2, 3].map((i) => (
                <rect key={i} x={i * 56} y={0} width={44} height={32} rx={6} fill={`${CK.push}33`} stroke={CK.push} strokeWidth={2} />
              ))}
            </g>

            {/* Large Bulk Payload group */}
            <rect x={122} y={482} width={324} height={172} rx={12} fill="#281326" stroke={CK.dma} strokeWidth={2} />
            <text x={284} y={514} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.dma} fontFamily={FONT}>
              Bulk Payloads (1–4 KiB)
            </text>
            <text x={284} y={544} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={MONO}>
              4 KiB = 64 × 64 B lines!
            </text>
            {/* Visual grid of 32 tiny pink blocks representing 64 lines */}
            <g transform="translate(142, 562)">
              {Array.from({ length: 32 }).map((_, i) => (
                <rect
                  key={i}
                  x={(i % 16) * 18}
                  y={Math.floor(i / 16) * 20}
                  width={14}
                  height={15}
                  rx={3}
                  fill={CK.dma}
                  opacity={0.85}
                />
              ))}
            </g>
            <text x={284} y={634} textAnchor="middle" fontSize={17} fill={CK.card} fontFamily={MONO}>
              64 MOVDIR64B stores per pkt!
            </text>

            {/* Center: Single Choked BAR4 Pipe */}
            <rect x={546} y={322} width={734} height={294} rx={18} fill="#1f1422" stroke={CK.trap} strokeWidth={2.8} />
            <text x={913} y={364} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
              Naive Full-Size Direct Push → Single Choked BAR4 Pipe
            </text>

            {/* Traffic arrows merging into the pipe */}
            <Arrow x1={478} y1={392} x2={542} y2={446} color={CK.push} w={4} k={k0} />
            <Arrow x1={478} y1={566} x2={542} y2={498} color={CK.dma} w={5} k={k0} />

            {/* Clogged BAR4 pipe visual */}
            <rect x={582} y={422} width={662} height={96} rx={14} fill="#120f1c" stroke={CK.trap} strokeWidth={2.2} />
            {/* Wall of pink 4 KiB chunks blocking tiny cyan RPC packets */}
            <PacketStream
              pts={[
                [598, 470],
                [1224, 470],
              ]}
              travel={slowTravel}
              gap={34}
              size={20}
              color={CK.dma}
              colorAt={(idx) => (idx % 9 === 0 ? CK.push : CK.dma)}
              opacity={cue0Op}
            />
            <text x={913} y={404} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              64 B TLP header (24 B) paid 64× per 4 KiB pkt
            </text>
            <text x={913} y={554} textAnchor="middle" fontSize={20} fontWeight={700} fill={CK.trap} fontFamily={MONO}>
              Head-of-line blocking + 14.7 host LLC misses/pkt
            </text>
            <text x={913} y={588} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={FONT}>
              Host core stalls copying 4 KiB through BAR4 line by line
            </text>

            {/* Right: Consequences Card Stack (SVG) */}
            <Arrow x1={1288} y1={470} x2={1354} y2={470} color={CK.trap} w={4.5} k={k0} />
            {[
              { y: 274, title: "46.6% Host CPU", sub: "1 core @ 1 M/s 4 KiB push", col: CK.card },
              { y: 410, title: "7.76 GB/s Collapse", sub: "Mix-Geo throughput starved", col: CK.trap },
              { y: 546, title: "19.5 µs 64 B p99", sub: "Small RPCs trapped behind 4K", col: CK.trap },
            ].map((item, idx) => {
              const ki = prog(t, T[0] + 0.3 + idx * 0.25, 0.55);
              return (
                <g key={item.title} opacity={ki}>
                  <rect x={1368} y={item.y} width={456} height={116} rx={14} fill="#14182b" stroke={item.col} strokeWidth={2.2} />
                  <text x={1396} y={item.y + 50} fontSize={30} fontWeight={800} fill={item.col} fontFamily={MONO}>
                    {item.title}
                  </text>
                  <text x={1396} y={item.y + 88} fontSize={20} fill={C.muted} fontFamily={FONT}>
                    {item.sub}
                  </text>
                </g>
              );
            })}

            {/* Bottom insight bar */}
            <rect x={94} y={716} width={1730} height={118} rx={14} fill="#0e1527" stroke={C.line} strokeWidth={1.6} />
            <text x={959} y={762} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Direct push shines for small messages (≤ 256 B), NOT bulk payloads (≥ 512 B)!
            </text>
            <text x={959} y={804} textAnchor="middle" fontSize={20} fill={CK.push} fontFamily={MONO}>
              Solution: Split by size — inline push for small RPCs, native NIC DMA for large payloads
            </text>
          </g>
        )}

        {/* ================= CUE 1: Intel DSA Removed vs Native NIC DPI DMA ================= */}
        {cue1Op > 0.01 && (
          <g opacity={cue1Op}>
            {/* Left Panel: Intel DSA fading out with clean red strikethrough */}
            <g opacity={lerp(1, 0.48, k1Strike)}>
              <rect x={60} y={142} width={730} height={724} rx={18} fill="#141624" stroke={CK.trap} strokeWidth={2.2} strokeDasharray="10 7" />
              <text x={425} y={200} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.muted} fontFamily={FONT}>
                Host CPU Accelerator
              </text>

              {/* Intel DSA chip die */}
              <rect x={225} y={272} width={400} height={240} rx={20} fill="#1a1f32" stroke={C.dim} strokeWidth={3} />
              {/* Chip pins */}
              {Array.from({ length: 6 }).map((_, i) => (
                <g key={i}>
                  <rect x={265 + i * 58} y={252} width={22} height={20} rx={3} fill={C.dim} />
                  <rect x={265 + i * 58} y={512} width={22} height={20} rx={3} fill={C.dim} />
                </g>
              ))}
              <text x={425} y={376} textAnchor="middle" fontSize={44} fontWeight={800} fill={C.muted} fontFamily={MONO}>
                Intel DSA
              </text>
              <text x={425} y={424} textAnchor="middle" fontSize={21} fill={C.dim} fontFamily={FONT}>
                Data Streaming Accelerator
              </text>
              <text x={425} y={462} textAnchor="middle" fontSize={18} fill={C.dim} fontFamily={MONO}>
                Xeon-only special CPU offload
              </text>

              <text x={425} y={626} textAnchor="middle" fontSize={22} fill={C.muted} fontFamily={FONT}>
                Not universal across AMD / ARM / older x86
              </text>
              <text x={425} y={664} textAnchor="middle" fontSize={20} fill={C.dim} fontFamily={MONO}>
                Unnecessary when the NIC already has DMA!
              </text>
            </g>

            {/* Crisp Diagonal Red Strikethrough & Badge on top of Intel DSA (full opacity!) */}
            {k1Strike > 0.01 && (
              <g opacity={k1Strike}>
                <line
                  x1={190}
                  y1={250}
                  x2={lerp(190, 660, k1Strike)}
                  y2={lerp(250, 535, k1Strike)}
                  stroke={CK.trap}
                  strokeWidth={10}
                  strokeLinecap="round"
                />
                <line
                  x1={660}
                  y1={250}
                  x2={lerp(660, 190, k1Strike)}
                  y2={lerp(250, 535, k1Strike)}
                  stroke={CK.trap}
                  strokeWidth={10}
                  strokeLinecap="round"
                />
                <rect x={115} y={718} width={620} height={86} rx={14} fill="#361218" stroke={CK.trap} strokeWidth={2.6} />
                <text x={425} y={754} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
                  Removed (MRV-120)
                </text>
                <text x={425} y={786} textAnchor="middle" fontSize={20} fontWeight={700} fill={C.text} fontFamily={MONO}>
                  Zero Special CPU Accelerator Required
                </text>
              </g>
            )}

            {/* Center transition arrow */}
            <Arrow x1={804} y1={504} x2={876} y2={504} color={CK.dma} w={6} k={k1Dpi} />

            {/* Right Panel: Commercial NIC Native DPI DMA Engine (Glowing Pink) */}
            <g opacity={k1Dpi}>
              <rect x={892} y={142} width={968} height={724} rx={18} fill={C.panel} stroke={CK.dma} strokeWidth={2.8} />
              <text x={928} y={194} fontSize={28} fontWeight={800} fill={CK.dma} fontFamily={FONT}>
                Built Into Every Commercial NIC: Native DPI DMA
              </text>
              <text x={928} y={226} fontSize={20} fill={C.muted} fontFamily={MONO}>
                Marvell CN106xx onboard DMA · 128–512 B PCIe TLPs · 0 host copy
              </text>

              {/* NIC die box with glowing DPI DMA core */}
              <rect x={928} y={258} width={896} height={310} rx={16} fill="#141024" stroke={CK.card} strokeWidth={2.2} />
              <text x={954} y={294} fontSize={22} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                COMMERCIAL NIC / DPU · CN106xx (PCIe Gen5 ×4)
              </text>

              {/* Host Pool mini box on left of internal diagram */}
              <rect x={954} y={334} width={220} height={196} rx={12} fill="#121a30" stroke={CK.host} strokeWidth={2} />
              <text x={1064} y={386} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.host} fontFamily={FONT}>
                Host RAM
              </text>
              <text x={1064} y={420} textAnchor="middle" fontSize={19} fill={C.text} fontFamily={MONO}>
                128 MiB Pool
              </text>
              <text x={1064} y={452} textAnchor="middle" fontSize={18} fill={CK.ok} fontFamily={MONO}>
                HW IOMMU ON
              </text>
              <text x={1064} y={484} textAnchor="middle" fontSize={17} fill={C.muted} fontFamily={MONO}>
                Zero host memcpy
              </text>

              {/* Glowing DPI DMA Engine center box */}
              <rect
                x={1302 - 6}
                y={334 - 6}
                width={262}
                height={208}
                rx={18}
                fill={CK.dma}
                opacity={0.14 + 0.14 * pulse}
              />
              <rect x={1302} y={334} width={250} height={196} rx={14} fill="#2b1228" stroke={CK.dma} strokeWidth={3} />
              <text x={1427} y={386} textAnchor="middle" fontSize={30} fontWeight={800} fill={CK.dma} fontFamily={MONO}>
                DPI DMA
              </text>
              <text x={1427} y={422} textAnchor="middle" fontSize={20} fontWeight={700} fill={C.text} fontFamily={FONT}>
                Native NIC Engine
              </text>
              <text x={1427} y={456} textAnchor="middle" fontSize={18} fill={CK.ok} fontFamily={MONO}>
                Fetch: 187.6 M/s
              </text>
              <text x={1427} y={486} textAnchor="middle" fontSize={18} fill={CK.dma} fontFamily={MONO}>
                128–512 B TLPs
              </text>

              {/* Card L3/DRAM target box */}
              <rect x={1624} y={334} width={174} height={196} rx={12} fill="#1b1526" stroke={CK.card} strokeWidth={2} />
              <text x={1711} y={404} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.card} fontFamily={FONT}>
                Card L3 /
              </text>
              <text x={1711} y={436} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.card} fontFamily={FONT}>
                NIX TX
              </text>
              <text x={1711} y={474} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                18.83 GB/s
              </text>

              {/* High-speed pink DMA streams */}
              <line x1={1182} y1={404} x2={1294} y2={404} stroke={CK.dma} strokeWidth={8} opacity={0.22} />
              <Arrow x1={1182} y1={404} x2={1294} y2={404} color={CK.dma} w={4} k={k1Dpi} />
              <PacketStream pts={[[1186, 404], [1288, 404]]} travel={dmaTravel} gap={36} size={11} color={CK.dma} opacity={cue1Op * k1Dpi} />

              <line x1={1294} y1={460} x2={1182} y2={460} stroke={CK.dma} strokeWidth={8} opacity={0.22} />
              <Arrow x1={1294} y1={460} x2={1182} y2={460} color={CK.dma} w={4} k={k1Dpi} />
              <PacketStream pts={[[1288, 460], [1186, 460]]} travel={dmaTravel + 18} gap={36} size={11} color={CK.dma} opacity={cue1Op * k1Dpi} />

              <Arrow x1={1558} y1={432} x2={1618} y2={432} color={CK.card} w={4} k={k1Dpi} />
            </g>
          </g>
        )}

        {/* ================= ACT 2: Cues 2..5 — DirectPush Dual-Path Architecture ================= */}
        {act2Op > 0.01 && (
          <g opacity={act2Op}>
            {/* Architecture Outer Container (morphs from full height in Cue 2 to top band in Cues 3..5) */}
            <rect x={60} y={archY} width={1800} height={archH} rx={18} fill={C.panel} stroke={CK.push} strokeWidth={2.2} />
            <text x={92} y={archY + 38} fontSize={lerp(27, 23, kShrink)} fontWeight={800} fill={C.text} fontFamily={FONT}>
              DirectPush Dual-Path Architecture · Size-Split Hybrid Interface
            </text>
            <text x={1828} y={archY + 38} textAnchor="end" fontSize={lerp(20, 18, kShrink)} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
              Zero Intel DSA · Zero Hidden Cores · HW IOMMU ON
            </text>

            {/* -------- UPPER CYAN LANE: <= 256 B Small Inline Path -------- */}
            <rect x={88} y={lane1Y} width={1744} height={laneH} rx={14} fill="#0c1928" stroke={CK.push} strokeWidth={2.2} />
            <rect x={88} y={lane1Y} width={10} height={laneH} rx={5} fill={CK.push} />
            {/* Threshold pill box */}
            <rect x={114} y={lane1Mid - 34} width={174} height={68} rx={10} fill="#102d42" stroke={CK.push} strokeWidth={2} />
            <text x={201} y={lane1Mid - 8} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              ≤ 256 B
            </text>
            <text x={201} y={lane1Mid + 18} textAnchor="middle" fontSize={17} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Small Path
            </text>

            {/* Nodes in Small Path: Host L1D -> MOVDIR64B -> BAR4 -> Card L3 */}
            {[
              { x: 326, w: 184, t1: "Host L1D", t2: "1 hot slot" },
              { x: 616, w: 224, t1: "MOVDIR64B", t2: "64 B by value" },
              { x: 946, w: 196, t1: "PCIe BAR4", t2: "Lossless MWr" },
              { x: 1248, w: 214, t1: "Card 48M L3", t2: "99.94% hit" },
            ].map((n) => (
              <g key={n.t1}>
                <rect x={n.x} y={lane1Mid - 34} width={n.w} height={68} rx={10} fill="#132136" stroke={CK.push} strokeWidth={1.8} />
                <text x={n.x + n.w / 2} y={lane1Mid - 8} textAnchor="middle" fontSize={21} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  {n.t1}
                </text>
                <text x={n.x + n.w / 2} y={lane1Mid + 18} textAnchor="middle" fontSize={17} fill={CK.push} fontFamily={MONO}>
                  {n.t2}
                </text>
              </g>
            ))}

            {/* Animated Cyan Streams between Small Path boxes */}
            {[
              [516, 610],
              [846, 940],
              [1148, 1242],
            ].map(([x1, x2], idx) => (
              <g key={idx}>
                <line x1={x1} y1={lane1Mid} x2={x2} y2={lane1Mid} stroke={CK.push} strokeWidth={7} opacity={0.18} />
                <Arrow x1={x1} y1={lane1Mid} x2={x2} y2={lane1Mid} color={CK.push} w={3.5} k={k2} />
                <PacketStream
                  pts={[
                    [x1 + 4, lane1Mid],
                    [x2 - 8, lane1Mid],
                  ]}
                  travel={fastTravel + idx * 25}
                  gap={48}
                  size={10}
                  color={CK.push}
                  opacity={act2Op}
                />
              </g>
            ))}

            {/* Small Path metric badge */}
            <rect x={1482} y={lane1Mid - 36} width={336} height={72} rx={12} fill="#0e2a3d" stroke={CK.push} strokeWidth={2.2} />
            <text x={1650} y={lane1Mid - 8} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              0.885 µs RTT
            </text>
            <text x={1650} y={lane1Mid + 18} textAnchor="middle" fontSize={16} fill={C.muted} fontFamily={MONO}>
              vs SDP 4.6–5.0 µs · 0 TX pool
            </text>

            {/* -------- LOWER PINK LANE: >= 512 B Large Native DPI DMA Path -------- */}
            <rect x={88} y={lane2Y} width={1744} height={laneH} rx={14} fill="#1f1122" stroke={CK.dma} strokeWidth={2.2} />
            <rect x={88} y={lane2Y} width={10} height={laneH} rx={5} fill={CK.dma} />
            {/* Threshold pill box */}
            <rect x={114} y={lane2Mid - 34} width={174} height={68} rx={10} fill="#35142f" stroke={CK.dma} strokeWidth={2} />
            <text x={201} y={lane2Mid - 8} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.dma} fontFamily={MONO}>
              ≥ 512 B
            </text>
            <text x={201} y={lane2Mid + 18} textAnchor="middle" fontSize={17} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Large Path
            </text>

            {/* Nodes in Large Path */}
            {[
              { x: 326, w: 204, t1: "1 × 64 B Cmd", t2: "Inline descriptor" },
              { x: 636, w: 224, t1: "Card DPI DMA", t2: "201.6 M lines/s" },
              { x: 966, w: 220, t1: "128 MiB Pool", t2: "Zero-copy pull/put" },
              { x: 1292, w: 170, t1: "0 LLC Miss", t2: "0 host memcpy" },
            ].map((n) => (
              <g key={n.t1}>
                <rect x={n.x} y={lane2Mid - 34} width={n.w} height={68} rx={10} fill="#251329" stroke={CK.dma} strokeWidth={1.8} />
                <text x={n.x + n.w / 2} y={lane2Mid - 8} textAnchor="middle" fontSize={21} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  {n.t1}
                </text>
                <text x={n.x + n.w / 2} y={lane2Mid + 18} textAnchor="middle" fontSize={17} fill={CK.dma} fontFamily={MONO}>
                  {n.t2}
                </text>
              </g>
            ))}

            {/* Animated Pink Streams between Large Path boxes */}
            {[
              [536, 630],
              [866, 960],
              [1192, 1286],
            ].map(([x1, x2], idx) => (
              <g key={idx}>
                <line x1={x1} y1={lane2Mid} x2={x2} y2={lane2Mid} stroke={CK.dma} strokeWidth={7} opacity={0.18} />
                <Arrow x1={x1} y1={lane2Mid} x2={x2} y2={lane2Mid} color={CK.dma} w={3.5} k={k2} />
                <PacketStream
                  pts={[
                    [x1 + 4, lane2Mid],
                    [x2 - 8, lane2Mid],
                  ]}
                  travel={dmaTravel + idx * 30}
                  gap={48}
                  size={11}
                  color={CK.dma}
                  opacity={act2Op}
                />
              </g>
            ))}

            {/* Large Path metric badge */}
            <rect x={1482} y={lane2Mid - 36} width={336} height={72} rx={12} fill="#36132f" stroke={CK.dma} strokeWidth={2.2} />
            <text x={1650} y={lane2Mid - 8} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.dma} fontFamily={MONO}>
              18.83 GB/s bidir
            </text>
            <text x={1650} y={lane2Mid + 18} textAnchor="middle" fontSize={16} fill={C.muted} fontFamily={MONO}>
              128–512 B TLPs · 97% line rate
            </text>

            {/* ================= CUE 3 BOTTOM: MRV-117 Comparison Charts ================= */}
            {k3Panel > 0.01 && (
              <g opacity={k3Panel}>
                {/* Left Chart Panel: 4 KiB @ 1 M/s Host CPU */}
                <rect x={60} y={458} width={882} height={414} rx={18} fill={C.panel} stroke={CK.dma} strokeWidth={2.2} />
                <text x={92} y={500} fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  MRV-117 · Bare-Ring 4 KiB @ 1.0 M/s Host CPU
                </text>
                <text x={92} y={528} fontSize={19} fill={C.muted} fontFamily={MONO}>
                  Switching large payloads from full-size push to native DPI DMA
                </text>
                <HBarChart x={84} y={564} w={832} labelW={228} barH={52} gap={26} max={55} rows={cpuRows} />

                {/* Right Chart Panel: Mix-Geo Throughput & 64 B p99 */}
                <rect x={972} y={458} width={888} height={414} rx={18} fill={C.panel} stroke={CK.push} strokeWidth={2.2} />
                <text x={1004} y={500} fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  MRV-117 · Mix-Geo Throughput &amp; 64 B Tail
                </text>
                <text x={1004} y={528} fontSize={19} fill={C.muted} fontFamily={MONO}>
                  Mixed large-heavy traffic: full push vs DirectPush hybrid
                </text>
                <HBarChart x={996} y={564} w={836} labelW={228} barH={52} gap={26} max={24} rows={mixRows} />
              </g>
            )}

            {/* ================= CUES 4..5 BOTTOM: Dual Credit Windows HoL Isolation ================= */}
            {k4Panel > 0.01 && (
              <g opacity={k4Panel}>
                {/* Left Panel: Shared Window -> Split Dual Credit Windows */}
                <rect x={60} y={458} width={910} height={414} rx={18} fill={C.panel} stroke={CK.push} strokeWidth={2.2} />
                <text x={92} y={498} fontSize={25} fontWeight={800} fill={CK.push} fontFamily={FONT}>
                  MRV-127 · Dual Credit Windows Eliminate HoL Blocking
                </text>

                {/* Shared Window (HoL Blocked) */}
                <rect x={88} y={516} width={854} height={116} rx={12} fill="#1d1320" stroke={CK.trap} strokeWidth={1.8} />
                <text x={108} y={544} fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
                  Shared Window: 4 KiB hogs 91% of slots → 64 B trapped!
                </text>
                <text x={922} y={544} textAnchor="end" fontSize={20} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  64 B p99 = 40.5 ms
                </text>
                <RingStrip
                  x={108}
                  y={562}
                  n={13}
                  cellW={46}
                  cellH={48}
                  gap={7}
                  state={(i) => (i < 11 ? "dma" : i === 11 ? "trap" : "empty")}
                  highlightRange={[0, 11]}
                  highlightColor={CK.trap}
                />
                <text x={868} y={591} textAnchor="middle" fontSize={18} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  BLOCKED
                </text>

                {/* Morph arrow */}
                <Arrow x1={515} y1={636} x2={515} y2={672} color={CK.ok} w={4.5} head={12} k={k4} />
                <text x={534} y={660} fontSize={18} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  Split into independent credit pools
                </text>

                {/* Dual Independent Windows */}
                <rect x={88} y={678} width={416} height={174} rx={12} fill="#0e1f2f" stroke={CK.push} strokeWidth={2.2} />
                <text x={108} y={708} fontSize={20} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                  W_small = 32 (≤ 256 B)
                </text>
                <RingStrip
                  x={108}
                  y={728}
                  n={7}
                  cellW={46}
                  cellH={46}
                  gap={7}
                  state={(i) => (i < 5 ? "hot" : "empty")}
                  highlightRange={[0, 6]}
                  highlightColor={CK.push}
                />
                <text x={108} y={804} fontSize={18} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
                  256 B: 5.00 Mreq/s (+11% vs SDP)
                </text>
                <text x={108} y={832} fontSize={17} fill={C.muted} fontFamily={MONO}>
                  Zero HoL · dedicated fast lane
                </text>

                <rect x={524} y={678} width={418} height={174} rx={12} fill="#231227" stroke={CK.dma} strokeWidth={2.2} />
                <text x={544} y={708} fontSize={20} fontWeight={800} fill={CK.dma} fontFamily={MONO}>
                  W_large = 32 + C_line = 32
                </text>
                <RingStrip
                  x={544}
                  y={728}
                  n={7}
                  cellW={46}
                  cellH={46}
                  gap={7}
                  state={(i) => (i < 6 ? "dma" : "empty")}
                  highlightRange={[0, 6]}
                  highlightColor={CK.dma}
                />
                <text x={544} y={804} fontSize={18} fontWeight={700} fill={CK.dma} fontFamily={MONO}>
                  512 B DMA: +3.47× speedup
                </text>
                <text x={544} y={832} fontSize={17} fill={C.muted} fontFamily={MONO}>
                  18.6 GB/s 4 KiB saturated concurrently
                </text>

                {/* Right Top Panel: Dramatic Log-Scale 40.5 ms -> 2.75 us Comparison */}
                <rect x={996} y={458} width={864} height={224} rx={18} fill={C.panel} stroke={CK.ok} strokeWidth={2.4} />
                <text x={1026} y={496} fontSize={23} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
                  64 B p99 Under Saturated 18.6 GB/s 4 KiB (log10 scale)
                </text>

                {/* Shared Window bar: 40,500 us (~full width) */}
                <text x={1026} y={536} fontSize={19} fontWeight={700} fill={C.text} fontFamily={FONT}>
                  Shared Window
                </text>
                <rect x={1196} y={514} width={450} height={34} rx={8} fill="#161b2f" stroke={C.line} strokeWidth={1.2} />
                <rect x={1196} y={514} width={450 * prog(t, T[4] + 0.25, 0.7)} height={34} rx={8} fill={CK.trap} opacity={0.92} />
                <text x={1658} y={537} fontSize={22} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  40.5 ms
                </text>

                {/* Dual Window bar: 2.75 us (tiny sliver on log scale!) */}
                <text x={1026} y={588} fontSize={19} fontWeight={700} fill={C.text} fontFamily={FONT}>
                  Dual Windows
                </text>
                <rect x={1196} y={566} width={450} height={34} rx={8} fill="#161b2f" stroke={C.line} strokeWidth={1.2} />
                <rect x={1196} y={566} width={42 * prog(t, T[4] + 0.65, 0.7)} height={34} rx={8} fill={CK.ok} opacity={0.95} />
                <text x={1252} y={589} fontSize={23} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  2.75 µs   ← 14,701× lower!
                </text>

                <text x={1026} y={646} fontSize={19} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                  Full 8.00 Mreq/s 64 B RPCs preserved under 18.6 GB/s 4 KiB
                </text>
              </g>
            )}
          </g>
        )}
      </Stage>

      {/* ================= HTML OVERLAYS (OUTSIDE <Stage>) ================= */}

      {/* Cue 1 HeroStats for Native DPI DMA */}
      {cue1Op * k1Dpi > 0.01 && (
        <>
          <HeroStat
            x={928}
            y={594}
            w={438}
            h={168}
            value="201.6 M/s"
            label="Native CN106 DPI DMA"
            sub="97% Gen5 ×4 · fetch 187.6 M/s"
            color={CK.dma}
            opacity={cue1Op * k1Dpi}
            dy={lerp(18, 0, k1Dpi)}
          />
          <HeroStat
            x={1386}
            y={594}
            w={438}
            h={168}
            value="0 DSA"
            label="Universal Commercial NIC"
            sub="No special CPU offload · universal"
            color={CK.ok}
            opacity={cue1Op * k1Dpi}
            dy={lerp(18, 0, k1Dpi)}
          />
          <Pill
            x={928}
            y={792}
            color={CK.dma}
            size={21}
            style={{ opacity: cue1Op * k1Dpi, fontFamily: MONO }}
          >
            Amortizes 24 B MWr / 20 B CplD headers across 128–512 B TLPs
          </Pill>
        </>
      )}

      {/* Cue 2 Bottom Explanatory Cards (fade out at T[3]) */}
      {act2Op * k2Cards > 0.01 && (
        <>
          <Card
            x={60}
            y={570}
            w={886}
            h={296}
            border={CK.push}
            title="Small Path (≤ 256 B) · Cache-Shortcut Direct Push"
            titleColor={CK.push}
            style={{ opacity: act2Op * k2Cards, transform: `translateY(${lerp(18, 0, k2)}px)` }}
          >
            <div style={{ fontSize: 22, color: C.text, lineHeight: 1.45 }}>
              Host copies payload directly by value via <span style={{ fontFamily: MONO, color: CK.push, fontWeight: 700 }}>MOVDIR64B</span> into card BAR4 (48 MiB L3).
            </div>
            <div style={{ fontSize: 21, color: CK.ok, fontFamily: MONO, fontWeight: 800, marginTop: 18 }}>
              64 B RTT = 0.885 µs (vs SDP 4.6–5.0 µs) · 0 TX DMA reads
            </div>
            <div style={{ fontSize: 19, color: C.muted, fontFamily: MONO, marginTop: 10 }}>
              Immediate in-place buffer reuse · zero descriptor ring overhead
            </div>
          </Card>

          <Card
            x={974}
            y={570}
            w={886}
            h={296}
            border={CK.dma}
            title="Large Path (≥ 512 B / 1 KiB) · 1-Line Cmd + Native DPI DMA"
            titleColor={CK.dma}
            style={{ opacity: act2Op * k2Cards, transform: `translateY(${lerp(18, 0, k2)}px)` }}
          >
            <div style={{ fontSize: 22, color: C.text, lineHeight: 1.45 }}>
              Host pushes a single <span style={{ fontFamily: MONO, color: CK.dma, fontWeight: 700 }}>64 B command</span>; card&apos;s native <span style={{ fontFamily: MONO, color: CK.dma, fontWeight: 700 }}>DPI DMA</span> reads/writes the 128 MiB host retention pool directly.
            </div>
            <div style={{ fontSize: 21, color: CK.dma, fontFamily: MONO, fontWeight: 800, marginTop: 18 }}>
              18.83 GB/s bidirectional · 0 host memcpy · 0 LLC pollution
            </div>
            <div style={{ fontSize: 19, color: C.muted, fontFamily: MONO, marginTop: 10 }}>
              Full 128–512 B PCIe TLP efficiency without clogging BAR4
            </div>
          </Card>
        </>
      )}

      {/* Cue 3 Bottom Verdict Rows inside the two MRV-117 Panels */}
      {act2Op * k3Panel > 0.01 && (
        <>
          <VerdictRow
            x={92}
            y={732}
            w={818}
            badge="2.86× CPU"
            badgeColor={CK.ok}
            title="Host CPU drops 46.6% → 16.3% (0.00 LLC miss/pkt)"
            detail="18.83 GB/s bidir via native CN106 DPI DMA (0 host memcpy)"
            opacity={act2Op * k3Panel * prog(t, T[3] + 0.85, 0.5)}
            dy={lerp(14, 0, k3)}
          />
          <VerdictRow
            x={1004}
            y={732}
            w={824}
            badge="2.16–3.13×"
            badgeColor={CK.dma}
            title="Beats SDP 2.16× (Mix-Geo) to 3.13× (Mix-Ads)"
            detail="Mix-Geo +2.41× vs full push · 64 B p99 cut 13× (19.5 → 1.50 µs)"
            opacity={act2Op * k3Panel * prog(t, T[3] + 1.0, 0.5)}
            dy={lerp(14, 0, k3)}
          />
        </>
      )}

      {/* Cue 4 Bottom-Right Takeaway Row (crossfades cleanly into Cue 5 KV card at T[5]) */}
      {act2Op * k4Panel * k4Note > 0.01 && (
        <VerdictRow
          x={996}
          y={708}
          w={864}
          badge="14,701×"
          badgeColor={CK.ok}
          title="Dual credit windows isolate 64 B RPCs from 4 KiB saturation"
          detail="W_small=32 · W_large=32 · C_line=32 → 256 B +11% vs SDP, 512 B DMA +3.47×"
          opacity={act2Op * k4Panel * k4Note * prog(t, T[4] + 0.8, 0.5)}
          dy={lerp(14, 0, prog(t, T[4] + 0.8, 0.5))}
        />
      )}

      {/* Cue 5 Bottom-Right Verification Card: Random Cross-Page 128 MiB KV + Honest IOTLB Note */}
      {act2Op * k5 > 0.01 && (
        <Card
          x={996}
          y={696}
          w={864}
          h={176}
          border={CK.ok}
          style={{
            opacity: act2Op * k5,
            transform: `translateY(${lerp(16, 0, k5)}px)`,
            padding: "16px 22px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
            <div style={{ fontSize: 22, fontWeight: 800, color: C.text }}>
              128 MiB Random KV (98.4% cross-page)
            </div>
            <div
              style={{
                fontSize: 22,
                fontWeight: 800,
                color: CK.ok,
                fontFamily: MONO,
                background: `${CK.ok}1f`,
                border: `1.5px solid ${CK.ok}`,
                borderRadius: 10,
                padding: "4px 14px",
                whiteSpace: "nowrap",
              }}
            >
              0.0% DMA Drop
            </div>
          </div>
          <div
            style={{
              marginTop: 14,
              padding: "10px 16px",
              borderRadius: 10,
              background: "rgba(249, 199, 79, 0.10)",
              border: `1.5px solid ${CK.cxl}88`,
              fontSize: 19,
              fontWeight: 600,
              color: CK.cxl,
              fontFamily: MONO,
            }}
          >
            Scope note: 64 × 2 MiB hugepages fit inside hot IOTLB
          </div>
        </Card>
      )}
    </>
  );
}

export const C06: SceneDef = {
  id: "C06",
  title: "4 \u00b7 DirectPush: Hybrid + Native DMA",
  stage: 3,
  source: "MRV-151 \u00b7 MRV-117 (333 runs) \u00b7 MRV-127 (228 runs) \u00b7 Zero DSA",
  dur: DUR,
  cues,
  C: Body,
};
