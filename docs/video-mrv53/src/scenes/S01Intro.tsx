 import React from "react";
 import {
   Arrow,
   C,
   Card,
   Cue,
   Edge,
   FONT,
   K,
   MONO,
   Note,
   PacketStream,
   Pill,
   Pt,
   SceneDef,
   Stage,
   appear,
   fade,
   pop,
   prog,
   useRealT,
   useT,
 } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 10.60, "在绕过 SDP 的 **BAR4/MMIO 直接通路**中，每条 64 B 消息的吞吐到底由谁决定：**主机代码**、**卡代码**，还是**硬件回程**？", "在绕过 S D P 的 B A R 四 直接通路中，每条六十四字节消息的吞吐到底由谁决定：是主机代码、卡代码，还是硬件回程？"], // 0
  [10.95, 19.35, "只有分清谁真正卡在**关键路径**上，我们才知道下一步该优化主机轮询、卡核代码，还是跨 PCIe 的硬件交互。", "只有分清谁卡在关键路径上，才能知道下一步该优化主机轮询、卡核代码，还是跨 P C I e 的硬件交互。"], // 1
  [19.70, 31.08, "实验前我们对三类状态提出假设：卡 1 核**快状态**（`40–50 M/s`）、三个 **`13 M/s` 慢状态**，以及卡 4 核 `W=1024` **中间态**（`22–25 M/s`）。", "我们对三类状态提出假设：一核快状态只有主机在关键路径；三个十三兆慢状态两侧代码都不在关键路径；四核一千零二十四窗口则是两侧代码部分重叠。"], // 2
  [31.43, 40.51, "为了直接给假设定界，MRV-53 设计了三项受控检查：**人为加忙等 `δ` 与延后 `D`**、**主机三段抽样计时**、以及**假硬件批量对齐**。", "为此，M R V 五十三设计了三项检查：人为加忙等德尔塔与延后 D、主机三段抽样计时、以及假硬件批量对齐。"], // 3
  [40.86, 52.26, "经过 **608 次零丢包校准运行**与多模型交叉评审，假设**部分成立**——并揭示了单卡核 `~5 ns` 余量与 4 核加延迟反升的真相。", "经过六百零八次零丢包运行与交叉评审，假设部分成立：既排除了纯回程时间，又测出单卡核约五纳秒余量，以及四核加延迟吞吐反升的真相。"], // 4
];
const DUR = 53.66; // <dur>
// </cues>
 function Body() {
   const t = useT();
   const rt = useRealT();
   const T = cues.map((c) => c[0]);
 
   const k0 = prog(t, T[0], 0.7);
   const fDecision = fade(t, T[1], T[2], 0.45);
   const kDecision = prog(t, T[1], 0.6);
   const kHyp = prog(t, T[2], 0.6);
 
   const reqPts: Pt[] = [
     [530, 240],
     [1390, 240],
   ];
   const repPts: Pt[] = [
     [1390, 294],
     [530, 294],
   ];
 
   const cols = [60, 672, 1284];
   const colCenters = [348, 960, 1572];
 
   return (
     <>
       <Stage>
         {/* Closed-loop 3-stage schematic in upper middle (y: 198..320) */}
         <g opacity={k0}>
           {/* Left Host Box */}
           <rect
             x={60}
             y={200}
             width={470}
             height={118}
             rx={16}
             fill={C.panel}
             stroke={K.host}
             strokeWidth={2.5}
           />
           <rect x={78} y={214} width={175} height={38} rx={8} fill={`${K.host}22`} stroke={K.host} strokeWidth={1.5} />
           <text x={165} y={241} fill={K.host} fontSize={24} fontWeight={800} fontFamily={FONT} textAnchor="middle">
             主机 · saturn
           </text>
           <text x={268} y={241} fill={C.text} fontSize={22} fontWeight={700} fontFamily={MONO}>
             lcore 40 · 3.9 GHz
           </text>
           <text x={82} y={278} fill={C.cyan} fontSize={22} fontWeight={700} fontFamily={MONO}>
             发: MOVDIR64B → BAR4
           </text>
           <text x={82} y={306} fill={C.green} fontSize={22} fontWeight={700} fontFamily={MONO}>
             收: RingReplies::poll
           </text>
 
           {/* Right Card Box */}
           <rect
             x={1390}
             y={200}
             width={470}
             height={118}
             rx={16}
             fill={C.panel}
             stroke={K.card}
             strokeWidth={2.5}
           />
           <rect x={1408} y={214} width={196} height={38} rx={8} fill={`${K.card}22`} stroke={K.card} strokeWidth={1.5} />
           <text x={1506} y={241} fill={K.card} fontSize={24} fontWeight={800} fontFamily={FONT} textAnchor="middle">
             DPU 卡 · CN106xx
           </text>
           <text x={1618} y={241} fill={C.text} fontSize={22} fontWeight={700} fontFamily={MONO}>
             1/4 核 · 2.0 GHz
           </text>
           <text x={1412} y={278} fill={C.cyan} fontSize={22} fontWeight={700} fontFamily={MONO}>
             收: 轮询 BAR4 reqRing
           </text>
           <text x={1412} y={306} fill={K.link} fontSize={22} fontWeight={700} fontFamily={MONO}>
             回: PEM0 WC/ST · DPI DMA
           </text>
 
           {/* PCIe Gen5 x4 Forward & Return Channels */}
           <Arrow x1={535} y1={236} x2={1385} y2={236} color={K.req} w={4} k={k0} />
           <Arrow x1={1385} y1={292} x2={535} y2={292} color={K.rep} w={4} k={k0} />
 
           <PacketStream pts={reqPts} travel={rt * 220} gap={140} size={11} color={K.req} opacity={0.9} />
           <PacketStream pts={repPts} travel={rt * 220 + 70} gap={140} size={11} color={K.rep} opacity={0.9} />
 
           {/* Labels on PCIe paths */}
           <rect x={630} y={197} width={660} height={30} rx={6} fill={`${C.bg}ee`} />
           <text x={960} y={220} fill={K.req} fontSize={24} fontWeight={700} fontFamily={FONT} textAnchor="middle">
             去程：MOVDIR64B 直写 BAR4 请求环 (64 B Cell)
           </text>
 
           <rect x={630} y={247} width={660} height={32} rx={8} fill={C.panel} stroke={K.link} strokeWidth={1.5} />
           <text x={960} y={270} fill={C.yellow} fontSize={24} fontWeight={800} fontFamily={FONT} textAnchor="middle">
             PCIe Gen5 x4 · 周期 1 / 吞吐 (ns/条) 由谁主导？
           </text>
 
           <rect x={630} y={297} width={660} height={28} rx={6} fill={`${C.bg}ee`} />
           <text x={960} y={318} fill={K.rep} fontSize={24} fontWeight={700} fontFamily={FONT} textAnchor="middle">
             回程：写合并 WC / 直写 ST / DPI DMA
           </text>
         </g>
 
         {/* Cue 1: Decision arrows from 3 suspect cards down to decision boxes */}
         {fDecision > 0.01 && (
           <g opacity={fDecision}>
             <Arrow x1={colCenters[0]} y1={450} x2={colCenters[0]} y2={524} color={K.host} w={4} k={kDecision} />
             <Arrow x1={colCenters[1]} y1={450} x2={colCenters[1]} y2={524} color={K.card} w={4} k={kDecision} />
             <Arrow x1={colCenters[2]} y1={450} x2={colCenters[2]} y2={524} color={K.link} w={4} k={kDecision} />
           </g>
         )}
 
         {/* Cue 2+: Subtle vertical guide lines from suspect cards to hypothesis cards */}
         {kHyp > 0.01 && (
           <g opacity={kHyp * 0.65}>
             <Edge x1={colCenters[0]} y1={448} x2={colCenters[0]} y2={462} color={K.fast} w={3} dash="5 5" />
             <Edge x1={colCenters[1]} y1={448} x2={colCenters[1]} y2={462} color={K.slow} w={3} dash="5 5" />
             <Edge x1={colCenters[2]} y1={448} x2={colCenters[2]} y2={462} color={K.bimodal} w={3} dash="5 5" />
           </g>
         )}
 
         {/* Cue 3+: Connecting lines between Check 1/2/3 roadmap capsules */}
         {t >= T[3] && (
           <g opacity={prog(t, T[3], 0.5)}>
             <Arrow x1={752} y1={806} x2={773} y2={806} color={C.cyan} w={3} head={9} />
             <Arrow x1={1292} y1={806} x2={1313} y2={806} color={C.yellow} w={3} head={9} />
           </g>
         )}
       </Stage>
 
       {/* Cue 0: Top Core Question Banner (y: 130..194) */}
       <Card
         x={60}
         y={130}
         w={1800}
         h={64}
         border={C.yellow}
         style={{
           ...appear(t, T[0], undefined, 16),
           padding: "8px 22px",
           display: "flex",
           alignItems: "center",
           justifyContent: "space-between",
           background: "linear-gradient(90deg, rgba(249,199,79,0.14) 0%, rgba(20,27,46,0.96) 55%)",
         }}
       >
         <div style={{ display: "flex", alignItems: "center", gap: 14, whiteSpace: "nowrap" }}>
           <span
             style={{
               background: C.yellow,
               color: C.bg,
               fontWeight: 900,
               fontSize: 20,
               padding: "4px 12px",
               borderRadius: 999,
               fontFamily: FONT,
               whiteSpace: "nowrap",
             }}
           >
             核心问题
           </span>
           <span style={{ fontSize: 26, fontWeight: 800, color: C.text, fontFamily: FONT, whiteSpace: "nowrap" }}>
             <span style={{ fontFamily: MONO, color: C.cyan }}>64 B</span> 闭环直接通路（绕过 SDP）：每条消息的间隔{" "}
             <span style={{ fontFamily: MONO, color: C.yellow }}>1 / 吞吐</span> 到底由谁决定？
           </span>
         </div>
         {t >= T[4] && (
           <div
             style={{
               transform: `scale(${pop(t, T[4], 0.45)})`,
               background: `${K.ok}22`,
               border: `2px solid ${K.ok}`,
               borderRadius: 999,
               padding: "4px 14px",
               fontSize: 20,
               fontWeight: 800,
               color: K.ok,
               fontFamily: MONO,
               whiteSpace: "nowrap",
             }}
           >
             608 次校准运行 (lost=bad=torn=0) → 部分成立
           </div>
         )}
       </Card>
 
       {/* Cue 0: Three Suspect Label Cards (y: 328..446) */}
       <Card
         x={cols[0]}
         y={328}
         w={576}
         h={118}
         border={K.host}
         style={{ ...appear(t, T[0] + 0.15, undefined, 18), padding: "12px 20px" }}
       >
         <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, whiteSpace: "nowrap" }}>
           <span style={{ fontSize: 26, fontWeight: 800, color: K.host, fontFamily: FONT }}>① 主机代码</span>
           <span style={{ fontSize: 22, fontWeight: 700, color: C.muted, fontFamily: MONO }}>Host · lcore 40</span>
         </div>
         <div style={{ fontSize: 24, color: C.text, fontFamily: FONT, whiteSpace: "nowrap" }}>
           <span style={{ fontFamily: MONO, color: C.cyan }}>MOVDIR64B</span> 发送 +{" "}
           <span style={{ fontFamily: MONO, color: C.green }}>RingReplies::poll</span>
         </div>
       </Card>
 
       <Card
         x={cols[1]}
         y={328}
         w={576}
         h={118}
         border={K.card}
         style={{ ...appear(t, T[0] + 0.3, undefined, 18), padding: "12px 20px" }}
       >
         <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, whiteSpace: "nowrap" }}>
           <span style={{ fontSize: 26, fontWeight: 800, color: K.card, fontFamily: FONT }}>② 卡侧代码</span>
           <span style={{ fontSize: 22, fontWeight: 700, color: C.muted, fontFamily: MONO }}>CN106xx · 1/4 核</span>
         </div>
         <div style={{ fontSize: 24, color: C.text, fontFamily: FONT, whiteSpace: "nowrap" }}>
           请求环 <span style={{ fontFamily: MONO, color: C.cyan }}>reqRing</span> 轮询 + 处理写回
         </div>
       </Card>
 
       <Card
         x={cols[2]}
         y={328}
         w={576}
         h={118}
         border={K.link}
         style={{ ...appear(t, T[0] + 0.45, undefined, 18), padding: "12px 20px" }}
       >
         <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, whiteSpace: "nowrap" }}>
           <span style={{ fontSize: 26, fontWeight: 800, color: K.link, fontFamily: FONT }}>③ 硬件回程</span>
           <span style={{ fontSize: 22, fontWeight: 700, color: C.muted, fontFamily: MONO }}>PCIe Gen5 x4</span>
         </div>
         <div style={{ fontSize: 24, color: C.text, fontFamily: FONT, whiteSpace: "nowrap" }}>
           写合并 <span style={{ fontFamily: MONO, color: K.wc }}>WC</span> / 直写{" "}
           <span style={{ fontFamily: MONO, color: K.st }}>ST</span> /{" "}
           <span style={{ fontFamily: MONO, color: K.dpi }}>DPI DMA</span>
         </div>
       </Card>
 
       {/* Cue 1: Why Distinguish the Critical Path? Decision Flowchart (fades out at T[2]) */}
       {fDecision > 0.01 && (
         <div style={{ opacity: fDecision, pointerEvents: "none" }}>
           <Pill
             x={640}
             y={466}
             color={C.yellow}
             size={24}
             solid
             style={{ width: 640, justifyContent: "center" }}
           >
             为什么必须分清关键路径？ → 决定下一步工程优化方向
           </Pill>
 
           <Card x={cols[0]} y={528} w={576} h={212} border={K.host} title="若卡在主机 → 优化轮询批次">
             <div style={{ fontSize: 24, color: C.text, lineHeight: 1.45, fontFamily: FONT }}>
               调整主机 <span style={{ fontFamily: MONO, color: C.cyan }}>RingReplies::poll</span> 单轮拉取批次与跨子环扫描，减少空转等待。
             </div>
           </Card>
 
           <Card x={cols[1]} y={528} w={576} h={212} border={K.card} title="若卡在卡核 → 并行 / 精简卡指令">
             <div style={{ fontSize: 24, color: C.text, lineHeight: 1.45, fontFamily: FONT }}>
               将单卡核扩展为多核子环并行分摊，或精简卡核轮询 <span style={{ fontFamily: MONO, color: C.orange }}>reqRing</span> 与写回指令。
             </div>
           </Card>
 
           <Card x={cols[2]} y={528} w={576} h={212} border={K.link} title="若卡在跨 PCIe 交互 → 调窗口 W 与节流">
             <div style={{ fontSize: 24, color: C.text, lineHeight: 1.45, fontFamily: FONT }}>
               调整并发窗口 <span style={{ fontFamily: MONO, color: K.link }}>W (64 → 1024)</span> 与写回节流，化解跨 PCIe 争用。
             </div>
           </Card>
         </div>
       )}
 
       {/* Cue 2: Three Initial Hypothesis Cards (y: 460..752) + Cue 4 Result Stamps */}
       {t >= T[2] - 0.1 && (
         <>
           {/* Hypothesis 1: 1-core Fast Regime */}
           <Card
             x={cols[0]}
             y={460}
             w={576}
             h={292}
             border={K.fast}
             style={{ ...appear(t, T[2] + 0.1, undefined, 22), padding: "12px 20px" }}
           >
             <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4, whiteSpace: "nowrap" }}>
               <span style={{ fontSize: 25, fontWeight: 800, color: K.fast, fontFamily: FONT }}>
                 假设 1 · 卡 1 核快状态
               </span>
               <span
                 style={{
                   fontSize: 20,
                   fontWeight: 800,
                   color: K.fast,
                   fontFamily: MONO,
                   background: `${K.fast}1f`,
                   padding: "2px 10px",
                   borderRadius: 8,
                 }}
               >
                 WC1 W=1024
               </span>
             </div>
             <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4, whiteSpace: "nowrap" }}>
               <span style={{ fontSize: 44, fontWeight: 900, color: C.text, fontFamily: MONO, lineHeight: 1.05 }}>
                 40.3–50.5
               </span>
               <span style={{ fontSize: 24, fontWeight: 800, color: K.fast, fontFamily: MONO }}>M/s</span>
               <span style={{ fontSize: 22, fontWeight: 600, color: C.muted, fontFamily: MONO }}>(19.8–24.8 ns)</span>
             </div>
             <div style={{ fontSize: 24, color: C.text, fontFamily: FONT, lineHeight: 1.3 }}>
               假设：<strong style={{ color: K.host }}>只有主机代码</strong>在关键路径上
             </div>
 
             {/* Cue 4 Stamp */}
             {t >= T[4] && (
               <div
                 style={{
                   marginTop: 8,
                   padding: "8px 14px",
                   borderRadius: 12,
                   background: "rgba(249, 199, 79, 0.15)",
                   border: `2px solid ${C.yellow}`,
                   transform: `scale(${pop(t, T[4] + 0.1, 0.45)})`,
                   transformOrigin: "left center",
                 }}
               >
                 <div style={{ fontSize: 24, fontWeight: 900, color: C.yellow, fontFamily: FONT, whiteSpace: "nowrap" }}>
                   部分成立：单卡核仅余 <span style={{ fontFamily: MONO }}>~5 ns</span> 紧平衡
                 </div>
                 <div style={{ fontSize: 22, color: C.text, fontFamily: MONO, marginTop: 2, whiteSpace: "nowrap" }}>
                   加 29 ns 忙等即跨过余量切为卡核封顶
                 </div>
               </div>
             )}
           </Card>
 
           {/* Hypothesis 2: Three ~13 M/s Slow Regimes */}
           <Card
             x={cols[1]}
             y={460}
             w={576}
             h={292}
             border={K.slow}
             style={{ ...appear(t, T[2] + 0.28, undefined, 22), padding: "12px 20px" }}
           >
             <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4, whiteSpace: "nowrap" }}>
               <span style={{ fontSize: 25, fontWeight: 800, color: K.slow, fontFamily: FONT }}>
                 假设 2 · 三个慢状态
               </span>
               <span
                 style={{
                   fontSize: 20,
                   fontWeight: 800,
                   color: K.slow,
                   fontFamily: MONO,
                   background: `${K.slow}1f`,
                   padding: "2px 10px",
                   borderRadius: 8,
                 }}
               >
                 W=64 · WC4/ST4/DPI4
               </span>
             </div>
             <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4, whiteSpace: "nowrap" }}>
               <span style={{ fontSize: 44, fontWeight: 900, color: C.text, fontFamily: MONO, lineHeight: 1.05 }}>
                 ~13 M/s
               </span>
               <span style={{ fontSize: 22, fontWeight: 600, color: C.muted, fontFamily: MONO }}>(73.5–76.9 ns/条)</span>
             </div>
             <div style={{ fontSize: 24, color: C.text, fontFamily: FONT, lineHeight: 1.3 }}>
               假设：<strong style={{ color: C.yellow }}>两侧代码都不在关键路径</strong>，延后不降速
             </div>
 
             {/* Cue 4 Stamp */}
             {t >= T[4] && (
               <div
                 style={{
                   marginTop: 8,
                   padding: "8px 14px",
                   borderRadius: 12,
                   background: "rgba(67, 197, 158, 0.16)",
                   border: `2px solid ${K.ok}`,
                   transform: `scale(${pop(t, T[4] + 0.25, 0.45)})`,
                   transformOrigin: "left center",
                 }}
               >
                 <div style={{ fontSize: 24, fontWeight: 900, color: K.ok, fontFamily: FONT, whiteSpace: "nowrap" }}>
                   实测成立 ✓：两侧代码均余量充足
                 </div>
                 <div style={{ fontSize: 22, color: C.text, fontFamily: MONO, marginTop: 2, whiteSpace: "nowrap" }}>
                   卡 +60 ns 斜率 ≈ 0 · D=2.0 µs 变化 &lt;2.5%
                 </div>
               </div>
             )}
           </Card>
 
           {/* Hypothesis 3: Card 4-core W=1024 Intermediate Regime */}
           <Card
             x={cols[2]}
             y={460}
             w={576}
             h={292}
             border={K.bimodal}
             style={{ ...appear(t, T[2] + 0.46, undefined, 22), padding: "12px 20px" }}
           >
             <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4, whiteSpace: "nowrap" }}>
               <span style={{ fontSize: 25, fontWeight: 800, color: K.bimodal, fontFamily: FONT }}>
                 假设 3 · 卡 4 核中间态
               </span>
               <span
                 style={{
                   fontSize: 20,
                   fontWeight: 800,
                   color: K.bimodal,
                   fontFamily: MONO,
                   background: `${K.bimodal}1f`,
                   padding: "2px 10px",
                   borderRadius: 8,
                 }}
               >
                 W=1024 · WC4/ST4/DPI4
               </span>
             </div>
             <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4, whiteSpace: "nowrap" }}>
               <span style={{ fontSize: 44, fontWeight: 900, color: C.text, fontFamily: MONO, lineHeight: 1.05 }}>
                 22.6–25.5
               </span>
               <span style={{ fontSize: 24, fontWeight: 800, color: K.bimodal, fontFamily: MONO }}>M/s</span>
               <span style={{ fontSize: 22, fontWeight: 600, color: C.muted, fontFamily: MONO }}>(39.3–44.3 ns)</span>
             </div>
             <div style={{ fontSize: 24, color: C.text, fontFamily: FONT, lineHeight: 1.3 }}>
               假设：<strong style={{ color: K.bimodal }}>主机与卡代码都有一部分</strong>在关键路径
             </div>
 
             {/* Cue 4 Stamp */}
             {t >= T[4] && (
               <div
                 style={{
                   marginTop: 8,
                   padding: "8px 14px",
                   borderRadius: 12,
                   background: "rgba(76, 201, 240, 0.16)",
                   border: `2px solid ${C.cyan}`,
                   transform: `scale(${pop(t, T[4] + 0.4, 0.45)})`,
                   transformOrigin: "left center",
                 }}
               >
                 <div style={{ fontSize: 24, fontWeight: 900, color: C.cyan, fontFamily: FONT, whiteSpace: "nowrap" }}>
                   推翻：卡核加忙等反升至 <span style={{ fontFamily: MONO }}>35–41 M/s</span>！
                 </div>
                 <div style={{ fontSize: 22, color: C.text, fontFamily: MONO, marginTop: 2, whiteSpace: "nowrap" }}>
                   快慢双态混合 · 卡 +60 ns 达 35.1–40.6 M/s
                 </div>
               </div>
             )}
           </Card>
         </>
       )}
 
       {/* Cue 3: Three Controlled Checks Roadmap (y: 764..850) */}
       {t >= T[3] - 0.1 && (
         <>
           <Card
             x={60}
             y={762}
             w={690}
             h={88}
             border={C.cyan}
             style={{
               ...appear(t, T[3] + 0.05, undefined, 16),
               padding: "10px 18px",
               display: "flex",
               flexDirection: "column",
               justifyContent: "center",
               gap: 4,
             }}
           >
             <div style={{ display: "flex", alignItems: "center", gap: 10, whiteSpace: "nowrap" }}>
               <span
                 style={{
                   background: C.cyan,
                   color: C.bg,
                   fontWeight: 900,
                   fontSize: 20,
                   padding: "2px 10px",
                   borderRadius: 8,
                   fontFamily: FONT,
                 }}
               >
                 检查 1
               </span>
               <span style={{ fontSize: 24, fontWeight: 800, color: C.cyan, fontFamily: FONT }}>
                 受控忙等 <span style={{ fontFamily: MONO }}>δ</span> 与非忙等延后 <span style={{ fontFamily: MONO }}>D</span>
               </span>
             </div>
             <div style={{ fontSize: 22, color: C.muted, fontFamily: MONO, whiteSpace: "nowrap" }}>
               δ: 10/20/40 ns vs 30/45/60 ns · D: 0.5/1.0/2.0 µs
             </div>
           </Card>
 
           <Card
             x={775}
             y={762}
             w={515}
             h={88}
             border={C.yellow}
             style={{
               ...appear(t, T[3] + 0.2, undefined, 16),
               padding: "10px 18px",
               display: "flex",
               flexDirection: "column",
               justifyContent: "center",
               gap: 4,
             }}
           >
             <div style={{ display: "flex", alignItems: "center", gap: 10, whiteSpace: "nowrap" }}>
               <span
                 style={{
                   background: C.yellow,
                   color: C.bg,
                   fontWeight: 900,
                   fontSize: 20,
                   padding: "2px 10px",
                   borderRadius: 8,
                   fontFamily: FONT,
                 }}
               >
                 检查 2
               </span>
               <span style={{ fontSize: 24, fontWeight: 800, color: C.yellow, fontFamily: FONT }}>
                 主机三段抽样计时
               </span>
             </div>
             <div style={{ fontSize: 22, color: C.muted, fontFamily: MONO, whiteSpace: "nowrap" }}>
               --account-every 64 (send/bpoll/tail)
             </div>
           </Card>
 
           <Card
             x={1315}
             y={762}
             w={545}
             h={88}
             border={C.pink}
             style={{
               ...appear(t, T[3] + 0.35, undefined, 16),
               padding: "10px 18px",
               display: "flex",
               flexDirection: "column",
               justifyContent: "center",
               gap: 4,
             }}
           >
             <div style={{ display: "flex", alignItems: "center", gap: 10, whiteSpace: "nowrap" }}>
               <span
                 style={{
                   background: C.pink,
                   color: C.bg,
                   fontWeight: 900,
                   fontSize: 20,
                   padding: "2px 10px",
                   borderRadius: 8,
                   fontFamily: FONT,
                 }}
               >
                 检查 3
               </span>
               <span style={{ fontSize: 24, fontWeight: 800, color: C.pink, fontFamily: FONT }}>
                 假硬件批量对齐
               </span>
             </div>
             <div style={{ fontSize: 22, color: C.muted, fontFamily: MONO, whiteSpace: "nowrap" }}>
               --fake-card --fake-batch K (K = 1..32)
             </div>
           </Card>
         </>
       )}
 
       <Note y={860}>
         实测基准 · logs/mrv53-2026-11-17/runs.jsonl（id = 210..817 共 608 次校准运行，lost = bad = torn = 0）
       </Note>
     </>
   );
 }

export const S01: SceneDef = {
  id: "S01",
  title: "MRV-53：直接通路的吞吐到底由谁决定？",
  stage: undefined,
  source: "MRV-53 · logs/mrv53-2026-11-17/runs.jsonl (608 runs)",
  dur: DUR,
  cues,
  C: Body,
};
