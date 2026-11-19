"""Editorial motion design for Reels, in the manner of visual-journalism explainers (not a copy of any brand).

Design system
  palette    deep navy / warm off-white as grounds, vibrant red as the one accent, orange or yellow for emphasis,
             muted blue and grey for support. No gradients except photo scrims, no cards, no stickers, no mascots.
  type       heavy uppercase sans headlines (Poppins Bold thickened with a stroke), huge numerals, small spaced kickers.
  layout     left-aligned, asymmetric, a lot of negative space, hairline rules, a ghost numeral drifting in the background.
  motion     mask reveals (text rises out of an invisible band), line drawing, count-up numbers, camera push / pull / pan
             on duotone photographs, an accent bar that wipes between scenes, parallax. Ease-in-out only: nothing bounces.
  scenes     photo (archival duotone), map (animated zoom on real coastlines), data (numbers, bars, people, steps,
             timeline), clip (the VERIFIED source sentence shown like a newspaper clipping), kinetic type, end card.
Every scene is chosen from what the beat explains, never for decoration.
"""
import io
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

import render_slides as RS

W, H = 1080, 1920
ML, MR = 84, 120                      # margins; the right one is wide because Instagram puts its buttons there
CW = W - ML - MR
NAVY, PAPER, BEIGE = (11, 19, 34), (244, 237, 223), (226, 213, 187)
RED, ORANGE, YELLOW = (229, 53, 45), (244, 127, 33), (250, 196, 36)
BLUE, STEEL, GREY = (92, 121, 160), (40, 58, 88), (150, 157, 170)

ACTS = {"hook": "PERTANYAAN", "context": "KONTEKS", "stakes": "KENAPA PENTING", "evidence": "BUKTI",
        "reveal": "TERNYATA", "conclusion": "KESIMPULAN", "cta": ""}
CAMERAS = ("push_in", "pull_out", "pan_left", "pan_right", "zoom_to_detail", "static")
TRANSITIONS = ("wipe", "slide", "fade", "cut")
LEAD = 0.15
ASSETS = Path(__file__).resolve().parent.parent / "assets"


