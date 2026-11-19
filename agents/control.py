"""Control panel for the autopilot: one small JSON file that decides whether anything runs at all.

mode: off    nothing runs (default, safe)
      shadow the whole pipeline runs and fills the queue, but NOTHING is posted (use this to judge quality)
      live   posts are published
paused: set by the watchdog (or by you) to stop posting without changing mode. Edit config/control.json
        from the GitHub app to flip any of this from your phone.
"""
import json
import math
import time
from datetime import datetime, timedelta, timezone

from common import CONFIG, STATE, load_json, save_json

WIB = timezone(timedelta(hours=7))
PATH = CONFIG / "control.json"
DEFAULT = {
    "mode": "off", "posts_per_day": 5, "slots_wib": ["07:30", "10:30", "13:30", "17:30", "20:30"],
    "buffer_days": 2, "max_usd_per_day": 1.0, "max_topics_per_run": 10, "min_score": 55,
    "max_per_pillar_per_day": 2, "reels_per_day": 2, "reel_slots_wib": ["12:00", "19:30"], "paused": False, "pause_reason": "", "max_consecutive_failures": 3,
}
MODES = ("off", "shadow", "live")


def load(path=None):
    c = {**DEFAULT, **load_json(path or PATH, {})}
    if c["mode"] not in MODES:
        c["mode"], c["paused"], c["pause_reason"] = "off", True, f"invalid mode in control.json"
    return c


def is_live(c):
    return c["mode"] == "live" and not c["paused"]


def pause(reason, path=None):
    c = load(path)
    c["paused"], c["pause_reason"] = True, reason
    save_json(path or PATH, c)
    return c


def spent_today(now=None, log_path=None):
    """USD spent on the WIB calendar day of `now`, from the usage log that is committed to the repo."""
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(WIB).date()
    p = log_path or (STATE / "usage.jsonl")
    if not p.exists():
        return 0.0
    total = 0.0
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if datetime.fromtimestamp(r.get("ts", 0), WIB).date() == day:
            total += r.get("usd", 0.0)
    return total


def next_slots(c, now, taken, count):
    """The next `count` free posting times (UTC ISO strings), from the slots_wib list, skipping taken ones."""
    out, day = [], now.astimezone(WIB).date()
    for offset in range(0, 30):
        for hhmm in sorted(c["slots_wib"]):
            h, m = (int(x) for x in hhmm.split(":"))
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=WIB) + timedelta(days=offset)
            iso = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if t.astimezone(timezone.utc) <= now + timedelta(minutes=45) or iso in taken:
                continue
            out.append(iso)
            if len(out) == count:
                return out
    return out


def topics_needed(need_posts, success_rate=0.6, cap=10):
    return min(cap, max(1, math.ceil(need_posts / success_rate)))
