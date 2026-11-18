import React from "react";
import {
  Arrow,
  C,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  Note,
  Pill,
  prog,
  SceneDef,
  Stage,
  appear,
  fade,
  useT,
} from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 10.14, "总结 MRV-53 的四条核心定界：① **纯回程飞行时间从不决定吞吐**（窗口够大时延后 `D = 0.5–2.0 µs` 吞吐变化 `< 2.5%`）。", "总结 M R V 五十三的四条核心定界：第一，只要并发窗口够大，纯回程飞行时间被流水线完全掩盖，从不决定吞吐。"], // 0
  [10.49, 20.13, "② **三个 `13 M/s` 慢状态（`73.5–76.9 ns`）两侧纯代码都不在关键路径上**（卡斜率 `≈ 0`、主机纯软件仅 `17–18 ns`），瓶颈在跨 PCIe 轮询/写回碰撞。", "第二，三个十三兆慢状态两侧纯代码都不在关键路径上，卡斜率约等于零，真正瓶颈在于跨 P C I e 的轮询与写回节奏碰撞。"], // 1
  [20.48, 30.22, "③ **卡 1 核快状态（`40.3–50.5 M/s`）无延迟时由主机侧决定周期**，单卡核仅余 **`~5 ns` 微小余量**，加 `≥ 30 ns` 卡延迟即切为单卡核 `1:1` 瓶颈。", "第三，卡一核快状态无延迟时由主机决定周期，但单卡核仅余约五纳秒余量，加三十纳秒以上卡延迟即切为单卡核一比一瓶颈。"], // 2
  [30.57, 43.85, "④ **卡 4 核 `W=1024`（`22.6–25.5 M/s`）是快慢双态混合**，慢在跨 4 子环等 32 条回复的 **`26–34 ns/条` `busy_poll`**；卡核加 `60 ns` 反而节流争用跳升至 **`35–41 M/s`**。", "第四，卡四核一千零二十四窗口是快慢双态混合，慢在主机跨四子环等满三十二条回复的微等待；卡核加六十纳秒忙等反而节流争用，跳升到三十五至四十一兆每秒。"], // 3
  [44.20, 58.32, "下一步直指三个子问题：**`MRV-73`** 限制主机单次轮询条数 `--poll-batch MAX`、**`MRV-74`** 无栅栏数据依赖链 `2–30 ns` 细扫、与 **`MRV-50 / 52`** 慢态硬件排队拆解。", "下一步直指三个子问题：M R V 七十三限制主机单次轮询条数；M R V 七十四用无栅栏数据依赖链做二到三十纳秒细扫；以及 M R V 五十拆解慢状态的硬件排队位置。感谢观看！"], // 4
];
const DUR = 59.72; // <dur>
// </cues>

type RowDef = {
  num: string;
  title: string;
  sub: string;
  baseMain: string;
  baseSub: string;
  ev1: string;
  ev2: string;
  verdict: string;
  inf?: boolean;
  color: string;
};

const ROWS: RowDef[] = [
  {
    num: "①",
    title: "纯回程飞行时间",
    sub: "W=1024 及 W=64 慢态",
    baseMain: "D = 0..2.0 µs",
    baseSub: "W=64 慢态 D≤1µs",
    ev1: "卡侧非忙等延后 D = 0 → 0.5 → 1.0 → 2.0 µs",
    ev2: "W=1024 全 6 配置及 W=64 慢态吞吐变化 < 2.5%",
    verdict: "窗口 W 盖住往返时，纯回程时间被流水线完全掩盖，从不决定吞吐",
    color: C.purple,
  },
  {
    num: "②",
    title: "三个 ~13 M/s 慢态",
    sub: "W=64 WC4/ST4/DPI4",
    baseMain: "13.0–13.6 M/s",
    baseSub: "73.5–76.9 ns/条",
    ev1: "卡侧 δ = 30..60 ns 斜率 -0.05 ~ +0.02 ≈ 0",
    ev2: "主机 K≈12 纯软件 17–18 ns，busy_poll 占 52.6–64.9 ns",
    verdict: "两侧纯代码都不在关键路径，瓶颈是跨 PCIe 轮询/写回碰撞",
    inf: true,
    color: C.red,
  },
  {
    num: "③",
     title: "卡 1 核快状态",
     sub: "W=1024 WC1 / DPI1",
     baseMain: "40.3–50.5 M/s",
     baseSub: "DPI1 44.5 M/s",
     ev1: "无旋钮 H2 跑出 50.5 M/s (19.8 ns) 证单核上限 ≤ 19.8 ns",
     ev2: "双 isb ≥ 29 ns 沿 58.6→72.3→86.8 ns 封顶 (斜率 0.86–1.06)",
     verdict: "无延迟由主机决定周期，但单卡核仅余 ~5 ns 余量，越拐点切卡核瓶颈",
     color: C.green,
   },
   {
     num: "④",
     title: "卡 4 核 W=1024",
     sub: "WC4/ST4/DPI4 双态",
     baseMain: "22.6–25.5 M/s",
     baseSub: "39.3–44.3 ns/条",
     ev1: "RTT 17–64 µs；poll_tail 805–1060 ns 闭合 1.0–1.5 µs 差 (<3%)",
     ev2: "4 环 bpoll 25.9–33.9 ns；卡加 60 ns 跳升至 35.1–40.6 M/s",
     verdict: "非主机算力封顶，而是 4 核无节流写回引发的跨子环等待与争用",
    inf: true,
    color: C.yellow,
  },
];

