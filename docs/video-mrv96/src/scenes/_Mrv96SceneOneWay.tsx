/**
 * Private helpers of agent Mrv96SceneOneWay: S03 (Exp 1 · Chasing the frontier), S04 (Exp 2 · Where the core reads)
 * share one scrolling window onto the request ring (kit RingStrip); S05 (Exp 3 · Inside the slow loop) uses the
 * path slicing and stopwatch at the end of this file.
 */
import { C, K, MONO, Pt, clamp01, lerp } from "../lib";
import { CellState, RingStrip } from "./kit";

// ---------------------------------------------------------------- shared strip geometry (identical in S03/S04)
export const RS = { x: 110, y: 262, n: 30, cw: 46, ch: 80, gap: 8 };
export const PITCH = RS.cw + RS.gap;
/** Right edge of the strip window. */
export const RS_RIGHT = RS.x + RS.n * PITCH - RS.gap;

/** Screen x (slot left edge) of ABSOLUTE slot a when the newest slot index is `head` (see ScrollStrip). */
export const absX = (a: number, head: number) => RS.x + (a - head + RS.n - 1) * PITCH;
/** Screen centre x of ABSOLUTE slot a. */
export const absC = (a: number, head: number) => absX(a, head) + RS.cw / 2;
/** Deterministic hash → [0, 1) per integer. */
export const hash01 = (a: number) => {
  let h = Math.imul(a ^ 0x9e3779b9, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
};

/**
 * Scrolling strip: absolute slot a is drawn at window position a - head + n (head = newest slot, fractional).
 * `state(a)` gets ABSOLUTE slot indices. The strip is clipped to the window so cells slide in/out smoothly.
 */
export function ScrollStrip({
  head,
  state,
  flicker,
  clipId,
  opacity = 1,
}: {
  head: number;
  state: (a: number) => CellState;
  flicker: number;
  clipId: string;
  opacity?: number;
}) {
  const base = Math.floor(head) - RS.n; // absolute slot drawn at window position 0 (before shift)
  const frac = head - Math.floor(head);
  return (
    <g opacity={opacity}>
      <defs>
        <clipPath id={clipId}>
          <rect x={RS.x - 2} y={RS.y - 4} width={RS_RIGHT - RS.x + 4} height={RS.ch + 8} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`}>
        <g transform={`translate(${-frac * PITCH}, 0)`}>
          <RingStrip x={RS.x} y={RS.y} n={RS.n + 1} cw={RS.cw} ch={RS.ch} gap={RS.gap} flicker={flicker}
            state={(i) => state(base + i + 1)} />
        </g>
      </g>
    </g>
  );
}

/** Card-core cursor (kit style triangle) above the strip at screen x; `tag` printed above. */
export function Cursor({
  x,
  color,
  tag,
  opacity = 1,
  size = 26,
  y = RS.y - 12,
}: {
  x: number;
  color: string;
  tag: string;
  opacity?: number;
  size?: number;
  y?: number;
}) {
  if (opacity <= 0) return null;
  return (
    <g transform={`translate(${x}, ${y})`} opacity={opacity}>
      <polygon points="-14,-22 14,-22 0,0" fill={color} />
      <text y={-32} textAnchor="middle" fontSize={size} fontWeight={800} fill={color} fontFamily={MONO}>
        {tag}
      </text>
    </g>
  );
}

/** Red ✕ centred at (x, y). */
export function XMark({ x, y, s = 14, color = C.red, opacity = 1, w = 5 }: { x: number; y: number; s?: number; color?: string; opacity?: number; w?: number }) {
  if (opacity <= 0) return null;
  return (
    <g opacity={opacity} stroke={color} strokeWidth={w} strokeLinecap="round">
      <line x1={x - s} y1={y - s} x2={x + s} y2={y + s} />
      <line x1={x - s} y1={y + s} x2={x + s} y2={y - s} />
    </g>
  );
}

/** Green ✓ centred at (x, y). */
export function CheckMark({ x, y, s = 16, color = C.green, opacity = 1, w = 6 }: { x: number; y: number; s?: number; color?: string; opacity?: number; w?: number }) {
  if (opacity <= 0) return null;
  return (
    <polyline points={`${x - s},${y} ${x - s * 0.3},${y + s * 0.7} ${x + s},${y - s * 0.8}`} fill="none" stroke={color}
      strokeWidth={w} strokeLinecap="round" strokeLinejoin="round" opacity={opacity} />
  );
}

/** Small clock/timer arc: a ring that fills clockwise with k ∈ [0,1], centred at (x, y). */
export function TimerArc({ x, y, r = 22, k, color, opacity = 1 }: { x: number; y: number; r?: number; k: number; color: string; opacity?: number }) {
  if (opacity <= 0) return null;
  const kk = clamp01(k);
  const a = kk * Math.PI * 2 - Math.PI / 2;
  const large = kk > 0.5 ? 1 : 0;
  const ex = x + r * Math.cos(a);
  const ey = y + r * Math.sin(a);
  return (
    <g opacity={opacity}>
      <circle cx={x} cy={y} r={r} fill="rgba(11,16,32,0.9)" stroke={C.line} strokeWidth={4} />
      {kk > 0.001 && kk < 0.999 && (
        <path d={`M${x} ${y - r} A${r} ${r} 0 ${large} 1 ${ex} ${ey}`} fill="none" stroke={color} strokeWidth={5} strokeLinecap="round" />
      )}
      {kk >= 0.999 && <circle cx={x} cy={y} r={r} fill="none" stroke={color} strokeWidth={5} />}
      <line x1={x} y1={y} x2={x + (r - 7) * Math.cos(a)} y2={y + (r - 7) * Math.sin(a)} stroke={color} strokeWidth={3} strokeLinecap="round" />
    </g>
  );
}

/** The host core writing into the strip's newest end (right): host box, MOVDIR64B arrow, one packet per beat. */
export function HostWriter({ rt, opacity = 1 }: { rt: number; opacity?: number }) {
  const bx = RS_RIGHT + 22;
  const my = RS.y + RS.ch / 2;
  const pk = (rt * 1.4) % 1;
  return (
    <g opacity={opacity}>
      <text x={bx + 62} y={RS.y - 18} textAnchor="middle" fontSize={22} fontWeight={700} fill={K.fwd} fontFamily={MONO}>
        MOVDIR64B
      </text>
      <rect x={bx} y={RS.y} width={126} height={RS.ch} rx={12} fill="#141b2e" stroke={K.host} strokeWidth={3} />
      <text x={bx + 63} y={RS.y + 33} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.host}>host</text>
      <text x={bx + 63} y={RS.y + 62} textAnchor="middle" fontSize={22} fill={K.host} fontFamily={MONO}>core 40</text>
      <line x1={bx - 2} y1={my} x2={RS_RIGHT + 2} y2={my} stroke={K.fwd} strokeWidth={4} />
      <rect x={lerp(bx - 4, RS_RIGHT - 20, pk) - 7} y={my - 7} width={14} height={14} rx={3} fill={K.fwd} opacity={1 - pk * 0.5} />
    </g>
  );
}

/** One legend entry: a small ring cell of the given state + label (SVG). */
export function LegendCell({ x, y, s, label, opacity = 1 }: { x: number; y: number; s: CellState; label: string; opacity?: number }) {
  if (opacity <= 0) return null;
  const w = 26;
  const h = 40;
  const cell =
    s === "consumed" ? <rect x={x} y={y} width={w} height={h} rx={5} fill={K.consumed} stroke={C.line} strokeWidth={1.5} />
    : s === "landed" ? <rect x={x} y={y} width={w} height={h} rx={5} fill={K.landed} opacity={0.88} stroke={K.landed} strokeWidth={2} />
    : s === "inflight" ? <rect x={x} y={y} width={w} height={h} rx={5} fill="url(#hatchRed)" stroke={K.inflight} strokeWidth={2} />
    : s === "hole" ? <rect x={x} y={y} width={w} height={h} rx={5} fill="#0b1020" stroke={K.inflight} strokeWidth={2.5} strokeDasharray="5 4" />
    : <rect x={x} y={y} width={w} height={h} rx={5} fill="none" stroke={C.line} strokeWidth={1.5} strokeDasharray="4 5" />;
  return (
    <g opacity={opacity}>
      {cell}
      <text x={x + w + 12} y={y + h / 2 + 8} fontSize={24} fill={C.text}>
        {label}
      </text>
    </g>
  );
}

// ---------------------------------------------------------------- S05: loop paths
/** Cumulative lengths of a polyline's vertices (cum[0] = 0, cum[last] = total length). */
export function cumLen(pts: Pt[]): number[] {
  const cum = [0];
  for (let i = 0; i + 1 < pts.length; i++) cum.push(cum[i] + Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]));
  return cum;
}

/** Point at path length s along pts (cum from cumLen). */
export function atLen(pts: Pt[], cum: number[], s: number): Pt {
  const v = Math.max(0, Math.min(cum[cum.length - 1], s));
  let i = 0;
  while (i + 2 < cum.length && cum[i + 1] < v) i++;
  const d = cum[i + 1] - cum[i];
  const u = d > 0 ? (v - cum[i]) / d : 0;
  return [lerp(pts[i][0], pts[i + 1][0], u), lerp(pts[i][1], pts[i + 1][1], u)];
}

/** The piece of a polyline between path lengths a < b (for highlighting a stretch of the loop). */
export function slicePath(pts: Pt[], cum: number[], a: number, b: number): Pt[] {
  const out: Pt[] = [atLen(pts, cum, a)];
  for (let i = 1; i + 1 < pts.length; i++) if (cum[i] > a && cum[i] < b) out.push(pts[i]);
  out.push(atLen(pts, cum, Math.max(a, b)));
  return out;
}

/** Stopwatch icon centred at (x, y); the hand turns with `spin` (radians). */
export function Stopwatch({ x, y, r = 22, spin = 0, color = K.timer, opacity = 1 }: { x: number; y: number; r?: number; spin?: number; color?: string; opacity?: number }) {
  if (opacity <= 0) return null;
  return (
    <g opacity={opacity}>
      <rect x={x - 6} y={y - r - 12} width={12} height={8} rx={2} fill={color} />
      <circle cx={x} cy={y} r={r} fill="rgba(11,16,32,0.92)" stroke={color} strokeWidth={4} />
      <line x1={x} y1={y} x2={x + (r - 7) * Math.sin(spin)} y2={y - (r - 7) * Math.cos(spin)} stroke={color} strokeWidth={3.5} strokeLinecap="round" />
      <circle cx={x} cy={y} r={3.5} fill={color} />
    </g>
  );
}
