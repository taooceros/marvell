import { Arrow, C, Cue, K, Note, SceneDef, Stage, clamp01, fade, lerp, prog, useRealT, useT } from "../lib";
import { CellState, HBarChart, InfTag, KnobBar, Meter, Takeaway } from "./kit";
import { CheckMark, Cursor, HostWriter, RS, RS_RIGHT, ScrollStrip, TimerArc, XMark, absC, absX, hash01 } from "./_Mrv96SceneOneWay";

// <cues>
const cues: Cue[] = [
  [0.60, 12.24, "**Experiment 2.** Why does polling at the frontier hurt? Suspect A: **where** the core reads. Suspect B: **how much** it loads the card's shared L3 cache.", "Experiment two. Why does polling at the frontier hurt? Suspect A is position: where the core reads. Suspect B is bandwidth: how hard it loads the card's shared L 3 cache."], // 0
  [12.59, 22.15, "Fixed: the fast case, pausing **3 µs**. Knob = what the core reads during each pause. Reading nothing: **160.4 M/s**.", "Fixed: the fast case, pausing three microseconds. The knob: what the core reads during each pause. Reading nothing gives one hundred sixty point four."], // 1
  [22.50, 35.78, "Read cold memory **outside** the ring: L3 requests rise from **608 to 815 M/s**, past **1 billion/s** with page strides. Throughput: **160.4–161.4**.", "Read cold memory outside the ring: requests into the shared cache rise from six hundred eight to eight hundred fifteen million per second, past a billion with page strides. Throughput stays near one hundred sixty-one."], // 2
  [36.13, 46.21, "Read old slots **half a lap behind**: **162.0**. Slots **just behind** the frontier, already landed: **160.2**. Bandwidth is not the problem.", "Old slots half a lap behind: one hundred sixty-two. Slots just behind the frontier, already landed: one hundred sixty point two. Bandwidth is not the problem."], // 3
  [46.56, 55.30, "Now read just **ahead** of the frontier, inside the ~3 µs band still landing. Throughput collapses to **113.4 M/s**.", "Now read just ahead of the frontier, inside the three microsecond band that is still landing. Throughput collapses to one hundred thirteen point four."], // 4
  [55.65, 69.03, "**[INF]** Touching lines still being written forces snoops or blocks inbound writes; host queueing hits **4.0 µs > 3 µs**, so pauses end too early.", "Our reading: touching lines still being written forces cache snoops, or blocks inbound writes. Host queueing grows to four microseconds, longer than the pause, so pauses end too early, and it feeds on itself."], // 5
  [69.38, 76.66, "Verdict: **position, not bandwidth**. A receiver should stay behind the landing band; how much else it reads barely matters.", "The verdict: position, not bandwidth. A receiver should stay behind the landing band. How much else it reads barely matters."], // 6
];
const DUR = 78.06; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---------------------------------------------------------------- schematic ring model (same strip as S03, 3 µs pauses)
const RATE = 1.4; // slots written per second (schematic)
const head = (t: number) => 40 + RATE * t;
// Reading inside the band (T4 on) slows the landing: the band widens.
const depth = (t: number) => lerp(7, 10.5, prog(t, T[4] + 1.0, 2.5) * (1 - prog(t, T[6] + 0.5, 2.5)));
const landed = (a: number, t: number) => head(t) - a >= depth(t) * (0.3 + 0.9 * hash01(a));
const frontier = (t: number) => {
  const h = head(t);
  for (let a = Math.floor(h) - 40; a <= h; a++) if (!landed(a, t)) return a;
  return Math.floor(h) + 1;
};
const NAP = 4.5; // one 3 µs pause (schematic)
const SWEEP = 0.8;
const W0 = T[0] + 0.4;
/** Joint `next` of the card cores: sweeps to the frontier at each wake-up, then pauses. */
function cursor(t: number) {
  const k = Math.max(0, Math.floor((t - W0) / NAP));
  const w = W0 + k * NAP;
  const to = frontier(w);
  const from = k === 0 ? to : frontier(w - NAP);
  return { next: lerp(from, to, prog(t, w, SWEEP)), nap: clamp01((t - w - SWEEP) / (NAP - SWEEP)) };
}

