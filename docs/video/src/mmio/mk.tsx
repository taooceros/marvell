/**
 * MmioLatencyBreakdown helpers on top of the CcnicPaperStory kit (../ccnic/kit): the rail, one colour per clock
 * domain, the running read-path diagram (host chip ⇄ PCIe ⇄ card), the time bar that gets cut into stages, and a few
 * SVG primitives (text, counter badge, dimension bracket, x-y plot). All SVG helpers go inside the kit's <Stage>.
 *
 * Layout bands (1920×1080) follow the kit: title block top-left (y < 120), rail top-right, content y 150..890,
 * captions bottom (y ≥ 905). Default read path: main row y 330..420, SDP branch y 190..262.
 */
import React from "react";
import { FONT, MONO, Pt, clamp01, lerp } from "../lib";
import { P, alpha, TAB } from "../ccnic/kit";

export const MMIO_STAGES: { label: string; color: string }[] = [
  { label: "The case", color: P.gold },
  { label: "First cut", color: P.push },
  { label: "Second cut", color: P.coh },
  { label: "Traps", color: P.bad },
  { label: "Verdict", color: P.ink },
];

/** One colour per clock domain / stage, fixed for the whole video. */
export const D = {
  core: "#8fb4ff", // host core (3.9 GHz)
  mesh: "#5fe3c8", // host mesh: CHA, M2PCIe (2.2 GHz)
  io: "#b9a3ff", // IRP + IIO (1 GHz) and the root port
  link: "#f0a6c8", // host PCIe layers + link + PEM0 + SDP path
  card: "#ffb470", // card: BAR4 window, IOBN, L3
  ddr: "#e48a52", // card DDR5
  dark: "#070b14", // black-box fill
  node: "#0b1220", // opaque node fill (cells travelling behind a node are hidden)
};

// ---------------------------------------------------------------- SVG text + small primitives
export function T({
  x,
  y,
  children,
  size = 24,
  color = P.ink,
  weight = 400,
  anchor = "start",
  opacity = 1,
  mono = false,
  spacing = 0,
}: {
  x: number;
  y: number;
  children: React.ReactNode;
  size?: number;
  color?: string;
  weight?: number;
  anchor?: "start" | "middle" | "end";
  opacity?: number;
  mono?: boolean;
  spacing?: number;
}) {
  if (opacity <= 0) return null;
  return (
    <text
      x={x}
      y={y}
      fontSize={size}
      fill={color}
      fontWeight={weight}
      textAnchor={anchor}
      dominantBaseline="central"
      opacity={opacity}
      fontFamily={mono ? MONO : FONT}
      letterSpacing={spacing}
      style={TAB}
    >
      {children}
    </text>
  );
}

/** Counter badge: small mono pill centred at (x, y), e.g. a PMU count "1.0000". */
export function Badge({
  x,
  y,
  text,
  color = P.push,
  k = 1,
  size = 18,
  solid = false,
}: {
  x: number;
  y: number;
  text: string;
  color?: string;
  k?: number;
  size?: number;
  solid?: boolean;
}) {
  if (k <= 0) return null;
  const w = text.length * size * 0.62 + 22;
  const h = size + 14;
  return (
    <g opacity={k} transform={`translate(${x} ${y + (1 - k) * 8})`}>
      <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={h / 2} fill={solid ? color : alpha(color, 0.12)} stroke={alpha(color, 0.7)} strokeWidth={1.5} />
      <text x={0} y={1} fontSize={size} fontFamily={MONO} fill={solid ? P.bg0 : color} textAnchor="middle" dominantBaseline="central" fontWeight={600}>
        {text}
      </text>
    </g>
  );
}

/** Horizontal dimension bracket from x0 to x1 at y, label above (dir -1) or below (dir 1). */
export function Dim({
  x0,
  x1,
  y,
  label,
  color = P.gold,
  k = 1,
  dir = -1,
  size = 24,
  weight = 700,
}: {
  x0: number;
  x1: number;
  y: number;
  label?: React.ReactNode;
  color?: string;
  k?: number;
  dir?: -1 | 1;
  size?: number;
  weight?: number;
}) {
  if (k <= 0) return null;
  const xm = (x0 + x1) / 2;
  const half = ((x1 - x0) / 2) * clamp01(k * 1.25);
  return (
    <g opacity={clamp01(k * 2)}>
      <line x1={xm - half} y1={y} x2={xm + half} y2={y} stroke={color} strokeWidth={2} />
      <line x1={xm - half} y1={y - 9} x2={xm - half} y2={y + 9} stroke={color} strokeWidth={2} />
      <line x1={xm + half} y1={y - 9} x2={xm + half} y2={y + 9} stroke={color} strokeWidth={2} />
      {label !== undefined && (
        <T x={xm} y={y + dir * (size * 0.5 + 14)} anchor="middle" size={size} weight={weight} color={color} opacity={clamp01((k - 0.5) * 2)}>
          {label}
        </T>
      )}
    </g>
  );
}

