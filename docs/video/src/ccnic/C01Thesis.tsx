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
  prog,
  useRealT,
  useT,
} from "../lib";
import { CK, HeroStat, TestbedTopo, topoGeom, topoRoutes } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 15.40, "**Why revisit CC-NIC's premise?** ASPLOS '24 argued PCIe MMIO is too slow, so future NICs must move to **cache-coherent CXL**.", "Why revisit C C NIC's premise? At A S P L O S twenty twenty four, C C NIC made a bold claim: ordinary P C I express M M I O is too slow, so future network cards must move to cache-coherent C X L."], // 0
  [15.75, 29.39, "Our thesis is the opposite: **fast NICs need zero hardware cache coherence**—and **zero** special CPU accelerators like Intel DSA.", "Our paper argues the opposite. Fast network cards need zero hardware cache coherence, and zero special C P U accelerators like Intel D S A—plain commercial P C I express is already enough when designed properly."], // 1
  [29.74, 45.10, "Real off-the-shelf testbed: Xeon host **saturn**, **PCIe Gen5 ×4** Marvell **CN106** DPU with full **IOMMU**, and **100GbE** to **mercury**.", "Instead of emulating a NIC on a second C P U socket, we measure real commercial silicon: Xeon host saturn, a P C I express gen five by four Marvell C N one oh six with hardware I O M M U on, and hundred gigabit Ethernet to mercury."], // 2
  [45.45, 59.15, "**3,500+ zero-error runs** (**>2.5 B** frames): **0.9 µs** RTT, **1-core 100GbE**, **0%** TX coherence tax, **−98.9%** burst DRAM.", "Across thirty-five hundred zero-error runs and two point five billion frames: sub-microsecond latency, one-core hundred gigabit line rate, zero transmit coherence tax, and ninety-eight point nine percent less burst D RAM."], // 3
  [59.50, 72.04, "Four principles drive the gain: **cache-aware PCIe push**, **native NIC DMA**, **credit pacing**, and an **active NIC** for bursts.", "Four simple principles drive everything: cache-aware P C I express push, native NIC D M A for large payloads, lightweight credit pacing, and an active NIC that shields host caches during receive bursts."], // 4
  [72.39, 81.63, "Next, we follow **six natural advisor questions**—each measurement directly motivating the next piece of the design.", "Next, we walk through six natural questions a systems advisor would ask step by step, letting each hardware measurement directly motivate the next piece of the design."], // 5
];
const DUR = 83.03; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const PILLARS = [
  {
    num: "1.",
    title: "Cache-Aware PCIe Push",
    desc: "Unfenced direct writes saturate PCIe Gen5 ×4 and stay in on-chip caches on the fast path.",
    color: CK.push,
  },
  {
    num: "2.",
    title: "Native NIC DMA",
    desc: "Keep large payloads on the NIC's built-in DMA engine — zero CPU copies and up to 97% line rate.",
    color: CK.dma,
  },
  {
    num: "3.",
    title: "Credit Pacing",
    desc: "Bound in-flight work to match cache capacity — eliminating contention cliffs and cache thrashing.",
    color: CK.cxl,
  },
  {
    num: "4.",
    title: "Active NIC for Bursts",
    desc: "Absorb RX bursts in card memory and feed a tiny hot host ring — cutting host burst DRAM (−98.9%).",
    color: CK.card,
  },
];

const ROADMAP = [
  { q: "Q1", title: "Isn't plain PCIe slow?", sub: "Throughput & 3-hop cache latency", color: CK.push },
  { q: "Q2", title: "What about TCP TX buffers?", sub: "Copy-by-value push · 0% TX coherence tax", color: CK.dma },
  { q: "Q3", title: "What breaks at high concurrency?", sub: "Contention cliffs & credit pacing", color: CK.trap },
  { q: "Q4", title: "How to handle 4 KiB fairly?", sub: "Hybrid direct push + native DMA", color: CK.cxl },
  { q: "Q5", title: "Survives real 100GbE?", sub: "1-core end-to-end line rate", color: CK.ok },
  { q: "Q6", title: "What about 100 M/s RX bursts?", sub: "Active NIC burst absorption", color: CK.card },
];

