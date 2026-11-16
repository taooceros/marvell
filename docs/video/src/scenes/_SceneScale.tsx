/** Private helpers for S12 / S13 (SceneScale agent). */
import React from "react";
import { C, CORE, FONT, K, MONO, Packet, Pt, Scale, along, clamp01, lerp, linScale, prog } from "../lib";
import { DpGeom, RING_CELLS, Rect, RingId } from "./pcie";

/**
 * Take-turns opacity for cue content shown from cue start a until cue start b: fully visible until b, then fades
 * over 0.5 s; the fade-in starts 0.3 s after a so content sharing a spot with the previous cue's is mostly gone.
 */
export const on = (t: number, a: number, b = 1e9) => prog(t, a + 0.3, 0.5) * (1 - prog(t, b, 0.5));
/** Simultaneous crossfade (for colour overlays that morph in place). */
export const xf = (t: number, a: number, b = 1e9) => prog(t, a, 0.5) * (1 - prog(t, b, 0.5));

/**
 * Ambient looping packet on a polyline (real time). Each `period` seconds it travels for `dur` seconds.
 * `cut` < 1: the packet stops at that fraction and vanishes (red flash) instead of arriving.
 */
export function Loop({
  pts,
  rt,
  period,
  dur,
  phase = 0,
  color,
  r = 8,
  cut = 1,
  wide = false,
}: {
  pts: Pt[];
  rt: number;
  period: number;
  dur: number;
  phase?: number;
  color: string;
  r?: number;
  cut?: number;
  wide?: boolean;
}) {
  const x = (((rt + phase) % period) + period) % period;
  if (x >= dur) return null;
  const k = x / dur;
  const fin = Math.min(1, k * 8);
  if (k > cut) {
    const v = clamp01((k - cut) / 0.18);
    if (v >= 1) return null;
    const [px, py] = along(pts, cut);
    return (
      <g opacity={1 - v}>
        <circle cx={px} cy={py} r={r * (1 + 1.6 * v)} fill="none" stroke={K.bad} strokeWidth={3} />
        <circle cx={px} cy={py} r={r * (1 - v)} fill={K.bad} />
      </g>
    );
  }
  if (wide) return <WidePacket pts={pts} k={k} color={color} opacity={fin * Math.min(1, (1 - k) * 10)} />;
  return (
    <g opacity={fin * Math.min(1, (1 - k) * 10)}>
      <Packet pts={pts} k={Math.max(k, 0.001)} color={color} trail={false} r={r} />
    </g>
  );
}

/** A wide capsule (one DMA carrying a batch) travelling a polyline, oriented along the current segment. */
export function WidePacket({ pts, k, color, opacity = 1, len = 58, th = 22 }: { pts: Pt[]; k: number; color: string; opacity?: number; len?: number; th?: number }) {
  const [x, y] = along(pts, k);
  const [x2, y2] = along(pts, Math.min(1, k + 0.01));
  const [x0, y0] = along(pts, Math.max(0, k - 0.01));
  const vert = Math.abs(y2 - y0) > Math.abs(x2 - x0);
  const w = vert ? th : len;
  const h = vert ? len : th;
  return (
    <g opacity={opacity}>
      <rect x={x - w / 2 - 6} y={y - h / 2 - 6} width={w + 12} height={h + 12} rx={12} fill={color} opacity={0.18} />
      <rect x={x - w / 2} y={y - h / 2} width={w} height={h} rx={8} fill={color} stroke="#fff" strokeWidth={1.5} />
      {Array.from({ length: 4 }, (_, i) =>
        vert ? (
          <line key={i} x1={x - w / 2 + 3} x2={x + w / 2 - 3} y1={y - h / 2 + ((i + 1) * h) / 5} y2={y - h / 2 + ((i + 1) * h) / 5} stroke={C.bg} strokeWidth={2} />
        ) : (
          <line key={i} y1={y - h / 2 + 3} y2={y + h / 2 - 3} x1={x - w / 2 + ((i + 1) * w) / 5} x2={x - w / 2 + ((i + 1) * w) / 5} stroke={C.bg} strokeWidth={2} />
        ),
      )}
    </g>
  );
}

/** Lit core squares drawn over Datapath's dim ones (cores=4, activeCores=0), each with its own opacity. */
export function CoreLights({ g, lit, cores = 4 }: { g: DpGeom; lit: number[]; cores?: number }) {
  const r = g.r.cores;
  const sz = 46 * g.s;
  const gap = 12 * g.s;
  const x0 = r.x + r.w - 20 * g.s - cores * sz - (cores - 1) * gap;
  return (
    <g>
      {lit.map((o, i) =>
        o <= 0.001 ? null : (
          <g key={i} opacity={o}>
            <rect x={x0 + i * (sz + gap)} y={r.y + (r.h - sz) / 2} width={sz} height={sz} rx={8 * g.s} fill={C.panel} />
            <rect x={x0 + i * (sz + gap)} y={r.y + (r.h - sz) / 2} width={sz} height={sz} rx={8 * g.s} fill={`${CORE[i]}33`} stroke={CORE[i]} strokeWidth={3} />
            <text x={x0 + i * (sz + gap) + sz / 2} y={r.y + r.h / 2} textAnchor="middle" dominantBaseline="central" fontSize={g.fs(18)} fontFamily={MONO} fill={CORE[i]}>
              {i}
            </text>
          </g>
        ),
      )}
    </g>
  );
}

