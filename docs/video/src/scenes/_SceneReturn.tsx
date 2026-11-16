/** Private helpers for S07 / S08 (return-path scenes). */
import React from "react";
import { C, FONT } from "../lib";

/**
 * Phase (seconds) inside a repeating loop of period P that starts at a. Only whole loops that finish
 * before `end` are played, so nothing is cut mid-flight. Returns -1 outside, plus the loop index.
 */
export function cycle(t: number, a: number, end: number, P: number): { ph: number; n: number } {
  const loops = Math.floor((end - a) / P);
  if (t < a || loops <= 0) return { ph: -1, n: 0 };
  const n = Math.floor((t - a) / P);
  if (n >= loops) return { ph: -1, n: loops };
  return { ph: (t - a) % P, n };
}

/** Red "X" (crossed-out) centred at (x, y). */
export function Xmark({ x, y, s = 1, color = C.red, opacity = 1 }: { x: number; y: number; s?: number; color?: string; opacity?: number }) {
  const d = 22 * s;
  return (
    <g opacity={opacity} stroke={color} strokeWidth={7 * s} strokeLinecap="round">
      <circle cx={x} cy={y} r={d * 1.6} fill={C.bg} stroke={color} strokeWidth={3 * s} opacity={0.9} />
      <line x1={x - d} y1={y - d} x2={x + d} y2={y + d} />
      <line x1={x - d} y1={y + d} x2={x + d} y2={y - d} />
    </g>
  );
}

/** Green check mark centred at (x, y). */
export function Check({ x, y, s = 1, color = C.green, opacity = 1, ring = true }: { x: number; y: number; s?: number; color?: string; opacity?: number; ring?: boolean }) {
  const d = 18 * s;
  return (
    <g opacity={opacity}>
      {ring && <circle cx={x} cy={y} r={d * 1.7} fill={C.bg} stroke={color} strokeWidth={3 * s} />}
      <polyline
        points={`${x - d},${y} ${x - d * 0.25},${y + d * 0.75} ${x + d},${y - d * 0.8}`}
        fill="none"
        stroke={color}
        strokeWidth={7 * s}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </g>
  );
}

/** SVG text label on a dark rounded backing (readable over lanes / hatches). */
export function Tag({
  x,
  y,
  children,
  color = C.text,
  size = 22,
  anchor = "middle",
  opacity = 1,
  border,
  weight = 700,
}: {
  x: number;
  y: number;
  children: string;
  color?: string;
  size?: number;
  anchor?: "start" | "middle" | "end";
  opacity?: number;
  border?: string;
  weight?: number;
}) {
  const w = children.length * size * 0.58 + 20;
  const h = size + 14;
  const x0 = anchor === "middle" ? x - w / 2 : anchor === "end" ? x - w : x;
  return (
    <g opacity={opacity}>
      <rect x={x0} y={y - h / 2} width={w} height={h} rx={h / 2} fill={C.bg} opacity={0.88} stroke={border ?? "none"} strokeWidth={2} />
      <text x={x0 + w / 2} y={y} textAnchor="middle" dominantBaseline="central" fontSize={size} fontWeight={weight} fill={color} fontFamily={FONT}>
        {children}
      </text>
    </g>
  );
}
