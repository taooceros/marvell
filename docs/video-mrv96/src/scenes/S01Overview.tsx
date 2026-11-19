import {
  C,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  PacketStream,
  Pill,
  SceneDef,
  Stage,
  Txt,
  clamp01,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import { InfPill, InfTag, Rig, rigGeom, rigRoutes } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 12.40, "Our direct datapath links an **Intel Xeon** host (`3.9 GHz` core, `2.2 GHz` uncore) to a **Marvell OCTEON 10 DPU** (24 Arm cores, `2.0 GHz`) over **PCIe Gen5 ×4**.", "Our direct datapath links an Intel Xeon host at three point nine gigahertz to a Marvell Octeon ten D P U with twenty-four Arm cores over P C I e Gen five by four."], // 0
  [12.75, 26.55, "Forward: one `MOVDIR64B` writes a **64-byte** cell directly into card memory (no doorbell, no DMA). Return: a card core sends a **64-byte** write-combining reply to host RAM.", "Forward, one move dir sixty-four B writes a sixty-four byte message straight into card memory, no doorbell, no D M A. Return, a card core writes a sixty-four byte combining reply to host memory."], // 1
  [26.90, 42.10, "At `W = 1`, round-trip latency is **897 ns** (`~429 ns` one way). Three same-machine anchors explain the budget: on-die **DSA NOP** takes **403 ns** (zero PCIe hops).", "At window one, round-trip latency is eight hundred ninety-seven nanoseconds. Three same-machine anchors explain why: an on-die host D S A no-op with zero P C I e hops already takes four hundred three nanoseconds."], // 2
  [42.45, 55.31, "Anchor 2: raw **BAR4 PCIe read** (no card core) takes **646 ns** (`+243 ns` link **[INF]**). Anchor 3: full core-to-core hop reaches **897 ns** (`+212 ns` cache/core **[INF]** + `40 ns` card).", "Second, a raw B A R four P C I e read without a card core takes six hundred forty-six nanoseconds. Third, adding the card core hop brings the full round trip to eight hundred ninety-seven."], // 3
  [55.66, 68.44, "Our honest progress report in 3 parts: **(1) three direct-MMIO findings nailed down**, **(2) one open puzzle** (`13 M/s` closed loop), and **(3) our new 31.3 GB/s Card → DSA breakthrough**.", "Our progress report has three honest parts: three direct-path findings nailed down, the one open puzzle we are actively dissecting on hardware, and our new thirty-one gigabyte per second card-to-D S A breakthrough."], // 4
];
const DUR = 69.84; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // ============================================================== Phase 1: Rig (Cues 0–1)
  const pRig = fade(t, T[0], T[2], 0.45);
  const g = rigGeom(0.9, 86, -12);
  const routes = rigRoutes(g);

  const showHost = prog(t, T[0] + 0.15, 0.7);
  const showHostIn = prog(t, T[0] + 1.0, 0.7);
  const showLink = prog(t, T[0] + 2.5, 0.7);
  const showCard = prog(t, T[0] + 3.6, 0.7);
  const showCardIn = prog(t, T[0] + 4.4, 0.7);
  const showSpecs = prog(t, T[0] + 5.2, 0.7);

  // Cue 1: Forward & Return direct MMIO flow
  const fwdK = prog(t, T[1] + 0.25, 0.65);
  const retK = prog(t, T[1] + 6.0, 0.65);
  const laneFwd = lerp(0.35 * prog(t, T[0] + 5.0, 0.6), 1, fwdK);
  const laneRet = lerp(0.35 * prog(t, T[0] + 5.4, 0.6), 1, retK);

  const fwdCardOp = prog(t, T[1] + 0.4, 0.55) * (1 - prog(t, T[2], 0.45));
  const retCardOp = prog(t, T[1] + 6.2, 0.55) * (1 - prog(t, T[2], 0.45));

  const fwdHl = fwdK * (1 - 0.55 * retK);
  const retHl = retK;

  // ============================================================== Phase 2: 897 ns 3-Anchor Budget (Cues 2–3)
  const pBudget = prog(t, T[2] + 0.25, 0.45) * (1 - prog(t, T[4], 0.45));
  const a1K = prog(t, T[2] + 1.2, 0.9); // Anchor 1: 403 ns on-die DSA NOP
  const a2K = prog(t, T[3] + 0.4, 0.9); // Anchor 2: 646 ns raw BAR4 read
  const a3K = prog(t, T[3] + 4.6, 1.0); // Anchor 3: 897 ns full host <-> card core
  const sumK = prog(t, T[3] + 7.2, 0.6) * (1 - prog(t, T[4], 0.45));

  const bx = 474;
  const pxNs = 1.18; // 1,000 ns = 1,180 px (x = 474..1654)
  const barH = 66;
  const r1y = 250;
  const r2y = 404;
  const r3y = 558;
  const x403 = bx + 403 * pxNs; // 949.5
  const x646 = bx + 646 * pxNs; // 1236.3
  const x858 = bx + 858 * pxNs; // 1486.4
  const x897 = bx + 897 * pxNs; // 1532.5

  // ============================================================== Phase 3: 3-Part Honest Roadmap (Cue 4)
  const pRoad = prog(t, T[4] + 0.25, 0.45);
  const c1K = prog(t, T[4] + 0.45, 0.65);
  const c2K = prog(t, T[4] + 3.0, 0.65);
  const c3K = prog(t, T[4] + 5.6, 0.65);

  return (
    <>
      {/* ======================================================== SVG STAGE */}
      <Stage>
        {/* ---------------- Phase 1: Hardware Direct Path Rig ---------------- */}
        {pRig > 0.001 && (
          <g opacity={pRig}>
            <Rig
              g={g}
              cores={4}
              activeCore={retK > 0.2 ? Math.floor(rt * 2.5) % 4 : -1}
              show={{
                host: showHost,
                hcore: showHostIn,
                wq: showHostIn,
                hring: showHostIn,
                link: showLink,
                card: showCard,
                ccores: showCardIn,
                cring: showCardIn,
                wc: showCardIn,
              }}
              hl={{
                hcore: fwdHl,
                wq: fwdHl,
                cring: fwdHl,
                ccores: retHl,
                wc: retHl,
                hring: retHl,
              }}
              labels={{
                host: ["HOST", "Intel Xeon"],
                card: ["DPU", "OCTEON 10"],
                hcore: ["core 40", "3.9 GHz"],
                wq: ["write queue", "CHA · uncore"],
                hring: ["reply ring", "64 B cells"],
                cring: ["request ring", "64 B cells"],
                ccores: ["24 Arm cores", "2.0 GHz"],
                wc: ["WC buffer", "4 × 16 B → 64 B"],
                link: ["PCIe Gen5 ×4"],
              }}
              ringFill={{
                cring: fwdK > 0.1 ? 3 + (Math.floor(rt * 4) % 5) : 0,
                hring: retK > 0.1 ? 2 + (Math.floor(rt * 3.5) % 5) : 0,
              }}
              lanes={{ fwd: laneFwd, ret: laneRet }}
            />
            {fwdK > 0.05 && (
              <g opacity={fwdK}>
                <PacketStream pts={routes.send} travel={rt * 230} gap={58} size={13} color={K.fwd} />
                <PacketStream pts={routes.fwd} travel={rt * 300} gap={68} size={15} color={K.fwd} />
              </g>
            )}
            {retK > 0.05 && (
              <g opacity={retK}>
                <PacketStream pts={routes.poll} travel={rt * 210} gap={62} size={12} color={K.ret} />
                <PacketStream pts={routes.reply} travel={rt * 230} gap={62} size={13} color={K.ret} />
                <PacketStream pts={routes.ret} travel={rt * 300} gap={68} size={15} color={K.ret} />
                <PacketStream pts={routes.hpoll} travel={rt * 220} gap={62} size={12} color={K.ret} />
              </g>
            )}
          </g>
        )}

        {/* ---------------- Phase 2: 897 ns 3-Anchor Latency Budget ---------------- */}
        {pBudget > 0.001 && (
          <g opacity={pBudget} fontFamily={FONT}>
            {/* Vertical axis gridlines: 0 .. 1,000 ns */}
            {[0, 200, 400, 600, 800, 1000].map((v) => {
              const gx = bx + v * pxNs;
              return (
                <g key={v}>
                  <line
                    x1={gx}
                    y1={212}
                    x2={gx}
                    y2={684}
                    stroke={v === 0 ? C.muted : C.line}
                    strokeWidth={v === 0 ? 2.5 : 1.5}
                    strokeDasharray={v === 0 ? undefined : "6 6"}
                    opacity={v === 0 ? 0.8 : 0.65}
                  />
                  <text
                    x={gx}
                    y={714}
                    textAnchor="middle"
                    fontSize={22}
                    fill={C.muted}
                    fontFamily={MONO}
                    fontWeight={600}
                  >
                    {v === 1000 ? "1,000 ns" : `${v} ns`}
                  </text>
                </g>
              );
            })}

            {/* Horizontal baseline */}
            <line x1={bx} y1={684} x2={bx + 1000 * pxNs} y2={684} stroke={C.line} strokeWidth={2.5} />

            {/* Measured 897 ns target marker */}
            <g opacity={prog(t, T[2] + 0.5, 0.7)}>
              <line
                x1={x897}
                y1={206}
                x2={x897}
                y2={684}
                stroke={C.yellow}
                strokeWidth={2.5}
                strokeDasharray="8 6"
                opacity={0.75}
              />
              <text
                x={x897}
                y={196}
                textAnchor="middle"
                fontSize={22}
                fontWeight={800}
                fill={C.yellow}
                fontFamily={MONO}
              >
                W = 1 total: 897 ns
              </text>
            </g>

            {/* Vertical anchor alignment guides */}
            {a2K > 0.05 && (
              <line
                x1={x403}
                y1={r1y + barH}
                x2={x403}
                y2={r3y + barH}
                stroke={K.host}
                strokeWidth={2}
                strokeDasharray="5 5"
                opacity={0.55 * a2K}
              />
            )}
            {a3K > 0.05 && (
              <line
                x1={x646}
                y1={r2y + barH}
                x2={x646}
                y2={r3y + barH}
                stroke={C.cyan}
                strokeWidth={2}
                strokeDasharray="5 5"
                opacity={0.55 * a3K}
              />
            )}

            {/* ---- Row 1: Anchor 1 — On-die Host DSA NOP (403 ns) ---- */}
            <g opacity={clamp01(a1K * 1.4)}>
              <text x={64} y={r1y + 28} fontSize={26} fontWeight={800} fill={C.text}>
                1 · On-die Host DSA NOP
              </text>
              <text x={64} y={r1y + 56} fontSize={21} fill={C.muted}>
                0 PCIe hops · Xeon mesh/IIO/LLC
              </text>
              <rect
                x={bx}
                y={r1y}
                width={Math.max(0, 403 * pxNs * a1K)}
                height={barH}
                rx={8}
                fill={K.host}
                opacity={0.92}
              />
              {a1K > 0.65 && (
                <text
                  x={bx + (403 * pxNs) / 2}
                  y={r1y + barH / 2 + 1}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={24}
                  fontWeight={800}
                  fill={C.bg}
                  opacity={clamp01((a1K - 0.65) / 0.35)}
                >
                  Xeon on-die · 403 ns
                </text>
              )}
              <text
                x={x403 + 18}
                y={r1y + 30}
                fontSize={30}
                fontWeight={800}
                fill={K.host}
                opacity={clamp01((a1K - 0.55) / 0.45)}
              >
                403 ns
              </text>
              <text
                x={x403 + 18}
                y={r1y + 58}
                fontSize={22}
                fill={C.muted}
                opacity={clamp01((a1K - 0.55) / 0.45)}
              >
                ~201 ns one way
              </text>
            </g>

            {/* ---- Row 2: Anchor 2 — Raw BAR4 PCIe Read (646 ns) ---- */}
            {a2K > 0.01 && (
              <g opacity={clamp01(a2K * 1.4)}>
                <text x={64} y={r2y + 28} fontSize={26} fontWeight={800} fill={C.cyan}>
                  2 · Raw BAR4 PCIe Read
                </text>
                <text x={64} y={r2y + 56} fontSize={21} fill={C.muted}>
                  Across Gen5 ×4 · no card core
                </text>
                {/* Seg 1: 403 ns Xeon on-die */}
                <rect
                  x={bx}
                  y={r2y}
                  width={Math.max(0, 403 * pxNs * clamp01(a2K * 1.5))}
                  height={barH}
                  rx={8}
                  fill={K.host}
                  opacity={0.55}
                />
                {a2K > 0.45 && (
                  <text
                    x={bx + (403 * pxNs) / 2}
                    y={r2y + barH / 2 + 1}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontSize={23}
                    fontWeight={700}
                    fill={C.text}
                    opacity={0.9}
                  >
                    403 ns (Xeon on-die)
                  </text>
                )}
                {/* Seg 2: +243 ns Gen5 x4 + PEM0 */}
                {a2K > 0.35 && (
                  <rect
                    x={x403 + 2}
                    y={r2y}
                    width={Math.max(0, (243 * pxNs - 2) * clamp01((a2K - 0.35) / 0.65))}
                    height={barH}
                    rx={8}
                    fill={C.cyan}
                    opacity={0.92}
                  />
                )}
                {a2K > 0.72 && (
                  <g opacity={clamp01((a2K - 0.72) / 0.28)}>
                    <text
                      x={x403 + (243 * pxNs) / 2}
                      y={r2y + barH / 2 + 1}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontSize={25}
                      fontWeight={800}
                      fill={C.bg}
                    >
                      +243 ns
                    </text>
                    {/* Above-bar label + [INF] tag */}
                    <text
                      x={x403 + 2}
                      y={r2y - 16}
                      fontSize={22}
                      fontWeight={700}
                      fill={C.cyan}
                    >
                      Gen5 ×4 + PEM0
                    </text>
                    <InfTag x={x403 + 196} y={r2y - 38} size={20} />
                    <text x={x646 + 18} y={r2y + 30} fontSize={30} fontWeight={800} fill={C.cyan}>
                      646 ns
                    </text>
                    <text x={x646 + 18} y={r2y + 58} fontSize={22} fill={C.muted}>
                      ~323 ns one way
                    </text>
                  </g>
                )}
              </g>
            )}

            {/* ---- Row 3: Anchor 3 — Full Host <-> Card Core (897 ns) ---- */}
            {a3K > 0.01 && (
              <g opacity={clamp01(a3K * 1.4)}>
                <text x={64} y={r3y + 28} fontSize={26} fontWeight={800} fill={C.yellow}>
                  3 · Host ⇄ Card Core (W = 1)
                </text>
                <text x={64} y={r3y + 56} fontSize={21} fill={C.muted}>
                  MOVDIR64B + 1 card core WC
                </text>
                {/* Seg 1: 403 ns */}
                <rect
                  x={bx}
                  y={r3y}
                  width={Math.max(0, 403 * pxNs * clamp01(a3K * 1.8))}
                  height={barH}
                  rx={8}
                  fill={K.host}
                  opacity={0.55}
                />
                {/* Seg 2: +243 ns */}
                {a3K > 0.25 && (
                  <rect
                    x={x403 + 2}
                    y={r3y}
                    width={Math.max(0, (243 * pxNs - 2) * clamp01((a3K - 0.25) / 0.35))}
                    height={barH}
                    rx={8}
                    fill={C.cyan}
                    opacity={0.68}
                  />
                )}
                {/* Seg 3: +212 ns core/cache/IOMMU */}
                {a3K > 0.45 && (
                  <rect
                    x={x646 + 2}
                    y={r3y}
                    width={Math.max(0, (212 * pxNs - 2) * clamp01((a3K - 0.45) / 0.35))}
                    height={barH}
                    rx={8}
                    fill="#60a5fa"
                    opacity={0.94}
                  />
                )}
                {/* Seg 4: +40 ns card work */}
                {a3K > 0.72 && (
                  <rect
                    x={x858 + 2}
                    y={r3y}
                    width={Math.max(0, (x897 - x858 - 2) * clamp01((a3K - 0.72) / 0.28))}
                    height={barH}
                    rx={8}
                    fill={C.orange}
                    opacity={0.95}
                  />
                )}

                {/* Segment inside labels */}
                {a3K > 0.5 && (
                  <text
                    x={bx + (403 * pxNs) / 2}
                    y={r3y + barH / 2 + 1}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontSize={23}
                    fontWeight={700}
                    fill={C.text}
                  >
                    403 ns
                  </text>
                )}
                {a3K > 0.6 && (
                  <text
                    x={x403 + (243 * pxNs) / 2}
                    y={r3y + barH / 2 + 1}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontSize={23}
                    fontWeight={800}
                    fill={C.bg}
                  >
                    +243 ns
                  </text>
                )}
                {a3K > 0.78 && (
                  <g opacity={clamp01((a3K - 0.78) / 0.22)}>
                    <text
                      x={x646 + (212 * pxNs) / 2}
                      y={r3y + barH / 2 + 1}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontSize={24}
                      fontWeight={800}
                      fill={C.bg}
                    >
                      +212 ns
                    </text>
                    {/* Above Seg 3: core/cache/IOMMU [INF] */}
                    <text
                      x={x646 - 4}
                      y={r3y - 16}
                      fontSize={21}
                      fontWeight={700}
                      fill="#60a5fa"
                    >
                      core/cache/IOMMU
                    </text>
                    <InfTag x={x646 + 216} y={r3y - 38} size={19} />
                    {/* Below Seg 4: 40 ns card work */}
                    <line
                      x1={(x858 + x897) / 2}
                      y1={r3y + barH + 2}
                      x2={(x858 + x897) / 2}
                      y2={r3y + barH + 16}
                      stroke={C.orange}
                      strokeWidth={2.5}
                    />
                    <text
                      x={(x858 + x897) / 2}
                      y={r3y + barH + 40}
                      textAnchor="middle"
                      fontSize={21}
                      fontWeight={800}
                      fill={C.orange}
                    >
                      40 ns card work
                    </text>
                    {/* Total 897 ns callout */}
                    <text x={x897 + 18} y={r3y + 30} fontSize={34} fontWeight={900} fill={C.yellow}>
                      897 ns
                    </text>
                    <text x={x897 + 18} y={r3y + 58} fontSize={22} fontWeight={600} fill={C.text}>
                      ~429 ns one way
                    </text>
                  </g>
                )}
              </g>
            )}
          </g>
        )}
      </Stage>

      {/* ======================================================== HTML OVERLAYS */}
      {/* ---------------- Phase 1 HTML: Spec pills + Forward/Return callouts ---------------- */}
      {pRig > 0.001 && (
        <>
          {/* Cue 1 top callout inside PCIe band */}
          {fwdCardOp > 0.001 && (
            <div
              style={{
                position: "absolute",
                left: 626,
                top: 168,
                width: 648,
                opacity: fwdCardOp,
                transform: `translateY(${(1 - fwdCardOp) * -10}px)`,
                background: "rgba(11,16,32,0.92)",
                border: `2px solid ${K.fwd}`,
                borderRadius: 14,
                padding: "10px 20px",
                boxSizing: "border-box",
                textAlign: "center",
                fontFamily: FONT,
              }}
            >
              <div style={{ fontSize: 19, fontWeight: 800, letterSpacing: 1.5, color: K.fwd }}>
                FORWARD · DIRECT MMIO
              </div>
              <div style={{ fontSize: 23, fontWeight: 700, color: C.text, marginTop: 4 }}>
                <span style={{ fontFamily: MONO, color: K.fwd }}>MOVDIR64B</span> → 64 B cell · no doorbell, no DMA
              </div>
            </div>
          )}

          {/* Cue 1 bottom callout inside PCIe band */}
          {retCardOp > 0.001 && (
            <div
              style={{
                position: "absolute",
                left: 626,
                top: 694,
                width: 648,
                opacity: retCardOp,
                transform: `translateY(${(1 - retCardOp) * 10}px)`,
                background: "rgba(11,16,32,0.92)",
                border: `2px solid ${K.ret}`,
                borderRadius: 14,
                padding: "10px 20px",
                boxSizing: "border-box",
                textAlign: "center",
                fontFamily: FONT,
              }}
            >
              <div style={{ fontSize: 19, fontWeight: 800, letterSpacing: 1.5, color: K.ret }}>
                RETURN · WRITE-COMBINING
              </div>
              <div style={{ fontSize: 23, fontWeight: 700, color: C.text, marginTop: 4 }}>
                <span style={{ fontFamily: MONO, color: K.ret }}>4 × 16 B</span> → 64 B WC reply into host RAM
              </div>
            </div>
          )}

          {/* Cue 0–1 bottom spec pills along y = 808 */}
          <div
            style={{
              position: "absolute",
              left: 80,
              top: 808,
              width: 1760,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              opacity: pRig * showSpecs,
              fontFamily: FONT,
            }}
          >
            <span
              style={{
                fontSize: 23,
                fontWeight: 700,
                color: K.host,
                background: "rgba(20,27,46,0.92)",
                border: `2px solid ${K.host}`,
                borderRadius: 999,
                padding: "7px 22px",
              }}
            >
              Intel Xeon · core 40 @ 3.9 GHz · uncore 2.2 GHz
            </span>
            <span
              style={{
                fontSize: 23,
                fontWeight: 700,
                color: C.cyan,
                background: "rgba(20,27,46,0.92)",
                border: `2px solid ${C.cyan}`,
                borderRadius: 999,
                padding: "7px 22px",
              }}
            >
              PCIe Gen5 ×4 · 64-byte direct MMIO
            </span>
            <span
              style={{
                fontSize: 23,
                fontWeight: 700,
                color: K.card,
                background: "rgba(20,27,46,0.92)",
                border: `2px solid ${K.card}`,
                borderRadius: 999,
                padding: "7px 22px",
              }}
            >
              OCTEON 10 DPU · 24 Arm cores @ 2.0 GHz
            </span>
          </div>
        </>
      )}

      {/* ---------------- Phase 2 HTML: Latency Budget Header + Summary Pill ---------------- */}
      {pBudget > 0.001 && (
        <>
          <Txt x={60} y={132} w={1800} size={28} weight={800} color={C.text} style={{ opacity: pBudget }}>
            W = 1 ROUND-TRIP BUDGET:{" "}
            <span style={{ color: C.yellow }}>897 ns</span>{" "}
            <span style={{ color: C.muted, fontWeight: 600 }}>(~429 ns one way)</span> — 3 SAME-MACHINE MEASURED ANCHORS
          </Txt>

          {sumK > 0.001 && (
            <div
              style={{
                position: "absolute",
                left: 60,
                top: 760,
                width: 1800,
                opacity: sumK,
                transform: `translateY(${(1 - sumK) * 14}px)`,
                background: "rgba(20,27,46,0.94)",
                border: `2px solid ${C.yellow}`,
                borderLeft: `10px solid ${C.yellow}`,
                borderRadius: 14,
                padding: "14px 22px",
                boxSizing: "border-box",
                fontFamily: FONT,
                fontSize: 22,
                fontWeight: 600,
                color: C.text,
                lineHeight: 1.35,
                whiteSpace: "nowrap",
              }}
            >
              <span style={{ color: C.yellow, fontWeight: 800 }}>Every hop accounted for:</span>{" "}
              <span style={{ color: K.host, fontWeight: 800 }}>403 ns</span> Xeon on-die (~201 ns 1-way) +{" "}
              <span style={{ color: C.cyan, fontWeight: 800 }}>+243 ns</span> PCIe Gen5 ×4{" "}
              <InfPill size={19} style={{ marginRight: 4 }} /> +{" "}
              <span style={{ color: "#60a5fa", fontWeight: 800 }}>+212 ns</span> card core/cache{" "}
              <InfPill size={19} style={{ marginRight: 4 }} /> +{" "}
              <span style={{ color: C.orange, fontWeight: 800 }}>40 ns</span> card ={" "}
              <span style={{ color: C.yellow, fontWeight: 800 }}>897 ns</span>.
            </div>
          )}
        </>
      )}

      {/* ---------------- Phase 3 HTML: 3-Part Honest Progress Roadmap (Cue 4) ---------------- */}
      {pRoad > 0.001 && (
        <>
          <Txt x={60} y={134} w={1180} size={30} weight={800} color={C.text} style={{ opacity: pRoad }}>
            HONEST PROGRESS REPORT · 3 PARTS
          </Txt>
          <Pill
            x={1370}
            y={130}
            color={C.yellow}
            size={22}
            style={{ opacity: pRoad }}
          >
            Baseline: W = 1 · 897 ns (~429 ns 1-way)
          </Pill>

          {/* Card 1: Green — Nailed Down */}
          <Card
            x={60}
            y={204}
            w={570}
            h={616}
            border={C.green}
            style={{
              opacity: c1K,
              transform: `translateY(${(1 - c1K) * 22}px)`,
              display: "flex",
              flexDirection: "column",
              gap: 18,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span
                style={{
                  fontSize: 20,
                  fontWeight: 800,
                  letterSpacing: 1.5,
                  color: C.bg,
                  background: C.green,
                  borderRadius: 8,
                  padding: "4px 12px",
                }}
              >
                NAILED DOWN
              </span>
              <span style={{ fontSize: 22, fontWeight: 700, color: C.muted, fontFamily: MONO }}>PART 1 / 3</span>
            </div>
            <div style={{ fontSize: 27, fontWeight: 800, color: C.text, lineHeight: 1.2 }}>
              1 · Direct MMIO Nailed Down
            </div>
            <div
              style={{
                background: "rgba(67,197,158,0.10)",
                border: `1.5px solid ${C.green}`,
                borderRadius: 12,
                padding: "14px 18px",
              }}
            >
              <div style={{ fontSize: 50, fontWeight: 900, color: C.green, lineHeight: 1.05, fontVariantNumeric: "tabular-nums" }}>
                161.6 M/s
              </div>
              <div style={{ fontSize: 22, color: C.muted, marginTop: 4 }}>one-way direct MMIO ceiling</div>
            </div>
            <div style={{ fontSize: 24, color: C.text, lineHeight: 1.55, display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                • <strong style={{ color: C.green }}>161.6 M/s</strong> one-way (write-band conflict resolved)
              </div>
              <div>
                • <strong style={{ color: C.cyan }}>897 ns</strong> cache/DDIO &amp; <strong style={{ color: C.cyan }}>64 B</strong> coherence
              </div>
              <div>
                • <strong style={{ color: C.green }}>91–97 M/s</strong> open-loop bidirectional
              </div>
            </div>
          </Card>

          {/* Card 2: Red — Active Open Puzzle */}
          <Card
            x={675}
            y={204}
            w={570}
            h={616}
            border={C.red}
            style={{
              opacity: c2K,
              transform: `translateY(${(1 - c2K) * 22}px)`,
              display: "flex",
              flexDirection: "column",
              gap: 18,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span
                style={{
                  fontSize: 20,
                  fontWeight: 800,
                  letterSpacing: 1.5,
                  color: C.bg,
                  background: C.red,
                  borderRadius: 8,
                  padding: "4px 12px",
                }}
              >
                ACTIVE OPEN PUZZLE
              </span>
              <span style={{ fontSize: 22, fontWeight: 700, color: C.muted, fontFamily: MONO }}>PART 2 / 3</span>
            </div>
            <div style={{ fontSize: 27, fontWeight: 800, color: C.text, lineHeight: 1.2 }}>
              2 · Open Closed-Loop Puzzle
            </div>
            <div
              style={{
                background: "rgba(249,65,68,0.10)",
                border: `1.5px solid ${C.red}`,
                borderRadius: 12,
                padding: "14px 18px",
              }}
            >
              <div style={{ fontSize: 50, fontWeight: 900, color: C.red, lineHeight: 1.05, fontVariantNumeric: "tabular-nums" }}>
                ~13 M/s
              </div>
              <div style={{ fontSize: 22, color: C.muted, marginTop: 4 }}>4-core closed-loop stall</div>
            </div>
            <div style={{ fontSize: 24, color: C.text, lineHeight: 1.55, display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                • Why do <strong style={{ color: C.red }}>4 card cores</strong> stall at <strong style={{ color: C.red }}>~13 M/s</strong>?
              </div>
              <div>• Honest evidence from completed runs — root cause not yet claimed</div>
              <div>• Next isolation test queued on hardware</div>
            </div>
          </Card>

          {/* Card 3: Yellow — New Breakthrough */}
          <Card
            x={1290}
            y={204}
            w={570}
            h={616}
            border={C.yellow}
            style={{
              opacity: c3K,
              transform: `translateY(${(1 - c3K) * 22}px)`,
              display: "flex",
              flexDirection: "column",
              gap: 18,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span
                style={{
                  fontSize: 20,
                  fontWeight: 800,
                  letterSpacing: 1.5,
                  color: C.bg,
                  background: C.yellow,
                  borderRadius: 8,
                  padding: "4px 12px",
                }}
              >
                NEW BREAKTHROUGH
              </span>
              <span style={{ fontSize: 22, fontWeight: 700, color: C.muted, fontFamily: MONO }}>PART 3 / 3</span>
            </div>
            <div style={{ fontSize: 27, fontWeight: 800, color: C.text, lineHeight: 1.2 }}>
              3 · Zero-Host-CPU Card → DSA P2P
            </div>
            <div
              style={{
                background: "rgba(249,199,79,0.10)",
                border: `1.5px solid ${C.yellow}`,
                borderRadius: 12,
                padding: "14px 18px",
              }}
            >
              <div style={{ fontSize: 50, fontWeight: 900, color: C.yellow, lineHeight: 1.05, fontVariantNumeric: "tabular-nums" }}>
                31.3 GB/s
              </div>
              <div style={{ fontSize: 22, color: C.muted, marginTop: 4 }}>sustained P2P throughput</div>
            </div>
            <div style={{ fontSize: 24, color: C.text, lineHeight: 1.55, display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                • Card drives Intel DSA directly — <strong style={{ color: C.yellow }}>zero host CPU</strong>
              </div>
              <div>
                • <strong style={{ color: C.yellow }}>2.17 µs</strong> at <strong style={{ color: C.text }}>W = 1</strong>
              </div>
              <div>
                • <strong style={{ color: C.green }}>0</strong> drops over <strong style={{ color: C.yellow }}>182.9M+</strong> ops
              </div>
            </div>
          </Card>
        </>
      )}
    </>
  );
}


export const S01: SceneDef = {
  id: "S01",
  title: "Direct PCIe path & 897 ns baseline",
  stage: undefined,
  source: "Xeon host ⇄ OCTEON 10 DPU · PCIe Gen5 ×4 · advisor progress report",
  dur: DUR,
  cues,
  C: Body,
};
