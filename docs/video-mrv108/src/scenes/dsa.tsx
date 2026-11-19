import React from "react";
import { C, FONT, K, MONO, Pt, clamp01, lerp, pathD } from "../lib";

/** Small [INF] tag for inferences (spoken: "我们推断" / "我们认为"). */
export function InfTag({
  x,
  y,
  text = "[INF] 推断",
  opacity = 1,
}: {
  x: number;
  y: number;
  text?: string;
  opacity?: number;
}) {
  if (opacity <= 0.01) return null;
  const w = Math.max(112, text.length * 16 + 24);
  return (
    <g opacity={opacity} fontFamily={MONO}>
      <rect x={x} y={y} width={w} height={32} rx={16} fill="rgba(94,234,212,0.14)" stroke={K.inf} strokeWidth={1.8} />
      <text x={x + w / 2} y={y + 17} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={700} fill={K.inf}>
        {text}
      </text>
    </g>
  );
}

/** Crisp SVG check or cross badge. */
export function StatusBadge({
  cx,
  cy,
  ok,
  r = 18,
  opacity = 1,
}: {
  cx: number;
  cy: number;
  ok: boolean;
  r?: number;
  opacity?: number;
}) {
  if (opacity <= 0.01) return null;
  const col = ok ? C.green : C.red;
  return (
    <g opacity={opacity}>
      <circle cx={cx} cy={cy} r={r} fill={ok ? "rgba(67,197,158,0.20)" : "rgba(249,65,68,0.22)"} stroke={col} strokeWidth={2.5} />
      {ok ? (
        <polyline
          points={`${cx - r * 0.44},${cy + r * 0.02} ${cx - r * 0.1},${cy + r * 0.38} ${cx + r * 0.46},${cy - r * 0.34}`}
          fill="none"
          stroke={col}
          strokeWidth={3.2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : (
        <g stroke={col} strokeWidth={3.2} strokeLinecap="round">
          <line x1={cx - r * 0.36} y1={cy - r * 0.36} x2={cx + r * 0.36} y2={cy + r * 0.36} />
          <line x1={cx + r * 0.36} y1={cy - r * 0.36} x2={cx - r * 0.36} y2={cy + r * 0.36} />
        </g>
      )}
    </g>
  );
}

/** Hardware block box inside SVG <Stage>. Scale-safe geometry. */
export function HwBox({
  x,
  y,
  w,
  h,
  title,
  sub,
  sub2,
  accent = C.line,
  active = false,
  dim = false,
  dashed = false,
  sleeping = false,
  badge,
  titleSize = 24,
  subSize = 18,
  opacity = 1,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  sub?: string;
  sub2?: string;
  accent?: string;
  active?: boolean;
  dim?: boolean;
  dashed?: boolean;
  sleeping?: boolean;
  badge?: string;
  titleSize?: number;
  subSize?: number;
  opacity?: number;
}) {
  if (opacity <= 0.01) return null;
  const effOp = opacity * (dim ? 0.32 : 1);
  const fill = sleeping
    ? "rgba(20,27,46,0.55)"
    : active
      ? "rgba(24,36,64,0.96)"
      : "rgba(20,27,46,0.92)";
  const stroke = sleeping ? C.dim : accent;
  const sw = active ? 3.2 : 2;
  const cy = y + h / 2;
  const lines = [title, sub, sub2].filter(Boolean).length;
  const y0 = lines === 1 ? cy : lines === 2 ? cy - 13 : cy - 24;
  return (
    <g opacity={effOp}>
      {active && !sleeping && (
        <rect
          x={x - 3}
          y={y - 3}
          width={Math.max(0, w + 6)}
          height={Math.max(0, h + 6)}
          rx={15}
          fill="none"
          stroke={accent}
          strokeWidth={6}
          opacity={0.22}
        />
      )}
      <rect
        x={x}
        y={y}
        width={Math.max(0, w)}
        height={Math.max(0, h)}
        rx={12}
        fill={fill}
        stroke={stroke}
        strokeWidth={sw}
        strokeDasharray={dashed || sleeping ? "8 6" : undefined}
      />
      <text
        x={x + w / 2}
        y={y0}
        textAnchor="middle"
        dominantBaseline="central"
        fontFamily={FONT}
        fontSize={titleSize}
        fontWeight={800}
        fill={sleeping ? C.muted : active ? accent : C.text}
      >
        {title}
      </text>
      {sub && (
        <text
          x={x + w / 2}
          y={y0 + 26}
          textAnchor="middle"
          dominantBaseline="central"
          fontFamily={MONO}
          fontSize={subSize}
          fill={sleeping ? C.dim : C.muted}
        >
          {sub}
        </text>
      )}
      {sub2 && (
        <text
          x={x + w / 2}
          y={y0 + 49}
          textAnchor="middle"
          dominantBaseline="central"
          fontFamily={FONT}
          fontSize={subSize - 1}
          fill={sleeping ? C.dim : accent}
          fontWeight={600}
        >
          {sub2}
        </text>
      )}
      {badge && (
        <g>
          <rect
            x={x + w - Math.max(74, badge.length * 14 + 18)}
            y={y - 13}
            width={Math.max(74, badge.length * 14 + 18)}
            height={26}
            rx={13}
            fill={sleeping ? C.panel : accent}
            stroke={sleeping ? C.dim : accent}
            strokeWidth={1.5}
          />
          <text
            x={x + w - Math.max(74, badge.length * 14 + 18) / 2}
            y={y}
            textAnchor="middle"
            dominantBaseline="central"
            fontFamily={FONT}
            fontSize={16}
            fontWeight={800}
            fill={sleeping ? C.muted : C.bg}
          >
            {badge}
          </text>
        </g>
      )}
    </g>
  );
}

/** Smooth polyline arrow drawn progressively with k in [0,1]. */
export function PolyArrow({
  pts,
  color = C.cyan,
  w = 4,
  k = 1,
  dash,
  head = 14,
  opacity = 1,
  halo = false,
}: {
  pts: Pt[];
  color?: string;
  w?: number;
  k?: number;
  dash?: string;
  head?: number;
  opacity?: number;
  halo?: boolean;
}) {
  const kk = clamp01(k);
  if (kk <= 0.01 || opacity <= 0.01 || pts.length < 2) return null;
  const segs: number[] = [];
  let tot = 0;
  for (let i = 1; i < pts.length; i++) {
    const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    segs.push(d);
    tot += d;
  }
  const target = tot * kk;
  let acc = 0;
  const vis: Pt[] = [pts[0]];
  let lastDir: [number, number] = [1, 0];
  for (let i = 0; i < segs.length; i++) {
    if (acc + segs[i] >= target) {
      const f = segs[i] > 0 ? (target - acc) / segs[i] : 1;
      const ex = lerp(pts[i][0], pts[i + 1][0], f);
      const ey = lerp(pts[i][1], pts[i + 1][1], f);
      vis.push([ex, ey]);
      lastDir = [pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]];
      break;
    }
    acc += segs[i];
    vis.push(pts[i + 1]);
    lastDir = [pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]];
  }
  const end = vis[vis.length - 1];
  const a = Math.atan2(lastDir[1], lastDir[0]);
  const p1: Pt = [end[0] - head * Math.cos(a - 0.45), end[1] - head * Math.sin(a - 0.45)];
  const p2: Pt = [end[0] - head * Math.cos(a + 0.45), end[1] - head * Math.sin(a + 0.45)];
  const dStr = pathD(vis);
  return (
    <g opacity={opacity}>
      {halo && <path d={dStr} fill="none" stroke={color} strokeWidth={w + 8} strokeLinecap="round" strokeLinejoin="round" opacity={0.18} />}
      <path d={dStr} fill="none" stroke={color} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={dash} />
      <polygon points={`${end[0]},${end[1]} ${p1[0]},${p1[1]} ${p2[0]},${p2[1]}`} fill={color} />
    </g>
  );
}

