import type { SceneDef } from "../lib";
import { C01 } from "./C01Intro";
import { C02 } from "./C02PremiseSpeed";
import { C03 } from "./C03SubUsAndTraps";
import { C04 } from "./C04TxBuffers";
import { C05 } from "./C05FlowControl";
import { C06 } from "./C06DirectPush";
import { C07 } from "./C07FairSdpAudit";
import { C08 } from "./C08Real100gbeLat";
import { C09 } from "./C09LineRate100gbe";
import { C10 } from "./C10ActiveNic";
import { C11 } from "./C11HonestAudit";
import { C12 } from "./C12Takeaways";

export const CCNIC_DEFS: SceneDef[] = [C01, C02, C03, C04, C05, C06, C07, C08, C09, C10, C11, C12];
