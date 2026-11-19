#!/usr/bin/env python3
"""Designer step (no AI, no tokens): turns content/<id>.json into 1080x1350 JPEG carousel slides in media/,
with licensed photos where a relevant one exists, and fills media_urls (and photo credits) in queue.csv.

Photos come only from agents/images.py (Wikimedia Commons, NASA) and only for the post's literal main subject.
A post without a relevant photo gets a typographic design, never a random picture.
Env: MEDIA_BASE_URL (public base URL of media/), NO_IMAGES=1 (skip photo search).
"""
import csv
import io
import json
import os
import re
import sys
import textwrap
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import images  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QUEUE, CONTENT, MEDIA = ROOT / "queue.csv", ROOT / "content", ROOT / "media"
FONT_DIR = ROOT / "assets" / "fonts"
FIELDS = ["id", "scheduled_at", "media_urls", "caption", "status", "result", "posted_at"]
W, H = 1080, 1350
BG, TEXT, MUTED = (17, 17, 26), (246, 244, 250), (190, 184, 204)
AMBER, MAGENTA = (255, 196, 61), (224, 96, 168)
HANDLE = "@alwaysfact_"
MAX_PHOTOS = 5


def font(weight, size):
    for path in (FONT_DIR / f"Poppins-{weight}.ttf", Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
                 Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def wrap(draw, text, fnt, max_w):
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=fnt) <= max_w:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    return lines + ([line] if line else [])


def draw_block(draw, text, fnt, x, y, max_w, fill, gap=1.28):
    for ln in wrap(draw, text, fnt, max_w):
        draw.text((x, y), ln, font=fnt, fill=fill)
        y += int(fnt.size * gap)
    return y


def fade(w, h, color, a0, a1):
    """RGBA overlay whose opacity goes from a0 (top) to a1 (bottom)."""
    ramp = Image.linear_gradient("L").resize((w, h))
    mask = ramp.point(lambda v: int(a0 + (a1 - a0) * v / 255))
    layer = Image.new("RGBA", (w, h), color + (0,))
    layer.putalpha(mask)
    return layer


def photo_fit(data, w, h, cy=0.42):
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=(0.5, cy))
    img = ImageEnhance.Contrast(img).enhance(1.08)
    return ImageEnhance.Color(img).enhance(1.12)  # a touch more life, never a filter that changes what the subject looks like


def is_grayscale(data):
    """True for black-and-white scans and engravings: they look dull next to colour photos."""
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((64, 64))
        sat = im.convert("HSV").split()[1]
        return sum(sat.getdata()) / max(1, sat.size[0] * sat.size[1]) < 22
    except Exception:
        return False


def rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius=radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def shadow_text(img, xy, text, fnt, fill, offset=3, alpha=150):
    """Text with a soft dark shadow so it stays readable on any photo."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((xy[0] + offset, xy[1] + offset + 1), text, font=fnt, fill=(0, 0, 0, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(5))
    base = Image.alpha_composite(img.convert("RGBA"), layer)
    ImageDraw.Draw(base).text(xy, text, font=fnt, fill=fill)
    return base.convert("RGB")


def backdrop():
    """Dark gradient with soft colour glows, used when there is no photo."""
    img = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([-260, -220, 620, 660], fill=MAGENTA + (70,))
    g.ellipse([560, 780, 1420, 1600], fill=AMBER + (46,))
    return Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(120))).convert("RGB")


def progress(img, idx, total):
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 10, W, H], fill=(38, 38, 54))
    d.rectangle([0, H - 10, int(W * (idx + 1) / total), H], fill=AMBER)


def footer(img, idx, total, credit_text=None):
    d = ImageDraw.Draw(img)
    d.text((70, H - 96), HANDLE, font=font("Medium", 30), fill=AMBER)
    label = f"{idx + 1}/{total}"
    d.text((W - 70 - d.textlength(label, font=font("Medium", 30)), H - 96), label, font=font("Medium", 30), fill=MUTED)
    if credit_text:
        d.text((70, H - 140), credit_text[:80], font=font("Regular", 21), fill=(176, 172, 194))
    progress(img, idx, total)


def badge(img, cx, cy, text, r=50):
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6], fill=BG)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=AMBER)
    d.text((cx, cy - 3), text, font=font("Bold", 46), fill=BG, anchor="mm")


def fit_text(d, title, body, max_w, max_h, tsize=58, bsize=38):
    """Largest title/body sizes that fit in max_h, so long Indonesian sentences never run off the slide."""
    while True:
        tf, bf = font("Bold", tsize), font("Regular", bsize)
        h = len(wrap(d, title, tf, max_w)) * int(tsize * 1.2) + 24 + len(wrap(d, body or "", bf, max_w)) * int(bsize * 1.38)
        if h <= max_h or tsize <= 40:
            return tf, bf, h
        tsize, bsize = tsize - 3, max(28, bsize - 2)


def slide_cover(s, idx, total, photo, credit_text):
    if photo:
        img = photo_fit(photo, W, H, cy=0.55).convert("RGBA")
        img = Image.alpha_composite(img, Image.new("RGBA", (W, H), (0, 0, 0, 28)))
        img.alpha_composite(fade(W, 330, BG, 150, 0), (0, 0))        # keeps the handle readable
        img.alpha_composite(fade(W, 900, BG, 0, 245), (0, H - 900))  # keeps the title readable
        img = img.convert("RGB")
    else:
        img = backdrop()
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([60, 60, 60 + d.textlength(HANDLE, font=font("Medium", 30)) + 56, 122], radius=31, fill=(24, 24, 36))
    d.text((88, 74), HANDLE, font=font("Medium", 30), fill=AMBER)
    fnt = font("Bold", 84 if len(s["title"]) < 60 else 70)
    lines = wrap(d, s["title"], fnt, W - 150)
    step = int(fnt.size * 1.2)
    y = H - 230 - step * len(lines)
    d.rounded_rectangle([75, y - 92, 75 + d.textlength("FAKTA SAINS", font=font("Bold", 26)) + 44, y - 44], radius=24, fill=AMBER)
    d.text((97, y - 85), "FAKTA SAINS", font=font("Bold", 26), fill=BG)
    for ln in lines:
        img = shadow_text(img, (75, y), ln, fnt, TEXT)
        y += step
    d = ImageDraw.Draw(img)
    label = "Geser"
    lw = d.textlength(label, font=font("Medium", 32))
    ax = W - 75
    d.text((ax - 70 - lw, H - 98), label, font=font("Medium", 32), fill=AMBER)
    d.line([ax - 52, H - 80, ax - 8, H - 80], fill=AMBER, width=5)           # arrow shaft
    d.polygon([(ax - 8, H - 94), (ax + 8, H - 80), (ax - 8, H - 66)], fill=AMBER)  # arrow head
    if credit_text:
        d.text((75, H - 96), credit_text[:60], font=font("Regular", 21), fill=(200, 196, 214))
    progress(img, idx, total)
    return img


def slide_card(s, idx, total, photo, credit_text):
    """Photo as a framed card on top, text below."""
    img = backdrop()
    card = rounded(photo_fit(photo, 960, 640, cy=0.5), 44)
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rounded_rectangle([64, 74, 1016, 726], radius=44, fill=(0, 0, 0, 120))
    img = Image.alpha_composite(img.convert("RGBA"), frame.filter(ImageFilter.GaussianBlur(18)))
    img.alpha_composite(card, (60, 60))
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([60, 60, 1020, 700], radius=44, outline=AMBER, width=4)
    badge(img, 150, 700, f"{idx:02d}" if idx < 100 else str(idx), r=48)
    tf, bf, _ = fit_text(d, s["title"], s.get("body") or "", W - 150, H - 190 - 790)
    y = draw_block(d, s["title"], tf, 75, 790, W - 150, TEXT, 1.2) + 24
    if s.get("body"):
        draw_block(d, s["body"], bf, 75, y, W - 150, MUTED, 1.38)
    footer(img, idx, total, credit_text)
    return img


def slide_full(s, idx, total, photo, credit_text):
    """Full-bleed photo with the text laid over a gradient."""
    img = photo_fit(photo, W, H, cy=0.5).convert("RGBA")
    img.alpha_composite(fade(W, 1000, BG, 0, 238), (0, H - 1000))
    img.alpha_composite(fade(W, 260, BG, 130, 0), (0, 0))
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([60, 60, 60 + d.textlength(HANDLE, font=font("Medium", 30)) + 56, 122], radius=31, fill=(24, 24, 36))
    d.text((88, 74), HANDLE, font=font("Medium", 30), fill=AMBER)
    badge(img, W - 120, 92, f"{idx:02d}", r=44)
    tf, bf, h = fit_text(d, s["title"], s.get("body") or "", W - 150, 700, tsize=62, bsize=38)
    y = H - 200 - h
    d.rounded_rectangle([75, y - 30, 75 + 90, y - 22], radius=4, fill=AMBER)
    for ln in wrap(d, s["title"], tf, W - 150):
        img = shadow_text(img, (75, y), ln, tf, TEXT)
        y += int(tf.size * 1.2)
    d = ImageDraw.Draw(img)
    y += 24
    if s.get("body"):
        draw_block(d, s["body"], bf, 75, y, W - 150, (226, 222, 236), 1.38)
    footer(img, idx, total, credit_text)
    return img


def slide_text(s, idx, total):
    """Typographic slide (no photo): big ghost number, accent bar."""
    img = backdrop()
    ghost = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(ghost).text((W - 60, 40), f"{idx:02d}", font=font("Bold", 400), fill=(255, 255, 255, 24), anchor="ra")
    img = Image.alpha_composite(img.convert("RGBA"), ghost).convert("RGB")
    d = ImageDraw.Draw(img)
    tf, bf, h = fit_text(d, s["title"], s.get("body") or "", W - 150, 760, tsize=64, bsize=40)
    y = max(380, int((H - h) / 2) + 20)
    d.rounded_rectangle([75, y - 40, 75 + 90, y - 32], radius=4, fill=AMBER)
    y = draw_block(d, s["title"], tf, 75, y, W - 150, TEXT, 1.2) + 24
    if s.get("body"):
        draw_block(d, s["body"], bf, 75, y, W - 150, MUTED, 1.38)
    footer(img, idx, total)
    return img


def slide_cta(s, idx, total):
    img = Image.new("RGB", (W, H), BG)
    px = img.load()
    for yy in range(H):  # diagonal-ish vertical gradient, magenta to amber
        t = yy / H
        for xx in range(0, W, 1):
            u = min(1.0, max(0.0, 0.65 * t + 0.35 * xx / W))
            px[xx, yy] = (int(120 + 135 * u), int(28 + 150 * u), int(96 - 36 * u))
    d = ImageDraw.Draw(img)
    d.text((75, 360), "Suka fakta yang", font=font("Medium", 48), fill=(255, 255, 255))
    d.text((75, 425), "bikin melongo?", font=font("Medium", 48), fill=(255, 255, 255))
    d.text((75, 540), "Ikuti", font=font("Bold", 120), fill=(255, 255, 255))
    d.text((75, 670), HANDLE, font=font("Bold", 92), fill=(255, 255, 255))
    body = re.sub(r"^\s*Ikuti\s+@alwaysfact_\s*(dan\s+)?", "", s.get("body") or "", flags=re.I).strip()
    body = (body[:1].upper() + body[1:]) if body else "Fakta sains baru setiap hari."
    draw_block(d, body, font("Regular", 38), 75, 820, W - 150, (255, 244, 230), 1.4)
    d.text((75, H - 96), "Simpan dan bagikan ke temanmu", font=font("Medium", 30), fill=(255, 244, 230))
    progress(img, idx, total)
    return img


def render_post(post, out_dir, photos, slide_photos=None):
    """Writes <id>-<n>.jpg for every slide. `photos` = list of (bytes, credit_text) of the main subject: the first is the
    cover, the rest go to the fact slides in order (alternating framed card and full-bleed). `slide_photos` maps a slide
    index to its own (bytes, credit_text). Slides left without a photo get the typographic design."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slides, total, names = post["slides"][:10], len(post["slides"][:10]), []
    spare, own, used = list(photos or []), dict(slide_photos or {}), 0
    for i, s in enumerate(slides):
        if s["type"] == "hook":
            photo, credit = (own.get(i) or (spare.pop(0) if spare else (None, None)))
            img = slide_cover(s, i, total, photo, credit)
        elif s["type"] == "cta":
            img = slide_cta(s, i, total)
        else:
            pick = own.get(i) or (spare.pop(0) if spare else None)
            if pick:
                img = (slide_card if used % 2 == 0 else slide_full)(s, i, total, pick[0], pick[1])
                used += 1
            else:
                img = slide_text(s, i, total)
        name = f"{post['id']}-{i + 1}.jpg"
        img.convert("RGB").save(out_dir / name, "JPEG", quality=92)
        names.append(name)
    return names


