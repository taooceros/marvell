import React from "react";
import { Arrow, C, Cue, K, MONO, PacketStream, SceneDef, Stage, Txt, clamp01, countUp, fade, lerp, partial, pathD, along, prog, rng, useRealT, useT } from "../lib";
import { Datapath, dpGeom, ringCell, routes } from "./pcie";
import { Bell, Centered, Hi, Legs, SDP_SHORT, SDP_SHORT_BELOW, dimWeights } from "./_SceneIntro";

// <cues>
const cues: Cue[] = [
  [0.60, 9.68, "The card's PCIe function exposes **BAR4**: 16 windows of **4 MiB**; a card register sets where each one points.", "The card's P C I function exposes BAR four: sixteen windows of four megabytes, and a card register decides where each one points."], // 0
  [10.03, 18.63, "On the card, `inline_reflector` repoints **window 15** at a 4 MiB hugepage, and restores it on exit.", "On the card, our reflector repoints window fifteen at a four megabyte hugepage, and restores the old mapping when it exits."], // 1
  [18.98, 27.98, "The host writes each 64 B **cell** with one **MOVDIR64B**: a single 64-byte store, and no doorbell.", "The host writes each sixty-four byte cell with one move dir sixty-four B instruction: a single sixty-four byte store, and no doorbell."], // 2
  [28.33, 37.83, "A card core **polls** the next cell in its ring. The hugepage sees PCIe writes coherently: nothing to flush.", "A card core simply polls the next cell in its ring. The hugepage sees incoming P C I express writes coherently, so nothing needs flushing."], // 3
  [38.18, 46.66, "W=1 round trip: **4.85 → 2.41 µs**, with replies still coming back over SDP.", "The round trip drops from four point eight five to two point four one microseconds, and that's with replies still coming back over S D P."], // 4
  [47.01, 58.61, "One card core becomes the limit: **13.7–15.9 M msg/s** while checking every payload (varies by session), vs **~7.5** for SDP.", "Throughput roughly doubles. One card core becomes the limit, at fourteen to sixteen million messages per second while checking every payload, against seven and a half for S D P."], // 5
];
const DUR = 60.01; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// BAR4 window row (c0–c1, under the shrunk datapath) and the card address space it maps into.
const RX0 = 400;
const RW = 75;
const RPITCH = 85;
const RY = 628;
const RH = 50;
const AX0 = 400;
const AX1 = 1750;
const AY = 798;
const AH = 40;
const rand = rng(4);
/** Window i → an arbitrary card address (x on the address bar); deterministic shuffle + jitter. */
const TARGET = Array.from({ length: 16 }, (_, i) => 430 + ((i * 5 + 3) % 16) * 80 + rand() * 30);

// c2 writes / c3 polls: seconds after T[2] / T[3] for cell i.
const WRITE_AT = Array.from({ length: 10 }, (_, i) => 1.2 + i * 0.75);
const WRITE_D = 0.7;
const READ_AT = Array.from({ length: 10 }, (_, i) => 1.2 + i * 0.75);

// c4/c5 horizontal bars
const BX0 = 520;
const BX1 = 1700;
const ROW = [640, 730];
const BH = 62;
const AXIS_Y = 812;