/** Label pill placed along a path or diagram. */
export function PathTag({
  cx,
  cy,
  text,
  color = C.cyan,
  bg = "#0d1528",
  size = 20,
  mono = false,
  opacity = 1,
}: {
  cx: number;
  cy: number;
  text: string;
  color?: string;
  bg?: string;
  size?: number;
  mono?: boolean;
  opacity?: number;
}) {
  if (opacity <= 0.01) return null;
  let units = 0;
  for (const ch of text) units += ch.charCodeAt(0) > 127 ? 1.05 : 0.62;
  const w = Math.max(88, units * size + 26);
  const h = size + 16;
  return (
    <g opacity={opacity}>
      <rect x={cx - w / 2} y={cy - h / 2} width={w} height={h} rx={h / 2} fill={bg} stroke={color} strokeWidth={2} />
      <text
        x={cx}
        y={cy + 1}
        textAnchor="middle"
        dominantBaseline="central"
        fontFamily={mono ? MONO : FONT}
        fontSize={size}
        fontWeight={700}
        fill={color}
      >
        {text}
      </text>
    </g>
  );
}

/** Execution timeline block for latency / bubble / batch pipeline diagrams. */
export function TimeBlock({
  x,
  y,
  w,
  h,
  label,
  sub,
  color = C.cyan,
  hatch = false,
  dashed = false,
  opacity = 1,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  sub?: string;
  color?: string;
  hatch?: boolean;
  dashed?: boolean;
  opacity?: number;
}) {
  if (opacity <= 0.01 || w <= 1) return null;
  const fill = hatch ? "url(#hatchRed)" : `${color}22`;
  return (
    <g opacity={opacity}>
      <rect
        x={x}
        y={y}
        width={Math.max(0, w)}
        height={Math.max(0, h)}
        rx={8}
        fill={fill}
        stroke={color}
        strokeWidth={2.4}
        strokeDasharray={dashed ? "7 5" : undefined}
      />
      <text
        x={x + w / 2}
        y={sub ? y + h / 2 - 11 : y + h / 2}
        textAnchor="middle"
        dominantBaseline="central"
        fontFamily={FONT}
        fontSize={w < 95 ? 18 : 22}
        fontWeight={800}
        fill={hatch ? C.text : color}
      >
        {label}
      </text>
      {sub && (
        <text
          x={x + w / 2}
          y={y + h / 2 + 14}
          textAnchor="middle"
          dominantBaseline="central"
          fontFamily={MONO}
          fontSize={w < 95 ? 15 : 18}
          fill={C.text}
          opacity={0.9}
        >
          {sub}
        </text>
      )}
    </g>
  );
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
  cx: number;
  cy: number;
}

