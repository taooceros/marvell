import React from "react";
import { Cue, SceneDef, lerp, prog, useT } from "../lib";
import { BigStat, CellAt, Flow, Glass, HBar, HBars, HostNic, P, Stage, Tag, Text, alpha, hnGeom, win } from "./kit";
import { Slot, TagC, Tokens, landOp, phase, stream } from "./_CcSceneCost";

// <cues>
const cues: Cue[] = [
  [0.60, 8.18, "Emulating a NIC with a second CPU hides one thing: a real NIC sits behind **I/O bridges**, and bridges can jam.", "Emulating a nick with a second C P U hides one thing. A real nick sits behind I O bridges, and bridges have failure modes of their own."], // 0
  [8.53, 22.17, "With many messages in flight while the NIC polls hard, its inbound bridge drops into **single file**: ~13 M/s.", "When many messages are in flight while the card's cores poll very hard, the card's inbound bridge drops into single file, letting through one write about every seventy-six nanoseconds. Throughput collapses to about thirteen million messages per second."], // 1
  [22.52, 32.76, "Counters showed the caches nearly empty: the jam sat **inside the bridge**, set off by polling empty slots.", "Hardware counters showed the queues on both sides were nearly empty. The jam sat inside the bridge itself, set off when card cores kept polling slot after empty slot while writes streamed in."], // 2
  [33.11, 48.39, "Not a link limit: push alone hits 175 M/s. It is a **congestion collapse**, and tails explode: p99 **44.5 µs**.", "That is not the link's limit; push alone reaches one hundred seventy-five million. It is a congestion collapse. Requests pile up behind the bridge, and at twenty million requests per second, the ninety-ninth percentile latency explodes to forty-four point five microseconds."], // 3
  [48.74, 56.98, "The fix is classic: **credits**. The host caps how many messages may be in flight, like a TCP window.", "The fix is a classic one: credits. The host simply caps how many messages may be in flight at once, much like a T C P window."], // 4
  [57.33, 67.11, "Same 20 M/s load, now with credits: p99 falls from **44.5 µs to 7.0 µs**, and the host-side queue drains.", "Under the same twenty million per second load, with credits the tail falls from forty-four point five to seven microseconds, and the queue that had built up inside the host drains away."], // 5
  [67.46, 77.40, "Honest limit: host credits alone can't hold p99 under **2 µs** at 20 M/s. That needs the NIC to back off as well.", "One honest limit: host credits alone cannot hold the tail under two microseconds at twenty million per second. That needs the nick to back off on demand as well, which is our next step."], // 6
];
const DUR = 78.80; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
/** Request lanes over the link (default-layout y), where writes leave the host and land in the NIC cache. */
const LANES = [472, 500, 528];
const X_HOST = 648;
const X_NIC = 1336;
const TRIP = (X_NIC - X_HOST) / 380;
/** The NIC's inbound I/O bridge straddles the NIC's left wall where the link enters. */
const BR = { x: 1098, y: 446, w: 174, h: 108 };
const JAW_X = 1262;
const FRONT = 1234; // first queued write, inside the bridge
const PITCH = 40;
const QMAX = 17; // the backlog reaches back into the host cache
const SERV = 0.9; // single file: one write leaves the bridge per SERV (schematic pace)
const DRAIN = 3.0;
const NSLOT = [1617, 1645, 1673, 1701];
const HSLOT = [236, 264, 292, 320];
const TOKX = Array.from({ length: 8 }, (_, i) => 712 + 20 * i);

const tj = T[1] + 3.0; // the bridge drops into single file
const tc = tj + SERV * Math.ceil((T[4] + 0.9 - tj) / SERV); // credits: the backlog drains (on a service boundary)

/** Backlog (in writes) inside and behind the bridge: fills the bridge, spills back into the host, drains with credits. */
function backlog(t: number): number {
  if (t < tj) return 0;
  if (t < T[3] + 0.5) return Math.min(4, (t - tj) * 0.5);
  if (t < tc) return Math.min(QMAX, 4 + (t - T[3] - 0.5) * 1.6);
  return Math.max(0, QMAX * (1 - (t - tc) / DRAIN));
}

