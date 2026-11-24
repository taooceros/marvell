/**
 * Private helpers for C09–C11 (agent CcSceneProof): item streams, part fills, credit tokens, braces.
 * Scale-safe: every size is clamped ≥ 0.
 */
import React from "react";
import { Pt, along, clamp01 } from "../lib";
import { P, Rect, alpha } from "./kit";

export type Live = { i: number; k: number };

/** Items launched at a, a + per, … (launch ≤ b), each crossing its path in `dur` s: the ones in flight at t. */
export function stream(t: number, a: number, b: number, per: number, dur: number): Live[] {
  const out: Live[] = [];
  if (t < a || per <= 0 || dur <= 0) return out;
  const last = Math.floor((Math.min(t, b) - a) / per);
  const first = Math.max(0, Math.ceil((t - dur - a) / per));
  for (let i = first; i <= last; i++) {
    const k = (t - a - i * per) / dur;
    if (k >= 0 && k <= 1) out.push({ i, k });
  }
  return out;
}

/** Opacity of a travelling item: fades in over the first `fi` and out over the last `fo` of its path. */
export const ends = (k: number, fi = 0.06, fo = 0.1) => clamp01(Math.min(fi > 0 ? k / fi : 1, fo > 0 ? (1 - k) / fo : 1));

/** Glow 0..1 of a stage crossed between tIn and tOut: quick rise just before, slow decay after. */
export function passGlow(t: number, tIn: number, tOut: number, rise = 0.15, decay = 0.8): number {
  if (t < tIn - rise) return 0;
  if (t < tIn) return (t - tIn + rise) / rise;
  if (t <= tOut) return 1;
  return Math.max(0, 1 - (t - tOut) / decay);
}

/** Left→right fill inside a part rect (SVG). Draw it before HostNic so the part label stays on top. */
export function PartFill({
  r,
  k,
  color,
  a = 0.3,
  hatch = false,
  s = 1,
  opacity = 1,
}: {
  r: Rect;
  k: number;
  color: string;
  a?: number;
  hatch?: boolean;
  s?: number;
  opacity?: number;
}) {
  const kk = clamp01(k);
  if (kk <= 0.001 || opacity <= 0) return null;
  const pad = 6 * s;
  const x = r.x + pad;
  const y = r.y + pad;
  const w = Math.max(0, (r.w - 2 * pad) * kk);
  const h = Math.max(0, r.h - 2 * pad);
  const rx = Math.min(10 * s, w / 2);
  return (
    <g opacity={opacity}>
      <rect x={x} y={y} width={w} height={h} rx={rx} fill={alpha(color, a)} />
      {hatch && <rect x={x} y={y} width={w} height={h} rx={rx} fill="url(#ccHatch)" opacity={0.8} />}
      {kk < 0.995 && h > 10 && (
        <line x1={x + w} y1={y + 5} x2={x + w} y2={y + h - 5} stroke={color} strokeWidth={2.5} strokeLinecap="round" opacity={0.9} />
      )}
    </g>
  );
}

/**
 * Highlight a HostNic part in a colour other than its side colour (HostNic's hlColor also recolours the part at
 * rest). Draw layer "under" before HostNic (soft glow) and layer "over" after it (stroke); both fade with k.
 */
export function PartGlow({ r, k, color, s = 1, layer }: { r: Rect; k: number; color: string; s?: number; layer: "under" | "over" }) {
  const kk = clamp01(k);
  if (kk <= 0.001) return null;
  return layer === "under" ? (
    <g opacity={kk}>
      <rect x={r.x - 6} y={r.y - 6} width={r.w + 12} height={r.h + 12} rx={16 * s} fill={alpha(color, 0.24)} filter="url(#ccSoft)" />
      <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={14 * s} fill={alpha(color, 0.14)} />
    </g>
  ) : (
    <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={14 * s} fill="none" stroke={color} strokeWidth={2.5} opacity={kk * 0.95} />
  );
}

/** A credit token: small ringed disc travelling a polyline at fraction k (hidden outside 0..1). */
export function Token({ pts, k, color = P.nic, r = 7, opacity = 1 }: { pts: Pt[]; k: number; color?: string; r?: number; opacity?: number }) {
  if (k < 0 || k > 1 || opacity <= 0) return null;
  const [x, y] = along(pts, k);
  const rr = Math.max(0, r);
  return (
    <g opacity={opacity}>
      <circle cx={x} cy={y} r={rr + 7} fill={alpha(color, 0.2)} filter="url(#ccSoft)" />
      <circle cx={x} cy={y} r={rr} fill={P.bg0} stroke={color} strokeWidth={2.4} />
      <circle cx={x} cy={y} r={rr * 0.38} fill={color} />
    </g>
  );
}

/** Square bracket from x0 to x1 at y, grown from its centre by k; ticks point up (dir 1) or down (dir -1). */
export function Brace({
  x0,
  x1,
  y,
  color,
  k = 1,
  dir = 1,
  tick = 10,
  w = 2,
  opacity = 1,
}: {
  x0: number;
  x1: number;
  y: number;
  color: string;
  k?: number;
  dir?: number;
  tick?: number;
  w?: number;
  opacity?: number;
}) {
  if (k <= 0 || opacity <= 0) return null;
  const xm = (x0 + x1) / 2;
  const half = (Math.max(0, x1 - x0) / 2) * clamp01(k);
  return (
    <path
      d={`M${xm - half} ${y - dir * tick} L${xm - half} ${y} L${xm + half} ${y} L${xm + half} ${y - dir * tick}`}
      fill="none"
      stroke={color}
      strokeWidth={w}
      strokeLinecap="round"
      strokeLinejoin="round"
      opacity={opacity}
    />
  );
}

/** Open arrowhead path ending at tip, pointing along unit vector (ux, uy). */
export function headD(tip: Pt, ux: number, uy: number, h = 10, spread = 0.45): string {
  const rot = (a: number): Pt => [ux * Math.cos(a) - uy * Math.sin(a), ux * Math.sin(a) + uy * Math.cos(a)];
  const [ax, ay] = rot(spread);
  const [bx, by] = rot(-spread);
  return `M${tip[0] - h * ax} ${tip[1] - h * ay} L${tip[0]} ${tip[1]} L${tip[0] - h * bx} ${tip[1] - h * by}`;
}
