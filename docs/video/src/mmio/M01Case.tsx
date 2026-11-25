import React from "react";
import { Cue, SceneDef, clamp01, lin, prog, useT } from "../lib";
import { BigStat, Cell, P, Stage, alpha, win } from "../ccnic/kit";
import { Badge, Check, Cross, D, MmDefs, NODES, NodeId, ReadPath, T as Tx, TimeBar, barLayout, rpGeom, tripRoute } from "./mk";

// <cues>
const cues: Cue[] = [
  [0.60, 7.36, "One load instruction. Eight bytes of a network card's memory. **627 ns** until the core has them.", "One load instruction. Eight bytes of a network card's memory. Six hundred twenty-seven nanoseconds until the core has them."], // 0
  [7.71, 14.57, "CXL memory reads are quoted at **~170–250 ns** (industry\u00a0figure, not measured here). Why is this read so slow?", "C X L memory reads are quoted at around two hundred nanoseconds. So why is this P C I E read so slow?"], // 1
  [14.92, 20.72, "On the way it crosses about a dozen blocks on two chips. To software, it is one black box.", "On the way, it crosses about a dozen blocks on two chips. To software, it is one black box."], // 2
  [21.07, 29.75, "Our rules: no hardware changes, no kernel patches, no root\u00a0shell. Just a stopwatch, the chips' counters, and six `chown`s.", "Our rules: no hardware changes, no kernel patches, no root shell. Just a stopwatch, the chips' own counters, and six file permissions."], // 3
  [30.10, 37.04, "Three rounds of measurement cut it into **six stages**, each checked two independent ways. Here is how.", "Three rounds of measurement cut it into six stages, each checked two independent ways. Here is how, and the traps along the way."], // 4
];
const DUR = 38.44; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const g = rpGeom(1, 0, 10);
/** Preview of the verdict (means, ns): core, core⇄CHA mesh, CHA⇄M2PCIe mesh, IRP+IIO+root port, link path, card L3. */
const SIX = [
  { v: 10.7, color: D.core },
  { v: 11.8, color: D.mesh },
  { v: 57.4, color: D.mesh },
  { v: 125.2, color: D.io },
  { v: 312.1, color: D.link },
  { v: 105.2, color: D.card },
];
const BAR = { x: 160, y: 640, w: 1600, max: 622.4 };

