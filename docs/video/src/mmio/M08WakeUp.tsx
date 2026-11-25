import React from "react";
import { C, Cue, SceneDef, Stage, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 6.94, "Trap three came from an interference test: one core keeps reading the root port, while another times the card register.", "Trap three came from an interference test: one core keeps reading the root port, while another times the card register."], // 0
  [7.29, 15.49, "Root-port reads never leave the host chip, yet the card read got **22.7 ns faster**. Someone else's traffic made us faster?", "Root-port reads never leave the host chip, yet the card read got twenty-two point seven nanoseconds faster. Someone else's traffic made us faster?"], // 1
  [15.84, 24.90, "Alone, a card read starts only every ~541 ns, and the IIO idles long enough to doze. Root-port reads every ~233 ns keep it awake.", "Alone, a card read starts only every five hundred forty nanoseconds, and the I I O sits idle long enough to doze. Root-port reads keep it awake."], // 2
  [25.25, 32.35, "So the isolated difference of +312.1 ns includes **18–23 ns** of wake-up that only the card path pays.", "So the isolated three hundred twelve nanoseconds include eighteen to twenty-three of wake-up that only the card path pays."], // 3
  [32.70, 39.96, "Keep the IIO awake for both: card register minus root port is **+290.9 ns** median, **+295.0 ns** mean.", "Keep it awake for both, and the difference is two hundred ninety-one nanoseconds at the median, two hundred ninety-five on average."], // 4
];
const DUR = 41.36; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  void t;
  void T;
  return <Stage>{null}</Stage>;
}

export const M08: SceneDef = {
  id: "M08",
  title: "Trap: the IO stack dozes",
  stage: 3,
  source: "Interference runs: one core reads the root port, cpu40 times",
  dur: DUR,
  cues,
  C: Body,
};
