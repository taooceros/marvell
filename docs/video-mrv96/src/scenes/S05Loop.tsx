import { C, Cue, K, MONO, Note, SceneDef, Stage, fade, lerp, pathD, prog, useRealT, useT } from "../lib";
import { Brace, InfPill, KnobBar, Rig, SegBar, Takeaway, rigGeom, rigRoutes } from "./kit";
import { Stopwatch, atLen, cumLen, slicePath } from "./_Mrv96SceneOneWay";

// <cues>
const cues: Cue[] = [
  [0.60, 9.18, "**Experiment 3** closes the loop: the host keeps at most **W** messages in flight, and each reply frees a slot for the next request.", "Experiment three closes the loop. The host keeps at most W messages in flight, and each reply frees a slot for the next request."], // 0
  [9.53, 21.81, "Fixed: WC replies, **4 card cores**. Knob = window **W = 1 vs 64**. Card timestamps split each round trip: forward · on card · return.", "Fixed: write combining replies and four card cores. The knob: a window of one, versus sixty-four. Card timestamps split each round trip into forward, on-card, and return."], // 1
  [22.16, 34.18, "**W = 1**, nothing queues: **0.92 µs** round trip = **0.45** forward + **0.04** on card + **0.43** return.", "With a window of one, nothing queues. The round trip is zero point nine two microseconds: zero point four five forward, zero point zero four on the card, and zero point four three back."], // 2
  [34.53, 48.17, "**W = 64**: **13.5 M/s**, round trip **3.7 µs**. On card still **0.04**, return **~0.5**. The extra **~2.7 µs** waits on the **forward** side.", "At sixty-four: thirteen and a half million per second, and a three point seven microsecond round trip. On-card time is unchanged and the return is about half a microsecond, so the extra two point seven waits on the forward side."], // 3
  [48.52, 60.52, "The host fires requests in bursts **9 ns** apart, but they reach the card core one every **~75 ns**. About **42** wait in between.", "The host fires its requests in bursts, nine nanoseconds apart. But they reach the card core only once every seventy-five nanoseconds or so. About forty-two requests wait in between."], // 4
  [60.87, 73.07, "Caveat: two runs that skipped pre-touching memory pages ran at the same **13.4 M/s**, with the queue on the **return** side: **0.7 µs** forward, **2.7 µs** back.", "A caveat: two runs that skipped pre-touching memory pages ran at the same thirteen point four million, but with the queue on the return side: zero point seven microseconds forward, two point seven back."], // 5
  [73.42, 82.26, "**[INF]** So ~13 M/s is not one stage's speed limit; the queue forms wherever the loop's rhythm puts it.", "So, our reading: thirteen million per second is not the speed limit of any one stage. The queue forms wherever the rhythm of the loop puts it."], // 6
];
const DUR = 83.66; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---------------------------------------------------------------- loop geometry (kit Rig at s = 1)
const G = rigGeom(1, 0, 0);
const LOOP = rigRoutes(G).loop;
const CUM = cumLen(LOOP);
// loop vertices: send 0-2 · fwd 3-4 · poll 5-7 · reply 8-10 · ret 11-12 · hpoll 13-15
const A_CRING = CUM[4]; // forward lane ends at the request ring
const A_CC_IN = CUM[7]; // request reaches the card core
const A_CC_OUT = CUM[8]; // reply leaves the card core
const A_HRING = CUM[12]; // return lane ends at the reply ring
const A_END = CUM[15];
const PILE = 190; // px of lane where a queue bunches up