const CORE_A = C.orange;
const CORE_B = "#ffc58a";
const BELOW = RS.y + RS.ch;

// throughput bars (right column)
const HX0 = 1330;
const HX1 = 1770;
const HY1 = 652;
const HY0 = 862;
const BARS = [
  { label: "nothing", value: 160.4, color: K.fixed },
  { label: "cold memory", value: 160.4, color: K.wait },
  { label: "cold, page strides", value: 161.4, color: K.wait },
  { label: "half a lap behind", value: 162.0, color: K.landed },
  { label: "just behind", value: 160.2, color: K.landed },
  { label: "just ahead", value: 113.4, color: K.slow },
];
const BAR_AT = [T[1] + 2.0, T[2] + 2.5, T[2] + 8.5, T[3] + 1.2, T[3] + 5.2, T[4] + 2.2];

// cold buffer (left column)
const CB = { x: 110, y: 520, n: 14, w: 46, h: 64, gap: 12 };
// L3 meter (left column)
const LM = { x: 110, y: 720, w: 840, max: 1300 };
const lmx = (v: number) => LM.x + (LM.w * v) / LM.max;

/** Suspect card (SVG). verdict > 0 draws ✓, < 0 draws ✗ (|verdict| = opacity); sub2 crossfades over sub with subSwap. */
function Suspect({ x, y, w, title, sub, color, hl, verdict, sub2, subSwap = 0 }: {
  x: number; y: number; w: number; title: string; sub: string; color: string; hl: number; verdict: number; sub2?: string; subSwap?: number;
}) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={112} rx={16} fill="#141b2e" stroke={color} strokeWidth={2 + 3 * hl} />
      {hl > 0 && <rect x={x - 6} y={y - 6} width={w + 12} height={124} rx={20} fill="none" stroke={color} strokeWidth={2} opacity={hl * 0.5} />}
      <text x={x + 22} y={y + 42} fontSize={28} fontWeight={800} fill={color}>{title}</text>
      <text x={x + 22} y={y + 84} fontSize={24} fill={C.text} opacity={1 - subSwap}>{sub}</text>
      {sub2 && <text x={x + 22} y={y + 84} fontSize={24} fontWeight={700} fill={color} opacity={subSwap}>{sub2}</text>}
      {verdict > 0 && <CheckMark x={x + w - 40} y={y + 36} s={20} opacity={verdict} />}
      {verdict < 0 && <XMark x={x + w - 40} y={y + 36} s={16} w={6} opacity={-verdict} />}
    </g>
  );
}

