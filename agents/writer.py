"""Writer: turns one approved topic into a carousel post. The model writes; plain code validates the structure."""
import re
from urllib.parse import urlparse

from common import ROOT, SONNET, extract_json

SLIDE_TYPES = {"hook", "fact", "compare", "chart", "cta"}
VISUAL_SOURCES = {"canva_stock", "unsplash", "pexels", "pixabay", "nasa", "wikimedia", "code_chart"}
LIMITS = {"title": 70, "body": 220, "caption": 1200, "slides": (6, 8), "hashtags": (5, 10), "claims_min": 2}

SYSTEM_TAIL = (
    "\n\nReply with JSON only. Never invent numbers, names, years or URLs. If you do not know a primary source "
    "URL for a claim, leave source_url empty: the claim will then be rejected, which is better than a guess."
)


def build_prompt(topic, feedback=None):
    fb = ""
    if feedback:
        fb = "\n\nThe previous draft was rejected. Fix exactly these problems:\n" + "\n".join(f"- {f}" for f in feedback)
    src = f"\nSource to start from: {topic['url']}" if topic.get("url") else "\n(no source URL yet; find the facts from well-established knowledge and give real source URLs you are certain of)"
    return (
        f"Topic: {topic['title']}\nSummary: {topic.get('summary', '')}\nHook idea: {topic.get('angle', '')}\n"
        f"Pillar: {topic.get('pillar', '')}{src}{fb}\n\n"
        "Write one Instagram carousel in Indonesian. Return a JSON object:\n"
        '{"topic": str, "pillar": str, "hook_options": [str, str] (two different hooks, max 70 chars),\n'
        ' "slides": [{"type": "hook|fact|compare|chart|cta", "title": str (max 70), "body": str (max 220), '
        '"visual": {"source": "canva_stock|unsplash|pexels|pixabay|nasa|wikimedia|code_chart", "keywords": str}}],\n'
        ' "caption": str (max 1200, ends with a "Sumber:" line naming the sources), "hashtags": [5-10 strings],\n'
        ' "claims": [{"text": str, "source_name": str, "source_url": str (https)}],\n'
        ' "music_suggestions": [3 short mood and tempo descriptions, no song titles]}\n'
        "Rules: 6-8 slides; slide 1 type hook and its title equals hook_options[0]; last slide type cta; "
        "every factual statement in the slides or caption appears in claims; at least 2 claims."
    )


