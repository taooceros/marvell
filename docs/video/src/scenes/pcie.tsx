/**
 * Shared running example: the host ⇄ CN106xx card datapath, used by every scene that draws hardware.
 * Parent-written; scene agents import it and must not edit it.
 *
 * Layout (base coords, s = 1): host panel x 60–700 (CPU core on top, host memory with SDP RX ring + DMA reply ring
 * below), PCIe link x 700–1220 (4 lanes), card panel x 1220–1860 (PEM strip on the left, then a column of SDP, DPI,
 * Arm cores, card DRAM with the request-cell ring and the control cell). All flows are Manhattan polylines that
 * cross the link on a lane, so they never cut through a block.
 *
 *   const g = dpGeom(0.8, 40, 60);             // scale + offset (all rects/points come back transformed)
 *   const r = routes(g);                        // named polylines in the same coords
 *   <Datapath g={g} hl={{ sdp: K.uc }} dimOthers={0.7} show={{ replyRing: 0 }} cores={4} activeCores={2} />
 *   <Packet pts={r.inline} k={prog(t, T[2], 1.2)} color={K.req} />
 */
import React from "react";
import { C, FONT, K, MONO, Pt } from "../lib";

export type Rect = { x: number; y: number; w: number; h: number };
export type BlockId =
  | "host"
  | "core"
  | "hmem"
  | "sdpRing"
  | "replyRing"
  | "link"
  | "card"
  | "pem"
  | "sdp"
  | "dpi"
  | "cores"
  | "cmem"
  | "reqRing"
  | "ctrl";
export type RingId = "sdpRing" | "replyRing" | "reqRing";

const BASE: Record<BlockId, Rect> = {
  host: { x: 60, y: 150, w: 640, h: 700 },
  core: { x: 100, y: 230, w: 320, h: 120 },
  hmem: { x: 100, y: 450, w: 560, h: 370 },
  sdpRing: { x: 125, y: 535, w: 510, h: 95 },
  replyRing: { x: 125, y: 690, w: 510, h: 95 },
  link: { x: 700, y: 425, w: 520, h: 110 },
  card: { x: 1220, y: 150, w: 640, h: 700 },
  pem: { x: 1245, y: 215, w: 90, h: 605 },
  sdp: { x: 1380, y: 215, w: 450, h: 80 },
  dpi: { x: 1380, y: 310, w: 450, h: 80 },
  cores: { x: 1380, y: 405, w: 450, h: 95 },
  cmem: { x: 1380, y: 515, w: 450, h: 305 },
  reqRing: { x: 1400, y: 575, w: 410, h: 90 },
  ctrl: { x: 1400, y: 705, w: 410, h: 90 },
};

/** Default labels [main, sub]. Override per scene with `labels`. */
export const DP_LABEL: Record<BlockId, [string, string?]> = {
  host: ["HOST · saturn", "Xeon Gold 6438M"],
  core: ["CPU core", "x86 · DPDK lcore 40"],
  hmem: ["Host memory", "DPDK hugepages"],
  sdpRing: ["SDP RX ring", "frames + info words"],
  replyRing: ["Reply ring", "64 B reply cells"],
  link: ["PCIe Gen5 x4", undefined],
  card: ["CARD · CN106xx", "OCTEON 10 · Arm @ 2.0 GHz"],
  pem: ["PEM", "PCIe endpoint"],
  sdp: ["SDP", "packet engine · doorbells · OUT_CNTS"],
  dpi: ["DPI", "DMA engine"],
  cores: ["Arm cores", "poll loop"],
  cmem: ["Card DRAM", "hugepage"],
  reqRing: ["Request cells", "BAR4 window 15"],
  ctrl: ["Control cell", "READY · cores · pull reply"],
};

/** Ring cell count drawn inside each ring block. */
export const RING_CELLS = 10;

/** PCIe lane y positions (base). Lane 0 = request direction, lane 3 = reply direction. */
const LANES = [445, 468, 492, 515];
const HOST_BUS = 672; // vertical bus inside the host panel (right of every host block)
const CARD_BUS = 1357; // vertical bus between PEM and the card column
const CARD_RIGHT_BUS = 1846; // vertical bus right of the card column (core → SDP / DPI)

export type DpGeom = {
  s: number;
  dx: number;
  dy: number;
  /** Map a base-coordinate point to scene coordinates. */
  pt: (p: Pt) => Pt;
  /** Transformed rects. */
  r: Record<BlockId, Rect>;
  /** Centre of a block (scene coords). */
  c: (id: BlockId) => Pt;
  /** Font size scaled (never below 14). */
  fs: (px: number) => number;
};

