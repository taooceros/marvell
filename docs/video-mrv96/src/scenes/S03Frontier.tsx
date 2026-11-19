import { Arrow, C, Cue, K, Note, SceneDef, Stage, clamp01, fade, lerp, prog, useRealT, useT } from "../lib";
import { BarChart, Brace, CellState, InfTag, KnobBar, Meter, Takeaway } from "./kit";
import { Cursor, HostWriter, LegendCell, RS, RS_RIGHT, ScrollStrip, TimerArc, XMark, absC, absX, hash01 } from "./_Mrv96SceneOneWay";

// <cues>
const cues: Cue[] = [
  [0.60, 8.18, "**Experiment 1.** One-way traffic only. When a card core catches up with the newest writes, should it keep polling, or step back?", "Experiment one. One-way traffic only. When a card core catches up with the newest writes, should it keep polling, or step back?"], // 0
  [8.53, 18.37, "Fixed: the host core sends; **two card cores** receive, each taking up to **32 messages per poll**. Fewer than 32 means it reached the frontier.", "Fixed: the host core sends, and two card cores receive, each taking up to thirty-two messages per poll. Fewer than thirty-two means it has reached the frontier."], // 1
  [18.72, 29.90, "The frontier is fuzzy: host counters show **~440 writes in flight**, each queued **~3.5 µs**. **[INF]** They land out of order, leaving holes.", "That frontier is fuzzy. Host counters show about four hundred forty writes in flight, each queued about three and a half microseconds. Our reading: they land out of order, leaving holes."], // 2
  [30.25, 41.93, "Knob = how long the core pauses when it catches up. **0 µs**: it never pauses, **93.6%** of polls come back empty, and throughput sticks at **128.7 M/s**.", "The knob: how long the core pauses when it catches up. At zero it never pauses: ninety-three point six percent of its polls come back empty, and throughput sticks at one hundred twenty-eight point seven."], // 3
  [42.28, 51.80, "**1 µs**: barely better, **132.2 M/s**. One microsecond is shorter than the ~3 µs landing window, so the core wakes up inside it again.", "One microsecond: barely better, one hundred thirty-two point two. That is shorter than the three microsecond landing window, so the core wakes up inside it again."], // 4
  [52.15, 64.57, "**3 µs**: **161.6 M/s**. The band has landed; the core grabs **~900 messages** per wake-up, and the host's write queue drops from **447 to 360**.", "Three microseconds: one hundred sixty-one point six. The band has landed, the core grabs about nine hundred messages per wake-up, and the host's write queue drops from four hundred forty-seven to three hundred sixty."], // 5
  [64.92, 74.92, "Now the host core itself is the limit, **~24 cycles per message**. A receiver glued to the frontier slows the sender; backing off gives **+26%**.", "Now the host core itself is the limit, about twenty-four cycles per message. A receiver glued to the frontier slows the sender. Backing off gives twenty-six percent."], // 6
];
const DUR = 76.32; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---------------------------------------------------------------- schematic ring model (script seconds)
// The window follows the host's write pointer (newest slot at the right). A slot lands D·(0.55..1.15) slots after it is
// written (out of order); the card cores consume landed slots in order and stop at the first hole (the frontier).
const RATE = 1.4; // slots written per second (schematic)
const head = (t: number) => 40 + RATE * t;
const depth = (t: number) => lerp(9, 7, prog(t, T[5] + 0.6, 3)); // band narrows when the cores back off 3 µs
const landed = (a: number, t: number) => head(t) - a >= depth(t) * (0.3 + 0.9 * hash01(a));
/** First slot not landed yet (the frontier). */
const frontier = (t: number) => {
  const h = head(t);
  for (let a = Math.floor(h) - 40; a <= h; a++) if (!landed(a, t)) return a;
  return Math.floor(h) + 1;
};
// Naps (schematic durations ∝ µs): 1 µs from T4, 3 µs from T5. WAKES = wake-up times; before WAKES[0] the cores spin.
const NAP1 = 2.4;
const NAP3 = 7.0;
const SWEEP = 0.8;
const WAKES: number[] = (() => {
  const w = [T[4] + 0.4];
  while (w[w.length - 1] + NAP1 < T[5] + 0.3) w.push(w[w.length - 1] + NAP1);
  w.push(T[5] + 0.3);
  while (w[w.length - 1] + NAP3 < DUR + 2) w.push(w[w.length - 1] + NAP3);
  return w;
})();
/** Joint `next` of the two card cores (absolute slot), index of the current wake-up and nap progress 0..1. */
function cursor(t: number) {
  if (t < WAKES[0]) return { next: frontier(t), k: -1, from: frontier(t), nap: 0 };
  let k = 0;
  while (k + 1 < WAKES.length && WAKES[k + 1] <= t) k++;
  const to = frontier(WAKES[k]);
  const from = k === 0 ? frontier(WAKES[0] - 0.01) : frontier(WAKES[k - 1]);
  const len = k + 1 < WAKES.length ? WAKES[k + 1] - WAKES[k] : NAP3;
  return { next: lerp(from, to, prog(t, WAKES[k], SWEEP)), k, from, nap: clamp01((t - WAKES[k] - SWEEP) / (len - SWEEP)) };
}

