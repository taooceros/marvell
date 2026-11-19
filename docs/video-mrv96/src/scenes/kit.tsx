/**
 * Shared vocabulary for the MRV-96 video (host ⇄ OCTEON 10 DPU direct path, six single-variable experiments).
 * Parent-written; scene agents use it and never edit it (add private helpers in _<Agent>.tsx instead).
 *
 * HTML (render OUTSIDE <Stage>):  KnobBar, Takeaway, InfPill, Chip
 * SVG  (render INSIDE  <Stage>):  Rig + rigGeom/rigRoutes, RingStrip + ringCenter, BarChart, HBarChart, SegBar,
 *                                 Meter, InfTag, Brace
 * All sizes are in 1920×1080 frame pixels. Labels are ≥ 22 px; keep any scale ≥ 0.8.
 */
import React from "react";
import { C, FONT, K, MONO, Pt, clamp01, fmt, lerp } from "../lib";

// ================================================================= HTML pieces
/** Small rounded chip (HTML). `tone` picks the colour family; `on` fills it. */
export function Chip({
  children,
  color = K.fixed,
  on = false,
  size = 24,
  style,
}: {
  children: React.ReactNode;
  color?: string;
  on?: boolean;
  size?: number;
  style?: React.CSSProperties;
}) {
  return (
    <span
      style={{
        display: "inline-block",
        fontSize: size,
        lineHeight: 1.15,
        fontWeight: on ? 800 : 600,
        color: on ? C.bg : color,
        background: on ? color : "rgba(20,27,46,0.9)",
        border: `2px solid ${color}`,
        borderRadius: 999,
        padding: "5px 16px",
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {children}
    </span>
  );
}

/**
 * The experiment frame, one row at the top of the content band (default y 118, height ~62):
 * grey "FIXED" chips on the left, then the yellow "ONE KNOB" with its settings as a segmented control.
 * `active` is the index of the current setting; a fractional value slides the highlight between two
 * settings (animate it with lerp/prog). active < 0 shows no setting selected. `k` fades the bar in.
 */
export function KnobBar({
  fixed,
  knob,
  options,
  active,
  k = 1,
  x = 60,
  y = 118,
  w = 1800,
  size = 24,
}: {
  fixed: string[];
  knob: string;
  options: string[];
  active: number;
  k?: number;
  x?: number;
  y?: number;
  w?: number;
  size?: number;
}) {
  const a = Math.max(-1, Math.min(options.length - 1, active));
  const segW = Math.max(...options.map((o) => o.length)) * size * 0.62 + 40;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        display: "flex",
        alignItems: "center",
        gap: 14,
        opacity: k,
        transform: `translateY(${(1 - k) * -12}px)`,
        fontFamily: FONT,
      }}
    >
      <span style={{ fontSize: size - 4, fontWeight: 800, letterSpacing: 2, color: K.fixed }}>FIXED</span>
      {fixed.map((f, i) => (
        <Chip key={i} color={K.fixed} size={size}>
          {f}
        </Chip>
      ))}
      <span style={{ flex: 1 }} />
      <span style={{ fontSize: size - 4, fontWeight: 800, letterSpacing: 2, color: K.knob }}>ONE KNOB</span>
      <span style={{ fontSize: size, fontWeight: 700, color: K.knob }}>{knob}</span>
      <span
        style={{
          position: "relative",
          display: "inline-flex",
          border: `2px solid ${K.knob}`,
          borderRadius: 999,
          padding: 3,
          background: "rgba(20,27,46,0.9)",
        }}
      >
        {a >= 0 && (
          <span
            style={{
              position: "absolute",
              left: 3 + a * segW,
              top: 3,
              width: segW,
              bottom: 3,
              borderRadius: 999,
              background: K.knob,
              boxShadow: `0 0 18px ${K.knob}88`,
            }}
          />
        )}
        {options.map((o, i) => {
          const near = a >= 0 ? clamp01(1 - Math.abs(a - i)) : 0;
          return (
            <span
              key={i}
              style={{
                position: "relative",
                width: segW,
                textAlign: "center",
                fontSize: size,
                fontWeight: 800,
                padding: "3px 0",
                color: near > 0.5 ? C.bg : K.knob,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {o}
            </span>
          );
        })}
      </span>
    </div>
  );
}

/** Conclusion banner (HTML): accent bar on the left, ≥ 30 px text. Use for the verdict / takeaway of a scene. */
export function Takeaway({
  x,
  y,
  w,
  k = 1,
  color = K.fast,
  label = "TAKEAWAY",
  size = 32,
  children,
}: {
  x: number;
  y: number;
  w: number;
  k?: number;
  color?: string;
  label?: string;
  size?: number;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        opacity: k,
        transform: `translateY(${(1 - k) * 20}px)`,
        background: "rgba(20,27,46,0.94)",
        border: `2px solid ${color}`,
        borderLeft: `12px solid ${color}`,
        borderRadius: 16,
        padding: "16px 26px",
        boxSizing: "border-box",
        fontFamily: FONT,
      }}
    >
      <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 2, color, marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: size, fontWeight: 600, color: C.text, lineHeight: 1.3 }}>{children}</div>
    </div>
  );
}

