import React from "react";
import {
  C,
  Cue,
  FONT,
  MONO,
  PacketStream,
  SceneDef,
  Stage,
  clamp01,
  countUp,
  fade,
  lerp,
  linScale,
  pop,
  prog,
  useRealT,
  useT,
} from "../lib";
import { CK, SplitBar } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 7.26, "Report 1 tests CC-NIC's core premise: is ordinary **PCIe direct push** actually slow?", "Report one tests the core premise of C C NIC: is ordinary P C I express direct push really slow?"], // 0
  [7.61, 18.27, "Inserting an **SFENCE** after every 64 B `MOVDIR64B` drops throughput to **8.10 M/s**—just like CC-NIC Fig. 2.", "Inserting an S fence after every single sixty-four byte move dir sixty-four B write stalls throughput at eight point one zero million per second—just like C C NIC figure two."], // 1
  [18.62, 33.30, "Relax fence interval `K`: **32 host CHA queues** pipeline writes—at `K=64` or unfenced, **1 core hits 175.2 M/s** (**21.6×**).", "Relax the fence interval K, and thirty-two host C H A queues pipeline writes in parallel. At K equals sixty-four or unfenced, one core hits one hundred seventy-five point two million per second—twenty-one point six times faster."], // 2
  [33.65, 49.59, "**175.2 M/s** (**11.21 GB/s**) is **97.9%** of Gen5 x4's 64 B ceiling (**179.0 M/s**); only hammering one fixed address drops to **8.89 M/s**.", "With twenty-four bytes of P C I express framing per sixty-four byte write, gen five by four tops out at one hundred seventy-nine million per second—so one core hits ninety-seven point nine percent of line rate. Only hammering one fixed address drops to eight point eight nine million."], // 3
  [49.94, 62.16, "Why does **DMA** win on large payloads? **128–512 B TLPs** amortize the fixed **24 B** PCIe header, reaching **13.3–15.1 GB/s**.", "For large payloads, native D M A still wins by packing contiguous data into one hundred twenty-eight to five hundred twelve byte packets, amortizing the twenty-four byte header up to fifteen gigabytes per second."], // 4
];
const DUR = 63.56; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const SWEEP_DATA: { label: string; rate: number; color: string; delay: number }[] = [
  { label: "K=1", rate: 8.1, color: CK.trap, delay: 0.0 },
  { label: "K=2", rate: 15.67, color: CK.cxl, delay: 0.5 },
  { label: "K=4", rate: 30.19, color: CK.cxl, delay: 1.3 },
  { label: "K=8", rate: 55.34, color: CK.cxl, delay: 2.1 },
  { label: "K=16", rate: 101.16, color: CK.push, delay: 3.0 },
  { label: "K=32", rate: 165.85, color: CK.push, delay: 4.1 },
  { label: "K=64", rate: 175.19, color: CK.push, delay: 5.4 },
  { label: "Unf.", rate: 175.2, color: CK.ok, delay: 6.7 },
];

const CEILING_MPS = 179.02;

const TLP_EFF: { label: string; sub: string; gbps: number; eff: string; note: string; color: string }[] = [
  { label: "64 B push", sub: "88 B wire · 72.7% eff.", gbps: 11.46, eff: "11.46 GB/s", note: "meas. 11.21", color: CK.push },
  { label: "128 B DMA", sub: "152 B wire · 84.2% eff.", gbps: 13.27, eff: "13.27 GB/s", note: "DPI: 12.90", color: CK.dma },
  { label: "256 B DMA", sub: "280 B wire · 91.4% eff.", gbps: 14.4, eff: "14.40 GB/s", note: "91.4% wire", color: CK.dma },
  { label: "512 B DMA", sub: "536 B wire · 95.5% eff.", gbps: 15.05, eff: "15.05 GB/s", note: "95.5% wire", color: CK.dma },
];