function Body() {
  const t = useT();
  // b5: the diagram shrinks and lifts to make room for the result bars
  const m = prog(t, T[5] + 0.1, 1.2);
  const g = hnGeom(lerp(1, 0.85, m), 0, lerp(0, -78, m));
  const S = g.s;
  const diag = prog(t, T[0] + 0.1, 0.8);

  // ---------------------------------------------------------------- the bridge and its backlog
  const close = prog(t, tj, 0.8) * (1 - prog(t, tc, 0.8));
  const open = lerp(100, 24, close);
  const pile = win(t, T[2] + 0.3, T[3] + 0.6, 0.6);
  const n = backlog(t);
  const queue: { x: number; op: number }[] = [];
  if (t >= tj && t < tc) {
    // single file: each SERV one write passes the jaws, the rest step up one slot, the newest joins the tail
    const ph = phase(t, tj, SERV);
    const s = prog(ph, 0, 0.4);
    const cnt = Math.ceil(n);
    for (let j = 0; j < cnt; j++) {
      const tailOp = j === cnt - 1 ? Math.min(1, (n - j) * 3) : 1;
      if (j === 0) queue.push({ x: lerp(FRONT, X_NIC, s), op: tailOp * (1 - prog(ph, 0.3, 0.3)) });
      else queue.push({ x: FRONT - (j - s) * PITCH, op: tailOp });
    }
  } else if (t >= tc) {
    // credits: the backlog slides through the bridge and drains
    const d = ((t - tc) / DRAIN) * QMAX;
    for (let j = 0; j < QMAX; j++) {
      const x = FRONT - (j - d) * PITCH;
      if (x < X_NIC) queue.push({ x, op: x <= FRONT ? 1 : 1 - (x - FRONT) / (X_NIC - FRONT) });
    }
  }
  const tailX = queue.length ? Math.min(...queue.map((q) => q.x)) : FRONT;

  // ---------------------------------------------------------------- writes in flight on three lanes
  const flights: { x: number; y: number; op: number; key: string }[] = [];
  LANES.forEach((y, L) => {
    const free = [
      ...stream(t, T[0] + 1.6 + L * 0.4, T[1] + 0.3, 1.2, TRIP),
      ...stream(t, T[1] + 0.3 + L * 0.2, tj, 0.6, TRIP).map((e) => ({ ...e, i: e.i + 1000 })),
      ...stream(t, tc + DRAIN - 0.4 + L * 0.5, 1e9, 1.5, TRIP).map((e) => ({ ...e, i: e.i + 2000 })),
    ];
    free.forEach(({ i, k }) => flights.push({ x: lerp(X_HOST, X_NIC, k), y, op: landOp(k, 0.08), key: `f${L}-${i}` }));
    // while jammed: writes run up to the tail and merge into single file; none leave a backed-up host
    if (tailX > X_HOST + 60) {
      stream(t, tj + L * 0.2, tc, 0.6, TRIP).forEach(({ i, k }) => {
        const x = lerp(X_HOST, X_NIC, k);
        if (x >= tailX - 26) return;
        const merge = prog(x, tailX - 160, 110);
        flights.push({ x, y: lerp(y, 500, merge), op: 1, key: `j${L}-${i}` });
      });
    }
  });

  // ---------------------------------------------------------------- polling, credits
  const pollOn = prog(t, T[1] + 1.0, 0.6);
  const pollRate = t < T[4] ? 3.4 : 1.0;
  const credOn = prog(t, T[4] + 0.5, 0.6);
  const tokens = TOKX.map((x, i) => {
    const held = t >= tc && (t < tc + DRAIN || (Math.floor((t - tc) / 0.5) + i) % 8 < 5);
    const [X, Y] = g.at(x, 426);
    return { x: X, y: Y, fill: held ? P.push : null, ring: alpha(P.push, 0.8) };
  });

  // ---------------------------------------------------------------- b5 bars
  const rows: HBar[] = [
    { label: "no credits", value: 44.5, valueText: "44.5 µs", color: P.bad, k: prog(t, T[5] + 1.4, 1.0) },
    { label: "credits", value: 7.0, valueText: "7.0 µs", color: P.push, k: prog(t, T[5] + 4.4, 0.8) },
  ];

  const [bx, by] = g.at(BR.x, BR.y);
  const [gx0, gy0] = g.at(700, 452);
  const [boX0, boY] = g.at(1096, 548);
  const [boX1] = g.at(712, 548);

  return (
    <>
      <Stage>
        <g opacity={diag}>
          <HostNic
            g={g}
            dim={{ hostMem: 0.75, nicMem: 0.75 }}
            hl={{
              nicCore: pollOn * (t < T[4] ? 0.55 + 0.45 * Math.abs(Math.sin(t * 6)) : 0.3),
              hostCache: 0.35 * pile,
              nicCache: 0.35 * pile,
            }}
          />
          {/* ring slots: the NIC polls its slots hard; both sides stay nearly empty */}
          {NSLOT.map((x, i) => {
            const [X, Y] = g.at(x, 500);
            return <Slot key={`n${i}`} x={X} y={Y} s={22 * S} color={P.nic} opacity={pollOn * (t < T[4] ? 1 : 0.5)} />;
          })}
          {HSLOT.map((x, i) => {
            const [X, Y] = g.at(x, 500);
            return <Slot key={`h${i}`} x={X} y={Y} s={22 * S} color={P.host} fill={i === 0 ? 0.5 : 0} opacity={prog(t, T[2] + 0.4, 0.6) * (t < T[4] ? 1 : 0.5)} />;
          })}
          {NSLOT.map((x, i) => {
            const f = 0.5 + 0.5 * Math.sin(t * pollRate * 2 * Math.PI + i * 1.7);
            return (
              <Flow
                key={`p${i}`}
                pts={[g.at(x, 400), g.at(x, 484)]}
                k={pollOn > 0 ? 1 : 0}
                color={P.nic}
                w={2.5 * S}
                head={9 * S}
                opacity={pollOn * (t < T[4] ? 0.15 + 0.85 * f * f : 0.35)}
              />
            );
          })}
          {/* the inbound bridge (opaque, covering the NIC wall), its jam glow and its jaws */}
          <g opacity={prog(t, T[0] + 0.8, 0.7)}>
            <rect x={bx - 10} y={by - 10} width={(BR.w + 20) * S} height={(BR.h + 20) * S} rx={26 * S} fill={alpha(P.bad, 0.22 * close + 0.25 * pile)} filter="url(#ccSoft)" />
            <rect x={bx} y={by} width={BR.w * S} height={BR.h * S} rx={18 * S} fill={P.bg0} />
            <rect
              x={bx}
              y={by}
              width={BR.w * S}
              height={BR.h * S}
              rx={18 * S}
              fill={alpha(P.nic, 0.08 + 0.06 * pile)}
              stroke={P.nic}
              strokeWidth={2 + pile}
            />
            {[
              [BR.y + 4, 500 - open / 2],
              [500 + open / 2, BR.y + BR.h - 4],
            ].map(([y0, y1], i) => {
              const [jx, jy] = g.at(JAW_X - 4, y0);
              return <rect key={`jaw${i}`} x={jx} y={jy} width={8 * S} height={Math.max(3, (y1 - y0) * S)} rx={3 * S} fill={P.nic} />;
            })}
          </g>
          {/* credits: a gate and its tokens on the host side */}
          <g opacity={credOn}>
            <rect x={gx0} y={gy0} width={8 * S} height={96 * S} rx={4 * S} fill={alpha(P.push, 0.25)} stroke={P.push} strokeWidth={2} />
            <Tokens tokens={tokens} r={7 * S} />
          </g>
          {/* writes in flight, then the backlog */}
          {flights.map((f) => {
            const [X, Y] = g.at(f.x, f.y);
            return <CellAt key={f.key} x={X} y={Y} w={34 * S} h={22 * S} color={P.push} opacity={f.op} />;
          })}
          {queue.map((q, j) => {
            const [X, Y] = g.at(q.x, 500);
            return <CellAt key={`q${j}`} x={X} y={Y} w={34 * S} h={22 * S} color={P.push} opacity={q.op} />;
          })}
          {/* b6: next step, the NIC signals back-off itself */}
          <Flow pts={[[boX0, boY], [boX1, boY]]} k={prog(t, T[6] + 1.6, 1.4)} color={P.nic} w={3} head={12} dash="9 8" opacity={0.9} />
        </g>

        {/* ---- b3: not a link limit */}
        <g opacity={win(t, T[3] + 0.4, T[3] + 8.6, 0.6)}>
          <rect x={730} y={232} width={330 * prog(t, T[3] + 0.6, 0.9)} height={26} rx={13} fill={P.push} opacity={0.92} />
          <rect x={730} y={322} width={((330 * 13) / 175) * prog(t, T[3] + 1.8, 0.6)} height={26} rx={13} fill={P.bad} opacity={0.92} />
        </g>

        {/* ---- b5: the tail at 20 M/s */}
        <g opacity={prog(t, T[5] + 0.9, 0.7)}>
          <HBars x={110} y={732} w={1150} rows={rows} max={44.5} labelW={250} barH={34} gap={26} size={26} />
        </g>
      </Stage>

      {/* ---- overlays (diagram) */}
      <TagC x={1500} y={186} color={P.nic} opacity={diag * win(t, T[0] + 1.6, T[1] + 1.0, 0.6)}>
        real NIC ≠ second CPU
      </TagC>
      <Text x={1000} y={406} w={220} align="right" size={22} weight={600} color={P.nic} opacity={win(t, T[0] + 1.2, T[5], 0.6)}>
        inbound bridge
      </Text>
      <TagC x={1160} y={566} color={P.bad} opacity={pile}>
        pile-up
      </TagC>
      <TagC x={420} y={572} color={P.host} opacity={win(t, T[2] + 1.0, T[3] + 0.3, 0.5)}>
        nearly empty
      </TagC>
      <TagC x={1500} y={572} color={P.nic} opacity={win(t, T[2] + 1.0, T[3] + 0.3, 0.5)}>
        nearly empty
      </TagC>
      <BigStat x={960} y={206} w={520} align="center" value="~13" unit="M/s" size={84} color={P.bad} opacity={win(t, T[1] + 10.4, T[2] + 0.2, 0.6)} />
      <Text x={700} y={318} w={520} align="center" size={24} color={P.ink} opacity={win(t, T[1] + 7.4, T[2] + 0.2, 0.6)}>
        one write every ~76 ns
      </Text>

      {/* b3: push alone vs collapse, then the tail */}
      <Text x={730} y={196} size={22} color={P.sub} opacity={win(t, T[3] + 0.4, T[3] + 8.6, 0.6)}>
        push alone
      </Text>
      <Text x={1076} y={226} size={30} weight={700} color={P.push} opacity={win(t, T[3] + 1.2, T[3] + 8.6, 0.5)} style={{ fontVariantNumeric: "tabular-nums" }}>
        175 M/s
      </Text>
      <Text x={730} y={286} size={22} color={P.sub} opacity={win(t, T[3] + 1.4, T[3] + 8.6, 0.6)}>
        collapse
      </Text>
      <Text x={774} y={316} size={30} weight={700} color={P.bad} opacity={win(t, T[3] + 2.2, T[3] + 8.6, 0.5)} style={{ fontVariantNumeric: "tabular-nums" }}>
        ~13 M/s
      </Text>
      <BigStat
        x={960}
        y={598}
        w={520}
        align="center"
        value="44.5"
        unit="µs"
        size={80}
        color={P.bad}
        label="p99 at 20 M/s"
        opacity={win(t, T[3] + 9.4, T[5] + 0.2, 0.7) * (1 - 0.5 * prog(t, T[4] + 0.4, 0.6))}
        dy={(1 - prog(t, T[3] + 9.4, 0.7)) * 14}
      />

      {/* b4: credits */}
      <Text x={706} y={366} size={24} color={P.ink} opacity={win(t, T[4] + 0.6, T[5] + 0.1, 0.6)}>
        <span style={{ color: P.push, fontWeight: 600 }}>credits:</span> at most 48 in flight
      </Text>

      {/* b5–b6: result and honest limit */}
      <Text x={110} y={690} size={17} color={P.sub} opacity={prog(t, T[5] + 0.9, 0.7)} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        P99 LATENCY AT 20 M/s · LOWER IS BETTER
      </Text>
      <Glass x={1290} y={692} w={520} h={150} opacity={prog(t, T[6] + 0.6, 0.8)}>
        <Tag x={28} y={22} color={P.nic}>
          next step
        </Tag>
        <div style={{ marginTop: 44, fontSize: 24, lineHeight: 1.35, color: P.ink }}>
          under <b style={{ color: P.gold }}>2 µs</b> at 20 M/s needs the NIC to back off on demand too
        </div>
      </Glass>
      <Tag x={96} y={160} opacity={diag}>
        schematic
      </Tag>
    </>
  );
}

export const C07: SceneDef = {
  id: "C07",
  title: "Real hardware pushes back",
  stage: 3,
  source: "Measured: 64 B messages \u00b7 open and closed loop \u00b7 IOMMU on",
  dur: DUR,
  cues,
  C: Body,
};
