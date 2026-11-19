import React from "react";
import { C, Cue, K, Link, Note, PacketStream, Pt, SceneDef, Stage, along, appear, clamp01, fade, lerp, lin, pathD, partial, polyLen, prog, useRealT, useT } from "../lib";
import { Bar, BarChart, Chip, InfPill, Rig, RigBlock, Takeaway, rigGeom, rigRoutes } from "./kit";
import { At, Check, CoreGrid, MsgBox, Padlock, StrikeChip, ZoomWedge, bump, lerpGeom, miniCell, squeezeGeom, travelAt } from "./_Mrv96FrameScenes";

// <cues>
const cues: Cue[] = [
  [0.60, 14.86, "Host: **Intel Xeon**, one core pinned at **3.9 GHz**. DPU: **Marvell OCTEON 10**, 24 Arm cores at **2.0 GHz**. Link: **PCIe Gen5 ×4**.", "The hardware: an Intel Xeon host, using one core pinned at three point nine gigahertz, and a Marvell Octeon ten D P U with twenty-four Arm cores at two gigahertz, linked by P C I e Gen five, by four."], // 0
  [15.21, 27.51, "Forward: one **MOVDIR64B** instruction writes a whole 64-byte message into a ring in card memory, through a PCIe window. No doorbell, no DMA.", "Forward: one move dir sixty-four B instruction writes a whole sixty-four byte message into a ring in card memory, through a P C I e window. No doorbell, no D M A."], // 1
  [27.86, 34.58, "A card core **polls** the ring. Sequence numbers at both ends of each message tell it the slot is complete and in order.", "A card core polls the ring. Sequence numbers at both ends of each message tell it a slot is complete, and in order."], // 2
  [34.93, 46.79, "Return: the card core writes its reply into host memory with **write-combining (WC)**: four 16-byte stores leave as one 64-byte PCIe write.", "Return: the card core writes its reply straight into host memory with write combining. Four stores, of sixteen bytes each, merge into a single sixty-four byte P C I e write."], // 3
  [47.14, 59.14, "Sanity check first: does reply traffic slow the forward path? Sender pinned at 3.9 GHz: **113.1 → 112.2 M/s**. No.", "First, a sanity check: does reply traffic slow the forward path? With the sender pinned, forward throughput only moves from one hundred thirteen point one to one hundred twelve point two million per second. No."], // 4
  [59.49, 74.85, "An earlier drop to **75.6 M/s** came from an unpinned sender at **~2.1 GHz**. **[INF]** The clock, not PCIe. Lesson: pin every clock.", "An earlier drop, to seventy-five point six, appeared only with the sender unpinned, at about two point one gigahertz. Our reading: the clock, not P C I e, though that clock was never run one way. Lesson: pin every clock."], // 5
];
const DUR = 76.25; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
/** Full diagram for the walk-through (T0–T3); compact copy on the left that makes room for the chart (T4–T5). */
const G0 = rigGeom(1, 0, 0);
const G1 = squeezeGeom(rigGeom(0.85, -1, 30), 366);
const LABELS: Partial<Record<RigBlock, [string, string?]>> = {
  host: ["HOST", "Intel Xeon Gold 6438M"],
  hcore: ["core 40", ""],
  link: [""],
};
/** Sanity-check chart, right column (T4–T5). Bar 3 lives in its own chart so its label cannot show early. */
const CH = { x0: 1262, x1: 1862, y0: 600, y1: 290, max: 120 };
const NOBAR: Bar = { label: "", value: 0 };
const BARS_E0: Bar[] = [
  { label: "forward alone", sub: "pinned 3.9 GHz", value: 113.1, color: K.fwd },
  { label: "with replies", sub: "pinned 3.9 GHz", value: 112.2, color: K.fwd },
  NOBAR,
];
const BARS_UNPINNED: Bar[] = [NOBAR, NOBAR, { label: "with replies", sub: "unpinned", value: 75.6, color: K.slow }];
/** Insets in full-diagram coordinates: one 64-byte cell (T2), the write-combining buffer (T3). */
const CELL = { x0: 640, x1: 1280, y: 452, h: 60 };
const WCB = { w: 588, gap: 12, y: 606, h: 58 };
const RECV_CORE = 2;
const REPLY_CORE = 3;

