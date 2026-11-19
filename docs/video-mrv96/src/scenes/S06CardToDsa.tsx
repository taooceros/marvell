import React from "react";
import {
  Arrow,
  C,
  Card,
  Cue,
  FONT,
  K,
  Link,
  MONO,
  PacketStream,
  Pill,
  SceneDef,
  Stage,
  Txt,
  fade,
  pop,
  prog,
  useRealT,
  useT,
} from "../lib";
import { BarChart, Brace, KnobBar, Takeaway } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 14.86, "**New breakthrough:** Let the DPU drive the host's **Intel DSA** copy engine directly over bidirectional PCIe P2P — **zero host CPU** in the data path!", "Meanwhile, we achieved a major systems breakthrough: letting the D P U drive the host's Intel D S A copy engine directly over bidirectional P C I e peer-to-peer, with zero host C P U in the data path."], // 0
  [15.21, 30.45, "**Forward P2P (Card → DSA portal):** Card **DPI DMA** 64 B descriptor writes hit **0 drops across 182.9M+ verified ops** — whereas card **WC** portal writes suffer **0.048%–1.217%** silent drops.", "Going forward to the host D S A portal, card D P I D M A delivered zero drops across one hundred eighty-two million verified operations, whereas direct write-combining suffered silent drops up to one point two percent."], // 1
  [30.80, 52.14, "**Reverse P2P (DSA → Card BAR4):** Writing DSA's 32 B completion straight into **Card BAR4** (vs host RAM) cuts `W = 1` period: `NOP` **1.96 → 1.27 µs**, `4 KiB` **3.13 → 2.17 µs**, slashing jitter **1,119 → 163 ns** (`−85%`).", "Closing the reverse path into card B A R four cuts single-op round trip from one point nine six to one point two seven microseconds for no-ops, three point one three to two point one seven for four kilobyte copies, and slashes tail jitter eighty-five percent, from one thousand one hundred nineteen down to one hundred sixty-three nanoseconds."], // 2
  [52.49, 71.51, "**DSA Batch (B = 1 → 16):** Single-op BAR4 P2P hit **15.28 GB/s** (`~131 ns/op` drain bubble). Batching `B = 2, 4, 8, 16` (`4 KiB`, hot `512 KiB` src, `CC = 0`) jumps **2.05× to 31.32 GB/s**!", "Single-op B A R four completion initially capped at fifteen point two eight gigabytes per second due to a one hundred thirty-one nanosecond drain bubble. Turning one knob, D S A batch size from one to sixteen, eliminates that bubble and doubles throughput to thirty-one point three two gigabytes per second."], // 3
  [71.86, 91.04, "**Matches/beats host-RAM completion on BOTH axes:** At `B = 16`, Card BAR4 P2P delivers **31.32 vs 31.10 GB/s**, lower `p50` (**4.42 vs 4.63 µs**), and tighter `p99` (**4.61 vs 5.75 µs**) — zero host CPU!", "At batch sixteen, bidirectional card-to-D S A peer-to-peer matches or beats host-memory completion on both axes: thirty-one point three two versus thirty-one point one zero gigabytes per second, lower median latency, and tighter ninety-ninth percentile latency, with zero host C P U cycles."], // 4
];
const DUR = 92.44; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Clean non-overlapping crossfades between cues
  const k0 = fade(t, T[0], T[1] - 0.15, 0.35);
  const k1 = fade(t, T[1] + 0.25, T[2] - 0.15, 0.35);
  const k2 = fade(t, T[2] + 0.25, T[3] - 0.15, 0.35);
  const k3 = fade(t, T[3] + 0.25, T[4] - 0.15, 0.35);
  const k4 = prog(t, T[4] + 0.25, 0.45);

  // Top KnobBar (cues 1-4)
  const kBar = prog(t, T[1] + 0.15, 0.45);
  const activeStep = prog(t, T[2], 0.55) + prog(t, T[3], 0.55) + prog(t, T[4], 0.55);

  // Cue 0 internal timings
  const k0Fwd = prog(t, T[0] + 1.0, 0.7);
  const k0Ret = prog(t, T[0] + 2.4, 0.7);
  const k0Idle = prog(t, T[0] + 3.6, 0.6);
  const k0Take = prog(t, T[0] + 4.8, 0.6) * (1 - prog(t, T[1] - 0.15, 0.35));
  const bar4Pulse = 0.45 + 0.55 * Math.abs(Math.sin(rt * 4.2));
  const dsaPulse = 0.55 + 0.45 * Math.sin(rt * 3.5);

  // Cue 1 internal timings
  const k1Left = prog(t, T[1] + 0.45, 0.55);
  const k1Right = prog(t, T[1] + 1.15, 0.55);
  const k1Badge = prog(t, T[1] + 2.6, 0.5);

  // Cue 2 internal timings
  const k2Left = prog(t, T[2] + 0.45, 0.55);
  const k2Right = prog(t, T[2] + 1.4, 0.55);
  const k2JitterPop = pop(t, T[2] + 3.0, 0.55);

  // Cue 3 internal timings
  const k3Mech = prog(t, T[3] + 1.6, 0.6);
  const k3JumpPop = pop(t, T[3] + 2.8, 0.55);

  // Cue 4 internal timings
  const k4P1 = prog(t, T[4] + 0.45, 0.55);
  const k4P2 = prog(t, T[4] + 1.05, 0.55);
  const k4P3 = prog(t, T[4] + 1.65, 0.55);
  const k4Take = prog(t, T[4] + 2.5, 0.55);

  return (
    <>
      {/* ==================== BACKGROUND HTML CARDS (behind SVG Stage) ==================== */}

      {/* CUE 1: Forward P2P Cards */}
      {k1 > 0.005 && (
        <div style={{ opacity: k1 }}>
          {/* Left Card: WINNER Card DPI DMA */}
          <Card
            x={60}
            y={202}
            w={875}
            h={666}
            border={K.fast}
            style={{
              opacity: k1Left,
              transform: `translateY(${(1 - k1Left) * 18}px)`,
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              padding: "24px 32px",
            }}
          >
            <div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                <span
                  style={{
                    fontSize: 22,
                    fontWeight: 800,
                    color: C.bg,
                    background: K.fast,
                    borderRadius: 999,
                    padding: "5px 18px",
                    letterSpacing: 1,
                  }}
                >
                  WINNER · FORWARD P2P
                </span>
                <span style={{ fontSize: 22, fontFamily: MONO, color: K.fwd, fontWeight: 700 }}>
                  64 B atomic PCIe MWr
                </span>
              </div>
              <div style={{ fontSize: 32, fontWeight: 800, color: C.text, marginBottom: 8 }}>
                Card DPI DMA → dsa2/wq2.0
              </div>
              <div style={{ fontSize: 24, color: C.muted }}>
                Hardware DMA descriptor submit directly to host Intel DSA2 portal
              </div>
            </div>

            <div style={{ textAlign: "center", padding: "10px 0" }}>
              <div
                style={{
                  fontSize: 112,
                  fontWeight: 900,
                  color: K.fast,
                  lineHeight: 1.0,
                  letterSpacing: -2,
                  textShadow: `0 0 36px ${K.fast}44`,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                0 drops
              </div>
              <div style={{ fontSize: 34, fontWeight: 800, color: K.fast, marginTop: 8, fontVariantNumeric: "tabular-nums" }}>
                0.000% drop rate
              </div>
            </div>

            <div
              style={{
                background: "rgba(67,197,158,0.10)",
                border: `2px solid ${K.fast}88`,
                borderRadius: 14,
                padding: "16px 22px",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 32, fontWeight: 800, color: C.text, fontVariantNumeric: "tabular-nums" }}>
                182.9M+ verified ops
              </div>
              <div style={{ fontSize: 25, fontFamily: MONO, color: K.fast, marginTop: 6, fontWeight: 700 }}>
                lost = 0 · bad = 0 · mismatches = 0
              </div>
            </div>

            <div style={{ fontSize: 24, color: C.text, lineHeight: 1.35 }}>
              Atomic 64 B PCIe write accepted cleanly by{" "}
              <span style={{ fontFamily: MONO, color: K.fwd, fontWeight: 700 }}>dsa2/wq2.0</span> — zero host CPU forwarding.
            </div>
          </Card>

          {/* Right Card: FAILED Card Direct WC */}
          <Card
            x={985}
            y={202}
            w={875}
            h={666}
            border={K.slow}
            style={{
              opacity: k1Right,
              transform: `translateY(${(1 - k1Right) * 18}px)`,
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              padding: "24px 32px",
            }}
          >
            <div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                <span
                  style={{
                    fontSize: 22,
                    fontWeight: 800,
                    color: C.bg,
                    background: K.slow,
                    borderRadius: 999,
                    padding: "5px 18px",
                    letterSpacing: 1,
                  }}
                >
                  FAILED · SILENT PORTAL DROPS
                </span>
                <span style={{ fontSize: 22, fontFamily: MONO, color: K.slow, fontWeight: 700 }}>
                  Arm WC stores
                </span>
              </div>
              <div style={{ fontSize: 32, fontWeight: 800, color: C.text, marginBottom: 8 }}>
                Card Direct WC → dsa2/wq2.0
              </div>
              <div style={{ fontSize: 24, color: C.muted }}>
                Silent portal drop rate across fence modes (%)
              </div>
            </div>

            <div
              style={{
                opacity: k1Badge,
                background: "rgba(249,65,68,0.14)",
                border: `2px solid ${K.slow}`,
                borderRadius: 14,
                padding: "16px 14px",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 24, fontWeight: 800, color: K.slow, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                0.048%–1.217% silent drops → unusable for reliable doorbell!
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* CUE 2: Reverse P2P Cards */}
      {k2 > 0.005 && (
        <div style={{ opacity: k2 }}>
          {/* Left Card: W = 1 Closed-Loop Period */}
          <Card
            x={60}
            y={202}
            w={960}
            h={666}
            border={K.ret}
            style={{
              opacity: k2Left,
              padding: "22px 28px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ fontSize: 30, fontWeight: 800, color: C.text }}>
                W = 1 Closed-Loop Period (µs)
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 20, fontSize: 23, fontWeight: 700 }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 8, color: C.muted }}>
                  <span style={{ width: 16, height: 16, borderRadius: 4, background: C.muted, display: "inline-block" }} />
                  Host RAM
                </span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 8, color: K.ret }}>
                  <span style={{ width: 16, height: 16, borderRadius: 4, background: K.ret, display: "inline-block" }} />
                  Card BAR4 P2P
                </span>
              </div>
            </div>
            <div style={{ fontSize: 23, color: C.muted, marginTop: 6 }}>
              DSA writes 32 B completion record directly into Card BAR4 vs remote Host RAM
            </div>
          </Card>

          {/* Right Card: Tail Jitter */}
          <Card
            x={1055}
            y={202}
            w={805}
            h={666}
            border={K.fast}
            style={{
              opacity: k2Right,
              padding: "22px 28px",
            }}
          >
            <div style={{ fontSize: 30, fontWeight: 800, color: C.text }}>
              W = 1 · 4 KiB Tail Jitter (p99 − p50)
            </div>
            <div style={{ fontSize: 23, color: C.muted, marginTop: 6 }}>
              Local BAR4 polling eliminates remote Host-RAM DPI read jumps
            </div>
          </Card>
        </div>
      )}

      {/* CUE 3: DSA Batch Scaling Cards */}
      {k3 > 0.005 && (
        <div style={{ opacity: k3 }}>
          {/* Left Main Chart Card */}
          <Card
            x={60}
            y={202}
            w={1120}
            h={666}
            border={K.knob}
            style={{ padding: "22px 28px" }}
          >
            <div style={{ fontSize: 30, fontWeight: 800, color: C.text }}>
              Card BAR4 P2P Throughput vs DSA Batch Size B
            </div>
            <div style={{ fontSize: 23, color: C.muted, marginTop: 6 }}>
              Single variable: batch size B = 1 → 2 → 4 → 8 → 16 (GB/s and ns/op)
            </div>
          </Card>

          {/* Right Mechanism Card */}
          <Card
            x={1210}
            y={202}
            w={650}
            h={666}
            border={K.fast}
            style={{
              opacity: k3Mech,
              transform: `translateY(${(1 - k3Mech) * 18}px)`,
              padding: "24px 28px",
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
            }}
          >
            <div>
              <div style={{ fontSize: 30, fontWeight: 800, color: K.fast, marginBottom: 8 }}>
                Eliminating the ~131 ns/op Drain Bubble
              </div>
              <div style={{ fontSize: 23, color: C.muted }}>
                Why single-op BAR4 P2P capped at 15.28 GB/s — and why batching doubles it
              </div>
            </div>

            {/* Visual timing strip comparison */}
            <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
              <div style={{ background: "rgba(11,16,32,0.7)", borderRadius: 12, padding: "14px 18px", border: `1.5px solid ${C.orange}88` }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 23, fontWeight: 800, marginBottom: 10 }}>
                  <span style={{ color: C.orange }}>B = 1 (single-op)</span>
                  <span style={{ color: C.orange, fontFamily: MONO }}>268.0 ns/op</span>
                </div>
                <div style={{ display: "flex", height: 36, borderRadius: 8, overflow: "hidden", gap: 3 }}>
                  <div style={{ flex: 1, background: K.fwd, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, fontWeight: 800, color: C.bg }}>
                    4 KiB copy
                  </div>
                  <div style={{ flex: 1, background: K.slow, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, fontWeight: 800, color: C.bg }}>
                    ~131 ns/op drain
                  </div>
                </div>
              </div>

              <div style={{ background: "rgba(11,16,32,0.7)", borderRadius: 12, padding: "14px 18px", border: `1.5px solid ${K.fast}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 23, fontWeight: 800, marginBottom: 10 }}>
                  <span style={{ color: K.fast }}>B = 16 (batched)</span>
                  <span style={{ color: K.knob, fontFamily: MONO }}>130.8 ns/op (2.05×)</span>
                </div>
                <div style={{ display: "flex", height: 36, borderRadius: 8, overflow: "hidden", gap: 3 }}>
                  <div style={{ flex: 16, background: K.fast, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, fontWeight: 800, color: C.bg, padding: "0 10px" }}>
                    16 copies · 1 shared 32 B BAR4 completion
                  </div>
                  <div style={{ flex: 1, background: K.knob }} />
                </div>
              </div>
            </div>

            <div style={{ fontSize: 24, color: C.text, lineHeight: 1.4 }}>
              Batching amortizes one <span style={{ color: K.ret, fontWeight: 700 }}>32 B BAR4 completion</span> across{" "}
              <span style={{ color: K.knob, fontWeight: 700 }}>B</span> descriptors — cutting per-op time from{" "}
              <span style={{ fontFamily: MONO, color: C.orange, fontWeight: 700 }}>268.0</span> to{" "}
              <span style={{ fontFamily: MONO, color: K.fast, fontWeight: 700 }}>130.8 ns/op</span>!
            </div>

            {/* Honest workload conditions */}
            <div
              style={{
                background: "rgba(139,149,173,0.10)",
                border: `1.5px solid ${C.line}`,
                borderRadius: 12,
                padding: "12px 18px",
              }}
            >
              <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 1.5, color: C.muted, marginBottom: 4 }}>
                WORKLOAD CONDITIONS
              </div>
              <div style={{ fontSize: 23, color: C.text, fontWeight: 600 }}>
                4 KiB copy · W = 64 ops · hot 512 KiB src · CC = 0 write-through dst
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* CUE 4: B = 16 Head-to-Head 3-Panel Cards */}
      {k4 > 0.005 && (
        <div style={{ opacity: k4 }}>
          {/* Panel 1: Sustained Throughput */}
          <Card
            x={60}
            y={202}
            w={576}
            h={512}
            border={K.fast}
            style={{ opacity: k4P1, transform: `translateY(${(1 - k4P1) * 16}px)`, padding: "20px 26px" }}
          >
            <div style={{ fontSize: 28, fontWeight: 800, color: C.text }}>Sustained Throughput</div>
            <div style={{ fontSize: 22, color: C.muted, marginTop: 4 }}>B = 16 · W = 64 · higher is better ↑</div>
            <div style={{ marginTop: 12 }}>
              <span
                style={{
                  display: "inline-block",
                  fontSize: 21,
                  fontWeight: 800,
                  color: C.bg,
                  background: K.fast,
                  borderRadius: 999,
                  padding: "4px 16px",
                }}
              >
                MATCHES / BEATS HOST RAM
              </span>
            </div>
          </Card>

          {/* Panel 2: W = 1 Batch p50 */}
          <Card
            x={672}
            y={202}
            w={576}
            h={512}
            border={K.fast}
            style={{ opacity: k4P2, transform: `translateY(${(1 - k4P2) * 16}px)`, padding: "20px 26px" }}
          >
            <div style={{ fontSize: 28, fontWeight: 800, color: C.text }}>W = 1 Batch Median (p50)</div>
            <div style={{ fontSize: 22, color: C.muted, marginTop: 4 }}>B = 16 · lower is better ↓</div>
            <div style={{ marginTop: 12 }}>
              <span
                style={{
                  display: "inline-block",
                  fontSize: 21,
                  fontWeight: 800,
                  color: C.bg,
                  background: K.fast,
                  borderRadius: 999,
                  padding: "4px 16px",
                }}
              >
                FASTER MEDIAN LATENCY
              </span>
            </div>
          </Card>

          {/* Panel 3: W = 1 Batch p99 */}
          <Card
            x={1284}
            y={202}
            w={576}
            h={512}
            border={K.fast}
            style={{ opacity: k4P3, transform: `translateY(${(1 - k4P3) * 16}px)`, padding: "20px 26px" }}
          >
            <div style={{ fontSize: 28, fontWeight: 800, color: C.text }}>W = 1 Batch Tail (p99)</div>
            <div style={{ fontSize: 22, color: C.muted, marginTop: 4 }}>B = 16 · lower is better ↓</div>
            <div style={{ marginTop: 12 }}>
              <span
                style={{
                  display: "inline-block",
                  fontSize: 21,
                  fontWeight: 800,
                  color: C.bg,
                  background: K.fast,
                  borderRadius: 999,
                  padding: "4px 16px",
                }}
              >
                −1.14 µs TIGHTER TAIL!
              </span>
            </div>
          </Card>
        </div>
      )}

      {/* ==================== SVG STAGE (Diagrams, PacketStreams, BarCharts) ==================== */}
      <Stage>
        {/* ---------- CUE 0: Zero-Host-CPU Bidirectional PCIe P2P Architecture ---------- */}
        {k0 > 0.005 && (
          <g opacity={k0} fontFamily={FONT}>
            {/* LEFT ENCLOSURE: MARVELL OCTEON 10 DPU */}
            <rect x={60} y={196} width={520} height={556} rx={22} fill="#12192c" stroke={K.card} strokeWidth={3} />
            <rect x={60} y={196} width={520} height={52} rx={22} fill="rgba(248,150,30,0.16)" />
            <rect x={60} y={226} width={520} height={22} fill="rgba(248,150,30,0.16)" />
            <text x={320} y={232} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.card} letterSpacing={1.5}>
              MARVELL OCTEON 10 DPU
            </text>

            {/* DPU Top Block: Card DPI DMA Engine (aligns with Forward P2P y=335) */}
            <rect
              x={92}
              y={272}
              width={456}
              height={126}
              rx={16}
              fill="#17233b"
              stroke={K.fwd}
              strokeWidth={3}
              filter={k0Fwd > 0.4 ? "url(#glow)" : undefined}
            />
            <text x={320} y={316} textAnchor="middle" fontSize={27} fontWeight={800} fill={K.fwd}>
              Card DPI DMA Engine
            </text>
            <text x={320} y={350} textAnchor="middle" fontSize={22} fill={C.text}>
              Atomic 64 B PCIe MWr submit
            </text>
            <text x={320} y={378} textAnchor="middle" fontSize={21} fontFamily={MONO} fill={C.muted}>
              → Host dsa2/wq2.0 portal
            </text>

            {/* DPU Middle Block: Card Core */}
            <rect x={92} y={436} width={456} height={106} rx={16} fill="#182036" stroke={K.card} strokeWidth={2.5} />
            <text x={320} y={478} textAnchor="middle" fontSize={27} fontWeight={800} fill={K.card}>
              Card Core (Arm)
            </text>
            <text x={320} y={514} textAnchor="middle" fontSize={22} fill={C.text}>
              Queues descriptors · polls local BAR4
            </text>

            {/* Internal arrows: Core -> DPI DMA (up), BAR4 -> Core (up) */}
            <Arrow x1={230} y1={436} x2={230} y2={400} color={K.fwd} w={4} head={12} k={k0Fwd} />
            <text x={246} y={425} textAnchor="start" fontSize={20} fontWeight={700} fill={K.fwd} opacity={k0Fwd}>
              submit
            </text>
            <Arrow x1={230} y1={580} x2={230} y2={544} color={K.ret} w={4} head={12} dash="6 4" k={k0Ret} />
            <text x={246} y={569} textAnchor="start" fontSize={20} fontWeight={700} fill={K.ret} opacity={k0Ret}>
              local poll
            </text>

            {/* DPU Bottom Block: Card BAR4 Completion Slot (aligns with Reverse P2P y=648) */}
            <rect
              x={92}
              y={580}
              width={456}
              height={142}
              rx={16}
              fill="#14292d"
              stroke={K.ret}
              strokeWidth={3}
              filter={k0Ret > 0.4 ? "url(#glow)" : undefined}
            />
            <text x={320} y={622} textAnchor="middle" fontSize={27} fontWeight={800} fill={K.ret}>
              Card BAR4 Completion Slot
            </text>
            <text x={320} y={656} textAnchor="middle" fontSize={22} fill={C.text}>
              32 B DSA completion lands locally
            </text>
            {/* Pulsing BAR4 status cell */}
            <rect
              x={168}
              y={674}
              width={304}
              height={32}
              rx={8}
              fill={`rgba(67,197,158,${0.14 + 0.18 * bar4Pulse})`}
              stroke={K.ret}
              strokeWidth={2}
            />
            <text x={320} y={697} textAnchor="middle" fontSize={20} fontFamily={MONO} fontWeight={700} fill={K.ret}>
              32 B status = DONE (local)
            </text>

            {/* CENTER: PCIe Gen5 x4 P2P BRIDGE */}
            <line x1={940} y1={210} x2={940} y2={740} stroke={C.line} strokeWidth={2.5} strokeDasharray="8 8" />
            <rect x={800} y={456} width={280} height={68} rx={34} fill="#141b2e" stroke={K.link} strokeWidth={2.5} />
            <text x={940} y={487} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.text}>
              PCIe Gen5 ×4 P2P
            </text>
            <text x={940} y={512} textAnchor="middle" fontSize={19} fill={C.muted}>
              Direct Bidirectional Bridge
            </text>

            {/* FORWARD P2P LANE (Top, y = 335, Card DPI DMA -> Host DSA2) */}
            <Link pts={[[548, 335], [1328, 335]]} color={K.fwd} w={7} k={k0Fwd} />
            {k0Fwd > 0.85 && (
              <polygon points="1334,335 1312,322 1312,348" fill={K.fwd} />
            )}
            <PacketStream
              pts={[[556, 335], [1316, 335]]}
              travel={rt * 230}
              gap={155}
              size={15}
              color={K.fwd}
              opacity={prog(t, T[0] + 1.4, 0.5)}
            />
            {/* Forward Lane Labels */}
            <rect x={630} y={246} width={620} height={46} rx={12} fill="#0e1c2f" stroke={K.fwd} strokeWidth={2} opacity={k0Fwd} />
            <text x={940} y={277} textAnchor="middle" fontSize={25} fontWeight={800} fill={K.fwd} opacity={k0Fwd}>
              Forward P2P →  64 B DSA descriptor
            </text>
            <text x={940} y={376} textAnchor="middle" fontSize={22} fontWeight={600} fill={C.muted} opacity={k0Fwd}>
              Card DPI DMA  →  Host dsa2/wq2.0 portal
            </text>

            {/* REVERSE P2P LANE (Bottom, Host DSA2 -> Card BAR4 y = 648) */}
            <Link
              pts={[
                [1334, 442],
                [1246, 442],
                [1246, 648],
                [554, 648],
              ]}
              color={K.ret}
              w={7}
              k={k0Ret}
            />
            {k0Ret > 0.85 && (
              <polygon points="548,648 570,635 570,661" fill={K.ret} />
            )}
            <PacketStream
              pts={[
                [1326, 442],
                [1246, 442],
                [1246, 648],
                [566, 648],
              ]}
              travel={rt * 230}
              gap={165}
              size={15}
              color={K.ret}
              opacity={prog(t, T[0] + 2.8, 0.5)}
            />
            {/* Reverse Lane Labels */}
            <rect x={615} y={562} width={595} height={46} rx={12} fill="#0e2624" stroke={K.ret} strokeWidth={2} opacity={k0Ret} />
            <text x={912} y={593} textAnchor="middle" fontSize={25} fontWeight={800} fill={K.ret} opacity={k0Ret}>
              ← Reverse P2P  32 B completion record
            </text>
            <text x={912} y={690} textAnchor="middle" fontSize={22} fontWeight={600} fill={C.muted} opacity={k0Ret}>
              Host Intel DSA2  →  Card BAR4 slot
            </text>

            {/* RIGHT ENCLOSURE: INTEL XEON HOST */}
            <rect x={1300} y={196} width={560} height={556} rx={22} fill="#12192c" stroke={K.host} strokeWidth={3} />
            <rect x={1300} y={196} width={560} height={52} rx={22} fill="rgba(124,147,214,0.18)" />
            <rect x={1300} y={226} width={560} height={22} fill="rgba(124,147,214,0.18)" />
            <text x={1580} y={232} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.host} letterSpacing={1.5}>
              INTEL XEON HOST
            </text>

            {/* Host Top Glowing Block: Intel DSA2 Copy Engine */}
            <rect
              x={1334}
              y={272}
              width={492}
              height={224}
              rx={16}
              fill={`rgba(67,197,158,${0.08 + 0.08 * dsaPulse})`}
              stroke={K.fast}
              strokeWidth={3.5}
              filter="url(#glow)"
            />
            <text x={1360} y={316} textAnchor="start" fontSize={28} fontWeight={800} fill={K.fast}>
              Intel DSA2 Copy Engine
            </text>
            <rect x={1696} y={291} width={108} height={32} rx={16} fill={K.fast} />
            <text x={1750} y={314} textAnchor="middle" fontSize={20} fontWeight={800} fill={C.bg}>
              ACTIVE
            </text>
            <text x={1360} y={352} textAnchor="start" fontSize={22} fill={C.text}>
              Accepts 64 B descriptor at{" "}
              <tspan fontFamily={MONO} fill={K.fwd} fontWeight={700}>
                wq2.0
              </tspan>
            </text>

            {/* Mini Host Memory Copy Sub-box inside DSA2 */}
            <rect x={1354} y={376} width={452} height={98} rx={12} fill="#141b2e" stroke={K.host} strokeWidth={2} />
            <text x={1580} y={408} textAnchor="middle" fontSize={23} fontWeight={800} fill={C.text}>
              4 KiB Host Memory Copy
            </text>
            <rect x={1372} y={424} width={174} height={34} rx={7} fill="rgba(76,201,240,0.18)" stroke={K.fwd} strokeWidth={1.5} />
            <text x={1459} y={448} textAnchor="middle" fontSize={20} fontFamily={MONO} fill={K.fwd} fontWeight={700}>
              src (512 KiB)
            </text>
            <Arrow x1={1552} y1={441} x2={1608} y2={441} color={K.fast} w={4} head={11} />
            <rect x={1614} y={424} width={174} height={34} rx={7} fill="rgba(67,197,158,0.18)" stroke={K.fast} strokeWidth={1.5} />
            <text x={1701} y={448} textAnchor="middle" fontSize={20} fontFamily={MONO} fill={K.fast} fontWeight={700}>
              dst (CC = 0)
            </text>

            {/* Host Bottom Faded/Crossed-Out Block: Host CPU Core (IDLE · 0% CPU) */}
            <g opacity={0.35 + 0.65 * k0Idle}>
              <rect
                x={1334}
                y={530}
                width={492}
                height={192}
                rx={16}
                fill="url(#hatch)"
                stroke={C.dim}
                strokeWidth={2.5}
                strokeDasharray="10 6"
              />
              {/* Subtle crossed-out diagonal slashes */}
              <line x1={1354} y1={548} x2={1806} y2={704} stroke={C.dim} strokeWidth={3} opacity={0.4} />
              <line x1={1806} y1={548} x2={1354} y2={704} stroke={C.dim} strokeWidth={3} opacity={0.4} />

              <rect x={1452} y={550} width={256} height={42} rx={10} fill="rgba(11,16,32,0.82)" />
              <text x={1580} y={581} textAnchor="middle" fontSize={29} fontWeight={800} fill={C.muted}>
                Host CPU Core
              </text>
              <rect x={1366} y={606} width={428} height={50} rx={25} fill="#141b2e" stroke={C.muted} strokeWidth={2.5} />
              <text x={1580} y={639} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.text} letterSpacing={0.6}>
                IDLE · 0% CPU · ZERO FORWARDING
              </text>
              <rect x={1356} y={670} width={448} height={36} rx={10} fill="rgba(11,16,32,0.85)" />
              <text x={1580} y={695} textAnchor="middle" fontSize={21} fill={C.muted}>
                Never touches descriptors, data, or completions
              </text>
            </g>
          </g>
        )}

        {/* ---------- CUE 1: Right Card WC Drop-Rate BarChart ---------- */}
        {k1 > 0.005 && (
          <g opacity={k1 * k1Right}>
            <BarChart
              x0={1035}
              x1={1810}
              y1={392}
              y0={672}
              max={1.45}
              dec={3}
              unit="%"
              size={30}
              labelSize={25}
              barW={142}
              k={(i) => prog(t, T[1] + 1.55 + i * 0.35, 0.55)}
              bars={[
                { label: "WC none", value: 0.048, color: C.orange, text: "0.048%" },
                { label: "WC DGH", value: 0.609, color: K.slow, text: "0.609%" },
                { label: "WC DSB", value: 1.217, color: K.slow, text: "1.217%" },
              ]}
            />
          </g>
        )}

        {/* ---------- CUE 2: Reverse P2P BarCharts (Period + Jitter) ---------- */}
        {k2 > 0.005 && (
          <g opacity={k2}>
            {/* Left Group A: NOP */}
            <g opacity={k2Left}>
              <BarChart
                x0={105}
                x1={495}
                y1={390}
                y0={696}
                max={3.6}
                dec={2}
                unit=" µs"
                size={29}
                labelSize={24}
                barW={120}
                k={(i) => prog(t, T[2] + 0.75 + i * 0.35, 0.55)}
                bars={[
                  { label: "Host RAM", value: 1.96, color: C.muted, text: "1.96 µs" },
                  { label: "Card BAR4", value: 1.27, color: K.ret, text: "1.27 µs" },
                ]}
              />
              <Brace x1={120} x2={480} y={758} label="NOP" color={C.muted} size={25} opacity={k2Left} />

              {/* Left Group B: 4 KiB */}
              <BarChart
                x0={565}
                x1={965}
                y1={390}
                y0={696}
                max={3.6}
                dec={2}
                unit=" µs"
                size={29}
                labelSize={24}
                barW={120}
                k={(i) => prog(t, T[2] + 1.45 + i * 0.35, 0.55)}
                bars={[
                  { label: "Host RAM", value: 3.13, color: C.muted, text: "3.13 µs" },
                  { label: "Card BAR4", value: 2.17, color: K.ret, text: "2.17 µs" },
                ]}
              />
              <Brace x1={580} x2={950} y={758} label="4 KiB copy" color={C.muted} size={25} opacity={k2Left} />

              {/* Reduction callouts over NOP and 4 KiB */}
              <g opacity={prog(t, T[2] + 1.5, 0.5)}>
                <rect x={218} y={322} width={164} height={44} rx={22} fill="rgba(67,197,158,0.16)" stroke={K.fast} strokeWidth={2.5} />
                <text x={300} y={352} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.fast}>
                  NOP: −35%
                </text>
              </g>
              <g opacity={prog(t, T[2] + 2.2, 0.5)}>
                <rect x={672} y={322} width={186} height={44} rx={22} fill="rgba(67,197,158,0.16)" stroke={K.fast} strokeWidth={2.5} />
                <text x={765} y={352} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.fast}>
                  4 KiB: −31%
                </text>
              </g>
            </g>

            {/* Right Card: Tail Jitter BarChart */}
            <g opacity={k2Right}>
              <BarChart
                x0={1115}
                x1={1800}
                y1={390}
                y0={696}
                max={1300}
                dec={0}
                unit=" ns"
                size={31}
                labelSize={25}
                barW={165}
                k={(i) => prog(t, T[2] + 1.85 + i * 0.45, 0.55)}
                bars={[
                  { label: "Host RAM poll", value: 1119, color: K.slow, text: "1,119 ns", sub: "remote DPI read jump" },
                  { label: "Card BAR4 P2P", value: 163, color: K.fast, text: "163 ns", sub: "local card poll" },
                ]}
              />
              {/* Downward drop arrow into the -85% badge */}
              <Arrow
                x1={1435}
                y1={432}
                x2={1568}
                y2={490}
                color={K.fast}
                w={5}
                head={14}
                k={prog(t, T[2] + 2.6, 0.5)}
              />
            </g>
          </g>
        )}

        {/* ---------- CUE 3: DSA Batch B = 1..16 BarChart ---------- */}
        {k3 > 0.005 && (
          <g opacity={k3}>
            <BarChart
              x0={100}
              x1={1140}
              y1={345}
              y0={745}
              max={36}
              dec={2}
              unit=" GB/s"
              size={24}
              labelSize={24}
              barW={124}
              refLine={{ value: 31.10, label: "Host-RAM B=16: 31.10 GB/s", color: C.muted }}
              k={(i) => prog(t, T[3] + 0.55 + i * 0.32, 0.55)}
              bars={[
                { label: "B = 1", value: 15.28, color: C.orange, text: "15.28 GB/s", sub: "268.0 ns/op" },
                { label: "B = 2", value: 23.35, color: K.fwd, text: "23.35 GB/s", sub: "175.4 ns/op" },
                { label: "B = 4", value: 31.13, color: K.fast, text: "31.13 GB/s", sub: "131.6 ns/op" },
                { label: "B = 8", value: 31.25, color: K.fast, text: "31.25 GB/s", sub: "131.1 ns/op" },
                { label: "B = 16", value: 31.32, color: K.knob, text: "31.32 GB/s", sub: "130.8 ns/op" },
              ]}
            />
            {/* Drain bubble annotation above B = 1 */}
            <g opacity={prog(t, T[3] + 1.8, 0.5)}>
              <rect x={102} y={434} width={206} height={56} rx={10} fill="rgba(248,150,30,0.14)" stroke={C.orange} strokeWidth={2} />
              <text x={205} y={459} textAnchor="middle" fontSize={20} fontWeight={800} fill={C.orange}>
                ~131 ns/op
              </text>
              <text x={205} y={482} textAnchor="middle" fontSize={19} fontWeight={600} fill={C.text}>
                drain bubble
              </text>
            </g>
          </g>
        )}

        {/* ---------- CUE 4: B = 16 Head-to-Head 3 BarCharts ---------- */}
        {k4 > 0.005 && (
          <g opacity={k4}>
            {/* Panel 1: Sustained Throughput */}
            <g opacity={k4P1}>
              <BarChart
                x0={105}
                x1={590}
                y1={368}
                y0={630}
                max={38}
                dec={2}
                unit=" GB/s"
                size={27}
                labelSize={24}
                barW={134}
                k={(i) => prog(t, T[4] + 0.65 + i * 0.3, 0.5)}
                bars={[
                  { label: "Host RAM", value: 31.10, color: C.muted, text: "31.10 GB/s" },
                  { label: "Card BAR4 P2P", value: 31.32, color: K.fast, text: "31.32 GB/s" },
                ]}
              />
            </g>

            {/* Panel 2: W = 1 Batch p50 */}
            <g opacity={k4P2}>
              <BarChart
                x0={717}
                x1={1202}
                y1={368}
                y0={630}
                max={6.5}
                dec={2}
                unit=" µs"
                size={27}
                labelSize={24}
                barW={134}
                k={(i) => prog(t, T[4] + 1.25 + i * 0.3, 0.5)}
                bars={[
                  { label: "Host RAM", value: 4.63, color: C.muted, text: "4.63 µs" },
                  { label: "Card BAR4 P2P", value: 4.42, color: K.fast, text: "4.42 µs" },
                ]}
              />
            </g>

            {/* Panel 3: W = 1 Batch p99 */}
            <g opacity={k4P3}>
              <BarChart
                x0={1329}
                x1={1814}
                y1={368}
                y0={630}
                max={6.5}
                dec={2}
                unit=" µs"
                size={27}
                labelSize={24}
                barW={134}
                k={(i) => prog(t, T[4] + 1.85 + i * 0.3, 0.5)}
                bars={[
                  { label: "Host RAM", value: 5.75, color: K.slow, text: "5.75 µs" },
                  { label: "Card BAR4 P2P", value: 4.61, color: K.fast, text: "4.61 µs" },
                ]}
              />
            </g>
          </g>
        )}
      </Stage>

      {/* ==================== FOREGROUND HTML OVERLAYS (above SVG Stage) ==================== */}

      {/* CUE 0 Header & Takeaway */}
      {k0 > 0.005 && (
        <div style={{ opacity: k0 }}>
          <Pill x={60} y={128} color={K.fast} solid size={22}>
            NEW BREAKTHROUGH
          </Pill>
          <Txt x={358} y={132} w={1502} size={27} weight={700} color={C.text}>
            Bidirectional PCIe P2P: DPU drives Host Intel DSA2 directly — ZERO Host CPU in the data path
          </Txt>
          <Takeaway x={60} y={774} w={1800} k={k0Take} color={K.fast} label="ZERO-HOST-CPU DATA PATH" size={25}>
            Card DPI DMA pushes 64 B descriptors to Intel DSA2; DSA2 writes 32 B completions back to Card BAR4 — 0% host CPU!
          </Takeaway>
        </div>
      )}

      {/* TOP KNOB BAR (Cues 1–4) */}
      {kBar > 0.005 && (
        <KnobBar
          y={118}
          size={22}
          fixed={["Zero host CPU", "4 KiB copy", "Hot 512 KiB, CC=0"]}
          knob="P2P step"
          options={["1 · Fwd DMA", "2 · Rev BAR4", "3 · Batch B", "4 · vs Host"]}
          active={activeStep}
          k={kBar}
        />
      )}

      {/* CUE 2 Pop Badge: -85% JITTER! */}
      {k2 > 0.005 && k2JitterPop > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 1492,
            top: 492,
            opacity: k2 * k2Right * Math.min(1, k2JitterPop),
            transform: `scale(${k2JitterPop})`,
            transformOrigin: "center",
            fontFamily: FONT,
            fontSize: 32,
            fontWeight: 900,
            color: C.bg,
            background: K.fast,
            borderRadius: 999,
            padding: "10px 26px",
            boxShadow: `0 0 28px ${K.fast}88`,
            whiteSpace: "nowrap",
          }}
        >
          −85% JITTER!
        </div>
      )}

      {/* CUE 3 Pop Badge: 2.05× JUMP! */}
      {k3 > 0.005 && k3JumpPop > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 916,
            top: 222,
            opacity: k3 * Math.min(1, k3JumpPop),
            transform: `scale(${k3JumpPop})`,
            transformOrigin: "center",
            fontFamily: FONT,
            fontSize: 28,
            fontWeight: 900,
            color: C.bg,
            background: K.knob,
            borderRadius: 999,
            padding: "8px 24px",
            boxShadow: `0 0 26px ${K.knob}88`,
            whiteSpace: "nowrap",
          }}
        >
          2.05× JUMP!
        </div>
      )}

      {/* CUE 4 Breakthrough Takeaway */}
      {k4 > 0.005 && (
        <Takeaway x={60} y={736} w={1800} k={k4Take} color={K.fast} label="BREAKTHROUGH" size={25}>
          Bidirectional Card → Intel DSA P2P matches/beats host-RAM completion on BOTH throughput (31.32 GB/s) and latency (4.42 / 4.61 µs) — with ZERO host CPU!
        </Takeaway>
      )}
    </>
  );
}

export const S06: SceneDef = {
  id: "S06",
  title: "New breakthrough · Zero-host-CPU Card → DSA P2P",
  stage: 4,
  source: "Card DPI DMA ⇄ Host Intel DSA2 ⇄ Card BAR4 · 182.9M+ verified ops",
  dur: DUR,
  cues,
  C: Body,
};
