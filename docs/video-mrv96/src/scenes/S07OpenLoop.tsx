import React from "react";
import {
  Arrow,
  Axes,
  C,
  Chip as Die,
  Cue,
  Edge,
  K,
  Note,
  Packet,
  PacketStream,
  Pt,
  SceneDef,
  Series,
  Stage,
  Stat,
  along,
  lerp,
  linScale,
  logScale,
  polyLen,
  pop,
  prog,
  useRealT,
  useT,
} from "../lib";
import { Chip, HBarChart, KnobBar, Takeaway } from "./kit";
import { SideBox, win } from "./_Mrv96SceneOpen";

// <cues>
const cues: Cue[] = [
  [0.60, 7.84, "**Experiment 5** removes the loop. Fixed: **one host core, one card core**, WC replies.", "Experiment five removes the loop. Fixed: one host core, one card core, and write combining replies."], // 0
  [8.19, 21.25, "Knob = **lockstep vs open loop**. Lockstep: a new request waits for an earlier reply. Open loop: each side sends at a set rate, independently.", "The knob: lockstep, versus open loop. In lockstep, each new request waits for an earlier reply. In open loop, host and card each send at a set rate, independently, like two people talking at once."], // 1
  [21.60, 33.36, "Lockstep: **14.2 M/s**. Open loop: every target from **5 to 35 M/s each way** is delivered exactly. The 13 M/s wall is gone.", "Lockstep gives fourteen point two million per second. Open loop delivers every target exactly, from five up to thirty-five million per second, each way. The thirteen million wall is gone."], // 2
  [33.71, 42.61, "At **35 + 35 M/s**, median one-way latency is **0.97 µs** forward and **1.24 µs** return.", "At thirty-five plus thirty-five, the median one-way latency is zero point nine seven microseconds forward, and one point two four back."], // 3
  [42.96, 57.88, "Unthrottled: **41.9 + 41.9 = 83.7 M/s** from one host core and one card core. Card receive and send on two cores: **91 M/s** (97 with DMA replies).", "Unthrottled, one host core and one card core move forty-one point nine each way, eighty-three point seven in total. With the card's receiving and sending split over two cores: ninety-one, or ninety-seven with D M A replies."], // 4
  [58.23, 72.39, "But those are medians. At 35 + 35 the forward **p99 is 38.7 µs**; at 20 + 20 even the median is **16.9 µs**. The tail is unexplained.", "But those are medians. At thirty-five plus thirty-five, the ninety-ninth percentile forward latency is thirty-eight point seven microseconds, and at twenty plus twenty even the median is sixteen point nine. That tail is unexplained."], // 5
  [72.74, 87.08, "When each reply is **triggered by a request**, p99 jumps to **35 µs** from **13.5 M/s**. Open loop sidesteps the wall; it does not explain it.", "And when each reply is triggered by a request, as in real request-response traffic, the ninety-ninth percentile jumps to thirty-five microseconds from thirteen and a half million per second. Open loop sidesteps the wall; it does not explain it."], // 6
];
const DUR = 88.48; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
/** Spoken "In open loop…" inside cue 1: the lockstep schematic hands over to two free-running streams. */
const TM = T[1] + 6.3;

/** Host ⇄ card diagram: m = 0 large (cues 0–1), m = 1 compact strip on top (cues 2–6). */
function geo(m: number) {
  const w = lerp(430, 330, m);
  const h = lerp(400, 190, m);
  const y = lerp(225, 200, m);
  const host = { x: lerp(80, 60, m), y, w, h };
  const card = { x: lerp(1410, 1530, m), y, w, h };
  return { host, card, x0: host.x + w, x1: card.x, fwdY: lerp(355, 255, m), retY: lerp(505, 340, m), fs: lerp(30, 24, m) };
}

/** Lockstep schematic, 2.6 s cycle: one request goes, the gate stays shut until its reply is back, then the next. */
function lockstep(rt: number) {
  const s = rt - (T[1] + 0.5);
  if (s < 0) return { req: -1, rep: -1, turn: false, open: 1 };
  const u = s % 2.6;
  return {
    req: u < 0.95 ? u / 0.95 : -1,
    rep: u >= 1.25 && u < 2.2 ? (u - 1.25) / 0.95 : -1,
    turn: u >= 0.95 && u < 1.25,
    open: u < 0.15 ? 1 : u < 0.4 ? 1 - (u - 0.15) / 0.25 : u < 2.2 ? 0 : Math.min(1, (u - 2.2) / 0.3),
  };
}

