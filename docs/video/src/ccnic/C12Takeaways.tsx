import React from "react";
 import {
   C,
   Cue,
   FONT,
   MONO,
   PacketStream,
   SceneDef,
   Stage,
   lerp,
   partial,
   pathD,
   prog,
   useRealT,
   useT,
 } from "../lib";
 import type { Pt } from "../lib";
 import { CK } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 15.68, "Across all **five reports**: ordinary PCIe Gen5 already delivers **175.2 M/s**, **897 ns** 3-hop cache RTT, and **full IOMMU**—no CXL needed.", "Across all five reports, off-the-shelf P C I express gen five already delivers one hundred seventy-five million writes per second on one core, eight hundred ninety-seven nanosecond cache round trips, and full hardware I O M M U—no C X L needed."], // 0
  [16.03, 24.21, "Dropping coherence **eliminates** TX invalidation tax (**99.99993%** DDIO, **0** misses at 128 MiB), while **credits** tame real DPU bridge cliffs.", "Dropping cache coherence removes cross-link cache invalidation tax completely, while credit shaping controls real D P U bridge congestion."], // 1
  [24.56, 44.36, "**DirectPush** (small push + **native NIC DMA**, **0 DSA**): **14,701×** HoL isolation, **−4.25 µs** on 100GbE (**0.935 µs** on-card), **99.8 Gbps** on **1 core**.", "DirectPush pairs small push with native NIC D M A and zero Intel D S A: fourteen thousand seven hundred times head-of-line isolation, saving four point two five microseconds on real hundred gigabit wire, zero point nine three five microseconds on-card, and full hundred gigabit line rate from one host core."], // 2
  [44.71, 56.71, "An **active NIC** holding RX bursts behind a **256 KiB hot host ring** cuts host DRAM **−98.9%** and keeps host LLC **100% clean**.", "And an active NIC absorbing receive bursts behind a two hundred fifty-six kilobyte hot host ring slashes host D RAM traffic by ninety-eight point nine percent while keeping host caches completely clean."], // 3
  [57.06, 68.78, "**CC-NIC does not need cache coherence**—it needs cache-aware PCIe, native NIC DMA, credit flow control, and an active NIC.", "The takeaway is simple: high-speed network cards do not need hardware cache coherence. They need cache-aware P C I express, native NIC D M A, credit flow control, and an active NIC."], // 4
];
const DUR = 70.18; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

 interface Pillar {
   x: number;
   y: number;
   w: number;
   h: number;
   start: number;
   color: string;
   accent2?: string;
   badge: string;
   title: string;
  metrics: { text: string; color?: string; flex?: number }[];
   sub: string;
 }
 
 const PILLARS: Pillar[] = [
   {
     x: 60,
     y: 146,
     w: 870,
     h: 156,
     start: T[0],
     color: CK.push,
     badge: "REPORT 1",
     title: "1 · Commercial PCIe Gen5 Is Fast",
     metrics: [
       { text: "175.2 M/s (97.9%) on 1 core", color: CK.push },
       { text: "897.3 ns 3-hop cache RTT", color: CK.ok },
     ],
     sub: "Full HW IOMMU (intel_iommu=on,sm_on) · Zero CXL needed",
   },
   {
     x: 990,
     y: 146,
     w: 870,
     h: 156,
     start: T[1],
     color: CK.sdp,
     accent2: CK.card,
     badge: "REPORTS 2–3",
     title: "2–3 · Zero Coherence Tax + Credit Flow Control",
    metrics: [
      { text: "174.8 M/s instant TX reuse", color: CK.sdp, flex: 0.78 },
      { text: "99.99993% DDIO (0 misses @ 128 MiB)", color: CK.ok, flex: 1.08 },
    ],
    sub: "Credits cut 16–30 M/s p99 by 6–64× & tame PEM0 cliff",
  },
  {
    x: 60,
    y: 326,
    w: 870,
    h: 170,
    start: T[2],
    color: CK.dma,
    accent2: CK.ok,
    badge: "REPORTS 4–5",
    title: "4–5 · DirectPush + Real 100GbE (Zero DSA)",
    metrics: [
      { text: "14,701× HoL isolation", color: CK.dma, flex: 0.78 },
      { text: "−4.25 µs wire RTT (0.935 µs on-card)", color: CK.ok, flex: 1.18 },
    ],
    sub: "99.8 Gbps on 1 host core · Clean 4-layer CPU ledger · Zero DSA",
  },
  {
    x: 990,
    y: 326,
    w: 870,
    h: 170,
    start: T[3],
    color: CK.cxl,
    badge: "MRV-158",
    title: "Active-NIC RX Buffering (256 KiB Hot Ring)",
    metrics: [
      { text: "−98.9% host burst DRAM", color: CK.ok, flex: 0.84 },
      { text: "0.0% host LLC misses · 100% DDIO", color: CK.cxl, flex: 1.14 },
    ],
    sub: "0.905 µs credit refill · 175.4 M/s past 48 MiB L3 · Conservation",
  },
 ];
 
 // Four sleek converging connector paths from all 4 pillars into the thesis banner (y=564)
 const CONVERGE_PATHS: { pts: Pt[]; color: string }[] = [
   {
     pts: [
       [495, 302],
       [495, 314],
       [948, 314],
       [948, 526],
       [780, 564],
     ],
     color: CK.push,
   },
   {
     pts: [
       [1425, 302],
       [1425, 314],
       [972, 314],
       [972, 526],
       [1140, 564],
     ],
     color: CK.sdp,
   },
   {
     pts: [
       [495, 496],
       [495, 530],
       [620, 564],
     ],
     color: CK.dma,
   },
   {
     pts: [
       [1425, 496],
       [1425, 530],
       [1300, 564],
     ],
     color: CK.cxl,
   },
 ];
 
 const FORMULA_PILLS = [
   { text: "Cache-Aware PCIe", color: CK.push },
   { text: "Native NIC DMA", color: CK.dma },
   { text: "Credit Flow Control", color: CK.card },
   { text: "Active NIC", color: CK.cxl },
 ];
 
 function Body() {
   const t = useT();
   const rt = useRealT();
 
   const kFinale = prog(t, T[4], 0.85);
   const kLines = prog(t, T[4], 0.75);
   const glowPulse = 0.5 + 0.5 * Math.sin(rt * 2.4);
 
   return (
     <>
      <Stage>
        {/* Subtle dashed blueprint slots so Cues 0..3 feel architecturally balanced while assembling */}
        {PILLARS.map((p, idx) => {
          const k = prog(t, p.start, 0.65);
          const ghostOp = (1 - k) * 0.28;
          if (ghostOp <= 0.01) return null;
          return (
            <rect
              key={`ghost-${idx}`}
              x={p.x}
              y={p.y}
              width={p.w}
              height={p.h}
              rx={18}
              fill="rgba(20,27,46,0.28)"
              stroke={C.line}
              strokeWidth={1.8}
              strokeDasharray="10 8"
              opacity={ghostOp}
            />
          );
        })}
        {kFinale < 0.99 && (
          <rect
            x={110}
            y={564}
            width={1700}
            height={280}
            rx={24}
            fill="rgba(20,27,46,0.2)"
            stroke={C.line}
            strokeWidth={1.8}
            strokeDasharray="10 8"
            opacity={(1 - kFinale) * 0.25}
          />
        )}

        {/* Subtle ambient radial halo behind the finale thesis banner */}
        {kFinale > 0.01 && (
          <ellipse
            cx={960}
            cy={704}
            rx={780}
            ry={148}
            fill={CK.push}
            opacity={(0.08 + 0.04 * glowPulse) * kFinale}
            filter="url(#softglow)"
          />
        )}
         {/* Converging SVG lines from all 4 pillars into the thesis banner (Cue 4) */}
         {kLines > 0.01 &&
           CONVERGE_PATHS.map((cp, idx) => {
             const sub = partial(cp.pts, kLines);
             const d = pathD(sub);
             return (
               <g key={idx} opacity={kLines}>
                 {/* Soft wide underlay instead of #glow filter on axis-aligned segments */}
                 <path d={d} fill="none" stroke={cp.color} strokeWidth={10} opacity={0.18} strokeLinejoin="round" />
                 <path d={d} fill="none" stroke={cp.color} strokeWidth={3.2} opacity={0.9} strokeLinejoin="round" />
                 {kLines > 0.85 && (
                   <PacketStream
                     pts={cp.pts}
                     travel={rt * 145 + idx * 45}
                     gap={110}
                     size={9}
                     color={cp.color}
                     opacity={0.95}
                   />
                 )}
               </g>
             );
           })}
 
         {/* Terminal arrival dots on top edge of finale banner */}
         {kLines > 0.75 &&
           [620, 780, 1140, 1300].map((cx, idx) => {
             const cols = [CK.dma, CK.push, CK.sdp, CK.cxl];
             const op = prog(t, T[4] + 0.55, 0.35);
             return (
               <g key={cx} opacity={op}>
                 <circle cx={cx} cy={564} r={10} fill={cols[idx]} opacity={0.25} />
                 <circle cx={cx} cy={564} r={5.5} fill={cols[idx]} />
               </g>
             );
           })}
       </Stage>
 
       {/* ================= HTML OVERLAYS (OUTSIDE <Stage>) ================= */}
 
       {/* Cues 0..3: Four Pillar Cards in 2×2 grid */}
       {PILLARS.map((p, idx) => {
         const k = prog(t, p.start, 0.65);
         if (k <= 0.005) return null;
         return (
           <div
             key={idx}
             style={{
               position: "absolute",
               left: p.x,
               top: p.y,
               width: p.w,
               height: p.h,
               opacity: k,
               transform: `translateY(${lerp(20, 0, k)}px)`,
               background: "linear-gradient(155deg, rgba(20,27,46,0.96), rgba(11,16,32,0.95))",
               border: `2px solid ${p.color}`,
               borderRadius: 18,
               padding: "16px 22px",
               boxSizing: "border-box",
               boxShadow: `0 10px 28px rgba(0,0,0,0.42), inset 0 1px 0 ${p.color}44`,
               display: "flex",
               flexDirection: "column",
               justifyContent: "space-between",
             }}
           >
             {/* Header row: Title + Report badge */}
             <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
               <div style={{ fontSize: 25, fontWeight: 800, color: p.color, fontFamily: FONT, lineHeight: 1.2 }}>
                 {p.title}
               </div>
              <div
                style={{
                  fontSize: 18,
                  fontWeight: 800,
                  fontFamily: MONO,
                  color: p.accent2 ?? p.color,
                  background: `${p.accent2 ?? p.color}1c`,
                  border: `1.5px solid ${p.accent2 ?? p.color}88`,
                  borderRadius: 8,
                  padding: "3px 10px",
                  whiteSpace: "nowrap",
                }}
              >
                {p.badge}
              </div>
            </div>

            {/* Key verified metric chips */}
            <div style={{ display: "flex", gap: 12 }}>
              {p.metrics.map((m, mi) => (
                <div
                  key={mi}
                  style={{
                    flex: m.flex ?? 1,
                    background: "rgba(11,16,32,0.85)",
                    border: `1.4px solid ${(m.color ?? p.color)}66`,
                    borderRadius: 10,
                    padding: "7px 10px",
                    fontSize: 19,
                    fontWeight: 800,
                    color: m.color ?? C.text,
                    fontFamily: MONO,
                    textAlign: "center",
                    whiteSpace: "nowrap",
                  }}
                >
                  {m.text}
                </div>
              ))}
            </div>

            {/* Sub-line */}
            <div style={{ fontSize: 20, color: C.muted, fontFamily: FONT, lineHeight: 1.25 }}>
              {p.sub}
            </div>
          </div>
         );
       })}
 
       {/* Cue 4: Full-Width Glowing Finale Thesis Banner (x 110, y 564, w 1700, h 280) */}
       {kFinale > 0.005 && (
         <div
           style={{
             position: "absolute",
             left: 110,
             top: 564,
             width: 1700,
             height: 280,
             opacity: kFinale,
             transform: `translateY(${lerp(22, 0, kFinale)}px) scale(${lerp(0.97, 1, kFinale)})`,
             background: "linear-gradient(145deg, rgba(18,28,52,0.98), rgba(10,15,30,0.97))",
             border: `2.5px solid ${CK.push}`,
             borderRadius: 24,
             padding: "26px 36px 22px",
             boxSizing: "border-box",
             boxShadow: `0 16px 48px rgba(0,0,0,0.55), 0 0 ${Math.round(24 + 12 * glowPulse)}px rgba(76,201,240,0.22)`,
             display: "flex",
             flexDirection: "column",
             alignItems: "center",
             justifyContent: "space-between",
           }}
         >
           {/* Main Headline (48 px bold white/cyan) */}
           <div
             style={{
               fontSize: 48,
               fontWeight: 900,
               letterSpacing: -0.5,
               color: C.text,
               fontFamily: FONT,
               textAlign: "center",
               lineHeight: 1.12,
             }}
           >
             <span style={{ color: CK.push }}>CC-NIC</span> Does Not Need Cache Coherence
           </div>
 
           {/* 4 Glowing Formula Pills in a Row */}
           <div
             style={{
               display: "flex",
               alignItems: "center",
               justifyContent: "center",
               gap: 14,
               width: "100%",
             }}
           >
             {FORMULA_PILLS.map((fp, i) => {
               const pk = prog(t, T[4] + 0.25 + i * 0.14, 0.45);
               return (
                 <React.Fragment key={fp.text}>
                   {i > 0 && (
                     <div
                       style={{
                         fontSize: 28,
                         fontWeight: 800,
                         color: C.muted,
                         fontFamily: MONO,
                         opacity: pk,
                       }}
                     >
                       +
                     </div>
                   )}
                   <div
                     style={{
                       opacity: pk,
                       transform: `translateY(${lerp(12, 0, pk)}px)`,
                       fontSize: 24,
                       fontWeight: 800,
                       color: fp.color,
                       fontFamily: FONT,
                       background: `${fp.color}1c`,
                       border: `2px solid ${fp.color}`,
                       borderRadius: 999,
                       padding: "10px 26px",
                       boxShadow: `0 0 18px ${fp.color}30`,
                       whiteSpace: "nowrap",
                     }}
                   >
                     {fp.text}
                   </div>
                 </React.Fragment>
               );
             })}
           </div>
 
           {/* Clean Footer Provenance */}
           <div
             style={{
               fontSize: 20,
               color: C.muted,
               fontFamily: MONO,
               letterSpacing: 0.3,
               textAlign: "center",
             }}
           >
             Marvell CN106xx · PCIe Gen5 ×4 · 100GbE · 3,540+ Verified Zero-Error Runs · Zero Intel DSA
           </div>
         </div>
       )}
     </>
   );
 }

export const C12: SceneDef = {
  id: "C12",
  title: "Takeaways: Fast NICs Without Coherence",
  stage: 5,
  source: "MRV-113 \u00b7 MRV-120 \u00b7 DirectPush pure-PCIe synthesis",
  dur: DUR,
  cues,
  C: Body,
};