_UNITS = {"nol": 0, "satu": 1, "dua": 2, "tiga": 3, "empat": 4, "lima": 5, "enam": 6, "tujuh": 7, "delapan": 8, "sembilan": 9,
          "sepuluh": 10, "sebelas": 11, "seratus": 100, "seribu": 1000,
          "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
          "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
          "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000}


def _word_numbers(text):
    """Numbers written as words (Indonesian and English) as digit strings: 'tujuh' -> 7, 'dua belas' -> 12, 'dua puluh' -> 20."""
    toks = re.findall(r"[a-zA-Z]+", (text or "").lower())
    out = set()
    for i, t in enumerate(toks):
        if t in _UNITS:
            out.add(str(_UNITS[t]))
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t in _UNITS and _UNITS[t] < 10 and nxt == "belas":
            out.add(str(_UNITS[t] + 10))
        if t in _UNITS and _UNITS[t] < 10 and nxt == "puluh":
            base = _UNITS[t] * 10
            out.add(str(base))
            if i + 2 < len(toks) and toks[i + 2] in _UNITS and _UNITS[toks[i + 2]] < 10:
                out.add(str(base + _UNITS[toks[i + 2]]))
    return out


def _numbers(text):
    return {re.sub(r"[.,]", "", n) for n in re.findall(r"\d[\d.,]*\d|\d", text or "")} | _word_numbers(text)


def _viz_texts(viz):
    """Every string of an infographic spec that a reader sees (scale-only fields such as dots.total are not claims)."""
    if not isinstance(viz, dict):
        return []
    out = [str(viz.get(k, "")) for k in ("value", "label", "note", "caption", "unit")]
    for it in viz.get("items") or []:
        out += [str(x) for x in it.values()] if isinstance(it, dict) else [str(it)]
    return out


def viz_problems(p, extra_numbers=()):
    """A number drawn in an infographic must be a number the verified claims (or their verified source quotes) contain.
    The picture can't say more than the claims."""
    claim_nums = set(extra_numbers)
    for c in p.get("claims") or []:
        claim_nums |= _numbers(c.get("text", ""))
    vizs = [("slide %d" % i, s.get("viz")) for i, s in enumerate(p.get("slides") or [], 1) if s.get("viz")]
    vizs += [("beat %d" % i, b.get("viz")) for i, b in enumerate(((p.get("reel") or {}).get("beats")) or [], 1) if isinstance(b, dict) and b.get("viz")]
    bad = []
    for where, v in vizs:
        extra = _numbers(" ".join(_viz_texts(v))) - claim_nums
        if extra:
            bad.append(f"{where}: infographic shows numbers {sorted(extra)} that no claim contains")
    return bad


def _common_problems(p):
    bad = []
    if not p.get("caption") or len(p["caption"]) > LIMITS["caption"]:
        bad.append(f"caption missing or over {LIMITS['caption']} chars")
    elif "sumber" not in p["caption"].lower():
        bad.append("caption must end with a Sumber: line")
    lo, hi = LIMITS["hashtags"]
    if not lo <= len(p.get("hashtags") or []) <= hi:
        bad.append(f"need {lo}-{hi} hashtags")
    claims = p.get("claims") or []
    if len(claims) < LIMITS["claims_min"]:
        bad.append(f"need at least {LIMITS['claims_min']} claims")
    for i, c in enumerate(claims, 1):
        u = urlparse(c.get("source_url", ""))
        if u.scheme != "https" or not u.netloc:
            bad.append(f"claim {i}: source_url must be a real https URL")
    return bad


JARGON = re.compile(r"\b(molekul\w*|protein\w*|enzim\w*|isotop\w*|spektrum|hipotesis|spesies|genus|sulfida|natrium|senyawa|"
                    r"kuantum|anomali|algoritma\w*|paleontolog\w*|arkeolog\w*|geolog\w*|mikrob\w*|genom\w*|neuron\w*|"
                    r"eksoplanet\w*|nebula\w*|supernova|metabolit\w*|spektroskop\w*|fotometri\w*|mineral\w*|kristal\w*|"
                    r"konvergen\w*|epigenetik\w*|magnetosfer\w*|heliosfer\w*|hidrogen sulfida)\b", re.I)
# a technical word in slide text is fine when the SAME sentence explains it in everyday words
EXPLAINS = re.compile(r"\b(yaitu|yakni|artinya|alias|disebut|maksudnya|berarti|atau)\b|[(:]", re.I)
HANGING_THING = re.compile(r"\b(hewan|batu|benda|zat|makhluk|tumbuhan|planet|bintang|fosil|pesawat|alat|mesin|kota|negara|"
                           r"teleskop|satelit|serangga|ikan|burung)( \w+){0,2} ini\b", re.I)


def context_problems(slides):
    """Lay-reader rules for slide TEXT (the hook title has its own check): the first slide must say WHAT the post is about in
    plain words, and a technical word may only appear in a sentence that explains it."""
    bad = []
    if slides:
        first = (slides[0].get("body") or "").strip()
        if len(first.split()) < 6:
            bad.append("slide 1 needs a body of at least 6 words that says what or who this is about, in plain words")
        if HANGING_THING.search(first):
            bad.append("slide 1 body says 'X ini' without naming the thing: the reader has not seen the picture yet, name it")
    for i, s in enumerate(slides or [], 1):
        for field in ("title", "body"):
            for sentence in re.split(r"(?<=[.!?])\s+", s.get(field) or ""):
                m = JARGON.search(sentence)
                if m and not EXPLAINS.search(sentence):
                    bad.append(f"slide {i} {field}: the technical word {m.group(0)!r} needs an everyday explanation in the same sentence "
                               "(for example 'yaitu ...') or a simpler word")
                    break
    return bad


def hook_problems(text):
    """A hook must be understandable by a lay reader in 3 seconds: no jargon, not too many foreign names, no hanging 'ini'."""
    bad, words = [], re.findall(r"[A-Za-z0-9'-]+", text or "")
    if m := JARGON.search(text or ""):
        bad.append(f"hook uses the technical word {m.group(0)!r}: say it in everyday words")
    names = [w for w in words[1:] if w[0].isupper() and len(w) > 2]
    if len(names) > 2:
        bad.append(f"hook has {len(names)} proper names ({', '.join(names[:4])}): keep at most 2, lay readers will not know them")
    if any(len(w) > 14 for w in words):
        bad.append("hook has a very long word: use a simpler one")
    if re.search(r"\b(hewan|batu|benda|zat|makhluk|tumbuhan|planet|bintang|fosil)( \w+){0,2} ini\b", text or "", re.I):
        bad.append("hook says 'X ini' without naming the thing: name it, the reader has not seen the picture yet")
    return bad


def validate_post(p, check_viz=True):
    """Returns a list of problems; an empty list means the structure is acceptable."""
    if p.get("format") == "reel":
        import reel
        return reel.validate_reel(p) + hook_problems((p.get("reel") or {}).get("hook")) + _common_problems(p) + (viz_problems(p) if check_viz else [])
    bad = []
    slides = p.get("slides") or []
    lo, hi = LIMITS["slides"]
    if not lo <= len(slides) <= hi:
        bad.append(f"need {lo}-{hi} slides, got {len(slides)}")
    if slides:
        if slides[0].get("type") != "hook":
            bad.append("first slide must be type hook")
        if slides[-1].get("type") != "cta":
            bad.append("last slide must be type cta")
        bad += hook_problems(slides[0].get("title"))
        bad += context_problems(slides)
    for i, s in enumerate(slides, 1):
        if s.get("type") not in SLIDE_TYPES:
            bad.append(f"slide {i}: unknown type {s.get('type')!r}")
        if not s.get("title") or len(s["title"]) > LIMITS["title"]:
            bad.append(f"slide {i}: title missing or over {LIMITS['title']} chars")
        if len(s.get("body", "")) > LIMITS["body"]:
            bad.append(f"slide {i}: body over {LIMITS['body']} chars")
        if s.get("visual") is not None:  # legacy field: photos now come from image_query, drawings from viz
            vs = (s.get("visual") or {}).get("source") if isinstance(s.get("visual"), dict) else None
            if vs not in VISUAL_SOURCES:
                bad.append(f"slide {i}: visual source must be one of {sorted(VISUAL_SOURCES)} (or leave visual out)")
    if len(p.get("hook_options") or []) < 2:
        bad.append("need 2 hook_options")
    for i, s in enumerate(slides, 1):
        if len(s.get("takeaway", "") or "") > 110:
            bad.append(f"slide {i}: takeaway over 110 chars")
        if s.get("viz"):
            import infographics
            if not infographics.valid(s["viz"]):
                bad.append(f"slide {i}: viz is not drawable")
    return bad + _common_problems(p) + (viz_problems(p) if check_viz else [])


def write_post(llm, topic, feedback=None):
    brand = (ROOT / "brand.md").read_text(encoding="utf-8")
    reply = llm.call("writer", SONNET, brand + SYSTEM_TAIL, build_prompt(topic, feedback), max_tokens=8000)
    post = extract_json(reply)
    if not isinstance(post, dict):
        raise ValueError("writer did not return a JSON object")
    return post