const mkRect = (x: number, y: number, w: number, h: number): Rect => ({
  x,
  y,
  w,
  h,
  cx: x + w / 2,
  cy: y + h / 2,
});

export interface DsaGeom {
  cardFrame: Rect;
  hostFrame: Rect;
  pcieX: number;
  core: Rect;
  dpiW: Rect;
  bar4: Rect;
  dpiR: Rect;
  hostCpu: Rect;
  dmar0: Rect;
  dsa: Rect;
  dmar6: Rect;
  mem: Rect;
}

/**
 * Full-width 16:9 architecture diagram: 1760 x 516 at s=1 (x=80..1840, y=134..650, 330px PCIe center gap).
 */
export function dsaGeom(dx = 80, dy = 134, s = 1): DsaGeom {
  const r = (x: number, y: number, w: number, h: number) => mkRect(dx + x * s, dy + y * s, w * s, h * s);
  return {
    cardFrame: r(0, 0, 520, 516),
    hostFrame: r(850, 0, 910, 516),
    pcieX: dx + 685 * s,
    // Card column (left, x 0..520)
    core: r(26, 70, 218, 138),
    dpiW: r(274, 70, 222, 138),
    bar4: r(26, 326, 224, 152),
    dpiR: r(278, 326, 218, 152),
    // Host column (right, x 850..1760)
    hostCpu: r(882, 66, 246, 108),
    dmar0: r(882, 206, 232, 114),
    dsa: r(1162, 184, 260, 148),
    dmar6: r(1162, 366, 260, 118),
    mem: r(1468, 184, 264, 300),
  };
}

export function dsaRoutes(g: DsaGeom) {
  return {
    tradSubmit: [
      [g.dpiW.x + g.dpiW.w, g.dpiW.cy - 18],
      [g.hostCpu.x, g.hostCpu.cy],
    ] as Pt[],
    tradCpuToDsa: [
      [g.hostCpu.x + g.hostCpu.w, g.hostCpu.cy],
      [g.dsa.cx, g.hostCpu.cy],
      [g.dsa.cx, g.dsa.y],
    ] as Pt[],
    fwdP2P: [
      [g.dpiW.x + g.dpiW.w, g.dpiW.cy + 14],
      [g.pcieX - 70, g.dpiW.cy + 14],
      [g.pcieX - 70, g.dmar0.cy],
      [g.dmar0.x, g.dmar0.cy],
    ] as Pt[],
    dmar0ToDsa: [
      [g.dmar0.x + g.dmar0.w, g.dmar0.cy],
      [g.dsa.x, g.dmar0.cy],
    ] as Pt[],
    dsaToDmar6: [
      [g.dsa.cx, g.dsa.y + g.dsa.h],
      [g.dmar6.cx, g.dmar6.y],
    ] as Pt[],
    dmar6ToMem: [
      [g.dmar6.x + g.dmar6.w, g.dmar6.cy],
      [g.mem.x, g.dmar6.cy],
    ] as Pt[],
    revP2P: [
      [g.dmar6.x, g.dmar6.cy],
      [g.pcieX + 40, g.dmar6.cy],
      [g.pcieX + 40, g.bar4.cy + 14],
      [g.bar4.x + g.bar4.w, g.bar4.cy + 14],
    ] as Pt[],
    localPoll: [
      [g.bar4.cx, g.bar4.y],
      [g.core.cx, g.core.y + g.core.h],
    ] as Pt[],
    dpiReadBack: [
      [g.mem.cx, g.mem.y + g.mem.h],
      [g.mem.cx, g.hostFrame.y + g.hostFrame.h - 14],
      [g.dpiR.cx, g.hostFrame.y + g.hostFrame.h - 14],
      [g.dpiR.cx, g.dpiR.y + g.dpiR.h],
    ] as Pt[],
  };
}
