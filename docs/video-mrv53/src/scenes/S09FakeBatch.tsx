import React from "react";
import {
  Arrow,
  Axes,
  C,
  Card,
  CORE,
  Cue,
  FONT,
  linScale,
  logScale,
  MONO,
  Note,
  Pill,
  prog,
  SceneDef,
  Series,
  Stage,
  appear,
  fade,
  useT,
} from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 10.56, "主机 `busy_poll` 高达 `26–65 ns/条`，到底是 CPU 跑轮询指令慢，还是在**等卡核写回内存**？检查 3 用对齐后的 `--fake-card` 给出定论。", "主机有回复轮询高达二十六到六十五纳秒，到底是 C P U 跑指令慢，还是在等卡核写回内存？检查三用对齐后的假硬件给出定论。"], // 0
  [10.91, 19.81, "新 `--fake-card` 与真实 `drive()` 走**完全同一条发送调用链**（含运行时 `cores` 子环寻址与租约检查），并用 `--fake-batch K` 控制每次轮询取 `K` 条。", "新假硬件与真实发送共用完全同一条代码路径，包含子环寻址与租约原子检查，并用参数控制每次轮询拿到的条数 K。"], // 1
  [20.16, 31.46, "当 `K = 1 → 2 → 4 → 8 → 32` 时，主机纯软件耗时从 **`39.18 / 38.80 ns/条`** 单调降至 **`16.15 / 15.96 ns/条`**（对应 **`61.9–62.7 M/s`** 软件上限）。", "当批量 K 从一、二、四、八增加到三十二时，主机纯软件耗时从约三十九纳秒单调降到十六纳秒，对应六十二兆每秒以上的纯软件上限。"], // 2
  [31.81, 40.61, "对比同样每次拿满 `K = 32` 条的 **假硬件 4 核**（**`16.0–16.6 ns/条`**，`bpoll ≈ 10 ns`）与 **真实卡 4 核 `W = 1024`**（**`32.2–42.8 ns/条`**）：", "对比同样每次拿满三十二条的假硬件四核与真实卡四核：假硬件每条总共才十六点六纳秒，其中轮询仅十纳秒。"], // 3
  [40.96, 53.88, "真实 4 核下 `send` 仍是 **`9.3 ns/条`**，唯独 `busy_poll` 暴涨到 **`25.9–33.9 ns/条`**！多出的 **`16–24 ns/条`** 全是主机跨 4 个子环等回复落地的**硬件微等待**。", "而真实四核下发请求仍是九点三纳秒，唯独有回复轮询暴涨到二十五点九至三十三点九纳秒！多出的十六到二十四纳秒，全是主机跨四个子环等回复落地的硬件微等待。"], // 4
];
const DUR = 55.28; // <dur>
// </cues>

const FAKE_1C: [number, number][] = [
  [1, 39.18],
  [2, 29.67],
  [4, 21.83],
  [8, 18.41],
  [32, 16.15],
];

const FAKE_4C: [number, number][] = [
  [1, 38.8],
  [2, 29.07],
  [4, 21.39],
  [8, 18.24],
  [32, 15.96],
];

