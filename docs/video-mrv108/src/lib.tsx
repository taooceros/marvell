import React, { createContext, useContext } from "react";
import { AbsoluteFill, Easing, Img, Sequence, staticFile, useCurrentFrame } from "remotion";
import { Audio } from "@remotion/media";
import voice from "./voice.json";

export const FPS = 30;
export const W = 1920;
export const H = 1080;

/** [start, end, caption, spoken?] in script seconds. Caption supports **accent** and `code`. */
export type Cue = [number, number, string, string?];

export type SceneDef = {
  id: string;
  title: string; // small section label, top-left (keep ≤ 12 CJK chars)
  source?: string; // provenance, small mono line under the title
  stage?: number; // highlighted step of the PipelineRail (index into STAGES); omit to hide the rail
  dur: number; // script seconds
  cues: Cue[];
  C: React.FC;
};

// ---------------------------------------------------------------- theme
export const C = {
  bg: "#0b1020",
  panel: "#141b2e",
  line: "#2a3552",
  text: "#e8ecf5",
  muted: "#8b95ad",
  dim: "#4a5572",
  cyan: "#4cc9f0",
  orange: "#f8961e",
  green: "#43c59e",
  red: "#f94144",
  purple: "#b388ff",
  yellow: "#f9c74f",
  pink: "#ff70a6",
  blue: "#7c93d6",
};
export const FONT = "'Inter', 'Noto Sans SC', sans-serif";
export const MONO = "'JetBrains Mono', monospace";

/**
 * Semantic colours — CN106 DPU <-> Host Intel DSA bidirectional PCIe P2P.
 */
export const K = {
  // lib defaults (keep)
  data: C.cyan,
  ack: C.green,
  nack: C.orange,
  ecn: C.yellow,
  loss: C.red,
  fail: C.red,
  reorder: C.yellow,
  pause: C.red,
  ok: C.green,
  nic: C.cyan,
  // sides
  host: "#7c93d6",
  card: C.orange,
  link: "#5eead4",
  dsa: C.purple,
  // paths & mechanisms
  fwd: C.cyan,      // forward P2P 64 B descriptor (DPI -> dmar0 -> dsa2 portal)
  rev: C.green,     // reverse P2P 32 B completion (dsa2 -> dmar6 -> card BAR4 L3)
  batch: C.yellow,  // DSA Batch opcode=0x01 aggregation
  hostComp: "#8b95ad", // control group: comp in host Memzone (DPI read-back)
  wc: C.orange,     // PEM0 WC submit (silent drops)
  dpi: C.cyan,      // DPI DMA 64 B atomic submit
  bubble: C.red,    // 131 ns drain bubble / 1.21 us DPI read-back floor
  iommu0: C.cyan,   // dmar0 VFIO portal mapping
  iommu6: C.purple, // dmar6 SVA PASID mapping
  inf: "#5eead4",   // [INF] inference tag
  bad: C.red,
};
export const FLOW = [C.cyan, C.pink, C.yellow, C.green, C.purple, C.orange];
/** Card core colours (core 0..3) for multi-core scenes. */
export const CORE = [C.cyan, C.green, C.yellow, C.pink];

// ---------------------------------------------------------------- narration time warp
const PAD = 0.45; // silence after each narrated line
const voiceLen = voice as Record<string, number>;
export const cueKey = (id: string, i: number) => `${id}_${String(i).padStart(2, "0")}`;

export type Warp = {
  toReal: (s: number) => number;
  toScript: (r: number) => number;
  realDur: number;
};

/** Piecewise-linear map: the segment starting at cue i lasts max(script length, clip + PAD). */
export function makeWarp(id: string, cues: Cue[], dur: number): Warp {
  const starts = cues.map((c) => c[0]);
  const knots = Array.from(new Set([0, ...starts, dur])).sort((a, b) => a - b);
  const real = [0];
  for (let j = 0; j + 1 < knots.length; j++) {
    const len = knots[j + 1] - knots[j];
    const i = starts.indexOf(knots[j]);
    const clip = i >= 0 ? voiceLen[cueKey(id, i)] : undefined;
    real.push(real[j] + (clip === undefined ? len : Math.max(len, clip + PAD)));
  }
  const map = (x: number, from: number[], to: number[]) => {
    if (x <= from[0]) return to[0] + (x - from[0]);
    for (let j = 0; j + 1 < from.length; j++) {
      if (x <= from[j + 1]) {
        const span = from[j + 1] - from[j];
        return to[j] + (span ? ((x - from[j]) / span) * (to[j + 1] - to[j]) : 0);
      }
    }
    const n = from.length - 1;
    return to[n] + (x - from[n]);
  };
  return {
    toReal: (s) => map(s, knots, real),
    toScript: (r) => map(r, real, knots),
    realDur: real[real.length - 1],
  };
}

type Ctx = { def: SceneDef; warp: Warp };
const SceneCtx = createContext<Ctx | null>(null);

/** Script-time seconds of the current frame (animation timeline before narration stretching). */
export function useT(): number {
  const ctx = useContext(SceneCtx);
  const f = useCurrentFrame();
  return ctx ? ctx.warp.toScript(f / FPS) : f / FPS;
}

/** Real (un-warped) seconds since scene start: use for ambient motion (packet streams, pulses) so speed is constant. */
export function useRealT(): number {
  return useCurrentFrame() / FPS;
}

