import React from "react";
import { Arrow, C, CORE, Cue, FONT, K, MONO, Pt, SceneDef, Stage, Pill, lerp, lin, linScale, logScale, pop, prog, useRealT, useT } from "../lib";
import { Datapath, dpGeom, routes } from "./pcie";
import { Callout, Cells, CoreLights, Glow, HBars, Headline, Loop, RangeBars, Timeline, XAxis, on, xf } from "./_SceneScale";

// <cues>
const cues: Cue[] = [
  [0.60, 8.74, "Same trick for DMA: one DPI queue per card core. It got **worse**: **13–15 M msg/s** in total.", "We tried the same trick for D M A, with one D P I queue per card core. It got worse: thirteen to fifteen million in total."], // 0
  [9.09, 20.95, "DPI doorbell → done: **0.67 µs** (1 queue, W=1) → **5.8 µs** (2 queues, loaded) → **21–24 µs** (4). The engine is saturated.", "The D P I's completion time shows why: point six seven microseconds for one idle queue, five point eight with two loaded queues, and over twenty with four. The engine itself is saturated."], // 1
  [21.30, 29.96, "So send fewer copies: split the host ring into **per-core sub-rings**, so one core's batch of replies is **contiguous**.", "So the answer is fewer copies. We split the host's reply ring into per-core sub-rings, so one core's batch of replies sits side by side."], // 2
  [30.31, 40.51, "Then one DMA covers a batch: **15–19 replies per copy**. One core: **15.1 → 20.0–20.9 M msg/s**.", "Then a single D M A covers the whole batch, fifteen to nineteen replies per copy. One card core goes from fifteen to about twenty-one million messages per second."], // 3
  [40.86, 51.00, "Plus one host timestamp per poll instead of per reply: **21.7–23.2 M msg/s** (host polls now 77–83% empty).", "Taking one host timestamp per poll, instead of one per reply, adds about ten percent more: twenty-two to twenty-three million, and the host is mostly idle again."], // 4
  [51.35, 60.17, "With 2–4 cores each poll finds only **1.3–2.7** cells: little to coalesce. DMA stays best on **one core**.", "With two to four cores, each poll finds only one to three cells, so there's little to coalesce. D M A stays best on a single core."], // 5
];
const DUR = 61.57; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
const g = dpGeom(0.64, 10, 28);
const R = routes(g);
const PX0 = 40;
const PX1 = 1205;

