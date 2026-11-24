"""Synthesize English Remotion narration with edge-tts (Microsoft neural voices, no local model) and update src/voice.json.

Adapted from the remotion-narrated-explainer-video skill's synth_edge.py (Mandarin) for English:
  bun tts/manifest.ts
  ~/tts/asr/bin/python tts/synth.py [--only S05_03,S07_01] [--force] [--voice en-US-AndrewNeural] [--rate +0%]

manifest.json = [{key, text}]. Each line: edge-tts mp3 -> trim silence, cap inner pauses at 0.3 s,
loudnorm -16 LUFS, 48 kHz mono -> public/voice/<key>.wav. If faster_whisper is importable (the
~/tts/asr venv has it, model "small" is cached), each clip gets a word error rate against its text;
> 0.25 is flagged. Unchanged lines (same voice/rate/text) are skipped unless --force.
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "public" / "voice"
TAKES = HERE / "takes"
VOICE_JSON = ROOT / "src" / "ccnic" / "voice.json"
SR_TTS = 24000
SR_OUT = 48000

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _int_en(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    for size, name in ((10**6, "million"), (1000, "thousand"), (100, "hundred")):
        if n >= size:
            hi, lo = divmod(n, size)
            return _int_en(hi) + " " + name + ("" if lo == 0 else " " + _int_en(lo))
    raise ValueError(n)


def digits_to_en(s: str) -> str:
    """ASR writes '191' / '3.5' / '12%'; the script spells numbers out. Compare like with like."""
    def num(m: re.Match) -> str:
        whole, _, frac = m.group(1).partition(".")
        en = _int_en(int(whole)) + (" point " + " ".join(_ONES[int(c)] for c in frac) if frac else "")
        return en + (" percent" if m.group(2) else "")
    return re.sub(r"(\d+(?:\.\d+)?)(%?)", num, s.replace(",", ""))


def norm_words(s: str) -> list[str]:
    s = digits_to_en(s).lower().replace("-", " ")
    words = re.sub(r"[^a-z0-9' ]+", " ", s).split()
    return [w for w in words if w not in {"a", "and", "the"}]  # "a hundred" vs "one hundred and"


def wer(ref: str, hyp: str) -> float:
    a, b = norm_words(ref), norm_words(hyp)
    if not a:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, wa in enumerate(a, 1):
        cur = [i]
        for j, wb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wa != wb)))
        prev = cur
    return prev[-1] / len(a)


def cap_pauses(x: np.ndarray, sr: int, floor_db=-42.0, max_gap=0.3, lead=0.05, tail=0.12) -> np.ndarray:
    """Trim leading/trailing silence and shorten silent stretches longer than max_gap."""
    win = int(0.02 * sr)
    n = len(x) // win
    if n == 0:
        return x
    frames = x[: n * win].reshape(n, win)
    loud = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9) > floor_db
    idx = np.flatnonzero(loud)
    if idx.size == 0:
        return x
    keep = np.zeros(n, bool)
    keep[idx[0] : idx[-1] + 1] = True
    run_start = None
    max_frames = int(max_gap / 0.02)
    for i in range(idx[0], idx[-1] + 1):
        if not loud[i]:
            run_start = i if run_start is None else run_start
            continue
        if run_start is not None and i - run_start > max_frames:
            keep[run_start + max_frames // 2 : i - max_frames // 2] = False
        run_start = None
    pieces = [frames[i] for i in range(n) if keep[i]]
    y = np.concatenate(pieces) if pieces else x
    return np.concatenate([np.zeros(int(lead * sr)), y, np.zeros(int(tail * sr))]).astype(np.float32)


def decode(src: Path, sr: int) -> np.ndarray:
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(pcm, dtype="<f4")


def text_hash(voice: str, rate: str, text: str) -> str:
    return hashlib.sha1("\x1f".join(("edge", voice, rate, text)).encode()).hexdigest()[:12]


def synth(key: str, text: str, voice: str, rate: str) -> float:
    mp3 = TAKES / f"{key}.mp3"
    for attempt in range(8):
        r = subprocess.run(["uvx", "edge-tts", "--voice", voice, f"--rate={rate}", "--text", text,
                            "--write-media", str(mp3)], capture_output=True, text=True)
        if r.returncode == 0 and mp3.exists() and mp3.stat().st_size > 0:
            break
        time.sleep(2 + 3 * attempt)  # NoAudioReceived is transient service throttling
    else:
        raise RuntimeError(f"edge-tts failed for {key}: {r.stderr[-400:]}")
    y = cap_pauses(decode(mp3, SR_TTS), SR_TTS)
    out = OUT / f"{key}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR_TTS), "-ac", "1", "-i", "-",
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(SR_OUT), "-ac", "1", str(out)],
                   input=y.tobytes(), check=True)
    with wave.open(str(out)) as w:
        return round(w.getnframes() / w.getframerate(), 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--voice", default="en-US-AndrewNeural")
    ap.add_argument("--rate", default="+0%")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    lines = json.loads((HERE / "ccnic_manifest.json").read_text())
    only = {k for k in args.only.split(",") if k}
    voice = json.loads(VOICE_JSON.read_text()) if VOICE_JSON.exists() else {}
    OUT.mkdir(parents=True, exist_ok=True)
    TAKES.mkdir(exist_ok=True)
    todo = []
    for ln in lines:
        if only and ln["key"] not in only:
            continue
        meta = TAKES / f"{ln['key']}.json"
        h = text_hash(args.voice, args.rate, ln["text"])
        fresh = meta.exists() and json.loads(meta.read_text()).get("hash") == h
        if fresh and not args.force and not only and (OUT / f"{ln['key']}.wav").exists():
            continue
        todo.append(ln)
    print(f"{len(todo)} of {len(lines)} lines to synthesize ({args.voice} {args.rate})", flush=True)

    with cf.ThreadPoolExecutor(args.jobs) as ex:
        durs = list(ex.map(lambda ln: synth(ln["key"], ln["text"], args.voice, args.rate), todo))

    try:
        from faster_whisper import WhisperModel
        asr = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=16) if todo else None
    except ImportError:
        asr = None
        print("faster_whisper not importable: skipping WER check", flush=True)

    for ln, dur in zip(todo, durs):
        key, score, hyp = ln["key"], None, ""
        if asr:
            segs, _ = asr.transcribe(decode(TAKES / f"{key}.mp3", 16000), language="en", beam_size=1)
            hyp = "".join(s.text for s in segs).strip()
            score = round(wer(ln["text"], hyp), 3)
        (TAKES / f"{key}.mp3").unlink()
        voice[key] = dur
        (TAKES / f"{key}.json").write_text(json.dumps(
            {"hash": text_hash(args.voice, args.rate, ln["text"]), "wer": score, "asr": hyp, "dur": dur},
            ensure_ascii=False))
        flag = "  <-- check" if score is not None and score > 0.25 else ""
        print(f"{key}  {dur:5.2f}s  WER {score}  {hyp}{flag}", flush=True)

    keys = {ln["key"] for ln in lines}
    voice = {k: v for k, v in sorted(voice.items()) if k in keys and (OUT / f"{k}.wav").exists()}
    VOICE_JSON.write_text(json.dumps(voice, indent=1) + "\n")
    missing = sorted(keys - voice.keys())
    print(f"voice.json: {len(voice)} clips" + (f"; missing {missing}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
