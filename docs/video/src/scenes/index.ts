import type { SceneDef } from "../lib";
import { S01 } from "./S01Intro";
import { S02 } from "./S02Testbed";
import { S03 } from "./S03Doorbell";
import { S04 } from "./S04Inline";
import { S05 } from "./S05Order";
import { S06 } from "./S06Rdtsc";
import { S07 } from "./S07MemPoll";
import { S08 } from "./S08Dma";
import { S09 } from "./S09Pull";
import { S10 } from "./S10Window";
import { S11 } from "./S11Store";
import { S12 } from "./S12MultiSdp";
import { S13 } from "./S13Coalesce";
import { S14 } from "./S14Verify";
import { S15 } from "./S15Takeaways";

export const DEFS: SceneDef[] = [S01, S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S12, S13, S14, S15];