function Body() {
  const t = useT();
  const rt = useRealT();
  const [T0, T1, T2, T3, T4, T5] = T;

  // card cores: 4 in c0–c2, 1 in c3–c4, 4 again in c5
  const many = Math.max(on(t, T0, T3), on(t, T5));
  const lit = [prog(t, T0 + 0.2, 0.5), many, many, many];
  const subRing = (i: number) => CORE[Math.floor((i * 4) / 10)];

  const o01 = on(t, T0, T2);
  const o0 = on(t, T0, T1);
  const o1 = on(t, T1, T2);
  const o2 = on(t, T2, T3);
  const o3 = on(t, T3, T4);
  const o34 = on(t, T3, T5);
  const o4 = on(t, T4, T5);
  const o5 = on(t, T5);

  const reqStream = (period: number) =>
    [0, 1, 2].map((j) => <Loop key={j} pts={R.inline} rt={rt} period={period} dur={1.0} phase={-(j * period) / 3} color={K.req} r={7} />);
  // one DMA per reply, per core
  const perReply = (n: number, period: number) =>
    Array.from({ length: n }, (_, i) => (
      <g key={i}>
        <Loop pts={R.coreToDpi} rt={rt} period={period} dur={0.4} phase={-i * (period / n)} color={CORE[i]} r={7} />
        <Loop pts={R.dmaReply} rt={rt} period={period} dur={1.0} phase={-(0.4 + i * (period / n))} color={CORE[i]} r={7} />
      </g>
    ));

  // ---- throughput chart (right column)
  const bars = [
    { lo: 15.1, text: "15.1", color: K.dma, label: ["1 core", "1 queue"], k: prog(t, T0 + 0.6, 1.0) },
    { lo: 13, hi: 15, text: "13–15", color: K.dma, label: ["4 cores", "4 queues"], k: prog(t, T0 + 2.6, 1.2), note: "worse", noteColor: K.bad },
    { lo: 20.0, hi: 20.9, text: "20.0–20.9", color: K.dma, label: ["1 core", "coalesced"], k: prog(t, T3 + 5.0, 1.2) },
    { lo: 21.7, hi: 23.2, text: "21.7–23.2", color: K.dma, label: ["1 core", "+ RDTSCP", "per poll"], k: prog(t, T4 + 3.5, 1.2) },
  ];

  // ---- c1 DPI completion (log scale)
  const lx = logScale(0.1, 100, 330, 1000);

  // ---- c2 reply ring split: cell j = reply from core j%4; it moves to sub-ring j%4, slot floor(j/4)
  const split = prog(t, T2 + 2.6, 1.6);
  const PITCH = 52;
  const groupX = (c: number) => 64 + c * 284;
  const batch = prog(t, T2 + 5.2, 0.6);

  // ---- c3 one DMA covers a batch (loops every 3 s)
  const cyc = (((rt - 0.3) % 3) + 3) % 3;
  const slide = prog(cyc, 0, 0.8);
  const filled = prog(cyc, 0.8, 0.25) * (1 - prog(cyc, 2.6, 0.3));
  const NB = 17;

  // ---- c4 timelines
  const rev = lin(t, T4 + 0.8, 6) * 920;
  const perReplyTs = [{ w: 40, color: K.mem, text: "", gap: 4 }].concat(
    Array.from({ length: 5 }, (_, i) => [
      { w: 30, color: K.timer, text: "", gap: 2 },
      { w: 28, color: K.rep, text: "", gap: i === 4 ? 12 : 4 },
    ]).flat(),
  );
  const perPollTs = [
    { w: 40, color: K.mem, text: "", gap: 4 },
    { w: 30, color: K.timer, text: "", gap: 2 },
  ].concat(Array.from({ length: 5 }, (_, i) => ({ w: 28, color: K.rep, text: "", gap: i === 4 ? 12 : 4 })));

  // ---- c5 cells per busy poll
  const cx5 = linScale(0, 20, 330, 900);

  const heads: { v: string; l?: string; c: string; o: number; s?: number }[] = [
    { v: "13–15", l: "M msg/s total · one DPI queue per core: worse", c: K.bad, o: o0 },
    { v: "21–24 µs", l: "DPI doorbell → done with 4 queues", c: K.bad, o: o1 },
    { v: "per-core sub-rings", l: "one core's batch is contiguous", c: K.dma, o: o2, s: 44 },
    { v: "15–19", l: "replies per DMA copy · 1 core", c: K.dma, o: o3 },
    { v: "21.7–23.2", l: "M msg/s · 1 core · one RDTSCP per poll", c: K.dma, o: o4 },
    { v: "1.3–2.7", l: "cells per busy poll at 2–4 cores", c: K.bad, o: o5 },
  ];

  return (
    <>
      <Stage>
        {/* ================= running datapath ================= */}
        <Datapath g={g} cores={4} activeCores={0} linkGlow={0.5} labels={{ sdp: ["SDP", "packet engine · OUT_CNTS"] }} />
        <Cells g={g} id="reqRing" fill={(i) => CORE[i % 4]} o={Math.max(xf(t, T0, T3), xf(t, T5))} />
        <Cells g={g} id="reqRing" fill={() => CORE[0]} o={xf(t, T3, T5)} />
        <Cells g={g} id="replyRing" fill={(i) => CORE[i % 4]} o={xf(t, T0, T2)} />
        <Cells g={g} id="replyRing" fill={subRing} o={Math.max(xf(t, T2, T3), xf(t, T5))} />
        <Cells g={g} id="replyRing" fill={() => CORE[0]} o={xf(t, T3, T5)} />
        <CoreLights g={g} lit={lit} />
        <Glow r={g.r.dpi} color={K.dma} o={o0} />
        <Glow r={g.r.dpi} color={K.bad} o={o1} />
        <Glow r={g.r.replyRing} color={K.dma} o={on(t, T2, T4)} />
        <Glow r={g.r.core} color={K.timer} o={o4} />

        {/* c0–c1: 4 cores, each with a DPI queue, one DMA per reply */}
        <g opacity={o01}>
          {reqStream(0.6)}
          {perReply(4, 1.4)}
          <Loop pts={R.hostPollReply} rt={rt} period={0.35} dur={0.28} color={K.mem} r={6} />
        </g>
        {/* c2: same flows, replies land in per-core sub-rings */}
        <g opacity={o2}>
          {reqStream(0.6)}
          {perReply(4, 1.4)}
        </g>
        {/* c3–c4: one core, one wide DMA per batch */}
        <g opacity={o34}>
          {reqStream(0.3)}
          <Loop pts={R.coreToDpi} rt={rt} period={1.6} dur={0.4} color={CORE[0]} r={7} />
          <Loop pts={R.dmaReply} rt={rt} period={1.6} dur={1.0} phase={-0.4} color={K.dma} wide />
          <Loop pts={R.hostPollReply} rt={rt} period={0.35} dur={0.28} color={K.mem} r={6} />
        </g>
        {/* c5: 4 cores, small batches */}
        <g opacity={o5}>
          {reqStream(0.5)}
          {perReply(4, 1.0)}
        </g>

        {/* ================= bottom panel ================= */}
        <rect x={PX0} y={596} width={PX1 - PX0} height={292} rx={16} fill="rgba(20,27,46,0.5)" stroke={C.line} strokeWidth={2} />

        {/* c0: 4 cores → 4 DPI queues → one DMA engine */}
        <g opacity={o0} fontFamily={FONT}>
          <text x={70} y={636} fontSize={26} fontWeight={700} fill={C.text}>
            One DPI queue per card core
          </text>
          {[0, 1, 2, 3].map((i) => {
            const y = 660 + i * 54;
            const lane: Pt[] = [
              [205, y + 21],
              [470, y + 21],
              [640, 760],
              [900, 760],
              [1040, 760],
            ];
            return (
              <g key={i}>
                <rect x={80} y={y} width={120} height={42} rx={8} fill={`${CORE[i]}33`} stroke={CORE[i]} strokeWidth={2.5} />
                <text x={140} y={y + 21} textAnchor="middle" dominantBaseline="central" fontSize={24} fontFamily={MONO} fill={CORE[i]}>
                  core {i}
                </text>
                <rect x={290} y={y} width={170} height={42} rx={8} fill={`${K.dma}1c`} stroke={K.dma} strokeWidth={2} />
                <text x={375} y={y + 21} textAnchor="middle" dominantBaseline="central" fontSize={24} fontFamily={MONO} fill={C.text}>
                  DPI q{i}
                </text>
                <Arrow x1={204} y1={y + 21} x2={286} y2={y + 21} color={CORE[i]} w={3} head={10} />
                <line x1={462} y1={y + 21} x2={636} y2={760} stroke={C.dim} strokeWidth={2.5} />
                <Loop pts={lane} rt={rt} period={1.6} dur={1.4} phase={-i * 0.4} color={CORE[i]} r={8} />
              </g>
            );
          })}
          <rect x={640} y={690} width={260} height={140} rx={14} fill={`${K.dma}22`} stroke={K.dma} strokeWidth={3} />
          <text x={770} y={748} textAnchor="middle" fontSize={30} fontWeight={800} fill={K.dma}>
            DPI engine
          </text>
          <text x={770} y={790} textAnchor="middle" fontSize={24} fill={C.muted}>
            one DMA per reply
          </text>
          <Arrow x1={904} y1={760} x2={1046} y2={760} color={K.dma} w={4} head={14} />
          <rect x={1050} y={730} width={125} height={60} rx={10} fill={`${K.host}22`} stroke={K.host} strokeWidth={2.5} />
          <text x={1112} y={761} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={700} fill={K.host}>
            host
          </text>
        </g>

        {/* c1: DPI completion time, log scale */}
        <g opacity={o1} fontFamily={FONT}>
          <text x={70} y={636} fontSize={26} fontWeight={700} fill={C.text}>
            DPI doorbell → completion, p50 (log scale)
          </text>
          <HBars
            sx={lx}
            base={0.1}
            data={[
              { y: 658, h: 44, lo: 0.67, text: "0.67 µs", color: K.dma, label: "1 queue, W=1", k: prog(t, T1 + 1.0, 0.9) },
              { y: 714, h: 44, lo: 5.8, text: "5.8 µs", color: K.dma, label: "2 queues, loaded", k: prog(t, T1 + 4.5, 0.9) },
              { y: 770, h: 44, lo: 21, hi: 24, text: "21–24 µs", color: K.bad, label: "4 queues", k: prog(t, T1 + 7.0, 0.9) },
            ]}
          />
          <XAxis
            sx={lx}
            x0={330}
            x1={1010}
            y={834}
            ticks={[[0.1, ""], [1, ""], [10, ""], [100, ""], [0.67, "0.67"], [5.8, "5.8"], [22.4, "21–24 µs"]]}
          />
        </g>

        {/* c2: host reply ring split into per-core sub-rings */}
        <g opacity={o2} fontFamily={FONT}>
          <text x={70} y={636} fontSize={26} fontWeight={700} fill={C.text} opacity={1 - prog(t, T2 + 2.2, 0.4)}>
            Host reply ring: replies from 4 cores interleaved
          </text>
          <text x={70} y={636} fontSize={26} fontWeight={700} fill={C.text} opacity={prog(t, T2 + 2.7, 0.4)}>
            Split into per-core sub-rings
          </text>
          {[0, 1, 2, 3].map((c) => (
            <text key={c} x={groupX(c) + (5 * PITCH - 8) / 2} y={680} textAnchor="middle" fontSize={24} fontWeight={700} fill={CORE[c]} opacity={split}>
              core {c}
            </text>
          ))}
          {Array.from({ length: 20 }, (_, j) => {
            const c = j % 4;
            const slotIdx = Math.floor(j / 4);
            const x = lerp(80 + j * PITCH, groupX(c) + slotIdx * PITCH, split);
            return <rect key={j} x={x} y={696} width={PITCH - 8} height={50} rx={7} fill={`${CORE[c]}55`} stroke={CORE[c]} strokeWidth={2} />;
          })}
          {/* one DMA covers core 0's batch */}
          <g opacity={batch}>
            <rect x={groupX(0) - 6} y={690} width={5 * PITCH + 4} height={62} rx={10} fill="none" stroke={K.dma} strokeWidth={4} filter="url(#glow)" />
            <text x={groupX(0) + (5 * PITCH - 8) / 2} y={786} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.dma}>
              1 DMA
            </text>
          </g>
          <text x={70} y={840} fontSize={26} fill={K.bad} opacity={1 - prog(t, T2 + 2.2, 0.4)}>
            one core's replies are scattered: one DMA per reply
          </text>
          <text x={340} y={840} fontSize={26} fill={K.ok} opacity={prog(t, T2 + 5.8, 0.5)}>
            one core's batch is contiguous: one copy covers it
          </text>
        </g>

        {/* c3: one DMA copy carries a batch of 15–19 replies */}
        <g opacity={o3} fontFamily={FONT}>
          <text x={70} y={636} fontSize={26} fontWeight={700} fill={C.text}>
            Core 0's sub-ring: one DMA copy carries the whole batch
          </text>
          {Array.from({ length: 20 }, (_, j) => (
            <rect key={j} x={80 + j * PITCH} y={680} width={PITCH - 8} height={50} rx={7} fill={j < NB ? `${CORE[0]}${filled > 0.5 ? "66" : "14"}` : "rgba(139,149,173,0.08)"} stroke={j < NB ? CORE[0] : C.dim} strokeWidth={2} opacity={j < NB ? 0.5 + 0.5 * filled : 1} />
          ))}
          <g opacity={prog(cyc, 0, 0.3) * (1 - prog(cyc, 0.9, 0.3))}>
            <rect x={lerp(80 + 200, 80, slide) - 4} y={674} width={NB * PITCH} height={62} rx={12} fill={`${K.dma}66`} stroke={K.dma} strokeWidth={3} />
            <text x={lerp(80 + 200, 80, slide) + (NB * PITCH) / 2} y={706} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={C.text}>
              one DMA
            </text>
          </g>
          <rect x={76} y={674} width={NB * PITCH} height={62} rx={12} fill="none" stroke={K.dma} strokeWidth={3} opacity={filled} />
          <text x={70} y={790} fontSize={30} fontWeight={800} fill={K.dma} opacity={prog(t, T3 + 1.5, 0.5)}>
            1 copy = 15–19 replies
          </text>
          <text x={70} y={838} fontSize={26} fill={C.muted} opacity={prog(t, T3 + 2.5, 0.5)}>
            before: one copy per reply (15.1 M msg/s)
          </text>
        </g>

        {/* c4: host timestamps per reply vs per poll */}
        <g opacity={o4} fontFamily={FONT}>
          <text x={70} y={636} fontSize={26} fontWeight={700} fill={C.text}>
            Host timestamps (RDTSCP)
          </text>
          <rect x={520} y={616} width={22} height={22} rx={4} fill={`${K.mem}55`} stroke={K.mem} strokeWidth={2} />
          <text x={552} y={635} fontSize={24} fill={C.text}>
            poll
          </text>
          <rect x={640} y={616} width={22} height={22} rx={4} fill={`${K.timer}55`} stroke={K.timer} strokeWidth={2} />
          <text x={672} y={635} fontSize={24} fill={C.text}>
            RDTSCP
          </text>
          <rect x={810} y={616} width={22} height={22} rx={4} fill={`${K.rep}55`} stroke={K.rep} strokeWidth={2} />
          <text x={842} y={635} fontSize={24} fill={C.text}>
            reply
          </text>
          <text x={70} y={700} dominantBaseline="central" fontSize={26} fontWeight={700} fill={C.muted}>
            per reply
          </text>
          <Timeline x0={250} x1={1170} y={675} h={50} pattern={perReplyTs} reveal={rev} opacity={1 - 0.4 * prog(t, T4 + 4.0, 0.5)} />
          <text x={70} y={800} dominantBaseline="central" fontSize={26} fontWeight={700} fill={K.timer}>
            per poll
          </text>
          <Timeline x0={250} x1={1170} y={775} h={50} pattern={perPollTs} reveal={rev} />
          <text x={250} y={864} fontSize={24} fill={C.muted} opacity={prog(t, T4 + 4.0, 0.5)}>
            same time, more replies handled
          </text>
        </g>

        {/* c5: cells per busy poll */}
        <g opacity={o5} fontFamily={FONT}>
          <text x={70} y={636} fontSize={26} fontWeight={700} fill={C.text}>
            Cells per busy poll
          </text>
          <HBars
            sx={cx5}
            base={0}
            data={[
              { y: 670, h: 50, lo: 15, hi: 19, text: "15–19", color: K.dma, label: "1 core", k: prog(t, T5 + 0.6, 0.9) },
              { y: 744, h: 50, lo: 1.3, hi: 2.7, text: "1.3–2.7 · little to coalesce", color: K.bad, label: "2–4 cores", k: prog(t, T5 + 1.6, 0.9) },
            ]}
          />
          <XAxis sx={cx5} x0={330} x1={910} y={826} ticks={[[0, ""], [5, ""], [10, ""], [15, ""], [20, ""]]} />
        </g>

        {/* ================= throughput chart ================= */}
        <RangeBars x0={1330} x1={1850} y0={735} y1={320} max={25} ticks={[0, 5, 10, 15, 20, 25]} ylabel="throughput, M msg/s" data={bars} />
      </Stage>
      {heads.map((h, i) => (
        <Headline key={i} value={h.v} label={h.l} color={h.c} o={h.o} size={h.s ?? 56} />
      ))}
      <Callout color={K.dma} o={o0 * prog(t, T0 + 1.0, 0.5)}>
        one DPI queue per core →
      </Callout>
      <Callout color={K.bad} o={o1 * prog(t, T1 + 7.5, 0.5)}>
        DPI engine saturated →
      </Callout>
      <Callout color={K.mem} o={o4 * prog(t, T4 + 5.0, 0.5)}>
        ← host polls
        <br />
        77–83% empty
      </Callout>
      {o5 > 0.001 && (
        <Pill x={830} y={604} color={K.dma} size={30} solid style={{ opacity: o5 * prog(t, T5 + 5.0, 0.4), transform: `scale(${pop(t, T5 + 5.0)})`, transformOrigin: "left center" }}>
          DMA: best on one core
        </Pill>
      )}
    </>
  );
}

export const S13: SceneDef = {
  id: "S13",
  title: "Scaling DMA: fewer, bigger copies",
  stage: 3,
  source: "inline_reflector --dma … --dma-coalesce 32",
  dur: DUR,
  cues,
  C: Body,
};