/** Packet train on a U-shaped path: cyan (request) until `split`, green (the reply it triggers) after the turn. */
function EchoStream({ pts, travel, split, opacity }: { pts: Pt[]; travel: number; split: number; opacity: number }) {
  if (opacity <= 0 || travel <= 0) return null;
  const L = polyLen(pts);
  const out: React.ReactNode[] = [];
  for (let i = Math.floor(travel / 64); i >= 0; i--) {
    const d = travel - i * 64;
    if (d > L) break;
    const [x, y] = along(pts, d / L);
    out.push(<rect key={i} x={x - 9} y={y - 9} width={18} height={18} rx={3} fill={d < split ? K.fwd : K.ret} />);
  }
  return <g opacity={opacity}>{out}</g>;
}

// cues 2–3: delivered vs target rate, each way
const A = { x0: 190, x1: 1010, y0: 800, y1: 490 };
const sxA = linScale(0, 40, A.x0, A.x1);
const syA = linScale(0, 40, A.y0, A.y1);
const TARGETS = [5, 10, 13.5, 20, 25, 30, 35];
// cue 5: forward one-way latency on a log axis
const LX0 = 260;
const LX1 = 1660;
const LY = 650;
const lx = logScale(0.5, 50, LX0, LX1);
const MARKS = [
  { v: 0.97, label: "p50 0.97 µs", color: K.fwd, at: 0.6 },
  { v: 5.55, label: "p90 5.55 µs", color: C.text, at: 3.0 },
  { v: 38.7, label: "p99 38.7 µs", color: K.slow, at: 4.2 },
];
// cue 6: request–response round-trip p99 vs offered rate
const RR = { x0: 200, x1: 1000, y0: 800, y1: 490 };
const sxR = linScale(0, 25, RR.x0, RR.x1);
const syR = logScale(0.5, 50, RR.y0, RR.y1);
const RTT: [number, number][] = [
  [5, 1.03],
  [10, 1.26],
  [13.5, 35.4],
  [20, 38.5],
];
const RTT_AT = [1.2, 2.6, 6.4, 8.4];
/** Value-label offset and anchor per point, clear of the line segments. */
const RTT_LAB: [number, number, "start" | "middle" | "end"][] = [
  [0, -22, "middle"],
  [14, 36, "start"],
  [-30, 8, "end"],
  [0, -22, "middle"],
];
const ticks = (vs: number[], unit = "") =>
  vs.map((v, i) => [v, i === vs.length - 1 ? `${v}${unit}` : String(v)] as [number, string]);

