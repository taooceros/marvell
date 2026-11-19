import React from "react";
import { Arrow, C, Cue, FONT, K, Note, PacketStream, Pt, SceneDef, Stage, along, appear, clamp01, lerp, lin, prog, useRealT, useT } from "../lib";
import { CellState, Chip, RingStrip, ringCenter } from "./kit";
import { bump } from "./_Mrv96FrameScenes";

// <cues>
const cues: Cue[] = [
  [0.60, 11.46, "One way, the path is fast as long as receivers **stay behind the landing band**: **161.6 M/s** from one host core.", "Putting it together. One way, the path is fast as long as receivers stay behind the band of writes still landing: one hundred sixty-one point six million per second from one host core."], // 0
  [11.81, 21.97, "In a closed loop it settles near **13 M/s**: both sides mostly wait, the queue sits **in transit**, and the host's **send pacing** changes it.", "In a closed loop, it settles near thirteen million. Both sides mostly wait, the queue sits in transit, forward or back, and the pacing of the host's sends changes it."], // 1
  [22.32, 33.64, "Without the loop, one host core moves **83.7 M/s** both ways at **~1 µs** median latency. But **p99 tails** of tens of µs remain.", "Without the loop, one host core moves eighty-three point seven million per second in both directions, at about one microsecond median latency. But tail latencies of tens of microseconds remain."], // 2
  [33.99, 41.37, "Ruled out: reply interleaving does **not** break write-combining, and card cores do **not** interfere at 64-byte spacing.", "Ruled out: interleaving replies does not break write combining, and card cores do not interfere at sixty-four byte spacing."], // 3
  [41.72, 53.60, "Next: explain and remove the slow state (pace host bursts, cap reply polling, sweep the mesh clock), and report p99 over long runs.", "Next, we want to explain and remove the slow state: pace the host's bursts, cap how many replies it polls at once, and sweep the mesh clock. And report tail latency over long runs."], // 4
  [53.95, 65.15, "Then: let card cores drive the host's **Intel DSA** engine directly, and build a **minimal message layer** with a real request–response workload.", "Then two directions. Let card cores drive the host's Intel D S A copy engine directly, and build a minimal message layer, tested with a real request-response workload."], // 5
];
const DUR = 66.55; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
/** Causal map: three regimes on top (one way → closed loop → no loop), ruled-out suspects and next steps below. */
const COL = [60, 680, 1300];
const CW = 560;
const TOP = { y: 128, h: 448 };
const BOT = { y: 616, h: 264 };
const NEUTRAL = "#5b6787";
/** Card 1's mini request ring: consumed · landed (the core's `next` reads here) · in-flight landing band · empty. */
const RING = { x: 127, y: 420, n: 12, cw: 30, ch: 40, gap: 6 };
const RING_STATE: CellState[] = [
  "consumed", "consumed", "consumed", "landed", "landed", "landed", "landed", "inflight", "inflight", "inflight", "inflight", "empty",
];
/** Card 2's loop icon (host ⇄ card, both lanes) and the path its queued messages travel. */
const LOOP = { hx: 712, cx: 1120, bw: 88, y0: 452, y1: 528, fy: 470, ry: 510 };
const LOOP_PATH: Pt[] = [
  [LOOP.hx + LOOP.bw, LOOP.fy],
  [LOOP.cx, LOOP.fy],
  [LOOP.cx, LOOP.ry],
  [LOOP.hx + LOOP.bw, LOOP.ry],
  [LOOP.hx + LOOP.bw, LOOP.fy],
];
/** Card 3's open-loop icon: same boxes, independent streams both ways. */
const OPEN = { hx: 1332, cx: 1740 };

const abs = (top: number, left = 24): React.CSSProperties => ({ position: "absolute", left, right: 24, top });

