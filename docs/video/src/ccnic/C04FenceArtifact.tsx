import React from "react";
import { Cue, FONT, Pt, SceneDef, clamp01, lerp, prog, useT } from "../lib";
import { BigStat, Cell, Flow, Glass, P, Stage, TAB, Tag, Text, alpha, rise, win } from "./kit";
import { TagC, stream } from "./_CcSceneCost";
import { Brace, ends } from "./_CcSceneProof";

// <cues>
const cues: Cue[] = [
  [0.60, 11.22, "Both coherent-NIC papers start from one premise: CPU writes into device memory (**MMIO**) are slow.", "Both coherent nick papers start from the same premise: when the C P U writes directly into device memory, called M M I O, it is slow, and it only works with large batches."], // 0
  [11.57, 20.47, "But that measurement put a **fence after every write**: the core must wait until each write drains.", "But that measurement placed a fence after every single write. A fence makes the core stop and wait until each write has fully drained before it may issue the next one."], // 1
  [20.82, 29.90, "We reproduce it: one core, 64-byte writes, a fence after each → **8.1 million writes/s**.", "We can reproduce their number. One core writing sixty-four byte messages, with a fence after each one, reaches only eight point one million writes per second."], // 2
  [30.25, 41.69, "Drop the per-write fence and writes overlap in flight: the same core reaches **175.2 M/s**, **97.9%** of the link.", "Now remove the fence after each write, and the writes overlap in flight. The same single core reaches one hundred seventy-five million writes per second: ninety-seven point nine percent of what the link can carry."], // 3
  [42.04, 51.26, "The link was never the bottleneck: **the fence was**. But without it, what keeps messages **in order**?", "So the link was never the bottleneck. The fence was. But a fence is there for a reason: ordering. Without it, could the reader see messages out of order, or half written?"], // 4
  [51.61, 61.23, "First, a small message is **one 64-byte write**: `MOVDIR64B` sends all 64 bytes as a single unit.", "First, a small message is a single sixty-four byte write. The move dir sixty-four B instruction sends all sixty-four bytes as one unit, never in pieces."], // 5
  [61.58, 70.74, "Each message carries its **sequence number at both ends**. The reader takes it only when **both match**.", "Second, every message carries its sequence number at both ends. The reader takes a message only when head and tail match. Until then, it simply polls again."], // 6
  [71.09, 82.27, "Write-combined replies and longer messages may land **in pieces, in any order**: the reader waits for **every part**.", "A write-combined reply, or a message longer than one line, may land in pieces, in any order. The reader waits until every part checks out: no fence per line, and no half-read message."], // 7
  [82.62, 91.76, "Messages may also **overtake each other**. The reader goes **strictly in sequence**: an early one waits its turn.", "Third, messages may overtake each other: forty-three can land before forty-two. The reader goes strictly in sequence, so forty-three just waits its turn."], // 8
  [92.11, 98.43, "Across all our runs, **more than 2.5 billion** messages and frames: not one was read torn.", "Across all of our runs, more than two and a half billion messages and frames, not a single one was read torn."], // 9
  [98.78, 111.26, "Coherence would not remove this check: CC-NIC's descriptors still carry a **ready flag, written last**.", "And coherence would not remove this check. It keeps each cache line up to date, not whole messages, so C C nick's own descriptors still carry a ready flag, written last. Ordering is the reader's job either way."], // 10
];
const DUR = 112.66; // <dur>
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

// ---- beats 5-6: a 64 B line = 8 words of 8 B; the sequence number sits in the first and the last word
const WB = 88; // word box width
const WG = 5; // gap between words
const WH = 70; // line height
const UW = 8 * WB + 7 * WG; // one 64 B line (739 px)
const LY = 520; // shared centre: the line in the core, the link, slot 42
const BOX_W = UW + 48;
const BOX_Y = 320;
const BOX_H = 380;
const CPU_X = 80;
const NIC_X = 1920 - 80 - BOX_W;
const SLOT_W = UW + 12;
const SLOT_H = WH + 12;
const SLOT_X = NIC_X + (BOX_W - SLOT_W) / 2;
const PITCH = 100; // slot pitch in the ring
const UNIT_X0 = CPU_X + (BOX_W - UW) / 2; // the line, composed in the core
const UNIT_X1 = SLOT_X + 6; // the line, landed in slot 42
const RING_DX = -630; // beat 6: the ring slides left to make room for the reader's verdicts
const VERDICT_X = NIC_X + RING_DX + BOX_W + 40;

// ---- beat 4: the two hazards a fence prevents
const GX1 = 700;
const GX2 = 1220;
const GY = 624;

