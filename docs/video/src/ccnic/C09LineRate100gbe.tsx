import React from "react";
import { Arrow, Axes, C, Card, Cue, FONT, HLine, MONO, SceneDef, Series, Stage, clamp01, countUp, fade, lerp, linScale, logScale, prog, useRealT, useT } from "../lib";
import { CK, HBarChart, HeroStat, VerdictRow } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 18.56, "**4-core 100GbE RSS**: unshaped `256 B` bursts hit **13.3 M lines/s**; line credits reach **22.5 M**, exclusive `128 B` sub-rings **33.2 / 48.2 M**—on **1 host core**!", "On four-core hundred gigabit R S S, unshaped bursts hit the exact thirteen point three million line cliff. Line credits lifted steady rate to twenty-two point five million, and exclusive sub-rings let one host core hit thirty-three point two million—or forty-eight point two million unshaped—matching four host cores."], // 0
  [18.91, 28.01, "Ultimate stress test (**MRV-155**): can **strictly 1 host core** saturate **100 Gbps** end-to-end into `mercury`?", "That led to M R V one fifty-five: keeping saturn strictly at one single host core, can DirectPush saturate a real hundred gigabit link into mercury?"], // 1
  [28.36, 48.64, "**Stage 1 (1 host core → BAR4)**: `DirectPush` pushes **161.42 Mpps** (above 100GbE 64 B max **148.81 Mpps**) vs optimal `ISM=1` `SDP` **50.52 Mpps**.", "We isolated four stages. At Stage one into B A R four, one host core using batched doorbells sustains one hundred sixty-one point four two million sixty-four byte cells per second—beating hundred gigabit wire rate's one hundred forty-eight point eight million. Optimal S D P with I S M stalls at fifty point five million on that same host core."], // 2
  [48.99, 67.87, "**Stage 0 (card eth0 alone)**: CN106's default single `SMQ[0]` caps at **99.77 Mpps**—meeting **100 Gbps** at the exact **106 B L2 knee**!", "At Stage zero, testing Ethernet port zero alone found C N one oh six's default single scheduler queue caps at ninety-nine point seven seven million packets per second. Adding twenty wire bytes per frame, ninety-nine point two million times one hundred twenty-six bytes hits one hundred gigabits at an exact one hundred six byte knee."], // 3
  [68.22, 88.52, "**Stage 2 (1 host core → mercury PHY)**: **64 B UDP (110 B L2)** hits **99.76 Gbps**, **64 B RoCEv2** **99.68 Gbps** (**1.90–1.96×** `SDP`), **256 B** **99.86 Gbps**!", "At full Stage two end-to-end, once headers cross the one hundred six byte knee—using static card templates and L one payloads—one host core drives ninety-nine point seven six gigabits for U D P, ninety-nine point six eight for Rocky v two—nearly twice optimal S D P—and ninety-nine point eight six at two hundred fifty-six bytes."], // 4
  [88.87, 103.63, "At **1–4 KiB** (`DPI DMA`), wire rate sits at **94.0–96.5 Gbps** as payload meets the **86–89 Gbps** host-to-card PCIe transfer ceiling.", "At one kilobyte and four kilobytes via native D P I D M A, wire rate lands at ninety-four to ninety-six point five gigabits as headers shrink to eight percent and payload meets the eighty-six to eighty-nine gigabit host-to-card transfer ceiling."], // 5
];
const DUR = 105.03; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const STAGE0_PTS: [number, number][] = [
  [64, 66.94],
  [84, 82.91],
  [100, 95.66],
  [106, 100.01],
  [128, 100.05],
  [256, 99.98],
  [512, 99.96],
  [1024, 99.98],
  [1500, 99.98],
];

const STAGE2_GROUPS = [
  { label: "64 B L2", sub: "84 B wire", dp: 66.69, sdp: 33.78, ratio: "1.97×", dpColor: CK.push, full100: false, isDma: false },
  { label: "64 B UDP", sub: "110 B L2", dp: 99.76, sdp: 52.62, ratio: "1.90×", dpColor: CK.ok, full100: true, isDma: false },
  { label: "64 B RoCE", sub: "134 B L2", dp: 99.68, sdp: 50.94, ratio: "1.96×", dpColor: CK.ok, full100: true, isDma: false },
  { label: "256 B RoCE", sub: "326 B L2", dp: 99.86, sdp: 84.11, ratio: "1.19×", dpColor: CK.ok, full100: true, isDma: false },
  { label: "1 KiB DPI", sub: "86.4G pyld", dp: 93.99, sdp: 90.48, ratio: "+3.5G", dpColor: CK.dma, full100: false, isDma: true },
  { label: "4 KiB DPI", sub: "88.7G pyld", dp: 96.49, sdp: 90.33, ratio: "+6.2G", dpColor: CK.dma, full100: false, isDma: true },
];

