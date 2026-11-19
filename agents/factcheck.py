"""Fact-checker: a separate, adversarial pass that must find real sources for every claim.

It uses web search. A post passes only when EVERY claim is 'supported' with an https evidence URL that the
checker itself found (the writer's URL is only a hint).
"""
from urllib.parse import urlparse

from common import CONFIG, SONNET, extract_json, load_json


def host(url):
    u = urlparse(url or "")
    if u.scheme != "https" or not u.netloc:
        return ""
    return u.netloc.lower().split(":")[0].removeprefix("www.")  # Python 3.9+


def is_tier1(url, tier1):
    h = host(url)
    return bool(h) and any(h == t.lstrip(".") or h.endswith("." + t.lstrip(".")) or (t.startswith(".") and h.endswith(t))
                           for t in tier1)


def site(url):
    """Registered-ish domain (last two labels) so that news.x.com and x.com count as one site."""
    parts = host(url).split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host(url)


def evidence_ok(v, tier1):
    """Automatic standard: a tier-1 source, or two sources on different sites."""
    first, second = v.get("evidence_url", ""), v.get("second_evidence_url", "")
    if not host(first):
        return False
    if is_tier1(first, tier1) or (host(second) and is_tier1(second, tier1)):
        return True
    return bool(host(second)) and site(first) != site(second)

SYSTEM = (
    "You are a skeptical fact-checker for a science account whose reputation depends on never being wrong. "
    "Use web search. For each claim, find a reliable page (primary source, institution, journal, or major science "
    "outlet) that explicitly supports the exact claim, including every number and qualifier. "
    "Verdicts: supported = a page you actually found states it; unsupported = sources contradict it or the "
    "numbers differ; unclear = you could not find solid support. Do not give the benefit of the doubt. "
    "Reply with JSON only."
)
TOOLS = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]


def build_prompt(post):
    claims = "\n".join(f"{i}. {c['text']}  (writer's source hint: {c.get('source_url', '')})"
                       for i, c in enumerate(post["claims"], 1))
    slides = "\n".join(f"- {s['title']} {s.get('body', '')}" for s in post["slides"])
    return (
        f"Claims to verify:\n{claims}\n\nSlide text that must not say more than the claims support:\n{slides}\n"
        f"Caption:\n{post['caption']}\n\n"
        'Return a JSON array, one item per claim in order: {"claim": int, "verdict": "supported|unsupported|unclear", '
        '"evidence_url": str (https, a page you found), "second_evidence_url": str (a DIFFERENT, independent site '
        'that also supports it, or empty), "note": str (max 160 chars, what exactly the source says, '
        'or what is wrong)}. Prefer institutions and journals (NASA, NOAA, Nature, Science, universities). '
        'Also add a final item {"claim": 0, "verdict": "supported|unsupported", "note": str} '
        "stating whether the slides or caption say anything stronger than the claims (use unsupported if so)."
    )


def passes(post, verdicts, tier1=None):
    """Pure check, returns (ok, problems). No human is in the loop, so the bar is strict."""
    if tier1 is None:
        tier1 = load_json(CONFIG / "trusted_sources.json", {}).get("tier1", [])
    problems = []
    by_n = {v.get("claim"): v for v in verdicts if isinstance(v, dict)}
    for i, c in enumerate(post["claims"], 1):
        v = by_n.get(i)
        if not v:
            problems.append(f"claim {i} was not checked: {c['text'][:80]}")
            continue
        if v.get("verdict") != "supported":
            problems.append(f"claim {i} {v.get('verdict')}: {c['text'][:80]} -> {v.get('note', '')}")
        elif not evidence_ok(v, tier1):
            problems.append(f"claim {i} needs a tier-1 source or two independent sites: {c['text'][:80]}")
    overall = by_n.get(0)
    if not overall:
        problems.append("overall check of slides and caption is missing")
    elif overall.get("verdict") != "supported":
        problems.append(f"slides or caption say more than the sources: {overall.get('note', '')}")
    return (not problems), problems


def check_post(llm, post):
    reply = llm.call("factcheck", SONNET, SYSTEM, build_prompt(post), max_tokens=6000, tools=TOOLS)
    verdicts = extract_json(reply)
    if not isinstance(verdicts, list):
        raise ValueError("fact-checker did not return a JSON array")
    ok, problems = passes(post, verdicts)
    return ok, problems, verdicts