// ---------------------------------------------------------------- easing helpers
export const ease = Easing.bezier(0.45, 0, 0.2, 1);
export const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
/** Eased 0→1 progress of t over [a, a+d]. */
export const prog = (t: number, a: number, d = 0.6) => ease(clamp01((t - a) / d));
export const lin = (t: number, a: number, d: number) => clamp01((t - a) / d);
export const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
/** Fade-in at a, fade-out at b (opacity only). */
export const fade = (t: number, a: number, b = 1e9, d = 0.5) => prog(t, a, d) * (1 - prog(t, b, d));
/** Fade + rise in at a, optional fade-out at b. */
export function appear(t: number, a: number, b?: number, dy = 24): React.CSSProperties {
  const k = prog(t, a, 0.5);
  const out = b === undefined ? 1 : 1 - prog(t, b, 0.5);
  return { opacity: k * out, transform: `translateY(${(1 - k) * dy}px)` };
}
/** Overshooting pop 0→1 (use as a scale). */
export function pop(t: number, a: number, d = 0.5) {
  const x = clamp01((t - a) / d);
  if (x <= 0) return 0;
  const s = 1.7;
  const y = x - 1;
  return 1 + (s + 1) * y * y * y + s * y * y;
}
/** Keyframed position: at time ti move (eased over d) to (xi, yi). keys[0] = start. */
export function track(t: number, keys: [number, number, number][], d = 0.6): [number, number] {
  let x = keys[0][1];
  let y = keys[0][2];
  for (let i = 1; i < keys.length; i++) {
    const k = prog(t, keys[i][0], d);
    if (k <= 0) break;
    x = lerp(x, keys[i][1], k);
    y = lerp(y, keys[i][2], k);
  }
  return [x, y];
}
/** Animated number string: 0 → v over [a, a+d], `dec` decimals, thousands separators. */
export function countUp(t: number, a: number, d: number, v: number, dec = 0, from = 0): string {
  const x = lerp(from, v, prog(t, a, d));
  return fmt(x, dec);
}
/** 4800 -> "4,800"; `digits` decimals. */
export const fmt = (n: number, digits = 0) =>
  n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

// ---------------------------------------------------------------- rich text
export function Rich({ text, accent = C.yellow }: { text: string; accent?: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("**") ? (
          <span key={i} style={{ color: accent, fontWeight: 700 }}>
            {p.slice(2, -2)}
          </span>
        ) : p.startsWith("`") ? (
          <span key={i} style={{ fontFamily: MONO, color: C.cyan, fontSize: "0.92em" }}>
            {p.slice(1, -1)}
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

// ---------------------------------------------------------------- scene shell
function Captions() {
  const ctx = useContext(SceneCtx)!;
  const f = useCurrentFrame();
  const r = f / FPS;
  const { cues } = ctx.def;
  let idx = -1;
  for (let i = 0; i < cues.length; i++) if (ctx.warp.toReal(cues[i][0]) <= r) idx = i;
  if (idx < 0) return null;
  const s = ctx.warp.toReal(cues[idx][0]);
  const e = idx + 1 < cues.length ? ctx.warp.toReal(cues[idx + 1][0]) : ctx.warp.realDur;
  const op = Math.min(clamp01((r - s) / 0.15), clamp01((e - r) / 0.15));
  return (
    <div
      style={{
        position: "absolute",
        left: 140,
        right: 140,
        bottom: 40,
        display: "flex",
        justifyContent: "center",
        opacity: op,
      }}
    >
      <div
        style={{
          fontFamily: FONT,
          fontSize: 35,
          lineHeight: 1.35,
          color: C.text,
          background: "rgba(8,12,24,0.84)",
          padding: "12px 30px",
          borderRadius: 14,
          textAlign: "center",
          maxWidth: 1640,
        }}
      >
        <Rich text={cues[idx][2]} />
      </div>
    </div>
  );
}

/** Mono clips duplicated into a stereo mix measure ~2 dB hot; this brings the mix to about -16 LUFS. */
const VOICE_GAIN = Math.pow(10, -2.1 / 20);

export function Scene({ def, warp }: { def: SceneDef; warp: Warp }) {
  const f = useCurrentFrame();
  const total = Math.ceil(warp.realDur * FPS);
  const fadeK = Math.min(clamp01(f / 12), clamp01((total - 1 - f) / 10));
  const Body = def.C;
  return (
    <SceneCtx.Provider value={{ def, warp }}>
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse 120% 90% at 50% 38%, #111a33 0%, ${C.bg} 62%, #060912 100%)`,
          fontFamily: FONT,
          color: C.text,
        }}
      >
        <AbsoluteFill style={{ opacity: fadeK }}>
          <Body />
        </AbsoluteFill>
        {def.title && (
          <div
            style={{
              position: "absolute",
              left: 60,
              top: 28,
              fontSize: 30,
              color: C.text,
              letterSpacing: 1,
              fontWeight: 700,
              opacity: 0.92,
            }}
          >
            {def.title}
          </div>
        )}
        {def.source && (
          <div style={{ position: "absolute", left: 60, top: 72, fontSize: 18, color: C.muted, fontFamily: MONO }}>
            {def.source}
          </div>
        )}
        {def.stage !== undefined && <PipelineRail active={def.stage} />}
        <Captions />
        {def.cues.map((_, i) => {
          const k = cueKey(def.id, i);
          if (voiceLen[k] === undefined) return null;
          return (
            <Sequence key={k} from={Math.round(warp.toReal(def.cues[i][0]) * FPS)} layout="none">
              <Audio src={staticFile(`voice/${k}.wav`)} volume={VOICE_GAIN} />
            </Sequence>
          );
        })}
      </AbsoluteFill>
    </SceneCtx.Provider>
  );
}

// ---------------------------------------------------------------- story rail (top-right)
/** Chapters: 0 目标与架构 (S01) · 1 两层映射 (S02) · 2 原子提交 (S03) ·
 *  3 反向P2P (S04) · 4 排空泡沫 (S05) · 5 Batch与总结 (S06–S07). */
export const STAGES: { label: string; color: string }[] = [
  { label: "目标与架构", color: C.cyan },
  { label: "两层映射", color: C.purple },
  { label: "原子提交", color: C.orange },
  { label: "反向P2P", color: C.green },
  { label: "排空泡沫", color: C.red },
  { label: "Batch与总结", color: C.yellow },
];
/** Top-right progress rail (x 760–1860, y 30–74) with paper brackets underneath (y ≤ 100). Drawn by Scene. */
export function PipelineRail({ active }: { active: number }) {
  const x0 = 760;
  const w = 1100 / STAGES.length;
  const bracket = (i0: number, i1: number, label: string, color: string) => {
    const a = x0 + i0 * w + 8;
    const b = x0 + (i1 + 1) * w - 12;
    return (
      <g>
        <path d={`M${a} 84 L${a} 90 L${b} 90 L${b} 84`} fill="none" stroke={color} strokeWidth={1.5} opacity={0.6} />
        <text x={(a + b) / 2} y={104} textAnchor="middle" fontSize={14} fill={color} opacity={0.85} fontFamily={MONO}>
          {label}
        </text>
      </g>
    );
  };
  return (
    <svg width={W} height={112} style={{ position: "absolute", left: 0, top: 0 }}>
      {STAGES.map((s, i) => {
        const on = i === active;
        const done = i < active;
        const x = x0 + i * w;
        return (
          <g key={i} fontFamily={FONT}>
            {i > 0 && <line x1={x - 10} y1={52} x2={x + 2} y2={52} stroke={done || on ? s.color : C.line} strokeWidth={2} />}
            <rect
              x={x + 4}
              y={33}
              width={w - 16}
              height={38}
              rx={19}
              fill={on ? s.color : "transparent"}
              stroke={on || done ? s.color : C.line}
              strokeWidth={2}
              opacity={on ? 1 : done ? 0.75 : 0.55}
            />
            <text
              x={x + 4 + (w - 16) / 2}
              y={53}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={18}
              fontWeight={on ? 800 : 500}
              fill={on ? C.bg : done ? s.color : C.dim}
            >
              {s.label}
            </text>
          </g>
        );
      })}
      {bracket(0, 5, "CN106 DPU ⇄ Xeon Socket 1 Intel DSA (dsa2/wq2.0) · 零主机 CPU 转发", C.muted)}
    </svg>
  );
}

// ---------------------------------------------------------------- generic visual pieces
/** Full-frame SVG in 1920x1080 coordinates with shared defs (#glow, #softglow, #hatch, #hatchRed). */
export function Stage({ children, opacity = 1 }: { children: React.ReactNode; opacity?: number }) {
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ position: "absolute", inset: 0, opacity }}>
      <defs>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="6" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="softglow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="14" />
        </filter>
        <pattern id="hatch" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="12" height="12" fill="rgba(139,149,173,0.10)" />
          <line x1="0" y1="0" x2="0" y2="12" stroke="#8b95ad" strokeWidth="3.5" />
        </pattern>
        <pattern id="hatchRed" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="14" height="14" fill="#3a1218" />
          <line x1="0" y1="0" x2="0" y2="14" stroke={C.red} strokeWidth="5" />
        </pattern>
      </defs>
      {children}
    </svg>
  );
}

