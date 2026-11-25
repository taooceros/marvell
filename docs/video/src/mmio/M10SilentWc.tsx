import React from "react";
import { C, Cue, SceneDef, Stage, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 7.56, "Trap five: we mapped card memory write-combining, `resource4_wc`, hoping four 16-byte streaming loads would merge.", "Trap five. We mapped card memory as write-combining, hoping four sixteen-byte streaming loads would merge into one read."], // 0
  [7.91, 16.37, "They didn't: still **4 TLPs** and **2,484.6 ns**, exactly like uncached. No error, no warning.", "They did not: still four T L Ps, almost two and a half microseconds, exactly like uncached. No error, no warning."], // 1
  [16.72, 26.66, "Root cause: the driver `ioremap()`s all 64 MiB of BAR4 as uncached at load time, and Linux PAT silently matches it.", "The octeon E P driver maps all of bar four as uncached at load time, so Linux PAT quietly downgrades our write-combining mapping. Not even a log line."], // 2
  [27.01, 36.09, "What works today: one 64-byte `zmm` load, **1 TLP**, **628.2 ns**, **3.95×** faster than four small loads.", "What works today is one sixty-four byte Z M M load: a single T L P, six hundred twenty-eight nanoseconds, nearly four times faster."], // 3
];
const DUR = 37.49; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  void t;
  void T;
  return <Stage>{null}</Stage>;
}

export const M10: SceneDef = {
  id: "M10",
  title: "Trap: the silent downgrade",
  stage: 3,
  source: "resource4_wc vs UC \u00b7 driver octep_main.c \u00b7 Linux x86 PAT",
  dur: DUR,
  cues,
  C: Body,
};
