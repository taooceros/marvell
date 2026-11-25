import React from "react";
import { Cue, SceneDef, clamp01, lin, prog, useT } from "../lib";
import { BigStat, Cell, P, Stage, Tag, alpha, win } from "../ccnic/kit";
import { Badge, D, Dim, MmDefs, T as Tx } from "./mk";

// <cues>
const cues: Cue[] = [
  [0.60, 8.94, "First, a stopwatch for one load: `lfence; rdtsc` on both sides, so nothing starts early or finishes late.", "First, a stopwatch for one load: L fence and R D T S C on both sides, so nothing starts early or finishes late."], // 0
  [9.29, 16.71, "The empty bracket costs **12.7 ns**, subtracted from every read: 100,000 reads per run, every reply checked.", "The empty bracket costs twelve point seven nanoseconds, and we subtract it from every read, a hundred thousand reads per run."], // 1
  [17.06, 24.28, "The counter runs at 2.2 GHz and moves two ticks at a time: every reading lands on a **0.91 ns** grid.", "The counter moves two ticks at a time, so every reading lands on a zero point nine one nanosecond grid. Remember that grid."], // 2
  [24.63, 34.79, "Clue one: width costs nothing. 4 to 32 bytes all take **627 ns** and exactly **one** PCIe read packet, one TLP.", "Clue one: the width costs nothing. Four to thirty-two bytes all take six hundred twenty-seven nanoseconds, and exactly one P C I E read packet, one T L P."], // 3
  [35.14, 44.02, "A 64-byte `zmm` load is still one TLP. Eight 8-byte loads are eight round trips in a row: **5,038 ns**.", "A sixty-four byte Z M M load is still one T L P. Eight small loads cost eight round trips in a row: over five thousand nanoseconds."], // 4
  [44.37, 54.01, "Four 16-byte loads sent **five** TLPs, and the card saw all five. Uncached loads never merge, and a replayed load is real.", "Four legacy sixteen-byte loads sent five T L Ps, and the card saw all five. Uncached loads never merge, and a replayed load is a real read too."], // 5
];
const DUR = 55.41; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---- part A: the timing bracket
const PILLS = [
  { text: "lfence", w: 130, color: P.sub },
  { text: "rdtsc", w: 120, color: D.core },
  { text: "lfence", w: 130, color: P.sub },
  { text: "load [p]", w: 210, color: P.gold },
  { text: "lfence", w: 130, color: P.sub },
  { text: "rdtsc", w: 120, color: D.core },
];
const STRIP_Y = 300;
/** Schematic histogram: readings stack only on grid points (0.91 ns apart). */
const HIST = [0, 1, 2, 5, 8, 9, 6, 4, 2, 1, 1, 0, 1, 0];

// ---- part B: the wire and the rows of round trips
const CORE = { x: 150, y: 290, w: 140, h: 84 };
const CARD = { x: 1630, y: 290, w: 150, h: 84 };
const REQ_Y = 316;
const REP_Y = 348;
const AX = { x0: 430, x1: 1700, max: 5200 };
const ROWS = [
  { label: "4 / 8 / 16 / 32 B", tlp: "1 TLP each", segs: [627.3], color: P.gold },
  { label: "64 B zmm load", tlp: "1 TLP", segs: [634.5], color: P.gold },
  { label: "8 × 8 B loads", tlp: "8 TLPs", segs: Array(8).fill(5038.2 / 8), color: D.core },
  { label: "4 × 16 B legacy SSE", tlp: "5 TLPs", segs: Array(5).fill(3199.1 / 5), color: D.core },
];
const ROW_Y0 = 500;
const ROW_DY = 72;

