import {
  C,
  Card,
  Cue,
  FONT,
  K,
  MONO,
  PacketStream,
  SceneDef,
  Stage,
  appear,
  countUp,
  fade,
  lerp,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { InfTag, PolyArrow, TimeBlock } from "./dsa";

// <cues>
const cues: Cue[] = [
  [0.60, 13.94, "If per-op `BAR4` completions cause the `131 ns` bubble, the fix is native **DSA Batch (opcode = 0x01)**: let `B` copies share **one** reverse-P2P completion!", "Since per-copy B A R four completions cause the one hundred thirty-one nanosecond bubble, the fix is native Intel D S A Batch: aggregate B copies so they share a single reverse P 2 P completion write."], // 0
  [14.29, 29.95, "Single-variable sweep: hold `W = 64` total in-flight `4 KiB` copies, `CC = 0`, single engine, and `BAR4` fixed—vary only **B = 1 -> 2 -> 4 -> 8 -> 16 -> 32** (`RCR = 0`).", "Holding total in-flight copies fixed at sixty-four with C C zero on one engine, we sweep only batch size B from one to thirty-two, turning off sub-descriptor completions so only the top-level Batch writes thirty-two bytes to B A R four."], // 1
  [30.30, 48.28, "Throughput surges: **15.28** (`B=1`) -> **23.35** (`B=2`) -> **31.13** (`B=4`) -> **31.32 GB/s** (`B=16`)—a **2.05x** jump down to `130.8 ns / 4 KiB`!", "Throughput jumps immediately: from fifteen point two eight at B equals one, to twenty-three point three five at B equals two, then thirty-one point one three at B equals four, peaking at thirty-one point three two gigabytes per second at B equals sixteen—a two point zero five times gain."], // 2
  [48.63, 69.85, "We infer two-stage pipelining `max(τ_comp, B·τ_copy)`: non-overlap residual drops to **+3.2 ns** at `B = 4` (`0.0 ns` at `B = 16`); `B = 32` (`W/B = 2`) dips to **30.57 GB/s**.", "We infer a two-stage parallel pipeline where data copying overlaps reverse completion draining: non-overlap overhead drops to just three point two nanoseconds per batch by B equals four, beating naive serial amortization. At B equals thirty-two, only two batches remain in flight, dipping slightly to thirty point five seven."], // 3
  [70.20, 92.56, "Tight tails stay intact: `p99 - p50` is **133–220 ns** across all `B` (despite a **+329 ns** Batch fetch step); sweet spot **B = 4** hits **2.84 µs** + **99.4%** peak BW!", "Tight latency tails stay intact across every batch size, with p ninety-nine minus p fifty locked between one hundred thirty-three and two hundred twenty nanoseconds, even accounting for a three hundred twenty-nine nanosecond indirect descriptor fetch step. B equals four is the sweet spot: two point eight four microseconds for ninety-nine point four percent of peak bandwidth."], // 4
  [92.91, 120.93, "At **B = 16**, `BAR4` beats Host Memzone on throughput (**31.32 vs 31.10 GB/s**), tail (**4.61 vs 5.75 µs**), and symmetric loop (**4.53 vs 5.74 µs**; `516 KiB` hot LLC `src` + DDR5 `dst`).", "At B equals sixteen, card B A R four wins on throughput, thirty-one point three two versus thirty-one point one zero gigabytes per second, on p ninety-nine latency, four point six one versus five point seven five microseconds, and on symmetric full-loop mean, four point five three versus five point seven four microseconds. Note that our five hundred sixteen kilobyte active set hits hot L L C on reads while writing through to D D R five."], // 5
];
const DUR = 122.33; // <dur>
// </cues>

const BW_DATA = [
  { b: 1, batches: 64, gbs: 15.28, ns4k: 268.0, color: K.bubble, tag: "" },
  { b: 2, batches: 32, gbs: 23.35, ns4k: 175.4, color: K.wc, tag: "" },
  { b: 4, batches: 16, gbs: 31.13, ns4k: 131.6, color: K.batch, tag: "★ 2.04×" },
  { b: 8, batches: 8, gbs: 31.25, ns4k: 131.1, color: K.rev, tag: "" },
  { b: 16, batches: 4, gbs: 31.32, ns4k: 130.8, color: K.rev, tag: "Peak 2.05×" },
  { b: 32, batches: 2, gbs: 30.57, ns4k: 134.0, color: K.fwd, tag: "-2.4%" },
];

const LAT_DATA = [
  { b: 1, p50: 2.09, p99: 2.29, diff: 201, sweet: false },
  { b: 2, p50: 2.52, p99: 2.65, diff: 133, sweet: false },
  { b: 4, p50: 2.84, p99: 3.02, diff: 184, sweet: true },
  { b: 8, p50: 3.42, p99: 3.62, diff: 200, sweet: false },
  { b: 16, p50: 4.42, p99: 4.61, diff: 190, sweet: false },
  { b: 32, p50: 6.64, p99: 6.86, diff: 220, sweet: false },
];

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  // Phase A: Mechanism comparison (T[0]) + Single-variable setup (T[1]), fades out at T[2]
  const kPhaseA = fade(t, T[0], T[2] - 0.1, 0.5);
  const kTopLane = prog(t, T[0] + 0.1, 0.7);
  const kBotLane = prog(t, T[0] + 1.2, 0.8);
  const kSetup = fade(t, T[1], T[2] - 0.1, 0.5);

  // Phase B: Left Throughput Chart (T[2]..DUR) + Right panels (T[2], T[3], T[4], T[5])
  const kChart = prog(t, T[2], 0.7);
  const kRefLines = prog(t, T[2] + 1.3, 0.6);
  const kR2 = fade(t, T[2], T[3] - 0.1, 0.5);
  const kR3 = fade(t, T[3], T[4] - 0.1, 0.5);
  const kR4 = fade(t, T[4], T[5] - 0.1, 0.5);
  const kR5 = prog(t, T[5], 0.6);

  // Chart geometry (left panel)
  const cx0 = 152;
  const cx1 = 1018;
  const cy0 = 758;
  const cy1 = 232;
  const sy = linScale(0, 36, cy0, cy1);
  const slotW = (cx1 - cx0) / BW_DATA.length;
  const barW = 92;

  return (
    <>
      <Stage>
        {/* ================= PHASE A: MECHANISM COMPARISON (T[0]..T[2]) ================= */}
        {kPhaseA > 0.01 && (
          <g opacity={kPhaseA}>
            {/* Top Lane: B = 1 Per-Op Submit */}
            <g opacity={kTopLane}>
              <rect
                x={72}
                y={144}
                width={1776}
                height={186}
                rx={16}
                fill={C.panel}
                stroke={K.bubble}
                strokeWidth={2}
                strokeOpacity={0.6}
              />
              <text x={102} y={182} fontFamily={FONT} fontSize={23} fontWeight={800} fill={K.bubble}>
                B = 1 Per-Op Submit · Drains 1 × BAR4 Completion After Every Copy
              </text>

              {[0, 1, 2].map((i) => {
                const bx = 102 + i * 442;
                const kBlk = prog(t, T[0] + 0.25 + i * 0.22, 0.45);
                return (
                  <g key={i} opacity={kBlk}>
                    <TimeBlock x={bx} y={206} w={196} h={96} label={`4 KiB #${i + 1}`} sub="Copy ~137 ns" color={K.fwd} />
                    <TimeBlock
                      x={bx + 206}
                      y={206}
                      w={216}
                      h={96}
                      label="+131 ns Bubble"
                      sub="BAR4 Rev-P2P Drain"
                      color={K.bubble}
                      hatch
                    />
                  </g>
                );
              })}

              <PolyArrow pts={[[1422, 254], [1518, 254]]} color={K.bubble} w={4} k={kTopLane} />
              <rect x={1532} y={204} width={286} height={100} rx={14} fill="rgba(249,65,68,0.14)" stroke={K.bubble} strokeWidth={2.5} />
              <text x={1675} y={244} textAnchor="middle" fontFamily={MONO} fontSize={36} fontWeight={900} fill={K.bubble}>
                15.28 GB/s
              </text>
              <text x={1675} y={282} textAnchor="middle" fontFamily={MONO} fontSize={20} fill={C.text}>
                268.0 ns / 4 KiB
              </text>
            </g>

            {/* Bottom Lane: DSA Batch opcode = 0x01 */}
            <g opacity={kBotLane}>
              <rect
                x={72}
                y={356}
                width={1776}
                height={210}
                rx={16}
                fill={C.panel}
                stroke={K.batch}
                strokeWidth={2.5}
              />
              <text x={102} y={396} fontFamily={FONT} fontSize={23} fontWeight={800} fill={K.batch}>
                DSA Batch (opcode = 0x01) · B Copies Share 1 Reverse-P2P Completion
              </text>

              {/* 1 x Batch 64 B */}
              <TimeBlock x={102} y={422} w={218} h={108} label="1 × Batch 64 B" sub="opcode = 0x01" color={K.batch} />
              <PolyArrow pts={[[326, 476], [392, 476]]} color={K.batch} w={4} k={kBotLane} />

              {/* Contiguous 4K sub-descriptors */}
              {[0, 1, 2, 3].map((i) => {
                const bx = 404 + i * 168;
                const kSub = prog(t, T[0] + 1.5 + i * 0.18, 0.4);
                const lbl = i < 3 ? `4K #${i + 1}` : "4K #B";
                return (
                  <TimeBlock
                    key={i}
                    x={bx}
                    y={422}
                    w={158}
                    h={108}
                    label={lbl}
                    sub="RCR = 0"
                    color={K.fwd}
                    opacity={kSub}
                  />
                );
              })}

              {/* Bracket under sub-descriptors */}
              <text x={736} y={552} textAnchor="middle" fontFamily={FONT} fontSize={19} fontWeight={700} fill={K.fwd}>
                Contiguous Copies · Zero Mid-Batch Bubble (Sub-Descriptors RCR = 0)
              </text>

              <PolyArrow pts={[[1076, 476], [1144, 476]]} color={K.rev} w={4} k={kBotLane} />
              <TimeBlock x={1156} y={422} w={270} h={108} label="1 × 32 B BAR4" sub="Rev-P2P Completion" color={K.rev} />

              <PolyArrow pts={[[1434, 476], [1518, 476]]} color={K.rev} w={4} k={kBotLane} />
              <rect x={1532} y={422} width={286} height={108} rx={14} fill="rgba(67,197,158,0.16)" stroke={K.rev} strokeWidth={2.8} />
              <text x={1675} y={464} textAnchor="middle" fontFamily={MONO} fontSize={36} fontWeight={900} fill={K.rev}>
                31.32 GB/s
              </text>
              <text x={1675} y={504} textAnchor="middle" fontFamily={MONO} fontSize={20} fontWeight={700} fill={K.batch}>
                2.05× · 130.8 ns/4K
              </text>

              {/* Ambient packet stream along the Batch pipeline */}
              <PacketStream pts={[[326, 476], [1144, 476]]} travel={rt * 150} gap={140} size={10} color={K.batch} opacity={0.75 * kBotLane} />
            </g>
          </g>
        )}

        {/* ================= PHASE B: LEFT THROUGHPUT BAR CHART (T[2]..DUR) ================= */}
        {kChart > 0.01 && (
          <g opacity={kChart}>
            {/* Panel frame */}
            <rect x={68} y={138} width={988} height={720} rx={18} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={102} y={182} fontFamily={FONT} fontSize={24} fontWeight={800} fill={C.text}>
              Card BAR4 Sustained BW (W = 64, CC = 0, 1 Engine)
            </text>
            <text x={1024} y={182} textAnchor="end" fontFamily={MONO} fontSize={19} fill={C.muted}>
              GB/s (10⁹ B/s)
            </text>

            {/* Horizontal gridlines & Y ticks */}
            {[0, 10, 20, 30, 35].map((v) => {
              const yy = sy(v);
              return (
                <g key={v}>
                  <line x1={cx0} y1={yy} x2={cx1} y2={yy} stroke={C.line} strokeWidth={1.5} strokeDasharray={v === 0 ? undefined : "5 6"} opacity={v === 0 ? 1 : 0.6} />
                  <text x={cx0 - 14} y={yy + 6} textAnchor="end" fontFamily={MONO} fontSize={20} fill={C.muted}>
                    {v}
                  </text>
                </g>
              );
            })}
            <line x1={cx0} y1={cy1} x2={cx0} y2={cy0} stroke={C.dim} strokeWidth={2} />

            {/* Reference lines: Host Memzone B=16 (31.10) & B=1 (29.98) */}
            {kRefLines > 0.01 && (
              <g opacity={kRefLines}>
                <line
                  x1={cx0}
                  y1={sy(31.10)}
                  x2={lerp(cx0, cx1, kRefLines)}
                  y2={sy(31.10)}
                  stroke={C.text}
                  strokeWidth={2}
                  strokeDasharray="8 6"
                  opacity={0.75}
                />
                <rect x={164} y={sy(31.10) - 34} width={286} height={28} rx={6} fill="#0d1528" fillOpacity={0.9} />
                <text x={172} y={sy(31.10) - 14} fontFamily={MONO} fontSize={18} fontWeight={700} fill={C.text}>
                  Host Memzone B=16: 31.10
                </text>

                <line
                  x1={cx0}
                  y1={sy(29.98)}
                  x2={lerp(cx0, cx1, kRefLines)}
                  y2={sy(29.98)}
                  stroke={C.muted}
                  strokeWidth={2}
                  strokeDasharray="4 6"
                  opacity={0.85}
                />
                <rect x={164} y={sy(29.98) + 6} width={274} height={28} rx={6} fill="#0d1528" fillOpacity={0.9} />
                <text x={172} y={sy(29.98) + 26} fontFamily={MONO} fontSize={18} fill={C.muted}>
                  Host Memzone B=1: 29.98
                </text>
              </g>
            )}

            {/* Bars */}
            {BW_DATA.map((d, i) => {
              const kBar = prog(t, T[2] + 0.15 + i * 0.18, 0.75);
              const bx = cx0 + i * slotW + (slotW - barW) / 2;
              const topY = sy(d.gbs * kBar);
              const bh = Math.max(0, cy0 - topY);
              const isFocus =
                (kR3 > 0.3 && (d.b === 4 || d.b === 32)) ||
                (kR4 > 0.3 && d.b === 4) ||
                (kR5 > 0.3 && d.b === 16);
              return (
                <g key={d.b}>
                  <rect
                    x={bx}
                    y={topY}
                    width={barW}
                    height={bh}
                    rx={8}
                    fill={`${d.color}${isFocus ? "55" : "30"}`}
                    stroke={d.color}
                    strokeWidth={isFocus ? 3.5 : 2.4}
                  />
                  {/* Value above bar */}
                  <text
                    x={bx + barW / 2}
                    y={topY - 14}
                    textAnchor="middle"
                    fontFamily={MONO}
                    fontSize={22}
                    fontWeight={800}
                    fill={d.color}
                    opacity={kBar}
                  >
                    {(d.gbs * kBar).toFixed(2)}
                  </text>
                  {/* Tag badge above value for B=4, B=16, B=32 */}
                  {d.tag && (
                    <text
                      x={bx + barW / 2}
                      y={topY - 40}
                      textAnchor="middle"
                      fontFamily={FONT}
                      fontSize={18}
                      fontWeight={800}
                      fill={d.b === 4 ? K.batch : d.b === 16 ? K.rev : K.fwd}
                      opacity={kBar}
                    >
                      {d.tag}
                    </text>
                  )}
                  {/* X labels */}
                  <text x={bx + barW / 2} y={cy0 + 30} textAnchor="middle" fontFamily={MONO} fontSize={22} fontWeight={800} fill={C.text}>
                    B={d.b}
                  </text>
                  <text x={bx + barW / 2} y={cy0 + 56} textAnchor="middle" fontFamily={MONO} fontSize={17} fill={C.muted}>
                    {d.ns4k.toFixed(1)} ns
                  </text>
                </g>
              );
            })}
          </g>
        )}

        {/* ================= BEAT 3 SVG: TWO-STAGE PIPELINE OVERLAP DIAGRAM (T[3]..T[4]) ================= */}
        {kR3 > 0.01 && (
          <g opacity={kR3}>
            <rect x={1084} y={138} width={768} height={720} rx={18} fill={C.panel} stroke={K.inf} strokeWidth={2.2} />
            <text x={1116} y={182} fontFamily={FONT} fontSize={24} fontWeight={800} fill={C.text}>
              Two-Stage Parallel Pipeline Overlap
            </text>
            <InfTag x={1652} y={156} text="[INF] Inference" opacity={kR3} />

            {/* Timeline axis */}
            <line x1={1116} y1={232} x2={1820} y2={232} stroke={C.dim} strokeWidth={1.8} />
            <text x={1116} y={222} fontFamily={MONO} fontSize={17} fill={C.muted}>
              t = 0
            </text>
            <text x={1440} y={222} fontFamily={MONO} fontSize={17} fill={K.batch}>
              B·τ_copy (~523 ns)
            </text>
            <text x={1820} y={222} textAnchor="end" fontFamily={MONO} fontSize={17} fill={C.muted}>
              Time →
            </text>

            {/* Batch #0 */}
            <text x={1116} y={268} fontFamily={MONO} fontSize={20} fontWeight={700} fill={K.fwd}>
              Batch #0 (B = 4)
            </text>
            <TimeBlock
              x={1116}
              y={280}
              w={330 * prog(t, T[3] + 0.15, 0.7)}
              h={82}
              label="4 × 4 KiB Copy"
              sub="B·τ_copy DDR5 write"
              color={K.fwd}
            />
            <TimeBlock
              x={1452}
              y={280}
              w={196 * prog(t, T[3] + 0.7, 0.5)}
              h={82}
              label="BAR4 Comp"
              sub="32 B Rev-P2P"
              color={K.rev}
            />

            {/* Batch #1 overlapping */}
            <text x={1116} y={404} fontFamily={MONO} fontSize={20} fontWeight={700} fill={K.batch}>
              Batch #1 (Next in Flight)
            </text>
            <TimeBlock
              x={1452}
              y={416}
              w={330 * prog(t, T[3] + 0.9, 0.7)}
              h={82}
              label="4 × 4 KiB Copy"
              sub="Hides Batch #0 drain!"
              color={K.batch}
            />

            {/* Overlap highlight bracket */}
            <rect
              x={1452}
              y={274}
              width={196}
              height={230}
              rx={10}
              fill="rgba(94,234,212,0.08)"
              stroke={K.inf}
              strokeWidth={2}
              strokeDasharray="6 4"
              opacity={prog(t, T[3] + 1.2, 0.5)}
            />
            <text x={1550} y={534} textAnchor="middle" fontFamily={MONO} fontSize={19} fontWeight={800} fill={K.inf}>
              max(τ_comp, B·τ_copy) · Full Overlap
            </text>
          </g>
        )}

        {/* ================= BEAT 4 SVG: TIGHT-TAIL LATENCY PANEL (T[4]..T[5]) ================= */}
        {kR4 > 0.01 && (
          <g opacity={kR4}>
            <rect x={1084} y={138} width={768} height={720} rx={18} fill={C.panel} stroke={K.rev} strokeWidth={2.2} />
            <text x={1116} y={182} fontFamily={FONT} fontSize={24} fontWeight={800} fill={C.text}>
              Single-Batch Latency &amp; Tight Tails (W = 1)
            </text>
            <text x={1820} y={182} textAnchor="end" fontFamily={MONO} fontSize={19} fill={K.rev}>
              p50 / p99 (µs)
            </text>

            {/* Horizontal bars for B=1..32 */}
            {LAT_DATA.map((d, i) => {
              const rowY = 224 + i * 74;
              const kRow = prog(t, T[4] + 0.1 + i * 0.14, 0.65);
              const sxLat = linScale(0, 7.5, 1210, 1650);
              const x50 = sxLat(d.p50 * kRow);
              const x99 = sxLat(d.p99 * kRow);
              const col = d.sweet ? K.batch : K.rev;
              return (
                <g key={d.b}>
                  {d.sweet && (
                    <rect x={1102} y={rowY - 11} width={732} height={62} rx={12} fill="rgba(249,199,79,0.12)" stroke={K.batch} strokeWidth={2} />
                  )}
                  <text x={1118} y={rowY + 26} fontFamily={MONO} fontSize={22} fontWeight={800} fill={d.sweet ? K.batch : C.text}>
                    B={d.b}
                  </text>
                  {/* Base track */}
                  <line x1={1210} y1={rowY + 20} x2={1650} y2={rowY + 20} stroke={C.line} strokeWidth={4} strokeLinecap="round" />
                  {/* p50 bar */}
                  <line x1={1210} y1={rowY + 20} x2={x50} y2={rowY + 20} stroke={col} strokeWidth={12} strokeLinecap="round" />
                  {/* p50 -> p99 whisker */}
                  <line x1={x50} y1={rowY + 20} x2={x99} y2={rowY + 20} stroke={C.text} strokeWidth={4} />
                  <circle cx={x50} cy={rowY + 20} r={7} fill={col} />
                  <line x1={x99} y1={rowY + 8} x2={x99} y2={rowY + 32} stroke={C.text} strokeWidth={3.5} />

                  {/* Right value */}
                  <text x={1666} y={rowY + 18} fontFamily={MONO} fontSize={20} fontWeight={800} fill={col}>
                    {d.p50.toFixed(2)} / {d.p99.toFixed(2)}
                  </text>
                  <text x={1666} y={rowY + 41} fontFamily={MONO} fontSize={17} fill={C.muted}>
                    Δ {d.diff} ns
                  </text>
                </g>
              );
            })}

            {/* Summary badges at bottom of right panel */}
            <rect x={1112} y={678} width={712} height={64} rx={12} fill="rgba(249,199,79,0.14)" stroke={K.batch} strokeWidth={2} />
            <text x={1468} y={717} textAnchor="middle" fontFamily={FONT} fontSize={21} fontWeight={800} fill={K.batch}>
              ★ Sweet Spot B = 4: 2.84 µs p50 + 99.4% Peak BW!
            </text>

            <rect x={1112} y={754} width={712} height={82} rx={12} fill="rgba(67,197,158,0.14)" stroke={K.rev} strokeWidth={2} />
            <text x={1468} y={788} textAnchor="middle" fontFamily={FONT} fontSize={20} fontWeight={800} fill={K.rev}>
              Tail p99 − p50 locked at 133–220 ns across B = 1..32
            </text>
            <text x={1468} y={819} textAnchor="middle" fontFamily={MONO} fontSize={17} fill={C.text}>
              +329 ns Batch fetch step · p50(B) = 2,278 + 136.1·B ns
            </text>
          </g>
        )}
      </Stage>

      {/* ================= PHASE A HTML: SINGLE-VARIABLE SETUP STRIP (T[1]..T[2]) ================= */}
      {kSetup > 0.01 && (
        <Card
          x={72}
          y={592}
          w={1776}
          h={266}
          border={K.fwd}
          title="Single-Variable Sweep Design (48 Runs · 0 Errors)"
          titleColor={K.fwd}
          style={{ opacity: kSetup }}
        >
          {/* Row 1: 4 Fixed conditions */}
          <div style={{ display: "flex", gap: 18, marginTop: 10 }}>
            {[
              "Fixed W = 64 × 4 KiB In-Flight",
              "CC = 0 (DDR5 Write-Through)",
              "Single Engine engine2.0",
              "Card BAR4 Slot 15 (Zero Host Fwd)",
            ].map((txt, i) => (
              <div
                key={i}
                style={{
                  flex: 1,
                  padding: "12px 14px",
                  borderRadius: 12,
                  background: "rgba(76,201,240,0.10)",
                  border: `1.5px solid ${K.fwd}66`,
                  fontFamily: FONT,
                  fontSize: 20,
                  fontWeight: 700,
                  color: C.text,
                  textAlign: "center",
                }}
              >
                {txt}
              </div>
            ))}
          </div>

          {/* Row 2: Single Variable B sweep */}
          <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 22 }}>
            <div style={{ fontFamily: FONT, fontSize: 22, fontWeight: 800, color: K.batch, whiteSpace: "nowrap" }}>
              Only Variable B (W/B Batches):
            </div>
            {BW_DATA.map((d, i) => {
              const kPill = prog(t, T[1] + 0.35 + i * 0.16, 0.45);
              return (
                <div
                  key={d.b}
                  style={{
                    flex: 1,
                    padding: "12px 10px",
                    borderRadius: 12,
                    background: d.b === 4 || d.b === 16 ? "rgba(249,199,79,0.16)" : "rgba(255,255,255,0.05)",
                    border: `2px solid ${d.b === 4 || d.b === 16 ? K.batch : C.line}`,
                    textAlign: "center",
                    opacity: kPill,
                    transform: `translateY(${(1 - kPill) * 14}px)`,
                  }}
                >
                  <div style={{ fontFamily: MONO, fontSize: 24, fontWeight: 900, color: d.b === 4 || d.b === 16 ? K.batch : C.text }}>
                    B = {d.b}
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 17, color: C.muted, marginTop: 2 }}>
                    {d.batches} batches
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* ================= BEAT 2 HTML: THROUGHPUT JUMP SUMMARY (T[2]..T[3]) ================= */}
      {kR2 > 0.01 && (
        <Card
          x={1084}
          y={138}
          w={768}
          h={720}
          border={K.rev}
          title="Throughput Surge: 2.05× Jump to Full BW"
          titleColor={K.rev}
          style={{ opacity: kR2 }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 22, marginTop: 14 }}>
            <div
              style={{
                padding: "22px 26px",
                borderRadius: 16,
                background: "rgba(67,197,158,0.12)",
                border: `2px solid ${K.rev}`,
              }}
            >
              <div style={{ fontFamily: FONT, fontSize: 21, color: C.muted, fontWeight: 700 }}>
                Card BAR4 Peak Sustained BW (B = 16)
              </div>
              <div style={{ fontFamily: MONO, fontSize: 64, fontWeight: 900, color: K.rev, marginTop: 6 }}>
                {countUp(t, T[2] + 0.3, 1.1, 31.32, 2, 15.28)} GB/s
              </div>
              <div style={{ fontFamily: MONO, fontSize: 22, color: C.text, marginTop: 6 }}>
                Up <span style={{ color: K.batch, fontWeight: 800 }}>2.05×</span> vs B = 1 (15.28 GB/s)
              </div>
            </div>

            <div
              style={{
                padding: "20px 26px",
                borderRadius: 16,
                background: "rgba(249,199,79,0.10)",
                border: `2px solid ${K.batch}`,
              }}
            >
              <div style={{ fontFamily: FONT, fontSize: 21, color: K.batch, fontWeight: 800 }}>
                ★ B = 4 Knee Hits 99.4% of Peak BW
              </div>
              <div style={{ fontFamily: MONO, fontSize: 42, fontWeight: 900, color: C.text, marginTop: 6 }}>
                31.13 GB/s <span style={{ fontSize: 24, color: K.batch }}>(2.04×)</span>
              </div>
              <div style={{ fontFamily: FONT, fontSize: 20, color: C.muted, marginTop: 6 }}>
                Already beats Host Memzone B = 1 baseline (29.98 GB/s)
              </div>
            </div>

            <div
              style={{
                padding: "18px 26px",
                borderRadius: 16,
                background: "rgba(255,255,255,0.04)",
                border: `1.5px solid ${C.line}`,
              }}
            >
              <div style={{ fontFamily: FONT, fontSize: 21, color: C.muted, fontWeight: 700 }}>
                Amortized Time per 4 KiB Copy
              </div>
              <div style={{ fontFamily: MONO, fontSize: 34, fontWeight: 800, color: K.fwd, marginTop: 8 }}>
                268.0 ns → 130.8 ns / 4K
              </div>
              <div style={{ fontFamily: FONT, fontSize: 20, color: C.muted, marginTop: 6 }}>
                The 131 ns reverse-P2P drain bubble is completely flattened
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* ================= BEAT 3 HTML: INFERENCE BULLETS BELOW SVG TIMELINE (T[3]..T[4]) ================= */}
      {kR3 > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 1116,
            top: 562,
            width: 704,
            display: "flex",
            flexDirection: "column",
            gap: 16,
            opacity: kR3,
          }}
        >
          <div
            style={{
              padding: "16px 22px",
              borderRadius: 14,
              background: "rgba(94,234,212,0.10)",
              border: `1.5px solid ${K.inf}88`,
              fontFamily: FONT,
              fontSize: 20,
              lineHeight: 1.48,
              color: C.text,
            }}
          >
            <strong style={{ color: K.batch }}>Non-overlap residual Δ_batch vanishes:</strong> drops from{" "}
            <span style={{ fontFamily: MONO, color: K.wc }}>+89.3 ns</span> (B=2) to{" "}
            <span style={{ fontFamily: MONO, color: K.rev, fontWeight: 800 }}>+3.2 / +2.3 / +0.0 ns/batch</span>{" "}
            at B = 4 / 8 / 16.
          </div>
          <div
            style={{
              padding: "16px 22px",
              borderRadius: 14,
              background: "rgba(76,201,240,0.08)",
              border: `1.5px solid ${K.fwd}66`,
              fontFamily: FONT,
              fontSize: 20,
              lineHeight: 1.48,
              color: C.text,
            }}
          >
            <strong style={{ color: K.fwd }}>B = 32 shallow pipeline (−2.4%):</strong> fixed{" "}
            <span style={{ fontFamily: MONO }}>W = 64</span> leaves only{" "}
            <span style={{ fontFamily: MONO }}>W/B = 2</span> batches in flight, dipping to{" "}
            <span style={{ fontFamily: MONO, color: K.fwd, fontWeight: 800 }}>30.57 GB/s</span>.
          </div>
        </div>
      )}

      {/* ================= BEAT 5 HTML: HEAD-TO-HEAD B = 16 VICTORY CARD (T[5]..DUR) ================= */}
      {kR5 > 0.01 && (
        <Card
          x={1084}
          y={138}
          w={768}
          h={720}
          border={K.rev}
          title="Head-to-Head B = 16: Card BAR4 Wins Across the Board"
          titleColor={K.rev}
          style={{ opacity: kR5 }}
        >
          {/* Table Header */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1.35fr 1.05fr 1.05fr 0.85fr",
              columnGap: 10,
              padding: "10px 16px",
              borderRadius: 10,
              background: "rgba(255,255,255,0.05)",
              fontFamily: FONT,
              fontSize: 19,
              fontWeight: 800,
              color: C.muted,
              marginTop: 6,
            }}
          >
            <div>Metric (B = 16)</div>
            <div style={{ color: K.rev }}>Card BAR4</div>
            <div>Host Mem</div>
            <div>Gain</div>
          </div>

          {/* 4 Metric Rows */}
          {[
            {
              metric: "Sustained BW (W=64)",
              card: "31.32 GB/s",
              host: "31.10 GB/s",
              delta: "+0.22 GB/s",
              hl: false,
            },
            {
              metric: "W = 1 p50 Latency",
              card: "4.42 µs",
              host: "4.63 µs",
              delta: "−214 ns",
              hl: false,
            },
            {
              metric: "W = 1 p99 Latency",
              card: "4.61 µs",
              host: "5.75 µs",
              delta: "−1,142 ns",
              hl: true,
            },
            {
              metric: "Symmetric loop_mean",
              card: "4.53 µs",
              host: "5.74 µs",
              delta: "−21.1%",
              hl: true,
            },
          ].map((row, i) => (
            <div
              key={i}
              style={{
                display: "grid",
                gridTemplateColumns: "1.35fr 1.05fr 1.05fr 0.85fr",
                columnGap: 10,
                alignItems: "center",
                padding: "16px 16px",
                marginTop: 10,
                borderRadius: 12,
                background: row.hl ? "rgba(67,197,158,0.13)" : "rgba(255,255,255,0.03)",
                border: `2px solid ${row.hl ? K.rev : C.line}`,
                ...appear(t, T[5] + 0.12 + i * 0.18),
              }}
            >
              <div style={{ fontFamily: FONT, fontSize: 19, fontWeight: 700, color: C.text }}>{row.metric}</div>
              <div style={{ fontFamily: MONO, fontSize: i === 0 ? 21 : 24, fontWeight: 900, color: K.rev }}>{row.card}</div>
              <div style={{ fontFamily: MONO, fontSize: i === 0 ? 20 : 22, color: C.muted }}>{row.host}</div>
              <div style={{ fontFamily: MONO, fontSize: 19, fontWeight: 800, color: K.batch }}>{row.delta}</div>
            </div>
          ))}

          {/* Tail spread & B=1 loop callout */}
          <div
            style={{
              marginTop: 14,
              padding: "13px 16px",
              borderRadius: 12,
              background: "rgba(249,199,79,0.12)",
              border: `1.5px solid ${K.batch}88`,
              fontFamily: FONT,
              fontSize: 18,
              fontWeight: 700,
              color: C.text,
              textAlign: "center",
              lineHeight: 1.4,
            }}
          >
            Spread <span style={{ fontFamily: MONO }}>p99−p50</span>:{" "}
            <span style={{ fontFamily: MONO, color: K.rev, fontWeight: 900 }}>190</span> vs{" "}
            <span style={{ fontFamily: MONO, color: K.bubble, fontWeight: 900 }}>1,118 ns</span> (5.9×) ·{" "}
            <span style={{ fontFamily: MONO }}>B=1</span> loop:{" "}
            <span style={{ fontFamily: MONO, color: K.rev, fontWeight: 900 }}>2.19</span> vs{" "}
            <span style={{ fontFamily: MONO, color: C.muted }}>3.01 µs</span> (−27.2%)
          </div>

          {/* Honest footnote badge */}
          <div
            style={{
              marginTop: 12,
              padding: "12px 14px",
              borderRadius: 10,
              background: "rgba(76,201,240,0.08)",
              border: `1.5px solid ${C.line}`,
              fontFamily: MONO,
              fontSize: 15,
              color: C.muted,
              textAlign: "center",
              lineHeight: 1.4,
            }}
          >
            516 KiB active set (256K src + 256K dst + 4K subdesc, &lt;0.9% LLC):
            {" "}hot LLC reads + CC=0 DDR5 writes · 48 runs 0 errors
          </div>
        </Card>
      )}
    </>
  );
}

export const S06: SceneDef = {
  id: "S06",
  title: "The Fix: DSA Batch Full BW + Tight Tail",
  stage: 5,
  source: "Single Variable: Batch B = 1..32 · W = 64 · CC = 0",
  dur: DUR,
  cues,
  C: Body,
};
