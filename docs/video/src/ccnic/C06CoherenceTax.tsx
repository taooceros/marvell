import React from "react";
import { Cue, Pt, SceneDef, along, lerp, prog, useT } from "../lib";
import { BigStat, Cell, Flow, Glass, HostNic, P, Stage, TAB, Tag, Text, alpha, hnGeom, hnRoutes, win } from "./kit";
import { Shield, TagC, XMark, landOp, phase, stream } from "./_CcSceneCost";

// <cues>
const cues: Cue[] = [
  [0.60, 6.22, "Coherence isn't only unnecessary here. It also carries a **cost** that push simply avoids.", "Coherence is not only unnecessary here. It also carries a cost that push simply avoids."], // 0
  [6.57, 16.65, "A coherent NIC caches host buffers it has read. When the host reuses one, each copy must be **invalidated over the link**.", "A coherent card keeps copies of the host buffers it has read in its own cache. When the host writes to those buffers again, each stale copy must first be invalidated across the link."], // 1
  [17.00, 27.32, "Real stacks keep sent buffers: TCP holds each one until it is acked. CC-NIC measured **−20%** throughput from that alone.", "Real network stacks keep sent buffers around. T C P, for example, holds every sent packet until it is acknowledged. C C nick measured a twenty percent throughput drop from that alone."], // 2
  [27.67, 34.75, "With push, data arrives **by value**: the NIC never caches host memory, so there is nothing to invalidate.", "With push, data arrives by value. The card never caches host memory, so there is never anything to invalidate."], // 3
  [35.10, 48.92, "It frees the sender too: a pushed buffer can be **reused at once**. One core rewriting one 64 B buffer: **174.8 M/s**.", "Push frees the sender, too. Since the card never reads a buffer back, the C P U can reuse it the moment it is pushed. One core rewriting a single sixty-four byte buffer still pushes one hundred seventy-four point eight million messages per second."], // 4
  [49.27, 60.23, "We grew the retained pool **2,000×**, from 64 KiB to 128 MiB, past the 60 MiB host cache. Throughput: **−0.9%**.", "We grew the retained buffer pool two thousand times, from sixty-four kilobytes to one hundred twenty-eight megabytes, larger than the entire host cache. Throughput moved by less than one percent."], // 5
  [60.58, 75.22, "Isolation stays too: every run kept the **IOMMU on**. CC-NIC leaves it to future work; CXL devices bypass it.", "Isolation stays, too. Every run here kept the I O M M U on. C C nick leaves protection to future work, and the C X L nick paper notes that C X L devices are not protected by the I O M M U the way P C I express devices are."], // 6
];
const DUR = 76.62; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
const G = hnGeom(1);
const RT = hnRoutes(G);
const MINUS = "\u2212";

/** Retained sent-buffer pool inside host memory: 2 rows × 10 buffers. */
const SQ = 26;
const POOL: Pt[] = Array.from({ length: 20 }, (_, i): Pt => [258 + 36 * (i % 10), 657 + 36 * Math.floor(i / 10)]);
/** The pool buffers a coherent NIC has read (cached), and where their copies sit in the NIC cache. */
const COPIED = [7, 8, 9];
const COPY: Pt[] = [
  [1312, 466],
  [1312, 500],
  [1312, 534],
];
/** Pool → host right margin → link centre → NIC margin → copy j (reads and invalidations ride these). */
const TO_COPY: Pt[][] = COPY.map(([, y]): Pt[] => [
  [600, 657],
  [675, 657],
  [675, 500],
  [1252, 500],
  [1252, y],
  [1294, y],
]);
/** b4: one 64 B buffer at the core's exit, rewritten and pushed again and again. */
const BUF: Pt = [443, 348];
const BUF_ROUTE: Pt[] = [
  [458, 348],
  [665, 348],
  [665, 486],
  [1335, 486],
];
const REUSE_PER = 0.45;
/** b6: the IOMMU sits on the host side of the link. */
const SHIELD: Pt = [758, 500];

