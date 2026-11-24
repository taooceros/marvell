import React from "react";
import { Cue, FONT, Pt, SceneDef, clamp01, lerp, prog, useT } from "../lib";
import { BigStat, Cell, Flow, HBar, HBars, HostNic, P, Stage, StepBadge, TAB, Tag, Text, alpha, hnGeom, hnRoutes, win } from "./kit";
import { Block, TagC, Tokens, landOp, stream } from "./_CcSceneCost";

// <cues>
const cues: Cue[] = [
  [0.60, 10.04, "Push is ideal for small messages. But a 4 KiB payload would cost the CPU **64 separate line writes**.", "Pushing is ideal for small messages. But a four kilobyte payload would cost the C P U sixty-four separate line writes, burning cycles the application needs."], // 0
  [10.39, 22.75, "So large payloads take another road: push **one 64-byte command**, and the NIC's own **DMA engine** moves the bytes.", "So large payloads take a different road. The host pushes a single sixty-four byte command, and the card's own D M A engine, which every nick already has, moves the bytes. No extra accelerator is needed."], // 1
  [23.10, 34.18, "Ordering stays cheap: **one fence per message**, before the command, so the DMA never reads stale data.", "Ordering stays cheap here too. The host fences once, after writing the payload and before the command, so the D M A engine never reads stale data. That is one fence per message, not one per line."], // 2
  [34.53, 47.79, "On the way back, the NIC writes the **payload first**, then a **64-byte completion**. PCIe keeps them in order.", "On the way back, the D M A engine writes the payload first, then a sixty-four byte completion, on the same path. P C I E keeps these writes in order, so once the host sees the completion, the whole payload is already there."], // 3
  [48.14, 61.30, "Up to 256 B: pushed inline. From 512 B: by DMA. At 4 KiB, the host's CPU share drops **46.6% → 16.3%**.", "Messages up to two hundred fifty-six bytes are pushed inline; from five hundred twelve bytes up, they go by D M A. At four kilobytes, the host's C P U share drops from forty-six point six to sixteen point three percent."], // 4
  [61.65, 72.49, "Mixing needs care: with **one shared credit pool**, a bulk flood starves small messages. Their p99: **40.5 ms**.", "Mixing the two needs care. If small and large messages share one credit pool, a flood of bulk transfers starves the small ones, and their tail latency reaches forty point five milliseconds."], // 5
  [72.84, 80.72, "Give each class **its own credit window**, and small messages stay at **2.75 µs** under the same flood.", "Give each class its own credit window, and the small messages stay at two point seven five microseconds under exactly the same flood."], // 6
  [81.07, 96.27, "Against a fairly tuned standard interface, DirectPush needs **1.5–2.1× fewer** host CPU cycles per packet.", "And against the card's standard interface, tuned as fairly as we could, DirectPush needs one point five to two point one times fewer host C P U cycles per packet. For large packets, much of that saving is software overhead; for small ones, it is the interface itself."], // 7
];
const DUR = 97.67; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
const G = hnGeom(1);
const RT = hnRoutes(G);
const GREY = "#8f9ab3";

/** The 4 KiB payload in host memory: 16 × 4 lines of 64 B, pushed line by line in b0. */
const GRID: Pt[] = Array.from({ length: 64 }, (_, i): Pt => [255 + 22 * (i % 16), 651 + 16 * Math.floor(i / 16)]);
const W_START = T[0] + 2.6;
const W_PER = 0.09;
/** b1–b2: one 64 B command (core → DMA engine), the DMA read request (engine → host memory), the data coming back. */
const CMD: Pt[] = [
  [420, 348],
  [665, 348],
  [665, 486],
  [1262, 486],
  [1262, 348],
  [1280, 348],
];
const DREQ: Pt[] = [
  [1280, 376],
  [1250, 376],
  [1250, 514],
  [665, 514],
  [665, 668],
  [640, 668],
];
const DATA: Pt[] = [
  [640, 692],
  [680, 692],
  [680, 486],
  [1336, 486],
];
const CHIP = { x: 1280, y: 300, w: 200, h: 96 };
const CMD_T = 1.3; // the command's trip, core → DMA engine
const CMD_LEN = CMD.slice(1).reduce((s, [x, y], i) => s + Math.hypot(x - CMD[i][0], y - CMD[i][1]), 0);

