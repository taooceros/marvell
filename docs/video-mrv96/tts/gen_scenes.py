"""Write/refresh scene cue blocks from tts/script_data.py.

  python3 tts/gen_scenes.py            # create missing stubs, refresh cue blocks + timing in existing files
  python3 tts/gen_scenes.py --script   # also regenerate SCRIPT.md
  python3 tts/gen_scenes.py --only S12,S13   # rewrite only these scenes' cue blocks

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
        seg = (clip + PAD + GAP) if clip is not None else len(spoken_of(cap, spk)) / 16.4 + 0.9
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
        out = ["# Why 161.6 M/s one way but ~13 M/s in a loop? Six single-variable experiments on the host ⇄ DPU "
               "direct path (narrated explainer script)", "",
               "Raw data: `logs/mrv{24,50,53,54,55,61,70,76}-*/` of `~/dev/marvell` (2026-11-17/18; every plotted run "
               "verified, lost = bad = torn = 0). Every on-screen number is recomputed by `data/verify_facts.py` and "
               "listed with its source in `data/FACTS.md`; `[INF]` marks our interpretations (spoken as \"our reading\").", "",
               "## Build", "",
               "1. Edit captions/narration only in `tts/script_data.py`, then `python3 tts/gen_scenes.py --script` "
               "(rewrites only the `// <cues>` blocks and this file).",
               "2. Narration: `bun tts/manifest.ts && ~/tts/asr/bin/python tts/synth.py` (edge-tts en-US-AndrewNeural +0%, "
               "cached by text), then rerun `python3 tts/gen_scenes.py --script` to retime.",
               "3. Check: `npx tsc -p .`; `./stills.sh S05 3 9 15` → half-size contact sheet `out/S05-sheet.png`.",
               "4. Final: `./render.sh` → `out/mrv96-six-experiments.mp4` (1920×1080 @ 30 fps); per-cue QA sheets: "
               "`python3 tts/qa_frames.py` → `out/qa/S*.png`.", "",
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