function Body() {
  const t = useT();
  const realT = useRealT();

  // Beat opacities (non-overlapping sequential fades)
  const opIntro = fade(t, T[0], T[2] - 0.1, 0.5);
  const kThesis = prog(t, T[1], 0.65);
  const opTopo = fade(t, T[2] + 0.15, T[3] - 0.1, 0.5);
  const opKpi = fade(t, T[3] + 0.15, T[4] - 0.1, 0.5);
  const opPillars = fade(t, T[4] + 0.15, T[5] - 0.1, 0.5);
  const opRoadmap = fade(t, T[5] + 0.15, 1e9, 0.5);

  // Cue 0 -> Cue 1 smooth vertical shift
  const pillY = lerp(130, 82, kThesis);
  const titleY = lerp(218, 146, kThesis);
  const claimY = lerp(388, 250, kThesis);
  const claimOp = lerp(1, 0.62, kThesis);

  // Cue 2 testbed geometry
  const gTopo = topoGeom(0.95, 25, 110);
  const routes = topoRoutes(gTopo);

  // Cue 3 KPI count-ups (finish within 1.2s)
  const rttVal = countUp(t, T[3] + 0.2, 1.1, 0.9, 1);
  const dramVal = countUp(t, T[3] + 0.35, 1.15, 98.9, 1);

  return (
    <>
      <Stage>
        {/* Subtle ambient header glow for Intro */}
        {opIntro > 0.01 && (
          <g opacity={opIntro}>
            <ellipse cx={960} cy={220} rx={680} ry={180} fill={CK.push} opacity={0.055} />
            <line
              x1={660}
              y1={lerp(346, 224, kThesis)}
              x2={1260}
              y2={lerp(346, 224, kThesis)}
              stroke={CK.push}
              strokeWidth={2}
              opacity={0.45}
            />
          </g>
        )}

        {/* Cue 2: Testbed Topology + Live Packet Streams */}
        {opTopo > 0.01 && (
          <g opacity={opTopo}>
            <TestbedTopo
              g={gTopo}
              showPeer={true}
              hl={{
                saturn: CK.host,
                pcie: CK.push,
                cn106: CK.card,
                wire: CK.peer,
                mercury: CK.peer,
              }}
              labels={{
                cpu: ["Host Core", "Direct push · L1"],
                pem: ["PCIe Endpoint", "Direct push + WC"],
                mercury: ["PEER · mercury", "ConnectX-5"],
              }}
            />
            <PacketStream pts={routes.pushH2C} travel={realT * 185} gap={78} size={11} color={CK.push} opacity={0.95} />
            <PacketStream pts={routes.wcC2H} travel={realT * 165} gap={86} size={10} color={CK.ok} opacity={0.9} />
            <PacketStream pts={routes.wireTx} travel={realT * 170} gap={52} size={11} color={CK.peer} opacity={0.95} />
            <PacketStream pts={routes.wireRx} travel={realT * 150} gap={58} size={10} color={CK.card} opacity={0.9} />
          </g>
        )}

        {/* Cue 5: Roadmap Connecting Arrows */}
        {opRoadmap > 0.01 && (
          <g opacity={opRoadmap}>
            {/* Row 1 horizontal connectors: Q1->Q2, Q2->Q3 */}
            {[
              [642, 410, 690, 410, CK.push],
              [1232, 410, 1280, 410, CK.dma],
              [642, 675, 690, 675, CK.cxl],
              [1232, 675, 1280, 675, CK.ok],
            ].map(([x1, y1, x2, y2, col], idx) => {
              const kk = prog(t, T[5] + 0.35 + idx * 0.14, 0.45);
              return (
                <g key={idx} opacity={kk}>
                  <line x1={x1 as number} y1={y1 as number} x2={(x2 as number) - 10} y2={y2 as number} stroke={col as string} strokeWidth={4} strokeLinecap="round" />
                  <polygon
                    points={`${x2},${y2} ${(x2 as number) - 14},${(y2 as number) - 8} ${(x2 as number) - 14},${(y2 as number) + 8}`}
                    fill={col as string}
                  />
                </g>
              );
            })}
            {/* Subtle return elbow from Q3 down to Q4 */}
            <path
              d="M 1550 518 L 1550 542 L 370 542 L 370 562"
              fill="none"
              stroke={C.line}
              strokeWidth={3}
              strokeDasharray="8 6"
              opacity={prog(t, T[5] + 0.6, 0.5) * 0.85}
            />
            <polygon
              points="370,568 362,554 378,554"
              fill={C.muted}
              opacity={prog(t, T[5] + 0.6, 0.5) * 0.85}
            />
          </g>
        )}
      </Stage>

      {/* ==================== CUE 0 & CUE 1: TITLE + COUNTER-THESIS ==================== */}
      {opIntro > 0.01 && (
        <div style={{ position: "absolute", inset: 0, opacity: opIntro, pointerEvents: "none" }}>
          {/* Top presentation pill */}
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: pillY,
              display: "flex",
              justifyContent: "center",
            }}
          >
            <span
              style={{
                fontSize: 20,
                fontWeight: 800,
                letterSpacing: 2.2,
                color: CK.push,
                fontFamily: MONO,
                background: `${CK.push}18`,
                border: `1.5px solid ${CK.push}88`,
                borderRadius: 999,
                padding: "8px 26px",
              }}
            >
              SYSTEMS RESEARCH PRESENTATION
            </span>
          </div>

          {/* Main bold title */}
          <div
            style={{
              position: "absolute",
              left: 80,
              right: 80,
              top: titleY,
              textAlign: "center",
              fontSize: 58,
              fontWeight: 800,
              color: C.text,
              fontFamily: FONT,
              letterSpacing: -0.8,
              lineHeight: 1.15,
            }}
          >
            CC-NIC Does Not Need Cache Coherence
          </div>

          {/* Cue 0 advisor opening-hook question */}
          {kThesis < 0.85 && (
            <div
              style={{
                position: "absolute",
                left: 80,
                right: 80,
                top: lerp(296, 220, kThesis),
                textAlign: "center",
                fontSize: 27,
                fontWeight: 700,
                color: CK.push,
                fontFamily: FONT,
                opacity: clamp01(1 - kThesis * 1.8) * prog(t, T[0] + 0.2, 0.5),
              }}
            >
              Why revisit CC-NIC&apos;s premise?
            </div>
          )}
          {/* Conventional claim box (ASPLOS '24 CC-NIC) */}
          <div
            style={{
              position: "absolute",
              left: 260,
              top: claimY,
              width: 1400,
              opacity: claimOp * prog(t, T[0] + 0.35, 0.6),
              background: "linear-gradient(135deg, rgba(20,27,46,0.96), rgba(11,16,32,0.92))",
              border: `2px solid ${CK.cxl}88`,
              borderLeft: `10px solid ${CK.cxl}`,
              borderRadius: 20,
              padding: kThesis > 0.5 ? "18px 30px" : "28px 36px",
              boxSizing: "border-box",
              boxShadow: "0 16px 40px rgba(0,0,0,0.45)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
              <span
                style={{
                  fontSize: 20,
                  fontWeight: 800,
                  color: C.bg,
                  background: CK.cxl,
                  borderRadius: 9,
                  padding: "6px 16px",
                  fontFamily: MONO,
                  whiteSpace: "nowrap",
                }}
              >
                ASPLOS &apos;24 · CC-NIC
              </span>
              <span style={{ fontSize: 30, fontWeight: 700, color: C.text, lineHeight: 1.25 }}>
                PCIe MMIO is too slow → Move NICs to CXL cache coherence
              </span>
            </div>
            {kThesis < 0.75 && (
              <div
                style={{
                  fontSize: 22,
                  color: C.muted,
                  marginTop: 14,
                  marginLeft: 4,
                  opacity: clamp01(1 - kThesis * 1.6),
                }}
              >
                Assumes uncached PCIe writes stall and force every message through slow DRAM.
              </div>
            )}
          </div>

          {/* Cue 1 reveal: Two crossed-out pills + bold thesis banner */}
          {kThesis > 0.01 && (
            <div
              style={{
                position: "absolute",
                left: 0,
                right: 0,
                top: 405,
                opacity: kThesis,
                transform: `translateY(${(1 - kThesis) * 28}px)`,
              }}
            >
              {/* Crossed-out pills */}
              <div style={{ display: "flex", justifyContent: "center", gap: 38 }}>
                {[
                  { text: "Hardware Cache Coherence", color: CK.trap },
                  { text: "Intel DSA Accelerator", color: CK.cxl },
                ].map((item, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 16,
                      background: `${item.color}18`,
                      border: `2px solid ${item.color}`,
                      borderRadius: 999,
                      padding: "12px 30px",
                      boxShadow: `0 8px 24px ${item.color}22`,
                    }}
                  >
                    <span
                      style={{
                        fontSize: 28,
                        fontWeight: 700,
                        color: C.text,
                        textDecoration: "line-through",
                        textDecorationColor: item.color,
                        textDecorationThickness: 3,
                      }}
                    >
                      {item.text}
                    </span>
                    <span
                      style={{
                        fontSize: 28,
                        fontWeight: 900,
                        color: item.color,
                        fontFamily: MONO,
                      }}
                    >
                      ✗
                    </span>
                  </div>
                ))}
              </div>

              {/* Glowing counter-thesis banner */}
              <div
                style={{
                  margin: "38px auto 0",
                  width: 1520,
                  background: "linear-gradient(135deg, rgba(16,44,58,0.96), rgba(14,34,40,0.95))",
                  border: `2.5px solid ${CK.ok}`,
                  borderRadius: 24,
                  padding: "32px 44px",
                  boxSizing: "border-box",
                  textAlign: "center",
                  boxShadow: `0 0 42px ${CK.push}28, 0 18px 46px rgba(0,0,0,0.55)`,
                }}
              >
                <div
                  style={{
                    display: "inline-block",
                    fontSize: 19,
                    fontWeight: 800,
                    letterSpacing: 1.8,
                    color: C.bg,
                    background: CK.ok,
                    borderRadius: 8,
                    padding: "5px 16px",
                    fontFamily: MONO,
                    marginBottom: 16,
                  }}
                >
                  OUR THESIS
                </div>
                <div
                  style={{
                    fontSize: 40,
                    fontWeight: 800,
                    color: C.text,
                    lineHeight: 1.22,
                    letterSpacing: -0.4,
                  }}
                >
                  Plain Commercial PCIe Is Already Enough — When Designed Properly
                </div>
                <div
                  style={{
                    fontSize: 24,
                    color: CK.push,
                    marginTop: 14,
                    fontWeight: 600,
                  }}
                >
                  Zero hardware cache coherence · Zero CPU accelerators · Real off-the-shelf silicon
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ==================== CUE 2: TESTBED HEADER & FOOTER ==================== */}
      {opTopo > 0.01 && (
        <div style={{ position: "absolute", inset: 0, opacity: opTopo, pointerEvents: "none" }}>
          <div
            style={{
              position: "absolute",
              left: 80,
              right: 80,
              top: 86,
              textAlign: "center",
            }}
          >
            <div style={{ fontSize: 40, fontWeight: 800, color: C.text, letterSpacing: -0.4 }}>
              Off-the-Shelf Hardware Testbed
            </div>
            <div style={{ fontSize: 23, color: CK.peer, marginTop: 8, fontWeight: 600 }}>
              Real Commercial Silicon — Not a 2-Socket CPU Emulator
            </div>
          </div>

          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: 794,
              display: "flex",
              justifyContent: "center",
              gap: 28,
            }}
          >
            {[
              { label: "PCIe Gen5 ×4 · 15.75 GB/s", color: CK.push },
              { label: "HW IOMMU Always On", color: CK.ok },
              { label: "3 m 100GbE Physical Cable", color: CK.peer },
            ].map((chip, i) => (
              <span
                key={i}
                style={{
                  fontSize: 21,
                  fontWeight: 700,
                  color: chip.color,
                  fontFamily: MONO,
                  background: `${chip.color}16`,
                  border: `1.5px solid ${chip.color}88`,
                  borderRadius: 999,
                  padding: "9px 24px",
                }}
              >
                {chip.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ==================== CUE 3: HERO KPI ROW ==================== */}
      {opKpi > 0.01 && (
        <div style={{ position: "absolute", inset: 0, opacity: opKpi, pointerEvents: "none" }}>
          {/* Top verification pill */}
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: 132,
              display: "flex",
              justifyContent: "center",
            }}
          >
            <span
              style={{
                fontSize: 24,
                fontWeight: 800,
                color: CK.ok,
                fontFamily: MONO,
                background: `${CK.ok}1a`,
                border: `2px solid ${CK.ok}`,
                borderRadius: 999,
                padding: "11px 34px",
                boxShadow: `0 0 28px ${CK.ok}28`,
              }}
            >
              3,500+ Verified Runs  ·  &gt;2.5 B Frames  ·  0 Errors
            </span>
          </div>

          <div
            style={{
              position: "absolute",
              left: 80,
              right: 80,
              top: 228,
              textAlign: "center",
              fontSize: 42,
              fontWeight: 800,
              color: C.text,
              letterSpacing: -0.5,
            }}
          >
            Headline Results on Plain Commercial PCIe
          </div>

          {/* 4 HeroStat cards */}
          <HeroStat
            x={92}
            y={334}
            w={410}
            h={240}
            value={`${rttVal} µs`}
            label="Sub-µs PCIe RTT"
            sub="with HW IOMMU"
            color={CK.push}
            opacity={prog(t, T[3] + 0.15, 0.5)}
            dy={(1 - prog(t, T[3] + 0.15, 0.5)) * 24}
          />
          <HeroStat
            x={534}
            y={334}
            w={410}
            h={240}
            value="1 Core"
            label="Full 100GbE Line Rate"
            sub="99.8 Gbps end-to-end"
            color={CK.ok}
            opacity={prog(t, T[3] + 0.3, 0.5)}
            dy={(1 - prog(t, T[3] + 0.3, 0.5)) * 24}
          />
          <HeroStat
            x={976}
            y={334}
            w={410}
            h={240}
            value="0%"
            label="TX Coherence Tax"
            sub="128 MiB retention pool"
            color={CK.dma}
            opacity={prog(t, T[3] + 0.45, 0.5)}
            dy={(1 - prog(t, T[3] + 0.45, 0.5)) * 24}
          />
          <HeroStat
            x={1418}
            y={334}
            w={410}
            h={240}
            value={`−${dramVal}%`}
            label="Host Burst DRAM"
            sub="256 KiB hot ring"
            color={CK.card}
            opacity={prog(t, T[3] + 0.6, 0.5)}
            dy={(1 - prog(t, T[3] + 0.6, 0.5)) * 24}
          />

          {/* Bottom executive strip */}
          <div
            style={{
              position: "absolute",
              left: 220,
              top: 650,
              width: 1480,
              textAlign: "center",
              background: "rgba(20,27,46,0.9)",
              border: `1.5px solid ${C.line}`,
              borderRadius: 16,
              padding: "20px 32px",
              boxSizing: "border-box",
              fontSize: 25,
              fontWeight: 600,
              color: C.muted,
            }}
          >
            All achieved on commercial <span style={{ color: C.text, fontWeight: 700 }}>PCIe Gen5 ×4</span> with{" "}
            <span style={{ color: CK.ok, fontWeight: 700 }}>0 errors</span> — no CXL coherence and no CPU accelerators.
          </div>
        </div>
      )}

      {/* ==================== CUE 4: 4 PRINCIPLES (2x2 GRID) ==================== */}
      {opPillars > 0.01 && (
        <div style={{ position: "absolute", inset: 0, opacity: opPillars, pointerEvents: "none" }}>
          <div style={{ position: "absolute", left: 0, right: 0, top: 100, display: "flex", justifyContent: "center" }}>
            <span
              style={{
                fontSize: 20,
                fontWeight: 800,
                letterSpacing: 1.8,
                color: CK.push,
                fontFamily: MONO,
                background: `${CK.push}18`,
                border: `1.5px solid ${CK.push}88`,
                borderRadius: 999,
                padding: "7px 24px",
              }}
            >
              4 PRINCIPLES
            </span>
          </div>
          <div
            style={{
              position: "absolute",
              left: 80,
              right: 80,
              top: 158,
              textAlign: "center",
              fontSize: 44,
              fontWeight: 800,
              color: C.text,
              letterSpacing: -0.5,
            }}
          >
            Four Principles Behind the Gains
          </div>

          {PILLARS.map((p, i) => {
            const col = i % 2;
            const row = Math.floor(i / 2);
            const x = 100 + col * 885;
            const y = 262 + row * 285;
            const k = prog(t, T[4] + 0.15 + i * 0.2, 0.5);
            return (
              <div
                key={p.num}
                style={{
                  position: "absolute",
                  left: x,
                  top: y,
                  width: 835,
                  height: 242,
                  opacity: k,
                  transform: `translateY(${(1 - k) * 24}px)`,
                  background: "linear-gradient(145deg, rgba(20,27,46,0.96), rgba(11,16,32,0.94))",
                  border: `2px solid ${p.color}88`,
                  borderLeft: `10px solid ${p.color}`,
                  borderRadius: 20,
                  padding: "28px 34px",
                  boxSizing: "border-box",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "center",
                  boxShadow: "0 14px 36px rgba(0,0,0,0.45)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
                  <span
                    style={{
                      fontSize: 22,
                      fontWeight: 800,
                      color: C.bg,
                      background: p.color,
                      borderRadius: 10,
                      padding: "6px 14px",
                      fontFamily: MONO,
                    }}
                  >
                    {p.num}
                  </span>
                  <span style={{ fontSize: 32, fontWeight: 800, color: C.text }}>{p.title}</span>
                </div>
                <div style={{ fontSize: 23, color: C.muted, marginTop: 18, lineHeight: 1.4 }}>{p.desc}</div>
              </div>
            );
          })}
        </div>
      )}

      {/* ==================== CUE 5: 6-STEP ADVISOR ROADMAP ==================== */}
      {opRoadmap > 0.01 && (
        <div style={{ position: "absolute", inset: 0, opacity: opRoadmap, pointerEvents: "none" }}>
          <div style={{ position: "absolute", left: 0, right: 0, top: 96, display: "flex", justifyContent: "center" }}>
            <span
              style={{
                fontSize: 20,
                fontWeight: 800,
                letterSpacing: 1.8,
                color: CK.ok,
                fontFamily: MONO,
                background: `${CK.ok}18`,
                border: `1.5px solid ${CK.ok}88`,
                borderRadius: 999,
                padding: "7px 24px",
              }}
            >
              6 QUESTIONS
            </span>
          </div>
          <div
            style={{
              position: "absolute",
              left: 80,
              right: 80,
              top: 152,
              textAlign: "center",
              fontSize: 44,
              fontWeight: 800,
              color: C.text,
              letterSpacing: -0.5,
            }}
          >
            Roadmap: Six Advisor Questions
          </div>
          <div
            style={{
              position: "absolute",
              left: 80,
              right: 80,
              top: 214,
              textAlign: "center",
              fontSize: 23,
              color: C.muted,
            }}
          >
            Each hardware measurement directly motivates the next piece of the design.
          </div>

          {ROADMAP.map((item, i) => {
            const col = i % 3;
            const row = Math.floor(i / 3);
            const x = 100 + col * 590;
            const y = 304 + row * 265;
            const k = prog(t, T[5] + 0.15 + i * 0.14, 0.48);
            return (
              <div
                key={item.q}
                style={{
                  position: "absolute",
                  left: x,
                  top: y,
                  width: 540,
                  height: 212,
                  opacity: k,
                  transform: `translateY(${(1 - k) * 20}px)`,
                  background: "linear-gradient(155deg, rgba(20,27,46,0.96), rgba(11,16,32,0.94))",
                  border: `2px solid ${item.color}99`,
                  borderTop: `6px solid ${item.color}`,
                  borderRadius: 18,
                  padding: "22px 26px",
                  boxSizing: "border-box",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "center",
                  boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                  <span
                    style={{
                      fontSize: 20,
                      fontWeight: 800,
                      color: C.bg,
                      background: item.color,
                      borderRadius: 8,
                      padding: "4px 12px",
                      fontFamily: MONO,
                    }}
                  >
                    {item.q}
                  </span>
                  <span style={{ fontSize: 18, fontWeight: 700, color: item.color, fontFamily: MONO }}>
                    STEP {i + 1} / 6
                  </span>
                </div>
                <div style={{ fontSize: 26, fontWeight: 800, color: C.text, marginTop: 14, lineHeight: 1.22 }}>
                  {item.title}
                </div>
                <div style={{ fontSize: 20, color: C.muted, marginTop: 8, lineHeight: 1.3 }}>{item.sub}</div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

export const C01: SceneDef = {
  id: "C01",
  title: "",
  source: "",
  dur: DUR,
  cues,
  C: Body,
};
