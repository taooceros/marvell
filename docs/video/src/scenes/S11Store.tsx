import React from "react";
import {
  Arrow,
  Axes,
  C,
  CORE,
  Cue,
  FONT,
  HLine,
  K,
  Link,
  Packet,
  Pill,
  Pt,
  SceneDef,
  Stage,
  Stat,
  Txt,
  along,
  appear,
  clamp01,
  countUp,
  fade,
  lerp,
  lin,
  linScale,
  prog,
  useT,
} from "../lib";
import { Datapath, InfTag, dpGeom, ringCell, routes } from "./pcie";
import { CellSeg, CodeBox, Glow, Piece, RangeBar, hex } from "./_ScenePull";

// <cues>
const cues: Cue[] = [
  [0.60, 14.74, "Each card core now writes its 64 B replies straight into the host ring. W=1 p50: **0.94–0.98 µs** (some runs **1.23–1.27**).", "Now each card core writes its sixty-four byte replies straight into the host's ring. The median round trip is zero point nine four to zero point nine eight microseconds, with some runs at one point two three to one point two seven."], // 0
  [15.09, 30.69, "The first **push** under 1 µs: DPI DMA took **1.33–1.60 µs**. On the card, the stores plus a completion barrier cost **21 ns** per reply, vs **675 ns** for DPI.", "That makes it the first push path under one microsecond; the D P I took one point three three to one point six. On the card, the stores plus a completion barrier cost twenty-one nanoseconds per reply, against six hundred seventy-five for the D P I."], // 1
  [31.04, 42.90, "Trap 1: one 64-byte `ST1` store is split in flight; head and tail land **before the middle**, and the host read a stale payload.", "But two traps showed up as real bad data. First: a single sixty-four byte store instruction gets split on the way, and the head and tail can land before the middle. The host then reads a stale payload."], // 2
  [43.25, 53.77, "Trap 2: PEM write merging (`wnmerge=0`) reorders the pieces too. Fix: four 16 B `STP` stores, **head last**, merging off.", "Second: letting the PEM merge writes reorders the pieces too. The fix: four separate stores of sixteen bytes each, writing the head last, with merging turned off."], // 3
  [54.12, 64.12, "Verified: **>40 M** replies with lost / bad / torn **0**; 100 corrupted on purpose, **100** caught by the host.", "With that, over forty million replies came back with zero lost, bad or torn. And of a hundred replies corrupted on purpose, the host caught all hundred."], // 4
  [64.47, 76.35, "Rate: 1 card core **13.9–17.7 M msg/s**, 2–4 cores **13–17.6**. Every host poll finds a reply: the **host** is the limit.", "Throughput is thirteen point nine to seventeen point seven million messages per second with one card core, and no better with two to four. Every host poll finds a reply waiting, so the host is the limit."], // 5
  [76.70, 89.70, "**[INF]** Likely cause: four partial writes per reply make the host core and PCIe fight over one cache line. Next: one full-line write per reply.", "Our guess, not yet verified: each reply arrives as four partial writes to one cache line, so the polling core and the link keep fighting over that line. The next step is a single full line write per reply."], // 6
];
const DUR = 91.10; // <dur>
// </cues>

// ---------------------------------------------------------------- zoom geometry (bottom band, c2–c3)
const LANE = [640, 680, 720, 760]; // in-flight lanes per piece
const SEG_W = 150;
const CELL_X = 240;
const CELL_Y = 660;
const CELL_H = 80;
const segCx = (j: number) => CELL_X + SEG_W * (j + 0.5);
const NAMES = ["head", "payload", "payload", "tail"];
const PEM_X = 1240;
const CORE_X = 1600; // card core box left edge
const RANK = [1, 3, 2, 0]; // trap 2: PEM releases tail, head, payload 2, payload 1
const ORDER = [1, 2, 3, 0]; // fix: store order (segment index), head last

// c6 cache-line zoom
const LINE_X = 560;
const Q_W = 170;

/** Last legs of a piece: up the right edge, along above the cell, then straight down into segment j (never over other segments or text). */
const landing = (j: number): Pt[] => [[880, LANE[j]], [880, 624], [segCx(j), 624], [segCx(j), 700]];