/** b2: the payload lines flash as written, one fence drops on the command's way out; the command waits there, then passes. */
const FRESH = T[2] + 1.8;
const FENCE = { x: 600, y: 348, h: 64 };
const FENCE_DROP = T[2] + 2.5;
const PASS = T[2] + 4.6;
/** Fractions along CMD (its first, horizontal leg) where the b2 command waits in front of the fence and where it crosses it. */
const WAIT_K = (FENCE.x - 24 - CMD[0][0]) / CMD_LEN;
const FENCE_K = (FENCE.x - CMD[0][0]) / CMD_LEN;
/** DMA rounds: two in b1, one in b2 (its command crosses the fence at PASS). */
const DMA_CYCLES = [T[1] + 1.4, T[1] + 6.9, PASS - CMD_T * FENCE_K];

/** b3: the write-back on the NIC→host lane: the payload into host memory (the read request's path), then a 64 B completion. */
const CPL: Pt[] = [
  [1280, 376],
  [1250, 376],
  [1250, 514],
  [585, 514],
];
const SHARED: Pt[] = [...CPL.slice(0, 3), [665, 514]];
const WB0 = T[3] + 2.1; // the payload leaves the DMA engine
const WB_D = 1.3;
const LAND = WB0 + WB_D;
const CPL0 = T[3] + 3.85; // the completion leaves
const CPL_D = 1.45;
const CPL_LAND = CPL0 + CPL_D;
const POLL0 = T[3] + 9.0;
const THERE = T[3] + 11.1;
const BADGE_Y = 544; // NIC → host crossing badges sit below the lanes, as in C02

/** b4: message-size axis, log2 from 64 B to 4 KiB, split between 256 B and 512 B. */
const AX0 = 300;
const AX1 = 1620;
const AXY = 404;
const TICKS = (
  [
    ["64 B", 6],
    ["256 B", 8],
    ["512 B", 9],
    ["1 KiB", 10],
    ["4 KiB", 12],
  ] as [string, number][]
).map(([label, log2]) => ({ label, x: AX0 + ((log2 - 6) / 6) * (AX1 - AX0) }));
const SPLIT = AX0 + (2.5 / 6) * (AX1 - AX0);

/** b5–b6: small messages queue at the core's exit while bulk DMA holds every credit, then get their own window. */
const SQX = Array.from({ length: 6 }, (_, j) => 640 - 40 * j);
const Q_START = T[5] + 1.4;
const Q_PER = 0.8;
const DEP0 = T[6] + 0.8;
const DEP_PER = 0.35;

/** b7: host CPU cycles per packet, standard interface (fairly tuned) vs DirectPush. */
const CYC = [
  { size: "64 B", std: 731, dp: 474, ratio: "1.5×" },
  { size: "1 KiB", std: 956, dp: 462, ratio: "2.1×" },
  { size: "4 KiB", std: 1011, dp: 508, ratio: "2.0×" },
];
const CYC_X = 290;
const CYC_W = 1000;
const CYC_Y = [306, 456, 606];

