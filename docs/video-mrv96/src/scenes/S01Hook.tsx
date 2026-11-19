import React from "react";
import { C, Cue, FONT, K, Link, Note, PacketStream, Pt, SceneDef, Stage, along, appear, clamp01, lerp, polyLen, pop, prog, useRealT, useT } from "../lib";
import { Brace, Chip, InfPill, Rig, RigBlock, rigGeom, rigRoutes } from "./kit";
import { At, Check, KnobGlyph, SliderGlyph, bump } from "./_Mrv96FrameScenes";

// <cues>
const cues: Cue[] = [
  [0.60, 10.90, "A server talks to its **DPU** through direct **64-byte** writes. One way, one host core pushes **161.6 M messages/s** into the card.", "A server talks to its D P U through direct sixty-four byte writes. One way, a single host core pushes one hundred sixty-one point six million messages per second."], // 0
  [11.25, 19.77, "Close the loop, so the host waits for replies before sending more, and the same path settles near **13 M/s**: about **12× slower**.", "But close the loop, so the host waits for replies before sending more, and the same path settles near thirteen million per second. Twelve times slower."], // 1
  [20.12, 29.24, "Why? We ran **six experiments**. Each keeps the fastest setup fixed and turns **exactly one knob**, so each result has one cause.", "Why? We ran six experiments. Each one keeps the fastest setup fixed, and turns exactly one knob, so every difference has a single cause."], // 2
  [29.59, 38.41, "Experiments **1–2** dissect the one-way path, **3–4** look inside the loop, **5** removes the loop, and **6** rules out two suspects.", "Experiments one and two dissect the one-way path, three and four look inside the loop, five removes the loop, and six rules out two suspects."], // 3
  [38.76, 50.70, "Every run verifies every message: **0 lost, 0 corrupt**. Numbers are medians unless marked; **[INF]** marks our interpretation.", "Every run verifies every message: zero lost, zero corrupted. Numbers are medians unless noted, and anything tagged as an inference is our interpretation, not a measurement."], // 4
];
const DUR = 52.10; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
/** The hook's loop diagram (T0–T1); it shrinks away upward at T2. */
const G = rigGeom(1, 0, 10);
const R = rigRoutes(G);
const FWD: Pt[] = [...R.send, ...R.fwd];
const LOOP_L = polyLen(R.loop);
/** Fraction of the loop where the reply half starts (packets turn green there). */
const RET_AT = polyLen([...R.send, ...R.fwd, ...R.poll]) / LOOP_L;
const LABELS: Partial<Record<RigBlock, [string, string?]>> = { hcore: ["host core"], ccores: ["card cores"], link: [""] };
const MID = 960;
/** Same-scale comparison under the headline (T1): one way vs closed loop. */
const BAR = { x0: 780, len: 470, y1: 604, y2: 648, h: 28 };

const EXPS: { n: string; title: string; group: number; subs?: string[] }[] = [
  { n: "1", title: "Chasing the frontier", group: 0 },
  { n: "2", title: "Where the core reads", group: 0 },
  { n: "3", title: "Inside the slow loop", group: 1 },
  { n: "4", title: "Who sets the pace?", group: 1 },
  { n: "5", title: "Remove the loop", group: 2 },
  { n: "6", title: "Two suspects", group: 3, subs: ["6a interleaved replies", "6b 64-byte coherence"] },
];
/** Groups in narration order; `at` = seconds after T[3] when the narrator names them. */
const GROUPS = [
  { label: "1–2 · one-way path", color: K.fwd, from: 0, to: 1, at: 0.3 },
  { label: "3–4 · inside the loop", color: C.orange, from: 2, to: 3, at: 3.0 },
  { label: "5 · remove the loop", color: K.fast, from: 4, to: 4, at: 5.1 },
  { label: "6 · rule out suspects", color: C.pink, from: 5, to: 5, at: 6.4 },
];
const TILE = { x0: 60, w: 290, gap: 12, y: 360, h: 360 };
const SLIDERS = [
  [0.3, 0.62, 0.45],
  [0.55, 0.28, 0.7],
  [0.4, 0.7, 0.3],
  [0.66, 0.42, 0.55],
  [0.35, 0.5, 0.72],
  [0.6, 0.35, 0.48],
];

