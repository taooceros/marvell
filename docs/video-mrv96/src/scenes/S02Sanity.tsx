import React from "react";
import {
  C,
  Card,
  Cue,
  FONT,
  K,
  Link,
  MONO,
  PacketStream,
  SceneDef,
  Stage,
  clamp01,
  fade,
  lerp,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { BarChart, InfPill, KnobBar, Meter, NextQuestion, Rig, Takeaway, rigGeom, rigRoutes } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 9.08, "**Suspect 0:** Test the simplest idea first — do return replies from card to host choke forward `MOVDIR64B` writes on PCIe?", "Start with the simplest suspect. Do return replies from card to host choke forward move dir sixty-four B writes on P C I e?"], // 0
  [9.43, 20.87, "**Fixed:** host sender pinned **3.9 GHz** (uncore **2.2 GHz**), **1 receive + 1 reply core** with WC. **One knob:** replies **OFF vs ON**.", "Fixed baseline: host sender pinned at three point nine gigahertz, uncore two point two, one receive core plus one write-combining reply core. One knob: replies off versus on."], // 1
  [21.22, 33.26, "**Result:** forward alone runs **113.1 M/s**; with replies flowing it stays **112.2 M/s**. **Verdict: No** — return traffic does not slow forward writes.", "Result: forward alone runs one hundred thirteen point one million per second; with replies flowing it stays one hundred twelve point two. Verdict: no, return traffic does not slow forward writes."], // 2
  [33.61, 47.09, "An earlier drop to **75.6 M/s** had an unpinned sender at **~2.1 GHz** — **[INF]** a clock drop, not PCIe backpressure. Rule zero: pin every clock.", "An earlier drop to seventy-five point six came only from an unpinned sender at two point one gigahertz. Our reading: clock drop, not P C I e, though two point one was never run one way. Always pin clocks."], // 3
  [47.44, 59.24, "**Next:** Return traffic is innocent. Give pure one-way traffic **2 receive cores** — yet forward still stalls at **128.7 M/s**. What holds it back?", "Return traffic is innocent. Next, give pure one-way traffic two receive cores so the receiver is not the limit, yet forward still stalls at one hundred twenty-eight point seven million. Why?"], // 4
];
const DUR = 60.64; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Left-half Rig: x = 60..1068, y = 218..588
  const g = rigGeom(0.56, 26.4, 106);
  const routes = rigRoutes(g);

  // Cue envelopes
  const kCue0 = fade(t, T[0], T[1] - 0.1, 0.45);
  const kBar = prog(t, T[1] + 0.1, 0.5);
  const kChartFrame = prog(t, T[1] + 0.35, 0.5);
  const kSetupStrip = fade(t, T[1] + 0.4, T[3] - 0.1, 0.45);

  // Knob toggle animation:
  // Cue 1: OFF (0) -> ON (1) preview as narrator says "OFF vs ON"
  // Cue 2: OFF (0) while first bar grows -> ON (1) as second bar grows
  // Cue 3-4: stays ON (1)
  const kPreviewOn = prog(t, T[1] + 7.2, 0.7);
  const kResetOff = prog(t, T[2] + 0.0, 0.35);
  const kTestOn = prog(t, T[2] + 4.0, 0.65);
  const knobActive = clamp01(kPreviewOn * (1 - kResetOff) + kTestOn);

  // Bars in Cue 2 & Cue 3
  const kBarOff = prog(t, T[2] + 0.8, 0.8);
  const kBarOn = prog(t, T[2] + 4.4, 0.8);
  const kDelta = prog(t, T[2] + 5.8, 0.5);
  const kVerdict = prog(t, T[2] + 6.4, 0.55);

  // Cue 3: Unpinned clock drop
  const kCue3 = prog(t, T[3] + 0.3, 0.55);
  const kBarUnpin = prog(t, T[3] + 0.9, 0.85);
  const kClockDrop = prog(t, T[3] + 2.2, 0.9);
  const kCue3Active = fade(t, T[3], T[4] - 0.05, 0.45);

  // Cue 4: Bridge to next scene (2 receive cores -> 128.7 M/s)
  const kCue4 = prog(t, T[4] + 0.3, 0.5);
  const kNextQ = prog(t, T[4] + 0.6, 0.55);

  // Return stream intensity on Rig
  const pulse0 = 0.45 + 0.55 * Math.sin(rt * 3.4);
  const retStreamOp =
    kCue0 * (0.55 + 0.45 * pulse0) +
    (1 - kCue0) * (1 - kCue4) * clamp01(knobActive) * 0.95;

  // Clock display on Rig host core: drops 3.9 -> ~2.1 during Cue 3, returns to 3.9 in Cue 4
  const clockDropNow = kClockDrop * (1 - kCue4);
  const clockVal = lerp(3.9, 2.1, clockDropNow);
  const hostClockLabel = clockDropNow > 0.5 ? "~2.1 GHz" : "3.9 GHz";

  // Forward packet speed slows slightly during Cue 3 clock drop
  const fwdTravel = rt * lerp(240, 145, clockDropNow);
  const retTravel = rt * 220;

  // Rig highlights per cue
  const hlHost = clamp01(fade(t, T[1] + 0.4, T[2] - 0.1, 0.4) + kCue3Active);
  const hlCard = clamp01(fade(t, T[1] + 3.2, T[2] - 0.1, 0.4) + kCue4);

  // Chart coordinates inside right panel (x: 1104..1860, y: 204..628)
  const chartY1 = 324;
  const chartY0 = 532;
  const chartMax = 135;
  const sy = linScale(0, chartMax, chartY0, chartY1);

  return (
    <>
      <Stage>
        {/* Left-half Hardware Rig (all cues) */}
        <Rig
          g={g}
          hl={{
            hcore: hlHost,
            ccores: hlCard,
            wc: fade(t, T[1] + 3.2, T[2] - 0.1, 0.4),
          }}
          labels={{
            host: ["HOST", "Xeon"],
            card: ["DPU", "OCTEON 10"],
            hcore: ["core 40", hostClockLabel],
            wq: ["WQ", "CHA"],
            hring: ["replies", "host"],
            cring: ["requests", "card"],
            ccores: kCue4 > 0.5 ? ["2 rx cores", "2.0 GHz"] : ["1 rx + 1 tx", "2.0 GHz"],
            wc: ["WC buf", "64 B"],
            link: ["PCIe Gen5 ×4"],
          }}
          cores={2}
          activeCore={kCue4 > 0.5 ? Math.floor((rt * 3) % 2) : retStreamOp > 0.2 ? 1 : 0}
          ringFill={{
            cring: 3 + Math.floor((rt * 3.2) % 4),
            hring: retStreamOp > 0.2 ? 2 + Math.floor((rt * 2.8) % 4) : 0,
          }}
          lanes={{
            fwd: 1,
            ret: lerp(0.22, 1, clamp01(kCue0 + retStreamOp)),
          }}
        />

        {/* Forward packet stream (always active) */}
        <Link pts={routes.send} color={clockDropNow > 0.5 ? K.slow : K.fwd} w={4} opacity={0.75} />
        <PacketStream
          pts={routes.send}
          travel={fwdTravel}
          gap={56}
          size={11}
          color={clockDropNow > 0.5 ? K.slow : K.fwd}
        />
        <PacketStream
          pts={routes.fwd}
          travel={fwdTravel}
          gap={52}
          size={12}
          color={clockDropNow > 0.5 ? K.slow : K.fwd}
        />

        {/* Return packet stream (pulsing in Cue 0, controlled by knob in Cues 1–3, off in Cue 4) */}
        {retStreamOp > 0.02 && (
          <g opacity={retStreamOp}>
            <Link pts={routes.reply} color={K.ret} w={4} opacity={0.75} />
            <Link pts={routes.hpoll} color={K.ret} w={4} opacity={0.65} />
            <PacketStream pts={routes.reply} travel={retTravel} gap={58} size={11} color={K.ret} />
            <PacketStream pts={routes.ret} travel={retTravel} gap={54} size={12} color={K.ret} />
            <PacketStream pts={routes.hpoll} travel={retTravel} gap={58} size={11} color={K.ret} />
          </g>
        )}

        {/* Cue 0: Question clash badge centered on PCIe link */}
        {kCue0 > 0.01 && (
          <g opacity={kCue0}>
            <circle
              cx={g.r.link.x + g.r.link.w / 2}
              cy={(g.fwdY + g.retY) / 2 - 44}
              r={26 + 4 * pulse0}
              fill="rgba(249,199,79,0.12)"
              stroke={K.knob}
              strokeWidth={2.5}
              opacity={0.8}
            />
            <text
              x={g.r.link.x + g.r.link.w / 2}
              y={(g.fwdY + g.retY) / 2 - 34}
              textAnchor="middle"
              fontFamily={FONT}
              fontSize={30}
              fontWeight={900}
              fill={K.knob}
            >
              ?
            </text>
          </g>
        )}

        {/* Cue 3: Red clock-warning ring around host core */}
        {clockDropNow > 0.05 && (
          <rect
            x={g.r.hcore.x - 6}
            y={g.r.hcore.y - 6}
            width={g.r.hcore.w + 12}
            height={g.r.hcore.h + 12}
            rx={14}
            fill="none"
            stroke={K.slow}
            strokeWidth={5}
            opacity={clockDropNow * 0.9}
          />
        )}

        {/* Cue 4: Forward ceiling callout on PCIe link */}
        {kCue4 > 0.01 && (
          <g opacity={kCue4}>
            <rect
              x={g.r.link.x + 86}
              y={g.fwdY - 82}
              width={254}
              height={38}
              rx={10}
              fill="rgba(11,16,32,0.92)"
              stroke={K.knob}
              strokeWidth={2}
            />
            <text
              x={g.r.link.x + 213}
              y={g.fwdY - 56}
              textAnchor="middle"
              fontFamily={MONO}
              fontSize={21}
              fontWeight={800}
              fill={K.knob}
            >
              stalls at 128.7 M/s?
            </text>
          </g>
        )}

        {/* Cue 3–4: Lower-left Clock Drop panel + SVG Host Clock Meter */}
        {kCue3 > 0.01 && (
          <g opacity={kCue3 * lerp(1, 0.6, kCue4)}>
            <rect
              x={60}
              y={614}
              width={1008}
              height={168}
              rx={18}
              fill={C.panel}
              stroke={K.slow}
              strokeWidth={2}
            />
            <Meter
              x={88}
              y={682}
              w={372}
              h={26}
              value={clockVal}
              max={4.2}
              color={clockDropNow > 0.5 ? K.slow : K.fwd}
              label="Host sender clock"
              valueText={clockDropNow > 0.5 ? "~2.1 GHz" : "3.9 GHz"}
              k={1}
              size={22}
              marks={[
                { at: 2.1, label: "~2.1", color: K.slow },
                { at: 3.9, label: "3.9 GHz", color: K.fwd },
              ]}
            />
          </g>
        )}

        {/* Cues 1–4: Right-half BarChart Panel */}
        {kChartFrame > 0.01 && (
          <g opacity={kChartFrame} fontFamily={FONT}>
            <rect
              x={1104}
              y={204}
              width={756}
              height={424}
              rx={18}
              fill={C.panel}
              stroke={C.line}
              strokeWidth={2}
            />
            <text x={1132} y={244} fontSize={25} fontWeight={800} fill={C.text}>
              Forward MOVDIR64B throughput (M/s)
            </text>

            {/* Subtle horizontal gridlines with clean left-margin tick labels */}
            {[50, 100].map((v) => (
              <g key={v}>
                <line
                  x1={1162}
                  y1={sy(v)}
                  x2={1832}
                  y2={sy(v)}
                  stroke={C.line}
                  strokeWidth={1.5}
                  strokeDasharray="6 6"
                  opacity={0.7}
                />
                <text x={1154} y={sy(v) + 7} textAnchor="end" fontSize={19} fill={C.dim} fontFamily={MONO}>
                  {v}
                </text>
              </g>
            ))}

            {/* Controlled A/B test bars (Replies OFF vs ON) */}
            <BarChart
              x0={1164}
              x1={1570}
              y0={chartY0}
              y1={chartY1}
              max={chartMax}
              barW={114}
              unit=""
              dec={1}
              size={26}
              labelSize={23}
              bars={[
                { label: "Replies OFF", sub: "3.9 GHz pinned", value: 113.1, color: K.fwd, text: "113.1 M/s" },
                { label: "Replies ON", sub: "3.9 GHz pinned", value: 112.2, color: K.ret, text: "112.2 M/s" },
              ]}
              k={(i) => (i === 0 ? kBarOff : kBarOn)}
            />

            {/* −0.8% callout badge spanning bar 0 and bar 1 */}
            {kDelta > 0.01 && (
              <g opacity={kDelta}>
                <rect
                  x={1241}
                  y={258}
                  width={252}
                  height={36}
                  rx={18}
                  fill="rgba(67,197,158,0.15)"
                  stroke={K.fast}
                  strokeWidth={2}
                />
                <text
                  x={1367}
                  y={282}
                  textAnchor="middle"
                  fontSize={21}
                  fontWeight={800}
                  fill={K.fast}
                  fontFamily={MONO}
                >
                  −0.8% (no slowdown)
                </text>
              </g>
            )}

            {/* Cue 3: Vertical divider + Unpinned 75.6 M/s comparison bar */}
            {kCue3 > 0.01 && (
              <g opacity={kCue3}>
                <line
                  x1={1594}
                  y1={264}
                  x2={1594}
                  y2={606}
                  stroke={C.line}
                  strokeWidth={2}
                  strokeDasharray="6 6"
                />
                <text x={1720} y={282} textAnchor="middle" fontSize={20} fontWeight={700} fill={K.slow}>
                  Earlier run
                </text>
                <BarChart
                  x0={1612}
                  x1={1828}
                  y0={chartY0}
                  y1={chartY1}
                  max={chartMax}
                  barW={114}
                  unit=""
                  dec={1}
                  size={26}
                  labelSize={23}
                  bars={[
                    { label: "Unpinned", sub: "~2.1 GHz", value: 75.6, color: K.slow, text: "75.6 M/s" },
                  ]}
                  k={() => kBarUnpin}
                />
              </g>
            )}
          </g>
        )}
      </Stage>

      {/* Top Experiment KnobBar (Cues 1–4) */}
      {kBar > 0.01 && (
        <KnobBar
          y={118}
          size={22}
          fixed={["Host 3.9 GHz", "Uncore 2.2 GHz", "1 recv + 1 reply core", "WC replies"]}
          knob="Return replies"
          options={["OFF", "ON"]}
          active={knobActive}
          k={kBar}
        />
      )}

      {/* Cue 0: Right-side Suspect 0 question card */}
      {kCue0 > 0.01 && (
        <Card
          x={1104}
          y={222}
          w={756}
          h={366}
          border={K.knob}
          style={{
            opacity: kCue0,
            transform: `translateY(${(1 - kCue0) * 16}px)`,
            padding: "28px 34px",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
          }}
        >
          <div>
            <div
              style={{
                display: "inline-block",
                fontFamily: MONO,
                fontSize: 21,
                fontWeight: 800,
                letterSpacing: 1.5,
                color: C.bg,
                background: K.knob,
                borderRadius: 8,
                padding: "4px 14px",
                marginBottom: 18,
              }}
            >
              SUSPECT 0 · RETURN TRAFFIC
            </div>
            <div style={{ fontSize: 38, fontWeight: 800, color: C.text, lineHeight: 1.25 }}>
              Do return replies choke forward{" "}
              <span style={{ fontFamily: MONO, color: K.fwd }}>MOVDIR64B</span> writes on PCIe?
            </div>
          </div>

          <div
            style={{
              display: "flex",
              gap: 16,
              fontSize: 23,
              color: C.muted,
              borderTop: `1.5px solid ${C.line}`,
              paddingTop: 18,
            }}
          >
            <span style={{ color: K.fwd, fontWeight: 700 }}>Forward: MOVDIR64B</span>
            <span>vs</span>
            <span style={{ color: K.ret, fontWeight: 700 }}>Return: WC replies</span>
          </div>
        </Card>
      )}

      {/* Cue 0: Left-side compact legend below Rig */}
      {kCue0 > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 60,
            top: 616,
            width: 1008,
            display: "flex",
            justifyContent: "center",
            gap: 28,
            opacity: kCue0,
            fontFamily: FONT,
            fontSize: 23,
          }}
        >
          <span
            style={{
              background: C.panel,
              border: `2px solid ${K.fwd}`,
              borderRadius: 12,
              padding: "10px 22px",
              color: C.text,
            }}
          >
            <strong style={{ color: K.fwd }}>Host → Card:</strong> MOVDIR64B writes
          </span>
          <span
            style={{
              background: C.panel,
              border: `2px solid ${K.ret}`,
              borderRadius: 12,
              padding: "10px 22px",
              color: C.text,
            }}
          >
            <strong style={{ color: K.ret }}>Card → Host:</strong> WC replies (pulsing)
          </span>
        </div>
      )}

      {/* Cues 1–2: Left-side Fixed Setup detail cards below Rig */}
      {kSetupStrip > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 60,
            top: 616,
            width: 1008,
            display: "flex",
            gap: 20,
            opacity: kSetupStrip,
            fontFamily: FONT,
          }}
        >
          <div
            style={{
              flex: 1,
              background: C.panel,
              border: `2px solid ${K.host}`,
              borderRadius: 16,
              padding: "16px 24px",
            }}
          >
            <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 1.5, color: K.host }}>
              HOST CLOCKS PINNED
            </div>
            <div style={{ fontSize: 27, fontWeight: 800, color: C.text, marginTop: 6 }}>
              3.9 GHz core · 2.2 GHz uncore
            </div>
            <div style={{ fontSize: 22, color: C.muted, marginTop: 4 }}>
              Same pinned sender for both OFF and ON runs
            </div>
          </div>

          <div
            style={{
              flex: 1,
              background: C.panel,
              border: `2px solid ${K.card}`,
              borderRadius: 16,
              padding: "16px 24px",
            }}
          >
            <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 1.5, color: K.card }}>
              DPU ROLES FIXED
            </div>
            <div style={{ fontSize: 27, fontWeight: 800, color: C.text, marginTop: 6 }}>
              1 receive + 1 reply core
            </div>
            <div style={{ fontSize: 22, color: C.muted, marginTop: 4 }}>
              Reply core toggles WC return traffic OFF vs ON
            </div>
          </div>
        </div>
      )}

      {/* Cue 3–4: Right text block inside lower-left Clock Drop panel */}
      {kCue3 > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 492,
            top: 628,
            width: 554,
            opacity: kCue3 * lerp(1, 0.6, kCue4),
            fontFamily: FONT,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <InfPill size={20} />
            <span style={{ fontSize: 23, fontWeight: 800, color: K.slow }}>
              Clock drop, not PCIe backpressure
            </span>
          </div>
          <div style={{ fontSize: 22, color: C.text, lineHeight: 1.35 }}>
            Earlier <strong>75.6 M/s</strong> run had unpinned sender at{" "}
            <strong style={{ color: K.slow }}>~2.1 GHz</strong> (~2.1 GHz not run one-way).
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: K.knob, marginTop: 6 }}>
            Rule 0: pin every clock.
          </div>
        </div>
      )}

      {/* Cues 2–4: Right-side Verdict Takeaway */}
      {kVerdict > 0.01 && (
        <Takeaway
          x={1104}
          y={648}
          w={756}
          k={kVerdict}
          color={K.fast}
          label="VERDICT · NO"
          size={28}
        >
          Return traffic does <strong>NOT</strong> slow forward writes (113.1 vs 112.2 M/s).
        </Takeaway>
      )}

      {/* Cue 4: Bottom Causal Bridge to Exp 1 */}
      {kNextQ > 0.01 && (
        <NextQuestion x={60} y={802} w={1800} k={kNextQ} size={23}>
          Return traffic is innocent. Give pure one-way traffic <strong>2 receive cores</strong> — yet forward still
          stalls at <strong style={{ color: K.knob }}>128.7 M/s</strong>. What holds the host back?
        </NextQuestion>
      )}
    </>
  );
}

export const S02: SceneDef = {
  id: "S02",
  title: "Sanity check · Return traffic vs forward",
  stage: 0,
  source: "Forward vs bidirectional · host pinned 3.9 GHz · 21 verified runs",
  dur: DUR,
  cues,
  C: Body,
};
