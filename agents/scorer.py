"""Scorer: rates candidates against the rubric, removes repeats, and proposes the top N for Gate 1.

The model only gives 1-5 ratings per criterion. The weighted total, the ranking and the duplicate
filter are plain code, so the result is reproducible and testable.
"""
import csv

from common import CONFIG, HAIKU, STATE, extract_json, history_titles, is_duplicate, load_json, save_json

SYSTEM = (
    "You are a strict editor choosing topics for an Indonesian Instagram account about mind-blowing science "
    "facts that people dismiss as trivial. Rate honestly: most candidates are average (2-3). Reply with JSON only."
)
CHUNK = 20


def weighted(scores, rubric):
    """0-100 from 1-5 ratings. Raises ValueError on missing or out-of-range ratings."""
    total = 0.0
    for c in rubric["criteria"]:
        v = scores.get(c["key"])
        if not isinstance(v, (int, float)) or not 1 <= v <= 5:
            raise ValueError(f"bad rating for {c['key']}: {v!r}")
        total += c["weight"] * (v / 5)
    return round(total * 100, 1)


def build_prompt(batch, rubric):
    crit = "\n".join(f"- {c['key']}: {c['question']}" for c in rubric["criteria"])
    pillars = "; ".join(rubric["pillars"])
    cands = "\n".join(f"[{c['id']}] ({c['origin']}) {c['title']} :: {c['summary']}" for c in batch)
    return (
        f"Rate each candidate 1-5 on every criterion:\n{crit}\n\nPillars: {pillars}\n\nCandidates:\n{cands}\n\n"
        'Return a JSON array, one item per candidate: {"id": str, "scores": {"surprise": int, "simple": int, '
        '"shareable": int, "source": int, "visual": int, "fresh": int}, "pillar": str, "angle": str (hook idea in '
        'Indonesian, max 110 chars, no clickbait lies), "reason": str (max 120 chars)}. '
        'Candidates from "brainstorm" have no source URL yet: rate "source" at most 3.'
    )


def score(llm, candidates, rubric, history, top_n=10):
    rated, dropped = [], []
    by_id = {c["id"]: c for c in candidates}
    for i in range(0, len(candidates), CHUNK):
        batch = candidates[i:i + CHUNK]
        reply = extract_json(llm.call("scorer", HAIKU, SYSTEM, build_prompt(batch, rubric), max_tokens=6000))
        for r in reply:
            c = by_id.get(r.get("id"))
            if not c:
                continue
            try:
                total = weighted(r.get("scores", {}), rubric)
            except ValueError as e:
                dropped.append(f"{c['id']}: {e}")
                continue
            if is_duplicate(c["title"], history):
                dropped.append(f"{c['id']}: repeat of an earlier topic")
                continue
            rated.append({**c, "score": total, "scores": r["scores"], "pillar": r.get("pillar") or c.get("pillar", ""),
                          "angle": (r.get("angle") or "")[:160], "reason": (r.get("reason") or "")[:160],
                          "decision": "", "status": "new"})
    rated.sort(key=lambda t: t["score"], reverse=True)
    picked = []
    for t in rated:  # also drop near-duplicates inside this very list, keep the higher score
        if not is_duplicate(t["title"], [p["title"] for p in picked]):
            picked.append(t)
    return picked[:top_n], dropped


def export_csv(topics, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "decision", "score", "pillar", "title", "angle", "source", "url"])
        for t in topics:  # .get: a hand-edited topic with a missing field must not break the export
            w.writerow([t.get(k, "") for k in ("id", "decision", "score", "pillar", "title", "angle", "source", "url")])


def run(llm, top_n=10):
    cands = load_json(STATE / "candidates.json", [])
    rubric = load_json(CONFIG / "rubric.json", None)
    existing = load_json(STATE / "topics.json", [])
    keep = [t for t in existing if t.get("status") != "new" or t.get("decision")]  # your Gate 1 choices survive
    history = history_titles() + [t["title"] for t in keep]
    topics, dropped = score(llm, cands, rubric, history, top_n)
    merged = keep + [t for t in topics if t["id"] not in {k["id"] for k in keep}]
    save_json(STATE / "topics.json", merged)
    export_csv(merged, STATE / "topics.csv")
    return merged, dropped
