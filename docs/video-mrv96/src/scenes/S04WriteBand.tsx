import React from "react";
import { C, Cue, FONT, K, MONO, SceneDef, Stage, clamp01, fade, lerp, prog, useRealT, useT } from "../lib";
import { HBarChart, InfPill, InfTag, KnobBar, NextQuestion, RingStrip } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 9.92, "**Two suspects:** Did the 3 µs pause unlock 161.6 M/s by cutting **card L3 read bandwidth**, or by staying out of the **in-flight write band**?", "Experiment two splits those two suspects: total L 3 read bandwidth on the card, versus colliding with the exact addresses still being written."], // 0
  [10.27, 23.53, "**Fixed:** the fast **3 µs pause** baseline (2 card cores). **One knob:** **where** the core reads during each pause — nothing, cold RAM, old ring slots, or the write band.", "Fixed baseline: the fast three microsecond pause from experiment one. One knob: where the core reads during each pause — nothing, cold memory, old ring slots, or ahead inside the active write band."], // 1
  [23.88, 37.72, "Idle spin gives **160.4 M/s** (`608 M/s` L3). Cold RAM pushes L3 reads to **815–1,033 M/s** (> 1 billion/s), yet forward stays **160.4–161.4 M/s**.", "Reading nothing gives one hundred sixty point four million at six hundred eight million L 3 requests per second. Cold memory pushes L 3 reads past one billion per second, yet forward stays at one hundred sixty-one million."], // 2
  [38.07, 51.89, "Old ring slots half a lap back (**162.0–162.6**) and slots just behind (**160.2**) stay full speed. **Only reading ahead inside the write band** crashes to **113.4 M/s**.", "Old ring slots half a lap back stay at one hundred sixty-two million, and slots just behind stay at one hundred sixty point two. Only reading ahead inside the active write band crashes throughput to one hundred thirteen point four."], // 3
  [52.24, 66.60, "**Why:** L3 bandwidth is innocent (write band uses only `472 M/s` L3). **[INF]** Touching landing lines spikes snoops (`67.9 → 111.9 M`) and pushes host queueing to **4.03 µs > 3 µs**.", "Verdict: location, not bandwidth. Touching lines still landing spikes snoops and pushes host queuing to four microseconds, longer than the pause. Our reading: snoops or write blocking feed back so pauses end too early."], // 4
  [66.95, 78.17, "**Next:** One-way is solved: stay behind the ~3 µs band to hit **161.6 M/s**. Now step inside the **13 M/s closed loop**: where do the extra microseconds sit?", "One-way is solved: stay behind the three microsecond band and hit one hundred sixty-one point six million. Now step inside the thirteen million closed loop. Where do the extra microseconds sit?"], // 5
];
const DUR = 79.57; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Cue 0: Two competing suspects
  const kSuspects = fade(t, T[0], T[1], 0.45);
  const kSuspA = prog(t, T[0] + 0.3, 0.55);
  const kSuspB = prog(t, T[0] + 2.0, 0.55);

  // Cues 1–5: Main layout
  const kMain = prog(t, T[1] + 0.25, 0.55);

  // KnobBar sliding: -1 in cue 1 -> 0 (Idle spin) at T[2] -> 1 (Cold RAM) at T[2]+4.5 -> 2 (Old ring) at T[3] -> 3 (Write band) at T[3]+6.5
  const kSel0 = prog(t, T[2], 0.45);
  const kSel1 = prog(t, T[2] + 4.4, 0.65);
  const kSel2 = prog(t, T[3], 0.65);
  const kSel3 = prog(t, T[3] + 6.4, 0.65);
  const activeKnob = kSel0 < 0.01 ? -1 : lerp(0, 1, kSel1) + lerp(0, 1, kSel2) + lerp(0, 1, kSel3);

  // Row reveals (synchronized on both HBarCharts)
  const kRow0 = prog(t, T[2] + 0.4, 0.75); // Idle spin
  const kRow1 = prog(t, T[2] + 4.6, 0.75); // Cold RAM 64B
  const kRow2 = prog(t, T[2] + 6.4, 0.75); // Cold RAM page stride (>1B/s)
  const kRow3 = prog(t, T[3] + 0.4, 0.75); // Old ring
  const kRow4 = prog(t, T[3] + 2.8, 0.75); // Just behind
  const kRow5 = prog(t, T[3] + 6.6, 0.8);  // Ahead (write band) — CRASH!

  const kVerdict = fade(t, T[4] + 0.35, T[5], 0.4);
  const kNextQ = prog(t, T[5] + 0.35, 0.45);

  // Top-right box switches from Cold RAM (cues 1–3) to Feedback Mechanism (cues 4–5)
  const kColdBox = fade(t, T[1] + 0.25, T[4], 0.4);
  const kFeedbackBox = prog(t, T[4] + 0.35, 0.5);

  // Active highlight weights for probe targets
  const coldHot = clamp01(kSel1 * (1 - kSel2));
  const oldHot = clamp01(kSel2 * (1 - kSel3));
  const bandHot = kSel3;
  const pulse = 0.5 + 0.5 * Math.sin(rt * 7);

  // Ring geometry (y = 256..310 inside top panel y = 192..386)
  const RX = 88;
  const RY = 256;
  const RCW = 44;
  const RCH = 54;
  const RGAP = 6;
  const RN = 24;

  const cellState = (i: number) => {
    if (i < 9) return "consumed" as const;
    if (i < 15) return "landed" as const;
    if (i < 22) return "inflight" as const;
    return "empty" as const;
  };

  // Probes under ring strip (zero overlap: slots 1..5, 10..14, 15..21)
  const probes = [
    {
      from: 1,
      to: 5,
      color: K.fwd,
      label: "Old ring (behind)",
      k: Math.max(0.28 * kMain, oldHot > 0.05 ? 1 : kRow3 * 0.65),
    },
    {
      from: 10,
      to: 14,
      color: K.fast,
      label: "Just behind",
      k: Math.max(0.28 * kMain, kRow4 > 0.05 ? (bandHot > 0.5 ? 0.55 : 1) : 0),
    },
    {
      from: 15,
      to: 22,
      color: K.slow,
      label: "Write band (ahead)",
      k: Math.max(0.28 * kMain, bandHot),
    },
  ];

  const rowKs = [kRow0, kRow1, kRow2, kRow3, kRow4, kRow5];

  return (
    <>
      {/* ================= CUE 0: TWO COMPETING SUSPECTS (HTML) ================= */}
      {kSuspects > 0.02 && (
        <div style={{ position: "absolute", inset: 0, opacity: kSuspects, fontFamily: FONT }}>
          <div
            style={{
              position: "absolute",
              left: 60,
              top: 138,
              width: 1800,
              textAlign: "center",
              fontSize: 30,
              fontWeight: 700,
              color: C.text,
            }}
          >
            Why did the <span style={{ color: K.fast }}>3 µs pause</span> unlock{" "}
            <span style={{ color: K.fast }}>161.6 M/s</span>? Two competing suspects:
          </div>

          {/* Suspect A: Card L3 Bandwidth */}
          <div
            style={{
              position: "absolute",
              left: 88,
              top: 224,
              width: 780,
              height: 416,
              opacity: kSuspA,
              transform: `translateY(${(1 - kSuspA) * 20}px)`,
              background: C.panel,
              border: `2.5px solid ${K.wait}`,
              borderRadius: 20,
              padding: "28px 34px",
              boxSizing: "border-box",
            }}
          >
            <span
              style={{
                display: "inline-block",
                fontSize: 20,
                fontWeight: 800,
                letterSpacing: 1.8,
                color: C.bg,
                background: K.wait,
                borderRadius: 999,
                padding: "5px 16px",
              }}
            >
              SUSPECT A · BANDWIDTH
            </span>
            <div style={{ fontSize: 36, fontWeight: 800, color: K.wait, marginTop: 18 }}>
              Card L3 Read Bandwidth
            </div>
            <div style={{ fontSize: 25, color: C.text, lineHeight: 1.45, marginTop: 16 }}>
              Did non-stop polling overload shared card L3 cache read bandwidth?
            </div>
            <div
              style={{
                marginTop: 28,
                padding: "16px 20px",
                borderRadius: 12,
                background: "rgba(179,136,255,0.10)",
                border: `1.5px solid ${K.wait}66`,
                fontSize: 23,
                color: C.text,
                lineHeight: 1.4,
              }}
            >
              <strong style={{ color: K.wait }}>If true:</strong> pushing heavy L3 reads during the 3 µs pause will slow forward throughput.
            </div>
          </div>

          {/* VS Badge */}
          <div
            style={{
              position: "absolute",
              left: 916,
              top: 388,
              width: 88,
              height: 88,
              borderRadius: 999,
              background: C.panel,
              border: `3px solid ${K.knob}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 30,
              fontWeight: 900,
              color: K.knob,
              boxShadow: `0 0 24px ${K.knob}44`,
            }}
          >
            VS
          </div>

          {/* Suspect B: Write-Band Location */}
          <div
            style={{
              position: "absolute",
              left: 1052,
              top: 224,
              width: 780,
              height: 416,
              opacity: kSuspB,
              transform: `translateY(${(1 - kSuspB) * 20}px)`,
              background: C.panel,
              border: `2.5px solid ${K.slow}`,
              borderRadius: 20,
              padding: "28px 34px",
              boxSizing: "border-box",
            }}
          >
            <span
              style={{
                display: "inline-block",
                fontSize: 20,
                fontWeight: 800,
                letterSpacing: 1.8,
                color: C.bg,
                background: K.slow,
                borderRadius: 999,
                padding: "5px 16px",
              }}
            >
              SUSPECT B · LOCATION
            </span>
            <div style={{ fontSize: 36, fontWeight: 800, color: K.slow, marginTop: 18 }}>
              In-Flight Write Band
            </div>
            <div style={{ fontSize: 25, color: C.text, lineHeight: 1.45, marginTop: 16 }}>
              Or did polling collide with the exact addresses still being written by the host?
            </div>
            <div
              style={{
                marginTop: 28,
                padding: "16px 20px",
                borderRadius: 12,
                background: "rgba(249,65,68,0.10)",
                border: `1.5px solid ${K.slow}66`,
                fontSize: 23,
                color: C.text,
                lineHeight: 1.4,
              }}
            >
              <strong style={{ color: K.slow }}>If true:</strong> only reading inside the active write band will crash forward throughput.
            </div>
          </div>

          {/* Bottom cue-0 test summary */}
          <div
            style={{
              position: "absolute",
              left: 60,
              top: 700,
              width: 1800,
              textAlign: "center",
              fontSize: 26,
              color: C.muted,
            }}
          >
            Keep the <strong style={{ color: C.text }}>3 µs pause</strong> fixed — vary only{" "}
            <strong style={{ color: K.knob }}>where the card core reads</strong> during each pause.
          </div>
        </div>
      )}

      {/* ================= CUES 1–5: TOP KNOB BAR ================= */}
      <KnobBar
        y={118}
        size={22}
        k={kMain}
        fixed={["One-way", "3 µs pause", "2 card cores"]}
        knob="Read target"
        options={["Idle spin", "Cold RAM", "Old ring", "Write band"]}
        active={activeKnob}
      />

      {/* ================= FULL-FRAME SVG STAGE (CUES 1–5) ================= */}
      <Stage opacity={kMain}>
        {/* TOP PANEL: RING MAP + COLD RAM / FEEDBACK BOX (y = 192..386) */}
        <rect x={60} y={192} width={1800} height={194} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />

        {/* Left title well left of next cursor (next is at x = 585) */}
        <text x={88} y={222} fontSize={22} fontWeight={700} fill={C.muted}>
          Request ring (during 3 µs pause)
        </text>

        {/* Active write band header above slots 15..21 (leaves underside free for probes!) */}
        <text x={1012} y={232} textAnchor="middle" fontSize={21} fontWeight={700} fill={K.inflight}>
          active write band (in-flight)
        </text>

        {/* RingStrip */}
        <RingStrip
          x={RX}
          y={RY}
          n={RN}
          cw={RCW}
          ch={RCH}
          gap={RGAP}
          state={cellState}
          flicker={rt}
          nextAt={9.5}
          nextLabel="next"
          nextColor={K.fast}
          band={{ from: 15, to: 22, color: K.inflight }}
          probes={probes}
          size={21}
        />

        {/* Pulsing red ring alert when write-band probe slams in */}
        {bandHot > 0.2 && (
          <rect
            x={RX + 15 * (RCW + RGAP) - RGAP}
            y={RY - 10}
            width={7 * (RCW + RGAP) + RGAP}
            height={RCH + 20}
            rx={12}
            fill="none"
            stroke={K.slow}
            strokeWidth={3.5}
            opacity={bandHot * (0.45 + 0.55 * pulse)}
          />
        )}

        {/* Vertical divider before right box */}
        <line x1={1314} y1={212} x2={1314} y2={366} stroke={C.line} strokeWidth={2} strokeDasharray="6 6" />

        {/* TOP-RIGHT BOX (Cues 1–3): Cold RAM (outside ring) */}
        {kColdBox > 0.02 && (
          <g opacity={kColdBox}>
            <rect
              x={1340}
              y={210}
              width={494}
              height={158}
              rx={14}
              fill={coldHot > 0.3 ? "rgba(249,199,79,0.12)" : "#101830"}
              stroke={coldHot > 0.3 ? K.knob : C.line}
              strokeWidth={coldHot > 0.3 ? 3 : 2}
            />
            <text x={1366} y={248} fontSize={24} fontWeight={800} fill={coldHot > 0.3 ? K.knob : C.text}>
              Cold RAM (outside ring)
            </text>
            <text x={1366} y={284} fontSize={21} fill={C.muted}>
              Separate buffer · never written by host
            </text>
            <rect x={1366} y={304} width={442} height={44} rx={8} fill="#0b1020" stroke={coldHot > 0.3 ? K.knob : C.line} strokeWidth={1.5} />
            <text
              x={1587}
              y={333}
              textAnchor="middle"
              fontSize={20}
              fontWeight={700}
              fill={coldHot > 0.3 ? K.knob : C.muted}
              fontFamily={MONO}
            >
              {coldHot > 0.3 ? "815–1,033 M/s (> 1 billion/s)" : "64 B stride & page stride probes"}
            </text>
          </g>
        )}

        {/* TOP-RIGHT BOX (Cues 4–5): Feedback Mechanism [INF] */}
        {kFeedbackBox > 0.02 && (
          <g opacity={kFeedbackBox}>
            <rect
              x={1340}
              y={208}
              width={494}
              height={162}
              rx={14}
              fill="rgba(249,65,68,0.10)"
              stroke={K.slow}
              strokeWidth={2.5}
            />
            <InfTag x={1362} y={222} size={19} />
            <text x={1438} y={242} fontSize={22} fontWeight={800} fill={K.inf}>
              Write-Band Feedback Loop
            </text>
            <text x={1362} y={284} fontSize={23} fontWeight={700} fill={C.text}>
              Snoops spike:{" "}
              <tspan fill={K.slow} fontFamily={MONO}>67.9 M → 111.9 M</tspan>
            </text>
            <text x={1362} y={318} fontSize={23} fontWeight={700} fill={C.text}>
              Host write queue:{" "}
              <tspan fill={K.slow} fontFamily={MONO}>4.03 µs &gt; 3 µs</tspan>
            </text>
            <text x={1362} y={352} fontSize={20} fill={C.muted}>
              Pause ends while writes are still landing
            </text>
          </g>
        )}

        {/* ================= BOTTOM PANEL: DUAL ALIGNED HBarCharts (y = 402..788) ================= */}
        <rect x={60} y={402} width={1800} height={386} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />

        {/* Left chart title */}
        <text x={335} y={436} fontSize={24} fontWeight={800} fill={K.fwd}>
          Forward Throughput (M/s)
        </text>

        {/* Right chart title + >1 billion/s callout pill */}
        <text x={1095} y={436} fontSize={24} fontWeight={800} fill={K.knob}>
          Card L3 Requests (M/s)
        </text>
        {kRow2 > 0.4 && (
          <g opacity={clamp01((kRow2 - 0.4) / 0.6)}>
            <rect x={1520} y={412} width={312} height={34} rx={8} fill="rgba(249,199,79,0.16)" stroke={K.knob} strokeWidth={2} />
            <text x={1676} y={435} textAnchor="middle" fontSize={20} fontWeight={800} fill={K.knob}>
              Cold RAM: &gt; 1 billion/s!
            </text>
          </g>
        )}

        {/* Red highlight band behind Row 6 when write-band crash happens */}
        {kRow5 > 0.1 && (
          <rect
            x={76}
            y={724}
            width={1768}
            height={52}
            rx={10}
            fill="rgba(249,65,68,0.14)"
            stroke={K.slow}
            strokeWidth={1.5}
            opacity={kRow5}
          />
        )}

        {/* Vertical divider between left & right charts */}
        <line x1={1058} y1={454} x2={1058} y2={774} stroke={C.line} strokeWidth={2} strokeDasharray="6 6" opacity={kRow0} />

        {/* LEFT HBarChart: Forward Throughput */}
        <HBarChart
          x0={335}
          x1={925}
          y1={470}
          y0={774}
          max={180}
          unit=" M/s"
          size={22}
          labelSize={22}
          barH={30}
          refLine={kRow0 > 0.5 ? { value: 160.4, label: "Idle 160.4", color: K.fast } : undefined}
          bars={[
            { label: "Idle spin (nothing)", value: 160.4, color: K.fast },
            { label: "Cold RAM (64 B)", value: 160.4, color: K.fast },
            { label: "Cold RAM (page stride)", value: 161.4, color: K.fast },
            { label: "Old ring (behind)", value: 162.6, color: K.fast, text: "162.0–162.6" },
            { label: "Just behind frontier", value: 160.2, color: K.fast },
            { label: "Ahead (write band)", value: 113.4, color: K.slow },
          ]}
          k={(i) => rowKs[i]}
        />

        {/* RIGHT HBarChart: Card L3 Requests (aligned rows, no duplicate labels) */}
        <HBarChart
          x0={1095}
          x1={1695}
          y1={470}
          y0={774}
          max={1150}
          dec={0}
          unit=" M/s"
          size={22}
          labelSize={22}
          barH={30}
          bars={[
            { label: "", value: 608, color: K.fwd },
            { label: "", value: 815, color: K.fwd },
            { label: "", value: 1033, color: K.knob, text: "1,033 M/s" },
            { label: "", value: 948, color: K.fwd, text: "790–948 M/s" },
            { label: "", value: 728, color: K.fwd },
            { label: "", value: 472, color: C.muted, text: "472 M/s (low!)" },
          ]}
          k={(i) => rowKs[i]}
        />

        {/* Subtle cue-1 hint inside empty chart frame before cue 2 fills it */}
        {kMain > 0.3 && kRow0 < 0.05 && (
          <text x={960} y={628} textAnchor="middle" fontSize={24} fontWeight={600} fill={C.muted} opacity={1 - kRow0 * 20}>
            Comparing forward throughput (left) vs. total card L3 reads (right) for each pause target…
          </text>
        )}
      </Stage>

      {/* Cue 4 verdict strip (fades out cleanly before Cue 5 NextQuestion) */}
      {kVerdict > 0.02 && (
        <div
          style={{
            position: "absolute",
            left: 60,
            top: 806,
            width: 1800,
            opacity: kVerdict,
            transform: `translateY(${(1 - kVerdict) * 14}px)`,
            background: "rgba(67,197,158,0.10)",
            border: `2px solid ${K.fast}`,
            borderRadius: 14,
            padding: "12px 22px",
            boxSizing: "border-box",
            fontFamily: FONT,
            display: "flex",
            alignItems: "center",
            gap: 16,
          }}
        >
          <span
            style={{
              fontSize: 19,
              fontWeight: 800,
              letterSpacing: 1.5,
              color: C.bg,
              background: K.fast,
              borderRadius: 8,
              padding: "5px 12px",
              whiteSpace: "nowrap",
            }}
          >
            VERDICT
          </span>
          <span style={{ fontSize: 25, fontWeight: 600, color: C.text, lineHeight: 1.25 }}>
            <strong style={{ color: K.fast }}>Location, not L3 bandwidth:</strong> write-band polling uses only{" "}
            <span style={{ fontFamily: MONO, color: C.muted }}>472 M/s</span> L3 yet crashes throughput to{" "}
            <strong style={{ color: K.slow }}>113.4 M/s</strong>{" "}
            <InfPill size={19} style={{ marginLeft: 8 }} />
          </span>
        </div>
      )}

      {/* Cue 5 explicit bridge to Exp 3 */}
      <NextQuestion x={60} y={806} w={1800} k={kNextQ}>
        One-way is solved (161.6 M/s). Now step inside the 13 M/s closed loop: where do the extra microseconds sit?
      </NextQuestion>
    </>
  );
}

export const S04: SceneDef = {
  id: "S04",
  title: "Exp 2 · Write-band conflict vs L3 bandwidth",
  stage: 1,
  source: "One-way · 3 µs pause · 33 verified runs × 2×10⁸ msgs · card L3 counters",
  dur: DUR,
  cues,
  C: Body,
};
