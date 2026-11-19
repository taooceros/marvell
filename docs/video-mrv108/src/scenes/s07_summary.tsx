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
  prog,
  useRealT,
  useT,
} from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 10.74, "回看三步演进——**第一步（基础直驱）**：两层 `vmem` 映射 + DPI 64 B 原子写，零主机转发达 **29.53 GB/s**，但有 `1.21 µs` 读回底噪。", "一屏回看三步演进：第一步打通两层虚拟映射加 D P I 原子写，零主机转发跑出二十九点五三 G B 每秒，留有一点二一微秒读回底噪。"], // 0
  [11.09, 22.13, "**第二步（反向 P2P 完成）**：32 B 完成记录直写卡 `BAR4` 槽 15，闭环压至 **1.27 / 2.17 µs**、抖动 **163 ns**，暴露 `131 ns` 泡沫。", "第二步用 B A R 四的十五号槽反向 P 二 P 完成写，把闭环压到一点二七和二点一七微秒、抖动一百六十三纳秒，却暴露出一百三十一纳秒泡沫。"], // 1
  [22.48, 32.34, "**第三步（DSA Batch 聚合）**：`B = 4 / 16` 共享 1 次反向完成写，消除泡沫并推至 **31.32 GB/s**，**满带宽与紧尾兼得**！", "第三步用 D S A Batch 聚合共享单次反向完成写，彻底消除泡沫、冲上三十一点三二 G B 每秒，真正实现满带宽与紧尾兼得。"], // 2
  [32.69, 44.17, "三条工程红线：门户仅开 `FLAG_WRITE`、零 `pem_wc` 全程纯 DPI 写、SVA 须配**专用最小隔离进程**与 `BAR4` 安全租约。", "三条工程红线务必牢记：门户只开只写权限，卡侧禁用写合并全程纯 D P I，S V A 须用专用最小隔离进程并配 B A R 四安全租约。"], // 3
  [44.52, 56.74, "下一步将补测 **`> 128 MiB` 真冷 DRAM** 对照与多引擎扩展——DPU 零主机转发直驱 CPU DSA 的双向 P2P 闭环已完整跑通！", "下一步我们将补测大于一百二十八兆字节的全冷 D R A M 对照与多引擎扩展。至此，D P U 零主机转发直驱主机 D S A 的双向 P 二 P 闭环完整跑通，感谢观看！"], // 4
];
const DUR = 58.14; // <dur>
// </cues>

const STAGES_DATA = [
  {
    idx: 0,
    tag: "阶段 1 · 基础直驱",
    color: K.fwd,
    mech: "两层 vmem（dmar0 + dmar6 SVA）+ DPI 64 B 原子写",
    bw: "29.53 GB/s",
    bwSub: "热 LLC · CC = 1",
    lat: "W = 1 4K: 3.22 µs",
    latSub: "Host Memzone 完成区",
    note: "瓶颈：1.21 µs DPI 读回底噪",
    noteColor: K.bubble,
  },
  {
    idx: 1,
    tag: "阶段 2 · 反向 P2P 完成",
    color: K.rev,
    mech: "32 B 完成记录直写卡 BAR4 槽 15（CN106 L3 TAD）",
    bw: "NOP 23.01 M/s",
    bwSub: "4K 平台 15.3 GB/s",
    lat: "1.27 / 2.17 µs",
    latSub: "抖动 p99−p50 仅 163 ns",
    note: "暴露：高并发 131 ns 排空泡沫",
    noteColor: K.wc,
  },
  {
    idx: 2,
    tag: "阶段 3 · DSA Batch (B=4/16)",
    color: K.batch,
    mech: "B 笔拷贝共享 1 次反向 P2P 完成写（RCR = 0）",
    bw: "31.13–31.32 GB/s",
    bwSub: "2.05× · 单引擎满带",
    lat: "B=4: 2.84 µs",
    latSub: "B=16: 4.42 / 4.61 µs",
    note: "满带宽 + 紧尾双赢 · 0 丢包",
    noteColor: K.rev,
  },
];

