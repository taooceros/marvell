import React from "react";
import { C, Card, Code, Cue, FONT, K, MONO, Packet, Pill, Pt, SceneDef, Stage, Stat, Txt, appear, clamp01, fade, lerp, partial, pathD, pop, prog, useT, along } from "../lib";
import { BlockId, DP_LABEL, Datapath, dpGeom, routes } from "./pcie";
import { Tag, cycle } from "./_SceneReturn";

// <cues>
const cues: Cue[] = [
  [0.60, 11.18, "Can a card core write host memory **itself**, with no DMA engine? Marvell's open drivers only set up **host → card** windows (BAR4).", "Can a card core write host memory by itself, with no D M A engine in between? Marvell's open source drivers only set up windows in the other direction, host to card."], // 0
  [11.53, 23.33, "Marvell's open docs stop at inbound BAR4. But the open Cavium **BDK** in coreboot documents `PEM()_REG_NORM()_ACC`: “for access to memory from cores”.", "Marvell's open documents stop at inbound BAR 4. But the open source Cavium B D K, which lives in coreboot, documents a PEM table for memory access from the cores."], // 1
  [23.68, 38.20, "**256 regions**, each mapping **2 GiB** of card-core addresses onto PCIe. Its `pf / vf / vf_active` fields “must be zero in RC mode”: an **endpoint** may use them.", "It has two hundred fifty-six regions, each mapping two gigabytes of core address space onto P C I express. Its function fields must be zero in root complex mode, which hints that an endpoint can send writes as a chosen function."], // 2
  [38.55, 55.05, "Disassembling the card firmware gave CN106's real layout: table at PEM0 **+0x4000 + 16·i**; region i at **0x8800_0000_0000 + i·2 GiB**; all 256 unused in EP mode.", "That header describes the older C N nine chips, and the offsets differ. So we disassembled the card's firmware, found where it sets up this table, and read off the real layout for C N one oh six. In endpoint mode, all two hundred fifty-six regions sit unused."], // 3
  [55.40, 66.02, "Point region **255** at the host reply ring, writing as host **VF0** (`vf_active`, vf 0). It worked on the first try, through the host **IOMMU**.", "We pointed region two fifty-five at the host's reply ring, writing as the host's virtual function zero. It worked on the first try, straight through the host I O M M U."], // 4
  [66.37, 81.71, "Lesson: one read-only probe of a CSR the firmware reads (`0x87e0_0300_1000`) raised a card **SEI-UER** error (2 → 3). The firmware runs in the secure world.", "One lesson along the way: a single read of a register that the firmware also reads raised a hardware error on the card. The firmware runs in the secure world, and Linux can't safely touch the same address. The card survived, and no further errors appeared."], // 5
];
const DUR = 83.11; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
const nxt = (i: number) => (i + 1 < T.length ? T[i + 1] : DUR);
/** Visible from cue i (+d) until cue j ends (held until the next cue start, then fades over 0.5 s). */
const during = (t: number, i: number, d = 0.2, j = i) => fade(t, T[i] + d, nxt(j));
/** Main labels only: the small layout (s ≈ 0.5) has no room for sub-labels. */
const BARE = Object.fromEntries(Object.entries(DP_LABEL).map(([k, v]) => [k, [v[0]]])) as Record<BlockId, [string]>;

// ---- region table (rows shown: 0..3, …, 254, 255)
const ROWS: (number | null)[] = [0, 1, 2, 3, null, 254, 255];
const ROW_H = 38;
const ROW_G = 6;
const TW = 540;
const ZOOM_J = 3; // row zoomed in cue 2
const rowY = (y: number, j: number) => y + j * (ROW_H + ROW_G);

// ---- one row's fields (cue 2), bottom-right
const FY = 600;
const FH = 84;
const FIELDS = [
  { name: "ba", sub: "PCIe address", w: 400 },
  { name: "wnmerge", sub: "write merge", w: 190 },
  { name: "pf", w: 110 },
  { name: "vf", w: 110 },
  { name: "vf_active", w: 180 },
];
const FX = FIELDS.reduce<number[]>((xs, f, i) => [...xs, i ? xs[i - 1] + FIELDS[i - 1].w + 12 : 720], []);

