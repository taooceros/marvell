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
  fade,
  prog,
  useRealT,
  useT,
} from "../lib";
import { HwBox, PathTag, PolyArrow, StatusBadge, dsaGeom, dsaRoutes } from "./dsa";

// <cues>
const cues: Cue[] = [
  [0.60, 7.88, "要让卡不经主机 CPU 驱动 `dsa2` 搬运主机内存，第一关是选对硬件队列，并打通**两层独立的 `vmem` 映射**。", "要让卡不经主机核直驱 d s a 二，先选对硬件队列，再打通两层独立的虚拟地址映射。"], // 0
  [8.23, 21.89, "先排雷：**共享队列 SWQ** 只认 `ENQCMD`，普通 PCIe 写会被丢；**专用队列 DWQ**（`dsa2/wq2.0`）要求单笔 64 B 原子写。", "先排雷：共享队列 S W Q 只认 C P U 的 E N Q C M D，普通写会被丢弃；专用队列 D W Q 要求单笔六十四字节原子写，普通非缓存拆成四笔十六字节也会被静默忽略。"], // 1
  [22.24, 36.40, "**映射 1（卡 → DSA 门户）**：`VFIO_IOMMU_MAP_DMA`（仅 `FLAG_WRITE`）把 `BAR2+0x1000` 映进卡 VF0 的 `dmar0`。", "第一层映射打通卡到门户：主机初始化进程用 V F I O 接口，仅开写权限，把 d s a 二的 B A R 二偏移零 x 一千门户页映进卡 V F 零的 d m a r 零页表，得到 portal I O V A。"], // 2
  [36.75, 47.59, "卡侧往 `portal_iova` 发一笔 64 B PCIe 写，同插槽 IIO 经 `dmar0` 翻译后直达 `dsa2` 门户，完全绕开主机 CPU。", "从此卡往 portal I O V A 发一笔六十四字节写请求，同插槽 I I O 经 d m a r 零翻译后点对点直达 d s a 二门户，完全绕开主机核。"], // 3
  [47.94, 59.20, "**映射 2（DSA → 主机数据区）**：描述符里的 `src`、`dst`、`comp` 地址，由 `dsa2` 自己的 `dmar6` 独立翻译。", "第二层映射解决 D S A 访问主机内存：描述符里的源、目的和完成地址，由 d s a 二自带的另一套 I O M M U，d m a r 六独立翻译。"], // 4
  [59.55, 71.49, "主机初始化进程仅调用一次 `open(\"/dev/dsa/wq2.0\")`，将自身页表 `PASID` 经 **SVA** 绑进 `WQCFG`，随后**休眠**。", "主机进程只需启动时打开一次 w q 二点零，借助共享虚拟地址 S V A 把自身页表标识 P A S I D 绑进队列寄存器，随后原地休眠，数据面零参与。"], // 5
  [71.84, 82.86, "卡侧在描述符里直接填主机进程虚拟地址 `VA`，`dsa2` 自动带 `PASID` 查 `dmar6` 搬运，一次跑通、`SWERROR = 0`。", "卡只需在描述符里填普通主机虚拟地址 V A，d s a 二自动带 P A S I D 查 d m a r 六搬运，两套页表接力一次跑通，硬件错误为零。"], // 6
];
const DUR = 84.26; // <dur>
// </cues>

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  // Architecture diagram in upper band (x = 80..1840, y = 134..650)
  const g = dsaGeom(80, 134, 1.0);
  const r = dsaRoutes(g);

  // Beat progress & crossfades
  const kBase = prog(t, T[0], 0.7);
  const kIntroStrip = fade(t, T[0] + 0.3, T[1] - 0.1, 0.5);
  const kMine = fade(t, T[1], T[2] - 0.1, 0.5);

  const kMap1 = prog(t, T[2], 0.7);
  const kMap1Strip = fade(t, T[2], T[4] - 0.1, 0.5);
  const kFwdFlow = prog(t, T[3], 0.7);

  const kMap2 = prog(t, T[4], 0.7);
  const kMap2Strip = fade(t, T[4], T[6] - 0.1, 0.5);
  const kSvaBind = prog(t, T[5], 0.7);
  const kCpuSleep = prog(t, T[5] + 2.2, 0.6);

  const kE2E = prog(t, T[6], 0.7);

  // Active highlights per phase
  const inMap1 = t >= T[2] && t < T[4];
  const inMap2 = t >= T[4] && t < T[6];
  const inE2E = t >= T[6];

  const dmar0Active = t < T[1] || inMap1 || inE2E;
  const dmar6Active = t < T[1] || inMap2 || inE2E;
  const dpiWActive = t >= T[3];
  const dsaActive = t >= T[2];
  const memActive = t >= T[4];
  const hostCpuActive = (t >= T[2] && t < T[3]) || (t >= T[5] && t < T[5] + 2.2);
  const hostCpuSleeping = kCpuSleep > 0.5;
  const hostCpuDim = (t >= T[3] && t < T[5]) || hostCpuSleeping;

  // Control-plane arrows
  const vfioArrowPts: [number, number][] = [
    [g.hostCpu.cx, g.hostCpu.y + g.hostCpu.h],
    [g.dmar0.cx, g.dmar0.y],
  ];
  const svaArrowPts: [number, number][] = [
    [g.hostCpu.x + g.hostCpu.w, g.hostCpu.cy],
    [g.dsa.cx, g.hostCpu.cy],
    [g.dsa.cx, g.dsa.y],
  ];

  // Protocol minefield cards for Beat 1
  const mineCards = [
    {
      title: "共享队列 SWQ",
      req: "仅认 CPU ENQCMD (Non-Posted DMWr)",
      res: "卡发 Posted MWr → 静默丢弃",
      ok: false,
      color: C.red,
    },
    {
      title: "DWQ + /dev/mem",
      req: "Device-nGnRnE 拆成 4 × 16 B",
      res: "非 64 B 原子写 → 静默忽略",
      ok: false,
      color: C.orange,
    },
    {
      title: "专用队列 DWQ (dsa2/wq2.0)",
      req: "单笔 64 B 对齐原子 Posted MWr",
      res: "同插槽 P2P 直通 → 选定方案",
      ok: true,
      color: C.green,
    },
  ];

  return (
    <>
      <Stage>
        {/* Left: CN106 Card Frame */}
        <g opacity={kBase}>
          <rect
            x={g.cardFrame.x}
            y={g.cardFrame.y}
            width={g.cardFrame.w}
            height={g.cardFrame.h}
            rx={18}
            fill="#101729"
            stroke={K.card}
            strokeWidth={2}
            strokeOpacity={0.65}
          />
          <text
            x={g.cardFrame.x + 24}
            y={g.cardFrame.y + 40}
            fill={K.card}
            fontFamily={FONT}
            fontSize={24}
            fontWeight={700}
          >
            Marvell CN106 DPU (crb106-pcie)
          </text>
        </g>

        {/* Center: PCIe Gen5 x4 Link Divider */}
        <g opacity={kBase}>
          <line
            x1={g.pcieX}
            y1={g.cardFrame.y + 44}
            x2={g.pcieX}
            y2={g.cardFrame.y + g.cardFrame.h - 24}
            stroke={K.link}
            strokeWidth={2.5}
            strokeDasharray="9 9"
            opacity={0.42}
          />
          <PathTag
            cx={g.pcieX}
            cy={g.cardFrame.y + 22}
            text="PCIe Gen5 x4 · 同插槽 P2P"
            color={K.link}
            size={20}
          />
        </g>

        {/* Right: Host Xeon Socket 1 Frame */}
        <g opacity={kBase}>
          <rect
            x={g.hostFrame.x}
            y={g.hostFrame.y}
            width={g.hostFrame.w}
            height={g.hostFrame.h}
            rx={18}
            fill="#101729"
            stroke={K.host}
            strokeWidth={2}
            strokeOpacity={0.65}
          />
          <text
            x={g.hostFrame.x + 24}
            y={g.hostFrame.y + 40}
            fill={K.host}
            fontFamily={FONT}
            fontSize={24}
            fontWeight={700}
          >
            Host Xeon Gold 6438M (Socket 1 / NUMA 1)
          </text>
        </g>

        {/* Card blocks */}
        <HwBox
          x={g.core.x}
          y={g.core.y}
          w={g.core.w}
          h={g.core.h}
          title="Neoverse-N2"
          sub="lcore 3 · 2.00 GHz"
          sub2="构造 64 B 描述符"
          accent={K.card}
          active={t >= T[3]}
          opacity={kBase}
        />
        <HwBox
          x={g.dpiW.x}
          y={g.dpiW.y}
          w={g.dpiW.w}
          h={g.dpiW.h}
          title="DPI DMA 写"
          sub="0000:06:00.2"
          sub2="64 B 原子 MWr"
          accent={K.fwd}
          active={dpiWActive}
          badge={dpiWActive ? "ToHost" : undefined}
          opacity={kBase}
        />
        <HwBox
          x={g.bar4.x}
          y={g.bar4.y}
          w={g.bar4.w}
          h={g.bar4.h}
          title="CN106 L3 (TAD)"
          sub="BAR4 slot 15"
          sub2="反向完成区 (S04)"
          accent={K.rev}
          dim={true}
          opacity={kBase * 0.55}
        />
        <HwBox
          x={g.dpiR.x}
          y={g.dpiR.y}
          w={g.dpiR.w}
          h={g.dpiR.h}
          title="DPI DMA 读"
          sub="0000:06:00.1"
          sub2="FromHost 轮询"
          accent={C.muted}
          dim={true}
          opacity={kBase * 0.55}
        />

        {/* Host blocks */}
        <HwBox
          x={g.hostCpu.x}
          y={g.hostCpu.y}
          w={g.hostCpu.w}
          h={g.hostCpu.h}
          title="Host Init 进程"
          sub={hostCpuSleeping ? "休眠 · 数据面零参与" : "仅初始化配置"}
          accent={hostCpuSleeping ? C.dim : K.host}
          active={hostCpuActive}
          sleeping={hostCpuSleeping}
          dim={hostCpuDim}
          opacity={kBase}
        />
        <HwBox
          x={g.dmar0.x}
          y={g.dmar0.y}
          w={g.dmar0.w}
          h={g.dmar0.h}
          title="IOMMU dmar0"
          sub="VF0 group 286"
          sub2="映射 1 · VFIO"
          accent={K.iommu0}
          active={dmar0Active}
          badge="映射 1"
          opacity={kBase}
        />
        <HwBox
          x={g.dsa.x}
          y={g.dsa.y}
          w={g.dsa.w}
          h={g.dsa.h}
          title="Intel DSA (dsa2)"
          sub="DWQ wq2.0 · 0000:f2:01.0"
          sub2={kSvaBind > 0.3 ? "WQCFG.PASID 已绑定" : "BAR2 + 0x1000 门户"}
          accent={K.dsa}
          active={dsaActive}
          badge={kSvaBind > 0.3 ? "SVA PASID" : "DWQ"}
          opacity={kBase}
        />
        <HwBox
          x={g.dmar6.x}
          y={g.dmar6.y}
          w={g.dmar6.w}
          h={g.dmar6.h}
          title="IOMMU dmar6"
          sub="DSA group 18"
          sub2="映射 2 · SVA PASID"
          accent={K.iommu6}
          active={dmar6Active}
          badge="映射 2"
          opacity={kBase}
        />
        <HwBox
          x={g.mem.x}
          y={g.mem.y}
          w={g.mem.w}
          h={g.mem.h}
          title="Host Memzone"
          sub="进程虚拟地址 VA"
          sub2={inE2E ? "src → dst 硬件直搬" : "src_va / dst_va / comp_va"}
          accent={memActive ? K.iommu6 : K.host}
          active={memActive}
          badge={inE2E ? "VA → HPA" : undefined}
          opacity={kBase}
        />

        {/* Beat 2: Host Init -> dmar0 VFIO_IOMMU_MAP_DMA arrow */}
        {kMap1 > 0.01 && (
          <g opacity={inMap1 ? kMap1 : kMap1 * 0.35}>
            <PolyArrow
              pts={vfioArrowPts}
              color={K.iommu0}
              w={3.5}
              k={kMap1}
              dash="7 6"
              head={12}
            />
            {inMap1 && (
              <PathTag
                cx={g.hostCpu.cx}
                cy={(g.hostCpu.y + g.hostCpu.h + g.dmar0.y) / 2}
                text="VFIO_IOMMU_MAP_DMA"
                color={K.iommu0}
                size={15}
                mono
              />
            )}
          </g>
        )}

        {/* Beat 3 & 6: Forward P2P 64 B descriptor flow (DPI -> dmar0 -> dsa2) */}
        {kFwdFlow > 0.01 && (
          <g opacity={kFwdFlow}>
            <PolyArrow
              pts={r.fwdP2P}
              color={K.iommu0}
              w={4.5}
              k={kFwdFlow}
              halo={t < T[4] || inE2E}
            />
            <PolyArrow
              pts={r.dmar0ToDsa}
              color={K.iommu0}
              w={4.5}
              k={prog(t, T[3] + 0.3, 0.5)}
              halo={t < T[4] || inE2E}
            />
            <PacketStream
              pts={r.fwdP2P}
              travel={rt * 170}
              gap={62}
              size={13}
              color={K.iommu0}
              opacity={t < T[4] || inE2E ? 1 : 0.45}
            />
            <PacketStream
              pts={r.dmar0ToDsa}
              travel={rt * 170}
              gap={48}
              size={12}
              color={K.iommu0}
              opacity={t < T[4] || inE2E ? 1 : 0.45}
            />
            <PathTag
              cx={g.pcieX}
              cy={g.dmar0.cy - 28}
              text="64 B MWr → portal_iova"
              color={K.iommu0}
              size={18}
              mono
            />
          </g>
        )}

        {/* Beat 4 & 6: Mapping 2 (dsa2 -> dmar6 -> Host Memzone) */}
        {kMap2 > 0.01 && (
          <g opacity={kMap2}>
            <PolyArrow
              pts={r.dsaToDmar6}
              color={K.iommu6}
              w={4.5}
              k={kMap2}
              halo={inMap2 || inE2E}
            />
            <PolyArrow
              pts={r.dmar6ToMem}
              color={K.iommu6}
              w={4.5}
              k={prog(t, T[4] + 0.3, 0.5)}
              halo={inMap2 || inE2E}
            />
            {inE2E && (
              <>
                <PacketStream
                  pts={r.dsaToDmar6}
                  travel={rt * 150}
                  gap={42}
                  size={12}
                  color={K.iommu6}
                />
                <PacketStream
                  pts={r.dmar6ToMem}
                  travel={rt * 150}
                  gap={42}
                  size={12}
                  color={K.iommu6}
                />
              </>
            )}
            <PathTag
              cx={g.dmar6.cx + 156}
              cy={g.dmar6.y + g.dmar6.h + 16}
              text="VA + PASID → HPA"
              color={K.iommu6}
              size={17}
              mono
            />
          </g>
        )}

        {/* Beat 5: One-time open("/dev/dsa/wq2.0") -> WQCFG.PASID */}
        {kSvaBind > 0.01 && (
          <g opacity={inMap2 ? kSvaBind * (1 - 0.55 * kCpuSleep) : kSvaBind * 0.25}>
            <PolyArrow
              pts={svaArrowPts}
              color={K.iommu6}
              w={3.5}
              k={kSvaBind}
              dash="8 6"
              head={12}
            />
            {inMap2 && (
              <PathTag
                cx={(g.hostCpu.x + g.hostCpu.w + g.dsa.cx) / 2 + 24}
                cy={g.hostCpu.cy - 24}
                text='open("/dev/dsa/wq2.0") → PASID'
                color={K.iommu6}
                size={17}
                mono
              />
            )}
          </g>
        )}

        {/* Beat 6: Verified check badge in bottom banner */}
        {kE2E > 0.01 && (
          <StatusBadge cx={142} cy={768} ok={true} r={28} opacity={kE2E} />
        )}
      </Stage>

      {/* ==================== BOTTOM DETAIL BAND (y = 670..866) ==================== */}

      {/* Beat 0: Two-layer IOMMU intro banner */}
      {kIntroStrip > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 80,
            top: 682,
            width: 1760,
            height: 168,
            borderRadius: 16,
            background: "rgba(20, 27, 46, 0.94)",
            border: `2px solid ${C.line}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 44px",
            boxSizing: "border-box",
            opacity: kIntroStrip,
            fontFamily: FONT,
          }}
        >
          <div>
            <div style={{ fontSize: 22, color: C.muted, fontWeight: 600 }}>
              核心架构 · 两套独立 IOMMU 页表接力
            </div>
            <div style={{ fontSize: 34, color: C.text, fontWeight: 800, marginTop: 8 }}>
              <span style={{ color: K.iommu0 }}>映射 1：dmar0 (VFIO)</span>
              <span style={{ color: C.dim, margin: "0 20px" }}>+</span>
              <span style={{ color: K.iommu6 }}>映射 2：dmar6 (SVA PASID)</span>
            </div>
          </div>
          <div
            style={{
              padding: "14px 28px",
              borderRadius: 12,
              background: `${K.link}18`,
              border: `1.5px solid ${K.link}77`,
              fontSize: 24,
              fontWeight: 700,
              color: K.link,
            }}
          >
            全程零主机 CPU 转发
          </div>
        </div>
      )}

      {/* Beat 1: Protocol minefield 3-card comparison */}
      {kMine > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 80,
            top: 672,
            width: 1760,
            display: "flex",
            gap: 31,
            opacity: kMine,
            fontFamily: FONT,
          }}
        >
          {mineCards.map((m, i) => (
            <div
              key={m.title}
              style={{
                flex: 1,
                height: 192,
                borderRadius: 16,
                background: m.ok ? "rgba(67, 197, 158, 0.10)" : "rgba(20, 27, 46, 0.94)",
                border: `2px solid ${m.color}`,
                padding: "20px 26px",
                boxSizing: "border-box",
                ...appear(t, T[1] + i * 0.35, undefined, 16),
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                <div style={{ fontSize: 26, fontWeight: 800, color: m.color }}>
                  {m.title}
                </div>
                <svg width={42} height={42} viewBox="0 0 42 42" style={{ flexShrink: 0 }}>
                  <StatusBadge cx={21} cy={21} ok={m.ok} r={19} opacity={1} />
                </svg>
              </div>
              <div
                style={{
                  fontSize: 20,
                  fontFamily: MONO,
                  color: C.text,
                  marginTop: 14,
                }}
              >
                {m.req}
              </div>
              <div
                style={{
                  fontSize: 22,
                  fontWeight: 700,
                  color: m.ok ? C.green : C.muted,
                  marginTop: 14,
                }}
              >
                {m.res}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Beat 2 & 3: Mapping 1 (VFIO_IOMMU_MAP_DMA -> dmar0) detail card */}
      {kMap1Strip > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 80,
            top: 674,
            width: 1760,
            height: 190,
            borderRadius: 16,
            background: "rgba(20, 27, 46, 0.95)",
            border: `2px solid ${K.iommu0}`,
            padding: "20px 34px",
            boxSizing: "border-box",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 32,
            opacity: kMap1Strip,
            fontFamily: FONT,
          }}
        >
          <div style={{ flex: 1.35 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <span
                style={{
                  padding: "4px 14px",
                  borderRadius: 999,
                  background: `${K.iommu0}26`,
                  border: `1.5px solid ${K.iommu0}`,
                  color: K.iommu0,
                  fontSize: 20,
                  fontWeight: 800,
                }}
              >
                映射 1 · 卡 → DSA 门户
              </span>
              <span style={{ fontFamily: MONO, fontSize: 20, color: C.yellow }}>
                VFIO_IOMMU_MAP_DMA (FLAG_WRITE 只写)
              </span>
            </div>
            <div
              style={{
                marginTop: 18,
                padding: "14px 20px",
                borderRadius: 12,
                background: "#0b1122",
                border: `1.5px solid ${K.iommu0}66`,
                fontFamily: MONO,
                fontSize: 22,
                color: C.text,
                display: "flex",
                alignItems: "center",
                gap: 16,
              }}
            >
              <span style={{ color: K.iommu0, fontWeight: 700 }}>dmar0 (VF0 g286):</span>
              <span>portal_iova</span>
              <span style={{ color: C.yellow, fontWeight: 700 }}>0x117fe00000</span>
              <span style={{ color: K.iommu0 }}>→</span>
              <span>HPA</span>
              <span style={{ color: C.green, fontWeight: 700 }}>0x237ffff21000</span>
              <span style={{ color: C.muted, fontSize: 19 }}>(BAR2+0x1000)</span>
            </div>
          </div>

          <div
            style={{
              width: 460,
              height: 132,
              borderRadius: 14,
              background: kFwdFlow > 0.2 ? "rgba(76, 201, 240, 0.14)" : "rgba(15, 22, 40, 0.85)",
              border: `2px solid ${kFwdFlow > 0.2 ? K.iommu0 : C.line}`,
              padding: "16px 24px",
              boxSizing: "border-box",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
            }}
          >
            <div style={{ fontSize: 22, fontWeight: 800, color: kFwdFlow > 0.2 ? K.iommu0 : C.muted }}>
              {kFwdFlow > 0.2 ? "数据面直达 · 绕开主机 CPU" : "权限最小化护栏"}
            </div>
            <div style={{ fontSize: 20, color: C.text, marginTop: 8, lineHeight: 1.4 }}>
              {kFwdFlow > 0.2
                ? "64 B Posted MWr 经同插槽 IIO + dmar0 直入 dsa2/wq2.0 门户"
                : "仅授 FLAG_WRITE 写权限，防止卡侧误读门户页触发异常"}
            </div>
          </div>
        </div>
      )}

      {/* Beat 4 & 5: Mapping 2 (64 B descriptor VA fields + SVA PASID binding) */}
      {kMap2Strip > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 80,
            top: 674,
            width: 1760,
            height: 190,
            display: "flex",
            gap: 28,
            opacity: kMap2Strip,
            fontFamily: FONT,
          }}
        >
          {/* Left: 64 B descriptor fields */}
          <div
            style={{
              flex: 1.05,
              borderRadius: 16,
              background: "rgba(20, 27, 46, 0.95)",
              border: `2px solid ${K.iommu6}`,
              padding: "18px 26px",
              boxSizing: "border-box",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontSize: 22, fontWeight: 800, color: K.iommu6 }}>
                映射 2 · 64 B 描述符内嵌主机 VA
              </span>
              <span style={{ fontFamily: MONO, fontSize: 19, color: C.muted }}>
                由 dmar6 (group 18) 独立翻译
              </span>
            </div>
            <div style={{ display: "flex", gap: 12, marginTop: 20 }}>
              {[
                { k: "opcode", v: "MEMMOVE", hl: false },
                { k: "src_va", v: "Host VA", hl: true },
                { k: "dst_va", v: "Host VA", hl: true },
                { k: "comp_va", v: "Host VA", hl: true },
                { k: "size", v: "4 KiB", hl: false },
              ].map((f) => (
                <div
                  key={f.k}
                  style={{
                    flex: 1,
                    borderRadius: 10,
                    padding: "12px 10px",
                    background: f.hl ? `${K.iommu6}22` : "#0c1324",
                    border: `1.5px solid ${f.hl ? K.iommu6 : C.line}`,
                    textAlign: "center",
                  }}
                >
                  <div style={{ fontFamily: MONO, fontSize: 19, fontWeight: 700, color: f.hl ? K.iommu6 : C.muted }}>
                    {f.k}
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 17, color: C.text, marginTop: 6 }}>
                    {f.v}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Right: SVA PASID one-time binding & sleep */}
          <div
            style={{
              flex: 0.95,
              borderRadius: 16,
              background: kSvaBind > 0.2 ? "rgba(179, 136, 255, 0.11)" : "rgba(20, 27, 46, 0.95)",
              border: `2px solid ${kSvaBind > 0.2 ? K.iommu6 : C.line}`,
              padding: "18px 26px",
              boxSizing: "border-box",
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
            }}
          >
            <div style={{ fontSize: 22, fontWeight: 800, color: kSvaBind > 0.2 ? K.iommu6 : C.muted }}>
              SVA 一次性绑定流程 (idxd.sva=Y)
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 14, fontFamily: MONO, fontSize: 19 }}>
              <div
                style={{
                  padding: "10px 14px",
                  borderRadius: 10,
                  background: "#0c1324",
                  border: `1.5px solid ${K.iommu6}88`,
                  color: C.text,
                }}
              >
                1. open("/dev/dsa/wq2.0")
              </div>
              <span style={{ color: K.iommu6, fontWeight: 800 }}>→</span>
              <div
                style={{
                  padding: "10px 14px",
                  borderRadius: 10,
                  background: "#0c1324",
                  border: `1.5px solid ${K.iommu6}88`,
                  color: C.yellow,
                }}
              >
                2. WQCFG.PASID
              </div>
              <span style={{ color: K.iommu6, fontWeight: 800 }}>→</span>
              <div
                style={{
                  padding: "10px 14px",
                  borderRadius: 10,
                  background: hostCpuSleeping ? `${C.green}22` : "#0c1324",
                  border: `1.5px solid ${hostCpuSleeping ? C.green : C.line}`,
                  color: hostCpuSleeping ? C.green : C.muted,
                  fontWeight: 700,
                }}
              >
                3. 进程休眠
              </div>
            </div>
            <div style={{ fontSize: 19, color: C.muted }}>
              主机初始化进程绑定自身页表后原地休眠，整个数据面运行期间零 CPU 参与
            </div>
          </div>
        </div>
      )}

      {/* Beat 6: End-to-end verified banner */}
      {kE2E > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 80,
            top: 682,
            width: 1760,
            height: 172,
            borderRadius: 18,
            background: "rgba(67, 197, 158, 0.12)",
            border: `2.5px solid ${C.green}`,
            padding: "0 44px 0 112px",
            boxSizing: "border-box",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            opacity: kE2E,
            fontFamily: FONT,
          }}
        >
          <div>
            <div style={{ fontSize: 32, fontWeight: 900, color: C.green }}>
              零主机转发 · 一次跑通 · SWERROR = 0
            </div>
            <div style={{ fontSize: 22, color: C.text, marginTop: 10 }}>
              卡填普通主机 <span style={{ fontFamily: MONO, color: K.iommu6 }}>VA</span> →{" "}
              <span style={{ fontFamily: MONO, color: K.iommu0 }}>dmar0</span> 送达门户 →{" "}
              <span style={{ fontFamily: MONO, color: K.dsa }}>dsa2</span> 自动带{" "}
              <span style={{ fontFamily: MONO, color: K.iommu6 }}>WQCFG.PASID</span> 查{" "}
              <span style={{ fontFamily: MONO, color: K.iommu6 }}>dmar6</span> 搬运
            </div>
          </div>
          <div
            style={{
              padding: "14px 28px",
              borderRadius: 14,
              background: "#0c1324",
              border: `2px solid ${C.green}`,
              fontFamily: MONO,
              fontSize: 28,
              fontWeight: 900,
              color: C.green,
            }}
          >
            SWERROR = 0
          </div>
        </div>
      )}
    </>
  );
}

export const S02: SceneDef = {
  id: "S02",
  title: "核心难题 1：两层虚拟地址映射",
  stage: 1,
  source: "dmar0 (VFIO) + dmar6 (SVA PASID) · 零主机转发",
  dur: DUR,
  cues,
  C: Body,
};
