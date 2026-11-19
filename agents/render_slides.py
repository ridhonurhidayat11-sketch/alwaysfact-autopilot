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
MAX_PHOTOS = 2


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
    return ImageEnhance.Contrast(img).enhance(1.05)


def backdrop():
    """Dark gradient with soft colour glows, used when there is no photo."""
    img = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([-260, -220, 620, 660], fill=MAGENTA + (70,))
    g.ellipse([560, 780, 1420, 1600], fill=AMBER + (46,))
    return Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(120))).convert("RGB")


def footer(img, idx, total, credit_text=None):
    d = ImageDraw.Draw(img)
    d.text((70, H - 96), HANDLE, font=font("Medium", 30), fill=AMBER)
    label = f"{idx + 1}/{total}"
    d.text((W - 70 - d.textlength(label, font=font("Medium", 30)), H - 96), label, font=font("Medium", 30), fill=MUTED)
    if credit_text:
        d.text((70, H - 150), credit_text[:78], font=font("Regular", 21), fill=(150, 146, 166))


def slide_cover(s, idx, total, photo, credit_text):
    if photo:
        img = photo_fit(photo, W, H, cy=0.62).convert("RGBA")
        img = Image.alpha_composite(img, Image.new("RGBA", (W, H), (0, 0, 0, 70)))
        over = fade(W, 860, BG, 0, 250)
        img.alpha_composite(over, (0, H - 860))
        img = img.convert("RGB")
    else:
        img = backdrop()
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([60, 60, 60 + d.textlength(HANDLE, font=font("Medium", 30)) + 56, 122], radius=31, fill=(24, 24, 36))
    d.text((88, 74), HANDLE, font=font("Medium", 30), fill=AMBER)
    fnt = font("Bold", 80 if len(s["title"]) < 70 else 66)
    lines = wrap(d, s["title"], fnt, W - 150)
    y = H - 250 - int(fnt.size * 1.22) * len(lines)
    d.text((75, y - 70), "FAKTA SAINS", font=font("Medium", 28), fill=AMBER)
    for ln in lines:
        d.text((75, y), ln, font=fnt, fill=TEXT)
        y += int(fnt.size * 1.22)
    label = "Geser"
    lw = d.textlength(label, font=font("Medium", 32))
    ax = W - 75
    d.text((ax - 70 - lw, H - 98), label, font=font("Medium", 32), fill=AMBER)
    d.line([ax - 52, H - 80, ax - 8, H - 80], fill=AMBER, width=5)           # arrow shaft
    d.polygon([(ax - 8, H - 94), (ax + 8, H - 80), (ax - 8, H - 66)], fill=AMBER)  # arrow head
    if credit_text:
        d.text((75, H - 96), credit_text[:60], font=font("Regular", 21), fill=(170, 166, 186))
    return img


def slide_fact(s, idx, total, photo, credit_text):
    if photo:
        img = Image.new("RGB", (W, H), BG)
        top = photo_fit(photo, W, 640).convert("RGBA")
        top.alpha_composite(fade(W, 220, BG, 0, 255), (0, 420))
        img.paste(top.convert("RGB"), (0, 0))
        y0 = 690
    else:
        img = backdrop()
        d0 = ImageDraw.Draw(img)
        num = f"{idx:02d}"
        ghost = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(ghost).text((W - 70, 70), num, font=font("Bold", 380), fill=(255, 255, 255, 22), anchor="ra")
        img = Image.alpha_composite(img.convert("RGBA"), ghost).convert("RGB")
        probe = ImageDraw.Draw(img)
        block = (len(wrap(probe, s["title"], font("Bold", 60), W - 150)) * int(60 * 1.22) + 22
                 + len(wrap(probe, s.get("body") or "", font("Regular", 40), W - 150)) * int(40 * 1.4))
        y0 = max(430, int((H - block) / 2) + 30)
    d = ImageDraw.Draw(img)
    y = draw_block(d, s["title"], font("Bold", 60), 75, y0, W - 150, TEXT, 1.22) + 22
    if s.get("body"):
        draw_block(d, s["body"], font("Regular", 40), 75, y, W - 150, MUTED, 1.4)
    footer(img, idx, total, credit_text if photo else None)
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
    return img


def render_post(post, out_dir, photos):
    """Writes <id>-<n>.jpg for every slide. `photos` = list of (bytes, credit_text). Returns the file names."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slides, total, names, pi = post["slides"][:10], len(post["slides"][:10]), [], 0
    for i, s in enumerate(slides):
        use_photo = None
        if photos and s["type"] == "hook":
            use_photo = photos[0]
        elif photos and s["type"] != "cta" and i in (2, 4) and len(photos) > (1 if i == 2 else 2):
            use_photo = photos[1 if i == 2 else 2]
        photo, credit_text = (use_photo if use_photo else (None, None))
        if s["type"] == "hook":
            img = slide_cover(s, i, total, photo, credit_text)
        elif s["type"] == "cta":
            img = slide_cta(s, i, total)
        else:
            img = slide_fact(s, i, total, photo, credit_text)
        name = f"{post['id']}-{i + 1}.jpg"
        img.convert("RGB").save(out_dir / name, "JPEG", quality=92)
        names.append(name)
    return names


def gather_photos(post):
    """Up to MAX_PHOTOS relevant, licensed photos of the post's literal main subject, with credit text."""
    query = post.get("image_query") or ((post["slides"][0].get("visual") or {}).get("keywords") if post.get("slides") else "")
    prefer = "nasa" if "semesta" in (post.get("pillar") or "").lower() else "commons"
    got = []
    for c in images.find_many(query, MAX_PHOTOS, prefer):
        try:
            got.append((images.get_bytes(c["url"]), "Foto ilustrasi: " + images.credit(c), c))
        except Exception as e:
            images.ERRORS.append(f"download {c['url'][:70]}: {type(e).__name__}: {str(e)[:60]}")
            continue
    return got


def caption_with_credits(caption, credits):
    if not credits or "Foto ilustrasi" in caption:
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
        names = render_post(post, MEDIA, [(b, t) for b, t, _ in found])
        r["media_urls"] = " ".join(f"{base}/{n}" for n in names)
        r["caption"] = caption_with_credits(r["caption"], [images.credit(c) for _, _, c in found])
        post["image_credits"] = [images.credit(c) for _, _, c in found]
        src.write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"rendered {len(names)} slides for {r['id']} with {len(found)} photo(s)")
        if not found:
            print(f"  no photo for {r['id']} (query: {post.get('image_query')!r}); reasons: {images.ERRORS[-3:] or 'no relevant licensed result'}")
    with open(QUEUE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