/** SVG arrow from (x1,y1) to (x2,y2), drawn progressively with k ∈ [0,1]. */
export function Arrow({
  x1,
  y1,
  x2,
  y2,
  color = C.muted,
  w = 4,
  k = 1,
  head = 14,
  dash,
  opacity = 1,
}: {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  color?: string;
  w?: number;
  k?: number;
  head?: number;
  dash?: string;
  opacity?: number;
}) {
  if (k <= 0) return null;
  const ex = lerp(x1, x2, k);
  const ey = lerp(y1, y2, k);
  const a = Math.atan2(y2 - y1, x2 - x1);
  const p1 = [ex - head * Math.cos(a - 0.45), ey - head * Math.sin(a - 0.45)];
  const p2 = [ex - head * Math.cos(a + 0.45), ey - head * Math.sin(a + 0.45)];
  return (
    <g opacity={opacity}>
      <line x1={x1} y1={y1} x2={ex} y2={ey} stroke={color} strokeWidth={w} strokeLinecap="round" strokeDasharray={dash} />
      <polygon points={`${ex},${ey} ${p1.join(",")} ${p2.join(",")}`} fill={color} />
    </g>
  );
}

/** Line drawn progressively by k∈[0,1]. */
export function Edge({
  x1,
  y1,
  x2,
  y2,
  k = 1,
  color = C.line,
  w = 3,
  opacity = 1,
  dash,
}: {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  k?: number;
  color?: string;
  w?: number;
  opacity?: number;
  dash?: string;
}) {
  if (k <= 0) return null;
  return (
    <line
      x1={x1}
      y1={y1}
      x2={x1 + (x2 - x1) * k}
      y2={y1 + (y2 - y1) * k}
      stroke={color}
      strokeWidth={w}
      strokeLinecap="round"
      opacity={opacity}
      strokeDasharray={dash}
    />
  );
}

