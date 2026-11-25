import React from "react";
import { Cue, SceneDef, clamp01, lerp, lin, prog, useT } from "../lib";
import { BigStat, P, Stage, alpha, win } from "../ccnic/kit";
import { Axes, Badge, Check, D, Dim, MmDefs, NodeId, PlotGeom, ReadPath, T as Tx, TimeBar, barLayout, plotGeom, rcx, rpGeom } from "./mk";

// <cues>
const cues: Cue[] = [
  [0.60, 8.52, "Third trick: change one clock and watch what stretches. A stage on that clock takes **cycles ÷ frequency**.", "Third trick: change one clock and watch what stretches. A stage on that clock takes a fixed number of cycles, divided by the frequency."], // 0
  [8.87, 17.99, "Sweep the mesh, 0.8 to 2.2 GHz. The CHA and M2PCIe₁\u00a0follow. IRP₁ and IIO₁ don't: they run on their own **1 GHz** clock.", "Sweep the mesh, and the C H A and M two P C I E follow it. I R P and I I O do not: they run on their own one gigahertz clock."], // 1
  [18.34, 28.72, "TOR time is a straight line in 1/f. Slope: **56.2–58.6 ns**\u00a0of\u00a0mesh. Intercept: **125.2 ns** for IRP₁, IIO₁ and the root port.", "Plot it against one over f, and it is a straight line: fifty-six to fifty-nine nanoseconds of mesh, plus a fixed hundred twenty-five for I R P, I I O and the root port."], // 2
  [29.07, 38.59, "Then sweep the core, 1.5 to 3.9 GHz. Its own share outside the TOR: **41–43 cycles**, or **10.5–10.9 ns**.", "Then sweep the core, from one point five to three point nine gigahertz. Its own share is forty-one to forty-three cycles: ten and a half nanoseconds."], // 3
  [38.94, 49.54, "Cross-check: what one sweep calls fixed, the other\u00a0sees\u00a0as\u00a0slope. Core-to-CHA mesh: **11.2–12.8 ns**. Mismatch: **≤ 0.73 ns**.", "Cross-check: what one sweep calls fixed, the other must see as slope. The core to C H A mesh is eleven to thirteen nanoseconds, and they agree within zero point seven three."], // 4
  [49.89, 58.13, "Two clocks, two sweeps, each stage measured twice. The host side is now four clean pieces.", "Two clocks, two sweeps, every stage measured twice: the host side is four clean pieces. But getting here meant dodging five traps."], // 5
];
const DUR = 59.53; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const g = rpGeom(0.78, 0, -58);
// C7 mesh sweep (TOR mean, ns) and C8 core sweep (root port, net − TOR, ns)
const MESH_F = [0.8, 1.1, 1.4, 1.8, 2.2];
const RP_TOR = [286.69, 242.03, 217.39, 196.25, 184.46];
const REG_TOR = [593.63, 550.16, 527.38, 507.35, 494.99];
const CORE_F = [1.5, 2.2, 3.0, 3.9];
const RP_OUT = [38.52, 29.65, 25.39, 21.32];
const pM = plotGeom(330, 460, 940, 350, [0, 1.3], [0, 650]);
const pC = plotGeom(330, 460, 940, 350, [0, 0.8], [0, 45]);
const CALL_X = 1300;
// c1 sliders
const SL = { x0: 640, x1: 1440, yMesh: 560, yIo: 720 };
const slx = (v: number) => SL.x0 + ((v - 0.8) / 1.4) * (SL.x1 - SL.x0);
// c5 host side (midpoints of the ranges)
const HOST = [
  { v: 10.7, color: D.core, label: "core", nodes: ["core"] as NodeId[] },
  { v: 11.8, color: D.mesh, label: "mesh", nodes: ["cha"] as NodeId[] },
  { v: 57.4, color: D.mesh, label: "mesh", nodes: ["m2p"] as NodeId[] },
  { v: 125.2, color: D.io, label: "IRP · IIO · root port", nodes: ["irp", "iio", "rp"] as NodeId[] },
];
const BAR = { x: 260, y: 670, w: 1400, max: 205.1 };

