import React from "react";
import { Cue, FONT, Pt, SceneDef, lerp, prog, useT } from "../lib";
import { BigStat, Cell, CellAt, Flow, HostNic, P, Stage, Tag, Text, alpha, btm, hnGeom, hnRoutes, rgt, rise, win } from "./kit";
import { BoxTitle, LinkText, PartText, tagW } from "./_CcSceneDebate";

// <cues>
const cues: Cue[] = [
  [0.60, 8.22, "**CC-NIC** (ASPLOS '24) proposed a fix: move the NIC inside the CPU's **cache-coherence domain**.", "In twenty twenty four, the C C nick paper proposed a fix: move the network card inside the C P U's cache coherence domain."], // 0
  [8.57, 18.91, "Host and NIC then share cache lines, and coherence hardware moves each line to whoever touches it next.", "Host and card would then share cache lines, and the coherence hardware moves each line to whichever side touches it next. Just loads and stores: no doorbells, and no D M A."], // 1
  [19.26, 32.20, "Modeled on a two-socket Xeon, one socket playing the NIC: **490–650 ns**, about **77% lower** than PCIe NICs.", "They modeled it on a two-socket Xeon server, with one socket playing the network card, and measured four hundred ninety to six hundred fifty nanoseconds, about seventy-seven percent lower than P C I express network cards."], // 2
  [32.55, 43.93, "Yet in their own control, the **old interface over the coherent link** moved fewer packets than plain PCIe.", "But one of their own experiments is telling. When they ran the old network card interface over the coherent link, unchanged, it delivered a lower packet rate than the same card on plain P C I express."], // 3
  [44.28, 53.50, "The win came with a **redesigned interface**: signals packed into the descriptors, rings placed with whoever writes them.", "In other words, the gain arrived together with a redesigned interface: signals packed into the descriptors themselves, and rings placed in the memory of whoever writes them."], // 4
  [53.85, 67.65, "A real coherent NIC (**CXL-NIC**, MICRO '25) was fastest using **mostly non-coherent pushes**: about **1.3 µs**.", "And when a later paper built a real coherent network card on a C X L F P G A, its fastest design relied mostly on non-coherent requests, pushing packets straight into the host cache, at about one point three microseconds."], // 5
  [68.00, 77.44, "So the real question: does the speedup come from **coherence**, or from **how data is handed over**?", "So the real question is this. Does the speedup come from coherence, or from redesigning how data is handed over? If it is the hand-over, we may not need coherence at all."], // 6
];
const DUR = 78.84; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

/** Same layout as C02: the running diagram at s = 1, nudged left. */
const G = hnGeom(1, -60, 0);
const RT = hnRoutes(G);
const GAP_X = G.at(960, 0)[0];

// ---- b1–b2: two cache lines move to whichever side touches them next (each keeps its own height on the link)
const LINE_Y = [G.at(0, 488)[1], G.at(0, 512)[1]];
const SIDE_X = { host: G.at(585, 0)[0], nic: G.at(1335, 0)[0] };
type Side = keyof typeof SIDE_X;
const START: Side[] = ["host", "nic"];
const MOVE_D = 1.1;
const MOVES: { t: number; line: number; to: Side }[] = [
  { t: T[1] + 1.0, line: 0, to: "nic" },
  { t: T[1] + 3.2, line: 1, to: "host" },
  { t: T[1] + 5.4, line: 0, to: "host" },
  { t: T[1] + 7.4, line: 1, to: "nic" },
  { t: T[2] + 1.6, line: 0, to: "nic" },
  { t: T[2] + 4.2, line: 1, to: "host" },
  { t: T[2] + 6.8, line: 0, to: "host" },
  { t: T[2] + 9.4, line: 1, to: "nic" },
];

/** x of a cache line: parked in a cache, or on its way to the side that touched it. */
function lineX(t: number, line: number): number {
  let side = START[line];
  let x = SIDE_X[side];
  for (const mv of MOVES) {
    if (mv.line !== line || t < mv.t) continue;
    x = lerp(SIDE_X[side], SIDE_X[mv.to], prog(t, mv.t, MOVE_D));
    side = mv.to;
  }
  return x;
}

// ---- b2, b5 labels
const EMU = "EMULATED: ONE XEON SOCKET PLAYS THE NIC";
const FIG = "READ FROM CXL-NIC FIG. 14";
const PUSH_PER = 0.95;
const PUSH_DUR = 1.25;

