import React from "react";
import { Cue, FONT, SceneDef, lerp, lin, prog, useT } from "../lib";
import { BigStat, Cell, Flow, HnGeom, HostNic, P, Stage, StepBadge, TAB, Tag, Text, alpha, btm, hnGeom, hnRoutes, rgt, win } from "./kit";
import { PartText, fracAtX, mapPts, tagW } from "./_CcSceneDebate";

// <cues>
const cues: Cue[] = [
  [0.60, 8.60, "Every request a server handles crosses the boundary between the **host CPU** and the **network card**.", "Every request a server handles crosses the boundary between the host C P U and the network card, once on the way in and once on the way out."], // 0
  [8.95, 15.95, "Today that boundary is **PCIe**, and NICs talk across it through descriptor rings that live in host memory.", "Today that boundary is P C I express, and network cards talk across it through descriptor rings that live in host memory."], // 1
  [16.30, 25.26, "To send, the CPU writes a descriptor and rings a **doorbell**. The NIC then **reads** the descriptor, then the payload.", "To send a message, the C P U writes a descriptor and rings a doorbell on the card. The card then reads the descriptor, and then the payload, back across the link."], // 2
  [25.61, 33.95, "Each read is a **full round trip** over the link, so one message pays several crossings before it even leaves.", "Each of those reads is a full round trip over P C I express. So a single message pays for several crossings before it even leaves the machine."], // 3
  [34.30, 42.82, "Receiving mirrors it: the NIC writes the packet, then a **completion**, and the CPU keeps checking for it.", "Receiving works the same way in reverse. The card writes the packet into host memory, then a completion record, and the C P U keeps checking for it."], // 4
  [43.17, 54.71, "A 64-byte round trip through today's NICs takes **2.1–3.8 µs**. For microsecond services, the interface is the bottleneck.", "The result: a sixty-four byte round trip through a modern network card takes two point one to three point eight microseconds. For microsecond-scale services, the interface itself is the bottleneck."], // 5
];
const DUR = 56.11; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

/** b0–b4: the running diagram at s = 1, nudged left so the network arrow fits on the right. */
const DX = -60;
const G1 = hnGeom(1, DX, 0);
const R1 = hnRoutes(G1);
type Route = keyof typeof R1;
const GAP_X = G1.at(960, 0)[0]; // centre of the link gap

// network attachment, descriptor ring and badge rows (default-layout coordinates)
const NET_Y = 348;
const NET_X0 = 1770; // NIC box right edge
const NET_X1 = 1890;
const NIC_OUT = 1745; // a finished message waits here (between NIC core and box edge) before it leaves
const RING = { x: 596, y: 675, ro: 31, ri: 17, n: 8 };
const BADGE_UP = 456; // host → NIC crossings: badge row above the lanes
const BADGE_DN = 544; // NIC → host crossings: badge row below the lanes

/** b0: a request comes in from the network, crosses to the host CPU, and the reply crosses back out. */
const LOOP_IN = mapPts(G1, [[NET_X1, NET_Y], [1720, NET_Y]]);
const LOOP_REQ = mapPts(G1, [[1500, 366], [1262, 366], [1262, 514], [665, 514], [665, 366], [420, 366]]);
const LOOP_REP = R1.doorbell;
const LOOP_OUT = mapPts(G1, [[1720, NET_Y], [NET_X1, NET_Y]]);
const LOOP = [
  { pts: LOOP_IN, a: T[0] + 2.0, d: 0.6 },
  { pts: LOOP_REQ, a: T[0] + 2.8, d: 1.5 },
  { pts: LOOP_REP, a: T[0] + 4.8, d: 1.5 },
  { pts: LOOP_OUT, a: T[0] + 6.5, d: 0.6 },
];
/** when the b0 request is on the link (soft glow on the boundary it crosses) */
const ON_LINK: [number, number][] = [
  [LOOP[1].a + LOOP[1].d * fracAtX(LOOP_REQ, G1.linkX1), LOOP[1].a + LOOP[1].d * fracAtX(LOOP_REQ, G1.linkX0)],
  [LOOP[2].a + LOOP[2].d * fracAtX(LOOP_REP, G1.linkX0), LOOP[2].a + LOOP[2].d * fracAtX(LOOP_REP, G1.linkX1)],
];

