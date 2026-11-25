import React from "react";
import { C, Cue, SceneDef, Stage, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 5.32, "Now the whole read, stage by stage: eight bytes from the card's L3.", "Now the whole read, stage by stage: eight bytes from the card's L three."], // 0
  [5.67, 15.17, "Host chip, core to root port: **~205 ns**. More than half is the 1 GHz IRP₁ + IIO₁ and the root port: **125.2 ns**.", "Inside the host chip, core to root port: about two hundred five nanoseconds, and more than half of it is the one gigahertz I R P, I I O and root port."], // 1
  [15.52, 24.92, "PCIe layers, link, PEM0 and the register path: **291–312 ns**, the biggest piece, depending on whether the IIO is awake.", "Then the P C I E layers, the link, P E M zero and the register path: two hundred ninety-one to three hundred twelve nanoseconds, the biggest piece."], // 2
  [25.27, 32.77, "Inside the card: **+103–105 ns** through the BAR4 window to L3, and **+93–102 ns** more on a miss to DDR5.", "Inside the card: about a hundred and four more to reach the L three, and ninety-three to a hundred and two more for DDR five."], // 3
  [33.12, 41.38, "Six stages, host core to card memory, each measured two independent ways, with nothing but software and the chips' own counters.", "Six stages, from the host core to the card's memory, each measured two independent ways, with nothing but software and the chips' own counters."], // 4
];
const DUR = 42.78; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  void t;
  void T;
  return <Stage>{null}</Stage>;
}

export const M11: SceneDef = {
  id: "M11",
  title: "Six stages, two proofs each",
  stage: 4,
  source: "8-byte read into card L3 \u00b7 means unless marked",
  dur: DUR,
  cues,
  C: Body,
};
