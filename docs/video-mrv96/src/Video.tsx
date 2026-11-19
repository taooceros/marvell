import { Series } from "remotion";
import { FPS, makeWarp, Scene } from "./lib";
import { DEFS } from "./scenes";

export const SCENES = DEFS.map((def) => {
  const warp = makeWarp(def.id, def.cues, def.dur);
  return { def, warp, frames: Math.ceil(warp.realDur * FPS), Only: () => <Scene def={def} warp={warp} /> };
});

export const totalFrames = SCENES.reduce((a, s) => a + s.frames, 0);

export const Mrv96Video = () => (
  <Series>
    {SCENES.map((s) => (
      <Series.Sequence key={s.def.id} durationInFrames={s.frames}>
        <Scene def={s.def} warp={s.warp} />
      </Series.Sequence>
    ))}
  </Series>
);
