/**
 * CcnicPaperStory design kit: palette, scene shell (title, rail, captions, narration), and the shared
 * visual vocabulary every chapter uses. Restrained look: near-black navy, hairlines, one accent per idea.
 *
 * Layout bands (1920×1080): title block x 96..1000, y 34..118 · rail x 1120..1824, y 34..96 ·
 * content y 150..880 · captions bottom band (y >= 900). HTML helpers go OUTSIDE <Stage>; SVG helpers inside.
 */
import React, { useContext } from "react";
import { AbsoluteFill, Sequence, staticFile, useCurrentFrame } from "remotion";
import { Audio } from "@remotion/media";
import {
  FONT,
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

// ---------------------------------------------------------------- palette
/** Colour meanings are fixed for the whole video. */
export const P = {
  bg0: "#04070d",
  bg1: "#0c1528",
  ink: "#eef2f8", // primary text
  sub: "#9aa5bc", // secondary text
  faint: "#5d6880", // tertiary text, inactive marks
  hair: "#1d2740", // hairlines, grid
  hair2: "#2c3955", // stronger hairline / box strokes
  panel: "rgba(255,255,255,0.032)",
  panelLine: "rgba(255,255,255,0.085)",
  host: "#8fb4ff", // host CPU side
  nic: "#ffb470", // NIC / card side
  push: "#5fe3c8", // our approach: DirectPush, data pushed into the reader's cache
  coh: "#b9a3ff", // coherence / CC-NIC / CXL
  bad: "#ff6b6b", // problems: round trips, collapse, invalidations
  gold: "#ffd27a", // the key number / emphasis
  link: "#41537a", // PCIe lanes at rest
};

export const TAB: React.CSSProperties = { fontVariantNumeric: "tabular-nums" };

/** rgba() from a #rrggbb colour. */
export function alpha(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// ---------------------------------------------------------------- rail
export const CC_STAGES: { label: string; color: string }[] = [
  { label: "The debate", color: P.coh },
  { label: "Not needed", color: P.push },
  { label: "Not free", color: P.gold },
  { label: "Real hardware", color: P.nic },
  { label: "End to end", color: P.host },
  { label: "Takeaway", color: P.ink },
];

/** Minimal progress rail: dots on a hairline, label under each; active dot ringed in its colour. */
export function StoryRail({ active, stages = CC_STAGES }: { active: number; stages?: { label: string; color: string }[] }) {
  const x0 = 1150;
  const x1 = 1800;
  const n = stages.length;
  const step = (x1 - x0) / (n - 1);
  const y = 52;
  return (
    <svg width={W} height={120} style={{ position: "absolute", left: 0, top: 0 }}>
      <line x1={x0} y1={y} x2={x1} y2={y} stroke={P.hair2} strokeWidth={1.5} />
      <line x1={x0} y1={y} x2={x0 + step * active} y2={y} stroke={alpha(stages[active].color, 0.7)} strokeWidth={2} />
      {stages.map((s, i) => {
        const x = x0 + i * step;
        const on = i === active;
        const done = i < active;
        return (
          <g key={s.label} fontFamily={FONT}>
            {on && <circle cx={x} cy={y} r={13} fill="none" stroke={alpha(s.color, 0.45)} strokeWidth={1.5} />}
            <circle
              cx={x}
              cy={y}
              r={on ? 7 : 5}
              fill={on ? s.color : done ? alpha(stages[active].color, 0.75) : P.bg0}
              stroke={on ? s.color : done ? alpha(stages[active].color, 0.75) : P.faint}
              strokeWidth={1.5}
            />
            <text
              x={x}
              y={y + 34}
              textAnchor="middle"
              fontSize={15}
              letterSpacing={0.6}
              fontWeight={on ? 600 : 400}
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

// ---------------------------------------------------------------- rich text + captions
/** Hyphenated / ranged tokens ("CC-NIC", "64-byte", "2.1–3.8") must not break across caption lines. */
const UNBREAKABLE = /([A-Za-z0-9.µ%×~]+(?:[-–][A-Za-z0-9.µ%×]+)+)/g;

/** Caption markup: **accent** (gold, bold) and `code` (mono, push colour). */
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
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("**") ? (
          <span key={i} style={{ color: accent, fontWeight: 600 }}>
            {keepWhole(p.slice(2, -2))}
          </span>
        ) : p.startsWith("`") ? (
          <span key={i} style={{ fontFamily: MONO, color: P.push, fontSize: "0.9em" }}>
            {p.slice(1, -1)}
          </span>
        ) : (
          <span key={i}>{keepWhole(p)}</span>
        ),
      )}
    </>
  );
}

function CcCaptions() {
  const ctx = useContext(SceneCtx)!;
  const r = useCurrentFrame() / FPS;
  const { cues } = ctx.def;
  let idx = -1;
  for (let i = 0; i < cues.length; i++) if (ctx.warp.toReal(cues[i][0]) <= r) idx = i;
  if (idx < 0) return null;
  const s = ctx.warp.toReal(cues[idx][0]);
  const e = idx + 1 < cues.length ? ctx.warp.toReal(cues[idx + 1][0]) : ctx.warp.realDur;
  const op = Math.min(clamp01((r - s) / 0.18), clamp01((e - r) / 0.18));
  // Spaces a caption line must not break at: a number and its unit; an article (or "no"/"one") and the next word;
  // a preposition and the number after it; an arrow and the numbers around it ("46.6% → 16.3%").
  const text = cues[idx][2]
    .replace(/(\d) (?=(ns|µs|ms|M\/s|Gbps|GB\/s|MiB|KiB|B\b|%|×))/g, "$1\u00a0")
    .replace(/\b(a|an|the|no|one) /gi, "$1\u00a0")
    .replace(/\b(at|to|from|under|than|of|by|about) (?=(\*\*)?[~−+<>≈]?\d)/gi, "$1\u00a0")
    .replace(/(\S) →(?= (\*\*)?[~−+]?\d)/g, "$1\u00a0→")
    .replace(/→ (?=(\*\*)?[~−+]?\d)/g, "→\u00a0");
  return (
    <div
      style={{
        position: "absolute",
        left: 150,
        right: 150,
        bottom: 42,
        display: "flex",
        justifyContent: "center",
        opacity: op,
      }}
    >
      <div
        style={{
          fontFamily: FONT,
          fontSize: 31,
          lineHeight: 1.42,
          color: P.ink,
          background: "rgba(4,7,13,0.82)",
          border: `1px solid ${P.panelLine}`,
          padding: "13px 34px 14px",
          borderRadius: 14,
          textAlign: "center",
          maxWidth: 1560,
          letterSpacing: 0.1,
          textWrap: "balance",
        }}
      >
        <RichText text={text} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- scene shell
/** Mono clips duplicated into a stereo mix measure ~2 dB hot; this brings the mix to about -16 LUFS. */
const VOICE_GAIN = Math.pow(10, -2.1 / 20);

function Backdrop() {
  return (
    <AbsoluteFill>
      <AbsoluteFill
        style={{ background: `radial-gradient(ellipse 110% 85% at 50% 40%, ${P.bg1} 0%, #070c18 55%, ${P.bg0} 100%)` }}
      />
      <svg width={W} height={H} style={{ position: "absolute", inset: 0, opacity: 0.55 }}>
        <defs>
          <pattern id="ccDots" width="48" height="48" patternUnits="userSpaceOnUse">
            <circle cx="24" cy="24" r="1.1" fill="rgba(160,180,220,0.10)" />
          </pattern>
        </defs>
        <rect width={W} height={H} fill="url(#ccDots)" />
      </svg>
    </AbsoluteFill>
  );
}

/** Scene shell (backdrop, title block, rail, captions, narration). `stages` defaults to the CcnicPaperStory rail. */
export function CcnicScene({
  def,
  warp,
  voiceMap,
  stages = CC_STAGES,
}: {
  def: SceneDef;
  warp: Warp;
  voiceMap: Record<string, number>;
  stages?: { label: string; color: string }[];
}) {
  const f = useCurrentFrame();
  const total = Math.ceil(warp.realDur * FPS);
  const fadeK = Math.min(clamp01(f / 12), clamp01((total - 1 - f) / 10));
  const Body = def.C;
  const accent = def.stage === undefined ? P.sub : stages[def.stage].color;
  const chapter = def.id.replace(/^[A-Z]+/, "");
  const kickerText = def.stage === undefined ? chapter : `${chapter}  ·  ${stages[def.stage].label.toUpperCase()}`;
  return (
    <SceneCtx.Provider value={{ def, warp }}>
      <AbsoluteFill style={{ fontFamily: FONT, color: P.ink, background: P.bg0 }}>
        <Backdrop />
        <AbsoluteFill style={{ opacity: fadeK }}>
          <Body />
        </AbsoluteFill>
        {def.title && (
          <div style={{ position: "absolute", left: 96, top: 34, opacity: fadeK }}>
            <div style={{ fontSize: 15, letterSpacing: 3.2, color: accent, fontWeight: 600 }}>{kickerText}</div>
            <div style={{ fontSize: 36, fontWeight: 600, color: P.ink, marginTop: 6, letterSpacing: -0.2 }}>{def.title}</div>
            {def.source && (
              <div style={{ fontSize: 15, color: P.faint, fontFamily: MONO, marginTop: 8, letterSpacing: 0.2 }}>
                {def.source}
              </div>
            )}
          </div>
        )}
        {def.stage !== undefined && (
          <div style={{ opacity: fadeK }}>
            <StoryRail active={def.stage} stages={stages} />
          </div>
        )}
        <CcCaptions />
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
/** Crossfade window: visible from a to b (both script seconds), d-second fades. */
export const win = (t: number, a: number, b = 1e9, d = 0.5) => prog(t, a, d) * (1 - prog(t, b, d));
/** Fast count-up (finishes within d ≤ 1.2 s); returns the formatted string. */
export function count(t: number, a: number, v: number, dec = 0, d = 0.9, from = 0): string {
  const x = lerp(from, v, prog(t, a, d));
  return x.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}
/** Soft rise + fade style for HTML blocks. */
export function rise(k: number, dy = 18): React.CSSProperties {
  return { opacity: k, transform: `translateY(${(1 - k) * dy}px)` };
}

// ---------------------------------------------------------------- HTML pieces (outside <Stage>)
/** Small uppercase pill: provenance / status tags ("EMULATED", "REAL HARDWARE", "READ FROM FIG. 14"). */
export function Tag({
  x,
  y,
  children,
  color = P.sub,
  opacity = 1,
  solid = false,
  size = 15,
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
        fontFamily: FONT,
        fontSize: size,
        fontWeight: 600,
        letterSpacing: 1.6,
        textTransform: "uppercase",
        color: solid ? P.bg0 : color,
        background: solid ? color : alpha(color.startsWith("#") ? color : "#ffffff", 0.08),
        border: `1px solid ${alpha(color.startsWith("#") ? color : "#ffffff", solid ? 1 : 0.45)}`,
        borderRadius: 999,
        padding: "5px 14px 4px",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </div>
  );
}

/** Absolutely positioned text block (HTML). */
export function Text({
  x,
  y,
  w,
  size = 28,
  color = P.ink,
  weight = 400,
  align = "left",
  opacity = 1,
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
        fontFamily: FONT,
        fontSize: size,
        color,
        fontWeight: weight,
        textAlign: align,
        lineHeight: 1.3,
        opacity,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/**
 * Headline number: big value (+ small unit), label underneath, optional note line.
 * (x, y) is the top-left of the block; `align` centres the block on x when "center".
 */
export function BigStat({
  x,
  y,
  value,
  unit,
  label,
  note,
  color = P.gold,
  size = 88,
  w = 420,
  align = "left",
  opacity = 1,
  dy = 0,
}: {
  x: number;
  y: number;
  value: React.ReactNode;
  unit?: string;
  label?: React.ReactNode;
  note?: React.ReactNode;
  color?: string;
  size?: number;
  w?: number;
  align?: "left" | "center";
  opacity?: number;
  dy?: number;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: align === "center" ? x - w / 2 : x,
        top: y + dy,
        width: w,
        opacity,
        textAlign: align,
        fontFamily: FONT,
      }}
    >
      <div style={{ fontSize: size, fontWeight: 800, color, letterSpacing: -1.5, lineHeight: 1, ...TAB }}>
        {value}
        {unit && (
          <span style={{ fontSize: size * 0.42, fontWeight: 600, marginLeft: size * 0.08, letterSpacing: 0 }}>{unit}</span>
        )}
      </div>
      {label && <div style={{ fontSize: 24, color: P.ink, marginTop: 12, lineHeight: 1.3 }}>{label}</div>}
      {note && <div style={{ fontSize: 18, color: P.sub, marginTop: 6, lineHeight: 1.35 }}>{note}</div>}
    </div>
  );
}

/** Quiet glass panel (HTML) with an optional kicker line. */
export function Glass({
  x,
  y,
  w,
  h,
  kicker,
  color = P.sub,
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
        border: `1px solid ${P.panelLine}`,
        borderRadius: 18,
        padding: "22px 28px",
        boxSizing: "border-box",
        fontFamily: FONT,
        ...style,
      }}
    >
      {kicker && (
        <div style={{ fontSize: 15, letterSpacing: 2.6, fontWeight: 600, color, textTransform: "uppercase", marginBottom: 12 }}>
          {kicker}
        </div>
      )}
      {children}
    </div>
  );
}

// ---------------------------------------------------------------- SVG pieces (inside <Stage>)
/** Full-frame SVG layer with the kit's filters (#ccGlow soft halo). */
export function Stage({ children, opacity = 1 }: { children: React.ReactNode; opacity?: number }) {
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ position: "absolute", inset: 0, opacity }}>
      <defs>
        <filter id="ccGlow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="7" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="ccSoft" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="16" />
        </filter>
        <pattern id="ccHatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="10" stroke={alpha(P.bad, 0.55)} strokeWidth="3" />
        </pattern>
      </defs>
      {children}
    </svg>
  );
}

/** Arrowed path (SVG) drawn progressively by k; `dash` for a dashed stroke; `head` = arrowhead length. */
export function Flow({
  pts,
  k = 1,
  color = P.sub,
  w = 3,
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
  if (k <= 0) return null;
  const segs: number[] = [];
  let total = 0;
  for (let i = 1; i < pts.length; i++) {
    const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    segs.push(d);
    total += d;
  }
  let left = total * clamp01(k);
  const drawn: Pt[] = [pts[0]];
  for (let i = 1; i < pts.length && left > 0; i++) {
    const d = segs[i - 1];
    if (left >= d) {
      drawn.push(pts[i]);
      left -= d;
    } else {
      const r = left / d;
      drawn.push([lerp(pts[i - 1][0], pts[i][0], r), lerp(pts[i - 1][1], pts[i][1], r)]);
      left = 0;
    }
  }
  const tip = drawn[drawn.length - 1];
  const prev = drawn.length > 1 ? drawn[drawn.length - 2] : pts[0];
  const ang = Math.atan2(tip[1] - prev[1], tip[0] - prev[0]);
  const hx = (a: number) => tip[0] - head * Math.cos(ang + a);
  const hy = (a: number) => tip[1] - head * Math.sin(ang + a);
  return (
    <g opacity={opacity}>
      {glow && <path d={pathD(drawn)} fill="none" stroke={alpha(color, 0.22)} strokeWidth={w + 10} strokeLinecap="round" />}
      <path d={pathD(drawn)} fill="none" stroke={color} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={dash} />
      {head > 0 && drawn.length > 1 && (
        <path d={`M${hx(0.42)} ${hy(0.42)} L${tip[0]} ${tip[1]} L${hx(-0.42)} ${hy(-0.42)}`} fill="none" stroke={color} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round" />
      )}
    </g>
  );
}

/** A 64-byte message: small rounded cell with a halo, travelling a polyline at fraction k (hidden outside 0..1). */
export function Cell({
  pts,
  k,
  color = P.push,
  w = 34,
  h = 22,
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
  if (k < 0 || k > 1 || opacity <= 0) return null;
  const [x, y] = along(pts, clamp01(k));
  return (
    <g opacity={opacity}>
      <rect x={x - w / 2 - 6} y={y - h / 2 - 6} width={w + 12} height={h + 12} rx={9} fill={alpha(color, 0.18)} filter="url(#ccSoft)" />
      <rect x={x - w / 2} y={y - h / 2} width={w} height={h} rx={5} fill={color} />
      {label && (
        <text x={x} y={y + 1} textAnchor="middle" dominantBaseline="central" fontSize={12} fontWeight={600} fill={P.bg0} fontFamily={FONT}>
          {label}
        </text>
      )}
    </g>
  );
}

/** Static cell glyph at (x, y) centre (no motion). */
export function CellAt({ x, y, color = P.push, w = 34, h = 22, opacity = 1 }: { x: number; y: number; color?: string; w?: number; h?: number; opacity?: number }) {
  return <Cell pts={[[x, y], [x + 0.01, y]]} k={0} color={color} w={w} h={h} opacity={opacity} />;
}

export type HBar = {
  label: string;
  value: number;
  valueText: string;
  color: string;
  note?: string; // small text under the label (e.g. "emulated · two Xeon sockets")
  k?: number; // 0..1 grow progress (default 1)
  opacity?: number;
  lo?: number; // range bar: solid up to lo, lighter from lo to value (e.g. "~1.3–2.1 µs")
  dashed?: boolean; // outline-only dashed bar (e.g. an emulated, not measured-on-hardware number)
};

/**
 * Horizontal bars (SVG): label column right-aligned at x + labelW - 24, bars start at x + labelW,
 * value text after the bar end. `max` sets the scale; `log` uses log10 from `min`.
 */
export function HBars({
  x,
  y,
  w,
  rows,
  max,
  labelW = 420,
  barH = 30,
  gap = 34,
  log = false,
  min = 1,
  size = 24,
  opacity = 1,
  track = false,
}: {
  x: number;
  y: number;
  w: number;
  rows: HBar[];
  /** draw a faint full-length track behind each bar */
  track?: boolean;
  max: number;
  labelW?: number;
  barH?: number;
  gap?: number;
  log?: boolean;
  min?: number;
  size?: number;
  opacity?: number;
}) {
  const span = Math.max(1, w - labelW - 190);
  const sc = (v: number) =>
    log ? (span * (Math.log10(Math.max(v, min)) - Math.log10(min))) / (Math.log10(max) - Math.log10(min)) : (span * v) / max;
  return (
    <g opacity={opacity} fontFamily={FONT}>
      {rows.map((r, i) => {
        const k = r.k ?? 1;
        const yy = y + i * (barH + gap);
        const bw = Math.max(0, sc(r.value) * k);
        const op = r.opacity ?? 1;
        return (
          <g key={r.label} opacity={op}>
            <text x={x + labelW - 24} y={yy + barH / 2 + (r.note ? -6 : 1)} textAnchor="end" dominantBaseline="central" fontSize={size} fill={P.ink}>
              {r.label}
            </text>
            {r.note && (
              <text x={x + labelW - 24} y={yy + barH / 2 + 20} textAnchor="end" dominantBaseline="central" fontSize={16} fill={P.sub} letterSpacing={0.4}>
                {r.note}
              </text>
            )}
            {track && <rect x={x + labelW} y={yy} width={span} height={barH} rx={barH / 2} fill="rgba(255,255,255,0.03)" />}
            {r.dashed ? (
              <rect
                x={x + labelW}
                y={yy + 1}
                width={bw}
                height={barH - 2}
                rx={(barH - 2) / 2}
                fill={alpha(r.color, 0.12)}
                stroke={r.color}
                strokeWidth={2}
                strokeDasharray="7 6"
              />
            ) : r.lo !== undefined ? (
              <>
                <rect x={x + labelW} y={yy} width={bw} height={barH} rx={barH / 2} fill={alpha(r.color, 0.38)} />
                <rect x={x + labelW} y={yy} width={Math.min(bw, Math.max(0, sc(r.lo) * k))} height={barH} rx={barH / 2} fill={r.color} opacity={0.92} />
              </>
            ) : (
              <rect x={x + labelW} y={yy} width={bw} height={barH} rx={barH / 2} fill={r.color} opacity={0.92} />
            )}
            <text
              x={x + labelW + bw + 18}
              y={yy + barH / 2 + 1}
              dominantBaseline="central"
              fontSize={size + 2}
              fontWeight={700}
              fill={r.color}
              opacity={clamp01((k - 0.6) / 0.4)}
              style={TAB}
            >
              {r.valueText}
            </text>
          </g>
        );
      })}
    </g>
  );
}

// ---------------------------------------------------------------- the running Host ⇄ NIC diagram
export type Rect = { x: number; y: number; w: number; h: number };
export const cx = (r: Rect) => r.x + r.w / 2;
export const cy = (r: Rect) => r.y + r.h / 2;
export const rgt = (r: Rect) => r.x + r.w;
export const btm = (r: Rect) => r.y + r.h;

export interface HnGeom {
  host: Rect;
  nic: Rect;
  hostCore: Rect;
  hostCache: Rect;
  hostMem: Rect;
  nicCore: Rect;
  nicCache: Rect;
  nicMem: Rect;
  /** link spans x from host right edge to nic left edge */
  linkX0: number;
  linkX1: number;
  /** flow lanes over the link: host→NIC traffic rides laneUp, NIC→host traffic rides laneDn; mid = link centre */
  laneUp: number;
  laneDn: number;
  mid: number;
  s: number;
  /** maps a point of the default (s = 1) layout into this geometry */
  at: (x: number, y: number) => Pt;
}

/**
 * Geometry of the running diagram. Default (s = 1): host box x 150..690, NIC box x 1230..1770, y 230..790;
 * inside each box a core (top), a cache (middle, at link height) and a memory (bottom).
 * `s` scales about (960, 510), then (dx, dy) shifts. Keep s ≥ 0.7 so part labels stay ≥ 16 px.
 */
export function hnGeom(s = 1, dx = 0, dy = 0): HnGeom {
  const at = (x: number, y: number): Pt => [960 + (x - 960) * s + dx, 510 + (y - 510) * s + dy];
  const R = (x: number, y: number, w: number, h: number): Rect => {
    const [X, Y] = at(x, y);
    return { x: X, y: Y, w: Math.max(0, w * s), h: Math.max(0, h * s) };
  };
  return {
    host: R(150, 230, 540, 560),
    nic: R(1230, 230, 540, 560),
    hostCore: R(200, 300, 220, 96),
    hostCache: R(200, 440, 440, 120),
    hostMem: R(200, 620, 440, 110),
    nicCore: R(1500, 300, 220, 96),
    nicCache: R(1280, 440, 440, 120),
    nicMem: R(1280, 620, 440, 110),
    linkX0: at(690, 0)[0],
    linkX1: at(1230, 0)[0],
    laneUp: at(0, 486)[1],
    laneDn: at(0, 514)[1],
    mid: at(0, 500)[1],
    s,
    at,
  };
}

export type HnPart = "host" | "nic" | "hostCore" | "hostCache" | "hostMem" | "nicCore" | "nicCache" | "nicMem" | "link";

/**
 * Routes on the diagram (polylines, default-layout coordinates mapped through g.at). Host→NIC traffic uses
 * laneUp (y 486), NIC→host traffic laneDn (y 514); vertical risers run in the box margins (host x 650–675,
 * NIC x 1250–1270, host-left x 178) so no route crosses a part box.
 */
export function hnRoutes(g: HnGeom) {
  const m = (pts: [number, number][]): Pt[] => pts.map(([x, y]) => g.at(x, y));
  return {
    /** host core → NIC cache: one pushed 64 B write (ends inside the NIC cache) */
    pushH2N: m([[420, 348], [665, 348], [665, 486], [1335, 486]]),
    /** NIC core → host cache: one pushed reply (ends inside the host cache) */
    pushN2H: m([[1500, 348], [1255, 348], [1255, 514], [585, 514]]),
    /** NIC core reads its own cache (short local hop, downward) */
    nicPoll: m([[1540, 396], [1540, 440]]),
    /** host core reads its own cache (short local hop, downward) */
    hostPoll: m([[280, 396], [280, 440]]),
    /** host core → its own memory down the left margin (writes a descriptor / buffer) */
    hostToMem: m([[200, 348], [178, 348], [178, 675], [200, 675]]),
    /** doorbell: host core → NIC core (an MMIO write) */
    doorbell: m([[420, 330], [650, 330], [650, 486], [1270, 486], [1270, 330], [1500, 330]]),
    /** NIC read request: NIC core → host memory */
    readReq: m([[1500, 366], [1262, 366], [1262, 514], [665, 514], [665, 668], [640, 668]]),
    /** NIC read completion: host memory → NIC core (the data coming back) */
    readCpl: m([[640, 690], [675, 690], [675, 486], [1250, 486], [1250, 384], [1500, 384]]),
    /** NIC → host memory write (e.g. packet / completion / burst spilling into host DRAM) */
    nicToHostMem: m([[1500, 372], [1262, 372], [1262, 514], [658, 514], [658, 700], [640, 700]]),
    /** NIC cache → NIC memory (inside the card) */
    nicCacheToMem: m([[1500, 560], [1500, 620]]),
  };
}

const PART_LABEL: Record<Exclude<HnPart, "host" | "nic" | "link">, string> = {
  hostCore: "CPU core",
  hostCache: "Cache (LLC)",
  hostMem: "Memory (DRAM)",
  nicCore: "NIC core",
  nicCache: "NIC cache (L3)",
  nicMem: "NIC memory",
};

/**
 * The running Host ⇄ NIC diagram (SVG, inside <Stage>).
 * - `hl[part]` 0..1 lights a part in its side colour (or `hlColor[part]`); `dim[part]` 0..1 fades it.
 * - `coh` 0..1 morphs the PCIe lanes into a thick lavender "coherent link" and draws one dashed coherence
 *   domain around both caches (CC-NIC's world). `linkLabel` overrides the link caption.
 * - `labels` overrides part captions; `sideLabels` false hides the HOST / NIC headers.
 */
export function HostNic({
  g,
  opacity = 1,
  hl = {},
  hlColor = {},
  dim = {},
  coh = 0,
  labels = {},
  linkLabel,
  hostTitle = "HOST · Xeon",
  nicTitle = "NIC · Marvell CN106",
  sideLabels = true,
}: {
  g: HnGeom;
  opacity?: number;
  hl?: Partial<Record<HnPart, number>>;
  hlColor?: Partial<Record<HnPart, string>>;
  dim?: Partial<Record<HnPart, number>>;
  coh?: number;
  labels?: Partial<Record<HnPart, string>>;
  linkLabel?: string;
  hostTitle?: string;
  nicTitle?: string;
  sideLabels?: boolean;
}) {
  const s = g.s;
  const fs = (v: number) => Math.max(10, v * s);
  const box = (r: Rect, side: string, part: HnPart, title?: string) => {
    const h = hl[part] ?? 0;
    const d = 1 - 0.75 * (dim[part] ?? 0);
    const col = hlColor[part] ?? side;
    return (
      <g opacity={d}>
        {h > 0 && <rect x={r.x - 4} y={r.y - 4} width={r.w + 8} height={r.h + 8} rx={22 * s} fill={alpha(col, 0.1 * h)} filter="url(#ccSoft)" />}
        <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={20 * s} fill="rgba(255,255,255,0.022)" stroke={alpha(side, 0.35 + 0.4 * h)} strokeWidth={1.5} />
        {title && sideLabels && (
          <text x={r.x + 22 * s} y={r.y + 32 * s} fontSize={fs(17)} letterSpacing={2.4} fontWeight={600} fill={alpha(side, 0.9)}>
            {title}
          </text>
        )}
      </g>
    );
  };
  const part = (r: Rect, side: string, p: Exclude<HnPart, "host" | "nic" | "link">) => {
    const h = hl[p] ?? 0;
    const d = 1 - 0.75 * (dim[p] ?? 0);
    const col = hlColor[p] ?? side;
    return (
      <g opacity={d}>
        {h > 0 && <rect x={r.x - 6} y={r.y - 6} width={r.w + 12} height={r.h + 12} rx={16 * s} fill={alpha(col, 0.22 * h)} filter="url(#ccSoft)" />}
        <rect
          x={r.x}
          y={r.y}
          width={r.w}
          height={r.h}
          rx={14 * s}
          fill={alpha(col, 0.06 + 0.1 * h)}
          stroke={alpha(col, 0.45 + 0.5 * h)}
          strokeWidth={1.5 + h}
        />
        <text x={cx(r)} y={cy(r) + 1} textAnchor="middle" dominantBaseline="central" fontSize={fs(24)} fontWeight={h > 0.5 ? 600 : 400} fill={P.ink}>
          {labels[p] ?? PART_LABEL[p]}
        </text>
      </g>
    );
  };
  const lh = hl.link ?? 0;
  const lanes = [-21, -7, 7, 21];
  const lk = clamp01(coh);
  return (
    <g opacity={opacity} fontFamily={FONT}>
      {box(g.host, P.host, "host", hostTitle)}
      {box(g.nic, P.nic, "nic", nicTitle)}
      {/* PCIe lanes (fade out as the coherent link fades in) */}
      <g opacity={(1 - lk) * (1 - 0.75 * (dim.link ?? 0))}>
        {lanes.map((o) => (
          <line key={o} x1={g.linkX0} y1={g.mid + o * s} x2={g.linkX1} y2={g.mid + o * s} stroke={lh > 0 ? alpha(P.push, 0.35 + 0.4 * lh) : P.link} strokeWidth={2} />
        ))}
        <text x={(g.linkX0 + g.linkX1) / 2} y={g.mid - 48 * s} textAnchor="middle" fontSize={fs(17)} letterSpacing={2} fontWeight={600} fill={P.sub}>
          {linkLabel ?? "PCIe Gen5 ×4"}
        </text>
      </g>
      {/* coherent link + coherence domain */}
      {lk > 0 && (
        <g opacity={lk}>
          <rect x={g.linkX0} y={g.mid - 26 * s} width={g.linkX1 - g.linkX0} height={52 * s} fill={alpha(P.coh, 0.16)} stroke={alpha(P.coh, 0.6)} strokeWidth={1.5} />
          <text x={(g.linkX0 + g.linkX1) / 2} y={g.mid - 48 * s} textAnchor="middle" fontSize={fs(17)} letterSpacing={2} fontWeight={600} fill={P.coh}>
            {linkLabel ?? "COHERENT LINK"}
          </text>
          <rect
            x={g.hostCache.x - 22 * s}
            y={g.hostCache.y - 22 * s}
            width={rgt(g.nicCache) - g.hostCache.x + 44 * s}
            height={g.hostCache.h + 44 * s}
            rx={26 * s}
            fill="none"
            stroke={P.coh}
            strokeWidth={2}
            strokeDasharray="10 8"
            opacity={0.85}
          />
          <text x={(g.linkX0 + g.linkX1) / 2} y={g.hostCache.y + g.hostCache.h + 52 * s} textAnchor="middle" fontSize={fs(18)} fill={P.coh} letterSpacing={1}>
            one coherence domain
          </text>
        </g>
      )}
      {part(g.hostCore, P.host, "hostCore")}
      {part(g.hostCache, P.host, "hostCache")}
      {part(g.hostMem, P.host, "hostMem")}
      {part(g.nicCore, P.nic, "nicCore")}
      {part(g.nicCache, P.nic, "nicCache")}
      {part(g.nicMem, P.nic, "nicMem")}
    </g>
  );
}

/** Small numbered step badge (SVG) at (x, y): used to count link crossings. */
export function StepBadge({ x, y, n, color = P.bad, k = 1 }: { x: number; y: number; n: number | string; color?: string; k?: number }) {
  if (k <= 0) return null;
  return (
    <g opacity={k} fontFamily={FONT}>
      <circle cx={x} cy={y} r={17 * (0.6 + 0.4 * k)} fill={P.bg0} stroke={color} strokeWidth={2} />
      <text x={x} y={y + 1} textAnchor="middle" dominantBaseline="central" fontSize={17} fontWeight={700} fill={color}>
        {n}
      </text>
    </g>
  );
}

/** A soft vertical divider line (SVG). */
export function Divider({ x, y0, y1, opacity = 1 }: { x: number; y0: number; y1: number; opacity?: number }) {
  return <line x1={x} y1={y0} x2={x} y2={y1} stroke={P.hair2} strokeWidth={1.5} opacity={opacity} />;
}
