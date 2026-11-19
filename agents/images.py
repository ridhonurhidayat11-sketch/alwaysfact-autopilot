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
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600})
    pages = ((fetch(url).get("query") or {}).get("pages") or {}).values()
    out = []
    for p in sorted(pages, key=lambda x: x.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if ii.get("mime") not in ("image/jpeg", "image/png") or not license_ok(lic):
            continue
        if ii.get("width", 0) < MIN_W or ii.get("height", 0) < MIN_H:
            continue
        out.append({"source": "Wikimedia Commons", "url": ii.get("thumburl") or ii.get("url"),
                    "title": strip(p.get("title", "")).replace("File:", ""), "creator": strip((meta.get("Artist") or {}).get("value", "")) or "unknown",
                    "license": lic, "page": ii.get("descriptionurl", ""), "w": ii.get("width"), "h": ii.get("height"),
                    "text": " ".join([p.get("title", ""), strip((meta.get("ImageDescription") or {}).get("value", "")),
                                      strip((meta.get("ObjectName") or {}).get("value", "")),
                                      strip((meta.get("Categories") or {}).get("value", "")).replace("|", " ")]).lower()})
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
    text = c.get("text", "")

    def has(t):  # whole words only: 'pig' must not match 'pigment'
        return bool(re.search(r"\b" + re.escape(t.rstrip("s") if len(t) > 3 else t) + r"s?\b", text))
    head = max(toks, key=lambda t: (len(t), toks.index(t)))
    # A capitalised name (a genus such as 'Scotoplanes', or a proper noun) found in the photo text is a firm identity match.
    names = {n.lower() for n in re.findall(r"\b[A-Z][a-z]{5,}\b", query or "")}
    if any(has(n) for n in names):
        return True
    need = 1.0 if len(toks) <= 3 else 0.75  # precision over recall: no photo beats a wrong photo
    return has(head) and sum(has(t) for t in toks) / len(toks) >= need


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


def find_many(query, n=3, prefer="commons", fetch=get_json):
    """Up to n distinct relevant photos of ONE subject (used for the cover and a few slides of the same post)."""
    if not enabled() or not query:
        return []
    order = [nasa_search, commons_search] if prefer == "nasa" else [commons_search]  # NASA only for space topics
    got, urls = [], set()
    for q in variants(query):
        for search in order:
            try:
                for c in search(q, fetch=fetch):
                    if c["url"] and c["url"] not in urls and relevant(c, q):
                        urls.add(c["url"])
                        got.append(c)
                        if len(got) == n:
                            return got
            except Exception as e:
                ERRORS.append(f"{search.__name__}({q!r}): {type(e).__name__}: {str(e)[:80]}")
                continue
        if got:  # a shorter, looser query must not add photos once the specific one produced some
            break
    return got


def credit(c):
    who = re.sub(r"\s+", " ", c["creator"]).strip()[:60]
    return f"{who} / {c['license']} ({c['source']})"