// ---- beat 7: one longer message in four parts
const PW = 190;
const PH = 84;
const PG = 16;
const P7X = 960 - (4 * PW + 3 * PG) / 2;
const P7Y = 500;
const ORDER7 = [0, 3, 1, 2]; // parts 1, 4, 2, 3 land in this order
const LAND7 = [4.6, 5.25, 5.9, 6.5]; // landing offsets after T[7]

// ---- beat 8: four ring slots, read in sequence
const SX8 = [630, 850, 1070, 1290];
const SY8 = 470;
const SW8 = 170;
const SH8 = 104;

// ---- beat 9: messages streaming past under the count
const STREAM9: Pt[] = [
  [600, 660],
  [1320, 660],
];

// ---- beat 10 glyphs
const SYW = 26; // word of the tiny cache line
const SYG = 4;
const SYH = 30;
const COPY_W = 8 * SYW + 7 * SYG;
const GLY_Y = 440;
const FW = 110; // descriptor field
const FH = 54;
const DX0 = 1130;
const READY_W = 140;
const KICK: React.CSSProperties = { fontSize: 15, letterSpacing: 2.6, fontWeight: 600, textTransform: "uppercase", marginBottom: 12 };

/** One 64 B line (adapted from the old BigCell): 8 word boxes, the sequence number in the first and the last. */
function Line64({
  x,
  y,
  head,
  tail,
  present = () => 1,
  ends: endGlow = 0,
  flash = 0,
  dim = false,
  tailAlert = 0,
  opacity = 1,
}: {
  x: number;
  y: number;
  head?: string;
  tail?: string;
  present?: (i: number) => number; // 0 = not written yet (dashed), 1 = written
  ends?: number; // lights the head and tail words
  flash?: number; // whole-line landing glow
  dim?: boolean; // already taken
  tailAlert?: number; // an empty tail outlined as the problem
  opacity?: number;
}) {
  if (opacity <= 0) return null;
  const col = dim ? P.faint : P.push;
  return (
    <g opacity={opacity} fontFamily={FONT}>
      {flash > 0 && <rect x={x - 12} y={y - 12} width={UW + 24} height={WH + 24} rx={20} fill={alpha(P.push, 0.35 * flash)} filter="url(#ccSoft)" />}
      {Array.from({ length: 8 }, (_, i) => {
        const wx = x + i * (WB + WG);
        const p = clamp01(present(i));
        const end = i === 0 || i === 7;
        const g = end && !dim ? endGlow : 0;
        const label = i === 0 ? head : i === 7 ? tail : undefined;
        return (
          <g key={i}>
            {p < 1 && (
              <g opacity={1 - p}>
                <rect x={wx} y={y} width={WB} height={WH} rx={10} fill="none" stroke={alpha(P.faint, 0.8)} strokeWidth={1.5} strokeDasharray="6 5" />
                {i === 7 && tailAlert > 0 && (
                  <rect x={wx} y={y} width={WB} height={WH} rx={10} fill={alpha(P.bad, 0.06)} stroke={P.bad} strokeWidth={2} strokeDasharray="6 5" opacity={tailAlert} />
                )}
              </g>
            )}
            {p > 0 && (
              <g opacity={p}>
                {g > 0 && <rect x={wx - 6} y={y - 6} width={WB + 12} height={WH + 12} rx={15} fill={alpha(col, 0.45 * Math.min(1, g))} filter="url(#ccSoft)" />}
                <rect
                  x={wx}
                  y={y}
                  width={WB}
                  height={WH}
                  rx={10}
                  fill={alpha(col, (end ? 0.16 : 0.08) + 0.24 * g + 0.14 * flash)}
                  stroke={alpha(col, Math.min(1, (dim ? 0.4 : end ? 0.8 : 0.45) + 0.2 * g))}
                  strokeWidth={1.5 + 1.2 * g}
                />
                {label ? (
                  <text x={wx + WB / 2} y={y + WH / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={600} fill={dim ? P.faint : P.ink}>
                    {label}
                  </text>
                ) : (
                  <g stroke={alpha(col, dim ? 0.3 : 0.5)} strokeWidth={2} strokeLinecap="round">
                    <line x1={wx + 18} y1={y + WH * 0.42} x2={wx + WB - 18} y2={y + WH * 0.42} />
                    <line x1={wx + 18} y1={y + WH * 0.6} x2={wx + WB - 32} y2={y + WH * 0.6} />
                  </g>
                )}
              </g>
            )}
          </g>
        );
      })}
    </g>
  );
}

/** Numbered message chip (SVG) centred at (x, y); `take` 0..1 greys it out once the reader has taken it. */
function SeqChip({
  x,
  y,
  n,
  color = P.push,
  w = 80,
  h = 50,
  size = 24,
  glow = 0,
  take = 0,
  opacity = 1,
}: {
  x: number;
  y: number;
  n: string;
  color?: string;
  w?: number;
  h?: number;
  size?: number;
  glow?: number;
  take?: number;
  opacity?: number;
}) {
  if (opacity <= 0) return null;
  const box = (fill: string, stroke: string, sw: number, ink: string, k: number) => (
    <g opacity={k}>
      <rect x={x - w / 2} y={y - h / 2} width={w} height={h} rx={10} fill={fill} stroke={stroke} strokeWidth={sw} />
      <text x={x} y={y + 1} textAnchor="middle" dominantBaseline="central" fontSize={size} fontWeight={700} fill={ink}>
        {n}
      </text>
    </g>
  );
  return (
    <g opacity={opacity} fontFamily={FONT}>
      {glow > 0 && <rect x={x - w / 2 - 8} y={y - h / 2 - 8} width={w + 16} height={h + 16} rx={14} fill={alpha(color, 0.45 * glow)} filter="url(#ccSoft)" />}
      {take < 1 && box(alpha(color, 0.18), color, 2, P.ink, 1 - take)}
      {take > 0 && box(alpha(P.faint, 0.12), alpha(P.faint, 0.7), 1.5, P.faint, take)}
    </g>
  );
}

/** Gentle poll pulse (SVG): a rounded outline around (x, y) that swells and fades every `per` s, cycles starting in [t0, t1]. */
function PollPulse({
  t,
  t0,
  t1 = 1e9,
  x,
  y,
  w,
  h,
  color,
  per = 1.2,
}: {
  t: number;
  t0: number;
  t1?: number;
  x: number;
  y: number;
  w: number;
  h: number;
  color: string;
  per?: number;
}) {
  if (t < t0) return null;
  const n = Math.floor((t - t0) / per);
  if (t0 + n * per > t1) return null;
  const ph = (t - t0 - n * per) / per;
  const g = 3 + 12 * ph;
  return (
    <rect x={x - w / 2 - g} y={y - h / 2 - g} width={w + 2 * g} height={h + 2 * g} rx={12 + g} fill="none" stroke={color} strokeWidth={2} opacity={0.75 * Math.sin(Math.PI * ph)} />
  );
}

function Body() {
  const t = useT();
  // ---- beat 0: the premise, in their words
  const quotes = win(t, T[0] + 0.2, T[1] + 0.1, 0.7);
  // ---- beats 1-3: one core writing into the NIC over the link
  const pipe = win(t, T[1] + 0.3, T[4] + 2.4, 0.7);
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
  const gauge = win(t, T[2] + 0.3, T[4] + 2.4, 0.6);
  const fillFrac = lerp(8.1 / 179, 175.2 / 179, prog(t, T[3] + 1.0, 1.1));
  const fillCol = t < T[3] + 1.0 ? P.bad : P.push;
  const slowNum = win(t, T[2] + 0.6, T[3] + 0.9, 0.5);
  const fastNum = prog(t, T[3] + 1.6, 0.6);
  // ---- beat 4: the obvious question, then the two things a fence prevents
  const q4 = win(t, T[4] + 2.9, T[5], 0.5);
  const oo = prog(t, T[4] + 7.0, 0.9); // 43 overtakes 42
  const hw = prog(t, T[4] + 8.1, 0.9); // a line caught half-written
  // ---- beats 5-6: one 64 B write lands whole; the reader checks both ends
  const s5 = win(t, T[5] + 0.25, T[6], 0.5); // core, link, MOVDIR64B
  const ring = win(t, T[5] + 0.25, T[7], 0.5); // the NIC ring and the line
  const ringDx = RING_DX * prog(t, T[6] + 0.3, 0.9);
  const lab5 = win(t, T[5] + 1.7, T[5] + 5.1, 0.5);
  const tag5 = prog(t, T[5] + 3.6, 0.5);
  const go = prog(t, T[5] + 5.5, 1.6); // sent as one unit, lands at T[5] + 7.1
  const land = prog(t, T[5] + 7.05, 0.15) * (1 - prog(t, T[5] + 7.3, 0.8));
  const ends6 = prog(t, T[6] + 1.9, 0.5) + 0.6 * prog(t, T[6] + 5.0, 0.2) * (1 - prog(t, T[6] + 5.25, 0.6));
  const ok6 = prog(t, T[6] + 5.3, 0.5);
  const in43 = prog(t, T[6] + 5.7, 0.3);
  const no6 = prog(t, T[6] + 6.6, 0.5);
  // ---- beat 7: a longer message lands in pieces, in any order
  const s7 = win(t, T[7] + 0.2, T[8], 0.5);
  const all7 = prog(t, T[7] + 6.5, 0.4);
  const flash7 = prog(t, T[7] + 6.5, 0.15) * (1 - prog(t, T[7] + 6.65, 0.7));
  const wait7 = win(t, T[7] + 4.1, T[7] + 6.55, 0.4);
  const ok7 = prog(t, T[7] + 7.0, 0.5);
  // ---- beat 8: an early message waits its turn
  const s8 = win(t, T[8] + 0.2, T[9], 0.5);
  const d43 = prog(t, T[8] + 1.9, 0.5); // 43 lands first, at T[8] + 2.4
  const d42 = prog(t, T[8] + 6.9, 0.5); // 42 lands at T[8] + 7.4
  const glow43 = prog(t, T[8] + 2.35, 0.1) * (1 - prog(t, T[8] + 2.5, 0.6));
  const glow42 = prog(t, T[8] + 7.35, 0.1) * (1 - prog(t, T[8] + 7.5, 0.6));
  const take42 = prog(t, T[8] + 7.55, 0.3);
  const take43 = prog(t, T[8] + 8.3, 0.3);
  const curX = lerp(SX8[1], SX8[2], prog(t, T[8] + 7.85, 0.4));
  const takeFlash = Math.max(
    prog(t, T[8] + 7.5, 0.15) * (1 - prog(t, T[8] + 7.7, 0.4)),
    prog(t, T[8] + 8.25, 0.15) * (1 - prog(t, T[8] + 8.45, 0.4)),
  );
  const seq8 = prog(t, T[8] + 5.2, 0.6);
  const wait8 = win(t, T[8] + 6.8, T[8] + 8.2, 0.4);
  const caretY = SY8 + SH8 / 2;
  // ---- beat 9: none read torn
  const s9 = win(t, T[9] + 0.2, T[10], 0.5);
  const zero = prog(t, T[9] + 0.2, 0.8);
  // ---- beat 10: what coherence gives vs what the reader still needs
  const s10 = prog(t, T[10] + 0.2, 0.6);
  const kickL = prog(t, T[10] + 0.5, 0.5);
  const kickR = prog(t, T[10] + 0.9, 0.5);
  const txL = prog(t, T[10] + 2.4, 0.5);
  const txR = prog(t, T[10] + 4.4, 0.5);
  const desc = prog(t, T[10] + 5.4, 0.5);
  const quote = prog(t, T[10] + 5.8, 0.6);
  const ready = prog(t, T[10] + 8.9, 0.4);
  const final = prog(t, T[10] + 9.8, 0.6);
  // coherence keeps the two copies of a line in sync: a word updates in one copy, then in the other
  const SYNC0 = T[10] + 2.9;
  const SYNC_PER = 1.3;
  const syncN = t < SYNC0 ? -1 : Math.floor((t - SYNC0) / SYNC_PER);
  const syncJ = syncN < 0 ? -1 : [2, 5, 1, 6, 3, 4][syncN % 6];
  const syncL = t - SYNC0 - syncN * SYNC_PER;
  const bump = (u: number) => clamp01(u / 0.12) * (1 - clamp01((u - 0.25) / 0.8));
  const syncA = syncN < 0 ? 0 : bump(syncL);
  const syncB = syncN < 0 ? 0 : bump(syncL - 0.2);
  const copyX = [540 - 42 - COPY_W, 540 + 42];
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
        <div style={{ fontSize: 34, lineHeight: 1.35, color: P.ink, marginTop: 18 }}>“MMIO … constrained bandwidth (&lt;0.3&nbsp;GB/s).”</div>
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
      <Tag x={846} y={548} color={P.bad} opacity={pipe * (1 - prog(t, T[3] + 0.4, 0.3)) * prog(t, T[1] + 0.8, 0.5)} size={16}>
        fence after every write
      </Tag>
      <Tag x={858} y={548} color={P.push} opacity={pipe * prog(t, T[3] + 0.75, 0.3)} size={16}>
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

      {/* ---------------- beat 4: the obvious question */}
      <Text x={260} y={318} w={1400} align="center" size={18} weight={600} color={P.sub} style={{ letterSpacing: 3.2, ...rise(q4) }}>
        THE OBVIOUS QUESTION
      </Text>
      <Text x={260} y={352} w={1400} align="center" size={64} weight={600} color={P.ink} style={{ letterSpacing: -0.5, ...rise(q4) }}>
        Without the fence, what keeps <span style={{ color: P.gold }}>order</span>?
      </Text>
      <Stage opacity={q4}>
        <g fontFamily={FONT}>
          {/* out of order: 43 overtakes 42 */}
          <g opacity={clamp01(oo * 2.5)}>
            <Flow
              pts={[
                [GX1 - 125, GY + 46],
                [GX1 + 128, GY + 46],
              ]}
              k={oo}
              color={P.faint}
              w={2}
              head={9}
            />
            {/* the reader the messages travel to: the one nearer it arrives first */}
            <line x1={GX1 + 142} y1={GY - 38} x2={GX1 + 142} y2={GY + 58} stroke={P.sub} strokeWidth={2.5} strokeLinecap="round" opacity={oo} />
            <SeqChip x={lerp(GX1 - 72, GX1 - 40, oo)} y={GY} n="42" color={P.bad} />
            <SeqChip x={lerp(GX1 - 190, GX1 + 72, oo)} y={GY - 76 * Math.min(1, 1.6 * Math.max(0, Math.sin(Math.PI * oo)))} n="43" color={P.bad} />
            <text x={GX1} y={GY + 96} textAnchor="middle" fontSize={26} fontWeight={600} fill={P.bad}>
              out of order?
            </text>
          </g>
          {/* half-written: the head is there, the tail is not */}
          <g opacity={clamp01(hw * 2.5)}>
            <rect x={GX2 - 160} y={GY - 28} width={100} height={56} rx={10} fill={alpha(P.bad, 0.16)} stroke={P.bad} strokeWidth={2} />
            <text x={GX2 - 110} y={GY + 1} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={600} fill={P.ink}>
              seq 43
            </text>
            <rect x={GX2 - 54} y={GY - 28} width={108} height={56} rx={10} fill="none" stroke={alpha(P.faint, 0.8)} strokeWidth={1.5} strokeDasharray="6 5" />
            <rect x={GX2 - 54} y={GY - 28} width={Math.max(0, 108 * 0.55 * clamp01((hw - 0.2) / 0.8))} height={56} rx={10} fill={alpha(P.bad, 0.16)} />
            <rect x={GX2 + 60} y={GY - 28} width={100} height={56} rx={10} fill="none" stroke={alpha(P.bad, 0.8)} strokeWidth={1.8} strokeDasharray="6 5" />
            <text x={GX2} y={GY + 96} textAnchor="middle" fontSize={26} fontWeight={600} fill={P.bad}>
              half-written?
            </text>
          </g>
        </g>
      </Stage>

      {/* ---------------- beats 5-6: one 64 B write lands whole; the reader checks both ends */}
      <Stage>
        {/* the core and a short link (beat 5) */}
        <g opacity={s5} fontFamily={FONT}>
          <g opacity={1 - 0.45 * go}>
            <rect x={CPU_X} y={BOX_Y} width={BOX_W} height={BOX_H} rx={18} fill={alpha(P.host, 0.06)} stroke={alpha(P.host, 0.55)} strokeWidth={1.5} />
            <text x={CPU_X + 25} y={BOX_Y + 34} fontSize={16} letterSpacing={2.4} fontWeight={600} fill={P.host}>
              HOST · CPU CORE
            </text>
          </g>
          {[-40, 40].map((o) => (
            <line key={o} x1={CPU_X + BOX_W} y1={LY + o} x2={NIC_X} y2={LY + o} stroke={P.hair2} strokeWidth={1.5} />
          ))}
          {[-27, -9, 9, 27].map((o) => (
            <line key={o} x1={CPU_X + BOX_W + 14} y1={LY + o} x2={NIC_X - 14} y2={LY + o} stroke={P.link} strokeWidth={1.5} opacity={0.6} />
          ))}
          <Brace x0={UNIT_X0} x1={UNIT_X0 + UW} y={LY + WH / 2 + 16} color={P.sub} k={lab5} opacity={lab5} />
          <text x={UNIT_X0 + UW / 2} y={LY + WH / 2 + 58} textAnchor="middle" fontSize={26} fontWeight={600} fill={P.ink} opacity={lab5}>
            one write · 64 B
          </text>
        </g>
        {/* the NIC ring (beats 5-6; slides left in beat 6) */}
        <g opacity={ring} transform={`translate(${ringDx} 0)`} fontFamily={FONT}>
          <rect x={NIC_X} y={BOX_Y} width={BOX_W} height={BOX_H} rx={18} fill={alpha(P.nic, 0.05)} stroke={alpha(P.nic, 0.55)} strokeWidth={1.5} />
          <text x={NIC_X + 25} y={BOX_Y + 34} fontSize={16} letterSpacing={2.4} fontWeight={600} fill={P.nic}>
            NIC · RING
          </text>
          {[-1, 0, 1].map((r) => (
            <rect key={r} x={SLOT_X} y={LY + r * PITCH - SLOT_H / 2} width={SLOT_W} height={SLOT_H} rx={14} fill="none" stroke={alpha(P.nic, 0.35)} strokeWidth={1.5} strokeDasharray="7 6" />
          ))}
          <Line64 x={UNIT_X1} y={LY - PITCH - WH / 2} head="seq 41" tail="seq 41" dim />
          <Line64 x={UNIT_X1} y={LY + PITCH - WH / 2} head="seq 43" present={(i) => (i === 7 ? 0 : prog(t, T[6] + 5.9 + i * 0.05, 0.3))} tailAlert={no6} opacity={in43} />
          <PollPulse t={t} t0={T[6] + 6.6} x={UNIT_X1 + 7 * (WB + WG) + WB / 2} y={LY + PITCH} w={WB} h={WH} color={P.bad} />
        </g>
        {/* the line itself: composed in the core, sent as one unit, lands whole */}
        <Line64 x={lerp(UNIT_X0, UNIT_X1, go) + ringDx} y={LY - WH / 2} head="seq 42" tail="seq 42" present={(i) => prog(t, T[5] + 0.6 + i * 0.07, 0.35)} ends={ends6} flash={land} opacity={ring} />
      </Stage>
      <TagC x={(CPU_X + BOX_W + NIC_X) / 2} y={LY - 90} color={P.push} opacity={s5 * tag5} size={16}>
        MOVDIR64B
      </TagC>
      <Text x={VERDICT_X} y={LY - 19} size={28} weight={600} color={P.push} opacity={ring * ok6}>
        ✓ take it
      </Text>
      <Text x={VERDICT_X} y={LY + PITCH - 19} size={28} weight={600} color={P.bad} opacity={ring * no6}>
        ✕ not yet · poll again
      </Text>

      {/* ---------------- beat 7: a longer message lands in pieces, in any order */}
      <TagC x={960} y={300} opacity={s7 * prog(t, T[7] + 0.3, 0.5)}>
        write-combined reply · longer message
      </TagC>
      <Stage opacity={s7}>
        <g fontFamily={FONT}>
          <rect x={P7X - 18} y={P7Y - PH / 2 - 18} width={4 * PW + 3 * PG + 36} height={PH + 36} rx={22} fill="rgba(255,255,255,0.02)" stroke={P.hair2} strokeWidth={1.5} />
          <rect x={P7X - 18} y={P7Y - PH / 2 - 18} width={4 * PW + 3 * PG + 36} height={PH + 36} rx={22} fill="none" stroke={alpha(P.push, 0.7)} strokeWidth={2} opacity={all7} />
          {[0, 1, 2, 3].map((j) => {
            const x = P7X + j * (PW + PG) + PW / 2;
            const landT = T[7] + LAND7[ORDER7.indexOf(j)];
            const d = prog(t, landT - 0.5, 0.5);
            const glow = prog(t, landT - 0.05, 0.1) * (1 - prog(t, landT + 0.1, 0.6));
            return (
              <g key={j}>
                <g opacity={prog(t, T[7] + 0.6 + j * 0.12, 0.4) * (1 - d)}>
                  <rect x={x - PW / 2} y={P7Y - PH / 2} width={PW} height={PH} rx={12} fill="none" stroke={alpha(P.faint, 0.8)} strokeWidth={1.5} strokeDasharray="7 6" />
                  <text x={x} y={P7Y + 1} textAnchor="middle" dominantBaseline="central" fontSize={30} fontWeight={600} fill={P.faint}>
                    {j + 1}
                  </text>
                </g>
                {d > 0 && <SeqChip x={x} y={P7Y - 130 * (1 - d)} n={String(j + 1)} w={PW} h={PH} size={30} glow={Math.max(glow, flash7)} opacity={clamp01(d * 3)} />}
              </g>
            );
          })}
          <text x={960} y={P7Y + PH / 2 + 82} textAnchor="middle" fontSize={26} fill={P.sub} opacity={wait7 * (0.72 + 0.28 * Math.cos(((t - T[7] - 4.1) / 1.2) * 2 * Math.PI))}>
            waiting
          </text>
          <text x={960} y={P7Y + PH / 2 + 82} textAnchor="middle" fontSize={28} fontWeight={600} fill={P.push} opacity={ok7}>
            ✓ every part: take it
          </text>
        </g>
      </Stage>
      <TagC x={960} y={P7Y + PH / 2 + 120} color={P.push} opacity={s7 * prog(t, T[7] + 8.1, 0.5)}>
        no fence per line
      </TagC>

      {/* ---------------- beat 8: messages may overtake; the reader goes in sequence */}
      <Stage opacity={s8}>
        <g fontFamily={FONT}>
          {SX8.map((x, i) => (
            <g key={i}>
              <rect x={x - SW8 / 2} y={SY8 - SH8 / 2} width={SW8} height={SH8} rx={14} fill="rgba(255,255,255,0.015)" stroke={alpha(P.nic, 0.4)} strokeWidth={1.5} strokeDasharray="7 6" />
              <text x={x} y={SY8 + 1} textAnchor="middle" dominantBaseline="central" fontSize={30} fontWeight={600} fill={P.faint} opacity={1 - clamp01(2 * [1, d42, d43, 0][i])}>
                {41 + i}
              </text>
            </g>
          ))}
          <SeqChip x={SX8[0]} y={SY8} n="41" w={150} h={84} size={30} take={1} />
          {d43 > 0 && <SeqChip x={SX8[2]} y={SY8 - 210 * (1 - d43)} n="43" w={150} h={84} size={30} glow={glow43} take={take43} opacity={clamp01(d43 * 3)} />}
          {d42 > 0 && <SeqChip x={SX8[1]} y={SY8 - 210 * (1 - d42)} n="42" w={150} h={84} size={30} glow={glow42} take={take42} opacity={clamp01(d42 * 3)} />}
          <PollPulse t={t} t0={T[8] + 2.6} t1={T[8] + 6.0} x={SX8[1]} y={SY8} w={SW8} h={SH8} color={P.sub} per={1.25} />
          {/* the reader's cursor */}
          <path d={`M${curX - 12} ${caretY + 36} L${curX} ${caretY + 18} L${curX + 12} ${caretY + 36} Z`} fill={P.ink} />
          <path d={`M${curX - 12} ${caretY + 36} L${curX} ${caretY + 18} L${curX + 12} ${caretY + 36} Z`} fill={P.push} opacity={takeFlash} />
          <text x={curX} y={caretY + 66} textAnchor="middle" fontSize={22} fill={P.sub}>
            reader
          </text>
          <Flow
            pts={[
              [SX8[0] - 85, 650],
              [SX8[3] + 85, 650],
            ]}
            k={seq8}
            color={P.push}
            w={2.5}
            head={11}
            opacity={0.8}
          />
          <text x={960} y={692} textAnchor="middle" fontSize={26} fontWeight={600} fill={P.push} opacity={seq8}>
            read in sequence
          </text>
        </g>
      </Stage>
      <TagC x={SX8[2]} y={SY8 - SH8 / 2 - 50} opacity={s8 * wait8}>
        43 waits its turn
      </TagC>

      {/* the schematic beats (4-8) */}
      <Tag x={96} y={826} opacity={0.9 * win(t, T[4] + 7.0, T[5], 0.5)}>
        schematic
      </Tag>
      <Tag x={96} y={826} opacity={0.9 * win(t, T[5] + 0.5, T[7], 0.5)}>
        schematic · one 64-byte message
      </Tag>
      <Tag x={96} y={826} opacity={0.9 * win(t, T[7] + 0.5, T[9], 0.5)}>
        schematic
      </Tag>

      {/* ---------------- beat 9: none read torn */}
      <BigStat x={960} y={360} w={1000} align="center" value="0" unit="torn" size={140} color={P.gold} label="in more than 2.5 billion messages and frames" opacity={s9} dy={(1 - zero) * 16} />
      <Stage opacity={s9}>
        <line x1={STREAM9[0][0]} y1={STREAM9[0][1]} x2={STREAM9[1][0]} y2={STREAM9[1][1]} stroke={P.hair2} strokeWidth={1.5} />
        {stream(t, T[9] + 0.5, T[10] + 1, 0.2, 2.6).map(({ i, k }) => (
          <Cell key={i} pts={STREAM9} k={k} w={26} h={16} color={P.push} opacity={0.55 * ends(k, 0.12, 0.12)} />
        ))}
      </Stage>

      {/* ---------------- beat 10: coherence keeps lines fresh; the reader still needs a whole-message mark */}
      <Glass x={150} y={230} w={780} h={330} style={rise(s10)}>
        <div style={{ ...KICK, color: P.coh, opacity: kickL }}>Coherence gives</div>
        <div style={{ fontSize: 30, fontWeight: 500, color: P.ink, ...rise(txL, 10) }}>each cache line up to date</div>
      </Glass>
      <Glass x={990} y={230} w={780} h={330} style={rise(s10)}>
        <div style={{ ...KICK, color: P.push, opacity: kickR }}>The reader still needs</div>
        <div style={{ fontSize: 30, fontWeight: 500, color: P.ink, ...rise(txR, 10) }}>a mark that the whole message is there</div>
      </Glass>
      <Stage opacity={s10}>
        {/* one line, two copies, kept in sync */}
        <g fontFamily={FONT} opacity={txL}>
          {copyX.map((x0, c) => (
            <g key={c}>
              {Array.from({ length: 8 }, (_, i) => {
                const hot = i === syncJ ? (c === 0 ? syncA : syncB) : 0;
                return <rect key={i} x={x0 + i * (SYW + SYG)} y={GLY_Y - SYH / 2} width={SYW} height={SYH} rx={5} fill={alpha(P.coh, 0.12 + 0.6 * hot)} stroke={alpha(P.coh, 0.5 + 0.4 * hot)} strokeWidth={1.5} />;
              })}
              <text x={x0 + COPY_W / 2} y={GLY_Y + 56} textAnchor="middle" fontSize={15} letterSpacing={2} fontWeight={600} fill={c === 0 ? alpha(P.host, 0.85) : alpha(P.nic, 0.85)}>
                {c === 0 ? "HOST CACHE" : "NIC CACHE"}
              </text>
            </g>
          ))}
          <Flow
            pts={[
              [506, GLY_Y - 8],
              [574, GLY_Y - 8],
            ]}
            color={P.coh}
            w={2}
            head={8}
            opacity={0.5 + 0.5 * syncA}
          />
          <Flow
            pts={[
              [574, GLY_Y + 8],
              [506, GLY_Y + 8],
            ]}
            color={P.coh}
            w={2}
            head={8}
            opacity={0.5 + 0.5 * syncB}
          />
        </g>
        {/* a descriptor: the fields first, the ready flag last */}
        <g fontFamily={FONT} opacity={desc}>
          {[0, 1, 2].map((i) => {
            const wf = prog(t, T[10] + 6.2 + 0.5 * i, 0.35);
            return <rect key={i} x={DX0 + i * (FW + 10)} y={GLY_Y - FH / 2} width={FW} height={FH} rx={9} fill={alpha(P.push, 0.05 + 0.2 * wf)} stroke={alpha(P.push, 0.3 + 0.35 * wf)} strokeWidth={1.5} />;
          })}
          <rect x={DX0 + 3 * (FW + 10) - 8} y={GLY_Y - FH / 2 - 8} width={READY_W + 16} height={FH + 16} rx={14} fill={alpha(P.push, 0.45 * ready)} filter="url(#ccSoft)" />
          <rect x={DX0 + 3 * (FW + 10)} y={GLY_Y - FH / 2} width={READY_W} height={FH} rx={9} fill="none" stroke={alpha(P.faint, 0.9)} strokeWidth={1.5} strokeDasharray="6 5" opacity={1 - ready} />
          <rect x={DX0 + 3 * (FW + 10)} y={GLY_Y - FH / 2} width={READY_W} height={FH} rx={9} fill={alpha(P.push, 0.85)} stroke={P.push} strokeWidth={1.5} opacity={ready} />
          <text x={DX0 + 3 * (FW + 10) + READY_W / 2} y={GLY_Y + 1} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={600} fill={P.faint} opacity={1 - ready}>
            ready
          </text>
          <text x={DX0 + 3 * (FW + 10) + READY_W / 2} y={GLY_Y + 1} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={700} fill={P.bg0} opacity={ready}>
            ready
          </text>
          <text x={DX0 + (3 * (FW + 10) + READY_W) / 2} y={GLY_Y + 56} textAnchor="middle" fontSize={15} letterSpacing={2} fontWeight={600} fill={P.faint}>
            DESCRIPTOR
          </text>
        </g>
      </Stage>
      <Text x={990} y={586} w={780} size={22} color={P.sub} style={rise(s10 * quote, 10)}>
        “…a ready flag, which the host sets after other descriptor fields are&nbsp;written.” <span style={{ color: P.coh, whiteSpace: "nowrap" }}>— CC-NIC §3.2</span>
      </Text>
      <Text x={360} y={716} w={1200} align="center" size={40} weight={600} color={P.gold} style={rise(s10 * final, 12)}>
        Ordering is the reader’s job either way.
      </Text>
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
