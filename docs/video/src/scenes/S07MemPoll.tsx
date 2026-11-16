import React from "react";
import {
  Axes,
  Bars,
  C,
  Code,
  Cue,
  FONT,
  K,
  MONO,
  Packet,
  Pill,
  SceneDef,
  Stage,
  Stat,
  Txt,
  appear,
  countUp,
  fade,
  lerp,
  lin,
  logScale,
  prog,
  useT,
} from "../lib";
import { BlockId, DP_LABEL, Datapath, dpGeom, ringCell, routes } from "./pcie";
import { Check, Tag, Xmark, cycle } from "./_SceneReturn";

// <cues>
const cues: Cue[] = [
  [0.60, 11.44, "An **uncached read** of card memory costs **0.63–0.66 µs**: one full round trip over PCIe.", "Any uncached read across the link is expensive. Reading card memory from the host takes zero point six three to zero point six six microseconds: one full round trip."], // 0
  [11.79, 19.65, "The stock driver learns about replies by reading the **OUT_CNTS** register: an MMIO read on every poll.", "Yet the stock driver learns about new replies by reading the out counts register: an M M I O read on every poll."], // 1
  [20.00, 30.28, "SDP also writes an **info word** into host memory with each frame. Polling that instead: **2.41 → 2.00 µs**.", "But S D P also writes an info word into host memory, with each frame. Polling that instead cuts the round trip from two point four one to two microseconds."], // 2
  [30.63, 37.57, "The catch: the info word lands with the frame's **head**. The **tail** can arrive up to **6.3 µs** later.", "The catch: the info word lands with the head of the frame. The tail can arrive up to six point three microseconds later."], // 3
  [37.92, 49.92, "Late tails per million frames: **0** at 64 B · ≤ **2** at 256–512 B · up to **35** at 1024 B · up to **193** at 1514 B.", "Per million frames, late tails go from zero at sixty-four bytes, to two or fewer at two fifty-six and five twelve, to as many as a hundred ninety-three at full size. None were lost."], // 4
  [50.27, 58.09, "So `rx_poll_mem=1` stays **opt-in**: it is safe only because the testbed waits for the trailing seq copy.", "So this mode stays opt-in. It is safe here only because the testbed waits for the trailing sequence copy before trusting a frame."], // 5
];
const DUR = 59.49; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
const nxt = (i: number) => (i + 1 < T.length ? T[i + 1] : DUR);
/** Visible from cue i (+d) until cue j ends (held until the next cue start, then fades over 0.5 s). */
const during = (t: number, i: number, d = 0.2, j = i) => fade(t, T[i] + d, nxt(j));

// ---- zoomed frame (cue 3+), scene px
const ZY = 580; // cell row top
const ZH = 66;
const INFO_X = 60;
const INFO_W = 170;
const CX0 = 246;
const CW = 64;
const CG = 8;
const NC = 22; // cells of the frame (schematic)
const HEAD = 9; // cells that land with the info word
const cellX = (i: number) => CX0 + i * (CW + CG);
const ZX1 = cellX(NC - 1) + CW;
// time axis under the frame
const AX0 = CX0;
const PXUS = 225; // px per µs
const BAR_Y = 718;
const BAR_H = 46;
const AXIS_Y = 780;
/** Main labels only: the small layout (s < 0.6) has no room for sub-labels. */
const BARE = Object.fromEntries(Object.entries(DP_LABEL).map(([k, v]) => [k, [v[0]]])) as Record<BlockId, [string]>;

