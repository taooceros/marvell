/** Private helpers for S01 / S02 / S10 (agent Mrv96FrameScenes): Rig morphs, overlays on Rig blocks, chips, glyphs. */
import React from "react";
import { C, FONT, K, Pt, clamp01, lerp } from "../lib";
import { Chip, RigBlock, RigGeom } from "./kit";

export type Box = { x: number; y: number; w: number; h: number };

// ================================================================= Rig geometry
/** Morph between two Rig geometries (k = 0 → a, k = 1 → b): one diagram that moves instead of a cut. */
export function lerpGeom(a: RigGeom, b: RigGeom, k: number): RigGeom {
  if (k <= 0) return a;
  if (k >= 1) return b;
  const r = {} as Record<RigBlock, Box>;
  (Object.keys(a.r) as RigBlock[]).forEach((id) => {
    const p = a.r[id];
    const q = b.r[id];
    r[id] = { x: lerp(p.x, q.x, k), y: lerp(p.y, q.y, k), w: lerp(p.w, q.w, k), h: lerp(p.h, q.h, k) };
  });
  return {
    s: lerp(a.s, b.s, k),
    dx: lerp(a.dx, b.dx, k),
    dy: lerp(a.dy, b.dy, k),
    r,
    fwdY: lerp(a.fwdY, b.fwdY, k),
    retY: lerp(a.retY, b.retY, k),
  };
}

/** The same diagram with the card side pulled left by d px (a narrower PCIe band). */
export function squeezeGeom(g: RigGeom, d: number): RigGeom {
  const r = { ...g.r };
  (["card", "cring", "ccores", "wc"] as RigBlock[]).forEach((id) => (r[id] = { ...r[id], x: r[id].x - d }));
  r.link = { ...r.link, w: r.link.w - d };
  return { ...g, r };
}

/** Cell i of the kit's 8-cell mini ring inside the request-ring / reply-ring block. */
export function miniCell(g: RigGeom, id: "cring" | "hring", i: number, n = 8): Box {
  const q = g.r[id];
  const s = g.s;
  const pad = 14 * s;
  const cw = (q.w - 2 * pad - (n - 1) * 4 * s) / n;
  return { x: q.x + pad + i * (cw + 4 * s), y: q.y + q.h - pad - 26 * s, w: Math.max(2, cw), h: 26 * s };
}

/** Distance travelled after x seconds when speed follows piecewise-linear knots [time, speed] (flat outside). */
export function travelAt(x: number, knots: [number, number][]): number {
  if (x <= 0) return 0;
  const pts: [number, number][] = [[0, knots[0][1]], ...knots.filter((k) => k[0] > 0)];
  let d = 0;
  for (let i = 0; i < pts.length; i++) {
    const [t0, v0] = pts[i];
    const nx = pts[i + 1];
    if (!nx) return d + v0 * (x - t0);
    const [t1, v1] = nx;
    const te = Math.min(x, t1);
    const ve = v0 + ((v1 - v0) * (te - t0)) / Math.max(1e-9, t1 - t0);
    d += ((v0 + ve) / 2) * (te - t0);
    if (x <= t1) return d;
  }
  return d;
}

/** Pulse 0 → 1 → 0 over [a, a + d] (sine bump). */
export const bump = (t: number, a: number, d: number) => Math.sin(Math.PI * clamp01((t - a) / d));

// ================================================================= card-core block (24 cores, drawn here)
const GRID = { cols: 6, rows: 4, cell: 16, gap: 5 };

/** Centre of core i (row-major, 6 × 4) inside the ccores block of g. */
export function coreBox(g: RigGeom, i: number): Box {
  const q = g.r.ccores;
  const s = g.s;
  const cell = GRID.cell * s;
  const gap = GRID.gap * s;
  const gw = GRID.cols * cell + (GRID.cols - 1) * gap;
  const gh = GRID.rows * cell + (GRID.rows - 1) * gap;
  const gx = q.x + (q.w - gw) / 2;
  const gy = q.y + q.h - 14 * s - gh;
  const col = i % GRID.cols;
  const row = Math.floor(i / GRID.cols);
  return { x: gx + col * (cell + gap), y: gy + row * (cell + gap), w: cell, h: cell };
}

/**
 * Rig-style "card cores" block with all 24 Arm cores as a 6 × 4 grid (the kit block draws at most 4).
 * Same look and dim/highlight rule as the kit's blocks; lit(i) returns a fill colour for core i, or null.
 */
