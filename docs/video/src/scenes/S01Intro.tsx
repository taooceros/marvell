import React from "react";
import { Arrow, C, Cue, K, PacketStream, SceneDef, Stage, STAGES, Stat, countUp, fade, lerp, pop, prog, useRealT, useT } from "../lib";
import { BlockId, Datapath, dpGeom, routes } from "./pcie";
import { Centered, Hi, INNER, Legs, SDP_SHORT, SDP_SHORT_BELOW } from "./_SceneIntro";

// <cues>
const cues: Cue[] = [
  [0.60, 7.04, "How fast can a **64-byte message** make a round trip between a server and the DPU plugged into it?", "How fast can a sixty-four byte message make a round trip, between a server and the D P U plugged into it?"], // 0
  [7.39, 18.15, "Host **saturn** (Xeon Gold 6438M) and a Marvell **OCTEON 10 CN106xx** card, linked by **PCIe Gen5 x4**.", "Our host, saturn, is a Xeon Gold server. The card is a Marvell Octeon ten, C N one oh six, on P C I express gen five, by four."], // 1
  [18.50, 28.54, "The stock packet path, **SDP**: **4.85 µs** per round trip, and at most **~7.5 M msg/s**.", "The stock packet path, called S D P, takes four point eight five microseconds per round trip, and tops out around seven and a half million messages per second."], // 2
  [28.89, 38.39, "Where we ended up: **0.83 µs** one message at a time, and up to **~28 M msg/s** with four card cores.", "By the end, one message at a time takes zero point eight three microseconds, and four card cores reach up to about twenty-eight million messages per second."], // 3
  [38.74, 46.80, "Roadmap: the baseline → a faster send path → four ways back → scaling out → why we trust the numbers.", "The road map: the baseline, a faster send path, four different ways back to the host, scaling out, and why we trust the numbers."], // 4
];
const DUR = 48.20; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

/** Roadmap chips (STAGES order) and when each is spoken, in seconds after T[4]. */
const ROAD_SUB = ["stock SDP", "BAR4 inline", "×4 ways back", "4 card cores", "check every reply"];
const ROAD_AT = [1.0, 1.9, 3.1, 4.8, 5.8];

/** Stat card geometry (c2–c3): width, top, and the two pairs' x positions. */
const SW = 390;
const SY = 640;

