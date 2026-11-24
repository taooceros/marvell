import React from "react";
import {
  Axes,
  C,
  Cue,
  FONT,
  HLine,
  MONO,
  Packet,
  PacketStream,
  Pill,
  SceneDef,
  Series,
  Stage,
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
import { BoundaryBar, CK, HeroStat, QuestionBanner } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 10.08, "**Question 2**: Push is fast—but what happens when **TCP** must retain sent buffers in host memory until remote ACKs arrive?", "Question two: push is fast, but when T C P must retain sent buffers in host memory until remote acks arrive, doesn't a large transmit pool kill performance?"], // 0
  [10.43, 21.83, "Separate **two lifetimes**: the **host-to-NIC PCIe hop** is lossless, while **end-to-end TCP retention** lives purely on the host.", "The key insight is separating two lifetimes that prior work mixed together. The host to card P C I express hop is lossless, while end-to-end retransmission buffering belongs strictly to the host."], // 1
  [22.18, 35.94, "Direct push copies **64 B by value** into NIC memory—the NIC **never reads host TX RAM**. Reusing **one 64 B buffer** hits **174.8 M/s**.", "Direct push copies sixty-four bytes by value into the NIC, so the NIC never reads host transmit memory back. Overwriting one single sixty-four byte buffer hits one hundred seventy-four point eight million per second with zero pool."], // 2
  [36.29, 47.93, "On coherent **CC-NIC**, host TCP retention triggers **cross-link invalidations**: **−20%** on retention alone (up to **−73%** with host pools).", "On coherent C C NIC, keeping T C P buffers in host memory forces cross-link cache invalidations—costing twenty percent on retention alone, and up to seventy-three percent with host pools."], // 3
  [48.28, 62.84, "Without coherence, NIC caches **never mirror** host TX RAM: growing the pool **2,000×** (**64 KiB → 128 MiB**) holds **47.5 → 47.1 M/s** (**−0.9%**).", "Without coherence, NIC caches never mirror host transmit memory. Growing a sequential pool two thousand times—from sixty-four kilobytes to one hundred twenty-eight megabytes, twice the host cache—keeps throughput flat within zero point nine percent."], // 4
  [63.19, 78.97, "Across **816 M** replies, receive **DDIO** stays **99.99993%** (**0** misses at 128 MiB). Random slowdown is purely local DDR5—**0% coherence tax**.", "Across eight hundred sixteen million replies, receive D D I O hits stay at ninety-nine point nine nine nine nine percent with zero misses. Any random-access slowdown comes purely from local host D D R five, with zero cross-link coherence tax."], // 5
];
const DUR = 80.37; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const POOL_PTS: [number, number][] = [
  [64, 47.5],
  [4096, 48.1],
  [32768, 47.8],
  [131072, 47.1],
];

