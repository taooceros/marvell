/**
 * Shared visual vocabulary for CcnicPaperStory (C01..C08 — Advisor-Ready Cut).
 * All SVG helpers are scale-safe (clamped >= 0) and stay strictly inside y 120..890.
 * HTML helpers (HeroStat, VerdictRow, QuestionBanner, BoundaryBar) MUST be placed outside <Stage>.
 */
import React from "react";
import { C, FONT, MONO, Pt, clamp01, lerp } from "../lib";

/** Unified semantic colors across all 12 CcnicPaperStory scenes. */
export const CK = {
  push: C.cyan,      // DirectPush small inline (MOVDIR64B + WC)
  dma: C.pink,       // Native NIC DPI DMA (large payloads >= 512 B / 1 KiB)
  sdp: C.purple,     // Traditional SDP baseline
  cxl: C.yellow,     // CC-NIC / CXL-NIC coherence comparison
  trap: C.red,       // Traps, cliffs, bottlenecks, retracted claims
  ok: C.green,       // Verified wins, 100% DDIO, shaped steady state, 100GbE line rate
  card: C.orange,    // CN106 DPU / card L3 / card DDR5 / active NIC
  host: "#7c93d6",   // Host saturn / LLC / DDR5
  peer: "#5eead4",   // Remote peer mercury (ConnectX-5) / 100GbE wire
  muted: C.muted,
};

export type BoxRect = { x: number; y: number; w: number; h: number };

export type TopoNodeId =
  | "saturn"
  | "cpu"
  | "hLlc"
  | "hDram"
  | "iommu"
  | "pcie"
  | "cn106"
  | "pem"
  | "arm"
  | "cL3"
  | "dpi"
  | "cDram"
  | "nix"
  | "wire"
  | "mercury";

const TOPO_BASE: Record<TopoNodeId, BoxRect> = {
  saturn:  { x: 60,   y: 160, w: 500, h: 520 },
  cpu:     { x: 90,   y: 220, w: 205, h: 112 },
  hLlc:    { x: 325,  y: 220, w: 205, h: 112 },
  iommu:   { x: 90,   y: 368, w: 440, h: 86 },
  hDram:   { x: 90,   y: 492, w: 440, h: 152 },

  pcie:    { x: 560,  y: 345, w: 170, h: 132 },

  cn106:   { x: 730,  y: 160, w: 620, h: 520 },
  pem:     { x: 760,  y: 220, w: 165, h: 156 },
  arm:     { x: 955,  y: 220, w: 195, h: 112 },
  cL3:     { x: 955,  y: 356, w: 195, h: 100 },
  dpi:     { x: 760,  y: 400, w: 165, h: 96 },
  cDram:   { x: 760,  y: 522, w: 390, h: 122 },
  nix:     { x: 1175, y: 220, w: 145, h: 424 },

  wire:    { x: 1350, y: 345, w: 170, h: 132 },
  mercury: { x: 1520, y: 195, w: 340, h: 450 },
};

export interface TopoGeom {
  s: number;
  dx: number;
  dy: number;
  r: Record<TopoNodeId, BoxRect>;
  pt: (p: Pt) => Pt;
  c: (id: TopoNodeId) => Pt;
}

export function topoGeom(s = 1, dx = 0, dy = 0): TopoGeom {
  const pt = ([x, y]: Pt): Pt => [x * s + dx, y * s + dy];
  const r = {} as Record<TopoNodeId, BoxRect>;
  (Object.keys(TOPO_BASE) as TopoNodeId[]).forEach((k) => {
    const b = TOPO_BASE[k];
    r[k] = { x: b.x * s + dx, y: b.y * s + dy, w: Math.max(0, b.w * s), h: Math.max(0, b.h * s) };
  });
  const c = (id: TopoNodeId): Pt => [r[id].x + r[id].w / 2, r[id].y + r[id].h / 2];
  return { s, dx, dy, r, pt, c };
}

export interface TopoRoutes {
  pushH2C: Pt[];
  wcC2H: Pt[];
  dmaH2C: Pt[];
  dmaC2H: Pt[];
  wireTx: Pt[];
  wireRx: Pt[];
}

