import React from "react";
import {
  C,
  Cue,
  FONT,
  MONO,
  Packet,
  PacketStream,
  Pt,
  SceneDef,
  Stage,
  clamp01,
  countUp,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import { CK, HBarChart, HeroStat, QuestionBanner, VerdictRow } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 10.98, "**Question 1**: Wait—isn't ordinary **PCIe MMIO** famously slow and forced through **DRAM** without cache coherence?", "Question one: wait—isn't ordinary P C I express M M I O famously slow, and without hardware cache coherence, doesn't every message have to crawl through slow D RAM?"], // 0
  [11.33, 22.71, "Only if you fence every write. Fencing every 64 B write stalls at **8.10 M/s**—reproducing CC-NIC's slow baseline.", "Only if you stall after every write. Issuing a memory fence after every sixty-four byte write stalls one core at eight point one zero million per second—reproducing C C NIC's slow baseline."], // 1
  [23.06, 35.36, "Relax the fence interval and host write queues pipeline in parallel: **1 core** hits **175.2 M/s** (**21.6×**)—**97.9%** of Gen5 ×4 line rate!", "Relax the fence interval and host queues pipeline writes in parallel. One host core jumps twenty-one point six times to one hundred seventy-five point two million per second—ninety-seven point nine percent of line rate."], // 2
  [35.71, 48.37, "What about latency? Cold-flushing each hop proves **all three cache shortcuts** already work on commercial PCIe—staying in caches.", "That settles raw throughput—now what about round-trip latency? Cold-flushing each hop one at a time proves all three cache shortcuts already work by default on commercial P C I express, keeping the fast path in caches."], // 3
  [48.72, 66.10, "Host **L1** saves **117 ns**; writes hit **card L3** (**99.94%**, **−75 ns**); replies hit **host LLC via DDIO** (**−87 ns**) → **897 ns** RTT.", "Host L one saves one hundred seventeen nanoseconds. Writes hit card L three at ninety-nine point nine four percent, saving seventy-five nanoseconds. Replies hit host L L C via Intel D D I O, saving eighty-seven—giving an eight hundred ninety-seven nanosecond round trip."], // 4
  [66.45, 81.25, "Even with **full IOMMU**, **897 ns** beats stock **SDP** (**2,400 ns**) and real **CXL-NIC** hardware (**1.3–2.1 µs**, no IOMMU).", "Even with full hardware I O M M U turned on, eight hundred ninety-seven nanoseconds beats the card's default queue interface nearly three times over, and beats real C X L network cards at one point three to two point one microseconds without I O M M U."], // 5
];
const DUR = 82.65; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// Round-trip cache-only paths for Act 2 (Cue 3 & Cue 4)
const CACHE_FWD: Pt[] = [
  [380, 292],
  [750, 292],
  [970, 292],
  [1045, 292],
  [1325, 292],
  [1370, 292],
];
const CACHE_RET: Pt[] = [
  [1370, 342],
  [1325, 342],
  [1045, 342],
  [970, 342],
  [750, 342],
  [685, 342],
];

const SHORTCUTS = [
  {
    hop: "HOP 1 · HOST L1D",
    saving: "−117 ns",
    detail: "Source buffer already hot in core",
    color: CK.push,
  },
  {
    hop: "HOP 2 · CARD L3",
    saving: "−75 ns",
    detail: "99.94% hit on direct PCIe write",
    color: CK.card,
  },
  {
    hop: "HOP 3 · HOST DDIO",
    saving: "−87 ns",
    detail: "Reply lands directly in host LLC",
    color: CK.ok,
  },
];

