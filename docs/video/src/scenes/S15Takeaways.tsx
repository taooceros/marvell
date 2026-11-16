import React from "react";
import { C, Cue, K, SceneDef, Stage, lerp, linScale, pop, prog, useRealT, useT } from "../lib";
import { Box, Tx } from "./_SceneWrap";

// <cues>
const cues: Cue[] = [
  [0.60, 16.04, "W=1 ladder: SDP **4.85** → inline + SDP **2.41** → poll memory **2.00** → DPI DMA **~1.4** → card stores **~0.96** → pull **0.83 µs**.", "Here's the latency ladder: four point eight five with S D P, two point four one writing inline, two by polling memory, about one point four with the card's D M A, about point nine six when card cores store directly, and zero point eight three with pull."], // 0
  [16.39, 25.73, "Lesson 1: **uncached accesses set the pace**, not bandwidth: ~120 ns per doorbell, ~0.63 µs per MMIO read.", "Lesson one: uncached accesses set the pace, not bandwidth. About a hundred twenty nanoseconds per doorbell, and point six three microseconds per read."], // 1
  [26.08, 32.58, "Lesson 2: **check order at the destination** instead of fencing at the source. It more than doubled the send rate.", "Lesson two: check ordering at the destination instead of fencing at the source. It more than doubled the send rate."], // 2
  [32.93, 48.81, "Max rate: SDP + 4 cores + mem polling, **24–28 M/s** · Balanced: DMA + coalescing, **22–23 M/s** · Fastest push: card stores, **~0.96 µs** · Min latency: pull, **0.83 µs**.", "So pick by goal. Maximum rate: S D P replies on four card cores, with memory polling. High rate with decent latency: D M A on one core, with coalescing. The fastest push: card cores storing directly. And the absolute minimum: pull."], // 3
  [49.16, 60.46, "Still open: the host-side ceiling for card stores (**~18 M/s**), the 2-core plateau at **17.2 M/s**, two-mode W=1 runs (**0.97 vs 1.23 µs**), a multi-core host.", "Still open: the host side ceiling for card stores at about eighteen million, the two-core plateau at seventeen million, runs that settle in one of two latency modes, and a multi-core host."], // 4
  [60.81, 66.43, "Measure the pieces, cross-check the totals, verify every byte.", "Measure the pieces, cross check the totals, and verify every byte. Thanks for watching."], // 5
];
const DUR = 67.83; // <dur>
// </cues>

// W=1 p50 round trip by mechanism (µs)
const LADDER = [
  { name: "SDP", v: 4.85, txt: "4.85 µs", c: K.sdp, at: 0.3 },
  { name: "inline + SDP", v: 2.41, txt: "2.41 µs", c: K.inline, at: 3.4 },
  { name: "poll memory", v: 2.0, txt: "2.00 µs", c: K.mem, at: 5.4 },
  { name: "DPI DMA", v: 1.4, txt: "~1.4 µs", c: K.dma, at: 6.9 },
  { name: "card stores", v: 0.96, txt: "~0.96 µs", c: K.store, at: 9.8 },
  { name: "pull", v: 0.83, txt: "0.83 µs", c: K.pull, at: 12.6 },
];

// lesson 2: send rate, M msg/s (inline cells, W=128)
const ORDER = [
  { name: "SFENCE every cell", lo: 6.86, hi: 6.86, txt: "6.86", c: K.fence, dash: false },
  { name: "no fence · checked", lo: 13.7, hi: 15.9, txt: "13.7–15.9", c: K.inline, dash: false },
  { name: "no fence · unchecked", lo: 26.2, hi: 26.2, txt: "26.2", c: K.inline, dash: true },
];

// bar = ladder row that glows while the card is introduced (-1: none)
const CONFIGS = [
  { tag: "MAX RATE", recipe: ["SDP + 4 cores", "+ memory polling"], big: "24–28 M/s", sub: "", c: K.sdp, at: 0.1, bar: -1 },
  { tag: "BALANCED", recipe: ["DMA + 1 core", "+ coalescing"], big: "22–23 M/s", sub: "~1.4 µs at W=1", c: K.dma, at: 6.0, bar: 3 },
  { tag: "FASTEST PUSH", recipe: ["card cores store", "into host memory"], big: "~0.96 µs", sub: "13.9–17.7 M/s · host-bound", c: K.store, at: 10.8, bar: 4 },
  { tag: "MIN LATENCY", recipe: ["pull: one UC read"], big: "0.83 µs", sub: "W=1 only", c: K.pull, at: 13.6, bar: 5 },
];
const CFG_W = 427.5;

