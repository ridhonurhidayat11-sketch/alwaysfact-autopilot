import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agents"))
sys.path.insert(0, str(HERE.parent / "publisher"))
sys.path.insert(0, str(HERE))

import autopilot  # noqa: E402
import common  # noqa: E402
import control  # noqa: E402
import factcheck  # noqa: E402
import pipeline  # noqa: E402
import publish as P  # noqa: E402
from test_agents import FakeClient, good_post, llm_for, resp, verdicts  # noqa: E402
from test_publisher import FakeApi, row  # noqa: E402

NOW = datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc)  # 08:00 WIB
TIER1 = ["nasa.gov", "nature.com", ".edu"]


def ctl(**kw):
    c = copy.deepcopy(control.DEFAULT)
    c.update({"mode": "shadow", **kw})
    return c



_SAVED_KEY = {}


def setUpModule():
    """These tests use a fake Meta API; a real BUFFER_API_KEY in the environment (as in GitHub Actions) must not redirect them to Buffer."""
    _SAVED_KEY["v"] = os.environ.pop("BUFFER_API_KEY", None)


def tearDownModule():
    if _SAVED_KEY.get("v") is not None:
        os.environ["BUFFER_API_KEY"] = _SAVED_KEY["v"]

class SourceRuleTests(unittest.TestCase):
    def ok(self, first, second=""):
        return factcheck.evidence_ok({"evidence_url": first, "second_evidence_url": second}, TIER1)

    def test_one_tier1_source_is_enough(self):
        self.assertTrue(self.ok("https://science.nasa.gov/venus"))
        self.assertTrue(self.ok("https://www.nature.com/articles/x"))
        self.assertTrue(self.ok("https://physics.mit.edu/x"))

    def test_one_ordinary_source_is_not_enough(self):
        self.assertFalse(self.ok("https://someblog.com/venus"))

    def test_two_independent_sites_are_enough(self):
        self.assertTrue(self.ok("https://someblog.com/a", "https://othernews.org/b"))

    def test_two_pages_of_the_same_site_do_not_count(self):
        self.assertFalse(self.ok("https://news.someblog.com/a", "https://www.someblog.com/b"))

    def test_insecure_or_missing_urls_fail(self):
        self.assertFalse(self.ok("http://science.nasa.gov/x"))
        self.assertFalse(self.ok(""))
        self.assertFalse(self.ok("https://nasa.gov.evil.example/x"))  # lookalike domain

    def test_passes_uses_the_source_rule(self):
        v = verdicts(True)
        v[0]["evidence_url"] = "https://someblog.com/x"
        ok, problems = factcheck.passes(good_post(), v, TIER1)
        self.assertFalse(ok)
        self.assertIn("tier-1", problems[0])


