/**
 * Private helpers for C06–C08 (scene agent CcSceneCost): cell streams, centred tags, X marks, the IOMMU shield,
 * credit tokens and bulk (DMA) blocks. Sizes clamp at 0 so they are safe at any scale.
 */
import React from "react";
import { FONT, Pt, along, clamp01 } from "../lib";
import { P, alpha } from "./kit";

export type Emit = { i: number; k: number };

/** Items emitted every `per` s in [start, stop), each travelling `dur` s: those in flight at t (k in 0..1). */
export function stream(t: number, start: number, stop: number, per: number, dur: number): Emit[] {
  const out: Emit[] = [];
  if (t < start || per <= 0 || dur <= 0) return out;
  const last = Math.ceil((stop - start) / per) - 1;
  const i0 = Math.max(0, Math.ceil((t - dur - start) / per));
  const i1 = Math.min(last, Math.floor((t - start) / per));
  for (let i = i0; i <= i1; i++) {
    const k = (t - start - i * per) / dur;
    if (k >= 0 && k <= 1) out.push({ i, k });
  }
  return out;
}

/** Opacity that fades a travelling item over the last `tail` of its trip, so it lands instead of popping out. */
export const landOp = (k: number, tail = 0.1) => clamp01((1 - k) / tail);

/** Sawtooth phase 0..1 of a periodic event that started at t0 (0 before t0). */
export const phase = (t: number, t0: number, per: number) => (t < t0 ? 0 : ((t - t0) % per) / per);

/** Same look as the kit Tag, but centred on x (HTML, outside <Stage>). */
export function TagC({
  x,
  y,
  children,
  color = P.sub,
  opacity = 1,
  solid = false,
  size = 15,
}: {
  x: number;
  y: number;
  children: React.ReactNode;
  color?: string;
  opacity?: number;
  solid?: boolean;
  size?: number;
}) {
  if (opacity <= 0) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        transform: "translateX(-50%)",
        opacity,
        fontFamily: FONT,
        fontSize: size,
        fontWeight: 600,
        letterSpacing: 1.6,
        textTransform: "uppercase",
        color: solid ? P.bg0 : color,
        background: solid ? color : alpha(color, 0.08),
        border: `1px solid ${alpha(color, solid ? 1 : 0.45)}`,
        borderRadius: 999,
        padding: "5px 14px 4px",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </div>
  );
}

/** X mark (SVG) centred at (x, y); grows in with k. */
export function XMark({ x, y, r = 10, color = P.bad, k = 1, w = 3.5 }: { x: number; y: number; r?: number; color?: string; k?: number; w?: number }) {
  if (k <= 0) return null;
  const q = Math.max(0, r * (0.4 + 0.6 * clamp01(k)));
  return (
    <g opacity={clamp01(k)} stroke={color} strokeWidth={w} strokeLinecap="round">
      <line x1={x - q} y1={y - q} x2={x + q} y2={y + q} />
      <line x1={x - q} y1={y + q} x2={x + q} y2={y - q} />
    </g>
  );
}

/** Shield glyph (SVG) centred at (x, y), total height 2·h, with a check mark; `glow` 0..1 brightens it. */
export function Shield({ x, y, h = 34, color = P.push, k = 1, glow = 0 }: { x: number; y: number; h?: number; color?: string; k?: number; glow?: number }) {
  if (k <= 0) return null;
  const hh = Math.max(0, h);
  const w = hh * 0.84;
  const d =
    `M${x} ${y - hh} L${x + w} ${y - hh * 0.68} L${x + w} ${y + hh * 0.02} ` +
    `C${x + w} ${y + hh * 0.55} ${x + w * 0.5} ${y + hh * 0.84} ${x} ${y + hh} ` +
    `C${x - w * 0.5} ${y + hh * 0.84} ${x - w} ${y + hh * 0.55} ${x - w} ${y + hh * 0.02} L${x - w} ${y - hh * 0.68} Z`;
  const c = `M${x - w * 0.42} ${y + hh * 0.02} L${x - w * 0.1} ${y + hh * 0.34} L${x + w * 0.46} ${y - hh * 0.3}`;
  return (
    <g opacity={clamp01(k)}>
      <path d={d} fill={alpha(color, 0.18 + 0.3 * glow)} filter="url(#ccSoft)" />
      <path d={d} fill={P.bg0} />
      <path d={d} fill={alpha(color, 0.14 + 0.2 * glow)} stroke={color} strokeWidth={2.5} strokeLinejoin="round" />
      <path d={c} fill="none" stroke={color} strokeWidth={3.5} strokeLinecap="round" strokeLinejoin="round" />
    </g>
  );
}

export type Token = { x: number; y: number; fill: string | null; ring: string };

/** Credit tokens (SVG): filled in the holder's colour while a credit is in use, hollow ring when free. */
export function Tokens({ tokens, r = 8, opacity = 1 }: { tokens: Token[]; r?: number; opacity?: number }) {
  if (opacity <= 0) return null;
  const rr = Math.max(0, r);
  return (
    <g opacity={opacity}>
      {tokens.map((tk, i) => (
        <circle key={i} cx={tk.x} cy={tk.y} r={rr} fill={tk.fill ?? P.bg0} stroke={tk.fill ?? tk.ring} strokeWidth={2} />
      ))}
    </g>
  );
}

/** A bulk block (SVG) at (x, y) centre, with an optional label (≥ 20 px). */
export function BlockAt({
  x,
  y,
  w = 80,
  h = 32,
  color = P.nic,
  label,
  opacity = 1,
}: {
  x: number;
  y: number;
  w?: number;
  h?: number;
  color?: string;
  label?: string;
  opacity?: number;
}) {
  if (opacity <= 0) return null;
  const ww = Math.max(0, w);
  const hh = Math.max(0, h);
  return (
    <g opacity={opacity}>
      <rect x={x - ww / 2 - 8} y={y - hh / 2 - 8} width={ww + 16} height={hh + 16} rx={12} fill={alpha(color, 0.2)} filter="url(#ccSoft)" />
      <rect x={x - ww / 2} y={y - hh / 2} width={ww} height={hh} rx={7} fill={color} />
      {label && (
        <text x={x} y={y + 1} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={700} fill={P.bg0} fontFamily={FONT}>
          {label}
        </text>
      )}
    </g>
  );
}

/** A bulk block travelling a polyline at fraction k (hidden outside 0..1). */
export function Block({ pts, k, ...rest }: { pts: Pt[]; k: number; w?: number; h?: number; color?: string; label?: string; opacity?: number }) {
  if (k < 0 || k > 1) return null;
  const [x, y] = along(pts, clamp01(k));
  return <BlockAt x={x} y={y} {...rest} />;
}

/** Empty (dashed) or filled message slot (SVG), centred at (x, y). */
export function Slot({ x, y, s = 22, color, fill = 0, opacity = 1 }: { x: number; y: number; s?: number; color: string; fill?: number; opacity?: number }) {
  if (opacity <= 0) return null;
  const q = Math.max(0, s);
  return (
    <g opacity={opacity}>
      <rect x={x - q / 2} y={y - q / 2} width={q} height={q} rx={4} fill={alpha(color, 0.06 + 0.8 * fill)} stroke={alpha(color, 0.75)} strokeWidth={1.5} strokeDasharray={fill > 0.5 ? undefined : "4 3"} />
    </g>
  );
}
