#!/usr/bin/env python3
"""Publisher: posts at most ONE approved, due row of queue.csv to Instagram per run. No AI, stdlib only.

queue.csv columns: id, scheduled_at (UTC ISO 8601), media_urls (space separated, 1 = photo, 2-10 = carousel),
caption, status (pending | approved | posted | error), result.

Safety rules (each one has a test in tests/test_publisher.py):
- only status == approved and scheduled_at <= now is ever posted
- a bad row becomes status=error and never blocks the other rows
- never double-posts: if an identical caption is already live on the account, the row is marked posted
- queue.csv is saved right after every state change, before anything else can fail
- media URLs are checked (HTTP 200, image) before any container is created
- the daily publishing quota is respected; transient errors are retried, the final publish call never is
Env: IG_USER_ID, IG_TOKEN. DRY_RUN=1 prints what would be posted.
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# Default: "Instagram API with Instagram Login" (no Facebook Page needed). For a Facebook-Login token set the repository
# variable IG_GRAPH_BASE to https://graph.facebook.com/v21.0
BASE = (os.environ.get("IG_GRAPH_BASE") or "https://graph.instagram.com/v21.0").rstrip("/")
QUEUE = os.environ.get("QUEUE_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "queue.csv")
FIELDS = ["id", "scheduled_at", "media_urls", "caption", "status", "result", "posted_at"]
CONTROL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "control.json")
MAX_CAPTION = 2200


def call(method, path, retries=2, **params):
    """Graph API call. Retries 429/5xx/connection errors with backoff when retries > 0."""
    params["access_token"] = os.environ["IG_TOKEN"]
    data = urllib.parse.urlencode(params).encode()
    url = f"{BASE}/{path}"
    if method == "GET":
        url, data = url + "?" + data.decode(), None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, method=method)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"{e.code} {body[:300]}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"network error: {e}") from None


VIDEO_EXT = (".mp4", ".mov")


def is_reel(urls):
    return len(urls) == 1 and urls[0].lower().split("?")[0].endswith(VIDEO_EXT)


def purge_cdn(url):
    """jsDelivr caches branch names for hours: ask it to refresh a just-added file before Instagram fetches it."""
    if "cdn.jsdelivr.net/" in url:
        try:
            urllib.request.urlopen(url.replace("https://cdn.jsdelivr.net/", "https://purge.jsdelivr.net/"), timeout=30).read()
        except Exception:
            pass


def check_media(url):
    """Fail early, with a clear message, if Instagram would not be able to fetch the image or video."""
    video = url.lower().split("?")[0].endswith(VIDEO_EXT)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
            ctype = r.headers.get("Content-Type", "")
            ok = ctype.startswith("video/") or ctype == "application/octet-stream" if video else ctype.startswith("image/")
            if r.status != 200 or not ok:
                raise RuntimeError(f"media not usable ({r.status}, {ctype}): {url}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"media not reachable ({e.code}): {url}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"media not reachable ({e.reason}): {url}") from None


def wait_ready(container_id, tries=30, pause=4):
    for _ in range(tries):
        status = call("GET", container_id, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id} status {status}")
        time.sleep(pause)
    raise RuntimeError(f"container {container_id} not ready in time")


def already_live(uid, caption):
    """True if a recent post on the account has the same caption (protects against double posting)."""
    key = caption.strip()[:120]
    recent = call("GET", f"{uid}/media", fields="caption", limit=25).get("data", [])
    return any((m.get("caption") or "").strip()[:120] == key for m in recent)


def quota_left(uid):
    """False only when Instagram says today's publishing quota is used up; unknown means go ahead."""
    try:
        d = call("GET", f"{uid}/content_publishing_limit", fields="quota_usage,config").get("data", [{}])[0]
        return d.get("quota_usage", 0) < d.get("config", {}).get("quota_total", 100)
    except Exception:
        return True


def validate(row):
    urls = row["media_urls"].split()
    if not 1 <= len(urls) <= 10:
        raise RuntimeError(f"need 1-10 media urls, got {len(urls)}")
    if not row["caption"].strip():
        raise RuntimeError("empty caption")
    if len(row["caption"]) > MAX_CAPTION:
        raise RuntimeError(f"caption is {len(row['caption'])} chars, limit {MAX_CAPTION}")
    return urls