const OPEN = ["host-side ceiling for card stores (~18 M/s)", "2-core plateau at 17.2 M/s", "two-mode W=1 runs (0.97 vs 1.23 µs)", "multi-core host"];
const OPEN_AT = [0.15, 4.3, 6.4, 9.4];

const TRIAD = [
  { head: "Measure the pieces", sub: "doorbell · UC read · DPI DMA", c: K.timer },
  { head: "Cross-check the totals", sub: "RTT = 1 / rate at W=1", c: K.link },
  { head: "Verify every byte", sub: "lost 0 · bad 0 · torn 0", c: K.ok },
];

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  const allOut = 1 - prog(t, T[5], 0.4);
  const dimQ = 1 - 0.45 * prog(t, T[4], 0.6); // ladder + configs step back for the open questions

  // ---------------------------------------------------- ladder: full width (c0) → tall left half (c1–c2) → compact (c3+)
  const m = prog(t, T[1], 0.5);
  const n = prog(t, T[3], 0.9);
  /** Three-state layout value: full (c0) → mid (c1–c2) → compact (c3+). */
  const lv = (full: number, mid: number, compact: number) => lerp(lerp(full, mid, m), compact, n);
  const lblR = lerp(400, 285, m);
  const bx0 = lerp(425, 300, m);
  const bx1 = lerp(1600, 790, m);
  const sx = linScale(0, 5, bx0, bx1);
  const rowY0 = lv(175, 170, 148);
  const pitch = lv(105, 104, 58);
  const barH = lv(66, 62, 40);
  const axisY = rowY0 + (LADDER.length - 1) * pitch + barH + lv(24, 20, 14);
  const lblSize = lv(30, 28, 26);
  const valSize = lv(32, 30, 26);
  const axisIn = prog(t, 0.3, 0.6);

  // ---------------------------------------------------- lessons (right column), replaced by open questions at c4
  const lessonsOut = 1 - prog(t, T[4], 0.35);
  const l1 = prog(t, T[1] + 0.35, 0.35) * lessonsOut;
  const l2 = prog(t, T[2], 0.5) * lessonsOut;
  const ox = linScale(0, 28, lerp(1340, 1290, n), 1720);
  const openIn = prog(t, T[4] + 0.3, 0.4) * allOut;

  return (
    <Stage>
      <g opacity={allOut}>
        {/* ================= latency ladder ================= */}
        <g opacity={dimQ}>
          <g opacity={axisIn}>
            <line x1={bx0} y1={axisY} x2={bx1 + 20} y2={axisY} stroke={C.muted} strokeWidth={2} />
            {[0, 1, 2, 3, 4, 5].map((v) => (
              <g key={v}>
                <line x1={sx(v)} y1={rowY0 - 16} x2={sx(v)} y2={axisY} stroke={C.line} strokeWidth={1.5} strokeDasharray="4 6" />
                <line x1={sx(v)} y1={axisY} x2={sx(v)} y2={axisY + 8} stroke={C.muted} strokeWidth={2} />
                <Tx x={sx(v)} y={axisY + 24} size={24} anchor="middle" color={C.muted}>
                  {v}
                </Tx>
              </g>
            ))}
            <Tx x={(bx0 + bx1) / 2} y={axisY + 60} size={24} anchor="middle" color={C.muted}>
              round trip at W=1, p50 (µs)
            </Tx>
          </g>
          {LADDER.map((b, i) => {
            const y = rowY0 + i * pitch;
            const k = prog(t, T[0] + b.at, 1.0);
            const w = sx(b.v) - bx0;
            const glow = Math.max(0, ...CONFIGS.filter((c) => c.bar === i).map((c) => prog(t, T[3] + c.at, 0.5) * (1 - prog(t, T[4], 0.5))));
            return (
              <g key={b.name} opacity={prog(t, T[0] + b.at - 0.3, 0.4)}>
                <Tx x={lblR} y={y + barH / 2} size={lblSize} weight={700} anchor="end" color={b.c}>
                  {b.name}
                </Tx>
                {glow > 0 && <rect x={bx0 - 6} y={y - 6} width={w * k + 12} height={barH + 12} rx={10} fill="none" stroke={b.c} strokeWidth={4} opacity={glow} filter="url(#glow)" />}
                <rect x={bx0} y={y} width={w * k} height={barH} rx={8} fill={`${b.c}55`} stroke={b.c} strokeWidth={2.5} />
                <Tx x={bx0 + w * k + 12} y={y + barH / 2} size={valSize} weight={800} color={b.c} opacity={prog(t, T[0] + b.at + 0.6, 0.4)}>
                  {b.txt}
                </Tx>
              </g>
            );
          })}
        </g>

        {/* ================= lesson 1 ================= */}
        <g opacity={l1} transform={`translate(0 ${(1 - prog(t, T[1] + 0.35, 0.35)) * 24})`}>
          <Box x={980} y={135} w={880} h={lerp(330, 200, n)} stroke={K.uc} />
          <Tx x={1008} y={lerp(178, 170, n)} size={24} weight={800} color={K.uc} spacing={2}>
            LESSON 1
          </Tx>
          <Tx x={1008} y={lerp(250, 214, n)} size={lerp(44, 38, n)} weight={800}>
            Uncached accesses set the pace
          </Tx>
          <Tx x={1008} y={lerp(305, 254, n)} size={lerp(30, 26, n)} color={C.muted} opacity={prog(t, T[1] + 2.4, 0.5)}>
            not bandwidth
          </Tx>
          {(
            [
              ["~120 ns per doorbell", 1008, 330, 4.6],
              ["~0.63 µs per MMIO read", 1360, 370, 7.0],
            ] as const
          ).map(([txt, x, w, at]) => {
            const k = pop(t, T[1] + at, 0.5);
            const pulse = 0.5 + 0.5 * Math.sin(rt * 4);
            return (
              <g key={txt} transform={`translate(${x + w / 2} ${lerp(400, 300, n)}) scale(${k})`}>
                <rect x={-w / 2} y={-23} width={w} height={46} rx={23} fill={`${K.uc}26`} stroke={K.uc} strokeWidth={2.5} />
                <circle cx={-w / 2 + 24} cy={0} r={7} fill={K.uc} opacity={0.5 + 0.5 * pulse} />
                <Tx x={14} y={0} size={26} weight={700} anchor="middle" color={C.text}>
                  {txt}
                </Tx>
              </g>
            );
          })}
        </g>

        {/* ================= lesson 2 ================= */}
        <g opacity={l2} transform={`translate(0 ${(1 - prog(t, T[2], 0.5)) * 24})`}>
          <Box x={980} y={lerp(490, 350, n)} w={880} h={lerp(380, 230, n)} stroke={K.ok} />
          <Tx x={1008} y={lerp(533, 383, n)} size={24} weight={800} color={K.ok} spacing={2}>
            LESSON 2
          </Tx>
          <Tx x={1836} y={lerp(533, 383, n)} size={22} anchor="end" color={C.muted}>
            send rate, M msg/s
          </Tx>
          <Tx x={1008} y={lerp(598, 425, n)} size={lerp(42, 36, n)} weight={800}>
            Check order at the destination
          </Tx>
          {ORDER.map((o, i) => {
            const y = lerp(690, 472, n) + i * lerp(66, 38, n);
            const bh = lerp(40, 28, n);
            const fs = lerp(28, 24, n);
            const k = prog(t, T[2] + 2.0 + 1.0 * i, 0.9);
            const wLo = (ox(o.lo) - ox(0)) * k;
            const wHi = (ox(o.hi) - ox(0)) * k;
            return (
              <g key={o.name} opacity={prog(t, T[2] + 1.8 + 1.0 * i, 0.4)}>
                <Tx x={1008} y={y} size={fs} color={o.c}>
                  {o.name}
                </Tx>
                {wHi > wLo && <rect x={ox(0)} y={y - bh / 2} width={wHi} height={bh} rx={6} fill={`${o.c}22`} stroke={o.c} strokeWidth={2} strokeDasharray="6 5" />}
                <rect x={ox(0)} y={y - bh / 2} width={wLo} height={bh} rx={6} fill={o.dash ? `${o.c}22` : `${o.c}66`} stroke={o.c} strokeWidth={2} strokeDasharray={o.dash ? "6 5" : undefined} />
                <Tx x={ox(0) + wHi + 10} y={y} size={fs} weight={800} color={o.c} opacity={prog(t, T[2] + 2.6 + 1.0 * i, 0.4)}>
                  {o.txt}
                </Tx>
              </g>
            );
          })}
        </g>

        {/* ================= open questions (c4) ================= */}
        <g opacity={openIn}>
          <Box x={980} y={135} w={880} h={445} stroke={C.yellow} />
          <Tx x={1008} y={178} size={32} weight={800} color={C.yellow}>
            Still open
          </Tx>
          {OPEN.map((q, i) => {
            const y = 250 + i * 88;
            const at = T[4] + OPEN_AT[i];
            const k = pop(t, at, 0.5);
            return (
              <g key={q} opacity={prog(t, at, 0.4)}>
                <g transform={`translate(1036 ${y}) scale(${k})`}>
                  <circle r={27} fill={`${C.yellow}22`} stroke={C.yellow} strokeWidth={2.5} />
                  <Tx x={0} y={1} size={32} weight={800} anchor="middle" color={C.yellow}>
                    ?
                  </Tx>
                </g>
                <Tx x={1084} y={y} size={30} weight={600}>
                  {q}
                </Tx>
              </g>
            );
          })}
        </g>

        {/* ================= configurations (c3) ================= */}
        <g opacity={dimQ}>
          {CONFIGS.map((c, i) => {
            const x = 60 + i * (CFG_W + 30);
            const at = T[3] + c.at;
            const k = prog(t, at, 0.6);
            return (
              <g key={c.tag} opacity={k} transform={`translate(0 ${(1 - k) * 24})`}>
                <Box x={x} y={600} w={CFG_W} h={275} stroke={c.c} sw={2.5} />
                <Tx x={x + 24} y={636} size={24} weight={800} color={c.c} spacing={2}>
                  {c.tag}
                </Tx>
                {c.recipe.map((line, j) => (
                  <Tx key={line} x={x + 24} y={682 + j * 34} size={26} weight={600}>
                    {line}
                  </Tx>
                ))}
                <Tx x={x + 24} y={790} size={52} weight={800} color={c.c}>
                  {c.big}
                </Tx>
                {c.sub && (
                  <Tx x={x + 24} y={845} size={24} color={C.muted} opacity={prog(t, at + 0.8, 0.5)}>
                    {c.sub}
                  </Tx>
                )}
              </g>
            );
          })}
        </g>
      </g>

      {/* ================= closing triad (c5) ================= */}
      <g opacity={prog(t, T[5] + 0.3, 0.4)}>
        {TRIAD.map((d, i) => {
          const x = 110 + i * 600;
          const lit = prog(t, T[5] + 0.4 + 1.6 * i, 0.5);
          return (
            <g key={d.head}>
              {i > 0 && (
                <path d={`M${x - 82} 420 L${x - 18} 420 M${x - 32} 406 L${x - 18} 420 L${x - 32} 434`} fill="none" stroke={lit > 0.5 ? d.c : C.dim} strokeWidth={4} strokeLinecap="round" />
              )}
              <rect x={x} y={330} width={500} height={180} rx={22} fill={C.panel} stroke={lit > 0.5 ? d.c : C.line} strokeWidth={lerp(2, 4, lit)} filter={lit > 0.5 ? "url(#glow)" : undefined} />
              <rect x={x} y={330} width={500} height={180} rx={22} fill={`${d.c}26`} opacity={lit} />
              <Tx x={x + 250} y={398} size={38} weight={800} anchor="middle" color={lit > 0.5 ? d.c : C.muted}>
                {d.head}
              </Tx>
              <Tx x={x + 250} y={456} size={26} anchor="middle" mono={i === 1} color={C.text} opacity={0.35 + 0.65 * lit}>
                {d.sub}
              </Tx>
            </g>
          );
        })}
        <Tx x={960} y={640} size={34} mono weight={700} anchor="middle" color={C.text} opacity={prog(t, T[5] + 4.4, 0.6)}>
          ~/dev/marvell/testbed
        </Tx>
        <Tx x={960} y={720} size={32} anchor="middle" color={C.muted} opacity={prog(t, T[5] + 5.0, 0.6)}>
          Thanks for watching
        </Tx>
      </g>
    </Stage>
  );
}

export const S15: SceneDef = {
  id: "S15",
  title: "Takeaways",
  stage: 4,
  source: "~/dev/marvell · AGENTS.md · testbed/",
  dur: DUR,
  cues,
  C: Body,
};
