import React from "react";
import {
  appear,
  Arrow,
  C,
  Card,
  Cue,
  fade,
  FONT,
  K,
  MONO,
  Note,
  prog,
  SceneDef,
  Stage,
  useT,
} from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 12.06, "给某一段注入忙等 `+δ` 后，消息间隔增量的比值（**斜率 `Δ ÷ δ`**）对应三种机制：**`≈ 1` 在关键路径**、**`≈ 0` 被流水线掩盖**、或**触发状态跳变**。", "给某一段注入微延迟德尔塔后，消息间隔的响应斜率对应三种机制：斜率约等于一代表在关键路径上；约等于零代表被流水线空转余量掩盖；或者触发状态跳变。"], // 0
  [12.41, 21.87, "为防止乱序 CPU 把忙等与收发指令重叠，主机用**双 `lfence` 依赖乘法链**校准 **`δ = 10 / 20 / 40 ns`**。", "为防止乱序核把忙等与收发指令重叠，主机用前后双 lfence 依赖乘法链，校准出十、二十、四十纳秒三档延迟。"], // 1
  [22.22, 33.24, "卡侧 Neoverse N2 核用**双 `isb` 依赖乘法链**；因空双 `isb` 下界即达 **`29–32 ns`**，卡侧取 **`δ = 30 / 45 / 60 ns`**。", "卡侧 Arm 核用前后双 I S B 依赖乘法链；因一对 I S B 本身下界就达二十九到三十二纳秒，卡侧取三十、四十五、六十纳秒三档。"], // 2
  [33.59, 44.37, "为区分“卡核 CPU 忙”与“回程飞行慢”，我们另设**非忙等延后 `D = 0.5 / 1.0 / 2.0 µs`**（`VecDeque` 到期队列，不占卡核 CPU，`D=0` 为同构对照）。", "为区分卡核 C P U 忙和回程路上慢，我们另设非忙等延后 D：用双端队列把回复压后零点五、一、二微秒再发出，期间完全不占卡核算力。"], // 3
  [44.72, 55.78, "在 **`W = 1` 串行对照**下核验：`WC1 / ST1 / DPI1` 主机斜率 **`1.01 / 1.03 / 0.99`**，卡在 `31→60 ns` 边际斜率 **`1.00`**，注入刻度精准。", "在窗口等于一的串行对照下，三种方式的主机斜率为零点九九到一点零三，卡侧三十一到六十纳秒边际斜率恰为一点零零，刻度完全准确。"], // 4
];
const DUR = 57.18; // <dur>
// </cues>

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  // Upper half crossfade: Cue 0-3 critical-path diagram fades out cleanly before T[4] W=1 verification table fades in
  const opTop0 = fade(t, T[0], T[4] - 0.35, 0.35);
  const opTop4 = fade(t, T[4], 1e9, 0.45);

  // Progressive reveal of the 3 rows in Cue 0
  const kRow1 = prog(t, T[0] + 0.15, 0.65);
  const kDelta1 = prog(t, T[0] + 0.5, 0.65);
  const kRow2 = prog(t, T[0] + 0.9, 0.65);
  const kDelta2 = prog(t, T[0] + 1.25, 0.65);
  const kRow3 = prog(t, T[0] + 1.65, 0.65);
  const kDelta3 = prog(t, T[0] + 2.0, 0.65);

  return (
    <>
      <Stage>
        {/* Upper Half (Cue 0..3): Three-row response slope criterion diagram (mrv-53-critical-path.svg redraw) */}
        {opTop0 > 0.001 && (
          <g opacity={opTop0}>
            {/* Background Panel */}
            <rect
              x={60}
              y={135}
              width={1800}
              height={325}
              rx={18}
              fill="rgba(20, 27, 46, 0.92)"
              stroke={C.line}
              strokeWidth={2}
            />
            {/* Top accent bar */}
            <rect x={84} y={135} width={160} height={5} rx={2.5} fill={C.cyan} />

            {/* Header row inside SVG */}
            <text x={88} y={172} fontFamily={FONT} fontSize={25} fontWeight={800} fill={C.text}>
              关键路径响应斜率判据：向某一段注入忙等 +δ，观测每条消息间隔（1 / 吞吐）增量 Δ
            </text>
            <rect
              x={1470}
              y={146}
              width={362}
              height={36}
              rx={8}
              fill="rgba(249,199,79,0.14)"
              stroke={C.yellow}
              strokeWidth={1.5}
            />
            <text
              x={1651}
              y={170}
              textAnchor="middle"
              fontFamily={MONO}
              fontSize={22}
              fontWeight={700}
              fill={C.yellow}
            >
              响应斜率 = Δ(1/吞吐) ÷ δ
            </text>

            {/* Row dividers */}
            <line x1={84} y1={190} x2={1836} y2={190} stroke={C.line} strokeWidth={1.5} />
            <line x1={84} y1={278} x2={1836} y2={278} stroke={C.line} strokeWidth={1} strokeDasharray="6 6" />
            <line x1={84} y1={366} x2={1836} y2={366} stroke={C.line} strokeWidth={1} strokeDasharray="6 6" />

            {/* Reference baseline dashed vertical line at x = 940 */}
            <line x1={940} y1={194} x2={940} y2={450} stroke={C.dim} strokeWidth={2} strokeDasharray="6 5" />
            <text x={932} y={208} textAnchor="end" fontFamily={MONO} fontSize={20} fill={C.muted}>
              原周期 T₀
            </text>

            {/* ---------------- ROW 1: On Critical Path (Slope ≈ 1) ---------------- */}
            <g opacity={kRow1}>
              <text x={88} y={226} fontFamily={FONT} fontSize={26} fontWeight={800} fill={C.red}>
                ① 在关键路径上
              </text>
              <text x={88} y={258} fontFamily={FONT} fontSize={24} fill={C.muted}>
                瓶颈段无余量
              </text>

              {/* Base bar */}
              <rect
                x={380}
                y={210}
                width={560}
                height={48}
                rx={8}
                fill="rgba(76, 201, 240, 0.20)"
                stroke={C.cyan}
                strokeWidth={2}
              />
              <text x={660} y={242} textAnchor="middle" fontFamily={FONT} fontSize={24} fontWeight={700} fill={C.text}>
                原关键路径耗时 T₀（无余量）
              </text>

              {/* Adjacent +delta red block */}
              <rect
                x={944}
                y={210}
                width={176 * kDelta1}
                height={48}
                rx={8}
                fill="rgba(249, 65, 68, 0.34)"
                stroke={C.red}
                strokeWidth={2.5}
              />
              {kDelta1 > 0.45 && (
                <text
                  x={944 + 88 * kDelta1}
                  y={242}
                  textAnchor="middle"
                  fontFamily={MONO}
                  fontSize={24}
                  fontWeight={800}
                  fill={C.red}
                >
                  +δ 忙等
                </text>
              )}

              <text x={1142} y={242} fontFamily={FONT} fontSize={24} fontWeight={700} fill={C.red}>
                → 总间隔一比一拉长 +δ
              </text>

              {/* Right Big Slope Badge (>= 44px) */}
              <rect
                x={1470}
                y={200}
                width={362}
                height={66}
                rx={12}
                fill="rgba(249, 65, 68, 0.16)"
                stroke={C.red}
                strokeWidth={2}
              />
              <text x={1494} y={246} fontFamily={FONT} fontSize={24} fontWeight={800} fill={C.red}>
                斜率 Δ÷δ
              </text>
              <text x={1810} y={249} textAnchor="end" fontFamily={MONO} fontSize={44} fontWeight={800} fill={C.red}>
                ≈ 1
              </text>
            </g>

            {/* ---------------- ROW 2: Masked by Pipeline Slack (Slope ≈ 0) ---------------- */}
            <g opacity={kRow2}>
              <text x={88} y={314} fontFamily={FONT} fontSize={26} fontWeight={800} fill={C.green}>
                ② 被流水线掩盖
              </text>
              <text x={88} y={346} fontFamily={FONT} fontSize={24} fill={C.muted}>
                非瓶颈段有余量
              </text>

              {/* Active work bar */}
              <rect
                x={380}
                y={298}
                width={320}
                height={48}
                rx={8}
                fill="rgba(67, 197, 158, 0.22)"
                stroke={C.green}
                strokeWidth={2}
              />
              <text x={540} y={330} textAnchor="middle" fontFamily={FONT} fontSize={24} fontWeight={700} fill={C.text}>
                本段有效工作
              </text>

              {/* Dashed slack container */}
              <rect
                x={704}
                y={298}
                width={236}
                height={48}
                rx={8}
                fill="url(#hatch)"
                stroke={C.muted}
                strokeWidth={2}
                strokeDasharray="8 6"
              />
              {/* +delta absorbed inside slack */}
              <rect
                x={710}
                y={304}
                width={136 * kDelta2}
                height={36}
                rx={6}
                fill="rgba(249, 65, 68, 0.30)"
                stroke={C.red}
                strokeWidth={2}
              />
              {kDelta2 > 0.45 && (
                <text
                  x={710 + 68 * kDelta2}
                  y={329}
                  textAnchor="middle"
                  fontFamily={MONO}
                  fontSize={22}
                  fontWeight={800}
                  fill={C.red}
                >
                  +δ 吸收
                </text>
              )}
              <text x={892} y={329} textAnchor="middle" fontFamily={MONO} fontSize={20} fontWeight={700} fill={C.muted}>
                slack
              </text>

              <text x={960} y={330} fontFamily={FONT} fontSize={24} fontWeight={700} fill={C.green}>
                → 落入空转余量 (slack)，总间隔不变
              </text>

              {/* Right Big Slope Badge (>= 44px) */}
              <rect
                x={1470}
                y={288}
                width={362}
                height={66}
                rx={12}
                fill="rgba(67, 197, 158, 0.16)"
                stroke={C.green}
                strokeWidth={2}
              />
              <text x={1494} y={334} fontFamily={FONT} fontSize={24} fontWeight={800} fill={C.green}>
                斜率 Δ÷δ
              </text>
              <text x={1810} y={337} textAnchor="end" fontFamily={MONO} fontSize={44} fontWeight={800} fill={C.green}>
                ≈ 0
              </text>
            </g>

            {/* ---------------- ROW 3: Regime / Phase Transition (Jump) ---------------- */}
            <g opacity={kRow3}>
              <text x={88} y={402} fontFamily={FONT} fontSize={26} fontWeight={800} fill={C.yellow}>
                ③ 触发状态跳变
              </text>
              <text x={88} y={434} fontFamily={FONT} fontSize={24} fill={C.muted}>
                改变读写锁相节奏
              </text>

              {/* Original phase-locked bar */}
              <rect
                x={380}
                y={386}
                width={260}
                height={48}
                rx={8}
                fill="rgba(139, 149, 173, 0.16)"
                stroke={C.muted}
                strokeWidth={2}
                strokeDasharray="6 4"
              />
              <text x={510} y={418} textAnchor="middle" fontFamily={FONT} fontSize={24} fill={C.muted}>
                原主卡锁相态
              </text>

              {/* +delta trigger badge */}
              <rect
                x={646}
                y={392}
                width={74}
                height={36}
                rx={6}
                fill="rgba(249, 65, 68, 0.32)"
                stroke={C.red}
                strokeWidth={2}
              />
              <text x={683} y={417} textAnchor="middle" fontFamily={MONO} fontSize={22} fontWeight={800} fill={C.red}>
                +δ
              </text>

              <Arrow x1={728} y1={410} x2={786} y2={410} color={C.yellow} w={4} k={kDelta3} head={12} />

              {/* Jumped new state bar */}
              <rect
                x={796}
                y={386}
                width={250 * kDelta3}
                height={48}
                rx={8}
                fill="rgba(249, 199, 79, 0.26)"
                stroke={C.yellow}
                strokeWidth={2.5}
              />
              {kDelta3 > 0.45 && (
                <text
                  x={796 + 125 * kDelta3}
                  y={418}
                  textAnchor="middle"
                  fontFamily={FONT}
                  fontSize={24}
                  fontWeight={800}
                  fill={C.yellow}
                >
                  跨态骤降或骤升
                </text>
              )}

              <text x={1064} y={418} fontFamily={FONT} fontSize={24} fontWeight={700} fill={C.yellow}>
                → 打破读写锁相节奏
              </text>

              {/* Right Big Slope Badge (>= 44px) */}
              <rect
                x={1470}
                y={376}
                width={362}
                height={66}
                rx={12}
                fill="rgba(249, 199, 79, 0.16)"
                stroke={C.yellow}
                strokeWidth={2}
              />
              <text x={1494} y={422} fontFamily={FONT} fontSize={24} fontWeight={800} fill={C.yellow}>
                响应形态
              </text>
              <text x={1810} y={424} textAnchor="end" fontFamily={FONT} fontSize={44} fontWeight={800} fill={C.yellow}>
                跳变
              </text>
            </g>
          </g>
        )}
      </Stage>

      {/* Upper Half in Cue 4 (y: 135..460): W = 1 Strict Serial Verification Table */}
      {opTop4 > 0.001 && (
        <Card
          x={60}
          y={135}
          w={1800}
          h={325}
          border={K.ok}
          style={{ opacity: opTop4, padding: "14px 24px" }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div style={{ fontFamily: FONT, fontSize: 25, fontWeight: 800, color: C.text }}>
              W = 1 严格串行对照实测结果（去程 → 卡处理 → 回程无并发掩盖，理论斜率 = 1.00）
            </div>
            <div
              style={{
                fontFamily: MONO,
                fontSize: 20,
                fontWeight: 700,
                color: K.ok,
                background: `${K.ok}1f`,
                border: `1.5px solid ${K.ok}`,
                borderRadius: 8,
                padding: "3px 12px",
                whiteSpace: "nowrap",
              }}
            >
              mrv-53-slopes.py 刻度校准通过 ✓
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 370px", gap: 18, alignItems: "stretch" }}>
            {/* Left 3-row W=1 calibration table */}
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {/* Header */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "140px 135px 1.4fr 115px 1.25fr",
                  fontFamily: FONT,
                  fontSize: 24,
                  fontWeight: 700,
                  color: C.muted,
                  padding: "0 12px",
                  whiteSpace: "nowrap",
                }}
              >
                <div>配置</div>
                <div>串行基线</div>
                <div>主机 δ = 0→10→20→40 ns</div>
                <div>主机斜率</div>
                <div>卡侧 δ = 30/45/60 ns</div>
              </div>

              {/* Row 1: WC1 W=1 */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "140px 135px 1.4fr 115px 1.25fr",
                  alignItems: "center",
                  background: "rgba(11,16,32,0.75)",
                  border: `1.5px solid ${K.wc}`,
                  borderRadius: 10,
                  padding: "10px 12px",
                  fontFamily: MONO,
                  fontSize: 24,
                  color: C.text,
                  whiteSpace: "nowrap",
                }}
              >
                <div style={{ fontWeight: 800, color: K.wc }}>WC1 W=1</div>
                <div style={{ fontWeight: 700 }}>925.1 ns</div>
                <div style={{ fontSize: 22 }}>924.2→934.9→944.7→964.3</div>
                <div style={{ fontWeight: 800, color: K.ok, fontSize: 26 }}>1.01</div>
                <div style={{ fontFamily: FONT, fontSize: 24 }}>
                  边际 <strong style={{ fontFamily: MONO, color: K.ok }}>1.00</strong>{" "}
                  <span style={{ fontFamily: MONO, color: C.muted, fontSize: 20 }}>(总1.11)</span>
                </div>
              </div>

              {/* Row 2: ST1 W=1 */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "140px 135px 1.4fr 115px 1.25fr",
                  alignItems: "center",
                  background: "rgba(11,16,32,0.75)",
                  border: `1.5px solid ${K.st}`,
                  borderRadius: 10,
                  padding: "10px 12px",
                  fontFamily: MONO,
                  fontSize: 24,
                  color: C.text,
                  whiteSpace: "nowrap",
                }}
              >
                <div style={{ fontWeight: 800, color: K.st }}>ST1 W=1</div>
                <div style={{ fontWeight: 700 }}>933.3 ns</div>
                <div style={{ fontFamily: FONT, fontSize: 24, color: C.muted }}>
                  RTT 0.91 µs · 1.07 M/s
                </div>
                <div style={{ fontWeight: 800, color: K.ok, fontSize: 26 }}>1.03</div>
                <div style={{ fontFamily: FONT, fontSize: 24 }}>
                  总斜率 <strong style={{ fontFamily: MONO, color: C.orange }}>1.13</strong>{" "}
                  <span style={{ fontFamily: MONO, color: C.muted, fontSize: 20 }}>(含isb)</span>
                </div>
              </div>

              {/* Row 3: DPI1 W=1 */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "140px 135px 1.4fr 115px 1.25fr",
                  alignItems: "center",
                  background: "rgba(11,16,32,0.75)",
                  border: `1.5px solid ${K.dpi}`,
                  borderRadius: 10,
                  padding: "10px 12px",
                  fontFamily: MONO,
                  fontSize: 24,
                  color: C.text,
                  whiteSpace: "nowrap",
                }}
              >
                <div style={{ fontWeight: 800, color: K.dpi }}>DPI1 W=1</div>
                <div style={{ fontWeight: 700 }}>1360.5 ns</div>
                <div style={{ fontFamily: FONT, fontSize: 24, color: C.muted }}>
                  RTT 1.34 µs · 0.73 M/s
                </div>
                <div style={{ fontWeight: 800, color: K.ok, fontSize: 26 }}>0.99</div>
                <div style={{ fontFamily: FONT, fontSize: 24 }}>
                  总斜率 <strong style={{ fontFamily: MONO, color: C.orange }}>1.10</strong>{" "}
                  <span style={{ fontFamily: MONO, color: C.muted, fontSize: 20 }}>(含isb)</span>
                </div>
              </div>
            </div>

            {/* Right Big Calibration Headline Metrics (>= 44px) */}
            <div
              style={{
                background: "rgba(11,16,32,0.85)",
                border: `1.5px solid ${C.line}`,
                borderRadius: 14,
                padding: "10px 14px",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
              }}
            >
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <div
                  style={{
                    background: `${K.host}16`,
                    border: `1.5px solid ${K.host}`,
                    borderRadius: 10,
                    padding: "8px 6px",
                    textAlign: "center",
                    whiteSpace: "nowrap",
                  }}
                >
                  <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 700, color: K.host }}>
                    主机斜率
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 44, fontWeight: 800, color: K.ok, lineHeight: 1.08 }}>
                    1.01
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 20, color: C.muted }}>
                    0.99 – 1.03
                  </div>
                </div>

                <div
                  style={{
                    background: `${K.card}16`,
                    border: `1.5px solid ${K.card}`,
                    borderRadius: 10,
                    padding: "8px 6px",
                    textAlign: "center",
                    whiteSpace: "nowrap",
                  }}
                >
                  <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 700, color: K.card }}>
                    卡边际斜率
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 44, fontWeight: 800, color: K.ok, lineHeight: 1.08 }}>
                    1.00
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 20, color: C.muted }}>
                    31→45→60 ns
                  </div>
                </div>
              </div>

              <div
                style={{
                  fontFamily: FONT,
                  fontSize: 20,
                  color: C.text,
                  background: "rgba(179,136,255,0.12)",
                  border: `1px solid ${K.hold}`,
                  borderRadius: 8,
                  padding: "6px 10px",
                  lineHeight: 1.3,
                  whiteSpace: "nowrap",
                }}
              >
                <div style={{ fontWeight: 700, color: K.hold }}>双 isb 固定跨轮冲刷 ~4.5 ns</div>
                <div style={{ fontFamily: MONO, fontSize: 20 }}>W=1 延后 D=0→2µs：1:1 线性</div>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* ---------------- LOWER HALF (y: 490..842): Three Method Cards ---------------- */}

      {/* Card 1 (Cue 1, x: 60..640, y: 490..842): Host Controlled Busy-Wait H1 */}
      {t >= T[1] && (
        <Card
          x={60}
          y={490}
          w={580}
          h={352}
          border={K.host}
          title="主机受控忙等 H1 (--host-delay-ns)"
          titleColor={K.host}
          style={{ ...appear(t, T[1]), padding: "18px 22px" }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.text, lineHeight: 1.35 }}>
              防止 x86 乱序核把忙等与{" "}
              <span style={{ fontFamily: MONO, color: K.inline, fontWeight: 700 }}>MOVDIR64B</span> / 轮询重叠：
            </div>

            <div
              style={{
                background: "#080c18",
                border: `1.5px solid ${C.line}`,
                borderLeft: `4px solid ${K.host}`,
                borderRadius: 10,
                padding: "9px 14px",
                fontFamily: MONO,
                fontSize: 23,
                color: C.cyan,
                lineHeight: 1.35,
                whiteSpace: "nowrap",
              }}
            >
              <div>lfence; imul; dec; jnz; lfence</div>
              <div style={{ fontSize: 20, color: C.muted, fontFamily: FONT, marginTop: 4 }}>
                前后双栅栏 + 寄存器数据依赖乘法链
              </div>
            </div>

            <div
              style={{
                background: `${K.host}14`,
                border: `1.5px solid ${K.host}`,
                borderRadius: 12,
                padding: "9px 14px",
                whiteSpace: "nowrap",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 700, color: K.host }}>校准三档 δ_host</span>
                <span style={{ fontFamily: MONO, fontSize: 28, fontWeight: 800, color: C.yellow }}>
                  10 / 20 / 40 ns
                </span>
              </div>
              <div style={{ fontFamily: MONO, fontSize: 24, color: C.text, marginTop: 4 }}>
                实测：<span style={{ color: K.ok, fontWeight: 700 }}>9.1–10.0 / 20.0 / 40.0 ns</span>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Card 2 (Cue 2, x: 665..1245, y: 490..842): Card Controlled Busy-Wait C1 */}
      {t >= T[2] && (
        <Card
          x={665}
          y={490}
          w={580}
          h={352}
          border={K.card}
          title="卡侧受控忙等 C1 (--exp-delay-ns)"
          titleColor={K.card}
          style={{ ...appear(t, T[2]), padding: "18px 22px" }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.text, lineHeight: 1.35 }}>
              Neoverse N2 乱序核采用前后双{" "}
              <span style={{ fontFamily: MONO, color: K.card, fontWeight: 700 }}>isb</span> 包裹依赖乘法链：
            </div>

            <div
              style={{
                background: "#080c18",
                border: `1.5px solid ${C.line}`,
                borderLeft: `4px solid ${K.card}`,
                borderRadius: 10,
                padding: "9px 14px",
                fontFamily: MONO,
                fontSize: 23,
                color: C.orange,
                lineHeight: 1.35,
                whiteSpace: "nowrap",
              }}
            >
              <div>isb; (依赖乘法链); isb</div>
              <div style={{ fontSize: 20, color: C.yellow, fontFamily: FONT, marginTop: 4 }}>
                空双 <span style={{ fontFamily: MONO }}>isb</span> 下界即达{" "}
                <strong style={{ fontFamily: MONO }}>29–32 ns</strong>
              </div>
            </div>

            <div
              style={{
                background: `${K.card}14`,
                border: `1.5px solid ${K.card}`,
                borderRadius: 12,
                padding: "9px 14px",
                whiteSpace: "nowrap",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 700, color: K.card }}>校准三档 δ_card</span>
                <span style={{ fontFamily: MONO, fontSize: 28, fontWeight: 800, color: C.yellow }}>
                  30 / 45 / 60 ns
                </span>
              </div>
              <div style={{ fontFamily: MONO, fontSize: 24, color: C.text, marginTop: 4 }}>
                实测：<span style={{ color: K.ok, fontWeight: 700 }}>29–32 / 45 / 59–60 ns</span>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Card 3 (Cue 3, x: 1270..1860, y: 490..842): Card Non-Busy-Wait Hold C1 */}
      {t >= T[3] && (
        <Card
          x={1270}
          y={490}
          w={590}
          h={352}
          border={K.hold}
          title="卡侧非忙等延后 C1 (--exp-hold-ns D)"
          titleColor={K.hold}
          style={{ ...appear(t, T[3]), padding: "18px 22px" }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.text, lineHeight: 1.35 }}>
              用 <span style={{ fontFamily: MONO, color: K.hold, fontWeight: 700 }}>VecDeque</span>{" "}
              到期队列把回复压后 <span style={{ fontFamily: MONO, color: C.yellow, fontWeight: 700 }}>D</span> 再发出：
            </div>

            <div
              style={{
                background: "#080c18",
                border: `1.5px solid ${C.line}`,
                borderLeft: `4px solid ${K.hold}`,
                borderRadius: 10,
                padding: "9px 14px",
                fontFamily: FONT,
                fontSize: 24,
                color: C.text,
                lineHeight: 1.35,
                whiteSpace: "nowrap",
              }}
            >
              <div>
                • 期间继续轮询，<strong style={{ color: K.ok }}>不占卡核 CPU</strong>
              </div>
              <div style={{ fontSize: 20, color: C.muted, marginTop: 3 }}>
                • <span style={{ fontFamily: MONO, color: C.cyan, fontWeight: 700 }}>D = 0</span>{" "}
                入队立即到期（同构对照）
              </div>
            </div>

            <div
              style={{
                background: `${K.hold}14`,
                border: `1.5px solid ${K.hold}`,
                borderRadius: 12,
                padding: "9px 14px",
                whiteSpace: "nowrap",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 700, color: K.hold }}>延后四档 D</span>
                <span style={{ fontFamily: MONO, fontSize: 27, fontWeight: 800, color: C.yellow }}>
                  0 / 0.5 / 1.0 / 2.0 µs
                </span>
              </div>
              <div style={{ fontFamily: FONT, fontSize: 24, color: C.text, marginTop: 4 }}>
                分离 <span style={{ color: K.hold, fontWeight: 700 }}>“纯回程飞行时间”</span>
              </div>
            </div>
          </div>
        </Card>
      )}

      <Note>按 mrv-53-critical-path.svg 与 mrv-53-slopes.py W=1 串行对照重绘</Note>
    </>
  );
}

export const S04: SceneDef = {
  id: "S04",
  title: "方法 1：受控忙等 δ 与非忙等延后 D",
  stage: 1,
  source: "mrv-53-critical-path.svg · FACTS §2.1",
  dur: DUR,
  cues,
  C: Body,
};
