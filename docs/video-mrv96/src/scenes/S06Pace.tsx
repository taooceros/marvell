import React from "react";
import {
  Axes,
  C,
  Chip as Die,
  Cue,
  Edge,
  K,
  MONO,
  Note,
  SceneDef,
  Series,
  Stage,
  clamp01,
  lerp,
  lin,
  linScale,
  prog,
  useRealT,
  useT,
} from "../lib";
import { Brace, Chip, InfPill, KnobBar, SegBar } from "./kit";
import { slide, win } from "./_Mrv96SceneOpen";

// <cues>
const cues: Cue[] = [
  [0.60, 9.62, "**Experiment 4**: who sets the pace? Add a known delay **δ** to every message on one side, and watch the time per message.", "Experiment four asks who sets the pace. The trick: add a known delay, delta, to every message on one side, and watch the time per message."], // 0
  [9.97, 21.07, "δ is a chain of dependent multiplies between two fences, so the CPU can't hide it. At W = 1 it adds what we ask: **+40 ns → +40.1 ns**.", "Delta is a chain of dependent multiplies between two fences, so the processor cannot hide it. With a window of one, it adds what we ask: forty nanoseconds become forty point one."], // 1
  [21.42, 33.22, "Each run sends **20 million** messages; time per message = total ÷ 20 M. **Slope 1**: that side is on the critical path. **Slope 0**: it was waiting anyway.", "Each run sends twenty million messages, so time per message resolves changes of a fraction of a nanosecond. Slope one: that side is on the critical path. Slope zero: it was waiting anyway."], // 2
  [33.57, 43.73, "Fast state (1 card core, W = 1024): host slope **1.07**, card slope **1.07**. Both sides are on the critical path, tightly interlocked.", "Fast state, one card core, window ten twenty-four: both slopes are one point zero seven. Host and card code are both on the critical path, tightly interlocked."], // 3
  [44.08, 57.36, "Slow state (4 card cores, W = 64): adding up to **59 ns** on the card changes nothing, **76.1 → 75.6 ns** per message. The card just waits.", "Slow state, four card cores, window sixty-four: adding up to fifty-nine nanoseconds on the card changes nothing. Seventy-six point one nanoseconds per message becomes seventy-five point six. The card just waits."], // 4
  [57.71, 71.29, "Surprise on the host: **+10 ns makes it faster**, **76.2 → 60.9 ns** per message. **[INF]** The host's bursts, not its code, seem to trigger the slow state.", "The surprise is on the host: adding ten nanoseconds makes it faster, from seventy-six point two to sixty point nine nanoseconds per message. Our reading: the host's bursts, not its code, seem to trigger the slow state."], // 5
  [71.64, 88.58, "At W = 1024, **4 card cores lose to 1**: **23.9 vs 50.5 M/s**. Sending costs the same **~9 ns**; reply polling grows **13.6 → 32.5 ns** across four rings.", "And at window ten twenty-four, four card cores do worse than one: twenty-three point nine, versus fifty point five million per second. Sending still costs nine nanoseconds, but reply polling grows from thirteen point six to thirty-two point five, waiting across four rings."], // 6
];
const DUR = 89.98; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---- left: schematic timelines of the host loop and the card loop; one period = one message
const SX0 = 170;
const SX1 = 750;
const RH = 60;
const HY = 268;
const CY = 400;
const WAIT = "rgba(179,136,255,0.3)";
type Cfg = { P: number; hw: number; hd: number; cw: number; cd: number; co: number };
/** Base period = 1; host work hw + δ hd, card work cw + δ cd starting at phase co; the rest of a period is waiting. */
const CFG0: Cfg = { P: 1, hw: 0.45, hd: 0, cw: 0.4, cd: 0, co: 0.5 };
const CFG: [number, Partial<Cfg>][] = [
  [T[0] + 4.5, { P: 1.3, hd: 0.3, co: 0.8 }], // δ on the host, on the critical path: the period grows
  [T[3] + 0.6, { P: 1, hw: 0.5, hd: 0, cw: 0.5, co: 0.5 }], // fast state: each side only waits for the other
  [T[3] + 1.4, { P: 1.3, hd: 0.3, co: 0.8 }],
  [T[3] + 4.8, { hd: 0, cd: 0.3, co: 0.5 }], // δ moved to the card: the period still grows
  [T[4] + 0.5, { P: 1, cw: 0.25, cd: 0 }], // slow state: the card waits most of the time
  [T[4] + 4.0, { cd: 0.3 }], // card δ only fills its wait
  [T[5] + 0.3, { cd: 0 }],
  [T[5] + 1.5, { P: 0.8, hd: 0.15, co: 0.55 }], // host δ: the period shrinks
];