export function dpGeom(s = 1, dx = 0, dy = 0): DpGeom {
  const pt = (p: Pt): Pt => [dx + p[0] * s, dy + p[1] * s];
  const r = Object.fromEntries(
    (Object.keys(BASE) as BlockId[]).map((k) => {
      const b = BASE[k];
      return [k, { x: dx + b.x * s, y: dy + b.y * s, w: Math.max(0, b.w * s), h: Math.max(0, b.h * s) }];
    }),
  ) as Record<BlockId, Rect>;
  return {
    s,
    dx,
    dy,
    pt,
    r,
    c: (id) => [r[id].x + r[id].w / 2, r[id].y + r[id].h / 2],
    fs: (px) => Math.max(14, px * s),
  };
}

/** Centre of ring cell i (0..RING_CELLS-1, left → right) in scene coords. */
export function ringCell(g: DpGeom, id: RingId, i: number): Pt {
  const b = BASE[id];
  const cw = (b.w - 20) / RING_CELLS;
  return g.pt([b.x + 10 + cw * (i + 0.5), b.y + b.h - 30]);
}

// ---------------------------------------------------------------- routes (base coords, then transformed)
const right = (id: BlockId, dyFrac = 0.5): Pt => [BASE[id].x + BASE[id].w, BASE[id].y + BASE[id].h * dyFrac];
const left = (id: BlockId, dyFrac = 0.5): Pt => [BASE[id].x, BASE[id].y + BASE[id].h * dyFrac];

/** Host block right edge → host bus → lane → PEM → card bus → card block left edge. */
function hostToCard(from: BlockId, to: BlockId, lane: number, off = 0, fromFrac = 0.5, toFrac = 0.5): Pt[] {
  const a = right(from, fromFrac);
  const b = left(to, toFrac);
  const y = LANES[lane];
  return [a, [HOST_BUS + off, a[1]], [HOST_BUS + off, y], [CARD_BUS + off, y], [CARD_BUS + off, b[1]], b];
}
const rev = (p: Pt[]) => [...p].reverse();

const BASE_ROUTES = {
  /** BAR4 inline write: CPU core → request cells (MOVDIR64B). */
  inline: hostToCard("core", "reqRing", 0, 0, 0.5, 0.5),
  /** SDP doorbell: CPU core → SDP (8 B uncached store). */
  doorbell: hostToCard("core", "sdp", 0, 0, 0.35),
  /** SDP descriptor fetch / request frame into the card (stock path, host → SDP). */
  sdpIn: hostToCard("hmem", "sdp", 1, 6, 0.2, 0.7),
  /** SDP reply: SDP → host SDP RX ring. */
  sdpReply: rev(hostToCard("sdpRing", "sdp", 3, 12, 0.5, 0.7)),
  /** OUT_CNTS MMIO read: request leg CPU core → SDP register, then completion back on lane 2. */
  mmioOut: hostToCard("core", "sdp", 1, 4, 0.65, 0.3),
  mmioBack: rev(hostToCard("core", "sdp", 2, 8, 0.8, 0.5)),
  /** DPI DMA reply: DPI → host DMA reply ring. */
  dmaReply: rev(hostToCard("replyRing", "dpi", 3, 12, 0.5, 0.5)),
  /** Card core store: Arm cores → PEM outbound region → host reply ring (no DPI). */
  storeReply: rev(hostToCard("replyRing", "cores", 3, 18, 0.5, 0.5)),
  /** Pull: CPU core UC read → control cell, completion back on lane 2. */
  pullOut: hostToCard("core", "ctrl", 1, 4, 0.65, 0.4),
  pullBack: rev(hostToCard("core", "ctrl", 2, 8, 0.8, 0.6)),
  /** Card core polls the request ring (short vertical). */
  cardPoll: [
    [BASE.cores.x + BASE.cores.w * 0.5, BASE.cores.y + BASE.cores.h],
    [BASE.cores.x + BASE.cores.w * 0.5, BASE.reqRing.y],
  ] as Pt[],
  /** Card core rings the SDP / DPI doorbell (right-hand bus). */
  coreToSdp: [right("cores", 0.5), [CARD_RIGHT_BUS, BASE.cores.y + BASE.cores.h / 2], [CARD_RIGHT_BUS, BASE.sdp.y + BASE.sdp.h / 2], right("sdp")] as Pt[],
  coreToDpi: [right("cores", 0.5), [CARD_RIGHT_BUS, BASE.cores.y + BASE.cores.h / 2], [CARD_RIGHT_BUS, BASE.dpi.y + BASE.dpi.h / 2], right("dpi")] as Pt[],
  /** Card core writes the control cell (pull reply). */
  coreToCtrl: [
    [BASE.cores.x + BASE.cores.w * 0.85, BASE.cores.y + BASE.cores.h],
    [BASE.cores.x + BASE.cores.w * 0.85, BASE.ctrl.y],
  ] as Pt[],
  /** Host CPU core polls its own memory (short vertical into a ring). */
  hostPollSdp: [
    [BASE.core.x + BASE.core.w * 0.75, BASE.core.y + BASE.core.h],
    [BASE.core.x + BASE.core.w * 0.75, BASE.sdpRing.y],
  ] as Pt[],
  /** Down the left margin (x 82) so it does not cross the SDP RX ring. */
  hostPollReply: [
    left("core", 0.75),
    [82, BASE.core.y + BASE.core.h * 0.75],
    [82, BASE.replyRing.y + BASE.replyRing.h / 2],
    left("replyRing"),
  ] as Pt[],
};
export type RouteId = keyof typeof BASE_ROUTES;

