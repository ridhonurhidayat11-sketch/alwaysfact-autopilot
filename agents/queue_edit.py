#!/usr/bin/env python3
"""Small, safe queue controls (run by the 'Queue edit' workflow, usable from a phone):

  python agents/queue_edit.py hold 008,013 "reel design under revision"
  python agents/queue_edit.py release 012
  python agents/queue_edit.py retry 009
  python agents/queue_edit.py at 009 2026-11-20T08:30:00Z
  python agents/queue_edit.py only-formats carousel "reels are on hold today"
  python agents/queue_edit.py reslot 2026-11-20T18:30:00Z

hold    approved / pending_media rows become `held` (the publisher never touches held rows)
release held rows that have media become `approved` again
retry   a row wrongly marked posted by the duplicate check (result already-live) goes back to approved
at      change the publishing time (UTC ISO) of a not-yet-posted row
only-formats  hold every waiting row whose format (carousel or reel) is not the one given
reslot  give every waiting row the next free slot from slots_wib after the given UTC time, keeping their order (held rows are skipped)
Posted rows are never modified.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "publisher"))
sys.path.insert(0, str(ROOT / "agents"))
import control  # noqa: E402
import publish  # noqa: E402

WAITING = ("approved", "pending_media")


def fmt(row_id, content_dir=ROOT / "content"):
    try:
        return json.loads((Path(content_dir) / f"{row_id}.json").read_text(encoding="utf-8")).get("format", "carousel")
    except (OSError, ValueError):
        return "carousel"


def hold(rows, ids, reason=""):
    n = 0
    for r in rows:
        if r["id"] in ids and r["status"] in WAITING:
            r["status"], r["result"] = "held", (reason or "held by hand")[:120]
            n += 1
    return n


def release(rows, ids):
    n = 0
    for r in rows:
        if r["id"] in ids and r["status"] == "held" and r.get("media_urls", "").strip():
            r["status"], r["result"] = "approved", ""
            n += 1
    return n


def retry(rows, ids):
    """Put rows that were marked posted only because a duplicate check matched (result 'already-live') back in the queue."""
    n = 0
    for r in rows:
        if r["id"] in ids and r["status"] == "posted" and r.get("result") == "already-live":
            r["status"], r["result"], r["posted_at"] = "approved", "", ""
            n += 1
    return n


def reschedule(rows, row_id, when):
    datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ")  # raises on a bad time
    for r in rows:
        if r["id"] == row_id and r["status"] in WAITING + ("held",):
            r["scheduled_at"] = when
            return 1
    return 0


def only_formats(rows, keep, reason="", content_dir=ROOT / "content"):
    n = 0
    for r in rows:
        if r["status"] in WAITING and fmt(r["id"], content_dir) != keep:
            r["status"], r["result"] = "held", (reason or f"only {keep} posts today")[:120]
            n += 1
    return n


def reslot(rows, c, start):
    """Waiting rows (approved / pending_media, not held) get the next free posting slots after `start`, in their current order."""
    start_dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    waiting = sorted((r for r in rows if r["status"] in WAITING), key=lambda r: (r.get("scheduled_at") or "", r["id"]))
    slots = control.next_slots(c, start_dt, set(), len(waiting))
    for r, when in zip(waiting, slots):
        r["scheduled_at"] = when
    rows.sort(key=lambda r: r.get("scheduled_at") or "")
    return min(len(waiting), len(slots))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, qpath = argv[0], str(ROOT / "queue.csv")
    rows = publish.read_queue(qpath)
    ids = set(argv[1].replace(" ", "").split(",")) if cmd in ("hold", "release", "retry") else set()
    if cmd == "hold":
        n = hold(rows, ids, argv[2] if len(argv) > 2 else "")
    elif cmd == "release":
        n = release(rows, ids)
    elif cmd == "retry":
        n = retry(rows, ids)
    elif cmd == "at":
        n = reschedule(rows, argv[1], argv[2])
    elif cmd == "only-formats":
        n = only_formats(rows, argv[1], argv[2] if len(argv) > 2 else "")
    elif cmd == "reslot":
        n = reslot(rows, control.load(), argv[1])
    else:
        print(__doc__)
        return 2
    publish.write_queue(rows, qpath)
    print(f"{cmd}: {n} row(s) changed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