const CORE_A = C.orange;
const CORE_B = "#ffc58a";
const BELOW = RS.y + RS.ch; // bottom edge of the strip

// throughput bars (lower right)
const BX0 = 1060;
const BX1 = 1840;
const BY0 = 815;
const BY1 = 600;
const BMAX = 180;
const by = (v: number) => BY0 - ((BY0 - BY1) * v) / BMAX;
const BARS = [
  { label: "0 µs", value: 128.7, color: K.slow },
  { label: "1 µs", value: 132.2, color: C.orange },
  { label: "3 µs", value: 161.6, color: K.fast },
];
const BAR_AT = [T[3] + 2.4, T[4] + 1.2, T[5] + 1.2];

// host write queue meter (lower left)
const MX = 110;
const MW = 800;
const MY = 690;
const mx = (v: number) => MX + (MW * v) / 500;

function Body() {
  const t = useT();
  const rt = useRealT();
  const h = head(t);
  const cur = cursor(t);
  const nx = cur.next;
  const fr = frontier(t);
  const two = prog(t, T[1] + 0.3, 0.8);
  const holesOn = t >= T[2] + 0.8;

  const state = (a: number): CellState => {
    if (a > h) return "empty";
    if (a < nx) return "consumed";
    if (landed(a, t)) return "landed";
    if (!holesOn) return "inflight";
    for (let b = a + 1; b <= h; b++) if (landed(b, t)) return "hole";
    return "inflight";
  };

  // in-flight band = frontier .. newest
  const bandK = prog(t, T[2] + 0.3, 0.8);
  const bandX0 = absX(fr, h) - RS.gap;
  const bandX1 = Math.min(RS_RIGHT, absX(h, h) + RS.cw) + RS.gap;

  // two cursors side by side at the joint `next`
  const ax = absC(nx, h);
  const bx = lerp(ax, absC(nx + 1, h), two);
  const frX = absC(fr, h);

  const active = t < T[3] ? -1 : prog(t, T[4], 0.9) + prog(t, T[5], 0.9);
  const spinning = t >= T[3] && t < WAKES[0];
  const blink = Math.sin(rt * 2 * Math.PI * 3) > 0 ? 1 : 0.2;
  // after a 1 µs wake-up the run ends at a hole again
  const hitHole = cur.k >= 0 && t < T[5] + 0.3 ? fade(t, WAKES[cur.k] + SWEEP, WAKES[cur.k] + SWEEP + 0.9, 0.2) : 0;

  // meter: 447 → 433 → 360 (entries), time each 3.47 → 3.28 → 2.23 µs
  const q = lerp(lerp(447, 433, prog(t, T[4] + 0.4, 1)), 360, prog(t, T[5] + 0.6, 1.2));
  const phase = t < T[4] + 0.4 ? 0 : t < T[5] + 0.6 ? 1 : 2;
  const qCol = [K.slow, C.orange, K.fast][phase];
  const qText = ["447 · 3.47 µs", "433 · 3.28 µs", "360 · 2.23 µs"][phase];

  // T1: one poll takes a batch; the brace spans the run it just took
  const batchK = fade(t, T[1] + 1.0, T[2] - 0.45);
  // T5: the long jump of the latest 3 µs wake-up (the first one that follows a full 3 µs pause)
  const first3 = WAKES.indexOf(T[5] + 0.3) + 1;
  const jumpK = cur.k >= first3 ? fade(t, WAKES[cur.k], (WAKES[cur.k + 1] ?? 1e9) - 0.45, 0.3) : 0;

  const fixed = ["one-way only", "core 40 · 3.9 GHz", "2 card cores", "≤ 32 msgs / poll"];

  return (
    <>
      <Stage>
        <HostWriter rt={rt} opacity={prog(t, T[0], 0.6)} />
        {bandK > 0 && (
          <rect x={bandX0} y={RS.y - 10} width={Math.max(0, bandX1 - bandX0)} height={RS.ch + 20} rx={12}
            fill={K.inflight + "22"} stroke={K.inflight} strokeWidth={2.5} strokeDasharray="8 6" opacity={bandK} />
        )}
        <ScrollStrip head={h} state={state} flicker={rt} clipId="s03strip" opacity={prog(t, T[0], 0.6)} />

        {/* which core took a consumed slot: A even, B odd */}
        {two > 0 &&
          Array.from({ length: RS.n + 2 }, (_, i) => {
            const a = Math.floor(h) - RS.n + i;
            const x = absC(a, h);
            if (a >= nx || x < RS.x + 10 || x > RS_RIGHT - 10) return null;
            return <circle key={a} cx={x} cy={RS.y + RS.ch / 2} r={7} fill={a % 2 === 0 ? CORE_A : CORE_B} opacity={two * 0.95} />;
          })}

        <Cursor x={ax} color={CORE_A} tag={two < 0.5 ? "next" : "A"} />
        <Cursor x={bx} color={CORE_B} tag="B" opacity={two} />

        {/* T1 */}
        <text x={RS.x} y={RS.y - 44} fontSize={26} fontWeight={700} fill={CORE_A} opacity={fade(t, T[1] + 0.5, T[2] - 0.45)}>
          A + B: alternate slots
        </text>
        <Brace x1={absX(nx - 6, h)} x2={absX(nx, h) - RS.gap} y={BELOW + 12} label="one poll: up to 32 msgs" color={CORE_A}
          opacity={batchK} size={26} />
        <text x={ax - 40} y={RS.y - 44} fontSize={26} fontWeight={800} fill={CORE_A}
          opacity={fade(t, T[1] + 4.5, T[2] - 0.45) * (0.7 + 0.3 * Math.sin(rt * 2 * Math.PI * 1.2))}
          textAnchor="end">
          fewer than 32 = caught up
        </text>

        {/* T2: the fuzzy frontier */}
        <text x={RS_RIGHT} y={BELOW + 52} textAnchor="end" fontSize={26} fontWeight={800} fill={K.inflight}
          opacity={fade(t, T[2] + 0.6, T[4] - 0.45)}>
          ~440 writes in flight · ~3.5 µs each
        </text>
        <text x={RS_RIGHT} y={BELOW + 52} textAnchor="end" fontSize={26} fontWeight={800} fill={K.inflight} opacity={prog(t, T[4], 0.5)}>
          still landing
        </text>
        <g opacity={prog(t, T[2] + 3.0, 0.6)}>
          <text x={RS_RIGHT - 88} y={BELOW + 92} textAnchor="end" fontSize={26} fontWeight={600} fill={C.text}>
            land out of order → holes
          </text>
          <InfTag x={RS_RIGHT - 74} y={BELOW + 68} />
        </g>

        {/* T3: 0 µs, glued to the frontier */}
        <XMark x={frX} y={RS.y + RS.ch / 2} s={13} w={6} opacity={spinning ? blink * prog(t, T[3] + 0.6, 0.3) : hitHole} />
        <text x={ax - 50} y={RS.y - 44} textAnchor="end" fontSize={28} fontWeight={800} fill={K.slow}
          opacity={fade(t, T[3] + 1.6, T[4] - 0.45)}>
          93.6 % of polls empty
        </text>

        {/* T4/T5: nap timer next to the cursors */}
        <g opacity={cur.k >= 0 ? 1 : 0}>
          <TimerArc x={ax - 76} y={RS.y - 52} r={20} k={cur.nap} color={K.wait} />
          <text x={ax - 108} y={RS.y - 44} textAnchor="end" fontSize={26} fontWeight={800} fill={K.wait}>
            {t < T[5] + 0.3 ? "pause 1 µs" : "pause 3 µs"}
          </text>
        </g>
        <g opacity={jumpK}>
          <Arrow x1={absC(cur.from, h)} y1={BELOW + 22} x2={ax} y2={BELOW + 22} color={K.fast} w={5} head={14}
            k={prog(t, WAKES[Math.max(0, cur.k)], SWEEP)} />
          <text x={absC(cur.from, h) - 10} y={BELOW + 60} textAnchor="start" fontSize={28} fontWeight={800} fill={K.fast}>
            ~900 per wake-up
          </text>
        </g>

        {/* legend (T0–T5) */}
        <g opacity={fade(t, T[0] + 1.0, T[6] - 0.45)}>
          <LegendCell x={110} y={470} s="consumed" label="taken by a card core" />
          <LegendCell x={480} y={470} s="landed" label="landed, ready" />
          <LegendCell x={110} y={530} s="inflight" label="host write in flight" opacity={prog(t, T[0] + 1.6, 0.6)} />
          <LegendCell x={480} y={530} s="hole" label="hole (older slot missing)" opacity={prog(t, T[2] + 3.0, 0.6)} />
        </g>

        {/* host write queue */}
        <g opacity={prog(t, T[2] + 1.2, 0.8)}>
          <Meter x={MX} y={MY} w={MW} value={q} max={500} color={qCol} label="host write queue (CHA): entries · time each"
            valueText={qText} marks={[{ at: 447 }, { at: 433 }, { at: 360 }]} />
          <text x={mx(447) + 6} y={MY + 56} fontSize={22} fill={C.muted}>447</text>
          <text x={mx(433) - 6} y={MY + 56} fontSize={22} fill={C.muted} textAnchor="end">433</text>
          <text x={mx(360)} y={MY + 56} fontSize={22} fill={C.muted} textAnchor="middle">360</text>
        </g>

        {/* throughput */}
        <g opacity={prog(t, T[3] + 1.2, 0.8)}>
          <text x={BX0} y={BY1 - 44} fontSize={24} fontWeight={600} fill={C.muted}>
            M/s (median) · 1 host core + 2 card cores
          </text>
          <BarChart x0={BX0} x1={BX1} y0={BY0} y1={BY1} bars={BARS} max={BMAX} k={(i) => prog(t, BAR_AT[i], 1.0)} />
        </g>
        <g opacity={prog(t, T[6] + 0.4, 0.8)}>
          <line x1={BX0} y1={by(161.6)} x2={BX1} y2={by(161.6)} stroke={K.host} strokeWidth={2.5} strokeDasharray="8 6" />
          <text x={BX0 + 8} y={by(161.6) - 12} fontSize={24} fill={K.host} fontWeight={700}>
            host core limit · ~24 cycles/msg
          </text>
        </g>
        <g opacity={prog(t, T[6] + 3.2, 0.6)}>
          <line x1={BX0 + 200} y1={by(128.7)} x2={1585} y2={by(128.7)} stroke={C.text} strokeWidth={2} strokeDasharray="4 5" />
          <Arrow x1={1585} y1={by(128.7)} x2={1585} y2={by(161.6) + 4} color={C.text} w={4} head={12} k={prog(t, T[6] + 3.2, 0.8)} />
          <text x={1585} y={by(128.7) + 34} textAnchor="middle" fontSize={30} fontWeight={800} fill={C.text}>+26 %</text>
        </g>
      </Stage>
      <KnobBar fixed={fixed} knob="pause when caught up" options={["0 µs", "1 µs", "3 µs"]} active={active} k={prog(t, T[0], 0.6)} />
      <Takeaway x={80} y={458} w={900} k={prog(t, T[6] + 5.0, 0.8)} color={K.fast}>
        A receiver glued to the frontier slows the sender — back off ~3 µs.
      </Takeaway>
      <Note x={60} y={858}>ring strip is schematic (real ring: 65,533 slots)</Note>
    </>
  );
}

export const S03: SceneDef = {
  id: "S03",
  title: "Exp 1 · Chasing the frontier",
  stage: 1,
  source: "MRV-61 · logs/mrv61-2026-11-18 · 27 runs × 2×10⁸ msgs",
  dur: DUR,
  cues,
  C: Body,
};
