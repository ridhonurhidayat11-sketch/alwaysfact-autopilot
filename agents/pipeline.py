#!/usr/bin/env python3
"""Orchestrator for the content agents (plain code, no AI).

  python agents/pipeline.py scout [--brainstorm N]   candidates from real feeds (+ N ideas per pillar)
  python agents/pipeline.py score [--top 10]         rate, dedupe, write state/topics.json + topics.csv  (Gate 1 list)
  python agents/pipeline.py write                    for topics you marked decision=select: write + fact-check
  python agents/pipeline.py report                   token and cost summary

Gate 1 = you set "decision" to select (or reject) in state/topics.json or topics.csv.
A post reaches content/<id>.json only if every claim passed the fact-checker. Max 2 rewrites, then it is rejected.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import factcheck  # noqa: E402
import scorer  # noqa: E402
import scout  # noqa: E402
import writer  # noqa: E402
from common import CONTENT, STATE, Llm, load_json, next_post_id, save_json  # noqa: E402

MAX_REWRITES = 2


def make_post(llm, topic):
    """Write -> validate -> fact-check, with up to MAX_REWRITES guided rewrites. Returns (post, report)."""
    feedback, report = None, []
    for attempt in range(MAX_REWRITES + 1):
        post = writer.write_post(llm, topic, feedback)
        problems = writer.validate_post(post)
        if problems:
            report.append({"attempt": attempt, "stage": "structure", "problems": problems})
            feedback = problems
            continue
        ok, fc_problems, verdicts = factcheck.check_post(llm, post)
        report.append({"attempt": attempt, "stage": "factcheck", "ok": ok, "problems": fc_problems})
        if ok:
            post["verification"] = verdicts
            return post, report
        feedback = fc_problems
    return None, report


def cmd_write(llm):
    topics = load_json(STATE / "topics.json", [])
    made = []
    for t in topics:
        if t.get("decision") != "select" or t.get("status") != "new":
            continue
        post, report = make_post(llm, t)
        t["report"] = report
        if post is None:
            t["status"] = "rejected"
            continue
        pid = next_post_id()
        post.update({"id": pid, "topic_id": t["id"], "status": "ready_for_design"})
        save_json(CONTENT / f"{pid}.json", post)
        t["status"], t["post_id"] = "ready", pid
        made.append(pid)
        save_json(STATE / "topics.json", topics)  # save after each post: a later failure loses nothing
    save_json(STATE / "topics.json", topics)
    return made


def cmd_report():
    rows = [__import__("json").loads(x) for x in (STATE / "usage.jsonl").read_text().splitlines() if x.strip()] \
        if (STATE / "usage.jsonl").exists() else []
    by = {}
    for r in rows:
        a = by.setdefault(r["agent"], {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
        a["calls"] += 1
        a["in"] += r["in"]
        a["out"] += r["out"]
        a["usd"] += r["usd"]
    for k, v in sorted(by.items()):
        print(f"{k:18} calls={v['calls']:3} in={v['in']:7} out={v['out']:7} ${v['usd']:.4f}")
    print(f"{'TOTAL':18} ${sum(v['usd'] for v in by.values()):.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scout", "score", "write", "report"])
    ap.add_argument("--brainstorm", type=int, default=0, help="ideas per pillar from the model (needs fact-check)")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    if a.cmd == "report":
        return cmd_report()
    llm = Llm()
    if a.cmd == "scout":
        cands, errors = scout.run(llm if a.brainstorm else None, per_pillar=a.brainstorm)
        print(f"{len(cands)} candidates; feed errors: {errors or 'none'}")
    elif a.cmd == "score":
        topics, dropped = scorer.run(llm, a.top)
        print(f"{len(topics)} topics for Gate 1 (state/topics.csv); dropped: {len(dropped)}")
    else:
        made = cmd_write(llm)
        print(f"ready for design: {made or 'none'}")


if __name__ == "__main__":
    main()