class ControlTests(unittest.TestCase):
    def test_slots_convert_wib_to_utc_and_skip_past_and_taken(self):
        c = ctl()
        slots = control.next_slots(c, NOW, {"2026-11-20T03:30:00Z"}, 4)  # 10:30 WIB taken
        self.assertEqual(slots[0], "2026-11-20T06:30:00Z")  # 13:30 WIB, since 07:30 is past and 10:30 taken
        self.assertEqual(len(slots), 4)
        self.assertEqual(slots[2], "2026-11-20T13:30:00Z")  # 20:30 WIB
        self.assertEqual(slots[3], "2026-11-21T00:30:00Z")  # 07:30 WIB next day

    def test_slot_too_close_to_now_is_skipped(self):
        c = ctl(slots_wib=["08:20", "12:00"])
        self.assertEqual(control.next_slots(c, NOW, set(), 1), ["2026-11-20T05:00:00Z"])  # 08:20 is under 45 min away

    def test_spent_today_counts_only_the_wib_day(self):
        d = Path(tempfile.mkdtemp()) / "usage.jsonl"
        wib = timezone(timedelta(hours=7))
        today = datetime(2026, 11, 20, 9, 0, tzinfo=wib).timestamp()
        yesterday = datetime(2026, 11, 19, 23, 0, tzinfo=wib).timestamp()
        d.write_text(json.dumps({"ts": today, "usd": 0.25}) + "\n" + json.dumps({"ts": yesterday, "usd": 9}) + "\nbroken\n")
        self.assertAlmostEqual(control.spent_today(NOW, d), 0.25)

    def test_invalid_mode_fails_safe(self):
        p = Path(tempfile.mkdtemp()) / "c.json"
        p.write_text('{"mode": "turbo"}')
        c = control.load(p)
        self.assertEqual((c["mode"], c["paused"]), ("off", True))

    def test_missing_file_means_defaults_which_are_off(self):
        self.assertEqual(control.load(Path("/nonexistent/c.json"))["mode"], "off")

    def test_pause_persists(self):
        p = Path(tempfile.mkdtemp()) / "c.json"
        control.pause("because", p)
        self.assertTrue(control.load(p)["paused"])

    def test_topics_needed_accounts_for_rejections_and_cap(self):
        self.assertEqual(control.topics_needed(6), 10)
        self.assertEqual(control.topics_needed(3), 5)
        self.assertEqual(control.topics_needed(30, cap=10), 10)


class SelectionTests(unittest.TestCase):
    def topics(self):
        def t(i, score, risk, pillar="A", **kw):
            return {"id": i, "title": i, "score": score, "risk": risk, "pillar": pillar, "status": "new", "decision": "", **kw}
        return [t("hi", 99, "high"), t("med", 95, "medium"), t("a1", 90, "low"), t("a2", 85, "low"),
                t("a3", 80, "low"), t("b1", 70, "low", "B"), t("weak", 40, "low", "C"),
                t("done", 99, "low", decision="reject")]

    def test_risky_topics_never_get_auto_selected(self):
        ts = self.topics()
        autopilot.select_topics(ts, 10, ctl())
        by = {t["id"]: t for t in ts}
        self.assertEqual(by["hi"]["decision"], "reject")
        self.assertEqual(by["med"]["decision"], "hold")

    def test_picks_best_low_risk_above_the_score_floor(self):
        ts = self.topics()
        chosen = autopilot.select_topics(ts, 10, ctl(max_per_pillar_per_day=1, buffer_days=2))
        self.assertEqual([t["id"] for t in chosen], ["a1", "a2", "b1"])  # a3 over pillar cap, weak under floor
        self.assertTrue(all(t["decided_by"] == "auto" for t in chosen))

    def test_respects_k_and_leaves_earlier_decisions_alone(self):
        ts = self.topics()
        chosen = autopilot.select_topics(ts, 1, ctl())
        self.assertEqual([t["id"] for t in chosen], ["a1"])
        self.assertEqual({t["id"]: t["decision"] for t in ts}["done"], "reject")


class QueueTests(unittest.TestCase):
    def test_enqueue_uses_free_slots_and_waits_for_media(self):
        rows = [row("001", at="2026-11-20T03:30:00Z", status="approved")]
        posts = [dict(good_post(), id="008"), dict(good_post(), id="009")]
        added = autopilot.enqueue(rows, posts, ctl(), NOW)
        self.assertEqual(added, 2)
        new = rows[1:]
        self.assertEqual([r["status"] for r in new], ["pending_media"] * 2)
        self.assertNotIn(rows[0]["scheduled_at"], [r["scheduled_at"] for r in new])
        self.assertIn("#a #b", new[0]["caption"])
        self.assertEqual(new[0]["media_urls"], "")

    def test_finalize_only_approves_rows_that_have_slides(self):
        rows = [row("a", status="pending_media", urls=""), row("b", status="pending_media", urls="https://x/1.jpg")]
        self.assertEqual(autopilot.finalize(rows), 1)
        self.assertEqual([r["status"] for r in rows], ["pending_media", "approved"])

    def test_ready_rows_counts_only_future_approved_or_waiting(self):
        rows = [row("a", at="2026-11-21T00:00:00Z"), row("b", at="2026-11-19T00:00:00Z"),
                row("c", at="2026-11-22T00:00:00Z", status="pending_media"), row("d", at="2026-11-22T00:00:00Z", status="posted")]
        self.assertEqual([r["id"] for r in autopilot.ready_rows(rows, NOW)], ["a", "c"])