/** Rounded label chip (HTML, absolutely positioned at its top-left). */
export function Pill({
  x,
  y,
  children,
  color = C.cyan,
  size = 24,
  solid = false,
  style,
}: {
  x: number;
  y: number;
  children: React.ReactNode;
  color?: string;
  size?: number;
  solid?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        fontSize: size,
        fontWeight: 700,
        color: solid ? C.bg : color,
        background: solid ? color : "transparent",
        border: `2px solid ${color}`,
        borderRadius: 999,
        padding: "4px 16px",
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** Absolutely positioned HTML text. */
export function Txt({
  x,
  y,
  w,
  size = 30,
  color = C.text,
  weight = 400,
  align = "left",
  style,
  children,
}: {
  x: number;
  y: number;
  w?: number;
  size?: number;
  color?: string;
  weight?: number;
  align?: "left" | "center" | "right";
  style?: React.CSSProperties;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        fontSize: size,
        color,
        fontWeight: weight,
        textAlign: align,
        lineHeight: 1.3,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** Rounded panel (HTML) with optional accent title strip. */
export function Card({
  x,
  y,
  w,
  h,
  border = C.line,
  title,
  titleColor,
  style,
  children,
}: {
  x: number;
  y: number;
  w: number;
  h?: number;
  border?: string;
  title?: React.ReactNode;
  titleColor?: string;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        background: C.panel,
        border: `2px solid ${border}`,
        borderRadius: 18,
        padding: "18px 24px",
        boxSizing: "border-box",
        ...style,
      }}
    >
      {title && (
        <div style={{ fontSize: 26, fontWeight: 800, color: titleColor ?? border, marginBottom: 10 }}>{title}</div>
      )}
      {children}
    </div>
  );
}

/** Monospace listing; `hl` = highlighted line index. */
export function Code({
  x,
  y,
  lines,
  hl = -1,
  size = 26,
  w,
  style,
}: {
  x: number;
  y: number;
  lines: string[];
  hl?: number;
  size?: number;
  w?: number;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        fontFamily: MONO,
        fontSize: size,
        background: "#0f1528",
        border: `2px solid ${C.line}`,
        borderRadius: 14,
        padding: "14px 20px",
        lineHeight: 1.45,
        whiteSpace: "pre",
        ...style,
      }}
    >
      {lines.map((l, i) => (
        <div
          key={i}
          style={{
            color: i === hl ? C.yellow : l.trim().startsWith("//") ? C.dim : C.text,
            background: i === hl ? "rgba(249,199,79,0.12)" : undefined,
            borderRadius: 6,
            margin: "0 -8px",
            padding: "0 8px",
          }}
        >
          {l || " "}
        </div>
      ))}
    </div>
  );
}

/** HTML card with a big number + label (paper numbers). Position it via `style`. */
export function Stat({
  value,
  label,
  color = C.yellow,
  style,
  size = 84,
  labelSize = 28,
}: {
  value: React.ReactNode;
  label: React.ReactNode;
  color?: string;
  style?: React.CSSProperties;
  size?: number;
  labelSize?: number;
}) {
  return (
    <div
      style={{
        background: C.panel,
        border: `2px solid ${C.line}`,
        borderRadius: 20,
        padding: "22px 30px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 8,
        ...style,
      }}
    >
      <div style={{ fontSize: size, fontWeight: 800, color, fontVariantNumeric: "tabular-nums", lineHeight: 1.05 }}>
        {value}
      </div>
      <div style={{ fontSize: labelSize, color: C.muted, textAlign: "center", lineHeight: 1.3 }}>{label}</div>
    </div>
  );
}

/** Small provenance / "schematic" tag; default sits just above the caption band. */
export function Note({ children, x = 60, y = 862, w }: { children: React.ReactNode; x?: number; y?: number; w?: number }) {
  return (
    <div style={{ position: "absolute", left: x, top: y, width: w, fontSize: 21, color: C.muted, fontStyle: "italic", opacity: 0.85 }}>
      {children}
    </div>
  );
}

/** Paper figure crop on a white card (public/figs/<src>); children overlay in card-local px. Label below in italics. */
export function FigCard({
  src,
  x,
  y,
  w,
  h,
  label,
  opacity = 1,
  scale = 1,
  children,
}: {
  src: string;
  x: number;
  y: number;
  w: number;
  h: number;
  label?: React.ReactNode;
  opacity?: number;
  scale?: number;
  children?: React.ReactNode;
}) {
  if (opacity <= 0) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        opacity,
        transform: `scale(${scale})`,
        transformOrigin: "50% 50%",
      }}
    >
      <div
        style={{
          position: "relative",
          width: w,
          height: h,
          borderRadius: 14,
          overflow: "hidden",
          background: "#fff",
          boxShadow: "0 18px 50px rgba(0,0,0,0.45)",
        }}
      >
        <Img src={staticFile(`figs/${src}`)} style={{ width: w, height: h, display: "block", objectFit: "contain" }} />
        {children}
      </div>
      {label && <div style={{ marginTop: 10, fontSize: 22, color: C.muted, fontStyle: "italic" }}>{label}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- geometry
export type Pt = [number, number];
export const pathD = (pts: Pt[]) => pts.map((p, i) => `${i ? "L" : "M"}${p[0]} ${p[1]}`).join(" ");
export const polyLen = (pts: Pt[]) =>
  pts.slice(1).reduce((s, p, i) => s + Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]), 0);

/** Point at fraction k along a polyline. */
export function along(pts: Pt[], k: number): Pt {
  if (pts.length === 1) return pts[0];
  const seg: number[] = [];
  let total = 0;
  for (let i = 0; i + 1 < pts.length; i++) {
    const d = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
    seg.push(d);
    total += d;
  }
  let want = clamp01(k) * total;
  for (let i = 0; i < seg.length; i++) {
    if (want <= seg[i] || i === seg.length - 1) {
      const u = seg[i] ? Math.min(1, want / seg[i]) : 0;
      return [lerp(pts[i][0], pts[i + 1][0], u), lerp(pts[i][1], pts[i + 1][1], u)];
    }
    want -= seg[i];
  }
  return pts[pts.length - 1];
}

/** First fraction k of a polyline (progressive drawing that still supports dashes). */
export function partial(pts: Pt[], k: number): Pt[] {
  if (k >= 1) return pts;
  if (k <= 0 || pts.length < 2) return [pts[0], pts[0]];
  let want = k * polyLen(pts);
  const out: Pt[] = [pts[0]];
  for (let i = 0; i + 1 < pts.length; i++) {
    const d = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
    if (want <= d) {
      const u = d ? want / d : 0;
      out.push([lerp(pts[i][0], pts[i + 1][0], u), lerp(pts[i][1], pts[i + 1][1], u)]);
      return out;
    }
    out.push(pts[i + 1]);
    want -= d;
  }
  return out;
}

