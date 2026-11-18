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
  lerp,
  linScale,
  MONO,
  Note,
  Pill,
  prog,
  SceneDef,
  Stage,
  useT,
} from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 9.78, "检查 2 用 `--account-every 64` 随机抽样拆解主机循环三段耗时：**发请求 `send`**、**有回复轮询 `busy_poll`** 与**空轮询 `empty_poll`**。", "检查二用每六十四轮随机抽样计时，将主机循环拆成三段：发请求 send、有回复轮询 busy poll、以及空轮询 empty poll。"], // 0
  [10.13, 24.07, "在 `W=64/1024` 下，发请求 `send` 恒为 **`8.9–9.9 ns/条`**；真正拉开差距的是 **`busy_poll`**：1 核快态仅 **`13.5–13.6 ns`**，4 核 `W=1024` 膨胀至 **`25.9–33.9 ns`**，4 核 `W=64` 慢态高达 **`52.6–64.9 ns/条`**！", "结果显示发请求恒为九纳秒左右；真正拉开差距的是有回复轮询：一核快态仅十三点五纳秒，四核一千零二十四窗口膨胀到二十六至三十四纳秒，四核慢态更达五十三到六十五纳秒！"], // 1
  [24.42, 33.72, "这同时解开了 **`1.0–1.5 µs` 记账时间差**：为何 Little 定律推算的 `W / 吞吐` 总比实测平均往返 `RTT` 多出 `1 µs` 以上？", "这同时解开了一至一点五微秒的记账时间差：为什么按 Little 定律用窗口除以吞吐推算的周期，总比实测平均往返多出一微秒以上？"], // 2
  [34.07, 45.19, "因为 `poll()` 每轮只在**首条回复**到达时打一次 `RDTSCP`（`first_at`）赋给全批 `K` 条；但槽位要等**同批轮询尾 `poll_tail = now − first_at` + 循环尾 + 半批发送**后才重发！", "因为主机每轮只在首条回复到达时打一次时间戳赋给全批 K 条；但槽位要等取完同批剩余回复的轮询尾、循环尾、以及下一轮半批发送之后才重发！"], // 3
  [45.54, 59.44, "三段直接实测之和与 `W / 吞吐 − RTT` 逐项吻合（**误差 `0.5%–2.3% < 3%`**）：如 `WC4 W=1024` 推算 **`1204 ns`** vs 实测 **`1180 ns`**（`poll_tail = 1016 ns` 占 86%）。", "把直接实测的这三段相加，与 Little 定律推算值逐项吻合，误差全部小于百分之三！例如写合并四核推算一千二百零四纳秒，实测一千一百八十纳秒，其中轮询尾占了一千零一十六纳秒。"], // 4
];
const DUR = 60.84; // <dur>
// </cues>

type PhaseBar = {
  name: string;
  sub: string;
  send: number;
  sendTxt: string;
  bpoll: number;
  bpollTxt: string;
  tail: number;
  tailTxt: string;
  kTxt: string;
  untimed: number;
  untimedTxt: string;
};

const PHASE_BARS: PhaseBar[] = [
  {
    name: "WC1",
    sub: "1024快",
    send: 9.2,
    sendTxt: "9.2",
    bpoll: 13.6,
    bpollTxt: "13.6",
    tail: 0.5,
    tailTxt: "0.5",
    kTxt: "K=32",
    untimed: 19.8,
    untimedTxt: "19.8",
  },
  {
    name: "DPI1",
    sub: "1024快",
    send: 8.9,
    sendTxt: "8.9",
    bpoll: 13.5,
    bpollTxt: "13.5",
    tail: 0.6,
    tailTxt: "0.6",
    kTxt: "K=32",
    untimed: 22.4,
    untimedTxt: "22.4",
  },
  {
    name: "WC1",
    sub: "W=64稳",
    send: 9.0,
    sendTxt: "9.0",
    bpoll: 20.5,
    bpollTxt: "20.5",
    tail: 0.5,
    tailTxt: "0.5",
    kTxt: "K=31",
    untimed: 29.6,
    untimedTxt: "29.6",
  },
  {
    name: "ST4",
    sub: "1024双",
    send: 9.3,
    sendTxt: "9.3",
    bpoll: 25.9,
    bpollTxt: "25.9",
    tail: 0.5,
    tailTxt: "0.5",
    kTxt: "K=32",
    untimed: 32.2,
    untimedTxt: "32.2",
  },
  {
    name: "WC4",
    sub: "1024双",
    send: 9.3,
    sendTxt: "9.3",
    bpoll: 32.5,
    bpollTxt: "32.5",
    tail: 0.5,
    tailTxt: "0.5",
    kTxt: "K=32",
    untimed: 41.8,
    untimedTxt: "41.8",
  },
  {
    name: "DPI4",
    sub: "1024双",
    send: 9.3,
    sendTxt: "9.3",
    bpoll: 33.9,
    bpollTxt: "33.9",
    tail: 0.5,
    tailTxt: "0.5",
    kTxt: "K=32",
    untimed: 41.8,
    untimedTxt: "41.8",
  },
  {
    name: "4核慢",
    sub: "W=64",
    send: 9.9,
    sendTxt: "9.0+",
    bpoll: 64.9,
    bpollTxt: "52.6+",
    tail: 1.5,
    tailTxt: "1.5+",
    kTxt: "K=6..13",
    untimed: 76.3,
    untimedTxt: "74~77",
  },
];

