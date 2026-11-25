import React from "react";
import { Cue, SceneDef, lin, prog, useT } from "../lib";
import { P, Stage, alpha, win } from "../ccnic/kit";
import { Check, Dim, MmDefs, NODES, NodeId, ReadPath, T as Tx, TimeBar, barLayout, rpGeom } from "./mk";
import { DDR_EXTRA, SIX } from "./_Mrv191M09to12Opus55";

// <cues>
const cues: Cue[] = [
  [0.60, 5.32, "Now the whole read, stage by stage: eight bytes from the card's L3.", "Now the whole read, stage by stage: eight bytes from the card's L three."], // 0
  [5.67, 15.17, "Host chip, core to root port: **~205 ns**. More\u00a0than\u00a0half\u00a0is\u00a0the\u00a01\u00a0GHz\u00a0IRP₁ + IIO₁ and the root port: **125.2 ns**.", "Inside the host chip, core to root port: about two hundred five nanoseconds, and more than half of it is the one gigahertz I R P, I I O and root port."], // 1
  [15.52, 24.92, "PCIe layers, link, PEM0 and the register path: **291–312 ns**, the biggest piece, depending on whether the IIO is awake.", "Then the P C I E layers, the link, P E M zero and the register path: two hundred ninety-one to three hundred twelve nanoseconds, the biggest piece."], // 2
  [25.27, 32.77, "Inside the card: **+103–105 ns** through the BAR4\u00a0window\u00a0to\u00a0L3, and **+93–102 ns** more on a miss to DDR5.", "Inside the card: about a hundred and four more to reach the L three, and ninety-three to a hundred and two more for DDR five."], // 3
  [33.12, 41.38, "Six stages, host core to card memory, each measured two\u00a0independent\u00a0ways, with nothing but software and the chips' own counters.", "Six stages, from the host core to the card's memory, each measured two independent ways, with nothing but software and the chips' own counters."], // 4
];
const DUR = 42.78; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const g = rpGeom(1, 0, 0);
const BAR = { x: 160, y: 600, w: 1600, h: 56, max: 724.0 };
/** six stages + the DDR5 extra (index 6, dashed) */
const SEGS = [...SIX, DDR_EXTRA];
const LAY = barLayout(BAR.x, BAR.w, BAR.max, SEGS);
const SEG_C = LAY.map((r) => (r.x0 + r.x1) / 2);
const BAR_END = LAY[LAY.length - 1].x1;
/** node group under the read path for each stage (x range) */
const n = g.n;
const GROUPS: [number, number][] = [
  [n.core.x, n.core.x + n.core.w],
  [n.cha.x, n.cha.x + n.cha.w],
  [n.m2p.x, n.m2p.x + n.m2p.w],
  [n.irp.x, n.rp.x + n.rp.w],
  [g.linkX0, n.pem.x + n.pem.w],
  [n.iobn.x, n.tad.x + n.tad.w],
  [n.ddr.x, n.ddr.x + n.ddr.w],
];
const GC = GROUPS.map(([a, b]) => (a + b) / 2);
const STAGE_OF: Record<NodeId, number> = { core: 0, cha: 1, m2p: 2, irp: 3, iio: 3, rp: 3, pem: 4, sdp: 4, iobn: 5, tad: 5, ddr: 6 };
/** stages in focus during c1, c2, c3 */
const FOCUS = [[0, 1, 2, 3], [4], [5, 6]];
const GL_Y = 438;
const NUM_Y = 470;
const LEAD_Y = NUM_Y + 20;
const SUB_Y = 690;

