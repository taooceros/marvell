import "@fontsource/inter/400.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/800.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/noto-sans-sc/400.css";
import "@fontsource/noto-sans-sc/600.css";
import "@fontsource/noto-sans-sc/700.css";
import { Composition } from "remotion";
import { FPS, H, W } from "./lib";
import { SCENES, PcieVideo, totalFrames } from "./Video";
import { CCNIC_SCENES, CcnicPaperStoryVideo, ccnicTotalFrames } from "./ccnic/Video";
import { MMIO_SCENES, MmioLatencyBreakdownVideo, mmioTotalFrames } from "./mmio/Video";
import { BAR4_SCENES, Bar4PollingWallsVideo, bar4TotalFrames } from "./bar4/Video";

export const Root = () => (
  <>
    <Composition id="PcieDatapath" component={PcieVideo} durationInFrames={totalFrames} fps={FPS} width={W} height={H} />
    {SCENES.map((s) => (
      <Composition
        key={s.def.id}
        id={s.def.id}
        component={s.Only}
        durationInFrames={s.frames}
        fps={FPS}
        width={W}
        height={H}
      />
    ))}
    <Composition
      id="CcnicPaperStory"
      component={CcnicPaperStoryVideo}
      durationInFrames={ccnicTotalFrames}
      fps={FPS}
      width={W}
      height={H}
    />
    {CCNIC_SCENES.map((s) => (
      <Composition
        key={s.def.id}
        id={s.def.id}
        component={s.Only}
        durationInFrames={s.frames}
        fps={FPS}
        width={W}
        height={H}
      />
    ))}
    <Composition
      id="MmioLatencyBreakdown"
      component={MmioLatencyBreakdownVideo}
      durationInFrames={mmioTotalFrames}
      fps={FPS}
      width={W}
      height={H}
    />
    {MMIO_SCENES.map((s) => (
      <Composition
        key={s.def.id}
        id={s.def.id}
        component={s.Only}
        durationInFrames={s.frames}
        fps={FPS}
        width={W}
        height={H}
      />
    ))}
    <Composition
      id="Bar4PollingWalls"
      component={Bar4PollingWallsVideo}
      durationInFrames={bar4TotalFrames}
      fps={FPS}
      width={W}
      height={H}
    />
    {BAR4_SCENES.map((s) => (
      <Composition
        key={s.def.id}
        id={s.def.id}
        component={s.Only}
        durationInFrames={s.frames}
        fps={FPS}
        width={W}
        height={H}
      />
    ))}
  </>
);
