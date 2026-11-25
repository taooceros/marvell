import React from "react";
import { Cue, SceneDef, lin, prog, useT } from "../lib";
import { BigStat, Cell, Flow, P, Stage, Tag, alpha, count, win } from "../ccnic/kit";
import { Badge, Cross, D, MmDefs, T as Tx } from "./mk";
import { Chip, ChipText } from "./_Mrv191M09to12Opus55";

// <cues>
const cues: Cue[] = [
  [0.60, 7.56, "Trap five: we mapped card memory write-combining, `resource4_wc`, hoping four 16-byte streaming loads would merge.", "Trap five. We mapped card memory as write-combining, hoping four sixteen-byte streaming loads would merge into one read."], // 0
  [7.91, 16.37, "They didn't: still **4 TLPs** and **2,484.6 ns**, exactly like uncached. No error, no warning.", "They did not: still four T L Ps, almost two and a half microseconds, exactly like uncached. No error, no warning."], // 1
  [16.72, 26.66, "Root cause: the driver `ioremap()`s all 64 MiB of BAR4 as\u00a0uncached at load time, and Linux PAT silently matches it.", "The octeon E P driver maps all of bar four as uncached at load time, so Linux PAT quietly downgrades our write-combining mapping. Not even a log line."], // 2
  [27.01, 36.09, "What works today: one 64-byte `zmm` load, **1 TLP**, **628.2 ns**, **3.95×** faster than four small loads.", "What works today is one sixty-four byte Z M M load: a single T L P, six hundred twenty-eight nanoseconds, nearly four times faster."], // 3
];
const DUR = 37.49; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---- top row: host core ⇄ card BAR4 over the link
const HOST = { x: 150, y: 260, w: 180, h: 110 };
const CARD = { x: 1590, y: 260, w: 180, h: 110 };
const REQ_Y = 300;
const REP_Y = 330;
const CARD_CX = CARD.x + CARD.w / 2;
/** the four 16 B loads waiting at the host */
const CELL_X = [380, 432, 484, 536];
const tripPts = (x0: number): [number, number][] => [
  [x0, REQ_Y],
  [CARD.x, REQ_Y],
  [CARD.x, REP_Y],
  [HOST.x + HOST.w, REP_Y],
];

// ---- c2: the chain behind the downgrade
const R1 = 450;
const R2 = 650;
const BH = 80;
const B1 = { x: 430, w: 320 };
const B2 = { x: 830, w: 360 };
const B3 = { x: 1270, w: 220 };