def _download(c, bucket_color, bucket_grey):
    try:
        time.sleep(0.6)  # Wikimedia throttles bursts from shared addresses
        data = images.get_bytes(c["url"])
    except Exception as e:
        images.ERRORS.append(f"download {c['url'][:70]}: {type(e).__name__}: {str(e)[:60]}")
        return
    (bucket_grey if is_grayscale(data) else bucket_color).append((data, "Foto ilustrasi: " + images.credit(c), c))


def gather_photos(post):
    """Up to MAX_PHOTOS real, relevant, licensed photos of the post's main subject. Colour photos first;
    black-and-white ones only if there are no colour ones."""
    if "image_query" in post:  # an explicit empty value means: no literal photographable subject, keep it typographic
        query = post["image_query"]
    else:
        query = (post["slides"][0].get("visual") or {}).get("keywords") if post.get("slides") else ""
    prefer = "nasa" if "semesta" in (post.get("pillar") or "").lower() else "commons"
    color, grey = [], []
    for c in images.find_many(query, MAX_PHOTOS, prefer, pool=MAX_PHOTOS * 4):
        if images.quality(c) < 0 and len(color) >= (3 if images.quality(c) > -4 else 2):
            break  # best-first order: dull museum shots only top up to three; drawings and models never
        _download(c, color, grey)
        if len(color) >= MAX_PHOTOS:
            break
    return (color + grey)[:MAX_PHOTOS]


