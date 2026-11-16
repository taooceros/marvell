import React from "react";
import {
  Axes,
  C,
  Cue,
  FONT,
  K,
  Link,
  Packet,
  Pill,
  Pt,
  SceneDef,
  Series,
  Stage,
  Stat,
  Txt,
  appear,
  clamp01,
  countUp,
  fade,
  lerp,
  lin,
  linScale,
  pop,
  prog,
  useT,
} from "../lib";
import { Datapath, InfTag, dpGeom, routes } from "./pcie";
import { Glow, hex } from "./_ScenePull";

// <cues>
const cues: Cue[] = [
  [0.60, 8.46, "If each DPI copy takes **~0.67 µs**, why not let the **host fetch the reply** with one read?", "If each D P I copy takes about two thirds of a microsecond, why not let the host fetch the reply itself, with a single read?"], // 0
  [8.81, 18.43, "The card stores (tag, timestamp) in its control cell. The host waits **150 ns**, then does one **16 B uncached read**.", "The card stores the tag and timestamp in its control cell. The host waits a hundred and fifty nanoseconds, then does a single sixteen byte uncached read."], // 1
  [18.78, 29.06, "W=1 p50: **0.83 µs** (repeats 0.83 / 0.83 / 1.04). The fastest round trip of all.", "The median round trip is zero point eight three microseconds. Repeats gave point eight three, point eight three, and one point zero four. The fastest of all."], // 2
  [29.41, 43.15, "Wait sweep (p50): 0 ns **0.89** bimodal · 50–100 ns **1.43** · 150 ns **0.83** · 200 **0.88** · 300 **0.98** · 400 **1.09**.", "The wait matters. With no wait, results are bimodal. At fifty to a hundred nanoseconds the first read misses, and a second read costs one point four three. A hundred fifty is the sweet spot; longer waits only add time."], // 3
  [43.50, 54.36, "The price: the host core **stalls 0.63 µs** per read, so pull fits only **W=1**, at **1.17 M msg/s**.", "The price: the host core stalls for point six three microseconds on every read. So pull only fits one message at a time, at about one point two million messages per second."], // 4
];
const DUR = 55.76; // <dur>
// </cues>

// ---------------------------------------------------------------- helpers
/** Countdown dial (timestamps / clocks colour) with a label on its right. */
function Timer({ c, r, k, fs, opacity, children }: { c: Pt; r: number; k: number; fs: number; opacity: number; children: React.ReactNode }) {
  if (opacity <= 0.001) return null;
  const circ = 2 * Math.PI * r;
  const a = 2 * Math.PI * k - Math.PI / 2;
  return (
    <g opacity={opacity}>
      <circle cx={c[0]} cy={c[1]} r={r} fill={C.panel} stroke={C.dim} strokeWidth={3} />
      <circle cx={c[0]} cy={c[1]} r={r} fill="none" stroke={K.timer} strokeWidth={5} strokeDasharray={`${circ * k} ${circ}`} transform={`rotate(-90 ${c[0]} ${c[1]})`} />
      <line x1={c[0]} y1={c[1]} x2={c[0] + Math.cos(a) * r * 0.75} y2={c[1] + Math.sin(a) * r * 0.75} stroke={K.timer} strokeWidth={3} strokeLinecap="round" />
      <text x={c[0] + r + 12} y={c[1]} dominantBaseline="central" fontSize={fs} fontWeight={700} fill={K.timer} fontFamily={FONT}>
        {children}
      </text>
    </g>
  );
}

/** Timeline segment growing left → right with k; label centred inside. */
function Seg({
  x,
  y,
  w,
  h,
  k,
  color,
  label,
  dashed = false,
  hatch = false,
  fs = 24,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  k: number;
  color: string;
  label?: string;
  dashed?: boolean;
  hatch?: boolean;
  fs?: number;
}) {
  if (k <= 0.001) return null;
  return (
    <g opacity={Math.min(1, k * 2)}>
      <rect x={x} y={y} width={w * k} height={h} rx={8} fill={hatch ? "url(#hatchRed)" : `${color}${dashed ? "18" : "40"}`} stroke={color} strokeWidth={2.5} strokeDasharray={dashed ? "9 7" : undefined} />
      {label && (
        <text x={x + w / 2} y={y + h / 2} textAnchor="middle" dominantBaseline="central" fontSize={fs} fontWeight={700} fill={hatch ? "#fff" : C.text} fontFamily={FONT} opacity={clamp01((k - 0.6) / 0.4)} style={hatch ? { paintOrder: "stroke", stroke: "#3a1218", strokeWidth: 6 } : undefined}>
          {label}
        </text>
      )}
    </g>
  );
}

