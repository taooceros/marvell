import React from "react";
import { C, Cue, SceneDef, Stage, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 9.90, "The method travels: endpoints as rulers, Little's law on counters, clock sweeps, and reads that do nothing.", "The method travels to any server: use endpoints as rulers, apply Little's law to counters, sweep the clocks, and cancel software with reads that do nothing."], // 0
  [10.25, 15.81, "And distrust every neat number: measure it twice, add a control group, know your timer's grid.", "And distrust every neat number: measure twice, add a control group, and know your timer's grid."], // 1
  [16.16, 25.44, "For our design the lesson is blunt: a read across PCIe costs **~620 ns** while the core waits. Our fast path never reads across.", "For our design the lesson is blunt: a read across P C I E costs over six hundred nanoseconds while the core waits. So our fast path never reads across."], // 2
  [25.79, 35.33, "DirectPush uses posted writes both ways: the host pushes with `MOVDIR64B`, the card pushes back, each side reads only its own cache.", "Direct Push sends posted writes both ways: the host pushes with move dir sixty-four B, the card pushes back, and each side only reads its own cache."], // 3
  [35.68, 43.20, "A full request and reply then takes **~0.9 µs**, and most of it is the same physical round trip we just took apart.", "A full request and reply then takes about nine tenths of a microsecond, most of it the same physical round trip we just took apart."], // 4
  [43.55, 51.37, "627 ns, six stages, each proved twice. Next time a device feels slow, open it up the same way.", "Six hundred twenty-seven nanoseconds, six stages, each proved twice. When a device feels slow, open it up the same way."], // 5
];
const DUR = 52.77; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  void t;
  void T;
  return <Stage>{null}</Stage>;
}

export const M12: SceneDef = {
  id: "M12",
  title: "What it buys us",
  stage: 4,
  source: "Method summary \u00b7 DirectPush measured on the same testbed",
  dur: DUR,
  cues,
  C: Body,
};
