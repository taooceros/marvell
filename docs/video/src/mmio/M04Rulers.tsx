import React from "react";
import { Cue, SceneDef, lin, prog, useT } from "../lib";
import { BigStat, Cell, P, Stage, alpha, win } from "../ccnic/kit";
import { Badge, Check, Cross, D, Dim, MmDefs, NodeId, ReadPath, T as Tx, TimeBar, barLayout, rcx, rpGeom, tripRoute } from "./mk";

// <cues>
const cues: Cue[] = [
  [0.60, 8.74, "Second trick: change only where the read lands. Same host path, three endpoints on the card; subtract, and the shared part cancels.", "Second trick: change only where the read lands. Same host path, three endpoints on the card. Subtract, and the shared part cancels."], // 0
  [9.09, 18.81, "A card register: **516.4 ns**. Card memory in its 48 MiB L3: **621.8 ns**. Flushed to DDR5: **715.5 ns**.", "A card register: five hundred sixteen nanoseconds. Card memory in its L three: six hundred twenty-two. Flushed to DDR five: seven hundred sixteen."], // 1
  [19.16, 26.16, "So\u00a0the\u00a0BAR4\u00a0window,\u00a0IOBN\u00a0bridge\u00a0and\u00a0L3\u00a0add\u00a0**+103.1–105.5 ns**; an L3 miss to DDR5 adds **+92.7–101.6 ns**.", "So reaching the L three adds about a hundred and four nanoseconds, and an L three miss adds ninety-three to a hundred and two."], // 2
  [26.51, 33.85, "The card's counters agree: **1.00** L3 miss and\u00a0**2.01**\u00a0DDR5\u00a0bursts\u00a0per\u00a0read: one 128-byte line, two 64-byte bursts.", "The card's counters agree: one L three miss per read, refilled by a pair of sixty-four byte DDR five bursts."], // 3
  [34.20, 42.62, "Causality check: at 1 GHz the card's miss would last **161.6 ns**, more than the host's whole **+101.6 ns**. So it runs at 2 GHz.", "A causality check pins the card's clock. At one gigahertz the miss alone would take longer than the host's whole delay. So it must run at two gigahertz."], // 4
  [42.97, 50.47, "Four pieces now. But the biggest, **~494 ns** from the host's CHA to the card, is still dark.", "Four pieces. But the biggest, about four hundred ninety-four nanoseconds from the host's C H A to the card, is still dark."], // 5
];
const DUR = 51.87; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const g = rpGeom(1, 0, 0);
/** Same host path, three endpoints on the card. */
const EPS: { id: NodeId; label: string; v: number; text: string; color: string }[] = [
  { id: "sdp", label: "card register", v: 516.4, text: "516.4 ns", color: D.link },
  { id: "tad", label: "card L3", v: 621.8, text: "621.8 ns", color: D.card },
  { id: "ddr", label: "DDR5", v: 715.5, text: "715.5 ns", color: D.ddr },
];
const TRIPS = EPS.map((e) => tripRoute(g, e.id));
// c1/c2: three rulers on one scale
const BX = 260;
const BW = 1470;
const BMAX = 715.5;
const BH = 44;
const rowY = (i: number) => 530 + i * 75;
const sx = (v: number) => BX + (v / BMAX) * BW;
// c3: one 128 B L3 line = two 64 B DDR5 bursts
const LINE = { x: 704, y: 600, w: 512, h: 54 };
const BURST_Y = 740;
// c4: the causality budget (5 px per ns)
const CX0 = 560;
const cx = (v: number) => CX0 + v * 5;
const CROWS = [
  { label: "host sees", v: 101.6, text: "+101.6 ns", color: P.gold },
  { label: "card at 1 GHz", v: 161.6, text: "161.6 ns", color: P.bad },
  { label: "card at 2 GHz", v: 80.8, text: "80.8 ns", color: P.push },
];
const crowY = (i: number) => 600 + i * 84;
// c5: four pieces of the L3 → DDR5 read
const FOUR = [
  { v: 21.8, color: D.core },
  { v: 494, color: P.faint, reveal: 0, inside: "?" },
  { v: 104, color: D.card },
  { v: 97.15, color: D.ddr, dashed: true },
];
const FMAX = 21.8 + 494 + 104 + 97.15;
const FB = { x: 160, y: 700, w: 1600, h: 48 };
const FLAB = [
  { text: "21.8 ns", sub: "core + mesh", color: D.core },
  null,
  { text: "+104 ns", sub: "card", color: D.card },
  { text: "+93–102 ns", sub: "DDR5", color: D.ddr },
];