class HealthTests(unittest.TestCase):
    def rows(self, *statuses):
        return [row(str(i), at=f"2026-11-1{i}T05:00:00Z", status=s) for i, s in enumerate(statuses)]

    def test_consecutive_failures_pause(self):
        alerts, reason = autopilot.health(self.rows("posted", "error", "error", "error"), [], ctl(), NOW, 0)
        self.assertIn("3 posts in a row failed", reason)
        self.assertTrue(any(a.startswith("PAUSED") for a in alerts))

    def test_one_failure_among_successes_does_not_pause(self):
        _, reason = autopilot.health(self.rows("error", "posted", "error"), [], ctl(), NOW, 0)
        self.assertIsNone(reason)

    def test_fact_checker_rejecting_nearly_everything_pauses(self):
        topics = [{"status": "rejected"}] * 9 + [{"status": "ready"}] * 1
        _, reason = autopilot.health([], topics, ctl(), NOW, 0)
        self.assertIn("rejected", reason)
        _, reason = autopilot.health([], [{"status": "rejected"}] * 5, ctl(), NOW, 0)  # too few samples to judge
        self.assertIsNone(reason)

    def test_low_queue_budget_and_token_age_alerts(self):
        alerts, _ = autopilot.health([], [], ctl(), NOW, 0.95, token_issued="2026-09-01")
        text = " | ".join(alerts)
        self.assertIn("queue is low", text)
        self.assertIn("daily cap", text)
        self.assertIn("IG_TOKEN", text)

    def test_mode_off_stays_quiet_about_the_queue(self):
        alerts, _ = autopilot.health([], [], dict(ctl(), mode="off"), NOW, 0)
        self.assertEqual(alerts, [])


class TickAliveTests(unittest.TestCase):
    NOW = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)

    def health_file(self, at):
        d = Path(tempfile.mkdtemp())
        p = d / "health.json"
        p.write_text(json.dumps({"at": at}) if at is not None else "{not json", encoding="utf-8")
        return p

    def test_recent_tick_is_alive(self):
        p = self.health_file("2026-11-20T03:41:16.502207+00:00")
        self.assertIsNone(autopilot.ticks_stale(self.NOW, 4, p))

    def test_old_tick_means_publishing_may_have_stopped(self):
        p = self.health_file("2026-11-20T00:41:16+00:00")
        self.assertIn("3.3 hours", autopilot.ticks_stale(self.NOW, 3, p))

    def test_missing_or_broken_file_is_reported(self):
        self.assertIn("no tick", autopilot.ticks_stale(self.NOW, 4, self.health_file(None)))
        self.assertIn("no tick", autopilot.ticks_stale(self.NOW, 4, Path(tempfile.mkdtemp()) / "nope.json"))


