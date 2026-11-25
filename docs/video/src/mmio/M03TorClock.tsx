import React from "react";
import { Cue, SceneDef, along, clamp01, lin, prog, useT } from "../lib";
import { BigStat, Cell, HBars, P, Stage, Text, alpha, win } from "../ccnic/kit";
import { Badge, D, Dim, MmDefs, NodeId, ReadPath, T as Tx, TimeBar, TorTable, barLayout, plotGeom, rcx, rpGeom, tripRoute } from "./mk";

// <cues>
const cues: Cue[] = [
  [0.60, 9.32, "Inside the host chip, each uncached read is handled by\u00a0one **CHA** and tracked in its table of requests, the **TOR**.", "Inside the host chip, each uncached read is handled by one caching agent, the C H A, and tracked in its table of requests, the T O R."], // 0
  [9.67, 16.69, "No public event counts these small reads. An unnamed\u00a0encoding\u00a0does, and it matches every counter below it **1 : 1**.", "No public event counts small reads like these. An unnamed encoding does, and it matches every counter below it, one for one."], // 1
  [17.04, 25.12, "The TOR keeps the entry until the reply returns. Little's\u00a0law: occupancy ÷ reads = time spent below the CHA.", "That table keeps the entry until the reply returns. So Little's law turns two counters into a time: occupancy divided by reads."], // 2
  [25.47, 34.73, "Card register read: **516.4 ns**. Below the CHA: **495.6 ns**. Core and mesh outside it: only **21.8 ns**.", "Reading a card register takes five hundred sixteen nanoseconds. Four hundred ninety-six are below the C H A; the core and mesh take only twenty-two."], // 3
  [35.08, 43.30, "From all 32 cores, distance moves the total by **10 ns**. Where the TOR frees early, the time reappears outside it.", "From all thirty-two cores, distance moves the total by just ten nanoseconds. And where the table lets go early, the time reappears outside it."], // 4
  [43.65, 50.75, "Eight cores reading at once scale **8.0×**: nothing queues. And one core never overlaps two uncached reads.", "Eight cores reading at once go eight times faster, so nothing queues. And one core never overlaps two uncached reads."], // 5
];
const DUR = 52.15; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

const g = rpGeom(1, 0, 10);
const TRIP = tripRoute(g, "sdp");
const CHA_X = rcx(g.n.cha);
/** Counters that count each uncached read once (c1). */
const COUNTED: NodeId[] = ["cha", "m2p", "irp", "iio"];
const BADGE_Y = g.n.cha.y - 42;
const TOR_Y = g.n.cha.y + g.n.cha.h + 60;
// c3: one register read split at the CHA (means)
const BAR = { x: 440, y: 690, w: 1280, h: 48 };
const SPLIT = [
  { v: 21.8, color: D.core },
  { v: 495.6, color: P.faint, reveal: 0, inside: "below the CHA" },
];
const SPLIT_MAX = 21.8 + 495.6;
// c4: per-core medians (ns, multiplicity) — plotted, never printed
const CORES: [number, number][] = [
  [617.27, 5],
  [618.18, 3],
  [620.0, 2],
  [620.91, 2],
  [623.64, 1],
  [625.45, 4],
  [626.36, 6],
  [627.27, 9],
];
const PL = plotGeom(400, 600, 880, 200, [614, 631], [0, 10]);
// c5: K cores reading different lines
const KS = [
  { label: "1 core", v: 1.54, text: "1.54" },
  { label: "2 cores", v: 3.08, text: "3.08" },
  { label: "4 cores", v: 6.18, text: "6.18" },
  { label: "8 cores", v: 12.23, text: "12.23" },
];

