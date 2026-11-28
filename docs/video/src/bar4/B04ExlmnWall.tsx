import React from "react";
import { Cue, SceneDef, prog, useRealT, useT } from "../lib";
import { CodeCard, Flow, Glass, P, PacketCell, Stage, Tag, Text, ZH_FONT, alpha, rise, win } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.45, 11.27, "先说大白话：**`EXLMN`** 不是玄学协议，它来自内核 `marvell_cn10k_tad_pmu.c`，全称 **`excluding MN`**（排除杂项节点）。", "先把 E X L M N 讲透。它不是什么新协议，而是 C N 一零六内核 T A D 计数器的缩写，全称 excluding M N，意思是排除杂项控制节点。"], // 0
  [11.47, 21.93, "`TAD` 的片上嗅探 `tad_snp_msh_out_any` 拆成 `mn`（TLB / 同步广播）加 **`exlmn`（事件 `0x16`：常规缓存行嗅探）**。", "T A D 往片上网格发出的嗅探拆成两半：M N 是 T L B 和同步广播，而 E X L M N 就是排除杂项后、所有针对普通缓存行的常规嗅探。"], // 1
  [22.13, 34.04, "单向宽窗 `W = 6144` 时，主机狂奔领跑数千格，卡核在安全后方读旧槽：**`EXLMN = 0.009 / 包`**，稳跑 **`159.9 M/s`**。", "看单向宽窗 W 等于六一四四：主机不用等回包，领先卡核好几千格，卡核永远在安全后方消费，E X L M N 每包只有零点零零九次，稳跑一百六十百万包每秒。"], // 2
  [34.24, 45.46, "但进了闭环 `W = 256 / 512`，主机必须等回包、缓冲垫变薄，卡核瞬间**追平写入前沿**，撞上主机正在落盘的槽。", "一进闭环 W 等于二五六或五一二，主机发满窗口就得停下来等回包，缓冲垫瞬间变薄，卡核追平写入前沿，和主机刚进来的写操作面对面撞上。"], // 3
  [45.66, 57.87, "此时 **`u4_ldpq`**（`8× ldp q`）一次齐射 `s0..s3`，往前偷看 **`3 × K = 12` 格未写空槽**；连 `adapt-*` 一命中也会切到 `u4` 中招！", "更致命的是 u 四 l d p q：它一口气预读未来三个步长共十二格的未写空槽。就连带退避的 adapt 状态机，只要 s 零一命中也会立刻切进 u 四，照样中招。"], // 4
  [58.07, 70.40, "穿透 `L1/L2` 的空读正面对撞 `PEM0` 入向写：`EXLMN` 飙升 **119×** 至 **`1.06 / 包`**，`tad_req_occ` 暴涨 **`7.0×`**！", "这些绕过 L 一和 L 二直打 T A D 的未来空读，与主机 P E M 零入向写迎头相撞。E X L M N 飙升一百一十九倍到每包一点零六次，T A D 请求队列占用暴涨七倍。"], // 5
  [70.60, 84.01, "**读撞写 → 写变慢 → 回包慢 → 主机发更慢 → 每包必撞**：正反馈雪崩把闭环锁死在 **`17.6–19.7 M/s`**（超配 `K = 8` 亦跌至 `16.3 M/s`）。", "读撞写让写变慢，写变慢让回包变慢，闭环主机拿不到回包发得更慢，导致每包必撞。正反馈雪崩把吞吐一路按死在十七点六到十九点七百万包每秒，超配八核也一样跌到十六点三。"], // 6
];
const DUR = 85.01; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Phase 1 (cues 0..1): Plain-Chinese decode of EXLMN from kernel source
  const p1 = win(t, T[0], T[2], 0.5);
  const kSplit = prog(t, T[1], 0.5);

  // Phase 2 (cues 2..6): One-way safe rear vs Closed-loop u4 frontier avalanche
  const p2 = prog(t, T[2], 0.55);
  const kClosed = prog(t, T[3], 0.5);
  const kU4 = prog(t, T[4], 0.5);
  const kSpike = prog(t, T[5], 0.55);
  const kLoop = prog(t, T[6], 0.55);

  const pulse = (rt * 1.1) % 1;
  const flash = 0.55 + 0.45 * Math.sin(rt * 7.5);

  return (
    <>
      {/* ==================== PHASE 1: What is EXLMN? ==================== */}
      <div style={{ opacity: p1 }}>
        <CodeCard
          x={76}
          y={156}
          w={910}
          h={396}
          title="linux-marvell/drivers/perf/marvell_cn10k_tad_pmu.c"
          badge="CN10K 内核驱动 · L285–306"
          accent={P.tad}
          fontSize={17.5}
          lines={[
            { text: "static struct attribute *tad_pmu_event_attrs[] = {" },
            { text: "  TAD_PMU_EVENT_ATTR(tad_req_msh_in_any,    0x1)," },
            { text: "  TAD_PMU_EVENT_ATTR(tad_req_msh_in_mn,     0x2)," },
            { text: "  TAD_PMU_EVENT_ATTR(tad_req_msh_in_exlmn,  0x3)," },
            { text: "  ..." },
            {
              text: "  TAD_PMU_EVENT_ATTR(tad_snp_msh_out_any,   0x14),",
              hl: kSplit > 0.2 ? "host" : undefined,
              note: kSplit > 0.2 ? "全部片上嗅探" : undefined,
            },
            {
              text: "  TAD_PMU_EVENT_ATTR(tad_snp_msh_out_mn,    0x15),",
              hl: kSplit > 0.2 ? "wb" : undefined,
              note: kSplit > 0.2 ? "杂项广播 (~0.2%)" : undefined,
            },
            {
              text: "  TAD_PMU_EVENT_ATTR(tad_snp_msh_out_exlmn, 0x16),",
              hl: "gold",
              note: "常规缓存行嗅探 (~99.8%)",
            },
            { text: "};" },
          ]}
        />

        <Glass x={1016} y={156} w={828} h={396} kicker="白话拆解 · EXLMN = excluding MN" color={P.gold}>
          <div style={{ fontSize: 22, color: P.ink, lineHeight: 1.65, marginTop: 8 }}>
            <div style={{ marginBottom: 14 }}>
              <span style={{ color: P.gold, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>exlmn</span> ={" "}
              <strong style={{ color: P.gold }}>excluding MN</strong>（排除杂项节点）
            </div>
            <div
              style={{
                background: alpha(P.tad, 0.12),
                border: `1.5px solid ${alpha(P.tad, 0.4)}`,
                borderRadius: 12,
                padding: "14px 18px",
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 19,
                marginBottom: 18,
              }}
            >
              <span style={{ color: P.host }}>any (0x14)</span> = <span style={{ color: P.wb }}>mn (0x15)</span> +{" "}
              <span style={{ color: P.gold, fontWeight: 700 }}>exlmn (0x16)</span>
            </div>
            <div style={{ fontSize: 19, color: P.sub, marginBottom: 10 }}>
              • <strong style={{ color: P.wb }}>mn (Miscellaneous Node)</strong>：ARM CHI 总线上的 DVM / TLB 广播与全局同步消息
            </div>
            <div style={{ fontSize: 19, color: P.ink }}>
              • <strong style={{ color: P.gold }}>exlmn (excluding MN)</strong>：排除杂项广播后，48 MiB TAD 发出的<strong>常规缓存行嗅探</strong>
            </div>
          </div>
        </Glass>

        <Glass x={76} y={584} w={1768} h={284} kicker="为什么它成了闭环雪崩的照妖镜？" color={P.tad} opacity={kSplit}>
          <div style={{ display: "flex", gap: 36, marginTop: 10 }}>
            <div style={{ flex: 1, fontSize: 21, lineHeight: 1.65, color: P.ink }}>
              在 <code style={{ color: P.nc, fontFamily: "'JetBrains Mono', monospace" }}>MT_NORMAL_NC</code> 下，卡核 L1D/L2 根本不缓存接收环（
              <strong style={{ color: P.nc }}>0x58 = 0.00</strong>），不存在旧的私有缓存作废。
              <br />
              因此一旦 <strong style={{ color: P.gold }}>tad_snp_msh_out_exlmn</strong> 突然暴涨，就铁证如山地指向：
              <strong style={{ color: P.bad }}>卡核穿透 L1/L2 的空读，正在 TAD 写入前沿与主机入向写正面对撞！</strong>
            </div>
            <div
              style={{
                width: 548,
                background: alpha(P.bad, 0.1),
                border: `1.5px solid ${alpha(P.bad, 0.4)}`,
                borderRadius: 14,
                padding: "16px 22px",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              <div style={{ fontSize: 16, color: P.sub }}>MRV-252 实测对比（K = 4）</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: P.nc, marginTop: 12, whiteSpace: "nowrap" }}>
                单向宽窗：0.009 次/包 → 159.9 M/s
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, color: P.bad, marginTop: 12, whiteSpace: "nowrap" }}>
                闭环 u4：1.06 次/包 (119×) → 17.6 M/s
              </div>
            </div>
          </div>
        </Glass>
      </div>

      {/* ==================== PHASE 2: One-way Safe vs Closed-loop u4 Avalanche ==================== */}
      <Stage opacity={p2}>
        {/* TOP STRIP: One-way wide window W = 6144 (Safe Rear) */}
        <rect
          x={76}
          y={144}
          width={1768}
          height={196}
          rx={16}
          fill="rgba(12, 24, 42, 0.78)"
          stroke={alpha(P.nc, 0.42)}
          strokeWidth={1.8}
        />
        <text x={108} y={180} fontFamily={ZH_FONT} fontSize={20} fontWeight={700} fill={P.nc}>
          ① 单向宽窗 W = 6144（安全后方区）：主机领跑数千格，卡核只读早已落盘的旧槽
        </text>
        {/* Ring slots for one-way */}
        {Array.from({ length: 12 }, (_, i) => {
          const sx = 108 + i * 94;
          const isRead = i === 1;
          const isWrite = i === 11;
          const filled = i >= 1 && i <= 11;
          return (
            <g key={i}>
              <rect
                x={sx}
                y={222}
                width={82}
                height={52}
                rx={8}
                fill={isRead ? alpha(P.nc, 0.25) : isWrite ? alpha(P.host, 0.28) : filled ? alpha(P.host, 0.1) : "rgba(10,16,28,0.8)"}
                stroke={isRead ? P.nc : isWrite ? P.host : filled ? alpha(P.host, 0.45) : P.hair2}
                strokeWidth={isRead || isWrite ? 2.6 : 1.4}
              />
              <text
                x={sx + 41}
                y={254}
                textAnchor="middle"
                fontFamily="'JetBrains Mono', monospace"
                fontSize={14}
                fontWeight={700}
                fill={isRead ? P.nc : isWrite ? P.host : P.sub}
              >
                {isRead ? "卡读" : isWrite ? "主机写" : i === 6 ? "···数千格···" : "已落盘"}
              </text>
            </g>
          );
        })}
        <line x1={210} y1={296} x2={1120} y2={296} stroke={P.nc} strokeWidth={2.5} strokeDasharray="6 4" />
        <text x={665} y={320} textAnchor="middle" fontFamily={ZH_FONT} fontSize={16} fontWeight={700} fill={P.nc}>
          超厚安全缓冲垫（读与写相隔数千格，互不干扰）
        </text>
        {/* Right summary box for one-way */}
        <rect x={1272} y={196} width={542} height={116} rx={12} fill={alpha(P.nc, 0.1)} stroke={alpha(P.nc, 0.35)} strokeWidth={1.4} />
        <text x={1302} y={244} fontFamily="'JetBrains Mono', monospace" fontSize={32} fontWeight={700} fill={P.nc}>
          159.9 M/s
        </text>
        <text x={1524} y={244} fontFamily="'JetBrains Mono', monospace" fontSize={22} fontWeight={700} fill={P.gold}>
          EXLMN 0.009 / 包
        </text>
        <text x={1302} y={286} fontFamily={ZH_FONT} fontSize={17} fill={P.sub}>
          tad_req_occ = 2.7 亿周期 · 主机 CHA = 16.9
        </text>

        {/* MIDDLE STRIP: Closed-loop W=256/512 + u4_ldpq lookahead collision */}
        <g opacity={kClosed}>
          <rect
            x={76}
            y={358}
            width={1038}
            height={272}
            rx={16}
            fill="rgba(34, 14, 22, 0.72)"
            stroke={alpha(P.bad, 0.48)}
            strokeWidth={1.8}
          />
          <text x={108} y={396} fontFamily={ZH_FONT} fontSize={20} fontWeight={700} fill={P.bad}>
            ② 闭环 W = 256 / 512：缓冲垫归零，卡核追平写入前沿 + u4 偷看未来空槽！
          </text>

          {/* 9 slots showing frontier + s0, s1, s2, s3 lookahead */}
          {Array.from({ length: 9 }, (_, i) => {
            const sx = 108 + i * 108;
            const isS0 = i === 2;
            const isFuture = i === 4 || i === 6 || i === 8;
            const isWriting = i === 4;
            const label =
              i === 2 ? "s0 刚到" : i === 4 ? "s1 正写!" : i === 6 ? "s2 空槽" : i === 8 ? "s3 空槽" : i < 2 ? "已读" : "其他核";
            return (
              <g key={i}>
                <rect
                  x={sx}
                  y={454}
                  width={96}
                  height={62}
                  rx={8}
                  fill={
                    isWriting && kU4 > 0.2
                      ? alpha(P.bad, 0.25 + 0.18 * flash)
                      : isFuture && kU4 > 0.2
                        ? alpha(P.bad, 0.16)
                        : isS0
                          ? alpha(P.gold, 0.22)
                          : "rgba(12,18,32,0.85)"
                  }
                  stroke={isFuture && kU4 > 0.2 ? P.bad : isS0 ? P.gold : P.hair2}
                  strokeWidth={isFuture || isS0 ? 2.6 : 1.4}
                />
                <text
                  x={sx + 48}
                  y={490}
                  textAnchor="middle"
                  fontFamily={ZH_FONT}
                  fontSize={15}
                  fontWeight={700}
                  fill={isFuture && kU4 > 0.2 ? P.bad : isS0 ? P.gold : P.sub}
                >
                  {label}
                </text>
              </g>
            );
          })}

          {/* Host PEM0 write colliding onto s1 */}
          <Flow pts={[[588, 412], [588, 450]]} color={P.host} w={4} head={11} glow />
          <text x={604} y={434} fontFamily={ZH_FONT} fontSize={15} fontWeight={700} fill={P.host}>
            ← 主机 PEM0 正在落盘
          </text>

          {/* u4_ldpq shooting 4 concurrent loads onto s0, s1, s2, s3 */}
          {kU4 > 0.05 && (
            <g opacity={kU4}>
              {[2, 4, 6, 8].map((slotIdx, j) => {
                const tx = 108 + slotIdx * 108 + 48;
                const col = j === 0 ? P.gold : P.bad;
                const pk = (pulse + j * 0.27) % 1;
                return (
                  <g key={slotIdx}>
                    <Flow pts={[[372, 578], [tx, 520]]} color={col} w={j === 0 ? 3 : 3.8} head={11} glow={j > 0} />
                    {j > 0 && <PacketCell pts={[[372, 578], [tx, 520]]} k={pk} color={P.bad} w={44} h={20} label="空读" />}
                  </g>
                );
              })}
              <rect x={174} y={578} width={396} height={36} rx={10} fill={alpha(P.bad, 0.22)} stroke={P.bad} strokeWidth={1.6} />
              <text x={372} y={602} textAnchor="middle" fontFamily="'JetBrains Mono', monospace" fontSize={16} fontWeight={700} fill={P.ink}>
                u4_ldpq (8× ldp q) 往前偷看 3×K = 12 格！
              </text>
              <text x={600} y={602} fontFamily={ZH_FONT} fontSize={16} fontWeight={700} fill={P.bad}>
                ⚡ 正面撞击 TAD 前沿 → 触发 EXLMN！
              </text>
            </g>
          )}
        </g>

        {/* BOTTOM LEFT: 5-step Positive-Feedback Avalanche Loop */}
        <g opacity={kLoop}>
          <rect
            x={76}
            y={648}
            width={1038}
            height={232}
            rx={16}
            fill="rgba(12, 20, 38, 0.8)"
            stroke={alpha(P.tad, 0.42)}
            strokeWidth={1.8}
          />
          <text x={108} y={686} fontFamily={ZH_FONT} fontSize={20} fontWeight={700} fill={P.tad}>
            ③ 正反馈雪崩闭环：为什么加大窗口 W = 64 → 256 → 512 也救不活？
          </text>
          {[
            { x: 108, title: "1. 前瞻空读", sub: "直打 TAD 前沿", col: P.bad },
            { x: 306, title: "2. 触发 EXLMN", sub: "1.06/包 · 串行停顿", col: P.bad },
            { x: 504, title: "3. 写变慢", sub: "TAD 排队 7.0×", col: P.tad },
            { x: 702, title: "4. 回包变慢", sub: "闭环主机等 ACK", col: P.gold },
            { x: 900, title: "5. 每包必撞", sub: "锁死 17.6 M/s", col: P.bad },
          ].map((st, idx) => (
            <g key={st.title}>
              <rect x={st.x} y={718} width={174} height={92} rx={12} fill={alpha(st.col, 0.14)} stroke={st.col} strokeWidth={2} />
              <text x={st.x + 87} y={756} textAnchor="middle" fontFamily={ZH_FONT} fontSize={18} fontWeight={700} fill={P.ink}>
                {st.title}
              </text>
              <text x={st.x + 87} y={786} textAnchor="middle" fontFamily={ZH_FONT} fontSize={14} fill={st.col}>
                {st.sub}
              </text>
              {idx < 4 && <Flow pts={[[st.x + 174, 764], [st.x + 196, 764]]} color={P.tad} w={3} head={9} />}
            </g>
          ))}
          {/* Feedback return arc */}
          <path d="M 987 812 L 987 852 L 195 852 L 195 814" fill="none" stroke={P.bad} strokeWidth={2.5} strokeDasharray="7 5" />
          <text x={591} y={845} textAnchor="middle" fontFamily={ZH_FONT} fontSize={15} fontWeight={700} fill={P.bad}>
            ↻ 正反馈锁死（W=64/256/512 全锁 17.5–17.7 M/s · 超配 K=8 跌至 16.3 M/s）
          </text>
        </g>
      </Stage>

      {/* Right column for Phase 2: adapt-* real code trap + PMU explosion card */}
      <div style={{ opacity: p2 }}>
        <div style={rise(kU4)}>
          <CodeCard
            x={1138}
            y={358}
            w={706}
            h={272}
            title="mrv252_card.rs:1162-1197 · adapt-* 为何也中招"
            badge="退避 50–100 ns 仍锁 19.7 M/s"
            accent={P.bad}
            fontSize={16.5}
            lines={[
              { text: "if in_busy_burst {", hl: "bad", note: "s0 命中即切入" },
              { text: "  let qs = load_4x64b_ldpq(p0,p1,p2,p3);", hl: "bad", note: "立刻齐射 u4!" },
              { text: "} else {" },
              { text: "  if load_u64_hdr(p0) == next { consume(s0); }" },
              { text: "  else { pause_ns(50); } // TAD 流量降 48%" },
              { text: "} // 一命中又切回 u4 → EXLMN 仍 1.05/包！" },
            ]}
          />
        </div>

        <Glass x={1138} y={648} w={706} h={232} kicker="MRV-252 实测铁证 · K=4, W=256 闭环雪崩" color={P.bad} opacity={kSpike}>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12, fontVariantNumeric: "tabular-nums" }}>
            <div>
              <div style={{ fontSize: 44, fontWeight: 700, color: P.bad }}>1.06 / 包</div>
              <div style={{ fontSize: 16, color: P.ink, fontWeight: 600 }}>EXLMN 飙升 119×</div>
              <div style={{ fontSize: 14, color: P.sub }}>对比单向 0.009 / 包</div>
            </div>
            <div>
              <div style={{ fontSize: 44, fontWeight: 700, color: P.tad }}>7.0×</div>
              <div style={{ fontSize: 16, color: P.ink, fontWeight: 600 }}>tad_req_occ 暴涨</div>
              <div style={{ fontSize: 14, color: P.sub }}>2.7 亿 → 19.1 亿周期</div>
            </div>
            <div>
              <div style={{ fontSize: 44, fontWeight: 700, color: P.gold }}>17.6 M/s</div>
              <div style={{ fontSize: 16, color: P.ink, fontWeight: 600 }}>闭环吞吐死锁</div>
              <div style={{ fontSize: 14, color: P.sub }}>主机 CHA 仅 1.8</div>
            </div>
          </div>
          <Tag x={22} y={178} color={P.gold} opacity={kLoop}>
            神对照 (K=2 u4)：H=2 供需未追平 → 88.0 M/s · H=1 追平前沿 → 18.0 M/s！
          </Tag>
        </Glass>
      </div>
    </>
  );
}

export const B04: SceneDef = {
  id: "B04",
  title: "第二道墙：什么是 EXLMN？闭环 + u4 为何雪崩至 17.6 M/s",
  stage: 3,
  source: "marvell_cn10k_tad_pmu.c:304-306 · mrv252_card.rs:1162-1191",
  dur: DUR,
  cues,
  C: Body,
};
