import React from "react";
import { Arrow, C, Card, Cue, FONT, K, Packet, Pill, SceneDef, Stage, Stat, Txt, along, appear, clamp01, fade, lerp, lin, prog, useT } from "../lib";
import { BlockId, DP_LABEL, Datapath, InfTag, StackBar, dpGeom, routes } from "./pcie";
import { Tag, Xmark, cycle } from "./_SceneReturn";

// <cues>
const cues: Cue[] = [
  [0.60, 10.14, "The card has its own DMA engine, **DPI**. The reflector copies each reply straight into a **ring in host memory**.", "The card has its own D M A engine, called D P I. The reflector uses it to copy each reply straight into a ring in host memory."], // 0
  [10.49, 20.73, "DPI writes as host **VF0**, so it must target an address inside the host DPDK process: the **IOMMU** is on.", "The D P I writes on behalf of the host's virtual function zero, so it must target an address inside the host D P D K process. The I O M M U is on."], // 1
  [21.08, 31.80, "The host polls only its own memory, with no MMIO at all. W=1 round trip: **1.33–1.60 µs** p50, run to run.", "Now the host polls only its own memory, with no M M I O at all. The median round trip is one point three three to one point six microseconds, depending on the run."], // 2
  [32.15, 46.37, "Card side, measured: poll loop **18 ns** · cell seen → DMA doorbell **59–67 ns** · DPI doorbell → done **675 ns**.", "On the card, we timed each piece. The poll loop takes eighteen nanoseconds. Seeing a cell and ringing the D M A doorbell takes about sixty. And the D P I needs six hundred seventy-five nanoseconds to finish the copy."], // 3
  [46.72, 57.10, "That 675 ns ignores the source line's cache state, and cleaning caches first only made it slower: it's **inside DPI**.", "Those six hundred seventy-five nanoseconds don't change with the cache state of the source, and cleaning caches first only made things slower. The cost is inside the D P I."], // 4
  [57.45, 70.53, "**[INF]** Budget: ~0.3 host→card + ~0.08 card + ~0.6 DPI + ~0.3 card→host ≈ **1.3 µs**. One-way halves are inferred.", "Adding it up, we estimate point three microseconds host to card, point zero eight on the card, point six in the D P I, and point three back. The one way halves are our inference, from splitting the measured read in two."], // 5
];
const DUR = 71.93; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
const nxt = (i: number) => (i + 1 < T.length ? T[i + 1] : DUR);
/** Visible from cue i (+d) until cue j ends (held until the next cue start, then fades over 0.5 s). */
const during = (t: number, i: number, d = 0.2, j = i) => fade(t, T[i] + d, nxt(j));
/** Main labels only: the small layout (s ≈ 0.5) has no room for sub-labels. */
const BARE = Object.fromEntries(Object.entries(DP_LABEL).map(([k, v]) => [k, [v[0]]])) as Record<BlockId, [string]>;

// ---- card-side timeline (cue 3–4), ns
const TX0 = 100;
const PXNS = 2.25;
const TY = 640;
const TH = 64;
const SEGS = [
  { ns: 18, color: K.card, at: 2.0, d: 0.6 },
  { ns: 63, color: K.card, at: 5.2, d: 0.8 },
  { ns: 675, color: K.dma, at: 8.6, d: 2.0 },
];
const segX = (i: number) => TX0 + SEGS.slice(0, i).reduce((s, g) => s + g.ns * PXNS, 0);

// ---- budget bar (cue 5), µs
const BX0 = 80;
const PXUS = 1330;
const BY = 630;
const BH = 70;
const BUDGET = [
  { label: "host→card ~0.3", us: 0.3, color: K.req, inf: true },
  { label: "card ~0.08", us: 0.08, color: K.card },
  { label: "DPI ~0.6", us: 0.6, color: K.dma },
  { label: "card→host ~0.3", us: 0.3, color: K.rep, inf: true },
];
const BX1 = BX0 + BUDGET.reduce((s, b) => s + b.us, 0) * PXUS;