// ---- b3–b4: CC-NIC's own control, as three rows (qualitative: no bars)
const ROW_X = 250;
const ROW_W = 1420;
const ROW_H = 96;
const ROW_Y = [330, 458, 586];
const ROWS: { name: string; link: "pcie" | "coh" }[] = [
  { name: "native PCIe NIC (E810)", link: "pcie" },
  { name: "E810's interface over the coherent link", link: "coh" },
  { name: "CC-NIC's redesigned interface", link: "coh" },
];
const CHANGED = "LINK AND INTERFACE CHANGED AT ONCE";

// ---- b6: the fork (two cubic branches from the question node)
const FY = 50; // vertical offset of the whole fork group
const FORK_L: Pt[] = [];
const FORK_R: Pt[] = [];
for (let i = 0; i <= 24; i++) {
  const u = i / 24;
  const [a, b, c, d] = [(1 - u) ** 3, 3 * (1 - u) ** 2 * u, 3 * (1 - u) * u * u, u ** 3];
  const y = FY + a * 440 + b * 500 + c * 470 + d * 540;
  FORK_L.push([(a + b) * 960 + (c + d) * 600, y]);
  FORK_R.push([(a + b) * 960 + (c + d) * 1320, y]);
}

function Body() {
  const t = useT();
  // ---------------------------------------------------------------- the diagram: CC-NIC's world (b0–b2), CXL-NIC (b5)
  const diagA = win(t, T[0] + 0.1, T[3] + 0.1, 0.8);
  const diagB = win(t, T[5] + 0.7, T[6] + 0.1, 0.6);
  const inB = t >= T[4]; // the diagram is hidden while it switches from CC-NIC to CXL-NIC
  const coh = inB ? 1 : prog(t, T[0] + 2.0, 1.6);
  const rl = inB ? 0 : prog(t, T[2] + 0.4, 0.9); // relabel: the "NIC" is one Xeon socket
  let touchHost = 0;
  let touchNic = 0;
  for (const mv of MOVES) {
    const k = win(t, mv.t - 0.45, mv.t + 0.5, 0.2);
    if (mv.to === "host") touchHost = Math.max(touchHost, k);
    else touchNic = Math.max(touchNic, k);
  }
  const linesOp = inB ? 0 : prog(t, T[1] + 0.2, 0.6);
  const pushHl = inB ? prog(t, T[5] + 5.6, 0.8) : 0;
  const hostCacheHl = inB ? pushHl : Math.min(1, 0.45 * coh + 0.4 * touchHost);
  const nicCacheHl = inB ? 0.3 : Math.min(1, 0.45 * coh + 0.4 * touchNic);
  const memDim = 0.7 * coh;
  const memOp = 1 - 0.75 * memDim;
  const stat490 = prog(t, T[2] + 5.2, 0.8);
  // b5: mostly non-coherent pushes into the host cache
  const pushK = inB ? prog(t, T[5] + 4.6, 1.2) : 0;
  const s0 = T[5] + 5.0;
  const pushCells: number[] = [];
  if (inB && t >= s0) {
    for (let i = Math.max(0, Math.ceil((t - s0 - PUSH_DUR) / PUSH_PER)); i <= Math.floor((t - s0) / PUSH_PER); i++) pushCells.push((t - s0 - i * PUSH_PER) / PUSH_DUR);
  }
  const pushLbl = prog(t, T[5] + 5.4, 0.6);
  const v13 = prog(t, T[5] + 8.4, 0.7);
  const v21 = prog(t, T[5] + 9.6, 0.7);

  // ---------------------------------------------------------------- b3–b4: the rows
  const rowsOp = win(t, T[3] + 0.8, T[5] + 0.1, 0.6);
  const rowK = [prog(t, T[3] + 0.9, 0.6), prog(t, T[3] + 2.4, 0.6), prog(t, T[3] + 4.4, 0.6)];
  const mark = prog(t, T[3] + 6.4, 0.6);
  const focus = prog(t, T[4] + 0.3, 0.7);
  const expand = prog(t, T[4] + 0.9, 0.8);
  const tagK = prog(t, T[4] + 1.4, 0.6);
  const chips: [string, number][] = [
    ["signals packed into descriptors", prog(t, T[4] + 3.6, 0.6)],
    ["rings placed with the writer", prog(t, T[4] + 5.6, 0.6)],
  ];

  // ---------------------------------------------------------------- b6: the fork
  const fork = prog(t, T[6] + 0.7, 0.8);
  const node = prog(t, T[6] + 0.9, 0.5);
  const branch = prog(t, T[6] + 1.3, 1.0);
  const wordL = prog(t, T[6] + 1.9, 0.7);
  const wordR = prog(t, T[6] + 2.4, 0.7);
  const lean = prog(t, T[6] + 5.2, 1.2); // "hand-over" gains a little weight

  return (
    <>
      <Stage>
        <g opacity={Math.max(diagA, diagB)}>
          <HostNic
            g={G}
            coh={coh}
            hl={{ hostCache: hostCacheHl, nicCache: nicCacheHl, nic: 0.8 * (inB ? win(t, T[5] + 1.4, T[5] + 3.6, 0.5) : win(t, T[2] + 0.3, T[2] + 1.8, 0.4)) }}
            hlColor={{ hostCache: inB ? P.push : P.coh, nicCache: P.coh }}
            dim={{ hostMem: memDim, nicMem: memDim }}
            labels={{ nicCore: "", nicCache: "", nicMem: "" }}
            linkLabel=""
            hostTitle=""
            nicTitle=""
          />
          {/* captions drawn here so they can crossfade when the NIC turns out to be a Xeon socket */}
          <BoxTitle r={G.host} s={1} color={P.host} opacity={1 - rl}>
            HOST · CPU
          </BoxTitle>
          <BoxTitle r={G.host} s={1} color={P.host} opacity={rl}>
            HOST · Xeon socket
          </BoxTitle>
          <BoxTitle r={G.nic} s={1} color={P.nic} opacity={1 - rl}>
            {inB ? "NIC · CXL FPGA" : "NIC"}
          </BoxTitle>
          <BoxTitle r={G.nic} s={1} color={P.nic} opacity={rl}>
            NIC · Xeon socket
          </BoxTitle>
          <PartText r={G.nicCore} s={1} opacity={1 - rl}>
            NIC core
          </PartText>
          <PartText r={G.nicCore} s={1} opacity={rl}>
            CPU core
          </PartText>
          <PartText r={G.nicCache} s={1} opacity={1 - rl} bold={nicCacheHl > 0.5}>
            NIC cache
          </PartText>
          <PartText r={G.nicCache} s={1} opacity={rl} bold={nicCacheHl > 0.5}>
            Cache (LLC)
          </PartText>
          <PartText r={G.nicMem} s={1} opacity={memOp * (1 - rl)}>
            NIC memory
          </PartText>
          <PartText r={G.nicMem} s={1} opacity={memOp * rl}>
            Memory (DRAM)
          </PartText>
          <LinkText g={G} color={P.sub} opacity={1 - coh}>
            PCIe
          </LinkText>
          <LinkText g={G} color={P.coh} opacity={coh}>
            {inB ? "CXL · COHERENT LINK" : "COHERENT LINK (UPI / CXL)"}
          </LinkText>
          {/* b1–b2: a core touches a line, the line moves into its cache */}
          <Flow pts={RT.hostPoll} k={touchHost > 0 ? 1 : 0} color={P.host} w={3} head={10} opacity={touchHost * diagA} />
          <Flow pts={RT.nicPoll} k={touchNic > 0 ? 1 : 0} color={P.nic} w={3} head={10} opacity={touchNic * diagA} />
          {linesOp > 0 && [0, 1].map((l) => <CellAt key={l} x={lineX(t, l)} y={LINE_Y[l]} color={P.coh} w={30} h={18} opacity={linesOp} />)}
          {/* b5: CXL-NIC's fastest design pushes into the host cache */}
          <Flow pts={RT.pushN2H} k={pushK} color={P.push} w={2.5} head={12} glow />
          {pushCells.map((k, i) => (
            <Cell key={i} pts={RT.pushN2H} k={k} color={P.push} />
          ))}
        </g>
      </Stage>

      {/* ---- b0–b2 overlays */}
      <Tag x={rgt(G.nic) - tagW("SCHEMATIC")} y={G.nic.y - 44} opacity={Math.max(diagA, diagB) * prog(t, T[0] + 0.8, 0.6)}>
        SCHEMATIC
      </Tag>
      <Tag x={G.host.x} y={G.host.y - 44} color={P.coh} opacity={diagA * prog(t, T[0] + 2.8, 0.6)}>
        CC-NIC, ASPLOS '24
      </Tag>
      <Tag x={GAP_X - tagW("LOADS AND STORES ONLY") / 2} y={636} color={P.coh} opacity={diagA * win(t, T[1] + 5.6, T[2] + 0.2, 0.5)}>
        LOADS AND STORES ONLY
      </Tag>
      <Text x={GAP_X - 270} y={684} w={540} align="center" size={22} color={P.sub} opacity={diagA * win(t, T[1] + 6.3, T[2] + 0.2, 0.5)}>
        no doorbell · no DMA
      </Text>
      <Tag x={G.nic.x + (G.nic.w - tagW(EMU)) / 2} y={btm(G.nic) + 16} color={P.coh} opacity={diagA * prog(t, T[2] + 1.2, 0.6)}>
        {EMU}
      </Tag>
      <BigStat
        x={GAP_X}
        y={232}
        w={520}
        align="center"
        value="490–650"
        unit="ns"
        size={80}
        color={P.coh}
        label="minimum round trip, emulated"
        note="~77% lower than PCIe NICs"
        opacity={diagA * stat490}
        dy={(1 - stat490) * 14}
      />

      {/* ---- b3–b4: CC-NIC's own control */}
      {rowsOp > 0 && (
        <div style={{ position: "absolute", inset: 0, opacity: rowsOp }}>
          <Text x={ROW_X} y={262} size={17} color={P.sub} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
            CC-NIC'S OWN CONTROL EXPERIMENT · ASPLOS '24
          </Text>
          {ROWS.map((r, i) => {
            const border = i === 1 && mark > 0 ? alpha(P.bad, 0.2 + 0.5 * mark) : i === 2 && focus > 0 ? alpha(P.coh, 0.2 + 0.55 * focus) : P.panelLine;
            const op = rowK[i] * (i < 2 ? 1 - 0.62 * focus : 0.8 + 0.2 * focus);
            return (
              <div
                key={r.name}
                style={{
                  position: "absolute",
                  left: ROW_X,
                  top: ROW_Y[i],
                  width: ROW_W,
                  height: i === 2 ? lerp(ROW_H, 214, expand) : ROW_H,
                  boxSizing: "border-box",
                  borderRadius: 18,
                  background: P.panel,
                  border: `1.5px solid ${border}`,
                  boxShadow: i === 1 && mark > 0 ? `0 0 26px ${alpha(P.bad, 0.16 * mark)}` : undefined,
                  ...rise(op, 14),
                }}
              >
                {/* link glyph: four PCIe lanes, or the lavender coherent link */}
                <div style={{ position: "absolute", left: 36, top: 16, width: 130, textAlign: "center", filter: i === 2 && tagK > 0 ? `drop-shadow(0 0 ${10 * tagK}px ${alpha(P.coh, 0.8)})` : undefined }}>
                  <svg width={130} height={36} style={{ display: "block" }}>
                    {r.link === "pcie" ? (
                      [4, 13, 22, 31].map((y) => <line key={y} x1={0} y1={y} x2={130} y2={y} stroke={P.link} strokeWidth={2} />)
                    ) : (
                      <rect x={1} y={4} width={128} height={28} fill={alpha(P.coh, 0.16)} stroke={alpha(P.coh, 0.6)} strokeWidth={1.5} />
                    )}
                  </svg>
                  <div style={{ fontSize: 15, letterSpacing: 1.6, fontWeight: 600, marginTop: 6, color: r.link === "pcie" ? P.sub : P.coh }}>{r.link === "pcie" ? "PCIe" : "COHERENT"}</div>
                </div>
                <div style={{ position: "absolute", left: 210, top: 0, height: ROW_H, display: "flex", alignItems: "center", fontSize: 30, color: P.ink }}>{r.name}</div>
                {i === 1 && (
                  <div style={{ position: "absolute", right: 30, top: 14, textAlign: "right", opacity: mark }}>
                    <div style={{ fontSize: 24, fontWeight: 600, color: P.bad }}>
                      <svg width={16} height={12} style={{ marginRight: 10 }}>
                        <path d="M0 0 L16 0 L8 12 Z" fill={P.bad} />
                      </svg>
                      lower packet rate than native PCIe
                    </div>
                    <div style={{ fontSize: 20, color: alpha(P.bad, 0.85), marginTop: 4 }}>79% lower throughput than CC-NIC</div>
                  </div>
                )}
                {i === 2 && (
                  <>
                    <Tag x={ROW_W - 32 - tagW(CHANGED)} y={30} color={P.coh} opacity={tagK}>
                      {CHANGED}
                    </Tag>
                    <div style={{ position: "absolute", left: 210, top: 116, display: "flex", gap: 22 }}>
                      {chips.map(([label, k]) => (
                        <div
                          key={label}
                          style={{
                            fontSize: 24,
                            color: P.ink,
                            padding: "11px 24px",
                            borderRadius: 14,
                            background: alpha(P.coh, 0.12),
                            border: `1.5px solid ${alpha(P.coh, 0.7)}`,
                            whiteSpace: "nowrap",
                            ...rise(k, 12),
                          }}
                        >
                          {label}
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ---- b5: CXL-NIC on real hardware */}
      <Tag x={G.host.x} y={G.host.y - 44} color={P.coh} opacity={diagB * prog(t, T[5] + 1.2, 0.6)}>
        CXL-NIC, MICRO '25 · REAL CXL FPGA
      </Tag>
      <BigStat x={GAP_X} y={236} w={520} align="center" value="~1.3" unit="µs" size={80} color={P.push} opacity={diagB * v13} dy={(1 - v13) * 14} />
      <Text x={GAP_X - 270} y={330} w={540} align="center" size={24} color={P.push} opacity={diagB * pushLbl}>
        mostly non-coherent pushes
      </Text>
      <Tag x={GAP_X - tagW(FIG) / 2} y={378} opacity={diagB * v13}>
        {FIG}
      </Tag>
      <BigStat
        x={GAP_X}
        y={640}
        w={600}
        align="center"
        value="~2.1"
        unit="µs"
        size={52}
        color={P.coh}
        label="CC-NIC's design on the same hardware"
        opacity={diagB * v21}
        dy={(1 - v21) * 12}
      />

      {/* ---- b6: the real question */}
      <Stage opacity={fork}>
        <Flow pts={[[960, FY + 300], [960, FY + 360]]} k={node} color={alpha(P.gold, 0.6)} w={2} head={0} />
        <Flow pts={FORK_L} k={branch} color={alpha(P.coh, 0.6)} w={2.5} head={0} />
        <Flow pts={FORK_R} k={branch} color={P.push} w={3} head={0} glow={lean > 0} />
        <g opacity={node}>
          <circle cx={960} cy={FY + 400} r={40} fill={P.bg0} stroke={P.gold} strokeWidth={2} />
          <text x={960} y={FY + 402} textAnchor="middle" dominantBaseline="central" fontSize={46} fontWeight={800} fill={P.gold} fontFamily={FONT}>
            ?
          </text>
        </g>
      </Stage>
      <Text x={0} y={FY + 250} w={1920} align="center" size={18} color={P.sub} opacity={fork} style={{ letterSpacing: 3, fontWeight: 600 }}>
        WHERE DOES THE SPEEDUP COME FROM?
      </Text>
      <Text x={200} y={FY + 560} w={800} align="center" size={92} weight={700} color={P.coh} opacity={fork * wordL * lerp(0.9, 0.62, lean)} style={{ letterSpacing: -1.5, transform: `translateY(${(1 - wordL) * 16}px)` }}>
        Coherence
      </Text>
      <Text
        x={920}
        y={FY + 552}
        w={800}
        align="center"
        size={100}
        weight={800}
        color={P.push}
        opacity={fork * wordR}
        style={{ letterSpacing: -1.5, textShadow: `0 0 ${lerp(18, 40, lean)}px ${alpha(P.push, lerp(0.22, 0.5, lean))}`, transform: `translateY(${(1 - wordR) * 16}px) scale(${lerp(1, 1.06, lean)})` }}
      >
        Hand-over
      </Text>
    </>
  );
}

export const C03: SceneDef = {
  id: "C03",
  title: "CC-NIC's bet",
  stage: 0,
  source: "CC-NIC (ASPLOS '24) \u00a74, \u00a75.2\u20135.3 \u00b7 CXL-NIC (MICRO '25) \u00a77",
  dur: DUR,
  cues,
  C: Body,
};
