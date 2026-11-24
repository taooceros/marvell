import React from "react";
 import {
   Axes,
   C,
   Cue,
   FONT,
   MONO,
   PacketStream,
   SceneDef,
   Series,
   Stage,
   clamp01,
   countUp,
   fade,
   lerp,
   linScale,
   logScale,
   pathD,
   prog,
   useRealT,
   useT,
 } from "../lib";
 import { CK, HeroStat, RingStrip, SplitBar } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 12.32, "**MRV-158** tests an **active NIC** on the RX path: during **100 M/s** traffic bursts, **don't dump 128 MiB into host RAM!**", "Report two left receive buffering open. M R V one fifty-eight tests an active NIC: during hundred million per second traffic bursts, stop dumping one hundred twenty-eight megabytes into host memory."], // 0
  [12.67, 25.35, "**Scheme A** (`128 MiB` host ring) vs **Scheme B** (backlog stays on **active NIC**; host keeps only a **256 KiB hot ring** refilled by credits).", "Scheme A pours bursts into a one hundred twenty-eight megabyte host ring. Scheme B buffers the backlog on the active NIC, keeping just a two hundred fifty-six kilobyte hot ring on the host refilled in place as credits return."], // 1
  [25.70, 38.50, "Zero-backlog overhead is practically zero: **W=1 mean +6 ns** (`0.887 → 0.893 µs`), `W=32` zero regression, streaming **176.9 M/s**.", "With zero backlog, checking credits costs only six nanoseconds on single-packet mean latency, zero regression under concurrency, and streaming still reaches one hundred seventy-six point nine million per second."], // 2
  [38.85, 60.09, "At **128 MiB bursts** (`512 MiB` total): host LLC misses **91.4% → 0.0%**, DDIO **16.3% → 100%**, **host DRAM traffic −98.9%** (**971.5 → 10.7 MiB**).", "Under one hundred twenty-eight megabyte bursts, Scheme A blows past host cache, hitting ninety-one point four percent L L C misses and nine hundred seventy-one megabytes of host D RAM traffic. Scheme B locks host reads into two hundred fifty-six kilobytes: zero percent misses, one hundred percent D D I O, and ninety-eight point nine percent less host D RAM."], // 3
  [60.44, 78.54, "**Conservation duality**: **≤ 32 MiB** fits in CN106's **48 MiB L3** (**≈ 0.02** system DRAM/pkt); **> 48 MiB** offloads **1.98/pkt** to card **6-ch DDR5**.", "System-wide counters reveal clean conservation: bursts up to thirty-two megabytes fit in the card's forty-eight megabyte L three cache, eliminating system D RAM traffic. Above forty-eight megabytes, the NIC's dedicated six-channel D D R five offloads the full two X memory traffic off the host."], // 4
  [78.89, 101.53, "Past 48 MiB L3, card drain holds **175.4 M/s** (**−0.14%**), beating Scheme A **+8.4%** at 128 MiB; credit refill stays **0.905 µs** (**1.05–1.16 µs** cold).", "Across the card L three wall, drain holds steady at one hundred seventy-five point four million per second, beating Scheme A by eight point four percent at one hundred twenty-eight megabytes. Sequential refill stays at zero point nine zero five microseconds, with a one point zero five to one point one six microsecond cold bound and a modest mid-burst single-core trade-off."], // 5
];
const DUR = 102.93; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

 // Verified MRV-158 Host LLC read miss % vs Burst Size (KiB), 64 KiB -> 128 MiB
 const MISS_A: [number, number][] = [
   [64, 0.8],
   [256, 0.2],
   [1024, 0.1],
   [4096, 4.3],
   [8192, 20.3],
   [16384, 40.3],
   [32768, 67.6],
   [49152, 80.0],
   [65536, 84.5],
   [98304, 88.8],
   [131072, 91.4],
 ];
 
 const MISS_B: [number, number][] = [
   [64, 0.02],
   [256, 0.02],
   [1024, 0.01],
   [4096, 0.01],
   [8192, 0.01],
   [16384, 0.01],
   [32768, 0.01],
   [49152, 0.02],
   [65536, 0.02],
   [98304, 0.02],
   [131072, 0.02],
 ];
 
 function Body() {
   const t = useT();
   const rt = useRealT();
 
   // Phase 1: Architecture & Zero-Backlog (Cues 0..2), fades out cleanly at T[3]
   const archOp = fade(t, T[0], T[3], 0.5);
   const kIntro = prog(t, T[0], 0.7);
   const kScheme = prog(t, T[1], 0.8);
   const kFast = prog(t, T[2], 0.7);
 
   // Phase 2: Burst Charts + Impact / Conservation Duality (Cues 3..5)
   const chartOp = fade(t, T[3], 1e9, 0.5);
   const kCurve = prog(t, T[3] + 0.15, 1.3);
   const impactOp = fade(t, T[3], T[4], 0.5);
   const dualOp = fade(t, T[4], 1e9, 0.5);
   const kDual = prog(t, T[4] + 0.15, 1.1);
   const boundOp = fade(t, T[5], 1e9, 0.5);
   const kBound = prog(t, T[5], 0.7);
 
   // Ambient burst fill animation during Phase 1
   const burstCycle = (rt * 0.28) % 1;
   const hostFlood = lerp(0.32, 0.96, kScheme) * (0.78 + 0.22 * Math.sin(rt * 2.2));
   const cardBufFill = lerp(0.28, 0.86, kScheme) * (0.8 + 0.2 * Math.sin(rt * 2.0 + 0.8));
   const activeSlot = Math.floor(rt * 7) % 10;
 
  // Chart scales (Left card: x 56..948, y 142..708)
  const cx0 = 152;
  const cx1 = 910;
  const cy0 = 600;
  const cy1 = 256;
  const sx = logScale(64, 131072, cx0, cx1);
  const sy = linScale(0, 100, cy0, cy1);

  const ddioX = sx(8192);
  const llcX = sx(61440);

  // Count-ups for Cue 3 impact stats (settle within 1.3 s)
  const dramCut = countUp(t, T[3] + 0.1, 1.25, 98.9, 1, 0);
  const missAVal = countUp(t, T[3] + 0.1, 1.2, 91.4, 1, 0);
  const ddioAVal = countUp(t, T[3] + 0.1, 1.2, 16.3, 1, 0);
 
   return (
     <>
       <Stage>
         {/* ================= PHASE 1: ARCHITECTURE COMPARISON (CUES 0..2) ================= */}
         {archOp > 0.005 && (
           <g opacity={archOp}>
             {/* Top-center incoming burst header */}
             <g opacity={kIntro}>
               <rect
                 x={630}
                 y={136}
                 width={660}
                 height={52}
                 rx={26}
                 fill="#141b2e"
                 stroke={CK.cxl}
                 strokeWidth={2}
               />
               <text
                 x={960}
                 y={162}
                 textAnchor="middle"
                 dominantBaseline="central"
                 fontSize={23}
                 fontWeight={800}
                 fill={CK.cxl}
                 fontFamily={MONO}
               >
                 100 M/s RX BURST (64 KiB → 128 MiB) · 1H + 1C
               </text>
             </g>
 
             {/* ---------- LEFT PANEL: SCHEME A (PASSIVE HOST, 128 MiB RING) ---------- */}
             <g opacity={kIntro}>
               <rect
                 x={56}
                 y={208}
                 width={878}
                 height={534}
                 rx={20}
                 fill="rgba(20,27,46,0.92)"
                 stroke={kScheme > 0.2 ? CK.trap : C.line}
                 strokeWidth={2.5}
               />
               {/* Panel title */}
               <text x={86} y={248} fontSize={26} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
                 Scheme A · Passive NIC → 128 MiB Host Ring
               </text>
               <text x={86} y={278} fontSize={19} fill={C.muted} fontFamily={MONO}>
                 Monotonic ring pours entire burst straight into host RAM
               </text>
 
               {/* NIC box (passive) */}
               <rect
                 x={86}
                 y={308}
                 width={205}
                 height={346}
                 rx={14}
                 fill="#101729"
                 stroke={CK.card}
                 strokeWidth={2}
               />
              <text x={188} y={340} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.card} fontFamily={FONT}>
                CN106 NIC
              </text>
              <text x={188} y={368} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                Passive NIC
              </text>
              <rect
                x={108}
                y={394}
                width={160}
                height={108}
                rx={10}
                fill="#171f36"
                stroke={C.dim}
                strokeDasharray="6 4"
                strokeWidth={1.6}
              />
              <text x={188} y={440} textAnchor="middle" fontSize={19} fontWeight={700} fill={C.muted} fontFamily={FONT}>
                No Backlog
              </text>
              <text x={188} y={468} textAnchor="middle" fontSize={18} fill={C.dim} fontFamily={MONO}>
                No buffering
              </text>
              <rect x={108} y={530} width={160} height={96} rx={10} fill="rgba(249,65,68,0.12)" stroke={CK.trap} strokeWidth={1.6} />
              <text x={188} y={566} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                100 M/s
              </text>
              <text x={188} y={598} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={FONT}>
                Unpaced DMA
              </text>
 
               {/* PCIe flood arrow NIC -> Host */}
               <path
                 d={pathD([
                   [294, 468],
                   [392, 468],
                 ])}
                 stroke={CK.trap}
                 strokeWidth={7}
                 opacity={0.35}
                 fill="none"
               />
               <PacketStream
                 pts={[
                   [294, 468],
                   [390, 468],
                 ]}
                 travel={rt * 190}
                 gap={34}
                 size={12}
                 color={CK.trap}
                 opacity={0.95}
               />
              <text x={343} y={444} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.trap} fontFamily={MONO}>
                PCIe ×4
              </text>
 
               {/* Host saturn box (flooded) */}
               <rect
                 x={396}
                 y={308}
                 width={510}
                 height={346}
                 rx={14}
                 fill="#101729"
                 stroke={CK.host}
                 strokeWidth={2}
               />
               <text x={422} y={340} fontSize={22} fontWeight={800} fill={CK.host} fontFamily={FONT}>
                 Host (saturn) · Cache Overflow!
               </text>
 
              {/* 8 MiB DDIO + 60 MiB LLC boxes (illuminate sharply in Cue 1) */}
              <g opacity={0.32 + 0.68 * kScheme}>
                <rect
                  x={422}
                  y={360}
                  width={216}
                  height={76}
                  rx={10}
                  fill={kScheme > 0.3 ? "rgba(249,65,68,0.22)" : "#151d33"}
                  stroke={kScheme > 0.3 ? CK.trap : C.line}
                  strokeWidth={2}
                />
                <text x={530} y={390} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  8 MiB DDIO
                </text>
                <text x={530} y={419} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={FONT}>
                  Evicted (16.3%)
                </text>

                {/* 60 MiB LLC box */}
                <rect
                  x={662}
                  y={360}
                  width={218}
                  height={76}
                  rx={10}
                  fill={kScheme > 0.3 ? "rgba(249,65,68,0.22)" : "#151d33"}
                  stroke={kScheme > 0.3 ? CK.trap : C.line}
                  strokeWidth={2}
                />
                <text x={771} y={390} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  60 MiB LLC
                </text>
                <text x={771} y={419} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={FONT}>
                  Thrashed (91.4%)
                </text>

                {/* Spillover downward arrow */}
                <path
                  d="M 650 442 L 650 486"
                  stroke={CK.trap}
                  strokeWidth={5}
                  strokeDasharray="8 6"
                />
                <polygon points="640,480 660,480 650,498" fill={CK.trap} />
              </g>

              {/* 128 MiB Host DDR5 Ring */}
              <rect
                x={422}
                y={506}
                width={458}
                height={124}
                rx={12}
                fill="#1a1220"
                stroke={CK.trap}
                strokeWidth={2.2}
              />
              {/* Animated red flood bar inside DDR5 */}
              <rect
                x={432}
                y={566}
                width={Math.max(12, 438 * clamp01(hostFlood))}
                height={48}
                rx={8}
                fill={CK.trap}
                opacity={0.78}
              />
              <rect x={432} y={566} width={438} height={48} rx={8} fill="url(#hatchRed)" opacity={0.3} />
              <text x={438} y={540} fontSize={20} fontWeight={800} fill={C.text} fontFamily={FONT}>
                Host DDR5 · 128 MiB Ring Spillover
              </text>
              <rect x={450} y={574} width={402} height={32} rx={7} fill="rgba(11,16,32,0.72)" />
              <text x={651} y={591} textAnchor="middle" dominantBaseline="central" fontSize={19} fontWeight={800} fill="#ffffff" fontFamily={MONO}>
                971.5 MiB Host DRAM (1.90/pkt)
              </text>
               {/* Bottom summary badge inside Scheme A */}
               <rect x={86} y={674} width={820} height={48} rx={10} fill="rgba(249,65,68,0.14)" stroke={CK.trap} strokeWidth={1.5} />
               <text x={496} y={699} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={700} fill={CK.trap} fontFamily={FONT}>
                 Host pays 2× DRAM traffic (write-back + cold read) &amp; app LLC thrashing
               </text>
             </g>
 
             {/* ---------- RIGHT PANEL: SCHEME B (ACTIVE NIC + 256 KiB HOT HOST RING) ---------- */}
             <g opacity={kIntro}>
               <rect
                 x={986}
                 y={208}
                 width={878}
                 height={534}
                 rx={20}
                 fill="rgba(20,27,46,0.94)"
                 stroke={kScheme > 0.2 ? CK.ok : C.line}
                 strokeWidth={2.5}
               />
               {/* Panel title */}
               <text x={1016} y={248} fontSize={26} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
                 Scheme B · Active NIC + 256 KiB Hot Host Ring
               </text>
               <text x={1016} y={278} fontSize={19} fill={C.muted} fontFamily={MONO}>
                 Backlog buffered on CN106 · host refills 256 KiB ring via credits
               </text>
 
               {/* Active NIC box (buffers burst) */}
               <rect
                 x={1016}
                 y={308}
                 width={318}
                 height={346}
                 rx={14}
                 fill="#101729"
                 stroke={CK.card}
                 strokeWidth={2.2}
               />
              <text x={1175} y={340} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.card} fontFamily={FONT}>
                Active CN106 DPU
              </text>
              <text x={1175} y={367} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                Absorbs 100 M/s Burst
              </text>

              {/* Card 48 MiB L3 + 6-ch DDR5 buffers (illuminate sharply in Cue 1) */}
              <g opacity={0.32 + 0.68 * kScheme}>
                <rect x={1036} y={386} width={278} height={106} rx={10} fill="#141e30" stroke={CK.ok} strokeWidth={2} />
                <text x={1052} y={414} fontSize={20} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  48 MiB Card L3
                </text>
                <text x={1052} y={439} fontSize={18} fill={C.muted} fontFamily={FONT}>
                  ≤ 32 MiB absorbed 100%
                </text>
                <rect x={1052} y={454} width={246} height={24} rx={6} fill="#0c1222" stroke={C.line} />
                <rect
                  x={1052}
                  y={454}
                  width={Math.max(10, 246 * clamp01(cardBufFill))}
                  height={24}
                  rx={6}
                  fill={CK.ok}
                  opacity={0.9}
                />

                <rect x={1036} y={512} width={278} height={118} rx={10} fill="#181c2e" stroke={CK.card} strokeWidth={2} />
                <text x={1052} y={540} fontSize={20} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                  6-ch Card DDR5
                </text>
                <text x={1052} y={565} fontSize={18} fill={C.muted} fontFamily={FONT}>
                  &gt; 48 MiB offloaded here
                </text>
                <rect x={1052} y={582} width={246} height={28} rx={6} fill="#0c1222" stroke={C.line} />
                <rect
                  x={1052}
                  y={582}
                  width={Math.max(10, 246 * clamp01(0.35 + 0.55 * burstCycle))}
                  height={28}
                  rx={6}
                  fill={CK.card}
                  opacity={0.85}
                />
              </g>
               {/* Paced forward refill arrow (top) */}
               <path
                 d={pathD([
                   [1336, 434],
                   [1454, 434],
                 ])}
                 stroke={CK.push}
                 strokeWidth={6}
                 opacity={0.35}
                 fill="none"
               />
               <PacketStream
                 pts={[
                   [1336, 434],
                   [1452, 434],
                 ]}
                 travel={rt * 150}
                 gap={38}
                 size={11}
                 color={CK.push}
                 opacity={0.95}
               />
              <text x={1395} y={408} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.push} fontFamily={MONO}>
                Refill
              </text>

              {/* Credit return arrow (bottom, reveals in Cue 1) */}
              <g opacity={0.2 + 0.8 * kScheme}>
                <path
                  d={pathD([
                    [1454, 524],
                    [1336, 524],
                  ])}
                  stroke={CK.ok}
                  strokeWidth={5}
                  opacity={0.35}
                  fill="none"
                />
                <PacketStream
                  pts={[
                    [1452, 524],
                    [1336, 524],
                  ]}
                  travel={rt * 110}
                  gap={52}
                  size={10}
                  color={CK.ok}
                  opacity={0.95}
                />
                <text x={1395} y={558} textAnchor="middle" fontSize={18} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  Credits
                </text>
                <text x={1395} y={582} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                  batch=512
                </text>
              </g>
               {/* Host box (clean & tiny hot ring) */}
               <rect
                 x={1458}
                 y={308}
                 width={376}
                 height={346}
                 rx={14}
                 fill="#101729"
                 stroke={CK.ok}
                 strokeWidth={2.2}
               />
               <text x={1482} y={340} fontSize={22} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
                 Host (saturn) · 100% Clean
               </text>
 
               {/* 8 MiB DDIO / LLC containing 256 KiB Hot Ring */}
               <rect
                 x={1480}
                 y={362}
                 width={332}
                 height={176}
                 rx={12}
                 fill="rgba(67,197,158,0.10)"
                 stroke={CK.push}
                 strokeWidth={2.2}
               />
              <text x={1498} y={392} fontSize={20} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                8 MiB DDIO / 60 MiB LLC
              </text>
              <text x={1498} y={419} fontSize={18} fill={CK.ok} fontFamily={FONT}>
                256 KiB Hot Ring (3% DDIO)
              </text>

              {/* Animated RingStrip showing in-place hot reuse */}
              <RingStrip
                x={1498}
                y={452}
                n={10}
                cellW={24}
                cellH={36}
                gap={6}
                state={(i) => {
                  const d = (i - activeSlot + 10) % 10;
                  if (d < 4) return "ok";
                  if (d < 7) return "hot";
                  return "empty";
                }}
                highlightRange={[0, 9]}
                highlightColor={CK.ok}
                opacity={0.95}
              />
              <text x={1646} y={519} textAnchor="middle" fontSize={18} fontWeight={700} fill={C.text} fontFamily={MONO}>
                In-place refill · 0.0% miss
              </text>

              {/* Host DDR5 untouched */}
              <rect
                x={1480}
                y={558}
                width={332}
                height={74}
                rx={10}
                fill="#11182c"
                stroke={C.line}
                strokeDasharray="6 4"
                strokeWidth={1.8}
              />
              <text x={1646} y={588} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                Host DDR5: Untouched
              </text>
              <text x={1646} y={616} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                10.7 MiB (−98.9% DRAM)
              </text>
 
               {/* Bottom summary badge inside Scheme B */}
               <rect x={1016} y={674} width={818} height={48} rx={10} fill="rgba(67,197,158,0.14)" stroke={CK.ok} strokeWidth={1.5} />
               <text x={1425} y={699} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
                 100.0% Host DDIO hit · Zero host LLC pollution · Zero coherence needed
               </text>
             </g>
           </g>
         )}
 
         {/* ================= PHASE 2: BURST IMPACT CHART + CONSERVATION DUALITY (CUES 3..5) ================= */}
         {chartOp > 0.005 && (
           <g opacity={chartOp}>
            {/* Left panel frame for MRV-158 Burst Miss Curve */}
            <rect
              x={56}
              y={142}
              width={892}
              height={568}
              rx={20}
              fill="rgba(20,27,46,0.94)"
              stroke={C.line}
              strokeWidth={2}
            />
            <text x={86} y={180} fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host LLC Read Miss Rate vs Burst Size
            </text>
            <text x={86} y={206} fontSize={18} fill={C.muted} fontFamily={MONO}>
              MRV-158 · 100 M/s bursts · 4 bursts/run · log₂ burst scale
            </text>

            {/* Vertical cache boundary markers: 8 MiB DDIO & 60 MiB LLC (badges sit cleanly above cy1=256) */}
            <g opacity={0.92}>
              <line
                x1={ddioX}
                y1={cy1}
                x2={ddioX}
                y2={cy0}
                stroke={CK.cxl}
                strokeWidth={2}
                strokeDasharray="7 6"
              />
              <rect x={ddioX - 71} y={cy1 - 36} width={142} height={30} rx={7} fill="#141b2e" stroke={CK.cxl} strokeWidth={1.5} />
              <text x={ddioX} y={cy1 - 21} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
                8 MiB DDIO
              </text>

              <line
                x1={llcX}
                y1={cy1}
                x2={llcX}
                y2={cy0}
                stroke={CK.card}
                strokeWidth={2}
                strokeDasharray="7 6"
              />
              <rect x={llcX - 71} y={cy1 - 36} width={142} height={30} rx={7} fill="#141b2e" stroke={CK.card} strokeWidth={1.5} />
              <text x={llcX} y={cy1 - 21} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                60 MiB LLC
              </text>
            </g>

            {/* Axes */}
            <Axes
              x0={cx0}
              x1={cx1}
              y0={cy0}
              y1={cy1}
              sx={sx}
              sy={sy}
              xticks={[
                [64, "64K"],
                [256, "256K"],
                [1024, "1M"],
                [4096, "4M"],
                [16384, "16M"],
                [65536, "64M"],
                [131072, "128M"],
              ]}
              yticks={[
                [0, "0%"],
                [25, "25%"],
                [50, "50%"],
                [75, "75%"],
                [100, "100%"],
              ]}
              xlabel="Burst Size (KiB / MiB)"
              size={22}
            />

            {/* Scheme A Curve (red climb to 91.4%) */}
            <Series pts={MISS_A} sx={sx} sy={sy} k={kCurve} color={CK.trap} w={5.5} r={6.5} />

            {/* Scheme B Curve (flat glowing green on 0.0% floor — wide translucent underlay instead of glow filter) */}
            <Series pts={MISS_B} sx={sx} sy={sy} k={kCurve} color={CK.ok} w={13} dots={false} opacity={0.22} />
            <Series pts={MISS_B} sx={sx} sy={sy} k={kCurve} color={CK.ok} w={5.5} r={6} />

            {/* Endpoint callout badges placed cleanly in open whitespace (ZERO curve overlap!) */}
            {kCurve > 0.85 && (
              <g opacity={clamp01((kCurve - 0.85) / 0.15)}>
                {/* Scheme A callout in open upper-left with dashed leader to 48M knee */}
                <path
                  d={`M 484 328 L ${sx(49152) - 10} ${sy(80.0)}`}
                  stroke={CK.trap}
                  strokeWidth={2}
                  strokeDasharray="5 4"
                  opacity={0.75}
                />
                <rect
                  x={186}
                  y={296}
                  width={298}
                  height={64}
                  rx={12}
                  fill="rgba(249,65,68,0.18)"
                  stroke={CK.trap}
                  strokeWidth={2}
                />
                <text
                  x={335}
                  y={318}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={20}
                  fontWeight={800}
                  fill={CK.trap}
                  fontFamily={MONO}
                >
                  Scheme A (128 MiB)
                </text>
                <text
                  x={335}
                  y={344}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={20}
                  fontWeight={700}
                  fill={C.text}
                  fontFamily={MONO}
                >
                  Climbs to 91.4% miss
                </text>

                {/* Scheme B 0.0% callout in open lower-right (generous padding & clear of red curve) */}
                <rect
                  x={sx(131072) - 258}
                  y={sy(0) - 62}
                  width={256}
                  height={44}
                  rx={10}
                  fill="rgba(67,197,158,0.18)"
                  stroke={CK.ok}
                  strokeWidth={2}
                />
                <text
                  x={sx(131072) - 130}
                  y={sy(0) - 40}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={20}
                  fontWeight={800}
                  fill={CK.ok}
                  fontFamily={MONO}
                >
                  Scheme B: 0.0% miss
                </text>
              </g>
            )}
 
             {/* ---------- RIGHT PANEL (CUE 4..5): SYSTEM-WIDE DRAM CONSERVATION DUALITY ---------- */}
             {dualOp > 0.005 && (
               <g opacity={dualOp}>
                 {/* Outer right container */}
                 <rect
                   x={980}
                   y={142}
                   width={884}
                   height={732}
                   rx={20}
                   fill="rgba(20,27,46,0.94)"
                   stroke={C.line}
                   strokeWidth={2}
                 />
                 <text x={1012} y={182} fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
                   System-Wide DRAM Conservation Duality
                 </text>
                 <text x={1012} y={208} fontSize={18} fill={C.muted} fontFamily={MONO}>
                   Host IMC + CN106 6-ch DDR5 accesses per 64 B packet
                 </text>
 
                 {/* REGIME 1: <= 32 MiB (True Elimination) */}
                 <rect
                   x={1010}
                   y={230}
                   width={824}
                   height={264}
                   rx={16}
                   fill="#10182b"
                   stroke={CK.ok}
                   strokeWidth={2.2}
                 />
                 <rect x={1030} y={248} width={164} height={32} rx={8} fill={`${CK.ok}24`} stroke={CK.ok} strokeWidth={1.5} />
                 <text x={1112} y={264} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                   BURST ≤ 32 MiB
                 </text>
                 <text x={1210} y={265} dominantBaseline="central" fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
                   True System DRAM Elimination
                 </text>
                <text x={1032} y={302} fontSize={20} fill={C.muted} fontFamily={FONT}>
                  CN106 48 MiB L3 absorbs 100% of burst (0.00 card DDR5)
                </text>

                {/* Regime 1 bars */}
                <text x={1032} y={346} fontSize={20} fontWeight={700} fill={CK.trap} fontFamily={MONO}>
                  Scheme A (32M)
                </text>
                <SplitBar
                  x={1248}
                  y={322}
                  w={420}
                  h={38}
                  max={2.1}
                  k={kDual}
                  segs={[{ label: "Host 1.60/pkt", value: 1.6, color: CK.trap }]}
                  totalText="1.60/pkt"
                  totalColor={CK.trap}
                />

                <text x={1032} y={406} fontSize={20} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
                  Scheme B (≤32M)
                </text>
                <SplitBar
                  x={1248}
                  y={382}
                  w={420}
                  h={38}
                  max={2.1}
                  k={kDual}
                  segs={[{ label: "", value: 0.03, color: CK.ok }]}
                  totalText="≈ 0.02/pkt total!"
                  totalColor={CK.ok}
                />

                <text x={1032} y={468} fontSize={19} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
                  Host 0.02 + Card 0.00 = ≈ 0.02/pkt (vs 0.70–1.60 A)
                </text>

                {/* REGIME 2: > 48 MiB -> 128 MiB (100% Host Offload) */}
                <rect
                  x={1010}
                  y={516}
                  width={824}
                  height={334}
                  rx={16}
                  fill="#10182b"
                  stroke={CK.card}
                  strokeWidth={2.2}
                />
                <rect x={1030} y={534} width={220} height={32} rx={8} fill={`${CK.card}24`} stroke={CK.card} strokeWidth={1.5} />
                <text x={1140} y={550} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                  &gt; 48 MiB (128 MiB)
                </text>
                <text x={1266} y={551} dominantBaseline="central" fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  100% Offload Off Host
                </text>
                <text x={1032} y={588} fontSize={20} fill={C.muted} fontFamily={FONT}>
                  Exact dual: 2× DRAM shifted onto NIC&apos;s 6-ch DDR5
                </text>

                {/* Regime 2 bars */}
                <text x={1032} y={638} fontSize={20} fontWeight={700} fill={CK.trap} fontFamily={MONO}>
                  Scheme A (128M)
                </text>
                <SplitBar
                  x={1248}
                  y={612}
                  w={420}
                  h={42}
                  max={2.1}
                  k={kDual}
                  segs={[{ label: "Host DDR5: 1.90", value: 1.9, color: CK.trap }]}
                  totalText="1.90/pkt"
                  totalColor={CK.trap}
                />

                <text x={1032} y={704} fontSize={20} fontWeight={700} fill={CK.card} fontFamily={MONO}>
                  Scheme B (128M)
                </text>
                <SplitBar
                  x={1248}
                  y={678}
                  w={420}
                  h={42}
                  max={2.1}
                  k={kDual}
                  segs={[
                    { label: "", value: 0.02, color: CK.ok },
                    { label: "Card 6-ch DDR5: 1.98", value: 1.98, color: CK.card },
                  ]}
                  totalText="2.00/pkt"
                  totalColor={CK.card}
                />

                {/* Legend for Regime 2 */}
                <rect x={1032} y={743} width={16} height={16} rx={4} fill={CK.trap} />
                <text x={1056} y={757} fontSize={19} fill={C.muted} fontFamily={MONO}>
                  Host A (971M)
                </text>
                <rect x={1282} y={743} width={16} height={16} rx={4} fill={CK.ok} />
                <text x={1306} y={757} fontSize={19} fill={CK.ok} fontFamily={MONO}>
                  Host B (10.7M)
                </text>
                <rect x={1542} y={743} width={16} height={16} rx={4} fill={CK.card} />
                <text x={1566} y={757} fontSize={19} fill={CK.card} fontFamily={MONO}>
                  Card B (1,014M)
                </text>
                <rect x={1032} y={782} width={780} height={46} rx={10} fill="rgba(248,150,30,0.12)" stroke={CK.card} strokeWidth={1.4} />
                <text x={1422} y={806} textAnchor="middle" dominantBaseline="central" fontSize={21} fontWeight={700} fill={C.text} fontFamily={FONT}>
                  Host memory bandwidth &amp; LLC stay 100% shielded from bursts
                </text>
               </g>
             )}
           </g>
         )}
       </Stage>
 
       {/* ================= HTML OVERLAYS (OUTSIDE <Stage>) ================= */}
 
       {/* Cue 2: Zero-Backlog Fast Path check pills strip (y 762..872) */}
       {archOp > 0.005 && kFast > 0.005 && (
         <div
           style={{
             position: "absolute",
             left: 56,
             top: 762,
             width: 1808,
             opacity: archOp * kFast,
             transform: `translateY(${lerp(18, 0, kFast)}px)`,
             background: "linear-gradient(90deg, rgba(67,197,158,0.14), rgba(20,27,46,0.95) 28%, rgba(20,27,46,0.95))",
             border: `2px solid ${CK.ok}`,
             borderRadius: 16,
             padding: "14px 24px",
             boxSizing: "border-box",
             display: "flex",
             alignItems: "center",
             justifyContent: "space-between",
             gap: 20,
           }}
         >
           <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 265 }}>
             <div
               style={{
                 width: 34,
                 height: 34,
                 borderRadius: 999,
                 background: CK.ok,
                 color: C.bg,
                 fontWeight: 900,
                 fontSize: 22,
                 display: "flex",
                 alignItems: "center",
                 justifyContent: "center",
               }}
             >
               ✓
             </div>
             <div>
              <div style={{ fontSize: 22, fontWeight: 800, color: CK.ok }}>Zero-Backlog Fast Path</div>
              <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>Credit-check overhead</div>
            </div>
          </div>

          {[
            { k: "W=1 mean: +6 ns", sub: "0.887 → 0.893 µs" },
            { k: "W=32: 13.05 → 13.30 M/s", sub: "Zero regression" },
            { k: "Stream: 176.9 M/s", sub: "11.32 GB/s · 100% DDIO" },
          ].map((item, idx) => (
            <div
              key={idx}
              style={{
                flex: 1,
                background: "rgba(11,16,32,0.8)",
                border: `1.5px solid ${CK.ok}66`,
                borderRadius: 12,
                padding: "10px 18px",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 22, fontWeight: 800, color: C.text, fontFamily: MONO }}>{item.k}</div>
              <div style={{ fontSize: 18, color: CK.ok, fontFamily: MONO, marginTop: 2 }}>{item.sub}</div>
             </div>
           ))}
         </div>
       )}
 
       {/* Cue 3: Right-side 3-stat impact stack at 128 MiB (fades out at T[4]) */}
       {impactOp > 0.005 && (
         <>
           <div
             style={{
               position: "absolute",
               left: 980,
               top: 142,
               width: 884,
               opacity: impactOp,
               display: "flex",
               alignItems: "center",
               justifyContent: "space-between",
               padding: "0 4px",
             }}
           >
             <div style={{ fontSize: 26, fontWeight: 800, color: C.text }}>
               128 MiB Burst Impact (4 × 128 MiB = 512 MiB)
             </div>
             <div
               style={{
                 fontSize: 18,
                 fontWeight: 800,
                 color: CK.ok,
                 fontFamily: MONO,
                 background: `${CK.ok}1f`,
                 border: `1.5px solid ${CK.ok}`,
                 borderRadius: 999,
                 padding: "4px 14px",
               }}
             >
               MRV-158 · 108/108 runs
             </div>
           </div>
 
           <HeroStat
             x={980}
             y={198}
             w={884}
             h={190}
             value={`${missAVal}% → 0.0%`}
             label="Host LLC Read Misses (Scheme A → Scheme B)"
             sub="Scheme B holds 0.01–0.02% across the entire 64 KiB → 128 MiB sweep"
             color={CK.push}
             opacity={impactOp}
             dy={lerp(20, 0, prog(t, T[3], 0.6))}
           />
 
           <HeroStat
             x={980}
             y={408}
             w={884}
             h={190}
             value={`${ddioAVal}% → 100.0%`}
             label="Host Intel DDIO Hit Rate"
             sub="256 KiB hot host ring stays permanently resident in 8 MiB DDIO window"
             color={CK.cxl}
             opacity={impactOp}
             dy={lerp(20, 0, prog(t, T[3] + 0.15, 0.6))}
           />
 
           <HeroStat
             x={980}
             y={618}
             w={884}
             h={246}
             value={`−${dramCut}%`}
             label="Host IMC DDR5 DRAM Traffic Cut"
             sub="971.5 MiB (1.90 lines/pkt) → 10.7 MiB (≈ 0.02 lines/pkt)"
             color={CK.ok}
             opacity={impactOp}
             dy={lerp(22, 0, prog(t, T[3] + 0.3, 0.6))}
           />
         </>
       )}
 
      {/* Cue 5: Bottom-left boundary strip tucked cleanly below curve (y 722..876) */}
      {boundOp > 0.005 && (
        <div
          style={{
            position: "absolute",
            left: 56,
            top: 722,
            width: 892,
            height: 154,
            opacity: boundOp,
            transform: `translateY(${lerp(16, 0, kBound)}px)`,
            background: "rgba(20,27,46,0.95)",
            border: `2px solid ${CK.push}`,
            borderRadius: 16,
            padding: "11px 14px 9px",
            boxSizing: "border-box",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
          }}
        >
          <div style={{ display: "flex", gap: 10 }}>
            {[
              { label: "Card drain >48M L3", val: "175.4 M/s", sub: "−0.14% past L3", col: CK.ok },
              { label: "128M host drain", val: "+8.4% faster", sub: "5.69 vs 6.21 ns", col: CK.push },
              { label: "Credit refill", val: "0.905 µs", sub: "1.05–1.16 cold", col: CK.cxl },
            ].map((b, i) => (
              <div
                key={i}
                style={{
                  flex: 1,
                  background: "#0d1426",
                  border: `1.2px solid ${b.col}66`,
                  borderRadius: 10,
                  padding: "6px 10px",
                }}
              >
                <div style={{ fontSize: 18, color: C.muted, fontFamily: FONT, whiteSpace: "nowrap" }}>{b.label}</div>
                <div style={{ fontSize: 23, fontWeight: 800, color: b.col, fontFamily: MONO, marginTop: 1, lineHeight: 1.15 }}>
                  {b.val}
                </div>
                <div style={{ fontSize: 18, color: C.text, fontFamily: MONO, marginTop: 1, opacity: 0.88, whiteSpace: "nowrap" }}>
                  {b.sub}
                </div>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO, textAlign: "center", whiteSpace: "nowrap" }}>
            Trade-off: 1–32 MiB 1C drain (4.44–5.66 vs 3.07–5.39 ns/pkt)
          </div>
        </div>
      )}
     </>
   );
 }

export const C10: SceneDef = {
  id: "C10",
  title: "5 \u00b7 Active-NIC 256 KiB Hot Ring",
  stage: 5,
  source: "MRV-158 (108 verified runs) \u00b7 100 M/s bursts \u00b7 64 KiB \u2192 128 MiB",
  dur: DUR,
  cues,
  C: Body,
};
