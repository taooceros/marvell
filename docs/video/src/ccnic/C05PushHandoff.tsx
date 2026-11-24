import React from "react";
import { Cue, SceneDef, lin, prog, useT } from "../lib";
import { BigStat, Cell, Flow, HBar, HBars, HostNic, P, Stage, TAB, Tag, Text, alpha, cx, hnGeom, hnRoutes, win } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 11.02, "What is host–NIC traffic, really? **One side writes, the other reads**, then the buffer changes hands.", "Look at what host to nick traffic actually is. One side writes a message, the other side reads it, and then the buffer changes hands. Nothing is shared back and forth. It is a hand-off."], // 0
  [11.37, 19.41, "A hand-off doesn't need coherence. It needs each write to **land in the reader's cache**.", "A hand-off does not need a protocol that tracks every copy of every cache line. It only needs each write to land directly in the reader's cache."], // 1
  [19.76, 28.82, "PCIe already does this: a 64-byte host write lands in the **NIC's cache** — 99.94% of reads hit.", "And P C I E already does this. A sixty-four byte write from the host lands in the nick's own cache: ninety-nine point nine four percent of its reads hit there."], // 2
  [29.17, 40.81, "Replies go the same way: the NIC's write lands in the **host's cache** (Intel DDIO). Each side polls only its own cache.", "Replies travel the same way in reverse: what the nick writes lands in the host's last level cache, through Intel's D D I O. So each side only ever polls its own cache, and nothing is read across the link."], // 3
  [41.16, 51.72, "We call it **DirectPush**: one write per message, no doorbell, no read across the link. Round trip: **897 ns**.", "We call this DirectPush: one write per message in each direction, with no doorbell and no read across the link. A sixty-four byte round trip takes eight hundred ninety-seven nanoseconds."], // 4
  [52.07, 62.35, "Same card, same link, standard descriptor interface: **4.6 µs**. Changing only the interface: **5.2× faster**.", "On the very same card and link, the standard descriptor interface takes four point six microseconds. Changing only the interface makes the round trip five point two times faster."], // 5
  [62.70, 76.36, "For scale: real CXL coherent NIC **~1.3–2.1 µs**, no IOMMU. CC-NIC's 490 ns was **emulated** on two CPUs.", "For scale, the real C X L coherent nick measures about one point three to two point one microseconds, without I O M M U protection. C C nick's faster numbers were emulated with two C P U sockets, and we do not claim to beat them."], // 6
];
const DUR = 77.76; // <dur>
// </cues>

const T = cues.map((c) => c[0]);
const G = hnGeom(1);
const RT = hnRoutes(G);
const GREY = "#8f9ab3";

/** k of the i-th cell in a repeating stream along a route: period `per`, travel time `dur`. */
function streamK(t: number, start: number, per: number, dur: number, i: number) {
  const local = t - start - i * per;
  return local < 0 ? -1 : local / dur;
}

