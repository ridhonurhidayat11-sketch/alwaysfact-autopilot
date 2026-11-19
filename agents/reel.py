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
VOICE = "id-ID-ArdiNeural"
LEAD, TAIL = 0.15, 0.45            # silence before and after each narrated line, in seconds
BACKGROUNDS = [RS.LAV, RS.WHITE, (255, 246, 190), (243, 213, 230), (214, 230, 255)]
MIN_BEATS, MAX_BEATS = 5, 8


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
    if not 45 <= words <= 150:
        errs.append(f"narration has {words} words; 45-150 fits a Reel of at most 60 seconds")
    titles = r.get("titles")
    if titles is not None and (not isinstance(titles, list) or len(titles) != 3 or not all(isinstance(x, str) and 8 <= len(x) <= 90 for x in titles)):
        errs.append("reel.titles must be 3 alternative titles of 8-90 characters")
    for i, b in enumerate(beats):
        if not isinstance(b, dict):
            continue
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


def speak(text, path, voice=VOICE, rate="+3%", tries=4):
    """Narrates `text` into an mp3 and returns [(start_s, end_s, word)]. Retries: the free service sometimes answers 403."""
    import time
    last = None
    for n in range(tries):
        try:
            return asyncio.run(_stream_tts(text, path, voice, rate))
        except Exception as e:  # noqa: BLE001 - any network or service error is retried
            last = e
            time.sleep(2 * (n + 1))
    raise RuntimeError(f"text-to-speech failed after {tries} tries: {last}")


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
ACTS = {"hook": "PERTANYAAN", "context": "KONTEKS", "stakes": "KENAPA PENTING", "evidence": "BUKTI",
        "reveal": "TERNYATA", "conclusion": "KESIMPULAN", "cta": ""}
CAMERAS = ("push_in", "pull_out", "pan_left", "pan_right", "zoom_to_detail", "static")
TRANSITIONS = ("wipe", "slide", "fade", "cut")
BIG = 1.25   # photos are fitted this much larger than the frame so the camera can move inside them


class Scene:
    def __init__(self, beat, words, dur, bg, photo=None, last=False, first=False, source=None, prev_bg=None, index=0):
        self.beat, self.words, self.dur, self.bg = beat, words, dur, bg
        self.photo, self.last, self.first, self.source, self.prev_bg, self.index = photo, last, first, source, prev_bg or bg, index
        self.chunks = chunk_words(words)
        name = beat.get("icon")
        self.icon = name if name in elements.ICONS else elements.pick_icon((beat.get("text") or "") + " " + (beat.get("say") or ""))
        self.camera = beat.get("camera") if beat.get("camera") in CAMERAS else ("push_in" if index % 2 == 0 else "pan_right")
        self.transition = beat.get("transition") if beat.get("transition") in TRANSITIONS else ("wipe" if index % 3 == 0 else ("slide" if index % 3 == 1 else "fade"))
        self.act = beat.get("act") if beat.get("act") in ACTS else ""
        self.photo_big = RS.photo_fit(photo[0], int(960 * BIG), int(760 * BIG), cy=0.5) if photo else None
        self.cta = RS.slide_cta({"title": beat.get("text") or beat.get("say", "")}, 0, 1).convert("RGB") if last else None


def camera_state(camera, p):
    """(zoom, centre x, centre y) of the camera at progress p (0..1) of the scene."""
    e = ease_in_out(p)
    if camera == "pull_out":
        return 1.3 - 0.3 * e, 0.5, 0.5
    if camera == "pan_left":
        return 1.3, 0.62 - 0.24 * e, 0.5
    if camera == "pan_right":
        return 1.3, 0.38 + 0.24 * e, 0.5
    if camera == "zoom_to_detail":
        return 1.0 + 0.7 * e, 0.5, 0.5 - 0.06 * e
    if camera == "static":
        return 1.0, 0.5, 0.5
    return 1.0 + 0.3 * e, 0.5, 0.5          # push_in


def camera_photo(sc, p, aw, ah):
    big = sc.photo_big
    z, cx, cy = camera_state(sc.camera, p)
    ww, hh = big.width / z, big.height / z
    x0 = clamp(cx * big.width - ww / 2, 0, big.width - ww)
    y0 = clamp(cy * big.height - hh / 2, 0, big.height - hh)
    return big.crop((int(x0), int(y0), int(x0 + ww), int(y0 + hh))).resize((aw, ah), Image.BICUBIC)