/** Check mark (two marks = "measured two ways"). */
export function Check({ x, y, k = 1, color = P.push, s = 1 }: { x: number; y: number; k?: number; color?: string; s?: number }) {
  if (k <= 0) return null;
  return (
    <path
      d={`M${x - 9 * s} ${y} L${x - 2 * s} ${y + 7 * s} L${x + 10 * s} ${y - 8 * s}`}
      fill="none"
      stroke={color}
      strokeWidth={3}
      strokeLinecap="round"
      strokeLinejoin="round"
      opacity={k}
    />
  );
}

/** Cross mark. */
export function Cross({ x, y, k = 1, color = P.bad, s = 1 }: { x: number; y: number; k?: number; color?: string; s?: number }) {
  if (k <= 0) return null;
  return (
    <g stroke={color} strokeWidth={3.2} strokeLinecap="round" opacity={k}>
      <line x1={x - 9 * s} y1={y - 9 * s} x2={x + 9 * s} y2={y + 9 * s} />
      <line x1={x - 9 * s} y1={y + 9 * s} x2={x + 9 * s} y2={y - 9 * s} />
    </g>
  );
}

// ---------------------------------------------------------------- the read path
export type NodeId = "core" | "cha" | "m2p" | "irp" | "iio" | "rp" | "pem" | "sdp" | "iobn" | "tad" | "ddr";
export type Rect = { x: number; y: number; w: number; h: number };
export const NODES: NodeId[] = ["core", "cha", "m2p", "irp", "iio", "rp", "pem", "sdp", "iobn", "tad", "ddr"];

const BASE: Record<NodeId, [number, number, number, number]> = {
  core: [70, 330, 130, 90],
  cha: [255, 330, 140, 90],
  m2p: [450, 330, 140, 90],
  irp: [640, 330, 100, 90],
  iio: [780, 330, 100, 90],
  rp: [930, 330, 120, 90],
  pem: [1200, 330, 100, 90],
  sdp: [1345, 190, 130, 72],
  iobn: [1345, 330, 130, 90],
  tad: [1525, 330, 140, 90],
  ddr: [1715, 330, 135, 90],
};

export const NODE_LABEL: Record<NodeId, [string, string?]> = {
  core: ["Core", "3.9 GHz"],
  cha: ["CHA", "TOR"],
  m2p: ["M2PCIe₁"],
  irp: ["IRP₁"],
  iio: ["IIO₁"],
  rp: ["Root port"],
  pem: ["PEM0"],
  sdp: ["SDP CSR", "register"],
  iobn: ["IOBN", "BAR4 window"],
  tad: ["L3", "48 MiB"],
  ddr: ["DDR5"],
};

export const NODE_DOMAIN: Record<NodeId, keyof typeof D> = {
  core: "core",
  cha: "mesh",
  m2p: "mesh",
  irp: "io",
  iio: "io",
  rp: "io",
  pem: "link",
  sdp: "link",
  iobn: "card",
  tad: "card",
  ddr: "ddr",
};

export interface RpGeom {
  n: Record<NodeId, Rect>;
  s: number;
  /** request lane (→) and reply lane (←) y */
  reqY: number;
  repY: number;
  midY: number;
  /** PCIe link span */
  linkX0: number;
  linkX1: number;
  at: (x: number, y: number) => Pt;
}