function Body() {
  const t = useT();
  // ---------------------------------------------------------------- phase A: the hand-off diagram (T0..T5)
  const diag = win(t, T[0] + 0.1, T[5] + 0.2, 0.8);
  const readerHl = prog(t, T[1] + 0.4, 0.8);
  const reqOn = t >= T[2] ? 1 : 0;
  const repOn = t >= T[3] ? 1 : 0;
  // one demo request in beat 0, a steady stream from beat 2, a full request→reply loop in beat 4
  const demoK = lin(t, T[0] + 2.2, 1.8);
  const firstReq = t < T[2] ? demoK : -1;
  const loop = t >= T[4];
  const per = loop ? 2.6 : 1.05;
  const reqCells = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13].map((i) => {
    const start = loop ? T[4] + 0.2 : T[2] + 0.3;
    const k = streamK(t, start, per, 1.25, i);
    return t < T[4] && t >= T[2] ? k : loop ? k : -1;
  });
  const repCells = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((i) => {
    const start = loop ? T[4] + 0.2 + 1.3 : T[3] + 0.3;
    return streamK(t, start, per, 1.25, i);
  });
  const nicPollPulse = reqOn ? 0.35 + 0.65 * Math.abs(Math.sin((t - T[2]) * 3.0)) : t > T[0] + 4.0 ? prog(t, T[0] + 4.0, 0.4) : 0;
  const hostPollPulse = repOn ? 0.35 + 0.65 * Math.abs(Math.sin((t - T[3]) * 3.0)) : 0;
  const loopGlow = prog(t, T[4] + 0.1, 0.8);

  // ---------------------------------------------------------------- phase B: same card, then for scale (T5..)
  const bars = win(t, T[5] + 0.3, 1e9, 0.7);
  const scale = prog(t, T[6] + 0.3, 0.7);
  const rows: HBar[] = [
    { label: "Standard descriptor interface", note: "same card, same link", value: 4.635, valueText: "4.6 µs", color: GREY, k: prog(t, T[5] + 0.6, 1.0) },
    { label: "DirectPush", note: "ours · plain PCIe Gen5 · IOMMU on", value: 0.897, valueText: "0.9 µs", color: P.push, k: prog(t, T[5] + 1.3, 0.8) },
    { label: "Real CXL coherent NIC", note: "CXL-NIC, MICRO '25 · no IOMMU", value: 2.1, lo: 1.3, valueText: "~1.3–2.1 µs", color: P.coh, k: prog(t, T[6] + 0.5, 0.9), opacity: scale },
    { label: "CC-NIC", note: "emulated with two Xeon sockets", value: 0.65, dashed: true, valueText: "0.49–0.65 µs", color: P.coh, k: prog(t, T[6] + 1.6, 0.9), opacity: prog(t, T[6] + 1.2, 0.7) },
  ];

  return (
    <>
      <Stage>
        <g opacity={diag}>
          <HostNic
            g={G}
            dim={{ hostMem: 0.75, nicMem: 0.75 }}
            hl={{ nicCache: Math.max(readerHl * (1 - 0.5 * prog(t, T[3], 0.6)), loopGlow * 0.6), hostCache: Math.max(prog(t, T[3] + 0.4, 0.8), loopGlow * 0.6), link: loopGlow }}
            hlColor={{ nicCache: P.push, hostCache: P.push }}
          />
          {/* the two push paths, drawn once they are introduced; glow when the round trip loops */}
          <Flow pts={RT.pushH2N} k={Math.max(prog(t, T[0] + 2.2, 1.8), 0)} color={alpha(P.push, 0.55 + 0.45 * loopGlow)} w={2.5} head={12} glow={loopGlow > 0} />
          <Flow pts={RT.pushN2H} k={prog(t, T[3] + 0.2, 1.4)} color={alpha(P.push, 0.55 + 0.45 * loopGlow)} w={2.5} head={12} glow={loopGlow > 0} />
          {/* local polls: each side reads only its own cache */}
          <Flow pts={RT.nicPoll} k={nicPollPulse > 0 ? 1 : 0} color={P.nic} w={3} head={10} opacity={nicPollPulse} />
          <Flow pts={RT.hostPoll} k={hostPollPulse > 0 ? 1 : 0} color={P.host} w={3} head={10} opacity={hostPollPulse} />
          {firstReq >= 0 && <Cell pts={RT.pushH2N} k={firstReq} color={P.push} />}
          {reqCells.map((k, i) => (
            <Cell key={`q${i}`} pts={RT.pushH2N} k={k} color={P.push} />
          ))}
          {repOn > 0 &&
            repCells.map((k, i) => <Cell key={`r${i}`} pts={RT.pushN2H} k={k} color={P.push} />)}
        </g>
        <g opacity={bars}>
          <HBars x={110} y={300} w={1700} rows={rows} max={5.0} labelW={560} barH={40} gap={74} size={27} />
        </g>
      </Stage>

      {/* ---- phase A overlays */}
      <Tag x={cx(G.host) - 52} y={186} color={P.host} opacity={diag * prog(t, T[0] + 1.0, 0.6)}>
        writer
      </Tag>
      <Tag x={cx(G.nic) - 52} y={186} color={P.nic} opacity={diag * prog(t, T[0] + 1.4, 0.6)}>
        reader
      </Tag>
      <Text x={1556} y={404} size={20} color={P.nic} opacity={diag * Math.min(1, nicPollPulse * 2)}>
        local poll
      </Text>
      <Text x={296} y={404} size={20} color={P.host} opacity={diag * Math.min(1, hostPollPulse * 2)}>
        local poll
      </Text>
      <Tag x={1290} y={572} color={P.push} opacity={diag * readerHl * (1 - prog(t, T[2], 0.5))}>
        lands in the reader's cache
      </Tag>
      <Text x={G.nic.x} y={806} w={G.nic.w} align="center" size={22} color={P.sub} opacity={diag * prog(t, T[2] + 1.0, 0.6)}>
        <span style={{ fontSize: 46, fontWeight: 800, color: P.gold, ...TAB }}>99.94%</span>
        <span style={{ marginLeft: 14 }}>of NIC reads hit its cache</span>
      </Text>
      <Text x={G.host.x} y={818} w={G.host.w} align="center" size={22} color={P.sub} opacity={diag * prog(t, T[3] + 1.2, 0.6)}>
        replies land in the host's cache via <span style={{ color: P.ink, fontWeight: 600 }}>Intel DDIO</span>
      </Text>
      <BigStat
        x={960}
        y={238}
        w={520}
        align="center"
        value="897"
        unit="ns"
        size={84}
        color={P.push}
        label="64-byte round trip · IOMMU on"
        opacity={diag * prog(t, T[4] + 4.2, 0.7)}
        dy={(1 - prog(t, T[4] + 4.2, 0.7)) * 14}
      />

      {/* ---- phase B overlays */}
      <Text x={110} y={196} size={17} color={P.sub} opacity={bars} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        64-BYTE ROUND TRIP · LOWER IS BETTER
      </Text>
      <BigStat
        x={1330}
        y={396}
        w={460}
        value="5.2×"
        size={76}
        color={P.push}
        label="same card, only the interface changed"
        opacity={bars * prog(t, T[5] + 2.6, 0.7) * (1 - 0.55 * scale)}
      />
      <Text x={110} y={838} w={1100} size={19} color={P.faint} opacity={scale}>
        Different machines and testbeds: the coherent-NIC numbers are from the papers, shown for scale only.
      </Text>
    </>
  );
}

export const C05: SceneDef = {
  id: "C05",
  title: "Push, don't share",
  stage: 1,
  source: "Measured: 64 B round trip · 1 host core + 1 NIC core · IOMMU on",
  dur: DUR,
  cues,
  C: Body,
};
