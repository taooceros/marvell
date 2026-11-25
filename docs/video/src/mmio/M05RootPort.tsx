import React from "react";
import { Cue, SceneDef, lin, prog, useT } from "../lib";
import { BigStat, Cell, CellAt, P, Stage, Tag, win } from "../ccnic/kit";
import { Badge, Check, D, MmDefs, NodeId, ReadPath, T as Tx, TimeBar, barLayout, rcx, rpGeom, tripRoute } from "./mk";

// <cues>
const cues: Cue[] = [
  [0.60, 8.50, "Round two: six `chown`s, still no root shell. Now\u00a0we\u00a0may\u00a0map\u00a0the\u00a0root\u00a0port's\u00a0own\u00a0registers, read-only, one address.", "Round two: six file permissions, still no root shell. Now we may map the root port's own registers, read only, one address."], // 0
  [8.85, 19.31, "A read to the root port turns around inside it: **205.5 ns**. Same core, mesh and IIO, but no link and no card.", "Reading the root port, the request turns around inside it after two hundred five nanoseconds. Same core and mesh, same I R P and I I O, but no link and no card."], // 1
  [19.66, 27.12, "A reviewer's warning: matching counters don't prove a shared\u00a0path. A config read that never leaves the port counts the same.", "But a reviewer warned us: matching counters do not prove a shared path. A config read that never leaves the root port counts the same."], // 2
  [27.47, 35.43, "So a gate first: on the same CHA, root port and card memory spend the same time outside the TOR: **21.2** vs **22.3 ns**.", "So it had to pass a gate: on the same C H A, it must spend the same time outside the T O R as a real card read. It does."], // 3
  [35.78, 43.22, "Then a control group: many cores on one address stall at 1\u00a0/\u00a0TOR on **both** paths. The limit is the CHA's, not the port's.", "And a control group: many cores on one address stall on the card register just like on the root port. That limit comes from the C H A."], // 4
  [43.57, 52.65, "Card register minus root port: **+312.1 ns**. That\u00a0is\u00a0the\u00a0host's\u00a0PCIe\u00a0layers, the link, PEM0, and the path to the register.", "Card register minus root port: three hundred twelve nanoseconds. The host's P C I E layers, the link, P E M zero, and the path to the register."], // 5
];
const DUR = 54.05; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const g = rpGeom(1, 0, 0);
const TO_RP = tripRoute(g, "rp");
const TO_SDP = tripRoute(g, "sdp");
/** Past the root port: link and card. */
const FAR: NodeId[] = ["pem", "sdp", "iobn", "tad", "ddr"];
const COUNTED: NodeId[] = ["m2p", "irp", "iio"];
// c3: same-CHA gate (25 px per ns)
const GX0 = 760;
const GATE = [
  { label: "root port", v: 21.2, text: "21.2 ns", color: D.io },
  { label: "card memory", v: 22.3, text: "22.3 ns", color: D.card },
];
// c4: control group, speedup of 8 cores over 1 (110 px per ×)
const SPEED = [
  { label: "root port · one address", v: 1.3, text: "1.30×", color: D.io },
  { label: "card register · one address", v: 1.1, text: "1.10×", color: D.link },
  { label: "different lines", v: 8.0, text: "8.0×", color: D.core },
];
// c5: card register = root port + the rest
const FB = { x: 200, y: 700, w: 1500, h: 52 };
const SPLIT = [
  { v: 205.5, color: D.io, inside: "root port · 205.5 ns" },
  { v: 312.1, color: D.link },
];
const SPLIT_MAX = 205.5 + 312.1;

