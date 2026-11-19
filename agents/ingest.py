#!/usr/bin/env python3
"""Ingest referee: decides which posts proposed by the Claude routine are allowed into the queue.

The routine (an LLM) writes proposals to inbox/*.json. This script, plain code, accepts a proposal only if:
  - risk is "low", the structure is valid, and the topic is not a repeat
  - EVERY claim has a verbatim quote that really exists on a page this code fetched itself,
    from a tier-1 source or two independent sites, and the claim's numbers appear in the quote
Accepted posts get an id, are saved to content/, and take the next free posting slots (status pending_media).
Nothing here calls an AI, so it costs nothing and cannot be talked into anything.

  python agents/ingest.py [inbox_dir]
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "publisher"))

import autopilot  # noqa: E402
import control  # noqa: E402
import evidence  # noqa: E402
import factcheck  # noqa: E402
import publish  # noqa: E402
import writer  # noqa: E402
from common import CONFIG, CONTENT, ROOT, STATE, history_titles, is_duplicate, load_json, next_post_id, save_json  # noqa: E402


def digest(data):
    """Identity of a proposal = post AND evidence, so a re-submission with corrected evidence is judged again."""
    body = {"post": data.get("post"), "verdicts": data.get("verdicts")}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def judge(data, tier1, fetcher, history):
    """Returns (accepted, reasons, checked_verdicts)."""
    post, verdicts = data.get("post"), data.get("verdicts")
    if not isinstance(post, dict) or not isinstance(verdicts, list):
        return False, ["malformed proposal"], []
    if data.get("risk") != "low":
        return False, [f"risk is {data.get('risk')!r}; only 'low' is posted automatically"], []
    problems = writer.validate_post(post)
    if problems:
        return False, problems, []
    if is_duplicate(post.get("topic", ""), history):
        return False, ["topic repeats an earlier post"], []
    checked, notes = evidence.verify_verdicts(post, verdicts, fetcher)
    ok, fc = factcheck.passes(post, checked, tier1)
    return (ok, fc + notes, checked)


def run(inbox, c, now=None, fetcher=evidence.fetch_text, rows=None, queue_path=None):
    now = now or datetime.now(timezone.utc)
    if c["mode"] == "off":
        return {"skipped": "mode is off"}
    if c["paused"]:
        return {"skipped": f"paused: {c['pause_reason']}"}
    qpath = str(queue_path or autopilot.QUEUE)
    rows = publish.read_queue(qpath) if rows is None and Path(qpath).exists() else (rows or [])
    tier1 = load_json(CONFIG / "trusted_sources.json", {}).get("tier1", [])
    seen = load_json(STATE / "ingested.json", [])
    used = load_json(STATE / "used.json", [])
    history = history_titles()
    need = c["posts_per_day"] * c["buffer_days"] - len(autopilot.ready_rows(rows, now))
    accepted, report = [], []
    for path in sorted(Path(inbox).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            report.append({"file": path.name, "result": "rejected", "reasons": ["not valid JSON"]})
            continue
        h = digest(data) if isinstance(data, dict) else path.name
        if h in seen:
            continue
        ok, reasons, checked = judge(data, tier1, fetcher, history)
        if ok and len(accepted) >= need:
            report.append({"file": path.name, "result": "waiting", "reasons": ["queue is already full"]})
            continue  # not marked as seen: it can be used tomorrow
        seen.append(h)
        if not ok:
            report.append({"file": path.name, "result": "rejected", "reasons": reasons[:6]})
            continue
        post = data["post"]
        pid = next_post_id()
        post.update(id=pid, status="ready_for_design", verification=checked, origin="routine",
                    source_url=data.get("source_url", ""), topic_id=data.get("topic_id", ""))
        save_json(CONTENT / f"{pid}.json", post)
        history.append(post["topic"])
        used.append({"url": data.get("source_url", ""), "title": post["topic"]})
        accepted.append(post)
        report.append({"file": path.name, "result": "accepted", "post": pid})
    added = autopilot.enqueue(rows, accepted, c, now)
    publish.write_queue(rows, qpath)
    for p in accepted[:added]:
        p["status"] = "queued"
        save_json(CONTENT / f"{p['id']}.json", p)
    save_json(STATE / "ingested.json", seen[-500:])
    save_json(STATE / "used.json", used[-500:])
    log = STATE / "ingest_log.jsonl"
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now.isoformat(), "need": need, "report": report}, ensure_ascii=False) + "\n")
    return {"need": need, "accepted": len(accepted), "queued": added,
            "rejected": sum(r["result"] == "rejected" for r in report), "report": report}


def main():
    inbox = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "inbox"
    out = run(inbox, control.load())
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
