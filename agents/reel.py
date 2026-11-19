"""Reel renderer: a Vox-style animated explainer (flat colour, bold type, pop-in graphics, word-synced captions,
Indonesian voice-over, original background music). 1080x1920, 30 fps, H.264 + AAC. No AI, no per-use cost.

Post JSON:  "format": "reel", "reel": {
    "hook": "short on-screen hook, max 60 chars",
    "mood": "curious | calm | energetic",
    "beats": [ {"say": "one narrated sentence (Indonesian)", "text": "2-5 word on-screen headline",
                "viz": {...infographics spec...}  OR  "image_query": "English words for a real photo"} ... ] }
The first beat is the hook, the last beat is the call to action (drawn by code). 5-8 beats, about 25-45 seconds.

Voice: edge-tts (Indonesian neural voice, word timestamps). Music: synthesised here with numpy, so it is ours and
cannot trigger a copyright claim. Video: frames drawn with Pillow and piped into ffmpeg.
"""
import asyncio
import math
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import elements  # noqa: E402
import images  # noqa: E402
import infographics  # noqa: E402
import render_slides as RS  # noqa: E402

W, H, FPS = 1080, 1920, 30
VOICE = "id-ID-ArdiNeural"  # default voice; reel.voice may be "ardi" or "gadis"
LEAD, TAIL = 0.15, 0.45            # silence before and after each narrated line, in seconds
BACKGROUNDS = [(11, 19, 34), (244, 237, 223)]  # navy and warm off-white (kept for older callers)
MIN_BEATS, MAX_BEATS = 5, 10


# ----------------------------------------------------------------------------------------------- validation
def validate_reel(post):
    """Problems with a reel proposal (empty list = fine). Used by the referee next to the fact checks."""
    r = post.get("reel")
    if not isinstance(r, dict):
        return ["reel missing"]
    errs = []
    if not (8 <= len((r.get("hook") or "").strip()) <= 70):
        errs.append("reel.hook must be 8-70 characters")
    beats = r.get("beats")
    if not isinstance(beats, list) or not MIN_BEATS <= len(beats) <= MAX_BEATS:
        return errs + [f"reel needs {MIN_BEATS}-{MAX_BEATS} beats"]
    words = 0
    for i, b in enumerate(beats):
        say = (b.get("say") or "").strip() if isinstance(b, dict) else ""
        if not 12 <= len(say) <= 190:
            errs.append(f"beat {i + 1}: say must be 12-190 characters")
        words += len(say.split())
        if isinstance(b, dict) and b.get("viz") and not infographics.valid(b["viz"]):
            errs.append(f"beat {i + 1}: viz is not drawable")
    est = words / 2.2 + 0.6 * len(beats)   # spoken seconds: about 2.2 words a second plus a pause around each beat
    if words < 45 or est > 62:
        errs.append(f"narration has {words} words (about {est:.0f} s); a Reel must be 45+ words and at most 60 seconds: tighten it")
    titles = r.get("titles")
    if titles is not None and (not isinstance(titles, list) or len(titles) != 3 or not all(isinstance(x, str) and 8 <= len(x) <= 90 for x in titles)):
        errs.append("reel.titles must be 3 alternative titles of 8-90 characters")
    if r.get("voice") is not None and r["voice"] not in VOICES:
        errs.append(f"reel.voice must be one of {sorted(VOICES)}")
    for i, b in enumerate(beats):
        if not isinstance(b, dict):
            continue
        if b.get("delivery") is not None and b["delivery"] not in DELIVERY:
            errs.append(f"beat {i + 1}: delivery must be one of {sorted(DELIVERY)}")
        if b.get("act") is not None and b["act"] not in ACTS:
            errs.append(f"beat {i + 1}: act must be one of {sorted(ACTS)}")
        if b.get("camera") is not None and b["camera"] not in CAMERAS:
            errs.append(f"beat {i + 1}: camera must be one of {list(CAMERAS)}")
        if b.get("transition") is not None and b["transition"] not in TRANSITIONS:
            errs.append(f"beat {i + 1}: transition must be one of {list(TRANSITIONS)}")
        nclaims = len(post.get("claims") or [])
        if b.get("claim") is not None and not (isinstance(b["claim"], int) and 1 <= b["claim"] <= max(nclaims, 1)):
            errs.append(f"beat {i + 1}: claim must be the number of one of the claims")
    return errs


