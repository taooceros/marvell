import React from "react";
import { Cue, SceneDef, prog, useT } from "../lib";
import { CodeCard, Glass, HBars, P, Stage, Tag, Text, alpha, rise, win } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.45, 12.22, "对比源码就一目了然：把 `u4_ldpq` 的 4 槽齐射换成零前瞻 **`static-u1q (u1_ldpq)`**，只用 `2× ldp q` 严格读当前格 **`s0`**。", "对比源码就一目了然。把 u 四 l d p q 的四槽齐射换成零前瞻的 static u 一 q，只用两条 l d p q 严格读取当前槽位 s 零，绝不偷看后面的未写空槽。"], // 0
  [12.42, 24.43, "绝不偷看 `s1 / s2 / s3` 后，同样 `K = 4, W = 256` 闭环，`EXLMN` 骤降 **`3.2×`**（`1.06 → 0.33 / 包`），`W = 512` 降到 `0.23 / 包`。", "不偷看未来空槽之后，同样四核、窗口二五六的闭环，E X L M N 立刻骤降三点二倍，从一点零六降到零点三三次每包，五一二窗口更降到零点二三次。"], // 1
  [24.63, 37.45, "同一零门铃二进制一箭三雕：`W = 1` **`904.5 ns`**，单向 **`159.9 M/s (CHA 16.9)`**，闭环 **`61.4–75.8 M/s`**（`H = 2` 达 **`119.7 M/s`**）！", "同一个免门铃、免 S fence 二进制直接一箭三雕：空载九百零四点五纳秒，单向一百五十九点九百万包每秒，闭环冲上六十一点四到七十五点八，双主机通道更达一百一十九点七百万包每秒。"], // 2
  [37.65, 47.04, "实测同机邻居保护：`NC K = 4` 单向满速时，同 Socket 六核 **`STREAM`** 稳居 **`99.5%–100.0%`**，实现**零劣化**。", "再看同机邻居实测：N C 四核单向满速跑时，同插槽六核 STREAM 内存带宽稳居百分之九十九点五到百分之百，对主机邻居几乎零干扰。"], // 3
  [47.24, 58.77, "但边界必须诚实守住：一旦把卡核超配到 **`K = 8`**，主机 `CHA` 飙至 **`374–383`**，`6c STREAM` 实跌 **`-11.4% ~ -12.6%`**。", "但工程边界必须守住：一旦把卡核超配到八核，主机 C H A 占用立刻飙升到三百七十四到三百八十三，同机六核 STREAM 实测下跌百分之十一点四到十二点六。"], // 4
  [58.97, 71.66, "且宽窗 `W = 6144` 部分负载下 `CHA` 仍会爬到 **`250–307`**——因此务必搭配**合理配核（`K = 2–4`）+ 信用窗 `W ≤ 256`**。", "同时宽窗部分负载下 C H A 仍会爬到二百五十到三百零七。所以最佳实践是三件套并举：零前瞻 u 一 l d p q、合理配核、再加主机信用窗不超过二百五十六。"], // 5
];
const DUR = 72.66; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();

  // Phase 1 (cues 0..2): u4 vs u1_ldpq code diff + throughput triumph
  const p1 = win(t, T[0], T[3], 0.5);
  const kDrop = prog(t, T[1], 0.55);
  const kWin = prog(t, T[2], 0.55);

  // Phase 2 (cues 3..5): Co-located 6c STREAM + CHA neighbor boundary
  const p2 = prog(t, T[3], 0.55);
  const kK8 = prog(t, T[4], 0.55);
  const kGuard = prog(t, T[5], 0.55);

  return (
    <>
      {/* ==================== PHASE 1: Code Diff + One Binary Triumph ==================== */}
      <div style={{ opacity: p1 }}>
        {/* Left CodeCard: u4_ldpq (Bad) */}
        <CodeCard
          x={76}
          y={144}
          w={858}
          h={332}
          title="✕ u4_ldpq · 4 槽齐射前瞻（踩坑）"
          badge="EXLMN 1.06/包 · 闭环 17.6 M/s"
          accent={P.bad}
          fontSize={16.5}
          lines={[
            { text: "let s0 = next; let s1 = next + stride;", hl: "bad", note: "提前算未来槽" },
            { text: "let s2 = next + stride*2; let s3 = next + stride*3;", hl: "bad" },
            { text: "let p0 = slot(s0); let p1 = slot(s1);" },
            { text: "let p2 = slot(s2); let p3 = slot(s3);" },
            { text: "let qs = load_4x64b_ldpq(p0, p1, p2, p3);", hl: "bad", note: "8× ldp q 撞前沿!" },
            { text: "// 命中 1 包顺手射出 3 发未来空读 → EXLMN 雪崩" },
          ]}
        />

        {/* Right CodeCard: u1_ldpq (Good) */}
        <CodeCard
          x={986}
          y={144}
          w={858}
          h={332}
          title="✓ static-u1q (u1_ldpq) · 零前瞻直读（正解）"
          badge="EXLMN 降 3.2× → 0.33/包"
          accent={P.nc}
          fontSize={16.5}
          lines={[
            { text: "let off = (((next - 1) as usize) & ring_mask) * 128;", hl: "nc", note: "只看当前 s0" },
            { text: "let words = unsafe {", hl: "nc" },
            { text: "  load_64b_ldpq(lane_base.add(off)) // 2× ldp q", hl: "nc", note: "绝不偷看 s1..s3" },
            { text: "};" },
            { text: "if words[0] == next && consume_one(words, next) {" },
            { text: "  next += stride; // 零前瞻 · 零门铃 · 零 SFENCE" },
          ]}
        />

        {/* Bottom-left Glass container for HBars */}
        <Glass x={76} y={498} w={858} h={382} kicker="闭环实测吞吐对比（MRV-245 / MRV-252 · 全零错）" color={P.gold} opacity={kDrop} />

        {/* Bottom-right: One Binary Wins All Three Regimes */}
        <Glass x={986} y={498} w={858} h={382} kicker="同一零门铃、零 SFENCE 二进制一箭三雕（MT_NORMAL_NC + u1_ldpq）" color={P.nc} opacity={kWin}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22, marginTop: 14, fontVariantNumeric: "tabular-nums" }}>
            <div style={{ background: alpha(P.nc, 0.1), border: `1px solid ${alpha(P.nc, 0.35)}`, borderRadius: 12, padding: "14px 18px" }}>
              <div style={{ fontSize: 15, color: P.sub }}>空载往返延迟 (W = 1, K = 4)</div>
              <div style={{ fontSize: 42, fontWeight: 700, color: P.nc, marginTop: 4 }}>904.5 ns</div>
              <div style={{ fontSize: 15, color: P.sub }}>p99 = 960.9 ns · 零延迟税</div>
            </div>
            <div style={{ background: alpha(P.gold, 0.1), border: `1px solid ${alpha(P.gold, 0.35)}`, borderRadius: 12, padding: "14px 18px" }}>
              <div style={{ fontSize: 15, color: P.sub }}>单向宽窗满速 (H = 1, K = 4)</div>
              <div style={{ fontSize: 42, fontWeight: 700, color: P.gold, marginTop: 4 }}>159.9 M/s</div>
              <div style={{ fontSize: 15, color: P.sub }}>CHA 仅 16.9 · 6c STREAM 99.5%–100%</div>
            </div>
            <div style={{ background: alpha(P.host, 0.1), border: `1px solid ${alpha(P.host, 0.35)}`, borderRadius: 12, padding: "14px 18px" }}>
              <div style={{ fontSize: 15, color: P.sub }}>单主机通道闭环 (H = 1)</div>
              <div style={{ fontSize: 42, fontWeight: 700, color: P.host, marginTop: 4 }}>61.4–75.8 M/s</div>
              <div style={{ fontSize: 15, color: P.sub }}>K=4 W=512 61.4 · K=2 W=256 75.8 (CHA 7.7)</div>
            </div>
            <div style={{ background: alpha(P.nc, 0.14), border: `1px solid ${alpha(P.nc, 0.45)}`, borderRadius: 12, padding: "14px 18px" }}>
              <div style={{ fontSize: 15, color: P.sub }}>双主机通道闭环 (H = 2, K = 4)</div>
              <div style={{ fontSize: 42, fontWeight: 700, color: P.nc, marginTop: 4 }}>119.7 M/s</div>
              <div style={{ fontSize: 15, color: P.sub }}>EXLMN 仅 0.04 / 包 · 原地起飞 5–9×</div>
            </div>
          </div>
        </Glass>
      </div>

      {/* Phase 1 SVG HBars */}
      <Stage opacity={p1 * kDrop}>
        <HBars
          x={96}
          y={554}
          w={818}
          max={125}
          labelW={295}
          barH={28}
          gap={22}
          size={18}
          rows={[
            { label: "Normal-WB (K=4, W=256)", value: 13.53, text: "13.5 M/s", color: P.wb, k: kDrop },
            { label: "NC u4_ldpq (K=4, W=256)", value: 17.58, text: "17.6 M/s", color: P.bad, k: kDrop },
            { label: "NC adapt-50 (K=4, W=512)", value: 19.74, text: "19.7 M/s", color: P.bad, k: kDrop },
            { label: "NC u1_ldpq (K=4, W=512)", value: 61.36, text: "61.4 M/s", color: P.nc, k: kDrop },
            { label: "NC u1_ldpq (K=2, W=256)", value: 75.75, text: "75.8 M/s", color: P.gold, k: kWin },
            { label: "NC u1 (H=2, K=4, W=512)", value: 119.65, text: "119.7 M/s", color: P.nc, k: kWin },
          ]}
        />
      </Stage>

      {/* ==================== PHASE 2: Neighbor Boundary (STREAM + CHA) ==================== */}
      <div style={{ opacity: p2 }}>
        {/* Left Panel: Co-located 6c STREAM */}
        <Glass x={76} y={152} w={858} h={504} kicker="同 Socket 邻居实测：6 核 STREAM 内存带宽（MRV-252）" color={P.nc}>
          <Text x={24} y={404} w={812} size={19} color={P.ink}>
            • <strong style={{ color: P.nc }}>NC K = 4 单向</strong>：47.46–47.70 GB/s（基线 47.69 的{" "}
            <strong style={{ color: P.nc }}>99.5%–100.0%</strong>），零伤邻！
          </Text>
          <Text x={24} y={448} w={812} size={19} color={P.bad} opacity={kK8}>
            • <strong>超配 K = 8 单向</strong>：降至 41.68–42.27 GB/s（实跌 <strong>-11.4% ~ -12.6%</strong>），真实伤邻！
          </Text>
        </Glass>

        {/* Right Panel: Host CHA Occupancy */}
        <Glass x={986} y={152} w={858} h={504} kicker="主机侧 CHA 队列占用（决定是否拖垮同机邻居）" color={P.gold}>
          <Text x={24} y={404} w={810} size={19} color={P.ink}>
            • 闭环限窗 <strong style={{ color: P.nc }}>W ≤ 256</strong> 或合理配核单向满速：CHA 仅{" "}
            <strong style={{ color: P.nc }}>4.8–16.9</strong>，邻居安全。
          </Text>
          <Text x={24} y={450} w={810} size={19} color={P.tad} opacity={kGuard}>
            • 宽窗 <strong style={{ color: P.gold }}>W = 6144 部分负载</strong> (40–80 M/s)：CHA 仍爬至{" "}
            <strong style={{ color: P.tad }}>250–307</strong> → 必须配合信用窗 W ≤ 256！
          </Text>
        </Glass>

        {/* Bottom Golden Guardrail Banner */}
        <Glass
          x={76}
          y={680}
          w={1768}
          h={196}
          kicker="工程落地的三条硬护栏（兼顾吞吐、延迟与同机邻居隔离）"
          color={P.nc}
          borderColor={alpha(P.nc, 0.5)}
          opacity={kGuard}
        >
          <div style={{ display: "flex", gap: 28, marginTop: 14 }}>
            <div style={{ flex: 1, background: alpha(P.nc, 0.1), borderRadius: 12, padding: "14px 20px", border: `1px solid ${alpha(P.nc, 0.35)}` }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: P.nc }}>① 零前瞻 static-u1q (u1_ldpq)</div>
              <div style={{ fontSize: 17, color: P.sub, marginTop: 6 }}>严格只读当前格 s0，绝不偷看 s1/s2/s3，切断 EXLMN 雪崩</div>
            </div>
            <div style={{ flex: 1, background: alpha(P.gold, 0.1), borderRadius: 12, padding: "14px 20px", border: `1px solid ${alpha(P.gold, 0.35)}` }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: P.gold }}>② 合理配核（每 Lane K = 2–4）</div>
              <div style={{ fontSize: 17, color: P.sub, marginTop: 6 }}>杜绝 K = 8 超配把 CHA 顶到 374–383（-12% STREAM）</div>
            </div>
            <div style={{ flex: 1, background: alpha(P.host, 0.1), borderRadius: 12, padding: "14px 20px", border: `1px solid ${alpha(P.host, 0.35)}` }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: P.host }}>③ 主机信用窗 W ≤ 256</div>
              <div style={{ fontSize: 17, color: P.sub, marginTop: 6 }}>封住部分负载下宽窗 CHA 飙至 250–307 的反压风险</div>
            </div>
          </div>
        </Glass>
      </div>

      {/* Phase 2 SVG HBars */}
      <Stage opacity={p2}>
        {/* Left: STREAM GB/s */}
        <HBars
          x={96}
          y={224}
          w={818}
          max={52}
          labelW={280}
          barH={36}
          gap={34}
          size={19}
          rows={[
            { label: "空闲基线 (6c STREAM)", value: 47.69, text: "47.69 GB/s", color: P.host, k: p2 },
            { label: "NC K=4 单向 (adapt-exp)", value: 47.70, text: "100.0%", color: P.nc, k: p2 },
            { label: "NC K=4 单向 (u4_ldpq)", value: 47.46, text: "99.5%", color: P.nc, k: p2 },
            { label: "NC K=8 超配 (u4_ldpq)", value: 41.68, text: "-12.6%", color: P.bad, k: kK8 },
          ]}
        />

        {/* Right: Host CHA occupancy */}
        <HBars
          x={1006}
          y={224}
          w={818}
          max={560}
          labelW={295}
          barH={32}
          gap={26}
          size={18}
          rows={[
            { label: "NC u1q 闭环 W=256", value: 7.7, text: "CHA 4.8–7.7", color: P.nc, k: p2 },
            { label: "NC u1q 单向 K=4", value: 16.9, text: "CHA 16.9", color: P.nc, k: p2 },
            { label: "宽窗部分负载 40–80M", value: 306.6, text: "CHA 250–307", color: P.tad, k: kGuard },
            { label: "NC 超配 K=8 单向", value: 382.6, text: "CHA 374–383", color: P.bad, k: kK8 },
            { label: "对比：WB K=4 单向", value: 546.2, text: "CHA 546.2", color: P.wb, k: kK8 },
          ]}
        />
      </Stage>
    </>
  );
}

export const B05: SceneDef = {
  id: "B05",
  title: "真正解法与邻居边界：零前瞻 u1_ldpq + 信用窗 W ≤ 256",
  stage: 4,
  source: "MRV-245/252 runs.jsonl · mrv252_card.rs:1095-1102",
  dur: DUR,
  cues,
  C: Body,
};
