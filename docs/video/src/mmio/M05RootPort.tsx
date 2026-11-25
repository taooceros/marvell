import React from "react";
import { C, Cue, SceneDef, Stage, useT } from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 8.50, "Round two: six `chown`s, still no root shell. Now we may map the root port's own registers, read-only, one address.", "Round two: six file permissions, still no root shell. Now we may map the root port's own registers, read only, one address."], // 0
  [8.85, 19.31, "A read to the root port turns around inside it: **205.5 ns**. Same core, mesh and IIO, but no link and no card.", "Reading the root port, the request turns around inside it after two hundred five nanoseconds. Same core and mesh, same I R P and I I O, but no link and no card."], // 1
  [19.66, 27.12, "A reviewer's warning: matching counters don't prove a shared path. A config read that never leaves the port counts the same.", "But a reviewer warned us: matching counters do not prove a shared path. A config read that never leaves the root port counts the same."], // 2
  [27.47, 35.43, "So a gate first: on the same CHA, root port and card memory spend the same time outside the TOR: **21.2** vs **22.3 ns**.", "So it had to pass a gate: on the same C H A, it must spend the same time outside the T O R as a real card read. It does."], // 3
  [35.78, 43.22, "Then a control group: many cores on one address stall at 1 / TOR on **both** paths. The limit is the CHA's, not the port's.", "And a control group: many cores on one address stall on the card register just like on the root port. That limit comes from the C H A."], // 4
  [43.57, 52.65, "Card register minus root port: **+312.1 ns**. That is the host's PCIe layers, the link, PEM0, and the path to the register.", "Card register minus root port: three hundred twelve nanoseconds. The host's P C I E layers, the link, P E M zero, and the path to the register."], // 5
];
const DUR = 54.05; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  void t;
  void T;
  return <Stage>{null}</Stage>;
}

export const M05: SceneDef = {
  id: "M05",
  title: "An endpoint inside the host",
  stage: 2,
  source: "Root port 98:01.0 BAR0, read-only mmap \u00b7 408 runs, 0 errors",
  dur: DUR,
  cues,
  C: Body,
};
