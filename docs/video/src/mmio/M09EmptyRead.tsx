import React from "react";
import { C, Cue, SceneDef, Stage, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 9.70, "Trap four: round one sized the link with sysfs config reads, **+209 ns** card minus root port. But each read also runs kernel code.", "Trap four. Round one sized the link with config reads through sysfs: two hundred nine nanoseconds. But each such read also runs kernel code."], // 0
  [10.05, 19.89, "The trick: `pread` at offset 64. Users may read only 64 bytes, so it returns nothing, after doing all the kernel's work.", "The fix is a read that does nothing. Users may read only sixty-four bytes, so a p read at offset sixty-four returns nothing, after all the kernel's work."], // 1
  [20.24, 28.62, "IO counters prove it never reaches hardware: **0**. Yet root port and VF pay **+17.3** and **+20.9 ns** in power management.", "The I O counters show it never reaches hardware. Yet the root port and the virtual function pay about twenty nanoseconds more, in power management."], // 2
  [28.97, 35.95, "Subtract the empty read from the real one, and a fake **19.1 ns** gap between the card's two functions vanishes.", "Subtract the empty read from the real one, and a fake nineteen nanosecond gap between the card's two functions disappears."], // 3
  [36.30, 45.24, "Device minus device cancels the shared locks too: across the link, **ΔH = +221.8 ns**, about 90 ns below the register path.", "Device minus device cancels the shared locks too: two hundred twenty-two nanoseconds across the link, ninety less than the deeper register path. It fits."], // 4
];
const DUR = 46.64; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  void t;
  void T;
  return <Stage>{null}</Stage>;
}

export const M09: SceneDef = {
  id: "M09",
  title: "Trap: the kernel in the ruler",
  stage: 3,
  source: "sysfs config reads at offset 0 vs 64 \u00b7 7 devices \u00b7 IO counters",
  dur: DUR,
  cues,
  C: Body,
};