const BACKLOG = [
  {
    id: "MRV-73",
    tag: "定界 ④ · 尾等待",
    title: "主机限批轮询 --poll-batch MAX",
    method: "扫描单次轮询上限 MAX = 1..32，配对 --account-every 64 三段计时。",
    question: "检验：能否直接消除 ~1 µs 轮询尾等待并让卡 4 核跳出慢态？",
    color: C.cyan,
  },
  {
    id: "MRV-74",
    tag: "定界 ③/④ · 细扫",
    title: "无栅栏数据依赖链 2–30 ns 细扫",
    method: "用 tsc 算术依赖链替代 isb / lfence，细扫 δ = 0, 2, 5..30 ns。",
    question: "检验：直测单卡核 ~5 ns 余量拐点与 4 核 bpoll 33→14 ns 回落。",
    color: C.green,
  },
  {
    id: "MRV-50 / 52",
    tag: "定界 ② · 硬件排队",
    title: "慢态硬件排队与节流机理",
    method: "拆解 13 M/s 慢态去程、卡上、回程驻留时间与卡核停顿机制。",
    question: "检验：跳出慢态源于少读请求环还是回复自然攒批（联动 56/60）。",
    color: C.orange,
  },
];

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  const activeIdx = t >= T[4] ? 4 : t >= T[3] ? 3 : t >= T[2] ? 2 : t >= T[1] ? 1 : 0;
  const opSpotlight = fade(t, T[0] + 0.2, T[4] - 0.1, 0.35);

  return (
    <>
      {/* ---------------- HTML LAYER ---------------- */}

      {/* Upper Section Header Strip (y: 126..166) */}
      <div
        style={{
          position: "absolute",
          left: 60,
          top: 126,
          width: 1800,
          height: 40,
          borderRadius: 10,
          background: "rgba(20, 27, 46, 0.95)",
          border: `1.5px solid ${C.line}`,
          display: "grid",
          gridTemplateColumns: "305px 235px 710px 514px",
          alignItems: "center",
          padding: "0 18px",
          boxSizing: "border-box",
          fontFamily: FONT,
          fontSize: 24,
          fontWeight: 700,
          color: C.cyan,
          whiteSpace: "nowrap",
          ...appear(t, T[0]),
        }}
      >
        <div>被测对象 / 状态</div>
        <div style={{ paddingLeft: 14 }}>实测基线</div>
        <div style={{ paddingLeft: 14 }}>受控实验关键证据（检查 1 / 2 / 3）</div>
        <div style={{ paddingLeft: 14 }}>真正决定因素（定界结论）</div>
      </div>

      {/* 4 Summary Rows (y: 172..590) */}
      {ROWS.map((r, i) => {
        const rowY = 172 + i * 106;
        const revealed = t >= T[i];
        const isCurrent = activeIdx === i;
        const op = revealed ? prog(t, T[i], 0.5) : 0.18;
        return (
          <div
            key={r.num}
            style={{
              position: "absolute",
              left: 60,
              top: rowY,
              width: 1800,
              height: 98,
              borderRadius: 12,
              background: isCurrent ? "rgba(26, 36, 62, 0.96)" : "rgba(20, 27, 46, 0.88)",
              border: `${isCurrent ? 2.5 : 1.5}px solid ${revealed ? r.color : C.line}`,
              boxShadow: isCurrent ? `0 0 24px ${r.color}33` : undefined,
              display: "grid",
              gridTemplateColumns: "305px 235px 710px 514px",
              alignItems: "center",
              padding: "0 18px",
              boxSizing: "border-box",
              fontFamily: FONT,
              opacity: op,
              overflow: "hidden",
            }}
          >
            {/* Col 1: Target / Regime */}
            <div style={{ display: "flex", alignItems: "center", gap: 12, paddingRight: 8 }}>
              <div
                style={{
                  width: 44,
                  height: 44,
                  borderRadius: 10,
                  background: `${r.color}25`,
                  border: `2px solid ${r.color}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 26,
                  fontWeight: 800,
                  color: r.color,
                  flexShrink: 0,
                }}
              >
                {r.num}
              </div>
              <div style={{ whiteSpace: "nowrap" }}>
                <div style={{ fontSize: 24, fontWeight: 700, color: C.text }}>{r.title}</div>
                <div style={{ fontSize: 22, fontFamily: MONO, color: C.muted, marginTop: 2 }}>{r.sub}</div>
              </div>
            </div>

            {/* Col 2: Baseline */}
            <div style={{ paddingRight: 10, borderLeft: `1.5px solid ${C.line}`, paddingLeft: 14, whiteSpace: "nowrap" }}>
              <div style={{ fontSize: 24, fontFamily: MONO, fontWeight: 700, color: r.color }}>{r.baseMain}</div>
              <div style={{ fontSize: 22, fontFamily: MONO, color: C.muted, marginTop: 2 }}>{r.baseSub}</div>
            </div>

            {/* Col 3: Evidence */}
             <div style={{ paddingRight: 12, borderLeft: `1.5px solid ${C.line}`, paddingLeft: 14, whiteSpace: "nowrap" }}>
               <div style={{ fontSize: 24, fontFamily: FONT, color: C.text, fontWeight: 600, lineHeight: 1.3 }}>{r.ev1}</div>
               <div style={{ fontSize: 24, fontFamily: FONT, color: C.muted, marginTop: 3, lineHeight: 1.3 }}>{r.ev2}</div>
             </div>

            {/* Col 4: True Determining Factor */}
            <div
              style={{
                borderLeft: `1.5px solid ${C.line}`,
                paddingLeft: 14,
                display: "flex",
                alignItems: "center",
              }}
            >
              <div style={{ fontSize: 24, fontWeight: 700, color: C.text, lineHeight: 1.3 }}>
                {r.verdict}
                {r.inf && (
                  <span
                    style={{
                      marginLeft: 8,
                      padding: "1px 7px",
                      borderRadius: 5,
                      fontSize: 20,
                      fontFamily: MONO,
                      fontWeight: 700,
                      color: K.inf,
                      border: `1.5px solid ${K.inf}`,
                      background: "rgba(94, 234, 212, 0.14)",
                    }}
                  >
                    [INF]
                  </span>
                )}
              </div>
            </div>
          </div>
        );
      })}

      {/* Lower Section during Cues 0..3: Live Evidence Spotlight Panel (fades out at T[4]) */}
      {opSpotlight > 0.01 && activeIdx < 4 && (
        <Card
          x={60}
          y={614}
          w={1800}
          h={238}
          border={ROWS[activeIdx].color}
          style={{
            opacity: opSpotlight,
            padding: "14px 24px",
            background: "rgba(20, 27, 46, 0.92)",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            overflow: "hidden",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", whiteSpace: "nowrap" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <Pill x={0} y={0} color={ROWS[activeIdx].color} size={20} solid style={{ position: "static" }}>
                定界 {ROWS[activeIdx].num} 闭环
              </Pill>
              <span style={{ fontSize: 25, fontWeight: 700, color: C.text }}>
                {ROWS[activeIdx].title} — {ROWS[activeIdx].verdict}
              </span>
            </div>
            <span style={{ fontSize: 22, fontFamily: MONO, color: C.muted }}>
              608 次校准运行 · lost=0
            </span>
          </div>

          {activeIdx === 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "360px 1fr 1fr", gap: 16 }}>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.purple}` }}>
                <div style={{ fontSize: 44, fontFamily: MONO, fontWeight: 800, color: C.green, lineHeight: 1.05 }}>&lt; 2.5%</div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 6 }}>D = 0..2 µs 吞吐变化</div>
              </div>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.green}` }}>
                <div style={{ fontSize: 24, color: C.muted }}>W = 1024 全 6 配置 &amp; W = 64 慢态</div>
                <div style={{ fontSize: 24, fontFamily: MONO, fontWeight: 700, color: C.green, marginTop: 6 }}>
                  WC1 30.57→30.40 · DPI4 23.92→23.22 M/s
                </div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 4 }}>WC4 W=64: 13.12→13.32 M/s (D≤1µs)</div>
              </div>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.cyan}` }}>
                <div style={{ fontSize: 24, color: C.muted }}>W = 1 串行对照（无窗口掩盖）</div>
                <div style={{ fontSize: 24, fontFamily: MONO, fontWeight: 700, color: C.yellow, marginTop: 6 }}>
                  WC1: 947.9 → 1449.3 → 1955.0 → 2958.6 ns
                </div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 4 }}>延后 D 一比一计入周期，验证注入准确</div>
              </div>
            </div>
          )}

          {activeIdx === 1 && (
            <div style={{ display: "grid", gridTemplateColumns: "360px 1fr 1fr", gap: 16 }}>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.red}` }}>
                <div style={{ fontSize: 44, fontFamily: MONO, fontWeight: 800, color: C.green, lineHeight: 1.05 }}>斜率 ≈ 0</div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 6 }}>卡侧 -0.05 ~ +0.02</div>
              </div>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.green}` }}>
                <div style={{ fontSize: 24, color: C.muted }}>卡侧忙等 δ_card = 30 / 45 / 60 ns</div>
                <div style={{ fontSize: 24, fontFamily: MONO, fontWeight: 700, color: C.green, marginTop: 6 }}>
                  WC4 76.1→75.6 · ST4 73.5→70.4 ns
                </div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 4 }}>DPI4 76.9→77.2 ns，卡核空转余量充足</div>
              </div>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.cyan}` }}>
                <div style={{ fontSize: 24, color: C.muted }}>主机假硬件对齐 vs 实测三段计时</div>
                <div style={{ fontSize: 24, fontFamily: MONO, fontWeight: 700, color: C.cyan, marginTop: 6 }}>
                  纯软件 K≈12 仅 17–18 ns vs 实测 73.5–76.9 ns
                </div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 4 }}>busy_poll 占 52.6–64.9 ns/条 [INF]</div>
              </div>
            </div>
          )}

          {activeIdx === 2 && (
            <div style={{ display: "grid", gridTemplateColumns: "360px 1fr 1fr", gap: 16 }}>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.cyan}` }}>
                <div style={{ fontSize: 44, fontFamily: MONO, fontWeight: 800, color: C.cyan, lineHeight: 1.05 }}>≤ 19.8 ns</div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 6 }}>单卡核上限 (余量 ~5 ns)</div>
              </div>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.yellow}` }}>
                <div style={{ fontSize: 24, color: C.muted }}>H0 vs H2 同卡对照 &amp; 主机忙等</div>
                <div style={{ fontSize: 24, fontFamily: MONO, fontWeight: 700, color: C.yellow, marginTop: 6 }}>
                  H0 40.3M (24.8 ns) → H2 50.5M (19.8 ns)
                </div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 4 }}>主机 δ=0..40 ns: 24.8→38.7→44.5→76.9 ns</div>
              </div>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.green}` }}>
                <div style={{ fontSize: 24, color: C.muted }}>卡侧忙等 δ_card = 0 → 29 → 45 → 59 ns</div>
                <div style={{ fontSize: 24, fontFamily: MONO, fontWeight: 700, color: C.green, marginTop: 6 }}>
                  WC1: 24.8 → 58.6 → 72.3 → 86.8 ns/条
                </div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 4 }}>越过 ~5 ns 余量后呈 1:1 封顶 (斜率 0.86–1.06)</div>
              </div>
            </div>
          )}

          {activeIdx === 3 && (
            <div style={{ display: "grid", gridTemplateColumns: "360px 1fr 1fr", gap: 16 }}>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.yellow}` }}>
                <div style={{ fontSize: 44, fontFamily: MONO, fontWeight: 800, color: C.yellow, lineHeight: 1.05 }}>误差 &lt; 3%</div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 6 }}>闭合 1.0–1.5 µs 记账差</div>
              </div>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.red}` }}>
                <div style={{ fontSize: 24, color: C.muted }}>跨 4 子环轮询尾 poll_tail 实测分解</div>
                <div style={{ fontSize: 24, fontFamily: MONO, fontWeight: 700, color: C.red, marginTop: 6 }}>
                  poll_tail = 805 / 1016 / 1060 ns
                </div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 4 }}>WC4 1204 vs 1180 ns · DPI4 1198 vs 1225 ns</div>
              </div>
              <div style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(11,16,32,0.8)", border: `1.5px solid ${C.green}` }}>
                <div style={{ fontSize: 24, color: C.muted }}>卡核加 δ_card = 60 ns 吞吐反跳升 [INF]</div>
                <div style={{ fontSize: 24, fontFamily: MONO, fontWeight: 700, color: C.green, marginTop: 6 }}>
                  WC4 22.6→36.9 · ST4 25.5→35.1 · DPI4 40.6M
                </div>
                <div style={{ fontSize: 24, color: C.text, marginTop: 4 }}>DPI4 总 DMA 从 5127 万降至 3036 万</div>
              </div>
            </div>
          )}
        </Card>
      )}

      {/* Lower Section at Cue 4 (y: 616..852): Three Next-Step Backlog Cards */}
      {t >= T[4] &&
        BACKLOG.map((b, i) => {
          const cardX = 60 + i * 608;
          return (
            <Card
              key={b.id}
              x={cardX}
              y={614}
              w={584}
              h={238}
              border={b.color}
              style={{
                ...appear(t, T[4] + i * 0.25),
                padding: "12px 18px",
                background: "rgba(20, 27, 46, 0.95)",
                boxShadow: `0 0 22px ${b.color}26`,
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                overflow: "hidden",
              }}
            >
              <div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6, whiteSpace: "nowrap" }}>
                  <Pill x={0} y={0} color={b.color} size={20} solid style={{ position: "static" }}>
                    {b.id}
                  </Pill>
                  <span style={{ fontSize: 22, fontFamily: MONO, color: b.color, fontWeight: 700 }}>{b.tag}</span>
                </div>
                <div style={{ fontSize: 25, fontWeight: 800, color: C.text, marginBottom: 6, whiteSpace: "nowrap" }}>{b.title}</div>
                <div style={{ fontSize: 24, color: C.muted, lineHeight: 1.32 }}>{b.method}</div>
              </div>
              <div
                style={{
                  padding: "6px 12px",
                  borderRadius: 8,
                  background: `${b.color}18`,
                  border: `1.5px solid ${b.color}88`,
                  fontSize: 24,
                  fontWeight: 700,
                  color: C.text,
                  lineHeight: 1.28,
                }}
              >
                {b.question}
              </div>
            </Card>
          );
        })}

      <Note>MRV-53 总结 · 全部 608 次校准运行 lost=bad=torn=0 · 后续实验 MRV-73 / MRV-74 / MRV-50 / MRV-52</Note>

      {/* ---------------- SVG STAGE LAYER ---------------- */}
      <Stage>
        {/* Connecting arrows from the 4 summary table rows to the 3 backlog cards at Cue 4 */}
        {t >= T[4] && (
          <g opacity={prog(t, T[4], 0.6)}>
            <Arrow x1={352} y1={590} x2={352} y2={612} color={C.cyan} w={3.5} head={11} k={prog(t, T[4], 0.5)} />
            <Arrow x1={960} y1={590} x2={960} y2={612} color={C.green} w={3.5} head={11} k={prog(t, T[4] + 0.2, 0.5)} />
            <Arrow x1={1568} y1={590} x2={1568} y2={612} color={C.orange} w={3.5} head={11} k={prog(t, T[4] + 0.4, 0.5)} />
          </g>
        )}
      </Stage>
    </>
  );
}

export const S10: SceneDef = {
  id: "S10",
  title: "总结与下一步：从定界到破局",
  stage: 4,
  source: "MRV-53 结论与子项 MRV-73 / MRV-74 / MRV-50 · §5",
  dur: DUR,
  cues,
  C: Body,
};