# ----------------------------------------------------------------------------------------------- tools
def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RuntimeError("ffmpeg not found (apt install ffmpeg, or pip install imageio-ffmpeg)")


def audio_seconds(path):
    out = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if not m:
        raise RuntimeError(f"cannot read the duration of {path}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


async def _stream_tts(text, path, voice, rate):
    import edge_tts
    com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words = []
    with open(path, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                start = ch["offset"] / 1e7
                words.append((start, start + ch["duration"] / 1e7, ch["text"]))
    if not words or Path(path).stat().st_size < 1000:
        raise RuntimeError("the voice service returned no audio")
    return words


SENTENCES = re.compile(r"(?<=[.?!])\s+")
VOICES = {"ardi": "id-ID-ArdiNeural", "gadis": "id-ID-GadisNeural"}
DELIVERY = {"serius": (-5, -3), "antusias": (6, 4), "santai": (2, 0), "tanya": (0, 5)}   # (rate %, pitch Hz) offsets


def split_sentences(text):
    return [x.strip() for x in SENTENCES.split(text.strip()) if x.strip()]


def prosody(sentence, first, last, delivery=None, bump=0):
    """(rate, pitch) for one sentence, so the voice moves like a person telling a story instead of reading a list:
    questions rise, exclamations push, the last sentence of a beat settles down, the first one opens a little brighter."""
    rate, pitch = 4, 0
    if sentence.rstrip().endswith("?"):
        rate, pitch = 0, 6
    elif sentence.rstrip().endswith("!"):
        rate, pitch = 8, 5
    if first and not sentence.rstrip().endswith("?"):
        pitch += 1
    if last:
        rate -= 3
        pitch -= 2
    dr, dp = DELIVERY.get(delivery, (0, 0))
    return f"{rate + dr + bump:+d}%", f"{pitch + dp:+d}Hz"


def gap_after(sentence):
    """Silence after a sentence in seconds: a question gets room to land."""
    return 0.34 if sentence.rstrip().endswith("?") else 0.2


async def _stream_tts(text, path, voice, rate, pitch="+0Hz"):
    import edge_tts
    com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    words = []
    with open(path, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                start = ch["offset"] / 1e7
                words.append((start, start + ch["duration"] / 1e7, ch["text"]))
    if not words or Path(path).stat().st_size < 1000:
        raise RuntimeError("the voice service returned no audio")
    return words


def _speak_one(text, path, voice, rate, pitch, tries=4):
    import time
    last = None
    for n in range(tries):
        try:
            return asyncio.run(_stream_tts(text, path, voice, rate, pitch))
        except Exception as e:  # noqa: BLE001 - any network or service error is retried
            last = e
            time.sleep(2 * (n + 1))
    raise RuntimeError(f"text-to-speech failed after {tries} tries: {last}")


def speak(text, path, rate=None, voice="ardi", delivery=None):
    """Narrates `text` into one mp3, sentence by sentence with its own intonation and natural pauses, and returns
    [(start_s, end_s, word)] for the whole beat. `rate` is an extra speed-up like "+12%" (used to fit a minute)."""
    voice = VOICES.get(voice, voice if voice and "Neural" in voice else VOICES["ardi"])
    bump = int(str(rate).replace("%", "")) if rate else 0
    sents = split_sentences(text) or [text]
    parts, words, offset = [], [], 0.0
    base = Path(path)
    for k, snt in enumerate(sents):
        r, pch = prosody(snt, k == 0, k == len(sents) - 1, delivery, bump)
        mp3 = base.with_name(f"{base.stem}-s{k}.mp3")
        w = _speak_one(snt, str(mp3), voice, r, pch)
        dur = audio_seconds(mp3)
        words += [(a + offset, b + offset, t) for a, b, t in w]
        gap = gap_after(snt) if k < len(sents) - 1 else 0.0
        parts.append((mp3, gap))
        offset += dur + gap
    if len(parts) == 1:
        shutil.copyfile(parts[0][0], path)
        return words
    cmd = [ffmpeg_exe(), "-y"]
    for mp3, _ in parts:
        cmd += ["-i", str(mp3)]
    chain = ";".join(f"[{k}:a]aresample=24000,apad=pad_dur={g:.2f}[a{k}]" for k, (_, g) in enumerate(parts))
    cmd += ["-filter_complex", chain + ";" + "".join(f"[a{k}]" for k in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[out]",
            "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "3", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("could not join the sentences: " + r.stderr[-300:])
    return words


# ----------------------------------------------------------------------------------------------- music
def make_music(seconds, mood, path, sr=44100):
    """Original background music: soft chord pad + plucked arpeggio (+ a gentle kick unless calm). Mono 16-bit wav."""
    import numpy as np
    bpm = {"curious": 96, "calm": 76, "energetic": 116}.get(mood, 96)
    prog = {"calm": [(48, 52, 55), (53, 57, 60), (55, 59, 62), (52, 55, 59)]}.get(
        mood, [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)])
    beat = 60.0 / bpm
    bar = beat * 4
    n = int(seconds * sr)
    out = np.zeros(n)

    def hz(m):
        return 440.0 * 2 ** ((m - 69) / 12.0)

    for k in range(int(seconds / bar) + 2):
        t0, ch = k * bar, prog[k % len(prog)]
        i0 = int(t0 * sr)
        if i0 >= n:
            break
        i1 = min(n, int((t0 + bar) * sr))
        seg = np.arange(i1 - i0) / sr
        env = np.minimum(1, seg / 0.3) * np.minimum(1, np.maximum(0, (bar - seg)) / 0.5)
        for m in ch:
            out[i0:i1] += env * 0.09 * (np.sin(2 * np.pi * hz(m) * seg) + 0.25 * np.sin(4 * np.pi * hz(m) * seg))
        for j in range(8):
            ii = int((t0 + j * beat / 2) * sr)
            if ii >= n:
                break
            ln = min(n - ii, int(0.4 * sr))
            s = np.arange(ln) / sr
            out[ii:ii + ln] += 0.09 * np.exp(-s * 8) * np.sin(2 * np.pi * hz(ch[j % 3] + 12) * s)
        if mood != "calm":
            for b in (0, 2):
                ii = int((t0 + b * beat) * sr)
                if ii >= n:
                    break
                ln = min(n - ii, int(0.25 * sr))
                s = np.arange(ln) / sr
                out[ii:ii + ln] += 0.22 * np.exp(-s * 14) * np.sin(2 * np.pi * (50 + 60 * np.exp(-s * 30)) * s)
    fade = int(1.2 * sr)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    out = out / (np.abs(out).max() or 1) * 0.85
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((out * 32767).astype("<i2").tobytes())


# ----------------------------------------------------------------------------------------------- animation helpers
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out_back(x, k=1.70158):
    x = clamp(x)
    return 1 + (k + 1) * (x - 1) ** 3 + k * (x - 1) ** 2


def ease_in_out(x):
    x = clamp(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out(x):
    return 1 - (1 - clamp(x)) ** 3


def chunk_words(words, size=4):
    """Caption chunks: up to `size` words, ending early at punctuation. Each chunk = list of (start, end, word)."""
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= size or re.search(r"[,.?!]$", w[2]):
            chunks.append(cur)
            cur = []
    return chunks + ([cur] if cur else [])


# ----------------------------------------------------------------------------------------------- scenes
from vox import ACTS, CAMERAS, TRANSITIONS, Scene, draw_scene  # noqa: E402,F401  (the editorial design system lives in vox.py)


# ----------------------------------------------------------------------------------------------- assembly
def gather_beat_photos(beats):
    """A real photo per beat that asks for one (`image_query`). {beat index: (bytes, credit)}"""
    out, taken = {}, set()
    for i, b in enumerate(beats):
        q = b.get("image_query")
        if not q or b.get("viz"):
            continue
        color, grey = [], []
        for c in images.find_many(q, 4, "commons", pool=8):
            if c["url"] in taken:
                continue
            RS._download(c, color, grey)
            if color:
                break
        pick = (color or grey or [None])[0]
        if pick:
            out[i] = (pick[0], pick[1])
            taken.add(pick[2]["url"])
    return out


def _speak_all(beats, workdir, tts, rate, voice="ardi"):
    out = []
    for i, b in enumerate(beats):
        mp3 = Path(workdir) / f"beat{i}.mp3"
        say = b["say"].strip()
        try:
            words = tts(say, str(mp3), rate, voice, b.get("delivery"))
        except TypeError:   # a stand-in voice with the older (text, path) signature
            words = tts(say, str(mp3))
        out.append((mp3, words, LEAD + audio_seconds(mp3) + TAIL))
    return out


def build_scenes(post, workdir, tts=speak, photos=None, max_seconds=59.0):
    reel = post["reel"]
    beats = reel["beats"]
    claims = post.get("claims") or []
    verified = {v.get("claim"): v for v in (post.get("verification") or []) if isinstance(v, dict) and v.get("evidence_url") and v.get("quote")}
    photos = photos if photos is not None else {}
    if beats and not beats[0].get("text"):
        beats[0]["text"] = reel.get("hook", "")
    voice = reel.get("voice") or "ardi"
    spoken = _speak_all(beats, workdir, tts, None, voice)
    for faster in ("+8%", "+16%"):    # a Reel never runs past a minute: speak a little faster, step by step
        if sum(d for _, _, d in spoken) <= max_seconds:
            break
        spoken = _speak_all(beats, workdir, tts, faster, voice)
    scenes, voice_files = [], []
    for i, (b, (mp3, words, dur)) in enumerate(zip(beats, spoken)):
        voice_files.append((mp3, dur))
        n = b.get("claim")
        src = quote = None
        if isinstance(n, int) and 1 <= n <= len(claims):
            src = claims[n - 1].get("source_name") or None
            quote = (verified.get(n) or {}).get("quote")
        prev_bg = scenes[-1].th["bg"] if scenes else None
        scenes.append(Scene(b, words, dur, None, photos.get(i), last=i == len(beats) - 1, first=i == 0, source=src,
                            prev_bg=prev_bg, index=i, total=len(beats), quote=quote, outlet=src))
    return scenes, voice_files


def mix_audio(voice_files, music_wav, out_m4a_wav, voice_gain=1.0, music_gain=0.16):
    """Voice (each line padded to its scene length) + music under it -> one wav."""
    exe = ffmpeg_exe()
    cmd = [exe, "-y"]
    for mp3, _ in voice_files:
        cmd += ["-i", str(mp3)]
    cmd += ["-i", str(music_wav)]
    parts, labels = [], []
    for k, (_, dur) in enumerate(voice_files):
        parts.append(f"[{k}:a]adelay={int(LEAD * 1000)}|{int(LEAD * 1000)},apad=whole_dur={dur:.3f},aresample=48000[a{k}]")
        labels.append(f"[a{k}]")
    n = len(voice_files)
    parts.append("".join(labels) + f"concat=n={n}:v=0:a=1,volume={voice_gain}[voice]")
    parts.append(f"[{n}:a]aresample=48000,volume={music_gain}[mus]")
    parts.append("[voice][mus]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[out]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-ac", "2", "-ar", "48000", str(out_m4a_wav)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("audio mix failed: " + r.stderr[-400:])


def render_reel(post, out_mp4, tts=speak, photos=None, fps=FPS, music=True, progress=None):
    """Renders the whole Reel to `out_mp4`. Returns {"seconds", "scenes", "thumb_offset_ms"}."""
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        scenes, voice_files = build_scenes(post, tmp, tts, photos)
        total = sum(sc.dur for sc in scenes)
        wav = tmp / "mix.wav"
        mus = tmp / "music.wav"
        make_music(total + 1, post["reel"].get("mood", "curious"), mus) if music else None
        if music:
            mix_audio(voice_files, mus, wav)
        else:
            mix_audio(voice_files, voice_files[0][0], wav, music_gain=0.0)
        cmd = [ffmpeg_exe(), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
               "-i", str(wav), "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-maxrate", "7M", "-bufsize", "14M", "-pix_fmt", "yuv420p",
               "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest",
               "-movflags", "+faststart", str(out_mp4)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        t0, frames = 0.0, 0
        try:
            for sc in scenes:
                n = int(round(sc.dur * fps))
                for f in range(n):
                    t = f / fps
                    proc.stdin.write(draw_scene(sc, t, total, t0 + t).tobytes())
                    frames += 1
                t0 += sc.dur
                if progress:
                    progress(t0 / total)
            proc.stdin.close()
            err = proc.stderr.read().decode("utf-8", "replace")
            if proc.wait():
                raise RuntimeError("video encode failed: " + err[-400:])
        finally:
            if proc.poll() is None:
                proc.kill()
    return {"seconds": round(total, 1), "scenes": len(scenes), "thumb_offset_ms": 1200,
            "timings": [round(sc.dur, 2) for sc in scenes]}


def cover_frame(post, tts_words=None):
    """A still of the hook scene (used as a cover image if needed)."""
    b = post["reel"]["beats"][0]
    sc = Scene(b, tts_words or [(0.1, 0.4, "Hook")], 4.0, BACKGROUNDS[0], first=True)
    return draw_scene(sc, 1.6, 4.0, 1.6)