/** Ring-cell colour overlay (same cell geometry as Datapath), faded by `o`. */
export function Cells({ g, id, fill, o }: { g: DpGeom; id: RingId; fill: (i: number) => string | undefined; o: number }) {
  if (o <= 0.001) return null;
  const r = g.r[id];
  const cw = (r.w - 20 * g.s) / RING_CELLS;
  return (
    <g opacity={o}>
      {Array.from({ length: RING_CELLS }, (_, i) => {
        const f = fill(i);
        if (!f) return null;
        return (
          <rect key={i} x={r.x + 10 * g.s + cw * i + 3 * g.s} y={r.y + r.h - 48 * g.s} width={Math.max(0, cw - 6 * g.s)} height={36 * g.s} rx={5 * g.s} fill={f} stroke={f} strokeWidth={1.5} opacity={0.9} />
        );
      })}
    </g>
  );
}

/** Glowing highlight frame over a block. */
export function Glow({ r, color, o }: { r: Rect; color: string; o: number }) {
  if (o <= 0.001) return null;
  return <rect x={r.x - 3} y={r.y - 3} width={r.w + 6} height={r.h + 6} rx={10} fill={`${color}1c`} stroke={color} strokeWidth={4} filter="url(#glow)" opacity={o} />;
}

/** HTML: big headline value + label (top of the right column). */
export function Headline({
  value,
  label,
  color,
  o,
  x = 1250,
  y = 124,
  w = 610,
  size = 56,
}: {
  value: React.ReactNode;
  label?: React.ReactNode;
  color: string;
  o: number;
  x?: number;
  y?: number;
  w?: number;
  size?: number;
}) {
  if (o <= 0.001) return null;
  return (
    <div style={{ position: "absolute", left: x, top: y, width: w, height: 110, display: "flex", alignItems: "center", gap: 20, opacity: o, transform: `translateY(${(1 - o) * 12}px)` }}>
      <div style={{ fontSize: size, fontWeight: 800, color, whiteSpace: "nowrap", lineHeight: 1.05, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      {label && <div style={{ fontSize: 25, color: C.muted, lineHeight: 1.25 }}>{label}</div>}
    </div>
  );
}

/** HTML callout in the free gap between the host and card panels (x 470–778, y ~150). */
export function Callout({ children, color, o, x = 470, y = 146, w = 308 }: { children: React.ReactNode; color: string; o: number; x?: number; y?: number; w?: number }) {
  if (o <= 0.001) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        boxSizing: "border-box",
        padding: "10px 14px",
        border: `2.5px solid ${color}`,
        borderRadius: 14,
        background: "rgba(11,16,32,0.92)",
        color,
        fontSize: 27,
        fontWeight: 800,
        lineHeight: 1.25,
        textAlign: "center",
        opacity: o,
        transform: `scale(${0.92 + 0.08 * o})`,
      }}
    >
      {children}
    </div>
  );
}

export type RBar = { lo: number; hi?: number; text: string; color: string; label: string[]; k: number; note?: string; noteColor?: string };

/** Vertical bar chart with optional ranges (lo solid, lo→hi hatched lighter), value text above, multi-line labels. */
export function RangeBars({
  x0,
  x1,
  y0,
  y1,
  max,
  ticks,
  ylabel,
  data,
  size = 28,
}: {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
  max: number;
  ticks: number[];
  ylabel: string;
  data: RBar[];
  size?: number;
}) {
  const sy = linScale(0, max, y0, y1);
  const slot = (x1 - x0) / data.length;
  const bw = slot * 0.58;
  return (
    <g fontFamily={FONT}>
      {ticks.map((v) => (
        <g key={v}>
          <line x1={x0} x2={x1} y1={sy(v)} y2={sy(v)} stroke={C.line} strokeWidth={1.5} opacity={0.6} />
        </g>
      ))}
      <text x={x0 - 40} y={y1 - 34} fontSize={24} fill={C.muted}>
        {ylabel}
      </text>
      <line x1={x0} x2={x1} y1={y0} y2={y0} stroke={C.dim} strokeWidth={2.5} />
      <line x1={x0} x2={x0} y1={y0} y2={y1} stroke={C.dim} strokeWidth={2.5} />
      {data.map((d, i) => {
        const kk = clamp01(d.k);
        if (kk <= 0) return null;
        const cx = x0 + slot * (i + 0.5);
        const top = lerp(y0, sy(d.hi ?? d.lo), kk);
        const loY = sy(d.lo);
        const solidTop = Math.max(top, loY);
        const tOp = clamp01((kk - 0.6) / 0.4);
        return (
          <g key={i}>
            <rect x={cx - bw / 2} y={solidTop} width={bw} height={y0 - solidTop} rx={6} fill={d.color} opacity={0.9} />
            {top < loY && <rect x={cx - bw / 2} y={top} width={bw} height={loY - top} rx={4} fill={`${d.color}55`} stroke={d.color} strokeWidth={2} strokeDasharray="6 4" />}
            <text x={cx} y={top - 12} textAnchor="middle" fontSize={size} fontWeight={800} fill={d.color} opacity={tOp}>
              {d.text}
            </text>
            {d.note && (
              <text x={cx} y={top - 18 - size} textAnchor="middle" fontSize={26} fontWeight={800} fill={d.noteColor ?? d.color} opacity={tOp}>
                {d.note}
              </text>
            )}
            {d.label.map((l, j) => (
              <text key={j} x={cx} y={y0 + 30 + j * 28} textAnchor="middle" fontSize={24} fill={j === 0 ? C.text : C.muted} opacity={Math.min(1, kk * 3)}>
                {l}
              </text>
            ))}
          </g>
        );
      })}
    </g>
  );
}

