import React from "react";
import { Cue, SceneDef, clamp01, lerp, lin, prog, useT } from "../lib";
import { BigStat, P, Stage, alpha, win } from "../ccnic/kit";
import { Badge, Check, Cross, D, Dim, MmDefs, T as Tx } from "./mk";

// <cues>
const cues: Cue[] = [
  [0.60, 12.24, "Trap one: the IIO clock first read **0.96–0.99 GHz**, lower\u00a0in\u00a0short\u00a0runs\u00a0than\u00a0in\u00a0long\u00a0ones. Real clocks don't do that.", "Trap one. The clock shared by I R P and I I O first read zero point nine six to zero point nine nine gigahertz, lower in short runs than in long ones. Real clocks don't do that."], // 0
  [12.59, 20.83, "After each run, the harness read 65 other counters first: **260–500 µs** with no reads in flight. The idle clock gated down.", "The culprit was our harness: after each run it read sixty-five other counters first, with no reads in flight, and the idle clock gated down."], // 1
  [21.18, 28.08, "Count cycles inside the timed loop only, and the clock\u00a0is\u00a0flat: **1.00 GHz** at every endpoint and every sweep point.", "Count only the cycles inside the timed loop, and the clock is flat: one point zero zero gigahertz, everywhere."], // 2
  [28.43, 36.93, "Trap two: the 0.91 ns grid. A grid-snapped median floor, taken from averaged reads, tilted the core fit by **~0.9 ns**.", "Trap two is the grid. We subtracted a grid-snapped median from averaged reads, and across core clocks that tilted the fit by almost a nanosecond."], // 3
  [37.28, 45.92, "Subtract a mean from a mean, and\u00a0the two\u00a0sweeps\u00a0agree\u00a0even\u00a0better: worst mismatch **1.16 → 0.73 ns**.", "Subtract a mean from a mean instead, and the worst mismatch between the sweeps drops from one point one six to zero point seven three nanoseconds."], // 4
];
const DUR = 47.32; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---- part A: measurement windows (D1), schematic lengths; each window ends in the harness tail
const ROWS = [
  { ms: "250 ms", ghz: "0.999 GHz", w: 1000, y: 230 },
  { ms: "21.6 ms", ghz: "0.988 GHz", w: 380, y: 320 },
  { ms: "9.3 ms", ghz: "0.962 GHz", w: 200, y: 410 },
];
const BX = 400;
const BH = 40;
const TAIL = 60;
/** the zoomed window: timed loop x0..x1, harness tail x1..x2 */
const TL = { x0: 300, x1: 1240, x2: 1620, y: 630, h: 60 };
// ---- part B: floor p50 − floor mean per core clock (D2), ns
const GRID = [
  { f: "1.5 GHz", v: -0.18, s: "−0.18" },
  { f: "2.2 GHz", v: 0, s: "0.00" },
  { f: "3.0 GHz", v: 0.36, s: "+0.36" },
  { f: "3.9 GHz", v: 0.42, s: "+0.42" },
];
const CH = { x0: 400, dx: 200, bw: 84, y0: 560, sc: 400 };
/** least-squares line through the four bars (per bar index) */
const TILT = { a: -0.174, b: 0.216 };
const HIST = [0, 1, 3, 5, 4, 2, 1, 0, 1, 0, 0];