/** Fit a + b·x: solid over the data (xa..xb) drawn by k, dashed back to x = 0 by `back`. */
function Fit({ p, a, b, xa, xb, k, back, color }: { p: PlotGeom; a: number; b: number; xa: number; xb: number; k: number; back: number; color: string }) {
  const y = (x: number) => p.sy(a + b * x);
  const xe = lerp(xa, xb, clamp01(k));
  const x0 = lerp(xa, 0, clamp01(back));
  return (
    <g>
      {k > 0 && <line x1={p.sx(xa)} y1={y(xa)} x2={p.sx(xe)} y2={y(xe)} stroke={color} strokeWidth={3} strokeLinecap="round" />}
      {back > 0 && <line x1={p.sx(xa)} y1={y(xa)} x2={p.sx(x0)} y2={y(x0)} stroke={color} strokeWidth={2} strokeDasharray="8 7" opacity={0.85} />}
    </g>
  );
}

/** Vertical dimension bracket at x from y0 to y1. */
function VDim({ x, y0, y1, color, k }: { x: number; y0: number; y1: number; color: string; k: number }) {
  if (k <= 0) return null;
  const ym = (y0 + y1) / 2;
  const half = ((y1 - y0) / 2) * clamp01(k * 1.25);
  return (
    <g opacity={clamp01(k * 2)} stroke={color} strokeWidth={2.5} strokeLinecap="round">
      <line x1={x} y1={ym - half} x2={x} y2={ym + half} />
      <line x1={x - 8} y1={ym - half} x2={x + 8} y2={ym - half} />
      <line x1={x - 8} y1={ym + half} x2={x + 8} y2={ym + half} />
    </g>
  );
}

function Dot({ x, y, color, k }: { x: number; y: number; color: string; k: number }) {
  if (k <= 0) return null;
  return <circle cx={x} cy={y} r={7 * (0.6 + 0.4 * k)} fill={color} stroke={P.bg0} strokeWidth={2} opacity={k} />;
}

