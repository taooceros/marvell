import React from "react";
import { C, Cue, SceneDef, Stage, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 8.74, "Second trick: change only where the read lands. Same host path, three endpoints on the card; subtract, and the shared part cancels.", "Second trick: change only where the read lands. Same host path, three endpoints on the card. Subtract, and the shared part cancels."], // 0
  [9.09, 18.81, "A card register: **516.4 ns**. Card memory in its 48 MiB L3: **621.8 ns**. Flushed to DDR5: **715.5 ns**.", "A card register: five hundred sixteen nanoseconds. Card memory in its L three: six hundred twenty-two. Flushed to DDR five: seven hundred sixteen."], // 1
  [19.16, 26.16, "So the BAR4 window, IOBN bridge and L3 add **+103.1–105.5 ns**; an L3 miss to DDR5 adds **+92.7–101.6 ns**.", "So reaching the L three adds about a hundred and four nanoseconds, and an L three miss adds ninety-three to a hundred and two."], // 2
  [26.51, 33.85, "The card's counters agree: **1.00** L3 miss and **2.01** DDR5 bursts per read: one 128-byte line, two 64-byte bursts.", "The card's counters agree: one L three miss per read, refilled by a pair of sixty-four byte DDR five bursts."], // 3
  [34.20, 42.62, "Causality check: at 1 GHz the card's miss would last **161.6 ns**, more than the host's whole **+101.6 ns**. So it runs at 2 GHz.", "A causality check pins the card's clock. At one gigahertz the miss alone would take longer than the host's whole delay. So it must run at two gigahertz."], // 4
  [42.97, 50.47, "Four pieces now. But the biggest, **~494 ns** from the host's CHA to the card, is still dark.", "Four pieces. But the biggest, about four hundred ninety-four nanoseconds from the host's C H A to the card, is still dark."], // 5
];
const DUR = 51.87; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  void t;
  void T;
  return <Stage>{null}</Stage>;
}

export const M04: SceneDef = {
  id: "M04",
  title: "Rulers at the far end",
  stage: 1,
  source: "Same host path, three card endpoints \u00b7 card TAD and DDR counters",
  dur: DUR,
  cues,
  C: Body,
};