type Leg = { pts: Pt[]; a: number; d: number };
/** Position along timed legs (holds at the end of a leg until the next starts); null before the first leg. */
function legsAt(t: number, legs: Leg[]): Pt | null {
  let p: Pt | null = null;
  for (const l of legs) {
    if (t < l.a) break;
    p = along(l.pts, clamp01((t - l.a) / l.d));
  }
  return p;
}

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);
  const g = dpGeom(0.62, 18, 36);
  const r = routes(g);
  const s = g.s;

  // cue content opacities (crossfade at boundaries)
  const o0 = fade(t, T[0] + 0.3, T[1]);
  const o1 = fade(t, T[1] + 0.5, T[2]);
  const oZ = fade(t, T[2] + 0.5, T[4]);
  const o4 = fade(t, T[4] + 0.5, T[5]);
  const o5 = fade(t, T[5] + 0.5, T[6]);
  const o6 = fade(t, T[6] + 0.5, 1e9);
  const focus = Math.max(oZ, o6);

  // ---- ambient round trip: request in, card core stores the reply, host polls its ring
  const L0 = T[0] + 0.5;
  const PER = 2.6;
  const lt = t - L0;
  const n = Math.max(0, Math.floor(lt / PER));
  const u = lt < 0 ? -1 : lt - n * PER;
  const seg = (a: number, d: number) => (u < a ? 0 : lin(u, a, d));
  const gone = (a: number) => 1 - lin(u, a, 0.3);
  const loopOp = prog(t, L0, 0.5) * lerp(1, 0.4, focus);
  const reqA = lt < 0 ? 0 : lin(u, 0.6, 0.2) * (1 - lin(u, 2.3, 0.3));
  const repA = lt < 0 ? 0 : lin(u, 1.7, 0.2) * (1 - lin(u, 2.3, 0.3));
  const zoomCell = 3;

  // ---- c2/c3 zoom: trap 1 (one ST1 splits), trap 2 (merging reorders), fix (4 × STP, head last)
  const split = T[2] + 4.5;
  const land1 = [7.5, 10.8, 10.8, 7.5].map((x) => T[2] + x);
  const blockCx = lerp(1600, PEM_X, prog(t, T[2] + 3.0, 1.5));
  const land2 = (j: number) => T[3] + 3.0 + 0.3 * RANK[j];
  const landF = (j: number) => T[3] + 5.2 + 0.9 * ORDER.indexOf(j) + 1.35;
  const reset1 = 1 - prog(t, T[3], 0.5);
  const fresh = [0, 1, 2, 3].map((j) =>
    Math.max(prog(t, land1[j], 0.3) * reset1, prog(t, land2(j), 0.3) * (1 - prog(t, T[3] + 4.6, 0.5)), prog(t, landF(j), 0.3)),
  );
  const badMid = fade(t, T[2] + 9.6, T[3]) + fade(t, T[3] + 3.45, T[3] + 4.6);
  const readK = fade(t, T[2] + 8.4, T[2] + 9.4, 0.25) + fade(t, T[3] + 3.3, T[3] + 4.0, 0.25) + fade(t, T[3] + 9.2, T[3] + 10.0, 0.25);

  // ---- c6: one host cache line, four partial writes, host core polling the same line
  const v6t = t - T[6] - 2.0;
  const c6n = v6t < 0 ? -1 : Math.floor(v6t / 2.0);
  const v6 = v6t - c6n * 2.0;
  const qj = c6n < 0 ? -1 : c6n % 4;
  const quarterA = (i: number) => {
    if (qj < 0) return 0;
    const prev = qj === 0 && c6n > 0 ? 1 - lin(v6, 0, 0.3) : 0;
    const cur = i < qj ? 1 : i === qj ? lin(v6, 0.6, 0.15) : 0;
    return Math.max(prev, cur);
  };
  const ownPcie = qj < 0 ? 0 : lin(v6, 0.6, 0.15) * (1 - lin(v6, 1.2, 0.15));
  const pollK = qj < 0 ? 0 : lin(v6, 1.1, 0.15) * (1 - lin(v6, 1.6, 0.2));

  // card core squares (same geometry as Datapath's) for the 2–4 core beat
  const cr = g.r.cores;
  const sz = 46 * s;
  const gap = 12 * s;
  const sqX = (i: number) => cr.x + cr.w - 20 * s - 4 * sz - 3 * gap + i * (sz + gap);
  const sqY = cr.y + (cr.h - sz) / 2;

  const cell3 = ringCell(g, "replyRing", zoomCell);
  const halfCell = ((510 - 20) / 10 / 2) * s;

  // c1 chart
  const sy1 = linScale(0, 2, 800, 250);
  // c5 chart
  const sy5 = linScale(0, 20, 800, 250);

  return (
    <>
      <Stage>
        <Datapath
          g={g}
          show={{ sdp: 0.35, sdpRing: 0.35, ctrl: 0.35, dpi: lerp(0.35, 1, o1) }}
          cellFill={{
            reqRing: (i) => (i === n % 10 && reqA * loopOp > 0.01 ? `${K.req}${hex(reqA * loopOp)}` : undefined),
            replyRing: (i) => {
              const a = Math.max(i === n % 10 ? repA * loopOp : 0, i === zoomCell ? focus * 0.8 : 0);
              return a > 0.01 ? `${K.store}${hex(a)}` : undefined;
            },
          }}
          cores={4}
          activeCores={1}
          linkGlow={0.4}
        />
        {/* c0: the new path */}
        <Glow r={g.r.pem} color={K.store} k={fade(t, T[0] + 2.5, T[1]) * 0.8} pad={4} />
        <Glow r={g.r.replyRing} color={K.store} k={fade(t, T[0] + 1.0, T[1]) * 0.7} pad={4} />
        {/* c1: DPI comparison */}
        <Glow r={g.r.dpi} color={K.dma} k={fade(t, T[1] + 4.2, T[2])} pad={4} />
        <Link pts={r.dmaReply} color={K.dma} w={4} dash="10 8" opacity={fade(t, T[1] + 4.2, T[2]) * 0.7} />
        {/* c5: 2–4 cores, host is the limit */}
        {[1, 2, 3].map((i) => (
          <g key={i} opacity={fade(t, T[5] + 5.0, T[6])}>
            <rect x={sqX(i)} y={sqY} width={sz} height={sz} rx={8 * s} fill={`${CORE[i]}33`} stroke={CORE[i]} strokeWidth={3} />
            <text x={sqX(i) + sz / 2} y={sqY + sz / 2} textAnchor="middle" dominantBaseline="central" fontSize={g.fs(18)} fontFamily="'JetBrains Mono', monospace" fill={CORE[i]}>
              {i}
            </text>
          </g>
        ))}
        <Glow r={g.r.core} color={K.host} k={fade(t, T[5] + 9.6, T[6])} pad={4} />
        {/* c6: line ping-pong mirrored on the drawing */}
        <Glow r={g.r.replyRing} color={K.store} k={o6 * ownPcie * 0.9} pad={4} />
        <Glow r={g.r.core} color={K.mem} k={o6 * (qj < 0 ? 0 : 1 - ownPcie) * 0.6} pad={4} />

        {/* ambient loop */}
        {lt >= 0 && (
          <g opacity={loopOp}>
            <g opacity={gone(0.7)}>
              <Packet pts={r.inline} k={seg(0, 0.7)} color={K.req} r={9} />
            </g>
            <g opacity={gone(1.8)}>
              <Packet pts={r.storeReply} k={seg(0.9, 0.9)} color={K.store} r={9} />
            </g>
            <g opacity={gone(2.3)}>
              <Packet pts={r.hostPollReply} k={seg(1.9, 0.4)} color={K.mem} r={7} />
            </g>
          </g>
        )}

        {/* ---------------- c0: reply path strip (bottom-left) */}
        <g opacity={o0}>
          {(
            [
              [70, "Arm core i", "stores to region 255 − i", K.card],
              [450, "PEM outbound region", "→ PCIe write as host VF0", K.card],
              [830, "Host reply ring", "64 B cells · host IOMMU", K.host],
            ] as [number, string, string, string][]
          ).map(([x, a, b, col], i) => (
            <g key={i} opacity={prog(t, T[0] + 1.2 + i * 0.7, 0.5)}>
              <rect x={x} y={640} width={340} height={110} rx={14} fill={C.panel} stroke={col} strokeWidth={2.5} />
              <text x={x + 20} y={680} fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
                {a}
              </text>
              <text x={x + 20} y={722} fontSize={22} fill={C.muted} fontFamily={FONT}>
                {b}
              </text>
            </g>
          ))}
          <Arrow x1={412} y1={695} x2={448} y2={695} color={K.store} w={4} head={12} k={prog(t, T[0] + 1.9, 0.4)} />
          <Arrow x1={792} y1={695} x2={828} y2={695} color={K.store} w={4} head={12} k={prog(t, T[0] + 2.6, 0.4)} />
          <line x1={240} y1={785} x2={1000} y2={785} stroke={K.store} strokeWidth={3} strokeDasharray="8 8" opacity={0.4 * prog(t, T[0] + 3.0, 0.5)} />
          {lt >= 0 && (
            <g opacity={gone(1.8) * prog(t, T[0] + 3.0, 0.5)}>
              <Packet pts={[[240, 785], [1000, 785]]} k={seg(0.9, 0.9)} color={K.store} r={10} />
            </g>
          )}
        </g>

        {/* ---------------- c1: W=1 push paths (right) + card cost (bottom-left) */}
        <g opacity={o1}>
          <Axes x0={1320} x1={1830} y0={800} y1={250} sx={(v) => v} sy={sy1} size={24} xticks={[]} yticks={[0, 0.5, 1, 1.5, 2].map((v) => [v, v.toFixed(1)] as [number, string])} ylabel="W=1 p50 round trip (µs)" />
          <RangeBar cx={1450} w={150} y0={800} sy={sy1} lo={1.33} hi={1.6} color={K.dma} k={prog(t, T[1] + 4.2, 0.8)} label="1.33–1.60" />
          <RangeBar cx={1700} w={150} y0={800} sy={sy1} lo={0.94} hi={0.98} color={K.store} k={prog(t, T[1] + 0.6, 0.8)} label="0.94–0.98" />
          <HLine y={sy1(1)} x0={1320} x1={1790} color={C.text} label="1 µs" size={24} opacity={prog(t, T[1] + 1.4, 0.5)} />
          <text x={1450} y={840} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.dma} fontFamily={FONT} opacity={prog(t, T[1] + 4.2, 0.5)}>
            DPI DMA
          </text>
          <text x={1700} y={840} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.store} fontFamily={FONT} opacity={prog(t, T[1] + 0.6, 0.5)}>
            card stores
          </text>
          {(
            [
              [640, 21, K.store, "21 ns", T[1] + 8.4],
              [720, 675, K.dma, "675 ns", T[1] + 12.0],
            ] as [number, number, string, string, number][]
          ).map(([y, ns, col, lab, a], i) => {
            const k = prog(t, a, 1.0);
            const w = ns * 0.95 * k;
            return (
              <g key={i} opacity={prog(t, a - 0.3, 0.4)}>
                <rect x={390} y={y} width={Math.max(w, 3)} height={48} rx={6} fill={`${col}66`} stroke={col} strokeWidth={2.5} />
                <text x={390 + w + 14} y={y + 24} dominantBaseline="central" fontSize={32} fontWeight={800} fill={col} fontFamily={FONT} opacity={clamp01((k - 0.5) * 2)}>
                  {lab}
                </text>
              </g>
            );
          })}
          <g opacity={prog(t, T[1] + 8.0, 0.5)}>
            <line x1={390} y1={786} x2={390 + 700 * 0.95} y2={786} stroke={C.dim} strokeWidth={2.5} />
            {[0, 200, 400, 600].map((v) => (
              <g key={v}>
                <line x1={390 + v * 0.95} y1={786} x2={390 + v * 0.95} y2={794} stroke={C.dim} strokeWidth={2} />
                <text x={390 + v * 0.95} y={818} textAnchor="middle" fontSize={24} fill={C.muted} fontFamily={FONT}>
                  {v}
                </text>
              </g>
            ))}
            <text x={390 + 700 * 0.95 + 14} y={818} fontSize={24} fill={C.muted} fontFamily={FONT}>
              ns
            </text>
          </g>
        </g>

        {/* ---------------- c2/c3: zoom on one reply cell */}
        <g opacity={oZ}>
          <line x1={cell3[0] - halfCell} y1={cell3[1] + 11} x2={CELL_X} y2={CELL_Y} stroke={K.store} strokeWidth={2} strokeDasharray="6 6" opacity={0.6} />
          <line x1={cell3[0] + halfCell} y1={cell3[1] + 11} x2={CELL_X + 4 * SEG_W} y2={CELL_Y} stroke={K.store} strokeWidth={2} strokeDasharray="6 6" opacity={0.6} />
          {/* host core */}
          <rect x={60} y={CELL_Y} width={140} height={CELL_H} rx={12} fill={C.panel} stroke={K.host} strokeWidth={2.5} />
          <text x={130} y={CELL_Y + 28} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={800} fill={K.host} fontFamily={FONT}>
            host
          </text>
          <text x={130} y={CELL_Y + 56} textAnchor="middle" dominantBaseline="central" fontSize={22} fill={C.muted} fontFamily={FONT}>
            polls
          </text>
          <Arrow x1={202} y1={CELL_Y + CELL_H / 2} x2={236} y2={CELL_Y + CELL_H / 2} color={K.mem} w={4} head={12} opacity={0.35 + 0.65 * clamp01(readK)} />
          {/* the cell */}
          {[0, 1, 2, 3].map((j) => {
            const q = ORDER.indexOf(j);
            return (
              <CellSeg
                key={j}
                x={CELL_X + j * SEG_W}
                y={CELL_Y}
                w={SEG_W}
                h={CELL_H}
                name={NAMES[j]}
                fresh={fresh[j]}
                bad={j === 1 || j === 2 ? clamp01(badMid) : 0}
                badge={[String(q + 1), prog(t, landF(j), 0.3)]}
              />
            );
          })}
          {/* PEM + card core */}
          <rect x={PEM_X - 60} y={620} width={120} height={160} rx={12} fill={C.panel} stroke={K.card} strokeWidth={2.5} />
          <text x={PEM_X} y={606} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.card} fontFamily={FONT}>
            PEM
          </text>
          <rect x={CORE_X} y={650} width={240} height={100} rx={12} fill={C.panel} stroke={K.card} strokeWidth={2.5} />
          <text x={CORE_X + 120} y={688} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
            Arm core
          </text>
          <text x={CORE_X + 120} y={722} textAnchor="middle" dominantBaseline="central" fontSize={22} fill={C.muted} fontFamily={FONT}>
            stores the reply
          </text>

          {/* trap 1: one 64 B block, split at the PEM, middle late */}
          <g opacity={fade(t, T[2] + 0.5, T[3])}>
            {[0, 1, 2, 3].map((j) => {
              if (t < T[2] + 3.0) return null;
              const bx = (cx: number) => cx + (j - 1.5) * 96;
              if (t < split) return <Piece key={j} p={[bx(blockCx), 700]} label={NAMES[j]} op={prog(t, T[2] + 3.0, 0.3)} />;
              const pts: Pt[] = [[bx(PEM_X), 700], [bx(PEM_X) - 60, LANE[j]], ...landing(j)];
              return <Piece key={j} p={along(pts, lin(t, split, land1[j] - split))} label={NAMES[j]} op={1 - lin(t, land1[j], 0.3)} />;
            })}
          </g>
          {/* trap 2: four ascending stores, merged and reordered at the PEM */}
          <g opacity={fade(t, T[3] + 0.3, T[3] + 4.6)}>
            {[0, 1, 2, 3].map((j) => {
              const a = T[3] + 0.8 + 0.25 * j;
              const e = land2(j) - 1.0;
              const p = legsAt(t, [
                { pts: [[CORE_X, 700], [PEM_X, LANE[j]]], a, d: 0.5 },
                { pts: [[PEM_X, LANE[j]], ...landing(j)], a: e, d: 1.0 },
              ]);
              return p && <Piece key={j} p={p} label={NAMES[j]} op={prog(t, a, 0.2) * (1 - lin(t, land2(j), 0.3))} />;
            })}
          </g>
          {/* fix: 4 × 16 B STP, head last, merging off */}
          <g opacity={fade(t, T[3] + 5.0, T[4])}>
            {ORDER.map((j, q) => {
              const a = T[3] + 5.2 + 0.9 * q;
              const p = legsAt(t, [{ pts: [[CORE_X, 700], [PEM_X, LANE[j]], ...landing(j)], a, d: 1.35 }]);
              return p && <Piece key={j} p={p} label={NAMES[j]} n={q + 1} op={prog(t, a, 0.2) * (1 - lin(t, landF(j), 0.3))} />;
            })}
          </g>
        </g>

        {/* ---------------- c4: fault injection grid */}
        <g opacity={o4}>
          {Array.from({ length: 100 }, (_, i) => {
            const x = 80 + (i % 20) * 30;
            const y = 640 + Math.floor(i / 20) * 30;
            const red = prog(t, T[4] + 5.0 + i * 0.015, 0.2);
            const ok = prog(t, T[4] + 7.0 + i * 0.015, 0.2);
            return (
              <g key={i}>
                <rect x={x} y={y} width={24} height={24} rx={4} fill="rgba(139,149,173,0.08)" stroke={C.dim} strokeWidth={1.5} />
                <rect x={x} y={y} width={24} height={24} rx={4} fill={K.bad} opacity={red * (1 - ok)} />
                <rect x={x} y={y} width={24} height={24} rx={4} fill={`${K.ok}66`} stroke={K.ok} strokeWidth={2.5} opacity={ok} />
              </g>
            );
          })}
          <Arrow x1={700} y1={715} x2={760} y2={715} color={K.ok} w={4} head={12} k={prog(t, T[4] + 6.8, 0.4)} />
        </g>

        {/* ---------------- c5: rate bars (right) + host polls (bottom-left) */}
        <g opacity={o5}>
          <Axes x0={1320} x1={1830} y0={800} y1={250} sx={(v) => v} sy={sy5} size={24} xticks={[]} yticks={[0, 5, 10, 15, 20].map((v) => [v, String(v)] as [number, string])} ylabel="throughput (M msg/s)" />
          <RangeBar cx={1450} w={150} y0={800} sy={sy5} lo={13.9} hi={17.7} color={K.store} k={prog(t, T[5] + 1.0, 0.9)} label="13.9–17.7" />
          <RangeBar cx={1700} w={150} y0={800} sy={sy5} lo={13} hi={17.6} color={K.store} k={prog(t, T[5] + 5.0, 0.9)} label="13–17.6" />
          <text x={1450} y={840} textAnchor="middle" fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT} opacity={prog(t, T[5] + 1.0, 0.5)}>
            1 card core
          </text>
          <text x={1700} y={840} textAnchor="middle" fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT} opacity={prog(t, T[5] + 5.0, 0.5)}>
            2–4 cores
          </text>
          {Array.from({ length: 24 }, (_, i) => {
            const k = prog(t, T[5] + 7.0 + i * 0.06, 0.2);
            return <rect key={i} x={80 + i * 38} y={640} width={30} height={30} rx={5} fill={k > 0.01 ? `${K.store}${hex(0.75 * k)}` : "rgba(139,149,173,0.08)"} stroke={k > 0.5 ? K.store : C.dim} strokeWidth={1.5} />;
          })}
        </g>

        {/* ---------------- c6: one host cache line */}
        <g opacity={o6}>
          <line x1={cell3[0] - halfCell} y1={cell3[1] + 11} x2={LINE_X} y2={690} stroke={K.store} strokeWidth={2} strokeDasharray="6 6" opacity={0.6 * prog(t, T[6] + 0.8, 0.5)} />
          <line x1={cell3[0] + halfCell} y1={cell3[1] + 11} x2={LINE_X + 4 * Q_W} y2={690} stroke={K.store} strokeWidth={2} strokeDasharray="6 6" opacity={0.6 * prog(t, T[6] + 0.8, 0.5)} />
          <g opacity={prog(t, T[6] + 0.8, 0.5)}>
            <rect x={80} y={680} width={250} height={100} rx={14} fill={C.panel} stroke={K.host} strokeWidth={2.5} />
            <text x={205} y={716} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={K.host} fontFamily={FONT}>
              host core
            </text>
            <text x={205} y={750} textAnchor="middle" dominantBaseline="central" fontSize={22} fill={C.muted} fontFamily={FONT}>
              polls the line
            </text>
            <rect x={1470} y={680} width={370} height={100} rx={14} fill={C.panel} stroke={K.store} strokeWidth={2.5} />
            <text x={1655} y={716} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={K.store} fontFamily={FONT}>
              PCIe from card
            </text>
            <text x={1655} y={750} textAnchor="middle" dominantBaseline="central" fontSize={22} fill={C.muted} fontFamily={FONT}>
              4 × 16 B per reply
            </text>
            {[0, 1, 2, 3].map((i) => (
              <g key={i}>
                <rect x={LINE_X + i * Q_W} y={690} width={Q_W} height={80} fill={`${K.store}${hex(0.45 * quarterA(i))}`} stroke={C.dim} strokeWidth={2} />
                <text x={LINE_X + (i + 0.5) * Q_W} y={730} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
                  16 B
                </text>
              </g>
            ))}
            <rect x={LINE_X} y={690} width={4 * Q_W} height={80} fill="none" stroke={K.mem} strokeWidth={5} opacity={qj < 0 ? 0.4 : 1 - ownPcie} />
            <rect x={LINE_X} y={690} width={4 * Q_W} height={80} fill="none" stroke={K.store} strokeWidth={5} opacity={ownPcie} filter="url(#glow)" />
            <Arrow x1={332} y1={730} x2={LINE_X - 4} y2={730} color={K.mem} w={5} head={14} opacity={0.3 + 0.7 * pollK} />
            <Arrow x1={1468} y1={730} x2={LINE_X + 4 * Q_W + 4} y2={730} color={K.store} w={3} head={12} dash="8 8" opacity={0.35} />
            {/* ping-pong arc */}
            <path d="M340 674 Q 900 556 1460 674" fill="none" stroke={C.muted} strokeWidth={3} strokeDasharray="10 8" />
            <polygon points="340,674 362,660 358,678" fill={C.muted} />
            <polygon points="1460,674 1438,660 1442,678" fill={C.muted} />
            <text x={900} y={592} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
              ping-pong
            </text>
            <text x={892} y={800} textAnchor="end" dominantBaseline="central" fontSize={24} fill={C.muted} fontFamily={FONT}>
              line held by:
            </text>
            <text x={904} y={800} dominantBaseline="central" fontSize={24} fontWeight={800} fill={K.mem} fontFamily={FONT} opacity={1 - clamp01(ownPcie * 2)}>
              host core
            </text>
            <text x={904} y={800} dominantBaseline="central" fontSize={24} fontWeight={800} fill={K.store} fontFamily={FONT} opacity={clamp01(ownPcie * 2 - 1)}>
              PCIe write
            </text>
            <text x={LINE_X + 2 * Q_W} y={840} textAnchor="middle" dominantBaseline="central" fontSize={24} fill={C.muted} fontFamily={FONT}>
              one 64 B host cache line · four partial writes per reply
            </text>
          </g>
          {qj >= 0 && <Piece p={[lerp(1440, LINE_X + (qj + 0.5) * Q_W, lin(v6, 0, 0.7)), 730]} label="16 B" w={110} op={1 - lin(v6, 0.7, 0.2)} />}
          <InfTag x={1462} y={593} opacity={prog(t, T[6] + 0.3, 0.5)} />
          <g opacity={prog(t, T[6] + 9.5, 0.6)}>
            <rect x={1255} y={250} width={560} height={70} rx={10} fill={K.store} />
            <text x={1535} y={285} textAnchor="middle" dominantBaseline="central" fontSize={28} fontWeight={800} fill={C.bg} fontFamily={FONT}>
              64 B · one write
            </text>
          </g>
        </g>
      </Stage>

      {/* ---------------- c0 */}
      <Txt x={70} y={588} size={24} color={C.muted} style={{ opacity: o0 * prog(t, T[0] + 1.0, 0.5) }}>
        Reply path: card core → host memory, no DPI
      </Txt>
      <Stat
        value={<span style={{ whiteSpace: "nowrap" }}>0.94–0.98 µs</span>}
        label="W=1 p50 round trip"
        color={K.store}
        size={84}
        style={{ position: "absolute", left: 1215, top: 230, width: 640, boxSizing: "border-box", ...appear(t, T[0] + 6.3, T[1]) }}
      />
      <Pill x={1215} y={470} size={30} color={C.muted} style={appear(t, T[0] + 10.3, T[1])}>
        some runs 1.23–1.27 µs
      </Pill>
      <Txt x={1215} y={545} w={640} size={24} color={C.muted} style={appear(t, T[0] + 11.0, T[1])}>
        two modes per host run, cause unknown; seen on other paths too
      </Txt>

      {/* ---------------- c1 */}
      <Pill x={1545} y={410} size={24} color={K.store} solid style={appear(t, T[1] + 2.0, T[2])}>
        first push under 1 µs
      </Pill>
      <Txt x={70} y={588} size={24} color={C.muted} style={{ opacity: o1 * prog(t, T[1] + 8.0, 0.5) }}>
        Card-side cost per reply
      </Txt>
      {(
        [
          [640, "stores + barrier", K.store, T[1] + 8.1],
          [720, "DPI doorbell → done", K.dma, T[1] + 11.7],
        ] as [number, string, string, number][]
      ).map(([y, l, col, a], i) => (
        <Txt key={i} x={60} y={y + 8} w={315} size={24} align="right" color={col} weight={700} style={{ opacity: o1 * prog(t, a, 0.4) }}>
          {l}
        </Txt>
      ))}

      {/* ---------------- c2 / c3 */}
      {(
        [
          ["Trap 1: one 64 B store splits", K.bad, T[2] + 0.5, T[3]],
          ["Trap 2: PEM write merging", K.bad, T[3] + 0.5, T[3] + 4.6],
          ["Fix: 4 × 16 B, head last", K.ok, T[3] + 5.1, T[4]],
        ] as [string, string, number, number][]
      ).map(([l, col, a, b], i) => (
        <Pill key={i} x={1215} y={135} size={28} color={col} solid style={appear(t, a, b, 12)}>
          {l}
        </Pill>
      ))}
      <CodeBox x={1215} y={200} w={640} lines={["// one instruction, 64 B", "st1  {v0.2d-v3.2d}, [x9]"]} style={appear(t, T[2] + 0.8, T[3], 12)} />
      <Txt x={1215} y={330} w={640} size={24} color={C.muted} style={appear(t, T[2] + 1.6, T[3], 12)}>
        host check: head seq = tail seq, then the payload pattern
      </Txt>
      <CodeBox
        x={1215}
        y={200}
        w={640}
        lines={["// ascending, wnmerge=0", "stp  x0, x1, [x9]        // head", "stp  x2, x3, [x9, #16]", "stp  x4, x5, [x9, #32]", "stp  x6, x7, [x9, #48]   // tail"]}
        hl={(i) => (i === 0 ? 0 : fade(t, T[3] + 0.8 + 0.25 * (i - 1), T[3] + 1.05 + 0.25 * (i - 1), 0.12))}
        style={appear(t, T[3] + 0.6, T[3] + 4.6, 12)}
      />
      <CodeBox
        x={1215}
        y={200}
        w={640}
        lines={["// head last, wnmerge=1", "stp  x2, x3, [x9, #16]   // 1", "stp  x4, x5, [x9, #32]   // 2", "stp  x6, x7, [x9, #48]   // 3 tail", "stp  x0, x1, [x9]        // 4 head", "dsb                      // barrier"]}
        hl={(i) => (i === 0 ? 0 : fade(t, T[3] + 5.2 + 0.9 * (i - 1), T[3] + 6.0 + 0.9 * (i - 1), 0.15))}
        style={appear(t, T[3] + 5.1, T[4], 12)}
      />
      <Pill x={1110} y={792} size={22} color={K.bad} style={appear(t, T[3] + 0.6, T[3] + 4.6, 10)}>
        wnmerge=0 · merging on
      </Pill>
      <Pill x={1110} y={792} size={22} color={K.ok} style={appear(t, T[3] + 5.0, T[4], 10)}>
        wnmerge=1 · merging off
      </Pill>
      {(
        [
          [752, 26, C.text, 400, "head seq = tail seq → looks complete", T[2] + 8.6, T[3]],
          [798, 28, K.bad, 800, "✗ stale payload: bad data", T[2] + 9.6, T[3]],
          [752, 26, C.text, 400, "merging reorders the pieces", T[3] + 2.2, T[3] + 4.6],
          [798, 28, K.bad, 800, "✗ stale payload again", T[3] + 3.5, T[3] + 4.6],
          [752, 26, C.text, 400, "merging off: stores land in order", T[3] + 6.4, T[4]],
          [798, 28, K.ok, 800, "✓ head lands last: valid head = complete reply", T[3] + 9.5, T[4]],
        ] as [number, number, string, number, string, number, number][]
      ).map(([y, size, col, wt, l, a, b], i) => (
        <Txt key={i} x={240} y={y} size={size} color={col} weight={wt} style={{ opacity: fade(t, a, b) }}>
          {l}
        </Txt>
      ))}

      {/* ---------------- c4 */}
      <Stat
        value={<span style={{ whiteSpace: "nowrap" }}>&gt; {countUp(t, T[4] + 0.8, 2.0, 40)} M</span>}
        label="replies verified with the fix"
        color={K.ok}
        size={110}
        style={{ position: "absolute", left: 1215, top: 170, width: 640, boxSizing: "border-box", ...appear(t, T[4] + 0.6, T[5]) }}
      />
      {["lost 0", "bad 0", "torn 0"].map((l, i) => (
        <Pill key={i} x={1215 + i * 215} y={440} size={34} color={K.ok} style={appear(t, T[4] + 2.8 + i * 0.4, T[5], 12)}>
          {l}
        </Pill>
      ))}
      <Txt x={70} y={588} size={24} color={C.muted} style={{ opacity: o4 * prog(t, T[4] + 4.8, 0.5) }}>
        Fault injection: the card corrupts 100 replies on purpose
      </Txt>
      <Txt x={790} y={640} size={26} color={C.muted} style={{ opacity: o4 * prog(t, T[4] + 6.8, 0.5) }}>
        host caught
      </Txt>
      <Txt x={790} y={672} size={96} color={K.ok} weight={800} style={{ opacity: o4 * prog(t, T[4] + 6.8, 0.5), fontVariantNumeric: "tabular-nums" }}>
        {countUp(t, T[4] + 7.0, 1.6, 100)}
      </Txt>
      <Txt x={790} y={800} size={26} color={C.muted} style={{ opacity: o4 * prog(t, T[4] + 6.8, 0.5) }}>
        of 100 corrupted
      </Txt>

      {/* ---------------- c5 */}
      <Txt x={1215} y={140} w={640} size={26} color={C.muted} style={appear(t, T[5] + 6.0, T[6], 12)}>
        no better with 2–4 card cores
      </Txt>
      <Txt x={70} y={588} size={24} color={C.muted} style={{ opacity: o5 * prog(t, T[5] + 6.8, 0.5) }}>
        Host polls of the reply ring
      </Txt>
      <Txt x={80} y={690} w={1100} size={30} style={appear(t, T[5] + 8.0, T[6])}>
        every poll finds a reply waiting: <span style={{ color: K.store, fontWeight: 800 }}>0 % empty</span>
      </Txt>
      <Txt x={80} y={752} w={1100} size={38} weight={800} style={appear(t, T[5] + 9.6, T[6])}>
        → the <span style={{ color: K.host }}>host</span> is the limit
      </Txt>

      {/* ---------------- c6 */}
      <Txt x={1290} y={588} size={26} weight={800} style={{ opacity: prog(t, T[6] + 0.3, 0.5) }}>
        Likely cause
      </Txt>
      <Pill x={1545} y={588} size={22} color={K.inf} style={{ opacity: prog(t, T[6] + 0.3, 0.5) }}>
        unverified
      </Pill>
      <div
        style={{
          position: "absolute",
          left: 1215,
          top: 150,
          width: 640,
          height: 340,
          boxSizing: "border-box",
          border: `2px solid ${K.store}`,
          borderRadius: 20,
          background: "rgba(20,27,46,0.6)",
          ...appear(t, T[6] + 9.3),
        }}
      />
      <Txt x={1245} y={168} size={26} color={C.muted} style={appear(t, T[6] + 9.3)}>
        Next step
      </Txt>
      <Txt x={1245} y={340} w={590} size={34} weight={800} style={appear(t, T[6] + 9.8)}>
        one full-line write per reply
      </Txt>
      <Txt x={1245} y={392} w={590} size={26} color={C.muted} style={appear(t, T[6] + 10.3)}>
        instead of four 16 B partial writes
      </Txt>
    </>
  );
}

export const S11: SceneDef = {
  id: "S11",
  title: "Way back #4: card cores store replies",
  stage: 2,
  source: "inline_reflector --store · inline_pingpong --reply ring",
  dur: DUR,
  cues,
  C: Body,
};
