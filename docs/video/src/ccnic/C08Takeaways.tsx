import React from "react";
import {
  C,
  Cue,
  FONT,
  MONO,
  Packet,
  Pt,
  SceneDef,
  Stage,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import { CK, QuestionBanner, VerdictRow } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 10.44, "**So what are the paper's contributions?** Every design step followed directly from testing **CC-NIC's assumption** on real silicon.", "So what are the paper's core contributions? Stepping back, every design step followed directly from testing C C NIC's central assumption on real commercial hardware."], // 0
  [10.79, 24.65, "**1 · Plain PCIe is fast**: unfenced push hits **175.2 M/s** (**97.9%** line rate), **897 ns** 3-hop cache RTT (with **IOMMU**), and **0%** TX coherence tax.", "Contribution one: plain P C I express is already fast—one hundred seventy-five million writes per second on one core, eight hundred ninety-seven nanosecond cache latency with full I O M M U, and zero transmit coherence tax."], // 1
  [25.00, 42.56, "**2 · DirectPush interface**: small push + **native NIC DMA** (**0 DSA**) + **dual credits** → **14,700×** HoL isolation & **1.5–2.1×** lower CPU vs fair SDP.", "Contribution two: DirectPush pairs small inline push with native NIC D M A, uses zero Intel D S A, and adds dual credit windows—delivering fourteen thousand seven hundred times head-of-line isolation and up to two point one times lower host C P U than fair tuned S D P."], // 2
  [42.91, 53.37, "**3 · Real 100GbE proof**: **−4.25 µs** (**−42%**) two-host RTT, and **1 single host core** saturating **99.8 Gbps** end-to-end into ConnectX-5.", "Contribution three: real hundred gigabit validation—cutting two-host latency forty-two percent and driving ninety-nine point eight gigabits per second from strictly one host core."], // 3
  [53.72, 67.38, "**4 · Active-NIC burst isolation**: holding bursts on the NIC behind a **256 KiB** hot host ring cuts host DRAM **−98.9%** at **100% DDIO**.", "Contribution four: active-NIC burst isolation—keeping receive bursts on the NIC behind a two hundred fifty-six kilobyte hot host ring cuts host D RAM traffic ninety-eight point nine percent while keeping host caches clean."], // 4
  [67.73, 80.43, "**One-sentence takeaway**: fast NICs do **not** need cache coherence—they need **cache-aware PCIe**, **native DMA**, **credit pacing**, and an **active NIC**.", "Here is our one-sentence paper takeaway: high-speed network cards do not need hardware cache coherence. Cache-friendly P C I express, built-in D M A, credit pacing, and an active NIC already get us there today."], // 5
];
const DUR = 81.83; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const CHAIN_NODES = [
  { num: "Q1", title: "Plain PCIe", sub: "175.2 M/s · 897 ns", color: CK.push },
  { num: "Q2", title: "TX Reuse", sub: "0% coherence tax", color: CK.push },
  { num: "Q3", title: "Pacing", sub: "Dual credit windows", color: CK.cxl },
  { num: "Q4", title: "DirectPush", sub: "Push + native DMA", color: CK.dma },
  { num: "Q5", title: "100GbE", sub: "99.8 Gbps · 1 core", color: CK.ok },
  { num: "Q6", title: "Active NIC", sub: "−98.9% host DRAM", color: CK.card },
];

const RECIPE_PILLS = [
  { label: "Cache-Aware PCIe", color: CK.push },
  { label: "Native NIC DMA", color: CK.dma },
  { label: "Credit Pacing", color: CK.cxl },
  { label: "Active NIC", color: CK.ok },
];

