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
from datetime import datetime, timezone

BASE = "https://graph.facebook.com/v21.0"
QUEUE = os.environ.get("QUEUE_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "queue.csv")
FIELDS = ["id", "scheduled_at", "media_urls", "caption", "status", "result"]
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


def check_media(url):
    """Fail early, with a clear message, if Instagram would not be able to fetch the image."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
            ctype = r.headers.get("Content-Type", "")
            if r.status != 200 or not ctype.startswith("image/"):
                raise RuntimeError(f"media not usable ({r.status}, {ctype}): {url}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"media not reachable ({e.code}): {url}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"media not reachable ({e.reason}): {url}") from None


def wait_ready(container_id):
    for _ in range(30):
        status = call("GET", container_id, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id} status {status}")
        time.sleep(4)
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
        check_media(u)
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


def run(rows, uid, now=None, save=lambda rows: None, dry=False):
    """Process one run. Returns (posted_id_or_None, had_error)."""
    now = now or datetime.now(timezone.utc)
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
        if not quota_left(uid):
            print("daily publishing quota used up; will retry on a later run")
            return None, False
        try:
            if already_live(uid, row["caption"]):
                row["status"], row["result"] = "posted", "already-live"
            else:
                row["result"] = publish(row, uid)
                row["status"] = "posted"
        except Exception as e:
            row["status"], row["result"] = "error", str(e)[:300]
            save(rows)
            return None, True
        save(rows)  # right after the post, before anything else
        return row["result"], False
    return None, False


def main():
    uid = os.environ["IG_USER_ID"]
    rows = read_queue()
    posted, failed = run(rows, uid, save=write_queue, dry=bool(os.environ.get("DRY_RUN")))
    if posted:
        print(f"posted -> {posted}")
    if failed:
        bad = [f"{r['id']}: {r['result']}" for r in rows if r["status"] == "error"]
        print("ERROR " + "; ".join(bad), file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
