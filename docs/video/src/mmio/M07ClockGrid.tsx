import React from "react";
import { C, Cue, SceneDef, Stage, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 12.24, "Trap one: the IIO clock first read **0.96–0.99 GHz**, lower in short runs than in long ones. Real clocks don't do that.", "Trap one. The clock shared by I R P and I I O first read zero point nine six to zero point nine nine gigahertz, lower in short runs than in long ones. Real clocks don't do that."], // 0
  [12.59, 20.83, "After each run, the harness read 65 other counters first: **260–500 µs** with no reads in flight. The idle clock gated down.", "The culprit was our harness: after each run it read sixty-five other counters first, with no reads in flight, and the idle clock gated down."], // 1
  [21.18, 28.08, "Count cycles inside the timed loop only, and the clock is flat: **1.00 GHz** at every endpoint and every sweep point.", "Count only the cycles inside the timed loop, and the clock is flat: one point zero zero gigahertz, everywhere."], // 2
  [28.43, 36.93, "Trap two: the 0.91 ns grid. A grid-snapped median floor, taken from averaged reads, tilted the core fit by **~0.9 ns**.", "Trap two is the grid. We subtracted a grid-snapped median from averaged reads, and across core clocks that tilted the fit by almost a nanosecond."], // 3
  [37.28, 45.92, "Subtract a mean from a mean, and the two sweeps agree even better: worst mismatch **1.16 → 0.73 ns**.", "Subtract a mean from a mean instead, and the worst mismatch between the sweeps drops from one point one six to zero point seven three nanoseconds."], // 4
];
const DUR = 47.32; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  void t;
  void T;
  return <Stage>{null}</Stage>;
}

export const M07: SceneDef = {
  id: "M07",
  title: "Traps: the clock and the grid",
  stage: 3,
  source: "IO clock from in-loop cycles \u00f7 wall time \u00b7 floor_mean basis",
  dur: DUR,
  cues,
  C: Body,
};