function Body() {
  const t = useT();
  const realT = useRealT();

  // Beat opacities (clean non-overlapping crossfades)
  const opQ = fade(t, T[0], T[1] - 0.1, 0.5);
  const opAct1 = fade(t, T[1] + 0.15, T[3] - 0.1, 0.5);
  const opAct1Cue1Text = fade(t, T[1] + 0.15, T[2] - 0.15, 0.4);
  const opAct1Cue2Text = fade(t, T[2] + 0.15, T[3] - 0.1, 0.45);
  const kUnfence = prog(t, T[2], 0.65);

  const opAct2 = fade(t, T[3] + 0.15, T[5] - 0.1, 0.5);
  const opAct2Cue3Sub = fade(t, T[3] + 0.15, T[4] - 0.15, 0.4);
  const opAct2Cue4Sub = fade(t, T[4] + 0.15, T[5] - 0.1, 0.45);

  const opCmp = fade(t, T[5] + 0.15, 1e9, 0.5);

  // Cue 1 stalled single packet motion (cycles cleanly, hits fence at x=440)
  const stallCycle = (realT * 0.75) % 1;
  const stallX = stallCycle < 0.45 ? lerp(300, 422, stallCycle / 0.45) : 422;

  // Cue 2 count-ups (finish within 1.2s)
  const speedupStr = countUp(t, T[2] + 0.25, 1.15, 21.6, 1);
  const rateStr = countUp(t, T[2] + 0.35, 1.15, 175.2, 1);

  // Cue 3/4 continuous round-trip packet phase
  const rtCycle = (realT * 0.55) % 1;
  const isFwd = rtCycle < 0.5;
  const rtK = isFwd ? rtCycle * 2 : (rtCycle - 0.5) * 2;

  // Cue 4 RTT count-up
  const rttStr = countUp(t, T[4] + 0.35, 1.1, 897, 0);

  // Cue 5 bar animations
  const kBarDirect = prog(t, T[5] + 0.25, 0.95);
  const kBarCxl = prog(t, T[5] + 0.45, 0.95);
  const kBarSdp = prog(t, T[5] + 0.65, 0.95);

  // CXL range bracket coordinates for HBarChart at x=90, w=1720, labelW=460, max=2600
  const cmpTrackX = 90 + 460;
  const cmpTrackW = 1720 - 460 - 150;
  const cxlX1 = cmpTrackX + (1300 / 2600) * cmpTrackW;
  const cxlX2 = cmpTrackX + (2100 / 2600) * cmpTrackW;
  const cxlRowMidY = 325 + (68 + 48) + 34;

  return (
    <>
      <Stage>
        {/* ==================== ACT 1: PIPELINE & THROUGHPUT (CUES 1 & 2) ==================== */}
        {opAct1 > 0.01 && (
          <g opacity={opAct1}>
            {/* Left panel frame */}
            <rect
              x={75}
              y={228}
              width={775}
              height={550}
              rx={22}
              fill="#11182c"
              stroke={kUnfence > 0.5 ? CK.ok : CK.trap}
              strokeWidth={2}
              opacity={0.94}
            />

            {/* Host Core box */}
            <rect x={112} y={348} width={178} height={144} rx={16} fill="#18233d" stroke={CK.host} strokeWidth={2.5} />
            <text x={201} y={405} textAnchor="middle" fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host Core
            </text>
            <text x={201} y={442} textAnchor="middle" fontSize={19} fontWeight={700} fill={CK.push} fontFamily={MONO}>
              1 core · 64 B
            </text>

            {/* Pipeline track */}
            <rect x={290} y={392} width={345} height={56} rx={12} fill="#0b1020" stroke={C.line} strokeWidth={1.8} />
            <text x={462} y={534} textAnchor="middle" fontSize={18} fontWeight={700} fill={C.muted} fontFamily={MONO}>
              PCIe Gen5 ×4 Write Queue
            </text>

            {/* CN106 NIC box */}
            <rect x={635} y={348} width={180} height={144} rx={16} fill="#18233d" stroke={CK.card} strokeWidth={2.5} />
            <text x={725} y={405} textAnchor="middle" fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
              CN106 NIC
            </text>
            <text x={725} y={442} textAnchor="middle" fontSize={19} fontWeight={700} fill={CK.card} fontFamily={MONO}>
              Card L3
            </text>

            {/* Cue 1: Red FENCE wall + stalled write */}
            {1 - kUnfence > 0.01 && (
              <g opacity={1 - kUnfence} transform={`translate(0, ${-kUnfence * 70})`}>
                {/* Wide halo underneath (never filter=glow on vertical line) */}
                <line x1={448} y1={336} x2={448} y2={504} stroke={CK.trap} strokeWidth={20} opacity={0.18} strokeLinecap="round" />
                <line x1={448} y1={336} x2={448} y2={504} stroke={CK.trap} strokeWidth={6} strokeDasharray="12 6" strokeLinecap="round" />
                <rect x={392} y={296} width={112} height={34} rx={8} fill={CK.trap} />
                <text x={448} y={313} textAnchor="middle" dominantBaseline="central" fontSize={19} fontWeight={800} fill={C.bg} fontFamily={MONO}>
                  FENCE
                </text>

                {/* Single stalled 64 B write */}
                <rect x={stallX - 24} y={404} width={48} height={32} rx={7} fill={CK.trap} />
                <text x={stallX} y={420} textAnchor="middle" dominantBaseline="central" fontSize={15} fontWeight={800} fill={C.bg} fontFamily={MONO}>
                  64 B
                </text>
              </g>
            )}

            {/* Cue 2: Continuous pipelined cyan 64 B stream */}
            {kUnfence > 0.01 && (
              <g opacity={kUnfence}>
                <PacketStream
                  pts={[
                    [298, 420],
                    [626, 420],
                  ]}
                  travel={realT * 240}
                  gap={44}
                  size={15}
                  color={CK.push}
                  opacity={0.95}
                />
              </g>
            )}

            {/* Right panel: Animated Horizontal Bar Chart */}
            <HBarChart
              x={895}
              y={290}
              w={945}
              labelW={320}
              barH={56}
              gap={34}
              max={190}
              refLine={{ value: 179.0, label: "Gen5 ×4 line rate (179.0 M/s)", color: CK.cxl }}
              rows={[
                {
                  label: "Fence Every Write",
                  sub: "K = 1 · CC-NIC baseline",
                  value: 8.1,
                  valueText: "8.10 M/s",
                  color: CK.trap,
                  k: prog(t, T[1] + 0.35, 1.0),
                },
                {
                  label: "Relaxed Fence",
                  sub: "K = 16 writes / fence",
                  value: 101.2,
                  valueText: "101.2 M/s",
                  color: CK.push,
                  k: prog(t, T[2] + 0.25, 1.0),
                },
                {
                  label: "Unfenced Direct Push",
                  sub: "1 host core · 11.21 GB/s",
                  value: 175.2,
                  valueText: "175.2 M/s",
                  color: CK.ok,
                  k: prog(t, T[2] + 0.5, 1.05),
                },
              ]}
            />
          </g>
        )}

        {/* ==================== ACT 2: 3-HOP CACHE LATENCY DIAGRAM (CUES 3 & 4) ==================== */}
        {opAct2 > 0.01 && (
          <g opacity={opAct2}>
            {/* Host container */}
            <rect x={80} y={208} width={630} height={328} rx={20} fill="#11182c" stroke={CK.host} strokeWidth={2} />
            <text x={108} y={240} fontSize={20} fontWeight={800} fill={CK.host} fontFamily={MONO}>
              HOST · saturn
            </text>

            {/* Host Core + L1 */}
            <rect
              x={110}
              y={260}
              width={270}
              height={108}
              rx={14}
              fill="#17243f"
              stroke={CK.push}
              strokeWidth={opAct2Cue4Sub > 0.3 ? 3 : 2}
            />
            <text x={245} y={303} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host Core + L1
            </text>
            <text x={245} y={338} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.push} fontFamily={MONO}>
              64 B direct push
            </text>

            {/* Host LLC (DDIO) */}
            <rect
              x={415}
              y={260}
              width={270}
              height={108}
              rx={14}
              fill="#152838"
              stroke={CK.ok}
              strokeWidth={opAct2Cue4Sub > 0.3 ? 3 : 2}
            />
            <text x={550} y={303} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host LLC (DDIO)
            </text>
            <text x={550} y={338} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
              Cache-injected reply
            </text>

            {/* Host DRAM (dimmed + crossed out) */}
            <g opacity={0.48}>
              <rect x={110} y={406} width={575} height={98} rx={14} fill="#101524" stroke={C.line} strokeWidth={1.8} strokeDasharray="8 6" />
              <text x={245} y={456} textAnchor="middle" dominantBaseline="central" fontSize={23} fontWeight={700} fill={C.muted} fontFamily={FONT}>
                Host DRAM
              </text>
              <rect x={386} y={435} width={274} height={42} rx={10} fill={`${CK.ok}18`} stroke={CK.ok} strokeWidth={1.6} />
              <text x={523} y={456} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                Bypassed on fast path
              </text>
            </g>

            {/* PCIe Gen5 x4 bridge */}
            <rect x={750} y={260} width={220} height={152} rx={16} fill="#141e36" stroke={CK.push} strokeWidth={2} />
            <text x={860} y={288} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              PCIe Gen5 ×4
            </text>
            <text x={860} y={388} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
              HW IOMMU ON
            </text>

            {/* NIC container */}
            <rect x={1010} y={208} width={830} height={328} rx={20} fill="#11182c" stroke={CK.card} strokeWidth={2} />
            <text x={1038} y={240} fontSize={20} fontWeight={800} fill={CK.card} fontFamily={MONO}>
              NIC · Marvell CN106 DPU
            </text>

            {/* Card L3 */}
            <rect
              x={1045}
              y={260}
              width={280}
              height={108}
              rx={14}
              fill="#221c2c"
              stroke={CK.card}
              strokeWidth={opAct2Cue4Sub > 0.3 ? 3 : 2}
            />
            <text x={1185} y={303} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Card L3 Cache
            </text>
            <text x={1185} y={338} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.card} fontFamily={MONO}>
              99.94% hit rate
            </text>

            {/* NIC Core */}
            <rect x={1370} y={260} width={220} height={108} rx={14} fill="#1b2236" stroke={CK.card} strokeWidth={2} />
            <text x={1480} y={303} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              NIC Core
            </text>
            <text x={1480} y={338} textAnchor="middle" fontSize={18} fontWeight={700} fill={C.muted} fontFamily={MONO}>
              Immediate echo
            </text>

            {/* Card DRAM (dimmed + bypassed) */}
            <g opacity={0.48}>
              <rect x={1045} y={406} width={545} height={98} rx={14} fill="#101524" stroke={C.line} strokeWidth={1.8} strokeDasharray="8 6" />
              <text x={1175} y={456} textAnchor="middle" dominantBaseline="central" fontSize={23} fontWeight={700} fill={C.muted} fontFamily={FONT}>
                Card DRAM
              </text>
              <rect x={1302} y={435} width={264} height={42} rx={10} fill={`${CK.card}18`} stroke={CK.card} strokeWidth={1.6} />
              <text x={1434} y={456} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                Bypassed (99.94% L3)
              </text>
            </g>

            {/* Right RTT Summary Badge inside NIC panel */}
            <rect
              x={1622}
              y={260}
              width={190}
              height={244}
              rx={16}
              fill={opAct2Cue4Sub > 0.2 ? "#102824" : "#151d33"}
              stroke={opAct2Cue4Sub > 0.2 ? CK.ok : CK.push}
              strokeWidth={2.5}
            />
            <text
              x={1717}
              y={320}
              textAnchor="middle"
              fontSize={18}
              fontWeight={800}
              fill={opAct2Cue4Sub > 0.2 ? CK.ok : CK.push}
              fontFamily={MONO}
            >
              64 B RTT
            </text>
            {opAct2Cue4Sub > 0.15 ? (
              <g opacity={opAct2Cue4Sub}>
                <text x={1717} y={392} textAnchor="middle" fontSize={44} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  {rttStr}
                </text>
                <text x={1717} y={430} textAnchor="middle" fontSize={22} fontWeight={700} fill={C.text} fontFamily={MONO}>
                  ns total
                </text>
              </g>
            ) : (
              <g opacity={opAct2Cue3Sub}>
                <text x={1717} y={382} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
                  Cache-Only
                </text>
                <text x={1717} y={416} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.push} fontFamily={FONT}>
                  Round Trip
                </text>
              </g>
            )}

            {/* Cache-only round-trip tracks */}
            <line x1={380} y1={292} x2={750} y2={292} stroke={CK.push} strokeWidth={3.5} opacity={0.45} />
            <line x1={970} y1={292} x2={1045} y2={292} stroke={CK.push} strokeWidth={3.5} opacity={0.45} />
            <line x1={1325} y1={292} x2={1370} y2={292} stroke={CK.push} strokeWidth={3.5} opacity={0.45} />

            <line x1={1370} y1={342} x2={1325} y2={342} stroke={CK.ok} strokeWidth={3.5} opacity={0.45} />
            <line x1={1045} y1={342} x2={970} y2={342} stroke={CK.ok} strokeWidth={3.5} opacity={0.45} />
            <line x1={750} y1={342} x2={685} y2={342} stroke={CK.ok} strokeWidth={3.5} opacity={0.45} />

            {/* Animated round-trip glowing packet */}
            <Packet pts={isFwd ? CACHE_FWD : CACHE_RET} k={rtK} color={isFwd ? CK.push : CK.ok} r={10} trail={true} />

            {/* Numbered hop badges in Cue 4 */}
            {opAct2Cue4Sub > 0.01 && (
              <g opacity={opAct2Cue4Sub}>
                {[
                  { x: 110, y: 260, n: "1", c: CK.push },
                  { x: 1045, y: 260, n: "2", c: CK.card },
                  { x: 415, y: 260, n: "3", c: CK.ok },
                ].map((b) => (
                  <g key={b.n}>
                    <circle cx={b.x} cy={b.y} r={18} fill={b.c} />
                    <text x={b.x} y={b.y} textAnchor="middle" dominantBaseline="central" fontSize={19} fontWeight={800} fill={C.bg} fontFamily={MONO}>
                      {b.n}
                    </text>
                  </g>
                ))}
              </g>
            )}
          </g>
        )}

        {/* ==================== CUE 5: VERIFIED LATENCY COMPARISON CHART ==================== */}
        {opCmp > 0.01 && (
          <g opacity={opCmp}>
            <HBarChart
              x={90}
              y={325}
              w={1720}
              labelW={460}
              barH={68}
              gap={48}
              max={2600}
              rows={[
                {
                  label: "Direct Push (Plain PCIe)",
                  sub: "HW IOMMU ON · saturn ⇄ CN106",
                  value: 897,
                  valueText: "897 ns",
                  color: CK.ok,
                  k: kBarDirect,
                },
                {
                  label: "Real CXL-NIC Hardware",
                  sub: "Published CXL NICs · NO IOMMU",
                  value: 1700,
                  valueText: "",
                  color: CK.cxl,
                  k: kBarCxl,
                },
                {
                  label: "Stock NIC Queue (SDP)",
                  sub: "Default card queue · ~2.7× slower",
                  value: 2400,
                  valueText: "2,400 ns",
                  color: CK.sdp,
                  k: kBarSdp,
                },
              ]}
            />
            {/* Range whisker (1,300..2,100 ns) + non-overlapping label on the 1700 ns CXL bar */}
            {kBarCxl > 0.55 && (
              <g opacity={clamp01((kBarCxl - 0.55) / 0.35)}>
                <line x1={cxlX1} y1={cxlRowMidY} x2={cxlX2} y2={cxlRowMidY} stroke={C.text} strokeWidth={3.5} />
                <line x1={cxlX1} y1={cxlRowMidY - 14} x2={cxlX1} y2={cxlRowMidY + 14} stroke={C.text} strokeWidth={3.5} />
                <line x1={cxlX2} y1={cxlRowMidY - 14} x2={cxlX2} y2={cxlRowMidY + 14} stroke={C.text} strokeWidth={3.5} />
                <text
                  x={cxlX2 + 16}
                  y={cxlRowMidY}
                  dominantBaseline="central"
                  fontSize={23}
                  fontWeight={800}
                  fill={CK.cxl}
                  fontFamily={MONO}
                >
                  1.3–2.1 µs
                </text>
              </g>
            )}
          </g>
        )}
      </Stage>

      {/* ==================== CUE 0: QUESTION 1 & TWO MYTHS ==================== */}
      {opQ > 0.01 && (
        <div style={{ position: "absolute", inset: 0, opacity: opQ, pointerEvents: "none" }}>
          <QuestionBanner
            num="QUESTION 1"
            question="Isn't plain PCIe MMIO slow — and forced through DRAM without coherence?"
            sub="CC-NIC's foundational premise: uncached PCIe writes cannot match coherent CXL."
            color={CK.push}
            y={180}
            opacity={prog(t, T[0], 0.55)}
            dy={(1 - prog(t, T[0], 0.55)) * 22}
          />

          {[
            {
              x: 110,
              badge: "MYTH A · THROUGHPUT",
              title: "MMIO throughput stalls (~8 M/s)",
              desc: "Assumes CPU-issued 64 B PCIe writes bottleneck far below link speed.",
              color: CK.trap,
              delay: 0.35,
            },
            {
              x: 990,
              badge: "MYTH B · LATENCY",
              title: "Every message hits slow DRAM",
              desc: "Assumes uncached PCIe traffic must crawl through host and card memory.",
              color: CK.cxl,
              delay: 0.55,
            },
          ].map((m, i) => {
            const k = prog(t, T[0] + m.delay, 0.55);
            return (
              <div
                key={i}
                style={{
                  position: "absolute",
                  left: m.x,
                  top: 435,
                  width: 820,
                  height: 256,
                  opacity: k,
                  transform: `translateY(${(1 - k) * 24}px)`,
                  background: "linear-gradient(150deg, rgba(20,27,46,0.96), rgba(11,16,32,0.94))",
                  border: `2px solid ${m.color}88`,
                  borderTop: `6px solid ${m.color}`,
                  borderRadius: 20,
                  padding: "30px 36px",
                  boxSizing: "border-box",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "center",
                  boxShadow: "0 16px 38px rgba(0,0,0,0.45)",
                }}
              >
                <span
                  style={{
                    alignSelf: "flex-start",
                    fontSize: 19,
                    fontWeight: 800,
                    color: m.color,
                    fontFamily: MONO,
                    background: `${m.color}1a`,
                    border: `1.5px solid ${m.color}88`,
                    borderRadius: 8,
                    padding: "5px 14px",
                  }}
                >
                  {m.badge}
                </span>
                <div style={{ fontSize: 32, fontWeight: 800, color: C.text, marginTop: 18, lineHeight: 1.2 }}>
                  {m.title}
                </div>
                <div style={{ fontSize: 23, color: C.muted, marginTop: 12, lineHeight: 1.38 }}>{m.desc}</div>
              </div>
            );
          })}
        </div>
      )}

      {/* ==================== ACT 1 OVERLAYS (CUES 1 & 2) ==================== */}
      {opAct1 > 0.01 && (
        <div style={{ position: "absolute", inset: 0, opacity: opAct1, pointerEvents: "none" }}>
          {/* Act 1 Header — Cue 1 */}
          {opAct1Cue1Text > 0.01 && (
            <div
              style={{
                position: "absolute",
                left: 80,
                top: 142,
                display: "flex",
                alignItems: "center",
                gap: 18,
                opacity: opAct1Cue1Text,
              }}
            >
              <span
                style={{
                  fontSize: 19,
                  fontWeight: 800,
                  color: C.bg,
                  background: CK.trap,
                  borderRadius: 8,
                  padding: "6px 16px",
                  fontFamily: MONO,
                }}
              >
                ACT 1 · THROUGHPUT
              </span>
              <span style={{ fontSize: 34, fontWeight: 800, color: C.text }}>
                Fencing Every 64 B Write Reproduces CC-NIC&apos;s Stall
              </span>
            </div>
          )}

          {/* Act 1 Header — Cue 2 */}
          {opAct1Cue2Text > 0.01 && (
            <div
              style={{
                position: "absolute",
                left: 80,
                top: 142,
                display: "flex",
                alignItems: "center",
                gap: 18,
                opacity: opAct1Cue2Text,
              }}
            >
              <span
                style={{
                  fontSize: 19,
                  fontWeight: 800,
                  color: C.bg,
                  background: CK.ok,
                  borderRadius: 8,
                  padding: "6px 16px",
                  fontFamily: MONO,
                }}
              >
                ACT 1 · THROUGHPUT
              </span>
              <span style={{ fontSize: 34, fontWeight: 800, color: C.text }}>
                Relax the Fence: One Core Saturates PCIe Gen5 ×4
              </span>
            </div>
          )}

          {/* Left panel captions — Cue 1 */}
          {opAct1Cue1Text > 0.01 && (
            <div
              style={{
                position: "absolute",
                left: 95,
                top: 252,
                width: 735,
                opacity: opAct1Cue1Text,
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 26, fontWeight: 800, color: CK.trap, fontFamily: MONO }}>
                Fenced Every Write (K = 1)
              </div>
              <div style={{ marginTop: 315, fontSize: 25, fontWeight: 700, color: C.text }}>
                Core stalls waiting after every single 64 B write
              </div>
              <div style={{ marginTop: 12, fontSize: 22, fontWeight: 700, color: CK.trap, fontFamily: MONO }}>
                1 write in flight → 8.10 M/s
              </div>
            </div>
          )}

          {/* Left panel captions — Cue 2 */}
          {opAct1Cue2Text > 0.01 && (
            <div
              style={{
                position: "absolute",
                left: 95,
                top: 252,
                width: 735,
                opacity: opAct1Cue2Text,
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 26, fontWeight: 800, color: CK.ok, fontFamily: MONO }}>
                Unfenced Direct Push (Pipelined)
              </div>
              <div style={{ marginTop: 315, fontSize: 25, fontWeight: 700, color: C.text }}>
                Host write queues pipeline 64 B writes in parallel
              </div>
              <div style={{ marginTop: 12, fontSize: 22, fontWeight: 700, color: CK.ok, fontFamily: MONO }}>
                175.2 M/s · 11.21 GB/s payload (1 core)
              </div>
            </div>
          )}

          {/* Cue 2 HeroStat Callouts below the bar chart */}
          {opAct1Cue2Text > 0.01 && (
            <>
              <HeroStat
                x={920}
                y={590}
                w={445}
                h={186}
                value={`${speedupStr}×`}
                label="Higher 64 B Throughput"
                sub="97.9% of Gen5 ×4 line rate"
                color={CK.ok}
                opacity={opAct1Cue2Text}
                dy={(1 - opAct1Cue2Text) * 20}
              />
              <HeroStat
                x={1395}
                y={590}
                w={445}
                h={186}
                value={`${rateStr} M/s`}
                label="Single Host Core"
                sub="11.21 GB/s · zero CXL"
                color={CK.push}
                opacity={opAct1Cue2Text}
                dy={(1 - opAct1Cue2Text) * 20}
              />
            </>
          )}
        </div>
      )}

      {/* ==================== ACT 2 OVERLAYS (CUES 3 & 4) ==================== */}
      {opAct2 > 0.01 && (
        <div style={{ position: "absolute", inset: 0, opacity: opAct2, pointerEvents: "none" }}>
          <div style={{ position: "absolute", left: 80, top: 138, display: "flex", alignItems: "center", gap: 18 }}>
            <span
              style={{
                fontSize: 19,
                fontWeight: 800,
                color: C.bg,
                background: CK.push,
                borderRadius: 8,
                padding: "6px 16px",
                fontFamily: MONO,
              }}
            >
              ACT 2 · LATENCY
            </span>
            <span style={{ fontSize: 34, fontWeight: 800, color: C.text }}>
              All Three Cache Shortcuts Already Work — Fast Path Stays in Caches
            </span>
          </div>

          {/* Cue 3 bottom explanation */}
          {opAct2Cue3Sub > 0.01 && (
            <VerdictRow
              x={140}
              y={592}
              w={1640}
              badge="COLD-FLUSH PROOF"
              badgeColor={CK.push}
              title="Flushing each cache tier one at a time isolates every hop's real savings"
              detail="The 64 B fast path stays in on-chip caches (99.94% card L3 hit rate) — bypassing Host and Card DRAM."
              opacity={opAct2Cue3Sub}
              dy={(1 - opAct2Cue3Sub) * 18}
            />
          )}

          {/* Cue 4: 3 numbered shortcut cards */}
          {opAct2Cue4Sub > 0.01 &&
            SHORTCUTS.map((s, i) => {
              const k = prog(t, T[4] + 0.15 + i * 0.2, 0.5) * opAct2Cue4Sub;
              return (
                <div
                  key={s.hop}
                  style={{
                    position: "absolute",
                    left: 80 + i * 598,
                    top: 568,
                    width: 564,
                    height: 222,
                    opacity: k,
                    transform: `translateY(${(1 - k) * 22}px)`,
                    background: "linear-gradient(155deg, rgba(20,27,46,0.96), rgba(11,16,32,0.94))",
                    border: `2px solid ${s.color}99`,
                    borderTop: `6px solid ${s.color}`,
                    borderRadius: 18,
                    padding: "24px 28px",
                    boxSizing: "border-box",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "center",
                    boxShadow: "0 14px 36px rgba(0,0,0,0.45)",
                  }}
                >
                  <div style={{ fontSize: 19, fontWeight: 800, color: s.color, fontFamily: MONO, letterSpacing: 0.6 }}>
                    {s.hop}
                  </div>
                  <div style={{ fontSize: 52, fontWeight: 800, color: C.text, fontFamily: MONO, marginTop: 8, lineHeight: 1.05 }}>
                    {s.saving}
                  </div>
                  <div style={{ fontSize: 22, color: C.muted, marginTop: 10, lineHeight: 1.3 }}>{s.detail}</div>
                </div>
              );
            })}
        </div>
      )}

      {/* ==================== CUE 5: VERIFIED LATENCY COMPARISON ==================== */}
      {opCmp > 0.01 && (
        <div style={{ position: "absolute", inset: 0, opacity: opCmp, pointerEvents: "none" }}>
          <div style={{ position: "absolute", left: 90, top: 146 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
              <span
                style={{
                  fontSize: 19,
                  fontWeight: 800,
                  color: C.bg,
                  background: CK.ok,
                  borderRadius: 8,
                  padding: "6px 16px",
                  fontFamily: MONO,
                }}
              >
                VERIFIED LATENCY
              </span>
              <span style={{ fontSize: 38, fontWeight: 800, color: C.text }}>
                Verified 64 B Round-Trip Latency (Hardware IOMMU On)
              </span>
            </div>
            <div style={{ fontSize: 23, color: C.muted, marginTop: 12 }}>
              Plain commercial PCIe beats both stock NIC queues and published CXL-NIC hardware.
            </div>
          </div>

          <VerdictRow
            x={90}
            y={708}
            w={1740}
            badge="Q1 ANSWERED"
            badgeColor={CK.ok}
            title="Plain PCIe is already fast: 175.2 M/s throughput (97.9% line rate) and 897 ns cache-to-cache RTT."
            detail="Zero hardware cache coherence required — and full hardware IOMMU protection stays enabled."
            opacity={prog(t, T[5] + 0.75, 0.5) * opCmp}
            dy={(1 - prog(t, T[5] + 0.75, 0.5)) * 18}
          />
        </div>
      )}
    </>
  );
}

export const C02: SceneDef = {
  id: "C02",
  title: "1 \u00b7 Why Plain PCIe Is Fast",
  stage: 0,
  source: "Xeon Host \u2192 PCIe Gen5 \u00d74 \u2192 Marvell CN106 DPU (HW IOMMU on)",
  dur: DUR,
  cues,
  C: Body,
};
