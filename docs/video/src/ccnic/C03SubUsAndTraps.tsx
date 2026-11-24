import React from "react";
import {
  C,
  Cue,
  FONT,
  MONO,
  Packet,
  PacketStream,
  SceneDef,
  Stage,
  countUp,
  fade,
  lerp,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { CK, RingStrip, TestbedTopo, TopoNodeId, topoGeom, topoRoutes } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 11.92, "Without cache coherence, doesn't every message hit slow **DRAM**? No—on commercial PCIe, **all three cache shortcuts are already on**.", "Without cache coherence, doesn't every message crawl through slow D RAM? We cold-flushed each hop one at a time and found all three cache shortcuts are already active on ordinary P C I express."], // 0
  [12.27, 30.61, "Host **L1D** saves **117.3 ns**; BAR4 writes hit **card L3** (**99.94%**, **−75.0 ns**); card WC replies hit **host LLC via DDIO** (**−87.3 ns**).", "Host L one source reads save one hundred seventeen nanoseconds. B A R four writes land in card L three at ninety-nine point nine four percent hits, saving seventy-five nanoseconds. Write-combining replies land in host L L C via Intel D D I O, saving eighty-seven nanoseconds."], // 1
  [30.96, 47.78, "Verified 64 B round trip: **897.3 ns** with full **IOMMU**—beating stock `SDP` (**2,400 ns**) and real **CXL-NIC** (**1.3–2.1 µs**, no IOMMU).", "A verified sixty-four byte round trip takes eight hundred ninety-seven point three nanoseconds with hardware I O M M U enabled—nearly three times faster than stock S D P, and faster than real C X L NIC hardware at one point three to two point one microseconds."], // 2
  [48.13, 55.31, "Testing on a real DPU—not a second CPU socket—exposed **two real PCIe microarchitecture traps** via hardware PMUs.", "Running on a real D P U rather than a two-socket C P U emulator also exposed two real microarchitecture traps."], // 3
  [55.66, 73.50, "**Trap 1 (write-frontier snooping)**: card polling the live **~3 µs** frontier slows push **162.6 → 113.5 M/s**; card L3 granularity is **64 B**.", "Trap one: polling the exact cells inside the live three-microsecond write frontier slows host push from one hundred sixty-two point six to one hundred thirteen point five million per second. Reading behind that frontier restores full speed, and card L three granularity is strictly sixty-four bytes."], // 4
  [73.85, 90.43, "**Trap 2 (PEM0/IOBN cliff)**: concurrent RPC + empty card polling serializes inbound writes to **76.4 ns (~13.1 M/s)** across **64–4224 B** strides.", "Trap two: under concurrent load plus relentless empty card polling, inbound writes serialize inside the P E M zero bridge at seventy-six point four nanoseconds—thirteen point one million per second—invariant from sixty-four to four thousand two hundred twenty-four byte strides."], // 5
];
const DUR = 91.83; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const STRIDES = ["64 B", "128 B", "4096 B", "4224 B"];

