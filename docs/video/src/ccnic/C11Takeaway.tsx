import React from "react";
import { Cue, FONT, Pt, SceneDef, lerp, lin, prog, useT } from "../lib";
import { Cell, Flow, P, Stage, TAB, Tag, Text, alpha, rise } from "./kit";
import { Token, ends, headD } from "./_CcSceneProof";

// <cues>
const cues: Cue[] = [
  [0.60, 10.02, "Back to the question: the speedup came from **putting data in the reader's cache without round trips**.", "So, back to our question. The speedup came from putting each message into the reader's cache, with no round trips. Coherence was one way to get there, not the reason it works."], // 0
  [10.37, 20.19, "Plain PCIe gets there with **writes pushed both ways**, **credits** against congestion, and the **NIC's DMA** for bulk.", "Plain P C I express gets there with three simple pieces: writes pushed in both directions, credits to avoid congestion, and the card's own D M A for bulk data."], // 1
  [20.54, 31.28, "The result: sub-µs round trips, line rate from one core, no coherence tax, and the IOMMU stays on.", "What we get: sub-microsecond round trips, line rate from a single core, no coherence tax on retained buffers, and full I O M M U isolation, on hardware you can buy today."], // 2
  [31.63, 43.01, "Coherence still matters for **truly shared state**, such as atomics on a shared buffer allocator. We don't claim that.", "Coherence still matters where host and card truly share mutable state, such as atomic operations on a shared buffer allocator. P C I express cannot do that, and we do not claim it can."], // 3
  [43.36, 55.96, "Related work proposed new PCIe and CPU extensions for write-based datapaths. We show **unmodified hardware** suffices.", "This builds on recent work that treats device writes as a data path, which proposed new P C I express and C P U extensions. We show that unmodified hardware is already enough, and what it takes to make it robust."], // 4
  [56.31, 63.61, "Open next step: let the NIC **back off on demand**, to keep sub-2 µs tails at higher load.", "The main open step is on the card side: backing off on demand, so tails stay under two microseconds at higher load."], // 5
  [63.96, 72.98, "**CC-NIC does not need cache coherence.** It needs a better hand-over, and PCIe already provides one.", "Our one-sentence takeaway: C C nick does not need cache coherence. It needs a better hand-over, and plain P C I express already provides one."], // 6
];
const DUR = 74.38; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---------------------------------------------------------------- b0 fork (as in chapter 3) → b1 roof over the recipe
const ROOT: Pt = [960, 300];
const OPT_L: Pt = [560, 524];
const OPT_R: Pt = [1360, 524];
const ROOF: Pt = [960, 272];
const OPT_W = 360;
const OPT_H = 100;
type Bez = [Pt, Pt, Pt, Pt];

/** Cubic branch from the root pill down to an option pill: control points and SVG path. */
function branch(to: Pt): { b: Bez; d: string } {
  const p0: Pt = [ROOT[0], ROOT[1] + 32];
  const p3: Pt = [to[0], to[1] - OPT_H / 2];
  const b: Bez = [p0, [p0[0], p0[1] + 100], [p3[0], p3[1] - 100], p3];
  return { b, d: `M${p0[0]} ${p0[1]} C${b[1][0]} ${b[1][1]} ${b[2][0]} ${b[2][1]} ${p3[0]} ${p3[1]}` };
}
const BR_L = branch(OPT_L);
const BR_R = branch(OPT_R);

const PIL_Y = 392;
const BUS_Y = PIL_Y - 36;
const PIL_W = 380;
const PIL_H = 292;
const PILLARS = [
  { x: 520, name: "Push both ways", sub: "one write per message", color: P.push },
  { x: 960, name: "Credits", sub: "cap messages in flight", color: P.nic },
  { x: 1400, name: "NIC DMA for bulk", sub: "the NIC moves big payloads", color: P.nic },
];

const TILE_W = 780;
const TILE_H = 150;
const TILES = [
  { x: 160, y: 372, hero: "0.9 µs", rest: "round trip" },
  { x: 980, y: 372, hero: "100GbE", rest: "from one core" },
  { x: 160, y: 552, hero: "no coherence tax", rest: "(−0.9%)" },
  { x: 980, y: 552, hero: "IOMMU on", rest: "" },
];