function Body() {
  const t = useT();
  let ci = 0;
  T.forEach((a, i) => {
    if (t >= a) ci = i;
  });

  // one evolving datapath: big (cues 0–2) → small top-left (cue 3+)
  const m = prog(t, T[3], 1.0);
  const g = dpGeom(lerp(0.7, 0.48, m), lerp(6, 20, m), lerp(150, 58, m));
  const r = routes(g);

  const hl =
    ci === 0
      ? { core: K.uc, ctrl: K.uc }
      : ci === 1
        ? { core: K.uc, sdp: K.uc }
        : ci === 2
          ? { core: K.mem, sdpRing: K.mem, sdp: K.sdp }
          : { sdpRing: K.mem };

  // cue 0: uncached read round trip (pull route)
  const pl = cycle(t, T[0] + 0.5, nxt(0) - 0.3, 3.4);
  // cue 1: OUT_CNTS MMIO read every poll
  const mm = cycle(t, T[1] + 0.6, nxt(1) - 0.3, 2.8);
  // cue 2: SDP replies land in the RX ring; host polls its own memory
  const REP_A = T[2] + 0.4;
  const REP_P = 3.0;
  const rp = cycle(t, REP_A, nxt(2) - 0.3, REP_P);
  const nRep = [0, 1, 2].filter((j) => t >= REP_A + j * REP_P + 1.4).length;
  const hp = cycle(t, T[2] + 1.6, nxt(2) - 0.3, 1.0);

  // late-tail story (cue 3+)
  const zo = fade(t, T[3] + 0.6, 1e9);
  const headK = (i: number) => prog(t, T[3] + 1.3 + i * 0.04, 0.3);
  const LAND = T[3] + 4.6;
  const tailK = (i: number) => {
    const j = i - HEAD;
    return prog(t, LAND + j * 0.04, 0.3) * (1 - prog(t, T[5] + 0.3, 0.4)) + prog(t, T[5] + 3.0 + j * 0.04, 0.3);
  };
  const gapK = lin(t, T[3] + 1.7, LAND - (T[3] + 1.7));
  const seqOK = prog(t, T[5] + 3.4, 0.4);
  const waiting = prog(t, T[5] + 0.9, 0.4) * (1 - seqOK);

  // bars geometry: right column (A) → top-right (B)
  const bx0 = lerp(1380, 1180, m);
  const bx1 = lerp(1850, 1760, m);
  const by0 = lerp(690, 410, m);
  const by1 = lerp(340, 215, m);
  const barsOp = fade(t, T[2] + 3.4, T[4]);

  // late-tail chart (cue 4+)
  const LX0 = 1120;
  const LX1 = 1840;
  const LY0 = 400;
  const LY1 = 205;
  const sy = logScale(0.5, 400, LY0, LY1);
  const chartOp = fade(t, T[4] + 0.5, 1e9);
  const lateK = [0.6, 3.4, 4.2, 7.4, 8.4].map((d) => prog(t, T[4] + d, 0.8));

  const cell0 = ringCell(g, "sdpRing", 0);
  const hw = 22 * g.s;
  const hh = 18 * g.s;
  const mmioX = g.pt([960, 480]);
  const infoNote = g.pt([125, 668]);

  return (
    <>
      <Stage>
        <Datapath
          g={g}
          show={{ replyRing: 0 }}
          hl={hl}
          dimOthers={0.6}
          labels={
            m > 0.5
              ? { ...BARE, sdp: ["SDP"], ctrl: ["Card DRAM word"] }
              : { sdp: ["SDP", "OUT_CNTS register · info words"], ctrl: ["Card DRAM word", "uncached read target"] }
          }
          cellFill={{
            sdpRing: (i) => (i < nRep ? K.rep : undefined),
          }}
          linkGlow={ci <= 2 ? 0.3 : 0.2}
          opacity={prog(t, 0, 0.5)}
        />
        {/* info-word strip on each landed frame */}
        {Array.from({ length: nRep }, (_, i) => {
          const [x, y] = ringCell(g, "sdpRing", i);
          return <rect key={i} x={x - hw * 0.85} y={y - hh} width={hw * 1.7} height={7 * g.s} rx={2} fill={K.mem} />;
        })}

        {/* cue 0: UC read round trip */}
        {pl.ph >= 0 && (
          <>
            <g opacity={1 - prog(pl.ph, 1.45, 0.25)}>
              <Packet pts={r.pullOut} k={prog(pl.ph, 0, 1.4)} color={K.uc} />
            </g>
            <g opacity={1 - prog(pl.ph, 3.0, 0.3)}>
              <Packet pts={r.pullBack} k={prog(pl.ph, 1.5, 1.4)} color={K.uc} />
            </g>
          </>
        )}

        {/* cue 1: OUT_CNTS MMIO read per poll */}
        {mm.ph >= 0 && (
          <>
            <g opacity={1 - prog(mm.ph, 1.25, 0.25)}>
              <Packet pts={r.mmioOut} k={prog(mm.ph, 0, 1.2)} color={K.uc} />
            </g>
            <g opacity={1 - prog(mm.ph, 2.5, 0.25)}>
              <Packet pts={r.mmioBack} k={prog(mm.ph, 1.3, 1.2)} color={K.uc} />
            </g>
          </>
        )}

        {/* cue 2: MMIO crossed out, reply lands, host polls own memory */}
        <g opacity={during(t, 2, 0.3) * 0.5}>
          {[r.mmioOut, r.mmioBack].map((p, i) => (
            <polyline key={i} points={p.map((q) => q.join(",")).join(" ")} fill="none" stroke={K.uc} strokeWidth={3} strokeDasharray="8 8" />
          ))}
        </g>
        <Xmark x={mmioX[0]} y={mmioX[1]} s={0.8} opacity={during(t, 2, 0.5)} />
        {rp.ph >= 0 && (
          <g opacity={1 - prog(rp.ph, 1.5, 0.3)}>
            <Packet pts={r.sdpReply} k={prog(rp.ph, 0, 1.4)} color={K.rep} />
          </g>
        )}
        {hp.ph >= 0 && (
          <g opacity={1 - prog(hp.ph, 0.55, 0.25)}>
            <Packet pts={r.hostPollSdp} k={prog(hp.ph, 0, 0.5)} color={K.mem} r={9} />
          </g>
        )}
        <text x={infoNote[0]} y={infoNote[1]} fontSize={24} fontWeight={700} fill={K.mem} fontFamily={FONT} dominantBaseline="hanging" opacity={during(t, 2, 1.6)}>
          ▲ frame + its info word, in host memory
        </text>

        {/* latency bars 2.41 → 2.00 (cue 2–3) */}
        <g opacity={barsOp}>
          <Bars
            x0={bx0}
            x1={bx1}
            y0={by0}
            y1={by1}
            max={2.6}
            size={26}
            data={[
              { label: "OUT_CNTS poll", value: 2.41, text: "2.41 µs", color: K.sdp },
              { label: "info-word poll", value: 2.0, text: "2.00 µs", color: K.mem },
            ]}
            k={[prog(t, T[2] + 3.6, 0.8), prog(t, T[2] + 6.2, 0.8)]}
          />
        </g>

        {/* cue 3+: zoom of one frame */}
        <g opacity={zo}>
          <polygon
            points={`${cell0[0] - hw},${cell0[1] + hh} ${cell0[0] + hw},${cell0[1] + hh} ${INFO_X + INFO_W},${ZY - 10} ${INFO_X},${ZY - 10}`}
            fill={K.mem}
            opacity={0.12}
          />
          <polyline points={`${cell0[0] - hw},${cell0[1] + hh} ${INFO_X},${ZY - 10}`} fill="none" stroke={K.mem} strokeWidth={2} strokeDasharray="6 6" opacity={0.8} />
          <polyline points={`${cell0[0] + hw},${cell0[1] + hh} ${INFO_X + INFO_W},${ZY - 10}`} fill="none" stroke={K.mem} strokeWidth={2} strokeDasharray="6 6" opacity={0.8} />
          <text x={CX0} y={ZY - 22} fontSize={26} fontWeight={700} fill={C.text} fontFamily={FONT}>
            One frame in host memory
            <tspan fill={C.muted} fontWeight={400} fontSize={22}>
              {"  (schematic)"}
            </tspan>
          </text>
          {/* info word */}
          <rect x={INFO_X} y={ZY} width={INFO_W} height={ZH} rx={8} fill={K.mem} opacity={0.15 + 0.85 * headK(0)} stroke={K.mem} strokeWidth={2} />
          <text x={INFO_X + INFO_W / 2} y={ZY + ZH / 2} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={800} fill={headK(0) > 0.5 ? C.bg : K.mem} fontFamily={FONT}>
            info word
          </text>
          {/* data cells */}
          {Array.from({ length: NC }, (_, i) => {
            const isTail = i >= HEAD;
            const k = isTail ? tailK(i) : headK(i);
            const x = cellX(i);
            return (
              <g key={i}>
                <rect x={x} y={ZY} width={CW} height={ZH} rx={6} fill="rgba(139,149,173,0.06)" stroke={isTail ? K.bad : C.dim} strokeWidth={isTail ? 2.5 : 1.5} strokeDasharray={isTail ? "6 5" : undefined} />
                <rect x={x} y={ZY} width={CW} height={ZH} rx={6} fill={K.rep} opacity={0.85 * k} />
                {i === NC - 1 && (
                  <text x={x + CW / 2} y={ZY + ZH / 2} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={800} fontFamily={MONO} fill={k > 0.5 ? C.bg : C.muted}>
                    seq
                  </text>
                )}
              </g>
            );
          })}
          <text x={CX0} y={ZY + ZH + 32} fontSize={24} fontWeight={700} fill={K.rep} fontFamily={FONT} opacity={prog(t, T[3] + 1.5, 0.4)}>
            head: lands with the info word
          </text>
          <text x={cellX(HEAD)} y={ZY + ZH + 32} fontSize={24} fontWeight={700} fill={K.bad} fontFamily={FONT} opacity={prog(t, T[3] + 2.0, 0.4)}>
            tail: written later
          </text>
          {/* time axis */}
          <line x1={AX0} y1={AXIS_Y} x2={ZX1} y2={AXIS_Y} stroke={C.dim} strokeWidth={2.5} />
          {[0, 2, 4].map((v) => (
            <g key={v}>
              <line x1={AX0 + v * PXUS} y1={AXIS_Y} x2={AX0 + v * PXUS} y2={AXIS_Y + 8} stroke={C.dim} strokeWidth={2} />
              <text x={AX0 + v * PXUS} y={AXIS_Y + 32} textAnchor="middle" fontSize={22} fill={C.muted} fontFamily={FONT}>
                {v}
              </text>
            </g>
          ))}
          <rect x={AX0} y={BAR_Y} width={Math.max(0, 6.3 * PXUS * gapK)} height={BAR_H} rx={6} fill="url(#hatchRed)" opacity={0.9} />
          <rect x={AX0 - 5} y={BAR_Y - 6} width={10} height={BAR_H + 12} rx={3} fill={K.mem} opacity={prog(t, T[3] + 1.3, 0.3)} />
          <g opacity={prog(t, LAND - 0.1, 0.4)}>
            <line x1={AX0 + 6.3 * PXUS} y1={BAR_Y - 8} x2={AX0 + 6.3 * PXUS} y2={AXIS_Y + 8} stroke={K.rep} strokeWidth={4} />
            <text x={AX0 + 6.3 * PXUS} y={AXIS_Y + 32} textAnchor="middle" fontSize={22} fontWeight={700} fill={K.rep} fontFamily={FONT}>
              6.3 µs
            </text>
            <Tag x={AX0 + 3.15 * PXUS} y={BAR_Y + BAR_H / 2} size={26} border={K.bad}>
              tail lags the info word: up to 6.3 µs
            </Tag>
          </g>
          {/* cue 5: host waits for the trailing seq copy */}
          <rect
            x={cellX(NC - 1) - 6}
            y={ZY - 6}
            width={CW + 12}
            height={ZH + 12}
            rx={9}
            fill="none"
            stroke={seqOK > 0.5 ? K.ok : K.mem}
            strokeWidth={4}
            strokeDasharray={seqOK > 0.5 ? undefined : "8 6"}
            opacity={Math.max(waiting, seqOK) * (seqOK > 0 ? 1 : 0.6 + 0.4 * Math.abs(Math.sin(t * 4)))}
          />
          <Check x={cellX(NC - 1) + CW / 2} y={ZY - 34} s={0.75} opacity={seqOK} />
        </g>

        {/* cue 4: late tails per million frames */}
        <g opacity={chartOp}>
          <Axes x0={LX0} x1={LX1} y0={LY0} y1={LY1} sx={(v) => v} sy={sy} xticks={[]} yticks={[[1, "1"], [10, "10"], [100, "100"]]} />
          <Bars
            x0={LX0}
            x1={LX1}
            y0={LY0}
            y1={LY1}
            log
            min={0.5}
            max={400}
            size={24}
            data={[
              { label: "64 B", value: 0, color: K.ok },
              { label: "256 B", value: 2, color: K.bad },
              { label: "512 B", value: 1, color: K.bad },
              { label: "1024 B", value: 35, color: K.bad },
              { label: "1514 B", value: 193, color: K.bad },
            ]}
            k={lateK}
          />
          <Check x={LX0 + 18} y={LY0 + 66} s={0.55} opacity={prog(t, T[4] + 10.0, 0.4)} ring={false} />
        </g>
      </Stage>

      {/* cue 0: timer */}
      <Stat
        value={
          <span style={{ display: "inline-grid", justifyItems: "center" }}>
            <span style={{ gridArea: "1 / 1", opacity: 1 - prog(t, T[0] + 3.4, 0.5) }}>{countUp(t, T[0] + 0.5, 2.9, 0.63, 2)} µs</span>
            <span style={{ gridArea: "1 / 1", opacity: prog(t, T[0] + 3.4, 0.5) }}>0.63–0.66 µs</span>
          </span>
        }
        label="uncached read of card DRAM · p50"
        color={K.timer}
        size={64}
        style={{ position: "absolute", left: 1336, top: 280, width: 540, boxSizing: "border-box", ...appear(t, T[0] + 0.3, nxt(0)) }}
      />
      <Txt x={1336} y={500} w={540} size={38} weight={800} color={K.uc} align="center" style={appear(t, T[0] + 3.4, nxt(0))}>
        one full round trip
      </Txt>
      <Txt x={1336} y={570} w={540} size={26} color={C.muted} align="center" style={appear(t, T[0] + 5.0, nxt(0))}>
        the reading core stalls until the completion comes back
      </Txt>

      {/* cue 1: MMIO every poll */}
      <Txt x={1336} y={275} w={540} size={38} weight={800} color={K.uc} style={appear(t, T[1] + 0.5, nxt(1))}>
        MMIO read every poll
      </Txt>
      <Code
        x={1336}
        y={350}
        w={540}
        size={24}
        hl={mm.ph >= 0 && mm.ph < 2.5 ? 2 : -1}
        lines={["// stock PMD rx poll", "for (;;) {", "  n = read(OUT_CNTS); // MMIO", "  if (n) take(n);", "}"]}
        style={{ boxSizing: "border-box", ...appear(t, T[1] + 0.6, nxt(1)) }}
      />
      <Txt x={1336} y={590} w={540} size={26} color={C.muted} style={appear(t, T[1] + 2.0, nxt(1))}>
        each poll = one uncached round trip, even when no reply is waiting
      </Txt>

      {/* cue 2–3: bars header */}
      <Txt x={lerp(1336, 980, m)} y={lerp(280, 122, m)} size={26} weight={700} color={C.text} style={{ opacity: barsOp }}>
        Inline W=1 round trip, p50
      </Txt>

      {/* cue 4: chart header + note */}
      <Txt x={980} y={122} size={26} weight={700} color={C.text} style={{ opacity: chartOp }}>
        Late tails per 1 M frames <span style={{ color: C.muted, fontWeight: 400 }}>(worst of W=1 / W=32, log)</span>
      </Txt>
      <Txt x={LX0 + 44} y={LY0 + 48} size={28} weight={700} color={K.ok} style={appear(t, T[4] + 10.0)}>
        none lost within 1 ms
      </Txt>

      {/* cue 3 time-axis label */}
      <Txt x={60} y={BAR_Y - 4} w={170} size={22} color={C.muted} style={{ opacity: zo, lineHeight: 1.15 }}>
        time after the info word (µs)
      </Txt>

      {/* cue 5 */}
      <Txt x={1122} y={ZY + ZH + 10} w={700} size={24} weight={700} align="right" color={K.mem} style={{ opacity: waiting > 0 ? Math.min(1, waiting * 2) : 0 }}>
        host waits for the trailing seq copy…
      </Txt>
      <Txt x={1122} y={ZY + ZH + 10} w={700} size={24} weight={700} align="right" color={K.ok} style={{ opacity: seqOK }}>
        trailing seq copy matches → frame complete
      </Txt>
      <Pill x={60} y={830} size={30} color={K.mem} solid style={appear(t, T[5] + 0.3)}>
        <span style={{ fontFamily: MONO }}>rx_poll_mem=1</span> → opt-in
      </Pill>
      <Txt x={560} y={836} size={28} color={C.text} style={appear(t, T[5] + 1.0)}>
        safe only because the testbed waits for the trailing seq copy
      </Txt>
    </>
  );
}

export const S07: SceneDef = {
  id: "S07",
  title: "Way back #1: poll memory, not MMIO",
  stage: 2,
  source: "octeon_ep PMD · devarg rx_poll_mem=1 (local patch)",
  dur: DUR,
  cues,
  C: Body,
};
