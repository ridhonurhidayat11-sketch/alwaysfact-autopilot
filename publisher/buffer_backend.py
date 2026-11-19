#!/usr/bin/env python3
"""Posting through Buffer (https://api.buffer.com, GraphQL) instead of Meta's Graph API. stdlib only.

Why: Buffer connects to the Instagram account itself (Instagram login, no Facebook Page, no own Meta developer app) and
the personal API key does not need the 60-day Instagram token dance. Used automatically when BUFFER_API_KEY is set.

Env: BUFFER_API_KEY (secret), BUFFER_CHANNEL_ID (optional: otherwise the connected Instagram channel is found itself),
     BUFFER_DRAFT=1 (create drafts only: nothing is published; used to test the whole chain safely).
Command line:  python publisher/buffer_backend.py check            lists organisation and Instagram channel, posts nothing
               python publisher/buffer_backend.py draft <row id>   puts that queue row into Buffer as a DRAFT (not published)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.buffer.com"
_CACHE = {}


def gql(query, key=None, retries=2):
    """One GraphQL call. Returns the `data` dict; raises RuntimeError with Buffer's own message on any error."""
    key = key or os.environ["BUFFER_API_KEY"]
    body = json.dumps({"query": query}).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(API, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "alwaysfact-autopilot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.load(r)
            break
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"Buffer HTTP {e.code}: {text}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"Buffer network error: {e}") from None
    if out.get("errors"):
        raise RuntimeError("Buffer error: " + "; ".join(str(e.get("message", e))[:160] for e in out["errors"]))
    return out.get("data") or {}


def lit(s):
    """A string as a GraphQL literal (JSON string syntax is valid GraphQL)."""
    return json.dumps(s, ensure_ascii=False)


def organizations(key=None):
    return (gql("query { account { organizations { id name } } }", key).get("account") or {}).get("organizations") or []


def instagram_channel(key=None):
    """(organization_id, channel_id) of the connected Instagram channel."""
    if "channel" in _CACHE:
        return _CACHE["channel"]
    for org in organizations(key):
        chans = gql(f'query {{ channels(input: {{ organizationId: {lit(org["id"])} }}) {{ id name service }} }}', key).get("channels") or []
        want = os.environ.get("BUFFER_CHANNEL_ID")
        for c in chans:
            if (want and c["id"] == want) or (not want and str(c.get("service", "")).lower() == "instagram"):
                _CACHE["channel"] = (org["id"], c["id"])
                return _CACHE["channel"]
    raise RuntimeError("no Instagram channel is connected in Buffer (Settings > Channels)")


def quota_left(uid=None):
    return True   # Buffer applies its own plan limits and answers with a clear error when they are hit


def already_live(uid, caption):
    """True when this caption is already in Buffer (scheduled, sent or draft): protects against double posting."""
    try:
        org, ch = instagram_channel()
        data = gql(f'query {{ posts(first: 30, input: {{ organizationId: {lit(org)}, filter: {{ channelIds: [{lit(ch)}] }} }}) '
                   f'{{ edges {{ node {{ id text }} }} }} }}')
        key = caption.strip()[:120]
        return any((e["node"].get("text") or "").strip()[:120] == key for e in (data.get("posts") or {}).get("edges", []))
    except Exception:   # the duplicate check must never block posting; the queue itself also records what was posted
        return False


def build_mutation(row, channel_id, mode="shareNow", draft=False, due=None):
    urls = row["media_urls"].split()
    reel = len(urls) == 1 and urls[0].lower().split("?")[0].endswith((".mp4", ".mov"))
    if reel:
        assets = f'{{ video: {{ url: {lit(urls[0])}, metadata: {{ thumbnailOffset: 1200 }} }} }}'
        meta = "metadata: { instagram: { type: reel, shouldShareToFeed: true } }"
    else:
        assets = " ".join(f'{{ image: {{ url: {lit(u)} }} }}' for u in urls)
        meta = "metadata: { instagram: { type: post, shouldShareToFeed: true } }"
    extra = (f", dueAt: {lit(due)}" if due else "") + (", saveToDraft: true" if draft else "")
    return (f'mutation {{ createPost(input: {{ text: {lit(row["caption"])}, channelId: {lit(channel_id)}, schedulingType: automatic, '
            f'mode: {mode}{extra}, assets: [{assets}], {meta} }}) {{ ... on PostActionSuccess {{ post {{ id status dueAt }} }} '
            f'... on MutationError {{ message }} }} }}')


def create(row, draft=False):
    from datetime import timezone as tz
    org, ch = instagram_channel()
    attempts = [("addToQueue", None)] if draft else [("shareNow", None), ("customScheduled", (datetime.now(tz.utc) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z"))]
    last = ""
    for mode, due in attempts:
        res = (gql(build_mutation(row, ch, mode, draft, due)).get("createPost") or {})
        post = res.get("post")
        if post:
            return post["id"], post.get("status", "")
        last = res.get("message", "no answer from Buffer")
        if "mode" not in last.lower() and "enum" not in last.lower():
            break   # a real problem (media, caption, channel): the other mode would fail the same way
    raise RuntimeError("Buffer refused the post: " + last[:200])


def publish(row, uid=None):
    import publish as P
    urls = P.validate(row)
    for u in urls:
        P.purge_cdn(u)
        P.check_media(u)
    pid, status = create(row, draft=os.environ.get("BUFFER_DRAFT") == "1")
    return f"buffer:{pid}:{status}"


def main(argv):
    if not os.environ.get("BUFFER_API_KEY"):
        print("BUFFER_API_KEY is not set")
        return 1
    if argv and argv[0] == "check":
        for o in organizations():
            print("organization:", o["name"])
        org, ch = instagram_channel()
        print("instagram channel found; posting is possible (nothing was posted)")
        return 0
    if len(argv) >= 2 and argv[0] == "draft":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import publish as P
        row = next((r for r in P.read_queue() if r["id"] == argv[1]), None)
        if not row:
            print("no such row")
            return 1
        pid, status = create(row, draft=True)
        print(f"draft created in Buffer (status {status}); it is NOT published")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