class PlanTests(unittest.TestCase):
    def sandbox(self, topics, rows=None):
        d = Path(tempfile.mkdtemp())
        (d / "state").mkdir()
        (d / "content").mkdir()
        common.save_json(d / "state" / "topics.json", topics)
        qp = d / "queue.csv"
        P.write_queue(rows or [], str(qp))
        patches = [mock.patch.object(m, n, v) for m, n, v in [
            (autopilot, "STATE", d / "state"), (autopilot, "CONTENT", d / "content"), (autopilot, "QUEUE", qp),
            (pipeline, "STATE", d / "state"), (pipeline, "CONTENT", d / "content"),
            (common, "STATE", d / "state"), (common, "CONTENT", d / "content"), (control, "STATE", d / "state")]]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return d, qp

    def topic(self):
        return {"id": "t1", "title": "Venus", "summary": "s", "url": "https://nasa.gov", "pillar": "P", "angle": "a",
                "score": 90, "risk": "low", "status": "new", "decision": ""}

    def small(self, **kw):
        return ctl(posts_per_day=1, buffer_days=1, max_topics_per_run=1, **kw)

    def test_skips_when_off_paused_or_over_budget(self):
        self.sandbox([self.topic()])
        self.assertIn("off", autopilot.plan(llm_for(FakeClient()), dict(self.small(), mode="off"), NOW)["skipped"])
        self.assertIn("paused", autopilot.plan(llm_for(FakeClient()), self.small(paused=True, pause_reason="x"), NOW)["skipped"])
        d, _ = self.sandbox([self.topic()])
        ts = datetime(2026, 11, 20, 3, 0, tzinfo=timezone(timedelta(hours=7))).timestamp()
        (d / "state" / "usage.jsonl").write_text(json.dumps({"ts": ts, "usd": 5.0}) + "\n")
        self.assertIn("budget", autopilot.plan(llm_for(FakeClient()), self.small(), NOW)["skipped"])

    def test_skips_when_the_buffer_is_already_full(self):
        self.sandbox([self.topic()], [row("001", at="2026-11-21T05:00:00Z", status="approved")])
        self.assertIn("buffer", autopilot.plan(llm_for(FakeClient()), self.small(), NOW)["skipped"])

    def test_full_run_writes_checks_and_queues_a_post(self):
        d, qp = self.sandbox([self.topic()])
        c = FakeClient(resp(json.dumps(good_post())), resp(json.dumps(verdicts(True))))
        out = autopilot.plan(llm_for(c), self.small(), NOW)
        self.assertEqual((out["topics_chosen"], out["posts_written"], out["queued"]), (1, 1, 1))
        rows = P.read_queue(str(qp))
        self.assertEqual((rows[0]["status"], rows[0]["media_urls"]), ("pending_media", ""))  # not postable until slides exist
        self.assertEqual(json.loads((d / "content" / "001.json").read_text())["status"], "queued")
        self.assertEqual(len(c.calls), 2)

    def test_post_that_fails_fact_checking_is_never_queued(self):
        d, qp = self.sandbox([self.topic()])
        script = []
        for _ in range(3):
            script += [resp(json.dumps(good_post())), resp(json.dumps(verdicts(False)))]
        out = autopilot.plan(llm_for(FakeClient(*script)), self.small(), NOW)
        self.assertEqual((out["posts_written"], out["queued"]), (0, 0))
        self.assertEqual(P.read_queue(str(qp)), [])
        self.assertEqual(json.loads((d / "state" / "topics.json").read_text())[0]["status"], "rejected")


try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


