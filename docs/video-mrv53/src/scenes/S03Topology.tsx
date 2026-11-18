import React from "react";
import {
  appear,
  C,
  Card,
  CORE,
  Cue,
  fade,
  FONT,
  K,
  Link,
  MONO,
  Note,
  PacketStream,
  prog,
  Pt,
  SceneDef,
  Stage,
  useRealT,
  useT,
} from "../lib";
import { Datapath, dpGeom, routes } from "./pcie";

// <cues>
const cues: Cue[] = [
  [0.60, 11.28, "实验平台：主机 `saturn` 绑定 `lcore 40`（锁定 **`3.90 GHz`**，uncore **`2.20 GHz`**），经 **PCIe Gen5 x4** 连接 CN106xx 卡（**`2.00 GHz`**）。", "实验平台上，主机绑定四十号核，主频锁定三点九吉赫兹，uncore 二点二吉赫兹，经 P C I e Gen 五 乘四连接两吉赫兹的 D P U 卡。"], // 0
  [11.63, 23.71, "去程统一用 **`MOVDIR64B`** 直写 PF **BAR4 窗口 15** 的卡 DRAM 请求环；回程对比**写合并 `WC`**、**直写 `ST`** 与 **DPI DMA**（各配 **1 核 / 4 核**）。", "去程统一用 M O V D I R 六十四 B 直写 B A R 四 请求环；回程对比写合并 W C、保序直写 S T 与 D P I D M A，各配一核或四核。"], // 1
  [24.06, 35.14, "在闭环窗口 `W = 64` 与 `W = 1024` 下，6 种配置自然分化成**三档吞吐状态**：第一档是 **`33.0–50.5 M/s` 快状态**（`WC1 / DPI1`，间隔 **`19.8–30.3 ns`**）。", "在窗口六十四与一千零二十四下，六种配置分化为三档状态：第一档是三十三到五十点五兆每秒的快状态，每条间隔十九点八到三十点三纳秒。"], // 2
  [35.49, 44.05, "第二档是三个 **`~13 M/s` 慢状态**（`W=64` 下的 `WC4 13.1`、`ST4 13.6`、`DPI4 13.0 M/s`，每条间隔 **`73.5–76.9 ns`**，外加 `WC1` 起步态 `13.7 M/s`）。", "第二档是三个约十三兆每秒的慢状态，出现在六十四窗口下的四核配置，每条间隔七十三点五到七十六点九纳秒。"], // 3
  [44.40, 53.62, "第三档是卡 4 核 `W=1024` 的 **`22.6–25.5 M/s` 中间态**（`WC4 44.3 ns`、`ST4 39.3 ns`、`DPI4 44.0 ns`），往返延迟在快慢两相间摆动。", "第三档是卡四核一千零二十四窗口的中间态，吞吐二十二点六到二十五点五兆每秒，间隔三十九点三到四十四点三纳秒。"], // 4
];
const DUR = 55.02; // <dur>
// </cues>

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  // Left Datapath geometry: s=0.58, dx=24, dy=40 -> x: 58.8..1102.8, y: 127..533
  const g = dpGeom(0.58, 24, 40);
  const rtMap = routes(g);
  // Offset ST route slightly above WC route so both PEM0 outbound store paths remain distinct
  const stRoute: Pt[] = rtMap.storeReply.map(([x, y], idx, arr) =>
    idx === 0 || idx === arr.length - 1 ? [x, y - 5] : [x - 5, y - 7],
  );

  const dpIn = prog(t, T[0], 0.7);
  const kInline = prog(t, T[1], 0.65);
  const kWc = prog(t, T[1] + 0.7, 0.65);
  const kSt = prog(t, T[1] + 1.4, 0.65);
  const kDpi = prog(t, T[1] + 2.1, 0.65);

  // Right panel crossfade: Cue 0-1 hardware/matrix overview fades out before T[2]
  const opRightIntro = fade(t, T[0], T[2] - 0.35, 0.35);
  // Bottom-left Cue 0 hardware detail fades out before T[1] path cards appear
  const opLeftCue0 = fade(t, T[0] + 0.25, T[1] - 0.35, 0.35);

  const hlBlocks: Record<string, string> = {
    core: K.host,
    cores: K.card,
  };
  if (t >= T[1]) {
    hlBlocks.reqRing = K.inline;
    hlBlocks.replyRing = K.rep;
    hlBlocks.pem = K.wc;
    hlBlocks.dpi = K.dpi;
  }

  const pathCards: {
    pill: string;
    title: string;
    flag: string;
    desc: string;
    color: string;
    at: number;
  }[] = [
    {
      pill: "去程",
      title: "MOVDIR64B 直写",
      flag: "BAR4 窗口 15",
      desc: "直写卡 DRAM 请求环 reqRing",
      color: K.inline,
      at: T[1],
    },
    {
      pill: "回程 ①",
      title: "写合并 WC",
      flag: "--store-wc",
      desc: "/dev/pem_wc 区域 248–255 直写",
      color: K.wc,
      at: T[1] + 0.6,
    },
    {
      pill: "回程 ②",
      title: "保序直写 ST",
      flag: "--store",
      desc: "PEM0 outbound 保序写回主机",
      color: K.st,
      at: T[1] + 1.2,
    },
    {
      pill: "回程 ③",
      title: "DPI DMA",
      flag: "--dma",
      desc: "VF 06:00.1–.4 引擎写回主机",
      color: K.dpi,
      at: T[1] + 1.8,
    },
  ];

  return (
    <>
      <Stage>
        <Datapath
          g={g}
          opacity={dpIn}
          show={{ sdp: 0.14, sdpRing: 0.14, ctrl: 0.14 }}
          hl={hlBlocks}
          cores={4}
          activeCores={4}
          coreColors={CORE}
          linkGlow={t >= T[1] ? 0.85 : 0.35}
          labels={{
            host: ["主机 · saturn", "uncore 2.20 GHz"],
            core: ["主机 CPU 核", "lcore 40 @ 3.90 GHz"],
            hmem: ["主机内存", "DPDK 大页 · 64 B · batch=1"],
            replyRing: ["主机回复环", "64 B 单元 · 1/4 子环"],
            link: ["PCIe Gen5 x4", "去程 BAR4 · 回程 WC/ST/DPI"],
            card: ["DPU 卡 · CN106xx", "Neoverse N2 @ 2.00 GHz"],
            cores: ["卡核 2GHz", "lcore 3/3-6"],
            reqRing: ["请求环 (BAR4)", "窗口 15 · MOVDIR64B"],
            dpi: ["DPI DMA", "VF 06:00.1–.4 · --dma"],
          }}
          cellFill={{
            reqRing: (i) => (t >= T[1] && i % 2 === 0 ? `${K.inline}66` : undefined),
            replyRing: (i) =>
              t >= T[1] + 0.7
                ? i % 3 === 0
                  ? `${K.wc}66`
                  : i % 3 === 1
                    ? `${K.st}66`
                    : `${K.dpi}66`
                : undefined,
          }}
        />

        {/* Internal polling helper links */}
        {kInline > 0 && (
          <>
            <Link pts={rtMap.cardPoll} k={kInline} color={K.card} w={3} dash="6 5" opacity={0.75} />
            <Link pts={rtMap.hostPollReply} k={kInline} color={K.mem} w={3} dash="6 5" opacity={0.75} />
          </>
        )}

        {/* Outbound: MOVDIR64B -> PF BAR4 window 15 reqRing */}
        {kInline > 0 && (
          <>
            <Link pts={rtMap.inline} k={kInline} color={K.inline} w={5} glow />
            <PacketStream
              pts={rtMap.inline}
              travel={rt * 210}
              gap={72}
              size={11}
              color={K.inline}
              opacity={kInline}
            />
          </>
        )}

        {/* Return 1: WC (--store-wc via /dev/pem_wc region 248-255) */}
        {kWc > 0 && (
          <>
            <Link pts={rtMap.storeReply} k={kWc} color={K.wc} w={4.5} glow />
            <PacketStream
              pts={rtMap.storeReply}
              travel={rt * 195}
              gap={84}
              size={10}
              color={K.wc}
              opacity={kWc}
            />
          </>
        )}

        {/* Return 2: ST (--store via PEM0 outbound Device-nGnRnE) */}
        {kSt > 0 && (
          <>
            <Link pts={stRoute} k={kSt} color={K.st} w={4} dash="10 6" />
            <PacketStream
              pts={stRoute}
              travel={rt * 165 + 40}
              gap={92}
              size={10}
              color={K.st}
              opacity={kSt}
            />
          </>
        )}

        {/* Return 3: DPI DMA (--dma via coreToDpi + dmaReply) */}
        {kDpi > 0 && (
          <>
            <Link pts={rtMap.coreToDpi} k={kDpi} color={K.dpi} w={4} />
            <Link pts={rtMap.dmaReply} k={kDpi} color={K.dpi} w={4.5} glow />
            <PacketStream
              pts={rtMap.dmaReply}
              travel={rt * 180 + 20}
              gap={88}
              size={10}
              color={K.dpi}
              opacity={kDpi}
            />
          </>
        )}
      </Stage>

      {/* Left Row A (x: 60..1100, y: 544..626): Persistent Hardware & Closed-Loop Spec Strip */}
      <div
        style={{
          position: "absolute",
          left: 60,
          top: 544,
          width: 1040,
          height: 82,
          display: "grid",
          gridTemplateColumns: "1.08fr 0.94fr 0.98fr",
          gap: 10,
          ...appear(t, T[0] + 0.15),
        }}
      >
        <div
          style={{
            minWidth: 0,
            background: "rgba(20,27,46,0.92)",
            border: `2px solid ${K.host}`,
            borderRadius: 12,
            padding: "8px 14px",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span
              style={{
                fontFamily: FONT,
                fontSize: 20,
                fontWeight: 700,
                color: K.host,
                background: `${K.host}1f`,
                borderRadius: 6,
                padding: "1px 8px",
              }}
            >
              主机 saturn
            </span>
            <span style={{ fontFamily: MONO, fontSize: 20, color: C.muted }}>uncore 2.20 GHz</span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 24, fontWeight: 800, color: C.text }}>
            lcore 40 @ 3.90 GHz
          </div>
        </div>

        <div
          style={{
            minWidth: 0,
            background: "rgba(20,27,46,0.92)",
            border: `2px solid ${K.link}`,
            borderRadius: 12,
            padding: "8px 14px",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span
              style={{
                fontFamily: FONT,
                fontSize: 20,
                fontWeight: 700,
                color: K.link,
                background: `${K.link}1f`,
                borderRadius: 6,
                padding: "1px 8px",
              }}
            >
              PCIe 链路与载荷
            </span>
            <span style={{ fontFamily: MONO, fontSize: 20, color: C.muted }}>batch = 1</span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 24, fontWeight: 800, color: C.text }}>
            Gen5 x4 · 64 B
          </div>
        </div>

        <div
          style={{
            minWidth: 0,
            background: "rgba(20,27,46,0.92)",
            border: `2px solid ${K.card}`,
            borderRadius: 12,
            padding: "8px 14px",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span
              style={{
                fontFamily: FONT,
                fontSize: 20,
                fontWeight: 700,
                color: K.card,
                background: `${K.card}1f`,
                borderRadius: 6,
                padding: "1px 8px",
              }}
            >
              DPU 卡 CN106xx
            </span>
            <span style={{ fontFamily: MONO, fontSize: 20, color: C.muted }}>1核(3)/4核(3–6)</span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 24, fontWeight: 800, color: C.text }}>
            Arm N2 @ 2.00 GHz
          </div>
        </div>
      </div>

      {/* Left Row B during Cue 0 (x: 60..1100, y: 636..846): Hardware Lock & Testbed Baseline Callout */}
      {opLeftCue0 > 0.001 && (
        <Card
          x={60}
          y={636}
          w={1040}
          h={210}
          border={C.line}
          style={{ opacity: opLeftCue0, padding: "18px 24px" }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
            <div style={{ fontFamily: FONT, fontSize: 25, fontWeight: 800, color: C.cyan }}>
              直接通路（绕过 SDP 包引擎）：全锁频零丢包闭环基准
            </div>
            <div
              style={{
                fontFamily: MONO,
                fontSize: 20,
                fontWeight: 700,
                color: K.ok,
                background: `${K.ok}1f`,
                border: `1.5px solid ${K.ok}`,
                borderRadius: 8,
                padding: "4px 12px",
                whiteSpace: "nowrap",
              }}
            >
              816 runs · lost = bad = torn = 0
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18, fontFamily: FONT, fontSize: 24, color: C.text, lineHeight: 1.4 }}>
            <div>
              • 主机侧：<span style={{ fontFamily: MONO, color: C.yellow }}>performance</span> 锁频{" "}
              <span style={{ fontFamily: MONO, fontWeight: 700 }}>3.90 GHz</span>，双路 uncore 锁定{" "}
              <span style={{ fontFamily: MONO, fontWeight: 700 }}>2.20 GHz</span>
            </div>
            <div>
              • 卡侧：Neoverse N2 核固定{" "}
              <span style={{ fontFamily: MONO, fontWeight: 700 }}>2.00 GHz</span>，4 核按{" "}
              <span style={{ fontFamily: MONO, color: C.cyan }}>(seq-1) % n == i</span> 分子环
            </div>
          </div>
        </Card>
      )}

      {/* Left Row B from Cue 1 onwards (x: 60..1100, y: 636..846): 2x2 Grid for Outbound + 3 Return Paths */}
      {t >= T[1] && (
        <div
          style={{
            position: "absolute",
            left: 60,
            top: 636,
            width: 1040,
            height: 210,
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gridTemplateRows: "1fr 1fr",
            gap: 10,
          }}
        >
          {pathCards.map((c, idx) => (
            <div
              key={idx}
              style={{
                minWidth: 0,
                background: "rgba(20,27,46,0.94)",
                border: `2px solid ${c.color}`,
                borderRadius: 14,
                padding: "10px 16px",
                boxShadow: `0 6px 20px rgba(0,0,0,0.32), inset 0 0 14px ${c.color}16`,
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                whiteSpace: "nowrap",
                ...appear(t, c.at),
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span
                    style={{
                      fontFamily: FONT,
                      fontSize: 20,
                      fontWeight: 700,
                      color: c.color,
                      background: `${c.color}1f`,
                      borderRadius: 6,
                      padding: "2px 8px",
                    }}
                  >
                    {c.pill}
                  </span>
                  <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.text }}>
                    {c.title}
                  </span>
                </div>
                <span
                  style={{
                    fontFamily: MONO,
                    fontSize: 20,
                    fontWeight: 700,
                    color: c.color,
                    background: "rgba(11,16,32,0.7)",
                    border: `1px solid ${c.color}`,
                    borderRadius: 6,
                    padding: "2px 8px",
                  }}
                >
                  {c.flag}
                </span>
              </div>
              <div style={{ fontFamily: FONT, fontSize: 24, color: C.text }}>{c.desc}</div>
            </div>
          ))}
        </div>
      )}

      {/* Right Panel during Cue 0 & Cue 1 (x: 1180..1860, y: 132..846): Hardware & 6-Config Matrix Intro */}
      {opRightIntro > 0.001 && (
        <Card
          x={1180}
          y={132}
          w={680}
          h={714}
          border={C.line}
          title="实验参数与 6 种直接通路配置矩阵"
          titleColor={C.cyan}
          style={{ opacity: opRightIntro, padding: "18px 20px" }}
        >
          {/* Top Big Hardware Metrics */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr 1fr",
              gap: 12,
              marginBottom: 14,
            }}
          >
            <div
              style={{
                background: "rgba(11,16,32,0.75)",
                border: `1.5px solid ${K.host}`,
                borderRadius: 12,
                padding: "10px 8px",
                textAlign: "center",
              }}
            >
              <div style={{ fontFamily: MONO, fontSize: 44, fontWeight: 800, color: K.host, lineHeight: 1.05 }}>
                3.90
              </div>
              <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 700, color: C.text, marginTop: 4, whiteSpace: "nowrap" }}>
                主机 GHz
              </div>
            </div>
            <div
              style={{
                background: "rgba(11,16,32,0.75)",
                border: `1.5px solid ${K.card}`,
                borderRadius: 12,
                padding: "10px 8px",
                textAlign: "center",
              }}
            >
              <div style={{ fontFamily: MONO, fontSize: 44, fontWeight: 800, color: K.card, lineHeight: 1.05 }}>
                2.00
              </div>
              <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 700, color: C.text, marginTop: 4, whiteSpace: "nowrap" }}>
                卡核 GHz
              </div>
            </div>
            <div
              style={{
                background: "rgba(11,16,32,0.75)",
                border: `1.5px solid ${K.link}`,
                borderRadius: 12,
                padding: "10px 8px",
                textAlign: "center",
              }}
            >
              <div style={{ fontFamily: MONO, fontSize: 44, fontWeight: 800, color: K.link, lineHeight: 1.05 }}>
                64 B
              </div>
              <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 700, color: C.text, marginTop: 4, whiteSpace: "nowrap" }}>
                batch = 1
              </div>
            </div>
          </div>

          {/* Hardware details list */}
          <div
            style={{
              background: "rgba(11,16,32,0.55)",
              border: `1px solid ${C.line}`,
              borderRadius: 12,
              padding: "12px 16px",
              marginBottom: 14,
              display: "flex",
              flexDirection: "column",
              gap: 6,
              fontFamily: FONT,
              fontSize: 24,
              color: C.text,
            }}
          >
            <div>
              • <strong style={{ color: K.host }}>主机 saturn</strong>：lcore 40 · 双路 uncore{" "}
              <span style={{ fontFamily: MONO, color: C.yellow }}>2.20 GHz</span>
            </div>
            <div>
              • <strong style={{ color: K.link }}>PCIe Gen5 x4</strong>：DPU 卡 1核(lcore 3) 或 4核(3–6)
            </div>
            <div>
              • <strong style={{ color: K.card }}>闭环窗口</strong>：对比{" "}
              <span style={{ fontFamily: MONO, color: C.cyan }}>W = 64</span> 与{" "}
              <span style={{ fontFamily: MONO, color: C.cyan }}>W = 1024</span>（及 <span style={{ fontFamily: MONO }}>W = 1</span>）
            </div>
          </div>

          {/* 3 Return x 2 Core Count Matrix */}
          <div style={{ opacity: prog(t, T[1], 0.6) }}>
            <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 700, color: C.text, marginBottom: 8 }}>
              3 种回程机制 × 卡核数（1 核 vs 4 核）= 6 种配置：
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {[
                {
                  name: "① 写合并 WC",
                  c1: "WC1 (1核)",
                  c4: "WC4 (4核)",
                  sub: "/dev/pem_wc",
                  col: K.wc,
                },
                {
                  name: "② 保序直写 ST",
                  c1: "ST1 (1核)",
                  c4: "ST4 (4核)",
                  sub: "PEM0 outbound",
                  col: K.st,
                },
                {
                  name: "③ DPI DMA",
                  c1: "DPI1 (1核)",
                  c4: "DPI4 (4核)",
                  sub: "VF 06:00.1–.4",
                  col: K.dpi,
                },
              ].map((row, i) => (
                <div
                  key={i}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1.2fr 0.85fr 0.85fr",
                    gap: 8,
                    alignItems: "center",
                    background: `${row.col}12`,
                    border: `1.5px solid ${row.col}`,
                    borderRadius: 10,
                    padding: "10px 12px",
                    fontFamily: FONT,
                    fontSize: 24,
                    whiteSpace: "nowrap",
                  }}
                >
                  <div>
                    <span style={{ fontWeight: 800, color: row.col }}>{row.name}</span>{" "}
                    <span style={{ fontFamily: MONO, fontSize: 20, color: C.muted }}>{row.sub}</span>
                  </div>
                  <div style={{ fontFamily: MONO, fontWeight: 700, color: C.text, textAlign: "center" }}>{row.c1}</div>
                  <div style={{ fontFamily: MONO, fontWeight: 700, color: C.text, textAlign: "center" }}>{row.c4}</div>
                </div>
              ))}
            </div>
          </div>
        </Card>
      )}

      {/* Right Panel from Cue 2 onwards (x: 1180..1860, y: 126..848): Three Throughput Regimes */}
      {t >= T[2] && (
        <>
          {/* Tier 1: Fast State (K.fast green, Cue 2, x: 1180..1860, y: 126..328) */}
          <Card
            x={1180}
            y={126}
            w={680}
            h={202}
            border={K.fast}
            style={{ ...appear(t, T[2]), padding: "10px 16px" }}
          >
            {/* Header Row with >= 44px Key Number */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 6,
                whiteSpace: "nowrap",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontFamily: FONT, fontSize: 25, fontWeight: 800, color: K.fast, lineHeight: 1.1 }}>
                  第一档：快状态
                </span>
                <span
                  style={{
                    fontFamily: MONO,
                    fontSize: 20,
                    fontWeight: 700,
                    color: K.fast,
                    background: `${K.fast}1f`,
                    borderRadius: 6,
                    padding: "2px 8px",
                  }}
                >
                  19.8–30.3 ns/条
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                <span style={{ fontFamily: MONO, fontSize: 44, fontWeight: 800, color: K.fast, lineHeight: 1 }}>
                  50.5
                </span>
                <span style={{ fontFamily: MONO, fontSize: 20, fontWeight: 700, color: C.muted }}>M/s</span>
              </div>
            </div>

            {/* 3 Data Rows at >= 24px */}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  background: "rgba(11,16,32,0.65)",
                  borderRadius: 8,
                  padding: "4px 12px",
                  lineHeight: 1.2,
                  borderLeft: `4px solid ${K.wc}`,
                  whiteSpace: "nowrap",
                }}
              >
                <span style={{ fontFamily: MONO, fontSize: 24, fontWeight: 700, color: K.wc }}>WC1 W=1024</span>
                <span style={{ fontFamily: MONO, fontSize: 24, color: C.text }}>
                  <strong style={{ color: K.fast }}>40.3–50.5 M/s</strong>{" "}
                  <span style={{ fontSize: 20, color: C.muted }}>(H0 24.8/H2 19.8ns)</span>
                </span>
              </div>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  background: "rgba(11,16,32,0.65)",
                  borderRadius: 8,
                  padding: "4px 12px",
                  lineHeight: 1.2,
                  borderLeft: `4px solid ${K.dpi}`,
                  whiteSpace: "nowrap",
                }}
              >
                <span style={{ fontFamily: MONO, fontSize: 24, fontWeight: 700, color: K.dpi }}>DPI1 W=1024</span>
                <span style={{ fontFamily: MONO, fontSize: 24, color: C.text }}>
                  <strong style={{ color: K.fast }}>44.5 M/s</strong>（22.5 ns/条）
                </span>
              </div>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  background: "rgba(11,16,32,0.65)",
                  borderRadius: 8,
                  padding: "4px 12px",
                  lineHeight: 1.2,
                  borderLeft: `4px solid ${K.wc}`,
                  whiteSpace: "nowrap",
                }}
              >
                <span style={{ fontFamily: MONO, fontSize: 24, fontWeight: 700, color: K.wc }}>
                  WC1 W=64 <span style={{ fontFamily: FONT, fontSize: 20, color: C.muted }}>(稳态 ≥ 7 s)</span>
                </span>
                <span style={{ fontFamily: MONO, fontSize: 24, color: C.text }}>
                  <strong style={{ color: K.fast }}>33.0 M/s</strong>（30.3 ns/条）
                </span>
              </div>
            </div>
          </Card>

          {/* Tier 2: Slow State (~13 M/s, K.slow red, Cue 3, x: 1180..1860, y: 336..570) */}
          <Card
            x={1180}
            y={336}
            w={680}
            h={234}
            border={K.slow}
            style={{ ...appear(t, T[3]), padding: "10px 16px" }}
          >
            {/* Header Row with >= 44px Key Number */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 6,
                whiteSpace: "nowrap",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontFamily: FONT, fontSize: 25, fontWeight: 800, color: K.slow, lineHeight: 1.1 }}>
                  第二档：三个 ~13 M/s 慢状态
                </span>
                <span
                  style={{
                    fontFamily: MONO,
                    fontSize: 20,
                    fontWeight: 700,
                    color: K.slow,
                    background: `${K.slow}1f`,
                    borderRadius: 6,
                    padding: "2px 8px",
                  }}
                >
                  73.5–76.9 ns
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                <span style={{ fontFamily: MONO, fontSize: 44, fontWeight: 800, color: K.slow, lineHeight: 1 }}>
                  ~13
                </span>
                <span style={{ fontFamily: MONO, fontSize: 20, fontWeight: 700, color: C.muted }}>M/s</span>
              </div>
            </div>

            {/* 3 Data Rows at >= 24px + Note Pill */}
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {[
                { cfg: "WC4 W=64", mps: "13.1 M/s", ns: "76.1 ns/条", rtt: "RTT 3.56 µs", col: K.wc },
                { cfg: "ST4 W=64", mps: "13.6 M/s", ns: "73.5 ns/条", rtt: "RTT 3.35 µs", col: K.st },
                { cfg: "DPI4 W=64", mps: "13.0 M/s", ns: "76.9 ns/条", rtt: "RTT 4.39 µs", col: K.dpi },
              ].map((r, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    background: "rgba(11,16,32,0.65)",
                    borderRadius: 8,
                    padding: "4px 12px",
                    lineHeight: 1.2,
                    borderLeft: `4px solid ${r.col}`,
                    whiteSpace: "nowrap",
                  }}
                >
                  <span style={{ fontFamily: MONO, fontSize: 24, fontWeight: 700, color: r.col }}>{r.cfg}</span>
                  <span style={{ fontFamily: MONO, fontSize: 24, color: C.text }}>
                    <strong style={{ color: K.slow }}>{r.mps}</strong>（{r.ns} ·{" "}
                    <span style={{ fontSize: 20, color: C.muted }}>{r.rtt}</span>）
                  </span>
                </div>
              ))}
              <div
                style={{
                  fontFamily: FONT,
                  fontSize: 20,
                  color: C.yellow,
                  background: "rgba(249,199,79,0.12)",
                  borderRadius: 6,
                  padding: "3px 12px",
                  lineHeight: 1.2,
                  whiteSpace: "nowrap",
                }}
              >
                附注：<span style={{ fontFamily: MONO, fontWeight: 700 }}>WC1 W=64</span>（&lt; 7 s 起步态）同样为{" "}
                <span style={{ fontFamily: MONO, fontWeight: 700 }}>13.7 M/s / 73.0 ns</span>
              </div>
            </div>
          </Card>

          {/* Tier 3: Intermediate / Bimodal Mix (K.bimodal yellow, Cue 4, x: 1180..1860, y: 578..778) */}
          <Card
            x={1180}
            y={578}
            w={680}
            h={200}
            border={K.bimodal}
            style={{ ...appear(t, T[4]), padding: "10px 16px" }}
          >
            {/* Header Row with >= 44px Key Number */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 6,
                whiteSpace: "nowrap",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontFamily: FONT, fontSize: 25, fontWeight: 800, color: K.bimodal, lineHeight: 1.1 }}>
                  第三档：4 核 W=1024 双态
                </span>
                <span
                  style={{
                    fontFamily: MONO,
                    fontSize: 20,
                    fontWeight: 700,
                    color: K.bimodal,
                    background: `${K.bimodal}1f`,
                    borderRadius: 6,
                    padding: "2px 8px",
                  }}
                >
                  39.3–44.3 ns
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                <span style={{ fontFamily: MONO, fontSize: 44, fontWeight: 800, color: K.bimodal, lineHeight: 1 }}>
                  ~23
                </span>
                <span style={{ fontFamily: MONO, fontSize: 20, fontWeight: 700, color: C.muted }}>M/s</span>
              </div>
            </div>

            {/* 3 Data Rows at >= 24px */}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {[
                { cfg: "WC4 W=1024", mps: "22.6 M/s", ns: "44.3 ns/条", col: K.wc },
                { cfg: "ST4 W=1024", mps: "25.5 M/s", ns: "39.3 ns/条", col: K.st },
                { cfg: "DPI4 W=1024", mps: "22.7 M/s", ns: "44.0 ns/条", col: K.dpi },
              ].map((r, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    background: "rgba(11,16,32,0.65)",
                    borderRadius: 8,
                    padding: "4px 12px",
                    lineHeight: 1.2,
                    borderLeft: `4px solid ${r.col}`,
                    whiteSpace: "nowrap",
                  }}
                >
                  <span style={{ fontFamily: MONO, fontSize: 24, fontWeight: 700, color: r.col }}>{r.cfg}</span>
                  <span style={{ fontFamily: MONO, fontSize: 24, color: C.text }}>
                    <strong style={{ color: K.bimodal }}>{r.mps}</strong>（{r.ns}）
                  </span>
                </div>
              ))}
            </div>
          </Card>

          {/* Bottom Question Banner (Cue 4, x: 1180..1860, y: 786..844) */}
          <div
            style={{
              position: "absolute",
              left: 1180,
              top: 786,
              width: 680,
              height: 58,
              background: "linear-gradient(90deg, rgba(249,199,79,0.20), rgba(76,201,240,0.16))",
              border: `2px solid ${C.yellow}`,
              borderRadius: 12,
              padding: "0 14px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              whiteSpace: "nowrap",
              ...appear(t, T[4] + 0.45),
            }}
          >
            <span style={{ fontFamily: FONT, fontSize: 24, fontWeight: 800, color: C.yellow }}>
              核心悬念：同样是直接通路，为何吞吐横跨{" "}
              <span style={{ fontFamily: MONO, color: K.slow }}>13</span> /{" "}
              <span style={{ fontFamily: MONO, color: K.bimodal }}>23</span> /{" "}
              <span style={{ fontFamily: MONO, color: K.fast }}>50 M/s</span>？
            </span>
          </div>
        </>
      )}

      <Note>按 FACTS.md §1–§2 实测数据绘制（runs.jsonl, 64 B, batch=1, lost=bad=torn=0）</Note>
    </>
  );
}

export const S03: SceneDef = {
  id: "S03",
  title: "直接通路拓扑与三档吞吐状态",
  stage: 0,
  source: "FACTS §1 · 64 B · batch=1 · lcore 40 @ 3.9 GHz",
  dur: DUR,
  cues,
  C: Body,
};