function Body() {
  const t = useT();
  const partA = 1 - prog(t, T[3] - 0.2, 0.6);
  const partB = prog(t, T[3] + 0.1, 0.6);
  // A: pills appear, then the load collapses (empty bracket)
  const empty = prog(t, T[1] + 0.4, 0.8);
  const widths = PILLS.map((p, i) => (i === 3 ? p.w * (1 - empty) : p.w));
  const gap = 16;
  const total = widths.reduce((a, w) => a + w, 0) + gap * (PILLS.length - 1);
  let cur = 960 - total / 2;
  const xs = widths.map((w) => {
    const x = cur;
    cur += w + gap;
    return x;
  });
  const t0x = xs[1] + widths[1] / 2;
  const t1x = xs[5] + widths[5] / 2;
  const grid = win(t, T[2] + 0.2, T[3]);
  const sx = (v: number) => AX.x0 + (v / AX.max) * (AX.x1 - AX.x0);
  // B: packet traffic on the wire, one stream per cue
  const lane: [number, number][] = [
    [CORE.x + CORE.w, REQ_Y],
    [CARD.x, REQ_Y],
    [CARD.x, REP_Y],
    [CORE.x + CORE.w, REP_Y],
  ];
  const cells: React.ReactNode[] = [];
  const trip = (start: number, dur: number, key: string, color: string, label?: string, w = 34) => {
    const k = lin(t, start, dur);
    if (k > 0 && k < 1) cells.push(<Cell key={key} pts={lane} k={k} color={color} label={label} w={w} />);
  };
  ["4B", "8B", "16B", "32B"].forEach((l, i) => trip(T[3] + 1.0 + i * 1.5, 1.3, l, P.gold, l));
  trip(T[4] + 0.4, 1.4, "64", P.gold, "64B", 46);
  for (let i = 0; i < 8; i++) trip(T[4] + 2.3 + i * 0.62, 0.6, `e${i}`, D.core, "8B");
  for (let i = 0; i < 5; i++) trip(T[5] + 0.8 + i * 0.95, 0.9, `s${i}`, i === 4 ? P.bad : D.core, "16B");
  // row reveal times
  const rowStart = [T[3] + 1.0, T[4] + 0.4, T[4] + 2.3, T[5] + 0.8];
  const rowDur = [1.3, 1.4, 8 * 0.62, 5 * 0.95];
  const dimOld = prog(t, T[5] + 0.3, 0.5);
  return (
    <>
      <Stage>
        <MmDefs />
        {/* ---------------- part A */}
        <g opacity={partA}>
          {/* fence walls */}
          {[0, 2, 4].map((i) => (
            <line
              key={i}
              x1={xs[i] + widths[i] / 2}
              y1={STRIP_Y - 70}
              x2={xs[i] + widths[i] / 2}
              y2={STRIP_Y + 110}
              stroke={alpha(P.sub, 0.5)}
              strokeWidth={2}
              strokeDasharray="6 6"
              opacity={prog(t, T[0] + 1.6, 0.5)}
            />
          ))}
          {PILLS.map((p, i) => {
            const k = prog(t, T[0] + 0.3 + i * 0.22, 0.4) * (i === 3 ? 1 - empty : 1);
            if (k <= 0 || widths[i] < 4) return null;
            return (
              <g key={i} opacity={k}>
                <rect x={xs[i]} y={STRIP_Y - 28} width={widths[i]} height={56} rx={12} fill={D.node} stroke={alpha(p.color, 0.8)} strokeWidth={i === 3 ? 2.5 : 1.5} />
                <Tx x={xs[i] + widths[i] / 2} y={STRIP_Y + 1} anchor="middle" size={26} mono color={p.color}>
                  {p.text}
                </Tx>
              </g>
            );
          })}
          {/* the load executes strictly between the fences */}
          <rect
            x={xs[2] + widths[2] / 2 + 8}
            y={STRIP_Y + 62}
            width={Math.max(0, (xs[4] + widths[4] / 2 - 8 - (xs[2] + widths[2] / 2 + 8)) * prog(t, T[0] + 2.4, 1.2))}
            height={16}
            rx={8}
            fill={P.gold}
            opacity={0.85 * (1 - empty)}
          />
          <Tx x={(xs[2] + xs[4] + widths[4]) / 2} y={STRIP_Y + 104} anchor="middle" size={20} color={P.sub} opacity={prog(t, T[0] + 3.2, 0.5) * (1 - empty)}>
            the load runs entirely inside the fences
          </Tx>
          {/* timestamps */}
          <Dim x0={t0x} x1={t1x} y={STRIP_Y - 92} label={empty > 0.5 ? "empty bracket" : "t₁ − t₀"} color={D.core} k={prog(t, T[0] + 1.9, 0.7)} size={22} weight={600} />
          {/* c2: the 2-tick grid */}
          <g opacity={grid}>
            <line x1={540} y1={640} x2={1380} y2={640} stroke={P.hair2} strokeWidth={2} />
            {HIST.map((n, i) => {
              const x = 560 + i * 61.5;
              return (
                <g key={i}>
                  <line x1={x} y1={640} x2={x} y2={652} stroke={P.sub} strokeWidth={2} />
                  {Array.from({ length: n }).map((_, j) => (
                    <circle key={j} cx={x} cy={624 - j * 17} r={7} fill={D.core} opacity={prog(t, T[2] + 0.6 + (i + j) * 0.05, 0.3)} />
                  ))}
                </g>
              );
            })}
            <Dim x0={560 + 5 * 61.5} x1={560 + 6 * 61.5} y={684} label="0.91 ns = 2 ticks" dir={1} color={P.gold} k={prog(t, T[2] + 1.8, 0.6)} size={24} />
            <Tx x={1400} y={600} size={18} color={P.faint} spacing={2}>
              SCHEMATIC
            </Tx>
          </g>
        </g>
        {/* ---------------- part B */}
        <g opacity={partB}>
          <rect x={CORE.x} y={CORE.y} width={CORE.w} height={CORE.h} rx={14} fill={D.node} stroke={alpha(D.core, 0.8)} strokeWidth={1.5} />
          <Tx x={CORE.x + CORE.w / 2} y={CORE.y + 32} anchor="middle" size={24} weight={600}>
            Core
          </Tx>
          <Tx x={CORE.x + CORE.w / 2} y={CORE.y + 60} anchor="middle" size={16} color={P.sub}>
            host
          </Tx>
          {[-15, -5, 5, 15].map((o) => (
            <line key={o} x1={CORE.x + CORE.w} y1={332 + o} x2={CARD.x} y2={332 + o} stroke={alpha(D.link, 0.35)} strokeWidth={2} />
          ))}
          <Tx x={960} y={282} anchor="middle" size={18} color={P.sub} weight={600} spacing={1}>
            PCIe Gen5 ×4 · one read packet (TLP) out, one completion back
          </Tx>
          {cells}
          <rect x={CARD.x} y={CARD.y} width={CARD.w} height={CARD.h} rx={14} fill={D.node} stroke={alpha(D.card, 0.8)} strokeWidth={1.5} />
          <Tx x={CARD.x + CARD.w / 2} y={CARD.y + 32} anchor="middle" size={24} weight={600}>
            L3
          </Tx>
          <Tx x={CARD.x + CARD.w / 2} y={CARD.y + 60} anchor="middle" size={16} color={P.sub}>
            card memory
          </Tx>
          {/* rows of round trips on one time axis */}
          <line x1={AX.x0} y1={ROW_Y0 - 30} x2={AX.x0} y2={ROW_Y0 + 4 * ROW_DY - 20} stroke={P.hair2} strokeWidth={1.5} />
          {[0, 1000, 2000, 3000, 4000, 5000].map((v) => (
            <g key={v}>
              <line x1={sx(v)} y1={ROW_Y0 + 4 * ROW_DY - 24} x2={sx(v)} y2={ROW_Y0 + 4 * ROW_DY - 14} stroke={P.hair2} strokeWidth={1.5} />
              <Tx x={sx(v)} y={ROW_Y0 + 4 * ROW_DY + 6} anchor="middle" size={18} color={P.sub}>
                {v === 0 ? "0" : `${v / 1000},000`}
              </Tx>
            </g>
          ))}
          <Tx x={AX.x1} y={ROW_Y0 + 4 * ROW_DY + 38} anchor="end" size={18} color={P.sub}>
            ns
          </Tx>
          {ROWS.map((r, i) => {
            const y = ROW_Y0 + i * ROW_DY;
            const vis = prog(t, rowStart[i] - 0.3, 0.4);
            const grow = lin(t, rowStart[i], rowDur[i]);
            const old = i < 3 ? 1 - 0.55 * dimOld : 1;
            let x = AX.x0;
            return (
              <g key={i} opacity={vis * old}>
                <Tx x={AX.x0 - 20} y={y - 9} anchor="end" size={22}>
                  {r.label}
                </Tx>
                <Tx x={AX.x0 - 20} y={y + 16} anchor="end" size={17} color={i === 3 ? P.bad : P.sub}>
                  {r.tlp}
                </Tx>
                {r.segs.map((v, j) => {
                  const w = sx(v) - AX.x0;
                  const k = clamp01(grow * r.segs.length - j);
                  const el = <rect key={j} x={x + 1} y={y - 16} width={Math.max(0, (w - 3) * k)} height={32} rx={6} fill={i === 3 && j === 4 ? P.bad : r.color} opacity={0.88} />;
                  x += w;
                  return el;
                })}
              </g>
            );
          })}
          <Tx x={sx(627.3) + 18} y={ROW_Y0} size={28} weight={800} color={P.gold} opacity={prog(t, T[3] + 2.0, 0.5) * (1 - 0.55 * dimOld)}>
            627 ns
          </Tx>
          <Tx x={sx(5038.2) - 4} y={ROW_Y0 + 2 * ROW_DY - 40} anchor="end" size={28} weight={800} color={D.core} opacity={prog(t, T[4] + 7.3, 0.5) * (1 - 0.55 * dimOld)}>
            5,038 ns
          </Tx>
          <Tx x={sx(3199.1) + 18} y={ROW_Y0 + 3 * ROW_DY} size={28} weight={800} color={D.core} opacity={prog(t, T[5] + 5.6, 0.5)}>
            3,199 ns
          </Tx>
          <Badge x={CARD.x + CARD.w / 2} y={CARD.y + CARD.h + 40} text="card saw 5" color={P.bad} k={prog(t, T[5] + 5.0, 0.5)} />
        </g>
      </Stage>
      <BigStat
        x={960}
        y={440}
        align="center"
        w={700}
        value="12.7"
        unit="ns"
        size={96}
        color={D.core}
        label="the empty bracket, subtracted from every read"
        opacity={win(t, T[1] + 1.0, T[2]) * partA}
      />
      <Tag x={700} y={600} color={P.push} opacity={win(t, T[1] + 3.2, T[2]) * partA} size={17}>
        100,000 reads per run · 3 runs · every reply checked
      </Tag>
      <Tag x={1180} y={446} color={P.bad} opacity={prog(t, T[5] + 5.4, 0.5)} size={16}>
        [INF] a replayed load after a machine clear
      </Tag>
    </>
  );
}

export const M02: SceneDef = {
  id: "M02",
  title: "A stopwatch for one load",
  stage: 0,
  source: "45 runs × 100,000 reads · cpu40 3.9 GHz · mesh 2.2 GHz",
  dur: DUR,
  cues,
  C: Body,
};
