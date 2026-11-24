"""Write/refresh CcnicPaperStory scene cue blocks from tts/ccnic_script_data.py.

  python3 tts/ccnic_gen_scenes.py            # create missing stubs, refresh cue blocks + timing
  python3 tts/ccnic_gen_scenes.py --script   # also regenerate SCRIPT_CCNIC.md
  python3 tts/ccnic_gen_scenes.py --only C01,C02
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ccnic_script_data import SCENES, STORY  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SC_DIR = ROOT / "src" / "ccnic"
VOICE_PATH = SC_DIR / "voice.json"
VOICE = json.loads(VOICE_PATH.read_text()) if VOICE_PATH.exists() else {}
PAD, GAP, LEAD, TAIL = 0.45, 0.35, 0.6, 1.4


def spoken_of(cap, spk):
    return spk if spk else re.sub(r"\*\*|`", "", cap)


def timing(sc):
    t = LEAD
    rows = []
    for i, (cap, spk, src) in enumerate(sc["cues"]):
        key = f"{sc['id']}_{i:02d}"
        if key in VOICE:
            seg = VOICE[key] + PAD
        else:
            seg = round(len(spoken_of(cap, spk)) / 16.4 + 0.9, 2)
        rows.append((round(t, 2), round(t + seg, 2), cap, spk, src))
        t += seg + GAP
    return rows, round(t + TAIL - GAP, 2)


def cue_block(sc):
    rows, dur = timing(sc)
    lines = ["const cues: Cue[] = ["]
    for i, (s, e, cap, spk, _) in enumerate(rows):
        if spk is None:
            lines.append(f"  [{s:.2f}, {e:.2f}, {json.dumps(cap, ensure_ascii=False)}], // {i}")
        else:
            lines.append(
                f"  [{s:.2f}, {e:.2f}, {json.dumps(cap, ensure_ascii=False)}, {json.dumps(spk, ensure_ascii=False)}], // {i}"
            )
    lines.append("];")
    lines.append(f"const DUR = {dur:.2f}; // <dur>")
    return "\n".join(lines), dur


STUB = """import React from "react";
import {{ C, Cue, SceneDef, Stage, useT }} from "../lib";

// <cues>
{block}
// </cues>

const T = cues.map((c) => c[0]);

function Body() {{
  const t = useT();
  void t;
  void T;
  return <Stage>{{null}}</Stage>;
}}

export const {id}: SceneDef = {{
  id: {id_q},
  title: {title_q},
  {stage_line}source: {source_q},
  dur: DUR,
  cues,
  C: Body,
}};
"""


def main():
    SC_DIR.mkdir(parents=True, exist_ok=True)
    args = sys.argv[1:]
    write_script = "--script" in args
    only = None
    if "--only" in args:
        val = args[args.index("--only") + 1]
        only = set() if val == "NONE" else set(val.split(","))

    total = 0.0
    table_rows = []
    script_sections = []

    for sc in SCENES:
        block, dur = cue_block(sc)
        total += dur
        path = SC_DIR / f"{sc['file']}.tsx"
        if only is None or sc["id"] in only:
            if not path.exists():
                stage_line = f"stage: {sc['stage']},\n  " if sc["stage"] is not None else ""
                path.write_text(
                    STUB.format(
                        block=block,
                        id=sc["id"],
                        id_q=json.dumps(sc["id"]),
                        title_q=json.dumps(sc["title"]),
                        stage_line=stage_line,
                        source_q=json.dumps(sc["source"]),
                    )
                )
                print(f"created {path.name} ({dur:.2f}s, {len(sc['cues'])} cues)")
            else:
                txt = path.read_text()
                new_txt, n = re.subn(
                    r"// <cues>.*?// </cues>",
                    lambda _: f"// <cues>\n{block}\n// </cues>",
                    txt,
                    count=1,
                    flags=re.S,
                )
                assert n == 1, f"Missing // <cues> block in {path}"
                path.write_text(new_txt)
                print(f"updated {path.name} ({dur:.2f}s, {len(sc['cues'])} cues)")

        rows, _ = timing(sc)
        table_rows.append(f"| `{sc['id']}` | {sc['title'] or 'Intro'} | {len(sc['cues'])} | {dur:.1f} s | {sc['source'] or '—'} |")
        sec = [f"## {sc['id']}: {sc['title'] or 'Intro'} ({dur:.1f} s)\n"]
        if sc["source"]:
            sec.append(f"*Source: `{sc['source']}`*\n")
        for i, (s, e, cap, spk, src) in enumerate(rows):
            sec.append(f"- **[{s:.2f}–{e:.2f}s]** (`{src}`) {cap}")
            if spk:
                sec.append(f"  - *Spoken*: {spk}")
        script_sections.append("\n".join(sec))

    print(f"Total: {len(SCENES)} scenes, {sum(len(s['cues']) for s in SCENES)} cues, {total:.1f} s ({total/60:.2f} min)")

    if write_script:
        md = [
            "# CC-NIC Does Not Need Cache Coherence — Narration Script (`CcnicPaperStory`)\n",
            f"Total: **{len(SCENES)} scenes**, **{sum(len(s['cues']) for s in SCENES)} cues**, **{total/60:.2f} min** ({total:.1f} s).\n",
            "## Build\n",
            "```bash",
            "python3 tts/ccnic_gen_scenes.py --script",
            "bun tts/ccnic_manifest.ts",
            "~/tts/asr/bin/python tts/ccnic_synth.py --jobs 4",
            "python3 tts/ccnic_gen_scenes.py --script",
            "./render_ccnic.sh",
            "```\n",
            "## Story\n",
            STORY,
            "## Scenes\n",
            "| ID | Title | Cues | Duration | Source |",
            "| --- | --- | ---: | ---: | --- |",
            *table_rows,
            "\n---\n",
            *script_sections,
        ]
        (ROOT / "SCRIPT_CCNIC.md").write_text("\n".join(md) + "\n")
        print("SCRIPT_CCNIC.md written")


if __name__ == "__main__":
    main()
