import { Series } from "remotion";
import { C, FPS, makeWarp, Scene } from "../lib";
import { CCNIC_DEFS } from "./index";
import ccnicVoice from "./voice.json";

export const CCNIC_STAGES: { label: string; color: string }[] = [
  { label: "PCIe Speed", color: C.cyan },
  { label: "TX Buffers", color: C.purple },
  { label: "Pacing", color: C.orange },
  { label: "DirectPush", color: C.pink },
  { label: "100GbE", color: C.green },
  { label: "Active NIC", color: C.yellow },
  { label: "Takeaways", color: "#5eead4" },
];

export const CCNIC_BRACKET = "CC-NIC Does Not Need Cache Coherence · saturn ⇄ CN106 ⇄ 100GbE ⇄ mercury";

const voiceMap = ccnicVoice as Record<string, number>;

export const CCNIC_SCENES = CCNIC_DEFS.map((def) => {
  const warp = makeWarp(def.id, def.cues, def.dur, voiceMap);
  return {
    def,
    warp,
    frames: Math.ceil(warp.realDur * FPS),
    Only: () => (
      <Scene def={def} warp={warp} voiceMap={voiceMap} stages={CCNIC_STAGES} railBracket={CCNIC_BRACKET} />
    ),
  };
});

export const ccnicTotalFrames = CCNIC_SCENES.reduce((a, s) => a + s.frames, 0);

export const CcnicPaperStoryVideo = () => (
  <Series>
    {CCNIC_SCENES.map((s) => (
      <Series.Sequence key={s.def.id} durationInFrames={s.frames}>
        <Scene def={s.def} warp={s.warp} voiceMap={voiceMap} stages={CCNIC_STAGES} railBracket={CCNIC_BRACKET} />
      </Series.Sequence>
    ))}
  </Series>
);
