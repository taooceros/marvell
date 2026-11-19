import React from "react";
import {
  Arrow,
  Axes,
  C,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  Packet,
  PacketStream,
  Pill,
  SceneDef,
  Series,
  Stage,
  Txt,
  clamp01,
  fade,
  lerp,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { BarChart, HBarChart, KnobBar, Takeaway } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 12.44, "**Hypothesis:** If **closed-loop lockstep** (waiting for replies before sending) creates the 13 M/s wall, independent **open-loop** bidirectional traffic should blow past it.", "Experiment six tests the decisive hypothesis. If waiting for replies in lockstep creates the thirteen million wall, independent open-loop bidirectional traffic should blow right past it."], // 0
  [12.79, 25.19, "**Fixed:** **1 host core + 1 card core**, WC replies. **One knob:** **lockstep** vs **open-loop duplex** (both sides transmit independently at target rate `R`).", "Fixed baseline: one host core, one card core, and write-combining replies. One knob: closed-loop lockstep, versus open-loop duplex where both sides send independently at a target rate."], // 1
  [25.54, 41.42, "**Wall gone:** Lockstep stalls at **14.2 M/s** (`W = 256`). Open loop hits every target **5–35 M/s each way** (`70 M/s` total) at **~1 µs** median (`0.97` fwd, `1.24 µs` ret at `35 + 35`).", "The wall vanishes. Lockstep stalls at fourteen point two million. Open loop hits every target from five to thirty-five million each way, seventy million combined, at about one microsecond median: zero point nine seven forward and one point two four back."], // 2
  [41.77, 57.09, "**Peak capacity:** Unthrottled, 1 host + 1 card core reaches **41.9 + 41.9 = 83.7 M/s**. Splitting card RX & TX onto 2 cores hits **91.0 M/s** (WC) and **97.3 M/s** (`49.3 + 47.9`, DMA).", "Unthrottled, one host core and one card core hit forty-one point nine each way, eighty-three point seven total. Splitting card receive and send onto two cores hits ninety-one million with write combining, and ninety-seven point three with D M A."], // 3
  [57.44, 72.78, "**Honest caveat — tails:** Those `~1 µs` numbers are medians (`p50`). At `35 + 35`, forward **p99 spikes to 38.7 µs** (at `20 + 20`, even `p50` hits `16.9 µs`).", "Honest caveat: those one microsecond numbers are medians. At thirty-five plus thirty-five, ninety-ninth percentile forward latency spikes to thirty-eight point seven microseconds, and at twenty plus twenty even the median hits sixteen point nine."], // 4
  [73.13, 87.31, "**Echo test:** With request-triggered replies, RTT **p99 jumps** from **1.26 µs** (`10 M/s`) to **35.4 µs** (`13.5 M/s`). **Verdict:** the link is fast; lockstep & tails are our target.", "With request-triggered replies, ninety-ninth percentile round trip jumps from one point two six microseconds at ten million to thirty-five point four at thirteen point five. Verdict: the link can fly; lockstep and tails are our target."], // 5
];
const DUR = 88.71; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Phase opacities with crisp non-overlapping crossfades
  const p0 = fade(t, T[0], T[1] - 0.15, 0.4);
  const pBar = prog(t, T[1] + 0.15, 0.45);
  const p1 = fade(t, T[1] + 0.25, T[2] - 0.15, 0.4);
  const p2 = fade(t, T[2] + 0.25, T[3] - 0.15, 0.4);
  const p3 = fade(t, T[3] + 0.25, T[4] - 0.15, 0.4);
  const p4Left = prog(t, T[4] + 0.25, 0.45);
  const p4Right = fade(t, T[4] + 0.25, T[5] - 0.15, 0.4);
  const p5Echo = prog(t, T[5] + 0.2, 0.45);
  const p5Verdict = prog(t, T[5] + 0.8, 0.45);

  // Knob position across cues 1..5:
  // 0 = Lockstep, 1 = Open duplex, 2 = Unthrottled, 3 = Echo (tails)
  const knob =
    t < T[1] + 4.5
      ? 0
      : t < T[3]
        ? prog(t, T[1] + 4.5, 0.85)
        : t < T[4]
          ? 1 + prog(t, T[3], 0.8)
          : 2 + prog(t, T[4], 0.8);

  // Cue 0 lockstep ping-pong cycle (2.4s)
  const lockCycle = (rt % 2.4) / 2.4;
  const lockFwd = lockCycle < 0.42 ? lockCycle / 0.42 : -1;
  const lockRet = lockCycle >= 0.50 && lockCycle < 0.92 ? (lockCycle - 0.50) / 0.42 : -1;
  const hostBlocked = lockCycle >= 0.08 && lockCycle < 0.92;

  // Echo RTT p99 chart scales (Cue 5)
  const ex0 = 1060;
  const ex1 = 1800;
  const ey1 = 304;
  const ey0 = 640;
  const esx = linScale(0, 22, ex0, ex1);
  const esy = linScale(0, 44, ey0, ey1);

  return (
    <>
      {/* Top KnobBar (cues 1–5) */}
      {pBar > 0.01 && (
        <KnobBar
          y={118}
          size={22}
          fixed={["1 host core", "WC replies"]}
          knob="Traffic mode"
          options={["Lockstep", "Open duplex", "Unthrottled", "Echo (tails)"]}
          active={knob}
          k={pBar}
        />
      )}

      {/* ======================= CUE 0 HTML ======================= */}
      {p0 > 0.01 && (
        <>
          <Card
            x={60}
            y={180}
            w={875}
            h={660}
            border={K.slow}
            title="CLOSED-LOOP LOCKSTEP"
            titleColor={K.slow}
            style={{ opacity: p0, transform: `translateY(${(1 - p0) * 16}px)` }}
          >
            <div style={{ fontSize: 25, color: C.muted, marginTop: 4 }}>
              Host sends request, then <strong style={{ color: K.slow }}>waits for reply</strong> before issuing the next.
            </div>
            <div
              style={{
                position: "absolute",
                left: 26,
                right: 26,
                bottom: 28,
                padding: "18px 22px",
                borderRadius: 14,
                background: "rgba(249,65,68,0.10)",
                border: `2px solid ${K.slow}88`,
                fontSize: 27,
                fontWeight: 700,
                color: C.text,
              }}
            >
              Waiting for replies creates the <span style={{ color: K.slow }}>~13 M/s</span> wall?
            </div>
          </Card>

          <Card
            x={985}
            y={180}
            w={875}
            h={660}
            border={K.fast}
            title="OPEN-LOOP DUPLEX"
            titleColor={K.fast}
            style={{ opacity: p0, transform: `translateY(${(1 - p0) * 16}px)` }}
          >
            <div style={{ fontSize: 25, color: C.muted, marginTop: 4 }}>
              Host and Card transmit <strong style={{ color: K.fast }}>independently</strong> at target rate{" "}
              <span style={{ fontFamily: MONO, color: K.knob }}>R</span> — no wait barrier.
            </div>
            <div
              style={{
                position: "absolute",
                left: 26,
                right: 26,
                bottom: 28,
                padding: "18px 22px",
                borderRadius: 14,
                background: "rgba(67,197,158,0.10)",
                border: `2px solid ${K.fast}88`,
                fontSize: 27,
                fontWeight: 700,
                color: C.text,
              }}
            >
              Decisive test: <span style={{ color: K.fast }}>remove lockstep</span>.
            </div>
          </Card>
        </>
      )}

      {/* ======================= CUE 1 HTML ======================= */}
      {p1 > 0.01 && (
        <>
          <Card
            x={60}
            y={208}
            w={890}
            h={628}
            border={C.line}
            title="FIXED HARDWARE · ONE KNOB CHANGED"
            titleColor={K.fwd}
            style={{ opacity: p1 }}
          >
            <div style={{ fontSize: 25, color: C.muted, marginTop: 4 }}>
              Exact same <strong style={{ color: C.text }}>1 host core + 1 card core</strong> and{" "}
              <strong style={{ color: K.ret }}>WC replies</strong>. Only the traffic schedule changes.
            </div>
          </Card>

          <Card
            x={980}
            y={208}
            w={880}
            h={628}
            border={K.knob}
            title="EXPERIMENT 6 DESIGN"
            titleColor={K.knob}
            style={{ opacity: p1 }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 22, marginTop: 12 }}>
              <div style={{ padding: "18px 22px", borderRadius: 14, background: "rgba(255,255,255,0.03)", border: `1.5px solid ${C.line}` }}>
                <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 1.5, color: K.fixed }}>FIXED BASELINE</div>
                <div style={{ fontSize: 27, fontWeight: 600, color: C.text, marginTop: 6 }}>
                  1 host core + 1 card core · 64 B WC replies
                </div>
              </div>
              <div style={{ padding: "18px 22px", borderRadius: 14, background: "rgba(249,65,68,0.08)", border: `1.5px solid ${K.slow}77` }}>
                <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 1.5, color: K.slow }}>LOCKSTEP REFERENCE</div>
                <div style={{ fontSize: 27, fontWeight: 600, color: C.text, marginTop: 6 }}>
                  Closed-loop <span style={{ fontFamily: MONO }}>W = 256</span> stalls at{" "}
                  <strong style={{ color: K.slow }}>14.2 M/s</strong> (~13 M/s wall)
                </div>
              </div>
              <div style={{ padding: "18px 22px", borderRadius: 14, background: "rgba(67,197,158,0.08)", border: `1.5px solid ${K.fast}77` }}>
                <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 1.5, color: K.fast }}>OPEN-LOOP DUPLEX SWEEP</div>
                <div style={{ fontSize: 27, fontWeight: 600, color: C.text, marginTop: 6 }}>
                  Independent targets: <span style={{ fontFamily: MONO, color: K.fast }}>5, 10, 20, 35 M/s</span> each
                  way + unthrottled peak
                </div>
              </div>
            </div>
          </Card>
        </>
      )}

      {/* ======================= CUE 2 HTML ======================= */}
      {p2 > 0.01 && (
        <>
          <Card
            x={60}
            y={206}
            w={910}
            h={640}
            border={C.line}
            title="COMBINED BIDIRECTIONAL THROUGHPUT (M/s)"
            titleColor={C.text}
            style={{ opacity: p2 }}
          >
            <div style={{ marginTop: 4 }}>
              <span
                style={{
                  display: "inline-block",
                  fontSize: 20,
                  fontWeight: 700,
                  color: K.slow,
                  border: `2px solid ${K.slow}`,
                  background: "rgba(249,65,68,0.14)",
                  borderRadius: 999,
                  padding: "3px 14px",
                }}
              >
                — — ~13 M/s closed-loop wall
              </span>
            </div>
          </Card>

          <Card
            x={995}
            y={206}
            w={865}
            h={640}
            border={K.fast}
            title="OPEN LOOP AT 35 + 35 M/s (70 M/s COMBINED)"
            titleColor={K.fast}
            style={{ opacity: p2 }}
          >
            {/* Big ~1 us median badge */}
            <div
              style={{
                marginTop: 14,
                padding: "24px 28px",
                borderRadius: 16,
                background: "rgba(67,197,158,0.12)",
                border: `2.5px solid ${K.fast}`,
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 21, fontWeight: 800, letterSpacing: 2, color: K.fast }}>
                MEDIAN ONE-WAY LATENCY (p50)
              </div>
              <div style={{ fontSize: 72, fontWeight: 800, color: K.fast, lineHeight: 1.1, marginTop: 6 }}>
                ~1 µs
              </div>
              <div style={{ fontSize: 24, color: C.text, marginTop: 6 }}>
                Every target 5–35 M/s each way met cleanly!
              </div>
            </div>

            {/* Forward & Return p50 pills */}
            <div style={{ display: "flex", gap: 20, marginTop: 26 }}>
              <div
                style={{
                  flex: 1,
                  padding: "20px 22px",
                  borderRadius: 14,
                  background: "rgba(76,201,240,0.10)",
                  border: `2px solid ${K.fwd}`,
                  textAlign: "center",
                }}
              >
                <div style={{ fontSize: 21, fontWeight: 800, letterSpacing: 1.4, color: K.fwd }}>FORWARD p50</div>
                <div style={{ fontSize: 52, fontWeight: 800, color: K.fwd, marginTop: 6, fontVariantNumeric: "tabular-nums" }}>
                  0.97 µs
                </div>
                <div style={{ fontSize: 21, color: C.muted, marginTop: 2 }}>Host → Card (35 M/s)</div>
              </div>
              <div
                style={{
                  flex: 1,
                  padding: "20px 22px",
                  borderRadius: 14,
                  background: "rgba(67,197,158,0.10)",
                  border: `2px solid ${K.ret}`,
                  textAlign: "center",
                }}
              >
                <div style={{ fontSize: 21, fontWeight: 800, letterSpacing: 1.4, color: K.ret }}>RETURN p50</div>
                <div style={{ fontSize: 52, fontWeight: 800, color: K.ret, marginTop: 6, fontVariantNumeric: "tabular-nums" }}>
                  1.24 µs
                </div>
                <div style={{ fontSize: 21, color: C.muted, marginTop: 2 }}>Card → Host (35 M/s)</div>
              </div>
            </div>

            <div
              style={{
                marginTop: 26,
                textAlign: "center",
                fontSize: 25,
                fontWeight: 700,
                color: K.fast,
              }}
            >
              Wall vanished: 70 M/s combined vs 14.2 M/s lockstep!
            </div>
          </Card>
        </>
      )}

      {/* ======================= CUE 3 HTML ======================= */}
      {p3 > 0.01 && (
        <Card
          x={60}
          y={206}
          w={1800}
          h={644}
          border={K.fast}
          title="PEAK UNTHROTTLED BIDIRECTIONAL CAPACITY (1 HOST CORE)"
          titleColor={K.fast}
          style={{ opacity: p3 }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 2 }}>
            <div style={{ fontSize: 23, color: C.muted }}>
              Combined rate (<strong style={{ color: C.text }}>M/s</strong>). Splitting card RX &amp; TX onto{" "}
              <strong style={{ color: K.knob }}>2 card cores</strong> hits{" "}
              <strong style={{ color: K.fast }}>91.0 M/s</strong> (WC) and{" "}
              <strong style={{ color: K.knob }}>97.3 M/s</strong> (DMA).
            </div>
            <span
              style={{
                fontSize: 20,
                fontWeight: 700,
                color: K.slow,
                border: `2px solid ${K.slow}`,
                background: "rgba(249,65,68,0.14)",
                borderRadius: 999,
                padding: "3px 14px",
                whiteSpace: "nowrap",
              }}
            >
              — — 14.2 M/s lockstep wall
            </span>
          </div>
        </Card>
      )}

      {/* ======================= CUES 4–5 HTML ======================= */}
      {p4Left > 0.01 && (
        <>
          <Card
            x={60}
            y={200}
            w={875}
            h={558}
            border={K.slow}
            title="HONEST CAVEAT · 35 + 35 M/s FORWARD LATENCY"
            titleColor={K.slow}
            style={{ opacity: p4Left }}
          >
            <div
              style={{
                position: "absolute",
                left: 24,
                right: 24,
                bottom: 20,
                padding: "14px 18px",
                borderRadius: 12,
                background: "rgba(249,65,68,0.09)",
                border: `1.5px solid ${K.slow}77`,
                fontSize: 22,
                color: C.text,
                lineHeight: 1.4,
              }}
            >
              <div>
                Return <strong style={{ color: K.knob }}>p99 = 13.9 µs</strong> · At{" "}
                <span style={{ fontFamily: MONO }}>20 + 20 M/s</span>, even forward{" "}
                <strong style={{ color: K.slow }}>p50 = 16.9 µs</strong>.
              </div>
            </div>
          </Card>
        </>
      )}

      {/* Cue 4 Right framing card (before Cue 5 Echo cliff) */}
      {p4Right > 0.01 && (
        <Card
          x={965}
          y={200}
          w={895}
          h={558}
          border={K.knob}
          title="WHY MEDIANS ALONE AREN'T ENOUGH"
          titleColor={K.knob}
          style={{ opacity: p4Right }}
        >
          <div style={{ fontSize: 32, fontWeight: 700, color: C.text, lineHeight: 1.35, marginTop: 14 }}>
            <span style={{ color: K.fast }}>~1 µs</span> is only the <span style={{ fontFamily: MONO }}>p50</span> median.
          </div>
          <div style={{ fontSize: 26, color: C.muted, lineHeight: 1.48, marginTop: 22 }}>
            At <strong style={{ color: C.text }}>35 + 35 M/s</strong>, the 99th-percentile forward tail spikes{" "}
            to <strong style={{ color: K.slow }}>38.7 µs</strong> (with{" "}
            <span style={{ fontFamily: MONO, color: K.knob }}>p90 = 5.55 µs</span>).
          </div>
          <div
            style={{
              marginTop: 34,
              padding: "20px 24px",
              borderRadius: 14,
              background: "rgba(249,199,79,0.09)",
              border: `2px solid ${K.knob}77`,
              fontSize: 25,
              color: C.text,
              lineHeight: 1.45,
            }}
          >
            <strong style={{ color: K.knob }}>Next check:</strong> What happens when replies are triggered by actual
            requests (open-loop Echo)?
          </div>
        </Card>
      )}

      {/* Cue 5 Right Echo RTT p99 cliff card frame */}
      {p5Echo > 0.01 && (
        <Card
          x={965}
          y={200}
          w={895}
          h={558}
          border={K.slow}
          title="OPEN-LOOP ECHO TEST · RTT p99 vs RATE"
          titleColor={K.slow}
          style={{ opacity: p5Echo }}
        >
          <div style={{ fontSize: 22, color: C.muted, marginTop: -2 }}>
            Request-triggered replies: sharp <strong style={{ color: K.slow }}>p99 cliff</strong> between{" "}
            <span style={{ fontFamily: MONO, color: K.fast }}>10</span> and{" "}
            <span style={{ fontFamily: MONO, color: K.slow }}>13.5 M/s</span>.
          </div>
        </Card>
      )}

      {/* Cue 5 Verdict banner */}
      {p5Verdict > 0.01 && (
        <Takeaway x={60} y={774} w={1800} k={p5Verdict} color={K.fast} label="VERDICT" size={27}>
          The PCIe path can fly (<strong style={{ color: K.fast }}>83.7–97.3 M/s</strong>); closed-loop lockstep &amp;
          the <strong style={{ color: K.slow }}>35–39 µs</strong> tail are our real target.
        </Takeaway>
      )}

      {/* ======================= SVG STAGE ======================= */}
      <Stage>
        {/* ---- CUE 0 Diagrams: Lockstep vs Open-loop Duplex ---- */}
        {p0 > 0.01 && (
          <g opacity={p0} fontFamily={FONT}>
            {/* Left: Closed-loop lockstep */}
            <rect x={115} y={365} width={175} height={190} rx={14} fill={`${K.host}22`} stroke={K.host} strokeWidth={3} />
            <text x={202} y={450} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text}>
              Host
            </text>
            <text x={202} y={486} textAnchor="middle" fontSize={22} fill={C.muted}>
              1 core
            </text>

            <rect x={705} y={365} width={175} height={190} rx={14} fill={`${K.card}22`} stroke={K.card} strokeWidth={3} />
            <text x={792} y={450} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text}>
              Card
            </text>
            <text x={792} y={486} textAnchor="middle" fontSize={22} fill={C.muted}>
              1 core
            </text>

            {/* Forward & Return lanes */}
            <Arrow x1={296} y1={415} x2={696} y2={415} color={K.fwd} w={4} head={14} opacity={0.65} />
            <text x={496} y={396} textAnchor="middle" fontSize={21} fontWeight={700} fill={K.fwd}>
              1. Send request →
            </text>
            <Arrow x1={696} y1={515} x2={296} y2={515} color={K.ret} w={4} head={14} opacity={0.65} />
            <text x={496} y={552} textAnchor="middle" fontSize={21} fontWeight={700} fill={K.ret}>
              ← 2. Wait for reply
            </text>

            {lockFwd >= 0 && <Packet pts={[[296, 415], [696, 415]]} k={lockFwd} color={K.fwd} r={11} />}
            {lockRet >= 0 && <Packet pts={[[696, 515], [296, 515]]} k={lockRet} color={K.ret} r={11} />}

            {/* Wait barrier dashed line */}
            <line
              x1={322}
              y1={352}
              x2={322}
              y2={582}
              stroke={K.slow}
              strokeWidth={4}
              strokeDasharray="10 8"
              opacity={hostBlocked ? 0.95 : 0.3}
            />
            <rect
              x={338}
              y={590}
              width={325}
              height={46}
              rx={10}
              fill="rgba(249,65,68,0.16)"
              stroke={K.slow}
              strokeWidth={2}
            />
            <text x={500} y={621} textAnchor="middle" fontSize={22} fontWeight={800} fill={K.slow}>
              LOCKSTEP WAIT BARRIER
            </text>

            {/* Right: Open-loop duplex */}
            <rect x={1040} y={365} width={175} height={190} rx={14} fill={`${K.host}22`} stroke={K.host} strokeWidth={3} />
            <text x={1127} y={450} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text}>
              Host
            </text>
            <text x={1127} y={486} textAnchor="middle" fontSize={22} fill={C.muted}>
              TX + RX
            </text>

            <rect x={1630} y={365} width={175} height={190} rx={14} fill={`${K.card}22`} stroke={K.card} strokeWidth={3} />
            <text x={1717} y={450} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text}>
              Card
            </text>
            <text x={1717} y={486} textAnchor="middle" fontSize={22} fill={C.muted}>
              TX + RX
            </text>

            {/* Continuous counter-flowing streams */}
            <Arrow x1={1222} y1={415} x2={1622} y2={415} color={K.fwd} w={4} head={14} opacity={0.55} />
            <text x={1422} y={394} textAnchor="middle" fontSize={21} fontWeight={700} fill={K.fwd}>
              Continuous stream at rate R →
            </text>
            <PacketStream pts={[[1225, 415], [1615, 415]]} travel={rt * 190} gap={54} size={13} color={K.fwd} />

            <Arrow x1={1622} y1={515} x2={1222} y2={515} color={K.ret} w={4} head={14} opacity={0.55} />
            <text x={1422} y={554} textAnchor="middle" fontSize={21} fontWeight={700} fill={K.ret}>
              ← Independent stream at rate R
            </text>
            <PacketStream pts={[[1615, 515], [1225, 515]]} travel={rt * 190} gap={54} size={13} color={K.ret} />

            <rect
              x={1248}
              y={590}
              width={350}
              height={46}
              rx={10}
              fill="rgba(67,197,158,0.16)"
              stroke={K.fast}
              strokeWidth={2}
            />
            <text x={1423} y={621} textAnchor="middle" fontSize={22} fontWeight={800} fill={K.fast}>
              NO LOCKSTEP · FULL DUPLEX
            </text>
          </g>
        )}

        {/* ---- CUE 1 Left Live Setup Diagram ---- */}
        {p1 > 0.01 && (
          <g opacity={p1} fontFamily={FONT}>
            <rect x={115} y={405} width={185} height={210} rx={16} fill={`${K.host}22`} stroke={K.host} strokeWidth={3} />
            <text x={207} y={495} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text}>
              1 Host Core
            </text>
            <text x={207} y={534} textAnchor="middle" fontSize={21} fill={C.muted}>
              MOVDIR64B TX
            </text>
            <text x={207} y={564} textAnchor="middle" fontSize={21} fill={C.muted}>
              + reply RX
            </text>

            <rect x={710} y={405} width={185} height={210} rx={16} fill={`${K.card}22`} stroke={K.card} strokeWidth={3} />
            <text x={802} y={495} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text}>
              1 Card Core
            </text>
            <text x={802} y={534} textAnchor="middle" fontSize={21} fill={C.muted}>
              64 B WC TX
            </text>
            <text x={802} y={564} textAnchor="middle" fontSize={21} fill={C.muted}>
              + req RX
            </text>

            <Arrow x1={308} y1={460} x2={702} y2={460} color={K.fwd} w={4} head={14} opacity={0.6} />
            <text x={505} y={436} textAnchor="middle" fontSize={22} fontWeight={700} fill={K.fwd}>
              Forward (Host → Card)
            </text>
            <Arrow x1={702} y1={560} x2={308} y2={560} color={K.ret} w={4} head={14} opacity={0.6} />
            <text x={505} y={602} textAnchor="middle" fontSize={22} fontWeight={700} fill={K.ret}>
              Return (Card → Host WC)
            </text>

            {/* Show mode switch visually as knob slides 0 -> 1 */}
            {knob < 0.5 ? (
              <>
                {lockFwd >= 0 && <Packet pts={[[308, 460], [702, 460]]} k={lockFwd} color={K.fwd} r={11} />}
                {lockRet >= 0 && <Packet pts={[[702, 560], [308, 560]]} k={lockRet} color={K.ret} r={11} />}
                <rect x={315} y={690} width={380} height={54} rx={12} fill="rgba(249,65,68,0.14)" stroke={K.slow} strokeWidth={2} />
                <text x={505} y={725} textAnchor="middle" fontSize={23} fontWeight={800} fill={K.slow}>
                  Mode: Closed-loop Lockstep
                </text>
              </>
            ) : (
              <>
                <PacketStream pts={[[312, 460], [696, 460]]} travel={rt * 210} gap={52} size={13} color={K.fwd} />
                <PacketStream pts={[[696, 560], [312, 560]]} travel={rt * 210} gap={52} size={13} color={K.ret} />
                <rect x={315} y={690} width={380} height={54} rx={12} fill="rgba(67,197,158,0.14)" stroke={K.fast} strokeWidth={2} />
                <text x={505} y={725} textAnchor="middle" fontSize={23} fontWeight={800} fill={K.fast}>
                  Mode: Open-loop Duplex (rate R)
                </text>
              </>
            )}
          </g>
        )}

        {/* ---- CUE 2 Left BarChart: Lockstep vs Open-loop 5..35 each way ---- */}
        {p2 > 0.01 && (
          <g opacity={p2} fontFamily={FONT}>
            {/* Dashed ~13 M/s wall line */}
            <line
              x1={92}
              y1={730 - (410 * 13) / 80}
              x2={942}
              y2={730 - (410 * 13) / 80}
              stroke={K.slow}
              strokeWidth={2.5}
              strokeDasharray="10 7"
            />

            <BarChart
              x0={92}
              x1={942}
              y1={320}
              y0={730}
              max={80}
              dec={1}
              size={25}
              labelSize={22}
              barW={106}
              bars={[
                { label: "Lockstep", sub: "W = 256", value: 14.2, color: K.slow, text: "14.2" },
                { label: "5 + 5", sub: "open", value: 10, color: K.fwd, text: "10" },
                { label: "10 + 10", sub: "open", value: 20, color: K.fwd, text: "20" },
                { label: "20 + 20", sub: "open", value: 40, color: K.fwd, text: "40" },
                { label: "35 + 35", sub: "open", value: 70, color: K.fast, text: "70 M/s" },
              ]}
              k={(i) => prog(t, T[2] + 0.5 + i * 0.45, 0.7)}
            />
          </g>
        )}

        {/* ---- CUE 3 Peak Capacity Horizontal BarChart ---- */}
        {p3 > 0.01 && (
          <g opacity={p3} fontFamily={FONT}>
            {/* Subtle vertical dashed lockstep reference line at 14.2 M/s (legend cleanly in Card header) */}
            <line
              x1={465 + ((1570 - 465) * 14.2) / 105}
              y1={322}
              x2={465 + ((1570 - 465) * 14.2) / 105}
              y2={816}
              stroke={K.slow}
              strokeWidth={2.5}
              strokeDasharray="8 6"
            />
            <HBarChart
              x0={465}
              x1={1570}
              y1={332}
              y0={812}
              max={105}
              dec={1}
              unit=" M/s"
              size={28}
              labelSize={24}
              barH={58}
              bars={[
                { label: "Lockstep (1 card core)", sub: "W = 256", value: 14.2, color: K.slow, text: "14.2 M/s" },
                { label: "Open duplex (1 card core)", sub: "35 + 35 M/s", value: 70.0, color: K.fwd, text: "70.0 M/s" },
                { label: "Unthrottled (1 card core)", sub: "41.9 + 41.9", value: 83.7, color: K.fast, text: "83.7 M/s" },
                { label: "2 card cores split (WC)", sub: "45.5 + 45.5", value: 91.0, color: K.fast, text: "91.0 M/s" },
                { label: "2 card cores split (DMA)", sub: "49.3 + 47.9", value: 97.3, color: K.knob, text: "97.3 M/s" },
              ]}
              k={(i) => prog(t, T[3] + 0.45 + i * 0.65, 0.75)}
            />
          </g>
        )}

        {/* ---- CUES 4–5 Left BarChart: p50 / p90 / p99 at 35+35 ---- */}
        {p4Left > 0.01 && (
          <g opacity={p4Left} fontFamily={FONT}>
            <BarChart
              x0={115}
              x1={880}
              y1={305}
              y0={565}
              max={45}
              dec={2}
              size={28}
              labelSize={24}
              barW={138}
              bars={[
                { label: "p50", sub: "median", value: 0.97, color: K.fast, text: "0.97 µs" },
                { label: "p90", sub: "90th pct", value: 5.55, color: K.knob, text: "5.55 µs" },
                { label: "p99", sub: "tail spike!", value: 38.7, color: K.slow, text: "38.7 µs" },
              ]}
              k={(i) => prog(t, T[4] + 0.55 + i * 0.55, 0.7)}
            />
          </g>
        )}

        {/* ---- CUE 5 Right Echo RTT p99 Cliff Chart ---- */}
        {p5Echo > 0.01 && (
          <g opacity={p5Echo} fontFamily={FONT}>
            {/* Cliff zone highlight between 10 and 13.5 M/s */}
            <rect
              x={esx(10)}
              y={ey1}
              width={esx(13.5) - esx(10)}
              height={ey0 - ey1}
              fill="rgba(249,65,68,0.11)"
              opacity={prog(t, T[5] + 1.0, 0.5)}
            />
            <text
              x={(esx(10) + esx(13.5)) / 2}
              y={ey1 + 26}
              textAnchor="middle"
              fontSize={21}
              fontWeight={800}
              fill={K.slow}
              opacity={prog(t, T[5] + 1.1, 0.5)}
            >
              CLIFF
            </text>

            <Axes
              x0={ex0}
              x1={ex1}
              y0={ey0}
              y1={ey1}
              sx={esx}
              sy={esy}
              xticks={[
                [5, "5"],
                [10, "10"],
                [13.5, "13.5"],
                [20, "20"],
              ]}
              yticks={[
                [0, "0"],
                [10, "10"],
                [20, "20"],
                [30, "30"],
                [40, "40 µs"],
              ]}
              xlabel="Offer rate (M/s)"
              size={22}
            />

            {/* Flat green segment: (5, 1.03) -> (10, 1.26) */}
            <Series
              pts={[
                [5, 1.03],
                [10, 1.26],
              ]}
              sx={esx}
              sy={esy}
              k={prog(t, T[5] + 0.45, 0.7)}
              color={K.fast}
              w={6}
              r={8}
            />

            {/* Red cliff segment: (10, 1.26) -> (13.5, 35.4) -> (20, 38.5) */}
            <Series
              pts={[
                [10, 1.26],
                [13.5, 35.4],
                [20, 38.5],
              ]}
              sx={esx}
              sy={esy}
              k={prog(t, T[5] + 0.95, 0.95)}
              color={K.slow}
              w={6}
              r={8}
            />

            {/* Point labels (carefully spaced for zero overlap) */}
            <g opacity={prog(t, T[5] + 0.85, 0.4)} fontWeight={800} fontSize={23}>
              <text x={esx(5)} y={esy(1.03) - 18} textAnchor="middle" fill={K.fast}>
                1.03 µs
              </text>
              <text x={esx(10) - 8} y={esy(1.26) - 18} textAnchor="end" fill={K.fast}>
                1.26 µs
              </text>
            </g>
            <g opacity={prog(t, T[5] + 1.5, 0.4)} fontWeight={800} fontSize={24}>
              <text x={esx(13.5) + 12} y={esy(35.4) + 26} textAnchor="start" fill={K.slow}>
                35.4 µs
              </text>
              <text x={esx(20) - 8} y={esy(38.5) - 18} textAnchor="end" fill={K.slow}>
                38.5 µs
              </text>
            </g>
          </g>
        )}
      </Stage>
    </>
  );
}

export const S08: SceneDef = {
  id: "S08",
  title: "Exp 6 · Breaking the loop (open-loop)",
  stage: 4,
  source: "Lockstep vs open-loop duplex · 1 host core · 483 verified runs",
  dur: DUR,
  cues,
  C: Body,
};