function Body() {
  const t = useT();
  const partA = 1 - prog(t, T[3], 0.5);
  const partB = prog(t, T[3] + 0.5, 0.5);
  // c0: three windows, three clocks
  const rowsK = 1 - prog(t, T[2], 0.5);
  const qK = win(t, T[0] + 5.6, T[1]);
  // c1: the harness tail, then zoom into the 9.3 ms window
  const tailK = prog(t, T[1] + 0.4, 0.6);
  const zoomK = prog(t, T[1] + 1.2, 0.7);
  const guideK = win(t, T[1] + 1.0, T[1] + 3.6);
  const scan = lin(t, T[1] + 1.6, 3.0);
  const loopLab = prog(t, T[1] + 2.4, 0.5);
  const tailLab = prog(t, T[1] + 4.2, 0.5);
  const avgK = prog(t, T[1] + 5.4, 0.6);
  // c2: count inside the loop only
  const m = prog(t, T[2] + 0.6, 0.8);
  const bigK = prog(t, T[2] + 1.0, 0.6);
  // c3: the grid tilts the fit
  const barK = (i: number) => prog(t, T[3] + 1.2 + i * 0.45, 0.5);
  const tiltK = lin(t, T[3] + 3.6, 1.0);
  const noteK = win(t, T[3] + 4.6, T[4]);
  const gridK = win(t, T[3] + 0.9, T[4]);
  // c4: mean minus mean
  const col = prog(t, T[4] + 0.4, 1.2);
  const fixK = prog(t, T[4] + 1.2, 0.6);
  const oldK = prog(t, T[4] + 2.0, 0.6);
  const newK = prog(t, T[4] + 4.0, 0.6);
  const ty = (i: number) => CH.y0 - (TILT.a + TILT.b * i) * CH.sc * (1 - col);
  const tiltEnd = lerp(-0.3, 3.3, tiltK);
  const playX = lerp(TL.x0, TL.x2, scan);
  const winX1 = lerp(TL.x2, TL.x1, m);
  return (
    <>
      <Stage>
        <MmDefs />
        <defs>
          <pattern id="m7Dense" width="7" height="10" patternUnits="userSpaceOnUse">
            <line x1="2" y1="0" x2="2" y2="10" stroke={D.io} strokeWidth="2" />
          </pattern>
          <pattern id="m7Sparse" width="20" height="10" patternUnits="userSpaceOnUse">
            <line x1="2" y1="0" x2="2" y2="10" stroke={P.faint} strokeWidth="2" />
          </pattern>
          <pattern id="m7DenseBig" width="10" height="10" patternUnits="userSpaceOnUse">
            <line x1="3" y1="0" x2="3" y2="10" stroke={D.io} strokeWidth="3" />
          </pattern>
          <pattern id="m7SparseBig" width="76" height="10" patternUnits="userSpaceOnUse">
            <line x1="30" y1="0" x2="30" y2="10" stroke={P.faint} strokeWidth="3" />
          </pattern>
          <clipPath id="m7Scan">
            <rect x={TL.x0 - 2} y={TL.y - 4} width={Math.max(0, playX - TL.x0 + 2)} height={TL.h + 8} />
          </clipPath>
        </defs>
        {/* ---------------- part A */}
        <g opacity={partA}>
          {/* c0: the three windows */}
          <g opacity={rowsK}>
            <Tx x={BX} y={182} size={18} color={P.faint} spacing={2} opacity={prog(t, T[0] + 0.5, 0.5)}>
              SCHEMATIC
            </Tx>
            {ROWS.map((r, i) => {
              const k = prog(t, T[0] + 0.6 + i * 0.8, 0.7);
              const x1 = BX + r.w;
              return (
                <g key={r.ms} opacity={clamp01(k * 3)}>
                  <Tx x={BX - 24} y={r.y} anchor="end" size={26}>
                    {r.ms}
                  </Tx>
                  <rect x={BX} y={r.y - BH / 2} width={r.w * k} height={BH} rx={6} fill={alpha(D.io, 0.1)} stroke={alpha(D.io, 0.5)} strokeWidth={1.5} />
                  <rect x={BX + 3} y={r.y - BH / 2 + 4} width={Math.max(0, (r.w - TAIL * tailK) * k - 6)} height={BH - 8} fill="url(#m7Dense)" />
                  <rect x={x1 - TAIL} y={r.y - BH / 2 + 4} width={TAIL - 3} height={BH - 8} fill="url(#m7Sparse)" opacity={tailK} />
                  <rect x={x1 - TAIL} y={r.y - BH / 2} width={TAIL} height={BH} rx={6} fill="none" stroke={alpha(P.sub, 0.8)} strokeWidth={1.5} strokeDasharray="4 4" opacity={tailK} />
                  <Tx x={1440} y={r.y} size={32} weight={700} color={D.io} opacity={clamp01((k - 0.6) / 0.4)}>
                    → {r.ghz}
                  </Tx>
                </g>
              );
            })}
            <Tx x={1790} y={320} anchor="middle" size={110} weight={800} color={P.bad} opacity={qK}>
              ?
            </Tx>
          </g>
          {/* c1: zoom into the 9.3 ms window */}
          <g opacity={guideK}>
            <line x1={BX} y1={430} x2={TL.x0} y2={TL.y} stroke={P.hair2} strokeWidth={1.5} strokeDasharray="5 5" />
            <line x1={BX + 200} y1={430} x2={TL.x2} y2={TL.y} stroke={P.hair2} strokeWidth={1.5} strokeDasharray="5 5" />
          </g>
          <g opacity={zoomK}>
            <Tx x={TL.x0} y={TL.y + TL.h + 34} size={18} color={P.faint} spacing={2}>
              SCHEMATIC
            </Tx>
            <rect x={TL.x0} y={TL.y} width={TL.x2 - TL.x0} height={TL.h} rx={8} fill={D.node} stroke={P.hair2} strokeWidth={1.5} />
            <g clipPath="url(#m7Scan)">
              <rect x={TL.x0 + 4} y={TL.y + 6} width={TL.x1 - TL.x0 - 8} height={TL.h - 12} fill="url(#m7DenseBig)" />
              <rect x={TL.x1 + 4} y={TL.y + 6} width={TL.x2 - TL.x1 - 8} height={TL.h - 12} fill="url(#m7SparseBig)" opacity={1 - 0.6 * m} />
            </g>
            <rect x={TL.x1} y={TL.y} width={TL.x2 - TL.x1} height={TL.h} rx={8} fill="none" stroke={alpha(P.sub, 0.8)} strokeWidth={1.5} strokeDasharray="4 4" opacity={tailLab} />
            {scan > 0 && scan < 1 && <line x1={playX} y1={TL.y - 10} x2={playX} y2={TL.y + TL.h + 10} stroke={P.gold} strokeWidth={2.5} />}
            {/* loop */}
            <Tx x={640} y={TL.y - 30} anchor="middle" size={22} color={D.io} weight={600} opacity={loopLab}>
              timed loop · IIO busy
            </Tx>
            <Badge x={900} y={TL.y - 30} text="1.00 GHz" color={D.io} k={loopLab} />
            {/* tail */}
            <g opacity={tailLab * (1 - 0.55 * m)}>
              <Tx x={TL.x1 + (TL.x2 - TL.x1) / 2} y={TL.y - 30} anchor="middle" size={22} color={P.sub} weight={600}>
                harness reads 65 counters
              </Tx>
              <Badge x={TL.x1 + (TL.x2 - TL.x1) / 2} y={TL.y - 74} text="~0.03 GHz" color={P.faint} />
              <Dim x0={TL.x1} x1={TL.x2} y={TL.y + TL.h + 28} dir={1} label="260–500 µs" color={P.sub} size={22} weight={600} />
            </g>
            <Cross x={TL.x1 + (TL.x2 - TL.x1) / 2} y={TL.y + TL.h / 2} k={prog(t, T[2] + 1.2, 0.4)} s={1.5} />
            {/* averaging window → count window */}
            <Dim x0={TL.x0} x1={winX1} y={TL.y + TL.h + 110} dir={1} label={m > 0.5 ? "count window: loop only" : "averaging window"} color={m > 0.5 ? P.push : P.sub} size={22} weight={600} k={avgK} />
          </g>
          {/* c2: the checks under the big number */}
          {["every endpoint", "every sweep point"].map((s, j) => {
            const k = prog(t, T[2] + 2.6 + j * 0.5, 0.4);
            return (
              <g key={s} opacity={k}>
                <Check x={660 + j * 330} y={420} />
                <Tx x={688 + j * 330} y={420} size={26}>
                  {s}
                </Tx>
              </g>
            );
          })}
        </g>
        {/* ---------------- part B: the 0.91 ns grid */}
        <g opacity={partB}>
          {/* tiny grid reminder */}
          <g opacity={gridK}>
            <line x1={330} y1={240} x2={830} y2={240} stroke={P.hair2} strokeWidth={2} />
            {HIST.map((nn, i) => (
              <g key={i}>
                <line x1={330 + i * 50} y1={240} x2={330 + i * 50} y2={250} stroke={P.sub} strokeWidth={2} />
                {Array.from({ length: nn }).map((_, j) => (
                  <circle key={j} cx={330 + i * 50} cy={228 - j * 13} r={5} fill={D.core} />
                ))}
              </g>
            ))}
            <Dim x0={480} x1={530} y={268} dir={1} label="0.91 ns grid" color={P.gold} size={20} weight={600} />
            <Tx x={860} y={228} size={18} color={P.faint} spacing={2}>
              SCHEMATIC
            </Tx>
          </g>
          <Tx x={320} y={360} size={20} color={P.sub}>
            floor p50 − floor mean (ns)
          </Tx>
          <line x1={320} y1={CH.y0} x2={1080} y2={CH.y0} stroke={P.hair2} strokeWidth={2} />
          <Tx x={304} y={CH.y0} anchor="end" size={18} color={P.sub}>
            0
          </Tx>
          {GRID.map((b, i) => {
            const k = barK(i);
            const x = CH.x0 + i * CH.dx;
            const h = b.v * CH.sc * k * (1 - col);
            const lab = clamp01((k - 0.5) * 2) * (1 - col);
            return (
              <g key={b.f}>
                <rect x={x - CH.bw / 2} y={Math.min(CH.y0, CH.y0 - h)} width={CH.bw} height={Math.abs(h)} rx={4} fill={alpha(P.bad, 0.8)} opacity={k} />
                {b.v === 0 && <line x1={x - CH.bw / 2} y1={CH.y0} x2={x + CH.bw / 2} y2={CH.y0} stroke={P.bad} strokeWidth={4} opacity={k * (1 - col)} />}
                <Tx x={x} y={b.v > 0 ? CH.y0 + 28 : b.v < 0 ? CH.y0 - 28 : CH.y0 + 28} anchor="middle" size={24} weight={700} color={P.bad} opacity={lab}>
                  {b.s}
                </Tx>
                <Tx x={x} y={700} anchor="middle" size={20} color={P.sub} opacity={k}>
                  {b.f}
                </Tx>
              </g>
            );
          })}
          <Tx x={700} y={740} anchor="middle" size={20} color={P.sub}>
            core clock
          </Tx>
          {tiltK > 0 && (
            <line
              x1={CH.x0 - 0.3 * CH.dx}
              y1={ty(-0.3)}
              x2={CH.x0 + tiltEnd * CH.dx}
              y2={ty(tiltEnd)}
              stroke={fixK > 0.5 ? P.push : P.bad}
              strokeWidth={3}
              strokeDasharray="10 7"
              strokeLinecap="round"
            />
          )}
          <Tx x={1110} y={ty(3.3)} size={30} weight={700} color={P.bad} opacity={noteK}>
            tilts the core fit ~0.9 ns
          </Tx>
          <Tx x={330} y={600} size={24} weight={600} color={P.push} opacity={fixK}>
            mean − mean
          </Tx>
        </g>
      </Stage>
      <BigStat x={960} y={170} align="center" w={800} value="1.00" unit="GHz" size={96} color={P.gold} label="in-loop cycles ÷ wall time" opacity={bigK * partA} />
      <BigStat
        x={1140}
        y={420}
        w={760}
        value={
          <>
            <span style={{ color: newK > 0.5 ? P.sub : P.ink, opacity: 1 - 0.45 * newK, textDecoration: newK > 0.5 ? "line-through" : "none" }}>1.16</span>
            <span style={{ opacity: newK }}>
              {" → "}
              <span style={{ color: P.push }}>0.73</span>
            </span>
          </>
        }
        unit="ns"
        size={88}
        color={P.push}
        label="worst mismatch between the two sweeps"
        opacity={oldK * partB}
      />
    </>
  );
}

export const M07: SceneDef = {
  id: "M07",
  title: "Traps: the clock and the grid",
  stage: 3,
  source: "IO clock from in-loop cycles \u00f7 wall time \u00b7 floor_mean basis",
  dur: DUR,
  cues,
  C: Body,
};
