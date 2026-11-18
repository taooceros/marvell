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
   Pt,
   SceneDef,
   Stage,
   appear,
   lerp,
   pop,
   prog,
   useRealT,
   useT,
 } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 9.88, "在先前的 `MRV-35` 中，我们把真实 PCIe 硬件替换成**立刻返回的本地内存操作**（假硬件），单独测量纯软件耗时。", "在先前的 M R V 三十五中，我们把真实硬件替换成立刻返回的本地内存操作，也就是假硬件，用来单测纯软件耗时。"], // 0
  [10.23, 19.93, "旧假硬件测出：主机每次轮询拿 `K=1` 条需 **`28.7 ns/条`**，拿满 `K=32` 条仅需 **`13.2 ns/条`**；单卡核纯软件也只需十几纳秒。", "旧假硬件测出，主机每轮拿一条需二十八点七纳秒，拿满三十二条每条仅十三点二纳秒；单卡核纯软件也只需十几纳秒。"], // 1
  [20.28, 29.12, "这种“消去法”只能做**排除**：当真硬件每条间隔高达 **`76 ns`**（`13 M/s`）时，两侧十几纳秒的纯代码显然都没算力封顶。", "这种消去法只能做单向排除：当真硬件间隔高达七十六纳秒时，两侧十几纳秒的纯代码显然都没算力封顶。"], // 2
  [29.47, 37.77, "一旦两侧纯代码耗时都比实测间隔（`1 / 吞吐`）短，静态对比就失灵了：**究竟是谁在决定周期，谁又藏在流水线空隙里？**", "但只要两侧代码耗时都比实测间隔短，静态对比就分不清了：到底是谁在决定周期，谁又藏在流水线空隙里？"], // 3
  [38.12, 48.12, "最直接的办法就是**微扰注入法**：不改产品循环结构，只在主机或卡上人为插入已知耗时 `+δ`，直接看 `1 / 吞吐` 跟不跟着变。", "最直接的办法就是微扰注入法：不改循环结构，只在主机或卡上插入已知的微延迟德尔塔，直接看消息间隔跟不跟着一比一增加。"], // 4
];
const DUR = 49.52; // <dur>
// </cues>

 function Body() {
   const t = useT();
   const rt = useRealT();
   const T = cues.map((c) => c[0]);
 
   const k0 = prog(t, T[0], 0.7);
   const k1 = prog(t, T[1], 0.7);
   const k2 = prog(t, T[2], 0.7);
   const k3 = prog(t, T[3], 0.7);
   const k4 = prog(t, T[4], 0.6);
 
   const fakeLoopPts: Pt[] = [
     [285, 336],
     [565, 336],
   ];
 
   return (
     <>
       {/* Cue 0: Left Top Card — MRV-35 Fake Hardware Elimination Method */}
       <Card
         x={60}
         y={130}
         w={815}
         h={278}
         border={C.cyan}
         title="MRV-35 假硬件（--fake-card）消去法原理"
         titleColor={C.cyan}
         style={{ ...appear(t, T[0], undefined, 18), padding: "14px 22px" }}
       />
 
       {/* Cue 1: Left Bottom Card — Pure Software Cost Comparison */}
       {t >= T[1] - 0.1 && (
         <Card
           x={60}
           y={420}
           w={815}
           h={274}
           border={K.host}
           title="旧假硬件实测：纯软件耗时条形对比"
           titleColor={K.host}
           style={{ ...appear(t, T[1], undefined, 18), padding: "14px 22px" }}
         >
           <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 4 }}>
             {/* Bar 1: K = 1 (28.7 ns/条 at 44px) */}
             <div>
               <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                 <span style={{ color: C.text, fontWeight: 700, fontSize: 24, fontFamily: FONT }}>
                   主机每轮拿 <span style={{ fontFamily: MONO, color: C.yellow }}>K = 1</span> 条
                 </span>
                 <span style={{ whiteSpace: "nowrap" }}>
                   <span style={{ fontFamily: MONO, fontWeight: 900, color: C.yellow, fontSize: 44, lineHeight: 1 }}>
                     28.7
                   </span>
                   <span style={{ fontSize: 24, color: C.yellow, fontWeight: 800, fontFamily: FONT, marginLeft: 4 }}>
                     ns/条
                   </span>
                   <span style={{ fontSize: 24, color: C.muted, fontWeight: 600, fontFamily: MONO, marginLeft: 8 }}>
                     (34.8 M/s)
                   </span>
                 </span>
               </div>
               <div style={{ width: "100%", height: 18, background: "#0b1020", borderRadius: 8, overflow: "hidden", border: `1px solid ${C.line}` }}>
                 <div
                   style={{
                     width: `${lerp(0, 68, k1)}%`,
                     height: "100%",
                     background: `linear-gradient(90deg, ${K.host}, ${C.yellow})`,
                     borderRadius: 8,
                   }}
                 />
               </div>
             </div>
 
             {/* Bar 2: K = 32 (13.2 ns/条 at 44px) */}
             <div>
               <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                 <span style={{ color: C.text, fontWeight: 700, fontSize: 24, fontFamily: FONT }}>
                   主机拿满 <span style={{ fontFamily: MONO, color: C.cyan }}>K = 32</span> 条
                 </span>
                 <span style={{ whiteSpace: "nowrap" }}>
                   <span style={{ fontFamily: MONO, fontWeight: 900, color: C.cyan, fontSize: 44, lineHeight: 1 }}>
                     13.2
                   </span>
                   <span style={{ fontSize: 24, color: C.cyan, fontWeight: 800, fontFamily: FONT, marginLeft: 4 }}>
                     ns/条
                   </span>
                   <span style={{ fontSize: 24, color: C.muted, fontWeight: 600, fontFamily: MONO, marginLeft: 8 }}>
                     (75.8 M/s)
                   </span>
                 </span>
               </div>
               <div style={{ width: "100%", height: 18, background: "#0b1020", borderRadius: 8, overflow: "hidden", border: `1px solid ${C.line}` }}>
                 <div
                   style={{
                     width: `${lerp(0, 31.5, k1)}%`,
                     height: "100%",
                     background: C.cyan,
                     borderRadius: 8,
                   }}
                 />
               </div>
             </div>
 
             {/* Bar 3: Single Card Core */}
             <div>
               <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                 <span style={{ color: C.text, fontWeight: 700, fontSize: 24, fontFamily: FONT }}>
                   单卡核纯软件耗时
                 </span>
                 <span style={{ fontFamily: FONT, fontWeight: 900, color: K.card, fontSize: 26 }}>
                   十几纳秒/条
                 </span>
               </div>
               <div style={{ width: "100%", height: 18, background: "#0b1020", borderRadius: 8, overflow: "hidden", border: `1px solid ${C.line}` }}>
                 <div
                   style={{
                     width: `${lerp(0, 38, k1)}%`,
                     height: "100%",
                     background: K.card,
                     borderRadius: 8,
                   }}
                 />
               </div>
             </div>
           </div>
         </Card>
       )}
 
       {/* Cue 2: Right Top Card — When Elimination Works (13 M/s, 76 ns) */}
       {t >= T[2] - 0.1 && (
         <Card
           x={900}
           y={130}
           w={960}
           h={278}
           border={K.ok}
           title="消去法何时有效：13 M/s 慢状态（每条间隔 76 ns）"
           titleColor={K.ok}
           style={{ ...appear(t, T[2], undefined, 18), padding: "14px 22px" }}
         />
       )}
 
       {/* Cue 3: Right Bottom Card — When Elimination Fails / Blind Spot (24.8 ns & 44.3 ns) */}
       {t >= T[3] - 0.1 && (
         <Card
           x={900}
           y={420}
           w={960}
           h={274}
           border={C.yellow}
           title="消去法何时失灵（盲区）：24.8 ns（40.3 M/s）与 44.3 ns（22.6 M/s）"
           titleColor={C.yellow}
           style={{ ...appear(t, T[3], undefined, 18), padding: "14px 22px" }}
         />
       )}
 
       {/* Cue 4: Bottom Full-Width Pop-up — MRV-53 Controlled Perturbation Injection (+δ) */}
       {t >= T[4] - 0.1 && (
         <Card
           x={60}
           y={708}
           w={1800}
           h={144}
           border={C.cyan}
           style={{
             ...appear(t, T[4], undefined, 20),
             padding: "14px 26px",
             background: "linear-gradient(90deg, rgba(76,201,240,0.16) 0%, rgba(20,27,46,0.98) 55%)",
             display: "flex",
             alignItems: "center",
             justifyContent: "space-between",
           }}
         >
           {/* Left Title Block */}
           <div style={{ whiteSpace: "nowrap" }}>
             <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
               <span
                 style={{
                   background: C.cyan,
                   color: C.bg,
                   fontWeight: 900,
                   fontSize: 20,
                   padding: "4px 12px",
                   borderRadius: 999,
                   fontFamily: FONT,
                   whiteSpace: "nowrap",
                 }}
               >
                 MRV-53 破局方案
               </span>
               <span style={{ fontSize: 28, fontWeight: 900, color: C.cyan, fontFamily: FONT, whiteSpace: "nowrap" }}>
                 受控微扰注入法（<span style={{ fontFamily: MONO }}>+δ</span>）
               </span>
             </div>
             <div style={{ fontSize: 24, color: C.text, fontFamily: FONT, whiteSpace: "nowrap" }}>
               保持产品循环结构不变，向主机或卡核注入忙等 <span style={{ fontFamily: MONO, color: C.yellow }}>+δ</span>
             </div>
           </div>
 
           {/* Center Formula Highlight */}
           <div
             style={{
               background: `${C.yellow}1a`,
               border: `2px solid ${C.yellow}`,
               borderRadius: 14,
               padding: "10px 26px",
               textAlign: "center",
               whiteSpace: "nowrap",
               transform: `scale(${pop(t, T[4] + 0.2, 0.45)})`,
             }}
           >
             <div style={{ fontSize: 44, fontWeight: 900, color: C.yellow, fontFamily: MONO, lineHeight: 1.05 }}>
               斜率 = Δ ÷ δ
             </div>
             <div style={{ fontSize: 22, color: C.text, fontFamily: MONO, marginTop: 4 }}>
               间隔增量 Δ(1/吞吐) ÷ 忙等 δ
             </div>
           </div>
 
           {/* Right Interpretation Box */}
           <div
             style={{
               background: "#0b1020",
               border: `1.5px solid ${C.line}`,
               borderRadius: 12,
               padding: "10px 20px",
               whiteSpace: "nowrap",
             }}
           >
             <div style={{ fontSize: 24, color: K.ok, fontWeight: 800, fontFamily: FONT, marginBottom: 6 }}>
               • <span style={{ fontFamily: MONO }}>斜率 ≈ 1</span>：暴露在关键路径（无余量）
             </div>
             <div style={{ fontSize: 24, color: C.muted, fontWeight: 700, fontFamily: FONT }}>
               • <span style={{ fontFamily: MONO }}>斜率 ≈ 0</span>：被流水线余量吸收（非瓶颈）
             </div>
           </div>
         </Card>
       )}
 
       {/* SVG diagrams drawn on top of card panels */}
       <Stage>
         {/* Cue 0: Left Top Schematic (x: 80..855, y: 182..392) */}
         <g opacity={k0}>
           {/* Upper row: Real PCIe + DPU card crossed out */}
           <rect x={84} y={190} width={185} height={58} rx={10} fill="#0b1020" stroke={C.dim} strokeWidth={2} />
           <text x={176} y={227} fill={C.muted} fontSize={24} fontWeight={700} fontFamily={FONT} textAnchor="middle">
             主机 CPU
           </text>
 
           <Edge x1={270} y1={219} x2={365} y2={219} color={C.dim} w={3} dash="6 5" />
 
           <rect x={365} y={190} width={210} height={58} rx={10} fill="#0b1020" stroke={C.dim} strokeWidth={2} strokeDasharray="6 4" />
           <text x={470} y={227} fill={C.dim} fontSize={22} fontWeight={700} fontFamily={MONO} textAnchor="middle">
             PCIe Gen5 x4
           </text>
 
           <Edge x1={575} y1={219} x2={650} y2={219} color={C.dim} w={3} dash="6 5" />
 
           <rect x={650} y={190} width={196} height={58} rx={10} fill="#0b1020" stroke={C.dim} strokeWidth={2} strokeDasharray="6 4" />
           <text x={748} y={227} fill={C.dim} fontSize={24} fontWeight={700} fontFamily={FONT} textAnchor="middle">
             CN106xx 卡
           </text>
 
           {/* Red elimination strike-through over PCIe + DPU */}
           <Edge x1={350} y1={186} x2={850} y2={252} color={C.red} w={3.5} k={k0} />
           <Edge x1={350} y1={252} x2={850} y2={186} color={C.red} w={3.5} k={k0} />
           <rect x={510} y={200} width={185} height={36} rx={8} fill="#2a1218" stroke={C.red} strokeWidth={1.8} />
           <text x={602} y={225} fill={C.red} fontSize={20} fontWeight={800} fontFamily={FONT} textAnchor="middle">
             消去真实硬件通路
           </text>
 
           {/* Lower row: Fake-card local memory immediate flip */}
           <rect x={84} y={292} width={205} height={88} rx={12} fill="#0b1020" stroke={K.host} strokeWidth={2.5} />
           <text x={186} y={328} fill={K.host} fontSize={24} fontWeight={800} fontFamily={FONT} textAnchor="middle">
             主机收发循环
           </text>
           <text x={186} y={358} fill={C.muted} fontSize={22} fontWeight={600} fontFamily={MONO} textAnchor="middle">
             send + poll()
           </text>
 
           <Arrow x1={292} y1={322} x2={560} y2={322} color={C.cyan} w={3.5} k={k0} />
           <Arrow x1={560} y1={354} x2={292} y2={354} color={C.green} w={3.5} k={k0} />
           <PacketStream pts={fakeLoopPts} travel={rt * 190} gap={95} size={9} color={C.cyan} opacity={0.9} />
 
           <text x={426} y={310} fill={C.cyan} fontSize={22} fontWeight={700} fontFamily={MONO} textAnchor="middle">
             本地内存直写
           </text>
           <text x={426} y={382} fill={C.green} fontSize={22} fontWeight={700} fontFamily={MONO} textAnchor="middle">
             0 链路延迟读回
           </text>
 
           <rect x={564} y={288} width={282} height={96} rx={12} fill={`${C.green}18`} stroke={C.green} strokeWidth={2.5} />
           <text x={705} y={326} fill={C.green} fontSize={24} fontWeight={800} fontFamily={FONT} textAnchor="middle">
             本地内存立即翻转
           </text>
           <text x={705} y={362} fill={C.yellow} fontSize={24} fontWeight={800} fontFamily={MONO} textAnchor="middle">
             Cell::publish
           </text>
         </g>
 
         {/* Cue 2: Right Top Ruler — 76 ns Slow State (x: 924..1836, y: 188..392) */}
         {t >= T[2] - 0.1 && (
           <g opacity={k2}>
             <text x={926} y={224} fill={C.text} fontSize={24} fontWeight={700} fontFamily={FONT}>
               真硬件 <tspan fill={K.slow} fontWeight={900}>13 M/s 慢状态</tspan> 间隔：
               <tspan fill={C.yellow} fontSize={44} fontWeight={900} fontFamily={MONO}> 76 </tspan>
               <tspan fill={C.yellow} fontSize={24} fontWeight={800}>ns/条</tspan>
               <tspan fill={C.muted} fontSize={22} fontFamily={MONO}> (73.5–76.9 ns)</tspan>
             </text>
 
             {/* 76 ns Ruler Track (x: 926..1830, total width = 904 px => 11.89 px/ns) */}
             <rect x={926} y={240} width={904} height={66} rx={10} fill="#0b1020" stroke={C.line} strokeWidth={2} />
 
             {/* Host 13.2 ns segment (width = 157 px) */}
             <rect
               x={926}
               y={240}
               width={157 * k2}
               height={66}
               rx={8}
               fill={`${K.host}cc`}
               stroke={K.host}
               strokeWidth={2}
             />
             <text x={1004} y={268} fill={C.text} fontSize={24} fontWeight={800} fontFamily={FONT} textAnchor="middle">
               主机
             </text>
             <text x={1004} y={295} fill={C.yellow} fontSize={24} fontWeight={900} fontFamily={MONO} textAnchor="middle">
               13.2 ns
             </text>
 
             {/* Card ~15 ns segment (x: 1083..1263, width = 180 px) */}
             <rect
               x={1083}
               y={240}
               width={180 * k2}
               height={66}
               fill={`${K.card}cc`}
               stroke={K.card}
               strokeWidth={2}
             />
             <text x={1173} y={268} fill={C.bg} fontSize={24} fontWeight={900} fontFamily={FONT} textAnchor="middle">
               卡核
             </text>
             <text x={1173} y={295} fill={C.bg} fontSize={24} fontWeight={900} fontFamily={MONO} textAnchor="middle">
               十几 ns
             </text>
 
             {/* Huge empty gap (x: 1263..1830, width = 567 px) */}
             <rect x={1263} y={240} width={567} height={66} fill="url(#hatch)" opacity={0.65} />
             <rect
               x={1263}
               y={240}
               width={567}
               height={66}
               fill="none"
               stroke={K.ok}
               strokeWidth={2.5}
               strokeDasharray="8 6"
             />
             <text x={1546} y={281} fill={K.ok} fontSize={24} fontWeight={900} fontFamily={FONT} textAnchor="middle">
               巨大空白（&gt; 45 ns 非代码等待）
             </text>
 
             {/* Ruler Ticks */}
             <text x={926} y={334} fill={C.muted} fontSize={24} fontFamily={MONO}>
               0 ns
             </text>
             <text x={1083} y={334} fill={K.host} fontSize={24} fontWeight={700} fontFamily={MONO} textAnchor="middle">
               13.2 ns
             </text>
             <text x={1263} y={334} fill={K.card} fontSize={24} fontWeight={700} fontFamily={FONT} textAnchor="middle">
               两侧代码区
             </text>
             <text x={1830} y={334} fill={C.yellow} fontSize={24} fontWeight={800} fontFamily={MONO} textAnchor="end">
               76 ns / 条 (13 M/s)
             </text>
 
             {/* Conclusion Pill in SVG */}
             <rect x={926} y={348} width={904} height={44} rx={10} fill={`${K.ok}1f`} stroke={K.ok} strokeWidth={1.8} />
             <text x={1378} y={378} fill={K.ok} fontSize={24} fontWeight={800} fontFamily={FONT} textAnchor="middle">
               ✓ 结论：一眼可排除“纯代码算力封顶”（两侧都在等跨 PCIe 交互）
             </text>
           </g>
         )}
 
         {/* Cue 3: Right Bottom Rulers — Blind Spot at 24.8 ns & 44.3 ns (x: 924..1836, y: 472..682) */}
         {t >= T[3] - 0.1 && (
           <g opacity={k3}>
             {/* Case A: 24.8 ns (40.3 M/s) */}
             <rect x={926} y={472} width={440} height={146} rx={12} fill="#0b1020" stroke={C.line} strokeWidth={2} />
             <text x={942} y={512} fill={C.text} fontSize={24} fontWeight={800} fontFamily={FONT}>
               快态{" "}
               <tspan fill={C.cyan} fontSize={44} fontWeight={900} fontFamily={MONO}>24.8</tspan>{" "}
               <tspan fill={C.cyan} fontSize={24} fontWeight={800}>ns</tspan>{" "}
               <tspan fill={C.muted} fontSize={22} fontWeight={600} fontFamily={MONO}>(40.3 M/s)</tspan>
             </text>
             {/* Host bar 13.2 ns vs 24.8 ns */}
             <rect x={942} y={524} width={332} height={26} rx={6} fill="#18223a" stroke={C.dim} strokeWidth={1.5} />
             <rect x={942} y={524} width={176 * k3} height={26} rx={6} fill={K.host} />
             <text x={952} y={544} fill={C.text} fontSize={22} fontWeight={800} fontFamily={MONO}>
               主机 13.2 ns &lt; 24.8
             </text>
             {/* Card bar ~15 ns vs 24.8 ns */}
             <rect x={942} y={556} width={332} height={26} rx={6} fill="#18223a" stroke={C.dim} strokeWidth={1.5} />
             <rect x={942} y={556} width={210 * k3} height={26} rx={6} fill={K.card} />
             <text x={952} y={576} fill={C.bg} fontSize={22} fontWeight={900} fontFamily={MONO}>
               卡核 十几 ns &lt; 24.8
             </text>
             <text x={1109} y={608} fill={C.yellow} fontSize={24} fontWeight={700} fontFamily={FONT} textAnchor="middle">
               重叠后谁刚好顶格？
             </text>
             {/* Question Mark 1 */}
             <circle cx={1318} cy={552} r={32} fill={`${C.yellow}22`} stroke={C.yellow} strokeWidth={3} />
             <text x={1318} y={568} fill={C.yellow} fontSize={46} fontWeight={900} fontFamily={MONO} textAnchor="middle">
               ?
             </text>
 
             {/* Case B: 44.3 ns (22.6 M/s) */}
             <rect x={1390} y={472} width={440} height={146} rx={12} fill="#0b1020" stroke={C.line} strokeWidth={2} />
             <text x={1406} y={512} fill={C.text} fontSize={24} fontWeight={800} fontFamily={FONT}>
               中间态{" "}
               <tspan fill={C.yellow} fontSize={44} fontWeight={900} fontFamily={MONO}>44.3</tspan>{" "}
               <tspan fill={C.yellow} fontSize={24} fontWeight={800}>ns</tspan>{" "}
               <tspan fill={C.muted} fontSize={22} fontWeight={600} fontFamily={MONO}>(22.6 M/s)</tspan>
             </text>
             {/* Host bar 13.2 ns vs 44.3 ns */}
             <rect x={1406} y={524} width={332} height={26} rx={6} fill="#18223a" stroke={C.dim} strokeWidth={1.5} />
             <rect x={1406} y={524} width={100 * k3} height={26} rx={6} fill={K.host} />
             <text x={1416} y={544} fill={C.text} fontSize={22} fontWeight={800} fontFamily={MONO}>
               主机 13.2 ns &lt; 44.3
             </text>
             {/* Card bar vs 44.3 ns */}
             <rect x={1406} y={556} width={332} height={26} rx={6} fill="#18223a" stroke={C.dim} strokeWidth={1.5} />
             <rect x={1406} y={556} width={120 * k3} height={26} rx={6} fill={K.card} />
             <text x={1416} y={576} fill={C.text} fontSize={22} fontWeight={800} fontFamily={MONO}>
               卡侧 十几 ns &lt; 44.3
             </text>
             <text x={1573} y={608} fill={C.orange} fontSize={24} fontWeight={700} fontFamily={FONT} textAnchor="middle">
               还是回程/跨环卡住？
             </text>
             {/* Question Mark 2 */}
             <circle cx={1782} cy={552} r={32} fill={`${C.orange}22`} stroke={C.orange} strokeWidth={3} />
             <text x={1782} y={568} fill={C.orange} fontSize={46} fontWeight={900} fontFamily={MONO} textAnchor="middle">
               ?
             </text>
 
             {/* Blind-spot summary strip */}
             <rect x={926} y={628} width={904} height={50} rx={10} fill="rgba(249,199,79,0.14)" stroke={C.yellow} strokeWidth={1.8} />
             <text x={1378} y={661} fill={C.yellow} fontSize={24} fontWeight={800} fontFamily={FONT} textAnchor="middle">
               ✗ 静态消去法盲区：两侧代码都比 1 / 吞吐 短，无法区分谁在关键路径！
             </text>
           </g>
         )}
 
         {/* Cue 4: Subtle highlight connector arrow from Blind Spot down to MRV-53 Injection Solution */}
         {t >= T[4] && (
           <g opacity={k4}>
             <Arrow x1={1378} y1={682} x2={1378} y2={706} color={C.cyan} w={4} head={10} />
           </g>
         )}
       </Stage>
 
       <Note y={862}>示意 · MRV-35 假硬件消去法盲区与 MRV-53 微扰注入思想对比</Note>
     </>
   );
 }

export const S02: SceneDef = {
  id: "S02",
  title: "为什么光测假硬件还不够？",
  stage: 0,
  source: "MRV-35 vs MRV-53 · §1, §4",
  dur: DUR,
  cues,
  C: Body,
};
