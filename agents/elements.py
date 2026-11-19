"""Illustration elements drawn by code (no AI, no files to download): flat doodles with thick ink outlines, sticker
borders, a small mascot, sparkles and confetti. Used on carousel slides and Reel scenes so every slide has
something to look at besides text.

  icon(name, size)          RGBA doodle        (names: ICONS)
  sticker(name, size)       the same with a white sticker border and a soft shadow
  mascot(size, mood)        "Faktu", the round purple fact-bot (moods: wow, happy, think)
  sparkle(size, color)      4-point star
  confetti(w, h, seed, n)   RGBA layer of small coloured shapes
  pick_icon(text)           best icon name for a slide's words (keyword match), never fails
"""
import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

INK = (30, 30, 40)
YELLOW, ORANGE, PINK, PURPLE = (255, 214, 10), (255, 150, 50), (240, 110, 170), (107, 99, 200)
TEAL, BLUE, RED, GREEN = (40, 190, 160), (120, 170, 255), (230, 60, 60), (110, 190, 100)
LAV, WHITE, GREY, PALEBLUE = (233, 233, 244), (255, 255, 255), (190, 190, 205), (214, 230, 255)
SS = 4  # supersampling factor: drawn 4x larger, then reduced, so the edges are smooth

ICONS = ["bulb", "magnifier", "atom", "flask", "cloud", "gas", "fish", "bacteria", "dna", "planet", "rocket",
         "flame", "droplet", "bolt", "leaf", "heart", "burst", "sparkle", "clock", "globe"]

KEYWORDS = [
    ("gas", ("kentut", "gas ", "bau", "belerang", "sulfur", "sulfida", "asap")),
    ("bacteria", ("bakteri", "kuman", "mikroba", "virus", "e. coli", "usus")),
    ("dna", ("dna", "gen ", "genom", "genetik", "protein", "kromosom")),
    ("fish", ("laut", "ikan", "paus", "gurita", "hiu", "teripang", "samudra", "karang")),
    ("planet", ("planet", "bintang", "venus", "mars", "bulan", "luar angkasa", "galaksi", "nebula", "matahari", "semesta")),
    ("rocket", ("roket", "antariksa", "satelit", "nasa")),
    ("flame", ("api", "panas", "kalor", "terbakar", "gunung berapi", "suhu")),
    ("droplet", ("air ", "hujan", "tetes", "cairan", "embun", "es ", "lembap")),
    ("bolt", ("listrik", "energi", "petir", "saraf", "sinyal", "baterai")),
    ("leaf", ("tumbuhan", "tanaman", "daun", "pohon", "hutan", "bunga", "fotosintesis")),
    ("heart", ("jantung", "darah", "cinta", "napas")),
    ("atom", ("atom", "molekul", "partikel", "fisika", "kuantum")),
    ("flask", ("kimia", "reaksi", "zat", "senyawa", "larutan", "laboratorium")),
    ("clock", ("waktu", "jam ", "detik", "tahun", "usia", "tidur")),
    ("globe", ("bumi", "dunia", "negara", "iklim", "benua")),
    ("magnifier", ("peneliti", "studi", "ilmuwan", "penelitian", "temuan", "misteri")),
]


def pick_icon(text, fallback="bulb"):
    t = " " + (text or "").lower() + " "
    for name, keys in KEYWORDS:
        if any(k in t for k in keys):
            return name
    return fallback


