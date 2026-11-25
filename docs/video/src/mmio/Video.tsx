import { Series } from "remotion";
import { FPS, makeWarp } from "../lib";
import { CcnicScene } from "../ccnic/kit";
import { MMIO_DEFS } from "./index";
import { MMIO_STAGES } from "./mk";
import mmioVoice from "./voice.json";

const voiceMap = mmioVoice as Record<string, number>;

export const MMIO_SCENES = MMIO_DEFS.map((def) => {
  const warp = makeWarp(def.id, def.cues, def.dur, voiceMap);
  return {
    def,
    warp,
    frames: Math.ceil(warp.realDur * FPS),
    Only: () => <CcnicScene def={def} warp={warp} voiceMap={voiceMap} stages={MMIO_STAGES} />,
  };
});

export const mmioTotalFrames = MMIO_SCENES.reduce((a, s) => a + s.frames, 0);

export const MmioLatencyBreakdownVideo = () => (
  <Series>
    {MMIO_SCENES.map((s) => (
      <Series.Sequence key={s.def.id} durationInFrames={s.frames}>
        <CcnicScene def={s.def} warp={s.warp} voiceMap={voiceMap} stages={MMIO_STAGES} />
      </Series.Sequence>
    ))}
  </Series>
);
