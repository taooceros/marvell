import React from "react";
import { Arrow, C, Card, Cue, FONT, MONO, SceneDef, Stage, clamp01, countUp, fade, lerp, prog, useRealT, useT } from "../lib";
import { CK, HBarChart, HeroStat, InfBadge, VerdictRow } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 16.50, "Review caught an unfair artifact: our strong `SDP` baseline spun synchronously on **54.8%** of 4 KiB tails—so we retracted it and ran **MRV-166**.", "During review, we caught an implementation flaw: our optimized S D P baseline spun synchronously waiting for fifty-four point eight percent of four kilobyte tails. We retracted that claim and ran M R V one sixty-six to make S D P completely fair."], // 0
  [16.85, 32.11, "**MRV-166** single-variable ladder (**4 KiB @ 1 M/s**): default **3862 cyc** (`99%`) → `rx_poll_mem` **2350** → sync-tail **1490** (`38.2%`).", "On four kilobytes at one million per second, default S D P burns three thousand eight hundred sixty-two cycles—ninety-nine percent of a core. Memory polling cuts it to two thousand three hundred fifty, and zero-copy to one thousand four hundred ninety cycles."], // 1
  [32.46, 43.62, "Switching `SDP` to **async tail poll** (`rx_poll_tail_off=16`) zeroes `late_tails` (**54.8% → 0.0%**) and cuts **−459 cyc/pkt** → **1031 cyc**.", "Switching S D P to non-blocking async tail polling zeroes late-tail spins from fifty-four point eight percent to zero, cutting four hundred fifty-nine cycles per packet down to one thousand thirty-one."], // 2
  [43.97, 64.87, "`+ism_enable=1` + **512-slot DDIO RX ring** lifts DDIO **45.2% → 100%**, cuts DRAM writes **71×** → **SDP fair-full 1011** vs **DirectPush 508 cyc**.", "Adding I S M completion counting and a five hundred twelve slot D D I O ring lifts S D P's D D I O hits to one hundred percent and cuts host D RAM writes seventy-one times. Fair S D P lands at one thousand eleven cycles versus five hundred eight for DirectPush—or eight hundred fifty-eight versus four hundred sixty-one at batch eight."], // 3
  [65.22, 88.76, "**4-layer CPU ledger** (`64 B / 1 KiB / 4 KiB`): **1.54–2.07×** (`330 ns`) → **[INF] 1.35–1.81×** (`5 µs`) → **[INF] 1.26–1.53×** DPDK stack tax.", "Across sixty-four bytes, one kilobyte, and four kilobytes, the clean gap splits into four layers: one point five four to two point zero seven times at short work quanta, one point three five to one point eight one at five microseconds, and one point two six to one point five three times of generic D P D K stack overhead after C N one oh six's two-line tail check."], // 4
  [89.11, 108.13, "Honest boundary: **1–4 KiB** is PCIe-isomorphic to bare-ring NICs (`mlx5`/`E810`); **64 B** keeps a **real HW win** (**0 vs 2.2** TX reads, **5.24×** RTT).", "Honest boundary: at one to four kilobytes, DirectPush is P C I express isomorphic to modern static-ring NICs with hardware completion queues. At sixty-four bytes, however, eliminating transmit D M A reads and instant reuse is a true hardware win, with five point two four times lower same-card idle latency."], // 5
];
const DUR = 109.53; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Phase opacities (clean crossfades at cue boundaries)
  const op0 = fade(t, T[0] - 0.2, T[1], 0.45);
  const opLadder = fade(t, T[1], T[4], 0.45);
  const opSide1 = fade(t, T[1], T[2], 0.42);
  const opSide2 = fade(t, T[2], T[3], 0.42);
  const opSide3 = fade(t, T[3], T[4], 0.42);
  const opLedger = fade(t, T[4], DUR + 5, 0.45);
  const opLayers = fade(t, T[4], T[5], 0.45);
  const opBoundary = fade(t, T[5], DUR + 5, 0.45);

  // Cue 0 timeline animation (ambient streaming + spin pulse)
  const streamPhase = (rt * 0.55) % 1;
  const spinPulse = 0.5 + 0.5 * Math.sin(rt * 11);
  const k0Stamp = prog(t, T[0] + 0.35, 0.65);
  const k0Diag = prog(t, T[0] + 0.15, 0.9);

  // Cue 1..3 ladder bar animations (finish <= 1.4s from each cue start)
  const kBar1 = prog(t, T[1] + 0.1, 0.8);
  const kBar2 = prog(t, T[1] + 0.45, 0.8);
  const kBar3 = prog(t, T[1] + 0.8, 0.8);
  const kBar4 = prog(t, T[2] + 0.15, 0.9);
  const kBar5 = prog(t, T[3] + 0.15, 0.85);
  const kBar6 = prog(t, T[3] + 0.55, 0.85);

  // Cue 4..5 pair bar animations
  const kPair1 = prog(t, T[4] + 0.1, 0.85);
  const kPair2 = prog(t, T[4] + 0.35, 0.85);
  const kPair3 = prog(t, T[4] + 0.6, 0.85);
  const kWf1 = prog(t, T[4] + 0.25, 0.75);
  const kWf2 = prog(t, T[4] + 0.55, 0.75);
  const kWf3 = prog(t, T[4] + 0.85, 0.75);
  const kBnd = prog(t, T[5] + 0.15, 0.85);

  // Ladder chart geometry (left panel)
  const chartX = 60;
  const chartY = 212;
  const chartW = 1155;
  const labelW = 345;
  const barH = 62;
  const gap = 32;
  const trackX = chartX + labelW;
  const trackW = chartW - labelW - 150; // 660 px
  const maxCyc = 4000;

  const ladderRows = [
    {
      label: "1. Default SDP",
      sub: "OUT_CNTS MMIO + copy · 99.0%",
      value: 3862,
      valueText: `${countUp(t, T[1] + 0.1, 0.85, 3862)} cyc`,
      color: CK.sdp,
      k: kBar1,
    },
    {
      label: "2. +rx_poll_mem=1",
      sub: "No MMIO poll, copy · 60.2%",
      value: 2350,
      valueText: `${countUp(t, T[1] + 0.45, 0.85, 2350)} cyc`,
      color: CK.sdp,
      k: kBar2,
      tag: "−1,513 cyc",
    },
    {
      label: "3. SDP sync-tail",
      sub: "+zero_copy · 38.2% · DDIO 45.6%",
      value: 1490,
      valueText: `${countUp(t, T[1] + 0.8, 0.85, 1490)} cyc`,
      color: CK.trap,
      k: kBar3,
      tag: "54.8% late_tails",
    },
    {
      label: "4. SDP async-tail",
      sub: "rx_poll_tail_off=16 · 26.4%",
      value: 1031,
      valueText: `${countUp(t, T[2] + 0.15, 0.9, 1031)} cyc`,
      color: CK.ok,
      k: kBar4,
      tag: "0.0% late · −459",
    },
    {
      label: "5. SDP fair-full",
      sub: "+ISM + 512-slot DDIO · 25.9%",
      value: 1011,
      valueText: `${countUp(t, T[3] + 0.15, 0.85, 1011)} cyc`,
      color: CK.sdp,
      k: kBar5,
      tag: "DDIO 100% · DRAM 71×↓",
    },
    {
      label: "6. DirectPush (clean)",
      sub: "Hybrid DPI DMA · 13.0% core",
      value: 508,
      valueText: `${countUp(t, T[3] + 0.55, 0.85, 508)} cyc`,
      color: CK.push,
      k: kBar6,
      tag: "1.99× vs fair SDP",
    },
  ];

  // Coordinates for green cut arrow between row 3 (index 2) and row 4 (index 3)
  const r3EndX = trackX + (1490 / maxCyc) * trackW;
  const r3MidY = chartY + 2 * (barH + gap) + barH;
  const r4EndX = trackX + (1031 / maxCyc) * trackW;
  const r4TopY = chartY + 3 * (barH + gap);

  // Cue 4 & 5 3-size pair data
  const sizePairs = [
    {
      size: "64 B @ 1.5 M/s",
      sdp: 731,
      dp: 474,
      ratio: "1.54×",
      sub: "B=8: 419 → 212 cyc (1.98×)",
      extra: "Idle RTT: 4.635 → 0.885 µs (5.24×)",
      isSmall: true,
      k: kPair1,
    },
    {
      size: "1 KiB @ 1.0 M/s",
      sdp: 956,
      dp: 462,
      ratio: "2.07×",
      sub: "B=8: 694 → 416 cyc (1.67×)",
      extra: "Native DPI DMA · zero copy",
      isSmall: false,
      k: kPair2,
    },
    {
      size: "4 KiB @ 1.0 M/s",
      sdp: 1011,
      dp: 508,
      ratio: "1.99×",
      sub: "B=8: 858 → 461 cyc (1.86×)",
      extra: "100% DDIO on both baselines",
      isSmall: false,
      k: kPair3,
    },
  ];

  const isC5 = prog(t, T[5], 0.5);

  return (
    <>
      <Stage>
        {/* ==================== CUE 0: SYNC-TAIL ARTIFACT SCHEMATIC ==================== */}
        {op0 > 0.01 && (
          <g opacity={op0}>
            {/* Top Retracted Artifact Banner */}
            <g transform={`translate(0, ${lerp(-14, 0, k0Stamp)})`}>
              <rect x={80} y={136} width={1760} height={64} rx={14} fill="#f941441c" stroke={CK.trap} strokeWidth={2.6} />
              <rect x={96} y={148} width={316} height={40} rx={8} fill={CK.trap} />
              <text x={254} y={168} textAnchor="middle" dominantBaseline="central" fontSize={21} fontWeight={900} fill={C.bg} fontFamily={MONO}>
                RETRACTED ARTIFACT
              </text>
              <text x={434} y={168} dominantBaseline="central" fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
                → FIXED IN MRV-166 · Why Sync-Tail SDP Was Unfair on 4 KiB Payloads
              </text>
              <text x={1815} y={168} textAnchor="end" dominantBaseline="central" fontSize={20} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
                126/126 verified runs
              </text>
            </g>

            {/* Main Timeline Panel */}
            <rect x={80} y={222} width={1760} height={446} rx={20} fill="#11182c" stroke={C.line} strokeWidth={2} />
            <text x={112} y={260} fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
              4 KiB Packet Arrival vs. Host SDP Receive Polling Timeline (Header-First PCIe DMA)
            </text>
            <text x={1808} y={260} textAnchor="end" fontSize={19} fill={C.muted} fontFamily={MONO}>
              time →
            </text>

            {/* Time axis line */}
            <line x1={380} y1={284} x2={1790} y2={284} stroke={C.line} strokeWidth={2} />

            {/* Lane 1: PCIe / DMA Arrival */}
            <text x={112} y={346} fontSize={22} fontWeight={700} fill={CK.host} fontFamily={FONT}>
              PCIe Gen5 ×4
            </text>
            <text x={112} y={374} fontSize={18} fill={C.muted} fontFamily={MONO}>
              CN106 → Host LLC
            </text>

            {/* Info word arrives */}
            <rect x={410} y={316} width={170} height={74} rx={10} fill={`${CK.cxl}26`} stroke={CK.cxl} strokeWidth={2.5} />
            <text x={495} y={344} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              info word
            </text>
            <text x={495} y={371} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={MONO}>
              8 B header
            </text>

            {/* 4 KiB payload streaming */}
            <rect x={592} y={316} width={780} height={74} rx={10} fill="#1b2440" stroke={CK.sdp} strokeWidth={2.2} />
            <rect
              x={592}
              y={316}
              width={780 * Math.min(1, 0.28 + 0.72 * streamPhase)}
              height={74}
              rx={10}
              fill={`${CK.sdp}38`}
            />
            <text x={982} y={344} textAnchor="middle" fontSize={21} fontWeight={700} fill={C.text} fontFamily={FONT}>
              4 KiB payload still streaming across PCIe (64 cache lines)
            </text>
            <text x={982} y={371} textAnchor="middle" fontSize={18} fill={CK.sdp} fontFamily={MONO}>
              ~260–420 ns in-flight DMA window
            </text>

            {/* Tail word arrives */}
            <rect x={1384} y={316} width={186} height={74} rx={10} fill={`${CK.ok}26`} stroke={CK.ok} strokeWidth={2.5} />
            <text x={1477} y={344} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              tail word
            </text>
            <text x={1477} y={371} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={MONO}>
              DMA complete
            </text>

            {/* Vertical alignment markers */}
            <line x1={495} y1={392} x2={495} y2={486} stroke={CK.cxl} strokeWidth={2.4} strokeDasharray="7 6" />
            <line x1={1477} y1={392} x2={1477} y2={486} stroke={CK.ok} strokeWidth={2.4} strokeDasharray="7 6" />

            {/* Lane 2: Host CPU (SDP sync-tail) */}
            <text x={112} y={518} fontSize={22} fontWeight={700} fill={CK.trap} fontFamily={FONT}>
              Host CPU (cpu40)
            </text>
            <text x={112} y={546} fontSize={18} fill={C.muted} fontFamily={MONO}>
              SDP sync-tail bug
            </text>

            {/* CPU wakes */}
            <rect x={410} y={488} width={170} height={82} rx={10} fill={`${CK.sdp}28`} stroke={CK.sdp} strokeWidth={2.2} />
            <text x={495} y={520} textAnchor="middle" fontSize={19} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Wakes early
            </text>
            <text x={495} y={549} textAnchor="middle" fontSize={18} fill={CK.cxl} fontFamily={MONO}>
              sees info != 0
            </text>

            {/* Red spin_loop stall */}
            <rect
              x={592}
              y={488}
              width={780 * k0Diag}
              height={82}
              rx={10}
              fill="url(#hatchRed)"
              stroke={CK.trap}
              strokeWidth={3}
            />
            <rect
              x={592}
              y={488}
              width={780 * k0Diag}
              height={82}
              rx={10}
              fill={CK.trap}
              opacity={0.12 + 0.1 * spinPulse}
            />
            <text x={982} y={520} textAnchor="middle" fontSize={24} fontWeight={900} fill={C.text} fontFamily={MONO}>
              spin_loop() blocked waiting for tail!
            </text>
            <text x={982} y={550} textAnchor="middle" fontSize={19} fontWeight={700} fill="#ffb3b5" fontFamily={FONT}>
              Stalls on 54.8% of 4 KiB packets · burns ~459 wasted cycles/pkt
            </text>

            {/* Finally delivers */}
            <rect x={1384} y={488} width={380} height={82} rx={10} fill={`${CK.ok}1f`} stroke={CK.ok} strokeWidth={2.2} />
            <text x={1574} y={520} textAnchor="middle" fontSize={20} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
              Deliver packet &amp; run app_work()
            </text>
            <text x={1574} y={549} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              only after tail word arrives
            </text>

            {/* Bottom callout inside timeline box */}
            <text x={112} y={630} fontSize={20} fontWeight={700} fill={CK.cxl} fontFamily={FONT}>
              Scientific honesty check:
            </text>
            <text x={382} y={630} fontSize={20} fill={C.muted} fontFamily={FONT}>
              We caught this self-inflicted polling wait during review, retracted the artifact, and rebuilt SDP to be 100% non-blocking.
            </text>
          </g>
        )}

        {/* ==================== CUES 1-3: MRV-166 SINGLE-VARIABLE LADDER ==================== */}
        {opLadder > 0.01 && (
          <g opacity={opLadder}>
            {/* Section header strip */}
            <rect x={60} y={136} width={1800} height={46} rx={10} fill="#11182c" stroke={C.line} strokeWidth={1.6} />
            <text x={82} y={159} dominantBaseline="central" fontSize={22} fontWeight={800} fill={C.text} fontFamily={FONT}>
              MRV-166 Single-Variable Host CPU Ablation Ladder
            </text>
            <text x={1838} y={159} textAnchor="end" dominantBaseline="central" fontSize={19} fontWeight={700} fill={CK.push} fontFamily={MONO}>
              4 KiB @ 1.0 M/s (8.19 GB/s bidir) · 1H+1C · cpu40 @ 3.90 GHz
            </text>

            {/* Ladder Chart Frame */}
            <rect x={52} y={196} width={1180} height={670} rx={18} fill="#10172b" stroke={C.line} strokeWidth={1.8} />

            {/* Cyan glow highlight behind DirectPush bar in Cue 3 */}
            {opSide3 > 0.01 && (
              <rect
                x={62}
                y={chartY + 5 * (barH + gap) - 8}
                width={1160}
                height={barH + 16}
                rx={12}
                fill={`${CK.push}14`}
                stroke={CK.push}
                strokeWidth={2.5}
                opacity={opSide3 * kBar6}
              />
            )}

            <HBarChart
              x={chartX}
              y={chartY}
              w={chartW}
              labelW={labelW}
              barH={barH}
              gap={gap}
              max={maxCyc}
              rows={ladderRows}
            />

            {/* Green −459 cyc cut arrow from Bar 3 to Bar 4 (Cue 2+) */}
            {kBar4 > 0.05 && (
              <g opacity={kBar4}>
                <path
                  d={`M ${r3EndX} ${r3MidY + 2} L ${r3EndX} ${r3MidY + 16} L ${r4EndX} ${r3MidY + 16} L ${r4EndX} ${r4TopY - 2}`}
                  fill="none"
                  stroke={CK.ok}
                  strokeWidth={3.5}
                  strokeLinejoin="round"
                />
                <polygon
                  points={`${r4EndX},${r4TopY} ${r4EndX - 8},${r4TopY - 11} ${r4EndX + 8},${r4TopY - 11}`}
                  fill={CK.ok}
                />
                {/* Horizontal leader to open space right of Row 3/4 values */}
                <line x1={r3EndX} y1={r3MidY + 16} x2={r3EndX + 104} y2={r3MidY + 16} stroke={CK.ok} strokeWidth={2.5} strokeDasharray="5 4" />
                <rect x={r3EndX + 104} y={r3MidY + 1} width={126} height={30} rx={7} fill={C.bg} stroke={CK.ok} strokeWidth={2} />
                <text x={r3EndX + 167} y={r3MidY + 16} textAnchor="middle" dominantBaseline="central" fontSize={19} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  −459 cyc
                </text>
              </g>
            )}

            {/* Bottom ladder footnote */}
            <text x={78} y={840} fontSize={18} fill={C.muted} fontFamily={MONO}>
              100% non-multiplexed Host/Card PMU · 126/126 verified runs · Zero hidden cores · Zero Intel DSA
            </text>

            {/* Right-side Cue 2 mini timeline (SVG) showing non-blocking async tail check */}
            {opSide2 > 0.01 && (
              <g opacity={opSide2}>
                <rect x={1256} y={374} width={604} height={282} rx={16} fill="#10172b" stroke={CK.ok} strokeWidth={2} />
                <text x={1280} y={408} fontSize={22} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
                  Non-Blocking Async Tail Check
                </text>
                <text x={1280} y={436} fontSize={18} fill={C.muted} fontFamily={MONO}>
                  rx_poll_tail_off=16 · zero spin_loop()
                </text>

                {/* Step 1: check tail */}
                <rect x={1276} y={460} width={166} height={74} rx={10} fill={`${CK.cxl}22`} stroke={CK.cxl} strokeWidth={2} />
                <text x={1359} y={490} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
                  peek tail
                </text>
                <text x={1359} y={518} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={FONT}>
                  not ready?
                </text>

                <Arrow x1={1444} y1={497} x2={1482} y2={497} color={CK.ok} w={3.5} head={11} />

                {/* Step 2: return to app_work */}
                <rect x={1486} y={460} width={186} height={74} rx={10} fill={`${CK.ok}26`} stroke={CK.ok} strokeWidth={2.4} />
                <text x={1579} y={490} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  app_work()
                </text>
                <text x={1579} y={518} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={FONT}>
                  runs right away
                </text>

                <Arrow x1={1674} y1={497} x2={1710} y2={497} color={CK.push} w={3.5} head={11} />

                {/* Step 3: deliver */}
                <rect x={1714} y={460} width={130} height={74} rx={10} fill={`${CK.push}24`} stroke={CK.push} strokeWidth={2.2} />
                <text x={1779} y={490} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                  deliver
                </text>
                <text x={1779} y={518} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={FONT}>
                  tail ready
                </text>

                {/* Status pills */}
                <rect x={1276} y={560} width={266} height={72} rx={10} fill="#141b2e" stroke={CK.ok} strokeWidth={1.8} />
                <text x={1409} y={587} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                  late_tails rate
                </text>
                <text x={1409} y={616} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  54.8% → 0.0%
                </text>

                <rect x={1560} y={560} width={284} height={72} rx={10} fill="#141b2e" stroke={CK.ok} strokeWidth={1.8} />
                <text x={1702} y={587} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                  SDP CPU after fix
                </text>
                <text x={1702} y={616} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  1,031 cyc (26.4%)
                </text>
              </g>
            )}
          </g>
        )}

        {/* ==================== CUES 4-5: 4-LAYER CPU LEDGER + HONEST BOUNDARY ==================== */}
        {opLedger > 0.01 && (
          <g opacity={opLedger}>
            {/* Top section title */}
            <text x={64} y={156} fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Clean Host CPU Ledger Across Packet Sizes: Fair Async-Tail SDP vs. DirectPush (B=1)
            </text>
            <g transform="translate(1390, 134)">
              <rect x={0} y={4} width={18} height={18} rx={4} fill={CK.sdp} />
              <text x={26} y={19} fontSize={20} fontWeight={700} fill={C.text} fontFamily={FONT}>
                SDP fair-full
              </text>
              <rect x={196} y={4} width={18} height={18} rx={4} fill={CK.push} />
              <text x={222} y={19} fontSize={20} fontWeight={700} fill={C.text} fontFamily={FONT}>
                DirectPush
              </text>
            </g>

            {/* 3 crisp size pair cards */}
            {sizePairs.map((p, idx) => {
              const cx = 60 + idx * 612;
              const cy = 178;
              const cw = 576;
              const ch = 330;
              const barMax = 1100;
              const barTrackW = 335;
              const sdpW = (p.sdp / barMax) * barTrackW * p.k;
              const dpW = (p.dp / barMax) * barTrackW * p.k;
              const cardBorder = isC5 > 0.05 ? (p.isSmall ? CK.ok : C.line) : C.line;
              const cardOp = isC5 > 0.05 ? (p.isSmall ? 1 : lerp(1, 0.72, isC5)) : 1;

              return (
                <g key={p.size} opacity={cardOp}>
                  <rect
                    x={cx}
                    y={cy}
                    width={cw}
                    height={ch}
                    rx={18}
                    fill={p.isSmall && isC5 > 0.2 ? "#0e222a" : "#11182c"}
                    stroke={cardBorder}
                    strokeWidth={p.isSmall && isC5 > 0.2 ? 3 : 1.8}
                  />
                  {/* Card title + ratio pill */}
                  <text x={cx + 24} y={cy + 42} fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
                    {p.size}
                  </text>
                  <rect x={cx + cw - 126} y={cy + 16} width={104} height={38} rx={10} fill={`${CK.push}24`} stroke={CK.push} strokeWidth={2} />
                  <text x={cx + cw - 74} y={cy + 35} textAnchor="middle" dominantBaseline="central" fontSize={23} fontWeight={900} fill={CK.push} fontFamily={MONO}>
                    {p.ratio}
                  </text>

                  {/* SDP Bar */}
                  <text x={cx + 24} y={cy + 98} fontSize={19} fontWeight={700} fill={CK.sdp} fontFamily={MONO}>
                    SDP fair
                  </text>
                  <rect x={cx + 148} y={cy + 74} width={barTrackW} height={40} rx={8} fill="#182038" />
                  <rect x={cx + 148} y={cy + 74} width={sdpW} height={40} rx={8} fill={CK.sdp} opacity={0.9} />
                  <text x={cx + 148 + sdpW + 12} y={cy + 94} dominantBaseline="central" fontSize={22} fontWeight={800} fill={CK.sdp} fontFamily={MONO} opacity={clamp01((p.k - 0.2) * 2)}>
                    {p.sdp}
                  </text>

                  {/* DirectPush Bar */}
                  <text x={cx + 24} y={cy + 162} fontSize={19} fontWeight={700} fill={CK.push} fontFamily={MONO}>
                    DirectPush
                  </text>
                  <rect x={cx + 148} y={cy + 138} width={barTrackW} height={40} rx={8} fill="#182038" />
                  <rect x={cx + 148} y={cy + 138} width={dpW} height={40} rx={8} fill={CK.push} opacity={0.95} />
                  <text x={cx + 148 + dpW + 12} y={cy + 158} dominantBaseline="central" fontSize={22} fontWeight={800} fill={CK.push} fontFamily={MONO} opacity={clamp01((p.k - 0.2) * 2)}>
                    {p.dp}
                  </text>

                  {/* Divider */}
                  <line x1={cx + 24} y1={cy + 196} x2={cx + cw - 24} y2={cy + 196} stroke={C.line} strokeWidth={1.5} />

                  {/* B=8 and extra note */}
                  <text x={cx + 24} y={cy + 228} fontSize={20} fontWeight={700} fill={C.text} fontFamily={MONO}>
                    {p.sub}
                  </text>
                  <text x={cx + 24} y={cy + 260} fontSize={19} fontWeight={p.isSmall ? 800 : 500} fill={p.isSmall ? CK.ok : C.muted} fontFamily={MONO}>
                    {p.extra}
                  </text>

                  {/* Cue 5 small badge on top cards */}
                  {isC5 > 0.05 && (
                    <g opacity={isC5}>
                      <rect
                        x={cx + cw - 274}
                        y={cy + 282}
                        width={252}
                        height={32}
                        rx={7}
                        fill={p.isSmall ? `${CK.ok}28` : `${CK.sdp}22`}
                        stroke={p.isSmall ? CK.ok : CK.sdp}
                        strokeWidth={1.6}
                      />
                      <text
                        x={cx + cw - 148}
                        y={cy + 298}
                        textAnchor="middle"
                        dominantBaseline="central"
                        fontSize={18}
                        fontWeight={800}
                        fill={p.isSmall ? CK.ok : CK.sdp}
                        fontFamily={MONO}
                      >
                        {p.isSmall ? "REAL HW INTERFACE WIN" : "PCIe-ISOMORPHIC"}
                      </text>
                    </g>
                  )}
                </g>
              );
            })}

            {/* Cue 4: Waterfall cards + arrows + InfBadges for Layers 1 -> 2 -> 3 */}
            {opLayers > 0.01 && (
              <g opacity={opLayers}>
                <text x={64} y={548} fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  Shrinking the Gap Honestly: Three Analytical Layers (MRV-166)
                </text>

                {/* Layer 1 SVG Card */}
                <g opacity={kWf1} transform={`translate(0, ${lerp(18, 0, kWf1)})`}>
                  <rect x={60} y={568} width={552} height={292} rx={18} fill={C.panel} stroke={CK.push} strokeWidth={2.2} />
                  <text x={84} y={599} fontSize={18} fontWeight={800} fill={C.muted} fontFamily={MONO}>
                    LAYER 1
                  </text>
                  <text x={84} y={632} fontSize={24} fontWeight={800} fill={CK.push} fontFamily={FONT}>
                    Measured (τ_app = 330.6 ns)
                  </text>
                  <text x={84} y={698} fontSize={54} fontWeight={900} fill={CK.push} fontFamily={MONO}>
                    1.54×–2.07×
                  </text>
                  <text x={84} y={748} fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
                    B=1 across 64 B / 1 KiB / 4 KiB
                  </text>
                  <text x={84} y={786} fontSize={19} fill={C.muted} fontFamily={MONO}>
                    B=8 batched: 1.67×–1.98×
                  </text>
                  <text x={84} y={818} fontSize={18} fill={C.muted} fontFamily={MONO}>
                    Direct non-muxed PMU on saturn cpu40
                  </text>
                </g>

                <Arrow x1={624} y1={712} x2={672} y2={712} color={CK.ok} w={5} head={14} k={kWf2} />

                {/* Layer 2 SVG Card + InfBadge */}
                <g opacity={kWf2} transform={`translate(0, ${lerp(18, 0, kWf2)})`}>
                  <rect x={684} y={568} width={552} height={292} rx={18} fill={C.panel} stroke={CK.ok} strokeWidth={2.2} />
                  <text x={708} y={599} fontSize={18} fontWeight={800} fill={C.muted} fontFamily={MONO}>
                    LAYER 2
                  </text>
                  <InfBadge x={1152} y={580} />
                  <text x={708} y={632} fontSize={24} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
                    Microservice (τ_app = 5 µs)
                  </text>
                  <text x={708} y={698} fontSize={54} fontWeight={900} fill={CK.ok} fontFamily={MONO}>
                    1.35×–1.81×
                  </text>
                  <text x={708} y={748} fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
                    Diluted over realistic 5 µs app work
                  </text>
                  <text x={708} y={786} fontSize={19} fill={C.muted} fontFamily={MONO}>
                    Empty-poll-stripped net: 1.33×–1.79×
                  </text>
                  <text x={708} y={818} fontSize={18} fill={C.muted} fontFamily={MONO}>
                    Projected from measured active cycles
                  </text>
                </g>

                <Arrow x1={1248} y1={712} x2={1296} y2={712} color={CK.sdp} w={5} head={14} k={kWf3} />

                {/* Layer 3 SVG Card + InfBadge */}
                <g opacity={kWf3} transform={`translate(0, ${lerp(18, 0, kWf3)})`}>
                  <rect x={1308} y={568} width={552} height={292} rx={18} fill={C.panel} stroke={CK.sdp} strokeWidth={2.2} />
                  <text x={1332} y={599} fontSize={18} fontWeight={800} fill={C.muted} fontFamily={MONO}>
                    LAYER 3
                  </text>
                  <InfBadge x={1776} y={580} />
                  <text x={1332} y={632} fontSize={24} fontWeight={800} fill={CK.sdp} fontFamily={FONT}>
                    Minus 2-line tail tax
                  </text>
                  <text x={1332} y={698} fontSize={54} fontWeight={900} fill={CK.sdp} fontFamily={MONO}>
                    1.26×–1.53×
                  </text>
                  <text x={1332} y={748} fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
                    Generic DPDK mbuf / refill stack tax
                  </text>
                  <text x={1332} y={786} fontSize={19} fill={C.muted} fontFamily={MONO}>
                    Minus CN106 +114/+120 cyc tail check
                  </text>
                  <text x={1332} y={818} fontSize={18} fill={C.muted} fontFamily={MONO}>
                    Remaining gap: +107–256 cyc/pkt
                  </text>
                </g>
              </g>
            )}
          </g>
        )}
      </Stage>

      {/* ==================== HTML OVERLAYS (OUTSIDE <Stage>) ==================== */}

      {/* Cue 0 bottom stats */}
      {op0 > 0.01 && (
        <>
          <HeroStat
            x={80}
            y={692}
            w={390}
            h={168}
            value="54.8%"
            label="4 KiB Sync-Tail Spins"
            sub="SDP sync-tail spin on 4 KiB"
            color={CK.trap}
            opacity={op0 * k0Stamp}
            dy={lerp(18, 0, k0Stamp)}
          />
          <VerdictRow
            x={500}
            y={704}
            w={1340}
            badge="MRV-166"
            badgeColor={CK.ok}
            title="Retracted sync-tail late_tails claim & inflated 3.6–7.9× default-SDP headline"
            detail="Re-audited 126/126 runs with single-variable ablation, non-blocking async tail polling, ISM, and a 512-slot DDIO ring."
            opacity={op0 * k0Stamp}
            dy={lerp(18, 0, k0Stamp)}
          />
        </>
      )}

      {/* Cue 1 right panel */}
      {opSide1 > 0.01 && (
        <>
          <Card
            x={1256}
            y={196}
            w={604}
            h={448}
            border={CK.sdp}
            title="Steps 1 → 3: Removing Easy Overhead"
            titleColor={CK.sdp}
            style={{ opacity: opSide1 }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 20, marginTop: 10 }}>
              <div style={{ fontSize: 22, color: C.text, lineHeight: 1.4 }}>
                <span style={{ color: CK.sdp, fontWeight: 800 }}>Default SDP (3,862 cyc)</span> burns{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: CK.trap }}>99.0%</span> of a Xeon core on MMIO{" "}
                <code style={{ fontFamily: MONO, color: CK.cxl }}>OUT_CNTS</code> + payload copy.
              </div>
              <div style={{ fontSize: 22, color: C.text, lineHeight: 1.4 }}>
                <span style={{ fontFamily: MONO, color: CK.ok, fontWeight: 800 }}>+rx_poll_mem=1</span> cuts{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800 }}>−1,513 cyc</span>; adding{" "}
                <span style={{ fontFamily: MONO, color: CK.push, fontWeight: 800 }}>zero_copy</span> drops to{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800 }}>1,490 cyc</span>.
              </div>
              <div
                style={{
                  background: "#f9414418",
                  border: `1.5px solid ${CK.trap}`,
                  borderRadius: 12,
                  padding: "14px 18px",
                  fontSize: 21,
                  color: C.text,
                  lineHeight: 1.35,
                }}
              >
                <strong style={{ color: CK.trap }}>Still unfair at Step 3:</strong> sync tail check stalls{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: CK.trap }}>54.8%</span> of packets, and RX ring overflows DDIO (
                <span style={{ fontFamily: MONO }}>45.6%</span> hit).
              </div>
            </div>
          </Card>
          <HeroStat
            x={1256}
            y={674}
            w={604}
            h={190}
            value="1,490 cyc"
            label="Sync-Tail SDP (Still Unfair)"
            sub="38.2% core · 54.8% late_tails · DDIO 45.6%"
            color={CK.trap}
            opacity={opSide1 * kBar3}
            dy={lerp(16, 0, kBar3)}
          />
        </>
      )}

      {/* Cue 2 right panel */}
      {opSide2 > 0.01 && (
        <>
          <Card
            x={1256}
            y={196}
            w={604}
            h={164}
            border={CK.ok}
            title="Step 4: Async Tail Poll Fix"
            titleColor={CK.ok}
            style={{ opacity: opSide2 }}
          >
            <div style={{ fontSize: 21, color: C.text, lineHeight: 1.38 }}>
              <span style={{ fontFamily: MONO, color: CK.ok, fontWeight: 800 }}>rx_poll_tail_off=16</span> returns immediately if tail is in flight — zero spin wait.
            </div>
          </Card>
          <HeroStat
            x={1256}
            y={674}
            w={604}
            h={190}
            value="−459 cyc"
            label="Real Per-Packet Cut on SDP"
            sub="late_tails 54.8% → 0.0% · 1,490 → 1,031 cyc"
            color={CK.ok}
            opacity={opSide2 * kBar4}
            dy={lerp(16, 0, kBar4)}
          />
        </>
      )}

      {/* Cue 3 right panel */}
      {opSide3 > 0.01 && (
        <>
          <Card
            x={1256}
            y={196}
            w={604}
            h={180}
            border={CK.ok}
            title="Step 5: Fair DDIO & ISM Polish"
            titleColor={CK.ok}
            style={{ opacity: opSide3 * kBar5 }}
          >
            <div style={{ display: "flex", gap: 14, marginTop: 4 }}>
              <div
                style={{
                  flex: 1,
                  background: "#43c59e16",
                  border: `1.5px solid ${CK.ok}88`,
                  borderRadius: 12,
                  padding: "10px 14px",
                }}
              >
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>Host DDIO Hit</div>
                <div style={{ fontSize: 24, fontWeight: 900, color: CK.ok, fontFamily: MONO, marginTop: 4 }}>
                  45.2% → 100%
                </div>
              </div>
              <div
                style={{
                  flex: 1,
                  background: "#43c59e16",
                  border: `1.5px solid ${CK.ok}88`,
                  borderRadius: 12,
                  padding: "10px 14px",
                }}
              >
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO }}>DRAM Writes (71×↓)</div>
                <div style={{ fontSize: 24, fontWeight: 900, color: CK.ok, fontFamily: MONO, marginTop: 4 }}>
                  35.6 → 0.5/pkt
                </div>
              </div>
            </div>
          </Card>

          <HeroStat
            x={1256}
            y={400}
            w={604}
            h={226}
            value="1.99×"
            label="DirectPush 508 vs Fair SDP 1,011 cyc"
            sub="4 KiB @ 1.0 M/s · B=1 (13.0% vs 25.9% core)"
            color={CK.push}
            opacity={opSide3 * kBar6}
            dy={lerp(18, 0, kBar6)}
          />

          <Card
            x={1256}
            y={652}
            w={604}
            h={212}
            border={CK.push}
            title="Batched B=8 Check (Same Clean Setup)"
            titleColor={CK.push}
            style={{ opacity: opSide3 * kBar6 }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginTop: 8 }}>
              <span style={{ fontSize: 24, fontWeight: 700, color: C.text }}>
                <span style={{ color: CK.sdp, fontFamily: MONO, fontWeight: 800 }}>858</span> vs{" "}
                <span style={{ color: CK.push, fontFamily: MONO, fontWeight: 800 }}>461 cyc</span>
              </span>
              <span style={{ fontSize: 36, fontWeight: 900, color: CK.push, fontFamily: MONO }}>1.86×</span>
            </div>
            <div style={{ fontSize: 19, color: C.muted, fontFamily: MONO, marginTop: 12 }}>
              22.0% vs 11.8% core · 512-slot DDIO + separate pools
            </div>
          </Card>
        </>
      )}

      {/* Cue 5 bottom Layer 4 Honest Boundary split */}
      {opBoundary > 0.01 && (
        <>
          <Card
            x={60}
            y={544}
            w={880}
            h={320}
            border={CK.sdp}
            style={{
              opacity: opBoundary * kBnd,
              transform: `translateY(${lerp(20, 0, kBnd)}px)`,
              background: "linear-gradient(155deg, rgba(24,28,50,0.96), rgba(14,18,34,0.95))",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
              <span
                style={{
                  fontSize: 19,
                  fontWeight: 800,
                  color: CK.sdp,
                  fontFamily: MONO,
                  background: `${CK.sdp}20`,
                  border: `1.5px solid ${CK.sdp}88`,
                  borderRadius: 8,
                  padding: "5px 12px",
                }}
              >
                LAYER 4 · 1–4 KiB LARGE PAYLOADS
              </span>
              <span style={{ fontSize: 19, color: C.muted, fontFamily: MONO }}>Honest NIC Comparison</span>
            </div>
            <div style={{ fontSize: 30, fontWeight: 800, color: C.text, lineHeight: 1.25 }}>
              PCIe-Isomorphic to Modern Static-Ring NICs
            </div>
            <div style={{ fontSize: 22, color: C.muted, marginTop: 14, lineHeight: 1.42 }}>
              Hardware CQE NICs (<span style={{ color: C.text, fontFamily: MONO, fontWeight: 700 }}>mlx5 / E810</span>) already avoid CN106&apos;s software tail check and heavy DPDK <span style={{ fontFamily: MONO }}>mbuf</span> refill tax.
            </div>
            <div
              style={{
                marginTop: 18,
                padding: "10px 16px",
                borderRadius: 10,
                background: "#b388ff16",
                borderLeft: `5px solid ${CK.sdp}`,
                fontSize: 21,
                fontWeight: 700,
                color: CK.sdp,
              }}
            >
              No hardware CPU magic claimed at 1–4 KiB — only clean software stack savings.
            </div>
          </Card>

          <Card
            x={980}
            y={544}
            w={880}
            h={320}
            border={CK.ok}
            style={{
              opacity: opBoundary * kBnd,
              transform: `translateY(${lerp(20, 0, kBnd)}px)`,
              background: "linear-gradient(155deg, rgba(14,36,42,0.97), rgba(11,18,32,0.96))",
              boxShadow: `0 12px 34px rgba(67,197,158,0.18), inset 0 1px 0 ${CK.ok}44`,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
              <span
                style={{
                  fontSize: 19,
                  fontWeight: 800,
                  color: CK.ok,
                  fontFamily: MONO,
                  background: `${CK.ok}22`,
                  border: `1.5px solid ${CK.ok}`,
                  borderRadius: 8,
                  padding: "5px 12px",
                }}
              >
                LAYER 4 · 64 B SMALL PACKETS · REAL HW WIN
              </span>
              <span style={{ fontSize: 19, color: CK.push, fontFamily: MONO, fontWeight: 700 }}>DirectPush Advantage</span>
            </div>
            <div style={{ fontSize: 30, fontWeight: 800, color: C.text, lineHeight: 1.25 }}>
              Zero TX PCIe Reads &amp; Instant Local Reuse
            </div>
            <div style={{ display: "flex", gap: 14, marginTop: 22 }}>
              <div style={{ flex: 1, background: "#0b1424", border: `1.5px solid ${CK.push}`, borderRadius: 12, padding: "14px 16px" }}>
                <div style={{ fontSize: 32, fontWeight: 900, color: CK.push, fontFamily: MONO }}>0.0 vs 2.2</div>
                <div style={{ fontSize: 18, color: C.text, marginTop: 4, fontWeight: 700 }}>TX DMA reads / pkt</div>
              </div>
              <div style={{ flex: 1, background: "#0b1424", border: `1.5px solid ${CK.ok}`, borderRadius: 12, padding: "14px 16px" }}>
                <div style={{ fontSize: 32, fontWeight: 900, color: CK.ok, fontFamily: MONO }}>5.24×</div>
                <div style={{ fontSize: 18, color: C.text, marginTop: 4, fontWeight: 700 }}>Same-card RTT</div>
                <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO, marginTop: 4 }}>0.885 vs 4.635 µs</div>
              </div>
              <div style={{ flex: 1, background: "#0b1424", border: `1.5px solid ${CK.cxl}`, borderRadius: 12, padding: "14px 16px" }}>
                <div style={{ fontSize: 26, fontWeight: 900, color: CK.cxl, fontFamily: MONO, lineHeight: 1.2 }}>Instant<br />Reuse</div>
                <div style={{ fontSize: 18, color: C.text, marginTop: 6, fontWeight: 700 }}>Zero TX pool</div>
              </div>
            </div>
          </Card>
        </>
      )}
    </>
  );
}

export const C07: SceneDef = {
  id: "C07",
  title: "4 \u00b7 Fair Async-Tail SDP Audit",
  stage: 3,
  source: "MRV-166 (126 runs, verified) \u00b7 MRV-151 updated 4-layer CPU ledger",
  dur: DUR,
  cues,
  C: Body,
};
