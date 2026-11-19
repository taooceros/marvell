import {
  C,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  PacketStream,
  Pill,
  SceneDef,
  Stage,
  appear,
  countUp,
  fade,
  lerp,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { InfTag, PolyArrow, StatusBadge, TimeBlock } from "./dsa";

// <cues>
const cues: Cue[] = [
  [0.60, 10.20, "既然泡沫来自“每笔一次 `BAR4` 完成写”，直接用 DSA **`Batch`（`opcode = 0x01`）** 让 `B` 笔拷贝共用 **1 次**完成写！", "既然泡沫来自每笔一次反向完成写，解法直截了当：用 D S A 原生 Batch 描述符，让 B 笔四 K B 拷贝共用一次 B A R 四完成写。"], // 0
  [10.55, 21.01, "严格单变量对比：固定总在途 `W = 64`、`CC = 0`、单引擎，只扫聚合度 **`B = 1 → 2 → 4 → 8 → 16 → 32`**，子描述符 `RCR = 0`。", "严格单变量对照：总在途笔数固定六十四、关闭缓存分配、单引擎不变，只把聚合度 B 从一扫到三十二，仅顶层 Batch 写回一次三十二字节。"], // 1
  [21.36, 32.78, "吞吐飙升：**15.28** (`B=1`) → **23.35** (`B=2`) → **31.13** (`B=4`) → **31.32 GB/s** (`B=16`)，翻 **2.05×**！", "吞吐立刻起飞：从十五点二八跃升到二十三点三五，B 等于四直接轰到三十一点一三，B 等于十六冲上三十一点三二 G B 每秒，翻了二点零五倍！"], // 2
  [33.13, 44.15, "我们推断 `B = 4` 单批 `~524 ns` 搬运已在跨批流水线内完全掩盖反向完成写；`B = 32` 仅剩 `2` 批略回落至 **30.57 GB/s**。", "我们推断 B 等于四时单批约五百二十四纳秒的搬运，已在跨批流水线内彻底掩盖了反向完成写；B 等于三十二仅剩两批在途，轻微回落到三十点五七。"], // 3
  [44.50, 55.58, "紧尾同步保留：`W = 1` 批尾差 `p99 − p50` 锁在 **133–220 ns**；甜点 **`B = 4`** `p50` 仅 **2.84 µs** 就拿回 **99.4%** 满带！", "延迟与紧尾同样惊艳：所有聚合度尾差都锁在一百三十三到二百二十纳秒；甜点 B 等于四单批仅二点八四微秒，就拿回百分之九十九点四的满带宽。"], // 4
  [55.93, 69.49, "**`B = 16`** 吞吐（**31.32 vs 31.10 GB/s**）与延迟（**4.42 / 4.61** vs **4.63 / 5.75 µs**）双胜主机区（热源 `512 KiB` + `CC=0` 写穿 DDR5）。", "B 等于十六时，吞吐三十一点三二、中位四点四二、九十九分位四点六一微秒，双维度全胜主机完成区；诚实注明源工作集五百一十二 K B 命中热缓存，目的区写穿内存。"], // 5
];
const DUR = 70.89; // <dur>
// </cues>

const BW_DATA = [
  { b: 1, batches: 64, gbs: 15.28, ns4k: 268.0, color: K.bubble, tag: "" },
  { b: 2, batches: 32, gbs: 23.35, ns4k: 175.4, color: K.wc, tag: "" },
  { b: 4, batches: 16, gbs: 31.13, ns4k: 131.6, color: K.batch, tag: "★ 2.04×" },
  { b: 8, batches: 8, gbs: 31.25, ns4k: 131.1, color: K.rev, tag: "" },
  { b: 16, batches: 4, gbs: 31.32, ns4k: 130.8, color: K.rev, tag: "峰值 2.05×" },
  { b: 32, batches: 2, gbs: 30.57, ns4k: 134.0, color: K.fwd, tag: "-2.4%" },
];

const LAT_DATA = [
  { b: 1, p50: 2.09, p99: 2.29, diff: 201, sweet: false },
  { b: 2, p50: 2.52, p99: 2.65, diff: 133, sweet: false },
  { b: 4, p50: 2.84, p99: 3.02, diff: 184, sweet: true },
  { b: 8, p50: 3.42, p99: 3.62, diff: 200, sweet: false },
  { b: 16, p50: 4.42, p99: 4.61, diff: 190, sweet: false },
  { b: 32, p50: 6.64, p99: 6.86, diff: 220, sweet: false },
];

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  // Phase A: Mechanism comparison (T[0]) + Single-variable setup (T[1]), fades out at T[2]
  const kPhaseA = fade(t, T[0], T[2] - 0.1, 0.5);
  const kTopLane = prog(t, T[0] + 0.1, 0.7);
  const kBotLane = prog(t, T[0] + 1.2, 0.8);
  const kSetup = fade(t, T[1], T[2] - 0.1, 0.5);

  // Phase B: Left Throughput Chart (T[2]..DUR) + Right panels (T[2], T[3], T[4], T[5])
  const kChart = prog(t, T[2], 0.7);
  const kRefLines = prog(t, T[2] + 1.3, 0.6);
  const kR2 = fade(t, T[2], T[3] - 0.1, 0.5);
  const kR3 = fade(t, T[3], T[4] - 0.1, 0.5);
  const kR4 = fade(t, T[4], T[5] - 0.1, 0.5);
  const kR5 = prog(t, T[5], 0.6);

  // Chart geometry (left panel)
  const cx0 = 152;
  const cx1 = 1018;
  const cy0 = 758;
  const cy1 = 232;
  const sy = linScale(0, 36, cy0, cy1);
  const slotW = (cx1 - cx0) / BW_DATA.length;
  const barW = 92;

  return (
    <>
      <Stage>
        {/* ================= PHASE A: MECHANISM COMPARISON (T[0]..T[2]) ================= */}
        {kPhaseA > 0.01 && (
          <g opacity={kPhaseA}>
            {/* Top Lane: B = 1 逐笔提交 */}
            <g opacity={kTopLane}>
              <rect
                x={72}
                y={144}
                width={1776}
                height={186}
                rx={16}
                fill={C.panel}
                stroke={K.bubble}
                strokeWidth={2}
                strokeOpacity={0.6}
              />
              <text x={102} y={182} fontFamily={FONT} fontSize={24} fontWeight={800} fill={K.bubble}>
                B = 1 逐笔提交 · 每笔拷贝后排空一次 BAR4 完成写
              </text>

              {[0, 1, 2].map((i) => {
                const bx = 102 + i * 442;
                const kBlk = prog(t, T[0] + 0.25 + i * 0.22, 0.45);
                return (
                  <g key={i} opacity={kBlk}>
                    <TimeBlock x={bx} y={206} w={196} h={96} label={`4 KiB #${i + 1}`} sub="搬运 ~137 ns" color={K.fwd} />
                    <TimeBlock
                      x={bx + 206}
                      y={206}
                      w={216}
                      h={96}
                      label="+131 ns 泡沫"
                      sub="BAR4 反向排空"
                      color={K.bubble}
                      hatch
                    />
                  </g>
                );
              })}

              <PolyArrow pts={[[1422, 254], [1518, 254]]} color={K.bubble} w={4} k={kTopLane} />
              <rect x={1532} y={204} width={286} height={100} rx={14} fill="rgba(249,65,68,0.14)" stroke={K.bubble} strokeWidth={2.5} />
              <text x={1675} y={244} textAnchor="middle" fontFamily={MONO} fontSize={36} fontWeight={900} fill={K.bubble}>
                15.28 GB/s
              </text>
              <text x={1675} y={282} textAnchor="middle" fontFamily={MONO} fontSize={20} fill={C.text}>
                268.0 ns / 4 KiB
              </text>
            </g>

            {/* Bottom Lane: DSA Batch opcode = 0x01 */}
            <g opacity={kBotLane}>
              <rect
                x={72}
                y={356}
                width={1776}
                height={210}
                rx={16}
                fill={C.panel}
                stroke={K.batch}
                strokeWidth={2.5}
              />
              <text x={102} y={396} fontFamily={FONT} fontSize={24} fontWeight={800} fill={K.batch}>
                DSA Batch（opcode = 0x01）· B 笔拷贝共用 1 次反向 P2P 完成写
              </text>

              {/* 1 x Batch 64 B */}
              <TimeBlock x={102} y={422} w={218} h={108} label="1 × Batch 64 B" sub="opcode = 0x01" color={K.batch} />
              <PolyArrow pts={[[326, 476], [392, 476]]} color={K.batch} w={4} k={kBotLane} />

              {/* Contiguous 4K sub-descriptors */}
              {[0, 1, 2, 3].map((i) => {
                const bx = 404 + i * 168;
                const kSub = prog(t, T[0] + 1.5 + i * 0.18, 0.4);
                const lbl = i < 3 ? `4K #${i + 1}` : "4K #B";
                return (
                  <TimeBlock
                    key={i}
                    x={bx}
                    y={422}
                    w={158}
                    h={108}
                    label={lbl}
                    sub="RCR = 0"
                    color={K.fwd}
                    opacity={kSub}
                  />
                );
              })}

              {/* Bracket under sub-descriptors */}
              <text x={736} y={552} textAnchor="middle" fontFamily={FONT} fontSize={20} fontWeight={700} fill={K.fwd}>
                连续搬运 · 零中间泡沫（子描述符 RCR = 0 不写完成记录）
              </text>

              <PolyArrow pts={[[1076, 476], [1144, 476]]} color={K.rev} w={4} k={kBotLane} />
              <TimeBlock x={1156} y={422} w={270} h={108} label="1 × 32 B BAR4" sub="反向 P2P 完成写" color={K.rev} />

              <PolyArrow pts={[[1434, 476], [1518, 476]]} color={K.rev} w={4} k={kBotLane} />
              <rect x={1532} y={422} width={286} height={108} rx={14} fill="rgba(67,197,158,0.16)" stroke={K.rev} strokeWidth={2.8} />
              <text x={1675} y={464} textAnchor="middle" fontFamily={MONO} fontSize={36} fontWeight={900} fill={K.rev}>
                31.32 GB/s
              </text>
              <text x={1675} y={504} textAnchor="middle" fontFamily={MONO} fontSize={20} fontWeight={700} fill={K.batch}>
                2.05× · 130.8 ns/4K
              </text>

              {/* Ambient packet stream along the Batch pipeline */}
              <PacketStream pts={[[326, 476], [1144, 476]]} travel={rt * 150} gap={140} size={10} color={K.batch} opacity={0.75 * kBotLane} />
            </g>
          </g>
        )}

        {/* ================= PHASE B: LEFT THROUGHPUT BAR CHART (T[2]..DUR) ================= */}
        {kChart > 0.01 && (
          <g opacity={kChart}>
            {/* Panel frame */}
            <rect x={68} y={138} width={988} height={720} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={102} y={182} fontFamily={FONT} fontSize={26} fontWeight={800} fill={C.text}>
              Card BAR4 持续吞吐（W = 64, CC = 0, 单引擎）
            </text>
            <text x={1024} y={182} textAnchor="end" fontFamily={MONO} fontSize={20} fill={C.muted}>
              单位：GB/s (10⁹ B/s)
            </text>

            {/* Horizontal gridlines & Y ticks */}
            {[0, 10, 20, 30, 35].map((v) => {
              const yy = sy(v);
              return (
                <g key={v}>
                  <line x1={cx0} y1={yy} x2={cx1} y2={yy} stroke={C.line} strokeWidth={1.5} strokeDasharray={v === 0 ? undefined : "5 6"} opacity={v === 0 ? 1 : 0.6} />
                  <text x={cx0 - 14} y={yy + 6} textAnchor="end" fontFamily={MONO} fontSize={20} fill={C.muted}>
                    {v}
                  </text>
                </g>
              );
            })}
            <line x1={cx0} y1={cy1} x2={cx0} y2={cy0} stroke={C.dim} strokeWidth={2} />

            {/* Reference lines: Host Memzone B=16 (31.10) & B=1 (29.98) */}
            {kRefLines > 0.01 && (
              <g opacity={kRefLines}>
                <line
                  x1={cx0}
                  y1={sy(31.10)}
                  x2={lerp(cx0, cx1, kRefLines)}
                  y2={sy(31.10)}
                  stroke={C.text}
                  strokeWidth={2}
                  strokeDasharray="8 6"
                  opacity={0.75}
                />
                <rect x={164} y={sy(31.10) - 34} width={286} height={28} rx={6} fill="#0d1528" fillOpacity={0.9} />
                <text x={172} y={sy(31.10) - 14} fontFamily={MONO} fontSize={18} fontWeight={700} fill={C.text}>
                  Host Memzone B=16: 31.10
                </text>

                <line
                  x1={cx0}
                  y1={sy(29.98)}
                  x2={lerp(cx0, cx1, kRefLines)}
                  y2={sy(29.98)}
                  stroke={C.muted}
                  strokeWidth={2}
                  strokeDasharray="4 6"
                  opacity={0.85}
                />
                <rect x={164} y={sy(29.98) + 6} width={274} height={28} rx={6} fill="#0d1528" fillOpacity={0.9} />
                <text x={172} y={sy(29.98) + 26} fontFamily={MONO} fontSize={18} fill={C.muted}>
                  Host Memzone B=1: 29.98
                </text>
              </g>
            )}

            {/* Bars */}
            {BW_DATA.map((d, i) => {
              const kBar = prog(t, T[2] + 0.15 + i * 0.18, 0.75);
              const bx = cx0 + i * slotW + (slotW - barW) / 2;
              const topY = sy(d.gbs * kBar);
              const bh = Math.max(0, cy0 - topY);
              const isFocus =
                (kR3 > 0.3 && (d.b === 4 || d.b === 32)) ||
                (kR4 > 0.3 && d.b === 4) ||
                (kR5 > 0.3 && d.b === 16);
              return (
                <g key={d.b}>
                  <rect
                    x={bx}
                    y={topY}
                    width={barW}
                    height={bh}
                    rx={8}
                    fill={`${d.color}${isFocus ? "55" : "30"}`}
                    stroke={d.color}
                    strokeWidth={isFocus ? 3.5 : 2.4}
                  />
                  {/* Value above bar */}
                  <text
                    x={bx + barW / 2}
                    y={topY - 14}
                    textAnchor="middle"
                    fontFamily={MONO}
                    fontSize={22}
                    fontWeight={800}
                    fill={d.color}
                    opacity={kBar}
                  >
                    {(d.gbs * kBar).toFixed(2)}
                  </text>
                  {/* Tag badge above value for B=4, B=16, B=32 */}
                  {d.tag && (
                    <text
                      x={bx + barW / 2}
                      y={topY - 40}
                      textAnchor="middle"
                      fontFamily={FONT}
                      fontSize={18}
                      fontWeight={800}
                      fill={d.b === 4 ? K.batch : d.b === 16 ? K.rev : K.fwd}
                      opacity={kBar}
                    >
                      {d.tag}
                    </text>
                  )}
                  {/* X labels */}
                  <text x={bx + barW / 2} y={cy0 + 30} textAnchor="middle" fontFamily={MONO} fontSize={22} fontWeight={800} fill={C.text}>
                    B={d.b}
                  </text>
                  <text x={bx + barW / 2} y={cy0 + 56} textAnchor="middle" fontFamily={MONO} fontSize={17} fill={C.muted}>
                    {d.ns4k.toFixed(1)} ns
                  </text>
                </g>
              );
            })}
          </g>
        )}

        {/* ================= BEAT 3 SVG: PIPELINE OVERLAP DIAGRAM (T[3]..T[4]) ================= */}
        {kR3 > 0.01 && (
          <g opacity={kR3}>
            <rect x={1084} y={138} width={768} height={720} rx={18} fill={C.panel} stroke={K.inf} strokeWidth={2.2} />
            <text x={1116} y={182} fontFamily={FONT} fontSize={26} fontWeight={800} fill={C.text}>
              跨批流水线掩盖反向完成写
            </text>
            <InfTag x={1690} y={156} text="[INF] 推断" opacity={kR3} />

            {/* Timeline axis */}
            <line x1={1116} y1={232} x2={1820} y2={232} stroke={C.dim} strokeWidth={1.8} />
            <text x={1116} y={222} fontFamily={MONO} fontSize={17} fill={C.muted}>
              t = 0
            </text>
            <text x={1440} y={222} fontFamily={MONO} fontSize={17} fill={K.batch}>
              ~524 ns (4 × 131 ns)
            </text>
            <text x={1820} y={222} textAnchor="end" fontFamily={MONO} fontSize={17} fill={C.muted}>
              时间 →
            </text>

            {/* Batch #0 */}
            <text x={1116} y={268} fontFamily={MONO} fontSize={20} fontWeight={700} fill={K.fwd}>
              Batch #0 (B = 4)
            </text>
            <TimeBlock
              x={1116}
              y={280}
              w={330 * prog(t, T[3] + 0.15, 0.7)}
              h={82}
              label="4 × 4 KiB 搬运"
              sub="~524 ns 连续写穿 DDR5"
              color={K.fwd}
            />
            <TimeBlock
              x={1452}
              y={280}
              w={196 * prog(t, T[3] + 0.7, 0.5)}
              h={82}
              label="BAR4 完成写"
              sub="32 B 反向 P2P"
              color={K.rev}
            />

            {/* Batch #1 overlapping */}
            <text x={1116} y={404} fontFamily={MONO} fontSize={20} fontWeight={700} fill={K.batch}>
              Batch #1 (紧随其后)
            </text>
            <TimeBlock
              x={1452}
              y={416}
              w={330 * prog(t, T[3] + 0.9, 0.7)}
              h={82}
              label="4 × 4 KiB 搬运"
              sub="完全掩盖 Batch #0 排空!"
              color={K.batch}
            />

            {/* Overlap highlight bracket */}
            <rect
              x={1452}
              y={274}
              width={196}
              height={230}
              rx={10}
              fill="rgba(94,234,212,0.08)"
              stroke={K.inf}
              strokeWidth={2}
              strokeDasharray="6 4"
              opacity={prog(t, T[3] + 1.2, 0.5)}
            />
            <text x={1550} y={534} textAnchor="middle" fontFamily={FONT} fontSize={20} fontWeight={800} fill={K.inf}>
              100% 流水线重叠 · 零残留泡沫
            </text>
          </g>
        )}

        {/* ================= BEAT 4 SVG: TIGHT-TAIL LATENCY PANEL (T[4]..T[5]) ================= */}
        {kR4 > 0.01 && (
          <g opacity={kR4}>
            <rect x={1084} y={138} width={768} height={720} rx={18} fill={C.panel} stroke={K.rev} strokeWidth={2.2} />
            <text x={1116} y={182} fontFamily={FONT} fontSize={26} fontWeight={800} fill={C.text}>
              单批延迟与紧尾（W = 1 批在途）
            </text>
            <text x={1820} y={182} textAnchor="end" fontFamily={MONO} fontSize={19} fill={K.rev}>
              p50 / p99 (µs)
            </text>

            {/* Horizontal bars for B=1..32 */}
            {LAT_DATA.map((d, i) => {
              const rowY = 228 + i * 76;
              const kRow = prog(t, T[4] + 0.1 + i * 0.14, 0.65);
              const sxLat = linScale(0, 7.5, 1210, 1650);
              const x50 = sxLat(d.p50 * kRow);
              const x99 = sxLat(d.p99 * kRow);
              const col = d.sweet ? K.batch : K.rev;
              return (
                <g key={d.b}>
                  {d.sweet && (
                    <rect x={1102} y={rowY - 12} width={732} height={64} rx={12} fill="rgba(249,199,79,0.12)" stroke={K.batch} strokeWidth={2} />
                  )}
                  <text x={1118} y={rowY + 26} fontFamily={MONO} fontSize={22} fontWeight={800} fill={d.sweet ? K.batch : C.text}>
                    B={d.b}
                  </text>
                  {/* Base track */}
                  <line x1={1210} y1={rowY + 20} x2={1650} y2={rowY + 20} stroke={C.line} strokeWidth={4} strokeLinecap="round" />
                  {/* p50 bar */}
                  <line x1={1210} y1={rowY + 20} x2={x50} y2={rowY + 20} stroke={col} strokeWidth={12} strokeLinecap="round" />
                  {/* p50 -> p99 whisker */}
                  <line x1={x50} y1={rowY + 20} x2={x99} y2={rowY + 20} stroke={C.text} strokeWidth={4} />
                  <circle cx={x50} cy={rowY + 20} r={7} fill={col} />
                  <line x1={x99} y1={rowY + 8} x2={x99} y2={rowY + 32} stroke={C.text} strokeWidth={3.5} />

                  {/* Right value */}
                  <text x={1666} y={rowY + 18} fontFamily={MONO} fontSize={20} fontWeight={800} fill={col}>
                    {d.p50.toFixed(2)} / {d.p99.toFixed(2)}
                  </text>
                  <text x={1666} y={rowY + 42} fontFamily={MONO} fontSize={17} fill={C.muted}>
                    Δ {d.diff} ns
                  </text>
                </g>
              );
            })}

            {/* Summary badges at bottom of right panel */}
            <rect x={1112} y={690} width={712} height={62} rx={12} fill="rgba(249,199,79,0.14)" stroke={K.batch} strokeWidth={2} />
            <text x={1468} y={728} textAnchor="middle" fontFamily={FONT} fontSize={22} fontWeight={800} fill={K.batch}>
              ★ 工程甜点 B = 4：仅 2.84 µs 就拿回 99.4% 满带宽！
            </text>

            <rect x={1112} y={768} width={712} height={62} rx={12} fill="rgba(67,197,158,0.14)" stroke={K.rev} strokeWidth={2} />
            <text x={1468} y={806} textAnchor="middle" fontFamily={FONT} fontSize={22} fontWeight={800} fill={K.rev}>
              全区间 B = 1..32 尾差 p99 − p50 稳锁 133–220 ns
            </text>
          </g>
        )}
      </Stage>

      {/* ================= PHASE A HTML: SINGLE-VARIABLE SETUP STRIP (T[1]..T[2]) ================= */}
      {kSetup > 0.01 && (
        <Card
          x={72}
          y={592}
          w={1776}
          h={266}
          border={K.fwd}
          title="严格单变量对照设计（48 次运行 · 0 错误）"
          titleColor={K.fwd}
          style={{ opacity: kSetup }}
        >
          {/* Row 1: 4 Fixed conditions */}
          <div style={{ display: "flex", gap: 18, marginTop: 10 }}>
            {[
              "固定总在途 W = 64 × 4 KiB",
              "目的 CC = 0（写穿 DDR5）",
              "单引擎 engine2.0",
              "Card BAR4 槽 15（零主机转发）",
            ].map((txt, i) => (
              <div
                key={i}
                style={{
                  flex: 1,
                  padding: "12px 16px",
                  borderRadius: 12,
                  background: "rgba(76,201,240,0.10)",
                  border: `1.5px solid ${K.fwd}66`,
                  fontFamily: FONT,
                  fontSize: 21,
                  fontWeight: 700,
                  color: C.text,
                  textAlign: "center",
                }}
              >
                {txt}
              </div>
            ))}
          </div>

          {/* Row 2: Single Variable B sweep */}
          <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 22 }}>
            <div style={{ fontFamily: FONT, fontSize: 23, fontWeight: 800, color: K.batch, whiteSpace: "nowrap" }}>
              唯一变量 B（在途批数 W/B）：
            </div>
            {BW_DATA.map((d, i) => {
              const kPill = prog(t, T[1] + 0.35 + i * 0.16, 0.45);
              return (
                <div
                  key={d.b}
                  style={{
                    flex: 1,
                    padding: "12px 10px",
                    borderRadius: 12,
                    background: d.b === 4 || d.b === 16 ? "rgba(249,199,79,0.16)" : "rgba(255,255,255,0.05)",
                    border: `2px solid ${d.b === 4 || d.b === 16 ? K.batch : C.line}`,
                    textAlign: "center",
                    opacity: kPill,
                    transform: `translateY(${(1 - kPill) * 14}px)`,
                  }}
                >
                  <div style={{ fontFamily: MONO, fontSize: 24, fontWeight: 900, color: d.b === 4 || d.b === 16 ? K.batch : C.text }}>
                    B = {d.b}
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 18, color: C.muted, marginTop: 2 }}>
                    {d.batches} 批在途
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* ================= BEAT 2 HTML: THROUGHPUT JUMP SUMMARY (T[2]..T[3]) ================= */}
      {kR2 > 0.01 && (
        <Card
          x={1084}
          y={138}
          w={768}
          h={720}
          border={K.rev}
          title="吞吐跃升：2.05× 翻倍回满"
          titleColor={K.rev}
          style={{ opacity: kR2 }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 22, marginTop: 14 }}>
            <div
              style={{
                padding: "22px 26px",
                borderRadius: 16,
                background: "rgba(67,197,158,0.12)",
                border: `2px solid ${K.rev}`,
              }}
            >
              <div style={{ fontFamily: FONT, fontSize: 22, color: C.muted, fontWeight: 700 }}>
                Card BAR4 峰值持续吞吐（B = 16）
              </div>
              <div style={{ fontFamily: MONO, fontSize: 64, fontWeight: 900, color: K.rev, marginTop: 6 }}>
                {countUp(t, T[2] + 0.3, 1.1, 31.32, 2, 15.28)} GB/s
              </div>
              <div style={{ fontFamily: MONO, fontSize: 22, color: C.text, marginTop: 6 }}>
                较 B = 1（15.28 GB/s）提升 <span style={{ color: K.batch, fontWeight: 800 }}>2.05×</span>
              </div>
            </div>

            <div
              style={{
                padding: "20px 26px",
                borderRadius: 16,
                background: "rgba(249,199,79,0.10)",
                border: `2px solid ${K.batch}`,
              }}
            >
              <div style={{ fontFamily: FONT, fontSize: 22, color: K.batch, fontWeight: 800 }}>
                ★ B = 4 拐点即达 99.4% 满带宽
              </div>
              <div style={{ fontFamily: MONO, fontSize: 42, fontWeight: 900, color: C.text, marginTop: 6 }}>
                31.13 GB/s <span style={{ fontSize: 24, color: K.batch }}>(2.04×)</span>
              </div>
              <div style={{ fontFamily: FONT, fontSize: 21, color: C.muted, marginTop: 6 }}>
                已反超 Host Memzone B = 1 基线（29.98 GB/s）
              </div>
            </div>

            <div
              style={{
                padding: "18px 26px",
                borderRadius: 16,
                background: "rgba(255,255,255,0.04)",
                border: `1.5px solid ${C.line}`,
              }}
            >
              <div style={{ fontFamily: FONT, fontSize: 22, color: C.muted, fontWeight: 700 }}>
                单笔 4 KiB 摊销耗时
              </div>
              <div style={{ fontFamily: MONO, fontSize: 34, fontWeight: 800, color: K.fwd, marginTop: 8 }}>
                268.0 ns → 130.8 ns / 4K
              </div>
              <div style={{ fontFamily: FONT, fontSize: 20, color: C.muted, marginTop: 6 }}>
                131 ns 反向排空泡沫被彻底压平
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* ================= BEAT 3 HTML: INFERENCE BULLETS BELOW SVG TIMELINE (T[3]..T[4]) ================= */}
      {kR3 > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 1116,
            top: 564,
            width: 704,
            display: "flex",
            flexDirection: "column",
            gap: 16,
            opacity: kR3,
          }}
        >
          <div
            style={{
              padding: "16px 22px",
              borderRadius: 14,
              background: "rgba(94,234,212,0.10)",
              border: `1.5px solid ${K.inf}88`,
              fontFamily: FONT,
              fontSize: 22,
              lineHeight: 1.5,
              color: C.text,
            }}
          >
            <strong style={{ color: K.batch }}>B = 4 零残留泡沫：</strong>单批{" "}
            <span style={{ fontFamily: MONO, color: K.fwd }}>~524 ns</span> 搬运在跨批流水线内完全掩盖反向完成写，优于串行摊销模型{" "}
            <span style={{ fontFamily: MONO, color: C.muted }}>131 + 137/B</span>。
          </div>
          <div
            style={{
              padding: "16px 22px",
              borderRadius: 14,
              background: "rgba(76,201,240,0.08)",
              border: `1.5px solid ${K.fwd}66`,
              fontFamily: FONT,
              fontSize: 22,
              lineHeight: 1.5,
              color: C.text,
            }}
          >
            <strong style={{ color: K.fwd }}>B = 32 轻微回落（−2.4%）：</strong>总在途固定{" "}
            <span style={{ fontFamily: MONO }}>W = 64</span> 时仅剩 <span style={{ fontFamily: MONO }}>W/B = 2</span> 批在途，流水线变浅导致回落至{" "}
            <span style={{ fontFamily: MONO, color: K.fwd, fontWeight: 800 }}>30.57 GB/s</span>。
          </div>
        </div>
      )}

      {/* ================= BEAT 5 HTML: HEAD-TO-HEAD B = 16 VICTORY CARD (T[5]..DUR) ================= */}
      {kR5 > 0.01 && (
        <Card
          x={1084}
          y={138}
          w={768}
          h={720}
          border={K.rev}
          title="B = 16 终极对决：Card BAR4 双维度完胜"
          titleColor={K.rev}
          style={{ opacity: kR5 }}
        >
          {/* Table Header */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1.1fr 1.15fr 1.1fr 0.9fr",
              columnGap: 14,
              padding: "12px 18px",
              borderRadius: 10,
              background: "rgba(255,255,255,0.05)",
              fontFamily: FONT,
              fontSize: 20,
              fontWeight: 800,
              color: C.muted,
              marginTop: 8,
            }}
          >
            <div>指标（B = 16）</div>
            <div style={{ color: K.rev }}>Card BAR4</div>
            <div>Host Memzone</div>
            <div>优势</div>
          </div>

          {/* 3 Metric Rows */}
          {[
            {
              metric: "持续吞吐 (W=64)",
              card: "31.32 GB/s",
              host: "31.10 GB/s",
              delta: "+0.22 GB/s",
            },
            {
              metric: "W = 1 中位 p50",
              card: "4.42 µs",
              host: "4.63 µs",
              delta: "−214 ns",
            },
            {
              metric: "W = 1 尾延迟 p99",
              card: "4.61 µs",
              host: "5.75 µs",
              delta: "−1,142 ns",
            },
          ].map((row, i) => (
            <div
              key={i}
              style={{
                display: "grid",
                gridTemplateColumns: "1.1fr 1.15fr 1.1fr 0.9fr",
                columnGap: 14,
                alignItems: "center",
                padding: "22px 18px",
                marginTop: 14,
                borderRadius: 14,
                background: i === 2 ? "rgba(67,197,158,0.14)" : "rgba(255,255,255,0.03)",
                border: `2px solid ${i === 2 ? K.rev : C.line}`,
                ...appear(t, T[5] + 0.15 + i * 0.2),
              }}
            >
              <div style={{ fontFamily: FONT, fontSize: 21, fontWeight: 700, color: C.text }}>{row.metric}</div>
              <div style={{ fontFamily: MONO, fontSize: i === 0 ? 24 : 28, fontWeight: 900, color: K.rev }}>{row.card}</div>
              <div style={{ fontFamily: MONO, fontSize: i === 0 ? 22 : 24, color: C.muted }}>{row.host}</div>
              <div style={{ fontFamily: MONO, fontSize: 21, fontWeight: 800, color: K.batch }}>{row.delta}</div>
            </div>
          ))}

          {/* Tail spread callout */}
          <div
            style={{
              marginTop: 18,
              padding: "16px 20px",
              borderRadius: 12,
              background: "rgba(249,199,79,0.12)",
              border: `1.5px solid ${K.batch}88`,
              fontFamily: FONT,
              fontSize: 22,
              fontWeight: 700,
              color: C.text,
              textAlign: "center",
            }}
          >
            尾差 <span style={{ fontFamily: MONO }}>p99 − p50</span>：Card BAR4{" "}
            <span style={{ fontFamily: MONO, color: K.rev, fontWeight: 900 }}>190 ns</span> vs Host Memzone{" "}
            <span style={{ fontFamily: MONO, color: K.bubble, fontWeight: 900 }}>1,118 ns</span>（缩窄 5.9×）
          </div>

          {/* Honest footnote badge */}
          <div
            style={{
              marginTop: 18,
              padding: "14px 18px",
              borderRadius: 10,
              background: "rgba(76,201,240,0.08)",
              border: `1.5px solid ${C.line}`,
              fontFamily: MONO,
              fontSize: 18,
              color: C.muted,
              textAlign: "center",
            }}
          >
            条件：源工作集 512 KiB 热 LLC + 目的 CC = 0 写穿 DDR5 · 48 次运行 0 错误
          </div>
        </Card>
      )}
    </>
  );
}

export const S06: SceneDef = {
  id: "S06",
  title: "终极解法：DSA Batch 满带紧尾兼得",
  stage: 5,
  source: "单一变量：聚合度 B = 1..32 · W = 64 · CC = 0",
  dur: DUR,
  cues,
  C: Body,
};