function Body() {
  const t = useT();
  const rt = useRealT();

  // One datapath: full view (c0–c1) morphs to a shrunk strip on top (c2–c4).
  const m = prog(t, T[2] + 0.2, 1.0);
  const g = dpGeom(lerp(0.78, 0.62, m), lerp(211.2, 364.8, m), lerp(120, 32, m));
  const r = routes(g);
  const gA = dpGeom(0.78, 211.2, 120);
  const linkCx = gA.c("link")[0];

  // c1: host → card → link highlights
  const kHost = prog(t, T[1] + 0.3, 0.5) * (1 - prog(t, T[1] + 3.2, 0.5));
  const kCard = prog(t, T[1] + 3.2, 0.5) * (1 - prog(t, T[1] + 7.0, 0.5));
  const kLink = prog(t, T[1] + 7.0, 0.5) * (1 - prog(t, T[2] - 0.1, 0.5));
  // c2: SDP; c3: pull (control cell) then 4 card cores
  const kSdp = prog(t, T[2] + 1.2, 0.6) * (1 - prog(t, T[3], 0.6));
  const kPull = prog(t, T[3] + 0.3, 0.6) * (1 - prog(t, T[3] + 4.6, 0.6));
  const kCores = prog(t, T[3] + 4.8, 0.6) * (1 - prog(t, T[4], 0.6));
  const weight: Partial<Record<BlockId, number>> = { sdp: kSdp, ctrl: kPull, cores: kCores };
  const dim = Math.max(kSdp, kPull, kCores);
  const show = Object.fromEntries(INNER.map((id) => [id, 1 - 0.62 * Math.max(0, dim - (weight[id] ?? 0))])) as Partial<Record<BlockId, number>>;
  const active = 1 + Math.round(3 * prog(t, T[3] + 4.8, 1.2));
  const dpOpacity = prog(t, 0.1, 0.8) * (1 - 0.72 * prog(t, T[4], 0.8));

  // c3: stats slide left to make room for their companions
  const m3 = prog(t, T[3] + 0.1, 0.8);
  const xA = lerp(300, 70, m3);
  const xC = lerp(1230, 990, m3);
  const statOut = 1 - prog(t, T[4] - 0.1, 0.5);
  const arrowY = SY + 82;

  // c1: animated lane overlay (traffic on the link)
  const L = g.r.link;
  const laneY = [445, 468, 492, 515].map((y) => g.pt([0, y])[1]);

  // c4: roadmap row
  const cw = 316;
  const cgap = 45;
  const cx0 = (1920 - (5 * cw + 4 * cgap)) / 2;
  const cy = 640;
  const ch = 170;

  const unit = (s: string, size = 32) => <span style={{ fontSize: size, fontWeight: 700 }}>{s}</span>;
  const statStyle = (left: number, op: number): React.CSSProperties => ({
    position: "absolute",
    left,
    top: SY,
    width: SW,
    boxSizing: "border-box",
    opacity: op,
  });

  return (
    <>
      <Stage>
        <Datapath g={g} show={show} labels={g.s < SDP_SHORT_BELOW ? SDP_SHORT : {}} cores={4} activeCores={active} coreColors={[C.cyan, C.green, C.yellow, C.pink]} opacity={dpOpacity} linkGlow={Math.max(kLink, 0.6 * kCores)} />
        <g opacity={dpOpacity}>
          <Hi g={g} id="host" color={K.host} k={kHost} pad={6} panel />
          <Hi g={g} id="card" color={K.card} k={kCard} pad={6} panel />
          <Hi g={g} id="link" color={K.link} k={kLink} pad={4} />
          <Hi g={g} id="sdp" color={K.sdp} k={kSdp} />
          <Hi g={g} id="ctrl" color={K.pull} k={kPull} />
          <Hi g={g} id="cores" color={K.ok} k={kCores} />
          {kLink > 0.001 &&
            laneY.map((y, i) => (
              <line
                key={i}
                x1={L.x}
                y1={y}
                x2={L.x + L.w}
                y2={y}
                stroke={K.link}
                strokeWidth={4}
                strokeDasharray="18 22"
                strokeDashoffset={(i < 2 ? -1 : 1) * rt * 140}
                opacity={kLink}
              />
            ))}
          {/* c0: one 64 B request out (inline), reply back (SDP) */}
          <Legs
            t={t}
            start={T[0] + 0.8}
            legs={[
              { pts: r.inline, color: K.req, d: 1.5 },
              { pts: r.sdpReply, color: K.rep, d: 1.5 },
            ]}
            opacity={fade(t, T[0] + 0.6, T[1] - 0.1)}
            tag="64 B"
          />
          {/* c2: stock SDP: doorbell out, SDP reply back */}
          <Legs
            t={t}
            start={T[2] + 1.4}
            legs={[
              { pts: r.doorbell, color: K.sdp, d: 1.1 },
              { pts: r.sdpReply, color: K.rep, d: 1.3 },
            ]}
            opacity={fade(t, T[2] + 1.2, T[3] - 0.1)}
          />
          {/* c3: pull (one UC read of the control cell), then a 4-core stream */}
          <Legs
            t={t}
            start={T[3] + 0.4}
            legs={[
              { pts: r.pullOut, color: K.pull, d: 0.9 },
              { pts: r.pullBack, color: K.pull, d: 0.9 },
            ]}
            hold={0.1}
            opacity={fade(t, T[3] + 0.3, T[3] + 4.5)}
          />
          <PacketStream pts={r.inline} travel={(rt - (T[3] + 4.8)) * 320} gap={42} size={12} color={K.req} opacity={fade(t, T[3] + 4.8, T[4] - 0.1)} />
          <PacketStream pts={r.sdpReply} travel={(rt - (T[3] + 5.6)) * 320} gap={42} size={12} color={K.rep} opacity={fade(t, T[3] + 5.6, T[4] - 0.1)} />
        </g>
        {/* c3: arrows to the companions */}
        <Arrow x1={xA + SW + 8} y1={arrowY} x2={70 + SW + 72} y2={arrowY} color={K.pull} w={5} k={prog(t, T[3] + 1.0, 0.6)} opacity={statOut} />
        <Arrow x1={xC + SW + 8} y1={arrowY} x2={990 + SW + 72} y2={arrowY} color={K.ok} w={5} k={prog(t, T[3] + 4.6, 0.6)} opacity={statOut} />
        {/* c4: roadmap arrows */}
        {[0, 1, 2, 3].map((i) => (
          <Arrow
            key={i}
            x1={cx0 + i * (cw + cgap) + cw + 6}
            y1={cy + ch / 2}
            x2={cx0 + (i + 1) * (cw + cgap) - 6}
            y2={cy + ch / 2}
            color={STAGES[i + 1].color}
            w={4}
            head={12}
            k={prog(t, T[4] + ROAD_AT[i + 1] - 0.3, 0.4)}
          />
        ))}
      </Stage>

      {/* c0: the question */}
      <Centered x={linkCx} y={585} op={fade(t, T[0] + 1.0, T[1] - 0.1)} scale={1 + 0.035 * Math.sin(rt * 4)}>
        <span style={{ fontSize: 84, fontWeight: 800, color: K.timer }}>RTT = ?</span>
      </Centered>
      <Centered x={linkCx} y={700} op={fade(t, T[0] + 1.6, T[1] - 0.1)}>
        <span style={{ fontSize: 26, color: C.muted }}>host → card → host</span>
      </Centered>

      {/* c1: callouts */}
      <Centered x={gA.c("host")[0]} y={800} op={fade(t, T[1] + 0.5, T[2] - 0.1)} dy={(1 - prog(t, T[1] + 0.5, 0.5)) * 20}>
        <span style={{ fontSize: 30, fontWeight: 700, color: K.host, border: `2px solid ${K.host}`, borderRadius: 999, padding: "6px 22px" }}>
          saturn · Xeon Gold 6438M
        </span>
      </Centered>
      <Centered x={gA.c("card")[0]} y={800} op={fade(t, T[1] + 3.4, T[2] - 0.1)} dy={(1 - prog(t, T[1] + 3.4, 0.5)) * 20}>
        <span style={{ fontSize: 30, fontWeight: 700, color: K.card, border: `2px solid ${K.card}`, borderRadius: 999, padding: "6px 22px" }}>
          Marvell OCTEON 10 CN106xx
        </span>
      </Centered>
      <Centered x={linkCx} y={600} op={fade(t, T[1] + 7.2, T[2] - 0.1)} dy={(1 - prog(t, T[1] + 7.2, 0.5)) * 20}>
        <span style={{ fontSize: 50, fontWeight: 800, color: K.link }}>PCIe Gen5 x4</span>
      </Centered>

      {/* c2–c3: headline numbers */}
      <Stat
        value={
          <>
            {countUp(t, T[2] + 2.8, 1.0, 4.85, 2)}
            {unit(" µs", 36)}
          </>
        }
        label="SDP · per round trip"
        color={K.sdp}
        size={66}
        labelSize={26}
        style={statStyle(xA, prog(t, T[2] + 2.6, 0.5) * statOut)}
      />
      <Stat
        value={
          <>
            ~{countUp(t, T[2] + 5.8, 1.2, 7.5, 1)}
            {unit(" M msg/s")}
          </>
        }
        label="SDP · max rate"
        color={K.sdp}
        size={66}
        labelSize={26}
        style={statStyle(xC, prog(t, T[2] + 5.6, 0.5) * statOut)}
      />
      <Stat
        value={
          <>
            {countUp(t, T[3] + 1.6, 1.2, 0.83, 2)}
            {unit(" µs", 36)}
          </>
        }
        label="one message at a time"
        color={K.pull}
        size={66}
        labelSize={26}
        style={statStyle(70 + SW + 80, prog(t, T[3] + 1.4, 0.5) * statOut)}
      />
      <Stat
        value={
          <>
            ~{countUp(t, T[3] + 5.1, 1.4, 28, 0)}
            {unit(" M msg/s")}
          </>
        }
        label="up to · 4 card cores"
        color={K.ok}
        size={66}
        labelSize={26}
        style={statStyle(990 + SW + 80, prog(t, T[3] + 4.9, 0.5) * statOut)}
      />

      {/* c4: roadmap */}
      <Centered x={960} y={572} op={prog(t, T[4] + 0.3, 0.5)}>
        <span style={{ fontSize: 40, fontWeight: 800, color: C.text, letterSpacing: 1 }}>Roadmap</span>
      </Centered>
      {STAGES.map((s, i) => {
        const base = prog(t, T[4] + 0.4 + i * 0.08, 0.5);
        if (base <= 0.001) return null;
        const lit = prog(t, T[4] + ROAD_AT[i], 0.5);
        const sc = lit > 0 ? 0.94 + 0.06 * pop(t, T[4] + ROAD_AT[i], 0.5) : 0.94;
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: cx0 + i * (cw + cgap),
              top: cy,
              width: cw,
              height: ch,
              opacity: base,
              transform: `scale(${sc})`,
            }}
          >
            <div style={{ position: "absolute", inset: 0, borderRadius: 22, background: C.panel, border: `3px solid ${C.line}` }} />
            <div
              style={{
                position: "absolute",
                inset: 0,
                borderRadius: 22,
                background: `${s.color}26`,
                border: `3px solid ${s.color}`,
                boxShadow: `0 0 28px ${s.color}66`,
                opacity: lit,
              }}
            />
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
                opacity: 0.4 + 0.6 * lit,
              }}
            >
              <div style={{ fontSize: 40, fontWeight: 800, color: s.color }}>{s.label}</div>
              <div style={{ fontSize: 27, color: C.text }}>{ROAD_SUB[i]}</div>
            </div>
          </div>
        );
      })}
    </>
  );
}

export const S01: SceneDef = {
  id: "S01",
  title: "Host ⇄ DPU over PCIe",
  stage: undefined,
  source: "Marvell OCTEON 10 CN106xx · host saturn · Nov 2026",
  dur: DUR,
  cues,
  C: Body,
};
