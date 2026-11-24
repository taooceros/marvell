import React from "react";
import { Cue, FONT, SceneDef, lerp, prog, useT } from "../lib";
import { Cell, P, Stage, Text, alpha, win } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 10.30, "**CC-NIC does not need cache coherence**: how should a host CPU and its network card talk?", "This is the story behind our paper, C C nick does not need cache coherence. It is about one question: how should a host C P U and its network card talk to each other?"], // 0
  [10.65, 19.69, "A recent line of work says fast NICs must join the CPU's **cache-coherence domain**. We argue the opposite.", "A recent line of work argues that fast network cards must join the C P U's cache coherence domain, over links like C X L. We argue the opposite."], // 1
  [20.04, 31.14, "The speedup comes from **how data is handed over**, not from coherence. Plain PCIe can already do that hand-over.", "The speedup comes from how data is handed from one side to the other, not from coherence itself. And plain P C I express, the link inside every server today, can already do that hand-over."], // 2
  [31.49, 42.29, "Everything is measured on real hardware: a Xeon host and a **Marvell CN106** NIC on PCIe Gen5, IOMMU on.", "Everything here is measured on real hardware: an Intel Xeon host and a Marvell C N one oh six network card on P C I express gen five, with the I O M M U turned on."], // 3
  [42.64, 52.34, "The plan: the debate → why coherence is **not needed** → why it is **not free** → what real hardware adds → end to end.", "Here is the plan. First, the debate. Then why coherence is not needed, and why it is not free. Then what real hardware taught us, and finally the end-to-end result."], // 4
];
const DUR = 53.74; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

