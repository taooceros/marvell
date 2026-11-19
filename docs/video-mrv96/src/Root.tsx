import "@fontsource/inter/400.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/800.css";
import "@fontsource/jetbrains-mono/400.css";
import { Composition } from "remotion";
import { FPS, H, W } from "./lib";
import { SCENES, Mrv96Video, totalFrames } from "./Video";

export const Root = () => (
  <>
    <Composition id="Mrv96SixExperiments" component={Mrv96Video} durationInFrames={totalFrames} fps={FPS} width={W} height={H} />
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
  </>
);
