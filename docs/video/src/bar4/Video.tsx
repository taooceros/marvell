import { Series } from "remotion";
import { FPS, makeWarp } from "../lib";
import { BAR4_DEFS } from "./index";
import { BAR4_STAGES, Bar4Scene } from "./kit";
import bar4Voice from "./voice.json";

const voiceMap = bar4Voice as Record<string, number>;

export const BAR4_SCENES = BAR4_DEFS.map((def) => {
  const warp = makeWarp(def.id, def.cues, def.dur, voiceMap);
  return {
    def,
    warp,
    frames: Math.ceil(warp.realDur * FPS),
    Only: () => <Bar4Scene def={def} warp={warp} voiceMap={voiceMap} stages={BAR4_STAGES} />,
  };
});

export const bar4TotalFrames = BAR4_SCENES.reduce((a, s) => a + s.frames, 0);

export const Bar4PollingWallsVideo = () => (
  <Series>
    {BAR4_SCENES.map((s) => (
      <Series.Sequence key={s.def.id} durationInFrames={s.frames}>
        <Bar4Scene def={s.def} warp={s.warp} voiceMap={voiceMap} stages={BAR4_STAGES} />
      </Series.Sequence>
    ))}
  </Series>
);