function Body() {
  const t = useT();
  let ci = 0;
  T.forEach((a, i) => {
    if (t >= a) ci = i;
  });

  // one evolving datapath: big left (0–2) → small centred (3) → small left (4–5)
  const m1 = prog(t, T[3] + 0.5, 1.0);
  const m2 = prog(t, T[4], 1.0);
  const g = dpGeom(lerp(0.7, 0.5, m1), lerp(lerp(6, 480, m1), 20, m2), lerp(150, 50, m1));
  const r = routes(g);

  const hl: Partial<Record<BlockId, string>> =
    ci === 0
      ? { dpi: K.dma, replyRing: K.dma }
      : ci === 1
        ? { dpi: K.dma, hmem: K.mem, replyRing: K.dma }
        : ci === 2
          ? { core: K.mem, replyRing: K.mem }
          : ci === 3
            ? { cores: K.card, reqRing: K.req, dpi: K.dma }
            : ci === 4
              ? { dpi: K.dma }
              : { reqRing: K.req, cores: K.card, dpi: K.dma, replyRing: K.dma };

  // cue 0–1: replies DMA'd into the host reply ring
  const REP_A = T[0] + 0.8;
  const REP_P = 3.2;
  const rp = cycle(t, REP_A, nxt(1) - 0.3, REP_P);
  const nRep = Array.from({ length: 10 }, (_, j) => j).filter((j) => t >= REP_A + j * REP_P + 2.3 && REP_A + j * REP_P + REP_P <= nxt(1) - 0.3).length;
  const repK = rp.ph >= 0 ? prog(rp.ph, 0.7, 1.6) : 0;
  const repPos = along(r.dmaReply, Math.max(0.001, repK));
  const gateP = g.pt([700, 515]);
  // gate lights up while the reply crosses it
  const gatePass = rp.ph >= 0 && repK > 0 && repK < 1 ? clamp01(1.4 - Math.hypot(repPos[0] - gateP[0], repPos[1] - gateP[1]) / (70 * g.s)) : 0;
  const vf0 = during(t, 1, 0.3);

  // cue 2: host polls its own memory
  const hp = cycle(t, T[2] + 0.8, nxt(2) - 0.3, 1.0);
  const mmioX = g.pt([960, 480]);

  // cue 3: one request through the card
  const reqLand = T[3] + 0.6 + 1.4;
  const cp = cycle(t, T[3] + 2.0, T[3] + 5.2, 0.8);
  // cue 5: one full round trip in sync with the budget bar
  const bK = lin(t, T[5] + 1.0, 7.0);
  const b5 = (i: number) => T[5] + 1.0 + i * 1.75;

  const timelineOp = fade(t, T[3] + 0.8, T[5]);
  const budgetOp = fade(t, T[5] + 0.5, 1e9);
  const dpiR = g.r.dpi;
  const dpiPulse = ci === 4 ? 0.5 + 0.5 * Math.sin(t * 5) : 0;
  const tagS = Math.max(20, 24 * g.s);

  return (
    <>
      <Stage>
        <Datapath
          g={g}
          show={{ sdpRing: 0.35, ctrl: 0.35 }}
          hl={hl}
          dimOthers={0.6}
          labels={m1 > 0.5 ? BARE : { hmem: ["Host memory", "DPDK process memory"] }}
          cellFill={{
            replyRing: (i) => (i < nRep || (ci === 5 && i === 0 && t > b5(3) + 1.6) ? K.dma : undefined),
            reqRing: (i) => (i === 0 && ((ci === 3 && t > reqLand) || ci === 4 || (ci === 5 && t > b5(0) + 1.6)) ? K.req : undefined),
          }}
          cores={1}
          activeCores={1}
          linkGlow={0.3}
          opacity={prog(t, 0, 0.5)}
        />
        {/* DPI pulse (cue 4: the cost is inside DPI) */}
        <rect x={dpiR.x - 6} y={dpiR.y - 6} width={dpiR.w + 12} height={dpiR.h + 12} rx={14} fill="none" stroke={K.dma} strokeWidth={4} opacity={dpiPulse * during(t, 4, 0.5)} filter="url(#glow)" />

        {/* IOMMU gate on the host side of the link */}
        <g opacity={fade(t, T[1] + 0.3, 1e9)}>
          <rect x={gateP[0] - 9 * g.s} y={gateP[1] - 34 * g.s} width={18 * g.s} height={68 * g.s} rx={4} fill={C.panel} stroke={C.text} strokeWidth={3} />
          <rect x={gateP[0] - 9 * g.s} y={gateP[1] - 34 * g.s} width={18 * g.s} height={68 * g.s} rx={4} fill={K.ok} stroke={K.ok} strokeWidth={3} opacity={gatePass} filter="url(#glow)" />
          <Tag x={gateP[0] + 4} y={g.pt([700, 572])[1]} size={tagS} anchor="start" border={C.muted} color={C.text}>
            IOMMU on
          </Tag>
        </g>

        {/* cue 0–1: DPI DMA replies into the host reply ring */}
        {rp.ph >= 0 && (
          <>
            <g opacity={1 - prog(rp.ph, 0.6, 0.2)}>
              <Packet pts={r.coreToDpi} k={prog(rp.ph, 0, 0.6)} color={K.card} r={9} />
            </g>
            <g opacity={1 - prog(rp.ph, 2.35, 0.3)}>
              <Packet pts={r.dmaReply} k={repK} color={K.dma} />
              {repK > 0 && (
                <Tag x={repPos[0] - 18} y={repPos[1] - 30} size={22} anchor="end" color={K.dma} opacity={vf0}>
                  as host VF0
                </Tag>
              )}
            </g>
          </>
        )}

        {/* cue 2: no MMIO; host polls its own reply ring */}
        <g opacity={during(t, 2, 0.3) * 0.5}>
          {[r.mmioOut, r.mmioBack].map((p, i) => (
            <polyline key={i} points={p.map((q) => q.join(",")).join(" ")} fill="none" stroke={K.uc} strokeWidth={3} strokeDasharray="8 8" />
          ))}
        </g>
        <Xmark x={mmioX[0]} y={mmioX[1]} s={0.8} opacity={during(t, 2, 0.5)} />
        {hp.ph >= 0 && (
          <g opacity={1 - prog(hp.ph, 0.6, 0.25)}>
            <Packet pts={r.hostPollReply} k={prog(hp.ph, 0, 0.55)} color={K.mem} r={9} />
          </g>
        )}

        {/* cue 3: request lands, card polls, rings DPI, DPI copies */}
        {ci === 3 && (
          <>
            <g opacity={1 - prog(t, reqLand, 0.3)}>
              <Packet pts={r.inline} k={prog(t, T[3] + 0.6, 1.4)} color={K.req} r={9} />
            </g>
            {cp.ph >= 0 && (
              <g opacity={1 - prog(cp.ph, 0.45, 0.2)}>
                <Packet pts={r.cardPoll} k={prog(cp.ph, 0, 0.4)} color={K.card} r={8} trail={false} />
              </g>
            )}
            <g opacity={1 - prog(t, T[3] + 6.1, 0.3)}>
              <Packet pts={r.coreToDpi} k={prog(t, T[3] + 5.2, 0.8)} color={K.card} r={9} />
            </g>
            <g opacity={1 - prog(t, T[3] + 11.0, 0.4)}>
              <Packet pts={r.dmaReply} k={prog(t, T[3] + 8.6, 2.2)} color={K.dma} />
            </g>
          </>
        )}

        {/* cue 5: one round trip in step with the budget bar */}
        {ci === 5 && (
          <>
            <g opacity={1 - prog(t, b5(0) + 1.6, 0.3)}>
              <Packet pts={r.inline} k={prog(t, b5(0), 1.6)} color={K.req} r={9} />
            </g>
            <g opacity={1 - prog(t, b5(1) + 1.6, 0.3)}>
              <Packet pts={r.cardPoll} k={prog(t, b5(1), 0.6)} color={K.card} r={8} trail={false} />
              <Packet pts={r.coreToDpi} k={prog(t, b5(1) + 0.7, 0.9)} color={K.card} r={9} />
            </g>
            <rect x={dpiR.x - 6} y={dpiR.y - 6} width={dpiR.w + 12} height={dpiR.h + 12} rx={14} fill="none" stroke={K.dma} strokeWidth={4} opacity={fade(t, b5(2), b5(3), 0.3)} filter="url(#glow)" />
            <g opacity={1 - prog(t, b5(3) + 1.6, 0.3)}>
              <Packet pts={r.dmaReply} k={prog(t, b5(3), 1.6)} color={K.dma} />
            </g>
          </>
        )}

        {/* cue 3–4: card-side timeline (ns) */}
        <g opacity={timelineOp} fontFamily={FONT}>
          {SEGS.map((s, i) => {
            const k = prog(t, T[3] + s.at, s.d);
            const x = segX(i);
            return (
              <g key={i}>
                <rect x={x} y={TY} width={Math.max(0, s.ns * PXNS * k)} height={TH} rx={6} fill={`${s.color}40`} stroke={s.color} strokeWidth={2.5} opacity={k > 0 ? 1 : 0} />
              </g>
            );
          })}
          <g opacity={prog(t, T[3] + 2.3, 0.4)}>
            <line x1={segX(0) + 9} y1={TY - 6} x2={segX(0) + 9} y2={TY - 26} stroke={K.card} strokeWidth={2.5} />
            <text x={TX0} y={TY - 38} fontSize={28} fontWeight={700} fill={K.card}>
              poll loop · 18 ns
            </text>
          </g>
          <g opacity={prog(t, T[3] + 5.6, 0.4)}>
            <line x1={segX(1) + 30} y1={TY + TH + 6} x2={segX(1) + 30} y2={TY + TH + 22} stroke={K.card} strokeWidth={2.5} />
            <text x={segX(1)} y={TY + TH + 50} fontSize={28} fontWeight={700} fill={K.card}>
              cell seen → DMA doorbell · 59–67 ns
            </text>
          </g>
          <text x={segX(2) + (675 * PXNS) / 2} y={TY + TH / 2} textAnchor="middle" dominantBaseline="central" fontSize={32} fontWeight={800} fill={C.text} opacity={prog(t, T[3] + 10.0, 0.5)}>
            DPI doorbell → done · 675 ns
          </text>
          <rect x={segX(2) - 4} y={TY - 4} width={675 * PXNS + 8} height={TH + 8} rx={8} fill="none" stroke={K.dma} strokeWidth={3} filter="url(#glow)" opacity={dpiPulse * during(t, 4, 7.5)} />
          {/* axis */}
          <line x1={TX0} y1={TY + TH + 92} x2={segX(3)} y2={TY + TH + 92} stroke={C.dim} strokeWidth={2.5} opacity={prog(t, T[3] + 1.6, 0.5)} />
          {[0, 250, 500, 750].map((v) => (
            <g key={v} opacity={prog(t, T[3] + 1.6, 0.5)}>
              <line x1={TX0 + v * PXNS} y1={TY + TH + 92} x2={TX0 + v * PXNS} y2={TY + TH + 100} stroke={C.dim} strokeWidth={2} />
              <text x={TX0 + v * PXNS} y={TY + TH + 126} textAnchor="middle" fontSize={22} fill={C.muted}>
                {v === 750 ? "750 ns" : v}
              </text>
            </g>
          ))}
        </g>

        {/* cue 4: cache-state test */}
        <g opacity={during(t, 4, 0.8)} fontFamily={FONT}>
          {[
            ["clean", 1.5],
            ["dirty", 3.0],
          ].map(([lab, at], i) => {
            const k = prog(t, T[4] + (at as number), 0.9);
            const y = 190 + i * 66;
            return (
              <g key={i} opacity={prog(t, T[4] + (at as number), 0.3)}>
                <text x={1000} y={y + 22} dominantBaseline="central" fontSize={28} fontWeight={700} fill={C.text}>
                  {lab}
                </text>
                <rect x={1110} y={y} width={675 * 0.8 * k} height={44} rx={6} fill={`${K.dma}40`} stroke={K.dma} strokeWidth={2.5} />
                <text x={1110 + 675 * 0.8 * k + 14} y={y + 22} dominantBaseline="central" fontSize={28} fontWeight={800} fill={K.dma} opacity={prog(t, T[4] + (at as number) + 0.6, 0.3)}>
                  675 ns
                </text>
              </g>
            );
          })}
        </g>
        <Arrow x1={1000} y1={430} x2={dpiR.x + dpiR.w + 10} y2={dpiR.y + dpiR.h / 2 + 6} color={K.dma} w={4} k={prog(t, T[4] + 7.8, 0.6)} opacity={during(t, 4, 7.6)} />

        {/* cue 5: budget bar */}
        <g opacity={budgetOp} fontFamily={FONT}>
          <StackBar x={BX0} y={BY} h={BH} px={PXUS} segs={BUDGET} k={bK} fontSize={26} />
          <g opacity={prog(t, T[5] + 8.2, 0.5)}>
            <path d={`M${BX0} ${BY + BH + 14} v14 H${BX1} v-14`} fill="none" stroke={C.text} strokeWidth={2.5} />
            <text x={(BX0 + BX1) / 2 - 40} y={BY + BH + 64} textAnchor="middle" dominantBaseline="central" fontSize={44} fontWeight={800} fill={C.text}>
              ≈ 1.3 µs
            </text>
            <InfTag x={(BX0 + BX1) / 2 + 60} y={BY + BH + 49} />
          </g>
          <g opacity={prog(t, T[5] + 9.5, 0.5)}>
            <InfTag x={BX0} y={BY + BH + 112} />
            <text x={BX0 + 82} y={BY + BH + 127} dominantBaseline="central" fontSize={26} fill={C.muted}>
              dashed one-way halves = the measured 0.63 µs read, split in two
            </text>
          </g>
        </g>
      </Stage>

      {/* cue 0 */}
      <Card x={1336} y={280} w={540} title="DPI: the card's DMA engine" titleColor={K.dma} border={K.dma} style={appear(t, T[0] + 0.4, nxt(0))}>
        <div style={{ fontSize: 28, color: C.text, lineHeight: 1.4 }}>
          The reflector has DPI copy each <b>64 B reply</b> straight into a <b style={{ color: K.dma }}>ring in host memory</b>.
        </div>
      </Card>

      {/* cue 1 */}
      <Card x={1336} y={280} w={540} title="DPI writes as host VF0" titleColor={K.dma} border={K.dma} style={appear(t, T[1] + 0.5, nxt(1))}>
        <div style={{ fontSize: 28, color: C.text, lineHeight: 1.4 }}>
          Every target address must be an <b style={{ color: K.mem }}>IOVA inside the host DPDK process</b> memory.
        </div>
        <div style={{ fontSize: 28, color: C.text, marginTop: 14 }}>
          Host <b>IOMMU: on</b>
        </div>
      </Card>

      {/* cue 2 */}
      <Stat
        value="1.33–1.60 µs"
        label="W=1 round trip · p50 · run to run"
        color={K.mem}
        size={64}
        style={{ position: "absolute", left: 1336, top: 280, width: 540, boxSizing: "border-box", ...appear(t, T[2] + 4.5, nxt(2)) }}
      />
      <Txt x={1336} y={500} w={540} size={32} weight={800} color={K.mem} align="center" style={appear(t, T[2] + 0.6, nxt(2))}>
        host polls only its own memory
      </Txt>
      <Txt x={1336} y={550} w={540} size={32} weight={800} color={K.uc} align="center" style={appear(t, T[2] + 1.6, nxt(2))}>
        no MMIO at all
      </Txt>

      {/* cue 3 header */}
      <Txt x={60} y={518} size={28} weight={700} style={{ opacity: timelineOp }}>
        Card side, measured <span style={{ color: C.muted, fontWeight: 400 }}>(one reply, ns)</span>
      </Txt>

      {/* cue 4 */}
      <Txt x={1000} y={124} size={26} weight={700} style={appear(t, T[4] + 0.8, nxt(4))}>
        DPI doorbell → done, by source-line cache state
      </Txt>
      <Txt x={1000} y={330} size={28} color={C.muted} style={{ textDecoration: "line-through", ...appear(t, T[4] + 5.0, nxt(4)) }}>
        clean caches first (DC CVAC / CIVAC)
      </Txt>
      <Pill x={1540} y={326} size={24} color={K.bad} style={appear(t, T[4] + 5.6, nxt(4))}>
        slower · removed
      </Pill>
      <Pill x={1000} y={406} size={30} color={K.dma} solid style={appear(t, T[4] + 7.6, nxt(4))}>
        cost is inside DPI
      </Pill>

      {/* cue 5 */}
      <Txt x={60} y={518} size={28} weight={700} style={{ opacity: budgetOp }}>
        Round-trip budget <span style={{ color: C.muted, fontWeight: 400 }}>(µs, W=1)</span>
      </Txt>
      <Stat
        value="1.33–1.60 µs"
        label="measured W=1 p50"
        color={K.mem}
        size={56}
        labelSize={26}
        style={{ position: "absolute", left: 1100, top: 170, width: 560, boxSizing: "border-box", ...appear(t, T[5] + 0.8) }}
      />
    </>
  );
}

export const S08: SceneDef = {
  id: "S08",
  title: "Way back #2: the card's DMA engine",
  stage: 2,
  source: "inline_reflector --dma · DPI VF 0000:06:00.1",
  dur: DUR,
  cues,
  C: Body,
};