function Body() {
  const t = useT();
  const rt = useRealT();
  const g = lerpGeom(G0, G1, prog(t, T[4] + 0.1, 1.3));
  const R = rigRoutes(g);
  const { r, s } = g;
  const dim = 0.6 * prog(t, T[4] + 0.3, 1.0);
  const hlCore = prog(t, T[5] + 3.4, 0.5);

  // ---- build-up: hardware (T0), forward-path blocks (T1), return-path blocks (T3)
  const show = {
    host: prog(t, T[0] + 0.1, 0.7),
    hcore: prog(t, T[0] + 2.4, 0.6),
    card: prog(t, T[0] + 6.3, 0.7),
    ccores: 0,
    link: prog(t, T[0] + 10.8, 0.7),
    wq: prog(t, T[1] + 0.3, 0.6),
    cring: prog(t, T[1] + 0.6, 0.6),
    wc: prog(t, T[3] + 0.3, 0.6),
    hring: prog(t, T[3] + 0.6, 0.6),
  };
  const coresK = prog(t, T[0] + 8.2, 0.6);
  const fade2 = 1 - 0.45 * (dim / 0.6);

  // ---- lanes: drawn progressively, then the kit lane (arrowhead + label) takes over
  const fwdDraw = prog(t, T[1] + 0.4, 1.0);
  const retDraw = prog(t, T[3] + 0.4, 1.0);
  const lanes = { fwd: clamp01((fwdDraw - 0.85) / 0.15), ret: clamp01((retDraw - 0.85) / 0.15) };

  // ---- traffic; both streams slow to 0.67× when the sender clock drops in T5 (75.6 / 112.2)
  const slowA = T[5] + 4.3;
  const slowB = T[5] + 5.5;
  const fwdPath: Pt[] = [...R.send, ...R.fwd];
  const Lf = polyLen(fwdPath);
  const F0 = T[1] + 6.0;
  const fTravel = travelAt(rt - F0, [[slowA - F0, 320], [slowB - F0, 320 * 0.67]]);
  const fLanded = fTravel >= Lf ? Math.floor((fTravel - Lf) / 54) + 1 : 0;
  const repPath: Pt[] = [...R.reply, ...R.ret];
  const Lr = polyLen(repPath);
  const R0 = T[3] + 11.0;
  const rTravel = travelAt(rt - R0, [[slowA - R0, 300], [slowB - R0, 300 * 0.67]]);
  const rLanded = rTravel >= Lr ? Math.floor((rTravel - Lr) / 70) + 1 : 0;
  // replies stop for the "forward alone" run at T4 and resume for "with replies"; in-flight ones finish
  const repColor = (i: number) => {
    const born = R0 + (i * 70) / 300;
    return born < T[4] - 0.3 || born > T[4] + 7.4 ? K.ret : null;
  };

  // ---- T1: the first 64-byte message (one MOVDIR64B) lands in request-ring cell 0
  const kP = prog(t, T[1] + 2.0, 3.4);
  const kIn = prog(t, T[1] + 5.4, 0.45);
  const c0 = miniCell(g, "cring", 0);
  const msgP: Pt = kIn > 0 ? [lerp(r.cring.x, c0.x + c0.w / 2, kIn), lerp(g.fwdY, c0.y + c0.h / 2, kIn)] : along(fwdPath, kP);
  const cringFill = Math.min(6, (t >= T[1] + 5.85 ? 1 : 0) + fLanded);
  const chipsK = fade(t, T[1] + 9.8, T[2] - 0.5);
  const windowK = fade(t, T[1] + 8.2, T[2] - 0.5);

  // ---- T2: the card core polls the ring; zoom on one cell
  const pollK = prog(t, T[2] + 0.2, 0.4) * (1 - 0.6 * prog(t, T[4] + 0.2, 0.6));
  const ph = (Math.max(0, rt - T[2]) / 1.5) % 1;
  const pollP = along(R.poll, ph < 0.5 ? 1 - ph * 2 : ph * 2 - 1);
  const sweep = miniCell(g, "cring", Math.floor(Math.max(0, rt - T[2]) / 0.34) % 8);
  const cellK = fade(t, T[2] + 1.3, T[3] - 0.5);
  const seqL = prog(t, T[2] + 2.3, 0.4);
  const seqR = prog(t, T[2] + 2.8, 0.4);
  const okK = prog(t, T[2] + 3.6, 0.5);
  const zc = miniCell(G0, "cring", 0);

  // ---- T3: four 16-byte stores fill the WC buffer, then ONE 64-byte line crosses the return lane
  const storeT = [5.4, 5.9, 6.4, 6.9].map((d) => T[3] + d);
  const wcK = fade(t, T[3] + 1.5, T[4] - 0.5);
  const mergeK = prog(t, T[3] + 8.0, 0.5);
  const flash = bump(t, T[3] + 7.35, 0.6);
  const gapNow = WCB.gap * (1 - mergeK);
  const slotW = (WCB.w - 3 * gapNow) / 4;
  const wx0 = 960 - WCB.w / 2;
  const kQ = prog(t, T[3] + 8.6, 1.9);
  const kQin = prog(t, T[3] + 10.5, 0.45);
  const h0 = miniCell(g, "hring", 0);
  const qP: Pt = kQin > 0 ? [lerp(r.hring.x + r.hring.w, h0.x + h0.w / 2, kQin), lerp(g.retY, h0.y + h0.h / 2, kQin)] : along(R.ret, kQ);
  const hringFill = Math.min(5, (t >= T[3] + 10.95 ? 1 : 0) + rLanded);

  // ---- host core: clock read-out, clock gauge and "pinned" (T0), unpinned drop (T5)
  const hc = r.hcore;
  const hcx = hc.x + hc.w / 2;
  const hcOp = show.hcore * lerp(1, Math.max(hlCore, 0.3), dim);
  const clockK = prog(t, T[0] + 3.4, 0.5);
  const pinK = prog(t, T[0] + 4.2, 0.5);
  const dropK = prog(t, T[5] + 4.3, 1.2);
  const oldK = 1 - prog(t, T[5] + 4.4, 0.4);
  const newK = prog(t, T[5] + 4.9, 0.4);
  const clockY = hc.y + 34 * s + Math.max(22, 24 * s) + 2;
  const clockFs = Math.max(22, 24 * s);
  const pinFs = Math.max(20, 22 * s);
  const gx0 = hc.x + 0.14 * hc.w;
  const gw = 0.72 * hc.w;
  const gy = hc.y + 0.655 * hc.h;
  const ghz = lerp(3.9, 2.1, dropK);
  const pinY = hc.y + 0.9 * hc.h;

  // ---- T4/T5: sanity-check chart
  const chartK = prog(t, T[4] + 1.4, 0.6);
  const pulseK = prog(t, T[4] + 7.4, 0.5);
  const verdictK = prog(t, T[4] + 10.4, 0.45);
  const linkMid: Pt = [r.link.x + r.link.w / 2, (g.fwdY + g.retY) / 2];
  const pcieK = show.link * (1 - prog(t, T[2] + 0.5, 0.45)) + prog(t, T[4] + 1.0, 0.6);

  return (
    <>
      <Stage>
        <Rig g={g} show={show} dim={dim} hl={{ hcore: hlCore }} labels={LABELS} lanes={lanes}
          ringFill={{ cring: cringFill, hring: hringFill }} />
        <CoreGrid g={g} show={coresK} dim={dim} main="Arm cores" sub="24 × 2.0 GHz"
          lit={(i) => ((i === RECV_CORE && t >= T[2] + 0.1) || (i === REPLY_CORE && t >= T[4] + 1.0) ? K.card : null)} />

        {/* short connectors inside each side (the kit draws only the two PCIe lanes) */}
        <Link pts={R.send} color={K.fwd} w={3} opacity={0.45 * Math.min(show.hcore, show.wq) * fade2} />
        <Link pts={R.poll} color={K.card} w={3} opacity={0.5 * Math.min(show.cring, coresK) * fade2} />
        <Link pts={R.reply} color={K.ret} w={3} opacity={0.45 * show.wc * fade2} />
        <Link pts={R.hpoll} color={K.ret} w={3} opacity={0.45 * show.hring * fade2} />
        {lanes.fwd < 1 && <Link pts={R.fwd} k={fwdDraw} color={K.fwd} w={5} />}
        {lanes.ret < 1 && <Link pts={R.ret} k={retDraw} color={K.ret} w={5} />}
        {pulseK > 0 && <Link pts={R.ret} color={K.ret} w={18} opacity={pulseK * (0.16 + 0.12 * Math.sin(rt * 6))} />}

        {/* host core: clock, clock gauge, pinned */}
        <g opacity={hcOp} fontFamily="Inter, sans-serif">
          <text x={hcx} y={clockY} textAnchor="middle" fontSize={clockFs} fontWeight={700} fill={C.text} opacity={clockK * oldK}>
            3.9 GHz
          </text>
          <text x={hcx} y={clockY} textAnchor="middle" fontSize={clockFs} fontWeight={800} fill={K.slow} opacity={newK}>
            ~2.1 GHz
          </text>
          <g opacity={clockK}>
            <rect x={gx0} y={gy} width={gw} height={12 * s} rx={6 * s} fill="#0f1528" stroke={C.line} strokeWidth={1.5} />
            <rect x={gx0} y={gy} width={(gw * ghz) / 4.2} height={12 * s} rx={6 * s} fill={C.text} opacity={0.85 * (1 - dropK)} />
            <rect x={gx0} y={gy} width={(gw * ghz) / 4.2} height={12 * s} rx={6 * s} fill={K.slow} opacity={dropK} />
          </g>
          <g opacity={pinK}>
            <Padlock x={hcx - 50 * s} y={pinY - 9 * s} s={s} open={prog(t, T[5] + 4.4, 0.5)} color={dropK > 0.5 ? K.slow : K.host} />
            <text x={hcx - 32 * s} y={pinY} fontSize={pinFs} fontWeight={700} fill={K.host} opacity={oldK}>
              pinned
            </text>
            <text x={hcx - 32 * s} y={pinY} fontSize={pinFs} fontWeight={800} fill={K.slow} opacity={newK}>
              unpinned
            </text>
          </g>
        </g>

        {/* PCIe band label: leaves while the insets use the band (T2–T3), returns on the compact diagram */}
        <text x={linkMid[0]} y={linkMid[1] + 11} textAnchor="middle" fontSize={30 * s} fontWeight={700} fill={C.muted}
          opacity={pcieK}>
          PCIe Gen5 ×4
        </text>
        {/* "MOVDIR64B →" lights up when the instruction is named */}
        <rect x={linkMid[0] - 130} y={g.fwdY - 50} width={260} height={42} rx={12} fill={K.fwd}
          opacity={0.22 * bump(t, T[1] + 1.0, 1.4)} />
        {/* the PCIe window the write goes through */}
        <g opacity={windowK}>
          <rect x={r.card.x - 16} y={g.fwdY - 26} width={32} height={52} rx={8} fill="none" stroke={K.fwd} strokeWidth={3}
            strokeDasharray="6 5" />
          <text x={r.card.x - 28} y={g.fwdY - 40} textAnchor="end" fontSize={24} fontWeight={700} fill={K.fwd}>
            PCIe window
          </text>
        </g>

        {/* the card core polls: a probe bounces core ↔ ring, a cursor sweeps the cells */}
        <g opacity={pollK}>
          <rect x={sweep.x - 3 * s} y={sweep.y - 3 * s} width={sweep.w + 6 * s} height={sweep.h + 6 * s} rx={5 * s} fill="none"
            stroke={K.card} strokeWidth={3} />
          <circle cx={pollP[0]} cy={pollP[1]} r={16} fill={K.card} opacity={0.2} />
          <circle cx={pollP[0]} cy={pollP[1]} r={8} fill={K.card} />
        </g>

        {/* traffic */}
        <PacketStream pts={fwdPath} travel={fTravel} gap={54} size={13} color={K.fwd} opacity={rt >= F0 ? 1 : 0} />
        <PacketStream pts={repPath} travel={rTravel} gap={70} size={13} color={K.ret} colorAt={repColor} opacity={rt >= R0 ? 1 : 0} />
        {storeT.map((te, j) => {
          const k = lin(t, te - 0.5, 0.5);
          if (k <= 0 || k >= 1) return null;
          const [x, y] = along(R.reply, k);
          return <rect key={j} x={x - 8} y={y - 8} width={16} height={16} rx={3} fill={K.ret} />;
        })}

        {/* T1 message + trail, T3 reply line + trail */}
        <path d={pathD(partial(fwdPath, kP))} fill="none" stroke={K.fwd} strokeWidth={6} strokeLinecap="round" strokeLinejoin="round"
          opacity={kP > 0 ? 0.45 * (1 - prog(t, T[1] + 5.6, 0.8)) : 0} />
        <MsgBox p={msgP} w={lerp(84, c0.w, kIn)} h={lerp(32, c0.h, kIn)} color={K.fwd} label="64 B" labelK={1 - kIn * 2}
          opacity={kP > 0 && kIn < 1 ? 1 : 0} />
        <path d={pathD(partial(R.ret, kQ))} fill="none" stroke={K.ret} strokeWidth={6} strokeLinecap="round"
          opacity={kQ > 0 ? 0.45 * (1 - prog(t, T[3] + 10.6, 0.8)) : 0} />
        <MsgBox p={qP} w={lerp(84, h0.w, kQin)} h={lerp(32, h0.h, kQin)} color={K.ret} label="64 B" labelK={1 - kQin * 2}
          opacity={kQ > 0 && kQin < 1 ? 1 : 0} />

        {/* T2 inset: one 64-byte cell, sequence numbers at both ends */}
        <g opacity={cellK} fontFamily="Inter, sans-serif">
          <rect x={zc.x - 4} y={zc.y - 4} width={zc.w + 8} height={zc.h + 8} rx={5} fill="none" stroke={K.landed} strokeWidth={3} />
          <ZoomWedge a0={[zc.x - 4, zc.y - 4]} a1={[zc.x - 4, zc.y + zc.h + 4]} b0={[CELL.x1, CELL.y]} b1={[CELL.x1, CELL.y + CELL.h]}
            color={K.landed} />
          <text x={960} y={CELL.y - 18} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text}>
            one 64-byte cell
          </text>
          <rect x={CELL.x0} y={CELL.y} width={CELL.x1 - CELL.x0} height={CELL.h} rx={10} fill="#0f1528" stroke={K.landed} strokeWidth={2.5} />
          <rect x={CELL.x0 + 110} y={CELL.y + 7} width={CELL.x1 - CELL.x0 - 220} height={CELL.h - 14} rx={6} fill="#1b2742" />
          <text x={960} y={CELL.y + CELL.h / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={26} fill={C.muted}>
            payload
          </text>
          {[
            { x: CELL.x0 + 7, k: seqL },
            { x: CELL.x1 - 103, k: seqR },
          ].map((q, i) => (
            <g key={i}>
              <rect x={q.x - 6} y={CELL.y + 1} width={108} height={CELL.h - 2} rx={9} fill={K.landed}
                opacity={0.35 * bump(t, i ? T[2] + 2.8 : T[2] + 2.3, 0.8)} filter="url(#softglow)" />
              <rect x={q.x} y={CELL.y + 7} width={96} height={CELL.h - 14} rx={6} fill={K.landed} opacity={0.18 + 0.82 * q.k} />
              <text x={q.x + 48} y={CELL.y + CELL.h / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={26}
                fontWeight={800} fill={q.k > 0.5 ? C.bg : K.landed}>
                seq
              </text>
            </g>
          ))}
          <g opacity={okK} transform={`translate(0 ${(1 - okK) * 10})`}>
            <Check x={800} y={CELL.y + CELL.h + 50} k={okK} />
            <text x={826} y={CELL.y + CELL.h + 52} dominantBaseline="central" fontSize={30} fontWeight={800} fill={K.fast}>
              complete · in order
            </text>
          </g>
        </g>

        {/* T3 inset: the write-combining buffer, four 16-byte stores → one 64-byte line */}
        <g opacity={wcK} fontFamily="Inter, sans-serif">
          <ZoomWedge a0={[G0.r.wc.x, G0.r.wc.y + 10]} a1={[G0.r.wc.x, G0.r.wc.y + 64]} b0={[wx0 + WCB.w + 6, WCB.y]}
            b1={[wx0 + WCB.w + 6, WCB.y + WCB.h]} color={K.ret} />
          <text x={960} y={WCB.y - 18} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text}
            opacity={1 - prog(t, T[3] + 7.6, 0.4)}>
            write-combining buffer
          </text>
          <text x={960} y={WCB.y - 18} textAnchor="middle" fontSize={28} fontWeight={800} fill={K.ret} opacity={prog(t, T[3] + 8.1, 0.45)}>
            4 × 16 B → one 64 B PCIe write
          </text>
          {storeT.map((te, j) => {
            const fk = prog(t, te, 0.3);
            const x = wx0 + j * (slotW + gapNow);
            return (
              <g key={j}>
                <rect x={x} y={WCB.y} width={slotW} height={WCB.h} rx={8} fill="none" stroke={C.dim} strokeWidth={2}
                  strokeDasharray="6 5" opacity={1 - fk} />
                <rect x={x} y={WCB.y} width={slotW} height={WCB.h} rx={8} fill={K.ret} opacity={0.9 * fk} />
                <text x={x + slotW / 2} y={WCB.y + WCB.h / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={26}
                  fontWeight={800} fill={fk > 0.5 ? C.bg : C.dim} opacity={1 - clamp01(mergeK * 2)}>
                  16 B
                </text>
              </g>
            );
          })}
          <rect x={wx0} y={WCB.y} width={WCB.w} height={WCB.h} rx={8} fill={K.ret} opacity={0.95 * clamp01(mergeK * 2 - 1)} />
          <rect x={wx0 - 4} y={WCB.y - 4} width={WCB.w + 8} height={WCB.h + 8} rx={11} fill="none" stroke="#ffffff" strokeWidth={4}
            opacity={flash} />
          <text x={960} y={WCB.y + WCB.h / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={30} fontWeight={800}
            fill={C.bg} opacity={clamp01(mergeK * 2 - 1)}>
            64 B
          </text>
        </g>

        {/* T4–T5: sanity-check chart */}
        <text x={(CH.x0 + CH.x1) / 2} y={232} textAnchor="middle" fontSize={26} fontWeight={600} fill={C.muted} opacity={chartK}>
          forward rate (M/s, median)
        </text>
        <BarChart x0={CH.x0} x1={CH.x1} y0={CH.y0} y1={CH.y1} bars={BARS_E0} max={CH.max} size={32} labelSize={26} barW={116} opacity={chartK}
          k={(i) => (i === 0 ? prog(t, T[4] + 5.0, 1.0) : i === 1 ? prog(t, T[4] + 8.4, 1.0) : 0)} />
        <BarChart x0={CH.x0} x1={CH.x1} y0={CH.y0} y1={CH.y1} bars={BARS_UNPINNED} max={CH.max} size={32} labelSize={26} barW={116}
          opacity={prog(t, T[5] + 0.2, 0.4)} k={(i) => (i === 2 ? prog(t, T[5] + 0.5, 1.0) : 0)} />
        <text x={CH.x0 + ((CH.x1 - CH.x0) * 5) / 6} y={CH.y0 + 100} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.slow}
          opacity={prog(t, T[5] + 0.2, 0.4)}>
          ~2.1 GHz
        </text>
        <g opacity={verdictK} transform={`translate(1462 724) scale(${0.85 + 0.15 * verdictK})`} fontFamily="Inter, sans-serif">
          <rect x={-128} y={-26} width={256} height={52} rx={26} fill="rgba(20,27,46,0.95)" stroke={K.fast} strokeWidth={2.5} />
          <Check x={-90} y={1} s={0.8} k={prog(t, T[4] + 10.6, 0.4)} w={4.5} />
          <text x={-66} y={2} dominantBaseline="central" fontSize={28} fontWeight={800} fill={K.fast}>
            no slowdown
          </text>
        </g>
      </Stage>

      <div style={{ opacity: fade(t, T[1] + 1.0, T[4] - 0.5) }}>
        <Note x={760} y={792} w={400}>
          <div style={{ textAlign: "center" }}>schematic · dots and cells not to scale</div>
        </Note>
      </div>

      {/* T1: what the forward path does NOT use */}
      <At x={960} y={232} center style={{ opacity: chipsK, display: "flex", gap: 22 }}>
        <StrikeChip strike={prog(t, T[1] + 10.3, 0.4)} size={28}>
          doorbell
        </StrikeChip>
        <StrikeChip strike={prog(t, T[1] + 10.8, 0.4)} size={28}>
          DMA
        </StrikeChip>
      </At>

      {/* T4: conditions of the sanity check */}
      <At x={50} y={128} style={{ ...appear(t, T[4] + 1.0), display: "flex", gap: 14, alignItems: "center" }}>
        <span style={{ fontSize: 22, fontWeight: 800, letterSpacing: 2, color: K.fixed }}>SANITY CHECK</span>
        <Chip>sender pinned 3.9 GHz</Chip>
        <Chip>card: 1 receive + 1 reply core</Chip>
      </At>

      {/* T5: our reading, its caveat, the lesson */}
      <At x={50} y={784} w={1160}>
        <div style={{ ...appear(t, T[5] + 8.2), display: "flex", gap: 14, alignItems: "center", fontSize: 32, fontWeight: 800 }}>
          <InfPill size={24} />
          <span>the clock, not PCIe</span>
        </div>
        <div style={{ ...appear(t, T[5] + 10.5), fontSize: 24, fontStyle: "italic", color: C.muted, marginTop: 8 }}>
          a one-way run at ~2.1 GHz was not measured
        </div>
      </At>
      <Takeaway x={1262} y={772} w={600} k={prog(t, T[5] + 12.6, 0.6)} label="LESSON">
        Pin every clock first.
      </Takeaway>
    </>
  );
}

export const S02: SceneDef = {
  id: "S02",
  title: "The direct path",
  stage: 0,
  source: "Xeon Gold 6438M ⇄ OCTEON 10 CN106 · PCIe Gen5 x4",
  dur: DUR,
  cues,
  C: Body,
};