function Body() {
  const t = useT();
  const rt = useRealT();
  const m = prog(t, T[2], 0.9);
  const g = geo(m);
  const midX = (g.x0 + g.x1) / 2;

  // diagram build-up (cue 0), then the sending rule: lockstep → open loop → request–response (cue 6)
  const kHost = prog(t, T[0], 0.6);
  const kCard = prog(t, T[0] + 0.5, 0.6);
  const kf = prog(t, T[0] + 1.4, 0.7);
  const kr = prog(t, T[0] + 2.2, 0.7);
  const kDie = prog(t, T[0] + 3.2, 0.5);
  const ls = lockstep(rt);
  const lsK = prog(t, T[1] + 0.2, 0.4) * (1 - prog(t, TM - 0.2, 0.5));
  const olK = prog(t, TM, 0.5) * (1 - prog(t, T[6] - 0.45, 0.45));
  const echoK = prog(t, T[6], 0.4);
  const trav = Math.max(0, rt - TM) * 380 + Math.max(0, rt - (T[4] + 0.3)) * 220; // faster once unthrottled
  const fwdP: Pt[] = [
    [g.x0 + 6, g.fwdY],
    [g.x1 - 24, g.fwdY],
  ];
  const retP: Pt[] = [
    [g.x1 - 6, g.retY],
    [g.x0 + 24, g.retY],
  ];
  const uX = g.x1 + 34;
  const echoP: Pt[] = [
    [g.x0 + 6, g.fwdY],
    [uX, g.fwdY],
    [uX, g.retY],
    [g.x0 + 24, g.retY],
  ];
  const flowing = olK > 0.5 || echoK > 0.5;
  const gx = g.x0 + 64;
  const halo = (y: number, color: string, k: number) =>
    k > 0 ? (
      <line x1={g.x0 + 12} y1={y} x2={g.x1 - 12} y2={y} stroke={color} strokeWidth={26} strokeLinecap="round" opacity={0.3 * k} />
    ) : null;

  const chK = win(t, T[2] + 0.7, T[4]);
  const t5 = win(t, T[5] + 0.3, T[6]);
  const t6 = win(t, T[6] + 0.3);
  const sk = (prog(t, T[6] + 1.2, 1.4) + prog(t, T[6] + 5.4, 1.0) + prog(t, T[6] + 7.6, 0.8)) / 3;
  const k20 = prog(t, T[5] + 8.0, 0.6);
  const x20 = lx(16.9);
  const kneeK = prog(t, T[6] + 6.0, 0.6);
  const panel: React.CSSProperties = { position: "absolute", left: 1100, top: 488, width: 760 };

  return (
    <>
      <Stage>
        <SideBox {...g.host} color={K.host} title="HOST" size={lerp(34, 26, m)} opacity={kHost} />
        <SideBox {...g.card} color={K.card} title="DPU" size={lerp(34, 26, m)} opacity={kCard} />
        <Die x={g.host.x + g.host.w / 2} y={g.host.y + g.host.h * 0.6} w={lerp(230, 170, m)} h={lerp(116, 76, m)}
          label="host core" color={K.host} fontSize={lerp(30, 24, m)} opacity={kDie}
          glow={flowing || (lsK > 0.5 && ls.req >= 0 && ls.req < 0.25)} />
        <Die x={g.card.x + g.card.w / 2} y={g.card.y + g.card.h * 0.6} w={lerp(230, 170, m)} h={lerp(116, 76, m)}
          label="card core" color={K.card} fontSize={lerp(30, 24, m)} opacity={kDie} glow={flowing || (lsK > 0.5 && ls.turn)} />
        <text x={midX} y={(g.fwdY + g.retY) / 2 + 10} textAnchor="middle" fontSize={28} fontWeight={700} fill={C.muted}
          opacity={kf * (1 - m)}>
          PCIe
        </text>
        {halo(g.fwdY, K.fwd, win(t, T[3] + 3.0, T[3] + 6.2))}
        {halo(g.retY, K.ret, win(t, T[3] + 6.0, T[4]))}
        {halo(g.fwdY, K.slow, win(t, T[5] + 4.2, T[6]))}
        <Arrow x1={g.x0} y1={g.fwdY} x2={g.x1 - 4} y2={g.fwdY} color={K.fwd} w={5} k={kf} head={18} />
        <Arrow x1={g.x1} y1={g.retY} x2={g.x0 + 4} y2={g.retY} color={K.ret} w={5} k={kr} head={18} />
        <text x={midX} y={g.fwdY - 16} textAnchor="middle" fontSize={g.fs} fontWeight={700} fill={K.fwd} opacity={kf}>
          requests →
        </text>
        <text x={midX} y={g.retY + g.fs + 12} textAnchor="middle" fontSize={g.fs} fontWeight={700} fill={K.ret} opacity={kr}>
          ← replies (write-combining)
        </text>
        {lsK > 0 && (
          <g opacity={lsK}>
            <rect x={gx - 9} y={g.fwdY - 40 - ls.open * 62} width={18} height={80} rx={6} fill={K.wait} />
            <text x={gx + 34} y={g.fwdY + 64} fontSize={28} fontWeight={700} fill={K.wait}>
              waits for its reply
            </text>
            {ls.req > 0 && <Packet pts={fwdP} k={ls.req} color={K.fwd} trail={false} r={12} />}
            {ls.rep > 0 && <Packet pts={retP} k={ls.rep} color={K.ret} trail={false} r={12} />}
          </g>
        )}
        <PacketStream pts={fwdP} travel={trav} gap={64} size={18} color={K.fwd} opacity={olK} />
        <PacketStream pts={retP} travel={trav * 0.93 + 20} gap={60} size={18} color={K.ret} opacity={olK} />
        <EchoStream pts={echoP} travel={Math.max(0, rt - T[6]) * 520} split={uX - g.x0 - 6 + (g.retY - g.fwdY) / 2}
          opacity={echoK} />
        <g fontSize={26} fontWeight={700} opacity={win(t, TM + 0.6, T[2])}>
          <text x={g.x0 + 24} y={g.fwdY - 18} fill={K.fwd}>
            rate R
          </text>
          <text x={g.x1 - 24} y={g.retY + 44} textAnchor="end" fill={K.ret}>
            rate R
          </text>
        </g>
        <text x={midX} y={(g.fwdY + g.retY) / 2 + 9} textAnchor="middle" fontSize={24} fill={C.muted}
          opacity={win(t, T[2] + 0.7, T[6])}>
          open loop · each side sends at its own set rate
        </text>
        <text x={midX} y={(g.fwdY + g.retY) / 2 + 9} textAnchor="middle" fontSize={24} fill={C.muted}
          opacity={win(t, T[6] + 0.1)}>
          request–response · each reply is triggered by a request
        </text>

        {/* cues 2–3: every target is delivered; the lockstep rate for reference */}
        {chK > 0 && (
          <g opacity={chK}>
            <Axes x0={A.x0} x1={A.x1} y0={A.y0} y1={A.y1} sx={sxA} sy={syA} size={24}
              xticks={TARGETS.map((v) => [v, String(v)] as [number, string])} yticks={ticks([0, 10, 20, 30, 40])}
              xlabel="target rate R, each way (M/s)" ylabel="delivered, each way (M/s)" />
            <Edge x1={sxA(0)} y1={syA(0)} x2={sxA(40)} y2={syA(40)} k={prog(t, T[2] + 0.9, 0.9)} color={C.muted} w={3}
              dash="10 8" />
            <text x={A.x1 - 10} y={syA(3)} textAnchor="end" fontSize={24} fill={C.muted} opacity={prog(t, T[2] + 1.5, 0.5)}>
              dashed: delivered = target
            </text>
            <Edge x1={A.x0} y1={syA(14.2)} x2={A.x1} y2={syA(14.2)} k={prog(t, T[2] + 0.8, 0.8)} color={K.slow} w={4}
              dash="14 8" />
            <text x={A.x1 - 10} y={syA(14.2) + 34} textAnchor="end" fontSize={28} fontWeight={800} fill={K.slow}
              opacity={prog(t, T[2] + 1.3, 0.5)}>
              lockstep 14.2 M/s
            </text>
            {TARGETS.map((v, i) => {
              const s = pop(t, T[2] + 3.6 + i * 0.5, 0.5);
              return s > 0 ? <circle key={v} cx={sxA(v)} cy={syA(v)} r={11 * s} fill={K.fast} stroke={C.bg} strokeWidth={2} /> : null;
            })}
            <g opacity={win(t, T[3] + 0.3, T[4])}>
              <circle cx={sxA(35)} cy={syA(35)} r={21 + 4 * Math.sin(rt * 5)} fill="none" stroke={K.fast} strokeWidth={3} />
              <text x={sxA(35) - 30} y={syA(35) - 16} textAnchor="end" fontSize={28} fontWeight={800} fill={K.fast}>
                35 + 35
              </text>
            </g>
          </g>
        )}

        {/* cue 4: unthrottled throughput */}
        <HBarChart x0={700} x1={1520} y1={515} y0={770} max={100} size={28} labelSize={28}
          bars={[
            { label: "1 card core", sub: "receives and replies", value: 83.7, text: "41.9 + 41.9 = 83.7 M/s", color: K.fast },
            { label: "2 card cores · WC replies", sub: "receive / send split", value: 91, text: "91 M/s", color: K.fast },
            { label: "2 card cores · DMA replies", sub: "receive / send split", value: 97, text: "97 M/s", color: K.fast },
          ]}
          k={(i) => prog(t, T[4] + [0.6, 9.2, 11.6][i], 0.9)} opacity={win(t, T[4] + 0.2, T[5])} />

        {/* cue 5: the medians hide a long forward tail */}
        {t5 > 0 && (
          <g opacity={t5}>
            <defs>
              <linearGradient id="s07tail" x1="0" x2="1" y1="0" y2="0">
                <stop offset="0" stopColor={K.fwd} />
                <stop offset="1" stopColor={K.slow} />
              </linearGradient>
            </defs>
            <rect x={lx(0.97)} y={LY - 8} width={(lx(38.7) - lx(0.97)) * prog(t, T[5] + 4.2, 1.0)} height={16} rx={8}
              fill="url(#s07tail)" opacity={0.6} />
            <line x1={LX0} y1={LY} x2={LX1} y2={LY} stroke={C.dim} strokeWidth={3} />
            {ticks([0.5, 1, 5, 10, 50], " µs").map(([v, l]) => (
              <g key={v}>
                <line x1={lx(v)} y1={LY} x2={lx(v)} y2={LY + 12} stroke={C.dim} strokeWidth={2.5} />
                <text x={lx(v)} y={LY + 40} textAnchor="middle" fontSize={24} fill={C.muted}>
                  {l}
                </text>
              </g>
            ))}
            {MARKS.map((mk) => {
              const k = prog(t, T[5] + mk.at, 0.6);
              if (k <= 0) return null;
              const x = lx(mk.v);
              return (
                <g key={mk.label} opacity={k}>
                  <line x1={x} y1={LY} x2={x} y2={LY - 80 * k} stroke={mk.color} strokeWidth={4} />
                  <circle cx={x} cy={LY} r={10} fill={mk.color} />
                  <text x={x} y={LY - 98} textAnchor="middle" fontSize={30} fontWeight={800} fill={mk.color}>
                    {mk.label}
                  </text>
                </g>
              );
            })}
            {k20 > 0 && (
              <g opacity={k20}>
                <line x1={x20} y1={LY} x2={x20} y2={LY + 92 * k20} stroke={K.slow} strokeWidth={4} strokeDasharray="8 6" />
                <circle cx={x20} cy={LY} r={10} fill={C.bg} stroke={K.slow} strokeWidth={4} />
                <text x={x20} y={LY + 128} textAnchor="middle" fontSize={30} fontWeight={800} fill={K.slow}>
                  20 + 20: even p50 = 16.9 µs
                </text>
              </g>
            )}
          </g>
        )}

        {/* cue 6: request–response: the round-trip p99 jumps at 13.5 M/s */}
        {t6 > 0 && (
          <g opacity={t6}>
            <Axes x0={RR.x0} x1={RR.x1} y0={RR.y0} y1={RR.y1} sx={sxR} sy={syR} size={24}
              xticks={RTT.map(([v]) => [v, String(v)] as [number, string])} yticks={ticks([0.5, 1, 5, 10, 50])}
              xlabel="offered request rate (M/s)" ylabel="round-trip p99 (µs, log scale)" />
            <rect x={sxR(13.5) - 22} y={RR.y1} width={44} height={RR.y0 - RR.y1} fill={K.slow} opacity={0.16 * kneeK} />
            <text x={sxR(13.5) + 32} y={650} fontSize={26} fontWeight={800} fill={K.slow} opacity={kneeK}>
              knee
            </text>
            <Series pts={RTT} sx={sxR} sy={syR} k={sk} color={C.text} w={4} r={9} />
            {RTT.map(([x, y], i) => {
              const k = prog(t, T[6] + RTT_AT[i] + 0.2, 0.4);
              const [dx, dy, anchor] = RTT_LAB[i];
              return (
                <text key={x} x={sxR(x) + dx} y={syR(y) + dy} textAnchor={anchor} fontSize={28} fontWeight={800}
                  fill={i < 2 ? K.fast : K.slow} opacity={k}>
                  {y}
                </text>
              );
            })}
          </g>
        )}
      </Stage>

      {/* cue 1: the rule in words (large layout) */}
      <div style={{ position: "absolute", left: 160, top: 700, width: 1600, textAlign: "center", opacity: win(t, T[1] + 0.3, TM) }}>
        <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: 3, color: K.wait }}>LOCKSTEP</div>
        <div style={{ fontSize: 36, marginTop: 6 }}>each new request waits for an earlier reply</div>
      </div>
      <div style={{ position: "absolute", left: 160, top: 700, width: 1600, textAlign: "center", opacity: win(t, TM + 0.05, T[2]) }}>
        <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: 3, color: K.fast }}>OPEN LOOP</div>
        <div style={{ fontSize: 36, marginTop: 6, lineHeight: 1.3 }}>
          host and card each send at a set rate R, independently — like two people talking at once
        </div>
      </div>
      <div style={{ opacity: win(t, T[1] + 0.6) }}>
        <Note x={lerp(80, 60, m)} y={lerp(640, 398, m)} w={480}>
          packet dots are schematic
        </Note>
      </div>

      {/* cue 2 */}
      <div style={{ ...panel, opacity: win(t, T[2] + 3.6, T[3]) }}>
        <div style={{ fontSize: 24, fontWeight: 800, letterSpacing: 3, color: K.fast }}>OPEN LOOP</div>
        <div style={{ fontSize: 32, lineHeight: 1.3, marginTop: 8 }}>every target from 5 to 35 M/s each way is delivered exactly</div>
      </div>
      <div style={{ position: "absolute", left: 1100, top: 650, fontSize: 46, fontWeight: 800, color: K.fast,
        opacity: win(t, T[2] + 8.4, T[3]), transform: `scale(${lerp(0.85, 1, prog(t, T[2] + 8.4, 0.5))})`, transformOrigin: "0 50%" }}>
        the ~13 M/s wall is gone
      </div>
      <div style={{ position: "absolute", left: 1100, top: 770, opacity: win(t, T[2] + 1.2, T[4]) }}>
        <Chip>1 host core + 1 card core</Chip>
      </div>

      {/* cue 3 */}
      <div style={{ ...panel, fontSize: 30, opacity: win(t, T[3] + 0.3, T[4]) }}>median one-way latency at 35 + 35 M/s</div>
      <Stat value="0.97 µs" label="forward p50" color={K.fwd} size={64}
        style={{ position: "absolute", left: 1100, top: 548, width: 350, boxSizing: "border-box", opacity: win(t, T[3] + 3.0, T[4]) }} />
      <Stat value="1.24 µs" label="return p50" color={K.ret} size={64}
        style={{ position: "absolute", left: 1480, top: 548, width: 350, boxSizing: "border-box", opacity: win(t, T[3] + 6.0, T[4]) }} />

      {/* cue 4 */}
      <div style={{ position: "absolute", left: 330, top: 452, fontSize: 28, color: C.muted, opacity: win(t, T[4] + 0.2, T[5]) }}>
        unthrottled throughput, both directions together
      </div>
      <div style={{ position: "absolute", left: 700, top: 800, opacity: win(t, T[4] + 1.6, T[5]) }}>
        <Chip>1 host core in every case</Chip>
      </div>

      {/* cue 5 */}
      <div style={{ position: "absolute", left: 260, top: 452, fontSize: 28, color: C.muted, opacity: t5 }}>
        forward one-way latency at 35 + 35 M/s (log scale)
      </div>
      <div style={{ position: "absolute", left: 1150, top: 518, opacity: win(t, T[5] + 11.3, T[6]) }}>
        <Chip color={K.slow}>tail: unexplained</Chip>
      </div>

      {/* cue 6 */}
      <div style={{ ...panel, fontSize: 30, lineHeight: 1.3, opacity: t6 }}>request–response: each reply is triggered by a request</div>
      <div style={{ position: "absolute", left: 1100, top: 580, opacity: win(t, T[6] + 0.8) }}>
        <Chip>1 host core + 1 card core</Chip>
      </div>
      <Takeaway x={1100} y={660} w={760} k={prog(t, T[6] + 9.6, 0.6)}>
        Open loop sidesteps the wall; it doesn't explain it.
      </Takeaway>

      <KnobBar fixed={["1 host core", "1 card core", "WC replies"]} knob="sending rule" options={["lockstep", "open loop"]}
        active={t < T[1] ? -1 : prog(t, TM - 0.3, 0.6)} k={prog(t, 0.1, 0.5)} />
    </>
  );
}

export const S07: SceneDef = {
  id: "S07",
  title: "Exp 5 · Remove the loop",
  stage: 3,
  source: "MRV-70 · logs/mrv70-2026-11-18 · 483 runs × 300 ms",
  dur: DUR,
  cues,
  C: Body,
};