/** "[INF]" tag as HTML (inline-block). Pair it with every interpretation shown on screen. */
export function InfPill({ size = 22, style }: { size?: number; style?: React.CSSProperties }) {
  return (
    <span
      style={{
        display: "inline-block",
        fontFamily: MONO,
        fontSize: size,
        fontWeight: 700,
        color: K.inf,
        border: `2px solid ${K.inf}`,
        borderRadius: 8,
        padding: "1px 8px",
        lineHeight: 1.2,
        verticalAlign: "middle",
        ...style,
      }}
    >
      [INF]
    </span>
  );
}

// ================================================================= SVG pieces
/** "[INF]" tag (SVG), top-left at (x, y). */
export function InfTag({ x, y, opacity = 1, size = 22 }: { x: number; y: number; opacity?: number; size?: number }) {
  const w = size * 3.3;
  const h = size * 1.45;
  return (
    <g opacity={opacity}>
      <rect x={x} y={y} width={w} height={h} rx={7} fill="rgba(11,16,32,0.9)" stroke={K.inf} strokeWidth={2} />
      <text x={x + w / 2} y={y + h / 2 + 1} textAnchor="middle" dominantBaseline="central" fontFamily={MONO} fontSize={size}
        fontWeight={700} fill={K.inf}>
        [INF]
      </text>
    </g>
  );
}

/** Horizontal brace under/over a span [x1, x2] at y (dir 1 = opens downward, label below). */
export function Brace({
  x1,
  x2,
  y,
  label,
  color = C.muted,
  dir = 1,
  size = 24,
  opacity = 1,
}: {
  x1: number;
  x2: number;
  y: number;
  label?: React.ReactNode;
  color?: string;
  dir?: 1 | -1;
  size?: number;
  opacity?: number;
}) {
  const h = 14 * dir;
  const m = (x1 + x2) / 2;
  return (
    <g opacity={opacity}>
      <path d={`M${x1} ${y} L${x1} ${y + h} L${m - 10} ${y + h} L${m} ${y + h * 1.8} L${m + 10} ${y + h} L${x2} ${y + h} L${x2} ${y}`}
        fill="none" stroke={color} strokeWidth={2.5} strokeLinejoin="round" />
      {label !== undefined && (
        <text x={m} y={y + h * 1.8 + (dir > 0 ? size + 4 : -10)} textAnchor="middle" fontSize={size} fill={color} fontWeight={600}>
          {label}
        </text>
      )}
    </g>
  );
}

