/**
 * Bar4PollingWalls design kit (MRV-258): dark editorial aesthetic with Noto Sans SC + JetBrains Mono,
 * fixed semantic palette, Chinese caption shell, syntax-highlighted Rust/C CodeCard, and BAR4 ring widgets.
 */
import React, { useContext } from "react";
import { AbsoluteFill, Sequence, staticFile, useCurrentFrame } from "remotion";
import { Audio } from "@remotion/media";
import {
  FPS,
  H,
  MONO,
  Pt,
  SceneCtx,
  SceneDef,
  W,
  Warp,
  along,
  clamp01,
  cueKey,
  lerp,
  pathD,
  prog,
} from "../lib";

export const ZH_FONT = "'Noto Sans SC', 'Inter', sans-serif";
export const TAB: React.CSSProperties = { fontVariantNumeric: "tabular-nums" };

// ---------------------------------------------------------------- palette
export const P = {
  bg0: "#05080f",
  bg1: "#0d1629",
  ink: "#f1f5fb",
  sub: "#9ba8c2",
  faint: "#64728d",
  hair: "#1b2640",
  hair2: "#2e3d5c",
  panel: "rgba(15, 24, 44, 0.72)",
  panelLine: "rgba(148, 175, 224, 0.16)",
  host: "#6ea8fe", // Host Xeon / MOVDIR64B
  tad: "#ffb470", // CN106 48 MiB TAD / PEM0
  nc: "#4de8c8", // MT_NORMAL_NC / u1_ldpq / clean path
  wb: "#b9a3ff", // Normal-WB / private L1D-L2 / doorbell
  bad: "#ff6464", // Deadlock / 0x58 snoop / EXLMN avalanche / CHA blowup
  gold: "#ffd27a", // Key headline metrics / highlights
  link: "#43567d",
  core: ["#4de8c8", "#6ea8fe", "#ffd27a", "#f48fb1"] as const,
};

