import React from "react";
import { Arrow, C, Code, Cue, FONT, K, MONO, Note, SceneDef, Stage, appear, clamp01, countUp, fade, lerp, pop, prog, useT } from "../lib";

/** Colour with alpha (hex colours only). */
const hexA = (col: string, o: number) =>
  col + Math.round(clamp01(o) * 255).toString(16).padStart(2, "0");

/** Centred HTML text at (cx, cy). */
function Centre({
  cx,
  cy,
  fs,
  color,
  opacity,
  weight = 800,
  children,
}: {
  cx: number;
  cy: number;
  fs: number;
  color: string;
  opacity: number;
  weight?: number;
  children: React.ReactNode;
}) {
  if (opacity <= 0.001) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: cx - 700,
        width: 1400,
        top: cy - fs * 0.6,
        fontSize: fs,
        lineHeight: 1.2,
        fontWeight: weight,
        color,
        opacity,
        textAlign: "center",
        fontVariantNumeric: "tabular-nums",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </div>
  );
}

// <cues>
const cues: Cue[] = [
  [0.60, 8.78, "Before working on the way back, we nearly fooled ourselves: the inline path first measured **1.87 µs**.", "Before working on the way back, we nearly fooled ourselves. The inline path first measured one point eight seven microseconds."], // 0
  [9.13, 16.01, "But **1 / message rate** said **2.41 µs**. At W=1 they must agree, so one of them was wrong.", "But one over the message rate said two point four one. At a window of one those must agree, so one of them was wrong."], // 1
  [16.36, 27.06, "The host polls an SDP counter with an **MMIO read**, then reads the clock with **RDTSC**, which doesn't wait for loads.", "The host polls an S D P counter with an M M I O read, then reads the clock with R D T S C. And R D T S C doesn't wait for earlier loads."], // 2
  [27.41, 34.63, "So the timestamp was taken **before the read returned**: about **0.5 µs** shaved off every round trip.", "So the arrival timestamp was taken before the read had returned, shaving about half a microsecond off every round trip."], // 3
  [34.98, 48.90, "Fix: **RDTSCP** waits for earlier loads. **LFENCE; RDTSC** gives the same **2.41 µs**. Cost: **20.9** vs **22.0** TSC ticks.", "The fix is R D T S C P, which waits for earlier loads. An L fence plus R D T S C gives the same two point four one microseconds; R D T S C P is one instruction, and slightly cheaper."], // 4
];
const DUR = 50.30; // <dur>
// </cues>

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  // ------------------------------------------------ headline numbers: big → side by side → top strip
  const k1 = prog(t, T[1], 0.8);
  const k2 = prog(t, T[2], 0.8);
  const fs = lerp(lerp(160, 130, k1), 64, k2);
  const numY = lerp(lerp(560, 470, k1), 160, k2);
  const aX = lerp(lerp(960, 560, k1), 560, k2);
  const labFs = lerp(30, 24, k2);
  const labY = numY + fs * 0.62 + labFs * 0.6;
  const fixK = prog(t, T[4] + 0.8, 0.6);

  // ------------------------------------------------ CPU timeline (c2+)
  const XI = 440;
  const XR = 1480;
  const XA = 520;
  const LA = 280;
  const LB = 390;
  const LH = 60;
  const tlA = prog(t, T[2] + 0.5, 0.5);
  const kA = prog(t, T[2] + 0.8, 2.6);
  const slide = prog(t, T[2] + 6.2, 1.2);
  const rdtscIn = prog(t, T[2] + 3.8, 0.4);
  const rdtscOut = 1 - prog(t, T[4] - 0.1, 0.4);
  const rdtscX = lerp(XR + 16, XA, slide);
  const kGap = prog(t, T[3] + 0.3, 1.0);
  const gapX = lerp(XR, XA + 120, kGap);
  const pIn = prog(t, T[4] + 0.2, 0.4);

  // ------------------------------------------------ RTT bars (c3+)
  const BX = 420;
  const PX = 260;
  const kM = prog(t, T[3] + 1.8, 0.9);
  const kT = prog(t, T[3] + 2.6, 1.0);
  const kMiss = prog(t, T[3] + 3.6, 0.6);
  const mUs = lerp(1.87, 2.41, fixK);
  const barsA = prog(t, T[3] + 1.5, 0.5);

  // ------------------------------------------------ stopwatch (c0)
  const swA = fade(t, T[0] + 0.2, T[1]);
  const hand = prog(t, T[0] + 0.8, 1.6) * 0.78 * 2 * Math.PI;

  const table: [string, string, string, string, number][] = [
    ["rdtsc", "14.7", "1.87 µs ✗", K.bad, T[4] + 3.6],
    ["lfence; rdtsc", "22.0", "2.41 µs ✓", K.ok, T[4] + 4.8],
    ["rdtscp", "20.9", "2.41 µs ✓", K.ok, T[4] + 8.5],
  ];
  const hlRow = prog(t, T[4] + 9.5, 0.5);

  return (
    <>
      <Stage>
        {/* ---------------- stopwatch (c0) */}
        {swA > 0.01 && (
          <g opacity={swA}>
            <rect x={946} y={174} width={28} height={20} rx={4} fill={K.timer} />
            <circle cx={960} cy={295} r={98} fill={hexA(K.timer, 0.08)} stroke={K.timer} strokeWidth={7} />
            {Array.from({ length: 12 }, (_, i) => {
              const a = (i / 12) * 2 * Math.PI;
              return <line key={i} x1={960 + 78 * Math.sin(a)} y1={295 - 78 * Math.cos(a)} x2={960 + 88 * Math.sin(a)} y2={295 - 88 * Math.cos(a)} stroke={K.timer} strokeWidth={3} opacity={0.7} />;
            })}
            <line x1={960} y1={295} x2={960 + 70 * Math.sin(hand)} y2={295 - 70 * Math.cos(hand)} stroke={C.text} strokeWidth={5} strokeLinecap="round" />
            <circle cx={960} cy={295} r={8} fill={C.text} />
          </g>
        )}

        {/* ---------------- CPU timeline (c2+) */}
        {tlA > 0.01 && (
          <g opacity={tlA} fontFamily={FONT}>
            {/* lane labels */}
            <text x={60} y={LA + 26} fontSize={26} fontWeight={800} fill={K.uc}>
              ① load OUT_CNTS
            </text>
            <text x={60} y={LA + 56} fontSize={22} fill={C.muted} fontFamily={MONO}>
              MMIO read · uncached
            </text>
            <text x={60} y={LB + 26} fontSize={26} fontWeight={800} fill={K.timer}>
              ② read the clock
            </text>
            <text x={60} y={LB + 56} fontSize={22} fill={C.muted} fontFamily={MONO}>
              arrival timestamp
            </text>
            {/* lanes */}
            <rect x={XI - 20} y={LA - 6} width={1420} height={LH + 12} rx={8} fill="rgba(139,149,173,0.04)" stroke={C.line} strokeWidth={1.5} />
            <rect x={XI - 20} y={LB - 6} width={1420} height={LH + 12} rx={8} fill="rgba(139,149,173,0.04)" stroke={C.line} strokeWidth={1.5} />
            {/* MMIO read bar */}
            {kA > 0 && (
              <rect x={XI} y={LA} width={(XR - XI) * kA} height={LH} rx={8} fill={hexA(K.uc, 0.28)} stroke={K.uc} strokeWidth={3} />
            )}
            <text x={(XI + XR) / 2} y={LA + LH / 2} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={700} fill={C.text} opacity={clamp01((kA - 0.6) / 0.3)}>
              MMIO read: a PCIe round trip to the card's SDP
            </text>
            {/* return marker */}
            <g opacity={prog(t, T[2] + 3.3, 0.4)}>
              <line x1={XR} y1={LA - 16} x2={XR} y2={560} stroke={K.uc} strokeWidth={3} strokeDasharray="8 7" />
              <text x={XR} y={596} textAnchor="middle" fontSize={24} fontWeight={700} fill={K.uc}>
                read returns
              </text>
            </g>
            {/* expected position (ghost) */}
            <g opacity={prog(t, T[2] + 6.2, 0.4) * (1 - pIn)}>
              <rect x={XR + 16} y={LB} width={150} height={LH} rx={8} fill="none" stroke={C.muted} strokeWidth={2.5} strokeDasharray="8 6" />
              <text x={XR + 91} y={LB + LH / 2} textAnchor="middle" dominantBaseline="central" fontSize={22} fill={C.muted}>
                expected
              </text>
            </g>
            {/* missing time (c3) */}
            {kGap > 0 && (
              <g opacity={1 - prog(t, T[4] - 0.1, 0.4)}>
                <rect x={gapX} y={LB + 6} width={XR - gapX} height={LH - 12} fill="url(#hatchRed)" opacity={0.85} />
                <g opacity={prog(t, T[3] + 1.1, 0.5)}>
                  <rect x={(XA + 120 + XR) / 2 - 150} y={LB + LH / 2 - 24} width={300} height={48} rx={10} fill={C.bg} opacity={0.92} stroke={K.bad} strokeWidth={2} />
                  <text x={(XA + 120 + XR) / 2} y={LB + LH / 2} textAnchor="middle" dominantBaseline="central" fontSize={30} fontWeight={800} fill={K.bad}>
                    ~0.5 µs missing
                  </text>
                </g>
              </g>
            )}
            {/* RDTSC block: drawn where you'd expect it, then moved to where it really runs */}
            {rdtscIn * rdtscOut > 0.01 && (
              <g opacity={rdtscIn * rdtscOut}>
                <rect x={rdtscX} y={LB} width={120} height={LH} rx={8} fill={K.timer} />
                <text x={rdtscX + 60} y={LB + LH / 2} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={C.bg} fontFamily={MONO}>
                  RDTSC
                </text>
              </g>
            )}
            {/* RDTSCP block (c4) */}
            {pIn > 0.01 && (
              <g opacity={pIn} transform={`translate(${XR + 91} ${LB + LH / 2}) scale(${pop(t, T[4] + 0.2, 0.5)}) translate(${-(XR + 91)} ${-(LB + LH / 2)})`}>
                <rect x={XR + 16} y={LB} width={150} height={LH} rx={8} fill={K.ok} />
                <text x={XR + 91} y={LB + LH / 2} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={800} fill={C.bg} fontFamily={MONO}>
                  RDTSCP
                </text>
              </g>
            )}
            {/* "executes early" arrow + label */}
            <Arrow x1={XR + 60} y1={LB + LH + 26} x2={XA + 60} y2={LB + LH + 26} color={K.timer} w={3.5} head={14} k={slide} opacity={rdtscOut} />
            <text x={(XI + XR) / 2} y={LB + LH + 70} textAnchor="middle" fontSize={26} fontWeight={700} fill={K.timer} opacity={prog(t, T[2] + 7.0, 0.5) * rdtscOut}>
              RDTSC runs early: it doesn't wait for the earlier load
            </text>
            <text x={(XI + XR) / 2} y={LB + LH + 70} textAnchor="middle" fontSize={26} fontWeight={700} fill={K.ok} opacity={prog(t, T[4] + 0.4, 0.5)}>
              RDTSCP waits for earlier loads, so it runs after the read returns
            </text>
            {/* time axis */}
            <Arrow x1={XI - 20} y1={560} x2={1850} y2={560} color={C.dim} w={2.5} head={12} />
            <line x1={XI} y1={552} x2={XI} y2={568} stroke={C.muted} strokeWidth={2.5} />
            <text x={XI} y={596} textAnchor="middle" fontSize={24} fill={C.muted}>
              load issued
            </text>
            <text x={1850} y={596} textAnchor="end" fontSize={24} fill={C.muted}>
              time
            </text>
          </g>
        )}

        {/* ---------------- RTT bars (c3+) */}
        {barsA > 0.01 && (
          <g opacity={barsA} fontFamily={FONT}>
            {/* measured */}
            <text x={60} y={683} fontSize={26} fontWeight={800} fill={K.timer} opacity={1 - fixK}>
              measured (RDTSC)
            </text>
            <text x={60} y={683} fontSize={26} fontWeight={800} fill={K.ok} opacity={fixK}>
              measured (RDTSCP)
            </text>
            {kM > 0 && <rect x={BX} y={650} width={mUs * PX * kM} height={50} rx={6} fill={fixK > 0.5 ? K.ok : K.timer} opacity={0.9} />}
            {kMiss > 0 && (
              <g opacity={kMiss * (1 - fixK)}>
                <rect x={BX + 1.87 * PX} y={650} width={0.54 * PX} height={50} fill="url(#hatchRed)" stroke={K.bad} strokeWidth={2} strokeDasharray="6 5" />
                <rect x={BX + 2.14 * PX - 52} y={658} width={104} height={34} rx={8} fill={C.bg} opacity={0.9} />
                <text x={BX + 2.14 * PX} y={676} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={800} fill={K.bad}>
                  ~0.5
                </text>
              </g>
            )}
            <text x={BX + 1.87 * PX - 14} y={686} textAnchor="end" fontSize={32} fontWeight={800} fill={C.bg} opacity={clamp01((kM - 0.6) / 0.4) * (1 - fixK)}>
              1.87 µs
            </text>
            <text x={BX + 2.41 * PX + 16} y={686} fontSize={32} fontWeight={800} fill={K.ok} opacity={fixK}>
              2.41 µs ✓
            </text>
            {/* true */}
            <text x={60} y={783} fontSize={26} fontWeight={800} fill={K.inline}>
              true (1 / rate)
            </text>
            {kT > 0 && <rect x={BX} y={750} width={2.41 * PX * kT} height={50} rx={6} fill={K.inline} opacity={0.9} />}
            <text x={BX + 2.41 * PX + 16} y={786} fontSize={32} fontWeight={800} fill={K.inline} opacity={clamp01((kT - 0.6) / 0.4)}>
              2.41 µs
            </text>
            {/* scale */}
            <line x1={BX} y1={822} x2={BX + 2.41 * PX} y2={822} stroke={C.dim} strokeWidth={2} />
            {[0, 1, 2].map((v) => (
              <g key={v}>
                <line x1={BX + v * PX} y1={816} x2={BX + v * PX} y2={830} stroke={C.dim} strokeWidth={2} />
                <text x={BX + v * PX} y={858} textAnchor="middle" fontSize={24} fill={C.muted}>
                  {v === 2 ? "2 µs" : v}
                </text>
              </g>
            ))}
          </g>
        )}
      </Stage>

      {/* ---------------- headline numbers */}
      <Centre cx={aX} cy={numY} fs={fs} color={K.timer} opacity={1 - fixK}>
        {countUp(t, T[0] + 0.8, 1.6, 1.87, 2)} µs
      </Centre>
      <Centre cx={aX} cy={numY} fs={fs} color={K.ok} opacity={fixK}>
        2.41 µs ✓
      </Centre>
      <Centre cx={aX} cy={labY} fs={labFs} color={C.muted} weight={500} opacity={fade(t, T[0] + 1.6, T[2] - 0.3, 0.4)}>
        inline path, W=1 · first measurement
      </Centre>
      <Centre cx={aX} cy={labY} fs={labFs} color={C.muted} weight={500} opacity={prog(t, T[2] + 0.5, 0.4) * (1 - fixK)}>
        measured with RDTSC
      </Centre>
      <Centre cx={aX} cy={labY} fs={labFs} color={C.muted} weight={500} opacity={fixK}>
        measured with RDTSCP
      </Centre>
      <Centre cx={1360} cy={numY} fs={fs} color={K.inline} opacity={prog(t, T[1] + 0.6, 0.5)}>
        2.41 µs
      </Centre>
      <Centre cx={1360} cy={labY} fs={labFs} color={C.muted} weight={500} opacity={prog(t, T[1] + 1.0, 0.5)}>
        1 / message rate
      </Centre>
      <Centre cx={960} cy={numY} fs={fs} color={K.bad} opacity={prog(t, T[1] + 2.2, 0.4) * (1 - fixK)}>
        <span style={{ display: "inline-block", transform: `scale(${pop(t, T[1] + 2.2, 0.5)})` }}>≠</span>
      </Centre>
      <Centre cx={960} cy={numY} fs={fs} color={K.ok} opacity={fixK}>
        =
      </Centre>
      <Centre cx={960} cy={690} fs={32} color={C.text} weight={500} opacity={fade(t, T[1] + 3.0, T[2], 0.5)}>
        At W=1, one message in flight: RTT should equal 1 / rate
      </Centre>

      <Note x={60} y={490} w={340}>
        <span style={{ opacity: tlA }}>schematic timeline, not to scale</span>
      </Note>

      {/* host poll loop (c2): what the two lanes are */}
      <Code
        x={420}
        y={650}
        size={28}
        lines={[
          "// host: poll for the reply, then timestamp it",
          "cnt    = *OUT_CNTS;   // MMIO load from the card",
          "t_recv = rdtsc();     // TSC read: not ordered after the load",
        ]}
        hl={t < T[2] + 3.8 ? 1 : 2}
        style={{ opacity: fade(t, T[2] + 1.0, T[3]) }}
      />

      {/* ---------------- cost table (c4) */}
      <div
        style={{
          position: "absolute",
          left: 1250,
          top: 632,
          width: 610,
          background: C.panel,
          border: `2px solid ${C.line}`,
          borderRadius: 18,
          padding: "16px 24px",
          boxSizing: "border-box",
          ...appear(t, T[4] + 3.3),
        }}
      >
        <div style={{ fontSize: 24, fontWeight: 800, color: C.text, marginBottom: 8 }}>Back-to-back cost (TSC ticks)</div>
        {table.map(([ins, ticks, res, col, a0], i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              fontSize: 26,
              lineHeight: 1.6,
              borderRadius: 8,
              padding: "0 10px",
              margin: "0 -10px",
              background: i === 2 ? `rgba(67,197,158,${0.16 * hlRow})` : undefined,
              ...appear(t, a0, undefined, 10),
            }}
          >
            <span style={{ width: 230, fontFamily: MONO, color: C.text }}>{ins}</span>
            <span style={{ width: 110, fontFamily: MONO, fontWeight: 800, color: K.timer, textAlign: "right", marginRight: 40 }}>{ticks}</span>
            <span style={{ fontWeight: 700, color: col }}>{res}</span>
          </div>
        ))}
      </div>
    </>
  );
}

export const S06: SceneDef = {
  id: "S06",
  title: "A measurement trap",
  stage: 2,
  source: "dpdk::rdtsc_after_loads · Xeon Gold 6438M",
  dur: DUR,
  cues,
  C: Body,
};
