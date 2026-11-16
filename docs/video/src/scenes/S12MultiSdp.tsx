import React from "react";
import { Arrow, C, CORE, Cue, FONT, K, MONO, Pt, SceneDef, Stage, linScale, lin, prog, useRealT, useT } from "../lib";
import { Datapath, dpGeom, routes } from "./pcie";
import { Callout, Cells, CoreLights, Glow, HBars, Headline, Loop, RangeBars, Timeline, XAxis, on, xf } from "./_SceneScale";

// <cues>
const cues: Cue[] = [
  [0.60, 10.74, "One card core with SDP replies: **13.66 M msg/s**, while the host's memory polls come back **97%** empty.", "With one card core and S D P replies, the rate stops at thirteen point seven million, while ninety-seven percent of the host's polls come back empty. The card is the limit."], // 0
  [11.09, 19.83, "Spread the ring across cores: core i takes every n-th cell and replies on its own SDP queue.", "So we spread the ring across card cores. With four cores, each core takes every fourth cell, and replies on its own S D P queue."], // 1
  [20.18, 33.56, "Gotcha: card TX queue i lands on host RX queue i. With one host queue, the other cores' replies **silently vanished**.", "One gotcha: card queue zero lands on host queue zero, card queue one on host queue one, and so on. With a single host queue, the other cores' replies silently vanished. Now the host polls one queue for each card core."], // 2
  [33.91, 41.73, "1 core **13.66** → 2 cores **17.1–17.2 M msg/s**. Neither side looks saturated at 2 cores: **unexplained** so far.", "Two cores reach seventeen point two million. But at two cores neither side looks saturated, and we can't yet explain that plateau."], // 3
  [42.08, 50.46, "At 4 cores the **host** becomes the bottleneck: four OUT_CNTS reads per poll → **13.0–13.3 M msg/s** (W=512).", "At four cores, the host becomes the bottleneck. Reading four out counts registers on every poll drags the rate down to thirteen million."], // 4
  [50.81, 60.57, "Poll memory instead: **24.4–27.9 M msg/s** at W=1024, about **1.8 GB/s** of 64-byte messages.", "Polling memory instead lifts it to between twenty-four and twenty-eight million messages per second, about one point eight gigabytes per second of sixty-four byte messages."], // 5
  [60.92, 71.24, "The price: W=1 gets slower with more queues. 2 cores: **2.76 µs** with OUT_CNTS, **2.06 µs** polling memory.", "The price is latency. One message at a time gets slower with more queues: two point seven six microseconds with two cores, or two point zero six when polling memory."], // 6
];
const DUR = 72.64; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
const g = dpGeom(0.64, 10, 28);
const R = routes(g);
// bottom panel (under the diagram) and right-hand chart column
const PX0 = 40;
const PX1 = 1205;
const rowY = (i: number) => 646 + i * 56;

