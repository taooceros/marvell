import React from "react";
import {
  Arrow,
  Axes,
  C,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  Note,
  SceneDef,
  Series,
  Stage,
  Txt,
  appear,
  fade,
  linScale,
  pop,
  prog,
  useT,
} from "../lib";
import { InfTag } from "./pcie";

// <cues>
const cues: Cue[] = [
  [0.60, 12.16, "卡 1 核快状态（`W=1024` 下 `WC1` `H0` **`40.3 M/s` / `24.8 ns`**，`DPI1` **`44.5 M/s` / `22.5 ns`**）：初版过原点拟合曾误判“两侧斜率都 `≈ 1`”。", "来到卡一核快状态：写合并一核在 H 零基线下跑出四十点三兆每秒、二十四点八纳秒。初版报告用过原点单斜率拟合，曾误判主卡两侧斜率都等于一。"], // 0
  [12.51, 22.99, "Carbon 与 Argon 评审指出同批数据的关键证据：面对**同一个无延迟卡进程 `C1`**，无旋钮 `H2` 二进制稳定跑出 **`50.5 M/s`（`19.8 ns/条`）**！", "但 Carbon 与 Argon 评审发现：面对同一个无延迟卡进程 C 一，无旋钮的 H 二主机二进制稳定跑出了五十点五兆每秒，每条仅十九点八纳秒！"], // 1
  [23.34, 33.78, "这证明单卡核真实上限至多 **`≤ 19.8 ns/条`**；在 `H0` 基线 **`24.8 ns/条`** 下由**主机侧决定周期**，单卡核原本有 **`~5 ns` 流水线余量**。", "这证明单卡核自身上限至多十九点八纳秒！因此在 H 零的二十四点八纳秒基线下，由主机侧决定周期，单卡核原本留有约五纳秒余量。"], // 2
  [34.13, 47.15, "由于双 `isb` 最小步长高达 **`29–32 ns`**，第一步就**跨过了 `~5 ns` 余量拐点**，此后 `WC1` 在 `δ = 29 / 45 / 59 ns` 下沿 **`58.6 / 72.3 / 86.8 ns`** 呈 `1:1` 封顶（斜率 **`0.86–1.04`**）。", "因为双 I S B 最小步长高达二十九到三十二纳秒，第一步就跨过了五纳秒余量拐点；此后写合并一核沿五十八点六、七十二点三、八十六点八纳秒的单卡核封顶线一比一增长。"], // 3
  [47.50, 59.10, "主机侧同样紧平衡：首档 `δ = 10 ns`（双 `lfence`）打破锁相阶跃 **`+13.9 ~ +18.0 ns`**，`10→20 ns` 边际斜率 **`0.58–0.63`**，`40 ns` 跌入 **`~77 ns` 慢态**。", "主机侧同样紧平衡：首档十纳秒打破主卡锁相阶跃十四到十八纳秒，十到二十纳秒边际斜率零点五八到零点六三，四十纳秒则跌入七十七纳秒慢态。"], // 4
];
const DUR = 60.50; // <dur>
// </cues>

const CARD_FAST_SERIES: {
  name: string;
  color: string;
  pts: [number, number][];
  dash?: string;
}[] = [
  {
    name: "DPI1 W=1024 (δ_card)",
    color: K.dpi,
    pts: [
      [0, 22.5],
      [32, 61.7],
      [45, 75.4],
      [59, 90.0],
    ],
  },
  {
    name: "WC1 W=1024 (δ_card)",
    color: K.wc,
    pts: [
      [0, 24.8],
      [29, 58.6],
      [45, 72.3],
      [59, 86.8],
    ],
  },
  {
    name: "WC1 W=64 稳态 (δ_card)",
    color: C.green,
    pts: [
      [0, 30.3],
      [29, 58.9],
      [45, 72.6],
      [59, 87.0],
    ],
  },
  {
    name: "WC1 W=64 起步 (δ_card)",
    color: C.yellow,
    pts: [
      [0, 73.0],
      [29, 59.4],
      [45, 73.4],
      [59, 87.0],
    ],
    dash: "10 5 3 5",
  },
];

