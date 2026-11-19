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
    "image_query": "<English search words for ONE real photo of the literal main subject, e.g. 'sea pig Scotoplanes' or 'hydrogen sulfide molecule'. Never a metaphor, never a pun, never an abstract idea. Photos come from Wikimedia Commons and NASA only; if the subject has no photo, the slide design is typographic.>",
    "pillar": "<one of the pillars>",
    "hook_options": ["<hook 1, max 70 chars>", "<hook 2, different>"],
    "slides": [ {"type": "hook|fact|compare|chart|cta", "title": "<max 70>", "body": "<max 220>",
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
Rules: 6-8 slides; slide 1 type hook with title equal to hook_options[0]; last slide type cta; every factual
statement in slides or caption must appear in claims; claim numbers in verdicts follow the order of claims;
every number in a claim must also appear in its quote."""


def brief(now=None, queue_path=None):
    now = now or datetime.now(timezone.utc)
    c = control.load()
    if c["mode"] == "off" or c["paused"]:
        return {"go": False, "reason": f"mode={c['mode']} paused={c['paused']} {c['pause_reason']}".strip()}
    qpath = Path(queue_path or autopilot.QUEUE)
    rows = publish.read_queue(str(qpath)) if qpath.exists() else []
    need = c["posts_per_day"] * c["buffer_days"] - len(autopilot.ready_rows(rows, now))
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