/** Send (b2) and receive (b4) steps; `n` = link-crossing number, its badge sits at default-layout x `bx`. */
type Hop = { a: number; d: number; route: Route; color: string; w: number; h: number; n?: number; bx?: number; up?: boolean };
const HOPS: Hop[] = [
  { a: T[2] + 0.3, d: 1.0, route: "hostToMem", color: P.host, w: 26, h: 18 },
  { a: T[2] + 1.7, d: 1.3, route: "doorbell", color: P.host, w: 26, h: 18, n: 1, bx: 720, up: true },
  { a: T[2] + 3.3, d: 1.1, route: "readReq", color: P.nic, w: 22, h: 14, n: 2, bx: 800, up: false },
  { a: T[2] + 4.5, d: 1.1, route: "readCpl", color: P.host, w: 26, h: 18, n: 3, bx: 800, up: true },
  { a: T[2] + 5.9, d: 1.1, route: "readReq", color: P.nic, w: 22, h: 14, n: 4, bx: 1050, up: false },
  { a: T[2] + 7.1, d: 1.1, route: "readCpl", color: P.host, w: 36, h: 22, n: 5, bx: 1050, up: true },
  { a: T[4] + 1.5, d: 1.3, route: "nicToHostMem", color: P.nic, w: 36, h: 22, n: 6, bx: 1120, up: false },
  { a: T[4] + 3.3, d: 1.2, route: "nicToHostMem", color: P.nic, w: 22, h: 14, n: 7, bx: 1190, up: false },
];
/** the moment each hop is counted: its cell passes the badge on the link (local steps: when they land) */
const POP = HOPS.map((h) => h.a + h.d * (h.bx === undefined ? 1 : fracAtX(R1[h.route], G1.at(h.bx, 0)[0])));
const LEAVE = T[3] + 6.3; // the send-side message finally leaves for the network
const PKT_IN = T[4] + 0.7; // a packet arrives from the network
const STEPS: [number, string][] = [
  [T[2] + 0.3, "CPU writes a descriptor into its memory"],
  [POP[1] - 0.25, "CPU rings the NIC's doorbell"],
  [POP[2] - 0.25, "NIC reads the descriptor"],
  [POP[4] - 0.25, "NIC reads the payload"],
  [T[3] + 0.4, "each read is a full round trip"],
  [LEAVE - 0.1, "only now does it leave"],
  [T[4] + 0.4, "a packet arrives"],
  [POP[6] - 0.25, "NIC writes the packet"],
  [POP[7] - 0.25, "NIC writes a completion"],
  [T[4] + 5.6, "CPU keeps checking its memory"],
];

/** Descriptor ring (SVG) inside host memory: slots appear one by one from t0; outlines in the NIC's colour (its queue), fills = who wrote. */
function DescRing({ g, t, t0, fills }: { g: HnGeom; t: number; t0: number; fills: { i: number; color: string; k: number }[] }) {
  const [x, y] = g.at(RING.x, RING.y);
  const ro = RING.ro * g.s;
  const ri = RING.ri * g.s;
  const step = (Math.PI * 2) / RING.n;
  const pt = (r: number, a: number) => `${x + r * Math.cos(a)} ${y + r * Math.sin(a)}`;
  return (
    <g>
      {Array.from({ length: RING.n }, (_, i) => {
        const op = prog(t, t0 + i * 0.12, 0.3);
        if (op <= 0) return null;
        const a0 = -Math.PI / 2 + i * step + 0.07;
        const a1 = -Math.PI / 2 + (i + 1) * step - 0.07;
        const d = `M${pt(ro, a0)} A${ro} ${ro} 0 0 1 ${pt(ro, a1)} L${pt(ri, a1)} A${ri} ${ri} 0 0 0 ${pt(ri, a0)} Z`;
        const f = fills.find((q) => q.i === i);
        return (
          <g key={i} opacity={op}>
            <path d={d} fill={alpha(P.nic, 0.08)} stroke={alpha(P.nic, 0.75)} strokeWidth={1.4} />
            {f && f.k > 0 && <path d={d} fill={f.color} opacity={0.9 * f.k} />}
          </g>
        );
      })}
    </g>
  );
}

