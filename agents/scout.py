"""Scout: gathers topic candidates.

1. News items come from real RSS/Atom feeds fetched by plain code, so every link is real (no AI-invented URLs).
2. Optionally, a cheap model brainstorms evergreen ideas per pillar. These carry no URL and MUST pass the fact-checker.
"""
import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from common import CONFIG, HAIKU, STATE, extract_json, load_json, save_json

MAX_FEED_BYTES = 2_000_000
UA = "Mozilla/5.0 (compatible; science-feed-reader/1.0)"


def tid(seed):
    return "t" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("feed too large")
    return data.decode("utf-8", "replace")


def clean(text, limit=400):
    t = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", t).strip()[:limit]


def parse_date(text):
    """RSS (RFC 822) or Atom (ISO 8601) date text -> aware UTC datetime, or None when it cannot be read."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        t = re.sub(r"Z$", "+00:00", text)
        t = re.sub(r"(\.\d+)(?=[+-]\d\d:?\d\d$|$)", "", t)   # fractional seconds of any length
        try:
            dt = datetime.fromisoformat(t)
        except ValueError:
            return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_feed(xml_text, source):
    """RSS or Atom -> list of {title, url, summary, source}. Rejects DTD/entity tricks before parsing."""
    head = xml_text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in xml_text[:20000].upper():
        raise ValueError("feed rejected: contains DOCTYPE/ENTITY")
    root = ET.fromstring(xml_text)
    items = []
    for el in root.iter():
        name = el.tag.split("}")[-1]
        if name not in ("item", "entry"):
            continue
        f = {}
        for c in el:
            n = c.tag.split("}")[-1]
            if n == "link":
                f["url"] = (c.attrib.get("href") or (c.text or "")).strip()
            elif n in ("title", "description", "summary", "content") and n not in f:
                f[n] = c.text or ""
            elif n in ("pubDate", "published", "updated", "date") and "date" not in f:
                f["date"] = c.text or ""
        title = clean(f.get("title"), 200)
        url = f.get("url", "")
        if title and url.startswith("http"):
            summary = clean(f.get("description") or f.get("summary") or f.get("content"))
            items.append({"title": title, "url": url, "summary": summary, "source": source, "date": f.get("date", "")})
    return items


def collect(feeds, per_feed=8, fetcher=fetch, now=None, max_age_days=7):
    """Never fails as a whole: a dead feed is reported and skipped. News older than `max_age_days` is dropped, the rest is
    ordered newest first (undated items go last), so every run works on what happened recently."""
    now = now or datetime.now(timezone.utc)
    limit = now - timedelta(days=max_age_days)
    out, errors, seen = [], [], set()
    for feed in feeds:
        try:
            items = parse_feed(fetcher(feed["url"]), feed["name"])
        except Exception as e:
            errors.append(f"{feed['name']}: {type(e).__name__}: {str(e)[:80]}")
            continue
        dated = [(parse_date(it.get("date")), it) for it in items]
        fresh = [(dt, it) for dt, it in dated if dt is None or dt >= limit]
        fresh.sort(key=lambda x: x[0] or datetime(1970, 1, 1, tzinfo=timezone.utc), reverse=True)
        if len(fresh) < len(dated):
            errors.append(f"{feed['name']}: {len(dated) - len(fresh)} item(s) older than {max_age_days} days skipped")
        for dt, it in fresh[:per_feed]:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            out.append({"id": tid(it["url"]), "origin": "feed", **{k: v for k, v in it.items() if k != "date"},
                        "published": dt.isoformat() if dt else ""})
    out.sort(key=lambda c: c["published"] or "", reverse=True)
    return out, errors


BRAINSTORM_SYSTEM = (
    "You propose evergreen science topics for an Indonesian Instagram account about mind-blowing facts "
    "people overlook. Reply with JSON only."
)


def brainstorm(llm, pillars, per_pillar, avoid):
    user = (
        f"For each pillar propose {per_pillar} topics that are well established in science textbooks or major "
        "institutions (not rumours, not myths). Skip anything similar to these already used:\n"
        + "\n".join(f"- {a}" for a in avoid[:40])
        + "\n\nPillars:\n" + "\n".join(f"- {p}" for p in pillars)
        + '\n\nReturn a JSON array of {"title": str (Indonesian, max 90 chars), "summary": str (1-2 sentences, '
        'Indonesian, the surprising core), "pillar": str}.'
    )
    items = extract_json(llm.call("scout.brainstorm", HAIKU, BRAINSTORM_SYSTEM, user, max_tokens=4000))
    return [{"id": tid("brainstorm:" + it["title"]), "origin": "brainstorm", "url": "", "source": "brainstorm",
             "title": it["title"], "summary": it.get("summary", ""), "pillar": it.get("pillar", "")}
            for it in items if it.get("title")]


MIN_TEXT = 800     # shorter than this is a paywall stub or an error page, not an article
MAX_TEXT = 9000    # enough for the key facts; keeps the repo small


def attach_sources(cands, directory, per_source=5, page_fetcher=None):
    """Download the article text of the best candidates so the Claude routine can read sources WITHOUT internet.
    The referee later re-fetches the live page itself and checks every quote, so stored text is never trusted blindly.
    Wipes and rewrites `directory` each day. Returns (fetched, failed)."""
    import shutil

    if page_fetcher is None:
        from evidence import fetch_text as page_fetcher
    directory = Path(directory)
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    seen, fetched, failed = {}, 0, 0
    for c in cands:
        if c.get("origin") != "feed" or seen.get(c["source"], 0) >= per_source:
            continue
        seen[c["source"]] = seen.get(c["source"], 0) + 1
        try:
            text = re.sub(r"\s+", " ", page_fetcher(c["url"])).strip()
            if len(text) < MIN_TEXT:
                raise ValueError("page text too short")
            (directory / f"{c['id']}.txt").write_text(text[:MAX_TEXT], encoding="utf-8")
            c["text_file"] = f"state/sources/{c['id']}.txt"
            fetched += 1
        except Exception as e:
            c["fetch_error"] = f"{type(e).__name__}: {str(e)[:60]}"
            failed += 1
    return fetched, failed


def run(llm=None, fetcher=fetch, per_pillar=0, with_text=False, page_fetcher=None):
    cfg = load_json(CONFIG / "sources.json", {"feeds": [], "per_feed": 8})
    rubric = load_json(CONFIG / "rubric.json", {"pillars": []})
    cands, errors = collect(cfg["feeds"], cfg.get("per_feed", 8), fetcher, max_age_days=cfg.get("max_age_days", 7))
    if with_text:
        ok, bad = attach_sources(cands, STATE / "sources", page_fetcher=page_fetcher)
        errors.append(f"article text: {ok} saved, {bad} unavailable")
    if llm and per_pillar:
        from common import history_titles

        cands += brainstorm(llm, rubric["pillars"], per_pillar, history_titles())
    save_json(STATE / "candidates.json", cands)
    return cands, errors
