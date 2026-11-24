// Collect every narrated line for CcnicPaperStory: `bun tts/ccnic_manifest.ts` -> tts/ccnic_manifest.json
import { writeFileSync } from "node:fs";
import { CCNIC_DEFS } from "../src/ccnic";
import { cueKey } from "../src/lib";

const lines = CCNIC_DEFS.flatMap((d) =>
  d.cues.map(([, , caption, spoken], i) => ({
    key: cueKey(d.id, i),
    text: spoken ?? caption.replace(/\*\*|`/g, ""),
  })),
);
writeFileSync(new URL("./ccnic_manifest.json", import.meta.url), JSON.stringify(lines, null, 1));
console.log(`${lines.length} lines`);
