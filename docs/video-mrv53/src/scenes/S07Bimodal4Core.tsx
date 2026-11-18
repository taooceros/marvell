import React from "react";
import {
  appear,
  Arrow,
  Axes,
  C,
  Card,
  Cue,
  fade,
  FONT,
  HLine,
  K,
  linScale,
  MONO,
  Note,
  Pill,
  pop,
  prog,
  SceneDef,
  Series,
  Stage,
  useT,
} from "../lib";
import { InfTag } from "./pcie";

// <cues>
const cues: Cue[] = [
  [0.60, 11.92, "最反直觉的是**卡 4 核 `W = 1024`**（`WC4 22.6 M/s` / `44.3 ns`、`ST4 25.5 M/s` / `39.3 ns`、`DPI4 22.7 M/s` / `44.0 ns`）：`RTT` 横跨 `min 17 µs` 到 `p90 64 µs`，属**快慢双态混合**。", "最反直觉的是卡四核一千零二十四窗口：基线二十二点六到二十五点五兆每秒，往返延迟从十七微秒横跨到六十四微秒，属于快慢双态混合。"], // 0
  [12.27, 23.63, "主机加 `δ = 10 → 20 → 40 ns` 时占空比倒向慢相，在 `40 ns` 三条线全部撞上 **`77.4–77.5 ns`（`12.9 M/s`）慢态天花板**（`20→40 ns` 边际斜率仅 **`0.39–0.50`**）。", "给主机加忙等会把占空比推向慢相：到四十纳秒时三条线全部撞上七十七点四纳秒的慢态天花板，后段边际斜率骤降到零点三九至零点五零。"], // 1
  [23.98, 33.08, "相反，**主机代码一行不改**，只给 4 个卡核每条回复加 **`30 → 45 → 60 ns`** 忙等，总吞吐不仅不降，反而**大幅跳升**！", "相反，主机代码一行不改，只给四个卡核每条加三十、四十五、六十纳秒忙等，总吞吐不仅不降，反而大幅跳升！"], // 2
  [33.43, 45.03, "实测 `WC4` 跳升至 **`27.0 → 30.4 → 36.9 M/s`**（`27.1 ns`），`ST4` 升至 **`35.1 M/s`**（`28.5 ns`），`DPI4` 更跃升至 **`40.6 M/s`**（`24.7 ns`，`RTT` 收敛为 `23.7–25.4 µs` 单峰快态）！", "写合并四核从二十二点六跳升到三十六点九兆每秒，直写四核升至三十五点一兆每秒，D P I 四核更跃升到四十点六兆每秒、二十四点七纳秒的单峰快态！"], // 3
  [45.38, 60.66, "`[INF]` 原因：卡核循环内忙等**节流了高频轮询与零散写回**，每核每轮处理条数从 **`1.4–1.6` 升至 `1.9–2.6`**（`DPI4` 总 DMA 从 **`5127 万` 降至 `3036 万`**），消除了跨 PCIe 争用。", "我们认为，这是因为卡核忙等节流了高频轮询与零散写回，每核每轮处理条数从一点五升至二点六，D P I 总 D M A 次数从五千一百二十七万降到三千零三十六万，从而消除了跨 P C I e 争用。"], // 4
];
const DUR = 62.06; // <dur>
// </cues>

const HOST_WC4: [number, number][] = [
  [0, 44.4],
  [10, 59.5],
  [20, 69.6],
  [40, 77.4],
];
const HOST_ST4: [number, number][] = [
  [0, 39.5],
  [10, 54.4],
  [20, 67.4],
  [40, 77.4],
];
const HOST_DPI4: [number, number][] = [
  [0, 44.1],
  [10, 58.6],
  [20, 69.8],
  [40, 77.5],
];