type Key = [number, number]; // [time phase 0..1, path length]
/** Time phase → path length, per measured split (forward · on card · return, µs). */
const MAP_W1: Key[] = [[0, 0], [0.45 / 0.92, A_CC_IN], [0.49 / 0.92, A_CC_OUT], [1, A_END]];
const MAP_FWD: Key[] = [[0, 0], [0.4 / 3.67, A_CRING - PILE], [3.06 / 3.67, A_CRING], [3.11 / 3.67, A_CC_IN], [3.15 / 3.67, A_CC_OUT], [1, A_END]];
const MAP_RET: Key[] = [[0, 0], [0.73 / 3.67, A_CC_IN], [0.77 / 3.67, A_CC_OUT], [1.17 / 3.67, A_HRING - PILE], [3.57 / 3.67, A_HRING], [1, A_END]];
const mapAt = (m: Key[], u: number) => {
  let i = 0;
  while (i + 2 < m.length && m[i + 1][0] < u) i++;
  return lerp(m[i][1], m[i + 1][1], (u - m[i][0]) / (m[i + 1][0] - m[i][0]));
};
const tokenColor = (s: number) => (s < A_CC_IN ? K.fwd : s < A_CC_OUT ? K.card : K.ret);

// SegBar rows inside the PCIe band: one µs scale for all rows
const SX = 700;
const PX = 170;
const ROWS = [440, 530, 620];
const RH = 40;
const sliverX = (fwd: number) => SX + fwd * PX + 0.02 * PX;

