// Collect every narrated line for Bar4PollingWalls: `bun tts/bar4_manifest.ts` -> tts/bar4_manifest.json
import { writeFileSync } from "node:fs";
import { BAR4_DEFS } from "../src/bar4";
import { cueKey } from "../src/lib";

const lines = BAR4_DEFS.flatMap((d) =>
  d.cues.map(([, , caption, spoken], i) => ({
    key: cueKey(d.id, i),
    text: spoken ?? caption.replace(/\*\*|`/g, ""),
  })),
);
writeFileSync(new URL("./bar4_manifest.json", import.meta.url), JSON.stringify(lines, null, 1));
console.log(`${lines.length} lines`);