function Body() {
  const t = useT();
  const rpK = prog(t, T[0] + 1.0, 0.6);
  const farDim = prog(t, T[1] + 0.3, 0.6);
  const linkBack = prog(t, T[5] + 0.3, 0.6);
  const dim: Partial<Record<NodeId, number>> = {};
  FAR.forEach((id) => (dim[id] = farDim * (id === "pem" || id === "sdp" ? 1 - linkBack : 1)));
  const c3 = win(t, T[3] + 0.2, T[4]);
  const c4 = win(t, T[4] + 0.2, T[5]);
  const c5 = prog(t, T[5] + 0.3, 0.5);
  const hl: Partial<Record<NodeId, number>> = {
    rp: rpK,
    core: c3,
    cha: Math.max(c3, c4),
    pem: linkBack,
    sdp: linkBack,
  };
  COUNTED.forEach((id, i) => (hl[id] = 0.8 * win(t, T[2] + 3.1 + i * 0.35, T[3])));
  // travelling reads
  const cells: React.ReactNode[] = [];
  const trip = (key: string, pts: typeof TO_RP, start: number, dur: number, color: string, label?: string, w = 30) => {
    const k = lin(t, start, dur);
    if (k > 0 && k < 1) cells.push(<Cell key={key} pts={pts} k={k} color={color} label={label} w={w} h={22} />);
  };
  trip("c1a", TO_RP, T[1] + 0.4, 2.6, P.gold);
  trip("c1b", TO_RP, T[1] + 5.4, 2.6, P.gold);
  trip("mmio", TO_RP, T[2] + 0.3, 2.6, P.gold, "MMIO", 48);
  trip("cfg", TO_RP, T[2] + 1.4, 2.6, D.io, "CFG", 48);
  trip("c5", TO_SDP, T[5] + 0.6, 3.6, D.link);
  const c2 = win(t, T[2] + 0.2, T[3]);
  const lay = barLayout(FB.x, FB.w, SPLIT_MAX, SPLIT);
  const linkMid = (lay[1].x0 + lay[1].x1) / 2;
  return (
    <>
      <Stage>
        <MmDefs />
        <ReadPath
          g={g}
          opacity={prog(t, T[0], 0.6)}
          hl={hl}
          hlColor={{ rp: P.gold }}
          dim={dim}
          link={1 - 0.75 * farDim * (1 - linkBack)}
          under={cells}
        />
        {/* c2: two kinds of read, same counts */}
        <g opacity={c2}>
          <g opacity={prog(t, T[2] + 0.3, 0.4)}>
            <CellAt x={g.n.core.x + 20} y={500} color={P.gold} w={30} />
            <Tx x={g.n.core.x + 50} y={500} size={24}>
              MMIO read
            </Tx>
          </g>
          <g opacity={prog(t, T[2] + 1.4, 0.4)}>
            <CellAt x={g.n.core.x + 270} y={500} color={D.io} w={30} />
            <Tx x={g.n.core.x + 300} y={500} size={24}>
              config read
            </Tx>
          </g>
          {COUNTED.map((id, i) => (
            <Badge key={id} x={rcx(g.n[id])} y={g.n[id].y - 40} text="1.0000" k={prog(t, T[2] + 3.1 + i * 0.35, 0.4)} />
          ))}
        </g>
        {/* c3: same-CHA gate */}
        <g opacity={c3}>
          <Tx x={GX0} y={560} size={22} color={P.sub}>
            time outside the TOR · same CHA 28
          </Tx>
          {GATE.map((r, i) => {
            const y = 610 + i * 80;
            const k = prog(t, T[3] + 0.6 + i * 1.2, 0.8);
            return (
              <g key={r.label} opacity={prog(t, T[3] + 0.4 + i * 1.2, 0.4)}>
                <Tx x={GX0 - 20} y={y + 21} anchor="end" size={26} color={r.color} weight={600}>
                  {r.label}
                </Tx>
                <rect x={GX0} y={y} width={r.v * 25 * k} height={42} rx={8} fill={r.color} opacity={0.88} />
                <Tx x={GX0 + r.v * 25 + 20} y={y + 22} size={28} weight={800} color={r.color} opacity={prog(t, T[3] + 1.2 + i * 1.2, 0.4)}>
                  {r.text}
                </Tx>
              </g>
            );
          })}
          <Check x={1540} y={670} s={2} k={prog(t, T[3] + 4.2, 0.5)} />
        </g>
        {/* c4: control group */}
        <g opacity={c4}>
          <Tx x={GX0} y={556} size={22} color={P.sub}>
            8 cores vs 1 · speedup
          </Tx>
          <line x1={GX0 + 110} y1={584} x2={GX0 + 110} y2={800} stroke={P.hair2} strokeWidth={1.5} strokeDasharray="5 6" />
          <Tx x={GX0 + 110} y={814} anchor="middle" size={18} color={P.sub}>
            1×
          </Tx>
          {SPEED.map((r, i) => {
            const y = 596 + i * 70;
            const at = T[4] + [0.5, 1.6, 3.0][i];
            const k = prog(t, at, i === 2 ? 1.1 : 0.7);
            return (
              <g key={r.label} opacity={prog(t, at - 0.2, 0.4)}>
                <Tx x={GX0 - 20} y={y + 20} anchor="end" size={24} color={r.color} weight={600}>
                  {r.label}
                </Tx>
                <rect x={GX0} y={y} width={r.v * 110 * k} height={40} rx={8} fill={r.color} opacity={0.88} />
                <Tx x={GX0 + r.v * 110 + 18} y={y + 21} size={28} weight={800} color={r.color} opacity={prog(t, at + 0.6, 0.4)}>
                  {r.text}
                </Tx>
              </g>
            );
          })}
          <Tx x={GX0} y={856} size={26} weight={700} color={D.mesh} opacity={prog(t, T[4] + 4.6, 0.5)}>
            both stall at 1 / TOR → the CHA
          </Tx>
        </g>
        {/* c5: register − root port */}
        <g opacity={c5}>
          <TimeBar x={FB.x} y={FB.y} w={FB.w} h={FB.h} max={SPLIT_MAX} segs={SPLIT} grow={prog(t, T[5] + 0.5, 1.4)} size={26} />
          <g opacity={prog(t, T[5] + 1.6, 0.5)}>
            <Tx x={FB.x + FB.w + 20} y={FB.y + 18} size={28} weight={800}>
              517.5 ns
            </Tx>
            <Tx x={FB.x + FB.w + 20} y={FB.y + 46} size={18} color={P.sub}>
              card register
            </Tx>
          </g>
          <Tx x={linkMid} y={FB.y + FB.h + 34} anchor="middle" size={24} color={P.ink} opacity={prog(t, T[5] + 4.2, 0.6)}>
            PCIe layers · link · PEM0 · register path
          </Tx>
        </g>
      </Stage>
      <Tag x={720} y={462} color={P.gold} size={18} opacity={win(t, T[0] + 2.0, T[1])}>
        read-only · one address · no root shell
      </Tag>
      <BigStat x={960} y={560} align="center" w={800} value="205.5" unit="ns" size={110} color={P.gold} label="read turns around in the root port" opacity={win(t, T[1] + 2.4, T[2])} />
      <Tag x={720} y={600} color={P.bad} size={22} opacity={win(t, T[2] + 4.6, T[3])}>
        same counts ≠ same path
      </Tag>
      <BigStat x={linkMid} y={540} align="center" w={600} value="+312.1" unit="ns" size={96} color={D.link} opacity={prog(t, T[5] + 2.6, 0.6)} />
    </>
  );
}

export const M05: SceneDef = {
  id: "M05",
  title: "An endpoint inside the host",
  stage: 2,
  source: "Root port 98:01.0 BAR0, read-only mmap \u00b7 408 runs, 0 errors",
  dur: DUR,
  cues,
  C: Body,
};
