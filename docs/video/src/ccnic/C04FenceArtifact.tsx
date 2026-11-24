import React from "react";
import { Cue, FONT, Pt, SceneDef, lerp, prog, useT } from "../lib";
import { BigStat, Cell, Glass, P, Stage, TAB, Tag, Text, alpha, rise, win } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 11.62, "Both coherent-NIC papers start from one premise: CPU writes into device memory (**MMIO**) are slow.", "Both coherent N I C papers start from the same premise: when the C P U writes directly into device memory, called M M I O, it is slow, and it only works with large batches."], // 0
  [11.97, 20.87, "But that measurement put a **fence after every write**: the core must wait until each write drains.", "But that measurement placed a fence after every single write. A fence makes the core stop and wait until each write has fully drained before it may issue the next one."], // 1
  [21.22, 30.30, "We reproduce it: one core, 64-byte writes, a fence after each → **8.1 million writes/s**.", "We can reproduce their number. One core writing sixty-four byte messages, with a fence after each one, reaches only eight point one million writes per second."], // 2
  [30.65, 41.59, "Drop the per-write fence and writes overlap in flight: the same core reaches **175.2 M/s**, **97.9%** of the link.", "Now remove the per-write fence, and the writes overlap in flight. The same single core reaches one hundred seventy-five million writes per second: ninety-seven point nine percent of what the link can carry."], // 3
  [41.94, 51.70, "The link was never the bottleneck: **the fence was**. Order is checked at the receiver instead.", "So the link was never the bottleneck. The fence was. And ordering does not need a fence on every write: the receiver simply checks a sequence number at both ends of each message."], // 4
  [52.05, 58.31, "Across all our runs, **more than 2.5 billion** messages and frames, not one arrived torn.", "Across all of our runs, more than two and a half billion messages and frames, not a single one arrived torn."], // 5
];
const DUR = 59.71; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const PIPE_X0 = 420;
const PIPE_X1 = 1500;
const PIPE_Y = 470;
const PIPE_H = 96;
const LANE: Pt[] = [
  [PIPE_X0 - 20, PIPE_Y],
  [PIPE_X1 + 20, PIPE_Y],
];
const TRAVEL = 1.1; // seconds a write takes to cross the pipe in the drawing
const CEIL_W = PIPE_X1 - PIPE_X0;

