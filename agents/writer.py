"""Writer: turns one approved topic into a carousel post. The model writes; plain code validates the structure."""
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


def validate_post(p):
    """Returns a list of problems; an empty list means the structure is acceptable."""
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
    for i, s in enumerate(slides, 1):
        if s.get("type") not in SLIDE_TYPES:
            bad.append(f"slide {i}: unknown type {s.get('type')!r}")
        if not s.get("title") or len(s["title"]) > LIMITS["title"]:
            bad.append(f"slide {i}: title missing or over {LIMITS['title']} chars")
        if len(s.get("body", "")) > LIMITS["body"]:
            bad.append(f"slide {i}: body over {LIMITS['body']} chars")
        vs = (s.get("visual") or {}).get("source")
        if vs not in VISUAL_SOURCES:
            bad.append(f"slide {i}: visual source must be one of {sorted(VISUAL_SOURCES)}")
    if len(p.get("hook_options") or []) < 2:
        bad.append("need 2 hook_options")
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


def write_post(llm, topic, feedback=None):
    brand = (ROOT / "brand.md").read_text(encoding="utf-8")
    reply = llm.call("writer", SONNET, brand + SYSTEM_TAIL, build_prompt(topic, feedback), max_tokens=8000)
    post = extract_json(reply)
    if not isinstance(post, dict):
        raise ValueError("writer did not return a JSON object")
    return post
