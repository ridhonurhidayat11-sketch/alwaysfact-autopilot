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


def _numbers(text):
    return {re.sub(r"[.,]", "", n) for n in re.findall(r"\d[\d.,]*\d|\d", text or "")}


def _viz_texts(viz):
    """Every string of an infographic spec that a reader sees (scale-only fields such as dots.total are not claims)."""
    if not isinstance(viz, dict):
        return []
    out = [str(viz.get(k, "")) for k in ("value", "label", "note", "caption", "unit")]
    for it in viz.get("items") or []:
        out += [str(x) for x in it.values()] if isinstance(it, dict) else [str(it)]
    return out


def viz_problems(p):
    """A number drawn in an infographic must be a number the verified claims contain. The picture can't say more than the claims."""
    claim_nums = set()
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


def validate_post(p):
    """Returns a list of problems; an empty list means the structure is acceptable."""
    if p.get("format") == "reel":
        import reel
        return reel.validate_reel(p) + _common_problems(p) + viz_problems(p)
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
    return bad + _common_problems(p) + viz_problems(p)


def write_post(llm, topic, feedback=None):
    brand = (ROOT / "brand.md").read_text(encoding="utf-8")
    reply = llm.call("writer", SONNET, brand + SYSTEM_TAIL, build_prompt(topic, feedback), max_tokens=8000)
    post = extract_json(reply)
    if not isinstance(post, dict):
        raise ValueError("writer did not return a JSON object")
    return post