@unittest.skipUnless(HAVE_PIL, "Pillow not installed (the planner workflow installs it)")
class DressRehearsal(PlanTests):
    """The whole chain once: topic -> write -> fact-check -> queue -> render slides -> approve -> publish."""

    def test_topic_to_posted(self):
        import os
        import render_slides

        d, qp = self.sandbox([self.topic()])
        c = FakeClient(resp(json.dumps(good_post())), resp(json.dumps(verdicts(True))))
        out = autopilot.plan(llm_for(c), self.small(mode="live"), NOW)
        self.assertEqual(out["queued"], 1)

        with mock.patch.object(render_slides, "QUEUE", qp), mock.patch.object(render_slides, "CONTENT", d / "content"), \
                mock.patch.object(render_slides, "MEDIA", d / "media"), \
                mock.patch.dict(os.environ, {"MEDIA_BASE_URL": "https://raw.githubusercontent.com/o/r/main/media", "NO_IMAGES": "1"}):
            render_slides.main()
        rows = P.read_queue(str(qp))
        self.assertEqual(rows[0]["status"], "pending_media")
        self.assertEqual(len(rows[0]["media_urls"].split()), len(good_post()["slides"]))
        self.assertEqual(len(list((d / "media").glob("*.jpg"))), len(good_post()["slides"]))

        # a forced re-render redoes the slides of a row that already has media, and leaves other rows alone
        with mock.patch.object(render_slides, "QUEUE", qp), mock.patch.object(render_slides, "CONTENT", d / "content"), \
                mock.patch.object(render_slides, "MEDIA", d / "media"), \
                mock.patch.dict(os.environ, {"MEDIA_BASE_URL": "https://raw.githubusercontent.com/o/r/main/media", "NO_IMAGES": "1", "RERENDER": "001"}):
            before = (d / "media" / "001-1.jpg").stat().st_mtime_ns
            render_slides.main()
            self.assertNotEqual((d / "media" / "001-1.jpg").stat().st_mtime_ns, before)
        rows = P.read_queue(str(qp))
        self.assertEqual(autopilot.finalize(rows), 1)
        api = FakeApi()
        later = P.parse_time(rows[0]["scheduled_at"]) + timedelta(minutes=1)
        with mock.patch.object(P, "call", api), mock.patch.object(P, "check_media", lambda u: None), \
                mock.patch.object(P.time, "sleep", lambda s: None):
            posted, failed = P.run(rows, "U", now=later, max_per_day=5, min_gap_min=60)
        self.assertEqual((posted, failed, rows[0]["status"]), ("MEDIA1", False, "posted"))
        self.assertTrue(rows[0]["posted_at"])
        carousel = [x for x in api.calls if x[2].get("media_type") == "CAROUSEL"]
        self.assertEqual(len(carousel), 1)
        self.assertIn("Sumber", carousel[0][2]["caption"])


class PublisherLimitTests(unittest.TestCase):
    def posted(self, minutes_ago):
        return dict(row(status="posted"), posted_at=(NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def go(self, rows, **kw):
        with mock.patch.object(P, "call", FakeApi()), mock.patch.object(P, "check_media", lambda u: None), \
                mock.patch.object(P.time, "sleep", lambda s: None):
            return P.run(rows, "U", now=NOW, **kw)

    def test_daily_cap_stops_the_sixth_post(self):
        rows = [self.posted(120 + 60 * i) for i in range(5)] + [row("next", at="2026-11-20T00:00:00Z")]
        self.assertEqual(self.go(rows, max_per_day=5), (None, False))
        self.assertEqual(rows[-1]["status"], "approved")

    def test_old_posts_do_not_count_toward_the_cap(self):
        rows = [self.posted(25 * 60 + i) for i in range(5)] + [row("next", at="2026-11-20T00:00:00Z")]
        self.assertEqual(self.go(rows, max_per_day=5)[0], "MEDIA1")

    def test_minimum_gap_between_posts(self):
        rows = [self.posted(20), row("next", at="2026-11-20T00:00:00Z")]
        self.assertEqual(self.go(rows, min_gap_min=60), (None, False))
        rows = [self.posted(90), row("next", at="2026-11-20T00:00:00Z")]
        self.assertEqual(self.go(rows, min_gap_min=60)[0], "MEDIA1")

    def test_a_real_post_records_posted_at(self):
        rows = [row("next", at="2026-11-20T00:00:00Z")]
        self.go(rows)
        self.assertEqual(rows[0]["posted_at"], "2026-11-20T01:00:00Z")

    def test_unreadable_control_file_means_not_posting(self):
        with mock.patch.object(P, "CONTROL", "/nonexistent/control.json"):
            c = P.load_control()
        self.assertTrue(c["paused"])
        self.assertEqual(c["mode"], "off")


if __name__ == "__main__":
    unittest.main()