/** One timeline row: blocks [length in base periods, fill, label] repeat every P from `offset`, clipped to the strip. */
function Row({ y, offset, P, U, blocks }: { y: number; offset: number; P: number; U: number; blocks: [number, string, string?][] }) {
  const out: React.ReactNode[] = [];
  for (let k = Math.floor(-offset / P) - 1; SX0 + (offset + k * P) * U < SX1; k++) {
    let x = SX0 + (offset + k * P) * U;
    out.push(<line key={`p${k}`} x1={x} y1={y - 6} x2={x} y2={y + RH + 6} stroke={C.text} strokeWidth={2} opacity={0.35} />);
    blocks.forEach(([len, fill, label], j) => {
      const w = len * U;
      if (w > 0.5) {
        // a δ block drops into place while it grows (and rises out while it shrinks)
        const drop = label ? clamp01(len / 0.12) : 1;
        out.push(
          <g key={`${k}-${j}`} opacity={drop} transform={`translate(0 ${(drop - 1) * 36})`}>
            <rect x={x} y={y} width={w} height={RH} fill={fill} />
            {label && w >= 28 && (
              <text x={x + w / 2} y={y + RH / 2 + 9} textAnchor="middle" fontSize={26} fontWeight={800} fill={C.bg}>
                {label}
              </text>
            )}
          </g>,
        );
      }
      x += w;
    });
  }
  return <g clipPath="url(#s06strip)">{out}</g>;
}

// ---- right: ns per message vs added δ; the y range changes with the state under test
const X0 = 940;
const X1 = 1780;
const Y0 = 620;
const Y1 = 260;
const sx = linScale(0, 60, X0, X1);
const syCal = linScale(900, 980, Y0, Y1);
const syFast = linScale(0, 100, Y0, Y1);
const sySlow = linScale(50, 90, Y0, Y1);
const XT: [number, string][] = [0, 10, 20, 30, 40, 50, 60].map((v) => [v, String(v)]);
const yt = (vs: number[]) => vs.map((v) => [v, String(v)] as [number, string]);
const CAL: [number, number][] = [
  [0, 924.2],
  [10, 934.9],
  [20, 944.7],
  [40, 964.3],
];
const CARD: [number, number][] = [
  [0, 76.1],
  [32, 75.9],
  [45, 75.7],
  [59, 75.6],
];
const HOST: [number, number][] = [
  [0, 76.2],
  [10, 60.9],
  [20, 61.4],
  [40, 78.3],
];
const FAST0 = 24.8; // this series' baseline (ns per message): positions the fitted lines, never printed
const RING_LIT = [5, 2, 6, 3];
/** Host-δ value labels [dx, dy, anchor], clear of the line and of the ring around the dip. */
const HOST_LAB: [number, number, "start" | "middle" | "end"][] = [
  [14, -18, "start"],
  [-34, 8, "end"],
  [0, 34, "middle"],
  [0, -18, "middle"],
];

