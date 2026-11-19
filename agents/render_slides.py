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
import elements  # noqa: E402
import infographics  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QUEUE, CONTENT, MEDIA = ROOT / "queue.csv", ROOT / "content", ROOT / "media"
FONT_DIR = ROOT / "assets" / "fonts"
FIELDS = ["id", "scheduled_at", "media_urls", "caption", "status", "result", "posted_at"]
W, H = 1080, 1350
BG, TEXT, MUTED = (17, 17, 26), (246, 244, 250), (190, 184, 204)  # BG/TEXT/MUTED: legacy dark theme names, still used by fade()
WHITE, INK, SOFT = (255, 255, 255), (34, 34, 42), (92, 92, 112)
LAV, LAV_D, CARD, CALL = (233, 233, 244), (189, 185, 230), (234, 234, 242), (230, 230, 253)
PURPLE, CHEV, YELLOW, NAVY = (107, 99, 200), (139, 139, 245), (255, 235, 0), (31, 26, 92)
AMBER, MAGENTA, TEAL = (255, 196, 61), (224, 96, 168), (72, 205, 190)
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


# ---------------------------------------------------------------- light design system (white, lavender, ink)
def rich_lines(d, text, size, max_w):
    """Words with a bold flag. `**key phrase**` is bold and underlined; with no markup, the first short clause is."""
    if "**" not in text:
        m = re.match(r"^(.{12,60}?)([,:;.]|\s[-\u2013\u2014]\s)\s", text)
        if m and 2 <= len(m.group(1).split()) <= 8:
            text = "**" + m.group(1) + "**" + text[len(m.group(1)):]
    words, bold = [], False
    for part in re.split(r"(\*\*)", text):
        if part == "**":
            bold = not bold
        else:
            toks = part.split()
            if toks and words and not part[:1].isspace() and toks[0][:1] in ",.;:!?)":
                words[-1] = (words[-1][0] + toks.pop(0), words[-1][1])  # punctuation sticks to the word before it
            words += [(w, bold) for w in toks]
    reg, bld = font("Regular", size), font("Bold", size)
    lines, cur, cw = [], [], 0.0
    space = d.textlength(" ", font=reg)
    for w, b in words:
        ww = d.textlength(w, font=bld if b else reg)
        if cur and cw + space + ww > max_w:
            lines.append(cur)
            cur, cw = [], 0.0
        cw += (space if cur else 0) + ww
        cur.append((w, b))
    return lines + ([cur] if cur else [])


def draw_rich(d, lines, x, y, size, gap, color=INK):
    reg, bld = font("Regular", size), font("Bold", size)
    space = d.textlength(" ", font=reg)
    for ln in lines:
        cx = x
        for k, (w, b) in enumerate(ln):
            f = bld if b else reg
            d.text((cx, y), w, font=f, fill=color)
            wl = d.textlength(w, font=f)
            if b:
                end = cx + wl + (space if k + 1 < len(ln) and ln[k + 1][1] else 0)
                d.line([cx, y + int(size * 1.24), end, y + int(size * 1.24)], fill=color, width=3)
            cx += wl + space
        y += gap
    return y


def header(img, idx, total, last=False):
    d = ImageDraw.Draw(img)
    d.text((50, 56), HANDLE, font=font("Bold", 32), fill=INK)
    if last:
        return
    lab = f"{idx}/{total - 1}"
    d.text((W - 50 - 60 - d.textlength(lab, font=font("Regular", 27)), 62), lab, font=font("Regular", 27), fill=SOFT)
    cx, cy = W - 75, 76
    d.ellipse([cx - 25, cy - 25, cx + 25, cy + 25], fill=CHEV)
    for dx in (-9, 3):  # double chevron = swipe hint
        d.line([cx + dx - 5, cy - 11, cx + dx + 6, cy, cx + dx - 5, cy + 11], fill=WHITE, width=5, joint="curve")


def credit_chip(img, text, x, y_bottom):
    if not text:
        return
    d = ImageDraw.Draw(img)
    f = font("Regular", 19)
    t = text[:70]
    w = int(d.textlength(t, font=f)) + 28
    d.rounded_rectangle([x, y_bottom - 40, x + w, y_bottom - 8], radius=16, fill=WHITE)
    d.text((x + 14, y_bottom - 35), t, font=f, fill=SOFT)


def art_from(photo, art_img):
    """Returns a function (w, h) -> RGBA picture for either a photo or a drawn infographic, or None."""
    if art_img is not None:
        return lambda w, h: art_img(w, h)
    if photo:
        return lambda w, h: rounded(photo_fit(photo, w, h, cy=0.5), 40)
    return None


def icon_for(s):
    name = s.get("icon")
    return name if name in elements.ICONS else elements.pick_icon((s.get("title") or "") + " " + (s.get("body") or ""))


