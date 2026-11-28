import React from "react";
import { Cue, SceneDef, prog, useRealT, useT } from "../lib";
import { CodeCard, Flow, P, PacketCell, Stage, Tag, Text, ZH_FONT, alpha, rise, win } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.45, 8.57, "主机把 **64 字节**请求经 `BAR4` 写进网卡共享环，为什么免门铃轮询会撞上**两面不同的硬件墙**？", "主机把六十四字节请求通过 bar 四写进网卡共享环，为什么免门铃轮询会先后撞上两面完全不同的硬件墙？"], // 0
  [8.77, 18.42, "先看主机热循环 `mrv245_host.rs`：每条 `Lane` 按 **128 字节步长**取槽，用 `MOVDIR64B` 原子推入 `BAR4` 槽 15。", "先看真实的主机热循环代码。每条通道按一百二十八字节步长错开缓存行，用 move dir 六十四字节指令原子推入 bar 四槽十五。"], // 1
  [18.62, 27.91, "步长取 **128 字节**而非 64 字节，专门避开 `CN106` 128 字节缓存行的伪共享；发完即走，**零门铃、零 `SFENCE`**。", "步长刻意取一百二十八字节，是为了避开 C N 一零六一百二十八字节缓存行的伪共享。写完即走，全程零门铃、零 S fence。"], // 2
  [28.11, 37.08, "卡侧 `mrv245_card.rs` 把 `H` 条 `Lane` 划给 `K` 颗卡核：每核起始 `next = lane_w + 1`，步长 `stride = K / H`。", "再看卡侧代码。H 条独立通道均匀划给 K 颗卡核，每颗核从自己的编号加一起步，步长等于每通道核数 K 除以 H。"], // 3
  [37.28, 48.11, "以 `H = 1, K = 4` 为例：0 号核扫 `1, 5, 9`，1 号核扫 `2, 6, 10`——槽位严格不交叠，**全程零跨核锁**。", "比如单通道配四颗卡核，零号核只扫一、五、九号槽，一号核只扫二、六、十号槽。槽位严格不交叠，完全不需要任何跨核锁。"], // 4
  [48.31, 58.67, "每格用头尾双序号 `words[0]` 与 `words[7]` 校验完整性，命中即回包。代码极简，可一上默认内存映射就锁死在 **13.5 M/s**。", "卡核直接比对首尾两个序号字段校验完整性，命中就回包。这套代码极其干净，但一跑在默认内存映射上，吞吐立刻被锁死在十三点五百万包每秒。"], // 5
];
const DUR = 59.67; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  const kHost = prog(t, T[1], 0.55);
  const kStride = prog(t, T[2], 0.5);
  const kCard = prog(t, T[3], 0.55);
  const kPartition = prog(t, T[4], 0.55);
  const kVerify = prog(t, T[5], 0.55);

  // Continuous host write sweep across 12 visible ring slots
  const cycle = (rt * 2.1) % 12;
  const hostSlot = Math.floor(cycle);
  const slotFrac = cycle - hostSlot;

  const slotX0 = 132;
  const slotW = 122;
  const slotGap = 14;
  const slotY = 636;
  const slotH = 74;

  return (
    <>
      <Stage>
        {/* Bottom BAR4 Slot 15 shared ring container */}
        <g opacity={prog(t, T[0], 0.6)}>
          <rect
            x={76}
            y={522}
            width={1768}
            height={362}
            rx={18}
            fill="rgba(12, 20, 38, 0.78)"
            stroke={alpha(P.host, 0.34)}
            strokeWidth={1.8}
          />
          <text x={110} y={558} fontFamily={ZH_FONT} fontSize={20} fontWeight={700} fill={P.ink}>
            BAR4 槽 15 共享接收环（每条 Lane 独立环形槽，示例 H = 1, K = 4）
          </text>
          <text x={1810} y={558} textAnchor="end" fontFamily="'JetBrains Mono', monospace" fontSize={16} fill={P.sub}>
            CELL_STRIDE = 128 B (64 B cell + 64 B pad)
          </text>

          {/* 12 ring slots */}
          {Array.from({ length: 12 }, (_, i) => {
            const sx = slotX0 + i * (slotW + slotGap);
            const owner = i % 4;
            const ownerCol = P.core[owner];
            const isWriting = i === hostSlot;
            const isFilled = i <= hostSlot;
            const strokeCol = kPartition > 0.2 ? ownerCol : isWriting ? P.host : isFilled ? alpha(P.host, 0.7) : P.hair2;
            const fillCol = isWriting
              ? alpha(P.host, 0.26)
              : kPartition > 0.2
                ? alpha(ownerCol, isFilled ? 0.16 : 0.06)
                : isFilled
                  ? alpha(P.host, 0.12)
                  : "rgba(9,14,26,0.85)";
            return (
              <g key={i}>
                {/* 128 B stride bracket cleanly below slots 1-2 (zero overlap with write packet) */}
                {i === 0 && kStride > 0.05 && (
                  <g opacity={kStride * (1 - prog(t, T[4], 0.45))}>
                    <line x1={sx} y1={slotY + slotH + 22} x2={sx + slotW + slotGap} y2={slotY + slotH + 22} stroke={P.gold} strokeWidth={2.2} />
                    <line x1={sx} y1={slotY + slotH + 16} x2={sx} y2={slotY + slotH + 28} stroke={P.gold} strokeWidth={2.2} />
                    <line x1={sx + slotW + slotGap} y1={slotY + slotH + 16} x2={sx + slotW + slotGap} y2={slotY + slotH + 28} stroke={P.gold} strokeWidth={2.2} />
                    <text
                      x={sx + (slotW + slotGap) / 2}
                      y={slotY + slotH + 46}
                      textAnchor="middle"
                      fontFamily="'JetBrains Mono', monospace"
                      fontSize={16}
                      fontWeight={700}
                      fill={P.gold}
                    >
                      128 B 步长
                    </text>
                  </g>
                )}

                <rect x={sx} y={slotY} width={slotW} height={slotH} rx={10} fill={fillCol} stroke={strokeCol} strokeWidth={isWriting ? 3 : 2} />

                {/* Split 64 B data + 64 B pad hint */}
                <line
                  x1={sx + slotW * 0.68}
                  y1={slotY + 4}
                  x2={sx + slotW * 0.68}
                  y2={slotY + slotH - 4}
                  stroke={alpha(P.sub, 0.28)}
                  strokeWidth={1.2}
                  strokeDasharray="3 3"
                />
                <text
                  x={sx + slotW * 0.34}
                  y={slotY + 32}
                  textAnchor="middle"
                  fontFamily="'JetBrains Mono', monospace"
                  fontSize={18}
                  fontWeight={700}
                  fill={isWriting ? P.gold : P.ink}
                >
                  s={i + 1}
                </text>
                <text
                  x={sx + slotW * 0.34}
                  y={slotY + 56}
                  textAnchor="middle"
                  fontFamily="'JetBrains Mono', monospace"
                  fontSize={13}
                  fill={kVerify > 0.2 ? P.nc : P.sub}
                >
                  {kVerify > 0.2 ? "w0..w7" : "64 B"}
                </text>
                <text
                  x={sx + slotW * 0.84}
                  y={slotY + 42}
                  textAnchor="middle"
                  fontFamily="'JetBrains Mono', monospace"
                  fontSize={12}
                  fill={P.faint}
                >
                  pad
                </text>

                {/* Core ownership pill underneath */}
                {kPartition > 0.05 && (
                  <g opacity={kPartition}>
                    <rect
                      x={sx + 12}
                      y={slotY + slotH + 14}
                      width={slotW - 24}
                      height={26}
                      rx={6}
                      fill={alpha(ownerCol, 0.16)}
                      stroke={alpha(ownerCol, 0.55)}
                      strokeWidth={1.3}
                    />
                    <text
                      x={sx + slotW / 2}
                      y={slotY + slotH + 32}
                      textAnchor="middle"
                      fontFamily="'JetBrains Mono', monospace"
                      fontSize={14}
                      fontWeight={700}
                      fill={ownerCol}
                    >
                      Core {owner}
                    </text>
                  </g>
                )}
              </g>
            );
          })}

          {/* Animated host MOVDIR64B write head arrow */}
          {kHost > 0.05 && (
            <g opacity={kHost}>
              <Flow
                pts={[
                  [slotX0 + hostSlot * (slotW + slotGap) + slotW / 2, 578],
                  [slotX0 + hostSlot * (slotW + slotGap) + slotW / 2, slotY - 6],
                ]}
                color={P.host}
                w={3.5}
                head={11}
                glow
              />
              <PacketCell
                pts={[
                  [slotX0 + hostSlot * (slotW + slotGap) + slotW / 2, 576],
                  [slotX0 + hostSlot * (slotW + slotGap) + slotW / 2, slotY - 12],
                ]}
                k={slotFrac}
                color={P.host}
                w={46}
                h={22}
                label="64B"
              />
            </g>
          )}

          {/* Core striding legend row at bottom of ring card */}
          {kPartition > 0.05 && (
            <g opacity={kPartition}>
              {[
                { c: 0, txt: "Core 0 → 槽 1, 5, 9 …" },
                { c: 1, txt: "Core 1 → 槽 2, 6, 10 …" },
                { c: 2, txt: "Core 2 → 槽 3, 7, 11 …" },
                { c: 3, txt: "Core 3 → 槽 4, 8, 12 …" },
              ].map((item, idx) => (
                <g key={item.c}>
                  <circle cx={150 + idx * 350} cy={832} r={7} fill={P.core[item.c]} />
                  <text
                    x={168 + idx * 350}
                    y={838}
                    fontFamily={ZH_FONT}
                    fontSize={18}
                    fontWeight={600}
                    fill={P.ink}
                  >
                    {item.txt}
                  </text>
                </g>
              ))}
              <text x={1810} y={838} textAnchor="end" fontFamily={ZH_FONT} fontSize={18} fontWeight={700} fill={P.nc}>
                零跨核锁 · stride = K/H
              </text>
            </g>
          )}
        </g>
      </Stage>

      {/* Left CodeCard: Real Host mrv245_host.rs */}
      <div style={rise(prog(t, T[0], 0.5))}>
        <CodeCard
          x={76}
          y={144}
          w={858}
          h={356}
          title="mrv245_host.rs · 主机发包热循环"
          badge="x86_64 · Socket 1"
          accent={P.host}
          fontSize={17.5}
          lines={[
            { text: "for _ in 0..chunk {" },
            { text: "  let s = sent + 1;" },
            {
              text: "  let off = (((s - 1) as usize) & ring_mask) * 128;",
              hl: t >= T[2] && t < T[3] ? "gold" : t >= T[1] ? "host" : undefined,
              note: t >= T[2] ? "避 128B 伪共享" : undefined,
            },
            { text: "  let cell = make_fast_cell(s, 1); // 头尾双 seq" },
            {
              text: "  unsafe { movdir64b(bar_lane_ptr.add(off), &cell) };",
              hl: t >= T[1] && t < T[3] ? "host" : undefined,
              note: t >= T[1] ? "64B 原子直推" : undefined,
            },
            { text: "  sent = s; // 零门铃 · 零 SFENCE" },
            { text: "}" },
          ]}
        />
      </div>

      {/* Right CodeCard: Real Card mrv245_card.rs */}
      <div style={rise(kCard)}>
        <CodeCard
          x={968}
          y={144}
          w={876}
          h={356}
          title="mrv245_card.rs · 卡核无锁跨步轮询"
          badge="CN106 ARM64 · K 核"
          accent={P.nc}
          fontSize={17.5}
          lines={[
            { text: "let k_per_lane = cpl / lanes;   // K / H" },
            { text: "let lane = w / k_per_lane;      // 所属 Lane" },
            { text: "let lane_w = w % k_per_lane;    // Lane 内编号" },
            {
              text: "let stride = k_per_lane as u64; // 跨步步长",
              hl: t >= T[3] && t < T[5] ? "nc" : undefined,
              note: t >= T[4] ? "槽位不交叠" : undefined,
            },
            {
              text: "let mut next: u64 = lane_w as u64 + 1;",
              hl: t >= T[3] && t < T[5] ? "nc" : undefined,
            },
            {
              text: "if words[0] == next && words[7] == next {",
              hl: t >= T[5] ? "gold" : undefined,
              note: t >= T[5] ? "头尾双序号校验" : undefined,
            },
            { text: "  consume_and_reply(words); next += stride; }" },
          ]}
        />
      </div>

      {/* Teaser tags at cues 0 and 5 */}
      <Tag x={76} y={766} color={P.gold} opacity={win(t, T[0], T[1])}>
        零门铃 · 零 SFENCE · 64 B 原子直推 → 为什么会先后撞上两道硬件墙？
      </Tag>
      <Tag x={1230} y={766} color={P.bad} solid opacity={kVerify}>
        实测默认 Normal-WB 闭环锁死：13.53 M/s
      </Tag>

      {/* Extra explanatory callout on cue 2 */}
      <Text
        x={108}
        y={766}
        w={980}
        size={19}
        color={P.gold}
        weight={600}
        opacity={win(t, T[2], T[4])}
      >
        每格 128 B 步长（64 B 有效载荷 + 64 B 隔离），避免 CN106 128 B 缓存行伪共享
      </Text>
    </>
  );
}

export const B01: SceneDef = {
  id: "B01",
  title: "真实轮询代码怎么写：零锁分片与跨步扫描",
  stage: 0,
  source: "mrv245_host.rs:765-776 · mrv245_card.rs:962-1037",
  dur: DUR,
  cues,
  C: Body,
};
