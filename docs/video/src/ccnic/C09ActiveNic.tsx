import React from "react";
import { Cue, FONT, Pt, SceneDef, clamp01, lerp, polyLen, prog, useT } from "../lib";
import { BigStat, Cell, Flow, HostNic, P, Rect, Stage, TAB, Tag, Text, alpha, btm, cx, cy, hnGeom, rgt, win } from "./kit";
import { Live, PartFill, PartGlow, Token, ends, stream } from "./_CcSceneProof";

// <cues>
const cues: Cue[] = [
  [0.60, 8.24, "Push has a bonus: the **NIC is the writer**, so it decides where data lands, even during a receive burst.", "Push has a bonus. Because the card is the one writing, it decides where data lands, and that matters most during a receive burst."], // 0
  [8.59, 18.51, "A passive NIC pours the burst into a big host ring. At 128 MiB, it overflows the host cache and spills to DRAM.", "A passive card pours the whole burst into a big ring in host memory. At one hundred twenty-eight megabytes, that overflows the host cache, and the data spills to D RAM."], // 1
  [18.86, 28.30, "An **active NIC** keeps the backlog in its own memory and refills a small **256 KiB** host ring as credits return.", "An active card instead keeps the backlog in its own memory, and refills a small, two hundred fifty-six kilobyte host ring as the host returns credits."], // 2
  [28.65, 38.67, "Same 128 MiB burst: host cache misses **91.4% → 0%**, host DRAM traffic **−98.9%**.", "With the same one hundred twenty-eight megabyte burst, the host's cache misses fall from ninety-one percent to zero, and host D RAM traffic falls by ninety-eight point nine percent."], // 3
  [39.02, 49.72, "Bursts up to 32 MiB fit in the NIC's **48 MiB cache** and touch no DRAM at all; larger ones go to the **NIC's own DRAM**.", "Bursts up to thirty-two megabytes fit entirely in the card's forty-eight megabyte cache, and touch no D RAM at all. Larger bursts land in the card's own D RAM, not the host's."], // 4
  [50.07, 55.87, "With no burst at all, the extra bookkeeping costs just **6 ns** per round trip.", "And when there is no burst, the extra credit bookkeeping costs only six nanoseconds per round trip."], // 5
];
const DUR = 57.27; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const G = hnGeom(0.86, -74, -60);
const S = G.s;
const HC = G.hostCache;
const HM = G.hostMem;
const NC = G.nicCache;
const NM = G.nicMem;
/** centre of the diagram (host left edge .. NIC right edge) */
const DC = (G.host.x + rgt(G.nic)) / 2;
/** the receive burst enters at the NIC's right edge, low in the NIC cache (clear of its label) */
const NET_Y = btm(NC) - 22;
const RISER = G.at(1255, 0)[0];
/** active NIC: a small host ring at the right end of the host cache (clear of the centred "Cache (LLC)" label) */
const RING: Rect = { x: rgt(HC) - 108, y: cy(HC) - 26, w: 96, h: 52 };
/** passive NIC: one big ring spanning the host cache and the host DRAM */
const RING_BIG: Rect = { x: HC.x - 14, y: HC.y - 14, w: HC.w + 28, h: btm(HM) - HC.y + 28 };
const SLOT_W = (RING.w - 12 - 3 * 5) / 4;
const R_PORT: Pt[] = [[1850, NET_Y], [rgt(G.nic) + 3, NET_Y]];
const R_IN: Pt[] = [[1850, NET_Y], [NC.x + NC.w * 0.6, NET_Y]];
const R_THRU: Pt[] = [[1850, NET_Y], [RISER, NET_Y], [RISER, G.laneDn], [rgt(HC) - 22, G.laneDn]];
const R_CHOICE: Pt[] = [[NC.x + 28, G.laneDn], [rgt(HC) - 16, G.laneDn]];
const R_PUSH: Pt[] = [[NC.x + 28, G.laneDn], [rgt(RING) - 4, G.laneDn]];
const R_CREDIT: Pt[] = [[rgt(RING) - 4, G.laneUp], [NC.x + 28, G.laneUp]];
const R_SPILL: Pt[] = [[cx(HC), btm(HC) + 4], [cx(HC), HM.y - 4]];
/** NIC cache → NIC memory overflow, in the left quarter (clear of the b4 "32 MiB" tick label) */
const R_DOWN: Pt[] = [[NC.x + NC.w * 0.25, btm(NC) + 4], [NC.x + NC.w * 0.25, NM.y - 4]];
const D_IN = polyLen(R_IN) / 470;
const D_THRU = polyLen(R_THRU) / 640;
const D_PUSH = polyLen(R_PUSH) / 560;
const PUSH_PER = 0.6;
const CALM_PER = 1.9;
/** fill edge of a 32 MiB burst in the 48 MiB NIC cache */
const TICK_X = NC.x + 6 * S + (NC.w - 12 * S) * (32 / 48);