def gather_slide_photos(post, taken):
    """A slide may carry its own literal `image_query` (e.g. the bacterium on the bacteria slide). {slide index: photo}."""
    out = {}
    for i, s in enumerate(post["slides"][:10]):
        q = s.get("image_query")
        if not q or s["type"] in ("hook", "cta"):
            continue
        color, grey = [], []
        for c in images.find_many(q, 4, "commons", pool=8):
            if c["url"] in taken:
                continue
            _download(c, color, grey)
            if color:
                break
        pick = (color or grey or [None])[0]
        if pick:
            out[i] = pick
            taken.add(pick[2]["url"])
    return out


def caption_with_credits(caption, credits):
    caption = re.sub(r"\n*Foto ilustrasi:[^\n]*", "", caption)  # a re-render replaces the old credit line, never stacks a second one
    if not credits:
        return caption
    line = "Foto ilustrasi: " + "; ".join(dict.fromkeys(credits)) + "."
    head, sep, tags = caption.rpartition("\n\n#")
    return f"{head}\n\n{line}{sep}{tags}" if sep else f"{caption}\n\n{line}"


def main():
    base = os.environ.get("MEDIA_BASE_URL", "").rstrip("/")
    with open(QUEUE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    MEDIA.mkdir(exist_ok=True)
    force = {x for x in os.environ.get("RERENDER", "").replace(" ", "").split(",") if x}  # ids to redo
    for r in rows:
        src = CONTENT / f"{r['id']}.json"
        if (r.get("media_urls") and r["id"] not in force) or not src.exists():
            continue
        post = json.loads(src.read_text(encoding="utf-8"))
        post.setdefault("id", r["id"])
        found = gather_photos(post)
        mine = gather_slide_photos(post, {c["url"] for _, _, c in found})
        names = render_post(post, MEDIA, [(b, t) for b, t, _ in found], {i: (b, t) for i, (b, t, _) in mine.items()})
        everything = found[:] + list(mine.values())
        r["media_urls"] = " ".join(f"{base}/{n}" for n in names)
        r["caption"] = caption_with_credits(r["caption"], [images.credit(c) for _, _, c in everything])
        post["image_credits"] = [images.credit(c) for _, _, c in everything]
        src.write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"rendered {len(names)} slides for {r['id']} with {len(everything)} photo(s)")
        if not everything:
            print(f"  no photo for {r['id']} (query: {post.get('image_query')!r}); reasons: {images.ERRORS[-3:] or 'no relevant licensed result'}")
    with open(QUEUE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