# ------------------------------------------------------------------------------------------------- drawing core
class _C:
    def __init__(self, size):
        self.s = size * SS
        self.img = Image.new("RGBA", (self.s, self.s), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.lw = max(2, int(self.s * 0.04))

    def p(self, x, y):
        return (x * self.s, y * self.s)

    def box(self, x0, y0, x1, y1):
        return [x0 * self.s, y0 * self.s, x1 * self.s, y1 * self.s]

    def ellipse(self, b, fill, outline=True):
        self.d.ellipse(self.box(*b), fill=fill, outline=INK if outline else None, width=self.lw)

    def poly(self, pts, fill, outline=True):
        pts = [self.p(*q) for q in pts]
        self.d.polygon(pts, fill=fill)
        if outline:
            self.d.line(pts + [pts[0]], fill=INK, width=self.lw, joint="curve")

    def line(self, pts, width=1.0, fill=INK):
        self.d.line([self.p(*q) for q in pts], fill=fill, width=int(self.lw * width), joint="curve")

    def rrect(self, b, r, fill):
        self.d.rounded_rectangle(self.box(*b), radius=r * self.s, fill=fill, outline=INK, width=self.lw)

    def merged(self, shapes, fill):
        """Outline of a union of ellipses: draw all outlined, then all fills, so inner outlines vanish."""
        e = self.lw / self.s
        for b in shapes:
            self.d.ellipse(self.box(b[0] - e / 2, b[1] - e / 2, b[2] + e / 2, b[3] + e / 2), fill=INK)
        for b in shapes:
            self.d.ellipse(self.box(*b), fill=fill)

    def done(self, size):
        return self.img.resize((size, size), Image.LANCZOS)


def _bulb(c):
    for a in range(-2, 3):
        ang = math.radians(-90 + a * 34)
        c.line([(0.5 + 0.38 * math.cos(ang), 0.46 + 0.38 * math.sin(ang)), (0.5 + 0.45 * math.cos(ang), 0.46 + 0.45 * math.sin(ang))], 1.0)
    c.ellipse((0.2, 0.2, 0.8, 0.76), YELLOW)
    c.poly([(0.36, 0.66), (0.64, 0.66), (0.6, 0.82), (0.4, 0.82)], YELLOW)
    c.rrect((0.38, 0.82, 0.62, 0.89), 0.03, GREY)
    c.rrect((0.42, 0.89, 0.58, 0.95), 0.03, GREY)
    c.line([(0.42, 0.66), (0.46, 0.5), (0.5, 0.6), (0.54, 0.5), (0.58, 0.66)], 0.8)


def _magnifier(c):
    c.line([(0.6, 0.6), (0.9, 0.9)], 2.6)
    c.line([(0.6, 0.6), (0.9, 0.9)], 1.4, ORANGE)
    c.ellipse((0.08, 0.08, 0.7, 0.7), PALEBLUE)
    c.d.arc(c.box(0.17, 0.17, 0.52, 0.52), 200, 280, fill=WHITE, width=c.lw)


def _atom(c):
    for ang in (0, 60, 120):
        layer = Image.new("RGBA", (c.s, c.s), (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse(c.box(0.06, 0.32, 0.94, 0.68), outline=INK, width=c.lw)
        c.img.alpha_composite(layer.rotate(ang, resample=Image.BICUBIC))
    c.d = ImageDraw.Draw(c.img)
    c.ellipse((0.4, 0.4, 0.6, 0.6), PINK)
    for (x, y) in ((0.12, 0.5), (0.74, 0.2), (0.74, 0.8)):
        c.ellipse((x, y - 0.06, x + 0.12, y + 0.06), YELLOW)


def _flask(c):
    c.poly([(0.38, 0.1), (0.62, 0.1), (0.62, 0.4), (0.9, 0.88), (0.1, 0.88), (0.38, 0.4)], WHITE)
    c.poly([(0.27, 0.62), (0.73, 0.62), (0.9, 0.88), (0.1, 0.88)], TEAL, outline=False)
    c.line([(0.38, 0.1), (0.38, 0.4), (0.1, 0.88), (0.9, 0.88), (0.62, 0.4), (0.62, 0.1), (0.38, 0.1)])
    c.rrect((0.32, 0.05, 0.68, 0.13), 0.03, GREY)
    for (x, y, r) in ((0.45, 0.5, 0.04), (0.56, 0.42, 0.03), (0.5, 0.72, 0.05)):
        c.ellipse((x - r, y - r, x + r, y + r), WHITE)


def _cloud(c):
    c.merged([(0.06, 0.42, 0.46, 0.84), (0.26, 0.2, 0.7, 0.7), (0.5, 0.34, 0.94, 0.84), (0.2, 0.5, 0.8, 0.84)], LAV)


def _gas(c):
    for k, x in enumerate((0.3, 0.5, 0.7)):
        pts = [(x + 0.04 * math.sin(i / 2.2 + k), 0.05 + i * 0.03) for i in range(10)]
        c.line(pts, 1.1, GREEN)
    c.merged([(0.06, 0.58, 0.4, 0.92), (0.24, 0.44, 0.62, 0.86), (0.46, 0.52, 0.9, 0.92), (0.2, 0.62, 0.8, 0.92)], (205, 232, 180))


def _fish(c):
    c.poly([(0.68, 0.5), (0.96, 0.24), (0.96, 0.76)], ORANGE)
    c.ellipse((0.08, 0.28, 0.76, 0.72), BLUE)
    c.ellipse((0.2, 0.4, 0.3, 0.5), WHITE)
    c.ellipse((0.23, 0.43, 0.28, 0.48), INK, outline=False)
    c.d.arc(c.box(0.36, 0.38, 0.62, 0.62), 280, 80, fill=INK, width=c.lw)


def _bacteria(c):
    for (x0, y0, x1, y1) in ((0.2, 0.15, 0.08, 0.02), (0.5, 0.1, 0.5, 0.0), (0.82, 0.18, 0.95, 0.06),
                             (0.2, 0.85, 0.08, 0.98), (0.8, 0.85, 0.95, 0.97)):
        c.line([(x0, y0), ((x0 + x1) / 2 + 0.03, (y0 + y1) / 2), (x1, y1)], 0.8)
    c.rrect((0.12, 0.16, 0.88, 0.84), 0.3, GREEN)
    for (x, y, r) in ((0.35, 0.4, 0.06), (0.62, 0.55, 0.07), (0.45, 0.66, 0.04)):
        c.ellipse((x - r, y - r, x + r, y + r), (170, 220, 140))
    c.ellipse((0.3, 0.28, 0.36, 0.34), INK, outline=False)
    c.ellipse((0.5, 0.28, 0.56, 0.34), INK, outline=False)


def _dna(c):
    n = 14
    a = [(0.5 + 0.28 * math.sin(i / n * 2 * math.pi * 1.5), 0.06 + 0.88 * i / n) for i in range(n + 1)]
    b = [(0.5 - 0.28 * math.sin(i / n * 2 * math.pi * 1.5), 0.06 + 0.88 * i / n) for i in range(n + 1)]
    for i in range(1, n, 2):
        c.line([a[i], b[i]], 0.6, PINK)
    c.line(a, 1.3, PURPLE)
    c.line(b, 1.3, TEAL)


def _planet(c):
    c.ellipse((0.2, 0.2, 0.8, 0.8), ORANGE)
    c.d.arc(c.box(0.3, 0.3, 0.55, 0.55), 180, 270, fill=(255, 190, 120), width=c.lw)
    layer = Image.new("RGBA", (c.s, c.s), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(c.box(0.02, 0.38, 0.98, 0.62), outline=INK, width=int(c.lw * 1.6))
    c.img.alpha_composite(layer.rotate(20, resample=Image.BICUBIC))
    c.d = ImageDraw.Draw(c.img)


def _rocket(c):
    c.poly([(0.5, 0.72), (0.38, 0.95), (0.5, 0.85), (0.62, 0.95)], ORANGE)
    c.poly([(0.3, 0.5), (0.1, 0.78), (0.34, 0.7)], RED)
    c.poly([(0.7, 0.5), (0.9, 0.78), (0.66, 0.7)], RED)
    c.ellipse((0.3, 0.06, 0.7, 0.8), WHITE)
    c.poly([(0.34, 0.26), (0.5, 0.0), (0.66, 0.26)], RED, outline=True)
    c.ellipse((0.4, 0.34, 0.6, 0.54), PALEBLUE)


def _flame(c):
    c.poly([(0.5, 0.04), (0.64, 0.26), (0.84, 0.46), (0.82, 0.74), (0.66, 0.92), (0.34, 0.92), (0.18, 0.74), (0.18, 0.5),
            (0.34, 0.34), (0.38, 0.14)], ORANGE)
    c.poly([(0.5, 0.42), (0.62, 0.6), (0.6, 0.8), (0.5, 0.88), (0.4, 0.8), (0.38, 0.6)], YELLOW)


def _droplet(c):
    c.poly([(0.5, 0.04), (0.74, 0.42), (0.82, 0.62)] + [(0.5 + 0.32 * math.cos(math.radians(a)), 0.64 + 0.3 * math.sin(math.radians(a)))
           for a in range(20, 161, 20)] + [(0.18, 0.62), (0.26, 0.42)], BLUE)
    c.d.arc(c.box(0.3, 0.5, 0.5, 0.8), 120, 200, fill=WHITE, width=c.lw)


def _bolt(c):
    c.poly([(0.58, 0.04), (0.2, 0.55), (0.46, 0.55), (0.36, 0.96), (0.8, 0.4), (0.54, 0.4)], YELLOW)


def _leaf(c):
    c.line([(0.2, 0.9), (0.4, 0.62)], 1.4)
    c.poly([(0.16, 0.86), (0.2, 0.4), (0.5, 0.12), (0.88, 0.1), (0.9, 0.5), (0.62, 0.84), (0.3, 0.88)], GREEN)
    c.line([(0.2, 0.85), (0.72, 0.28)], 0.8)


def _heart(c):
    c.ellipse((0.1, 0.16, 0.54, 0.6), RED)
    c.ellipse((0.46, 0.16, 0.9, 0.6), RED)
    c.poly([(0.13, 0.5), (0.87, 0.5), (0.5, 0.92)], RED, outline=False)
    c.line([(0.14, 0.52), (0.5, 0.92), (0.86, 0.52)])
    c.d.arc(c.box(0.18, 0.24, 0.34, 0.4), 190, 260, fill=WHITE, width=c.lw)


def _burst(c):
    pts = []
    for i in range(24):
        r = 0.48 if i % 2 == 0 else 0.34
        a = i / 24 * 2 * math.pi
        pts.append((0.5 + r * math.cos(a), 0.5 + r * math.sin(a)))
    c.poly(pts, ORANGE)
    c.rrect((0.44, 0.22, 0.56, 0.58), 0.05, WHITE)
    c.ellipse((0.43, 0.66, 0.57, 0.8), WHITE)


def _sparkle(c):
    pts = []
    for i in range(8):
        r = 0.48 if i % 2 == 0 else 0.13
        a = i / 8 * 2 * math.pi - math.pi / 2
        pts.append((0.5 + r * math.cos(a), 0.5 + r * math.sin(a)))
    c.poly(pts, YELLOW)


def _clock(c):
    c.ellipse((0.08, 0.08, 0.92, 0.92), WHITE)
    for k in range(12):
        a = k / 12 * 2 * math.pi
        c.line([(0.5 + 0.34 * math.cos(a), 0.5 + 0.34 * math.sin(a)), (0.5 + 0.4 * math.cos(a), 0.5 + 0.4 * math.sin(a))], 0.6)
    c.line([(0.5, 0.5), (0.5, 0.24)], 1.2)
    c.line([(0.5, 0.5), (0.68, 0.6)], 1.2, PURPLE)
    c.ellipse((0.46, 0.46, 0.54, 0.54), INK)


def _globe(c):
    c.ellipse((0.08, 0.08, 0.92, 0.92), BLUE)
    c.poly([(0.22, 0.3), (0.4, 0.22), (0.46, 0.4), (0.34, 0.56), (0.24, 0.48)], GREEN)
    c.poly([(0.56, 0.5), (0.78, 0.44), (0.82, 0.66), (0.62, 0.8)], GREEN)
    c.d.arc(c.box(0.08, 0.08, 0.92, 0.92), 0, 360, fill=INK, width=c.lw)


_DRAW = {"bulb": _bulb, "magnifier": _magnifier, "atom": _atom, "flask": _flask, "cloud": _cloud, "gas": _gas,
         "fish": _fish, "bacteria": _bacteria, "dna": _dna, "planet": _planet, "rocket": _rocket, "flame": _flame,
         "droplet": _droplet, "bolt": _bolt, "leaf": _leaf, "heart": _heart, "burst": _burst, "sparkle": _sparkle,
         "clock": _clock, "globe": _globe}


@lru_cache(maxsize=256)
def icon(name, size=300):
    name = name if name in _DRAW else "bulb"
    c = _C(size)
    _DRAW[name](c)
    return c.done(size)


@lru_cache(maxsize=256)
def sticker(name, size=300, border=12):
    """The doodle with a white sticker outline and a soft shadow."""
    ic = icon(name, size)
    pad = border * 2 + 14
    big = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
    big.paste(ic, (pad, pad), ic)
    alpha = big.getchannel("A").filter(ImageFilter.MaxFilter(border * 2 + 1))
    out = Image.new("RGBA", big.size, (0, 0, 0, 0))
    shadow = Image.new("RGBA", big.size, (60, 50, 110, 0))
    shadow.putalpha(alpha.point(lambda v: int(v * 0.30)))
    out.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(7)), (0, 7))
    white = Image.new("RGBA", big.size, WHITE + (0,))
    white.putalpha(alpha)
    out.alpha_composite(white)
    out.alpha_composite(big)
    return out


@lru_cache(maxsize=64)
def mascot(size=300, mood="wow"):
    """Faktu: a round purple fact-bot with an antenna light bulb."""
    c = _C(size)
    c.line([(0.5, 0.2), (0.5, 0.06)], 1.2)
    c.ellipse((0.42, 0.0, 0.58, 0.14), YELLOW)
    c.ellipse((0.1, 0.16, 0.9, 0.94), PURPLE)
    c.ellipse((0.3, 0.34, 0.47, 0.58), WHITE)
    c.ellipse((0.53, 0.34, 0.7, 0.58), WHITE)
    look = {"wow": 0.0, "happy": 0.0, "think": -0.04}.get(mood, 0.0)
    for x in (0.385, 0.615):
        c.ellipse((x - 0.05 + look, 0.41, x + 0.05 + look, 0.53), INK)
        c.ellipse((x - 0.02 + look, 0.43, x + 0.01 + look, 0.46), WHITE, outline=False)
    c.ellipse((0.17, 0.58, 0.3, 0.68), (255, 160, 200), outline=False)
    c.ellipse((0.7, 0.58, 0.83, 0.68), (255, 160, 200), outline=False)
    if mood == "wow":
        c.ellipse((0.42, 0.66, 0.58, 0.84), INK)
        c.ellipse((0.45, 0.76, 0.55, 0.83), PINK, outline=False)
    elif mood == "think":
        c.line([(0.42, 0.74), (0.58, 0.72)], 1.0)
    else:
        c.d.arc(c.box(0.36, 0.6, 0.64, 0.82), 20, 160, fill=INK, width=c.lw)
    c.line([(0.28, 0.28), (0.4, 0.3)], 1.0)
    c.line([(0.6, 0.3), (0.72, 0.26)] if mood != "think" else [(0.6, 0.26), (0.72, 0.22)], 1.0)
    return c.done(size)


@lru_cache(maxsize=64)
def sparkle(size=80, color=YELLOW):
    c = _C(size)
    pts = []
    for i in range(8):
        r = 0.5 if i % 2 == 0 else 0.12
        a = i / 8 * 2 * math.pi - math.pi / 2
        pts.append((0.5 + r * math.cos(a), 0.5 + r * math.sin(a)))
    c.poly(pts, color, outline=False)
    return c.done(size)


def confetti(w, h, seed=1, n=18, avoid=None):
    """Transparent layer with small coloured circles, plus signs, squiggles and triangles. `avoid` = (x0, y0, x1, y1)
    is kept clear so the text stays clean."""
    rnd = random.Random(seed)
    layer = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cols = [(189, 185, 230), (243, 190, 220), (255, 226, 80), (150, 220, 205), (170, 200, 255)]
    made = 0
    tries = 0
    while made < n and tries < n * 12:
        tries += 1
        x, y = rnd.randint(30, w - 30), rnd.randint(30, h - 30)
        if avoid and avoid[0] <= x <= avoid[2] and avoid[1] <= y <= avoid[3]:
            continue
        col, s, kind = rnd.choice(cols), rnd.randint(9, 22) * 2, rnd.choice(["dot", "plus", "wave", "tri"])
        X, Y = x * 2, y * 2
        if kind == "dot":
            d.ellipse([X - s, Y - s, X + s, Y + s], fill=col)
        elif kind == "plus":
            d.rectangle([X - s, Y - s // 4, X + s, Y + s // 4], fill=col)
            d.rectangle([X - s // 4, Y - s, X + s // 4, Y + s], fill=col)
        elif kind == "wave":
            pts = [(X + i * s // 2, Y + (s // 3 if i % 2 else -s // 3)) for i in range(-3, 4)]
            d.line(pts, fill=col, width=max(4, s // 3), joint="curve")
        else:
            d.polygon([(X, Y - s), (X + s, Y + s), (X - s, Y + s)], fill=col)
        made += 1
    return layer.resize((w, h), Image.LANCZOS)


def rotated(img, angle):
    return img.rotate(angle, resample=Image.BICUBIC, expand=True)
