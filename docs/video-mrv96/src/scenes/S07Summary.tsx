import React from "react";
import { Arrow, C, Card, Cue, FONT, K, MONO, SceneDef, Stage, prog, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 19.72, "**Three verified direct-path pillars:** (1) One-way `MOVDIR64B` hits **161.6 M/s** (`3 µs` pause avoids write-band conflict); (2) `897 ns` `W = 1` has all 3 cache/DDIO shortcuts active + **64 B** coherence; (3) Open-loop hits **91–97 M/s**.", "To summarize our progress. Three direct-path pillars are nailed down: one hundred sixty-one point six million one-way by avoiding the write band, eight hundred ninety-seven nanoseconds with all three cache shortcuts active and sixty-four byte coherence, and ninety-one to ninety-seven million open-loop bidirectional."], // 0
  [20.07, 37.79, "**Breakthrough pillar:** Zero-host-CPU bidirectional **Card → Intel DSA P2P** reaches **31.32 GB/s** (`B = 16`) and **2.17 µs** (`W = 1`, `163 ns` jitter) with **0 drops over 182.9M+ ops**.", "In parallel, our zero-host-C P U card-to-Intel D S A peer-to-peer path reaches thirty-one point three two gigabytes per second and two point one seven microseconds at one hundred sixty-three nanoseconds jitter, with zero drops across one hundred eighty-two million operations."], // 1
  [38.14, 50.30, "**Active open puzzle (queued next):** Pin down which hardware queue throttles 4-core closed-loop traffic to **13 M/s** (and drives the `35–39 µs` `p99` tail) via host burst pacing + Store Buffer / CHA / IIO / TAD counters.", "One open puzzle is queued next on the card right now: pinning down which exact hardware queue throttles four-core closed-loop traffic to thirteen million per second and drives the thirty-five microsecond tail."], // 2
  [50.65, 64.47, "**Next system milestone:** Combine the **897 ns** direct control ring with **31.3 GB/s** Card → DSA P2P bulk offload into a minimal DPU-driven **RPC & memory-offload layer**.", "Next system milestone: combine the eight hundred ninety-seven nanosecond direct control ring with thirty-one gigabyte per second card-driven D S A offload into a minimal D P U to host R P C layer."], // 3
];
const DUR = 65.87; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Badge({ label, color, solid = true }: { label: string; color: string; solid?: boolean }) {
  return (
    <span
      style={{
        display: "inline-block",
        fontFamily: FONT,
        fontSize: 20,
        fontWeight: 900,
        letterSpacing: 1.5,
        color: solid ? C.bg : color,
        background: solid ? color : "rgba(11,16,32,0.7)",
        border: `2px solid ${color}`,
        borderRadius: 8,
        padding: "4px 13px",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

function Body() {
  const t = useT();

  // Progressive reveal across the 4 executive cues (all remain crisp together by Cue 3)
  const kC0 = prog(t, T[0] + 0.15, 0.55);
  const kP1 = prog(t, T[0] + 1.8, 0.55);
  const kP2 = prog(t, T[0] + 6.8, 0.55);
  const kP3 = prog(t, T[0] + 12.2, 0.55);

  const kC1 = prog(t, T[1] + 0.15, 0.55);
  const kC1Kpi = prog(t, T[1] + 1.2, 0.55);
  const kC1Rel = prog(t, T[1] + 4.2, 0.55);

  const kC2 = prog(t, T[2] + 0.15, 0.55);
  const kC2Body = prog(t, T[2] + 1.4, 0.55);

  const kC3 = prog(t, T[3] + 0.15, 0.55);
  const kC3Body = prog(t, T[3] + 1.4, 0.55);

  // Active cue accent glow (subtle visual focus on the quadrant currently being narrated)
  const activeCue = t < T[1] ? 0 : t < T[2] ? 1 : t < T[3] ? 2 : 3;
  const cardShadow = (idx: number, col: string) =>
    activeCue === idx ? `0 0 28px ${col}33, inset 0 0 0 1px ${col}55` : "none";

  return (
    <>
      {/* ==================== TOP-LEFT (CUE 0): 3 VERIFIED DIRECT-PATH PILLARS ==================== */}
      <Card
        x={60}
        y={132}
        w={884}
        h={372}
        border={K.fast}
        style={{
          opacity: kC0,
          transform: `translateY(${(1 - kC0) * 18}px)`,
          boxShadow: cardShadow(0, K.fast),
          padding: "16px 22px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14 }}>
          <Badge label="NAILED DOWN" color={K.fast} />
          <span style={{ fontFamily: FONT, fontSize: 27, fontWeight: 800, color: C.text }}>
            3 Verified Direct-Path Pillars
          </span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
          {/* Pillar 1 */}
          <div
            style={{
              opacity: kP1,
              transform: `translateX(${(1 - kP1) * -14}px)`,
              display: "flex",
              alignItems: "center",
              gap: 16,
              background: "rgba(67,197,158,0.08)",
              borderLeft: `6px solid ${K.fast}`,
              borderRadius: 10,
              padding: "10px 14px",
            }}
          >
            <div style={{ width: 174, flexShrink: 0, fontFamily: MONO, fontSize: 30, fontWeight: 900, color: K.fast }}>
              161.6 M/s
            </div>
            <div style={{ fontFamily: FONT, fontSize: 22, color: C.text, lineHeight: 1.3 }}>
              <strong>One-way:</strong> <span style={{ fontFamily: MONO, color: K.knob }}>3 µs</span> pause avoids
              write-band conflict (not L3 BW; return innocent).
            </div>
          </div>

          {/* Pillar 2 */}
          <div
            style={{
              opacity: kP2,
              transform: `translateX(${(1 - kP2) * -14}px)`,
              display: "flex",
              alignItems: "center",
              gap: 16,
              background: "rgba(76,201,240,0.08)",
              borderLeft: `6px solid ${K.fwd}`,
              borderRadius: 10,
              padding: "10px 14px",
            }}
          >
            <div style={{ width: 174, flexShrink: 0, fontFamily: MONO, fontSize: 30, fontWeight: 900, color: K.fwd }}>
              897 ns
            </div>
            <div style={{ fontFamily: FONT, fontSize: 22, color: C.text, lineHeight: 1.3 }}>
              <strong>W = 1 + 64 B coherence:</strong> all 3 cache/DDIO shortcuts active (
              <span style={{ fontFamily: MONO, color: C.muted }}>+117 / +75 / +87 ns</span> cold).
            </div>
          </div>

          {/* Pillar 3 */}
          <div
            style={{
              opacity: kP3,
              transform: `translateX(${(1 - kP3) * -14}px)`,
              display: "flex",
              alignItems: "center",
              gap: 16,
              background: "rgba(67,197,158,0.08)",
              borderLeft: `6px solid ${K.ret}`,
              borderRadius: 10,
              padding: "10px 14px",
            }}
          >
            <div style={{ width: 174, flexShrink: 0, fontFamily: MONO, fontSize: 30, fontWeight: 900, color: K.ret }}>
              91–97 M/s
            </div>
            <div style={{ fontFamily: FONT, fontSize: 22, color: C.text, lineHeight: 1.3 }}>
              <strong>Open-loop duplex:</strong> breaks lockstep at{" "}
              <span style={{ fontFamily: MONO, color: K.fast }}>~1 µs</span> p50 (honest{" "}
              <span style={{ fontFamily: MONO, color: K.knob }}>p99</span> tails noted).
            </div>
          </div>
        </div>
      </Card>

      {/* ==================== TOP-RIGHT (CUE 1): NEW BREAKTHROUGH P2P ==================== */}
      <Card
        x={976}
        y={132}
        w={884}
        h={372}
        border={K.knob}
        style={{
          opacity: kC1,
          transform: `translateY(${(1 - kC1) * 18}px)`,
          boxShadow: cardShadow(1, K.knob),
          padding: "16px 22px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14 }}>
          <Badge label="BREAKTHROUGH" color={K.knob} />
          <span style={{ fontFamily: FONT, fontSize: 27, fontWeight: 800, color: C.text }}>
            Zero-Host-CPU Card → Intel DSA P2P
          </span>
        </div>

        {/* Two Hero KPI Tiles */}
        <div
          style={{
            display: "flex",
            gap: 16,
            marginBottom: 14,
            opacity: kC1Kpi,
            transform: `translateY(${(1 - kC1Kpi) * 10}px)`,
          }}
        >
          <div
            style={{
              flex: 1,
              background: "rgba(249,199,79,0.10)",
              border: `2px solid ${K.knob}`,
              borderRadius: 14,
              padding: "12px 18px",
            }}
          >
            <div style={{ fontFamily: MONO, fontSize: 48, fontWeight: 900, color: K.knob, lineHeight: 1.1 }}>
              31.32 GB/s
            </div>
            <div style={{ fontFamily: FONT, fontSize: 22, color: C.text, marginTop: 6 }}>
              Batched P2P (<span style={{ fontFamily: MONO, color: K.knob, fontWeight: 700 }}>B = 16</span>)
            </div>
          </div>

          <div
            style={{
              flex: 1,
              background: "rgba(76,201,240,0.10)",
              border: `2px solid ${K.fwd}`,
              borderRadius: 14,
              padding: "12px 18px",
            }}
          >
            <div style={{ fontFamily: MONO, fontSize: 48, fontWeight: 900, color: K.fwd, lineHeight: 1.1 }}>
              2.17 µs
            </div>
            <div style={{ fontFamily: FONT, fontSize: 22, color: C.text, marginTop: 6 }}>
              <span style={{ fontFamily: MONO }}>W = 1</span> (4 KiB) ·{" "}
              <strong style={{ color: K.fast }}>163 ns</strong> jitter (−85%)
            </div>
          </div>
        </div>

        {/* Path Ribbon */}
        <div
          style={{
            background: "rgba(11,16,32,0.7)",
            border: `1.5px solid ${C.line}`,
            borderRadius: 10,
            padding: "9px 16px",
            fontFamily: FONT,
            fontSize: 22,
            fontWeight: 700,
            color: C.text,
            textAlign: "center",
            marginBottom: 12,
          }}
        >
          <span style={{ color: K.card }}>Card DPI DMA</span>
          <span style={{ color: C.muted }}> → </span>
          <span style={{ color: K.fwd }}>Host Intel DSA2</span>
          <span style={{ color: C.muted }}> → </span>
          <span style={{ color: K.knob }}>Card BAR4 P2P</span>
          <span style={{ color: C.muted, fontWeight: 500 }}> (zero host CPU)</span>
        </div>

        {/* Reliability & Parity Footer */}
        <div
          style={{
            opacity: kC1Rel,
            background: "rgba(67,197,158,0.10)",
            borderLeft: `6px solid ${K.fast}`,
            borderRadius: 10,
            padding: "10px 14px",
            fontFamily: FONT,
            fontSize: 22,
            color: C.text,
            lineHeight: 1.35,
          }}
        >
          <strong style={{ color: K.fast }}>0 drops</strong> over{" "}
          <strong style={{ fontFamily: MONO, color: K.fast }}>182.9M+</strong> ops · Matches/beats host-RAM
          completion on throughput &amp; latency.
        </div>
      </Card>

      {/* ==================== BOTTOM-LEFT (CUE 2): ACTIVE OPEN PUZZLE ==================== */}
      <Card
        x={60}
        y={526}
        w={884}
        h={348}
        border={K.slow}
        style={{
          opacity: kC2,
          transform: `translateY(${(1 - kC2) * 18}px)`,
          boxShadow: cardShadow(2, K.slow),
          padding: "18px 24px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 16 }}>
          <Badge label="ACTIVE OPEN PUZZLE" color={K.slow} />
          <span style={{ fontFamily: FONT, fontSize: 27, fontWeight: 800, color: C.text }}>
            Queued Next on Card
          </span>
        </div>

        <div
          style={{
            background: "rgba(249,65,68,0.10)",
            border: `2px solid ${K.slow}`,
            borderRadius: 12,
            padding: "14px 18px",
            marginBottom: 16,
            fontFamily: FONT,
            fontSize: 28,
            fontWeight: 800,
            color: C.text,
            lineHeight: 1.3,
          }}
        >
          Pin down the <span style={{ fontFamily: MONO, color: K.slow, whiteSpace: "nowrap" }}>13 M/s</span> 4-core
          closed-loop throttle &amp;{" "}
          <span style={{ fontFamily: MONO, color: K.knob, whiteSpace: "nowrap" }}>35–39 µs</span> p99 tail
        </div>

        <div
          style={{
            opacity: kC2Body,
            display: "flex",
            flexDirection: "column",
            gap: 12,
            fontFamily: FONT,
            fontSize: 24,
            color: C.text,
            lineHeight: 1.4,
          }}
        >
          <div>
            <strong style={{ color: K.knob }}>Next run:</strong>{" "}
            <span style={{ fontFamily: MONO }}>MOVDIR64B</span> burst pacing + host{" "}
            <strong>Store Buffer / CHA / IIO</strong> vs card <strong>TAD</strong> counters.
          </div>
          <div>
            <strong style={{ color: K.fast }}>Goal:</strong> Eliminate the slow state &amp; verify clean
            long-run <span style={{ fontFamily: MONO }}>p99</span> latency.
          </div>
        </div>
      </Card>

      {/* ==================== BOTTOM-RIGHT (CUE 3): NEXT SYSTEM MILESTONE ==================== */}
      <Card
        x={976}
        y={526}
        w={884}
        h={348}
        border={C.purple}
        style={{
          opacity: kC3,
          transform: `translateY(${(1 - kC3) * 18}px)`,
          boxShadow: cardShadow(3, C.purple),
          padding: "18px 24px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 16 }}>
          <Badge label="NEXT MILESTONE" color={C.purple} />
          <span style={{ fontFamily: FONT, fontSize: 27, fontWeight: 800, color: C.text }}>
            DPU-Driven System Layer
          </span>
        </div>

        {/* Architecture Combine Strip */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 14,
            marginBottom: 16,
          }}
        >
          <div
            style={{
              flex: 1,
              background: "rgba(76,201,240,0.10)",
              border: `2px solid ${K.fwd}`,
              borderRadius: 12,
              padding: "12px 16px",
              textAlign: "center",
            }}
          >
            <div style={{ fontFamily: MONO, fontSize: 34, fontWeight: 900, color: K.fwd }}>897 ns</div>
            <div style={{ fontFamily: FONT, fontSize: 21, fontWeight: 700, color: C.text }}>
              Direct Control Ring
            </div>
          </div>

          <div style={{ fontFamily: FONT, fontSize: 38, fontWeight: 900, color: C.purple }}>+</div>

          <div
            style={{
              flex: 1,
              background: "rgba(249,199,79,0.10)",
              border: `2px solid ${K.knob}`,
              borderRadius: 12,
              padding: "12px 16px",
              textAlign: "center",
            }}
          >
            <div style={{ fontFamily: MONO, fontSize: 34, fontWeight: 900, color: K.knob }}>31.3 GB/s</div>
            <div style={{ fontFamily: FONT, fontSize: 21, fontWeight: 700, color: C.text }}>
              Card → DSA P2P Bulk
            </div>
          </div>
        </div>

        <div
          style={{
            opacity: kC3Body,
            display: "flex",
            flexDirection: "column",
            gap: 12,
            fontFamily: FONT,
            fontSize: 24,
            color: C.text,
            lineHeight: 1.4,
          }}
        >
          <div>
            <strong style={{ color: C.purple }}>Build:</strong> Minimal DPU-driven{" "}
            <strong>RPC &amp; memory-offload layer</strong> uniting control + bulk paths.
          </div>
          <div>
            <strong style={{ color: K.fast }}>Evaluate:</strong> Real end-to-end host ↔ DPU workloads.
          </div>
        </div>
      </Card>

      {/* Subtle SVG connector arrow from Open Puzzle → Next Milestone on Cue 3 */}
      <Stage>
        {kC3 > 0.05 && (
          <Arrow x1={946} y1={700} x2={974} y2={700} color={C.purple} w={4} head={11} k={kC3} />
        )}
      </Stage>
    </>
  );
}

export const S07: SceneDef = {
  id: "S07",
  title: "Executive summary & immediate next steps",
  stage: 5,
  source: "Advisor progress summary · verified pillars, open puzzle & roadmap",
  dur: DUR,
  cues,
  C: Body,
};
