import React from "react";
import { Arrow, Axes, C, Card, Cue, FONT, K, MONO, SceneDef, Series, Stage, fade, linScale, prog, useT } from "../lib";
import { BarChart, InfPill, KnobBar, SegBar, Takeaway } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 15.10, "**The open puzzle:** By Little's Law at `0.92 µs` RTT, `W = 64` should saturate near **~50 M/s** — and **1 card core at W = 1024 DOES hit 50.5 M/s**!", "Now the one open puzzle we have not solved yet. By Little's Law at zero point nine two microseconds round trip, window sixty-four should approach fifty million per second. Indeed, one card core at window ten twenty-four hits fifty point five million."], // 0
  [15.45, 28.93, "Yet **4 card cores** collapse to **13.1–13.5 M/s** at `W = 64..256` (and only **23.9 M/s** at `W = 1024`, where host polling grows `13.6 → 32.5 ns` across 4 sub-rings). Why?", "Yet four card cores collapse to thirteen million across windows sixty-four to two fifty-six, and reach only twenty-three point nine million at window ten twenty-four, where host polling swells across four sub-rings. Why?"], // 1
  [29.28, 43.66, "**Clue 1 (Timestamps):** At `W = 64` (`13.5 M/s`, `3.7 µs` RTT), card work stays **0.04 µs** while **~42 msgs** queue (`3.1–3.2 µs` forward — flipping to **return** `0.7 / 2.7 µs` in 2 runs without page pre-touch).", "First clue: timestamps show card work stays zero point zero four microseconds, while forty-two messages pile up in transit, three point one microseconds forward, though flipping to return in two runs without pre-touched pages."], // 2
  [44.01, 57.83, "**Clue 2 (Both CPUs idle):** Delay injection (`+δ`, `20M` msgs) shows card slope **−0.01** (`76.1 → 75.6 ns`), while **+10 ns on host speeds it up +20%** (`76.2 → 60.9 ns` **[INF]** burst spacing).", "Second clue: delay slopes prove neither processor is maxed out. Adding fifty-nine nanoseconds on the card changes nothing, and adding ten nanoseconds on the host actually speeds it up twenty percent, our reading being burst spacing."], // 3
  [58.18, 69.82, "**Already ruled out:** Interleaved 4-core WC stays **99.99%** full 64 B writes (`13.14 vs 13.16 M/s`), and card L3 coherence is **64 B**. Neither causes the `13 M/s` throttle.", "We also cleared both obvious four-core hardware suspects: interleaved write-combining stays ninety-nine point nine nine percent full writes, and card L 3 coherence is sixty-four bytes."], // 4
  [70.17, 88.35, "**Honest status (running next on card):** We have **NOT** yet pinned down which hardware queue throttles those ~42 writes to `~75 ns/msg`. Next on hardware: `MOVDIR64B` burst pacing + host Store Buffer / CHA / IIO vs card TAD counters.", "Honest status: we have not yet identified which exact hardware queue throttles those forty-two in-flight writes to seventy-five nanoseconds per message. That experiment is queued next on our card, sweeping host burst pacing with store-buffer, C H A, I I O, and card T A D counters."], // 5
];
const DUR = 89.75; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const CARD_SLOPE_PTS: [number, number][] = [
  [0, 76.1],
  [32, 75.9],
  [45, 75.7],
  [59, 75.6],
];

const HOST_SLOPE_PTS: [number, number][] = [
  [0, 76.2],
  [10, 60.9],
  [20, 61.4],
  [40, 78.3],
];

