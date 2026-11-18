"""Write/refresh scene cue blocks from tts/script_data.py.

  python3 tts/gen_scenes.py            # create missing stubs, refresh cue blocks + timing in existing files
  python3 tts/gen_scenes.py --script   # also regenerate SCRIPT.md
  python3 tts/gen_scenes.py --only S05,S06   # rewrite only these scenes' cue blocks

Timing: if src/voice.json has a clip for a cue, its segment lasts clip + PAD + GAP (script time == real time);
otherwise an estimate from the spoken length. Each scene starts with LEAD s of silence and ends with TAIL s.
Only the text between `// <cues>` and `// </cues>` and the `dur:` line marked `// <dur>` are rewritten, so scene
bodies written later survive.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_data import SCENES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SC_DIR = ROOT / "src" / "scenes"
VOICE = json.loads((ROOT / "src" / "voice.json").read_text())
PAD, GAP, LEAD, TAIL = 0.45, 0.35, 0.6, 1.4


def spoken_of(cap, spk):
    return spk if spk else re.sub(r"\*\*|`", "", cap)


def timing(sc):
    t = LEAD
    rows = []
    for i, (cap, spk, _src) in enumerate(sc["cues"]):
        key = f"{sc['id']}_{i:02d}"
        clip = VOICE.get(key)
        seg = (clip + PAD + GAP) if clip is not None else len(spoken_of(cap, spk)) / 5.6 + 0.9
        rows.append((round(t, 2), round(t + seg - GAP, 2)))
        t += seg
    return rows, round(t + TAIL - GAP, 2)


def cue_block(sc):
    rows, dur = timing(sc)
    lines = ["// <cues>", "const cues: Cue[] = ["]
    for i, ((s, e), (cap, spk, _src)) in enumerate(zip(rows, sc["cues"])):
        parts = [f"{s:.2f}", f"{e:.2f}", json.dumps(cap, ensure_ascii=False)]
        if spk:
            parts.append(json.dumps(spk, ensure_ascii=False))
        lines.append(f"  [{', '.join(parts)}], // {i}")
    lines += ["];", f"const DUR = {dur:.2f}; // <dur>", "// </cues>"]
    return "\n".join(lines), dur


STUB = """import {{ C, Cue, SceneDef, Stage, useT }} from "../lib";

{block}

function Body() {{
  const t = useT();
  return <Stage>{{null}}</Stage>;
}}

export const {id}: SceneDef = {{
  id: "{id}",
  title: {title},
  stage: {stage},
  source: {source},
  dur: DUR,
  cues,
  C: Body,
}};
"""


def main():
    SC_DIR.mkdir(parents=True, exist_ok=True)
    total = 0.0
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    for sc in SCENES:
        block, dur = cue_block(sc)
        total += dur
        f = SC_DIR / f"{sc['file']}.tsx"
        if only and sc["id"] not in only:
            print(f"{sc['id']:4} {len(sc['cues']):2} cues  {dur:7.2f} s  (skipped)")
            continue
        if f.exists():
            src = f.read_text()
            new = re.sub(r"// <cues>.*?// </cues>", lambda _m: block, src, flags=re.S)
            if new == src and block not in src:
                raise SystemExit(f"{f}: no // <cues> block found")
            f.write_text(new)
        else:
            f.write_text(
                STUB.format(
                    block=block,
                    id=sc["id"],
                    title=json.dumps(sc["title"], ensure_ascii=False),
                    stage="undefined" if sc["stage"] is None else sc["stage"],
                    source=json.dumps(sc["source"], ensure_ascii=False),
                )
            )
        print(f"{sc['id']:4} {len(sc['cues']):2} cues  {dur:7.2f} s")
    imports = "\n".join(f'import {{ {sc["id"]} }} from "./{sc["file"]}";' for sc in SCENES)
    ids = ", ".join(sc["id"] for sc in SCENES)
    (SC_DIR / "index.ts").write_text(
        f'import type {{ SceneDef }} from "../lib";\n{imports}\n\nexport const DEFS: SceneDef[] = [{ids}];\n'
    )
    print(f"total {total:.1f} s = {total / 60:.2f} min, {sum(len(s['cues']) for s in SCENES)} cues")
    if "--script" in sys.argv:
        out = ["# MRV-53：直接通路各状态的吞吐由谁的时间决定（中文配音讲解脚本）", "",
               "实验数据来自 `logs/mrv53-2026-11-17/runs.jsonl`（817 条记录，`id = 210..817` 608 次校准运行，`lost = bad = torn = 0`）及 Linear `MRV-53` 评审复核记录。所有数字出处见 `data/FACTS.md`；`[INF]` 标记推论（口播说“我们认为”）。", "",
               "## Build", "",
               "1. 仅在 `tts/script_data.py` 编辑字幕与口播，然后运行 `python3 tts/gen_scenes.py --script`（只重写 `// <cues>` 块与本文件）。",
               "2. 语音合成：`bun tts/manifest.ts && ~/tts/asr/bin/python tts/synth.py`（edge-tts `zh-CN-YunxiNeural` `+8%`，按文本哈希缓存），随后重跑 `python3 tts/gen_scenes.py --script` 用真实时长对齐。",
               "3. 单场景预览与类型检查：`npx tsc -p .`；`./stills.sh S05 3 9 15` → `out/S05-sheet.png`。",
               "4. 完整渲染：`./render.sh` → `out/mrv53-critical-path.mp4`（1920×1080 @ 30 fps），再用 `python3 tts/qa_frames.py` 生成逐 cue 检查表 `out/qa/S*.png`。", "",
               "## Scenes", "",
               "| Scene | Title | Length | Cues | Source |", "|---|---|---|---|---|"]
        for sc in SCENES:
            _rows, dur = timing(sc)
            out.append(f"| {sc['id']} | {sc['title']} | {dur:.0f} s | {len(sc['cues'])} | {sc['source']} |")
        out += ["", "## Cues (caption / narration / provenance)", ""]
        for sc in SCENES:
            rows, dur = timing(sc)
            out.append(f"### {sc['id']} {sc['title']} ({dur:.0f} s)")
            out.append("")
            for i, ((s, _e), (cap, spk, srcs)) in enumerate(zip(rows, sc["cues"])):
                out.append(f"{i + 1}. `{s:6.1f}s` {cap}")
                if spk:
                    out.append(f"   - spoken: {spk}")
                if srcs:
                    out.append(f"   - source: {srcs}")
            out.append("")
        (ROOT / "SCRIPT.md").write_text("\n".join(out))
        print("SCRIPT.md written")


if __name__ == "__main__":
    main()