function Body() {
  const t = useT();
  const rt = useRealT();

  // Shrunk on top in c0–c1 and c4–c5 (content band below); large in c2–c3 for the cell writes.
  const mL = prog(t, T[2], 1.0) * (1 - prog(t, T[4], 1.0));
  const g = dpGeom(lerp(0.6, 0.8, mL), lerp(384, 192, mL), lerp(40, 5, mL));
  const gS = dpGeom(0.6, 384, 40);
  const r = routes(g);

  // highlights
  const kPem = fade(t, T[0] + 0.6, T[1] - 0.1);
  const kReq = fade(t, T[1] + 2.8, T[5] - 0.1);
  const kCore = fade(t, T[2] + 0.6, T[3] - 0.1);
  const kCores = Math.max(fade(t, T[3] + 0.4, T[4] - 0.1), fade(t, T[5] + 2.0));
  const kSdp = fade(t, T[4] + 0.4, T[5] - 0.1);
  const show = dimWeights({ pem: kPem, reqRing: kReq, core: kCore, cores: kCores, sdp: kSdp });

  // c2/c3 ring cells: written (request colour) → consumed (verified colour) → cleared in c4
  const clear = 1 - prog(t, T[4], 0.6);
  const alphaHex = Math.round(clear * 255)
    .toString(16)
    .padStart(2, "0");
  const cellFill = (i: number) => {
    if (t < T[2] + WRITE_AT[i] + WRITE_D || clear <= 0.01) return undefined;
    return `${t >= T[3] + READ_AT[i] ? K.ok : K.req}${alphaHex}`;
  };
  const pitch = ringCell(g, "reqRing", 1)[0] - ringCell(g, "reqRing", 0)[0];
  const pollIdx = Math.min(9, READ_AT.reduce((s, a) => s + prog(t, T[3] + a, 0.35), 0));
  const kPoll = fade(t, T[3] + 0.4, T[4] - 0.1);
  const ringB = g.r.reqRing.y + g.r.reqRing.h;

  // c0–c1 BAR4 band
  const bandOut = 1 - prog(t, T[2] - 0.1, 0.5);
  const pemC: [number, number] = [gS.c("pem")[0], gS.r.pem.y + gS.r.pem.h];
  const k15 = prog(t, T[1] + 1.5, 0.5);
  const oldOut = prog(t, T[1] + 2.4, 0.5);
  const newIn = prog(t, T[1] + 3.0, 0.9);
  const ghost = prog(t, T[1] + 5.4, 0.6);
  const box15: [number, number] = [RX0 + 15 * RPITCH + RW / 2, RY - 2];
  const reqTarget: [number, number] = [gS.r.reqRing.x + gS.r.reqRing.w * 0.85, gS.r.reqRing.y + gS.r.reqRing.h + 4];

  // c4 latency / c5 rate bars
  const latOp = fade(t, T[4] + 0.4, T[5] - 0.1);
  const rateOp = prog(t, T[5] + 0.4, 0.5);
  const sxL = (v: number) => BX0 + (v / 5) * (BX1 - BX0);
  const sxR = (v: number) => BX0 + v * 60; // 60 px per M msg/s: leaves room for the "13.7–15.9 M msg/s" label
  const kLatSdp = prog(t, T[4] + 0.8, 0.8);
  const kLatIn = prog(t, T[4] + 3.0, 0.8);
  const kRateSdp = prog(t, T[5] + 0.6, 0.8);
  const kRateIn = prog(t, T[5] + 1.2, 1.0);
  const kRange = prog(t, T[5] + 4.0, 0.7);
  const barText = { fontFamily: "Inter, sans-serif", fontWeight: 800 } as const;

  return (
    <>
      <Stage>
        <Datapath g={g} show={show} labels={g.s < SDP_SHORT_BELOW ? SDP_SHORT : {}} cores={1} activeCores={1} cellFill={{ reqRing: cellFill }} linkGlow={0.3 * kCore} />
        <Hi g={g} id="pem" color={K.card} k={kPem} />
        <Hi g={g} id="reqRing" color={K.inline} k={kReq} />
        <Hi g={g} id="core" color={K.host} k={kCore} />
        <Hi g={g} id="cores" color={K.card} k={kCores} />
        <Hi g={g} id="sdp" color={K.sdp} k={kSdp} />

        {/* ---------------- c0–c1: BAR4 windows → card addresses */}
        <g opacity={bandOut * prog(t, T[0] + 0.6, 0.5)}>
          <path d={`M${pemC[0]} ${pemC[1]} L${pemC[0]} ${RY - 16}`} stroke={K.link} strokeWidth={3} strokeDasharray="6 6" fill="none" />
          <path d={`M${RX0} ${RY - 8} L${RX0} ${RY - 16} L${AX1} ${RY - 16} L${AX1} ${RY - 8}`} stroke={K.link} strokeWidth={2.5} fill="none" />
          {TARGET.map((tx, i) => {
            const cx = RX0 + i * RPITCH + RW / 2;
            const kA = prog(t, T[0] + 5.4 + i * 0.05, 0.5);
            const k = i === 15 ? kA * (1 - oldOut) : kA;
            return <Arrow key={i} x1={cx} y1={RY + RH + 4} x2={tx} y2={AY - 4} color={C.muted} w={2} head={9} k={k} opacity={0.75} />;
          })}
          {ghost > 0 && <Arrow x1={box15[0]} y1={RY + RH + 4} x2={TARGET[15]} y2={AY - 4} color={C.muted} w={2} head={9} dash="5 7" opacity={0.6 * ghost} />}
          {Array.from({ length: 16 }, (_, i) => {
            const k = prog(t, T[0] + 1.0 + i * 0.06, 0.4);
            const on = i === 15 ? k15 : 0;
            const x = RX0 + i * RPITCH;
            return (
              <g key={i} opacity={k}>
                <rect x={x} y={RY} width={RW} height={RH} rx={8} fill={on > 0 ? `${K.inline}${Math.round(0x22 + on * 0x40).toString(16)}` : C.panel} stroke={on > 0.5 ? K.inline : K.link} strokeWidth={on > 0.5 ? 3.5 : 2} filter={on > 0.5 ? "url(#glow)" : undefined} />
                <text x={x + RW / 2} y={RY + RH / 2} textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fontWeight={700} fill={on > 0.5 ? K.inline : C.text}>
                  {i}
                </text>
              </g>
            );
          })}
          <g opacity={prog(t, T[0] + 4.6, 0.6)}>
            <rect x={AX0} y={AY} width={AX1 - AX0} height={AH} rx={8} fill="rgba(248,150,30,0.08)" stroke={K.card} strokeWidth={2} />
            <text x={AX0 + 14} y={AY + AH / 2} dominantBaseline="central" fontSize={22} fill={C.muted} fontFamily={MONO}>
              card address space
            </text>
          </g>
          <Arrow x1={376} y1={738} x2={428} y2={738} color={K.card} w={3} head={10} k={prog(t, T[0] + 5.2, 0.4)} />
          {/* c1: window 15 → the hugepage holding the request cells */}
          <Arrow x1={box15[0]} y1={box15[1]} x2={reqTarget[0]} y2={reqTarget[1]} color={K.inline} w={4} head={14} k={newIn} />
        </g>

        {/* ---------------- c2: MOVDIR64B cell writes */}
        {WRITE_AT.map((a, i) => {
          const t0 = T[2] + a;
          if (t < t0 || t > t0 + WRITE_D + 0.5) return null;
          const end = r.inline[r.inline.length - 1];
          const c = ringCell(g, "reqRing", i);
          const pts = [...r.inline, [c[0], end[1]] as [number, number], c];
          const k = prog(t, t0, WRITE_D);
          const trail = 1 - clamp01((t - t0 - WRITE_D) / 0.5);
          const [x, y] = along(pts, k);
          return (
            <g key={i}>
              <path d={pathD(partial(pts, k))} fill="none" stroke={K.req} strokeWidth={6} strokeLinecap="round" strokeLinejoin="round" opacity={0.5 * trail} />
              {k < 1 && <circle cx={x} cy={y} r={11} fill={K.req} />}
              {k < 1 && <circle cx={x} cy={y} r={22} fill={K.req} opacity={0.2} />}
            </g>
          );
        })}

        {/* ---------------- c3: card core polls the next cell */}
        {kPoll > 0.001 && (
          <g opacity={kPoll}>
            <path d={pathD(r.cardPoll)} stroke={K.card} strokeWidth={5} strokeDasharray="8 8" strokeDashoffset={-rt * 60} fill="none" opacity={0.55 + 0.45 * Math.abs(Math.sin(rt * 5))} />
            <polygon
              points={`${ringCell(g, "reqRing", 0)[0] + pollIdx * pitch},${ringB + 3} ${ringCell(g, "reqRing", 0)[0] + pollIdx * pitch - 11},${ringB + 21} ${ringCell(g, "reqRing", 0)[0] + pollIdx * pitch + 11},${ringB + 21}`}
              fill={K.card}
            />
          </g>
        )}

        {/* ---------------- c4: send inline, reply over SDP */}
        <Legs
          t={t}
          start={T[4] + 1.0}
          legs={[
            { pts: r.inline, color: K.req, d: 1.0 },
            { pts: r.sdpReply, color: K.rep, d: 1.0 },
          ]}
          opacity={fade(t, T[4] + 0.8, T[5] - 0.1)}
        />
        <PacketStream pts={r.inline} travel={(rt - (T[5] + 0.8)) * 300} gap={44} size={12} color={K.req} opacity={fade(t, T[5] + 0.8)} />
        <PacketStream pts={r.sdpReply} travel={(rt - (T[5] + 1.6)) * 300} gap={44} size={12} color={K.rep} opacity={fade(t, T[5] + 1.6)} />

        {/* latency bars */}
        {latOp > 0.001 && (
          <g opacity={latOp}>
            {[
              { label: "SDP", v: 4.85, color: K.sdp, k: kLatSdp },
              { label: "BAR4 inline + SDP replies", v: 2.41, color: K.inline, k: kLatIn },
            ].map((b, i) => (
              <g key={i} opacity={prog(t, T[4] + 0.4 + i * 1.8, 0.5)}>
                <text x={BX0 - 20} y={ROW[i] + BH / 2} textAnchor="end" dominantBaseline="central" fontSize={28} fill={b.color} {...barText}>
                  {b.label}
                </text>
                <rect x={BX0} y={ROW[i]} width={(sxL(b.v) - BX0) * b.k} height={BH} rx={8} fill={`${b.color}40`} stroke={b.color} strokeWidth={2.5} />
                {b.k > 0 && (
                  <text x={BX0 + (sxL(b.v) - BX0) * b.k + 16} y={ROW[i] + BH / 2} dominantBaseline="central" fontSize={34} fill={b.color} {...barText}>
                    {countUp(t, T[4] + (i ? 3.0 : 0.8), 0.8, b.v, 2)} µs
                  </text>
                )}
              </g>
            ))}
            <line x1={BX0} y1={AXIS_Y} x2={BX1} y2={AXIS_Y} stroke={C.dim} strokeWidth={2} />
            {[0, 1, 2, 3, 4, 5].map((v) => (
              <g key={v}>
                <line x1={sxL(v)} y1={AXIS_Y} x2={sxL(v)} y2={AXIS_Y + 8} stroke={C.dim} strokeWidth={2} />
                <text x={sxL(v)} y={AXIS_Y + 30} textAnchor="middle" fontSize={22} fill={C.muted} fontFamily={MONO}>
                  {v === 5 ? "5 µs" : v}
                </text>
              </g>
            ))}
          </g>
        )}

        {/* rate bars */}
        {rateOp > 0.001 && (
          <g opacity={rateOp}>
            <g>
              <text x={BX0 - 20} y={ROW[0] + BH / 2} textAnchor="end" dominantBaseline="central" fontSize={28} fill={K.sdp} {...barText}>
                SDP
              </text>
              <rect x={BX0} y={ROW[0]} width={(sxR(7.5) - BX0) * kRateSdp} height={BH} rx={8} fill={`${K.sdp}40`} stroke={K.sdp} strokeWidth={2.5} />
              {kRateSdp > 0 && (
                <text x={BX0 + (sxR(7.5) - BX0) * kRateSdp + 16} y={ROW[0] + BH / 2} dominantBaseline="central" fontSize={34} fill={K.sdp} {...barText}>
                  ~{countUp(t, T[5] + 0.6, 0.8, 7.5, 1)} M msg/s
                </text>
              )}
            </g>
            <g>
              <text x={BX0 - 20} y={ROW[1] + BH / 2} textAnchor="end" dominantBaseline="central" fontSize={28} fill={K.inline} {...barText}>
                BAR4 inline
              </text>
              <rect x={BX0} y={ROW[1]} width={(sxR(13.7) - BX0) * kRateIn} height={BH} rx={8} fill={`${K.inline}40`} stroke={K.inline} strokeWidth={2.5} />
              {kRange > 0 && (
                <rect x={sxR(13.7)} y={ROW[1]} width={(sxR(15.9) - sxR(13.7)) * kRange} height={BH} rx={8} fill="url(#hatch)" stroke={K.inline} strokeWidth={2.5} strokeDasharray="8 6" />
              )}
              {kRateIn > 0 && (
                <text x={lerp(BX0 + (sxR(13.7) - BX0) * kRateIn, sxR(15.9), kRange) + 16} y={ROW[1] + BH / 2} dominantBaseline="central" fontSize={34} fill={K.inline} {...barText}>
                  {kRange > 0 ? "13.7–15.9" : countUp(t, T[5] + 1.2, 1.0, 13.7, 1)} M msg/s
                </text>
              )}
              {kRange > 0 && (
                <g opacity={kRange}>
                  <path d={`M${sxR(13.7)} ${ROW[1] - 8} L${sxR(13.7)} ${ROW[1] - 16} L${sxR(15.9)} ${ROW[1] - 16} L${sxR(15.9)} ${ROW[1] - 8}`} stroke={C.muted} strokeWidth={2} fill="none" />
                  <text x={(sxR(13.7) + sxR(15.9)) / 2} y={ROW[1] - 30} textAnchor="middle" fontSize={22} fill={C.muted} fontStyle="italic" fontFamily="Inter, sans-serif">
                    varies by session
                  </text>
                </g>
              )}
            </g>
            <line x1={BX0} y1={AXIS_Y} x2={sxR(16.5)} y2={AXIS_Y} stroke={C.dim} strokeWidth={2} />
            {[0, 5, 10, 15].map((v) => (
              <g key={v}>
                <line x1={sxR(v)} y1={AXIS_Y} x2={sxR(v)} y2={AXIS_Y + 8} stroke={C.dim} strokeWidth={2} />
                <text x={sxR(v)} y={AXIS_Y + 30} textAnchor="middle" fontSize={22} fill={C.muted} fontFamily={MONO}>
                  {v === 15 ? "15 M msg/s" : v}
                </text>
              </g>
            ))}
          </g>
        )}
        <Arrow x1={1526} y1={gS.c("cores")[1]} x2={gS.r.cores.x + gS.r.cores.w + 8} y2={gS.c("cores")[1]} color={K.card} w={4} head={12} k={prog(t, T[5] + 2.2, 0.4)} />
      </Stage>

      {/* c0 labels */}
      <Txt x={RX0} y={566} size={28} weight={800} color={K.link} style={{ opacity: bandOut * prog(t, T[0] + 1.0, 0.5) }}>
        PF BAR4 <span style={{ color: C.text }}>· 16 × 4 MiB windows</span>
      </Txt>
      <div
        style={{
          position: "absolute",
          left: 110,
          top: 662,
          width: 262,
          padding: "12px 16px",
          boxSizing: "border-box",
          background: C.panel,
          border: `2px solid ${K.card}`,
          borderRadius: 14,
          opacity: bandOut * prog(t, T[0] + 5.0, 0.5),
          boxShadow: `0 0 ${24 * fade(t, T[0] + 5.2, T[0] + 6.2)}px ${K.card}`,
        }}
      >
        <div style={{ fontFamily: MONO, fontSize: 26, fontWeight: 700, color: K.card }}>BAR4_INDEX</div>
        <div style={{ fontSize: 22, color: C.muted }}>PEM register: sets each window</div>
      </div>

      {/* c1 labels */}
      <Centered x={1640} y={392} op={bandOut * prog(t, T[1] + 3.6, 0.5)}>
        <span style={{ fontSize: 28, fontWeight: 700, color: K.inline, border: `2px solid ${K.inline}`, borderRadius: 999, padding: "4px 18px" }}>4 MiB hugepage</span>
      </Centered>
      <Centered x={1640} y={448} op={bandOut * prog(t, T[1] + 5.4, 0.5)}>
        <span style={{ fontSize: 24, color: C.muted, fontStyle: "italic" }}>restored on exit</span>
      </Centered>

      {/* c2 / c3 captions under the large datapath */}
      <Centered x={960} y={712} op={fade(t, T[2] + 1.6, T[3] - 0.1)} dy={(1 - prog(t, T[2] + 1.6, 0.5)) * 20}>
        <span style={{ fontSize: 36, fontWeight: 700, color: C.text, display: "inline-flex", alignItems: "center", gap: 14 }}>
          <span style={{ fontFamily: MONO, color: K.inline }}>MOVDIR64B</span>
          <span style={{ color: C.muted }}>·</span> one 64-byte store <span style={{ color: C.muted }}>·</span>
          <svg width={46} height={46}>
            <Bell x={23} y={25} s={1.1} color={C.muted} />
            <line x1={6} y1={42} x2={40} y2={6} stroke={K.bad} strokeWidth={4} strokeLinecap="round" />
          </svg>
          no doorbell
        </span>
      </Centered>
      <Centered x={960} y={712} op={fade(t, T[3] + 0.5, T[4] - 0.1)} dy={(1 - prog(t, T[3] + 0.5, 0.5)) * 20}>
        <span style={{ fontSize: 36, fontWeight: 700, color: C.text }}>
          <span style={{ color: K.card }}>card core polls</span> the next cell
        </span>
      </Centered>
      <Centered x={960} y={784} op={fade(t, T[3] + 4.4, T[4] - 0.1)} dy={(1 - prog(t, T[3] + 4.4, 0.5)) * 20}>
        <span style={{ fontSize: 30, color: C.text }}>
          hugepage sees PCIe writes <span style={{ color: K.ok, fontWeight: 700 }}>coherently</span> · nothing to flush
        </span>
      </Centered>

      {/* c4 / c5 chart titles */}
      <Txt x={140} y={580} size={26} color={C.muted} style={{ opacity: latOp }}>
        W=1 round trip (p50)
      </Txt>
      <Txt x={140} y={580} size={26} color={C.muted} style={{ opacity: rateOp }}>
        message rate · 1 card core · every payload checked
      </Txt>
      <Centered x={1690} y={gS.c("cores")[1] - 38} op={prog(t, T[5] + 2.0, 0.5)}>
        <div style={{ fontSize: 28, fontWeight: 800, color: K.card, textAlign: "center", lineHeight: 1.25 }}>
          one card core
          <br />
          is the limit
        </div>
      </Centered>
    </>
  );
}

export const S04: SceneDef = {
  id: "S04",
  title: "Send path: write into card memory",
  stage: 1,
  source: "inline_pingpong (host) · inline_reflector (card) · BAR4",
  dur: DUR,
  cues,
  C: Body,
};