/** Ring slots (0..1 each): every pushed cell lights the next slot; the host consumes it `hold` s after arrival. */
function fillSlots(out: number[], t: number, a: number, b: number, per: number, hold: number) {
  const n = Math.floor((t - a - D_PUSH) / per);
  const nMax = Math.floor((b - a) / per);
  for (let i = Math.max(0, n - 4); i <= Math.min(n, nMax); i++) {
    const d = t - (a + i * per + D_PUSH);
    if (d < 0) continue;
    const f = d < 0.12 ? d / 0.12 : d < hold ? 1 : Math.max(0, 1 - (d - hold) / 0.35);
    out[i % 4] = Math.max(out[i % 4], f);
  }
}

function Body() {
  const t = useT();
  const diag = prog(t, T[0] + 0.1, 0.8);
  const passiveOut = 1 - prog(t, T[2] + 0.2, 0.8);
  const backlogOut = 1 - prog(t, T[4] + 0.2, 0.7);
  const sizesOut = 1 - prog(t, T[5] + 0.2, 0.8);

  // b1 passive NIC: a big host ring fills the host cache, overflows, spills into host DRAM
  const hostFill = prog(t, T[1] + 2.3, 3.4);
  const overflow = prog(t, T[1] + 5.6, 0.5) * passiveOut;
  const dramFill = prog(t, T[1] + 6.1, 3.4);
  // b2–b3 active NIC: the backlog piles up in the NIC cache, then in NIC memory
  const nicFill = prog(t, T[2] + 1.4, 3.2);
  const nicMemFill = 0.76 * prog(t, T[2] + 4.8, 4.5);
  // b4: a 32 MiB burst fits the 48 MiB NIC cache; a larger one continues into NIC memory
  const sizeCache = (32 / 48) * prog(t, T[4] + 1.9, 2.4) + (16 / 48) * prog(t, T[4] + 6.8, 1.0);
  const sizeMem = 0.8 * prog(t, T[4] + 7.8, 3.0);

  // host ring outline: the big passive ring shrinks to the small 256 KiB ring
  const ringOn = prog(t, T[1] + 2.2, 0.8);
  const shrink = prog(t, T[2] + 2.8, 1.3);
  const ring: Rect = {
    x: lerp(RING_BIG.x, RING.x, shrink),
    y: lerp(RING_BIG.y, RING.y, shrink),
    w: lerp(RING_BIG.w, RING.w, shrink),
    h: lerp(RING_BIG.h, RING.h, shrink),
  };

  // credit-paced pushes into the small ring (b2..b4), then calm single round trips with no burst (b5)
  const pushA = T[2] + 4.0;
  const calmA = T[5] + 1.0;
  const slots = [0, 0, 0, 0];
  fillSlots(slots, t, pushA, T[5] - 0.3, PUSH_PER, 0.9);
  fillSlots(slots, t, calmA, 1e9, CALM_PER, 1.1);

  // diagram highlights: b1 alarms coral, b3 host results teal (own overlays: hlColor would recolour the parts at rest)
  const hostDim = 0.5 * win(t, T[4] + 0.4, T[5], 0.7);
  const noDram = 0.6 * win(t, T[4] + 4.2, T[4] + 6.8, 0.5);
  const nicHl = Math.max(
    prog(t, T[0] + 2.3, 0.6) * (1 - prog(t, T[1], 0.6)),
    0.55 * win(t, T[2] + 1.4, T[4] + 0.2, 0.8),
    0.7 * win(t, T[4] + 1.9, T[5] + 0.2, 0.6),
  );
  const glows: [Rect, number, string][] = [
    [HC, overflow, P.bad],
    [HM, 0.8 * prog(t, T[1] + 6.1, 0.6) * passiveOut, P.bad],
    [HC, 0.8 * win(t, T[3] + 3.4, T[4] + 0.2, 0.6), P.push],
    [HM, 0.7 * win(t, T[3] + 6.0, T[4] + 0.2, 0.6), P.push],
  ];
  const nicMemHl = Math.max(0.5 * win(t, T[2] + 4.8, T[4] + 0.2, 0.8), 0.6 * win(t, T[4] + 7.8, T[5] + 0.2, 0.6));
  const consume = t > pushA + D_PUSH ? 0.3 + 0.2 * Math.sin((t - pushA) * 5.2) : 0;
  const labelA = Math.min(1, 1 - prog(t, T[4] + 0.3, 0.4) + prog(t, T[5] + 0.9, 0.5));
  const labelB = prog(t, T[4] + 0.8, 0.5) * (1 - prog(t, T[5] + 0.3, 0.5));
  const choices = 1 - prog(t, T[1] + 0.1, 0.5);

  // b3 results, b5 closing stat
  const res = win(t, T[3] + 0.5, T[4], 0.6);
  const zero = prog(t, T[3] + 3.3, 0.6);
  const dram = prog(t, T[3] + 5.9, 0.6);
  const calm = prog(t, T[5] + 1.4, 0.7);

  const cells = (list: Live[], pts: Pt[], color: string, key: string, op = 1, w = 26, h = 17) =>
    list.map(({ i, k }) => <Cell key={key + i} pts={pts} k={k} color={color} w={w} h={h} opacity={op * ends(k, 0.05, 0.08)} />);
  const tokens = (list: Live[], key: string) =>
    list.map(({ i, k }) => <Token key={key + i} pts={R_CREDIT} k={k} r={6.5} opacity={ends(k, 0.05, 0.08)} />);

  return (
    <>
      <Stage>
        <g opacity={diag}>
          {/* fills sit under the parts so the part labels stay readable */}
          <PartFill r={HC} k={hostFill} color={P.nic} s={S} opacity={passiveOut} />
          <PartFill r={HM} k={dramFill} color={P.bad} a={0.2} hatch s={S} opacity={passiveOut} />
          <PartFill r={NC} k={nicFill} color={P.nic} s={S} opacity={backlogOut} />
          <PartFill r={NM} k={nicMemFill} color={P.nic} s={S} opacity={backlogOut} />
          <PartFill r={NC} k={sizeCache} color={P.nic} s={S} opacity={sizesOut} />
          <PartFill r={NM} k={sizeMem} color={P.nic} s={S} opacity={sizesOut} />
          {glows.map(([r, k, c], i) => (
            <PartGlow key={i} r={r} k={k} color={c} s={S} layer="under" />
          ))}
          <HostNic
            g={G}
            hl={{ nicCache: nicHl, nicMem: nicMemHl, hostCore: consume * (1 - hostDim), link: 0.35 * prog(t, pushA, 0.8) }}
            dim={{ hostCore: hostDim, hostCache: hostDim, hostMem: Math.max(hostDim, noDram), nicMem: noDram }}
            labels={{ nicCache: "" }}
          />
          {glows.map(([r, k, c], i) => (
            <PartGlow key={i} r={r} k={k} color={c} s={S} layer="over" />
          ))}
          {/* NIC cache caption (own copy, so b4 can name the 48 MiB capacity, left of the 32 MiB fill edge) */}
          <g fontFamily={FONT} fontSize={24 * S} fill={P.ink} fontWeight={nicHl > 0.5 ? 600 : 400}>
            <text x={cx(NC)} y={cy(NC) + 1} textAnchor="middle" dominantBaseline="central" opacity={labelA}>
              NIC cache (L3)
            </text>
            <text x={NC.x + 22} y={cy(NC) + 1} dominantBaseline="central" opacity={labelB}>
              NIC cache · <tspan fill={P.nic} fontWeight={700}>48 MiB</tspan>
            </text>
          </g>
          {/* host ring: big passive ring (cache + DRAM) → small active ring inside the cache */}
          <g opacity={ringOn * (1 - 0.75 * hostDim)}>
            {/* the big outline contracts toward the small ring and is gone before it reaches the centred cache label */}
            <rect x={ring.x} y={ring.y} width={ring.w} height={ring.h} rx={lerp(22, 10, shrink) * S} fill="none" stroke={alpha(P.ink, 0.55)} strokeWidth={2} strokeDasharray="9 7" opacity={clamp01(1 - 2 * shrink)} />
            <rect x={RING.x} y={RING.y} width={RING.w} height={RING.h} rx={10 * S} fill={alpha(P.push, 0.07)} stroke={P.push} strokeWidth={2} opacity={clamp01((shrink - 0.45) / 0.55)} />
            {[0, 1, 2, 3].map((j) => (
              <rect
                key={j}
                x={RING.x + 6 + j * (SLOT_W + 5)}
                y={RING.y + 6}
                width={SLOT_W}
                height={RING.h - 12}
                rx={4}
                fill={alpha(P.push, 0.1 + 0.8 * slots[j])}
                stroke={alpha(P.push, 0.45)}
                strokeWidth={1.2}
                opacity={clamp01((shrink - 0.8) / 0.2)}
              />
            ))}
          </g>
          {/* network port: the receive burst arrives at the NIC's right edge */}
          <Flow pts={R_PORT} k={prog(t, T[0] + 1.0, 0.7)} color={alpha(P.nic, 0.55)} w={2.5} head={11} opacity={1 - 0.6 * prog(t, T[5] + 0.3, 0.8)} />
          {/* b0: the writer decides where data lands */}
          <Flow pts={R_CHOICE} k={prog(t, T[0] + 4.4, 0.9)} color={alpha(P.nic, 0.75)} w={2.2} head={11} dash="8 8" opacity={choices} />
          <Flow pts={R_DOWN} k={prog(t, T[0] + 5.1, 0.5)} color={alpha(P.nic, 0.75)} w={2.2} head={10} dash="5 5" opacity={choices} />
          {/* b1: the passive NIC pours straight through into the host ring; the overflow spills into DRAM */}
          <Flow pts={R_THRU} k={prog(t, T[1] + 0.2, 1.0)} color={alpha(P.nic, 0.4)} w={2.2} head={0} opacity={passiveOut} />
          <Flow pts={R_SPILL} k={prog(t, T[1] + 5.8, 0.4)} color={P.bad} w={2.5} head={10} opacity={passiveOut} />
          {/* b2 / b4: the NIC overflows into its own memory */}
          <Flow pts={R_DOWN} k={prog(t, T[2] + 4.6, 0.4)} color={P.nic} w={2.5} head={10} opacity={backlogOut} />
          <Flow pts={R_DOWN} k={prog(t, T[4] + 7.6, 0.4)} color={P.nic} w={2.5} head={10} opacity={sizesOut} />
          {/* b4: how far a 32 MiB burst reaches in the 48 MiB cache */}
          <g opacity={win(t, T[4] + 3.0, T[5] + 0.2, 0.5)} fontFamily={FONT}>
            <line x1={TICK_X} y1={NC.y + 5} x2={TICK_X} y2={btm(NC) + 6} stroke={P.nic} strokeWidth={2.5} strokeLinecap="round" />
            <text x={TICK_X} y={btm(NC) + 30} textAnchor="middle" fontSize={21} fontWeight={700} fill={P.nic} style={TAB}>
              32 MiB
            </text>
          </g>
          {cells(stream(t, T[0] + 1.6, T[1] - 0.4, 0.17, D_IN), R_IN, P.nic, "a")}
          {cells(stream(t, T[1] + 0.3, T[2] - 0.6, 0.15, D_THRU), R_THRU, P.nic, "b")}
          {cells(stream(t, T[2] + 0.5, T[4] - 0.5, 0.19, D_IN), R_IN, P.nic, "c")}
          {cells(stream(t, T[4] + 1.0, T[4] + 3.4, 0.15, D_IN), R_IN, P.nic, "d")}
          {cells(stream(t, T[4] + 5.8, T[5] - 0.8, 0.15, D_IN), R_IN, P.nic, "e")}
          {cells(stream(t, T[1] + 5.9, T[2] - 0.2, 0.24, 0.5), R_SPILL, P.bad, "s", passiveOut, 18, 12)}
          {cells(stream(t, T[2] + 4.7, T[4] - 0.3, 0.32, 0.5), R_DOWN, P.nic, "m", backlogOut, 18, 12)}
          {cells(stream(t, T[4] + 7.7, T[5] - 0.3, 0.26, 0.5), R_DOWN, P.nic, "n", sizesOut, 18, 12)}
          {cells(stream(t, pushA, T[5] - 0.3, PUSH_PER, D_PUSH), R_PUSH, P.push, "p", 1, 24, 16)}
          {cells(stream(t, calmA, 1e9, CALM_PER, D_PUSH), R_PUSH, P.push, "q", 1, 24, 16)}
          {tokens(stream(t, pushA + 0.3, T[5] - 0.3, PUSH_PER, D_PUSH), "k")}
          {tokens(stream(t, calmA + D_PUSH + 0.25, 1e9, CALM_PER, D_PUSH), "l")}
        </g>
      </Stage>

      {/* roles, then the NIC's mode */}
      <Tag x={cx(G.host) - 50} y={166} color={P.host} opacity={win(t, T[0] + 1.4, T[1], 0.5)}>
        reader
      </Tag>
      <Tag x={cx(G.nic) - 136} y={166} color={P.nic} opacity={win(t, T[0] + 2.4, T[1], 0.5)}>
        the NIC is the writer
      </Tag>
      <Tag x={cx(G.nic) - 78} y={166} color={P.sub} opacity={win(t, T[1] + 0.5, T[2], 0.5)}>
        passive NIC
      </Tag>
      <Tag x={cx(G.nic) - 72} y={166} color={P.push} opacity={prog(t, T[2] + 0.5, 0.5)}>
        active NIC
      </Tag>
      <Tag x={rgt(G.nic) + 26} y={652} opacity={diag}>
        schematic
      </Tag>
      <Text x={rgt(G.nic) + 18} y={NET_Y - 48} size={20} color={P.sub} opacity={diag * prog(t, T[0] + 1.2, 0.6)}>
        from the network
      </Text>

      {/* captions under the host and under the NIC */}
      <Text x={G.host.x} y={708} w={G.host.w} align="center" size={24} opacity={win(t, T[1] + 2.6, T[2] + 0.2, 0.6)}>
        <span style={{ fontWeight: 700, ...TAB }}>128 MiB</span> host ring
        <div style={{ color: P.bad, fontSize: 22, opacity: prog(t, T[1] + 6.4, 0.6) }}>spills into host DRAM</div>
      </Text>
      <Text x={G.host.x} y={708} w={G.host.w} align="center" size={24} opacity={win(t, T[2] + 3.4, T[3], 0.6)}>
        <span style={{ fontWeight: 700, ...TAB }}>256 KiB</span> host ring
        <div style={{ color: P.push, fontSize: 22 }}>stays in the host cache</div>
      </Text>
      <Text x={G.nic.x} y={708} w={G.nic.w} align="center" size={24} color={P.nic} opacity={win(t, T[2] + 1.6, T[3], 0.6)}>
        backlog stays on the NIC
      </Text>
      <Text x={G.nic.x} y={708} w={G.nic.w} align="center" size={24} color={P.push} opacity={win(t, T[4] + 4.4, T[4] + 6.4, 0.5)}>
        cache only · no DRAM at all
      </Text>
      <Text x={G.nic.x} y={708} w={G.nic.w} align="center" size={24} color={P.nic} opacity={win(t, T[4] + 6.9, T[5], 0.5)}>
        larger bursts → the NIC's own DRAM
      </Text>
      <Text x={G.host.x} y={708} w={G.host.w} align="center" size={24} color={P.push} opacity={win(t, T[4] + 8.2, T[5], 0.5)}>
        host DRAM untouched
      </Text>

      {/* b3: the payoff for the same 128 MiB burst */}
      <div style={{ position: "absolute", left: 300, top: 724, width: 580, textAlign: "center", fontFamily: FONT, opacity: res }}>
        <div style={{ fontSize: 64, fontWeight: 800, letterSpacing: -1.5, lineHeight: 1, ...TAB }}>
          <span style={{ color: P.bad }}>91.4%</span>
          <span style={{ color: P.sub, fontSize: 44, fontWeight: 600, margin: "0 18px", opacity: zero }}>→</span>
          <span style={{ color: P.push, opacity: zero }}>0%</span>
        </div>
        <div style={{ fontSize: 24, color: P.ink, marginTop: 12 }}>host cache misses</div>
      </div>
      <BigStat x={1190} y={724} w={480} align="center" value="−98.9%" size={64} color={P.push} label="host DRAM traffic" opacity={res * dram} dy={(1 - dram) * 12} />
      <Text x={DC - 400} y={848} w={800} align="center" size={20} color={P.sub} opacity={res}>
        same 128 MiB burst · passive NIC → active NIC
      </Text>

      {/* b5: with no burst the bookkeeping is nearly free */}
      <BigStat x={DC} y={722} w={640} align="center" value="+6" unit="ns" size={60} color={P.gold} label="per round trip, with no burst" opacity={calm} dy={(1 - calm) * 12} />
    </>
  );
}

export const C09: SceneDef = {
  id: "C09",
  title: "Bursts: keep the backlog on the NIC",
  stage: 3,
  source: "Measured: 64 B bursts at 100 M/s \u00b7 1 host core + 1 NIC core",
  dur: DUR,
  cues,
  C: Body,
};