export function alpha(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// ---------------------------------------------------------------- rail
export const BAR4_STAGES: { label: string; color: string }[] = [
  { label: "轮询代码", color: P.host },
  { label: "第一道墙", color: P.bad },
  { label: "NC 破局", color: P.nc },
  { label: "第二道墙", color: P.tad },
  { label: "零前瞻解", color: P.nc },
  { label: "选型口诀", color: P.gold },
];

export function StoryRail({ active, stages = BAR4_STAGES }: { active: number; stages?: { label: string; color: string }[] }) {
  const x0 = 1170;
  const x1 = 1830;
  const n = stages.length;
  const step = (x1 - x0) / (n - 1);
  const y = 48;
  return (
    <svg width={W} height={116} style={{ position: "absolute", left: 0, top: 0 }}>
      <line x1={x0} y1={y} x2={x1} y2={y} stroke={P.hair2} strokeWidth={1.8} />
      <line
        x1={x0}
        y1={y}
        x2={x0 + step * active}
        y2={y}
        stroke={alpha(stages[active].color, 0.78)}
        strokeWidth={2.4}
      />
      {stages.map((s, i) => {
        const x = x0 + i * step;
        const on = i === active;
        const done = i < active;
        return (
          <g key={s.label} fontFamily={ZH_FONT}>
            {on && <circle cx={x} cy={y} r={13} fill="none" stroke={alpha(s.color, 0.45)} strokeWidth={1.8} />}
            <circle
              cx={x}
              cy={y}
              r={on ? 7 : 5}
              fill={on ? s.color : done ? alpha(stages[active].color, 0.78) : P.bg0}
              stroke={on ? s.color : done ? alpha(stages[active].color, 0.78) : P.faint}
              strokeWidth={1.6}
            />
            <text
              x={x}
              y={y + 34}
              textAnchor="middle"
              fontSize={16}
              fontWeight={on ? 700 : 400}
              fill={on ? P.ink : done ? P.sub : P.faint}
            >
              {s.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ---------------------------------------------------------------- captions
const UNBREAKABLE = /([A-Za-z0-9_.*µ%×~<>≤≥=]+(?:[-–/][A-Za-z0-9_.*µ%×]+)+)/g;

export function RichText({ text, accent = P.gold }: { text: string; accent?: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  const keepWhole = (s: string) =>
    s.split(UNBREAKABLE).map((q, j) =>
      j % 2 ? (
        <span key={j} style={{ whiteSpace: "nowrap" }}>
          {q}
        </span>
      ) : (
        q
      ),
    );
  const renderInner = (s: string, bold: boolean) =>
    s
      .split(/(`[^`]+`)/g)
      .filter(Boolean)
      .map((chunk, idx) =>
        chunk.startsWith("`") ? (
          <span
            key={idx}
            style={{
              fontFamily: MONO,
              color: bold ? accent : P.nc,
              fontWeight: bold ? 700 : 500,
              fontSize: "0.9em",
              background: bold ? alpha(accent, 0.14) : "rgba(77,232,200,0.09)",
              padding: "1px 7px",
              borderRadius: 6,
              whiteSpace: "nowrap",
            }}
          >
            {chunk.slice(1, -1)}
          </span>
        ) : (
          <span key={idx}>{keepWhole(chunk)}</span>
        ),
      );
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("**") ? (
          <span key={i} style={{ color: accent, fontWeight: 700 }}>
            {renderInner(p.slice(2, -2), true)}
          </span>
        ) : p.startsWith("`") ? (
          <span
            key={i}
            style={{
              fontFamily: MONO,
              color: P.nc,
              fontSize: "0.88em",
              background: "rgba(77,232,200,0.09)",
              padding: "1px 7px",
              borderRadius: 6,
              whiteSpace: "nowrap",
            }}
          >
            {p.slice(1, -1)}
          </span>
        ) : (
          <span key={i}>{keepWhole(p)}</span>
        ),
      )}
    </>
  );
}

function Bar4Captions() {
  const ctx = useContext(SceneCtx)!;
  const r = useCurrentFrame() / FPS;
  const { cues } = ctx.def;
  let idx = -1;
  for (let i = 0; i < cues.length; i++) if (ctx.warp.toReal(cues[i][0]) <= r) idx = i;
  if (idx < 0) return null;
  const s = ctx.warp.toReal(cues[idx][0]);
  const e = idx + 1 < cues.length ? ctx.warp.toReal(cues[idx + 1][0]) : ctx.warp.realDur;
  const op = Math.min(clamp01((r - s) / 0.16), clamp01((e - r) / 0.16));
  const text = cues[idx][2]
    .replace(/(\d) (?=(ns|µs|ms|M\/s|Mpps|GB\/s|MiB|KiB|B\b|%|×))/g, "$1\u00a0")
    .replace(/(\S) →(?= (\*\*)?[~−+]?\d)/g, "$1\u00a0→")
    .replace(/→ (?=(\*\*)?[~−+]?\d)/g, "→\u00a0");
  return (
    <div
      style={{
        position: "absolute",
        left: 96,
        right: 96,
        bottom: 30,
        display: "flex",
        justifyContent: "center",
        opacity: op,
      }}
    >
      <div
        style={{
          fontFamily: ZH_FONT,
          fontSize: 30,
          lineHeight: 1.44,
          color: P.ink,
          background: "rgba(5, 8, 15, 0.88)",
          border: `1px solid ${P.panelLine}`,
          padding: "12px 32px 13px",
          borderRadius: 14,
          textAlign: "center",
          maxWidth: 1680,
          textWrap: "balance",
          boxShadow: "0 10px 32px rgba(0,0,0,0.45)",
        }}
      >
        <RichText text={text} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- scene shell
/** Calibrated gain to land integrated loudness cleanly at -15.6 LUFS (target -15 to -16 LUFS). */
const VOICE_GAIN = Math.pow(10, -1.0 / 20);

function Backdrop() {
  return (
    <AbsoluteFill>
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse 110% 85% at 50% 38%, ${P.bg1} 0%, #070d1a 56%, ${P.bg0} 100%)`,
        }}
      />
      <svg width={W} height={H} style={{ position: "absolute", inset: 0, opacity: 0.55 }}>
        <defs>
          <pattern id="bar4Dots" width="48" height="48" patternUnits="userSpaceOnUse">
            <circle cx="24" cy="24" r="1.15" fill="rgba(160,185,230,0.10)" />
          </pattern>
        </defs>
        <rect width={W} height={H} fill="url(#bar4Dots)" />
      </svg>
    </AbsoluteFill>
  );
}

export function Bar4Scene({
  def,
  warp,
  voiceMap,
  stages = BAR4_STAGES,
}: {
  def: SceneDef;
  warp: Warp;
  voiceMap: Record<string, number>;
  stages?: { label: string; color: string }[];
}) {
  const f = useCurrentFrame();
  const total = Math.ceil(warp.realDur * FPS);
  const fadeK = Math.min(clamp01(f / 10), clamp01((total - 1 - f) / 10));
  const Body = def.C;
  const accent = def.stage === undefined ? P.sub : stages[def.stage].color;
  const chNum = def.id.replace(/^B0?/, "");
  const kickerText = def.stage === undefined ? `第 ${chNum} 章` : `第 ${chNum} 章  ·  ${stages[def.stage].label}`;
  return (
    <SceneCtx.Provider value={{ def, warp }}>
      <AbsoluteFill style={{ fontFamily: ZH_FONT, color: P.ink, background: P.bg0 }}>
        <Backdrop />
        <AbsoluteFill style={{ opacity: fadeK }}>
          <Body />
        </AbsoluteFill>
        {def.title && (
          <div style={{ position: "absolute", left: 76, top: 24, maxWidth: 1060, opacity: fadeK }}>
            <div style={{ fontSize: 16, letterSpacing: 2.2, color: accent, fontWeight: 700 }}>{kickerText}</div>
            <div
              style={{
                fontSize: 33,
                fontWeight: 700,
                color: P.ink,
                marginTop: 4,
                letterSpacing: -0.2,
                whiteSpace: "nowrap",
              }}
            >
              {def.title}
            </div>
            {def.source && (
              <div style={{ fontSize: 15, color: P.faint, fontFamily: MONO, marginTop: 6 }}>{def.source}</div>
            )}
          </div>
        )}
        {def.stage !== undefined && (
          <div style={{ opacity: fadeK }}>
            <StoryRail active={def.stage} stages={stages} />
          </div>
        )}
        <Bar4Captions />
        {def.cues.map((_, i) => {
          const k = cueKey(def.id, i);
          if (voiceMap[k] === undefined) return null;
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

// ---------------------------------------------------------------- motion helpers
export const win = (t: number, a: number, b = 1e9, d = 0.45) => prog(t, a, d) * (1 - prog(t, b, d));

export function count(t: number, a: number, v: number, dec = 0, d = 0.85, from = 0): string {
  const k = prog(t, a, d);
  const val = from + (v - from) * k;
  return val.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

export function rise(k: number, dy = 16): React.CSSProperties {
  return { opacity: k, transform: `translateY(${(1 - k) * dy}px)` };
}

// ---------------------------------------------------------------- HTML components (outside <Stage>)
export function Tag({
  x,
  y,
  children,
  color = P.sub,
  opacity = 1,
  solid = false,
  size = 16,
}: {
  x: number;
  y: number;
  children: React.ReactNode;
  color?: string;
  opacity?: number;
  solid?: boolean;
  size?: number;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        opacity,
        fontFamily: ZH_FONT,
        fontSize: size,
        fontWeight: 700,
        letterSpacing: 0.5,
        color: solid ? P.bg0 : color,
        background: solid ? color : alpha(color, 0.14),
        border: `1.5px solid ${alpha(color, solid ? 1 : 0.45)}`,
        padding: "5px 14px",
        borderRadius: 999,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </div>
  );
}

export function Text({
  x,
  y,
  w,
  size = 24,
  color = P.ink,
  weight = 400,
  align = "left",
  opacity = 1,
  mono = false,
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
  opacity?: number;
  mono?: boolean;
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
        fontFamily: mono ? MONO : ZH_FONT,
        fontSize: size,
        fontWeight: weight,
        color,
        textAlign: align,
        opacity,
        lineHeight: 1.45,
        ...TAB,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function Glass({
  x,
  y,
  w,
  h,
  kicker,
  color = P.sub,
  borderColor,
  opacity = 1,
  style,
  children,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  kicker?: string;
  color?: string;
  borderColor?: string;
  opacity?: number;
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
        opacity,
        background: P.panel,
        border: `1.5px solid ${borderColor ?? alpha(color, 0.3)}`,
        borderRadius: 16,
        padding: "18px 22px",
        boxSizing: "border-box",
        boxShadow: "0 12px 30px rgba(0,0,0,0.30)",
        ...style,
      }}
    >
      {kicker && (
        <div
          style={{
            fontFamily: ZH_FONT,
            fontSize: 16,
            fontWeight: 700,
            letterSpacing: 1.2,
            color,
            marginBottom: 10,
          }}
        >
          {kicker}
        </div>
      )}
      {children}
    </div>
  );
}

export function BigStat({
  x,
  y,
  value,
  unit,
  label,
  note,
  color = P.gold,
  size = 64,
  w = 340,
  align = "left",
  opacity = 1,
}: {
  x: number;
  y: number;
  value: string;
  unit?: string;
  label: string;
  note?: string;
  color?: string;
  size?: number;
  w?: number;
  align?: "left" | "center" | "right";
  opacity?: number;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: align === "center" ? x - w / 2 : x,
        top: y,
        width: w,
        textAlign: align,
        opacity,
        ...TAB,
      }}
    >
      <div style={{ fontSize: size, fontWeight: 700, color, lineHeight: 1.05, letterSpacing: -1 }}>
        {value}
        {unit && <span style={{ fontSize: size * 0.36, fontWeight: 600, marginLeft: 6, color: P.sub }}>{unit}</span>}
      </div>
      <div style={{ fontSize: 21, fontWeight: 600, color: P.ink, marginTop: 8 }}>{label}</div>
      {note && <div style={{ fontSize: 16, color: P.sub, marginTop: 4 }}>{note}</div>}
    </div>
  );
}

/** Real Rust / C syntax-highlighted code panel with per-line highlight & annotation badge. */
export type CodeLine = { text: string; hl?: "host" | "nc" | "bad" | "gold" | "wb"; note?: string };

const HL_COLOR = {
  host: P.host,
  nc: P.nc,
  bad: P.bad,
  gold: P.gold,
  wb: P.wb,
};

function colorRustToken(line: string): React.ReactNode {
  const commentIdx = line.indexOf("//");
  const code = commentIdx >= 0 ? line.slice(0, commentIdx) : line;
  const comment = commentIdx >= 0 ? line.slice(commentIdx) : "";
  const tokens = code.split(
    /(\b(?:let|mut|for|in|if|else|unsafe|fn|while|match|return|as|usize|u64|u8|bool|true|false)\b|\b(?:movdir64b|load_64b_ldpq|load_4x64b_ldpq|consume_one|load_u64_hdr|pause_ns|TAD_PMU_EVENT_ATTR)\b|\b0x[0-9a-fA-F]+\b|\b\d+\b)/g,
  );
  return (
    <>
      {tokens.map((tok, i) => {
        if (/^(let|mut|for|in|if|else|unsafe|fn|while|match|return|as|usize|u64|u8|bool|true|false)$/.test(tok)) {
          return (
            <span key={i} style={{ color: "#c792ea", fontWeight: 600 }}>
              {tok}
            </span>
          );
        }
        if (/^(movdir64b|load_64b_ldpq|load_4x64b_ldpq|consume_one|load_u64_hdr|pause_ns|TAD_PMU_EVENT_ATTR)$/.test(tok)) {
          return (
            <span key={i} style={{ color: P.nc, fontWeight: 600 }}>
              {tok}
            </span>
          );
        }
        if (/^(0x[0-9a-fA-F]+|\d+)$/.test(tok)) {
          return (
            <span key={i} style={{ color: P.gold }}>
              {tok}
            </span>
          );
        }
        return <span key={i}>{tok}</span>;
      })}
      {comment && <span style={{ color: "#6d809e", fontStyle: "italic" }}>{comment}</span>}
    </>
  );
}

export function CodeCard({
  x,
  y,
  w,
  h,
  title,
  badge,
  accent = P.host,
  lines,
  fontSize = 18.5,
  opacity = 1,
}: {
  x: number;
  y: number;
  w: number;
  h?: number;
  title: string;
  badge?: string;
  accent?: string;
  lines: CodeLine[];
  fontSize?: number;
  opacity?: number;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        opacity,
        background: "rgba(9, 15, 29, 0.92)",
        border: `1.5px solid ${alpha(accent, 0.42)}`,
        borderRadius: 16,
        overflow: "hidden",
        boxShadow: "0 14px 36px rgba(0,0,0,0.42)",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "11px 18px",
          background: alpha(accent, 0.12),
          borderBottom: `1px solid ${alpha(accent, 0.25)}`,
        }}
      >
        <span style={{ fontFamily: MONO, fontSize: 16, fontWeight: 600, color: P.ink }}>{title}</span>
        {badge && (
          <span
            style={{
              fontFamily: ZH_FONT,
              fontSize: 14,
              fontWeight: 700,
              color: accent,
              background: alpha(accent, 0.16),
              padding: "2px 10px",
              borderRadius: 999,
            }}
          >
            {badge}
          </span>
        )}
      </div>
      <div style={{ padding: "12px 14px", fontFamily: MONO, fontSize, lineHeight: 1.56, color: "#dce6f7" }}>
        {lines.map((ln, idx) => {
          const hlCol = ln.hl ? HL_COLOR[ln.hl] : undefined;
          return (
            <div
              key={idx}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "2px 10px",
                borderRadius: 6,
                background: hlCol ? alpha(hlCol, 0.16) : "transparent",
                borderLeft: hlCol ? `3px solid ${hlCol}` : "3px solid transparent",
                whiteSpace: "pre",
              }}
            >
              <span>{colorRustToken(ln.text)}</span>
              {ln.note && (
                <span
                  style={{
                    fontFamily: ZH_FONT,
                    fontSize: 14,
                    fontWeight: 700,
                    color: hlCol ?? P.gold,
                    marginLeft: 12,
                  }}
                >
                  {ln.note}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- SVG primitives (inside <Stage>)
export function Stage({ children, opacity = 1 }: { children: React.ReactNode; opacity?: number }) {
  return (
    <svg width={W} height={H} style={{ position: "absolute", left: 0, top: 0, opacity, pointerEvents: "none" }}>
      <defs>
        <filter id="b4Glow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="5" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      {children}
    </svg>
  );
}

/** Arrowed polyline drawn progressively by k (halo via wide faint stroke so straight H/V lines never disappear). */
export function Flow({
  pts,
  k = 1,
  color = P.sub,
  w = 3.5,
  head = 13,
  dash,
  opacity = 1,
  glow = false,
}: {
  pts: Pt[];
  k?: number;
  color?: string;
  w?: number;
  head?: number;
  dash?: string;
  opacity?: number;
  glow?: boolean;
}) {
  if (k <= 0.005 || opacity <= 0.005) return null;
  const kk = clamp01(k);
  const sub: Pt[] = [];
  let total = 0;
  for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  const target = total * kk;
  let acc = 0;
  sub.push(pts[0]);
  for (let i = 1; i < pts.length; i++) {
    const seg = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    if (acc + seg >= target) {
      const f = seg > 0 ? (target - acc) / seg : 0;
      sub.push([lerp(pts[i - 1][0], pts[i][0], f), lerp(pts[i - 1][1], pts[i][1], f)]);
      break;
    }
    sub.push(pts[i]);
    acc += seg;
  }
  const end = sub[sub.length - 1];
  const prev = sub[sub.length - 2] ?? pts[0];
  const ang = Math.atan2(end[1] - prev[1], end[0] - prev[0]);
  const h1: Pt = [end[0] - head * Math.cos(ang - 0.44), end[1] - head * Math.sin(ang - 0.44)];
  const h2: Pt = [end[0] - head * Math.cos(ang + 0.44), end[1] - head * Math.sin(ang + 0.44)];
  const d = pathD(sub);
  return (
    <g opacity={opacity}>
      {glow && <path d={d} fill="none" stroke={alpha(color, 0.28)} strokeWidth={w + 8} strokeLinecap="round" />}
      <path d={d} fill="none" stroke={color} strokeWidth={w} strokeDasharray={dash} strokeLinecap="round" strokeLinejoin="round" />
      <polygon points={`${end[0]},${end[1]} ${h1[0]},${h1[1]} ${h2[0]},${h2[1]}`} fill={color} />
    </g>
  );
}

/** 64 B cell packet travelling a polyline at fraction k. */
export function PacketCell({
  pts,
  k,
  color = P.host,
  w = 38,
  h = 24,
  label,
  opacity = 1,
}: {
  pts: Pt[];
  k: number;
  color?: string;
  w?: number;
  h?: number;
  label?: string;
  opacity?: number;
}) {
  if (k < 0 || k > 1 || opacity <= 0.01) return null;
  const [x, y] = along(pts, k);
  return (
    <g opacity={opacity}>
      <rect x={x - w / 2 - 4} y={y - h / 2 - 4} width={w + 8} height={h + 8} rx={9} fill={alpha(color, 0.25)} />
      <rect x={x - w / 2} y={y - h / 2} width={w} height={h} rx={6} fill={P.bg0} stroke={color} strokeWidth={2.2} />
      {label && (
        <text
          x={x}
          y={y + 5}
          textAnchor="middle"
          fontFamily={MONO}
          fontSize={13}
          fontWeight={700}
          fill={color}
        >
          {label}
        </text>
      )}
    </g>
  );
}

/** SVG Horizontal Bar chart with crisp Chinese labels & right values. */
export type BarRow = {
  label: string;
  value: number;
  text: string;
  color: string;
  k?: number;
  sub?: string;
};

export function HBars({
  x,
  y,
  w,
  rows,
  max,
  labelW = 310,
  barH = 30,
  gap = 26,
  size = 20,
  opacity = 1,
}: {
  x: number;
  y: number;
  w: number;
  rows: BarRow[];
  max: number;
  labelW?: number;
  barH?: number;
  gap?: number;
  size?: number;
  opacity?: number;
}) {
  const trackW = Math.max(80, w - labelW - 150);
  return (
    <g opacity={opacity} fontFamily={ZH_FONT}>
      {rows.map((r, i) => {
        const ry = y + i * (barH + gap);
        const kk = clamp01(r.k ?? 1);
        const bw = kk > 0.01 ? Math.max(4, (Math.min(r.value, max) / max) * trackW * kk) : 0;
        return (
          <g key={r.label} opacity={kk > 0.01 ? 1 : 0.28}>
            <text x={x + labelW - 18} y={ry + barH / 2 + 6} textAnchor="end" fontSize={size} fontWeight={600} fill={P.ink}>
              {r.label}
            </text>
            <rect x={x + labelW} y={ry} width={trackW} height={barH} rx={7} fill={alpha(P.hair2, 0.45)} />
            {bw > 0 && <rect x={x + labelW} y={ry} width={bw} height={barH} rx={7} fill={r.color} />}
            <text
              x={x + labelW + bw + 14}
              y={ry + barH / 2 + 7}
              fontFamily={MONO}
              fontSize={size}
              fontWeight={700}
              fill={r.color}
              opacity={kk > 0.15 ? 1 : 0}
            >
              {r.text}
            </text>
            {r.sub && (
              <text x={x + labelW - 18} y={ry + barH + 15} textAnchor="end" fontSize={14} fill={P.sub}>
                {r.sub}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
}

/** SVG Box with title + subtitle (used for Host / PEM0 / TAD / L1D-L2 / Card Core blocks). */
export function ArchBox({
  x,
  y,
  w,
  h,
  title,
  sub,
  color = P.hair2,
  fill,
  titleSize = 22,
  subSize = 15,
  opacity = 1,
  dashed = false,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  sub?: string;
  color?: string;
  fill?: string;
  titleSize?: number;
  subSize?: number;
  opacity?: number;
  dashed?: boolean;
}) {
  return (
    <g opacity={opacity} fontFamily={ZH_FONT}>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={14}
        fill={fill ?? alpha(color, 0.1)}
        stroke={color}
        strokeWidth={2.2}
        strokeDasharray={dashed ? "8 6" : undefined}
      />
      <text
        x={x + w / 2}
        y={sub ? y + h / 2 - 4 : y + h / 2 + titleSize * 0.34}
        textAnchor="middle"
        fontSize={titleSize}
        fontWeight={700}
        fill={P.ink}
      >
        {title}
      </text>
      {sub && (
        <text x={x + w / 2} y={y + h / 2 + 20} textAnchor="middle" fontFamily={MONO} fontSize={subSize} fill={P.sub}>
          {sub}
        </text>
      )}
    </g>
  );
}