function Body() {
  const t = useT();
  const rt = useRealT();

  // ================= PART A (Cues 0..2): 3 Cache Shortcuts & Sub-us RTT =================
  const kPartA = fade(t, 0.15, T[3] - 0.15, 0.5);
  const mTopo = prog(t, T[2], 0.8);
  const topoS = lerp(0.86, 0.64, mTopo);
  const topoDx = lerp(350, 508, mTopo);
  const topoDy = lerp(60, 34, mTopo);
  const g = topoGeom(topoS, topoDx, topoDy);
  const r = topoRoutes(g);

  // Cue 0: Question pill -> Answer pill (mutually exclusive)
  const kAsk0 = fade(t, T[0] + 0.2, T[0] + 5.0, 0.45);
  const kAns0 = fade(t, T[0] + 5.2, T[2] - 0.2, 0.45);

  // Cue 1: 3 sequential cache-shortcut hops
  const kHop1 = fade(t, T[1] + 0.4, T[2] - 0.15, 0.45);
  const kHop2 = fade(t, T[1] + 5.0, T[2] - 0.15, 0.45);
  const kHop3 = fade(t, T[1] + 11.2, T[2] - 0.15, 0.45);

  // Packet animation during Cue 1..2
  const kPktActive = prog(t, T[1] + 0.5, 0.4) * kPartA;
  const cycle = (rt * 0.52) % 1; // 0..0.5 forward H2C, 0.5..1.0 return C2H
  const isFwd = cycle < 0.5;
  const pktK = isFwd ? cycle * 2 : (cycle - 0.5) * 2;

  // Active node highlights during Cue 1
  const hl: Partial<Record<TopoNodeId, string>> = {};
  if (t >= T[1] + 0.3 && t < T[3]) {
    if (kHop1 > 0.3) hl.cpu = CK.push;
    if (kHop2 > 0.3) {
      hl.pem = CK.card;
      hl.cL3 = CK.ok;
    }
    if (kHop3 > 0.3) hl.hLlc = CK.ok;
  } else if (kAns0 > 0.3) {
    hl.cpu = CK.push;
    hl.cL3 = CK.ok;
    hl.hLlc = CK.ok;
  }

  // Cue 2: 64 B RTT Horizontal Bar Chart
  const kRttPanel = fade(t, T[2] + 0.25, T[3] - 0.15, 0.5);
  const kBarDp = prog(t, T[2] + 0.6, 0.85);
  const kBarCxl = prog(t, T[2] + 6.4, 0.9);
  const kBarSdp = prog(t, T[2] + 3.4, 0.85);
  const rttDpStr = t < T[2] + 0.6 ? "897.3 ns" : `${countUp(t, T[2] + 0.6, 1.0, 897.3, 1)} ns`;

  // ================= PART B (Cues 3..5): Two Real DPU Hardware Traps =================
  const kPartB = prog(t, T[3] + 0.15, 0.55);
  const kTrap1 = prog(t, T[4] + 0.15, 0.55);
  const kTrap2 = prog(t, T[5] + 0.15, 0.55);

  // Trap 1 animated ring strip (14 cells)
  const nCells = 14;
  const wrIdx = Math.floor(rt * 2.2) % nCells;
  // Frontier spans 4 cells ending at wrIdx (non-wrapping sliding window for clean visual)
  const winStart = Math.max(0, Math.min(nCells - 4, wrIdx - 3));
  const winEnd = winStart + 3;
  const safeIdx = Math.max(0, winStart - 3);
  const kT1Bars = prog(t, T[4] + 2.0, 0.85);
  const kT1Gran = prog(t, T[4] + 9.2, 0.6);

  // Trap 2 funnel & stride falsification
  const kT2Funnel = prog(t, T[5] + 0.6, 0.7);
  const kT2Strides = prog(t, T[5] + 5.6, 0.8);
  const sxRtt = linScale(0, 2600, 560, 1580);

  // Trap 2 funnel packet routes
  const inLaneY = [354, 378, 402, 426];
  const outLanePts: [number, number][] = [
    [1418, 390],
    [1648, 390],
  ];

  return (
    <>
      <Stage>
        {/* ================= PART A: Topology + 3 Cache Shortcuts + RTT Chart ================= */}
        {kPartA > 0.01 && (
          <g opacity={kPartA}>
            {/* Cue 0 top question -> answer pill */}
            {kAsk0 > 0.01 && (
              <g opacity={kAsk0}>
                <rect x={600} y={134} width={720} height={46} rx={23} fill={`${CK.cxl}1c`} stroke={CK.cxl} strokeWidth={2} />
                <text x={960} y={157} textAnchor="middle" dominantBaseline="central" fontSize={23} fontWeight={800} fill={CK.cxl} fontFamily={FONT}>
                  No hardware coherence → Does every message hit slow DRAM?
                </text>
              </g>
            )}
            {kAns0 > 0.01 && (
              <g opacity={kAns0}>
                <rect x={510} y={134} width={900} height={46} rx={23} fill={`${CK.ok}1c`} stroke={CK.ok} strokeWidth={2} />
                <text x={960} y={157} textAnchor="middle" dominantBaseline="central" fontSize={23} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
                  No! All 3 commercial PCIe cache shortcuts are ON by default
                </text>
              </g>
            )}

            {/* 2-node saturn <-> PCIe <-> CN106 topology with DRAM dimmed */}
            <TestbedTopo
              g={g}
              showPeer={false}
              hl={hl}
              dim={{ hDram: 0.72, cDram: 0.72, dpi: 0.55, nix: 0.6 }}
            />

            {/* DRAM bypassed stamps over dimmed DRAM blocks */}
            <g opacity={0.85}>
              <rect
                x={g.r.hDram.x + g.r.hDram.w * 0.18}
                y={g.r.hDram.y + g.r.hDram.h * 0.5 - 16 * topoS}
                width={g.r.hDram.w * 0.64}
                height={32 * topoS}
                rx={8 * topoS}
                fill="rgba(11,16,32,0.88)"
                stroke={C.dim}
                strokeWidth={1.5}
                strokeDasharray="6 4"
              />
              <text
                x={g.r.hDram.x + g.r.hDram.w / 2}
                y={g.r.hDram.y + g.r.hDram.h / 2}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={Math.max(12, Math.round(17 * topoS))}
                fontWeight={800}
                fill={C.muted}
                fontFamily={MONO}
              >
                DRAM BYPASSED
              </text>
              <rect
                x={g.r.cDram.x + g.r.cDram.w * 0.16}
                y={g.r.cDram.y + g.r.cDram.h * 0.5 - 16 * topoS}
                width={g.r.cDram.w * 0.68}
                height={32 * topoS}
                rx={8 * topoS}
                fill="rgba(11,16,32,0.88)"
                stroke={C.dim}
                strokeWidth={1.5}
                strokeDasharray="6 4"
              />
              <text
                x={g.r.cDram.x + g.r.cDram.w / 2}
                y={g.r.cDram.y + g.r.cDram.h / 2}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={Math.max(12, Math.round(17 * topoS))}
                fontWeight={800}
                fill={C.muted}
                fontFamily={MONO}
              >
                DRAM BYPASSED
              </text>
            </g>

            {/* Glowing 64 B round-trip packet along PCIe cache-shortcut routes */}
            {kPktActive > 0.02 && (
              <g opacity={kPktActive}>
                {isFwd ? (
                  <Packet pts={r.pushH2C} k={pktK} color={CK.push} r={10 * topoS} trail={true} />
                ) : (
                  <Packet pts={r.wcC2H} k={pktK} color={CK.ok} r={10 * topoS} trail={true} />
                )}
              </g>
            )}

            {/* Cue 1: 3 Cache-Shortcut Callout Cards below topology (y 672..838) */}
            {kHop1 > 0.01 && (
              <g opacity={kHop1}>
                <rect x={90} y={674} width={546} height={162} rx={16} fill="rgba(16,28,46,0.95)" stroke={CK.push} strokeWidth={2.4} />
                <rect x={112} y={694} width={104} height={30} rx={8} fill={`${CK.push}24`} stroke={CK.push} strokeWidth={1.4} />
                <text x={164} y={709} textAnchor="middle" dominantBaseline="central" fontSize={17} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                  HOP 1
                </text>
                <text x={230} y={715} fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  Host L1D TX Source
                </text>
                <text x={112} y={774} fontSize={40} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                  −117.3 ns
                </text>
                <text x={358} y={770} fontSize={19} fill={C.muted} fontFamily={MONO}>
                  saved vs cold DRAM
                </text>
                <text x={112} y={814} fontSize={18} fill={C.muted} fontFamily={FONT}>
                  MOVDIR64B reads hot 64 B payload directly from L1D
                </text>
              </g>
            )}

            {kHop2 > 0.01 && (
              <g opacity={kHop2}>
                <rect x={687} y={674} width={546} height={162} rx={16} fill="rgba(14,32,34,0.95)" stroke={CK.ok} strokeWidth={2.4} />
                <rect x={709} y={694} width={104} height={30} rx={8} fill={`${CK.ok}24`} stroke={CK.ok} strokeWidth={1.4} />
                <text x={761} y={709} textAnchor="middle" dominantBaseline="central" fontSize={17} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  HOP 2
                </text>
                <text x={827} y={715} fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  Card 48 MiB L3 Hit
                </text>
                <text x={709} y={774} fontSize={40} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  −75.0 ns
                </text>
                <text x={932} y={770} fontSize={19} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
                  99.94% L3 hit
                </text>
                <text x={709} y={814} fontSize={18} fill={C.muted} fontFamily={FONT}>
                  Inbound BAR4 write allocates cleanly in CN106 L3
                </text>
              </g>
            )}

            {kHop3 > 0.01 && (
              <g opacity={kHop3}>
                <rect x={1284} y={674} width={546} height={162} rx={16} fill="rgba(14,32,34,0.95)" stroke={CK.ok} strokeWidth={2.4} />
                <rect x={1306} y={694} width={104} height={30} rx={8} fill={`${CK.ok}24`} stroke={CK.ok} strokeWidth={1.4} />
                <text x={1358} y={709} textAnchor="middle" dominantBaseline="central" fontSize={17} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  HOP 3
                </text>
                <text x={1424} y={715} fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  Host LLC via Intel DDIO
                </text>
                <text x={1306} y={774} fontSize={40} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  −87.3 ns
                </text>
                <text x={1526} y={770} fontSize={19} fontWeight={700} fill={CK.push} fontFamily={MONO}>
                  1.00 ItoM/msg
                </text>
                <text x={1306} y={814} fontSize={18} fill={C.muted} fontFamily={FONT}>
                  Card WC reply lands straight in host LLC (0 DRAM)
                </text>
              </g>
            )}

            {/* Cue 2: Verified 64 B W=1 RTT Comparison Chart (y 500..858) */}
            {kRttPanel > 0.01 && (
              <g opacity={kRttPanel}>
                <rect
                  x={70}
                  y={496}
                  width={1780}
                  height={364}
                  rx={20}
                  fill="rgba(16,22,40,0.94)"
                  stroke={CK.push}
                  strokeWidth={2}
                />
                <text x={106} y={538} fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  Verified 64 B Round-Trip Latency (W = 1) — Lower Is Faster
                </text>
                <text x={1814} y={538} textAnchor="end" fontSize={18} fill={C.muted} fontFamily={MONO}>
                  MRV-97 (40 runs) · full HW IOMMU
                </text>

                {/* Vertical grid ticks along track x=560..1580 */}
                {[0, 500, 1000, 1500, 2000, 2500].map((v) => (
                  <g key={v}>
                    <line x1={sxRtt(v)} y1={566} x2={sxRtt(v)} y2={810} stroke={C.line} strokeWidth={1.3} opacity={0.55} />
                    <text x={sxRtt(v)} y={838} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                      {v === 0 ? "0" : `${v} ns`}
                    </text>
                  </g>
                ))}

                {/* Row 1: DirectPush 897.3 ns (cyan glowing) */}
                <g opacity={Math.max(0.1, kBarDp)}>
                  <text x={540} y={598} textAnchor="end" fontSize={22} fontWeight={800} fill={CK.push} fontFamily={FONT}>
                    DirectPush (PCIe Gen5)
                  </text>
                  <text x={540} y={622} textAnchor="end" fontSize={16} fill={CK.ok} fontFamily={MONO}>
                    HW IOMMU ON · 0 coherence
                  </text>
                  <rect x={560} y={576} width={1020} height={54} rx={10} fill="#141b2e" stroke={C.line} strokeWidth={1.2} />
                  {/* Subtle outer glow stroke */}
                  <rect
                    x={558}
                    y={574}
                    width={Math.max(0, (sxRtt(897.3) - 560) * kBarDp + 4)}
                    height={58}
                    rx={12}
                    fill="none"
                    stroke={CK.push}
                    strokeWidth={3}
                    opacity={0.45 * kBarDp}
                  />
                  <rect
                    x={560}
                    y={576}
                    width={Math.max(0, (sxRtt(897.3) - 560) * kBarDp)}
                    height={54}
                    rx={10}
                    fill={CK.push}
                    opacity={0.95}
                  />
                  {kBarDp > 0.3 && (
                    <text
                      x={560 + (sxRtt(897.3) - 560) * kBarDp + 18}
                      y={603}
                      dominantBaseline="central"
                      fontSize={28}
                      fontWeight={800}
                      fill={CK.push}
                      fontFamily={MONO}
                    >
                      {rttDpStr}
                    </text>
                  )}
                  {kBarDp > 0.7 && (
                    <g>
                      <rect x={1606} y={584} width={212} height={38} rx={10} fill={`${CK.ok}24`} stroke={CK.ok} strokeWidth={1.6} />
                      <text x={1712} y={603} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                        SUB-µs WINNER
                      </text>
                    </g>
                  )}
                </g>

                {/* Row 2: CXL-NIC 1,300-2,100 ns (solid 1300 + hatched range 1300..2100) */}
                <g opacity={Math.max(0.1, kBarCxl)}>
                  <text x={540} y={678} textAnchor="end" fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
                    CXL-NIC (real CXL 1.1)
                  </text>
                  <text x={540} y={702} textAnchor="end" fontSize={16} fill={C.muted} fontFamily={MONO}>
                    Ji et al., MICRO &apos;25 · no IOMMU
                  </text>
                  <rect x={560} y={656} width={1020} height={54} rx={10} fill="#141b2e" stroke={C.line} strokeWidth={1.2} />
                  {/* Solid min portion 0..1300 */}
                  <rect
                    x={560}
                    y={656}
                    width={Math.max(0, (sxRtt(1300) - 560) * kBarCxl)}
                    height={54}
                    rx={10}
                    fill={CK.cxl}
                    opacity={0.86}
                  />
                  {/* Hatched range band 1300..2100 */}
                  <rect
                    x={560 + (sxRtt(1300) - 560) * kBarCxl}
                    y={656}
                    width={Math.max(0, (sxRtt(2100) - sxRtt(1300)) * kBarCxl)}
                    height={54}
                    rx={6}
                    fill="url(#hatch)"
                    stroke={CK.cxl}
                    strokeWidth={2.5}
                    strokeDasharray="7 5"
                  />
                  {kBarCxl > 0.4 && (
                    <text
                      x={560 + (sxRtt(2100) - 560) * kBarCxl + 18}
                      y={683}
                      dominantBaseline="central"
                      fontSize={24}
                      fontWeight={800}
                      fill={CK.cxl}
                      fontFamily={MONO}
                    >
                      1,300–2,100 ns
                    </text>
                  )}
                </g>

                {/* Row 3: Stock CN106 SDP 2,400 ns (purple) */}
                <g opacity={Math.max(0.1, kBarSdp)}>
                  <text x={540} y={758} textAnchor="end" fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
                    Stock CN106 SDP
                  </text>
                  <text x={540} y={782} textAnchor="end" fontSize={16} fill={C.muted} fontFamily={MONO}>
                    raw ping-pong (~2.7× slower)
                  </text>
                  <rect x={560} y={736} width={1020} height={54} rx={10} fill="#141b2e" stroke={C.line} strokeWidth={1.2} />
                  <rect
                    x={560}
                    y={736}
                    width={Math.max(0, (sxRtt(2400) - 560) * kBarSdp)}
                    height={54}
                    rx={10}
                    fill={CK.sdp}
                    opacity={0.9}
                  />
                  {kBarSdp > 0.4 && (
                    <text
                      x={560 + (sxRtt(2400) - 560) * kBarSdp + 18}
                      y={763}
                      dominantBaseline="central"
                      fontSize={25}
                      fontWeight={800}
                      fill={CK.sdp}
                      fontFamily={MONO}
                    >
                      2,400 ns
                    </text>
                  )}
                </g>
              </g>
            )}
          </g>
        )}

        {/* ================= PART B (Cues 3..5): Two Real DPU Hardware Traps ================= */}
        {kPartB > 0.01 && (
          <g opacity={kPartB}>
            {/* Split header bar */}
            <rect
              x={60}
              y={138}
              width={1800}
              height={66}
              rx={16}
              fill="rgba(20,27,46,0.95)"
              stroke={CK.card}
              strokeWidth={2}
            />
            <text x={960} y={171} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Real DPU vs 2-Socket CPU Emulator{" "}
              <tspan fill={CK.card}>→ Two Real Hardware Traps</tspan>{" "}
              <tspan fill={C.muted} fontFamily={MONO} fontSize={21}>
                (100% Non-Multiplexed PMU)
              </tspan>
            </text>

            {/* -------- LEFT CARD: TRAP 1 — Live Write-Frontier Snooping -------- */}
            <g>
              <rect
                x={60}
                y={226}
                width={870}
                height={634}
                rx={20}
                fill="rgba(16,22,40,0.94)"
                stroke={kTrap1 > 0.3 ? CK.card : C.line}
                strokeWidth={kTrap1 > 0.3 ? 2.6 : 1.8}
              />
              {/* Card Header (crisp in Cue 3 preview, highlighted in Cue 4+) */}
              <rect x={88} y={248} width={118} height={34} rx={9} fill={`${CK.trap}26`} stroke={CK.trap} strokeWidth={1.6} />
              <text x={147} y={265} textAnchor="middle" dominantBaseline="central" fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                TRAP 1
              </text>
              <text x={220} y={272} fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
                Live Write-Frontier Snooping
              </text>
              <text x={902} y={270} textAnchor="end" fontSize={17} fill={C.muted} fontFamily={MONO}>
                MRV-76 · MRV-55
              </text>

              {/* Cue 3 preview summary (fades out cleanly as Cue 4 starts) */}
              {kTrap1 < 0.99 && (
                <g opacity={1 - kTrap1}>
                  <text x={92} y={368} fontSize={23} fontWeight={700} fill={C.muted} fontFamily={FONT}>
                    Card core polling the live PCIe write frontier triggers
                  </text>
                  <text x={92} y={402} fontSize={23} fontWeight={700} fill={CK.card} fontFamily={FONT}>
                    L3 snoop stalls on host MOVDIR64B writes.
                  </text>
                  <text x={92} y={468} fontSize={20} fill={C.dim} fontFamily={MONO}>
                    Exposed via CN106 L3 TAD + Host CHA PMU counters
                  </text>
                </g>
              )}

              {/* Trap 1 full visual (Cue 4+) */}
              <g opacity={kTrap1}>
                {/* Animated Ring Strip */}
                <text x={92} y={322} fontSize={20} fontWeight={700} fill={C.muted} fontFamily={FONT}>
                  BAR4 64 B Ring Buffer — Host Write Frontier vs Card Poll Position
                </text>
                <RingStrip
                  x={108}
                  y={364}
                  n={nCells}
                  cellW={46}
                  cellH={48}
                  gap={8}
                  highlightRange={[winStart, winEnd]}
                  highlightColor={CK.trap}
                  state={(i) => {
                    if (i >= winStart && i <= winEnd) return "trap";
                    if (i === safeIdx) return "ok";
                    if (i < winStart) return "hot";
                    return "empty";
                  }}
                />
                {/* Live Frontier & Safe Lag Pointers */}
                <text
                  x={108 + ((winStart + winEnd) / 2) * 54 + 23}
                  y={442}
                  textAnchor="middle"
                  fontSize={18}
                  fontWeight={800}
                  fill={CK.trap}
                  fontFamily={MONO}
                >
                  ▲ ~3 µs live frontier (snoop stall!)
                </text>
                {winStart >= 3 && (
                  <text
                    x={108 + safeIdx * 54 + 23}
                    y={350}
                    textAnchor="middle"
                    fontSize={17}
                    fontWeight={800}
                    fill={CK.ok}
                    fontFamily={MONO}
                  >
                    ▼ &gt;3 µs lag
                  </text>
                )}
              {/* Throughput Comparison Bars inside Trap 1 */}
              <line x1={92} y1={462} x2={898} y2={462} stroke={C.line} strokeWidth={1.4} opacity={0.7} />
              <text x={92} y={498} fontSize={22} fontWeight={800} fill={C.text} fontFamily={FONT}>
                Host Push Throughput (64 B MOVDIR64B)
              </text>

              {/* Red bar: polling inside ~3 us frontier -> 113.5 M/s */}
              <text x={92} y={536} fontSize={20} fontWeight={700} fill={CK.trap} fontFamily={FONT}>
                Card polls inside live ~3 µs frontier
              </text>
              <rect x={92} y={548} width={600} height={42} rx={8} fill="#141b2e" stroke={C.line} strokeWidth={1.2} />
              <rect
                x={92}
                y={548}
                width={Math.max(0, (113.5 / 175) * 600 * kT1Bars)}
                height={42}
                rx={8}
                fill={CK.trap}
                opacity={0.92}
              />
              <text x={710} y={569} dominantBaseline="central" fontSize={25} fontWeight={800} fill={CK.trap} fontFamily={MONO} opacity={kT1Bars}>
                113.5 M/s
              </text>

              {/* Green bar: lagging behind > 3 us frontier -> 162.6 M/s */}
              <text x={92} y={626} fontSize={20} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
                Card reads behind &gt;3 µs frontier (even @ 1,033 M/s L3 reads)
              </text>
              <rect x={92} y={638} width={600} height={42} rx={8} fill="#141b2e" stroke={C.line} strokeWidth={1.2} />
              <rect
                x={92}
                y={638}
                width={Math.max(0, (162.6 / 175) * 600 * kT1Bars)}
                height={42}
                rx={8}
                fill={CK.ok}
                opacity={0.92}
              />
              <text x={710} y={659} dominantBaseline="central" fontSize={25} fontWeight={800} fill={CK.ok} fontFamily={MONO} opacity={kT1Bars}>
                162.6 M/s
              </text>

              {/* Strict 64 B granularity badge */}
              <g opacity={Math.max(0.25, kT1Gran)}>
                <rect
                  x={92}
                  y={718}
                  width={806}
                  height={112}
                  rx={14}
                  fill="rgba(248,150,30,0.12)"
                  stroke={CK.card}
                  strokeWidth={2}
                />
                <text x={118} y={756} fontSize={22} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                  Card L3 coherence granularity = strictly 64 B
                </text>
                <text x={118} y={796} fontSize={19} fill={C.text} fontFamily={FONT}>
                  Adjacent 64 B cells inside the same 128 B LLC line do NOT false-share;
                </text>
                <text x={118} y={820} fontSize={19} fill={C.muted} fontFamily={FONT}>
                  only polling the exact live cell slows push (162.6 → 113.5 M/s).
                </text>
              </g>
              </g>
            </g>

            {/* -------- RIGHT CARD: TRAP 2 — PEM0/IOBN Inbound Bridge Cliff -------- */}
            <g>
              <rect
                x={990}
                y={226}
                width={870}
                height={634}
                rx={20}
                fill="rgba(16,22,40,0.94)"
                stroke={kTrap2 > 0.3 ? CK.trap : C.line}
                strokeWidth={kTrap2 > 0.3 ? 2.6 : 1.8}
              />
              {/* Card Header (crisp in Cue 3..4 preview, highlighted in Cue 5) */}
              <rect x={1018} y={248} width={118} height={34} rx={9} fill={`${CK.trap}26`} stroke={CK.trap} strokeWidth={1.6} />
              <text x={1077} y={265} textAnchor="middle" dominantBaseline="central" fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                TRAP 2
              </text>
              <text x={1150} y={272} fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
                PEM0/IOBN Inbound Bridge Cliff
              </text>
              <text x={1832} y={270} textAnchor="end" fontSize={17} fill={C.muted} fontFamily={MONO}>
                MRV-75 · MRV-112
              </text>

              {/* Cue 3..4 preview summary (fades out cleanly as Cue 5 starts) */}
              {kTrap2 < 0.99 && (
                <g opacity={1 - kTrap2}>
                  <text x={1022} y={368} fontSize={23} fontWeight={700} fill={C.muted} fontFamily={FONT}>
                    Concurrent closed-loop RPC + empty card polling
                  </text>
                  <text x={1022} y={402} fontSize={23} fontWeight={700} fill={CK.trap} fontFamily={FONT}>
                    serializes inbound writes inside the PCIe bridge.
                  </text>
                  <text x={1022} y={468} fontSize={20} fill={C.dim} fontFamily={MONO}>
                    Falsified across 64 B → 4224 B ring cell strides
                  </text>
                </g>
              )}

              {/* Trap 2 full visual (Cue 5) */}
              <g opacity={kTrap2}>
              {/* Animated Serialization Funnel (y 310..474) */}
              <g opacity={Math.max(0.25, kT2Funnel)}>
                <rect x={1018} y={310} width={814} height={164} rx={14} fill="#10172b" stroke={C.line} strokeWidth={1.4} />
                {/* 4 parallel inbound host streams entering funnel */}
                <text x={1034} y={332} fontSize={16} fontWeight={700} fill={CK.push} fontFamily={MONO}>
                  Parallel W=48–128 + empty poll
                </text>
                {inLaneY.map((ly, i) => (
                  <g key={i}>
                    <line x1={1036} y1={ly} x2={1242} y2={ly} stroke={CK.push} strokeWidth={2.5} opacity={0.55} />
                    <PacketStream
                      pts={[
                        [1036, ly],
                        [1242, ly],
                      ]}
                      travel={rt * 145 + i * 27}
                      gap={64}
                      size={8}
                      color={CK.push}
                      opacity={0.85}
                    />
                  </g>
                ))}

                {/* Red funnel trapezoid: PEM0 / IOBN */}
                <polygon
                  points="1244,336 1418,371 1418,409 1244,444"
                  fill="rgba(249,65,68,0.22)"
                  stroke={CK.trap}
                  strokeWidth={2.5}
                />
                <text x={1318} y={385} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  PEM0
                </text>
                <text x={1318} y={407} textAnchor="middle" fontSize={16} fontWeight={700} fill={C.text} fontFamily={MONO}>
                  IOBN
                </text>

                {/* Single serialized red queue out */}
                <line x1={1418} y1={390} x2={1648} y2={390} stroke={CK.trap} strokeWidth={4} opacity={0.8} />
                <PacketStream pts={outLanePts} travel={rt * 56} gap={52} size={11} color={CK.trap} opacity={0.95} />
                <text x={1532} y={364} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  76.4 ns / line
                </text>
                <text x={1532} y={422} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.cxl} fontFamily={MONO}>
                  ~13.1 M/s cap
                </text>
                <rect x={1652} y={332} width={164} height={98} rx={12} fill={`${CK.ok}12`} stroke={CK.ok} strokeWidth={2} />
                <text x={1734} y={362} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
                  Card L3 TAD
                </text>
                <text x={1734} y={388} textAnchor="middle" fontSize={17} fontWeight={700} fill={C.text} fontFamily={MONO}>
                  occ 0.02–0.12
                </text>
                <text x={1734} y={412} textAnchor="middle" fontSize={15} fill={C.muted} fontFamily={MONO}>
                  (nearly empty!)
                </text>
              </g>

              {/* Mini 4-Bar Stride Falsification Proof (MRV-112) */}
              <g opacity={Math.max(0.2, kT2Strides)}>
                <text x={1022} y={514} fontSize={22} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  MRV-112 Stride Falsification (86 runs)
                </text>
                <text x={1832} y={514} textAnchor="end" fontSize={18} fontWeight={700} fill={CK.cxl} fontFamily={MONO}>
                  3.3% spread across 66× stride!
                </text>

                {/* Mini chart frame: x 1094..1812, y0=716 (0 M/s), y1=548 (20 M/s) */}
                {[0, 10, 20].map((v) => {
                  const yy = linScale(0, 20, 716, 548)(v);
                  return (
                    <g key={v}>
                      <line x1={1094} y1={yy} x2={1812} y2={yy} stroke={C.line} strokeWidth={1.2} opacity={0.6} />
                      <text x={1082} y={yy} textAnchor="end" dominantBaseline="central" fontSize={18} fill={C.muted} fontFamily={MONO}>
                        {v}
                      </text>
                    </g>
                  );
                })}
                <text x={1082} y={536} textAnchor="end" fontSize={16} fill={C.muted} fontFamily={MONO}>
                  M/s
                </text>
                <line x1={1094} y1={716} x2={1812} y2={716} stroke={C.dim} strokeWidth={2} />

                {/* Locked band highlight at 13.12-13.55 M/s */}
                <rect
                  x={1094}
                  y={linScale(0, 20, 716, 548)(13.8)}
                  width={718}
                  height={linScale(0, 20, 716, 548)(12.8) - linScale(0, 20, 716, 548)(13.8)}
                  fill={`${CK.trap}28`}
                />
                <line
                  x1={1094}
                  y1={linScale(0, 20, 716, 548)(13.34)}
                  x2={1812}
                  y2={linScale(0, 20, 716, 548)(13.34)}
                  stroke={CK.cxl}
                  strokeWidth={2.2}
                  strokeDasharray="8 6"
                />

                {/* 4 equal-height bars locked in 13.12-13.55 M/s */}
                {STRIDES.map((sLabel, i) => {
                  const slot = (1812 - 1094) / 4;
                  const bx = 1094 + slot * (i + 0.5);
                  const val = 13.12 + i * 0.14; // strictly inside verified [13.12, 13.55] band
                  const kk = prog(t, T[5] + 5.8 + i * 0.45, 0.65);
                  const top = lerp(716, linScale(0, 20, 716, 548)(val), kk);
                  return (
                    <g key={sLabel}>
                      <rect x={bx - 42} y={top} width={84} height={Math.max(0, 716 - top)} rx={7} fill={CK.trap} opacity={0.9} />
                      <text x={bx} y={744} textAnchor="middle" fontSize={20} fontWeight={700} fill={C.text} fontFamily={MONO}>
                        {sLabel}
                      </text>
                    </g>
                  );
                })}

                {/* Verified range pill centered cleanly above the flat bars */}
                <rect x={1224} y={554} width={458} height={36} rx={9} fill="rgba(26,14,22,0.95)" stroke={CK.cxl} strokeWidth={1.8} />
                <text x={1453} y={572} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
                  All 4 locked at 13.12–13.55 M/s
                </text>

                {/* Conclusion strip */}
                <rect
                  x={1018}
                  y={770}
                  width={814}
                  height={66}
                  rx={12}
                  fill="rgba(249,65,68,0.12)"
                  stroke={CK.trap}
                  strokeWidth={1.8}
                />
                <text x={1425} y={803} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={700} fill={C.text} fontFamily={FONT}>
                  Proves <tspan fill={CK.trap} fontWeight={800}>bridge serialization</tspan> — not cache-line or page-prefetch contention
                </text>
              </g>
              </g>
            </g>
          </g>
        )}
      </Stage>
    </>
  );
}

export const C03: SceneDef = {
  id: "C03",
  title: "1 \u00b7 Sub-\u00b5s & Two DPU Traps",
  stage: 0,
  source: "MRV-148 \u00b7 MRV-97 (40) \u00b7 MRV-76 (33) \u00b7 MRV-75 (131) \u00b7 MRV-112 (86)",
  dur: DUR,
  cues,
  C: Body,
};
