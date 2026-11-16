import React from "react";
import { Arrow, C, Card, Cue, K, MONO, Pt, SceneDef, Stage, along, clamp01, countUp, fade, lerp, pathD, polyLen, pop, prog, useRealT, useT } from "../lib";
import { Datapath, dpGeom } from "./pcie";
import { Bell, Centered, Hi, SDP_SHORT, SDP_SHORT_BELOW, dimMap } from "./_SceneIntro";

// <cues>
const cues: Cue[] = [
  [0.60, 7.92, "A **closed loop**: the host sends a request, the card reflects it, the host matches the reply.", "The experiment is a closed loop. The host sends a request, the card reflects it back, and the host matches the reply."], // 0
  [8.27, 16.25, "Each message carries a **sequence number**, the **send timestamp**, a payload **pattern**, and a trailing **seq copy**.", "Each message carries a sequence number, its send timestamp, a payload pattern, and a second copy of the sequence number at the very end."], // 1
  [16.60, 24.60, "**Concurrency W** = messages in flight. **W=1** measures latency; a large W measures message rate.", "Concurrency, W, is the number of messages in flight. A window of one measures latency. A large window measures message rate."], // 2
  [24.95, 32.51, "**Batch** = messages per doorbell. Here the batch is always **1**: one message, one notification.", "Batch is the number of messages that share one doorbell. Here the batch is always one: one message, one notification."], // 3
  [32.86, 42.46, "Both ends are **Rust** on DPDK: x86 host, Arm card, one wire format, one set of checks.", "Both ends are written in Rust on top of D P D K: x86 on the host, Arm on the card, with one wire format and one set of checks."], // 4
  [42.81, 49.99, "Cross-check on every result: at **W=1**, the round-trip time must equal **1 / message rate**.", "And every result gets a cross check. With a window of one, the round trip time has to equal one over the message rate."], // 5
];
const DUR = 51.39; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// Closed loop in Datapath base coords: host CPU core → lane 0 → card Arm cores (reflect) → lane 3 → host CPU core.
const OUT_B: Pt[] = [
  [420, 275],
  [672, 275],
  [672, 445],
  [1380, 445],
];
const BACK_B: Pt[] = [
  [1380, 485],
  [1369, 485],
  [1369, 515],
  [684, 515],
  [684, 320],
  [420, 320],
];
const LOOP_B: Pt[] = [...OUT_B, ...BACK_B, [420, 275]];
const U_SPLIT = polyLen(OUT_B) / polyLen(LOOP_B); // request (out) part of the loop
const U_BACK = (polyLen(OUT_B) + 40) / polyLen(LOOP_B); // reply starts after the reflect jog
const LAP = 2.8; // seconds per lap
const TRAIN = 8;
const SPACING = 0.05; // loop fraction between train packets
const wrap = (u: number) => ((u % 1) + 1) % 1;
const colorAt = (u: number) => (u < U_SPLIT ? K.req : K.rep);

/** Wire format of one 64 B cell; `at` = seconds after T[1] when narration names the field. */
const FIELDS = [
  { name: "seq", b: 8, color: C.cyan, at: 1.6 },
  { name: "send TSC", b: 8, color: K.timer, at: 2.8 },
  { name: "tag", b: 8, color: C.muted, at: 3.4 },
  { name: "payload pattern", b: 32, color: "#a9b4d0", at: 4.2 },
  { name: "seq copy", b: 8, color: C.cyan, at: 5.5 },
];
const BX = 160; // bar left
const BPX = 25; // px per byte (64 B → 1600 px)
const BY = 655;
const BH = 100;