def publish(row, uid):
    urls = validate(row)
    for u in urls:
        purge_cdn(u)
        check_media(u)
    if is_reel(urls):
        parent = call("POST", f"{uid}/media", media_type="REELS", video_url=urls[0], caption=row["caption"],
                      share_to_feed="true", thumb_offset=1200)["id"]
        wait_ready(parent, tries=90, pause=5)  # video processing takes a while
        return call("POST", f"{uid}/media_publish", retries=0, creation_id=parent)["id"]
    if len(urls) == 1:
        parent = call("POST", f"{uid}/media", image_url=urls[0], caption=row["caption"])["id"]
    else:
        kids = []
        for u in urls:
            kid = call("POST", f"{uid}/media", image_url=u, is_carousel_item="true")["id"]
            wait_ready(kid)
            kids.append(kid)
        parent = call("POST", f"{uid}/media", media_type="CAROUSEL", children=",".join(kids), caption=row["caption"])["id"]
    wait_ready(parent)
    return call("POST", f"{uid}/media_publish", retries=0, creation_id=parent)["id"]


def read_queue(path=QUEUE):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_queue(rows, path=QUEUE):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)  # never leaves a half-written queue


def parse_time(s):
    return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))


def limit_reached(rows, now, max_per_day, min_gap_min):
    """Returns a reason string when posting now would break the daily cap or the minimum gap, else None."""
    times = []
    for r in rows:
        if r.get("status") == "posted" and r.get("posted_at"):
            try:
                times.append(parse_time(r["posted_at"]))
            except ValueError:
                continue
    if max_per_day and sum(1 for t in times if now - t < timedelta(hours=24)) >= max_per_day:
        return f"daily cap of {max_per_day} posts reached"
    if min_gap_min and any(now - t < timedelta(minutes=min_gap_min) for t in times):
        return f"last post was under {min_gap_min} minutes ago"
    return None


def backend():
    """Who actually posts: Buffer when BUFFER_API_KEY is set, otherwise Meta's Graph API (this module)."""
    if os.environ.get("BUFFER_API_KEY"):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import buffer_backend
        return buffer_backend
    return sys.modules[__name__]


def run(rows, uid, now=None, save=lambda rows: None, dry=False, max_per_day=0, min_gap_min=0):
    """Process one run. Returns (posted_id_or_None, had_error)."""
    now = now or datetime.now(timezone.utc)
    be = backend()
    why = limit_reached(rows, now, max_per_day, min_gap_min)
    if why:
        print(why + "; nothing posted this run")
        return None, False
    for row in rows:
        if row.get("status") != "approved":
            continue
        try:
            due = parse_time(row["scheduled_at"])
        except Exception:
            row["status"], row["result"] = "error", f"bad scheduled_at: {row.get('scheduled_at')!r}"
            save(rows)
            return None, True
        if due > now:
            continue
        if dry:
            print(f"[dry run] would post {row['id']}: {row['media_urls']}")
            return None, False
        if not be.quota_left(uid):
            print("daily publishing quota used up; will retry on a later run")
            return None, False
        try:
            if be.already_live(uid, row["caption"]):
                row["status"], row["result"] = "posted", "already-live"
            else:
                row["result"] = be.publish(row, uid)
                row["status"] = "posted"
                row["posted_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as e:
            row["status"], row["result"] = "error", str(e)[:300]
            save(rows)
            return None, True
        save(rows)  # right after the post, before anything else
        return row["result"], False
    return None, False


def load_control():
    try:
        with open(CONTROL, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        return {"mode": "off", "paused": True, "pause_reason": "config/control.json missing or unreadable"}
    return c


def main():
    c = load_control()
    if c.get("paused") or c.get("mode") not in ("live", "shadow"):
        print(f"not posting: mode={c.get('mode')} paused={c.get('paused')} {c.get('pause_reason', '')}")
        sys.exit(0)
    shadow = c.get("mode") == "shadow"
    if not shadow and not (os.environ.get("BUFFER_API_KEY") or (os.environ.get("IG_USER_ID") and os.environ.get("IG_TOKEN"))):
        print("mode is live but there is no BUFFER_API_KEY (or IG_USER_ID and IG_TOKEN) in the secrets (Settings > Secrets and variables > Actions)", file=sys.stderr)
        sys.exit(1)  # loud on purpose: live mode without credentials is a misconfiguration
    uid = os.environ.get("IG_USER_ID", "")
    rows = read_queue()
    posted, failed = run(rows, uid, save=write_queue, dry=shadow or bool(os.environ.get("DRY_RUN")),
                         max_per_day=int(c.get("posts_per_day", 5)), min_gap_min=int(c.get("min_gap_min", 60)))
    if posted:
        print(f"posted -> {posted}")
    if failed:
        bad = [f"{r['id']}: {r['result']}" for r in rows if r["status"] == "error"]
        print("ERROR " + "; ".join(bad), file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
