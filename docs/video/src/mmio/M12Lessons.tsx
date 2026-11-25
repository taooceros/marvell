import React from "react";
import { Cue, SceneDef, Pt, prog, useT } from "../lib";
import { BigStat, Cell, Flow, HostNic, P, Stage, Tag, alpha, count, hnGeom, hnRoutes, win } from "../ccnic/kit";
import { Check, D, MmDefs, T as Tx, TimeBar } from "./mk";
import { SIX } from "./_Mrv191M09to12Opus55";

// <cues>
const cues: Cue[] = [
  [0.60, 9.90, "The method travels: endpoints as rulers, Little's\u00a0law\u00a0on\u00a0counters, clock sweeps, and reads that do nothing.", "The method travels to any server: use endpoints as rulers, apply Little's law to counters, sweep the clocks, and cancel software with reads that do nothing."], // 0
  [10.25, 15.81, "And distrust every neat number: measure it twice, add a control group, know your timer's grid.", "And distrust every neat number: measure twice, add a control group, and know your timer's grid."], // 1
  [16.16, 25.44, "For our design the lesson is blunt: a read across PCIe costs\u00a0**~620 ns** while the core waits. Our fast path never reads across.", "For our design the lesson is blunt: a read across P C I E costs over six hundred nanoseconds while the core waits. So our fast path never reads across."], // 2
  [25.79, 35.33, "DirectPush uses posted writes both ways: the host pushes with\u00a0`MOVDIR64B`, the card pushes back, each side reads only its own cache.", "Direct Push sends posted writes both ways: the host pushes with move dir sixty-four B, the card pushes back, and each side only reads its own cache."], // 3
  [35.68, 43.20, "A full request and reply then takes **~0.9 µs**, and\u00a0most\u00a0of\u00a0it\u00a0is\u00a0the\u00a0same\u00a0physical\u00a0round\u00a0trip we just took apart.", "A full request and reply then takes about nine tenths of a microsecond, most of it the same physical round trip we just took apart."], // 4
  [43.55, 51.37, "627 ns, six stages, each proved twice. Next time a device feels slow, open it up the same way.", "Six hundred twenty-seven nanoseconds, six stages, each proved twice. When a device feels slow, open it up the same way."], // 5
];
const DUR = 52.77; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---- c0/c1: method cards and reminders
const MC = { x: 135, y: 200, w: 390, h: 250, step: 420 };
const METHODS = [
  { label: "Endpoints as rulers", color: D.io },
  { label: "Little's law on counters", color: D.mesh },
  { label: "Clock sweeps", color: D.core },
  { label: "Reads that do nothing", color: D.link },
];
const RM = { x: 170, y: 560, w: 500, h: 120, step: 540 };
const REMIND = ["Measure twice", "Add a control group", "Know your timer's grid"];

// ---- c2–c4: the Host ⇄ card diagram
const g = hnGeom(0.9, 0, -20);
const R = hnRoutes(g);
/** read completion: card cache → host core (reply lane, riser in the host margin) */
const CPL: Pt[] = (
  [
    [1335, 514],
    [650, 514],
    [650, 372],
    [420, 372],
  ] as Pt[]
).map(([x, y]) => g.at(x, y));

function Glyph({ i, cx, cy, t, color }: { i: number; cx: number; cy: number; t: number; color: string }) {
  if (i === 0) {
    // a ruler between two endpoints
    return (
      <g>
        <line x1={cx - 90} y1={cy + 10} x2={cx + 90} y2={cy + 10} stroke={color} strokeWidth={2.5} />
        {Array.from({ length: 10 }).map((_, j) => (
          <line key={j} x1={cx - 72 + j * 16} y1={cy + 10} x2={cx - 72 + j * 16} y2={cy + (j % 5 === 0 ? -6 : 0)} stroke={alpha(color, 0.7)} strokeWidth={2} />
        ))}
        {[-90, 90].map((o) => (
          <rect key={o} x={cx + o - 7} y={cy - 22} width={14} height={44} rx={4} fill={color} />
        ))}
      </g>
    );
  }
  if (i === 1) {
    return (
      <Tx x={cx} y={cy} anchor="middle" size={38} mono weight={600} color={color}>
        L = λ · W
      </Tx>
    );
  }
  if (i === 2) {
    // a chirp: the clock swept from slow to fast
    const pts: string[] = [];
    for (let j = 0; j <= 90; j++) {
      const u = j / 90;
      const th = 2 * Math.PI * (1.2 * u + 2.6 * u * u) - t * 3;
      pts.push(`${j ? "L" : "M"}${cx - 95 + u * 190} ${cy + 24 * Math.sin(th)}`);
    }
    return <path d={pts.join(" ")} fill="none" stroke={color} strokeWidth={3} strokeLinecap="round" />;
  }
  // an empty read: nothing comes back but the trip
  return (
    <g>
      <rect x={cx - 80} y={cy - 24} width={84} height={48} rx={8} fill="none" stroke={color} strokeWidth={2.5} strokeDasharray="7 6" />
      <Flow
        pts={[
          [cx + 22, cy - 14],
          [cx + 82, cy - 14],
          [cx + 82, cy + 14],
          [cx + 22, cy + 14],
        ]}
        color={color}
        w={2.5}
        head={10}
      />
    </g>
  );
}