/** One experiment tile: grey fixed sliders + ONE yellow knob that turns; group colour and title arrive at T3. */
function Tile({ i, t, rt }: { i: number; t: number; rt: number }) {
  const e = EXPS[i];
  const gr = GROUPS[e.group];
  const k = prog(t, T[2] + 0.9 + 0.1 * i, 0.7);
  if (k <= 0) return null;
  const gk = prog(t, T[3] + gr.at, 0.5);
  const titleK = prog(t, T[3] + gr.at + 0.25, 0.5);
  const spin = prog(t, T[2] + 4.6, 0.8);
  const angle = lerp(-60, 115 * Math.sin(rt * 1.5 + i * 1.3), spin);
  const checkK = prog(t, T[4] + 0.6 + 0.12 * i, 0.4);
  const glow = bump(t, T[3] + gr.at, 1.0);
  return (
    <div
      style={{
        position: "absolute",
        left: TILE.x0 + i * (TILE.w + TILE.gap),
        top: TILE.y,
        width: TILE.w,
        height: TILE.h,
        opacity: k,
        transform: `translateY(${(1 - k) * 110}px) scale(${0.9 + 0.1 * k})`,
        fontFamily: FONT,
      }}
    >
      <div style={{ position: "absolute", inset: 0, background: C.panel, border: `2px solid ${C.line}`, borderRadius: 20 }} />
      <div
        style={{
          position: "absolute",
          inset: 0,
          border: `3px solid ${gr.color}`,
          borderRadius: 20,
          opacity: gk,
          boxShadow: `0 0 ${8 + 30 * glow}px ${gr.color}${glow > 0.05 ? "aa" : "44"}`,
        }}
      />
      <div style={{ position: "absolute", left: 22, top: 10, fontSize: 54, fontWeight: 800, color: C.muted, opacity: 1 - gk }}>
        {e.n}
      </div>
      <div style={{ position: "absolute", left: 22, top: 10, fontSize: 54, fontWeight: 800, color: gr.color, opacity: gk }}>{e.n}</div>
      <svg width={TILE.w} height={140} style={{ position: "absolute", left: 0, top: 84 }}>
        <SliderGlyph x={50} y={14} h={108} pos={SLIDERS[i]} gap={36} />
        <KnobGlyph cx={208} cy={68} r={44} angle={angle} glow={spin} />
      </svg>
      <div
        style={{
          position: "absolute",
          left: 8,
          right: 8,
          top: 240,
          textAlign: "center",
          opacity: titleK,
          transform: `translateY(${(1 - titleK) * 12}px)`,
        }}
      >
        <div style={{ fontSize: 30, fontWeight: 800, lineHeight: 1.18, color: C.text }}>{e.title}</div>
        {e.subs?.map((s) => (
          <div key={s} style={{ fontSize: 24, color: C.muted, marginTop: 4, whiteSpace: "nowrap" }}>
            {s}
          </div>
        ))}
      </div>
      {checkK > 0 && (
        <svg width={56} height={56} style={{ position: "absolute", right: 10, top: 12, opacity: checkK }}>
          <circle cx={28} cy={28} r={22} fill="rgba(67,197,158,0.14)" stroke={K.fast} strokeWidth={3} />
          <Check x={28} y={30} s={0.85} k={checkK} w={4.5} />
        </svg>
      )}
    </div>
  );
}