# ----------------------------------------------------------------------------------------------- small helpers
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_in_out(x):
    x = clamp(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out(x):
    return 1 - (1 - clamp(x)) ** 3


def lerp(a, b, t):
    return a + (b - a) * t


def theme(dark):
    return {"dark": dark, "bg": NAVY if dark else PAPER, "fg": PAPER if dark else NAVY,
            "sub": (168, 175, 189) if dark else (92, 98, 112), "rule": (50, 66, 96) if dark else (206, 194, 169),
            "acc": RED, "acc2": YELLOW if dark else ORANGE, "panel": (22, 35, 58) if dark else BEIGE}


def font(size, weight="Bold"):
    return RS.font(weight, size)


def heavy(d, xy, text, size, fill, anchor="la", weight="Bold"):
    """Poppins Bold thickened with a same-colour stroke: the 'extra heavy' look of editorial headlines."""
    d.text(xy, text, font=font(size, weight), fill=fill, anchor=anchor,
           stroke_width=max(1, size // 40) if weight == "Bold" else 0, stroke_fill=fill)


def spaced(d, xy, text, size, fill, spacing=4, weight="Medium"):
    x, y = xy
    f = font(size, weight)
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + spacing
    return x


def parse_words(text):
    """'Wajah *paus* miring' -> [('WAJAH', False), ('PAUS', True), ...]. Asterisks mark the accent words."""
    out, acc = [], False
    for part in re.split(r"(\*)", text or ""):
        if part == "*":
            acc = not acc
        else:
            toks = part.split()
            if toks and out and not part[:1].isspace() and toks[0][:1] in ",.;:!?)":
                out[-1] = (out[-1][0] + toks.pop(0), out[-1][1])   # punctuation sticks to the word before it
            out += [(w.upper(), acc) for w in toks]
    return out


def wrap_words(d, words, size, max_w):
    f = font(size)
    sp = d.textlength(" ", font=f) + 4
    lines, cur, cw = [], [], 0.0
    for w, a in words:
        ww = d.textlength(w, font=f) + size // 20
        if cur and cw + sp + ww > max_w:
            lines.append(cur)
            cur, cw = [], 0.0
        cw += (sp if cur else 0) + ww
        cur.append((w, a))
    return lines + ([cur] if cur else [])


def fit_headline(d, words, start, max_w, max_lines, minimum=64):
    size = start
    while size > minimum:
        lines = wrap_words(d, words, size, max_w)
        if len(lines) <= max_lines:
            return size, lines
        size -= 6
    return minimum, wrap_words(d, words, minimum, max_w)


def reveal_lines(img, lines, x, y, size, th, t, t0=0.08, stagger=0.13, leading=1.04, accent=None):
    """Each line rises out of an invisible band (a mask reveal). Returns the y below the last line."""
    bh = int(size * 1.18)
    f = font(size)
    d0 = ImageDraw.Draw(img)
    sp = d0.textlength(" ", font=f) + 4
    for k, line in enumerate(lines):
        e = ease_out((t - t0 - k * stagger) / 0.55)
        yy = y + int(k * size * leading)
        if e > 0.001:
            band = Image.new("RGBA", (CW + 40, bh), (0, 0, 0, 0))
            bd = ImageDraw.Draw(band)
            cx, off = 0.0, int((1 - e) * bh * 0.95)
            for w, a in line:
                heavy(bd, (cx, off), w, size, th["acc"] if (a and accent is None) else (accent if a else th["fg"]))
                cx += bd.textlength(w, font=f) + sp
            img.paste(band, (x, yy), band)
    return y + int(len(lines) * size * leading)


def hline(d, x0, x1, y, color, p, width=3):
    """A line that draws itself from left to right."""
    p = clamp(p)
    if p > 0:
        d.line([x0, y, x0 + (x1 - x0) * p, y], fill=color, width=width)


def vline(d, x, y0, y1, color, p, width=3):
    p = clamp(p)
    if p > 0:
        d.line([x, y0, x, y0 + (y1 - y0) * p], fill=color, width=width)


# ----------------------------------------------------------------------------------------------- photo and camera
def duotone(data, w, h):
    """Archival look: greyscale mapped onto navy shadows and warm paper highlights."""
    im = Image.open(io.BytesIO(data)).convert("L")
    im = ImageOps.fit(im, (w, h), method=Image.LANCZOS, centering=(0.5, 0.45))
    im = ImageOps.autocontrast(im, cutoff=1)
    return ImageOps.colorize(im, black=(7, 12, 24), mid=(92, 108, 134), white=(241, 233, 216), midpoint=110)


def camera_state(camera, p):
    e = ease_in_out(p)
    if camera == "pull_out":
        return 1.3 - 0.3 * e, 0.5, 0.5
    if camera == "pan_left":
        return 1.18, 0.62 - 0.24 * e, 0.5
    if camera == "pan_right":
        return 1.18, 0.38 + 0.24 * e, 0.5
    if camera == "zoom_to_detail":
        return 1.0 + 0.7 * e, 0.5, 0.5 - 0.05 * e
    if camera == "static":
        return 1.0, 0.5, 0.5
    return 1.0 + 0.3 * e, 0.5, 0.5


def camera_crop(big, camera, p, out_w=W, out_h=H):
    z, cx, cy = camera_state(camera, p)
    ww, hh = out_w / z, out_h / z
    x0 = clamp(cx * big.width - ww / 2, 0, big.width - ww)
    y0 = clamp(cy * big.height - hh / 2, 0, big.height - hh)
    return big.crop((int(x0), int(y0), int(x0 + ww), int(y0 + hh))).resize((out_w, out_h), Image.BICUBIC)


def camera_whole(img, camera, p):
    """A slow camera move over a finished composition (zoom or sideways drift)."""
    if camera in ("static", None):
        return img
    z, cx, _ = camera_state(camera, p)
    if camera in ("push_in", "zoom_to_detail"):
        z = 1.0 + (z - 1.0) * 0.16
    elif camera == "pull_out":
        z = 1.0 + (z - 1.0) * 0.16
    else:
        z = 1.05
    ww, hh = W / z, H / z
    x0 = clamp((cx - 0.5) * W * 0.12 + (W - ww) / 2, 0, W - ww)
    y0 = (H - hh) / 2
    return img.crop((int(x0), int(y0), int(x0 + ww), int(y0 + hh))).resize((W, H), Image.BICUBIC)


_GRAIN = None
_VIGNETTE = None
_SCRIM = {}


def _grain_layers():
    global _GRAIN, _VIGNETTE
    if _GRAIN is None:
        try:
            import numpy as np
            rng = np.random.default_rng(7)
            n = rng.integers(0, 255, (H + 90, W + 90), dtype=np.uint8)
            g = Image.fromarray(n, "L")
            _GRAIN = Image.merge("RGBA", (g, g, g, Image.new("L", g.size, 15)))
            yy, xx = np.mgrid[0:H, 0:W]
            r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
            a = np.clip((r - 0.72) * 150, 0, 120).astype(np.uint8)
            v = Image.fromarray(a, "L")
            _VIGNETTE = Image.merge("RGBA", (Image.new("L", (W, H), 4), Image.new("L", (W, H), 8), Image.new("L", (W, H), 18), v))
        except Exception:   # numpy missing: skip the film texture, the design still works
            _GRAIN = False
            _VIGNETTE = False
    return _GRAIN, _VIGNETTE


def finish(img, gt, dark=True):
    grain, vig = _grain_layers()
    out = img.convert("RGBA")
    if vig and dark:
        out = Image.alpha_composite(out, vig)
    if grain:   # one fixed texture: a grain that changes every frame cannot be compressed (a 60-second file became 220 MB)
        out = Image.alpha_composite(out, grain.crop((20, 30, 20 + W, 30 + H)))
    return out.convert("RGB")


def scrim(y0, y1, a0, a1):
    key = (y0, y1, a0, a1)
    if key not in _SCRIM:
        ramp = Image.linear_gradient("L").resize((W, y1 - y0))
        mask = ramp.point(lambda v: int(a0 + (a1 - a0) * v / 255))
        lay = Image.new("RGBA", (W, y1 - y0), NAVY + (0,))
        lay.putalpha(mask)
        _SCRIM[key] = lay
    return _SCRIM[key]


# ----------------------------------------------------------------------------------------------- scene model
class Scene:
    def __init__(self, beat, words, dur, bg=None, photo=None, last=False, first=False, source=None, prev_bg=None,
                 index=0, total=1, quote=None, outlet=None):
        self.beat, self.words, self.dur = beat, words, max(0.5, dur)
        self.photo, self.last, self.first, self.source = photo, last, first, source
        self.index, self.total, self.quote, self.outlet = index, total, quote, outlet
        self.chunks = _chunks(words)
        viz = beat.get("viz") if isinstance(beat.get("viz"), dict) else None
        self.viz = viz
        if last:
            self.layout = "cta"
        elif viz and viz.get("kind") == "map":
            self.layout = "map"
        elif beat.get("clip") and quote:
            self.layout = "clip"
        elif viz:
            self.layout = "data"
        elif photo:
            self.layout = "photo"
        else:
            self.layout = "kinetic"
        dark = {"data": index % 2 == 0, "clip": False}.get(self.layout, True)
        if beat.get("theme") in ("dark", "light"):
            dark = beat["theme"] == "dark"
        self.th = theme(dark)
        cam = beat.get("camera")
        self.camera = cam if cam in CAMERAS else ("push_in" if index % 2 == 0 else "pan_right")
        tr = beat.get("transition")
        self.transition = tr if tr in TRANSITIONS else ("wipe", "slide", "fade")[index % 3]
        self.act = beat.get("act") if beat.get("act") in ACTS else ""
        self.prev_bg = prev_bg or NAVY
        self.photo_big = duotone(photo[0], int(W * 1.35), int(H * 1.35)) if (photo and self.layout == "photo") else None


def _chunks(words, size=5):
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= size or re.search(r"[,.?!]$", w[2]):
            chunks.append(cur)
            cur = []
    return chunks + ([cur] if cur else [])


# ----------------------------------------------------------------------------------------------- data drawings
def _person(d, cx, cy, s, fill):
    r = s * 0.17
    d.ellipse([cx - r, cy - s * 0.42 - r, cx + r, cy - s * 0.42 + r], fill=fill)
    d.rounded_rectangle([cx - s * 0.23, cy - s * 0.18, cx + s * 0.23, cy + s * 0.42], radius=int(s * 0.14), fill=fill)


def viz_stat(img, spec, rect, th, p):
    x, y, w, h = rect
    d = ImageDraw.Draw(img)
    value = str(spec.get("value", ""))
    m = re.match(r"^\s*(\d[\d.,]*)(.*)$", value)
    shown = value
    if m and not re.search(r"\d", m.group(2)):
        raw = m.group(1)
        num = float(raw.replace(",", ".")) if raw.count(",") + raw.count(".") <= 1 else None
        if num is not None:
            dec = len(raw.replace(",", ".").split(".")[1]) if "." in raw.replace(",", ".") else 0
            shown = f"{num * ease_in_out(p * 1.4):.{dec}f}".replace(".", "," if "," in raw else ".") + m.group(2)
    size = 330
    while size > 90 and d.textlength(shown if len(shown) > len(value) else value, font=font(size)) > w:
        size -= 10
    heavy(d, (x - 6, y), shown, size, th["fg"])
    wnum = d.textlength(value, font=font(size))
    hline(d, x, x + wnum, y + int(size * 1.22), th["acc"], ease_in_out((p - 0.2) / 0.5), 14)
    if p > 0.35:
        yy = y + int(size * 1.22) + 36
        for ln in RS.wrap(d, spec.get("label", ""), font(52, "Medium"), w)[:2]:
            d.text((x, yy), ln, font=font(52, "Medium"), fill=th["fg"])
            yy += 64
        if p > 0.6 and spec.get("note"):
            for ln in RS.wrap(d, spec["note"], font(36, "Regular"), w)[:2]:
                d.text((x, yy + 8), ln, font=font(36, "Regular"), fill=th["sub"])
                yy += 46


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def viz_compare(img, spec, rect, th, p):
    x, y, w, h = rect
    d = ImageDraw.Draw(img)
    items = [i for i in spec.get("items", []) if _num(i.get("value")) is not None][:4]
    top = max(_num(i["value"]) for i in items) or 1
    unit = spec.get("unit", "")
    row = min(330, h / max(1, len(items)))
    d.line([x, y, x, y + row * len(items) - 10], fill=th["rule"], width=3)
    for k, it in enumerate(items):
        yy = y + k * row + 6
        e = ease_in_out((p - k * 0.12) / 0.6)
        d.text((x + 28, yy), str(it.get("label", "")).upper(), font=font(36, "Medium"), fill=th["sub"])
        bw = (w - 330) * _num(it["value"]) / top * e
        col = th["acc"] if _num(it["value"]) == top else th["fg"]
        if bw > 2:
            d.rectangle([x, yy + 64, x + bw, yy + 64 + 96], fill=col)
        if e > 0.3:
            heavy(d, (x + bw + 26, yy + 64), f"{it['value']}{' ' + unit if unit else ''}", 70, col)
    if spec.get("caption") and p > 0.6:
        d.text((x + 28, y + row * len(items) + 6), spec["caption"], font=font(32, "Regular"), fill=th["sub"])


def viz_share(img, spec, rect, th, p):
    x, y, w, h = rect
    d = ImageDraw.Draw(img)
    items = [i for i in spec.get("items", []) if _num(i.get("pct")) is not None][:5]
    total = sum(_num(i["pct"]) for i in items) or 1
    cols = [th["acc"], th["fg"], BLUE, th["acc2"], GREY]
    cx = x
    e = ease_in_out(p / 0.7)
    for k, it in enumerate(items):
        seg = w * _num(it["pct"]) / total * e
        d.rectangle([cx, y, cx + seg - 4, y + 96], fill=cols[k % 5])
        cx += seg
    yy = y + 150
    for k, it in enumerate(items):
        if p < (k + 1) / (len(items) + 1) * 0.9:
            yy += 96
            continue
        d.rectangle([x, yy + 14, x + 26, yy + 40], fill=cols[k % 5])
        heavy(d, (x + 50, yy - 6), str(it.get("text") or f"{it['pct']}%"), 62, cols[k % 5] if cols[k % 5] != th["fg"] else th["fg"])
        d.text((x + 50 + d.textlength(str(it.get("text") or f"{it['pct']}%"), font=font(62)) + 30, yy + 12), str(it.get("label", "")),
               font=font(38, "Medium"), fill=th["sub"])
        yy += 96


def viz_people(img, spec, rect, th, p):
    x, y, w, h = rect
    d = ImageDraw.Draw(img)
    total = max(1, min(200, int(_num(spec.get("total")) or 100)))
    hi = max(0, min(total, int(_num(spec.get("highlight")) or 1)))
    cols = 10 if total <= 100 else 20
    rows = math.ceil(total / cols)
    cell = min(w / cols, (h - 170) / rows)
    ox = x + (w - cell * cols) / 2 + cell / 2
    shown = int(total * ease_in_out(p / 0.7))
    for k in range(total):
        if k >= shown and k >= hi:
            continue
        if k >= shown and p < 0.75:
            continue
        _person(d, ox + (k % cols) * cell, y + cell * 0.55 + (k // cols) * cell, cell * 0.9, th["acc"] if k < hi else (STEEL if th["dark"] else (190, 178, 152)))
    yy = y + rows * cell + 36
    if p > 0.7:
        for ln in RS.wrap(d, spec.get("label", ""), font(54), w)[:2]:
            heavy(d, (x, yy), ln, 54, th["fg"])
            yy += 66
        if spec.get("caption"):
            d.text((x, yy + 4), spec["caption"], font=font(32, "Regular"), fill=th["sub"])


def viz_steps(img, spec, rect, th, p):
    x, y, w, h = rect
    d = ImageDraw.Draw(img)
    items = [str(i) for i in spec.get("items", [])][:4]
    n = max(1, len(items))
    row = min(250, h / n)
    vline(d, x + 66, y + 96, y + row * (n - 1) + 96, th["rule"], ease_in_out(p / 0.9), 6)
    for k, t in enumerate(items):
        e = ease_out((p * 1.1 - k / n * 0.9) / 0.25)
        if e <= 0:
            continue
        yy = y + k * row
        heavy(d, (x, yy), f"{k + 1:02d}", 104, th["acc"])
        for j, ln in enumerate(RS.wrap(d, t, font(56, "Medium"), w - 190)[:2]):
            d.text((x + 180, yy + 12 + j * 68 + int((1 - e) * 30)), ln, font=font(56, "Medium"), fill=th["fg"])


def viz_timeline(img, spec, rect, th, p):
    x, y, w, h = rect
    d = ImageDraw.Draw(img)
    items = [i for i in spec.get("items", []) if isinstance(i, dict)][:4]
    n = max(1, len(items))
    gap = min(300, (h - 60) / max(1, n - 1)) if n > 1 else 0
    top, bottom = y + 40, y + 40 + gap * (n - 1)
    vline(d, x + 24, top, bottom, th["rule"], 1.0, 6)
    vline(d, x + 24, top, bottom, th["acc"], ease_in_out(p / 0.85), 6)
    for k, it in enumerate(items):
        yy = top + k * gap
        if p * 1.0 < (k / max(1, n - 1)) * 0.85 - 0.02:
            continue
        d.ellipse([x + 2, yy - 22, x + 46, yy + 22], fill=th["acc"], outline=th["bg"], width=6)
        heavy(d, (x + 96, yy - 40), str(it.get("label", "")).upper(), 62, th["acc2"])
        for j, ln in enumerate(RS.wrap(d, it.get("text", ""), font(48, "Medium"), w - 120)[:2]):
            d.text((x + 96, yy + 44 + j * 60), ln, font=font(48, "Medium"), fill=th["fg"])


_VIZ = {"stat": viz_stat, "compare": viz_compare, "share": viz_share, "dots": viz_people, "steps": viz_steps, "timeline": viz_timeline}


# ----------------------------------------------------------------------------------------------- map
_LAND = None


def land():
    global _LAND
    if _LAND is None:
        try:
            _LAND = json.loads((ASSETS / "world-land.json").read_text(encoding="utf-8"))
        except OSError:
            _LAND = []
    return _LAND


def layout_map(img, sc, th, t, p, gt, rect):
    spec = sc.viz
    x, y, w, h = rect
    lat, lon = float(spec.get("lat", 0)), float(spec.get("lon", 0))
    zoom = clamp(float(spec.get("zoom") or 4.0), 1.5, 9.0)
    e = ease_in_out((p - 0.08) / 0.6)
    z = lerp(1.0, zoom, e)
    lat0, lon0 = lerp(12.0, lat, e), lerp(10.0, lon, e)
    s = W * z / 360.0
    cx, cy = w / 2, h / 2
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    gcol = (30, 46, 74, 255)
    for g in range(-180, 181, 30):
        px = cx + (g - lon0) * s
        if 0 <= px <= w:
            d.line([px, 0, px, h], fill=gcol, width=2)
    for g in range(-60, 91, 30):
        py = cy - (g - lat0) * s
        if 0 <= py <= h:
            d.line([0, py, w, py], fill=gcol, width=2)
    for ring in land():
        pts = [(cx + (lo - lon0) * s, cy - (la - lat0) * s) for lo, la in ring]
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
            continue
        d.polygon(pts, fill=(36, 54, 84, 255), outline=(98, 128, 168, 255))
    mx, my = cx + (lon - lon0) * s, cy - (lat - lat0) * s
    pulse = (gt * 0.9) % 1.0
    if e > 0.5:
        a = int(200 * (1 - pulse) * clamp((e - 0.5) * 3))
        r = 16 + 90 * pulse
        d.ellipse([mx - r, my - r, mx + r, my + r], outline=(229, 53, 45, a), width=5)
    d.ellipse([mx - 15, my - 15, mx + 15, my + 15], fill=RED + (255,), outline=(244, 237, 223, 255), width=4)
    if e > 0.7:
        la = clamp((e - 0.7) / 0.3)
        lx, ly = mx + 70, my - 120
        d.line([mx + 12, my - 12, lx - 8, ly + 52], fill=(244, 237, 223, int(255 * la)), width=4)
        d.text((lx, ly), str(spec.get("label", "")).upper(), font=font(66), fill=(244, 237, 223, int(255 * la)),
               stroke_width=2, stroke_fill=(244, 237, 223, int(255 * la)))
        hemi = f"{abs(lat):.0f}°{'S' if lat < 0 else 'N'}  {abs(lon):.0f}°{'W' if lon < 0 else 'E'}"
        d.text((lx, ly + 78), hemi, font=font(30, "Medium"), fill=(168, 175, 189, int(255 * la)))
    img.paste(layer, (x, y), layer)
    d2 = ImageDraw.Draw(img)
    d2.line([x, y, x + w, y], fill=th["rule"], width=3)
    d2.line([x, y + h, x + w, y + h], fill=th["rule"], width=3)


# ----------------------------------------------------------------------------------------------- layouts
def hud_kicker(img, sc, th, t):
    d = ImageDraw.Draw(img)
    d.rectangle([ML, 196, ML + 20, 216], fill=th["acc"])
    label = f"{sc.index + 1:02d} / {sc.total:02d}   {ACTS.get(sc.act, '')}".strip()
    spaced(d, (ML + 40, 190), label, 30, th["fg"], 4, "Medium")
    handle = "@ALWAYSFACT_"
    f = font(26, "Medium")
    hw = d.textlength(handle, font=f) + 3 * len(handle)
    spaced(d, (W - MR - hw + 40, 194), handle, 26, th["sub"], 3, "Medium")
    hline(d, ML, W - MR + 40, 244, th["rule"], ease_in_out(t / 0.6), 3)


def hud_bottom(img, sc, th, t):
    d = ImageDraw.Draw(img)
    if sc.source and t > 0.8:
        d.rectangle([ML, 1352, ML + 8, 1388], fill=th["acc"])
        spaced(d, (ML + 28, 1354), ("SUMBER: " + sc.source).upper()[:48], 24, th["sub"], 3, "Medium")
    ta = t - LEAD
    cur = None
    for ch in sc.chunks:
        if ch[0][0] - 0.05 <= ta:
            cur = ch
    if not cur or ta < 0:
        return
    cf = font(58)
    sp = d.textlength(" ", font=cf)
    rows, row, rw = [], [], 0.0
    for w in cur:
        ww = d.textlength(w[2], font=cf)
        if row and rw + sp + ww > CW:
            rows.append(row)
            row, rw = [], 0.0
        rw += (sp if row else 0) + ww
        row.append((w, ww))
    rows.append(row)
    y = 1428
    on = th["acc2"] if th["dark"] else th["acc"]
    for row in rows:
        x = ML
        for w, ww in row:
            col = on if (w[0] - 0.02 <= ta < w[1] + 0.06) else th["fg"]
            d.text((x, y), w[2], font=cf, fill=col, stroke_width=1, stroke_fill=col)
            x += ww + sp
        y += 74


def headline_zone(img, sc, th, t, size, max_lines=3, y=300):
    d = ImageDraw.Draw(img)
    words = parse_words(sc.beat.get("text") or "")
    if sc.first and not any(a for _, a in words) and words:
        words[-1] = (words[-1][0], True)
    s, lines = fit_headline(d, words, size, CW, max_lines)
    return reveal_lines(img, lines, ML, y, s, th, t, accent=None), s


def layout_photo(img, sc, th, t, p, gt):
    frame = camera_crop(sc.photo_big, sc.camera, p)
    frame = Image.blend(frame, Image.new("RGB", (W, H), NAVY), 0.22)          # darker, more archival
    img.paste(frame, (0, 0))
    top = scrim(0, 980, 222, 0)
    img.paste(top, (0, 0), top)
    bot = scrim(900, H, 0, 244)
    img.paste(bot, (0, 900), bot)
    bottom, size = headline_zone(img, sc, th, t, 134 if sc.first else 112, 3)
    d = ImageDraw.Draw(img)
    e = ease_in_out((t - 0.5) / 0.7)
    wbar = lerp(34, 240, e)
    hbar = lerp(34, 14, e)
    d.rounded_rectangle([ML, bottom + 34, ML + wbar, bottom + 34 + hbar], radius=int(hbar / 2), fill=th["acc"])
    if sc.photo and t > 0.6:
        credit = re.sub(r"^Foto ilustrasi:\s*", "", sc.photo[1])[:62].upper()
        d.text((ML, 1318), "FOTO " + credit, font=font(20, "Medium"), fill=(168, 175, 189))


def layout_kinetic(img, sc, th, t, p, gt):
    d = ImageDraw.Draw(img)
    vline(d, ML - 34, 300, 1180, th["acc"], ease_in_out(t / 0.9), 10)
    bottom, size = headline_zone(img, sc, th, t, 176, 4, y=330)
    sub = sc.beat.get("sub")
    if sub and t > 0.7:
        for j, ln in enumerate(RS.wrap(d, sub, font(42, "Medium"), CW)[:3]):
            d.text((ML, bottom + 40 + j * 54), ln, font=font(42, "Medium"), fill=th["sub"])


def layout_data(img, sc, th, t, p, gt):
    bottom, size = headline_zone(img, sc, th, t, 100, 3)
    prog = clamp((t - 0.45) / max(0.8, min(2.2, sc.dur * 0.6)))
    y0 = bottom + 56
    rect = (ML, y0, CW, max(420, 1330 - y0))
    _VIZ[sc.viz["kind"]](img, sc.viz, rect, th, prog)


def layout_map_scene(img, sc, th, t, p, gt):
    bottom, size = headline_zone(img, sc, th, t, 100, 2)
    y0 = bottom + 40
    layout_map(img, sc, th, t, p, gt, (0, y0, W, max(520, 1340 - y0)))


def layout_clip(img, sc, th, t, p, gt):
    bottom, size = headline_zone(img, sc, th, t, 88, 2)
    d = ImageDraw.Draw(img)
    pw, ph = CW + 30, 700
    paper = Image.new("RGBA", (pw, ph), PAPER + (255,))
    pd = ImageDraw.Draw(paper)
    pd.rectangle([0, 0, pw - 1, ph - 1], outline=(214, 200, 172), width=3)
    pd.rectangle([40, 44, 52, 92], fill=RED)
    spaced(pd, (72, 48), ("KUTIPAN ASLI · " + (sc.outlet or "SUMBER")).upper()[:44], 28, NAVY, 3, "Medium")
    pd.line([40, 118, pw - 40, 118], fill=(26, 34, 52), width=3)
    q = (sc.quote or "").strip()
    qf = font(41, "Medium")
    words = q.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if pd.textlength(trial, font=qf) <= pw - 110:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    lines = lines[:9]
    yy = 150
    marker_p = ease_in_out((t - 0.7) / 1.0)
    hl = {str(x).lower() for x in (sc.beat.get("highlight") or [])}
    for ln in lines:
        x = 56
        for w in ln.split():
            ww = pd.textlength(w + " ", font=qf)
            if re.search(r"\d", w) or re.sub(r"[^a-z]", "", w.lower()) in hl:
                pd.rectangle([x - 4, yy + 6, x - 4 + (ww - 2) * marker_p, yy + 52], fill=YELLOW)
            pd.text((x, yy), w, font=qf, fill=NAVY)
            x += ww
        yy += 60
    pd.text((40, ph - 60), "Kutipan ini diperiksa kode langsung di halaman sumbernya", font=font(24, "Regular"), fill=(110, 104, 92))
    e = ease_out((t - 0.2) / 0.7)
    rot = paper.rotate(-2.2, resample=Image.BICUBIC, expand=True)
    sh = Image.new("RGBA", rot.size, (0, 0, 0, 0))
    sh.putalpha(rot.getchannel("A").point(lambda v: int(v * 0.45)))
    sh = sh.filter(ImageFilter.GaussianBlur(14))
    oy = bottom + 50 + int((1 - e) * 120)
    ox = ML - 24
    alpha = int(255 * e)
    sh.putalpha(sh.getchannel("A").point(lambda v: int(v * e)))
    img.paste(sh, (ox + 10, oy + 16), sh)
    r2 = rot.copy()
    r2.putalpha(rot.getchannel("A").point(lambda v: int(v * e)))
    img.paste(r2, (ox, oy), r2)


def layout_cta(img, sc, th, t, p, gt):
    d = ImageDraw.Draw(img)
    vline(d, ML - 34, 250, 1500, th["acc"], ease_in_out(t / 1.0), 10)
    spaced(d, (ML, 250), "SATU FAKTA SAINS, SETIAP HARI", 30, th["sub"], 5, "Medium")
    lines = [("IKUTI", th["fg"], 230), ("@ALWAYSFACT_", th["acc"], 92)]
    y = 330
    for k, (txt, col, size) in enumerate(lines):
        band = Image.new("RGBA", (CW + 60, int(size * 1.2)), (0, 0, 0, 0))
        bd = ImageDraw.Draw(band)
        e = ease_out((t - 0.15 - k * 0.15) / 0.55)
        heavy(bd, (0, int((1 - e) * size * 1.1)), txt, size, col)
        img.paste(band, (ML, y), band)
        y += int(size * 1.08)
    hline(d, ML, ML + 620, y + 20, th["acc"], ease_in_out((t - 0.7) / 0.5), 10)
    rows = [("plane", "KIRIM", "ke temanmu yang suka bilang “masa sih?”"), ("bookmark", "SIMPAN", "supaya bisa dibaca lagi"),
            ("comment", "KOMENTAR", "fakta apa yang kamu ingin bahas?")]
    yy = y + 150
    for k, (ic, head, sub) in enumerate(rows):
        e = ease_out((t - 0.9 - k * 0.18) / 0.45)
        if e <= 0:
            continue
        off = int((1 - e) * 50)
        hline(d, ML, W - MR, yy - 24, th["rule"], e, 3)
        RS._icon(d, ic, ML + 40, yy + 52 + off, 34, th["acc2"])
        d.text((ML + 120, yy + 8 + off), head, font=font(54), fill=th["fg"], stroke_width=1, stroke_fill=th["fg"])
        for j, ln in enumerate(RS.wrap(d, sub, font(34, "Regular"), CW - 130)[:2]):
            d.text((ML + 120, yy + 74 + j * 44 + off), ln, font=font(34, "Regular"), fill=th["sub"])
        yy += 190


# ----------------------------------------------------------------------------------------------- frame
def _background(img, sc, th, p):
    d = ImageDraw.Draw(img)
    drift = (p - 0.5) * 60
    for k in range(4):
        x = ML + k * 280 + drift * (0.4 + 0.3 * k) - 80
        d.line([x, 250, x, H], fill=th["rule"], width=1)
    ghost = f"{sc.index + 1:02d}"
    d.text((W + 40 - drift * 2.2, 520), ghost, font=font(560), fill=th["bg"], anchor="ra", stroke_width=3, stroke_fill=th["rule"])


def draw_scene(sc, t, total_t, global_t):
    """One frame of a scene at local time t (seconds)."""
    p = clamp(t / sc.dur)
    th = sc.th
    img = Image.new("RGB", (W, H), th["bg"])
    if sc.layout == "cta":
        layout_cta(img, sc, th, t, p, global_t)
    elif sc.layout == "photo":
        layout_photo(img, sc, th, t, p, global_t)
        hud_kicker(img, sc, th, t)
    else:
        _background(img, sc, th, p)
        {"kinetic": layout_kinetic, "data": layout_data, "map": layout_map_scene, "clip": layout_clip}[sc.layout](img, sc, th, t, p, global_t)
        img = camera_whole(img, sc.camera, p)
        hud_kicker(img, sc, th, t)
    if sc.layout != "cta":
        hud_bottom(img, sc, th, t)
    img = finish(img, global_t, th["dark"])
    return transition(sc, img, t)


def transition(sc, img, t):
    """How the scene arrives: an accent bar wipes it in, it pushes in from the right, it fades up, or it cuts."""
    TD = {"wipe": 0.5, "slide": 0.45, "fade": 0.4, "cut": 0.0}[sc.transition]
    if t >= TD or sc.transition == "cut" or sc.index == 0:
        return img
    q = ease_in_out(t / TD)
    base = Image.new("RGB", (W, H), sc.prev_bg)
    if sc.transition == "wipe":
        x = int(W * q)
        base.paste(img.crop((0, 0, x, H)), (0, 0))
        ImageDraw.Draw(base).rectangle([max(0, x - 36), 0, x + 36, H], fill=RED)
        return base
    if sc.transition == "slide":
        base.paste(img, (int(W * (1 - q)), 0))
        ImageDraw.Draw(base).rectangle([int(W * (1 - q)) - 10, 0, int(W * (1 - q)), H], fill=RED)
        return base
    return Image.blend(base, img, q)