function Body() {
  const t = useT();
  const rt = useRealT();

  // One datapath: large in c0, shrinks to the top strip from c1 on (content band below).
  const m = prog(t, T[1] + 0.1, 1.0);
  const g = dpGeom(lerp(0.78, 0.6, m), lerp(211.2, 384, m), lerp(23, 40, m));
  const loop = LOOP_B.map(g.pt);
  const lead = wrap((rt - (T[0] + 0.6)) / LAP);
  const loopOp = prog(t, T[0] + 0.4, 0.5);
  // packet 0 runs all scene; packets 1..7 form the W=8 train from late c2 until c4
  const pk = Array.from({ length: TRAIN }, (_, i) => ({
    i,
    u: wrap(lead - i * SPACING),
    op: i === 0 ? loopOp : prog(t, T[2] + 5.0 + i * 0.12, 0.3) * (1 - prog(t, T[4] + 0.2 + (TRAIN - i) * 0.06, 0.4)),
  })).filter((p) => p.op > 0.001);
  const inFlight = pk.filter((p) => p.op > 0.5).length;
  const gapX = g.c("link")[0];
  const gapY = g.pt([0, 515])[1];

  // ring pulses where the card reflects and where the host matches
  const reflectPt = g.pt([1380, 465]);
  const matchPt = g.pt([420, 297]);
  const pulse = (d: number) => (d >= 0 && d < 0.07 ? d / 0.07 : -1);

  // c3 bells: a packet rings its own doorbell as it leaves the host
  const bellK = fade(t, T[3] + 0.2, T[4] - 0.1);
  const ringOf = (u: number) => (u < 0.1 ? u / 0.1 : 1);

  // c4: highlight the two ends
  const kEnds = fade(t, T[4] + 0.3, T[5] - 0.1);

  // c1: the exploded cell
  const kBar = prog(t, T[1] + 0.3, 0.8);
  const barOut = 1 - prog(t, T[2] - 0.1, 0.5);
  const fieldX = FIELDS.map((_, i) => BX + FIELDS.slice(0, i).reduce((s, f) => s + f.b * BPX, 0));
  const kBracket = prog(t, T[1] + 6.0, 0.8);

  // c2: W panels; c3: batch row
  const wOut = 1 - prog(t, T[3] - 0.1, 0.5);
  const rowX = 820;
  const rowSz = 70;
  const rowGap = 40;

  const step = (at: number) => fade(t, T[0] + at, T[1] - 0.1);
  const pill = (color: string): React.CSSProperties => ({
    fontSize: 30,
    fontWeight: 700,
    color,
    border: `2px solid ${color}`,
    borderRadius: 999,
    padding: "6px 22px",
    background: "rgba(11,16,32,0.85)",
  });
  const dots = (n: number, color: string) => (
    <div style={{ display: "flex", gap: 12, height: 26, alignItems: "center" }}>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} style={{ width: 24, height: 24, borderRadius: 12, background: color, boxShadow: `0 0 12px ${color}` }} />
      ))}
    </div>
  );

  return (
    <>
      <Stage>
        <Datapath g={g} show={dimMap(["core", "cores"], 0.75)} labels={g.s < SDP_SHORT_BELOW ? SDP_SHORT : {}} />
        <Hi g={g} id="core" color={K.host} k={kEnds} />
        <Hi g={g} id="cores" color={K.card} k={kEnds} />
        {/* loop track: request half, reply half */}
        <g opacity={loopOp * 0.55}>
          <path d={pathD(OUT_B.map(g.pt))} fill="none" stroke={K.req} strokeWidth={3} strokeDasharray="10 10" />
          <path d={pathD([...BACK_B, [420, 275] as Pt].map(g.pt))} fill="none" stroke={K.rep} strokeWidth={3} strokeDasharray="10 10" />
        </g>
        {pk.map(({ i, u, op }) => {
          const [x, y] = along(loop, u);
          const s = g.s / 0.78;
          const rp = pulse(u - U_SPLIT);
          const mp = pulse(u);
          return (
            <g key={i} opacity={op}>
              {Array.from({ length: 8 }, (_, j) => {
                const uj = wrap(u - (j + 1) * 0.006);
                const [tx, ty] = along(loop, uj);
                return <circle key={j} cx={tx} cy={ty} r={10 * s * (1 - j / 10)} fill={colorAt(uj)} opacity={0.42 * (1 - j / 8)} />;
              })}
              <circle cx={x} cy={y} r={22 * s} fill={colorAt(u)} opacity={0.2} />
              <circle cx={x} cy={y} r={11 * s} fill={colorAt(u)} />
              {rp >= 0 && <circle cx={reflectPt[0]} cy={reflectPt[1]} r={(16 + 40 * rp) * s} fill="none" stroke={K.card} strokeWidth={4} opacity={1 - rp} />}
              {mp >= 0 && <circle cx={matchPt[0]} cy={matchPt[1]} r={(16 + 40 * mp) * s} fill="none" stroke={K.rep} strokeWidth={4} opacity={1 - mp} />}
              {bellK > 0.001 && u < U_SPLIT * 0.7 && (
                <Bell x={x} y={y - 34} s={1.15} color={K.sdp} ring={ringOf(u)} opacity={bellK * (1 - clamp01((u - U_SPLIT * 0.55) / (U_SPLIT * 0.15)))} />
              )}
            </g>
          );
        })}

        {/* c1: exploded 64 B cell */}
        {kBar > 0.001 && (
          <g opacity={barOut}>
            <rect x={960 - 800 * kBar} y={BY} width={1600 * kBar} height={BH} rx={10} fill="none" stroke={C.dim} strokeWidth={2} />
            {FIELDS.map((f, i) => {
              const k = prog(t, T[1] + f.at, 0.5);
              if (k <= 0.001) return null;
              const x = fieldX[i];
              const w = f.b * BPX;
              return (
                <g key={i} opacity={k}>
                  <rect x={x + 3} y={BY + (1 - k) * 30} width={w - 6} height={BH} rx={10} fill={`${f.color}2e`} stroke={f.color} strokeWidth={3} />
                  <text x={x + w / 2} y={BY + BH / 2 - 14} textAnchor="middle" dominantBaseline="central" fontSize={28} fontWeight={800} fill={f.color} fontFamily="Inter, sans-serif">
                    {f.name}
                  </text>
                  <text x={x + w / 2} y={BY + BH / 2 + 22} textAnchor="middle" dominantBaseline="central" fontSize={22} fill={C.muted} fontFamily={MONO}>
                    {f.b} B
                  </text>
                </g>
              );
            })}
            {[0, 8, 16, 24, 56, 64].map((b) => (
              <text key={b} x={BX + b * BPX} y={BY - 16} textAnchor="middle" fontSize={20} fill={C.muted} fontFamily={MONO} opacity={kBar}>
                {b}
              </text>
            ))}
            {kBracket > 0.001 && (
              <path
                d={`M${BX + 100} ${BY + BH + 8} L${BX + 100} 818 L${BX + 1500} 818 L${BX + 1500} ${BY + BH + 8}`}
                fill="none"
                stroke={C.cyan}
                strokeWidth={3}
                strokeDasharray={`${3000 * kBracket} 3000`}
              />
            )}
          </g>
        )}

        {/* c4: shared crate feeds both binaries */}
        <Arrow x1={778} y1={764} x2={726} y2={764} color={K.ok} w={5} head={14} k={prog(t, T[4] + 6.4, 0.5)} opacity={1 - prog(t, T[5] - 0.1, 0.5)} />
        <Arrow x1={1142} y1={764} x2={1194} y2={764} color={K.ok} w={5} head={14} k={prog(t, T[4] + 6.4, 0.5)} opacity={1 - prog(t, T[5] - 0.1, 0.5)} />
      </Stage>

      {/* c0: the three steps of the loop */}
      <Centered x={gapX} y={gapY + 70} op={fade(t, T[0] + 0.8, T[1] - 0.1)}>
        <span style={{ fontSize: 40, fontWeight: 800, color: C.text }}>closed loop ↻</span>
      </Centered>
      <Centered x={480} y={728} op={step(2.0)} dy={(1 - prog(t, T[0] + 2.0, 0.5)) * 20}>
        <span style={pill(K.req)}>① host sends request</span>
      </Centered>
      <Centered x={960} y={728} op={step(3.6)} dy={(1 - prog(t, T[0] + 3.6, 0.5)) * 20}>
        <span style={pill(K.card)}>② card reflects it</span>
      </Centered>
      <Centered x={1440} y={728} op={step(5.0)} dy={(1 - prog(t, T[0] + 5.0, 0.5)) * 20}>
        <span style={pill(K.rep)}>③ host matches reply</span>
      </Centered>

      {/* c1: cell title + receiver rule */}
      <Centered x={1160} y={590} op={kBar * barOut}>
        <span style={{ fontSize: 30, fontWeight: 700, color: C.text }}>
          one message = one <span style={{ color: K.req }}>64 B</span> cell
        </span>
      </Centered>
      <Centered x={960} y={828} op={kBracket * barOut}>
        <span style={{ fontSize: 26, color: C.text }}>
          valid only if <span style={{ color: C.cyan, fontFamily: MONO }}>head seq == tail seq</span>
        </span>
      </Centered>

      {/* c2: concurrency */}
      <Centered x={gapX} y={gapY + 40} op={fade(t, T[2] + 0.4, T[3] - 0.1)}>
        <span style={pill(K.req)}>in flight: {Math.max(1, inFlight)}</span>
      </Centered>
      <Card
        x={160}
        y={620}
        w={760}
        h={220}
        border={prog(t, T[2] + 3.0, 0.5) > 0.5 && t < T[2] + 5.0 ? K.req : C.line}
        style={{ opacity: prog(t, T[2] + 2.8, 0.5) * wOut, display: "flex", flexDirection: "column", justifyContent: "center", gap: 18 }}
      >
        {dots(1, K.req)}
        <div style={{ fontSize: 50, fontWeight: 800, color: C.text }}>
          W=1 <span style={{ color: C.muted }}>→</span> <span style={{ color: K.timer }}>latency</span>
        </div>
        <div style={{ fontSize: 26, color: C.muted }}>one message in flight</div>
      </Card>
      <Card
        x={1000}
        y={620}
        w={760}
        h={220}
        border={t >= T[2] + 5.0 ? K.req : C.line}
        style={{ opacity: prog(t, T[2] + 4.8, 0.5) * wOut, display: "flex", flexDirection: "column", justifyContent: "center", gap: 18 }}
      >
        {dots(TRAIN, K.req)}
        <div style={{ fontSize: 50, fontWeight: 800, color: C.text }}>
          large W <span style={{ color: C.muted }}>→</span> <span style={{ color: K.ok }}>message rate</span>
        </div>
        <div style={{ fontSize: 26, color: C.muted }}>e.g. W=8: eight messages in flight</div>
      </Card>

      {/* c3: batch = 1 */}
      <Centered x={420} y={640} op={fade(t, T[3] + 0.4, T[4] - 0.1)} scale={0.9 + 0.1 * pop(t, T[3] + 3.6, 0.5)}>
        <div style={{ fontSize: 100, fontWeight: 800, color: C.text, lineHeight: 1.1 }}>
          batch = <span style={{ color: K.sdp }}>1</span>
        </div>
        <div style={{ fontSize: 28, color: C.muted, textAlign: "center" }}>messages per doorbell</div>
      </Centered>
      {(() => {
        const op = fade(t, T[3] + 0.8, T[4] - 0.1);
        if (op <= 0.001) return null;
        return (
          <>
            <Stage opacity={op}>
              {Array.from({ length: TRAIN }, (_, i) => {
                const u = wrap(lead - i * SPACING);
                const ring = ringOf(u);
                const hot = u < 0.1 ? 1 - u / 0.1 : 0;
                const x = rowX + i * (rowSz + rowGap);
                return (
                  <g key={i}>
                    <Bell x={x + rowSz / 2} y={652} s={1.25} color={K.sdp} ring={ring} opacity={0.55 + 0.45 * hot} />
                    <rect x={x} y={690} width={rowSz} height={rowSz} rx={10} fill={`${K.req}${hot > 0 ? "55" : "22"}`} stroke={K.req} strokeWidth={hot > 0 ? 4 : 2} />
                    <text x={x + rowSz / 2} y={690 + rowSz / 2} textAnchor="middle" dominantBaseline="central" fontSize={20} fontFamily={MONO} fill={C.text}>
                      64 B
                    </text>
                  </g>
                );
              })}
            </Stage>
            <Centered x={rowX + (TRAIN * rowSz + (TRAIN - 1) * rowGap) / 2} y={790} op={op}>
              <span style={{ fontSize: 30, color: C.text }}>
                1 message → <span style={{ color: K.sdp, fontWeight: 700 }}>1 doorbell</span>
              </span>
            </Centered>
          </>
        );
      })()}

      {/* c4: one Rust codebase, two targets */}
      <Centered x={960} y={588} op={fade(t, T[4] + 0.4, T[5] - 0.1)}>
        <span style={{ fontSize: 34, fontWeight: 800, color: C.text }}>
          Rust on DPDK <span style={{ color: C.muted }}>· both ends</span>
        </span>
      </Centered>
      <Card x={120} y={648} w={600} h={232} border={K.host} title="host · x86-64" style={{ opacity: fade(t, T[4] + 3.2, T[5] - 0.1) }}>
        <div style={{ fontFamily: MONO, fontSize: 28, color: C.text, lineHeight: 1.4 }}>inline_pingpong</div>
        <div style={{ fontFamily: MONO, fontSize: 28, color: C.text, lineHeight: 1.4 }}>pingpong</div>
        <div style={{ fontFamily: MONO, fontSize: 22, color: C.muted, marginTop: 8 }}>DPDK 26.10.1</div>
      </Card>
      <Card x={780} y={648} w={360} h={232} border={K.ok} title="testbed crate" style={{ opacity: fade(t, T[4] + 6.0, T[5] - 0.1), textAlign: "center" }}>
        <div style={{ fontSize: 28, color: C.text, lineHeight: 1.4 }}>wire format</div>
        <div style={{ fontSize: 28, color: C.text, lineHeight: 1.4 }}>+ checks</div>
        <div style={{ fontFamily: MONO, fontSize: 22, color: C.muted, marginTop: 8 }}>shared by both</div>
      </Card>
      <Card x={1200} y={648} w={600} h={232} border={K.card} title="card · aarch64" style={{ opacity: fade(t, T[4] + 4.6, T[5] - 0.1) }}>
        <div style={{ fontFamily: MONO, fontSize: 28, color: C.text, lineHeight: 1.4 }}>inline_reflector</div>
        <div style={{ fontFamily: MONO, fontSize: 22, color: C.muted, marginTop: 8 }}>DPDK 24.11 · Arm @ 2.0 GHz</div>
      </Card>

      {/* c5: the cross-check */}
      <Centered x={gapX} y={gapY + 40} op={fade(t, T[5] + 0.6)}>
        <span style={pill(K.timer)}>1 lap = 1 RTT</span>
      </Centered>
      <Centered x={960} y={598} op={prog(t, T[5] + 0.5, 0.5)} dy={(1 - prog(t, T[5] + 0.5, 0.5)) * 20}>
        <span style={{ fontSize: 54, fontWeight: 800, color: C.text }}>
          <span style={{ color: K.timer }}>RTT</span> at W=1 ≈ 1 / <span style={{ color: K.ok }}>message rate</span>
        </span>
      </Centered>
      <Centered x={960} y={698} op={prog(t, T[5] + 2.6, 0.5)}>
        <span style={{ fontSize: 30, color: C.muted, marginRight: 24 }}>SDP:</span>
        <span style={{ fontSize: 60, fontWeight: 800, color: K.timer }}>{countUp(t, T[5] + 2.8, 1.0, 4.85, 2)} µs</span>
        <span style={{ fontSize: 50, color: C.muted, margin: "0 28px" }}>↔</span>
        <span style={{ fontSize: 60, fontWeight: 800, color: K.ok }}>{countUp(t, T[5] + 3.6, 1.0, 0.2, 2)} M msg/s</span>
      </Centered>
      <Centered x={960} y={808} op={prog(t, T[5] + 4.6, 0.5)}>
        <span style={{ fontSize: 30, color: C.text, fontFamily: MONO }}>
          1 / 4.85 µs ≈ 0.206 M msg/s <span style={{ color: K.ok }}>✓</span>
        </span>
      </Centered>
    </>
  );
}

export const S02: SceneDef = {
  id: "S02",
  title: "The testbed",
  stage: 0,
  source: "testbed/ (Rust) · host DPDK 26.10.1 · card DPDK 24.11",
  dur: DUR,
  cues,
  C: Body,
};