function Body() {
  const t = useT();
  const growT0 = T[0] + 0.8;
  const growD = 2.6;
  const grow = lin(t, growT0, growD);
  const appear = SEG_C.map((c) => prog(t, growT0 + growD * ((c - BAR.x) / (BAR_END - BAR.x)), 0.4));
  // focus weights per cue; off[i] = how much stage i is out of focus
  const w = [win(t, T[1], T[2]), win(t, T[2], T[3]), win(t, T[3], T[4])];
  const off = SEGS.map((_, i) => w.reduce((a, wc, c) => a + (FOCUS[c].includes(i) ? 0 : wc), 0));
  const on = SEGS.map((_, i) => w.reduce((a, wc, c) => a + (FOCUS[c].includes(i) ? wc : 0), 0));
  const all = prog(t, T[4], 0.5);
  const lit = prog(t, T[0] + 0.3, 0.8);
  const hl: Partial<Record<NodeId, number>> = {};
  const dim: Partial<Record<NodeId, number>> = {};
  NODES.forEach((id) => {
    const s = STAGE_OF[id];
    hl[id] = lit * (0.55 + 0.45 * Math.max(all, on[s]));
    dim[id] = 0.6 * off[s];
  });
  const segs = SEGS.map((s, i) => ({ ...s, op: 1 - 0.65 * off[i] }));
  const inside = (i: number, text: string, k: number, size = 26) => (
    <Tx x={SEG_C[i]} y={BAR.y + BAR.h / 2 + 1} anchor="middle" size={size} weight={800} color={SEGS[i].dashed ? SEGS[i].color : P.bg0} opacity={k}>
      {text}
    </Tx>
  );
  const sub = (i: number, text: string, k: number) => (
    <Tx x={SEG_C[i]} y={SUB_Y} anchor="middle" size={20} weight={600} color={SEGS[i].color} opacity={k}>
      {text}
    </Tx>
  );
  return (
    <Stage>
      <MmDefs />
      <ReadPath g={g} opacity={prog(t, T[0], 0.5)} hl={hl} dim={dim} bands={0} heads={1} />
      {/* stage groups, numbers, leaders to the bar */}
      {SEGS.map((s, i) => {
        const k = appear[i] * (1 - 0.65 * off[i]);
        const [a, b] = GROUPS[i];
        const ddr = i === 6;
        const y0 = ddr ? GL_Y + 8 : LEAD_Y;
        return (
          <g key={i} opacity={k}>
            {ddr ? (
              <line x1={a + 8} y1={GL_Y + 2} x2={b - 8} y2={GL_Y + 2} stroke={s.color} strokeWidth={3} strokeDasharray="7 6" />
            ) : (
              <rect x={a + 8} y={GL_Y} width={Math.max(0, b - a - 16)} height={4} rx={2} fill={s.color} opacity={0.85} />
            )}
            <path
              d={`M${GC[i]} ${y0} C${GC[i]} ${y0 + 40} ${SEG_C[i]} ${BAR.y - 46} ${SEG_C[i]} ${BAR.y - 6}`}
              fill="none"
              stroke={alpha(s.color, 0.55)}
              strokeWidth={1.5}
              strokeDasharray={ddr ? "5 5" : undefined}
            />
            {!ddr && (
              <>
                <circle cx={GC[i]} cy={NUM_Y} r={18} fill={P.bg0} stroke={s.color} strokeWidth={2} />
                <Tx x={GC[i]} y={NUM_Y + 1} anchor="middle" size={20} weight={700} color={s.color}>
                  {i + 1}
                </Tx>
                <Check x={GC[i] + 36} y={NUM_Y} s={0.8} k={prog(t, T[4] + 0.6 + i * 0.3, 0.3)} />
                <Check x={GC[i] + 58} y={NUM_Y} s={0.8} k={prog(t, T[4] + 0.75 + i * 0.3, 0.3)} />
              </>
            )}
          </g>
        );
      })}
      <TimeBar x={BAR.x} y={BAR.y} w={BAR.w} h={BAR.h} max={BAR.max} segs={segs} grow={grow} />
      {/* c1: the host chip */}
      {inside(3, "125.2 ns", win(t, T[1] + 1.6, T[2]))}
      {sub(3, "IRP₁ + IIO₁ + root port", win(t, T[1] + 1.9, T[2]))}
      <g opacity={win(t, T[1] + 0.4, T[2])}>
        <Dim x0={LAY[0].x0} x1={LAY[3].x1} y={744} label="~205 ns" dir={1} size={30} k={prog(t, T[1] + 0.5, 0.8)} />
      </g>
      {/* c2: the link path */}
      {inside(4, "291–312 ns", win(t, T[2] + 0.6, T[3]), 28)}
      {sub(4, "biggest piece · IIO awake vs dozing", win(t, T[2] + 1.2, T[3]))}
      {/* c3: inside the card */}
      {inside(5, "+103–105 ns", win(t, T[3] + 0.6, T[4]), 24)}
      {sub(5, "BAR4 window → L3", win(t, T[3] + 0.9, T[4]))}
      {inside(6, "+93–102 ns", win(t, T[3] + 2.2, T[4]), 24)}
      {sub(6, "DDR5 miss", win(t, T[3] + 2.5, T[4]))}
      {/* c4: summary */}
      <Tx x={960} y={790} anchor="middle" size={30} opacity={prog(t, T[4] + 2.8, 0.6)}>
        <tspan fill={P.gold} fontWeight={800}>
          622.7 ns
        </tspan>{" "}
        mean (4,000 lines) · fixed line{" "}
        <tspan fill={P.gold} fontWeight={800}>
          627.3 ns
        </tspan>{" "}
        median
      </Tx>
    </Stage>
  );
}

export const M11: SceneDef = {
  id: "M11",
  title: "Six stages, two proofs each",
  stage: 4,
  source: "8-byte read into card L3 \u00b7 means unless marked",
  dur: DUR,
  cues,
  C: Body,
};
