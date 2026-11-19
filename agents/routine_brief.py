#!/usr/bin/env python3
"""Prepares everything the Claude routine needs, as one JSON document, so the routine spends its effort on
thinking and not on reading files. Run by the routine at the start of every session. No network, no AI.

  python agents/routine_brief.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "publisher"))

import autopilot  # noqa: E402
import control  # noqa: E402
import publish  # noqa: E402
from common import CONFIG, ROOT, STATE, history_titles, is_duplicate, load_json  # noqa: E402

SCHEMA = """Write each proposal to inbox/<YYYY-MM-DD>-<n>.json as exactly this JSON object:
{
  "topic_id": "<id of the candidate, or '' for an evergreen idea of your own>",
  "source_url": "<the candidate's url, or the main source you used>",
  "risk": "low",
  "post": {
    "topic": "<Indonesian title, max 90 chars>",
    "image_query": "<English search words for real PHOTOS of the post's main subject: a concrete animal, plant, place, object, body part or space object, e.g. 'Scotoplanes sea pig' or 'Crab Nebula'. Use the Latin/scientific name when there is one. Never a metaphor or pun, never an abstract idea, and not a gas, chemical or concept (their photos are dull or misleading): use '' then. Photos come from Wikimedia Commons and NASA only; with '' the design is typographic.>",
    "pillar": "<one of the pillars>",
    "hook_options": ["<hook 1, max 70 chars>", "<hook 2, different>"],
    "slides": [ {"type": "hook|fact|compare|chart|cta", "title": "<max 70>", "body": "<max 220>",
                 "image_query": "<optional, fact slides only: English words for a photo of the concrete thing THIS slide is about, e.g. 'Escherichia coli'; leave out when abstract>",
                 "visual": {"source": "canva_stock|unsplash|pexels|pixabay|nasa|wikimedia|code_chart", "keywords": "<search words>"}} ],
    "caption": "<Indonesian, max 1200, ends with a line starting 'Sumber:' naming the sources>",
    "hashtags": ["<5-10 tags without #>"],
    "claims": [ {"text": "<one factual statement, Indonesian>", "source_name": "<name>", "source_url": "<https url>"} ],
    "music_suggestions": ["<3 short mood+tempo descriptions, no song titles>"]
  },
  "verdicts": [
    {"claim": 1, "verdict": "supported", "evidence_url": "<https page you opened>",
     "quote": "<VERBATIM sentence copied from that page, 25+ characters, in the page's own language>",
     "second_evidence_url": "<optional, a DIFFERENT site>", "second_quote": "<verbatim, optional>",
     "note": "<max 160 chars>"},
    {"claim": 0, "verdict": "supported", "note": "slides and caption say nothing stronger than the claims"}
  ]
}
Rules (carousel): 6-8 slides; slide 1 type hook with title equal to hook_options[0]; last slide type cta; every factual
statement in slides or caption must appear in claims; claim numbers in verdicts follow the order of claims;
every number in a claim must also appear in its quote.

VISUAL FIELDS on a fact slide (all optional but strongly wanted, so every slide EXPLAINS itself with a picture):
  "takeaway": "<one plain sentence, max 100 chars: the point of the slide>",
  "icon": "<one of: bulb magnifier atom flask cloud gas fish bacteria dna planet rocket flame droplet bolt leaf heart burst clock globe>",
  "image_query": "<English words for a real photo of the concrete thing on THIS slide, or leave out>",
  "viz": ONE infographic, drawn by code, used when there is no photographable subject (numbers, shares, comparisons, processes):
     {"kind":"stat","value":"14-20x","label":"kali per hari","note":"pada orang sehat"}
     {"kind":"share","items":[{"label":"Nitrogen","pct":60},{"label":"Lainnya","pct":40}],"caption":"..."}
     {"kind":"compare","items":[{"label":"A","value":3},{"label":"B","value":5}],"unit":"cm","caption":"..."}
     {"kind":"dots","total":100,"highlight":1,"label":"1 dari 1 juta","caption":"Satu titik = ..."}
     {"kind":"steps","items":["langkah 1","langkah 2","langkah 3"],"caption":"..."}
  A number in a viz MUST also appear in a claim (code checks this). Do not draw anything the claims do not say.
  In body text you may mark the key phrase with **double asterisks** (bold + underline).

