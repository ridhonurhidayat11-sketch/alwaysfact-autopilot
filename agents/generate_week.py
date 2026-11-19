#!/usr/bin/env python3
"""Weekly content agent: one Claude call drafts a week of carousel posts into queue.csv (status=pending).

One call does ideas + slide text + caption together, so the shared context is paid for once.
Env: ANTHROPIC_API_KEY. Optional: POSTS_PER_WEEK (7), POST_HOUR_UTC (5 = 12:00 WIB),
MODEL (claude-haiku-5-5), DRY_RUN=1 (canned output, no API call, no cost).
"""
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "queue.csv"
CONTENT = ROOT / "content"
FIELDS = ["id", "scheduled_at", "media_urls", "caption", "status", "result"]
N = int(os.environ.get("POSTS_PER_WEEK", "7"))
HOUR = int(os.environ.get("POST_HOUR_UTC", "5"))
MODEL = os.environ.get("MODEL", "claude-haiku-5-5")


def read_queue():
    if not QUEUE.exists():
        return []
    with open(QUEUE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_queue(rows):
    with open(QUEUE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def build_prompt(recent):
    brand = (ROOT / "brand.md").read_text(encoding="utf-8")
    seen = "\n".join(f"- {c[:80]}" for c in recent) or "- (none yet)"
    system = brand
    user = (
        f"Write {N} Instagram carousel posts for the coming week.\n"
        f"Do not repeat or closely paraphrase these recent posts:\n{seen}\n\n"
        "Return ONLY a JSON array, no prose, no code fences. Each item:\n"
        '{"topic": str, "slides": [{"title": str, "body": str}, ...5 to 7 items], '
        '"caption": str, "hashtags": [str, ...5 to 8 items]}\n'
        "Slide 1 is the hook (title only, body may be empty). The last slide is a short call to action. "
        "Keep each slide body under 220 characters. Every claim must be a real, checkable fact; "
        "put the source name in the caption."
    )
    return system, user


def parse(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    data = json.loads(text)
    if not isinstance(data, list) or not data:
        raise ValueError("expected a non-empty JSON array")
    for p in data:
        assert p["slides"] and p["caption"], "post missing slides or caption"
    return data


def call_model(system, user):
    import anthropic

    client = anthropic.Anthropic()
    for attempt in (1, 2):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        print(f"tokens in={resp.usage.input_tokens} out={resp.usage.output_tokens}", file=sys.stderr)
        try:
            return parse(text)
        except Exception as e:
            print(f"parse failed (attempt {attempt}): {e}", file=sys.stderr)
    raise SystemExit("model did not return valid JSON twice; nothing written")


def canned():
    return [
        {
            "topic": f"Contoh topik {i + 1}",
            "slides": [{"title": f"Hook {i + 1}", "body": ""}, {"title": "Fakta", "body": "Isi contoh."},
                       {"title": "Ikuti @alwaysfact_", "body": "Fakta baru setiap hari."}],
            "caption": f"Caption contoh {i + 1}. Sumber: contoh.",
            "hashtags": ["fakta", "alwaysfact"],
        }
        for i in range(N)
    ]


def main():
    rows = read_queue()
    recent = [r["caption"] for r in rows[-10:]]
    posts = canned() if os.environ.get("DRY_RUN") else call_model(*build_prompt(recent))
    posts = posts[:N]

    last = max((datetime.fromisoformat(r["scheduled_at"].replace("Z", "+00:00")) for r in rows), default=None)
    day = max(datetime.now(timezone.utc) + timedelta(days=1), (last or datetime.min.replace(tzinfo=timezone.utc)) + timedelta(days=1))
    day = day.replace(hour=HOUR, minute=0, second=0, microsecond=0)
    next_id = max((int(r["id"]) for r in rows if r["id"].isdigit()), default=0) + 1

    CONTENT.mkdir(exist_ok=True)
    for i, p in enumerate(posts):
        pid = f"{next_id + i:03d}"
        (CONTENT / f"{pid}.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        caption = p["caption"].strip() + "\n\n" + " ".join("#" + h.lstrip("#") for h in p["hashtags"])
        rows.append({
            "id": pid,
            "scheduled_at": (day + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "media_urls": "",
            "caption": caption,
            "status": "pending",
            "result": "",
        })
    write_queue(rows)
    print(f"added {len(posts)} pending posts")


if __name__ == "__main__":
    main()
