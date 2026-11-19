import React from "react";
import {
  Arrow,
  C,
  Cue,
  FONT,
  K,
  MONO,
  Packet,
  SceneDef,
  Stage,
  clamp01,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import { InfPill } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 15.30, "**1 · One-way solved (161.6 M/s):** Return traffic is innocent (`113.1 vs 112.2`). Pausing **3 µs** behind the write band unlocks **+26%** — location conflict, not L3 bandwidth.", "Here is the complete causal map. First, one-way is solved: return traffic is innocent, and pausing three microseconds behind the write band unlocks one hundred sixty-one point six million, driven by write-band conflict, not L 3 bandwidth."], // 0
  [15.65, 33.37, "**2 · Closed loop diagnosed (~13 M/s):** Not busy CPUs (`0.04 µs` card, slopes ≈ 0), not broken WC (`99.99%`), not 128 B false sharing (`64 B`). **~42 msgs** queue; `+10 ns` host gap speeds it up **20%**.", "Second, the thirteen million loop is diagnosed: neither C P U is busy, write combining is ninety-nine point nine nine percent intact, and L 3 coherence is sixty-four bytes. Forty-two messages queue in transit, and ten nanoseconds of host spacing speeds it up twenty percent."], // 1
  [33.72, 46.86, "**3 · Open loop unlocked (83.7–97.3 M/s):** Breaking lockstep removes the wall (`35 + 35 M/s` at **~1 µs** `p50`, up to **97.3 M/s** combined), leaving a **35–39 µs** `p99` tail.", "Third, breaking lockstep unlocks thirty-five million each way at one microsecond median, up to ninety-seven point three million combined from one host core, leaving a thirty-five to thirty-nine microsecond tail."], // 2
  [47.21, 58.39, "**Next step 1 — Kill slow state & tail:** Pace host bursts, cap reply-poll batches, sweep the mesh clock, and track long-run **p99**.", "Next step one: kill the slow state and tail by pacing host bursts, capping reply-poll batches, sweeping the mesh clock, and tracking long-run ninety-ninth percentile latency."], // 3
  [58.74, 70.16, "**Next step 2 — Build real systems:** Let card cores drive the host's **Intel DSA** copy engine directly, and build a minimal **RPC / message layer** for real offload.", "Next step two: put this direct path to work by letting card cores drive the host's Intel D S A copy engine directly, and building a minimal R P C layer for real offload."], // 4
];
const DUR = 71.56; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function PillarCard({
  x,
  y,
  w,
  h,
  accent,
  step,
  headline,
  active,
  enter,
  bullets,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  accent: string;
  step: string;
  headline: string;
  active: boolean;
  enter: number;
  bullets: { k: number; node: React.ReactNode }[];
}) {
  if (enter <= 0.01) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        opacity: enter * (active ? 1 : 0.86),
        transform: `translateY(${(1 - enter) * 22}px)`,
        background: active ? "rgba(20,27,46,0.96)" : "rgba(20,27,46,0.86)",
        border: `2.5px solid ${active ? accent : `${accent}88`}`,
        borderTop: `8px solid ${accent}`,
        borderRadius: 18,
        padding: "20px 26px",
        boxSizing: "border-box",
        fontFamily: FONT,
        boxShadow: active ? `0 0 28px ${accent}33` : "none",
      }}
    >
      <div style={{ fontSize: 21, fontWeight: 800, letterSpacing: 1.4, color: accent, textTransform: "uppercase" }}>
        {step}
      </div>
      <div
        style={{
          fontSize: 48,
          fontWeight: 800,
          color: C.text,
          marginTop: 6,
          lineHeight: 1.1,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {headline}
      </div>
      <div style={{ height: 2, background: `${accent}33`, margin: "14px 0 16px" }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {bullets.map((b, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 12,
              fontSize: 23,
              lineHeight: 1.35,
              color: C.text,
              opacity: b.k,
              transform: `translateX(${(1 - b.k) * 14}px)`,
            }}
          >
            <span style={{ color: accent, fontWeight: 800, marginTop: 1 }}>▸</span>
            <div>{b.node}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RoadmapCard({
  x,
  y,
  w,
  h,
  accent,
  badge,
  title,
  active,
  enter,
  items,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  accent: string;
  badge: string;
  title: string;
  active: boolean;
  enter: number;
  items: { k: number; node: React.ReactNode }[];
}) {
  if (enter <= 0.01) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        opacity: enter * (active ? 1 : 0.88),
        transform: `translateY(${(1 - enter) * 20}px)`,
        background: "rgba(20,27,46,0.95)",
        border: `2.5px solid ${active ? accent : `${accent}88`}`,
        borderLeft: `10px solid ${accent}`,
        borderRadius: 16,
        padding: "18px 26px",
        boxSizing: "border-box",
        fontFamily: FONT,
        boxShadow: active ? `0 0 26px ${accent}2e` : "none",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14 }}>
        <span
          style={{
            fontSize: 19,
            fontWeight: 800,
            letterSpacing: 1.5,
            color: C.bg,
            background: accent,
            borderRadius: 8,
            padding: "4px 12px",
            whiteSpace: "nowrap",
          }}
        >
          {badge}
        </span>
        <span style={{ fontSize: 28, fontWeight: 800, color: C.text }}>{title}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
        {items.map((it, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 12,
              fontSize: 24,
              lineHeight: 1.32,
              color: C.text,
              opacity: it.k,
              transform: `translateX(${(1 - it.k) * 14}px)`,
            }}
          >
            <span style={{ color: accent, fontWeight: 800 }}>•</span>
            <div>{it.node}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Body() {
  const t = useT();
  const rt = useRealT();

  const p1 = prog(t, T[0], 0.55);
  const p2 = prog(t, T[1], 0.55);
  const p3 = prog(t, T[2], 0.55);
  const pNext1 = prog(t, T[3], 0.55);
  const pNext2 = prog(t, T[4], 0.55);

  const a12 = prog(t, T[1] - 0.15, 0.5);
  const a23 = prog(t, T[2] - 0.15, 0.5);
  const nextHeader = prog(t, T[3] - 0.1, 0.45);

  const pulse12 = (rt * 0.65) % 1;
  const pulse23 = ((rt * 0.65) + 0.5) % 1;

  // Which card is currently active for subtle highlight glow
  const act1 = t < T[1] || t >= T[4] + 3.0;
  const act2 = (t >= T[1] && t < T[2]) || t >= T[4] + 3.0;
  const act3 = (t >= T[2] && t < T[3]) || t >= T[4] + 3.0;
  const actN1 = (t >= T[3] && t < T[4]) || t >= T[4] + 3.0;
  const actN2 = t >= T[4];

  return (
    <>
      {/* ======================= TOP CAUSAL CHAIN (3 PILLARS) ======================= */}
      <PillarCard
        x={60}
        y={136}
        w={566}
        h={392}
        accent={K.fwd}
        step="1 · One-way solved"
        headline="161.6 M/s"
        active={act1}
        enter={p1}
        bullets={[
          {
            k: prog(t, T[0] + 1.2, 0.5),
            node: (
              <>
                Return innocent (<span style={{ fontFamily: MONO, color: K.fast }}>113.1 vs 112.2</span> M/s)
              </>
            ),
          },
          {
            k: prog(t, T[0] + 4.2, 0.5),
            node: (
              <>
                Pause <strong style={{ color: K.knob }}>3 µs</strong> behind write band →{" "}
                <strong style={{ color: K.fast }}>+26%</strong>
              </>
            ),
          },
          {
            k: prog(t, T[0] + 7.5, 0.5),
            node: (
              <>
                Write-band location conflict, not L3 bandwidth <InfPill size={17} style={{ marginLeft: 4 }} />
              </>
            ),
          },
        ]}
      />

      <PillarCard
        x={677}
        y={136}
        w={566}
        h={392}
        accent={K.card}
        step="2 · Closed loop diagnosed"
        headline="~13 M/s"
        active={act2}
        enter={p2}
        bullets={[
          {
            k: prog(t, T[1] + 1.2, 0.5),
            node: (
              <>
                Not busy CPUs (<span style={{ fontFamily: MONO, color: K.card }}>0.04 µs</span> card, slopes ≈ 0)
              </>
            ),
          },
          {
            k: prog(t, T[1] + 4.6, 0.5),
            node: (
              <>
                Not WC (<strong style={{ color: K.fast }}>99.99%</strong> full) · Not 128 B (
                <strong style={{ color: K.fast }}>64 B</strong> coherence)
              </>
            ),
          },
          {
            k: prog(t, T[1] + 8.6, 0.5),
            node: (
              <>
                <strong style={{ color: K.slow }}>~42 msgs</strong> queue;{" "}
                <span style={{ fontFamily: MONO, color: K.knob }}>+10 ns</span> host gap →{" "}
                <strong style={{ color: K.fast }}>20% faster</strong>
              </>
            ),
          },
        ]}
      />

      <PillarCard
        x={1294}
        y={136}
        w={566}
        h={392}
        accent={K.fast}
        step="3 · Open loop unlocked"
        headline="83.7–97.3 M/s"
        active={act3}
        enter={p3}
        bullets={[
          {
            k: prog(t, T[2] + 1.0, 0.5),
            node: (
              <>
                Breaking lockstep removes the <strong style={{ color: K.slow }}>~13 M/s</strong> wall
              </>
            ),
          },
          {
            k: prog(t, T[2] + 3.6, 0.5),
            node: (
              <>
                <strong style={{ color: K.fast }}>35 + 35 M/s</strong> at{" "}
                <strong style={{ color: K.fast }}>~1 µs</strong> median (<span style={{ fontFamily: MONO }}>p50</span>)
              </>
            ),
          },
          {
            k: prog(t, T[2] + 6.4, 0.5),
            node: (
              <>
                Open target: <strong style={{ color: K.slow }}>35–39 µs</strong>{" "}
                <span style={{ fontFamily: MONO, color: K.slow }}>p99</span> tail remains
              </>
            ),
          },
        ]}
      />

      {/* ======================= BOTTOM ROADMAP DIVIDER ======================= */}
      {nextHeader > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 60,
            top: 544,
            width: 1800,
            display: "flex",
            alignItems: "center",
            gap: 18,
            opacity: nextHeader,
            fontFamily: FONT,
          }}
        >
          <div style={{ flex: 1, height: 2, background: `linear-gradient(90deg, transparent, ${C.line})` }} />
          <span style={{ fontSize: 20, fontWeight: 800, letterSpacing: 3, color: K.knob }}>WHAT&apos;S NEXT</span>
          <div style={{ flex: 1, height: 2, background: `linear-gradient(90deg, ${C.line}, transparent)` }} />
        </div>
      )}

      {/* ======================= BOTTOM ROADMAP (2 WIDE CARDS) ======================= */}
      <RoadmapCard
        x={60}
        y={584}
        w={875}
        h={286}
        accent={K.knob}
        badge="NEXT 1"
        title="Kill the slow state & p99 tail"
        active={actN1}
        enter={pNext1}
        items={[
          {
            k: prog(t, T[3] + 0.9, 0.45),
            node: (
              <>
                Pace host <span style={{ fontFamily: MONO, color: K.fwd }}>MOVDIR64B</span> bursts to avoid the slow
                regime
              </>
            ),
          },
          {
            k: prog(t, T[3] + 2.8, 0.45),
            node: <>Cap host reply-poll batches across sub-rings</>,
          },
          {
            k: prog(t, T[3] + 4.8, 0.45),
            node: (
              <>
                Sweep mesh clock &amp; track long-run <strong style={{ color: K.slow }}>p99</strong> latency
              </>
            ),
          },
        ]}
      />

      <RoadmapCard
        x={985}
        y={584}
        w={875}
        h={286}
        accent={K.fast}
        badge="NEXT 2"
        title="Build real DPU-to-host systems"
        active={actN2}
        enter={pNext2}
        items={[
          {
            k: prog(t, T[4] + 0.9, 0.45),
            node: (
              <>
                Card cores drive host <strong style={{ color: K.fwd }}>Intel DSA</strong> copy engine directly
              </>
            ),
          },
          {
            k: prog(t, T[4] + 3.0, 0.45),
            node: (
              <>
                Build a minimal <strong style={{ color: K.fast }}>RPC / message layer</strong> over direct rings
              </>
            ),
          },
          {
            k: prog(t, T[4] + 5.2, 0.45),
            node: <>Benchmark on real request–response offload workloads</>,
          },
        ]}
      />

      {/* ======================= SVG STAGE (ARROWS & FLOW) ======================= */}
      <Stage>
        {/* Arrow 1 -> 2 */}
        {a12 > 0.01 && (
          <g opacity={a12}>
            <Arrow x1={630} y1={332} x2={672} y2={332} color={K.card} w={5} k={a12} head={14} />
            {a12 > 0.9 && <Packet pts={[[630, 332], [670, 332]]} k={pulse12} color={K.card} r={6} trail={false} />}
          </g>
        )}

        {/* Arrow 2 -> 3 */}
        {a23 > 0.01 && (
          <g opacity={a23}>
            <Arrow x1={1247} y1={332} x2={1289} y2={332} color={K.fast} w={5} k={a23} head={14} />
            {a23 > 0.9 && <Packet pts={[[1247, 332], [1287, 332]]} k={pulse23} color={K.fast} r={6} trail={false} />}
          </g>
        )}

        {/* Subtle downward flow arrows from top synthesis to Next 1 & Next 2 */}
        {pNext1 > 0.2 && (
          <g opacity={clamp01((pNext1 - 0.2) / 0.8) * 0.75}>
            <Arrow x1={498} y1={532} x2={498} y2={578} color={K.knob} w={4} k={pNext1} head={12} />
          </g>
        )}
        {pNext2 > 0.2 && (
          <g opacity={clamp01((pNext2 - 0.2) / 0.8) * 0.75}>
            <Arrow x1={1422} y1={532} x2={1422} y2={578} color={K.fast} w={4} k={pNext2} head={12} />
          </g>
        )}
      </Stage>
    </>
  );
}

export const S09: SceneDef = {
  id: "S09",
  title: "The complete causal map & what's next",
  stage: 5,
  source: "Six single-variable experiments · verified causal map & roadmap",
  dur: DUR,
  cues,
  C: Body,
};