// ---- cue 5: secure CSR chip (right column)
const CHIP = { x: 1336, y: 168, w: 540, h: 88 };

function Body() {
  const t = useT();
  let ci = 0;
  T.forEach((a, i) => {
    if (t >= a) ci = i;
  });

  // one evolving datapath: big left (0) → small top-left (1–3) → big left (4–5)
  const m1 = prog(t, T[1], 1.0);
  const m2 = prog(t, T[4] + 0.5, 1.0);
  const g = dpGeom(lerp(lerp(0.7, 0.5, m1), 0.7, m2), lerp(lerp(6, 20, m1), 6, m2), lerp(lerp(150, 50, m1), 150, m2));
  const r = routes(g);

  const hl: Partial<Record<BlockId, string>> =
    ci === 0
      ? { core: K.inline, reqRing: K.inline, cores: K.store }
      : ci <= 3
        ? { pem: K.store }
        : ci === 4
          ? { cores: K.store, pem: K.store, replyRing: K.store }
          : { cores: K.uc };

  // cue 0: inline (host → card) works; card → host is the open question
  const inl = cycle(t, T[0] + 5.0, nxt(0) - 0.3, 1.8);
  const qK = prog(t, T[0] + 1.0, 1.5);
  const qPt = g.pt([960, 515]);

  // cue 4: stores through region 255
  const ST_A = T[4] + 4.0;
  const ST_P = 2.0;
  const st = cycle(t, ST_A, nxt(4) - 0.3, ST_P);
  const nStore = [0, 1, 2].filter((j) => t >= ST_A + j * ST_P + 1.6 && ST_A + (j + 1) * ST_P <= nxt(4) - 0.3).length;
  const stK = st.ph >= 0 ? prog(st.ph, 0, 1.6) : 0;
  const stPos = along(r.storeReply, Math.max(0.001, stK));
  const gateP = g.pt([700, 515]);
  const gatePass = st.ph >= 0 && stK > 0 && stK < 1 ? clamp01(1.4 - Math.hypot(stPos[0] - gateP[0], stPos[1] - gateP[1]) / (70 * g.s)) : 0;
  const gateOp = fade(t, T[4] + 3.4, 1e9);

  // region table: bottom-left (cues 2–3) → right column (cue 4)
  const tx = lerp(60, 1336, m2);
  const ty = lerp(560, 196, m2);
  const tableOp = fade(t, T[2] + 0.3, T[5]);
  const greyK = prog(t, T[3] + 12.5, 0.8);
  const liveK = prog(t, T[4] + 1.8, 0.6);
  const zoomOp = during(t, 2, 6.0);

  // cue 5: probe read of a secure CSR
  const coreR = g.pt([1830, 452.5]);
  const probe: Pt[] = [coreR, [1318, coreR[1]], [1318, CHIP.y + CHIP.h / 2], [CHIP.x, CHIP.y + CHIP.h / 2]];
  const PROBE = T[5] + 1.0;
  const hit = prog(t, PROBE + 1.3, 0.3);
  const lessonOp = fade(t, T[5] + 0.5, 1e9);

  return (
    <>
      <Stage>
        <Datapath
          g={g}
          show={{ sdpRing: 0.35, ctrl: 0.35 }}
          hl={hl}
          dimOthers={0.6}
          labels={g.s < 0.6 ? BARE : {}}
          cellFill={{ replyRing: (i) => (i < nStore ? K.store : undefined) }}
          cores={1}
          activeCores={1}
          linkGlow={0.3}
          opacity={prog(t, 0, 0.5)}
        />

        {/* cue 0: host → card works (inline BAR4 window); card core → host memory? */}
        {inl.ph >= 0 && (
          <g opacity={1 - prog(inl.ph, 1.3, 0.3)}>
            <Packet pts={r.inline} k={prog(inl.ph, 0, 1.3)} color={K.inline} r={9} />
          </g>
        )}
        <g opacity={during(t, 0, 4.6)}>
          <Tag x={g.pt([960, 345])[0]} y={g.pt([960, 345])[1]} size={22} color={K.inline} border={K.inline}>
            host → card: BAR4 windows ✓
          </Tag>
        </g>
        <g opacity={during(t, 0, 0.8)}>
          <path d={pathD(partial(r.storeReply, qK))} fill="none" stroke={K.store} strokeWidth={4} strokeDasharray="10 9" opacity={0.75} />
          <g transform={`translate(${qPt[0]} ${qPt[1]}) scale(${pop(t, T[0] + 2.2, 0.5)})`}>
            <circle r={30} fill={C.bg} stroke={K.store} strokeWidth={3} />
            <text textAnchor="middle" dominantBaseline="central" fontSize={40} fontWeight={800} fill={K.store} fontFamily={FONT}>
              ?
            </text>
          </g>
          <Tag x={g.pt([960, 600])[0]} y={g.pt([960, 600])[1]} size={22} color={K.store} border={K.store} opacity={prog(t, T[0] + 2.6, 0.4)}>
            card core → host memory, by itself?
          </Tag>
        </g>

        {/* region table */}
        <g opacity={tableOp} fontFamily={FONT}>
          <text x={tx} y={ty - 16} fontSize={24} fontWeight={700} fill={C.text} fontFamily={MONO}>
            REG_NORM(i)_ACC
            <tspan fill={C.muted} fontFamily={FONT} fontWeight={400} fontSize={22}>
              {"  · 256 rows"}
            </tspan>
          </text>
          {ROWS.map((idx, j) => {
            const y = rowY(ty, j);
            const k = prog(t, T[2] + 0.5 + j * 0.15, 0.4);
            if (idx === null) {
              return (
                <text key={j} x={tx + TW / 2} y={y + ROW_H / 2} textAnchor="middle" dominantBaseline="central" fontSize={28} fontWeight={800} fill={C.muted} opacity={k}>
                  ⋮
                </text>
              );
            }
            const live = idx === 255 ? liveK : 0;
            const zoom = j === ZOOM_J ? zoomOp : 0;
            return (
              <g key={j} opacity={k}>
                <rect x={tx} y={y} width={TW} height={ROW_H} rx={6} fill={C.panel} stroke={C.line} strokeWidth={1.5} />
                <rect x={tx} y={y} width={TW} height={ROW_H} rx={6} fill={`${K.store}26`} stroke={K.store} strokeWidth={3} opacity={live} filter="url(#glow)" />
                <rect x={tx} y={y} width={TW} height={ROW_H} rx={6} fill="none" stroke={C.yellow} strokeWidth={3} opacity={zoom} />
                <text x={tx + 16} y={y + ROW_H / 2} dominantBaseline="central" fontSize={22} fontWeight={700} fontFamily={MONO} fill={live > 0.5 ? K.store : C.muted}>
                  {idx}
                </text>
                <text x={tx + 84} y={y + ROW_H / 2} dominantBaseline="central" fontSize={22} fill={C.text} opacity={1 - greyK}>
                  2 GiB of core addresses → PCIe
                </text>
                <text x={tx + 84} y={y + ROW_H / 2} dominantBaseline="central" fontSize={22} fill={C.muted} fontFamily={MONO} opacity={greyK * (1 - live)}>
                  0 · unused in EP mode
                </text>
                <text x={tx + 84} y={y + ROW_H / 2} dominantBaseline="central" fontSize={22} fontWeight={700} fill={K.store} opacity={live}>
                  → host reply ring · as host VF0
                </text>
              </g>
            );
          })}
        </g>

        {/* cue 2: zoom of one row's fields */}
        <g opacity={zoomOp} fontFamily={FONT}>
          <polygon
            points={`${tx + TW},${rowY(ty, ZOOM_J)} ${tx + TW},${rowY(ty, ZOOM_J) + ROW_H} ${FX[0]},${FY + FH} ${FX[0]},${FY}`}
            fill={C.yellow}
            opacity={0.08}
          />
          <text x={FX[0]} y={FY - 22} fontSize={24} fill={C.muted}>
            one row, field by field (BDK names)
          </text>
          {FIELDS.map((f, i) => {
            const k = prog(t, T[2] + 6.4 + i * 0.25, 0.4);
            const ef = i >= 2 ? prog(t, T[2] + 8.6, 0.5) : 0;
            return (
              <g key={f.name} opacity={k}>
                <rect x={FX[i]} y={FY} width={f.w} height={FH} rx={10} fill={ef > 0 ? `${K.store}${Math.round(ef * 40).toString(16).padStart(2, "0")}` : C.panel} stroke={ef > 0.5 ? K.store : C.line} strokeWidth={2.5} />
                <text x={FX[i] + f.w / 2} y={FY + (f.sub ? FH * 0.38 : FH / 2)} textAnchor="middle" dominantBaseline="central" fontSize={28} fontWeight={800} fontFamily={MONO} fill={C.text}>
                  {f.name}
                </text>
                {f.sub && (
                  <text x={FX[i] + f.w / 2} y={FY + FH * 0.74} textAnchor="middle" dominantBaseline="central" fontSize={20} fill={C.muted}>
                    {f.sub}
                  </text>
                )}
              </g>
            );
          })}
          <g opacity={prog(t, T[2] + 8.6, 0.5)}>
            <path d={`M${FX[2]} ${FY + FH + 12} v12 H${FX[4] + FIELDS[4].w} v-12`} fill="none" stroke={K.store} strokeWidth={2.5} />
            <text x={(FX[2] + FX[4] + FIELDS[4].w) / 2} y={FY + FH + 58} textAnchor="middle" fontSize={28} fontStyle="italic" fill={C.text}>
              “in RC mode must be zero”
            </text>
          </g>
        </g>

        {/* cue 4: IOMMU gate on the host side + stores through region 255 */}
        <g opacity={gateOp}>
          <rect x={gateP[0] - 9 * g.s} y={gateP[1] - 34 * g.s} width={18 * g.s} height={68 * g.s} rx={4} fill={C.panel} stroke={C.text} strokeWidth={3} />
          <rect x={gateP[0] - 9 * g.s} y={gateP[1] - 34 * g.s} width={18 * g.s} height={68 * g.s} rx={4} fill={K.ok} stroke={K.ok} strokeWidth={3} opacity={gatePass} filter="url(#glow)" />
          <Tag x={gateP[0] + 4} y={g.pt([700, 572])[1]} size={22} anchor="start" border={K.ok} color={C.text}>
            IOMMU ✓
          </Tag>
          <Tag x={g.pt([1266, 595])[0]} y={g.pt([1266, 595])[1]} size={22} color={K.store} border={K.store}>
            region 255
          </Tag>
        </g>
        {st.ph >= 0 && (
          <g opacity={1 - prog(st.ph, 1.65, 0.3)}>
            <Packet pts={r.storeReply} k={stK} color={K.store} />
          </g>
        )}

        {/* cue 5: probe read of a CSR the secure firmware reads */}
        <g opacity={lessonOp} fontFamily={FONT}>
          <rect x={CHIP.x} y={CHIP.y} width={CHIP.w} height={CHIP.h} rx={12} fill={`${K.uc}${hit > 0 ? "30" : "10"}`} stroke={K.uc} strokeWidth={hit > 0 ? 4 : 2} filter={hit > 0 ? "url(#glow)" : undefined} />
          <text x={CHIP.x + 22} y={CHIP.y + 32} fontSize={28} fontWeight={800} fontFamily={MONO} fill={C.text}>
            CSR 0x87e0_0300_1000
          </text>
          <text x={CHIP.x + 22} y={CHIP.y + 66} fontSize={22} fill={C.muted}>
            read by the secure-world firmware
          </text>
          {ci === 5 && (
            <g opacity={1 - prog(t, PROBE + 1.4, 0.4)}>
              <Packet pts={probe} k={prog(t, PROBE, 1.3)} color={K.uc} r={10} />
            </g>
          )}
          <Tag x={CHIP.x + CHIP.w - 12} y={CHIP.y + CHIP.h + 4} size={22} anchor="end" color={K.uc} border={K.uc} opacity={prog(t, PROBE + 2.0, 0.4)}>
            read returned junk
          </Tag>
        </g>
      </Stage>

      {/* cue 0: open drivers (right column) → context card (top-right, cue 1) */}
      <Card
        x={lerp(1336, 1180, m1)}
        y={lerp(250, 140, m1)}
        w={540}
        title="Open drivers"
        titleColor={K.inline}
        border={K.inline}
        style={{ ...appear(t, T[0] + 5.2, nxt(1)), filter: `grayscale(${prog(t, T[1] + 0.5, 0.6)})` }}
      >
        <div style={{ fontFamily: MONO, fontSize: 22, color: C.text, lineHeight: 1.5 }}>
          pcie-marvell-cnxk-ep.c
          <br />
          DAO lib/pem
          <br />
          octep agent
        </div>
        <div style={{ fontSize: 26, fontWeight: 800, color: K.inline, marginTop: 10 }}>→ inbound BAR4 only (host → card)</div>
      </Card>

      {/* cue 1: two documents */}
      <Card
        x={60}
        y={520}
        w={600}
        h={340}
        title="Marvell open docs and drivers"
        titleColor={C.muted}
        style={{ ...appear(t, T[1] + 0.8, nxt(1)), filter: `grayscale(1) brightness(${1 - 0.35 * prog(t, T[1] + 4.5, 0.6)})` }}
      >
        <div style={{ fontSize: 26, color: C.muted, marginTop: 30 }}>card → host windows:</div>
        <div style={{ fontSize: 56, fontWeight: 800, color: C.text, marginTop: 16 }}>not covered</div>
      </Card>
      <Card
        x={720}
        y={520}
        w={1140}
        h={340}
        title="Cavium BDK (open, in coreboot) · bdk-csrs-pem.h"
        titleColor={K.store}
        border={K.store}
        style={{ ...appear(t, T[1] + 5.0, nxt(1)), boxShadow: `0 0 ${24 * prog(t, T[1] + 8.5, 0.6)}px ${K.store}55` }}
      >
        <div style={{ fontFamily: MONO, fontSize: 40, fontWeight: 800, color: C.text, marginTop: 30 }}>PEM()_REG_NORM()_ACC</div>
        <div
          style={{
            fontSize: 36,
            fontStyle: "italic",
            color: C.text,
            marginTop: 26,
            display: "inline-block",
            borderBottom: `4px solid ${K.store}`,
            borderBottomColor: `rgba(195,232,141,${prog(t, T[1] + 8.5, 0.6)})`,
          }}
        >
          “…for access to memory from cores”
        </div>
      </Card>

      {/* cue 2 */}
      <Stat
        value="256 × 2 GiB"
        label="regions · card-core addresses → PCIe"
        color={K.store}
        size={64}
        style={{ position: "absolute", left: 1180, top: 140, width: 560, boxSizing: "border-box", ...appear(t, T[2] + 1.2, nxt(2)) }}
      />
      <Pill x={FX[2] + 30} y={FY + FH + 96} size={30} color={K.store} solid style={appear(t, T[2] + 11.5, nxt(2))}>
        → EP may use them
      </Pill>

      {/* cue 3: header ≠ CN10K → firmware disassembly → real layout */}
      <Pill x={1000} y={124} size={28} color={K.bad} style={appear(t, T[3] + 0.5, nxt(3))}>
        CN9 header ≠ CN10K offsets
      </Pill>
      <Code
        x={1000}
        y={192}
        w={860}
        size={22}
        hl={t < T[3] + 4.0 ? -1 : t < T[3] + 5.5 ? 1 : t < T[3] + 7.5 ? 2 : t < T[3] + 9.5 ? 3 : 4}
        lines={[
          "// how the CN106 offsets were found",
          "1. card firmware (EBF) from the SPI backup",
          "2. disassemble it",
          "3. find where it sets up REG_NORM_ACC",
          "4. read off: CONST_ACC +0x218 · table +0x4000 + 16·i",
        ]}
        style={{ boxSizing: "border-box", ...appear(t, T[3] + 3.5, nxt(3)) }}
      />
      <Card x={680} y={520} w={1180} h={350} title="CN106 layout, read from the firmware" titleColor={K.store} border={K.store} style={appear(t, T[3] + 8.2, nxt(3))}>
        {[
          ["table", "PEM0 +0x4000 + 16·i  (0x8e00_0000_4000)", 8.5, C.text],
          ["geometry", "PEM0_CONST_ACC +0x218 → 256 × 2 GiB", 9.5, C.text],
          ["region i", "core PA 0x8800_0000_0000 + i·2 GiB", 10.5, C.text],
          ["EP mode", "all 256 entries = 0 (unused)", 12.5, C.muted],
        ].map(([k, v, at, col]) => (
          <div key={k as string} style={{ display: "flex", fontFamily: MONO, fontSize: 26, lineHeight: 1.75, ...appear(t, T[3] + (at as number), undefined, 12) }}>
            <span style={{ width: 170, color: C.muted }}>{k}</span>
            <span style={{ color: col as string, fontWeight: 700 }}>{v}</span>
          </div>
        ))}
      </Card>

      {/* cue 4: region 255 retargeted */}
      <Txt x={1336} y={530} w={540} size={26} style={appear(t, T[4] + 2.6, nxt(4))}>
        <span style={{ fontFamily: MONO, color: K.store, fontWeight: 700 }}>ba</span> → IOVA of the host reply ring
      </Txt>
      <Txt x={1336} y={575} w={540} size={26} style={appear(t, T[4] + 3.4, nxt(4))}>
        <span style={{ fontFamily: MONO, color: K.store, fontWeight: 700 }}>vf_active</span>, <span style={{ fontFamily: MONO, color: K.store, fontWeight: 700 }}>vf 0</span> → writes as host VF0
      </Txt>
      <Pill x={1336} y={650} size={32} color={K.ok} solid style={appear(t, T[4] + 6.5, nxt(4))}>
        ✓ worked on the first try
      </Pill>

      {/* cue 5: lesson */}
      <Card x={1336} y={300} w={540} title="Lesson" titleColor={K.uc} border={K.uc} style={appear(t, T[5] + 0.6)}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 18 }}>
          <span style={{ fontSize: 24, color: C.muted }}>card SEI-UER count</span>
          <span style={{ fontSize: 56, fontWeight: 800, color: C.text, fontVariantNumeric: "tabular-nums" }}>
            2<span style={{ opacity: prog(t, PROBE + 3.0, 0.5), color: K.uc }}> → 3</span>
          </span>
        </div>
        <div style={{ fontSize: 26, color: C.text, lineHeight: 1.35, marginTop: 14, ...appear(t, T[5] + 7.0, undefined, 12) }}>
          The firmware runs in the <b style={{ color: K.uc }}>secure world</b>: Linux must not read what it reads.
        </div>
        <div style={{ fontSize: 26, color: K.ok, fontWeight: 700, marginTop: 14, ...appear(t, T[5] + 11.5, undefined, 12) }}>
          ✓ card stayed up · no further errors
        </div>
      </Card>
      <Txt x={60} y={792} w={1800} size={36} weight={800} align="center" color={K.uc} style={appear(t, T[5] + 13.5)}>
        Rule: never read a CSR just because the firmware does.
      </Txt>
    </>
  );
}

export const S10: SceneDef = {
  id: "S10",
  title: "Way back #4: a hidden outbound window",
  stage: 2,
  source: "PEM0_REG_NORM_ACC · Cavium BDK · EBF disassembly",
  dur: DUR,
  cues,
  C: Body,
};