type GapPair = {
  name: string;
  sub: string;
  little: number;
  meas: number;
  pollTail: number;
  loopTail: number;
  halfSend: number;
};

const GAP_PAIRS: GapPair[] = [
  { name: "WC1 64起", sub: "1381+11+82", little: 1497, meas: 1475, pollTail: 1381, loopTail: 11, halfSend: 82 },
  { name: "WC4 64", sub: "998+17+57", little: 1079, meas: 1071, pollTail: 998, loopTail: 17, halfSend: 57 },
  { name: "ST4 64", sub: "883+46+62", little: 996, meas: 991, pollTail: 883, loopTail: 46, halfSend: 62 },
  { name: "WC4 1024", sub: "1016+16+148", little: 1204, meas: 1180, pollTail: 1016, loopTail: 16, halfSend: 148 },
  { name: "DPI4 1024", sub: "1060+16+149", little: 1198, meas: 1225, pollTail: 1060, loopTail: 16, halfSend: 149 },
];

const COL_SEND = "#3b82f6";
const COL_BPOLL = C.cyan;
const COL_TAIL = "#94a3b8";

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  const k0 = prog(t, T[0], 0.7);
  const k0Detail = fade(t, T[0] + 0.3, T[1], 0.45);
  const k1Highlight = prog(t, T[1] + 0.9, 0.7);
  const k3 = prog(t, T[3], 0.8);
  const k4 = prog(t, T[4], 0.8);

  // Left stacked bar chart coords (x: 60..930, y: 130..845)
  const lx0 = 138;
  const lx1 = 910;
  const ly0 = 734;
  const ly1 = 344;
  const lsy = linScale(0, 90, ly0, ly1);
  const lSlot = (lx1 - lx0) / PHASE_BARS.length;
  const lBw = 72;

  // Right bottom paired bar chart coords (x: 970..1860, y: 492..845)
  const rx0 = 1052;
  const rx1 = 1842;
  const ry0 = 768;
  const ry1 = 610;
  const rsy = linScale(0, 1650, ry0, ry1);
  const rSlot = (rx1 - rx0) / GAP_PAIRS.length;
  const rBw = 52;

  return (
    <>
      {/* Left panel: (a) Host 3-phase sampled timing */}
      <Card
        x={60}
        y={130}
        w={870}
        h={715}
        border={C.line}
        title="(a) 主机三段抽样计时（--account-every 64）"
        titleColor={C.cyan}
      />

      {/* Right top card: Cue 2 Question Card with >=24px body and >=44px key metric */}
      {t >= T[2] - 0.1 && (
        <Card
          x={970}
          y={130}
          w={890}
          h={128}
          border={C.yellow}
          title="(b) 记账时间差：为何 W / 吞吐 总比平均 RTT 多出 1 µs+？"
          titleColor={C.yellow}
          style={appear(t, T[2], undefined, 16)}
        >
          <div
            style={{
              marginTop: 2,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              fontSize: 24,
              color: C.text,
            }}
          >
            <div>
              Little 推算{" "}
              <span style={{ fontFamily: MONO, fontWeight: 800, color: C.yellow }}>W / 吞吐</span> 减实测平均{" "}
              <span style={{ fontFamily: MONO, fontWeight: 800, color: C.cyan }}>RTT</span>：
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <span style={{ fontSize: 24, fontFamily: MONO, color: C.muted, fontWeight: 700 }}>Δ =</span>
              <span style={{ fontSize: 44, fontFamily: MONO, fontWeight: 900, color: C.yellow, lineHeight: 1 }}>
                1.0–1.5 µs
              </span>
            </div>
          </div>
        </Card>
      )}

      {/* Right middle card: Cue 3 Poll timeline decomposition */}
      {t >= T[3] - 0.1 && (
        <Card
          x={970}
          y={268}
          w={890}
          h={212}
          border={C.cyan}
          title="单轮 RingReplies::poll() 时间轴分解"
          titleColor={C.cyan}
          style={appear(t, T[3], undefined, 18)}
        />
      )}

      {/* Right bottom card: Cue 4 Paired bar chart */}
      {t >= T[4] - 0.1 && (
        <Card
          x={970}
          y={492}
          w={890}
          h={353}
          border={C.green}
          title="记账差闭合：Little 推算 vs 三段实测 (ns)"
          titleColor={C.green}
          style={appear(t, T[4], undefined, 18)}
        />
      )}

      {t >= T[4] && (
        <Pill x={1476} y={500} color={C.green} size={20} solid style={{ opacity: k4 }}>
          误差 0.5%–2.3% &lt; 3% · poll_tail 占 85%–93%
        </Pill>
      )}

      <Stage>
        {/* Left Top Schematic: --account-every 64 3-stage sampling diagram (ALL labels 24px) */}
        <g opacity={k0} fontFamily={FONT}>
          <text x={84} y={214} fontSize={24} fontWeight={800} fill={C.muted}>
            每64轮抽样：
          </text>
          {/* Stage 1: send */}
          <rect x={236} y={188} width={174} height={42} rx={7} fill={COL_SEND} />
          <text x={323} y={209} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={800} fill="#fff">
            ① send 发请求
          </text>
          <Arrow x1={412} y1={209} x2={434} y2={209} color={C.muted} w={3} head={9} />

          {/* Stage 2: busy_poll */}
          <rect x={436} y={188} width={236} height={42} rx={7} fill={COL_BPOLL} />
          <text
            x={554}
            y={209}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={24}
            fontWeight={800}
            fill={C.bg}
          >
            ② busy_poll 轮询
          </text>
          <Arrow x1={674} y1={209} x2={696} y2={209} color={C.muted} w={3} head={9} />

          {/* Stage 3: empty_poll + tail */}
          <rect x={698} y={188} width={212} height={42} rx={7} fill={COL_TAIL} />
          <text
            x={804}
            y={209}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={24}
            fontWeight={800}
            fill={C.bg}
          >
            ③ empty+tail
          </text>

          {/* Sub-legend row at y = 266 (24px) */}
          <text x={84} y={266} fontSize={24} fill={C.muted} fontWeight={700}>
            柱顶 <tspan fill={C.yellow} fontWeight={800}>K = 每轮非空轮询条数</tspan>
          </text>
          <polygon points="435,257 444,248 453,257 444,266" fill={C.red} />
          <text x={462} y={266} fontSize={24} fill={C.red} fontWeight={800}>
            红菱形 ◆ = 不计时 1/吞吐 (ns/条)
          </text>
        </g>

        {/* Left Chart Axes (size=24) */}
        <Axes
          x0={lx0}
          x1={lx1}
          y0={ly0}
          y1={ly1}
          sx={(v) => v}
          sy={lsy}
          xticks={[]}
          yticks={[
            [0, "0"],
            [15, "15"],
            [30, "30"],
            [45, "45"],
            [60, "60"],
            [75, "75"],
            [90, "90"],
          ]}
          ylabel="每条消息均摊耗时 (ns/条)"
          opacity={k0}
          size={24}
        />

        {/* Cue 0 Expanded Sampling Diagram inside plot area (fades out at T[1] when bars rise) */}
        {k0Detail > 0.01 && (
          <g opacity={k0Detail} fontFamily={FONT}>
            <rect
              x={168}
              y={374}
              width={720}
              height={320}
              rx={14}
              fill="rgba(11,16,32,0.92)"
              stroke={C.cyan}
              strokeWidth={2}
            />
            <text x={528} y={418} textAnchor="middle" fontSize={25} fontWeight={800} fill={C.cyan}>
              H2 --account-every 64：每 64 轮随机间隔抽样 1 轮打 RDTSCP
            </text>
            <text x={528} y={454} textAnchor="middle" fontSize={24} fill={C.muted}>
              63 轮全速不打表（防计时污染）· 抽中轮拆解为三段：
            </text>

            {/* Large 3-segment pipeline strip */}
            <rect x={196} y={482} width={190} height={68} rx={10} fill={COL_SEND} />
            <text x={291} y={516} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={800} fill="#fff">
              ① send 发请求
            </text>

            <rect x={394} y={482} width={268} height={68} rx={10} fill={COL_BPOLL} />
            <text x={528} y={516} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={800} fill={C.bg}>
              ② busy_poll 有回复轮询
            </text>

            <rect x={670} y={482} width={190} height={68} rx={10} fill={COL_TAIL} />
            <text x={765} y={516} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={800} fill={C.bg}>
              ③ empty+tail
            </text>

            <text x={291} y={588} textAnchor="middle" fontSize={24} fontWeight={700} fill={COL_SEND}>
              MOVDIR64B 直写
            </text>
            <text x={528} y={588} textAnchor="middle" fontSize={24} fontWeight={700} fill={COL_BPOLL}>
              poll() 取 K 条回复
            </text>
            <text x={765} y={588} textAnchor="middle" fontSize={24} fontWeight={700} fill={COL_TAIL}>
              空轮询 + 收尾
            </text>

            <text x={528} y={654} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.yellow}>
              按该轮回复条数 K 均摊为 ns/条，并与不计时 1/吞吐 (◆) 对照
            </text>
          </g>
        )}

        {/* Cue 1: 7 Stacked Bars + K labels + Red Diamond untimed 1/throughput (ALL labels 24px) */}
        {PHASE_BARS.map((b, i) => {
          const kb = prog(t, T[1] + i * 0.14, 0.65);
          if (kb <= 0) return null;
          const cx = lx0 + lSlot * (i + 0.5);
          const xLeft = cx - lBw / 2;

          const sendH = (ly0 - lsy(b.send)) * kb;
          const bpollH = (ly0 - lsy(b.bpoll)) * kb;
          const tailH = Math.max(4, (ly0 - lsy(b.tail)) * kb);

          const ySendTop = ly0 - sendH;
          const yBpollTop = ySendTop - bpollH;
          const yTailTop = yBpollTop - tailH;
          const yDiamond = lerp(ly0, lsy(b.untimed), kb);

          return (
            <g key={i} fontFamily={FONT}>
              {/* Bottom segment: send */}
              <rect x={xLeft} y={ySendTop} width={lBw} height={sendH} fill={COL_SEND} rx={3} />
              {/* Middle segment: busy_poll */}
              <rect x={xLeft} y={yBpollTop} width={lBw} height={bpollH} fill={COL_BPOLL} rx={3} />
              {/* Top segment: tail / empty_poll */}
              <rect x={xLeft} y={yTailTop} width={lBw} height={tailH} fill={COL_TAIL} rx={2} />

              {/* Segment value labels (ALL 24px) */}
              {kb > 0.7 && (
                <g opacity={(kb - 0.7) / 0.3}>
                  <text
                    x={cx}
                    y={ly0 - sendH / 2}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontSize={24}
                    fontWeight={800}
                    fill="#ffffff"
                  >
                    {b.sendTxt}
                  </text>
                  {i === 6 ? (
                    <>
                      <text
                        x={cx}
                        y={ySendTop - bpollH / 2 - 14}
                        textAnchor="middle"
                        dominantBaseline="central"
                        fontSize={24}
                        fontWeight={800}
                        fill={C.bg}
                      >
                        52.6
                      </text>
                      <text
                        x={cx}
                        y={ySendTop - bpollH / 2 + 14}
                        textAnchor="middle"
                        dominantBaseline="central"
                        fontSize={24}
                        fontWeight={800}
                        fill={C.bg}
                      >
                        ~64.9
                      </text>
                    </>
                  ) : (
                    <text
                      x={cx}
                      y={ySendTop - bpollH / 2}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontSize={24}
                      fontWeight={800}
                      fill={C.bg}
                    >
                      {b.bpollTxt}
                    </text>
                  )}
                  {/* K label & untimed 1/throughput above bar (24px) */}
                  <text x={cx} y={yTailTop - 36} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.yellow}>
                    {b.kTxt}
                  </text>
                  <text x={cx} y={yTailTop - 10} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.red}>
                    {b.untimedTxt}
                  </text>
                </g>
              )}

              {/* Red diamond for untimed 1/throughput */}
              <polygon
                points={`${cx},${yDiamond - 8} ${cx + 8},${yDiamond} ${cx},${yDiamond + 8} ${cx - 8},${yDiamond}`}
                fill={C.red}
                stroke="#ffffff"
                strokeWidth={1.5}
              />

              {/* X-axis category two-line labels (both 24px) */}
              <text x={cx} y={ly0 + 30} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.text}>
                {b.name}
              </text>
              <text x={cx} y={ly0 + 58} textAnchor="middle" fontSize={24} fill={C.muted}>
                {b.sub}
              </text>
            </g>
          );
        })}

        {/* Cue 1 Highlight Box in open upper-left space above Bars 1..6 (x: 150..792, y: 346..440, fontSize=24) */}
        {k1Highlight > 0 && (
          <g opacity={k1Highlight} fontFamily={FONT}>
            <rect
              x={150}
              y={346}
              width={642}
              height={94}
              rx={10}
              fill="rgba(11,16,32,0.94)"
              stroke={C.cyan}
              strokeWidth={2}
            />
            <text x={166} y={382} fontSize={24} fontWeight={700} fill={C.text}>
              发请求 <tspan fill={COL_SEND} fontWeight={800}>send 恒为 8.9–9.9 ns</tspan>（tail ≈ 0.5 ns）
            </text>
            <text x={166} y={420} fontSize={24} fontWeight={800} fill={C.cyan}>
              差距全在 busy_poll：13.5 vs 25.9–33.9 vs 52.6–64.9ns
            </text>
          </g>
        )}

        {/* Cue 3: Single-round poll() timeline decomposition diagram (ALL labels 24px) */}
        {k3 > 0 && (
          <g opacity={k3} fontFamily={FONT}>
            <text x={1832} y={306} textAnchor="end" fontSize={24} fontWeight={700} fill={C.muted}>
              赋给全批 K 条，省每条 10 ns
            </text>

            {/* Segment 0: True RTT (old send_tsc -> first_at) */}
            <rect
              x={996}
              y={386}
              width={162}
              height={44}
              rx={7}
              fill="rgba(76,201,240,0.14)"
              stroke={C.dim}
              strokeWidth={2}
            />
            <text x={1077} y={408} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={700} fill={C.muted}>
              实测 RTT
            </text>

            {/* RDTSCP first_at marker at x = 1162 */}
            <line x1={1162} y1={362} x2={1162} y2={438} stroke={C.yellow} strokeWidth={3.5} />
            <circle cx={1162} cy={362} r={6} fill={C.yellow} />
            <text x={1162} y={350} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.yellow}>
              首条打 RDTSCP (first_at)
            </text>

            {/* Segment 1: poll_tail = now - first_at */}
            <rect x={1166} y={386} width={352 * k3} height={44} rx={7} fill={COL_BPOLL} />
            <text
              x={1342}
              y={408}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={24}
              fontWeight={800}
              fill={C.bg}
            >
              ① 轮询尾 poll_tail (余 K−1 条)
            </text>

            {/* Segment 2: loop_tail */}
            <rect x={1522} y={386} width={138 * k3} height={44} rx={7} fill={C.purple} />
            <text
              x={1591}
              y={408}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={24}
              fontWeight={800}
              fill={C.bg}
            >
              ② loop_tail
            </text>

            {/* Segment 3: 0.5 * T_send */}
            <rect x={1664} y={386} width={166 * k3} height={44} rx={7} fill={COL_SEND} />
            <text
              x={1747}
              y={408}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={24}
              fontWeight={800}
              fill="#ffffff"
            >
              ③ 0.5×send
            </text>

            {/* New send_tsc marker at x = 1832 */}
            <line x1={1832} y1={362} x2={1832} y2={438} stroke={C.green} strokeWidth={3.5} />
            <text x={1832} y={350} textAnchor="end" fontSize={24} fontWeight={800} fill={C.green}>
              重发新 send_tsc
            </text>

            {/* Bottom brace spanning ① + ② + ③ */}
            <path
              d="M 1166 434 L 1166 443 L 1832 443 L 1832 434"
              fill="none"
              stroke={C.yellow}
              strokeWidth={2.5}
            />
            <text x={1499} y={468} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.yellow}>
              重发前等待 = ① poll_tail (now − first_at) + ② loop_tail + ③ 0.5×send
            </text>
          </g>
        )}

        {/* Cue 4: Right Bottom Paired Bar Chart (ALL labels 24px, code breakdown 22px MONO) */}
        {k4 > 0 && (
          <g opacity={k4} fontFamily={FONT}>
            {/* Legend row at y = 571 (fontSize = 24, clear of top-right Pill) */}
            <rect x={995} y={554} width={20} height={18} rx={3} fill="#64748b" />
            <text x={1022} y={571} fontSize={24} fontWeight={700} fill={C.text}>
              灰色：Little 推算 (W/吞吐−RTT)
            </text>

            <rect x={1395} y={554} width={16} height={18} rx={3} fill={COL_BPOLL} />
            <rect x={1414} y={554} width={16} height={18} rx={3} fill={C.purple} />
            <rect x={1433} y={554} width={16} height={18} rx={3} fill={COL_SEND} />
            <text x={1455} y={571} fontSize={24} fontWeight={700} fill={C.text}>
              三色：实测 poll_tail+loop+0.5×send
            </text>

            {/* Axes for paired bars (0..1650 ns, size=24) */}
            <Axes
              x0={rx0}
              x1={rx1}
              y0={ry0}
              y1={ry1}
              sx={(v) => v}
              sy={rsy}
              xticks={[]}
              yticks={[
                [0, "0"],
                [500, "500"],
                [1000, "1000"],
                [1500, "1500"],
              ]}
              opacity={k4}
              size={24}
            />

            {GAP_PAIRS.map((g, i) => {
              const kg = prog(t, T[4] + i * 0.14, 0.6);
              if (kg <= 0) return null;
              const cx = rx0 + rSlot * (i + 0.5);
              const lx = cx - rBw - 4;
              const rx = cx + 4;

              // Left gray bar: Little's Law
              const littleH = (ry0 - rsy(g.little)) * kg;
              const yLittleTop = ry0 - littleH;

              // Right stacked bar: poll_tail + loop_tail + 0.5*send
              const pH = (ry0 - rsy(g.pollTail)) * kg;
              const lH = Math.max(4, (ry0 - rsy(g.loopTail)) * kg);
              const sH = Math.max(6, (ry0 - rsy(g.halfSend)) * kg);

              const yPTop = ry0 - pH;
              const yLTop = yPTop - lH;
              const ySTop = yLTop - sH;

              return (
                <g key={i}>
                  {/* Gray bar: Little estimate */}
                  <rect x={lx} y={yLittleTop} width={rBw} height={littleH} rx={4} fill="#64748b" />

                  {/* Stacked bar: Measured */}
                  <rect x={rx} y={yPTop} width={rBw} height={pH} rx={3} fill={COL_BPOLL} />
                  <rect x={rx} y={yLTop} width={rBw} height={lH} rx={2} fill={C.purple} />
                  <rect x={rx} y={ySTop} width={rBw} height={sH} rx={3} fill={COL_SEND} />

                  {kg > 0.7 && (
                    <g opacity={(kg - 0.7) / 0.3}>
                      {/* Top value labels (24px) */}
                      <text
                        x={lx + rBw / 2}
                        y={yLittleTop - 8}
                        textAnchor="middle"
                        fontSize={24}
                        fontWeight={800}
                        fill={C.muted}
                      >
                        {g.little}
                      </text>
                      <text
                        x={rx + rBw / 2}
                        y={ySTop - 8}
                        textAnchor="middle"
                        fontSize={24}
                        fontWeight={800}
                        fill={C.green}
                      >
                        {g.meas}
                      </text>
                      {/* poll_tail number inside cyan bar (24px) */}
                      <text
                        x={rx + rBw / 2}
                        y={ry0 - pH / 2}
                        textAnchor="middle"
                        dominantBaseline="central"
                        fontSize={24}
                        fontWeight={800}
                        fill={C.bg}
                      >
                        {g.pollTail}
                      </text>
                    </g>
                  )}

                  {/* Category label (24px) & formula breakdown below axis (22px MONO) */}
                  <text x={cx} y={ry0 + 28} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.text}>
                    {g.name}
                  </text>
                  <text x={cx} y={ry0 + 55} textAnchor="middle" fontFamily={MONO} fontSize={22} fill={C.cyan}>
                    {g.sub}
                  </text>
                </g>
              );
            })}
          </g>
        )}
      </Stage>

      <Note>按 mrv-53-phases.py (a)(b) 实测数据重绘（--account-every 64）</Note>
    </>
  );
}

export const S08: SceneDef = {
  id: "S08",
  title: "方法 2：主机三段计时与 1.0–1.5 µs 记账差闭合",
  stage: 3,
  source: "mrv-53-phases.py · --account-every 64 · FACTS §3",
  dur: DUR,
  cues,
  C: Body,
};
