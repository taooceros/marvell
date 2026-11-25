import type { SceneDef } from "../lib";
import { M01 } from "./M01Case";
import { M02 } from "./M02Stopwatch";
import { M03 } from "./M03TorClock";
import { M04 } from "./M04Rulers";
import { M05 } from "./M05RootPort";
import { M06 } from "./M06ClockSweep";
import { M07 } from "./M07ClockGrid";
import { M08 } from "./M08WakeUp";
import { M09 } from "./M09EmptyRead";
import { M10 } from "./M10SilentWc";
import { M11 } from "./M11Verdict";
import { M12 } from "./M12Lessons";

export const MMIO_DEFS: SceneDef[] = [M01, M02, M03, M04, M05, M06, M07, M08, M09, M10, M11, M12];
