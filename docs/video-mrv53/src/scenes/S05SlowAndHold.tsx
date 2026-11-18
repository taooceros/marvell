import React from "react";
import {
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

// <cues>
const cues: Cue[] = [
  [0.60, 10.12, "先看非忙等延后 `D`：在 `W = 1024` 全部 6 种配置下，卡上把回复压后 **`0 → 0.5 → 1.0 → 2.0 µs`**，吞吐**完全平坦（变化 `< 2.5%`）**！", "先看非忙等延后 D：在一千零二十四窗口的全部六种配置下，把回复压后零点五到两微秒，吞吐完全平坦，变化不到百分之二点五！"], // 0
  [10.47, 20.89, "如 `WC1 W=1024` 保持 **`30.57 → 30.40 M/s`**，`WC4` 保持 **`22.77 → 22.22 M/s`**；证明**只要窗口 `W` 够大，纯回程飞行时间从不决定吞吐**。", "例如写合并一核保持三十点五兆每秒，写合并四核保持二十二点五兆每秒；这证明只要并发窗口够大，纯回程飞行时间从不决定吞吐。"], // 1
  [21.24, 31.28, "再看三个 **`~13 M/s` 慢状态**（`W=64` 下 `WC4 76.1 ns`、`ST4 73.5 ns`、`DPI4 76.9 ns`）：给 4 个卡核每条加 **`30 / 45 / 60 ns`** 忙等。", "再看六十四窗口下的三个十三兆慢状态：基线每条七十三点五到七十六点九纳秒。给四个卡核每条加三十、四十五、六十纳秒忙等。"], // 2
  [31.63, 40.91, "三条曲线**纹丝不动**：`WC4` 保持 **`76.1 → 75.6 ns`**，`DPI4` **`76.9 → 77.2 ns`**，逐段边际斜率 **`-0.05 ~ +0.02 ≈ 0`**，卡核空转余量充足。", "三条曲线纹丝不动！写合并四核保持七十六点一到七十五点六纳秒，逐段边际斜率全在零附近，说明卡核有大量空转余量。"], // 3
  [41.26, 52.04, "主机加 `δ = 40 ns` 时斜率也仅 **`0.05–0.12`**；而少加 **`10 / 20 ns`** 反而打断慢节奏，使 `WC4 / ST4` 跳升至 **`16.2–17.3 M/s`**（`57.7–61.9 ns`）。", "主机加四十纳秒时斜率也仅零点零五到零点一二；而少加十或二十纳秒反而打断慢节奏，让写合并和直写四核跳升到十六至十七兆每秒。"], // 4
];
const DUR = 53.44; // <dur>
// </cues>

const HOLD_W1024: {
  name: string;
  color: string;
  pts: [number, number][];
  text: string;
}[] = [
  {
    name: "WC1 W=1024",
    color: K.wc,
    pts: [
      [0, 30.57],
      [0.5, 30.43],
      [1.0, 30.41],
      [2.0, 30.4],
    ],
    text: "30.57 → 30.43 → 30.41 → 30.40",
  },
  {
    name: "DPI1 W=1024",
    color: K.dpi,
    pts: [
      [0, 25.27],
      [0.5, 25.04],
      [1.0, 25.22],
      [2.0, 25.12],
    ],
    text: "25.27 → 25.04 → 25.22 → 25.12",
  },
  {
    name: "ST4 W=1024",
    color: K.st,
    pts: [
      [0, 24.61],
      [0.5, 24.03],
      [1.0, 24.1],
      [2.0, 24.15],
    ],
    text: "24.61 → 24.03 → 24.10 → 24.15",
  },
  {
    name: "DPI4 W=1024",
    color: C.purple,
    pts: [
      [0, 23.92],
      [0.5, 23.67],
      [1.0, 23.47],
      [2.0, 23.22],
    ],
    text: "23.92 → 23.67 → 23.47 → 23.22",
  },
  {
    name: "WC4 W=1024",
    color: C.green,
    pts: [
      [0, 22.77],
      [0.5, 22.48],
      [1.0, 22.49],
      [2.0, 22.22],
    ],
    text: "22.77 → 22.48 → 22.49 → 22.22",
  },
  {
    name: "ST1 W=1024",
    color: C.yellow,
    pts: [
      [0, 3.83],
      [0.5, 3.84],
      [1.0, 3.85],
      [2.0, 3.83],
    ],
    text: "3.83 → 3.84 → 3.85 → 3.83",
  },
];

const HOLD_W64: {
  name: string;
  color: string;
  pts: [number, number][];
  text: string;
}[] = [
  {
    name: "WC4 W=64",
    color: K.wc,
    pts: [
      [0, 13.12],
      [0.5, 12.95],
      [1.0, 13.32],
    ],
    text: "13.12 → 12.95 → 13.32 M/s",
  },
  {
    name: "ST4 W=64",
    color: K.st,
    pts: [
      [0, 13.64],
      [0.5, 13.94],
      [1.0, 14.2],
    ],
    text: "13.64 → 13.94 → 14.20 M/s",
  },
  {
    name: "DPI4 W=64",
    color: K.dpi,
    pts: [
      [0, 12.98],
      [0.5, 12.89],
      [1.0, 13.84],
    ],
    text: "12.98 → 12.89 → 13.84 M/s",
  },
];

const SLOW_CARD: {
  name: string;
  baseNs: string;
  baseMs: string;
  color: string;
  pts: [number, number][];
  traj: string;
  slope: string;
}[] = [
  {
    name: "WC4 W=64",
    baseNs: "76.1 ns",
    baseMs: "13.1 M/s",
    color: K.wc,
    pts: [
      [0, 76.1],
      [30, 75.9],
      [45, 75.7],
      [60, 75.6],
    ],
    traj: "76.1→75.9→75.7→75.6 ns",
    slope: "-0.01",
  },
  {
    name: "ST4 W=64",
    baseNs: "73.5 ns",
    baseMs: "13.6 M/s",
    color: K.st,
    pts: [
      [0, 73.5],
      [30, 71.9],
      [45, 71.1],
      [60, 70.4],
    ],
    traj: "73.5→71.9→71.1→70.4 ns",
    slope: "-0.05",
  },
  {
    name: "DPI4 W=64",
    baseNs: "76.9 ns",
    baseMs: "13.0 M/s",
    color: K.dpi,
    pts: [
      [0, 76.9],
      [32, 77.3],
      [45, 77.4],
      [59, 77.2],
    ],
    traj: "76.9→77.3→77.4→77.2 ns",
    slope: "+0.02~-0.02",
  },
];

const SLOW_HOST: {
  name: string;
  color: string;
  pts: [number, number][];
}[] = [
  {
    name: "DPI4 δ_host",
    color: K.dpi,
    pts: [
      [0, 76.6],
      [10, 75.6],
      [20, 72.0],
      [40, 81.8],
    ],
  },
  {
    name: "WC4 δ_host",
    color: K.wc,
    pts: [
      [0, 76.2],
      [10, 60.9],
      [20, 61.4],
      [40, 78.3],
    ],
  },
  {
    name: "ST4 δ_host",
    color: K.st,
    pts: [
      [0, 73.8],
      [10, 57.7],
      [20, 61.9],
      [40, 78.3],
    ],
  },
];

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  // Full-width top plot in Left Panel (a): x in [146, 882], y in [468, 222]
  const sxL = linScale(0, 2.0, 146, 882);
  const syL = linScale(0, 40, 468, 222);

  // Full-width top plot in Right Panel (b): x in [1068, 1824], y in [468, 222]
  const sxR = linScale(0, 60, 1068, 1824);
  const syR = linScale(50, 90, 468, 222);

  const kLeft = prog(t, T[0], 0.8);
  const kW64 = prog(t, T[1], 1.2);
  const kRight = prog(t, T[2], 0.7);
  const kCard = prog(t, T[3], 1.4);
  const kHost = prog(t, T[4], 1.4);

  return (
    <>
      {/* Panel backgrounds (HTML, behind Stage SVG) */}
      <Card
        x={60}
        y={128}
        w={860}
        h={720}
        border={`${K.hold}88`}
        style={{
          background: `linear-gradient(180deg, ${C.panel}f2 0%, ${C.bg}f5 100%)`,
        }}
      />
      <Card
        x={980}
        y={128}
        w={880}
        h={720}
        border={t >= T[2] ? `${K.card}99` : C.line}
        style={{
          background: `linear-gradient(180deg, ${C.panel}f2 0%, ${C.bg}f5 100%)`,
          opacity: t >= T[2] ? 1 : 0.35,
        }}
      />

      {/* Panel titles (>= 24 px) */}
      <Txt x={84} y={142} size={26} weight={800} color={C.text}>
        (a) 卡上非忙等延后 D = 0..2.0 µs 实测吞吐曲线 (--exp-hold-ns)
      </Txt>
      <Txt
        x={1004}
        y={142}
        size={26}
        weight={800}
        color={C.text}
        style={{ opacity: t >= T[2] ? 1 : 0.35 }}
      >
        (b) 三个 13 M/s 慢状态（W=64 4 核）在忙等 δ 下的消息间隔
      </Txt>

      <Stage>
        {/* ==================== LEFT CHART (a) ==================== */}
        <g opacity={kLeft}>
          <Axes
            x0={146}
            x1={882}
            y0={468}
            y1={222}
            sx={sxL}
            sy={syL}
            xticks={[
              [0, "0"],
              [0.5, "0.5"],
              [1.0, "1.0"],
              [2.0, "2.0 µs"],
            ]}
            yticks={[
              [0, "0"],
              [10, "10"],
              [20, "20"],
              [30, "30"],
              [40, "40"],
            ]}
            xlabel="卡上非忙等延后 D (µs)"
            ylabel="吞吐 (Mmsg/s)"
            size={24}
          />

          {/* W = 1024 6 horizontal lines */}
          {HOLD_W1024.map((s, i) => (
            <Series
              key={s.name}
              pts={s.pts}
              sx={sxL}
              sy={syL}
              k={prog(t, T[0] + 0.2 + i * 0.16, 1.0)}
              color={s.color}
              w={4.5}
              r={5.5}
            />
          ))}

          {/* Cue 1: W = 64 3 slow states dashed lines */}
          {HOLD_W64.map((s, i) => (
            <Series
              key={s.name}
              pts={s.pts}
              sx={sxL}
              sy={syL}
              k={prog(t, T[1] + 0.15 + i * 0.18, 1.0)}
              color={s.color}
              w={4}
              r={5.5}
              dash="10 6"
            />
          ))}

          {/* Clean 24px chart annotations in non-overlapping vertical bands */}
          <text
            x={sxL(1.25)}
            y={syL(33.5)}
            fontFamily={MONO}
            fontSize={22}
            fontWeight={800}
            fill={K.wc}
            opacity={prog(t, T[0] + 0.5, 0.4)}
          >
            WC1 W=1024 (30.57→30.40 M/s)
          </text>
          <text
            x={sxL(1.25)}
            y={syL(7.2)}
            fontFamily={MONO}
            fontSize={22}
            fontWeight={800}
            fill={C.yellow}
            opacity={prog(t, T[0] + 0.9, 0.4)}
          >
            ST1 W=1024 (3.83→3.83 M/s)
          </text>
          {kW64 > 0 && (
            <g opacity={kW64}>
              <rect
                x={sxL(0.08)}
                y={syL(39.2)}
                width={356}
                height={38}
                rx={8}
                fill={`${C.green}26`}
                stroke={C.green}
                strokeWidth={2}
              />
              <text
                x={sxL(0.08) + 178}
                y={syL(39.2) + 26}
                textAnchor="middle"
                fontFamily={FONT}
                fontSize={24}
                fontWeight={800}
                fill={C.green}
              >
                W=1024 变化 &lt; 2.5%（全平坦）
              </text>
            </g>
          )}
        </g>

        {/* ==================== RIGHT CHART (b) ==================== */}
        <g opacity={t >= T[2] ? 1 : 0.25}>
          <Axes
            x0={1068}
            x1={1824}
            y0={468}
            y1={222}
            sx={sxR}
            sy={syR}
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
              [50, "50"],
              [60, "60"],
              [70, "70"],
              [80, "80"],
              [90, "90"],
            ]}
            xlabel="受控忙等延迟 δ (ns)"
            ylabel="每条消息间隔 (ns/条)"
            size={24}
          />

          {/* Baseline ~13 M/s horizontal reference band */}
          {kRight > 0 && (
            <g opacity={kRight}>
              <rect
                x={1068}
                y={syR(78.5)}
                width={756}
                height={syR(72.0) - syR(78.5)}
                fill={`${K.slow}18`}
              />
              <line
                x1={1068}
                y1={syR(76.1)}
                x2={1824}
                y2={syR(76.1)}
                stroke={C.dim}
                strokeWidth={1.8}
                strokeDasharray="6 6"
              />
              {SLOW_CARD.map((s, i) => {
                const sc = pop(t, T[2] + 0.2 + i * 0.15, 0.45);
                const cx = sxR(0);
                const cy = syR(s.pts[0][1]);
                return (
                  <g key={`base-${s.name}`} transform={`translate(${cx},${cy}) scale(${sc})`}>
                    <circle r={11} fill={`${s.color}33`} />
                    <circle r={6.5} fill={s.color} stroke={C.bg} strokeWidth={2} />
                  </g>
                );
              })}
            </g>
          )}

          {/* Cue 3: Card busy-wait solid trajectories */}
          {SLOW_CARD.map((s, i) => (
            <Series
              key={`card-${s.name}`}
              pts={s.pts}
              sx={sxR}
              sy={syR}
              k={prog(t, T[3] + 0.1 + i * 0.18, 1.1)}
              color={s.color}
              w={5}
              r={6}
            />
          ))}

          {/* Cue 3 callout inside chart top area: slope ~ 0 */}
          {kCard > 0.4 && (
            <g opacity={prog(t, T[3] + 0.4, 0.5)}>
              <rect
                x={1084}
                y={228}
                width={476}
                height={42}
                rx={8}
                fill={`${C.green}26`}
                stroke={C.green}
                strokeWidth={2}
              />
              <text
                x={1322}
                y={256}
                textAnchor="middle"
                fontFamily={FONT}
                fontSize={24}
                fontWeight={800}
                fill={C.green}
              >
                卡侧斜率 ≈ 0（卡核有大量空转余量）
              </text>
            </g>
          )}

          {/* Cue 4: Host busy-wait dashed trajectories */}
          {SLOW_HOST.map((s, i) => (
            <Series
              key={`host-${s.name}`}
              pts={s.pts}
              sx={sxR}
              sy={syR}
              k={prog(t, T[4] + 0.1 + i * 0.18, 1.1)}
              color={s.color}
              w={4.2}
              r={5.5}
              dash="9 6"
            />
          ))}

          {/* Cue 4 annotations on the chart in open lower-right & top-right zones */}
          {kHost > 0.5 && (
            <g opacity={prog(t, T[4] + 0.5, 0.5)}>
              <rect
                x={sxR(10) - 18}
                y={syR(63.2)}
                width={sxR(20) - sxR(10) + 36}
                height={syR(56.0) - syR(63.2)}
                rx={10}
                fill={`${C.yellow}1f`}
                stroke={C.yellow}
                strokeWidth={2}
                strokeDasharray="6 4"
              />
              <text
                x={sxR(22.5)}
                y={syR(59.2)}
                fontFamily={FONT}
                fontSize={24}
                fontWeight={800}
                fill={C.yellow}
              >
                ← 主机 10/20ns 打断慢节奏 (57.7–61.9 ns)
              </text>
              <text
                x={sxR(42)}
                y={syR(82.5)}
                fontFamily={MONO}
                fontSize={22}
                fontWeight={800}
                fill={C.text}
              >
                δ=40ns: 78.3/81.8ns
              </text>
            </g>
          )}
        </g>
      </Stage>

      {/* ==================== LEFT PANEL BOTTOM DATA AREA (y: 552..834, width: 820) ==================== */}
      {/* Cue 0: 2x3 Grid of all 6 W=1024 configurations (>=24px labels, >=22px MONO) */}
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 552,
          width: 820,
          height: 282,
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gridTemplateRows: "1fr 1fr 1fr",
          gap: "10px 14px",
          opacity: fade(t, T[0] + 0.2, T[1], 0.4),
        }}
      >
        {HOLD_W1024.map((s) => (
          <div
            key={`grid-${s.name}`}
            style={{
              borderRadius: 10,
              padding: "8px 14px",
              background: `${C.bg}e6`,
              border: `2px solid ${s.color}88`,
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              whiteSpace: "nowrap",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: s.color }}>
                {s.name}
              </span>
              <span style={{ fontFamily: FONT, fontSize: 22, fontWeight: 700, color: C.muted }}>
                单位: M/s
              </span>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
              {s.text}
            </div>
          </div>
        ))}
      </div>

      {/* Cue 1+: W=64 3 slow states table + Green conclusion badge */}
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 550,
          width: 820,
          height: 286,
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          ...appear(t, T[1] + 0.15),
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            background: `${C.bg}e6`,
            border: `1.8px solid ${C.line}`,
            borderRadius: 10,
            padding: "8px 16px",
          }}
        >
          {HOLD_W64.map((s) => (
            <div
              key={`w64-${s.name}`}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                whiteSpace: "nowrap",
              }}
            >
              <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: s.color }}>
                {s.name}（虚线）
              </span>
              <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
                {s.text}
              </span>
            </div>
          ))}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              borderTop: `1px solid ${C.line}`,
              paddingTop: 4,
              fontFamily: MONO,
              fontSize: 22,
              color: C.muted,
              whiteSpace: "nowrap",
            }}
          >
            <span>W=1024 代表线：WC1 30.57→30.40 M/s</span>
            <span>WC4 22.77→22.22 M/s</span>
          </div>
        </div>

        {/* Green conclusion badge with >=44px hero number and >=24px label */}
        <div
          style={{
            height: 108,
            borderRadius: 12,
            padding: "10px 18px",
            background: `linear-gradient(90deg, ${C.green}2a 0%, ${C.panel}f5 100%)`,
            border: `2.5px solid ${C.green}`,
            display: "flex",
            alignItems: "center",
            gap: 18,
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 46, fontWeight: 900, color: C.green }}>
            &lt;2.5%
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.green }}>
              只要并发窗口 W 够盖住往返时间，纯回程飞行时间从不决定吞吐！
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.text }}>
              延后 D = 0 → 2.0 µs 下，W=1024 与 W=64 慢状态吞吐均不下降
            </div>
          </div>
        </div>
      </div>

      {/* ==================== RIGHT PANEL BOTTOM DATA AREA (y: 550..836, width: 840) ==================== */}
      {/* Cue 2 & Cue 3: 3 full-width rows for W=64 4-core slow states (baseline + card busy-wait) */}
      <div
        style={{
          position: "absolute",
          left: 1000,
          top: 550,
          width: 840,
          height: 286,
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          opacity: fade(t, T[2] + 0.15, T[4], 0.4),
        }}
      >
        {SLOW_CARD.map((s, i) => {
          const showTraj = prog(t, T[3] + 0.15 + i * 0.15, 0.5);
          return (
            <div
              key={`rrow-${s.name}`}
              style={{
                height: 54,
                borderRadius: 10,
                padding: "0 16px",
                background: `${C.bg}e8`,
                border: `2px solid ${s.color}99`,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                whiteSpace: "nowrap",
              }}
            >
              <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: s.color }}>
                {s.name}
              </span>
              {showTraj > 0.05 ? (
                <span
                  style={{
                    fontFamily: MONO,
                    fontSize: 22,
                    fontWeight: 700,
                    color: C.text,
                    opacity: showTraj,
                  }}
                >
                  {s.traj}（斜率 <strong style={{ color: C.green }}>{s.slope}</strong>）
                </span>
              ) : (
                <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
                  基线 {s.baseNs}/条（{s.baseMs}）
                </span>
              )}
            </div>
          );
        })}

        <div
          style={{
            height: 96,
            borderRadius: 12,
            padding: "10px 18px",
            background: `${C.bg}ee`,
            border: `2px solid ${C.green}`,
            display: "flex",
            alignItems: "center",
            gap: 18,
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 46, fontWeight: 900, color: C.green }}>
            ≈ 0
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.green }}>
              卡侧斜率 ≈ 0：卡核每条加 30/45/60 ns 忙等，消息间隔纹丝不动！
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.text }}>
              证明 13 M/s 慢状态瓶颈不在卡核纯算力，卡核有大量空转余量
            </div>
          </div>
        </div>
      </div>

      {/* Cue 4: Host delta_host trajectories & conclusion */}
      <div
        style={{
          position: "absolute",
          left: 1000,
          top: 550,
          width: 840,
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
            background: `${C.bg}e8`,
            border: `1.8px solid ${C.green}88`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.yellow }}>
            主机 δ=40 ns
          </span>
          <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
            78.3 / 78.3 / 81.8 ns · 斜率 0.05 / 0.11 / 0.12
          </span>
        </div>

        <div
          style={{
            height: 54,
            borderRadius: 10,
            padding: "0 16px",
            background: `${C.bg}e8`,
            border: `2px dashed ${K.wc}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: K.wc }}>
            WC4 (δ_host)
          </span>
          <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
            10/20 ns 降至 60.9 / 61.4 ns（16.4 M/s）
          </span>
        </div>

        <div
          style={{
            height: 54,
            borderRadius: 10,
            padding: "0 16px",
            background: `${C.bg}e8`,
            border: `2px dashed ${K.st}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: K.st }}>
            ST4 (δ_host)
          </span>
          <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.text }}>
            10/20 ns 降至 57.7 / 61.9 ns（17.3 M/s）
          </span>
        </div>

        <div
          style={{
            height: 96,
            borderRadius: 12,
            padding: "10px 18px",
            background: `linear-gradient(90deg, ${C.yellow}28 0%, ${C.panel}f5 100%)`,
            border: `2.5px solid ${C.yellow}`,
            display: "flex",
            alignItems: "center",
            gap: 18,
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 46, fontWeight: 900, color: C.yellow }}>
            17.3M
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.yellow }}>
              主机微延迟打断了主卡慢节奏，吞吐反升至 16.4 / 17.3 M/s！
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.text }}>
              卡侧斜率 ≈ 0，主机 δ=40 ns 处斜率仅 0.05 / 0.11 / 0.12
            </div>
          </div>
        </div>
      </div>

      <Note>按 mrv-53-slopes.py 实测数据重绘（64 B, 批 1, runs.jsonl id≥210）</Note>
    </>
  );
}

export const S05: SceneDef = {
  id: "S05",
  title: "结果一：纯回程时间与三个 13 M/s 慢状态",
  stage: 2,
  source: "mrv-53-slopes.py · FACTS §2.2, §2.3",
  dur: DUR,
  cues,
  C: Body,
};