function Body() {
  const t = useT();
  const rt = useRealT();

  // ---- T0–T1: the loop diagram with its headline; at T2 it shrinks upward and fades
  const out = prog(t, T[2], 1.0);
  const showAll = prog(t, T[0], 0.6);
  const retSide = lerp(0.22, 1, prog(t, T[1] + 0.2, 0.6));
  const fwdDraw = prog(t, T[0] + 0.3, 0.9);
  const retDraw = prog(t, T[1] + 0.2, 1.0);
  const lanes = { fwd: clamp01((fwdDraw - 0.85) / 0.15), ret: clamp01((retDraw - 0.85) / 0.15) };
  const denseK = prog(t, T[0] + 0.8, 0.3) * (1 - prog(t, T[1] + 0.3, 0.6));
  const lockK = prog(t, T[1] + 1.0, 0.5);
  const waitK = prog(t, T[1] + 1.4, 0.5);
  const tagK = prog(t, T[0] + 2.2, 0.5);
  const subK = prog(t, T[0] + 4.3, 0.5) * (1 - prog(t, T[1] + 3.9, 0.45));
  const fastK = prog(t, T[0] + 5.6, 0.5) * (1 - prog(t, T[1] + 3.9, 0.45));
  const slowK = prog(t, T[1] + 4.4, 0.5);
  const bar1 = prog(t, T[1] + 5.0, 0.7);
  const bar2 = prog(t, T[1] + 5.6, 0.5);
  const badge = pop(t, T[1] + 6.9, 0.5);
  const busy = t < T[1] + 0.9;
  const hc = G.r.hcore;
  const head = (k: number, v: string, color: string) => (
    <g opacity={k} transform={`translate(${MID} 560) scale(${0.92 + 0.08 * k}) translate(${-MID} -560)`}>
      <text x={MID} y={560} textAnchor="middle" fontWeight={800} style={{ fontVariantNumeric: "tabular-nums" }}>
        <tspan fontSize={132} fill={color}>
          {v}
        </tspan>
        <tspan fontSize={56} fill={C.text} dx={18}>
          M/s
        </tspan>
      </text>
    </g>
  );

  // ---- T2–T4: six experiment tiles, groups, ground rules
  const legend = (a: number) => ({ ...appear(t, a, undefined, 14), display: "inline-flex", alignItems: "center", gap: 10 });
  const rule = (a: number): React.CSSProperties => {
    const p = pop(t, a, 0.5);
    return { opacity: clamp01(p * 1.4), transform: `scale(${0.8 + 0.2 * p})`, display: "inline-block" };
  };

  return (
    <>
      <Stage>
        {out < 1 && (
          <g opacity={1 - out} transform={`translate(${MID} ${520 - 200 * out}) scale(${1 - 0.5 * out}) translate(${-MID} -520)`}>
            <Rig g={G} labels={LABELS} lanes={lanes} cores={2} activeCore={0}
              show={{ host: showAll, card: showAll, hcore: showAll, wq: showAll, cring: showAll, ccores: showAll, link: showAll,
                hring: showAll * retSide, wc: showAll * retSide }}
              ringFill={{ cring: busy ? 4 + (Math.floor(rt * 7) % 4) : 1, hring: busy ? 0 : 1 }} />
            {/* the kit lights one core; light the second one too (the one-way runs used two receive cores) */}
            <rect x={G.r.ccores.x + 114} y={G.r.ccores.y + 100} width={52} height={52} rx={8} fill={K.card} stroke={K.card} strokeWidth={2}
              opacity={showAll} />
            {lanes.fwd < 1 && <Link pts={R.fwd} k={fwdDraw} color={K.fwd} w={5} />}
            {lanes.ret < 1 && <Link pts={R.ret} k={retDraw} color={K.ret} w={5} />}
            <Link pts={R.send} color={K.fwd} w={3} opacity={0.45 * showAll} />
            <Link pts={R.poll} color={K.card} w={3} opacity={0.5 * showAll} />
            <Link pts={R.reply} color={K.ret} w={3} opacity={0.45 * showAll * retSide} />
            <Link pts={R.hpoll} color={K.ret} w={3} opacity={0.45 * showAll * retSide} />
            {/* one way: a dense stream, no waiting */}
            <PacketStream pts={FWD} travel={Math.max(0, rt - (T[0] + 0.8)) * 560} gap={28} size={12} color={K.fwd} opacity={denseK} />
            {/* closed loop: a few messages walk the whole loop in lockstep */}
            {Array.from({ length: 4 }, (_, i) => {
              const k = ((Math.max(0, rt - (T[1] + 1.0)) * 330) / LOOP_L + i / 4) % 1;
              const edge = Math.min(clamp01(k / 0.02), clamp01((1 - k) / 0.02));
              const [x, y] = along(R.loop, k);
              const col = k < RET_AT ? K.fwd : K.ret;
              return (
                <g key={i} opacity={lockK * edge}>
                  <circle cx={x} cy={y} r={18} fill={col} opacity={0.2} />
                  <circle cx={x} cy={y} r={11} fill={col} />
                </g>
              );
            })}
            <text x={hc.x + hc.w / 2} y={hc.y + 34 + 30} textAnchor="middle" fontSize={24} fontWeight={700} fill={K.wait} opacity={waitK}>
              waits for replies
            </text>

            {/* headline in the PCIe band: 161.6 M/s one way → ~13 M/s closed loop */}
            <text x={MID} y={G.fwdY + 46} textAnchor="middle" fontSize={26} fontWeight={700} fill={K.fwd} opacity={tagK}>
              64-byte writes
            </text>
            {head(fastK, "161.6", K.fast)}
            <text x={MID} y={614} textAnchor="middle" fontSize={34} fontWeight={600} fill={C.muted} opacity={subK}>
              one way · 1 host core
            </text>
            {head(slowK, "~13", K.slow)}
            <g opacity={prog(t, T[1] + 5.0, 0.4)}>
              <text x={BAR.x0 - 16} y={BAR.y1 + BAR.h / 2 + 1} textAnchor="end" dominantBaseline="central" fontSize={24} fill={C.muted}>
                one way
              </text>
              <text x={BAR.x0 - 16} y={BAR.y2 + BAR.h / 2 + 1} textAnchor="end" dominantBaseline="central" fontSize={24} fill={C.muted}>
                closed loop
              </text>
            </g>
            {bar1 > 0 && <rect x={BAR.x0} y={BAR.y1} width={BAR.len * bar1} height={BAR.h} rx={6} fill={K.fast} />}
            <text x={BAR.x0 + BAR.len - 12} y={BAR.y1 + BAR.h / 2 + 1} textAnchor="end" dominantBaseline="central" fontSize={24}
              fontWeight={800} fill={C.bg} opacity={clamp01((bar1 - 0.6) / 0.4)}>
              161.6
            </text>
            {bar2 > 0 && <rect x={BAR.x0} y={BAR.y2} width={((BAR.len * 13) / 161.6) * bar2} height={BAR.h} rx={6} fill={K.slow} />}
            <text x={BAR.x0 + (BAR.len * 13) / 161.6 + 12} y={BAR.y2 + BAR.h / 2 + 1} dominantBaseline="central" fontSize={24}
              fontWeight={800} fill={K.slow} opacity={clamp01((bar2 - 0.6) / 0.4)}>
              ~13
            </text>
            {badge > 0 && (
              <g transform={`translate(${BAR.x0 + 214} ${BAR.y2 + BAR.h / 2}) scale(${badge})`}>
                <rect x={-100} y={-23} width={200} height={46} rx={23} fill={K.slow} />
                <text textAnchor="middle" dominantBaseline="central" y={1} fontSize={28} fontWeight={800} fill={C.bg}>
                  12× slower
                </text>
              </g>
            )}
          </g>
        )}

        {/* T3: group brackets over the tiles */}
        {GROUPS.map((gr) => {
          const k = prog(t, T[3] + gr.at, 0.5);
          if (k <= 0) return null;
          return (
            <g key={gr.label} transform={`translate(0 ${(1 - k) * 12})`}>
              <Brace x1={TILE.x0 + gr.from * (TILE.w + TILE.gap) + 10} x2={TILE.x0 + gr.to * (TILE.w + TILE.gap) + TILE.w - 10}
                y={TILE.y - 14} dir={-1} label={gr.label} color={gr.color} size={26} opacity={k} />
            </g>
          );
        })}
      </Stage>

      <div style={{ opacity: 1 - out }}>
        <Note x={MID - 200} y={800} w={400}>
          <div style={{ textAlign: "center" }}>schematic · dots are not to scale</div>
        </Note>
      </div>

      {/* T2: six experiments = fixed setup + one knob → one cause */}
      <At x={60} y={130} w={1800} style={{ display: "flex", alignItems: "center", gap: 18 }}>
        <span style={{ ...appear(t, T[2] + 1.0, undefined, 14), fontSize: 52, fontWeight: 800, color: C.text }}>Six experiments</span>
        <span style={{ flex: 1 }} />
        <span style={legend(T[2] + 3.0)}>
          <svg width={58} height={48}>
            <SliderGlyph x={12} y={4} h={40} pos={[0.3, 0.65, 0.45]} gap={17} />
          </svg>
          <span style={{ fontSize: 30, fontWeight: 700, color: K.fixed }}>fixed setup</span>
        </span>
        <span style={{ ...legend(T[2] + 4.6), fontSize: 30, color: C.muted }}>+</span>
        <span style={legend(T[2] + 4.6)}>
          <svg width={50} height={50}>
            <KnobGlyph cx={25} cy={25} r={15} angle={lerp(-60, 110 * Math.sin(rt * 1.5), prog(t, T[2] + 4.6, 0.8))} />
          </svg>
          <span style={{ fontSize: 30, fontWeight: 800, color: K.knob }}>one knob</span>
        </span>
        <span style={{ ...legend(T[2] + 6.6), fontSize: 30, fontWeight: 700, color: C.text }}>→ one cause</span>
      </At>

      {EXPS.map((_, i) => (
        <Tile key={i} i={i} t={t} rt={rt} />
      ))}

      {/* T4: ground rules */}
      <At x={MID} y={790} center style={{ display: "flex", gap: 24, alignItems: "center", whiteSpace: "nowrap" }}>
        <span style={rule(T[4] + 0.3)}>
          <Chip color={K.fast} size={26}>
            <svg width={30} height={26} style={{ verticalAlign: "-3px", marginRight: 8 }}>
              <Check x={15} y={15} s={0.7} w={4} />
            </svg>
            every message verified · 0 lost · 0 corrupt
          </Chip>
        </span>
        <span style={rule(T[4] + 4.0)}>
          <Chip color={C.text} size={26}>
            numbers are medians unless marked
          </Chip>
        </span>
        <span style={{ ...rule(T[4] + 6.6), fontSize: 26, fontWeight: 700, color: C.text }}>
          <InfPill size={26} style={{ marginRight: 10 }} />= our interpretation
        </span>
      </At>
    </>
  );
}

export const S01: SceneDef = {
  id: "S01",
  title: "One path, two speeds",
  stage: undefined,
  source: "host ⇄ DPU direct path · six experiments · Nov 17–18, 2026",
  dur: DUR,
  cues,
  C: Body,
};