/** Read-path geometry: default (s = 1) spans x 70..1850; scaled about (960, 375), then shifted. */
export function rpGeom(s = 1, dx = 0, dy = 0): RpGeom {
  const at = (x: number, y: number): Pt => [960 + (x - 960) * s + dx, 375 + (y - 375) * s + dy];
  const n = {} as Record<NodeId, Rect>;
  for (const id of NODES) {
    const [x, y, w, h] = BASE[id];
    const [X, Y] = at(x, y);
    n[id] = { x: X, y: Y, w: Math.max(0, w * s), h: Math.max(0, h * s) };
  }
  return { n, s, reqY: at(0, 361)[1], repY: at(0, 389)[1], midY: at(0, 375)[1], linkX0: at(1050, 0)[0], linkX1: at(1200, 0)[0], at };
}

export const rcx = (r: Rect) => r.x + r.w / 2;
export const rcy = (r: Rect) => r.y + r.h / 2;

/** Request route core → endpoint (req lane) and reply route endpoint → core (rep lane). */
export function rpRoute(g: RpGeom, to: NodeId, dir: "req" | "rep", from: NodeId = "core"): Pt[] {
  const a = g.n[from];
  const b = g.n[to];
  const x0 = a.x + a.w;
  if (to === "sdp") {
    const xr = g.at(1316, 0)[0];
    const xp = g.at(1330, 0)[0];
    const yy = rcy(b);
    const pts: Pt[] = dir === "req" ? [[x0, g.reqY], [xr, g.reqY], [xr, yy - 10 * g.s], [rcx(b), yy - 10 * g.s]] : [[rcx(b), yy + 10 * g.s], [xp, yy + 10 * g.s], [xp, g.repY], [x0, g.repY]];
    return pts;
  }
  return dir === "req" ? [[x0, g.reqY], [rcx(b), g.reqY]] : [[rcx(b), g.repY], [x0, g.repY]];
}

/** Point of a round trip at progress k (0..0.5 out on the request lane, 0.5..1 back on the reply lane). */
export function tripRoute(g: RpGeom, to: NodeId, from: NodeId = "core"): Pt[] {
  const req = rpRoute(g, to, "req", from);
  const rep = rpRoute(g, to, "rep", from);
  return [...req, ...rep];
}

export type BlackBox = { from: NodeId; to: NodeId; label?: React.ReactNode; k?: number; tall?: boolean; color?: string };

/**
 * The running read-path diagram (SVG, inside <Stage>).
 * - `hl[node]` 0..1 lights a node in its domain colour (or `hlColor`); `dim[node]` 0..1 fades it; `hide[node]` hides it.
 * - `boxes`: black boxes drawn over node ranges (from..to), `k` = opacity, `tall` also covers the SDP row.
 * - `bands` 0..1 draws the clock-domain bands under the row; `heads` 0..1 shows the HOST / CARD headings.
 * - `under` renders below the nodes (travelling cells hide behind node boxes); children render on top.
 */