/** Numbered step row (HTML). */
function Step({ x, y, n, color, style, children }: { x: number; y: number; n: number; color: string; style: React.CSSProperties; children: React.ReactNode }) {
  return (
    <div style={{ position: "absolute", left: x, top: y, display: "flex", alignItems: "center", gap: 14, fontSize: 25, color: C.text, whiteSpace: "nowrap", ...style }}>
      <div style={{ width: 40, height: 40, borderRadius: 20, border: `3px solid ${color}`, color, display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, fontSize: 22, flexShrink: 0 }}>{n}</div>
      <div>{children}</div>
    </div>
  );
}

// W=1 p50 by return path (FACTS §SDP, §Inline, §MemPoll, §DMA, §Pull)
const BARS: { label: string; v: number; hi?: number; text: string; color: string }[] = [
  { label: "stock SDP", v: 4.85, text: "4.85 µs", color: K.sdp },
  { label: "inline + SDP", v: 2.41, text: "2.41 µs", color: K.inline },
  { label: "inline + mem poll", v: 2.0, text: "2.00 µs", color: K.mem },
  { label: "inline + DPI DMA", v: 1.33, hi: 1.6, text: "1.33–1.60 µs", color: K.dma },
  { label: "inline + pull", v: 0.83, text: "0.83 µs", color: K.pull },
];

// wait sweep (FACTS §Pull): [ns, p50 µs]
const SWEEP: [number, number][] = [
  [0, 0.89],
  [50, 1.43],
  [100, 1.43],
  [150, 0.83],
  [200, 0.88],
  [300, 0.98],
  [400, 1.09],
];