/** One finding on the map: coloured frame, title + experiment tag; `glow` flashes the frame. */
function MapCard({
  x,
  y,
  w,
  h,
  color,
  k,
  glow,
  title,
  tag,
  titleColor,
  children,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  color: string;
  k: number;
  glow: number;
  title: string;
  tag?: string;
  titleColor?: string;
  children?: React.ReactNode;
}) {
  if (k <= 0) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        opacity: k,
        transform: `translateY(${(1 - k) * 26}px)`,
        background: "rgba(20,27,46,0.94)",
        border: `2px solid ${color}`,
        borderRadius: 20,
        boxSizing: "border-box",
        fontFamily: FONT,
        color: C.text,
        boxShadow: `0 0 ${12 + 44 * glow}px ${color}${glow > 0.02 ? "aa" : "30"}`,
      }}
    >
      <div style={{ ...abs(12), right: 20, display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ fontSize: 34, fontWeight: 800, color: titleColor ?? color }}>{title}</span>
        <span style={{ flex: 1 }} />
        {tag && (
          <Chip color={color} size={22}>
            {tag}
          </Chip>
        )}
      </div>
      {children}
    </div>
  );
}

function Dot({ color }: { color: string }) {
  return <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 3, background: color, marginRight: 14, flex: "none" }} />;
}

