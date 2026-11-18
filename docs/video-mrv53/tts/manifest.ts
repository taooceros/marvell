// Collect every narrated line: `bun tts/manifest.ts` -> tts/manifest.json [{key, text}]
import { writeFileSync } from "node:fs";
import { DEFS } from "../src/scenes";
import { cueKey } from "../src/lib";

const lines = DEFS.flatMap((d) =>
  d.cues.map(([, , caption, spoken], i) => ({
    key: cueKey(d.id, i),
    text: spoken ?? caption.replace(/\*\*|`/g, ""),
  })),
);
writeFileSync(new URL("./manifest.json", import.meta.url), JSON.stringify(lines, null, 1));
console.log(`${lines.length} lines`);
