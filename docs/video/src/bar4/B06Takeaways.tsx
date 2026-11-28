import React from "react";
import { Cue, SceneDef, prog, useT } from "../lib";
import { Glass, P, Stage, alpha, rise } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.45, 13.28, "回顾全貌，两面「十几 M 墙」泾渭分明：墙一是 **`Normal-WB`** 进 `L1D/L2` 引发 `0x58` 死锁，墙二是闭环 **`u4`** 前瞻撞 `TAD` 触发 `EXLMN` 雪崩。", "回顾全貌，两面十几百万包每秒的墙泾渭分明：第一面是 Normal W B 进 L 一引发的零 x 五八嗅探死锁，第二面是闭环 u 四前瞻撞击 T A D 触发的 E X L M N 雪崩。"], // 0
  [13.48, 27.25, "**路线 A（首选）**：`MT_NORMAL_NC (0x44)` + 零前瞻 **`u1_ldpq`** + `W ≤ 256`——免门铃、免 `SFENCE`，保住 **`904.5 ns`** 最低延迟。", "工程选型浓缩成两句口诀。首选路线 A：M T Normal N C 加零前瞻 u 一 l d p q，配合合理配核与信用窗不超二百五十六，免门铃免 S fence，拿满九百零四点五纳秒最低延迟。"], // 1
  [27.45, 39.73, "**路线 B（稳健）**：**解耦合并门铃 + 批尾 `SFENCE`**——1 颗卡核即可吃满，严格保多行顺序，天然零数据前沿冲撞，代价是 `W = 1` 多 `+190 ns`。", "备选路线 B：解耦合并门铃加批尾一次 S fence。一颗卡核就能吃满吞吐，严格保证多缓存行顺序且不怕核超配，代价只是空载固定多一百九十纳秒延迟。"], // 2
  [39.93, 51.73, "记住核心心法：**绕过 `L1/L2` 卸旧锁，零前瞻避前沿撞，配核限窗护邻居**——`BAR4` 直推通路，至此彻底打通。", "最后记住这句心法：用 N C 绕过私有缓存卸掉旧锁，用零前瞻避开写入前沿对撞，用配核与信用窗护住主机邻居。bar 四直推通路，至此彻底打通。"], // 3
];
const DUR = 52.73; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();

  const kWalls = prog(t, T[0], 0.55);
  const kRouteA = prog(t, T[1], 0.55);
  const kRouteB = prog(t, T[2], 0.55);
  const kMantra = prog(t, T[3], 0.55);

  return (
    <>
      <Stage>{null}</Stage>

      {/* Top Row: Two-Wall Crisp Recap */}
      <div style={{ display: "flex", gap: 28, position: "absolute", left: 76, top: 146, width: 1768, ...rise(kWalls) }}>
        <Glass
          x={0}
          y={0}
          w={870}
          h={140}
          kicker="第一道墙 · Normal-WB (0xFF) 缓存嗅探死锁"
          color={P.wb}
          borderColor={alpha(P.wb, 0.45)}
        >
          <div style={{ fontSize: 20, color: P.ink, lineHeight: 1.5 }}>
            读过即进 <strong style={{ color: P.wb }}>L1D/L2</strong> → 主机写必发 <strong style={{ color: P.bad }}>0x58 作废 (1.96/包)</strong> → 闭环焊死{" "}
            <strong style={{ color: P.bad }}>13.5 M/s</strong>（单向 CHA 爆至 546）
          </div>
        </Glass>

        <Glass
          x={898}
          y={0}
          w={870}
          h={140}
          kicker="第二道墙 · 闭环 MT_NORMAL_NC + u4 前瞻雪崩"
          color={P.bad}
          borderColor={alpha(P.bad, 0.45)}
        >
          <div style={{ fontSize: 20, color: P.ink, lineHeight: 1.5 }}>
            L1/L2 已归零，但 <strong style={{ color: P.bad }}>u4 偷看未来空槽</strong>撞 TAD 前沿 →{" "}
            <strong style={{ color: P.gold }}>EXLMN 飙升 119× (1.06/包)</strong> → 雪崩锁死{" "}
            <strong style={{ color: P.bad }}>17.6–19.7 M/s</strong>
          </div>
        </Glass>
      </div>

      {/* Middle Row: Two Engineering Routes Side by Side */}
      <Glass
        x={76}
        y={312}
        w={870}
        h={422}
        kicker="路线 A（推荐首选） · 免门铃 · 免 SFENCE · 极致低延迟"
        color={P.nc}
        borderColor={alpha(P.nc, 0.55)}
        opacity={kRouteA}
        style={rise(kRouteA)}
      >
        <div style={{ fontSize: 28, fontWeight: 700, color: P.nc, marginTop: 4 }}>
          MT_NORMAL_NC (0x44) + 零前瞻 u1_ldpq + W ≤ 256
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 14,
            marginTop: 18,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          <div style={{ background: alpha(P.nc, 0.12), borderRadius: 10, padding: "10px 14px" }}>
            <div style={{ fontSize: 14, color: P.sub }}>空载 W = 1</div>
            <div style={{ fontSize: 30, fontWeight: 700, color: P.nc }}>904.5 ns</div>
          </div>
          <div style={{ background: alpha(P.gold, 0.12), borderRadius: 10, padding: "10px 14px" }}>
            <div style={{ fontSize: 14, color: P.sub }}>单向满速 (K=4)</div>
            <div style={{ fontSize: 30, fontWeight: 700, color: P.gold }}>159.9 M/s</div>
          </div>
          <div style={{ background: alpha(P.host, 0.12), borderRadius: 10, padding: "10px 14px" }}>
            <div style={{ fontSize: 14, color: P.sub }}>闭环 (H=1 / H=2)</div>
            <div style={{ fontSize: 28, fontWeight: 700, color: P.host }}>75.8 / 119.7</div>
          </div>
        </div>
        <div style={{ marginTop: 20, fontSize: 19, lineHeight: 1.72, color: P.ink }}>
          <div>✓ 零修改 MAIR_EL1（标准 Linux <code style={{ color: P.nc, fontFamily: "'JetBrains Mono', monospace" }}>pgprot_writecombine</code>）</div>
          <div>✓ 同一二进制通吃单向 / 闭环 / 低延迟，同机 6c STREAM <strong>99.5%–100%</strong></div>
          <div style={{ color: P.gold }}>⚠ 避坑红线：禁用 u4 跨槽前瞻 · 不超配 K = 8 · 限窗 W ≤ 256</div>
        </div>
      </Glass>

      <Glass
        x={974}
        y={312}
        w={870}
        h={422}
        kicker="路线 B（稳健备选） · 解耦批门铃 · 严保多行顺序"
        color={P.wb}
        borderColor={alpha(P.wb, 0.55)}
        opacity={kRouteB}
        style={rise(kRouteB)}
      >
        <div style={{ fontSize: 28, fontWeight: 700, color: P.wb, marginTop: 4 }}>
          解耦合并门铃 (prod_idx) + 批尾 1 次 SFENCE
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 14,
            marginTop: 18,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          <div style={{ background: alpha(P.bad, 0.12), borderRadius: 10, padding: "10px 14px" }}>
            <div style={{ fontSize: 14, color: P.sub }}>空载 W = 1</div>
            <div style={{ fontSize: 30, fontWeight: 700, color: P.bad }}>1,096 ns</div>
          </div>
          <div style={{ background: alpha(P.wb, 0.12), borderRadius: 10, padding: "10px 14px" }}>
            <div style={{ fontSize: 14, color: P.sub }}>单向 (B=128)</div>
            <div style={{ fontSize: 30, fontWeight: 700, color: P.wb }}>161.4 Mpps</div>
          </div>
          <div style={{ background: alpha(P.nc, 0.12), borderRadius: 10, padding: "10px 14px" }}>
            <div style={{ fontSize: 14, color: P.sub }}>闭环 (仅 1 卡核!)</div>
            <div style={{ fontSize: 30, fontWeight: 700, color: P.nc }}>66.7 M/s</div>
          </div>
        </div>
        <div style={{ marginTop: 20, fontSize: 19, lineHeight: 1.72, color: P.ink }}>
          <div>✓ 仅需 <strong>1 颗卡核</strong>即可吃满闭环（CHA 仅 7.1），极度省卡核</div>
          <div>✓ 读写时空彻底错开，严保多缓存行顺序，天然不怕超配冲撞</div>
          <div style={{ color: P.tad }}>⚠ 固有代价：空载 W = 1 因门铃 + SFENCE 固定多 <strong>+190 ns</strong> 延迟税</div>
        </div>
      </Glass>

      {/* Bottom Golden Mantra Strip */}
      <Glass
        x={76}
        y={762}
        w={1768}
        h={114}
        color={P.gold}
        borderColor={alpha(P.gold, 0.6)}
        opacity={kMantra}
        style={{
          ...rise(kMantra),
          background: "linear-gradient(90deg, rgba(77,232,200,0.12) 0%, rgba(255,210,122,0.14) 50%, rgba(110,168,254,0.12) 100%)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 38px",
        }}
      >
        <span style={{ fontSize: 22, fontWeight: 700, color: P.gold, letterSpacing: 1 }}>核心心法</span>
        <span style={{ fontSize: 30, fontWeight: 700, color: P.ink, letterSpacing: 1 }}>
          <span style={{ color: P.nc }}>绕过 L1/L2 卸旧锁</span>
          <span style={{ color: P.faint, margin: "0 20px" }}>·</span>
          <span style={{ color: P.gold }}>零前瞻避前沿撞</span>
          <span style={{ color: P.faint, margin: "0 20px" }}>·</span>
          <span style={{ color: P.host }}>配核限窗护邻居</span>
        </span>
        <span
          style={{
            fontSize: 16,
            fontWeight: 700,
            color: P.bg0,
            background: P.nc,
            padding: "6px 16px",
            borderRadius: 999,
          }}
        >
          MRV-258
        </span>
      </Glass>
    </>
  );
}

export const B06: SceneDef = {
  id: "B06",
  title: "一屏选型口诀：两面墙与两条工程路线",
  stage: 5,
  source: "MRV-155 · MRV-177 · MRV-235 · MRV-245 · MRV-252 · MRV-258",
  dur: DUR,
  cues,
  C: Body,
};