/** Glowing single packet travelling a polyline at fraction k; trail draws the visited part. */
export function Packet({
  pts,
  k,
  color = C.yellow,
  trail = true,
  r = 11,
}: {
  pts: Pt[];
  k: number;
  color?: string;
  trail?: boolean;
  r?: number;
}) {
  if (k <= 0) return null;
  const [x, y] = along(pts, k);
  return (
    <g>
      {trail && (
        <path d={pathD(partial(pts, k))} fill="none" stroke={color} strokeWidth={6} strokeLinecap="round" strokeLinejoin="round" opacity={0.5} />
      )}
      <circle cx={x} cy={y} r={r * 2} fill={color} opacity={0.18} />
      <circle cx={x} cy={y} r={r} fill={color} />
    </g>
  );
}

// ---------------------------------------------------------------- network vocabulary
/**
 * A network link (polyline) drawn progressively by k. `paused` (0..1) blends in the PFC look:
 * red hatched overlay + a pause glyph at `pauseAt` (fraction along the link, default 0.5).
 */
export function Link({
  pts,
  k = 1,
  color = C.line,
  w = 5,
  opacity = 1,
  glow = false,
  dash,
  paused = 0,
  pauseAt = 0.5,
}: {
  pts: Pt[];
  k?: number;
  color?: string;
  w?: number;
  opacity?: number;
  glow?: boolean;
  dash?: string;
  paused?: number;
  pauseAt?: number;
}) {
  if (k <= 0 || opacity <= 0) return null;
  const d = pathD(partial(pts, k));
  const [px, py] = along(pts, pauseAt);
  return (
    <g opacity={opacity}>
      {glow && <path d={d} fill="none" stroke={color} strokeWidth={w * 3} opacity={0.25} filter="url(#softglow)" />}
      <path d={d} fill="none" stroke={color} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={dash} />
      {paused > 0 && (
        <g opacity={paused}>
          <path d={d} fill="none" stroke={K.pause} strokeWidth={w + 2} strokeLinecap="round" strokeDasharray="10 9" />
          <PauseGlyph x={px} y={py} s={0.8} />
        </g>
      )}
    </g>
  );
}

