#!/usr/bin/env python3
"""Autopilot: keeps the posting queue filled and healthy with no human in the loop.

  python agents/autopilot.py plan       fill the buffer: pick topics -> write -> fact-check -> queue (pending_media)
  python agents/autopilot.py finalize   after slides are rendered: rows with media become `approved`
  python agents/autopilot.py watchdog   health checks; pauses itself when something looks wrong

Everything is gated by config/control.json (mode off | shadow | live, paused). Exit code 1 from `watchdog`
means "tell the owner" (the workflow opens a GitHub issue).
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "publisher"))

import control  # noqa: E402
import pipeline  # noqa: E402
import publish  # noqa: E402
import scorer  # noqa: E402
import scout  # noqa: E402
from common import CONTENT, ROOT, STATE, Llm, load_json, save_json  # noqa: E402

QUEUE = Path(os.environ.get("QUEUE_FILE") or ROOT / "queue.csv")


# ---------- helpers (pure, tested) ----------

def ready_rows(rows, now):
    """Posts waiting for their time: approved or still waiting for media, and not yet due."""
    out = []
    for r in rows:
        if r.get("status") in ("approved", "pending_media"):
            try:
                if publish.parse_time(r["scheduled_at"]) > now:
                    out.append(r)
            except Exception:
                continue
    return out


def select_topics(topics, k, c):
    """Auto Gate 1. Mutates `topics` (decision), returns the chosen ones.
    high risk -> reject, medium risk -> hold for a human (never auto-posted), low risk -> candidate."""
    fresh = [t for t in topics if t.get("status") == "new" and not t.get("decision")]
    for t in fresh:
        if t.get("risk", "high") == "high":
            t["decision"], t["decided_by"] = "reject", "auto:risk"
        elif t.get("risk") == "medium":
            t["decision"], t["decided_by"] = "hold", "auto:risk"
    eligible = sorted((t for t in fresh if not t.get("decision") and t.get("score", 0) >= c["min_score"]),
                      key=lambda t: t["score"], reverse=True)
    per_pillar_cap = max(1, c["max_per_pillar_per_day"] * c["buffer_days"])
    counts, chosen = {}, []
    for t in eligible:
        p = t.get("pillar", "")
        if counts.get(p, 0) >= per_pillar_cap:
            continue
        counts[p] = counts.get(p, 0) + 1
        t["decision"], t["decided_by"] = "select", "auto"
        chosen.append(t)
        if len(chosen) == k:
            break
    return chosen


def caption_with_tags(post):
    return post["caption"].strip() + "\n\n" + " ".join("#" + h.lstrip("#") for h in post["hashtags"])


def enqueue(rows, posts, c, now):
    """Add one `pending_media` row per ready post. Reels take the prime-time reel slots, carousels the other slots."""
    taken = {r["scheduled_at"] for r in rows if r.get("status") != "held"}   # a held row does not use up its slot
    reel_slots = [x for x in c.get("reel_slots_wib", []) if x in c["slots_wib"]]
    other = [x for x in c["slots_wib"] if x not in reel_slots] or list(c["slots_wib"])
    groups = [([p for p in posts if p.get("format") == "reel"], {**c, "slots_wib": reel_slots or c["slots_wib"]}),
              ([p for p in posts if p.get("format") != "reel"], {**c, "slots_wib": other})]
    added = 0
    for group, cfg in groups:
        slots = control.next_slots(cfg, now, taken, len(group))
        for post, when in zip(group, slots):
            taken.add(when)
            rows.append({"id": post["id"], "scheduled_at": when, "media_urls": "", "caption": caption_with_tags(post),
                         "status": "pending_media", "result": "", "posted_at": ""})
            added += 1
    rows.sort(key=lambda r: r.get("scheduled_at") or "")
    return added


def prune_media(rows, now, media_dir, keep_days=3):
    """Delete the picture/video files of posts that went live more than `keep_days` ago, so the repository stays small.
    The queue row and content/<id>.json stay as the permanent record. Returns the number of files removed."""
    media_dir, removed = Path(media_dir), 0
    for r in rows:
        if r.get("status") != "posted" or not r.get("posted_at"):
            continue
        try:
            age = now - publish.parse_time(r["posted_at"])
        except Exception:
            continue
        if age.days >= keep_days:
            for f in list(media_dir.glob(f"{r['id']}-*.jpg")) + list(media_dir.glob(f"{r['id']}.mp4")):
                f.unlink()
                removed += 1
    return removed


def finalize(rows):
    """pending_media -> approved, but only when the slides really exist."""
    n = 0
    for r in rows:
        if r.get("status") == "pending_media" and r.get("media_urls", "").strip():
            r["status"] = "approved"
            n += 1
    return n


def health(rows, topics, c, now, usage_today, token_issued=None):
    """Returns (alerts, pause_reason_or_None)."""
    alerts, pause_reason = [], None
    posted = sorted((r for r in rows if r.get("status") in ("posted", "error") and r.get("scheduled_at")),
                    key=lambda r: r["scheduled_at"])
    n = c["max_consecutive_failures"]
    if len(posted) >= n and all(r["status"] == "error" for r in posted[-n:]):
        pause_reason = f"{n} posts in a row failed: {posted[-1].get('result', '')[:120]}"
    tried = [t for t in topics if t.get("status") in ("ready", "rejected")][-20:]
    if len(tried) >= 10 and sum(t["status"] == "rejected" for t in tried) / len(tried) >= 0.8:
        pause_reason = pause_reason or "the fact-checker rejected 80%+ of the last topics: check sources or prompts"
    if c["mode"] != "off":
        upcoming = len(ready_rows(rows, now))
        if upcoming < c["posts_per_day"]:
            alerts.append(f"queue is low: {upcoming} upcoming posts, need at least {c['posts_per_day']}")
        if usage_today >= 0.9 * c["max_usd_per_day"]:
            alerts.append(f"spent ${usage_today:.2f} of the ${c['max_usd_per_day']:.2f} daily cap")
    if token_issued:
        try:
            age = (now.date() - datetime.strptime(token_issued, "%Y-%m-%d").date()).days
            if age >= 50:
                alerts.append(f"IG_TOKEN is {age} days old and expires at about 60: renew it")
        except ValueError:
            alerts.append("IG_TOKEN_ISSUED is not a YYYY-MM-DD date")
    if pause_reason:
        alerts.append("PAUSED: " + pause_reason)
    return alerts, pause_reason


# ---------- commands ----------

def plan(llm, c, now=None, fetcher=scout.fetch):
    now = now or datetime.now(timezone.utc)
    if c["mode"] == "off":
        return {"skipped": "mode is off"}
    if c["paused"]:
        return {"skipped": f"paused: {c['pause_reason']}"}
    left = c["max_usd_per_day"] - control.spent_today(now)
    if left <= 0:
        return {"skipped": "daily budget used up"}
    llm.budget = min(llm.budget, left)

    rows = publish.read_queue(str(QUEUE)) if QUEUE.exists() else []
    need = c["posts_per_day"] * c["buffer_days"] - len(ready_rows(rows, now))
    if need <= 0:
        return {"skipped": "buffer is full"}

    topics = load_json(STATE / "topics.json", [])
    usable = [t for t in topics if t.get("status") == "new" and not t.get("decision") and t.get("risk") == "low"
              and t.get("score", 0) >= c["min_score"]]
    k = control.topics_needed(need, cap=c["max_topics_per_run"])
    if len(usable) < k:  # not enough fresh topics: look for more
        scout.run(llm, fetcher=fetcher, per_pillar=2)
        scorer.run(llm, top_n=20)
        topics = load_json(STATE / "topics.json", [])
    chosen = select_topics(topics, k, c)
    save_json(STATE / "topics.json", topics)
    made = pipeline.cmd_write(llm) if chosen else []

    posts = []
    for pid in made:
        post = load_json(CONTENT / f"{pid}.json", None)
        if post and post.get("status") == "ready_for_design":
            posts.append(post)
    added = enqueue(rows, posts, c, now)
    publish.write_queue(rows, str(QUEUE))
    for post in posts[:added]:
        post["status"] = "queued"
        save_json(CONTENT / f"{post['id']}.json", post)
    return {"needed": need, "topics_chosen": len(chosen), "posts_written": len(made), "queued": added,
            "spent_usd": round(llm.spent, 4)}


def run_watchdog(c, now=None):
    now = now or datetime.now(timezone.utc)
    rows = publish.read_queue(str(QUEUE)) if QUEUE.exists() else []
    topics = load_json(STATE / "topics.json", [])
    alerts, pause_reason = health(rows, topics, c, now, control.spent_today(now), os.environ.get("IG_TOKEN_ISSUED"))
    if pause_reason and not c["paused"]:
        control.pause(pause_reason)
    save_json(STATE / "health.json", {"at": now.isoformat(), "mode": c["mode"], "alerts": alerts})
    return alerts


def ticks_stale(now=None, hours=4, health_path=None):
    """None when the publish ticks are alive, else a sentence saying why not. The watchdog writes state/health.json on every
    tick, so an old (or missing) file means the ticks stopped. Run from the ingest workflow, which has its own trigger."""
    now = now or datetime.now(timezone.utc)
    try:
        at = datetime.fromisoformat(load_json(health_path or (STATE / "health.json"), {})["at"])
    except (KeyError, ValueError, TypeError):
        return "state/health.json has no readable timestamp: no tick has run yet"
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    age = now - at
    if age > timedelta(hours=hours):
        return f"the last tick ran {age.total_seconds() / 3600:.1f} hours ago (limit {hours}): publishing may have stopped"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "finalize", "watchdog", "prune", "stale"])
    ap.add_argument("--hours", type=float, default=4)
    a = ap.parse_args()
    c = control.load()
    if a.cmd == "stale":
        why = ticks_stale(hours=a.hours)
        print(why or "ticks are alive")
        sys.exit(1 if why else 0)
    if a.cmd == "plan":
        print(plan(Llm(), c))
    elif a.cmd == "finalize":
        rows = publish.read_queue(str(QUEUE))
        n = finalize(rows)
        publish.write_queue(rows, str(QUEUE))
        print(f"approved {n} rows with finished slides")
    elif a.cmd == "prune":
        rows = publish.read_queue(str(QUEUE))
        print(f"removed {prune_media(rows, datetime.now(timezone.utc), ROOT / 'media')} old media files")
    else:
        alerts = run_watchdog(c)
        for x in alerts:
            print("ALERT:", x)
        sys.exit(1 if alerts else 0)


if __name__ == "__main__":
    main()