function Body() {
  const t = useT();
  const rt = useRealT();

  // Global panel entrance
  const kIn = prog(t, 0.15, 0.65);

  // Regime crossfades on Left Panel status pill (mutually exclusive — zero text overlap!)
  const kBanner0 = fade(t, T[0] + 0.1, T[1] - 0.15, 0.45);
  const kBanner1 = fade(t, T[1] + 0.05, T[2] - 0.15, 0.45);
  const kBanner2 = fade(t, T[2] + 0.05, T[4] - 0.15, 0.45);
  const kBanner4 = prog(t, T[4] + 0.1, 0.45);

  // SFENCE wall in Cue 1 (dissolves cleanly in Cue 2)
  const kWall = fade(t, T[1] + 0.3, T[2] - 0.1, 0.45);
  // 32 CHA queues open in Cue 2+
  const kPipe = prog(t, T[2] + 0.4, 0.8);
  const activeCha = kWall > 0.2 ? 1 : Math.round(lerp(1, 32, kPipe));

  // Right panel crossfade: MRV-89 SFENCE sweep (Cues 0..3) -> TLP efficiency (Cue 4)
  const kSweep = fade(t, T[0] + 0.1, T[4] - 0.15, 0.5);
  const kTlpPanel = prog(t, T[4] + 0.15, 0.55);

  // Cue 1..3 chart elements
  const kBar0 = prog(t, T[1] + 0.9, 0.9); // K=1 bar
  const kCalloutK1 = fade(t, T[1] + 2.0, T[2] + 1.2, 0.45);
  const k21x = prog(t, T[2] + 7.4, 0.6) * kSweep;
  const kCeilHl = prog(t, T[3] + 0.5, 0.65) * kSweep;

  // Left panel bottom: TLP 88 B framing (Cue 3) -> 64 B vs 512 B overhead visual (Cue 4)
  const kTlp88 = prog(t, T[3] + 0.3, 0.65);
  const kSameAddr = fade(t, T[3] + 6.5, T[4] - 0.15, 0.45);
  const kTlp512 = prog(t, T[4] + 0.5, 0.7);

  // Chart coordinates on Right Panel (x 858..1862, y 140..862)
  const cx0 = 960;
  const cx1 = 1824;
  const cy0 = 766; // y bottom (0 M/s)
  const cy1 = 296; // y top (200 M/s) — extra headroom for 97.9% banner & ceiling label
  const sy = linScale(0, 200, cy0, cy1);
  const slotW = (cx1 - cx0) / SWEEP_DATA.length;
  const barW = 72;

  // Left schematic coordinates (x 58..824, y 140..862)
  const laneY = 368;
  const slowPktFrac = (rt * 0.34) % 1;
  const wallX = 414;

  // Packet stream along PCIe lanes when pipelined (Cue 2+)
  const pcieLaneTop: [number, number][] = [
    [388, 344],
    [588, 344],
  ];
  const pcieLaneMid: [number, number][] = [
    [388, 368],
    [588, 368],
  ];
  const pcieLaneBot: [number, number][] = [
    [388, 392],
    [588, 392],
  ];

  // Count-up headline value for 175.2 M/s
  const rateText =
    t < T[2] + 6.7 ? "175.2" : countUp(t, T[2] + 6.7, 1.0, 175.2, 1, 101.2);

  return (
    <>
      <Stage opacity={kIn}>
        {/* ================= LEFT PANEL: PCIe Pipeline & TLP Framing ================= */}
        <rect
          x={58}
          y={140}
          width={766}
          height={722}
          rx={20}
          fill="rgba(16,22,40,0.92)"
          stroke={C.line}
          strokeWidth={2}
        />

        {/* Top regime header strip inside Left Panel */}
        <g>
          {kBanner0 > 0.01 && (
            <g opacity={kBanner0}>
              <rect x={86} y={164} width={710} height={46} rx={12} fill={`${CK.cxl}1c`} stroke={CK.cxl} strokeWidth={1.8} />
              <text x={441} y={187} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={800} fill={CK.cxl} fontFamily={FONT}>
                Is PCIe MMIO really slow?
              </text>
            </g>
          )}
          {kBanner1 > 0.01 && (
            <g opacity={kBanner1}>
              <rect x={86} y={164} width={710} height={46} rx={12} fill={`${CK.trap}20`} stroke={CK.trap} strokeWidth={1.8} />
              <text x={441} y={187} textAnchor="middle" dominantBaseline="central" fontSize={21} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                K=1 · SFENCE STALLS EVERY 64 B WRITE
              </text>
            </g>
          )}
          {kBanner2 > 0.01 && (
            <g opacity={kBanner2}>
              <rect x={86} y={164} width={710} height={46} rx={12} fill={`${CK.push}1c`} stroke={CK.push} strokeWidth={1.8} />
              <text x={441} y={187} textAnchor="middle" dominantBaseline="central" fontSize={21} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                32 HOST CHA QUEUES · PIPELINED MOVDIR64B
              </text>
            </g>
          )}
          {kBanner4 > 0.01 && (
            <g opacity={kBanner4}>
              <rect x={86} y={164} width={710} height={46} rx={12} fill={`${CK.dma}1e`} stroke={CK.dma} strokeWidth={1.8} />
              <text x={441} y={187} textAnchor="middle" dominantBaseline="central" fontSize={21} fontWeight={800} fill={CK.dma} fontFamily={MONO}>
                HEADER AMORTIZATION · 64 B PUSH vs DMA
              </text>
            </g>
          )}
        </g>

        {/* Hardware Pipeline Diagram (y 232..498) */}
        {/* Host Core + 32 CHA Queues box */}
        <rect
          x={86}
          y={236}
          width={302}
          height={252}
          rx={16}
          fill="#10182d"
          stroke={kPipe > 0.3 ? CK.push : CK.host}
          strokeWidth={2.5}
        />
        <text x={237} y={266} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.text} fontFamily={FONT}>
          Host Core (cpu40)
        </text>
        <text x={237} y={291} textAnchor="middle" fontSize={17} fill={C.muted} fontFamily={MONO}>
          Xeon 3.9 GHz · MOVDIR64B
        </text>

        {/* 32 Host CHA queue slots (4 rows x 8 cols) */}
        <text x={106} y={326} fontSize={16} fontWeight={700} fill={kPipe > 0.3 ? CK.push : C.muted} fontFamily={MONO}>
          CHA Write Queues ({activeCha}/32)
        </text>
        {Array.from({ length: 32 }).map((_, i) => {
          const col = i % 8;
          const row = Math.floor(i / 8);
          const sx = 106 + col * 33;
          const sySlot = 338 + row * 28;
          const isLit = i < activeCha;
          const pulse = isLit && kPipe > 0.2 ? 0.72 + 0.28 * Math.sin(rt * 7 + i * 0.6) : 1;
          return (
            <rect
              key={i}
              x={sx}
              y={sySlot}
              width={26}
              height={20}
              rx={4}
              fill={isLit ? (kWall > 0.3 ? CK.trap : CK.push) : "#1a233d"}
              stroke={isLit ? (kWall > 0.3 ? CK.trap : CK.push) : C.line}
              strokeWidth={1.2}
              opacity={isLit ? pulse : 0.65}
            />
          );
        })}
        <text x={237} y={472} textAnchor="middle" fontSize={16} fill={C.muted} fontFamily={MONO}>
          {kWall > 0.3 ? "1 in-flight (fenced)" : kPipe > 0.3 ? "32 in-flight MWr TLPs" : "64 B WC BAR4 writes"}
        </text>

        {/* PCIe Gen5 x4 Link Bridge (x 388..588) */}
        <line x1={388} y1={344} x2={588} y2={344} stroke={kPipe > 0.3 ? CK.push : C.dim} strokeWidth={3} opacity={0.65} />
        <line x1={388} y1={368} x2={588} y2={368} stroke={kPipe > 0.3 ? CK.push : C.dim} strokeWidth={3.5} opacity={0.8} />
        <line x1={388} y1={392} x2={588} y2={392} stroke={kPipe > 0.3 ? CK.push : C.dim} strokeWidth={3} opacity={0.65} />

        <rect x={406} y={248} width={164} height={46} rx={10} fill={C.bg} stroke={CK.push} strokeWidth={1.8} />
        <text x={488} y={266} textAnchor="middle" fontSize={18} fontWeight={800} fill={CK.push} fontFamily={MONO}>
          PCIe Gen5 ×4
        </text>
        <text x={488} y={286} textAnchor="middle" fontSize={15} fill={C.muted} fontFamily={MONO}>
          15.75 GB/s raw
        </text>

        {/* Slow stalled single packet (Cue 1) */}
        {kWall > 0.02 && (
          <g opacity={kWall}>
            {/* Red SFENCE wall */}
            <rect x={wallX - 6} y={316} width={12} height={106} rx={4} fill={CK.trap} />
            <rect x={392} y={432} width={132} height={34} rx={8} fill={`${CK.trap}28`} stroke={CK.trap} strokeWidth={1.8} />
            <text x={458} y={449} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              SFENCE!
            </text>
            {/* Single packet creeping through */}
            <rect
              x={lerp(424, 560, slowPktFrac)}
              y={laneY - 10}
              width={24}
              height={20}
              rx={5}
              fill={CK.trap}
            />
          </g>
        )}

        {/* Pipelined lush packet streams (Cue 2+) */}
        {kPipe > 0.02 && (
          <g opacity={kPipe}>
            <PacketStream pts={pcieLaneTop} travel={rt * 260} gap={42} size={11} color={CK.push} opacity={0.9} />
            <PacketStream pts={pcieLaneMid} travel={rt * 260 + 18} gap={42} size={12} color={CK.ok} opacity={0.95} />
            <PacketStream pts={pcieLaneBot} travel={rt * 260 + 32} gap={42} size={11} color={CK.push} opacity={0.9} />
            <rect x={410} y={432} width={156} height={34} rx={8} fill={`${CK.ok}20`} stroke={CK.ok} strokeWidth={1.6} />
            <text x={488} y={449} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              FULL PIPE
            </text>
          </g>
        )}

        {/* CN106 BAR4 Target box */}
        <rect
          x={588}
          y={236}
          width={208}
          height={252}
          rx={16}
          fill="#141928"
          stroke={CK.card}
          strokeWidth={2.5}
        />
        <text x={692} y={268} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.card} fontFamily={FONT}>
          CN106 BAR4
        </text>
        <text x={692} y={293} textAnchor="middle" fontSize={16} fill={C.muted} fontFamily={MONO}>
          PEM0 Inbound MWr
        </text>
        <rect x={608} y={322} width={168} height={74} rx={12} fill={`${CK.ok}18`} stroke={CK.ok} strokeWidth={1.8} />
        <text x={692} y={352} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
          48 MiB Card L3
        </text>
        <text x={692} y={378} textAnchor="middle" fontSize={16} fill={C.muted} fontFamily={MONO}>
          64 B ring cells
        </text>
        <rect x={608} y={412} width={168} height={56} rx={10} fill={`${CK.dma}14`} stroke={kBanner4 > 0.3 ? CK.dma : C.line} strokeWidth={1.8} />
        <text x={692} y={436} textAnchor="middle" fontSize={18} fontWeight={700} fill={kBanner4 > 0.3 ? CK.dma : C.muted} fontFamily={FONT}>
          DPI DMA Engine
        </text>
        <text x={692} y={456} textAnchor="middle" fontSize={15} fill={C.muted} fontFamily={MONO}>
          128–512 B TLPs
        </text>

        {/* Lower Left Panel: 88 B Wire TLP Framing (Cue 3+) */}
        <line x1={86} y1={514} x2={796} y2={514} stroke={C.line} strokeWidth={1.5} opacity={0.7} />

        {/* Cue 0..2 placeholder context note in lower-left so it never looks empty */}
        {kTlp88 < 0.99 && (
          <g opacity={1 - kTlp88}>
            <rect x={86} y={540} width={710} height={290} rx={16} fill="#10172b" stroke={C.line} strokeWidth={1.5} />
            <text x={116} y={584} fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
              MRV-89 Experimental Setup (54 runs)
            </text>
            <text x={116} y={626} fontSize={20} fill={C.muted} fontFamily={MONO}>
              • Single pinned core: cpu40 @ 3.90 GHz
            </text>
            <text x={116} y={664} fontSize={20} fill={C.muted} fontFamily={MONO}>
              • Instruction: 64 B MOVDIR64B (WC BAR4)
            </text>
            <text x={116} y={702} fontSize={20} fill={C.muted} fontFamily={MONO}>
              • Sweep: insert SFENCE every K writes
            </text>
            <text x={116} y={740} fontSize={20} fill={CK.cxl} fontFamily={MONO}>
              • Gen5 ×4 64 B ceiling: 179.02 M/s (11.46 GB/s)
            </text>
            <text x={116} y={796} fontSize={19} fill={CK.ok} fontWeight={700} fontFamily={FONT}>
              Hardware IOMMU enabled · batch = 1 · distinct 64 B slots
            </text>
          </g>
        )}

        {/* Cue 3+: 88 B Wire TLP Dissection */}
        {kTlp88 > 0.01 && (
          <g opacity={kTlp88}>
            <text x={86} y={550} fontSize={22} fontWeight={800} fill={C.text} fontFamily={FONT}>
              64 B MOVDIR64B Wire TLP = <tspan fill={CK.cxl} fontFamily={MONO}>88 B</tspan> (72.7% payload)
            </text>
            {/* 4 B STP | 16 B 4DW hdr | 64 B payload | 4 B LCRC */}
            <SplitBar
              x={86}
              y={570}
              w={710}
              h={56}
              max={88}
              k={kTlp88}
              segs={[
                { label: "", value: 4, color: CK.trap },
                { label: "16 B hdr", value: 16, color: CK.cxl },
                { label: "64 B Payload (72.7%)", value: 64, color: CK.push },
                { label: "", value: 4, color: CK.trap },
              ]}
            />
            {/* Crisp sub-legend for STP / LCRC */}
            <text x={86} y={652} fontSize={18} fill={C.muted} fontFamily={MONO}>
              <tspan fill={CK.trap} fontWeight={700}>4 B STP</tspan> +{" "}
              <tspan fill={CK.cxl} fontWeight={700}>16 B 4DW hdr</tspan> +{" "}
              <tspan fill={CK.push} fontWeight={700}>64 B payload</tspan> +{" "}
              <tspan fill={CK.trap} fontWeight={700}>4 B LCRC</tspan> = 88 B
            </text>
            <text x={86} y={682} fontSize={18} fill={C.muted} fontFamily={MONO}>
              15.75 GB/s ÷ 88 B = <tspan fill={CK.cxl} fontWeight={700}>179.02 M/s</tspan> → measured{" "}
              <tspan fill={CK.ok} fontWeight={700}>97.9%</tspan>
            </text>

            {/* Cue 3 warning: Same-address serialization (fades out at T[4]) */}
            {kSameAddr > 0.01 && (
              <g opacity={kSameAddr}>
                <rect
                  x={86}
                  y={714}
                  width={710}
                  height={118}
                  rx={14}
                  fill="rgba(249,65,68,0.12)"
                  stroke={CK.trap}
                  strokeWidth={2}
                />
                <text x={112} y={752} fontSize={22} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
                  ⚠ Caveat: Hammering One Fixed 64 B BAR4 Address
                </text>
                <rect x={112} y={770} width={500} height={24} rx={6} fill="#181f36" stroke={C.line} strokeWidth={1.2} />
                <rect x={112} y={770} width={Math.max(12, (8.89 / 179.02) * 500)} height={24} rx={6} fill={CK.trap} />
                <text x={148} y={787} fontSize={19} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  8.89 M/s (per-core serialized)
                </text>
                <text x={112} y={818} fontSize={17} fill={C.muted} fontFamily={MONO}>
                  Use a ring of distinct 64 B slots — never overwrite 1 slot!
                </text>
              </g>
            )}

            {/* Cue 4: 512 B DMA TLP comparison bar (replaces Same-Addr warning cleanly) */}
            {kTlp512 > 0.01 && (
              <g opacity={kTlp512}>
                <text x={86} y={736} fontSize={22} fontWeight={800} fill={CK.dma} fontFamily={FONT}>
                  512 B Native DMA TLP = <tspan fontFamily={MONO}>536 B</tspan> (95.5% payload!)
                </text>
                <SplitBar
                  x={86}
                  y={752}
                  w={710}
                  h={52}
                  max={536}
                  k={kTlp512}
                  segs={[
                    { label: "", value: 20, color: CK.cxl },
                    { label: "512 B Payload (95.5% wire efficiency)", value: 512, color: CK.dma },
                    { label: "", value: 4, color: CK.trap },
                  ]}
                />
                <text x={86} y={832} fontSize={18} fill={C.muted} fontFamily={MONO}>
                  Fixed 24 B framing shrinks from <tspan fill={CK.cxl} fontWeight={700}>27.3%</tspan> overhead down to{" "}
                  <tspan fill={CK.ok} fontWeight={700}>4.5%</tspan>
                </text>
              </g>
            )}
          </g>
        )}

        {/* ================= RIGHT PANEL: MRV-89 SFENCE Sweep (Cues 0..3) ================= */}
        <rect
          x={856}
          y={140}
          width={1006}
          height={722}
          rx={20}
          fill="rgba(16,22,40,0.92)"
          stroke={kTlpPanel > 0.4 ? CK.dma : C.line}
          strokeWidth={2}
        />

        {kSweep > 0.01 && (
          <g opacity={kSweep}>
            {/* Chart Title */}
            <text x={892} y={184} fontSize={26} fontWeight={800} fill={C.text} fontFamily={FONT}>
              MRV-89: Single-Core 64 B Push vs SFENCE Interval K
            </text>
            <text x={892} y={214} fontSize={18} fill={C.muted} fontFamily={MONO}>
              Xeon cpu40 → CN106 BAR4 · 1 core · batch = 1 · 54 runs
            </text>

            {/* Horizontal grid & Y axis ticks */}
            {[0, 50, 100, 150, 200].map((v) => (
              <g key={v}>
                <line x1={cx0} y1={sy(v)} x2={cx1} y2={sy(v)} stroke={C.line} strokeWidth={1.4} opacity={0.65} />
                <text x={cx0 - 14} y={sy(v)} textAnchor="end" dominantBaseline="central" fontSize={20} fill={C.muted} fontFamily={MONO}>
                  {v}
                </text>
              </g>
            ))}
            <text x={cx0 - 14} y={cy1 - 26} textAnchor="end" fontSize={19} fontWeight={700} fill={C.muted} fontFamily={MONO}>
              M/s
            </text>
            {/* Axes lines */}
            <line x1={cx0} y1={cy0} x2={cx1} y2={cy0} stroke={C.dim} strokeWidth={2.5} />
            <line x1={cx0} y1={cy0} x2={cx0} y2={cy1 - 12} stroke={C.dim} strokeWidth={2.5} />

            {/* Dashed yellow Gen5 x4 64 B ceiling line at 179.02 M/s */}
            <line
              x1={cx0}
              y1={sy(CEILING_MPS)}
              x2={cx1}
              y2={sy(CEILING_MPS)}
              stroke={CK.cxl}
              strokeWidth={3}
              strokeDasharray="10 8"
            />
            <rect
              x={cx0 + 10}
              y={sy(CEILING_MPS) - 40}
              width={450}
              height={32}
              rx={8}
              fill="rgba(11,16,32,0.92)"
              stroke={`${CK.cxl}88`}
              strokeWidth={1.4}
            />
            <text x={cx0 + 24} y={sy(CEILING_MPS) - 24} dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              179.0 M/s · Gen5 ×4 64 B ceiling (11.46 GB/s)
            </text>

            {/* Animated Bars */}
            {SWEEP_DATA.map((d, i) => {
              const kk = i === 0 ? kBar0 : prog(t, T[2] + d.delay, 0.75);
              const bx = cx0 + slotW * (i + 0.5);
              const top = lerp(cy0, sy(d.rate), kk);
              const h = Math.max(0, cy0 - top);
              const isPeak = i >= 6;
              const insideLabel = d.rate >= 160;
              return (
                <g key={d.label}>
                  {/* Subtle glow halo on top bars when reached */}
                  {isPeak && kk > 0.7 && (
                    <rect
                      x={bx - barW / 2 - 4}
                      y={top - 4}
                      width={barW + 8}
                      height={h + 4}
                      rx={10}
                      fill="none"
                      stroke={d.color}
                      strokeWidth={3}
                      opacity={0.35 * kk}
                    />
                  )}
                  {kk > 0.01 && (
                    <rect
                      x={bx - barW / 2}
                      y={top}
                      width={barW}
                      height={h}
                      rx={7}
                      fill={d.color}
                      opacity={0.92}
                    />
                  )}
                  {kk > 0.55 && (
                    <text
                      x={bx}
                      y={insideLabel ? top + 28 : top - 12}
                      textAnchor="middle"
                      fontSize={i === 0 || isPeak ? 20 : 19}
                      fontWeight={800}
                      fill={insideLabel ? C.bg : d.color}
                      fontFamily={MONO}
                      opacity={clamp01((kk - 0.55) / 0.45)}
                    >
                      {i === 7 ? rateText : d.rate.toFixed(i >= 4 ? 1 : 2)}
                    </text>
                  )}
                  <text x={bx} y={cy0 + 30} textAnchor="middle" fontSize={20} fontWeight={700} fill={C.text} fontFamily={MONO}>
                    {d.label}
                  </text>
                </g>
              );
            })}
            <text x={(cx0 + cx1) / 2} y={cy0 + 66} textAnchor="middle" fontSize={21} fill={C.muted} fontFamily={FONT}>
              SFENCE interval K (writes per fence)
            </text>

            {/* Cue 1 callout: CC-NIC Fig. 2 regime (fades out cleanly before K=8 grows) */}
            {kCalloutK1 > 0.01 && (
              <g opacity={kCalloutK1}>
                <line
                  x1={cx0 + slotW * 0.5}
                  y1={sy(8.1) - 40}
                  x2={cx0 + slotW * 0.5 + 32}
                  y2={sy(66)}
                  stroke={CK.trap}
                  strokeWidth={2.5}
                />
                <rect
                  x={cx0 + 22}
                  y={sy(102)}
                  width={340}
                  height={74}
                  rx={12}
                  fill="rgba(34,14,22,0.95)"
                  stroke={CK.trap}
                  strokeWidth={2}
                />
                <text x={cx0 + 42} y={sy(102) + 28} fontSize={21} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
                  CC-NIC Fig. 2 regime
                </text>
                <text x={cx0 + 42} y={sy(102) + 55} fontSize={18} fill={C.text} fontFamily={MONO}>
                  8.10 M/s · fenced every write
                </text>
              </g>
            )}

            {/* Cue 2+ bold 21.6x green speedup badge (clean upper-left quadrant, zero bar overlap) */}
            {k21x > 0.01 && (
              <g
                opacity={k21x}
                transform={`translate(${cx0 + 42}, ${sy(148)}) scale(${lerp(0.85, 1, pop(t, T[2] + 7.4, 0.45))})`}
              >
                <rect x={0} y={0} width={312} height={90} rx={16} fill="rgba(14,34,30,0.96)" stroke={CK.ok} strokeWidth={2.8} />
                <text x={156} y={37} textAnchor="middle" fontSize={36} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  21.6× faster
                </text>
                <text x={156} y={69} textAnchor="middle" fontSize={18} fontWeight={700} fill={C.text} fontFamily={MONO}>
                  8.10 → 175.2 M/s (1 core)
                </text>
              </g>
            )}

            {/* Cue 3: 97.9% ceiling highlight badge (above K=32..Unf., generous horizontal padding) */}
            {kCeilHl > 0.01 && (
              <g opacity={kCeilHl}>
                <rect
                  x={1326}
                  y={224}
                  width={512}
                  height={46}
                  rx={12}
                  fill="rgba(14,36,42,0.96)"
                  stroke={CK.push}
                  strokeWidth={2.4}
                />
                <text x={1582} y={247} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                  97.9% of Gen5 ×4 line rate · 11.21 GB/s
                </text>
              </g>
            )}
          </g>
        )}

        {/* ================= RIGHT PANEL (Cue 4): TLP Efficiency Comparison ================= */}
        {kTlpPanel > 0.01 && (
          <g opacity={kTlpPanel}>
            <text x={896} y={190} fontSize={28} fontWeight={800} fill={C.text} fontFamily={FONT}>
              PCIe Gen5 ×4 Wire Efficiency vs TLP Size
            </text>
            <text x={896} y={222} fontSize={19} fill={C.muted} fontFamily={MONO}>
              Fixed 24 B MWr overhead amortized across 128–512 B native DMA TLPs
            </text>

            {/* Custom clean horizontal bars: track x=1126..1618 (0..16 GB/s), right value col x=1640..1830 */}
            {(() => {
              const trackX = 1126;
              const trackW = 492;
              const maxG = 16.0;
              const refX = trackX + (15.75 / maxG) * trackW;
              const rowY0 = 304;
              const rowH = 56;
              const rowGap = 32;
              const totalH = TLP_EFF.length * (rowH + rowGap) - rowGap;
              return (
                <g>
                  {/* Vertical dashed yellow raw Gen5 x4 ceiling line */}
                  <line
                    x1={refX}
                    y1={rowY0 - 20}
                    x2={refX}
                    y2={rowY0 + totalH + 12}
                    stroke={CK.cxl}
                    strokeWidth={2.5}
                    strokeDasharray="8 6"
                  />
                  <text x={refX} y={rowY0 - 30} textAnchor="middle" fontSize={18} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
                    15.75 GB/s raw Gen5 ×4
                  </text>

                  {TLP_EFF.map((row, idx) => {
                    const kk = prog(t, T[4] + 0.4 + idx * 0.65, 0.75);
                    const ry = rowY0 + idx * (rowH + rowGap);
                    const bw = Math.max(0, (row.gbps / maxG) * trackW * kk);
                    return (
                      <g key={row.label} opacity={Math.max(0.1, kk)}>
                        <text x={trackX - 18} y={ry + 20} textAnchor="end" fontSize={22} fontWeight={800} fill={C.text} fontFamily={FONT}>
                          {row.label}
                        </text>
                        <text x={trackX - 18} y={ry + 44} textAnchor="end" fontSize={16} fill={C.muted} fontFamily={MONO}>
                          {row.sub}
                        </text>
                        <rect x={trackX} y={ry} width={trackW} height={rowH} rx={9} fill="#141b2e" stroke={C.line} strokeWidth={1.2} />
                        <rect x={trackX} y={ry} width={bw} height={rowH} rx={9} fill={row.color} opacity={0.92} />
                        {/* Measured / eff pill inside bar */}
                        {bw > 170 && (
                          <text
                            x={trackX + bw - 14}
                            y={ry + rowH / 2}
                            textAnchor="end"
                            dominantBaseline="central"
                            fontSize={17}
                            fontWeight={800}
                            fill={C.bg}
                            fontFamily={MONO}
                          >
                            {row.note}
                          </text>
                        )}
                        {/* Clean right-hand value column (never touches refLine!) */}
                        <text
                          x={trackX + trackW + 22}
                          y={ry + rowH / 2}
                          dominantBaseline="central"
                          fontSize={24}
                          fontWeight={800}
                          fill={row.color}
                          fontFamily={MONO}
                          opacity={kk > 0.3 ? clamp01((kk - 0.3) / 0.4) : 0}
                        >
                          {row.eff}
                        </text>
                      </g>
                    );
                  })}
                </g>
              );
            })()}

            {/* Bottom hybrid design takeaway box */}
            <rect
              x={896}
              y={688}
              width={926}
              height={138}
              rx={16}
              fill="rgba(20,27,46,0.95)"
              stroke={CK.dma}
              strokeWidth={2}
            />
            <text x={926} y={730} fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Why DirectPush Is Hybrid (Preview of Part 4)
            </text>
            <text x={926} y={768} fontSize={20} fill={CK.push} fontWeight={700} fontFamily={MONO}>
              • Small (≤ 256 B): MOVDIR64B inline → sub-µs, 0 DMA setup
            </text>
            <text x={926} y={802} fontSize={20} fill={CK.dma} fontWeight={700} fontFamily={MONO}>
              • Large (≥ 512 B): 64 B desc + DPI DMA → 12.9–15.1 GB/s (95.5%)
            </text>
          </g>
        )}
      </Stage>
    </>
  );
}

export const C02: SceneDef = {
  id: "C02",
  title: "1 \u00b7 PCIe MMIO Isn't Slow",
  stage: 0,
  source: "MRV-148 \u00b7 MRV-89 (54 runs) \u00b7 Xeon cpu40 3.9 GHz \u2192 CN106 BAR4",
  dur: DUR,
  cues,
  C: Body,
};
