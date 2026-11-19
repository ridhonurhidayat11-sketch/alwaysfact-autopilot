"""Image finder: only photos with a clear, reusable license, always with a credit line.

Sources (no API key, no scraping of Google Images):
  - NASA Image and Video Library (space and Earth imagery)
  - Wikimedia Commons (species, fossils, places, diagrams) with per-file license metadata
Accepted licenses: CC0, public domain, CC BY, CC BY-SA. Anything NonCommercial, NoDerivatives or unclear is rejected.
Plain code, no AI, no cost. Every network call is guarded, so a missing photo only means a text slide.
"""
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "SciFactBot/1.0 (educational image search)"
COMMONS = "https://commons.wikimedia.org/w/api.php"
NASA = "https://images-api.nasa.gov/search"
MIN_W, MIN_H = 900, 700
OK_LICENSE = re.compile(r"^(cc0|public domain|pd[ -]|pdm|cc[ -]by([ -]sa)?[ -]\d)", re.I)
BAD_LICENSE = re.compile(r"(\bnc\b|\bnd\b|non-?commercial|no[ -]?deriv|fair use|gfdl|unknown|all rights)", re.I)


def enabled():
    return not os.environ.get("NO_IMAGES")


ERRORS = []  # why a search or download failed, printed by the renderer so a missing photo is never a mystery


def _open(url, timeout, tries=4):
    """urlopen with polite retries: shared CI addresses are sometimes throttled (429) for a moment."""
    for attempt in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise


def get_json(url, timeout=20):
    with _open(url, timeout) as r:
        return json.load(r)


