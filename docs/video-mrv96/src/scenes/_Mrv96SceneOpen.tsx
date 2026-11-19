/**
 * Private helpers for S07–S09 (agent Mrv96SceneOpen): cue crossfade windows, keyed slides, the dashed side panel.
 */
import React from "react";
import { C, FONT, fade, lerp, prog } from "../lib";

/** Opacity of an element shown from `a` (0.45 s fade-in) until `b` (0.45 s fade-out that ends exactly at b). */
export const win = (t: number, a: number, b = 1e9) => fade(t, a, b - 0.45, 0.45);

/** Value that eases (over d s) to keys[i][1] starting at time keys[i][0]; `from` before the first key. */
export function slide(t: number, keys: [number, number][], from: number, d = 0.6) {
  let v = from;
  for (const [at, to] of keys) v = lerp(v, to, prog(t, at, d));
  return v;
}

/** Dashed host / DPU panel (SVG): title top-left, optional sub-title top-right. */
export function SideBox({
  x,
  y,
  w,
  h,
  color,
  title,
  sub,
  subColor = C.muted,
  size = 28,
  opacity = 1,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  color: string;
  title: string;
  sub?: React.ReactNode;
  subColor?: string;
  size?: number;
  opacity?: number;
}) {
  if (opacity <= 0) return null;
  return (
    <g opacity={opacity} fontFamily={FONT}>
      <rect x={x} y={y} width={w} height={h} rx={20} fill="rgba(20,27,46,0.55)" stroke={color} strokeWidth={2.5}
        strokeDasharray="10 8" />
      <text x={x + 22} y={y + size + 10} fontSize={size} fontWeight={800} fill={color} letterSpacing={2}>
        {title}
      </text>
      {sub !== undefined && (
        <text x={x + w - 22} y={y + size + 10} textAnchor="end" fontSize={Math.max(24, size - 2)} fontWeight={600}
          fill={subColor}>
          {sub}
        </text>
      )}
    </g>
  );
}