const GUARDRAILS = [
  {
    num: "01",
    title: "门户只写权限",
    code: "VFIO_DMA_MAP_FLAG_WRITE",
    desc: "门户页仅授写权限（无读权限），卡侧只能投递 64 B Posted MWr。",
    color: K.fwd,
  },
  {
    num: "02",
    title: "零 pem_wc · 纯 DPI",
    code: "DPI 硬件原子 64 B MWr",
    desc: "禁用 ARM WC 写合并，全程纯 DPI 原子提交，杜绝 SWERROR = 0 静默丢包。",
    color: K.wc,
  },
  {
    num: "03",
    title: "SVA 进程隔离 + BAR4 租约",
    code: "专用最小休眠进程 + 槽 15 租约",
    desc: "避免共享宿主地址空间暴露给 dmar6，租约握手保护 BAR4 槽 15 安全复用。",
    color: K.dsa,
  },
];

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  const kGuard = prog(t, T[3], 0.6);
  const kClose = prog(t, T[4], 0.7);

  return (
    <>
      {/* Subtle ambient SVG motion behind the HTML cards */}
      <Stage>
        {/* Animated connector rail on the left edge of the 3-stage table */}
        <line
          x1={44}
          y1={248}
          x2={44}
          y2={248 + 256 * prog(t, T[0], 0.8)}
          stroke={C.line}
          strokeWidth={4}
          strokeLinecap="round"
        />
        {STAGES_DATA.map((s, i) => {
          const kRow = prog(t, T[i], 0.5);
          const cy = 248 + i * 128;
          return (
            <circle
              key={s.idx}
              cx={44}
              cy={cy}
              r={9 * kRow}
              fill={s.color}
              opacity={kRow}
            />
          );
        })}

        {/* Ambient bidirectional P2P pulse along the closing banner when T[4] activates */}
        {kClose > 0.01 && (
          <PacketStream
            pts={[[80, 774], [1840, 774]]}
            travel={rt * 180}
            gap={220}
            size={8}
            color={K.rev}
            opacity={0.75 * kClose}
          />
        )}
      </Stage>

      {/* ================= 3-STAGE EVOLUTION COMPARISON TABLE (y = 136..568) ================= */}
      <div
        style={{
          position: "absolute",
          left: 64,
          top: 136,
          width: 1792,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        {/* Header Bar */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1.35fr 2.15fr 1.25fr 1.45fr 1.6fr",
            padding: "11px 24px",
            borderRadius: 12,
            background: "rgba(255,255,255,0.05)",
            border: `1.5px solid ${C.line}`,
            fontFamily: FONT,
            fontSize: 20,
            fontWeight: 800,
            color: C.muted,
          }}
        >
          <div>演进阶段</div>
          <div>核心机制</div>
          <div>持续吞吐</div>
          <div>W = 1 延迟 / 尾差</div>
          <div>关键结论 / 瓶颈</div>
        </div>

        {/* 3 Progressive Rows */}
        {STAGES_DATA.map((s, i) => {
          const isActive = t >= T[i] && (i === 2 ? t < T[3] : t < T[i + 1]);
          const rowStyle = appear(t, T[i]);
          return (
            <div
              key={s.idx}
              style={{
                display: "grid",
                gridTemplateColumns: "1.35fr 2.15fr 1.25fr 1.45fr 1.6fr",
                alignItems: "center",
                height: 116,
                padding: "0 24px",
                borderRadius: 16,
                background: isActive ? `${s.color}16` : C.panel,
                border: `2.2px solid ${isActive ? s.color : `${s.color}55`}`,
                boxShadow: isActive ? `0 0 24px ${s.color}28` : "none",
                ...rowStyle,
              }}
            >
              {/* Stage badge */}
              <div style={{ fontFamily: FONT, fontSize: 23, fontWeight: 900, color: s.color, paddingRight: 12 }}>
                {s.tag}
              </div>

              {/* Mechanism */}
              <div style={{ fontFamily: FONT, fontSize: 21, fontWeight: 600, color: C.text, lineHeight: 1.35, paddingRight: 16 }}>
                {s.mech}
              </div>

              {/* Throughput */}
              <div>
                <div style={{ fontFamily: MONO, fontSize: 25, fontWeight: 900, color: i === 2 ? K.batch : C.text }}>
                  {s.bw}
                </div>
                <div style={{ fontFamily: MONO, fontSize: 17, color: C.muted, marginTop: 3 }}>
                  {s.bwSub}
                </div>
              </div>

              {/* Latency */}
              <div>
                <div style={{ fontFamily: MONO, fontSize: 23, fontWeight: 800, color: i === 1 || i === 2 ? K.rev : C.text }}>
                  {s.lat}
                </div>
                <div style={{ fontFamily: MONO, fontSize: 17, color: C.muted, marginTop: 3 }}>
                  {s.latSub}
                </div>
              </div>

              {/* Conclusion / Bottleneck */}
              <div
                style={{
                  fontFamily: FONT,
                  fontSize: 21,
                  fontWeight: 800,
                  color: s.noteColor,
                  padding: "10px 14px",
                  borderRadius: 10,
                  background: `${s.noteColor}14`,
                  border: `1.5px solid ${s.noteColor}55`,
                  textAlign: "center",
                }}
              >
                {s.note}
              </div>
            </div>
          );
        })}
      </div>

      {/* ================= 3 ENGINEERING GUARDRAIL CARDS (T[3], y = 590..754) ================= */}
      {kGuard > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 64,
            top: 590,
            width: 1792,
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 24,
          }}
        >
          {GUARDRAILS.map((g, i) => (
            <div
              key={g.num}
              style={{
                height: 162,
                padding: "16px 22px",
                borderRadius: 16,
                background: C.panel,
                border: `2px solid ${g.color}88`,
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                ...appear(t, T[3] + i * 0.22),
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span
                  style={{
                    fontFamily: MONO,
                    fontSize: 18,
                    fontWeight: 900,
                    color: C.bg,
                    background: g.color,
                    padding: "3px 10px",
                    borderRadius: 8,
                  }}
                >
                  红线 {g.num}
                </span>
                <span style={{ fontFamily: FONT, fontSize: 23, fontWeight: 900, color: g.color }}>
                  {g.title}
                </span>
              </div>

              <div
                style={{
                  fontFamily: MONO,
                  fontSize: 19,
                  fontWeight: 700,
                  color: C.text,
                  background: "rgba(255,255,255,0.05)",
                  padding: "6px 12px",
                  borderRadius: 8,
                }}
              >
                {g.code}
              </div>

              <div style={{ fontFamily: FONT, fontSize: 19, color: C.muted, lineHeight: 1.35 }}>
                {g.desc}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ================= BOTTOM CLOSING BANNER (T[4], y = 774..862) ================= */}
      {kClose > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 64,
            top: 774,
            width: 1792,
            height: 86,
            padding: "0 28px",
            borderRadius: 16,
            background: "linear-gradient(90deg, rgba(67,197,158,0.18), rgba(76,201,240,0.16), rgba(249,199,79,0.16))",
            border: `2.5px solid ${K.rev}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            ...appear(t, T[4]),
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            <span
              style={{
                fontFamily: FONT,
                fontSize: 21,
                fontWeight: 900,
                color: C.bg,
                background: K.batch,
                padding: "6px 16px",
                borderRadius: 10,
              }}
            >
              下一步
            </span>
            <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.text }}>
              <span style={{ fontFamily: MONO, color: K.batch }}>&gt; 128 MiB</span> 全冷 DRAM 对照 + 多引擎扩展
            </span>
          </div>

          <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 900, color: K.rev }}>
            CN106 DPU ⇄ Intel DSA 双向 P2P 零主机转发闭环已跑通
          </div>
        </div>
      )}
    </>
  );
}

export const S07: SceneDef = {
  id: "S07",
  title: "全链路总结与工程启示",
  stage: 5,
  source: "三步演进汇总 · 安全红线 · 下一步冷 DRAM",
  dur: DUR,
  cues,
  C: Body,
};