const PX = 480; // px per µs in the schematic timelines (left-bottom band)
const RD = 0.63 * PX; // one 16 B UC read (FACTS §Budget / §Pull)
const TL_X = 340;

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  // ---- geometry: full size for c0–c1, shrink to the top-left at c2 (morph)
  const m = prog(t, T[2], 0.9);
  const s = lerp(1, 0.62, m);
  const g = dpGeom(s, lerp(0, 18, m), lerp(0, 36, m));
  const r = routes(g);
  const P = g.pt;

  // ---- c0: the DPI copy + the question
  const c0 = fade(t, T[0] + 0.3, T[1]);
  const dmaOn = fade(t, T[0] + 0.3, T[0] + 3.6, 0.8); // DPI path dims as the pull question starts
  const dmaU = (((t - T[0] - 0.5) % 2.2) + 2.2) % 2.2;
  const dmaK = t > T[0] + 0.5 ? lin(dmaU, 0, 1.6) : 0;
  const qK = prog(t, T[0] + 3.8, 1.2);
  const qOp = fade(t, T[0] + 3.6, T[1]);

  // ---- c1: one round trip, step by step
  const c1 = fade(t, T[1], T[2]);
  const s1 = [T[1] + 0.5, T[1] + 1.8, T[1] + 3.8, T[1] + 5.8];
  const inK = prog(t, T[1] + 0.3, 1.3);
  const wrK = prog(t, T[1] + 1.9, 0.8);
  const tmK = lin(t, T[1] + 3.9, 1.4);
  const outK = prog(t, T[1] + 5.8, 1.1);
  const backK = prog(t, T[1] + 7.0, 1.1);

  // ---- c2–c4: ambient round-trip loop on the shrunk drawing
  const L0 = T[2] + 1.0;
  const PER = 4.2;
  const loopOn = prog(t, L0 - 0.2, 0.5);
  const lt = t - L0;
  const n = Math.max(0, Math.floor(lt / PER));
  const u = lt < 0 ? -1 : lt - n * PER;
  const cs = L0 + n * PER;
  const dbl = cs >= T[3] + 3.4 && cs < T[3] + 8.4; // double read while the narration covers 50–100 ns
  const seg = (a: number, d: number) => (u < a ? 0 : lin(u, a, d));
  const gone = (a: number) => 1 - lin(u, a, 0.3);
  const L = dbl
    ? { inl: 0, wr: 1.3, wrD: 0.6, wait: 0.8, waitD: 0.1, out1: 0.9, back1: 1.5, out2: 2.1, back2: 2.7, end: 3.3 }
    : { inl: 0, wr: 0.8, wrD: 0.4, wait: 1.2, waitD: 0.4, out1: 1.6, back1: 2.2, out2: -1, back2: -1, end: 2.8 };
  const inflight = lt < 0 ? 0 : lin(u, L.out1, 0.15) * (1 - lin(u, L.end, 0.15));

  // lit cells
  const cellA = (i: number) => {
    const a1 = i === 0 ? prog(t, T[1] + 1.5, 0.3) * c1 : 0;
    const a2 = i === n % 10 && lt >= 0 ? lin(u, 0.7, 0.2) * (1 - lin(u, 3.7, 0.4)) * loopOn : 0;
    return Math.max(a1, a2);
  };
  const ctrlLit = Math.max(prog(t, T[1] + 2.6, 0.3) * c1, lt >= 0 ? lin(u, L.wr + L.wrD, 0.2) * (1 - lin(u, 3.7, 0.4)) * loopOn : 0);

  // dimming of blocks the pull path does not use
  const d1 = prog(t, T[0] + 3.6, 0.8);
  const show = {
    sdp: lerp(0.6, 0.35, d1),
    sdpRing: lerp(0.6, 0.35, d1),
    dpi: lerp(1, 0.35, d1),
    replyRing: lerp(1, 0.3, d1),
    hmem: lerp(1, 0.6, d1),
  };

  // control-cell (tag, tsc) boxes, base coords
  const ctrlBox = (i: number) => {
    const a = P([1668 + i * 72, 722]);
    const b = P([1668 + i * 72 + 64, 778]);
    return { x: a[0], y: a[1], w: b[0] - a[0], h: b[1] - a[1] };
  };

  // timer near the host core (between core and host memory)
  const tc = P([140, 400]);
  const timerC1 = fade(t, T[1] + 3.7, T[2]);
  const dblLab = fade(t, T[3] + 3.3, T[3] + 8.6, 0.4);
  const loopTimerK = lt < 0 ? 0 : u < L.wait ? 0 : u < L.wait + L.waitD ? lin(u, L.wait, L.waitD) : 1 - lin(u, L.wait + L.waitD + 0.2, 0.3);
  const tfs = Math.max(20, 26 * s);

  // ---- c2: comparison bars + stat (fixed coords, after the morph)
  const c2 = fade(t, T[2] + 0.8, T[3]);
  const BX = 400;
  const BPX = 120;

  // ---- c3: wait sweep chart + read schematic
  const c3 = fade(t, T[3] + 0.5, T[4]);
  const x0 = 1320,
    x1 = 1830,
    y0 = 770,
    y1 = 200;
  const sx = linScale(0, 400, x0, x1);
  const sy = linScale(0.6, 1.6, y0, y1);
  const segT = [T[3] + 3.8, T[3] + 4.3, T[3] + 8.6, T[3] + 10.6, T[3] + 11.1, T[3] + 11.6];
  const serK = t < T[3] + 1.3 ? 0 : (0.001 + segT.reduce((a, st) => a + prog(t, st, 0.5), 0)) / 6;
  const ptOp = (i: number) => (i === 0 ? prog(t, T[3] + 1.3, 0.4) : prog(t, segT[i - 1] + 0.4, 0.3));
  const row0 = T[3] + 1.4;
  const row1 = T[3] + 3.9;
  const row2 = T[3] + 8.7;

  // ---- c4: the price
  const c4 = fade(t, T[4] + 0.5, 1e9);
  const msgX = (i: number) => TL_X + i * 0.83 * PX;
  const tlK = (i: number, j: number) => prog(t, T[4] + 0.8 + i * 1.2 + j * 0.5, 0.6);

  return (
    <>
      <Stage>
        <Datapath
          g={g}
          show={show}
          labels={{ ctrl: ["Control cell", "tag + timestamp"], replyRing: ["DMA reply ring", "DPI writes here"] }}
          cellFill={{ reqRing: (i) => (cellA(i) > 0.01 ? `${K.req}${hex(cellA(i))}` : undefined) }}
          cores={1}
          activeCores={1}
          linkGlow={0.4}
        />

        {/* c0: DPI copy (previous scene) */}
        <Glow r={g.r.dpi} color={K.dma} k={dmaOn} />
        <Glow r={g.r.replyRing} color={K.dma} k={dmaOn * 0.7} />
        <g opacity={dmaOn * (dmaK > 0 && dmaK < 1 ? 1 : 0.0)}>
          <Packet pts={r.dmaReply} k={dmaK} color={K.dma} r={10} />
        </g>
        {/* c0: the question: host reads the control cell itself? */}
        <Link pts={r.pullOut} k={qK} color={K.pull} w={5} dash="14 10" opacity={qOp} />
        <Glow r={g.r.ctrl} color={K.pull} k={fade(t, T[0] + 4.8, T[1]) + fade(t, T[1] + 6.8, T[2]) * 0.9} />
        {(() => {
          const [qx, qy] = P([960, 615]);
          const k = pop(t, T[0] + 4.8);
          if (k <= 0) return null;
          return (
            <g opacity={qOp} transform={`translate(${qx} ${qy}) scale(${k})`}>
              <circle r={40} fill={`${K.pull}22`} stroke={K.pull} strokeWidth={4} />
              <text textAnchor="middle" dominantBaseline="central" fontSize={56} fontWeight={800} fill={K.pull} fontFamily={FONT}>
                ?
              </text>
            </g>
          );
        })()}

        {/* control-cell contents */}
        {[0, 1].map((i) => {
          const b = ctrlBox(i);
          return (
            <g key={i}>
              <rect x={b.x} y={b.y} width={b.w} height={b.h} rx={6 * s} fill={ctrlLit > 0.01 ? `${K.rep}${hex(ctrlLit * 0.45)}` : "rgba(139,149,173,0.08)"} stroke={ctrlLit > 0.5 ? K.rep : C.dim} strokeWidth={2} />
              <text x={b.x + b.w / 2} y={b.y + b.h / 2} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={700} fill={C.text} fontFamily={FONT} opacity={1 - m}>
                {i === 0 ? "tag" : "tsc"}
              </text>
            </g>
          );
        })}

        {/* c1: one round trip */}
        <g opacity={c1}>
          <Packet pts={r.inline} k={inK} color={K.req} />
          <g opacity={1 - prog(t, T[1] + 5.6, 0.5)}>
            <Packet pts={r.coreToCtrl} k={wrK} color={K.rep} r={9} />
          </g>
          <Packet pts={r.pullOut} k={outK} color={K.pull} />
          <Packet pts={r.pullBack} k={backK} color={K.pull} />
        </g>
        <Timer c={tc} r={24 * s + 4} k={tmK} fs={tfs} opacity={timerC1}>
          wait 150 ns
        </Timer>

        {/* c2–c4: ambient loop */}
        {lt >= 0 && (
          <g opacity={loopOn}>
            <g opacity={gone(L.inl + 0.8)}>
              <Packet pts={r.inline} k={seg(L.inl, 0.8)} color={K.req} r={9} />
            </g>
            <g opacity={gone(L.wr + L.wrD)}>
              <Packet pts={r.coreToCtrl} k={seg(L.wr, L.wrD)} color={K.rep} r={8} />
            </g>
            <g opacity={gone(L.back1)}>
              <Packet pts={r.pullOut} k={seg(L.out1, 0.6)} color={K.pull} r={9} />
            </g>
            <g opacity={gone(L.out2 > 0 ? L.out2 : L.end)}>
              <Packet pts={r.pullBack} k={seg(L.back1, 0.6)} color={dbl ? K.bad : K.pull} r={9} />
            </g>
            {dbl && (
              <>
                <g opacity={gone(L.back2)}>
                  <Packet pts={r.pullOut} k={seg(L.out2, 0.6)} color={K.pull} r={9} />
                </g>
                <g opacity={gone(L.end)}>
                  <Packet pts={r.pullBack} k={seg(L.back2, 0.6)} color={K.pull} r={9} />
                </g>
              </>
            )}
            <Glow r={g.r.core} color={K.uc} k={inflight * c4} pad={4} />
            <Timer c={tc} r={24 * s + 4} k={loopTimerK} fs={tfs} opacity={1 - dblLab}>
              wait 150 ns
            </Timer>
            <Timer c={tc} r={24 * s + 4} k={loopTimerK} fs={tfs} opacity={dblLab}>
              wait 50–100 ns
            </Timer>
          </g>
        )}

        {/* c2: W=1 p50 by return path */}
        <g opacity={c2}>
          {BARS.map((b, i) => {
            const y = 632 + i * 50;
            const k = prog(t, T[2] + 1.2 + i * 0.35, 0.6);
            const end = BX + (b.hi ?? b.v) * BPX * k;
            const pull = i === BARS.length - 1;
            return (
              <g key={i}>
                {b.hi && <rect x={BX} y={y} width={b.hi * BPX * k} height={34} rx={6} fill={`${b.color}18`} stroke={b.color} strokeWidth={2} strokeDasharray="7 5" />}
                <rect x={BX} y={y} width={b.v * BPX * k} height={34} rx={6} fill={`${b.color}${pull ? "88" : "55"}`} stroke={b.color} strokeWidth={pull ? 3.5 : 2} filter={pull ? "url(#glow)" : undefined} />
                <text x={end + 12} y={y + 17} dominantBaseline="central" fontSize={24} fontWeight={700} fill={b.color} fontFamily={FONT} opacity={clamp01((k - 0.5) * 2)}>
                  {b.text}
                </text>
              </g>
            );
          })}
        </g>

        {/* c3: wait sweep */}
        <g opacity={c3}>
          <Axes
            x0={x0}
            x1={x1}
            y0={y0}
            y1={y1}
            sx={sx}
            sy={sy}
            size={24}
            xticks={SWEEP.map(([w]) => [w, String(w)] as [number, string])}
            yticks={[0.6, 0.8, 1.0, 1.2, 1.4, 1.6].map((v) => [v, v.toFixed(1)] as [number, string])}
            xlabel="wait before the read (ns)"
            ylabel="p50 round trip (µs)"
          />
          {/* sweet-spot band */}
          <rect x={sx(150) - 22} y={sy(1.05)} width={44} height={y0 - sy(1.05)} rx={10} fill={`${K.ok}1c`} stroke={K.ok} strokeWidth={2} strokeDasharray="6 6" opacity={prog(t, segT[2] + 0.3, 0.5)} />
          <Series pts={SWEEP} sx={sx} sy={sy} k={serK} color={K.pull} w={5} r={8} />
          <circle cx={sx(150)} cy={sy(0.83)} r={16 * pop(t, segT[2] + 0.4)} fill="none" stroke={K.ok} strokeWidth={4} />
          {SWEEP.map(([w, v], i) => {
            const above = i === 1 || i === 2;
            const right = i === 0;
            return (
              <text
                key={i}
                x={right ? sx(w) + 16 : sx(w)}
                y={above ? sy(v) - 30 : right ? sy(v) : sy(v) + 36}
                textAnchor={right ? "start" : "middle"}
                dominantBaseline="central"
                fontSize={26}
                fontWeight={800}
                fill={i === 3 ? K.ok : C.text}
                fontFamily={FONT}
                opacity={ptOp(i)}
              >
                {v.toFixed(2)}
              </text>
            );
          })}
          <text x={sx(0) + 16} y={sy(0.89) + 34} dominantBaseline="central" fontSize={24} fontWeight={700} fill={C.muted} fontStyle="italic" fontFamily={FONT} opacity={prog(t, T[3] + 1.8, 0.5)}>
            bimodal
          </text>
          <text x={sx(100) + 24} y={sy(1.43)} dominantBaseline="central" fontSize={24} fontWeight={700} fill={K.bad} fontFamily={FONT} opacity={prog(t, T[3] + 5.0, 0.5)}>
            first read misses
          </text>
          <text x={sx(150)} y={sy(0.83) + 70} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={K.ok} fontFamily={FONT} opacity={prog(t, segT[2] + 0.5, 0.5)}>
            sweet spot
          </text>
          <text x={sx(250)} y={sy(1.09) - 90} fontSize={24} fontWeight={700} fill={C.muted} fontFamily={FONT} opacity={prog(t, segT[5] + 0.3, 0.5)}>
            <tspan x={sx(250)}>longer waits</tspan>
            <tspan x={sx(250)} dy={30}>
              only add time
            </tspan>
          </text>

          {/* left-bottom: reads per round trip (schematic) */}
          <InfTag x={505} y={592} opacity={prog(t, row0, 0.5)} />
          {(
            [
              { at: row0, y: 632, wait: 0, reads: [{ c: K.pull, l: "16 B read ✓" }, { c: K.pull, l: "maybe a 2nd", d: true }], v: "0.89 µs", vc: C.text },
              { at: row1, y: 712, wait: 0.075, reads: [{ c: K.bad, l: "read: stale ✗" }, { c: K.pull, l: "read ✓" }], v: "1.43 µs", vc: K.bad },
              { at: row2, y: 792, wait: 0.15, reads: [{ c: K.pull, l: "16 B read ✓" }], v: "0.83 µs", vc: K.ok },
            ] as { at: number; y: number; wait: number; reads: { c: string; l: string; d?: boolean }[]; v: string; vc: string }[]
          ).map((row, ri) => {
            const wW = row.wait * PX;
            const kW = prog(t, row.at, 0.4);
            return (
              <g key={ri}>
                {row.wait > 0 && <Seg x={TL_X} y={row.y} w={wW} h={48} k={kW} color={K.timer} />}
                {row.reads.map((rd, j) => (
                  <Seg key={j} x={TL_X + wW + (row.wait > 0 ? 4 : 0) + j * (RD + 6)} y={row.y} w={RD} h={48} k={prog(t, row.at + 0.3 + j * 0.6, 0.6)} color={rd.c} label={rd.l} dashed={rd.d} />
                ))}
                <text x={1010} y={row.y + 24} dominantBaseline="central" fontSize={28} fontWeight={800} fill={row.vc} fontFamily={FONT} opacity={prog(t, row.at + 0.4 + row.reads.length * 0.6, 0.4)}>
                  {row.v}
                </text>
              </g>
            );
          })}
        </g>

        {/* c4: host core timeline */}
        <g opacity={c4}>
          {[0, 1].map((i) => (
            <g key={i}>
              <Seg x={msgX(i)} y={648} w={0.15 * PX} h={64} k={tlK(i, 0)} color={K.timer} />
              <Seg x={msgX(i) + 0.15 * PX + 4} y={648} w={RD} h={64} k={tlK(i, 1)} color={K.uc} hatch label="stalled 0.63 µs" fs={26} />
              <text x={msgX(i)} y={622} fontSize={24} fill={C.muted} fontFamily={FONT} opacity={tlK(i, 0)}>
                {i === 0 ? "message n" : "message n+1"}
              </text>
            </g>
          ))}
          {(() => {
            const k = prog(t, T[4] + 3.4, 0.6);
            const a = msgX(0);
            const b = msgX(1) - 6;
            return (
              <g opacity={k}>
                <path d={`M${a} 728 v12 H${b} v-12`} fill="none" stroke={C.muted} strokeWidth={2.5} />
                <text x={(a + b) / 2} y={764} textAnchor="middle" dominantBaseline="central" fontSize={24} fill={C.muted} fontFamily={FONT}>
                  0.83 µs p50 round trip
                </text>
              </g>
            );
          })()}
          {/* playhead */}
          {(() => {
            const k = lin(t, T[4] + 1.0, 8.5);
            const x = lerp(TL_X, msgX(1) + 0.78 * PX, k);
            return <line x1={x} y1={640} x2={x} y2={720} stroke={C.text} strokeWidth={3} opacity={0.7 * prog(t, T[4] + 1.0, 0.4) * (1 - prog(t, T[4] + 9.5, 0.5))} />;
          })()}
        </g>
      </Stage>

      {/* c0 labels in the link area */}
      {(() => {
        const [ax, ay] = P([720, 205]);
        const [bx, by] = P([720, 672]);
        return (
          <>
            <Txt x={ax} y={ay} w={480 * s} size={28} color={C.muted} align="center" style={{ opacity: c0 }}>
              each DPI copy
              <div style={{ fontSize: 60, fontWeight: 800, color: K.dma }}>≈ 0.67 µs</div>
            </Txt>
            <Txt x={bx} y={by} w={480 * s} size={28} align="center" style={{ opacity: fade(t, T[0] + 5.0, T[1]) }}>
              host fetches the reply
              <br />
              with <span style={{ color: K.pull, fontWeight: 700 }}>one read</span>?
            </Txt>
          </>
        );
      })()}

      {/* c1 steps below the link */}
      {(
        [
          [K.req, <><span style={{ color: K.req, fontWeight: 700 }}>Request cell</span> lands</>],
          [K.rep, <>Card writes <span style={{ color: K.rep, fontWeight: 700 }}>(tag, timestamp)</span></>],
          [K.timer, <>Host waits <span style={{ color: K.timer, fontWeight: 700 }}>150 ns</span></>],
          [K.pull, <>One <span style={{ color: K.pull, fontWeight: 700 }}>16 B uncached read</span></>],
        ] as [string, React.ReactNode][]
      ).map(([col, txt], i) => {
        const [x, y] = P([722, 572 + i * 68]);
        return (
          <Step key={i} x={x} y={y} n={i + 1} color={col} style={appear(t, s1[i], T[2])}>
            {txt}
          </Step>
        );
      })}

      {/* c2 */}
      <Txt x={70} y={584} size={24} color={C.muted} style={{ opacity: c2 }}>
        W=1 p50 round trip, by path
      </Txt>
      {BARS.map((b, i) => (
        <Txt key={i} x={60} y={632 + i * 50 + 1} w={320} size={24} align="right" color={i === BARS.length - 1 ? K.pull : C.text} weight={i === BARS.length - 1 ? 700 : 400} style={{ opacity: c2 }}>
          {b.label}
        </Txt>
      ))}
      <Stat
        value={`${countUp(t, T[2] + 0.9, 1.4, 0.83, 2)} µs`}
        label="W=1 p50 round trip · 150 ns wait"
        color={K.pull}
        size={120}
        labelSize={28}
        style={{ position: "absolute", left: 1215, top: 160, width: 640, boxSizing: "border-box", ...appear(t, T[2] + 0.8, T[3]) }}
      />
      <Txt x={1215} y={420} size={26} color={C.muted} style={appear(t, T[2] + 4.2, T[3])}>
        repeat runs
      </Txt>
      {["0.83", "0.83", "1.04"].map((v, i) => {
        const a = T[2] + 4.6 + i * 0.8;
        return (
          <Pill key={i} x={1215 + i * 160} y={465} size={40} color={i === 2 ? C.muted : K.pull} style={{ opacity: fade(t, a, T[3], 0.3), transform: `scale(${pop(t, a)})`, transformOrigin: "center" }}>
            {v}
          </Pill>
        );
      })}
      <Txt x={1215} y={560} w={640} size={24} color={C.muted} style={appear(t, T[2] + 6.8, T[3])}>
        1.04: a run-level mode shift of ~0.2 µs
      </Txt>
      <Pill x={1215} y={650} size={36} color={K.pull} solid style={{ opacity: fade(t, T[2] + 7.8, T[3], 0.3), transform: `scale(${pop(t, T[2] + 7.8)})`, transformOrigin: "left center" }}>
        fastest round trip of all
      </Pill>

      {/* c3 left-bottom labels */}
      <Txt x={70} y={584} size={24} color={C.muted} style={{ opacity: c3 * prog(t, row0, 0.5) }}>
        Reads per round trip (schematic)
      </Txt>
      {(
        [
          [row0, 632, "no wait"],
          [row1, 712, "wait 50–100 ns"],
          [row2, 792, "wait 150 ns"],
        ] as [number, number, string][]
      ).map(([a, y, l], i) => (
        <Txt key={i} x={60} y={y + 8} w={265} size={24} align="right" style={{ opacity: c3 * prog(t, a, 0.4) }}>
          {l}
        </Txt>
      ))}

      {/* c4 */}
      <Txt x={60} y={662} w={265} size={28} weight={700} align="right" color={K.host} style={{ opacity: c4 }}>
        host core
      </Txt>
      <Txt x={70} y={808} w={1100} size={30} style={appear(t, T[4] + 5.2)}>
        One read in flight per core → pull fits only <span style={{ color: K.pull, fontWeight: 800 }}>W=1</span>
      </Txt>
      <Stat
        value={`${countUp(t, T[4] + 0.7, 1.2, 0.63, 2)} µs`}
        label="host core stalled per 16 B read"
        color={K.uc}
        size={104}
        style={{ position: "absolute", left: 1215, top: 170, width: 640, boxSizing: "border-box", ...appear(t, T[4] + 0.6) }}
      />
      <Stat
        value={<span style={{ whiteSpace: "nowrap" }}>{countUp(t, T[4] + 6.4, 1.2, 1.17, 2, 0.5)} M msg/s</span>}
        label="W=1 only"
        color={K.pull}
        size={84}
        style={{ position: "absolute", left: 1215, top: 470, width: 640, boxSizing: "border-box", ...appear(t, T[4] + 6.4) }}
      />
    </>
  );
}

export const S09: SceneDef = {
  id: "S09",
  title: "Way back #3: just read it",
  stage: 2,
  source: "inline_pingpong --reply pull --pull-delay-ns 150",
  dur: DUR,
  cues,
  C: Body,
};