function Body() {
  const t = useT();
  const rt = useRealT();

  // ---- card entrances (one per cue) and the closing glow sweep (T5)
  const start = [T[0] + 0.2, T[1] + 0.3, T[2] + 0.3, T[3] + 0.2, T[4] + 0.2];
  const ck = start.map((a) => prog(t, a, 0.6));
  const dy = ck.map((k) => (1 - k) * 26);
  const sweepK = lin(t, T[5] + 9.6, 1.8);
  const sweepX = lerp(-500, 2400, sweepK);
  const centers = [COL[0] + CW / 2, COL[1] + CW / 2, COL[2] + CW / 2, COL[0] + CW / 2, 1270];
  const glow = start.map((a, i) => Math.max(bump(t, a, 1.2), sweepK > 0 && sweepK < 1 ? clamp01(1 - Math.abs(sweepX - centers[i]) / 320) : 0));

  // ---- card 1 ring: cells reveal, the `next` cursor idles behind the landing band
  const ringK = lin(t, T[0] + 3.4, 1.0);
  const bandK = prog(t, T[0] + 4.6, 0.5);
  const nextK = prog(t, T[0] + 4.0, 0.5);
  const bandMid = (ringCenter(RING.x, RING.cw, RING.gap, 7) + ringCenter(RING.x, RING.cw, RING.gap, 10)) / 2;

  // ---- arrows of the map
  const a1 = prog(t, T[1] + 0.0, 0.6);
  const a2 = prog(t, T[2] + 0.0, 0.6);
  const a3 = prog(t, T[3] + 1.0, 0.6);
  const a45 = prog(t, T[4] + 0.4, 0.5);

  return (
    <>
      <MapCard x={COL[0]} y={TOP.y} w={CW} h={TOP.h} color={K.fast} k={ck[0]} glow={glow[0]} title="One way" tag="Exp 1–2">
        <div style={{ ...abs(62), fontSize: 26, lineHeight: 1.25, ...appear(t, T[0] + 2.4, undefined, 12) }}>
          fast if receivers stay
          <br />
          <b style={{ color: K.fast }}>behind the landing band</b>
        </div>
        <div style={{ ...abs(132), display: "flex", alignItems: "center", gap: 12, ...appear(t, T[0] + 6.6, undefined, 12) }}>
          <span style={{ fontSize: 76, fontWeight: 800, color: K.fast, lineHeight: 1 }}>161.6</span>
          <span style={{ fontSize: 34, fontWeight: 700, alignSelf: "flex-end", marginBottom: 4 }}>M/s</span>
          <span style={{ fontSize: 24, color: C.muted, marginLeft: 8, lineHeight: 1.2 }}>
            one host core
            <br />+ two card cores
          </span>
        </div>
        <div style={{ ...abs(346), ...appear(t, T[0] + 8.4, undefined, 12) }}>
          <Chip color={K.fast} size={24}>
            Exp 1 · pause 3 µs → +26 %
          </Chip>
        </div>
        <div style={{ ...abs(394), ...appear(t, T[0] + 9.2, undefined, 12) }}>
          <Chip color={K.fast} size={24}>
            Exp 2 · position, not bandwidth
          </Chip>
        </div>
      </MapCard>

      <MapCard x={COL[1]} y={TOP.y} w={CW} h={TOP.h} color={K.slow} k={ck[1]} glow={glow[1]} title="Closed loop" tag="Exp 3–4">
        <div style={{ ...abs(62), display: "flex", alignItems: "center", gap: 12, ...appear(t, T[1] + 1.2, undefined, 12) }}>
          <span style={{ fontSize: 76, fontWeight: 800, color: K.slow, lineHeight: 1 }}>~13</span>
          <span style={{ fontSize: 34, fontWeight: 700, alignSelf: "flex-end", marginBottom: 4 }}>M/s</span>
          <span style={{ fontSize: 24, color: C.muted, marginLeft: 8, lineHeight: 1.2 }}>
            one host core
            <br />+ four card cores
          </span>
        </div>
        {[
          { tag: "Exp 4", text: "both sides mostly wait", at: 3.2, top: 156 },
          { tag: "Exp 3", text: "queue in transit, forward or back", at: 4.9, top: 200 },
          { tag: "Exp 4", text: "host send pacing changes it", at: 7.5, top: 244 },
        ].map((b) => (
          <div key={b.text} style={{ ...abs(b.top), display: "flex", alignItems: "center", gap: 12, ...appear(t, T[1] + b.at, undefined, 12) }}>
            <Chip color={K.slow} size={20}>
              {b.tag}
            </Chip>
            <span style={{ fontSize: 25, whiteSpace: "nowrap" }}>{b.text}</span>
          </div>
        ))}
        <div style={{ opacity: prog(t, T[1] + 1.4, 0.6) }}>
          <Note x={24} y={410} w={512}>
            <div style={{ textAlign: "center" }}>icons and ring cells are schematic</div>
          </Note>
        </div>
      </MapCard>

      <MapCard x={COL[2]} y={TOP.y} w={CW} h={TOP.h} color={K.fwd} k={ck[2]} glow={glow[2]} title="No loop" tag="Exp 5">
        <div style={{ ...abs(62), display: "flex", alignItems: "baseline", gap: 12, ...appear(t, T[2] + 1.4, undefined, 12) }}>
          <span style={{ fontSize: 76, fontWeight: 800, color: K.fwd, lineHeight: 1 }}>83.7</span>
          <span style={{ fontSize: 34, fontWeight: 700 }}>M/s</span>
          <span style={{ fontSize: 28, fontWeight: 700, marginLeft: 6 }}>both ways</span>
        </div>
        <div style={{ ...abs(150), fontSize: 24, color: C.muted, ...appear(t, T[2] + 3.2, undefined, 12) }}>one host core + one card core</div>
        <div style={{ ...abs(194), display: "flex", alignItems: "baseline", gap: 10, ...appear(t, T[2] + 5.6, undefined, 12) }}>
          <span style={{ fontSize: 32, fontWeight: 800, color: K.fwd }}>~1 µs</span>
          <span style={{ fontSize: 26 }}>median latency</span>
        </div>
        <div style={{ ...abs(240), display: "flex", alignItems: "baseline", gap: 10, ...appear(t, T[2] + 8.2, undefined, 12) }}>
          <span style={{ fontSize: 26, color: K.slow, fontWeight: 700 }}>but p99 tails:</span>
          <span style={{ fontSize: 32, fontWeight: 800, color: K.slow }}>tens of µs</span>
        </div>
      </MapCard>

      <MapCard x={COL[0]} y={BOT.y} w={CW} h={BOT.h} color={C.pink} k={ck[3]} glow={glow[3]} title="Ruled out" tag="Exp 6">
        <div style={{ ...abs(64), display: "flex", alignItems: "flex-start", gap: 12, ...appear(t, T[3] + 0.9, undefined, 12) }}>
          <Chip color={C.pink} size={22}>
            6a
          </Chip>
          <span style={{ fontSize: 25, lineHeight: 1.3 }}>
            interleaved replies keep write‑combining <span style={{ color: C.pink, fontWeight: 700 }}>(99.99 % full lines)</span>
          </span>
        </div>
        <div style={{ ...abs(154), display: "flex", alignItems: "center", gap: 12, ...appear(t, T[3] + 4.2, undefined, 12) }}>
          <Chip color={C.pink} size={22}>
            6b
          </Chip>
          <span style={{ fontSize: 25 }}>card cores: 64‑byte coherence</span>
        </div>
      </MapCard>

      <MapCard x={680} y={BOT.y} w={1180} h={BOT.h} color={NEUTRAL} titleColor={C.text} k={ck[4]} glow={glow[4]} title="Next">
        <div style={{ position: "absolute", left: 24, top: 60, width: 530 }}>
          <div style={{ fontSize: 24, color: C.muted, ...appear(t, T[4] + 0.9, undefined, 10) }}>explain + remove the slow state</div>
          {["pace host bursts", "cap reply polling", "sweep the mesh clock"].map((s, i) => (
            <div key={s} style={{ fontSize: 27, marginTop: 6, display: "flex", alignItems: "center", ...appear(t, T[4] + [3.1, 4.5, 6.8][i], undefined, 10) }}>
              <Dot color={K.slow} />
              {s}
            </div>
          ))}
          <div style={{ fontSize: 27, marginTop: 12, display: "flex", alignItems: "center", ...appear(t, T[4] + 8.6, undefined, 10) }}>
            <Dot color={K.fwd} />
            p99 over long runs
          </div>
        </div>
        <div style={{ position: "absolute", left: 578, top: 64, width: 2, height: 172, background: C.line, opacity: ck[4] }} />
        <div style={{ position: "absolute", left: 604, top: 60, width: 550 }}>
          <div style={{ fontSize: 24, color: C.muted, ...appear(t, T[5] + 0.3, undefined, 10) }}>then</div>
          <div style={{ fontSize: 27, marginTop: 6, lineHeight: 1.25, display: "flex", alignItems: "baseline", ...appear(t, T[5] + 1.2, undefined, 10) }}>
            <Dot color={K.card} />
            <span>
              card cores drive the host's <b>Intel DSA</b> directly
            </span>
          </div>
          <div style={{ fontSize: 27, marginTop: 10, lineHeight: 1.25, display: "flex", alignItems: "baseline", ...appear(t, T[5] + 6.0, undefined, 10) }}>
            <Dot color={K.card} />
            <span>
              <b>minimal message layer</b> + real request–response workload
            </span>
          </div>
        </div>
      </MapCard>

      <Stage>
        {/* card 1: the core's `next` stays behind the in-flight landing band */}
        <g transform={`translate(0 ${dy[0]})`} opacity={ck[0]}>
          <RingStrip x={RING.x} y={RING.y} n={RING.n} cw={RING.cw} ch={RING.ch} gap={RING.gap} flicker={rt}
            state={(i) => (ringK >= (i + 1) / RING.n ? RING_STATE[i] : "empty")}
            nextAt={nextK > 0 ? 3 + 0.35 * Math.sin(rt * 1.4) : undefined} band={{ from: 7, to: 11, k: bandK }} />
          <text x={bandMid} y={RING.y - 44} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.inflight} opacity={bandK}>
            landing band
          </text>
        </g>

        {/* card 2: host and card both wait; the queue travels the loop (forward or back) */}
        <g transform={`translate(0 ${dy[1]})`} opacity={prog(t, T[1] + 1.0, 0.6)} fontFamily={FONT}>
          {[LOOP.hx, LOOP.cx].map((x, i) => (
            <g key={x}>
              <rect x={x} y={LOOP.y0} width={LOOP.bw} height={LOOP.y1 - LOOP.y0} rx={10} fill={C.panel} stroke={i ? K.card : K.host} strokeWidth={2.5} />
              <text x={x + LOOP.bw / 2} y={(LOOP.y0 + LOOP.y1) / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={24}
                fontWeight={700} fill={C.text}>
                {i ? "card" : "host"}
              </text>
              <g opacity={0.55 + 0.45 * Math.sin(rt * 3 + i * 1.7)} transform={`translate(${x + LOOP.bw / 2} ${LOOP.y0 - 22})`}>
                <path d="M-9 -12 H9 L0 0 L9 12 H-9 L0 0 Z" fill="none" stroke={K.wait} strokeWidth={3} strokeLinejoin="round" />
                <path d="M-4 9 H4 L0 4 Z" fill={K.wait} />
              </g>
            </g>
          ))}
          <Arrow x1={LOOP.hx + LOOP.bw} y1={LOOP.fy} x2={LOOP.cx - 2} y2={LOOP.fy} color={K.fwd} w={4} head={12} opacity={0.6} />
          <Arrow x1={LOOP.cx} y1={LOOP.ry} x2={LOOP.hx + LOOP.bw + 2} y2={LOOP.ry} color={K.ret} w={4} head={12} opacity={0.6} />
          {Array.from({ length: 6 }, (_, j) => {
            const L = 2 * (LOOP.cx - LOOP.hx - LOOP.bw) + 2 * (LOOP.ry - LOOP.fy);
            const k = (((rt * 110 - j * 15) / L) % 1 + 1) % 1;
            const [x, y] = along(LOOP_PATH, k);
            return <rect key={j} x={x - 6} y={y - 6} width={12} height={12} rx={3} fill={y < (LOOP.fy + LOOP.ry) / 2 ? K.fwd : K.ret} />;
          })}
        </g>

        {/* card 3: no loop, both directions stream independently */}
        <g transform={`translate(0 ${dy[2]})`} opacity={prog(t, T[2] + 1.0, 0.6)} fontFamily={FONT}>
          {[OPEN.hx, OPEN.cx].map((x, i) => (
            <g key={x}>
              <rect x={x} y={LOOP.y0} width={LOOP.bw} height={LOOP.y1 - LOOP.y0} rx={10} fill={C.panel} stroke={i ? K.card : K.host} strokeWidth={2.5} />
              <text x={x + LOOP.bw / 2} y={(LOOP.y0 + LOOP.y1) / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={24}
                fontWeight={700} fill={C.text}>
                {i ? "card" : "host"}
              </text>
            </g>
          ))}
          <Arrow x1={OPEN.hx + LOOP.bw} y1={LOOP.fy} x2={OPEN.cx - 2} y2={LOOP.fy} color={K.fwd} w={4} head={12} opacity={0.5} />
          <Arrow x1={OPEN.cx} y1={LOOP.ry} x2={OPEN.hx + LOOP.bw + 2} y2={LOOP.ry} color={K.ret} w={4} head={12} opacity={0.5} />
          <PacketStream pts={[[OPEN.hx + LOOP.bw + 6, LOOP.fy], [OPEN.cx - 14, LOOP.fy]]} travel={rt * 250} gap={30} size={12} color={K.fwd} />
          <PacketStream pts={[[OPEN.cx - 6, LOOP.ry], [OPEN.hx + LOOP.bw + 14, LOOP.ry]]} travel={rt * 225 + 11} gap={33} size={12} color={K.ret} />
        </g>

        {/* map arrows: close the loop → remove it; suspects ruled out; open items */}
        <Arrow x1={COL[0] + CW + 6} y1={TOP.y + TOP.h / 2} x2={COL[1] - 6} y2={TOP.y + TOP.h / 2} color={C.muted} w={5} head={16} k={a1} />
        <Arrow x1={COL[1] + CW + 6} y1={TOP.y + TOP.h / 2} x2={COL[2] - 6} y2={TOP.y + TOP.h / 2} color={C.muted} w={5} head={16} k={a2} />
        <g opacity={a3}>
          <Arrow x1={590} y1={BOT.y - 6} x2={704} y2={TOP.y + TOP.h + 6} color={C.pink} w={4} head={14} dash="9 7" k={a3} />
          <g transform={`translate(647 ${(BOT.y + TOP.y + TOP.h) / 2})`}>
            <circle r={13} fill={C.bg} stroke={C.pink} strokeWidth={2.5} />
            <path d="M-5 -5 L5 5 M5 -5 L-5 5" stroke={C.pink} strokeWidth={3} strokeLinecap="round" />
          </g>
        </g>
        <Arrow x1={COL[1] + CW / 2} y1={TOP.y + TOP.h + 6} x2={COL[1] + CW / 2} y2={BOT.y - 6} color={C.muted} w={4} head={12} k={a45} />
        <Arrow x1={COL[2] + CW / 2} y1={TOP.y + TOP.h + 6} x2={COL[2] + CW / 2} y2={BOT.y - 6} color={C.muted} w={4} head={12} k={a45} />
      </Stage>

      {/* T5: a final light sweep across the whole map */}
      {sweepK > 0 && sweepK < 1 && (
        <div
          style={{
            position: "absolute",
            left: sweepX - 210,
            top: 124,
            width: 420,
            height: 758,
            background: "linear-gradient(90deg, rgba(255,255,255,0), rgba(255,255,255,0.2), rgba(255,255,255,0))",
            transform: "skewX(-12deg)",
          }}
        />
      )}
    </>
  );
}

export const S10: SceneDef = {
  id: "S10",
  title: "What we learned",
  stage: 5,
  source: "six single-variable experiments · open items",
  dur: DUR,
  cues,
  C: Body,
};
