import React from "react";
import {
  C,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  PacketStream,
  SceneDef,
  Stage,
  clamp01,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import { Brace, InfPill, KnobBar, NextQuestion, Rig, SegBar, rigGeom, rigRoutes } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 13.28, "**Question:** In a closed loop, the host caps in-flight requests at window **W** and waits for replies. At **13.5 M/s**, which leg is slow: **forward, card, or return**?", "Experiment three opens up the closed loop, where the host caps in-flight requests at window W and waits for replies. At thirteen point five million, which leg is slow: forward, on-card, or return?"], // 0
  [13.63, 26.19, "**Fixed:** closed loop, WC replies, **4 card cores**, synchronized timestamps. **One knob:** window **W = 1** (no queue) vs **W = 64** (slow state).", "Fixed baseline: closed loop, write-combining replies, four card cores, and synchronized hardware timestamps. One knob: window one with no queueing, versus window sixty-four in the slow state."], // 1
  [26.54, 38.04, "**W = 1** (no queue): median round trip is **0.92 µs** = **0.45 µs** forward + **0.04 µs** on card + **0.43 µs** return.", "At window one, nothing queues. Round trip is zero point nine two microseconds: zero point four five forward, zero point zero four on card, and zero point four three back."], // 2
  [38.39, 53.81, "**W = 64** (**13.5 M/s**): round trip swells to **3.7 µs**. On-card stays **0.04 µs**, return **~0.5 µs** (`0.49–0.52`), while forward swells to **3.1–3.2 µs** (`+2.7 µs`).", "At sixty-four, throughput stalls at thirteen point five million and round trip swells to three point seven microseconds. On-card stays zero point zero four and return stays half a microsecond; the extra two point seven waits on the forward leg."], // 3
  [54.16, 73.04, "**Why + caveat:** Host sends **9.1 ns** apart; card sees one every **~75 ns** → **~42 queued**. Yet 2 runs without page pre-touch hit **13.4 M/s** with the queue on **return** (`0.7` / `2.7 µs`) — **[INF]** loop rhythm.", "The host sends nine nanoseconds apart while the card sees one every seventy-five nanoseconds, queuing about forty-two messages. Caveat: two runs without pre-touched pages hit the same thirteen point four million, but flipped the queue to return. Our reading: loop rhythm, not one fixed stage limit."], // 4
  [73.39, 83.67, "**Next:** About **42 messages** pile up in transit at 13 M/s. Are the host CPU or card CPUs actually busy, or are **both sitting idle**?", "About forty-two messages pile up in transit at thirteen million per second. Next question: is either the host or card C P U actually busy, or are both sitting idle?"], // 5
];
const DUR = 85.07; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const realT = useRealT();

  // Phase 1 (Cue 0): Closed-loop anatomy & question
  const rigOp = fade(t, T[0], T[1], 0.45);
  const g = rigGeom(0.62, 340, 80);
  const routes = rigRoutes(g);
  const pulse = (phase: number) => 0.5 + 0.5 * Math.sin(realT * 4.2 + phase);

  // Phase 2 (Cues 1–5): Stacked-bar comparison workspace
  const workOp = prog(t, T[1] + 0.3, 0.5);
  const knobOp = prog(t, T[1] + 0.25, 0.5);
  const knobActive = t < T[2] - 0.1 ? -1 : lerp(0, 1, prog(t, T[3], 0.6));

  // Placeholder tracks visible only in Cue 1 before bars populate
  const placeholderOp = fade(t, T[1] + 0.4, T[2], 0.4);

  // Row 1: W = 1 baseline (Cue 2+)
  const w1Op = prog(t, T[2] + 0.15, 0.45);
  const w1BarK = prog(t, T[2] + 0.35, 1.0);
  const w1NoteOp = prog(t, T[2] + 1.4, 0.45);

  // Row 2: W = 64 slow state (Cue 3+)
  const w64Op = prog(t, T[3] + 0.15, 0.45);
  const w64BarK = prog(t, T[3] + 0.35, 1.2);
  const braceOp = prog(t, T[3] + 1.5, 0.5);

  // Cue 4: Why (~42 queued) + Honest caveat (flipped queue)
  const whyOp = prog(t, T[4] + 0.2, 0.5);
  const caveatOp = prog(t, T[4] + 0.7, 0.5);
  const caveatBarK = prog(t, T[4] + 1.1, 1.0);

  // Cue 5: Bridge to Exp 4
  const nextOp = prog(t, T[5] + 0.15, 0.5);

  // Stacked bar geometry for Row 1 & Row 2
  const barX = 340;
  const pxPerUs = 340; // 4.0 µs = 1360 px -> x = 340..1700
  const row1Y = 276;
  const row2Y = 408;
  const barH = 62;

  return (
    <>
      {/* ================= BACKGROUND HTML CARDS (BEHIND <Stage>) ================= */}
      {whyOp > 0.005 && (
        <Card
          x={60}
          y={554}
          w={566}
          h={226}
          border={K.fwd}
          title="WHY ~42 QUEUE IN FLIGHT"
          titleColor={K.fwd}
          style={{ opacity: whyOp, background: "rgba(20,27,46,0.72)" }}
        >
          <div />
        </Card>
      )}

      {caveatOp > 0.005 && (
        <Card
          x={652}
          y={554}
          w={1208}
          h={226}
          border={K.knob}
          title="HONEST CAVEAT · 2 RUNS WITHOUT PAGE PRE-TOUCH (13.4 M/s · 3.8 µs)"
          titleColor={K.knob}
          style={{ opacity: caveatOp, background: "rgba(20,27,46,0.72)" }}
        >
          <div
            style={{
              position: "absolute",
              left: 28,
              bottom: 16,
              right: 28,
              display: "flex",
              alignItems: "center",
              gap: 14,
              fontFamily: FONT,
              fontSize: 22,
              fontWeight: 600,
              color: C.text,
            }}
          >
            <InfPill size={20} />
            <span>
              Queue flipped to <strong style={{ color: K.ret }}>return (2.7 µs)</strong>, forward{" "}
              <strong style={{ color: K.fwd }}>0.7 µs</strong> — loop rhythm, not one fixed stage limit.
            </span>
          </div>
        </Card>
      )}

      {/* ================= MAIN SVG STAGE ================= */}
      <Stage>
        {/* ================= CUE 0: CLOSED-LOOP ANATOMY ================= */}
        {rigOp > 0.005 && (
          <g opacity={rigOp}>
            <Rig
              g={g}
              cores={4}
              activeCore={Math.floor(realT * 4) % 4}
              ringFill={{ cring: 5, hring: 4 }}
              laneText={false}
            />
            <PacketStream
              pts={routes.loop}
              travel={realT * 260}
              gap={72}
              size={12}
              color={K.fwd}
              colorAt={(i) => (i % 3 === 0 ? K.fwd : i % 3 === 1 ? K.card : K.ret)}
              opacity={0.92}
            />

            {/* Timestamp Marker 1: Forward leg */}
            <g opacity={prog(t, T[0] + 0.8, 0.5)}>
              <circle
                cx={(g.r.wq.x + g.r.wq.w + g.r.cring.x) / 2}
                cy={g.fwdY}
                r={14 + 8 * pulse(0)}
                fill="none"
                stroke={K.fwd}
                strokeWidth={2.5}
                opacity={0.55 * (1 - 0.5 * pulse(0))}
              />
              <rect
                x={(g.r.wq.x + g.r.wq.w + g.r.cring.x) / 2 - 140}
                y={g.fwdY - 82}
                width={280}
                height={52}
                rx={12}
                fill="rgba(20,27,46,0.94)"
                stroke={K.fwd}
                strokeWidth={2.5}
              />
              <text
                x={(g.r.wq.x + g.r.wq.w + g.r.cring.x) / 2}
                y={g.fwdY - 56}
                textAnchor="middle"
                dominantBaseline="central"
                fontFamily={FONT}
                fontSize={24}
                fontWeight={800}
                fill={K.fwd}
              >
                ① Forward (t₁ → t₂)
              </text>
            </g>

            {/* Timestamp Marker 2: On card */}
            <g opacity={prog(t, T[0] + 1.5, 0.5)}>
              <circle
                cx={g.r.ccores.x + g.r.ccores.w / 2}
                cy={g.r.ccores.y + g.r.ccores.h / 2}
                r={14 + 8 * pulse(2.1)}
                fill="none"
                stroke={K.card}
                strokeWidth={2.5}
                opacity={0.55 * (1 - 0.5 * pulse(2.1))}
              />
              <line
                x1={g.r.card.x + g.r.card.w + 8}
                y1={g.r.ccores.y + g.r.ccores.h / 2}
                x2={g.r.card.x + g.r.card.w + 42}
                y2={g.r.ccores.y + g.r.ccores.h / 2}
                stroke={K.card}
                strokeWidth={2.5}
                strokeDasharray="6 4"
              />
              <rect
                x={g.r.card.x + g.r.card.w + 42}
                y={g.r.ccores.y + g.r.ccores.h / 2 - 36}
                width={296}
                height={72}
                rx={12}
                fill="rgba(20,27,46,0.94)"
                stroke={K.card}
                strokeWidth={2.5}
              />
              <text
                x={g.r.card.x + g.r.card.w + 190}
                y={g.r.ccores.y + g.r.ccores.h / 2 - 10}
                textAnchor="middle"
                dominantBaseline="central"
                fontFamily={FONT}
                fontSize={24}
                fontWeight={800}
                fill={K.card}
              >
                ② On card (t₂ → t₃)
              </text>
              <text
                x={g.r.card.x + g.r.card.w + 190}
                y={g.r.ccores.y + g.r.ccores.h / 2 + 18}
                textAnchor="middle"
                dominantBaseline="central"
                fontFamily={FONT}
                fontSize={21}
                fill={C.muted}
              >
                4 card cores polling
              </text>
            </g>

            {/* Timestamp Marker 3: Return leg */}
            <g opacity={prog(t, T[0] + 2.2, 0.5)}>
              <circle
                cx={(g.r.wc.x + g.r.hring.x + g.r.hring.w) / 2}
                cy={g.retY}
                r={14 + 8 * pulse(4.2)}
                fill="none"
                stroke={K.ret}
                strokeWidth={2.5}
                opacity={0.55 * (1 - 0.5 * pulse(4.2))}
              />
              <rect
                x={(g.r.wc.x + g.r.hring.x + g.r.hring.w) / 2 - 140}
                y={g.retY + 28}
                width={280}
                height={52}
                rx={12}
                fill="rgba(20,27,46,0.94)"
                stroke={K.ret}
                strokeWidth={2.5}
              />
              <text
                x={(g.r.wc.x + g.r.hring.x + g.r.hring.w) / 2}
                y={g.retY + 54}
                textAnchor="middle"
                dominantBaseline="central"
                fontFamily={FONT}
                fontSize={24}
                fontWeight={800}
                fill={K.ret}
              >
                ③ Return (t₃ → t₄)
              </text>
            </g>

            {/* Closed-loop window cap badge on left */}
            <g opacity={prog(t, T[0] + 0.5, 0.5)}>
              <rect
                x={64}
                y={346}
                width={264}
                height={120}
                rx={14}
                fill="rgba(20,27,46,0.92)"
                stroke={K.knob}
                strokeWidth={2.5}
              />
              <text x={196} y={384} textAnchor="middle" fontFamily={FONT} fontSize={22} fontWeight={800} fill={K.knob}>
                CLOSED-LOOP CAP
              </text>
              <text x={196} y={418} textAnchor="middle" fontFamily={MONO} fontSize={26} fontWeight={800} fill={C.text}>
                in-flight ≤ W
              </text>
              <text x={196} y={448} textAnchor="middle" fontFamily={FONT} fontSize={20} fill={C.muted}>
                host waits for replies
              </text>
              <line
                x1={328}
                y1={406}
                x2={g.r.host.x - 4}
                y2={406}
                stroke={K.knob}
                strokeWidth={2.5}
                strokeDasharray="6 4"
              />
            </g>
          </g>
        )}

        {/* ================= CUES 1–5: STACKED BAR WORKSPACE ================= */}
        {workOp > 0.005 && (
          <g opacity={workOp}>
            {/* Legend row at y = 190 */}
            <g fontFamily={FONT} fontSize={23} fontWeight={700}>
              <rect x={340} y={185} width={22} height={22} rx={5} fill={K.fwd} />
              <text x={372} y={204} fill={K.fwd}>
                Forward (host → card)
              </text>
              <rect x={665} y={185} width={22} height={22} rx={5} fill={K.card} />
              <text x={697} y={204} fill={K.card}>
                On card (processing)
              </text>
              <rect x={988} y={185} width={22} height={22} rx={5} fill={K.ret} />
              <text x={1020} y={204} fill={K.ret}>
                Return (WC reply → host)
              </text>
            </g>

            {/* Vertical gridlines & axis ticks 0..4 µs */}
            {[0, 1, 2, 3, 4].map((u) => {
              const gx = barX + u * pxPerUs;
              return (
                <g key={u}>
                  <line
                    x1={gx}
                    y1={250}
                    x2={gx}
                    y2={492}
                    stroke={u === 0 ? C.dim : C.line}
                    strokeWidth={u === 0 ? 2.5 : 1.5}
                    strokeDasharray={u === 0 ? undefined : "6 6"}
                    opacity={u === 0 ? 0.9 : 0.65}
                  />
                  <text x={gx} y={242} textAnchor="middle" fontFamily={MONO} fontSize={20} fill={C.muted}>
                    {u}.0 µs
                  </text>
                </g>
              );
            })}

            {/* Cue 1 preview tracks before measurements animate in */}
            {placeholderOp > 0.005 && (
              <g opacity={placeholderOp}>
                <rect
                  x={barX}
                  y={row1Y}
                  width={1360}
                  height={barH}
                  rx={10}
                  fill="rgba(20,27,46,0.5)"
                  stroke={C.line}
                  strokeWidth={2}
                  strokeDasharray="10 8"
                />
                <text
                  x={barX + 680}
                  y={row1Y + barH / 2}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontFamily={FONT}
                  fontSize={24}
                  fill={C.muted}
                >
                  W = 1 baseline — zero queueing in flight
                </text>
                <rect
                  x={barX}
                  y={row2Y}
                  width={1360}
                  height={barH}
                  rx={10}
                  fill="rgba(20,27,46,0.5)"
                  stroke={K.knob}
                  strokeWidth={2}
                  strokeDasharray="10 8"
                  opacity={0.75}
                />
                <text
                  x={barX + 680}
                  y={row2Y + barH / 2}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontFamily={FONT}
                  fontSize={24}
                  fill={K.knob}
                >
                  W = 64 slow state — where does the extra round-trip time sit?
                </text>
              </g>
            )}

            {/* Row 1: W = 1 baseline (0.92 µs) */}
            {w1Op > 0.005 && (
              <g opacity={w1Op}>
                <SegBar
                  x={barX}
                  y={row1Y}
                  h={barH}
                  px={pxPerUs}
                  segs={[
                    { label: "Forward", value: 0.45, color: K.fwd, text: "0.45" },
                    { label: "On card", value: 0.04, color: K.card, text: "0.04" },
                    { label: "Return", value: 0.43, color: K.ret, text: "0.43" },
                  ]}
                  k={w1BarK}
                  size={22}
                />
                {/* Total 0.92 µs right of bar */}
                <g opacity={clamp01((w1BarK - 0.7) / 0.3)}>
                  <text
                    x={barX + 0.92 * pxPerUs + 22}
                    y={row1Y + barH / 2 - 4}
                    dominantBaseline="central"
                    fontFamily={MONO}
                    fontSize={34}
                    fontWeight={800}
                    fill={C.text}
                  >
                    0.92 µs
                  </text>
                  <text
                    x={barX + 0.92 * pxPerUs + 22}
                    y={row1Y + barH / 2 + 24}
                    dominantBaseline="central"
                    fontFamily={FONT}
                    fontSize={20}
                    fill={C.muted}
                  >
                    median RTT
                  </text>
                </g>
                {/* Clean callout for tiny on-card time */}
                <g opacity={w1NoteOp}>
                  <rect
                    x={860}
                    y={row1Y + 8}
                    width={430}
                    height={46}
                    rx={10}
                    fill="rgba(248,150,30,0.14)"
                    stroke={K.card}
                    strokeWidth={2}
                  />
                  <text
                    x={1075}
                    y={row1Y + 31}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontFamily={FONT}
                    fontSize={22}
                    fontWeight={700}
                    fill={K.card}
                  >
                    On card is tiny: only 0.04 µs
                  </text>
                </g>
              </g>
            )}

            {/* Row 2: W = 64 slow state (3.7 µs) */}
            {w64Op > 0.005 && (
              <g opacity={w64Op}>
                {/* Reference drop-line from W = 1 forward end (0.45 µs) */}
                <line
                  x1={barX + 0.45 * pxPerUs}
                  y1={row1Y + barH + 4}
                  x2={barX + 0.45 * pxPerUs}
                  y2={row2Y + barH + 14}
                  stroke={K.knob}
                  strokeWidth={2}
                  strokeDasharray="6 5"
                  opacity={braceOp * 0.85}
                />
                <SegBar
                  x={barX}
                  y={row2Y}
                  h={barH}
                  px={pxPerUs}
                  segs={[
                    { label: "Forward", value: 3.11, color: K.fwd, text: "3.1–3.2 µs forward" },
                    { label: "On card", value: 0.04, color: K.card, text: "0.04" },
                    { label: "Return", value: 0.52, color: K.ret, text: "~0.5 µs" },
                  ]}
                  k={w64BarK}
                  size={22}
                />
                {/* Total 3.7 µs right of bar */}
                <g opacity={clamp01((w64BarK - 0.75) / 0.25)}>
                  <text
                    x={barX + 3.67 * pxPerUs + 22}
                    y={row2Y + barH / 2 - 6}
                    dominantBaseline="central"
                    fontFamily={MONO}
                    fontSize={36}
                    fontWeight={800}
                    fill={K.slow}
                  >
                    3.7 µs
                  </text>
                  <text
                    x={barX + 3.67 * pxPerUs + 22}
                    y={row2Y + barH / 2 + 24}
                    dominantBaseline="central"
                    fontFamily={FONT}
                    fontSize={20}
                    fill={C.muted}
                  >
                    at 13.5 M/s
                  </text>
                </g>

                {/* Brace highlighting +2.7 µs waiting on forward */}
                <Brace
                  x1={barX + 0.45 * pxPerUs}
                  x2={barX + 3.11 * pxPerUs}
                  y={row2Y + barH + 8}
                  dir={1}
                  color={K.knob}
                  label="+2.7 µs waiting on forward (card stays 0.04 µs · return 0.49–0.52 µs)"
                  size={23}
                  opacity={braceOp}
                />
              </g>
            )}

            {/* Cue 4 Left Card SVG: Burst queueing timeline */}
            {whyOp > 0.005 && (
              <g opacity={whyOp}>
                {/* Host bursts: 9.1 ns apart (tight cyan ticks) */}
                <text x={86} y={634} fontFamily={FONT} fontSize={21} fontWeight={700} fill={K.fwd}>
                  Host bursts: 9.1 ns apart
                </text>
                <line x1={86} y1={658} x2={596} y2={658} stroke={C.line} strokeWidth={2} />
                {Array.from({ length: 16 }, (_, i) => (
                  <rect
                    key={i}
                    x={94 + i * 16}
                    y={644}
                    width={7}
                    height={26}
                    rx={3}
                    fill={K.fwd}
                    opacity={0.92}
                  />
                ))}
                <text x={364} y={664} fontFamily={MONO} fontSize={20} fontWeight={700} fill={K.fwd}>
                  9.1 ns / send →
                </text>

                {/* Card arrivals: ~75 ns apart (spaced orange ticks) */}
                <text x={86} y={702} fontFamily={FONT} fontSize={21} fontWeight={700} fill={K.card}>
                  Card arrivals: ~75 ns apart
                </text>
                <line x1={86} y1={726} x2={596} y2={726} stroke={C.line} strokeWidth={2} />
                {Array.from({ length: 5 }, (_, i) => (
                  <rect
                    key={i}
                    x={94 + i * 108}
                    y={712}
                    width={10}
                    height={26}
                    rx={3}
                    fill={K.card}
                    opacity={0.92}
                  />
                ))}
                <text x={86} y={762} fontFamily={MONO} fontSize={22} fontWeight={800} fill={K.knob}>
                  13.5 M/s × 3.1 µs → ~42 queued
                </text>
              </g>
            )}

            {/* Cue 4 Right Panel SVG: Flipped caveat SegBar */}
            {caveatOp > 0.005 && (
              <g opacity={caveatOp}>
                <SegBar
                  x={682}
                  y={652}
                  h={48}
                  px={275}
                  segs={[
                    { label: "Fwd", value: 0.73, color: K.fwd, text: "0.7 µs" },
                    { label: "Card", value: 0.04, color: K.card, text: "0.04" },
                    { label: "Return", value: 2.67, color: K.ret, text: "2.7 µs return (flipped!)" },
                  ]}
                  k={caveatBarK}
                  size={21}
                />
                <text
                  x={682 + 3.44 * 275 + 22}
                  y={652 + 24}
                  dominantBaseline="central"
                  fontFamily={MONO}
                  fontSize={32}
                  fontWeight={800}
                  fill={K.knob}
                  opacity={clamp01((caveatBarK - 0.7) / 0.3)}
                >
                  3.8 µs
                </text>
              </g>
            )}
          </g>
        )}
      </Stage>

      {/* ================= FOREGROUND HTML OVERLAYS (ABOVE <Stage>) ================= */}

      {/* Cue 0 Question Banner */}
      {rigOp > 0.005 && (
        <div
          style={{
            position: "absolute",
            left: 160,
            top: 668,
            width: 1600,
            opacity: rigOp * prog(t, T[0] + 2.6, 0.5),
            transform: `translateY(${(1 - prog(t, T[0] + 2.6, 0.5)) * 18}px)`,
            background: "rgba(20,27,46,0.92)",
            border: `2px solid ${K.knob}`,
            borderRadius: 16,
            padding: "18px 32px",
            boxSizing: "border-box",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            fontFamily: FONT,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            <span
              style={{
                fontSize: 20,
                fontWeight: 800,
                letterSpacing: 1.5,
                color: C.bg,
                background: K.knob,
                borderRadius: 8,
                padding: "5px 14px",
              }}
            >
              QUESTION
            </span>
            <span style={{ fontSize: 30, fontWeight: 700, color: C.text }}>
              At <span style={{ color: K.slow }}>13.5 M/s</span>, which of the 3 legs absorbs the extra time?
            </span>
          </div>
          <div style={{ display: "flex", gap: 12 }}>
            {[
              ["Forward", K.fwd],
              ["On card", K.card],
              ["Return", K.ret],
            ].map(([name, col]) => (
              <span
                key={name}
                style={{
                  fontSize: 22,
                  fontWeight: 800,
                  color: col,
                  border: `2px solid ${col}`,
                  borderRadius: 999,
                  padding: "4px 16px",
                  background: "rgba(11,16,32,0.7)",
                }}
              >
                {name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Cues 1–5 Top Experiment KnobBar */}
      {knobOp > 0.005 && (
        <KnobBar
          y={118}
          size={22}
          fixed={["Closed loop", "WC replies", "4 card cores", "Synced timestamps"]}
          knob="In-flight window W"
          options={["W = 1 (no queue)", "W = 64 (slow)"]}
          active={knobActive}
          k={knobOp}
        />
      )}

      {/* Row 1 Left Label (W = 1) */}
      {workOp > 0.005 && (
        <div
          style={{
            position: "absolute",
            left: 60,
            top: row1Y + 2,
            width: 260,
            textAlign: "right",
            opacity: workOp * (placeholderOp > 0.5 ? 0.65 : Math.max(0.45, w1Op)),
            fontFamily: FONT,
          }}
        >
          <div style={{ fontSize: 30, fontWeight: 800, color: C.text, fontFamily: MONO }}>W = 1</div>
          <div style={{ fontSize: 21, color: C.muted, marginTop: 2 }}>1.01 M/s · no queue</div>
        </div>
      )}

      {/* Row 2 Left Label (W = 64) */}
      {workOp > 0.005 && (
        <div
          style={{
            position: "absolute",
            left: 60,
            top: row2Y + 2,
            width: 260,
            textAlign: "right",
            opacity: workOp * (placeholderOp > 0.5 ? 0.65 : Math.max(0.3, w64Op)),
            fontFamily: FONT,
          }}
        >
          <div style={{ fontSize: 30, fontWeight: 800, color: w64Op > 0.5 ? K.slow : C.text, fontFamily: MONO }}>
            W = 64
          </div>
          <div style={{ fontSize: 21, color: C.muted, marginTop: 2 }}>13.5 M/s · pre-touched</div>
        </div>
      )}

      {/* Cue 5 Bridge to Exp 4 */}
      {nextOp > 0.005 && (
        <NextQuestion x={60} y={804} w={1800} k={nextOp}>
          ~42 messages pile up in transit at 13 M/s. Are the host or card CPUs actually busy — or are{" "}
          <strong style={{ color: K.knob }}>both sitting idle</strong>?
        </NextQuestion>
      )}
    </>
  );
}

export const S05: SceneDef = {
  id: "S05",
  title: "Exp 3 · Where time sits in the 13 M/s loop",
  stage: 2,
  source: "Closed-loop timestamps · 4 card cores · WC replies · W = 1 vs 64",
  dur: DUR,
  cues,
  C: Body,
};
