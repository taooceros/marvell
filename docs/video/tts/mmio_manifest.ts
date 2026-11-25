// Collect every narrated line for MmioLatencyBreakdown: `bun tts/mmio_manifest.ts` -> tts/mmio_manifest.json
import { writeFileSync } from "node:fs";
import { MMIO_DEFS } from "../src/mmio";
import { cueKey } from "../src/lib";

const lines = MMIO_DEFS.flatMap((d) =>
  d.cues.map(([, , caption, spoken], i) => ({
    key: cueKey(d.id, i),
    text: spoken ?? caption.replace(/\*\*|`/g, ""),
  })),
);
writeFileSync(new URL("./mmio_manifest.json", import.meta.url), JSON.stringify(lines, null, 1));
console.log(`${lines.length} lines`);