export function ReadPath({
  g,
  opacity = 1,
  hl = {},
  hlColor = {},
  dim = {},
  hide = {},
  labels = {},
  subs = {},
  boxes = [],
  bands = 0,
  heads = 1,
  link = 1,
  under,
  children,
}: {
  g: RpGeom;
  opacity?: number;
  hl?: Partial<Record<NodeId, number>>;
  hlColor?: Partial<Record<NodeId, string>>;
  dim?: Partial<Record<NodeId, number>>;
  hide?: Partial<Record<NodeId, number>>;
  labels?: Partial<Record<NodeId, string>>;
  subs?: Partial<Record<NodeId, string>>;
  boxes?: BlackBox[];
  bands?: number;
  heads?: number;
  link?: number;
  under?: React.ReactNode;
  children?: React.ReactNode;
}) {
  if (opacity <= 0) return null;
  const s = g.s;
  const fs = (v: number) => Math.max(11, v * s);
  const vis = (id: NodeId) => 1 - (hide[id] ?? 0);
  const n = g.n;
  // connectors: hairlines between neighbours on the main row + the SDP branch
  const row: NodeId[] = ["core", "cha", "m2p", "irp", "iio", "rp"];
  const card: NodeId[] = ["pem", "iobn", "tad", "ddr"];
  const conn = (a: NodeId, b: NodeId) => {
    const op = Math.min(vis(a), vis(b));
    if (op <= 0) return null;
    return <line key={a + b} x1={n[a].x + n[a].w} y1={g.midY} x2={n[b].x} y2={g.midY} stroke={P.hair2} strokeWidth={1.5} opacity={op} />;
  };
  const bandRow: { ids: NodeId[]; key: keyof typeof D; text: string }[] = [
    { ids: ["core"], key: "core", text: "core · 3.9 GHz" },
    { ids: ["cha", "m2p"], key: "mesh", text: "mesh · 2.2 GHz" },
    { ids: ["irp", "iio", "rp"], key: "io", text: "IO · 1 GHz" },
    { ids: ["pem", "iobn", "tad", "ddr"], key: "card", text: "card · 2 GHz" },
  ];
  const bandY = g.at(0, 446)[1];
  return (
    <g opacity={opacity} fontFamily={FONT}>
      {/* headings */}
      {heads > 0 && (
        <g opacity={heads}>
          <T x={n.core.x} y={g.at(0, 300)[1]} size={fs(16)} color={alpha(D.core, 0.9)} weight={600} spacing={2.4}>
            HOST
          </T>
          <T x={n.pem.x} y={g.at(0, 300)[1]} size={fs(16)} color={alpha(D.card, 0.9)} weight={600} spacing={2.4}>
            CARD
          </T>
        </g>
      )}
      {row.slice(1).map((b, i) => conn(row[i], b))}
      {card.slice(1).map((b, i) => conn(card[i], b))}
      {vis("sdp") > 0 && vis("pem") > 0 && (
        <path
          d={`M${n.pem.x + n.pem.w} ${g.midY} L${g.at(1323, 0)[0]} ${g.midY} L${g.at(1323, 0)[0]} ${rcy(n.sdp)} L${n.sdp.x} ${rcy(n.sdp)}`}
          fill="none"
          stroke={P.hair2}
          strokeWidth={1.5}
          opacity={Math.min(vis("sdp"), vis("pem"))}
        />
      )}
      {/* PCIe link */}
      {link > 0 && vis("rp") > 0 && (
        <g opacity={link * vis("rp")}>
          {[-15, -5, 5, 15].map((o) => (
            <line key={o} x1={g.linkX0} y1={g.midY + o * s} x2={g.linkX1} y2={g.midY + o * s} stroke={alpha(D.link, 0.55)} strokeWidth={2} />
          ))}
          <T x={(g.linkX0 + g.linkX1) / 2} y={g.midY - 40 * s} anchor="middle" size={fs(16)} color={P.sub} weight={600} spacing={1}>
            PCIe Gen5 ×4
          </T>
        </g>
      )}
      {under}
      {NODES.map((id) => {
        const r = n[id];
        const op = vis(id) * (1 - 0.72 * (dim[id] ?? 0));
        if (op <= 0) return null;
        const h = hl[id] ?? 0;
        const col = hlColor[id] ?? D[NODE_DOMAIN[id]];
        const [lab, sub0] = NODE_LABEL[id];
        const sub = subs[id] ?? sub0;
        return (
          <g key={id} opacity={op}>
            {h > 0 && <rect x={r.x - 7} y={r.y - 7} width={r.w + 14} height={r.h + 14} rx={18 * s} fill={alpha(col, 0.2 * h)} filter="url(#ccSoft)" />}
            <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={14 * s} fill={D.node} />
            <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={14 * s} fill={alpha(col, 0.07 + 0.13 * h)} stroke={alpha(col, 0.5 + 0.45 * h)} strokeWidth={1.5 + h} />
            <T x={rcx(r)} y={rcy(r) + (sub ? -10 * s : 0)} anchor="middle" size={fs(24)} weight={h > 0.5 ? 600 : 500}>
              {labels[id] ?? lab}
            </T>
            {sub && (
              <T x={rcx(r)} y={rcy(r) + 20 * s} anchor="middle" size={fs(16)} color={P.sub}>
                {sub}
              </T>
            )}
          </g>
        );
      })}
      {/* clock-domain bands */}
      {bands > 0 && (
        <g opacity={bands}>
          {bandRow.map((b) => {
            const x0 = n[b.ids[0]].x;
            const x1 = n[b.ids[b.ids.length - 1]].x + n[b.ids[b.ids.length - 1]].w;
            return (
              <g key={b.key}>
                <rect x={x0} y={bandY} width={x1 - x0} height={6 * s} rx={3 * s} fill={D[b.key]} opacity={0.85} />
                <T x={(x0 + x1) / 2} y={bandY + 26 * s} anchor="middle" size={fs(18)} color={D[b.key]} weight={600}>
                  {b.text}
                </T>
              </g>
            );
          })}
        </g>
      )}
      {/* black boxes */}
      {boxes.map((b, i) => {
        const k = b.k ?? 1;
        if (k <= 0) return null;
        const x0 = n[b.from].x - 16 * s;
        const x1 = n[b.to].x + n[b.to].w + 16 * s;
        const y0 = b.tall ? g.at(0, 172)[1] : g.at(0, 312)[1];
        const y1 = g.at(0, 438)[1];
        const col = b.color ?? P.faint;
        return (
          <g key={i} opacity={k}>
            <rect x={x0} y={y0} width={x1 - x0} height={y1 - y0} rx={18 * s} fill={D.dark} opacity={0.96} />
            <rect x={x0} y={y0} width={x1 - x0} height={y1 - y0} rx={18 * s} fill="url(#mmHatch)" opacity={0.55} />
            <rect x={x0} y={y0} width={x1 - x0} height={y1 - y0} rx={18 * s} fill="none" stroke={alpha(col, 0.8)} strokeWidth={1.5} strokeDasharray="8 7" />
            {b.label !== undefined && (
              <T x={(x0 + x1) / 2} y={(y0 + y1) / 2} anchor="middle" size={fs(34)} weight={700} color={P.ink}>
                {b.label}
              </T>
            )}
          </g>
        );
      })}
      {children}
    </g>
  );
}

