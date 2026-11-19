"""Infographic panels drawn by code (no AI, no photos): turns a small JSON spec on a slide into a clear picture,
so a slide that has no photographable subject (a gas, a ratio, a process) still SHOWS its idea.

Slide spec:  "viz": {"kind": "stat|share|compare|dots|steps", ...}
  stat    {"value": "14-20x", "label": "kentut per hari", "note": "orang sehat"}
  share   {"items": [{"label": "Nitrogen", "pct": 60}, {"label": "Oksigen", "pct": 25}, ...], "caption": "..."}
  compare {"items": [{"label": "Manusia", "value": 14}, {"label": "...", "value": 20}], "unit": "kali", "caption": "..."}
  dots    {"total": 100, "highlight": 1, "label": "1 dari sejuta", "caption": "..."}
  steps   {"items": ["Makanan", "Bakteri usus", "Gas berbau"], "caption": "..."}
  timeline {"items": [{"label": "6,3 juta tahun lalu", "text": "Paus hidup di Peru"}, ...2-4 items], "caption": "..."}
The same code draws a panel of any size, so it works for the carousel and for Reel frames.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
PANEL, PANEL_EDGE = (236, 236, 247), (214, 214, 236)
TEXT, MUTED, DIM = (34, 34, 42), (92, 92, 112), (210, 210, 230)
AMBER, MAGENTA, TEAL, BLUE, PURPLE = (255, 160, 40), (233, 100, 160), (31, 175, 140), (116, 130, 250), (107, 99, 200)
PALETTE = [PURPLE, MAGENTA, TEAL, AMBER, BLUE]
KINDS = ("stat", "share", "compare", "dots", "steps", "timeline", "map")


def font(weight, size):
    p = FONT_DIR / f"Poppins-{weight}.ttf"
    for path in (p, Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def wrap(d, text, fnt, max_w):
    lines, line = [], ""
    for word in str(text).split():
        trial = (line + " " + word).strip()
        if d.textlength(trial, font=fnt) <= max_w:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    return lines + ([line] if line else [])


def fit(d, text, weight, size, max_w, min_size=24):
    while size > min_size and d.textlength(str(text), font=font(weight, size)) > max_w:
        size -= 2
    return font(weight, size)


def _panel(w, h):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, w - 1, h - 1], radius=40, fill=PANEL, outline=PANEL_EDGE, width=3)
    return img


def _caption(d, text, w, y, size=30, color=MUTED):
    if not text:
        return y
    f = font("Regular", size)
    for ln in wrap(d, text, f, w - 120):
        d.text((w // 2, y), ln, font=f, fill=color, anchor="ma")
        y += int(size * 1.4)
    return y


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def stat(spec, w, h, p=1.0):
    img = _panel(w, h)
    d = ImageDraw.Draw(img)
    value = str(spec.get("value", ""))
    f = fit(d, value, "Bold", 190, w - 110, 70)
    if p > 0.04:
        d.text((w // 2, int(h * 0.40)), value, font=f, fill=PURPLE, anchor="mm")
    y = int(h * 0.40) + f.size // 2 + 26
    lab = spec.get("label", "")
    lf = font("Bold", 44)
    for ln in wrap(d, lab, lf, w - 140)[:3]:
        if p > 0.35:
            d.text((w // 2, y), ln, font=lf, fill=TEXT, anchor="ma")
        y += 58
    if p > 0.6:
        _caption(d, spec.get("note", ""), w, y + 8, 30)
    return img


def share(spec, w, h, p=1.0):
    img = _panel(w, h)
    d = ImageDraw.Draw(img)
    items = [i for i in spec.get("items", []) if _num(i.get("pct")) is not None][:5]
    total = sum(_num(i["pct"]) for i in items) or 1
    x0, x1, y0, bh = 60, w - 60, int(h * 0.14), 120
    d.rounded_rectangle([x0, y0, x1, y0 + bh], radius=30, fill=DIM)
    x = x0
    for k, it in enumerate(items):
        seg = (x1 - x0) * _num(it["pct"]) / total
        full_seg = seg
        seg = seg * p
        col = PALETTE[k % len(PALETTE)]
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rectangle([x, y0, x + seg, y0 + bh], fill=col)
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rounded_rectangle([x0, y0, x1, y0 + bh], radius=30, fill=255)
        layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", (w, h), 0), m))
        img = Image.alpha_composite(img, layer)
        x += full_seg * p
    d = ImageDraw.Draw(img)
    y = y0 + bh + 60
    for k, it in enumerate(items):
        if p < (k + 1) / (len(items) + 1) * 0.9:
            y += 64
            continue
        col = PALETTE[k % len(PALETTE)]
        d.ellipse([x0 + 6, y + 8, x0 + 38, y + 40], fill=col)
        label = str(it.get("label", ""))
        d.text((x0 + 62, y), label, font=font("Medium", 36), fill=TEXT)
        shown = str(it.get("text") or f"{it['pct']}%")
        d.text((x1 - 6, y), shown, font=font("Bold", 36), fill=col, anchor="ra")
        y += 64
    _caption(d, spec.get("caption", ""), w, y + 14, 28)
    return img


def compare(spec, w, h, p=1.0):
    img = _panel(w, h)
    d = ImageDraw.Draw(img)
    items = [i for i in spec.get("items", []) if _num(i.get("value")) is not None][:4]
    top = max([_num(i["value"]) for i in items] + [1e-9])
    unit = spec.get("unit", "")
    n = max(1, len(items))
    gap = 36
    bar_h = min(110, int((h * 0.62 - gap * (n - 1)) / n))
    y = int(h * 0.12)
    for k, it in enumerate(items):
        v = _num(it["value"])
        col = PALETTE[k % len(PALETTE)]
        d.text((70, y), str(it.get("label", "")), font=font("Medium", 34), fill=TEXT)
        by = y + 50
        full = w - 140
        d.rounded_rectangle([70, by, 70 + full, by + bar_h - 50 + 20], radius=24, fill=DIM)
        d.rounded_rectangle([70, by, 70 + max(40, int(full * v / top * p)), by + bar_h - 50 + 20], radius=24, fill=col)
        d.text((w - 70, y), f"{it['value']} {unit}".strip(), font=font("Bold", 34), fill=col, anchor="ra")
        y += bar_h + gap
    _caption(d, spec.get("caption", ""), w, min(h - 110, y + 6), 28)
    return img


def dots(spec, w, h, p=1.0):
    img = _panel(w, h)
    d = ImageDraw.Draw(img)
    total = max(1, min(200, int(_num(spec.get("total")) or 100)))
    hi = max(0, min(total, int(_num(spec.get("highlight")) or 1)))
    area_w, area_h = w - 140, int(h * 0.56)
    cols = max(1, int(round(math.sqrt(total * area_w / area_h))))
    rows = math.ceil(total / cols)
    cell = min(area_w / cols, area_h / rows)
    r = cell * 0.36
    ox = (w - cell * cols) / 2 + cell / 2
    oy = int(h * 0.09) + cell / 2
    for k in range(total):
        cx, cy = ox + (k % cols) * cell, oy + (k // cols) * cell
        on = k < hi
        if k >= int(total * min(1.0, p * 1.7)) and not (on and p > 0.55):
            continue
        rr = r * (1.25 if on else 1)
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=MAGENTA if on else DIM)
    y = int(oy + rows * cell + 10)
    lab = spec.get("label", "")
    f = fit(d, lab, "Bold", 46, w - 120, 28)
    if p > 0.7:
        d.text((w // 2, y), lab, font=f, fill=PURPLE, anchor="ma")
        _caption(d, spec.get("caption", ""), w, y + f.size + 14, 28)
    return img


def steps(spec, w, h, p=1.0):
    img = _panel(w, h)
    d = ImageDraw.Draw(img)
    items = [str(i) for i in spec.get("items", [])][:4]
    n = max(1, len(items))
    y = int(h * 0.10)
    box_h = min(120, int((h * 0.66 - 44 * (n - 1)) / n))
    shown = max(1, math.ceil(p * n - 1e-6)) if p > 0 else 0
    for k, t in enumerate(items):
        if k >= shown:
            break
        col = PALETTE[k % len(PALETTE)]
        d.rounded_rectangle([70, y, w - 70, y + box_h], radius=30, fill=DIM)
        d.ellipse([90, y + box_h // 2 - 30, 150, y + box_h // 2 + 30], fill=col)
        d.text((120, y + box_h // 2 - 2), str(k + 1), font=font("Bold", 34), fill=(255, 255, 255), anchor="mm")
        f = fit(d, t, "Medium", 38, w - 300, 24)
        d.text((176, y + box_h // 2), t, font=f, fill=TEXT, anchor="lm")
        if k < n - 1 and k + 1 < shown:
            ax, ay = w // 2, y + box_h + 22
            d.polygon([(ax - 18, ay - 12), (ax + 18, ay - 12), (ax, ay + 12)], fill=MUTED)
        y += box_h + 44
    _caption(d, spec.get("caption", ""), w, min(h - 100, y - 20), 28)
    return img


def timeline(spec, w, h, p=1.0):
    img = _panel(w, h)
    d = ImageDraw.Draw(img)
    items = [i for i in spec.get("items", []) if isinstance(i, dict)][:4]
    n = max(1, len(items))
    x0, top = 120, int(h * 0.12)
    span = int(h * 0.62)
    d.line([x0, top, x0, top + span], fill=DIM, width=10)
    d.line([x0, top, x0, top + int(span * clamp01(p * 1.15))], fill=PURPLE, width=10)
    for k, it in enumerate(items):
        y = top + int(span * (k / max(1, n - 1))) if n > 1 else top
        if p * 1.15 < (k / max(1, n - 1)) - 0.02:
            continue
        col = PALETTE[k % len(PALETTE)]
        d.ellipse([x0 - 26, y - 26, x0 + 26, y + 26], fill=col, outline=(255, 255, 255), width=6)
        d.text((x0 + 62, y - 30), str(it.get("label", "")), font=font("Bold", 38), fill=col)
        for j, ln in enumerate(wrap(d, it.get("text", ""), font("Medium", 32), w - x0 - 110)[:2]):
            d.text((x0 + 62, y + 18 + j * 40), ln, font=font("Medium", 32), fill=TEXT)
    _caption(d, spec.get("caption", ""), w, min(h - 80, top + span + 70), 28)
    return img


def clamp01(x):
    return max(0.0, min(1.0, x))


_DRAW = {"timeline": timeline, "stat": stat, "share": share, "compare": compare, "dots": dots, "steps": steps}


def valid(spec):
    """True when the spec can be drawn (the referee uses this too, so a broken spec never reaches a slide)."""
    if not isinstance(spec, dict) or spec.get("kind") not in KINDS:
        return False
    k = spec["kind"]
    if k == "stat":
        return bool(spec.get("value")) and bool(spec.get("label"))
    if k in ("share", "compare"):
        its = spec.get("items")
        return isinstance(its, list) and 2 <= len(its) <= 5 and all(isinstance(i, dict) for i in its) and \
            sum(_num(i.get("pct" if k == "share" else "value")) is not None for i in its) >= 2
    if k == "dots":
        return _num(spec.get("total")) is not None and _num(spec.get("highlight")) is not None
    if k == "steps":
        return isinstance(spec.get("items"), list) and 2 <= len(spec["items"]) <= 4
    if k == "map":   # Reels only: an animated zoom on real coastlines
        try:
            return -90 <= float(spec.get("lat")) <= 90 and -180 <= float(spec.get("lon")) <= 180 and bool(spec.get("label"))
        except (TypeError, ValueError):
            return False
    if k == "timeline":
        its = spec.get("items")
        return isinstance(its, list) and 2 <= len(its) <= 4 and all(isinstance(i, dict) and i.get("label") for i in its)
    return False


def render(spec, w=960, h=640, p=1.0):
    """RGBA picture of the infographic, or None when the spec is not drawable."""
    if not valid(spec) or spec["kind"] not in _DRAW:
        return None
    return _DRAW[spec["kind"]](spec, w, h, max(0.0, min(1.0, p)))