def get_bytes(url, limit=9_000_000, timeout=30):
    with _open(url, timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("image too large")
    return data


def strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def license_ok(name):
    return bool(name) and bool(OK_LICENSE.search(name.strip())) and not BAD_LICENSE.search(name)


def commons_search(query, fetch=get_json, limit=12):
    url = COMMONS + "?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1280})
    data = fetch(url)
    pages = ((data.get("query") or {}).get("pages") or {}).values()
    out, dropped = [], {}
    for p in sorted(pages, key=lambda x: x.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if ii.get("mime") not in ("image/jpeg", "image/png"):
            dropped["mime"] = dropped.get("mime", 0) + 1
            continue
        if not license_ok(lic):
            dropped["license:" + (lic or "none")[:20]] = dropped.get("license:" + (lic or "none")[:20], 0) + 1
            continue
        if ii.get("width", 0) < MIN_W or ii.get("height", 0) < MIN_H:
            dropped["small"] = dropped.get("small", 0) + 1
            continue
        out.append({"source": "Wikimedia Commons", "url": ii.get("thumburl") or ii.get("url"),
                    "title": strip(p.get("title", "")).replace("File:", ""), "creator": strip((meta.get("Artist") or {}).get("value", "")) or "unknown",
                    "license": lic, "page": ii.get("descriptionurl", ""), "w": ii.get("width"), "h": ii.get("height"),
                    "core": " ".join([p.get("title", ""), strip((meta.get("ImageDescription") or {}).get("value", "")),
                                      strip((meta.get("ObjectName") or {}).get("value", ""))]).lower(),
                    "text": " ".join([p.get("title", ""), strip((meta.get("ImageDescription") or {}).get("value", "")),
                                      strip((meta.get("ObjectName") or {}).get("value", "")),
                                      strip((meta.get("Categories") or {}).get("value", "")).replace("|", " ")]).lower()})
    if not out:  # an empty answer is not an error, but it must not be silent either
        keys = [k for k in data if k != "batchcomplete"][:3]
        ERRORS.append(f"commons_search({query!r}): 0 usable of {len(list(pages))}; dropped={dropped}; keys={keys}")
    return out


def nasa_search(query, fetch=get_json, limit=10):
    url = NASA + "?" + urllib.parse.urlencode({"q": query, "media_type": "image", "page_size": limit})
    out = []
    for it in ((fetch(url).get("collection") or {}).get("items") or []):
        d = (it.get("data") or [{}])[0]
        thumbs = [l.get("href") for l in it.get("links", []) if l.get("href")]
        if not thumbs:
            continue
        big = re.sub(r"~thumb\.", "~large.", thumbs[0])  # NASA's own naming for the large rendition
        out.append({"source": "NASA", "url": big, "title": strip(d.get("title", "")), "creator": strip(d.get("photographer") or d.get("secondary_creator") or d.get("center") or "NASA"),
                    "license": "NASA media (public domain unless noted)", "page": f"https://images.nasa.gov/details/{d.get('nasa_id', '')}",
                    "w": None, "h": None,
                    "text": " ".join([d.get("title", ""), strip(d.get("description", "")), " ".join(d.get("keywords", []) or [])]).lower()})
    return out


GENERIC = {"ocean", "deep", "science", "scientific", "curiosity", "mystery", "illustration", "picture", "photo", "image",
           "structure", "drawing", "diagram", "animal", "nature", "water", "light", "the", "and", "of", "in", "a"}


# Words that show the photo is about a PLACE, a sign or a monument that merely contains the subject's name
# ('Orcas Island' is not an orca, a memorial plaque is not the stone). Ignored when the query itself asks for that thing.
INCIDENTAL = re.compile(r"\b(islands?|streets?|roads?|avenues?|schools?|churche?s?|hotels?|stadiums?|restaurants?|airports?|"
                        r"plaques?|memorials?|inscriptions?|signage|signs?|statues?|screenshots?|logos?)\b")


def relevant(c, query):
    """The photo must actually be about the subject: its title/description/categories have to contain the
    subject words, and the HEAD word (the most specific one) is mandatory. A loose full-text match
    (a mosasaur for 'prehistoric whale') would mislead readers."""
    toks = []
    for t in re.findall(r"[A-Za-z]{3,}", query or ""):
        t = t.lower()
        if t not in GENERIC and t not in toks:
            toks.append(t)
    if not toks:
        return False
    text = c.get("core", c.get("text", ""))  # title + description; categories are too loose to prove the subject
    asked = set(re.findall(r"[a-z]+", (query or "").lower()))
    if any(m.group(1).rstrip("s") not in {a.rstrip("s") for a in asked} for m in INCIDENTAL.finditer(text)):
        return False

    def has(t):  # whole words only: 'pig' must not match 'pigment'
        return bool(re.search(r"\b" + re.escape(t.rstrip("s") if len(t) > 3 else t) + r"s?\b", text))
    head = max(toks, key=lambda t: (len(t), toks.index(t)))
    # A capitalised name (a genus such as 'Scotoplanes', or a proper noun) found in the photo text is a firm identity match.
    names = {n.lower() for n in re.findall(r"\b[A-Z][a-z]{5,}\b", query or "")}
    if any(has(n) for n in names):
        return True
    if len(toks) >= 2:  # 'Ethylene sulfide' is not 'hydrogen sulfide': the phrase, or the head word in the TITLE, must be there
        phrase = " ".join(re.findall(r"[a-z]{3,}", (query or "").lower()))
        if phrase not in re.sub(r"[^a-z ]+", " ", text) and not re.search(r"\b" + re.escape(head) + r"", (c.get("title") or text).lower()):
            return False
    need = 1.0 if len(toks) <= 3 else 0.75  # precision over recall: no photo beats a wrong photo
    return has(head) and sum(has(t) for t in toks) / len(toks) >= need


NOT_A_PHOTO = re.compile(r"\b(illustrations?|drawings?|engravings?|lithographs?|plates?|sketch(?:es)?|paintings?|diagrams?|maps?|"
                         r"charts?|logos?|scans?|stamps?|posters?|cartoons?|clip ?art|schematics?|reconstructions?|renderings?|icons?|models?)\b")
MUSEUM_SHOT = re.compile(r"\b(usnm|nmnh|specimens?|holotypes?|syntypes?|paratypes?|preserved|catalogue?|accession)\b")
TOP_PICK = re.compile(r"featured pictures|quality images|valued images|picture of the day")


def quality(c):
    """Higher is better. Prefers real, striking photographs (featured/quality pictures, large) over drawings, plates,
    diagrams and scans, which read as dull and are often not photographs of the real animal at all."""
    text, score = c.get("text", ""), 0
    if NOT_A_PHOTO.search(text):
        score -= 6
    if MUSEUM_SHOT.search(text):  # a dead specimen next to a ruler and a label: a real photo, but a dull one
        score -= 3
    if re.search(r"noaa|mbari|okeanos|nasa|esa\b|eso\b|usgs", (c.get("creator") or "").lower()):
        score += 2  # field/science photographers: live animals in their habitat, real telescope and satellite images
    if TOP_PICK.search(text):
        score += 4
    if (c.get("w") or 0) >= 2000:
        score += 1
    return score


def variants(query):
    """Full query first, then shorter ones: very specific searches often return nothing."""
    words = [w for w in re.findall(r"[A-Za-z0-9-]+", query or "")]
    seen, out = set(), []
    for v in (words, words[:3], words[:2], words[-2:]) + ((words,) if len(words) == 1 else ()):
        q = " ".join(v)
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def find(query, prefer="commons", used=(), fetch=get_json):
    """Best licensed photo for a query, or None. `used` is a set of URLs already taken by this post."""
    if not enabled() or not query:
        return None
    order = [nasa_search, commons_search] if prefer == "nasa" else [commons_search]
    for q in variants(query):
        for search in order:
            try:
                for c in search(q, fetch=fetch):
                    if c["url"] and c["url"] not in used and relevant(c, q):
                        return c
            except Exception as e:
                ERRORS.append(f"{search.__name__}({q!r}): {type(e).__name__}: {str(e)[:80]}")
                continue
    return None


def find_many(query, n=3, prefer="commons", fetch=get_json, pool=None):
    """Up to `pool` (default n) distinct relevant photos of ONE subject, best quality first (real photos before drawings)."""
    if not enabled() or not query:
        return []
    want = pool or n
    order = [nasa_search, commons_search] if prefer == "nasa" else [commons_search]  # NASA only for space topics
    got, urls = [], set()
    for q in variants(query):
        for search in order:
            try:
                for c in search(q, fetch=fetch, **({"limit": 40} if pool else {})):
                    if c["url"] and c["url"] not in urls and relevant(c, q):
                        urls.add(c["url"])
                        got.append(c)
            except Exception as e:
                ERRORS.append(f"{search.__name__}({q!r}): {type(e).__name__}: {str(e)[:80]}")
                continue
        if got:  # a shorter, looser query must not add photos once the specific one produced some
            break
    got.sort(key=lambda c: -quality(c))  # stable: search order is kept among equals
    return got[:want]


def credit(c):
    who = re.sub(r"\s+", " ", c["creator"]).strip()[:60]
    return f"{who} / {c['license']} ({c['source']})"
