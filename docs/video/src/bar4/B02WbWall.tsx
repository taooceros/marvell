import React from "react";
import { Cue, SceneDef, prog, useRealT, useT } from "../lib";
import { ArchBox, BigStat, Flow, Glass, P, PacketCell, Stage, Tag, Text, ZH_FONT, alpha, rise, win } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.45, 9.45, "默认映射是 `Normal-WB`：卡核只要读过一个槽，硬件就会把这条缓存行拉进核私有 **`L1D / L2`**。", "问题出在默认的 Normal W B 映射。卡核只要读过某个槽，硬件就会把这条缓存行搬进卡核私有的 L 一 D 和 L 二缓存。"], // 0
  [9.65, 19.01, "下一圈主机经 `BAR4` 写新包进 **48 MiB `TAD`** 时，硬件被迫跨核发 **`0x58` 嗅探**，逐一作废卡核私有副本。", "等环形队列绕回下一圈，主机再经 bar 四把新包写进四十八兆 T A D 时，硬件被迫跨核发出零 x 五八嗅探，去作废卡核私有的旧副本。"], // 1
  [19.21, 29.66, "实测闭环 `K = 4` 每包触发 `0.99` 次 `l1d_refill` 和 **`1.96` 次 `0x58` 作废**，吞吐焊死在 **`13.53 M/s`**。", "实测闭环四核下，平均每包触发零点九九次 L 一重填和一点九六次零 x 五八作废，读写在前沿死锁，吞吐被焊死在十三点五三百万包每秒。"], // 2
  [29.86, 40.63, "哪怕切到单向宽窗硬扛，`Normal-WB` 也只跑 **`104.2 M/s`**，还把主机 `CHA` 拥塞顶爆至 **`546.2`**，重伤同机邻居。", "哪怕切到单向宽窗硬扛，Normal W B 也只跑到一百零四点二百万包每秒，而且把主机 C H A 队列占用顶爆到五百四十六点二，直接拖垮同机邻居。"], // 3
  [40.83, 50.78, "那以前用 `WB` 怎么跑出 **161–175 Mpps** 的？真相只有两条路——**绝不让卡核去轮询正在写的数据槽**。", "这时候你一定会问，那以前用 W B 怎么跑到一百六十一到一百七十五百万包每秒的？真相很直白：全靠绝不让卡核去轮询正在写的数据槽。"], // 4
  [50.98, 61.60, "路一是**卡静默**（`tx_none`）：卡核压根不读，天然零嗅探，单核 **`161.7`**、双核 **`175.2 Mpps`**。", "第一种情况是卡静默纯写测试，卡核压根不读数据环，一条都不进 L 一缓存，天然零嗅探，单核一百六十一点七、双核一百七十五点二百万包每秒。"], // 5
  [61.80, 75.27, "路二是 **128 格解耦批门铃 + 批尾 `SFENCE`**（`161.42 Mpps`）：卡核只盯门铃、时空错开，代价是 `W = 1` 固定慢 **`~190 ns`**。", "第二种是解耦批门铃加批尾一次 S fence，实测跑出一百六十一点四二百万包每秒。主机写满一百二十八格再敲门铃，卡核只盯门铃不碰热槽，代价是空载延迟固定慢大约一百九十纳秒。"], // 6
];
const DUR = 76.27; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Phase 1 (cues 0..3): Normal-WB L1D/L2 0x58 snoop deadlock
  const p1 = win(t, T[0], T[4], 0.5);
  const kSnoop = prog(t, T[1], 0.5);
  const kLock = prog(t, T[2], 0.5);
  const kCha = prog(t, T[3], 0.5);

  // Phase 2 (cues 4..6): How historical WB reached 161-175 Mpps
  const p2 = prog(t, T[4], 0.55);
  const kSilent = prog(t, T[5], 0.5);
  const kDoorbell = prog(t, T[6], 0.5);

  const pulse = (rt * 0.9) % 1;
  const pulse2 = (rt * 1.15) % 1;

  return (
    <>
      {/* ==================== PHASE 1: Normal-WB 0x58 Snoop Wall ==================== */}
      <Stage opacity={p1}>
        {/* Main architecture panel */}
        <rect
          x={76}
          y={152}
          width={1160}
          height={726}
          rx={18}
          fill="rgba(12, 20, 38, 0.72)"
          stroke={alpha(P.wb, 0.35)}
          strokeWidth={1.8}
        />
        <text x={110} y={194} fontFamily={ZH_FONT} fontSize={22} fontWeight={700} fill={P.ink}>
          Normal-WB (0xFF) 硬件冲突链：读进 L1D/L2 → 写发 0x58 作废
        </text>

        {/* Host Xeon */}
        <ArchBox x={118} y={248} w={230} h={148} title="主机 Xeon" sub="MOVDIR64B → BAR4" color={P.host} />

        {/* PCIe / PEM0 */}
        <Flow pts={[[348, 322], [468, 322]]} color={P.host} w={4} glow />
        <PacketCell pts={[[348, 322], [468, 322]]} k={pulse} color={P.host} label="64B" />
        <text x={408} y={300} textAnchor="middle" fontFamily="'JetBrains Mono', monospace" fontSize={14} fill={P.host}>
          PEM0 写入
        </text>

        {/* 48 MiB TAD */}
        <ArchBox
          x={470}
          y={236}
          w={270}
          h={174}
          title="48 MiB TAD"
          sub="CN106 共享 L3 + 目录"
          color={P.tad}
          fill={alpha(P.tad, 0.14)}
        />

        {/* L1D / L2 Private Cache (highlighted purple/red) */}
        <ArchBox
          x={870}
          y={236}
          w={316}
          h={174}
          title="卡核私有 L1D / L2"
          sub="Normal-WB 自动缓存行副本"
          color={kSnoop > 0.3 ? P.bad : P.wb}
          fill={alpha(kSnoop > 0.3 ? P.bad : P.wb, 0.16)}
        />

        {/* Card Cores */}
        {[0, 1, 2, 3].map((c) => {
          const cx = 870 + c * 82;
          return (
            <g key={c}>
              <rect
                x={cx}
                y={490}
                width={70}
                height={56}
                rx={10}
                fill={alpha(P.core[c], 0.14)}
                stroke={P.core[c]}
                strokeWidth={2}
              />
              <text x={cx + 35} y={524} textAnchor="middle" fontFamily="'JetBrains Mono', monospace" fontSize={16} fontWeight={700} fill={P.ink}>
                C{c}
              </text>
              <Flow pts={[[cx + 35, 488], [cx + 35, 414]]} color={P.wb} w={2.5} head={9} />
            </g>
          );
        })}
        <text x={1028} y={580} textAnchor="middle" fontFamily={ZH_FONT} fontSize={18} fontWeight={600} fill={P.wb}>
          步骤 ①：卡核轮询读槽 → 硬件拉入私有 L1D / L2
        </text>

        {/* Step 2: 0x58 Snoop Invalidation arrows from TAD -> L1D/L2 */}
        {kSnoop > 0.05 && (
          <g opacity={kSnoop}>
            <Flow pts={[[742, 290], [866, 290]]} color={P.bad} w={4.5} glow />
            <PacketCell pts={[[742, 290], [866, 290]]} k={pulse2} color={P.bad} w={54} h={24} label="0x58" />
            <Flow pts={[[866, 354], [742, 354]]} color={P.bad} w={3} dash="6 5" />
            <text x={804} y={266} textAnchor="middle" fontFamily={ZH_FONT} fontSize={16} fontWeight={700} fill={P.bad}>
              0x58 嗅探作废
            </text>
            <text x={804} y={384} textAnchor="middle" fontFamily={ZH_FONT} fontSize={15} fill={P.sub}>
              等 ACK 才放行
            </text>
          </g>
        )}

        {/* Bottom ring collision diagram inside phase 1 */}
        <g opacity={kSnoop}>
          <rect
            x={118}
            y={626}
            width={1068}
            height={216}
            rx={14}
            fill="rgba(255, 100, 100, 0.08)"
            stroke={alpha(P.bad, 0.4)}
            strokeWidth={1.6}
          />
          <text x={150} y={666} fontFamily={ZH_FONT} fontSize={20} fontWeight={700} fill={P.bad}>
            步骤 ②：下一圈主机重写同槽 → 跨核嗅探 + 串行等作废 → 读写前沿死锁
          </text>
          {/* 8 mini ring cells showing dirty L1D copies */}
          {Array.from({ length: 8 }, (_, i) => {
            const rx = 152 + i * 124;
            const hit = i === 3;
            return (
              <g key={i}>
                <rect
                  x={rx}
                  y={696}
                  width={108}
                  height={62}
                  rx={8}
                  fill={hit ? alpha(P.bad, 0.28) : alpha(P.wb, 0.14)}
                  stroke={hit ? P.bad : P.wb}
                  strokeWidth={hit ? 3 : 1.6}
                />
                <text x={rx + 54} y={724} textAnchor="middle" fontFamily="'JetBrains Mono', monospace" fontSize={15} fontWeight={700} fill={P.ink}>
                  slot {i + 1}
                </text>
                <text x={rx + 54} y={746} textAnchor="middle" fontFamily={ZH_FONT} fontSize={13} fill={hit ? P.gold : P.wb}>
                  {hit ? "撞 0x58!" : "在 L1D 中"}
                </text>
              </g>
            );
          })}
          <text x={152} y={806} fontFamily={ZH_FONT} fontSize={18} fill={P.sub}>
            结论：在 Normal-WB 下，怎么改轮询代码都逃不掉「读进 L1D/L2 → 写必发 0x58 作废」的硬件死锁
          </text>
        </g>
      </Stage>

      {/* Right column metric cards for Phase 1 */}
      <div style={{ opacity: p1 }}>
        <Glass x={1272} y={152} w={572} h={376} kicker="闭环实测 · H=1, K=4, W=256" color={P.bad} opacity={kLock}>
          <BigStat x={24} y={54} value="13.53" unit="M/s" label="Normal-WB 闭环吞吐死锁" note="MRV-245 cl_wb_raw_k4_w256" color={P.bad} size={68} w={480} />
          <div style={{ position: "absolute", left: 24, top: 222, display: "flex", gap: 36 }}>
            <div>
              <div style={{ fontSize: 38, fontWeight: 700, color: P.wb, fontVariantNumeric: "tabular-nums" }}>0.99 / 包</div>
              <div style={{ fontSize: 16, color: P.sub }}>卡侧 l1d_refill</div>
            </div>
            <div>
              <div style={{ fontSize: 38, fontWeight: 700, color: P.bad, fontVariantNumeric: "tabular-nums" }}>1.96 / 包</div>
              <div style={{ fontSize: 16, color: P.sub }}>卡侧 0x58 (L2 作废)</div>
            </div>
          </div>
          <Text x={24} y={322} w={520} size={16} color={P.sub}>
            TAD 常规嗅探 EXLMN 达 3.04 / 包，主机 CHA 仅 1.4（纯卡侧死锁）
          </Text>
        </Glass>

        <Glass x={1272} y={554} w={572} h={324} kicker="单向宽窗硬扛 · H=1, K=4, W=6144" color={P.gold} opacity={kCha}>
          <div style={{ position: "absolute", left: 24, top: 58, display: "flex", gap: 46 }}>
            <div>
              <div style={{ fontSize: 54, fontWeight: 700, color: P.gold, fontVariantNumeric: "tabular-nums" }}>104.2</div>
              <div style={{ fontSize: 18, color: P.ink, fontWeight: 600 }}>单向 M/s（跑不满）</div>
            </div>
            <div>
              <div style={{ fontSize: 54, fontWeight: 700, color: P.bad, fontVariantNumeric: "tabular-nums" }}>546.2</div>
              <div style={{ fontSize: 18, color: P.bad, fontWeight: 600 }}>主机 CHA 占用爆表</div>
            </div>
          </div>
          <Text x={24} y={208} w={520} size={18} color={P.sub}>
            主机写出反压挤爆 Socket 1 片上队列，直接拖垮同机邻居；一旦切回闭环立刻跌回 13.53 M/s。
          </Text>
        </Glass>
      </div>

      {/* ==================== PHASE 2: How historical WB hit 161-175 Mpps ==================== */}
      <div style={{ opacity: p2 }}>
        <Glass
          x={76}
          y={144}
          w={1768}
          h={76}
          color={P.gold}
          borderColor={alpha(P.gold, 0.45)}
          style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 28px" }}
        >
          <span style={{ fontSize: 24, fontWeight: 700, color: P.gold }}>
            回答关键问题：以前用 Normal-WB 怎么跑出 161–175 Mpps 的？
          </span>
          <span style={{ fontSize: 20, fontWeight: 600, color: P.ink }}>
            秘诀只有一条：绝不让卡核去轮询正在被写的数据槽！
          </span>
        </Glass>

        {/* Left Card: Card Silent */}
        <Glass x={76} y={242} w={860} h={636} kicker="历史路线 ① · 卡静默 / 纯写黑洞（MRV-196 / MRV-245 tx_none）" color={P.host} opacity={kSilent}>
          <Text x={24} y={54} w={810} size={20} color={P.sub}>
            卡核压根不读接收环 → 数据槽一条都没进过 L1D/L2 → 天然 <strong style={{ color: P.nc }}>0 嗅探</strong>
          </Text>
          <div style={{ position: "absolute", left: 24, top: 378, display: "flex", gap: 48 }}>
            <BigStat x={0} y={0} value="161.7" unit="Mpps" label="单主机核直推上限" note="CHA = 16.7 · 零卡读" color={P.host} size={58} w={360} />
            <BigStat x={380} y={0} value="175.2" unit="Mpps" label="双主机核直推上限" note="tx_none_h2 实测" color={P.gold} size={58} w={360} />
          </div>
          <Tag x={24} y={558} color={P.host}>
            仅测主机 → PCIe → TAD 写入天花板，不等于卡核能实时消费
          </Tag>
        </Glass>

        {/* Right Card: Decoupled 128-cell Batch Doorbell + SFENCE */}
        <Glass x={984} y={242} w={860} h={636} kicker="历史路线 ② · 128 格解耦批门铃 + 批尾 SFENCE（MRV-155 / MRV-177）" color={P.wb} opacity={kDoorbell}>
          <Text x={24} y={54} w={812} size={19.5} color={P.sub}>
            主机写满 <strong style={{ color: P.gold }}>128 格</strong> → 批尾 1 次 <strong style={{ color: P.wb }}>SFENCE</strong> → 敲门铃；卡核<strong style={{ color: P.nc }}>只盯门铃</strong>，读写时空错开！
          </Text>
          <div style={{ position: "absolute", left: 24, top: 378, display: "flex", gap: 36 }}>
            <BigStat x={0} y={0} value="161.42" unit="Mpps" label="单核全包验证消费" note="MRV-155 Stage 1 · 零前沿冲撞" color={P.nc} size={58} w={380} />
            <BigStat x={410} y={0} value="+190" unit="ns" label="W=1 空载固定延迟税" note="1,096 ns vs 905 ns（门铃+SFENCE）" color={P.bad} size={58} w={380} />
          </div>
          <Tag x={24} y={558} color={P.wb}>
            闭环 K=1 稳跑 66.7 M/s (CHA 7.1) · 但无法做到免门铃 904 ns 低延迟
          </Tag>
        </Glass>
      </div>

      {/* SVG diagrams inside Phase 2 cards */}
      <Stage opacity={p2}>
        {/* Left card diagram: Silent card */}
        <g opacity={kSilent}>
          <ArchBox x={118} y={390} w={180} h={108} title="主机" sub="MOVDIR64B" color={P.host} />
          <Flow pts={[[298, 444], [412, 444]]} color={P.host} w={4} glow />
          <PacketCell pts={[[298, 444], [412, 444]]} k={pulse} color={P.host} label="64B" />
          <ArchBox x={416} y={390} w={196} h={108} title="48 MiB TAD" sub="畅通落盘" color={P.tad} />
          <ArchBox x={678} y={390} w={212} h={108} title="卡核 (静默)" sub="0 次读 · 0 嗅探" color={P.faint} dashed />
          <line x1={624} y1={444} x2={668} y2={444} stroke={P.faint} strokeWidth={2.5} strokeDasharray="5 5" />
        </g>

        {/* Right card diagram: Decoupled 128-cell batch doorbell */}
        <g opacity={kDoorbell}>
          {/* Batch N being written */}
          <rect x={1020} y={376} width={356} height={106} rx={12} fill={alpha(P.host, 0.14)} stroke={P.host} strokeWidth={2} />
          <text x={1198} y={412} textAnchor="middle" fontFamily={ZH_FONT} fontSize={18} fontWeight={700} fill={P.host}>
            批 N（128 格热写区）
          </text>
          <text x={1198} y={442} textAnchor="middle" fontFamily="'JetBrains Mono', monospace" fontSize={14} fill={P.sub}>
            主机写完 → SFENCE → 敲 prod_idx
          </text>
          <text x={1198} y={466} textAnchor="middle" fontFamily={ZH_FONT} fontSize={14} fontWeight={700} fill={P.nc}>
            卡核绝不碰此区！
          </text>

          {/* Doorbell arrow */}
          <Flow pts={[[1378, 429], [1448, 429]]} color={P.wb} w={4} glow />
          <text x={1413} y={406} textAnchor="middle" fontFamily="'JetBrains Mono', monospace" fontSize={13} fontWeight={700} fill={P.wb}>
            门铃
          </text>

          {/* Batch N-1 safely drained */}
          <rect x={1452} y={376} width={356} height={106} rx={12} fill={alpha(P.nc, 0.12)} stroke={P.nc} strokeWidth={2} />
          <text x={1630} y={412} textAnchor="middle" fontFamily={ZH_FONT} fontSize={18} fontWeight={700} fill={P.nc}>
            批 N-1（已写稳旧区）
          </text>
          <text x={1630} y={442} textAnchor="middle" fontFamily={ZH_FONT} fontSize={15} fill={P.ink}>
            卡核看到门铃推进才来扫读
          </text>
          <text x={1630} y={466} textAnchor="middle" fontFamily="'JetBrains Mono', monospace" fontSize={13} fill={P.sub}>
            读写时空完全错开
          </text>
        </g>
      </Stage>
    </>
  );
}

export const B02: SceneDef = {
  id: "B02",
  title: "第一道墙：Normal-WB 嗅探死锁与历史 175 Mpps 真相",
  stage: 1,
  source: "MRV-235/245 runs.jsonl · MRV-155/177/196",
  dur: DUR,
  cues,
  C: Body,
};
