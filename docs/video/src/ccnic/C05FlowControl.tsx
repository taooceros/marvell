import React from "react";
import {
  Arrow,
  Axes,
  C,
  Card,
  Cue,
  FONT,
  HLine,
  MONO,
  PacketStream,
  Pill,
  QueueGauge,
  SceneDef,
  Series,
  Stage,
  Txt,
  clamp01,
  countUp,
  fade,
  lerp,
  linScale,
  logScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { CK, HeroStat, RingStrip, VerdictRow } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 14.58, "Myth buster: **~13 M/s is NOT the channel cap** (raw push hits **175 M/s**, 1C RPC **50.4 M/s**)—it is a concurrent polling collision trap.", "Report three tames the thirteen million per second bridge cliff. First, thirteen million is not the channel cap—raw push hits one hundred seventy-five million and one-core R P C hits fifty million. It is a specific polling collision trap."], // 0
  [14.93, 28.07, "Unshaped at **16–30 M/s**, serialized writes back up into **48–248** host CHA entries, exploding open-loop **p99** to **44–51 µs**.", "Under open-loop arrivals at sixteen to thirty million per second, serialized writes back up into forty-eight to two hundred forty-eight host C H A entries, exploding tail latency to forty-four to fifty-one microseconds."], // 1
  [28.42, 43.98, "A **host credit cap** (`W=32–48`) empties CHA (**1.4–3.1**), cuts **p99 6–64×** (**44.5 → 7.01 µs** Poisson), and lifts 4C cliff **+43%**.", "Adding a host in-flight credit gate drains C H A backlog down to one point four to three point one entries, cuts tail latency six to sixty-four times—down to seven point zero one microseconds at twenty million Poisson—and eliminates the four-core collapse."], // 2
  [44.33, 52.01, "Honest boundary: can host-only credits hold **≤ 2 µs** at **20 M/s**? No—PMUs reveal an unavoidable **[18, 25] arithmetic gap**.", "Can host-only credits hold two microseconds at twenty million per second? Hardware counters prove no, due to a strict arithmetic gap."], // 3
  [52.36, 68.46, "Serial drain is **0.58 µs + W × 77 ns** → `≤ 2 µs` needs **W ≤ 18**, while **20 M/s** at `1.25 µs` RTT needs **W ≥ 25** (Little's Law).", "Serial drain takes zero point five eight microseconds plus W times seventy-seven nanoseconds. Staying under two microseconds needs W at most eighteen, yet twenty million per second at one point two five microseconds requires at least twenty-five in flight."], // 4
  [68.81, 82.29, "Host-only `≤ 2 µs` safe zone: **≤ 13.5 M/s** (1C) / **≤ 10 M/s** (4C). Static pauses hurt **0.9 µs** idle RTT → **on-demand busy-burst backoff**.", "So host-only shaping holds two microseconds up to thirteen point five million on one card core and ten million on four. Static pauses hurt zero point nine microsecond idle latency, pointing to on-demand busy-burst backoff."], // 5
];
const DUR = 83.69; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Act 1: Cue 0 (Myth-buster + PEM0 collision trap schematic)
  const cue0Op = fade(t, T[0], T[1], 0.5);
  const k0 = prog(t, T[0], 0.7);

  // Act 2: Cues 1..5 (Persistent right p99 chart + morphing left panel)
  const mainOp = prog(t, T[1], 0.5);
  const leftFlowOp = fade(t, T[1], T[3], 0.5);
  const leftMathOp = prog(t, T[3], 0.5);

  const k1 = prog(t, T[1], 0.8);
  const k1Curve = prog(t, T[1] + 0.35, 1.1);
  const k2 = prog(t, T[2], 0.75);
  const k2Curve = prog(t, T[2] + 0.35, 1.1);
  const k3 = prog(t, T[3], 0.7);
  const k3Preview = fade(t, T[3], T[4], 0.45);
  const k4 = prog(t, T[4], 0.75);
  const k5 = prog(t, T[5], 0.75);

  // Continuous ambient motion
  const pulse = 0.5 + 0.5 * Math.sin(rt * 7.0);
  const fastTravel = rt * 230;
  const pollTravel = rt * 190;

  // Host CHA gauge level: surges up in Cue 1, drains in Cue 2
  const chaLevel = lerp(0.1, 0.88, prog(t, T[1] + 0.25, 1.0)) * (1 - k2) + 0.09 * k2;
  const chaEntries =
    k2 < 0.05
      ? `${countUp(t, T[1] + 0.25, 1.1, 248, 0, 48)}`
      : `${lerp(248, 2.2, k2).toFixed(1)}`;

  // Right panel chart: MRV-115 / MRV-123 open-loop tot p99 (us) vs rate (M/s)
  const cx0 = 1054;
  const cx1 = 1812;
  const cy0 = 678;
  const cy1 = 272;
  const sx = linScale(0, 21.5, cx0, cx1);
  const sy = logScale(0.7, 70, cy0, cy1);

  const unshapedPts: [number, number][] = [
    [1, 0.98],
    [5, 1.66],
    [10, 1.78],
    [13.5, 1.75],
    [16, 35.15],
    [20, 46.32],
  ];
  const creditPts: [number, number][] = [
    [1, 0.98],
    [5, 1.67],
    [10, 1.78],
    [13.5, 1.72],
    [16, 3.04],
    [20, 7.01],
  ];

  // Number line scale for W in [0, 40] (Cue 4)
  const nlX0 = 104;
  const nlX1 = 876;
  const sw = linScale(0, 40, nlX0, nlX1);
  const nlY = 414;

  return (
    <>
      <Stage>
        {/* ================= CUE 0: Myth Buster + PEM0 Collision Trap ================= */}
        {cue0Op > 0.01 && (
          <g opacity={cue0Op}>
            {/* Top Myth Buster Banner */}
            <rect x={60} y={136} width={1800} height={134} rx={18} fill={C.panel} stroke={CK.push} strokeWidth={2.2} />
            <rect x={60} y={136} width={10} height={134} rx={5} fill={CK.push} />
            <text x={92} y={182} fontSize={28} fontWeight={800} fill={CK.push} fontFamily={FONT}>
              Myth Buster: ~13.1 M/s is NOT the PCIe Channel Ceiling
            </text>
            <text x={92} y={220} fontSize={21} fill={C.muted} fontFamily={FONT}>
              Same Gen5 ×4 link &amp; CN106 card sustain 4×–13× higher rates without collision:
            </text>

            {/* 3 Speed Proof Badges on the right of the banner */}
            {[
              { x: 1125, label: "Raw Push", val: "175.2 M/s", col: CK.push },
              { x: 1372, label: "1C RPC", val: "50.4 M/s", col: CK.ok },
              { x: 1612, label: "Sat. Unidir", val: "128 M/s", col: CK.peer },
            ].map((b, i) => {
              const kb = prog(t, T[0] + 0.25 + i * 0.2, 0.55);
              return (
                <g key={b.label} opacity={kb}>
                  <rect x={b.x} y={154} width={224} height={98} rx={14} fill="#0e172a" stroke={b.col} strokeWidth={2.2} />
                  <text x={b.x + 112} y={185} textAnchor="middle" fontSize={18} fontWeight={700} fill={C.muted} fontFamily={MONO}>
                    {b.label}
                  </text>
                  <text x={b.x + 112} y={228} textAnchor="middle" fontSize={32} fontWeight={800} fill={b.col} fontFamily={MONO}>
                    {b.val}
                  </text>
                </g>
              );
            })}

            {/* Main Trap Schematic Panel */}
            <rect x={60} y={292} width={1800} height={578} rx={18} fill={C.panel} stroke={CK.trap} strokeWidth={2.4} />
            <text x={94} y={338} fontSize={27} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
              Root Cause · PEM0 / IOBN Inbound Bridge Serialization Trap
            </text>
            <text x={94} y={370} fontSize={21} fill={C.muted} fontFamily={MONO}>
              Concurrent closed-loop traffic + relentless card polling on empty next slot → single-file 76.4–77 ns/msg drain
            </text>

            {/* Host Box */}
            <rect x={96} y={422} width={356} height={272} rx={16} fill="#10182d" stroke={CK.host} strokeWidth={2.4} />
            <text x={274} y={462} textAnchor="middle" fontSize={25} fontWeight={800} fill={CK.host} fontFamily={FONT}>
              HOST · saturn
            </text>
            <text x={274} y={494} textAnchor="middle" fontSize={20} fill={C.text} fontFamily={MONO}>
              MOVDIR64B → BAR4
            </text>
            <rect x={128} y={522} width={292} height={72} rx={10} fill="#16213d" stroke={CK.push} strokeWidth={1.8} />
            <text x={274} y={553} textAnchor="middle" fontSize={20} fontWeight={700} fill={CK.push} fontFamily={MONO}>
              64 B Posted Writes
            </text>
            <text x={274} y={579} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              Unshaped burst arrival
            </text>
            <text x={274} y={640} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={FONT}>
              Host CHA sits nearly empty
            </text>
            <text x={274} y={668} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              until offered rate &gt; 13.1 M/s
            </text>

            {/* PCIe inbound push stream */}
            <line x1={462} y1={556} x2={658} y2={556} stroke={CK.push} strokeWidth={9} opacity={0.18} />
            <Arrow x1={462} y1={556} x2={658} y2={556} color={CK.push} w={4.5} k={k0} />
            <PacketStream
              pts={[
                [468, 556],
                [648, 556],
              ]}
              travel={fastTravel}
              gap={52}
              size={12}
              color={CK.push}
              opacity={cue0Op}
            />
            <text x={560} y={534} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              PCIe Gen5 ×4
            </text>

            {/* Card PEM0 / IOBN Bridge & Mailbox Slots */}
            <rect x={672} y={406} width={564} height={310} rx={16} fill="#1d1321" stroke={CK.trap} strokeWidth={2.8} />
            <text x={954} y={446} textAnchor="middle" fontSize={25} fontWeight={800} fill={CK.card} fontFamily={FONT}>
              CN106 · PEM0 / IOBN Bridge
            </text>
            <text x={954} y={476} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={MONO}>
              BAR4 Mailbox Ring Slots (Card L3)
            </text>

            <RingStrip
              x={728}
              y={522}
              n={8}
              cellW={48}
              cellH={56}
              gap={8}
              state={(i) => (i < 4 ? "hot" : i === 4 ? "trap" : "empty")}
              highlightRange={[4, 4]}
              highlightColor={CK.trap}
            />
            {/* Pulsing collision halo on slot 4 */}
            <circle
              cx={728 + 4 * 56 + 24}
              cy={550}
              r={34 + 8 * pulse}
              fill="none"
              stroke={CK.trap}
              strokeWidth={3.5}
              opacity={0.85 - 0.45 * pulse}
            />
            <text x={728 + 4 * 56 + 24} y={550} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={900} fill={CK.trap} fontFamily={MONO}>
              !
            </text>
            <text x={954} y={620} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              Write vs Empty-Slot Poll Collision
            </text>
            <text x={954} y={652} textAnchor="middle" fontSize={19} fill={C.text} fontFamily={FONT}>
              Forces single-file serialization at inbound bridge
            </text>
            <rect x={754} y={670} width={400} height={34} rx={8} fill="#341319" stroke={CK.trap} strokeWidth={1.6} />
            <text x={954} y={688} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              76.4–77 ns/msg → ~13.1 M/s cliff
            </text>

            {/* Card ARM Core aggressively polling */}
            <rect x={1446} y={422} width={376} height={272} rx={16} fill="#1c1624" stroke={CK.card} strokeWidth={2.4} />
            <text x={1634} y={462} textAnchor="middle" fontSize={25} fontWeight={800} fill={CK.card} fontFamily={FONT}>
              Card ARM Core
            </text>
            <text x={1634} y={494} textAnchor="middle" fontSize={20} fill={C.text} fontFamily={MONO}>
              Tight empty-slot poll loop
            </text>
            <rect x={1476} y={522} width={316} height={72} rx={10} fill="#261922" stroke={CK.trap} strokeWidth={1.8} />
            <text x={1634} y={552} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              while (slot[next] == 0)
            </text>
            <text x={1634} y={578} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              Hammers next BAR4 line
            </text>
            <text x={1634} y={640} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={FONT}>
              Even 4 KiB stride stays 13.1–13.6 M/s
            </text>
            <text x={1634} y={668} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              (MRV-112: bridge, not cache line)
            </text>

            {/* Aggressive poll arrows from ARM back to PEM0 */}
            <line x1={1436} y1={556} x2={1248} y2={556} stroke={CK.trap} strokeWidth={9} opacity={0.18} />
            <Arrow x1={1436} y1={556} x2={1248} y2={556} color={CK.trap} w={4.5} k={k0} />
            <PacketStream
              pts={[
                [1430, 556],
                [1256, 556],
              ]}
              travel={pollTravel}
              gap={44}
              size={11}
              color={CK.trap}
              opacity={cue0Op}
            />
            <text x={1342} y={534} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              Empty Poll!
            </text>

            {/* Bottom summary strip inside Cue 0 */}
            <rect x={96} y={744} width={1726} height={94} rx={14} fill="#0e1424" stroke={C.line} strokeWidth={1.6} />
            <text x={959} y={782} textAnchor="middle" fontSize={23} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Consequence: If host pushes faster than 13.1 M/s without flow control, writes back up across PCIe into Host CHA!
            </text>
            <text x={959} y={818} textAnchor="middle" fontSize={20} fill={C.muted} fontFamily={MONO}>
              Next: Measure open-loop tail latency (MRV-115 · 459 runs) &amp; credit shaping (MRV-123 · 255 runs)
            </text>
          </g>
        )}

        {/* ================= CUES 1..5: Split-Screen Evolving Composition ================= */}
        {mainOp > 0.01 && (
          <g opacity={mainOp}>
            {/* ---------- LEFT PANEL FRAME ---------- */}
            <rect
              x={60}
              y={136}
              width={864}
              height={738}
              rx={18}
              fill={C.panel}
              stroke={k4 > 0.2 ? CK.cxl : k2 > 0.2 && k3 < 0.2 ? CK.push : CK.trap}
              strokeWidth={2.2}
            />

            {/* ---------- LEFT PHASE A: Cues 1..2 (Host CHA Queue + Credit Gate) ---------- */}
            {leftFlowOp > 0.01 && (
              <g opacity={leftFlowOp}>
                <text x={92} y={178} fontSize={26} fontWeight={800} fill={k2 > 0.2 ? CK.push : CK.trap} fontFamily={FONT}>
                  {k2 > 0.4 ? "Host Credit Gate (W = 32–48) Drains CHA" : "Unshaped Traffic Floods Host CHA"}
                </text>
                <text x={92} y={208} fontSize={20} fill={C.muted} fontFamily={MONO}>
                  {k2 > 0.4 ? "Cap in-flight writes before PEM0 bridge serializes" : "16–30 M/s offered load > 13.1 M/s serial bridge drain"}
                </text>

                {/* Host CPU + Live CHA QueueGauge */}
                <rect x={92} y={238} width={350} height={366} rx={16} fill="#10182c" stroke={k2 > 0.4 ? CK.ok : CK.trap} strokeWidth={2.2} />
                <text x={267} y={274} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.host} fontFamily={FONT}>
                  HOST · saturn
                </text>
                <text x={267} y={302} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                  CHA Uncore Write Queue
                </text>

                <QueueGauge
                  x={110}
                  y={324}
                  w={82}
                  h={222}
                  level={chaLevel}
                  color={CK.ok}
                  hot={CK.trap}
                  hotAt={0.45}
                  slots={8}
                  label="Host CHA"
                  fontSize={18}
                  marks={[
                    { at: 0.14, label: "1.4–3.1", color: CK.ok },
                    { at: 0.85, label: "48–248", color: CK.trap },
                  ]}
                />

                {/* Live CHA entry readout box */}
                <rect x={274} y={404} width={152} height={98} rx={12} fill="#161f36" stroke={k2 > 0.4 ? CK.ok : CK.trap} strokeWidth={2} />
                <text x={350} y={432} textAnchor="middle" fontSize={17} fontWeight={700} fill={C.muted} fontFamily={MONO}>
                  CHA Backlog
                </text>
                <text x={350} y={472} textAnchor="middle" fontSize={34} fontWeight={800} fill={k2 > 0.4 ? CK.ok : CK.trap} fontFamily={MONO}>
                  {chaEntries}
                </text>
                <text x={350} y={493} textAnchor="middle" fontSize={15} fill={C.muted} fontFamily={MONO}>
                  entries
                </text>

                {/* PCIe Link + Credit Gate */}
                <line x1={454} y1={420} x2={656} y2={420} stroke={k2 > 0.4 ? CK.push : CK.trap} strokeWidth={8} opacity={0.2} />
                <Arrow x1={454} y1={420} x2={656} y2={420} color={k2 > 0.4 ? CK.push : CK.trap} w={4} k={k1} />
                <PacketStream
                  pts={[
                    [460, 420],
                    [646, 420],
                  ]}
                  travel={rt * (k2 > 0.4 ? 140 : 250)}
                  gap={k2 > 0.4 ? 76 : 36}
                  size={11}
                  color={k2 > 0.4 ? CK.push : CK.trap}
                  opacity={leftFlowOp}
                />

                {/* Glowing Cyan Credit Gate snapping onto the link in Cue 2 */}
                {k2 > 0.01 && (
                  <g opacity={k2} transform={`translate(0, ${lerp(-36, 0, k2)})`}>
                    <rect x={488} y={342} width={136} height={154} rx={14} fill="#0c2638" stroke={CK.push} strokeWidth={3} />
                    <text x={556} y={376} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.push} fontFamily={FONT}>
                      CREDIT
                    </text>
                    <text x={556} y={400} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.push} fontFamily={FONT}>
                      GATE
                    </text>
                    <rect x={502} y={416} width={108} height={34} rx={8} fill="#143a52" stroke={CK.push} strokeWidth={1.6} />
                    <text x={556} y={434} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={C.text} fontFamily={MONO}>
                      W=32–48
                    </text>
                    <text x={556} y={474} textAnchor="middle" fontSize={15} fill={CK.ok} fontFamily={MONO}>
                      C=48 B=32
                    </text>
                  </g>
                )}

                {/* Card PEM0 box on right of left panel */}
                <rect x={670} y={292} width={224} height={258} rx={16} fill="#1b1424" stroke={k2 > 0.4 ? CK.card : CK.trap} strokeWidth={2.2} />
                <text x={782} y={334} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.card} fontFamily={FONT}>
                  CN106 PEM0
                </text>
                <text x={782} y={366} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                  77 ns/msg drain
                </text>
                <rect x={694} y={394} width={176} height={66} rx={10} fill={k2 > 0.4 ? "#122624" : "#341319"} stroke={k2 > 0.4 ? CK.ok : CK.trap} strokeWidth={1.8} />
                <text x={782} y={422} textAnchor="middle" fontSize={18} fontWeight={800} fill={k2 > 0.4 ? CK.ok : CK.trap} fontFamily={MONO}>
                  {k2 > 0.4 ? "Bounded Burst" : "BACKPRESSURE"}
                </text>
                <text x={782} y={446} textAnchor="middle" fontSize={16} fill={C.muted} fontFamily={MONO}>
                  {k2 > 0.4 ? "No CHA stall" : "Stalls host PCIe"}
                </text>
                <text x={782} y={502} textAnchor="middle" fontSize={17} fill={C.muted} fontFamily={MONO}>
                  {k2 > 0.4 ? "4C cliff: +43%" : "4C collapses"}
                </text>
              </g>
            )}

            {/* ---------- LEFT PHASE B: Cues 3..5 (Arithmetic Proof + Safe Operating Map) ---------- */}
            {leftMathOp > 0.01 && (
              <g opacity={leftMathOp}>
                {/* Anchor Title */}
                <text x={92} y={178} fontSize={26} fontWeight={800} fill={CK.cxl} fontFamily={FONT}>
                  Why can&apos;t host-only credits hit ≤ 2 µs at 20 M/s?
                </text>
                <text x={92} y={208} fontSize={20} fill={C.muted} fontFamily={MONO}>
                  Hardware PMUs prove an unavoidable [18, 25] arithmetic gap
                </text>

                {/* Cue 3 Preview Card (fades out at T[4]) */}
                {k3Preview > 0.01 && (
                  <g opacity={k3Preview * k3}>
                    <rect x={92} y={252} width={800} height={240} rx={16} fill="#10182b" stroke={CK.cxl} strokeWidth={2} />
                    <text x={492} y={316} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text} fontFamily={FONT}>
                      Credit-capped p99 drops to 7.01 µs at 20 M/s
                    </text>
                    <text x={492} y={362} textAnchor="middle" fontSize={24} fontWeight={700} fill={CK.trap} fontFamily={MONO}>
                      Still ABOVE the 2.0 µs target line!
                    </text>
                    <text x={492} y={418} textAnchor="middle" fontSize={21} fill={C.muted} fontFamily={FONT}>
                      Two physics constraints collide on in-flight window W:
                    </text>
                    <text x={492} y={454} textAnchor="middle" fontSize={19} fontWeight={700} fill={CK.push} fontFamily={MONO}>
                      Tail ceiling (W ≤ 18)  vs  Little&apos;s Law demand (W ≥ 25)
                    </text>
                  </g>
                )}

                {/* Cue 4..5: Formula Card + Visual [18, 25] Constraint-Gap Number Line */}
                {k4 > 0.01 && (
                  <g opacity={k4}>
                    {/* Formula Card */}
                    <rect x={92} y={230} width={800} height={74} rx={14} fill="#0e1629" stroke={CK.push} strokeWidth={2} />
                    <text x={492} y={261} textAnchor="middle" fontSize={25} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                      Drain time = 0.58 µs + W × 77 ns
                    </text>
                    <text x={492} y={291} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                      Under relentless card polling · serial bridge service = 77 ns/msg
                    </text>

                    {/* Number-line panel */}
                    <rect x={92} y={320} width={800} height={240} rx={16} fill="#0d1323" stroke={C.line} strokeWidth={1.8} />
                    <text x={112} y={352} fontSize={21} fontWeight={800} fill={C.text} fontFamily={FONT}>
                      In-Flight Window W (messages) — Constraint Overlap Test
                    </text>

                    {/* Green Region: W <= 18 */}
                    <rect
                      x={sw(0)}
                      y={nlY - 26}
                      width={(sw(18) - sw(0)) * prog(t, T[4] + 0.2, 0.65)}
                      height={52}
                      rx={8}
                      fill={`${CK.ok}33`}
                      stroke={CK.ok}
                      strokeWidth={2.5}
                    />
                    <text
                      x={(sw(0) + sw(18)) / 2}
                      y={nlY + 1}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontSize={22}
                      fontWeight={800}
                      fill={CK.ok}
                      fontFamily={MONO}
                      opacity={prog(t, T[4] + 0.45, 0.4)}
                    >
                      W ≤ 18
                    </text>
                    <text
                      x={(sw(0) + sw(18)) / 2}
                      y={nlY - 40}
                      textAnchor="middle"
                      fontSize={18}
                      fontWeight={700}
                      fill={CK.ok}
                      fontFamily={FONT}
                      opacity={prog(t, T[4] + 0.45, 0.4)}
                    >
                      Required for ≤ 2.0 µs tail
                    </text>

                    {/* Cyan Region: W >= 25 */}
                    <rect
                      x={sw(25)}
                      y={nlY - 26}
                      width={(sw(40) - sw(25)) * prog(t, T[4] + 0.6, 0.65)}
                      height={52}
                      rx={8}
                      fill={`${CK.push}33`}
                      stroke={CK.push}
                      strokeWidth={2.5}
                    />
                    <text
                      x={(sw(25) + sw(40)) / 2}
                      y={nlY + 1}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontSize={22}
                      fontWeight={800}
                      fill={CK.push}
                      fontFamily={MONO}
                      opacity={prog(t, T[4] + 0.85, 0.4)}
                    >
                      W ≥ 25
                    </text>
                    <text
                      x={(sw(25) + sw(40)) / 2}
                      y={nlY - 40}
                      textAnchor="middle"
                      fontSize={18}
                      fontWeight={700}
                      fill={CK.push}
                      fontFamily={FONT}
                      opacity={prog(t, T[4] + 0.85, 0.4)}
                    >
                      20 M/s × 1.25 µs (Little&apos;s Law)
                    </text>

                    {/* Red Hatched IMPOSSIBLE GAP [18, 25] */}
                    <g opacity={prog(t, T[4] + 1.0, 0.5)}>
                      <rect
                        x={sw(18)}
                        y={nlY - 32}
                        width={sw(25) - sw(18)}
                        height={64}
                        rx={6}
                        fill="url(#hatchRed)"
                        stroke={CK.trap}
                        strokeWidth={3}
                      />
                      <rect x={sw(21.5) - 110} y={nlY + 70} width={220} height={34} rx={9} fill="#391218" stroke={CK.trap} strokeWidth={2} />
                      <text x={sw(21.5)} y={nlY + 87} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                        IMPOSSIBLE [18, 25]
                      </text>
                    </g>

                    {/* Number line axis + ticks */}
                    <line x1={nlX0} y1={nlY + 34} x2={nlX1} y2={nlY + 34} stroke={C.dim} strokeWidth={2.5} />
                    {[0, 10, 18, 25, 32, 40].map((v) => {
                      const isCrit = v === 18 || v === 25;
                      const col = v === 18 ? CK.ok : v === 25 ? CK.push : C.muted;
                      return (
                        <g key={v}>
                          <line x1={sw(v)} y1={nlY + 28} x2={sw(v)} y2={nlY + 42} stroke={col} strokeWidth={isCrit ? 3 : 2} />
                          <text x={sw(v)} y={nlY + 60} textAnchor="middle" fontSize={isCrit ? 19 : 16} fontWeight={isCrit ? 800 : 500} fill={col} fontFamily={MONO}>
                            {v}
                          </text>
                        </g>
                      );
                    })}
                    <text x={492} y={544} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                      No integer W satisfies both · C=32 caps at 17.9 M/s
                    </text>
                  </g>
                )}
              </g>
            )}

            {/* ---------- RIGHT PANEL: Persistent Quantitative p99 Chart ---------- */}
            <rect x={954} y={136} width={906} height={738} rx={18} fill={C.panel} stroke={CK.push} strokeWidth={2.2} />
            <text x={986} y={178} fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Open-Loop Tail Latency · MRV-115 &amp; MRV-123
            </text>
            <text x={986} y={208} fontSize={20} fill={C.muted} fontFamily={MONO}>
              End-to-end tot p99 (µs, log scale) vs offered rate (M/s)
            </text>

            {/* Shaded safe zone in Cues 4..5 */}
            {k4 > 0.05 && (
              <g opacity={k4 * 0.9}>
                <rect
                  x={sx(0.5)}
                  y={sy(2.0)}
                  width={sx(13.5) - sx(0.5)}
                  height={cy0 - sy(2.0)}
                  fill={CK.ok}
                  opacity={0.11}
                />
                <text x={sx(8.9)} y={sy(0.95)} textAnchor="middle" fontSize={18} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  Host-only ≤ 2 µs Safe Zone (≤ 13.5 M/s)
                </text>
              </g>
            )}

            <Axes
              x0={cx0}
              x1={cx1}
              y0={cy0}
              y1={cy1}
              sx={sx}
              sy={sy}
              xticks={[
                [1, "1"],
                [5, "5"],
                [10, "10"],
                [13.5, "13.5"],
                [16, "16"],
                [20, "20"],
              ]}
              yticks={[
                [1, "1 µs"],
                [2, "2 µs"],
                [5, "5 µs"],
                [10, "10 µs"],
                [20, "20 µs"],
                [50, "50 µs"],
              ]}
              xlabel="Offered Arrival Rate (M/s)"
              ylabel="tot p99 (µs)"
              size={20}
            />

            {/* Green 2.0 us target line (subtle in Cues 1..2, highlighted bright in Cues 3..5) */}
            <HLine
              y={sy(2.0)}
              x0={cx0}
              x1={cx1}
              color={CK.ok}
              opacity={k3 > 0.05 ? lerp(0.4, 1, k3) : 0.35}
              size={20}
            />
            <rect
              x={cx0 + 12}
              y={sy(2.0) - 34}
              width={168}
              height={28}
              rx={6}
              fill="#0d2420"
              stroke={CK.ok}
              strokeWidth={k3 > 0.1 ? 2 : 1.2}
              opacity={k3 > 0.05 ? lerp(0.65, 1, k3) : 0.55}
            />
            <text
              x={cx0 + 96}
              y={sy(2.0) - 20}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={17}
              fontWeight={800}
              fill={CK.ok}
              fontFamily={MONO}
              opacity={k3 > 0.05 ? lerp(0.7, 1, k3) : 0.65}
            >
              2.0 µs target
            </text>

            {/* Red Unshaped Curve (Cue 1+) */}
            <Series
              pts={unshapedPts}
              sx={sx}
              sy={sy}
              k={k1Curve}
              color={CK.trap}
              w={5.5}
              r={7.5}
              opacity={k3 > 0.05 ? lerp(1, 0.35, k3) : 1}
            />
            {k1Curve > 0.85 && (
              <g opacity={clamp01((k1Curve - 0.85) / 0.15) * (k3 > 0.05 ? lerp(1, 0.4, k3) : 1)}>
                <rect x={sx(8.8)} y={sy(60)} width={268} height={58} rx={10} fill="#311218" stroke={CK.trap} strokeWidth={2} />
                <text x={sx(8.8) + 134} y={sy(60) + 22} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  Unshaped: 46.3 µs
                </text>
                <text x={sx(8.8) + 134} y={sy(60) + 45} textAnchor="middle" fontSize={16} fill={C.muted} fontFamily={MONO}>
                  Poisson 20M: 44.5–50.8 µs
                </text>
                <line x1={sx(8.8) + 268} y1={sy(60) + 34} x2={sx(19.6)} y2={sy(46.32)} stroke={CK.trap} strokeWidth={2} strokeDasharray="5 4" />
              </g>
            )}

            {/* Cyan Credit-Capped Curve (Cue 2+) */}
            {k2Curve > 0.01 && (
              <g>
                <Series pts={creditPts} sx={sx} sy={sy} k={k2Curve} color={CK.push} w={5.5} r={7.5} />

                {/* 6-64x vertical drop arrow at 20 M/s (Cue 2 only, fades softly in Cue 3) */}
                {k2Curve > 0.9 && (
                  <g opacity={clamp01((k2Curve - 0.9) / 0.1) * (1 - k3 * 0.85)}>
                    <Arrow x1={sx(20)} y1={sy(41)} x2={sx(20)} y2={sy(8.5)} color={CK.ok} w={4} head={13} />
                    <rect x={sx(15.2)} y={sy(26)} width={144} height={36} rx={8} fill="#102924" stroke={CK.ok} strokeWidth={2} />
                    <text x={sx(15.2) + 72} y={sy(26) + 19} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                      6–64× cut
                    </text>
                  </g>
                )}

                {/* Credit-capped 7.01 us label + dashed 3.23 us wire p99 dot */}
                {k2Curve > 0.9 && (
                  <g opacity={clamp01((k2Curve - 0.9) / 0.1)}>
                    <rect x={sx(10.8)} y={sy(12.2)} width={246} height={54} rx={10} fill="#0c2335" stroke={CK.push} strokeWidth={2} />
                    <text x={sx(10.8) + 123} y={sy(12.2) + 21} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                      Credit (W=48): 7.01 µs
                    </text>
                    <text x={sx(10.8) + 123} y={sy(12.2) + 42} textAnchor="middle" fontSize={16} fill={C.muted} fontFamily={MONO}>
                      wire p99 = 3.23 µs
                    </text>
                    <line x1={sx(10.8) + 246} y1={sy(12.2) + 34} x2={sx(19.6)} y2={sy(7.01)} stroke={CK.push} strokeWidth={2} strokeDasharray="5 4" />
                    {/* Wire p99 dashed marker */}
                    <line x1={sx(18.2)} y1={sy(3.23)} x2={sx(20.6)} y2={sy(3.23)} stroke={CK.push} strokeWidth={2.2} strokeDasharray="5 4" />
                    <circle cx={sx(20)} cy={sy(3.23)} r={5.5} fill={C.bg} stroke={CK.push} strokeWidth={2.5} />
                  </g>
                )}
              </g>
            )}

            {/* Cue 3+ Gap highlight between 2.0 us target and 7.01 us at 20 M/s */}
            {k3 > 0.05 && (
              <g opacity={k3}>
                <line x1={sx(20)} y1={sy(7.01) + 9} x2={sx(20)} y2={sy(2.0) - 4} stroke={CK.cxl} strokeWidth={4} strokeDasharray="6 4" />
                <circle cx={sx(20)} cy={sy(7.01)} r={12 + 4 * pulse} fill="none" stroke={CK.cxl} strokeWidth={2.5} opacity={0.85} />
              </g>
            )}

            {/* Legend Strip at bottom of Right Panel */}
            <g transform="translate(986, 774)">
              <rect x={0} y={0} width={842} height={74} rx={12} fill="#0e1424" stroke={C.line} strokeWidth={1.5} />
              <circle cx={28} cy={26} r={7} fill={CK.trap} />
              <text x={46} y={32} fontSize={19} fontWeight={700} fill={C.text} fontFamily={FONT}>
                Unshaped (CHA 48–248)
              </text>
              <circle cx={324} cy={26} r={7} fill={CK.push} />
              <text x={342} y={32} fontSize={19} fontWeight={700} fill={C.text} fontFamily={FONT}>
                Credit-Capped W=48 (CHA 1.4–3.1)
              </text>
              <text x={22} y={59} fontSize={18} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
                4-card-core cliff recovery: +43% · 13.5 M/s @ 1.72 µs (1C)
              </text>
            </g>
          </g>
        )}
      </Stage>

      {/* ================= HTML OVERLAYS (OUTSIDE <Stage>) ================= */}

      {/* Left Phase A Bottom Callout: Cue 1 vs Cue 2 */}
      {mainOp * leftFlowOp > 0.01 && (
        <>
          {/* Cue 1 Unshaped Verdict */}
          {fade(t, T[1], T[2], 0.4) > 0.01 && (
            <VerdictRow
              x={92}
              y={638}
              w={802}
              badge="44–51 µs"
              badgeColor={CK.trap}
              title="Open-loop p99 explodes 25× past 13.5 M/s"
              detail="Host CHA backs up 48.5–248.4 entries; 25–30 M/s paced hits 756–1960 µs"
              opacity={mainOp * leftFlowOp * fade(t, T[1], T[2], 0.4)}
              dy={lerp(16, 0, k1)}
            />
          )}

          {/* Cue 2 Credit-Capped Hero & Verdict */}
          {k2 > 0.01 && (
            <>
              <HeroStat
                x={92}
                y={628}
                w={386}
                h={150}
                value="7.01 µs"
                label="20 M/s Poisson tot p99"
                sub="vs 44.5 µs · wire p99 3.23 µs"
                color={CK.push}
                opacity={mainOp * leftFlowOp * k2}
                dy={lerp(16, 0, k2)}
              />
              <HeroStat
                x={498}
                y={628}
                w={396}
                h={150}
                value="6–64×"
                label="Tail latency cut (16–30 M/s)"
                sub="CHA 248 → 1.4–3.1 · 4C cliff +43%"
                color={CK.ok}
                opacity={mainOp * leftFlowOp * k2}
                dy={lerp(16, 0, k2)}
              />
              <Pill
                x={92}
                y={798}
                color={CK.ok}
                size={20}
                style={{ opacity: mainOp * leftFlowOp * k2, fontFamily: MONO }}
              >
                MRV-123 optimal C=48 B=32 · zero host CHA starvation
              </Pill>
            </>
          )}
        </>
      )}

      {/* Left Phase B Cue 5: Honest Operating Map + Static vs On-Demand Backoff */}
      {mainOp * leftMathOp * k5 > 0.01 && (
        <>
          <Pill
            x={92}
            y={578}
            color={CK.ok}
            size={21}
            solid
            style={{
              opacity: mainOp * leftMathOp * k5,
              transform: `translateY(${lerp(14, 0, k5)}px)`,
              fontFamily: MONO,
            }}
          >
            Host-only ≤ 2 µs Safe Zone: ≤ 13.5 M/s (1C) / ≤ 10 M/s (4C)
          </Pill>

          <Card
            x={92}
            y={636}
            w={390}
            h={214}
            border={CK.trap}
            title="✕ Static Card Pause"
            titleColor={CK.trap}
            style={{
              opacity: mainOp * leftMathOp * prog(t, T[5] + 0.35, 0.55),
              transform: `translateY(${lerp(14, 0, prog(t, T[5] + 0.35, 0.55))}px)`,
            }}
          >
            <div style={{ fontSize: 20, color: C.text, lineHeight: 1.38 }}>
              1.0–1.8 µs pause on every empty poll cuts 20 M/s tail, but stalls idle
            </div>
            <div style={{ fontSize: 19, fontWeight: 800, color: CK.trap, fontFamily: MONO, marginTop: 12 }}>
              Pushes 0.9 µs idle RTT &gt; 2 µs!
            </div>
          </Card>

          <Card
            x={502}
            y={636}
            w={390}
            h={214}
            border={CK.ok}
            title="✓ Busy-Burst Backoff"
            titleColor={CK.ok}
            style={{
              opacity: mainOp * leftMathOp * prog(t, T[5] + 0.7, 0.55),
              transform: `translateY(${lerp(14, 0, prog(t, T[5] + 0.7, 0.55))}px)`,
            }}
          >
            <div style={{ fontSize: 20, color: C.text, lineHeight: 1.38 }}>
              On-demand pause only after draining a busy burst (never on idle)
            </div>
            <div style={{ fontSize: 19, fontWeight: 800, color: CK.ok, fontFamily: MONO, marginTop: 12 }}>
              Preserves 0.9 µs idle RTT!
            </div>
          </Card>
        </>
      )}
    </>
  );
}

export const C05: SceneDef = {
  id: "C05",
  title: "3 \u00b7 Taming Inbound Bridge Congestion",
  stage: 2,
  source: "MRV-150 \u00b7 MRV-115 (459 runs) \u00b7 MRV-123 (255 runs)",
  dur: DUR,
  cues,
  C: Body,
};