function Body() {
  const t = useT();
  const rt = useRealT();
  const [T0, T1, T2, T3, T4, T5, T6] = T;

  // ---- which card cores are active: c0 1 · c1–c2 4 · c3 2 · c4–c5 4 · c6 2
  const lit = [
    prog(t, T0 + 0.2, 0.5),
    on(t, T1),
    Math.max(on(t, T1, T3), on(t, T4, T6)),
    Math.max(on(t, T1, T3), on(t, T4, T6)),
  ];
  const cellPhases: [number, number, number][] = [
    [T0, T1, 1],
    [T1, T3, 4],
    [T3, T4, 2],
    [T4, T6, 4],
    [T6, 1e9, 2],
  ];

  // ---- per-cue flow opacities
  const o0 = on(t, T0, T1);
  const o12 = on(t, T1, T3);
  const o3 = on(t, T3, T4);
  const o4 = on(t, T4, T5);
  const o5 = on(t, T5, T6);
  const o6 = on(t, T6);
  // cue 2 phases: A mapping rule · B single host queue (replies vanish) · C host polls 4 queues
  const pB = T2 + 5.0;
  const pC = T2 + 9.6;
  const inB = t >= pB && t < pC;

  const coreFlows = (n: number, period: number, cutFor?: (i: number) => number) =>
    Array.from({ length: n }, (_, i) => (
      <g key={i}>
        <Loop pts={R.coreToSdp} rt={rt} period={period} dur={0.4} phase={-i * (period / n)} color={CORE[i]} r={7} />
        <Loop pts={R.sdpReply} rt={rt} period={period} dur={1.0} phase={-(0.4 + i * (period / n))} color={CORE[i]} r={8} cut={cutFor ? cutFor(i) : 1} />
      </g>
    ));
  const reqStream = (period: number) =>
    [0, 1, 2].map((j) => <Loop key={j} pts={R.inline} rt={rt} period={period} dur={1.0} phase={-(j * period) / 3} color={K.req} r={7} />);

  // ---- chart (right column)
  const bars = [
    { lo: 13.66, text: "13.66", color: K.sdp, label: ["1 core"], k: prog(t, T0 + 0.8, 1.2) },
    { lo: 17.1, hi: 17.2, text: "17.1–17.2", color: K.sdp, label: ["2 cores"], k: prog(t, T3 + 0.6, 1.2) },
    { lo: 13.0, hi: 13.3, text: "13.0–13.3", color: K.uc, label: ["4 cores", "OUT_CNTS", "W=512"], k: prog(t, T4 + 2.5, 1.2) },
    { lo: 24.4, hi: 27.9, text: "24.4–27.9", color: K.mem, label: ["4 cores", "mem poll", "W=1024"], k: prog(t, T5 + 1.0, 1.4) },
  ];
  const CX0 = 1330;
  const CX1 = 1850;
  const CY0 = 735;
  const CY1 = 320;
  const cy = linScale(0, 30, CY0, CY1);
  const slot = (CX1 - CX0) / 4;

  // ---- c0 poll meter
  const hits = new Set([13, 47, 81]);
  const polled = lin(t, T0 + 0.6, 7.5) * 100;

  // ---- c4/c5 timelines
  const revA = lin(t, T4 + 0.6, 6) * 920;
  const revB = lin(t, T5 + 0.6, 6) * 920;
  const mmioPoll = [0, 1, 2, 3].map((q) => ({ w: 54, color: K.uc, text: `q${q}`, gap: q === 3 ? 4 : 2 })).concat([{ w: 26, color: K.rep, text: "", gap: 10 }]);
  const memPoll = [0, 1, 2, 3].map((q) => ({ w: 14, color: K.mem, gap: q === 3 ? 4 : 2 })).concat([{ w: 40, color: K.rep, gap: 8 }]);

  // ---- c6 latency bars
  const lx = linScale(0, 3, 300, 1000);
  const kL1 = prog(t, T6 + 0.8, 0.9);
  const kL2 = prog(t, T6 + 4.0, 0.9);

  // headline per cue (takes turns)
  const heads: { v: string; l?: string; c: string; o: number; s?: number }[] = [
    { v: "13.66", l: "M msg/s · 1 card core", c: K.sdp, o: o0 },
    { v: "4 SDP queues", l: "one per card core", c: C.text, o: on(t, T1, T2), s: 48 },
    { v: "TX q i → RX q i", c: C.text, o: on(t, T2, pB), s: 48 },
    { v: "replies vanish ✗", l: "only RX q0 open", c: K.bad, o: on(t, pB, pC), s: 48 },
    { v: "host polls 4 RX queues ✓", c: K.ok, o: on(t, pC, T3), s: 44 },
    { v: "17.1–17.2", l: "M msg/s · 2 cores", c: K.sdp, o: o3 },
    { v: "13.0–13.3", l: "M msg/s · 4 cores · OUT_CNTS", c: K.uc, o: o4 },
    { v: "24.4–27.9", l: "M msg/s · ≈ 1.8 GB/s of 64 B messages", c: K.mem, o: o5 },
    { v: "2.76 µs", l: "W=1 · 2 cores · OUT_CNTS", c: K.uc, o: o6 },
  ];

  return (
    <>
      <Stage>
        {/* ================= the running datapath (shrunk, top-left) ================= */}
        <Datapath g={g} cores={4} activeCores={0} linkGlow={0.5} labels={{ sdp: ["SDP", "packet engine · OUT_CNTS"] }} />
        {cellPhases.map(([a, b, n]) => (
          <Cells key={a} g={g} id="reqRing" fill={(i) => CORE[i % n]} o={xf(t, a, b)} />
        ))}
        <CoreLights g={g} lit={lit} />
        <Glow r={g.r.cores} color={K.card} o={o0} />
        <Glow r={g.r.sdp} color={K.sdp} o={o12} />
        <Glow r={g.r.core} color={K.uc} o={o4} />
        <Glow r={g.r.sdpRing} color={K.mem} o={o5} />

        {/* c0: one core, host polls memory (mostly empty) */}
        <g opacity={o0}>
          {reqStream(1.2)}
          <Loop pts={R.cardPoll} rt={rt} period={1.2} dur={0.3} phase={-0.95} color={CORE[0]} r={7} />
          {coreFlows(1, 1.2)}
          <Loop pts={R.hostPollSdp} rt={rt} period={0.3} dur={0.24} color={K.mem} r={6} />
        </g>
        {/* c1–c2: four cores, each on its own SDP queue */}
        <g opacity={o12}>
          {reqStream(0.6)}
          {coreFlows(4, 1.2, (i) => (inB && i > 0 ? 0.62 : 1))}
          <Loop pts={R.hostPollSdp} rt={rt} period={0.3} dur={0.24} color={K.mem} r={6} />
        </g>
        {/* c3: two cores */}
        <g opacity={o3}>
          {reqStream(0.8)}
          {coreFlows(2, 1.0)}
          <Loop pts={R.hostPollSdp} rt={rt} period={0.3} dur={0.24} color={K.mem} r={6} />
        </g>
        {/* c4: 4 cores, host reads 4 OUT_CNTS registers per poll */}
        <g opacity={o4}>
          {reqStream(0.9)}
          {coreFlows(4, 1.6)}
          {[0, 1, 2, 3].map((q) => (
            <g key={q}>
              <Loop pts={R.mmioOut} rt={rt} period={2.4} dur={0.3} phase={-q * 0.6} color={K.uc} r={7} />
              <Loop pts={R.mmioBack} rt={rt} period={2.4} dur={0.3} phase={-(q * 0.6 + 0.3)} color={K.uc} r={7} />
            </g>
          ))}
        </g>
        {/* c5: 4 cores, host polls memory */}
        <g opacity={o5}>
          {reqStream(0.45)}
          {coreFlows(4, 0.8)}
          <Loop pts={R.hostPollSdp} rt={rt} period={0.3} dur={0.24} color={K.mem} r={6} />
        </g>
        {/* c6: W=1, one message in flight */}
        <g opacity={o6}>
          <Loop pts={R.inline} rt={rt} period={3.2} dur={1.0} color={K.req} r={9} />
          <Loop pts={R.cardPoll} rt={rt} period={3.2} dur={0.3} phase={-1.0} color={CORE[1]} r={7} />
          <Loop pts={R.coreToSdp} rt={rt} period={3.2} dur={0.4} phase={-1.3} color={CORE[1]} r={7} />
          <Loop pts={R.sdpReply} rt={rt} period={3.2} dur={1.0} phase={-1.7} color={CORE[1]} r={9} />
          <Loop pts={R.hostPollSdp} rt={rt} period={0.4} dur={0.3} color={K.mem} r={6} />
        </g>

        {/* ================= bottom panel ================= */}
        <rect x={PX0} y={596} width={PX1 - PX0} height={292} rx={16} fill="rgba(20,27,46,0.5)" stroke={C.line} strokeWidth={2} />

        {/* c0: host poll meter, 97% empty */}
        <g opacity={o0} fontFamily={FONT}>
          <text x={70} y={640} fontSize={26} fontWeight={700} fill={C.text}>
            Host polls its memory
          </text>
          <text x={70} y={740} fontSize={72} fontWeight={800} fill={K.mem}>
            97%
          </text>
          <text x={72} y={786} fontSize={28} fill={C.muted}>
            come back empty
          </text>
          {Array.from({ length: 100 }, (_, n) => {
            const col = n % 25;
            const row = Math.floor(n / 25);
            const vis = Math.min(1, Math.max(0, polled - n));
            if (vis <= 0) return null;
            const hit = hits.has(n);
            return (
              <rect key={n} x={340 + col * 33} y={618 + row * 46} width={27} height={38} rx={5} opacity={vis} fill={hit ? K.rep : "rgba(139,149,173,0.10)"} stroke={hit ? K.rep : C.dim} strokeWidth={1.5} />
            );
          })}
          <rect x={340} y={812} width={24} height={24} rx={4} fill={K.rep} />
          <text x={374} y={832} fontSize={24} fill={C.text}>
            reply found
          </text>
          <rect x={560} y={812} width={24} height={24} rx={4} fill="rgba(139,149,173,0.10)" stroke={C.dim} strokeWidth={1.5} />
          <text x={594} y={832} fontSize={24} fill={C.text}>
            empty poll
          </text>
          <text x={1170} y={832} textAnchor="end" fontSize={24} fill={C.muted}>
            one square = one poll
          </text>
        </g>

        {/* c1: cell n → core (n−1) mod 4 */}
        <g opacity={on(t, T1, T2)} fontFamily={FONT}>
          <text x={70} y={634} fontSize={26} fontWeight={700} fill={C.text}>
            Request cells, split across 4 cores
          </text>
          {Array.from({ length: 12 }, (_, j) => {
            const s = prog(t, T1 + 0.6 + j * 0.35, 0.4);
            if (s <= 0) return null;
            const c = CORE[j % 4];
            return (
              <g key={j} opacity={s} transform={`translate(0 ${(1 - s) * -16})`}>
                <rect x={70 + j * 62} y={668} width={54} height={54} rx={8} fill={`${c}40`} stroke={c} strokeWidth={2.5} />
                <text x={97 + j * 62} y={695} textAnchor="middle" dominantBaseline="central" fontSize={24} fontFamily={MONO} fontWeight={700} fill={C.text}>
                  {j + 1}
                </text>
              </g>
            );
          })}
          <text x={70} y={772} fontSize={28} fill={C.muted} fontFamily={MONO} opacity={prog(t, T1 + 3.5, 0.5)}>
            cell n → core (n − 1) mod 4
          </text>
          <text x={70} y={822} fontSize={26} fill={C.text} opacity={prog(t, T1 + 5.0, 0.5)}>
            each core replies on its own SDP TX queue →
          </text>
        </g>

        {/* c1–c2: card side of the queue mapping (core i → SDP TX q i) */}
        <g opacity={o12} fontFamily={FONT}>
          <text x={935} y={630} textAnchor="middle" fontSize={24} fill={K.card} fontWeight={700}>
            card SDP TX
          </text>
          <text x={1112} y={630} textAnchor="middle" fontSize={24} fill={K.card} fontWeight={700}>
            cores
          </text>
          {[0, 1, 2, 3].map((i) => {
            const y = rowY(i);
            return (
              <g key={i}>
                <rect x={860} y={y} width={150} height={44} rx={8} fill={`${CORE[i]}22`} stroke={CORE[i]} strokeWidth={2} />
                <text x={935} y={y + 22} textAnchor="middle" dominantBaseline="central" fontSize={24} fontFamily={MONO} fill={C.text}>
                  TX q{i}
                </text>
                <rect x={1055} y={y} width={115} height={44} rx={8} fill={`${CORE[i]}33`} stroke={CORE[i]} strokeWidth={2.5} />
                <text x={1112} y={y + 22} textAnchor="middle" dominantBaseline="central" fontSize={24} fontFamily={MONO} fill={CORE[i]}>
                  core {i}
                </text>
                <Arrow x1={1053} y1={y + 22} x2={1014} y2={y + 22} color={CORE[i]} w={3} head={10} />
              </g>
            );
          })}
        </g>

        {/* c2: host side of the mapping; one host queue → replies vanish; then 4 host queues */}
        <g opacity={on(t, T2, T3)} fontFamily={FONT}>
          <text x={160} y={630} textAnchor="middle" fontSize={24} fill={K.host} fontWeight={700}>
            host RX
          </text>
          <text x={550} y={630} textAnchor="middle" fontSize={24} fill={C.muted}>
            PCIe: TX q i lands on RX q i
          </text>
          {[0, 1, 2, 3].map((i) => {
            const y = rowY(i);
            const ya = y + 22;
            const draw = prog(t, T2 + 0.6 + i * 1.1, 0.8);
            const neutral = i === 0 ? 0 : 1 - prog(t, pB, 0.4);
            const red = i === 0 ? 0 : prog(t, pB, 0.4) * (1 - prog(t, pC, 0.4));
            const solid = i === 0 ? 1 : prog(t, pC, 0.4);
            const fixed = prog(t, pC, 0.4);
            const lane: Pt[] = [
              [856, ya],
              [246, ya],
            ];
            return (
              <g key={i}>
                <Arrow x1={856} y1={ya} x2={246} y2={ya} k={draw} color={fixed > 0.5 ? K.ok : C.muted} w={3} head={12} dash="10 7" />
                {/* host RX boxes in three states */}
                <g opacity={neutral}>
                  <rect x={80} y={y} width={160} height={44} rx={8} fill="none" stroke={C.muted} strokeWidth={2} strokeDasharray="7 5" />
                  <text x={160} y={ya} textAnchor="middle" dominantBaseline="central" fontSize={24} fontFamily={MONO} fill={C.muted}>
                    RX q{i}
                  </text>
                </g>
                <g opacity={red}>
                  <rect x={80} y={y} width={160} height={44} rx={8} fill="rgba(249,65,68,0.08)" stroke={K.bad} strokeWidth={2} strokeDasharray="7 5" />
                  <text x={160} y={ya} textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fill={K.bad}>
                    not polled
                  </text>
                  <text x={300} y={ya} textAnchor="middle" dominantBaseline="central" fontSize={38} fontWeight={800} fill={K.bad}>
                    ✗
                  </text>
                </g>
                <g opacity={solid}>
                  <rect x={80} y={y} width={160} height={44} rx={8} fill={`${K.host}33`} stroke={K.host} strokeWidth={2.5} />
                  <text x={160} y={ya} textAnchor="middle" dominantBaseline="central" fontSize={24} fontFamily={MONO} fill={C.text}>
                    RX q{i}
                  </text>
                  {i > 0 && (
                    <text x={300} y={ya} textAnchor="middle" dominantBaseline="central" fontSize={34} fontWeight={800} fill={K.ok} opacity={fixed}>
                      ✓
                    </text>
                  )}
                </g>
                {t >= pB && (
                  <g opacity={prog(t, pB, 0.4)}>
                    <Loop pts={lane} rt={rt} period={1.4} dur={1.0} phase={-i * 0.35} color={CORE[i]} r={9} cut={inB && i > 0 ? 0.88 : 1} />
                  </g>
                )}
              </g>
            );
          })}
        </g>

        {/* c3: neither side saturated */}
        <g opacity={o3} fontFamily={FONT}>
          <rect x={70} y={630} width={430} height={210} rx={14} fill={`${K.host}14`} stroke={K.host} strokeWidth={2.5} />
          <text x={95} y={682} fontSize={30} fontWeight={800} fill={K.host}>
            Host
          </text>
          <text x={95} y={738} fontSize={30} fill={C.text}>
            polls 87–91% empty
          </text>
          <text x={95} y={790} fontSize={26} fill={C.muted}>
            waiting, not busy
          </text>
          <rect x={740} y={630} width={430} height={210} rx={14} fill={`${K.card}14`} stroke={K.card} strokeWidth={2.5} />
          <text x={765} y={682} fontSize={30} fontWeight={800} fill={K.card}>
            Card · 2 cores
          </text>
          <text x={765} y={738} fontSize={30} fill={C.text}>
            not saturated either
          </text>
          <g opacity={prog(t, T3 + 3.5, 0.5)}>
            <circle cx={620} cy={712} r={44} fill="rgba(249,199,79,0.12)" stroke={C.yellow} strokeWidth={3} />
            <text x={620} y={714} textAnchor="middle" dominantBaseline="central" fontSize={56} fontWeight={800} fill={C.yellow}>
              ?
            </text>
            <text x={620} y={800} textAnchor="middle" fontSize={26} fontWeight={700} fill={C.yellow}>
              unexplained
            </text>
          </g>
        </g>

        {/* c4–c5: one host poll, OUT_CNTS vs memory */}
        <g opacity={on(t, T4, T6)} fontFamily={FONT}>
          <g opacity={on(t, T4, T5)}>
            <text x={70} y={636} fontSize={26} fontWeight={700} fill={C.text}>
              Host core at 4 cores: every poll reads 4 OUT_CNTS registers
            </text>
          </g>
          <g opacity={o5}>
            <text x={70} y={636} fontSize={26} fontWeight={700} fill={C.text}>
              Poll host memory instead: no MMIO on the poll path
            </text>
          </g>
          <text x={70} y={700} dominantBaseline="central" fontSize={26} fontWeight={700} fill={K.uc} opacity={1 - 0.55 * o5}>
            OUT_CNTS
          </text>
          <Timeline x0={250} x1={1170} y={675} h={50} pattern={mmioPoll} reveal={revA} opacity={1 - 0.55 * o5} />
          <text x={250} y={760} dominantBaseline="central" fontSize={24} fill={K.uc} opacity={prog(t, T4 + 3.0, 0.5) * (1 - 0.55 * o5)}>
            4 uncached MMIO reads per poll · host polls 0% empty
          </text>
          <g opacity={o5}>
            <text x={70} y={820} dominantBaseline="central" fontSize={26} fontWeight={700} fill={K.mem}>
              mem poll
            </text>
            <Timeline x0={250} x1={1170} y={795} h={50} pattern={memPoll} reveal={revB} />
          </g>
          <g opacity={on(t, T4, T5) * prog(t, T4 + 0.8, 0.5)}>
            <rect x={250} y={800} width={22} height={22} rx={4} fill={`${K.uc}55`} stroke={K.uc} strokeWidth={2} />
            <text x={282} y={818} fontSize={24} fill={C.text}>
              MMIO read (q0–q3)
            </text>
            <rect x={560} y={800} width={22} height={22} rx={4} fill={`${K.rep}55`} stroke={K.rep} strokeWidth={2} />
            <text x={592} y={818} fontSize={24} fill={C.text}>
              handle replies
            </text>
          </g>
        </g>

        {/* c6: W=1 latency */}
        <g opacity={o6} fontFamily={FONT}>
          <text x={70} y={636} fontSize={26} fontWeight={700} fill={C.text}>
            W=1 round trip (one message in flight), µs
          </text>
          <HBars
            sx={lx}
            base={0}
            data={[
              { y: 656, h: 40, lo: 2.41, text: "2.41", color: K.uc, inner: "OUT_CNTS", k: kL1 },
              { y: 700, h: 40, lo: 2.0, text: "2.00", color: K.mem, inner: "mem poll", k: kL1 },
              { y: 762, h: 40, lo: 2.76, text: "2.76", color: K.uc, inner: "OUT_CNTS", k: kL2 },
              { y: 806, h: 40, lo: 2.06, text: "2.06", color: K.mem, inner: "mem poll", k: kL2 },
            ]}
          />
          <text x={284} y={698} textAnchor="end" dominantBaseline="central" fontSize={28} fontWeight={700} fill={C.text}>
            1 core
          </text>
          <text x={284} y={804} textAnchor="end" dominantBaseline="central" fontSize={28} fontWeight={700} fill={C.text} opacity={prog(t, T6 + 4.0, 0.5)}>
            2 cores
          </text>
          <XAxis sx={lx} x0={300} x1={1010} y={852} ticks={[[0, ""], [1, ""], [2, ""], [3, ""]]} />
          <text x={1170} y={690} textAnchor="end" fontSize={24} fill={C.muted} opacity={prog(t, T6 + 5.5, 0.5)}>
            more queues →
          </text>
          <text x={1170} y={720} textAnchor="end" fontSize={24} fill={C.muted} opacity={prog(t, T6 + 5.5, 0.5)}>
            slower at W=1
          </text>
        </g>

        {/* ================= throughput chart (right column) ================= */}
        <RangeBars x0={CX0} x1={CX1} y0={CY0} y1={CY1} max={30} ticks={[0, 10, 20, 30]} ylabel="throughput, M msg/s" data={bars} />
        <g opacity={o3} fontFamily={FONT}>
          <line x1={CX0 + slot * 1.8} x2={CX1} y1={cy(17.2)} y2={cy(17.2)} stroke={C.yellow} strokeWidth={2.5} strokeDasharray="8 6" />
          <circle cx={CX0 + slot * 2.5} cy={cy(17.2) - 48} r={24} fill="rgba(249,199,79,0.12)" stroke={C.yellow} strokeWidth={2.5} />
          <text x={CX0 + slot * 2.5} y={cy(17.2) - 47} textAnchor="middle" dominantBaseline="central" fontSize={32} fontWeight={800} fill={C.yellow}>
            ?
          </text>
          <text x={CX0 + slot * 2.5 + 34} y={cy(17.2) - 47} dominantBaseline="central" fontSize={25} fontWeight={700} fill={C.yellow}>
            plateau
          </text>
        </g>
      </Stage>
      {heads.map((h, i) => (
        <Headline key={i} value={h.v} label={h.l} color={h.c} o={h.o} size={h.s ?? 56} />
      ))}
      <Callout color={K.card} o={o0 * prog(t, T0 + 5.0, 0.5)}>
        card is the limit →
      </Callout>
      <Callout color={K.uc} o={o4 * prog(t, T4 + 0.8, 0.5)}>
        ← host is the bottleneck
      </Callout>
    </>
  );
}

export const S12: SceneDef = {
  id: "S12",
  title: "Scaling out: more card cores",
  stage: 3,
  source: "inline_reflector -l 3-6 · host rx_poll_mem=1",
  dur: DUR,
  cues,
  C: Body,
};
