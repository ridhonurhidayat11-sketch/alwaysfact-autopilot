import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "publisher"))
os.environ.setdefault("IG_TOKEN", "x")
import publish as P  # noqa: E402

NOW = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)


def row(i="001", at="2026-11-20T05:00:00Z", urls="https://x/a.jpg", cap="cap", status="approved"):
    return {"id": i, "scheduled_at": at, "media_urls": urls, "caption": cap, "status": status, "result": "", "posted_at": ""}



_SAVED_KEY = {}


def setUpModule():
    """These tests use a fake Meta API; a real BUFFER_API_KEY in the environment (as in GitHub Actions) must not redirect them to Buffer."""
    _SAVED_KEY["v"] = os.environ.pop("BUFFER_API_KEY", None)


def tearDownModule():
    if _SAVED_KEY.get("v") is not None:
        os.environ["BUFFER_API_KEY"] = _SAVED_KEY["v"]

class FakeApi:
    """Records Graph API calls and answers like Instagram would."""

    def __init__(self, live=None, quota_total=100, quota_usage=0, fail_on=None):
        self.calls, self.live, self.fail_on = [], live or [], fail_on
        self.quota = {"data": [{"quota_usage": quota_usage, "config": {"quota_total": quota_total}}]}
        self.n = 0

    def __call__(self, method, path, retries=2, **p):
        self.calls.append((method, path, p))
        if self.fail_on and self.fail_on in path:
            raise RuntimeError("boom")
        if path.endswith("/media") and method == "GET":
            return {"data": [{"caption": c} for c in self.live]}
        if path.endswith("content_publishing_limit"):
            return self.quota
        if path.endswith("/media_publish"):
            return {"id": "MEDIA1"}
        if method == "GET":
            return {"status_code": "FINISHED"}
        self.n += 1
        return {"id": f"C{self.n}"}

    def published(self):
        return [c for c in self.calls if c[1].endswith("/media_publish")]


class PublisherTests(unittest.TestCase):
    def go(self, rows, api, **kw):
        with mock.patch.object(P, "call", api), mock.patch.object(P, "check_media", lambda u: None), \
                mock.patch.object(P.time, "sleep", lambda s: None):
            return P.run(rows, "U", now=NOW, **kw)

    def test_posts_due_approved_row(self):
        rows, api = [row()], FakeApi()
        posted, failed = self.go(rows, api)
        self.assertEqual((posted, failed, rows[0]["status"], rows[0]["result"]), ("MEDIA1", False, "posted", "MEDIA1"))
        self.assertEqual(len(api.published()), 1)

    def test_pending_and_future_rows_are_never_posted(self):
        rows, api = [row(status="pending"), row("002", at="2026-11-20T07:00:00Z")], FakeApi()
        self.assertEqual(self.go(rows, api), (None, False))
        self.assertEqual(api.published(), [])
        self.assertEqual([r["status"] for r in rows], ["pending", "approved"])

    def test_only_one_post_per_run(self):
        rows, api = [row("001"), row("002", cap="other")], FakeApi()
        self.go(rows, api)
        self.assertEqual(len(api.published()), 1)
        self.assertEqual(rows[1]["status"], "approved")

    def test_bad_date_marks_error_and_next_run_continues(self):
        rows, api = [row("001", at="not a date"), row("002", cap="ok")], FakeApi()
        self.assertEqual(self.go(rows, api), (None, True))
        self.assertEqual(rows[0]["status"], "error")
        self.assertEqual(self.go(rows, api)[0], "MEDIA1")
        self.assertEqual(rows[1]["status"], "posted")

    def test_never_double_posts_when_caption_already_live(self):
        rows, api = [row(cap="same caption")], FakeApi(live=["same caption"])
        self.assertEqual(self.go(rows, api), ("already-live", False))
        self.assertEqual(rows[0]["status"], "posted")
        self.assertEqual(api.published(), [])

    def test_failure_marks_error_and_saves(self):
        saved = []
        rows, api = [row()], FakeApi(fail_on="/media")
        # fail on container creation but let the "already live" listing succeed
        api.fail_on = None
        orig = api.__call__

        def flaky(method, path, retries=2, **p):
            if method == "POST" and path.endswith("/media"):
                raise RuntimeError("container boom")
            return orig(method, path, retries, **p)

        with mock.patch.object(P, "call", flaky), mock.patch.object(P, "check_media", lambda u: None):
            posted, failed = P.run(rows, "U", now=NOW, save=lambda r: saved.append([dict(x) for x in r]))
        self.assertEqual((posted, failed, rows[0]["status"]), (None, True, "error"))
        self.assertIn("container boom", rows[0]["result"])
        self.assertTrue(saved)

    def test_carousel_creates_children_then_parent(self):
        rows, api = [row(urls="https://x/1.jpg https://x/2.jpg https://x/3.jpg")], FakeApi()
        self.go(rows, api)
        posts = [c for c in api.calls if c[0] == "POST"]
        self.assertEqual([c[2].get("is_carousel_item") for c in posts[:3]], ["true"] * 3)
        self.assertEqual(posts[3][2]["media_type"], "CAROUSEL")
        self.assertEqual(posts[3][2]["children"], "C1,C2,C3")
        self.assertEqual(len(api.published()), 1)

    def test_caption_too_long_is_rejected_before_any_post(self):
        rows, api = [row(cap="x" * 2300)], FakeApi()
        self.assertEqual(self.go(rows, api), (None, True))
        self.assertEqual(rows[0]["status"], "error")
        self.assertEqual(api.published(), [])

    def test_too_many_images_is_rejected(self):
        rows, api = [row(urls=" ".join(f"https://x/{i}.jpg" for i in range(11)))], FakeApi()
        self.assertEqual(self.go(rows, api)[1], True)

    def test_quota_used_up_posts_nothing_and_is_not_an_error(self):
        rows, api = [row()], FakeApi(quota_total=100, quota_usage=100)
        self.assertEqual(self.go(rows, api), (None, False))
        self.assertEqual(rows[0]["status"], "approved")

    def test_dry_run_posts_nothing(self):
        rows, api = [row()], FakeApi()
        self.assertEqual(self.go(rows, api, dry=True), (None, False))
        self.assertEqual(api.calls, [])

    def test_queue_roundtrip_is_atomic_and_lossless(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "q.csv")
            rows = [row(cap="baris 1\n\n#tag #lagi", status="pending")]
            P.write_queue(rows, path)
            self.assertEqual(P.read_queue(path), rows)
            self.assertFalse(os.path.exists(path + ".tmp"))


if __name__ == "__main__":
    unittest.main()
