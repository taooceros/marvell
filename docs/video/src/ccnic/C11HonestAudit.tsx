import React from "react";
import { C, Cue, FONT, MONO, SceneDef, Stage, clamp01, fade, lerp, prog, useRealT, useT } from "../lib";
import { CK, HeroStat, VerdictRow } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 11.30, "Every experiment underwent **independent multi-model audit (MRV-37)**—actively **tightening or retracting five optimistic claims**.", "What shaped this entire story is scientific discipline. Every experiment went through independent multi-model verification, and whenever a claim did not hold up, we tightened or retracted it."], // 0
  [11.65, 25.31, "**1. Host-only 20 M/s ≤ 2 µs**: falsified by **[18, 25]** gap (`7.01 µs` best). **2. Stride fix**: `64–4224 B` all stay locked at `~13 M/s`.", "First, host-only credit shaping cannot hit two microseconds at twenty million per second because of the eighteen-versus-twenty-five gap. Second, widening ring stride up to four kilobytes did not fix the thirteen million bridge cliff."], // 1
  [25.66, 40.90, "**3. Sync-tail SDP retracted (MRV-166)**: async tail poll zeroes `late_tails` (**−459 cyc**), leaving **1.26–1.53×** DPDK stack tax (`1–4 KiB` bare-ring equal).", "Third, we retracted our early synchronous tail-spin claim against S D P: M R V one sixty-six proved non-blocking tail polling eliminates the wait, separating generic D P D K stack overhead from real sixty-four byte hardware gains."], // 2
  [41.25, 54.25, "**4. Exact CC-NIC Fig. 15 & host limits**: only **−20%** is TX retention; `2 KiB` drop is `1/32` LLC aliasing; `128 MiB` fits `64 × 2 MiB` IOTLB.", "Fourth, we corrected C C NIC figure fifteen to separate the twenty percent retention tax from stride aliasing, and noted where our one hundred twenty-eight megabyte pool stays inside the two-megabyte hugepage I O T L B."], // 3
  [54.60, 66.12, "**5. Exposed ceilings**: 4C `33.5 M` lockstep (`1.91 µs`), `SMQ[0]` **99.8 Mpps** default, and `1–32 MiB` 1C drain trade-off—all tracked in Backlog.", "Fifth, we exposed every remaining ceiling—credit lockstep, the default ninety-nine point eight million packet scheduler cap, and the mid-burst single-core drain trade-off—all tracked cleanly in backlog."], // 4
];
const DUR = 67.52; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

 interface AuditItem {
   num: string;
   y: number;
   start: number;
   badge: string;
   color: string;
   title: string;
   detail: string;
   resolvedTag?: string;
 }
 
 const ITEMS: AuditItem[] = [
   {
     num: "1",
     y: 254,
     start: T[1],
     badge: "TIGHTENED",
     color: CK.card,
     title: "Host-only 20 M/s ≤ 2 µs",
     detail: "Falsified by [18, 25] arithmetic gap (best 7.01 µs; honest safe zone ≤ 13.5 M/s 1C / ≤ 10 M/s 4C)",
   },
   {
     num: "2",
     y: 376,
     start: T[1] + 2.6,
     badge: "FALSIFIED",
     color: CK.trap,
     title: "Ring-stride fix for PEM0 cliff",
     detail: "64 / 128 / 4096 / 4224 B strides all lock at 13.1–13.6 M/s (proves inbound bridge serialization)",
   },
   {
     num: "3",
     y: 498,
     start: T[2],
     badge: "RETRACTED",
     color: CK.trap,
     title: "Sync-tail SDP late_tails claim (MRV-166)",
     detail: "Async tail poll zeroes late_tails (−459 cyc), leaving 1.26–1.53× [INF] DPDK stack tax; 1–4 KiB bare-ring equal",
     resolvedTag: "MRV-166 FIXED",
   },
   {
     num: "4",
     y: 620,
     start: T[3],
     badge: "CORRECTED",
     color: CK.cxl,
     title: "CC-NIC Fig. 15 & host pool scope",
     detail: "Only −20% is TX retention (−50% 4 KiB stride, −73% host pool); 128 MiB KV stays inside 64 × 2 MiB IOTLB",
   },
   {
     num: "5",
     y: 742,
     start: T[4],
     badge: "BOUNDARY",
     color: CK.push,
     title: "Exposed hardware & prototype ceilings",
     detail: "4C 33.5 M credit lockstep (1.91 µs), SMQ[0] 99.8 Mpps default, 1–32 MiB 1C drain trade-off — all tracked",
   },
 ];
 
 function Body() {
   const t = useT();
   const rt = useRealT();
 
  const kBanner = prog(t, T[0], 0.7);
  const introOp = fade(t, T[0] + 0.15, T[1], 0.45);
  const pulse = 0.5 + 0.5 * Math.sin(rt * 2.8);
   // Rail extends smoothly down to whichever item is currently revealed
   const maxItemProg = ITEMS.reduce((acc, item, i) => {
     const k = prog(t, item.start, 0.55);
     return Math.max(acc, i * k + k);
   }, 0);
   const railTop = 234;
   const railBottom = lerp(234, 790, clamp01(maxItemProg / ITEMS.length));
 
   // Highlight active cue's rows with subtle left glow ring
   const activeIdx =
     t >= T[4] ? 4 : t >= T[3] ? 3 : t >= T[2] ? 2 : t >= T[1] + 2.6 ? 1 : t >= T[1] ? 0 : -1;
 
   return (
     <>
       <Stage>
         {/* Subtle vertical verification spine connecting banner to the 5 audit rows */}
         {railBottom > railTop + 4 && (
           <g opacity={0.85}>
             <line
               x1={88}
               y1={railTop}
               x2={88}
               y2={railBottom}
               stroke={CK.ok}
               strokeWidth={10}
               opacity={0.14}
             />
             <line
               x1={88}
               y1={railTop}
               x2={88}
               y2={railBottom}
               stroke={C.line}
               strokeWidth={3}
             />
           </g>
         )}
 
         {/* Animated shield + check emblem anchored on the left of top banner */}
         <g transform="translate(94, 182)" opacity={kBanner}>
           <circle cx={0} cy={0} r={33 + pulse * 3} fill="none" stroke={CK.ok} strokeWidth={2} opacity={0.25 + 0.2 * pulse} />
           <path
             d="M 0 -28 L 24 -18 L 24 2 C 24 17 12 26 0 31 C -12 26 -24 17 -24 2 L -24 -18 Z"
             fill="rgba(67,197,158,0.16)"
             stroke={CK.ok}
             strokeWidth={2.8}
             strokeLinejoin="round"
           />
           <path
             d="M -10 1 L -2 9 L 12 -8"
             fill="none"
             stroke={CK.ok}
             strokeWidth={4}
             strokeLinecap="round"
             strokeLinejoin="round"
           />
         </g>
 
         {/* Numbered timeline nodes + horizontal tick connectors for each row */}
         {ITEMS.map((item, idx) => {
           const k = prog(t, item.start, 0.55);
           if (k <= 0.01) return null;
           const cy = item.y + 48;
           const isCur = idx === activeIdx || (idx === 0 && activeIdx === 1 && t < T[2]);
           return (
             <g key={item.num} opacity={k}>
               <line x1={108} y1={cy} x2={132} y2={cy} stroke={item.color} strokeWidth={2.5} opacity={0.8} />
               {isCur && (
                 <circle
                   cx={88}
                   cy={cy}
                   r={24 + pulse * 4}
                   fill="none"
                   stroke={item.color}
                   strokeWidth={2}
                   opacity={0.35 * (1 - pulse * 0.4)}
                 />
               )}
               <circle
                 cx={88}
                 cy={cy}
                 r={20}
                 fill="#101729"
                 stroke={item.color}
                 strokeWidth={2.8}
               />
               <text
                 x={88}
                 y={cy}
                 textAnchor="middle"
                 dominantBaseline="central"
                 fontSize={19}
                 fontWeight={800}
                 fill={item.color}
                 fontFamily={MONO}
               >
                 {item.num}
               </text>
             </g>
           );
         })}
 
         {/* Subtle resolved accent pill on Row 3 (Red -> Green clean ledger transition) */}
         {t >= T[2] + 0.3 && (
           <g opacity={prog(t, T[2] + 0.3, 0.6)}>
            <rect
              x={1674}
              y={524}
              width={190}
              height={42}
              rx={21}
              fill="rgba(67,197,158,0.16)"
              stroke={CK.ok}
              strokeWidth={2}
            />
            <text
              x={1769}
              y={545}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={18}
              fontWeight={800}
              fill={CK.ok}
              fontFamily={MONO}
            >
              ✓ LEDGER CLEAN
            </text>
          </g>
         )}
       </Stage>
 
       {/* ================= HTML OVERLAYS (OUTSIDE <Stage>) ================= */}
 
       {/* Cue 0: Top Summary Banner (y 136..228) */}
       <div
         style={{
           position: "absolute",
           left: 140,
           top: 136,
           width: 1724,
           height: 92,
           opacity: kBanner,
           transform: `translateY(${lerp(14, 0, kBanner)}px)`,
           background: "linear-gradient(90deg, rgba(67,197,158,0.14), rgba(20,27,46,0.95) 26%, rgba(20,27,46,0.95))",
           border: `2px solid ${CK.ok}`,
           borderRadius: 18,
           padding: "14px 26px",
           boxSizing: "border-box",
           display: "flex",
           alignItems: "center",
           justifyContent: "space-between",
           gap: 20,
         }}
       >
         <div>
           <div style={{ fontSize: 27, fontWeight: 800, color: C.text, fontFamily: FONT, lineHeight: 1.2 }}>
             Independent Multi-Model Verification (MRV-37) · 3,540+ Zero-Error Runs
           </div>
           <div style={{ fontSize: 20, color: CK.ok, fontFamily: MONO, marginTop: 4 }}>
             5 Optimistic Claims Actively Tightened, Corrected, or Retracted
           </div>
         </div>
         <div
           style={{
             display: "flex",
             gap: 12,
             flexShrink: 0,
           }}
         >
           {[
             { txt: "0 torn / 0 corrupt", col: CK.ok },
             { txt: "100% HW PMU", col: CK.push },
           ].map((tag, i) => (
             <div
               key={i}
               style={{
                 fontSize: 18,
                 fontWeight: 800,
                 fontFamily: MONO,
                 color: tag.col,
                 background: `${tag.col}1a`,
                 border: `1.5px solid ${tag.col}88`,
                 borderRadius: 10,
                 padding: "8px 14px",
                 whiteSpace: "nowrap",
               }}
             >
               {tag.txt}
             </div>
           ))}
         </div>
       </div>
 
      {/* Cue 0: Three high-level scientific discipline overview cards (fade out cleanly at T[1]) */}
      {introOp > 0.005 && (
        <>
          <HeroStat
            x={132}
            y={308}
            w={554}
            h={248}
            value="3,540+"
            label="Hardware-Verified Runs"
            sub="> 2.5B msgs/frames · 0 torn · 0 corrupt · 0 SEI-UER"
            color={CK.ok}
            opacity={introOp}
            dy={lerp(22, 0, prog(t, T[0] + 0.15, 0.6))}
          />
          <HeroStat
            x={721}
            y={308}
            w={554}
            h={248}
            value="MRV-37"
            label="Independent Multi-Model Audit"
            sub="Every experiment & claim independently challenged"
            color={CK.push}
            opacity={introOp}
            dy={lerp(22, 0, prog(t, T[0] + 0.3, 0.6))}
          />
          <HeroStat
            x={1310}
            y={308}
            w={554}
            h={248}
            value="5 Claims"
            label="Tightened or Retracted"
            sub="Falsified assumptions replaced with exact measured limits"
            color={CK.card}
            opacity={introOp}
            dy={lerp(22, 0, prog(t, T[0] + 0.45, 0.6))}
          />
          <div
            style={{
              position: "absolute",
              left: 132,
              top: 604,
              width: 1732,
              opacity: introOp,
              background: "rgba(20,27,46,0.88)",
              border: `1.5px solid ${C.line}`,
              borderRadius: 16,
              padding: "20px 28px",
              boxSizing: "border-box",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            {[
              { tag: "1 · TIGHTENED", txt: "20 M/s ≤ 2 µs gap", col: CK.card },
              { tag: "2 · FALSIFIED", txt: "PEM0 stride fix", col: CK.trap },
              { tag: "3 · RETRACTED", txt: "Sync-tail SDP claim", col: CK.trap },
              { tag: "4 · CORRECTED", txt: "Fig. 15 & IOTLB scope", col: CK.cxl },
              { tag: "5 · BOUNDARY", txt: "Backlog ceilings", col: CK.push },
            ].map((s, idx) => (
              <div key={idx} style={{ textAlign: "center", flex: 1 }}>
                <div style={{ fontSize: 19, fontWeight: 800, color: s.col, fontFamily: MONO }}>{s.tag}</div>
                <div style={{ fontSize: 20, color: C.muted, fontFamily: FONT, marginTop: 4 }}>{s.txt}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Cues 1..4: Five VerdictRows cleanly spaced from y=254 to y=840 */}
      {ITEMS.map((item) => {
        const k = prog(t, item.start, 0.55);
        if (k <= 0.005) return null;
         return (
           <VerdictRow
             key={item.num}
             x={132}
             y={item.y}
            w={item.resolvedTag ? 1526 : 1732}
             badge={item.badge}
             badgeColor={item.color}
             title={item.title}
             detail={item.detail}
             opacity={k}
             dy={lerp(18, 0, k)}
           />
         );
       })}
     </>
   );
 }

export const C11: SceneDef = {
  id: "C11",
  title: "Scientific Rigor & Boundaries",
  stage: 5,
  source: "MRV-120 \u00b7 MRV-37 multi-model reviews \u00b7 5 hypotheses tightened",
  dur: DUR,
  cues,
  C: Body,
};