const CARD_WC4: [number, number][] = [
  [0, 44.3],
  [30, 37.0],
  [45, 32.9],
  [60, 27.1],
];
const CARD_ST4: [number, number][] = [
  [0, 39.3],
  [30, 37.8],
  [45, 33.6],
  [60, 28.5],
];
const CARD_DPI4: [number, number][] = [
  [0, 44.0],
  [32, 35.6],
  [45, 30.1],
  [59, 24.7],
];

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  // Left chart coordinate system (x: 60..1120, y: 130..845)
  const x0 = 152;
  const x1 = 896;
  const y0 = 740;
  const y1 = 238;
  const sx = linScale(0, 60, x0, x1);
  const sy = linScale(20, 85, y0, y1);

  const k0 = prog(t, T[0], 0.7);
  const kBase = pop(t, T[0] + 0.25, 0.5);
  const kHost = prog(t, T[1], 1.4);
  const kCeil = prog(t, T[1] + 0.5, 0.7);
  const kQ = fade(t, T[2], T[3], 0.45);
  const kArrow = prog(t, T[2] + 0.2, 0.7);
  const kCard = prog(t, T[3], 1.5);
  const kCardInfo = prog(t, T[3] + 0.5, 0.7);
  const kPreInf = fade(t, T[1] + 0.3, T[4], 0.45);
  const kInf = prog(t, T[4], 0.6);

  // Right-top RTT scale (15..82 us -> x: 1205..1820)
  const rttX = linScale(15, 82, 1205, 1820);

  return (
    <>
      {/* Left chart card */}
      <Card
        x={60}
        y={130}
        w={1060}
        h={715}
        border={C.line}
        title="卡 4 核 W=1024 分叉轨迹：+δ_host vs +δ_card"
        titleColor={C.cyan}
      />

      {/* Top-right card: Bimodal RTT distribution */}
      <Card
        x={1160}
        y={130}
        w={700}
        h={348}
        border={K.bimodal}
        title="快慢双态 RTT 分位跨度条（WC4 / DPI4, W = 1024）"
        titleColor={K.bimodal}
        style={appear(t, T[0], undefined, 18)}
      />

      {/* Bottom-right card during Cues 1..3 (fades out at T[4]) */}
      {kPreInf > 0.01 && (
        <Card
          x={1160}
          y={494}
          w={700}
          h={351}
          border={C.line}
          title="分叉对照：慢态触顶 vs 反直觉跳升"
          titleColor={C.text}
          style={{ opacity: kPreInf }}
        >
          <div style={{ marginTop: 4, display: "flex", flexDirection: "column", gap: 12 }}>
            <div
              style={{
                padding: "10px 16px",
                borderRadius: 12,
                background: "rgba(249,65,68,0.10)",
                border: `1.5px solid ${C.red}66`,
              }}
            >
              <div style={{ fontSize: 24, fontWeight: 800, color: C.red }}>
                ▲ 主机加 δ_host：40 ns 撞上慢态天花板
              </div>
              <div style={{ fontSize: 24, color: C.text, marginTop: 4, lineHeight: 1.32 }}>
                三线触顶{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: C.yellow }}>77.4–77.5 ns (12.9 M/s)</span>
                ，后段斜率骤降至 <span style={{ fontFamily: MONO, fontWeight: 800, color: C.red }}>0.39–0.50</span>。
              </div>
            </div>

            {t >= T[2] && (
              <div
                style={{
                  padding: "10px 16px",
                  borderRadius: 12,
                  background: "rgba(67,197,158,0.12)",
                  border: `1.5px solid ${C.green}88`,
                  opacity: prog(t, T[2], 0.5),
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                }}
              >
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 24, fontWeight: 800, color: C.green }}>
                    ▼ 卡核加 δ_card = 30 → 45 → 60 ns
                  </div>
                  <div style={{ fontSize: 24, color: C.text, marginTop: 4, lineHeight: 1.32 }}>
                    {t < T[3] ? (
                      <>主机不改，卡核加忙等周期反而沿绿箭加速！</>
                    ) : (
                      <>
                        WC4 <span style={{ fontFamily: MONO, fontWeight: 800, color: C.cyan }}>36.9</span> · ST4{" "}
                        <span style={{ fontFamily: MONO, fontWeight: 800, color: C.orange }}>35.1</span> · DPI4{" "}
                        <span style={{ fontFamily: MONO, fontWeight: 800, color: C.pink }}>40.6 M/s</span>
                      </>
                    )}
                  </div>
                </div>
                {t >= T[3] && (
                  <div style={{ textAlign: "right", flexShrink: 0 }}>
                    <div style={{ fontFamily: MONO, fontSize: 44, fontWeight: 900, color: C.green, lineHeight: 1 }}>
                      40.6M
                    </div>
                    <div style={{ fontSize: 24, color: C.muted, fontWeight: 700 }}>DPI4 (24.7ns)</div>
                  </div>
                )}
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Bottom-right card in Cue 4: [INF] Mechanism Explanation */}
      {t >= T[4] - 0.1 && (
        <Card
          x={1160}
          y={494}
          w={700}
          h={351}
          border={K.inf}
          title={<span style={{ paddingLeft: 76 }}>为什么卡核加忙等反而加速？</span>}
          titleColor={K.inf}
          style={appear(t, T[4], undefined, 20)}
        >
          <div style={{ marginTop: 4, display: "flex", flexDirection: "column", gap: 11, fontSize: 24, lineHeight: 1.32 }}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <span style={{ color: K.inf, fontWeight: 800 }}>●</span>
              <div>
                卡核忙等<strong>节流高频轮询与零散写回</strong>，消除跨 PCIe 争用
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <span style={{ color: C.green, fontWeight: 800 }}>●</span>
              <div>
                卡侧每核每轮处理条数从{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: C.yellow }}>1.4–1.6 条</span> 升至{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: C.green }}>1.9–2.6 条</span>
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <span style={{ color: C.pink, fontWeight: 800 }}>●</span>
              <div>
                <span style={{ fontFamily: MONO, color: C.pink, fontWeight: 800 }}>DPI4</span> 总 DMA 次数从{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: C.yellow }}>5127 万</span> 降至{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: C.green }}>3036 万</span>
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <span style={{ color: C.cyan, fontWeight: 800 }}>●</span>
              <div>
                主机记账差{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: C.yellow }}>1101–1260</span> →{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: C.cyan }}>623–738 ns</span>
                <span style={{ color: C.muted, fontSize: 24 }}>（近1核 557–636）</span>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Legend pills on top-right of Left Chart (size >= 20px) */}
      <Pill x={708} y={145} color={K.wc} size={20} style={{ opacity: k0 }}>
        WC4 写合并
      </Pill>
      <Pill x={852} y={145} color={K.st} size={20} style={{ opacity: k0 }}>
        ST4 直写
      </Pill>
      <Pill x={974} y={145} color={K.dpi} size={20} style={{ opacity: k0 }}>
        DPI4 DMA
      </Pill>

      {/* SVG Stage rendered on top of cards so all SVG elements & InfTag are crisp */}
      <Stage>
        {/* Left Chart Axes (size=24) */}
        <Axes
          x0={x0}
          x1={x1}
          y0={y0}
          y1={y1}
          sx={sx}
          sy={sy}
          xticks={[
            [0, "0"],
            [10, "10"],
            [20, "20"],
            [30, "30"],
            [40, "40"],
            [45, "45"],
            [60, "60 ns"],
          ]}
          yticks={[
            [20, "20"],
            [30, "30"],
            [40, "40"],
            [50, "50"],
            [60, "60"],
            [70, "70"],
            [80, "80"],
          ]}
          xlabel="注入受控忙等 δ (ns/条)：上支 +δ_host（主机） / 下支 +δ_card（4 个卡核）"
          ylabel="每条消息间隔 (ns/条)  [越低越快 ↓]"
          opacity={k0}
          size={24}
        />

        {/* Cue 0: Baseline dots at delta = 0 and permanent bottom-left baseline callout box (all 24px) */}
        {kBase > 0 && (
          <g opacity={k0} fontFamily={FONT}>
            <path
              d={`M ${sx(0)} ${sy(39.3)} L ${sx(0)} 668 L 172 668`}
              fill="none"
              stroke={C.muted}
              strokeWidth={2}
              strokeDasharray="5 5"
            />
            <circle cx={sx(0)} cy={sy(44.3)} r={8 * kBase} fill={K.wc} />
            <circle cx={sx(0)} cy={sy(44.0)} r={7 * kBase} fill={K.dpi} />
            <circle cx={sx(0)} cy={sy(39.3)} r={8 * kBase} fill={K.st} />

            {/* Permanent Baseline Summary Box in open bottom-left zone (x: 172..562, y: 608..732) */}
            <rect
              x={172}
              y={608}
              width={390}
              height={124}
              rx={10}
              fill="rgba(11,16,32,0.92)"
              stroke={K.bimodal}
              strokeWidth={1.8}
            />
            <text x={188} y={637} fontSize={24} fontWeight={800} fill={K.bimodal}>
              基线起点 (δ = 0, W = 1024)：
            </text>
            <text x={188} y={666} fontSize={24} fontWeight={700} fill={K.wc}>
              WC4: 44.3 ns/条 (22.6 M/s)
            </text>
            <text x={188} y={695} fontSize={24} fontWeight={700} fill={K.st}>
              ST4: 39.3 ns/条 (25.5 M/s)
            </text>
            <text x={188} y={724} fontSize={24} fontWeight={700} fill={K.dpi}>
              DPI4: 44.0 ns/条 (22.7 M/s)
            </text>
          </g>
        )}

        {/* Cue 1: Slow-state ceiling HLine (~77.4 ns / 12.9 M/s) & Host +delta_host upward curves */}
        {kCeil > 0 && (
          <g opacity={kCeil}>
            <HLine y={sy(77.4)} x0={x0} x1={x1} color={C.red} />
            <rect
              x={168}
              y={sy(77.4) - 42}
              width={720}
              height={36}
              rx={6}
              fill="rgba(249,65,68,0.16)"
              stroke={C.red}
              strokeWidth={1.5}
            />
            <text x={182} y={sy(77.4) - 17} fontFamily={FONT} fontSize={24} fontWeight={800} fill={C.red}>
              慢状态天花板 ~77.4 ns/条 (12.9 M/s) —— 主机 40 ns 全部触顶！
            </text>
          </g>
        )}

        <Series pts={HOST_WC4} sx={sx} sy={sy} k={kHost} color={K.wc} w={4.5} dash="10 6" r={6} />
        <Series pts={HOST_ST4} sx={sx} sy={sy} k={kHost} color={K.st} w={4.5} dash="10 6" r={6} />
        <Series pts={HOST_DPI4} sx={sx} sy={sy} k={kHost} color={K.dpi} w={4.5} dash="10 6" r={6} />

        {/* Cue 1: Upper branch trajectory & slope callout box (all 24px in FONT, wide gap between left & right columns) */}
        {kCeil > 0 && (
          <g opacity={kCeil} fontFamily={FONT}>
            <rect
              x={404}
              y={332}
              width={700}
              height={132}
              rx={10}
              fill="rgba(11,16,32,0.94)"
              stroke={C.red}
              strokeWidth={1.8}
            />
            <text x={420} y={361} fontSize={24} fontWeight={800} fill={C.red}>
              ▲ 主机加 δ_host (0→10→20→40ns) · 0→10 vs 20→40 斜率
            </text>
            {/* Row 1: WC4 */}
            <text x={420} y={391} fontSize={24} fontWeight={700} fill={K.wc}>
              WC4: 44.4→59.5→69.6→77.4 ns
            </text>
            <text x={1090} y={391} textAnchor="end" fontSize={24} fontWeight={700} fill={C.text}>
              斜率 <tspan fill={C.yellow}>1.51</tspan> → <tspan fill={C.red}>0.39</tspan>
            </text>
            {/* Row 2: ST4 */}
            <text x={420} y={420} fontSize={24} fontWeight={700} fill={K.st}>
              ST4: 39.5→54.4→67.4→77.4 ns
            </text>
            <text x={1090} y={420} textAnchor="end" fontSize={24} fontWeight={700} fill={C.text}>
              斜率 <tspan fill={C.yellow}>1.49</tspan> → <tspan fill={C.red}>0.50</tspan>
            </text>
            {/* Row 3: DPI4 */}
            <text x={420} y={449} fontSize={24} fontWeight={700} fill={K.dpi}>
              DPI4: 44.1→58.6→69.8→77.5 ns
            </text>
            <text x={1090} y={449} textAnchor="end" fontSize={24} fontWeight={700} fill={C.text}>
              斜率 <tspan fill={C.yellow}>1.45</tspan> → <tspan fill={C.red}>0.39</tspan>
            </text>
          </g>
        )}

        {/* Cue 2: Green downward arrow from baseline + Reversal Question Highlight */}
        {kArrow > 0 && (
          <g opacity={kArrow}>
            <circle
              cx={sx(0)}
              cy={sy(42)}
              r={22}
              fill="none"
              stroke={C.green}
              strokeWidth={3}
              strokeDasharray="6 4"
            />
            <Arrow
              x1={sx(2)}
              y1={sy(43.5)}
              x2={sx(22)}
              y2={sy(35.0)}
              color={C.green}
              w={7}
              head={18}
              k={kArrow}
            />
          </g>
        )}

        {kQ > 0.01 && (
          <g opacity={kQ}>
            <rect
              x={225}
              y={474}
              width={755}
              height={114}
              rx={12}
              fill="rgba(16,42,38,0.95)"
              stroke={C.green}
              strokeWidth={2.5}
            />
            <text x={245} y={512} fontFamily={FONT} fontSize={24} fontWeight={700} fill={C.text}>
              如果主机代码一行不改，给 4 个卡核每条加 δ_card = 30 → 45 → 60 ns 忙等，
            </text>
            <text x={245} y={548} fontFamily={FONT} fontSize={25} fontWeight={800} fill={C.green}>
              周期会往哪走？ ──► 沿绿色箭头向下加速，吞吐反而大幅跳升！
            </text>
          </g>
        )}

        {/* Cue 3: Card +delta_card downward acceleration curves */}
        <Series pts={CARD_WC4} sx={sx} sy={sy} k={kCard} color={K.wc} w={5} r={7} />
        <Series pts={CARD_ST4} sx={sx} sy={sy} k={kCard} color={K.st} w={5} r={7} />
        <Series pts={CARD_DPI4} sx={sx} sy={sy} k={kCard} color={K.dpi} w={5} r={7} />

        {/* Cue 3: Lower branch trajectory & throughput jump box */}
        {kCardInfo > 0 && (
          <g opacity={kCardInfo} fontFamily={FONT}>
            <rect
              x={344}
              y={470}
              width={760}
              height={132}
              rx={10}
              fill="rgba(11,16,32,0.94)"
              stroke={C.green}
              strokeWidth={2}
            />
            <text x={360} y={499} fontSize={24} fontWeight={800} fill={C.green}>
              ▼ 卡核加 δ_card (0→30/32→45→59/60ns) · 周期(ns) vs 吞吐(M/s)
            </text>
            {/* Row 1: WC4 */}
            <text x={360} y={529} fontSize={24} fontWeight={700} fill={K.wc}>
              WC4: 44.3→37.0→32.9→27.1ns
            </text>
            <text x={1090} y={529} textAnchor="end" fontSize={24} fontWeight={700} fill={K.wc}>
              22.6→27.0→30.4→36.9 M/s
            </text>
            {/* Row 2: ST4 */}
            <text x={360} y={558} fontSize={24} fontWeight={700} fill={K.st}>
              ST4: 39.3→37.8→33.6→28.5ns
            </text>
            <text x={1090} y={558} textAnchor="end" fontSize={24} fontWeight={700} fill={K.st}>
              25.5→26.4→29.8→35.1 M/s
            </text>
            {/* Row 3: DPI4 */}
            <text x={360} y={587} fontSize={24} fontWeight={700} fill={K.dpi}>
              DPI4: 44.0→35.6→30.1→24.7ns
            </text>
            <text x={1090} y={587} textAnchor="end" fontSize={24} fontWeight={700} fill={K.dpi}>
              22.7→28.1→33.2→40.6 M/s
            </text>

            {/* Right-side endpoint badges at delta_card = 60 ns (fontSize=24) */}
            <text x={x1 + 10} y={666} fontSize={24} fontWeight={800} fill={K.st}>
              28.5ns · 35.1M/s
            </text>
            <text x={x1 + 10} y={695} fontSize={24} fontWeight={800} fill={K.wc}>
              27.1ns · 36.9M/s
            </text>
            <text x={x1 + 10} y={724} fontSize={24} fontWeight={800} fill={K.dpi}>
              24.7ns · 40.6M/s
            </text>
          </g>
        )}

        {/* Top-Right Card SVG Content: Bimodal RTT Quantile Span Bar (ALL labels & ticks 24px) */}
        {k0 > 0 && (
          <g opacity={k0} fontFamily={FONT}>
            {/* Axis line for RTT 15..82 us */}
            <line x1={1205} y1={266} x2={1820} y2={266} stroke={C.dim} strokeWidth={2} />
            {[18, 24, 45, 63, 78].map((u) => (
              <g key={u}>
                <line x1={rttX(u)} y1={260} x2={rttX(u)} y2={272} stroke={C.muted} strokeWidth={2} />
              </g>
            ))}
            {[18, 45, 63, 78].map((u) => (
              <text key={`lbl${u}`} x={rttX(u)} y={252} textAnchor="middle" fontSize={24} fill={C.muted}>
                {u}µs
              </text>
            ))}

            {/* Fast phase (~24 us) & Slow phase (~78 us) vertical reference guides */}
            <line
              x1={rttX(24)}
              y1={270}
              x2={rttX(24)}
              y2={448}
              stroke={C.green}
              strokeWidth={2}
              strokeDasharray="6 5"
            />
            <text x={rttX(24) + 8} y={220} textAnchor="start" fontSize={24} fontWeight={800} fill={C.green}>
              快相 ~24 µs
            </text>

            <line
              x1={rttX(78)}
              y1={270}
              x2={rttX(78)}
              y2={448}
              stroke={C.red}
              strokeWidth={2}
              strokeDasharray="6 5"
            />
            <text x={rttX(78) - 8} y={220} textAnchor="end" fontSize={24} fontWeight={800} fill={C.red}>
              慢相 ~78 µs
            </text>

            {/* Row 1: Baseline WC4 / DPI4 bimodal RTT span bar */}
            <text x={1200} y={298} fontSize={24} fontWeight={800} fill={K.bimodal}>
              基线 (δ = 0)：快慢双态占空比混合
            </text>
            <rect
              x={rttX(18)}
              y={310}
              width={(rttX(63) - rttX(18)) * k0}
              height={30}
              rx={8}
              fill="rgba(249,199,79,0.22)"
              stroke={K.bimodal}
              strokeWidth={2.5}
            />
            {/* min marker */}
            <circle cx={rttX(18)} cy={325} r={7} fill={C.green} />
            <text x={rttX(18) + 26} y={366} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.green}>
              min 17–19µs
            </text>
            {/* p50 marker */}
            <line x1={rttX(45)} y1={306} x2={rttX(45)} y2={344} stroke={K.bimodal} strokeWidth={4} />
            <text x={rttX(45)} y={366} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.bimodal}>
              p50 44–46µs
            </text>
            {/* p90 marker */}
            <circle cx={rttX(63)} cy={325} r={7} fill={C.red} />
            <text x={rttX(63) + 18} y={366} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.red}>
              p90 62–64µs
            </text>

            {/* Row 2 (appears in Cue 3): DPI4 +delta_card = 60 ns converged single-peak fast state */}
            {kCardInfo > 0 && (
              <g opacity={kCardInfo}>
                <text x={1200} y={404} fontSize={24} fontWeight={800} fill={C.green}>
                  DPI4 (+δ_card = 60 ns)：收敛为单峰快态！
                </text>
                <rect
                  x={rttX(23.7)}
                  y={416}
                  width={Math.max(22, rttX(25.4) - rttX(23.7))}
                  height={28}
                  rx={6}
                  fill={C.green}
                  stroke="#ffffff"
                  strokeWidth={1.5}
                />
                <text x={rttX(25.4) + 14} y={438} fontSize={24} fontWeight={800} fill={C.green}>
                  RTT [p50, p90] = [23.7, 25.4] µs (40.6 M/s)
                </text>
              </g>
            )}
          </g>
        )}

        {/* Cue 4: SVG InfTag badge on top-left of Bottom-Right Mechanism Card */}
        {kInf > 0 && <InfTag x={1184} y={513} opacity={kInf} s={1.05} />}
      </Stage>

      <Note>按 mrv-53-slopes.py 与 Carbon/Argon 评审复算数据重绘</Note>
    </>
  );
}

export const S07: SceneDef = {
  id: "S07",
  title: "结果三：卡 4 核 W=1024 双态混合与反直觉加速",
  stage: 2,
  source: "mrv-53-slopes.py · Carbon & Argon 复算 · §2.5",
  dur: DUR,
  cues,
  C: Body,
};
