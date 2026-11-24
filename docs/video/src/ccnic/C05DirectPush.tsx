import React from "react";
import {
  Arrow,
  C,
  Cue,
  FONT,
  MONO,
  PacketStream,
  Pt,
  SceneDef,
  Stage,
  countUp,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import {
  BoundaryBar,
  CK,
  HBarChart,
  HBarRow,
  HeroStat,
  QuestionBanner,
} from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 10.66, "**Question 4**: Direct push shines at **64 B**—but pushing **4 KiB** wastes host CPU. How do we handle **mixed packet sizes** cleanly?", "Question four: direct push shines for sixty-four byte control messages, but pushing four kilobytes line by line wastes host C P U. How do we handle real mixed traffic?"], // 0
  [11.01, 28.31, "**DirectPush** splits by size (**zero DSA**): **≤ 256 B** pushes inline (**0.89 µs**); **≥ 512 B** pushes one **64 B** command for **native NIC DMA**.", "DirectPush splits cleanly by message size with zero Intel D S A. Messages up to two hundred fifty-six bytes go inline in zero point eight nine microseconds. Larger payloads push a single sixty-four byte command for zero-copy built-in D M A at ninety-seven percent of line rate."], // 1
  [28.66, 45.30, "Wider DMA packets amortize PCIe headers: **4 KiB** host CPU drops **2.86×** (**46.6% → 16.3%**), lifting `Mix-Geo` **2.41×** (**7.8 → 18.7 GB/s**).", "Wider D M A packets amortize P C I express headers. On four kilobytes, host C P U drops two point eight six times—from forty-six point six to sixteen point three percent—lifting large-heavy Mix-Geo from seven point eight to eighteen point seven gigabytes per second."], // 2
  [45.65, 63.09, "Dual **small/large credit windows** stop head-of-line blocking: under saturated **18.6 GB/s** 4 KiB load, 64 B **p99** drops **40.5 ms → 2.75 µs**.", "Giving small and large traffic two separate credit windows eliminates head-of-line blocking. Under saturated eighteen point six gigabyte per second bulk traffic, sixty-four byte tail latency drops from forty point five milliseconds to two point seven five microseconds—over fourteen thousand times lower."], // 3
  [63.44, 89.14, "Against a **fair zero-copy SDP** baseline (non-blocking, **100% DDIO**), DirectPush still saves **1.5–2.1×** host CPU and **5.24×** 64 B RTT.", "Now for the fairness check every systems advisor asks first: we aggressively tuned the card's stock S D P driver with zero-copy, non-blocking completions, and full D D I O. Even against that fair zero-copy S D P baseline, DirectPush still cuts host C P U by one point five to two point one times across sixty-four bytes to four kilobytes, while delivering five point two four times lower sixty-four byte latency."], // 4
  [89.49, 109.55, "**Honest boundary**: **1–4 KiB** savings mostly avoid DPDK stack overhead; at **64 B**, eliminating TX DMA reads is a **true hardware win**.", "One plain-English boundary keeps our claims completely honest. At one to four kilobytes, a modern static-ring network card has an isomorphic P C I express transfer, so our C P U win mainly reflects skipping D P D K software stack overhead. At sixty-four bytes, however, eliminating transmit D M A reads is a genuine hardware win."], // 5
];
const DUR = 110.95; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const realT = useRealT();

  // Cue opacities (sequential beats finish fading out before next fades in)
  const o0 = fade(t, T[0], T[1], 0.5);
  const oArch = fade(t, T[1], T[4], 0.5);
  const compactK = prog(t, T[2], 0.7);
  const oDsaBadge = oArch * (1 - compactK);
  const o2 = fade(t, T[2] + 0.1, T[3] - 0.1, 0.45);
  const o3 = fade(t, T[3] + 0.1, T[4], 0.45);
  const oCred = prog(t, T[3], 0.6) * (1 - prog(t, T[4], 0.5));
  const oAct2 = fade(t, T[4], DUR + 2, 0.5);
  const o5Bound = fade(t, T[5], DUR + 2, 0.5);

  // Cue 0: congested single pipe animation
  const stallDrift = (realT * 28) % 240;
  const stallPulse = 0.55 + 0.45 * Math.sin(realT * 5.2);

  // Dual-Path Architecture morph geometry (full in Cue 1 -> compact in Cues 2-3)
  const hostBoxY = lerp(220, 144, compactK);
  const hostBoxH = lerp(475, 254, compactK);
  const topBlockY = lerp(272, 178, compactK);
  const topBlockH = lerp(136, 78, compactK);
  const botBlockY = lerp(476, 294, compactK);
  const botBlockH = lerp(176, 86, compactK);
  const topLaneY = topBlockY + topBlockH / 2;
  const botLaneY = botBlockY + botBlockH / 2;

  const topPts: Pt[] = [
    [390, topLaneY],
    [1495, topLaneY],
  ];
  const cmdPts: Pt[] = [
    [390, botLaneY - lerp(28, 16, compactK)],
    [1495, botLaneY - lerp(28, 16, compactK)],
  ];
  const dmaPts: Pt[] = [
    [1495, botLaneY + lerp(28, 16, compactK)],
    [390, botLaneY + lerp(28, 16, compactK)],
  ];

  // Cue 2 chart progress
  const k2a = prog(t, T[2] + 0.25, 1.0);
  const k2b = prog(t, T[2] + 0.55, 1.0);
  const cpuRows: HBarRow[] = [
    { label: "Full Direct Push", value: 46.6, valueText: "46.6%", color: CK.trap, k: k2a },
    { label: "DirectPush + DMA", value: 16.3, valueText: "16.3%", color: CK.ok, k: k2b },
  ];
  const mixRows: HBarRow[] = [
    { label: "Full Direct Push", value: 7.8, valueText: "7.8 GB/s", color: CK.trap, k: k2a },
    { label: "DirectPush Hybrid", value: 18.7, valueText: "18.7 GB/s", color: CK.ok, k: k2b },
  ];

  // Cue 3 count-ups (finish within 1.2s)
  const holBadStr = `${countUp(t, T[3] + 0.15, 1.1, 40.5, 1)} ms`;
  const holGoodStr = `${countUp(t, T[3] + 0.35, 1.1, 2.75, 2)} µs`;

  // Cues 4-5: Fair Zero-Copy SDP vs. DirectPush (2-bar chart)
  const fairRows: HBarRow[] = [
    {
      label: "Fair Zero-Copy SDP",
      sub: "Non-blocking · 100% DDIO",
      value: 1011,
      valueText: "1,011 cyc/pkt",
      color: CK.sdp,
      k: prog(t, T[4] + 0.2, 0.95),
    },
    {
      label: "DirectPush Hybrid",
      sub: "Small push + native NIC DMA",
      value: 508,
      valueText: "508 cyc/pkt",
      color: CK.ok,
      tag: "~2× less CPU",
      k: prog(t, T[4] + 0.55, 0.95),
    },
  ];

  return (
    <>
      <Stage>
        {/* ================= CUE 0: Congested Single Pipe ================= */}
        {o0 > 0.01 && (
          <g opacity={o0}>
            <text
              x={960}
              y={465}
              textAnchor="middle"
              fontSize={28}
              fontWeight={800}
              fill={CK.trap}
              fontFamily={FONT}
            >
              Single Direct-Push Pipe — Giant 4 KiB Transfers Stall 64 B RPCs
            </text>

            {/* Left Host Core */}
            <rect
              x={100}
              y={505}
              width={250}
              height={180}
              rx={18}
              fill="#141b2e"
              stroke={CK.host}
              strokeWidth={2.4}
            />
            <text x={225} y={580} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host Core
            </text>
            <text x={225} y={620} textAnchor="middle" fontSize={20} fill={C.muted} fontFamily={MONO}>
              CPU line-by-line
            </text>

            {/* Right NIC L3 */}
            <rect
              x={1570}
              y={505}
              width={250}
              height={180}
              rx={18}
              fill="#141b2e"
              stroke={CK.card}
              strokeWidth={2.4}
            />
            <text x={1695} y={580} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text} fontFamily={FONT}>
              NIC L3
            </text>
            <text x={1695} y={620} textAnchor="middle" fontSize={20} fill={C.muted} fontFamily={MONO}>
              PCIe Gen5 ×4
            </text>

            {/* Pipe body */}
            <rect
              x={380}
              y={535}
              width={1160}
              height={120}
              rx={22}
              fill="#11182c"
              stroke={CK.trap}
              strokeWidth={2.4}
              strokeDasharray="10 6"
            />

            {/* Giant 4 KiB block slowly creeping */}
            <g transform={`translate(${940 + stallDrift * 0.45}, 555)`}>
              <rect
                x={0}
                y={0}
                width={420}
                height={80}
                rx={14}
                fill={`${CK.dma}2a`}
                stroke={CK.dma}
                strokeWidth={3}
              />
              <text x={210} y={33} textAnchor="middle" fontSize={26} fontWeight={800} fill={CK.dma} fontFamily={MONO}>
                4 KiB Bulk Push
              </text>
              <text x={210} y={62} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={MONO}>
                64 × 64 B CPU writes
              </text>
            </g>

            {/* Tiny 64 B packets jammed behind it */}
            {[0, 1, 2, 3].map((i) => {
              const bx = 435 + i * 108;
              return (
                <g key={i} transform={`translate(${bx}, 567)`}>
                  <rect
                    x={0}
                    y={0}
                    width={86}
                    height={56}
                    rx={10}
                    fill={`${CK.push}26`}
                    stroke={CK.push}
                    strokeWidth={2.2}
                  />
                  <text x={43} y={34} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                    64 B
                  </text>
                </g>
              );
            })}

            {/* Pulsing stall warning */}
            <g opacity={stallPulse}>
              <rect
                x={620}
                y={690}
                width={680}
                height={48}
                rx={12}
                fill={`${CK.trap}1e`}
                stroke={CK.trap}
                strokeWidth={1.8}
              />
              <text x={960} y={721} textAnchor="middle" fontSize={22} fontWeight={700} fill={CK.trap} fontFamily={FONT}>
                Tiny 64 B RPCs blocked behind 4 KiB CPU-pushed payload
              </text>
            </g>
          </g>
        )}

        {/* ================= CUES 1-3: DirectPush Dual-Path Architecture ================= */}
        {oArch > 0.01 && (
          <g opacity={oArch}>
            {/* Top-right crossed-out Intel DSA badge (full Cue 1 view) */}
            {oDsaBadge > 0.02 && (
              <g opacity={oDsaBadge}>
                <rect
                  x={1345}
                  y={142}
                  width={495}
                  height={50}
                  rx={12}
                  fill="#141b2e"
                  stroke={CK.cxl}
                  strokeWidth={2}
                />
                <text x={1425} y={174} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  Intel DSA ✗
                </text>
                <line x1={1362} y1={167} x2={1470} y2={167} stroke={CK.trap} strokeWidth={2.5} />
                <text x={1665} y={174} textAnchor="middle" fontSize={21} fontWeight={700} fill={C.text} fontFamily={FONT}>
                  (Zero CPU Accelerators)
                </text>
              </g>
            )}

            {/* Section title in Cue 1 */}
            {oDsaBadge > 0.02 && (
              <text
                x={95}
                y={178}
                fontSize={32}
                fontWeight={800}
                fill={C.text}
                fontFamily={FONT}
                opacity={oDsaBadge}
              >
                DirectPush Dual-Path Architecture
              </text>
            )}

            {/* Host Container */}
            <rect
              x={90}
              y={hostBoxY}
              width={290}
              height={hostBoxH}
              rx={20}
              fill="#10182b"
              stroke={CK.host}
              strokeWidth={2.4}
            />
            <text
              x={235}
              y={hostBoxY + lerp(32, 23, compactK)}
              textAnchor="middle"
              fontSize={lerp(24, 17, compactK)}
              fontWeight={800}
              fill={CK.host}
              fontFamily={MONO}
            >
              HOST (saturn)
            </text>

            {/* Host Core */}
            <rect
              x={116}
              y={topBlockY}
              width={238}
              height={topBlockH}
              rx={14}
              fill="#17223b"
              stroke={CK.push}
              strokeWidth={2}
            />
            <text
              x={235}
              y={topLaneY - lerp(12, 6, compactK)}
              textAnchor="middle"
              fontSize={lerp(26, 22, compactK)}
              fontWeight={800}
              fill={C.text}
              fontFamily={FONT}
            >
              Host Core
            </text>
            <text
              x={235}
              y={topLaneY + lerp(22, 16, compactK)}
              textAnchor="middle"
              fontSize={lerp(18, 15, compactK)}
              fill={CK.push}
              fontFamily={MONO}
            >
              size-split TX
            </text>

            {/* Host RAM */}
            <rect
              x={116}
              y={botBlockY}
              width={238}
              height={botBlockH}
              rx={14}
              fill="#17223b"
              stroke={CK.dma}
              strokeWidth={2}
            />
            <text
              x={235}
              y={botLaneY - lerp(14, 7, compactK)}
              textAnchor="middle"
              fontSize={lerp(26, 22, compactK)}
              fontWeight={800}
              fill={C.text}
              fontFamily={FONT}
            >
              Host RAM
            </text>
            <text
              x={235}
              y={botLaneY + lerp(22, 16, compactK)}
              textAnchor="middle"
              fontSize={lerp(18, 15, compactK)}
              fill={CK.dma}
              fontFamily={MONO}
            >
              zero-copy payloads
            </text>

            {/* NIC Container */}
            <rect
              x={1505}
              y={hostBoxY}
              width={325}
              height={hostBoxH}
              rx={20}
              fill="#10182b"
              stroke={CK.card}
              strokeWidth={2.4}
            />
            <text
              x={1668}
              y={hostBoxY + lerp(32, 22, compactK)}
              textAnchor="middle"
              fontSize={lerp(24, 18, compactK)}
              fontWeight={800}
              fill={CK.card}
              fontFamily={MONO}
            >
              NIC (CN106)
            </text>

            {/* NIC L3 */}
            <rect
              x={1532}
              y={topBlockY}
              width={272}
              height={topBlockH}
              rx={14}
              fill="#17223b"
              stroke={CK.push}
              strokeWidth={2}
            />
            <text
              x={1668}
              y={topLaneY - lerp(12, 6, compactK)}
              textAnchor="middle"
              fontSize={lerp(26, 22, compactK)}
              fontWeight={800}
              fill={C.text}
              fontFamily={FONT}
            >
              NIC L3
            </text>
            <text
              x={1668}
              y={topLaneY + lerp(22, 16, compactK)}
              textAnchor="middle"
              fontSize={lerp(18, 15, compactK)}
              fill={CK.push}
              fontFamily={MONO}
            >
              inline landing
            </text>

            {/* Built-In Native DMA */}
            <rect
              x={1532}
              y={botBlockY}
              width={272}
              height={botBlockH}
              rx={14}
              fill="#17223b"
              stroke={CK.dma}
              strokeWidth={2}
            />
            <text
              x={1668}
              y={botLaneY - lerp(14, 7, compactK)}
              textAnchor="middle"
              fontSize={lerp(25, 20, compactK)}
              fontWeight={800}
              fill={C.text}
              fontFamily={FONT}
            >
              Built-In Native DMA
            </text>
            <text
              x={1668}
              y={botLaneY + lerp(22, 16, compactK)}
              textAnchor="middle"
              fontSize={lerp(18, 15, compactK)}
              fill={CK.dma}
              fontFamily={MONO}
            >
              pulls payload
            </text>

            {/* ===== TOP LANE: Small <= 256 B ===== */}
            {/* Halo under horizontal line (never filter=#glow on H/V line!) */}
            <line
              x1={390}
              y1={topLaneY}
              x2={1495}
              y2={topLaneY}
              stroke={CK.push}
              strokeWidth={14}
              opacity={0.14}
            />
            <Arrow x1={390} y1={topLaneY} x2={1495} y2={topLaneY} color={CK.push} w={5} head={16} />
            <PacketStream pts={topPts} travel={realT * 210} gap={130} size={13} color={CK.push} />

            {/* Top lane badges */}
            <g transform={`translate(425, ${topLaneY - lerp(62, 42, compactK)})`}>
              <rect x={0} y={0} width={220} height={lerp(38, 30, compactK)} rx={8} fill={`${CK.push}22`} stroke={CK.push} strokeWidth={1.8} />
              <text
                x={110}
                y={lerp(20, 16, compactK)}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={lerp(20, 17, compactK)}
                fontWeight={800}
                fill={CK.push}
                fontFamily={MONO}
              >
                Small ≤ 256 B
              </text>
            </g>
            <text
              x={950}
              y={topLaneY - lerp(36, 22, compactK)}
              textAnchor="middle"
              fontSize={lerp(23, 19, compactK)}
              fontWeight={700}
              fill={C.text}
              fontFamily={FONT}
              opacity={1 - oCred * 0.9}
            >
              64 B Direct Push → NIC L3
            </text>
            <g transform={`translate(1285, ${topLaneY - lerp(62, 42, compactK)})`}>
              <rect x={0} y={0} width={170} height={lerp(38, 30, compactK)} rx={8} fill={`${CK.ok}24`} stroke={CK.ok} strokeWidth={1.8} />
              <text
                x={85}
                y={lerp(20, 16, compactK)}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={lerp(21, 18, compactK)}
                fontWeight={800}
                fill={CK.ok}
                fontFamily={MONO}
              >
                0.89 µs
              </text>
            </g>

            {/* ===== BOTTOM LANE: Large >= 512 B ===== */}
            {/* Upper command line (Host -> NIC) */}
            <Arrow
              x1={390}
              y1={botLaneY - lerp(28, 16, compactK)}
              x2={1495}
              y2={botLaneY - lerp(28, 16, compactK)}
              color={CK.cxl}
              w={3.2}
              dash="10 7"
              head={13}
            />
            <PacketStream pts={cmdPts} travel={realT * 165} gap={260} size={10} color={CK.cxl} />

            {/* Lower DMA pull stream (NIC -> Host RAM zero-copy pull) */}
            <line
              x1={390}
              y1={botLaneY + lerp(28, 16, compactK)}
              x2={1495}
              y2={botLaneY + lerp(28, 16, compactK)}
              stroke={CK.dma}
              strokeWidth={14}
              opacity={0.14}
            />
            <Arrow
              x1={1495}
              y1={botLaneY + lerp(28, 16, compactK)}
              x2={390}
              y2={botLaneY + lerp(28, 16, compactK)}
              color={CK.dma}
              w={5.5}
              head={16}
            />
            <PacketStream pts={dmaPts} travel={realT * 190} gap={110} size={14} color={CK.dma} />

            {/* Bottom lane badges */}
            <g transform={`translate(425, ${botLaneY - lerp(84, 54, compactK)})`}>
              <rect x={0} y={0} width={220} height={lerp(38, 30, compactK)} rx={8} fill={`${CK.dma}22`} stroke={CK.dma} strokeWidth={1.8} />
              <text
                x={110}
                y={lerp(20, 16, compactK)}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={lerp(20, 17, compactK)}
                fontWeight={800}
                fill={CK.dma}
                fontFamily={MONO}
              >
                Large ≥ 512 B
              </text>
            </g>
            <text
              x={950}
              y={botLaneY - lerp(52, 34, compactK)}
              textAnchor="middle"
              fontSize={lerp(21, 17, compactK)}
              fontWeight={700}
              fill={CK.cxl}
              fontFamily={FONT}
              opacity={1 - oCred * 0.9}
            >
              1 × 64 B Command →
            </text>
            <text
              x={950}
              y={botLaneY + lerp(66, 46, compactK)}
              textAnchor="middle"
              fontSize={lerp(22, 17, compactK)}
              fontWeight={700}
              fill={CK.dma}
              fontFamily={FONT}
              opacity={1 - oCred * 0.9}
            >
              ← Native DMA Pulls Payload Zero-Copy from Host RAM
            </text>
            <g transform={`translate(1240, ${botLaneY - lerp(84, 54, compactK)})`}>
              <rect x={0} y={0} width={215} height={lerp(38, 30, compactK)} rx={8} fill={`${CK.dma}24`} stroke={CK.dma} strokeWidth={1.8} />
              <text
                x={107}
                y={lerp(20, 16, compactK)}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={lerp(20, 17, compactK)}
                fontWeight={800}
                fill={CK.dma}
                fontFamily={MONO}
              >
                97% line rate
              </text>
            </g>

            {/* Cue 3: TWO independent glowing Credit Gate boxes */}
            {oCred > 0.01 && (
              <g opacity={oCred}>
                {/* Small Credit Window gate on top lane */}
                <rect
                  x={785}
                  y={topLaneY - 26}
                  width={310}
                  height={52}
                  rx={12}
                  fill="#0b1020"
                  stroke={CK.push}
                  strokeWidth={6}
                  opacity={0.3}
                />
                <rect
                  x={785}
                  y={topLaneY - 26}
                  width={310}
                  height={52}
                  rx={12}
                  fill="#0f1a30"
                  stroke={CK.push}
                  strokeWidth={2.6}
                />
                <text
                  x={940}
                  y={topLaneY + 1}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={20}
                  fontWeight={800}
                  fill={CK.push}
                  fontFamily={MONO}
                >
                  Small Credit Window
                </text>

                {/* Large Credit Window gate on bottom lane */}
                <rect
                  x={785}
                  y={botLaneY - 26}
                  width={310}
                  height={52}
                  rx={12}
                  fill="#0b1020"
                  stroke={CK.dma}
                  strokeWidth={6}
                  opacity={0.3}
                />
                <rect
                  x={785}
                  y={botLaneY - 26}
                  width={310}
                  height={52}
                  rx={12}
                  fill="#0f1a30"
                  stroke={CK.dma}
                  strokeWidth={2.6}
                />
                <text
                  x={940}
                  y={botLaneY + 1}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={20}
                  fontWeight={800}
                  fill={CK.dma}
                  fontFamily={MONO}
                >
                  Large Credit Window
                </text>
              </g>
            )}
          </g>
        )}

        {/* ================= CUE 2: Side-by-Side Proof Charts ================= */}
        {o2 > 0.01 && (
          <g opacity={o2}>
            {/* Left panel: 4 KiB Host CPU Usage */}
            <rect
              x={85}
              y={435}
              width={855}
              height={385}
              rx={20}
              fill="#10182b"
              stroke={C.line}
              strokeWidth={1.8}
            />
            <text x={115} y={488} fontSize={27} fontWeight={800} fill={C.text} fontFamily={FONT}>
              4 KiB Host CPU Usage
            </text>
            <rect x={675} y={456} width={235} height={42} rx={10} fill={`${CK.ok}20`} stroke={CK.ok} strokeWidth={1.8} />
            <text x={792} y={478} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              2.86× less CPU
            </text>
            <HBarChart x={105} y={555} w={815} labelW={240} barH={52} gap={34} max={55} rows={cpuRows} />
            <text x={115} y={785} fontSize={20} fill={C.muted} fontFamily={FONT}>
              Wider DMA transfers amortize PCIe headers and free host cores
            </text>

            {/* Right panel: Mix-Geo Throughput */}
            <rect
              x={980}
              y={435}
              width={855}
              height={385}
              rx={20}
              fill="#10182b"
              stroke={C.line}
              strokeWidth={1.8}
            />
            <text x={1010} y={488} fontSize={27} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Large-Heavy Mix-Geo Throughput
            </text>
            <rect x={1605} y={456} width={200} height={42} rx={10} fill={`${CK.ok}20`} stroke={CK.ok} strokeWidth={1.8} />
            <text x={1705} y={478} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              2.41× faster
            </text>
            <HBarChart x={1000} y={555} w={815} labelW={240} barH={52} gap={34} max={22} rows={mixRows} />
            <text x={1010} y={785} fontSize={20} fill={C.muted} fontFamily={FONT}>
              Mixed small + bulk traffic runs near full link bandwidth
            </text>
          </g>
        )}

        {/* ================= CUE 3: Head-of-Line Isolation Center Arrow ================= */}
        {o3 > 0.01 && (
          <g opacity={o3}>
            <text
              x={960}
              y={462}
              textAnchor="middle"
              fontSize={28}
              fontWeight={800}
              fill={C.text}
              fontFamily={FONT}
            >
              Head-of-Line Isolation Under Saturated 18.6 GB/s 4 KiB Load
            </text>

            {/* Big center arrow */}
            <line x1={720} y1={650} x2={1190} y2={650} stroke={CK.ok} strokeWidth={22} opacity={0.14} />
            <Arrow x1={720} y1={650} x2={1190} y2={650} color={CK.ok} w={8} head={24} k={prog(t, T[3] + 0.25, 0.7)} />

            <rect
              x={785}
              y={548}
              width={350}
              height={62}
              rx={14}
              fill={`${CK.ok}22`}
              stroke={CK.ok}
              strokeWidth={2.4}
            />
            <text
              x={960}
              y={580}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={34}
              fontWeight={800}
              fill={CK.ok}
              fontFamily={MONO}
            >
              14,700× lower
            </text>
            <text
              x={960}
              y={712}
              textAnchor="middle"
              fontSize={22}
              fontWeight={600}
              fill={C.muted}
              fontFamily={FONT}
            >
              Separate small / large credit windows
            </text>
          </g>
        )}

        {/* ================= CUES 4-5: Fair Zero-Copy SDP vs. DirectPush ================= */}
        {oAct2 > 0.01 && (
          <g opacity={oAct2}>
            <text x={100} y={182} fontSize={32} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Fair Zero-Copy SDP vs. DirectPush
            </text>
            <text x={100} y={222} fontSize={22} fill={C.muted} fontFamily={MONO}>
              4 KiB @ 1 M/s · Host CPU cycles per packet
            </text>
            <HBarChart
              x={100}
              y={265}
              w={1720}
              labelW={420}
              barH={64}
              gap={34}
              max={1200}
              rows={fairRows}
            />
          </g>
        )}
      </Stage>

      {/* ================= HTML OVERLAYS (strictly outside <Stage>) ================= */}

      {/* Cue 0 Question Banner */}
      {o0 > 0.01 && (
        <QuestionBanner
          num="QUESTION 4"
          question="Pushing 4 KiB wastes host CPU — how do we handle mixed traffic cleanly?"
          sub="Real workloads mix tiny 64 B RPCs with multi-kilobyte bulk transfers."
          color={CK.dma}
          y={180}
          opacity={o0}
          dy={lerp(18, 0, prog(t, T[0], 0.6))}
        />
      )}

      {/* Cue 3 Head-of-Line Left Red Card & Right Green HeroStat */}
      {o3 > 0.01 && (
        <>
          <div
            style={{
              position: "absolute",
              left: 110,
              top: 515,
              width: 560,
              height: 270,
              opacity: o3,
              background: "linear-gradient(155deg, rgba(35,20,34,0.96), rgba(14,16,32,0.94))",
              border: `2px solid ${CK.trap}`,
              borderRadius: 20,
              padding: "24px 30px",
              boxSizing: "border-box",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              boxShadow: `0 14px 36px rgba(0,0,0,0.45), inset 0 1px 0 ${CK.trap}40`,
            }}
          >
            <div style={{ fontSize: 19, fontWeight: 800, color: CK.trap, fontFamily: MONO, letterSpacing: 0.6 }}>
              SHARED WINDOW · 18.6 GB/s 4 KiB
            </div>
            <div style={{ fontSize: 66, fontWeight: 800, color: CK.trap, fontFamily: MONO, lineHeight: 1.08, marginTop: 10 }}>
              {holBadStr}
            </div>
            <div style={{ fontSize: 23, fontWeight: 700, color: C.text, marginTop: 12 }}>
              64 B p99 blocked behind bulk
            </div>
          </div>

          <HeroStat
            x={1250}
            y={515}
            w={560}
            h={270}
            value={holGoodStr}
            label="Dual Credit Windows"
            sub="64 B p99 stays crisp under full 4 KiB load"
            color={CK.ok}
            opacity={o3}
          />
        </>
      )}

      {/* Cue 4-5 Two Spacious Executive HeroStat Cards */}
      {oAct2 > 0.01 && (
        <>
          <HeroStat
            x={100}
            y={472}
            w={835}
            h={180}
            value="1.5–2.1×"
            label="Less Host CPU"
            sub="64 B – 4 KiB vs fair zero-copy SDP"
            color={CK.ok}
            opacity={oAct2}
            dy={lerp(18, 0, prog(t, T[4] + 0.35, 0.55))}
          />
          <HeroStat
            x={985}
            y={472}
            w={835}
            h={180}
            value="5.24×"
            label="Lower 64 B Idle RTT"
            sub="true hardware advantage"
            color={CK.push}
            opacity={oAct2}
            dy={lerp(18, 0, prog(t, T[4] + 0.5, 0.55))}
          />
        </>
      )}

      {/* Cue 5 Plain-English Honest Boundary Bar */}
      {o5Bound > 0.01 && (
        <BoundaryBar
          badge="HONEST BOUNDARY"
          text="At 1–4 KiB, savings mostly avoid DPDK software stack overhead (isomorphic to static-ring NICs); at 64 B, eliminating TX DMA reads is a genuine hardware win."
          color={CK.cxl}
          x={100}
          y={705}
          w={1720}
          opacity={o5Bound}
          dy={lerp(14, 0, prog(t, T[5], 0.55))}
        />
      )}
    </>
  );
}

export const C05: SceneDef = {
  id: "C05",
  title: "4 \u00b7 DirectPush: Hybrid + Fair CPU",
  stage: 3,
  source: "Small Direct Push + Native NIC DMA \u00b7 Fair Zero-Copy SDP Baseline",
  dur: DUR,
  cues,
  C: Body,
};
