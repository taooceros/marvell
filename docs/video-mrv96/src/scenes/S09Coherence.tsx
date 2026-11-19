import {
  Arrow,
  C,
  Chip as Die,
  Cue,
  K,
  MONO,
  Note,
  PacketStream,
  Pt,
  SceneDef,
  Stage,
  clamp01,
  lerp,
  prog,
  rng,
  useRealT,
  useT,
} from "../lib";
import { BarChart, Brace, Chip, KnobBar, Meter, Takeaway } from "./kit";
import { slide, win } from "./_Mrv96SceneOpen";

// <cues>
const cues: Cue[] = [
  [0.60, 13.10, "Second suspect: Linux reports a **128-byte** coherency line for the card's L3. Then cores writing **neighboring 64-byte slots** would fight.", "Second suspect: Linux reports a one hundred twenty-eight byte coherency line for the card's L 3 cache. If true, two cores writing neighboring sixty-four byte slots would fight over one line."], // 0
  [13.45, 25.39, "Fixed: two card cores, each rewriting **its own** 64-byte slot. Knob = the distance between the slots: **0 B, 64 B, 128 B, 256 B, 4 KB**.", "Fixed: two card cores, each rewriting its own sixty-four byte slot, as fast as it can. The knob: the distance between the two slots, from zero bytes up to four kilobytes."], // 1
  [25.74, 36.42, "Same slot (0 B): each core manages only **72.3 M writes/s**, and coherence snoops jump to **17.8 M/s**.", "When both write the same slot, each core manages only seventy-two point three million writes per second, and coherence snoops jump to seventeen point eight million per second."], // 2
  [36.77, 50.73, "**64 B apart**, inside one 128-byte block: **259.8 M/s** per core, snoops at the idle background, **0.52 M/s**. Same at 128 B, 256 B, 4 KB.", "At sixty-four bytes apart, still inside one Linux-sized line, each core runs at two hundred fifty-nine point eight million, and snoops stay at the idle background. The same at one twenty-eight, two fifty-six, and four kilobytes."], // 3
  [51.08, 62.12, "So card cores track coherence per **64 bytes**. Still untested: PCIe writes landing right next to a polling core.", "So the card's cores track coherence per sixty-four bytes, and neighboring slots are safe. Still untested: P C I e writes landing right next to a slot that a core is polling."], // 4
];
const DUR = 63.52; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// Card memory row: 64 B slots in 128 B blocks (the Linux line), four blocks, a break, then the block 4 KB away.
const SW = 96;
const SH = 90;
const RY = 450;
const BLK = 2 * SW + 4;
const slotX = (i: number) => (i < 8 ? 160 + Math.floor(i / 2) * (BLK + 28) : 1120) + (i % 2) * (SW + 4);
const scx = (i: number) => slotX(i) + SW / 2;
const OPTS = ["0 B", "64 B", "128 B", "256 B", "4 KB"];
const OPT_SLOT = [0, 1, 2, 4, 8];
const C3X = scx(0);
/** Knob d (option index): neighbours in cue 0, 0 B at "from zero bytes" (cue 1), the sweep of cue 3, back to 64 B. */
const D_KEYS: [number, number][] = [
  [T[1] + 8.3, 0],
  [T[3] + 0.3, 1],
  [T[3] + 10.3, 2],
  [T[3] + 11.4, 3],
  [T[3] + 12.5, 4],
  [T[4] + 0.3, 1],
];
const BAR_AT = [T[3] + 0.6, T[3] + 1.2, T[3] + 10.5, T[3] + 11.6, T[3] + 12.7];
// right column: meters, then writes per core for each d
const MX = 1400;
const MW = 460;