function Body() {
  const t = useT();

  // Top KnobBar smoothly slides across the 5 investigation steps
  const kBar = prog(t, T[0], 0.5);
  const activeIdx =
    prog(t, T[2], 0.5) +
    prog(t, T[3], 0.5) +
    prog(t, T[4], 0.5) +
    prog(t, T[5], 0.5);

  // Non-overlapping crossfade envelopes (fade out before fading in next phase)
  const opAB = fade(t, T[0], T[2] - 0.2, 0.42);
  const opC2 = fade(t, T[2] + 0.25, T[3] - 0.2, 0.42);
  const opC3 = fade(t, T[3] + 0.25, T[4] - 0.2, 0.42);
  const opC4 = fade(t, T[4] + 0.25, T[5] - 0.2, 0.42);
  const opC5 = prog(t, T[5] + 0.25, 0.45);

  // Phase A (Cues 0–1): Little's Law bar chart + 4-core breakdown
  const kBar0 = prog(t, T[0] + 1.2, 0.85);
  const kBar1 = prog(t, T[1] + 0.4, 0.7);
  const kBar2 = prog(t, T[1] + 0.95, 0.7);
  const kBar3 = prog(t, T[1] + 1.5, 0.7);
  const kMath = prog(t, T[0] + 0.45, 0.6);
  const kSplit = prog(t, T[1] + 1.4, 0.65);
  const kSplitSeg = prog(t, T[1] + 1.85, 0.9);
  const kPuzzleQ = prog(t, T[1] + 2.5, 0.6);
  const kCollapseTag = prog(t, T[1] + 1.7, 0.55);

  // BarChart geometry (left card x=60..924, y=198..864)
  const bcX0 = 112;
  const bcX1 = 876;
  const bcY1 = 294;
  const bcY0 = 724;
  const bcMax = 62;
  const bcSy = (v: number) => bcY0 - ((bcY0 - bcY1) * v) / bcMax;
  const slotW = (bcX1 - bcX0) / 4;
  const c1x = bcX0 + slotW * 1.5;
  const c2x = bcX0 + slotW * 2.5;

  // Phase B (Cue 2): Timestamps SegBars
  const kRow1 = prog(t, T[2] + 0.65, 0.85);
  const kRow2 = prog(t, T[2] + 2.0, 1.0);
  const kRow3 = prog(t, T[2] + 5.2, 1.0);
  const kC2Take = prog(t, T[2] + 6.4, 0.6);

  // Phase C (Cue 3): Delay-injection slope plot
  const axX0 = 185;
  const axX1 = 1075;
  const axY1 = 304;
  const axY0 = 744;
  const sx = linScale(0, 62, axX0, axX1);
  const sy = linScale(55, 85, axY0, axY1);
  const kCardSeries = prog(t, T[3] + 0.9, 1.35);
  const kCardLbl = prog(t, T[3] + 2.1, 0.5);
  const kHostSeries = prog(t, T[3] + 2.8, 1.45);
  const kHostLbl = prog(t, T[3] + 3.9, 0.55);
  const kC3Right = prog(t, T[3] + 1.6, 0.65);
  const kC3Inf = prog(t, T[3] + 4.4, 0.6);

  // Phase D (Cue 4): Ruled out suspects
  const kSusA = prog(t, T[4] + 0.55, 0.6);
  const kSusB = prog(t, T[4] + 2.4, 0.6);
  const kC4Take = prog(t, T[4] + 4.2, 0.6);

  // Phase E (Cue 5): Honest status & queued hardware experiment
  const kBox1 = prog(t, T[5] + 1.6, 0.6);
  const kBox2 = prog(t, T[5] + 2.8, 0.6);
  const kBox3 = prog(t, T[5] + 4.0, 0.6);
  const kArr12 = prog(t, T[5] + 2.5, 0.45);
  const kArr23 = prog(t, T[5] + 3.7, 0.45);

  return (
    <>
      {/* Persistent single-line KnobBar across all 6 cues */}
      <KnobBar
        y={118}
        size={20}
        fixed={["Closed loop", "WC replies", "UNSOLVED"]}
        knob="Evidence"
        options={["Little's Law", "Timestamps", "Delay slopes", "Ruled out", "Next on card"]}
        active={activeIdx}
        k={kBar}
      />

      {/* ==================== PHASE A: CUES 0–1 (HTML cards) ==================== */}
      {opAB > 0.005 && (
        <div style={{ opacity: opAB }}>
          <Card
            x={60}
            y={198}
            w={864}
            h={666}
            border={C.line}
            title="Closed-Loop Throughput (Host ⇄ Card, WC replies)"
            titleColor={C.text}
          >
            <div
              style={{
                position: "absolute",
                left: 24,
                right: 24,
                bottom: 18,
                fontFamily: FONT,
                fontSize: 22,
                color: C.muted,
                borderTop: `1px solid ${C.line}`,
                paddingTop: 12,
                display: "flex",
                justifyContent: "space-between",
                gap: 16,
              }}
            >
              <span>
                <strong style={{ color: K.fast }}>1 core</strong> hits{" "}
                <strong style={{ color: K.fast }}>50.5 M/s</strong> (not capped)
              </span>
              <span style={{ color: K.slow, fontWeight: 700, opacity: kCollapseTag, whiteSpace: "nowrap" }}>
                4 cores stall at W = 64..256
              </span>
            </div>
          </Card>

          {/* Right Top: Little's Law Expectation */}
          <Card
            x={952}
            y={198}
            w={908}
            h={214}
            border={K.fwd}
            title="Little's Law Expectation"
            titleColor={K.fwd}
            style={{ opacity: kMath, transform: `translateY(${(1 - kMath) * 14}px)` }}
          >
            <div style={{ fontFamily: FONT, fontSize: 27, color: C.text, lineHeight: 1.45 }}>
              <span style={{ fontFamily: MONO, color: K.fwd, fontWeight: 700 }}>W = 64</span> at{" "}
              <span style={{ fontFamily: MONO, color: K.timer, fontWeight: 700 }}>0.92 µs</span> RTT should
              saturate near{" "}
              <strong style={{ color: K.fast }}>~50 M/s</strong> host rate.
            </div>
            <div
              style={{
                marginTop: 14,
                display: "inline-flex",
                alignItems: "center",
                gap: 12,
                background: "rgba(67,197,158,0.12)",
                border: `2px solid ${K.fast}`,
                borderRadius: 10,
                padding: "7px 16px",
                fontFamily: FONT,
                fontSize: 24,
                fontWeight: 700,
                color: K.fast,
              }}
            >
              <span>✓</span>
              <span>1 card core at W = 1024 DOES hit 50.5 M/s</span>
            </div>
          </Card>

          {/* Right Middle: 4-Core W = 1024 Poll Breakdown (SVG SegBars sit inside Stage) */}
          <Card
            x={952}
            y={432}
            w={908}
            h={244}
            border={C.orange}
            title="At W = 1024: Host Polling Swells Across 4 Sub-Rings"
            titleColor={C.orange}
            style={{ opacity: kSplit, transform: `translateY(${(1 - kSplit) * 16}px)` }}
          >
            <div
              style={{
                position: "absolute",
                left: 24,
                right: 24,
                bottom: 14,
                display: "flex",
                gap: 24,
                fontFamily: FONT,
                fontSize: 22,
                color: C.muted,
              }}
            >
              <span>
                <span style={{ color: K.fwd, fontWeight: 800 }}>■</span> Send:{" "}
                <strong style={{ color: C.text }}>9.2 vs 9.3 ns</strong>
              </span>
              <span>
                <span style={{ color: C.orange, fontWeight: 800 }}>■</span> Host poll:{" "}
                <strong style={{ color: C.orange }}>13.6 → 32.5 ns</strong> (4 sub-rings)
              </span>
            </div>
          </Card>

          {/* Right Bottom: Bold Red Question Box */}
          <Takeaway
            x={952}
            y={696}
            w={908}
            k={kPuzzleQ}
            color={K.slow}
            label="THE OPEN QUESTION"
            size={29}
          >
            Why do <strong style={{ color: K.slow }}>4 card cores</strong> collapse 4× to{" "}
            <strong style={{ color: K.slow }}>13.1–13.5 M/s</strong> at{" "}
            <span style={{ fontFamily: MONO, color: K.knob, whiteSpace: "nowrap" }}>W = 64..256</span>?
          </Takeaway>
        </div>
      )}

      {/* ==================== PHASE B: CUE 2 (Timestamps HTML) ==================== */}
      {opC2 > 0.005 && (
        <div style={{ opacity: opC2 }}>
          <Card
            x={60}
            y={198}
            w={1800}
            h={524}
            border={C.line}
            title="Clue 1 · Hardware Timestamps Inside the Round Trip"
            titleColor={K.timer}
          >
            {/* Top-right color legend */}
            <div
              style={{
                position: "absolute",
                right: 28,
                top: 18,
                display: "flex",
                gap: 22,
                fontFamily: FONT,
                fontSize: 22,
                fontWeight: 600,
              }}
            >
              <span style={{ color: K.fwd }}>■ Forward (Host → Card)</span>
              <span style={{ color: K.card }}>■ Card work (0.04 µs)</span>
              <span style={{ color: K.ret }}>■ Return (Card → Host)</span>
            </div>
          </Card>

          <Takeaway
            x={60}
            y={744}
            w={1800}
            k={kC2Take}
            color={K.timer}
            label="CLUE 1 VERDICT · CARD IS NEVER THE BOTTLENECK"
            size={28}
          >
            Card work stays <strong style={{ color: K.card }}>0.04 µs</strong> in every run while{" "}
            <strong style={{ color: K.slow }}>~42 msgs</strong> queue in transit (
            <strong style={{ color: K.fwd }}>3.1–3.2 µs</strong> fwd — flipping to{" "}
            <strong style={{ color: K.ret }}>0.7 / 2.7 µs</strong> return in 2 runs without pre-touch).
          </Takeaway>
        </div>
      )}

      {/* ==================== PHASE C: CUE 3 (Delay Slopes HTML) ==================== */}
      {opC3 > 0.005 && (
        <div style={{ opacity: opC3 }}>
          <Card
            x={60}
            y={198}
            w={1074}
            h={668}
            border={C.line}
            title="Clue 2 · Delay-Injection Slopes (+δ per msg, 20M msgs/run)"
            titleColor={K.fwd}
          />

          <Card
            x={1164}
            y={198}
            w={696}
            h={668}
            border={K.fast}
            title="What the Slopes Prove"
            titleColor={K.fast}
            style={{ opacity: kC3Right, transform: `translateY(${(1 - kC3Right) * 16}px)` }}
          >
            <div
              style={{
                background: "rgba(67,197,158,0.13)",
                border: `2px solid ${K.fast}`,
                borderRadius: 14,
                padding: "16px 20px",
                marginBottom: 20,
                textAlign: "center",
              }}
            >
              <div style={{ fontFamily: FONT, fontSize: 44, fontWeight: 900, color: K.fast, letterSpacing: 1 }}>
                BOTH CPUs IDLE
              </div>
              <div style={{ fontFamily: FONT, fontSize: 23, color: C.text, marginTop: 4 }}>
                Neither card nor host processor is saturated
              </div>
            </div>

            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 16,
                fontFamily: FONT,
                fontSize: 24,
                lineHeight: 1.4,
                color: C.text,
              }}
            >
              <div style={{ borderLeft: `6px solid ${K.card}`, paddingLeft: 16 }}>
                <strong style={{ color: K.card }}>Card CPU (+59 ns):</strong> slope{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: K.card }}>−0.01</span> (
                <span style={{ fontFamily: MONO }}>76.1 → 75.6 ns</span>). Extra work is completely hidden by idle wait.
              </div>

              <div style={{ borderLeft: `6px solid ${K.fwd}`, paddingLeft: 16 }}>
                <strong style={{ color: K.fwd }}>Host CPU (+10 ns):</strong>{" "}
                <span style={{ fontFamily: MONO, fontWeight: 800, color: K.fast }}>76.2 → 60.9 ns</span> (
                <strong style={{ color: K.fast }}>+20% faster!</strong>). Slowing the sender accelerates the loop.
              </div>
            </div>

            <div
              style={{
                marginTop: 22,
                opacity: kC3Inf,
                transform: `translateY(${(1 - kC3Inf) * 12}px)`,
                background: "rgba(94,234,212,0.08)",
                border: `2px solid ${K.inf}`,
                borderRadius: 12,
                padding: "14px 18px",
                fontFamily: FONT,
                fontSize: 23,
                lineHeight: 1.4,
                color: C.text,
              }}
            >
              <InfPill size={20} style={{ marginRight: 10 }} />
              Adding <strong style={{ color: K.fast }}>+10 ns</strong> on the host spaces out write bursts and
              speeds the closed loop up <strong style={{ color: K.fast }}>+20%</strong>.
            </div>
          </Card>
        </div>
      )}

      {/* ==================== PHASE D: CUE 4 (Ruled Out HTML) ==================== */}
      {opC4 > 0.005 && (
        <div style={{ opacity: opC4 }}>
          {/* Suspect A */}
          <Card
            x={60}
            y={210}
            w={880}
            h={512}
            border={K.fast}
            style={{ opacity: kSusA, transform: `translateY(${(1 - kSusA) * 18}px)` }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 18 }}>
              <span style={{ fontFamily: FONT, fontSize: 28, fontWeight: 800, color: C.text }}>
                Suspect A · Interleaved 4-Core WC
              </span>
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 22,
                  fontWeight: 900,
                  color: C.bg,
                  background: K.fast,
                  borderRadius: 999,
                  padding: "5px 18px",
                }}
              >
                INNOCENT ✓
              </span>
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.muted, lineHeight: 1.4, marginBottom: 22 }}>
              Does interleaving 4 card cores fragment write-combining stores into partial PCIe writes?
            </div>
            <div
              style={{
                background: "rgba(67,197,158,0.1)",
                border: `2px solid ${K.fast}`,
                borderRadius: 14,
                padding: "18px 24px",
                display: "flex",
                alignItems: "baseline",
                gap: 20,
                marginBottom: 20,
              }}
            >
              <span style={{ fontFamily: MONO, fontSize: 56, fontWeight: 900, color: K.fast }}>99.99%</span>
              <span style={{ fontFamily: FONT, fontSize: 25, fontWeight: 700, color: C.text }}>
                full 64 B writes (<span style={{ fontFamily: MONO }}>4 × 16 B</span> flushes whole)
              </span>
            </div>
            <div style={{ fontFamily: FONT, fontSize: 25, color: C.text, lineHeight: 1.5 }}>
              Throughput unchanged:{" "}
              <strong style={{ fontFamily: MONO, color: K.knob }}>13.14 vs 13.16 M/s</strong>
              <div style={{ fontSize: 23, color: C.muted, marginTop: 8 }}>
                Interleaving across 4 cores causes zero WC splitting and zero slowdown.
              </div>
            </div>
          </Card>

          {/* Suspect B */}
          <Card
            x={980}
            y={210}
            w={880}
            h={512}
            border={K.fast}
            style={{ opacity: kSusB, transform: `translateY(${(1 - kSusB) * 18}px)` }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 18 }}>
              <span style={{ fontFamily: FONT, fontSize: 28, fontWeight: 800, color: C.text }}>
                Suspect B · 128 B Card L3 False Sharing
              </span>
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 22,
                  fontWeight: 900,
                  color: C.bg,
                  background: K.fast,
                  borderRadius: 999,
                  padding: "5px 18px",
                }}
              >
                INNOCENT ✓
              </span>
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.muted, lineHeight: 1.4, marginBottom: 22 }}>
              Linux reports 128 B cache lines — do adjacent 64 B slots false-share across card cores?
            </div>
            <div
              style={{
                background: "rgba(67,197,158,0.1)",
                border: `2px solid ${K.fast}`,
                borderRadius: 14,
                padding: "18px 24px",
                display: "flex",
                alignItems: "baseline",
                gap: 20,
                marginBottom: 20,
              }}
            >
              <span style={{ fontFamily: MONO, fontSize: 56, fontWeight: 900, color: K.fast }}>64 B</span>
              <span style={{ fontFamily: FONT, fontSize: 25, fontWeight: 700, color: C.text }}>
                true card L3 coherence unit (NOT 128 B)
              </span>
            </div>
            <div style={{ fontFamily: FONT, fontSize: 25, color: C.text, lineHeight: 1.5 }}>
              Adjacent 64 B cells run at{" "}
              <strong style={{ fontFamily: MONO, color: K.fast }}>259.8 M/s</strong> with{" "}
              <strong style={{ fontFamily: MONO, color: K.fwd }}>0.52 M/s</strong> idle snoops.
              <div style={{ fontSize: 23, color: C.muted, marginTop: 8 }}>
                Core-to-core L3 coherence is strictly 64 B — not the source of the 13 M/s stall.
              </div>
            </div>
          </Card>

          <Takeaway
            x={60}
            y={748}
            w={1800}
            k={kC4Take}
            color={K.fast}
            label="BOTH OBVIOUS 4-CORE SUSPECTS CLEARED"
            size={28}
          >
            Neither interleaved WC fragmentation nor 128 B L3 false sharing explains the{" "}
            <strong style={{ color: K.slow }}>13.1–13.5 M/s</strong> closed-loop stall.
          </Takeaway>
        </div>
      )}

      {/* ==================== PHASE E: CUE 5 (Honest Status & Next Run HTML) ==================== */}
      {opC5 > 0.005 && (
        <div style={{ opacity: opC5 }}>
          {/* High-contrast Honest Status Banner */}
          <div
            style={{
              position: "absolute",
              left: 60,
              top: 204,
              width: 1800,
              height: 214,
              background: "rgba(249,65,68,0.10)",
              border: `3px solid ${K.slow}`,
              borderLeft: `14px solid ${K.slow}`,
              borderRadius: 18,
              padding: "20px 30px",
              boxSizing: "border-box",
              fontFamily: FONT,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 12 }}>
              <span
                style={{
                  fontSize: 20,
                  fontWeight: 900,
                  letterSpacing: 1.8,
                  color: C.bg,
                  background: K.slow,
                  borderRadius: 8,
                  padding: "5px 14px",
                }}
              >
                HONEST STATUS · NOT YET SOLVED
              </span>
              <span style={{ fontSize: 21, fontWeight: 700, letterSpacing: 1.4, color: K.knob }}>
                ACTIVE OPEN QUESTION · RUNNING NEXT ON HARDWARE
              </span>
            </div>
            <div style={{ fontSize: 38, fontWeight: 800, color: C.text, lineHeight: 1.25 }}>
              Which hardware queue throttles <span style={{ color: K.slow }}>~42</span> in-flight writes to{" "}
              <span style={{ fontFamily: MONO, color: K.slow }}>~75 ns/msg</span>?
            </div>
            <div style={{ fontSize: 23, color: C.muted, marginTop: 10 }}>
              Known so far: card work = <strong style={{ color: C.text }}>0.04 µs</strong> · both CPUs idle ·{" "}
              <strong style={{ color: K.fast }}>+10 ns</strong> host pacing gives{" "}
              <strong style={{ color: K.fast }}>+20%</strong> speedup · WC &amp; 64 B L3 cleared.
            </div>
          </div>

          {/* Section Header */}
          <div
            style={{
              position: "absolute",
              left: 60,
              top: 444,
              fontFamily: FONT,
              fontSize: 23,
              fontWeight: 800,
              letterSpacing: 2,
              color: K.knob,
            }}
          >
            NEXT EXPERIMENT QUEUED ON HARDWARE
          </div>

          {/* 3-Box Pipeline */}
          <Card
            x={60}
            y={484}
            w={572}
            h={376}
            border={K.knob}
            style={{ opacity: kBox1, transform: `translateY(${(1 - kBox1) * 18}px)` }}
          >
            <div
              style={{
                display: "inline-block",
                fontFamily: FONT,
                fontSize: 20,
                fontWeight: 900,
                color: C.bg,
                background: K.knob,
                borderRadius: 8,
                padding: "4px 12px",
                marginBottom: 14,
              }}
            >
              1 · KNOB TO SWEEP
            </div>
            <div style={{ fontFamily: FONT, fontSize: 28, fontWeight: 800, color: C.text, marginBottom: 16, lineHeight: 1.25 }}>
              <span style={{ fontFamily: MONO, color: K.knob }}>MOVDIR64B</span> Burst Pacing &amp; Ingress Parallelism
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.muted, lineHeight: 1.55 }}>
              <div>• Sweep host inter-burst spacing (+δ)</div>
              <div>
                • Parallel ingress at{" "}
                <span style={{ fontFamily: MONO, color: C.text, whiteSpace: "nowrap" }}>W = 64..256</span>
              </div>
              <div style={{ color: C.text, marginTop: 8 }}>
                Test if spacing bursts eliminates the <strong style={{ color: K.slow }}>13.1–13.5 M/s</strong> trap.
              </div>
            </div>
          </Card>

          <Card
            x={674}
            y={484}
            w={572}
            h={376}
            border={K.host}
            style={{ opacity: kBox2, transform: `translateY(${(1 - kBox2) * 18}px)` }}
          >
            <div
              style={{
                display: "inline-block",
                fontFamily: FONT,
                fontSize: 20,
                fontWeight: 900,
                color: C.bg,
                background: K.host,
                borderRadius: 8,
                padding: "4px 12px",
                marginBottom: 14,
              }}
            >
              2 · HOST-SIDE COUNTERS
            </div>
            <div style={{ fontFamily: FONT, fontSize: 28, fontWeight: 800, color: C.text, marginBottom: 16, lineHeight: 1.25 }}>
              Xeon Core, Uncore &amp; PCIe Egress
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.muted, lineHeight: 1.55 }}>
              <div>
                • <strong style={{ color: C.text }}>Store Buffer</strong> occupancy &amp; stalls
              </div>
              <div>
                • <strong style={{ color: C.text }}>CHA TOR</strong> queue depth &amp; residency
              </div>
              <div>
                • <strong style={{ color: C.text }}>IIO</strong> outbound write backpressure
              </div>
            </div>
          </Card>

          <Card
            x={1288}
            y={484}
            w={572}
            h={376}
            border={K.card}
            style={{ opacity: kBox3, transform: `translateY(${(1 - kBox3) * 18}px)` }}
          >
            <div
              style={{
                display: "inline-block",
                fontFamily: FONT,
                fontSize: 20,
                fontWeight: 900,
                color: C.bg,
                background: K.card,
                borderRadius: 8,
                padding: "4px 12px",
                marginBottom: 14,
              }}
            >
              3 · CARD-SIDE COUNTERS
            </div>
            <div style={{ fontFamily: FONT, fontSize: 28, fontWeight: 800, color: C.text, marginBottom: 16, lineHeight: 1.25 }}>
              OCTEON 10 Inbound Path &amp; TAD
            </div>
            <div style={{ fontFamily: FONT, fontSize: 24, color: C.muted, lineHeight: 1.55 }}>
              <div>
                • <strong style={{ color: C.text }}>TAD</strong> coherence snoops &amp; allocations
              </div>
              <div>
                • Inbound write-queue backpressure stalls
              </div>
              <div>
                • Pin down fwd ↔ ret flip (<span style={{ fontFamily: MONO, color: C.text }}>0.7 / 2.7 µs</span>)
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* ==================== SVG STAGE (Charts, SegBars, Arrows) ==================== */}
      <Stage>
        {/* Phase A SVG: BarChart + 4-core W=1024 SegBars */}
        {opAB > 0.005 && (
          <g opacity={opAB}>
            {/* Little's Law dashed target line with right-aligned label (no collision with Bar 0) */}
            <line
              x1={bcX0}
              y1={bcSy(50)}
              x2={bcX1}
              y2={bcSy(50)}
              stroke={K.fwd}
              strokeWidth={2.5}
              strokeDasharray="10 7"
            />
            <text
              x={bcX1 - 6}
              y={bcSy(50) - 12}
              textAnchor="end"
              fontFamily={FONT}
              fontSize={22}
              fontWeight={700}
              fill={K.fwd}
            >
              Little&apos;s Law target (~50 M/s · W = 64, 0.92 µs)
            </text>

            <BarChart
              x0={bcX0}
              x1={bcX1}
              y0={bcY0}
              y1={bcY1}
              max={bcMax}
              size={27}
              labelSize={23}
              barW={116}
              bars={[
                { label: "1 core", sub: "W = 1024", value: 50.5, color: K.fast, text: "50.5 M/s" },
                { label: "4 cores", sub: "W = 64", value: 13.5, color: K.slow, text: "13.5 M/s" },
                { label: "4 cores", sub: "W = 256", value: 13.0, color: K.slow, text: "13.0 M/s" },
                { label: "4 cores", sub: "W = 1024", value: 23.9, color: C.orange, text: "23.9 M/s" },
              ]}
              k={(i) => [kBar0, kBar1, kBar2, kBar3][i]}
            />

            {/* Red collapse callout above bars 1 & 2 */}
            {kCollapseTag > 0.01 && (
              <g opacity={kCollapseTag}>
                <path
                  d={`M${c1x - 45} ${bcSy(13.5) - 52} L${c1x - 45} ${bcSy(13.5) - 66} L${c2x + 45} ${bcSy(13.5) - 66} L${c2x + 45} ${bcSy(13.5) - 52}`}
                  fill="none"
                  stroke={K.slow}
                  strokeWidth={2.5}
                />
                <text
                  x={(c1x + c2x) / 2}
                  y={bcSy(13.5) - 78}
                  textAnchor="middle"
                  fontFamily={FONT}
                  fontSize={24}
                  fontWeight={800}
                  fill={K.slow}
                >
                  4× collapse (13.1–13.5 M/s)
                </text>
              </g>
            )}

            {/* W = 1024 1-core vs 4-core send/poll SegBars inside right-middle card */}
            {kSplit > 0.01 && (
              <g opacity={kSplit}>
                <text
                  x={1086}
                  y={525}
                  textAnchor="end"
                  fontFamily={FONT}
                  fontSize={24}
                  fontWeight={700}
                  fill={K.fast}
                >
                  1 core
                </text>
                <SegBar
                  x={1104}
                  y={496}
                  h={44}
                  px={13.5}
                  k={kSplitSeg}
                  size={21}
                  segs={[
                    { label: "send", value: 9.2, color: K.fwd, text: "9.2 ns" },
                    { label: "poll", value: 13.6, color: K.ret, text: "13.6 ns" },
                  ]}
                />
                <text
                  x={1104 + (9.2 + 13.6) * 13.5 + 16}
                  y={525}
                  fontFamily={MONO}
                  fontSize={23}
                  fontWeight={800}
                  fill={K.fast}
                  opacity={kSplitSeg}
                >
                  50.5 M/s
                </text>

                <text
                  x={1086}
                  y={593}
                  textAnchor="end"
                  fontFamily={FONT}
                  fontSize={24}
                  fontWeight={700}
                  fill={C.orange}
                >
                  4 cores
                </text>
                <SegBar
                  x={1104}
                  y={564}
                  h={44}
                  px={13.5}
                  k={kSplitSeg}
                  size={21}
                  segs={[
                    { label: "send", value: 9.3, color: K.fwd, text: "9.3 ns" },
                    { label: "poll", value: 32.5, color: C.orange, text: "poll: 13.6 → 32.5 ns" },
                  ]}
                />
                <text
                  x={1104 + (9.3 + 32.5) * 13.5 + 16}
                  y={593}
                  fontFamily={MONO}
                  fontSize={23}
                  fontWeight={800}
                  fill={C.orange}
                  opacity={kSplitSeg}
                >
                  23.9 M/s
                </text>
              </g>
            )}
          </g>
        )}

        {/* Phase B SVG: Cue 2 Timestamps SegBars */}
        {opC2 > 0.005 && (
          <g opacity={opC2}>
            {/* Row 1: W = 1 baseline (0.92 µs) */}
            <g opacity={prog(t, T[2] + 0.4, 0.45)}>
              <text x={380} y={328} textAnchor="end" fontFamily={FONT} fontSize={26} fontWeight={800} fill={C.text}>
                W = 1 baseline
              </text>
              <text x={380} y={358} textAnchor="end" fontFamily={MONO} fontSize={22} fill={K.fast}>
                0.92 µs RTT
              </text>
              <SegBar
                x={402}
                y={306}
                h={56}
                px={310}
                k={kRow1}
                size={22}
                segs={[
                  { label: "fwd", value: 0.45, color: K.fwd, text: "0.45" },
                  { label: "card", value: 0.04, color: K.card, text: "0.04 µs" },
                  { label: "ret", value: 0.43, color: K.ret, text: "0.43" },
                ]}
              />
              <text
                x={402 + 0.92 * 310 + 22}
                y={342}
                fontFamily={FONT}
                fontSize={24}
                fontWeight={700}
                fill={K.fast}
                opacity={kRow1}
              >
                0.92 µs total · no queueing
              </text>
            </g>

            {/* Row 2: W = 64 (13.5 M/s, 3.7 µs) */}
            <g opacity={prog(t, T[2] + 1.8, 0.45)}>
              <text x={380} y={462} textAnchor="end" fontFamily={FONT} fontSize={26} fontWeight={800} fill={K.slow}>
                W = 64 · 4 cores
              </text>
              <text x={380} y={492} textAnchor="end" fontFamily={MONO} fontSize={22} fill={C.muted}>
                13.5 M/s · 3.7 µs
              </text>
              <SegBar
                x={402}
                y={440}
                h={56}
                px={310}
                k={kRow2}
                size={23}
                segs={[
                  { label: "fwd", value: 3.15, color: K.fwd, text: "3.1–3.2 µs forward (~42 msgs queued)" },
                  { label: "card", value: 0.04, color: K.card, text: "0.04 µs card" },
                  { label: "ret", value: 0.51, color: K.ret, text: "~0.5 µs" },
                ]}
              />
              <text
                x={1586}
                y={464}
                fontFamily={MONO}
                fontSize={26}
                fontWeight={800}
                fill={K.slow}
                opacity={kRow2}
              >
                3.7 µs RTT
              </text>
              <text
                x={1586}
                y={494}
                fontFamily={FONT}
                fontSize={22}
                fill={C.muted}
                opacity={kRow2}
              >
                ~42 queued
              </text>
            </g>

            {/* Row 3: Honest caveat (2 runs without pre-touch, flipped!) */}
            <g opacity={prog(t, T[2] + 4.9, 0.45)}>
              <text x={380} y={602} textAnchor="end" fontFamily={FONT} fontSize={26} fontWeight={800} fill={K.knob}>
                2 runs (no pre-touch)
              </text>
              <text x={380} y={632} textAnchor="end" fontFamily={MONO} fontSize={22} fill={C.muted}>
                13.4 M/s · 3.8 µs
              </text>
              <SegBar
                x={402}
                y={580}
                h={56}
                px={310}
                k={kRow3}
                size={23}
                segs={[
                  { label: "fwd", value: 0.7, color: K.fwd, text: "0.7 µs" },
                  { label: "card", value: 0.04, color: K.card, text: "0.04 µs" },
                  { label: "ret", value: 2.7, color: K.ret, text: "2.7 µs return (FLIPPED!)" },
                ]}
              />
              <text
                x={1586}
                y={604}
                fontFamily={MONO}
                fontSize={26}
                fontWeight={800}
                fill={K.knob}
                opacity={kRow3}
              >
                3.8 µs RTT
              </text>
              <text
                x={1586}
                y={634}
                fontFamily={FONT}
                fontSize={22}
                fill={C.muted}
                opacity={kRow3}
              >
                13.4 M/s
              </text>
            </g>

            {/* Subtle time scale ticks at bottom of card */}
            <line x1={402} y1={676} x2={402 + 3.8 * 310} y2={676} stroke={C.line} strokeWidth={2} />
            {[0, 1, 2, 3].map((u) => (
              <g key={u}>
                <line x1={402 + u * 310} y1={676} x2={402 + u * 310} y2={684} stroke={C.dim} strokeWidth={2} />
                <text
                  x={402 + u * 310}
                  y={706}
                  textAnchor="middle"
                  fontFamily={MONO}
                  fontSize={20}
                  fill={C.muted}
                >
                  {u} µs
                </text>
              </g>
            ))}
          </g>
        )}

        {/* Phase C SVG: Cue 3 Delay-Injection Slope Plot */}
        {opC3 > 0.005 && (
          <g opacity={opC3}>
            <Axes
              x0={axX0}
              x1={axX1}
              y0={axY0}
              y1={axY1}
              sx={sx}
              sy={sy}
              xticks={[
                [0, "0"],
                [10, "+10"],
                [20, "+20"],
                [30, "+30"],
                [40, "+40"],
                [50, "+50"],
                [60, "+60"],
              ]}
              yticks={[
                [55, "55"],
                [60, "60"],
                [65, "65"],
                [70, "70"],
                [75, "75"],
                [80, "80"],
                [85, "85"],
              ]}
              xlabel="Injected delay δ (ns / msg)"
              ylabel="Observed period (ns / msg)"
              size={22}
            />

            {/* Baseline dashed guide at ~76 ns */}
            <line
              x1={axX0}
              y1={sy(76.1)}
              x2={axX1}
              y2={sy(76.1)}
              stroke={C.dim}
              strokeWidth={2}
              strokeDasharray="8 7"
            />

            {/* Orange Series: Card CPU delay (flat) */}
            <Series pts={CARD_SLOPE_PTS} sx={sx} sy={sy} k={kCardSeries} color={K.card} w={5} r={8} />
            {kCardLbl > 0.01 && (
              <g opacity={kCardLbl}>
                <rect
                  x={sx(24)}
                  y={sy(84.4)}
                  width={470}
                  height={44}
                  rx={10}
                  fill="rgba(248,150,30,0.16)"
                  stroke={K.card}
                  strokeWidth={2}
                />
                <text
                  x={sx(24) + 235}
                  y={sy(84.4) + 23}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontFamily={FONT}
                  fontSize={22}
                  fontWeight={800}
                  fill={K.card}
                >
                  Card CPU: slope −0.01 (76.1 → 75.6 ns)
                </text>
                <Arrow
                  x1={sx(52)}
                  y1={sy(81.2)}
                  x2={sx(52)}
                  y2={sy(76.4)}
                  color={K.card}
                  w={3}
                  head={10}
                />
              </g>
            )}

            {/* Cyan Series: Host CPU delay (dips +20% faster!) */}
            <Series pts={HOST_SLOPE_PTS} sx={sx} sy={sy} k={kHostSeries} color={K.fwd} w={5} r={8} />
            {kHostLbl > 0.01 && (
              <g opacity={kHostLbl}>
                <circle
                  cx={sx(10)}
                  cy={sy(60.9)}
                  r={15}
                  fill="none"
                  stroke={K.fast}
                  strokeWidth={3.5}
                />
                <rect
                  x={sx(19)}
                  y={sy(58.8)}
                  width={462}
                  height={44}
                  rx={10}
                  fill="rgba(67,197,158,0.16)"
                  stroke={K.fast}
                  strokeWidth={2.5}
                />
                <text
                  x={sx(19) + 231}
                  y={sy(58.8) + 23}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontFamily={FONT}
                  fontSize={23}
                  fontWeight={800}
                  fill={K.fast}
                >
                  Host +10 ns → 60.9 ns (+20% FASTER!)
                </text>
                <Arrow
                  x1={sx(19)}
                  y1={sy(57.8)}
                  x2={sx(11.4)}
                  y2={sy(60.4)}
                  color={K.fast}
                  w={3}
                  head={10}
                />
                <text
                  x={sx(31.5)}
                  y={sy(69.2)}
                  fontFamily={FONT}
                  fontSize={22}
                  fontWeight={800}
                  fill={K.fwd}
                >
                  Host CPU
                </text>
              </g>
            )}
          </g>
        )}

        {/* Phase E SVG: Connecting arrows between the 3 hardware experiment boxes */}
        {opC5 > 0.005 && (
          <g opacity={opC5}>
            <Arrow x1={636} y1={672} x2={670} y2={672} color={K.knob} w={4} head={12} k={kArr12} />
            <Arrow x1={1250} y1={672} x2={1284} y2={672} color={K.knob} w={4} head={12} k={kArr23} />
          </g>
        )}
      </Stage>
    </>
  );
}

export const S05: SceneDef = {
  id: "S05",
  title: "Open puzzle · Why 4-core closed loop stalls",
  stage: 3,
  source: "Active investigation · timestamps, delay slopes & WC layout · next on card",
  dur: DUR,
  cues,
  C: Body,
};
