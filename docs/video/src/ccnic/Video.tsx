import { Series } from "remotion";
import { FPS, makeWarp } from "../lib";
import { CCNIC_DEFS } from "./index";
import { CcnicScene } from "./kit";
import ccnicVoice from "./voice.json";

const voiceMap = ccnicVoice as Record<string, number>;

export const CCNIC_SCENES = CCNIC_DEFS.map((def) => {
  const warp = makeWarp(def.id, def.cues, def.dur, voiceMap);
  return {
    def,
    warp,
    frames: Math.ceil(warp.realDur * FPS),
    Only: () => <CcnicScene def={def} warp={warp} voiceMap={voiceMap} />,
  };
});

export const ccnicTotalFrames = CCNIC_SCENES.reduce((a, s) => a + s.frames, 0);

export const CcnicPaperStoryVideo = () => (
  <Series>
    {CCNIC_SCENES.map((s) => (
      <Series.Sequence key={s.def.id} durationInFrames={s.frames}>
        <CcnicScene def={s.def} warp={s.warp} voiceMap={voiceMap} />
      </Series.Sequence>
    ))}
  </Series>
);
