"""Synthesize Bar4PollingWalls Chinese narration with edge-tts (zh-CN-YunxiNeural +8%) and update src/bar4/voice.json.

  ~/tts/asr/bin/python tts/bar4_synth.py [--only B01_00,B01_01] [--force] [--jobs 4]
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
VOICE_JSON = ROOT / "src" / "bar4" / "voice.json"
SR_TTS = 24000
SR_OUT = 48000

_DIG = "零一二三四五六七八九"


def _int_zh(n: int) -> str:
    if n < 10:
        return _DIG[n]
    if n < 100:
        t, o = divmod(n, 10)
        return ("" if t == 1 else _DIG[t]) + "十" + (_DIG[o] if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        if not r:
            return _DIG[h] + "百"
        return _DIG[h] + "百" + ("零" if r < 10 else "") + _int_zh(r)
    return "".join(_DIG[int(c)] for c in str(n))


def digits_to_zh(s: str) -> str:
    def num(m: re.Match) -> str:
        raw, pct = m.group(1), m.group(2)
        if "." in raw:
            a, b = raw.split(".", 1)
            out = _int_zh(int(a)) + "点" + "".join(_DIG[int(c)] for c in b)
        else:
            out = _int_zh(int(raw))
        return ("百分之" + out) if pct else out

    return re.sub(r"(\d+(?:\.\d+)?)(%?)", num, s.replace(",", ""))


def norm_for_cer(s: str) -> str:
    return re.sub(r"[\W_]+", "", digits_to_zh(s).lower())


def cer(ref: str, hyp: str) -> float:
    a, b = norm_for_cer(ref), norm_for_cer(hyp)
    if not a:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1] / len(a)


def cap_pauses(x: np.ndarray, sr: int, floor_db=-42.0, max_gap=0.3, lead=0.05, tail=0.12) -> np.ndarray:
    """Trim leading/trailing silence and shorten silent stretches longer than max_gap."""
    hop = sr // 100
    n = len(x) // hop
    if n == 0:
        return x
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    voiced = 20 * np.log10(rms) > floor_db
    if not voiced.any():
        return x
    i0, i1 = int(np.argmax(voiced)), n - int(np.argmax(voiced[::-1]))
    x = x[i0 * hop : i1 * hop]
    voiced = voiced[i0:i1]
    max_s = int(max_gap * 100)
    parts, i = [], 0
    while i < len(voiced):
        j = i
        while j < len(voiced) and voiced[j] == voiced[i]:
            j += 1
        if voiced[i] or (j - i) <= max_s:
            parts.append(x[i * hop : j * hop])
        else:
            parts.append(x[i * hop : (i + max_s) * hop])
        i = j
    y = np.concatenate(parts) if parts else x
    return np.concatenate([np.zeros(int(lead * sr)), y, np.zeros(int(tail * sr))]).astype(np.float32)


def decode(src: Path, sr: int) -> np.ndarray:
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(pcm, dtype="<f4")


def text_hash(voice: str, rate: str, text: str) -> str:
    return hashlib.sha1("\x1f".join(("edge", voice, rate, text)).encode()).hexdigest()[:12]


def synth(key: str, text: str, voice: str, rate: str) -> float:
    mp3 = TAKES / f"{key}.mp3"
    r = None
    for attempt in range(8):
        r = subprocess.run(
            ["uvx", "edge-tts", "--voice", voice, f"--rate={rate}", "--text", text, "--write-media", str(mp3)],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and mp3.exists() and mp3.stat().st_size > 0:
            break
        time.sleep(2 + 3 * attempt)
    else:
        raise RuntimeError(f"edge-tts failed for {key}: {r.stderr[-400:] if r else ''}")
    y = cap_pauses(decode(mp3, SR_TTS), SR_TTS)
    out = OUT / f"{key}.wav"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR_TTS), "-ac", "1", "-i", "-",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(SR_OUT), "-ac", "1", str(out),
        ],
        input=y.tobytes(),
        check=True,
    )
    with wave.open(str(out)) as w:
        return round(w.getnframes() / w.getframerate(), 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--voice", default="zh-CN-YunxiNeural")
    ap.add_argument("--rate", default="+14%")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    lines = json.loads((HERE / "bar4_manifest.json").read_text())
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
        print("faster_whisper not importable: skipping CER check", flush=True)

    for ln, dur in zip(todo, durs):
        key, score, hyp = ln["key"], None, ""
        if asr:
            segs, _ = asr.transcribe(
                decode(TAKES / f"{key}.mp3", 16000),
                language="zh",
                beam_size=1,
                initial_prompt="以下是普通话的句子。",
            )
            hyp = "".join(s.text for s in segs)
            score = round(cer(ln["text"], hyp), 3)
        (TAKES / f"{key}.mp3").unlink()
        voice[key] = dur
        (TAKES / f"{key}.json").write_text(
            json.dumps(
                {"hash": text_hash(args.voice, args.rate, ln["text"]), "cer": score, "asr": hyp, "dur": dur},
                ensure_ascii=False,
            )
        )
        flag = "  <-- check" if score is not None and score > 0.25 else ""
        print(f"{key}  {dur:5.2f}s  CER {score}  {hyp}{flag}", flush=True)

    keys = {ln["key"] for ln in lines}
    voice = {k: v for k, v in sorted(voice.items()) if k in keys and (OUT / f"{k}.wav").exists()}
    VOICE_JSON.write_text(json.dumps(voice, indent=1) + "\n")
    missing = sorted(keys - voice.keys())
    print(f"voice.json: {len(voice)} clips" + (f"; missing {missing}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
