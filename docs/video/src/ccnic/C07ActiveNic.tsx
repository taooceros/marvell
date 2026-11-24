import React from "react";
import {
  C,
  Cue,
  FONT,
  MONO,
  PacketStream,
  Pt,
  SceneDef,
  Stage,
  clamp01,
  countUp,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import { BoundaryBar, CK, HBarChart, HeroStat, QuestionBanner } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 9.50, "**Question 6**: What about the **receive side** during **100 M/s** traffic bursts—won't bursting into host rings thrash **host LLC** and **DRAM**?", "Question six: what about the receive side when a hundred million packet per second burst hits? Won't pouring bursts into host rings thrash host caches and D RAM?"], // 0
  [9.85, 21.11, "**Passive NIC (Scheme A)** dumps bursts into a **128 MiB** host ring—blowing past the **8 MiB** DDIO window and **60 MiB** host LLC.", "A passive NIC dumps incoming bursts straight into a one hundred twenty-eight megabyte host ring, overflowing the eight megabyte D D I O window and the entire sixty megabyte host cache."], // 1
  [21.46, 33.92, "**Active NIC (Scheme B)** holds burst backlog on the **NIC**; the host keeps only a **256 KiB hot ring**, refilled in place as credits return.", "Our active NIC flips that around. Backlog stays buffered on the NIC itself, while the host keeps only a tiny two hundred fifty-six kilobyte hot ring—permanently cache-resident—refilled in place as credits return."], // 2
  [34.27, 44.71, "Zero-backlog overhead is practically zero: **+6 ns** on idle RTT (**0.887 → 0.893 µs**), zero concurrency loss, **176.9 M/s** streaming.", "With no burst, credit checks cost just six nanoseconds on idle latency, zero loss under concurrency, and one hundred seventy-six point nine million per second streaming."], // 3
  [45.06, 61.22, "At **128 MiB** bursts: host LLC misses drop **91.4% → 0.0%**, DDIO climbs **16.3% → 100%**, and **host DRAM drops −98.9%** (**972 → 11 MiB**).", "Under one hundred twenty-eight megabyte bursts, host L L C read misses plunge from ninety-one point four percent to zero, D D I O stays at one hundred percent, and host D RAM traffic drops ninety-eight point nine percent—from nine hundred seventy-two down to eleven megabytes."], // 4
  [61.57, 81.47, "**Conservation law**: bursts **≤ 32 MiB** sit inside CN106's **48 MiB L3** (~0 system DRAM); **> 48 MiB** cleanly offloads to **card DDR5**.", "Dual-side counters reveal clean conservation: bursts up to thirty-two megabytes fit in the card's forty-eight megabyte L three cache, keeping system D RAM traffic near zero. Above forty-eight megabytes, the NIC's dedicated six-channel D D R five absorbs the spill, keeping host memory traffic minimal at about eleven megabytes."], // 5
  [81.82, 98.94, "Refill stays **0.91 µs** (`1.05–1.16 µs` cold) and 128 MiB drain wins **+8.4%**. **Trade-off**: 1 card core drains `1–32 MiB` bursts slightly slower.", "Refill stays sub-microsecond—zero point nine one microseconds sequentially, up to one point one six cold—while one hundred twenty-eight megabyte drain wins eight point four percent. Honestly, one card core drains mid-size bursts slightly slower before winning past forty-eight megabytes."], // 6
];
const DUR = 100.34; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();
  const pulse = 0.5 + 0.5 * Math.sin(rt * 4.2);

  // Beat opacities (clean crossfades with zero sequential text collisions)
  const k0 = fade(t, T[0], T[1] - 0.1, 0.45);
  const kA = fade(t, T[1] + 0.15, T[3] - 0.1, 0.45); // Scheme A visible in Cue 1 & Cue 2
  const kB = fade(t, T[2], T[3] - 0.1, 0.5); // Scheme B reveals in Cue 2
  const k3 = fade(t, T[3] + 0.15, T[4] - 0.1, 0.45);
  const k4 = fade(t, T[4] + 0.15, T[5] - 0.1, 0.45);
  const k5 = fade(t, T[5] + 0.15, T[6] - 0.1, 0.45);
  const k6 = fade(t, T[6] + 0.15, DUR + 5, 0.45);

  // Cue 0: burst wave route
  const q0Route: Pt[] = [
    [515, 575],
    [1205, 575],
  ];

  // Cue 1-2: Scheme A & B routes
  const aPcieRoute: Pt[] = [
    [290, 410],
    [435, 410],
  ];
  const aSpillRoute: Pt[] = [
    [662, 608],
    [662, 668],
  ];
  const bRxRoute: Pt[] = [
    [1365, 365],
    [1520, 365],
  ];
  const bCredRoute: Pt[] = [
    [1520, 445],
    [1365, 445],
  ];
  const bCardSpill: Pt[] = [
    [1195, 468],
    [1195, 530],
  ];

  // Cue 3: steady loop routes + count-ups
  const c3Stream = countUp(t, T[3] + 0.2, 1.2, 176.9, 1);
  const c3RxRoute: Pt[] = [
    [520, 665],
    [1340, 665],
  ];
  const c3CredRoute: Pt[] = [
    [1340, 725],
    [520, 725],
  ];

  // Cue 4: 128 MiB burst bars & hero count-up
  const k4Bar1 = prog(t, T[4] + 0.35, 0.9);
  const k4Bar2 = prog(t, T[4] + 0.6, 0.9);
  const k4Bar3 = prog(t, T[4] + 0.85, 0.9);
  const dramDrop = countUp(t, T[4] + 0.45, 1.25, 98.9, 1);

  // Cue 5: conservation diagram
  const k5L = prog(t, T[5] + 0.25, 0.7);
  const k5R = prog(t, T[5] + 0.65, 0.7);
  const r2SpillRoute: Pt[] = [
    [1290, 470],
    [1435, 470],
  ];

  // Cue 6: refill & drain count-ups
  const c6Refill = countUp(t, T[6] + 0.25, 1.1, 0.91, 2);
  const c6Gain = countUp(t, T[6] + 0.45, 1.1, 8.4, 1);

  return (
    <>
      <Stage>
        {/* ===================== CUE 0: RX BURST THREAT ===================== */}
        {k0 > 0.005 && (
          <g opacity={k0}>
            {/* Left: 100 M/s RX Burst Source */}
            <rect
              x={155}
              y={455}
              width={360}
              height={240}
              rx={20}
              fill="#141b2e"
              stroke={CK.cxl}
              strokeWidth={2.5}
            />
            <text x={335} y={498} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              100 M/s RX BURST
            </text>
            <text x={335} y={530} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={MONO}>
              64 KiB → 128 MiB
            </text>
            {/* Animated burst wave bars */}
            {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => {
              const phase = (rt * 3.8 + i * 0.55) % (Math.PI * 2);
              const h = Math.max(16, 26 + 56 * Math.abs(Math.sin(phase)));
              const bx = 200 + i * 32;
              return (
                <rect
                  key={i}
                  x={bx}
                  y={665 - h}
                  width={20}
                  height={h}
                  rx={5}
                  fill={i >= 5 ? CK.trap : CK.cxl}
                  opacity={0.85}
                />
              );
            })}

            {/* Middle: PCIe Firehose */}
            <line x1={515} y1={575} x2={1205} y2={575} stroke={CK.trap} strokeWidth={18} opacity={0.14} strokeLinecap="round" />
            <line x1={515} y1={575} x2={1205} y2={575} stroke={CK.cxl} strokeWidth={4} strokeDasharray="12 10" opacity={0.6} />
            <PacketStream pts={q0Route} travel={rt * 360} gap={44} size={15} color={CK.cxl} opacity={0.95} />
            <rect x={775} y={510} width={170} height={38} rx={10} fill="#141b2e" stroke={CK.cxl} strokeWidth={1.5} />
            <text x={860} y={535} textAnchor="middle" fontSize={20} fontWeight={700} fill={CK.cxl} fontFamily={MONO}>
              PCIe Push
            </text>

            {/* Right: Host LLC (60 MiB) under siege */}
            <rect
              x={1210}
              y={425}
              width={545}
              height={310}
              rx={22}
              fill="#141b2e"
              stroke={CK.trap}
              strokeWidth={2.5 + pulse * 1.5}
            />
            <text x={1482} y={468} textAnchor="middle" fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host LLC (60 MiB)
            </text>

            {/* Nested 8 MiB DDIO box */}
            <rect
              x={1255}
              y={494}
              width={215}
              height={110}
              rx={14}
              fill="rgba(248,113,113,0.14)"
              stroke={CK.trap}
              strokeWidth={2}
            />
            <text x={1362} y={542} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              8 MiB DDIO
            </text>
            <text x={1362} y={574} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              tiny cache slice
            </text>

            {/* Eviction warning box */}
            <rect
              x={1495}
              y={494}
              width={220}
              height={110}
              rx={14}
              fill={`rgba(248,113,113,${0.12 + 0.12 * pulse})`}
              stroke={CK.trap}
              strokeWidth={2}
              strokeDasharray="8 5"
            />
            <text x={1605} y={542} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
              Cache Eviction?
            </text>
            <text x={1605} y={574} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Spills to Host DDR5
            </text>

            {/* Host DDR5 strip */}
            <rect x={1255} y={634} width={460} height={68} rx={12} fill="#0f1526" stroke={CK.host} strokeWidth={1.8} />
            <text x={1485} y={675} textAnchor="middle" fontSize={21} fontWeight={700} fill={CK.host} fontFamily={MONO}>
              Host DDR5 · High Bandwidth Spill Risk
            </text>
          </g>
        )}

        {/* ===================== CUE 1 & 2: SCHEME A vs SCHEME B ===================== */}
        {kA > 0.005 && (
          <g opacity={kA}>
            {/* Scheme A Outer Panel (Left Half) */}
            <rect
              x={68}
              y={142}
              width={856}
              height={714}
              rx={22}
              fill="rgba(20,27,46,0.92)"
              stroke={CK.trap}
              strokeWidth={2.5}
            />
            {/* Header Strip */}
            <rect x={92} y={166} width={280} height={42} rx={10} fill={CK.trap} />
            <text x={232} y={194} textAnchor="middle" fontSize={21} fontWeight={800} fill={C.bg} fontFamily={MONO}>
              SCHEME A · PASSIVE NIC
            </text>
            <text x={392} y={194} fontSize={21} fontWeight={600} fill={C.muted} fontFamily={FONT}>
              Dumps burst straight into host ring
            </text>

            {/* Passive NIC block */}
            <rect x={96} y={280} width={192} height={270} rx={16} fill="#101729" stroke={C.muted} strokeWidth={2} />
            <text x={192} y={330} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Passive NIC
            </text>
            <text x={192} y={362} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              no burst queue
            </text>
            <rect x={120} y={392} width={144} height={54} rx={10} fill="rgba(248,113,113,0.18)" stroke={CK.trap} strokeWidth={1.8} />
            <text x={192} y={426} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              100 M/s RX
            </text>
            <text x={192} y={510} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Pass-through
            </text>

            {/* PCIe Firehose Arrow */}
            <line x1={290} y1={410} x2={435} y2={410} stroke={CK.trap} strokeWidth={22} opacity={0.18} strokeLinecap="round" />
            <line x1={290} y1={410} x2={435} y2={410} stroke={CK.trap} strokeWidth={4} />
            <PacketStream pts={aPcieRoute} travel={rt * 260} gap={34} size={13} color={CK.trap} opacity={0.95} />
            <text x={362} y={376} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              PCIe Flood
            </text>

            {/* Host Memory Hierarchy Box */}
            <rect x={438} y={240} width={456} height={520} rx={18} fill="#0f1526" stroke={CK.host} strokeWidth={2} />
            <text x={666} y={276} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.host} fontFamily={MONO}>
              HOST MEMORY HIERARCHY
            </text>

            {/* 128 MiB Host Ring */}
            <rect x={464} y={294} width={404} height={76} rx={12} fill="rgba(248,113,113,0.22)" stroke={CK.trap} strokeWidth={2.2} />
            <text x={666} y={328} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              128 MiB Host Ring
            </text>
            <text x={666} y={355} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={FONT}>
              Giant RX ring absorbs entire burst
            </text>

            {/* 8 MiB DDIO Overflow */}
            <rect x={464} y={388} width={404} height={72} rx={12} fill="url(#hatchRed)" stroke={CK.trap} strokeWidth={2} />
            <text x={585} y={430} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.text} fontFamily={MONO}>
              8 MiB DDIO
            </text>
            <rect x={712} y={404} width={136} height={38} rx={8} fill={CK.trap} />
            <text x={780} y={430} textAnchor="middle" fontSize={18} fontWeight={800} fill={C.bg} fontFamily={MONO}>
              OVERFLOW
            </text>

            {/* 60 MiB Host LLC Thrashing */}
            <rect
              x={464}
              y={478}
              width={404}
              height={128}
              rx={12}
              fill={`rgba(248,113,113,${0.18 + 0.14 * pulse})`}
              stroke={CK.trap}
              strokeWidth={2.5}
            />
            <text x={666} y={522} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              60 MiB Host LLC
            </text>
            <text x={666} y={558} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Cache Thrashing!
            </text>
            <text x={666} y={588} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Hot app data evicted to DRAM
            </text>

            {/* Spill Arrow to Host DDR5 */}
            <line x1={662} y1={608} x2={662} y2={668} stroke={CK.trap} strokeWidth={5} />
            <polygon points="650,656 674,656 662,672" fill={CK.trap} />
            <PacketStream pts={aSpillRoute} travel={rt * 110} gap={26} size={10} color={CK.trap} opacity={0.9} />

            {/* Host DDR5 */}
            <rect x={464} y={672} width={404} height={68} rx={12} fill="rgba(248,113,113,0.28)" stroke={CK.trap} strokeWidth={2} />
            <text x={666} y={713} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              Host DDR5 · Heavy Spill
            </text>

            {/* Bottom Summary Tag */}
            <rect x={96} y={784} width={798} height={50} rx={12} fill="rgba(248,113,113,0.14)" stroke={CK.trap} strokeWidth={1.5} />
            <text x={495} y={816} textAnchor="middle" fontSize={21} fontWeight={700} fill={CK.trap} fontFamily={FONT}>
              Result: 128 MiB burst blows past 8 MiB DDIO and 60 MiB Host LLC
            </text>
          </g>
        )}

        {/* Scheme B Outer Panel (Right Half, reveals at Cue 2) */}
        {kB > 0.005 && (
          <g opacity={kB}>
            {/* Divider */}
            <line x1={960} y1={165} x2={960} y2={840} stroke={C.line} strokeWidth={2} strokeDasharray="8 8" opacity={0.7} />

            <rect
              x={996}
              y={142}
              width={856}
              height={714}
              rx={22}
              fill="rgba(20,27,46,0.94)"
              stroke={CK.ok}
              strokeWidth={2.5}
            />
            {/* Header Strip */}
            <rect x={1020} y={166} width={280} height={42} rx={10} fill={CK.ok} />
            <text x={1160} y={194} textAnchor="middle" fontSize={21} fontWeight={800} fill={C.bg} fontFamily={MONO}>
              SCHEME B · ACTIVE NIC
            </text>
            <text x={1320} y={194} fontSize={21} fontWeight={600} fill={C.muted} fontFamily={FONT}>
              Backlog on NIC · paced credits to host
            </text>

            {/* Active NIC Block */}
            <rect x={1024} y={240} width={340} height={520} rx={18} fill="#0f1526" stroke={CK.card} strokeWidth={2.4} />
            <text x={1194} y={276} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.card} fontFamily={MONO}>
              ACTIVE NIC (CN106)
            </text>

            {/* Incoming Burst Badge */}
            <rect x={1054} y={292} width={280} height={42} rx={10} fill="rgba(251,146,60,0.18)" stroke={CK.card} strokeWidth={1.6} />
            <text x={1194} y={320} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.card} fontFamily={MONO}>
              100 M/s Burst Stops Here
            </text>

            {/* 48 MiB Card L3 */}
            <rect x={1054} y={352} width={280} height={116} rx={14} fill="rgba(251,146,60,0.22)" stroke={CK.card} strokeWidth={2.2} />
            <text x={1194} y={396} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.card} fontFamily={MONO}>
              48 MiB Card L3
            </text>
            <text x={1194} y={432} textAnchor="middle" fontSize={19} fill={C.text} fontFamily={FONT}>
              Absorbs bursts cleanly
            </text>

            {/* Card Spill Arrow */}
            <line x1={1195} y1={468} x2={1195} y2={530} stroke={CK.card} strokeWidth={4} />
            <polygon points="1183,518 1207,518 1195,534" fill={CK.card} />
            <PacketStream pts={bCardSpill} travel={rt * 80} gap={24} size={9} color={CK.card} opacity={0.9} />

            {/* Card DDR5 */}
            <rect x={1054} y={534} width={280} height={106} rx={14} fill="rgba(251,146,60,0.14)" stroke={CK.card} strokeWidth={2} />
            <text x={1194} y={578} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.card} fontFamily={MONO}>
              Card DDR5
            </text>
            <text x={1194} y={612} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Dedicated 6-Ch overflow
            </text>

            <rect x={1054} y={668} width={280} height={66} rx={12} fill="#141b2e" stroke={C.line} strokeWidth={1.5} />
            <text x={1194} y={707} textAnchor="middle" fontSize={19} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Backlog queued on NIC
            </text>

            {/* Paced RX & Credits across PCIe */}
            <line x1={1365} y1={365} x2={1520} y2={365} stroke={CK.ok} strokeWidth={14} opacity={0.16} strokeLinecap="round" />
            <line x1={1365} y1={365} x2={1520} y2={365} stroke={CK.ok} strokeWidth={3.5} />
            <polygon points="1508,355 1508,375 1524,365" fill={CK.ok} />
            <PacketStream pts={bRxRoute} travel={rt * 110} gap={52} size={11} color={CK.ok} opacity={0.95} />
            <text x={1442} y={338} textAnchor="middle" fontSize={18} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              Paced RX
            </text>

            <line x1={1520} y1={445} x2={1365} y2={445} stroke={CK.push} strokeWidth={3} strokeDasharray="8 6" />
            <polygon points="1377,435 1377,455 1361,445" fill={CK.push} />
            <PacketStream pts={bCredRoute} travel={rt * 110} gap={56} size={9} color={CK.push} opacity={0.95} />
            <text x={1442} y={480} textAnchor="middle" fontSize={18} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              Credits
            </text>

            {/* Host Memory Box (Clean & Green) */}
            <rect x={1522} y={240} width={302} height={520} rx={18} fill="#0f1526" stroke={CK.ok} strokeWidth={2.2} />
            <text x={1673} y={276} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              HOST MEMORY
            </text>

            {/* 8 MiB DDIO enclosing 256 KiB Hot Ring */}
            <rect x={1544} y={296} width={258} height={212} rx={14} fill="rgba(74,222,128,0.08)" stroke={CK.ok} strokeWidth={2} />
            <text x={1673} y={326} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              8 MiB DDIO
            </text>

            {/* Tiny glowing 256 KiB Hot Host Ring */}
            <rect
              x={1564}
              y={344}
              width={218}
              height={96}
              rx={12}
              fill={`rgba(74,222,128,${0.2 + 0.1 * pulse})`}
              stroke={CK.ok}
              strokeWidth={2.8}
            />
            <text x={1673} y={384} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.text} fontFamily={MONO}>
              256 KiB Hot Ring
            </text>
            <text x={1673} y={416} textAnchor="middle" fontSize={17} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
              Refilled in place
            </text>

            <rect x={1564} y={454} width={218} height={38} rx={8} fill={CK.ok} />
            <text x={1673} y={479} textAnchor="middle" fontSize={17} fontWeight={800} fill={C.bg} fontFamily={MONO}>
              100% Cache-Resident
            </text>

            {/* 60 MiB Host LLC Clean */}
            <rect x={1544} y={530} width={258} height={94} rx={12} fill="rgba(124,147,214,0.14)" stroke={CK.host} strokeWidth={1.8} />
            <text x={1673} y={569} textAnchor="middle" fontSize={21} fontWeight={800} fill={C.text} fontFamily={MONO}>
              60 MiB Host LLC
            </text>
            <text x={1673} y={601} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
              Clean · Zero Eviction
            </text>

            {/* Host DDR5 Untouched */}
            <rect x={1544} y={648} width={258} height={88} rx={12} fill="#141b2e" stroke={C.line} strokeWidth={1.8} />
            <text x={1673} y={686} textAnchor="middle" fontSize={21} fontWeight={800} fill={C.muted} fontFamily={MONO}>
              Host DDR5
            </text>
            <text x={1673} y={716} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
              −98.9% Traffic
            </text>

            {/* Bottom Summary Tag */}
            <rect x={1024} y={784} width={800} height={50} rx={12} fill="rgba(74,222,128,0.14)" stroke={CK.ok} strokeWidth={1.5} />
            <text x={1424} y={816} textAnchor="middle" fontSize={21} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
              Result: 256 KiB hot ring stays permanently inside 8 MiB DDIO
            </text>
          </g>
        )}

        {/* ===================== CUE 3: ZERO-BACKLOG FAST-PATH STRIP ===================== */}
        {k3 > 0.005 && (
          <g opacity={k3}>
            <rect x={150} y={605} width={1620} height={175} rx={20} fill="rgba(20,27,46,0.9)" stroke={C.line} strokeWidth={1.8} />
            {/* Left NIC box */}
            <rect x={200} y={638} width={320} height={110} rx={14} fill="#0f1526" stroke={CK.card} strokeWidth={2.2} />
            <text x={360} y={684} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.card} fontFamily={MONO}>
              Active NIC (CN106)
            </text>
            <text x={360} y={718} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Zero backlog queued
            </text>

            {/* Steady bidirectional credit loop */}
            <line x1={520} y1={665} x2={1340} y2={665} stroke={CK.ok} strokeWidth={14} opacity={0.14} strokeLinecap="round" />
            <line x1={520} y1={665} x2={1340} y2={665} stroke={CK.ok} strokeWidth={3.5} />
            <polygon points="1326,655 1326,675 1344,665" fill={CK.ok} />
            <PacketStream pts={c3RxRoute} travel={rt * 190} gap={72} size={11} color={CK.ok} opacity={0.95} />
            <text x={930} y={647} textAnchor="middle" fontSize={19} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
              176.9 M/s Steady Streaming (100% DDIO)
            </text>

            <line x1={1340} y1={725} x2={520} y2={725} stroke={CK.push} strokeWidth={3} strokeDasharray="10 8" />
            <polygon points="534,715 534,735 516,725" fill={CK.push} />
            <PacketStream pts={c3CredRoute} travel={rt * 190} gap={82} size={9} color={CK.push} opacity={0.95} />
            <text x={930} y={758} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.push} fontFamily={MONO}>
              Lightweight Credit Return (+6 ns fast-path check)
            </text>

            {/* Right Host box */}
            <rect x={1344} y={638} width={376} height={110} rx={14} fill="#0f1526" stroke={CK.ok} strokeWidth={2.2} />
            <text x={1532} y={684} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              256 KiB Hot Ring
            </text>
            <text x={1532} y={718} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              100% DDIO hits · 0% regression
            </text>
          </g>
        )}

        {/* ===================== CUE 4: 128 MiB BURST PAYOFF CHARTS ===================== */}
        {k4 > 0.005 && (
          <g opacity={k4}>
            <rect x={70} y={222} width={1180} height={615} rx={22} fill="rgba(20,27,46,0.9)" stroke={C.line} strokeWidth={1.8} />

            {/* Metric 1: Host LLC Read Misses */}
            <text x={106} y={266} fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host LLC Read Misses
            </text>
            <text x={1210} y={266} textAnchor="end" fontSize={19} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
              lower is better
            </text>
            <HBarChart
              x={96}
              y={284}
              w={1120}
              labelW={210}
              barH={40}
              gap={14}
              max={100}
              rows={[
                { label: "Passive NIC", value: 91.4, valueText: "91.4%", color: CK.trap, k: k4Bar1 },
                { label: "Active NIC", value: 0.4, valueText: "0.0%", color: CK.ok, k: k4Bar1 },
              ]}
            />

            <line x1={106} y1={412} x2={1214} y2={412} stroke={C.line} strokeWidth={1.2} opacity={0.6} />

            {/* Metric 2: Host DDIO Hit Rate */}
            <text x={106} y={454} fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host DDIO Hit Rate
            </text>
            <text x={1210} y={454} textAnchor="end" fontSize={19} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
              higher is better
            </text>
            <HBarChart
              x={96}
              y={472}
              w={1120}
              labelW={210}
              barH={40}
              gap={14}
              max={100}
              rows={[
                { label: "Passive NIC", value: 16.3, valueText: "16.3%", color: CK.trap, k: k4Bar2 },
                { label: "Active NIC", value: 100, valueText: "100%", color: CK.ok, k: k4Bar2 },
              ]}
            />

            <line x1={106} y1={600} x2={1214} y2={600} stroke={C.line} strokeWidth={1.2} opacity={0.6} />

            {/* Metric 3: Host DRAM Traffic */}
            <text x={106} y={642} fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host DRAM Traffic (128 MiB Burst)
            </text>
            <text x={1210} y={642} textAnchor="end" fontSize={19} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
              lower is better
            </text>
            <HBarChart
              x={96}
              y={660}
              w={1120}
              labelW={210}
              barH={40}
              gap={14}
              max={1000}
              rows={[
                { label: "Passive NIC", value: 972, valueText: "972 MiB", color: CK.trap, k: k4Bar3 },
                { label: "Active NIC", value: 11, valueText: "11 MiB", color: CK.ok, k: k4Bar3 },
              ]}
            />
          </g>
        )}

        {/* ===================== CUE 5: TWO-REGIME CONSERVATION DIAGRAM ===================== */}
        {k5 > 0.005 && (
          <g opacity={k5}>
            {/* Left Regime Card: Bursts <= 32 MiB */}
            <g opacity={k5L}>
              <rect x={80} y={235} width={840} height={585} rx={22} fill="rgba(20,27,46,0.94)" stroke={CK.ok} strokeWidth={2.5} />
              <rect x={112} y={265} width={330} height={44} rx={10} fill={CK.ok} />
              <text x={277} y={294} textAnchor="middle" fontSize={21} fontWeight={800} fill={C.bg} fontFamily={MONO}>
                REGIME 1 · BURSTS ≤ 32 MiB
              </text>
              <text x={464} y={294} fontSize={21} fontWeight={600} fill={C.muted} fontFamily={FONT}>
                Fits inside NIC L3
              </text>

              {/* Card L3 Box with 32/48 MiB fill */}
              <rect x={118} y={346} width={380} height={240} rx={16} fill="#0f1526" stroke={CK.card} strokeWidth={2.2} />
              <text x={308} y={384} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                48 MiB Card L3
              </text>
              {/* Capacity bar */}
              <rect x={150} y={408} width={316} height={96} rx={12} fill="#141b2e" stroke={C.line} strokeWidth={1.5} />
              <rect
                x={150}
                y={408}
                width={Math.max(0, 316 * 0.67 * k5L)}
                height={96}
                rx={12}
                fill={CK.ok}
                opacity={0.78}
              />
              <text x={308} y={464} textAnchor="middle" fontSize={23} fontWeight={800} fill={C.text} fontFamily={MONO}>
                ≤ 32 MiB Burst
              </text>
              <text x={308} y={550} textAnchor="middle" fontSize={19} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
                100% absorbed in on-card L3
              </text>

              {/* Right zero-DRAM boxes */}
              <rect x={548} y={346} width={336} height={106} rx={14} fill="#0f1526" stroke={C.line} strokeWidth={1.8} strokeDasharray="8 6" />
              <text x={716} y={390} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.muted} fontFamily={MONO}>
                Card DDR5
              </text>
              <text x={716} y={426} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                0 MiB
              </text>

              <rect x={548} y={480} width={336} height={106} rx={14} fill="#0f1526" stroke={C.line} strokeWidth={1.8} strokeDasharray="8 6" />
              <text x={716} y={524} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.muted} fontFamily={MONO}>
                Host DDR5
              </text>
              <text x={716} y={560} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                Minimal (~0.02/pkt)
              </text>

              {/* Bottom Verdict */}
              <rect x={118} y={632} width={766} height={148} rx={16} fill="rgba(74,222,128,0.12)" stroke={CK.ok} strokeWidth={1.8} />
              <text x={501} y={688} textAnchor="middle" fontSize={29} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
                Near-Zero DRAM System-Wide
              </text>
              <text x={501} y={736} textAnchor="middle" fontSize={21} fill={C.text} fontFamily={MONO}>
                Host DRAM ~0.02/pkt · Card DRAM = 0
              </text>
            </g>

            {/* Right Regime Card: Bursts > 48 MiB (128 MiB) */}
            <g opacity={k5R}>
              <rect x={1000} y={235} width={840} height={585} rx={22} fill="rgba(20,27,46,0.94)" stroke={CK.card} strokeWidth={2.5} />
              <rect x={1032} y={265} width={488} height={44} rx={10} fill={CK.card} />
              <text x={1276} y={294} textAnchor="middle" fontSize={20} fontWeight={800} fill={C.bg} fontFamily={MONO}>
                REGIME 2 · BURSTS &gt; 48 MiB (128 MiB)
              </text>
              <text x={1542} y={294} fontSize={21} fontWeight={600} fill={C.muted} fontFamily={FONT}>
                Clean spill to card
              </text>

              {/* Card L3 Full */}
              <rect x={1038} y={346} width={250} height={240} rx={16} fill="#0f1526" stroke={CK.card} strokeWidth={2.2} />
              <text x={1163} y={386} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                48 MiB Card L3
              </text>
              <rect x={1064} y={412} width={198} height={96} rx={12} fill={CK.card} opacity={0.85} />
              <text x={1163} y={468} textAnchor="middle" fontSize={23} fontWeight={800} fill={C.bg} fontFamily={MONO}>
                FULL (48 MiB)
              </text>
              <text x={1163} y={550} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
                Spills cleanly →
              </text>

              {/* Spill Arrow to Card DDR5 */}
              <line x1={1290} y1={470} x2={1435} y2={470} stroke={CK.card} strokeWidth={14} opacity={0.18} strokeLinecap="round" />
              <line x1={1290} y1={470} x2={1435} y2={470} stroke={CK.card} strokeWidth={4} />
              <polygon points="1421,459 1421,481 1440,470" fill={CK.card} />
              <PacketStream pts={r2SpillRoute} travel={rt * 110} gap={36} size={10} color={CK.card} opacity={0.95} />

              {/* Card DDR5 Absorbs */}
              <rect x={1440} y={346} width={362} height={126} rx={14} fill="rgba(251,146,60,0.2)" stroke={CK.card} strokeWidth={2.4} />
              <text x={1621} y={392} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                Card DDR5 (6-Ch)
              </text>
              <text x={1621} y={434} textAnchor="middle" fontSize={20} fontWeight={700} fill={C.text} fontFamily={FONT}>
                Absorbs 128 MiB spill
              </text>

              {/* Host DDR5 Minimal Residual */}
              <rect x={1440} y={492} width={362} height={94} rx={14} fill="rgba(74,222,128,0.1)" stroke={CK.ok} strokeWidth={2} />
              <text x={1621} y={532} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                Host DDR5 · ~11 MiB
              </text>
              <text x={1621} y={564} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
                −98.9% vs 972 MiB passive ring
              </text>

              {/* Bottom Verdict */}
              <rect x={1038} y={632} width={764} height={148} rx={16} fill="rgba(251,146,60,0.12)" stroke={CK.card} strokeWidth={1.8} />
              <text x={1420} y={688} textAnchor="middle" fontSize={29} fontWeight={800} fill={CK.card} fontFamily={FONT}>
                Dedicated 6-Channel Card DDR5 Offload
              </text>
              <text x={1420} y={736} textAnchor="middle" fontSize={20} fill={C.text} fontFamily={MONO}>
                Card absorbs overflow · Host DRAM stays minimal (~11 MiB)
              </text>
            </g>
          </g>
        )}

        {/* ===================== CUE 6: DRAIN REGIME SPECTRUM ===================== */}
        {k6 > 0.005 && (
          <g opacity={k6}>
            <rect x={120} y={525} width={1680} height={162} rx={20} fill="rgba(20,27,46,0.92)" stroke={C.line} strokeWidth={1.8} />
            <text x={160} y={564} fontSize={21} fontWeight={800} fill={C.muted} fontFamily={MONO}>
              DRAIN THROUGHPUT BY BURST SIZE
            </text>

            {/* Zone 1: Small (64 KiB - 256 KiB) */}
            <rect x={160} y={584} width={280} height={68} rx={12} fill="rgba(56,189,248,0.16)" stroke={CK.push} strokeWidth={2} />
            <text x={300} y={614} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              64 KiB → 256 KiB
            </text>
            <text x={300} y={640} textAnchor="middle" fontSize={17} fill={C.text} fontFamily={FONT}>
              Hot ring fast path
            </text>

            {/* Zone 2: Mid-size (1-32 MiB) trade-off */}
            <rect x={460} y={584} width={620} height={68} rx={12} fill="rgba(250,204,21,0.16)" stroke={CK.cxl} strokeWidth={2} />
            <text x={770} y={614} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              1–32 MiB (Mid-Size Bursts)
            </text>
            <text x={770} y={640} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={FONT}>
              1 NIC core drains slightly slower than host cache
            </text>

            {/* 48 MiB L3 Threshold */}
            <line x1={1108} y1={568} x2={1108} y2={668} stroke={CK.card} strokeWidth={3} strokeDasharray="8 6" />
            <text x={1108} y={558} textAnchor="middle" fontSize={18} fontWeight={800} fill={CK.card} fontFamily={MONO}>
              48 MiB L3
            </text>

            {/* Zone 3: Large (> 48 MiB .. 128 MiB) Active NIC wins */}
            <rect x={1136} y={584} width={624} height={68} rx={12} fill="rgba(74,222,128,0.18)" stroke={CK.ok} strokeWidth={2.4} />
            <text x={1448} y={614} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              &gt; 48 MiB → 128 MiB
            </text>
            <text x={1448} y={640} textAnchor="middle" fontSize={18} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Active NIC wins +8.4% drain &amp; −98.9% host DRAM
            </text>
          </g>
        )}
      </Stage>

      {/* ===================== HTML OVERLAYS (OUTSIDE <Stage>) ===================== */}

      {/* Cue 0: Question 6 Banner */}
      {k0 > 0.005 && (
        <QuestionBanner
          num="QUESTION 6"
          question="What happens on the RX side during 100 M/s traffic bursts?"
          sub="Even with fast PCIe push, massive receive bursts can evict the host's entire cache."
          color={CK.cxl}
          y={180}
          opacity={k0}
          dy={lerp(20, 0, prog(t, T[0], 0.55))}
        />
      )}

      {/* Cue 3: Zero-Backlog Fast-Path Check */}
      {k3 > 0.005 && (
        <>
          <div
            style={{
              position: "absolute",
              left: 120,
              top: 155,
              width: 1680,
              textAlign: "center",
              opacity: k3,
              transform: `translateY(${lerp(18, 0, prog(t, T[3] + 0.15, 0.5))}px)`,
            }}
          >
            <div style={{ fontSize: 38, fontWeight: 800, color: C.text, fontFamily: FONT }}>
              No-Burst Overhead: Virtually Zero
            </div>
            <div style={{ fontSize: 22, color: C.muted, marginTop: 8, fontFamily: FONT }}>
              Credit-based active NIC adds negligible cost on the steady-state fast path
            </div>
          </div>

          <HeroStat
            x={150}
            y={290}
            w={500}
            h={240}
            value="+6 ns"
            label="Idle RTT Overhead"
            sub="0.887 → 0.893 µs"
            color={CK.push}
            opacity={k3 * prog(t, T[3] + 0.2, 0.5)}
            dy={lerp(20, 0, prog(t, T[3] + 0.2, 0.5))}
          />
          <HeroStat
            x={710}
            y={290}
            w={500}
            h={240}
            value="0%"
            label="Concurrency Regression"
            sub="steady credit flow"
            color={CK.ok}
            opacity={k3 * prog(t, T[3] + 0.35, 0.5)}
            dy={lerp(20, 0, prog(t, T[3] + 0.35, 0.5))}
          />
          <HeroStat
            x={1270}
            y={290}
            w={500}
            h={240}
            value={`${c3Stream} M/s`}
            label="Credit-On Streaming"
            sub="100% DDIO hits"
            color={CK.ok}
            opacity={k3 * prog(t, T[3] + 0.5, 0.5)}
            dy={lerp(20, 0, prog(t, T[3] + 0.5, 0.5))}
          />
        </>
      )}

      {/* Cue 4: 128 MiB Burst Payoff Header + HeroStat */}
      {k4 > 0.005 && (
        <>
          <div
            style={{
              position: "absolute",
              left: 76,
              top: 142,
              width: 1768,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              opacity: k4,
            }}
          >
            <div style={{ fontSize: 34, fontWeight: 800, color: C.text, fontFamily: FONT }}>
              128 MiB Burst Payoff: Passive vs. Active NIC
            </div>
            <div style={{ display: "flex", gap: 16 }}>
              <span
                style={{
                  fontSize: 19,
                  fontWeight: 800,
                  color: CK.trap,
                  fontFamily: MONO,
                  background: "rgba(248,113,113,0.14)",
                  border: `1.5px solid ${CK.trap}`,
                  borderRadius: 10,
                  padding: "6px 16px",
                }}
              >
                ■ Passive NIC (128 MiB Ring)
              </span>
              <span
                style={{
                  fontSize: 19,
                  fontWeight: 800,
                  color: CK.ok,
                  fontFamily: MONO,
                  background: "rgba(74,222,128,0.14)",
                  border: `1.5px solid ${CK.ok}`,
                  borderRadius: 10,
                  padding: "6px 16px",
                }}
              >
                ■ Active NIC (256 KiB Hot Ring)
              </span>
            </div>
          </div>

          <HeroStat
            x={1290}
            y={245}
            w={554}
            h={320}
            value={`−${dramDrop}%`}
            label="Host DRAM Traffic"
            sub="972 MiB → 11 MiB · Host cache stays 100% clean"
            color={CK.ok}
            opacity={k4 * prog(t, T[4] + 0.4, 0.55)}
            dy={lerp(24, 0, prog(t, T[4] + 0.4, 0.55))}
          />

          <div
            style={{
              position: "absolute",
              left: 1290,
              top: 605,
              width: 554,
              height: 215,
              opacity: k4 * prog(t, T[4] + 0.7, 0.55),
              transform: `translateY(${lerp(20, 0, prog(t, T[4] + 0.7, 0.55))}px)`,
              background: "linear-gradient(155deg, rgba(20,27,46,0.96), rgba(11,16,32,0.94))",
              border: `2px solid ${CK.push}`,
              borderRadius: 18,
              padding: "22px 26px",
              boxSizing: "border-box",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              gap: 12,
            }}
          >
            <div style={{ fontSize: 22, fontWeight: 800, color: CK.push, fontFamily: MONO }}>
              HOST CACHE ISOLATION
            </div>
            <div style={{ fontSize: 28, fontWeight: 800, color: C.text, fontFamily: FONT }}>
              91.4% → 0.0% LLC Read Misses
            </div>
            <div style={{ fontSize: 21, color: C.muted, fontFamily: FONT }}>
              DDIO jumps <strong style={{ color: CK.ok }}>16.3% → 100%</strong> under full burst load.
            </div>
          </div>
        </>
      )}

      {/* Cue 5: Conservation Law Header */}
      {k5 > 0.005 && (
        <div
          style={{
            position: "absolute",
            left: 80,
            top: 145,
            width: 1760,
            opacity: k5,
          }}
        >
          <div style={{ fontSize: 36, fontWeight: 800, color: C.text, fontFamily: FONT }}>
            Two-Regime Conservation Law
          </div>
          <div style={{ fontSize: 22, color: C.muted, marginTop: 4, fontFamily: FONT }}>
            Dual-side counters confirm where every byte of burst backlog lands
          </div>
        </div>
      )}

      {/* Cue 6: Performance Cards + Honest Boundary */}
      {k6 > 0.005 && (
        <>
          <div
            style={{
              position: "absolute",
              left: 120,
              top: 145,
              width: 1680,
              opacity: k6,
            }}
          >
            <div style={{ fontSize: 34, fontWeight: 800, color: C.text, fontFamily: FONT }}>
              Refill Latency, Drain Speed &amp; Honest Boundary
            </div>
          </div>

          <HeroStat
            x={120}
            y={220}
            w={800}
            h={250}
            value={`${c6Refill} µs`}
            label="Sequential Credit Refill"
            sub="1.05–1.16 µs cold bound"
            color={CK.push}
            opacity={k6 * prog(t, T[6] + 0.2, 0.5)}
            dy={lerp(20, 0, prog(t, T[6] + 0.2, 0.5))}
          />

          <HeroStat
            x={1000}
            y={220}
            w={800}
            h={250}
            value={`+${c6Gain}%`}
            label="Faster 128 MiB Drain"
            sub="steady past 48 MiB L3"
            color={CK.ok}
            opacity={k6 * prog(t, T[6] + 0.4, 0.5)}
            dy={lerp(20, 0, prog(t, T[6] + 0.4, 0.5))}
          />

          <BoundaryBar
            x={80}
            y={732}
            w={1760}
            badge="HONEST BOUNDARY"
            text="Honest trade-off: a single NIC core drains mid-size bursts (1–32 MiB) slightly slower than host cache before winning above 48 MiB."
            color={CK.cxl}
            opacity={k6 * prog(t, T[6] + 0.65, 0.55)}
            dy={lerp(18, 0, prog(t, T[6] + 0.65, 0.55))}
          />
        </>
      )}
    </>
  );
}

export const C07: SceneDef = {
  id: "C07",
  title: "6 \u00b7 Active-NIC Burst Buffering",
  stage: 5,
  source: "100 M/s RX Bursts (64 KiB \u2192 128 MiB) \u00b7 256 KiB Hot Host Ring",
  dur: DUR,
  cues,
  C: Body,
};