export function topoRoutes(g: TopoGeom): TopoRoutes {
  const tr = (pts: Pt[]) => pts.map(g.pt);
  return {
    // Host CPU -> PCIe upper lane -> PEM0 -> Card L3 -> ARM
    pushH2C: tr([
      [295, 276],
      [540, 276],
      [540, 382],
      [760, 382],
      [842, 382],
      [842, 298],
      [955, 298],
    ]),
    // ARM -> PEM0 -> PCIe lower lane -> Host LLC (DDIO)
    wcC2H: tr([
      [955, 255],
      [880, 255],
      [880, 442],
      [540, 442],
      [540, 315],
      [530, 315],
    ]),
    // Host DRAM -> IOMMU -> PCIe -> DPI DMA -> Card DRAM
    dmaH2C: tr([
      [530, 568],
      [548, 568],
      [548, 464],
      [760, 464],
    ]),
    // DPI DMA -> PCIe -> IOMMU -> Host DRAM
    dmaC2H: tr([
      [760, 428],
      [548, 428],
      [548, 535],
      [530, 535],
    ]),
    // CN106 NIX -> 100GbE wire -> mercury
    wireTx: tr([
      [1320, 386],
      [1520, 386],
    ]),
    wireRx: tr([
      [1520, 438],
      [1320, 438],
    ]),
  };
}

export type TopoProps = {
  g: TopoGeom;
  showPeer?: boolean;
  hl?: Partial<Record<TopoNodeId, string>>;
  dim?: Partial<Record<TopoNodeId, number>>;
  labels?: Partial<Record<TopoNodeId, [string, string?]>>;
  opacity?: number;
};

const DEFAULT_TOPO_LABELS: Record<TopoNodeId, [string, string?]> = {
  saturn:  ["HOST · saturn", "Xeon · 1 pinned core"],
  cpu:     ["Host Core", "64 B direct push · L1"],
  hLlc:    ["60 MiB LLC", "8 MiB Intel DDIO"],
  iommu:   ["HW IOMMU (always on)", "Full translation · 2 MiB hugepages"],
  hDram:   ["Host DDR5", "TX retention / RX ring"],
  pcie:    ["PCIe Gen5 ×4", "15.75 GB/s / dir"],
  cn106:   ["DPU · Marvell CN106", "ARM Neoverse-N2 cores"],
  pem:     ["PCIe / BAR4", "Direct push + WC"],
  arm:     ["NIC Cores", "Packet & burst logic"],
  cL3:     ["48 MiB L3", "Card cache"],
  dpi:     ["Native DMA", "Built-in NIC engine"],
  cDram:   ["Card DDR5", "Dedicated NIC RAM"],
  nix:     ["100G MAC", "eth0 port"],
  wire:    ["100GbE DAC", "3 m physical cable"],
  mercury: ["PEER · mercury", "100GbE"],
};

