#!/usr/bin/env python3
"""Designer step (no AI, no tokens): turns content/<id>.json into 1080x1350 JPEG slides in media/
and fills media_urls in queue.csv for rows that have none. Needs Pillow.

Env: MEDIA_BASE_URL, e.g. https://raw.githubusercontent.com/OWNER/REPO/main/media (repo must be public,
or host media elsewhere and set this to that base URL).
"""
import csv
import json
import os
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
QUEUE, CONTENT, MEDIA = ROOT / "queue.csv", ROOT / "content", ROOT / "media"
W, H = 1080, 1350
BG, FG, ACCENT = (18, 18, 24), (245, 245, 245), (255, 196, 61)
FONTS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def font(size):
    for path in FONTS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_block(d, text, y, size, color, width_chars):
    f = font(size)
    for line in textwrap.wrap(text, width=width_chars) or [""]:
        d.text((90, y), line, font=f, fill=color)
        y += int(size * 1.3)
    return y


def render(slide, idx, total, path):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([90, 110, 190, 118], fill=ACCENT)
    cover = idx == 0
    y = draw_block(d, slide["title"], 400 if cover else 200, 84 if cover else 64, FG, 18 if cover else 24)
    if slide.get("body"):
        draw_block(d, slide["body"], y + 40, 44, (200, 200, 205), 36)
    d.text((90, H - 120), "@alwaysfact_", font=font(36), fill=ACCENT)
    d.text((W - 190, H - 120), f"{idx + 1}/{total}", font=font(36), fill=(150, 150, 158))
    img.convert("RGB").save(path, "JPEG", quality=92)


def main():
    base = os.environ.get("MEDIA_BASE_URL", "").rstrip("/")
    with open(QUEUE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    MEDIA.mkdir(exist_ok=True)
    for r in rows:
        src = CONTENT / f"{r['id']}.json"
        if r["media_urls"] or not src.exists():
            continue
        slides = json.loads(src.read_text(encoding="utf-8"))["slides"][:10]
        names = []
        for i, s in enumerate(slides):
            name = f"{r['id']}-{i + 1}.jpg"
            render(s, i, len(slides), MEDIA / name)
            names.append(name)
        r["media_urls"] = " ".join(f"{base}/{n}" for n in names)
        print(f"rendered {len(names)} slides for {r['id']}")
    with open(QUEUE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "scheduled_at", "media_urls", "caption", "status", "result", "posted_at"])
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