/** Hatch pattern for black boxes / unknown time; put once inside a <Stage>. */
export function MmDefs() {
  return (
    <defs>
      <pattern id="mmHatch" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <line x1="0" y1="0" x2="0" y2="12" stroke="rgba(150,170,210,0.16)" strokeWidth="3" />
      </pattern>
    </defs>
  );
}

/** A TOR table (5 rows) drawn under the CHA node; `busy` 0..1 lights row 2 (the read's entry). */
export function TorTable({ g, k = 1, busy = 0 }: { g: RpGeom; k?: number; busy?: number }) {
  if (k <= 0) return null;
  const r = g.n.cha;
  const s = g.s;
  const x = r.x + 14 * s;
  const w = r.w - 28 * s;
  const y0 = r.y + r.h + 22 * s;
  return (
    <g opacity={k}>
      <rect x={r.x} y={y0 - 12 * s} width={r.w} height={98 * s} rx={10 * s} fill={D.node} stroke={alpha(D.mesh, 0.45)} strokeWidth={1.2} />
      {[0, 1, 2, 3, 4].map((i) => (
        <rect
          key={i}
          x={x}
          y={y0 + i * 15 * s}
          width={w}
          height={10 * s}
          rx={3 * s}
          fill={i === 2 ? alpha(P.gold, 0.15 + 0.75 * busy) : "rgba(255,255,255,0.06)"}
          stroke={i === 2 && busy > 0 ? alpha(P.gold, busy) : "none"}
        />
      ))}
    </g>
  );
}

// ---------------------------------------------------------------- the time bar
export type Seg = {
  v: number;
  color: string;
  /** 0..1: colour fill over the dark "unknown" base */
  reveal?: number;
  /** text centred inside the bar (only if wide enough) */
  inside?: string;
  dashed?: boolean;
  op?: number;
};

/** x ranges of each segment (left to right). */
export function barLayout(x: number, w: number, max: number, segs: Seg[]): { x0: number; x1: number }[] {
  let cur = x;
  return segs.map((sg) => {
    const ww = (sg.v / max) * w;
    const r = { x0: cur, x1: cur + ww };
    cur += ww;
    return r;
  });
}