function ReminderIcon({ i, x, y }: { i: number; x: number; y: number }) {
  if (i === 0) {
    return (
      <>
        <Check x={x - 14} y={y} s={1.2} color={P.gold} />
        <Check x={x + 18} y={y} s={1.2} color={P.gold} />
      </>
    );
  }
  if (i === 1) {
    return (
      <>
        <rect x={x - 32} y={y - 18} width={64} height={14} rx={4} fill={P.gold} />
        <rect x={x - 32} y={y + 4} width={44} height={14} rx={4} fill={P.sub} />
      </>
    );
  }
  return (
    <>
      <line x1={x - 36} y1={y + 12} x2={x + 36} y2={y + 12} stroke={P.sub} strokeWidth={2} />
      {[-30, -10, 10, 30].map((o, j) => (
        <g key={o}>
          <line x1={x + o} y1={y + 12} x2={x + o} y2={y + 20} stroke={P.sub} strokeWidth={2} />
          {Array.from({ length: [1, 3, 2, 1][j] }).map((_, k) => (
            <circle key={k} cx={x + o} cy={y + 2 - k * 11} r={4.5} fill={P.gold} />
          ))}
        </g>
      ))}
    </>
  );
}

function Body() {
  const t = useT();
  // c0/c1
  const cardsOp = win(t, T[0] + 0.2, T[2]) * (1 - 0.55 * prog(t, T[1], 0.5));
  const remOp = win(t, T[1] + 0.2, T[2]);
  // c2–c4
  const hnK = prog(t, T[2] + 0.2, 0.6) * (1 - prog(t, T[5], 0.5));
  const c2 = win(t, T[2] + 0.6, T[3]);
  const c3 = win(t, T[3] + 0.3, T[5]);
  const c4 = win(t, T[4] + 0.4, T[5]);
  const pollK = prog(t, T[3] + 4.2, 0.5);
  const pulse = 0.65 + 0.35 * Math.sin(t * 5);
  // MMIO read: request then completion, looping while the core waits
  const rd = t - (T[2] + 0.8);
  const rdPh = rd % 2.6;
  const readCell =
    rd >= 0 && t < T[3] ? (
      rdPh < 1.1 ? (
        <Cell pts={R.pushH2N} k={rdPh / 1.1} color={P.bad} w={36} h={22} />
      ) : rdPh < 2.2 ? (
        <Cell pts={CPL} k={(rdPh - 1.1) / 1.1} color={P.bad} w={36} h={22} />
      ) : null
    ) : null;
  // DirectPush: host push, then the card's pushed reply
  const dp = t - (T[3] + 0.5);
  const dpPh = dp % 3.0;
  const pushCell =
    dp >= 0 && t < T[5] ? (
      dpPh < 1.1 ? (
        <Cell pts={R.pushH2N} k={dpPh / 1.1} color={P.push} w={36} h={22} />
      ) : dpPh >= 1.4 && dpPh < 2.5 ? (
        <Cell pts={R.pushN2H} k={(dpPh - 1.4) / 1.1} color={P.push} w={36} h={22} />
      ) : null
    ) : null;
  const laneLabel = (text: string, y: number, color: string, k: number) => (
    <Tx x={(g.linkX0 + g.linkX1) / 2} y={y} anchor="middle" size={24} mono weight={600} color={color} opacity={k}>
      {text}
    </Tx>
  );
  const stalled = c2 > 0.5;
  // c5
  const c5 = prog(t, T[5] + 0.3, 0.5);
  return (
    <>
      <Stage>
        <MmDefs />
        {/* ---------------- c0: four methods */}
        <g opacity={cardsOp}>
          {METHODS.map((m, i) => {
            const k = prog(t, T[0] + 0.4 + i * 1.7, 0.5);
            const x = MC.x + i * MC.step;
            return (
              <g key={m.label} opacity={k} transform={`translate(0 ${(1 - k) * 16})`}>
                <rect x={x} y={MC.y} width={MC.w} height={MC.h} rx={18} fill={P.panel} stroke={alpha(m.color, 0.45)} strokeWidth={1.5} />
                <Glyph i={i} cx={x + MC.w / 2} cy={MC.y + 100} t={t} color={m.color} />
                <Tx x={x + MC.w / 2} y={MC.y + 196} anchor="middle" size={28} weight={600}>
                  {m.label}
                </Tx>
              </g>
            );
          })}
        </g>
        {/* ---------------- c1: three reminders */}
        <g opacity={remOp}>
          {REMIND.map((s, i) => {
            const k = prog(t, T[1] + 0.3 + i * 1.4, 0.5);
            const x = RM.x + i * RM.step;
            return (
              <g key={s} opacity={k} transform={`translate(0 ${(1 - k) * 16})`}>
                <rect x={x} y={RM.y} width={RM.w} height={RM.h} rx={18} fill={P.panel} stroke={alpha(P.gold, 0.4)} strokeWidth={1.5} />
                <ReminderIcon i={i} x={x + 70} y={RM.y + RM.h / 2} />
                <Tx x={x + 132} y={RM.y + RM.h / 2 + 1} size={30} weight={600}>
                  {s}
                </Tx>
              </g>
            );
          })}
        </g>
        {/* ---------------- c2–c4: host ⇄ card */}
        <HostNic
          g={g}
          opacity={hnK}
          hostTitle="HOST · Xeon"
          nicTitle="CARD · Marvell"
          linkLabel=""
          hl={{ hostCore: stalled ? pulse : 0, hostCache: pollK * c3, nicCache: pollK * c3 }}
          hlColor={{ hostCore: stalled ? P.bad : P.host, hostCache: pollK > 0 ? P.push : P.host, nicCache: pollK > 0 ? P.push : P.nic }}
          labels={{ hostCore: stalled ? "core stalled" : "CPU core" }}
        />
        <g opacity={hnK}>
          {/* c2: one MMIO read across the link */}
          <g opacity={c2}>
            <Flow pts={R.pushH2N} k={prog(t, T[2] + 0.8, 1.0)} color={alpha(P.bad, 0.55)} w={2.5} head={11} />
            <Flow pts={CPL} k={prog(t, T[2] + 1.8, 1.0)} color={alpha(P.bad, 0.55)} w={2.5} head={11} />
            {laneLabel("MRd →", g.laneUp - 26, P.bad, prog(t, T[2] + 1.0, 0.4))}
            {laneLabel("← CplD", g.laneDn + 28, P.bad, prog(t, T[2] + 2.0, 0.4))}
            <Tx x={(g.linkX0 + g.linkX1) / 2} y={640} anchor="middle" size={64} weight={800} color={P.bad} opacity={prog(t, T[2] + 2.6, 0.5)}>
              ~620 ns
            </Tx>
          </g>
          {readCell}
          {/* c3: DirectPush, posted writes both ways */}
          <g opacity={c3}>
            <Tx x={960} y={186} anchor="middle" size={18} weight={600} color={P.push} spacing={2.6}>
              DIRECTPUSH · POSTED WRITES BOTH WAYS
            </Tx>
            <Flow pts={R.pushH2N} k={prog(t, T[3] + 0.5, 1.0)} color={alpha(P.push, 0.6)} w={2.5} head={11} />
            <Flow pts={R.pushN2H} k={prog(t, T[3] + 1.8, 1.0)} color={alpha(P.push, 0.6)} w={2.5} head={11} />
            {laneLabel("MOVDIR64B →", g.laneUp - 26, P.push, prog(t, T[3] + 1.0, 0.4))}
            {laneLabel("← pushed reply", g.laneDn + 28, P.push, prog(t, T[3] + 2.4, 0.4))}
            <Flow pts={R.hostPoll} k={pollK} color={P.push} w={3} head={9} />
            <Flow pts={R.nicPoll} k={pollK} color={P.push} w={3} head={9} />
            <Tx x={R.hostPoll[0][0] + 14} y={(R.hostPoll[0][1] + R.hostPoll[1][1]) / 2} size={20} weight={600} color={P.push} opacity={pollK}>
              poll
            </Tx>
            <Tx x={R.nicPoll[0][0] + 14} y={(R.nicPoll[0][1] + R.nicPoll[1][1]) / 2} size={20} weight={600} color={P.push} opacity={pollK}>
              poll
            </Tx>
          </g>
          {pushCell}
        </g>
        {/* ---------------- c5: closing */}
        <g opacity={c5}>
          <TimeBar x={560} y={640} w={800} h={44} max={622.4} segs={SIX} grow={prog(t, T[5] + 0.5, 1.4)} />
          <Tx x={960} y={730} anchor="middle" size={26} color={P.sub} opacity={prog(t, T[5] + 1.8, 0.5)}>
            six stages · each measured two ways
          </Tx>
        </g>
      </Stage>
      <BigStat x={960} y={560} align="center" w={460} value="~0.9" unit="µs" size={84} color={P.push} label="request + reply" opacity={c4 * hnK} />
      <Tag x={757} y={800} color={P.sub} size={16} opacity={win(t, T[4] + 2.0, T[5]) * hnK}>
        [INF] mostly the same physical path
      </Tag>
      <BigStat x={960} y={330} align="center" w={900} value={count(t, T[5] + 0.4, 627, 0, 1.0)} unit="ns" size={170} color={P.gold} opacity={c5} />
    </>
  );
}

export const M12: SceneDef = {
  id: "M12",
  title: "What it buys us",
  stage: 4,
  source: "Method summary \u00b7 DirectPush measured on the same testbed",
  dur: DUR,
  cues,
  C: Body,
};