function Body() {
  const t = useT();
  // ---------------------------------------------------------------- phases: diagram (b0–b3, b5–b6), size chart (b4), cycles chart (b7)
  const diagA = win(t, T[0] + 0.1, T[4] + 0.1, 0.8);
  const diagC = win(t, T[5] + 0.4, T[7] + 0.1, 0.8);
  const diag = Math.max(diagA, diagC);
  const sizeChart = win(t, T[4] + 0.6, T[5] + 0.1, 0.7);
  const cycChart = prog(t, T[7] + 0.6, 0.7);
  const phaseC = t > T[5];

  // ---------------------------------------------------------------- b0: the CPU pushes a 4 KiB payload as 64 line writes
  const busy = win(t, W_START - 0.2, W_START + 63 * W_PER + 0.4, 0.3);
  const coreBusy = busy * (0.6 + 0.4 * Math.abs(Math.sin(t * 10)));
  const writes = stream(t, W_START, W_START + 63.5 * W_PER, W_PER, 1.0);

  // ---------------------------------------------------------------- b1–b2: one command, then the NIC's DMA engine pulls the block
  const chipOn = prog(t, T[1] + 0.6, 0.6);
  const cyc = DMA_CYCLES.map((c0, n) => {
    const run = (t - c0) / CMD_T;
    // b2's command glides out of the core, waits at the fence, then leaves on the usual schedule
    const glide = t < PASS - 0.65 ? -1 : WAIT_K * prog(t, PASS - 0.65, 0.5);
    const fenced = n === 2;
    return {
      cmdK: fenced ? Math.max(run, glide) : run,
      cmdOp: fenced ? prog(t, PASS - 0.65, 0.15) : 1,
      reqK: prog(t, c0 + 1.4, 0.9),
      reqOp: win(t, c0 + 1.4, c0 + 4.8, 0.4),
      dataK: (t - (c0 + 2.4)) / 1.8,
      trail: win(t, c0 + 2.4, c0 + 4.8, 0.4),
    };
  });
  // the engine also glows while it writes back (b3); host memory glows amber when the DMA reads it and when the reply lands
  const chipGlow = Math.max(...DMA_CYCLES.map((c0) => win(t, c0 + 1.2, c0 + 2.6, 0.2)), win(t, WB0 - 0.3, CPL0 + 0.4, 0.25));
  const memRead = Math.max(...DMA_CYCLES.map((c0) => win(t, c0 + 2.2, c0 + 4.0, 0.3)), win(t, LAND - 0.2, LAND + 1.0, 0.3));

  // ---------------------------------------------------------------- b5–b6: one shared credit pool, then a window per class
  const split = prog(t, T[6] + 0.3, 1.0);
  const yS = lerp(500, 472, split);
  const yB = lerp(500, 528, split);
  const smallRoute: Pt[] = [
    [640, 348],
    [665, 348],
    [665, yS],
    [1336, yS],
  ];
  const bulkRoute: Pt[] = [
    [640, 692],
    [680, 692],
    [680, yB],
    [1336, yB],
  ];
  const bulk = phaseC ? stream(t, T[5] + 0.8, T[7] + 0.4, 1.1, 2.8) : [];
  const dep = (t - DEP0) / DEP_PER; // queued small messages released so far
  const small = SQX.map((x0, j) => {
    const arrive = Q_START + j * Q_PER;
    if (t < arrive) return null;
    if (dep < j) return { x: lerp(437, x0 + 40 * Math.max(0, dep), prog(t, arrive, 0.4)), k: -1 };
    return { x: 0, k: (t - (DEP0 + j * DEP_PER)) / 1.6 };
  });
  const flow = stream(t, DEP0 + 6 * DEP_PER + 0.2, T[7] + 0.4, 0.55, 1.9);
  const gh = lerp(48, 22, split);
  const tokens = Array.from({ length: 6 }, (_, i) => {
    const top = i < 3;
    const bulkHeld = t >= T[5] + 1.2 + i * 0.25 && (!top || split < 0.5);
    const smallHeld = top && split >= 0.5 && (Math.floor(t / 0.45) + i) % 3 < 2;
    return {
      x: lerp(712 + 22 * i, 712 + 22 * (i % 3), split),
      y: lerp(428, top ? 428 : 572, split),
      fill: smallHeld ? P.push : bulkHeld ? P.nic : null,
      ring: split < 0.5 ? P.sub : top ? P.push : P.nic,
    };
  });

  // ---------------------------------------------------------------- b4 bars
  const share: HBar[] = [
    { label: "pushed line by line", value: 46.6, valueText: "46.6%", color: P.push, k: prog(t, T[4] + 7.4, 1.0) },
    { label: "NIC DMA", value: 16.3, valueText: "16.3%", color: P.nic, k: prog(t, T[4] + 9.6, 0.9) },
  ];

  // ---------------------------------------------------------------- b2: payload written, one fence, the command passes it
  const writeArrow = win(t, FRESH - 0.5, FRESH + 1.4, 0.4);
  const cmdRoute = 1 - prog(t, T[3], 0.5);
  const fenceOp = prog(t, FENCE_DROP, 0.3) * (1 - prog(t, T[3], 0.5));
  const fenceDy = -36 * (1 - prog(t, FENCE_DROP, 0.45));
  const passGlow = win(t, PASS - 0.12, PASS + 0.3, 0.15);

  // ---------------------------------------------------------------- b3: payload first, then the completion, same lane; the core polls only the completion
  const wbK = (t - WB0) / WB_D;
  const cplK = (t - CPL0) / CPL_D;
  const awaitK = prog(t, T[3] + 0.4, 0.7); // the old payload dims while the reply is on its way
  const cplHit = win(t, CPL_LAND - 0.1, CPL_LAND + 0.6, 0.3); // the host cache flashes amber as the completion lands
  const pollOn = prog(t, POLL0, 0.4);
  const pollBeat = Math.abs(Math.sin((t - POLL0) * 3.0));

  return (
    <>
      <Stage>
        <g opacity={diag}>
          {/* busy core (coral); host memory / cache touched by the DMA engine (amber): own overlays, so kit part colours never snap */}
          <rect x={G.hostCore.x - 8} y={G.hostCore.y - 8} width={G.hostCore.w + 16} height={G.hostCore.h + 16} rx={20} fill={alpha(P.bad, 0.3)} filter="url(#ccSoft)" opacity={coreBusy} />
          <rect x={G.hostMem.x - 8} y={G.hostMem.y - 8} width={G.hostMem.w + 16} height={G.hostMem.h + 16} rx={20} fill={alpha(P.nic, 0.3)} filter="url(#ccSoft)" opacity={memRead} />
          <rect x={G.hostCache.x - 8} y={G.hostCache.y - 8} width={G.hostCache.w + 16} height={G.hostCache.h + 16} rx={20} fill={alpha(P.nic, 0.3)} filter="url(#ccSoft)" opacity={cplHit} />
          <HostNic
            g={G}
            labels={phaseC ? {} : { hostMem: "" }}
            dim={{ nicMem: 0.75 }}
            hl={{
              hostMem: phaseC ? 0 : 0.5 * prog(t, T[0] + 1.0, 0.6) * (1 - busy) * (1 - memRead),
              nicCache: phaseC ? 0 : 0.5 * win(t, W_START + 1.0, T[1] + 0.4, 0.5),
              hostCore: phaseC ? 0 : 0.45 * pollOn,
            }}
            hlColor={{ nicCache: P.push }}
          />
          <rect x={G.hostCore.x} y={G.hostCore.y} width={G.hostCore.w} height={G.hostCore.h} rx={14} fill={alpha(P.bad, 0.12)} stroke={P.bad} strokeWidth={2.5} opacity={coreBusy} />
          <rect x={G.hostMem.x} y={G.hostMem.y} width={G.hostMem.w} height={G.hostMem.h} rx={14} fill="none" stroke={P.nic} strokeWidth={2.5} opacity={memRead} />
          <rect x={G.hostCache.x} y={G.hostCache.y} width={G.hostCache.w} height={G.hostCache.h} rx={14} fill="none" stroke={P.nic} strokeWidth={2.5} opacity={cplHit} />
          {/* NIC's own DMA engine */}
          <g opacity={chipOn} fontFamily={FONT}>
            <rect x={CHIP.x - 6} y={CHIP.y - 6} width={CHIP.w + 12} height={CHIP.h + 12} rx={18} fill={alpha(P.nic, 0.25 * chipGlow)} filter="url(#ccSoft)" />
            <rect x={CHIP.x} y={CHIP.y} width={CHIP.w} height={CHIP.h} rx={14} fill={alpha(P.nic, 0.08 + 0.12 * chipGlow)} stroke={alpha(P.nic, 0.6 + 0.4 * chipGlow)} strokeWidth={1.5 + chipGlow} />
            <text x={CHIP.x + CHIP.w / 2} y={CHIP.y + 34} textAnchor="middle" dominantBaseline="central" fontSize={20} fill={P.nic}>
              NIC's own
            </text>
            <text x={CHIP.x + CHIP.w / 2} y={CHIP.y + 64} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={600} fill={P.ink}>
              DMA engine
            </text>
          </g>
          {/* ---- phase A: the 4 KiB payload */}
          <g opacity={diagA}>
            {GRID.map(([x, y], i) => {
              const col = i % 16;
              const row = Math.floor(i / 16);
              const shown = prog(t, T[0] + 1.0 + row * 0.15, 0.4);
              const sent = prog(t, W_START + i * W_PER, 0.12) * (1 - prog(t, T[1] + 0.4, 0.6));
              const d = 0.03 * col + 0.07 * row; // b2: freshly written, as a quick wave
              const fresh = win(t, FRESH + d, FRESH + d + 0.2, 0.18);
              const filled = prog(t, LAND - 0.1 + 0.03 * (15 - col) + 0.05 * row, 0.25); // b3: the reply fills in from the right
              return (
                <g key={`g${i}`}>
                  <rect x={x - 9} y={y - 6} width={18} height={12} rx={3} fill={alpha(P.host, 0.75)} opacity={shown * (1 - 0.85 * sent) * (1 - 0.7 * awaitK) * (1 - filled)} />
                  {fresh > 0 && <rect x={x - 9} y={y - 6} width={18} height={12} rx={3} fill={P.ink} opacity={0.9 * fresh} />}
                  {filled > 0 && <rect x={x - 9} y={y - 6} width={18} height={12} rx={3} fill={alpha(P.nic, 0.85)} opacity={filled} />}
                </g>
              );
            })}
            <Flow pts={[...RT.hostToMem].reverse()} k={prog(t, W_START - 0.4, 0.6)} color={alpha(P.host, 0.7)} w={2} head={10} dash="6 6" opacity={busy} />
            <Flow pts={RT.hostToMem} k={prog(t, FRESH - 0.5, 0.6)} color={alpha(P.host, 0.8)} w={2.5} head={10} opacity={writeArrow} />
            {writes.map(({ i, k }) => (
              <Cell key={`w${i}`} pts={RT.pushH2N} k={k} w={28} h={18} color={P.push} opacity={landOp(k)} />
            ))}
            {/* b2: the command's route, and the one fence on it */}
            <Flow pts={CMD} k={prog(t, FRESH + 0.2, 0.8)} color={alpha(P.push, 0.35)} w={2} head={10} dash="5 7" opacity={cmdRoute} />
            <g opacity={fenceOp} transform={`translate(0 ${fenceDy})`}>
              <rect x={FENCE.x - 16} y={FENCE.y - FENCE.h / 2 - 10} width={32} height={FENCE.h + 20} rx={16} fill={alpha(P.gold, 0.2 + 0.35 * passGlow)} filter="url(#ccSoft)" />
              <rect x={FENCE.x - 5} y={FENCE.y - FENCE.h / 2} width={10} height={FENCE.h} rx={5} fill={P.gold} />
            </g>
            {cyc.map((c, n) => (
              <g key={`d${n}`}>
                <Cell pts={CMD} k={c.cmdK} color={P.push} opacity={c.cmdOp * landOp(c.cmdK, 0.05)} />
                <Flow pts={DREQ} k={c.reqK} color={P.nic} w={2.5} head={11} dash="8 7" opacity={c.reqOp} />
                <Flow pts={DATA} k={Math.max(0, Math.min(1, c.dataK))} color={alpha(P.nic, 0.6)} w={3} head={0} opacity={c.trail} />
                <Block pts={DATA} k={c.dataK} w={84} h={34} color={P.nic} label="4 KiB" opacity={landOp(c.dataK, 0.08)} />
              </g>
            ))}
            {/* b3: the write-back on the NIC→host lane: payload first, then the completion; the core polls only the completion */}
            <Flow pts={SHARED} k={1} color={alpha(P.nic, 0.2)} w={16} head={0} opacity={prog(t, T[3] + 5.9, 0.6)} />
            <Flow pts={DREQ} k={clamp01(wbK)} color={alpha(P.nic, 0.6)} w={3} head={11} opacity={prog(t, WB0, 0.2)} />
            <Flow pts={CPL} k={clamp01(cplK)} color={alpha(P.nic, 0.6)} w={3} head={0} opacity={prog(t, CPL0, 0.2)} />
            <Block pts={DREQ} k={wbK} w={84} h={34} color={P.nic} label="4 KiB" opacity={prog(t, WB0, 0.15) * landOp(wbK, 0.08)} />
            <rect x={CPL[3][0] - 28} y={CPL[3][1] - 22} width={56} height={44} rx={12} fill={alpha(P.nic, 0.6)} filter="url(#ccSoft)" opacity={pollOn * pollBeat} />
            <Cell pts={CPL} k={cplK < 0 ? -1 : Math.min(1, cplK)} color={P.nic} opacity={prog(t, CPL0, 0.15)} />
            <StepBadge x={1060} y={BADGE_Y} n={1} color={P.nic} k={prog(t, WB0 + 0.5, 0.35)} />
            <StepBadge x={1130} y={BADGE_Y} n={2} color={P.nic} k={prog(t, CPL0 + 0.5, 0.35)} />
            <Flow pts={RT.hostPoll} k={1} color={P.host} w={3} head={10} opacity={pollOn * (0.35 + 0.65 * pollBeat)} />
          </g>
          {/* ---- phase C: credit gate(s), tokens, bulk blocks and small messages */}
          <g opacity={diagC}>
            {[yS, yB].map((y, i) => (
              <rect
                key={`gate${i}`}
                x={700}
                y={y - gh}
                width={8}
                height={2 * gh}
                rx={4}
                fill={alpha(split < 0.5 ? P.sub : i === 0 ? P.push : P.nic, 0.25)}
                stroke={split < 0.5 ? P.sub : i === 0 ? P.push : P.nic}
                strokeWidth={2}
              />
            ))}
            <Tokens tokens={tokens} r={8} opacity={prog(t, T[5] + 0.8, 0.5)} />
            {bulk.map(({ i, k }) => (
              <Block key={`b${i}`} pts={bulkRoute} k={k} w={66} h={28} color={P.nic} opacity={landOp(k, 0.06)} />
            ))}
            {small.map((s, j) =>
              s === null ? null : s.k < 0 ? (
                <Cell key={`s${j}`} pts={[[s.x, 348], [s.x + 0.01, 348]]} k={0} color={P.push} />
              ) : (
                <Cell key={`s${j}`} pts={smallRoute} k={s.k} color={P.push} opacity={landOp(s.k)} />
              ),
            )}
            {flow.map(({ i, k }) => (
              <Cell
                key={`f${i}`}
                pts={[
                  [420, 348],
                  [665, 348],
                  [665, yS],
                  [1336, yS],
                ]}
                k={k}
                color={P.push}
                opacity={landOp(k)}
              />
            ))}
          </g>
        </g>

        {/* ---- b4: which road, by size */}
        <g opacity={sizeChart} fontFamily={FONT}>
          <rect x={260} y={296} width={(SPLIT - 260) * prog(t, T[4] + 1.0, 0.8)} height={84} rx={16} fill={alpha(P.push, 0.14)} stroke={alpha(P.push, 0.5)} strokeWidth={1.5} />
          <rect x={SPLIT} y={296} width={(1660 - SPLIT) * prog(t, T[4] + 3.6, 0.8)} height={84} rx={16} fill={alpha(P.nic, 0.14)} stroke={alpha(P.nic, 0.5)} strokeWidth={1.5} />
          <line x1={SPLIT} y1={280} x2={SPLIT} y2={AXY + 14} stroke={P.ink} strokeWidth={2} strokeDasharray="6 6" opacity={0.6 * prog(t, T[4] + 2.6, 0.5)} />
          <line x1={260} y1={AXY} x2={1660} y2={AXY} stroke={P.hair2} strokeWidth={2} />
          {TICKS.map((tk, i) => (
            <g key={tk.label} opacity={prog(t, T[4] + 0.8 + i * 0.12, 0.4)}>
              <line x1={tk.x} y1={AXY - 8} x2={tk.x} y2={AXY + 8} stroke={P.sub} strokeWidth={2} />
              <text x={tk.x} y={AXY + 34} textAnchor="middle" dominantBaseline="central" fontSize={22} fill={P.sub} style={TAB}>
                {tk.label}
              </text>
            </g>
          ))}
          <HBars x={110} y={584} w={1700} rows={share} max={50} labelW={470} barH={40} gap={46} size={26} opacity={prog(t, T[4] + 6.4, 0.6)} />
        </g>

        {/* ---- b7: host CPU cycles per packet */}
        <g opacity={cycChart} fontFamily={FONT}>
          {CYC.map((c, g) => {
            const kStd = prog(t, T[7] + 1.0 + 1.3 * g, 0.8);
            const kDp = prog(t, T[7] + 1.4 + 1.3 * g, 0.8);
            const y = CYC_Y[g];
            const wStd = (CYC_W * c.std * kStd) / 1011;
            const wDp = (CYC_W * c.dp * kDp) / 1011;
            return (
              <g key={c.size}>
                <rect x={CYC_X} y={y} width={wStd} height={34} rx={17} fill={GREY} opacity={0.92} />
                <rect x={CYC_X} y={y + 44} width={wDp} height={34} rx={17} fill={P.push} opacity={0.92} />
                <text x={CYC_X + wStd + 16} y={y + 18} dominantBaseline="central" fontSize={24} fontWeight={700} fill={GREY} opacity={Math.max(0, (kStd - 0.6) / 0.4)} style={TAB}>
                  {c.std}
                </text>
                <text x={CYC_X + wDp + 16} y={y + 62} dominantBaseline="central" fontSize={24} fontWeight={700} fill={P.push} opacity={Math.max(0, (kDp - 0.6) / 0.4)} style={TAB}>
                  {c.dp}
                </text>
              </g>
            );
          })}
        </g>
      </Stage>

      {/* ---- phase A overlays */}
      <Text x={G.hostMem.x} y={742} w={G.hostMem.w} align="center" size={22} color={P.host} opacity={diagA * prog(t, T[0] + 1.6, 0.6) * (1 - prog(t, THERE - 0.5, 0.4))}>
        4 KiB payload
      </Text>
      <Tag x={436} y={300} color={P.bad} opacity={diagA * busy}>
        CPU busy
      </Tag>
      <Text x={660} y={236} w={600} align="center" size={34} color={P.ink} opacity={diagA * win(t, T[0] + 3.4, T[1] + 0.3, 0.6)}>
        <span style={{ fontSize: 76, fontWeight: 800, color: P.bad, ...TAB }}>64</span> line writes
      </Text>
      <Text x={710} y={534} w={500} align="center" size={22} color={P.push} opacity={diagA * win(t, T[1] + 1.4, T[1] + 3.6, 0.5)}>
        one 64-byte command
      </Text>
      <TagC x={1500} y={186} color={P.nic} opacity={diagA * win(t, T[1] + 8.0, T[2] + 0.2, 0.6)}>
        no extra accelerator
      </TagC>

      {/* ---- b2–b3 overlays */}
      <TagC x={FENCE.x} y={272} color={P.gold} opacity={diagA * prog(t, FENCE_DROP + 0.4, 0.4) * (1 - prog(t, T[3], 0.5))}>
        1 fence
      </TagC>
      <Text x={360} y={814} w={1200} align="center" size={24} color={P.sub} opacity={diagA * win(t, T[2] + 8.4, T[3], 0.5)}>
        one fence per message · not per line
      </Text>
      <Text x={700} y={530} w={340} align="center" size={22} color={P.nic} opacity={diagA * prog(t, T[3] + 5.9, 0.6)}>
        same path · in order
      </Text>
      <Text x={G.hostMem.x} y={742} w={G.hostMem.w} align="center" size={22} weight={600} color={P.push} opacity={diagA * prog(t, THERE, 0.6)}>
        ✓ payload already there
      </Text>

      {/* ---- b4 overlays */}
      <Text x={110} y={210} size={17} color={P.sub} opacity={sizeChart} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        MESSAGE SIZE DECIDES THE ROAD
      </Text>
      <Text x={260} y={318} w={SPLIT - 260} align="center" size={28} weight={700} color={P.push} opacity={sizeChart * prog(t, T[4] + 1.5, 0.6)}>
        pushed inline
      </Text>
      <Text x={SPLIT} y={318} w={1660 - SPLIT} align="center" size={28} weight={700} color={P.nic} opacity={sizeChart * prog(t, T[4] + 4.1, 0.6)}>
        NIC DMA
      </Text>
      <Text x={110} y={528} size={17} color={P.sub} opacity={sizeChart * prog(t, T[4] + 6.4, 0.6)} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        HOST CPU SHARE AT 4 KiB
      </Text>

      {/* ---- phase C overlays */}
      <Text x={706} y={378} size={22} color={P.ink} opacity={diagC * win(t, T[5] + 1.2, T[6] + 0.2, 0.5)}>
        one shared credit pool
      </Text>
      <Text x={706} y={378} size={22} color={P.ink} opacity={diagC * prog(t, T[6] + 0.7, 0.5)}>
        separate credit windows
      </Text>
      <Text x={784} y={413} size={20} weight={600} color={P.push} opacity={diagC * prog(t, T[6] + 1.2, 0.5)}>
        small
      </Text>
      <Text x={784} y={557} size={20} weight={600} color={P.nic} opacity={diagC * prog(t, T[6] + 1.2, 0.5)}>
        bulk
      </Text>
      <BigStat
        x={960}
        y={200}
        w={520}
        align="center"
        value="40.5"
        unit="ms"
        size={84}
        color={P.bad}
        label="small-message p99"
        opacity={diagC * win(t, T[5] + 6.6, T[6] + 0.2, 0.7)}
        dy={(1 - prog(t, T[5] + 6.6, 0.7)) * 14}
      />
      <BigStat
        x={960}
        y={200}
        w={520}
        align="center"
        value="2.75"
        unit="µs"
        size={84}
        color={P.push}
        label="small-message p99"
        opacity={diagC * prog(t, T[6] + 3.6, 0.7)}
        dy={(1 - prog(t, T[6] + 3.6, 0.7)) * 14}
      />
      <Tag x={96} y={160} opacity={diag}>
        schematic
      </Tag>

      {/* ---- b7 overlays */}
      <Text x={110} y={186} size={17} color={P.sub} opacity={cycChart} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        HOST CPU CYCLES PER PACKET · LOWER IS BETTER
      </Text>
      <div style={{ position: "absolute", left: 110, top: 228, display: "flex", gap: 40, fontFamily: FONT, fontSize: 22, color: P.sub, opacity: cycChart }}>
        <span>
          <span style={{ display: "inline-block", width: 26, height: 14, borderRadius: 7, background: GREY, marginRight: 10 }} />
          standard interface, fairly tuned
        </span>
        <span>
          <span style={{ display: "inline-block", width: 26, height: 14, borderRadius: 7, background: P.push, marginRight: 10 }} />
          DirectPush
        </span>
      </div>
      {CYC.map((c, g) => (
        <React.Fragment key={c.size}>
          <Text x={60} y={CYC_Y[g] + 20} w={200} align="right" size={30} weight={700} color={P.ink} opacity={cycChart * prog(t, T[7] + 1.0 + 1.3 * g, 0.5)} style={TAB}>
            {c.size}
          </Text>
          <Text x={1460} y={CYC_Y[g] + 6} size={56} weight={800} color={P.push} opacity={cycChart * prog(t, T[7] + 2.3 + 1.3 * g, 0.5)} style={TAB}>
            {c.ratio}
          </Text>
        </React.Fragment>
      ))}
      <Text x={1460} y={CYC_Y[2] + 96} size={22} color={P.sub} opacity={cycChart * prog(t, T[7] + 5.0, 0.6)}>
        fewer cycles
      </Text>
      <Text x={110} y={790} w={1700} size={22} color={P.sub} opacity={cycChart * prog(t, T[7] + 8.6, 0.7)}>
        large packets: much of the saving is software overhead; 64 B: the interface itself
      </Text>
    </>
  );
}

export const C08: SceneDef = {
  id: "C08",
  title: "Big messages: let the NIC move them",
  stage: 3,
  source: "Measured: 1 host core + 1 NIC core \u00b7 64 B \u2013 4 KiB messages",
  dur: DUR,
  cues,
  C: Body,
};
