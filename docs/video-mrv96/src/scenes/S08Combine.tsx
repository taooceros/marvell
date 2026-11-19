import React from "react";
import { Arrow, C, Cue, K, Note, Pt, SceneDef, Stage, Stat, along, clamp01, lerp, pathD, prog, useT } from "../lib";
import { BarChart, Chip, KnobBar } from "./kit";
import { SideBox, win } from "./_Mrv96SceneOpen";

// <cues>
const cues: Cue[] = [
  [0.60, 12.10, "**Experiment 6** rules out two suspects. First: with 4 card cores, replies land in different **64 KB regions**. Does that break write-combining?", "Experiment six rules out two suspects. First: with four card cores, replies land in different sixty-four kilobyte regions of host memory. Does jumping around break write combining?"], // 0
  [12.45, 23.17, "Fixed: closed loop, 4 card cores, W = 64. Knob = replies written **contiguously** in one region vs **interleaved** across regions.", "Fixed: the closed loop, four card cores, and a window of sixty-four. The knob: replies written contiguously in one region, versus interleaved across regions."], // 1
  [23.52, 36.24, "Host counters: full 64-byte line writes per reply **1.0001 vs 1.0000**, i.e. **99.99%** full lines. Throughput **13.14 vs 13.16 M/s**.", "Host counters see one full sixty-four byte line write per reply in both layouts: ninety-nine point nine nine percent full lines. Throughput: thirteen point one four versus thirteen point one six."], // 2
  [36.59, 49.85, "Why: each reply **is** one aligned 64-byte line. Its four 16-byte stores fill the combining buffer exactly, and it leaves whole before the next reply starts.", "Why? Each reply is itself one aligned sixty-four byte line. Its four stores, of sixteen bytes each, fill the combining buffer exactly, and it leaves as one whole write before the next reply even starts."], // 3
  [50.20, 63.36, "Caveat: equal throughput in the slow state is weak evidence. At **W = 1024** the same change costs **3%** (**21.88 → 21.22 M/s**), still unexplained.", "One caveat: equal throughput in the slow state is weak evidence, since both sides were waiting. At window ten twenty-four the same change costs three percent, still unexplained. The line count is the real proof."], // 4
];
const DUR = 64.76; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
/** Spoken "versus interleaved" inside cue 1: the knob slides from contiguous to interleaved here. */
const TSW = T[1] + 7.6;

// Host memory: 4 regions × 8 line cells. DPU: 4 cores and one core's 64 B write-combining buffer (4 × 16 B slots).
const HP = { x: 60, y: 200, w: 700, h: 400 };
const CP = { x: 1160, y: 200, w: 700, h: 400 };
const CW = 58;
const CH = 60;
const rowY = (r: number) => 262 + r * 82;
const cellX = (c: number) => 200 + c * 68;
const WC = { x: 1250, y: 400, w: 520, h: 100 };
const SLOT = (WC.w - 24 - 30) / 4;
const slotX = (j: number) => WC.x + 12 + j * (SLOT + 10);
const coreX = (i: number) => 1354 + i * 84;
const CORE_Y = 262;
const EXIT: Pt = [WC.x, WC.y + WC.h / 2];
/** Return lane from the WC buffer to the right end of region r. */
const lane = (r: number): Pt[] => [EXIT, [CP.x - 10, EXIT[1]], [HP.x + HP.w + 30, rowY(r) + CH / 2]];

/** Reply schedules (real s): each reply = 4 stores, flush, flight, landing. Cue 3 replays it in slow motion. */
const SEGS = [
  { a: T[0] + 1.6, b: T[1], P: 1.0, inter: true },
  { a: T[1] + 0.6, b: TSW, P: 1.0, inter: false },
  { a: TSW + 0.5, b: T[3], P: 1.0, inter: true },
  { a: T[3] + 0.5, b: T[4], P: 3.2, inter: true },
  { a: T[4] + 0.5, b: 1e9, P: 1.0, inter: true },
];
// Phase of a reply as a fraction of its period: store j starts at STEP·j and lasts STORE_D; flush; landing.
const STEP = 0.11;
const STORE_D = 0.09;
const FLUSH = 0.5;
const LAND = 0.86;
/** Region (row) and line (column) of reply n: contiguous fills region 0; interleaved deals replies across regions. */
const target = (n: number, inter: boolean) => (inter ? { r: n % 4, c: Math.floor(n / 4) % 8 } : { r: 0, c: n % 8 });