function Body() {
  const t = useT();
  const rt = useRealT();

  // three token sets: generic loop (T0–T1), W = 1 (T2), W = 64 (T3 on; pile moves to the return side at T5)
  const sets = [
    { n: 4, period: 4.0, k: fade(t, T[0] + 0.3, T[2] - 0.45), map: (u: number) => mapAt(MAP_W1, u), label: "" },
    { n: 1, period: 2.0, k: fade(t, T[2] + 0.3, T[3] - 0.45), map: (u: number) => mapAt(MAP_W1, u), label: "= 1" },
    {
      n: 16,
      period: 8.0,
      k: prog(t, T[3] + 0.1, 0.6),
      map: (u: number) => lerp(mapAt(MAP_FWD, u), mapAt(MAP_RET, u), prog(t, T[5] + 0.6, 3.0)),
      label: "= 64",
    },
  ];
  const tokenDim = 1 - 0.7 * prog(t, T[6] + 0.3, 1.0);
  const rowsDim = 1 - prog(t, T[6] + 0.3, 0.6); // the takeaway takes the rows' place at T6

  const active = t < T[2] ? -1 : prog(t, T[3], 0.9);

  // T1 path highlights
  const hlF = prog(t, T[1] + 0.8, 1.2);
  const hlC = prog(t, T[1] + 2.2, 0.6);
  const hlR = prog(t, T[1] + 3.0, 1.2);
  const hlK = fade(t, T[1] + 0.6, T[2] - 0.45);

  // SegBar rows
  const r1 = prog(t, T[2] + 1.2, 1.4);
  const r2 = prog(t, T[3] + 1.6, 1.6);
  const r3 = prog(t, T[5] + 2.0, 1.6);
  const barsK = prog(t, T[2] + 1.0, 0.6);

  // rhythm pulse (T6)
  const pulseK = prog(t, T[6] + 0.8, 0.8);
  const ps = ((rt / 3.2) % 1) * A_END;

  // T4 spacing ticks (host burst 9 ns apart vs card arrivals ~75 ns apart; 1 px = 1 ns)
  const tickK = fade(t, T[4] + 0.3, T[5] - 0.45);
  const TX = 790;

  return (
    <>
      <Stage>
        <Rig g={G} cores={4} labels={{ link: [""] }} />

        {/* T1: the three timed segments along the loop */}
        <g opacity={hlK} strokeLinecap="round" strokeLinejoin="round" fill="none">
          <path d={pathD(slicePath(LOOP, CUM, 0, A_CC_IN * hlF))} stroke={K.fwd} strokeWidth={16} opacity={0.35} />
          <path d={pathD(slicePath(LOOP, CUM, A_CC_IN, lerp(A_CC_IN, A_CC_OUT, hlC)))} stroke={K.card} strokeWidth={16} opacity={0.6} />
          <path d={pathD(slicePath(LOOP, CUM, A_CC_OUT, lerp(A_CC_OUT, A_END, hlR)))} stroke={K.ret} strokeWidth={16} opacity={0.35} />
        </g>
        <g opacity={fade(t, T[1] + 2.0, T[2] - 0.45)}>
          <Stopwatch x={1790} y={418} spin={rt * 3} />
          <text x={1790} y={378} textAnchor="middle" fontSize={24} fontWeight={700} fill={K.timer}>arrive</text>
          <Stopwatch x={1790} y={668} spin={rt * 3 + 1} />
          <text x={1790} y={726} textAnchor="middle" fontSize={24} fontWeight={700} fill={K.timer}>leave</text>
          <text x={1727} y={812} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.timer}>card timestamps</text>
        </g>

        {/* window indicator (host side) */}
        {sets.map((st, j) => {
          if (st.k <= 0) return null;
          const per = Math.min(8, st.n);
          return (
            <g key={j} opacity={st.k}>
              <text x={96} y={752} fontSize={24} fontWeight={800} fill={K.host}>window W {st.label}</text>
              {Array.from({ length: st.n }, (_, i) => {
                const u = (rt / st.period + i / st.n) % 1;
                const freed = u < 0.07;
                const x = 96 + (i % per) * 28;
                const y = 766 + Math.floor(i / per) * 34;
                return (
                  <rect key={i} x={x} y={y} width={22} height={28} rx={5} fill={freed ? K.ret : K.fwd} opacity={freed ? 1 : 0.85}
                    stroke={freed ? C.text : "none"} strokeWidth={2} />
                );
              })}
            </g>
          );
        })}
        <text x={96} y={850} fontSize={24} fill={C.muted} opacity={fade(t, T[0] + 1.5, T[2] - 0.45)}>reply in → next out</text>

        {/* tokens */}
        <g opacity={tokenDim}>
          {sets.map((st, j) =>
            st.k <= 0
              ? null
              : Array.from({ length: st.n }, (_, i) => {
                  const u = (rt / st.period + i / st.n) % 1;
                  const s = st.map(u);
                  const [x, y] = atLen(LOOP, CUM, s);
                  const col = tokenColor(s);
                  return (
                    <g key={`${j}-${i}`} opacity={st.k}>
                      <circle cx={x} cy={y} r={20} fill={col} opacity={0.18} />
                      <rect x={x - 11} y={y - 11} width={22} height={22} rx={5} fill={col} stroke={C.bg} strokeWidth={2.5} />
                    </g>
                  );
                }),
          )}
        </g>

        {/* rhythm pulse */}
        {pulseK > 0 && (
          <g opacity={pulseK}>
            <path d={pathD(slicePath(LOOP, CUM, Math.max(0, ps - 280), ps))} stroke={C.text} strokeWidth={8} fill="none" opacity={0.45}
              strokeLinecap="round" strokeLinejoin="round" />
            <circle cx={atLen(LOOP, CUM, ps)[0]} cy={atLen(LOOP, CUM, ps)[1]} r={14} fill={C.text} />
          </g>
        )}

        {/* headline area (between the panels, above the PCIe band) */}
        <text x={960} y={300} textAnchor="middle" fontSize={28} fontWeight={700} fill={C.text} opacity={fade(t, T[0] + 1.0, T[1] - 0.45)}>
          a reply frees a slot → the next request leaves
        </text>
        <text x={960} y={300} textAnchor="middle" fontSize={28} fontWeight={700} fill={K.timer} opacity={fade(t, T[1] + 0.6, T[2] - 0.45)}>
          one round trip = forward + on card + return
        </text>
        <g opacity={fade(t, T[2] + 0.6, T[3] - 0.45)}>
          <text x={960} y={262} textAnchor="middle" fontSize={48} fontWeight={800} fill={C.text}>round trip 0.92 µs</text>
          <text x={960} y={306} textAnchor="middle" fontSize={24} fill={C.muted}>W = 1 · median (p50)</text>
        </g>
        <g opacity={fade(t, T[3] + 0.6, T[4] - 0.45)}>
          <text x={960} y={262} textAnchor="middle" fontSize={46} fontWeight={800} fill={C.text}>13.5 M/s · round trip 3.7 µs</text>
          <text x={960} y={306} textAnchor="middle" fontSize={24} fill={C.muted}>W = 64 · p50 · 1 host core + 4 card cores</text>
        </g>
        <g opacity={tickK}>
          <text x={600} y={240} fontSize={24} fontWeight={700} fill={K.host}>host sends</text>
          <text x={600} y={296} fontSize={24} fontWeight={700} fill={K.card}>card gets</text>
          {Array.from({ length: 8 }, (_, i) => (
            <g key={i}>
              <line x1={TX + i * 9} y1={216} x2={TX + i * 9} y2={244} stroke={K.host} strokeWidth={4} opacity={prog(t, T[4] + 0.6 + i * 0.05, 0.2)} />
              <line x1={TX + i * 75} y1={272} x2={TX + i * 75} y2={300} stroke={K.card} strokeWidth={4} opacity={prog(t, T[4] + 1.2 + i * 0.35, 0.2)} />
            </g>
          ))}
          <text x={TX + 90} y={240} fontSize={26} fontWeight={800} fill={K.host}>burst: 9 ns apart</text>
          <text x={TX + 75 * 3.5} y={326} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.card} opacity={prog(t, T[4] + 3.4, 0.5)}>
            ~75 ns apart
          </text>
        </g>
        <g opacity={fade(t, T[5] + 0.6, T[6] - 0.45)}>
          <text x={960} y={262} textAnchor="middle" fontSize={40} fontWeight={800} fill={K.ret}>same rate, queue on the return side</text>
          <text x={960} y={306} textAnchor="middle" fontSize={24} fill={C.muted}>caveat · 2 runs without pre-touched pages</text>
        </g>

        {/* T4: the pile in the forward lane */}
        <g opacity={fade(t, T[4] + 1.6, T[5] - 0.45)}>
          <text x={1279} y={344} textAnchor="middle" fontSize={26} fontWeight={800} fill={K.wait}>~42 waiting</text>
          <text x={1324} y={404} textAnchor="end" fontSize={24} fill={C.muted}>13.5 M/s × 3.1 µs, Little's law</text>
        </g>

        {/* latency rows: forward · on card · return, one µs scale */}
        <g opacity={barsK * rowsDim}>
          <text x={688} y={ROWS[0] + 29} textAnchor="end" fontSize={24} fontWeight={700} fill={C.text}>W = 1</text>
          <SegBar x={SX} y={ROWS[0]} h={RH} px={PX} k={r1} segs={[
            { label: "0.45", value: 0.45, color: K.fwd }, { label: "", text: "", value: 0.04, color: K.card }, { label: "0.43", value: 0.43, color: K.ret },
          ]} />
          <g opacity={prog(t, T[2] + 2.4, 0.5)}>
            <line x1={sliverX(0.45)} y1={ROWS[0] - 4} x2={sliverX(0.45)} y2={ROWS[0] - 14} stroke={K.card} strokeWidth={3} />
            <text x={sliverX(0.45)} y={ROWS[0] - 20} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.card}>0.04 on card</text>
          </g>
        </g>
        <g opacity={prog(t, T[3] + 1.4, 0.5) * rowsDim}>
          <text x={688} y={ROWS[1] + 29} textAnchor="end" fontSize={24} fontWeight={700} fill={C.text}>W = 64</text>
          <SegBar x={SX} y={ROWS[1]} h={RH} px={PX} k={r2} segs={[
            { label: "3.11", value: 3.11, color: K.fwd }, { label: "", text: "", value: 0.04, color: K.card }, { label: "0.52", value: 0.52, color: K.ret },
          ]} />
          <g opacity={prog(t, T[3] + 3.4, 0.5)}>
            <line x1={sliverX(3.11)} y1={ROWS[1] + RH + 4} x2={sliverX(3.11)} y2={ROWS[1] + RH + 12} stroke={K.card} strokeWidth={3} />
            <text x={sliverX(3.11)} y={ROWS[1] + RH + 34} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.card}>0.04</text>
          </g>
          <g opacity={prog(t, T[3] + 5.0, 0.6)}>
            <line x1={SX + 0.45 * PX} y1={ROWS[0] + RH + 4} x2={SX + 0.45 * PX} y2={ROWS[1] - 4} stroke={C.muted} strokeWidth={2} strokeDasharray="4 5" />
            <Brace x1={SX + 0.45 * PX} x2={SX + 3.11 * PX} y={ROWS[1] - 6} dir={-1} color={K.wait} label="extra ~2.7 µs forward" />
          </g>
        </g>
        <g opacity={prog(t, T[5] + 1.8, 0.5) * rowsDim}>
          <text x={688} y={ROWS[2] + 29} textAnchor="end" fontSize={24} fontWeight={700} fill={C.muted}>W = 64</text>
          <text x={SX} y={ROWS[2] - 14} fontSize={24} fontWeight={700} fill={K.ret}>pages not pre-touched · 13.4 M/s</text>
          <SegBar x={SX} y={ROWS[2]} h={RH} px={PX} k={r3} segs={[
            { label: "0.73", value: 0.73, color: K.fwd }, { label: "", text: "", value: 0.04, color: K.card }, { label: "2.67", value: 2.67, color: K.ret },
          ]} />
          <g opacity={prog(t, T[5] + 3.8, 0.5)}>
            <line x1={sliverX(0.73)} y1={ROWS[2] + RH + 4} x2={sliverX(0.73)} y2={ROWS[2] + RH + 12} stroke={K.card} strokeWidth={3} />
            <text x={sliverX(0.73)} y={ROWS[2] + RH + 34} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.card}>0.04</text>
          </g>
        </g>

        {/* legend under the PCIe band */}
        <g opacity={fade(t, T[1] + 1.0, T[6] - 0.45)} fontSize={24} fontWeight={700}>
          <rect x={640} y={790} width={22} height={22} rx={4} fill={K.fwd} />
          <text x={672} y={809} fill={K.fwd}>forward</text>
          <rect x={830} y={790} width={22} height={22} rx={4} fill={K.card} />
          <text x={862} y={809} fill={K.card}>on card</text>
          <rect x={1020} y={790} width={22} height={22} rx={4} fill={K.ret} />
          <text x={1052} y={809} fill={K.ret}>return</text>
          <text x={1200} y={809} fill={C.muted} fontWeight={400} fontFamily={MONO}>µs, p50</text>
        </g>
      </Stage>
      <KnobBar fixed={["closed loop", "WC replies", "4 card cores"]} knob="window W (messages in flight)" options={["1", "64"]}
        active={active} k={prog(t, T[0], 0.6)} />
      <Takeaway x={590} y={430} w={740} k={prog(t, T[6] + 0.9, 0.8)} color={K.inf} label="OUR READING" size={30}>
        <InfPill size={22} style={{ marginRight: 10 }} />~13 M/s is not one stage's speed limit — the queue forms where the loop's rhythm
        puts it.
      </Takeaway>
      <Note x={60} y={862}>tokens are schematic (16 drawn for W = 64)</Note>
    </>
  );
}

export const S05: SceneDef = {
  id: "S05",
  title: "Exp 3 · Inside the slow loop",
  stage: 2,
  source: "MRV-50 · logs/mrv50-2026-11-17 · card-side timestamps",
  dur: DUR,
  cues,
  C: Body,
};