/** Named polylines in scene coords for geometry g (feed to lib `Packet` / `Link` / `partial`). */
export function routes(g: DpGeom): Record<RouteId, Pt[]> {
  return Object.fromEntries(
    (Object.keys(BASE_ROUTES) as RouteId[]).map((k) => [k, BASE_ROUTES[k].map(g.pt)]),
  ) as Record<RouteId, Pt[]>;
}

// ---------------------------------------------------------------- drawing
const PANEL: BlockId[] = ["host", "card"];
const RINGS: RingId[] = ["sdpRing", "replyRing", "reqRing"];
const CONTAINERS: BlockId[] = ["hmem", "cmem"];

export type DatapathProps = {
  g: DpGeom;
  /** Per-block opacity multiplier (0 hides). Default 1 for every block. */
  show?: Partial<Record<BlockId, number>>;
  /** Highlight colour per block: coloured border + glow + tinted fill. */
  hl?: Partial<Record<BlockId, string>>;
  /** 0..1: fade blocks that are NOT in `hl` (and not panels) towards 0.35 opacity. */
  dimOthers?: number;
  /** Label overrides [main, sub]. */
  labels?: Partial<Record<BlockId, [string, string?]>>;
  /** Ring cell fill colour (undefined = empty cell). */
  cellFill?: Partial<Record<RingId, (i: number) => string | undefined>>;
  /** Draw this many core squares inside "cores" (1..4); activeCores of them lit in CORE colours. */
  cores?: number;
  activeCores?: number;
  coreColors?: string[];
  /** Global opacity. */
  opacity?: number;
  /** Lane glow 0..1 (e.g. traffic on the link). */
  linkGlow?: number;
};