function Body() {
  const t = useT();
  const rt = useRealT();
  const d = slide(t, D_KEYS, 1);
  const di = Math.round(d);
  const i0 = Math.min(3, Math.floor(d));
  const x4 = lerp(scx(OPT_SLOT[i0]), scx(OPT_SLOT[i0 + 1]), clamp01(d - i0)); // core 4's slot
  const off = 18 * clamp01(1 - d * 3); // arrow tips step apart when both cores hit the same slot
  const c4x = Math.max(x4, C3X + 175);
  const kCores = prog(t, T[0] + 7.0, 0.6);
  const hammer = prog(t, T[1], 0.5);
  const fight = t >= T[2] && d < 0.25;
  // write dots: 300 px/s, slowed to 140 px/s while both cores fight over one slot
  const trav = Math.max(0, rt - T[1]) * 300 - Math.max(0, Math.min(rt, T[3] + 0.3) - T[2]) * 160;
  const a3: Pt[] = [
    [C3X, 356],
    [C3X - off, RY - 8],
  ];
  const a4: Pt[] = [
    [c4x, 356],
    [x4 + off, RY - 8],
  ];
  const flash = 0.5 + 0.5 * Math.sin(rt * 16);
  const wv = slide(t, [[T[2] + 0.5, 72.3], [T[3] + 0.3, 259.8]], 0, 1.0);
  const sv = slide(t, [[T[2] + 4.5, 17.8], [T[3] + 1.0, 0.52]], 0, 1.0);
  const spark = rng(Math.floor(rt * 12));

  return (
    <>
      <Stage>
        {/* 128 B blocks (Linux line) of two 64 B slots each */}
        {[0, 1, 2, 3, 4].map((b) => (
          <g key={b} opacity={prog(t, T[0] + 0.5 + b * 0.24, 0.4)}>
            <rect x={slotX(2 * b) - 6} y={RY - 8} width={BLK + 12} height={SH + 16} rx={12} fill="none" stroke={C.muted}
              strokeWidth={2} strokeDasharray="6 6" />
            {b === 0 && (
              <rect x={slotX(0) - 6} y={RY - 8} width={BLK + 12} height={SH + 16} rx={12} fill="none" stroke={K.slow} strokeWidth={4}
                opacity={win(t, T[0] + 7.4, T[1]) * (0.4 + 0.6 * flash)} />
            )}
          </g>
        ))}
        {Array.from({ length: 10 }, (_, i) => {
          const k = prog(t, T[0] + 0.3 + i * 0.12, 0.4);
          if (k <= 0) return null;
          const hot = kCores > 0 && (i === 0 || i === OPT_SLOT[di]);
          const col = fight && i === 0 ? K.slow : K.card;
          return (
            <g key={i} opacity={k}>
              <rect x={slotX(i)} y={RY} width={SW} height={SH} rx={8} fill="#0f1528" stroke={hot ? col : C.dim} strokeWidth={hot ? 3 : 2} />
              {hot && <rect x={slotX(i)} y={RY} width={SW} height={SH} rx={8} fill={col} opacity={kCores * (0.22 + 0.25 * hammer * flash)} />}
              <text x={scx(i)} y={RY + SH / 2 + 9} textAnchor="middle" fontSize={24} fontWeight={700} fill={hot ? C.text : C.muted}>
                64 B
              </text>
            </g>
          );
        })}
        <g opacity={prog(t, T[0] + 1.6, 0.4)} fill={C.muted}>
          <text x={1074} y={RY + SH / 2 + 12} textAnchor="middle" fontSize={40} fontWeight={800}>
            ⋯
          </text>
          <text x={slotX(8) + BLK / 2} y={605} textAnchor="middle" fontSize={24}>
            4 KB away
          </text>
        </g>
        <Brace x1={slotX(0) - 4} x2={slotX(1) + SW + 4} y={RY + SH + 12} label="Linux: coherency line = 128 B" size={24}
          opacity={win(t, T[0] + 2.5)} />

        {/* the two writers */}
        <Die x={C3X} y={305} w={150} h={80} label="core 3" color={K.card} fontSize={26} opacity={kCores} glow={hammer > 0.5} />
        <Die x={c4x} y={305} w={150} h={80} label="core 4" color={K.card} fontSize={26} opacity={kCores} glow={hammer > 0.5} />
        <Arrow x1={a3[0][0]} y1={a3[0][1]} x2={a3[1][0]} y2={a3[1][1]} color={K.card} w={4} k={kCores} head={14} />
        <Arrow x1={a4[0][0]} y1={a4[0][1]} x2={a4[1][0]} y2={a4[1][1]} color={K.card} w={4} k={kCores} head={14} />
        <PacketStream pts={a3} travel={trav} gap={30} size={11} color={fight ? K.slow : K.card} opacity={hammer} />
        <PacketStream pts={a4} travel={trav + 13} gap={30} size={11} color={fight ? K.slow : K.card} opacity={hammer} />
        {fight && (
          <g>
            {Array.from({ length: 10 }, (_, j) => {
              const a = Math.PI * (1.08 + (0.84 * j) / 9);
              const len = 16 + 30 * spark();
              const x = C3X + (spark() - 0.5) * 56;
              return (
                <line key={j} x1={x} y1={RY - 2} x2={x + Math.cos(a) * len} y2={RY - 2 + Math.sin(a) * len}
                  stroke={j % 2 ? K.slow : "#ffd0d0"} strokeWidth={3} strokeLinecap="round" />
              );
            })}
          </g>
        )}
        <text x={400} y={410} fontSize={30} fontWeight={800} fill={K.slow} opacity={win(t, T[0] + 7.4, T[1])}>
          fight?
        </text>
        <text x={400} y={410} fontSize={30} fontWeight={800} fill={K.fast} opacity={win(t, T[3] + 3.0, T[3] + 10.3)}>
          no fight
        </text>

        {/* the knob: distance d between the two slots */}
        <g opacity={win(t, T[1] + 5.5)}>
          <line x1={C3X} y1={640} x2={x4} y2={640} stroke={K.knob} strokeWidth={4} />
          <line x1={C3X} y1={626} x2={C3X} y2={654} stroke={K.knob} strokeWidth={4} />
          <line x1={x4} y1={626} x2={x4} y2={654} stroke={K.knob} strokeWidth={4} />
          <text x={(C3X + x4) / 2} y={686} textAnchor="middle" fontSize={28} fontWeight={800} fill={K.knob}
            opacity={clamp01(1 - 2.5 * Math.abs(d - di))}>
            d = {OPTS[di]}
          </text>
        </g>

        {/* meters: writes per core and coherence snoops */}
        <g opacity={win(t, T[1] + 2.0)}>
          <Meter x={MX} y={290} w={MW} h={30} value={wv} max={300} color={t < T[3] + 0.3 ? K.slow : K.fast}
            label="writes per core (mean)" size={24} />
          <Meter x={MX} y={450} w={MW} h={30} value={sv} max={20} color={t < T[3] + 1.0 ? K.slow : K.fast}
            label="coherence snoops" size={24} marks={[{ at: 0.52, color: C.muted }]} />
          <text x={MX + (MW * 0.52) / 20} y={512} textAnchor="middle" fontSize={24} fill={C.muted}>
            idle 0.52
          </text>
        </g>
        <g fontSize={28} fontWeight={800} textAnchor="end">
          <text x={MX + MW} y={280} fill={K.slow} opacity={win(t, T[2] + 1.1, T[3] + 0.3)}>
            72.3 M/s
          </text>
          <text x={MX + MW} y={280} fill={K.fast} opacity={win(t, T[3] + 0.9)}>
            259.8 M/s
          </text>
          <text x={MX + MW} y={440} fill={K.slow} opacity={win(t, T[2] + 5.0, T[3] + 1.0)}>
            17.8 M/s
          </text>
          <text x={MX + MW} y={440} fill={K.fast} opacity={win(t, T[3] + 1.5, T[3] + 10.3)}>
            0.52 M/s
          </text>
          <text x={MX + MW} y={440} fill={K.fast} opacity={win(t, T[3] + 10.7, T[4] + 0.3)}>
            idle level
          </text>
          <text x={MX + MW} y={440} fill={K.fast} opacity={win(t, T[4] + 0.8)}>
            0.52 M/s
          </text>
        </g>
        <text x={MX} y={366} fontSize={24} fill={C.muted} opacity={win(t, T[2] + 2.0, T[3])}>
          core 3 63.7 · core 4 80.8
        </text>
        <g opacity={win(t, T[3] + 0.4)}>
          <text x={MX} y={590} fontSize={24} fill={C.muted}>
            writes per core by d (M/s)
          </text>
          <BarChart x0={MX} x1={MX + MW} y0={810} y1={660} max={300} size={24} labelSize={24} barW={56}
            bars={OPTS.map((o, i) => ({ label: o, value: i === 0 ? 72.3 : 259.8, color: i === 0 ? K.slow : K.fast }))}
            k={(i) => prog(t, BAR_AT[i], 0.7)} />
        </g>
      </Stage>

      <div style={{ position: "absolute", left: 560, top: 204, display: "flex", alignItems: "center", gap: 14, opacity: win(t, T[0] + 1.0) }}>
        <Chip color={C.text} style={{ fontFamily: MONO }}>
          coherency_line_size = 128
        </Chip>
        <span style={{ fontSize: 24, color: C.muted }}>Linux · card L3</span>
      </div>
      <Takeaway x={160} y={700} w={1180} k={prog(t, T[4] + 0.6, 0.6)} size={34}>
        Card cores track coherence per 64 bytes.
        <div style={{ marginTop: 12, opacity: prog(t, T[4] + 5.6, 0.5) }}>
          <Chip color={C.muted}>untested: PCIe writes landing next to a polled slot</Chip>
        </div>
      </Takeaway>
      <div style={{ opacity: win(t, T[1] + 1.0) }}>
        <Note x={MX} y={862} w={470}>
          schematic · the 4 KB gap is not to scale
        </Note>
      </div>
      <KnobBar fixed={["2 card cores", "own 64 B slot each", "1 s × 5"]} knob="distance d" options={OPTS}
        active={t < D_KEYS[0][0] ? -1 : slide(t, D_KEYS.slice(1), 0)} k={prog(t, 0.1, 0.5)} />
    </>
  );
}

export const S09: SceneDef = {
  id: "S09",
  title: "Exp 6b · 64 B or 128 B?",
  stage: 4,
  source: "MRV-55 · logs/mrv55-2026-11-18 · 1 s × 5 per point",
  dur: DUR,
  cues,
  C: Body,
};
