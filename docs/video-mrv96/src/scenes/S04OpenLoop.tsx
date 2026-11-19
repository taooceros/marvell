import React from "react";
import {
  Arrow,
  Axes,
  C,
  Card,
  Cue,
  FONT,
  K,
  Packet,
  PacketStream,
  SceneDef,
  Series,
  Stage,
  clamp01,
  fade,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { BarChart, HBarChart, KnobBar, NextQuestion } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 14.26, "**Finding 3:** Can the PCIe link sustain high **bidirectional** traffic without lockstep? Fixed: **1 host core + 1 card core**, WC replies. Knob: **lockstep vs open-loop duplex**.", "Finding three: can the link sustain high bidirectional traffic without lockstep? Fixed: one host core, one card core, write-combining replies. One knob: closed-loop lockstep versus independent open-loop duplex."], // 0
  [14.61, 26.51, "**Wall disappears:** Lockstep (`W = 256`) stalls at **14.2 M/s**. Open loop delivers **5–35 M/s each way** (`70 M/s` combined) at **~1 µs** median (`0.97 µs` fwd, `1.24 µs` ret).", "The wall disappears. Lockstep stalls at fourteen point two million, whereas open loop delivers five to thirty-five million each way, seventy million combined, at about one microsecond median."], // 1
  [26.86, 40.66, "**Peak capacity (1 host core):** Unthrottled `1 card core` hits **41.9 + 41.9 = 83.7 M/s**. Splitting card RX & TX onto **2 cores** drives **91.0 M/s** (WC) and **97.3 M/s** (`49.3 + 47.9`, DMA).", "Unthrottled, one host core and one card core hit eighty-three point seven million combined. Splitting card receive and send onto two cores drives ninety-one million with write combining, and ninety-seven point three with D M A."], // 2
  [41.01, 56.41, "**Honest caveat — tails:** At `35 + 35 M/s`, forward **p99 spikes to 38.7 µs** (`p90 = 5.55 µs`; at `20 + 20`, even `p50` hits **16.9 µs**).", "Honest caveat: those one microsecond figures are medians. At thirty-five plus thirty-five, ninety-ninth percentile forward latency spikes to thirty-eight point seven microseconds, and at twenty plus twenty even the median hits sixteen point nine."], // 3
  [56.76, 70.24, "**Echo cliff:** With request-triggered replies, RTT **p99 jumps** from **1.26 µs** (`10 M/s`) to **35.4 µs** at **13.5 M/s**. The link can fly — sharpening our one open puzzle.", "With request-triggered replies, ninety-ninth percentile round trip jumps from one point two six microseconds at ten million to thirty-five point four at thirteen point five. The link can fly, which sharpens our one open puzzle."], // 4
];
const DUR = 71.64; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  const barK = prog(t, T[0], 0.45);
  const knobActive = prog(t, T[1], 0.45) + prog(t, T[2], 0.45) + prog(t, T[3], 0.45);

  // Clean non-overlapping phase crossfades
  const p0 = fade(t, T[0], T[1] - 0.15, 0.4);
  const p1 = fade(t, T[1] + 0.25, T[2] - 0.15, 0.4);
  const p2 = fade(t, T[2] + 0.25, T[3] - 0.15, 0.4);
  const p34 = prog(t, T[3] + 0.25, 0.45);
  const p3Only = fade(t, T[3] + 0.25, T[4] - 0.15, 0.4);
  const p4 = prog(t, T[4] + 0.25, 0.45);

  // Cue 0 ambient animations
  const lockCycle = (rt * 0.42) % 1;
  const lockFwdK = clamp01(lockCycle / 0.36);
  const lockRetK = clamp01((lockCycle - 0.48) / 0.36);
  const lockBlocked = lockCycle > 0.04 && lockCycle < 0.86;
  const streamTravel = rt * 220;

  // Cue 4 Echo RTT p99 scales
  const ex0 = 1075;
  const ex1 = 1805;
  const ey0 = 644;
  const ey1 = 310;
  const esx = linScale(0, 22, ex0, ex1);
  const esy = linScale(0, 44, ey0, ey1);

  const echoFlatK = prog(t, T[4] + 0.6, 0.9);
  const echoCliffK = prog(t, T[4] + 1.8, 1.1);
  const bridgeK = prog(t, T[4] + 3.2, 0.55);

  return (
    <>
      {/* Top KnobBar persists across all 5 cues */}
      <KnobBar
        y={118}
        k={barK}
        fixed={["1 host core", "WC replies"]}
        knob="Traffic mode"
        options={["Lockstep", "Open duplex", "Unthrottled", "Tails & Echo"]}
        active={knobActive}
        size={24}
      />

      {/* ======================= CUE 0 HTML: Lockstep vs Open-Loop Duplex ======================= */}
      {p0 > 0.005 && (
        <>
          <Card
            x={60}
            y={204}
            w={880}
            h={652}
            border={K.slow}
            title="Closed-Loop Lockstep"
            titleColor={K.slow}
            style={{ opacity: p0 }}
          >
            <div style={{ fontSize: 24, color: C.muted, fontFamily: FONT }}>
              Host waits for returning reply before releasing the next slot
            </div>
            <div
              style={{
                position: "absolute",
                left: 24,
                right: 24,
                bottom: 24,
                background: "rgba(249,65,68,0.12)",
                border: `2px solid ${K.slow}88`,
                borderRadius: 14,
                padding: "16px 20px",
                fontFamily: FONT,
                fontSize: 24,
                color: C.text,
                lineHeight: 1.35,
              }}
            >
              <span style={{ color: K.slow, fontWeight: 800 }}>Coupled to round-trip loop:</span>{" "}
              any return-path or queueing stall immediately throttles forward sends.
            </div>
          </Card>

          <Card
            x={980}
            y={204}
            w={880}
            h={652}
            border={K.fast}
            title="Open-Loop Duplex"
            titleColor={K.fast}
            style={{ opacity: p0 }}
          >
            <div style={{ fontSize: 24, color: C.muted, fontFamily: FONT }}>
              Independent paced streams in both directions at rate R
            </div>
            <div
              style={{
                position: "absolute",
                left: 24,
                right: 24,
                bottom: 24,
                background: "rgba(67,197,158,0.12)",
                border: `2px solid ${K.fast}88`,
                borderRadius: 14,
                padding: "16px 20px",
                fontFamily: FONT,
                fontSize: 24,
                color: C.text,
                lineHeight: 1.35,
              }}
            >
              <span style={{ color: K.fast, fontWeight: 800 }}>Decoupled streams:</span>{" "}
              measures raw bidirectional PCIe + core throughput without a wait barrier.
            </div>
          </Card>
        </>
      )}

      {/* ======================= CUE 1 HTML: Wall Disappears ======================= */}
      {p1 > 0.005 && (
        <>
          <Card
            x={60}
            y={204}
            w={1130}
            h={656}
            border={C.line}
            title="Combined Bidirectional Rate (1 host + 1 card core)"
            titleColor={K.fwd}
            style={{ opacity: p1 }}
          />

          <Card
            x={1220}
            y={204}
            w={640}
            h={656}
            border={K.fast}
            title="Median Latency at 35 + 35 M/s"
            titleColor={K.fast}
            style={{ opacity: p1 }}
          >
            <div
              style={{
                marginTop: 22,
                textAlign: "center",
                background: "rgba(67,197,158,0.12)",
                border: `2px solid ${K.fast}88`,
                borderRadius: 16,
                padding: "22px 20px",
                fontFamily: FONT,
              }}
            >
              <div
                style={{
                  fontSize: 82,
                  fontWeight: 900,
                  color: K.fast,
                  lineHeight: 1.05,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                ~1 µs
              </div>
              <div style={{ fontSize: 25, fontWeight: 700, color: C.text, marginTop: 8 }}>
                median (p50) at 70 M/s combined
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 28 }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  background: "rgba(76,201,240,0.10)",
                  border: `2px solid ${K.fwd}66`,
                  borderRadius: 14,
                  padding: "18px 24px",
                  fontFamily: FONT,
                }}
              >
                <div>
                  <div style={{ fontSize: 25, fontWeight: 800, color: K.fwd }}>Forward (Host → Card)</div>
                  <div style={{ fontSize: 21, color: C.muted }}>35 M/s paced stream · p50</div>
                </div>
                <div
                  style={{
                    fontSize: 44,
                    fontWeight: 900,
                    color: K.fwd,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  0.97 µs
                </div>
              </div>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  background: "rgba(67,197,158,0.10)",
                  border: `2px solid ${K.ret}66`,
                  borderRadius: 14,
                  padding: "18px 24px",
                  fontFamily: FONT,
                }}
              >
                <div>
                  <div style={{ fontSize: 25, fontWeight: 800, color: K.ret }}>Return (Card → Host)</div>
                  <div style={{ fontSize: 21, color: C.muted }}>35 M/s WC stream · p50</div>
                </div>
                <div
                  style={{
                    fontSize: 44,
                    fontWeight: 900,
                    color: K.ret,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  1.24 µs
                </div>
              </div>
            </div>

            <div
              style={{
                position: "absolute",
                left: 24,
                right: 24,
                bottom: 22,
                fontSize: 23,
                color: C.muted,
                textAlign: "center",
                fontFamily: FONT,
              }}
            >
              5, 10, 20, and 35 M/s each way all meet target cleanly
            </div>
          </Card>
        </>
      )}

      {/* ======================= CUE 2 HTML: Peak Capacity ======================= */}
      {p2 > 0.005 && (
        <Card
          x={60}
          y={204}
          w={1800}
          h={656}
          border={C.line}
          title="Peak Combined Bidirectional Capacity (1 Host Core)"
          titleColor={K.knob}
          style={{ opacity: p2 }}
        >
          <div style={{ fontSize: 23, color: C.muted, fontFamily: FONT }}>
            Removing lockstep unlocks up to 97.3 M/s bidirectional traffic from a single host core
          </div>
        </Card>
      )}

      {/* ======================= CUES 3 & 4 HTML: Tails + Echo Cliff ======================= */}
      {p34 > 0.005 && (
        <>
          {/* Left Panel persists across Cues 3 & 4 */}
          <Card
            x={60}
            y={204}
            w={875}
            h={564}
            border={K.slow}
            title="Honest Caveat: 35 + 35 M/s Forward Latency"
            titleColor={K.slow}
            style={{ opacity: p34 }}
          >
            <div
              style={{
                position: "absolute",
                left: 24,
                right: 24,
                bottom: 20,
                background: "rgba(249,65,68,0.12)",
                border: `2px solid ${K.slow}66`,
                borderRadius: 12,
                padding: "12px 18px",
                fontFamily: FONT,
                fontSize: 23,
                color: C.text,
                lineHeight: 1.35,
              }}
            >
              Return <span style={{ color: K.slow, fontWeight: 800 }}>p99 = 13.9 µs</span> · At{" "}
              <span style={{ color: K.knob, fontWeight: 800 }}>20 + 20 M/s</span>, even forward{" "}
              <span style={{ color: K.slow, fontWeight: 800 }}>p50 = 16.9 µs</span>.
            </div>
          </Card>

          {/* Cue 3 Right Panel: Why Medians Hide Spikes */}
          {p3Only > 0.005 && (
            <Card
              x={965}
              y={204}
              w={895}
              h={564}
              border={K.knob}
              title="Why Medians Alone Hide Queueing Spikes"
              titleColor={K.knob}
              style={{ opacity: p3Only }}
            >
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 20,
                  marginTop: 14,
                  fontFamily: FONT,
                }}
              >
                <div
                  style={{
                    background: "rgba(11,16,32,0.7)",
                    border: `2px solid ${K.slow}66`,
                    borderLeft: `8px solid ${K.slow}`,
                    borderRadius: 14,
                    padding: "18px 22px",
                  }}
                >
                  <div style={{ fontSize: 24, fontWeight: 800, color: K.slow, marginBottom: 6 }}>
                    Wide tail spread at 35 + 35 M/s
                  </div>
                  <div style={{ fontSize: 24, color: C.text, lineHeight: 1.38 }}>
                    Median forward latency stays <span style={{ color: K.fast, fontWeight: 800 }}>0.97 µs</span>, yet{" "}
                    <span style={{ color: K.knob, fontWeight: 800 }}>p90 = 5.55 µs</span> and{" "}
                    <span style={{ color: K.slow, fontWeight: 800 }}>p99 spikes to 38.7 µs</span>.
                  </div>
                </div>

                <div
                  style={{
                    background: "rgba(11,16,32,0.7)",
                    border: `2px solid ${K.knob}66`,
                    borderLeft: `8px solid ${K.knob}`,
                    borderRadius: 14,
                    padding: "18px 22px",
                  }}
                >
                  <div style={{ fontSize: 24, fontWeight: 800, color: K.knob, marginBottom: 6 }}>
                    Non-monotonic interference at 20 + 20 M/s
                  </div>
                  <div style={{ fontSize: 24, color: C.text, lineHeight: 1.38 }}>
                    Even at lower rate, forward median <span style={{ color: K.slow, fontWeight: 800 }}>p50 climbs to 16.9 µs</span> — transient bursts still occur under duplex load.
                  </div>
                </div>

                <div
                  style={{
                    background: "rgba(76,201,240,0.10)",
                    border: `2px solid ${K.fwd}66`,
                    borderRadius: 14,
                    padding: "16px 22px",
                    fontSize: 24,
                    color: C.text,
                  }}
                >
                  <span style={{ color: K.fwd, fontWeight: 800 }}>Next check →</span> What happens when replies are triggered by incoming requests (open-loop Echo)?
                </div>
              </div>
            </Card>
          )}

          {/* Cue 4 Right Panel: Echo RTT p99 Cliff */}
          {p4 > 0.005 && (
            <Card
              x={965}
              y={204}
              w={895}
              h={564}
              border={K.slow}
              title="Open-Loop Echo: Request–Response RTT p99"
              titleColor={K.slow}
              style={{ opacity: p4 }}
            />
          )}
        </>
      )}

      {/* ======================= SVG STAGE ======================= */}
      <Stage>
        {/* ---------- CUE 0 SVG: Lockstep vs Open-Loop Duplex Diagrams ---------- */}
        {p0 > 0.005 && (
          <g opacity={p0} fontFamily={FONT}>
            {/* Left Diagram: Closed-Loop Lockstep */}
            <rect
              x={108}
              y={356}
              width={180}
              height={252}
              rx={14}
              fill="rgba(124,147,214,0.16)"
              stroke={K.host}
              strokeWidth={3}
            />
            <text x={198} y={468} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.host}>
              1 Host Core
            </text>
            <text x={198} y={502} textAnchor="middle" fontSize={22} fill={C.muted}>
              MOVDIR64B
            </text>

            <rect
              x={712}
              y={356}
              width={180}
              height={252}
              rx={14}
              fill="rgba(248,150,30,0.16)"
              stroke={K.card}
              strokeWidth={3}
            />
            <text x={802} y={468} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.card}>
              1 Card Core
            </text>
            <text x={802} y={502} textAnchor="middle" fontSize={22} fill={C.muted}>
              WC replies
            </text>

            {/* Forward & return links */}
            <Arrow x1={296} y1={420} x2={704} y2={420} color={K.fwd} w={4.5} head={13} />
            <text x={500} y={398} textAnchor="middle" fontSize={22} fontWeight={700} fill={K.fwd}>
              Request (Host → Card)
            </text>

            <Arrow x1={704} y1={544} x2={296} y2={544} color={K.ret} w={4.5} head={13} />
            <text x={500} y={584} textAnchor="middle" fontSize={22} fontWeight={700} fill={K.ret}>
              Reply (Card → Host)
            </text>

            {/* Red wait barrier on Host send port */}
            <line
              x1={314}
              y1={368}
              x2={314}
              y2={472}
              stroke={K.slow}
              strokeWidth={5}
              strokeDasharray="10 6"
              opacity={lockBlocked ? 1 : 0.35}
            />
            <rect
              x={376}
              y={462}
              width={248}
              height={42}
              rx={10}
              fill={lockBlocked ? "rgba(249,65,68,0.24)" : "rgba(249,65,68,0.10)"}
              stroke={K.slow}
              strokeWidth={2}
            />
            <text x={500} y={490} textAnchor="middle" fontSize={21} fontWeight={800} fill={K.slow}>
              WAIT BARRIER
            </text>

            {/* Single packet bouncing back and forth in lockstep */}
            {lockCycle < 0.38 && (
              <Packet
                pts={[
                  [296, 420],
                  [704, 420],
                ]}
                k={lockFwdK}
                color={K.fwd}
                r={10}
              />
            )}
            {lockCycle >= 0.46 && lockCycle < 0.86 && (
              <Packet
                pts={[
                  [704, 544],
                  [296, 544],
                ]}
                k={lockRetK}
                color={K.ret}
                r={10}
              />
            )}

            {/* Right Diagram: Open-Loop Duplex */}
            <rect
              x={1028}
              y={356}
              width={180}
              height={252}
              rx={14}
              fill="rgba(124,147,214,0.16)"
              stroke={K.host}
              strokeWidth={3}
            />
            <text x={1118} y={468} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.host}>
              1 Host Core
            </text>
            <text x={1118} y={502} textAnchor="middle" fontSize={22} fill={C.muted}>
              paces at R
            </text>

            <rect
              x={1632}
              y={356}
              width={180}
              height={252}
              rx={14}
              fill="rgba(248,150,30,0.16)"
              stroke={K.card}
              strokeWidth={3}
            />
            <text x={1722} y={468} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.card}>
              1 Card Core
            </text>
            <text x={1722} y={502} textAnchor="middle" fontSize={22} fill={C.muted}>
              paces at R
            </text>

            {/* Counter-flowing continuous packet streams */}
            <Arrow x1={1216} y1={420} x2={1624} y2={420} color={K.fwd} w={3.5} head={12} opacity={0.5} />
            <PacketStream
              pts={[
                [1220, 420],
                [1618, 420],
              ]}
              travel={streamTravel}
              gap={54}
              size={14}
              color={K.fwd}
            />
            <text x={1420} y={396} textAnchor="middle" fontSize={22} fontWeight={800} fill={K.fwd}>
              Host → Card (rate R)
            </text>

            <Arrow x1={1624} y1={544} x2={1216} y2={544} color={K.ret} w={3.5} head={12} opacity={0.5} />
            <PacketStream
              pts={[
                [1618, 544],
                [1220, 544],
              ]}
              travel={streamTravel}
              gap={54}
              size={14}
              color={K.ret}
            />
            <text x={1420} y={586} textAnchor="middle" fontSize={22} fontWeight={800} fill={K.ret}>
              Card → Host (rate R)
            </text>

            <rect
              x={1272}
              y={462}
              width={296}
              height={42}
              rx={10}
              fill="rgba(67,197,158,0.18)"
              stroke={K.fast}
              strokeWidth={2}
            />
            <text x={1420} y={490} textAnchor="middle" fontSize={21} fontWeight={800} fill={K.fast}>
              INDEPENDENT DUPLEX
            </text>
          </g>
        )}

        {/* ---------- CUE 1 SVG: Lockstep Wall vs Open-Loop 5..35+35 M/s ---------- */}
        {p1 > 0.005 && (
          <g opacity={p1} fontFamily={FONT}>
            <BarChart
              x0={96}
              x1={1156}
              y1={320}
              y0={734}
              max={82}
              barW={122}
              size={26}
              labelSize={24}
              k={(i) => prog(t, T[1] + 0.35 + i * 0.32, 0.55)}
              bars={[
                { label: "Lockstep", sub: "W = 256", value: 14.2, text: "14.2 M/s", color: K.slow },
                { label: "5 + 5", sub: "open", value: 10, text: "10 M/s", color: K.fwd },
                { label: "10 + 10", sub: "open", value: 20, text: "20 M/s", color: K.fwd },
                { label: "20 + 20", sub: "open", value: 40, text: "40 M/s", color: K.fwd },
                { label: "35 + 35", sub: "open", value: 70, text: "70 M/s", color: K.fast },
              ]}
            />

            {/* Dashed ~13 M/s lockstep wall reference line + non-overlapping callout */}
            <line
              x1={96}
              y1={734 - (414 * 13) / 82}
              x2={1156}
              y2={734 - (414 * 13) / 82}
              stroke={K.slow}
              strokeWidth={2.5}
              strokeDasharray="10 7"
              opacity={0.75}
            />
            <rect
              x={116}
              y={488}
              width={258}
              height={42}
              rx={10}
              fill="rgba(249,65,68,0.16)"
              stroke={K.slow}
              strokeWidth={2}
            />
            <text x={245} y={516} textAnchor="middle" fontSize={22} fontWeight={800} fill={K.slow}>
              ~13 M/s lockstep wall
            </text>
            <Arrow x1={276} y1={532} x2={310} y2={658} color={K.slow} w={3} head={10} />
          </g>
        )}

        {/* ---------- CUE 2 SVG: Peak Bidirectional HBarChart ---------- */}
        {p2 > 0.005 && (
          <g opacity={p2} fontFamily={FONT}>
            <HBarChart
              x0={500}
              x1={1665}
              y1={362}
              y0={836}
              max={105}
              barH={52}
              size={28}
              labelSize={25}
              refLine={{ value: 14.2, label: "Lockstep (14.2 M/s)", color: K.slow }}
              k={(i) => prog(t, T[2] + 0.35 + i * 0.4, 0.6)}
              bars={[
                {
                  label: "Lockstep (W = 256)",
                  sub: "1 card core",
                  value: 14.2,
                  text: "14.2 M/s",
                  color: K.slow,
                },
                {
                  label: "Open duplex 35 + 35",
                  sub: "1 card core",
                  value: 70.0,
                  text: "70 M/s",
                  color: K.fwd,
                },
                {
                  label: "Unthrottled (1 card core)",
                  sub: "41.9 + 41.9",
                  value: 83.7,
                  text: "83.7 M/s",
                  color: K.fast,
                },
                {
                  label: "2 card cores split (WC)",
                  sub: "45.5 + 45.5",
                  value: 91.0,
                  text: "91.0 M/s",
                  color: K.fast,
                },
                {
                  label: "2 card cores split (DMA)",
                  sub: "49.3 + 47.9",
                  value: 97.3,
                  text: "97.3 M/s",
                  color: K.knob,
                },
              ]}
            />
          </g>
        )}

        {/* ---------- CUES 3 & 4 LEFT CHART: 35 + 35 M/s Forward Tail Bars ---------- */}
        {p34 > 0.005 && (
          <g opacity={p34} fontFamily={FONT}>
            <BarChart
              x0={110}
              x1={885}
              y1={314}
              y0={600}
              max={45}
              barW={132}
              size={27}
              labelSize={25}
              k={(i) => prog(t, T[3] + 0.4 + i * 0.45, 0.55)}
              bars={[
                { label: "p50", sub: "median", value: 0.97, text: "0.97 µs", color: K.fast },
                { label: "p90", sub: "tail begins", value: 5.55, text: "5.55 µs", color: K.knob },
                { label: "p99", sub: "spike!", value: 38.7, text: "38.7 µs", color: K.slow },
              ]}
            />
          </g>
        )}

        {/* ---------- CUE 4 RIGHT CHART: Echo RTT p99 Cliff ---------- */}
        {p4 > 0.005 && (
          <g opacity={p4} fontFamily={FONT}>
            {/* Cliff highlight band between 10 and 13.5 M/s */}
            <rect
              x={esx(10)}
              y={ey1}
              width={esx(13.5) - esx(10)}
              height={ey0 - ey1}
              fill="rgba(249,65,68,0.13)"
              opacity={echoCliffK}
            />
            <g opacity={echoCliffK}>
              <rect
                x={1554}
                y={462}
                width={236}
                height={66}
                rx={12}
                fill="rgba(249,65,68,0.16)"
                stroke={K.slow}
                strokeWidth={2}
              />
              <text x={1672} y={491} textAnchor="middle" fontSize={22} fontWeight={900} fill={K.slow}>
                SHARP CLIFF
              </text>
              <text x={1672} y={517} textAnchor="middle" fontSize={19} fill={C.text}>
                10 → 13.5 M/s
              </text>
              <Arrow x1={1550} y1={495} x2={1494} y2={495} color={K.slow} w={3} head={10} />
            </g>

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
              xlabel="Offered rate (M/s)"
              ylabel="Echo RTT p99 (µs)"
              size={22}
            />

            {/* Green low-latency segment: 5 -> 10 M/s */}
            <Series
              pts={[
                [5, 1.03],
                [10, 1.26],
              ]}
              sx={esx}
              sy={esy}
              k={echoFlatK}
              color={K.fast}
              w={5.5}
              r={8}
            />

            {/* Red cliff segment: 10 -> 13.5 -> 20 M/s */}
            <Series
              pts={[
                [10, 1.26],
                [13.5, 35.4],
                [20, 38.5],
              ]}
              sx={esx}
              sy={esy}
              k={echoCliffK}
              color={K.slow}
              w={5.5}
              r={8}
            />

            {/* Point value annotations */}
            <g opacity={echoFlatK}>
              <text
                x={esx(5)}
                y={esy(1.03) - 18}
                textAnchor="middle"
                fontSize={23}
                fontWeight={800}
                fill={K.fast}
              >
                1.03 µs
              </text>
              <text
                x={esx(10) - 14}
                y={esy(1.26) - 18}
                textAnchor="end"
                fontSize={23}
                fontWeight={800}
                fill={K.fast}
              >
                1.26 µs
              </text>
            </g>

            <g opacity={echoCliffK}>
              <text
                x={esx(13.5) - 14}
                y={esy(35.4) - 16}
                textAnchor="end"
                fontSize={24}
                fontWeight={900}
                fill={K.slow}
              >
                35.4 µs
              </text>
              <text
                x={esx(20)}
                y={esy(38.5) - 18}
                textAnchor="middle"
                fontSize={24}
                fontWeight={900}
                fill={K.slow}
              >
                38.5 µs
              </text>
            </g>
          </g>
        )}
      </Stage>

      {/* ======================= BOTTOM BANNERS (Cues 3 & 4) ======================= */}
      {p3Only > 0.005 && (
        <NextQuestion
          x={60}
          y={792}
          w={1800}
          k={p3Only}
          color={K.knob}
          label="HONEST CAVEAT"
          size={26}
        >
          Open-loop duplex proves raw link headroom, but p99 bursts (38.7 µs fwd, 13.9 µs ret) reveal hidden queueing.
        </NextQuestion>
      )}

      {p4 > 0.005 && (
        <NextQuestion
          x={60}
          y={792}
          w={1800}
          k={bridgeK * p4}
          color={K.slow}
          label="NEXT · OPEN PUZZLE →"
          size={26}
        >
          The link itself can fly (91–97 M/s) — so WHY does 4-core closed-loop collapse to ~13 M/s?
        </NextQuestion>
      )}
    </>
  );
}

export const S04: SceneDef = {
  id: "S04",
  title: "Nailed down #3 · Open-loop breaks lockstep",
  stage: 2,
  source: "Lockstep vs open-loop duplex · 1 host core · 483 verified runs",
  dur: DUR,
  cues,
  C: Body,
};