/** Rounded pill with a centred label (SVG); `glow` lights it. */
function Pill({ c, w, h, color, label, size, opacity = 1, glow = 0, text }: { c: Pt; w: number; h: number; color: string; label: string; size: number; opacity?: number; glow?: number; text?: string }) {
  if (opacity <= 0) return null;
  const [x, y] = c;
  return (
    <g opacity={opacity} fontFamily={FONT}>
      {glow > 0.01 && <rect x={x - w / 2 - 12} y={y - h / 2 - 12} width={w + 24} height={h + 24} rx={(h + 24) / 2} fill={alpha(color, 0.26 * glow)} filter="url(#ccSoft)" />}
      <rect x={x - w / 2} y={y - h / 2} width={w} height={h} rx={h / 2} fill={alpha(color, 0.06 + 0.12 * glow)} stroke={alpha(color, 0.5 + 0.5 * glow)} strokeWidth={1.5 + glow} />
      <text x={x} y={y + 1} textAnchor="middle" dominantBaseline="central" fontSize={size} fontWeight={600} fill={text ?? color}>
        {label}
      </text>
    </g>
  );
}

/** Host bar (left) and NIC bar (right) framing a small glyph lane. */
function Ends({ x, y }: { x: number; y: number }) {
  return (
    <>
      <rect x={x - 118} y={y - 36} width={10} height={72} rx={5} fill={alpha(P.host, 0.85)} />
      <rect x={x + 108} y={y - 36} width={10} height={72} rx={5} fill={alpha(P.nic, 0.85)} />
    </>
  );
}

/** Pillar glyphs: 0 push both ways, 1 credits circulating, 2 one command then a bulk DMA block. */
function PillarGlyph({ kind, x, y, t }: { kind: number; x: number; y: number; t: number }) {
  const loop = (v: number) => ((v % 1) + 1) % 1;
  if (kind === 0) {
    const k1 = loop(t * 0.62);
    const k2 = loop(t * 0.62 + 0.5);
    return (
      <g>
        <Ends x={x} y={y} />
        <line x1={x - 100} y1={y - 14} x2={x + 100} y2={y - 14} stroke={P.link} strokeWidth={2} />
        <line x1={x - 100} y1={y + 14} x2={x + 100} y2={y + 14} stroke={P.link} strokeWidth={2} />
        <Cell pts={[[x - 92, y - 14], [x + 92, y - 14]]} k={k1} color={P.push} w={28} h={18} opacity={ends(k1, 0.15, 0.15)} />
        <Cell pts={[[x + 92, y + 14], [x - 92, y + 14]]} k={k2} color={P.push} w={28} h={18} opacity={ends(k2, 0.15, 0.15)} />
      </g>
    );
  }
  if (kind === 1) {
    return (
      <g>
        <ellipse cx={x} cy={y} rx={96} ry={32} fill="none" stroke={P.hair2} strokeWidth={2} />
        {[0, 1, 2, 3].map((j) => {
          const a = t * 1.0 + (j * Math.PI) / 2;
          const p: Pt = [x + 96 * Math.cos(a), y + 32 * Math.sin(a)];
          return <Token key={j} pts={[p, [p[0] + 0.01, p[1]]]} k={0} r={10} />;
        })}
      </g>
    );
  }
  const ph = loop(t / 2.6);
  const kc = ph / 0.3;
  const kb = (ph - 0.36) / 0.56;
  return (
    <g>
      <Ends x={x} y={y} />
      <line x1={x - 100} y1={y} x2={x + 100} y2={y} stroke={P.link} strokeWidth={2} />
      <Cell pts={[[x - 92, y], [x + 92, y]]} k={kc} color={P.push} w={24} h={16} opacity={ends(kc, 0.15, 0.15)} />
      <Cell pts={[[x - 80, y], [x + 80, y]]} k={kb} color={P.nic} w={70} h={34} opacity={ends(kb, 0.15, 0.15)} />
    </g>
  );
}