function Body() {
  const t = useT();
  const rt = useRealT();

  // Beat opacities (smooth 0.5s crossfades, no text collision)
  const op0 = fade(t, T[0], T[1] - 0.1, 0.5);
  const op1 = fade(t, T[1], T[2] - 0.1, 0.5);
  const op2 = fade(t, T[2], T[3] - 0.1, 0.5);
  const op3 = fade(t, T[3], T[4] - 0.1, 0.5);
  const op45 = fade(t, T[4], DUR, 0.5);
  const op4Only = fade(t, T[4] + 0.6, T[5] - 0.15, 0.45);
  const op5 = fade(t, T[5] + 0.1, DUR, 0.5);

  // Cue 2 count-up (finishes in 1.3s)
  const pushRate = countUp(t, T[2] + 0.1, 1.3, 174.8, 1);

  // Cue 4 -> 5 chart morph
  const m5 = prog(t, T[5], 0.7);
  const cx0 = lerp(210, 165, m5);
  const cx1 = lerp(1460, 1090, m5);
  const cy0 = lerp(695, 635, m5);
  const cy1 = lerp(225, 215, m5);
  const sx = logScale(64, 131072, cx0, cx1);
  const sy = linScale(0, 60, cy0, cy1);
  const kCurve = prog(t, T[4] + 0.25, 1.2);
  const llcX = sx(61440);

  // Ambient motion
  const pulse = 0.5 + 0.5 * Math.sin(rt * 4.5);
  const ackK = (rt * 0.28) % 1;
  const invPhase = (rt * 1.4) % 1;

  return (
    <>
      <Stage>
        {/* ==================== CUE 0: TCP Retransmit Queue Visual ==================== */}
        {op0 > 0.01 && (
          <g opacity={op0}>
            {/* Host TCP Retransmit Queue */}
            <rect x={120} y={405} width={560} height={240} rx={20} fill="#121a30" stroke={CK.sdp} strokeWidth={2.5} />
            <text x={150} y={450} fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host TCP Retransmit Queue
            </text>
            <text x={150} y={482} fontSize={20} fill={C.muted} fontFamily={FONT}>
              Sent buffers pinned in host RAM until ACK
            </text>
            {[0, 1, 2, 3, 4, 5].map((i) => {
              const bx = 150 + i * 84;
              const active = i === Math.floor(rt * 2) % 6;
              return (
                <g key={i}>
                  <rect
                    x={bx}
                    y={512}
                    width={72}
                    height={68}
                    rx={10}
                    fill={active ? `${CK.sdp}40` : "#1c2440"}
                    stroke={active ? CK.sdp : "#4c5a82"}
                    strokeWidth={active ? 2.5 : 1.5}
                  />
                  <text x={bx + 36} y={548} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={700} fill={C.text} fontFamily={MONO}>
                    buf {i + 1}
                  </text>
                </g>
              );
            })}
            <text x={150} y={616} fontSize={19} fontWeight={700} fill={CK.sdp} fontFamily={MONO}>
              LOCKED · CANNOT OVERWRITE BEFORE REMOTE ACK
            </text>

            {/* Forward path: Host -> NIC -> Remote Peer */}
            <line x1={680} y1={525} x2={930} y2={525} stroke={CK.push} strokeWidth={4} opacity={0.45} />
            <PacketStream pts={[[680, 525], [930, 525]]} travel={rt * 150} gap={64} size={13} color={CK.push} />
            <text x={805} y={498} textAnchor="middle" fontSize={19} fontWeight={700} fill={CK.push} fontFamily={MONO}>
              PCIe Direct Push
            </text>

            {/* NIC box */}
            <rect x={935} y={445} width={235} height={160} rx={18} fill="#141b2e" stroke={CK.card} strokeWidth={2.5} />
            <text x={1052} y={512} textAnchor="middle" fontSize={28} fontWeight={800} fill={CK.card} fontFamily={FONT}>
              NIC / DPU
            </text>
            <text x={1052} y={548} textAnchor="middle" fontSize={20} fill={C.muted} fontFamily={FONT}>
              100GbE Wire TX
            </text>

            {/* Wire to Remote Peer */}
            <line x1={1170} y1={525} x2={1435} y2={525} stroke={CK.peer} strokeWidth={4} opacity={0.45} />
            <PacketStream pts={[[1170, 525], [1435, 525]]} travel={rt * 130} gap={72} size={13} color={CK.peer} />
            <text x={1302} y={498} textAnchor="middle" fontSize={19} fontWeight={700} fill={CK.peer} fontFamily={MONO}>
              100GbE Network
            </text>

            {/* Remote Peer */}
            <rect x={1440} y={445} width={350} height={160} rx={18} fill="#121a30" stroke={CK.peer} strokeWidth={2.5} />
            <text x={1615} y={512} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Remote Peer
            </text>
            <text x={1615} y={548} textAnchor="middle" fontSize={20} fill={C.muted} fontFamily={FONT}>
              Receives payload · Sends ACK
            </text>

            {/* Return ACK arc underneath */}
            <path
              d="M 1615 605 L 1615 710 L 400 710 L 400 648"
              fill="none"
              stroke={CK.cxl}
              strokeWidth={3.5}
              strokeDasharray="10 8"
              opacity={0.75}
            />
            <polygon points="400,645 391,664 409,664" fill={CK.cxl} />
            <Packet
              pts={[[1615, 605], [1615, 710], [400, 710], [400, 650]]}
              k={ackK}
              color={CK.cxl}
              r={10}
              trail={false}
            />
            <rect x={830} y={688} width={360} height={42} rx={10} fill="#0b1020" stroke={CK.cxl} strokeWidth={1.5} />
            <text x={1010} y={710} textAnchor="middle" dominantBaseline="central" fontSize={21} fontWeight={700} fill={CK.cxl} fontFamily={MONO}>
              Remote ACK (long RTT)
            </text>
          </g>
        )}

        {/* ==================== CUE 1: Two Separate Lifetimes ==================== */}
        {op1 > 0.01 && (
          <g opacity={op1}>
            {/* Left Card: Lifetime 1 */}
            <rect x={100} y={230} width={745} height={540} rx={22} fill="#11182c" stroke={CK.push} strokeWidth={2.5} />
            <rect x={100} y={230} width={745} height={68} rx={22} fill={`${CK.push}1e`} />
            <text x={135} y={272} fontSize={24} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              LIFETIME 1 · HOST → NIC (PCIe)
            </text>

            {/* Mini flow inside Left Card */}
            <rect x={145} y={345} width={175} height={110} rx={14} fill="#19233e" stroke={CK.host} strokeWidth={2} />
            <text x={232} y={392} textAnchor="middle" fontSize={23} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Host Core
            </text>
            <text x={232} y={424} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              64 B push
            </text>

            <line x1={320} y1={400} x2={560} y2={400} stroke={CK.push} strokeWidth={5} opacity={0.45} />
            <PacketStream pts={[[320, 400], [560, 400]]} travel={rt * 170} gap={54} size={13} color={CK.push} />
            <text x={440} y={372} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              &lt; 1 µs · Lossless
            </text>

            <rect x={565} y={345} width={235} height={110} rx={14} fill="#16282e" stroke={CK.ok} strokeWidth={2.2} />
            <text x={682} y={392} textAnchor="middle" fontSize={23} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
              NIC Memory
            </text>
            <text x={682} y={424} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={MONO}>
              Owns its copy
            </text>

            {/* Bullet points Left */}
            {[
              "Lossless PCIe hop — hardware guarantees delivery",
              "Payload handed off in < 1 µs",
              "NIC owns its private copy immediately",
            ].map((line, i) => (
              <g key={i}>
                <circle cx={155} cy={538 + i * 68} r={7} fill={CK.push} />
                <text x={180} y={540 + i * 68} dominantBaseline="central" fontSize={25} fontWeight={600} fill={C.text} fontFamily={FONT}>
                  {line}
                </text>
              </g>
            ))}

            {/* Center Vertical Divider */}
            <line x1={960} y1={230} x2={960} y2={770} stroke={CK.cxl} strokeWidth={3} strokeDasharray="10 8" opacity={0.85} />
            <rect x={852} y={468} width={216} height={54} rx={14} fill="#0b1020" stroke={CK.cxl} strokeWidth={2.5} />
            <text x={960} y={496} textAnchor="middle" dominantBaseline="central" fontSize={19} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              SEPARATE LIFETIMES
            </text>

            {/* Right Card: Lifetime 2 */}
            <rect x={1075} y={230} width={745} height={540} rx={22} fill="#11182c" stroke={CK.host} strokeWidth={2.5} />
            <rect x={1075} y={230} width={745} height={68} rx={22} fill={`${CK.host}24`} />
            <text x={1110} y={272} fontSize={24} fontWeight={800} fill={CK.host} fontFamily={MONO}>
              LIFETIME 2 · END-TO-END TCP
            </text>

            {/* Mini visual inside Right Card */}
            <rect x={1120} y={345} width={290} height={110} rx={14} fill="#1a213b" stroke={CK.sdp} strokeWidth={2.2} />
            <text x={1265} y={392} textAnchor="middle" fontSize={23} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Host Retransmit Pool
            </text>
            <text x={1265} y={424} textAnchor="middle" fontSize={18} fill={CK.sdp} fontFamily={MONO}>
              Private to Host RAM
            </text>

            <line x1={1410} y1={400} x2={1565} y2={400} stroke={CK.cxl} strokeWidth={3} strokeDasharray="8 6" />
            <rect x={1570} y={345} width={205} height={110} rx={14} fill="#19233e" stroke={CK.peer} strokeWidth={2} />
            <text x={1672} y={392} textAnchor="middle" fontSize={22} fontWeight={700} fill={CK.peer} fontFamily={FONT}>
              Remote ACK
            </text>
            <text x={1672} y={424} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              Frees slot later
            </text>

            {/* Bullet points Right */}
            {[
              "Host-private TCP retransmit pool",
              "Waits strictly for remote end-to-end ACK",
              "NIC never needs or reads these buffers",
            ].map((line, i) => (
              <g key={i}>
                <circle cx={1130} cy={538 + i * 68} r={7} fill={CK.host} />
                <text x={1155} y={540 + i * 68} dominantBaseline="central" fontSize={25} fontWeight={600} fill={C.text} fontFamily={FONT}>
                  {line}
                </text>
              </g>
            ))}
          </g>
        )}

        {/* ==================== CUE 2: Copy-by-Value Direct Push ==================== */}
        {op2 > 0.01 && (
          <g opacity={op2}>
            {/* Host Container */}
            <rect x={90} y={200} width={470} height={570} rx={22} fill="#11182c" stroke={CK.host} strokeWidth={2.5} />
            <text x={124} y={248} fontSize={26} fontWeight={800} fill={CK.host} fontFamily={FONT}>
              HOST (saturn)
            </text>

            {/* Host Core + Single 64 B L1 Slot */}
            <rect x={124} y={280} width={402} height={185} rx={16} fill="#17223d" stroke={CK.push} strokeWidth={2.2} />
            <text x={152} y={322} fontSize={23} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Host Core · L1 Cache
            </text>
            {/* Glowing single 64 B slot */}
            <rect
              x={152}
              y={348}
              width={346}
              height={88}
              rx={12}
              fill={`rgba(56, 189, 248, ${0.15 + 0.18 * pulse})`}
              stroke={CK.push}
              strokeWidth={3}
            />
            <text x={325} y={384} textAnchor="middle" fontSize={28} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              ONE 64 B Slot
            </text>
            <text x={325} y={418} textAnchor="middle" fontSize={19} fill={C.text} fontFamily={FONT}>
              Overwritten &amp; reused continuously
            </text>

            {/* Host TX RAM (Private) */}
            <rect x={124} y={540} width={402} height={190} rx={16} fill="#151b30" stroke="#475569" strokeWidth={2} strokeDasharray="8 6" />
            <text x={152} y={588} fontSize={23} fontWeight={700} fill={C.muted} fontFamily={FONT}>
              Host TX RAM (TCP Pool)
            </text>
            <text x={152} y={626} fontSize={20} fill={C.muted} fontFamily={FONT}>
              Retained for remote ACKs only
            </text>
            <rect x={152} y={652} width={220} height={44} rx={10} fill="#1e293b" stroke="#475569" strokeWidth={1.5} />
            <text x={262} y={675} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={700} fill={C.muted} fontFamily={MONO}>
              HOST-PRIVATE
            </text>

            {/* Forward Push Stream: Host L1 -> NIC Memory */}
            <line x1={526} y1={392} x2={870} y2={392} stroke={CK.push} strokeWidth={12} opacity={0.18} />
            <line x1={526} y1={392} x2={870} y2={392} stroke={CK.push} strokeWidth={4.5} opacity={0.75} />
            <PacketStream pts={[[526, 392], [870, 392]]} travel={rt * 210} gap={52} size={14} color={CK.push} />
            <text x={698} y={352} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              Copy 64 B by Value
            </text>
            <text x={698} y={432} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={MONO}>
              PCIe Gen5 ×4
            </text>

            {/* Crossed-out return arrow: NIC -> Host TX RAM */}
            <line x1={870} y1={635} x2={540} y2={635} stroke="#475569" strokeWidth={3.5} strokeDasharray="10 8" />
            <polygon points="532,635 552,625 552,645" fill="#475569" />
            {/* Bold Red X */}
            <circle cx={698} cy={635} r={34} fill="#2a1218" stroke={CK.trap} strokeWidth={3.5} />
            <line x1={681} y1={618} x2={715} y2={652} stroke={CK.trap} strokeWidth={5} strokeLinecap="round" />
            <line x1={715} y1={618} x2={681} y2={652} stroke={CK.trap} strokeWidth={5} strokeLinecap="round" />
            <text x={698} y={694} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
              NIC NEVER reads
            </text>
            <text x={698} y={718} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
              host TX RAM
            </text>

            {/* NIC Container */}
            <rect x={875} y={200} width={375} height={570} rx={22} fill="#11182c" stroke={CK.card} strokeWidth={2.5} />
            <text x={910} y={248} fontSize={26} fontWeight={800} fill={CK.card} fontFamily={FONT}>
              NIC / DPU MEMORY
            </text>
            <rect x={910} y={280} width={305} height={240} rx={16} fill="#182338" stroke={CK.ok} strokeWidth={2.2} />
            <text x={1062} y={328} textAnchor="middle" fontSize={23} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
              NIC Inbound Ring
            </text>
            {[0, 1, 2].map((i) => (
              <rect
                key={i}
                x={942}
                y={355 + i * 48}
                width={240}
                height={36}
                rx={8}
                fill={`${CK.push}28`}
                stroke={CK.push}
                strokeWidth={1.8}
              />
            ))}
            <text x={1062} y={374} textAnchor="middle" dominantBaseline="central" fontSize={17} fontWeight={700} fill={C.text} fontFamily={MONO}>
              64 B copy (owned)
            </text>
            <text x={1062} y={422} textAnchor="middle" dominantBaseline="central" fontSize={17} fontWeight={700} fill={C.text} fontFamily={MONO}>
              64 B copy (owned)
            </text>
            <text x={1062} y={470} textAnchor="middle" dominantBaseline="central" fontSize={17} fontWeight={700} fill={C.text} fontFamily={MONO}>
              64 B copy (owned)
            </text>
            <text x={1062} y={592} textAnchor="middle" fontSize={21} fill={C.muted} fontFamily={FONT}>
              Zero DMA pull back
            </text>
            <text x={1062} y={626} textAnchor="middle" fontSize={21} fill={C.muted} fontFamily={FONT}>
              from host memory
            </text>
          </g>
        )}

        {/* ==================== CUE 3: CC-NIC vs Plain PCIe Direct Push ==================== */}
        {op3 > 0.01 && (
          <g opacity={op3}>
            {/* Left Panel: Coherent NIC (CC-NIC) */}
            <rect x={80} y={170} width={845} height={650} rx={22} fill="#141322" stroke={CK.trap} strokeWidth={2.5} />
            <rect x={80} y={170} width={845} height={74} rx={22} fill={`${CK.trap}20`} />
            <text x={115} y={216} fontSize={28} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
              Coherent NIC (CC-NIC)
            </text>
            <text x={890} y={216} textAnchor="end" fontSize={20} fontWeight={700} fill={CK.cxl} fontFamily={MONO}>
              SHARED CACHE LINES
            </text>

            {/* Host TX Pool (Left) */}
            <rect x={125} y={310} width={230} height={175} rx={16} fill="#1d1c30" stroke={CK.cxl} strokeWidth={2.2} />
            <text x={240} y={376} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Host TX Pool
            </text>
            <text x={240} y={412} textAnchor="middle" fontSize={19} fill={CK.cxl} fontFamily={MONO}>
              Recycles buffers
            </text>
            <text x={240} y={444} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Writes invalidate NIC
            </text>

            {/* NIC Cache (Left) */}
            <rect x={645} y={310} width={235} height={175} rx={16} fill="#251724" stroke={CK.trap} strokeWidth={2.2} />
            <text x={762} y={376} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.text} fontFamily={FONT}>
              NIC Cache
            </text>
            <text x={762} y={412} textAnchor="middle" fontSize={19} fill={CK.trap} fontFamily={MONO}>
              Coherent copies
            </text>
            <text x={762} y={444} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Must snoop &amp; flush
            </text>

            {/* Invalidation storm arrows */}
            {[352, 398, 444].map((yy, idx) => {
              const dir = idx % 2 === 0 ? 1 : -1;
              const px = dir === 1 ? lerp(375, 625, (invPhase + idx * 0.33) % 1) : lerp(625, 375, (invPhase + idx * 0.33) % 1);
              return (
                <g key={idx}>
                  <line x1={368} y1={yy} x2={632} y2={yy} stroke={CK.trap} strokeWidth={3} strokeDasharray="8 6" opacity={0.75} />
                  <circle cx={px} cy={yy} r={8} fill={CK.trap} />
                </g>
              );
            })}
            <rect x={415} y={266} width={175} height={36} rx={8} fill="#2d131a" stroke={CK.trap} strokeWidth={1.8} />
            <text x={502} y={285} textAnchor="middle" dominantBaseline="central" fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              Invalidate!
            </text>

            {/* Impact stats inside Left Panel */}
            <rect x={125} y={555} width={360} height={210} rx={16} fill="#1d1c2e" stroke={CK.cxl} strokeWidth={2.2} />
            <text x={305} y={640} textAnchor="middle" fontSize={62} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              −20%
            </text>
            <text x={305} y={696} textAnchor="middle" fontSize={23} fontWeight={700} fill={C.text} fontFamily={FONT}>
              TCP Retention Alone
            </text>
            <text x={305} y={730} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Cross-link snoop overhead
            </text>

            <rect x={520} y={555} width={360} height={210} rx={16} fill="#241522" stroke={CK.trap} strokeWidth={2.2} />
            <text x={700} y={640} textAnchor="middle" fontSize={62} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              −73%
            </text>
            <text x={700} y={696} textAnchor="middle" fontSize={23} fontWeight={700} fill={C.text} fontFamily={FONT}>
              With Host TX Pools
            </text>
            <text x={700} y={730} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Invalidation storm collapse
            </text>

            {/* Right Panel: Plain PCIe Direct Push */}
            <rect x={995} y={170} width={845} height={650} rx={22} fill="#0f1b24" stroke={CK.ok} strokeWidth={2.5} />
            <rect x={995} y={170} width={845} height={74} rx={22} fill={`${CK.ok}1e`} />
            <text x={1030} y={216} fontSize={28} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
              Plain PCIe Direct Push
            </text>
            <text x={1805} y={216} textAnchor="end" fontSize={20} fontWeight={700} fill={CK.push} fontFamily={MONO}>
              DECOUPLED BY DESIGN
            </text>

            {/* Host TX Pool (Right) */}
            <rect x={1040} y={310} width={235} height={175} rx={16} fill="#152236" stroke={CK.host} strokeWidth={2.2} />
            <text x={1157} y={376} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Host TX Pool
            </text>
            <text x={1157} y={412} textAnchor="middle" fontSize={19} fill={CK.host} fontFamily={MONO}>
              Fully host-local
            </text>
            <text x={1157} y={444} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Never snooped
            </text>

            {/* Decoupling Wall */}
            <line x1={1417} y1={282} x2={1417} y2={505} stroke={CK.ok} strokeWidth={10} opacity={0.18} />
            <line x1={1417} y1={282} x2={1417} y2={505} stroke={CK.ok} strokeWidth={4} />
            <rect x={1315} y={372} width={204} height={46} rx={10} fill="#0b1020" stroke={CK.ok} strokeWidth={2} />
            <text x={1417} y={396} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              NO COHERENCE
            </text>

            {/* NIC Cache (Right) */}
            <rect x={1560} y={310} width={235} height={175} rx={16} fill="#12272c" stroke={CK.ok} strokeWidth={2.2} />
            <text x={1677} y={376} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.text} fontFamily={FONT}>
              NIC Cache
            </text>
            <text x={1677} y={412} textAnchor="middle" fontSize={19} fill={CK.ok} fontFamily={MONO}>
              Private copies only
            </text>
            <text x={1677} y={444} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              Zero invalidations
            </text>

            {/* Verdict Card inside Right Panel */}
            <rect x={1040} y={555} width={755} height={210} rx={16} fill="#112627" stroke={CK.ok} strokeWidth={2.4} />
            <text x={1417} y={634} textAnchor="middle" fontSize={58} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              0% Coherence Tax
            </text>
            <text x={1417} y={696} textAnchor="middle" fontSize={25} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Zero shared cache lines · Zero invalidations
            </text>
            <text x={1417} y={732} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={FONT}>
              Host TCP buffers can grow freely without touching the PCIe link
            </text>
          </g>
        )}

        {/* ==================== CUE 4 & 5: Scaling Chart (Morphs Left in Cue 5) ==================== */}
        {op45 > 0.01 && (
          <g opacity={op45}>
            {/* Chart Title */}
            <text x={cx0} y={cy1 - 56} fontSize={28} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Closed-Loop Throughput vs. Host TX Retention Pool Size
            </text>

            {/* CC-NIC -20% reference band */}
            <rect
              x={cx0}
              y={sy(47.5)}
              width={Math.max(0, cx1 - cx0)}
              height={Math.max(0, sy(38.0) - sy(47.5))}
              fill={CK.cxl}
              opacity={0.09}
            />
            <HLine
              y={sy(38.0)}
              x0={cx0}
              x1={cx1}
              color={CK.cxl}
              size={20}
              opacity={0.9}
            />
            <text x={cx0 + 20} y={sy(38.0) + 28} fontSize={20} fontWeight={700} fill={CK.cxl} fontFamily={FONT}>
              CC-NIC retention baseline (−20%)
            </text>

            {/* Vertical dashed marker: 60 MiB Host LLC */}
            <line x1={llcX} y1={cy1} x2={llcX} y2={cy0} stroke={CK.sdp} strokeWidth={2.5} strokeDasharray="9 7" opacity={0.9} />
            <rect x={llcX - 208} y={cy1 + 8} width={196} height={34} rx={8} fill="#14182e" stroke={CK.sdp} strokeWidth={1.6} />
            <text x={llcX - 110} y={cy1 + 26} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.sdp} fontFamily={MONO}>
              60 MiB Host LLC
            </text>

            {/* Axes */}
            <Axes
              x0={cx0}
              x1={cx1}
              y0={cy0}
              y1={cy1}
              sx={sx}
              sy={sy}
              xticks={[
                [64, "64 KiB"],
                [4096, "4 MiB"],
                [32768, "32 MiB"],
                [131072, "128 MiB"],
              ]}
              yticks={[
                [0, "0"],
                [20, "20"],
                [40, "40"],
                [60, "60 M/s"],
              ]}
              xlabel="Host TX Pool Size (log scale)"
              ylabel="Throughput (M/s)"
              size={22}
            />

            {/* Flat Green Direct Push Series */}
            <Series pts={POOL_PTS} sx={sx} sy={sy} k={kCurve} color={CK.ok} w={6} r={8} />

            {/* Endpoint labels */}
            {kCurve > 0.15 && (
              <text x={sx(64) + 14} y={sy(47.5) - 18} fontSize={22} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                47.5 M/s
              </text>
            )}
            {kCurve > 0.92 && (
              <text x={sx(131072) - 6} y={sy(47.1) - 18} textAnchor="end" fontSize={22} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                47.1 M/s
              </text>
            )}

            {/* Direct Push Legend pill inside chart (lower-left open space) */}
            <rect x={cx0 + 24} y={cy0 - 72} width={288} height={42} rx={10} fill="#102526" stroke={CK.ok} strokeWidth={1.8} />
            <circle cx={cx0 + 48} cy={cy0 - 51} r={7} fill={CK.ok} />
            <text x={cx0 + 66} y={cy0 - 50} dominantBaseline="central" fontSize={20} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
              Direct Push (flat −0.9%)
            </text>
          </g>
        )}
      </Stage>

      {/* ==================== HTML OVERLAYS (OUTSIDE <Stage>) ==================== */}

      {/* Cue 0 Question Banner */}
      {op0 > 0.01 && (
        <QuestionBanner
          num="QUESTION 2"
          question="What happens when TCP must retain sent buffers in host memory?"
          sub="Real transport protocols cannot overwrite payloads until remote ACKs arrive."
          color={CK.sdp}
          y={165}
          opacity={op0}
          dy={lerp(20, 0, prog(t, T[0], 0.5))}
        />
      )}

      {/* Cue 1 Section Title */}
      {op1 > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 100,
            top: 145,
            width: 1720,
            opacity: op1,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ fontSize: 34, fontWeight: 800, color: C.text, fontFamily: FONT }}>
            Core Insight: Separate Two Independent Lifetimes
          </div>
          <Pill x={1355} y={2} color={CK.cxl} size={20}>
            PCIe Hop ≠ End-to-End TCP
          </Pill>
        </div>
      )}

      {/* Cue 2 Right-Side HeroStat + Summary */}
      {op2 > 0.01 && (
        <>
          <HeroStat
            x={1310}
            y={255}
            w={520}
            h={220}
            value={`${pushRate} M/s`}
            label="Single 64 B Buffer Reused"
            sub="Zero TX buffer pool · Copy by value"
            color={CK.push}
            opacity={op2}
            dy={lerp(22, 0, prog(t, T[2], 0.5))}
          />
          <div
            style={{
              position: "absolute",
              left: 1310,
              top: 515,
              width: 520,
              opacity: op2,
              background: "rgba(20,27,46,0.92)",
              border: `1.5px solid ${CK.ok}77`,
              borderLeft: `6px solid ${CK.ok}`,
              borderRadius: 16,
              padding: "20px 24px",
              boxSizing: "border-box",
            }}
          >
            <div style={{ fontSize: 23, fontWeight: 700, color: CK.ok, fontFamily: FONT }}>
              Zero Readback Dependency
            </div>
            <div style={{ fontSize: 21, color: C.muted, marginTop: 8, lineHeight: 1.4, fontFamily: FONT }}>
              Host overwrites one 64 B line in L1 cache while the NIC owns its pushed copy immediately.
            </div>
          </div>
        </>
      )}

      {/* Cue 4 Right Callout (fades out before Cue 5 HeroStats appear) */}
      {op4Only > 0.01 && (
        <HeroStat
          x={1515}
          y={285}
          w={335}
          h={225}
          value="−0.9%"
          label="Across 2,000× Pool Growth"
          sub="64 KiB → 128 MiB"
          color={CK.ok}
          opacity={op4Only}
          dy={lerp(18, 0, prog(t, T[4] + 0.6, 0.5))}
        />
      )}

      {/* Cue 5 Right HeroStats + Bottom BoundaryBar */}
      {op5 > 0.01 && (
        <>
          <HeroStat
            x={1245}
            y={180}
            w={595}
            h={218}
            value="99.99993%"
            label="Receive DDIO Hit Rate"
            sub="816 M replies · 0 misses @ 128 MiB"
            color={CK.ok}
            opacity={op5}
            dy={lerp(20, 0, prog(t, T[5] + 0.1, 0.5))}
          />
          <HeroStat
            x={1245}
            y={425}
            w={595}
            h={212}
            value="0%"
            label="Cross-Link Coherence Tax"
            sub="NIC cache completely decoupled"
            color={CK.push}
            opacity={op5}
            dy={lerp(24, 0, prog(t, T[5] + 0.25, 0.5))}
          />
          <BoundaryBar
            x={80}
            y={760}
            w={1760}
            badge="LOCALITY NOTE"
            text="Under worst-case random TX access past 60 MiB LLC, slowdown comes purely from host-local DDR5 — NIC receive DDIO still stays 100%."
            color={CK.cxl}
            opacity={op5}
            dy={lerp(16, 0, prog(t, T[5] + 0.35, 0.5))}
          />
        </>
      )}
    </>
  );
}

export const C03: SceneDef = {
  id: "C03",
  title: "2 \u00b7 Zero Coherence Tax on TX",
  stage: 1,
  source: "Host TX Retention Pool: 64 KiB \u2192 128 MiB vs 60 MiB Host LLC",
  dur: DUR,
  cues,
  C: Body,
};