/** Horizontal gauge: track [x, x+w] × h, filled to value/max; label left above, valueText right above. */
export function Meter({
  x,
  y,
  w,
  h = 26,
  value,
  max,
  color = K.fwd,
  label,
  valueText,
  k = 1,
  marks = [],
  size = 24,
  opacity = 1,
}: {
  x: number;
  y: number;
  w: number;
  h?: number;
  value: number;
  max: number;
  color?: string;
  label?: React.ReactNode;
  valueText?: React.ReactNode;
  k?: number;
  marks?: { at: number; label?: string; color?: string }[];
  size?: number;
  opacity?: number;
}) {
  const fw = Math.max(0, Math.min(w, (w * value * clamp01(k)) / max));
  return (
    <g opacity={opacity}>
      {label !== undefined && (
        <text x={x} y={y - 10} fontSize={size} fill={C.muted} fontWeight={600}>
          {label}
        </text>
      )}
      {valueText !== undefined && (
        <text x={x + w} y={y - 10} fontSize={size} fill={color} fontWeight={800} textAnchor="end" opacity={clamp01(k * 2)}>
          {valueText}
        </text>
      )}
      <rect x={x} y={y} width={w} height={h} rx={h / 2} fill="#0f1528" stroke={C.line} strokeWidth={2} />
      {fw > 0 && <rect x={x} y={y} width={Math.max(h, fw)} height={h} rx={h / 2} fill={color} opacity={0.9} />}
      {marks.map((m, i) => {
        const mx = x + (w * m.at) / max;
        return (
          <g key={i}>
            <line x1={mx} y1={y - 6} x2={mx} y2={y + h + 6} stroke={m.color ?? C.text} strokeWidth={2.5} strokeDasharray="5 4" />
            {m.label && (
              <text x={mx} y={y + h + 30} fontSize={size - 2} fill={m.color ?? C.muted} textAnchor="middle">
                {m.label}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
}

export type Bar = { label: string; value: number; color?: string; sub?: string; text?: string };

/**
 * Vertical bars in [x0, x1] × [y1 (top), y0 (bottom)]. Bar i grows with k(i) ∈ [0,1]; its value label
 * (`text`, else value with `dec` decimals + unit) fades in WITH the bar (never counts up), so a paused
 * frame never shows an intermediate number. Category label under each bar, optional `sub` line below it.
 */
export function BarChart({
  x0,
  x1,
  y0,
  y1,
  bars,
  max,
  k,
  dec = 1,
  unit = "",
  size = 26,
  labelSize = 24,
  barW,
  refLine,
  opacity = 1,
}: {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
  bars: Bar[];
  max: number;
  k: (i: number) => number;
  dec?: number;
  unit?: string;
  size?: number;
  labelSize?: number;
  barW?: number;
  refLine?: { value: number; label: string; color?: string };
  opacity?: number;
}) {
  const n = bars.length;
  const slot = (x1 - x0) / n;
  const bw = barW ?? Math.min(150, slot * 0.6);
  const sy = (v: number) => y0 - ((y0 - y1) * v) / max;
  return (
    <g opacity={opacity}>
      <line x1={x0} y1={y0} x2={x1} y2={y0} stroke={C.line} strokeWidth={3} />
      {refLine && (
        <g>
          <line x1={x0} y1={sy(refLine.value)} x2={x1} y2={sy(refLine.value)} stroke={refLine.color ?? C.muted} strokeWidth={2.5} strokeDasharray="8 6" />
          <text x={x0 + 8} y={sy(refLine.value) - 10} textAnchor="start" fontSize={labelSize - 2} fill={refLine.color ?? C.muted}>
            {refLine.label}
          </text>
        </g>
      )}
      {bars.map((b, i) => {
        const kk = clamp01(k(i));
        const cx = x0 + slot * (i + 0.5);
        const top = sy(b.value * kk);
        const col = b.color ?? K.fwd;
        return (
          <g key={i}>
            {kk > 0 && <rect x={cx - bw / 2} y={top} width={bw} height={Math.max(0, y0 - top)} rx={8} fill={col} opacity={0.9} />}
            <text x={cx} y={sy(b.value) - 14} textAnchor="middle" fontSize={size} fontWeight={800} fill={col}
              opacity={clamp01((kk - 0.6) / 0.4)} style={{ fontVariantNumeric: "tabular-nums" }}>
              {b.text ?? `${fmt(b.value, dec)}${unit}`}
            </text>
            <text x={cx} y={y0 + labelSize + 12} textAnchor="middle" fontSize={labelSize} fill={C.text} fontWeight={600}>
              {b.label}
            </text>
            {b.sub && (
              <text x={cx} y={y0 + 2 * labelSize + 18} textAnchor="middle" fontSize={labelSize - 2} fill={C.muted}>
                {b.sub}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
}

/**
 * Horizontal bars: categories stacked top → bottom in [y1, y0], values grow rightward from x0 to x1.
 * Category label at the left of x0 (right-aligned, width `labelW`), value label at the bar's end.
 */
export function HBarChart({
  x0,
  x1,
  y1,
  y0,
  bars,
  max,
  k,
  dec = 1,
  unit = "",
  size = 26,
  labelSize = 24,
  barH,
  refLine,
  opacity = 1,
}: {
  x0: number;
  x1: number;
  y1: number;
  y0: number;
  bars: Bar[];
  max: number;
  k: (i: number) => number;
  dec?: number;
  unit?: string;
  size?: number;
  labelSize?: number;
  barH?: number;
  refLine?: { value: number; label: string; color?: string };
  opacity?: number;
}) {
  const n = bars.length;
  const slot = (y0 - y1) / n;
  const bh = barH ?? Math.min(54, slot * 0.62);
  const sx = (v: number) => x0 + ((x1 - x0) * v) / max;
  return (
    <g opacity={opacity}>
      <line x1={x0} y1={y1 - 8} x2={x0} y2={y0 + 8} stroke={C.line} strokeWidth={3} />
      {refLine && (
        <g>
          <line x1={sx(refLine.value)} y1={y1 - 16} x2={sx(refLine.value)} y2={y0 + 10} stroke={refLine.color ?? C.muted} strokeWidth={2.5} strokeDasharray="8 6" />
          <text x={sx(refLine.value)} y={y1 - 24} textAnchor="middle" fontSize={labelSize - 2} fill={refLine.color ?? C.muted}>
            {refLine.label}
          </text>
        </g>
      )}
      {bars.map((b, i) => {
        const kk = clamp01(k(i));
        const cy = y1 + slot * (i + 0.5);
        const right = sx(b.value * kk);
        const col = b.color ?? K.fwd;
        return (
          <g key={i}>
            <text x={x0 - 16} y={cy + (b.sub ? -4 : labelSize * 0.35)} textAnchor="end" fontSize={labelSize} fill={C.text} fontWeight={600}>
              {b.label}
            </text>
            {b.sub && (
              <text x={x0 - 16} y={cy + labelSize} textAnchor="end" fontSize={labelSize - 4} fill={C.muted}>
                {b.sub}
              </text>
            )}
            {kk > 0 && <rect x={x0} y={cy - bh / 2} width={Math.max(0, right - x0)} height={bh} rx={8} fill={col} opacity={0.9} />}
            <text x={sx(b.value) + 14} y={cy + size * 0.35} fontSize={size} fontWeight={800} fill={col}
              opacity={clamp01((kk - 0.6) / 0.4)} style={{ fontVariantNumeric: "tabular-nums" }}>
              {b.text ?? `${fmt(b.value, dec)}${unit}`}
            </text>
          </g>
        );
      })}
    </g>
  );
}

export type Seg = { label: string; value: number; color: string; text?: string };

/**
 * Stacked horizontal bar (e.g. a round trip split into forward · on card · return). Segments are laid left →
 * right from x at `px` pixels per unit; `k` ∈ [0,1] reveals them in order. A segment's label sits inside when
 * it is wide enough; otherwise it goes above the bar, or below when the above row is already taken there.
 */
export function SegBar({
  x,
  y,
  h = 60,
  px,
  segs,
  k = 1,
  size = 24,
  opacity = 1,
}: {
  x: number;
  y: number;
  h?: number;
  px: number;
  segs: Seg[];
  k?: number;
  size?: number;
  opacity?: number;
}) {
  const total = segs.reduce((s, g) => s + g.value, 0);
  // Lay labels out first: inside, or the first outside row (above, below, further below) still free there.
  const rowEnd = [-Infinity, -Infinity, -Infinity];
  let acc = 0;
  const placed = segs.map((g) => {
    const x0 = x + acc * px;
    acc += g.value;
    const wFull = g.value * px;
    const t = g.text ?? g.label;
    const tw = t.length * size * 0.56;
    const cx = x0 + wFull / 2;
    if (wFull >= tw + 16) return { x0, wFull, t, cx, row: -1 };
    const free = rowEnd.findIndex((end) => end + 12 <= cx - tw / 2);
    const row = free < 0 ? 2 : free;
    rowEnd[row] = cx + tw / 2;
    return { x0, wFull, t, cx, row };
  });
  return (
    <g opacity={opacity}>
      {segs.map((g, i) => {
        const p = placed[i];
        const a = (p.x0 - x) / (total * px);
        const b = a + g.value / total;
        const kk = clamp01((k - a) / Math.max(1e-6, b - a));
        const w = p.wFull * kk;
        return (
          <g key={i}>
            {w > 0 && <rect x={p.x0} y={y} width={Math.max(2, w)} height={h} rx={6} fill={g.color} opacity={0.92} />}
            {kk > 0.6 &&
              (p.row < 0 ? (
                <text x={p.cx} y={y + h / 2} textAnchor="middle" dominantBaseline="central" fontSize={size} fontWeight={800}
                  fill={C.bg}>
                  {p.t}
                </text>
              ) : (
                <text x={p.cx} y={p.row === 0 ? y - 12 : y + h + size + 6 + (p.row - 1) * (size + 8)} textAnchor="middle" fontSize={size} fontWeight={700}
                  fill={g.color}>
                  {p.t}
                </text>
              ))}
          </g>
        );
      })}
    </g>
  );
}

// ----------------------------------------------------------------- request ring strip
export type CellState = "consumed" | "landed" | "inflight" | "hole" | "empty";

/** Centre x of slot i of a RingStrip with the same x / cw / gap. */
export const ringCenter = (x: number, cw: number, gap: number, i: number) => x + i * (cw + gap) + cw / 2;

/**
 * A window onto the card's request ring: n slots left → right (oldest → newest).
 *  consumed = already taken by the card core (dark) · landed = written, visible (cyan)
 *  inflight = host write still landing (red hatch, shimmering with `flicker` = useRealT())
 *  hole = an older slot still missing while newer ones have landed (dashed red outline) · empty = not written yet.
 * `nextAt` (float slot index) draws the card core's `next` cursor above the strip; `band` shades a slot range
 * with a label below; `probes` draw read markers under the strip (where a core reads).
 */
export function RingStrip({
  x,
  y,
  n,
  cw = 40,
  ch = 64,
  gap = 6,
  state,
  flicker = 0,
  nextAt,
  nextLabel = "next",
  nextColor = K.card,
  band,
  probes = [],
  opacity = 1,
  size = 24,
}: {
  x: number;
  y: number;
  n: number;
  cw?: number;
  ch?: number;
  gap?: number;
  state: (i: number) => CellState;
  flicker?: number;
  nextAt?: number;
  nextLabel?: string;
  nextColor?: string;
  band?: { from: number; to: number; label?: string; color?: string; k?: number };
  probes?: { from: number; to: number; color?: string; label?: string; k?: number }[];
  opacity?: number;
  size?: number;
}) {
  const sx = (i: number) => x + i * (cw + gap);
  const bandK = band ? clamp01(band.k ?? 1) : 0;
  return (
    <g opacity={opacity}>
      {band && bandK > 0 && (
        <g opacity={bandK}>
          <rect x={sx(band.from) - gap} y={y - 10} width={sx(band.to) - sx(band.from) + gap} height={ch + 20} rx={12}
            fill={(band.color ?? K.inflight) + "22"} stroke={band.color ?? K.inflight} strokeWidth={2} strokeDasharray="8 6" />
          {band.label && (
            <text x={(sx(band.from) + sx(band.to) - gap) / 2} y={y + ch + 10 + size + 8} textAnchor="middle" fontSize={size}
              fontWeight={700} fill={band.color ?? K.inflight}>
              {band.label}
            </text>
          )}
        </g>
      )}
      {Array.from({ length: n }, (_, i) => {
        const s = state(i);
        const px = sx(i);
        if (s === "consumed")
          return <rect key={i} x={px} y={y} width={cw} height={ch} rx={6} fill={K.consumed} stroke={C.line} strokeWidth={1.5} />;
        if (s === "landed")
          return <rect key={i} x={px} y={y} width={cw} height={ch} rx={6} fill={K.landed} opacity={0.88} stroke={K.landed} strokeWidth={2} />;
        if (s === "inflight") {
          const ph = Math.sin(flicker * 9 + i * 2.17) * 0.5 + 0.5;
          return (
            <g key={i} opacity={0.55 + 0.45 * ph}>
              <rect x={px} y={y} width={cw} height={ch} rx={6} fill="url(#hatchRed)" stroke={K.inflight} strokeWidth={2} />
            </g>
          );
        }
        if (s === "hole")
          return <rect key={i} x={px} y={y} width={cw} height={ch} rx={6} fill="#0b1020" stroke={K.inflight} strokeWidth={2.5} strokeDasharray="5 4" />;
        return <rect key={i} x={px} y={y} width={cw} height={ch} rx={6} fill="none" stroke={C.line} strokeWidth={1.5} strokeDasharray="4 5" />;
      })}
      {probes.map((p, j) => {
        const pk = clamp01(p.k ?? 1);
        if (pk <= 0) return null;
        const col = p.color ?? K.probe;
        const a = sx(p.from);
        const b = sx(p.to) - gap;
        return (
          <g key={j} opacity={pk}>
            <rect x={a} y={y + ch + 12} width={Math.max(cw, b - a)} height={10} rx={5} fill={col} />
            {p.label && (
              <text x={(a + Math.max(a + cw, b)) / 2} y={y + ch + 12 + 10 + size + 6} textAnchor="middle" fontSize={size}
                fontWeight={700} fill={col}>
                {p.label}
              </text>
            )}
          </g>
        );
      })}
      {nextAt !== undefined && (
        <g transform={`translate(${ringCenter(x, cw, gap, nextAt)}, ${y - 12})`}>
          <polygon points="-14,-22 14,-22 0,0" fill={nextColor} />
          <text y={-32} textAnchor="middle" fontSize={size} fontWeight={800} fill={nextColor} fontFamily={MONO}>
            {nextLabel}
          </text>
        </g>
      )}
    </g>
  );
}

// ----------------------------------------------------------------- the host ⇄ card loop diagram
export type RigBlock = "host" | "card" | "hcore" | "wq" | "hring" | "cring" | "ccores" | "wc" | "link";
type Rect = { x: number; y: number; w: number; h: number };

// Base layout (s = 1, no offset): host panel left, card panel right, forward lane on top, return lane below.
const RIG: Record<RigBlock, Rect> = {
  host: { x: 60, y: 200, w: 520, h: 660 },
  card: { x: 1340, y: 200, w: 520, h: 660 },
  hcore: { x: 96, y: 455, w: 220, h: 170 },
  wq: { x: 346, y: 285, w: 200, h: 150 },
  hring: { x: 346, y: 625, w: 200, h: 150 },
  cring: { x: 1374, y: 285, w: 200, h: 150 },
  ccores: { x: 1604, y: 455, w: 220, h: 170 },
  wc: { x: 1374, y: 625, w: 200, h: 150 },
  link: { x: 580, y: 332, w: 760, h: 416 },
};

export const RIG_LABEL: Record<RigBlock, [string, string?]> = {
  host: ["HOST", "Intel Xeon"],
  card: ["DPU", "Marvell OCTEON 10"],
  hcore: ["core 40", "3.9 GHz"],
  wq: ["write queue", "CHA"],
  hring: ["reply ring", "host memory"],
  cring: ["request ring", "card memory"],
  ccores: ["Arm core", "2.0 GHz"],
  wc: ["WC buffer", "64 B"],
  link: ["PCIe Gen5 ×4"],
};

export type RigGeom = { s: number; dx: number; dy: number; r: Record<RigBlock, Rect>; fwdY: number; retY: number };

/** Geometry of the loop diagram scaled by s about the frame origin, then shifted by (dx, dy). */
export function rigGeom(s = 1, dx = 0, dy = 0): RigGeom {
  const r = {} as Record<RigBlock, Rect>;
  (Object.keys(RIG) as RigBlock[]).forEach((b) => {
    const q = RIG[b];
    r[b] = { x: q.x * s + dx, y: q.y * s + dy, w: q.w * s, h: q.h * s };
  });
  return { s, dx, dy, r, fwdY: 372 * s + dy, retY: 708 * s + dy };
}

/**
 * Named polylines (frame coords) for packets / Link / partial():
 *  send  host core → write queue      fwd   write queue → PCIe → request ring (MOVDIR64B)
 *  poll  card core ↔ request ring     reply card core → WC buffer
 *  ret   WC buffer → PCIe → reply ring hpoll reply ring → host core
 *  loop  the whole cycle: send + fwd + poll + reply + ret + hpoll
 */
export function rigRoutes(g: RigGeom) {
  const { r } = g;
  const hc = r.hcore;
  const cc = r.ccores;
  const sendP: Pt[] = [[hc.x + hc.w * 0.5, hc.y], [hc.x + hc.w * 0.5, g.fwdY], [r.wq.x, g.fwdY]];
  const fwdP: Pt[] = [[r.wq.x + r.wq.w, g.fwdY], [r.cring.x, g.fwdY]];
  const pollP: Pt[] = [[r.cring.x + r.cring.w, g.fwdY], [cc.x + cc.w * 0.5, g.fwdY], [cc.x + cc.w * 0.5, cc.y]];
  const replyP: Pt[] = [[cc.x + cc.w * 0.5, cc.y + cc.h], [cc.x + cc.w * 0.5, g.retY], [r.wc.x + r.wc.w, g.retY]];
  const retP: Pt[] = [[r.wc.x, g.retY], [r.hring.x + r.hring.w, g.retY]];
  const hpollP: Pt[] = [[r.hring.x, g.retY], [hc.x + hc.w * 0.5, g.retY], [hc.x + hc.w * 0.5, hc.y + hc.h]];
  return {
    send: sendP,
    fwd: fwdP,
    poll: pollP,
    reply: replyP,
    ret: retP,
    hpoll: hpollP,
    loop: [...sendP, ...fwdP, ...pollP, ...replyP, ...retP, ...hpollP] as Pt[],
  };
}

/** Small ring icon (row of `n` cells, `fill` of them lit) inside a block. */
function MiniRing({ q, n, fill, color, s }: { q: Rect; n: number; fill: number; color: string; s: number }) {
  const pad = 14 * s;
  const cw = (q.w - 2 * pad - (n - 1) * 4 * s) / n;
  const y = q.y + q.h - pad - 26 * s;
  return (
    <g>
      {Array.from({ length: n }, (_, i) => (
        <rect key={i} x={q.x + pad + i * (cw + 4 * s)} y={y} width={Math.max(2, cw)} height={26 * s} rx={4 * s}
          fill={i < fill ? color : "none"} opacity={i < fill ? 0.85 : 1} stroke={i < fill ? color : C.line} strokeWidth={1.5} />
      ))}
    </g>
  );
}

/**
 * The host ⇄ card loop diagram (SVG). show[b] (0..1) fades a block in (default 1); hl[b] (0..1) glows it in
 * yellow; dim (0..1) fades every non-highlighted block toward 30 %. `cores` card-core squares are drawn in
 * the "ccores" block (1–4); `ringFill` lights cells in the two rings; `lanes` fade the two PCIe lanes
 * (labels "MOVDIR64B →" and "← write-combining"). Override any label with `labels`.
 */
export function Rig({
  g,
  show = {},
  hl = {},
  dim = 0,
  labels = {},
  cores = 1,
  activeCore = -1,
  ringFill = {},
  lanes = { fwd: 1, ret: 1 },
  laneText = true,
  opacity = 1,
}: {
  g: RigGeom;
  show?: Partial<Record<RigBlock, number>>;
  hl?: Partial<Record<RigBlock, number>>;
  dim?: number;
  labels?: Partial<Record<RigBlock, [string, string?]>>;
  cores?: number;
  activeCore?: number;
  ringFill?: { cring?: number; hring?: number };
  lanes?: { fwd?: number; ret?: number };
  laneText?: boolean;
  opacity?: number;
}) {
  const { r, s } = g;
  const fs = (v: number) => Math.max(20, v * s);
  const op = (b: RigBlock) => {
    const sh = show[b] ?? 1;
    const h = hl[b] ?? 0;
    return sh * lerp(1, Math.max(h, 0.3), dim);
  };
  const lab = (b: RigBlock) => labels[b] ?? RIG_LABEL[b];
  const block = (b: RigBlock, color: string) => {
    const q = r[b];
    const h = hl[b] ?? 0;
    const [main, sub] = lab(b);
    return (
      <g key={b} opacity={op(b)}>
        {h > 0 && <rect x={q.x - 6} y={q.y - 6} width={q.w + 12} height={q.h + 12} rx={18 * s} fill="none" stroke={K.knob}
          strokeWidth={10} opacity={0.25 * h} />}
        <rect x={q.x} y={q.y} width={q.w} height={q.h} rx={14 * s} fill={C.panel} stroke={h > 0.5 ? K.knob : color}
          strokeWidth={h > 0.5 ? 3.5 : 2.5} />
        <text x={q.x + q.w / 2} y={q.y + 34 * s} textAnchor="middle" fontSize={fs(26)} fontWeight={800} fill={C.text}>
          {main}
        </text>
        {sub && (
          <text x={q.x + q.w / 2} y={q.y + 34 * s + fs(24)} textAnchor="middle" fontSize={fs(22)} fill={C.muted}>
            {sub}
          </text>
        )}
      </g>
    );
  };
  const panel = (b: "host" | "card", color: string) => {
    const q = r[b];
    const [main, sub] = lab(b);
    return (
      <g key={b} opacity={show[b] ?? 1}>
        <rect x={q.x} y={q.y} width={q.w} height={q.h} rx={22 * s} fill="rgba(20,27,46,0.55)" stroke={color} strokeWidth={2.5}
          strokeDasharray="10 8" />
        <text x={q.x + 24 * s} y={q.y + 44 * s} fontSize={fs(30)} fontWeight={800} fill={color} letterSpacing={2}>
          {main}
        </text>
        {sub && (
          <text x={q.x + q.w - 24 * s} y={q.y + 44 * s} textAnchor="end" fontSize={fs(24)} fill={C.muted}>
            {sub}
          </text>
        )}
      </g>
    );
  };
  const fwdK = clamp01(lanes.fwd ?? 1);
  const retK = clamp01(lanes.ret ?? 1);
  const L = r.link;
  const n = Math.max(1, Math.min(4, Math.round(cores)));
  const cc = r.ccores;
  const cs = Math.min((cc.w - 40 * s) / n - 8 * s, 52 * s);
  return (
    <g opacity={opacity} fontFamily={FONT}>
      {panel("host", K.host)}
      {panel("card", K.card)}
      {/* PCIe band */}
      <g opacity={show.link ?? 1}>
        <rect x={L.x} y={L.y} width={L.w} height={L.h} fill="rgba(100,116,139,0.07)" />
        <text x={L.x + L.w / 2} y={(g.fwdY + g.retY) / 2 + 10} textAnchor="middle" fontSize={fs(26)} fill={C.muted} fontWeight={700}>
          {lab("link")[0]}
        </text>
      </g>
      {fwdK > 0 && (
        <g opacity={fwdK}>
          <line x1={r.wq.x + r.wq.w} y1={g.fwdY} x2={r.cring.x - 12} y2={g.fwdY} stroke={K.fwd} strokeWidth={5} />
          <polygon points={`${r.cring.x},${g.fwdY} ${r.cring.x - 18},${g.fwdY - 10} ${r.cring.x - 18},${g.fwdY + 10}`} fill={K.fwd} />
          {laneText && (
            <text x={L.x + L.w / 2} y={g.fwdY - 18} textAnchor="middle" fontSize={fs(26)} fontWeight={700} fill={K.fwd} fontFamily={MONO}>
              MOVDIR64B →
            </text>
          )}
        </g>
      )}
      {retK > 0 && (
        <g opacity={retK}>
          <line x1={r.wc.x} y1={g.retY} x2={r.hring.x + r.hring.w + 12} y2={g.retY} stroke={K.ret} strokeWidth={5} />
          <polygon points={`${r.hring.x + r.hring.w},${g.retY} ${r.hring.x + r.hring.w + 18},${g.retY - 10} ${r.hring.x + r.hring.w + 18},${g.retY + 10}`}
            fill={K.ret} />
          {laneText && (
            <text x={L.x + L.w / 2} y={g.retY + 40} textAnchor="middle" fontSize={fs(26)} fontWeight={700} fill={K.ret} fontFamily={MONO}>
              ← write-combining
            </text>
          )}
        </g>
      )}
      {block("hcore", K.host)}
      {block("wq", K.host)}
      {block("hring", K.host)}
      {block("cring", K.card)}
      {block("wc", K.card)}
      {block("ccores", K.card)}
      <g opacity={op("hring")}>
        <MiniRing q={r.hring} n={8} fill={ringFill.hring ?? 0} color={K.ret} s={s} />
      </g>
      <g opacity={op("cring")}>
        <MiniRing q={r.cring} n={8} fill={ringFill.cring ?? 0} color={K.fwd} s={s} />
      </g>
      <g opacity={op("ccores")}>
        {Array.from({ length: n }, (_, i) => {
          const x = cc.x + (cc.w - n * cs - (n - 1) * 8 * s) / 2 + i * (cs + 8 * s);
          const y = cc.y + cc.h - cs - 18 * s;
          const on = i === activeCore;
          return (
            <rect key={i} x={x} y={y} width={cs} height={cs} rx={8 * s} fill={on ? K.card : "rgba(248,150,30,0.18)"}
              stroke={K.card} strokeWidth={2} />
          );
        })}
      </g>
    </g>
  );
}

/** Point at the centre of a rig block (for callouts). */
export function rigMid(g: RigGeom, b: RigBlock): Pt {
  const q = g.r[b];
  return [q.x + q.w / 2, q.y + q.h / 2];
}
