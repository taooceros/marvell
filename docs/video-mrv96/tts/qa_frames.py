"""Per-cue QA sheets from the final MP4: one frame per cue at 70% of its segment, 3 columns per scene.

  python3 tts/qa_frames.py [S05 S07 ...] [--at 0.7] [--video out/mrv96-six-experiments.mp4]  ->  out/qa/<id>.png

Scene offsets = cumulative ceil(DUR*30)/30 (script time == real time once every cue has a voice clip).
"""

import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from math import ceil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SC = ROOT / "src" / "scenes"
args = sys.argv[1:]
at = float(args[args.index("--at") + 1]) if "--at" in args else 0.7
video = args[args.index("--video") + 1] if "--video" in args else str(ROOT / "out" / "mrv96-six-experiments.mp4")
only = {a for a in args if re.fullmatch(r"S\d\d", a)}
out = ROOT / "out" / "qa"
out.mkdir(parents=True, exist_ok=True)

index = (SC / "index.ts").read_text()
order = re.findall(r'import \{ (S\d\d) \} from "\./(\w+)";', index)
offset = 0.0
jobs = []
for sid, stem in order:
    src = (SC / f"{stem}.tsx").read_text()
    block = re.search(r"// <cues>(.*?)// </cues>", src, re.S).group(1)
    starts = [float(m) for m in re.findall(r'^\s*\[(\d+\.\d+), \d+\.\d+, "', block, re.M)]
    dur = float(re.search(r"const DUR = (\d+\.\d+);", block).group(1))
    if not only or sid in only:
        ends = starts[1:] + [dur]
        times = [offset + s + at * (e - s) for s, e in zip(starts, ends)]
        jobs.append((sid, times))
    offset += ceil(dur * 30) / 30


def grab(sid, i, t):
    p = out / f"{sid}_{i:02d}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                    "-vf", f"scale=640:-1,drawtext=text='{sid} cue {i} @{t:.1f}s':x=8:y=8:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.6",
                    str(p)], check=True)
    return p


with ThreadPoolExecutor(16) as ex:
    for sid, times in jobs:
        frames = list(ex.map(lambda a: grab(sid, *a), enumerate(times)))
        n = len(frames)
        cols = 3
        layout = "|".join(f"{(i % cols) * 640}_{(i // cols) * 360}" for i in range(n))
        cmd = ["ffmpeg", "-v", "error", "-y"]
        for f in frames:
            cmd += ["-i", str(f)]
        cmd += ["-filter_complex", f"{''.join(f'[{i}:v]' for i in range(n))}xstack=inputs={n}:layout={layout}:fill=black",
                str(out / f"{sid}.png")]
        if n == 1:
            cmd = ["cp", str(frames[0]), str(out / f"{sid}.png")]
        subprocess.run(cmd, check=True)
        for f in frames:
            f.unlink()
        print(out / f"{sid}.png")
print(f"total {offset:.2f} s")