export function CoreGrid({
  g,
  show = 1,
  dim = 0,
  hl = 0,
  main,
  sub,
  lit = () => null,
}: {
  g: RigGeom;
  show?: number;
  dim?: number;
  hl?: number;
  main: string;
  sub?: string;
  lit?: (i: number) => string | null;
}) {
  if (show <= 0) return null;
  const q = g.r.ccores;
  const s = g.s;
  const fs = (v: number) => Math.max(20, v * s);
  const op = show * lerp(1, Math.max(hl, 0.3), dim);
  return (
    <g opacity={op} fontFamily={FONT}>
      <rect x={q.x} y={q.y} width={q.w} height={q.h} rx={14 * s} fill={C.panel} stroke={K.card} strokeWidth={2.5} />
      <text x={q.x + q.w / 2} y={q.y + 34 * s} textAnchor="middle" fontSize={fs(26)} fontWeight={800} fill={C.text}>
        {main}
      </text>
      {sub && (
        <text x={q.x + q.w / 2} y={q.y + 34 * s + fs(24)} textAnchor="middle" fontSize={fs(22)} fill={C.muted}>
          {sub}
        </text>
      )}
      {Array.from({ length: GRID.cols * GRID.rows }, (_, i) => {
        const b = coreBox(g, i);
        const c = lit(i);
        return (
          <rect key={i} x={b.x} y={b.y} width={b.w} height={b.h} rx={3 * s} fill={c ?? "rgba(248,150,30,0.16)"}
            stroke={c ?? K.card} strokeWidth={1.5} opacity={c ? 1 : 0.8} />
        );
      })}
    </g>
  );
}

// ================================================================= SVG glyphs
/** A 64-byte message as a labelled box centred at p (with a soft halo). */
export function MsgBox({
  p,
  w = 84,
  h = 42,
  color,
  label,
  labelK = 1,
  opacity = 1,
  size = 24,
}: {
  p: Pt;
  w?: number;
  h?: number;
  color: string;
  label?: string;
  labelK?: number;
  opacity?: number;
  size?: number;
}) {
  if (opacity <= 0) return null;
  return (
    <g opacity={opacity}>
      <rect x={p[0] - w / 2 - 4} y={p[1] - h / 2 - 4} width={w + 8} height={h + 8} rx={10} fill={color} opacity={0.22} />
      <rect x={p[0] - w / 2} y={p[1] - h / 2} width={w} height={h} rx={Math.min(8, h / 3)} fill={color} />
      {label && labelK > 0 && (
        <text x={p[0]} y={p[1] + 1} textAnchor="middle" dominantBaseline="central" fontSize={size} fontWeight={800} fill={C.bg}
          opacity={clamp01(labelK)} fontFamily={FONT}>
          {label}
        </text>
      )}
    </g>
  );
}

/** Padlock centred at (x, y); open ∈ [0, 1] lifts and swings the shackle. */
export function Padlock({ x, y, s = 1, open = 0, color }: { x: number; y: number; s?: number; open?: number; color: string }) {
  const top = y - 3 * s;
  return (
    <g>
      <g transform={`translate(0 ${-5 * s * open}) rotate(${-38 * open} ${x + 6 * s} ${top})`}>
        <path d={`M${x - 6 * s} ${top} v${-5 * s} a${6 * s} ${6 * s} 0 0 1 ${12 * s} 0 v${5 * s}`} fill="none" stroke={color}
          strokeWidth={3.2 * s} strokeLinecap="round" />
      </g>
      <rect x={x - 10 * s} y={top} width={20 * s} height={15 * s} rx={3 * s} fill={color} />
    </g>
  );
}

/** Check mark drawn progressively (k), centred near (x, y). */
export function Check({ x, y, s = 1, k = 1, color = K.fast, w = 5 }: { x: number; y: number; s?: number; k?: number; color?: string; w?: number }) {
  if (k <= 0) return null;
  const len = 38 * s;
  return (
    <path d={`M${x - 12 * s} ${y} l${8 * s} ${9 * s} l${16 * s} ${-19 * s}`} fill="none" stroke={color} strokeWidth={w}
      strokeLinecap="round" strokeLinejoin="round" strokeDasharray={len} strokeDashoffset={len * (1 - clamp01(k))} />
  );
}