REEL FORMAT (when the brief asks for reels): same object, but set "post.format": "reel", "post.slides": [] and add
  "post.reel": {"hook": "<8-70 chars, shown on screen in the first second>", "mood": "curious|calm|energetic",
    "beats": [ {"say": "<ONE narrated Indonesian sentence, 12-190 chars, max ~25 words>",
                "text": "<2-5 word on-screen headline>", "viz": {...as above...} OR "image_query": "<English>", "icon": "<optional>"} ]}
  5-8 beats, 45-140 narrated words in total (about 25-45 seconds). Beat 1 is the hook (a question or surprising fact),
  the LAST beat is the call to action: ask to send it to a friend and to follow @alwaysfact_ (the code draws that scene).
  Spell numbers in words inside "say" (the voice reads it), and keep digits in "text"/"viz". Caption, hashtags, claims and
  verdicts work exactly as for a carousel. Reels follow the same fact rules: nothing is said that a verified claim does not support."""


def _format(post_id):
    try:
        return load_json(ROOT / "content" / f"{post_id}.json", {}).get("format", "carousel")
    except Exception:
        return "carousel"


def brief(now=None, queue_path=None):
    now = now or datetime.now(timezone.utc)
    c = control.load()
    if c["mode"] == "off" or c["paused"]:
        return {"go": False, "reason": f"mode={c['mode']} paused={c['paused']} {c['pause_reason']}".strip()}
    qpath = Path(queue_path or autopilot.QUEUE)
    rows = publish.read_queue(str(qpath)) if qpath.exists() else []
    ready = autopilot.ready_rows(rows, now)
    reels_ready = sum(1 for r in ready if _format(r["id"]) == "reel")
    need_reels = max(0, c.get("reels_per_day", 0) * c["buffer_days"] - reels_ready)
    need_carousels = max(0, (c["posts_per_day"] - c.get("reels_per_day", 0)) * c["buffer_days"] - (len(ready) - reels_ready))
    need = need_reels + need_carousels
    if need <= 0:
        return {"go": False, "reason": "the queue already holds enough posts"}
    used = load_json(STATE / "used.json", [])
    used_urls = {u["url"] for u in used if u.get("url")}
    history = history_titles() + [u["title"] for u in used]
    fresh = [x for x in load_json(STATE / "candidates.json", [])
             if x.get("url") not in used_urls and not is_duplicate(x["title"], history)]
    fresh.sort(key=lambda x: 0 if x.get("text_file") else 1)  # candidates whose article text is stored come first
    rubric = load_json(CONFIG / "rubric.json", {})
    return {
        "go": True,
        "need_posts": need,
        "need_carousels": need_carousels,
        "need_reels": need_reels,
        "propose_at_most": control.topics_needed(need, cap=c["max_topics_per_run"]),
        "posts_per_day": c["posts_per_day"],
        "how_to_verify": ("This session may have NO internet for opening pages. Candidates with a text_file have their full "
                          "article text stored in the repository: read that file and copy quotes VERBATIM from it, with "
                          "evidence_url set to the candidate's url. Never quote text you did not read in a file or page."),
        "candidates": [{k: x[k] for k in ("id", "source", "title", "summary", "url", "text_file") if k in x}
                       for x in fresh[:60]],
        "already_covered": history[-40:],
        "rubric": rubric,
        "brand": (ROOT / "brand.md").read_text(encoding="utf-8"),
        "trusted_tier1_domains": load_json(CONFIG / "trusted_sources.json", {}).get("tier1", []),
        "schema": SCHEMA,
    }


if __name__ == "__main__":
    print(json.dumps(brief(), ensure_ascii=False, indent=2))
