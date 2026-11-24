import React from "react";
import {
  Arrow,
  C,
  Card,
  Cue,
  MONO,
  PacketStream,
  SceneDef,
  Stage,
  Txt,
  clamp01,
  countUp,
  fade,
  lerp,
  pop,
  prog,
  useRealT,
  useT,
} from "../lib";
import { CK, HeroStat, TestbedTopo, TopoNodeId, topoGeom, topoRoutes } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 11.42, "ASPLOS '24 **CC-NIC** argued PCIe MMIO is too slow—and NICs must move to **cache-coherent** interconnects like CXL.", "A S P L O S twenty twenty-four C C NIC argued that ordinary P C I express M M I O is too slow, so NICs must move to cache-coherent C X L."], // 0
  [11.77, 20.91, "Our measurements find the opposite: **fast NICs need no hardware cache coherence**—and no special CPU accelerators like Intel DSA.", "Our measurements find the opposite. Fast NIC interfaces need no hardware cache coherence, and no special C P U accelerators like Intel D S A."], // 1
  [21.26, 34.52, "Testbed: Xeon **saturn**, commercial **PCIe Gen5 x4** Marvell **CN106**, full **IOMMU**, and a **3 m 100GbE** cable to **mercury**.", "Our testbed is off-the-shelf hardware: Xeon host saturn, a P C I express gen five by four Marvell C N one oh six with hardware I O M M U enabled, and a three meter hundred gigabit cable to mercury."], // 2
  [34.87, 48.05, "**3,500+ verified runs**, **>2.5 B frames**, zero errors: **0.9 µs** RTT, **1-core 100GbE**, **0% coherence tax**, **−98.9%** burst DRAM.", "Across thirty-five hundred zero-error runs and two point five billion frames: sub-microsecond latency, one-core hundred gigabit line rate, zero coherence tax, and ninety-eight point nine percent less burst D RAM."], // 3
  [48.40, 61.52, "Five-part story: **Premise** → **TX Buffers** → **Flow Control** → **DirectPush & Audit** → **Real 100GbE & Active NIC**.", "Here is the five-part story: refuting the slow P C I express premise, transmit buffer reuse, bridge flow control, DirectPush with a fair baseline audit, and real hundred gigabit Ethernet plus an active NIC."], // 4
];
const DUR = 62.92; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const ROADMAP: { num: string; title: string; sub: string; detail: string; color: string; at: number }[] = [
  { num: "1", title: "Premise", sub: "897 ns · 175 M/s", detail: "PCIe isn't slow + 2 DPU traps", color: CK.push, at: 1.0 },
  { num: "2", title: "TX Buffers", sub: "0% Coh. Tax", detail: "99.99993% DDIO · 128 MiB", color: CK.sdp, at: 3.0 },
  { num: "3", title: "Flow Control", sub: "Taming 13 M/s", detail: "Credits + busy-burst backoff", color: CK.card, at: 5.0 },
  { num: "4", title: "DirectPush", sub: "+ Fair Audit", detail: "Hybrid DMA + MRV-166 ledger", color: CK.dma, at: 7.2 },
  { num: "5", title: "Real 100GbE", sub: "+ Active NIC", detail: "99.8 Gbps · −98.9% DRAM", color: CK.ok, at: 9.6 },
];