function Body() {
  const t = useT();
  const rt = useRealT();
  const c = { ...CFG0 };
  (Object.keys(c) as (keyof Cfg)[]).forEach((f) => {
    const keys = CFG.filter(([, p]) => p[f] !== undefined).map(([at, p]) => [at, p[f] as number] as [number, number]);
    c[f] = slide(t, keys, CFG0[f], 0.8);
  });
  const zoom = prog(t, T[2] + 0.5, 1.0) * (1 - prog(t, T[2] + 7.0, 1.0)); // cue 2: zoom out to the whole run
  const U = lerp(200, 30, zoom);
  const tlK = prog(t, T[0] + 0.2, 0.6) * (1 - prog(t, T[6] - 0.45, 0.45));
  const pw = c.P * U;
  const calK = win(t, T[1] + 5.2, T[3]);
  const fastK = win(t, T[3] + 0.2, T[4]);
  const slowK = win(t, T[4] + 0.2, T[6]);
  const cardDim = lerp(1, 0.3, prog(t, T[5], 0.6));
  const poll = Math.floor(rt / 0.45) % 4;
  const ringY = (r: number) => 486 + r * 60;
  const seg = (send: number, polling: number, label: string) => {
    const f = send / (send + polling);
    return {
      k: f * prog(t, T[6] + 7.0, 0.8) + (1 - f) * prog(t, T[6] + 10.0, 1.2),
      segs: [
        { label: "send", value: send, color: K.fwd, text: `send ${send}` },
        { label: "polling", value: polling, color: K.ret, text: `${label} ${polling}` },
      ],
    };
  };
  const one = seg(9.2, 13.6, "polling");
  const four = seg(9.3, 32.5, "reply polling");
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
        <defs>
          <clipPath id="s06strip">
            <rect x={SX0} y={200} width={SX1 - SX0} height={700} />
          </clipPath>
        </defs>

        {/* timelines (cues 0–5) */}
        <g opacity={tlK}>
          <text x={155} y={HY + RH / 2 + 9} textAnchor="end" fontSize={26} fontWeight={800} fill={K.host}>
            host
          </text>
          <text x={155} y={CY + RH / 2 + 9} textAnchor="end" fontSize={26} fontWeight={800} fill={K.card}>
            card
          </text>
          <Row y={HY} offset={0} P={c.P} U={U}
            blocks={[[c.hw, K.host], [c.hd, K.knob, "δ"], [Math.max(0, c.P - c.hw - c.hd), WAIT]]} />
          <Row y={CY} offset={c.co} P={c.P} U={U}
            blocks={[[c.cw, K.card], [c.cd, K.knob, "δ"], [Math.max(0, c.P - c.cw - c.cd), WAIT]]} />
          <g opacity={1 - zoom}>
            <line x1={SX0} y1={254} x2={SX0 + pw} y2={254} stroke={C.text} strokeWidth={3} />
            {c.P > 1.001 && <line x1={SX0 + U} y1={254} x2={SX0 + pw} y2={254} stroke={K.knob} strokeWidth={6} />}
            {c.P < 0.999 && (
              <line x1={SX0 + pw} y1={254} x2={SX0 + U} y2={254} stroke={K.fast} strokeWidth={4} strokeDasharray="6 5" />
            )}
            <line x1={SX0} y1={244} x2={SX0} y2={264} stroke={C.text} strokeWidth={3} />
            <line x1={SX0 + pw} y1={244} x2={SX0 + pw} y2={264} stroke={C.text} strokeWidth={3} />
            <text x={SX0 + pw / 2} y={236} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.text}>
              time per message
            </text>
          </g>
          <Brace x1={SX0} x2={SX1} y={258} dir={-1} label="20,000,000 messages per run" size={24} color={C.text} opacity={zoom} />
          {(
            [
              [90, K.host, "host work"],
              [262, K.card, "card work"],
              [434, K.knob, "δ"],
              [520, WAIT, "waiting"],
            ] as [number, string, string][]
          ).map(([x, col, lab]) => (
            <g key={lab} opacity={prog(t, T[0] + 1.0, 0.5)}>
              <rect x={x} y={538} width={22} height={22} rx={4} fill={col} stroke={col === WAIT ? K.wait : col} strokeWidth={2} />
              <text x={x + 30} y={557} fontSize={24} fill={C.text}>
                {lab}
              </text>
            </g>
          ))}
          {/* cue 5: the host's send pattern, as we read it */}
          <g opacity={win(t, T[5] + 7.6, T[6])}>
            <text x={90} y={628} fontSize={24} fill={C.muted}>
              host sends (schematic)
            </text>
            <text x={90} y={681} fontSize={24} fontWeight={700} fill={C.text}>
              no δ
            </text>
            <text x={90} y={745} fontSize={24} fontWeight={700} fill={K.knob}>
              +10 ns
            </text>
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <g key={i}>
                <rect x={200 + i * 22} y={652} width={12} height={40} rx={3} fill={K.fwd} />
                <rect x={200 + i * 92} y={716} width={12} height={40} rx={3} fill={K.fwd} />
              </g>
            ))}
            <text x={350} y={681} fontSize={24} fill={C.muted}>
              burst, then wait
            </text>
          </g>
        </g>

        {/* cue 6: the host polls one reply ring per card core */}
        <g opacity={win(t, T[6] + 0.3)}>
          <text x={90} y={250} fontSize={26} fill={C.muted}>
            the host core polls the reply rings
          </text>
          <text x={400} y={312} fontSize={24} fill={C.text}>
            1 card core → 1 reply ring
          </text>
          <text x={400} y={468} fontSize={24} fill={C.text}>
            4 card cores → 4 reply rings
          </text>
          <Die x={180} y={344} w={160} h={70} label="host core" color={K.host} fontSize={24} />
          <Die x={180} y={594} w={160} h={70} label="host core" color={K.host} fontSize={24} />
          <line x1={264} y1={344} x2={392} y2={344} stroke={K.probe} strokeWidth={3} strokeDasharray="6 5" />
          <line x1={264} y1={594} x2={392} y2={ringY(poll) + 18} stroke={K.probe} strokeWidth={3} strokeDasharray="6 5" />
          <polygon points={`${392},${ringY(poll) + 8} ${392},${ringY(poll) + 28} ${404},${ringY(poll) + 18}`} fill={K.probe} />
          {[326, ...[0, 1, 2, 3].map(ringY)].map((y, r) => (
            <g key={r}>
              {Array.from({ length: 8 }, (_, i) => (
                <rect key={i} x={410 + i * 36} y={y} width={30} height={36} rx={5}
                  fill={i < (r === 0 ? 5 : RING_LIT[r - 1]) ? K.ret : "none"} opacity={0.85} stroke={K.ret} strokeWidth={1.5} />
              ))}
            </g>
          ))}
        </g>

        {/* chart frame before the first data (cue 0) */}
        <Axes x0={X0} x1={X1} y0={Y0} y1={Y1} sx={sx} sy={syCal} xticks={XT} yticks={[]} xlabel="added δ (ns)"
          ylabel="ns per message" size={24} opacity={win(t, T[0] + 6.5, T[1] + 5.6)} />

        {/* cues 1–2: calibration at W = 1 */}
        {calK > 0 && (
          <g opacity={calK}>
            <Axes x0={X0} x1={X1} y0={Y0} y1={Y1} sx={sx} sy={syCal} xticks={XT} yticks={yt([900, 920, 940, 960, 980])}
              xlabel="added δ (ns)" ylabel="ns per message" size={24} />
            <Edge x1={sx(0)} y1={syCal(924.2)} x2={sx(55.8)} y2={syCal(980)} k={prog(t, T[1] + 5.8, 0.8)} color={C.muted} w={3}
              dash="10 8" />
            <Edge x1={sx(0)} y1={syCal(924.2)} x2={sx(60)} y2={syCal(924.2)} k={prog(t, T[2] + 9.4, 0.8)} color={K.wait} w={3}
              dash="10 8" />
            <Series pts={CAL} sx={sx} sy={syCal} k={lin(t, T[1] + 6.0, 2.4)} color={K.host} w={4} r={8} />
            {CAL.map(([x, y], i) => (
              <text key={x} x={sx(x) + 14} y={syCal(y) + 30} fontSize={24} fontWeight={700} fill={C.text}
                opacity={prog(t, T[1] + 6.0 + 0.8 * i, 0.4)}>
                {y}
              </text>
            ))}
            <text x={960} y={302} fontSize={28} fontWeight={800} opacity={prog(t, T[1] + 8.8, 0.5)}>
              <tspan fill={K.knob}>+40 ns</tspan>
              <tspan fill={C.text}> → +40.1 ns</tspan>
              <tspan fill={K.fast}> · slope 1.01</tspan>
            </text>
          </g>
        )}

        {/* cue 3: fast state, fitted slopes */}
        {fastK > 0 && (
          <g opacity={fastK}>
            <Axes x0={X0} x1={X1} y0={Y0} y1={Y1} sx={sx} sy={syFast} xticks={XT} yticks={yt([0, 25, 50, 75, 100])}
              xlabel="added δ (ns)" ylabel="ns per message" size={24} />
            <Edge x1={sx(0)} y1={syFast(FAST0)} x2={sx(40)} y2={syFast(FAST0 + 40)} k={prog(t, T[3] + 0.8, 0.8)} color={C.muted}
              w={3} dash="10 8" />
            <Edge x1={sx(0)} y1={syFast(FAST0)} x2={sx(40)} y2={syFast(FAST0 + 1.07 * 40)} k={prog(t, T[3] + 3.8, 1.0)}
              color={K.host} w={7} />
            <Edge x1={sx(0)} y1={syFast(FAST0)} x2={sx(40)} y2={syFast(FAST0 + 1.07 * 40)} k={prog(t, T[3] + 5.2, 1.0)}
              color={K.card} w={3} dash="14 8" />
            <text x={1514} y={syFast(FAST0 + 1.07 * 40) - 8} fontSize={28} fontWeight={800} fill={K.host} opacity={prog(t, T[3] + 4.6, 0.5)}>
              host slope 1.07
            </text>
            <text x={1514} y={syFast(FAST0 + 1.07 * 40) + 30} fontSize={28} fontWeight={800} fill={K.card} opacity={prog(t, T[3] + 6.0, 0.5)}>
              card slope 1.07
            </text>
          </g>
        )}

        {/* cues 4–5: slow state */}
        {slowK > 0 && (
          <g opacity={slowK}>
            <Axes x0={X0} x1={X1} y0={Y0} y1={Y1} sx={sx} sy={sySlow} xticks={XT} yticks={yt([50, 60, 70, 80, 90])}
              xlabel="added δ (ns)" ylabel="ns per message" size={24} />
            <g opacity={win(t, T[4] + 5.0, T[5])}>
              <Edge x1={sx(0)} y1={sySlow(76.1)} x2={sx(13.9)} y2={sySlow(90)} k={prog(t, T[4] + 5.0, 0.6)} color={C.muted} w={3}
                dash="10 8" />
            </g>
            <g opacity={cardDim}>
              <Series pts={CARD} sx={sx} sy={sySlow} k={lin(t, T[4] + 5.2, 3.0)} color={K.card} w={4} r={8} />
            </g>
            {CARD.map(([x, y], i) => (
              <text key={x} x={i ? sx(x) : sx(x) + 14} y={sySlow(y) + 34} textAnchor={i ? "middle" : "start"} fontSize={24}
                fontWeight={700} fill={C.text} opacity={win(t, T[4] + 5.2 + i, T[5])}>
                {y}
              </text>
            ))}
            <text x={1350} y={472} fontSize={30} fontWeight={800} fill={K.card} opacity={win(t, T[4] + 11.0, T[5])}>
              card just waits
            </text>
            <Series pts={HOST} sx={sx} sy={sySlow} k={lin(t, T[5] + 1.2, 3.0)} color={K.host} w={4} r={8} />
            {HOST.map(([x, y], i) => (
              <text key={x} x={sx(x) + HOST_LAB[i][0]} y={sySlow(y) + HOST_LAB[i][1]} textAnchor={HOST_LAB[i][2]} fontSize={24}
                fontWeight={700} fill={C.text} opacity={prog(t, T[5] + 1.2 + i, 0.4)}>
                {y}
              </text>
            ))}
            <g opacity={prog(t, T[5] + 3.6, 0.5)}>
              <circle cx={sx(10)} cy={sySlow(60.9)} r={24 + 3 * Math.sin(rt * 5)} fill="none" stroke={K.fast} strokeWidth={4} />
              <text x={1110} y={598} fontSize={28} fontWeight={800} fill={K.fast}>
                +10 ns → faster
              </text>
            </g>
          </g>
        )}

        {/* slope legend (cues 2–5) */}
        <g opacity={win(t, T[2] + 7.0, T[6])}>
          <line x1={940} y1={790} x2={980} y2={766} stroke={C.muted} strokeWidth={4} strokeDasharray="8 5" />
          <text x={996} y={790} fontSize={28} fill={C.text}>
            slope 1 = on the critical path
          </text>
        </g>
        <g opacity={win(t, T[2] + 9.4, T[6])}>
          <line x1={940} y1={828} x2={980} y2={828} stroke={K.wait} strokeWidth={4} strokeDasharray="8 5" />
          <text x={996} y={838} fontSize={28} fill={C.text}>
            slope 0 = it was waiting anyway
          </text>
        </g>

        {/* cue 6: host time per message at W = 1024 */}
        <g opacity={win(t, T[6] + 0.3)}>
          <text x={1020} y={292} fontSize={26} fill={C.muted}>
            host time per message at W = 1024 (ns)
          </text>
          {[
            { y: 350, name: "1 card core", rate: "50.5 M/s", col: K.fast, bar: one },
            { y: 510, name: "4 card cores", rate: "23.9 M/s", col: K.slow, bar: four },
          ].map((b, i) => (
            <g key={b.name}>
              <text x={1000} y={b.y + 26} textAnchor="end" fontSize={28} fontWeight={800} fill={C.text}>
                {b.name}
              </text>
              <text x={1000} y={b.y + 62} textAnchor="end" fontSize={30} fontWeight={800} fill={b.col}
                opacity={prog(t, T[6] + 1.6 + 1.6 * i, 0.5)}>
                {b.rate}
              </text>
              <SegBar x={1020} y={b.y} h={64} px={16} segs={b.bar.segs} k={b.bar.k} size={24} />
            </g>
          ))}
        </g>
      </Stage>

      {/* δ = dependent multiplies between two fences (cues 1–2) */}
      <div style={abs(SX0, 342, win(t, T[1] + 0.8, T[3]))}>
        <Chip color={K.host} size={22} style={{ fontFamily: MONO }}>
          lfence · imul × R · lfence
        </Chip>
      </div>
      <div style={abs(SX0, 474, win(t, T[1] + 1.6, T[3]))}>
        <Chip color={K.card} size={22} style={{ fontFamily: MONO }}>
          isb · mul × R · isb
        </Chip>
      </div>
      <div style={abs(880, 714, win(t, T[2] + 1.2, T[3]), { fontSize: 26 })}>
        20,000,000 messages per run → time per message = total ÷ 20 M
      </div>
      <div style={abs(1780, 216, 1, { fontSize: 26, fontWeight: 700, whiteSpace: "nowrap" })}>
        <span style={{ position: "absolute", right: 0, opacity: calK }}>W = 1 · 1 card core</span>
        <span style={{ position: "absolute", right: 0, opacity: fastK }}>fast state · 1 card core · W = 1024</span>
        <span style={{ position: "absolute", right: 0, opacity: slowK }}>slow state · 4 card cores · W = 64</span>
      </div>
      <div style={abs(1514, syFast(FAST0 + 1.07 * 40) + 46, win(t, T[3] + 4.6, T[4]))}>
        <Note x={0} y={0} w={300}>
          fitted slopes
        </Note>
      </div>
      <div style={abs(90, 640, win(t, T[3] + 7.0, T[4]), { width: 660, fontSize: 26, lineHeight: 1.3 })}>
        tightly interlocked: each side only waits for the other
      </div>
      <div style={abs(90, 640, win(t, T[4] + 4.6, T[5]), { width: 660, fontSize: 26, lineHeight: 1.3, color: K.wait })}>
        the card's δ only fills time it spent waiting
      </div>
      <div style={abs(90, 776, win(t, T[5] + 8.4, T[6]), { width: 730, fontSize: 24, lineHeight: 1.3 })}>
        <InfPill size={24} /> bursts, not code, seem to trigger the slow state
      </div>
      <div style={{ opacity: prog(t, T[0] + 1.0, 0.5) }}>
        <Note x={90} y={862} w={640}>
          timelines and rings are schematic
        </Note>
      </div>

      <KnobBar fixed={["closed loop", "WC replies", "20 M msgs / run"]} knob="extra delay δ per message, on the"
        options={["host", "card"]} active={t < T[1] || t >= T[6] ? -1 : slide(t, [[T[3] + 4.8, 1], [T[5], 0]], 0)}
        k={prog(t, 0.1, 0.5)} />
    </>
  );
}

export const S06: SceneDef = {
  id: "S06",
  title: "Exp 4 · Who sets the pace?",
  stage: 2,
  source: "MRV-53 · logs/mrv53-2026-11-17 · 2×10⁷ msgs per run",
  dur: DUR,
  cues,
  C: Body,
};
