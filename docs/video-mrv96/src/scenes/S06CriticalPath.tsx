import React from "react";
import {
  Axes,
  C,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  PacketStream,
  Pt,
  SceneDef,
  Series,
  Stage,
  clamp01,
  fade,
  lerp,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { BarChart, Brace, InfPill, KnobBar, NextQuestion, SegBar } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 11.48, "**Question:** In the 13 M/s slow state, who sets the pace? **One knob:** inject an un-hideable delay **δ** (`10–59 ns`, fenced multiplies) per message on **one side**.", "Experiment four asks who owns the critical path. One knob: inject an un-hideable delay delta of ten to fifty-nine nanoseconds per message on either the host or the card."], // 0
  [11.83, 25.39, "**Calibration:** Average **20M** messages (sub-ns precision). At `W = 1`, `+40 ns` adds `+40.1 ns` (slope **1.01**). **Slope ≈ 1** = bottleneck; **slope ≈ 0** = idle.", "Over twenty million messages we resolve fractions of a nanosecond. At window one, forty nanoseconds adds forty point one, slope one point zero one. Slope one means bottleneck; slope zero means idle."], // 1
  [25.74, 41.58, "**Fast state** (1 card core, `W = 1024`): both slopes **1.07** (interlocked). **Slow state** (4 cores, `W = 64`, `76.1 ns/msg`): **+59 ns** on card changes nothing (**76.1 → 75.6 ns**, slope **−0.01**).", "Fast state, one core at window ten twenty-four: both slopes are one point zero seven, tightly interlocked. Slow state, four cores at sixty-four: adding fifty-nine nanoseconds on the card changes nothing, seventy-six point one to seventy-five point six."], // 2
  [41.93, 57.27, "**Host surprise:** in the slow state, **+10 ns** makes it **20% faster** (**76.2 → 60.9 ns/msg**)! **[INF]** Both CPUs are idle; spacing host bursts breaks the slow rhythm.", "Surprise on the host: adding ten nanoseconds makes the slow state twenty percent faster, dropping from seventy-six point two to sixty point nine nanoseconds. Our reading: both processors sit idle, and spacing host bursts breaks the slow rhythm."], // 3
  [57.62, 74.16, "**4-core clue:** At `W = 1024`, **4 card cores lose to 1** (**23.9 vs 50.5 M/s**). Sending stays **9.2–9.3 ns**, while reply polling jumps **13.6 → 32.5 ns** across 4 sub-rings.", "Another four-core clue: at window ten twenty-four, four card cores lose to one, twenty-three point nine versus fifty point five million. Sending stays nine nanoseconds, while reply polling grows from thirteen point six to thirty-two point five across four rings."], // 4
  [74.51, 86.63, "**Next:** Four card cores appear in both slowdowns. Before blaming the loop itself, rule out two **4-core hardware suspects**: broken **WC** and **128 B false sharing**.", "Four card cores show up in both slowdowns. Before blaming the closed loop itself, we must rule out two classic four-core hardware suspects: broken write combining, and one-hundred-twenty-eight byte false sharing."], // 5
];
const DUR = 88.03; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const realT = useRealT();

  // Top KnobBar (cues 0–5)
  const knobOp = prog(t, T[0] + 0.15, 0.5);
  const knobActive =
    t < T[1] - 0.1
      ? -1
      : t < T[2] - 0.1
      ? 0
      : t < T[2] + 7.2
      ? lerp(0, 1, prog(t, T[2], 0.55))
      : t < T[4] - 0.1
      ? lerp(1, 2, prog(t, T[2] + 7.2, 0.55))
      : lerp(2, 3, prog(t, T[4], 0.55));

  // Phase 0 (Cue 0): Delay-injection trick diagram
  const trickOp = fade(t, T[0], T[1], 0.45);
  const injectOnCard = Math.floor(realT / 3.2) % 2 === 1;
  const loopPts: Pt[] = [
    [220, 390],
    [220, 320],
    [950, 320],
    [950, 390],
    [950, 620],
    [220, 620],
    [220, 550],
  ];

  // Phase 1 (Cue 1): Calibration at W = 1
  const calOp = fade(t, T[1] + 0.25, T[2], 0.45);
  const calSeriesK = prog(t, T[1] + 0.8, 1.4);
  const calBadgeOp = prog(t, T[1] + 2.4, 0.5);
  const calSx = linScale(0, 45, 165, 865);
  const calSy = linScale(915, 975, 680, 318);
  const calPts: [number, number][] = [
    [0, 924.2],
    [10, 934.9],
    [20, 944.7],
    [40, 964.3],
  ];

  // Phase 2 (Cues 2–3): Side-by-side Fast vs Slow slope plots
  const slopeOp = fade(t, T[2] + 0.25, T[4], 0.45);
  const fastHostK = prog(t, T[2] + 0.7, 1.2);
  const fastCardK = prog(t, T[2] + 1.4, 1.2);
  const slowCardOp = prog(t, T[2] + 6.8, 0.5);
  const slowCardK = prog(t, T[2] + 7.2, 1.3);
  const slowHostOp = prog(t, T[3] + 0.25, 0.45);
  const slowHostK = prog(t, T[3] + 0.55, 1.4);
  const slowDipOp = prog(t, T[3] + 2.0, 0.5);
  const cue2NoteOp = fade(t, T[2] + 8.4, T[3], 0.4);
  const cue3NoteOp = prog(t, T[3] + 2.4, 0.5);

  const fastSx = linScale(0, 62, 155, 865);
  const fastSy = linScale(20, 95, 648, 314);
  const fastHostPts: [number, number][] = [
    [0, 24.8],
    [10, 38.7],
    [20, 44.5],
    [40, 76.9],
  ];
  const fastCardPts: [number, number][] = [
    [0, 24.8],
    [29, 58.6],
    [45, 72.3],
    [59, 86.8],
  ];

  const slowSx = linScale(0, 62, 1085, 1805);
  const slowSy = linScale(50, 90, 648, 314);
  const slowCardPts: [number, number][] = [
    [0, 76.1],
    [32, 75.9],
    [45, 75.7],
    [59, 75.6],
  ];
  const slowHostPts: [number, number][] = [
    [0, 76.2],
    [10, 60.9],
    [20, 61.4],
    [40, 78.3],
  ];

  // Phase 3 (Cues 4–5): 4-core clue at W = 1024 + Bridge
  const clueOp = prog(t, T[4] + 0.3, 0.5);
  const bar1K = prog(t, T[4] + 0.7, 0.8);
  const bar4K = prog(t, T[4] + 1.4, 0.8);
  const seg1K = prog(t, T[4] + 2.3, 0.9);
  const seg4K = prog(t, T[4] + 3.2, 1.0);
  const clueBraceOp = prog(t, T[4] + 4.3, 0.5);
  const nextOp = prog(t, T[5] + 0.15, 0.5);

  const pulse = 0.5 + 0.5 * Math.sin(realT * 5);

  return (
    <>
      {/* ================= BACKGROUND HTML CARDS (BEHIND <Stage>) ================= */}

      {/* Cue 0 Cards */}
      {trickOp > 0.005 && (
        <>
          <Card
            x={60}
            y={208}
            w={1055}
            h={568}
            border={C.line}
            title="THE DELAY-INJECTION PROBE (ONE SIDE AT A TIME)"
            titleColor={K.knob}
            style={{ opacity: trickOp, background: "rgba(20,27,46,0.62)" }}
          >
            <div />
          </Card>
          <Card
            x={1145}
            y={208}
            w={715}
            h={568}
            border={K.knob}
            title="HOW TO READ THE SLOPE"
            titleColor={K.knob}
            style={{ opacity: trickOp, background: "rgba(20,27,46,0.72)" }}
          >
            <div style={{ padding: "8px 12px", fontFamily: FONT }}>
              <div
                style={{
                  marginTop: 12,
                  padding: "18px 20px",
                  borderRadius: 12,
                  background: "rgba(67,197,158,0.1)",
                  border: `2px solid ${K.fast}`,
                }}
              >
                <div style={{ fontSize: 26, fontWeight: 800, color: K.fast }}>Slope ≈ 1 → Bottleneck</div>
                <div style={{ fontSize: 23, color: C.text, marginTop: 8, lineHeight: 1.35 }}>
                  Adding <strong style={{ color: K.knob }}>+δ</strong> adds{" "}
                  <strong style={{ color: K.knob }}>+δ</strong> to time/msg. That CPU is on the critical path.
                </div>
              </div>
              <div
                style={{
                  marginTop: 22,
                  padding: "18px 20px",
                  borderRadius: 12,
                  background: "rgba(179,136,255,0.1)",
                  border: `2px solid ${K.wait}`,
                }}
              >
                <div style={{ fontSize: 26, fontWeight: 800, color: K.wait }}>Slope ≈ 0 → Sitting Idle</div>
                <div style={{ fontSize: 23, color: C.text, marginTop: 8, lineHeight: 1.35 }}>
                  Adding <strong style={{ color: K.knob }}>+δ</strong> does not change time/msg. The extra work hides
                  inside idle wait.
                </div>
              </div>
              <div
                style={{
                  marginTop: 26,
                  fontSize: 21,
                  fontFamily: MONO,
                  color: C.muted,
                  lineHeight: 1.4,
                }}
              >
                δ = 10–59 ns fenced dependent multiplies
                <br />
                Averaged over 20M messages/run (sub-ns precision)
              </div>
            </div>
          </Card>
        </>
      )}

      {/* Cue 1 Calibration Cards */}
      {calOp > 0.005 && (
        <>
          <Card
            x={60}
            y={208}
            w={860}
            h={572}
            border={K.knob}
            title="CALIBRATION AT W = 1 (20M MSGS · SUB-NS PRECISION)"
            titleColor={K.knob}
            style={{ opacity: calOp, background: "rgba(20,27,46,0.62)" }}
          >
            <div />
          </Card>
          <Card
            x={950}
            y={208}
            w={910}
            h={272}
            border={K.fast}
            title="SLOPE ≈ 1 · CPU ON CRITICAL PATH"
            titleColor={K.fast}
            style={{ opacity: calOp * prog(t, T[1] + 2.8, 0.5), background: "rgba(20,27,46,0.68)" }}
          >
            <div style={{ paddingLeft: 320, paddingTop: 22, paddingRight: 18, fontFamily: FONT }}>
              <div style={{ fontSize: 27, fontWeight: 800, color: K.fast }}>Every +1 ns adds +1 ns</div>
              <div style={{ fontSize: 23, color: C.text, marginTop: 10, lineHeight: 1.35 }}>
                At <span style={{ fontFamily: MONO, color: K.knob }}>W = 1</span>, nothing overlaps: host send + wait
                is strictly serial, so <strong style={{ color: K.fast }}>+40 ns → +40.1 ns</strong>.
              </div>
            </div>
          </Card>
          <Card
            x={950}
            y={508}
            w={910}
            h={272}
            border={K.wait}
            title="SLOPE ≈ 0 · CPU SITTING IDLE"
            titleColor={K.wait}
            style={{ opacity: calOp * prog(t, T[1] + 3.6, 0.5), background: "rgba(20,27,46,0.68)" }}
          >
            <div style={{ paddingLeft: 320, paddingTop: 22, paddingRight: 18, fontFamily: FONT }}>
              <div style={{ fontSize: 27, fontWeight: 800, color: K.wait }}>Time/msg barely moves</div>
              <div style={{ fontSize: 23, color: C.text, marginTop: 10, lineHeight: 1.35 }}>
                When a CPU is mostly waiting on the loop, extra work <span style={{ fontFamily: MONO }}>+δ</span> is
                absorbed for free → <strong style={{ color: K.wait }}>flat line</strong>.
              </div>
            </div>
          </Card>
        </>
      )}

      {/* Cues 2–3 Side-by-Side Slope Plot Cards */}
      {slopeOp > 0.005 && (
        <>
          <Card
            x={60}
            y={204}
            w={855}
            h={532}
            border={K.fast}
            title="FAST STATE · 1 CARD CORE, W = 1024"
            titleColor={K.fast}
            style={{ opacity: slopeOp, background: "rgba(20,27,46,0.62)" }}
          >
            <div />
          </Card>
          <Card
            x={945}
            y={204}
            w={915}
            h={532}
            border={K.slow}
            title="SLOW STATE · 4 CARD CORES, W = 64 (76.1 ns/msg)"
            titleColor={K.slow}
            style={{ opacity: slopeOp, background: "rgba(20,27,46,0.62)" }}
          >
            <div />
          </Card>
        </>
      )}

      {/* Cues 4–5 4-Core Clue Cards */}
      {clueOp > 0.005 && (
        <>
          <Card
            x={60}
            y={210}
            w={720}
            h={566}
            border={K.slow}
            title="THROUGHPUT AT W = 1024 (4 CORES LOSE TO 1)"
            titleColor={K.slow}
            style={{ opacity: clueOp, background: "rgba(20,27,46,0.62)" }}
          >
            <div />
          </Card>
          <Card
            x={812}
            y={210}
            w={1048}
            h={566}
            border={K.knob}
            title="HOST TIME PER MESSAGE · SEND vs REPLY POLL"
            titleColor={K.knob}
            style={{ opacity: clueOp, background: "rgba(20,27,46,0.62)" }}
          >
            <div />
          </Card>
        </>
      )}

      {/* ================= MAIN SVG STAGE ================= */}
      <Stage>
        {/* ================= CUE 0: DELAY-INJECTION TRICK ================= */}
        {trickOp > 0.005 && (
          <g opacity={trickOp}>
            {/* Forward & Return lanes */}
            <line x1={325} y1={320} x2={835} y2={320} stroke={K.fwd} strokeWidth={5} />
            <polygon points="850,320 830,309 830,331" fill={K.fwd} />
            <text x={587} y={298} textAnchor="middle" fontFamily={MONO} fontSize={23} fontWeight={700} fill={K.fwd}>
              Forward requests →
            </text>

            <line x1={850} y1={620} x2={340} y2={620} stroke={K.ret} strokeWidth={5} />
            <polygon points="325,620 345,609 345,631" fill={K.ret} />
            <text x={587} y={656} textAnchor="middle" fontFamily={MONO} fontSize={23} fontWeight={700} fill={K.ret}>
              ← WC replies
            </text>

            {/* Vertical connectors */}
            <line x1={220} y1={390} x2={220} y2={320} stroke={K.fwd} strokeWidth={4} />
            <line x1={220} y1={320} x2={325} y2={320} stroke={K.fwd} strokeWidth={4} />
            <line x1={950} y1={320} x2={950} y2={390} stroke={K.fwd} strokeWidth={4} />
            <line x1={850} y1={320} x2={950} y2={320} stroke={K.fwd} strokeWidth={4} />
            <line x1={950} y1={550} x2={950} y2={620} stroke={K.ret} strokeWidth={4} />
            <line x1={950} y1={620} x2={850} y2={620} stroke={K.ret} strokeWidth={4} />
            <line x1={220} y1={620} x2={220} y2={550} stroke={K.ret} strokeWidth={4} />
            <line x1={325} y1={620} x2={220} y2={620} stroke={K.ret} strokeWidth={4} />

            <PacketStream pts={loopPts} travel={realT * 240} gap={84} size={11} color={K.fwd} opacity={0.85} />

            {/* Host CPU Block */}
            <rect
              x={110}
              y={390}
              width={220}
              height={160}
              rx={16}
              fill={C.panel}
              stroke={!injectOnCard ? K.knob : K.host}
              strokeWidth={!injectOnCard ? 4 : 3}
            />
            <text x={220} y={448} textAnchor="middle" fontFamily={FONT} fontSize={28} fontWeight={800} fill={K.host}>
              HOST CPU
            </text>
            <text x={220} y={482} textAnchor="middle" fontFamily={FONT} fontSize={21} fill={C.muted}>
              send + poll replies
            </text>

            {/* Card CPU Block */}
            <rect
              x={840}
              y={390}
              width={220}
              height={160}
              rx={16}
              fill={C.panel}
              stroke={injectOnCard ? K.knob : K.card}
              strokeWidth={injectOnCard ? 4 : 3}
            />
            <text x={950} y={448} textAnchor="middle" fontFamily={FONT} fontSize={28} fontWeight={800} fill={K.card}>
              CARD CPU
            </text>
            <text x={950} y={482} textAnchor="middle" fontFamily={FONT} fontSize={21} fill={C.muted}>
              poll + WC reply
            </text>

            {/* Glowing +δ delay block positioned in center with dashed target arrow */}
            <g>
              <rect
                x={420}
                y={402}
                width={334}
                height={136}
                rx={16}
                fill="rgba(249,199,79,0.14)"
                stroke={K.knob}
                strokeWidth={3.5}
              />
              <rect
                x={412}
                y={394}
                width={350}
                height={152}
                rx={20}
                fill="none"
                stroke={K.knob}
                strokeWidth={8}
                opacity={0.14 + 0.14 * pulse}
              />
              <text x={587} y={444} textAnchor="middle" fontFamily={MONO} fontSize={32} fontWeight={800} fill={K.knob}>
                +δ delay block
              </text>
              <text x={587} y={480} textAnchor="middle" fontFamily={FONT} fontSize={22} fontWeight={700} fill={C.text}>
                10–59 ns fenced multiplies
              </text>
              <text x={587} y={512} textAnchor="middle" fontFamily={FONT} fontSize={20} fill={C.muted}>
                inject on Host OR Card
              </text>

              {/* Target indicator arrows left & right */}
              <line
                x1={420}
                y1={470}
                x2={342}
                y2={470}
                stroke={K.knob}
                strokeWidth={!injectOnCard ? 4.5 : 2}
                strokeDasharray={!injectOnCard ? undefined : "6 6"}
                opacity={!injectOnCard ? 1 : 0.4}
              />
              <polygon
                points="332,470 348,461 348,479"
                fill={K.knob}
                opacity={!injectOnCard ? 1 : 0.4}
              />

              <line
                x1={754}
                y1={470}
                x2={828}
                y2={470}
                stroke={K.knob}
                strokeWidth={injectOnCard ? 4.5 : 2}
                strokeDasharray={injectOnCard ? undefined : "6 6"}
                opacity={injectOnCard ? 1 : 0.4}
              />
              <polygon
                points="838,470 822,461 822,479"
                fill={K.knob}
                opacity={injectOnCard ? 1 : 0.4}
              />
            </g>

            {/* Bottom question caption inside left card */}
            <text x={587} y={732} textAnchor="middle" fontFamily={FONT} fontSize={24} fontWeight={700} fill={C.text}>
              Measure change in <tspan fill={K.knob}>ns / msg</tspan> as δ increases
            </text>
          </g>
        )}

        {/* ================= CUE 1: CALIBRATION AT W = 1 ================= */}
        {calOp > 0.005 && (
          <g opacity={calOp}>
            <Axes
              x0={165}
              x1={865}
              y0={680}
              y1={318}
              sx={calSx}
              sy={calSy}
              xticks={[
                [0, "+0"],
                [10, "+10"],
                [20, "+20"],
                [40, "+40 ns"],
              ]}
              yticks={[
                [920, "920"],
                [940, "940"],
                [960, "960"],
                [975, "975"],
              ]}
              xlabel="Injected host delay δ (ns)"
              ylabel="Time per message (ns / msg)"
              size={22}
            />
            <Series pts={calPts} sx={calSx} sy={calSy} k={calSeriesK} color={K.knob} w={5} r={8} />

            {/* Exact data point labels */}
            {calPts.map(([dx, dy], i) => {
              const showPt = clamp01((calSeriesK * 3 - i + 0.4) / 0.4);
              const px = calSx(dx);
              const py = calSy(dy);
              return (
                <text
                  key={dx}
                  x={i === 0 ? px + 18 : i === 3 ? px - 12 : px - 6}
                  y={i === 0 ? py + 28 : py - 18}
                  textAnchor={i === 0 ? "start" : i === 3 ? "end" : "middle"}
                  fontFamily={MONO}
                  fontSize={22}
                  fontWeight={800}
                  fill={C.text}
                  opacity={showPt}
                >
                  {dy.toFixed(1)}
                </text>
              );
            })}

            {/* Slope 1.01 verification badge */}
            <g opacity={calBadgeOp}>
              <rect
                x={205}
                y={332}
                width={455}
                height={58}
                rx={12}
                fill="rgba(67,197,158,0.16)"
                stroke={K.fast}
                strokeWidth={2.5}
              />
              <text
                x={432}
                y={361}
                textAnchor="middle"
                dominantBaseline="central"
                fontFamily={MONO}
                fontSize={25}
                fontWeight={800}
                fill={K.fast}
              >
                +40 ns → +40.1 ns · slope 1.01 ✓
              </text>
            </g>

            {/* Right panel mini-signature 1: Slope ≈ 1 */}
            <g opacity={prog(t, T[1] + 2.8, 0.5)}>
              <line x1={995} y1={432} x2={1225} y2={432} stroke={C.dim} strokeWidth={2.5} />
              <line x1={995} y1={432} x2={995} y2={292} stroke={C.dim} strokeWidth={2.5} />
              <line x1={1008} y1={418} x2={1210} y2={305} stroke={K.fast} strokeWidth={5.5} strokeLinecap="round" />
              <circle cx={1008} cy={418} r={6} fill={K.fast} />
              <circle cx={1109} cy={361} r={6} fill={K.fast} />
              <circle cx={1210} cy={305} r={6} fill={K.fast} />
              <text x={1110} y={462} textAnchor="middle" fontFamily={MONO} fontSize={20} fill={C.muted}>
                +δ →
              </text>
            </g>

            {/* Right panel mini-signature 2: Slope ≈ 0 */}
            <g opacity={prog(t, T[1] + 3.6, 0.5)}>
              <line x1={995} y1={732} x2={1225} y2={732} stroke={C.dim} strokeWidth={2.5} />
              <line x1={995} y1={732} x2={995} y2={592} stroke={C.dim} strokeWidth={2.5} />
              <line x1={1008} y1={658} x2={1210} y2={658} stroke={K.wait} strokeWidth={5.5} strokeLinecap="round" />
              <circle cx={1008} cy={658} r={6} fill={K.wait} />
              <circle cx={1109} cy={658} r={6} fill={K.wait} />
              <circle cx={1210} cy={658} r={6} fill={K.wait} />
              <text x={1110} y={762} textAnchor="middle" fontFamily={MONO} fontSize={20} fill={C.muted}>
                +δ →
              </text>
            </g>
          </g>
        )}

        {/* ================= CUES 2–3: FAST vs SLOW SLOPE PLOTS ================= */}
        {slopeOp > 0.005 && (
          <g opacity={slopeOp}>
            {/* LEFT PLOT: Fast state (1 card core, W = 1024) */}
            <Axes
              x0={155}
              x1={865}
              y0={648}
              y1={314}
              sx={fastSx}
              sy={fastSy}
              xticks={[
                [0, "+0"],
                [20, "+20"],
                [40, "+40"],
                [60, "+60 ns"],
              ]}
              yticks={[
                [20, "20"],
                [40, "40"],
                [60, "60"],
                [80, "80"],
                [95, "95"],
              ]}
              xlabel="Injected delay δ (ns)"
              ylabel="ns / msg"
              size={21}
            />
            <Series pts={fastHostPts} sx={fastSx} sy={fastSy} k={fastHostK} color={K.fwd} w={5} r={7} />
            <Series pts={fastCardPts} sx={fastSx} sy={fastSy} k={fastCardK} color={K.card} w={5} r={7} />

            {/* Left plot inline series labels */}
            <g opacity={clamp01((fastHostK - 0.7) / 0.3)}>
              <rect x={205} y={326} width={280} height={42} rx={9} fill="rgba(76,201,240,0.14)" stroke={K.fwd} strokeWidth={2} />
              <text x={345} y={347} textAnchor="middle" dominantBaseline="central" fontFamily={MONO} fontSize={22} fontWeight={800} fill={K.fwd}>
                Host · slope 1.07
              </text>
            </g>
            <g opacity={clamp01((fastCardK - 0.7) / 0.3)}>
              <rect x={545} y={442} width={285} height={42} rx={9} fill="rgba(248,150,30,0.14)" stroke={K.card} strokeWidth={2} />
              <text x={687} y={463} textAnchor="middle" dominantBaseline="central" fontFamily={MONO} fontSize={22} fontWeight={800} fill={K.card}>
                Card · slope 1.07
              </text>
            </g>

            {/* RIGHT PLOT: Slow state (4 card cores, W = 64) */}
            <Axes
              x0={1085}
              x1={1805}
              y0={648}
              y1={314}
              sx={slowSx}
              sy={slowSy}
              xticks={[
                [0, "+0"],
                [10, "+10"],
                [20, "+20"],
                [32, "+32"],
                [45, "+45"],
                [59, "+59 ns"],
              ]}
              yticks={[
                [50, "50"],
                [60, "60"],
                [70, "70"],
                [80, "80"],
                [90, "90"],
              ]}
              xlabel="Injected delay δ (ns)"
              ylabel="ns / msg"
              size={21}
            />

            {/* Card series in Slow state (dead flat!) */}
            {slowCardOp > 0.005 && (
              <g opacity={slowCardOp}>
                <Series pts={slowCardPts} sx={slowSx} sy={slowSy} k={slowCardK} color={K.card} w={5.5} r={8} />
                {slowCardPts.map(([dx, dy], i) => {
                  const showPt = clamp01((slowCardK * 3 - i + 0.4) / 0.4);
                  const below = i === 2;
                  return (
                    <text
                      key={`c${dx}`}
                      x={slowSx(dx) + (i === 0 ? 18 : 0)}
                      y={below ? slowSy(dy) + 30 : slowSy(dy) - 18}
                      textAnchor={i === 0 ? "start" : "middle"}
                      fontFamily={MONO}
                      fontSize={21}
                      fontWeight={800}
                      fill={K.card}
                      opacity={showPt}
                    >
                      {dy.toFixed(1)}
                    </text>
                  );
                })}
                <g opacity={clamp01((slowCardK - 0.75) / 0.25)}>
                  <rect
                    x={1280}
                    y={322}
                    width={450}
                    height={46}
                    rx={10}
                    fill="rgba(248,150,30,0.14)"
                    stroke={K.card}
                    strokeWidth={2}
                  />
                  <text
                    x={1505}
                    y={345}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontFamily={MONO}
                    fontSize={23}
                    fontWeight={800}
                    fill={K.card}
                  >
                    Card slope −0.01 (just waiting)
                  </text>
                </g>
              </g>
            )}

            {/* Host series in Slow state (Cue 3: dramatic dip at +10 ns!) */}
            {slowHostOp > 0.005 && (
              <g opacity={slowHostOp}>
                <Series pts={slowHostPts} sx={slowSx} sy={slowSy} k={slowHostK} color={K.fwd} w={5.5} r={8} />
                {/* Point labels for Host series */}
                <text
                  x={slowSx(0) + 18}
                  y={slowSy(76.2) + 28}
                  fontFamily={MONO}
                  fontSize={20}
                  fontWeight={700}
                  fill={K.fwd}
                  opacity={clamp01(slowHostK * 4)}
                >
                  76.2
                </text>
                <text
                  x={slowSx(20) - 4}
                  y={slowSy(61.4) - 20}
                  textAnchor="middle"
                  fontFamily={MONO}
                  fontSize={21}
                  fontWeight={800}
                  fill={K.fwd}
                  opacity={clamp01((slowHostK - 0.55) / 0.2)}
                >
                  61.4
                </text>
                <text
                  x={slowSx(40)}
                  y={slowSy(78.3) - 20}
                  textAnchor="middle"
                  fontFamily={MONO}
                  fontSize={21}
                  fontWeight={800}
                  fill={K.fwd}
                  opacity={clamp01((slowHostK - 0.85) / 0.15)}
                >
                  78.3
                </text>

                {/* Highlight ring + big green badge for +10 ns -> 60.9 (20% FASTER!) */}
                <g opacity={slowDipOp}>
                  <circle
                    cx={slowSx(10)}
                    cy={slowSy(60.9)}
                    r={16 + 6 * pulse}
                    fill="none"
                    stroke={K.fast}
                    strokeWidth={3.5}
                  />
                  <polyline
                    points={`${slowSx(10)},${slowSy(60.9) + 18} ${slowSx(10)},610 1405,610`}
                    fill="none"
                    stroke={K.fast}
                    strokeWidth={2.5}
                    strokeDasharray="6 4"
                  />
                  <rect
                    x={1405}
                    y={546}
                    width={390}
                    height={76}
                    rx={12}
                    fill="rgba(67,197,158,0.16)"
                    stroke={K.fast}
                    strokeWidth={3}
                  />
                  <text
                    x={1600}
                    y={572}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontFamily={MONO}
                    fontSize={22}
                    fontWeight={800}
                    fill={K.fast}
                  >
                    Host +10 ns → 60.9 ns/msg
                  </text>
                  <text
                    x={1600}
                    y={602}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontFamily={FONT}
                    fontSize={23}
                    fontWeight={800}
                    fill={C.text}
                  >
                    20% FASTER!
                  </text>
                </g>
              </g>
            )}
          </g>
        )}

        {/* ================= CUES 4–5: 4-CORE CLUE AT W = 1024 ================= */}
        {clueOp > 0.005 && (
          <g opacity={clueOp}>
            {/* Left BarChart: 50.5 vs 23.9 M/s */}
            <BarChart
              x0={135}
              x1={705}
              y0={668}
              y1={335}
              max={60}
              bars={[
                { label: "1 card core", sub: "W = 1024", value: 50.5, color: K.fast, text: "50.5 M/s" },
                { label: "4 card cores", sub: "W = 1024", value: 23.9, color: K.slow, text: "23.9 M/s" },
              ]}
              k={(i) => (i === 0 ? bar1K : bar4K)}
              size={34}
              labelSize={25}
              barW={155}
            />

            {/* Right Panel: Stacked SegBar pair (Host send vs reply poll) */}
            <g fontFamily={FONT} fontSize={22} fontWeight={700}>
              <rect x={852} y={282} width={22} height={22} rx={5} fill={K.fwd} />
              <text x={884} y={301} fill={K.fwd}>
                Host send
              </text>
              <rect x={1035} y={282} width={22} height={22} rx={5} fill={K.fast} />
              <text x={1067} y={301} fill={K.fast}>
                Reply poll (1c)
              </text>
              <rect x={1275} y={282} width={22} height={22} rx={5} fill={K.slow} />
              <text x={1307} y={301} fill={K.slow}>
                Reply poll (4c · 4 sub-rings)
              </text>
            </g>

            {/* Row 1: 1 card core */}
            <text x={852} y={366} fontFamily={FONT} fontSize={24} fontWeight={800} fill={C.text}>
              1 card core <tspan fill={K.fast} fontFamily={MONO}>(50.5 M/s)</tspan>
            </text>
            <SegBar
              x={852}
              y={382}
              h={62}
              px={19}
              segs={[
                { label: "Send", value: 9.2, color: K.fwd, text: "9.2 ns" },
                { label: "Reply poll", value: 13.6, color: K.fast, text: "13.6 ns poll" },
              ]}
              k={seg1K}
              size={22}
            />

            {/* Dashed alignment guide showing Send stays flat (9.2–9.3 ns) */}
            <line
              x1={852 + 9.25 * 19}
              y1={376}
              x2={852 + 9.25 * 19}
              y2={598}
              stroke={K.fwd}
              strokeWidth={2}
              strokeDasharray="6 5"
              opacity={clueBraceOp * 0.85}
            />
            <text
              x={852 + 9.25 * 19 + 10}
              y={484}
              fontFamily={FONT}
              fontSize={21}
              fontWeight={700}
              fill={K.fwd}
              opacity={clueBraceOp}
            >
              Send stays flat (9.2–9.3 ns)
            </text>

            {/* Row 2: 4 card cores */}
            <text x={852} y={518} fontFamily={FONT} fontSize={24} fontWeight={800} fill={C.text}>
              4 card cores <tspan fill={K.slow} fontFamily={MONO}>(23.9 M/s)</tspan>
            </text>
            <SegBar
              x={852}
              y={534}
              h={62}
              px={19}
              segs={[
                { label: "Send", value: 9.3, color: K.fwd, text: "9.3 ns" },
                { label: "Reply poll", value: 32.5, color: K.slow, text: "32.5 ns reply poll" },
              ]}
              k={seg4K}
              size={22}
            />

            {/* Brace highlighting reply poll jump */}
            <Brace
              x1={852 + 9.3 * 19}
              x2={852 + (9.3 + 32.5) * 19}
              y={606}
              dir={1}
              color={K.knob}
              label="Reply poll jumps 13.6 → 32.5 ns across 4 reply sub-rings"
              size={23}
              opacity={clueBraceOp}
            />

            {/* Concise footer callout inside right card */}
            <g opacity={clueBraceOp}>
              <rect
                x={852}
                y={696}
                width={968}
                height={54}
                rx={12}
                fill="rgba(249,65,68,0.12)"
                stroke={K.slow}
                strokeWidth={2}
              />
              <text
                x={1336}
                y={723}
                textAnchor="middle"
                dominantBaseline="central"
                fontFamily={FONT}
                fontSize={23}
                fontWeight={700}
                fill={C.text}
              >
                Host send barely moves (<tspan fill={K.fwd} fontFamily={MONO}>9.2 → 9.3 ns</tspan>) — the slowdown is polling{" "}
                <tspan fill={K.slow} fontWeight={800}>4 reply sub-rings</tspan>
              </text>
            </g>
          </g>
        )}
      </Stage>

      {/* ================= FOREGROUND HTML OVERLAYS (ABOVE <Stage>) ================= */}

      {/* Top Experiment KnobBar (Cues 0–5) */}
      {knobOp > 0.005 && (
        <KnobBar
          y={118}
          size={20}
          fixed={["Closed loop", "WC replies", "20M msgs/run"]}
          knob="Inject δ"
          options={["Calibrate (W=1)", "Fast (1c)", "Slow (4c)", "4c vs 1c"]}
          active={knobActive}
          k={knobOp}
        />
      )}

      {/* Cues 2–3 Bottom Summary Strip below the two slope plots */}
      {slopeOp > 0.005 && (
        <>
          <div
            style={{
              position: "absolute",
              left: 60,
              top: 754,
              width: 855,
              opacity: slopeOp * prog(t, T[2] + 2.2, 0.5),
              background: "rgba(67,197,158,0.12)",
              border: `2px solid ${K.fast}`,
              borderRadius: 12,
              padding: "12px 22px",
              boxSizing: "border-box",
              fontFamily: FONT,
              fontSize: 24,
              fontWeight: 700,
              color: C.text,
            }}
          >
            <span style={{ color: K.fast }}>Fast state:</span> Both CPUs interlocked on critical path (slope{" "}
            <span style={{ fontFamily: MONO, color: K.fast }}>1.07</span>)
          </div>

          {cue2NoteOp > 0.005 && (
            <div
              style={{
                position: "absolute",
                left: 945,
                top: 754,
                width: 915,
                opacity: slopeOp * cue2NoteOp,
                background: "rgba(248,150,30,0.12)",
                border: `2px solid ${K.card}`,
                borderRadius: 12,
                padding: "12px 22px",
                boxSizing: "border-box",
                fontFamily: FONT,
                fontSize: 24,
                fontWeight: 700,
                color: C.text,
              }}
            >
              <span style={{ color: K.card }}>Card CPU:</span> +59 ns changes nothing (
              <span style={{ fontFamily: MONO }}>76.1 → 75.6 ns</span> · slope{" "}
              <span style={{ fontFamily: MONO, color: K.card }}>−0.01</span>)
            </div>
          )}

          {cue3NoteOp > 0.005 && (
            <div
              style={{
                position: "absolute",
                left: 945,
                top: 754,
                width: 915,
                opacity: slopeOp * cue3NoteOp,
                background: "rgba(94,234,212,0.10)",
                border: `2px solid ${K.inf}`,
                borderRadius: 12,
                padding: "10px 20px",
                boxSizing: "border-box",
                display: "flex",
                alignItems: "center",
                gap: 14,
                fontFamily: FONT,
                fontSize: 23,
                fontWeight: 700,
                color: C.text,
              }}
            >
              <InfPill size={20} />
              <span>Both CPUs sit idle; spacing host bursts breaks the slow rhythm.</span>
            </div>
          )}
        </>
      )}

      {/* Cue 5 Bridge to Exp 5 */}
      {nextOp > 0.005 && (
        <NextQuestion x={60} y={804} w={1800} size={24} k={nextOp}>
          4 card cores appear in both slowdowns. Before blaming the loop, rule out 2 hardware suspects:{" "}
          <strong style={{ color: K.knob }}>broken WC</strong> &{" "}
          <strong style={{ color: K.knob }}>128{"\u00A0"}B false sharing</strong>.
        </NextQuestion>
      )}
    </>
  );
}

export const S06: SceneDef = {
  id: "S06",
  title: "Exp 4 · Who owns the critical path?",
  stage: 2,
  source: "Closed-loop delay injection · 2×10⁷ msgs/run · host vs card slopes",
  dur: DUR,
  cues,
  C: Body,
};