function StrikeBadge({
  label,
  sub,
  kStrike,
}: {
  label: string;
  sub: string;
  kStrike: number;
}) {
  const s = clamp01(kStrike);
  return (
    <div
      style={{
        position: "relative",
        flex: 1,
        background: "rgba(249,65,68,0.10)",
        border: `2px solid ${s > 0.2 ? CK.trap : C.line}`,
        borderRadius: 14,
        padding: "14px 18px",
        overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
        <div>
          <div style={{ fontSize: 25, fontWeight: 800, color: s > 0.4 ? C.muted : C.text, fontFamily: MONO }}>
            {label}
          </div>
          <div style={{ fontSize: 18, color: C.muted, marginTop: 2 }}>{sub}</div>
        </div>
        <div
          style={{
            fontSize: 17,
            fontWeight: 800,
            fontFamily: MONO,
            color: CK.trap,
            background: "rgba(249,65,68,0.18)",
            border: `1.5px solid ${CK.trap}`,
            borderRadius: 999,
            padding: "4px 12px",
            opacity: s,
            transform: `scale(${lerp(0.75, 1, s)})`,
            whiteSpace: "nowrap",
          }}
        >
          SKIPPED
        </div>
      </div>
      {/* Animated red strike line across the label */}
      <div
        style={{
          position: "absolute",
          left: 14,
          top: 28,
          height: 4,
          width: `${Math.round(165 * s)}px`,
          background: CK.trap,
          borderRadius: 3,
          transform: "rotate(-4deg)",
          boxShadow: `0 0 10px ${CK.trap}aa`,
        }}
      />
    </div>
  );
}

function Body() {
  const t = useT();
  const rt = useRealT();

  // Header entrance & subtle compacting when topology shrinks into upper strip at T[3]
  const kTitle = prog(t, 0.15, 0.7);
  const kCompact = prog(t, T[3], 0.75);
  const titleSize = lerp(50, 40, kCompact);
  const titleY = lerp(50, 44, kCompact);
  const subOp = kTitle * (1 - prog(t, T[3] - 0.1, 0.5));

  // Cue 0..1 contrast cards (fade out at T[2])
  const kLeftCard = fade(t, T[0] + 0.1, T[2] - 0.15, 0.55);
  const kLeftIn = prog(t, T[0] + 0.1, 0.65);
  const kRightCard = fade(t, T[1] + 0.1, T[2] - 0.15, 0.55);
  const kRightIn = prog(t, T[1] + 0.1, 0.65);
  const kStrikeCxl = prog(t, T[1] + 2.4, 0.55);
  const kStrikeDsa = prog(t, T[1] + 5.0, 0.55);
  const kSlowBar = prog(t, T[0] + 2.2, 1.1);

  // Cue 2..4 evolving topology: full at T[2], morphs cleanly into upper strip at T[3]
  const kTopo = prog(t, T[2] + 0.1, 0.65);
  const mTopo = prog(t, T[3], 0.85);
  const topoScale = lerp(0.92, 0.62, mTopo);
  const topoDx = lerp(58, 350, mTopo);
  const topoDy = lerp(88, 62, mTopo);
  const g = topoGeom(topoScale, topoDx, topoDy);
  const r = topoRoutes(g);

  // Sequential highlights across hardware nodes during Cue 2
  const hSaturn = fade(t, T[2] + 0.6, T[2] + 3.6, 0.45);
  const hPcie = fade(t, T[2] + 3.1, T[2] + 5.8, 0.45);
  const hCn106 = fade(t, T[2] + 5.3, T[2] + 8.7, 0.45);
  const hWire = fade(t, T[2] + 8.2, T[2] + 10.9, 0.45);
  const hMercury = fade(t, T[2] + 10.4, T[3] - 0.1, 0.45);

  const topoHl: Partial<Record<TopoNodeId, string>> = {};
  if (hSaturn > 0.35) {
    topoHl.saturn = CK.host;
    topoHl.cpu = CK.push;
    topoHl.iommu = CK.cxl;
  }
  if (hPcie > 0.35) topoHl.pcie = CK.push;
  if (hCn106 > 0.35) {
    topoHl.cn106 = CK.card;
    topoHl.cL3 = CK.ok;
    topoHl.pem = CK.card;
  }
  if (hWire > 0.35) {
    topoHl.wire = CK.peer;
    topoHl.nix = CK.peer;
  }
  if (hMercury > 0.35) topoHl.mercury = CK.peer;

  // Cue 2 live callout ribbon beneath topology (fades out before T[3] morph)
  const kTopoCaps = fade(t, T[2] + 0.5, T[3] - 0.25, 0.45);

  // Cue 3 hero stats + provenance pill (crossfade out at T[4])
  const kStatsOut = 1 - prog(t, T[4] - 0.15, 0.5);
  const statAt = [0.6, 2.5, 4.4, 6.2];
  const statKs = statAt.map((off) => prog(t, T[3] + off, 0.55) * kStatsOut);
  const statDys = statAt.map((off) => lerp(22, 0, prog(t, T[3] + off, 0.55)));
  const kProv = fade(t, T[3] + 0.25, T[4] - 0.15, 0.5);

  const vRtt = t < T[3] + statAt[0] ? "0.90 µs" : `${countUp(t, T[3] + statAt[0], 1.1, 0.9, 2)} µs`;
  const vGbps = t < T[3] + statAt[1] ? "99.8 Gbps" : `${countUp(t, T[3] + statAt[1], 1.1, 99.8, 1)} Gbps`;
  const vDram = t < T[3] + statAt[3] ? "−98.9%" : `−${countUp(t, T[3] + statAt[3], 1.1, 98.9, 1)}%`;

  // Cue 4 5-step Story Roadmap
  const kRoadBase = prog(t, T[4] + 0.15, 0.55);
  const cardW = 332;
  const cardGap = 35;
  const roadX0 = 60;
  const roadY = 568;
  const roadH = 236;

  // Ambient slow-pulse for Cue 0 indicator
  const slowPulse = 0.5 + 0.5 * Math.sin(rt * 3.2);
  const slowPos = (rt * 0.22) % 1;

  return (
    <>
      <Stage>
        {/* Subtle decorative header rule */}
        <line
          x1={260}
          y1={lerp(172, 108, kCompact)}
          x2={1660}
          y2={lerp(172, 108, kCompact)}
          stroke={C.line}
          strokeWidth={1.5}
          opacity={kTitle * 0.65}
        />

        {/* Cue 0..1: VS connector badge between contrast cards */}
        {kRightCard > 0.01 && (
          <g opacity={kRightCard}>
            <circle cx={960} cy={432} r={26} fill={C.bg} stroke={CK.push} strokeWidth={2.5} />
            <text
              x={960}
              y={432}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={21}
              fontWeight={800}
              fill={CK.push}
              fontFamily={MONO}
            >
              VS
            </text>
          </g>
        )}

        {/* Cue 2..4: Live evolving 3-node hardware topology */}
        {kTopo > 0.01 && (
          <g opacity={kTopo}>
            <TestbedTopo g={g} showPeer={true} hl={topoHl} opacity={1} />
            {/* Live animated packet trains along PCIe & 100GbE */}
            <PacketStream
              pts={r.pushH2C}
              travel={rt * 195}
              gap={76 * topoScale}
              size={12 * topoScale}
              color={CK.push}
              opacity={0.92}
            />
            <PacketStream
              pts={r.wcC2H}
              travel={rt * 175}
              gap={86 * topoScale}
              size={11 * topoScale}
              color={CK.ok}
              opacity={0.88}
            />
            <PacketStream
              pts={r.wireTx}
              travel={rt * 165}
              gap={58 * topoScale}
              size={11 * topoScale}
              color={CK.peer}
              opacity={0.9}
            />
            <PacketStream
              pts={r.wireRx}
              travel={rt * 155}
              gap={64 * topoScale}
              size={10 * topoScale}
              color={CK.peer}
              opacity={0.72}
            />
          </g>
        )}

        {/* Cue 4: Connecting arrows between 5 Roadmap cards */}
        {kRoadBase > 0.01 &&
          ROADMAP.slice(0, 4).map((item, i) => {
            const x1 = roadX0 + (i + 1) * cardW + i * cardGap + 4;
            const x2 = x1 + cardGap - 8;
            const kArr = prog(t, T[4] + ROADMAP[i + 1].at - 0.25, 0.45);
            return (
              <Arrow
                key={item.num}
                x1={x1}
                y1={roadY + roadH / 2}
                x2={x2}
                y2={roadY + roadH / 2}
                color={kArr > 0.5 ? ROADMAP[i + 1].color : C.dim}
                w={3.5}
                head={11}
                k={kRoadBase}
                opacity={0.85}
              />
            );
          })}
      </Stage>

      {/* Top Title Banner (HTML, outside <Stage>) */}
      <Txt
        x={60}
        y={titleY}
        w={1800}
        size={titleSize}
        weight={800}
        align="center"
        color={C.text}
        style={{ opacity: kTitle, letterSpacing: -0.5 }}
      >
        CC-NIC Does Not Need Cache Coherence
      </Txt>
      {subOp > 0.01 && (
        <Txt
          x={60}
          y={118}
          w={1800}
          size={26}
          weight={600}
          align="center"
          color={CK.push}
          style={{ opacity: subOp, fontFamily: MONO }}
        >
          DirectPush: Pure Commercial PCIe Gen5 NIC Interface
        </Txt>
      )}

      {/* Cue 0: Left Contrast Card (ASPLOS '24 CC-NIC claim) */}
      {kLeftCard > 0.01 && (
        <Card
          x={60}
          y={220}
          w={870}
          h={425}
          border={CK.cxl}
          style={{
            opacity: kLeftCard,
            transform: `translateY(${lerp(24, 0, kLeftIn)}px)`,
            background: "linear-gradient(160deg, rgba(28,26,38,0.96), rgba(14,18,32,0.94))",
            boxShadow: `0 14px 36px rgba(0,0,0,0.45), inset 0 1px 0 ${CK.cxl}35`,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
            <span
              style={{
                fontSize: 19,
                fontWeight: 800,
                fontFamily: MONO,
                color: CK.cxl,
                background: `${CK.cxl}1e`,
                border: `1.5px solid ${CK.cxl}`,
                borderRadius: 999,
                padding: "5px 16px",
              }}
            >
              ASPLOS &apos;24 · CC-NIC CLAIM
            </span>
            <span style={{ fontSize: 19, fontWeight: 700, fontFamily: MONO, color: CK.trap }}>
              ⚠ Premise Under Test
            </span>
          </div>
          <div style={{ fontSize: 34, fontWeight: 800, color: C.text, lineHeight: 1.28 }}>
            &ldquo;PCIe MMIO is too slow
            <br />
            <span style={{ color: CK.cxl }}>→ Move NICs to CXL.cache</span>&rdquo;
          </div>
          <div style={{ fontSize: 22, color: C.muted, marginTop: 14, lineHeight: 1.38 }}>
            Assumes fenced direct push stalls on PCIe, forcing hardware cache coherence and special interconnects.
          </div>

          {/* Animated slow/red MMIO indicator inside Left Card */}
          <div
            style={{
              marginTop: 26,
              background: "#0f1528",
              border: `1.5px solid ${CK.trap}77`,
              borderRadius: 12,
              padding: "12px 16px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <span style={{ fontSize: 17, fontWeight: 800, color: CK.trap, fontFamily: MONO }}>
                ASSUMED SERIALIZED MMIO (1 PKT IN FLIGHT)
              </span>
              <span
                style={{
                  fontSize: 17,
                  fontWeight: 800,
                  color: CK.trap,
                  fontFamily: MONO,
                  opacity: 0.72 + 0.28 * slowPulse,
                }}
              >
                STALLED · SLOW
              </span>
            </div>
            <svg width="100%" height={30} viewBox="0 0 780 30">
              <rect x={0} y={5} width={780} height={20} rx={6} fill="#181f36" stroke={C.line} strokeWidth={1.2} />
              <rect
                x={0}
                y={5}
                width={Math.max(24, 118 * kSlowBar)}
                height={20}
                rx={6}
                fill={CK.trap}
                opacity={0.78 + 0.18 * slowPulse}
              />
              {/* Red fence wall */}
              <line x1={390} y1={1} x2={390} y2={29} stroke={CK.trap} strokeWidth={4} />
              <circle cx={24 + slowPos * 344} cy={15} r={7} fill={CK.cxl} opacity={0.95} />
              <text x={406} y={20} fontSize={15} fontWeight={700} fill={CK.trap} fontFamily={MONO}>
                SFENCE WALL · PIPELINE IDLE
              </text>
            </svg>
          </div>
        </Card>
      )}

      {/* Cue 1: Right Contrast Card (Our Finding) */}
      {kRightCard > 0.01 && (
        <Card
          x={990}
          y={220}
          w={870}
          h={425}
          border={CK.push}
          style={{
            opacity: kRightCard,
            transform: `translateY(${lerp(24, 0, kRightIn)}px)`,
            background: "linear-gradient(160deg, rgba(16,30,48,0.96), rgba(11,16,32,0.95))",
            boxShadow: `0 14px 36px rgba(0,0,0,0.45), inset 0 1px 0 ${CK.push}45`,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
            <span
              style={{
                fontSize: 19,
                fontWeight: 800,
                fontFamily: MONO,
                color: C.bg,
                background: CK.ok,
                borderRadius: 999,
                padding: "5px 18px",
              }}
            >
              OUR FINDING
            </span>
            <span style={{ fontSize: 19, fontWeight: 700, fontFamily: MONO, color: CK.push }}>
              100% Commercial Silicon
            </span>
          </div>
          <div style={{ fontSize: 32, fontWeight: 800, color: C.text, lineHeight: 1.3 }}>
            Off-the-shelf <span style={{ color: CK.push }}>PCIe Gen5 NIC</span>
          </div>
          <div style={{ fontSize: 22, color: C.text, marginTop: 12, lineHeight: 1.5, fontFamily: MONO }}>
            <span style={{ color: CK.ok }}>✓</span> Zero Cache Coherence &nbsp;·&nbsp;{" "}
            <span style={{ color: CK.ok }}>✓</span> Zero Intel DSA
            <br />
            <span style={{ color: CK.ok }}>✓</span> Full Hardware IOMMU Always On
          </div>

          <div style={{ display: "flex", gap: 16, marginTop: 32 }}>
            <StrikeBadge label="CXL.cache" sub="Coherent interconnect" kStrike={kStrikeCxl} />
            <StrikeBadge label="Intel DSA" sub="Special CPU accelerator" kStrike={kStrikeDsa} />
          </div>
        </Card>
      )}

      {/* Cue 2: Clean hardware summary strip beneath full topology */}
      {kTopoCaps > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 110,
            top: 748,
            width: 1700,
            display: "flex",
            justifyContent: "space-between",
            gap: 18,
            opacity: kTopoCaps,
          }}
        >
          {[
            { tag: "HOST", text: "Xeon 6438M · cpu40 3.9 GHz · IOMMU on", color: CK.host, active: hSaturn > 0.35 },
            { tag: "LINK", text: "PCIe Gen5 ×4 · 15.75 GB/s / dir", color: CK.push, active: hPcie > 0.35 },
            { tag: "DPU", text: "Marvell CN106xx · 48 MiB L3 · DPI DMA", color: CK.card, active: hCn106 > 0.35 },
            { tag: "WIRE", text: "3 m 100GbE DAC ⇄ mercury CX-5", color: CK.peer, active: hWire > 0.35 || hMercury > 0.35 },
          ].map((item) => (
            <div
              key={item.tag}
              style={{
                flex: 1,
                background: item.active ? `${item.color}1c` : "rgba(20,27,46,0.88)",
                border: `2px solid ${item.active ? item.color : C.line}`,
                borderRadius: 12,
                padding: "10px 14px",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 16, fontWeight: 800, color: item.color, fontFamily: MONO }}>{item.tag}</div>
              <div style={{ fontSize: 18, fontWeight: 600, color: C.text, marginTop: 3 }}>{item.text}</div>
            </div>
          ))}
        </div>
      )}

      {/* Cue 3: 4 HeroStat cards + Provenance Pill */}
      {statKs[0] > 0.01 && (
        <HeroStat
          x={60}
          y={558}
          w={415}
          h={172}
          value={vRtt}
          label="64 B PCIe RTT"
          sub="0.935 µs on-card 100GbE"
          color={CK.push}
          opacity={statKs[0]}
          dy={statDys[0]}
        />
      )}
      {statKs[1] > 0.01 && (
        <HeroStat
          x={505}
          y={558}
          w={415}
          h={172}
          value={vGbps}
          label="1 Host Core · 100GbE"
          sub="Full wire line rate (MRV-155)"
          color={CK.ok}
          opacity={statKs[1]}
          dy={statDys[1]}
        />
      )}
      {statKs[2] > 0.01 && (
        <HeroStat
          x={950}
          y={558}
          w={415}
          h={172}
          value="0% Tax"
          label="99.99993% DDIO Hit"
          sub="128 MiB TX pool · 0 misses"
          color={CK.sdp}
          opacity={statKs[2]}
          dy={statDys[2]}
        />
      )}
      {statKs[3] > 0.01 && (
        <HeroStat
          x={1395}
          y={558}
          w={415}
          h={172}
          value={vDram}
          label="Host Burst DRAM"
          sub="256 KiB active-NIC hot ring"
          color={CK.cxl}
          opacity={statKs[3]}
          dy={statDys[3]}
        />
      )}

      {kProv > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 60,
            top: 760,
            width: 1800,
            display: "flex",
            justifyContent: "center",
            opacity: kProv,
          }}
        >
          <div
            style={{
              fontFamily: MONO,
              fontSize: 21,
              fontWeight: 700,
              color: C.text,
              background: "rgba(20,27,46,0.92)",
              border: `1.5px solid ${CK.ok}88`,
              borderRadius: 999,
              padding: "10px 28px",
            }}
          >
            <span style={{ color: CK.ok }}>3,500+ verified runs</span> ·{" "}
            <span style={{ color: CK.push }}>&gt;2.5 B frames</span> · 0 torn / 0 corrupt / 0 EDAC UE
          </div>
        </div>
      )}

      {/* Cue 4: 5-step horizontal Story Roadmap */}
      {kRoadBase > 0.01 && (
        <>
          <Txt
            x={60}
            y={516}
            w={1800}
            size={23}
            weight={700}
            align="center"
            color={C.muted}
            style={{ opacity: kRoadBase, fontFamily: MONO, letterSpacing: 1.5 }}
          >
            FIVE-PART EXPERIMENTAL STORY
          </Txt>
          {ROADMAP.map((item, i) => {
            const kLit = prog(t, T[4] + item.at, 0.5);
            const kPop = pop(t, T[4] + item.at, 0.45);
            const scale = lerp(1, 1.03, kLit) * lerp(0.96, 1, kPop > 0 ? Math.min(1, kPop) : 1);
            const cx = roadX0 + i * (cardW + cardGap);
            return (
              <div
                key={item.num}
                style={{
                  position: "absolute",
                  left: cx,
                  top: roadY,
                  width: cardW,
                  height: roadH,
                  opacity: kRoadBase * lerp(0.42, 1, kLit),
                  transform: `translateY(${lerp(18, 0, kRoadBase)}px) scale(${scale})`,
                  background: kLit > 0.3
                    ? `linear-gradient(160deg, ${item.color}24, rgba(14,20,38,0.96))`
                    : "rgba(20,27,46,0.9)",
                  border: `2.5px solid ${kLit > 0.3 ? item.color : C.line}`,
                  borderRadius: 18,
                  padding: "18px 20px",
                  boxSizing: "border-box",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                  boxShadow: kLit > 0.3 ? `0 12px 30px ${item.color}28` : "none",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div
                    style={{
                      width: 38,
                      height: 38,
                      borderRadius: 999,
                      background: kLit > 0.3 ? item.color : C.line,
                      color: kLit > 0.3 ? C.bg : C.text,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 22,
                      fontWeight: 800,
                      fontFamily: MONO,
                      flexShrink: 0,
                    }}
                  >
                    {item.num}
                  </div>
                  <div style={{ fontSize: 26, fontWeight: 800, color: C.text, lineHeight: 1.15 }}>
                    {item.title}
                  </div>
                </div>
                <div
                  style={{
                    fontSize: 22,
                    fontWeight: 800,
                    color: item.color,
                    fontFamily: MONO,
                    marginTop: 8,
                  }}
                >
                  {item.sub}
                </div>
                <div style={{ fontSize: 19, color: C.muted, lineHeight: 1.3 }}>{item.detail}</div>
              </div>
            );
          })}
        </>
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
