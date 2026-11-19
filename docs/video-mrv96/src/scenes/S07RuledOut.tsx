import React from "react";
import {
  Arrow,
  C,
  CORE,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  Packet,
  Pill,
  SceneDef,
  Stage,
  Txt,
  clamp01,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import { BarChart, KnobBar, NextQuestion } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 13.02, "**Suspect 5a (Write-combining):** With 4 card cores, replies scatter across **64 KB host regions** by tag. Does interleaving break 64-byte write-combining?", "Experiment five clears both four-core suspects, one knob each. First: with four card cores, replies scatter across sixty-four kilobyte host regions. Does interleaving break write combining?"], // 0
  [13.37, 30.39, "**Fixed:** closed loop, 4 cores, `W = 64`. **One knob:** contiguous vs interleaved. **Result:** **1.0001 vs 1.0000** full 64 B writes/reply (**99.99%**), **13.14 vs 13.16 M/s**.", "Fixed: closed loop, four cores, window sixty-four. One knob: contiguous versus interleaved regions. Host counters measure ninety-nine point nine nine percent full sixty-four byte writes in both, at thirteen point one four versus thirteen point one six million."], // 1
  [30.74, 44.46, "**Why 5a is innocent:** Each 64 B reply fills one whole WC line (`4 × 16 B`) and flushes before the next starts. (Caveat: `W = 1024` sees an unexplained `−3%`, `21.88 → 21.22`.)", "Why? Each sixty-four byte reply fills one complete combining buffer by itself and flushes whole. Caveat: window ten twenty-four sees an unexplained three percent dip, but the counter proves write combining stays intact."], // 2
  [44.81, 55.65, "**Suspect 5b (128 B false sharing):** Linux reports a **128-byte** card L3 coherency line — if true, cores touching adjacent 64 B slots would thrash.", "Second suspect: Linux reports a one hundred twenty-eight byte coherency line for card L 3. If true, cores writing neighboring sixty-four byte slots would thrash."], // 3
  [56.00, 77.54, "**Fixed:** 2 card cores rewriting own 64 B slots. **One knob:** spacing `d` (`0 B`–`4 KB`). **0 B**: **72.3 M/s** (`17.8 M/s` snoops). **64 B**: full **259.8 M/s** (`0.52 M/s` idle snoops) — same as `128 B–4 KB`.", "Fixed: two card cores rewriting their own sixty-four byte slots. One knob: spacing from zero bytes to four kilobytes. Zero bytes drops to seventy-two point three million with seventeen point eight million snoops. At sixty-four bytes, both hit two hundred fifty-nine point eight million with idle snoops, identical to four kilobytes."], // 4
  [77.89, 91.37, "**Next:** Card L3 coherence is **64 B** (core-to-core; PCIe beside polling untested). Both 4-core suspects are innocent — one test remains: **break the closed loop**.", "So card-core coherence is sixty-four bytes, though P C I e beside a polling core remains untested. Both four-core suspects are innocent. One decisive test remains: break closed-loop lockstep itself."], // 5
];
const DUR = 92.77; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // ---- Part 5a (cues 0–2) vs Part 5b (cues 3–5) ----
  const p5a = fade(t, T[0], T[3] - 0.15, 0.4);
  const p5b = prog(t, T[3] + 0.25, 0.45);

  // Part 5a sub-phases
  const p5aDiag = fade(t, T[0], T[2] - 0.1, 0.4);
  const p5aHyp = fade(t, T[0], T[1] - 0.1, 0.4);
  const p5aRes = fade(t, T[1] + 0.25, T[3] - 0.15, 0.4);
  const p5aZoom = fade(t, T[2] + 0.25, T[3] - 0.15, 0.4);
  const p5aCaveat = fade(t, T[2] + 0.45, T[3] - 0.15, 0.4);

  // Knob 5a: 0 = Contiguous, 1 = Interleaved (64 KB)
  const knob5a =
    t < T[0] + 6.0
      ? 0
      : t < T[1]
        ? prog(t, T[0] + 6.0, 0.8) * 0.95
        : lerp(0.95, 1, prog(t, T[1], 0.5));
  const isInterleaved = knob5a > 0.5;

  // Part 5b sub-phases
  const p5bHyp = fade(t, T[3] + 0.25, T[4] - 0.1, 0.4);
  const p5bData = prog(t, T[4] + 0.25, 0.45);
  const p5bVerdict = prog(t, T[5] + 0.15, 0.45);
  const p5bNext = prog(t, T[5] + 0.55, 0.45);

  // Knob 5b: steps 0 -> 1 -> 2 -> 3 -> 4 during cue 4, then settles at 1 (64 B) on cue 5
  const knob5b =
    t < T[4]
      ? 1
      : t < T[4] + 5.5
        ? 0
        : t < T[4] + 10.5
          ? prog(t, T[4] + 5.5, 0.7)
          : t < T[4] + 14.0
            ? 1 + prog(t, T[4] + 10.5, 0.7)
            : t < T[4] + 16.5
              ? 2 + prog(t, T[4] + 14.0, 0.7) * 2
              : t < T[5]
                ? 4
                : lerp(4, 1, prog(t, T[5], 0.8));

  // Part 5a left diagram geometry (4 card cores -> 4 x 64 KB host regions)
  const coreY = [295, 410, 525, 640];
  const regY = [295, 410, 525, 640];

  // Part 5a Cue 2 WC buffer animation cycle (4.0s loop)
  const wcCycle = (rt % 4.0) / 4.0;
  // 0..0.60: 4 x 16 B stores snap in; 0.60..0.78: line complete glow; 0.78..0.96: flush full 64 B over PCIe
  const wcFlush = clamp01((wcCycle - 0.74) / 0.22);
  const wcComplete = wcCycle >= 0.58 && wcCycle < 0.80;

  // Part 5b Cue 3 ping-pong bounce animation
  const bounce = 0.5 + 0.5 * Math.sin(rt * 6.0);

  return (
    <>
      {/* ======================= PART 5a HTML ======================= */}
      {p5a > 0.01 && (
        <KnobBar
          y={118}
          size={23}
          fixed={["Closed loop", "4 card cores", "W = 64"]}
          knob="Reply layout"
          options={["Contiguous (1 seg)", "Interleaved (64 KB)"]}
          active={knob5a}
          k={p5a}
        />
      )}

      {/* Cue 0 right hypothesis card */}
      {p5aHyp > 0.01 && (
        <Card
          x={970}
          y={220}
          w={890}
          h={560}
          border={K.knob}
          title="SUSPECT 5a · WRITE-COMBINING"
          titleColor={K.knob}
          style={{ opacity: p5aHyp, transform: `translateY(${(1 - p5aHyp) * 18}px)` }}
        >
          <div style={{ fontSize: 36, fontWeight: 700, color: C.text, lineHeight: 1.32, marginTop: 10 }}>
            Does jumping across <span style={{ color: K.knob }}>64 KB</span> host regions break{" "}
            <span style={{ color: K.ret }}>64-byte</span> write-combining?
          </div>
          <div style={{ fontSize: 26, color: C.muted, lineHeight: 1.45, marginTop: 26 }}>
            With <strong style={{ color: C.text }}>4 card cores</strong> replying by tag, consecutive writes target
            different <strong style={{ color: C.text }}>64 KB</strong> segments instead of one contiguous ring.
          </div>
          <div
            style={{
              marginTop: 34,
              padding: "20px 24px",
              borderRadius: 14,
              background: "rgba(249,199,79,0.08)",
              border: `2px solid ${K.knob}66`,
              fontSize: 25,
              color: C.text,
              lineHeight: 1.45,
            }}
          >
            <strong style={{ color: K.knob }}>Prediction if guilty:</strong> WC buffers evict early as partial{" "}
            <span style={{ fontFamily: MONO, color: K.slow }}>&lt; 64 B</span> PCIe writes, tanking reply efficiency.
          </div>
        </Card>
      )}

      {/* Cues 1–2 right results header & stats */}
      {p5aRes > 0.01 && (
        <>
          <Card
            x={970}
            y={210}
            w={435}
            h={168}
            border={K.fast}
            style={{ opacity: p5aRes, padding: "18px 22px" }}
          >
            <div style={{ fontSize: 21, fontWeight: 800, letterSpacing: 1.5, color: K.fast }}>
              FULL 64 B WRITES / REPLY
            </div>
            <div style={{ fontSize: 52, fontWeight: 800, color: K.fast, marginTop: 6, fontVariantNumeric: "tabular-nums" }}>
              99.99%
            </div>
            <div style={{ fontSize: 23, color: C.text, marginTop: 2, fontFamily: MONO }}>
              1.0001 vs 1.0000
            </div>
          </Card>

          <Card
            x={1425}
            y={210}
            w={435}
            h={168}
            border={K.fast}
            style={{ opacity: p5aRes, padding: "18px 22px" }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 21, fontWeight: 800, letterSpacing: 1.5, color: K.fast }}>
                VERDICT · 5a
              </span>
              <span
                style={{
                  fontSize: 20,
                  fontWeight: 800,
                  color: C.bg,
                  background: K.fast,
                  padding: "3px 12px",
                  borderRadius: 999,
                }}
              >
                INNOCENT ✓
              </span>
            </div>
            <div
              style={{
                fontSize: 34,
                fontWeight: 800,
                color: C.text,
                marginTop: 16,
                whiteSpace: "nowrap",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              13.14 <span style={{ color: C.muted, fontSize: 26 }}>vs</span> 13.16 M/s
            </div>
            <div style={{ fontSize: 22, color: C.muted, marginTop: 10 }}>
              Zero WC penalty at W = 64
            </div>
          </Card>

          <Card
            x={970}
            y={394}
            w={890}
            h={324}
            border={C.line}
            style={{ opacity: p5aRes, padding: "14px 20px" }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 22, fontWeight: 700, color: C.muted }}>
              <span style={{ width: 420, textAlign: "center" }}>Full 64 B writes / reply</span>
              <span style={{ width: 420, textAlign: "center" }}>Closed-loop throughput (W = 64)</span>
            </div>
          </Card>
        </>
      )}

      {/* Cue 2 honest caveat note under right chart */}
      {p5aCaveat > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 970,
            top: 734,
            width: 890,
            padding: "16px 24px",
            boxSizing: "border-box",
            borderRadius: 14,
            background: "rgba(249,199,79,0.08)",
            border: `2px solid ${K.knob}`,
            borderLeft: `10px solid ${K.knob}`,
            fontFamily: FONT,
            opacity: p5aCaveat,
            transform: `translateY(${(1 - p5aCaveat) * 14}px)`,
          }}
        >
          <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 1.5, color: K.knob, marginBottom: 4 }}>
            HONEST CAVEAT
          </div>
          <div style={{ fontSize: 25, color: C.text, lineHeight: 1.35 }}>
            At <span style={{ fontFamily: MONO, color: K.knob }}>W = 1024</span>, interleaving dips{" "}
            <strong style={{ color: K.knob }}>−3%</strong> ({" "}
            <span style={{ fontFamily: MONO }}>21.88 → 21.22 M/s</span>, unexplained), while WC stays{" "}
            <strong style={{ color: K.fast }}>99.99%</strong> full.
          </div>
        </div>
      )}

      {/* Cue 2 left zoomed WC buffer explanation card frame */}
      {p5aZoom > 0.01 && (
        <Card
          x={60}
          y={210}
          w={880}
          h={640}
          border={K.ret}
          title="WHY 5a IS INNOCENT · ONE 64-BYTE WC BUFFER"
          titleColor={K.ret}
          style={{ opacity: p5aZoom, transform: `translateY(${(1 - p5aZoom) * 16}px)` }}
        >
          <div style={{ fontSize: 25, color: C.muted, marginTop: 4 }}>
            Each <strong style={{ color: C.text }}>64 B</strong> reply fills one aligned combining line (
            <span style={{ fontFamily: MONO, color: K.ret }}>4 × 16 B = 64 B</span>) and flushes complete over PCIe
            before the next reply begins.
          </div>
        </Card>
      )}

      {/* ======================= PART 5b HTML ======================= */}
      {p5b > 0.01 && (
        <KnobBar
          y={118}
          size={23}
          fixed={["2 card cores", "Own 64 B slot each"]}
          knob="Slot spacing d"
          options={["0 B", "64 B", "128 B", "256 B", "4 KB"]}
          active={knob5b}
          k={p5b}
        />
      )}

      {/* Cue 3 question callout */}
      {p5bHyp > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 140,
            top: 660,
            width: 1640,
            padding: "22px 32px",
            boxSizing: "border-box",
            borderRadius: 16,
            background: "rgba(249,199,79,0.09)",
            border: `2.5px solid ${K.knob}`,
            borderLeft: `12px solid ${K.knob}`,
            fontFamily: FONT,
            opacity: p5bHyp,
            transform: `translateY(${(1 - p5bHyp) * 18}px)`,
          }}
        >
          <div style={{ fontSize: 21, fontWeight: 800, letterSpacing: 2, color: K.knob, marginBottom: 6 }}>
            SUSPECT 5b · FALSE SHARING HYPOTHESIS
          </div>
          <div style={{ fontSize: 31, fontWeight: 600, color: C.text, lineHeight: 1.32 }}>
            If card L3 coherence is <span style={{ color: K.knob }}>128 B</span>, adjacent{" "}
            <span style={{ color: K.fwd }}>64 B</span> slots (<span style={{ fontFamily: MONO }}>d = 64 B</span>) will
            thrash just like sharing the same slot!
          </div>
        </div>
      )}

      {/* Cues 4–5 chart panel frames + titles + highlight badge (HTML behind Stage) */}
      {p5bData > 0.01 && (
        <>
          <Card x={60} y={198} w={915} h={508} border={C.line} style={{ opacity: p5bData, padding: "14px 20px" }}>
            <div style={{ fontSize: 26, fontWeight: 700, color: C.text, textAlign: "center" }}>
              Writes per core (M/s)
            </div>
            <div
              style={{
                marginTop: 8,
                textAlign: "center",
                opacity: prog(t, T[4] + 5.6, 0.5),
              }}
            >
              <span
                style={{
                  display: "inline-block",
                  fontSize: 20,
                  fontWeight: 700,
                  color: K.fast,
                  border: `2px solid ${K.fast}`,
                  background: "rgba(67,197,158,0.16)",
                  borderRadius: 999,
                  padding: "3px 16px",
                }}
              >
                d = 64 B inside 128 B block — ZERO false sharing!
              </span>
            </div>
          </Card>

          <Card x={1000} y={198} w={860} h={508} border={C.line} style={{ opacity: p5bData, padding: "14px 20px" }}>
            <div style={{ fontSize: 26, fontWeight: 700, color: C.text, textAlign: "center" }}>
              Card L3 coherence snoops (M/s)
            </div>
          </Card>
        </>
      )}

      {/* Cue 5 Verdict badge + NextQuestion */}
      {p5bVerdict > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 60,
            top: 722,
            width: 1800,
            padding: "12px 22px",
            boxSizing: "border-box",
            borderRadius: 14,
            background: "rgba(67,197,158,0.10)",
            border: `2px solid ${K.fast}`,
            display: "flex",
            alignItems: "center",
            gap: 18,
            fontFamily: FONT,
            opacity: p5bVerdict,
            transform: `translateY(${(1 - p5bVerdict) * 14}px)`,
          }}
        >
          <span
            style={{
              fontSize: 19,
              fontWeight: 800,
              letterSpacing: 1.5,
              color: C.bg,
              background: K.fast,
              borderRadius: 8,
              padding: "5px 14px",
              whiteSpace: "nowrap",
            }}
          >
            VERDICT · 5b INNOCENT ✓
          </span>
          <span style={{ fontSize: 25, fontWeight: 600, color: C.text }}>
            Card L3 coherence = <strong style={{ color: K.fast }}>64 B</strong>{" "}
            <span style={{ color: C.muted }}>(core-to-core; PCIe beside polling untested)</span>
          </span>
        </div>
      )}

      {p5bNext > 0.01 && (
        <NextQuestion x={60} y={806} w={1800} k={p5bNext} size={25}>
          Both 4-core hardware suspects are innocent! One decisive test remains:{" "}
          <strong style={{ color: K.knob }}>break closed-loop lockstep itself</strong>.
        </NextQuestion>
      )}

      {/* ======================= SVG STAGE ======================= */}
      <Stage>
        {/* ---- Part 5a Left Diagram: 4 Card Cores -> Host 64 KB Regions (Cues 0–1) ---- */}
        {p5aDiag > 0.01 && (
          <g opacity={p5aDiag} fontFamily={FONT}>
            {/* Container panel */}
            <rect x={60} y={210} width={880} height={630} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={92} y={248} fontSize={21} fontWeight={800} letterSpacing={1.8} fill={K.card}>
              4 CARD CORES
            </text>
            <text x={555} y={248} fontSize={21} fontWeight={800} letterSpacing={1.8} fill={K.host}>
              {isInterleaved ? "HOST: 4 × 64 KB REGIONS (BY TAG)" : "HOST: CONTIGUOUS (1 × 64 KB SEG)"}
            </text>

            {/* PCIe divider */}
            <line x1={415} y1={266} x2={415} y2={750} stroke={C.dim} strokeWidth={2} strokeDasharray="8 8" />

            {/* 4 Card Cores */}
            {coreY.map((cy, i) => (
              <g key={`c${i}`}>
                <rect
                  x={92}
                  y={cy - 34}
                  width={185}
                  height={68}
                  rx={12}
                  fill={`${CORE[i]}1a`}
                  stroke={CORE[i]}
                  strokeWidth={2.5}
                />
                <text x={184} y={cy + 8} textAnchor="middle" fontSize={25} fontWeight={800} fill={CORE[i]}>
                  Card Core {i}
                </text>
              </g>
            ))}

            {/* 4 Host 64 KB Regions */}
            {regY.map((ry, i) => {
              const activeReg = isInterleaved || i === 0;
              const col = isInterleaved ? CORE[i] : i === 0 ? K.ret : C.dim;
              return (
                <g key={`r${i}`} opacity={activeReg ? 1 : 0.35}>
                  <rect
                    x={555}
                    y={ry - 34}
                    width={350}
                    height={68}
                    rx={12}
                    fill={activeReg ? `${col}18` : "#0f1526"}
                    stroke={col}
                    strokeWidth={activeReg ? 2.5 : 1.5}
                    strokeDasharray={activeReg ? undefined : "6 6"}
                  />
                  <text x={580} y={ry - 4} fontSize={22} fontWeight={700} fill={activeReg ? C.text : C.muted}>
                    {isInterleaved ? `64 KB Region ${i}` : i === 0 ? "Single 64 KB Region" : `Unused Region ${i}`}
                  </text>
                  <text x={580} y={ry + 22} fontSize={19} fontFamily={MONO} fill={activeReg ? col : C.dim}>
                    {isInterleaved ? `tag % 4 = ${i} · 64 B slots` : i === 0 ? "all 4 cores → 1 seg" : "—"}
                  </text>
                </g>
              );
            })}

            {/* Routing paths & moving packets */}
            {coreY.map((cy, i) => {
              const targetY = lerp(regY[0], regY[i], clamp01(knob5a));
              const pts: [number, number][] = [
                [277, cy],
                [395, cy],
                [445, targetY],
                [555, targetY],
              ];
              const pk = (rt * 0.65 + i * 0.25) % 1;
              return (
                <g key={`route${i}`}>
                  <polyline
                    points={pts.map((p) => p.join(",")).join(" ")}
                    fill="none"
                    stroke={CORE[i]}
                    strokeWidth={3}
                    opacity={0.55}
                  />
                  <Packet pts={pts} k={pk} color={CORE[i]} r={9} trail={false} />
                </g>
              );
            })}

            {/* Status strip at bottom of left panel */}
            <rect x={92} y={770} width={270} height={44} rx={10} fill="rgba(249,199,79,0.12)" stroke={K.knob} strokeWidth={1.8} />
            <text x={227} y={799} textAnchor="middle" fontSize={21} fontWeight={700} fill={K.knob}>
              {isInterleaved ? "Interleaved by tag" : "Contiguous layout"}
            </text>
            <text x={730} y={799} textAnchor="middle" fontSize={21} fontWeight={700} fill={C.muted}>
              PCIe 64 B WC writes
            </text>
          </g>
        )}

        {/* ---- Part 5a Cue 1–2 Right BarCharts ---- */}
        {p5aRes > 0.01 && (
          <g opacity={p5aRes}>
            {/* Subtle 1.0 ideal dashed reference line (no colliding text over bar 0) */}
            <line
              x1={995}
              y1={638 - (173 * 1.0) / 1.25}
              x2={1395}
              y2={638 - (173 * 1.0) / 1.25}
              stroke={C.muted}
              strokeWidth={2}
              strokeDasharray="8 6"
              opacity={0.55}
            />
            <BarChart
              x0={995}
              x1={1395}
              y1={465}
              y0={638}
              max={1.25}
              dec={4}
              size={24}
              labelSize={22}
              barW={92}
              bars={[
                { label: "Contiguous", value: 1.0001, color: K.fast, text: "1.0001" },
                { label: "Interleaved", value: 1.0000, color: K.fast, text: "1.0000" },
              ]}
              k={(i) => prog(t, T[1] + 0.5 + i * 0.25, 0.7)}
            />
            <BarChart
              x0={1445}
              x1={1835}
              y1={465}
              y0={638}
              max={18}
              dec={2}
              unit=" M/s"
              size={24}
              labelSize={22}
              barW={92}
              bars={[
                { label: "Contiguous", value: 13.14, color: K.fwd, text: "13.14" },
                { label: "Interleaved", value: 13.16, color: K.ret, text: "13.16" },
              ]}
              k={(i) => prog(t, T[1] + 0.8 + i * 0.25, 0.7)}
            />
          </g>
        )}

        {/* ---- Part 5a Cue 2 Zoomed 64 B WC Buffer SVG ---- */}
        {p5aZoom > 0.01 && (
          <g opacity={p5aZoom} fontFamily={FONT}>
            {/* WC Buffer box */}
            <text x={100} y={392} fontSize={22} fontWeight={800} letterSpacing={1.4} fill={K.ret}>
              CARD WRITE-COMBINING BUFFER (1 ALIGNED 64 B LINE)
            </text>
            <rect
              x={100}
              y={410}
              width={790}
              height={146}
              rx={16}
              fill="#0f172a"
              stroke={wcComplete ? K.fast : K.ret}
              strokeWidth={wcComplete ? 4 : 2.5}
            />

            {/* 4 x 16 B slots inside the 64 B buffer */}
            {[0, 1, 2, 3].map((i) => {
              const slotStart = 0.04 + i * 0.13;
              const snap = clamp01((wcCycle - slotStart) / 0.10);
              const slideOut = wcFlush * 320;
              const slotOpacity = snap * (1 - clamp01((wcFlush - 0.55) / 0.45));
              const sx = 122 + i * 190;
              const yOffset = (1 - snap) * -42;
              return (
                <g key={`wc${i}`}>
                  {/* empty slot outline */}
                  <rect
                    x={sx}
                    y={434}
                    width={174}
                    height={98}
                    rx={10}
                    fill="#141e35"
                    stroke={C.line}
                    strokeWidth={2}
                    strokeDasharray="6 4"
                  />
                  {/* filled 16 B store */}
                  {slotOpacity > 0.01 && (
                    <g opacity={slotOpacity} transform={`translate(${slideOut}, ${yOffset})`}>
                      <rect
                        x={sx}
                        y={434}
                        width={174}
                        height={98}
                        rx={10}
                        fill={`${K.ret}30`}
                        stroke={K.ret}
                        strokeWidth={3}
                      />
                      <text x={sx + 87} y={476} textAnchor="middle" fontSize={28} fontWeight={800} fill={K.ret} fontFamily={MONO}>
                        16 B
                      </text>
                      <text x={sx + 87} y={510} textAnchor="middle" fontSize={20} fill={C.text}>
                        store #{i + 1}
                      </text>
                    </g>
                  )}
                </g>
              );
            })}

            {/* Equation + Flush arrow */}
            <text x={495} y={604} textAnchor="middle" fontSize={28} fontWeight={800} fill={K.ret} fontFamily={MONO}>
              4 × 16 B = 64 B (100% full line)
            </text>

            {/* Flush pipeline */}
            <rect
              x={100}
              y={644}
              width={320}
              height={80}
              rx={14}
              fill={wcComplete ? "rgba(67,197,158,0.22)" : "rgba(67,197,158,0.08)"}
              stroke={K.fast}
              strokeWidth={2.5}
            />
            <text x={260} y={680} textAnchor="middle" fontSize={23} fontWeight={800} fill={K.fast}>
              Complete 64 B line
            </text>
            <text x={260} y={708} textAnchor="middle" fontSize={20} fill={C.muted}>
              ready before next reply
            </text>

            <Arrow x1={432} y1={684} x2={560} y2={684} color={K.fast} w={5} k={1} head={16} />
            {wcFlush > 0.02 && wcFlush < 0.98 && (
              <Packet pts={[[432, 684], [560, 684]]} k={wcFlush} color={K.fast} r={11} />
            )}

            <rect
              x={572}
              y={644}
              width={318}
              height={80}
              rx={14}
              fill="rgba(76,201,240,0.10)"
              stroke={K.fwd}
              strokeWidth={2.5}
            />
            <text x={731} y={680} textAnchor="middle" fontSize={23} fontWeight={800} fill={K.fwd}>
              1 Full PCIe Write
            </text>
            <text x={731} y={708} textAnchor="middle" fontSize={20} fill={C.muted}>
              any 64 KB target region
            </text>

            <text x={495} y={796} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.text}>
              Zero partial flushes — interleaving regions cannot break WC!
            </text>
          </g>
        )}

        {/* ---- Part 5b Cue 3: 128-byte coherence block schematic ---- */}
        {p5bHyp > 0.01 && (
          <g opacity={p5bHyp} fontFamily={FONT}>
            <rect x={140} y={224} width={1640} height={408} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />
            {/* Linux sysfs label */}
            <rect x={540} y={248} width={840} height={48} rx={12} fill="rgba(249,199,79,0.12)" stroke={K.knob} strokeWidth={2} />
            <text x={960} y={280} textAnchor="middle" fontSize={24} fontWeight={700} fill={K.knob} fontFamily={MONO}>
              Linux sysfs: coherency_line_size = 128 B
            </text>

            {/* Outer 128-byte block */}
            <rect
              x={260}
              y={334}
              width={1400}
              height={200}
              rx={18}
              fill="#0d1426"
              stroke={K.knob}
              strokeWidth={3.5}
            />
            <text x={960} y={368} textAnchor="middle" fontSize={23} fontWeight={800} letterSpacing={1.5} fill={K.knob}>
              ONE REPORTED 128-BYTE L3 COHERENCY BLOCK
            </text>

            {/* Left 64 B slot (Core 0) */}
            <rect
              x={290}
              y={388}
              width={650}
              height={124}
              rx={12}
              fill={`${CORE[0]}22`}
              stroke={CORE[0]}
              strokeWidth={3}
            />
            <text x={615} y={442} textAnchor="middle" fontSize={32} fontWeight={800} fill={CORE[0]}>
              Core 0 · Own 64 B slot
            </text>
            <text x={615} y={482} textAnchor="middle" fontSize={23} fontFamily={MONO} fill={C.muted}>
              bytes [0 .. 63]
            </text>

            {/* Right 64 B slot (Core 1) */}
            <rect
              x={980}
              y={388}
              width={650}
              height={124}
              rx={12}
              fill={`${CORE[1]}22`}
              stroke={CORE[1]}
              strokeWidth={3}
            />
            <text x={1305} y={442} textAnchor="middle" fontSize={32} fontWeight={800} fill={CORE[1]}>
              Core 1 · Own 64 B slot
            </text>
            <text x={1305} y={482} textAnchor="middle" fontSize={23} fontFamily={MONO} fill={C.muted}>
              bytes [64 .. 127]
            </text>

            {/* Dashed midpoint */}
            <line x1={960} y1={382} x2={960} y2={520} stroke={K.knob} strokeWidth={3} strokeDasharray="8 6" />

            {/* Animated thrash bounce indicator between the two halves */}
            <line x1={820} y1={576} x2={1100} y2={576} stroke={K.slow} strokeWidth={4} strokeDasharray="8 6" />
            <circle cx={lerp(830, 1090, bounce)} cy={576} r={12} fill={K.slow} />
            <text x={960} y={612} textAnchor="middle" fontSize={24} fontWeight={700} fill={K.slow}>
              Predicted coherence ping-pong? (d = 64 B)
            </text>
          </g>
        )}

        {/* ---- Part 5b Cues 4–5: Synchronized BarCharts ---- */}
        {p5bData > 0.01 && (
          <g opacity={p5bData} fontFamily={FONT}>
            {/* Highlight column around 64 B bar on left chart */}
            <rect
              x={276}
              y={308}
              width={142}
              height={328}
              rx={12}
              fill="rgba(67,197,158,0.10)"
              stroke={K.fast}
              strokeWidth={2.5}
              strokeDasharray="6 4"
              opacity={prog(t, T[4] + 5.6, 0.5)}
            />

            {/* Left BarChart: Writes per core (M/s) */}
            <BarChart
              x0={90}
              x1={945}
              y1={345}
              y0={626}
              max={300}
              dec={1}
              size={25}
              labelSize={23}
              barW={106}
              bars={[
                { label: "0 B", sub: "same slot", value: 72.3, color: K.slow, text: "72.3" },
                { label: "64 B", sub: "adjacent", value: 259.8, color: K.fast, text: "259.8" },
                { label: "128 B", value: 259.8, color: K.fast, text: "259.8" },
                { label: "256 B", value: 259.8, color: K.fast, text: "259.8" },
                { label: "4 KB", value: 259.8, color: K.fast, text: "259.8" },
              ]}
              k={(i) => {
                if (i === 0) return prog(t, T[4] + 1.0, 0.7);
                if (i === 1) return prog(t, T[4] + 5.8, 0.7);
                return prog(t, T[4] + 9.2 + (i - 2) * 0.5, 0.7);
              }}
            />

            {/* Right BarChart: L3 coherence snoops (M/s) */}
            <BarChart
              x0={1030}
              x1={1830}
              y1={345}
              y0={626}
              max={21}
              dec={2}
              size={25}
              labelSize={23}
              barW={100}
              bars={[
                { label: "0 B", sub: "thrashing", value: 17.8, color: K.slow, text: "17.8" },
                { label: "64 B", sub: "idle", value: 0.52, color: K.fast, text: "0.52" },
                { label: "128 B", value: 0.53, color: K.fast, text: "0.53" },
                { label: "256 B", value: 0.55, color: K.fast, text: "0.55" },
                { label: "4 KB", value: 0.55, color: K.fast, text: "0.55" },
              ]}
              k={(i) => {
                if (i === 0) return prog(t, T[4] + 1.5, 0.7);
                if (i === 1) return prog(t, T[4] + 6.2, 0.7);
                return prog(t, T[4] + 9.6 + (i - 2) * 0.5, 0.7);
              }}
            />

            {/* Clean dashed idle baseline callout on right chart (sits cleanly above the 0.52 bars, right-aligned) */}
            <g opacity={prog(t, T[4] + 6.5, 0.5)}>
              <line
                x1={1195}
                y1={626 - (281 * 0.52) / 21}
                x2={1825}
                y2={626 - (281 * 0.52) / 21}
                stroke={K.fast}
                strokeWidth={2}
                strokeDasharray="6 5"
              />
              <text x={1820} y={554} textAnchor="end" fontSize={21} fontWeight={700} fill={K.fast}>
                0.52 M/s = idle baseline (zero snoops)
              </text>
            </g>
          </g>
        )}
      </Stage>
    </>
  );
}

export const S07: SceneDef = {
  id: "S07",
  title: "Exp 5 · Ruling out two 4-core suspects",
  stage: 3,
  source: "4-core WC layout (64 runs) · card L3 spacing sweep (155 runs)",
  dur: DUR,
  cues,
  C: Body,
};