const STEPS = [
  { x: 60, at: 3.2, text: "4 × 16 B = 64 B: four stores fill the buffer exactly" },
  { x: 690, at: 6.8, text: "the buffer leaves as ONE full 64 B line write" },
  { x: 1320, at: 9.6, text: "the next reply starts with an empty buffer, whichever region it targets" },
];

function Body() {
  const t = useT();
  let cur = -1;
  SEGS.forEach((s, i) => {
    if (t >= s.a) cur = i;
  });

  // host cells: replies landed in the running schedule (the previous schedule fades out before the next starts)
  const cells: React.ReactNode[] = [];
  let last = { land: -1, r: 0, c: 0 };
  for (let si = Math.max(0, cur - 1); si <= cur; si++) {
    const s = SEGS[si];
    const alive = si + 1 < SEGS.length ? 1 - prog(t, SEGS[si + 1].a - 0.45, 0.4) : 1;
    if (alive <= 0) continue;
    for (let n = 0; s.a + n * s.P < s.b; n++) {
      const land = s.a + (n + LAND) * s.P;
      if (land > t) break;
      const { r, c } = target(n, s.inter);
      cells.push(
        <g key={`${si}-${n}`} opacity={alive}>
          <rect x={cellX(c)} y={rowY(r)} width={CW} height={CH} rx={6} fill={K.ret} opacity={0.85} />
          {n < 4 && (
            <text x={cellX(c) + CW / 2} y={rowY(r) + CH / 2 + 9} textAnchor="middle" fontSize={26} fontWeight={800} fill={C.bg}>
              {n + 1}
            </text>
          )}
        </g>,
      );
      if (si === cur) last = { land, r, c };
    }
  }

  // the reply in progress: u = its phase (0..1)
  let u = -1;
  let tg = { r: 0, c: 0 };
  if (cur >= 0) {
    const s = SEGS[cur];
    const n = Math.min(Math.floor((t - s.a) / s.P), Math.ceil((s.b - s.a) / s.P) - 1);
    const ph = (t - s.a - n * s.P) / s.P;
    if (ph < 1) {
      u = ph;
      tg = target(n, s.inter);
    }
  }
  const fills = [0, 1, 2, 3].map((j) => (u >= 0 && u < FLUSH ? clamp01((u - STEP * j) / STORE_D) : 0));
  const storing = u >= 0 && u < STEP * 3 + STORE_D;
  const full = u >= STEP * 3 + STORE_D && u < FLUSH;
  const flying = u >= FLUSH && u < LAND;
  // the flushed line appears just outside the buffer (clear of its slot labels) and flies to its cell
  const pk = along([[WC.x - 40, EXIT[1]], ...lane(tg.r).slice(1), [cellX(tg.c) + CW / 2, rowY(tg.r) + CH / 2]],
    (u - FLUSH) / (LAND - FLUSH));

  const kHost = prog(t, T[0], 0.6);
  const kCard = prog(t, T[0] + 0.4, 0.6);
  const kLane = prog(t, T[0] + 1.0, 0.6);
  const dimK = 1 - 0.5 * prog(t, T[4], 0.6);
  const sw = prog(t, TSW, 0.6);
  const t0 = win(t, T[0] + 5.5, T[1]);
  const st = prog(t, T[4] + 10.4, 0.35);
  const abs = (left: number, top: number, opacity: number, extra?: React.CSSProperties): React.CSSProperties => ({
    position: "absolute",
    left,
    top,
    opacity,
    ...extra,
  });

  return (
    <>
      <Stage>
        <g opacity={dimK}>
          <SideBox {...HP} color={K.host} title="HOST memory" opacity={kHost} />
          <SideBox {...CP} color={K.card} title="DPU" sub="4 card cores" opacity={kCard} />
          <text x={HP.x + HP.w - 22} y={HP.y + 38} textAnchor="end" fontSize={26} fontWeight={800} fill={K.knob}
            opacity={win(t, T[1] + 0.3, TSW)}>
            contiguous
          </text>
          <text x={HP.x + HP.w - 22} y={HP.y + 38} textAnchor="end" fontSize={26} fontWeight={800} fill={K.knob}
            opacity={win(t, TSW + 0.05, T[4])}>
            interleaved
          </text>
          {[0, 1, 2, 3].map((r) => (
            <g key={r} opacity={prog(t, T[0] + 0.5 + r * 0.15, 0.5)}>
              <text x={HP.x + 22} y={rowY(r) + CH / 2 + 9} fontSize={24} fontWeight={700} fill={C.muted}>
                64 KB
              </text>
              {Array.from({ length: 8 }, (_, c) => (
                <rect key={c} x={cellX(c)} y={rowY(r)} width={CW} height={CH} rx={6} fill="none" stroke={C.line} strokeWidth={2}
                  strokeDasharray="5 5" />
              ))}
            </g>
          ))}
          {cells}
          {last.land > 0 && t - last.land < 0.6 && (
            <rect x={cellX(last.c) - 5} y={rowY(last.r) - 5} width={CW + 10} height={CH + 10} rx={9} fill="none" stroke={K.ret}
              strokeWidth={4} opacity={1 - (t - last.land) / 0.6} />
          )}
          {[0, 1, 2, 3].map((r) => (
            <path key={r} d={pathD(lane(r))} fill="none" stroke={flying && tg.r === r ? K.ret : C.line}
              strokeWidth={flying && tg.r === r ? 4 : 3} opacity={kLane} />
          ))}
          <text x={960} y={262} textAnchor="middle" fontSize={26} fontWeight={700} fill={K.ret} opacity={kLane}>
            ← replies over PCIe
          </text>
          {[0, 1, 2, 3].map((i) => (
            <rect key={i} x={coreX(i)} y={CORE_Y} width={60} height={60} rx={10}
              fill={i === 0 && storing ? K.card : "rgba(248,150,30,0.18)"} stroke={K.card} strokeWidth={2} opacity={kCard} />
          ))}
          <g opacity={kCard}>
            <rect x={WC.x} y={WC.y} width={WC.w} height={WC.h} rx={14} fill={C.panel} stroke={full ? K.ret : K.card}
              strokeWidth={full ? 4 : 2.5} />
            {fills.map((f, j) => (
              <g key={j}>
                <rect x={slotX(j)} y={WC.y + 12} width={SLOT} height={WC.h - 24} rx={8} fill="none" stroke={C.line} strokeWidth={2}
                  strokeDasharray="5 5" />
                {f > 0 && <rect x={slotX(j)} y={WC.y + 12} width={SLOT} height={WC.h - 24} rx={8} fill={K.ret} opacity={0.9 * f} />}
                <text x={slotX(j) + SLOT / 2} y={WC.y + WC.h / 2 + 9} textAnchor="middle" fontSize={24} fontWeight={700}
                  fill={f > 0.5 ? C.bg : C.muted}>
                  16 B
                </text>
              </g>
            ))}
            <text x={WC.x} y={WC.y + WC.h + 34} fontSize={24} fill={C.muted}>
              write-combining buffer · 64 B
            </text>
            {[0, 1, 2, 3].map((j) => {
              const k = (u - STEP * j) / STORE_D;
              if (u < 0 || k < 0 || k >= 1) return null;
              return (
                <circle key={j} cx={lerp(coreX(0) + 30, slotX(j) + SLOT / 2, k)} cy={lerp(CORE_Y + 60, WC.y + 12, k)} r={9}
                  fill={K.ret} />
              );
            })}
          </g>
          {flying && (
            <g>
              <rect x={pk[0] - 36} y={pk[1] - 21} width={72} height={42} rx={8} fill={K.ret} />
              <text x={pk[0]} y={pk[1] + 9} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.bg}>
                64 B
              </text>
            </g>
          )}
          <text x={955} y={585} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.ret} opacity={win(t, T[3] + 6.8, T[4])}>
            ONE 64 B line write
          </text>
        </g>

        {/* cue 0: what a reply should become on PCIe vs the suspicion */}
        {t0 > 0 && (
          <g opacity={t0}>
            <text x={960} y={672} textAnchor="middle" fontSize={28} fill={C.text}>
              what does each reply become on PCIe?
            </text>
            <rect x={330} y={705} width={320} height={64} rx={10} fill={K.fast} opacity={0.9} />
            <text x={490} y={746} textAnchor="middle" fontSize={28} fontWeight={800} fill={C.bg}>
              64 B
            </text>
            <text x={490} y={815} textAnchor="middle" fontSize={28} fontWeight={700} fill={K.fast}>
              one full 64 B line write
            </text>
            <text x={960} y={746} textAnchor="middle" fontSize={30} fill={C.muted}>
              or
            </text>
            {[0, 1, 2, 3].map((j) => (
              <g key={j}>
                <rect x={1170 + j * 92} y={705} width={74} height={64} rx={8} fill="rgba(249,65,68,0.15)" stroke={K.slow}
                  strokeWidth={2.5} strokeDasharray="6 5" />
                <text x={1207 + j * 92} y={745} textAnchor="middle" fontSize={24} fontWeight={700} fill={K.slow}>
                  16 B
                </text>
              </g>
            ))}
            <text x={1345} y={815} textAnchor="middle" fontSize={28} fontWeight={700} fill={K.slow}>
              partial writes?
            </text>
          </g>
        )}

        {/* cue 2: throughput at W = 64 */}
        <BarChart x0={1300} x1={1820} y0={815} y1={705} max={15} dec={2} size={28}
          bars={[
            { label: "contiguous", value: 13.14, color: K.fast },
            { label: "interleaved", value: 13.16, color: K.fast },
          ]}
          k={(i) => prog(t, T[2] + 7.6 + 0.6 * i, 0.8)} opacity={win(t, T[2] + 7.4, T[3])} />

        {/* cue 3: step arrows */}
        <g opacity={win(t, T[3] + 0.4, T[4])}>
          <Arrow x1={608} y1={775} x2={682} y2={775} color={K.ret} w={4} k={prog(t, T[3] + 6.5, 0.4)} />
          <Arrow x1={1238} y1={775} x2={1312} y2={775} color={K.ret} w={4} k={prog(t, T[3] + 9.3, 0.4)} />
        </g>

        {/* cue 4: the caveat at W = 1024 */}
        <BarChart x0={640} x1={1060} y0={815} y1={705} max={25} dec={2} size={28}
          bars={[
            { label: "contiguous", value: 21.88, color: K.fast },
            { label: "interleaved", value: 21.22, color: K.slow },
          ]}
          k={(i) => prog(t, T[4] + 6.0 + 0.6 * i, 0.8)} opacity={win(t, T[4] + 5.6)} />
      </Stage>

      <div style={{ opacity: win(t, T[0] + 2.0) }}>
        <Note x={60} y={606} w={680}>
          schematic: cells, cores and timing are illustrative
        </Note>
      </div>
      <div style={abs(1250, 552, win(t, T[0] + 6.5, T[2]))}>
        <Chip color={K.slow}>jumping regions → partial writes?</Chip>
      </div>
      <div style={abs(1250, 552, win(t, T[2] + 0.6, T[3]))}>
        <Chip color={K.fast}>no: whole 64 B lines</Chip>
      </div>
      <div style={abs(1250, 552, win(t, T[3] + 3.2, T[4]))}>
        <Chip color={C.text}>4 × 16 B = 64 B</Chip>
      </div>

      {/* cue 1: the two settings of the knob */}
      {[
        { x: 160, on: 1 - sw, title: "contiguous", body: "reply after reply into one 64 KB region" },
        { x: 1000, on: sw, title: "interleaved", body: "consecutive replies hop across the 64 KB regions" },
      ].map((o) => (
        <div key={o.x} style={abs(o.x, 650, win(t, T[1] + 0.4, T[2]) * lerp(0.45, 1, o.on), {
          width: 760, height: 150, boxSizing: "border-box", borderRadius: 18, background: C.panel, padding: "18px 26px",
          border: `3px solid ${o.on > 0.5 ? K.knob : C.line}`, boxShadow: o.on > 0.5 ? `0 0 24px ${K.knob}55` : "none",
        })}>
          <div style={{ fontSize: 30, fontWeight: 800, color: o.on > 0.5 ? K.knob : C.muted }}>{o.title}</div>
          <div style={{ fontSize: 28, marginTop: 8, lineHeight: 1.3 }}>{o.body}</div>
        </div>
      ))}

      {/* cue 2: the host counters answer it */}
      <div style={abs(60, 640, win(t, T[2] + 0.6, T[3]), {
        width: 780, height: 210, boxSizing: "border-box", background: C.panel, border: `2px solid ${C.line}`, borderRadius: 18,
        padding: "16px 26px",
      })}>
        <div style={{ fontSize: 24, fontWeight: 600, color: C.muted }}>host counters (means) · full 64 B line writes per reply</div>
        <div style={{ display: "flex", gap: 80, marginTop: 12 }}>
          {[
            ["1.0001", "contiguous"],
            ["1.0000", "interleaved"],
          ].map(([v, l]) => (
            <div key={l}>
              <div style={{ fontSize: 64, fontWeight: 800, color: K.fast, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>{v}</div>
              <div style={{ fontSize: 26 }}>{l}</div>
            </div>
          ))}
        </div>
      </div>
      <Stat value="99.99 %" label="full lines in both layouts" color={K.fast} size={64}
        style={abs(880, 640, win(t, T[2] + 4.6, T[3]), { width: 360, boxSizing: "border-box" })} />
      <div style={abs(1300, 640, win(t, T[2] + 7.4, T[3]), { fontSize: 24, color: C.muted })}>
        mean M/s · 4 card cores · W = 64
      </div>

      {/* cue 3: why */}
      <div style={abs(60, 638, win(t, T[3] + 0.6, T[4]), { fontSize: 30 })}>each reply is one aligned 64 B line</div>
      {STEPS.map((s) => (
        <div key={s.x} style={abs(s.x, 695, win(t, T[3] + s.at, T[4]), {
          width: 540, height: 160, boxSizing: "border-box", background: C.panel, border: `2px solid ${K.ret}`, borderRadius: 16,
          padding: "16px 22px", fontSize: 28, lineHeight: 1.3,
        })}>
          {s.text}
        </div>
      ))}

      {/* cue 4: caveat */}
      <div style={abs(60, 650, win(t, T[4] + 0.4), { width: 540, fontSize: 30, lineHeight: 1.3 })}>
        <span style={{ color: K.knob, fontWeight: 800 }}>Caveat:</span> equal throughput in the slow state is weak evidence — both
        sides were waiting
      </div>
      <div style={abs(640, 640, win(t, T[4] + 5.6), { fontSize: 26, color: C.muted })}>W = 1024 · 4 card cores · mean M/s</div>
      <div style={abs(1080, 690, win(t, T[4] + 7.8), { fontSize: 44, fontWeight: 800, color: K.slow })}>−3 %</div>
      <div style={abs(1080, 760, win(t, T[4] + 8.6))}>
        <Chip color={K.slow}>unexplained</Chip>
      </div>
      <div style={abs(1310, 668, st, {
        transform: `rotate(-6deg) scale(${lerp(1.5, 1, st)})`, border: `5px solid ${K.fast}`, borderRadius: 12,
        padding: "8px 22px", color: K.fast, fontSize: 36, fontWeight: 800, whiteSpace: "nowrap",
      })}>
        line count = the proof
      </div>
      <div style={abs(1310, 770, win(t, T[4] + 10.8), { width: 540 })}>
        <div style={{ fontSize: 30, fontWeight: 800, color: K.fast }}>1.0001 vs 1.0000</div>
        <div style={{ fontSize: 24, color: C.muted }}>full 64 B line writes per reply</div>
      </div>

      <KnobBar fixed={["closed loop", "4 card cores", "W = 64"]} knob="reply layout in host memory"
        options={["contiguous", "interleaved"]} active={t < T[1] || t >= T[4] ? -1 : sw} k={prog(t, 0.1, 0.5)} />
    </>
  );
}

export const S08: SceneDef = {
  id: "S08",
  title: "Exp 6a · Interleaved replies",
  stage: 4,
  source: "MRV-54 · logs/mrv54-2026-11-18 · 4 × 2×10⁷ msgs per point",
  dur: DUR,
  cues,
  C: Body,
};