def put(img, el, x, y):
    img.paste(el, (int(x), int(y)), el)


def slide_fact(s, idx, total, art_fn, credit_text=None):
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    header(img, idx, total)
    ic = icon_for(s)
    st = elements.rotated(elements.sticker(ic, 132), 8)
    put(img, st, W - 50 - st.width + 24, 40)                    # sticker in the free band right of the big number
    put(img, elements.sparkle(54), 520, 150)
    put(img, elements.sparkle(34, (243, 190, 220)), 610, 232)
    put(img, elements.sparkle(40, (189, 185, 230)), 400, 214)
    d = ImageDraw.Draw(img)
    m, tw, bottom = 50, W - 100, 1296
    d.text((m - 4, 112), f"{idx:02d}", font=font("Bold", 150), fill=LAV_D)
    hs = 66 if len(wrap(d, s["title"], font("Bold", 66), tw)) <= 3 else 56
    hl = wrap(d, s["title"], font("Bold", hs), tw)
    y = 276
    for ln in hl:
        d.text((m, y), ln, font=font("Bold", hs), fill=INK)
        y += int(hs * 1.2)
    y += 14
    take = (s.get("takeaway") or "").strip()
    bs = 32 if art_fn else 42

    def blocks(bs):
        bl = rich_lines(d, s.get("body") or "", bs, tw - 70)
        card_h = len(bl) * int(bs * 1.45) + 56 if bl else 0
        tl = wrap(d, take, font("Bold", 31), tw - 60) if take else []
        call_h = len(tl) * 44 + 40 if tl else 0
        return bl, card_h, tl, call_h
    bl, card_h, tl, call_h = blocks(bs)
    art_h = bottom - y - card_h - 24 - ((call_h + 24 + 26) if call_h else 0)
    if art_fn and art_h < 300 and bs > 28:
        bs = 28
        bl, card_h, tl, call_h = blocks(bs)
        art_h = bottom - y - card_h - 24 - ((call_h + 24 + 26) if call_h else 0)
    if art_fn and art_h >= 300:
        ah = min(art_h, 540)
        art = art_fn(tw, ah)
        img.paste(art, (m, y), art)
        credit_chip(img, credit_text, m + 18, y + ah - 4)
        y += ah + 24
    elif not art_fn:  # typographic slide: a big doodle fills the free space between headline and text
        free = bottom - y - card_h - ((call_h + 50) if call_h else 0)
        if free > 260:
            size = min(440, free - 50)
            big = elements.sticker(ic, size)
            put(img, big, (W - big.width) // 2, y + (free - big.height) // 2 + 10)
            put(img, elements.sparkle(60), (W - big.width) // 2 - 70, y + 30)
            put(img, elements.sparkle(44, (243, 190, 220)), (W + big.width) // 2 + 10, y + free - 90)
            y += free
        else:
            y += max(0, int(free * 0.35))
    d = ImageDraw.Draw(img)
    if bl:
        d.rounded_rectangle([m, y, m + tw, y + card_h], radius=24, fill=CARD)
        draw_rich(d, bl, m + 35, y + 26, bs, int(bs * 1.45))
        y += card_h + 24
    if tl:
        d.rectangle([m, y, m + int(tw * 0.92), y + call_h], fill=CALL)
        d.polygon([(m, y + call_h), (m + 70, y + call_h), (m, y + call_h + 28)], fill=CALL)
        yy = y + 20
        for ln in tl:
            d.text((m + 30, yy), ln, font=font("Bold", 31), fill=NAVY)
            yy += 44
    return img


def slide_cover(s, idx, total, art_fn, credit_text=None):
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    d.ellipse([-260, 700, W + 260, 1900], fill=LAV)  # lavender arc rising behind the picture
    d.text((W // 2, 80), HANDLE, font=font("Bold", 34), fill=INK, anchor="mm")
    fnt = font("Bold", 84 if len(s["title"]) < 55 else 70)
    lines = wrap(d, s["title"], fnt, W - 120)
    step = int(fnt.size * 1.14)
    y = 150
    for ln in lines:
        d.text((W // 2, y), ln, font=fnt, fill=INK, anchor="ma")
        y += step
    pill = "FAKTA SAINS  \u2022  geser untuk lanjut"
    pf = font("Medium", 27)
    pw = int(d.textlength(pill, font=pf)) + 60
    py = y + 26
    d.rounded_rectangle([(W - pw) // 2, py, (W + pw) // 2, py + 62], radius=31, fill=WHITE, outline=INK, width=2)
    d.text((W // 2, py + 31), pill, font=pf, fill=INK, anchor="mm")
    top = py + 100
    ah = H - top - 70
    aw = W - 140
    d = ImageDraw.Draw(img)
    if art_fn:
        art = art_fn(aw, ah)
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle([70 + 14, top + 18, 70 + aw + 14, top + ah + 18], radius=44, fill=PURPLE + (70,))
        img = Image.alpha_composite(img.convert("RGBA"), halo.filter(ImageFilter.GaussianBlur(2))).convert("RGB")
        img.paste(art, (70, top), art)
        credit_chip(img, credit_text, 90, top + ah - 4)
    else:  # no picture: a bold question-mark sticker as the hero
        cx, cy, r = W // 2, top + ah // 2, min(ah, aw) // 2 - 20
        d.ellipse([cx - r - 16, cy - r - 16, cx + r + 16, cy + r + 16], fill=CHEV)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE, outline=INK, width=6)
        d.text((cx, cy - 10), "?", font=font("Bold", int(r * 1.5)), fill=PURPLE, anchor="mm")
    d = ImageDraw.Draw(img)
    conf = elements.confetti(W, H, seed=sum(ord(c) for c in s["title"]), n=16, avoid=(40, 130, 1040, 560))
    img = Image.alpha_composite(img.convert("RGBA"), conf).convert("RGB")
    st = elements.rotated(elements.sticker(icon_for(s), 210), 10)
    put(img, st, W - st.width - 10, top - 70)
    mk = elements.rotated(elements.mascot(230, "wow"), -8)
    put(img, mk, 6, top - 60)
    return img


def _icon(d, kind, cx, cy, r, fill):
    if kind == "plane":
        d.polygon([(cx - r, cy - r * 0.1), (cx + r, cy - r), (cx + r * 0.3, cy + r), (cx - r * 0.05, cy + r * 0.2)], fill=fill)
    elif kind == "bookmark":
        d.polygon([(cx - r * 0.6, cy - r), (cx + r * 0.6, cy - r), (cx + r * 0.6, cy + r), (cx, cy + r * 0.45), (cx - r * 0.6, cy + r)], fill=fill)
    elif kind == "comment":
        d.rounded_rectangle([cx - r, cy - r * 0.8, cx + r, cy + r * 0.55], radius=int(r * 0.35), fill=fill)
        d.polygon([(cx - r * 0.5, cy + r * 0.4), (cx - r * 0.55, cy + r), (cx + r * 0.05, cy + r * 0.4)], fill=fill)
    elif kind == "plus":
        d.rectangle([cx - r, cy - r * 0.18, cx + r, cy + r * 0.18], fill=fill)
        d.rectangle([cx - r * 0.18, cy - r, cx + r * 0.18, cy + r], fill=fill)


CTA_COPY = [
    ("Seru, kan?", "Masih banyak fakta yang bikin melongo.",
     ("Kirim ke temanmu", "yang sering bilang \"masa sih?\""), ("Simpan dulu", "biar tidak lupa saat ditanya orang"),
     ("Tulis di komentar", "fakta apa yang mau kamu bahas?")),
    ("Baru tahu, ya?", "Besok ada fakta baru yang tak kalah aneh.",
     ("Bagikan ke grup", "supaya ada yang ikut kaget"), ("Simpan postingan ini", "untuk bahan ngobrol nanti"),
     ("Komentar angka 1-10", "seberapa kaget kamu tadi?")),
    ("Jangan berhenti di sini", "Tiap hari ada fakta sains yang jarang dibahas.",
     ("Kirim ke satu orang", "yang pasti penasaran"), ("Simpan buat nanti", "kamu pasti ingin baca lagi"),
     ("Tebak di komentar", "fakta besok soal apa?")),
]


def slide_cta(s, idx, total):
    """Call to action: follow, share, save, comment. Copy rotates between three variants."""
    copy = CTA_COPY[sum(ord(c) for c in (s.get("title") or "")) % len(CTA_COPY)]
    img = Image.new("RGB", (W, H), LAV)
    d = ImageDraw.Draw(img)
    d.ellipse([W - 420, -200, W + 200, 420], fill=(243, 213, 230))
    tf = font("Bold", 84 if len(copy[0]) < 16 else 70)
    ty = 100
    for ln in wrap(d, copy[0], tf, 690):
        d.text((60, ty), ln, font=tf, fill=NAVY)
        ty += int(tf.size * 1.12)
    draw_block(d, copy[1], font("Medium", 36), 62, ty + 14, 660, SOFT, 1.35)
    top = 390
    d.rounded_rectangle([50, top, W - 50, top + 200], radius=48, fill=PURPLE)
    d.ellipse([84, top + 38, 208, top + 162], fill=WHITE)
    d.text((146, top + 100), "A", font=font("Bold", 66), fill=PURPLE, anchor="mm")
    d.text((238, top + 44), HANDLE, font=font("Bold", 50), fill=WHITE)
    d.text((238, top + 112), "Fakta sains yang bikin melongo", font=font("Medium", 28), fill=(224, 222, 250))
    bw = 200
    d.rounded_rectangle([W - 50 - bw - 28, top + 58, W - 78, top + 142], radius=42, fill=YELLOW)
    _icon(d, "plus", W - 78 - bw + 44, top + 100, 15, INK)
    d.text((W - 78 - bw // 2 + 18, top + 100), "IKUTI", font=font("Bold", 33), fill=INK, anchor="mm")
    y = top + 250
    for kind, (head, sub), col in (("plane", copy[2], PURPLE), ("bookmark", copy[3], (31, 175, 115)), ("comment", copy[4], (233, 100, 160))):
        d.rounded_rectangle([50, y, W - 50, y + 160], radius=80, fill=WHITE)
        d.rounded_rectangle([76, y + 26, 184, y + 134], radius=30, fill=col)
        _icon(d, kind, 130, y + 80, 28, WHITE)
        d.text((216, y + 32), head, font=font("Bold", 42), fill=NAVY)
        d.text((216, y + 94), sub, font=font("Regular", 29), fill=SOFT)
        y += 190
    d.text((W // 2, H - 70), "Satu fakta baru setiap hari", font=font("Medium", 28), fill=SOFT, anchor="mm")
    mk = elements.rotated(elements.mascot(250, "happy"), 8)
    put(img, mk, W - mk.width - 20, 60)
    put(img, elements.sparkle(60), W - 430, 120)
    put(img, elements.sparkle(40, (255, 255, 255)), W - 380, 300)
    return img


def render_post(post, out_dir, photos, slide_photos=None):
    """Writes <id>-<n>.jpg for every slide. `photos` = list of (bytes, credit_text) of the main subject: the first is the
    cover, the rest go to the fact slides in order. `slide_photos` maps a slide index to its own (bytes, credit_text).
    A slide with a `viz` spec gets that drawn infographic; slides left with nothing get the typographic design."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slides, total, names = post["slides"][:10], len(post["slides"][:10]), []
    spare, own = list(photos or []), dict(slide_photos or {})
    for i, s in enumerate(slides):
        art_img = (lambda spec: (lambda w, h: infographics.render(spec, w, h)))(s["viz"]) if s.get("viz") and infographics.valid(s["viz"]) else None
        if s["type"] == "hook":
            pick = own.get(i) or (spare.pop(0) if spare else None)
            if not pick and not art_img:  # no photo: borrow the most striking infographic of the post as the hero
                hero = next((x["viz"] for x in slides if x.get("viz") and x["viz"].get("kind") in ("stat", "dots") and infographics.valid(x["viz"])), None)
                art_img = (lambda spec: (lambda w, h: infographics.render(spec, w, h)))(hero) if hero else None
            fn = art_from(pick[0] if pick else None, art_img)
            img = slide_cover(s, i, total, fn, pick[1] if pick and art_img is None else None)
        elif s["type"] == "cta":
            img = slide_cta(s, i, total)
        else:
            pick = None if art_img else (own.get(i) or (spare.pop(0) if spare else None))
            img = slide_fact(s, i, total, art_from(pick[0] if pick else None, art_img), pick[1] if pick else None)
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


def render_reel_row(r, post):
    """Renders a Reel (mp4) for this queue row. A failure leaves media_urls empty, so the next run simply tries again."""
    import reel
    base = (os.environ.get("REEL_MEDIA_BASE_URL") or os.environ.get("MEDIA_BASE_URL", "")).rstrip("/")
    try:
        photos = reel.gather_beat_photos(post["reel"]["beats"])
        info = reel.render_reel(post, MEDIA / f"{post['id']}.mp4", photos=photos)
    except Exception as e:  # noqa: BLE001 - network (voice service, photos) or encoder trouble: retry on the next run
        print(f"reel {r['id']} NOT rendered, will retry: {type(e).__name__}: {str(e)[:160]}")
        return
    credits = [t.replace("Foto ilustrasi: ", "") for _, t in photos.values()]
    r["media_urls"] = f"{base}/{post['id']}.mp4"
    r["caption"] = caption_with_credits(r["caption"], credits)
    post["image_credits"] = credits
    (CONTENT / f"{post['id']}.json").write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        import reel_sheet
        (CONTENT / f"{post['id']}-produksi.md").write_text(reel_sheet.sheet(post, info.get("timings"), credits), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - the sheet is documentation: never block a finished video
        print(f"production sheet skipped: {type(e).__name__}: {e}")
    print(f"rendered reel {r['id']}: {info['seconds']} s, {info['scenes']} scenes, {len(photos)} photo(s)")


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
        if post.get("format") == "reel":
            render_reel_row(r, post)
            continue
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
