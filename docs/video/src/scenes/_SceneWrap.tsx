/** Private SVG helpers for S14 / S15 (owned by SceneWrap). */
import React from "react";
import { C, FONT, MONO } from "../lib";

/** SVG text, vertically centred on y. */
export function Tx({
  x,
  y,
  size = 26,
  color = C.text,
  weight = 400,
  anchor = "start",
  mono = false,
  opacity = 1,
  italic = false,
  spacing,
  children,
}: {
  x: number;
  y: number;
  size?: number;
  color?: string;
  weight?: number;
  anchor?: "start" | "middle" | "end";
  mono?: boolean;
  opacity?: number;
  italic?: boolean;
  spacing?: number;
  children: React.ReactNode;
}) {
  if (opacity <= 0.001) return null;
  return (
    <text
      x={x}
      y={y}
      fontSize={size}
      fill={color}
      fontWeight={weight}
      textAnchor={anchor}
      dominantBaseline="central"
      fontFamily={mono ? MONO : FONT}
      fontStyle={italic ? "italic" : undefined}
      letterSpacing={spacing}
      opacity={opacity}
    >
      {children}
    </text>
  );
}

/** Rounded panel rectangle. */
export function Box({
  x,
  y,
  w,
  h,
  stroke = C.line,
  fill = C.panel,
  sw = 2,
  rx = 16,
  opacity = 1,
  dash,
  glow = false,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  stroke?: string;
  fill?: string;
  sw?: number;
  rx?: number;
  opacity?: number;
  dash?: string;
  glow?: boolean;
}) {
  if (opacity <= 0.001) return null;
  return (
    <rect
      x={x}
      y={y}
      width={Math.max(0, w)}
      height={Math.max(0, h)}
      rx={rx}
      fill={fill}
      stroke={stroke}
      strokeWidth={sw}
      strokeDasharray={dash}
      opacity={opacity}
      filter={glow ? "url(#glow)" : undefined}
    />
  );
}

/** Check mark in a ring, centred at (x, y); k = scale (use lib `pop`). */
export function Check({ x, y, k = 1, color, r = 16 }: { x: number; y: number; k?: number; color: string; r?: number }) {
  if (k <= 0.001) return null;
  const s = r / 16;
  return (
    <g transform={`translate(${x} ${y}) scale(${k * s})`}>
      <circle r={16} fill={`${color}26`} stroke={color} strokeWidth={2.5} />
      <path d="M-7 0.5 L-2 6 L8 -6" fill="none" stroke={color} strokeWidth={4} strokeLinecap="round" strokeLinejoin="round" />
    </g>
  );
}

/** Cross in a ring, centred at (x, y). */
export function Cross({ x, y, k = 1, color, r = 16 }: { x: number; y: number; k?: number; color: string; r?: number }) {
  if (k <= 0.001) return null;
  const s = r / 16;
  return (
    <g transform={`translate(${x} ${y}) scale(${k * s})`}>
      <circle r={16} fill={`${color}26`} stroke={color} strokeWidth={2.5} />
      <path d="M-6 -6 L6 6 M6 -6 L-6 6" fill="none" stroke={color} strokeWidth={4} strokeLinecap="round" />
    </g>
  );
}