/** Result-tile glyphs: 0 round trip, 1 one core filling a link, 2 no coherence domain, 3 IOMMU shield. */
function TileGlyph({ kind, x, y, t, k }: { kind: number; x: number; y: number; t: number; k: number }) {
  if (k <= 0) return null;
  const c = P.push;
  if (kind === 0) {
    const r = 24;
    const a0 = t * 1.2;
    const a1 = a0 + 5.0 * k;
    const s: Pt = [x + r * Math.cos(a0), y + r * Math.sin(a0)];
    const e: Pt = [x + r * Math.cos(a1), y + r * Math.sin(a1)];
    return (
      <g fill="none" stroke={c} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
        <path d={`M${s[0]} ${s[1]} A${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${e[0]} ${e[1]}`} />
        <path d={headD(e, -Math.sin(a1), Math.cos(a1), 10)} />
      </g>
    );
  }
  if (kind === 1) {
    return (
      <g opacity={k}>
        <rect x={x - 32} y={y - 16} width={32} height={32} rx={6} fill={alpha(c, 0.15)} stroke={c} strokeWidth={2.5} />
        {[-10, 0, 10].map((o) => (
          <line key={o} x1={x + 8} y1={y + o} x2={x + 40} y2={y + o} stroke={c} strokeWidth={3} strokeLinecap="round" strokeDasharray="7 7" strokeDashoffset={-((t * 42) % 14)} />
        ))}
      </g>
    );
  }
  if (kind === 2) {
    return (
      <g>
        <circle cx={x} cy={y} r={24} fill="none" stroke={alpha(P.coh, 0.75)} strokeWidth={2.5} strokeDasharray="6 5" opacity={k} />
        <line x1={x - 20} y1={y + 20} x2={x - 20 + 40 * k} y2={y + 20 - 40 * k} stroke={c} strokeWidth={3.5} strokeLinecap="round" />
      </g>
    );
  }
  return (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <path d={`M${x} ${y - 27} L${x + 22} ${y - 18} L${x + 21} ${y + 2} Q${x + 17} ${y + 19} ${x} ${y + 28} Q${x - 17} ${y + 19} ${x - 21} ${y + 2} L${x - 22} ${y - 18} Z`} stroke={c} strokeWidth={2.5} fill={alpha(c, 0.1)} opacity={k} />
      <path d={`M${x - 9} ${y + 1} L${x - 2} ${y + 9} L${x + 11} ${y - 8}`} stroke={c} strokeWidth={3} pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - k} />
    </g>
  );
}

/** Host and NIC both updating one shared allocator inside a coherence domain (SVG). */
function SharedGlyph({ x, y, t }: { x: number; y: number; t: number }) {
  const ph = (t * 1.6) % 2;
  const hostOn = ph < 1 ? Math.sin(ph * Math.PI) : 0;
  const nicOn = ph >= 1 ? Math.sin((ph - 1) * Math.PI) : 0;
  return (
    <g fontFamily={FONT}>
      <rect x={x - 74} y={y - 62} width={148} height={124} rx={24} fill={alpha(P.coh, 0.05)} stroke={P.coh} strokeWidth={2} strokeDasharray="9 7" />
      {[0, 1, 2].map((j) => (
        <rect key={j} x={x - 44} y={y - 36 + j * 26} width={88} height={18} rx={6} fill={alpha(P.coh, 0.14)} stroke={alpha(P.coh, 0.75)} strokeWidth={1.5} />
      ))}
      {(
        [
          [-1, P.host, "host", hostOn],
          [1, P.nic, "NIC", nicOn],
        ] as [number, string, string, number][]
      ).map(([side, col, name, on]) => (
        <g key={name}>
          <rect x={x + side * 176 - 38} y={y - 24} width={76} height={48} rx={12} fill={alpha(col, 0.1)} stroke={col} strokeWidth={1.5} />
          <text x={x + side * 176} y={y + 1} textAnchor="middle" dominantBaseline="central" fontSize={20} fill={P.ink}>
            {name}
          </text>
          <g stroke={col} strokeWidth={3} strokeLinecap="round" fill="none" opacity={0.3 + 0.7 * on}>
            <line x1={x + side * 136} y1={y} x2={x + side * 82} y2={y} />
            <path d={headD([x + side * 80, y], -side, 0, 10)} />
          </g>
        </g>
      ))}
    </g>
  );
}

/** A chip with pins (SVG): prior work adds modules to it, ours leaves it unmodified. */
function Chip({ x, y, color }: { x: number; y: number; color: string }) {
  const s = 70;
  return (
    <g>
      <rect x={x - s / 2} y={y - s / 2} width={s} height={s} rx={10} fill={alpha(color, 0.08)} stroke={color} strokeWidth={2} />
      <rect x={x - 16} y={y - 16} width={32} height={32} rx={5} fill="none" stroke={alpha(color, 0.6)} strokeWidth={1.5} />
      {[-21, -7, 7, 21].map((o) => (
        <g key={o} stroke={alpha(color, 0.7)} strokeWidth={2} strokeLinecap="round">
          <line x1={x + o} y1={y - s / 2 - 10} x2={x + o} y2={y - s / 2} />
          <line x1={x + o} y1={y + s / 2} x2={x + o} y2={y + s / 2 + 10} />
          <line x1={x - s / 2 - 10} y1={y + o} x2={x - s / 2} y2={y + o} />
          <line x1={x + s / 2} y1={y + o} x2={x + s / 2 + 10} y2={y + o} />
        </g>
      ))}
    </g>
  );
}

