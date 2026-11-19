import {
  Axes,
  C,
  Cue,
  FONT,
  HLine,
  K,
  MONO,
  SceneDef,
  Series,
  Stage,
  fade,
  linScale,
  prog,
  useT,
} from "../lib";
import { InfTag, PathTag, StatusBadge, TimeBlock } from "./dsa";

// <cues>
const cues: Cue[] = [
  [0.60, 17.24, "Yet high-concurrency `4 KiB` copies hit a sudden cliff: for `W >= 32`, reverse-P2P stalls at **3.74 M/s (15.3 GB/s)**—half of Host Memzone's **30.0 GB/s**!", "Just when victory seemed complete, high-concurrency four-kilobyte copies hit a sudden cliff: at concurrency thirty-two and above, reverse P 2 P stalls at three point seven four million ops per second, or fifteen point three gigabytes per second, half the host-completion baseline."], // 0
  [17.59, 32.85, "Rule out link limits: the exact same bidirectional P2P path drives `NOP` to **23.01 M/s** (`43.5 ns/op`)—**6.1x** faster than `3.74 M/s`.", "First, we rule out link saturation: the exact same bidirectional P 2 P path already runs N O P at twenty-three point zero one million ops per second, or forty-three point five nanoseconds per op, over six times faster than the stall."], // 1
  [33.20, 45.98, "Rule out card doorbells: raising `min_batch = 1 -> 8 -> 16` at `W = 64` cuts doorbells **16x** (`3.54M -> 0.22M`), yet rate stays flat at **3.73 M/s (15.28 GB/s)**.", "Next, we rule out card-side doorbells: raising minimum submit batching from one to eight to sixteen cuts doorbell writes sixteen-fold, yet throughput stays flat at three point seven three million ops per second."], // 2
  [46.33, 58.17, "Rule out engine slowdown: single-op marginal slope across `4 KiB -> 64 KiB -> 1 MiB` on `BAR4` remains **30.8–31.9 GB/s** (`133 -> 128 ns / 4 KiB`).", "Finally, we check the copy engine itself: across single-op sizes from four kilobytes to one megabyte, marginal copy bandwidth stays at thirty point eight to thirty-one point nine gigabytes per second."], // 3
  [58.52, 75.36, "Subtraction nails it: `267.5 - 136.6 = 130.9 ns/op`—we infer a per-copy **~131 ns drain bubble** waiting for `dst` writes before each `BAR4` completion!", "Subtracting the one hundred thirty-six point six nanosecond copy baseline from two hundred sixty-seven point five leaves one hundred thirty-one nanoseconds per op. We infer a per-copy drain bubble waiting for host writes to drain before issuing each reverse P 2 P completion."], // 4
];
const DUR = 76.76; // <dur>
// </cues>

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  // Beats 0..3 share the left scaling chart + right 3 elimination cards; Beat 4 crossfades to full-width Root-Cause & Bubble Timeline
  const opB03 = fade(t, T[0], T[4] - 0.1, 0.5);
  const kChartHost = prog(t, T[0] + 0.2, 1.2);
  const kChartBar4 = prog(t, T[0] + 0.8, 1.4);
  const kCliff = prog(t, T[0] + 2.2, 0.7);

  const opE1 = fade(t, T[1], T[4] - 0.1, 0.5);
  const kE1Bar = prog(t, T[1] + 0.4, 0.9);
  const opE2 = fade(t, T[2], T[4] - 0.1, 0.5);
  const kE2Row = prog(t, T[2] + 0.4, 0.9);
  const opE3 = fade(t, T[3], T[4] - 0.1, 0.5);
  const kE3Bar = prog(t, T[3] + 0.4, 0.9);

  const opB4 = prog(t, T[4], 0.6);
  const kEq = prog(t, T[4] + 0.3, 0.8);
  const kPipe = prog(t, T[4] + 1.0, 1.2);

  // Chart scales (left panel: x = 70..1035, y = 148..845)
  const x0 = 165;
  const x1 = 985;
  const y0 = 760;
  const y1 = 230;
  const sx = linScale(0, 100, x0, x1);
  const sy = linScale(0, 35, y0, y1);

  const hostPts: [number, number][] = [
    [1, 1.31],
    [8, 7.38],
    [32, 22.33],
    [64, 29.97],
    [96, 29.98],
  ];
  const bar4GreenPts: [number, number][] = [
    [1, 1.89],
    [8, 11.65],
    [32, 15.37],
  ];
  const bar4RedPts: [number, number][] = [
    [32, 15.37],
    [64, 15.28],
    [96, 15.31],
  ];

  return (
    <>
      <Stage>
        {/* ==================== BEATS 0..3: Scaling Chart + 3 Elimination Cards ==================== */}
        {opB03 > 0.01 && (
          <g opacity={opB03}>
            {/* Left Panel: Dual-line scaling chart */}
            <rect x={70} y={145} width={965} height={705} rx={16} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={102} y={188} fill={C.text} fontSize={24} fontWeight={800} fontFamily={FONT}>
              4 KiB MEMMOVE (CC = 0) Scaling
            </text>

            {/* Legend */}
            <line x1={570} y1={180} x2={612} y2={180} stroke={C.cyan} strokeWidth={4} strokeDasharray="8 5" />
            <text x={622} y={187} fill={C.cyan} fontSize={19} fontWeight={700} fontFamily={FONT}>
              Host Memzone
            </text>
            <line x1={790} y1={180} x2={832} y2={180} stroke={K.bubble} strokeWidth={5} />
            <text x={842} y={187} fill={K.bubble} fontSize={19} fontWeight={700} fontFamily={FONT}>
              Card BAR4 P2P
            </text>

            {/* Red shaded plateau region for W >= 32 */}
            {kCliff > 0.05 && (
              <g opacity={kCliff}>
                <rect
                  x={sx(32)}
                  y={sy(30.5)}
                  width={sx(96) - sx(32)}
                  height={sy(14.5) - sy(30.5)}
                  rx={10}
                  fill={K.bubble}
                  opacity={0.11}
                />
                {/* Vertical gap arrow at W=64 */}
                <line x1={sx(64)} y1={sy(29.97) + 8} x2={sx(64)} y2={sy(15.28) - 8} stroke={K.bubble} strokeWidth={3} strokeDasharray="6 5" />
                <PathTag cx={sx(64)} cy={sy(22.6)} text="Half-Rate Cliff: 15.3 vs 30.0 GB/s" color={K.bubble} size={18} />
                <text x={sx(64)} y={sy(15.28) + 34} textAnchor="middle" fill={K.bubble} fontSize={19} fontWeight={800} fontFamily={MONO}>
                  Stalled 3.74 M/s · 267.5 ns/op
                </text>
              </g>
            )}

            <Axes
              x0={x0}
              x1={x1}
              y0={y0}
              y1={y1}
              sx={sx}
              sy={sy}
              xticks={[[1, "1"], [8, "8"], [32, "32"], [64, "64"], [96, "96"]]}
              yticks={[[0, "0"], [10, "10"], [20, "20"], [30, "30"]]}
              xlabel="In-Flight Window W (ops)"
              ylabel="Bandwidth (GB/s)"
              size={20}
            />

            <HLine y={sy(29.98)} x0={x0} x1={x1} color={C.cyan} opacity={0.45 * kChartHost} />
            <HLine y={sy(15.31)} x0={x0} x1={x1} color={K.bubble} opacity={0.55 * kChartBar4} />
            {kChartHost > 0.7 && (
              <text x={x1 - 6} y={sy(29.98) - 12} textAnchor="end" fill={C.cyan} fontSize={18} fontWeight={700} fontFamily={MONO}>
                30.0 GB/s (7.32 M/s)
              </text>
            )}

            <Series pts={hostPts} sx={sx} sy={sy} k={kChartHost} color={C.cyan} w={4} dash="10 6" r={6} />
            <Series pts={bar4GreenPts} sx={sx} sy={sy} k={Math.min(1, kChartBar4 * 1.8)} color={K.rev} w={5} r={7} />
            <Series pts={bar4RedPts} sx={sx} sy={sy} k={Math.max(0, (kChartBar4 - 0.45) / 0.55)} color={K.bubble} w={5} r={7} />

            {/* Value callouts at W=8 */}
            {kChartBar4 > 0.6 && (
              <g opacity={prog(t, T[0] + 1.4, 0.5)}>
                <text x={sx(8) + 12} y={sy(11.65) - 12} fill={K.rev} fontSize={18} fontWeight={700} fontFamily={MONO}>
                  11.65 (+58%)
                </text>
              </g>
            )}

            {/* ==================== RIGHT PANEL: 3 Elimination Cards ==================== */}
            {/* Card 1 (Beat 1): Bidirectional Link Not Saturated */}
            {opE1 > 0.01 && (
              <g opacity={opE1}>
                <rect x={1065} y={145} width={785} height={212} rx={16} fill={C.panel} stroke={K.rev} strokeWidth={2.5} />
                <StatusBadge cx={1108} cy={186} ok r={18} />
                <text x={1140} y={194} fill={K.rev} fontSize={23} fontWeight={800} fontFamily={FONT}>
                  Ruled Out 1: Link Not Saturated
                </text>
                <text x={1820} y={194} textAnchor="end" fill={C.yellow} fontSize={21} fontWeight={800} fontFamily={MONO}>
                  6.1x Headroom
                </text>

                {/* Mini comparison bars */}
                <text x={1095} y={248} fill={C.text} fontSize={19} fontFamily={FONT}>
                  Same-Path NOP
                </text>
                <rect x={1265} y={226} width={420 * kE1Bar} height={30} rx={6} fill={K.rev} />
                <text x={1278 + 420 * kE1Bar} y={248} fill={K.rev} fontSize={20} fontWeight={800} fontFamily={MONO}>
                  23.01 M/s
                </text>

                <text x={1095} y={298} fill={C.muted} fontSize={19} fontFamily={FONT}>
                  4 KiB Plateau
                </text>
                <rect x={1265} y={276} width={420 * (3.74 / 23.01) * kE1Bar} height={30} rx={6} fill={K.bubble} />
                <text x={1278 + 420 * (3.74 / 23.01) * kE1Bar} y={298} fill={K.bubble} fontSize={20} fontWeight={700} fontFamily={MONO}>
                  3.74 M/s (267.5 ns/op)
                </text>

                <text x={1095} y={338} fill={C.muted} fontSize={17} fontFamily={MONO}>
                  NOP takes 43.5 ns/op → both P2P directions have ample headroom
                </text>
              </g>
            )}

            {/* Card 2 (Beat 2): Card Doorbell Not Bottleneck */}
            {opE2 > 0.01 && (
              <g opacity={opE2}>
                <rect x={1065} y={380} width={785} height={225} rx={16} fill={C.panel} stroke={K.rev} strokeWidth={2.5} />
                <StatusBadge cx={1108} cy={420} ok r={18} />
                <text x={1140} y={428} fill={K.rev} fontSize={23} fontWeight={800} fontFamily={FONT}>
                  Ruled Out 2: Not Card Doorbells
                </text>
                <text x={1820} y={428} textAnchor="end" fill={C.cyan} fontSize={20} fontWeight={700} fontFamily={MONO}>
                  min_batch (W=64)
                </text>

                {/* Mini 3-row table */}
                <rect x={1095} y={448} width={725} height={135} rx={10} fill="#0e1526" stroke={C.line} strokeWidth={1.5} />
                <text x={1120} y={478} fill={C.muted} fontSize={18} fontWeight={700} fontFamily={MONO}>min_batch</text>
                <text x={1285} y={478} fill={C.muted} fontSize={18} fontWeight={700} fontFamily={MONO}>Doorbells (16x down)</text>
                <text x={1565} y={478} fill={C.muted} fontSize={18} fontWeight={700} fontFamily={MONO}>Measured Rate / BW</text>
                <line x1={1110} y1={488} x2={1805} y2={488} stroke={C.line} strokeWidth={1.5} />

                {[
                  { mb: "1", db: "3.54M (1.0x)", rate: "3.73 M/s · 15.28 GB/s" },
                  { mb: "8", db: "0.44M (8.0x ↓)", rate: "3.73 M/s · 15.28 GB/s" },
                  { mb: "16", db: "0.22M (16x ↓)", rate: "3.73 M/s · 15.28 GB/s" },
                ].map((r, i) => (
                  <g key={r.mb} opacity={kE2Row}>
                    <text x={1120} y={516 + i * 28} fill={C.text} fontSize={19} fontWeight={700} fontFamily={MONO}>
                      {r.mb}
                    </text>
                    <text x={1285} y={516 + i * 28} fill={C.cyan} fontSize={19} fontWeight={700} fontFamily={MONO}>
                      {r.db}
                    </text>
                    <text x={1565} y={516 + i * 28} fill={K.bubble} fontSize={19} fontWeight={700} fontFamily={MONO}>
                      {r.rate}
                    </text>
                  </g>
                ))}
              </g>
            )}

            {/* Card 3 (Beat 3): Single-Engine Copy BW Intact */}
            {opE3 > 0.01 && (
              <g opacity={opE3}>
                <rect x={1065} y={628} width={785} height={222} rx={16} fill={C.panel} stroke={K.rev} strokeWidth={2.5} />
                <StatusBadge cx={1108} cy={668} ok r={18} />
                <text x={1140} y={676} fill={K.rev} fontSize={23} fontWeight={800} fontFamily={FONT}>
                  Ruled Out 3: Engine Copy Intact
                </text>
                <text x={1820} y={676} textAnchor="end" fill={K.rev} fontSize={22} fontWeight={800} fontFamily={MONO}>
                  30.8–31.9 GB/s
                </text>

                <text x={1095} y={726} fill={C.text} fontSize={19} fontFamily={FONT}>
                  Card BAR4 single-op marginal slope (4 KiB → 64 KiB → 1 MiB):
                </text>

                {/* Slope progress bar */}
                <rect x={1095} y={748} width={725} height={42} rx={8} fill="#0e1526" stroke={C.line} strokeWidth={1.5} />
                <rect x={1095} y={748} width={700 * kE3Bar} height={42} rx={8} fill={K.rev} opacity={0.28} />
                <text x={1115} y={776} fill={K.rev} fontSize={20} fontWeight={800} fontFamily={MONO}>
                  133.1 → 128.2 ns / 4 KiB  ≡  30.8–31.9 GB/s (Full Speed!)
                </text>

                <text x={1095} y={826} fill={C.muted} fontSize={18} fontFamily={FONT}>
                  dsa2 copy engine is intact → bottleneck is the per-op completion write!
                </text>
              </g>
            )}
          </g>
        )}

        {/* ==================== BEAT 4: Root-Cause Subtraction & Bubble Timeline ==================== */}
        {opB4 > 0.01 && (
          <g opacity={opB4}>
            {/* Top Equation Spotlight Card */}
            <rect x={70} y={145} width={1780} height={235} rx={18} fill={C.panel} stroke={K.bubble} strokeWidth={2.5} />
            <text x={110} y={194} fill={C.text} fontSize={26} fontWeight={800} fontFamily={FONT}>
              Root-Cause Subtraction: Each Reverse-P2P Completion Adds ~131 ns Drain Bubble
            </text>
            <InfTag x={1610} y={164} text="[INF] Inference" />

            {/* Visual Subtraction Equation */}
            <g opacity={kEq}>
              {/* Box 1: 267.5 ns */}
              <rect x={110} y={225} width={450} height={125} rx={14} fill="#1d1422" stroke={K.bubble} strokeWidth={2} />
              <text x={335} y={282} textAnchor="middle" fill={K.bubble} fontSize={46} fontWeight={800} fontFamily={MONO}>
                267.5 ns
              </text>
              <text x={335} y={325} textAnchor="middle" fill={C.muted} fontSize={20} fontFamily={FONT}>
                BAR4 Measured / op (3.74 M/s)
              </text>

              {/* Minus sign */}
              <text x={615} y={298} textAnchor="middle" fill={C.text} fontSize={52} fontWeight={800} fontFamily={MONO}>
                −
              </text>

              {/* Box 2: 136.6 ns */}
              <rect x={670} y={225} width={450} height={125} rx={14} fill="#101e30" stroke={C.cyan} strokeWidth={2} />
              <text x={895} y={282} textAnchor="middle" fill={C.cyan} fontSize={46} fontWeight={800} fontFamily={MONO}>
                136.6 ns
              </text>
              <text x={895} y={325} textAnchor="middle" fill={C.muted} fontSize={20} fontFamily={FONT}>
                Host Copy Baseline (7.32 M/s)
              </text>

              {/* Equals sign */}
              <text x={1175} y={298} textAnchor="middle" fill={C.text} fontSize={52} fontWeight={800} fontFamily={MONO}>
                =
              </text>

              {/* Box 3: 130.9 ns */}
              <rect x={1230} y={225} width={580} height={125} rx={14} fill="#26131a" stroke={C.yellow} strokeWidth={3} />
              <text x={1520} y={282} textAnchor="middle" fill={C.yellow} fontSize={46} fontWeight={800} fontFamily={MONO}>
                130.9 ns (~131 ns)
              </text>
              <text x={1520} y={325} textAnchor="middle" fill={C.text} fontSize={21} fontWeight={700} fontFamily={FONT}>
                Per-op dst → BAR4 Drain Bubble [INF]
              </text>
            </g>

            {/* Bottom Repeating Pipeline Timeline */}
            <rect x={70} y={408} width={1780} height={442} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={110} y={456} fill={C.text} fontSize={26} fontWeight={800} fontFamily={FONT}>
              Single-Engine Pipeline: Why Bandwidth Halves (137 / (137 + 131) ≈ 51%)
            </text>

            {/* Track 1: Host Memzone (continuous) */}
            <text x={110} y={534} fill={C.cyan} fontSize={22} fontWeight={700} fontFamily={FONT}>
              Host Memzone
            </text>
            <text x={110} y={562} fill={C.muted} fontSize={19} fontFamily={MONO}>
              29.98 GB/s
            </text>
            {[0, 1, 2, 3, 4].map((i) => {
              const bx = 315 + i * 295;
              const bw = 285 * Math.min(1, Math.max(0, kPipe * 1.4 - i * 0.1));
              return (
                <TimeBlock
                  key={`h-${i}`}
                  x={bx}
                  y={496}
                  w={bw}
                  h={76}
                  label={`4 KiB Copy #${i + 1}`}
                  sub="~137 ns"
                  color={C.cyan}
                />
              );
            })}

            {/* Track 2: Card BAR4 (copy + bubble repeating) */}
            <text x={110} y={668} fill={K.bubble} fontSize={22} fontWeight={700} fontFamily={FONT}>
              Card BAR4 P2P
            </text>
            <text x={110} y={696} fill={C.muted} fontSize={19} fontFamily={MONO}>
              15.31 GB/s (Half!)
            </text>
            {[0, 1, 2].map((i) => {
              const baseX = 315 + i * 575;
              const copyW = 285 * Math.min(1, Math.max(0, kPipe * 1.5 - i * 0.2));
              const bubW = 275 * Math.min(1, Math.max(0, kPipe * 1.5 - i * 0.2 - 0.12));
              const maxRight = 1805;
              const clampedCopyW = Math.max(0, Math.min(copyW, maxRight - baseX));
              const clampedBubW = Math.max(0, Math.min(bubW, maxRight - (baseX + 292)));
              return (
                <g key={`b-${i}`}>
                  {clampedCopyW > 4 && (
                    <TimeBlock
                      x={baseX}
                      y={630}
                      w={clampedCopyW}
                      h={76}
                      label={`4 KiB Copy #${i + 1}`}
                      sub="~137 ns"
                      color={C.cyan}
                    />
                  )}
                  {i < 2 && clampedBubW > 4 && (
                    <TimeBlock
                      x={baseX + 292}
                      y={630}
                      w={clampedBubW}
                      h={76}
                      label="BAR4 32 B Drain Bubble"
                      sub="+131 ns [INF]"
                      color={K.bubble}
                      hatch
                    />
                  )}
                </g>
              );
            })}

            {/* Bottom summary takeaway */}
            <rect x={110} y={748} width={1700} height={72} rx={12} fill="#14192a" stroke={C.yellow} strokeWidth={2} />
            <text x={960} y={792} textAnchor="middle" fill={C.yellow} fontSize={22} fontWeight={700} fontFamily={FONT}>
              Key Insight: One BAR4 completion per 4 KiB copy is too frequent → Next: DSA Batch amortizes N copies into 1 completion!
            </text>
          </g>
        )}
      </Stage>
    </>
  );
}

export const S05: SceneDef = {
  id: "S05",
  title: "Surprise: Why High-W 4 KiB Halved",
  stage: 4,
  source: "W >= 32 Plateaus at 15.3 GB/s · Isolating 131 ns Bubble",
  dur: DUR,
  cues,
  C: Body,
};