const HOST_FAST_SERIES: {
  name: string;
  color: string;
  pts: [number, number][];
}[] = [
  {
    name: "WC1 W=1024 (δ_host)",
    color: K.wc,
    pts: [
      [0, 24.8],
      [10, 38.7],
      [20, 44.5],
      [40, 76.9],
    ],
  },
  {
    name: "DPI1 W=1024 (δ_host)",
    color: K.dpi,
    pts: [
      [0, 22.5],
      [10, 40.5],
      [20, 46.7],
      [40, 77.4],
    ],
  },
];

/** 5-pointed star polygon points centred at (0, 0). */
function starPoints(rOuter: number, rInner: number): string {
  const pts: string[] = [];
  for (let i = 0; i < 10; i++) {
    const r = i % 2 === 0 ? rOuter : rInner;
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    pts.push(`${(Math.cos(a) * r).toFixed(1)},${(Math.sin(a) * r).toFixed(1)}`);
  }
  return pts.join(" ");
}

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  // Full-width top plot in Left Panel: delta in [0, 60] ns -> x in [150, 1136], interval in [15, 95] ns/msg -> y in [474, 222]
  const sx = linScale(0, 60, 150, 1136);
  const sy = linScale(15, 95, 474, 222);

  const k0 = prog(t, T[0], 0.8);
  const kStar = pop(t, T[1] + 0.15, 0.55);
  const k1 = prog(t, T[1], 0.8);
  const k2 = prog(t, T[2], 0.8);
  const k3 = prog(t, T[3], 1.3);
  const k4 = prog(t, T[4], 1.3);

  return (
    <>
      {/* Panel backgrounds (HTML, behind Stage) */}
      <Card
        x={60}
        y={128}
        w={1120}
        h={720}
        border={`${K.fast}88`}
        style={{
          background: `linear-gradient(180deg, ${C.panel}f2 0%, ${C.bg}f5 100%)`,
        }}
      />
      <Card
        x={1220}
        y={128}
        w={640}
        h={720}
        border={`${C.yellow}99`}
        style={{
          background: `linear-gradient(180deg, ${C.panel}f2 0%, ${C.bg}f5 100%)`,
        }}
      />

      {/* Panel headers (>= 24 px) */}
      <Txt x={84} y={142} size={26} weight={800} color={C.text}>
        卡 1 核快状态（WC1 / DPI1）：卡侧 δ_card（实线）与主机 δ_host（虚线）
      </Txt>
      <Txt x={1244} y={142} size={26} weight={800} color={C.yellow}>
        Carbon &amp; Argon 评审复算
      </Txt>
      <Txt x={1244} y={176} size={24} weight={700} color={C.muted}>
        为什么单卡核在 δ = 0 时留有 ~5 ns 余量？
      </Txt>

      <Stage>
        <g opacity={k0}>
          <Axes
            x0={150}
            x1={1136}
            y0={474}
            y1={222}
            sx={sx}
            sy={sy}
            xticks={[
              [0, "0"],
              [10, "10"],
              [20, "20"],
              [30, "29/32"],
              [40, "40"],
              [45, "45"],
              [59, "59 ns"],
            ]}
            yticks={[
              [15, "15"],
              [30, "30"],
              [50, "50"],
              [70, "70"],
              [90, "90"],
            ]}
            xlabel="受控忙等延迟 δ (ns)"
            ylabel="每条消息间隔 (ns/条)"
            size={24}
          />

          {/* Cue 0: Naive through-origin single-slope fit */}
          <g opacity={t < T[3] ? 0.7 : 0.18}>
            <line
              x1={sx(0)}
              y1={sy(24.8)}
              x2={sx(59)}
              y2={sy(24.8 + 59)}
              stroke={C.muted}
              strokeWidth={2.5}
              strokeDasharray="6 6"
            />
            {t < T[3] && (
              <text
                x={sx(22)}
                y={sy(58)}
                fontFamily={FONT}
                fontSize={24}
                fontWeight={700}
                fill={C.muted}
              >
                初版过原点单斜率拟合（曾误以为“主卡斜率都 ≈ 1”）
              </text>
            )}
          </g>

          {/* Cue 0: H0 Baseline points at delta = 0 */}
          {[
            { name: "WC1 W=64 起步", y: 73.0, color: C.yellow, showAt: T[3] },
            { name: "WC1 W=64 稳态", y: 30.3, color: C.green, showAt: T[0] + 0.35 },
            { name: "WC1 W=1024 H0", y: 24.8, color: K.wc, showAt: T[0] + 0.15 },
            { name: "DPI1 W=1024 H0", y: 22.5, color: K.dpi, showAt: T[0] + 0.25 },
          ].map((b) => {
            const sc = pop(t, b.showAt, 0.45);
            if (sc <= 0) return null;
            return (
              <g key={b.name} transform={`translate(${sx(0)},${sy(b.y)}) scale(${sc})`}>
                <circle r={11} fill={`${b.color}33`} />
                <circle r={6.5} fill={b.color} stroke={C.bg} strokeWidth={2} />
              </g>
            );
          })}

          {/* Cue 1: Glowing Red Star at (delta = 0, 19.8 ns) for untimed H2 + C1 */}
          {kStar > 0 && (
            <g>
              {/* Vertical ~5 ns slack bracket at delta = 0..5 ns */}
              {k2 > 0 && (
                <g opacity={k2}>
                  <rect
                    x={sx(0)}
                    y={sy(24.8)}
                    width={sx(5) - sx(0)}
                    height={sy(19.8) - sy(24.8)}
                    fill={`${C.green}38`}
                    stroke={C.green}
                    strokeWidth={2}
                  />
                  <line
                    x1={sx(5)}
                    y1={sy(15)}
                    x2={sx(5)}
                    y2={sy(52)}
                    stroke={C.green}
                    strokeWidth={2.2}
                    strokeDasharray="5 4"
                  />
                  <text
                    x={sx(5.8)}
                    y={sy(48)}
                    fontFamily={FONT}
                    fontSize={24}
                    fontWeight={800}
                    fill={C.green}
                  >
                    ~5 ns 余量拐点
                  </text>
                  <InfTag x={sx(5.8) + 174} y={sy(48) - 24} opacity={k2} />
                </g>
              )}

              {/* Red 5-pointed star ★ */}
              <g transform={`translate(${sx(0)},${sy(19.8)}) scale(${kStar})`}>
                <circle r={18} fill={`${C.red}33`} />
                <polygon
                  points={starPoints(13.5, 5.8)}
                  fill={C.red}
                  stroke={C.yellow}
                  strokeWidth={2}
                />
              </g>

              {/* Leader callout for the Red Star in open bottom-right of plot */}
              <g opacity={k1}>
                <line
                  x1={sx(0) + 15}
                  y1={sy(19.8)}
                  x2={sx(17)}
                  y2={sy(19.8)}
                  stroke={C.red}
                  strokeWidth={2.2}
                />
                <rect
                  x={sx(17)}
                  y={sy(19.8) - 21}
                  width={612}
                  height={40}
                  rx={8}
                  fill={`${C.bg}f0`}
                  stroke={C.red}
                  strokeWidth={2}
                />
                <text
                  x={sx(17) + 306}
                  y={sy(19.8) + 7}
                  textAnchor="middle"
                  fontFamily={FONT}
                  fontSize={24}
                  fontWeight={800}
                  fill={C.yellow}
                >
                  ★ 无旋钮 H2 + 同卡进程 C1：19.8 ns/条（50.5 M/s）
                </text>
              </g>
            </g>
          )}

          {/* Cue 3: Single-card-core 1:1 cap reference line (~29 ns + delta_card) & trajectories */}
          {k3 > 0 && (
            <g opacity={prog(t, T[3], 0.5)}>
              <line
                x1={sx(0)}
                y1={sy(29.0)}
                x2={sx(60)}
                y2={sy(89.0)}
                stroke={C.orange}
                strokeWidth={3}
                strokeDasharray="10 6"
                opacity={0.85}
              />
              <rect
                x={sx(1.5)}
                y={sy(93.8)}
                width={520}
                height={40}
                rx={8}
                fill={`${C.bg}f0`}
                stroke={C.orange}
                strokeWidth={2}
              />
              <text
                x={sx(1.5) + 260}
                y={sy(93.8) + 28}
                textAnchor="middle"
                fontFamily={FONT}
                fontSize={24}
                fontWeight={800}
                fill={C.orange}
              >
                单卡核封顶线 ~29 ns + δ_card（斜率 = 1）
              </text>

              <Arrow
                x1={sx(5)}
                y1={sy(74)}
                x2={sx(28.5)}
                y2={sy(62)}
                color={C.orange}
                w={3}
                head={11}
                dash="5 4"
                k={prog(t, T[3] + 0.25, 0.6)}
              />
              <text
                x={sx(17)}
                y={sy(76.5)}
                textAnchor="middle"
                fontFamily={FONT}
                fontSize={24}
                fontWeight={800}
                fill={C.yellow}
              >
                双 isb 步长 29–32 ns
              </text>
            </g>
          )}

          {CARD_FAST_SERIES.map((s, i) => (
            <Series
              key={s.name}
              pts={s.pts}
              sx={sx}
              sy={sy}
              k={prog(t, T[3] + 0.15 + i * 0.16, 1.1)}
              color={s.color}
              w={s.dash ? 3.8 : 4.8}
              r={5.5}
              dash={s.dash}
            />
          ))}

          {/* Cue 4: Host busy-wait dashed trajectories */}
          {HOST_FAST_SERIES.map((s, i) => (
            <Series
              key={s.name}
              pts={s.pts}
              sx={sx}
              sy={sy}
              k={prog(t, T[4] + 0.15 + i * 0.18, 1.1)}
              color={s.color}
              w={4.2}
              r={5.5}
              dash="8 5"
            />
          ))}

          {k4 > 0.5 && (
            <g opacity={prog(t, T[4] + 0.5, 0.5)}>
              <circle
                cx={sx(40)}
                cy={sy(77.1)}
                r={11}
                fill={`${K.slow}33`}
                stroke={K.slow}
                strokeWidth={2.2}
              />
              <polyline
                points={`${sx(40) + 9},${sy(75.2)} ${sx(43)},${sy(53)}`}
                fill="none"
                stroke={K.slow}
                strokeWidth={2.2}
              />
              <rect
                x={sx(34)}
                y={sy(53)}
                width={396}
                height={40}
                rx={8}
                fill={`${C.bg}f2`}
                stroke={K.slow}
                strokeWidth={2}
              />
              <text
                x={sx(34) + 198}
                y={sy(53) + 27}
                textAnchor="middle"
                fontFamily={FONT}
                fontSize={24}
                fontWeight={800}
                fill={K.slow}
              >
                δ_host=40ns 跌入 76.9/77.4ns 慢态
              </text>
            </g>
          )}
        </g>
      </Stage>

      {/* ==================== LEFT PANEL BOTTOM DATA AREA (x: 80..1160, y: 552..834, width: 1080) ==================== */}
      {/* Cue 0..2: H0 Baselines & Star H2 discovery */}
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 552,
          width: 1080,
          height: 282,
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          opacity: fade(t, T[0] + 0.15, T[3], 0.4),
        }}
      >
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
          {[
            { name: "WC1 W=1024 (H0)", val: "24.8 ns/条", ms: "40.3 M/s", color: K.wc },
            { name: "DPI1 W=1024 (H0)", val: "22.5 ns/条", ms: "44.5 M/s", color: K.dpi },
            { name: "WC1 W=64 稳态", val: "30.3 ns/条", ms: "33.0 M/s", color: C.green },
          ].map((b) => (
            <div
              key={b.name}
              style={{
                borderRadius: 10,
                padding: "12px 16px",
                background: `${C.bg}ea`,
                border: `2px solid ${b.color}99`,
                display: "flex",
                flexDirection: "column",
                gap: 6,
                whiteSpace: "nowrap",
              }}
            >
              <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: b.color }}>
                {b.name}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontFamily: MONO, fontSize: 28, fontWeight: 900, color: C.text }}>
                  {b.val}
                </span>
                <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.muted }}>
                  ({b.ms})
                </span>
              </div>
            </div>
          ))}
        </div>

        <div
          style={{
            height: 156,
            borderRadius: 12,
            padding: "14px 20px",
            background: `linear-gradient(90deg, ${C.green}24 0%, ${C.panel}f5 100%)`,
            border: `2.5px solid ${t >= T[1] ? C.red : C.line}`,
            display: "flex",
            alignItems: "center",
            gap: 22,
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 48, fontWeight: 900, color: C.yellow }}>
            {t >= T[1] ? "19.8ns" : "24.8ns"}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.yellow }}>
              {t >= T[1]
                ? "★ 无旋钮 H2 配同卡 C1 实测 19.8 ns/条（50.5 M/s，早期核验 50.1–52.2 M/s）"
                : "初版过原点拟合曾误以为“主卡两侧斜率都 ≈ 1”"}
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.text }}>
              {t >= T[2]
                ? "单卡核上限 ≤ 19.8 ns/条 → H0 (24.8 ns) 由主机决定周期，单卡核留有 ~5 ns 余量"
                : "Carbon & Argon 评审复算同批数据（id=396, 398），揭开 ~5 ns 紧平衡余量"}
            </div>
          </div>
        </div>
      </div>

      {/* Cue 3: Card delta_card trajectories & 1:1 convergence */}
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 550,
          width: 1080,
          height: 286,
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          opacity: fade(t, T[3] + 0.1, T[4], 0.4),
        }}
      >
        <div
          style={{
            height: 54,
            borderRadius: 10,
            padding: "0 16px",
            background: `${C.bg}ea`,
            border: `2px solid ${K.wc}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: K.wc }}>
            WC1 W=1024 (δ_card)
          </span>
          <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
            24.8 → 58.6 → 72.3 → 86.8 ns/条（29→59ns 斜率 0.86 / 1.04）
          </span>
        </div>

        <div
          style={{
            height: 54,
            borderRadius: 10,
            padding: "0 16px",
            background: `${C.bg}ea`,
            border: `2px solid ${C.green}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.green }}>
            WC1 W=64 稳态 / 起步
          </span>
          <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
            稳态 30.3→58.9→72.6→87.0 ns · 起步 73.0→59.4→73.4→87.0 ns
          </span>
        </div>

        <div
          style={{
            height: 54,
            borderRadius: 10,
            padding: "0 16px",
            background: `${C.bg}ea`,
            border: `2px solid ${K.dpi}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: K.dpi }}>
            DPI1 W=1024 (δ_card)
          </span>
          <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
            22.5→61.7→75.4→90.0 ns（斜率 1.06/1.04，主机空轮询 4%→99%）
          </span>
        </div>

        <div
          style={{
            height: 96,
            borderRadius: 12,
            padding: "10px 18px",
            background: `linear-gradient(90deg, ${C.orange}28 0%, ${C.panel}f5 100%)`,
            border: `2.5px solid ${C.orange}`,
            display: "flex",
            alignItems: "center",
            gap: 20,
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 46, fontWeight: 900, color: C.orange }}>
            1 : 1
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.orange }}>
              双 isb 最小步长 29–32 ns（加乱序冲刷代价）一步跨过 ~5 ns 余量拐点！
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.text }}>
              跨过拐点后三条 WC1 线完全收敛到单卡核 ~29 ns + δ_card 封顶线（斜率 0.86–1.04）
            </div>
          </div>
        </div>
      </div>

      {/* Cue 4: Host delta_host trajectories & tight balance summary */}
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 550,
          width: 1080,
          height: 286,
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          ...appear(t, T[4] + 0.15),
        }}
      >
        <div
          style={{
            height: 54,
            borderRadius: 10,
            padding: "0 16px",
            background: `${C.bg}ea`,
            border: `1.8px solid ${C.orange}99`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.orange }}>
            卡侧 δ_card
          </span>
          <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
            WC1 24.8/30.3/73.0→86.8/87.0 ns · DPI1 22.5→90.0 ns
          </span>
        </div>

        <div
          style={{
            height: 54,
            borderRadius: 10,
            padding: "0 16px",
            background: `${C.bg}ea`,
            border: `2px dashed ${K.wc}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: K.wc }}>
            WC1 (δ_host)
          </span>
          <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
            24.8→38.7→44.5→76.9 ns（+13.9ns/斜率1.39 · 10→20ns 斜率0.58）
          </span>
        </div>

        <div
          style={{
            height: 54,
            borderRadius: 10,
            padding: "0 16px",
            background: `${C.bg}ea`,
            border: `2px dashed ${K.dpi}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: K.dpi }}>
            DPI1 (δ_host)
          </span>
          <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
            22.5→40.5→46.7→77.4 ns（+18.0ns/斜率1.80 · 10→20ns 斜率0.63）
          </span>
        </div>

        <div
          style={{
            height: 96,
            borderRadius: 12,
            padding: "10px 18px",
            background: `linear-gradient(90deg, ${K.host}2a 0%, ${C.panel}f5 100%)`,
            border: `2.5px solid ${K.host}`,
            display: "flex",
            alignItems: "center",
            gap: 20,
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 46, fontWeight: 900, color: C.cyan }}>
            +13.9ns
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.cyan }}>
              主机首档 δ_host=10 ns（双 lfence）阶跃 +13.9 / +18.0 ns（斜率 1.39 / 1.80）
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.text }}>
              10→20 ns 边际斜率 0.58 / 0.63，40 ns 双双跌入 76.9 / 77.4 ns 慢状态
            </div>
          </div>
        </div>
      </div>

      {/* ==================== RIGHT PANEL: CARBON & ARGON BREAKDOWN (x: 1242..1838) ==================== */}
      {/* Upper Card: Cue 0 misconception crossfades to Cue 1+ H0 vs H2 comparison bars */}
      <div
        style={{
          position: "absolute",
          left: 1242,
          top: 214,
          width: 596,
          height: 232,
          borderRadius: 12,
          padding: "14px 18px",
          background: `${C.bg}ea`,
          border: `2px solid ${C.muted}88`,
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          opacity: fade(t, T[0] + 0.2, T[1], 0.4),
        }}
      >
        <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.orange }}>
          初版报告为何误判“主卡两侧斜率都 ≈ 1”？
        </div>
        <div style={{ fontFamily: FONT, fontSize: 24, color: C.text, lineHeight: 1.4 }}>
          初版对 δ = 0..60 ns 强行过原点单斜率拟合，忽略了双 isb 最小步长高达 29–32 ns。
        </div>
        <div style={{ fontFamily: MONO, fontSize: 22, color: C.cyan, fontWeight: 700 }}>
          H0 基线：WC1 24.8 ns · DPI1 22.5 ns
        </div>
      </div>

      {/* Cue 1+: H0 24.8 ns vs H2 19.8 ns comparison bars */}
      <div
        style={{
          position: "absolute",
          left: 1242,
          top: 214,
          width: 596,
          height: 232,
          borderRadius: 12,
          padding: "12px 18px",
          background: `${C.bg}f2`,
          border: `2px solid ${C.red}aa`,
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          whiteSpace: "nowrap",
          ...appear(t, T[1] + 0.15),
        }}
      >
        <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.yellow }}>
          ★ 同卡进程 C1：H0 周期 vs H2 周期对比
        </div>

        {/* H0 bar: 24.8 ns */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontFamily: FONT, fontSize: 24, color: C.text, fontWeight: 700 }}>
              H0 主机 + C1 卡
            </span>
            <span style={{ fontFamily: MONO, fontSize: 22, color: K.wc, fontWeight: 800 }}>
              24.8 ns/条 (40.3 M/s)
            </span>
          </div>
          <div style={{ width: "100%", height: 34, background: `${C.panel}`, borderRadius: 7, overflow: "hidden" }}>
            <div
              style={{
                width: `${100 * prog(t, T[1] + 0.25, 0.7)}%`,
                height: "100%",
                background: `linear-gradient(90deg, ${K.host} 0%, ${K.wc} 100%)`,
                display: "flex",
                alignItems: "center",
                paddingLeft: 14,
                fontFamily: MONO,
                fontSize: 22,
                fontWeight: 800,
                color: C.bg,
              }}
            >
              H0 周期 24.8 ns
            </div>
          </div>
        </div>

        {/* H2 bar: 19.8 ns + 5.0 ns slack */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontFamily: FONT, fontSize: 24, color: C.yellow, fontWeight: 800 }}>
              ★ 无旋钮 H2 + C1
            </span>
            <span style={{ fontFamily: MONO, fontSize: 22, color: C.red, fontWeight: 800 }}>
              19.8 ns/条 (50.5 M/s)
            </span>
          </div>
          <div style={{ width: "100%", height: 34, background: `${C.panel}`, borderRadius: 7, display: "flex", overflow: "hidden" }}>
            <div
              style={{
                width: `${(19.8 / 24.8) * 100 * prog(t, T[1] + 0.35, 0.7)}%`,
                height: "100%",
                background: `linear-gradient(90deg, ${C.red} 0%, ${C.orange} 100%)`,
                display: "flex",
                alignItems: "center",
                paddingLeft: 14,
                fontFamily: MONO,
                fontSize: 22,
                fontWeight: 800,
                color: C.text,
              }}
            >
              H2 周期 19.8 ns (核验 50.1–52.2 M/s)
            </div>
            <div
              style={{
                width: `${(5.0 / 24.8) * 100 * prog(t, T[1] + 0.55, 0.6)}%`,
                height: "100%",
                background: `${C.green}33`,
                borderLeft: `2.5px solid ${C.green}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontFamily: MONO,
                fontSize: 22,
                fontWeight: 800,
                color: C.green,
              }}
            >
              -5ns
            </div>
          </div>
        </div>
      </div>

      {/* Lower Card in Right Panel (Cue 2+): Horizontal Pipeline Bottleneck Comparison Bars */}
      <div
        style={{
          position: "absolute",
          left: 1242,
          top: 460,
          width: 596,
          height: 374,
          borderRadius: 12,
          padding: "14px 18px",
          background: `${C.bg}f2`,
          border: `2px solid ${C.green}aa`,
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          whiteSpace: "nowrap",
          ...appear(t, T[2] + 0.15),
        }}
      >
        <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.green }}>
          H0 (24.8 ns/条) 主卡并行流水线拆解：
        </div>

        {/* Host pipeline stage bar (only measured send 9.2 + bpoll 13.6 ns, no invented 2.0 segment) */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontFamily: FONT, fontSize: 24, color: C.text, fontWeight: 700 }}>
              主机侧决定周期
            </span>
            <span style={{ fontFamily: MONO, fontSize: 22, color: C.cyan, fontWeight: 700 }}>
              send 9.2 + bpoll 13.6 ns
            </span>
          </div>
          <div
            style={{
              width: "100%",
              height: 42,
              borderRadius: 8,
              overflow: "hidden",
              display: "flex",
              border: `2px solid ${K.host}`,
            }}
          >
            <div
              style={{
                flex: 9.2,
                background: `${K.req}dd`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontFamily: MONO,
                fontSize: 22,
                fontWeight: 800,
                color: C.bg,
              }}
            >
              send 9.2 ns
            </div>
            <div
              style={{
                flex: 13.6,
                background: `${K.host}ee`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontFamily: MONO,
                fontSize: 22,
                fontWeight: 800,
                color: C.text,
              }}
            >
              bpoll 13.6 ns
            </div>
          </div>
        </div>
        {/* Card 1-core pipeline stage bar with ~5 ns slack */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontFamily: FONT, fontSize: 24, color: C.text, fontWeight: 700 }}>
              单卡核真实上限 ≤ 19.8 ns
            </span>
            <span style={{ fontFamily: FONT, fontSize: 24, color: C.green, fontWeight: 800 }}>
              留有 ~5 ns 余量！
            </span>
          </div>
          <div
            style={{
              width: "100%",
              height: 42,
              borderRadius: 8,
              overflow: "hidden",
              display: "flex",
              border: `2px solid ${C.green}`,
            }}
          >
            <div
              style={{
                width: `${(19.8 / 24.8) * 100}%`,
                background: `${K.card}ee`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontFamily: MONO,
                fontSize: 22,
                fontWeight: 800,
                color: C.bg,
              }}
            >
              单卡核上限 ≤ 19.8 ns/条
            </div>
            <div
              style={{
                width: `${(5.0 / 24.8) * 100}%`,
                background: `${C.green}38`,
                borderLeft: `2.5px dashed ${C.green}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontFamily: MONO,
                fontSize: 22,
                fontWeight: 800,
                color: C.green,
              }}
            >
              ~5ns
            </div>
          </div>
        </div>

        {/* Hero takeaway inside Right Panel with [INF] tag */}
        <div
          style={{
            borderRadius: 10,
            padding: "10px 14px",
            background: `${C.green}1a`,
            border: `1.8px solid ${C.green}88`,
            display: "flex",
            alignItems: "center",
            gap: 14,
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 44, fontWeight: 900, color: C.green }}>
            ~5ns
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.green }}>
                δ=0 主机主导，卡核留 ~5 ns 余量
              </span>
              <span
                style={{
                  fontFamily: MONO,
                  fontSize: 20,
                  fontWeight: 800,
                  color: K.inf,
                  border: `1.5px solid ${K.inf}`,
                  borderRadius: 6,
                  padding: "0 6px",
                }}
              >
                [INF]
              </span>
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.text }}>
              双 isb 29–32 ns 跨过余量后切为 1:1 封顶
            </div>
          </div>
        </div>
      </div>

      <Note>按修正后 mrv-53-slopes.py 与 Carbon/Argon 评审复算数据重绘</Note>
    </>
  );
}

export const S06: SceneDef = {
  id: "S06",
  title: "结果二：卡 1 核快状态与 ~5 ns 紧平衡余量",
  stage: 2,
  source: "mrv-53-slopes.py · Carbon & Argon 评审复算 · §2.4",
  dur: DUR,
  cues,
  C: Body,
};