function Body() {
  const t = useT();
  const out3 = 1 - prog(t, T[3], 0.5);
  // c0: path, CHA, TOR table
  const chaK = prog(t, T[0] + 0.8, 0.6);
  const torK = prog(t, T[0] + 1.6, 0.6);
  // c1: badges left → right, the unnamed encoding, 1 : 1
  const badgeK = COUNTED.map((_, i) => prog(t, T[1] + 0.4 + i * 0.35, 0.4) * out3);
  const encK = prog(t, T[1] + 2.2, 0.5) * out3;
  const ratioK = prog(t, T[1] + 3.6, 0.7) * out3;
  const hl: Partial<Record<NodeId, number>> = { cha: chaK };
  COUNTED.slice(1).forEach((id, i) => (hl[id] = 0.8 * prog(t, T[1] + 0.75 + i * 0.35, 0.4) * (1 - prog(t, T[2], 0.6))));
  // c2: one read makes the round trip; the TOR entry is busy while it is beyond the CHA
  const trip = lin(t, T[2] + 0.4, 4.4);
  let busy = 0;
  if (trip > 0 && trip < 1) busy = clamp01((along(TRIP, trip)[0] - CHA_X) / 40);
  const lawK = win(t, T[2] + 2.2, T[3]);
  // c3: the split bar
  const c3 = win(t, T[3] + 0.2, T[4]);
  const grow = prog(t, T[3] + 0.3, 1.1);
  const lay = barLayout(BAR.x, BAR.w, SPLIT_MAX, SPLIT);
  // c4: 32 dots
  const c4 = win(t, T[4] + 0.2, T[5]);
  // c5: scaling bars
  const c5 = prog(t, T[5] + 0.2, 0.5);
  let n = 0;
  return (
    <>
      <Stage>
        <MmDefs />
        <ReadPath
          g={g}
          opacity={prog(t, T[0], 0.6)}
          hl={hl}
          heads={0}
          hide={{ rp: 1, pem: 1, sdp: 1, iobn: 1, tad: 1, ddr: 1 }}
          boxes={[{ from: "rp", to: "ddr", tall: true, label: "?" }]}
          under={trip > 0 && trip < 1 ? <Cell pts={TRIP} k={trip} color={P.gold} w={30} h={20} /> : null}
        />
        {/* HOST heading drawn here: ReadPath's CARD heading would ghost through the tall box */}
        <Tx x={g.n.core.x} y={g.at(0, 300)[1]} size={Math.max(11, 16 * g.s)} color={alpha(D.core, 0.9)} weight={600} spacing={2.4} opacity={prog(t, T[0], 0.6)}>
          HOST
        </Tx>
        <TorTable g={g} k={torK} busy={busy} />
        <Tx x={g.n.cha.x - 18} y={TOR_Y - 20} anchor="end" size={26} weight={700} color={D.mesh} opacity={prog(t, T[0] + 2.1, 0.5)}>
          TOR
        </Tx>
        {/* c1 */}
        {COUNTED.map((id, i) => (
          <Badge key={id} x={rcx(g.n[id])} y={BADGE_Y} text="1.0000" k={badgeK[i]} />
        ))}
        <Dim x0={rcx(g.n.cha) - 46} x1={rcx(g.n.iio) + 46} y={BADGE_Y - 38} label="1 : 1" color={P.push} k={ratioK} size={24} />
        <Tx x={g.n.cha.x + g.n.cha.w + 22} y={TOR_Y - 20} size={24} mono color={P.ink} opacity={encK}>
          0xc83fde01
        </Tx>
        <Tx x={g.n.cha.x + g.n.cha.w + 22} y={TOR_Y + 12} size={18} color={P.sub} opacity={encK}>
          unnamed CHA event
        </Tx>
        {/* c2: Little's law */}
        <g opacity={lawK}>
          <Tx x={g.n.cha.x} y={640} size={24} color={P.sub} weight={600} spacing={1}>
            Little&apos;s law
          </Tx>
          <Tx x={g.n.cha.x} y={700} size={44} mono color={P.ink} weight={600}>
            T_TOR = occupancy ÷ inserts
          </Tx>
        </g>
        {/* c3: 516.4 = core + mesh | below the CHA (split from means) */}
        <g opacity={c3}>
          <TimeBar x={BAR.x} y={BAR.y} w={BAR.w} h={BAR.h} max={SPLIT_MAX} segs={SPLIT} grow={grow} size={24} />
          <Dim x0={BAR.x} x1={BAR.x + BAR.w} y={BAR.y - 22} label="516.4 ns" color={P.gold} k={prog(t, T[3] + 0.9, 0.6)} size={30} weight={800} />
          <Tx x={BAR.x + BAR.w} y={BAR.y - 48} anchor="end" size={20} color={P.sub} opacity={prog(t, T[3] + 1.3, 0.5)}>
            median read
          </Tx>
          <Dim x0={lay[1].x0 + 2} x1={lay[1].x1} y={BAR.y + BAR.h + 22} label="495.6 ns" color={P.ink} dir={1} k={prog(t, T[3] + 3.4, 0.6)} size={28} weight={800} />
          <g opacity={prog(t, T[3] + 5.6, 0.5)}>
            <Tx x={BAR.x - 20} y={BAR.y + 12} anchor="end" size={28} weight={800} color={D.core}>
              21.8 ns
            </Tx>
            <Tx x={BAR.x - 20} y={BAR.y + 42} anchor="end" size={18} color={P.sub}>
              core + mesh
            </Tx>
          </g>
          <Tx x={BAR.x + BAR.w} y={BAR.y + BAR.h + 82} anchor="end" size={18} color={P.faint} opacity={prog(t, T[3] + 6.4, 0.5)}>
            TOR split: means
          </Tx>
        </g>
        {/* c4: per-core medians */}
        <g opacity={c4}>
          <line x1={PL.x - 10} y1={PL.y + PL.h} x2={PL.x + PL.w} y2={PL.y + PL.h} stroke={P.hair2} strokeWidth={1.5} />
          {[615, 620, 625, 630].map((v) => (
            <g key={v}>
              <line x1={PL.sx(v)} y1={PL.y + PL.h} x2={PL.sx(v)} y2={PL.y + PL.h + 8} stroke={P.hair2} strokeWidth={1.5} />
              <Tx x={PL.sx(v)} y={PL.y + PL.h + 26} anchor="middle" size={18} color={P.sub}>
                {v}
              </Tx>
            </g>
          ))}
          <Tx x={PL.x + PL.w / 2} y={PL.y + PL.h + 58} anchor="middle" size={20} color={P.sub}>
            median read per core, ns
          </Tx>
          <Tx x={PL.x - 24} y={PL.y + PL.h - 60} anchor="end" size={24} color={P.ink} weight={600}>
            32 cores
          </Tx>
          <Tx x={PL.x - 24} y={PL.y + PL.h - 30} anchor="end" size={18} color={P.sub}>
            one dot each
          </Tx>
          {CORES.map(([v, m]) =>
            Array.from({ length: m }).map((_, j) => {
              const k = prog(t, T[4] + 0.5 + n++ * 0.045, 0.35);
              return <circle key={`${v}_${j}`} cx={PL.sx(v)} cy={PL.y + PL.h - 14 - j * 20 - (1 - k) * 40} r={8.5} fill={D.core} opacity={0.9 * k} />;
            }),
          )}
          <Dim x0={PL.sx(617.27)} x1={PL.sx(627.27)} y={PL.y + PL.h - 14 - 9 * 20 - 6} label="10 ns" color={P.gold} k={prog(t, T[4] + 2.6, 0.6)} size={30} weight={800} />
          <g opacity={prog(t, T[4] + 4.6, 0.6)}>
            <Tx x={1380} y={660} size={22} color={P.ink} weight={600}>
              6 cores free the TOR early:
            </Tx>
            <Tx x={1380} y={696} size={20} color={P.sub}>
              ΔTOR −9 ns, outside +9 ns,
            </Tx>
            <Tx x={1380} y={726} size={20} color={P.sub}>
              total unchanged
            </Tx>
          </g>
        </g>
        {/* c5: K cores on different lines */}
        <g opacity={c5}>
          <Tx x={180} y={590} size={22} color={P.sub}>
            K cores reading different lines · Mops/s
          </Tx>
          <HBars
            x={180}
            y={624}
            w={1160}
            labelW={200}
            barH={34}
            gap={26}
            max={12.23}
            rows={KS.map((r, i) => ({
              label: r.label,
              value: r.v,
              valueText: r.text,
              color: i === 3 ? P.gold : alpha(D.core, 1),
              k: prog(t, T[5] + 0.5 + i * 0.45, 0.8),
            }))}
          />
        </g>
      </Stage>
      <BigStat x={1400} y={600} value="8.0×" size={120} color={P.gold} w={440} label="8 cores vs 1" opacity={prog(t, T[5] + 2.6, 0.6)} />
      <Text x={1400} y={800} w={480} size={22} color={P.sub} opacity={prog(t, T[5] + 4.4, 0.6)}>
        1 core, 8 reads in a row: 621.7 ns per read
      </Text>
    </>
  );
}

export const M03: SceneDef = {
  id: "M03",
  title: "A stopwatch inside the CPU",
  stage: 1,
  source: "249 runs \u00b7 host CHA, M2PCIe, IRP, IIO counters, unmultiplexed",
  dur: DUR,
  cues,
  C: Body,
};
