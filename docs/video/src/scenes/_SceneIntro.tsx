/** Private helpers for S01 / S02 (agent SceneIntro). */
import React from "react";
import { C, FONT, MONO, Pt, along, clamp01, partial, pathD, prog } from "../lib";
import { BlockId, DpGeom } from "./pcie";

/** Inner (non-panel) Datapath blocks. */
export const INNER: BlockId[] = ["core", "hmem", "sdpRing", "replyRing", "pem", "sdp", "dpi", "cores", "cmem", "reqRing", "ctrl"];

/** Smooth replacement for `dimOthers`: inner blocks not in `keep` fade towards 0.38 opacity as k → 1. */
export function dimMap(keep: BlockId[], k: number): Partial<Record<BlockId, number>> {
  return Object.fromEntries(INNER.map((id) => [id, keep.includes(id) ? 1 : 1 - 0.62 * k])) as Partial<Record<BlockId, number>>;
}

/** Per-block highlight weights (0..1): blocks fade towards 0.38 by how far they trail the strongest weight. */
export function dimWeights(w: Partial<Record<BlockId, number>>): Partial<Record<BlockId, number>> {
  const top = Math.max(0, ...Object.values(w).map((v) => v ?? 0));
  return Object.fromEntries(INNER.map((id) => [id, 1 - 0.62 * Math.max(0, top - (w[id] ?? 0))])) as Partial<Record<BlockId, number>>;
}

/**
 * SDP sub-label stays 14 px (fs floor) while the block shrinks: below s ≈ 0.72 the default
 * "packet engine · doorbells · OUT_CNTS" overflows the card panel, so pass this as `labels`.
 */
export const SDP_SHORT: Partial<Record<BlockId, [string, string?]>> = { sdp: ["SDP", "packet engine · doorbells"] };
export const SDP_SHORT_BELOW = 0.72;

/** Glowing highlight frame over a Datapath block (opacity k), drawn on top of the Datapath. */
export function Hi({ g, id, color, k, pad = 0, panel = false }: { g: DpGeom; id: BlockId; color: string; k: number; pad?: number; panel?: boolean }) {
  if (k <= 0.001) return null;
  const r = g.r[id];
  return (
    <rect
      x={r.x - pad}
      y={r.y - pad}
      width={r.w + 2 * pad}
      height={r.h + 2 * pad}
      rx={(panel ? 22 : 14) * g.s + pad}
      fill={`${color}${panel ? "10" : "22"}`}
      stroke={color}
      strokeWidth={panel ? 5 : 4}
      filter="url(#glow)"
      opacity={k}
    />
  );
}

export type Leg = { pts: Pt[]; color: string; d: number };

/**
 * Repeating sequence of packet legs (e.g. request out, reply back) starting at `start`, one leg after the other with
 * `hold` seconds between. Each leg's trail fades after arrival, so nothing vanishes in a single frame.
 */
export function Legs({
  t,
  start,
  legs,
  hold = 0.25,
  opacity = 1,
  tag,
  r = 11,
}: {
  t: number;
  start: number;
  legs: Leg[];
  hold?: number;
  opacity?: number;
  tag?: string;
  r?: number;
}) {
  if (t < start || opacity <= 0.001) return null;
  const period = legs.reduce((s, l) => s + l.d + hold, 0) + 0.5;
  const ph = (t - start) % period;
  let s0 = 0;
  const out: React.ReactNode[] = [];
  legs.forEach((L, i) => {
    const loc = ph - s0;
    s0 += L.d + hold;
    if (loc < 0) return;
    const k = prog(loc, 0, L.d);
    const dotOp = 1 - clamp01((loc - L.d) / 0.3);
    const trailOp = 1 - clamp01((loc - L.d) / 0.7);
    if (trailOp <= 0) return;
    const [x, y] = along(L.pts, k);
    out.push(
      <g key={i}>
        <path d={pathD(partial(L.pts, k))} fill="none" stroke={L.color} strokeWidth={6} strokeLinecap="round" strokeLinejoin="round" opacity={0.5 * trailOp} />
        {k > 0 && (
          <g opacity={dotOp}>
            <circle cx={x} cy={y} r={r * 2} fill={L.color} opacity={0.2} />
            <circle cx={x} cy={y} r={r} fill={L.color} />
            {tag && (
              <g transform={`translate(${x} ${y - 42})`}>
                <rect x={-40} y={-17} width={80} height={34} rx={9} fill={C.bg} stroke={L.color} strokeWidth={2} />
                <text textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fontWeight={700} fill={L.color}>
                  {tag}
                </text>
              </g>
            )}
          </g>
        )}
      </g>,
    );
  });
  return <g opacity={opacity}>{out}</g>;
}

/** Doorbell glyph centred at (x, y); `ring` 0..1 draws a pulse ring and shakes the bell. */
export function Bell({ x, y, s = 1, color, ring = 0, opacity = 1 }: { x: number; y: number; s?: number; color: string; ring?: number; opacity?: number }) {
  if (opacity <= 0.001) return null;
  const rot = Math.sin(ring * Math.PI * 4) * 18 * (1 - ring);
  return (
    <g opacity={opacity} transform={`translate(${x} ${y})`}>
      {ring > 0 && ring < 1 && <circle r={(14 + 26 * ring) * s} fill="none" stroke={color} strokeWidth={3} opacity={1 - ring} />}
      <g transform={`scale(${s}) rotate(${rot})`}>
        <path d="M -11 7 Q -11 -13 0 -13 Q 11 -13 11 7 L 15 11 L -15 11 Z" fill={color} />
        <circle cx={0} cy={-15} r={3} fill={color} />
        <circle cx={0} cy={14} r={3.5} fill={color} />
      </g>
    </g>
  );
}

/** Centred HTML label (x = centre); `op` opacity, `dy` rise offset, `scale` pop scale. */
export function Centered({
  x,
  y,
  children,
  style,
  op = 1,
  dy = 0,
  scale = 1,
}: {
  x: number;
  y: number;
  children: React.ReactNode;
  style?: React.CSSProperties;
  op?: number;
  dy?: number;
  scale?: number;
}) {
  if (op <= 0.001) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        opacity: op,
        transform: `translateX(-50%) translateY(${dy}px) scale(${scale})`,
        transformOrigin: "50% 50%",
        whiteSpace: "nowrap",
        fontFamily: FONT,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
