"""Fact-checker: a separate, adversarial pass that must find real sources for every claim.

It uses web search. A post passes only when EVERY claim is 'supported' with an https evidence URL that the
checker itself found (the writer's URL is only a hint).
"""
from urllib.parse import urlparse

from common import SONNET, extract_json

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
        '"evidence_url": str (https, a page you found), "note": str (max 160 chars, what exactly the source says, '
        'or what is wrong)}. Also add a final item {"claim": 0, "verdict": "supported|unsupported", "note": str} '
        "stating whether the slides or caption say anything stronger than the claims (use unsupported if so)."
    )


def passes(post, verdicts):
    """Pure check, returns (ok, problems)."""
    problems = []
    by_n = {v.get("claim"): v for v in verdicts if isinstance(v, dict)}
    for i, c in enumerate(post["claims"], 1):
        v = by_n.get(i)
        if not v:
            problems.append(f"claim {i} was not checked: {c['text'][:80]}")
            continue
        u = urlparse(v.get("evidence_url", ""))
        if v.get("verdict") != "supported":
            problems.append(f"claim {i} {v.get('verdict')}: {c['text'][:80]} -> {v.get('note', '')}")
        elif u.scheme != "https" or not u.netloc:
            problems.append(f"claim {i} has no real evidence URL: {c['text'][:80]}")
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