export function Datapath({
  g,
  show = {},
  hl = {},
  dimOthers = 0,
  labels = {},
  cellFill = {},
  cores = 0,
  activeCores = 0,
  coreColors = [C.cyan, C.green, C.yellow, C.pink],
  opacity = 1,
  linkGlow = 0,
}: DatapathProps) {
  const op = (id: BlockId) => {
    const base = show[id] ?? 1;
    if (PANEL.includes(id) || id === "link") return base;
    return hl[id] ? base : base * (1 - 0.65 * dimOthers);
  };
  const lab = (id: BlockId) => labels[id] ?? DP_LABEL[id];
  const block = (id: BlockId, kind: "panel" | "container" | "block" | "ring") => {
    const r = g.r[id];
    const o = op(id);
    if (o <= 0.001) return null;
    const color = hl[id];
    const sideColor = ["host", "core", "hmem", "sdpRing", "replyRing"].includes(id) ? K.host : K.card;
    const [main, sub] = lab(id);
    const rx = 14 * g.s;
    if (kind === "panel") {
      return (
        <g key={id} opacity={o}>
          <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={22 * g.s} fill="rgba(20,27,46,0.55)" stroke={sideColor} strokeWidth={2.5} strokeDasharray={`${10 * g.s} ${8 * g.s}`} opacity={0.9} />
          <text x={r.x + 22 * g.s} y={r.y + 38 * g.s} fontSize={g.fs(26)} fontWeight={800} fill={sideColor} fontFamily={FONT} letterSpacing={1}>
            {main}
          </text>
          {sub && (
            <text x={r.x + r.w - 22 * g.s} y={r.y + 38 * g.s} fontSize={g.fs(18)} fill={C.muted} fontFamily={MONO} textAnchor="end">
              {sub}
            </text>
          )}
        </g>
      );
    }
    if (kind === "container") {
      return (
        <g key={id} opacity={o}>
          <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={rx} fill={color ? `${color}14` : "rgba(11,16,32,0.6)"} stroke={color ?? C.line} strokeWidth={color ? 3 : 2} filter={color ? "url(#glow)" : undefined} />
          <text x={r.x + 18 * g.s} y={r.y + 34 * g.s} fontSize={g.fs(24)} fontWeight={700} fill={C.text} fontFamily={FONT}>
            {main}
          </text>
          {sub && (
            <text x={r.x + r.w - 18 * g.s} y={r.y + 34 * g.s} fontSize={g.fs(17)} fill={C.muted} fontFamily={MONO} textAnchor="end">
              {sub}
            </text>
          )}
        </g>
      );
    }
    if (kind === "ring") {
      const fill = cellFill[id as RingId];
      const cw = (r.w - 20 * g.s) / RING_CELLS;
      return (
        <g key={id} opacity={o}>
          <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={rx * 0.7} fill={color ? `${color}1f` : C.panel} stroke={color ?? sideColor} strokeWidth={color ? 3 : 2} filter={color ? "url(#glow)" : undefined} />
          <text x={r.x + 14 * g.s} y={r.y + 27 * g.s} fontSize={g.fs(21)} fontWeight={700} fill={C.text} fontFamily={FONT}>
            {main}
          </text>
          {sub && (
            <text x={r.x + r.w - 14 * g.s} y={r.y + 27 * g.s} fontSize={g.fs(16)} fill={C.muted} fontFamily={MONO} textAnchor="end">
              {sub}
            </text>
          )}
          {Array.from({ length: RING_CELLS }, (_, i) => {
            const f = fill?.(i);
            return (
              <rect
                key={i}
                x={r.x + 10 * g.s + cw * i + 3 * g.s}
                y={r.y + r.h - 48 * g.s}
                width={Math.max(0, cw - 6 * g.s)}
                height={Math.max(0, 36 * g.s)}
                rx={5 * g.s}
                fill={f ?? "rgba(139,149,173,0.08)"}
                stroke={f ?? C.dim}
                strokeWidth={1.5}
              />
            );
          })}
        </g>
      );
    }
    // plain block
    const vertical = id === "pem";
    return (
      <g key={id} opacity={o}>
        <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={rx} fill={color ? `${color}24` : C.panel} stroke={color ?? sideColor} strokeWidth={color ? 3.5 : 2} filter={color ? "url(#glow)" : undefined} />
        {vertical ? (
          <g transform={`translate(${r.x + r.w / 2} ${r.y + r.h * 0.8}) rotate(-90)`}>
            <text y={-6 * g.s} textAnchor="middle" fontSize={g.fs(26)} fontWeight={800} fill={C.text} fontFamily={FONT}>
              {main}
            </text>
            {sub && (
              <text y={22 * g.s} textAnchor="middle" fontSize={g.fs(17)} fill={C.muted} fontFamily={MONO}>
                {sub}
              </text>
            )}
          </g>
        ) : (
          <>
            <text x={r.x + 20 * g.s} y={r.y + (sub ? r.h * 0.44 : r.h * 0.5)} dominantBaseline="central" fontSize={g.fs(26)} fontWeight={800} fill={color ?? C.text} fontFamily={FONT}>
              {main}
            </text>
            {sub && (
              <text x={r.x + 20 * g.s} y={r.y + r.h * 0.76} dominantBaseline="central" fontSize={g.fs(17)} fill={C.muted} fontFamily={MONO}>
                {sub}
              </text>
            )}
          </>
        )}
      </g>
    );
  };

  const L = g.r.link;
  const lanes = LANES.map((y) => g.pt([0, y])[1]);
  const coreSquares = () => {
    if (cores <= 0 || op("cores") <= 0.001) return null;
    const r = g.r.cores;
    const sz = 46 * g.s;
    const gap = 12 * g.s;
    const x0 = r.x + r.w - 20 * g.s - cores * sz - (cores - 1) * gap;
    return (
      <g opacity={op("cores")}>
        {Array.from({ length: cores }, (_, i) => {
          const on = i < activeCores;
          const col = coreColors[i % coreColors.length];
          return (
            <g key={i}>
              <rect x={x0 + i * (sz + gap)} y={r.y + (r.h - sz) / 2} width={sz} height={sz} rx={8 * g.s} fill={on ? `${col}33` : "transparent"} stroke={on ? col : C.dim} strokeWidth={on ? 3 : 1.5} />
              <text x={x0 + i * (sz + gap) + sz / 2} y={r.y + r.h / 2} textAnchor="middle" dominantBaseline="central" fontSize={g.fs(18)} fontFamily={MONO} fill={on ? col : C.dim}>
                {i}
              </text>
            </g>
          );
        })}
      </g>
    );
  };

  return (
    <g opacity={opacity}>
      {PANEL.map((id) => block(id, "panel"))}
      {/* PCIe link */}
      <g opacity={op("link")}>
        <rect x={L.x} y={L.y} width={L.w} height={L.h} fill={hl.link ? `${hl.link}14` : "transparent"} />
        {/* No SVG filter here: a horizontal line has a zero-height bbox, so #glow would erase it. Halo = wide faint stroke. */}
        {lanes.map((y, i) => (
          <g key={i}>
            {linkGlow > 0 && <line x1={L.x} y1={y} x2={L.x + L.w} y2={y} stroke={hl.link ?? K.link} strokeWidth={12} opacity={0.22 * linkGlow} strokeLinecap="round" />}
            <line x1={L.x} y1={y} x2={L.x + L.w} y2={y} stroke={hl.link ?? K.link} strokeWidth={3} opacity={0.35 + 0.5 * linkGlow} />
          </g>
        ))}
        <text x={L.x + L.w / 2} y={L.y - 18 * g.s} textAnchor="middle" fontSize={g.fs(24)} fontWeight={700} fill={hl.link ?? K.link} fontFamily={FONT}>
          {lab("link")[0]}
        </text>
        {lab("link")[1] && (
          <text x={L.x + L.w / 2} y={L.y + L.h + 30 * g.s} textAnchor="middle" fontSize={g.fs(18)} fill={C.muted} fontFamily={MONO}>
            {lab("link")[1]}
          </text>
        )}
      </g>
      {CONTAINERS.map((id) => block(id, "container"))}
      {(["core", "pem", "sdp", "dpi", "cores"] as BlockId[]).map((id) => block(id, "block"))}
      {coreSquares()}
      {RINGS.map((id) => block(id, "ring"))}
      {block("ctrl", "block")}
    </g>
  );
}