/** b5 chart: pool bar drawn linear in bytes, and a throughput bar that does not move. */
const BX = 160;
const BW = 1440;
const BH = 44;
const POOL_Y = 426;
const TP_Y = 656;
const CACHE_X = BX + (BW * 60) / 128;
const TP_W = (BW * 47.5) / 100;

/** One coherent round from c0: the NIC reads (caches) three buffers, the host rewrites them, each copy is invalidated. */
function cohRound(t: number, c0: number) {
  return {
    readK: COPY.map((_, j) => (t - (c0 + j * 0.45)) / 1.5),
    copyK: COPY.map((_, j) => prog(t, c0 + j * 0.45 + 1.45, 0.3)),
    write: prog(t, c0 + 3.6, 0.9),
    writeOp: 1 - prog(t, c0 + 5.6, 0.5),
    flash: win(t, c0 + 4.45, c0 + 5.1, 0.15),
    pulseK: COPY.map((_, j) => (t - (c0 + 4.7 + j * 0.3)) / 1.4),
    xK: COPY.map((_, j) => prog(t, c0 + 6.05 + j * 0.3, 0.25)),
    talk: win(t, c0 + 4.6, c0 + 7.0, 0.4),
    core: win(t, c0 + 3.5, c0 + 4.7, 0.3),
  };
}