function Body() {
  const t = useT();
  // b0: the fork returns and resolves toward the hand-over
  const out0 = prog(t, T[1], 0.8);
  const rootK = prog(t, T[0] + 0.2, 0.6) * (1 - out0);
  const brK = prog(t, T[0] + 0.7, 0.9);
  const optK = prog(t, T[0] + 1.2, 0.6);
  const resolve = prog(t, T[0] + 2.4, 0.8);
  const cohDim = prog(t, T[0] + 3.2, 0.9);
  const caption = prog(t, T[0] + 3.6, 0.7) * (1 - out0);
  const pulseU = lin(t, T[0] + 2.0, 0.7);
  const [b0, b1, b2, b3] = BR_R.b;
  const v = 1 - pulseU;
  const pulse: Pt = [
    v * v * v * b0[0] + 3 * v * v * pulseU * b1[0] + 3 * v * pulseU * pulseU * b2[0] + pulseU * pulseU * pulseU * b3[0],
    v * v * v * b0[1] + 3 * v * v * pulseU * b1[1] + 3 * v * pulseU * pulseU * b2[1] + pulseU * pulseU * pulseU * b3[1],
  ];
  // b1: "Hand-over" rises to become the roof over the three pieces of the recipe
  const mv = prog(t, T[1] + 0.2, 1.2);
  const out1 = prog(t, T[2], 0.8);
  const conn = prog(t, T[1] + 1.4, 0.8) * (1 - out1);
  const pk = [2.9, 4.7, 6.4].map((d) => prog(t, T[1] + d, 0.7) * (1 - out1));
  // b2: four results
  const out2 = prog(t, T[3], 0.7);
  const kick2 = prog(t, T[2] + 0.6, 0.6) * (1 - out2);
  const tk = [1.2, 3.2, 5.2, 7.4].map((d) => prog(t, T[2] + d, 0.6) * (1 - out2));
  // b3: where coherence still matters
  const pan3 = prog(t, T[3] + 0.4, 0.7) * (1 - prog(t, T[4], 0.7));
  const it1 = prog(t, T[3] + 1.6, 0.6);
  const it2 = prog(t, T[3] + 4.4, 0.6);
  const notClaimed = prog(t, T[3] + 7.4, 0.5);
  // b4: prior work vs ours
  const out4 = prog(t, T[5], 0.7);
  const left4 = prog(t, T[4] + 0.4, 0.7) * (1 - out4);
  const ext = prog(t, T[4] + 1.4, 0.9);
  const right4 = prog(t, T[4] + 6.6, 0.7) * (1 - out4);
  const check = prog(t, T[4] + 7.3, 0.6);
  // b5: the open next step
  const pan5 = prog(t, T[5] + 0.3, 0.6) * (1 - prog(t, T[6], 0.7));
  const step1 = prog(t, T[5] + 0.5, 0.6);
  const arrow = prog(t, T[5] + 2.5, 0.8);
  const step2 = prog(t, T[5] + 3.1, 0.6);
  // b6: the closing card
  const close1 = prog(t, T[6] + 0.8, 0.9);
  const rule = prog(t, T[6] + 1.6, 1.2);
  const close2 = prog(t, T[6] + 4.2, 0.8);

  return (
    <>
      <Stage>
        {/* ---------------- b0 fork */}
        <g opacity={1 - out0} fill="none" strokeLinecap="round">
          <path d={BR_L.d} stroke={P.coh} strokeWidth={3} pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - brK} opacity={1 - 0.78 * cohDim} />
          <path d={BR_R.d} stroke={P.push} strokeWidth={3 + 1.5 * resolve} pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - brK} opacity={0.6 + 0.4 * resolve} />
          {pulseU > 0 && pulseU < 1 && <circle cx={pulse[0]} cy={pulse[1]} r={8} fill={P.push} filter="url(#ccGlow)" />}
        </g>
        <Pill c={ROOT} w={280} h={64} color={P.sub} text={P.ink} label="the speedup" size={28} opacity={rootK} />
        <Pill c={OPT_L} w={OPT_W} h={OPT_H} color={P.coh} label="Coherence" size={44} opacity={optK * (1 - 0.7 * cohDim) * (1 - out0)} />
        <Pill
          c={[lerp(OPT_R[0], ROOF[0], mv), lerp(OPT_R[1], ROOF[1], mv)]}
          w={lerp(OPT_W, 320, mv)}
          h={lerp(OPT_H, 80, mv)}
          color={P.push}
          label="Hand-over"
          size={lerp(44, 34, mv)}
          glow={resolve}
          opacity={optK * (1 - out1)}
        />

        {/* ---------------- b1 the recipe: three pillars under the hand-over */}
        <g opacity={conn} stroke={alpha(P.push, 0.45)} strokeWidth={2} fill="none">
          <path d={`M${ROOF[0]} ${ROOF[1] + 40} V${BUS_Y} M${PILLARS[0].x} ${BUS_Y} H${PILLARS[2].x}`} />
          {PILLARS.map((p) => (
            <path key={p.x} d={`M${p.x} ${BUS_Y} V${PIL_Y}`} />
          ))}
        </g>
        {PILLARS.map((p, i) => (
          <g key={p.name} opacity={pk[i]}>
            <rect x={p.x - PIL_W / 2} y={PIL_Y} width={PIL_W} height={PIL_H} rx={20} fill={P.panel} stroke={alpha(p.color, 0.35)} strokeWidth={1.5} />
            <PillarGlyph kind={i} x={p.x} y={PIL_Y + 92} t={t} />
          </g>
        ))}

        {/* ---------------- b2 result tiles */}
        {TILES.map((tile, i) => (
          <g key={tile.hero} opacity={tk[i]}>
            <rect x={tile.x} y={tile.y} width={TILE_W} height={TILE_H} rx={18} fill={P.panel} stroke={P.panelLine} strokeWidth={1.5} />
            <TileGlyph kind={i} x={tile.x + 72} y={tile.y + TILE_H / 2} t={t} k={prog(t, T[2] + [1.2, 3.2, 5.2, 7.4][i] + 0.2, 0.8)} />
          </g>
        ))}

        {/* ---------------- b3 where coherence still matters */}
        <g opacity={pan3}>
          <rect x={360} y={350} width={1200} height={260} rx={22} fill={alpha(P.coh, 0.04)} stroke={alpha(P.coh, 0.45)} strokeWidth={1.5} />
          <circle cx={414} cy={459} r={7} fill={P.coh} opacity={it1} />
          <circle cx={414} cy={533} r={7} fill={P.coh} opacity={it2} />
          <g opacity={it1}>
            <SharedGlyph x={1310} y={490} t={t} />
          </g>
        </g>

        {/* ---------------- b4 prior work vs ours */}
        <g opacity={left4}>
          <rect x={150} y={340} width={740} height={340} rx={20} fill={P.panel} stroke={P.panelLine} strokeWidth={1.5} />
          <Chip x={520} y={450} color={P.sub} />
          {[-1, 1].map((side) => {
            const mx = 520 + side * lerp(118, 78, ext);
            return (
              <g key={side} opacity={ext} stroke={P.sub} strokeWidth={2} strokeLinecap="round">
                <rect x={mx - 17} y={433} width={34} height={34} rx={8} fill={alpha(P.sub, 0.1)} />
                <path d={`M${mx - 8} 450 H${mx + 8} M${mx} 442 V458`} />
              </g>
            );
          })}
        </g>
        <g opacity={right4}>
          <rect x={1030} y={340} width={740} height={340} rx={20} fill={alpha(P.push, 0.035)} stroke={alpha(P.push, 0.45)} strokeWidth={1.5} />
          <Chip x={1400} y={450} color={P.push} />
          <g opacity={check}>
            <circle cx={1446} cy={404} r={15} fill={P.bg0} stroke={P.push} strokeWidth={2.5} />
            <path d="M1439 404 L1444 410 L1453 398" fill="none" stroke={P.push} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - check} />
          </g>
        </g>

        {/* ---------------- b5 next step */}
        <g opacity={pan5}>
          <rect x={230} y={400} width={1460} height={190} rx={20} fill={P.panel} stroke={P.panelLine} strokeWidth={1.5} />
          <Flow pts={[[902, 512], [1014, 512]]} k={arrow} color={P.sub} w={3} head={14} />
        </g>
      </Stage>

      {/* b0: what the hand-over means */}
      <Text x={OPT_R[0] - 360} y={OPT_R[1] + 76} w={720} align="center" size={26} opacity={caption}>
        data in the reader's cache, no round trips
      </Text>

      {/* b1: pillar names */}
      {PILLARS.map((p, i) => (
        <div key={p.name} style={{ position: "absolute", left: p.x - PIL_W / 2, top: PIL_Y + 166, width: PIL_W, textAlign: "center", fontFamily: FONT, ...rise(pk[i], 14) }}>
          <div style={{ fontSize: 30, fontWeight: 600, color: p.color }}>{p.name}</div>
          <div style={{ fontSize: 21, color: P.sub, marginTop: 10 }}>{p.sub}</div>
        </div>
      ))}

      {/* b2: result tiles */}
      <Text x={0} y={322} w={1920} align="center" size={17} color={P.sub} opacity={kick2} style={{ letterSpacing: 3, fontWeight: 600 }}>
        WHAT PLAIN PCIe DELIVERS
      </Text>
      {TILES.map((tile, i) => (
        <div
          key={tile.hero}
          style={{ position: "absolute", left: tile.x + 132, top: tile.y, height: TILE_H, width: TILE_W - 156, display: "flex", alignItems: "center", fontFamily: FONT, whiteSpace: "nowrap", ...rise(tk[i], 12) }}
        >
          <span style={{ fontSize: 48, fontWeight: 800, color: P.push, letterSpacing: -1, ...TAB }}>{tile.hero}</span>
          {tile.rest && <span style={{ fontSize: 28, fontWeight: 500, color: i === 2 ? P.sub : P.ink, marginLeft: 16, ...TAB }}>{tile.rest}</span>}
        </div>
      ))}

      {/* b3: coherence's real job, not claimed by us */}
      <Text x={400} y={380} size={17} color={P.coh} opacity={pan3} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        WHERE COHERENCE STILL MATTERS
      </Text>
      <Tag x={1380} y={374} color={P.coh} opacity={pan3 * notClaimed}>
        not claimed
      </Tag>
      <Text x={440} y={438} size={32} style={rise(pan3 * it1, 10)}>
        truly shared state
      </Text>
      <Text x={440} y={512} size={32} style={rise(pan3 * it2, 10)}>
        atomics on a shared buffer allocator
      </Text>

      {/* b4: positioning */}
      <Text x={182} y={366} size={17} color={P.sub} opacity={left4} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        PRIOR WORK
      </Text>
      <Text x={190} y={526} w={660} align="center" size={30} opacity={left4}>
        new PCIe / CPU extensions for write-based datapaths
      </Text>
      <Tag x={312} y={628} opacity={left4}>
        Liew et al., HotOS '25 · ASPLOS '26
      </Tag>
      <Text x={1062} y={366} size={17} color={P.push} opacity={right4} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        OURS
      </Text>
      <Text x={1070} y={526} w={660} align="center" size={36} weight={600} opacity={right4}>
        <span style={{ color: P.push }}>unmodified</span> PCIe hardware
        <div style={{ fontSize: 22, fontWeight: 400, color: P.sub, marginTop: 12, opacity: check }}>and what it takes to make it robust</div>
      </Text>

      {/* b5: next step */}
      <Tag x={262} y={424} color={P.gold} opacity={pan5}>
        next step
      </Tag>
      <Text x={250} y={486} w={630} align="right" size={40} weight={600} color={P.nic} opacity={pan5 * step1}>
        NIC-side backoff on demand
      </Text>
      <Text x={1040} y={486} w={640} size={40} weight={600} opacity={pan5 * step2}>
        tails under <span style={{ color: P.gold, ...TAB }}>2 µs</span> at higher load
      </Text>

      {/* b6: closing card */}
      <div style={{ position: "absolute", left: 0, right: 0, top: 372, textAlign: "center", fontFamily: FONT, ...rise(close1, 16) }}>
        <div style={{ fontSize: 68, fontWeight: 800, color: P.ink, letterSpacing: -1.2, lineHeight: 1.1 }}>
          CC-NIC does not need <span style={{ color: P.coh }}>cache coherence</span>.
        </div>
        <div style={{ margin: "34px auto 0", height: 2, width: 560 * rule, background: `linear-gradient(90deg, transparent, ${alpha(P.push, 0.8)}, transparent)` }} />
        <div style={{ fontSize: 36, color: P.sub, marginTop: 30, opacity: close2 }}>
          It needs a better <span style={{ color: P.push, fontWeight: 600 }}>hand-over</span>, and PCIe already provides one.
        </div>
      </div>
    </>
  );
}

export const C11: SceneDef = {
  id: "C11",
  title: "Takeaway",
  stage: 5,
  source: "",
  dur: DUR,
  cues,
  C: Body,
};
