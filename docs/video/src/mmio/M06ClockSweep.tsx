import React from "react";
import { C, Cue, SceneDef, Stage, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 8.52, "Third trick: change one clock and watch what stretches. A stage on that clock takes **cycles ÷ frequency**.", "Third trick: change one clock and watch what stretches. A stage on that clock takes a fixed number of cycles, divided by the frequency."], // 0
  [8.87, 17.99, "Sweep the mesh, 0.8 to 2.2 GHz. The CHA and M2PCIe₁ follow. IRP₁ and IIO₁ don't: they run on their own **1 GHz** clock.", "Sweep the mesh, and the C H A and M two P C I E follow it. I R P and I I O do not: they run on their own one gigahertz clock."], // 1
  [18.34, 28.72, "TOR time is a straight line in 1/f. Slope: **56.2–58.6 ns** of mesh. Intercept: **125.2 ns** for IRP₁, IIO₁ and the root port.", "Plot it against one over f, and it is a straight line: fifty-six to fifty-nine nanoseconds of mesh, plus a fixed hundred twenty-five for I R P, I I O and the root port."], // 2
  [29.07, 38.59, "Then sweep the core, 1.5 to 3.9 GHz. Its own share outside the TOR: **41–43 cycles**, or **10.5–10.9 ns**.", "Then sweep the core, from one point five to three point nine gigahertz. Its own share is forty-one to forty-three cycles: ten and a half nanoseconds."], // 3
  [38.94, 49.54, "Cross-check: what one sweep calls fixed, the other sees as slope. Core-to-CHA mesh: **11.2–12.8 ns**. Mismatch: **≤ 0.73 ns**.", "Cross-check: what one sweep calls fixed, the other must see as slope. The core to C H A mesh is eleven to thirteen nanoseconds, and they agree within zero point seven three."], // 4
  [49.89, 58.13, "Two clocks, two sweeps, each stage measured twice. The host side is now four clean pieces.", "Two clocks, two sweeps, every stage measured twice: the host side is four clean pieces. But getting here meant dodging five traps."], // 5
];
const DUR = 59.53; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  void t;
  void T;
  return <Stage>{null}</Stage>;
}

export const M06: SceneDef = {
  id: "M06",
  title: "Sweep the clocks",
  stage: 2,
  source: "Mesh 0.8\u20132.2 GHz and core 1.5\u20133.9 GHz sweeps \u00b7 4 endpoints",
  dur: DUR,
  cues,
  C: Body,
};