function Body() {
  const t = useT();
  const rt = useRealT();
  const h = head(t);
  const cur = cursor(t);
  const nx = cur.next;
  const fr = frontier(t);

  const state = (a: number): CellState => {
    if (a > h) return "empty";
    if (a < nx) return "consumed";
    if (landed(a, t)) return "landed";
    for (let b = a + 1; b <= h; b++) if (landed(b, t)) return "hole";
    return "inflight";
  };
  const bandX0 = absX(fr, h) - RS.gap;
  const bandX1 = Math.min(RS_RIGHT, absX(h, h) + RS.cw) + RS.gap;
  const ax = absC(nx, h);
  const bx = absC(nx + 1, h);
  const stripK = prog(t, T[0] + 1.5, 0.8);

  // knob: nothing → cold memory → old slots → just behind → just ahead
  const active = t < T[1] ? -1 : prog(t, T[2], 0.8) + prog(t, T[3], 0.8) + prog(t, T[3] + 4.5, 0.8) + prog(t, T[4], 0.8);

  // probes under the strip (absolute slot ranges, attached to `next`)
  const behindK = fade(t, T[3] + 4.0, T[4] - 0.45);
  const aheadK = fade(t, T[4] + 0.6, T[6] - 0.45);
  const lapK = fade(t, T[3] + 0.3, T[3] + 4.0);
  const pulse = 0.75 + 0.25 * Math.sin(rt * 7);

  // L3 meter: 608 → 815 → 1033
  const l3 = lerp(lerp(608, 815, prog(t, T[2] + 2.5, 1.0)), 1033, prog(t, T[2] + 8.5, 1.0));
  const l3Text = t < T[2] + 2.5 ? "608 M/s" : t < T[2] + 8.5 ? "815 M/s" : t < T[3] ? "1033 M/s" : "1033 M/s (max tested)";
  const coldK = fade(t, T[2] + 0.3, T[3] - 0.45);
  const meterK = fade(t, T[1] + 1.0, T[5] - 0.45);
  const meterDim = 1 - 0.45 * prog(t, T[3] + 7.0, 0.8);
  // cold probe position: 64 B steps, then page strides
  const strided = t >= T[2] + 8.0;
  const cbi = strided ? (Math.floor(rt * 4) * 5) % CB.n : Math.floor(rt * 6) % CB.n;

  // suspects
  const susK = prog(t, T[0] + 0.3, 0.8);
  const park = prog(t, T[1] + 0.2, 1.0);
  const aHl = prog(t, T[4] + 2.5, 0.8);
  const bX = prog(t, T[3] + 7.0, 0.6);
  const aOk = prog(t, T[6] + 0.8, 0.6);

  // T5 inset
  const insK = fade(t, T[5] + 0.3, T[6] - 0.45);
  const fixed = ["one-way only", "2 card cores", "pause 3 µs"];
  const options = ["nothing", "cold", "old", "behind", "ahead"];

  return (
    <>
      <Stage>
        {/* the same ring strip as S03 */}
        <g opacity={stripK}>
          <HostWriter rt={rt} />
          <rect x={bandX0} y={RS.y - 10} width={Math.max(0, bandX1 - bandX0)} height={RS.ch + 20} rx={12}
            fill={K.inflight + "22"} stroke={K.inflight} strokeWidth={2.5} strokeDasharray="8 6" />
          <ScrollStrip head={h} state={state} flicker={rt} clipId="s04strip" />
          {Array.from({ length: RS.n + 2 }, (_, i) => {
            const a = Math.floor(h) - RS.n + i;
            const x = absC(a, h);
            if (a >= nx || x < RS.x + 10 || x > RS_RIGHT - 10) return null;
            return <circle key={a} cx={x} cy={RS.y + RS.ch / 2} r={7} fill={a % 2 === 0 ? CORE_A : CORE_B} opacity={0.95} />;
          })}
          <Cursor x={ax} color={CORE_A} tag="A" />
          <Cursor x={bx} color={CORE_B} tag="B" />
          <TimerArc x={ax - 76} y={RS.y - 52} r={20} k={cur.nap} color={K.wait} opacity={prog(t, T[1], 0.6)} />
          <text x={ax - 108} y={RS.y - 44} textAnchor="end" fontSize={26} fontWeight={800} fill={K.wait} opacity={prog(t, T[1], 0.6)}>
            pause 3 µs
          </text>
          <text x={RS_RIGHT} y={BELOW + 52} textAnchor="end" fontSize={26} fontWeight={800} fill={K.inflight}>
            still landing
          </text>
        </g>

        {/* probes on the ring */}
        <g opacity={lapK * pulse}>
          <rect x={RS.x} y={BELOW + 12} width={200} height={12} rx={6} fill={K.probe} />
          <Arrow x1={RS.x + 60} y1={BELOW + 18} x2={RS.x - 40} y2={BELOW + 18} color={K.probe} w={4} head={12} />
        </g>
        <text x={RS.x} y={BELOW + 58} fontSize={26} fontWeight={800} fill={K.probe} opacity={lapK}>
          ← half a lap behind (off-strip)
        </text>
        <g opacity={behindK}>
          <rect x={absX(nx - 6, h)} y={BELOW + 12} width={6 * (RS.cw + RS.gap) - RS.gap} height={12} rx={6} fill={K.probe} opacity={pulse} />
          <text x={absC(nx - 3.5, h)} y={BELOW + 58} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.probe}>
            just behind
          </text>
        </g>
        <g opacity={aheadK}>
          <rect x={absX(fr, h)} y={BELOW + 12} width={6 * (RS.cw + RS.gap) - RS.gap} height={12} rx={6} fill={K.probe} opacity={pulse} />
          <text x={absC(fr + 2.5, h)} y={BELOW + 58} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.probe}>
            just ahead
          </text>
        </g>
        <text x={RS.x} y={BELOW + 58} fontSize={26} fontWeight={800} fill={K.fixed} opacity={fade(t, T[1] + 0.6, T[2] - 0.45)}>
          during each pause: read nothing
        </text>

        {/* suspects: big and centred at T0, then parked in the right column */}
        <g opacity={susK}>
          <g transform={`translate(${lerp(230, 1000, park)} ${lerp(500, 470, park)}) scale(${lerp(1.45, 1, park)}) translate(-1000 -470)`}>
            <Suspect x={1000} y={470} w={410} title="A · position" sub="reading lines still landing" color={K.slow} hl={aHl}
              verdict={aOk} />
          </g>
          <g transform={`translate(${lerp(1080, 1430, park)} ${lerp(500, 470, park)}) scale(${lerp(1.45, 1, park)}) translate(-1430 -470)`}>
            <Suspect x={1430} y={470} w={420} title="B · bandwidth" sub="loading the card's shared L3" color={K.wait} hl={0}
              verdict={-bX} sub2="bandwidth is not the problem" subSwap={prog(t, T[3] + 7.0, 0.5)} />
          </g>
        </g>

        {/* cold buffer outside the ring */}
        <g opacity={coldK}>
          <text x={CB.x} y={CB.y - 18} fontSize={26} fontWeight={700} fill={K.wait}>cold buffer · outside the ring</text>
          {Array.from({ length: CB.n }, (_, i) => (
            <rect key={i} x={CB.x + i * (CB.w + CB.gap)} y={CB.y} width={CB.w} height={CB.h} rx={6} fill="#26304a"
              stroke={i === cbi ? K.probe : "#3a4870"} strokeWidth={i === cbi ? 5 : 2} />
          ))}
          <rect x={CB.x + cbi * (CB.w + CB.gap)} y={CB.y + CB.h + 10} width={CB.w} height={10} rx={5} fill={K.probe} />
          <text x={CB.x} y={CB.y + CB.h + 56} fontSize={24} fill={K.probe} fontWeight={700}>
            {strided ? "reads jump a page at a time" : "reads step one line at a time"}
          </text>
        </g>

        {/* shared L3 load */}
        <g opacity={meterK * meterDim} transform={`translate(0 ${lerp(0, -180, prog(t, T[3] + 0.2, 1.0))})`}>
          <Meter x={LM.x} y={LM.y} w={LM.w} value={l3} max={LM.max} color={K.wait} label="card's shared L3: requests / s"
            valueText={l3Text} k={prog(t, T[1] + 1.4, 0.8)} marks={[{ at: 608 }, { at: 815 }, { at: 1000, color: C.text }]} />
          <text x={lmx(608)} y={LM.y + 56} textAnchor="middle" fontSize={22} fill={C.muted}>608</text>
          <text x={lmx(815)} y={LM.y + 56} textAnchor="middle" fontSize={22} fill={C.muted}>815</text>
          <text x={lmx(1000)} y={LM.y + 56} textAnchor="middle" fontSize={22} fill={C.text}>1 billion/s</text>
        </g>

        {/* throughput */}
        <g opacity={prog(t, T[1] + 1.2, 0.8)}>
          <text x={1000} y={HY1 - 30} fontSize={24} fontWeight={600} fill={C.muted}>
            M/s (median) · 1 host core + 2 card cores
          </text>
          <HBarChart x0={HX0} x1={HX1} y1={HY1} y0={HY0} bars={BARS} max={180} k={(i) => prog(t, BAR_AT[i], 1.0)} size={24} />
        </g>

        {/* T5: mechanism inset [INF] */}
        <g opacity={insK}>
          <rect x={80} y={455} width={880} height={393} rx={18} fill="#141b2e" stroke={K.inf} strokeWidth={2} />
          <text x={110} y={502} fontSize={28} fontWeight={800} fill={C.text}>Why reading ahead hurts</text>
          <InfTag x={480} y={478} />
          {/* one 64-byte line: the host write is still landing while the core reads it */}
          <rect x={400} y={536} width={170} height={80} rx={8} fill="url(#hatchRed)" stroke={K.inflight} strokeWidth={2.5} />
          <text x={485} y={646} textAnchor="middle" fontSize={24} fill={K.inflight} fontWeight={700}>line still landing</text>
          <Arrow x1={790} y1={576} x2={580} y2={576} color={K.fwd} w={5} head={14} k={prog(t, T[5] + 0.8, 0.6)} />
          <text x={800} y={584} fontSize={24} fill={K.fwd} fontWeight={700}>host write</text>
          <Arrow x1={240} y1={576} x2={390} y2={576} color={K.probe} w={5} head={14} k={prog(t, T[5] + 1.2, 0.6)} />
          <text x={110} y={584} fontSize={24} fill={K.probe} fontWeight={700}>core read</text>
          <g opacity={prog(t, T[5] + 2.2, 0.5)}>
            <path d="M420 530 l13 -12 l13 12 l13 -12 l13 12 l13 -12 l13 12 l13 -12 l13 12 l13 -12 l13 12" fill="none"
              stroke={C.yellow} strokeWidth={3.5} opacity={0.6 + 0.4 * Math.sin(rt * 9)} />
            <text x={485} y={680} textAnchor="middle" fontSize={24} fill={C.yellow} fontWeight={700}>
              snoop / invalidate, or inbound writes wait
            </text>
          </g>
          {/* timing: pause vs host queue time, same scale */}
          <g opacity={prog(t, T[5] + 4.5, 0.6)}>
            <text x={110} y={728} fontSize={24} fill={K.wait} fontWeight={700}>pause 3 µs</text>
            <rect x={390} y={708} width={lerp(0, 3 * 100, prog(t, T[5] + 4.5, 0.8))} height={26} rx={6} fill={K.wait} />
            <text x={110} y={772} fontSize={24} fill={K.slow} fontWeight={700}>host queue 4.0 µs</text>
            <rect x={390} y={752} width={lerp(0, 4.0 * 100, prog(t, T[5] + 5.2, 0.8))} height={26} rx={6} fill={K.slow} />
            <line x1={690} y1={696} x2={690} y2={790} stroke={C.text} strokeWidth={2.5} strokeDasharray="5 4" />
          </g>
          <text x={110} y={824} fontSize={24} fill={C.text} fontWeight={700} opacity={prog(t, T[5] + 6.5, 0.6)}>
            4.0 &gt; 3 → pauses end too early
          </text>
          <g opacity={prog(t, T[5] + 8.0, 0.6)}>
            <g transform={`rotate(${rt * 90} 860 745)`}>
              <path d="M860 711 A34 34 0 1 1 828 734" fill="none" stroke={K.slow} strokeWidth={5} strokeLinecap="round" />
              <polygon points="860,699 876,711 860,723" fill={K.slow} />
            </g>
            <text x={860} y={810} textAnchor="middle" fontSize={24} fill={K.slow} fontWeight={700}>feeds on itself</text>
            <text x={860} y={838} textAnchor="middle" fontSize={24} fill={C.muted}>~2.6× pauses</text>
          </g>
        </g>
      </Stage>
      <KnobBar fixed={fixed} knob="core reads" options={options} active={active} k={prog(t, T[0], 0.6)} />
      <Takeaway x={80} y={470} w={880} k={prog(t, T[6] + 0.6, 0.8)} color={K.slow} label="VERDICT">
        Position, not bandwidth: stay behind the landing band.
      </Takeaway>
      <Note x={60} y={858}>ring strip is schematic (real ring: 65,533 slots)</Note>
    </>
  );
}

export const S04: SceneDef = {
  id: "S04",
  title: "Exp 2 · Where the core reads",
  stage: 1,
  source: "MRV-76 · logs/mrv76-2026-11-18 · 33 runs × 2×10⁸ msgs",
  dur: DUR,
  cues,
  C: Body,
};