const POINT_LABELS: {
  k: number;
  c1: string;
  c4: string;
  dx: number;
  dy: number;
  anchor: "start" | "middle" | "end";
}[] = [
  { k: 1, c1: "39.18", c4: "38.80", dx: 14, dy: -20, anchor: "start" },
  { k: 2, c1: "29.67", c4: "29.07", dx: 14, dy: -22, anchor: "start" },
  { k: 4, c1: "21.83", c4: "21.39", dx: -12, dy: 32, anchor: "end" },
  { k: 8, c1: "18.41", c4: "18.24", dx: 0, dy: -24, anchor: "middle" },
  { k: 32, c1: "16.15", c4: "15.96", dx: -12, dy: -24, anchor: "end" },
];

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  // Left chart scales: x in [165..935], y in [350..735]
  const sx = logScale(1, 32, 188, 912);
  const sy = linScale(0, 45, 735, 350);

  const kAxes = prog(t, T[0], 0.7);
  const kRef = prog(t, T[1], 0.8);
  const kCurve = prog(t, T[2], 2.0);
  const kBars = prog(t, T[3] + 0.2, 1.1);
  const kSubring = prog(t, T[4], 1.1);

  // Right panel crossfade: cue 0..2 table fades out cleanly before cue 3..4 bars + subring diagram
  const opEarlyRight = fade(t, T[0] + 0.2, T[3] - 0.1, 0.35);

  // Horizontal bar scale on right panel (0..45 ns -> 0..480 px)
  const barX0 = 1275;
  const barPx = 11.0;

  return (
    <>
      {/* ---------------- HTML LAYER (Cards, Pills, Text, Note) ---------------- */}

      {/* Left Top: Cue 0 initial placeholder or Cue 1 aligned --fake-card call-chain card */}
      <Card
        x={60}
        y={128}
        w={920}
        h={110}
        border={t >= T[1] ? C.cyan : C.line}
        style={{
          ...appear(t, T[0]),
          padding: "12px 18px",
          background: "rgba(20, 27, 46, 0.94)",
          overflow: "hidden",
        }}
      >
        {t < T[1] ? (
          <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", height: "100%" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, whiteSpace: "nowrap" }}>
              <Pill x={0} y={0} color={C.cyan} size={20} solid style={{ position: "static" }}>
                检查 3
              </Pill>
              <span style={{ fontSize: 25, fontWeight: 700, color: C.text }}>
                剥离 PCIe 写回，单测主机 <span style={{ fontFamily: MONO, color: C.cyan }}>drive()</span> 纯软件上限
              </span>
            </div>
            <div style={{ fontSize: 24, color: C.muted, whiteSpace: "nowrap" }}>
              扫描每次轮询条数 <span style={{ fontFamily: MONO, color: C.yellow }}>K = 1, 2, 4, 8, 32</span>，测每条均摊耗时 (ns/条)
            </div>
          </div>
        ) : (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              height: "100%",
              opacity: prog(t, T[1], 0.45),
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", whiteSpace: "nowrap" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <Pill x={0} y={0} color={C.cyan} size={20} solid style={{ position: "static" }}>
                  对齐 --fake-card
                </Pill>
                <span style={{ fontSize: 24, fontWeight: 700, color: C.text }}>
                  与真实 <span style={{ fontFamily: MONO, color: C.cyan }}>drive()</span> 共用同一调用链
                </span>
              </div>
              <span style={{ fontSize: 22, fontFamily: MONO, color: C.yellow }}>--fake-batch K</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 22, fontFamily: MONO, whiteSpace: "nowrap" }}>
              <span style={{ padding: "2px 10px", borderRadius: 6, background: `${C.cyan}22`, border: `1.5px solid ${C.cyan}`, color: C.cyan }}>
                drive()
              </span>
              <span style={{ color: C.muted }}>→</span>
              <span style={{ padding: "2px 10px", borderRadius: 6, background: `${C.green}22`, border: `1.5px solid ${C.green}`, color: C.green }}>
                cores 子环寻址
              </span>
              <span style={{ color: C.muted }}>→</span>
              <span style={{ padding: "2px 10px", borderRadius: 6, background: `${C.yellow}22`, border: `1.5px solid ${C.yellow}`, color: C.yellow }}>
                LeasedSlot 租约
              </span>
              <span style={{ color: C.muted }}>→</span>
              <span style={{ padding: "2px 10px", borderRadius: 6, background: `${C.purple}22`, border: `1.5px solid ${C.purple}`, color: C.purple }}>
                FakeSlot (取 K 条)
              </span>
            </div>
          </div>
        )}
      </Card>

      {/* Left Main Card: Chart container */}
      <Card
        x={60}
        y={248}
        w={920}
        h={600}
        border={C.line}
        style={{
          ...appear(t, T[0]),
          background: "rgba(20, 27, 46, 0.78)",
          padding: "12px 20px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", whiteSpace: "nowrap" }}>
          <span style={{ fontSize: 25, fontWeight: 700, color: C.text }}>
            主机假硬件耗时 vs 每次轮询批量 <span style={{ fontFamily: MONO, color: C.yellow }}>K</span>
          </span>
          <span style={{ fontSize: 22, fontFamily: MONO, color: C.muted }}>mrv-53-phases (c)</span>
        </div>
      </Card>

      {/* Right Top: Core Question Banner (persists from Cue 0..4) */}
      <Card
        x={1020}
        y={128}
        w={840}
        h={110}
        border={t >= T[4] ? C.green : C.yellow}
        style={{
          ...appear(t, T[0]),
          padding: "12px 18px",
          background: "rgba(20, 27, 46, 0.94)",
          overflow: "hidden",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", height: "100%" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, whiteSpace: "nowrap" }}>
            <Pill x={0} y={0} color={C.yellow} size={20} solid style={{ position: "static" }}>
              核心问题
            </Pill>
            <span style={{ fontSize: 25, fontWeight: 700, color: C.text }}>
              主机 <span style={{ fontFamily: MONO, color: C.yellow }}>busy_poll</span> 高达{" "}
              <span style={{ fontFamily: MONO, color: C.red }}>26–65 ns/条</span>，慢在哪？
            </span>
          </div>
          <div style={{ display: "flex", gap: 12, fontSize: 24, whiteSpace: "nowrap" }}>
            <div
              style={{
                flex: 1,
                padding: "3px 12px",
                borderRadius: 8,
                background: t >= T[2] ? "rgba(249, 65, 68, 0.14)" : "rgba(42, 53, 82, 0.45)",
                border: `1.5px solid ${t >= T[2] ? C.red : C.line}`,
                color: t >= T[2] ? C.muted : C.text,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span>A. CPU 指令慢？</span>
              {t >= T[2] && <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.red }}>✗ 仅 16 ns</span>}
            </div>
            <div
              style={{
                flex: 1,
                padding: "3px 12px",
                borderRadius: 8,
                background: t >= T[3] ? "rgba(67, 197, 158, 0.16)" : "rgba(42, 53, 82, 0.45)",
                border: `1.5px solid ${t >= T[3] ? C.green : C.line}`,
                color: C.text,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span>B. 等卡写回？</span>
              {t >= T[3] && <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 700, color: C.green }}>✓ +16–24 ns</span>}
            </div>
          </div>
        </div>
      </Card>

      {/* Right Body (Cue 0..2): Hero Stats & Aligned Fake-Card Data Table */}
      {opEarlyRight > 0.01 && (
        <Card
          x={1020}
          y={248}
          w={840}
          h={600}
          border={C.line}
          style={{
            opacity: opEarlyRight,
            padding: "16px 22px",
            background: "rgba(20, 27, 46, 0.88)",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            overflow: "hidden",
          }}
        >
          {/* Top Hero Stat Strip (>= 44px big metrics) */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <div
              style={{
                padding: "10px 16px",
                borderRadius: 10,
                background: "rgba(67, 197, 158, 0.12)",
                border: `1.5px solid ${C.green}`,
              }}
            >
              <div style={{ fontSize: 44, fontFamily: MONO, fontWeight: 800, color: C.green, lineHeight: 1.05 }}>
                60–63 M/s
              </div>
              <div style={{ fontSize: 24, color: C.text, marginTop: 6 }}>
                K=32 纯软件上限 (16.0–16.6 ns)
              </div>
            </div>
            <div
              style={{
                padding: "10px 16px",
                borderRadius: 10,
                background: "rgba(76, 201, 240, 0.12)",
                border: `1.5px solid ${C.cyan}`,
              }}
            >
              <div style={{ fontSize: 44, fontFamily: MONO, fontWeight: 800, color: C.cyan, lineHeight: 1.05 }}>
                +3.0~10.5 ns
              </div>
              <div style={{ fontSize: 24, color: C.text, marginTop: 6 }}>
                纳入子环/租约调用链软件差
              </div>
            </div>
          </div>

          {/* Data table populated at Cue 2 */}
          <div>
            <div style={{ fontSize: 24, fontWeight: 700, color: C.text, marginBottom: 10 }}>
              对齐假硬件不计时实测表（<span style={{ fontFamily: MONO, color: C.cyan }}>W=64</span> 1核/4核 vs{" "}
              <span style={{ fontFamily: MONO, color: C.yellow }}>W=1024</span> 4核）：
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "95px 1fr 1fr 1fr",
                rowGap: 10,
                columnGap: 12,
                fontSize: 22,
                fontFamily: MONO,
                padding: "12px 16px",
                borderRadius: 10,
                background: "rgba(11, 16, 32, 0.8)",
                border: `1.5px solid ${C.line}`,
                whiteSpace: "nowrap",
              }}
            >
              <div style={{ color: C.muted, fontWeight: 700 }}>批量 K</div>
              <div style={{ color: C.cyan, fontWeight: 700 }}>1核 (W=64)</div>
              <div style={{ color: C.green, fontWeight: 700 }}>4核 (W=64)</div>
              <div style={{ color: C.yellow, fontWeight: 700 }}>4核 (W=1024)</div>

              {[
                ["K = 1", "39.18 (25.5M)", "38.80 (25.8M)", "40.78 (24.5M)"],
                ["K = 2", "29.67 (33.7M)", "29.07 (34.4M)", "31.14 (32.1M)"],
                ["K = 4", "21.83 (45.8M)", "21.39 (46.8M)", "22.11 (45.2M)"],
                ["K = 8", "18.41 (54.3M)", "18.24 (54.8M)", "18.93 (52.8M)"],
                ["K = 32", "16.15 (61.9M)", "15.96 (62.7M)", "16.61 (60.2M)"],
              ].map(([k, v1, v4, v1024], idx) => (
                <React.Fragment key={k}>
                  <div style={{ color: idx === 4 ? C.yellow : C.text, fontWeight: idx === 4 ? 700 : 500 }}>{k}</div>
                  <div style={{ color: C.cyan, opacity: t >= T[2] ? 1 : 0.3 }}>{v1}</div>
                  <div style={{ color: C.green, opacity: t >= T[2] ? 1 : 0.3 }}>{v4}</div>
                  <div style={{ color: C.yellow, opacity: t >= T[2] ? 1 : 0.3 }}>{v1024}</div>
                </React.Fragment>
              ))}
            </div>
          </div>

          <div
            style={{
              padding: "10px 16px",
              borderRadius: 10,
              background: "rgba(67, 197, 158, 0.14)",
              border: `2px solid ${C.green}`,
              fontSize: 24,
              color: C.text,
              whiteSpace: "nowrap",
              opacity: t >= T[2] ? prog(t, T[2] + 0.6, 0.5) : 0.25,
            }}
          >
            <strong style={{ color: C.green }}>结论：</strong>1 核与 4 核纯软件相差{" "}
            <span style={{ fontFamily: MONO, color: C.yellow }}>&lt; 0.6 ns</span>，CPU 算力绝非瓶颈！
          </div>
        </Card>
      )}

      {/* Right Upper Card (Cue 3..4): K = 32 Phase Breakdown Comparison */}
      {t >= T[3] && (
        <Card
          x={1020}
          y={248}
          w={840}
          h={302}
          border={C.cyan}
          style={{
            ...appear(t, T[3] + 0.15),
            padding: "12px 20px",
            background: "rgba(20, 27, 46, 0.92)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", whiteSpace: "nowrap" }}>
            <span style={{ fontSize: 25, fontWeight: 700, color: C.text }}>
              同为 <span style={{ fontFamily: MONO, color: C.yellow }}>K = 32</span> 满批：逐段耗时横向对比
            </span>
            <div style={{ display: "flex", gap: 14, fontSize: 22, fontFamily: MONO }}>
              <span style={{ color: C.cyan }}>■ send</span>
              <span style={{ color: C.orange }}>■ bpoll</span>
              <span style={{ color: C.purple }}>■ tail</span>
            </div>
          </div>
        </Card>
      )}

      {/* Right Lower Card (Cue 4): 4-Subring Scan Schematic & Hardware Micro-Wait */}
      {t >= T[4] && (
        <Card
          x={1020}
          y={562}
          w={840}
          h={286}
          border={C.orange}
          style={{
            ...appear(t, T[4]),
            padding: "12px 20px",
            background: "rgba(20, 27, 46, 0.94)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", whiteSpace: "nowrap" }}>
            <span style={{ fontSize: 25, fontWeight: 700, color: C.text }}>
              主机 <span style={{ fontFamily: MONO, color: C.cyan }}>RingReplies::poll</span> 顺次扫 4 子环 (
              <span style={{ fontFamily: MONO, color: C.yellow }}>core 0..3</span>)
            </span>
            <span style={{ fontSize: 22, fontFamily: MONO, color: C.orange }}>K = 32</span>
          </div>
        </Card>
      )}

      <Note>按 mrv-53-phases.py (c) 与检查 2/3 对照数据重绘（runs.jsonl, lost=bad=torn=0）</Note>

      {/* ---------------- SVG STAGE LAYER ---------------- */}
      <Stage>
        {/* 1. Left Chart Axes (Cue 0) */}
        <Axes
          x0={165}
          x1={935}
          y0={735}
          y1={350}
          sx={sx}
          sy={sy}
          xticks={[
            [1, "1"],
            [2, "2"],
            [4, "4"],
            [8, "8"],
            [32, "32"],
          ]}
          yticks={[
            [0, "0"],
            [10, "10"],
            [20, "20"],
            [30, "30"],
            [40, "40"],
            [45, "45"],
          ]}
          xlabel="每次轮询批量 K（--fake-batch K，对数刻度）"
          ylabel="每条消息耗时 (ns/条)"
          opacity={kAxes}
          size={22}
        />

        {/* 2. Old Fake-Card Horizontal Reference Dashed Lines (Cue 1) */}
        {kRef > 0 && (
          <g opacity={kRef}>
            {/* Old K = 1: 28.7 ns */}
            <line
              x1={165}
              y1={sy(28.7)}
              x2={165 + 770 * kRef}
              y2={sy(28.7)}
              stroke={C.yellow}
              strokeWidth={2.5}
              strokeDasharray="10 8"
              opacity={0.85}
            />
            <rect x={545} y={sy(28.7) - 36} width={385} height={30} rx={6} fill="rgba(11, 16, 32, 0.92)" stroke={C.yellow} strokeWidth={1.5} />
            <text x={737} y={sy(28.7) - 21} textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fontWeight={700} fill={C.yellow}>
              旧假硬件 K=1：28.7 ns (34.8M)
            </text>

            {/* Old K = 32: 13.2 ns */}
            <line
              x1={165}
              y1={sy(13.2)}
              x2={165 + 770 * kRef}
              y2={sy(13.2)}
              stroke={C.purple}
              strokeWidth={2.5}
              strokeDasharray="10 8"
              opacity={0.9}
            />
            <rect x={535} y={sy(13.2) + 8} width={395} height={30} rx={6} fill="rgba(11, 16, 32, 0.92)" stroke={C.purple} strokeWidth={1.5} />
            <text x={732} y={sy(13.2) + 23} textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fontWeight={700} fill={C.purple}>
              旧假硬件 K=32：13.2 ns (75.8M)
            </text>
          </g>
        )}

        {/* 3. Aligned Fake-Card Measured Series: 1-core (cyan) & 4-core (green) (Cue 2) */}
        {kCurve > 0 && (
          <g>
            <Series pts={FAKE_1C} sx={sx} sy={sy} k={kCurve} color={C.cyan} w={5} r={7} />
            <Series pts={FAKE_4C} sx={sx} sy={sy} k={kCurve} color={C.green} w={4.5} dash="10 5" r={6} />

            {/* W=1024 4-core K=32 marker: 16.61 ns */}
            {kCurve > 0.92 && (
              <g opacity={prog(t, T[2] + 1.8, 0.5)}>
                <polygon
                  points={`${sx(32)},${sy(16.61) - 9} ${sx(32) + 9},${sy(16.61)} ${sx(32)},${sy(16.61) + 9} ${sx(32) - 9},${sy(16.61)}`}
                  fill={C.yellow}
                  stroke={C.bg}
                  strokeWidth={2}
                />
              </g>
            )}

            {/* Data callouts along the curve */}
            {POINT_LABELS.map((pt, i) => {
              const showPt = prog(t, T[2] + i * 0.38, 0.45);
              if (showPt <= 0) return null;
              const px = sx(pt.k);
              const py = sy(FAKE_1C[i][1]);
              return (
                <g key={pt.k} opacity={showPt} fontFamily={MONO}>
                  <text
                    x={px + pt.dx}
                    y={py + pt.dy}
                    textAnchor={pt.anchor}
                    fontSize={22}
                    fontWeight={700}
                    fill={C.text}
                  >
                    <tspan fill={C.cyan}>{pt.c1}</tspan>
                    <tspan fill={C.muted}>/</tspan>
                    <tspan fill={C.green}>{pt.c4}</tspan>
                  </text>
                </g>
              );
            })}

            {/* Chart Legend & Upper-Right Ceiling Callout Box (strictly inside x: 390..955) */}
            <g opacity={prog(t, T[2] + 0.8, 0.6)} fontFamily={FONT}>
              <rect x={390} y={312} width={565} height={116} rx={10} fill="rgba(11, 16, 32, 0.94)" stroke={C.green} strokeWidth={2} />
              {/* Legend row 1 */}
              <line x1={408} y1={338} x2={444} y2={338} stroke={C.cyan} strokeWidth={5} />
              <circle cx={426} cy={338} r={5} fill={C.cyan} />
              <text x={454} y={338} dominantBaseline="central" fontSize={24} fill={C.text} fontWeight={600}>
                1核子环 (W=64)：25.52 → 61.93 M/s
              </text>
              {/* Legend row 2 */}
              <line x1={408} y1={370} x2={444} y2={370} stroke={C.green} strokeWidth={4.5} strokeDasharray="8 4" />
              <circle cx={426} cy={370} r={5} fill={C.green} />
               <text x={454} y={370} dominantBaseline="central" fontSize={24} fill={C.text} fontWeight={600}>
                 4核子环 (W=64)：25.77 → 62.67 M/s
               </text>
               {/* Ceiling highlight */}
               <text x={408} y={404} dominantBaseline="central" fontSize={24} fill={C.yellow} fontWeight={700}>
                 ★ K=32 纯软件上限：60–63 M/s (16.0–16.61 ns)
               </text>
            </g>
          </g>
        )}

        {/* 4. Right Upper: K = 32 Horizontal Phase Breakdown Bars (Cue 3..4) */}
        {t >= T[3] && kBars > 0 && (
          <g opacity={prog(t, T[3] + 0.2, 0.5)} fontFamily={FONT}>
            {/* Row 1: Fake Card 4-core (W=64/1024) */}
            <text x={1040} y={316} fontSize={24} fontWeight={700} fill={C.green}>
              假硬件 4 核
            </text>
            <text x={1040} y={344} fontSize={22} fontFamily={MONO} fill={C.muted}>
              15.96–16.61 ns
            </text>
            {/* send 8.9 */}
            <rect x={barX0} y={300} width={8.9 * barPx * kBars} height={42} rx={5} fill={`${C.cyan}40`} stroke={C.cyan} strokeWidth={2} />
            <text x={barX0 + 4.45 * barPx} y={321} textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fontWeight={700} fill={C.text}>
              8.9
            </text>
            {/* bpoll 9.9-10.5 (draw 10.2) */}
            <rect
              x={barX0 + 8.9 * barPx * kBars}
              y={300}
              width={10.2 * barPx * kBars}
              height={42}
              rx={5}
              fill={`${C.green}45`}
              stroke={C.green}
              strokeWidth={2}
            />
            <text
              x={barX0 + (8.9 + 5.1) * barPx * kBars}
              y={321}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={22}
              fontFamily={MONO}
              fontWeight={700}
              fill={C.text}
            >
              9.9–10.5
            </text>
            {/* tail 0.5 */}
            <rect
              x={barX0 + 19.1 * barPx * kBars}
              y={300}
              width={Math.max(6, 0.5 * barPx * kBars)}
              height={42}
              rx={3}
              fill={`${C.purple}70`}
              stroke={C.purple}
              strokeWidth={1.5}
            />
            <text
              x={barX0 + 19.8 * barPx * kBars + 10}
              y={321}
              dominantBaseline="central"
              fontSize={22}
              fontFamily={MONO}
              fontWeight={700}
              fill={C.green}
            >
              tail 0.5 ns
            </text>

            {/* Row 2: Real Card 1-core fast state (WC1 / DPI1 W=1024) */}
            <text x={1040} y={386} fontSize={24} fontWeight={700} fill={C.cyan}>
              真卡 1 核快态
            </text>
            <text x={1040} y={414} fontSize={22} fontFamily={MONO} fill={C.muted}>
              19.8–22.5 ns
            </text>
            {/* send 8.9-9.2 */}
            <rect x={barX0} y={370} width={9.1 * barPx * kBars} height={42} rx={5} fill={`${C.cyan}40`} stroke={C.cyan} strokeWidth={2} />
            <text x={barX0 + 4.55 * barPx} y={391} textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fontWeight={700} fill={C.text}>
              8.9–9.2
            </text>
            {/* bpoll 13.5-13.6 */}
            <rect
              x={barX0 + 9.1 * barPx * kBars}
              y={370}
              width={13.6 * barPx * kBars}
              height={42}
              rx={5}
              fill={`${C.yellow}40`}
              stroke={C.yellow}
              strokeWidth={2}
            />
            <text
              x={barX0 + (9.1 + 6.8) * barPx * kBars}
              y={391}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={22}
              fontFamily={MONO}
              fontWeight={700}
              fill={C.text}
            >
              13.5–13.6
            </text>
            {/* tail 0.5 */}
            <rect
              x={barX0 + 22.7 * barPx * kBars}
              y={370}
              width={Math.max(6, 0.5 * barPx * kBars)}
              height={42}
              rx={3}
              fill={`${C.purple}70`}
              stroke={C.purple}
              strokeWidth={1.5}
            />
            <text
              x={barX0 + 23.4 * barPx * kBars + 10}
              y={391}
              dominantBaseline="central"
              fontSize={22}
              fontFamily={MONO}
              fontWeight={700}
              fill={C.cyan}
            >
              WC1 / DPI1
            </text>

            {/* Row 3: Real Card 4-core W=1024 (ST4 / WC4 / DPI4) */}
            <text x={1040} y={456} fontSize={24} fontWeight={700} fill={C.orange}>
              真卡 4 核 W=1024
            </text>
            <text x={1040} y={484} fontSize={22} fontFamily={MONO} fill={C.muted}>
              32.2–42.8 ns
            </text>
            {/* send 9.3 */}
            <rect x={barX0} y={440} width={9.3 * barPx * kBars} height={42} rx={5} fill={`${C.cyan}40`} stroke={C.cyan} strokeWidth={2} />
            <text x={barX0 + 4.65 * barPx} y={461} textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fontWeight={700} fill={C.text}>
              9.3
            </text>
            {/* bpoll 25.9 / 32.5 / 33.9 */}
            <rect
              x={barX0 + 9.3 * barPx * kBars}
              y={440}
              width={33.9 * barPx * kBars}
              height={42}
              rx={5}
              fill={`${C.red}45`}
              stroke={C.red}
              strokeWidth={2.5}
            />
            {/* Sub-markers for ST4 (25.9) */}
            <line
              x1={barX0 + (9.3 + 25.9) * barPx * kBars}
              y1={440}
              x2={barX0 + (9.3 + 25.9) * barPx * kBars}
              y2={482}
              stroke={C.yellow}
              strokeWidth={2}
              strokeDasharray="4 3"
            />
            <text
              x={barX0 + (9.3 + 17.0) * barPx * kBars}
              y={461}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={22}
              fontFamily={MONO}
              fontWeight={700}
              fill={C.text}
            >
              bpoll 25.9 / 32.5 / 33.9 ns
            </text>
            {/* tail 0.5 */}
            <rect
              x={barX0 + 43.2 * barPx * kBars}
              y={440}
              width={Math.max(6, 0.5 * barPx * kBars)}
              height={42}
              rx={3}
              fill={`${C.purple}70`}
              stroke={C.purple}
              strokeWidth={1.5}
            />

            {/* Delta bracket highlighting +16 ~ +24 ns/msg */}
            <line x1={barX0 + 19.1 * barPx} y1={500} x2={barX0 + 43.2 * barPx} y2={500} stroke={C.red} strokeWidth={2.5} />
            <line x1={barX0 + 19.1 * barPx} y1={492} x2={barX0 + 19.1 * barPx} y2={506} stroke={C.red} strokeWidth={2.5} />
            <line x1={barX0 + 43.2 * barPx} y1={492} x2={barX0 + 43.2 * barPx} y2={506} stroke={C.red} strokeWidth={2.5} />
            <text x={1040} y={528} fontSize={24} fontWeight={700} fill={C.yellow}>
              ST4 / WC4 / DPI4
            </text>
            <text x={barX0 + 28 * barPx} y={528} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.red}>
              ▲ 多出 +16 ~ +24 ns/条 全在 bpoll！
            </text>
          </g>
        )}

        {/* 5. Right Lower: 4-Subring Scan Schematic & Hardware Wait Breakdown (Cue 4) */}
        {t >= T[4] && kSubring > 0 && (
          <g opacity={kSubring} fontFamily={FONT}>
            {/* Left side of lower card: 4 subrings (core 0..3) scanned sequentially by RingReplies::poll */}
            <rect x={1038} y={608} width={316} height={178} rx={10} fill="rgba(11, 16, 32, 0.8)" stroke={C.line} strokeWidth={1.5} />
            <text x={1052} y={634} fontSize={24} fontWeight={700} fill={C.cyan}>
              扫 4 子环取满 32 条：
            </text>

            {[0, 1, 2, 3].map((ci) => {
              const ry = 652 + ci * 31;
              const col = CORE[ci];
              return (
                <g key={ci}>
                  <text x={1052} y={ry + 12} fontSize={22} fontFamily={MONO} fontWeight={700} fill={col}>
                    c{ci}
                  </text>
                  {[0, 1, 2, 3, 4, 5, 6].map((si) => {
                    const sxCell = 1098 + si * 31;
                    const isFirst = ci === 0 && si === 0;
                    const isWaiting = (ci === 2 && si >= 4) || (ci === 3 && si >= 3);
                    return (
                      <rect
                        key={si}
                        x={sxCell}
                        y={ry}
                        width={25}
                        height={21}
                        rx={4}
                        fill={isFirst ? C.yellow : isWaiting ? `${C.red}35` : `${col}35`}
                        stroke={isFirst ? C.yellow : isWaiting ? C.red : col}
                        strokeWidth={isFirst ? 2.5 : 1.5}
                        strokeDasharray={isWaiting ? "4 2" : undefined}
                      />
                    );
                  })}
                </g>
              );
            })}

            {/* Scan arrow across the 4 subrings */}
            <Arrow x1={1332} y1={654} x2={1332} y2={766} color={C.yellow} w={3} head={10} k={kSubring} />

            {/* Right side of lower card: 1st reply vs remaining 31 replies timeline */}
            <rect x={1366} y={608} width={476} height={178} rx={10} fill="rgba(11, 16, 32, 0.8)" stroke={C.line} strokeWidth={1.5} />

            {/* Bar for 1st reply vs 31 replies */}
            <rect x={1382} y={620} width={68} height={42} rx={6} fill={`${C.green}45`} stroke={C.green} strokeWidth={2} />
            <text x={1416} y={641} textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fontWeight={700} fill={C.text}>
              首条
            </text>
            <rect x={1456} y={620} width={370 * kSubring} height={42} rx={6} fill={`${C.red}40`} stroke={C.red} strokeWidth={2} />
            <text x={1641} y={641} textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fontWeight={700} fill={C.text}>
              后 31 条: 805–1060 ns
            </text>

            <text x={1382} y={692} fontSize={24} fill={C.green} fontWeight={700}>
              • 首条 (含 RDTSCP)：仅 22.8 ns
            </text>
            <text x={1382} y={728} fontSize={24} fill={C.orange} fontWeight={700}>
              • 后 31 条均值：26–34 ns/条
            </text>
            <text x={1382} y={764} fontSize={24} fill={C.muted} fontWeight={700}>
              • 减软件 10 ns = <tspan fill={C.red}>16–24 ns 硬件等待</tspan>
            </text>

            {/* Bottom summary banner inside Right Lower Card */}
            <rect x={1038} y={794} width={804} height={42} rx={8} fill="rgba(249, 65, 68, 0.16)" stroke={C.red} strokeWidth={1.8} />
            <text x={1440} y={815} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={700} fill={C.text}>
              定论：多出的 <tspan fontFamily={MONO} fill={C.yellow}>16–24 ns/条</tspan> 全是跨 4 子环等回复落地的<tspan fill={C.red}>硬件微等待</tspan>！
            </text>
          </g>
        )}
      </Stage>
    </>
  );
}

export const S09: SceneDef = {
  id: "S09",
  title: "方法 3：假硬件批量对齐与跨子环等待",
  stage: 3,
  source: "mrv-53-phases.py · --fake-card --fake-batch K · FACTS §4",
  dur: DUR,
  cues,
  C: Body,
};