def _parallax(d, sc, p, global_t):
    """Three layers of flat shapes that drift at different speeds while the camera moves (depth without 3D)."""
    far, near = RS.LAV_D if sc.bg == RS.LAV else RS.LAV, RS.WHITE if sc.bg != RS.WHITE else RS.LAV
    drift = (p - 0.5)
    d.ellipse([740 - 140 * drift, -380, 1500 - 140 * drift, 360], fill=far)
    d.ellipse([-300 + 90 * drift, 1250, 760 + 90 * drift, 2300], fill=near)
    d.ellipse([820 + 260 * drift, 900, 1250 + 260 * drift, 1330], fill=RS.LAV if sc.bg != RS.LAV else RS.WHITE)


def draw_scene(sc, t, total_t, global_t):
    """One frame of a scene at local time t (seconds)."""
    p = clamp(t / max(0.1, sc.dur))
    img = Image.new("RGB", (W, H), sc.bg)
    d = ImageDraw.Draw(img)
    _parallax(d, sc, p, global_t)
    if sc.last:
        pop = ease_out_back(t / 0.5)
        cta = sc.cta
        w, h = int(cta.width * (0.6 + 0.4 * pop)), int(cta.height * (0.6 + 0.4 * pop))
        c = cta.resize((max(1, w), max(1, h)), Image.LANCZOS)
        img.paste(c, ((W - w) // 2, 330 + (cta.height - h) // 2))
        return _transition(sc, img, t)
    d.text((W // 2, 112), RS.HANDLE, font=RS.font("Bold", 40), fill=RS.INK, anchor="mm")
    if sc.act:
        label = ACTS[sc.act]
        lf = RS.font("Bold", 26)
        lw = int(d.textlength(label, font=lf)) + 44
        d.rounded_rectangle([(W - lw) // 2, 152, (W + lw) // 2, 200], radius=24, fill=RS.PURPLE)
        d.text((W // 2, 176), label, font=lf, fill=RS.WHITE, anchor="mm")
    # headline with a yellow marker wiping in behind it
    text = (sc.beat.get("text") or "").upper()
    size = 108 if sc.first else 86
    fnt = RS.font("Bold", size)
    lines = RS.wrap(d, text, fnt, W - 130)[:3]
    step = int(size * 1.16)
    y = 225
    wipe = ease_out(t / 0.4)
    for ln in lines:
        lw = d.textlength(ln, font=fnt)
        x0 = (W - lw) / 2
        d.rectangle([x0 - 16, y + int(size * 0.28), x0 - 16 + (lw + 32) * wipe, y + int(size * 1.05)], fill=RS.YELLOW)
        appear = ease_out_back((t - 0.05) / 0.35)
        if appear > 0.02:
            off = int((1 - clamp(appear, 0, 1)) * 40)
            d.text((W // 2, y + off), ln, font=fnt, fill=RS.INK, anchor="ma")
        y += step
    # the picture: pops in, then the camera moves
    ay0, aw, ah = 580, 960, 740
    pop = ease_out_back((t - 0.25) / 0.45)
    if pop > 0.02:
        prog = clamp((t - 0.45) / max(0.6, min(1.8, sc.dur * 0.55)))
        art = None
        if sc.beat.get("viz") and infographics.valid(sc.beat["viz"]):
            art = infographics.render(sc.beat["viz"], aw, ah, prog)
            z, cx, cy = camera_state(sc.camera, p)
            zoom = 1.0 + (z - 1.0) * 0.18
        elif sc.photo_big is not None:
            art = RS.rounded(camera_photo(sc, p, aw, ah), 40)
            zoom = 1.0
        else:   # nothing photographable and no chart: a large doodle carries the scene
            big = elements.sticker(sc.icon, 430)
            art = Image.new("RGBA", (aw, ah), (0, 0, 0, 0))
            art.paste(big, ((aw - big.width) // 2, (ah - big.height) // 2 + int(14 * math.sin(global_t * 2.2))), big)
            zoom = 1.0
        z0 = 0.55 + 0.45 * clamp(pop, 0, 1.15)
        nw, nh = max(2, int(aw * z0 * zoom)), max(2, int(ah * z0 * zoom))
        art = art.resize((nw, nh), Image.LANCZOS)
        img.paste(art, ((W - nw) // 2, ay0 + (ah - nh) // 2), art)
        if sc.photo and not sc.beat.get("viz"):
            RS.credit_chip(img, sc.photo[1], (W - nw) // 2 + 20, ay0 + (ah + nh) // 2 - 4)
    # source tag: where the fact in this scene comes from
    if sc.source and t > 0.8:
        d = ImageDraw.Draw(img)
        sf = RS.font("Medium", 25)
        label = "Sumber: " + sc.source[:44]
        sw = int(d.textlength(label, font=sf)) + 36
        sx = 60
        d.rounded_rectangle([sx, 1356, sx + sw, 1402], radius=23, fill=RS.WHITE, outline=RS.INK, width=2)
        d.text((sx + 18, 1379), label, font=sf, fill=RS.INK, anchor="lm")
    # elements: the topic sticker bobs at the corner of the panel, sparkles twinkle, Faktu greets in the first scene
    pop2 = ease_out_back((t - 0.55) / 0.4)
    if pop2 > 0.02:
        bob = math.sin(global_t * 2.4 + sc.dur) * 14
        z = clamp(pop2, 0, 1.15)
        if sc.beat.get("viz") or sc.photo_big is not None:
            st = elements.rotated(elements.sticker(sc.icon, int(170 * z) or 2), 8 * math.sin(global_t * 1.7))
            img.paste(st, (W - 50 - st.width, int(ay0 + ah - st.height // 2 - 6 + bob * 0.6)), st)
        for k, (sx, sy) in enumerate(((70, ay0 - 40), (W - 120, ay0 + ah + 20), (110, ay0 + ah - 10))):
            sz = int(30 + 30 * (0.5 + 0.5 * math.sin(global_t * 3 + k * 2)))
            sp = elements.sparkle(sz, (255, 214, 10) if k != 1 else (243, 150, 200))
            img.paste(sp, (sx - sp.width // 2, sy - sp.height // 2), sp)
    if sc.first:
        mp = ease_out_back((t - 0.2) / 0.45)
        if mp > 0.02:
            mk = elements.rotated(elements.mascot(max(2, int(200 * clamp(mp, 0, 1.15))), "wow"), -8 + 6 * math.sin(global_t * 2))
            img.paste(mk, (24, 26), mk)
    # captions, word by word
    ta = t - LEAD
    cur = None
    for ch in sc.chunks:
        if ch[0][0] - 0.05 <= ta:
            cur = ch
    if cur and ta >= 0:
        d = ImageDraw.Draw(img)
        cf = RS.font("Bold", 74)
        rows, row, rw = [], [], 0.0
        sp = d.textlength(" ", font=cf)
        for w in cur:
            ww = d.textlength(w[2], font=cf)
            if row and rw + sp + ww > W - 150:
                rows.append((row, rw))
                row, rw = [], 0.0
            rw += (sp if row else 0) + ww
            row.append((w, ww))
        rows.append((row, rw))
        y = 1450
        for row, rw in rows:
            x = (W - rw) / 2
            for w, ww in row:
                if w[0] - 0.02 <= ta < w[1] + 0.06:
                    d.rounded_rectangle([x - 12, y - 4, x + ww + 12, y + 92], radius=16, fill=RS.YELLOW)
                d.text((x, y), w[2], font=cf, fill=RS.INK)
                x += ww + sp
            y += 104
    return _transition(sc, img, t)


def _transition(sc, img, t):
    """How the scene arrives: wipe (purple bar), slide (rises from below), fade (from the last scene's colour), cut."""
    if sc.transition == "cut" or t >= 0.3:
        return img
    q = ease_in_out(t / 0.3)
    if sc.transition == "wipe":
        ImageDraw.Draw(img).rectangle([int(W * q), 0, W, H], fill=RS.PURPLE)
        return img
    base = Image.new("RGB", (W, H), sc.prev_bg)
    if sc.transition == "slide":
        base.paste(img, (0, int(H * (1 - q))))
        return base
    return Image.blend(base, img, q)


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


def build_scenes(post, workdir, tts=speak, photos=None):
    reel = post["reel"]
    beats = reel["beats"]
    claims = post.get("claims") or []
    photos = photos if photos is not None else {}
    scenes, voice_files = [], []
    for i, b in enumerate(beats):
        last = i == len(beats) - 1
        if i == 0 and not b.get("text"):
            b["text"] = reel.get("hook", "")
        say = b["say"].strip()
        mp3 = Path(workdir) / f"beat{i}.mp3"
        words = tts(say, str(mp3))
        dur = LEAD + audio_seconds(mp3) + TAIL
        voice_files.append((mp3, dur))
        src = None
        n = b.get("claim")
        if isinstance(n, int) and 1 <= n <= len(claims):
            src = claims[n - 1].get("source_name") or None
        scenes.append(Scene(b, words, dur, BACKGROUNDS[i % len(BACKGROUNDS)], photos.get(i), last=last, first=i == 0,
                            source=src, prev_bg=BACKGROUNDS[(i - 1) % len(BACKGROUNDS)] if i else RS.WHITE, index=i))
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
               "-i", str(wav), "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
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
