import React from "react";
import { C, Cue, SceneDef, Stage, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 9.32, "Inside the host chip, each uncached read is handled by one **CHA** and tracked in its table of requests, the **TOR**.", "Inside the host chip, each uncached read is handled by one caching agent, the C H A, and tracked in its table of requests, the T O R."], // 0
  [9.67, 16.69, "No public event counts these small reads. An unnamed encoding does, and it matches every counter below it **1 : 1**.", "No public event counts small reads like these. An unnamed encoding does, and it matches every counter below it, one for one."], // 1
  [17.04, 25.12, "The TOR keeps the entry until the reply returns. Little's law: occupancy ÷ reads = time spent below the CHA.", "That table keeps the entry until the reply returns. So Little's law turns two counters into a time: occupancy divided by reads."], // 2
  [25.47, 34.73, "Card register read: **516.4 ns**. Below the CHA: **495.6 ns**. Core and mesh outside it: only **21.8 ns**.", "Reading a card register takes five hundred sixteen nanoseconds. Four hundred ninety-six are below the C H A; the core and mesh take only twenty-two."], // 3
  [35.08, 43.30, "From all 32 cores, distance moves the total by **10 ns**. Where the TOR frees early, the time reappears outside it.", "From all thirty-two cores, distance moves the total by just ten nanoseconds. And where the table lets go early, the time reappears outside it."], // 4
  [43.65, 50.75, "Eight cores reading at once scale **8.0×**: nothing queues. And one core never overlaps two uncached reads.", "Eight cores reading at once go eight times faster, so nothing queues. And one core never overlaps two uncached reads."], // 5
];
const DUR = 52.15; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  void t;
  void T;
  return <Stage>{null}</Stage>;
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