function Body() {
  const t = useT();
  const rt = useRealT();
  const pulse = 0.5 + 0.5 * Math.sin(rt * 3.6);

  // Cue 0: framing + 6-node causal chain
  const k0 = fade(t, T[0], T[1] - 0.1, 0.45);

  // Cues 1..5: progressive 4-Contribution stack + bottom keynote banner
  const kStack = fade(t, T[1] + 0.15, DUR + 5, 0.45);
  const kC1 = prog(t, T[1] + 0.15, 0.5);
  const kC2 = prog(t, T[2], 0.5);
  const kC3 = prog(t, T[3], 0.5);
  const kC4 = prog(t, T[4], 0.5);
  const kKeynote = prog(t, T[5], 0.6);

  // Gently dim the 4 rows when the bottom centerpiece banner reveals
  const rowDim = lerp(1, 0.68, prog(t, T[5], 0.55));

  // Chain geometry (6 nodes across x=85..1835)
  const nodeW = 248;
  const nodeH = 190;
  const nodeGap = 52;
  const startX = 86;
  const chainY = 445;
  const chainPath: Pt[] = [
    [startX + nodeW / 2, chainY + nodeH / 2],
    [startX + 5 * (nodeW + nodeGap) + nodeW / 2, chainY + nodeH / 2],
  ];
  const packetK = (rt * 0.22) % 1;

  return (
    <>
      <Stage>
        {/* ===================== CUE 0: 6-NODE CAUSAL CHAIN ===================== */}
        {k0 > 0.005 && (
          <g opacity={k0}>
            {/* Subtle connecting track behind nodes */}
            <line
              x1={startX + nodeW / 2}
              y1={chainY + nodeH / 2}
              x2={startX + 5 * (nodeW + nodeGap) + nodeW / 2}
              y2={chainY + nodeH / 2}
              stroke={C.line}
              strokeWidth={4}
            />

            {/* Connecting arrows between nodes */}
            {CHAIN_NODES.slice(0, -1).map((node, i) => {
              const lit = prog(t, T[0] + 0.8 + i * 1.05, 0.55);
              const x1 = startX + i * (nodeW + nodeGap) + nodeW + 4;
              const x2 = x1 + nodeGap - 10;
              const cy = chainY + nodeH / 2;
              return (
                <g key={i} opacity={0.2 + 0.8 * lit}>
                  <line x1={x1} y1={cy} x2={x2} y2={cy} stroke={node.color} strokeWidth={4} />
                  <polygon points={`${x2 - 10},${cy - 8} ${x2 - 10},${cy + 8} ${x2 + 4},${cy}`} fill={node.color} />
                </g>
              );
            })}

            {/* Ambient traveling signal along the chain */}
            <Packet pts={chainPath} k={packetK} color={CK.push} trail={false} r={8} />

            {/* 6 Causal Nodes lighting up sequentially left-to-right */}
            {CHAIN_NODES.map((node, i) => {
              const lit = prog(t, T[0] + 0.5 + i * 1.05, 0.55);
              const nx = startX + i * (nodeW + nodeGap);
              const ny = chainY + lerp(18, 0, lit);
              return (
                <g key={node.num} opacity={0.18 + 0.82 * lit}>
                  <rect
                    x={nx}
                    y={ny}
                    width={nodeW}
                    height={nodeH}
                    rx={18}
                    fill="#141b2e"
                    stroke={node.color}
                    strokeWidth={1.5 + 1.5 * lit}
                  />
                  {/* Top badge */}
                  <rect x={nx + 20} y={ny + 18} width={64} height={34} rx={8} fill={node.color} />
                  <text x={nx + 52} y={ny + 41} textAnchor="middle" fontSize={19} fontWeight={800} fill={C.bg} fontFamily={MONO}>
                    {node.num}
                  </text>
                  {/* Step title */}
                  <text x={nx + 20} y={ny + 104} fontSize={28} fontWeight={800} fill={C.text} fontFamily={FONT}>
                    {node.title}
                  </text>
                  {/* Verified metric */}
                  <text x={nx + 20} y={ny + 148} fontSize={19} fontWeight={700} fill={node.color} fontFamily={MONO}>
                    {node.sub}
                  </text>
                </g>
              );
            })}

            {/* Bottom synthesis note */}
            <rect x={460} y={710} width={1000} height={58} rx={14} fill="rgba(20,27,46,0.9)" stroke={C.line} strokeWidth={1.5} />
            <text x={960} y={747} textAnchor="middle" fontSize={23} fontWeight={600} fill={C.muted} fontFamily={FONT}>
              One empirical assumption tested → six verified system building blocks
            </text>
          </g>
        )}
      </Stage>

      {/* ===================== HTML OVERLAYS (OUTSIDE <Stage>) ===================== */}

      {/* Cue 0: Advisor Question Opening Banner */}
      {k0 > 0.005 && (
        <QuestionBanner
          num="SYNTHESIS"
          question="So what are the paper's core contributions?"
          sub="From Challenging One Assumption to a Complete System Design"
          color={CK.push}
          y={165}
          opacity={k0}
          dy={lerp(20, 0, prog(t, T[0], 0.55))}
        />
      )}

      {/* Cues 1..5: Progressive 4-Contribution Stack */}
      {kStack > 0.005 && (
        <>
          <VerdictRow
            x={100}
            y={144}
            w={1720}
            badge="CONTRIB 1"
            badgeColor={CK.push}
            title="Refuting Slow PCIe (No Coherence Needed)"
            detail="175.2 M/s (97.9% line rate) · 897 ns 3-hop cache RTT w/ IOMMU · 0% TX coherence tax"
            opacity={kStack * kC1 * rowDim}
            dy={lerp(22, 0, kC1)}
          />

          <VerdictRow
            x={100}
            y={270}
            w={1720}
            badge="CONTRIB 2"
            badgeColor={CK.dma}
            title="DirectPush Hybrid Interface (Zero Intel DSA)"
            detail="Small push + native NIC DMA + dual credits → 14,700× HoL isolation & 1.5–2.1× less CPU vs fair SDP"
            opacity={kStack * kC2 * rowDim}
            dy={lerp(22, 0, kC2)}
          />

          <VerdictRow
            x={100}
            y={396}
            w={1720}
            badge="CONTRIB 3"
            badgeColor={CK.ok}
            title="End-to-End Real 100GbE Validation"
            detail="−4.25 µs (−42%) two-host RTT · 1 single host core saturates 99.8 Gbps into ConnectX-5"
            opacity={kStack * kC3 * rowDim}
            dy={lerp(22, 0, kC3)}
          />

          <VerdictRow
            x={100}
            y={522}
            w={1720}
            badge="CONTRIB 4"
            badgeColor={CK.cxl}
            title="Active-NIC RX Burst Isolation"
            detail="Backlog stays on NIC behind a 256 KiB hot host ring → −98.9% host DRAM & 100% DDIO"
            opacity={kStack * kC4 * rowDim}
            dy={lerp(22, 0, kC4)}
          />

          {/* Cue 5: Bottom Centerpiece Keynote Takeaway Banner */}
          {kKeynote > 0.005 && (
            <div
              style={{
                position: "absolute",
                left: 100,
                top: 662,
                width: 1720,
                height: 196,
                opacity: kKeynote,
                transform: `translateY(${lerp(26, 0, kKeynote)}px)`,
                background: "linear-gradient(135deg, rgba(16,32,52,0.97), rgba(11,22,36,0.96))",
                border: `2.5px solid ${CK.push}`,
                borderRadius: 22,
                padding: "24px 36px",
                boxSizing: "border-box",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: 22,
                boxShadow: `0 18px 48px rgba(0,0,0,0.55), 0 0 ${24 + 14 * pulse}px rgba(56,189,248,${0.22 + 0.14 * pulse})`,
              }}
            >
              <div
                style={{
                  fontSize: 44,
                  fontWeight: 800,
                  color: C.text,
                  fontFamily: FONT,
                  letterSpacing: -0.4,
                  textAlign: "center",
                  lineHeight: 1.15,
                }}
              >
                &ldquo;CC-NIC Does Not Need Cache Coherence&rdquo;
              </div>

              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 22 }}>
                {RECIPE_PILLS.map((p, idx) => (
                  <React.Fragment key={p.label}>
                    <span
                      style={{
                        fontSize: 22,
                        fontWeight: 800,
                        color: p.color,
                        fontFamily: MONO,
                        background: `${p.color}1c`,
                        border: `2px solid ${p.color}`,
                        borderRadius: 999,
                        padding: "9px 24px",
                        boxShadow: `0 0 18px ${p.color}33`,
                        whiteSpace: "nowrap",
                      }}
                    >
                      {p.label}
                    </span>
                    {idx < RECIPE_PILLS.length - 1 && (
                      <span style={{ fontSize: 24, fontWeight: 800, color: C.muted }}>·</span>
                    )}
                  </React.Fragment>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}

export const C08: SceneDef = {
  id: "C08",
  title: "7 \u00b7 Paper Contributions & Recipe",
  stage: 6,
  source: "Synthesis: Cache-Aware PCIe + Native DMA + Credits + Active NIC",
  dur: DUR,
  cues,
  C: Body,
};
