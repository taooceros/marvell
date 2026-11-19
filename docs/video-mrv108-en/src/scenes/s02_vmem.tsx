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
  [0.60, 11.10, "To drive `dsa2` directly from the DPU without the host CPU, we must pick the right queue mode and wire **two independent vmem mappings**.", "To drive d s a two directly from the D P U without host C P U forwarding, we first pick the right hardware queue and then wire two independent virtual memory mappings."], // 0
  [11.45, 35.55, "Protocol trap: **SWQ** requires CPU-only `ENQCMD` (`DMWr`), and `/dev/mem` splits writes into 4 x 16 B—both are silently dropped. We must use **DWQ** (`dsa2/wq2.0`).", "First, two protocol traps: a shared work queue, or S W Q, only accepts C P U-only E N Q C M D writes, and uncached device memory splits writes into four sixteen-byte pieces. Both are silently dropped, so we must use a dedicated work queue, d s a two w q two point zero, which takes a single sixty-four-byte atomic write."], // 1
  [35.90, 56.36, "**Mapping 1 (Card -> Portal)**: `VFIO_IOMMU_MAP_DMA` (`FLAG_WRITE` only) maps `BAR2 + 0x1000` (`HPA 0x237ffff21000`) into card VF0's `dmar0` as `portal_iova`.", "Mapping one connects the card to the D S A portal. At startup, a host init process uses V F I O I O M M U map D M A with write-only permission to map B A R two plus zero x one thousand into card V F zero's d m a r zero page table, creating portal I O V A."], // 2
  [56.71, 73.07, "When the card issues a 64 B PCIe write to `portal_iova`, same-socket IIO translates it via `dmar0` and routes it straight into `dsa2`—bypassing the host CPU.", "Now when the card sends a sixty-four-byte P C I e write to portal I O V A, the same-socket I I O translates it through d m a r zero and routes it straight into d s a two's portal, completely bypassing the host C P U."], // 3
  [73.42, 88.10, "**Mapping 2 (DSA -> Host Buffers)**: `src`, `dst`, and `comp` inside the 64 B descriptor are translated separately by `dsa2`'s own IOMMU, `dmar6`.", "Mapping two lets D S A access host buffers. The source, destination, and completion addresses inside the sixty-four-byte descriptor are translated by a completely separate I O M M U, d m a r six."], // 4
  [88.45, 104.33, "The host init process calls `open(\"/dev/dsa/wq2.0\")` once, binding its page-table `PASID` into `WQCFG.PASID` via **SVA**, then **sleeps** during the datapath.", "The host process calls open on w q two point zero just once. Shared virtual addressing, or S V A, binds its page-table P A S I D into the work queue configuration, and the process then sleeps with zero datapath involvement."], // 5
  [104.68, 121.66, "The card simply fills ordinary host process `VA`s; `dsa2` attaches `PASID` automatically and walks `dmar6`—working on the first try with `SWERROR = 0`.", "The card simply writes ordinary host virtual addresses into the descriptor, and d s a two automatically attaches that P A S I D to walk d m a r six. Both page tables relay seamlessly, with S W E R R O R staying zero."], // 6
];
const DUR = 123.06; // <dur>
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

  // Protocol trap cards for Beat 1
  const mineCards = [
    {
      title: "Shared Queue (SWQ)",
      req: "CPU ENQCMD only (Non-Posted DMWr)",
      res: "Card Posted MWr → Silently Dropped",
      ok: false,
      color: C.red,
    },
    {
      title: "DWQ + /dev/mem",
      req: "Device-nGnRnE splits into 4 × 16 B",
      res: "Non-64 B atomic → Silently Ignored",
      ok: false,
      color: C.orange,
    },
    {
      title: "DWQ (dsa2/wq2.0)",
      req: "Single 64 B aligned atomic Posted MWr",
      res: "Same-socket P2P direct → Selected",
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
            text="PCIe Gen5 x4 · Same-Socket P2P"
            color={K.link}
            size={19}
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
          sub2="Builds 64 B descriptor"
          accent={K.card}
          active={t >= T[3]}
          opacity={kBase}
        />
        <HwBox
          x={g.dpiW.x}
          y={g.dpiW.y}
          w={g.dpiW.w}
          h={g.dpiW.h}
          title="DPI DMA Write"
          sub="0000:06:00.2"
          sub2="64 B atomic MWr"
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
          sub2="Reverse comp (S04)"
          accent={K.rev}
          dim={true}
          opacity={kBase * 0.55}
        />
        <HwBox
          x={g.dpiR.x}
          y={g.dpiR.y}
          w={g.dpiR.w}
          h={g.dpiR.h}
          title="DPI DMA Read"
          sub="0000:06:00.1"
          sub2="FromHost polling"
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
          title="Host Init Process"
          sub={hostCpuSleeping ? "Sleeping · Zero Datapath" : "Init setup only"}
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
          sub2="Map 1 · VFIO"
          accent={K.iommu0}
          active={dmar0Active}
          badge="Map 1"
          opacity={kBase}
        />
        <HwBox
          x={g.dsa.x}
          y={g.dsa.y}
          w={g.dsa.w}
          h={g.dsa.h}
          title="Intel DSA (dsa2)"
          sub="DWQ wq2.0 · 0000:f2:01.0"
          sub2={kSvaBind > 0.3 ? "WQCFG.PASID bound" : "BAR2 + 0x1000 portal"}
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
          sub2="Map 2 · SVA PASID"
          accent={K.iommu6}
          active={dmar6Active}
          badge="Map 2"
          opacity={kBase}
        />
        <HwBox
          x={g.mem.x}
          y={g.mem.y}
          w={g.mem.w}
          h={g.mem.h}
          title="Host Memzone"
          sub="Process virtual addr VA"
          sub2={inE2E ? "src → dst HW copy" : "src_va / dst_va / comp_va"}
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
              Core Architecture · Two Independent IOMMU Page-Table Relays
            </div>
            <div style={{ fontSize: 32, color: C.text, fontWeight: 800, marginTop: 8 }}>
              <span style={{ color: K.iommu0 }}>Map 1: dmar0 (VFIO)</span>
              <span style={{ color: C.dim, margin: "0 20px" }}>+</span>
              <span style={{ color: K.iommu6 }}>Map 2: dmar6 (SVA PASID)</span>
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
            Zero Host CPU Forwarding
          </div>
        </div>
      )}

      {/* Beat 1: Protocol trap 3-card comparison */}
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
                <div style={{ fontSize: 25, fontWeight: 800, color: m.color }}>
                  {m.title}
                </div>
                <svg width={42} height={42} viewBox="0 0 42 42" style={{ flexShrink: 0 }}>
                  <StatusBadge cx={21} cy={21} ok={m.ok} r={19} opacity={1} />
                </svg>
              </div>
              <div
                style={{
                  fontSize: 19,
                  fontFamily: MONO,
                  color: C.text,
                  marginTop: 14,
                }}
              >
                {m.req}
              </div>
              <div
                style={{
                  fontSize: 21,
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
                Map 1 · Card → DSA Portal
              </span>
              <span style={{ fontFamily: MONO, fontSize: 19, color: C.yellow }}>
                VFIO_IOMMU_MAP_DMA (FLAG_WRITE only)
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
                fontSize: 21,
                color: C.text,
                display: "flex",
                alignItems: "center",
                gap: 14,
              }}
            >
              <span style={{ color: K.iommu0, fontWeight: 700 }}>dmar0 (VF0 g286):</span>
              <span>portal_iova</span>
              <span style={{ color: C.yellow, fontWeight: 700 }}>0x117fe00000</span>
              <span style={{ color: K.iommu0 }}>→</span>
              <span>HPA</span>
              <span style={{ color: C.green, fontWeight: 700 }}>0x237ffff21000</span>
              <span style={{ color: C.muted, fontSize: 18 }}>(BAR2+0x1000)</span>
            </div>
          </div>

          <div
            style={{
              width: 470,
              height: 132,
              borderRadius: 14,
              background: kFwdFlow > 0.2 ? "rgba(76, 201, 240, 0.14)" : "rgba(15, 22, 40, 0.85)",
              border: `2px solid ${kFwdFlow > 0.2 ? K.iommu0 : C.line}`,
              padding: "16px 22px",
              boxSizing: "border-box",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
            }}
          >
            <div style={{ fontSize: 21, fontWeight: 800, color: kFwdFlow > 0.2 ? K.iommu0 : C.muted }}>
              {kFwdFlow > 0.2 ? "Direct Datapath · Bypass Host CPU" : "Least-Privilege Guardrail"}
            </div>
            <div style={{ fontSize: 18, color: C.text, marginTop: 8, lineHeight: 1.4 }}>
              {kFwdFlow > 0.2
                ? "64 B Posted MWr via same-socket IIO + dmar0 straight to dsa2/wq2.0"
                : "FLAG_WRITE only prevents accidental card reads of the portal page"}
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
              <span style={{ fontSize: 21, fontWeight: 800, color: K.iommu6 }}>
                Map 2 · Host VAs in 64 B Descriptor
              </span>
              <span style={{ fontFamily: MONO, fontSize: 18, color: C.muted }}>
                Translated by dmar6 (group 18)
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
            <div style={{ fontSize: 21, fontWeight: 800, color: kSvaBind > 0.2 ? K.iommu6 : C.muted }}>
              One-Time SVA Binding (idxd.sva=Y)
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, fontFamily: MONO, fontSize: 17 }}>
              <div
                style={{
                  padding: "10px 12px",
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
                  padding: "10px 12px",
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
                  padding: "10px 12px",
                  borderRadius: 10,
                  background: hostCpuSleeping ? `${C.green}22` : "#0c1324",
                  border: `1.5px solid ${hostCpuSleeping ? C.green : C.line}`,
                  color: hostCpuSleeping ? C.green : C.muted,
                  fontWeight: 700,
                }}
              >
                3. Sleep
              </div>
            </div>
            <div style={{ fontSize: 18, color: C.muted }}>
              Host init binds its page table once, then sleeps—zero CPU in datapath
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
            <div style={{ fontSize: 30, fontWeight: 900, color: C.green }}>
              Zero Host Forwarding · Works on First Try · SWERROR = 0
            </div>
            <div style={{ fontSize: 21, color: C.text, marginTop: 10 }}>
              Card fills Host <span style={{ fontFamily: MONO, color: K.iommu6 }}>VA</span> →{" "}
              <span style={{ fontFamily: MONO, color: K.iommu0 }}>dmar0</span> routes to portal →{" "}
              <span style={{ fontFamily: MONO, color: K.dsa }}>dsa2</span> attaches{" "}
              <span style={{ fontFamily: MONO, color: K.iommu6 }}>WQCFG.PASID</span> &amp; walks{" "}
              <span style={{ fontFamily: MONO, color: K.iommu6 }}>dmar6</span>
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
  title: "Challenge 1: Two-Layer vmem Mapping",
  stage: 1,
  source: "dmar0 (VFIO) + dmar6 (SVA PASID) · Zero Host Proxy",
  dur: DUR,
  cues,
  C: Body,
};