/** One head/tail-tagged 64 B cell drawn large (SVG), for the receiver-side order check. */
function BigCell({ x, y, head, tail, ok, k }: { x: number; y: number; head: string; tail: string; ok: boolean; k: number }) {
  const w = 760;
  const h = 96;
  const col = ok ? P.push : P.bad;
  return (
    <g opacity={k} fontFamily={FONT}>
      <rect x={x} y={y} width={w} height={h} rx={14} fill={alpha(P.push, 0.06)} stroke={alpha(P.push, 0.5)} strokeWidth={1.5} />
      <rect x={x} y={y} width={120} height={h} rx={14} fill={alpha(col, 0.22)} stroke={col} strokeWidth={2} />
      <rect x={x + w - 120} y={y} width={120} height={h} rx={14} fill={alpha(col, 0.22)} stroke={col} strokeWidth={2} />
      <text x={x + 60} y={y + h / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={700} fill={P.ink}>
        {head}
      </text>
      <text x={x + w - 60} y={y + h / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={700} fill={P.ink}>
        {tail}
      </text>
      <text x={x + w / 2} y={y + h / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={24} fill={P.sub}>
        payload
      </text>
    </g>
  );
}

function Body() {
  const t = useT();
  // ---- beat 0: the premise, in their words
  const quotes = win(t, T[0] + 0.2, T[1] + 0.1, 0.7);
  // ---- beats 1-3: one core writing into the NIC over the link
  const pipe = win(t, T[1] + 0.3, T[4] + 0.1, 0.7);
  const fenced = t < T[3] + 0.4;
  const freeK = prog(t, T[3] + 0.4, 0.6);
  // fenced: one write at a time, the next leaves only after the previous drained
  const fCycle = TRAVEL + 0.25;
  const fLocal = t - (T[1] + 1.0);
  const fK = fLocal < 0 ? -1 : (fLocal % fCycle) / TRAVEL;
  // unfenced: back-to-back writes fill the pipe
  const period = 0.085;
  const freeCells = Array.from({ length: 16 }, (_, i) => {
    const local = t - (T[3] + 0.4) - i * period;
    if (local < 0) return -1;
    return (local % (16 * period)) / TRAVEL;
  });
  const waitOn = fenced && fK >= 0 && fK <= 1 ? 1 - prog(t, T[3] + 0.2, 0.4) : 0;
  // ---- gauge
  const gauge = win(t, T[2] + 0.3, T[4] + 0.1, 0.6);
  const fillFrac = lerp(8.1 / 179, 175.2 / 179, prog(t, T[3] + 1.0, 1.1));
  const fillCol = t < T[3] + 1.0 ? P.bad : P.push;
  const slowNum = win(t, T[2] + 0.6, T[3] + 0.9, 0.5);
  const fastNum = prog(t, T[3] + 1.6, 0.6);
  // ---- beats 4-5: order is checked at the receiver
  const order = win(t, T[4] + 0.3, T[5] + 0.2, 0.7);
  const torn = prog(t, T[4] + 3.4, 0.6);
  const zero = prog(t, T[5] + 0.3, 0.8);

  return (
    <>
      {/* ---------------- beat 0: quote cards */}
      <Glass x={150} y={300} w={760} h={300} kicker="CC-NIC · ASPLOS '24 · §2.2" color={P.coh} style={rise(quotes)}>
        <div style={{ fontSize: 34, lineHeight: 1.35, color: P.ink, marginTop: 18 }}>
          “… cannot achieve high throughput without extensive batching.”
        </div>
        <div style={{ fontSize: 21, color: P.sub, marginTop: 22 }}>MMIO writes to the NIC, one core</div>
      </Glass>
      <Glass x={1010} y={300} w={760} h={300} kicker="CXL-NIC · MICRO '25 · §1" color={P.coh} style={rise(quotes * prog(t, T[0] + 3.0, 0.6))}>
        <div style={{ fontSize: 34, lineHeight: 1.35, color: P.ink, marginTop: 18 }}>“MMIO … constrained bandwidth (&lt;0.3 GB/s).”</div>
        <div style={{ fontSize: 21, color: P.sub, marginTop: 22 }}>CPU-to-device writes over PCIe</div>
      </Glass>

      {/* ---------------- beats 1-3: the write pipe */}
      <Stage opacity={pipe}>
        <g fontFamily={FONT}>
          {/* host core */}
          <rect x={150} y={404} width={250} height={132} rx={18} fill={alpha(P.host, 0.08)} stroke={alpha(P.host, 0.6)} strokeWidth={1.5} />
          <text x={175} y={436} fontSize={16} letterSpacing={2.4} fontWeight={600} fill={P.host}>
            HOST · 1 CORE
          </text>
          <text x={275} y={490} textAnchor="middle" dominantBaseline="central" fontSize={26} fill={P.ink}>
            CPU core
          </text>
          {/* the link */}
          <rect x={PIPE_X0} y={PIPE_Y - PIPE_H / 2} width={PIPE_X1 - PIPE_X0} height={PIPE_H} rx={PIPE_H / 2} fill="rgba(255,255,255,0.025)" stroke={P.hair2} strokeWidth={1.5} />
          {[-27, -9, 9, 27].map((o) => (
            <line key={o} x1={PIPE_X0 + 30} y1={PIPE_Y + o} x2={PIPE_X1 - 30} y2={PIPE_Y + o} stroke={P.link} strokeWidth={1.5} opacity={0.6} />
          ))}
          <text x={(PIPE_X0 + PIPE_X1) / 2} y={PIPE_Y - PIPE_H / 2 - 22} textAnchor="middle" fontSize={17} letterSpacing={2} fontWeight={600} fill={P.sub}>
            PCIe GEN5 ×4 · 64-BYTE WRITES
          </text>
          {/* NIC */}
          <rect x={1520} y={404} width={250} height={132} rx={18} fill={alpha(P.nic, 0.08)} stroke={alpha(P.nic, 0.6)} strokeWidth={1.5} />
          <text x={1545} y={436} fontSize={16} letterSpacing={2.4} fontWeight={600} fill={P.nic}>
            NIC
          </text>
          <text x={1645} y={490} textAnchor="middle" dominantBaseline="central" fontSize={26} fill={P.ink}>
            NIC memory
          </text>
          {/* writes in flight */}
          {fenced && fK >= 0 && fK <= 1 && <Cell pts={LANE} k={fK} color={P.push} />}
          {!fenced && freeCells.map((k, i) => <Cell key={i} pts={LANE} k={k} color={P.push} opacity={freeK} />)}
        </g>
        {/* throughput gauge against the link ceiling */}
        <g opacity={gauge} fontFamily={FONT}>
          <rect x={PIPE_X0} y={640} width={CEIL_W} height={20} rx={10} fill="rgba(255,255,255,0.05)" />
          <rect x={PIPE_X0} y={640} width={CEIL_W * fillFrac} height={20} rx={10} fill={fillCol} />
          <line x1={PIPE_X1} y1={626} x2={PIPE_X1} y2={674} stroke={P.sub} strokeWidth={2} />
          <text x={PIPE_X1} y={700} textAnchor="end" fontSize={20} fill={P.sub}>
            link ceiling for 64 B writes · 179 M/s
          </text>
        </g>
      </Stage>
      <Tag x={150} y={572} color={P.bad} opacity={pipe * waitOn} size={16}>
        fence · wait for drain
      </Tag>
      <Tag x={846} y={548} color={P.bad} opacity={pipe * (1 - freeK) * prog(t, T[1] + 0.8, 0.5)} size={16}>
        fence after every write
      </Tag>
      <Tag x={858} y={548} color={P.push} opacity={pipe * freeK} size={16}>
        no per-write fence
      </Tag>
      <BigStat x={PIPE_X0} y={724} w={560} value="8.1" unit="M writes/s" size={60} color={P.bad} label="one core, fence after every write" opacity={gauge * slowNum} />
      <BigStat x={PIPE_X0} y={724} w={560} value="175.2" unit="M writes/s" size={60} color={P.push} label="the same core, no per-write fence" opacity={gauge * fastNum} />
      <Text x={1060} y={730} w={440} align="right" size={30} weight={600} color={P.ink} opacity={gauge * prog(t, T[3] + 2.6, 0.6)}>
        <span style={{ color: P.push, ...TAB }}>97.9%</span> of the link
      </Text>
      <Text x={1060} y={778} w={440} align="right" size={22} color={P.sub} opacity={gauge * prog(t, T[3] + 3.6, 0.6)}>
        <span style={{ ...TAB }}>21.6×</span> the fenced rate
      </Text>

      {/* ---------------- beats 4-5: order checked at the receiver */}
      <Stage opacity={order}>
        <BigCell x={580} y={340} head="seq 42" tail="seq 42" ok k={prog(t, T[4] + 0.5, 0.6)} />
        <BigCell x={580} y={520} head="seq 43" tail="seq 42" ok={false} k={torn} />
      </Stage>
      <Tag x={580} y={298} color={P.push} opacity={order * prog(t, T[4] + 0.9, 0.5)}>
        head
      </Tag>
      <Tag x={1270} y={298} color={P.push} opacity={order * prog(t, T[4] + 0.9, 0.5)}>
        tail
      </Tag>
      <Text x={1370} y={368} size={26} weight={600} color={P.push} opacity={order * prog(t, T[4] + 1.6, 0.5)}>
        ✓ match: accept
      </Text>
      <Text x={1370} y={548} w={420} size={26} weight={600} color={P.bad} opacity={order * torn}>
        ✕ not complete yet
      </Text>
      <Text x={1370} y={584} w={420} size={20} color={P.sub} opacity={order * torn}>
        the receiver keeps polling
      </Text>
      <Tag x={580} y={662} opacity={order * 0.9}>
        schematic · one 64-byte message
      </Tag>
      <BigStat x={960} y={360} w={1000} align="center" value="0" unit="torn" size={140} color={P.gold} label="in more than 2.5 billion messages and frames" opacity={zero} dy={(1 - zero) * 16} />
    </>
  );
}

export const C04: SceneDef = {
  id: "C04",
  title: "Is PCIe really slow?",
  stage: 1,
  source: "Measured: 1 host core · 64 B writes · batch 1 · PCIe Gen5 ×4",
  dur: DUR,
  cues,
  C: Body,
};