function Body() {
  const t = useT();
  // c0: three reads, staggered; c1: one more read per ruler as it grows
  const c0Start = EPS.map((_, i) => T[0] + 0.9 + i * 1.3);
  const growStart = [T[1] + 0.4, T[1] + 3.6, T[1] + 6.7];
  const cells: React.ReactNode[] = [];
  const hl: Partial<Record<NodeId, number>> = {};
  EPS.forEach((e, i) => {
    const a = lin(t, c0Start[i], 3.4);
    if (a > 0 && a < 1) cells.push(<Cell key={`a${i}`} pts={TRIPS[i]} k={a} color={e.color} w={30} h={20} />);
    const b = lin(t, growStart[i], 2.2);
    if (b > 0 && b < 1) cells.push(<Cell key={`b${i}`} pts={TRIPS[i]} k={b} color={e.color} w={30} h={20} />);
    hl[e.id] = prog(t, c0Start[i] + 1.5, 0.4);
  });
  // c3 / c4 emphasis on the card side
  const c3 = win(t, T[3] + 0.4, T[4]);
  hl.tad = Math.max(hl.tad ?? 0, prog(t, T[3] + 0.4, 0.4));
  const c12 = win(t, T[1] + 0.1, T[3]);
  const dimAbs = prog(t, T[2] + 0.2, 0.6);
  const split = prog(t, T[3] + 3.8, 1.0);
  const c4 = win(t, T[4] + 0.3, T[5]);
  const c5 = prog(t, T[5] + 0.2, 0.5);
  const flay = barLayout(FB.x, FB.w, FMAX, FOUR);
  return (
    <>
      <Stage>
        <MmDefs />
        {/* PCIe link drawn here: ReadPath drops its link together with the hidden root port */}
        <g opacity={prog(t, T[0], 0.6) * (1 - c5)}>
          {[-15, -5, 5, 15].map((o) => (
            <line key={o} x1={g.linkX0} y1={g.midY + o} x2={g.linkX1} y2={g.midY + o} stroke={alpha(D.link, 0.55)} strokeWidth={2} />
          ))}
          <Tx x={(g.linkX0 + g.linkX1) / 2} y={g.midY - 40} anchor="middle" size={16} color={P.sub} weight={600} spacing={1}>
            PCIe Gen5 ×4
          </Tx>
        </g>
        <ReadPath
          g={g}
          opacity={prog(t, T[0], 0.6)}
          hl={hl}
          hide={{ cha: 1, m2p: 1, irp: 1, iio: 1, rp: 1, pem: c5 }}
          boxes={[
            { from: "cha", to: "rp", label: "shared", k: 1 - c5 },
            { from: "cha", to: "pem", label: "?", k: c5 },
          ]}
          under={cells}
        />
        {/* c1–c2: three rulers */}
        <g opacity={c12}>
          {EPS.map((e, i) => {
            const vis = prog(t, growStart[i] - 0.2, 0.4);
            return (
              <g key={e.id} opacity={vis}>
                <Tx x={BX - 20} y={rowY(i) + BH / 2} anchor="end" size={26} color={e.color} weight={600}>
                  {e.label}
                </Tx>
                <TimeBar
                  x={BX}
                  y={rowY(i)}
                  w={BW}
                  h={BH}
                  max={BMAX}
                  segs={[{ v: e.v, color: e.color, inside: e.text }]}
                  grow={prog(t, growStart[i], 1.2)}
                  size={26}
                  opacity={1 - 0.6 * dimAbs}
                />
                <line
                  x1={sx(e.v)}
                  y1={rowY(i) + BH + 4}
                  x2={sx(e.v)}
                  y2={852}
                  stroke={alpha(P.sub, 0.6)}
                  strokeWidth={1.5}
                  strokeDasharray="5 6"
                  opacity={prog(t, T[2] + 0.3, 0.5)}
                />
              </g>
            );
          })}
          <Dim x0={sx(516.4)} x1={sx(621.8)} y={800} label="+103.1–105.5 ns" color={D.card} k={prog(t, T[2] + 0.7, 0.7)} size={26} />
          <Dim x0={sx(621.8)} x1={sx(715.5)} y={836} label="+92.7–101.6 ns" color={D.ddr} dir={1} k={prog(t, T[2] + 3.4, 0.7)} size={26} />
        </g>
        {/* c3: the card's own counters */}
        <g opacity={c3}>
          <Badge x={rcx(g.n.tad)} y={472} text="1.00 miss/read" color={D.card} k={prog(t, T[3] + 0.6, 0.4)} />
          <Badge x={rcx(g.n.ddr) - 8} y={518} text="2.01 bursts/read" color={D.ddr} k={prog(t, T[3] + 1.7, 0.4)} />
          <g opacity={prog(t, T[3] + 2.6, 0.5)}>
            <Tx x={LINE.x - 24} y={LINE.y + LINE.h / 2} anchor="end" size={26} color={D.card} weight={600}>
              card L3
            </Tx>
            <rect x={LINE.x} y={LINE.y} width={LINE.w} height={LINE.h} rx={8} fill={D.card} opacity={0.88 * (1 - 0.6 * split)} />
            <Tx x={LINE.x + LINE.w / 2} y={LINE.y + LINE.h / 2 + 1} anchor="middle" size={24} weight={700} color={P.bg0}>
              one line · 128 B
            </Tx>
          </g>
          <g opacity={split}>
            <Tx x={LINE.x - 24} y={BURST_Y + LINE.h / 2} anchor="end" size={26} color={D.ddr} weight={600}>
              DDR5
            </Tx>
            {[0, 1].map((i) => {
              const w = LINE.w / 2 - 6 * split;
              const x = LINE.x + i * (LINE.w / 2 + 12 * split);
              const y = LINE.y + (BURST_Y - LINE.y) * split;
              return (
                <g key={i}>
                  <rect x={x} y={y} width={w} height={LINE.h} rx={8} fill={D.ddr} opacity={0.88} />
                  <Tx x={x + w / 2} y={y + LINE.h / 2 + 1} anchor="middle" size={24} weight={700} color={P.bg0}>
                    burst · 64 B
                  </Tx>
                </g>
              );
            })}
          </g>
        </g>
        {/* c4: causality budget */}
        <g opacity={c4}>
          <Tx x={CX0} y={548} size={22} color={P.sub}>
            one L3 miss on the card
          </Tx>
          {CROWS.map((r, i) => {
            const k = prog(t, T[4] + [0.5, 2.0, 4.6][i], i === 1 ? 1.2 : 0.8);
            const y = crowY(i);
            const w = (cx(r.v) - CX0) * k;
            return (
              <g key={r.label} opacity={prog(t, T[4] + [0.3, 1.8, 4.4][i], 0.4)}>
                <Tx x={CX0 - 20} y={y + 22} anchor="end" size={26} color={i ? P.ink : P.gold} weight={600}>
                  {r.label}
                </Tx>
                <rect x={CX0} y={y} width={Math.max(0, w)} height={44} rx={8} fill={r.color} opacity={0.88} />
                <Tx x={(CX0 + cx(r.v)) / 2} y={y + 23} anchor="middle" size={24} weight={800} color={P.bg0} opacity={prog(t, T[4] + [1.0, 2.9, 5.2][i], 0.4)}>
                  {r.text}
                </Tx>
              </g>
            );
          })}
          <g opacity={prog(t, T[4] + 1.2, 0.5)}>
            <line x1={cx(101.6)} y1={crowY(0) - 14} x2={cx(101.6)} y2={crowY(2) + 60} stroke={P.gold} strokeWidth={2} strokeDasharray="7 6" />
            <Tx x={cx(101.6) + 12} y={crowY(0) - 22} size={20} color={P.gold} weight={600}>
              budget
            </Tx>
          </g>
          <Cross x={cx(161.6) + 34} y={crowY(1) + 22} k={prog(t, T[4] + 3.4, 0.4)} s={1.3} />
          <Check x={cx(80.8) + 34} y={crowY(2) + 22} k={prog(t, T[4] + 5.8, 0.4)} s={1.3} />
        </g>
        {/* c5: four pieces, the biggest still dark */}
        <g opacity={c5}>
          <TimeBar x={FB.x} y={FB.y} w={FB.w} h={FB.h} max={FMAX} segs={FOUR} grow={prog(t, T[5] + 0.4, 1.4)} size={30} />
          {FLAB.map((l, i) =>
            l ? (
              <g key={i} opacity={prog(t, T[5] + 1.2 + i * 0.3, 0.4)}>
                <Tx x={(flay[i].x0 + flay[i].x1) / 2} y={FB.y + FB.h + 30} anchor="middle" size={26} weight={700} color={l.color}>
                  {l.text}
                </Tx>
                <Tx x={(flay[i].x0 + flay[i].x1) / 2} y={FB.y + FB.h + 62} anchor="middle" size={18} color={P.sub}>
                  {l.sub}
                </Tx>
              </g>
            ) : null,
          )}
          <Dim x0={flay[1].x0 + 2} x1={flay[1].x1} y={FB.y - 20} color={P.sub} k={prog(t, T[5] + 2.4, 0.6)} />
        </g>
      </Stage>
      <BigStat
        x={(flay[1].x0 + flay[1].x1) / 2}
        y={500}
        align="center"
        w={700}
        value="~494"
        unit="ns"
        size={96}
        color={P.ink}
        label="host CHA → card · still unknown"
        opacity={prog(t, T[5] + 2.6, 0.6)}
      />
    </>
  );
}

export const M04: SceneDef = {
  id: "M04",
  title: "Rulers at the far end",
  stage: 1,
  source: "Same host path, three card endpoints \u00b7 card TAD and DDR counters",
  dur: DUR,
  cues,
  C: Body,
};
