import React from "react";
import {
  Arrow,
  Axes,
  C,
  Card,
  Cue,
  FONT,
  MONO,
  PacketStream,
  Pill,
  SceneDef,
  Series,
  Stage,
  Txt,
  clamp01,
  countUp,
  fade,
  lerp,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { CK, HBarChart, HBarRow, HeroStat, RingStrip, VerdictRow } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 10.20, "Report 2 splits **two TX buffer lifetimes**: **host-to-NIC transfer** vs **upper-layer TCP retransmission retention**.", "Report two examines transmit buffers by splitting two distinct lifetimes: transferring to the NIC, and retaining packets for T C P retransmission."], // 0
  [10.55, 26.39, "PCIe is **lossless** and pushes **64 B by value**—the NIC never reads host TX RAM back. Reusing **one 64 B buffer** hits **174.8 M/s** (**97.7%**).", "Because P C I express is lossless and direct push copies sixty-four bytes by value, the NIC never reads host transmit memory back. Overwriting one single sixty-four byte buffer hits one hundred seventy-four point eight million per second—zero buffer pool needed."], // 1
  [26.74, 40.76, "For **TCP retention**, **CC-NIC Fig. 15** pays cross-link invalidations (**−20%**), plus **−50%** at 4 KiB stride and **−73%** with host pools.", "When T C P must retain sent buffers until remote acks, C C NIC figure fifteen pays cross-link invalidations that cut throughput twenty percent—climbing to fifty percent at four kilobyte strides and seventy-three percent with host pools."], // 2
  [41.11, 59.09, "With direct push, growing a sequential pool **2,000×** (**64 KiB → 128 MiB**) holds **47.5 → 47.1 M/s** (**−0.9%**, **99.99993%** DDIO, **0** misses).", "With direct push, the host retention pool never enters NIC caches. Growing it two thousand times from sixty-four kilobytes to one hundred twenty-eight megabytes holds throughput flat at forty-seven point one million per second, with ninety-nine point nine nine nine nine percent D D I O hits and zero misses."], // 3
  [59.44, 73.26, "Why did a **2048 B-stride random pool** halve speed (**44.4 → 22.3 M/s**)? Power-of-two `2^11` stride aliases into **1/32** of host LLC sets!", "When a two-kilobyte random message-buffer pool cut speed in half from forty-four point four to twenty-two point three million, the culprit was host-local: two thousand forty-eight is a power of two, colliding into one out of thirty-two host L L C sets."], // 4
  [73.61, 90.45, "Add **64 B** → **2112 B** stride: speed rebounds to **43.9 M/s**! True 128 MiB random hits host DDR5 (**21.5 M/s**) with **100% DDIO**.", "Pad each slot by sixty-four bytes to two thousand one hundred twelve bytes, and speed rebounds to forty-three point nine million. Even a truly random one hundred twenty-eight megabyte set hits local D D R five while NIC D D I O stays one hundred percent—zero coherence tax."], // 5
];
const DUR = 91.85; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Act 1: Cues 0..2 (Buffer lifetime split, single 64 B reuse, CC-NIC Fig. 15 waterfall)
  const act1 = fade(t, T[0], T[3], 0.5);
  const k0 = prog(t, T[0], 0.7);
  const k1 = prog(t, T[1], 0.7);
  const k2 = prog(t, T[2], 0.7);

  // Act 2: Cues 3..5 (MRV-116 sequential pool flat line + MRV-125 random stride detective)
  const act2 = prog(t, T[3], 0.55);
  const k3 = prog(t, T[3] + 0.25, 1.2);
  const k4 = prog(t, T[4], 0.7);
  const k5 = prog(t, T[5], 0.8);

  // Continuous ambient motion
  const pulse = 0.5 + 0.5 * Math.sin(rt * 7.5);
  const pushTravel = rt * 220;
  const snoopTravel = rt * 150;

  // Act 1 Lane emphasis
  const laneAOp = act1 * (k2 > 0.01 ? lerp(1, 0.36, k2) : 1);
  const laneBOp = act1 * (k2 > 0.01 ? 1 : k1 > 0.01 ? lerp(0.82, 0.36, k1) : 0.88);
  const lane1Cue0 = act1 * fade(t, T[0], T[1], 0.45);
  const lane1Cue1 = act1 * prog(t, T[1], 0.5);
  const lane2Cue01 = act1 * fade(t, T[0], T[2], 0.45);
  const lane2Cue2 = act1 * prog(t, T[2], 0.5);

  // Hero count-up in Cue 1 (finishes in 1.2s)
  const heroRate = countUp(t, T[1] + 0.15, 1.2, 174.8, 1);

  // CC-NIC Fig. 15 waterfall bars (Cue 2)
  const wfBars = [
    { label: "Baseline", pct: 100, delta: "100%", color: C.muted, k: prog(t, T[2] + 0.1, 0.5) },
    { label: "No TX reuse", pct: 80, delta: "−20%", color: CK.cxl, k: prog(t, T[2] + 0.45, 0.5) },
    { label: "4 KiB stride", pct: 50, delta: "−50%", color: CK.card, k: prog(t, T[2] + 0.8, 0.5) },
    { label: "Host pool", pct: 27, delta: "−73%", color: CK.trap, k: prog(t, T[2] + 1.15, 0.5) },
  ];

  // MRV-116 chart data (Cue 3)
  const mrv116Pts: [number, number][] = [
    [0, 47.7],
    [1, 47.5],
    [2, 48.1],
    [3, 47.8],
    [4, 47.2],
  ];
  const mrv116X0 = 152;
  const mrv116X1 = 875;
  const mrv116Y0 = 576;
  const mrv116Y1 = 274;
  const sx116 = linScale(0, 4, mrv116X0 + 36, mrv116X1 - 36);
  const sy116 = linScale(0, 60, mrv116Y0, mrv116Y1);

  // MRV-125 horizontal bar chart rows (Cues 4 & 5)
  const mrv125Rows: HBarRow[] = [
    {
      label: "4 MiB seq",
      sub: "64 B compact",
      value: 50.9,
      valueText: "50.9 M/s",
      color: CK.push,
      k: prog(t, T[4] + 0.15, 0.65),
    },
    {
      label: "4 MiB rand",
      sub: "64 B compact",
      value: 44.4,
      valueText: "44.4 M/s",
      color: CK.host,
      k: prog(t, T[4] + 0.45, 0.65),
    },
    {
      label: "2048 B rand",
      sub: "128M span · 4M act.",
      value: 22.3,
      valueText: "22.3 M/s",
      color: CK.trap,
      k: prog(t, T[4] + 0.85, 0.7),
      tag: "−50% alias",
    },
    {
      label: "2112 B rand",
      sub: "+64 B → rebound!",
      value: 43.9,
      valueText: "43.9 M/s",
      color: CK.ok,
      k: prog(t, T[5] + 0.2, 0.7),
    },
    {
      label: "128 MiB rand",
      sub: "true 128M active",
      value: 21.5,
      valueText: "21.5 M/s",
      color: C.muted,
      k: prog(t, T[5] + 0.7, 0.7),
      tag: "Host DDR5",
    },
  ];

  // Matrix column highlight states
  const colDmaBorder = k1 > 0.1 && k2 < 0.5 ? CK.sdp : C.line;
  const colCcnicBorder = k2 > 0.1 ? CK.cxl : C.line;
  const colDpBorder = k1 > 0.1 && k2 < 0.5 ? CK.push : k2 > 0.1 ? CK.ok : C.line;

  // LLC set morphing (32 buckets)
  const llcX0 = 994;
  const llcYBase = 738;
  const llcMaxH = 84;
  const bucketW = 22;
  const bucketGap = 4;

  return (
    <>
      <Stage>
        {/* ================= ACT 1: Cues 0..2 ================= */}
        {act1 > 0.01 && (
          <g opacity={act1}>
            {/* -------- LANE 1: Lifetime A -------- */}
            <g opacity={laneAOp}>
              <rect
                x={60}
                y={136}
                width={1800}
                height={180}
                rx={18}
                fill={C.panel}
                stroke={k1 > 0.1 && k2 < 0.5 ? CK.push : C.line}
                strokeWidth={k1 > 0.1 && k2 < 0.5 ? 2.8 : 1.8}
              />
              <rect x={60} y={136} width={10} height={180} rx={5} fill={CK.push} />
              <text x={88} y={170} fontSize={25} fontWeight={800} fill={CK.push} fontFamily={FONT}>
                Lifetime A · Host → NIC Transfer
              </text>
              <text x={1830} y={170} textAnchor="end" fontSize={20} fill={C.muted} fontFamily={FONT} opacity={lane1Cue0}>
                How long must the host hold a TX buffer before overwriting?
              </text>

              {/* Cue 0 overview inside Lane 1 */}
              {lane1Cue0 > 0.01 && (
                <g opacity={lane1Cue0 * k0}>
                  <rect x={120} y={198} width={280} height={86} rx={12} fill="#10182b" stroke={CK.host} strokeWidth={2} />
                  <text x={260} y={234} textAnchor="middle" fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
                    Host TX Buffer
                  </text>
                  <text x={260} y={263} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={MONO}>
                    L1D / stack 64 B
                  </text>
                  <Arrow x1={415} y1={241} x2={695} y2={241} color={CK.push} w={4.5} k={k0} />
                  <text x={555} y={226} textAnchor="middle" fontSize={20} fontWeight={700} fill={CK.push} fontFamily={MONO}>
                    PCIe Gen5 ×4 (lossless)
                  </text>
                  <rect x={715} y={198} width={300} height={86} rx={12} fill="#10182b" stroke={CK.card} strokeWidth={2} />
                  <text x={865} y={234} textAnchor="middle" fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
                    NIC / DPU Ingress
                  </text>
                  <text x={865} y={263} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={MONO}>
                    DMA read vs Direct Push
                  </text>
                  <rect x={1085} y={198} width={730} height={86} rx={12} fill="#0e1629" stroke={C.line} strokeWidth={1.6} strokeDasharray="7 5" />
                  <text x={1450} y={233} textAnchor="middle" fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
                    Key Distinction: Transfer ≠ Retransmission
                  </text>
                  <text x={1450} y={264} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={FONT}>
                    Conflating these two lifetimes leads to false coherence requirements
                  </text>
                </g>
              )}

              {/* Cue 1 detailed animation inside Lane 1 */}
              {lane1Cue1 > 0.01 && (
                <g opacity={lane1Cue1}>
                  {/* Traditional DMA comparison box (muted left) */}
                  <rect x={88} y={192} width={360} height={106} rx={12} fill="#131426" stroke={CK.sdp} strokeWidth={1.8} opacity={0.92} />
                  <text x={108} y={222} fontSize={20} fontWeight={800} fill={CK.sdp} fontFamily={FONT}>
                    Traditional DMA
                  </text>
                  <text x={108} y={252} fontSize={19} fill={C.text} fontFamily={FONT}>
                    Must wait for NIC DMA read
                  </text>
                  <text x={108} y={279} fontSize={18} fill={C.muted} fontFamily={MONO}>
                    + TX completion before reuse
                  </text>

                  {/* DirectPush single 64 B hot slot */}
                  <g>
                    {/* Glowing aura behind slot 0 */}
                    <rect
                      x={494 - 5}
                      y={218 - 5}
                      width={64}
                      height={64}
                      rx={12}
                      fill={CK.push}
                      opacity={0.18 + 0.22 * pulse}
                    />
                    <RingStrip
                      x={494}
                      y={218}
                      n={5}
                      cellW={54}
                      cellH={54}
                      gap={8}
                      label="Host TX: 1 hot 64 B slot"
                      state={(i) => (i === 0 ? "hot" : "empty")}
                      highlightRange={[0, 0]}
                      highlightColor={CK.push}
                    />
                    <text x={521} y={245} textAnchor="middle" dominantBaseline="central" fontSize={17} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                      64B
                    </text>
                    {/* Cross out unused pool slots 1..4 */}
                    {[1, 2, 3, 4].map((idx) => {
                      const cx = 494 + idx * 62;
                      return (
                        <g key={idx} opacity={0.45}>
                          <line x1={cx + 8} y1={226} x2={cx + 46} y2={264} stroke={C.dim} strokeWidth={2.5} />
                          <line x1={cx + 46} y1={226} x2={cx + 8} y2={264} stroke={C.dim} strokeWidth={2.5} />
                        </g>
                      );
                    })}
                    <text x={644} y={295} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                      0 TX pool · in-place reuse
                    </text>
                  </g>

                  {/* Push stream over lossless PCIe */}
                  <line x1={822} y1={232} x2={1145} y2={232} stroke={CK.push} strokeWidth={8} opacity={0.18} />
                  <Arrow x1={822} y1={232} x2={1145} y2={232} color={CK.push} w={4} k={k1} />
                  <PacketStream
                    pts={[
                      [826, 232],
                      [1135, 232],
                    ]}
                    travel={pushTravel}
                    gap={56}
                    size={12}
                    color={CK.push}
                    opacity={lane1Cue1}
                  />
                  <text x={984} y={214} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                    MOVDIR64B (by value)
                  </text>

                  {/* Crossed-out return DMA read arrow */}
                  <line x1={1140} y1={274} x2={832} y2={274} stroke={CK.trap} strokeWidth={2.5} strokeDasharray="6 6" opacity={0.65} />
                  <text x={990} y={297} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.trap} fontFamily={MONO}>
                    ✕ NIC NEVER reads host TX RAM
                  </text>

                  {/* Card BAR4 destination box */}
                  <rect x={1158} y={196} width={220} height={100} rx={12} fill="#161b2e" stroke={CK.card} strokeWidth={2.2} />
                  <text x={1268} y={232} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.card} fontFamily={FONT}>
                    Card BAR4
                  </text>
                  <text x={1268} y={261} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={MONO}>
                    48 MiB L3 copy
                  </text>
                  <text x={1268} y={284} textAnchor="middle" fontSize={17} fill={CK.ok} fontFamily={MONO}>
                    Lossless PCIe TLP
                  </text>
                </g>
              )}
            </g>

            {/* -------- LANE 2: Lifetime B -------- */}
            <g opacity={laneBOp}>
              <rect
                x={60}
                y={330}
                width={1800}
                height={188}
                rx={18}
                fill={C.panel}
                stroke={k2 > 0.1 ? CK.cxl : C.line}
                strokeWidth={k2 > 0.1 ? 2.8 : 1.8}
              />
              <rect x={60} y={330} width={10} height={188} rx={5} fill={CK.cxl} />
              <text x={88} y={364} fontSize={25} fontWeight={800} fill={CK.cxl} fontFamily={FONT}>
                Lifetime B · Upper-Layer TCP Retransmission Retention
              </text>
              <text x={1830} y={364} textAnchor="end" fontSize={20} fill={C.muted} fontFamily={FONT}>
                Host retains TX buffers until remote TCP ACK arrives
              </text>

              {/* Cue 0-1 preview inside Lane 2 */}
              {lane2Cue01 > 0.01 && (
                <g opacity={lane2Cue01 * k0}>
                  <rect x={120} y={392} width={520} height={96} rx={12} fill="#10182b" stroke={CK.host} strokeWidth={2} />
                  <text x={380} y={430} textAnchor="middle" fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
                    Host TCP Retransmission Pool (64 KiB – 128 MiB)
                  </text>
                  <text x={380} y={462} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={MONO}>
                    Lives in host LLC / DDR5 until remote ACK
                  </text>
                  <Arrow x1={655} y1={440} x2={835} y2={440} color={CK.cxl} w={3.5} dash="8 6" k={k0} />
                  <rect x={852} y={392} width={962} height={96} rx={12} fill="#10182b" stroke={C.line} strokeWidth={1.8} />
                  <text x={1333} y={430} textAnchor="middle" fontSize={22} fontWeight={700} fill={CK.cxl} fontFamily={FONT}>
                    Question: Does retaining buffers force cross-link cache coherence?
                  </text>
                  <text x={1333} y={462} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={FONT}>
                    CC-NIC (CXL coherent): YES (NIC caches stale copies) · DirectPush: NO (copied by value)
                  </text>
                </g>
              )}

              {/* Cue 2 detailed animation: CC-NIC cross-link invalidations + Fig. 15 waterfall */}
              {lane2Cue2 > 0.01 && (
                <g opacity={lane2Cue2}>
                  {/* Left: Coherent invalidation ping-pong diagram */}
                  <rect x={88} y={388} width={225} height={108} rx={12} fill="#151b2f" stroke={CK.host} strokeWidth={2} />
                  <text x={200} y={422} textAnchor="middle" fontSize={21} fontWeight={800} fill={C.text} fontFamily={FONT}>
                    Host TCP Pool
                  </text>
                  <text x={200} y={450} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                    Retains &amp; rewrites
                  </text>
                  <text x={200} y={476} textAnchor="middle" fontSize={18} fill={CK.cxl} fontFamily={MONO}>
                    56-core pool
                  </text>

                  {/* Red cross-link invalidation arrows */}
                  <line x1={325} y1={420} x2={610} y2={420} stroke={CK.trap} strokeWidth={7} opacity={0.18} />
                  <Arrow x1={325} y1={420} x2={610} y2={420} color={CK.trap} w={3.5} k={k2} />
                  <PacketStream
                    pts={[
                      [330, 420],
                      [602, 420],
                    ]}
                    travel={snoopTravel}
                    gap={64}
                    size={10}
                    color={CK.trap}
                    opacity={lane2Cue2}
                  />
                  <line x1={610} y1={462} x2={325} y2={462} stroke={CK.trap} strokeWidth={7} opacity={0.18} />
                  <Arrow x1={610} y1={462} x2={325} y2={462} color={CK.trap} w={3.5} k={k2} />
                  <PacketStream
                    pts={[
                      [602, 462],
                      [330, 462],
                    ]}
                    travel={snoopTravel + 30}
                    gap={64}
                    size={10}
                    color={CK.trap}
                    opacity={lane2Cue2}
                  />
                  <text x={468} y={404} textAnchor="middle" fontSize={18} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                    Cross-link Invalidations
                  </text>
                  <text x={468} y={491} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.cxl} fontFamily={MONO}>
                    Snoop / Back-Inv Tax
                  </text>

                  {/* CC-NIC Stale Cache box */}
                  <rect x={624} y={388} width={235} height={108} rx={12} fill="#251c17" stroke={CK.cxl} strokeWidth={2.2} />
                  <text x={741} y={422} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.cxl} fontFamily={FONT}>
                    CC-NIC Cache
                  </text>
                  <text x={741} y={450} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={MONO}>
                    Holds stale copies
                  </text>
                  <text x={741} y={476} textAnchor="middle" fontSize={18} fill={CK.trap} fontFamily={MONO}>
                    Coherence thrash!
                  </text>

                  {/* Right: Animated 3-step waterfall for CC-NIC Fig. 15 */}
                  <rect x={896} y={382} width={940} height={122} rx={12} fill="#0f1527" stroke={C.line} strokeWidth={1.6} />
                  <text x={918} y={407} fontSize={19} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
                    CC-NIC Fig. 15 (ASPLOS &apos;24 · 56 cores) — Cumulative Throughput Drop
                  </text>
                  {wfBars.map((b, i) => {
                    const bx = 924 + i * 224;
                    const maxW = 192;
                    const barW = maxW * (b.pct / 100) * clamp01(b.k);
                    return (
                      <g key={b.label} opacity={Math.max(0.15, b.k)}>
                        <text x={bx} y={435} fontSize={18} fontWeight={700} fill={C.text} fontFamily={FONT}>
                          {b.label}
                        </text>
                        <rect x={bx} y={445} width={maxW} height={26} rx={6} fill="#182038" stroke={C.line} strokeWidth={1.2} />
                        <rect x={bx} y={445} width={barW} height={26} rx={6} fill={b.color} opacity={0.92} />
                        <text x={bx + maxW} y={435} textAnchor="end" fontSize={20} fontWeight={800} fill={b.color} fontFamily={MONO}>
                          {b.delta}
                        </text>
                        <text x={bx} y={491} fontSize={16} fill={C.muted} fontFamily={MONO}>
                          {i === 0 ? "Hot stack" : i === 1 ? "Step 1: retx" : i === 2 ? "Step 2: 4 KiB" : "Step 3: pool"}
                        </text>
                        {i < 3 && (
                          <text x={bx + maxW + 15} y={463} textAnchor="middle" fontSize={20} fontWeight={800} fill={C.dim} fontFamily={MONO}>
                            →
                          </text>
                        )}
                      </g>
                    );
                  })}
                </g>
              )}
            </g>
          </g>
        )}

        {/* ================= ACT 2: Cues 3..5 ================= */}
        {act2 > 0.01 && (
          <g opacity={act2}>
            {/* Left Panel: MRV-116 Sequential Pool Retention */}
            <rect x={60} y={136} width={876} height={738} rx={18} fill={C.panel} stroke={CK.push} strokeWidth={2.2} />
            <text x={92} y={178} fontSize={26} fontWeight={800} fill={CK.push} fontFamily={FONT}>
              MRV-116 · Sequential Retention Pool (W=64, 64 B)
            </text>
            <text x={92} y={208} fontSize={20} fill={C.muted} fontFamily={MONO}>
              2,000× pool growth past 60 MiB Host LLC → Rock-flat throughput
            </text>

            {/* Chart axes */}
            <Axes
              x0={mrv116X0}
              x1={mrv116X1}
              y0={mrv116Y0}
              y1={mrv116Y1}
              sx={sx116}
              sy={sy116}
              xticks={[
                [0, "stack"],
                [1, "64 KiB"],
                [2, "4 MiB"],
                [3, "32 MiB"],
                [4, "128 MiB"],
              ]}
              yticks={[
                [0, "0"],
                [20, "20"],
                [40, "40"],
                [60, "60"],
              ]}
              ylabel="Throughput (M/s)"
              size={21}
            />

            {/* Vertical dashed marker for 60 MiB Host LLC between 32 MiB (3) and 128 MiB (4) */}
            <g opacity={prog(t, T[3] + 0.5, 0.5)}>
              <line
                x1={sx116(3.5)}
                y1={mrv116Y1 - 6}
                x2={sx116(3.5)}
                y2={mrv116Y0}
                stroke={CK.cxl}
                strokeWidth={2.6}
                strokeDasharray="8 6"
              />
              <rect x={sx116(3.5) - 94} y={388} width={188} height={34} rx={8} fill="#252113" stroke={CK.cxl} strokeWidth={1.6} />
              <text x={sx116(3.5)} y={405} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
                60 MiB Host LLC
              </text>
            </g>

            {/* Subtle shaded area under flat line */}
            {k3 > 0.1 && (
              <polygon
                points={`${sx116(0)},${mrv116Y0} ${mrv116Pts
                  .slice(0, Math.max(2, Math.round(k3 * 5)))
                  .map(([x, y]) => `${sx116(x)},${sy116(y)}`)
                  .join(" ")} ${sx116(Math.min(4, (mrv116Pts.length - 1) * k3))},${mrv116Y0}`}
                fill={CK.push}
                opacity={0.1}
              />
            )}

            {/* Flat cyan line */}
            <Series pts={mrv116Pts} sx={sx116} sy={sy116} k={k3} color={CK.push} w={5.5} r={8} />

            {/* Point labels */}
            {mrv116Pts.map(([xv, yv], idx) => {
              const showPt = clamp01((k3 * 4 - idx + 0.4) / 0.5);
              return (
                <text
                  key={idx}
                  x={sx116(xv)}
                  y={sy116(yv) - 18}
                  textAnchor="middle"
                  fontSize={21}
                  fontWeight={800}
                  fill={CK.push}
                  fontFamily={MONO}
                  opacity={showPt}
                >
                  {yv.toFixed(1)}
                </text>
              );
            })}

            {/* Callout badge inside chart */}
            <g opacity={prog(t, T[3] + 1.0, 0.5)}>
              <rect x={198} y={462} width={455} height={58} rx={12} fill="#0e2230" stroke={CK.push} strokeWidth={2} />
              <text x={425} y={487} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                47.5 → 47.1 M/s (−0.9% summary)
              </text>
              <text x={425} y={509} textAnchor="middle" fontSize={17} fill={C.muted} fontFamily={MONO}>
                Idle W=1 RTT rock-constant: 0.91 µs
              </text>
            </g>

            {/* Right Panel: MRV-125 Random-Pool Detective Story (frame visible at Cue 3, data populates at Cues 4..5) */}
            <g>
              <rect
                x={964}
                y={136}
                width={896}
                height={738}
                rx={18}
                fill={C.panel}
                stroke={k5 > 0.3 ? CK.ok : k4 > 0.1 ? CK.trap : C.line}
                strokeWidth={k4 > 0.1 ? 2.2 : 1.8}
              />
              <text x={994} y={178} fontSize={26} fontWeight={800} fill={k5 > 0.3 ? CK.ok : k4 > 0.1 ? CK.trap : C.muted} fontFamily={FONT}>
                MRV-125 · Random-Pool Stride Detective (p1)
              </text>
              <text x={994} y={208} fontSize={20} fill={C.muted} fontFamily={MONO}>
                N = 65,536 pkts · avoiding PEM0 cliff · random vs strided pools
              </text>

              {/* Cue 3 Preview inside Right Panel (crossfades cleanly into bars at T[4]) */}
              {fade(t, T[3], T[4], 0.45) > 0.01 && (
                <g opacity={fade(t, T[3], T[4], 0.45)}>
                  <rect x={1004} y={286} width={816} height={310} rx={16} fill="#101729" stroke={C.line} strokeWidth={1.8} strokeDasharray="8 6" />
                  <text x={1412} y={366} textAnchor="middle" fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
                    Sequential retention stays flat across 128 MiB...
                  </text>
                  <text x={1412} y={416} textAnchor="middle" fontSize={23} fontWeight={700} fill={CK.cxl} fontFamily={FONT}>
                    Next: What about a random 2 KiB-stride pool?
                  </text>
                  <text x={1412} y={474} textAnchor="middle" fontSize={20} fill={C.muted} fontFamily={MONO}>
                    MRV-125 · 165 hardware runs · p1 shaped traffic
                  </text>
                  <text x={1412} y={514} textAnchor="middle" fontSize={19} fill={CK.push} fontFamily={MONO}>
                    Isolating host LLC set aliasing vs cross-link coherence
                  </text>
                </g>
              )}

              {k4 > 0.01 && (
                <g opacity={k4}>
                {/* Horizontal Bar Chart (Bars 0..2 in Cue 4, Bars 3..4 in Cue 5) */}
                <HBarChart
                  x={984}
                  y={236}
                  w={854}
                  labelW={218}
                  barH={42}
                  gap={14}
                  max={60}
                  rows={t >= T[5] ? mrv125Rows : mrv125Rows.slice(0, 3)}
                  opacity={1}
                />

                {/* Mini LLC-Set Aliasing Diagram (morphs smoothly from Cue 4 -> Cue 5) */}
                <rect x={986} y={534} width={852} height={240} rx={14} fill="#0d1324" stroke={k5 > 0.3 ? CK.ok : CK.trap} strokeWidth={1.8} />

                {/* Header inside LLC-set box: crossfade Cue 4 vs Cue 5 */}
                <text
                  x={1008}
                  y={566}
                  fontSize={21}
                  fontWeight={800}
                  fill={CK.trap}
                  fontFamily={MONO}
                  opacity={1 - k5}
                >
                  2048 B = 2^11 → low 5 LLC set bits locked to 00000
                </text>
                <text
                  x={1008}
                  y={594}
                  fontSize={19}
                  fill={C.text}
                  fontFamily={FONT}
                  opacity={1 - k5}
                >
                  All 4 MiB active slots collide into 1/32 of LLC (1.875 MiB)!
                </text>

                <text
                  x={1008}
                  y={566}
                  fontSize={21}
                  fontWeight={800}
                  fill={CK.ok}
                  fontFamily={MONO}
                  opacity={k5}
                >
                  2112 B = 2048 + 64 B → odd line stride breaks aliasing!
                </text>
                <text
                  x={1008}
                  y={594}
                  fontSize={19}
                  fill={C.text}
                  fontFamily={FONT}
                  opacity={k5}
                >
                  Slots spread evenly across all 32/32 LLC set buckets → 43.9 M/s
                </text>

                {/* 32 LLC set buckets */}
                {Array.from({ length: 32 }).map((_, idx) => {
                  const bx = llcX0 + idx * (bucketW + bucketGap);
                  const h4 = idx === 0 ? llcMaxH : 6;
                  const h5 = 30 + ((idx * 7) % 9);
                  const bh = lerp(h4, h5, k5);
                  const fillCol =
                    idx === 0
                      ? k5 > 0.45
                        ? CK.ok
                        : CK.trap
                      : k5 > 0.25
                        ? CK.ok
                        : "#1d2640";
                  const strokeCol =
                    idx === 0
                      ? k5 > 0.45
                        ? CK.ok
                        : CK.trap
                      : k5 > 0.25
                        ? `${CK.ok}99`
                        : C.line;
                  return (
                    <g key={idx}>
                      {/* Background slot outline */}
                      <rect x={bx} y={llcYBase - llcMaxH} width={bucketW} height={llcMaxH} rx={4} fill="#121a30" stroke="#1e2945" strokeWidth={1} />
                      {/* Filled height */}
                      <rect
                        x={bx}
                        y={llcYBase - bh}
                        width={bucketW}
                        height={bh}
                        rx={4}
                        fill={fillCol}
                        stroke={strokeCol}
                        strokeWidth={1.3}
                        opacity={idx === 0 && k5 < 0.4 ? 0.8 + 0.2 * pulse : 0.9}
                      />
                    </g>
                  );
                })}
                {/* Bucket axis annotations */}
                <text x={llcX0 + 11} y={llcYBase + 22} textAnchor="middle" fontSize={16} fontWeight={700} fill={k5 > 0.45 ? CK.ok : CK.trap} fontFamily={MONO}>
                  #0
                </text>
                <text x={llcX0 + 16 * 26} y={llcYBase + 22} textAnchor="middle" fontSize={17} fill={C.muted} fontFamily={MONO}>
                  Host LLC Set Buckets (32 stride-aliased groups)
                </text>
                <text x={llcX0 + 31 * 26 + 11} y={llcYBase + 22} textAnchor="middle" fontSize={16} fill={C.muted} fontFamily={MONO}>
                  #31
                </text>
                </g>
              )}
            </g>
          </g>
        )}
      </Stage>

      {/* ================= HTML OVERLAYS (OUTSIDE <Stage>) ================= */}

      {/* Act 1 Cue 1 HeroStat */}
      {act1 * lane1Cue1 > 0.01 && (
        <HeroStat
          x={1398}
          y={150}
          w={438}
          h={152}
          value={`${heroRate} M/s`}
          label="97.7% Gen5 ×4 line rate"
          sub="1 × 64 B buffer · 11.19 GB/s · 0 pool"
          color={CK.push}
          opacity={act1 * laneAOp * lane1Cue1}
          dy={lerp(18, 0, k1)}
        />
      )}

      {/* Act 1 Bottom 3-Column Comparison Matrix (Cues 0..2) */}
      {act1 > 0.01 && (
        <>
          <Card
            x={60}
            y={534}
            w={580}
            h={336}
            border={colDmaBorder}
            title="Traditional DMA"
            titleColor={CK.sdp}
            style={{ opacity: act1 * k0 * (k2 > 0.1 ? 0.55 : 1) }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 8 }}>
              <div>
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>LIFETIME A · TRANSFER</div>
                <div style={{ fontSize: 23, fontWeight: 700, color: C.text, marginTop: 4 }}>Wait for NIC DMA read + completion</div>
              </div>
              <div>
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>LIFETIME B · TCP RETENTION</div>
                <div style={{ fontSize: 23, fontWeight: 700, color: C.text, marginTop: 4 }}>Host RAM pool (never cached on NIC)</div>
              </div>
              <div>
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>COHERENCE TAX</div>
                <div style={{ fontSize: 23, fontWeight: 700, color: CK.sdp, marginTop: 4 }}>Zero snoops · high descriptor latency</div>
              </div>
            </div>
          </Card>

          <Card
            x={670}
            y={534}
            w={580}
            h={336}
            border={colCcnicBorder}
            title="CC-NIC (Coherent CXL)"
            titleColor={CK.cxl}
            style={{
              opacity: act1 * k0 * (k1 > 0.1 && k2 < 0.1 ? 0.58 : 1),
              boxShadow: k2 > 0.2 ? `0 0 28px ${CK.cxl}28` : undefined,
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 8 }}>
              <div>
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>LIFETIME A · TRANSFER</div>
                <div style={{ fontSize: 23, fontWeight: 700, color: C.text, marginTop: 4 }}>Cache-coherent line pull / push</div>
              </div>
              <div>
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>LIFETIME B · TCP RETENTION</div>
                <div style={{ fontSize: 23, fontWeight: 700, color: CK.trap, marginTop: 4 }}>Stale NIC cache copies → invalidations</div>
              </div>
              <div>
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>COHERENCE TAX (FIG. 15)</div>
                <div style={{ fontSize: 23, fontWeight: 800, color: CK.trap, fontFamily: MONO, marginTop: 4 }}>−20% → −50% (4K) → −73% (pool)</div>
              </div>
            </div>
          </Card>

          <Card
            x={1280}
            y={534}
            w={580}
            h={336}
            border={colDpBorder}
            title="DirectPush (Lossless PCIe)"
            titleColor={CK.push}
            style={{
              opacity: act1 * k0,
              boxShadow: k1 > 0.2 && k2 < 0.2 ? `0 0 28px ${CK.push}28` : undefined,
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 8 }}>
              <div>
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>LIFETIME A · TRANSFER</div>
                <div style={{ fontSize: 23, fontWeight: 700, color: CK.push, marginTop: 4 }}>64 B by value → instant reuse (174.8 M/s)</div>
              </div>
              <div>
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>LIFETIME B · TCP RETENTION</div>
                <div style={{ fontSize: 23, fontWeight: 700, color: CK.ok, marginTop: 4 }}>Host-local pool · never enters NIC cache</div>
              </div>
              <div>
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>COHERENCE TAX</div>
                <div style={{ fontSize: 22, fontWeight: 800, color: CK.ok, fontFamily: MONO, marginTop: 4 }}>0 invalidations · 99.99993% DDIO</div>
              </div>
            </div>
          </Card>
        </>
      )}

      {/* Act 2 Left Panel Green Shield VerdictRow (Cue 3+) */}
      {act2 > 0.01 && (
        <VerdictRow
          x={92}
          y={654}
          w={812}
          badge="99.99993%"
          badgeColor={CK.ok}
          title="RX DDIO Hit Rate · 0 misses at 128 MiB"
          detail="297 closed-loop runs · 816M replies · pool never enters NIC cache"
          opacity={act2 * prog(t, T[3] + 0.85, 0.55)}
          dy={lerp(18, 0, prog(t, T[3] + 0.85, 0.55))}
        />
      )}

      {/* Act 2 Left Panel secondary note */}
      {act2 > 0.01 && (
        <Txt
          x={92}
          y={808}
          w={812}
          size={18}
          color={C.muted}
          style={{ opacity: act2 * prog(t, T[3] + 1.1, 0.5), fontFamily: MONO }}
        >
          Measured: stack 47.7 · 64K 47.5 · 4M 48.1 · 32M 47.8 · 128M 47.2 M/s
        </Txt>
      )}

      {/* Act 2 Right Panel Bottom Pill / Verdict in Cue 5 */}
      {k5 > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 986,
            top: 792,
            width: 852,
            textAlign: "center",
            fontSize: 19,
            fontWeight: 800,
            color: C.bg,
            background: CK.ok,
            borderRadius: 999,
            padding: "9px 18px",
            boxSizing: "border-box",
            fontFamily: MONO,
            opacity: act2 * prog(t, T[5] + 0.9, 0.5),
            transform: `translateY(${lerp(14, 0, prog(t, T[5] + 0.9, 0.5))}px)`,
          }}
        >
          NIC DDIO = 100% (0 misses) · Host DDR5 Bound · Zero Coherence Tax
        </div>
      )}
    </>
  );
}

export const C04: SceneDef = {
  id: "C04",
  title: "2 \u00b7 TX Reuse & Zero Coherence Tax",
  stage: 1,
  source: "MRV-149 \u00b7 MRV-116 (204 runs) \u00b7 MRV-125 (165 runs) \u00b7 816M writes",
  dur: DUR,
  cues,
  C: Body,
};
