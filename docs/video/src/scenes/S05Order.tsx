import React from "react";
import {
  Arrow,
  Axes,
  Bars,
  C,
  Cue,
  FONT,
  K,
  MONO,
  Packet,
  Pt,
  SceneDef,
  Stage,
  Txt,
  along,
  appear,
  clamp01,
  countUp,
  fade,
  lerp,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { Datapath, dpGeom, ringCell, routes } from "./pcie";

/** Colour with alpha (hex colours only). */
const hexA = (col: string, o: number) =>
  col + Math.round(clamp01(o) * 255).toString(16).padStart(2, "0");

/** 64 B inline cell: seq 8 B | send TSC 8 B | tag 8 B | payload pattern 32 B | seq copy 8 B (25 px per byte). */
const SEG = [
  { name: "head seq", bytes: 8, col: K.req, val: "8" },
  { name: "send TSC", bytes: 8, col: K.timer, val: "" },
  { name: "tag", bytes: 8, col: C.muted, val: "" },
  { name: "payload pattern", bytes: 32, col: C.blue, val: "" },
  { name: "tail seq", bytes: 8, col: K.req, val: "8" },
].map((s) => ({ ...s, w: s.bytes * 25 }));
const SEG_X = SEG.map((_, i) => SEG.slice(0, i).reduce((s, x) => s + x.w, 0));

/** Small cell strip used by the receiver-check cards. `torn` = half-written cell. */
function MiniCell({ x, y, w, h, torn, a }: { x: number; y: number; w: number; h: number; torn: boolean; a: number }) {
  const f = w / 1600;
  return (
    <g opacity={a}>
      {SEG.map((s, i) => {
        const sx = x + SEG_X[i] * f;
        const sw = s.w * f;
        const stale = torn && i === 4;
        const isPay = i === 3;
        return (
          <g key={i}>
            <rect x={sx} y={y} width={sw} height={h} fill={stale ? "url(#hatch)" : hexA(s.col, 0.22)} stroke={stale ? C.dim : s.col} strokeWidth={2} />
            {isPay && torn && <rect x={sx + sw * 0.45} y={y + 1} width={sw * 0.55 - 1} height={h - 2} fill="url(#hatch)" />}
            {(i === 0 || i === 4) && (
              <text x={sx + sw / 2} y={y + h / 2} textAnchor="middle" dominantBaseline="central" fontSize={stale ? 24 : 34} fontWeight={800} fill={stale ? C.muted : K.req} fontFamily={FONT}>
                {stale ? "old" : "8"}
              </text>
            )}
          </g>
        );
      })}
      <rect x={x} y={y} width={w} height={h} fill="none" stroke={C.text} strokeWidth={2.5} rx={6} />
    </g>
  );
}

// <cues>
const cues: Cue[] = [
  [0.60, 7.46, "MOVDIR64B stores are **weakly ordered**: without a fence, cell 8 may land before cell 7.", "Move dir stores are weakly ordered. Without a fence, cell eight may land in card memory before cell seven."], // 0
  [7.81, 16.19, "The textbook fix: an **SFENCE** after every cell. It works, but caps the host at **6.86 M msg/s** (W=128).", "The textbook fix is an S fence after every cell. It works, but it caps the host at six point eight six million messages per second."], // 1
  [16.54, 24.90, "Instead, the **receiver** checks: a cell counts only when **head seq = tail seq** and the pattern matches.", "Instead, the receiver checks. A cell counts only when its head sequence number equals the tail copy, and the payload pattern matches."], // 2
  [25.25, 34.77, "No fences: the host pushes **26.2 M msg/s** when the card skips the payload check. Torn cells seen: **0**.", "Without fences, the host pushes twenty-six million messages per second when the card skips the payload check. Torn cells seen across every run: zero."], // 3
  [35.12, 44.00, "Same idea as **destination-side ordering** (Liew et al., ASPLOS '26): pay for order where it's checked, not where it's sent.", "It's the same idea as destination side ordering, from Liew and colleagues at ASPLOS twenty-six: pay for ordering where it's checked, not where it's sent."], // 4
];
const DUR = 45.40; // <dur>
// </cues>

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);
  const g = dpGeom(0.62, 0, 30);
  const r = routes(g);

  // ------------------------------------------------ c0: cell 7 sent first, cell 8 lands first
  const dep7 = T[0] + 1.4;
  const land7 = dep7 + 2.8;
  const dep8 = T[0] + 2.0;
  const land8 = dep8 + 1.6;
  const toCell = (i: number): Pt[] => {
    const end = r.inline[r.inline.length - 1];
    const c = ringCell(g, "reqRing", i);
    return [...r.inline, [c[0], end[1]], c];
  };

  // ------------------------------------------------ c1: store · SFENCE · store · SFENCE …
  const P0 = T[1] + 0.3;
  const ST = 0.25;
  const FE = 1.6;
  const CY = ST + FE;
  const fenced = [7, 8, 9].map((seq, i) => ({ seq, s0: P0 + i * CY, dep: P0 + i * CY + ST, land: P0 + i * CY + ST + 1.3 }));

  const reset = 1 - prog(t, T[1], 0.4);
  const cellState = (i: number): [string, number] | null => {
    if (t < T[1] + 0.4) {
      if (i === 8) return [K.reorder, prog(t, land8, 0.3) * reset];
      if (i === 7) return [K.req, prog(t, land7, 0.3) * reset];
      return null;
    }
    const f = fenced.find((x) => x.seq === i);
    return f ? [K.req, prog(t, f.land, 0.3)] : null;
  };

  const dpOp = prog(t, T[0] - 0.4, 0.5) * (1 - prog(t, T[2], 0.5));
  const zoomOp = fade(t, T[0] + 0.3, T[2]);

  // zoom panel (card DRAM close-up, slots 6–9)
  const Z = { x: 1210, y: 135, w: 650, h: 425 };
  const cw = 128;
  const ch = 150;
  const cg = 22;
  const zx0 = Z.x + (Z.w - (4 * cw + 3 * cg)) / 2;
  const zy = 262;
  const zCellX = (i: number) => zx0 + (i - 6) * (cw + cg);
  const ordinal = ["1st", "2nd", "3rd"];
  const badge = (i: number): [string, string, number] | null => {
    if (t < T[1] + 0.4) {
      if (i === 8) return ["1st", K.reorder, prog(t, land8 + 0.2, 0.4) * reset];
      if (i === 7) return ["2nd", K.req, prog(t, land7 + 0.2, 0.4) * reset];
      return null;
    }
    const j = fenced.findIndex((x) => x.seq === i);
    return j >= 0 ? [ordinal[j], K.ok, prog(t, fenced[j].land + 0.2, 0.4) * (1 - prog(t, T[2], 0.5))] : null;
  };

  // c2 morph: zoom cell 8 → anatomy strip
  const m = prog(t, T[2] - 0.1, 0.8);
  const AX = 160;
  const AY = 200;
  const AW = 1600;
  const AH = 120;
  const c2Out = 1 - prog(t, T[3], 0.5);
  const mx = lerp(zCellX(8), AX, m);
  const my = lerp(zy, AY, m);
  const mw = lerp(cw, AW, m);
  const mh = lerp(ch, AH, m);
  const st8 = cellState(8);
  const cell8A = t < T[2] ? (st8 ? st8[1] : 0) : 1;
  const segA = prog(t, T[2] + 0.5, 0.5) * c2Out;
  const arcK = prog(t, T[2] + 2.0, 0.9);

  // c3 bars / c4 dim
  const c4Dim = 1 - prog(t, T[4] - 0.3, 0.45);
  const c3A = prog(t, T[3] + 0.4, 0.5) * c4Dim;
  const sy = linScale(0, 30, 790, 210);
  const kSf = prog(t, T[3] + 0.6, 0.9);
  const kNf = prog(t, T[3] + 2.2, 1.4);

  // ambient stream of verified cells (c3)
  const speed = 2.2;
  const base = Math.floor(rt * speed);
  const frac = rt * speed - base;

  const fencing = fenced.some((f) => t >= f.dep && t < f.s0 + CY) && t < T[2];

  return (
    <>
      <Stage>
        {/* ---------------- running example: datapath (c0–c1) */}
        {dpOp > 0.001 && (
          <g opacity={dpOp}>
            <Datapath
              g={g}
              hl={{ reqRing: K.req }}
              dimOthers={0.55}
              cellFill={{ reqRing: (i) => {
                const s = cellState(i);
                return s && s[1] > 0.02 ? hexA(s[0], 0.9 * s[1]) : undefined;
              } }}
            />
            {/* zoom connectors */}
            <g opacity={zoomOp * 0.7}>
              <line x1={g.r.reqRing.x + g.r.reqRing.w} y1={g.r.reqRing.y} x2={Z.x} y2={Z.y} stroke={K.req} strokeWidth={2} strokeDasharray="8 8" />
              <line x1={g.r.reqRing.x + g.r.reqRing.w} y1={g.r.reqRing.y + g.r.reqRing.h} x2={Z.x} y2={Z.y + Z.h} stroke={K.req} strokeWidth={2} strokeDasharray="8 8" />
            </g>
            {/* c0 packets: 8 overtakes 7 */}
            {[
              { seq: 7, dep: dep7, land: land7, dy: -30 },
              { seq: 8, dep: dep8, land: land8, dy: 36 },
            ].map((p) => {
              const k = prog(t, p.dep, p.land - p.dep);
              if (k <= 0 || t > p.land + 0.8) return null;
              const pts = toCell(p.seq);
              const [x, y] = along(pts, k);
              return (
                <g key={p.seq} opacity={1 - prog(t, p.land + 0.1, 0.5)}>
                  <Packet pts={pts} k={k} color={p.seq === 8 ? K.reorder : K.req} r={12} />
                  <text x={x} y={y + p.dy} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={p.seq === 8 ? K.reorder : K.req} fontFamily={FONT}>
                    {p.seq}
                  </text>
                </g>
              );
            })}
            {/* c1 packets: one at a time behind fences */}
            {fenced.map((p) => {
              const k = prog(t, p.dep, p.land - p.dep);
              if (k <= 0 || t > p.land + 0.8) return null;
              const pts = toCell(p.seq);
              const [x, y] = along(pts, k);
              return (
                <g key={`f${p.seq}`} opacity={1 - prog(t, p.land + 0.1, 0.5)}>
                  <Packet pts={pts} k={k} color={K.req} r={12} />
                  <text x={x} y={y - 30} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={K.req} fontFamily={FONT}>
                    {p.seq}
                  </text>
                </g>
              );
            })}
            {/* SFENCE stall badge under the host core */}
            <g opacity={(fencing ? 1 : 0.0) * fade(t, T[1] + 0.2, T[2])}>
              <rect x={g.r.core.x} y={g.r.core.y + g.r.core.h + 12} width={200} height={38} rx={10} fill="url(#hatchRed)" stroke={K.fence} strokeWidth={2} />
              <text x={g.r.core.x + 100} y={g.r.core.y + g.r.core.h + 31} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
                SFENCE: stall
              </text>
            </g>
          </g>
        )}

        {/* ---------------- zoom panel: card DRAM request cells 6–9 */}
        {zoomOp > 0.001 && (
          <g opacity={zoomOp} fontFamily={FONT}>
            <rect x={Z.x} y={Z.y} width={Z.w} height={Z.h} rx={18} fill={C.panel} stroke={K.req} strokeWidth={2.5} />
            <text x={Z.x + 26} y={Z.y + 44} fontSize={28} fontWeight={800} fill={C.text}>
              Request cells · card DRAM
            </text>
            <text x={Z.x + Z.w - 26} y={Z.y + 44} textAnchor="end" fontSize={20} fill={C.muted} fontFamily={MONO}>
              zoom
            </text>
            {[6, 7, 8, 9].map((i) => {
              const s = cellState(i);
              const a = s ? s[1] : 0;
              const col = s ? s[0] : C.dim;
              const b = badge(i);
              return (
                <g key={i}>
                  <text x={zCellX(i) + cw / 2} y={zy - 16} textAnchor="middle" fontSize={22} fill={C.muted} fontFamily={MONO}>
                    slot {i}
                  </text>
                  {i !== 8 && (
                    <>
                      <rect x={zCellX(i)} y={zy} width={cw} height={ch} rx={10} fill="rgba(139,149,173,0.08)" stroke={C.dim} strokeWidth={2} />
                      {a > 0.01 && (
                        <g opacity={a}>
                          <rect x={zCellX(i)} y={zy} width={cw} height={ch} rx={10} fill={hexA(col, 0.22)} stroke={col} strokeWidth={3} />
                          <text x={zCellX(i) + cw / 2} y={zy + ch / 2} textAnchor="middle" dominantBaseline="central" fontSize={64} fontWeight={800} fill={col}>
                            {i}
                          </text>
                        </g>
                      )}
                    </>
                  )}
                  {i === 8 && <rect x={zCellX(i)} y={zy} width={cw} height={ch} rx={10} fill="rgba(139,149,173,0.08)" stroke={C.dim} strokeWidth={2} />}
                  {b && b[2] > 0.01 && (
                    <text x={zCellX(i) + cw / 2} y={zy + ch + 40} textAnchor="middle" fontSize={28} fontWeight={800} fill={b[1]} opacity={b[2]}>
                      {b[0]}
                    </text>
                  )}
                </g>
              );
            })}
            <text x={Z.x + Z.w / 2} y={Z.y + Z.h - 28} textAnchor="middle" fontSize={28} fontWeight={700} fill={K.reorder} opacity={fade(t, land7 + 0.3, T[1])}>
              arrival order: 8, then 7
            </text>
            <text x={Z.x + Z.w / 2} y={Z.y + Z.h - 28} textAnchor="middle" fontSize={28} fontWeight={700} fill={K.ok} opacity={fade(t, fenced[2].land + 0.3, T[2] - 0.5)}>
              with SFENCE: 7, 8, 9 in order
            </text>
          </g>
        )}

        {/* ---------------- cell 8: zoom cell → anatomy strip (c0–c2) */}
        {cell8A * c2Out > 0.01 && (
          <g opacity={cell8A * c2Out * (t < T[2] ? zoomOp : 1)} fontFamily={FONT}>
            <rect x={mx} y={my} width={mw} height={mh} rx={lerp(10, 8, m)} fill={hexA(st8 && t < T[2] ? st8[0] : K.req, 0.22 * (1 - segA))} stroke={st8 && t < T[2] ? st8[0] : K.req} strokeWidth={3} />
            <text x={mx + mw / 2} y={my + mh / 2} textAnchor="middle" dominantBaseline="central" fontSize={64} fontWeight={800} fill={st8 && t < T[2] ? st8[0] : K.req} opacity={1 - prog(t, T[2], 0.3)}>
              8
            </text>
            {segA > 0.01 && (
              <g opacity={segA}>
                {SEG.map((s, i) => {
                  const sx = AX + SEG_X[i];
                  const isSeq = i === 0 || i === 4;
                  return (
                    <g key={i}>
                      <rect x={sx} y={AY} width={s.w} height={AH} fill={hexA(s.col, 0.2)} stroke={s.col} strokeWidth={2.5} />
                      <text x={sx + s.w / 2} y={isSeq || i === 3 ? AY + 38 : AY + AH / 2} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={700} fill={C.text}>
                        {s.name} · {s.bytes} B
                      </text>
                      {isSeq && (
                        <text x={sx + s.w / 2} y={AY + 86} textAnchor="middle" dominantBaseline="central" fontSize={48} fontWeight={800} fill={K.req}>
                          {s.val}
                        </text>
                      )}
                      {i === 3 && (
                        <text x={sx + s.w / 2} y={AY + 84} textAnchor="middle" dominantBaseline="central" fontSize={24} fill={C.muted} fontFamily={MONO}>
                          derived from seq 8
                        </text>
                      )}
                    </g>
                  );
                })}
                <rect x={AX} y={AY} width={AW} height={AH} fill="none" stroke={C.text} strokeWidth={3} rx={8} />
              </g>
            )}
          </g>
        )}

        {/* head ↔ tail comparison arc (c2) */}
        {arcK > 0 && (
          <g opacity={c2Out}>
            <path
              d={`M ${AX + 100} ${AY + AH} C ${AX + 100} ${AY + AH + 60}, ${AX + AW - 100} ${AY + AH + 60}, ${AX + AW - 100} ${AY + AH}`}
              fill="none"
              stroke={K.card}
              strokeWidth={3.5}
              strokeDasharray={`${arcK} 1`}
              pathLength={1}
            />
            <text x={AX + AW / 2} y={AY + AH + 82} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={700} fill={K.card} fontFamily={FONT} opacity={prog(t, T[2] + 2.6, 0.5)}>
              receiver (card core) compares head and tail
            </text>
          </g>
        )}

        {/* receiver check cards (c2) */}
        {[
          { x: 160, torn: false, a0: T[2] + 3.0 },
          { x: 990, torn: true, a0: T[2] + 5.0 },
        ].map((cd) => {
          const y0 = 448;
          const cwid = 770;
          const a = prog(t, cd.a0, 0.5) * c2Out;
          if (a <= 0.01) return null;
          const col = cd.torn ? K.bad : K.ok;
          const rows = cd.torn
            ? ["head 8 ≠ tail (stale)  ✗", "payload half-written  ✗"]
            : ["head 8 = tail 8  ✓", "pattern matches  ✓"];
          return (
            <g key={cd.x} opacity={a} fontFamily={FONT}>
              <rect x={cd.x} y={y0} width={cwid} height={420} rx={18} fill={C.panel} stroke={col} strokeWidth={2.5} />
              <text x={cd.x + 30} y={y0 + 48} fontSize={30} fontWeight={800} fill={col}>
                {cd.torn ? "Half-written cell" : "Complete cell"}
              </text>
              <MiniCell x={cd.x + 40} y={y0 + 82} w={cwid - 80} h={80} torn={cd.torn} a={1} />
              {rows.map((row, i) => (
                <text key={i} x={cd.x + 40} y={y0 + 230 + i * 56} fontSize={32} fontWeight={700} fill={col} opacity={prog(t, cd.a0 + 0.6 + i * 0.6, 0.4)}>
                  {row}
                </text>
              ))}
              <g opacity={prog(t, cd.a0 + 1.9, 0.4)}>
                <rect x={cd.x + 40} y={y0 + 330} width={cd.torn ? 470 : 260} height={66} rx={33} fill={cd.torn ? "transparent" : col} stroke={col} strokeWidth={3} />
                <text x={cd.x + 40 + (cd.torn ? 235 : 130)} y={y0 + 363} textAnchor="middle" dominantBaseline="central" fontSize={30} fontWeight={800} fill={cd.torn ? col : C.bg}>
                  {cd.torn ? "→ not yet: poll again" : "→ counted"}
                </text>
              </g>
            </g>
          );
        })}

        {/* ---------------- c0 bottom: send order vs arrival order */}
        {(() => {
          const a = fade(t, T[0] + 0.8, T[1] - 0.3);
          if (a <= 0.01) return null;
          const bx = (j: number) => 420 + j * 150;
          const box = (j: number, y: number, seq: number, col: string, k: number) =>
            k > 0.01 && (
              <g opacity={k}>
                <rect x={bx(j)} y={y} width={110} height={66} rx={10} fill={hexA(col, 0.2)} stroke={col} strokeWidth={3} />
                <text x={bx(j) + 55} y={y + 33} textAnchor="middle" dominantBaseline="central" fontSize={38} fontWeight={800} fill={col}>
                  {seq}
                </text>
              </g>
            );
          return (
            <g opacity={a} fontFamily={FONT}>
              <text x={80} y={655} fontSize={30} fontWeight={800} fill={K.host}>
                Host sends
              </text>
              <text x={80} y={822} fontSize={30} fontWeight={800} fill={K.card}>
                Card memory sees
              </text>
              {box(0, 620, 7, K.req, prog(t, dep7, 0.4))}
              {box(1, 620, 8, K.req, prog(t, dep8, 0.4))}
              {box(0, 788, 8, K.reorder, prog(t, land8, 0.4))}
              {box(1, 788, 7, K.req, prog(t, land7, 0.4))}
              <Arrow x1={bx(0) + 55} y1={690} x2={bx(1) + 55} y2={782} color={K.req} w={3.5} k={prog(t, land7 - 0.4, 0.5)} head={12} />
              <Arrow x1={bx(1) + 55} y1={690} x2={bx(0) + 55} y2={782} color={K.reorder} w={3.5} k={prog(t, land8 - 0.4, 0.5)} head={12} />
              <text x={740} y={655} fontSize={24} fill={C.muted} opacity={prog(t, dep8, 0.4)}>
                ← earlier · later →
              </text>
              <g opacity={prog(t, land7 + 0.3, 0.5)}>
                <text x={1060} y={720} fontSize={60} fontWeight={800} fill={K.reorder}>
                  weakly ordered
                </text>
                <text x={1060} y={782} fontSize={28} fill={C.muted}>
                  MOVDIR64B, no fence: a later cell can land first
                </text>
              </g>
            </g>
          );
        })()}

        {/* ---------------- c1 bottom: host timeline with a fence after every cell */}
        {(() => {
          const a = fade(t, T[1] + 0.15, T[2]);
          if (a <= 0.01) return null;
          const x0 = 300;
          const pxs = 950 / (3 * CY);
          const ly = 650;
          const lh = 80;
          const xNow = x0 + Math.max(0, Math.min(3 * CY, t - P0)) * pxs;
          return (
            <g opacity={a} fontFamily={FONT}>
              <text x={60} y={ly + 34} fontSize={30} fontWeight={800} fill={K.host}>
                Host core
              </text>
              <text x={60} y={ly + 66} fontSize={22} fill={C.muted} fontFamily={MONO}>
                MOVDIR64B; SFENCE
              </text>
              <rect x={x0} y={ly} width={950} height={lh} rx={8} fill="rgba(139,149,173,0.05)" stroke={C.line} strokeWidth={2} />
              {fenced.map((f, i) => {
                const sx = x0 + i * CY * pxs;
                const stW = Math.min(ST, Math.max(0, t - f.s0)) * pxs;
                const feW = Math.min(FE, Math.max(0, t - f.dep)) * pxs;
                return (
                  <g key={i}>
                    {stW > 0 && <rect x={sx} y={ly + 6} width={stW} height={lh - 12} rx={6} fill={K.req} />}
                    {stW > 30 && (
                      <text x={sx + ST * pxs / 2} y={ly + lh / 2} textAnchor="middle" dominantBaseline="central" fontSize={30} fontWeight={800} fill={C.bg}>
                        {f.seq}
                      </text>
                    )}
                    {feW > 0 && <rect x={sx + ST * pxs + 3} y={ly + 6} width={Math.max(0, feW - 6)} height={lh - 12} rx={6} fill="url(#hatchRed)" stroke={K.fence} strokeWidth={2} />}
                    {feW > 0 && (
                      <g opacity={clamp01((feW - 150) / 60)}>
                        <rect x={sx + ST * pxs + FE * pxs / 2 - 100} y={ly + lh / 2 - 21} width={200} height={42} rx={8} fill={C.bg} opacity={0.85} />
                        <text x={sx + ST * pxs + FE * pxs / 2} y={ly + lh / 2} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={K.fence}>
                          SFENCE stall
                        </text>
                      </g>
                    )}
                  </g>
                );
              })}
              <line x1={xNow} y1={ly - 14} x2={xNow} y2={ly + lh + 14} stroke={C.text} strokeWidth={2.5} opacity={t < P0 + 3 * CY ? 0.8 : 0.3} />
              <Arrow x1={x0} y1={ly + lh + 40} x2={x0 + 950} y2={ly + lh + 40} color={C.dim} w={2.5} head={12} />
              <text x={x0 + 950} y={ly + lh + 76} textAnchor="end" fontSize={22} fill={C.muted}>
                time (schematic)
              </text>
            </g>
          );
        })()}

        {/* ---------------- c3: rate bars */}
        {c3A > 0.01 && (
          <g opacity={c3A} fontFamily={FONT}>
            <Axes
              x0={220}
              x1={1100}
              y0={790}
              y1={210}
              sx={linScale(0, 1, 220, 1100)}
              sy={sy}
              xticks={[]}
              yticks={[
                [0, "0"],
                [10, "10"],
                [20, "20"],
                [30, "30"],
              ]}
              ylabel="message rate (M msg/s)"
              size={24}
            />
            <Bars
              x0={220}
              x1={1100}
              y0={790}
              y1={210}
              max={30}
              size={26}
              k={[kSf, kNf]}
              data={[
                { label: "SFENCE every cell", value: 6.86, color: K.fence, text: " " },
                { label: "no fence", value: 26.2, color: K.inline, text: " " },
              ]}
            />
            <text x={440} y={sy(6.86 * kSf) - 18} textAnchor="middle" fontSize={48} fontWeight={800} fill={K.fence} opacity={kSf}>
              {countUp(t, T[3] + 0.6, 0.9, 6.86, 2)}
            </text>
            <text x={880} y={sy(26.2 * kNf) - 18} textAnchor="middle" fontSize={48} fontWeight={800} fill={K.inline} opacity={kNf}>
              {countUp(t, T[3] + 2.2, 1.4, 26.2, 1)}
            </text>
            <text x={440} y={866} textAnchor="middle" fontSize={24} fill={C.muted}>
              W=128
            </text>
            <text x={880} y={866} textAnchor="middle" fontSize={24} fill={C.muted} opacity={prog(t, T[3] + 3.2, 0.5)}>
              card skips payload check
            </text>
          </g>
        )}

        {/* c3: stream of arriving cells, all complete */}
        {c3A > 0.01 && (
          <g opacity={prog(t, T[3] + 4.5, 0.6) * c4Dim} fontFamily={FONT}>
            <text x={1250} y={612} fontSize={24} fill={C.muted}>
              cells arriving with no fence
            </text>
            {Array.from({ length: 7 }, (_, j) => {
              const x = 1250 + (j - frac) * 96;
              const edge = clamp01((x - 1240) / 60) * clamp01((1800 - x) / 60);
              return (
                <g key={j} opacity={edge}>
                  <rect x={x} y={632} width={84} height={84} rx={10} fill={hexA(K.req, 0.2)} stroke={K.req} strokeWidth={2.5} />
                  <text x={x + 42} y={674} textAnchor="middle" dominantBaseline="central" fontSize={44} fontWeight={800} fill={K.ok}>
                    ✓
                  </text>
                </g>
              );
            })}
          </g>
        )}

        {/* ---------------- c4: host sends freely, card checks */}
        {(() => {
          const a = prog(t, T[4] + 0.6, 0.6);
          if (a <= 0.01) return null;
          const y = 320;
          return (
            <g fontFamily={FONT}>
              <g opacity={a}>
                <rect x={330} y={y} width={520} height={150} rx={18} fill={C.panel} stroke={K.host} strokeWidth={3} />
                <text x={360} y={y + 52} fontSize={32} fontWeight={800} fill={K.host}>
                  HOST · where it's sent
                </text>
                <text x={360} y={y + 106} fontSize={28} fill={C.text}>
                  MOVDIR64B, no SFENCE
                </text>
              </g>
              <g opacity={prog(t, T[4] + 1.1, 0.5)}>
                {Array.from({ length: 3 }, (_, j) => {
                  const k = (rt * 0.8 + j / 3) % 1;
                  return <circle key={j} cx={lerp(880, 1060, k)} cy={y + 75} r={10} fill={K.req} opacity={Math.sin(Math.PI * k)} />;
                })}
                <Arrow x1={870} y1={y + 75} x2={1075} y2={y + 75} color={K.req} w={4} k={prog(t, T[4] + 1.1, 0.6)} />
              </g>
              <g opacity={prog(t, T[4] + 1.6, 0.6)}>
                <rect x={1090} y={y} width={520} height={150} rx={18} fill={C.panel} stroke={K.card} strokeWidth={3} />
                <text x={1120} y={y + 52} fontSize={32} fontWeight={800} fill={K.card}>
                  CARD · where it's checked
                </text>
                <text x={1120} y={y + 106} fontSize={28} fill={C.text}>
                  head seq = tail seq{" "}
                  <tspan fill={K.ok} fontWeight={800}>
                    ✓
                  </tspan>
                </text>
              </g>
            </g>
          );
        })()}
      </Stage>

      {/* c1 rate readout */}
      <div
        style={{
          position: "absolute",
          left: 1310,
          top: 618,
          width: 550,
          background: C.panel,
          border: `2px solid ${K.fence}`,
          borderRadius: 20,
          padding: "20px 28px",
          boxSizing: "border-box",
          textAlign: "center",
          ...appear(t, T[1] + 3.2, T[2]),
        }}
      >
        <div style={{ fontSize: 76, fontWeight: 800, color: K.fence, fontVariantNumeric: "tabular-nums", lineHeight: 1.05 }}>
          {countUp(t, T[1] + 3.2, 1.2, 6.86, 2)}
          <span style={{ fontSize: 40 }}> M msg/s</span>
        </div>
        <div style={{ fontSize: 26, color: C.muted, marginTop: 8 }}>SFENCE after every cell · W=128</div>
      </div>

      {/* c2 header line */}
      <Txt x={160} y={150} size={26} color={C.muted} style={appear(t, T[2] + 1.0, T[3])}>
        One request cell = 64 B, written by one MOVDIR64B store
      </Txt>

      {/* c3 torn-cell counter */}
      <div
        style={{
          position: "absolute",
          left: 1250,
          top: 250,
          width: 550,
          background: C.panel,
          border: `2px solid ${K.ok}`,
          borderRadius: 20,
          padding: "22px 30px",
          boxSizing: "border-box",
          textAlign: "center",
          opacity: prog(t, T[3] + 4.0, 0.5) * c4Dim,
        }}
      >
        <div style={{ fontSize: 30, color: C.text, fontWeight: 700 }}>torn cells seen</div>
        <div style={{ fontSize: 140, fontWeight: 800, color: K.ok, lineHeight: 1.1, transform: `scale(${0.6 + 0.4 * prog(t, T[3] + 4.2, 0.5)})` }}>0</div>
        <div style={{ fontSize: 26, color: C.muted }}>across every run</div>
      </div>

      {/* c4 citation line + principle card */}
      <div
        style={{
          position: "absolute",
          left: 210,
          top: 170,
          width: 1500,
          textAlign: "center",
          fontSize: 34,
          fontFamily: MONO,
          color: C.muted,
          padding: "14px 0",
          borderTop: `2px solid ${C.line}`,
          borderBottom: `2px solid ${C.line}`,
          ...appear(t, T[4] + 0.15),
        }}
      >
        <span style={{ color: K.ok, fontWeight: 800 }}>destination-side ordering</span> · Liew et al., ASPLOS '26
      </div>
      <div
        style={{
          position: "absolute",
          left: 260,
          top: 560,
          width: 1400,
          background: C.panel,
          border: `3px solid ${K.ok}`,
          borderRadius: 24,
          padding: "36px 50px",
          boxSizing: "border-box",
          textAlign: "center",
          boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
          ...appear(t, T[4] + 4.0),
        }}
      >
        <div style={{ fontSize: 60, fontWeight: 800, lineHeight: 1.2, color: C.text }}>
          Pay for order where it's <span style={{ color: K.card }}>checked</span>,
          <br />
          not where it's <span style={{ color: K.host }}>sent</span>.
        </div>
      </div>
    </>
  );
}

export const S05: SceneDef = {
  id: "S05",
  title: "Order at the destination",
  stage: 1,
  source: "testbed::inline · cf. Liew et al., ASPLOS '26",
  dur: DUR,
  cues,
  C: Body,
};