function Body() {
  const t = useT();
  const rt = useRealT();

  // Phase opacities
  const op0 = fade(t, T[0] - 0.2, T[1], 0.45);
  const opBanner = fade(t, T[1], DUR + 5, 0.45);
  const opQ = fade(t, T[1], T[2], 0.42);
  const opS1 = fade(t, T[2], T[3], 0.45);
  const opS0 = fade(t, T[3], T[4], 0.45);
  const opS2 = fade(t, T[4], DUR + 5, 0.45);
  const opS2Right4 = fade(t, T[4], T[5], 0.42);
  const opS2Right5 = fade(t, T[5], DUR + 5, 0.42);

  // Cue 0 bars
  const kRss1 = prog(t, T[0] + 0.1, 0.75);
  const kRss2 = prog(t, T[0] + 0.35, 0.75);
  const kRss3 = prog(t, T[0] + 0.6, 0.75);
  const kRss4 = prog(t, T[0] + 0.85, 0.75);

  // Cue 2 Stage 1 bars
  const kS1Dp = prog(t, T[2] + 0.15, 0.95);
  const kS1Sdp = prog(t, T[2] + 0.55, 0.85);

  // Cue 3 Stage 0 curve + knee ring
  const kCurve = prog(t, T[3] + 0.15, 1.15);
  const kKnee = prog(t, T[3] + 0.75, 0.65);
  const kneePulse = 0.5 + 0.5 * Math.sin(rt * 6.5);

  // Cue 4 & 5 Stage 2 grouped bars
  const kG0 = prog(t, T[4] + 0.1, 0.8);
  const kG1 = prog(t, T[4] + 0.3, 0.8);
  const kG2 = prog(t, T[4] + 0.5, 0.8);
  const kG3 = prog(t, T[4] + 0.7, 0.8);
  const kG4 = prog(t, T[5] + 0.15, 0.85);
  const kG5 = prog(t, T[5] + 0.45, 0.85);
  const groupKs = [kG0, kG1, kG2, kG3, kG4, kG5];

  // Active stage index for top 4-stage pipeline banner
  const activeStage = t < T[2] ? -1 : t < T[3] ? 0 : t < T[4] ? 1 : 2;
  const stagesMeta = [
    { id: "STAGE 1", title: "1 Host Core → BAR4", sub: "161.4 vs 50.5 Mpps", color: CK.push, done: t >= T[3] },
    { id: "STAGE 0", title: "Card eth0 TX (SMQ[0])", sub: "99.8 Mpps · 106 B knee", color: CK.cxl, done: t >= T[4] },
    { id: "STAGE 2", title: "Full E2E (1H → DAC)", sub: "99.7–99.9 Gbps line", color: CK.ok, done: false },
    { id: "STAGE 3", title: "mercury Unbiased PHY", sub: "ConnectX-5 HW verify", color: CK.peer, done: false },
  ];

  // Stage 0 chart scales (Cue 3)
  const s0X0 = 152;
  const s0X1 = 1245;
  const s0Y0 = 768;
  const s0Y1 = 330;
  const s0Sx = logScale(58, 1650, s0X0, s0X1);
  const s0Sy = linScale(0, 110, s0Y0, s0Y1);

  // Stage 2 grouped bar chart scales (Cues 4-5)
  const s2X0 = 136;
  const s2X1 = 1344;
  const s2Y0 = 748;
  const s2Y1 = 326;
  const s2Sy = linScale(0, 124, s2Y0, s2Y1);
  const s2GroupW = (s2X1 - s2X0) / 6;

  // Ambient flow pulse dot position along banner arrows
  const flowPhase = (rt * 0.9) % 1;

  return (
    <>
      <Stage>
        {/* ==================== CUE 0: 4-CORE 100GbE RSS STAIRCASE ==================== */}
        {op0 > 0.01 && (
          <g opacity={op0}>
            <rect x={60} y={136} width={1800} height={48} rx={10} fill="#11182c" stroke={C.line} strokeWidth={1.6} />
            <text x={84} y={160} dominantBaseline="central" fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
              4-Core 100GbE RSS Breakthrough: Eliminating the PEM0 Cliff &amp; Lockstep
            </text>
            <text x={1836} y={160} textAnchor="end" dominantBaseline="central" fontSize={19} fontWeight={700} fill={CK.push} fontFamily={MONO}>
              256 B messages · MRV-135 (198) + MRV-137 (213 runs)
            </text>

            <rect x={60} y={202} width={1224} height={458} rx={18} fill="#10172b" stroke={C.line} strokeWidth={1.8} />
            <HBarChart
              x={72}
              y={230}
              w={1196}
              labelW={352}
              barH={74}
              gap={34}
              max={52}
              rows={[
                {
                  label: "Unshaped 256 B cliff",
                  sub: "4RxQ/4TxQ bursts · CHA 38.6",
                  value: 13.3,
                  valueText: `${countUp(t, T[0] + 0.1, 0.75, 13.3, 1)} M/s`,
                  color: CK.trap,
                  k: kRss1,
                  tag: "PEM0 cliff · >127 ms queue",
                },
                {
                  label: "Line credits (C_line=48)",
                  sub: "Card-cursor pacing · CHA 2.1",
                  value: 22.5,
                  valueText: `${countUp(t, T[0] + 0.35, 0.75, 22.5, 1)} M/s`,
                  color: CK.card,
                  k: kRss2,
                  tag: "+69% steady capacity",
                },
                {
                  label: "128 B sub-rings (1H)",
                  sub: "Matches 4H (33.53 M) · 99.1%",
                  value: 33.24,
                  valueText: `${countUp(t, T[0] + 0.6, 0.75, 33.2, 1)} M/s`,
                  color: CK.push,
                  k: kRss3,
                },
                {
                  label: "Unshaped 128 B (1H)",
                  sub: "1H · 83.8% of 4C card_echo",
                  value: 48.2,
                  valueText: `${countUp(t, T[0] + 0.85, 0.75, 48.2, 1)} M/s`,
                  color: CK.ok,
                  k: kRss4,
                },
              ]}
            />
          </g>
        )}

        {/* ==================== CUES 1-5: PERSISTENT 4-STAGE PIPELINE BANNER ==================== */}
        {opBanner > 0.01 && (
          <g opacity={opBanner}>
            {stagesMeta.map((st, idx) => {
              const bx = 60 + idx * 458;
              const bw = 426;
              const isActive =
                activeStage === idx || (activeStage === 2 && idx === 3) || (activeStage === -1);
              const borderCol = isActive ? st.color : st.done ? CK.ok : C.line;
              const fillCol = isActive ? `${st.color}1e` : "#11182c";
              return (
                <g key={st.id} opacity={isActive || st.done ? 1 : 0.55}>
                  <rect x={bx} y={130} width={bw} height={102} rx={14} fill={fillCol} stroke={borderCol} strokeWidth={isActive ? 3 : 1.6} />
                  <text x={bx + 18} y={158} fontSize={18} fontWeight={900} fill={borderCol} fontFamily={MONO}>
                    {st.id} {st.done ? "✓" : ""}
                  </text>
                  <text x={bx + 18} y={188} fontSize={21} fontWeight={800} fill={C.text} fontFamily={FONT}>
                    {st.title}
                  </text>
                  <text x={bx + 18} y={216} fontSize={18} fill={isActive ? st.color : C.muted} fontFamily={MONO}>
                    {st.sub}
                  </text>
                  {idx < 3 && (
                    <g>
                      <Arrow x1={bx + bw + 4} y1={181} x2={bx + 454} y2={181} color={isActive ? st.color : C.dim} w={3} head={10} />
                      <circle
                        cx={lerp(bx + bw + 6, bx + 448, flowPhase)}
                        cy={181}
                        r={4.5}
                        fill={st.color}
                        opacity={activeStage >= idx || activeStage === -1 ? 0.9 : 0.25}
                      />
                    </g>
                  )}
                </g>
              );
            })}
          </g>
        )}

        {/* ==================== CUE 2: STAGE 1 BAR CHART (1 HOST CORE -> BAR4) ==================== */}
        {opS1 > 0.01 && (
          <g opacity={opS1}>
            <rect x={60} y={252} width={1224} height={606} rx={18} fill="#10172b" stroke={C.line} strokeWidth={2} />
            <text x={88} y={292} fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Stage 1 Isolation: Strictly 1 Host Core Pushing 64 B Cells into BAR4
            </text>
            <text x={88} y={322} fontSize={19} fill={C.muted} fontFamily={MONO}>
              saturn cpu40 @ 3.90 GHz · zero wire overhead · batch_cells=128 · 1 SFENCE + batched doorbell
            </text>

            {/* Subtle cyan highlight around DirectPush row */}
            <rect
              x={78}
              y={398}
              width={1188}
              height={120}
              rx={14}
              fill={`${CK.push}12`}
              stroke={CK.push}
              strokeWidth={2.2}
              opacity={kS1Dp}
            />

            <HBarChart
              x={84}
              y={412}
              w={1176}
              labelW={328}
              barH={92}
              gap={72}
              max={175}
              refLine={{ value: 148.81, label: "100GbE 64 B Max: 148.81 Mpps", color: CK.cxl }}
              rows={[
                {
                  label: "DirectPush (1H)",
                  sub: "0.00 M/s TAD retries",
                  value: 161.42,
                  valueText: `${countUp(t, T[2] + 0.15, 0.95, 161.42, 2)} Mpps`,
                  color: CK.push,
                  k: kS1Dp,
                },
                {
                  label: "Optimal SDP (ISM=1)",
                  sub: "Same 1 host core",
                  value: 50.52,
                  valueText: `${countUp(t, T[2] + 0.55, 0.85, 50.52, 2)} Mpps`,
                  color: CK.sdp,
                  k: kS1Sdp,
                  tag: "Host CPU bound (3.20× slower)",
                },
              ]}
            />

            {/* Bottom headroom callout inside Stage 1 panel */}
            <rect x={88} y={736} width={1168} height={92} rx={14} fill="#141b2e" stroke={CK.ok} strokeWidth={1.8} />
            <text x={116} y={772} fontSize={22} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
              DirectPush beats 100GbE 64 B wire max (148.81 Mpps) by +8.5% on a single Xeon core!
            </text>
            <text x={116} y={806} fontSize={18} fill={C.muted} fontFamily={MONO}>
              Bare 1-core dpdk-testpmd TX peaks at 64.66 Mpps (4-core testpmd: 107.61 Mpps) → SDP is host-CPU limited
            </text>
          </g>
        )}

        {/* ==================== CUE 3: STAGE 0 106 B KNEE CURVE ==================== */}
        {opS0 > 0.01 && (
          <g opacity={opS0}>
            <rect x={60} y={252} width={1224} height={606} rx={18} fill="#10172b" stroke={C.line} strokeWidth={2} />
            <text x={88} y={290} fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Stage 0 (Card eth0 Alone): The Exact 106 B L2 Saturation Knee
            </text>

            <Axes
              x0={s0X0}
              x1={s0X1}
              y0={s0Y0}
              y1={s0Y1}
              sx={s0Sx}
              sy={s0Sy}
              xticks={[
                [64, "64"],
                [84, "84"],
                [106, "106"],
                [128, "128"],
                [256, "256"],
                [512, "512"],
                [1024, "1024"],
                [1500, "1500"],
              ]}
              yticks={[
                [0, "0"],
                [25, "25"],
                [50, "50"],
                [75, "75"],
                [100, "100"],
              ]}
              xlabel="L2 Ethernet Frame Size (bytes, log scale)"
              ylabel="Wire Rate (Gbps)"
              size={20}
            />

            {/* 100 Gbps ceiling line */}
            <HLine y={s0Sy(100)} x0={s0X0} x1={s0X1} color={CK.ok} size={19} opacity={0.85} />
            <text x={s0X1 - 8} y={s0Sy(100) - 14} textAnchor="end" fontSize={19} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              100.0 Gbps Wire Ceiling
            </text>

            {/* Vertical dashed drop line from 106 B knee */}
            <line
              x1={s0Sx(106)}
              y1={s0Sy(100.01)}
              x2={s0Sx(106)}
              y2={s0Y0}
              stroke={CK.cxl}
              strokeWidth={2.4}
              strokeDasharray="7 6"
              opacity={kKnee}
            />

            {/* Stage 0 Data Series */}
            <Series pts={STAGE0_PTS} sx={s0Sx} sy={s0Sy} k={kCurve} color={CK.push} w={5.5} r={7} />

            {/* Ramp point annotations (placed clear of curve & 106 B drop line) */}
            <text x={s0Sx(64) + 12} y={s0Sy(66.94) + 28} fontSize={18} fontWeight={800} fill={CK.push} fontFamily={MONO} opacity={kCurve}>
              64 B: 66.9G
            </text>
            <text x={s0Sx(84) - 28} y={s0Sy(82.91) - 18} textAnchor="middle" fontSize={18} fontWeight={700} fill={C.text} fontFamily={MONO} opacity={kCurve}>
              84 B: 82.9G
            </text>

            {/* Glowing yellow knee ring at (106 B, 100.01 Gbps) */}
            <g opacity={kKnee}>
              <circle cx={s0Sx(106)} cy={s0Sy(100.01)} r={16 + 7 * kneePulse} fill="none" stroke={CK.cxl} strokeWidth={3} opacity={0.55 - 0.3 * kneePulse} />
              <circle cx={s0Sx(106)} cy={s0Sy(100.01)} r={12} fill={`${CK.cxl}30`} stroke={CK.cxl} strokeWidth={3.5} />
              <circle cx={s0Sx(106)} cy={s0Sy(100.01)} r={5} fill={CK.cxl} />

              {/* Formula box inside wide-open area beneath 100G plateau */}
              <Arrow x1={s0Sx(106) + 14} y1={s0Sy(100.01) + 14} x2={516} y2={526} color={CK.cxl} w={3} dash="7 5" />
              <rect x={516} y={488} width={688} height={180} rx={16} fill="#141b2e" stroke={CK.cxl} strokeWidth={2.5} />
              <text x={544} y={528} fontSize={22} fontWeight={900} fill={CK.cxl} fontFamily={MONO}>
                EXACT 106 B L2 KNEE (100.01 Gbps)
              </text>
              <text x={544} y={568} fontSize={22} fontWeight={800} fill={C.text} fontFamily={MONO}>
                99.22 Mpps × (106 + 20 B) × 8 = 100.01 Gbps
              </text>
              <text x={544} y={608} fontSize={19} fill={C.muted} fontFamily={FONT}>
                CN106 default single scheduler queue (SMQ[0]) caps at 99.77 Mpps.
              </text>
              <text x={544} y={642} fontSize={19} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
                Any L2 frame ≥ 106 B saturates 100.0 Gbps flat across 128 → 1500 B!
              </text>
            </g>
          </g>
        )}

        {/* ==================== CUES 4-5: STAGE 2 E2E GROUPED BAR CHART ==================== */}
        {opS2 > 0.01 && (
          <g opacity={opS2}>
            <rect x={60} y={252} width={1308} height={606} rx={18} fill="#10172b" stroke={C.line} strokeWidth={2} />
            <text x={88} y={290} fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Stage 2 End-to-End (1 Host Core → mercury Unbiased PHY)
            </text>

            {/* Legend */}
            <g transform="translate(836, 272)">
              <rect x={0} y={1} width={16} height={16} rx={4} fill={CK.ok} />
              <text x={22} y={16} fontSize={18} fontWeight={700} fill={C.text} fontFamily={MONO}>
                DP 100G
              </text>
              <rect x={122} y={1} width={16} height={16} rx={4} fill={CK.push} />
              <text x={144} y={16} fontSize={18} fontWeight={700} fill={C.text} fontFamily={MONO}>
                DP 64B
              </text>
              <rect x={232} y={1} width={16} height={16} rx={4} fill={CK.dma} />
              <text x={254} y={16} fontSize={18} fontWeight={700} fill={C.text} fontFamily={MONO}>
                DPI DMA
              </text>
              <rect x={356} y={1} width={16} height={16} rx={4} fill={CK.sdp} />
              <text x={378} y={16} fontSize={18} fontWeight={700} fill={C.text} fontFamily={MONO}>
                SDP ISM=1
              </text>
            </g>

            {/* Y Grid + Axis */}
            {[0, 25, 50, 75, 100].map((v) => (
              <g key={v}>
                <line x1={s2X0} y1={s2Sy(v)} x2={s2X1} y2={s2Sy(v)} stroke={C.line} strokeWidth={1.4} opacity={0.55} />
                <text x={s2X0 - 12} y={s2Sy(v)} textAnchor="end" dominantBaseline="central" fontSize={19} fill={C.muted} fontFamily={MONO}>
                  {v}
                </text>
              </g>
            ))}
            <line x1={s2X0} y1={s2Y0} x2={s2X1} y2={s2Y0} stroke={C.dim} strokeWidth={2.5} />
            <line x1={s2X0} y1={s2Y0} x2={s2X0} y2={s2Y1} stroke={C.dim} strokeWidth={2.5} />
            <text x={s2X0} y={s2Y1 - 10} fontSize={18} fontWeight={700} fill={C.muted} fontFamily={FONT}>
              Wire Rate (Gbps)
            </text>

            {/* 100 Gbps dashed reference line */}
            <HLine y={s2Sy(100)} x0={s2X0} x1={s2X1} color={CK.ok} opacity={0.8} />

            {/* Grouped bars */}
            {STAGE2_GROUPS.map((g, idx) => {
              const kk = groupKs[idx];
              const gx = s2X0 + idx * s2GroupW + s2GroupW / 2;
              const barW = 54;
              const innerGap = 16;
              const dpX = gx - barW - innerGap / 2;
              const sdpX = gx + innerGap / 2;
              const dpH = Math.max(0, (s2Y0 - s2Sy(g.dp)) * kk);
              const sdpH = Math.max(0, (s2Y0 - s2Sy(g.sdp)) * kk);
              const visOp = g.isDma ? opS2Right5 : 1;

              return (
                <g key={g.label} opacity={visOp}>
                  {/* Subtle full-100G highlight box behind 100G achievers */}
                  {g.full100 && kk > 0.2 && (
                    <rect
                      x={dpX - 6}
                      y={s2Sy(100) - 4}
                      width={barW + 12}
                      height={s2Y0 - s2Sy(100) + 8}
                      rx={10}
                      fill={`${CK.ok}12`}
                      stroke={CK.ok}
                      strokeWidth={1.6}
                      strokeDasharray="5 4"
                      opacity={0.75 * kk}
                    />
                  )}

                  {/* DirectPush bar */}
                  <rect x={dpX} y={s2Y0 - dpH} width={barW} height={dpH} rx={7} fill={g.dpColor} opacity={0.94} />
                  {kk > 0.35 && (
                    <text
                      x={dpX + barW / 2}
                      y={s2Y0 - dpH - 12}
                      textAnchor="middle"
                      fontSize={19}
                      fontWeight={900}
                      fill={g.dpColor}
                      fontFamily={MONO}
                    >
                      {g.dp.toFixed(1)}
                    </text>
                  )}

                  {/* Optimal SDP bar */}
                  <rect x={sdpX} y={s2Y0 - sdpH} width={barW} height={sdpH} rx={7} fill={CK.sdp} opacity={0.86} />
                  {kk > 0.35 && (
                    <text
                      x={sdpX + barW / 2}
                      y={s2Y0 - sdpH - 12}
                      textAnchor="middle"
                      fontSize={18}
                      fontWeight={800}
                      fill={CK.sdp}
                      fontFamily={MONO}
                    >
                      {g.sdp.toFixed(1)}
                    </text>
                  )}

                  {/* Ratio badge above group */}
                  {kk > 0.65 && (
                    <g opacity={clamp01((kk - 0.65) * 3)}>
                      <rect x={gx - 42} y={s2Sy(Math.max(g.dp, g.sdp)) - 66} width={84} height={30} rx={7} fill={`${g.dpColor}24`} stroke={g.dpColor} strokeWidth={1.5} />
                      <text x={gx} y={s2Sy(Math.max(g.dp, g.sdp)) - 51} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={900} fill={g.dpColor} fontFamily={MONO}>
                        {g.ratio}
                      </text>
                    </g>
                  )}

                  {/* X-axis category label */}
                  <text x={gx} y={s2Y0 + 30} textAnchor="middle" fontSize={20} fontWeight={800} fill={C.text} fontFamily={FONT}>
                    {g.label}
                  </text>
                  <text x={gx} y={s2Y0 + 56} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                    {g.sub}
                  </text>
                </g>
              );
            })}

            {/* Cue 5 subtle second knee callout bracket over 1 KiB & 4 KiB */}
            {opS2Right5 > 0.05 && (
              <g opacity={opS2Right5 * kG5}>
                <rect
                  x={s2X0 + 4 * s2GroupW + 8}
                  y={334}
                  width={2 * s2GroupW - 16}
                  height={482}
                  rx={14}
                  fill="none"
                  stroke={CK.dma}
                  strokeWidth={2.5}
                  strokeDasharray="8 6"
                />
              </g>
            )}

            {/* Subtle honest scope note at bottom left of chart */}
            <text x={88} y={842} fontSize={18} fill={C.muted} fontFamily={MONO}>
              Static 5-tuple templates + L1 payload (64–256 B) · DPI DMA (1–4 KiB) · Unbiased mercury PHY
            </text>
          </g>
        )}
      </Stage>

      {/* ==================== HTML OVERLAYS (OUTSIDE <Stage>) ==================== */}

      {/* Cue 0 right stats + bottom verdict */}
      {op0 > 0.01 && (
        <>
          <HeroStat
            x={1312}
            y={202}
            w={548}
            h={214}
            value="33.24 M/s"
            label="1 Host Core Matches 4 Host Cores"
            sub="1H 33.24 M vs 4H 33.53 M (99.1%)"
            color={CK.push}
            opacity={op0 * kRss3}
            dy={lerp(16, 0, kRss3)}
          />
          <Card
            x={1312}
            y={438}
            w={548}
            h={222}
            border={CK.ok}
            title="Lockstep Discovery (MRV-137)"
            titleColor={CK.ok}
            style={{ opacity: op0 * kRss4 }}
          >
            <div style={{ fontSize: 21, color: C.text, lineHeight: 1.42 }}>
              The <span style={{ fontFamily: MONO, fontWeight: 800, color: CK.push }}>33.5 M</span> plateau was prototype credit-writeback lockstep (
              <span style={{ fontFamily: MONO, color: CK.cxl }}>T_cycle = 1.91 µs</span>), NOT host CPU — unshaped 1 host core hits{" "}
              <span style={{ fontFamily: MONO, fontWeight: 800, color: CK.ok }}>48.2 M lines/s</span>.
            </div>
          </Card>
          <VerdictRow
            x={60}
            y={694}
            w={1800}
            badge="1 HOST CORE"
            badgeColor={CK.push}
            title="With exclusive 128 B sub-rings, 1 host core saturates all 4 card RSS cores"
            detail="Leading straight to MRV-155: keeping saturn strictly at 1 host core, can DirectPush drive a real 100GbE wire at line rate?"
            opacity={op0 * kRss4}
          />
        </>
      )}

      {/* Cue 1 MRV-155 challenge card */}
      {opQ > 0.01 && (
        <>
          <Card
            x={140}
            y={282}
            w={1640}
            h={320}
            border={CK.ok}
            style={{
              opacity: opQ,
              background: "linear-gradient(155deg, rgba(16,32,44,0.96), rgba(11,16,32,0.96))",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              alignItems: "center",
              textAlign: "center",
              padding: "28px 54px",
            }}
          >
            <div
              style={{
                fontSize: 21,
                fontWeight: 900,
                color: CK.cxl,
                fontFamily: MONO,
                letterSpacing: 1.5,
                marginBottom: 18,
              }}
            >
              ULTIMATE STRESS TEST · MRV-155 PHASE 2 (178 VERIFIED RUNS)
            </div>
            <div style={{ fontSize: 52, fontWeight: 900, color: C.text, lineHeight: 1.2 }}>
              Can <span style={{ color: CK.push }}>STRICTLY 1 Host Core</span> Saturate{" "}
              <span style={{ color: CK.ok }}>100 Gbps</span>
              <br />
              End-to-End into <span style={{ color: CK.peer, fontFamily: MONO }}>mercury</span>?
            </div>
          </Card>

          <Card x={140} y={634} w={524} h={212} border={CK.push} title="Host: Strictly 1 Core" titleColor={CK.push} style={{ opacity: opQ }}>
            <div style={{ fontSize: 21, color: C.text, lineHeight: 1.4 }}>
              Pinned <span style={{ fontFamily: MONO, color: CK.push, fontWeight: 800 }}>saturn cpu40 @ 3.90 GHz</span>,{" "}
              <span style={{ fontFamily: MONO }}>intel_iommu=on,sm_on</span>, zero hidden host cores.
            </div>
          </Card>
          <Card x={698} y={634} w={524} h={212} border={CK.sdp} title="Fair Baseline: Optimal SDP" titleColor={CK.sdp} style={{ opacity: opQ }}>
            <div style={{ fontSize: 21, color: C.text, lineHeight: 1.4 }}>
              Same 1 host core with <span style={{ fontFamily: MONO, color: CK.sdp, fontWeight: 800 }}>ISM=1</span> + batched TX/RX ring completions.
            </div>
          </Card>
          <Card x={1256} y={634} w={524} h={212} border={CK.peer} title="Unbiased Wire Verifier" titleColor={CK.peer} style={{ opacity: opQ }}>
            <div style={{ fontSize: 21, color: C.text, lineHeight: 1.4 }}>
              Measured at remote <span style={{ fontFamily: MONO, color: CK.peer, fontWeight: 800 }}>mercury ConnectX-5 PHY</span> hardware counters across 3 m 100GbE DAC.
            </div>
          </Card>
        </>
      )}

      {/* Cue 2 Stage 1 right cards */}
      {opS1 > 0.01 && (
        <>
          <HeroStat
            x={1312}
            y={252}
            w={548}
            h={244}
            value="161.42 Mpps"
            label="+8.5% Above 100GbE 64 B Max"
            sub="Beats 148.81 Mpps wire cap · 0.00 TAD retries"
            color={CK.push}
            opacity={opS1 * kS1Dp}
            dy={lerp(18, 0, kS1Dp)}
          />
          <Card
            x={1312}
            y={522}
            w={548}
            h={336}
            border={CK.sdp}
            title="Why SDP Caps at 50.52 Mpps"
            titleColor={CK.sdp}
            style={{ opacity: opS1 * kS1Sdp }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 16, fontSize: 21, color: C.text, lineHeight: 1.4 }}>
              <div>
                Even with <span style={{ fontFamily: MONO, color: CK.sdp, fontWeight: 800 }}>ISM=1</span>, descriptor ring bookkeeping saturates 1 Xeon core at{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800 }}>50.52 Mpps</span>.
              </div>
              <div style={{ background: "#141b2e", borderRadius: 10, padding: "12px 16px", fontSize: 19, fontFamily: MONO, color: C.muted, lineHeight: 1.45 }}>
                dpdk-testpmd bare TX:
                <br />
                <span style={{ color: C.text, fontWeight: 700 }}>1 core: 64.66 Mpps</span>
                <br />
                <span style={{ color: C.text, fontWeight: 700 }}>4 cores: 107.61 Mpps</span>
              </div>
              <div style={{ color: CK.push, fontWeight: 800, fontFamily: MONO, fontSize: 20 }}>
                DirectPush delivers 3.20× higher 1-core packet rate!
              </div>
            </div>
          </Card>
        </>
      )}

      {/* Cue 3 Stage 0 right cards */}
      {opS0 > 0.01 && (
        <>
          <HeroStat
            x={1312}
            y={252}
            w={548}
            h={236}
            value="106 B L2"
            label="Exact 100 Gbps Saturation Knee"
            sub="99.77 Mpps SMQ[0] ceiling × (106+20) B"
            color={CK.cxl}
            opacity={opS0 * kKnee}
            dy={lerp(18, 0, kKnee)}
          />
          <Card
            x={1312}
            y={512}
            w={548}
            h={346}
            border={CK.card}
            title="Card eth0 Scheduler Ceiling"
            titleColor={CK.card}
            style={{ opacity: opS0 * kCurve }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 16, fontSize: 21, color: C.text, lineHeight: 1.4 }}>
              <div>
                Below <span style={{ fontFamily: MONO, color: CK.cxl, fontWeight: 800 }}>106 B</span>, CN106&apos;s default single{" "}
                <span style={{ fontFamily: MONO, color: CK.card }}>SMQ[0]</span> caps at{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800 }}>99.77 Mpps</span> (64 B L2 → 66.94 Gbps).
              </div>
              <div
                style={{
                  background: "#43c59e18",
                  border: `1.5px solid ${CK.ok}`,
                  borderRadius: 12,
                  padding: "12px 16px",
                  fontSize: 20,
                  color: C.text,
                }}
              >
                <strong style={{ color: CK.ok }}>Above 106 B L2</strong> (UDP 110 B, RoCE 134 B), eth0 locks flat at{" "}
                <strong style={{ color: CK.ok, fontFamily: MONO }}>100.0 Gbps</strong>!
              </div>
            </div>
          </Card>
        </>
      )}

      {/* Cue 4 Stage 2 right cards */}
      {opS2Right4 > 0.01 && (
        <>
          <HeroStat
            x={1394}
            y={252}
            w={466}
            h={232}
            value="99.8 Gbps"
            label="Full 100GbE on 1 Host Core!"
            sub="UDP 99.76 · RoCE 99.68 · 256B 99.86"
            color={CK.ok}
            opacity={opS2Right4 * kG2}
            dy={lerp(18, 0, kG2)}
          />
          <Card
            x={1394}
            y={506}
            w={466}
            h={352}
            border={CK.push}
            title="1.90–1.97× vs Optimal SDP"
            titleColor={CK.push}
            style={{ opacity: opS2Right4 * kG3 }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 12, fontSize: 19, fontFamily: MONO, color: C.text, marginTop: 4 }}>
              <div>
                L2: <span style={{ color: CK.push, fontWeight: 800 }}>99.2</span> vs{" "}
                <span style={{ color: CK.sdp }}>50.3 Mpps</span> (1.97×)
              </div>
              <div>
                UDP: <span style={{ color: CK.ok, fontWeight: 800 }}>95.9</span> vs{" "}
                <span style={{ color: CK.sdp }}>50.6 Mpps</span> (1.90×)
              </div>
              <div>
                RoCE: <span style={{ color: CK.ok, fontWeight: 800 }}>80.9</span> vs{" "}
                <span style={{ color: CK.sdp }}>41.4 Mpps</span> (1.96×)
              </div>
              <div style={{ color: CK.cxl, fontSize: 18 }}>
                Host sb_stall: 0.0% vs 69.1% (SDP)
              </div>
              <div
                style={{
                  marginTop: 8,
                  padding: "10px 12px",
                  background: "#141b2e",
                  borderRadius: 10,
                  borderLeft: `4px solid ${CK.peer}`,
                  fontSize: 18,
                  color: C.muted,
                  lineHeight: 1.35,
                  fontFamily: FONT,
                }}
              >
                Static 5-tuple card templates + L1 payload · verified on remote mercury PHY.
              </div>
            </div>
          </Card>
        </>
      )}

      {/* Cue 5 Stage 2 DPI DMA right cards */}
      {opS2Right5 > 0.01 && (
        <>
          <HeroStat
            x={1394}
            y={252}
            w={466}
            h={232}
            value="94.0–96.5G"
            label="1–4 KiB DPI DMA Wire Rate"
            sub="1K: 93.99 · 4K (4×1K): 96.49 Gbps"
            color={CK.dma}
            opacity={opS2Right5 * kG5}
            dy={lerp(18, 0, kG5)}
          />
          <Card
            x={1394}
            y={506}
            w={466}
            h={352}
            border={CK.dma}
            title="The Second Subtle Knee"
            titleColor={CK.dma}
            style={{ opacity: opS2Right5 * kG5 }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 14, fontSize: 20, color: C.text, lineHeight: 1.4, marginTop: 4 }}>
              <div>
                At <span style={{ fontFamily: MONO, color: CK.dma, fontWeight: 800 }}>1–4 KiB</span>, Ethernet + RoCE headers shrink to{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800 }}>~8%</span> of wire bytes.
              </div>
              <div style={{ background: "#ff70a618", border: `1.5px solid ${CK.dma}`, borderRadius: 12, padding: "12px 14px", fontSize: 19 }}>
                Payload meets the <strong style={{ color: CK.dma, fontFamily: MONO }}>86.4–88.7 Gbps</strong> host→card PCIe DMA ceiling (~92% wire share).
              </div>
              <div style={{ fontSize: 18, fontFamily: MONO, color: CK.ok }}>
                Still beats optimal SDP (+3.5 to +6.2 Gbps) on 1 host core.
              </div>
            </div>
          </Card>
        </>
      )}
    </>
  );
}

export const C09: SceneDef = {
  id: "C09",
  title: "5 \u00b7 1-Core 100GbE & 106 B Knee",
  stage: 4,
  source: "MRV-152 \u00b7 MRV-135 (198) \u00b7 MRV-137 (213) \u00b7 MRV-155 P2 (178 runs)",
  dur: DUR,
  cues,
  C: Body,
};