/** Unified 3-node (or 2-node when showPeer=false) hardware architecture SVG block. */
export function TestbedTopo({ g, showPeer = true, hl = {}, dim = {}, labels = {}, opacity = 1 }: TopoProps) {
  const s = g.s;
  const fs = (px: number) => Math.max(10, Math.round(px * s));

  const drawBox = (
    id: TopoNodeId,
    baseColor: string,
    opts?: { fill?: string; dashed?: boolean; topLabel?: boolean; vertical?: boolean }
  ) => {
    const b = g.r[id];
    const accent = hl[id];
    const stroke = accent ?? baseColor;
    const op = (1 - (dim[id] ?? 0)) * opacity;
    const [main, sub] = labels[id] ?? DEFAULT_TOPO_LABELS[id];
    const pad = 12 * s;
    return (
      <g key={id} opacity={op}>
        <rect
          x={b.x}
          y={b.y}
          width={b.w}
          height={b.h}
          rx={Math.max(4, 14 * s)}
          fill={opts?.fill ?? (accent ? `${accent}1e` : C.panel)}
          stroke={stroke}
          strokeWidth={Math.max(1.2, (accent ? 3.2 : 2) * s)}
          strokeDasharray={opts?.dashed ? `${8 * s} ${6 * s}` : undefined}
        />
        {opts?.topLabel ? (
          <>
            <text x={b.x + pad + 4 * s} y={b.y + 30 * s} fontSize={fs(22)} fontWeight={800} fill={stroke} fontFamily={FONT}>
              {main}
            </text>
            {sub && (
              <text x={b.x + b.w - pad - 4 * s} y={b.y + 29 * s} textAnchor="end" fontSize={fs(16)} fill={C.muted} fontFamily={MONO}>
                {sub}
              </text>
            )}
          </>
        ) : (
          <>
            <text
              x={b.x + b.w / 2}
              y={b.y + b.h / 2 - (sub ? 9 * s : 0)}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={fs(21)}
              fontWeight={700}
              fill={accent ? C.text : baseColor === "#53658c" ? "#c8d4ee" : stroke}
              fontFamily={FONT}
            >
              {main}
            </text>
            {sub && (
              <text
                x={b.x + b.w / 2}
                y={b.y + b.h / 2 + 17 * s}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={fs(15)}
                fill={C.muted}
                fontFamily={MONO}
              >
                {sub}
              </text>
            )}
          </>
        )}
      </g>
    );
  };

  const pcieB = g.r.pcie;
  const wireB = g.r.wire;
  const pcieAccent = hl.pcie ?? CK.push;
  const wireAccent = hl.wire ?? CK.peer;

  return (
    <g>
      {drawBox("saturn", CK.host, { fill: "#10172b", topLabel: true })}
      {drawBox("cpu", CK.host)}
      {drawBox("hLlc", CK.ok)}
      {drawBox("iommu", CK.cxl)}
      {drawBox("hDram", "#53658c")}

      {/* PCIe link strip */}
      <g opacity={(1 - (dim.pcie ?? 0)) * opacity}>
        <line x1={pcieB.x} y1={pcieB.y + pcieB.h * 0.28} x2={pcieB.x + pcieB.w} y2={pcieB.y + pcieB.h * 0.28} stroke={pcieAccent} strokeWidth={Math.max(2, 3.5 * s)} opacity={0.75} />
        <line x1={pcieB.x} y1={pcieB.y + pcieB.h * 0.72} x2={pcieB.x + pcieB.w} y2={pcieB.y + pcieB.h * 0.72} stroke={CK.ok} strokeWidth={Math.max(2, 3.5 * s)} opacity={0.75} />
        <rect
          x={pcieB.x + 12 * s}
          y={pcieB.y + pcieB.h * 0.34}
          width={Math.max(0, pcieB.w - 24 * s)}
          height={Math.max(0, pcieB.h * 0.32)}
          rx={Math.max(4, 10 * s)}
          fill={C.bg}
          stroke={pcieAccent}
          strokeWidth={Math.max(1, 1.8 * s)}
        />
        <text x={pcieB.x + pcieB.w / 2} y={pcieB.y + pcieB.h * 0.5} textAnchor="middle" dominantBaseline="central" fontSize={fs(16)} fontWeight={700} fill={pcieAccent} fontFamily={MONO}>
          {(labels.pcie ?? DEFAULT_TOPO_LABELS.pcie)[0]}
        </text>
        <text x={pcieB.x + pcieB.w / 2} y={pcieB.y + pcieB.h + 18 * s} textAnchor="middle" fontSize={fs(14)} fill={C.muted} fontFamily={MONO}>
          {(labels.pcie ?? DEFAULT_TOPO_LABELS.pcie)[1]}
        </text>
      </g>

      {drawBox("cn106", CK.card, { fill: "#141826", topLabel: true })}
      {drawBox("pem", CK.card)}
      {drawBox("arm", CK.card)}
      {drawBox("cL3", CK.ok)}
      {drawBox("dpi", CK.dma)}
      {drawBox("cDram", "#53658c")}
      {drawBox("nix", CK.peer)}

      {showPeer && (
        <>
          <g opacity={(1 - (dim.wire ?? 0)) * opacity}>
            <line x1={wireB.x} y1={wireB.y + wireB.h * 0.32} x2={wireB.x + wireB.w} y2={wireB.y + wireB.h * 0.32} stroke={wireAccent} strokeWidth={Math.max(2, 4 * s)} opacity={0.8} />
            <line x1={wireB.x} y1={wireB.y + wireB.h * 0.68} x2={wireB.x + wireB.w} y2={wireB.y + wireB.h * 0.68} stroke={wireAccent} strokeWidth={Math.max(2, 4 * s)} opacity={0.8} />
            <rect
              x={wireB.x + 14 * s}
              y={wireB.y + wireB.h * 0.34}
              width={Math.max(0, wireB.w - 28 * s)}
              height={Math.max(0, wireB.h * 0.32)}
              rx={Math.max(4, 10 * s)}
              fill={C.bg}
              stroke={wireAccent}
              strokeWidth={Math.max(1, 1.8 * s)}
            />
            <text x={wireB.x + wireB.w / 2} y={wireB.y + wireB.h * 0.5} textAnchor="middle" dominantBaseline="central" fontSize={fs(16)} fontWeight={700} fill={wireAccent} fontFamily={MONO}>
              {(labels.wire ?? DEFAULT_TOPO_LABELS.wire)[0]}
            </text>
            <text x={wireB.x + wireB.w / 2} y={wireB.y + wireB.h + 18 * s} textAnchor="middle" fontSize={fs(14)} fill={C.muted} fontFamily={MONO}>
              {(labels.wire ?? DEFAULT_TOPO_LABELS.wire)[1]}
            </text>
          </g>
          {drawBox("mercury", CK.peer, { fill: "#0e1d26", topLabel: true })}
          <g opacity={(1 - (dim.mercury ?? 0)) * opacity}>
            <rect
              x={g.r.mercury.x + 28 * s}
              y={g.r.mercury.y + 84 * s}
              width={Math.max(0, g.r.mercury.w - 56 * s)}
              height={Math.max(0, 128 * s)}
              rx={Math.max(4, 12 * s)}
              fill={C.panel}
              stroke={CK.peer}
              strokeWidth={Math.max(1, 2 * s)}
            />
            <text x={g.r.mercury.x + g.r.mercury.w / 2} y={g.r.mercury.y + 134 * s} textAnchor="middle" fontSize={fs(21)} fontWeight={700} fill={CK.peer} fontFamily={FONT}>
              ConnectX-5
            </text>
            <text x={g.r.mercury.x + g.r.mercury.w / 2} y={g.r.mercury.y + 168 * s} textAnchor="middle" fontSize={fs(15)} fill={C.muted} fontFamily={MONO}>
              mlx5_2 · Gen3 ×16
            </text>
            <rect
              x={g.r.mercury.x + 28 * s}
              y={g.r.mercury.y + 244 * s}
              width={Math.max(0, g.r.mercury.w - 56 * s)}
              height={Math.max(0, 150 * s)}
              rx={Math.max(4, 12 * s)}
              fill={C.panel}
              stroke={C.line}
              strokeWidth={Math.max(1, 2 * s)}
            />
            <text x={g.r.mercury.x + g.r.mercury.w / 2} y={g.r.mercury.y + 304 * s} textAnchor="middle" fontSize={fs(20)} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Wire Verifier
            </text>
            <text x={g.r.mercury.x + g.r.mercury.w / 2} y={g.r.mercury.y + 340 * s} textAnchor="middle" fontSize={fs(15)} fill={C.muted} fontFamily={MONO}>
              PHY + FCS + 16C UD
            </text>
          </g>
        </>
      )}
    </g>
  );
}

