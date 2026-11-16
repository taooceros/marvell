/** Private helpers shared by S09Pull and S11Store. */
import React from "react";
import { C, FONT, K, Pt, Scale, clamp01, lerp } from "../lib";
import { Rect } from "./pcie";

/** 0..1 → two-digit alpha hex suffix for `#rrggbb` colours. */
export const hex = (a: number) =>
  Math.round(clamp01(a) * 255)
    .toString(16)
    .padStart(2, "0");

/** Fading highlight frame over a datapath block. */
export function Glow({ r, color, k, pad = 6 }: { r: Rect; color: string; k: number; pad?: number }) {
  if (k <= 0.001) return null;
  return (
    <rect x={r.x - pad} y={r.y - pad} width={r.w + 2 * pad} height={r.h + 2 * pad} rx={16} fill={`${color}1c`} stroke={color} strokeWidth={4} filter="url(#glow)" opacity={k} />
  );
}

/** Vertical bar (SVG) with a measured range: solid to `lo`, dashed to `hi`; label above, grows with k. */
export function RangeBar({ cx, w, y0, sy, lo, hi, color, k, label }: { cx: number; w: number; y0: number; sy: Scale; lo: number; hi: number; color: string; k: number; label: string }) {
  if (k <= 0.001) return null;
  const yLo = lerp(y0, sy(lo), k);
  const yHi = lerp(y0, sy(hi), k);
  return (
    <g>
      <rect x={cx - w / 2} y={yHi} width={w} height={y0 - yHi} rx={8} fill={`${color}18`} stroke={color} strokeWidth={2} strokeDasharray="8 6" />
      <rect x={cx - w / 2} y={yLo} width={w} height={y0 - yLo} rx={8} fill={`${color}66`} stroke={color} strokeWidth={3} />
      <text x={cx} y={yHi - 22} textAnchor="middle" dominantBaseline="central" fontSize={30} fontWeight={800} fill={color} fontFamily={FONT} opacity={clamp01((k - 0.6) / 0.4)}>
        {label}
      </text>
    </g>
  );
}

/** Cell segment that turns from "old" (grey) to "new" (fresh colour); `bad` overlays a red ✗. */
export function CellSeg({ x, y, w, h, name, fresh, bad = 0, color = K.store, badge }: { x: number; y: number; w: number; h: number; name: string; fresh: number; bad?: number; color?: string; badge?: [string, number] }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} fill="url(#hatch)" stroke={C.dim} strokeWidth={2} />
      <rect x={x} y={y} width={w} height={h} fill={`${color}55`} stroke={color} strokeWidth={3} opacity={fresh} />
      <text x={x + w / 2} y={y + h * 0.38} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
        {name}
      </text>
      <text x={x + w / 2} y={y + h * 0.72} textAnchor="middle" dominantBaseline="central" fontSize={20} fill={C.muted} fontFamily={FONT} opacity={1 - fresh}>
        old
      </text>
      <text x={x + w / 2} y={y + h * 0.72} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={700} fill={color} fontFamily={FONT} opacity={fresh}>
        new
      </text>
      {bad > 0.001 && (
        <g opacity={bad}>
          <rect x={x + 2} y={y + 2} width={w - 4} height={h - 4} fill={`${K.bad}33`} stroke={K.bad} strokeWidth={4} />
          <text x={x + w - 16} y={y + 18} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={K.bad} fontFamily={FONT}>
            ✗
          </text>
        </g>
      )}
      {badge && badge[1] > 0.001 && (
        <g opacity={badge[1]}>
          <circle cx={x + 18} cy={y + 18} r={14} fill={C.bg} stroke={color} strokeWidth={2.5} />
          <text x={x + 18} y={y + 18} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={800} fill={color} fontFamily={FONT}>
            {badge[0]}
          </text>
        </g>
      )}
    </g>
  );
}

/** Store piece in flight (SVG), centred at p; optional order badge. */
export function Piece({ p, label, color = K.store, n, op = 1, w = 96 }: { p: Pt; label: string; color?: string; n?: number; op?: number; w?: number }) {
  if (op <= 0.001) return null;
  return (
    <g opacity={op}>
      <rect x={p[0] - w / 2} y={p[1] - 16} width={w} height={32} rx={6} fill={color} stroke={C.bg} strokeWidth={1.5} />
      <text x={p[0]} y={p[1]} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={800} fill={C.bg} fontFamily={FONT}>
        {label}
      </text>
      {n !== undefined && (
        <g>
          <circle cx={p[0] - w / 2} cy={p[1] - 16} r={14} fill={C.bg} stroke={color} strokeWidth={2.5} />
          <text x={p[0] - w / 2} y={p[1] - 16} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={800} fill={color} fontFamily={FONT}>
            {n}
          </text>
        </g>
      )}
    </g>
  );
}

/** Monospace listing whose line highlight fades (hl(i) ∈ 0..1). */
export function CodeBox({ x, y, w, lines, hl = () => 0, size = 24, style }: { x: number; y: number; w: number; lines: string[]; hl?: (i: number) => number; size?: number; style?: React.CSSProperties }) {
  return (
    <div style={{ position: "absolute", left: x, top: y, width: w, boxSizing: "border-box", fontFamily: "'JetBrains Mono', monospace", fontSize: size, background: "#0f1528", border: `2px solid ${C.line}`, borderRadius: 14, padding: "12px 20px", lineHeight: 1.45, whiteSpace: "pre", ...style }}>
      {lines.map((l, i) => {
        const h = clamp01(hl(i));
        return (
          <div key={i} style={{ color: l.trim().startsWith("//") ? C.muted : C.text, background: `rgba(249,199,79,${0.16 * h})`, boxShadow: h > 0.01 ? `inset 3px 0 0 rgba(249,199,79,${h})` : undefined, borderRadius: 6, margin: "0 -8px", padding: "0 8px" }}>
            {l || " "}
          </div>
        );
      })}
    </div>
  );
}
