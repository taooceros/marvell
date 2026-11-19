import React from "react";
import { C, Cue, FONT, K, MONO, PacketStream, SceneDef, Stage, clamp01, fade, lerp, prog, useRealT, useT } from "../lib";
import { BarChart, Brace, InfPill, InfTag, KnobBar, Meter, NextQuestion, RingStrip, ringCenter } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 9.86, "**Question:** Two card cores polling non-stop catch up to the **write frontier** where `MOVDIR64B` writes are landing. Does chasing that frontier slow the host?", "Experiment one. Two card cores polling non-stop catch right up to the write frontier where host writes are still landing. Does chasing that frontier slow the host?"], // 0
  [10.21, 24.03, "**Fixed:** one-way forward, host **3.9 GHz**, **2 card cores**, up to **32 cells/poll** (`< 32` = caught up). **One knob:** pause when caught up (**0, 1, or 3 µs**).", "Fixed baseline: one-way forward, host three point nine gigahertz, two card cores taking up to thirty-two cells per poll. One knob: how long a core pauses when caught up: zero, one, or three microseconds."], // 1
  [24.38, 37.00, "**0 µs** (non-stop): **93.6%** of polls are empty; throughput sticks at **128.7 M/s**. **1 µs**: barely moves, **132.2 M/s** (`132–134` across modes).", "At zero microseconds, ninety-three point six percent of polls return empty, and throughput sticks at one hundred twenty-eight point seven million. One microsecond barely moves it, to one hundred thirty-two point two."], // 2
  [37.35, 48.61, "**3 µs** pause: throughput leaps **+26% to 161.6 M/s** — saturating the host core at **24.2 cycles per message**.", "At three microseconds, throughput leaps twenty-six percent to one hundred sixty-one point six million per second, saturating the host core at twenty-four point two cycles per message."], // 3
  [48.96, 64.66, "**Why:** Host counters show **447 writes** queuing **3.47 µs** at 0 µs (**[INF]** landing out of order). `1 µs` wakes inside that band; `3 µs` lets **~900 cells** settle (`360` queued, `2.23 µs`).", "Why? Host counters show four hundred forty-seven writes queuing three point five microseconds, our reading being out-of-order landing. One microsecond wakes inside that band; three lets nine hundred cells settle, cutting the queue to three hundred sixty."], // 4
  [65.01, 75.33, "**Next:** Did pausing 3 µs help because the core avoided the **active write band**, or simply because it freed **card L3 read bandwidth**?", "Wait. During a three microsecond pause, the core stopped reading completely. Did pausing help by avoiding the active write band, or simply by freeing card L 3 read bandwidth?"], // 5
];
const DUR = 76.73; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Knob bar timing: appears in cue 1 (-1), selects 0 µs at cue 2 start, slides to 1 µs mid-cue 2, then 3 µs at cue 3.
  const kKnob = prog(t, T[1], 0.55);
  const kSel0 = prog(t, T[2], 0.45);
  const kSel1 = prog(t, T[2] + 6.4, 0.65);
  const kSel2 = prog(t, T[3], 0.65);
  const activeKnob = kSel0 < 0.01 ? -1 : lerp(0, 1, kSel1) + lerp(0, 1, kSel2);

  // Phase progresses
  const kTop = prog(t, T[0], 0.55);
  const kCue0Lower = fade(t, T[0] + 0.3, T[1], 0.45);
  const kCue1Lower = fade(t, T[1] + 0.35, T[2], 0.45);
  const kData = prog(t, T[2] + 0.25, 0.55);

  // Bar & meter growth
  const kBar0 = prog(t, T[2] + 0.6, 0.85);
  const kBar1 = prog(t, T[2] + 6.6, 0.85);
  const kBar2 = prog(t, T[3] + 0.45, 0.9);
  const kGain = prog(t, T[3] + 1.15, 0.55);
  const kCue4 = prog(t, T[4] + 0.35, 0.55);
  const kWhyStrip = fade(t, T[4] + 0.35, T[5], 0.4);
  const kNextQ = prog(t, T[5] + 0.35, 0.45);

  // Ring geometry: x = 84..1206, y = 308..366
  const RX = 84;
  const RY = 308;
  const RCW = 41;
  const RCH = 58;
  const RGAP = 6;
  const RN = 24;

  // Ring cursor dynamics:
  // Cue 0..2 early: pressed hard against slot 14 (colliding!) with slight jitter.
  // Cue 2 late (1 µs): pulls back slightly to ~12.4, still right inside/adjacent to frontier.
  // Cue 3..5 (3 µs): pulls back cleanly to slot 7.5 behind settled cyan cells.
  const jitter = Math.sin(rt * 12) * 0.26 * (1 - kSel2);
  const baseNext = lerp(lerp(13.65, 12.2, kSel1), 7.4, kSel2);
  const nextAt = baseNext + jitter;
  const colliding = 1 - kSel2;
  const nextColor = kSel2 > 0.55 ? K.fast : kSel1 > 0.55 ? K.card : K.slow;
  const nextLabel = kSel2 > 0.55 ? "next (3 µs)" : kSel1 > 0.55 ? "next (1 µs)" : "next";

  const cellState = (i: number) => {
    const settledEdge = Math.round(lerp(lerp(13, 12, kSel1), 7, kSel2));
    if (i < settledEdge) return "consumed" as const;
    if (i < 14) return "landed" as const;
    if (i < 21) {
      // In cue 4+, reveal out-of-order holes inside the ~3.5 µs landing band
      if (kCue4 > 0.4 && (i === 15 || i === 18)) return "hole" as const;
      if (kCue4 > 0.4 && (i === 16 || i === 19)) return "landed" as const;
      return "inflight" as const;
    }
    return "empty" as const;
  };

  // Top-right card crossfades (Cue 0 -> Cue 1 -> Cues 2..5)
  const kRight0 = fade(t, T[0] + 0.15, T[1], 0.4);
  const kRight1 = fade(t, T[1] + 0.3, T[2], 0.4);
  const kRight2 = prog(t, T[2] + 0.25, 0.45);
  const kRight3 = prog(t, T[3] + 0.35, 0.5);

  const frontierX = ringCenter(RX, RCW, RGAP, 14);
  const pulse = 0.5 + 0.5 * Math.sin(rt * 7);

  return (
    <>
      {/* Top experiment frame (cues 1–5) */}
      <KnobBar
        y={118}
        size={22}
        k={kKnob}
        fixed={["One-way forward", "Host 3.9 GHz", "2 card cores", "≤ 32 cells/poll"]}
        knob="Pause when caught up"
        options={["0 µs", "1 µs", "3 µs"]}
        active={activeKnob}
      />

      {/* Full-frame SVG stage */}
      <Stage>
        {/* ================= TOP-LEFT: REQUEST RING MECHANISM PANEL (y = 196..442) ================= */}
        <g opacity={kTop}>
          <rect x={60} y={196} width={1180} height={246} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />
          <text x={84} y={228} fontSize={23} fontWeight={700} fill={C.text}>
            Card Request Ring · host MOVDIR64B writes →
          </text>

          {/* Compact legend top-right of ring panel */}
          <g transform="translate(808, 212)">
            <rect x={0} y={0} width={18} height={18} rx={4} fill={K.consumed} stroke={C.line} strokeWidth={1.5} />
            <text x={24} y={15} fontSize={19} fill={C.muted}>consumed</text>

            <rect x={128} y={0} width={18} height={18} rx={4} fill={K.landed} opacity={0.9} />
            <text x={152} y={15} fontSize={19} fill={K.landed}>landed</text>

            <rect x={228} y={0} width={18} height={18} rx={4} fill="url(#hatchRed)" stroke={K.inflight} strokeWidth={1.5} />
            <text x={252} y={15} fontSize={19} fill={K.inflight}>in-flight</text>

            {kCue4 > 0.05 && (
              <g opacity={kCue4}>
                <rect x={338} y={0} width={18} height={18} rx={4} fill="#0b1020" stroke={K.inflight} strokeWidth={2} strokeDasharray="4 3" />
                <text x={362} y={15} fontSize={19} fill={K.inflight}>hole</text>
              </g>
            )}
          </g>

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
            nextAt={nextAt}
            nextLabel={nextLabel}
            nextColor={nextColor}
            band={{
              from: 14,
              to: 21,
              color: K.inflight,
              label: kCue4 > 0.5 ? "~3.5 µs landing band" : "active write frontier",
            }}
            size={22}
          />

          {/* Collision pulse at frontier during 0 µs / 1 µs */}
          {colliding > 0.05 && (
            <g opacity={colliding * (0.45 + 0.55 * pulse)}>
              <circle cx={frontierX - 12} cy={RY + RCH / 2} r={22 + pulse * 10} fill="none" stroke={K.slow} strokeWidth={3} />
              <text x={838} y={272} fontSize={21} fontWeight={700} fill={K.slow}>
                polling hits landing writes!
              </text>
            </g>
          )}

          {/* Green settled gap brace in cues 3–5 */}
          {kSel2 > 0.25 && (
            <Brace
              x1={RX + 7 * (RCW + RGAP)}
              x2={RX + 14 * (RCW + RGAP) - RGAP}
              y={RY + RCH + 10}
              dir={1}
              color={K.fast}
              size={22}
              label={kCue4 > 0.5 ? "~900 cells settle (3 µs)" : "clean gap behind frontier"}
              opacity={clamp01((kSel2 - 0.25) / 0.75)}
            />
          )}

          {/* [INF] out-of-order landing callout above red band in cue 4–5 */}
          {kCue4 > 0.05 && (
            <g opacity={kCue4}>
              <InfTag x={788} y={248} size={20} />
              <text x={864} y={268} fontSize={22} fontWeight={700} fill={K.inf}>
                out-of-order landing
              </text>
            </g>
          )}
        </g>

        {/* ================= TOP-RIGHT: DYNAMIC POLLING STATUS PANEL (x = 1265..1860, y = 196..442) ================= */}
        <g opacity={kTop}>
          <rect x={1265} y={196} width={595} height={246} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />

          {/* Cue 0: Frontier collision question */}
          {kRight0 > 0.02 && (
            <g opacity={kRight0}>
              <text x={1294} y={236} fontSize={22} fontWeight={800} fill={K.slow} letterSpacing={1.5}>
                FRONTIER COLLISION?
              </text>
              <text x={1294} y={282} fontSize={26} fontWeight={700} fill={C.text}>
                2 card cores poll non-stop
              </text>
              <text x={1294} y={322} fontSize={23} fill={C.muted}>
                Read cursor catches up to the exact
              </text>
              <text x={1294} y={354} fontSize={23} fill={C.muted}>
                cache lines still arriving from host.
              </text>
              <rect x={1294} y={376} width={536} height={46} rx={10} fill="rgba(249,65,68,0.12)" stroke={K.slow} strokeWidth={1.5} />
              <text x={1562} y={406} textAnchor="middle" fontSize={23} fontWeight={700} fill={K.slow}>
                Does chasing the frontier slow the host?
              </text>
            </g>
          )}

          {/* Cue 1: Caught-up rule */}
          {kRight1 > 0.02 && (
            <g opacity={kRight1}>
              <text x={1294} y={236} fontSize={22} fontWeight={800} fill={K.knob} letterSpacing={1.5}>
                CAUGHT-UP RULE
              </text>
              <text x={1294} y={282} fontSize={25} fontWeight={700} fill={C.text}>
                2 card cores · up to 32 cells/poll
              </text>
              <rect x={1294} y={304} width={536} height={54} rx={10} fill="#0f1528" stroke={K.knob} strokeWidth={2} />
              <text x={1562} y={339} textAnchor="middle" fontSize={24} fontWeight={700} fill={K.knob} fontFamily={MONO}>
                {"< 32 cells  ⇒  caught up  ⇒  pause"}
              </text>
              <text x={1294} y={404} fontSize={22} fill={C.muted}>
                Sweep one knob: 0 µs · 1 µs · 3 µs pause
              </text>
            </g>
          )}

          {/* Cues 2–5: Empty-poll rate meters */}
          {kRight2 > 0.02 && (
            <g opacity={kRight2}>
              <text x={1294} y={232} fontSize={23} fontWeight={700} fill={C.text}>
                Card Core Empty-Poll Rate
              </text>
              <Meter
                x={1294}
                y={278}
                w={536}
                h={24}
                value={93.6}
                max={100}
                color={K.slow}
                label="0 µs (non-stop)"
                valueText="93.6% empty"
                k={kBar0}
                size={22}
              />
              <Meter
                x={1294}
                y={356}
                w={536}
                h={24}
                value={0.7}
                max={100}
                color={K.fast}
                label="3 µs pause"
                valueText="0.7% empty"
                k={kRight3}
                size={22}
              />
              <text
                x={1294}
                y={418}
                fontSize={21}
                fontWeight={600}
                fill={kRight3 > 0.5 ? K.fast : K.card}
                opacity={kRight3 > 0.5 ? kRight3 : kBar1}
              >
                {kRight3 > 0.5
                  ? "Host core saturated: 24.2 cycles/msg"
                  : "1 µs: 132–134 M/s across modes (inside band)"}
              </text>
            </g>
          )}
        </g>

        {/* ================= CUE 0 LOWER VISUAL: LIVE HOST -> CARD FRONTIER RACE ================= */}
        {kCue0Lower > 0.02 && (
          <g opacity={kCue0Lower}>
            <rect x={60} y={468} width={1800} height={316} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={96} y={512} fontSize={25} fontWeight={700} fill={C.text}>
              One-Way Forward Path: Host Streaming Writes vs. Non-Stop Card Polling
            </text>

            {/* Host box */}
            <rect x={100} y={548} width={410} height={190} rx={16} fill="#101830" stroke={K.host} strokeWidth={2.5} />
            <text x={305} y={600} textAnchor="middle" fontSize={28} fontWeight={800} fill={K.host}>
              Host Core (3.9 GHz)
            </text>
            <text x={305} y={644} textAnchor="middle" fontSize={23} fontFamily={MONO} fill={K.fwd}>
              MOVDIR64B (64 B writes)
            </text>
            <text x={305} y={688} textAnchor="middle" fontSize={22} fill={C.muted}>
              Posted PCIe writes → card ring
            </text>

            {/* Animated write stream */}
            <line x1={518} y1={642} x2={790} y2={642} stroke={K.fwd} strokeWidth={4} opacity={0.35} />
            <PacketStream pts={[[520, 642], [790, 642]]} travel={rt * 190} gap={52} size={13} color={K.fwd} />
            <text x={655} y={606} textAnchor="middle" fontSize={22} fontWeight={700} fill={K.fwd}>
              write stream
            </text>

            {/* Frontier clash zone */}
            <rect x={800} y={548} width={400} height={190} rx={16} fill="rgba(249,65,68,0.10)" stroke={K.inflight} strokeWidth={2.5} strokeDasharray="8 6" />
            <text x={1000} y={604} textAnchor="middle" fontSize={27} fontWeight={800} fill={K.inflight}>
              Active Write Frontier
            </text>
            <text x={1000} y={648} textAnchor="middle" fontSize={23} fill={C.text}>
              Host writes still landing
            </text>
            <text x={1000} y={688} textAnchor="middle" fontSize={23} fontWeight={700} fill={K.slow}>
              ← Card reads collide here →
            </text>

            {/* Card polling arrows */}
            <line x1={1412} y1={642} x2={1208} y2={642} stroke={K.card} strokeWidth={4} opacity={0.45} />
            <PacketStream pts={[[1410, 642], [1208, 642]]} travel={rt * 190} gap={52} size={13} color={K.card} />
            <text x={1310} y={606} textAnchor="middle" fontSize={22} fontWeight={700} fill={K.card}>
              non-stop polls
            </text>

            {/* Card box */}
            <rect x={1420} y={548} width={400} height={190} rx={16} fill="#101830" stroke={K.card} strokeWidth={2.5} />
            <text x={1620} y={600} textAnchor="middle" fontSize={28} fontWeight={800} fill={K.card}>
              2 Card Cores
            </text>
            <text x={1620} y={644} textAnchor="middle" fontSize={23} fill={C.text}>
              Continuous ring-head polling
            </text>
            <text x={1620} y={688} textAnchor="middle" fontSize={22} fill={C.muted}>
              Catches up immediately
            </text>
          </g>
        )}

        {/* ================= CUE 1 LOWER VISUAL: SINGLE-VARIABLE SWEEP PREVIEW ================= */}
        {kCue1Lower > 0.02 && (
          <g opacity={kCue1Lower}>
            <rect x={60} y={468} width={1800} height={316} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={96} y={512} fontSize={25} fontWeight={700} fill={K.knob}>
              Single-Variable Test: Hold everything fixed, vary only the caught-up pause
            </text>

            {[
              { x: 100, title: "0 µs Pause", sub: "Non-stop polling", lines: ["Core immediately re-reads the frontier", "on every empty poll."], color: K.slow },
              { x: 690, title: "1 µs Pause", sub: "Short backoff", lines: ["Core waits 1 µs after seeing", "< 32 ready cells."], color: K.card },
              { x: 1280, title: "3 µs Pause", sub: "Longer backoff", lines: ["Core waits 3 µs after seeing", "< 32 ready cells."], color: K.fast },
            ].map((c, idx) => (
              <g key={idx}>
                <rect x={c.x} y={544} width={540} height={204} rx={16} fill="#101830" stroke={c.color} strokeWidth={2.5} />
                <text x={c.x + 28} y={596} fontSize={32} fontWeight={800} fill={c.color}>
                  {c.title}
                </text>
                <text x={c.x + 28} y={636} fontSize={23} fontWeight={700} fill={C.text}>
                  {c.sub}
                </text>
                <text x={c.x + 28} y={684} fontSize={22} fill={C.muted}>
                  {c.lines[0]}
                </text>
                <text x={c.x + 28} y={716} fontSize={22} fill={C.muted}>
                  {c.lines[1]}
                </text>
              </g>
            ))}
          </g>
        )}

        {/* ================= CUES 2–5 LOWER-LEFT: THROUGHPUT BAR CHART ================= */}
        {kData > 0.02 && (
          <g opacity={kData}>
            <rect x={60} y={464} width={900} height={328} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={90} y={498} fontSize={24} fontWeight={700} fill={C.text}>
              Forward Throughput (M/s)
            </text>

            {/* +26% highlight badge in Cue 3–5 */}
            {kGain > 0.02 && (
              <g opacity={kGain}>
                <rect x={762} y={472} width={174} height={40} rx={10} fill="rgba(67,197,158,0.18)" stroke={K.fast} strokeWidth={2.5} />
                <text x={849} y={499} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.fast}>
                  +26% leap!
                </text>
              </g>
            )}

            <BarChart
              x0={100}
              x1={920}
              y1={532}
              y0={714}
              max={185}
              unit=" M/s"
              size={25}
              labelSize={23}
              barW={148}
              refLine={kBar2 > 0.4 ? { value: 161.6, label: "Host core ceiling", color: K.fast } : undefined}
              bars={[
                { label: "0 µs", value: 128.7, color: K.slow, sub: "93.6% empty polls" },
                { label: "1 µs", value: 132.2, color: K.card, sub: "132–134 across modes" },
                { label: "3 µs", value: 161.6, color: K.fast, sub: "24.2 cycles/msg" },
              ]}
              k={(i) => (i === 0 ? kBar0 : i === 1 ? kBar1 : kBar2)}
            />
          </g>
        )}

        {/* ================= CUES 2–5 LOWER-RIGHT: HOST WRITE-QUEUE METERS ================= */}
        {kData > 0.02 && (
          <g opacity={kData}>
            <rect x={990} y={464} width={870} height={328} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={1020} y={498} fontSize={24} fontWeight={700} fill={K.host}>
              Host Write Queue (entries · residence time)
            </text>

            <Meter
              x={1022}
              y={550}
              w={806}
              h={26}
              value={447}
              max={520}
              color={K.slow}
              label="0 µs pause (non-stop)"
              valueText="447 entries · 3.47 µs"
              k={kBar0}
              size={23}
            />

            <Meter
              x={1022}
              y={628}
              w={806}
              h={26}
              value={433}
              max={520}
              color={K.card}
              label="1 µs pause"
              valueText="433 entries · 3.28 µs"
              k={kBar1}
              size={23}
            />

            <Meter
              x={1022}
              y={706}
              w={806}
              h={26}
              value={360}
              max={520}
              color={K.fast}
              label="3 µs pause"
              valueText="360 entries · 2.23 µs"
              k={kBar2}
              size={23}
            />

            {kCue4 > 0.02 && (
              <text x={1022} y={770} fontSize={22} fontWeight={600} fill={C.text} opacity={kCue4}>
                1 µs wakes inside ~3.5 µs band · 3 µs lets ~900 cells settle
              </text>
            )}
          </g>
        )}
      </Stage>

      {/* Cue 4 compact bottom explanation strip (fades out cleanly before NextQuestion at cue 5) */}
      {kWhyStrip > 0.02 && (
        <div
          style={{
            position: "absolute",
            left: 60,
            top: 806,
            width: 1800,
            opacity: kWhyStrip,
            transform: `translateY(${(1 - kWhyStrip) * 14}px)`,
            background: "rgba(94,234,212,0.09)",
            border: `2px solid ${K.inf}`,
            borderRadius: 14,
            padding: "12px 22px",
            boxSizing: "border-box",
            fontFamily: FONT,
            display: "flex",
            alignItems: "center",
            gap: 16,
          }}
        >
          <InfPill size={20} />
          <span style={{ fontSize: 23, fontWeight: 600, color: C.text, lineHeight: 1.25 }}>
            Out-of-order landing over ~3.5 µs: <strong style={{ color: K.card }}>1 µs</strong> wakes inside the band;{" "}
            <strong style={{ color: K.fast }}>3 µs</strong> lets ~900 cells settle (0.7% empty, queue 447 → 360).
          </span>
        </div>
      )}

      {/* Cue 5 explicit bridge to Exp 2 */}
      <NextQuestion x={60} y={806} w={1800} k={kNextQ}>
        Did pausing 3 µs help by avoiding the active write band — or simply by freeing card L3 read bandwidth?
      </NextQuestion>
    </>
  );
}

export const S03: SceneDef = {
  id: "S03",
  title: "Exp 1 · Card frontier polling",
  stage: 1,
  source: "One-way forward · 2 card cores · 27 verified runs × 2×10⁸ msgs",
  dur: DUR,
  cues,
  C: Body,
};