/** Zoom callout: two dashed rays from a source edge (a0, a1) to a target edge (b0, b1) with a faint wedge fill. */
export function ZoomWedge({ a0, a1, b0, b1, k = 1, color = C.muted }: { a0: Pt; a1: Pt; b0: Pt; b1: Pt; k?: number; color?: string }) {
  if (k <= 0) return null;
  return (
    <g opacity={k}>
      <polygon points={[a0, b0, b1, a1].map((p) => p.join(",")).join(" ")} fill={color} opacity={0.07} />
      <line x1={a0[0]} y1={a0[1]} x2={b0[0]} y2={b0[1]} stroke={color} strokeWidth={2} strokeDasharray="6 6" />
      <line x1={a1[0]} y1={a1[1]} x2={b1[0]} y2={b1[1]} stroke={color} strokeWidth={2} strokeDasharray="6 6" />
    </g>
  );
}

/** Rotary knob (THE variable under test) centred at (cx, cy); angle in degrees (0 = straight up). */
export function KnobGlyph({ cx, cy, r, angle, color = K.knob, glow = 0 }: { cx: number; cy: number; r: number; angle: number; color?: string; glow?: number }) {
  const a = ((angle - 90) * Math.PI) / 180;
  return (
    <g>
      {glow > 0 && <circle cx={cx} cy={cy} r={r * 1.35} fill={color} opacity={0.16 * glow} />}
      {Array.from({ length: 7 }, (_, i) => {
        const ta = ((-135 + i * 45 - 90) * Math.PI) / 180;
        return (
          <line key={i} x1={cx + Math.cos(ta) * r * 1.12} y1={cy + Math.sin(ta) * r * 1.12} x2={cx + Math.cos(ta) * r * 1.26}
            y2={cy + Math.sin(ta) * r * 1.26} stroke={color} strokeWidth={Math.max(1.5, r / 14)} strokeLinecap="round" opacity={0.7} />
        );
      })}
      <circle cx={cx} cy={cy} r={r} fill="#211d0f" stroke={color} strokeWidth={Math.max(2, r / 9)} />
      <line x1={cx} y1={cy} x2={cx + Math.cos(a) * r * 0.78} y2={cy + Math.sin(a) * r * 0.78} stroke={color}
        strokeWidth={Math.max(2.5, r / 7)} strokeLinecap="round" />
      <circle cx={cx} cy={cy} r={Math.max(2.5, r / 7)} fill={color} />
    </g>
  );
}

/** Row of fixed (grey) vertical sliders: tracks from y to y + h, handle i at fraction pos[i] from the top. */
export function SliderGlyph({ x, y, h, pos, gap, color = K.fixed }: { x: number; y: number; h: number; pos: number[]; gap: number; color?: string }) {
  const tw = Math.max(3, h / 16);
  const hw = h * 0.3;
  return (
    <g>
      {pos.map((p, i) => (
        <g key={i}>
          <rect x={x + i * gap - tw / 2} y={y} width={tw} height={h} rx={tw / 2} fill={color} opacity={0.35} />
          <rect x={x + i * gap - hw / 2} y={y + p * h - h * 0.07} width={hw} height={h * 0.14} rx={h * 0.04} fill={color} />
        </g>
      ))}
    </g>
  );
}

// ================================================================= HTML pieces
/** Absolutely positioned HTML box at (x, y); center = translate it left by half its width. */
export function At({
  x,
  y,
  w,
  center = false,
  style,
  children,
}: {
  x: number;
  y: number;
  w?: number;
  center?: boolean;
  style?: React.CSSProperties;
  children: React.ReactNode;
}) {
  const tf = [center ? "translateX(-50%)" : "", style?.transform ?? ""].join(" ").trim();
  return (
    <div style={{ position: "absolute", left: x, top: y, width: w, fontFamily: FONT, ...style, transform: tf || undefined }}>
      {children}
    </div>
  );
}

/** A kit Chip with a strike-through line drawn left → right by `strike` ∈ [0, 1]. */
export function StrikeChip({ children, strike, color = K.bad, size = 26 }: { children: React.ReactNode; strike: number; color?: string; size?: number }) {
  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <Chip color={color} size={size}>
        {children}
      </Chip>
      {strike > 0 && (
        <span
          style={{
            position: "absolute",
            left: -8,
            top: "50%",
            height: 5,
            marginTop: -2.5,
            width: `calc(${clamp01(strike)} * (100% + 16px))`,
            background: color,
            borderRadius: 3,
            boxShadow: `0 0 10px ${color}`,
          }}
        />
      )}
    </span>
  );
}