/** Small host/NIC pair used by the two "worldview" panels (SVG). */
function MiniPair({ x, y, mode, t, t0, opacity }: { x: number; y: number; mode: "coh" | "push"; t: number; t0: number; opacity: number }) {
  const hostX = x;
  const nicX = x + 470;
  const boxW = 250;
  const boxH = 230;
  const cacheY = y + 108;
  const cacheH = 64;
  const linkY = cacheY + cacheH / 2;
  const coh = mode === "coh";
  const col = coh ? P.coh : P.push;
  const hostCacheR: [number, number] = [hostX + boxW - 30, linkY];
  const nicCacheL: [number, number] = [nicX + 30, linkY];
  // coherent: lines bounce both ways on demand; push: one-way cells on two lanes
  const cells = [0, 1, 2, 3].map((i) => {
    const per = coh ? 1.7 : 1.3;
    const ph = (t - t0 - i * per * 0.5) / per;
    return ph < 0 ? -1 : ph - Math.floor(ph);
  });
  return (
    <g opacity={opacity} fontFamily={FONT}>
      {[hostX, nicX].map((bx, i) => (
        <g key={bx}>
          <rect x={bx} y={y} width={boxW} height={boxH} rx={18} fill="rgba(255,255,255,0.022)" stroke={alpha(i ? P.nic : P.host, 0.45)} strokeWidth={1.5} />
          <text x={bx + 20} y={y + 32} fontSize={16} letterSpacing={2.4} fontWeight={600} fill={i ? P.nic : P.host}>
            {i ? "NIC" : "HOST"}
          </text>
          <rect x={bx + 24} y={cacheY} width={boxW - 48} height={cacheH} rx={12} fill={alpha(col, 0.1)} stroke={alpha(col, 0.7)} strokeWidth={1.5} />
          <text x={bx + boxW / 2} y={cacheY + cacheH / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={22} fill={P.ink}>
            cache
          </text>
        </g>
      ))}
      {coh ? (
        <g>
          <rect x={hostX + boxW} y={linkY - 16} width={nicX - hostX - boxW} height={32} fill={alpha(P.coh, 0.16)} stroke={alpha(P.coh, 0.55)} strokeWidth={1.2} />
          <rect x={hostX + 8} y={cacheY - 16} width={nicX + boxW - hostX - 16} height={cacheH + 32} rx={22} fill="none" stroke={P.coh} strokeWidth={2} strokeDasharray="10 8" />
          {cells.map((k, i) =>
            k < 0 ? null : (
              <Cell key={i} pts={i % 2 ? [nicCacheL, hostCacheR] : [hostCacheR, nicCacheL]} k={0.5 - 0.5 * Math.cos(k * Math.PI * 2)} color={P.coh} w={26} h={18} />
            ),
          )}
        </g>
      ) : (
        <g>
          {[-14, -5, 5, 14].map((o) => (
            <line key={o} x1={hostX + boxW} y1={linkY + o} x2={nicX} y2={linkY + o} stroke={P.link} strokeWidth={2} />
          ))}
          {cells.map((k, i) =>
            k < 0 ? null : (
              <Cell
                key={i}
                pts={i % 2 ? [[nicX + 60, linkY + 10], [hostX + boxW - 50, linkY + 10]] : [[hostX + boxW - 50, linkY - 10], [nicX + 60, linkY - 10]]}
                k={k}
                color={P.push}
                w={26}
                h={18}
              />
            ),
          )}
        </g>
      )}
    </g>
  );
}

const ROADMAP: [string, string][] = [
  ["The debate", "Why PCIe NICs feel slow, and CC-NIC's bet"],
  ["Not needed", "Push each message into the reader's cache"],
  ["Not free", "What coherence costs that push avoids"],
  ["Real hardware", "Credits, NIC DMA, NIC-held bursts"],
  ["End to end", "A real 100GbE link, one host core"],
  ["Takeaway", "And what coherence is still for"],
];
const ROAD_COL = [P.coh, P.push, P.gold, P.nic, P.host, P.ink];

function Body() {
  const t = useT();
  // title: centred hero until T1, then a quiet header
  const up = prog(t, T[1] - 0.2, 1.0);
  const titleIn = prog(t, T[0] + 0.1, 1.2);
  const titleY = lerp(330, 150, up);
  const titleSize = lerp(86, 46, up);
  const rule = prog(t, T[0] + 0.9, 1.4);
  const sub = prog(t, T[0] + 2.0, 0.8) * (1 - up);

  const views = win(t, T[1] + 0.4, T[3] - 0.1, 0.7);
  const ours = prog(t, T[1] + 4.6, 0.8);
  const thesis = prog(t, T[2] + 0.6, 0.8);
  const hw = win(t, T[3] + 0.3, T[4] - 0.1, 0.7);
  const road = prog(t, T[4] + 0.3, 0.7);

  return (
    <>
      {/* ---------------- title */}
      <div style={{ position: "absolute", left: 0, right: 0, top: titleY, textAlign: "center", opacity: titleIn, fontFamily: FONT }}>
        <div style={{ fontSize: lerp(18, 15, up), letterSpacing: 5, color: P.sub, fontWeight: 600 }}>THE STORY BEHIND OUR PAPER</div>
        <div style={{ fontSize: titleSize, fontWeight: 800, color: P.ink, letterSpacing: -1.2, marginTop: lerp(22, 10, up), lineHeight: 1.08 }}>
          CC-NIC does not need <span style={{ color: P.coh }}>cache coherence</span>
        </div>
        <div style={{ margin: `${lerp(30, 16, up)}px auto 0`, height: 2, width: 560 * rule, background: `linear-gradient(90deg, transparent, ${alpha(P.coh, 0.8)}, transparent)` }} />
        <div style={{ fontSize: 32, color: P.sub, marginTop: 26, opacity: sub }}>How should a host CPU and its network card talk to each other?</div>
      </div>

      {/* ---------------- two worldviews */}
      <Stage opacity={views}>
        <MiniPair x={150} y={430} mode="coh" t={t} t0={T[1] + 0.8} opacity={1} />
        <MiniPair x={1050} y={430} mode="push" t={t} t0={T[1] + 4.8} opacity={ours} />
        <line x1={960} y1={400} x2={960} y2={760} stroke={P.hair2} strokeWidth={1.5} opacity={ours} />
      </Stage>
      <Text x={150} y={360} size={17} color={P.coh} opacity={views} style={{ letterSpacing: 2.8, fontWeight: 600 }}>
        COHERENT NICs · CC-NIC '24, CXL-NIC '25
      </Text>
      <Text x={150} y={690} w={720} size={26} opacity={views}>
        Join the CPU's coherence domain: <span style={{ color: P.coh }}>share cache lines</span>, moved on demand
      </Text>
      <Text x={1050} y={360} size={17} color={P.push} opacity={views * ours} style={{ letterSpacing: 2.8, fontWeight: 600 }}>
        OUR VIEW · PLAIN PCIe
      </Text>
      <Text x={1050} y={690} w={740} size={26} opacity={views * ours}>
        <span style={{ color: P.push }}>Hand each message over</span>: push it straight into the reader's cache
      </Text>
      <Text x={0} y={792} w={1920} align="center" size={30} weight={600} opacity={views * thesis}>
        The speedup is in the <span style={{ color: P.gold }}>hand-over</span>, not in coherence.
      </Text>

      {/* ---------------- real hardware */}
      <Stage opacity={hw}>
        <g fontFamily={FONT}>
          {[
            { x: 170, w: 420, col: P.host, kick: "HOST", name: "Intel Xeon", sub: "Sapphire Rapids · 'saturn'" },
            { x: 750, w: 420, col: P.nic, kick: "NIC", name: "Marvell CN106", sub: "OCTEON 10 DPU" },
            { x: 1330, w: 420, col: P.sub, kick: "PEER (CH. 10)", name: "Second server", sub: "ConnectX-5 · 'mercury'" },
          ].map((d, i) => (
            <g key={d.kick} opacity={i === 2 ? 0.75 * prog(t, T[3] + 3.2, 0.7) : prog(t, T[3] + 0.4 + i * 0.6, 0.7)}>
              <rect x={d.x} y={420} width={d.w} height={190} rx={20} fill="rgba(255,255,255,0.025)" stroke={alpha(d.col, 0.55)} strokeWidth={1.5} />
              <text x={d.x + 28} y={462} fontSize={16} letterSpacing={2.6} fontWeight={600} fill={d.col}>
                {d.kick}
              </text>
              <text x={d.x + 28} y={524} fontSize={38} fontWeight={600} fill={P.ink}>
                {d.name}
              </text>
              <text x={d.x + 28} y={570} fontSize={22} fill={P.sub}>
                {d.sub}
              </text>
            </g>
          ))}
          <g opacity={prog(t, T[3] + 1.0, 0.7)}>
            {[-9, -3, 3, 9].map((o) => (
              <line key={o} x1={590} y1={515 + o} x2={750} y2={515 + o} stroke={P.push} strokeWidth={2} opacity={0.7} />
            ))}
            <text x={670} y={665} textAnchor="middle" fontSize={22} fill={P.ink}>
              PCIe Gen5 ×4
            </text>
            <text x={670} y={696} textAnchor="middle" fontSize={19} fill={P.sub}>
              IOMMU on
            </text>
          </g>
          <g opacity={0.75 * prog(t, T[3] + 3.2, 0.7)}>
            <line x1={1170} y1={515} x2={1330} y2={515} stroke={P.sub} strokeWidth={3} strokeDasharray="2 7" strokeLinecap="round" />
            <text x={1250} y={665} textAnchor="middle" fontSize={22} fill={P.ink}>
              100GbE
            </text>
            <text x={1250} y={696} textAnchor="middle" fontSize={19} fill={P.sub}>
              3 m cable
            </text>
          </g>
        </g>
      </Stage>
      <Text x={0} y={330} w={1920} align="center" size={17} color={P.sub} opacity={hw} style={{ letterSpacing: 3, fontWeight: 600 }}>
        EVERY NUMBER IN THIS VIDEO IS MEASURED ON THIS HARDWARE, UNLESS MARKED AS FROM A PAPER
      </Text>

      {/* ---------------- roadmap */}
      <Stage opacity={road}>
        <line x1={250} y1={500} x2={1670} y2={500} stroke={P.hair2} strokeWidth={1.5} />
        {ROADMAP.map((_, i) => {
          const k = prog(t, T[4] + 0.5 + i * 1.05, 0.6);
          const x = 250 + i * 284;
          return (
            <g key={i} opacity={0.25 + 0.75 * k}>
              <circle cx={x} cy={500} r={26} fill={P.bg0} stroke={ROAD_COL[i]} strokeWidth={2} />
              <text x={x} y={501} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={700} fill={ROAD_COL[i]} fontFamily={FONT}>
                {i + 1}
              </text>
            </g>
          );
        })}
      </Stage>
      {ROADMAP.map(([name, line], i) => {
        const k = prog(t, T[4] + 0.5 + i * 1.05, 0.6);
        return (
          <div key={name} style={{ position: "absolute", left: 250 + i * 284 - 130, top: 556, width: 260, textAlign: "center", fontFamily: FONT, opacity: road * (0.25 + 0.75 * k) }}>
            <div style={{ fontSize: 26, fontWeight: 600, color: P.ink }}>{name}</div>
            <div style={{ fontSize: 19, color: P.sub, marginTop: 10, lineHeight: 1.35 }}>{line}</div>
          </div>
        );
      })}
    </>
  );
}

export const C01: SceneDef = {
  id: "C01",
  title: "",
  source: "",
  dur: DUR,
  cues,
  C: Body,
};