/** Horizontal stacked bar (SVG). `grow` 0..1 draws it left to right. Unknown parts are dark hatch. */
export function TimeBar({
  x,
  y,
  w,
  h = 44,
  max,
  segs,
  grow = 1,
  opacity = 1,
  size = 22,
}: {
  x: number;
  y: number;
  w: number;
  h?: number;
  max: number;
  segs: Seg[];
  grow?: number;
  opacity?: number;
  size?: number;
}) {
  if (opacity <= 0 || grow <= 0) return null;
  const lay = barLayout(x, w, max, segs);
  const xEnd = lay.length ? lay[lay.length - 1].x1 : x;
  const clipX = lerp(x, xEnd, clamp01(grow));
  const id = `tb${Math.round(x)}_${Math.round(y)}_${Math.round(w)}`;
  return (
    <g opacity={opacity}>
      <defs>
        <clipPath id={id}>
          <rect x={x - 2} y={y - 4} width={Math.max(0, clipX - x + 4)} height={h + 8} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${id})`}>
        {segs.map((sg, i) => {
          const { x0, x1 } = lay[i];
          const ww = Math.max(0, x1 - x0 - 2);
          const rev = sg.reveal ?? 1;
          const op = sg.op ?? 1;
          return (
            <g key={i} opacity={op}>
              {sg.dashed ? (
                <rect x={x0 + 1} y={y + 1} width={ww} height={h - 2} rx={6} fill={alpha(sg.color, 0.14 * rev)} stroke={alpha(sg.color, 0.4 + 0.6 * rev)} strokeWidth={2} strokeDasharray="7 6" />
              ) : (
                <>
                  <rect x={x0 + 1} y={y} width={ww} height={h} rx={6} fill={D.dark} stroke={P.hair2} strokeWidth={1} />
                  <rect x={x0 + 1} y={y} width={ww} height={h} rx={6} fill="url(#mmHatch)" opacity={1 - rev} />
                  <rect x={x0 + 1} y={y} width={ww} height={h} rx={6} fill={sg.color} opacity={0.88 * rev} />
                </>
              )}
              {sg.inside && ww > sg.inside.length * size * 0.56 + 16 && (
                <T x={x0 + 1 + ww / 2} y={y + h / 2 + 1} anchor="middle" size={size} weight={700} color={rev > 0.5 && !sg.dashed ? P.bg0 : P.ink}>
                  {sg.inside}
                </T>
              )}
            </g>
          );
        })}
      </g>
    </g>
  );
}

// ---------------------------------------------------------------- x-y plot
export interface PlotGeom {
  x: number;
  y: number;
  w: number;
  h: number;
  sx: (v: number) => number;
  sy: (v: number) => number;
}

export function plotGeom(x: number, y: number, w: number, h: number, xr: [number, number], yr: [number, number]): PlotGeom {
  return {
    x,
    y,
    w,
    h,
    sx: (v) => x + ((v - xr[0]) / (xr[1] - xr[0])) * w,
    sy: (v) => y + h - ((v - yr[0]) / (yr[1] - yr[0])) * h,
  };
}

/** Axes with ticks; labels in ≥ 18 px. */
export function Axes({
  p,
  xTicks,
  yTicks,
  xLabel,
  yLabel,
  xFmt = (v) => String(v),
  yFmt = (v) => String(v),
  opacity = 1,
}: {
  p: PlotGeom;
  xTicks: number[];
  yTicks: number[];
  xLabel?: string;
  yLabel?: string;
  xFmt?: (v: number) => string;
  yFmt?: (v: number) => string;
  opacity?: number;
}) {
  if (opacity <= 0) return null;
  return (
    <g opacity={opacity}>
      {yTicks.map((v) => (
        <g key={`y${v}`}>
          <line x1={p.x} y1={p.sy(v)} x2={p.x + p.w} y2={p.sy(v)} stroke={P.hair} strokeWidth={1} />
          <T x={p.x - 12} y={p.sy(v)} anchor="end" size={18} color={P.sub}>
            {yFmt(v)}
          </T>
        </g>
      ))}
      <line x1={p.x} y1={p.y + p.h} x2={p.x + p.w} y2={p.y + p.h} stroke={P.hair2} strokeWidth={1.5} />
      <line x1={p.x} y1={p.y} x2={p.x} y2={p.y + p.h} stroke={P.hair2} strokeWidth={1.5} />
      {xTicks.map((v) => (
        <g key={`x${v}`}>
          <line x1={p.sx(v)} y1={p.y + p.h} x2={p.sx(v)} y2={p.y + p.h + 7} stroke={P.hair2} strokeWidth={1.5} />
          <T x={p.sx(v)} y={p.y + p.h + 24} anchor="middle" size={18} color={P.sub}>
            {xFmt(v)}
          </T>
        </g>
      ))}
      {xLabel && (
        <T x={p.x + p.w / 2} y={p.y + p.h + 58} anchor="middle" size={20} color={P.sub}>
          {xLabel}
        </T>
      )}
      {yLabel && (
        <T x={p.x} y={p.y - 30} anchor="start" size={20} color={P.sub}>
          {yLabel}
        </T>
      )}
    </g>
  );
}