/** Small "[INF]" tag (SVG) marking an inference; place it next to the inferred claim. */
export function InfTag({ x, y, opacity = 1, s = 1 }: { x: number; y: number; opacity?: number; s?: number }) {
  return (
    <g opacity={opacity}>
      <rect x={x} y={y} width={68 * s} height={30 * s} rx={8 * s} fill="rgba(94,234,212,0.12)" stroke={K.inf} strokeWidth={1.5} />
      <text x={x + 34 * s} y={y + 15 * s} textAnchor="middle" dominantBaseline="central" fontSize={Math.max(16, 20 * s)} fontFamily={MONO} fontWeight={700} fill={K.inf}>
        [INF]
      </text>
    </g>
  );
}

/**
 * Latency stack bar (SVG): segments laid left → right from x, each {label, us, color}; `px` = pixels per µs.
 * `k` (0..1) reveals segments in order. Labels sit inside when wide enough, else above.
 */
export function StackBar({
  x,
  y,
  h = 56,
  px,
  segs,
  k = 1,
  fontSize = 22,
}: {
  x: number;
  y: number;
  h?: number;
  px: number;
  segs: { label: string; us: number; color: string; inf?: boolean }[];
  k?: number;
  fontSize?: number;
}) {
  let cx = x;
  let outside = 0;
  const n = segs.length;
  return (
    <g>
      {segs.map((sg, i) => {
        const reveal = Math.min(1, Math.max(0, k * n - i));
        const w = Math.max(0, sg.us * px * reveal);
        const x0 = cx;
        cx += sg.us * px;
        if (reveal <= 0) return null;
        const inside = w > sg.label.length * fontSize * 0.55 + 16;
        // narrow segments: labels alternate above / below the bar so neighbours don't collide
        const below = !inside && outside++ % 2 === 1;
        return (
          <g key={i} opacity={Math.min(1, reveal * 1.5)}>
            <rect x={x0} y={y} width={w} height={h} rx={6} fill={`${sg.color}40`} stroke={sg.color} strokeWidth={2} strokeDasharray={sg.inf ? "8 6" : undefined} />
            <text
              x={x0 + (inside ? w / 2 : 4)}
              y={inside ? y + h / 2 : below ? y + h + 16 : y - 14}
              textAnchor={inside ? "middle" : "start"}
              dominantBaseline="central"
              fontSize={fontSize}
              fill={inside ? C.text : sg.color}
              fontFamily={FONT}
              fontWeight={600}
            >
              {sg.label}
            </text>
          </g>
        );
      })}
    </g>
  );
}