export type HBar = { y: number; h: number; lo: number; hi?: number; text: string; color: string; label?: string; inner?: string; k: number };

/** Horizontal bars (linear or log scale `sx`), starting at sx(base). */
export function HBars({ sx, base, data, size = 28 }: { sx: Scale; base: number; data: HBar[]; size?: number }) {
  const xb = sx(base);
  return (
    <g fontFamily={FONT}>
      {data.map((d, i) => {
        const kk = clamp01(d.k);
        const end = lerp(xb, sx(d.hi ?? d.lo), kk);
        const loX = sx(d.lo);
        const solidEnd = Math.min(end, loX);
        return (
          <g key={i}>
            {d.label && (
              <text x={xb - 16} y={d.y + d.h / 2} textAnchor="end" dominantBaseline="central" fontSize={25} fill={C.text}>
                {d.label}
              </text>
            )}
            {kk > 0 && (
              <>
                <rect x={xb} y={d.y} width={Math.max(0, solidEnd - xb)} height={d.h} rx={6} fill={d.color} opacity={0.88} />
                {end > loX && <rect x={loX} y={d.y} width={end - loX} height={d.h} rx={4} fill={`${d.color}55`} stroke={d.color} strokeWidth={2} strokeDasharray="6 4" />}
                {d.inner && (
                  <text x={xb + 12} y={d.y + d.h / 2} dominantBaseline="central" fontSize={22} fontWeight={700} fill={C.bg} opacity={clamp01(kk * 2 - 0.6)}>
                    {d.inner}
                  </text>
                )}
                <text x={end + 14} y={d.y + d.h / 2} dominantBaseline="central" fontSize={size} fontWeight={800} fill={d.color} opacity={clamp01((kk - 0.6) / 0.4)}>
                  {d.text}
                </text>
              </>
            )}
          </g>
        );
      })}
    </g>
  );
}

/** Horizontal axis with ticks (for HBars). */
export function XAxis({ sx, x0, x1, y, ticks }: { sx: Scale; x0: number; x1: number; y: number; ticks: [number, string][] }) {
  return (
    <g fontFamily={FONT}>
      <line x1={x0} x2={x1} y1={y} y2={y} stroke={C.dim} strokeWidth={2.5} />
      {ticks.map(([v, l]) => (
        <g key={v}>
          <line x1={sx(v)} x2={sx(v)} y1={y} y2={y + 8} stroke={C.dim} strokeWidth={2} />
          <text x={sx(v)} y={y + 30} textAnchor="middle" fontSize={22} fill={C.muted}>
            {l}
          </text>
        </g>
      ))}
    </g>
  );
}

/** Timeline row of coloured blocks revealed left → right up to `reveal` px past x0. */
export function Timeline({
  x0,
  x1,
  y,
  h,
  pattern,
  reveal,
  opacity = 1,
}: {
  x0: number;
  x1: number;
  y: number;
  h: number;
  pattern: { w: number; color: string; text?: string; gap?: number }[];
  reveal: number;
  opacity?: number;
}) {
  const out: React.ReactNode[] = [];
  let x = x0;
  let n = 0;
  while (x < x1 && n < 400) {
    for (const p of pattern) {
      if (x >= x1) break;
      const w = Math.min(p.w, x1 - x);
      const vis = clamp01((x0 + reveal - x) / w) * w;
      if (vis > 0)
        out.push(
          <g key={n}>
            <rect x={x} y={y} width={vis} height={h} rx={4} fill={`${p.color}55`} stroke={p.color} strokeWidth={2} />
            {p.text && vis >= w - 1 && (
              <text x={x + w / 2} y={y + h / 2} textAnchor="middle" dominantBaseline="central" fontSize={20} fontFamily={MONO} fontWeight={700} fill={C.text}>
                {p.text}
              </text>
            )}
          </g>,
        );
      x += p.w + (p.gap ?? 4);
      n++;
    }
  }
  return <g opacity={opacity}>{out}</g>;
}