function Body() {
  const t = useT();
  // c0: one round trip core → card memory → core, with a nanosecond stopwatch
  const trip = lin(t, T[0] + 0.7, 3.4);
  const ns = Math.round(627.3 * trip);
  const showStat = win(t, T[0] + 0.3, T[1]);
  // c2: the blocks in between appear, then one black box swallows them
  const between: NodeId[] = ["cha", "m2p", "irp", "iio", "rp", "pem", "sdp", "iobn", "ddr"];
  const hide: Partial<Record<NodeId, number>> = {};
  between.forEach((id, i) => (hide[id] = 1 - prog(t, T[2] + 0.15 + i * 0.12, 0.4)));
  const boxK = prog(t, T[2] + 1.9, 0.6) * (1 - prog(t, T[4] + 0.2, 0.7));
  // c4: six stages light up
  const six = prog(t, T[4] + 0.5, 0.6);
  const hl: Partial<Record<NodeId, number>> = {};
  NODES.forEach((id) => (hl[id] = id === "core" || id === "tad" ? Math.max(0.6 * (1 - prog(t, T[2], 0.5)), six) : six));
  const lay = barLayout(BAR.x, BAR.w, BAR.max, SIX);
  // badge x positions: segment centres, pushed right so neighbours keep ≥ 56 px apart
  const badgeX: number[] = [];
  lay.forEach((r, i) => badgeX.push(Math.max((r.x0 + r.x1) / 2, i ? badgeX[i - 1] + 56 : 0)));
  // c1 bars: 0..700 ns over 860 px
  const sx = (v: number) => 640 + (v / 700) * 860;
  const barsK = win(t, T[1] + 0.2, T[2] + 0.3);
  const cxlK = prog(t, T[1] + 0.4, 0.6);
  const ourK = prog(t, T[1] + 1.4, 0.7);
  const rules = win(t, T[3] + 0.2, T[4] + 0.1);
  return (
    <>
      <Stage>
        <MmDefs />
        <ReadPath
          g={g}
          hide={hide}
          hl={hl}
          bands={0}
          heads={prog(t, T[0], 0.6)}
          link={1 - hide.rp!}
          boxes={[{ from: "cha", to: "ddr", tall: true, k: boxK, label: "?" }]}
          under={
            trip > 0 && trip < 1 ? <Cell pts={tripRoute(g, "tad")} k={trip} color={P.gold} w={30} h={20} /> : null
          }
        />
        {/* c1: quoted CXL.mem vs this read */}
        <g opacity={barsK}>
          <Tx x={600} y={600} anchor="end" size={26}>
            CXL.mem read
          </Tx>
          <Tx x={600} y={632} anchor="end" size={18} color={P.sub}>
            industry figure, not measured here
          </Tx>
          <rect x={sx(0)} y={592} width={(sx(170) - sx(0)) * cxlK} height={34} rx={8} fill={alpha(D.mesh, 0.16)} stroke={alpha(D.mesh, 0.6)} strokeWidth={1.5} strokeDasharray="7 6" />
          <rect x={sx(170)} y={592} width={(sx(250) - sx(170)) * cxlK} height={34} rx={8} fill="url(#mmHatch)" stroke={alpha(D.mesh, 0.6)} strokeWidth={1.5} strokeDasharray="7 6" />
          <Tx x={sx(250) + 20} y={609} size={28} weight={700} color={D.mesh} opacity={clamp01((cxlK - 0.6) / 0.4)}>
            ~170–250 ns
          </Tx>
          <Tx x={600} y={704} anchor="end" size={26}>
            This PCIe read
          </Tx>
          <Tx x={600} y={736} anchor="end" size={18} color={P.sub}>
            measured, median of 300,000
          </Tx>
          <rect x={sx(0)} y={696} width={(sx(627.3) - sx(0)) * ourK} height={34} rx={8} fill={P.gold} opacity={0.92} />
          <Tx x={sx(0) + (sx(627.3) - sx(0)) * ourK + 20} y={713} size={28} weight={800} color={P.gold} opacity={clamp01((ourK - 0.6) / 0.4)}>
            627 ns
          </Tx>
          <line x1={sx(0)} y1={572} x2={sx(0)} y2={752} stroke={P.hair2} strokeWidth={1.5} />
        </g>
        {/* c3: the rules */}
        <g opacity={rules}>
          <rect x={300} y={560} width={600} height={260} rx={18} fill={P.panel} stroke={P.panelLine} />
          <rect x={1020} y={560} width={600} height={260} rx={18} fill={P.panel} stroke={P.panelLine} />
          <Tx x={336} y={598} size={16} weight={600} color={P.bad} spacing={2.6}>
            NOT USED
          </Tx>
          <Tx x={1056} y={598} size={16} weight={600} color={P.push} spacing={2.6}>
            ALL WE USED
          </Tx>
          {["hardware changes", "kernel patches", "a root shell"].map((s, i) => {
            const k = prog(t, T[3] + 0.4 + i * 0.35, 0.4);
            return (
              <g key={s} opacity={k}>
                <Cross x={352} y={650 + i * 54} />
                <Tx x={384} y={650 + i * 54} size={28}>
                  {s}
                </Tx>
              </g>
            );
          })}
          {["a stopwatch for one load", "the chips' own counters", "six chown permissions"].map((s, i) => {
            const k = prog(t, T[3] + 3.2 + i * 0.45, 0.4);
            return (
              <g key={s} opacity={k}>
                <Check x={1072} y={650 + i * 54} />
                <Tx x={1104} y={650 + i * 54} size={28}>
                  {s}
                </Tx>
              </g>
            );
          })}
        </g>
        {/* c4: six stages, two proofs each */}
        <g opacity={six}>
          <TimeBar x={BAR.x} y={BAR.y} w={BAR.w} max={BAR.max} segs={SIX} grow={prog(t, T[4] + 0.6, 1.4)} />
          {lay.map((r, i) => {
            const k = prog(t, T[4] + 0.9 + i * 0.22, 0.4);
            const bx = badgeX[i];
            const mx = (r.x0 + r.x1) / 2;
            return (
              <g key={i} opacity={k}>
                <path d={`M${bx} ${BAR.y - 26} L${mx} ${BAR.y - 4}`} stroke={P.hair2} strokeWidth={1.5} fill="none" />
                <circle cx={bx} cy={BAR.y - 46} r={19} fill={P.bg0} stroke={SIX[i].color} strokeWidth={2} />
                <Tx x={bx} y={BAR.y - 45} anchor="middle" size={19} weight={700} color={SIX[i].color}>
                  {i + 1}
                </Tx>
                <Check x={bx - 8} y={BAR.y + 76} s={0.7} color={P.push} k={prog(t, T[4] + 2.6 + i * 0.15, 0.3)} />
                <Check x={bx + 9} y={BAR.y + 76} s={0.7} color={P.push} k={prog(t, T[4] + 2.8 + i * 0.15, 0.3)} />
              </g>
            );
          })}
          <Badge x={960} y={BAR.y + 150} text="six stages · each measured two ways" color={P.gold} k={prog(t, T[4] + 3.4, 0.5)} size={22} />
        </g>
      </Stage>
      <BigStat
        x={960}
        y={560}
        align="center"
        w={900}
        value={ns.toLocaleString("en-US")}
        unit="ns"
        size={132}
        color={P.gold}
        label="one 8-byte uncached load: core → card memory → core"
        opacity={showStat}
      />
    </>
  );
}

export const M01: SceneDef = {
  id: "M01",
  title: "627 nanoseconds",
  stage: 0,
  source: "Xeon 6438M host ⇄ Marvell CN106 card · PCIe Gen5 ×4",
  dur: DUR,
  cues,
  C: Body,
};
