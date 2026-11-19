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
  [0.60, 8.54, "今天我们展示如何让 Marvell CN106 **DPU** 直接驱动主机 CPU 里的 Intel **DSA**，全程**零主机 CPU 转发**。", "今天我们展示如何让 Marvell C N 一零六 D P U 直驱主机里的 Intel D S A，全程零主机 C P U 转发。"], // 0
  [8.89, 20.27, "Intel **DSA** 是集成在 Xeon 片上的硬件内存搬运引擎（`dsa2`）；传统做法须主机 CPU 中转，既占核又多一跳延迟。", "Intel D S A 是至强片上的硬件内存搬运引擎，这里用插槽一号的 d s a 二。传统做法须主机核中转提交，既占核又多一跳延迟。"], // 1
  [20.62, 32.92, "我们的解法走**同插槽双向 PCIe P2P**：正向卡直写 64 B 描述符到 `dsa2` 门户，反向 `dsa2` 直写 32 B 完成记录回卡内存。", "我们改走同插槽双向 P C I e 点对点直通：正向卡直写六十四字节描述符进门户，反向 d s a 二直写三十二字节完成记录回卡内存，主机核数据面休眠。"], // 2
  [33.27, 42.83, "最终单引擎持续带宽达 **31.32 GB/s**，单并发闭环压至 `NOP` **1.27 µs**、`4 KiB` **2.17 µs**，零丢包。", "最终单引擎持续带宽达三十一点三二 G B 每秒，单并发闭环压到空操作一点二七微秒、四 K B 拷贝二点一七微秒，零丢包。"], // 3
  [43.18, 53.92, "全片四步拆解：**两层虚拟映射** → **DPI 原子提交** → **反向 P2P 完成** → **Batch 消除排空泡沫**。", "全片分四步：先打通两层虚拟映射，再换用 D P I 原子写避开丢包，接着反向 P 二 P 砍掉读回底噪，最后用 Batch 消除排空泡沫。"], // 4
];
const DUR = 55.32; // <dur>
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
    { n: "01", title: "两层 vmem 映射", sub: "dmar0 + dmar6 SVA", col: C.purple },
    { n: "02", title: "DPI 64 B 原子写", sub: "0 丢包替代 WC", col: C.orange },
    { n: "03", title: "反向 P2P 写 BAR4", sub: "砍掉 1.21 µs 读回", col: C.green },
    { n: "04", title: "DSA Batch 聚合", sub: "消 131 ns 泡沫 → 31.32 GB/s", col: C.yellow },
  ];

  return (
    <>
      <Stage>
        {/* Card & Host containers */}
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
          <text x={g.cardFrame.cx} y={g.cardFrame.y + 30} textAnchor="middle" fontFamily={FONT} fontSize={24} fontWeight={800} fill={K.card}>
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
          <text x={g.hostFrame.x + 28} y={g.hostFrame.y + 38} textAnchor="start" fontFamily={FONT} fontSize={24} fontWeight={800} fill={K.host}>
            Xeon Socket 1 主机 (NUMA 1 · 同插槽)
          </text>

          {/* PCIe Gen5 x4 divider */}
          <line
            x1={g.pcieX}
            y1={g.cardFrame.y + 40}
            x2={g.pcieX}
            y2={g.cardFrame.y + g.cardFrame.h - 10}
            stroke={K.link}
            strokeWidth={2.5}
            strokeDasharray="8 8"
            opacity={0.55}
          />
          <PathTag cx={g.pcieX} cy={g.cardFrame.y + 22} text="PCIe Gen5 x4" color={K.link} size={18} mono />

          {/* Hardware blocks */}
          <HwBox
            x={g.core.x}
            y={g.core.y}
            w={g.core.w}
            h={g.core.h}
            title="ARM64 核"
            sub="lcore 3"
            accent={K.card}
            titleSize={21}
            subSize={16}
          />
          <HwBox
            x={g.dpiW.x}
            y={g.dpiW.y}
            w={g.dpiW.w}
            h={g.dpiW.h}
            title="DPI 写引擎"
            sub="06:00.2"
            sub2={kP2P > 0.3 ? "64 B 原子写" : undefined}
            accent={K.fwd}
            active={kP2P > 0.3}
            titleSize={21}
            subSize={16}
          />
          <HwBox
            x={g.bar4.x}
            y={g.bar4.y}
            w={g.bar4.w}
            h={g.bar4.h}
            title="卡 L3 (TAD)"
            sub="BAR4 槽 15"
            sub2={kP2P > 0.3 ? "32 B comp" : undefined}
            accent={K.rev}
            active={kP2P > 0.3}
            titleSize={21}
            subSize={16}
          />
          <HwBox
            x={g.dpiR.x}
            y={g.dpiR.y}
            w={g.dpiR.w}
            h={g.dpiR.h}
            title="DPI 读引擎"
            sub="06:00.1"
            accent={C.dim}
            dim={kP2P > 0.4}
            titleSize={20}
            subSize={15}
          />

          <HwBox
            x={g.hostCpu.x}
            y={g.hostCpu.y}
            w={g.hostCpu.w}
            h={g.hostCpu.h}
            title="主机 CPU"
            sub={kCpuSleep > 0.5 ? "休眠 · 零转发" : "cpu40 · 3.9 GHz"}
            accent={kTrad > 0.2 ? C.red : K.host}
            active={kTrad > 0.2}
            sleeping={kCpuSleep > 0.5}
            badge={kCpuSleep > 0.5 ? "休眠" : kTrad > 0.2 ? "中转瓶颈" : undefined}
            titleSize={21}
            subSize={16}
          />
          <HwBox
            x={g.dmar0.x}
            y={g.dmar0.y}
            w={g.dmar0.w}
            h={g.dmar0.h}
            title="IOMMU dmar0"
            sub="VF0 门户映射"
            accent={K.iommu0}
            active={kP2P > 0.3}
            titleSize={20}
            subSize={15}
          />
          <HwBox
            x={g.dsa.x}
            y={g.dsa.y}
            w={g.dsa.w}
            h={g.dsa.h}
            title="Intel DSA"
            sub="dsa2 / wq2.0"
            sub2="片上内存搬运引擎"
            accent={K.dsa}
            active={prog(t, T[1], 0.6) > 0.3}
            titleSize={22}
            subSize={16}
          />
          <HwBox
            x={g.dmar6.x}
            y={g.dmar6.y}
            w={g.dmar6.w}
            h={g.dmar6.h}
            title="IOMMU dmar6"
            sub="SVA PASID 页表"
            accent={K.iommu6}
            active={kP2P > 0.3}
            titleSize={20}
            subSize={15}
          />
          <HwBox
            x={g.mem.x}
            y={g.mem.y}
            w={g.mem.w}
            h={g.mem.h}
            title="主机大页内存"
            sub="Memzone"
            sub2="src → dst 搬运"
            accent={K.host}
            titleSize={22}
            subSize={17}
          />
        </g>

        {/* Cue 1: Traditional CPU-forwarded path (red dashed) */}
        <g opacity={kTrad}>
          <PolyArrow pts={r.tradSubmit} color={C.red} w={4.5} dash="10 7" k={prog(t, T[1] + 0.5, 0.7)} />
          <PolyArrow pts={r.tradCpuToDsa} color={C.red} w={4.5} dash="10 7" k={prog(t, T[1] + 1.0, 0.7)} />
          <PathTag cx={g.pcieX} cy={g.hostCpu.cy - 44} text="传统：先通知主机 CPU 转发" color={C.red} size={20} />
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

          {/* Live ambient packet streams */}
          <PacketStream pts={[...r.fwdP2P, r.dmar0ToDsa[1]]} gap={170} travel={rt * 170} color={K.fwd} size={11} />
          <PacketStream pts={r.revP2P} gap={170} travel={rt * 170 + 85} color={K.rev} size={11} />
          <PathTag cx={g.pcieX + 18} cy={g.dmar0.cy - 54} text="正向 P2P · 64 B 描述符" color={K.fwd} size={20} />
          <PathTag cx={g.pcieX} cy={g.bar4.cy + 58} text="反向 P2P · 32 B 完成记录" color={K.rev} size={20} />
        </g>

        {/* Integrated hero pill on top-right of Host frame */}
        <g opacity={prog(t, T[0] + 0.8, 0.6)}>
          <rect x={g.hostFrame.x + g.hostFrame.w - 270} y={g.hostFrame.y + 12} width={250} height={40} rx={20} fill="rgba(67,197,158,0.18)" stroke={C.green} strokeWidth={2.2} />
          <text x={g.hostFrame.x + g.hostFrame.w - 145} y={g.hostFrame.y + 33} textAnchor="middle" dominantBaseline="central" fontFamily={FONT} fontSize={22} fontWeight={800} fill={C.green}>
            零主机 CPU 转发
          </text>
        </g>
      </Stage>

      {/* Bottom band (y = 684..868): Cue 0-2 teaser -> Cue 3 KPIs -> Cue 4 Roadmap */}
      <div
        style={{
          position: "absolute",
          left: 75,
          top: 682,
          width: 1770,
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
            label: "单引擎持续搬运带宽",
            sub: "4 KiB MEMMOVE · Batch B=16 · 零丢包",
            col: C.yellow,
          },
          {
            val: `${countUp(t, T[3] + 0.25, 1.3, 1.27, 2)} µs`,
            label: "W = 1 空操作 (NOP) 闭环",
            sub: "反向 P2P 直写卡 L3 · 比主机读回降 35.1%",
            col: C.green,
          },
          {
            val: `${countUp(t, T[3] + 0.4, 1.3, 2.17, 2)} µs`,
            label: "W = 1 4 KiB 拷贝闭环",
            sub: "尾抖动 163 ns · 彻底消除 1.21 µs 台阶",
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
            <div style={{ fontFamily: MONO, fontSize: 54, fontWeight: 800, color: item.col, lineHeight: 1.05 }}>{item.val}</div>
            <div style={{ fontSize: 25, fontWeight: 700, color: C.text, marginTop: 8 }}>{item.label}</div>
            <div style={{ fontSize: 19, color: C.muted, marginTop: 4 }}>{item.sub}</div>
          </div>
        ))}
      </div>

      {/* Cue 4: 4-step roadmap */}
      <div
        style={{
          position: "absolute",
          left: 75,
          top: 690,
          width: 1770,
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
                  fontSize: 22,
                  fontWeight: 800,
                  color: C.bg,
                  background: s.col,
                  borderRadius: 99,
                  padding: "2px 12px",
                }}
              >
                {s.n}
              </span>
              <span style={{ fontSize: 26, fontWeight: 800, color: C.text }}>{s.title}</span>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 20, color: s.col, marginTop: 12 }}>{s.sub}</div>
          </div>
        ))}
      </div>
    </>
  );
}
export const S01: SceneDef = {
  id: "S01",
  title: "目标与直觉：DPU 直驱主机 DSA",
  stage: 0,
  source: "CN106 DPU ⇄ Xeon Socket 1 · dsa2/wq2.0",
  dur: DUR,
  cues,
  C: Body,
};