function Body() {
  const t = useT();
  // ---------------------------------------------------------------- the diagram (shrinks under the punchline in b5)
  const sk = prog(t, T[5] + 0.2, 1.3);
  const g = sk > 0 ? hnGeom(lerp(1, 0.7, sk), lerp(DX, 0, sk), lerp(0, 150, sk)) : G1;
  const R = sk > 0 ? hnRoutes(g) : R1;
  const diagIn = prog(t, T[0] + 0.1, 0.8);
  const diagOp = diagIn * lerp(1, 0.5, sk);
  const labelsOut = 1 - prog(t, T[5], 0.5); // HTML labels tied to the s = 1 layout

  // b0: network arrow + one request crossing in and out; b1: PCIe, then host memory with the NIC's ring
  const netK = prog(t, T[0] + 1.2, 0.8);
  const [nx0, ny] = g.at(NET_X0, NET_Y);
  const [nx1] = g.at(NET_X1, NET_Y);
  const nm = (nx0 + nx1) / 2;
  const loopFade = 1 - prog(t, T[1] - 0.5, 0.6);
  const crossGlow = Math.max(...ON_LINK.map(([a, b]) => win(t, a - 0.1, b + 0.1, 0.25)));
  const linkGlow = Math.max(0.8 * crossGlow, win(t, T[1] + 0.2, T[1] + 2.4, 0.4));
  const memHl = prog(t, T[1] + 2.0, 0.8) * (1 - 0.4 * prog(t, T[2], 0.6));
  const dimK = 0.6 * prog(t, T[1] + 2.0, 0.8);
  const relabel = prog(t, T[1] + 2.2, 0.7);

  // b2–b4: crossings
  const fl = (i: number) => lin(t, HOPS[i].a, HOPS[i].d);
  const sendDim = 1 - 0.65 * prog(t, T[4] + 0.1, 0.6) * (1 - prog(t, T[5] + 0.2, 0.8));
  const rtGlow = prog(t, T[3] + 0.3, 0.6) * (1 - 0.6 * prog(t, T[4] + 0.1, 0.6));
  const pollK = t >= T[4] + 0.6 ? (0.35 + 0.65 * Math.abs(Math.sin((t - T[4] - 0.6) * 3.0))) * prog(t, T[4] + 0.6, 0.4) * (1 - prog(t, T[5], 0.4)) : 0;
  let count = 0;
  HOPS.forEach((h, i) => {
    if (h.n !== undefined && t >= POP[i]) count = h.n;
  });
  const lastPop = count > 0 ? POP[HOPS.findIndex((h) => h.n === count)] : 0;
  const bump = 1 + 0.22 * (1 - prog(t, lastPop, 0.35));
  const ctrOp = prog(t, POP[1] - 0.1, 0.4) * labelsOut;
  let si = -1;
  STEPS.forEach(([a], i) => {
    if (t >= a) si = i;
  });
  const stepOp = si < 0 ? 0 : prog(t, STEPS[si][0], 0.35) * (si + 1 < STEPS.length ? 1 - prog(t, STEPS[si + 1][0] - 0.3, 0.3) : 1) * labelsOut;
  const waitK = t >= HOPS[5].a + HOPS[5].d && t < LEAVE ? 0.6 + 0.4 * Math.abs(Math.sin((t - T[3]) * 2.4)) : 0;

  // b5: the punchline
  const stat = prog(t, T[5] + 1.0, 0.8);

  return (
    <>
      <Stage>
        <g opacity={diagOp}>
          <HostNic
            g={g}
            hl={{ hostMem: memHl }}
            dim={{ hostCache: dimK, nicCache: dimK, nicMem: dimK }}
            labels={{ hostMem: "", nicCache: "NIC cache" }}
            linkLabel="PCIe"
            hostTitle="HOST · CPU"
            nicTitle="NIC · network card"
          />
          {/* the boundary every request crosses */}
          {linkGlow > 0 && (
            <rect x={g.linkX0} y={g.mid - 34 * g.s} width={g.linkX1 - g.linkX0} height={68 * g.s} rx={12 * g.s} fill={alpha(P.sub, 0.1 * linkGlow)} stroke={alpha(P.sub, 0.35 * linkGlow)} strokeWidth={1.2} />
          )}
          <Flow pts={[[nm, ny], [nx1, ny]]} k={netK} color={P.sub} w={2} head={10} />
          <Flow pts={[[nm, ny], [nx0 + 2, ny]]} k={netK} color={P.sub} w={2} head={10} />
          {/* host memory: relabelled, with the NIC's descriptor ring */}
          <PartText r={g.hostMem} s={g.s} opacity={1 - relabel}>
            Memory (DRAM)
          </PartText>
          <text x={g.at(222, 0)[0]} y={g.at(0, 675)[1] + 1} dominantBaseline="central" fontSize={Math.max(10, 24 * g.s)} fontWeight={memHl > 0.5 ? 600 : 400} fill={P.ink} opacity={relabel} fontFamily={FONT}>
            Memory: rings + buffers
          </text>
          <DescRing
            g={g}
            t={t}
            t0={T[1] + 2.6}
            fills={[
              { i: 0, color: P.host, k: prog(t, POP[0], 0.3) },
              { i: 1, color: P.nic, k: prog(t, POP[7], 0.3) },
            ]}
          />
          {/* b0 trails */}
          <Flow pts={LOOP_REQ} k={lin(t, LOOP[1].a, LOOP[1].d)} color={alpha(P.sub, 0.5)} w={2} head={10} opacity={loopFade} />
          <Flow pts={LOOP_REP} k={lin(t, LOOP[2].a, LOOP[2].d)} color={alpha(P.sub, 0.5)} w={2} head={10} opacity={loopFade} />
          {/* send path (b2), round trips (b3) */}
          <g opacity={sendDim}>
            <Flow pts={R.hostToMem} k={fl(0)} color={alpha(P.host, 0.6)} w={2.5} head={11} />
            <Flow pts={R.doorbell} k={fl(1)} color={alpha(P.host, 0.7)} w={2.5} head={12} />
            <Flow pts={R.readReq} k={fl(2)} color={alpha(P.nic, 0.6)} w={2.5} head={12} />
            <Flow pts={R.readCpl} k={fl(3)} color={alpha(P.host, 0.6)} w={2.5} head={12} />
            <Flow pts={R.readReq} k={rtGlow > 0 ? 1 : 0} color={P.bad} w={3} head={12} glow opacity={rtGlow} />
            <Flow pts={R.readCpl} k={rtGlow > 0 ? 1 : 0} color={P.bad} w={3} head={12} glow opacity={rtGlow} />
          </g>
          {/* receive path (b4) and the CPU's polling */}
          <Flow pts={R.nicToHostMem} k={fl(6)} color={alpha(P.nic, 0.75)} w={2.5} head={12} />
          <Flow pts={R.hostToMem} k={pollK > 0 ? 1 : 0} color={P.host} w={3} head={11} opacity={pollK} />
          {/* moving cells (b0–b4 only, on the s = 1 layout) */}
          {LOOP.map((s, i) => (
            <Cell key={`l${i}`} pts={s.pts} k={(t - s.a) / s.d} color={P.sub} w={28} h={18} />
          ))}
          {HOPS.map((h, i) => (
            <Cell key={`h${i}`} pts={R1[h.route]} k={(t - h.a) / h.d} color={h.color} w={h.w} h={h.h} />
          ))}
          {waitK > 0 && <Cell pts={mapPts(G1, [[NIC_OUT, NET_Y], [NIC_OUT + 0.01, NET_Y]])} k={0} color={P.host} w={34} h={22} opacity={waitK} />}
          <Cell pts={mapPts(G1, [[NIC_OUT, NET_Y], [NET_X1, NET_Y]])} k={(t - LEAVE) / 0.7} color={P.host} w={34} h={22} />
          <Cell pts={LOOP_IN} k={(t - PKT_IN) / 0.6} color={P.nic} w={34} h={22} />
          {/* crossing badges */}
          {HOPS.map((h, i) => {
            if (h.n === undefined || h.bx === undefined) return null;
            const k = prog(t, POP[i], 0.35);
            if (k <= 0) return null;
            const [bx, by] = g.at(h.bx, h.up ? BADGE_UP : BADGE_DN);
            const p0 = T[5] + 2.4 + (h.n - 1) * 0.32;
            const pulse = win(t, p0, p0 + 0.45, 0.18);
            return (
              <g key={`b${h.n}`} opacity={h.n <= 5 ? sendDim : 1}>
                {pulse > 0 && <circle cx={bx} cy={by} r={28} fill={alpha(P.bad, 0.4 * pulse)} filter="url(#ccSoft)" />}
                <StepBadge x={bx} y={by} n={h.n} k={k} />
              </g>
            );
          })}
        </g>
      </Stage>

      {/* ---- diagram labels */}
      <Tag x={rgt(g.nic) - tagW("SCHEMATIC")} y={g.nic.y - 44} opacity={diagIn * prog(t, T[0] + 0.8, 0.6)}>
        SCHEMATIC
      </Tag>
      <Text x={nm - 70} y={ny - 40} w={140} align="center" size={20} color={P.sub} opacity={prog(t, T[0] + 1.6, 0.6) * labelsOut}>
        network
      </Text>
      <Text x={G1.host.x} y={btm(G1.host) + 16} w={G1.host.w} align="center" size={22} color={P.sub} opacity={win(t, T[1] + 3.0, T[2] + 0.2, 0.6)}>
        the NIC's queues live in <span style={{ color: P.host, fontWeight: 600 }}>host memory</span>
      </Text>
      {[800, 1050].map((x) => (
        <Text key={x} x={G1.at(x, 0)[0] - 60} y={566} w={120} align="center" size={20} color={P.bad} opacity={win(t, T[3] + 0.8, T[4] + 0.1, 0.5)}>
          round trip
        </Text>
      ))}
      <Text x={nm - 70} y={ny + 16} w={140} align="center" size={20} color={P.bad} opacity={win(t, T[3] + 1.2, LEAVE - 0.2, 0.5)}>
        not sent yet
      </Text>
      <Text x={G1.at(188, 0)[0]} y={402} size={20} color={P.host} opacity={Math.min(1, pollK * 2)}>
        polls
      </Text>
      {/* crossing counter + current step */}
      <Text x={GAP_X - 260} y={236} w={520} align="center" size={17} color={P.sub} opacity={ctrOp} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        LINK CROSSINGS
      </Text>
      <Text x={GAP_X - 260} y={262} w={520} align="center" size={72} color={P.bad} weight={800} opacity={ctrOp} style={{ ...TAB, lineHeight: 1, transform: `scale(${bump})` }}>
        {count}
      </Text>
      {si >= 0 && (
        <Text x={GAP_X - 260} y={348} w={520} align="center" size={22} color={P.ink} opacity={stepOp}>
          {STEPS[si][1]}
        </Text>
      )}

      {/* ---- b5: the punchline */}
      <BigStat
        x={960}
        y={196}
        w={1000}
        align="center"
        value="2.1–3.8"
        unit="µs"
        size={96}
        color={P.bad}
        label="64-byte round trip through today's NICs"
        note="CX6 and E810 · from CC-NIC, ASPLOS '24"
        opacity={stat}
        dy={(1 - stat) * 16}
      />
    </>
  );
}

export const C02: SceneDef = {
  id: "C02",
  title: "The interface tax",
  stage: 0,
  source: "Latency numbers: CC-NIC (ASPLOS '24) \u00a75.3, CX6 and E810 NICs",
  dur: DUR,
  cues,
  C: Body,
};