/** Red PFC "pause" badge (octagon with ‖), centred at (x, y). `pulse` 0..1 draws an expanding ring. */
export function PauseGlyph({ x, y, s = 1, pulse = 0, opacity = 1 }: { x: number; y: number; s?: number; pulse?: number; opacity?: number }) {
  if (opacity <= 0 || s <= 0) return null;
  const r = 22;
  const oct = Array.from({ length: 8 }, (_, i) => {
    const a = (Math.PI / 4) * i + Math.PI / 8;
    return `${(Math.cos(a) * r).toFixed(1)},${(Math.sin(a) * r).toFixed(1)}`;
  }).join(" ");
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`} opacity={opacity}>
      {pulse > 0 && pulse < 1 && <circle r={r + 40 * pulse} fill="none" stroke={K.pause} strokeWidth={4 * (1 - pulse)} opacity={1 - pulse} />}
      <polygon points={oct} fill={K.pause} stroke="#fff" strokeWidth={2.5} />
      <rect x={-8} y={-10} width={5} height={20} rx={1.5} fill="#fff" />
      <rect x={3} y={-10} width={5} height={20} rx={1.5} fill="#fff" />
    </g>
  );
}

/**
 * A train of packets moving along a polyline. `travel` = distance (px) the train has advanced; drive it with
 * real time (useRealT()*speed) for constant speed, and freeze it (stop increasing) while a link is paused.
 * `colorAt(i)` overrides colour per packet index (stable across frames: i counts packets since t=0), e.g. to mark ECN.
 * `head` limits how far along the path packets have reached (px), so a stream can "start" at the source.
 */
export function PacketStream({
  pts,
  travel,
  gap = 48,
  size = 14,
  color = K.data,
  colorAt,
  opacity = 1,
  head,
  tail = 0,
}: {
  pts: Pt[];
  travel: number;
  gap?: number;
  size?: number;
  color?: string;
  colorAt?: (i: number) => string | null;
  opacity?: number;
  head?: number;
  tail?: number;
}) {
  if (opacity <= 0) return null;
  const L = polyLen(pts);
  const maxD = Math.min(L, head ?? L);
  const out: React.ReactNode[] = [];
  const first = Math.floor(travel / gap);
  for (let j = 0; j * gap <= L + gap; j++) {
    const idx = first - j; // packet index (packets emitted so far)
    if (idx < 0) break;
    const d = travel - idx * gap;
    if (d < tail || d > maxD) continue;
    const c = colorAt ? colorAt(idx) : color;
    if (c === null) continue;
    const [x, y] = along(pts, d / L);
    out.push(<rect key={idx} x={x - size / 2} y={y - size / 2} width={size} height={size} rx={3} fill={c ?? color} />);
  }
  return <g opacity={opacity}>{out}</g>;
}

/** Switch icon centred at (x, y): dark box with a row of port LEDs; label inside, optional sub-label below. */
export function SwitchBox({
  x,
  y,
  w = 128,
  h = 56,
  label,
  sub,
  color = C.muted,
  glow = false,
  alarm = 0,
  opacity = 1,
  fontSize = 24,
}: {
  x: number;
  y: number;
  w?: number;
  h?: number;
  label?: string;
  sub?: string;
  color?: string;
  glow?: boolean;
  alarm?: number;
  opacity?: number;
  fontSize?: number;
}) {
  if (opacity <= 0) return null;
  const ports = Math.max(4, Math.floor((w - 24) / 16));
  return (
    <g transform={`translate(${x} ${y})`} opacity={opacity} fontFamily={FONT}>
      {glow && <rect x={-w / 2 - 8} y={-h / 2 - 8} width={w + 16} height={h + 16} rx={14} fill={color} opacity={0.35} filter="url(#softglow)" />}
      {alarm > 0 && (
        <rect x={-w / 2 - 6} y={-h / 2 - 6} width={w + 12} height={h + 12} rx={13} fill="none" stroke={K.pause} strokeWidth={4} opacity={alarm} />
      )}
      <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={9} fill={C.panel} stroke={color} strokeWidth={2.5} />
      {Array.from({ length: ports }, (_, i) => (
        <rect key={i} x={-w / 2 + 12 + i * 16} y={h / 2 - 12} width={10} height={5} rx={1.5} fill={color} opacity={0.45} />
      ))}
      {label && (
        <text y={-3} textAnchor="middle" dominantBaseline="central" fontSize={fontSize} fontWeight={700} fill={C.text}>
          {label}
        </text>
      )}
      {sub && (
        <text y={h / 2 + fontSize * 0.9 + 4} textAnchor="middle" fontSize={fontSize * 0.75} fill={C.muted}>
          {sub}
        </text>
      )}
    </g>
  );
}

/** Rack server / host centred at (x, y): 1U-style box; label below. */
export function Server({
  x,
  y,
  w = 96,
  h = 50,
  label,
  color = C.cyan,
  opacity = 1,
  glow = false,
  fontSize = 22,
}: {
  x: number;
  y: number;
  w?: number;
  h?: number;
  label?: string;
  color?: string;
  opacity?: number;
  glow?: boolean;
  fontSize?: number;
}) {
  if (opacity <= 0) return null;
  return (
    <g transform={`translate(${x} ${y})`} opacity={opacity}>
      {glow && <rect x={-w / 2 - 6} y={-h / 2 - 6} width={w + 12} height={h + 12} rx={12} fill={color} opacity={0.4} filter="url(#softglow)" />}
      <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={8} fill={C.panel} stroke={color} strokeWidth={2.5} />
      {[0, 1, 2].map((i) => (
        <rect key={i} x={-w / 2 + 10 + i * (w * 0.17)} y={-h / 2 + 9} width={w * 0.13} height={h - 18} rx={2} fill={color} opacity={0.22} />
      ))}
      <circle cx={w / 2 - 14} cy={-h / 6} r={3.5} fill={C.green} />
      <circle cx={w / 2 - 14} cy={h / 6} r={3.5} fill={color} />
      {label && (
        <text y={h / 2 + fontSize + 4} textAnchor="middle" fontSize={fontSize} fontWeight={600} fill={C.text} fontFamily={FONT}>
          {label}
        </text>
      )}
    </g>
  );
}

/** Small chip (GPU / NIC / CPU) centred at (x, y): square-ish die with pins; label inside. */
export function Chip({
  x,
  y,
  w = 84,
  h = 64,
  label,
  color = K.nic,
  opacity = 1,
  glow = false,
  fontSize = 22,
}: {
  x: number;
  y: number;
  w?: number;
  h?: number;
  label?: string;
  color?: string;
  opacity?: number;
  glow?: boolean;
  fontSize?: number;
}) {
  if (opacity <= 0) return null;
  const pins = 4;
  return (
    <g transform={`translate(${x} ${y})`} opacity={opacity} fontFamily={FONT}>
      {glow && <rect x={-w / 2 - 8} y={-h / 2 - 8} width={w + 16} height={h + 16} rx={12} fill={color} opacity={0.35} filter="url(#softglow)" />}
      {Array.from({ length: pins }, (_, i) => {
        const px = -w / 2 + ((i + 1) * w) / (pins + 1);
        return (
          <g key={i}>
            <rect x={px - 3} y={-h / 2 - 8} width={6} height={8} fill={color} opacity={0.6} />
            <rect x={px - 3} y={h / 2} width={6} height={8} fill={color} opacity={0.6} />
          </g>
        );
      })}
      <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={8} fill="#10182c" stroke={color} strokeWidth={2.5} />
      {label && (
        <text textAnchor="middle" dominantBaseline="central" fontSize={fontSize} fontWeight={700} fill={color}>
          {label}
        </text>
      )}
    </g>
  );
}

/**
 * Buffer / queue gauge (SVG). Bottom-left at (x, y+h) for vertical (fills upward), left edge for horizontal
 * (fills rightward). `level` 0..1 of capacity. `marks` = threshold lines at fractions with labels (drawn on the
 * right of a vertical gauge / above a horizontal one). Fill turns `hot` colour above `hotAt`.
 */
export function QueueGauge({
  x,
  y,
  w,
  h,
  level,
  color = K.data,
  hot = K.pause,
  hotAt = 2,
  marks = [],
  label,
  vertical = true,
  opacity = 1,
  fontSize = 22,
  slots = 0,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  level: number;
  color?: string;
  hot?: string;
  hotAt?: number;
  marks?: { at: number; label: string; color?: string }[];
  label?: string;
  vertical?: boolean;
  opacity?: number;
  fontSize?: number;
  slots?: number;
}) {
  if (opacity <= 0) return null;
  const lv = clamp01(level);
  const fill = level >= hotAt ? hot : color;
  return (
    <g opacity={opacity} fontFamily={FONT}>
      <rect x={x} y={y} width={w} height={h} rx={8} fill="#0d1326" stroke={C.dim} strokeWidth={2} />
      {vertical ? (
        <rect x={x + 4} y={y + 4 + (h - 8) * (1 - lv)} width={w - 8} height={(h - 8) * lv} rx={5} fill={fill} opacity={0.85} />
      ) : (
        <rect x={x + 4} y={y + 4} width={(w - 8) * lv} height={h - 8} rx={5} fill={fill} opacity={0.85} />
      )}
      {slots > 0 &&
        Array.from({ length: slots - 1 }, (_, i) =>
          vertical ? (
            <line key={i} x1={x + 4} x2={x + w - 4} y1={y + 4 + ((h - 8) * (i + 1)) / slots} y2={y + 4 + ((h - 8) * (i + 1)) / slots} stroke={C.bg} strokeWidth={2} />
          ) : (
            <line key={i} y1={y + 4} y2={y + h - 4} x1={x + 4 + ((w - 8) * (i + 1)) / slots} x2={x + 4 + ((w - 8) * (i + 1)) / slots} stroke={C.bg} strokeWidth={2} />
          ),
        )}
      {marks.map((m, i) => {
        const c = m.color ?? C.yellow;
        if (vertical) {
          const yy = y + 4 + (h - 8) * (1 - m.at);
          return (
            <g key={i}>
              <line x1={x - 6} x2={x + w + 6} y1={yy} y2={yy} stroke={c} strokeWidth={2.5} strokeDasharray="8 6" />
              <text x={x + w + 12} y={yy} dominantBaseline="central" fontSize={fontSize} fill={c} fontWeight={700}>
                {m.label}
              </text>
            </g>
          );
        }
        const xx = x + 4 + (w - 8) * m.at;
        return (
          <g key={i}>
            <line x1={xx} x2={xx} y1={y - 6} y2={y + h + 6} stroke={c} strokeWidth={2.5} strokeDasharray="8 6" />
            <text x={xx} y={y - 14} textAnchor="middle" fontSize={fontSize} fill={c} fontWeight={700}>
              {m.label}
            </text>
          </g>
        );
      })}
      {label &&
        (vertical ? (
          <text x={x + w / 2} y={y + h + fontSize + 8} textAnchor="middle" fontSize={fontSize} fill={C.muted}>
            {label}
          </text>
        ) : (
          <text x={x} y={y + h + fontSize + 8} fontSize={fontSize} fill={C.muted}>
            {label}
          </text>
        ))}
    </g>
  );
}

// ---------------------------------------------------------------- charts
export type Scale = (v: number) => number;
export const linScale =
  (d0: number, d1: number, r0: number, r1: number): Scale =>
  (v) =>
    r0 + ((v - d0) / (d1 - d0)) * (r1 - r0);
export const logScale =
  (d0: number, d1: number, r0: number, r1: number): Scale =>
  (v) =>
    r0 + ((Math.log10(v) - Math.log10(d0)) / (Math.log10(d1) - Math.log10(d0))) * (r1 - r0);

/** Axes box with ticks (SVG). x0..x1 / y0 (bottom)..y1 (top) are pixels. */
export function Axes({
  x0,
  x1,
  y0,
  y1,
  sx,
  sy,
  xticks,
  yticks,
  xlabel,
  ylabel,
  opacity = 1,
  size = 22,
}: {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
  sx: Scale;
  sy: Scale;
  xticks: [number, string][];
  yticks: [number, string][];
  xlabel?: string;
  ylabel?: string;
  opacity?: number;
  size?: number;
}) {
  return (
    <g opacity={opacity} fontFamily={FONT}>
      {yticks.map(([v, l]) => (
        <g key={`y${v}`}>
          <line x1={x0} y1={sy(v)} x2={x1} y2={sy(v)} stroke={C.line} strokeWidth={1.5} opacity={0.6} />
          <text x={x0 - 12} y={sy(v)} textAnchor="end" dominantBaseline="central" fontSize={size} fill={C.muted}>
            {l}
          </text>
        </g>
      ))}
      {xticks.map(([v, l]) => (
        <g key={`x${v}`}>
          <line x1={sx(v)} y1={y0} x2={sx(v)} y2={y0 + 8} stroke={C.dim} strokeWidth={2} />
          <text x={sx(v)} y={y0 + 34} textAnchor="middle" fontSize={size} fill={C.muted}>
            {l}
          </text>
        </g>
      ))}
      <line x1={x0} y1={y0} x2={x1} y2={y0} stroke={C.dim} strokeWidth={2.5} />
      <line x1={x0} y1={y0} x2={x0} y2={y1} stroke={C.dim} strokeWidth={2.5} />
      {xlabel && (
        <text x={(x0 + x1) / 2} y={y0 + 72} textAnchor="middle" fontSize={size + 2} fill={C.muted}>
          {xlabel}
        </text>
      )}
      {ylabel && (
        <text x={x0} y={y1 - 22} textAnchor="start" fontSize={size + 2} fill={C.muted}>
          {ylabel}
        </text>
      )}
    </g>
  );
}

/** Polyline revealed point by point: k in [0,1] over the whole series. */
export function Series({
  pts,
  sx,
  sy,
  k,
  color,
  w = 5,
  dots = true,
  dash,
  opacity = 1,
  r = 6,
}: {
  pts: [number, number][];
  sx: Scale;
  sy: Scale;
  k: number;
  color: string;
  w?: number;
  dots?: boolean;
  dash?: string;
  opacity?: number;
  r?: number;
}) {
  if (k <= 0) return null;
  const P = pts.map(([a, b]) => [sx(a), sy(b)] as [number, number]);
  const f = k * (P.length - 1);
  const n = Math.floor(f);
  const vis = P.slice(0, n + 1);
  if (n + 1 < P.length) vis.push([lerp(P[n][0], P[n + 1][0], f - n), lerp(P[n][1], P[n + 1][1], f - n)]);
  return (
    <g opacity={opacity}>
      <polyline
        points={vis.map((p) => p.join(",")).join(" ")}
        fill="none"
        stroke={color}
        strokeWidth={w}
        strokeLinejoin="round"
        strokeLinecap="round"
        strokeDasharray={dash}
      />
      {dots && P.slice(0, n + 1).map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r={r} fill={color} />)}
    </g>
  );
}

/** Horizontal threshold line with a right-side label. */
export function HLine({
  y,
  x0,
  x1,
  color = C.dim,
  label,
  opacity = 1,
  size = 22,
}: {
  y: number;
  x0: number;
  x1: number;
  color?: string;
  label?: string;
  opacity?: number;
  size?: number;
}) {
  return (
    <g opacity={opacity}>
      <line x1={x0} y1={y} x2={x1} y2={y} stroke={color} strokeWidth={2.5} strokeDasharray="10 8" />
      {label && (
        <text x={x1 + 10} y={y} dominantBaseline="central" fontSize={size} fill={color} fontFamily={FONT} fontWeight={700}>
          {label}
        </text>
      )}
    </g>
  );
}

/**
 * Vertical bar chart (SVG) inside [x0, x1] × [y1 (top), y0 (bottom)]. Each bar grows with k[i] (0..1).
 * `log` uses a log10 scale from `min` to `max`. Value labels above bars, category labels below.
 */
export function Bars({
  x0,
  x1,
  y0,
  y1,
  data,
  max,
  min = 1,
  log = false,
  k,
  size = 24,
  barW,
}: {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
  data: { label: string; value: number; color: string; text?: string }[];
  max: number;
  min?: number;
  log?: boolean;
  k: number[] | number;
  size?: number;
  barW?: number;
}) {
  const n = data.length;
  const slot = (x1 - x0) / n;
  const bw = barW ?? slot * 0.56;
  const sy = log ? logScale(min, max, y0, y1) : linScale(0, max, y0, y1);
  return (
    <g fontFamily={FONT}>
      <line x1={x0} y1={y0} x2={x1} y2={y0} stroke={C.dim} strokeWidth={2.5} />
      {data.map((d, i) => {
        const kk = Array.isArray(k) ? k[i] ?? 0 : k;
        const cx = x0 + slot * (i + 0.5);
        const top = lerp(y0, sy(Math.max(d.value, log ? min : 0)), kk);
        return (
          <g key={i}>
            {kk > 0 && <rect x={cx - bw / 2} y={top} width={bw} height={y0 - top} rx={6} fill={d.color} opacity={0.9} />}
            {kk > 0.6 && (
              <text x={cx} y={top - 12} textAnchor="middle" fontSize={size} fontWeight={800} fill={d.color} opacity={(kk - 0.6) / 0.4}>
                {d.text ?? fmt(d.value)}
              </text>
            )}
            <text x={cx} y={y0 + size + 10} textAnchor="middle" fontSize={size} fill={C.muted}>
              {d.label}
            </text>
          </g>
        );
      })}
    </g>
  );
}

// ---------------------------------------------------------------- deterministic randomness
export function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------- sequence-number row (reliability scenes)
/** Cell state for SeqRow: what the sender/receiver knows about one packet sequence number. */
export type SeqState = "empty" | "sent" | "got" | "lost" | "late" | "retx" | "dup";
const SEQ_STYLE: Record<SeqState, { fill: string; stroke: string; text: string }> = {
  empty: { fill: "transparent", stroke: C.line, text: C.dim },
  sent: { fill: "rgba(76,201,240,0.16)", stroke: C.cyan, text: C.cyan },
  got: { fill: "rgba(67,197,158,0.85)", stroke: C.green, text: C.bg },
  lost: { fill: "url(#hatchRed)", stroke: C.red, text: C.red },
  late: { fill: "rgba(249,199,79,0.22)", stroke: C.yellow, text: C.yellow },
  retx: { fill: "rgba(255,112,166,0.85)", stroke: C.pink, text: C.bg },
  dup: { fill: "rgba(139,149,173,0.25)", stroke: C.muted, text: C.muted },
};
/**
 * Row of numbered packet cells (PSNs / bitmap bits), SVG, inside <Stage>. Cell i is at x + i*(cell+gap).
 * `state(i)` picks the style (empty/sent/got/lost/late/retx/dup); `show(i)` ∈ [0,1] fades/pops each cell in;
 * `label(i)` overrides the number (default start+i); `bits` renders 1/0 instead of numbers (for the ACK bitmap).
 */
export function SeqRow({
  x,
  y,
  n,
  start = 0,
  cell = 56,
  gap = 8,
  state,
  show = () => 1,
  label,
  bits = false,
  fontSize = 22,
}: {
  x: number;
  y: number;
  n: number;
  start?: number;
  cell?: number;
  gap?: number;
  state: (i: number) => SeqState;
  show?: (i: number) => number;
  label?: (i: number) => string;
  bits?: boolean;
  fontSize?: number;
}) {
  return (
    <g fontFamily={MONO}>
      {Array.from({ length: n }, (_, i) => {
        const st = state(i);
        const s = SEQ_STYLE[st];
        const k = clamp01(show(i));
        if (k <= 0) return null;
        const cx = x + i * (cell + gap) + cell / 2;
        const cy = y + cell / 2;
        const txt = label ? label(i) : bits ? (st === "got" ? "1" : "0") : String(start + i);
        return (
          <g key={i} opacity={k} transform={`translate(${cx} ${cy}) scale(${0.7 + 0.3 * k}) translate(${-cx} ${-cy})`}>
            <rect x={cx - cell / 2} y={y} width={cell} height={cell} rx={8} fill={s.fill} stroke={s.stroke} strokeWidth={2.5} />
            <text x={cx} y={cy + 1} textAnchor="middle" dominantBaseline="central" fontSize={fontSize} fontWeight={700} fill={s.text}>
              {txt}
            </text>
          </g>
        );
      })}
    </g>
  );
}
