import React from "react";
import {
  Arrow,
  C,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  Packet,
  SceneDef,
  Stage,
  clamp01,
  fade,
  prog,
  useRealT,
  useT,
} from "../lib";
import { BarChart, KnobBar, Takeaway } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 13.62, "**Finding 2:** In our **897 ns** `W = 1` baseline, are all 3 cache/DDIO shortcuts already active? We flush **one cache level at a time** and check latency plus PMU counters.", "Finding two: in our eight hundred ninety-seven nanosecond baseline, are all three cache and D D I O shortcuts already active? We flush one cache level at a time and check latency plus hardware counters."], // 0
  [13.97, 23.21, "**Shortcut 1 (Host L1D source):** Flushing the host source line to cold DRAM before `MOVDIR64B` adds **+117.3 ns** end-to-end (`897.3 → 1,014.5 ns`).", "First, flushing the host source buffer to cold D RAM before sending adds one hundred seventeen point three nanoseconds to end-to-end round-trip latency."], // 1
  [23.56, 36.74, "**Shortcut 2 (Card L3 write-allocate):** Flushing the card slot before arrival keeps card read flat (`104 → 102 ns`) while `tad_alloc_ltg` jumps **+1.00/msg**; flushing after arrival to DRAM costs **+75.0 ns**.", "Second, flushing the card slot before arrival keeps card read flat while L 3 allocations jump by one per message. Flushing after arrival to card D RAM adds seventy-five nanoseconds on the read side."], // 2
  [37.09, 52.33, "**Shortcut 3 (Host Xeon DDIO):** Hot replies hit `io_hit_itom = 1.00` into host LLC. Flushing before arrival stays flat (`42.7 ns`); flushing after arrival to host DRAM costs **+87.3 ns**.", "Third, on the return path, counters confirm one hundred percent host D D I O cache hits. Flushing before arrival stays flat at forty-two point seven nanoseconds, while forcing a host D RAM read adds eighty-seven point three."], // 3
  [52.68, 75.92, "**64 B card coherence:** Linux reports a **128-byte** card L3 line. Sweeping spacing `d` between 2 card cores: **0 B** thrashes at **72.3 M/s** (`17.8 M/s` snoops), while **64 B** immediately hits **259.8 M/s** (`0.52 M/s` idle snoops) — same as `128 B–4 KiB`.", "Finally, Linux reports a one hundred twenty-eight byte card L 3 line. Sweeping slot spacing between two card cores: zero bytes thrashes at seventy-two point three million with heavy snoops, while sixty-four bytes immediately hits full two hundred fifty-nine point eight million with idle snoops, identical to four kilobytes. Card coherence is strictly sixty-four bytes."], // 4
];
const DUR = 77.32; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Part A (Cues 0-3): 3 Cache/DDIO shortcuts in 897 ns W = 1 baseline
  const partA = fade(t, T[0], T[4] - 0.2, 0.4);
  const knobA = prog(t, T[1], 0.45) + prog(t, T[2], 0.45) + prog(t, T[3], 0.45);

  const cue0Card = fade(t, T[0] + 0.15, T[1] - 0.2, 0.35);
  const p1 = prog(t, T[1] + 0.1, 0.5) * partA;
  const p2 = prog(t, T[2] + 0.1, 0.5) * partA;
  const p3 = prog(t, T[3] + 0.1, 0.5) * partA;

  // Which checkpoint is actively highlighted in the top schematic
  const hl1 = fade(t, T[1], T[2] - 0.15, 0.35);
  const hl2 = fade(t, T[2], T[3] - 0.15, 0.35);
  const hl3 = fade(t, T[3], T[4] - 0.2, 0.35);

  // Ambient packet orbiting the 897 ns loop (forward top, card drop, return bottom)
  const loopPhase = (rt * 0.42) % 1;
  const fwdPts: [number, number][] = [
    [686, 250],
    [1234, 250],
  ];
  const retPts: [number, number][] = [
    [1234, 344],
    [686, 344],
  ];
  const fwdK = clamp01(loopPhase / 0.44);
  const retK = clamp01((loopPhase - 0.52) / 0.44);

  // Part B (Cue 4): 64 B card core-to-core L3 coherence
  const partB = prog(t, T[4] + 0.25, 0.45);
  const knobB = prog(t, T[4] + 7.2, 0.55); // smoothly steps 0 B -> 64 B
  const b0K = prog(t, T[4] + 1.2, 0.55);
  const b1K = prog(t, T[4] + 7.0, 0.55);
  const bRestK = prog(t, T[4] + 9.2, 0.55);
  const verdictK = prog(t, T[4] + 10.5, 0.5);

  // Panels grid appears once Cue 1 starts
  const panelsGrid = prog(t, T[1] + 0.05, 0.4) * partA;

  return (
    <>
      {/* ======================= PART A HTML BACKGROUNDS (Cues 0–3) ======================= */}
      {partA > 0.005 && (
        <>
          <KnobBar
            y={118}
            k={partA}
            fixed={["W = 1 (897 ns)", "Host 3.9 GHz", "100% PMU active"]}
            knob="Flush level"
            options={["Baseline", "Host L1D", "Card L3", "Host DDIO"]}
            active={knobA}
            size={23}
          />

          {/* Cue 0 overview card before the 3 proof panels open */}
          {cue0Card > 0.005 && (
            <Card
              x={60}
              y={416}
              w={1800}
              h={452}
              border={C.line}
              title="Single-Variable Flush Protocol · 897 ns Baseline (40 runs)"
              titleColor={K.knob}
              style={{ opacity: cue0Card * partA }}
            >
              <div
                style={{
                  fontSize: 26,
                  color: C.muted,
                  marginBottom: 24,
                  fontFamily: FONT,
                }}
              >
                Question: Are all three cache and DDIO shortcuts already active in our{" "}
                <span style={{ color: C.text, fontWeight: 700 }}>897 ns</span> round trip? We flush{" "}
                <span style={{ color: K.knob, fontWeight: 700 }}>one cache level at a time</span> and measure latency + PMU counters:
              </div>
              <div style={{ display: "flex", gap: 28, marginTop: 14 }}>
                {[
                  {
                    num: "1",
                    color: K.host,
                    title: "Host L1D Source",
                    body: "Flush host source buffer to cold DRAM before MOVDIR64B and measure end-to-end RTT penalty.",
                  },
                  {
                    num: "2",
                    color: K.fwd,
                    title: "Card L3 Write-Allocate",
                    body: "Flush card ring slot before vs after PCIe write; verify read latency and tad_alloc_ltg counter.",
                  },
                  {
                    num: "3",
                    color: K.ret,
                    title: "Host Xeon DDIO LLC",
                    body: "Flush host reply slot before vs after card WC write; verify read latency and io_hit_itom counter.",
                  },
                ].map((item) => (
                  <div
                    key={item.num}
                    style={{
                      flex: 1,
                      background: "rgba(11,16,32,0.7)",
                      border: `2px solid ${item.color}66`,
                      borderTop: `6px solid ${item.color}`,
                      borderRadius: 14,
                      padding: "20px 22px",
                      fontFamily: FONT,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 12,
                        marginBottom: 12,
                      }}
                    >
                      <span
                        style={{
                          width: 36,
                          height: 36,
                          borderRadius: 999,
                          background: item.color,
                          color: C.bg,
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontSize: 22,
                          fontWeight: 900,
                        }}
                      >
                        {item.num}
                      </span>
                      <span style={{ fontSize: 26, fontWeight: 800, color: item.color }}>
                        {item.title}
                      </span>
                    </div>
                    <div style={{ fontSize: 24, color: C.text, lineHeight: 1.4 }}>{item.body}</div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Cues 1–3 Three side-by-side Proof Panel backgrounds */}
          {panelsGrid > 0.005 && (
            <>
              {/* Panel 1: Host L1D source */}
              <Card
                x={60}
                y={408}
                w={576}
                h={464}
                border={hl1 > 0.5 ? K.host : `${K.host}88`}
                title="1 · Host L1D Source (RTT)"
                titleColor={K.host}
                style={{
                  opacity: panelsGrid * (0.25 + 0.75 * p1),
                  boxShadow: hl1 > 0.3 ? `0 0 28px ${K.host}33` : "none",
                }}
              >
                <div style={{ fontSize: 22, color: C.muted, fontFamily: FONT }}>
                  Flush source line before MOVDIR64B
                </div>
                <div
                  style={{
                    position: "absolute",
                    left: 24,
                    right: 24,
                    bottom: 20,
                    background: "rgba(249,65,68,0.14)",
                    border: `2px solid ${K.slow}`,
                    borderRadius: 12,
                    padding: "10px 14px",
                    textAlign: "center",
                    fontFamily: FONT,
                    opacity: prog(t, T[1] + 1.4, 0.45),
                  }}
                >
                  <span style={{ fontSize: 26, fontWeight: 900, color: K.slow }}>+117.3 ns RTT</span>
                  <span style={{ fontSize: 22, color: C.text, marginLeft: 10 }}>
                    (897.3 → 1,014.5 ns)
                  </span>
                </div>
              </Card>

              {/* Panel 2: Card L3 write-allocate */}
              <Card
                x={672}
                y={408}
                w={576}
                h={464}
                border={hl2 > 0.5 ? K.fwd : p2 > 0.5 ? `${K.fwd}88` : C.line}
                title="2 · Card L3 Write-Alloc (Read)"
                titleColor={K.fwd}
                style={{
                  opacity: panelsGrid * (0.22 + 0.78 * p2),
                  boxShadow: hl2 > 0.3 ? `0 0 28px ${K.fwd}33` : "none",
                }}
              >
                <div style={{ fontSize: 22, color: C.muted, fontFamily: FONT }}>
                  Pre-write flush flat; post-write hits DRAM
                </div>
                <div
                  style={{
                    position: "absolute",
                    left: 20,
                    right: 20,
                    bottom: 20,
                    background: "rgba(76,201,240,0.13)",
                    border: `2px solid ${K.fwd}`,
                    borderRadius: 12,
                    padding: "10px 12px",
                    textAlign: "center",
                    fontFamily: MONO,
                    fontSize: 20,
                    fontWeight: 800,
                    color: K.fwd,
                    opacity: prog(t, T[2] + 1.6, 0.45),
                    whiteSpace: "nowrap",
                  }}
                >
                  tad_alloc_ltg: +1.00/msg (0.11 → 1.11)
                </div>
              </Card>

              {/* Panel 3: Host Xeon DDIO */}
              <Card
                x={1284}
                y={408}
                w={576}
                h={464}
                border={hl3 > 0.5 ? K.ret : p3 > 0.5 ? `${K.ret}88` : C.line}
                title="3 · Host Xeon DDIO (Read)"
                titleColor={K.ret}
                style={{
                  opacity: panelsGrid * (0.22 + 0.78 * p3),
                  boxShadow: hl3 > 0.3 ? `0 0 28px ${K.ret}33` : "none",
                }}
              >
                <div style={{ fontSize: 22, color: C.muted, fontFamily: FONT }}>
                  Pre-write flush flat (0.0 ns); DRAM +87.3 ns
                </div>
                <div
                  style={{
                    position: "absolute",
                    left: 20,
                    right: 20,
                    bottom: 20,
                    background: "rgba(67,197,158,0.14)",
                    border: `2px solid ${K.ret}`,
                    borderRadius: 12,
                    padding: "10px 12px",
                    textAlign: "center",
                    fontFamily: MONO,
                    fontSize: 21,
                    fontWeight: 800,
                    color: K.ret,
                    opacity: prog(t, T[3] + 1.4, 0.45),
                    whiteSpace: "nowrap",
                  }}
                >
                  io_hit_itom = 1.00 (100% DDIO hit)
                </div>
              </Card>
            </>
          )}
        </>
      )}

      {/* ======================= PART B HTML BACKGROUNDS (Cue 4) ======================= */}
      {partB > 0.005 && (
        <>
          <KnobBar
            y={118}
            k={partB}
            fixed={["2 card cores", "Own 64 B slot each", "Linux reports 128 B"]}
            knob="Slot spacing d"
            options={["0 B", "64 B", "128 B", "256 B", "4 KiB"]}
            active={knobB}
            size={23}
          />

          <Card
            x={60}
            y={200}
            w={885}
            h={546}
            border={C.line}
            title="Writes per Card Core (M/s · 155 runs)"
            titleColor={K.fast}
            style={{ opacity: partB }}
          />

          <Card
            x={975}
            y={200}
            w={885}
            h={546}
            border={C.line}
            title="Card L3 Coherence Snoops (M/s)"
            titleColor={K.fwd}
            style={{ opacity: partB }}
          />
        </>
      )}

      {/* ======================= SVG STAGE ======================= */}
      <Stage>
        {/* ---------- PART A SVG (Top Schematic + 3 Mini BarCharts) ---------- */}
        {partA > 0.005 && (
          <g opacity={partA} fontFamily={FONT}>
            {/* Host Container */}
            <rect
              x={60}
              y={192}
              width={650}
              height={200}
              rx={16}
              fill="rgba(20,27,46,0.85)"
              stroke={K.host}
              strokeWidth={2}
              strokeDasharray="8 6"
            />
            <text x={82} y={216} fontSize={20} fontWeight={800} fill={K.host} letterSpacing={1}>
              HOST · XEON 3.9 GHz
            </text>

            {/* Card Container */}
            <rect
              x={1210}
              y={192}
              width={650}
              height={200}
              rx={16}
              fill="rgba(20,27,46,0.85)"
              stroke={K.card}
              strokeWidth={2}
              strokeDasharray="8 6"
            />
            <text x={1232} y={216} fontSize={20} fontWeight={800} fill={K.card} letterSpacing={1}>
              CARD · OCTEON 10 DPU
            </text>

            {/* Host DRAM (cold fallback, left column inside Host) */}
            <rect
              x={82}
              y={230}
              width={186}
              height={146}
              rx={12}
              fill={hl1 > 0.3 || hl3 > 0.3 ? "rgba(249,65,68,0.16)" : "rgba(11,16,32,0.85)"}
              stroke={hl1 > 0.3 || hl3 > 0.3 ? K.slow : C.dim}
              strokeWidth={hl1 > 0.3 || hl3 > 0.3 ? 3 : 2}
            />
            <text
              x={175}
              y={292}
              textAnchor="middle"
              fontSize={23}
              fontWeight={800}
              fill={hl1 > 0.3 || hl3 > 0.3 ? K.slow : C.muted}
            >
              Host DRAM
            </text>
            <text x={175} y={322} textAnchor="middle" fontSize={20} fill={C.muted}>
              cold fallback
            </text>

            {/* Checkpoint 1: Host L1D Source */}
            <rect
              x={338}
              y={228}
              width={348}
              height={62}
              rx={12}
              fill={hl1 > 0.3 ? "rgba(124,147,214,0.28)" : "rgba(124,147,214,0.12)"}
              stroke={K.host}
              strokeWidth={hl1 > 0.3 ? 4 : 2.5}
              filter={hl1 > 0.3 ? "url(#glow)" : undefined}
            />
            <circle cx={368} cy={259} r={16} fill={K.host} />
            <text x={368} y={266} textAnchor="middle" fontSize={21} fontWeight={900} fill={C.bg}>
              1
            </text>
            <text x={398} y={266} fontSize={23} fontWeight={800} fill={C.text}>
              Host L1D Source
            </text>

            {/* Flush dashed arrow between Host L1D and Host DRAM */}
            <Arrow
              x1={334}
              y1={259}
              x2={272}
              y2={259}
              color={hl1 > 0.3 ? K.slow : C.dim}
              w={hl1 > 0.3 ? 3.5 : 2}
              dash="6 4"
              head={10}
            />

            {/* Checkpoint 3: Host Xeon DDIO LLC */}
            <rect
              x={338}
              y={312}
              width={348}
              height={64}
              rx={12}
              fill={hl3 > 0.3 ? "rgba(67,197,158,0.28)" : "rgba(67,197,158,0.12)"}
              stroke={K.ret}
              strokeWidth={hl3 > 0.3 ? 4 : 2.5}
              filter={hl3 > 0.3 ? "url(#glow)" : undefined}
            />
            <circle cx={368} cy={344} r={16} fill={K.ret} />
            <text x={368} y={351} textAnchor="middle" fontSize={21} fontWeight={900} fill={C.bg}>
              3
            </text>
            <text x={398} y={351} fontSize={23} fontWeight={800} fill={C.text}>
              Host Xeon DDIO LLC
            </text>

            {/* Flush dashed arrow between Host DDIO and Host DRAM */}
            <Arrow
              x1={334}
              y1={344}
              x2={272}
              y2={344}
              color={hl3 > 0.3 ? K.slow : C.dim}
              w={hl3 > 0.3 ? 3.5 : 2}
              dash="6 4"
              head={10}
            />

            {/* Checkpoint 2: Card L3 Write-Allocate */}
            <rect
              x={1234}
              y={228}
              width={338}
              height={62}
              rx={12}
              fill={hl2 > 0.3 ? "rgba(76,201,240,0.28)" : "rgba(76,201,240,0.12)"}
              stroke={K.fwd}
              strokeWidth={hl2 > 0.3 ? 4 : 2.5}
              filter={hl2 > 0.3 ? "url(#glow)" : undefined}
            />
            <circle cx={1264} cy={259} r={16} fill={K.fwd} />
            <text x={1264} y={266} textAnchor="middle" fontSize={21} fontWeight={900} fill={C.bg}>
              2
            </text>
            <text x={1294} y={266} fontSize={23} fontWeight={800} fill={C.text}>
              Card L3 Write-Alloc
            </text>

            {/* Card DRAM (cold fallback, top-right inside Card) */}
            <rect
              x={1638}
              y={228}
              width={200}
              height={62}
              rx={12}
              fill={hl2 > 0.3 ? "rgba(249,65,68,0.16)" : "rgba(11,16,32,0.85)"}
              stroke={hl2 > 0.3 ? K.slow : C.dim}
              strokeWidth={hl2 > 0.3 ? 3 : 2}
            />
            <text
              x={1738}
              y={254}
              textAnchor="middle"
              fontSize={22}
              fontWeight={800}
              fill={hl2 > 0.3 ? K.slow : C.muted}
            >
              Card DRAM
            </text>
            <text x={1738} y={278} textAnchor="middle" fontSize={19} fill={C.muted}>
              cold fallback
            </text>

            {/* Flush dashed arrow between Card L3 and Card DRAM */}
            <Arrow
              x1={1576}
              y1={259}
              x2={1634}
              y2={259}
              color={hl2 > 0.3 ? K.slow : C.dim}
              w={hl2 > 0.3 ? 3.5 : 2}
              dash="6 4"
              head={10}
            />

            {/* Card Core (bottom-left inside Card) */}
            <rect
              x={1234}
              y={312}
              width={338}
              height={64}
              rx={12}
              fill="rgba(248,150,30,0.14)"
              stroke={K.card}
              strokeWidth={2.5}
            />
            <text x={1403} y={352} textAnchor="middle" fontSize={23} fontWeight={800} fill={K.card}>
              Card Core (polls L3 → WC)
            </text>

            {/* Vertical short link from Card L3 down to Card Core */}
            <Arrow x1={1403} y1={290} x2={1403} y2={310} color={K.fwd} w={3.5} head={9} />

            {/* W = 1 897 ns RTT Badge (bottom-right inside Card) */}
            <rect
              x={1638}
              y={312}
              width={200}
              height={64}
              rx={12}
              fill="rgba(249,199,79,0.12)"
              stroke={K.knob}
              strokeWidth={2}
            />
            <text x={1738} y={341} textAnchor="middle" fontSize={24} fontWeight={900} fill={K.knob}>
              897 ns
            </text>
            <text x={1738} y={365} textAnchor="middle" fontSize={19} fill={C.muted}>
              W = 1 · 40 runs
            </text>

            {/* PCIe Gen5 x4 Center Bridge */}
            <text x={960} y={230} textAnchor="middle" fontSize={22} fontWeight={800} fill={K.fwd}>
              MOVDIR64B (64 B) →
            </text>
            <Arrow x1={692} y1={250} x2={1226} y2={250} color={K.fwd} w={4.5} head={13} />

            <text x={960} y={302} textAnchor="middle" fontSize={21} fontWeight={700} fill={C.muted}>
              PCIe Gen5 ×4
            </text>

            <Arrow x1={1226} y1={344} x2={692} y2={344} color={K.ret} w={4.5} head={13} />
            <text x={960} y={376} textAnchor="middle" fontSize={22} fontWeight={800} fill={K.ret}>
              ← WC 64 B reply
            </text>

            {/* Live animated packet looping around PCIe */}
            {loopPhase < 0.46 && <Packet pts={fwdPts} k={fwdK} color={K.fwd} r={9} />}
            {loopPhase >= 0.5 && loopPhase < 0.98 && (
              <Packet pts={retPts} k={retK} color={K.ret} r={9} />
            )}

            {/* ---------- 3 MINI BAR CHARTS INSIDE PROOF PANELS (Cues 1–3) ---------- */}
            {p1 > 0.01 && (
              <BarChart
                x0={96}
                x1={600}
                y1={536}
                y0={706}
                max={1200}
                barW={122}
                size={25}
                labelSize={23}
                opacity={p1}
                k={(i) => prog(t, T[1] + 0.35 + i * 0.45, 0.55)}
                bars={[
                  { label: "Hot L1D", sub: "baseline", value: 897.3, text: "897.3 ns", color: K.host },
                  { label: "Cold DRAM", sub: "+117.3 ns", value: 1014.5, text: "1,014.5 ns", color: K.slow },
                ]}
              />
            )}

            {p2 > 0.01 && (
              <BarChart
                x0={696}
                x1={1224}
                y1={536}
                y0={706}
                max={215}
                barW={92}
                size={24}
                labelSize={22}
                opacity={p2}
                k={(i) => prog(t, T[2] + 0.35 + i * 0.4, 0.55)}
                bars={[
                  { label: "Hot L3", sub: "baseline", value: 104, text: "104 ns", color: K.fwd },
                  { label: "Pre-write", sub: "flat!", value: 102, text: "102 ns", color: K.fast },
                  { label: "Post-write", sub: "+75.0 ns", value: 179, text: "179 ns", color: K.slow },
                ]}
              />
            )}

            {p3 > 0.01 && (
              <BarChart
                x0={1308}
                x1={1836}
                y1={536}
                y0={706}
                max={160}
                barW={92}
                size={24}
                labelSize={22}
                opacity={p3}
                k={(i) => prog(t, T[3] + 0.35 + i * 0.4, 0.55)}
                bars={[
                  { label: "Hot LLC", sub: "baseline", value: 42.7, text: "42.7 ns", color: K.ret },
                  { label: "Pre-write", sub: "0.0 ns", value: 42.7, text: "42.7 ns", color: K.fast },
                  { label: "Post-write", sub: "+87.3 ns", value: 130.0, text: "130.0 ns", color: K.slow },
                ]}
              />
            )}
          </g>
        )}

        {/* ---------- PART B SVG (Cue 4: 64 B Card Coherence Charts) ---------- */}
        {partB > 0.005 && (
          <g opacity={partB} fontFamily={FONT}>
            {/* Left Chart: Writes per core (M/s) */}
            <BarChart
              x0={90}
              x1={915}
              y1={346}
              y0={644}
              max={310}
              barW={102}
              size={25}
              labelSize={23}
              k={(i) => (i === 0 ? b0K : i === 1 ? b1K : bRestK)}
              bars={[
                { label: "0 B", sub: "same cell", value: 72.3, text: "72.3", color: K.slow },
                { label: "64 B", sub: "full speed", value: 259.8, text: "259.8", color: K.fast },
                { label: "128 B", value: 259.8, text: "259.8", color: K.fast },
                { label: "256 B", value: 259.8, text: "259.8", color: K.fast },
                { label: "4 KiB", value: 259.8, text: "259.8", color: K.fast },
              ]}
            />

            {/* Highlight box around 64 B bar + callout pill */}
            {b1K > 0.05 && (
              <g opacity={b1K}>
                <rect
                  x={276}
                  y={324}
                  width={123}
                  height={326}
                  rx={12}
                  fill="rgba(67,197,158,0.08)"
                  stroke={K.fast}
                  strokeWidth={3}
                  strokeDasharray="8 5"
                />
                <rect
                  x={292}
                  y={264}
                  width={560}
                  height={42}
                  rx={21}
                  fill="rgba(67,197,158,0.18)"
                  stroke={K.fast}
                  strokeWidth={2}
                />
                <text
                  x={572}
                  y={292}
                  textAnchor="middle"
                  fontSize={22}
                  fontWeight={800}
                  fill={K.fast}
                >
                  64 B inside 128 B block — ZERO false sharing!
                </text>
              </g>
            )}

            {/* Right Chart: L3 Coherence Snoops (M/s) */}
            <BarChart
              x0={1005}
              x1={1830}
              y1={346}
              y0={644}
              max={21}
              barW={102}
              size={25}
              labelSize={23}
              k={(i) => (i === 0 ? b0K : i === 1 ? b1K : bRestK)}
              bars={[
                { label: "0 B", sub: "thrashing", value: 17.8, text: "17.8", color: K.slow },
                { label: "64 B", sub: "idle", value: 0.52, text: "0.52", color: K.fast },
                { label: "128 B", value: 0.53, text: "0.53", color: K.fast },
                { label: "256 B", value: 0.55, text: "0.55", color: K.fast },
                { label: "4 KiB", value: 0.55, text: "0.55", color: K.fast },
              ]}
            />

            {/* Idle baseline callout in open space above 64 B–4 KiB snoop bars */}
            {b1K > 0.05 && (
              <g opacity={b1K}>
                <rect
                  x={1248}
                  y={446}
                  width={540}
                  height={76}
                  rx={14}
                  fill="rgba(67,197,158,0.12)"
                  stroke={K.fast}
                  strokeWidth={2}
                />
                <text
                  x={1518}
                  y={478}
                  textAnchor="middle"
                  fontSize={24}
                  fontWeight={800}
                  fill={K.fast}
                >
                  Idle snoop baseline: 0.52–0.55 M/s
                </text>
                <text x={1518} y={508} textAnchor="middle" fontSize={21} fill={C.muted}>
                  64 B matches 128 B, 256 B, and 4 KiB
                </text>
                <Arrow x1={1285} y1={522} x2={1258} y2={590} color={K.fast} w={3} head={10} />
              </g>
            )}
          </g>
        )}
      </Stage>

      {/* ======================= PART B BOTTOM VERDICT (Cue 4) ======================= */}
      {partB > 0.005 && (
        <Takeaway
          x={60}
          y={766}
          w={1800}
          k={verdictK * partB}
          color={K.fast}
          label="VERDICT"
          size={28}
        >
          Card core-to-core L3 coherence is strictly 64 B (not 128 B); PCIe beside a polling core remains untested.
        </Takeaway>
      )}
    </>
  );
}

export const S03: SceneDef = {
  id: "S03",
  title: "Nailed down #2 · Cache/DDIO & 64 B coherence",
  stage: 1,
  source: "W = 1 cache/DDIO proof (40 runs) · card L3 spacing sweep (155 runs)",
  dur: DUR,
  cues,
  C: Body,
};
