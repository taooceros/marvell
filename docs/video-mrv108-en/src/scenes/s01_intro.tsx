import {
  C,
  Cue,
  FONT,
  K,
  MONO,
  PacketStream,
  SceneDef,
  Stage,
  appear,
  countUp,
  fade,
  prog,
  useRealT,
  useT,
} from "../lib";
import { HwBox, PathTag, PolyArrow, StatusBadge, dsaGeom, dsaRoutes } from "./dsa";
// <cues>
const cues: Cue[] = [
  [0.60, 9.84, "How do we let a Marvell CN106 **DPU** directly drive the host CPU's Intel **DSA** with **zero host CPU forwarding**?", "How do we let a Marvell C N one oh six D P U directly drive the host C P U's Intel D S A with zero host C P U forwarding?"], // 0
  [10.19, 24.63, "Intel **DSA** (`dsa2`) is an on-die Xeon copy engine; traditionally, the host CPU must proxy every DPU request, burning cores and adding latency.", "Intel D S A, specifically d s a two on Xeon Socket one, is an on-die memory copy engine. Traditionally, the host C P U has to proxy every D P U request, burning cores and adding a software hop."], // 1
  [24.98, 40.86, "Instead, we build **same-socket bidirectional PCIe P2P**: 64 B descriptors go forward to `dsa2`, and 32 B completions return straight to card memory.", "Instead, we build same-socket bidirectional P C I e peer-to-peer: sixty-four-byte descriptors travel forward to d s a two's portal, and thirty-two-byte completions fly straight back to card memory while the host C P U sleeps."], // 2
  [41.21, 58.09, "Result: a single engine reaches **31.32 GB/s** sustained copy throughput, with `W = 1` loop cycles down to **1.27 µs** (`NOP`) and **2.17 µs** (`4 KiB`), zero errors.", "The result: a single engine reaches thirty-one point three two gigabytes per second sustained throughput, with single-in-flight loop times down to one point two seven microseconds for N O P and two point one seven microseconds for four-kilobyte copies, with zero errors."], // 3
  [58.44, 73.10, "We'll walk through four steps: **two-layer vmem mapping** -> **DPI atomic submit** -> **reverse-P2P completions** -> **DSA Batch aggregation**.", "We will walk through four steps: setting up two-layer virtual memory mapping, replacing write-combining with D P I atomic writes, eliminating read-back via reverse P 2 P, and clearing a hidden drain bubble with D S A Batch."], // 4
];
const DUR = 74.50; // <dur>
// </cues>

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  // Full-width architecture diagram in upper-middle band (x = 80..1840, y = 134..650)
  const g = dsaGeom(80, 134, 1.0);
  const r = dsaRoutes(g);

  const kFrame = prog(t, T[0], 0.7);
  const kTrad = fade(t, T[1] + 0.3, T[2] - 0.1, 0.5);
  const kP2P = prog(t, T[2], 0.8);
  const kCpuSleep = prog(t, T[2] + 0.2, 0.5);
  const kKpi = fade(t, T[3], T[4] - 0.1, 0.5);
  const kRoad = prog(t, T[4], 0.6);

  const steps = [
    { n: "01", title: "2-Layer vmem", sub: "dmar0 + dmar6 SVA", col: C.purple },
    { n: "02", title: "DPI 64 B Atomic", sub: "0 drops vs WC", col: C.orange },
    { n: "03", title: "Reverse P2P BAR4", sub: "Cut 1.21 µs read-back", col: C.green },
    { n: "04", title: "DSA Batch", sub: "Clear 131 ns -> 31.32 GB/s", col: C.yellow },
  ];

  return (
    <>
      <Stage>
        <g opacity={kFrame}>
          <rect
            x={g.cardFrame.x}
            y={g.cardFrame.y}
            width={g.cardFrame.w}
            height={g.cardFrame.h}
            rx={16}
            fill="rgba(20,27,46,0.55)"
            stroke={K.card}
            strokeWidth={2}
            opacity={0.9}
          />
          <text x={g.cardFrame.cx} y={g.cardFrame.y + 34} textAnchor="middle" fontFamily={FONT} fontSize={22} fontWeight={800} fill={K.card}>
            Marvell CN106 DPU (ARM64 · 2.0 GHz)
          </text>

          <rect
            x={g.hostFrame.x}
            y={g.hostFrame.y}
            width={g.hostFrame.w}
            height={g.hostFrame.h}
            rx={16}
            fill="rgba(20,27,46,0.55)"
            stroke={K.host}
            strokeWidth={2}
            opacity={0.9}
          />
          <text x={g.hostFrame.x + 26} y={g.hostFrame.y + 36} textAnchor="start" fontFamily={FONT} fontSize={22} fontWeight={800} fill={K.host}>
            Xeon Socket 1 Host (NUMA 1 · Same Socket)
          </text>

          <line
            x1={g.pcieX}
            y1={g.cardFrame.y + 44}
            x2={g.pcieX}
            y2={g.cardFrame.y + g.cardFrame.h - 10}
            stroke={K.link}
            strokeWidth={2.5}
            strokeDasharray="8 8"
            opacity={0.55}
          />
          <PathTag cx={g.pcieX} cy={g.cardFrame.y + 22} text="PCIe Gen5 x4" color={K.link} size={17} mono />

          <HwBox x={g.core.x} y={g.core.y} w={g.core.w} h={g.core.h} title="ARM64 Core" sub="lcore 3" accent={K.card} titleSize={20} subSize={15} />
          <HwBox
            x={g.dpiW.x}
            y={g.dpiW.y}
            w={g.dpiW.w}
            h={g.dpiW.h}
            title="DPI Write Engine"
            sub="06:00.2"
            sub2={kP2P > 0.3 ? "64 B Atomic MWr" : undefined}
            accent={K.fwd}
            active={kP2P > 0.3}
            titleSize={20}
            subSize={15}
          />
          <HwBox
            x={g.bar4.x}
            y={g.bar4.y}
            w={g.bar4.w}
            h={g.bar4.h}
            title="Card L3 (TAD)"
            sub="BAR4 Slot 15"
            sub2={kP2P > 0.3 ? "32 B Completion" : undefined}
            accent={K.rev}
            active={kP2P > 0.3}
            titleSize={20}
            subSize={15}
          />
          <HwBox
            x={g.dpiR.x}
            y={g.dpiR.y}
            w={g.dpiR.w}
            h={g.dpiR.h}
            title="DPI Read Engine"
            sub="06:00.1"
            accent={C.dim}
            dim={kP2P > 0.4}
            titleSize={19}
            subSize={15}
          />

          <HwBox
            x={g.hostCpu.x}
            y={g.hostCpu.y}
            w={g.hostCpu.w}
            h={g.hostCpu.h}
            title="Host CPU"
            sub={kCpuSleep > 0.5 ? "Sleeping · 0 Proxy" : "cpu40 · 3.9 GHz"}
            accent={kTrad > 0.2 ? C.red : K.host}
            active={kTrad > 0.2}
            sleeping={kCpuSleep > 0.5}
            badge={kCpuSleep > 0.5 ? "Sleeping" : kTrad > 0.2 ? "Proxy Hop" : undefined}
            titleSize={20}
            subSize={15}
          />
          <HwBox
            x={g.dmar0.x}
            y={g.dmar0.y}
            w={g.dmar0.w}
            h={g.dmar0.h}
            title="IOMMU dmar0"
            sub="VF0 Portal Map"
            accent={K.iommu0}
            active={kP2P > 0.3}
            titleSize={19}
            subSize={15}
          />
          <HwBox
            x={g.dsa.x}
            y={g.dsa.y}
            w={g.dsa.w}
            h={g.dsa.h}
            title="Intel DSA"
            sub="dsa2 / wq2.0"
            sub2="On-Die Copy Engine"
            accent={K.dsa}
            active={prog(t, T[1], 0.6) > 0.3}
            titleSize={21}
            subSize={15}
          />
          <HwBox
            x={g.dmar6.x}
            y={g.dmar6.y}
            w={g.dmar6.w}
            h={g.dmar6.h}
            title="IOMMU dmar6"
            sub="SVA PASID Walk"
            accent={K.iommu6}
            active={kP2P > 0.3}
            titleSize={19}
            subSize={15}
          />
          <HwBox
            x={g.mem.x}
            y={g.mem.y}
            w={g.mem.w}
            h={g.mem.h}
            title="Host Memzone"
            sub="2 MiB Hugepages"
            sub2="src -> dst Copy"
            accent={K.host}
            titleSize={21}
            subSize={16}
          />
        </g>

        {/* Cue 1: Traditional CPU-proxied path */}
        <g opacity={kTrad}>
          <PolyArrow pts={r.tradSubmit} color={C.red} w={4.5} dash="10 7" k={prog(t, T[1] + 0.5, 0.7)} />
          <PolyArrow pts={r.tradCpuToDsa} color={C.red} w={4.5} dash="10 7" k={prog(t, T[1] + 1.0, 0.7)} />
          <PathTag cx={g.pcieX} cy={g.hostCpu.cy - 44} text="Traditional: Host CPU Proxy" color={C.red} size={18} />
          <StatusBadge cx={g.hostCpu.x - 18} cy={g.hostCpu.y + 16} ok={false} r={18} />
        </g>

        {/* Cue 2+: Bidirectional PCIe P2P highway */}
        <g opacity={kP2P}>
          <PolyArrow pts={r.fwdP2P} color={K.fwd} w={4.5} k={prog(t, T[2] + 0.2, 0.7)} halo />
          <PolyArrow pts={r.dmar0ToDsa} color={K.fwd} w={4.5} k={prog(t, T[2] + 0.6, 0.5)} halo />
          <PolyArrow pts={r.dsaToDmar6} color={K.dsa} w={3.5} k={prog(t, T[2] + 0.8, 0.5)} />
          <PolyArrow pts={r.dmar6ToMem} color={K.dsa} w={3.5} k={prog(t, T[2] + 1.0, 0.5)} />
          <PolyArrow pts={r.revP2P} color={K.rev} w={4.5} k={prog(t, T[2] + 1.2, 0.7)} halo />
          <PolyArrow pts={r.localPoll} color={K.rev} w={3.5} k={prog(t, T[2] + 1.6, 0.5)} />

          <PacketStream pts={[...r.fwdP2P, r.dmar0ToDsa[1]]} gap={170} travel={rt * 170} color={K.fwd} size={11} />
          <PacketStream pts={r.revP2P} gap={170} travel={rt * 170 + 85} color={K.rev} size={11} />

          <PathTag cx={g.pcieX + 18} cy={g.dmar0.cy - 54} text="Fwd P2P · 64 B Desc" color={K.fwd} size={18} />
          <PathTag cx={g.pcieX} cy={g.bar4.cy + 58} text="Rev P2P · 32 B Comp" color={K.rev} size={18} />
        </g>

        {/* Top-right hero badge on Host frame */}
        <g opacity={prog(t, T[0] + 0.8, 0.6)}>
          <rect x={g.hostFrame.x + g.hostFrame.w - 306} y={g.hostFrame.y + 12} width={286} height={40} rx={20} fill="rgba(67,197,158,0.18)" stroke={C.green} strokeWidth={2.2} />
          <text x={g.hostFrame.x + g.hostFrame.w - 163} y={g.hostFrame.y + 33} textAnchor="middle" dominantBaseline="central" fontFamily={FONT} fontSize={19} fontWeight={800} fill={C.green}>
            Zero Host CPU Forwarding
          </text>
        </g>
      </Stage>

      {/* Cue 3: 3 Hero KPI cards */}
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 682,
          width: 1760,
          height: 184,
          display: "flex",
          gap: 28,
          opacity: kKpi,
          transform: `translateY(${(1 - prog(t, T[3], 0.5)) * 20}px)`,
        }}
      >
        {[
          {
            val: `${countUp(t, T[3] + 0.1, 1.3, 31.32, 2)} GB/s`,
            label: "Single-Engine Sustained Copy",
            sub: "4 KiB MEMMOVE · Batch B=16 · 0 drops",
            col: C.yellow,
          },
          {
            val: `${countUp(t, T[3] + 0.25, 1.3, 1.27, 2)} µs`,
            label: "W = 1 NOP Closed-Loop Cycle",
            sub: "Rev P2P to Card L3 · -35.1% vs Host read-back",
            col: C.green,
          },
          {
            val: `${countUp(t, T[3] + 0.4, 1.3, 2.17, 2)} µs`,
            label: "W = 1 4 KiB Copy Loop Cycle",
            sub: "163 ns tail jitter · 1.21 µs step eliminated",
            col: C.cyan,
          },
        ].map((item, idx) => (
          <div
            key={idx}
            style={{
              flex: 1,
              background: "rgba(20,27,46,0.92)",
              border: `2px solid ${item.col}`,
              borderRadius: 16,
              padding: "18px 26px",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              boxShadow: `0 0 24px ${item.col}22`,
            }}
          >
            <div style={{ fontFamily: MONO, fontSize: 52, fontWeight: 800, color: item.col, lineHeight: 1.05 }}>{item.val}</div>
            <div style={{ fontSize: 23, fontWeight: 700, color: C.text, marginTop: 8 }}>{item.label}</div>
            <div style={{ fontSize: 18, color: C.muted, marginTop: 4 }}>{item.sub}</div>
          </div>
        ))}
      </div>

      {/* Cue 4: 4-step roadmap */}
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 690,
          width: 1760,
          height: 166,
          display: "flex",
          gap: 22,
          opacity: kRoad,
        }}
      >
        {steps.map((s, idx) => (
          <div
            key={idx}
            style={{
              flex: 1,
              background: "rgba(20,27,46,0.94)",
              border: `2px solid ${s.col}`,
              borderRadius: 16,
              padding: "18px 22px",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              ...appear(t, T[4] + idx * 0.45, undefined, 18),
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span
                style={{
                  fontFamily: MONO,
                  fontSize: 20,
                  fontWeight: 800,
                  color: C.bg,
                  background: s.col,
                  borderRadius: 99,
                  padding: "2px 12px",
                }}
              >
                {s.n}
              </span>
              <span style={{ fontSize: 23, fontWeight: 800, color: C.text }}>{s.title}</span>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 18, color: s.col, marginTop: 12 }}>{s.sub}</div>
          </div>
        ))}
      </div>
    </>
  );
}
export const S01: SceneDef = {
  id: "S01",
  title: "Goal: DPU Directly Driving Host DSA",
  stage: 0,
  source: "CN106 DPU <-> Xeon Socket 1 · dsa2/wq2.0",
  dur: DUR,
  cues,
  C: Body,
};