function Body() {
  const t = useT();
  // ---------------- c0: t = t_fixed + N / f
  const c0 = win(t, T[0] + 0.2, T[1]);
  const fK = prog(t, T[0] + 0.4, 0.7);
  const glossK = prog(t, T[0] + 1.4, 0.6);
  const nfK = prog(t, T[0] + 2.2, 0.5);
  const tick0 = T[0] + 3.0;
  // ---------------- c1: mesh dial sweeps, IO clock stays
  const c1 = win(t, T[1] + 0.5, T[2]);
  const s0 = T[1] + 1.8;
  const pos = [0, 1, 2, 3].reduce((a, i) => a + prog(t, s0 + i * 1.5, 0.7), 0);
  const pi = Math.floor(Math.min(pos, 3.999));
  const knobV = lerp(MESH_F[pi], MESH_F[pi + 1], pos - pi);
  const fNow = MESH_F[Math.round(pos)];
  const moving = [0, 1, 2, 3].reduce((a, i) => Math.max(a, win(t, s0 + i * 1.5 - 0.1, s0 + i * 1.5 + 0.7, 0.25)), 0);
  const badgeK = win(t, T[1] + 0.9, T[2]);
  const ioBadgeK = win(t, T[1] + 1.3, T[2]);
  // ---------------- c2: TOR time vs 1/f_mesh
  const c2 = win(t, T[2] + 0.5, T[3]);
  const ptM = (i: number) => prog(t, T[2] + 0.9 + i * 0.3, 0.35);
  const lineM = lin(t, T[2] + 2.6, 0.9);
  const backM = lin(t, T[2] + 3.5, 0.8);
  const slopeK = prog(t, T[2] + 4.4, 0.7);
  const icK = prog(t, T[2] + 6.6, 0.7);
  // ---------------- c3: outside-TOR time vs 1/f_core
  const c3 = win(t, T[3] + 0.5, T[4]);
  const ptC = (i: number) => prog(t, T[3] + 1.0 + i * 0.3, 0.35);
  const lineC = lin(t, T[3] + 2.5, 0.8);
  const backC = lin(t, T[3] + 3.3, 0.7);
  const coreK = prog(t, T[3] + 4.2, 0.7);
  // ---------------- c4: cross-check
  const c4 = win(t, T[4] + 0.5, T[5]);
  const row1 = prog(t, T[4] + 0.9, 0.5);
  const row2 = prog(t, T[4] + 3.4, 0.5);
  const agreeK = prog(t, T[4] + 6.6, 0.6);
  // ---------------- c5: host side, four pieces
  const c5 = prog(t, T[5] + 0.4, 0.5);
  const grow = prog(t, T[5] + 0.6, 1.4);
  const lay = barLayout(BAR.x, BAR.w, BAR.max, HOST);
  // read-path highlights per cue
  const hl: Partial<Record<NodeId, number>> = {};
  const up = (ids: NodeId[], v: number) => ids.forEach((id) => (hl[id] = Math.max(hl[id] ?? 0, v)));
  up(["cha", "m2p"], c1 * (0.35 + 0.65 * moving));
  up(["cha", "m2p"], c2 * slopeK);
  up(["irp", "iio", "rp"], c2 * icK);
  up(["core"], c3 * prog(t, T[3] + 0.6, 0.5));
  up(["core"], c4 * row1);
  up(["cha"], c4 * row2);
  HOST.forEach((h, i) => up(h.nodes, c5 * prog(t, T[5] + 0.8 + i * 0.35, 0.4)));
  const n = g.n;
  const bY = n.cha.y - 34;
  // c0 tick rows: x is time; the slow clock ticks half as often
  const ticks = (y: number, dx: number, dt: number, color: string, label: string) => {
    const N = 8;
    const x0 = 640;
    const kEnd = prog(t, tick0 + N * dt, 0.3);
    return (
      <g>
        <Tx x={600} y={y} anchor="end" size={24} color={color}>
          {label}
        </Tx>
        <line x1={x0} y1={y} x2={x0 + N * dx * lin(t, tick0, N * dt)} y2={y} stroke={alpha(color, 0.5)} strokeWidth={2} />
        {Array.from({ length: N + 1 }).map((_, j) => {
          const k = prog(t, tick0 + j * dt, 0.15);
          return <line key={j} x1={x0 + j * dx} y1={y - 16} x2={x0 + j * dx} y2={y + 16} stroke={color} strokeWidth={3} opacity={k} />;
        })}
        <Dim x0={x0} x1={x0 + N * dx} y={y + 36} dir={1} label="N cycles" color={color} k={kEnd} size={20} weight={600} />
      </g>
    );
  };
  return (
    <>
      <Stage>
        <MmDefs />
        <ReadPath g={g} bands={1} hl={hl} />
        {/* c1 badges on the nodes */}
        <Badge x={rcx(n.cha)} y={bY} text={`${fNow.toFixed(2)} GHz`} color={D.mesh} k={badgeK} solid={moving > 0.5} />
        <Badge x={rcx(n.m2p)} y={bY} text={`${fNow.toFixed(2)} GHz`} color={D.mesh} k={badgeK} solid={moving > 0.5} />
        <Badge x={rcx(n.irp)} y={bY} text="1.00 GHz" color={D.io} k={ioBadgeK} />
        <g opacity={ioBadgeK}>
          <line x1={rcx(n.iio)} y1={bY - 22} x2={rcx(n.iio)} y2={n.iio.y - 2} stroke={alpha(D.io, 0.6)} strokeWidth={1.5} />
        </g>
        <Badge x={rcx(n.iio)} y={bY - 40} text="1.00 GHz" color={D.io} k={ioBadgeK} />
        {/* ---------------- c0 */}
        <g opacity={c0}>
          <Tx x={960} y={500} anchor="middle" size={48} mono weight={600} opacity={fK}>
            t = t<tspan fontSize={28} dy={12}>fixed</tspan>
            <tspan dy={-12}> + </tspan>
            <tspan fill={nfK > 0.5 ? P.gold : P.ink}>N / f</tspan>
          </Tx>
          <rect x={1028} y={466} width={174} height={68} rx={12} fill="none" stroke={alpha(P.gold, 0.8)} strokeWidth={2} opacity={nfK} />
          <Tx x={960} y={570} anchor="middle" size={24} color={P.sub} opacity={glossK}>
            N cycles on a clock at f
          </Tx>
          <g opacity={prog(t, tick0 - 0.4, 0.4)}>
            {ticks(670, 40, 0.18, D.mesh, "high f")}
            {ticks(790, 80, 0.36, D.core, "low f")}
            <Tx x={1360} y={630} size={18} color={P.faint} spacing={2}>
              SCHEMATIC
            </Tx>
          </g>
        </g>
        {/* ---------------- c1 */}
        <g opacity={c1}>
          {[
            { y: SL.yMesh, label: "mesh clock", sub: "CHA · M2PCIe₁", color: D.mesh, v: knobV, ticks: MESH_F },
            { y: SL.yIo, label: "IO clock", sub: "IRP₁ · IIO₁", color: D.io, v: 1.0, ticks: [1.0] },
          ].map((r) => (
            <g key={r.label}>
              <Tx x={590} y={r.y - 12} anchor="end" size={26} weight={600} color={r.color}>
                {r.label}
              </Tx>
              <Tx x={590} y={r.y + 18} anchor="end" size={18} color={P.sub}>
                {r.sub}
              </Tx>
              <rect x={SL.x0} y={r.y - 4} width={SL.x1 - SL.x0} height={8} rx={4} fill={P.hair2} />
              <rect x={SL.x0} y={r.y - 4} width={Math.max(0, slx(r.v) - SL.x0)} height={8} rx={4} fill={alpha(r.color, 0.7)} />
              {r.ticks.map((v) => (
                <g key={v}>
                  <line x1={slx(v)} y1={r.y + 14} x2={slx(v)} y2={r.y + 24} stroke={P.sub} strokeWidth={1.5} />
                  <Tx x={slx(v)} y={r.y + 42} anchor="middle" size={18} color={r.label === "mesh clock" && Math.abs(v - fNow) < 0.01 ? r.color : P.sub}>
                    {v.toFixed(1)}
                  </Tx>
                </g>
              ))}
              <Tx x={SL.x1 + 24} y={r.y} size={18} color={P.sub}>
                GHz
              </Tx>
              <circle cx={slx(r.v)} cy={r.y} r={17} fill={P.bg0} stroke={r.color} strokeWidth={3} />
              <circle cx={slx(r.v)} cy={r.y} r={7} fill={r.color} />
            </g>
          ))}
          <Tx x={SL.x1 + 24} y={SL.yIo + 36} size={20} color={P.sub} opacity={prog(t, T[1] + 6.0, 0.5)}>
            own clock: does not follow
          </Tx>
        </g>
        {/* ---------------- c2: TOR time vs 1 / f_mesh */}
        <g opacity={c2}>
          <Axes p={pM} xTicks={[0, 0.5, 1]} yTicks={[0, 200, 400, 600]} xFmt={(v) => (v === 0 ? "0" : v.toFixed(1))} xLabel="1 / f_mesh (1/GHz)" yLabel="TOR time (ns)" />
          {/* intercept level */}
          <line x1={pM.sx(0)} y1={pM.sy(125.19)} x2={pM.sx(1 / 2.2)} y2={pM.sy(125.19)} stroke={alpha(D.io, 0.5)} strokeWidth={1.5} strokeDasharray="3 5" opacity={icK} />
          <Fit p={pM} a={438.65} b={123.67} xa={1 / 2.2} xb={1 / 0.8} k={lineM} back={backM} color={D.link} />
          <Fit p={pM} a={125.19} b={128.98} xa={1 / 2.2} xb={1 / 0.8} k={lineM} back={backM} color={D.io} />
          {MESH_F.map((f, i) => (
            <g key={f}>
              <Dot x={pM.sx(1 / f)} y={pM.sy(REG_TOR[i])} color={D.link} k={ptM(i)} />
              <Dot x={pM.sx(1 / f)} y={pM.sy(RP_TOR[i])} color={D.io} k={ptM(i)} />
              <Tx x={pM.sx(1 / f)} y={pM.sy(REG_TOR[i]) - 30} anchor="middle" size={18} color={P.sub} opacity={ptM(i)}>
                {i === 0 ? `${f.toFixed(1)} GHz` : f.toFixed(1)}
              </Tx>
            </g>
          ))}
          <Tx x={pM.sx(1.25) + 26} y={pM.sy(438.65 + 123.67 * 1.25)} size={24} color={D.link} weight={600} opacity={lineM}>
            card register
          </Tx>
          <Tx x={pM.sx(1.25) + 26} y={pM.sy(125.19 + 128.98 * 1.25)} size={24} color={D.io} weight={600} opacity={lineM}>
            root port
          </Tx>
          {/* slope: the mesh share at 2.2 GHz */}
          <VDim x={pM.sx(1 / 2.2)} y0={pM.sy(125.19 + 128.98 / 2.2)} y1={pM.sy(125.19)} color={D.mesh} k={slopeK} />
          <g opacity={clamp01((slopeK - 0.4) / 0.6)}>
            <line x1={pM.sx(1 / 2.2) + 14} y1={pM.sy(125.19 + 64.5 / 2.2)} x2={CALL_X - 12} y2={pM.sy(125.19 + 64.5 / 2.2)} stroke={alpha(D.mesh, 0.5)} strokeWidth={1.5} strokeDasharray="4 5" />
            <Tx x={CALL_X} y={pM.sy(125.19 + 64.5 / 2.2)} size={26} weight={700} color={D.mesh}>
              56.2–58.6 ns @ 2.2 GHz
            </Tx>
            <Tx x={CALL_X} y={pM.sy(125.19 + 64.5 / 2.2) + 30} size={20} color={P.sub}>
              mesh share
            </Tx>
          </g>
          {/* intercept: IRP₁ + IIO₁ + root port */}
          <circle cx={pM.sx(0)} cy={pM.sy(125.19)} r={8} fill={D.io} opacity={icK} />
          <Tx x={pM.sx(0) + 20} y={pM.sy(125.19) + 30} size={26} weight={700} color={D.io} opacity={icK}>
            125.2 ns
          </Tx>
          <Tx x={pM.sx(0) + 140} y={pM.sy(125.19) + 31} size={20} color={P.sub} opacity={icK}>
            IRP₁ + IIO₁ + root port
          </Tx>
        </g>
        {/* ---------------- c3: outside-TOR time vs 1 / f_core */}
        <g opacity={c3}>
          <Axes p={pC} xTicks={[0, 0.2, 0.4, 0.6, 0.8]} yTicks={[0, 10, 20, 30, 40]} xFmt={(v) => (v === 0 ? "0" : v.toFixed(1))} xLabel="1 / f_core (1/GHz)" yLabel="time outside the TOR (ns)" />
          <Fit p={pC} a={11.14} b={41.1} xa={1 / 3.9} xb={1 / 1.5} k={lineC} back={backC} color={D.core} />
          {CORE_F.map((f, i) => (
            <g key={f}>
              <Dot x={pC.sx(1 / f)} y={pC.sy(RP_OUT[i])} color={D.core} k={ptC(i)} />
              <Tx x={pC.sx(1 / f)} y={pC.sy(RP_OUT[i]) - 30} anchor="middle" size={18} color={P.sub} opacity={ptC(i)}>
                {i === 0 ? `${f.toFixed(1)} GHz` : f.toFixed(1)}
              </Tx>
            </g>
          ))}
          <Tx x={pC.sx(1 / 1.5) + 26} y={pC.sy(11.14 + 41.1 / 1.5)} size={24} color={D.core} weight={600} opacity={lineC}>
            root port
          </Tx>
          <VDim x={pC.sx(1 / 3.9)} y0={pC.sy(11.14 + 41.1 / 3.9)} y1={pC.sy(11.14)} color={D.core} k={coreK} />
          <line x1={pC.sx(0)} y1={pC.sy(11.14)} x2={pC.sx(1 / 3.9)} y2={pC.sy(11.14)} stroke={alpha(D.core, 0.5)} strokeWidth={1.5} strokeDasharray="3 5" opacity={coreK} />
          <g opacity={clamp01((coreK - 0.4) / 0.6)}>
            <line x1={pC.sx(1 / 3.9) + 14} y1={pC.sy(16.4)} x2={CALL_X - 12} y2={pC.sy(16.4)} stroke={alpha(D.core, 0.5)} strokeWidth={1.5} strokeDasharray="4 5" />
            <Tx x={CALL_X} y={pC.sy(16.4)} size={26} weight={700} color={D.core}>
              41–43 cycles = 10.5–10.9 ns
            </Tx>
            <Tx x={CALL_X} y={pC.sy(16.4) + 30} size={20} color={P.sub}>
              core share @ 3.9 GHz
            </Tx>
          </g>
        </g>
        {/* ---------------- c4: each stage seen by both sweeps */}
        <g opacity={c4}>
          <Tx x={1000} y={500} anchor="middle" size={22} weight={600} color={D.mesh}>
            mesh sweep
          </Tx>
          <Tx x={1200} y={500} anchor="middle" size={22} weight={600} color={D.core}>
            core sweep
          </Tx>
          {[
            { y: 590, k: row1, ck0: T[4] + 1.4, label: "core", v: "10.5–10.9 ns", color: D.core, how: ["fixed", "slope"] },
            { y: 690, k: row2, ck0: T[4] + 3.9, label: "core ⇄ CHA mesh", v: "11.2–12.8 ns", color: D.mesh, how: ["slope", "fixed"] },
          ].map((r) => (
            <g key={r.label} opacity={r.k}>
              <Tx x={300} y={r.y} size={28} weight={600} color={r.color}>
                {r.label}
              </Tx>
              <Tx x={620} y={r.y} size={34} weight={800} color={r.color}>
                {r.v}
              </Tx>
              {[1000, 1200].map((x, j) => {
                const ck = prog(t, r.ck0 + j * 0.5, 0.35);
                return (
                  <g key={x}>
                    <Check x={x - 24} y={r.y} k={ck} />
                    <Tx x={x - 4} y={r.y + 1} size={20} color={P.sub} opacity={ck}>
                      {r.how[j]}
                    </Tx>
                  </g>
                );
              })}
            </g>
          ))}
          <line x1={300} y1={640} x2={1290} y2={640} stroke={P.hair} strokeWidth={1.5} opacity={row2} />
        </g>
        {/* ---------------- c5: the host side in four pieces */}
        <g opacity={c5}>
          <TimeBar x={BAR.x} y={BAR.y} w={BAR.w} h={50} max={BAR.max} segs={HOST.map((h) => ({ v: h.v, color: h.color, inside: h.v.toFixed(1) }))} grow={grow} size={22} />
          {lay.map((r, i) => {
            const k = prog(t, T[5] + 0.8 + i * 0.35, 0.4);
            const x = (r.x0 + r.x1) / 2;
            return (
              <g key={i} opacity={k}>
                <Tx x={x} y={BAR.y + 82} anchor="middle" size={24} weight={600} color={HOST[i].color}>
                  {HOST[i].label}
                </Tx>
                <Check x={x - 10} y={BAR.y + 124} s={0.7} k={prog(t, T[5] + 3.6 + i * 0.25, 0.3)} />
                <Check x={x + 10} y={BAR.y + 124} s={0.7} k={prog(t, T[5] + 3.8 + i * 0.25, 0.3)} />
              </g>
            );
          })}
          <Dim x0={BAR.x} x1={BAR.x + BAR.w} y={BAR.y - 26} label="205.5 ns" color={P.gold} size={32} k={prog(t, T[5] + 2.3, 0.7)} />
          <Tx x={960} y={BAR.y - 118} anchor="middle" size={20} color={P.sub} opacity={prog(t, T[5] + 2.8, 0.5)}>
            root port, measured
          </Tx>
        </g>
      </Stage>
      <BigStat x={1400} y={540} w={460} value="≤ 0.73" unit="ns" size={96} color={P.gold} label="the two sweeps agree within" opacity={c4 * agreeK} />
    </>
  );
}

export const M06: SceneDef = {
  id: "M06",
  title: "Sweep the clocks",
  stage: 2,
  source: "Mesh 0.8\u20132.2 GHz and core 1.5\u20133.9 GHz sweeps \u00b7 4 endpoints",
  dur: DUR,
  cues,
  C: Body,
};
