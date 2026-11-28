import React from "react";
import { Cue, SceneDef, prog, useRealT, useT } from "../lib";
import { ArchBox, Flow, Glass, P, PacketCell, Stage, Tag, Text, ZH_FONT, alpha, rise } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.45, 10.57, "既想保留免门铃、免 `SFENCE` 的 `904 ns` 极致低延迟，又要彻底甩掉 `L1D / L2` 嗅探，解法藏在**标准 Linux 页表**里。", "如果既想免门铃、免 S fence，保住九百零四纳秒的超低延迟，又要彻底甩掉 L 一和 L 二缓存嗅探，答案就在标准 Linux 页表属性里。"], // 0
  [10.77, 21.13, "零修改内核 `MAIR_EL1`，直接用标准 `pgprot_writecombine` 映射接收环——也就是 ARM64 的 **`MT_NORMAL_NC (0x44)`**。", "完全不用魔改 M A I R E L 一寄存器，直接用内核自带的写合并页表属性，也就是 ARM 六十四标准的 M T Normal N C，编码零 x 四四。"], // 1
  [21.33, 31.32, "卡核发起 `ldp q` 读取时，数据**直接绕过私有 `L1D` 与 `L2`** 落进向量寄存器，硬件永远不在核内留副本。", "在 M T Normal N C 下，卡核发出 l d p q 指令时，数据直接绕过核私有的 L 一 D 和 L 二，落进向量寄存器，绝不在核心里留副本。"], // 2
  [31.52, 42.27, "关键在于：它虽绕过 `L1 / L2`，在 `CN106` 上依然稳稳命中片上 **48 MiB `TAD` 共享 L3**，实测 **`DDR5 读 = 0.00`**。", "最妙的是，它虽然绕过了 L 一和 L 二，但在 C N 一零六上依然稳稳命中片上四十八兆 T A D 共享缓存，实测 D D R 五内存读次数完全为零。"], // 3
  [42.47, 51.14, "PMU 铁证如山：`l1d_refill` 从 `0.99` 归 **`0.00`**，`0x58` L2 作废从 `1.96` 归 **`0.00`**，第一道嗅探墙彻底消失。", "硬件计数器铁证如山：L 一重填归零，零 x 五八作废彻底归零，第一道缓存嗅探墙瞬间烟消云散。"], // 4
  [51.34, 62.14, "免门铃单向吞吐原地直飙 **`159.9–174.1 M/s`**，空载 `W = 1` 仍稳守 **`904.5 ns`**——可一进闭环，第二道墙冒了出来。", "于是免门铃单向吞吐直接飙到一百六十到一百七十四百万包每秒，空载延迟稳守九百零四点五纳秒。可一进闭环，第二道墙却突然冒了出来。"], // 5
];
const DUR = 63.14; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  const kPte = prog(t, T[1], 0.5);
  const kBypass = prog(t, T[2], 0.6);
  const kTad = prog(t, T[3], 0.55);
  const kPmu = prog(t, T[4], 0.55);
  const kPerf = prog(t, T[5], 0.55);

  const pHost = (rt * 0.85) % 1;
  const pBypass = (rt * 0.95 + 0.3) % 1;

  // Bypass polyline from 48 MiB TAD curving over L1D/L2 straight into Card Core NEON registers
  const bypassPts: [number, number][] = [
    [930, 316],
    [1010, 232],
    [1365, 232],
    [1448, 316],
  ];

  return (
    <>
      <Stage>
        {/* Main CN106 Memory Hierarchy Card */}
        <rect
          x={76}
          y={152}
          width={1768}
          height={464}
          rx={18}
          fill="rgba(12, 20, 38, 0.74)"
          stroke={alpha(P.nc, 0.36)}
          strokeWidth={1.8}
        />

        {/* Host Xeon */}
        <ArchBox x={118} y={266} w={236} h={162} title="主机 Xeon" sub="MOVDIR64B → BAR4" color={P.host} />

        {/* PCIe Gen5 x4 / PEM0 */}
        <Flow pts={[[354, 347], [608, 347]]} color={P.host} w={4.5} glow />
        <PacketCell pts={[[354, 347], [608, 347]]} k={pHost} color={P.host} w={48} h={24} label="64B" />
        <text x={481} y={322} textAnchor="middle" fontFamily={ZH_FONT} fontSize={17} fontWeight={700} fill={P.host}>
          PCIe Gen5 x4 · PEM0 入向写
        </text>
        <text x={481} y={380} textAnchor="middle" fontFamily={ZH_FONT} fontSize={15} fill={P.sub}>
          零门铃 · 零 SFENCE
        </text>

        {/* 48 MiB TAD Shared L3 */}
        <ArchBox
          x={612}
          y={254}
          w={318}
          h={186}
          title="48 MiB TAD (片上 L3)"
          sub="CN106 共享末级缓存 · 100% 命中"
          color={P.tad}
          fill={alpha(P.tad, 0.16)}
        />

        {/* DDR5 below TAD (untouched!) */}
        <g opacity={kTad}>
          <line x1={771} y1={442} x2={771} y2={492} stroke={P.faint} strokeWidth={2.5} strokeDasharray="6 6" />
          <ArchBox
            x={646}
            y={494}
            w={250}
            h={92}
            title="片外 DDR5 内存"
            sub="DDR5 读 = 0.0000"
            color={P.faint}
            dashed
            titleSize={19}
          />
          <text x={916} y={546} fontFamily={ZH_FONT} fontSize={17} fontWeight={700} fill={P.nc}>
            ✓ 不落盘 DDR5，片上全命中
          </text>
        </g>

        {/* Card Private L1D / L2 (Dimmed / Bypassed!) */}
        <g opacity={kBypass > 0.1 ? 0.45 : 0.88}>
          <ArchBox
            x={1044}
            y={334}
            w={286}
            h={148}
            title="卡核私有 L1D / L2"
            sub={kBypass > 0.2 ? "已彻底绕过 · 0 缓存副本" : "Normal-WB 曾缓存于此"}
            color={P.faint}
            dashed={kBypass > 0.2}
          />
        </g>
        {kBypass > 0.25 && (
          <g opacity={kBypass}>
            <line x1={1074} y1={356} x2={1300} y2={460} stroke={alpha(P.nc, 0.55)} strokeWidth={3} strokeDasharray="8 6" />
            <text x={1187} y={518} textAnchor="middle" fontFamily={ZH_FONT} fontSize={17} fontWeight={700} fill={P.nc}>
              核内零副本 → 主机写再无 0x58 嗅探
            </text>
          </g>
        )}

        {/* Glowing MT_NORMAL_NC Bypass Arc over L1D/L2 */}
        <Flow pts={bypassPts} k={kBypass} color={P.nc} w={5.5} head={15} glow />
        {kBypass > 0.5 && (
          <PacketCell pts={bypassPts} k={pBypass} color={P.nc} w={56} h={24} label="ldp q" />
        )}
        <g opacity={kBypass}>
          <rect x={1072} y={182} width={232} height={36} rx={18} fill={alpha(P.nc, 0.2)} stroke={P.nc} strokeWidth={1.6} />
          <text x={1188} y={206} textAnchor="middle" fontFamily={ZH_FONT} fontSize={17} fontWeight={700} fill={P.nc}>
            MT_NORMAL_NC 直通读
          </text>
        </g>

        {/* Card Core NEON Registers */}
        <ArchBox
          x={1452}
          y={254}
          w={352}
          h={186}
          title="卡核向量寄存器 (q0..q3)"
          sub="2× ldp q 直读 64 B · 零缓存污染"
          color={P.nc}
          fill={alpha(P.nc, 0.14)}
        />
      </Stage>

      {/* Top badge inside panel */}
      <Tag x={116} y={176} color={P.nc} solid opacity={kPte}>
        标准 Linux pgprot_writecombine · MT_NORMAL_NC (0x44) · enable_custom_mair = 0（零改 MAIR_EL1）
      </Tag>

      {/* Bottom 4 verified metric cards */}
      <div style={{ position: "absolute", left: 76, top: 640, display: "flex", gap: 24 }}>
        <Glass x={0} y={0} w={424} h={238} kicker="PMU 核内重填 (l1d_refill)" color={P.nc} opacity={kPmu} style={rise(kPmu)}>
          <div style={{ fontSize: 54, fontWeight: 700, color: P.nc, marginTop: 14, fontVariantNumeric: "tabular-nums" }}>
            0.00 <span style={{ fontSize: 22, color: P.sub }}>/ 包</span>
          </div>
          <Text x={22} y={136} w={380} size={18} color={P.sub}>
            对比 Normal-WB 的 <span style={{ color: P.bad, fontWeight: 700 }}>0.99 / 包</span>，彻底告别核私有 L1D 污染
          </Text>
        </Glass>

        <Glass x={448} y={0} w={424} h={238} kicker="PMU L2 嗅探作废 (0x58)" color={P.nc} opacity={kPmu} style={rise(kPmu)}>
          <div style={{ fontSize: 54, fontWeight: 700, color: P.nc, marginTop: 14, fontVariantNumeric: "tabular-nums" }}>
            0.00 <span style={{ fontSize: 22, color: P.sub }}>/ 包</span>
          </div>
          <Text x={22} y={136} w={380} size={18} color={P.sub}>
            对比 Normal-WB 的 <span style={{ color: P.bad, fontWeight: 700 }}>1.96 / 包</span>，第一道硬件墙永久归零
          </Text>
        </Glass>

        <Glass x={896} y={0} w={424} h={238} kicker="免门铃单向直推吞吐" color={P.gold} opacity={kPerf} style={rise(kPerf)}>
          <div style={{ fontSize: 52, fontWeight: 700, color: P.gold, marginTop: 14, fontVariantNumeric: "tabular-nums" }}>
            159.9–174.1
          </div>
          <Text x={22} y={136} w={380} size={18} color={P.sub}>
            H=1 单核 <strong style={{ color: P.ink }}>159.9 M/s (CHA 16.9)</strong> · H=2 双核 <strong style={{ color: P.ink }}>174.1 M/s</strong>
          </Text>
        </Glass>

        <Glass x={1344} y={0} w={424} h={238} kicker="空载 W = 1 往返延迟" color={P.nc} opacity={kPerf} style={rise(kPerf)}>
          <div style={{ fontSize: 54, fontWeight: 700, color: P.nc, marginTop: 14, fontVariantNumeric: "tabular-nums" }}>
            904.5 <span style={{ fontSize: 22, color: P.sub }}>ns</span>
          </div>
          <Text x={22} y={136} w={380} size={18} color={P.sub}>
            p99 = 960.9 ns，免门铃免 SFENCE 零延迟税 —— 但闭环下另有隐情…
          </Text>
        </Glass>
      </div>
    </>
  );
}

export const B03: SceneDef = {
  id: "B03",
  title: "破局：标准 Linux MT_NORMAL_NC 直读 48 MiB TAD",
  stage: 2,
  source: "MRV-245/252 runs.jsonl · pgprot_writecombine (0x44)",
  dur: DUR,
  cues,
  C: Body,
};