function Body() {
  const t = useT();
  // ---------------------------------------------------------------- visibility: diagram (b0–b4, b6) and chart (b5)
  const diag = Math.max(win(t, T[0] + 0.1, T[5] + 0.1, 0.8), prog(t, T[6] + 0.3, 0.8));
  const chart = win(t, T[5] + 0.6, T[6] + 0.1, 0.7);
  const cohK = 1 - prog(t, T[3] + 0.2, 1.2);
  const poolDim = prog(t, T[4] + 0.2, 0.6);

  // ---------------------------------------------------------------- b1–b2: the NIC caches copies, the host rewrites, copies are invalidated
  const c1 = T[1] + 0.3;
  const c2 = T[2] + 1.4;
  const R = [cohRound(t, c1), cohRound(t, c2)];
  const gone = [prog(t, c2 - 0.3, 0.5), prog(t, T[3] + 0.1, 0.7)];
  const copyOp = (r: number, j: number) => R[r].copyK[j] * (1 - gone[r]);
  const anyCopy = Math.max(...COPY.map((_, j) => Math.max(copyOp(0, j), copyOp(1, j))));
  const flash = Math.max(R[0].flash, R[1].flash);
  const talk = Math.max(R[0].talk, R[1].talk);
  const coreWrite = Math.max(R[0].core, R[1].core);

  // ---------------------------------------------------------------- b3: data pushed by value; b4: one buffer reused at once
  const pushes = stream(t, T[3] + 1.0, T[4] + 0.5, 0.85, 1.3);
  const reuseOn = prog(t, T[4] + 0.3, 0.5);
  const reuses = stream(t, T[4] + 0.8, T[5] + 0.5, REUSE_PER, 1.0);
  const bufFlash = t >= T[4] + 0.6 && t < T[5] + 0.5 ? Math.max(0, 1 - phase(t, T[4] + 0.6, REUSE_PER) / 0.3) : 0;
  const coreBusy = prog(t, T[4] + 0.3, 0.6) * (1 - prog(t, T[5], 0.5));

  // ---------------------------------------------------------------- b5: the retained pool grows 2,000×, throughput stays flat
  const growStart = T[5] + 1.8;
  const grow = prog(t, growStart, 4.4);
  const poolW = Math.max(6, BW * lerp(64 / 131072, 1, grow));
  const swap = T[5] + 6.9;
  const tpW = TP_W * (t < swap ? 1 : 47.1 / 47.5);

  // ---------------------------------------------------------------- b6: replies cross the IOMMU on the host side
  const replies = stream(t, T[6] + 1.4, 1e9, 1.1, 1.4);
  const shieldOn = prog(t, T[6] + 1.0, 0.6);
  const shieldGlow = Math.max(
    0,
    ...replies.map(({ k }) => 1 - Math.abs(along(RT.pushN2H, k)[0] - SHIELD[0]) / 50),
  );

  return (
    <>
      <Stage>
        <g opacity={diag}>
          <HostNic
            g={G}
            coh={cohK}
            labels={{ hostMem: "" }}
            dim={{ nicMem: 0.75, hostMem: 0.6 * poolDim }}
            hl={{
              hostMem: 0.55 * prog(t, T[0] + 1.0, 0.6) * (1 - poolDim),
              nicCache: Math.max(0.7 * anyCopy, 0.6 * win(t, T[3] + 1.8, T[5], 0.6)),
              hostCore: Math.max(coreWrite, coreBusy),
            }}
            hlColor={{ nicCache: t < T[3] + 1.0 ? P.coh : P.push }}
          />
          {/* the retained pool of sent buffers (lavender ring = the NIC holds a copy) */}
          {POOL.map(([x, y], i) => {
            const j = COPIED.indexOf(i);
            const shared = j >= 0 ? Math.max(copyOp(0, j) * (1 - R[0].xK[j]), copyOp(1, j) * (1 - R[1].xK[j])) : 0;
            const fl = j >= 0 ? flash : 0;
            return (
              <g key={`s${i}`} opacity={prog(t, T[0] + 1.0 + i * 0.05, 0.4) * (1 - 0.6 * poolDim)}>
                <rect x={x - SQ / 2} y={y - SQ / 2} width={SQ} height={SQ} rx={5} fill={alpha(P.host, 0.24)} stroke={alpha(P.host, 0.8)} strokeWidth={1.5} />
                {fl > 0 && <rect x={x - SQ / 2} y={y - SQ / 2} width={SQ} height={SQ} rx={5} fill="#ffffff" opacity={0.6 * fl} />}
                {shared > 0 && (
                  <rect x={x - SQ / 2 - 4} y={y - SQ / 2 - 4} width={SQ + 8} height={SQ + 8} rx={8} fill="none" stroke={P.coh} strokeWidth={2} opacity={shared} />
                )}
              </g>
            );
          })}
          {/* the host core rewrites its buffers */}
          {R.map((r, n) => (
            <Flow key={`w${n}`} pts={RT.hostToMem} k={r.write} color={P.host} w={2.5} head={11} opacity={r.writeOp} />
          ))}
          {/* the coherent NIC reads buffers and keeps copies */}
          {R.map((r, n) =>
            r.readK.map((k, j) => <Cell key={`r${n}${j}`} pts={TO_COPY[j]} k={k} color={P.coh} w={28} h={20} opacity={landOp(k, 0.06)} />),
          )}
          {[0, 1].map((r) =>
            COPY.map(([x, y], j) => {
              const op = copyOp(r, j);
              if (op <= 0) return null;
              const xk = R[r].xK[j];
              return (
                <g key={`c${r}${j}`} opacity={op}>
                  <rect x={x - 14} y={y - 14} width={28} height={28} rx={5} fill={alpha(P.coh, 0.85 - 0.6 * xk)} stroke={P.coh} strokeWidth={1.5} />
                  <XMark x={x} y={y} r={11} k={xk} />
                </g>
              );
            }),
          )}
          {/* invalidations cross the link, one per stale copy */}
          {R.map((r, n) =>
            r.pulseK.map((k, j) => {
              if (k < 0 || k > 1) return null;
              const [x, y] = along(TO_COPY[j], k);
              return (
                <g key={`p${n}${j}`}>
                  <circle cx={x} cy={y} r={16} fill={alpha(P.bad, 0.3)} filter="url(#ccSoft)" />
                  <circle cx={x} cy={y} r={8} fill={P.bad} />
                </g>
              );
            }),
          )}
          {/* b3: pushed by value */}
          {pushes.map(({ i, k }) => (
            <Cell key={`b${i}`} pts={RT.pushH2N} k={k} color={P.push} opacity={landOp(k)} />
          ))}
          {/* b4: one buffer, rewritten and pushed again and again */}
          <g opacity={reuseOn * (1 - prog(t, T[5], 0.5))}>
            <circle cx={BUF[0]} cy={BUF[1]} r={18 + 14 * (1 - bufFlash)} fill="none" stroke={P.push} strokeWidth={2} opacity={0.6 * bufFlash} />
            <rect x={BUF[0] - 13} y={BUF[1] - 13} width={26} height={26} rx={5} fill={alpha(P.host, 0.35)} stroke={P.host} strokeWidth={2} />
            <rect x={BUF[0] - 13} y={BUF[1] - 13} width={26} height={26} rx={5} fill="#ffffff" opacity={0.65 * bufFlash} />
          </g>
          {reuses.map(({ i, k }) => (
            <Cell key={`u${i}`} pts={BUF_ROUTE} k={k} color={P.push} opacity={landOp(k)} />
          ))}
          {/* b6: NIC→host writes pass the IOMMU */}
          {replies.map(({ i, k }) => (
            <Cell key={`y${i}`} pts={RT.pushN2H} k={k} color={P.push} opacity={landOp(k)} />
          ))}
          <Shield x={SHIELD[0]} y={SHIELD[1]} h={36} k={shieldOn} glow={shieldGlow} />
        </g>

        {/* ---- b5 chart */}
        <g opacity={chart}>
          <rect x={BX} y={POOL_Y} width={BW} height={BH} rx={BH / 2} fill="rgba(255,255,255,0.035)" />
          <rect x={BX} y={POOL_Y} width={poolW} height={BH} rx={Math.min(BH / 2, poolW / 2)} fill={P.host} opacity={0.9} />
          <line x1={CACHE_X} y1={POOL_Y - 26} x2={CACHE_X} y2={POOL_Y + BH + 16} stroke={P.ink} strokeWidth={2} strokeDasharray="6 6" opacity={0.75} />
          <rect x={BX} y={TP_Y} width={tpW} height={BH} rx={BH / 2} fill={P.push} opacity={0.92} />
        </g>
      </Stage>

      {/* ---- diagram overlays */}
      <Text x={G.hostMem.x} y={742} w={G.hostMem.w} align="center" size={22} color={P.host} opacity={diag * prog(t, T[0] + 2.0, 0.6) * (1 - 0.6 * poolDim)}>
        sent buffers
      </Text>
      <Text x={G.host.x} y={806} w={G.host.w} align="center" size={24} color={P.ink} opacity={diag * win(t, T[2] + 0.6, T[4], 0.6)}>
        TCP keeps them until acked
      </Text>
      <Text x={810} y={538} w={300} align="center" size={22} weight={600} color={P.bad} opacity={diag * talk}>
        invalidate
      </Text>
      <BigStat
        x={960}
        y={196}
        w={540}
        align="center"
        value={`${MINUS}20%`}
        size={88}
        color={P.bad}
        label="throughput, from retaining sent buffers alone"
        opacity={diag * win(t, T[2] + 6.4, T[3] + 0.1, 0.7)}
        dy={(1 - prog(t, T[2] + 6.4, 0.7)) * 14}
      />
      <TagC x={960} y={376} color={P.coh} opacity={diag * win(t, T[2] + 6.9, T[3] + 0.1, 0.6)}>
        CC-NIC Fig. 15
      </TagC>
      <TagC x={960} y={548} color={P.push} opacity={diag * win(t, T[3] + 1.6, T[4] + 0.3, 0.5)}>
        pushed by value
      </TagC>
      <Tag x={1290} y={572} color={P.push} opacity={diag * win(t, T[3] + 2.6, T[4] + 0.3, 0.5)}>
        nothing to invalidate
      </Tag>
      <Text x={472} y={296} size={22} color={P.push} opacity={diag * win(t, T[4] + 1.4, T[4] + 7.3, 0.5)}>
        reused at once
      </Text>
      <BigStat
        x={960}
        y={210}
        w={620}
        align="center"
        value="174.8"
        unit="M/s"
        size={84}
        color={P.push}
        label="one core, one 64 B buffer, reused"
        opacity={diag * win(t, T[4] + 7.5, T[5] + 0.1, 0.7)}
        dy={(1 - prog(t, T[4] + 7.5, 0.7)) * 14}
      />
      <TagC x={SHIELD[0]} y={424} color={P.push} solid opacity={diag * shieldOn}>
        IOMMU on
      </TagC>
      <Glass x={710} y={598} w={500} h={148} kicker="Coherent designs" color={P.coh} opacity={diag * prog(t, T[6] + 4.0, 0.7)}>
        <div style={{ fontSize: 22, color: P.ink, lineHeight: 1.45 }}>
          <div>CC-NIC: left to future work</div>
          <div style={{ opacity: prog(t, T[6] + 7.5, 0.7) }}>CXL devices: not covered by the IOMMU</div>
        </div>
      </Glass>
      <Tag x={96} y={160} opacity={diag}>
        schematic
      </Tag>

      {/* ---- b5 chart overlays */}
      <Text x={BX} y={250} size={17} color={P.sub} opacity={chart} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        CLOSED LOOP · 64 B MESSAGES
      </Text>
      <Text x={BX} y={POOL_Y - 62} size={26} color={P.ink} opacity={chart}>
        retained buffer pool
      </Text>
      <Text x={CACHE_X - 160} y={POOL_Y - 62} w={320} align="center" size={22} color={P.sub} opacity={chart}>
        host cache <span style={{ color: P.ink, fontWeight: 600 }}>60 MiB</span>
      </Text>
      <Text x={BX} y={POOL_Y + BH + 12} size={22} color={P.sub} opacity={chart} style={TAB}>
        64 KiB
      </Text>
      <Text x={BX + BW + 18} y={POOL_Y + 4} size={30} weight={700} color={P.host} opacity={chart * prog(t, growStart + 4.2, 0.4)} style={TAB}>
        128 MiB
      </Text>
      <Text x={1080} y={POOL_Y - 70} w={BX + BW - 1080} align="right" size={26} color={P.ink} opacity={chart * prog(t, growStart + 4.6, 0.6)}>
        <span style={{ fontSize: 36, fontWeight: 800, color: P.gold, ...TAB }}>2,000×</span> larger pool
      </Text>
      <Text x={BX} y={TP_Y - 62} size={26} color={P.ink} opacity={chart}>
        throughput
      </Text>
      <Text x={BX + TP_W + 18} y={TP_Y + 4} size={30} weight={700} color={P.push} opacity={chart * (1 - prog(t, swap, 0.4))} style={TAB}>
        47.5 M/s
      </Text>
      <Text x={BX + TP_W + 18} y={TP_Y + 4} size={30} weight={700} color={P.push} opacity={chart * prog(t, swap + 0.3, 0.4)} style={TAB}>
        47.1 M/s
      </Text>
      <BigStat
        x={1400}
        y={TP_Y - 44}
        w={440}
        align="center"
        value={`${MINUS}0.9%`}
        size={96}
        color={P.push}
        label="throughput change"
        opacity={chart * prog(t, T[5] + 7.6, 0.7)}
        dy={(1 - prog(t, T[5] + 7.6, 0.7)) * 14}
      />
    </>
  );
}

export const C06: SceneDef = {
  id: "C06",
  title: "Coherence is not free",
  stage: 2,
  source: "CC-NIC Fig. 15 \u00b7 measured: closed loop, 64 B, pool 64 KiB \u2192 128 MiB",
  dur: DUR,
  cues,
  C: Body,
};