function Body() {
  const t = useT();
  // c0
  const mapK = win(t, T[0] + 0.2, T[2]);
  const ghostK = prog(t, T[0] + 2.4, 0.5);
  // c1: the merge does not happen; four separate round trips
  const cross = prog(t, T[1] + 0.2, 0.4);
  const tripStart = CELL_X.map((_, i) => T[1] + 0.9 + i * 1.05);
  const c1End = 1 - prog(t, T[2], 0.5);
  const statK = win(t, T[1] + 4.5, T[2]);
  // c2
  const c2 = win(t, T[2] + 0.4, T[3]);
  const swapK = prog(t, T[2] + 5.8, 0.5);
  // c3: one 64 B load
  const c3 = prog(t, T[3] + 0.4, 0.5);
  const ph = t - (T[3] + 0.6);
  const zK = ph >= 0 ? (ph % 3.2) / 1.4 : -1;
  const box = (b: { x: number; w: number }, y: number, i: number, a: number, color: string, children: React.ReactNode, k = 1) => (
    <Chip key={i} x={b.x} y={y} w={b.w} h={BH} color={color} k={prog(t, a, 0.4) * k}>
      {children}
    </Chip>
  );
  const arrow = (x0: number, x1: number, y: number, a: number, color = P.sub, dash?: string) => (
    <Flow
      pts={[
        [x0 + 8, y],
        [x1 - 8, y],
      ]}
      k={prog(t, a, 0.4)}
      color={color}
      w={2.5}
      head={10}
      dash={dash}
    />
  );
  return (
    <>
      <Stage>
        <MmDefs />
        {/* ---------------- top row */}
        <g opacity={prog(t, T[0], 0.5)}>
          {[-15, -5, 5, 15].map((o) => (
            <line key={o} x1={HOST.x + HOST.w} y1={315 + o} x2={CARD.x} y2={315 + o} stroke={alpha(D.link, 0.35)} strokeWidth={2} />
          ))}
          <Tx x={960} y={266} anchor="middle" size={18} color={P.sub} weight={600} spacing={1}>
            PCIe
          </Tx>
        </g>
        {/* c0/c1: the hoped-for 64 B merge */}
        <g opacity={ghostK * (1 - 0.65 * cross) * c1End}>
          <rect x={895} y={REQ_Y - 17} width={130} height={34} rx={7} fill={alpha(P.sub, 0.08)} stroke={P.sub} strokeWidth={2} strokeDasharray="6 5" />
          <Tx x={960} y={REQ_Y + 1} anchor="middle" size={20} weight={600} color={P.sub}>
            64 B
          </Tx>
        </g>
        <Flow
          pts={[
            [CELL_X[3] + 34, REQ_Y],
            [884, REQ_Y],
          ]}
          k={prog(t, T[0] + 1.8, 0.7)}
          color={P.faint}
          w={2}
          head={9}
          dash="5 6"
          opacity={1 - prog(t, T[1], 0.4)}
        />
        <Tx x={960} y={366} anchor="middle" size={22} color={P.sub} opacity={win(t, T[0] + 2.8, T[1])}>
          hoped-for merge
        </Tx>
        <Cross x={960} y={REQ_Y} s={1.6} k={cross * c1End} />
        {CELL_X.map((x, i) => {
          const appear = prog(t, T[0] + 0.6 + i * 0.25, 0.3);
          const k = lin(t, tripStart[i], 1.0);
          return k <= 0 ? (
            <Cell key={i} pts={[[x, REQ_Y], [x + 0.01, REQ_Y]]} k={0} color={D.core} w={40} h={26} opacity={appear} />
          ) : k < 1 ? (
            <Cell key={i} pts={tripPts(x)} k={k} color={D.core} w={40} h={26} />
          ) : null;
        })}
        <Tx x={(CELL_X[0] + CELL_X[3]) / 2} y={264} anchor="middle" size={22} color={D.core} weight={600} opacity={win(t, T[0] + 1.2, T[1] + 1.0)}>
          4 × 16 B
        </Tx>
        {/* arrivals at the card */}
        <g opacity={c1End}>
          {CELL_X.map((_, i) => (
            <rect key={i} x={CARD_CX - 70 + i * 36} y={390} width={28} height={12} rx={3} fill={D.core} opacity={prog(t, tripStart[i] + 0.5, 0.2)} />
          ))}
          <Badge x={CARD_CX} y={434} text="4 TLPs" size={22} color={P.bad} k={prog(t, tripStart[3] + 0.6, 0.4)} />
        </g>
        {/* c2: card memory is UC- */}
        <Badge x={CARD_CX} y={404} text="UC-" size={24} color={P.bad} k={win(t, T[2] + 6.2, T[3])} />
        {/* c3: one 64 B load, one TLP */}
        {zK >= 0 && zK <= 1 && <Cell pts={tripPts(400)} k={zK} color={P.push} w={64} h={30} />}
        <rect x={CARD_CX - 32} y={390} width={64} height={12} rx={3} fill={P.push} opacity={prog(t, T[3] + 1.3, 0.2)} />
        {/* hosts and card on top of the lanes */}
        <g opacity={prog(t, T[0], 0.5)}>
          <Chip x={HOST.x} y={HOST.y} w={HOST.w} h={HOST.h} color={D.core}>
            <ChipText x={HOST.x} y={HOST.y} w={HOST.w} h={HOST.h} text="Core" sub="host" />
          </Chip>
          <Chip x={CARD.x} y={CARD.y} w={CARD.w} h={CARD.h} color={D.card}>
            <ChipText x={CARD.x} y={CARD.y} w={CARD.w} h={CARD.h} text="BAR4" sub="card memory" />
          </Chip>
        </g>
        <Tx x={HOST.x} y={222} size={26} mono opacity={mapK}>
          mmap(resource4_wc)
        </Tx>
        <Tx x={HOST.x} y={222} size={26} mono color={P.push} opacity={c3}>
          64 B zmm load
        </Tx>
        {/* ---------------- c2: driver load vs our mapping */}
        <g opacity={c2}>
          <Tx x={B1.x} y={R1 - 22} size={18} color={P.faint} weight={600} spacing={2}>
            DRIVER LOAD
          </Tx>
          {box(B1, R1, 1, T[2] + 0.6, P.sub, <ChipText x={B1.x} y={R1} w={B1.w} h={BH} text="octep_main.c:1484" mono size={24} />)}
          {arrow(B1.x + B1.w, B2.x, R1 + BH / 2, T[2] + 0.9)}
          {box(B2, R1, 2, T[2] + 1.1, P.sub, <ChipText x={B2.x} y={R1} w={B2.w} h={BH} text="ioremap(BAR4, 64 MiB)" mono size={24} />)}
          {arrow(B2.x + B2.w, B3.x, R1 + BH / 2, T[2] + 1.4)}
          {box(
            B3,
            R1,
            3,
            T[2] + 1.6,
            P.bad,
            <>
              <Tx x={B3.x + B3.w / 2} y={R1 + 24} anchor="middle" size={18} color={P.sub}>
                PAT
              </Tx>
              <Tx x={B3.x + B3.w / 2} y={R1 + 52} anchor="middle" size={30} mono weight={700} color={P.bad}>
                UC-
              </Tx>
            </>,
          )}
          <Tx x={B1.x} y={R2 - 22} size={18} color={P.faint} weight={600} spacing={2}>
            OUR MAPPING
          </Tx>
          {box(B1, R2, 4, T[2] + 3.6, D.core, <ChipText x={B1.x} y={R2} w={B1.w} h={BH} text="mmap(resource4_wc)" mono size={24} />)}
          {arrow(B1.x + B1.w, B2.x, R2 + BH / 2, T[2] + 3.9)}
          {box(
            { x: B2.x, w: 220 },
            R2,
            5,
            T[2] + 4.1,
            P.push,
            <>
              <Tx x={B2.x + 110} y={R2 + 24} anchor="middle" size={18} color={P.sub}>
                asks
              </Tx>
              <Tx x={B2.x + 110} y={R2 + 52} anchor="middle" size={30} mono weight={700} color={P.push}>
                WC
              </Tx>
            </>,
            1 - 0.55 * swapK,
          )}
          <Cross x={B2.x + 190} y={R2 + 24} s={0.9} k={swapK} />
          <Flow
            pts={[
              [B3.x + B3.w / 2, R1 + BH + 8],
              [B3.x + B3.w / 2, R2 - 8],
            ]}
            k={prog(t, T[2] + 5.0, 0.5)}
            color={P.bad}
            w={2.5}
            head={10}
          />
          {arrow(B2.x + 220, B3.x, R2 + BH / 2, T[2] + 5.2, P.bad, "6 6")}
          <Tx x={(B2.x + 220 + B3.x) / 2} y={R2 + 12} anchor="middle" size={22} weight={600} color={P.bad} opacity={prog(t, T[2] + 5.6, 0.4)}>
            silently
          </Tx>
          {box(
            B3,
            R2,
            6,
            T[2] + 5.4,
            P.bad,
            <>
              <Tx x={B3.x + B3.w / 2} y={R2 + 24} anchor="middle" size={18} color={P.sub}>
                gets
              </Tx>
              <Tx x={B3.x + B3.w / 2} y={R2 + 52} anchor="middle" size={30} mono weight={700} color={P.bad}>
                UC-
              </Tx>
            </>,
          )}
        </g>
        {/* ---------------- c3: what works */}
        <g opacity={c3}>
          <Tx x={700} y={510} anchor="middle" size={56} weight={800} color={P.push} opacity={prog(t, T[3] + 1.5, 0.5)}>
            1 TLP
          </Tx>
          <Tx x={1220} y={510} anchor="middle" size={56} weight={800} color={P.push} opacity={prog(t, T[3] + 2.1, 0.5)}>
            628.2 ns
          </Tx>
        </g>
      </Stage>
      <BigStat x={960} y={480} align="center" w={900} value={count(t, T[1] + 4.6, 2484.6, 1, 1.0)} unit="ns" size={120} color={P.bad} label="four 16 B loads · same as uncached" opacity={statK} />
      <Tag x={832} y={700} color={P.bad} size={18} opacity={win(t, T[1] + 5.8, T[2])}>
        no error · no warning
      </Tag>
      <BigStat x={960} y={590} align="center" w={900} value={`${count(t, T[3] + 3.0, 3.95, 2, 1.0)}×`} size={150} color={P.push} label="faster than four 16 B loads" opacity={prog(t, T[3] + 2.9, 0.4)} />
    </>
  );
}

export const M10: SceneDef = {
  id: "M10",
  title: "Trap: the silent downgrade",
  stage: 3,
  source: "resource4_wc vs UC \u00b7 driver octep_main.c \u00b7 Linux x86 PAT",
  dur: DUR,
  cues,
  C: Body,
};