/** Scale-safe SVG "[INF]" badge marking an inferred/projected value. */
export function InfBadge({ x, y, opacity = 1, s = 1 }: { x: number; y: number; opacity?: number; s?: number }) {
  return (
    <g transform={`translate(${x},${y}) scale(${s})`} opacity={opacity}>
      <rect x={0} y={0} width={66} height={28} rx={6} fill="#5eead420" stroke="#5eead4" strokeWidth={1.6} />
      <text x={33} y={14} textAnchor="middle" dominantBaseline="central" fontSize={16} fontWeight={800} fill="#5eead4" fontFamily={MONO}>
        [INF]
      </text>
    </g>
  );
}

/** SVG horizontal bar row (animated width + crisp label + right value). */
export interface HBarRow {
  label: string;
  sub?: string;
  value: number;
  valueText: string;
  color: string;
  k?: number;
  tag?: string;
}

export function HBarChart({
  x,
  y,
  w,
  labelW = 260,
  barH = 38,
  gap = 18,
  max,
  rows,
  refLine,
  opacity = 1,
}: {
  x: number;
  y: number;
  w: number;
  labelW?: number;
  barH?: number;
  gap?: number;
  max: number;
  rows: HBarRow[];
  refLine?: { value: number; label: string; color?: string };
  opacity?: number;
}) {
  const trackX = x + labelW;
  const trackW = Math.max(40, w - labelW - 150);
  const totalH = rows.length * (barH + gap) - gap;

  return (
    <g opacity={opacity}>
      {refLine && (
        <g>
          <line
            x1={trackX + clamp01(refLine.value / max) * trackW}
            y1={y - 14}
            x2={trackX + clamp01(refLine.value / max) * trackW}
            y2={y + totalH + 10}
            stroke={refLine.color ?? C.yellow}
            strokeWidth={2.5}
            strokeDasharray="8 6"
          />
          <text
            x={trackX + clamp01(refLine.value / max) * trackW}
            y={y - 22}
            textAnchor="middle"
            fontSize={19}
            fontWeight={700}
            fill={refLine.color ?? C.yellow}
            fontFamily={MONO}
          >
            {refLine.label}
          </text>
        </g>
      )}
      {rows.map((row, i) => {
        const ry = y + i * (barH + gap);
        const kk = clamp01(row.k ?? 1);
        const bw = Math.max(0, clamp01(row.value / max) * trackW * kk);
        return (
          <g key={i} opacity={Math.max(0.08, kk)}>
            <text
              x={trackX - 16}
              y={ry + barH / 2 - (row.sub ? 8 : 0)}
              textAnchor="end"
              dominantBaseline="central"
              fontSize={22}
              fontWeight={700}
              fill={C.text}
              fontFamily={FONT}
            >
              {row.label}
            </text>
            {row.sub && (
              <text x={trackX - 16} y={ry + barH / 2 + 14} textAnchor="end" dominantBaseline="central" fontSize={16} fill={C.muted} fontFamily={MONO}>
                {row.sub}
              </text>
            )}
            <rect x={trackX} y={ry} width={trackW} height={barH} rx={8} fill="#141b2e" stroke={C.line} strokeWidth={1.2} />
            <rect x={trackX} y={ry} width={bw} height={barH} rx={8} fill={row.color} opacity={0.9} />
            <text
              x={trackX + bw + 14}
              y={ry + barH / 2}
              dominantBaseline="central"
              fontSize={23}
              fontWeight={800}
              fill={row.color}
              fontFamily={MONO}
              opacity={kk > 0.25 ? clamp01((kk - 0.25) / 0.4) : 0}
            >
              {row.valueText}
            </text>
            {row.tag && kk > 0.5 && (
              <text
                x={trackX + trackW - 10}
                y={ry + barH / 2}
                textAnchor="end"
                dominantBaseline="central"
                fontSize={17}
                fontWeight={700}
                fill={C.muted}
                fontFamily={MONO}
              >
                {row.tag}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
}

/** Stacked horizontal bar (SVG) for latency or cycle decomposition. */
export interface SplitSeg {
  label: string;
  value: number;
  color: string;
}

export function SplitBar({
  x,
  y,
  w,
  h = 48,
  max,
  segs,
  k = 1,
  totalText,
  totalColor = C.text,
  opacity = 1,
}: {
  x: number;
  y: number;
  w: number;
  h?: number;
  max: number;
  segs: SplitSeg[];
  k?: number;
  totalText?: string;
  totalColor?: string;
  opacity?: number;
}) {
  const kk = clamp01(k);
  let cur = 0;
  return (
    <g opacity={opacity}>
      <rect x={x} y={y} width={w} height={h} rx={10} fill="#141b2e" stroke={C.line} strokeWidth={1.4} />
      {segs.map((seg, i) => {
        const sw = Math.max(0, (seg.value / max) * w * kk);
        const sx = x + cur;
        cur += sw;
        return (
          <g key={i}>
            <rect x={sx} y={y} width={sw} height={h} rx={i === 0 ? 10 : 3} fill={seg.color} opacity={0.92} />
            {sw > 78 && (
              <text
                x={sx + sw / 2}
                y={y + h / 2}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={19}
                fontWeight={800}
                fill={C.bg}
                fontFamily={MONO}
              >
                {seg.label}
              </text>
            )}
          </g>
        );
      })}
      {totalText && (
        <text x={x + cur + 14} y={y + h / 2} dominantBaseline="central" fontSize={24} fontWeight={800} fill={totalColor} fontFamily={MONO}>
          {totalText}
        </text>
      )}
    </g>
  );
}

/** SVG ring buffer strip with cell states and optional credit window highlight. */
export function RingStrip({
  x,
  y,
  n,
  cellW = 40,
  cellH = 44,
  gap = 6,
  state,
  label,
  highlightRange,
  highlightColor = C.cyan,
  opacity = 1,
}: {
  x: number;
  y: number;
  n: number;
  cellW?: number;
  cellH?: number;
  gap?: number;
  state: (i: number) => "empty" | "hot" | "inflight" | "trap" | "dma" | "ok";
  label?: string;
  highlightRange?: [number, number];
  highlightColor?: string;
  opacity?: number;
}) {
  const styles = {
    empty:    { fill: "#11182c", stroke: C.line },
    hot:      { fill: `${CK.push}33`, stroke: CK.push },
    inflight: { fill: `${CK.cxl}35`, stroke: CK.cxl },
    trap:     { fill: `${CK.trap}38`, stroke: CK.trap },
    dma:      { fill: `${CK.dma}35`, stroke: CK.dma },
    ok:       { fill: `${CK.ok}33`, stroke: CK.ok },
  };
  return (
    <g opacity={opacity}>
      {label && (
        <text x={x} y={y - 14} fontSize={21} fontWeight={700} fill={C.text} fontFamily={FONT}>
          {label}
        </text>
      )}
      {Array.from({ length: n }).map((_, i) => {
        const st = styles[state(i)];
        const cx = x + i * (cellW + gap);
        return (
          <rect
            key={i}
            x={cx}
            y={y}
            width={cellW}
            height={cellH}
            rx={6}
            fill={st.fill}
            stroke={st.stroke}
            strokeWidth={2}
          />
        );
      })}
      {highlightRange && (
        <rect
          x={x + highlightRange[0] * (cellW + gap) - 5}
          y={y - 5}
          width={Math.max(0, (highlightRange[1] - highlightRange[0] + 1) * (cellW + gap) - gap + 10)}
          height={cellH + 10}
          rx={10}
          fill="none"
          stroke={highlightColor}
          strokeWidth={3}
          strokeDasharray="7 5"
        />
      )}
    </g>
  );
}

/** HTML Hero metric card (place OUTSIDE <Stage>). */
export function HeroStat({
  x,
  y,
  w = 340,
  h = 152,
  value,
  label,
  sub,
  color = C.cyan,
  opacity = 1,
  dy = 0,
}: {
  x: number;
  y: number;
  w?: number;
  h?: number;
  value: string;
  label: string;
  sub?: string;
  color?: string;
  opacity?: number;
  dy?: number;
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
        transform: `translateY(${dy}px)`,
        background: "linear-gradient(155deg, rgba(20,27,46,0.96), rgba(11,16,32,0.94))",
        border: `2px solid ${color}`,
        borderRadius: 18,
        padding: "16px 22px",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        boxShadow: `0 12px 32px rgba(0,0,0,0.45), inset 0 1px 0 ${color}40`,
      }}
    >
      <div style={{ fontSize: 52, fontWeight: 800, color, fontFamily: MONO, lineHeight: 1.05 }}>{value}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: C.text, marginTop: 8, lineHeight: 1.2 }}>{label}</div>
      {sub && <div style={{ fontSize: 17, color: C.muted, fontFamily: MONO, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

/** HTML compact verdict / takeaway row (place OUTSIDE <Stage>). */
export function VerdictRow({
  x,
  y,
  w,
  badge,
  badgeColor,
  title,
  detail,
  opacity = 1,
  dy = 0,
}: {
  x: number;
  y: number;
  w: number;
  badge: string;
  badgeColor: string;
  title: string;
  detail: string;
  opacity?: number;
  dy?: number;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        opacity,
        transform: `translateY(${dy}px)`,
        background: "rgba(20,27,46,0.92)",
        border: `1.5px solid ${badgeColor}66`,
        borderLeft: `6px solid ${badgeColor}`,
        borderRadius: 14,
        padding: "14px 20px",
        boxSizing: "border-box",
        display: "flex",
        alignItems: "center",
        gap: 18,
      }}
    >
      <div
        style={{
          minWidth: 112,
          textAlign: "center",
          fontSize: 19,
          fontWeight: 800,
          color: badgeColor,
          fontFamily: MONO,
          background: `${badgeColor}1a`,
          border: `1.5px solid ${badgeColor}88`,
          borderRadius: 10,
          padding: "7px 12px",
        }}
      >
        {badge}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 24, fontWeight: 700, color: C.text, lineHeight: 1.25 }}>{title}</div>
        <div style={{ fontSize: 20, color: C.muted, marginTop: 4, lineHeight: 1.3 }}>{detail}</div>
      </div>
    </div>
  );
}

/** HTML Advisor Question Banner — big, spacious opening hook for a chapter (place OUTSIDE <Stage>). */
export function QuestionBanner({
  num,
  question,
  sub,
  color = C.cyan,
  y = 136,
  opacity = 1,
  dy = 0,
}: {
  num: string;
  question: string;
  sub?: string;
  color?: string;
  y?: number;
  opacity?: number;
  dy?: number;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: 90,
        top: y,
        width: 1740,
        opacity,
        transform: `translateY(${dy}px)`,
        background: "linear-gradient(135deg, rgba(20,27,46,0.96), rgba(11,16,32,0.94))",
        border: `2px solid ${color}aa`,
        borderLeft: `10px solid ${color}`,
        borderRadius: 20,
        padding: "26px 34px",
        boxSizing: "border-box",
        boxShadow: "0 18px 44px rgba(0,0,0,0.48)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
        <span
          style={{
            fontSize: 21,
            fontWeight: 800,
            color: C.bg,
            background: color,
            borderRadius: 10,
            padding: "6px 16px",
            fontFamily: MONO,
            letterSpacing: 0.6,
            whiteSpace: "nowrap",
          }}
        >
          {num}
        </span>
        <span style={{ fontSize: 34, fontWeight: 800, color: C.text, lineHeight: 1.25 }}>{question}</span>
      </div>
      {sub && (
        <div style={{ fontSize: 23, color: C.muted, marginTop: 12, marginLeft: 4, lineHeight: 1.35 }}>{sub}</div>
      )}
    </div>
  );
}

/** HTML concise one-line Honest Boundary strip (place OUTSIDE <Stage>). */
export function BoundaryBar({
  x = 80,
  y = 776,
  w = 1760,
  badge = "HONEST BOUNDARY",
  text,
  color = C.yellow,
  opacity = 1,
  dy = 0,
}: {
  x?: number;
  y?: number;
  w?: number;
  badge?: string;
  text: string;
  color?: string;
  opacity?: number;
  dy?: number;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        opacity,
        transform: `translateY(${dy}px)`,
        background: "rgba(20,27,46,0.95)",
        border: `1.5px solid ${color}88`,
        borderLeft: `7px solid ${color}`,
        borderRadius: 14,
        padding: "14px 22px",
        boxSizing: "border-box",
        display: "flex",
        alignItems: "center",
        gap: 18,
      }}
    >
      <span
        style={{
          fontSize: 17,
          fontWeight: 800,
          color,
          fontFamily: MONO,
          background: `${color}1c`,
          border: `1.5px solid ${color}80`,
          borderRadius: 8,
          padding: "5px 12px",
          whiteSpace: "nowrap",
        }}
      >
        {badge}
      </span>
      <span style={{ fontSize: 22, fontWeight: 600, color: C.text, lineHeight: 1.3 }}>{text}</span>
    </div>
  );
}

/** Smooth horizontal slide / fade helper for HTML overlays. */
export const slideIn = (k: number, dy = 20): React.CSSProperties => ({
  opacity: clamp01(k),
  transform: `translateY(${lerp(dy, 0, clamp01(k))}px)`,
});
